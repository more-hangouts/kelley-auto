# Kelley Autoplex MVP Sprint Roadmap

This document scopes a cheapest-working-solution sprint for Kelley Autoplex.
It assumes we are adapting the existing Drivereliable public site and Bellas XV
back office, not rebuilding the product from scratch.

The sprint target is an MVP where staff can upload cars, customers can view and
inquire about cars, managers can assign leads, salespeople can work their
pipeline, and appointments can be requested and confirmed.

## Sprint Rules

- One system of record: FastAPI/Postgres in `backend/`.
- Public Next.js site reads data from backend HTTP APIs only.
- No Payload or Prisma remains after the frontend rewrite.
- Reuse `catalog_items` for vehicles in v1; do not rename tables during the
  sprint.
- Reuse `events` for sales deals; add `event_type='vehicle_sale'`.
- Reuse `appointments` for test-drive/requested-visit tracking.
- Reuse Bellas admin and sales portal patterns; rebrand and hide irrelevant
  boutique/attendance surfaces instead of rebuilding.
- Keep deployment cheap: one VPS, one Postgres, one Redis, Docker Compose.
- Selected VPS target: `$17/mo`, 2 cores, 4 GB RAM, 80 GB SSD, 3 TB bandwidth.

## Efficiency Pass

This pass is the working order adjustment for a fast sprint:

1. **Brand shell first.** Before changing inventory/deal logic, make the admin
   and sales shell say Kelley Autoplex, use dealership navigation labels, and
   use the black/night admin theme. This prevents every later screenshot/manual
   check from looking like the old boutique app.
2. **Env/domain naming first.** Use Kelley names in `.env.example`, README,
   cookie domains, upload paths, and deploy docs before the VPS is configured.
   Do not carry `shopbellasxv.com`, `bellas_xv`, or `/var/lib/bellas-xv` into
   the new server.
3. **Keep internal compatibility names until their phase.** Do not rename
   `catalog_items`, `events`, `appointments`, `celebrant_*`, or old quince
   workflow columns globally at the start. Add vehicle/deal fields beside them,
   then hide old language in user-facing DTOs and UI. Full internal renames can
   wait until after the MVP is stable.
4. **Build the backend contract before deleting Payload.** Public inventory and
   lead endpoints must be stable before the Next.js rewrite. That avoids a
   frontend rewrite against moving backend shapes.
5. **Deploy manually before automating.** On the selected 4 GB VPS, get manual
   `git pull` / build / restart boring first. Add GitHub Actions only after the
   one-box deploy is proven.

## Rebrand Boundaries

Use this rule to avoid wasted work:

| Layer | Sprint behavior |
|---|---|
| User-facing public site | Must say Kelley Autoplex before launch. No Reliable/Bellas/Payload admin surfaces. |
| Admin/sales shell | Must say Kelley Autoplex early. Dark dealership theme, inventory/deal/customer language. |
| Admin/sales deep forms | Reword when touched for vehicle/deal work. Hide boutique-only sections for v1. |
| Backend public DTOs | Must use vehicle/deal/customer names only. |
| Database table names | Keep existing names during MVP unless a new field/table is needed. |
| Legacy tests/docs | May mention Bellas/quince if they are not user-facing and still protect old compatibility. |

## MVP Definition

The MVP is done when this full workflow works end to end:

1. Admin logs in.
2. Admin creates or imports a vehicle with photos.
3. Vehicle appears on the public website.
4. Customer submits an inquiry or test-drive request.
5. Backend creates or matches a contact.
6. Backend creates or updates a `vehicle_sale` deal.
7. Manager assigns the deal to a salesperson.
8. Salesperson sees the lead in their portal.
9. Staff confirms or edits the requested appointment.
10. Deal moves through `new_lead`, `contacted`, `appointment`, `test_drive`,
    `negotiation`, `financing`, `sold`, `delivered`, or `lost`.
11. Manager can create quote/invoice/payment using existing backend flows.
12. Sold/delivered inventory no longer appears in normal public listings.

## Out Of Scope For This Sprint

- Full automated test-drive slot booking from the public site.
- Managed database hosting.
- Multi-server deployment.
- Kubernetes.
- Financing integrations.
- CARFAX/auction feed integrations.
- Automated email/SMS marketing campaigns.
- Perfect final copywriting and brand polish.
- Migrating Payload rich text unless real content must be preserved.
- Full accounting or DMS integration.

## Open Decisions To Confirm Before Day 2

