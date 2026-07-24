# Porting Guide — Analytics, Twilio/SMS & Omnichannel Communications

> **Archived / historical — not authoritative.** Retained for context; may describe old paths, hostnames, or a pre-monorepo layout. Current docs: [README](../../README.md) · [ARCHITECTURE](../ARCHITECTURE.md) · [OPERATIONS](../OPERATIONS.md) · [CLAUDE](../../CLAUDE.md). See [archive index](README.md).


**Purpose:** reimplement the Kelley Autoplex analytics + communications stack in another
project running the same CRM/public-site combo (FastAPI + SQLAlchemy + Postgres backend,
Next.js App Router public site, Vite/MUI admin SPA).

Every file path below refers to THIS repo (`/opt/kelley`) so you can lift code directly.
Migration numbers are Kelley's — renumber for the target project, but keep the
**dependency order** in §9.

Deeper design docs worth copying alongside this guide:

- `STORE_FRONT_ANALYTICS_AND_CAPI_PLAN.md` (repo root) — analytics/CAPI privacy invariants
- `backend/docs/CRM_OMNICHANNEL_INBOX_PLAN.md` — full omnichannel inbox design (540 lines)

---

## 0. What's actually built vs. still planned

Don't port vaporware. Status as of 2026-07-11:

| Piece | Status |
|---|---|
| Storefront analytics (visitors/sessions/events + lead attribution) | **SHIPPED** (migration 090) |
| Admin per-lead journey panel (`GET /events/{id}/journey`) | **SHIPPED** |
| Aggregate analytics dashboards (most-viewed, conversion rates) | PLAN only (Phase 5) |
| Meta Pixel (browser) + CAPI sender (server, queued) | **SHIPPED**, delivery flag-gated off |
| Twilio **inbound** SMS webhook + signature verification | **SHIPPED** |
| Twilio **outbound** SMS | PLAN — `NoopSmsTransport` stub only, hard-gated |
| Twilio status callbacks (`/webhooks/twilio/status`) | PLAN |
| A2P consent capture (checkbox on 3 public forms + `sms_consent_at`) | **SHIPPED** (migration 095) |
| STOP/START keyword opt-out handling (inbound) | **SHIPPED** |
| Quiet-hours guard (9pm–8am TCPA block) | PLAN |
| Omnichannel inbox core (conversations/messages/reads) | **SHIPPED** (migration 094) |
| Meta (FB/IG) **inbound** webhook | **SHIPPED** |
| Meta outbound send (`meta_messaging.py`) | PLAN — module doesn't exist |
| MMS/media self-hosting (Twilio URLs expire) | PLAN — temp URLs stored as-is |
| Notification subscriber system ("who gets what") | **SHIPPED** (migration 093) |
| Notification job queue + async email worker | **SHIPPED** |
| Email transports (Gmail API OAuth / SMTP / Null) + MJML pipeline | **SHIPPED** |
| Sales-portal inbox UI, canned replies | PLAN |

---

## 1. Storefront analytics (first-party, Sprint 1)

### Architecture

```
Browser (Next.js public site)
  │  first-party cookies: ka_vid (visitor, 1yr), ka_sid (session, 30-min sliding)
  │  POST /api/public/track   (fire-and-forget, keepalive:true)
  ▼
FastAPI  public_site.py :: track_event        ← rate-limited 120/min/IP, best-effort
  ▼
storefront_analytics_service.record_event
  ▼
storefront_visitors ── storefront_sessions ── storefront_events
                                                    │
Lead submit ─────────► attach_lead_attribution ─────┘
                       lead_attribution (1:1 with CRM deal)
                                ▼
                  Admin SPA "Lead Journey" panel (read-only)
```

### Files to lift

| Concern | File |
|---|---|
| Browser client (cookies, UTM, tracking context) | `frontend/src/lib/analytics.ts` |
| `page_view` on every route change | `frontend/src/app/components/PageViewTracker.tsx` (mounted once in `layout.tsx`) |
| `vehicle_view` on detail pages | `frontend/src/app/inventory/[id]/VehicleViewTracker.tsx` |
| Form funnel events + submit context | `InquiryForm.tsx`, `LoanApplicationForm.tsx` |
| Ingestion endpoint + schemas | `backend/api/routers/public_site.py` (`TrackEventRequest`, `track_event`) |
| Write/read service | `backend/services/storefront_analytics_service.py` |
| Lead join | `backend/services/public_lead_service.py` (`submit_public_lead`, `attach_lead_attribution` calls) |
| Migration (5 tables, raw SQL) | `backend/database/migrations/090_storefront_analytics.py` |
| Models | `backend/database/models.py` L432–542 |
| Admin journey endpoint | `backend/api/routers/events.py` L523–536 |
| Admin journey UI | `backend/frontend/src/pages/event/tabs/LeadJourneyPanel.jsx` |

