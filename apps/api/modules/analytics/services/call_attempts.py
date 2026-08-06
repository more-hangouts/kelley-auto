"""Native-dialer call-attempt tracking service (Phase 7).

Writes/reads ``contact_call_attempts`` (migration 098). A salesperson taps a
customer number in the dashboard; the client logs the attempt here BEFORE the
device opens ``tel:``. Not Twilio Voice — no routing, no recording, no audio.

Design invariants:
  * Server owns identity: the caller passes the authenticated ``User``; a
    ``salesperson_user_id`` is NEVER accepted from the browser.
  * Phone stored normalized E.164 (falls back to the raw digits string only if
    normalization fails, so a logged attempt is never lost).
  * Create is idempotent on ``idempotency_key`` — a double-tap with the same
    key returns the existing row instead of inserting a duplicate.
  * Outcome updates are idempotent and validated; re-recording the same
    outcome is a successful no-op. We NEVER infer 'connected' — an outcome is
    only ever what the salesperson explicitly reports.
"""

from __future__ import annotations

import logging

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import Integer as _INT, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import Contact, ContactCallAttempt, User
from modules.core.services import activity_log, business_time
from modules.core.services.phone import normalize_phone_e164

log = logging.getLogger(__name__)

# Kept in sync with migration 098's CHECK and the API OUTCOME literal.
CALL_OUTCOMES: frozenset[str] = frozenset(
    {
        "call_initiated",
        "connected",
        "left_voicemail",
        "no_answer",
        "busy",
        "wrong_number",
        "cancelled",
    }
)
# Outcomes a salesperson may report (everything except the birth state).
REPORTABLE_OUTCOMES: frozenset[str] = CALL_OUTCOMES - {"call_initiated"}

_NOTES_MAX = 2000


class CallAttemptError(Exception):
    def __init__(self, code: str, http_status: int = 400):
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mirror_to_activity(
    db: Session,
    *,
    attempt: ContactCallAttempt,
    activity_type: str,
    user: User | None,
) -> None:
    """Mirror a call milestone onto the linked deal's Activity timeline.

    Calls live in their own table (migration 098) because an outcome
    transitions in place, which an append-only audit stream can't model.
    The cost was that the deal timeline never showed calls at all — a rep
    looking at a deal couldn't see that anyone had phoned. This writes the
    two milestones worth seeing there: the call going out, and the outcome
    coming back.

    No-ops for a contact-only call: ``activity_log.event_id`` is NOT NULL,
    so an attempt with no deal has no timeline to write to. Never raises —
    a failed mirror must not roll back the call tracking that is the
    caller's actual job.
    """
    if attempt.event_id is None:
        return

    actor_user_id = user.id if user is not None else attempt.salesperson_user_id
    try:
        activity_log.log_activity(
            db,
            event_id=attempt.event_id,
            actor_kind="staff" if actor_user_id else "system",
            actor_user_id=actor_user_id,
            activity_type=activity_type,
            subject_kind="contact_call_attempt",
            subject_id=attempt.id,
            # No phone number, no call notes — activity_log forbids PII in
            # metadata; the contact_call_attempts row keeps both.
            payload={
                "attempt_id": attempt.id,
                "outcome": attempt.outcome,
                "source": attempt.source,
            },
        )
    except Exception:  # pragma: no cover — defensive
        log.exception("call attempt %s could not be mirrored to activity", attempt.id)