| Decision | Recommended v1 | Why |
|---|---|---|
| Stock number public? | Private | Public can use `listingCode`; staff keeps real stock/internal values private. |
| Sold cars visible? | Hide from lists, allow detail links | Preserves old links without advertising unavailable inventory. |
| Appointment mode | Request then staff confirms | Avoids scheduling edge cases during MVP. |
| Photo storage | Local upload folder or existing URL list | Cheapest, fastest; back it up. |
| Duplicate lead rule | One open deal per contact + vehicle | Prevents spam/repeat inquiries from flooding the board. |
| Public price | Exact cash price or call-for-price | Must be decided before public DTO and UI copy. |
| Blog | New backend markdown posts | Avoids keeping Payload only for blog. |
| Deployment host | $17/mo VPS, 2 cores, 4 GB RAM, 80 GB SSD | Cheapest selected plan that should run the full stack without constant babysitting. |

## Existing Code To Reuse

| Area | Existing files |
|---|---|
| Catalog/inventory backend | `backend/database/migrations/041_create_catalog_items.py`, `backend/services/catalog_service.py`, `backend/api/routers/catalog.py` |
| Deal pipeline backend | `backend/database/migrations/015_create_events.py`, `backend/services/event_service.py`, `backend/services/event_workflow.py`, `backend/api/routers/events.py` |
| Appointments | `backend/database/migrations/005_create_appointments.py`, `backend/services/booking_service.py`, `backend/api/routers/booking.py`, `backend/api/routers/admin_booking.py` |
| Admin inventory UI | `backend/frontend/src/pages/AdminCatalog.jsx`, `backend/frontend/src/components/CatalogDetailModal.jsx` |
| Admin pipeline UI | `backend/frontend/src/pages/Pipeline.jsx`, `backend/frontend/src/components/EventQuickViewDrawer.jsx` |
| Sales portal | `backend/frontend/src/sales/`, especially `RepDashboard.jsx`, `AppointmentsToday.jsx`, `LeadSearch.jsx`, `AppointmentDetail.jsx` |
| Public site inventory | `frontend/src/app/shop/`, `frontend/src/app/inventory/[id]/`, `frontend/src/app/components/VehicleCard.tsx` |
| Public site data layer | `frontend/src/lib/api.ts`, `frontend/src/types/vehicle.ts`, `frontend/src/types/cms.ts` |

## Day 0 - Baseline And Local Boot

Goal: prove the imported apps run and lock the Kelley naming/environment
baseline before behavior changes.

Tasks:

- Work from `kelley-auto/`, not the zip extracts.
- Confirm the selected VPS spec is reflected in docs:
  - `$17/mo`
  - 2 CPU cores
  - 4 GB RAM
  - 80 GB SSD
  - 3 TB bandwidth
  - Ubuntu 24.04
  - 4 GB swapfile
- Confirm env examples use Kelley defaults:
  - `DATABASE_URL=postgresql://kelley_user:.../kelley_autoplex`
  - local `SESSION_COOKIE_DOMAIN=` stays blank for localhost
  - production `SESSION_COOKIE_DOMAIN=.kelleyautoplex.com` is documented in
    VPS/deploy config
  - `DOCUMENT_STORAGE_ROOT=/var/lib/kelley-autoplex/uploads`
  - `CORS_ORIGINS` includes Kelley public/admin/sales domains.
- Confirm the admin shell uses Kelley Autoplex branding and the dark/night
  dealership theme.
- Confirm sales shell nav hides attendance/time-off/clock surfaces for Kelley
  v1 unless the dealership explicitly wants employee time tracking.
- Confirm env templates exist:
  - `backend/.env.example`
  - `backend/frontend/.env.example`
  - `frontend/.env.example`
- Create local env files:
  - `backend/.env`
  - `backend/frontend/.env.local`
  - `frontend/.env.local`
- Create a local Postgres database and Redis instance.
- Run backend migrations:
  - `cd backend`
  - `python -m database.migrations.runner`
- Boot the three dev servers:
  - backend API at `http://127.0.0.1:8000`
  - admin SPA at `http://127.0.0.1:5173`
  - public site at `http://127.0.0.1:3000`
- Run baseline checks:
  - `curl http://127.0.0.1:8000/api/health`
  - `cd backend && pytest tests/test_auth_smoke.py tests/test_catalog_router_smoke.py tests/test_events_smoke.py tests/test_business_profile_smoke.py tests/test_sales_auth_smoke.py`
  - `cd backend/frontend && npm run build`
  - `cd frontend && pnpm build`
- Record failures before fixing them.

Edge cases:

- If migrations fail on a fresh DB, stop and fix that first.
- If the frontend build fails due to Payload env, document it; do not remove
  Payload yet.
- If admin auth fails because no admin exists, seed an admin with the existing
  seed script before doing UI work.

Exit criteria:

- Backend health endpoint returns OK.
- Admin SPA opens.
- Admin shell says Kelley Autoplex and uses the dark dealership theme.
- Sales login/shell says Kelley Autoplex.
- Public site opens.
- Baseline failures are documented with exact command and error.