### Event taxonomy — two lists that MUST stay in sync

- TS union `StorefrontEventName` in `analytics.ts`
- backend `ALLOWED_EVENTS` frozenset in `storefront_analytics_service.py`

`page_view | vehicle_view | lead_form_opened | lead_form_started | lead_submitted`

Unknown event names are **silently dropped** server-side (anti-pollution). That means a
typo in the frontend loses data with zero errors — grep both lists whenever you add an event.

### Design rules that made this robust (keep them)

1. **Everything is best-effort.** `track()` never throws into UI; `track_event` swallows all
   exceptions and returns `{ok:true}`; `attach_lead_attribution` is SAVEPOINT-wrapped so it
   can **never fail a customer's lead**.
2. **Router owns the transaction.** `record_event` / `attach_lead_attribution` /
   `enqueue_lead_conversion` do NOT commit — the queue row and the deal commit atomically.
3. **Dedup by `event_id`** — partial unique index `WHERE event_id IS NOT NULL`
   (page_views carry no id). Duplicate insert rolls back only the SAVEPOINT, keeps the
   visitor/session touch.
4. **Permissive schema:** `TrackEventRequest` uses `ConfigDict(extra="ignore")` so a stale
   cached bundle never 422s a real visitor.
5. **Privacy by shape:** no raw IP anywhere (only `hash_ip()`), no DOB/DL/SSN/address in
   analytics tables, referrer captured only when external.
6. **Model gotcha:** `metadata` is reserved on SQLAlchemy declarative Base — the column is
   mapped as `StorefrontEvent.event_metadata → column "metadata"`.
7. Session upsert requires a visitor row (FK NOT NULL); no visitor → event stored unattached
   rather than erroring.

---

## 2. Meta Pixel + Conversions API (Sprint 2)

### The one idea that matters: shared `event_id` dedup

One UUID, generated **once per conversion** by `getTrackingContext()` in the browser, flows
to three places:

1. Browser Pixel: `fbq('track', 'Lead', params, { eventID })`
2. First-party `lead_submitted` storefront event
3. Server CAPI row (`ad_conversion_events.event_id`)

Meta dedups the browser event and the server event by that id. **Break the chain anywhere
and every conversion double-counts in Ads Manager.**

### Files to lift

| Concern | File |
|---|---|
| Pixel loader + SPA PageView handling | `frontend/src/app/components/MetaPixel.tsx` |
| `fbqTrack` helper + vehicle params | `frontend/src/lib/metaPixel.ts` |
| CAPI queue + sender (whole thing) | `backend/services/meta_capi_service.py` |
| Worker sweep (5-min retry tick) | `backend/workers/schedule_monitor.py` `_tick()` |
| Queue table | migration 090 (`ad_conversion_events`) |

### Server-side flow

```
lead txn:  enqueue_lead_conversion(...)   ← same txn as the deal, no commit
             hashes PII → ad_conversion_events row (status=pending)
commit
BackgroundTask: flush_queue()             ← fast path, own SessionLocal()
every 5 min:    tick()                    ← retry sweep (safety net)
send_pending(): FOR UPDATE SKIP LOCKED, ≤5 attempts, >7 days old → 'suppressed'
POST graph.facebook.com/{v21.0}/{PIXEL_ID}/events?access_token=...
```

### PII normalization for `user_data` (Meta-required, exact rules)

```python
_hash_email: strip → lower → sha256          (must contain "@")
_hash_phone: digits only; 10 digits → prepend "1"; require ≥11 → sha256
_hash_name : strip → lower → sha256          (name split on first space → fn/ln)
```

`client_ip_address` + `client_user_agent` are the ONLY raw values (Meta requires them
unhashed for web events); they're set by the **router** from the request, never trusted
from the client payload. `custom_data` carries commerce context only
(`content_type="vehicle"`, `content_ids`, `content_name`, `value` in dollars).

**Never** forward message text, DOB, DL, or address to CAPI — the enqueue call takes only
name/email/phone + ad cookies (`_fbp`/`_fbc`) by design.

