# Kelley Autoplex Migration & Integration Runbook

Combines the **drivereliable** dealership site with the **bellasxv** back office,
rebranded for Kelley Autoplex.

The target is one repo with two apps:

```text
kelley-autoplex/
├─ frontend/  # Next.js public site, no Payload, no Prisma
└─ backend/   # Bellas FastAPI API + React/MUI admin + salesman portal
```

The public site reads inventory, leads, business profile, and blog content from
the backend over HTTP. The backend/admin is the only system of record.

## One-Screen Plan

1. **Baseline first:** copy both apps into the final repo, commit untouched,
   boot backend API, admin SPA, and public frontend locally.
2. **Set Kelley shell + env early:** use Kelley domains, database names, upload
   paths, cookie names, dark admin theme, and dealership nav labels before deep
   feature work starts.
3. **Make Bellas car-capable:** add vehicle fields to `catalog_items`, add
   `vehicle_sale` workflow stages, and smoke-test catalog/deal behavior before
   touching the public site.
4. **Stabilize the public API:** ship `/api/public/inventory`,
   `/api/public/leads`, `/api/public/business-profile`, and blog endpoints with
   public-safe DTOs.
5. **Then remove Payload:** rewire Next.js to those endpoints, build the site,
   and delete Payload/Prisma only after the pages compile and forms create
   backend deals.
6. **Finish the back office:** complete admin/sales UI rebrand, seed Kelley
   branding and inventory, deploy domains, run the lead-to-delivered QA script.

## Locked Decisions

- **Single backend:** Bellas/FastAPI owns all data, admin, CRM, quotes, invoices,
  payments, users, notifications, and workers.
- **Public frontend stays:** Keep the drivereliable Next.js marketing/site UI,
  but replace Payload local API calls with HTTP calls to Bellas public endpoints.
- **Payload is removed:** No Payload admin, collections, route group, or Payload
  packages remain after frontend parity.
- **Prisma is removed:** The legacy drivereliable Prisma stack is unused once the
  public site reads Bellas.
- **Blog default:** Add lightweight admin-editable `posts` in Bellas. Do not keep
  Payload just for blog.
- **Sales login default:** Keep Bellas PIN login for the salesman portal for v1.
  Revisit email/password only if the dealership needs remote individual login.
- **Booking default:** Website forms create a lead/deal immediately. Full slot
  availability/test-drive scheduling can reuse Bellas appointments after the lead
  spine works.
- **Rebrand boundary:** user-facing surfaces, env examples, cookie/domain
  defaults, and deploy paths use Kelley immediately. Legacy internal
  table/workflow names remain until their sprint phase to avoid risky global
  renames.

## Gaps Closed In This Version

- Do not rename database tables in the first pass. Keep `catalog_items`, `events`,
  and `contacts` internally until the app is stable; rebrand API DTOs and UI copy.
- Add explicit API contracts before rewiring the frontend.
- Add a current-state inventory before deleting Payload/Prisma code.
- Preserve old Bellas workflow assumptions until the car workflow has smoke tests.
- Add CORS, cookie/domain, rate-limit, spam, and lead attribution decisions.
- Add media/image handling, inventory import, SEO redirects, and production smoke
  gates.
- Add phase exit criteria that can be verified without interpretation.

## Source Projects

- `drivereliable/drivereliable-main/`
  - Next.js 15 App Router, Payload 3.79, Postgres.
  - Public pages currently use `getPayload()` in `src/lib/api.ts`.
  - Lead form posts to `src/app/api/inquiries/route.ts`.
  - Remove later: `src/payload.config.ts`, `src/collections`, `src/app/(payload)`,
    `prisma/`, `/api/cars`, `/api/admin/cars`, `reliable-cars/`.
- `bellasxv/bellasxv-main/`
  - FastAPI + SQLAlchemy + Postgres.
  - React 19 + MUI 6 admin SPA under `frontend/`.
  - Strong existing fits: contacts, event kanban, catalog, quote/invoice/payment,
    business profile, sales PIN portal, smoke-test pattern.

## Hosting Status

