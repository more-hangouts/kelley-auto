"""Smoke for the Phase 3 API module enable flags.

Each optional module (messaging, deals, inventory, scheduling, booking,
analytics) has a MODULE_<X>_ENABLED settings flag, default true. Disabling one
must drop exactly that module's routers (and, for scheduling, its worker) while
leaving every other module — and the kernel modules core + contacts — intact,
and must not break SQLAlchemy mapper configuration.

Flags are read at import time (module-level os.getenv), and both the SQLAlchemy
mapper registry and the FastAPI app are process-global once imported, so each
flag combination MUST be exercised in a fresh interpreter. This test therefore
shells out to a subprocess per case rather than toggling os.environ in-process.
"""

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# A route-path prefix that uniquely identifies each optional module's surface.
MODULE_PREFIXES = {
    "messaging": ("/api/inbox", "/api/web-chat", "/api/webhooks"),
    "deals": ("/api/invoices", "/api/quotes", "/api/payments", "/portal"),
    "inventory": ("/api/catalog", "/api/admin/vin"),
    "scheduling": ("/api/sales/schedule", "/api/admin/schedule", "/api/sales/clock"),
    "booking": ("/api/booking", "/api/walk-in-leads"),
    "analytics": ("/api/dashboard", "/api/admin/storefront-analytics"),
}

# A sentinel route from a kernel module that must ALWAYS be present.
CORE_SENTINEL = "/api/auth"
CONTACTS_SENTINEL = "/api/contacts"

_PROBE = r"""
import json
from api.server import app
from sqlalchemy.orm import configure_mappers
configure_mappers()
paths = sorted({getattr(r, "path", "") for r in app.routes})
print("PATHS_JSON:" + json.dumps(paths))
"""


def _run(env_overrides):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT)
    env.update(env_overrides)
    out = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise AssertionError(f"probe failed ({env_overrides}):\n{out.stderr[-2000:]}")
    line = next(l for l in out.stdout.splitlines() if l.startswith("PATHS_JSON:"))
    import json

    return json.loads(line[len("PATHS_JSON:"):])


def _has_prefix(paths, prefixes):
    return any(p.startswith(pref) for p in paths for pref in prefixes)


def main() -> None:
    # Baseline: everything enabled.
    base = _run({})
    assert len(base) >= 1, base
    assert _has_prefix(base, (CORE_SENTINEL,)), "core auth routes missing at baseline"
    assert _has_prefix(base, (CONTACTS_SENTINEL,)), "contacts routes missing at baseline"
    for mod, prefixes in MODULE_PREFIXES.items():
        assert _has_prefix(base, prefixes), f"{mod} routes missing at baseline"
    baseline_count = len(base)
    print(f"baseline: {baseline_count} route paths, all modules present ok")

    # Disable each optional module in turn.
    for mod, prefixes in MODULE_PREFIXES.items():
        flag = f"MODULE_{mod.upper()}_ENABLED"
        paths = _run({flag: "false"})
        # its own routes gone
        assert not _has_prefix(paths, prefixes), (
            f"{mod} routes still present with {flag}=false: "
            f"{[p for p in paths if any(p.startswith(x) for x in prefixes)]}"
        )
        # kernel modules still present
        assert _has_prefix(paths, (CORE_SENTINEL,)), f"core dropped when {mod} disabled"
        assert _has_prefix(paths, (CONTACTS_SENTINEL,)), f"contacts dropped when {mod} disabled"
        # every OTHER optional module still present
        for other, oprefixes in MODULE_PREFIXES.items():
            if other == mod:
                continue
            assert _has_prefix(paths, oprefixes), (
                f"{other} routes wrongly dropped when {mod} disabled"
            )
        assert len(paths) < baseline_count, f"{flag}=false did not drop any routes"
        print(f"  {flag}=false: {mod} routes gone, all others + core/contacts intact ok")

    # Kernel modules cannot be disabled via env: bogus flags must be no-ops.
    kernel = _run({"MODULE_CORE_ENABLED": "false", "MODULE_CONTACTS_ENABLED": "false"})
    assert len(kernel) == baseline_count, (
        f"bogus MODULE_CORE/CONTACTS_ENABLED changed route count: "
        f"{len(kernel)} vs {baseline_count}"
    )
    assert _has_prefix(kernel, (CORE_SENTINEL,)) and _has_prefix(kernel, (CONTACTS_SENTINEL,))
    print("kernel modules (core, contacts) ignore bogus disable flags ok")

    # Worker selection: disabling scheduling excludes the schedule_monitor worker
    # WITHOUT starting any worker loop (pure registry inspection in a subprocess).
    worker_probe = (
        "import config.settings as s; from modules.registry import iter_enabled_workers; "
        "print('WORKERS:' + ','.join(w.name for w in iter_enabled_workers(s)))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT)
    default_w = subprocess.run(
        [sys.executable, "-c", worker_probe], cwd=str(_REPO_ROOT), env=env,
        capture_output=True, text=True,
    ).stdout
    assert "WORKERS:notifications,daily,schedule_monitor" in default_w, default_w
    env["MODULE_SCHEDULING_ENABLED"] = "false"
    off_w = subprocess.run(
        [sys.executable, "-c", worker_probe], cwd=str(_REPO_ROOT), env=env,
        capture_output=True, text=True,
    ).stdout
    assert "schedule_monitor" not in off_w, off_w
    assert "notifications" in off_w and "daily" in off_w, off_w
    print("worker selection: schedule_monitor excluded when scheduling disabled ok")

    print("\nmodule_flags smoke ok")


if __name__ == "__main__":
    main()