### Gating & test mode

- Delivery requires ALL THREE: `META_CAPI_ENABLED=true` + `META_PIXEL_ID` + `META_CAPI_TOKEN`.
  Until then rows accumulate as `pending`; flipping the flag back-delivers the backlog
  (minus anything past the 7-day suppression window). Nice property — enqueue from day 1,
  enable later.
- `META_CAPI_TEST_EVENT_CODE` → payload gets `test_event_code` → visible in Events Manager
  "Test Events" before going live.
- Browser side gates on `NEXT_PUBLIC_META_PIXEL_ID` (renders null when unset).
- `MetaPixel.tsx`: init snippet fires the first PageView; the `usePathname` effect skips
  first render (`first.current` ref) — otherwise SPA nav double-counts PageView.

### Not yet wired (if you want them in the new project)

`Contact` / `Schedule` / `SubmitApplication` CAPI events (only `Lead`/`ViewContent`/
`PageView` exist); consent/suppression UI; no admin surface over `ad_conversion_events`;
Turnstile token accepted but not verified server-side.

---

## 3. Twilio SMS + A2P 10DLC compliance

### Posture (deliberate, copy it)

**Inbound live, outbound hard-gated OFF until the A2P campaign clears.** The transport
interface exists so lead/booking code can enqueue SMS today against a no-op:

- `backend/services/sms_transport.py` — `SmsTransport` Protocol, `NoopSmsTransport`,
  `get_sms_transport()` (returns Noop even with creds set — real Twilio send lands here).
- `inbox_service.send_reply()` raises `503 sms_sending_disabled` when
  `SMS_SENDING_ENABLED=false`, else `501 sms_sending_not_implemented`. Endpoint exists,
  cannot send early.

### Inbound webhook — `backend/api/routers/webhooks_twilio.py`

`POST /api/webhooks/twilio/sms` (public; the signature IS the auth). Order of operations:

1. **Verify `X-Twilio-Signature`** — `services/twilio_signature.py`, stdlib HMAC-SHA1
   (`base64(hmac_sha1(auth_token, url + sorted(k+v)))`), `hmac.compare_digest`.
   ⚠️ **THE trap:** Twilio signs the **public** URL (`https://api.example.com/...`) but
   behind Caddy/nginx the app sees `http://127.0.0.1:8000/...`. Build the verification URL
   from `PUBLIC_API_BASE_URL + request.url.path`, never from `request.url`. Works in dev,
   fails 100% in prod otherwise. Gate: `INBOUND_SMS_REQUIRE_SIGNATURE` (default true);
   503 if `TWILIO_AUTH_TOKEN` unset, 403 on bad sig.
2. **Raw-store first** — `webhook_ingest.record_webhook_event(source="twilio",
   external_id=MessageSid)`. `IntegrityError` on duplicate `(source, external_id)` →
   short-circuit empty 200 (Twilio retries are idempotent). Headers pass through an
   **allowlist** redactor (denylist would leak new sensitive headers).
3. Thread into inbox: `inbox_service.record_inbound_sms(...)` (second dedup layer on
   `(provider, provider_message_id)`), then `notify_inbound(...)`.
4. Return empty TwiML `<Response></Response>`.

Rate limit: 60/min per sender number (Redis bucket). MMS media extracted from
`NumMedia`/`MediaUrl{i}` but stored as Twilio's **expiring** URLs — self-hosting is a
known TODO; do it early in the new project.

### STOP/START — `backend/services/inbox_service.py`

```python
STOP_KEYWORDS  = {"STOP","STOPALL","UNSUBSCRIBE","CANCEL","END","QUIT"}   # CTIA
START_KEYWORDS = {"START","YES","UNSTOP"}
```

Exact case-insensitive match on inbound body → `_apply_opt_out(source="sms_keyword")`
stamps `contacts.sms_opted_out_at/_source`; START clears it. Twilio also blocks STOP at
carrier level; we mirror for our own records. HELP auto-reply is not implemented (HELP is
advertised in form copy + Terms only).

### A2P consent — why we got rejected, what fixed it

The campaign was rejected for **consent-as-a-condition-of-service**. The fix (this is the
compliance playbook for the new project):

1. **Checkbox is optional, never pre-checked, never required to submit.** All three public
   forms (`ContactForm.tsx`, `InquiryForm.tsx`, `LoanApplicationForm.tsx`) carry the same
   guard comment: *"consent may not be a condition of service."* Form submits fine
   unchecked; API default is `sms_consent: bool = False` and the client sends an explicit
   `false` (absent ≠ no).
