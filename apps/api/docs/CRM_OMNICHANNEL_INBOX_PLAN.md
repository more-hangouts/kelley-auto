# Omnichannel CRM Inbox + Notification Routing Plan

Goal: staff handle every lead conversation from inside the CRM — Twilio
SMS/MMS, Facebook Messenger, Instagram DMs, later web chat/email — with one
shared inbox, per-user read state, and a configurable "who gets what"
notification system that supports recipients who have no CRM login at all.

This builds a Chatwoot-style inbox natively (concepts, not code) instead of
running a separate Chatwoot/Rails/Docker stack. Do not enable live outbound
messaging until the channel-specific compliance and app-review gates pass.

> **Canonical copy.** This doc lives in `/opt/kelley` (branch
> `rebrand/bellas-to-kelley`), the production checkout. An earlier draft in
> `/home/deploy/kelley-auto` is superseded — never build from that tree.

## Locked Decisions

| Decision | Choice |
|---|---|
| Chatwoot | Borrow the product model (inbox/conversation/assignee/status/canned replies); build natively. No second app. |
| Notification recipients | New subscriber system layered onto `services/notification_routing.py`. Admins add/remove people per event kind. A recipient may be **email-only with no user account**. |
| Sales rep access | Reps reply to customers from **sales.kelleyautoplex.com** (the sales tree of the admin SPA dist — Caddy block already live). Reps see the whole inbox ("Mine" filter provided); small team, coverage beats territoriality. Revisit if reps ever poach. |
| Read state | **Per-user** (`conversation_reads.last_read_at`), not a global counter. |
| Conversation lifecycle | `open` / `pending` / `resolved`; **inbound on a resolved conversation reopens it** and re-notifies. |
| Quiet hours | Outbound guard, 9:00 PM – 8:00 AM America/Chicago (TCPA window). Staff replies get a confirm-to-send override; automated sends hard-block. |
| Meta reply window | Request the **`human_agent` tag** in App Review (7-day reply window). Without it the 24-hour standard window makes weekend leads unreachable. |
| Message identity | Channel-neutral `sender_ref`/`recipient_ref` (phone for SMS, PSID/IGSID for Meta). Uniqueness on `(provider, provider_message_id)`. |
| Person ↔ channel link | Chatwoot-style `contact_channel_identities` join table so one contact's SMS + FB + IG threads group under one person. |
| Media | Download and self-host on receipt (Meta/Twilio URLs expire). Reuse the `import_inventory_photos.py` storage pattern. |

## Current Starting Point (verified in /opt/kelley)

- Public leads create/reuse a `vehicle_sale` event via
  `services/public_lead_service.py`; staff get email + in-app lead alerts.
- Contacts carry `phone`, `phone_e164`, `marketing_consent_at` (email-marketing
  consent — **not** an SMS consent signal).
- `services/sms_transport.py` defines the `SmsTransport` protocol; currently
  returns `NoopSmsTransport`. Twilio env names already reserved in settings.
- Notification spine already exists and is the thing we extend, not replace:
  - `services/notification_routing.py` — `record_event()` writes
    `staff_notification_events`, `recipients_for()` resolves
    intrinsic targeting → `ROLE_DEFAULTS` → per-user `notification_preferences`.
  - `notification_jobs` queue (`channel` CHECK `email|sms`, `recipient` string,
    nullable `recipient_user_id`) + `workers/notifications.py`
    (asyncio, 30 s poll, `FOR UPDATE SKIP LOCKED`).
  - `services/notification_preferences_service.py` — `KIND_DESCRIPTORS`
    self-describing kinds for prefs UI.
- `services/integration_tokens.py` — Fernet-encrypted token storage (use for
  Meta page tokens).
- `api/redis_rate_limit.py` — rate limiting for the public webhook endpoints.
- Migrations are **Python**, in `backend/database/migrations/`; next is `093`.
- Routers live in `api/routers/` (`admin_*`, `sales_*` conventions).
- Admin SPA + sales portal are one Vite build at `backend/frontend/`;
  hostname self-routing (`isSalesSubdomain`) serves `src/sales/` on
  sales.kelleyautoplex.com.

