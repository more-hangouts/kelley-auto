# Booking widget + admin appointments

How a customer books a consult and what staff see afterward.

## Files

| File | Purpose |
|---|---|
| [widgets/booking-widget.js](../widgets/) | Public widget (vanilla JS, embedded on marketing site) |
| [api/routers/booking.py](../api/routers/booking.py) | Public API: availability, submit, reschedule, cancel, abandon |
| [api/routers/admin_booking.py](../api/routers/admin_booking.py) | Admin: list, detail, patch (status/notes/purchase) |
| [api/routers/admin_booking_settings.py](../api/routers/admin_booking_settings.py) | Theme/copy/flow + availability rules + blackouts |
| [services/booking_service.py](../services/booking_service.py) | Slot algorithm, normalization, code generation |
| [services/booking_contracts.py](../services/booking_contracts.py) | Pydantic shapes shared with the widget |
| [services/booking_tokens.py](../services/booking_tokens.py) | Signed reschedule/cancel/enrichment tokens |
| [services/contact_service.py](../services/contact_service.py) | Find-or-create contact on submission |
| [services/notification_service.py](../services/notification_service.py) | Confirmation + reminder emails/SMS |
| [workers/notifications.py](../workers/) | In-process worker draining `notification_jobs` |
| Migrations 005–013 | Appointments, availability, blackouts, visitors, session events, enrichment, theme, notifications |

## Public submission flow

```
1. Widget GET /api/booking/availability?from=...&to=...
     -> services/booking_service.compute_availability
     -> reads availability rules + blackouts + existing live bookings
     -> returns days[].slots[] in shop tz

2. Widget POST /api/booking/appointments
     body: { slot_start, duration, name, phone, email, ... attribution, ... behavior }
     -> services/booking_service.slot_is_bookable (re-validate at write time)
     -> services/contact_service.find_or_create_contact (phone-first identity)
     -> INSERT appointments + appointments.contact_id set
     -> services/notification_service.enqueue_for_new_booking
     -> 201 with confirmation_code, signed reschedule_url + cancel_url

3. Worker drains notification_jobs (workers/notifications.py)
     -> sends confirmation email/SMS via transports
     -> updates job status; retries on transient failure

4. Optional: enrichment survey
     - separate signed token in confirmation email
     - widget POSTs back to /api/booking/enrichment/{token}
     - INSERT appointment_enrichment_responses
     - dress_styles, colors, budget_range, theme, court_size, photos
```

## Slot algorithm

`services/booking_service.compute_availability`:

- Walks each day in range
- Loads `appointment_availability_rules` rows for that weekday
- Subtracts `appointment_blackouts` overlapping the day
- Subtracts live bookings (status in `pending`, `confirmed`)
- Generates contiguous slots respecting `slot_duration_minutes` and `capacity`

Min-lead time comes from the `flow.min_lead_time_minutes` field of
`booking_widget_theme_settings.flow`. Default 0; current production has
something like 120 minutes to keep walk-ins from booking in 5 minutes.

## Idempotency

Each widget submission carries a client-generated `event_id` (UUID). The
column `appointments.event_id` has a UNIQUE constraint. A retry with the same
`event_id` returns the existing appointment (200) instead of creating a
duplicate (201). Distinct from `appointments.crm_event_id` (FK to events).

> **Naming caveat:** `event_id` was named before the CRM existed; it's the
> analytics dedup key, not a foreign key. Don't confuse with `crm_event_id`.

## Reschedule + cancel

Both flows use signed tokens minted at submission time:

```
GET /api/booking/reschedule/{token} -> AppointmentSummary
POST /api/booking/reschedule/{token} -> creates a new appointment, marks old as 'rescheduled'
GET /api/booking/cancel/{token} -> AppointmentSummary
POST /api/booking/cancel/{token} -> sets status='cancelled', cancelled_at, cancellation_reason
```

Tokens are HMAC-signed by `services/booking_tokens` with a separate secret per
purpose ("reschedule", "cancel", "enrichment"). Wrong-purpose tokens are
rejected with 404.

The new reschedule appointment **inherits `contact_id` from the original** —
no second find-or-create needed.

## Bot defenses

Light, in-app:
- `company_website` honeypot field — non-empty rejects the submission (400).
- Behavior heuristics in `booking_service.looks_like_bot`: time on widget,
  interaction count, steps completed, user agent shape. Sets
  `appointments.bot_suspected=true`; does NOT block.

Real WAF / rate limiting lives in nginx. See [INFRASTRUCTURE.md](../INFRASTRUCTURE.md).

## Attribution + behavior

The widget submission carries:
- UTMs (utm_source/medium/campaign/content/term/id)
- Click IDs (fbclid, gclid, msclkid)
- Cookies (`_fbp`, `_fbc`)
- Visitor + session UUIDs
- Device shape (type, screen, viewport, browser tz, language)
- Behavior (time on widget, interactions, steps completed, journey log)

All persisted on `appointments` for ad-platform value pushback (Meta CAPI,
Google Enhanced Conversions). The `meta_capi_synced_at` and
`google_enhanced_synced_at` columns are stamped when the conversion has been
sent upstream.

## Admin surface

`/api/admin/booking/appointments` — list with filters (status, date range,
search by name/email/phone/code, source/utm filter).

`/api/admin/booking/appointments/{id}` — full detail including:
- enrichment payload
- raw widget payload (for debugging)
- attribution + behavior + device fields
- **CRM linkage**: `contact_id`, `contact_display_name`, `crm_event_id`,
  `crm_event_name`, `crm_event_status`, `can_promote_to_event`

`PATCH /api/admin/booking/appointments/{id}` accepts:
- `status` (with side-effect timestamps: `attended_at`, `no_show_at`, `cancelled_at`)
- `internal_notes`
- `purchase_value_cents` (sets `purchase_at` when not null)

Cancellation from the admin side also enqueues a customer notification.

## CRM linkage on detail

The detail response exposes three states for the "Promote to Event" UI:

| Backend signal | UI state |
|---|---|
| `contact_id == null` | Disabled: "Cannot promote — no contact linked" |
| `can_promote_to_event == true` | Active button: "Promote to Event" |
| `crm_event_id != null` | "Linked to Event #N" + status pill |

Promotion happens from the appointment detail drawer, then routes the user
mentally onward to the kanban. See [CRM.md](CRM.md#promotion-appointment---event).

## Smoke tests

- [tests/test_booking_smoke.py](../tests/test_booking_smoke.py) — public widget end-to-end
- [tests/test_admin_booking_smoke.py](../tests/test_admin_booking_smoke.py) — admin list/detail/patch
- [tests/test_admin_booking_settings_smoke.py](../tests/test_admin_booking_settings_smoke.py) — settings + availability rules
- [tests/test_notifications_smoke.py](../tests/test_notifications_smoke.py) — notification enqueue/drain
- [tests/test_boutique_experience_smoke.py](../tests/test_boutique_experience_smoke.py) — calculator-first / token-flow profile, board + event detail surface

Run them as scripts: `venv/bin/python tests/test_booking_smoke.py`.