def log_call_attempt(
    db: Session,
    *,
    contact: Contact,
    user: User,
    raw_phone: str,
    event_id: int | None = None,
    source: str | None = None,
    idempotency_key: str | None = None,
) -> tuple[ContactCallAttempt, bool]:
    """Create a ``call_initiated`` attempt. Returns ``(attempt, created)``.

    Idempotent: if ``idempotency_key`` matches an existing row (double-tap),
    the existing attempt is returned with ``created=False`` and nothing new is
    written. Identity comes from ``user`` — the browser cannot spoof it.
    """
    if idempotency_key:
        existing = (
            db.query(ContactCallAttempt)
            .filter(ContactCallAttempt.idempotency_key == idempotency_key)
            .one_or_none()
        )
        if existing is not None:
            return existing, False

    phone = normalize_phone_e164(raw_phone or "") or (raw_phone or "").strip()
    if not phone:
        raise CallAttemptError("phone_required", http_status=422)

    attempt = ContactCallAttempt(
        contact_id=contact.id,
        salesperson_user_id=user.id,
        salesperson_display_name=(user.full_name or user.username),
        event_id=event_id,
        phone_e164=phone[:20],
        outcome="call_initiated",
        outcome_pending=True,
        source=(source or None) and str(source)[:40],
        idempotency_key=idempotency_key,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(attempt)
    try:
        db.flush()
    except IntegrityError:
        # Race: a concurrent double-tap inserted the same key between our
        # SELECT and INSERT. Roll back to the pre-insert state and return the
        # winner — still idempotent, no duplicate.
        db.rollback()
        if idempotency_key:
            winner = (
                db.query(ContactCallAttempt)
                .filter(ContactCallAttempt.idempotency_key == idempotency_key)
                .one_or_none()
            )
            if winner is not None:
                return winner, False
        raise

    _mirror_to_activity(
        db,
        attempt=attempt,
        activity_type=activity_log.CALL_INITIATED,
        user=user,
    )
    return attempt, True


def start_bridge_call(
    db: Session,
    *,
    contact: Contact,
    user: User,
    rep_number: str,
    event_id: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[ContactCallAttempt, "object"]:
    """Twilio Voice click-to-call bridge (business-number call path).

    Logs a call attempt for the CONTACT's number (reusing ``log_call_attempt``,
    so the same tracking/idempotency/attribution applies and managers see the
    call in the exact same place as native-dialer calls), then asks Twilio to
    ring the rep first and bridge to the contact. ``source`` is stamped
    ``twilio_bridge`` so the two call paths are distinguishable in reporting.

    Returns ``(attempt, VoiceCallResult)``. The Twilio call is only attempted on
    a fresh attempt; an idempotent replay (same key) skips re-dialing and
    returns a ``not_configured``/``ok=False`` placeholder-free result carrying
    the reason so the caller can decide. Raises ``CallAttemptError`` only for
    input problems (missing contact phone); Twilio failures come back as a
    non-ok result, never an exception.

    Imports of the voice transport are local so the analytics module has no
    hard import dependency on the messaging module at load time.
    """
    from modules.messaging.services import voice_transport

    contact_number = normalize_phone_e164(contact.phone_e164 or contact.phone or "")
    if not contact_number:
        raise CallAttemptError("contact_phone_missing", http_status=422)

    rep_dial = normalize_phone_e164(rep_number or "")
    if not rep_dial:
        raise CallAttemptError("rep_phone_missing", http_status=422)

    if not voice_transport.voice_transport_configured():
        raise CallAttemptError("voice_not_configured", http_status=503)

    attempt, created = log_call_attempt(
        db,
        contact=contact,
        user=user,
        raw_phone=contact_number,
        event_id=event_id,
        source="twilio_bridge",
        idempotency_key=idempotency_key,
    )

    if not created:
        # Idempotent replay of the same tap — the first call was already placed.
        # Don't dial a second time; report the existing attempt with no new SID.
        return attempt, voice_transport.VoiceCallResult(
            ok=True, status="duplicate", error_message="idempotent_replay"
        )

    # Persist the attempt so its id is available for the signed bridge token
    # (the token binds the callback to this specific attempt + contact number).
    db.flush()
    token = voice_transport.mint_bridge_token(
        call_attempt_id=attempt.id,
        contact_id=contact.id,
        dial_number=contact_number,
    )
    result = voice_transport.initiate_bridge_call(
        rep_number=rep_dial,
        callback_url=voice_transport.bridge_callback_url(token),
    )
    return attempt, result


def record_outcome(
    db: Session,
    *,
    attempt: ContactCallAttempt,
    outcome: str | None = None,
    notes: str | None = None,
) -> ContactCallAttempt:
    """Idempotently update an attempt's salesperson-reported outcome/notes.

    * ``outcome`` must be a REPORTABLE outcome (never 'call_initiated' — you
      cannot walk a call back to un-started). Re-recording the current outcome
      is a no-op success (idempotent).
    * We NEVER infer 'connected'. The outcome is only ever what is passed here
      explicitly by the salesperson.
    * A real outcome clears ``outcome_pending``. Notes-only updates are allowed
      and leave the outcome untouched.
    """
    if outcome is not None:
        if outcome not in REPORTABLE_OUTCOMES:
            raise CallAttemptError("invalid_outcome", http_status=422)
        attempt.outcome = outcome
        attempt.outcome_pending = False

    if notes is not None:
        trimmed = notes.strip()
        attempt.notes = trimmed[:_NOTES_MAX] if trimmed else None

    attempt.updated_at = _now()
    db.flush()

    if outcome is not None:
        _mirror_to_activity(
            db,
            attempt=attempt,
            activity_type=activity_log.CALL_OUTCOME_RECORDED,
            user=None,
        )
    return attempt


def get_attempt(
    db: Session, *, contact_id: int, attempt_id: int
) -> ContactCallAttempt | None:
    return (
        db.query(ContactCallAttempt)
        .filter(
            ContactCallAttempt.id == attempt_id,
            ContactCallAttempt.contact_id == contact_id,
        )
        .one_or_none()
    )


def list_for_contact(
    db: Session, *, contact_id: int, limit: int = 100
) -> list[ContactCallAttempt]:
    return (
        db.query(ContactCallAttempt)
        .filter(ContactCallAttempt.contact_id == contact_id)
        .order_by(ContactCallAttempt.created_at.desc(), ContactCallAttempt.id.desc())
        .limit(max(1, min(limit, 500)))
        .all()
    )


def serialize(attempt: ContactCallAttempt) -> dict:
    return {
        "id": attempt.id,
        "contact_id": attempt.contact_id,
        "salesperson_user_id": attempt.salesperson_user_id,
        "salesperson_display_name": attempt.salesperson_display_name,
        "event_id": attempt.event_id,
        "phone_e164": attempt.phone_e164,
        "outcome": attempt.outcome,
        "outcome_pending": attempt.outcome_pending,
        "notes": attempt.notes,
        "source": attempt.source,
        "created_at": attempt.created_at.isoformat() if attempt.created_at else None,
        "updated_at": attempt.updated_at.isoformat() if attempt.updated_at else None,
    }


# ─── Manager aggregation (business-local dates) ──────────────────────────────


def _local_day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """Return the UTC [start, end) covering one business-local calendar day."""
    tz = business_time.shop_tz()
    start_local = datetime.combine(day, time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def summary_by_rep(
    db: Session, *, day: date | None = None
) -> tuple[date, list[dict]]:
    """Per-salesperson call counts for one business-local day (default today).

    ``initiated`` = total attempts logged. ``connected``/``left_voicemail``/
    ``no_answer`` count reported outcomes; ``pending`` = still call_initiated.
    call_initiated is NEVER counted as connected — reports read the explicit
    outcome only.
    """
    day = day or business_time.business_date()
    start_utc, end_utc = _local_day_bounds_utc(day)

    rows = (
        db.query(
            ContactCallAttempt.salesperson_user_id.label("uid"),
            func.max(ContactCallAttempt.salesperson_display_name).label("name"),
            func.count(ContactCallAttempt.id).label("initiated"),
            func.sum((ContactCallAttempt.outcome == "connected").cast(_INT)).label("connected"),
            func.sum((ContactCallAttempt.outcome == "left_voicemail").cast(_INT)).label("voicemail"),
            func.sum((ContactCallAttempt.outcome == "no_answer").cast(_INT)).label("no_answer"),
            func.sum(ContactCallAttempt.outcome_pending.cast(_INT)).label("pending"),
        )
        .filter(
            ContactCallAttempt.created_at >= start_utc,
            ContactCallAttempt.created_at < end_utc,
        )
        .group_by(ContactCallAttempt.salesperson_user_id)
        .all()
    )
    out = [
        {
            "salesperson_user_id": r.uid,
            "salesperson_display_name": r.name,
            "initiated": int(r.initiated or 0),
            "connected": int(r.connected or 0),
            "left_voicemail": int(r.voicemail or 0),
            "no_answer": int(r.no_answer or 0),
            "pending": int(r.pending or 0),
        }
        for r in rows
    ]
    out.sort(key=lambda d: d["initiated"], reverse=True)
    return day, out


def calls_today_count(db: Session, *, user_id: int | None = None) -> int:
    """Total attempts logged so far in the current business-local day,
    optionally scoped to one salesperson."""
    start_utc, end_utc = _local_day_bounds_utc(business_time.business_date())
    q = db.query(func.count(ContactCallAttempt.id)).filter(
        ContactCallAttempt.created_at >= start_utc,
        ContactCallAttempt.created_at < end_utc,
    )
    if user_id is not None:
        q = q.filter(ContactCallAttempt.salesperson_user_id == user_id)
    return int(q.scalar() or 0)


def recent_calls(db: Session, *, limit: int = 25) -> list[dict]:
    """Most-recent call attempts across the floor, with contact display name,
    for the manager's recent-activity list."""
    rows = (
        db.query(ContactCallAttempt, Contact.display_name)
        .outerjoin(Contact, Contact.id == ContactCallAttempt.contact_id)
        .order_by(ContactCallAttempt.created_at.desc(), ContactCallAttempt.id.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
    result = []
    for attempt, contact_name in rows:
        d = serialize(attempt)
        d["contact_display_name"] = contact_name
        result.append(d)
    return result