---

## Part 1 — "Who Gets What": Notification Subscribers

The gap in today's routing: every recipient must be a `users` row. The owner
wants, e.g., an accountant or the dealership principal to get email alerts
with no login. So we add a **subscriber registry** as a fourth resolution
layer.

### Migration 093: subscriber tables

`notification_subscribers`

- `id SERIAL PRIMARY KEY`
- `user_id INTEGER NULL REFERENCES users(id) ON DELETE CASCADE`
  — NULL ⇒ external, email-only person
- `display_name TEXT NOT NULL`
- `email TEXT NULL` — required when `user_id IS NULL` (CHECK)
- `phone_e164 TEXT NULL` — future SMS alerts
- `is_active BOOLEAN NOT NULL DEFAULT TRUE`
- `created_at / updated_at TIMESTAMPTZ`
- Partial unique: one subscriber row per user; unique lower(email) for
  external rows.

`notification_subscriptions`

- `subscriber_id INTEGER NOT NULL REFERENCES notification_subscribers ON DELETE CASCADE`
- `kind TEXT NOT NULL` — event kind, e.g. `inbox.message_received`
- `channel TEXT NOT NULL DEFAULT 'email'` — `email` | `in_app` | later `sms`
- `enabled BOOLEAN NOT NULL DEFAULT TRUE`
- `PRIMARY KEY (subscriber_id, kind, channel)`

### Routing integration

`recipients_for()` resolution becomes four layers, first three unchanged:

1. Intrinsic targeting (event is *about* user X) — always wins.
2. `ROLE_DEFAULTS` per `users.role`.
3. Per-user `notification_preferences` overrides.
4. **NEW:** union of active `notification_subscribers` subscribed to the kind.
   External subscribers (`user_id IS NULL`) can only receive `email` — the
   dispatcher enqueues `notification_jobs` with `recipient=email`,
   `recipient_user_id=NULL` (the queue already supports this; migration 077
   pattern). `in_app` subscriptions are only valid for subscribers with a
   `user_id`.

Dedupe recipients by email/user across layers so someone doesn't get double
emails from role default + subscription.

### New event kinds (register in `KIND_DESCRIPTORS` + `TIMING_MODE`)

| Kind | Intrinsic target | Default routing |
|---|---|---|
| `inbox.message_received` | conversation assignee, else lead owner | admins (role default) + subscribers |
| `inbox.conversation_unlinked` (new Meta thread, no contact) | — | admins + subscribers |
| `inbox.message_failed` (delivery failure) | sender of the message | admins |
| `inbox.channel_disconnected` (Meta token dead / webhook failing) | — | admins + subscribers — **real-time, loud**; this is the silent-NullEmailTransport failure class |
| `inbox.opt_out_received` | lead owner | admins |