Server size is selected, but the server is not provisioned yet. **We build and
test entirely locally** and keep the code host-agnostic so the selected server
slots in with only env + DNS changes:

- All URLs, origins, secrets, DB/Redis, SMTP/Twilio, and cookie domains come from
  `.env` — never hardcoded. This is a Phase 0 constraint, not a Phase 7 task.
- Phase 7 (Deployment) is **deferred** until the server is provisioned. Everything
  before it is fully verifiable on localhost.
- Repo: single GitHub monorepo (`frontend/` + `backend/`) — different stacks but
  one source of truth, simplest for solo dev and matches the Phase 0 layout. Each
  app deploys independently later from its subfolder.

Selected VPS target:

```text
$17/mo
2 CPU cores
4 GB RAM
80 GB SSD
3 TB bandwidth
Ubuntu 24.04
4 GB swapfile
```

## Target Domains (intended — not provisioned yet)

```text
kelleyautoplex.com        -> frontend Next.js public site
api.kelleyautoplex.com    -> backend FastAPI
admin.kelleyautoplex.com  -> backend admin SPA
sales.kelleyautoplex.com  -> backend salesman portal route/surface
```

Local defaults (what we actually run during the build):

```text
frontend: http://127.0.0.1:3000
backend API: http://127.0.0.1:8000/api
backend admin SPA: http://127.0.0.1:5173
```

## Domain Mapping

| Bellas concept | Kelley concept | Implementation note |
|---|---|---|
| `catalog_items` | vehicle inventory | Keep table name for v1. Add vehicle columns and expose vehicle DTOs. |
| `internal_sku` | stock number | Staff-facing only. Never expose from public vehicle list/detail unless explicitly approved. |
| `public_code` | listing code | Keep immutable. Consider new prefix after the current CHECK is updated. |
| `designer` | make | Reuse initially, then add typed `make` if cleaner. |
| `style_number` | model | Reuse initially, then add typed `model` if cleaner. |
| `product_title` | listing title | Public title, for example `2019 Toyota Camry LE`. |
| `color` | exterior color | Add `interior_color`; keep `color` only as compatibility field if needed. |
| `unit_price_cents` | cash price | Public price; null means call for price. |
| `image_urls` | vehicle photos | Keep URL array for v1; define upload/import process before seed. |
| `events` | sales deals | Prefer new `event_type='vehicle_sale'`; keep old workflow support until migrated. |
| `events.status` | deal stage | `new_lead`, `contacted`, `appointment`, `test_drive`, `negotiation`, `financing`, `sold`, `delivered`, `lost`. |
| `contacts` | customers | Keep phone-first identity. Email is fallback only. |
| `event_participants` | co-buyer/co-signer | Hide in v1 unless needed. |
| appointments | test-drive/requested visit | Reuse after basic lead capture works. |
| sales portal | salesman portal | Keep appointments today, assigned leads, customer notes; hide attendance/time-off. |
| business profile | NAP/brand/tax/defaults | Add hours/social fields if missing. |

## Public API Contract

Create a new router, for example `api/routers/public_site.py`, mounted under
`/api/public`. Keep routers thin and put behavior in services.

### `GET /api/public/inventory`

Purpose: feed home page featured cars and `/shop`.

Query:

- `status=available` default
- `make`, `model`, `body_type`, `fuel_type`, `transmission`, `drivetrain`
- `min_price`, `max_price`, `min_year`, `max_year`, `max_mileage`
- `q`, `sort`, `page`, `limit`

Response DTO:

```json
{
  "docs": [
    {
      "id": 123,
      "listingCode": "KAX-00123",
      "title": "2019 Toyota Camry LE",
      "make": "Toyota",
      "model": "Camry",
      "year": 2019,
      "priceCents": 1499500,
      "mileage": 82214,
      "status": "available",
      "exteriorColor": "White",
      "interiorColor": "Black",
      "transmission": "Automatic",
      "fuelType": "Gas",
      "bodyType": "Sedan",
      "drivetrain": "FWD",
      "photos": ["https://..."],
      "createdAt": "2026-06-16T00:00:00Z",
      "updatedAt": "2026-06-16T00:00:00Z"
    }
  ],
  "totalDocs": 1,
  "page": 1,
  "limit": 24,
  "totalPages": 1
}
```