## Day 1 - Inventory Data Model

Goal: make `catalog_items` store vehicle inventory without breaking existing
catalog, quote, or invoice behavior.

Backend tasks:

- Add migration `backend/database/migrations/085_vehicle_inventory_fields.py`.
- Add fields to `catalog_items`:
  - `vin`
  - `stock_number`
  - `year`
  - `make`
  - `model`
  - `trim`
  - `mileage`
  - `transmission`
  - `fuel_type`
  - `exterior_color`
  - `interior_color`
  - `body_type`
  - `drivetrain`
  - `condition`
  - `vehicle_status`
  - `carfax_url`
  - `video_url`
  - `features_json`
- Update the ORM model in `backend/database/models.py`.
- Keep old compatibility fields:
  - `designer` can mirror make.
  - `style_number` can mirror model.
  - `color` can mirror exterior color.
  - `unit_price_cents` remains price.
  - `image_urls` remains ordered photo URLs.
- Update `backend/services/catalog_service.py` input and output handling.
- Update `backend/api/routers/catalog.py` create/list/patch responses.
- Add validation:
  - VIN length should be 17 when present.
  - Year should be plausible, for example 1980 through next calendar year.
  - Mileage should be non-negative.
  - Price should be non-negative.
  - `vehicle_status` should be constrained to known statuses.

Recommended status values:

```text
available
pending
sold
delivered
wholesale
hidden
```

Edge cases:

- Empty VIN is allowed for early manual entry, but duplicate non-empty VINs
  should be blocked.
- Duplicate stock numbers should be blocked when present.
- A vehicle can be hidden without being sold.
- A sold vehicle should not be deleted.
- Existing dress catalog rows must not break if vehicle fields are null.

Tests:

- Add or update backend smoke tests:
  - `tests/test_vehicle_inventory_smoke.py`
  - create vehicle
  - patch vehicle
  - list vehicles
  - search by make/model/VIN/stock number
  - verify old catalog tests still pass

Exit criteria:

- A vehicle can be created through the backend API.
- Existing catalog quote/invoice tests still pass.
- Public-safe vehicle DTO can be built without leaking `internal_sku`,
  `stock_number`, wholesale cost, or staff notes.

## Day 2 - Admin Vehicle Upload/Edit UI

Goal: staff can manage vehicle inventory from a Kelley-branded admin dashboard.

Admin UI tasks:

- Update `backend/frontend/src/pages/AdminCatalog.jsx`.
- Update `backend/frontend/src/components/CatalogDetailModal.jsx`.
- Relabel catalog UI to inventory language.
- Keep the admin shell in the black/night dealership theme from Day 0.
- Use vehicle/deal/customer vocabulary in every touched admin surface.
- Add vehicle form fields:
  - VIN
  - stock number
  - year
  - make
  - model
  - trim
  - mileage
  - cash price
  - exterior/interior color
  - transmission
  - fuel type
  - body type
  - drivetrain
  - condition
  - status
  - description
  - photo URLs or uploaded photo references
  - Carfax URL
  - video URL
  - features
- Add table columns useful for dealership work:
  - photo thumbnail
  - title
  - stock number
  - VIN
  - mileage
  - price
  - status
  - updated date
- Add filters:
  - status
  - make
  - year
  - price range
  - search text
- Hide or de-emphasize boutique-only fields.

Photo handling v1:

- Keep `image_urls` as an ordered list if upload handling is not ready.
- If using local upload, store files under a backend-owned uploads directory and
  save public URLs in `image_urls`.
- Do not store images inside Postgres.

Edge cases:

- Vehicle with no photo should show a clean placeholder.
- Reordering photos should preserve the first image as the public thumbnail.
- Bad image URL should not crash public pages.
- Changing status to `sold` or `delivered` should affect public listing rules.
- Admin should be able to save a draft/hidden vehicle before every public field
  is complete.

Tests:

- `cd backend/frontend && npm run build`
- Manual admin flow:
  - create vehicle
  - edit vehicle
  - add/remove/reorder photo URLs
  - switch status to hidden/sold/available
  - confirm table/filter updates

Exit criteria:

- Non-technical staff can add a vehicle without touching database/admin code.
- Admin inventory table shows vehicles in dealership language.

## Day 3 - Vehicle Sales Pipeline Model

Goal: make the existing event pipeline support car deals.

Backend tasks:

- Update `backend/services/event_workflow.py`.
- Add `VEHICLE_SALE_STATUSES`.
- Add `vehicle_sale` to `EVENT_WORKFLOWS`.
- Add migration `backend/database/migrations/086_vehicle_sale_workflow.py`.
- Widen `events.event_type` constraint to include `vehicle_sale`.
- Widen `events.status` constraint to include:
  - `new_lead`
  - `contacted`
  - `appointment`
  - `test_drive`
  - `negotiation`
  - `financing`
  - `sold`
  - `delivered`
  - `lost`