Owner-first routing rule for `inbox.message_received`: if the conversation
has an assignee or its event has a lead owner, in-app ping goes to them
immediately; everyone else's role/subscription copy is suppressed for 5
minutes and cancelled if the message is read (prevents "every text pings the
whole store" fatigue while guaranteeing nothing is silently missed).

### Admin UI — Settings → Notifications

New tab in `pages/Settings.jsx` (or sibling page) rendering a people × kinds
matrix from a self-describing endpoint (same pattern as
`notification_preferences_service`):

```
Notification recipients                                [+ Add person]
──────────────────────────────────────────────────────────────────────
Person            Login   New msg  Unlinked  Failures  Opt-out  Channel
Chase (sales)     yes       ✓        —          —        ✓      in-app+email
Luis (admin)      yes       ✓        ✓          ✓        ✓      in-app+email
Front desk        NO        ✓        ✓          —        —      email only
accountant@…      NO        —        —          ✓        —      email only
```

"+ Add person" asks only name + email — no user account required.
Deactivating a row stops all their notifications; deleting a `users` row
cascades. Router: `api/routers/admin_notification_subscribers.py` with
`GET/POST /api/admin/notification-subscribers`,
`PUT /{id}/subscriptions`, `DELETE /{id}`.

This subsystem ships **first** and is useful immediately: retro-fit existing
`admin.new_booking` / lead-alert kinds onto it so the owner can add the
front desk to lead emails today, before the inbox exists.

---

## Part 2 — Inbox Data Model (migration 094)

### `contact_channel_identities` (Chatwoot's contact_inboxes)

One row per (person, channel address). Linking an identity once links every
past and future conversation on it.

- `id BIGSERIAL PRIMARY KEY`
- `contact_id INTEGER NULL REFERENCES contacts(id) ON DELETE SET NULL`
  — NULL until staff links or auto-match succeeds
- `channel TEXT NOT NULL` — `sms` | `facebook` | `instagram`
- `external_id TEXT NOT NULL` — E.164 phone, PSID, or IG-scoped ID
- `display_name TEXT`, `avatar_url TEXT` — from Meta profile fetch; makes
  unlinked triage humane
- `metadata JSONB NOT NULL DEFAULT '{}'`
- `created_at / updated_at`
- `UNIQUE (channel, external_id)`

### `crm_conversations`

- `id BIGSERIAL PRIMARY KEY`
- `identity_id BIGINT NOT NULL REFERENCES contact_channel_identities`
- `event_id INTEGER NULL REFERENCES events(id) ON DELETE SET NULL`
- `channel TEXT NOT NULL`, `provider TEXT NOT NULL` — `twilio` | `meta`
- `provider_thread_id TEXT`
- `status TEXT NOT NULL DEFAULT 'open'` CHECK (`open|pending|resolved`)
- `assigned_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL`
- `last_message_at / last_inbound_at / last_outbound_at TIMESTAMPTZ`
- `last_inbound_preview TEXT` — list-pane snippet without joining messages
- `metadata JSONB`, `created_at / updated_at`
- `UNIQUE (provider, channel, identity_id)` — **hard guard against duplicate
  conversations from racing webhooks; all creation goes through upsert.**
- No `unread_count` column — unread is derived per user (below).

Contact linkage lives on the identity, not the conversation; `event_id`
stays here (a person can have identities across channels but the deal
context is per-conversation).

### `crm_messages`

- `id BIGSERIAL PRIMARY KEY`
- `conversation_id BIGINT NOT NULL REFERENCES crm_conversations ON DELETE CASCADE`
- `direction TEXT NOT NULL` CHECK (`inbound|outbound`)
- `channel TEXT NOT NULL`
- `sender_ref TEXT NOT NULL`, `recipient_ref TEXT NOT NULL` — channel-neutral
  (E.164 / PSID / IGSID); never called "number"
- `body TEXT`
- `media JSONB NOT NULL DEFAULT '[]'` — self-hosted paths + original URLs
- `status TEXT NOT NULL` — `queued|sent|delivered|read|failed|received`
- `provider TEXT NOT NULL`, `provider_message_id TEXT`
- `UNIQUE (provider, provider_message_id)` — dedupe across webhook retries
  **and** echo events of our own sends
- `provider_payload JSONB`, `provider_error_code/`_message TEXT`
- `sent_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL`
- `is_echo BOOLEAN NOT NULL DEFAULT FALSE` — staff replied from the FB app /
  Page inbox; ingested so the thread stays complete
- `consent_snapshot JSONB NOT NULL DEFAULT '{}'` — exact consent copy at send
- `created_at / sent_at / delivered_at / failed_at TIMESTAMPTZ`

Indexes: `(conversation_id, created_at DESC)`;
`(direction, status, created_at DESC)`.

Status updates are **monotonic** (`queued < sent < delivered/read`,
`failed` terminal): Twilio callbacks arrive out of order; never let a late
`sent` overwrite `delivered`.

### `conversation_reads` (per-user read state)

- `conversation_id BIGINT REFERENCES crm_conversations ON DELETE CASCADE`
- `user_id INTEGER REFERENCES users(id) ON DELETE CASCADE`
- `last_read_at TIMESTAMPTZ NOT NULL`
- `PRIMARY KEY (conversation_id, user_id)`

Unread for user U = conversations where `last_inbound_at > COALESCE(U.last_read_at, '-infinity')`.
Opening a thread upserts `last_read_at = now()`.

### `webhook_events_raw` (fast-ack buffer)

- `id BIGSERIAL`, `provider TEXT`, `payload JSONB`, `headers JSONB`,
  `received_at`, `processed_at TIMESTAMPTZ NULL`, `error TEXT NULL`
- Webhook handlers validate the signature, insert the raw row, and return
  200/TwiML **immediately**. A worker loop (same
  `FOR UPDATE SKIP LOCKED` pattern as `workers/notifications.py`) does
  matching, media download, notification fan-out. Meta disables slow
  webhooks; this also gives replay-for-free when a processing bug ships.

### Contact SMS state (columns on `contacts`)

- `sms_opted_out_at TIMESTAMPTZ`, `sms_opt_out_source TEXT`
- `sms_consent_at TIMESTAMPTZ`, `sms_consent_source TEXT`
- Do **not** overload `marketing_consent_at` — that is email-marketing.

Phone tie-break when matching inbound SMS to `contacts.phone_e164` (the CRM
import means duplicate phones are plausible): newest contact having an open
event wins; else newest contact; ambiguity flagged in conversation
`metadata.match='ambiguous'` for triage.

---

## Part 3 — Channel Transports

### Twilio (`services/sms_transport.py`, replace the noop branch)

Env: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`,
`TWILIO_MESSAGING_SERVICE_SID` (preferred once A2P attached),
`TWILIO_STATUS_CALLBACK_URL`, `SMS_SENDING_ENABLED=false` (default),
`SMS_DEV_REDIRECT` (optional test-routing).

- Disabled flag ⇒ persist the outbound row as `queued`, never call Twilio.
- Capture Message SID; map Twilio exceptions to `failed` rows +
  `inbox.message_failed` event — never crash the CRM request.
- Keep the `SmsTransport` protocol so `workers/notifications.py` is untouched.
- Add `twilio` to `requirements.txt`; smokes use a fake client.

### Meta (`services/meta_messaging.py`, new)

Env/settings: `META_APP_ID`, `META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN`,
`META_PAGE_ID`, `META_IG_ACCOUNT_ID`, `META_MESSAGING_ENABLED=false`.
Page access token stored **encrypted in `integration_tokens`** (Fernet
helper already exists), not in `.env`.

- Send via Graph Send API; Messenger and IG differ only in recipient ID +
  endpoint — one module, two thin adapters.
- **Reply window enforcement before send:** ≤24 h since last inbound ⇒
  standard send; ≤7 days ⇒ send with `tag=HUMAN_AGENT` (requires the
  App-Review permission); older ⇒ hard-block, composer explains "window
  closed — call or text instead".
- **Token lifecycle:** prefer a System-User long-lived token if a Business
  Manager exists. On any auth error from Graph, fire
  `inbox.channel_disconnected` and show a red "Meta disconnected —
  reconnect" banner in the inbox. Never fail silently (NullEmailTransport
  lesson).