### `GET /api/public/inventory/{idOrListingCode}`

Purpose: feed inventory detail page.

Rules:

- Return only active public inventory.
- Do not expose wholesale, staff notes, internal SKU/stock number, or deleted rows.
- Sold vehicles can return if directly requested so old links do not hard fail.

### `POST /api/public/leads`

Purpose: contact page and vehicle inquiry forms.

Request:

```json
{
  "firstName": "Luis",
  "lastName": "Vazquez",
  "email": "luis@example.com",
  "phone": "+12105550123",
  "message": "I want to see this car.",
  "vehicleId": 123,
  "preferredTime": "2026-06-20 afternoon",
  "sourcePage": "/inventory/123",
  "utm": { "source": "google", "campaign": "used-cars" },
  "marketingConsent": true
}
```

Behavior:

- Normalize phone and use `contact_service.find_or_create_contact`.
- Create or link a `vehicle_sale` deal in `new_lead`.
- Attach the vehicle/listing reference to the deal notes or a dedicated field.
- Enqueue internal notification if email/SMS is configured.
- Rate-limit by IP and add a honeypot/turnstile-compatible field before launch.
- Return a generic success response; do not leak duplicate/contact matching.

### `GET /api/public/business-profile`

Purpose: NAP, hours, logo, phone/email, social links, tax/business display name.

Add missing `business_profile` fields if needed:

- `hours_json`
- `social_links`
- `hero_image_url` or brand asset references

### Blog Endpoints

Default implementation:

- `posts`: `id`, `slug`, `title`, `excerpt`, `body_markdown`, `cover_image_url`,
  `status`, `published_at`, `created_at`, `updated_at`.
- Admin CRUD in the Bellas admin.
- Public endpoints:
  - `GET /api/public/posts`
  - `GET /api/public/posts/{slug}`

Do not migrate Payload rich text directly unless existing posts are real content
worth preserving. If there are only placeholders, seed fresh markdown posts.

## Seamless Build Order

### Phase 0 - Repo, Baseline, And Inventory

Goal: get both apps running unchanged and write down what can be deleted.

Tasks:

- Create the final repo layout: `frontend/` from drivereliable and `backend/`
  from bellas.
- Initialize git after copying. Commit the unmodified import as `baseline`.
- Add root-level `README.md` with local dev commands and app URLs.
- Add root-level `.gitignore` covering Python, Node, env files, builds, zips,
  SQLite/test output, and OS files.
- Add `.env.example` files:
  - root orchestration values
  - `frontend/.env.example` with `NEXT_PUBLIC_API_BASE_URL`
  - `backend/.env.example` with DB, Redis, secrets, SMTP, Twilio, app domains.
- Boot backend API, backend admin SPA, and frontend locally.
- Run existing backend smoke tests that do not require production services:
  - `tests/test_auth_smoke.py`
  - `tests/test_events_smoke.py`
  - `tests/test_catalog_router_smoke.py`
  - `tests/test_business_profile_smoke.py`
  - `tests/test_sales_auth_smoke.py`
- Make a deletion inventory for the frontend:
  - every import of `payload`, `@payload-config`, `@payloadcms/*`
  - every import of Prisma
  - every route under `src/app/api`
  - every page/component depending on Payload-only types.

Exit criteria:

- All three dev servers boot from the new repo.
- Backend migrations run on an empty local DB.
- Existing critical smokes pass or failures are documented with exact cause.
- There is a committed baseline before behavior changes.

### Phase 1 - Vehicle Data Model Without Big Renames

Goal: make Bellas able to store cars while keeping existing internals stable.

Tasks:

- Add migration `085_vehicle_inventory_fields.py`:
  - `vin`, `stock_number`, `year`, `make`, `model`, `trim`
  - `mileage`, `transmission`, `fuel_type`, `exterior_color`, `interior_color`
  - `body_type`, `drivetrain`, `condition`, `vehicle_status`
  - optional: `carfax_url`, `video_url`, `features_json`
