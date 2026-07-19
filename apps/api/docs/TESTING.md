# Testing

## What we run

Smoke tests under [tests/](../tests). They are **scripts, not pytest**. Each
file mints its own ephemeral fixtures, exercises the API end-to-end against
the dev DB, and cleans up.

Tests are organized by domain (auth, booking, admin, contacts/events,
catalog, money, sales, attendance, schedule, crons, audit, search,
integrations). Filenames are stable domain names like `test_schedule_smoke.py`
or `test_payments_smoke.py` — no phase markers.

## Handoff smoke suite

The lean release-gate suite lives in
[scripts/smoke_handoff.sh](../scripts/smoke_handoff.sh) and runs 68 smokes
serially. This is the suite to run before a release or before handing the
project off.

```bash
# stop on first failure
scripts/smoke_handoff.sh

# run all, report failures at the end
scripts/smoke_handoff.sh --keep-going
```

The suite definition and the rationale for what is in or out of it are
documented in [docs/SMOKE_TEST_AUDIT.md](SMOKE_TEST_AUDIT.md).

## Running a single smoke

```bash
venv/bin/python tests/test_events_smoke.py
```

Each smoke prints a stream of `xxx ok` lines and exits non-zero on
assertion failure.

## Excluded from the handoff suite

Some smokes are kept in the repo but excluded from `smoke_handoff.sh` because
they validate one-shot migrations, heavy schema probes, admin scripts that
aren't part of the request path, or single-vendor scrapers. Run them on
demand — when touching that surface. The list and the why is in
[SMOKE_TEST_AUDIT.md](SMOKE_TEST_AUDIT.md).

## CI

GitHub Actions runs the handoff suite on every push and pull request via
[.github/workflows/smoke.yml](../.github/workflows/smoke.yml). The workflow:

- starts a fresh PostgreSQL service,
- installs backend dependencies,
- applies all migrations,
- runs `scripts/smoke_handoff.sh`, and
- runs the Vite production build.

The tests are intentionally sequential — several smokes mutate the
singleton `numbering_state` row and would collide in parallel.

## Why scripts, not pytest

- They mutate the real dev database (creates rows, runs migrations against
  it). pytest fixtures and per-test rollback would obscure that intent.
- The shape is "real HTTP request -> real DB write -> verify -> clean up,"
  which reads better as procedural code than as fixture trees.
- They cover integration, not units. Pure-function tests don't exist yet
  because the value isn't there yet.

If unit tests start to matter (e.g. a new pure helper has 6 edge cases),
introduce pytest then. Don't preemptively migrate the smoke style.

## Anatomy of a smoke test

[tests/test_events_smoke.py](../tests/test_events_smoke.py) is the most
recent and best example. Pattern:

```python
# 1. Bootstrap env so the same .env that runs the server is in scope
load_dotenv(_REPO_ROOT / ".env")

# 2. Mint an ephemeral admin user (so you don't depend on or modify a seeded one)
user_id, user_email = _make_admin()

# 3. Seed any fixture rows the test needs (contact, appointment, enrichment...)
contact_id, appt_id = _seed_lead(...)

try:
    # 4. Log in to get a Bearer token
    resp = client.post("/api/auth/login", json={"email": user_email, "password": "..."})
    token = resp.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    # 5. Exercise endpoints. Print "xxx ok" after each non-trivial assertion.
    resp = client.get("/api/events/board", headers=auth)
    assert resp.status_code == 200, resp.text
    print("board ok")

finally:
    # 6. Always clean up. Order matters when there are FKs between fixtures.
    _cleanup(...)
    print("cleanup done")

print("\nfeature smoke ok")
```

## Cleanup discipline

- Capture every fixture id and tear it down in the `finally`.
- Order: child rows before parents (FK constraints). Cascade is allowed when
  the FK uses `ON DELETE CASCADE`.
- Don't delete contacts that other appointments still reference — gate with
  `NOT EXISTS (SELECT 1 FROM appointments WHERE contact_id = ...)`.
- Don't depend on the DB being empty. Use UUID-derived suffixes for unique
  fields (phone_e164, analytics event_id).

## Adding a new smoke test

Prefer extending the existing domain smoke before creating a new file —
that keeps the handoff suite stable and avoids three-files-for-one-feature
sprawl. Add a new file only when the new behavior is its own domain.

When you do need a new file:

1. Copy [test_events_smoke.py](../tests/test_events_smoke.py) as the template.
2. Replace the `_make_admin`, `_seed_*`, `_cleanup` helpers for your domain.
3. Run it twice in a row. **The second run must pass.** If it doesn't, your
   cleanup is incomplete or your fixtures aren't unique per run.
4. Add the file to the SUITE array in
   [scripts/smoke_handoff.sh](../scripts/smoke_handoff.sh) if it covers a
   production surface the handoff gate should protect.
5. Add a one-liner in [BOOKING.md](BOOKING.md) or [CRM.md](CRM.md) under
   "Smoke testing" pointing to the new file.

**Filenames.** Use stable domain names (`test_schedule_smoke.py`,
`test_payments_smoke.py`). Do **not** name new files after a project phase
(`test_phase12_*`). Phase markers rot and force a cleanup pass like the
2026-05-17 audit.

## What we don't have (yet)

- UI tests. The kanban drag-drop works because both ends are well-typed and
  the smoke tests cover the API. We'll add Playwright when a UI regression
  bites us.
- Load tests. One-shop volume is in the dozens-per-week range. No need.
