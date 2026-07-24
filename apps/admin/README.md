# Admin + Sales SPA

The Kelley Autoplex back office: a single-page app built with **Vite 6**,
**React 19**, **MUI 6**, **TanStack Query 5**, and **React Router 6**. One build
serves two surfaces:

- **Admin** (`admin.kelleyautoplex.com`) — full CRM/deals/inventory/scheduling
  back office.
- **Sales** (`sales.kelleyautoplex.com`) — the sales-rep surface (PIN login,
  clock-in, schedule, appointments).

Which surface renders is decided **at runtime by hostname**. `App.jsx` calls
`isSalesSubdomain()` (in `src/services/api/client.js`): a `sales.*` host mounts
the sales app, anything else mounts admin. Both are lazy-loaded, so a visitor to
one surface never downloads the other's page chunks.

Part of the root pnpm workspace — run commands from the repository root with
`pnpm --filter ./apps/admin <script>`, or from this directory with `pnpm <script>`.

## Development

```bash
pnpm --filter ./apps/admin dev        # admin surface at http://127.0.0.1:5173
```

To work on the **sales** surface locally without DNS, force the hostname branch:

```bash
VITE_FORCE_SUBDOMAIN=sales pnpm --filter ./apps/admin dev
```

Environment: `VITE_API_URL` (in `.env.local` for dev, `.env.production` for
builds) points at the API and **must end in `/api`**.

## Scripts

| Script | Purpose |
|---|---|
| `pnpm dev` | Vite dev server (HMR) |
| `pnpm build` | Production build — **see the warning below** |
| `pnpm lint` | ESLint |
| `pnpm check:api-exports` | Verify the API export surface against `EXPORTS.frozen` |
| `pnpm preview` | Serve a build locally |

## API client layout

All API calls go through `src/services/api/`:

- **`client.js`** owns the one and only `axios.create` instance. It attaches the
  CSRF header on unsafe methods (choosing the admin vs sales CSRF cookie by
  hostname) and redirects to `/login` on `401`. It also exports
  `isSalesSubdomain()`. **There must never be a second Axios instance.**
- **16 domain modules** (`auth`, `core`, `contacts`, `deals`, `booking`,
  `analytics`, `documents`, `billing`, `businessProfile`, `dashboard`,
  `inventory`, `sales`, `staff`, `attendance`, `scheduling`, `messaging`) each
  hold the endpoint functions for their domain and import `api` from `./client`.
- **`index.js`** is a barrel that re-exports every module plus the default Axios
  instance. Existing code imports from `services/api` (the barrel).
  **Do not import the barrel from inside `services/api/`** — that risks an import
  cycle. New endpoint functions go in the matching domain module.

### Export guard

The public export surface is frozen at **227 named exports + 1 default** in
`src/services/api/EXPORTS.frozen`. If you add, remove, or rename an API function,
update `EXPORTS.frozen` in the same change and verify:

```bash
pnpm --filter ./apps/admin check:api-exports
```

## Routing and bundling

- Every page-level route component is `React.lazy` + `Suspense` in `App.jsx`
  (admin) and `src/sales/SalesApp.jsx` (sales). `SalesApp` itself is lazy from
  `App.jsx`. New pages should follow the same pattern so they load on demand.
- `vite.config.js` defines `manualChunks` grouping vendor code into
  `react-vendor`, `mui`, `query`, `dnd`, and `vendor` chunks. Application source
  is never placed into a vendor chunk.

## ⚠️ Production build warning

`vite build` writes to the **default `dist/` directory, which is the live
Caddy-served admin root in production** (`apps/admin/dist`). A normal build here
publishes immediately — a broken build takes down the live admin and sales
surfaces.

**For local verification of a bundle, always build to a temporary directory:**

```bash
pnpm --filter ./apps/admin exec vite build --outDir "$(mktemp -d)" --manifest
```

Never run the default `pnpm build` (or `deploy/build.sh --admin`) unless you are
intentionally deploying inside a release window.

## Browser verification

Interactive browser verification (route rendering, lazy-load behavior, console
errors, mobile layout) is a release gate that is currently **outstanding** —
Chromium system libraries are unavailable on the VPS. Static and HTTP-level
checks (build manifest analysis, SPA-fallback and asset-fetch checks against a
temporary `vite preview`) stand in until a browser-capable environment is
available. Do not claim browser verification is complete until it has actually
run.
