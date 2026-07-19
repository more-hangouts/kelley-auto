# Kelley Autoplex — public site

Next.js storefront for Kelley Autoplex. Payload CMS (in this app) owns
editorial content (blog/pages/globals); the **FastAPI backend owns the
business data** — vehicle inventory, lead capture, and the business profile.

> **Data sources (Day 5 rewrite).** Vehicles, leads, and business NAP now come
> from the FastAPI public API (`/api/public/*`), not Payload/Prisma:
> - Inventory list/detail → `getInventory` / `getVehicle` in [`src/lib/publicApi.ts`](src/lib/publicApi.ts), adapted to the legacy `PayloadVehicle` shape in [`src/lib/api.ts`](src/lib/api.ts).
> - Lead forms → `submitLead` → `POST /api/public/leads` (creates a `vehicle_sale` deal in the CRM).
> - Business NAP → `getBusinessProfile`.
> - Posts/pages/globals → still Payload.
>
> The legacy Prisma data layer and the Payload-backed `/api/inquiries` +
> Resend relay were **removed** in this rewrite. Sections below that mention
> Prisma, `prisma generate`, `/api/inquiries`, or Resend are historical.
> Configure `NEXT_PUBLIC_API_BASE_URL` / `API_BASE_URL` to point at the
> backend.

---

## Architecture

```
src/
├── app/
│   ├── (payload)/          # Payload CMS admin (content) — DO NOT TOUCH
│   │   ├── admin/          # Admin UI shell + importMap.js (custom component registry)
│   │   └── api/            # Payload REST API handler (content collections)
│   ├── components/         # Public site UI components (Navbar, Footer, cards, etc.)
│   ├── inventory/[id]/     # Car detail page (ImageGallery, InquiryForm)
│   ├── shop/               # Inventory listing page
│   ├── globals.css         # Tailwind v4 + custom design tokens
│   ├── layout.tsx          # Root layout (Inter + Bebas Neue fonts, metadata)
│   └── page.tsx            # Homepage
├── collections/
│   └── fields/
│       ├── ModelSelect.tsx # Smart model autocomplete based on selected make
│       └── VinDecoder.tsx  # VIN input with NHTSA decode button (auto-fills fields)
├── lib/
│   ├── publicApi.ts        # FastAPI public-API client (inventory/leads/business profile)
│   ├── api.ts              # Vehicle adapter (FastAPI → PayloadVehicle) + Payload content helpers
│   └── vehicle-utils.ts    # Pure view helpers (photos, display fields)
├── types/
│   └── vehicle.ts          # PayloadVehicle/media view types (consumed by components)
└── payload.config.ts       # Payload CMS configuration (content collections, DB, auth)
```

---

## Tech Stack

| Layer | Tech |
|---|---|
| Framework | Next.js 15 (App Router) |
| CMS / Admin | Payload CMS 3.x |
| Database | PostgreSQL (via `@payloadcms/db-postgres`) |
| Rich Text | Lexical editor (`@payloadcms/richtext-lexical`) |
| Styling | Tailwind CSS v4 (`@tailwindcss/postcss`) |
| Fonts | Inter (body), Bebas Neue (display) via `next/font/google` |
| Images | `next/image` with remote patterns for `drivereliablecars.com/media/**` and `drivereliablecars.com/api/media/file/**` |
| Email | Resend (`resend` package) |
| Package Manager | pnpm |

---

## Design Tokens (Tailwind v4)

Defined in `src/app/globals.css` via `@theme inline`:

| Token | Value | Use |
|---|---|---|
| `primary` | `#F76C45` | Brand orange — buttons, accents |
| `neutral-700` | `#272835` | Primary text |
| `neutral-400` | `#808897` | Secondary/muted text |
| `neutral-25` | `#F8F9FB` | Page backgrounds, card fills |
| `neutral-50` | `#EEEFF2` | Borders, dividers |

---

## Payload CMS Collections