- Keep `catalog_items` and existing quote/invoice references intact.
- Backfill compatibility values:
  - `make` from `designer`
  - `model` from `style_number`
  - `exterior_color` from `color`
  - `vehicle_status='available'` for active rows.
- Add service input/output support in `services/catalog_service.py`.
- Add admin API support in `api/routers/catalog.py`.
- Add or update smoke tests for vehicle field create/list/search/patch.

Exit criteria:

- A vehicle can be created, listed, searched, patched, and used on a quote.
- Existing catalog quote/invoice smokes still pass.
- Public-safe vehicle DTO can be generated without internal fields.

### Phase 2 - Deal Workflow

Goal: support dealership kanban stages without breaking the existing event code.

Tasks:

- Add `vehicle_sale` to `services/event_workflow.py`.
- Add migration `086_vehicle_sale_workflow.py` to widen DB constraints for:
  - `event_type='vehicle_sale'`
  - statuses: `new_lead`, `contacted`, `appointment`, `test_drive`,
    `negotiation`, `financing`, `sold`, `delivered`, `lost`.
- Decide whether `sold` or `delivered` is terminal:
  - recommended: `delivered` and `lost` terminal; `sold` is not terminal until
    paperwork and delivery are complete.
- Update event create/promote helpers to default public leads to `vehicle_sale`
  and `new_lead`.
- Preserve old quince workflow until all admin routes/pages are rebranded.
- Add status-change smoke for the full vehicle flow.

Exit criteria:

- Public/service code can create a `vehicle_sale` deal in `new_lead`.
- Kanban board can fetch `event_type=vehicle_sale`.
- Status patching, audit rows, and terminal flags work.

### Phase 3 - Public API

Goal: expose exactly what the Next.js site needs and nothing more.

Tasks:

- Build `api/routers/public_site.py`.
- Add `services/public_inventory_service.py` if catalog public rendering would
  otherwise leak internal fields.
- Add `services/public_lead_service.py` for website lead orchestration.
- Add `services/post_service.py` and migrations/admin CRUD for blog posts.
- Add CORS rules for local frontend and production domain.
- Add request validation, rate limiting, and spam fields for lead submission.
- Add public API smoke tests:
  - inventory list hides internal fields
  - vehicle detail handles available/sold/missing
  - lead post creates contact + vehicle_sale deal
  - business profile returns seeded NAP
  - posts list/detail return only published posts.

Exit criteria:

- The frontend can be built against stable JSON contracts.
- Public endpoints require no staff auth.
- Public endpoints do not expose internal SKU, wholesale, staff notes, or deleted rows.

### Phase 4 - Frontend API Rewrite And Payload Removal

Goal: keep the public site UI but remove all local CMS/database coupling.

Tasks:

- Replace `src/lib/api.ts` with HTTP fetch helpers using
  `NEXT_PUBLIC_API_BASE_URL` or server-only `API_BASE_URL`.
- Keep ISR/revalidation where pages already use it.
- Create new frontend types matching the public DTOs.
- Rewire:
  - home featured cars
  - `/shop`
  - `/inventory/[id]`
  - inventory inquiry form
  - `/contact` form
  - `/blog` and `/blog/[slug]`
  - shared navbar/footer NAP.
- Replace Payload media helpers with URL-safe image helpers.
- Remove local API routes that become obsolete:
  - `/api/inquiries`
  - `/api/cars`
  - `/api/admin/cars`
- Delete Payload and Prisma only after pages compile without them.
- Run `rg` gates:
  - no `payload`
  - no `@payload`
  - no `@payload-config`
  - no `prisma`
  - no `getPayload`

Exit criteria:

- `pnpm build` passes in `frontend/`.
- Public pages render from Bellas API.
- Lead forms create backend kanban cards.
- Payload and Prisma dependencies are absent from `package.json`.

### Phase 5 - Admin Rebrand

Goal: staff can run the dealership workflow in dealership language.

Tasks:

- Relabel visible UI:
  - Event -> Deal
  - Pipeline -> Deals
  - Catalog -> Inventory
  - Celebrant/stylist/quince wording -> Customer/salesman/deal wording.
- Update `Pipeline.jsx` to request `event_type=vehicle_sale`.
- Update `AdminCatalog.jsx` columns and forms for vehicle fields.
- Hide quince-only fields:
  - court size
  - theme
  - theme colors
  - measurements
  - tried-on language unless repurposed.
- Salesman portal:
  - keep PIN login
  - show assigned leads/deals
  - show appointments/test drives
  - show customer notes
  - hide attendance, shifts, time-off, clock-in.
- Update global search labels/results for vehicles, customers, deals.
- Add UI smoke/manual checklist for lead-to-delivered.

Exit criteria:

- Staff can create a vehicle, receive a lead, move it through the deal board,
  create quote/invoice/payment, and mark delivered.
- No user-facing Bellas/quince wording remains in admin/sales/public surfaces.

### Phase 6 - Branding, NAP, And Seed Data

Goal: make the product feel native to Kelley Autoplex.

Tasks:

- Seed `business_profile`:
  - legal/display name
  - address
  - phone/email
  - website
  - hours
  - logo
  - default tax rate/name
  - invoice terms/footer/payment instructions.
- Replace public frontend logos and hardcoded strings.
- Update MUI theme and favicon/app metadata.
- Create inventory CSV import script using the catalog seed pattern.
- Define required CSV columns:
  - stock number, VIN, year, make, model, trim, price, mileage, status,
    transmission, fuel, exterior/interior colors, body type, drivetrain,
    description, photo URLs.
- Add import dry-run mode and duplicate VIN/stock-number handling.
- Seed 5-10 realistic vehicles for local QA.

Exit criteria:

- A string scan for `Bellas`, `quince`, `Reliable`, and `drivereliable` has no
  user-facing hits.
- A real or representative inventory CSV imports cleanly.
- Public SEO metadata uses Kelley Autoplex.

### Phase 7 - Deployment (DEFERRED until the selected server is provisioned)

Goal: production-ready services with clear rollback. Do not start until hosting
exists. Target the selected `$17/mo` VPS: 2 CPU cores, 4 GB RAM, 80 GB SSD, and
3 TB bandwidth, with Ubuntu 24.04 and a 4 GB swapfile. Because Phase 0 keeps all
config in env, this phase should be mostly provisioning + DNS + TLS, with no code
changes.

> **Full step-by-step runbook: [VPS_SETUP.md](VPS_SETUP.md)** — provisioning, SSH
> hardening, automatic security updates, swap, UFW firewall, fail2ban, Docker log
> rotation, per-container memory limits, worker recycling, Redis/Postgres memory
> caps, earlyoom, backups, TLS, and a security sign-off checklist.

> **Hard constraint — run the backend as a single worker.** The rate limiters and
> the two background workers (`workers/notifications`, `workers/daily`) run
> in-process in the FastAPI lifespan and hold state in-process. Multiple uvicorn
> workers would duplicate the background loops and split rate-limit counters. Deploy
> `--workers 1` until that state is moved to Redis. See `backend/docs/ARCHITECTURE.md`.

Tasks:

- Server:
  - provision selected `$17/mo` 2-core / 4 GB RAM / 80 GB SSD VPS
  - add 4 GB swap
  - install Docker and Docker Compose plugin
  - configure firewall for SSH, HTTP, and HTTPS only
  - keep Postgres and Redis private to the host/container network.
- Backend:
  - provision Postgres and Redis
  - configure `.env`
  - run migrations
  - configure systemd for uvicorn
  - configure workers/lifespan jobs
  - configure log rotation and backups.
- Frontend:
  - build Next.js
  - if memory is tight, build one heavy service at a time or build frontend in CI
  - run under the chosen process manager
  - set API base URL to `https://api.kelleyautoplex.com/api`.
- Admin SPA:
  - build `backend/frontend/dist`
  - serve via nginx vhost.