2. **Consent must be RECORDED, not just displayed** — migration 095 adds
   `contacts.sms_consent_at TIMESTAMPTZ` + `sms_consent_source TEXT`, stamped server-side
   in `submit_public_lead`:
   ```python
   if lead.sms_consent and contact.sms_consent_at is None:
       contact.sms_consent_at = datetime.now(timezone.utc)
       contact.sms_consent_source = f"web_form:{lead.source_page or 'unknown'}"[:200]
   ```
   First consent wins (the timestamp is the legal record). A later form submit does NOT
   clear a prior STOP — opt-out stands until the customer texts START.
3. **Compliant opt-in copy** (lift verbatim, swap brand): *"Optional: By checking this box,
   I agree to receive calls and text messages from {Brand} about my inquiry at the phone
   number provided, including via automated technology. Consent is not a condition of any
   purchase or service — you may submit this form without checking this box. Msg frequency
   varies. Msg & data rates may apply. Reply STOP to opt out, HELP for help. See our
   Privacy Policy and Terms."*
4. **Live Privacy + Terms pages with SMS sections** are a hard A2P gate. Must include the
   CTIA-critical line: *"Text-messaging originator opt-in data and consent are not shared
   with third parties."* See `frontend/src/app/privacy-policy/page.tsx` and
   `terms-and-conditions/page.tsx`.
5. **Three distinct consent column pairs — never overload one:**
   - `marketing_consent_at` — email marketing
   - `sms_consent_at/_source` — SMS express written consent (095)
   - `sms_opted_out_at/_source` — SMS revocation (094)
6. **Never text imported/legacy contacts** without recorded consent. One-to-one lead
   replies only in v1; bulk campaigns need their own A2P campaign + marketing-consent flow.

### Outbound send gate (implement before flipping the flag)

Validation chain per the plan: enabled flag → valid E.164 →
`sms_consent_at IS NOT NULL AND sms_opted_out_at IS NULL` → quiet hours
(block 9:00 PM–8:00 AM America/Chicago, staff confirm-to-send override `409 quiet_hours`,
automated sends hard-blocked) → body caps/rate limits → snapshot the consent copy into
`conversation_messages.consent_snapshot` per send.

**Launch gates before `SMS_SENDING_ENABLED=true`:** A2P brand+campaign approved and number
attached to the Messaging Service; Privacy/Terms/SMS-terms live; signature validation
passing against the public URL **in prod**; live STOP/START round-trip tested.

Prefer `TWILIO_MESSAGING_SERVICE_SID` over `TWILIO_FROM_NUMBER` once A2P is attached
(the campaign binds to the messaging service). Status callbacks
(`POST /webhooks/twilio/status`) arrive out of order — enforce **monotonic** status
transitions on `(provider, provider_message_id)`; never let a late `sent` downgrade a
`delivered`.

### Phone normalization

`booking_service.normalize_phone_e164()`: strip non-digits; 10 → `+1{d}`; 11 starting
with 1 → `+{d}`; explicit `+` with 8–15 digits passes through; else `None`. Reused by
lead intake (422 if neither phone nor email normalizes) and inbound matching. Inbound
contact matching is **exact `phone_e164` match, newest non-deleted wins — do NOT mint new
contacts from unknown inbound numbers** (avoids junk against imported rosters).

---

## 4. Omnichannel inbox core

### Data model (migration 094 — actual shipped names, NOT the plan's `crm_*` names)

- **`conversations`** — unique `(provider, channel, external_id)`.
  `channel ∈ sms|facebook|instagram`, `provider ∈ twilio|meta`, `external_id` holds
  E.164/PSID/IGSID directly (the plan's separate `contact_channel_identities` table was
  collapsed away — simpler, worked fine). `status ∈ open|pending|resolved` (inbound
  reopens resolved), `assigned_user_id`, `last_message_at/last_inbound_at/last_outbound_at`,
  `last_inbound_preview`, JSONB `metadata`, nullable `contact_id`/`event_id` links to CRM.
- **`conversation_messages`** — `direction`, channel-neutral `sender_ref`/`recipient_ref`,
  `body`, `media` JSONB, `status ∈ received|queued|sent|delivered|read|failed`,
  partial-unique `(provider, provider_message_id)` (dedups provider retries AND echo
  events), `provider_error_code/_message`, `consent_snapshot` JSONB, `is_echo`,
  `sent_at/delivered_at/failed_at`. Status monotonicity enforced in the service layer.
