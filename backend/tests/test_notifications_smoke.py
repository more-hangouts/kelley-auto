"""Smoke tests for the notification pipeline.

Walks through:
  1. Book an appointment via the public API → verify the right notification
     jobs were enqueued at the right due_at offsets.
  2. Run the worker once → confirmation + internal jobs flip to 'sent', the
     enrichment + reminder jobs stay pending (their due_at is in the future).
  3. Reschedule → old reminder/enrichment cancelled, new ones queued.
  4. Cancel → cancellation email queued, pending jobs cancelled.
  5. Reminder for a cancelled appointment is skipped at dispatch time.
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
    "SECRET_KEY", "test-key-not-for-production-just-smoke-testing-only-please"
)
# Force the NullEmailTransport for these tests by clearing SMTP_HOST.
os.environ["SMTP_HOST"] = ""
os.environ["SMTP_FROM_EMAIL"] = ""

from fastapi.testclient import TestClient
from sqlalchemy import text as sql_text

from api.server import app
from database.connection import SessionLocal
from database.models import Appointment, AppointmentEnrichmentResponse, NotificationJob
from services import booking_service, notification_service
from services.booking_tokens import cancel_url, reschedule_url
from services.notification_templates import (
    render_booking_confirmation,
    render_enrichment_invitation,
    render_reminder,
)


client = TestClient(app)


def _next_open_slot():
    db = SessionLocal()
    try:
        for offset in range(0, 30):
            d = date.today() + timedelta(days=offset)
            days = booking_service.compute_availability(
                db, from_date=d, to_date=d, min_lead_minutes=120
            )
            for day in days:
                for s in day["slots"]:
                    return s["start"].astimezone(timezone.utc), s["duration_minutes"]
    finally:
        db.close()
    raise RuntimeError("no slots")


def _jobs_for(appt_id):
    db = SessionLocal()
    try:
        return (
            db.query(NotificationJob)
            .filter(NotificationJob.appointment_id == appt_id)
            .order_by(NotificationJob.id.asc())
            .all()
        )
    finally:
        db.close()


def _cleanup(event_id):
    db = SessionLocal()
    try:
        ids = [
            r[0]
            for r in db.execute(
                sql_text("SELECT id FROM appointments WHERE event_id LIKE :p"),
                {"p": event_id + "%"},
            ).all()
        ]
        if ids:
            db.execute(
                sql_text("DELETE FROM notification_jobs WHERE appointment_id = ANY(:ids)"),
                {"ids": ids},
            )
            db.execute(
                sql_text("DELETE FROM appointments WHERE rescheduled_from_id = ANY(:ids)"),
                {"ids": ids},
            )
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:ids)"),
                {"ids": ids},
            )
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. Book → jobs enqueued
# ---------------------------------------------------------------------------

slot_start_utc, duration = _next_open_slot()
event_id = f"notif-smoke-{uuid.uuid4()}"
visitor_id = str(uuid.uuid4())

book_payload = {
    "slot_start": slot_start_utc.isoformat(),
    "slot_duration_minutes": duration,
    "parent_first_name": "Notif",
    "parent_last_name": "Tester",
    "celebrant_first_name": "Notif Smoke",
    "event_date": (date.today() + timedelta(days=180)).isoformat(),
    "party_size": "pair",
    "phone": "(210) 555-0177",
    "email": "notif-smoke@example.com",
    "event_id": event_id,
    "visitor_id": visitor_id,
    "session_id": "notif-smoke-session",
    "device": {"user_agent": "smoke", "browser_timezone": "America/Chicago"},
    "behavior": {"time_on_widget_ms": 30000, "interaction_count": 8, "steps_completed": 3},
}

try:
    resp = client.post("/api/booking/appointments", json=book_payload)
    assert resp.status_code == 201, resp.text
    appt_id = None
    appt_for_token = None
    db = SessionLocal()
    try:
        appt = db.query(Appointment).filter(Appointment.event_id == event_id).first()
        appt_id = appt.id
        # G1: URL helpers need the Appointment row. Detach for use after close.
        db.expunge(appt)
        appt_for_token = appt
    finally:
        db.close()

    jobs = _jobs_for(appt_id)
    kinds = sorted({j.kind for j in jobs})
    # Internal notification only fires when BOOKING_INTERNAL_NOTIFICATION_EMAILS is set.
    expected = {"booking_confirmation", "enrichment_invitation"}
    if "reminder" in {j.kind for j in jobs}:
        expected.add("reminder")
    assert expected.issubset(set(kinds)), f"missing kinds, got {kinds}"
    print(f"new-booking enqueue ok ({len(jobs)} jobs: {kinds})")

    confirm_job = next(j for j in jobs if j.kind == "booking_confirmation")
    enrich_job = next(j for j in jobs if j.kind == "enrichment_invitation")
    now = datetime.now(timezone.utc)
    assert confirm_job.due_at <= now + timedelta(seconds=5), "confirmation should be due immediately"
    assert enrich_job.due_at > now, "enrichment should be due in the future (T+2min)"
    enrich_offset_min = (enrich_job.due_at - now).total_seconds() / 60
    assert 1.5 <= enrich_offset_min <= 2.5, enrich_offset_min
    print(f"due_at offsets ok (enrichment +{enrich_offset_min:.1f}min)")

    # -----------------------------------------------------------------
    # Phase 7: template copy + token URL + conditional reminder CTA
    # -----------------------------------------------------------------
    db = SessionLocal()
    try:
        appt_for_render = db.get(Appointment, appt_id)

        confirm = render_booking_confirmation(appt_for_render)
        assert "Complete your Boutique Experience Profile" in confirm.text, confirm.text
        assert "Complete your Boutique Experience Profile" in confirm.html, "html missing CTA"
        assert "/fit-prep.html?token=" in confirm.text, confirm.text

        invite = render_enrichment_invitation(appt_for_render)
        assert invite.subject == "Complete your Boutique Experience Profile", invite.subject
        assert "/fit-prep.html?token=" in invite.text, invite.text

        # Reminder when no profile is attached: CTA is present.
        rem_open = render_reminder(appt_for_render)
        assert "Complete your Boutique Experience Profile" in rem_open.text, rem_open.text
        assert "Complete your Boutique Experience Profile" in rem_open.html

        # Attach a complete profile and re-render: CTA disappears.
        profile_row = AppointmentEnrichmentResponse(
            appointment_id=appt_id,
            source="post_booking_email",
            bust_inches=36.5,
            style_preference="ball_gown",
            summary="phase 7 reminder smoke",
            submitted_at=datetime.now(timezone.utc),
        )
        db.add(profile_row)
        db.commit()
        db.refresh(appt_for_render)

        rem_closed = render_reminder(appt_for_render)
        assert "Complete your Boutique Experience Profile" not in rem_closed.text, \
            "reminder should drop the CTA when a profile is attached"
        assert "Complete your Boutique Experience Profile" not in rem_closed.html

        # Reminder still includes the slot/quick-prep block regardless.
        assert "Quick prep" in rem_closed.html

        # Clean up the synthetic profile row.
        db.delete(profile_row)
        db.commit()
    finally:
        db.close()
    print("template copy + token URL + conditional reminder CTA ok")

    # ---------------------------------------------------------------------
    # 2. Run worker once → immediate jobs sent, future ones still pending
    # ---------------------------------------------------------------------

    db = SessionLocal()
    try:
        processed = notification_service.run_once(db)
    finally:
        db.close()

    jobs = _jobs_for(appt_id)
    sent = [j for j in jobs if j.status == "sent"]
    pending = [j for j in jobs if j.status == "pending"]
    assert any(j.kind == "booking_confirmation" and j.status == "sent" for j in jobs), \
        "booking_confirmation should have flipped to sent"
    assert all(j.status == "pending" for j in jobs if j.kind in ("enrichment_invitation", "reminder")), \
        "future-due jobs should still be pending"
    print(f"worker dispatch ok ({len(sent)} sent, {len(pending)} pending)")

    # ---------------------------------------------------------------------
    # 3. Reschedule → old pending cancelled, new appt has its own jobs
    # ---------------------------------------------------------------------

    # Find another open slot (different from current)
    new_start_utc = None
    new_dur = None
    db = SessionLocal()
    try:
        for offset in range(1, 30):
            d = date.today() + timedelta(days=offset)
            days = booking_service.compute_availability(
                db, from_date=d, to_date=d, min_lead_minutes=120
            )
            for day in days:
                for s in day["slots"]:
                    candidate = s["start"].astimezone(timezone.utc)
                    if candidate != slot_start_utc:
                        new_start_utc = candidate
                        new_dur = s["duration_minutes"]
                        break
                if new_start_utc:
                    break
            if new_start_utc:
                break
    finally:
        db.close()
    assert new_start_utc, "could not find a different slot to reschedule to"

    resched_link = reschedule_url(appt_for_token)
    token = resched_link.rsplit("/", 1)[1]
    resp = client.post(
        f"/api/booking/reschedule/{token}",
        json={"slot_start": new_start_utc.isoformat(), "slot_duration_minutes": new_dur},
    )
    assert resp.status_code == 200, resp.text
    new_code = resp.json()["confirmation_code"]
    # D1: API returns the hyphenated display form; canonicalise back to
    # the stored form for direct ORM lookup.
    from services.booking_service import normalize_confirmation_code  # noqa: E402
    new_code_canon = normalize_confirmation_code(new_code)
    db = SessionLocal()
    try:
        new_appt = (
            db.query(Appointment).filter(Appointment.confirmation_code == new_code_canon).first()
        )
        new_appt_id = new_appt.id
        db.expunge(new_appt)
        new_appt_for_token = new_appt
    finally:
        db.close()

    old_jobs = _jobs_for(appt_id)
    new_jobs = _jobs_for(new_appt_id)
    # Original pending jobs should be cancelled.
    pending_old = [j for j in old_jobs if j.status == "pending"]
    assert pending_old == [], f"original pending jobs should be cancelled, got {[j.kind for j in pending_old]}"
    # New appt should have a reschedule_confirmation queued.
    assert any(j.kind == "reschedule_confirmation" for j in new_jobs), \
        f"reschedule_confirmation missing on new appt, got {[j.kind for j in new_jobs]}"
    print(f"reschedule cascades ok (old pending cancelled, new has {len(new_jobs)} jobs)")

    # ---------------------------------------------------------------------
    # 4. Cancel new appt → cancellation queued, pending cancelled
    # ---------------------------------------------------------------------

    cancel_link = cancel_url(new_appt_for_token)
    token = cancel_link.rsplit("/", 1)[1]
    resp = client.post(f"/api/booking/cancel/{token}", json={"reason": "smoke cleanup"})
    assert resp.status_code == 200, resp.text

    after_cancel_jobs = _jobs_for(new_appt_id)
    assert any(
        j.kind == "cancellation_confirmation" and j.status == "pending"
        for j in after_cancel_jobs
    ), "cancellation_confirmation should be pending"
    pending_others = [
        j for j in after_cancel_jobs
        if j.status == "pending" and j.kind != "cancellation_confirmation"
    ]
    assert pending_others == [], f"non-cancellation pending jobs should be cancelled, got {[j.kind for j in pending_others]}"
    print("cancel cascades ok")

    # ---------------------------------------------------------------------
    # 5. Run worker — cancellation_confirmation sends; if any reminder were
    #    pending for the cancelled appt it would be skipped at dispatch.
    # ---------------------------------------------------------------------

    # Synthesize a "due-now" reminder for the cancelled appt to confirm skip behavior.
    db = SessionLocal()
    try:
        reminder = NotificationJob(
            kind="reminder",
            channel="email",
            appointment_id=new_appt_id,
            recipient="notif-smoke@example.com",
            due_at=datetime.now(timezone.utc),
            payload={},
        )
        db.add(reminder)
        db.commit()
        synth_id = reminder.id
    finally:
        db.close()

    db = SessionLocal()
    try:
        notification_service.run_once(db)
    finally:
        db.close()

    db = SessionLocal()
    try:
        synth = db.query(NotificationJob).filter(NotificationJob.id == synth_id).first()
        assert synth.status == "cancelled", f"reminder for cancelled appt should be cancelled, got {synth.status}"
        assert "status=cancelled" in (synth.last_error or "")
    finally:
        db.close()
    print("reminder for cancelled appt skipped ok")

finally:
    _cleanup(event_id)
    print("cleanup done")


# ---------------------------------------------------------------------------
# Phase 7 amendment: calculator-first + reschedule-after-completion cases
# ---------------------------------------------------------------------------
# These exercise the bug fix where:
#  1) `enqueue_for_new_booking` must NOT schedule an enrichment_invitation
#     when a profile is already attached (calculator-first path).
#  2) `is_boutique_profile_attached` must return True for a newly
#     rescheduled appointment whose original appointment carries the
#     completed profile (cross-appointment, same crm_event_id).
# Both go through the service/template layer directly to keep the
# scenarios small and independent of slot availability.

from sqlalchemy import text as _t
from services.notification_templates import (
    is_boutique_profile_attached,
    render_booking_confirmation,
    render_reminder,
)
from services.notification_service import enqueue_for_new_booking
from database.models import Contact, Event, EventParticipant, EventStatusChangeEvent


def _seed_event_with_profile(prefix: str):
    """Returns (contact_id, event_id, appt_id, profile_id) all linked."""
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        contact = Contact(
            first_name="PhaseSeven",
            last_name="Smoke",
            display_name="PhaseSeven Smoke",
            email=f"{prefix}-{suffix}@example.com",
            phone="(210) 555-0188",
            phone_e164=f"+1888{suffix[:4].zfill(4)}",
        )
        db.add(contact); db.commit(); db.refresh(contact)

        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"{prefix} smoke",
            status="lead",
        )
        db.add(event); db.commit(); db.refresh(event)
        db.add(EventStatusChangeEvent(
            event_id=event.id,
            from_status=None,
            to_status="lead",
        ))
        db.add(EventParticipant(
            event_id=event.id,
            contact_id=contact.id,
            role="quinceanera",
            display_name=contact.display_name,
            phone=contact.phone,
            email=contact.email,
        ))
        db.commit()

        appt = Appointment(
            confirmation_code=f"BX-P7{suffix[:5].upper()}",
            slot_start_at=datetime.now(timezone.utc) + timedelta(days=2),
            slot_end_at=datetime.now(timezone.utc) + timedelta(days=2, hours=1),
            slot_duration_minutes=60,
            timezone="America/Chicago",
            celebrant_first_name="PhaseSeven",
            party_size_bucket="solo",
            phone="(210) 555-0188",
            phone_e164=contact.phone_e164,
            email=contact.email,
            status="confirmed",
            contact_id=contact.id,
            crm_event_id=event.id,
            event_id=f"evt-{prefix}-{suffix}",
        )
        db.add(appt); db.commit(); db.refresh(appt)

        profile = AppointmentEnrichmentResponse(
            appointment_id=appt.id,
            source="pre_booking",
            bust_inches=36.5,
            style_preference="ball_gown",
            summary="phase 7 amendment seed",
            submitted_at=datetime.now(timezone.utc),
        )
        db.add(profile); db.commit(); db.refresh(profile)
        return contact.id, event.id, appt.id, profile.id
    finally:
        db.close()


def _seed_appt_on_event(event_id: int, contact_id: int, code: str, days: int):
    db = SessionLocal()
    try:
        contact = db.get(Contact, contact_id)
        appt = Appointment(
            confirmation_code=code,
            slot_start_at=datetime.now(timezone.utc) + timedelta(days=days),
            slot_end_at=datetime.now(timezone.utc) + timedelta(days=days, hours=1),
            slot_duration_minutes=60,
            timezone="America/Chicago",
            celebrant_first_name="PhaseSeven",
            party_size_bucket="solo",
            phone="(210) 555-0188",
            phone_e164=contact.phone_e164,
            email=contact.email,
            status="confirmed",
            contact_id=contact.id,
            crm_event_id=event_id,
            event_id=f"evt-{code}",
        )
        db.add(appt); db.commit(); db.refresh(appt)
        return appt.id
    finally:
        db.close()


def _delete_seed(contact_id: int, event_id: int):
    db = SessionLocal()
    try:
        appt_ids = [
            r[0] for r in db.execute(
                _t("SELECT id FROM appointments WHERE crm_event_id = :eid"),
                {"eid": event_id},
            ).all()
        ]
        if appt_ids:
            db.execute(
                _t("DELETE FROM appointment_enrichment_responses WHERE appointment_id = ANY(:ids)"),
                {"ids": appt_ids},
            )
            db.execute(
                _t("DELETE FROM notification_jobs WHERE appointment_id = ANY(:ids)"),
                {"ids": appt_ids},
            )
        db.execute(_t("DELETE FROM event_status_change_events WHERE event_id = :eid"), {"eid": event_id})
        db.execute(_t("DELETE FROM event_participants WHERE event_id = :eid"), {"eid": event_id})
        if appt_ids:
            db.execute(_t("DELETE FROM appointments WHERE id = ANY(:ids)"), {"ids": appt_ids})
        db.execute(_t("DELETE FROM events WHERE id = :eid"), {"eid": event_id})
        db.execute(_t("DELETE FROM contacts WHERE id = :cid"), {"cid": contact_id})
        db.commit()
    finally:
        db.close()


# 1. Calculator-first booking: profile already attached at enqueue time.
contact_id, event_id_1, appt_id_1, profile_id_1 = _seed_event_with_profile("calc-first")
try:
    db = SessionLocal()
    try:
        appt = db.get(Appointment, appt_id_1)
        # Sanity: helper sees the attached profile.
        assert is_boutique_profile_attached(appt) is True

        enqueue_for_new_booking(db, appt)
        db.commit()

        kinds = [
            j.kind
            for j in db.query(NotificationJob)
            .filter(NotificationJob.appointment_id == appt_id_1)
            .all()
        ]
        assert "enrichment_invitation" not in kinds, (
            f"calculator-first booking should skip enrichment_invitation, "
            f"got jobs: {kinds}"
        )
        assert "booking_confirmation" in kinds, kinds
        print(f"calculator-first skips invitation ok (jobs: {sorted(kinds)})")

        # And the confirmation email itself drops the CTA.
        confirm = render_booking_confirmation(appt)
        assert "Complete your Boutique Experience Profile" not in confirm.text, \
            "confirmation should drop the CTA when profile is attached"
        assert "Complete your Boutique Experience Profile" not in confirm.html
        print("confirmation drops CTA when attached ok")
    finally:
        db.close()
finally:
    _delete_seed(contact_id, event_id_1)


# 2. Reschedule after profile completion: new appt on same crm_event_id,
#    profile lives on the original. Reminder for the new appt should still
#    suppress the CTA.
contact_id, event_id_2, original_appt_id, profile_id_2 = _seed_event_with_profile("reschedule")
new_appt_id = _seed_appt_on_event(event_id_2, contact_id, "BX-P7NEW", days=5)
try:
    db = SessionLocal()
    try:
        new_appt = db.get(Appointment, new_appt_id)
        assert new_appt.crm_event_id == event_id_2
        # Helper must span crm_event_id, not just appointment_id.
        assert is_boutique_profile_attached(new_appt) is True, (
            "is_boutique_profile_attached should return True when a sibling "
            "appointment on the same lead has a submitted profile"
        )

        rem = render_reminder(new_appt)
        assert "Complete your Boutique Experience Profile" not in rem.text, (
            "reminder for rescheduled appt should drop the CTA when the "
            "lead already has a completed profile"
        )
        assert "Complete your Boutique Experience Profile" not in rem.html

        # And the enqueue side also skips invitation for the rescheduled appt.
        from services.notification_service import enqueue_for_reschedule
        enqueue_for_reschedule(db, original_id=original_appt_id, new_appt=new_appt)
        db.commit()
        new_jobs = [
            j.kind
            for j in db.query(NotificationJob)
            .filter(NotificationJob.appointment_id == new_appt_id)
            .all()
        ]
        assert "enrichment_invitation" not in new_jobs, (
            f"reschedule should skip enrichment_invitation when the lead "
            f"already has a profile, got jobs: {new_jobs}"
        )
        print(f"reschedule-after-completion suppresses CTA + invite ok (jobs: {sorted(new_jobs)})")
    finally:
        db.close()
finally:
    _delete_seed(contact_id, event_id_2)


print("\nnotifications smoke ok")
