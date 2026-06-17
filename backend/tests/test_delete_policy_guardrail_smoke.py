"""Smoke for G3: delete-policy guardrail.

Enforces docs/DATA_RETENTION_AND_DELETE_POLICY.md by scanning every Python file
under services/ and api/routers/ for two patterns:

  1. Calls shaped like `db.delete(x)` / `session.delete(x)` / `db_session.delete(x)`
     (the SQLAlchemy ORM delete). For each call, we resolve `x` back to the
     ORM model class by walking the enclosing function for the closest prior
     `x = db.get(<Model>, ...)` or `x = db.query(<Model>).filter(...).first()`
     style binding.

  2. Raw SQL `DELETE FROM <table>` text, by literal string scan inside the
     `services/` and `api/routers/` trees.

Each call site (file + delete-kind + target) is checked against an explicit
allowlist below. A new delete site anywhere in those trees fails the smoke
until the allowlist is updated AND the policy doc gains the new entry.

The allowlist is keyed by relative file path so the failure message points
at exactly what's new. Lines are not pinned (line numbers shift on routine
edits); the smoke matches on (file, kind, target) tuples.

Run with: venv/bin/python tests/test_delete_policy_guardrail_smoke.py
"""

import ast
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Expected delete call sites, derived from the audit done at G3 ship time.
# Tier-1 (soft-delete) and Tier-2 (append-only) tables must NEVER appear here.
# ---------------------------------------------------------------------------

# (relative_path, model_class_name) for db.delete() / session.delete() calls.
EXPECTED_ORM_DELETES: set[tuple[str, str]] = {
    # Tier 4 — operational config:
    ("services/staff_shifts_admin.py", "StaffShift"),
    ("services/staff_shifts_admin.py", "StaffShiftOverride"),
    ("services/staff_holidays_admin.py", "StaffHoliday"),
    ("services/staff_schedule.py", "StaffScheduleEntry"),
    ("services/recurring_availability.py", "RecurringUnavailability"),
    ("api/routers/admin_booking_settings.py", "AppointmentAvailabilityRule"),
    ("api/routers/admin_booking_settings.py", "AppointmentBlackout"),
    # Special case — activity_log breadcrumb substitutes for soft-delete:
    ("services/sales_tried_on.py", "AppointmentTriedOnItem"),
}

# (relative_path, table_name) for `DELETE FROM <table>` raw SQL.
EXPECTED_RAW_DELETES: set[tuple[str, str]] = {
    # Tier 3 — retention sweep:
    ("services/webhook_ingest.py", "webhook_events"),
    # Tier 5 — rebuild-children inside parent transactions:
    ("services/invoice_service.py", "invoice_order_discounts"),
    ("services/invoice_service.py", "invoice_line_items"),
    ("services/invoice_service.py", "invoice_installments"),
    ("services/quote_service.py", "quote_order_discounts"),
    ("services/quote_service.py", "quote_line_items"),
    ("services/quote_service.py", "quote_installments"),
    ("services/payment_service.py", "payment_allocations"),
}

# Models in Tier 1 (financial soft-delete) and Tier 2 (CRM append-only). Any
# `db.delete(<x>)` resolving to one of these models is a policy violation,
# even if it's accidentally added to the allowlist above (defense in depth).
TIER1_MODELS = {
    "Invoice",
    "InvoiceInvitation",
    "Quote",
    "QuoteInvitation",
    "Payment",
    "EventDocument",
    # D2 (migration 080) moved these from Tier 2 to Tier 1 (single-state
    # soft-delete via ``deleted_at``). Hard-delete is still forbidden;
    # archive lives in D3 service helpers and writes ``deleted_at``.
    "Contact",
    "Event",
    "EventParticipant",
    "SpecialOrder",
}
TIER2_MODELS = {
    "Appointment",
    "CatalogItem",
}
FORBIDDEN_ORM_TARGETS = TIER1_MODELS | TIER2_MODELS