- **`conversation_reads`** — `(conversation_id, user_id) → last_read_at`. Unread is
  **derived**, no counter to corrupt.

### Service + API

- `backend/services/inbox_service.py` (626 lines) — `upsert_conversation` (race-safe
  savepoint upsert on the unique key — two simultaneous webhooks for a new number work),
  `record_inbound_sms` / `record_inbound_meta`, contact matching, STOP/START,
  `notify_inbound`, `mark_read`, list/detail, `send_reply` (gated), `unread_count_for_user`.
- `backend/api/routers/inbox.py` — `GET /unread-count`, `GET/PATCH /conversations`,
  `GET /conversations/{id}`, `POST /conversations/{id}/messages`.
- `backend/api/routers/webhooks_meta.py` — `GET` hub.challenge verify + `POST` ingest with
  `X-Hub-Signature-256` verification (`META_APP_SECRET`), same raw-store-first pattern.

### Meta channel notes

Meta App Review is a **long-lead clock** (like A2P) — start it before you need it.
Request the `human_agent` tag for the 7-day reply window. `META_PAGE_ACCESS_TOKEN` lives
in env with a comment to move it to encrypted `integration_tokens` once a long-lived
System-User token exists. Outbound Meta send is gated by `META_MESSAGING_ENABLED` and
not yet implemented.

---

## 5. Notification routing — "who gets what"

Four-layer recipient resolution in `backend/services/notification_routing.py`
(`record_event(kind, subject_kind, subject_id, payload)` → `recipients_for()`):

1. **Intrinsic targeting** (`INTRINSIC_TARGETING[kind]`) — always wins, not opt-out-able.
   e.g. `inbox.message_received` → conversation assignee, else linked-deal owner, else
   falls through to admins+subscribers.
2. **Role defaults** (`ROLE_DEFAULTS[role][kind]`).
3. **Per-user overrides** (`notification_preferences`).
4. **Subscriber registry** (migration 093) — recipients **without CRM logins**
   (`notification_subscribers` + `notification_subscriptions`, PK
   `(subscriber_id, kind, channel)`). CHECK constraint guarantees an external row has a
   deliverable email even against raw DB inserts; a user-linked row inherits the user's
   email and is dropped if the user goes inactive.

Deduped by lowercased email so role-default + subscription can't double-send. Each kind
has a `TIMING_MODE` (`real_time` / `digest` / `real_time_and_digest` / `direct`).
Fan-out is skipped with a warning if no email renderer exists for the kind — add the
renderer BEFORE the event fires or notifications silently don't go out.

**Delivery queue:** `notification_jobs` (channel CHECK `email|sms` — sms channel reserved
for when the transport goes live), claimed with `FOR UPDATE SKIP LOCKED`, ≤5 attempts,
rendered **at send time against current state**, driven by `workers/notifications.py`
poll loop. `run_once(db)` exists for tests.

Admin CRUD: `api/routers/admin_notification_subscribers.py`
(`/api/admin/notification-subscribers`).

**Golden rule everywhere:** a notification failure must never fail the webhook/lead/booking
that triggered it. Every notify call is best-effort with an audit row
(`lead.notification_sent/_failed`, `lead.confirmation_sent/_failed`).

---

## 6. Email infrastructure

### Transport selection — `backend/services/email_transport.py`

`active_email_transport_kind()` → `"gmail_api" | "smtp" | "null"`:

- **Gmail API wins** if all four present: `GMAIL_OAUTH_CLIENT_ID`, `GMAIL_OAUTH_CLIENT_SECRET`,
  `GMAIL_OAUTH_REFRESH_TOKEN`, `GMAIL_API_SENDER`.
- else SMTP if `SMTP_HOST` + `SMTP_FROM_EMAIL` (STARTTLS, optional login).
- else **Null** — logs the message and drops it.

⚠️ **The silent NullEmailTransport bug — worst pitfall in this whole stack.** With
`SMTP_HOST` empty (the dev default) and no Gmail creds, production mail was **silently
dropped** — no error, just an info log. Fixes now in place, all worth porting:
`email_delivery_enabled()` (False when resolved transport is null) checked by callers;
the lead path records an explicit `delivery_disabled_null_transport` audit outcome; the
design treats "channel silently disconnected" as a first-class loud alert class. In the
new project: **assert a real transport at startup in prod, or at minimum alarm on it.**

