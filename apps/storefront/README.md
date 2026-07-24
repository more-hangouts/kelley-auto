# Kelley Autoplex — Storefront

The public marketing and inventory site, built with **Next.js 15** (App Router),
**React 19**, and **Tailwind 4**. It is a read-mostly presentation layer: all
business data — vehicle inventory, lead capture, and the business profile (NAP) —
comes from the FastAPI backend's public API (`/api/public/*`). The storefront
holds no database of its own.

> The earlier Payload CMS + Prisma data layer was **removed**. There is no
> Payload config, no `prisma`, and no `/api/inquiries`/Resend relay in this app
> anymore. If you find references to those in older docs, they are historical.

Part of the root pnpm workspace — run from the repo root with
`pnpm --filter ./apps/storefront <script>`, or from this directory with
`pnpm <script>`.

## Data sources

- **Inventory** — `getInventory` / `getVehicle` in
  [`src/lib/publicApi.ts`](src/lib/publicApi.ts); the FastAPI vehicle shape is
  adapted for the view layer in [`src/lib/api.ts`](src/lib/api.ts).
- **Lead capture** — the storefront lead forms `POST /api/public/leads`, which
  creates a `vehicle_sale` deal in the CRM.
- **Business profile (NAP)** — `getBusinessProfile`.

Configure the backend location via `NEXT_PUBLIC_API_BASE_URL` and `API_BASE_URL`.

## Layout

```text
src/
├── app/                 # App Router pages (home, shop, inventory/[…], about,
│                        #   blog, financing, contact, legal, sitemap, robots)
│   ├── components/      # Public UI (navbar, footer, vehicle cards, forms, …)
│   ├── layout.tsx       # Root layout, fonts, metadata
│   └── globals.css      # Tailwind v4 + design tokens
└── lib/                 # publicApi.ts, api.ts (adapter), analytics.ts,
                         #   metaPixel.ts, nap.ts, pricing.ts, seo/utility helpers
```

## Development

```bash
pnpm --filter ./apps/storefront dev        # http://127.0.0.1:3000
```

Copy `.env.local` from the example and set `NEXT_PUBLIC_API_BASE_URL` /
`API_BASE_URL` to your running backend.

## Production build

In production the storefront runs as `kelley-public.service` (`next start` on
`127.0.0.1:3000`) behind Caddy. The production build bakes `NEXT_PUBLIC_*` values
from `.env.production` at build time.

> **Do not run the legacy in-place storefront build against production.**
> `deploy/stage-release.sh` builds and validates a versioned artifact, and
> `deploy/promote-release.sh` activates it as a real `.next` directory while
> the service is stopped. Next 15.5 must not be pointed at a symlink distDir.
> See [docs/OPERATIONS.md](../../docs/OPERATIONS.md) for promotion and rollback.

## Docs

- [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) — where the storefront sits in the system
- [docs/OPERATIONS.md](../../docs/OPERATIONS.md) — build/deploy and serving procedures