# Tables corresponding to the above. Same defense-in-depth logic for raw SQL.
TIER1_TABLES = {
    "invoices",
    "invoice_invitations",
    "quotes",
    "quote_invitations",
    "payments",
    "event_documents",
    "contacts",
    "events",
    "event_participants",
    "special_orders",
}
TIER2_TABLES = {
    "appointments",
    "catalog_items",
}
FORBIDDEN_RAW_TABLES = TIER1_TABLES | TIER2_TABLES


# ---------------------------------------------------------------------------
# AST walker for ORM .delete() calls.
# ---------------------------------------------------------------------------

_SESSION_NAMES = {"db", "session", "db_session"}

# Match `var = db.get(Model, ...)` and `var = db.query(Model).<chain>`.
# We don't try to track full SQLAlchemy chains; the audit only relies on the
# first ORM model name we see in the binding's RHS.
_ORM_BIND_PATTERNS = ("get", "query")


def _resolve_model_for_var(func_node: ast.AST, var_name: str, before_line: int) -> str | None:
    """Walk `func_node`'s body in source order and return the ORM model name
    bound to `var_name` by the nearest assignment occurring before `before_line`.
    Returns None if we can't tell.
    """
    resolved: str | None = None
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Assign):
            continue
        if getattr(node, "lineno", 10**9) >= before_line:
            continue
        # Single-target name assignments only.
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id != var_name:
            continue
        # RHS shape: db.get(Model, ...) or db.query(Model)....
        rhs = node.value
        while isinstance(rhs, ast.Call) and isinstance(rhs.func, ast.Attribute):
            inner = rhs.func.value
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr in _ORM_BIND_PATTERNS
                and isinstance(inner.func.value, ast.Name)
                and inner.func.value.id in _SESSION_NAMES
                and inner.args
                and isinstance(inner.args[0], ast.Name)
            ):
                resolved = inner.args[0].id
                break
            if (
                rhs.func.attr in _ORM_BIND_PATTERNS
                and isinstance(rhs.func.value, ast.Name)
                and rhs.func.value.id in _SESSION_NAMES
                and rhs.args
                and isinstance(rhs.args[0], ast.Name)
            ):
                resolved = rhs.args[0].id
                break
            rhs = rhs.func.value
    return resolved


def _scan_orm_deletes(py_path: Path) -> list[tuple[str, int, str | None]]:
    """Return [(var_name, lineno, resolved_model_or_None), ...] for every
    db.delete()/session.delete() call in `py_path`."""
    src = py_path.read_text()
    tree = ast.parse(src, filename=str(py_path))
    out: list[tuple[str, int, str | None]] = []
    # For each function/method, scan its body for delete-calls so we can resolve
    # variables in the right scope.
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not isinstance(f, ast.Attribute):
                continue
            if f.attr != "delete":
                continue
            if not isinstance(f.value, ast.Name):
                continue
            if f.value.id not in _SESSION_NAMES:
                continue
            if not node.args or not isinstance(node.args[0], ast.Name):
                # E.g. db.delete(<expression>) — too dynamic to resolve;
                # flag it.
                out.append(("<non-name-arg>", node.lineno, None))
                continue
            var_name = node.args[0].id
            model = _resolve_model_for_var(func, var_name, node.lineno)
            out.append((var_name, node.lineno, model))
    return out


# ---------------------------------------------------------------------------
# Regex scan for raw `DELETE FROM <table>`.
# ---------------------------------------------------------------------------