Other transport features to keep:

- `_RedirectingEmailTransport` — set `EMAIL_DEV_REDIRECT` and ALL mail reroutes to that
  address with `[TEST -> original]` subject prefix + banner. Invaluable in staging.
- Inline logo attached by CID (`kelley-logo`, from `assets/email/`) — missing file logs
  and skips, mail still sends.
- `send_rendered_safely(to, rendered, scope)` — the best-effort adapter new call sites use.
- Gmail errors re-raise with the API response body so auth failures are distinguishable
  from bad recipients.

### Gmail API OAuth — the constraint that shaped it

Org policy **blocked service-account key creation**, so the transport uses an
**Internal + Desktop-type OAuth client** with a one-time interactive consent granted by
the sending mailbox (`sales@`), minting a long-lived **refresh token** stored in env.
`google.oauth2.credentials.Credentials` auto-refreshes access tokens;
`AuthorizedSession` POSTs base64url MIME to `users/me/messages/send`. Mail sends *as*
the consenting mailbox and lands in its Sent folder — consent must be granted as exactly
`GMAIL_API_SENDER`. Google libs are imported lazily so environments without
`google-auth` still import the module. Note: the interactive consent needs a browser;
minting the token from a headless box fails (Gmail sends from bare CLI test scripts also
fail — expected, not a bug).

### Templates — `backend/services/notification_templates.py` (~2450 lines)

Hand-rolled Python-string HTML (no template engine except the MJML case below). Shared
`_wrap_html(body, preheader=...)` shell: `#F4F5F7` page, white rounded card, CID logo
header, hidden preheader, NAP footer. Brand tokens (swap for the new brand):
gold CTA `#E8C46A` / ink `#14181F` / border `#D9B255`; Inter font stack. Helpers:
`_html_button`, `_details_table`, `_bullet_list`. Renderer registries: `EMAIL_RENDERERS`
(customer lifecycle), `STAFF_EMAIL_RENDERERS` (maps notification kind → renderer),
`SMS_RENDERERS` (ready for the SMS channel).

PII rule baked into `render_public_lead_notification`: when a BHPH finance application is
attached, the staff email suppresses free-text message + all PII and links to the CRM
instead — **email is not a PII channel**.

### MJML pipeline (customer lead confirmation)

- Source: `backend/templates/emails/lead_confirmation.mjml` (design tool)
- Compile: `npx -y mjml lead_confirmation.mjml -o lead_confirmation.html` (artifact is
  committed; **never hand-edit the compiled HTML**)
- Render: Jinja `FileSystemLoader` over `templates/emails/` with
  `autoescape=select_autoescape(["html"])` (injection guard on customer-supplied names)

**Hard rule: only `{{ variable }}` in the MJML — no `{% %}` control flow.** MJML mangles
Jinja blocks; precompute every conditional in Python and pass final strings.

Known wart to fix when porting: the `.mjml` still carries old-brand colors
(coral `#F76C45`) while `notification_templates.py` uses Kelley gold — recompile with the
new project's tokens from day one.

Customer confirmation is sent on BOTH the new-deal and duplicate-append lead paths
(commit `89ea999`), best-effort, audit rows record only the recipient **domain**
(PII minimization).

---

## 7. Env var checklist