- Treat `delivered` and `lost` as terminal.
- Keep `sold` non-terminal so the team can finish paperwork and delivery.
- Add a vehicle link to the deal.

Recommended v1 vehicle link:

- Add nullable `vehicle_catalog_item_id` to `events`, referencing
  `catalog_items(id)`.
- Keep fallback notes/source metadata for leads that are not tied to a single
  vehicle.

Edge cases:

- Customer asks about no specific vehicle.
- Customer asks about a sold vehicle from an old link.
- Same customer asks about two vehicles.
- Same customer submits the same vehicle inquiry more than once.
- Assigned salesperson leaves or is deactivated.
- Deal moves backward in the board.
- Deal is lost then reopened.

Duplicate lead rule:

- Find open `vehicle_sale` by normalized contact plus vehicle.
- If one exists, append activity/note and keep the existing deal.
- If none exists, create a new deal in `new_lead`.
- If no vehicle is present, create or reuse one open general lead per contact.

Tests:

- Add `tests/test_vehicle_sale_workflow_smoke.py`.
- Cover:
  - create `vehicle_sale`
  - move through every status
  - reject invalid status
  - verify audit rows are written
  - verify `delivered` and `lost` terminal semantics
  - verify old quince workflow still works

Exit criteria:

- `getEventBoard("vehicle_sale")` returns vehicle sale columns.
- Status patching works.
- Existing event tests still pass.

## Day 4 - Public API Contracts

Goal: create stable public endpoints before touching the Next.js site.

Backend tasks:

- Add `backend/api/routers/public_site.py`.
- Add service modules if needed:
  - `backend/services/public_inventory_service.py`
  - `backend/services/public_lead_service.py`
  - `backend/services/post_service.py`
- Mount router in `backend/api/server.py` under `/api/public`.

Required endpoints:

```text
GET  /api/public/inventory
GET  /api/public/inventory/{idOrListingCode}
POST /api/public/leads
GET  /api/public/business-profile
GET  /api/public/posts
GET  /api/public/posts/{slug}
```

Inventory list behavior:

- Default to `status=available`.
- Hide `hidden`, `wholesale`, and deleted vehicles.
- Hide sold/delivered from list unless explicitly requested by staff-only APIs.
- Support query filters:
  - make
  - model
  - body type
  - fuel type
  - transmission
  - drivetrain
  - min/max price
  - min/max year
  - max mileage
  - text search
  - page/limit
  - sort

Inventory detail behavior:

- Allow available, pending, sold, and delivered detail pages.
- Return 404 for hidden/wholesale/deleted vehicles.
- Do not expose:
  - `internal_sku`
  - `stock_number` unless the public decision changes
  - wholesale price/cost
  - staff notes
  - source scrape metadata

Lead behavior:

- Normalize phone and email.
- Create or match contact with existing `contact_service`.
- Create or update `vehicle_sale`.
- Link selected vehicle when provided.
- Store source page and UTM fields.
- Store preferred day/time as requested appointment intent.
- Return generic success response.
- Do not reveal whether a contact already existed.

Spam/rate-limit behavior:

- Add honeypot field.
- Add Redis rate limit around public lead submission.
- Keep Turnstile-compatible field in the contract even if not enabled day one.

Edge cases:

- Missing phone but valid email.
- Missing email but valid phone.
- Invalid vehicle id.
- Vehicle changed to sold between page load and submit.
- Duplicate submit from double-click.
- Bot fills honeypot.
- Redis is down and `RATE_LIMIT_FAIL_OPEN` is true.
- Long message payload.

Tests:

- Add `tests/test_public_site_smoke.py`.
- Cover:
  - inventory list hides private fields
  - detail by id
  - detail by listing code
  - hidden vehicle returns 404
  - sold detail still works
  - lead creates contact and deal
  - duplicate lead appends to existing deal
  - invalid lead returns validation error
  - business profile returns public NAP

Exit criteria:

- Public API responses match the frontend DTOs.
- All public routes work without staff auth.
- Private/internal fields are absent from responses.

## Day 5 - Public Website Inventory Rewrite

Goal: make the Next.js website read inventory from FastAPI, not Payload.

Frontend tasks:

- Replace `frontend/src/lib/api.ts`.
- Add DTO types in `frontend/src/types/vehicle.ts` and `frontend/src/types/cms.ts`
  that match `/api/public`.
- Use `NEXT_PUBLIC_API_BASE_URL` for client fetches and `API_BASE_URL` for
  server fetches if needed.