### `vehicles`
The main inventory collection. Key fields:
- `title` (text, required) — e.g. "2019 Toyota Camry"
- `vin` (text) — rendered as `VinDecoderComponent` (auto-fills make/model/year/fuel/transmission via NHTSA API)
- `make` (select) — 43 options + Other
- `model` (text) — rendered as `ModelSelectComponent` (auto-populates based on make)
- `year` (select) — "2026" down to "1980" (string values, not numbers)
- `cashPrice` (number) — nullable, shown as "Call for price" when null
- `mileage` (number) — nullable
- `condition` — NEW | USED
- `exteriorColor` / `interiorColor` (select) + custom override fields
- `transmission` — AUTOMATIC | MANUAL
- `fuelType` — GAS | DIESEL | ELECTRIC | HYBRID
- `description` (richText) — Lexical editor; use `lexicalToText()` to extract plain text
- `status` — AVAILABLE | PENDING | SOLD (default: AVAILABLE)
- `photos` (relationship → media, hasMany) — populated at `depth=1`; first photo = hero image

### `inquiries`
Submitted from the public InquiryForm. Fields:
- `firstName`, `lastName`, `email` (required)
- `phone` (optional)
- `message` (textarea)
- `vehicle` (relationship → vehicles)
- `status` — NEW | REVIEWED | CONTACTED (default: NEW)

Public create access is open (no auth). Read/update/delete requires login.

### `media`
Standard Payload upload collection. Upload images here and attach to vehicles.
- Uploaded files served from `/media/**`
- `serverURL` is set to `NEXT_PUBLIC_SERVER_URL` so `photo.url` returns a full absolute URL

### `users`
Admin users only. Auth cookies set `secure: false` (Cloudflare Flexible SSL — HTTP between CF and server).

---

## Custom Admin Components

### `VinDecoder.tsx`
Replaces the default VIN text input. Type a 17-char VIN and click "Decode VIN" to:
- Hit the free NHTSA vPIC API (`vpic.nhtsa.dot.gov`) — no API key needed
- Auto-fill: make, model, year, fuelType, transmission
- Make matching: case-insensitive, falls back to "Other" for unknown makes

### `ModelSelect.tsx`
Replaces the model text input with a smart autocomplete that filters suggestions based on the currently selected make.

Both components are registered in `src/app/(payload)/admin/importMap.js`.

---

## Public Site — Data Fetching

`src/lib/api.ts` uses the **Payload local API** — direct DB access, no HTTP round trips:

```ts
import { getVehicles, getVehicle } from "@/lib/api"

// In a server component:
const { docs: vehicles } = await getVehicles({ limit: 100 })
const vehicle = await getVehicle(id)
```

All public pages use `export const revalidate = 60` (ISR — stale-while-revalidate, 60s).

Helper functions:
- `primaryPhoto(vehicle)` → first photo URL or null
- `allPhotos(vehicle)` → array of all photo URLs
- `displayYear(vehicle)` → year string
- `displayColor(vehicle)` → exterior color, resolves "Other" → custom value
- `isSold(vehicle)` → boolean
- `lexicalToText(richText)` → extracts plain text from Lexical JSON

---

## Inquiry Form Flow

1. User fills out form on `/inventory/[id]` page
2. `InquiryForm.tsx` (client component) POSTs to `/api/inquiries`
3. `src/app/api/inquiries/route.ts` (server):
   - Validates required fields (`firstName`, `lastName`, `email`, `vehicle`)
   - Creates inquiry record in Payload (`collection: "inquiries"`)
   - Sends email notification via Resend
4. Inquiry appears in Payload admin under Inquiries collection

---

## Server Info

- **Host**: AWS Lightsail — `bitnami@3.133.112.157`
- **SSH key**: `~/Desktop/LightsailDefaultKey-us-east-2.pem`
- **App directory**: `/var/www/reliable-cars`
- **Process manager**: PM2 (`pm2 restart reliable-cars`)
- **SSL**: Cloudflare Flexible (HTTPS to CF, HTTP from CF to server)
- **Domain**: drivereliablecars.com
- **Admin panel**: drivereliablecars.com/admin

### Deploy workflow
```bash
# On your Mac — push changes
git add <files>
git commit -m "your message"
git push

# SSH into server
ssh -i ~/Desktop/LightsailDefaultKey-us-east-2.pem bitnami@3.133.112.157

# On the server
cd /var/www/reliable-cars
git pull
pnpm install          # only if package.json changed
pnpm build
pm2 restart reliable-cars
```