- Store Meta message ID + raw Graph payload on every send.

### Media archiving (`services/inbox_media.py`)

Inbound attachment URLs (Meta and Twilio MMS) expire. The webhook worker
downloads each to local storage following the `import_inventory_photos.py`
pattern, stores the local path in `crm_messages.media`, keeps the original
URL for audit. Size cap + content-type allowlist (images, pdf, audio).

---

## Part 4 — Inbound Webhooks

Routers: `api/routers/webhooks_twilio.py`, `api/routers/webhooks_meta.py`.
Both public endpoints get `redis_rate_limit` + request-size caps. No Caddy
change needed — `api.kelleyautoplex.com` already proxies to :8000.

### Twilio

- `POST /api/webhooks/twilio/sms` — validate the signature **against the
  public URL** (behind Caddy the app sees the internal URL; build the
  validation URL from configured `PUBLIC_API_BASE_URL` + path, honoring
  `X-Forwarded-Proto`). This is the classic works-in-dev-fails-in-prod trap;
  test it explicitly.
- Respond with empty TwiML `<Response/>` (Twilio treats other bodies/errors
  as failures).
- Insert raw row → worker: normalize E.164 → upsert identity → upsert
  conversation → insert message → STOP/START handling → `record_event`.
- STOP/STOPALL/UNSUBSCRIBE/CANCEL/END/QUIT ⇒ set `sms_opted_out_at` (Twilio
  also blocks at carrier level; we mirror). START/UNSTOP ⇒ clear it.