- Rewire:
  - `frontend/src/app/page.tsx`
  - `frontend/src/app/shop/page.tsx`
  - `frontend/src/app/shop/ShopGrid.tsx`
  - `frontend/src/app/components/VehicleCard.tsx`
  - `frontend/src/app/components/PopularCars.tsx`
  - `frontend/src/app/components/FeaturedCars.tsx`
  - `frontend/src/app/inventory/[id]/page.tsx`
  - `frontend/src/app/inventory/[id]/ImageGallery.tsx`
- Replace Payload media helpers with plain URL helpers.
- Keep pages tolerant of missing photos, missing price, and sold vehicles.

Data mapping:

| Old Payload field | New public DTO field |
|---|---|
| `id` | `id` or `listingCode` |
| `title` | `title` |
| `cashPrice` | `priceCents` |
| `photos[].url` | `photos[]` |
| `status` | `status` |
| `description` rich text | `descriptionText` |
| `exteriorColor` | `exteriorColor` |
| `fuelType` | `fuelType` |

Edge cases:

- API down should render a clear empty state, not crash the whole page.
- Empty inventory should show a professional empty state.
- Sold vehicle detail should show sold state and no inquiry form.
- No price should show "Call for price" or chosen copy.
- Broken image URLs should fall back to placeholder styling.

Tests:

- `cd frontend && pnpm build`
- Manual browser checks:
  - home
  - shop
  - vehicle detail
  - mobile shop grid
  - sold vehicle detail
  - empty inventory

Exit criteria:

- Public inventory pages no longer call Payload.
- Website renders from FastAPI data.

## Day 6 - Public Lead And Appointment Requests

Goal: website forms create backend leads and requested appointments.

Frontend tasks:

- Rewire `frontend/src/app/inventory/[id]/InquiryForm.tsx`.
- Rewire `frontend/src/app/contact/ContactForm.tsx`.
- Delete or stop using `frontend/src/app/api/inquiries/route.ts`.
- Submit to `POST /api/public/leads`.
- Include:
  - first name
  - last name
  - phone
  - email
  - message
  - vehicle id or listing code
  - source page
  - preferred day/time text
  - UTM values if available
  - honeypot
  - marketing consent if displayed

Backend tasks:

- Ensure lead service creates:
  - contact
  - `vehicle_sale` deal
  - optional appointment/request record or appointment intent metadata
  - activity log entry
  - assignment-ready owner field
- Decide whether the first version creates an `appointments` row with status
  `pending` or stores requested time on the deal.

Recommended appointment v1:

- If the customer chose a specific requested time, create `appointments.status =
  'pending'`.
- If the customer entered free text, store it in deal notes/activity until staff
  confirms.
- Staff confirmation changes appointment to `confirmed`.

Edge cases:

- Customer submits without choosing a vehicle.
- Customer submits a sold vehicle.
- Customer changes phone formatting.
- Customer submits two forms in a row.
- Customer leaves message blank.
- Staff must see source page and vehicle context without digging.

Tests:

- Add lead integration coverage to `tests/test_public_site_smoke.py`.
- Manual flow:
  - submit vehicle inquiry
  - submit contact form
  - verify deal appears in admin pipeline
  - verify assigned salesperson can see it after assignment

Exit criteria:

- Website lead forms no longer use local Next.js API routes.
- A public lead creates a visible backend deal.

## Day 7 - Admin Pipeline And Salesperson Portal

Goal: managers and salespeople can work car deals without boutique clutter.

Admin tasks:

- Update `backend/frontend/src/pages/Pipeline.jsx`.
- Use `event_type=vehicle_sale`.
- Relabel:
  - Event -> Deal
  - Catalog -> Inventory
  - Consulted -> Contacted/Test Drive as appropriate
  - Quince/celebrant/party wording -> Customer/vehicle/deal wording
- Show useful card fields:
  - customer name
  - phone
  - vehicle title
  - status
  - assigned salesperson
  - last activity
  - next appointment/requested time
- Ensure drag/drop status changes still call `patchEventStatus`.
- Update `EventQuickViewDrawer.jsx` for vehicle/deal language.
- Add manager assignment controls if existing assignment UI is not visible.

Sales portal tasks:

- Keep PIN login.
- Update `backend/frontend/src/sales/RepDashboard.jsx`.
- Update `backend/frontend/src/sales/LeadSearch.jsx`.
- Update `backend/frontend/src/sales/AppointmentsToday.jsx`.
- Update `backend/frontend/src/sales/AppointmentDetail.jsx`.
- Hide or remove from Kelley navigation:
  - clock screen
  - attendance
  - time off
  - shift requests
  - open shifts
  - tried-on language unless repurposed
- Salesperson should be able to:
  - see assigned leads
  - see today/upcoming appointments
  - open customer/deal detail
  - add note
  - move status to contacted/appointment/test_drive/lost
  - request manager help or reassignment if existing pattern supports it

