"""Smoke test for Phase 10 Slice 6 — labor spend ticker (Epic 6.1).

Covers:

  1. Service-level `compute_labor_cost`:
       - mix of draft + published rows sums into the right totals
       - users with NULL hourly_wage contribute 0 but show up in
         `unknown_wage_user_ids`
       - zero-or-negative-duration entries are skipped
  2. Admin router GET /api/admin/schedule/week:
       - response carries `labor_cost.total_cents` and matches a
         per-user lower bound (we assert `>=` to stay robust against
         dev-DB residue from prior smokes — see "global-pass smokes
         assert per-user, not global counts" convention)
       - `unknown_wage_user_ids` names the no-wage seeded user
       - the empty-user-ids early return still includes a zeroed
         `labor_cost` block

The smoke deliberately picks an isolated future week so other
schedule smokes' rows don't bleed into the assertions.
"""

import os
import sys
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from config.settings import APP_TIMEZONE  # noqa: E402
from database.auth import create_access_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    BusinessProfile,
    Contact,
    Event,
    Invoice,
    StaffScheduleEntry,
    User,
)
from services import staff_schedule  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []
_contact_ids: list[int] = []
_event_ids: list[int] = []
_original_target_labor_pct: object = "__unset__"


def _make_user(*, role: str, hourly_wage: Decimal | None) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-labor-{suffix}",
            email=f"{role}-labor-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"Labor {role.title()} {suffix}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
            hourly_wage=hourly_wage,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _user_ids.append(u.id)
        return u.id
    finally:
        db.close()


def _seed_entry(
    *,
    user_id: int,
    creator_id: int,
    business_date_: date,
    starts_at_local: datetime,
    ends_at_local: datetime,
    status: str,
) -> int:
    db = SessionLocal()
    try:
        e = StaffScheduleEntry(
            user_id=user_id,
            business_date=business_date_,
            starts_at_local=starts_at_local,
            ends_at_local=ends_at_local,
            status=status,
            attendance_status="scheduled",
            late_grace_minutes=30,
            source="manual",
            published_at=datetime.now(ZoneInfo("UTC"))
            if status == "published"
            else None,
            published_by_user_id=creator_id if status == "published" else None,
            created_by_user_id=creator_id,
        )
        db.add(e)
        db.commit()
        db.refresh(e)
        return e.id
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_schedule_entries "
                    "WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
        if _event_ids:
            db.execute(
                sql_text(
                    "DELETE FROM invoices WHERE event_id = ANY(:eids)"
                ),
                {"eids": _event_ids},
            )
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:eids)"),
                {"eids": _event_ids},
            )
        if _contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:cids)"),
                {"cids": _contact_ids},
            )
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        # Restore the original target_labor_pct singleton value so
        # other smokes / runs don't inherit our test override.
        if _original_target_labor_pct != "__unset__":
            profile = db.get(BusinessProfile, 1)
            if profile is not None:
                profile.target_labor_pct = _original_target_labor_pct
        db.commit()
    finally:
        db.close()


