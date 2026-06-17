"""Smoke tests for the global search surface.

Mints its own ephemeral admin, seeds Spanish-named contacts + events
(the real Bellas customer shape: "Lorena Hernández", "María José
Vargas", themed quinces), exercises GET /api/search across the locked
behaviors, and cleans up. No external deps, no leftover rows.

Locked behaviors covered:
  - auth required (401 without bearer)
  - q shorter than MIN_QUERY_LENGTH rejected by router validation
  - unknown types parameter rejected (400)
  - tiered ranking: exact > prefix > substring > trigram-fuzzy
  - accent + case folding via f_unaccent: "hernandez" matches
    "Hernández", "maria" matches "María"
  - phone preprocessing: digit-only query matches phone_e164
  - email preprocessing: "@example.com" tail matches the email column
  - per-type filter via types=
  - Phase 4 invoice + quote lookup by number and by joined contact/event names
  - EXPLAIN-based index-use assertion (the GIN trigram index is in
    the plan when seqscan is disabled, which is the only reliable
    proof that the runtime expression matches the index expression)
"""

import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # C4: audit-trigger bypass for cleanup
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import Contact, Event, Invoice, Quote, User  # noqa: E402


client = TestClient(app)


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


def _make_user(role: str, label: str):
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"search-smoke-{label}-{suffix}",
            email=f"search-smoke-{label}-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"Search Smoke {label.title()}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, u.email
    finally:
        db.close()


def _seed(tag: str, suffix_a: str, suffix_b: str, suffix_c: str):
    """Three contacts, two events, one invoice, and one quote. Names are
    intentionally accented Spanish so the unaccent path is exercised on
    every assertion."""
    db = SessionLocal()
    try:
        c_lorena = Contact(
            first_name="Lorena",
            last_name="Hernández",
            display_name="Lorena Hernández",
            email=f"lorena-{tag}@example.com",
            phone="(956) 555-0188",
            phone_e164=f"+1956555{suffix_a}",
            tags=["search-smoke"],
        )
        c_maria = Contact(
            first_name="María José",
            last_name="Vargas",
            display_name="María José Vargas",
            email=f"maria-{tag}@example.com",
            phone="(210) 555-0199",
            phone_e164=f"+1210555{suffix_b}",
            tags=["search-smoke"],
        )
        c_pena = Contact(
            first_name="Sofía",
            last_name="Peña",
            display_name="Sofía Peña",
            email=f"sofia-{tag}@example.com",
            phone="(512) 555-0144",
            phone_e164=f"+1512555{suffix_c}",
            tags=["search-smoke"],
        )
        db.add_all([c_lorena, c_maria, c_pena])
        db.flush()

        e_lorena = Event(
            primary_contact_id=c_lorena.id,
            event_type="quinceanera",
            event_name="Lorena Hernández - Quince",
            event_date=date(2026, 8, 15),
            quince_theme="Floral Rose Gold",
            quince_theme_colors=[],
            status="sold",
            status_changed_at=datetime.now(timezone.utc),
            notes="search-smoke",
        )
        e_maria = Event(
            primary_contact_id=c_maria.id,
            event_type="quinceanera",
            event_name="María José - Sweet 15",
            event_date=date(2026, 11, 1),
            quince_theme="Mariposa Garden",
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            notes="search-smoke",
        )
        db.add_all([e_lorena, e_maria])
        db.flush()

        invoice_number = f"INV-SMOKE-{tag[:10].upper()}"
        quote_number = f"Q-SMOKE-{tag[:10].upper()}"
        inv_lorena = Invoice(
            event_id=e_lorena.id,
            contact_id=c_lorena.id,
            invoice_number=invoice_number,
            status="sent",
            issue_date=date(2026, 7, 1),
            due_date=date(2026, 7, 15),
            subtotal_cents=150_000,
            discount_cents=0,
            tax_cents=0,
            total_cents=150_000,
            paid_to_date_cents=50_000,
            balance_cents=100_000,
        )
        quote_maria = Quote(
            event_id=e_maria.id,
            contact_id=c_maria.id,
            quote_number=quote_number,
            status="sent",
            issue_date=date(2026, 6, 1),
            expires_at=date(2026, 6, 30),
            subtotal_cents=250_000,
            discount_cents=0,
            tax_cents=0,
            total_cents=250_000,
        )
        db.add_all([inv_lorena, quote_maria])
        db.commit()
        return {
            "contact_ids": [c_lorena.id, c_maria.id, c_pena.id],
            "event_ids": [e_lorena.id, e_maria.id],
            "invoice_ids": [inv_lorena.id],
            "quote_ids": [quote_maria.id],
            "lorena_contact_id": c_lorena.id,
            "maria_contact_id": c_maria.id,
            "pena_contact_id": c_pena.id,
            "lorena_event_id": e_lorena.id,
            "maria_event_id": e_maria.id,
            "lorena_invoice_id": inv_lorena.id,
            "maria_quote_id": quote_maria.id,
            "invoice_number": invoice_number,
            "quote_number": quote_number,
        }
    finally:
        db.close()


