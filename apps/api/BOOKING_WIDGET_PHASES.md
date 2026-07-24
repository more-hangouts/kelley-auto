# Bellas XV Booking Widget Phases

Implementation plan for the Bellas XV scheduling widget and admin booking system.

The core product goal is simple: convert a stranger on the marketing page into a confirmed appointment with as little friction as possible, while giving the shop enough operational context to prepare well.

## Guiding Principles

- Keep the public booking flow to three steps maximum.
- Ask only for data required to book or prepare the room.
- Move preference-heavy questions into a post-booking enrichment flow.
- Treat source attribution, funnel analytics, and raw payload storage as first-class product requirements.
- Keep external integrations non-blocking after the booking has been accepted locally.
- Make the admin experience useful for real shop operations: schedule control, follow-up, attendance, purchase tracking, and reporting.

## Proposed Defaults

These defaults can change before implementation, but the phase plan assumes them:

- Bellas XV owns the calendar in-app for v1.
- Availability is based on recurring rules, blackout dates, existing bookings, and slot capacity.
- Slot capacity defaults to `1`, but the schema supports multiple rooms or consultants later.
- Email confirmation ships in v1.
- SMS confirmation is designed as an integration seam, then wired when Twilio and 10DLC are ready.
- Customers receive a signed self-service reschedule and cancel link.
- The booking flow is minimal, followed by a post-booking enrichment survey.
- Server-side Meta CAPI and Google Ads Enhanced Conversions are the paid-ad conversion path.
- Plausible plus first-party Postgres events are the product/UX analytics path.

## Phase 0: Product Decisions And Configuration

### Scope

Lock the operational decisions that shape the schema, API contracts, and admin tools.

Decisions to confirm:

- Calendar ownership: in-app availability versus external calendar sync.
- Capacity model: one appointment at a time versus multiple simultaneous appointments.
- Confirmation channels: email-only, SMS-only, or both.
- Reschedule/cancel flow: self-service links versus call-the-shop.
- Data capture model: minimal booking plus enrichment versus all fields up front.

Configuration to add:

- `WIDGET_PUBLIC_BASE_URL`
- `PUBLIC_SITE_URL`
- `BOOKING_WIDGET_ALLOWED_ORIGINS`
- `RESCHEDULE_TOKEN_SECRET`
- `ATTRIBUTION_COOKIE_DOMAIN`
- `META_PIXEL_ID`
- `META_CAPI_TOKEN`
- `GOOGLE_ADS_CONVERSION_ID`
- `GOOGLE_ADS_CONVERSION_LABEL`
- `PLAUSIBLE_DOMAIN`
- `SMTP_*`
- `TWILIO_*` placeholders

### Success Looks Like

- The five product decisions are written down and agreed.
- `.env.example` documents every setting needed for local and production booking work.
- CORS/origin expectations are clear for the marketing site and widget script.
- The implementation path does not depend on a future calendar or SMS vendor decision.

## Phase 1: Database Schema

### Scope

Add persistent storage for appointments, availability, attribution, funnel analytics, visitor identity, widget theme settings, and post-booking enrichment.

Proposed tables:

- `appointments`
- `appointment_availability_rules`
- `appointment_blackouts`
- `appointment_session_events`
- `appointment_visitors`
- `appointment_enrichment_responses`
- `booking_widget_theme_settings`

The `appointments` table should include:

- Appointment slot fields: start, end, duration, timezone.
- Customer fields: quinceanera name, event date, appointment guest count, phone, email, optional note.
- Workflow fields: status, assigned admin, internal notes, cancel/reschedule metadata.
- Attribution fields: UTM values, click IDs, `_fbp`, `_fbc`, visitor ID, event ID, page URL, referrer.
- Device fields: user agent, device type, screen, viewport, browser language, platform, browser timezone.
- UX fields: time on widget, interaction count, steps completed, user journey, behavior score.
- Conversion-quality fields: attended timestamp, no-show status, purchase timestamp, purchase value.
- Integration fields: Meta and Google sync state.
- `raw_payload` JSONB for full production debugging.

Seed availability rules from the current public hours:

- Wednesday: 12:00 PM to 7:00 PM
- Thursday: 12:00 PM to 7:00 PM
- Friday: 12:00 PM to 5:00 PM
- Saturday: 11:00 AM to 5:00 PM
- Sunday: 12:00 PM to 5:00 PM
- Monday and Tuesday closed

### Success Looks Like

- Migrations run cleanly from an empty database.
- ORM models match the migrated schema.
- Default availability rules exist after migration or seed.
- The schema can represent capacity greater than `1` without another migration.
- Raw payload and analytics JSON fields are present for debugging.
- Indexes exist for common admin queries: appointment date, status, email, phone, visitor ID, and source fields.

## Phase 2: Public Booking API

### Scope

Add public endpoints for the customer widget.

Proposed endpoints:

```text
GET  /api/booking/theme
GET  /api/booking/availability?from=YYYY-MM-DD&to=YYYY-MM-DD
POST /api/booking/appointments
POST /api/booking/events
POST /api/booking/abandon
GET  /api/booking/reschedule/{token}
POST /api/booking/reschedule/{token}
POST /api/booking/cancel/{token}
```

Core behavior:

- Compute available slots from recurring rules, blackout dates, existing appointments, and capacity.
- Validate booking payloads with Pydantic contracts.
- Normalize phone and email.
- Use idempotency keys or event IDs to prevent duplicate appointments.
- Reject obvious bot submissions and mark suspicious weak-signal sessions.
- Persist complete attribution and raw payload data.
- Return a confirmation code and customer-safe appointment summary.
- Queue email, SMS, ad conversion, and internal notification work in the background.

### Success Looks Like

- A real appointment can be created through the API with only the six booking fields.
- Double submit does not create duplicate appointments.
- Availability no longer shows a full slot after capacity is reached.
- Blackout dates remove slots from public availability.
- Bot/honeypot submissions are rejected or marked suspicious.
- Background integration failures do not block the appointment response.
- Unit or integration tests cover availability, booking creation, idempotency, and cancellation.

## Phase 3: Customer Booking Widget

### Scope

Build a host-page-safe embeddable widget served as a standalone script.

Proposed file:

```text
widgets/bellas-booking-widget.js
```

Embed shape:

```html
<div id="bellas-booking-widget"></div>
<script src="https://shopbellasxv.com/widgets/bellas-booking-widget.js"></script>
<script>
  BellasBookingWidget.init({
    containerId: "bellas-booking-widget",
    apiBaseUrl: "https://shopbellasxv.com"
  });
</script>
```

Customer flow:

1. Pick a date and time.
2. Enter who the appointment is for.
3. Enter contact information.

Step 1 fields:

- Calendar date.
- Available appointment time.

Step 2 fields:

- Quinceanera's name.
- Event date, if known.
- Appointment guest count: `Just me`, `2-3 people`, `4+ people`.

Step 3 fields:

- Phone number.
- Email.
- Optional note.

Widget analytics:

- `widget_loaded`
- `date_selected`
- `slot_selected`
- `step_2_viewed`
- `step_2_submitted`
- `step_3_viewed`
- `submit_attempted`
- `submit_succeeded`
- `submit_failed`
- `abandoned`

Attribution capture:

- UTM parameters.
- `fbclid`, `gclid`, `msclkid`.
- `_fbp`, `_fbc`.
- First-party `visitor_id`.
- Session ID.
- Event ID.
- Page URL and referrer.

### Success Looks Like

- The widget embeds on a blank HTML page and inside `marketing/index.html`.
- The widget works on mobile and desktop.
- The widget visually matches the Bellas XV warm cream and rose-gold brand direction.
- Host page CSS does not break the widget.
- The booking flow can be completed in three steps.
- Abandon telemetry sends through `sendBeacon` without blocking navigation.
- Attribution persists across sessions and attaches to the final appointment.
- The success screen gives the customer a clear confirmation state.

## Phase 4: Admin Appointment Dashboard

### Scope

Add authenticated dashboard tools for shop staff.

Proposed pages:

- `Appointments.jsx`
- `AppointmentsCalendar.jsx`
- `BookingWidgetSettings.jsx`
- `BookingAnalytics.jsx`

Admin features:

- Appointment list with search, status filters, date range filters, and source filters.
- Appointment detail drawer with customer info, event info, notes, source attribution, device data, and raw payload.
- Calendar view with daily, weekly, and monthly appointment context.
- Status updates: confirmed, attended, no-show, cancelled, rescheduled.
- Internal notes.
- Manual purchase value entry.
- Availability rules editor.
- Blackout date manager.
- Widget theme and copy settings.
- Embed code preview.

### Success Looks Like

- Staff can see upcoming appointments without touching the database.
- Staff can filter by date, status, source, phone, or email.
- Staff can mark an appointment attended or no-show.
- Staff can record purchase value after a successful visit.
- Staff can add blackout dates from the dashboard.
- Staff can change recurring hours without editing code.
- The dashboard surfaces enough attribution context to answer which campaigns are producing bookings.

## Phase 5: Notifications And Appointment Lifecycle

### Scope

Add confirmation, reminder, reschedule, cancellation, and internal notification flows.

Email templates:

- Customer booking confirmation.
- Internal new appointment notification.
- Post-booking enrichment survey invitation.
- Appointment reminder.
- Reschedule confirmation.
- Cancellation confirmation.

SMS templates:

- Booking confirmation.
- Appointment reminder.
- Reschedule confirmation.
- Cancellation confirmation.

Lifecycle jobs:

- Send confirmation immediately after booking.
- Send enrichment survey about two minutes after booking.
- Send reminder roughly 24 hours before appointment.
- Alert staff for new bookings and cancellations.

### Success Looks Like

- Customer receives an email confirmation after booking.
- Shop receives an internal notification after booking.
- Email includes signed reschedule and cancel links.
- Reminder jobs do not send for cancelled appointments.
- SMS integration can be enabled without changing booking logic.
- Failed notification attempts are logged for admin visibility.