- nginx/certbot:
  - `kelleyautoplex.com`
  - `api.kelleyautoplex.com`
  - `admin.kelleyautoplex.com`
  - `sales.kelleyautoplex.com`.
- Cookie/CORS:
  - API allows public frontend origin.
  - Admin/sales auth works on their hostnames.
  - Set cookie domains deliberately; do not rely on browser defaults by accident.
- Backups:
  - nightly Postgres dump
  - uploaded media/logo backup
  - restore test before launch.
- Current-site capture:
  - keep `backend/scripts/scrape_kelley_current_site.py` read-only
  - after the new app is deployed and before final DNS/cutover, run it against
    `https://www.kelleyautoplex.com/`
  - write `data/reports/kelley_current_site_scrape.json`
  - review VINs, specs, photos, hours, phone/email/address, and scrape failures
  - feed the reviewed JSON into the Day 9 vehicle import path
  - do not run it during early local development because inventory may change.

Exit criteria:

- Production health endpoint returns OK.
- Public site can read inventory and submit a lead.
- Admin login works.
- Salesman PIN login works.
- Backup restore has been tested once.
- Current Carsforsale site scrape has been dry-run and reviewed before import.

### Phase 8 - QA, Launch, And Cleanup

Goal: verify the business workflow and remove temporary scaffolding.

QA script:

1. Create or import a vehicle.
2. View it on the public site.
3. Submit an inquiry from the vehicle page.
4. Confirm contact is created or matched.
5. Confirm a `vehicle_sale` deal appears in `new_lead`.
6. Assign it to a salesman.
7. Move through `contacted`, `appointment`, `test_drive`, `negotiation`,
   `financing`, `sold`, `delivered`.
8. Create a quote.
9. Convert/send invoice.
10. Record payment.
11. Confirm dashboard/search reflect the deal.
12. Confirm sold/delivered inventory presentation on public site.

Technical gates:

- Backend public API smokes pass.
- Existing auth, event, catalog, business-profile, quote, invoice, payment, and
  sales-auth smokes pass.
- Frontend build passes.
- Admin SPA build passes.
- Browser smoke on desktop and mobile widths for home, shop, detail, contact,
  blog, admin login, pipeline, inventory admin, salesman portal.
- Current-site scraper limited dry-run succeeds:
  `python scripts/scrape_kelley_current_site.py --dry-run --max-vehicles 2`
- `rg` confirms no removed stack or old brand strings in user-facing code.

Cleanup:

- Remove zip files from repo if they were copied into the final repo.
- Remove old scaffolds and dead docs that reference deploying drivereliable with
  Payload.
- Update README with the actual launch/deploy commands.

Exit criteria:

- Kelley Autoplex can operate the lead-to-sale workflow without touching Payload
  or the old drivereliable admin.

## Open Decisions To Confirm Early

1. **Vehicle stock number public or private?** Current recommendation: private,
   with `listingCode` public.
2. **Do sold cars stay visible?** Recommendation: detail links stay visible;
   list pages hide sold by default.
3. **Price display:** show exact cash price, call for price, or financing teaser.
4. **Lead duplicate rule:** one open deal per contact+vehicle, or allow repeated
   inquiries as notes on the same deal.
5. **Test-drive booking:** request-only in v1, or hard scheduled slots at launch.
6. **Photo storage:** hotlink/import photo URLs for v1, or upload into backend
   document storage before launch.
7. **Blog migration:** migrate existing Payload posts if real; otherwise seed new
   markdown posts.
8. **Deployment host:** selected `$17/mo` VPS, 2 cores, 4 GB RAM, 80 GB SSD,
   3 TB bandwidth, Ubuntu 24.04, plus 4 GB swap.

## Recommended First Sprint

Do these in order:

1. Phase 0 baseline repo and local boot.
2. Deletion/current-state inventory.
3. Vehicle columns and smoke tests.
4. Vehicle-sale workflow and smoke tests.
5. Public API contract implementation.

Only after those pass should the frontend Payload removal start. That keeps the
highest-risk work behind stable backend contracts instead of rewriting the site
against moving targets.