def _cleanup(
    contact_ids: list[int],
    event_ids: list[int],
    user_ids: list[int],
    invoice_ids: list[int] | None = None,
    quote_ids: list[int] | None = None,
):
    db = SessionLocal()
    try:
        if quote_ids:
            db.execute(
                sql_text("DELETE FROM quotes WHERE id = ANY(:ids)"),
                {"ids": quote_ids},
            )
        if invoice_ids:
            db.execute(
                sql_text("DELETE FROM invoices WHERE id = ANY(:ids)"),
                {"ids": invoice_ids},
            )
        if event_ids:
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:ids)"),
                {"ids": event_ids},
            )
        if contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": contact_ids},
            )
        if user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": user_ids},
            )
        db.commit()
    finally:
        db.close()


def _types_in(results: list[dict], wanted: str) -> list[dict]:
    return [r for r in results if r["type"] == wanted]


def _ids_for(results: list[dict], wanted: str) -> list[int]:
    return [r["id"] for r in results if r["type"] == wanted]


# ---------------------------------------------------------------------------
# Auth required
# ---------------------------------------------------------------------------

resp = client.get("/api/search", params={"q": "lorena"})
assert resp.status_code == 401, f"expected 401 unauth, got {resp.status_code}: {resp.text}"
print("auth required ok")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

admin_id, admin_email = _make_user("admin", "admin")
sales_id, sales_email = _make_user("sales", "sales")
tag = uuid.uuid4().hex[:12]
suffix_a = f"{uuid.uuid4().int % 10_000:04d}"
suffix_b = f"{uuid.uuid4().int % 10_000:04d}"
suffix_c = f"{uuid.uuid4().int % 10_000:04d}"
seed: dict = {}

