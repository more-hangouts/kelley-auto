"""Smoke tests for the Boutique Experience profile surface.

Covers:
  - empty submissions are rejected (Pydantic validator)
  - POST /api/booking/boutique-experience returns a profile id
  - POST /api/booking/appointments with `boutique_experience_profile_id`
    links the pre-booking profile to the new appointment
  - GET /api/events/{id} surfaces structured profile + status
  - GET /api/events/board surfaces per-event status
  - POST /api/booking/boutique-experience/{token} upserts in place
  - bad / wrong-purpose / blocked-status tokens are rejected

Self-contained: mints its own admin user and visitor + slot, cleans up.
Invoke with: ``venv/bin/python tests/test_boutique_experience_smoke.py``
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
from database.models import (  # noqa: E402
    Appointment,
    AppointmentEnrichmentResponse,
    User,
)
from services import booking_service  # noqa: E402
from services.booking_tokens import mint_token, reschedule_url  # noqa: E402


client = TestClient(app)


def _next_open_slot(skip: datetime | None = None) -> tuple[datetime, int]:
    """Return the next bookable slot (UTC start, duration_minutes).

    `skip` lets the reschedule case ask for a different slot than the one
    the original booking took, which would otherwise fail capacity checks.
    """
    today = date.today()
    db = SessionLocal()
    try:
        for offset in range(0, 30):
            d = today + timedelta(days=offset)
            days = booking_service.compute_availability(
                db, from_date=d, to_date=d, min_lead_minutes=120
            )
            for day in days:
                for slot in day["slots"]:
                    candidate = slot["start"].astimezone(timezone.utc)
                    if skip is not None and candidate == skip:
                        continue
                    return candidate, slot["duration_minutes"]
    finally:
        db.close()
    raise RuntimeError("no open slot found within 30 days — seed data missing?")


def _cleanup_event_id(event_id: str) -> None:
    """Drop appointment + auto-promoted event + linked profile, mirroring
    the cleanup pattern in tests/test_booking_smoke.py.

    Reschedule creates a second appointment row pointing at the original via
    `rescheduled_from_id`. That row carries the same `crm_event_id` and
    `contact_id` but a fresh (NULL) `event_id`, so we have to expand the
    cleanup set to include rescheduled descendants before dropping rows.
    """
    db = SessionLocal()
    try:
        rows = db.execute(
            sql_text(
                "SELECT id, contact_id, crm_event_id FROM appointments "
                "WHERE event_id = :eid"
            ),
            {"eid": event_id},
        ).all()
        seed_ids = [r[0] for r in rows]
        contact_ids = sorted({r[1] for r in rows if r[1] is not None})
        crm_event_ids = sorted({r[2] for r in rows if r[2] is not None})

        # Pull in rescheduled descendants by walking rescheduled_from_id
        # transitively. One pass is enough for a single reschedule but
        # the loop handles re-reschedules for free.
        all_ids = list(seed_ids)
        frontier = list(seed_ids)
        while frontier:
            children = [
                r[0]
                for r in db.execute(
                    sql_text(
                        "SELECT id FROM appointments "
                        "WHERE rescheduled_from_id = ANY(:ids)"
                    ),
                    {"ids": frontier},
                ).all()
            ]
            new = [c for c in children if c not in all_ids]
            if not new:
                break
            all_ids.extend(new)
            frontier = new

        if all_ids:
            db.execute(
                sql_text(
                    "DELETE FROM appointment_enrichment_responses "
                    "WHERE appointment_id = ANY(:ids)"
                ),
                {"ids": all_ids},
            )
        if crm_event_ids:
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:eids)"),
                {"eids": crm_event_ids},
            )
        if all_ids:
            # Children first so the rescheduled_from_id FK stays satisfied.
            db.execute(
                sql_text(
                    "DELETE FROM appointments "
                    "WHERE rescheduled_from_id = ANY(:ids)"
                ),
                {"ids": all_ids},
            )
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:ids)"),
                {"ids": all_ids},
            )
        if contact_ids:
            db.execute(
                sql_text(
                    "DELETE FROM contacts WHERE id = ANY(:cids) "
                    "AND NOT EXISTS ("
                    "  SELECT 1 FROM appointments WHERE contact_id = contacts.id"
                    ") "
                    "AND NOT EXISTS ("
                    "  SELECT 1 FROM events WHERE primary_contact_id = contacts.id"
                    ")"
                ),
                {"cids": contact_ids},
            )
        db.commit()
    finally:
        db.close()


def _make_admin() -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"bx-prof-smoke-{suffix}",
            email=f"bx-prof-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Boutique Profile Smoke Admin",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, u.email
    finally:
        db.close()


def _delete_admin(user_id: int) -> None:
    db = SessionLocal()
    try:
        db.execute(sql_text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. Validator rejects empty / placeholder-only payloads
# ---------------------------------------------------------------------------

resp = client.post("/api/booking/boutique-experience", json={})
assert resp.status_code == 422, resp.text
print("empty submission rejected ok")

resp = client.post(
    "/api/booking/boutique-experience",
    json={"summary": "   ", "visitor_id": "abc", "session_id": "xyz"},
)
assert resp.status_code == 422, resp.text
print("placeholder-only submission rejected ok")


# ---------------------------------------------------------------------------
# 2. Pre-booking profile create + link through booking submission
# ---------------------------------------------------------------------------

profile_payload = {
    "measurements": {
        "bust_inches": 36.5, "waist_inches": 28.0, "hips_inches": 39.0,
        "height_ft": 5, "height_in": 5,
    },
    "sizing": {
        "estimated_size_low": 8, "estimated_size_high": 10,
        "size_by_bust": 8, "size_by_waist": 8, "size_by_hips": 10,
        "chart_source": "Bella's XV reference formalwear chart",
        "off_chart": False,
    },
    "preferences": {
        "style": "ball_gown", "back": "corset", "budget": "1500_2000",
        "colors": "champagne, blush",
        "likes": "off-shoulder, sparkle",
        "avoids": "strapless",
    },
    "summary": "Smoke profile, size 8-10, ball gown, $1.5-2k",
    "visitor_id": str(uuid.uuid4()),
    "session_id": "bx-prof-smoke",
}

resp = client.post("/api/booking/boutique-experience", json=profile_payload)
assert resp.status_code == 201, resp.text
created = resp.json()
assert created["source"] == "pre_booking", created
profile_id = created["profile_id"]
print(f"pre-booking profile created ok (id={profile_id})")

slot_start_utc, duration = _next_open_slot()
event_id = f"bx-prof-smoke-{uuid.uuid4()}"
admin_id, admin_email = _make_admin()

booking_payload = {
    "slot_start": slot_start_utc.isoformat(),
    "slot_duration_minutes": duration,
    "parent_first_name": "Profile",
    "parent_last_name": "Tester",
    "celebrant_first_name": "Profile Smoke",
    "event_date": (date.today() + timedelta(days=180)).isoformat(),
    "party_size": "pair",
    "phone": "(210) 555-0143",
    "email": "bx-prof-smoke@example.com",
    "note": "Profile smoke",
    "event_id": event_id,
    "visitor_id": profile_payload["visitor_id"],
    "session_id": "bx-prof-smoke",
    "boutique_experience_profile_id": profile_id,
    "attribution": {
        "page_url": "https://shopbellasxv.com/",
        "utm_source": "smoke",
        "utm_campaign": "boutique-experience",
    },
    "device": {
        "device_type": "desktop",
        "user_agent": "smoke-test/1.0",
        "browser_timezone": "America/Chicago",
    },
    "behavior": {
        "time_on_widget_ms": 45000,
        "interaction_count": 12,
        "steps_completed": 3,
        "user_journey": [{"step": "date"}, {"step": "who"}, {"step": "contact"}],
    },
}

try:
    resp = client.post("/api/booking/appointments", json=booking_payload)
    assert resp.status_code == 201, resp.text
    booking_body = resp.json()
    code = booking_body["confirmation_code"]
    print(f"booking with profile_id ok ({code})")

    # Phase 5: AppointmentResponse exposes the tokenized profile URL +
    # whether a profile is already attached. Booking-with-profile_id
    # should report attached=true.
    assert booking_body["boutique_experience_attached"] is True, booking_body
    assert "/fit-prep.html?token=" in booking_body["boutique_experience_url"], booking_body
    print("response includes attached=true + token URL ok")

    db = SessionLocal()
    try:
        appt = db.query(Appointment).filter(Appointment.event_id == event_id).one()
        appt_id = appt.id
        crm_event_id = appt.crm_event_id
        # G1: mint_token + reschedule_url need the Appointment row.
        # Detach so we can pass it to the helpers after the session closes.
        db.expunge(appt)
        appt_for_token = appt
        profile_row = (
            db.query(AppointmentEnrichmentResponse)
            .filter(AppointmentEnrichmentResponse.id == profile_id)
            .one()
        )
        assert profile_row.appointment_id == appt_id, (
            f"profile {profile_id} not linked to appt {appt_id}; "
            f"linked to {profile_row.appointment_id}"
        )
    finally:
        db.close()
    print(f"profile linked to appointment id={appt_id} ok")

    # 3. Auth + event detail surfaces structured profile + status
    resp = client.post(
        "/api/auth/login",
        json={"email": admin_email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    print("admin login ok")

    resp = client.get(f"/api/events/{crm_event_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert len(detail["appointments"]) == 1
    appt_summary = detail["appointments"][0]

    assert appt_summary["boutique_experience_status"] == "complete", appt_summary
    assert appt_summary["boutique_experience_submitted_at"] is not None
    assert (
        appt_summary["boutique_experience_summary"]
        == "Smoke profile, size 8-10, ball gown, $1.5-2k"
    )

    bep = appt_summary["boutique_experience"]
    assert bep is not None, "structured profile missing on full detail"
    assert bep["profile_id"] == profile_id
    assert bep["source"] == "pre_booking"  # original write source preserved
    assert bep["bust_inches"] == 36.5
    assert bep["waist_inches"] == 28.0
    assert bep["estimated_size_low"] == 8
    assert bep["estimated_size_high"] == 10
    assert bep["chart_source"] == "Bella's XV reference formalwear chart"
    assert bep["off_chart"] is False
    assert bep["style"] == "ball_gown"
    assert bep["back"] == "corset"
    assert bep["budget"] == "1500_2000"
    assert bep["colors"] == "champagne, blush"
    assert bep["likes"] == "off-shoulder, sparkle"
    assert bep["avoids"] == "strapless"
    print("event detail surfaces structured profile + status ok")

    # 4. Board card status is complete
    resp = client.get("/api/events/board", headers=headers)
    assert resp.status_code == 200, resp.text
    cards = [
        c for col in resp.json()["columns"]
        for c in col["cards"]
        if c["id"] == crm_event_id
    ]
    assert len(cards) == 1, "expected exactly one matching board card"
    assert cards[0]["boutique_experience_status"] == "complete"
    print("board card status=complete ok")

    # 5. Token endpoint upserts in place; submitting again replaces summary
    token = mint_token(appt_for_token, "enrichment")
    second_payload = {
        **profile_payload,
        "summary": "Revised smoke summary",
    }
    resp = client.post(
        f"/api/booking/boutique-experience/{token}", json=second_payload
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["profile_id"] == profile_id, "upsert should reuse the existing row"
    assert body["confirmation_code"] == code
    assert body["source"] == "post_booking_email"

    db = SessionLocal()
    try:
        refreshed = db.get(AppointmentEnrichmentResponse, profile_id)
        assert refreshed.summary == "Revised smoke summary"
        # Original write source is preserved on upsert (only set if NULL).
        assert refreshed.source == "pre_booking"
    finally:
        db.close()
    print("token upsert reuses row + preserves original source ok")

    # 6. Confirmation-code + email path attaches from a fresh browser/device
    #    without relying on localStorage or the tokenized email URL.
    confirmation_payload = {
        "confirmation_code": code.lower(),
        "email": "BX-PROF-SMOKE@example.com",
        "profile": {
            **profile_payload,
            "summary": "Confirmation lookup summary",
        },
    }
    resp = client.post(
        "/api/booking/boutique-experience/confirm", json=confirmation_payload
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["profile_id"] == profile_id, body
    assert body["confirmation_code"] == code
    assert body["source"] == "post_booking_confirmation"

    db = SessionLocal()
    try:
        refreshed = db.get(AppointmentEnrichmentResponse, profile_id)
        assert refreshed.summary == "Confirmation lookup summary"
        assert refreshed.source == "pre_booking"
    finally:
        db.close()
    print("confirmation-code + email upsert reuses row ok")

    bad_lookup = {
        **confirmation_payload,
        "email": "wrong@example.com",
    }
    resp = client.post("/api/booking/boutique-experience/confirm", json=bad_lookup)
    assert resp.status_code == 404, resp.text
    print("confirmation-code + wrong email rejected ok")

    # 7. Token validator rejects bad / wrong-purpose tokens
    resp = client.post(
        "/api/booking/boutique-experience/not-a-real-token", json=profile_payload
    )
    assert resp.status_code == 404, resp.text
    print("bad token rejected ok")

    wrong_purpose_token = mint_token(appt_for_token, "cancel")
    resp = client.post(
        f"/api/booking/boutique-experience/{wrong_purpose_token}",
        json=profile_payload,
    )
    assert resp.status_code == 404, resp.text
    print("wrong-purpose token rejected ok")

    # 8. Reschedule keeps the profile attached to the original appointment;
    #    the new appointment shows not_started on its own row but the lead
    #    aggregates to "complete" on the board because the original is
    #    complete. Validates Phase 1's "do not duplicate profile rows on
    #    reschedule" + Phase 3's board-level any-appointment-complete
    #    aggregation + Phase 7's amendment behavior at the HTTP layer.
    new_slot_utc, new_dur = _next_open_slot(skip=slot_start_utc)
    resched_link = reschedule_url(appt_for_token)
    resched_token = resched_link.rsplit("/", 1)[1]
    resp = client.post(
        f"/api/booking/reschedule/{resched_token}",
        json={
            "slot_start": new_slot_utc.isoformat(),
            "slot_duration_minutes": new_dur,
        },
    )
    assert resp.status_code == 200, resp.text
    new_code = resp.json()["confirmation_code"]
    assert new_code != code
    # D1: API returns hyphenated display form; canonicalise for ORM lookup.
    from services.booking_service import normalize_confirmation_code  # noqa: E402
    new_code_canon = normalize_confirmation_code(new_code)

    db = SessionLocal()
    try:
        new_appt = (
            db.query(Appointment)
            .filter(Appointment.confirmation_code == new_code_canon)
            .one()
        )
        assert new_appt.crm_event_id == crm_event_id, (
            "reschedule should carry crm_event_id forward"
        )
        # Profile rows are NOT duplicated; the only row still belongs to
        # the original appointment.
        profile_rows = (
            db.query(AppointmentEnrichmentResponse)
            .join(Appointment,
                  Appointment.id == AppointmentEnrichmentResponse.appointment_id)
            .filter(Appointment.crm_event_id == crm_event_id)
            .all()
        )
        assert len(profile_rows) == 1, (
            f"expected exactly one profile row across the lead, got "
            f"{len(profile_rows)}"
        )
        assert profile_rows[0].appointment_id == appt_id
    finally:
        db.close()

    # Event detail returns both appointments; profile completion sits on
    # whichever appointment owns the row.
    resp = client.get(f"/api/events/{crm_event_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    appts_by_code = {a["confirmation_code"]: a for a in resp.json()["appointments"]}
    assert code in appts_by_code and new_code in appts_by_code, list(appts_by_code)
    original_summary = appts_by_code[code]
    new_summary = appts_by_code[new_code]
    assert original_summary["boutique_experience_status"] == "complete"
    assert original_summary["boutique_experience"] is not None
    assert new_summary["boutique_experience_status"] == "not_started"
    assert new_summary["boutique_experience"] is None
    print("event detail keeps profile on original after reschedule ok")

    # Board-level aggregation: lead remains "complete" because the original
    # appointment carries the submitted profile.
    resp = client.get("/api/events/board", headers=headers)
    cards = [
        c for col in resp.json()["columns"]
        for c in col["cards"]
        if c["id"] == crm_event_id
    ]
    assert cards and cards[0]["boutique_experience_status"] == "complete", (
        "board card should stay complete across reschedule"
    )
    print("board aggregation stays complete across reschedule ok")

    # 8. Cancelled appointments refuse profile updates.
    # G1: the reschedule in step 7 above already bumped the original
    # appointment's `tokens_invalidated_at`, so the pre-reschedule token
    # for this appointment now 404s via the revocation check BEFORE the
    # status check would have fired the old 409. This is the desired
    # post-G1 contract — revoked tokens leak less than status-mismatch
    # responses. A fresh post-cancel token would also be unable to even
    # decode against the new appointment, so 404 is the right answer
    # either way.
    db = SessionLocal()
    try:
        appt_to_cancel = db.get(Appointment, appt_id)
        appt_to_cancel.status = "cancelled"
        db.commit()
    finally:
        db.close()

    resp = client.post(
        f"/api/booking/boutique-experience/{token}", json=profile_payload
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "link is invalid or expired", resp.json()
    print("cancelled appt -> 404 (G1 revoked-token semantics) ok")

    # 9. Status flips back to not_started when submitted_at is cleared.
    #    After step 7 there are two appointments on this lead, so look up
    #    the original by confirmation_code instead of relying on list order.
    db = SessionLocal()
    try:
        db.query(AppointmentEnrichmentResponse).filter(
            AppointmentEnrichmentResponse.id == profile_id
        ).update({"submitted_at": None})
        db.commit()
    finally:
        db.close()

    resp = client.get(f"/api/events/{crm_event_id}", headers=headers)
    appts_by_code = {a["confirmation_code"]: a for a in resp.json()["appointments"]}
    original_summary = appts_by_code[code]
    assert original_summary["boutique_experience_status"] == "not_started"
    # Structured object survives so future "Started" state has a place to live.
    assert original_summary["boutique_experience"] is not None
    assert original_summary["boutique_experience"]["submitted_at"] is None
    print("cleared submitted_at -> status=not_started, object preserved ok")

finally:
    _cleanup_event_id(event_id)
    _delete_admin(admin_id)
    # The pre-booking profile cascades when the appointment goes, but a stray
    # row from a failure mid-flight is worth catching.
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "DELETE FROM appointment_enrichment_responses WHERE id = :id"
            ),
            {"id": profile_id},
        )
        db.commit()
    finally:
        db.close()
    print("cleanup done")

print("\nboutique experience smoke ok")