### Production deploy runbook (recommended)
Use this sequence for predictable deploys on Lightsail + PM2:

```bash
cd /var/www/reliable-cars
git pull origin main

# ensure shell has env values required by build tools / scripts
set -a
. ./.env
set +a

# required because legacy API routes still import @prisma/client
pnpm exec prisma generate

pnpm build
pm2 restart reliable-cars
```

Post-deploy smoke tests:

```bash
curl -s -o /dev/null -w "home=%{http_code}\n" "http://127.0.0.1:3000/"
curl -s -o /dev/null -w "media_api=%{http_code}\n" "http://127.0.0.1:3000/api/media?depth=0&fallback-locale=null"
curl -s -o /dev/null -w "hero_bg=%{http_code}\n" "http://127.0.0.1:3000/images/hero-bg.jpg"
pm2 logs reliable-cars --lines 80
```

---

## Production incidents and fixes (March 18, 2026)

This section documents real production failures encountered on AWS Lightsail and the exact mitigations used.

| Symptom | Root cause | Fix applied | Mitigation for future client deployments |
|---|---|---|---|
| Uploads failed and `/api/media` returned 400/500 with `sizes_thumbnail_url does not exist` | Payload `media` schema expected new `sizes_*` columns not present in DB | Added missing `sizes_*` columns on `media` table | Run schema migration/patch before deploying config changes that touch upload/image sizes |
| Media upload insert failed with `null value in column "alt"` | DB had `media.alt` as `NOT NULL`, while app allows empty alt | Dropped `NOT NULL` and default on `media.alt` | Keep DB constraints aligned with Payload field optionality |
| Hero CMS/admin failed with `show_car_image does not exist` | New Hero field added in config but DB table not updated | Added `show_car_image` to `hero_content` | Apply global field schema changes before release |
| Hero draft query failed with `version_show_car_image does not exist` | Payload drafts table (`_hero_content_v`) lacked new version column | Added `version_show_car_image` to `_hero_content_v` | Include versions table in migration checklist when `drafts: true` is enabled |
| Could not alter `_hero_content_v` (`must be owner of table`) | DB user lacked ownership on versions table | Changed table owner to app DB role, then patched column | Standardize table ownership during initial provisioning |
| Frontend showed `/_next/image` 400 for Payload files | `next.config.ts` did not allow `/api/media/file/**` in `images.remotePatterns` | Added both HTTPS and localhost remote patterns for `/api/media/file/**` | Include all actual media URL patterns in Next image allowlist |
| Browser showed React `#418` / server action mismatch | Stale client assets and cached HTML across deploy boundaries | Hard refresh/incognito + Cloudflare purge | Always purge CDN cache after major deploys affecting server actions or chunk hashes |
| PM2 looped on `Could not find a production build` / missing `.next/*manifest*` | Restart attempted while build artifacts were missing/stale | Rebuilt `.next` then restarted | Stop process, build, then restart; do not restart while `.next` is absent |
| Payload CLI migration crashed under Node 20 (`undici Illegal constructor`) | Runtime/tooling mismatch in environment | Used direct SQL patches against target DB | Define and enforce Node version baseline per client |
| Upload size errors from proxy | Nginx body size too small | Set `client_max_body_size 50M` in nginx config | Include reverse-proxy upload limit in infra baseline checklist |

---

## DB patch snippets used in production

Run from `/var/www/reliable-cars` with env loaded:

```bash
set -a
. ./.env
set +a
```