try:
    resp = client.post(
        "/api/auth/login",
        json={"email": admin_email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    print("admin login ok")

    # ----- sales-scoped authenticated user: 403 -----
    resp = client.post(
        "/api/auth/login",
        json={"email": sales_email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200, resp.text
    sales_token = resp.json()["access_token"]
    sales_auth = {"Authorization": f"Bearer {sales_token}"}
    resp = client.get(
        "/api/search", params={"q": "lorena"}, headers=sales_auth
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "scope_forbidden", resp.text
    print("sales-scoped authenticated user 403 ok")

    seed = _seed(tag, suffix_a, suffix_b, suffix_c)
    print(
        f"seeded contacts={seed['contact_ids']} events={seed['event_ids']}"
    )

    # ----- q too short rejected by router validation -----
    resp = client.get("/api/search", params={"q": "x"}, headers=auth)
    assert resp.status_code == 422, resp.text
    print("q < 2 chars rejected ok")

    # ----- unknown types rejected -----
    resp = client.get(
        "/api/search",
        params={"q": "lorena", "types": "event,unicorn"},
        headers=auth,
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["detail"]["code"] == "unknown_types", body
    assert body["detail"]["unknown"] == ["unicorn"], body
    print("unknown types rejected ok")

    # ----- empty types rejected -----
    resp = client.get(
        "/api/search",
        params={"q": "lorena", "types": ","},
        headers=auth,
    )
    assert resp.status_code == 400, resp.text
    print("empty types rejected ok")

    # ----- special_order stays disabled until a staff UI exists -----
    resp = client.get(
        "/api/search",
        params={"q": "BVX", "types": "special_order"},
        headers=auth,
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["detail"]["code"] == "unknown_types", body
    assert body["detail"]["unknown"] == ["special_order"], body
    print("special_order disabled until UI exists ok")

    # ----- accent-insensitive contact match: "hernandez" hits "Hernández" -----
    resp = client.get("/api/search", params={"q": "hernandez"}, headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    contact_ids = _ids_for(body["results"], "contact")
    assert seed["lorena_contact_id"] in contact_ids, body
    # Lorena's event also contains "Hernández" in event_name so it
    # should appear in event results too.
    event_ids = _ids_for(body["results"], "event")
    assert seed["lorena_event_id"] in event_ids, body
    print("accent-insensitive contact + event match ok")

    # ----- "maria" matches "María" -----
    resp = client.get("/api/search", params={"q": "maria"}, headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    contact_ids = _ids_for(body["results"], "contact")
    assert seed["maria_contact_id"] in contact_ids, body
    print("accent-insensitive maria match ok")

    # ----- "pena" matches "Peña" (ñ is the canonical unaccent edge) -----
    resp = client.get(
        "/api/search",
        params={"q": "pena", "types": "contact"},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    contact_ids = _ids_for(body["results"], "contact")
    assert seed["pena_contact_id"] in contact_ids, body
    print("accent-insensitive ñ match ok")

    # ----- tiered ranking: exact > prefix > substring -----
    # "Lorena" is an exact prefix of "Lorena Hernández" so the contact
    # should rank tier 1 (prefix). "ena" is only substring so it ranks
    # lower; both should match.
    resp = client.get(
        "/api/search",
        params={"q": "Lorena", "types": "contact"},
        headers=auth,
    )
    body = resp.json()
    contacts = _types_in(body["results"], "contact")
    lorena_first = next(
        (c for c in contacts if c["id"] == seed["lorena_contact_id"]),
        None,
    )
    assert lorena_first is not None, body
    # tier-encoded score: prefix = (10-1)+0.9 = 9.9 (or higher for exact).
    assert lorena_first["score"] >= 9.0, lorena_first

    resp = client.get(
        "/api/search",
        params={"q": "ena", "types": "contact"},
        headers=auth,
    )
    body = resp.json()
    contacts = _types_in(body["results"], "contact")
    lorena_sub = next(
        (c for c in contacts if c["id"] == seed["lorena_contact_id"]),
        None,
    )
    assert lorena_sub is not None, body
    # substring tier 2 -> score around 8.x; prefix score must be greater.
    assert lorena_first["score"] > lorena_sub["score"], (
        lorena_first,
        lorena_sub,
    )
    print("tiered ranking prefix > substring ok")

    # ----- theme match -----
    resp = client.get(
        "/api/search",
        params={"q": "mariposa", "types": "event"},
        headers=auth,
    )
    body = resp.json()
    event_ids = _ids_for(body["results"], "event")
    assert seed["maria_event_id"] in event_ids, body
    print("event theme match ok")

    # ----- phone preprocessing: last-four digit lookup hits phone_e164 -----
    last_four = suffix_a  # 4 digits
    resp = client.get(
        "/api/search",
        params={"q": last_four, "types": "contact"},
        headers=auth,
    )
    body = resp.json()
    contact_ids = _ids_for(body["results"], "contact")
    assert seed["lorena_contact_id"] in contact_ids, body
    print("phone last-four match ok")

    # ----- formatted phone shape also works -----
    formatted = f"956-555-{suffix_a}"
    resp = client.get(
        "/api/search",
        params={"q": formatted, "types": "contact"},
        headers=auth,
    )
    body = resp.json()
    contact_ids = _ids_for(body["results"], "contact")
    assert seed["lorena_contact_id"] in contact_ids, body
    print("formatted phone match ok")

    # ----- email substring lookup -----
    resp = client.get(
        "/api/search",
        params={"q": f"lorena-{tag}", "types": "contact"},
        headers=auth,
    )
    body = resp.json()
    contact_ids = _ids_for(body["results"], "contact")
    assert seed["lorena_contact_id"] in contact_ids, body
    print("email substring match ok")

    # ----- email preprocessing: local-part match wins when domain is wrong -----
    # The full string "lorena-{tag}@nonsense" is NOT a substring of the
    # real email "lorena-{tag}@example.com", so the only branch that
    # can find this contact is the documented local-part branch.
    resp = client.get(
        "/api/search",
        params={"q": f"lorena-{tag}@nonsense", "types": "contact"},
        headers=auth,
    )
    body = resp.json()
    contact_ids = _ids_for(body["results"], "contact")
    assert seed["lorena_contact_id"] in contact_ids, body
    print("email local-part preprocessing match ok")

    # ----- types=event excludes contacts -----
    resp = client.get(
        "/api/search",
        params={"q": "lorena", "types": "event"},
        headers=auth,
    )
    body = resp.json()
    types_present = {r["type"] for r in body["results"]}
    assert "contact" not in types_present, body
    assert "event" in types_present, body
    print("types= filter ok")

    # ----- Phase 4: invoice number lookup routes to the event invoices tab -----
    resp = client.get(
        "/api/search",
        params={"q": seed["invoice_number"], "types": "invoice"},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    invoices = _types_in(body["results"], "invoice")
    inv = next(
        (r for r in invoices if r["id"] == seed["lorena_invoice_id"]),
        None,
    )
    assert inv is not None, body
    assert inv["label"] == seed["invoice_number"], inv
    assert inv["route"] == f"/events/{seed['lorena_event_id']}/invoices", inv
    assert "Sent" in inv["sublabel"], inv
    assert "Lorena Hernández - Quince" in inv["sublabel"], inv
    print("invoice number route ok")

    # ----- Phase 4: quote number lookup routes to the event quotes tab -----
    resp = client.get(
        "/api/search",
        params={"q": seed["quote_number"], "types": "quote"},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    quotes = _types_in(body["results"], "quote")
    quote = next(
        (r for r in quotes if r["id"] == seed["maria_quote_id"]),
        None,
    )
    assert quote is not None, body
    assert quote["label"] == seed["quote_number"], quote
    assert quote["route"] == f"/events/{seed['maria_event_id']}/quotes", quote
    assert "María José - Sweet 15" in quote["sublabel"], quote
    print("quote number route ok")

    # ----- Phase 4: joined name match finds documents -----
    resp = client.get(
        "/api/search",
        params={"q": "hernandez", "types": "invoice"},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    invoice_ids = _ids_for(body["results"], "invoice")
    assert seed["lorena_invoice_id"] in invoice_ids, body

    resp = client.get(
        "/api/search",
        params={"q": "maria", "types": "quote"},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    quote_ids = _ids_for(body["results"], "quote")
    assert seed["maria_quote_id"] in quote_ids, body
    print("document joined-name match ok")

    # ----- limit caps per type -----
    resp = client.get(
        "/api/search",
        params={"q": "search-smoke", "limit": 1, "types": "contact"},
        headers=auth,
    )
    body = resp.json()
    # The seed gives at most 3 contacts that could match "search-smoke"
    # by tag; limit=1 must cap regardless.
    contacts = _types_in(body["results"], "contact")
    assert len(contacts) <= 1, body
    print("per-type limit cap ok")

    # ----- response shape: each result carries route + sublabel -----
    resp = client.get(
        "/api/search",
        params={"q": "lorena"},
        headers=auth,
    )
    body = resp.json()
    assert body["query"] == "lorena", body
    assert len(body["results"]) > 0, body
    for r in body["results"]:
        assert r["type"] in ("event", "contact", "invoice", "quote"), r
        assert r["id"] > 0, r
        assert r["label"], r
        # sublabel may be empty string but must be present and string-typed
        assert isinstance(r["sublabel"], str), r
        assert r["score"] > 0, r
        if r["type"] == "event":
            assert r["route"] == f"/events/{r['id']}", r
        elif r["type"] == "contact":
            assert r["route"] == f"/contacts/{r['id']}", r
        elif r["type"] == "invoice":
            assert r["route"].endswith("/invoices"), r
        elif r["type"] == "quote":
            assert r["route"].endswith("/quotes"), r
    print("response shape ok")

    # ----- EXPLAIN: query plan uses the trigram index -----
    # With seqscan disabled, the planner is forced to either pick the
    # trigram GIN index or fail. This is the cheapest reliable proof
    # that the runtime expression `f_unaccent(lower(display_name))`
    # matches the index expression exactly. If they ever diverge the
    # plan no longer references our index and the assertion below
    # fails loudly instead of silently sequential-scanning in prod.
    db = SessionLocal()
    try:
        db.execute(sql_text("SET LOCAL enable_seqscan = off"))
        plan_rows = db.execute(
            sql_text(
                "EXPLAIN SELECT id FROM contacts "
                "WHERE f_unaccent(lower(display_name)) "
                "LIKE '%' || f_unaccent(lower(:q)) || '%'"
            ),
            {"q": "hernandez"},
        ).all()
        plan_text = "\n".join(row[0] for row in plan_rows)
        assert "contacts_display_name_trgm" in plan_text, (
            "expected plan to reference contacts_display_name_trgm; "
            f"got:\n{plan_text}"
        )
    finally:
        db.close()
    print("EXPLAIN uses trigram index ok")

    # ----- Phase 4 document-number indexes exist -----
    # On tiny local smoke databases Postgres may prefer the existing
    # unique invoice_number btree even with seqscan disabled, so this
    # assertion checks the migration contract directly instead of
    # pretending planner preference is stable at small row counts.
    db = SessionLocal()
    try:
        plan_rows = db.execute(
            sql_text(
                "SELECT indexname, indexdef "
                "FROM pg_indexes "
                "WHERE schemaname = 'public' "
                "AND indexname IN ("
                "'invoices_number_trgm', "
                "'quotes_number_trgm', "
                "'special_orders_vendor_order_number_trgm', "
                "'catalog_items_public_code_trgm'"
                ")"
            )
        ).all()
        index_defs = {row.indexname: row.indexdef for row in plan_rows}
        assert set(index_defs) == {
            "invoices_number_trgm",
            "quotes_number_trgm",
            "special_orders_vendor_order_number_trgm",
            "catalog_items_public_code_trgm",
        }, index_defs
        assert "lower((invoice_number)::text)" in index_defs["invoices_number_trgm"]
        assert "lower((quote_number)::text)" in index_defs["quotes_number_trgm"]
        assert (
            "lower((vendor_order_number)::text)"
            in index_defs["special_orders_vendor_order_number_trgm"]
        )
        assert "lower((public_code)::text)" in index_defs[
            "catalog_items_public_code_trgm"
        ]
    finally:
        db.close()
    print("Phase 4 trigram indexes present ok")

    print("\nALL SEARCH SMOKE TESTS PASSED")
finally:
    _cleanup(
        seed.get("contact_ids", []),
        seed.get("event_ids", []),
        [admin_id, sales_id],
        seed.get("invoice_ids", []),
        seed.get("quote_ids", []),
    )