```bash
# ---- URLs (load-bearing beyond display) ----
PUBLIC_API_BASE_URL=      # Twilio signature verification builds URLs from this. MUST be the public https URL in prod.
PUBLIC_SITE_URL=          # CAPI event_source_url
ADMIN_BASE_URL=           # deep links in staff notifications (/inbox)

# ---- Storefront analytics ----
STOREFRONT_ANALYTICS_ENABLED=true

# ---- Meta Pixel / CAPI ----
META_PIXEL_ID=
META_CAPI_TOKEN=
META_CAPI_ENABLED=false          # flip only after token pasted; backlog back-delivers
META_CAPI_TEST_EVENT_CODE=       # set temporarily to validate in Events Manager
META_CAPI_API_VERSION=v21.0

# ---- Twilio ----
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=               # also the signature-verification key
TWILIO_FROM_NUMBER=
TWILIO_MESSAGING_SERVICE_SID=    # preferred once A2P campaign is attached
SMS_SENDING_ENABLED=false        # see launch gates §3
INBOUND_SMS_REQUIRE_SIGNATURE=true

# ---- Meta messaging (inbox channel — distinct from CAPI vars) ----
META_APP_ID=
META_APP_SECRET=                 # X-Hub-Signature-256 verification
META_WEBHOOK_VERIFY_TOKEN=
META_PAGE_ID=
META_IG_ACCOUNT_ID=
META_PAGE_ACCESS_TOKEN=
META_MESSAGING_ENABLED=false
INBOUND_META_REQUIRE_SIGNATURE=true

# ---- Email ----
GMAIL_OAUTH_CLIENT_ID=           # all four present ⇒ Gmail API wins over SMTP
GMAIL_OAUTH_CLIENT_SECRET=
GMAIL_OAUTH_REFRESH_TOKEN=
GMAIL_API_SENDER=
SMTP_HOST=                       # empty + no Gmail ⇒ SILENT NullTransport — see §6
SMTP_PORT=587
SMTP_USERNAME= / SMTP_PASSWORD=
SMTP_FROM_EMAIL= / SMTP_FROM_NAME=
EMAIL_DEV_REDIRECT=              # staging: reroute ALL mail here
PUBLIC_LEAD_NOTIFY_EMAILS=       # csv override; else business profile email; else admins
```

Frontend (public site, **baked at build**): `NEXT_PUBLIC_API_BASE_URL`,
`NEXT_PUBLIC_META_PIXEL_ID`, `NEXT_PUBLIC_GA_ID`.

⚠️ **The env-bake bug (bit us in prod).** Next.js loads `.env.local` at HIGHER priority
than `.env.production` during `pnpm build`. A stray `.env.local` with
`NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000` baked the dev API URL into the prod
bundle — every browser beacon and lead form POSTed to localhost and got connection-refused.
Rules for the new project: never keep a `.env.local` containing `NEXT_PUBLIC_*` on a build
machine; make the build script delete/guard it; verify with
`grep -r "127.0.0.1" .next/` after building. Same bug class in the Vite admin SPA:
`VITE_API_URL` must end in `/api` in `.env.production` while dev doesn't — mismatched
suffix 404s all authed calls.

---

## 8. Cross-cutting patterns (the actual architecture)

These recur in every subsystem and are the real thing being ported:

1. **Best-effort side effects, atomic core.** Analytics, attribution, CAPI enqueue,
   notifications, confirmations — none may fail the customer-facing write. SAVEPOINT +
   broad-except around each side effect; the router owns the outer transaction.
2. **Queue-then-flush for external providers.** Write an outbox row in the same txn as
   the business object (CAPI: `ad_conversion_events`; notifications: `notification_jobs`),
   then a fast BackgroundTask flush + a periodic worker sweep as the safety net. Claim
   with `FOR UPDATE SKIP LOCKED`, cap attempts, keep `last_error`.
3. **Raw-store-first idempotent webhooks.** Persist the raw provider event keyed on the
   provider's message id BEFORE processing; duplicate = short-circuit 200. Second dedup
   layer on the domain table `(provider, provider_message_id)`. Providers retry; carriers
   duplicate; this makes it all safe.
4. **Verify signatures against the PUBLIC url** behind any reverse proxy
   (Twilio HMAC-SHA1, Meta `X-Hub-Signature-256`), from config not from `request.url`.
5. **Gates default OFF, code ships early.** `SMS_SENDING_ENABLED`, `META_CAPI_ENABLED`,
   `META_MESSAGING_ENABLED` all default false; the queues accumulate so enabling later
   back-delivers. Compliance clocks (A2P 10DLC ~weeks, Meta App Review ~weeks) start on
   day 1, code proceeds in parallel.
6. **Consent is data, not UI.** Timestamps + source columns, stamped server-side,
   first-consent-wins, opt-out never auto-cleared, separate columns per channel, and a
   `consent_snapshot` on every future outbound message.
7. **Shared correlation ids across systems** — one browser-generated `event_id` ties
   Pixel ↔ first-party analytics ↔ CAPI; `webhook_events.external_id` ↔
   `conversation_messages.provider_message_id` ties raw webhooks ↔ inbox.
8. **PII stays in the CRM.** Hash-or-drop at every boundary: hashed IPs in analytics,
   SHA-256 identifiers in CAPI, PII-suppressed staff emails, domain-only audit rows.

---

## 9. Recommended build order for the new project

Mirrors the dependency graph; each step is shippable alone.

1. **Day 0 — start the clocks:** register A2P 10DLC brand + campaign (with compliant
   consent copy + live Privacy/Terms URLs from step 3) and submit Meta App Review.
   These gate nothing below but take weeks.