Edge cases:

- Unassigned lead should be visible to managers but not every salesperson.
- Salesperson should not see hidden admin-only surfaces.
- Salesperson should not edit another salesperson's deal unless role permits.
- Pipeline should handle old quince events still in database.
- Lost/deleted/archived deals should not clutter active board.

Tests:

- `cd backend/frontend && npm run build`
- Backend assignment tests:
  - existing sales assignment smoke tests
  - add vehicle-sale-specific test if needed
- Manual browser checks:
  - admin pipeline
  - quick view drawer
  - assign salesperson
  - sales PIN login
  - salesperson lead list
  - salesperson appointment list

Exit criteria:

- Manager can see all active vehicle deals.
- Salesperson can see and update assigned vehicle deals.
- No major boutique wording blocks the vehicle workflow.

## Day 8 - Appointments And Calendar Workflow

Goal: requested visits/test drives can be reviewed, confirmed, assigned, and
tracked.

Backend tasks:

- Decide final v1 mapping between lead requests and `appointments`.
- If using `appointments.status='pending'`, ensure admin booking routes can list
  and confirm pending vehicle appointments.
- Link appointments to `events.crm_event_id`.
- Ensure assigned salesperson is visible through appointment APIs.
- Ensure appointment status transitions work:
  - pending
  - confirmed
  - attended
  - no_show
  - cancelled
  - rescheduled
- Add or reuse audit entries for appointment notes/status changes.

Admin UI tasks:

- Update `backend/frontend/src/pages/AppointmentsCalendar.jsx`.
- Update booking/admin appointment views to use dealership language:
  - appointment
  - test drive
  - customer
  - vehicle
  - salesperson
- Add quick path from appointment to deal detail.
- Add quick path from deal to appointment detail.

Sales portal tasks:

- Show today/upcoming appointments for the logged-in salesperson.
- Let salesperson mark:
  - attended
  - no-show
  - note added
- Keep cancellation/reschedule rules manager-controlled if that is simpler.

Edge cases:

- Appointment requested outside business hours.
- Same vehicle has overlapping test-drive requests.
- Same salesperson has overlapping appointments.
- Customer no-shows.
- Customer reschedules.
- Vehicle is sold before appointment.
- Appointment exists with no vehicle.
- Appointment exists with no assigned salesperson.

Tests:

- Existing appointment/admin booking smoke tests.
- Add `tests/test_vehicle_appointment_smoke.py`.
- Manual flow:
  - submit requested appointment from public site
  - see pending appointment in admin
  - assign salesperson
  - confirm appointment
  - see in salesperson portal
  - mark attended/no-show

Exit criteria:

- Staff can manage test-drive/request appointments without editing raw data.
- Salesperson can see and act on assigned appointments.

## Day 9 - Branding, Content, Import, And Cleanup

Goal: complete the final user-facing brand pass and remove old stack coupling.

Branding tasks:

- Seed/update business profile:
  - business name
  - address
  - phone
  - email
  - website
  - hours
  - social links
  - logo paths
  - invoice/footer/payment terms
- Update public metadata:
  - `frontend/src/app/layout.tsx`
  - page titles/descriptions
  - nav/footer
- Replace remaining visible Reliable/Bellas/quince wording.
- Verify admin shell/theme/logo stayed Kelley after feature work:
  - `backend/frontend/src/theme.js`
  - `backend/frontend/src/assets/`
  - navigation labels
- Keep old Bellas/quince strings only in historical docs, migrations, tests, or
  compatibility comments that are not visible to users.

Import tasks:

- Add CSV import script, for example:
  - `backend/scripts/import_vehicle_inventory.py`
- Required columns:
  - stock_number
  - vin
  - year
  - make
  - model
  - trim
  - price
  - mileage
  - status
  - transmission
  - fuel_type
  - exterior_color
  - interior_color
  - body_type
  - drivetrain
  - description
  - photo_urls
- Include dry-run mode.
- Include duplicate handling:
  - duplicate VIN updates existing vehicle or errors based on flag
  - duplicate stock number updates existing vehicle or errors based on flag
- Seed 5-10 representative vehicles for QA.

Payload/Prisma cleanup:

- Remove unused local Next.js API routes:
  - `frontend/src/app/api/inquiries/route.ts`
  - `frontend/src/app/api/cars/route.ts`
  - obsolete admin car routes
- Delete or quarantine:
  - `frontend/src/app/(payload)/`
  - `frontend/src/payload.config.ts`
  - `frontend/src/collections/`
  - `frontend/src/lib/prisma.ts`
  - `frontend/src/lib/auth-guard.ts` if only used for Payload admin