def main() -> None:
    tz = ZoneInfo(APP_TIMEZONE)
    admin_id = _make_user(role="admin", hourly_wage=None)
    paid_id = _make_user(role="sales", hourly_wage=Decimal("25.00"))
    nowage_id = _make_user(role="sales", hourly_wage=None)
    admin_hdr = {
        "Authorization": f"Bearer {create_access_token_for(admin_id)}"
    }

    # Pick a Monday well in the future to avoid colliding with other
    # smokes' canonical week (2026-06-01).
    week_start = date(2026, 9, 7)
    assert week_start.isoweekday() == 1

    # paid_id: published 9-17 Mon (8h × $25 = $200) + draft 9-13 Tue
    # (4h × $25 = $100). Total $300 → 30000 cents. Drafts = 10000.
    paid_pub_id = _seed_entry(
        user_id=paid_id,
        creator_id=admin_id,
        business_date_=week_start,
        starts_at_local=datetime(2026, 9, 7, 9, 0, tzinfo=tz),
        ends_at_local=datetime(2026, 9, 7, 17, 0, tzinfo=tz),
        status="published",
    )
    paid_draft_id = _seed_entry(
        user_id=paid_id,
        creator_id=admin_id,
        business_date_=week_start + timedelta(days=1),
        starts_at_local=datetime(2026, 9, 8, 9, 0, tzinfo=tz),
        ends_at_local=datetime(2026, 9, 8, 13, 0, tzinfo=tz),
        status="draft",
    )
    # nowage_id: published 9-17 Wed (8h × $0 attributable; counted in
    # unknown_wage_user_ids and contributes 0 cents).
    nowage_pub_id = _seed_entry(
        user_id=nowage_id,
        creator_id=admin_id,
        business_date_=week_start + timedelta(days=2),
        starts_at_local=datetime(2026, 9, 9, 9, 0, tzinfo=tz),
        ends_at_local=datetime(2026, 9, 9, 17, 0, tzinfo=tz),
        status="published",
    )

    # ============================================================
    # 1) SERVICE-LEVEL compute_labor_cost
    # ============================================================
    print("===== compute_labor_cost =====")
    db = SessionLocal()
    try:
        entries = (
            db.query(StaffScheduleEntry)
            .filter(
                StaffScheduleEntry.id.in_(
                    [paid_pub_id, paid_draft_id, nowage_pub_id]
                )
            )
            .all()
        )
        wage_map = {paid_id: Decimal("25.00"), nowage_id: None}
        result = staff_schedule.compute_labor_cost(entries, wage_map)
        assert result["total_cents"] == 30000, result
        assert result["published_cents"] == 20000, result
        assert result["draft_cents"] == 10000, result
        assert result["unknown_wage_user_ids"] == [nowage_id], result

        # Zero-duration entry skipped.
        odd = StaffScheduleEntry(
            user_id=paid_id,
            business_date=week_start,
            starts_at_local=datetime(2026, 9, 7, 10, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 9, 7, 10, 0, tzinfo=tz),
            status="draft",
            attendance_status="scheduled",
            late_grace_minutes=30,
            source="manual",
            created_by_user_id=admin_id,
        )
        result2 = staff_schedule.compute_labor_cost([odd], wage_map)
        assert result2["total_cents"] == 0
    finally:
        db.close()

    # ============================================================
    # 2) ADMIN WEEK ENDPOINT — labor_cost in payload
    # ============================================================
    print("===== admin /week labor_cost =====")
    resp = client.get(
        "/api/admin/schedule/week",
        headers=admin_hdr,
        params={
            "week_start": week_start.isoformat(),
            "user_ids": [paid_id, nowage_id],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "labor_cost" in body, body.keys()
    lc = body["labor_cost"]
    # Scoping to our two seeded users isolates the assertion — no
    # residue from other smokes can inflate. Equality is safe here.
    assert lc["total_cents"] == 30000, lc
    assert lc["published_cents"] == 20000, lc
    assert lc["draft_cents"] == 10000, lc
    assert nowage_id in lc["unknown_wage_user_ids"], lc

    # ============================================================
    # 3) Global-week fetch — assert per-user >= our seeded floor.
    # ============================================================
    # Per "global-pass smokes assert per-user, not global counts": the
    # un-scoped week call may include other dev-DB stylists' entries.
    # We can't pin total to 30000, but the labor_cost.total_cents must
    # be AT LEAST our seed contribution.
    print("===== admin /week global labor_cost floor =====")
    resp = client.get(
        "/api/admin/schedule/week",
        headers=admin_hdr,
        params={"week_start": week_start.isoformat()},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    lc = body["labor_cost"]
    assert lc["total_cents"] >= 30000, lc
    assert nowage_id in lc["unknown_wage_user_ids"], lc

    # ============================================================
    # 4) Empty user_ids early-return still carries zeroed labor_cost.
    # ============================================================
    print("===== empty user_ids zeroed labor_cost =====")
    # Mimic the early-return branch by passing a single bogus id that
    # doesn't intersect any active user. `user_ids=[]` is the literal
    # short-circuit; we exercise it via a positive but empty filter.
    from services import staff_schedule as svc

    db = SessionLocal()
    try:
        empty = svc.list_week(db, week_start=week_start, user_ids=[])
        assert empty["staff"] == []
        assert empty["entries"] == []
        assert empty["labor_cost"]["total_cents"] == 0
        assert empty["labor_cost"]["unknown_wage_user_ids"] == []
        # Empty-scope payload still carries a zeroed labor_target.
        assert empty["labor_target"]["actual_sales_cents"] == 0
    finally:
        db.close()

    # ============================================================
    # 5) labor_target — Phase F (Epic 6.2)
    # ============================================================
    # Seed an invoice with issue_date in the week and toggle the
    # business_profile.target_labor_pct. The chip math is:
    #
    #   target_sales_cents = labor_cost_cents * 100 / target_labor_pct
    #
    # With labor_cost_cents=30000 and target_labor_pct=25 → 120000.
    # actual_sales_cents must be at least our seeded contribution
    # (we use >= to stay robust against dev-DB residue per the
    # global-pass-smokes convention).
    print("===== labor_target via /week =====")

    global _original_target_labor_pct  # noqa: PLW0603

    seeded_invoice_paid = 7500  # $75 in cents
    db = SessionLocal()
    try:
        # Capture original singleton value for cleanup.
        profile = db.get(BusinessProfile, 1)
        _original_target_labor_pct = profile.target_labor_pct

        # Seed contact + event + invoice in the visible week.
        contact = Contact(
            display_name=f"Labor Contact {uuid.uuid4().hex[:6]}",
            email=f"labor-{uuid.uuid4().hex[:6]}@example.com",
        )
        db.add(contact)
        db.flush()
        _contact_ids.append(int(contact.id))
        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name="Labor Target Event",
            event_date=date(2027, 6, 15),
            quince_theme_colors=[],
            status="lead",
        )
        db.add(event)
        db.flush()
        _event_ids.append(int(event.id))

        inv = Invoice(
            event_id=event.id,
            contact_id=contact.id,
            invoice_number=f"LBR-{uuid.uuid4().hex[:10]}",
            status="partial",
            issue_date=week_start,
            total_cents=20000,
            paid_to_date_cents=seeded_invoice_paid,
            balance_cents=20000 - seeded_invoice_paid,
            created_by_user_id=admin_id,
            sold_by_user_id=paid_id,
        )
        db.add(inv)

        # Set the target to 25% (Decimal-safe via assignment).
        from decimal import Decimal as _Dec

        profile.target_labor_pct = _Dec("25.00")
        db.commit()
    finally:
        db.close()

    resp = client.get(
        "/api/admin/schedule/week",
        headers=admin_hdr,
        params={
            "week_start": week_start.isoformat(),
            "user_ids": [paid_id, nowage_id],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "labor_target" in body, body.keys()
    target = body["labor_target"]
    # 30000 labor_cost_cents * 100 / 25 = 120000 cents target sales.
    assert target["target_pct"] == "25.00", target
    assert target["target_sales_cents"] == 120000, target
    # Per-user >= floor: our seeded invoice contributes 7500.
    assert target["actual_sales_cents"] >= seeded_invoice_paid, target
    # gap_cents = target - actual; with our seed alone the gap is
    # positive (target 120000 > actual >= 7500).
    assert target["gap_cents"] is not None, target
    assert (
        target["gap_cents"]
        == target["target_sales_cents"] - target["actual_sales_cents"]
    ), target

    # When target_labor_pct is cleared, target_sales_cents goes None
    # but actual_sales_cents still surfaces.
    print("===== labor_target with target cleared =====")
    db = SessionLocal()
    try:
        profile = db.get(BusinessProfile, 1)
        profile.target_labor_pct = None
        db.commit()
    finally:
        db.close()

    resp = client.get(
        "/api/admin/schedule/week",
        headers=admin_hdr,
        params={
            "week_start": week_start.isoformat(),
            "user_ids": [paid_id, nowage_id],
        },
    )
    assert resp.status_code == 200, resp.text
    target = resp.json()["labor_target"]
    assert target["target_pct"] is None, target
    assert target["target_sales_cents"] is None, target
    assert target["gap_cents"] is None, target
    assert target["actual_sales_cents"] >= seeded_invoice_paid, target

    print("labor_cost smoke ok")


def create_access_token_for(user_id: int) -> str:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        return create_access_token(u)
    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
