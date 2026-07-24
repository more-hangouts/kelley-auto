# Storefront Analytics and CAPI Plan

> **Archived / historical — not authoritative.** Retained for context; may describe old paths, hostnames, or a pre-monorepo layout. Current docs: [README](../../README.md) · [ARCHITECTURE](../ARCHITECTURE.md) · [OPERATIONS](../OPERATIONS.md) · [CLAUDE](../../CLAUDE.md). See [archive index](README.md).


## Goal

Build first-party storefront analytics so Kelley Autoplex can see each shopper's path to conversion, then use the same clean event stream for ad attribution and retargeting through Meta Conversions API when ads begin.

This should be implemented without putting sensitive BHPH application data into analytics, notes, emails, or third-party ad payloads.

## Decisions

1. Build first-party analytics first.
   - Track anonymous storefront behavior in Kelley-owned tables.
   - Attach the current visitor/session to a lead at submission time.
   - Keep analytics useful even before any ad platform is configured.

2. Make the event model CAPI-ready from day one.
   - Generate stable `event_id` values for conversion events.
   - Capture `_fbp` and `_fbc` values when present.
   - Store UTM, referrer, landing page, user agent, and IP-derived request context server-side.
   - Keep a provider-neutral outbound event queue so Meta CAPI is not hardwired into the lead intake path.

3. Do not send BHPH sensitive PII to ad platforms.
   - Never send DOB, driver license data, SSN, full application address, or encrypted `lead_applications` payloads to Meta.
   - For lead matching, only use approved normalized identifiers such as email/phone/name/ZIP when consent and policy allow.
   - Hash customer information parameters server-side before sending to Meta CAPI.

4. Start with lead-level journey analytics before dashboards.
   - First deliver: "This lead viewed these vehicles, came from this source, and converted after this many minutes."
   - Aggregate reports can follow once the base events are reliable.

5. Add consent and suppression controls before activating ad destinations.
   - Analytics can be first-party operational tracking.
   - Retargeting/CAPI activation needs explicit configuration, documented policy review, and an easy kill switch.

## Phase 1: First-Party Storefront Tracking

### Backend Schema

Add tables similar to:

- `storefront_visitors`
  - `id`
  - `visitor_key` unique opaque ID
  - `first_seen_at`
  - `last_seen_at`
  - `created_at`

- `storefront_sessions`
  - `id`
  - `visitor_id`
  - `session_key` unique opaque ID
  - `started_at`
  - `last_seen_at`
  - `landing_page`
  - `initial_referrer`
  - `initial_utm` JSONB
  - `user_agent`
  - `ip_hash`
  - `created_at`

- `storefront_events`
  - `id`
  - `visitor_id`
  - `session_id`
  - `event_name`
  - `event_id` unique nullable
  - `path`
  - `referrer`
  - `utm` JSONB
  - `listing_code`
  - `vehicle_catalog_item_id`
  - `metadata` JSONB
  - `occurred_at`
  - `created_at`

- `lead_attribution`
  - `id`
  - `event_id` FK to `events.id`
  - `visitor_id`
  - `session_id`
  - `conversion_storefront_event_id`
  - `landing_page`
  - `source_page`
  - `utm` JSONB
  - `referrer`
  - `created_at`

### Storefront Events

Track these first:

- `page_view`
- `vehicle_view`
- `lead_form_opened`
- `lead_form_started`
- `lead_submitted`

Useful metadata:

- Listing code
- Vehicle year/make/model
- Stock number
- Price, if available
- CTA name
- Form type, such as `contact`, `schedule_viewing`, or `bhph_prequal`

### Browser Identifiers

Use first-party cookies:

- `ka_vid`: anonymous visitor ID
- `ka_sid`: session ID
- Preserve Meta browser cookies if present:
  - `_fbp`
  - `_fbc`

Do not store raw application PII in these cookies.

### Lead Join

On public lead submit:

- Include `ka_vid`, `ka_sid`, `_fbp`, `_fbc`, and current `event_id`.
- Create the CRM lead/event as usual.
- Create a `lead_attribution` row.
- Record `lead_submitted` in `storefront_events`.

## Phase 2: Admin Lead Journey View

Add a section on the event detail page:

- Landing page
- Source / UTM
- Referrer
- Session duration
- Vehicles viewed before conversion
- Path to conversion
- Last page before submit
- Lead form type

This should be read-only and available to the sales users who can already see the lead. It must not expose encrypted BHPH application fields.