- `POST /api/webhooks/twilio/status` — match `(provider,
  provider_message_id)`, monotonic status update, idempotent on retries.

### Meta

- `GET /api/webhooks/meta` — hub.challenge verification.
- `POST /api/webhooks/meta` — verify `X-Hub-Signature-256` (app secret),
  insert raw row, 200 immediately. Worker handles:
  - Messenger `messages`, Instagram `messages` events.
  - **`message_echoes`**: staff replying from the FB Page inbox/phone —
    ingest as `is_echo` outbound so the CRM thread is complete; the
    `(provider, provider_message_id)` unique also swallows echoes of our own
    API sends.
  - Dedupe by message ID (Meta retries aggressively).
  - Identity upsert by `(channel, PSID/IGSID)`; fetch profile name + avatar
    via Graph for triage display.
  - Conversation upsert; **resolved ⇒ reopen to `open`** on inbound;
    `record_event('inbox.message_received', ...)`.
  - Auto-link: if identity already linked to a contact, attach; else the
    conversation surfaces in the **Unlinked** filter and fires
    `inbox.conversation_unlinked`.

---

## Part 5 — APIs

### Shared inbox service (`services/inbox_service.py`)

All conversation/message logic lives here; admin and sales routers are thin
wrappers with different auth dependencies (per repo convention: routers
contain no business logic).

### Admin router `api/routers/inbox.py` (admin session auth)

- `GET /api/inbox/conversations` — filters: channel, status, assigned,
  `unread` (per current user), `unlinked`, `q`
- `GET /api/inbox/conversations/{id}` — thread + identity + event summary;
  upserts `conversation_reads`
- `POST /api/inbox/conversations/{id}/messages` — send
- `PATCH /api/inbox/conversations/{id}` — status, assignee, event link
- `POST /api/inbox/conversations/{id}/link-contact` — link identity →
  contact (creates contact if asked); retro-links all conversations on that
  identity
- `GET /api/events/{event_id}/messages`, `GET /api/contacts/{id}/messages`

### Sales router `api/routers/sales_inbox.py` (rep PIN-session auth, same
dependency as `sales_notifications.py`)

Same surface, scoped: reps see all conversations (locked decision), can
send, assign-to-self, set status, and link contacts; only admins delete or
edit consent state. `sent_by_user_id` always records the actual rep.

### Send validation (both routers, enforced in the service)