2. **Email transports + templates** (§6) — everything else notifies through this.
   Assert non-null transport in prod.
3. **Public-form consent capture** (§3) — consent columns migration + optional-unchecked
   checkbox + Privacy/Terms SMS sections. Needed for the A2P submission in step 1.
4. **Storefront analytics** (§1) — migration, `analytics.ts`, track endpoint, lead
   attribution, journey panel.
5. **Meta Pixel + CAPI** (§2) — Pixel component, CAPI queue+sender behind
   `META_CAPI_ENABLED=false`; validate with test_event_code, then enable.
6. **Notification routing + subscribers** (§5) — event registry, four-layer resolution,
   job queue + worker, admin subscriber CRUD.
7. **Inbox core + inbound webhooks** (§4, §3-inbound) — conversations model, Twilio +
   Meta inbound, STOP/START, `inbox.message_received` fan-out. No approvals needed.
8. **Outbound SMS** — real Twilio transport, status callback endpoint (monotonic),
   quiet hours, full consent gate, media self-hosting. Flip `SMS_SENDING_ENABLED` only
   after the §3 launch gates pass.
9. **Outbound Meta send** after App Review (`human_agent` tag).

---

## 10. Pitfalls master list (what actually bit us)

| # | Pitfall | Defense |
|---|---|---|
| 1 | **Silent NullEmailTransport** — empty `SMTP_HOST` drops prod mail with zero errors | `email_delivery_enabled()` checks + loud audit rows; assert transport at startup in prod |
| 2 | **`.env.local` overrides `.env.production` at Next build** — dev API URL baked into prod bundle, all leads/beacons hit localhost | never keep `.env.local` on build machine; guard in build script; grep the bundle post-build |
| 3 | **Twilio signature verified against internal URL behind proxy** — 100% webhook rejection in prod only | build verify-URL from `PUBLIC_API_BASE_URL`; test against public URL explicitly before launch |
| 4 | **A2P rejected: consent as condition of service** — gated/required consent checkbox | optional + unchecked + "not a condition" wording + submit works without it |
| 5 | Consent shown but not recorded | `sms_consent_at/_source` stamped server-side, first-consent-wins |
| 6 | One consent flag reused across channels | 3 separate column pairs (email mktg / sms consent / sms opt-out) |
| 7 | Later form-submit silently clearing a STOP | opt-out only cleared by inbound START |
| 8 | Pixel/CAPI double-count | single browser `event_id` through all three systems; SPA PageView skips first render |
| 9 | CAPI events older than 7 days rejected by Meta | `suppressed` status; don't let the queue rot while disabled for months |
| 10 | Webhook retries creating duplicate messages | raw-store unique `(source, external_id)` + domain unique `(provider, provider_message_id)` |
| 11 | Unknown analytics events polluting tables / typos silently dropped | server whitelist + keep TS union in sync (grep both on every new event) |
| 12 | Twilio MMS media URLs expire | self-host media on ingest (we deferred this — don't) |
| 13 | Status callbacks out of order | monotonic transitions keyed on `(provider, provider_message_id)` |
| 14 | Minting new contacts from unknown inbound numbers | match-only; never create from inbound |
| 15 | Org policy blocks GCP service-account keys | Desktop-type OAuth client + refresh token as the sender mailbox; consent needs a browser |
| 16 | Jinja `{% %}` inside MJML gets mangled | variables only in `.mjml`; conditionals precomputed in Python |
| 17 | Notification kind fired with no email renderer registered | fan-out skips with warning — add renderer before wiring the event |
| 18 | `metadata` reserved on SQLAlchemy Base | map as `event_metadata` → column `"metadata"` |
| 19 | Compliance clocks started late | A2P + Meta App Review submitted on day 1, code flag-gated in parallel |
| 20 | Backend `.env` changes need a service restart (interactive sudo here) | fold restarts into the deploy runbook; flags don't hot-reload |
| 21 | **Unhandled 500s masquerade as CORS errors** — exceptions escape `CORSMiddleware`, so the error response carries no `Access-Control-Allow-Origin` and the browser reports a CORS block | when the SPA shows a CORS error on ONE endpoint while others work, it's a 500 — check backend logs for the traceback, not the CORS config; confirm with `curl -i -H "Origin: <spa-origin>"` (a 401/404 WITH CORS headers proves config is fine) |