## Phase 3: Meta CAPI-Ready Event Pipeline

### Configuration

Add environment variables:

- `META_CAPI_ENABLED=false`
- `META_PIXEL_ID=`
- `META_CAPI_ACCESS_TOKEN=`
- `META_CAPI_TEST_EVENT_CODE=`
- `META_CAPI_API_VERSION=`

Keep CAPI disabled by default until the ad account and Pixel are ready.

### Outbound Queue

Add an `ad_conversion_events` table:

- `id`
- `provider`, initially `meta`
- `event_name`
- `event_id`
- `event_time`
- `source_url`
- `action_source`, usually `website`
- `user_data` JSONB
- `custom_data` JSONB
- `status`, such as `pending`, `sent`, `failed`, `suppressed`
- `attempt_count`
- `last_error`
- `sent_at`
- `created_at`

The lead submission path should enqueue CAPI events, not block on Meta.

### Meta Events To Support

Start with:

- `PageView`
- `ViewContent` for vehicle detail views
- `Lead` for completed lead forms

Later:

- `Contact`
- `Schedule`
- `SubmitApplication`, likely as a custom event if needed

### Deduplication

Generate a unique `event_id` for each browser event that might also be sent server-side.

Use the same `event_id` for:

- Browser Pixel event, once Pixel is installed
- Server-side CAPI event

This prevents duplicate counting when both browser Pixel and CAPI are active.

### User Data For Matching

Allowed CAPI matching inputs, when policy and consent allow:

- Hashed email
- Hashed phone
- Hashed first name / last name
- Hashed city/state/ZIP/country if collected outside sensitive application storage
- `_fbp`
- `_fbc`
- Client IP address
- Client user agent

Do not use:

- DOB
- Driver license number
- SSN
- Encrypted application address from `lead_applications`
- Notes content
- Free-text message content

### Custom Data

For vehicle events, send non-sensitive commerce/context fields:

- `content_type=vehicle`
- `content_ids`, using listing code or catalog item ID
- `content_name`, such as `2006 HUMMER H2`
- `currency=USD`
- `value`, if price is available
- `vehicle_year`
- `vehicle_make`
- `vehicle_model`
- `stock_number`

## Phase 4: Retargeting Readiness

Once ads begin, create audiences around non-sensitive behavior:

- Viewed any vehicle detail page
- Viewed a specific make/model
- Viewed BHPH vehicles or opened BHPH form
- Started a lead form but did not submit
- Submitted a lead, for exclusion from prospecting campaigns

Avoid creating audiences based on sensitive credit/application details.

## Phase 5: Aggregate Reporting

After lead-level journeys are working, add admin dashboards:

- Most-viewed vehicles
- Highest-converting vehicles
- Views with no leads
- Source/UTM performance
- Lead conversion rate by vehicle
- Lead conversion rate by traffic source
- Average time from first view to lead

## Privacy and Security Rules

- No BHPH sensitive PII in analytics tables.
- No raw email/phone in analytics tables unless there is a clear operational reason.
- Hash IP if storing it in first-party analytics.
- Keep ad destination tokens encrypted or in environment variables only.
- Add a kill switch for all outbound CAPI delivery.
- Audit CAPI config changes and failed delivery spikes.
- Document which identifiers are sent to Meta before enabling live ads.

## Implementation Order

1. Add analytics tables and backend event ingestion endpoint.
2. Add storefront visitor/session cookies and page/vehicle tracking.
3. Attach visitor/session IDs to lead submissions.
4. Add lead journey panel in admin.
5. Add outbound ad conversion queue with Meta disabled.
6. Add Meta CAPI sender in test mode.
7. Install browser Pixel with matching `event_id` only when ads are ready.
8. Enable CAPI live after Meta Events Manager test events pass.
9. Build aggregate reporting dashboards.

## Open Inputs Needed Before CAPI Activation

- Meta Business Manager access
- Pixel ID
- CAPI access token
- Test event code
- Final privacy/consent language for storefront
- Decision on whether browser Pixel will run alongside server CAPI
- List of audiences to build first

## First Sprint Definition Of Done

- A public-site visitor gets a `ka_vid` and `ka_sid`.
- Vehicle detail views are recorded.
- Lead submissions are tied to the visitor/session.
- Event detail admin page shows the lead's journey.
- No sensitive BHPH application data appears in analytics records.
- CAPI data model exists but outbound Meta delivery remains disabled until credentials and policy are ready.