- Remove dependencies from `frontend/package.json`:
  - `payload`
  - `@payloadcms/*`
  - `prisma`
  - `@prisma/*`
- Update lockfile with package manager.

Search gates:

```bash
rg -n "payload|@payload|@payload-config|getPayload|prisma" frontend
rg -n "Bellas|quince|quinceanera|Reliable|drivereliable" frontend backend/frontend backend/api backend/services
```

Edge cases:

- Some old brand strings may remain in historical docs or migrations; the gate
  is about user-facing code.
- Do not remove backend quince workflow if old tests still rely on it.
- Do not delete files needed by existing quote/invoice/payment flows.

Tests:

- `cd frontend && pnpm build`
- `cd backend/frontend && npm run build`
- Backend targeted smoke tests.

Exit criteria:

- Public and admin surfaces read as Kelley Autoplex.
- Frontend no longer depends on Payload or Prisma.
- Seed/import path exists for real inventory.

## Day 10 - Cheap VPS Deployment And End-To-End QA

Goal: deploy the MVP to a single cheap VPS and prove the workflow.

> **Run the full provisioning + hardening + memory runbook in
> [VPS_SETUP.md](VPS_SETUP.md)** for the exact commands (SSH hardening, swap, UFW,
> fail2ban, Docker log rotation, container memory limits, worker recycling,
> Redis/Postgres caps, earlyoom, backups, TLS). The steps below are the summary.
>
> **Constraint:** deploy the backend with `--workers 1`. The rate limiters and the
> `notifications`/`daily` background workers run in-process in the FastAPI lifespan;
> more than one worker duplicates the loops and splits rate-limit state.

Selected server:

```text
$17/mo
2 CPU cores
4 GB RAM
80 GB SSD
3 TB bandwidth
Ubuntu 24.04
4 GB swapfile
```

Recommended deployment architecture:

```text
Caddy
  kelleyautoplex.com        -> Next.js frontend
  api.kelleyautoplex.com    -> FastAPI backend
  admin.kelleyautoplex.com  -> built admin SPA
  sales.kelleyautoplex.com  -> built admin SPA, sales route/host mode

Docker Compose
  frontend
  backend
  admin-static or caddy-served dist
  postgres
  redis
```

VPS tasks:

- Provision the selected `$17/mo` VPS: 2 cores, 4 GB RAM, 80 GB SSD, 3 TB
  bandwidth.
- Add 4 GB swap.
- Install Docker and Docker Compose plugin.
- Set firewall:
  - allow SSH
  - allow HTTP/HTTPS
  - block direct Postgres/Redis from public internet
- Configure `.env` values:
  - `DATABASE_URL`
  - `REDIS_URL`
  - `SECRET_KEY`
  - token secrets
  - `CORS_ORIGINS`
  - cookie domains
  - public API base URL
  - SMTP/Twilio blank or real
- Build and start services.
- Build one heavy service at a time if memory is tight. If `next build` fails
  from memory pressure, build the frontend in CI or temporarily stop backend
  workers during build.
- Run migrations.
- Seed admin user and business profile.
- Configure domains and HTTPS.

Backup tasks:

- Nightly `pg_dump`.
- Backup upload/media directory.
- Store at least one backup off the VPS periodically.
- Run one restore test before launch if real customer data exists.

End-to-end QA script:

1. Admin login works.
2. Add vehicle.
3. Upload/add photos.
4. Vehicle appears on public shop.
5. Vehicle detail loads.
6. Submit inquiry.
7. Deal appears in `new_lead`.
8. Assign salesperson.
9. Salesperson logs in with PIN.
10. Salesperson sees assigned lead.
11. Staff confirms appointment.
12. Salesperson sees appointment.
13. Move deal to contacted.
14. Move deal to appointment.
15. Move deal to test_drive.
16. Move deal to negotiation.
17. Move deal to financing.
18. Move deal to sold.
19. Move deal to delivered.
20. Vehicle no longer appears in normal public inventory.
21. Sold vehicle detail still handles direct URL correctly.
22. Quote/invoice/payment smoke path still works.

Technical gates:

```bash
cd backend && pytest \
  tests/test_auth_smoke.py \
  tests/test_catalog_router_smoke.py \
  tests/test_events_smoke.py \
  tests/test_business_profile_smoke.py \
  tests/test_sales_auth_smoke.py \
  tests/test_public_site_smoke.py \
  tests/test_vehicle_inventory_smoke.py \
  tests/test_vehicle_sale_workflow_smoke.py

cd backend/frontend && npm run build
cd frontend && pnpm build
curl https://api.kelleyautoplex.com/api/health
```

Exit criteria:

- Production health endpoint is OK.
- Public site reads inventory from production API.
- Public lead form creates a backend deal.
- Admin dashboard works.
- Sales portal works.
- Backup job exists.
- Rollback command is documented.