## Phase 6: Post-Booking Enrichment

### Scope

Build the separate preference survey that runs after the appointment is already booked.

Survey fields:

- Dress style preferences through a visual picker.
- Color preferences through visual swatches.
- Budget range with careful, non-judgmental copy.
- Theme or colors of the quince.
- Court size.
- Optional inspiration photo uploads.

Proposed endpoints:

```text
GET  /api/booking/enrichment/{token}
POST /api/booking/enrichment/{token}
```

### Success Looks Like

- The survey can only be opened with a valid signed token.
- The customer can complete the survey after booking without re-entering contact information.
- Survey responses attach to the correct appointment.
- Admin appointment detail shows enrichment answers clearly.
- Incomplete enrichment does not affect the original booking.
- Uploaded inspiration photos, if included, are linked to the appointment record.

## Phase 7: Attribution, Ad Events, And Analytics

### Scope

Separate ad optimization from product analytics.

Ad optimization:

- Send booking conversion events server-side to Meta CAPI.
- Send booking conversion events server-side to Google Ads Enhanced Conversions.
- Include hashed email and phone where allowed.
- Include `_fbp`, `_fbc`, `fbclid`, `gclid`, and event ID where available.
- Push downstream quality signals when appointments become attended or purchased.

Product analytics:

- Store funnel events in Postgres.
- Add Plausible for page-level analytics.
- Track time on step, drop-off point, field edits, validation errors, and re-engagement.
- Preserve visitor history across sessions with a one-year first-party visitor ID.

### Success Looks Like

- A completed booking creates a local appointment and queues ad conversion events.
- Meta and Google receive server-side events without relying only on browser pixels.
- Admin can see first touch, last touch, and time-to-booking.
- Admin can compare booking rate by campaign/source.
- Admin can see funnel drop-off by step.
- Marking an appointment attended or purchased can queue a value/quality event back to ad platforms.
- UX analytics never send PII to Plausible.

## Phase 8: Reliability, Security, And Abuse Controls

### Scope

Harden the booking surface before production traffic.

Controls:

- IP-based rate limiting for public endpoints.
- Honeypot fields.
- Timing and interaction heuristics for suspicious sessions.
- Signed tokens for reschedule, cancel, and enrichment links.
- Server-side validation for all date and slot claims.
- CORS allowlist for trusted origins.
- Idempotency for booking submission.
- Structured logs for integration failures.
- Admin-only access for appointment and settings endpoints.

### Success Looks Like

- A customer cannot book a slot that is already full by manipulating the frontend.
- Public endpoints reject malformed or suspicious payloads safely.
- Tokens cannot be guessed or reused outside their intended purpose.
- Admin endpoints require authentication.
- Integration failures are visible but do not break customer booking.
- Logs contain enough context to debug production issues without exposing secrets.

## Phase 9: QA, Launch, And Monitoring

### Scope

Validate the full system in local, staging, and production.

QA checklist:

- Mobile booking.
- Desktop booking.
- Slow network behavior.
- Double-click submit behavior.
- Refresh after selecting a slot.
- Abandon before submit.
- Booking from a URL with UTMs.
- Booking from a URL with `fbclid` or `gclid`.
- Full slot behavior.
- Blackout date behavior.
- Reschedule.
- Cancel.
- Admin mark attended.
- Admin mark no-show.
- Admin purchase value entry.
- Enrichment survey completion.
- Email delivery.
- Integration failure handling.

### Success Looks Like

- The widget is live on the marketing booking section.
- A real customer can book without staff assistance.
- Staff can manage the appointment from the dashboard.
- Confirmation emails arrive reliably.
- Abandon and funnel analytics are visible.
- Paid-ad attribution fields are stored on booking records.
- The team has a rollback path for widget script changes.
- Production health checks and logs make failures visible quickly.

## Phase 10: Iteration And Optimization

### Scope

Use real traffic and shop outcomes to tune the system.

Optimization loops:

- Improve slot availability presentation if date/time selection drops off.
- Adjust copy if contact step hesitation is high.
- Tune reminder timing based on no-show rate.
- Tune enrichment survey timing and copy based on completion rate.
- Compare campaigns by booking, attendance, and purchase value.
- Adjust ad platform conversion values based on downstream revenue.

### Success Looks Like

- Admin can answer how many visitors saw, started, and completed the widget.
- Admin can answer where users abandon.
- Admin can answer which campaigns generate appointments.
- Admin can answer which campaigns generate attended appointments and purchases.
- The shop can prepare better for appointments because enrichment data is visible before the visit.
- Ad optimization shifts from cheap bookings to high-quality bookings.

## First Build Slice Recommendation

The first useful build slice should be:

1. Phase 1 database schema.
2. Phase 2 public availability and booking API.
3. Phase 3 basic customer widget.
4. A minimal Phase 4 appointment list in admin.

This gets Bellas XV from marketing-page visitor to confirmed appointment, with enough admin visibility to operate manually while analytics, enrichment, SMS, and ad optimization mature behind it.
