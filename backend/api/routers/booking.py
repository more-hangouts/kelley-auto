"""Public booking widget endpoints.

All endpoints in this router are unauthenticated and CORS-allowed for the
marketing site. Programmatic rate limits land in B3: per-IP buckets on
every public POST, plus a per-email bucket on the confirmation-code
attach path so a brute-force search over confirmation codes against one
email is shut down before the row lookup fires.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.redis_rate_limit import enforce_or_raise, rate_limit
from config.settings import APP_TIMEZONE
from database.connection import get_db
from database.models import (
    Appointment,
    AppointmentEnrichmentResponse,
    AppointmentSessionEvent,
    AppointmentVisitor,
)
from services import (
    booking_service,
    contact_service,
    event_service,
    notification_service,
)
from services.event_service import EventServiceError
from services.booking_contracts import (
    AbandonRequest,
    AcknowledgedResponse,
    AppointmentResponse,
    AppointmentSubmission,
    AvailabilityDay,
    AvailabilityResponse,
    AvailabilitySlot,
    BoutiqueExperienceConfirmRequest,
    BoutiqueExperienceCreatedResponse,
    BoutiqueExperienceSubmission,
    BoutiqueExperienceTokenResponse,
    CancelRequest,
    RescheduleRequest,
    RescheduleSummary,
    SessionEventRequest,
    ThemeResponse,
)
from services.booking_tokens import (
    InvalidBookingToken,
    cancel_url,
    enrichment_url,
    ensure_not_revoked,
    reschedule_url,
    revoke_appointment_tokens,
    verify_token,
)

log = logging.getLogger(__name__)

router = APIRouter()

# Per-IP buckets for public booking surfaces. Sized to the action:
# - writes (create / pre-booking profile) are tight: real customers do
#   each once or twice per session.
# - telemetry (events + abandon) is generous: a single session can fire
#   many step events legitimately during heavy interaction.
# - tokenized routes (reschedule, cancel, profile-by-token) are medium:
#   the signed token is hard to forge, so we are mostly defending
#   against scripted DOS rather than guessing.
# - the confirmation-code attach path is tight per-IP AND tight per-email
#   so a brute-force search over codes for one email is stopped before
#   the row lookup, layered with D1's pending entropy boost.
_booking_create_ip_limit = rate_limit(
    bucket="booking_create_ip", limit=5, window=60
)
_booking_telemetry_ip_limit = rate_limit(
    bucket="booking_telemetry_ip", limit=240, window=60
)
_booking_profile_ip_limit = rate_limit(
    bucket="booking_profile_ip", limit=10, window=60
)
_booking_confirm_ip_limit = rate_limit(
    bucket="booking_confirm_ip", limit=10, window=60
)
_booking_token_ip_limit = rate_limit(
    bucket="booking_token_ip", limit=30, window=60
)


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------


@router.get("/theme", response_model=ThemeResponse)
def get_theme(db: Session = Depends(get_db)) -> ThemeResponse:
    settings = booking_service.get_theme_settings(db)
    return ThemeResponse(theme=settings.theme, copy_text=settings.copy, flow=settings.flow)


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


_MAX_RANGE_DAYS = 90


@router.get("/availability", response_model=AvailabilityResponse)
def get_availability(
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
    db: Session = Depends(get_db),
) -> AvailabilityResponse:
    if to_date < from_date:
        raise HTTPException(status_code=400, detail="`to` must be on or after `from`")
    if (to_date - from_date).days > _MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"date range cannot exceed {_MAX_RANGE_DAYS} days",
        )

    settings = booking_service.get_theme_settings(db)
    flow = settings.flow or {}
    min_lead = int(flow.get("min_lead_time_minutes", 0))
    max_days_ahead = int(flow.get("max_days_ahead", _MAX_RANGE_DAYS))

    today = datetime.now(booking_service.shop_tz()).date()
    cap = today + timedelta(days=max_days_ahead)
    capped_to = min(to_date, cap)
    if capped_to < from_date:
        capped_to = from_date

    raw_days = booking_service.compute_availability(
        db, from_date=from_date, to_date=capped_to, min_lead_minutes=min_lead
    )
    return AvailabilityResponse(
        timezone=APP_TIMEZONE,
        from_date=from_date,
        to_date=capped_to,
        days=[
            AvailabilityDay(
                date=day["date"],
                weekday=day["weekday"],
                slots=[AvailabilitySlot(**s) for s in day["slots"]],
            )
            for day in raw_days
        ],
    )


# ---------------------------------------------------------------------------
# Appointment submission
# ---------------------------------------------------------------------------


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _parse_visitor_id(raw: str | None) -> UUID | None:
    if not raw:
        return None
    try:
        return UUID(raw)
    except (ValueError, TypeError):
        return None


def _appointment_to_response(db: Session, appt: Appointment) -> AppointmentResponse:
    attached = (
        db.query(AppointmentEnrichmentResponse.id)
        .filter(AppointmentEnrichmentResponse.appointment_id == appt.id)
        .first()
        is not None
    )
    return AppointmentResponse(
        confirmation_code=booking_service.format_confirmation_code(appt.confirmation_code),
        slot_start=appt.slot_start_at,
        slot_end=appt.slot_end_at,
        timezone=appt.timezone,
        status=appt.status,
        reschedule_url=reschedule_url(appt),
        cancel_url=cancel_url(appt),
        boutique_experience_url=enrichment_url(appt),
        boutique_experience_attached=attached,
    )


@router.post(
    "/appointments",
    response_model=AppointmentResponse,
    status_code=201,
    dependencies=[Depends(_booking_create_ip_limit)],
)
def create_appointment(
    payload: AppointmentSubmission,
    request: Request,
    db: Session = Depends(get_db),
) -> AppointmentResponse:
    # Honeypot: silently 200 with a fake-looking response would be ideal, but
    # callers expect 201 + a code. Reject with a generic 400 instead.
    if payload.company_website:
        log.info("booking.honeypot_triggered event_id=%s", payload.event_id)
        raise HTTPException(status_code=400, detail="invalid submission")

    # Idempotency: if this event_id already produced an appointment, return it.
    existing = (
        db.query(Appointment).filter(Appointment.event_id == payload.event_id).first()
    )
    if existing is not None:
        return _appointment_to_response(db, existing)

    settings = booking_service.get_theme_settings(db)
    flow = settings.flow or {}
    min_lead = int(flow.get("min_lead_time_minutes", 0))

    slot_start = payload.slot_start
    if slot_start.tzinfo is None:
        slot_start = slot_start.replace(tzinfo=booking_service.shop_tz())
    slot_start_utc = slot_start.astimezone(timezone.utc)

    ok, reason = booking_service.slot_is_bookable(
        db,
        slot_start=slot_start_utc,
        slot_duration_minutes=payload.slot_duration_minutes,
        min_lead_minutes=min_lead,
    )
    if not ok:
        raise HTTPException(status_code=409, detail=f"slot unavailable: {reason}")

    slot_end_utc = slot_start_utc + timedelta(minutes=payload.slot_duration_minutes)

    bot_suspected = booking_service.looks_like_bot(
        time_on_widget_ms=payload.behavior.time_on_widget_ms,
        interaction_count=payload.behavior.interaction_count,
        steps_completed=payload.behavior.steps_completed,
        user_agent=payload.device.user_agent,
    )

    code = booking_service.generate_unique_confirmation_code(db)
    visitor_uuid = _parse_visitor_id(payload.visitor_id)
    phone_e164 = booking_service.normalize_phone_e164(payload.phone)

    contact, _was_new_contact = contact_service.find_or_create_contact(
        db,
        phone_e164=phone_e164,
        email=payload.email.lower(),
        phone=payload.phone,
        first_name=payload.parent_first_name,
        last_name=payload.parent_last_name,
    )

    if payload.marketing_consent and contact.marketing_consent_at is None:
        contact.marketing_consent_at = datetime.now(timezone.utc)

    appt = Appointment(
        confirmation_code=code,
        slot_start_at=slot_start_utc,
        slot_end_at=slot_end_utc,
        slot_duration_minutes=payload.slot_duration_minutes,
        timezone=APP_TIMEZONE,
        celebrant_first_name=payload.celebrant_first_name,
        celebrant_last_name=payload.celebrant_last_name,
        parent_first_name=payload.parent_first_name,
        parent_last_name=payload.parent_last_name,
        event_date=payload.event_date,
        party_size_bucket=payload.party_size,
        phone=payload.phone,
        phone_e164=phone_e164,
        email=payload.email.lower(),
        customer_note=payload.note,
        contact_id=contact.id,
        status="confirmed",
        visitor_id=visitor_uuid,
        session_id=payload.session_id,
        event_id=payload.event_id,
        page_url=payload.attribution.page_url,
        referrer_url=payload.attribution.referrer_url,
        utm_source=payload.attribution.utm_source,
        utm_medium=payload.attribution.utm_medium,
        utm_campaign=payload.attribution.utm_campaign,
        utm_content=payload.attribution.utm_content,
        utm_term=payload.attribution.utm_term,
        utm_id=payload.attribution.utm_id,
        fbclid=payload.attribution.fbclid,
        gclid=payload.attribution.gclid,
        msclkid=payload.attribution.msclkid,
        fbp_cookie=payload.attribution.fbp,
        fbc_cookie=payload.attribution.fbc,
        device_type=payload.device.device_type,
        user_agent=payload.device.user_agent,
        screen=payload.device.screen,
        viewport=payload.device.viewport,
        browser_language=payload.device.browser_language,
        platform=payload.device.platform,
        browser_timezone=payload.device.browser_timezone,
        time_on_widget_ms=payload.behavior.time_on_widget_ms,
        interaction_count=payload.behavior.interaction_count,
        steps_completed=payload.behavior.steps_completed,
        user_journey=payload.behavior.user_journey,
        bot_suspected=bot_suspected,
        raw_payload=payload.model_dump(mode="json"),
    )
    db.add(appt)
    try:
        db.commit()
    except IntegrityError:
        # Another request with the same event_id snuck in between our SELECT
        # and INSERT. Fetch and return the winner.
        db.rollback()
        winner = (
            db.query(Appointment)
            .filter(Appointment.event_id == payload.event_id)
            .first()
        )
        if winner is not None:
            return _appointment_to_response(db, winner)
        raise HTTPException(status_code=409, detail="duplicate submission")
    db.refresh(appt)

    # Visitor row upsert — best-effort, never block submission.
    if visitor_uuid is not None:
        _touch_visitor(
            db,
            visitor_uuid,
            attribution=payload.attribution.model_dump(),
            booked=True,
        )

    # Link a pre-booking Boutique Experience profile, if one is claimed.
    # Best-effort: a stale or already-linked id must not invalidate the
    # booking. Support can re-attach later if anything goes wrong here.
    if payload.boutique_experience_profile_id is not None:
        try:
            booking_service.link_profile_to_appointment(
                db,
                profile_id=payload.boutique_experience_profile_id,
                appointment_id=appt.id,
            )
            db.commit()
        except Exception:
            log.exception(
                "boutique-experience link failed appt_id=%s profile_id=%s",
                appt.id,
                payload.boutique_experience_profile_id,
            )
            db.rollback()

    # Auto-promote to a CRM lead so the appointment shows up on the pipeline
    # board immediately. Best-effort: a promotion failure must not invalidate
    # the already-persisted booking — staff can repair via the
    # POST /events from_appointment_id escape hatch if needed.
    try:
        event_service.promote_appointment_to_event(
            db, appointment_id=appt.id, event_type="quinceanera"
        )
        db.commit()
    except EventServiceError:
        log.exception("auto-promote failed appt_id=%s", appt.id)
        db.rollback()
    except Exception:
        log.exception("auto-promote crashed appt_id=%s", appt.id)
        db.rollback()

    # Notifications — best-effort. A failure here does not invalidate the
    # already-persisted booking; the worker will retry due-failed jobs.
    try:
        notification_service.enqueue_for_new_booking(db, appt)
        db.commit()
    except Exception:
        log.exception("failed to enqueue new-booking notifications appt_id=%s", appt.id)
        db.rollback()

    return _appointment_to_response(db, appt)


# ---------------------------------------------------------------------------
# Session events + abandon
# ---------------------------------------------------------------------------


def _touch_visitor(
    db: Session,
    visitor_id: UUID,
    *,
    attribution: dict | None = None,
    booked: bool = False,
) -> None:
    try:
        visitor = (
            db.query(AppointmentVisitor)
            .filter(AppointmentVisitor.visitor_id == visitor_id)
            .first()
        )
        now = datetime.now(timezone.utc)
        if visitor is None:
            visitor = AppointmentVisitor(
                visitor_id=visitor_id,
                first_seen_at=now,
                last_seen_at=now,
                first_touch_attribution=attribution or {},
                last_touch_attribution=attribution or {},
                session_count=1,
                booked_at=now if booked else None,
            )
            db.add(visitor)
        else:
            visitor.last_seen_at = now
            if attribution:
                visitor.last_touch_attribution = attribution
            if booked:
                visitor.booked_at = now
        db.commit()
    except Exception:  # pragma: no cover — visitor tracking is best-effort
        log.exception("visitor touch failed visitor_id=%s", visitor_id)
        db.rollback()


@router.post(
    "/events",
    response_model=AcknowledgedResponse,
    dependencies=[Depends(_booking_telemetry_ip_limit)],
)
def post_session_event(
    payload: SessionEventRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> AcknowledgedResponse:
    visitor_uuid = _parse_visitor_id(payload.visitor_id)
    ip_hash = booking_service.hash_ip(_client_ip(request))

    event = AppointmentSessionEvent(
        visitor_id=visitor_uuid,
        session_id=payload.session_id,
        event_id=payload.event_id,
        event_name=payload.event_name,
        step=payload.step,
        payload=payload.payload,
        page_url=payload.page_url,
        referrer_url=payload.referrer_url,
        user_agent=request.headers.get("user-agent"),
        ip_hash=ip_hash,
    )
    db.add(event)
    try:
        db.commit()
    except IntegrityError:
        # event_id collision (UNIQUE WHERE event_id IS NOT NULL) — already logged.
        db.rollback()
    return AcknowledgedResponse()


@router.post(
    "/abandon",
    response_model=AcknowledgedResponse,
    dependencies=[Depends(_booking_telemetry_ip_limit)],
)
def post_abandon(
    payload: AbandonRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> AcknowledgedResponse:
    visitor_uuid = _parse_visitor_id(payload.visitor_id)
    behavior = payload.behavior

    # Drop zero-engagement noise.
    if (
        not visitor_uuid
        and not payload.session_id
        and not behavior.interaction_count
        and not behavior.time_on_widget_ms
    ):
        return AcknowledgedResponse()

    enriched_payload = {
        "partial": payload.partial,
        "attribution": payload.attribution.model_dump(),
        "device": payload.device.model_dump(),
        "behavior": behavior.model_dump(),
    }

    event = AppointmentSessionEvent(
        visitor_id=visitor_uuid,
        session_id=payload.session_id,
        event_id=payload.event_id,
        event_name="abandoned",
        step=payload.step,
        payload=enriched_payload,
        page_url=payload.page_url,
        referrer_url=payload.referrer_url,
        user_agent=request.headers.get("user-agent"),
        ip_hash=booking_service.hash_ip(_client_ip(request)),
    )
    db.add(event)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return AcknowledgedResponse()


# ---------------------------------------------------------------------------
# Boutique Experience profile (sizing + style preferences)
# ---------------------------------------------------------------------------


_PROFILE_BLOCKED_STATUSES = ("cancelled", "rescheduled", "attended", "no_show")


@router.post(
    "/boutique-experience",
    response_model=BoutiqueExperienceCreatedResponse,
    status_code=201,
    dependencies=[Depends(_booking_profile_ip_limit)],
)
def create_boutique_experience_profile(
    payload: BoutiqueExperienceSubmission,
    db: Session = Depends(get_db),
) -> BoutiqueExperienceCreatedResponse:
    """Calculator-first path: create an unlinked profile before booking.

    The returned `profile_id` is then passed to `POST /api/booking/appointments`
    via `boutique_experience_profile_id` so the booking endpoint can link the
    profile to the new appointment.
    """
    profile = booking_service.create_pre_booking_profile(db, payload=payload)
    db.commit()
    db.refresh(profile)
    return BoutiqueExperienceCreatedResponse(profile_id=profile.id)


@router.post(
    "/boutique-experience/confirm",
    response_model=BoutiqueExperienceTokenResponse,
    dependencies=[Depends(_booking_confirm_ip_limit)],
)
def submit_boutique_experience_profile_by_confirmation(
    payload: BoutiqueExperienceConfirmRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> BoutiqueExperienceTokenResponse:
    """Confirmation-code path: attach profile answers to an existing booking.

    This covers customers who open the calculator from a fresh browser or
    device and do not have the tokenized email/success URL handy. The
    confirmation code must match the booking, and the email must match the
    appointment email before the profile is written.
    """
    # Per-email bucket: tighter than per-IP and always counts (even before
    # we check whether the appointment exists) so the 429 cannot be used
    # to enumerate registered emails. Five attempts per email per minute
    # is enough headroom for a real customer who mistypes a code once or
    # twice, and tight enough that a brute-force search over codes for
    # one known email gets shut down before D1's entropy boost ships.
    enforce_or_raise(
        bucket="booking_confirm_email",
        scoped=str(payload.email).strip().lower(),
        limit=5,
        window=60,
        request=request,
    )

    appt = _appointment_from_confirmation(db, payload.confirmation_code)
    if not _email_matches_appointment(str(payload.email), appt):
        raise HTTPException(status_code=404, detail="appointment not found")
    if appt.status in _PROFILE_BLOCKED_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"appointment is {appt.status} and cannot accept profile updates",
        )

    profile = booking_service.upsert_profile_for_appointment(
        db,
        appointment_id=appt.id,
        payload=payload.profile,
        # Existing DB constraint allows `manual_attach` for support/customer
        # code-confirmed attachment. Keep the API response specific below.
        source="manual_attach",
    )
    db.commit()
    db.refresh(profile)
    return BoutiqueExperienceTokenResponse(
        profile_id=profile.id,
        source="post_booking_confirmation",
        slot_start=appt.slot_start_at,
        timezone=appt.timezone,
        confirmation_code=booking_service.format_confirmation_code(appt.confirmation_code),
    )


@router.post(
    "/boutique-experience/{token}",
    response_model=BoutiqueExperienceTokenResponse,
    dependencies=[Depends(_booking_token_ip_limit)],
)
def submit_boutique_experience_profile_by_token(
    token: str,
    payload: BoutiqueExperienceSubmission,
    db: Session = Depends(get_db),
) -> BoutiqueExperienceTokenResponse:
    """Email-token path: upsert the profile for the appointment in the token.

    Customer never has to enter a confirmation code or phone number; the
    signed token identifies the appointment. A re-submit replaces the prior
    answers in place.
    """
    appt = _appointment_from_token(db, token, "enrichment")
    if appt.status in _PROFILE_BLOCKED_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"appointment is {appt.status} and cannot accept profile updates",
        )

    profile = booking_service.upsert_profile_for_appointment(
        db,
        appointment_id=appt.id,
        payload=payload,
        source="post_booking_email",
    )
    db.commit()
    db.refresh(profile)
    return BoutiqueExperienceTokenResponse(
        profile_id=profile.id,
        slot_start=appt.slot_start_at,
        timezone=appt.timezone,
        confirmation_code=booking_service.format_confirmation_code(appt.confirmation_code),
    )


# ---------------------------------------------------------------------------
# Reschedule
# ---------------------------------------------------------------------------


def _appointment_from_token(
    db: Session, token: str, expected_purpose
) -> Appointment:
    try:
        claims = verify_token(token, expected_purpose)
    except InvalidBookingToken:
        raise HTTPException(status_code=404, detail="link is invalid or expired")

    appt = db.query(Appointment).filter(Appointment.id == int(claims["sub"])).first()
    if appt is None:
        raise HTTPException(status_code=404, detail="appointment not found")

    # G1: explicit revocation check. A token minted before the appointment's
    # `tokens_invalidated_at` (bumped on cancel/reschedule of the original)
    # is rejected with the same generic 404 — so an emailed link stops
    # working the moment the customer cancels or reschedules.
    try:
        ensure_not_revoked(claims, appt)
    except InvalidBookingToken:
        raise HTTPException(status_code=404, detail="link is invalid or expired")

    return appt


def _appointment_from_confirmation(db: Session, confirmation_code: str) -> Appointment:
    # Canonicalise input the same way new codes are stored (D1): strip
    # every non-alphanumeric and uppercase. Legacy `BX-ABCDEF` rows were
    # backfilled to `BXABCDEF` in migration 064, so the same comparison
    # works for codes minted before and after D1.
    canonical = booking_service.normalize_confirmation_code(confirmation_code)
    appt = (
        db.query(Appointment)
        .filter(Appointment.confirmation_code == canonical)
        .first()
    )
    if appt is None:
        raise HTTPException(status_code=404, detail="appointment not found")
    return appt


def _email_matches_appointment(raw_email: str, appt: Appointment) -> bool:
    return (raw_email or "").strip().lower() == (appt.email or "").strip().lower()


def _to_summary(appt: Appointment) -> RescheduleSummary:
    return RescheduleSummary(
        confirmation_code=booking_service.format_confirmation_code(appt.confirmation_code),
        slot_start=appt.slot_start_at,
        slot_end=appt.slot_end_at,
        timezone=appt.timezone,
        status=appt.status,
        celebrant_first_name=appt.celebrant_first_name,
    )


@router.get("/reschedule/{token}", response_model=RescheduleSummary)
def get_reschedule(token: str, db: Session = Depends(get_db)) -> RescheduleSummary:
    appt = _appointment_from_token(db, token, "reschedule")
    if appt.status in ("cancelled", "attended", "no_show", "rescheduled"):
        raise HTTPException(
            status_code=409, detail=f"appointment is {appt.status} and cannot be rescheduled"
        )
    return _to_summary(appt)


@router.post(
    "/reschedule/{token}",
    response_model=AppointmentResponse,
    dependencies=[Depends(_booking_token_ip_limit)],
)
def post_reschedule(
    token: str,
    payload: RescheduleRequest,
    db: Session = Depends(get_db),
) -> AppointmentResponse:
    original = _appointment_from_token(db, token, "reschedule")
    if original.status in ("cancelled", "attended", "no_show", "rescheduled"):
        raise HTTPException(
            status_code=409, detail=f"appointment is {original.status} and cannot be rescheduled"
        )

    settings = booking_service.get_theme_settings(db)
    min_lead = int((settings.flow or {}).get("min_lead_time_minutes", 0))

    slot_start = payload.slot_start
    if slot_start.tzinfo is None:
        slot_start = slot_start.replace(tzinfo=booking_service.shop_tz())
    slot_start_utc = slot_start.astimezone(timezone.utc)

    ok, reason = booking_service.slot_is_bookable(
        db,
        slot_start=slot_start_utc,
        slot_duration_minutes=payload.slot_duration_minutes,
        min_lead_minutes=min_lead,
    )
    if not ok:
        raise HTTPException(status_code=409, detail=f"slot unavailable: {reason}")

    slot_end_utc = slot_start_utc + timedelta(minutes=payload.slot_duration_minutes)
    new_code = booking_service.generate_unique_confirmation_code(db)

    new_appt = Appointment(
        confirmation_code=new_code,
        slot_start_at=slot_start_utc,
        slot_end_at=slot_end_utc,
        slot_duration_minutes=payload.slot_duration_minutes,
        timezone=APP_TIMEZONE,
        celebrant_first_name=original.celebrant_first_name,
        celebrant_last_name=original.celebrant_last_name,
        parent_first_name=original.parent_first_name,
        parent_last_name=original.parent_last_name,
        event_date=original.event_date,
        party_size_bucket=original.party_size_bucket,
        phone=original.phone,
        phone_e164=original.phone_e164,
        email=original.email,
        customer_note=original.customer_note,
        contact_id=original.contact_id,
        # Carry the CRM linkage forward so the rescheduled visit shows up
        # under the same lead on the pipeline board.
        crm_event_id=original.crm_event_id,
        # Carry the stylist assignment forward so the booking stays on the
        # same column when the customer moves the slot. The staff.booking_
        # rescheduled email fires below; without this carry-forward the new
        # row would be unassigned and the stylist would silently lose the
        # booking on the next page load. A staff-initiated reassignment via
        # /api/sales/appointments/{id}/assignment can still move it later.
        assigned_user_id=original.assigned_user_id,
        status="confirmed",
        rescheduled_from_id=original.id,
        visitor_id=original.visitor_id,
        utm_source=original.utm_source,
        utm_medium=original.utm_medium,
        utm_campaign=original.utm_campaign,
        utm_content=original.utm_content,
        utm_term=original.utm_term,
        utm_id=original.utm_id,
        fbclid=original.fbclid,
        gclid=original.gclid,
        msclkid=original.msclkid,
        raw_payload={"rescheduled_from": original.confirmation_code},
    )
    db.add(new_appt)

    original.status = "rescheduled"
    original.updated_at = datetime.now(timezone.utc)
    # G1: invalidate every self-service token minted for the original
    # appointment. The new appointment's tokens are unaffected (different
    # row, different `tokens_invalidated_at`).
    revoke_appointment_tokens(original)

    db.commit()
    db.refresh(new_appt)

    try:
        notification_service.enqueue_for_reschedule(
            db, original_id=original.id, new_appt=new_appt
        )
        db.commit()
    except Exception:
        log.exception(
            "failed to enqueue reschedule notifications original=%s new=%s",
            original.id,
            new_appt.id,
        )
        db.rollback()

    # Staff-facing: same stylist, new slot. Skipped silently when the
    # appointment was unassigned (no one to notify). Customer-initiated
    # reschedule has no staff actor on record.
    try:
        from services.staff_booking_notifications import notify_booking_rescheduled

        notify_booking_rescheduled(
            db,
            new_appt,
            previous_slot_start_at=original.slot_start_at,
            actor_user_id=None,
        )
        db.commit()
    except Exception:
        log.exception(
            "failed to enqueue staff booking_rescheduled new=%s", new_appt.id
        )
        db.rollback()

    return _appointment_to_response(db, new_appt)


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


@router.post(
    "/cancel/{token}",
    response_model=RescheduleSummary,
    dependencies=[Depends(_booking_token_ip_limit)],
)
def post_cancel(
    token: str,
    payload: CancelRequest,
    db: Session = Depends(get_db),
) -> RescheduleSummary:
    appt = _appointment_from_token(db, token, "cancel")
    if appt.status in ("cancelled", "attended", "no_show", "rescheduled"):
        raise HTTPException(
            status_code=409, detail=f"appointment is already {appt.status}"
        )

    appt.status = "cancelled"
    appt.cancelled_at = datetime.now(timezone.utc)
    appt.cancellation_reason = payload.reason
    appt.updated_at = datetime.now(timezone.utc)
    # G1: invalidate every self-service token minted for this row.
    # The cancel-side status check already 409s a re-cancel attempt;
    # this closes the loop on the reschedule/enrichment surfaces.
    revoke_appointment_tokens(appt)
    db.commit()
    db.refresh(appt)

    # Mirror the cancellation onto the linked CRM event so the kanban reflects
    # board truth. Best-effort — appointment is the source record.
    if appt.crm_event_id is not None:
        try:
            reason = payload.reason or ""
            note = (
                f"Customer cancelled via token: {reason}".strip()
                if reason
                else "Customer cancelled via token"
            )
            event_service.change_event_status(
                db,
                event_id=appt.crm_event_id,
                new_status="cancelled",
                notes=note,
            )
            db.commit()
        except EventServiceError:
            log.exception(
                "mirror cancel to event failed appt_id=%s event_id=%s",
                appt.id,
                appt.crm_event_id,
            )
            db.rollback()
        except Exception:
            log.exception(
                "mirror cancel to event crashed appt_id=%s", appt.id
            )
            db.rollback()

    try:
        notification_service.enqueue_for_cancellation(db, appt)
        db.commit()
    except Exception:
        log.exception("failed to enqueue cancellation notifications appt_id=%s", appt.id)
        db.rollback()

    # Staff-facing: the assigned stylist's slot just opened up. Fires
    # via record_event so the email lands through the same bus the
    # sales assignment lifecycle uses. Skipped silently when the
    # appointment was never assigned to anyone.
    try:
        from services.staff_booking_notifications import notify_booking_cancelled

        notify_booking_cancelled(db, appt, actor_user_id=None)
        db.commit()
    except Exception:
        log.exception(
            "failed to enqueue staff booking_cancelled appt_id=%s", appt.id
        )
        db.rollback()

    return _to_summary(appt)