_DELETE_FROM_RX = re.compile(r"DELETE\s+FROM\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)


def _scan_raw_deletes(py_path: Path) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(py_path.read_text().splitlines(), start=1):
        for m in _DELETE_FROM_RX.finditer(line):
            out.append((lineno, m.group(1)))
    return out


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def _iter_target_files() -> list[Path]:
    roots = [_REPO_ROOT / "services", _REPO_ROOT / "api"]
    files: list[Path] = []
    for root in roots:
        for p in root.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            files.append(p)
    return sorted(files)


def main() -> int:
    failures: list[str] = []
    found_orm: set[tuple[str, str]] = set()
    found_raw: set[tuple[str, str]] = set()

    for py in _iter_target_files():
        rel = str(py.relative_to(_REPO_ROOT))
        try:
            for var_name, lineno, model in _scan_orm_deletes(py):
                tag = model or "<unresolved>"
                found_orm.add((rel, tag))
                if model is None:
                    failures.append(
                        f"{rel}:{lineno}: db.delete({var_name}) target could "
                        "not be resolved to an ORM model. Refactor so the "
                        "deleted variable is bound via db.get(Model, ...) "
                        "or db.query(Model) in the same function."
                    )
                    continue
                if model in FORBIDDEN_ORM_TARGETS:
                    failures.append(
                        f"{rel}:{lineno}: db.delete({var_name}) targets "
                        f"{model}, which is in Tier 1 (financial soft-delete) "
                        "or Tier 2 (CRM append-only). Hard-delete is forbidden "
                        "for this table; use the soft-delete service helper "
                        "or add a status field instead. "
                        "See docs/DATA_RETENTION_AND_DELETE_POLICY.md."
                    )
                    continue
                if (rel, model) not in EXPECTED_ORM_DELETES:
                    failures.append(
                        f"{rel}:{lineno}: NEW db.delete() call site for "
                        f"{model}. If this is intentional, classify the "
                        f"table in docs/DATA_RETENTION_AND_DELETE_POLICY.md "
                        f'and add ("{rel}", "{model}") to '
                        f"EXPECTED_ORM_DELETES in this smoke."
                    )

            for lineno, table in _scan_raw_deletes(py):
                table_l = table.lower()
                found_raw.add((rel, table_l))
                if table_l in FORBIDDEN_RAW_TABLES:
                    failures.append(
                        f"{rel}:{lineno}: DELETE FROM {table} targets a "
                        "Tier 1 or Tier 2 table. Hard-delete is forbidden; "
                        "use the soft-delete service helper or a status "
                        "field. See docs/DATA_RETENTION_AND_DELETE_POLICY.md."
                    )
                    continue
                if (rel, table_l) not in EXPECTED_RAW_DELETES:
                    failures.append(
                        f"{rel}:{lineno}: NEW raw DELETE FROM {table}. "
                        f"Classify the table in docs/DATA_RETENTION_AND_DELETE_POLICY.md "
                        f'and add ("{rel}", "{table_l}") to '
                        f"EXPECTED_RAW_DELETES in this smoke."
                    )
        except SyntaxError as exc:
            failures.append(f"{rel}: SyntaxError while parsing: {exc}")

    # Reverse check: every allowlisted entry must still exist. If an entry is
    # stale (the delete was removed), the allowlist is wrong and should be
    # pruned — otherwise it permits future re-introduction without review.
    missing_orm = EXPECTED_ORM_DELETES - found_orm
    for rel, model in sorted(missing_orm):
        failures.append(
            f"EXPECTED_ORM_DELETES has stale entry: ({rel}, {model}) no "
            "longer appears in source. Remove it from the allowlist."
        )
    missing_raw = EXPECTED_RAW_DELETES - found_raw
    for rel, table in sorted(missing_raw):
        failures.append(
            f"EXPECTED_RAW_DELETES has stale entry: ({rel}, {table}) no "
            "longer appears in source. Remove it from the allowlist."
        )

    if failures:
        print("DELETE-POLICY GUARDRAIL FAILED:\n")
        for f in failures:
            print(f"  - {f}")
        print(
            "\nSee docs/DATA_RETENTION_AND_DELETE_POLICY.md for the tier "
            "definitions and the procedure for adding a new delete site."
        )
        return 1

    print(
        f"delete-policy guardrail OK "
        f"({len(found_orm)} ORM delete sites, "
        f"{len(found_raw)} raw DELETE FROM sites scanned)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