1. Channel enabled flag (`SMS_SENDING_ENABLED` / `META_MESSAGING_ENABLED`).
2. SMS: identity has valid E.164; reject if `sms_opted_out_at`.
3. Meta: reply-window check (24 h / 7 d `human_agent` / blocked).
4. **Quiet hours:** outside 8 AM–9 PM America/Chicago ⇒ API returns
   `409 quiet_hours`; UI shows a confirm dialog ("It's 9:40 PM for this
   customer — send anyway?") and retries with `override_quiet_hours=true`
   (logged in `consent_snapshot`). Automated/scheduled sends have no
   override path.
5. Per-channel body-length caps; rate limit per staff user and per
   conversation (accidental-spam guard).
6. Response returns the persisted message row so the UI appends instantly.

---

## Part 6 — UI

### Admin SPA — new top-level **Inbox** (`pages/Inbox.jsx`)

Nav item between Dashboard and Pipeline, with a per-user unread badge.

```
┌ Inbox ──────────────┬──────────────────────────────┬────────────────────┐
│ [All][SMS][FB][IG]  │  Maria G.  · IG  · open      │ CONTACT            │
│ [Unread][Mine]      │──────────────────────────────│ Maria Gonzalez     │
│ [Unlinked][Resolved]│  ◀ is the 2019 Silverado     │ (210) 555-0187     │
│─────────────────────│    still available?    7:02p │ [Open contact]     │
│ ● Maria G.      IG  │  ▶ Yes! Want to come see     │ EVENT              │
│   is the 2019…  2m  │    it tomorrow?        7:05p │ 2019 Silverado     │
│   Jake T.      SMS  │    ✓ delivered               │ KAP-00003 · $2,000 │
│   STOP          1h  │──────────────────────────────│ down · Deals: New  │
│   Unknown (FB)  ⚠   │ [canned ▾] [reply…    ][Send]│ [Open deal]        │
│   hey do yall…  3h  │  quiet hours 9p–8a · win 6d  │ ASSIGN: Chase ▾    │
└─────────────────────┴──────────────────────────────┴────────────────────┘
```

- Unlinked (⚠) rows show Meta profile name/avatar; right pane offers
  **Create contact / Link existing / Link to deal**.
- Composer disabled states with explicit reasons: missing phone · opted out
  · channel not enabled (registration pending) · Meta window closed ·
  Meta disconnected.
- Canned replies (admin-editable list, stored server-side): availability,
  schedule-a-visit, financing/trade-in.
- Red banner on `inbox.channel_disconnected`.

### Event detail — **Messages** tab (in `pages/event/`)

Chronological bubbles for conversations linked to this event, composer with
segment counter, same disabled-state logic. Contact detail
(`ContactDetail.jsx`) gets the same tab across all the contact's identities,
each message linking back to its event.

### Sales portal — `src/sales/InboxList.jsx` + `src/sales/ConversationView.jsx`

Added to `SalesLayout.jsx` nav with unread badge; mobile-first (reps live on
phones). Same list/thread/composer, "Mine" filter defaults on the list,
one-tap **Assign to me**. Read state is per rep user via
`conversation_reads`. Reuses the rep session from `SalesProtectedRoute`.

### Dashboard + notifications

Extend the existing lead-alert pop-up pattern: inbound message on any
channel pops for the intrinsic target (assignee/lead owner) immediately;
click deep-links to the conversation. Realtime = existing polling for v1
(same interval as lead alerts); SSE/WebSocket deferred.

---

## Part 7 — Consent + Compliance

- Lead-form disclosure (deploy before first outbound SMS):

  ```text
  By submitting this form, you agree to receive calls and text messages from
  Kelley Autoplex about your inquiry. Msg & data rates may apply. Reply STOP
  to opt out.
  ```

- Store `sms_consent_at/_source` on the contact at form submit; snapshot the
  exact copy into `consent_snapshot` on every outbound.
- Never text imported/legacy contacts without recorded consent.
- One-to-one lead replies only in v1; bulk campaigns are out of scope and
  would need their own A2P campaign + marketing consent flow.
- Privacy Policy / Terms / SMS Terms pages live on the public site (A2P and
  Meta App Review both require them).

### External clocks — start both on day 1, in parallel with the build

1. **Twilio:** Primary Compliance profile → A2P 10DLC brand + campaign →
   buy local 210 (backup 726) number with SMS+MMS → attach to messaging
   service. Weeks, not days.
2. **Meta:** Developer app → connect FB Page → connect IG Professional
   account → webhook setup → App Review requesting `pages_messaging`,
   `instagram_manage_messages`, **and the `human_agent` tag** → possibly
   Business verification for advanced access. Needs live privacy policy,
   screencast, test instructions. Until approval, only app-role test
   accounts can message.

---

## Part 8 — Testing

Backend smokes (fake transports; follow `docs/TESTING.md` pattern):

- Subscriber CRUD; external email-only subscriber receives an enqueued job
  with `recipient_user_id=NULL`; deactivation stops jobs; layer-dedupe (role
  default + subscription ⇒ one email).
- Owner-first suppression: assignee ping immediate, others suppressed then
  cancelled on read.
- Conversation upsert race: two concurrent inbound events, one conversation
  (unique constraint + upsert).
- Message dedupe on `(provider, provider_message_id)`; echo ingestion
  doesn't duplicate our own API send.
- Monotonic status: late `sent` after `delivered` is a no-op.
- STOP sets opt-out and blocks sends; START clears.
- Quiet-hours 409 + override path logged; automated send has no override.
- Meta window: >7 d blocked; 25 h send carries `HUMAN_AGENT` tag.
- Twilio signature validated against the **public** URL; invalid sig 403.
- Meta challenge verification; bad `X-Hub-Signature-256` rejected.
- Reopen-on-inbound: resolved → open + notification fired.
- Per-user unread: user A reads, user B still unread.
- Link-contact retro-links all conversations on the identity.
- Phone tie-break: duplicate `phone_e164` picks open-event contact and flags
  ambiguity.

Manual, post-approval: live SMS round-trip incl. STOP/START; FB + IG
round-trip from real non-admin accounts; reply from CRM and from the FB Page
inbox (echo appears); window-closed UI; media attachment archived and
rendered.

---

## Part 9 — Build Order + Rollout

Phases are independently shippable; 1–4 need no external approvals.

1. **Migration 093 + subscriber system + Settings UI** (Part 1). Retro-fit
   existing lead-alert kinds. *Immediately useful standalone.*
2. **Migration 094 + inbox core**: tables, `inbox_service`, webhook raw
   buffer + worker, Twilio inbound/webhooks with flags off, admin Inbox UI,
   event/contact Messages tabs. Inbound SMS works read-only day one
   (inbound is compliance-free; only outbound is gated).
3. **Outbound SMS** behind `SMS_SENDING_ENABLED=false`: transport, send
   validation, quiet hours, composer. Flip true only after the Twilio gate.
4. **Sales portal inbox** (list/thread/composer, Mine filter, assign-to-me).
5. **Meta channel**: transport, webhooks, unlinked triage, profile fetch,
   echo ingestion — testable with app-role accounts pre-review.
6. **Flip flags** per launch gates below; monitor week one: delivery
   failures, webhook errors/latency, window blocks, opt-outs, unmatched
   inbound, per-rep send volume.

Deferred (post-v1): website chat + email on the same conversation model;
after-hours auto-reply; assignment rules; pipeline-card unread badges;
conversation search; compliant bulk follow-ups.

### Launch gates

Shared:
- [ ] Subscriber system live; owner can add an email-only recipient.
- [ ] Inbox shipped behind disabled flags; unlinked triage works.
- [ ] `inbox.channel_disconnected` alarm verified (kill a token in staging).
- [ ] Backend restarted (joins the pending restart queue — needs
      interactive sudo).

Twilio:
- [ ] Compliance profile + A2P brand/campaign approved; 210 number attached.
- [ ] Privacy/Terms/SMS-Terms URLs live; lead-form consent copy deployed.
- [ ] Signature validation passes against the public URL in prod.
- [ ] Live STOP/START round-trip passed. → `SMS_SENDING_ENABLED=true`.

Meta:
- [ ] App Review approved incl. `human_agent`; Page + IG connected.
- [ ] Webhook signature + challenge verified in prod; echo ingestion seen.
- [ ] Live FB + IG round-trips from non-admin accounts passed.
      → `META_MESSAGING_ENABLED=true`.