## Post-Deploy Current Site Capture

Goal: after the new Kelley app is deployed and tested, capture the current
Carsforsale-powered site inventory/business data from `https://www.kelleyautoplex.com/`
so the final import has fresh VINs, specs, photos, and business details.

Timing:

- Do not run this during early local development.
- Run it after backend vehicle fields, public API, admin inventory, and VPS
  deployment are working.
- Run it before final DNS cutover or before the old Carsforsale site stops
  serving the current inventory.
- Treat the output as a migration artifact; commit the script, not the real
  scrape output unless the business explicitly wants inventory snapshots in git.

Script:

```bash
cd backend
python scripts/scrape_kelley_current_site.py \
  --output data/reports/kelley_current_site_scrape.json
```

Dry-run / limited validation:

```bash
cd backend
python scripts/scrape_kelley_current_site.py --dry-run --max-vehicles 2
```

Current site structure observed while planning:

- Home page exposes business name, phone, address, email, hours, and inventory
  category counts.
- Inventory page is `https://www.kelleyautoplex.com/cars-for-sale`.
- Vehicle detail pages use `/Inventory/Details/{id}` URLs.
- Detail pages expose useful fields such as VIN, trim, condition, engine,
  drivetrain, fuel, body type, description, features, and images.

Output shape:

```text
business_profile
vehicles[]
  source_url
  source_platform
  source_product_id
  title
  year
  make
  model
  trim
  condition
  vin
  price_cents
  mileage
  engine
  transmission
  drivetrain
  fuel_type
  body_type
  exterior_color
  interior_color
  mpg_city
  mpg_highway
  description_text
  features
  image_urls
```

Import rule:

- The scraper itself is read-only and does not write to Postgres.
- After reviewing the JSON, feed it into the vehicle import path from Day 9.
- Match existing records by VIN first, then stock/source id if VIN is missing.
- Do not overwrite staff-edited prices, statuses, or descriptions without an
  explicit update flag.

Edge cases:

- Inventory count changes while scraping.
- A detail page disappears between list and detail fetch.
- A car has no VIN, no price, no mileage, or no photos.
- Carsforsale changes markup.
- Images are lazy-loaded or hosted behind transformed URLs.
- Robots.txt disallows a path.
- The old site is already replaced by the new site.

Exit criteria:

- Script produces JSON without writing to DB.
- Failures are listed per vehicle URL instead of aborting the whole run.
- At least one limited dry-run has been tested.
- The final JSON can be reviewed before import.

## Final Launch Checklist

- [ ] DNS points to VPS.
- [ ] HTTPS active on all domains.
- [ ] Admin user created.
- [ ] Salesperson PIN users created.
- [ ] Business profile seeded.
- [ ] 5-10 real or representative vehicles loaded.
- [ ] Public inquiry form tested.
- [ ] Appointment request tested.
- [ ] Salesperson assignment tested.
- [ ] Current Carsforsale site scraper dry-run completed.
- [ ] Current Carsforsale inventory JSON reviewed before import.
- [ ] Quote/invoice/payment smoke tested.
- [ ] Daily Postgres backup configured.
- [ ] Upload/media backup configured.
- [ ] Old Payload admin inaccessible.
- [ ] Public pages have Kelley metadata.
- [ ] No public page exposes internal stock/private fields.

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---:|---:|---|
| 4 GB VPS runs out of memory during Next build | Medium | Medium | Add swap; build one service at a time; if needed build frontend in CI. |
| Public API accidentally leaks internal fields | Medium | High | Use explicit DTOs and smoke tests checking absent fields. |
| Duplicate leads flood pipeline | High | Medium | Reuse open deal per contact+vehicle and append activity. |
| Photo storage lost during VPS issue | Medium | High | Back up upload folder, not only Postgres. |
| Appointment workflow gets too complex | Medium | Medium | Use request/confirm v1 instead of true public booking. |
| Rebrand misses old wording | High | Low | Run string scans and manual UI pass. |
| Removing Payload breaks public pages late | Medium | Medium | Do not delete Payload until FastAPI-backed pages build. |
| Sales portal exposes attendance/shift features | Medium | Medium | Hide nav/routes for Kelley v1; keep code if needed for tests. |
| Old quince workflow breaks existing tests | Medium | Medium | Add `vehicle_sale` alongside `quinceanera`; do not replace it. |

## Daily Standup Template

Use this at the start and end of each sprint day:

```text
Yesterday completed:
- 

Today target:
- 

Blocked by:
- 

Commands/tests run:
- 

Demo proof:
- 

Decision needed:
- 
```

## Completion Standard

A day is not complete because code was written. A day is complete only when its
exit criteria pass, tests or manual checks are recorded, and the next day can
start without guessing what is broken.