### 1) Media image-size columns
```sql
ALTER TABLE "media"
  ADD COLUMN IF NOT EXISTS "sizes_thumbnail_url" TEXT,
  ADD COLUMN IF NOT EXISTS "sizes_thumbnail_width" INTEGER,
  ADD COLUMN IF NOT EXISTS "sizes_thumbnail_height" INTEGER,
  ADD COLUMN IF NOT EXISTS "sizes_thumbnail_mime_type" TEXT,
  ADD COLUMN IF NOT EXISTS "sizes_thumbnail_filesize" BIGINT,
  ADD COLUMN IF NOT EXISTS "sizes_thumbnail_filename" TEXT,
  ADD COLUMN IF NOT EXISTS "sizes_card_url" TEXT,
  ADD COLUMN IF NOT EXISTS "sizes_card_width" INTEGER,
  ADD COLUMN IF NOT EXISTS "sizes_card_height" INTEGER,
  ADD COLUMN IF NOT EXISTS "sizes_card_mime_type" TEXT,
  ADD COLUMN IF NOT EXISTS "sizes_card_filesize" BIGINT,
  ADD COLUMN IF NOT EXISTS "sizes_card_filename" TEXT,
  ADD COLUMN IF NOT EXISTS "sizes_full_url" TEXT,
  ADD COLUMN IF NOT EXISTS "sizes_full_width" INTEGER,
  ADD COLUMN IF NOT EXISTS "sizes_full_height" INTEGER,
  ADD COLUMN IF NOT EXISTS "sizes_full_mime_type" TEXT,
  ADD COLUMN IF NOT EXISTS "sizes_full_filesize" BIGINT,
  ADD COLUMN IF NOT EXISTS "sizes_full_filename" TEXT;
```

### 2) Media alt constraint alignment
```sql
ALTER TABLE "media"
  ALTER COLUMN "alt" DROP NOT NULL,
  ALTER COLUMN "alt" DROP DEFAULT;
```

### 3) Hero toggle field for primary + versions tables
```sql
ALTER TABLE "hero_content"
  ADD COLUMN IF NOT EXISTS "show_car_image" BOOLEAN;

UPDATE "hero_content"
SET "show_car_image" = TRUE
WHERE "show_car_image" IS NULL;

ALTER TABLE "hero_content"
  ALTER COLUMN "show_car_image" SET DEFAULT TRUE;

ALTER TABLE "_hero_content_v"
  ADD COLUMN IF NOT EXISTS "version_show_car_image" BOOLEAN;

UPDATE "_hero_content_v"
SET "version_show_car_image" = TRUE
WHERE "version_show_car_image" IS NULL;
```

If you get `must be owner of table _hero_content_v`, run once as postgres superuser:

```sql
ALTER TABLE public."_hero_content_v" OWNER TO reliable_admin;
```

---

## Multi-client rollout checklist

1. Pin Node version and package manager version per project.
2. Define DB ownership model so app role can evolve Payload tables (including versions tables).
3. Treat Payload config changes as DB schema changes; apply migration before app restart.
4. Keep Next image allowlist in sync with real media URL shape.
5. Add proxy upload limits (`client_max_body_size`) in base server template.
6. Standardize deploy order: pull -> env load -> prisma generate (if present) -> build -> restart -> smoke test.
7. Add post-deploy CDN cache purge policy for projects using Server Actions and ISR.
8. Keep a SQL patch log per client for emergency rollback/forensics.

---

## Environment Variables

The server has these set in the environment (managed outside of git):

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `PAYLOAD_SECRET` | Payload CMS JWT secret |
| `NEXT_PUBLIC_SERVER_URL` | Public URL — `https://drivereliablecars.com` |
| `RESEND_API_KEY` | Resend email API key |
| `EMAIL_FROM` | From address for inquiry notifications |
| `EMAIL_TO` | Destination address for inquiry notifications |

For local development, create `.env.local`:
```
DATABASE_URL=postgresql://...
PAYLOAD_SECRET=any-random-string
NEXT_PUBLIC_SERVER_URL=http://localhost:3000
RESEND_API_KEY=re_...
```

---

## Local Development

```bash
git clone git@github.com:more-hangouts/drivereliable.git
cd drivereliable
pnpm install
# create .env.local (see above)
pnpm dev
```

App runs at `http://localhost:3000`.
Admin at `http://localhost:3000/admin`.

> **Note**: The `reliable-cars/` subfolder is an old unused Next.js scaffold. Ignore it.

---

## Pages

| Route | Description |
|---|---|
| `/` | Homepage — hero, popular cars, featured cars, testimonials |
| `/shop` | Full inventory grid with status filters |
| `/inventory/[id]` | Car detail — gallery, specs, inquiry form |
| `/admin` | Payload CMS admin panel |
| `/api/inquiries` | POST endpoint — inquiry submission |
| `/api/vehicles` | Payload REST API (auto-generated) |
