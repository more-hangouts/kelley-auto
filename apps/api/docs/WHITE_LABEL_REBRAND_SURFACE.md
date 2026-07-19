# White-Label Rebrand Surface Audit

Phase H2 deliverable. Catalogues every place a new-client deployment of this
codebase would need to change because the system is currently built around one
specific client (Bella's XV — a quinceañera dress boutique in San Antonio).

This is an **inventory**, not a refactor. Per the H1 strategy lock-in, we do
not parameterize speculatively. Each touch point below is tagged with one of:

- **PARAM** — already parameterized; the deployment changes a DB row or
  `.env` value with no code edit.
- **PARTIAL** — parameterized in the happy path but has a hardcoded fallback
  or a hardcoded sibling string that defeats the parameterization.
- **HARDCODE** — currently requires a code or template edit. Two sub-tags:
  - **HARDCODE / runbook** — the H3 deployment runbook should walk the
    operator through the edit during onboarding.
  - **HARDCODE / future slice** — too invasive to do per deployment;
    should be parameterized once a real second-client requirement
    justifies a follow-up slice.

The dividing line between `runbook` and `future slice` is: can one operator
make the change in an hour while standing up a new client, or does it require
a code-quality decision that should be made deliberately, not under deadline?
Short copy strings → runbook. Status vocabulary, event types, role
vocabulary, enrichment question schemas → future slice.

---

## 1. Business identity

| Touch point | State | File / location | Action |
|---|---|---|---|
| Legal name, display name, address, phone, email, website | **PARAM** | `business_profile` table (`legal_name`, `display_name`, `address_line1/2`, `city`, `state`, `postal_code`, `country`, `phone`, `email`, `website`) | DB UPDATE during onboarding |
| Logo file | **PARAM** | `business_profile.logo_storage_key` → file in `DOCUMENT_STORAGE_ROOT/business_profile/` | Upload via `POST /api/business-profile/logo` during onboarding |
| Default tax rate + name | **PARAM** | `business_profile.default_tax_rate`, `default_tax_name` | DB UPDATE |
| Invoice/quote terms + footer + payment instructions | **PARAM** | `business_profile.default_invoice_terms`, `default_invoice_footer`, `default_payment_instructions` | DB UPDATE |
| Discount presets, payment plan defaults, deposit % | **PARAM** | `business_profile.discount_presets` (JSONB), `default_payment_plan_count`, `default_deposit_percent` | DB UPDATE |
| Reminder cadence + late fee | **PARAM** | `business_profile.reminder{1,2,3}_*`, `reminder_late_fee_cents`, `reminder_late_fee_pct` | DB UPDATE |
| Attendance + selfie policy | **PARAM** | `business_profile.attendance_gate_enabled`, `selfie_policy`, `selfie_retention_days`, `biweekly_anchor_date` | DB UPDATE |
| PDF render fallback when `business_profile` is missing | **PARTIAL** | `services/invoice_pdf.py:298` defaults `business_name = "Bella's XV"` | **HARDCODE / runbook** — runbook step asserts `business_profile` is populated before any PDF render; alternatively, fix the fallback to raise rather than default (one-line follow-up if/when justified) |
| Portal email fallback when profile is missing | **PARTIAL** | `services/portal_email.py:90` falls back to `"Bella's XV"` | Same as above |
| Customer portal `"the boutique"` fallback | **PARTIAL** | `templates/portal/invoice.html:52` says `"the boutique"` when phone/email absent | **HARDCODE / runbook** — runbook step asserts `business_profile.phone` and `email` are set |
| Marketing site brand | **HARDCODE / runbook** | `marketing/index.html`, `marketing/fit-prep.html` — `<title>`, meta description, schema.org name, hero copy, footer, copyright; ~15 string sites total | Edit during onboarding; H3 runbook lists every line |
| Marketing logos + wordmarks | **HARDCODE / runbook** | `marketing/assets/logo.svg`, `wordmark.svg`, `wordmark-light.svg`, `og-image.jpg` | Replace assets during onboarding |
| Marketing hero photos | **HARDCODE / runbook** | `marketing/assets/hero-desktop.{jpg,webp}`, `hero-mobile.*`, `booking-placeholder.*` | Replace assets during onboarding |
| Booking widget logo | **HARDCODE / runbook** | `widgets/bellas-logo.svg` (45 lines, inline SVG path) | Replace asset during onboarding |
| Frontend SPA brand text in admin + sales chrome | **HARDCODE / runbook** | `frontend/index.html:7` `<title>Bellas XV</title>`; `frontend/src/pages/Login.jsx:58`; `frontend/src/sales/PinLogin.jsx:170`; `frontend/src/components/DashboardLayout.jsx:73`; `frontend/src/sales/SalesLayout.jsx:53` | Edit during onboarding — staff-only surface, low risk |
| Frontend favicon | **HARDCODE / runbook** | `frontend/public/vite.svg` (default Vite icon — Bella's never replaced it) | Replace asset during onboarding |
| Brand color tokens | **HARDCODE / runbook** | `marketing/styles.css:3-13` (`--rose-gold #B76E79`, `--amethyst #5D3A6B`, `--cream #FAF6F4`, `--blush #F4D5DC`, `--aubergine #2D1B2E`, etc.); portal templates use `--primary #A7616F`, `--text #2A1B1F` | Edit CSS during onboarding |
| Brand fonts | **HARDCODE / runbook** | `marketing/styles.css:14-15` (Inter + Playfair Display); `frontend/index.html` preconnects to Google Fonts for the same pair | Edit during onboarding |

## 2. Domains, hosts, CORS, cookies

| Touch point | State | File / location | Action |
|---|---|---|---|
| Cookie domain | **PARAM** | `config/settings.py:28` `SESSION_COOKIE_DOMAIN`, defaults `.shopbellasxv.com` | Set via `.env` to the client's apex |
| CORS allowlist | **PARAM** | `config/settings.py:21` `CORS_ORIGINS` | Set via `.env` per deployment |
| Booking widget allowed origins | **PARAM** | `config/settings.py:40` `BOOKING_WIDGET_ALLOWED_ORIGINS` | Set via `.env` per deployment |
| Public site URL (links in emails, etc.) | **PARAM** | `config/settings.py:31` `PUBLIC_SITE_URL` | Set via `.env` per deployment |
| Widget public base URL (for embed code) | **PARAM** | `config/settings.py:32` `WIDGET_PUBLIC_BASE_URL` | Set via `.env` per deployment |
| Portal base URL (invoice/quote portal links) | **PARAM** | `config/settings.py:39` `PORTAL_BASE_URL` | Set via `.env` per deployment |
| Attribution cookie domain (Meta CAPI, etc.) | **PARAM** | `config/settings.py:48` `ATTRIBUTION_COOKIE_DOMAIN` | Set via `.env` per deployment |
| nginx server blocks (admin, sales, api, marketing) | **HARDCODE / runbook** | `/etc/nginx/sites-enabled/admin.shopbellasxv.com`, `sales.*`, `api.*`, `shopbellasxv.com` | Per-deployment provisioning step in H3 runbook |
| certbot certs (per-host + apex) | **HARDCODE / runbook** | `/etc/letsencrypt/live/<host>/` for each host | Per-deployment provisioning step in H3 runbook |
| Hardcoded production hostname in notification template | **HARDCODE / runbook** | `services/notification_templates.py:505,511` — rebooking URL `https://shopbellasxv.com/#book` baked into cancellation email | Substitute `PUBLIC_SITE_URL` env var; one-line edit during onboarding, candidate for parameterization in a future slice if more URLs accumulate |
| Hardcoded host in booking widget settings UI | **HARDCODE / runbook** | `frontend/src/pages/BookingWidgetSettings.jsx:49,54,864` — embed code snippet hardcodes `https://api.shopbellasxv.com/widgets/...` | Edit the embed-code helper for the new client's API host |
| Hardcoded host in sales staff settings UI | **HARDCODE / runbook** | `frontend/src/pages/SalesStaffSettings.jsx:170` — `"sales.shopbellasxv.com"` in copy | Edit during onboarding |
| Marketing site canonical/OG URLs | **HARDCODE / runbook** | `marketing/fit-prep.html:8,13,14` — `https://shopbellasxv.com/...` canonical + OG | Edit during onboarding |
| Comments referencing `shopbellasxv.com` | **Informational** | ~15 file:line hits in `api/cookies.py`, `api/middleware/csrf.py`, `frontend/src/services/api.js`, etc. | Cosmetic only — comments do not affect behavior; safe to leave but ideally swept during a docs slice |

## 3. Outbound channel config + secrets

| Touch point | State | File / location | Action |
|---|---|---|---|
| SMTP host, port, username, password, from-email, TLS flag | **PARAM** | `config/settings.py:62-68` `SMTP_*` | Per-deployment `.env` |
| SMTP `From` display name | **PARTIAL** | `config/settings.py:67` `SMTP_FROM_NAME` defaults `"Bella's XV"` | Set via `.env` per deployment; runbook flags this is one of the few env defaults that is itself the brand string |
| Twilio account SID, auth token, from-number, messaging service SID | **PARAM** | `config/settings.py:72-75` `TWILIO_*` | Per-deployment `.env` |
| Booking internal notification CC list | **PARAM** | `config/settings.py:69` `BOOKING_INTERNAL_NOTIFICATION_EMAILS` | Per-deployment `.env` |
| Meta Pixel ID + CAPI token + test event code | **PARAM** | `config/settings.py:51-53` `META_*` | Per-deployment `.env` |
| Google Ads conversion ID, label, developer token | **PARAM** | `config/settings.py:54-56` `GOOGLE_ADS_*` | Per-deployment `.env` |
| Plausible analytics domain | **PARAM** | `config/settings.py:59` `PLAUSIBLE_DOMAIN` | Per-deployment `.env` |
| Per-deployment secrets (must be unique per client) | **PARAM** | `SECRET_KEY`, `INTEGRATION_TOKEN_KEYS`, `QUOTE_SIGNATURE_KEY`, `RESCHEDULE_TOKEN_SECRET`, `ENRICHMENT_TOKEN_SECRET` | Generated fresh per deployment; H3 runbook includes commands |
| Application config (DATABASE_URL, APP_TIMEZONE, APP_ENV, LOG_LEVEL, Redis URL, rate-limit fail-open, document storage backend/root/max-MB, JWT expiry, webhook retention days) | **PARAM** | `config/settings.py` | Per-deployment `.env` |

## 4. Customer-facing copy (the heavy hitter)

### 4a. Booking widget (`widgets/bellas-booking-widget.js`)

Compiled IIFE bundle, 51 KB, ~1300 lines. Reads a `theme` + `copy` config
object from the API at runtime and uses hardcoded fallbacks if the API
doesn't supply each field.

| Touch point | State | Location | Action |
|---|---|---|---|
| Header brand label | **PARTIAL** | line 790 — falls back to `"Bella's XV"` | The runtime config (`theme.header_brand`) already overrides this; runbook step sets the override |
| `"Bella's XV boutique"` label | **PARTIAL** | line 837 — falls back to `"Bella's XV boutique"` | Same — `state.copy.boutique_label` overrides |
| Party size option `"Me and my quinceañera"` | **PARTIAL** | line 1028 — falls back to `"Me and my quinceañera"` | `state.copy.step2_party_pair` overrides; **but** the *available party-size options* themselves (the enum keys) are baked in — a non-quince event with different party shapes would need a code change |
| Phone fallback in error message `"call (210) 670-5845"` | **HARDCODE / runbook** | line 762 | Rebuild the widget bundle with the client's phone, or surface this via the runtime config in a future slice |

### 4b. Fit-prep tool (`widgets/bellas-fit-prep-tool.js`)

Compiled IIFE bundle, 51 KB. Less runtime-configurable than the booking
widget.

| Touch point | State | Location | Action |
|---|---|---|---|
| Size chart label `"Bella's XV reference formalwear chart"` | **HARDCODE / runbook** | line 64 | Edit during onboarding |
| Dress style options (Ball gown, A-line, Mermaid, Two-piece, Unsure) | **HARDCODE / future slice** | lines 87-107 | Quinceañera-specific style vocabulary; a different boutique segment (bridal, formalwear, suiting) needs different options. Worth parameterizing via a config row once a second client validates which axes matter |
| Back style options (Corset, Zipper, Unsure) | **HARDCODE / future slice** | lines 87-107 | Same |
| Budget tiers ($1,000 / $1,500 / $2,000+) | **HARDCODE / future slice** | lines 87-107 | Numeric ranges tied to Bella's price point; needs per-client config |
| Favorite colors free-text input | **PARAM** | lines 87-107 | Already free-text; no rebrand needed |

### 4c. Marketing site (`marketing/`)

| Touch point | State | Location | Action |
|---|---|---|---|
| `index.html` `<title>`, meta description, OG tags, schema.org name | **HARDCODE / runbook** | `marketing/index.html:6,7,10,21` | Edit during onboarding |
| `index.html` hero headline `"She'll know when she finds it. Quinceañera & formal gowns in San Antonio."` | **HARDCODE / runbook** | `marketing/index.html:71` | Replace with client's tagline |
| `index.html` JS-disabled fallback `"call us at (210) 670-5845"` | **HARDCODE / runbook** | `marketing/index.html:88` | Edit during onboarding |
| `index.html` footer (address, phone, copyright) | **HARDCODE / runbook** | `marketing/index.html:111-116, 132` | Edit during onboarding |
| `fit-prep.html` page title, meta, OG, breadcrumb, headings, lede, CTA copy | **HARDCODE / runbook** | `marketing/fit-prep.html:6,7,10,93,96,98-100,122-124` | Edit during onboarding |
| Marketing CSS color tokens + fonts | **HARDCODE / runbook** | `marketing/styles.css:3-15` | Edit during onboarding |

### 4d. Email + SMS templates (`services/notification_templates.py`)

This is the biggest concentration of hardcoded copy in the entire codebase.
**None of these templates read `business_profile` at render time.** Subject
lines, body copy, header/footer wrappers, address constants, and the
rebooking URL are all baked into Python source.

| Touch point | State | Location | Action |
|---|---|---|---|
| `_BOUTIQUE_ADDRESS` constant `"7723 Guilbeau Rd #101, San Antonio, TX 78250"` | **HARDCODE / runbook** | line 34 | Edit during onboarding |
| `_BOUTIQUE_PHONE` constant `"(210) 670-5845"` | **HARDCODE / runbook** | line 35 | Edit during onboarding |
| Email HTML header `"Bella's XV"` + subheader `"Quinceanera appointments and styling"` | **HARDCODE / runbook** | lines 255-256 | Edit during onboarding |
| Email HTML footer `"Bella's XV · <addr> · <phone>"` | **HARDCODE / runbook** | line 262 | Auto-fixed once `_BOUTIQUE_ADDRESS` + `_BOUTIQUE_PHONE` are updated |
| Booking confirmation subject `"You're booked at Bella's XV — {slot}"` | **HARDCODE / runbook** | line 298 | Edit during onboarding |
| Booking confirmation body `"Bella's XV boutique, <addr>"` | **HARDCODE / runbook** | line 303 | Auto-fixed via the constants |
| Reminder subject `"See you tomorrow at Bella's XV — {slot}"` | **HARDCODE / runbook** | line 443 | Edit |
| Reminder body `"Bella's XV boutique, <addr>"`, `"your fitting is"` | **HARDCODE / runbook** | lines 455-456 | Edit (the word "fitting" assumes dress-fitting domain) |
| Cancellation subject `"Your Bella's XV appointment is cancelled — {slot}"` | **HARDCODE / runbook** | line 500 | Edit |
| Cancellation body rebooking URL `https://shopbellasxv.com/#book` | **HARDCODE / runbook** | lines 505, 511 | Substitute `PUBLIC_SITE_URL`; one-line follow-up to parameterize |
| Reschedule subject `"Your Bella's XV appointment is now {slot}"` | **HARDCODE / runbook** | line 529 | Edit |
| SMS confirmation `"Bella's XV: You're booked..."` | **HARDCODE / runbook** | line 559 | Edit |
| SMS reminder `"Bella's XV: see you tomorrow..."` | **HARDCODE / runbook** | line 569 | Edit |
| Internal booking notification subject + body | **HARDCODE / runbook** | line 367 | Edit (mentions "Quinceanera: {name}") |
| Enrichment-invitation email `"Complete your Boutique Experience Profile"` + `"Help us prepare dresses in your size, style, and budget"` | **HARDCODE / runbook** | lines 395-426 | Edit — the term "Boutique Experience Profile" is a Bella's-specific product name |
| Password-reset email subject `"Reset your Bella's XV admin password"` | **HARDCODE / runbook** | `services/password_reset.py:71` | Edit |
| Attendance pre-close reminder subject + body `"Bellas XV"` | **HARDCODE / runbook** | `services/attendance_pre_close.py:128,132` | Edit |
| Portal email subject helpers `"Your {shop} invoice {number}"`, `"the quote you asked for from {shop}"` | **PARAM** | `services/portal_email.py:111,155` — `shop` pulled from `business.legal_name` | Already parameterized |
| Portal email CTA `"call the boutique"` | **PARTIAL** | `services/portal_email.py:118,131` | Generic-noun phrasing — works for most boutique-adjacent clients but not for a non-retail business |

### 4e. Portal templates (`templates/portal/`)

| Touch point | State | Location | Action |
|---|---|---|---|
| Business name in header/footer | **PARAM** | `templates/portal/base.html:31,40` — pulled from `business.legal_name` | None |
| Quote signature copy `"Once you sign, this quote becomes your contract with {{ business.legal_name }}."` | **PARAM** | `templates/portal/quote.html:35` | None |
| `"the boutique"` fallback when phone/email missing | **PARTIAL** | `templates/portal/invoice.html:52` | Runbook step asserts profile completeness |
| Brand colors `--primary #A7616F`, `--text #2A1B1F` | **HARDCODE / runbook** | `templates/portal/static/portal.css` | Edit during onboarding |
| Logo asset path | **PARAM** | `templates/portal/base.html:15` — pulled via `/api/business-profile/logo` | None |

### 4f. PDF templates (`templates/pdf/`)

| Touch point | State | Location | Action |
|---|---|---|---|
| Business name + address + phone + email + website + logo in PDF header | **PARAM** | `templates/pdf/_base.html` + `services/invoice_pdf.py:_resolve_business_header` (lines 293-331) | None — all DB-driven |
| Logo file URL | **PARAM** | `business_profile.logo_storage_key` → absolute filesystem path → `file://` URL | None |
| PDF render fallback name | **PARTIAL** | `services/invoice_pdf.py:298` defaults `"Bella's XV"` if profile missing | Runbook step asserts profile populated; fallback to be tightened in a future slice |

## 5. Workflow language + domain vocabulary (the parameterization minefield)

This is where the codebase is most thoroughly Bella's-specific. **All entries
in this section are HARDCODE / future slice** because changing them touches
schema CHECK constraints, service-layer logic, API contracts, and frontend
state in lockstep. A second-client deployment that happens to be another
quinceañera boutique can use this section as-is; a client in any other
appointment-driven vertical needs the work in this section before it can
ship.

### 5a. Event types

- **`services/event_workflow.py:81`** — only `"quinceanera"` defined.
- **`database/migrations/015_create_events.py:29-31`** — CHECK constraint
  `event_type IN ('quinceanera')`.
- **`api/routers/events.py:58`** — `event_type: Literal["quinceanera"] = "quinceanera"` default.
- **`frontend/src/pages/Pipeline.jsx:42`** — `const EVENT_TYPE = 'quinceanera'`.

Adding a new event type requires a migration, a workflow definition, an API
contract update, and frontend wiring. **Future slice.**

### 5b. Event statuses

- **`services/event_workflow.py:20-77`** — nine statuses, several with
  dress-domain semantics: `lead`, `consulted`, `sold` (= deposit paid +
  *dress selected*), `on_order`, `arrived` (= *dress in store*),
  `in_alterations`, `ready_for_pickup`, `picked_up`, `cancelled`.
- **`database/migrations/015_create_events.py:32-38`** — CHECK constraint
  enforcing the same set.
- Status display labels for the pipeline kanban are server-rendered via
  `/api/events/board` so the labels live in the workflow definition above.

Changing the status set is a migration + service + API + UI sweep. **Future
slice.**

### 5c. Participant roles

- **`database/migrations/015_create_events.py:89-91`** — CHECK constraint
  `role IN ('quinceanera', 'dama', 'chambelan', 'parent', 'other')`.
- **Migration line 115-117** — unique index forcing exactly one
  `'quinceanera'` participant per event.
- **`api/routers/event_participants.py:39-42`** — `ParticipantRole` Literal
  type enforces the same vocabulary at the API.

A non-quinceañera client cannot use this schema as-is. **Future slice.**

### 5d. Domain-flavored column names

- **`services/event_service.py:51-52,113-115`** — `EventOverrides` carries
  `quince_theme` + `quince_theme_colors`.
- **`services/booking_contracts.py:106-107,122`** — `celebrant_first_name`,
  `celebrant_last_name`, `boutique_experience_profile_id`.
- **`appointments.celebrant_first_name`** + `celebrant_last_name` columns —
  the booking record itself uses celebrant vocabulary.
- **`services/catalog_service.py:93-99`** — `_CATEGORY_LABELS` includes
  `quince_gown`, `bridal_gown`, etc.

Renaming these requires schema migrations and ripples across service
signatures, API serializers, frontend types, and every smoke that references
them. **Future slice.** A non-Bella's quinceañera client can use the schema;
any other vertical needs a generalization pass.

## 6. Database CHECK constraint cleanup (concrete migration list)

For reference when the future-slice work is picked up — these are every
quinceañera-flavored CHECK constraint or unique index the cleanup would
need to touch:

- `database/migrations/015_create_events.py:29-31` — `chk_events_event_type`
- `database/migrations/015_create_events.py:32-38` — `chk_events_status`
- `database/migrations/015_create_events.py:89-91` —
  `chk_event_participants_role`
- `database/migrations/015_create_events.py:115-117` — unique index on
  `(event_id, role)` where `role = 'quinceanera'`

## 7. What's already cleanly parameterized (the wins)

Things that look like they'd be a rebrand pain but already aren't:

- **Business profile mechanism** — single-row `business_profile` table with
  every operational dial (name, address, tax, terms, reminders, attendance
  policy). Most rebrand work that *looks* like code editing is actually a
  DB UPDATE.
- **PDF rendering pipeline** — WeasyPrint reads `business_profile` at render
  time; logos pass through `logo_storage_key`. No PDF re-templating needed
  to ship a new client.
- **Portal templates** — base.html, invoice.html, quote.html all pull from
  `business`; only one fallback string (`"the boutique"`) and one CSS
  color token need a touch.
- **Email send pipeline** — SMTP config is fully env-driven. Only the
  *bodies* are hardcoded; the *transport* is clean.
- **Cookie + CORS + URL config** — every host-related env var is wired
  through `config/settings.py` with sensible defaults that can be
  overridden per deployment. D3 cookie auth landed with the right
  parameterization shape already.
- **Outbound channel config** — Meta, Google Ads, Plausible, Twilio: all
  env-driven, all optional.
- **Secrets generation** — every per-deployment secret has a documented
  generation command in `config/settings.py` comments.

---

## Rebrand workload estimate for a hypothetical second quinceañera boutique

If a second client is in the same vertical (quinceañera dress boutique) and
just needs to be rebranded:

- **DB UPDATE**: ~1 hour to populate `business_profile` with the new
  client's name, address, contacts, tax, terms, reminder cadence,
  discount presets, attendance/selfie policy.
- **Asset replacement**: ~1 hour to swap logo, wordmark, hero photos,
  favicon, and OG image.
- **Code edits per runbook**: ~2-3 hours to walk `services/notification_templates.py`,
  `services/portal_email.py`, `services/password_reset.py`,
  `services/attendance_pre_close.py`, `frontend/src/{pages,sales,components}/*`,
  `marketing/index.html`, `marketing/fit-prep.html`, `widgets/bellas-fit-prep-tool.js`,
  and the brand-token CSS files. About 30-40 string sites total per the
  inventory above.
- **Hostname swaps**: ~1 hour to update nginx configs, certbot, `.env`
  hostnames, and the three frontend embed-code references.
- **Smoke gate + DNS cutover**: per the H3 runbook (pending).

**Total: roughly one work-day per rebrand for a quinceañera-vertical client.**

## Rebrand workload estimate for a non-quinceañera client

If the second client is in a different vertical (bridal, formalwear, suiting,
spa, salon, photo studio, etc.), all of the above PLUS a future slice to:

- Generalize event types (drop the single-value CHECK constraint, define
  multiple workflow vocabularies, route event_workflow.py through a
  registry).
- Rename or generalize quinceañera-specific column/field names
  (`quince_theme*`, `celebrant_*`, `boutique_experience_profile_*`,
  catalog `quince_gown` etc.).
- Decide whether participant roles become a workflow-specific list or get
  generalized to a freer "role" field with per-workflow validation.
- Rebuild the fit-prep widget bundle with the new vertical's style /
  budget / back-style options, or surface those via the runtime config.

**Estimated effort: 1-2 weeks of dedicated work**, justified once a real
second-vertical client commits.

The H1 strategy doc names this as a future-pivot trigger candidate, but
**a non-vertical rebrand is its own kind of trigger** — it forces the same
generalization work whether deployment is per-tenant or multi-tenant.

---

## What this audit does NOT do

Per the H2 hard rule (audit-only):

- No code, schema, template, or asset was modified.
- No parameterization was implemented.
- No new env vars were added.
- No follow-up slices were created beyond the markers in this doc.

The H3 client deployment runbook is the next slice and is where the
`HARDCODE / runbook` entries above turn into concrete edit steps.
