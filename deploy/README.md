# Kelley Autoplex — Production Serving Runbook

systemd + Caddy on a single VPS (no Docker Compose). Replaces the three dev
servers (`next dev` / `vite dev` / manual `uvicorn`) with process-managed
services behind a reverse proxy with automatic HTTPS.

> **Scope & status.** This is the original first-provisioning runbook for
> standing the box up; production is now live. Some sections below describe the
> pre-launch cutover (DNS not yet pointed, TLS not yet issued) and are retained
> as historical provisioning notes. For the current, authoritative operations
> contract — build/deploy behavior, rollback, release gates, troubleshooting —
> see **[../docs/OPERATIONS.md](../docs/OPERATIONS.md)**. Where the two differ,
> OPERATIONS.md wins.
>
> Note in particular: `deploy/build.sh` **does not** `git pull` and **does not**
> restart services (you check out the ref and restart separately), and its
> frontend builds write **in place** into the live `apps/admin/dist` and the
> running `apps/storefront/.next` — see OPERATIONS.md §5 and §20.

## Architecture

| Public host | Serves | Backed by |
|---|---|---|
| `kelleyautoplex.com`, `www.` | Public storefront | Next.js `next start` → `127.0.0.1:3000` (`kelley-public.service`) |
| `api.kelleyautoplex.com` | FastAPI (`/api/*`, incl. `/api/public/media/*` photos) | uvicorn → `127.0.0.1:8000` (`kelley-backend.service`) |
| `admin.kelleyautoplex.com` | Admin SPA (static) | Caddy `file_server` from `apps/admin/dist` |
| `sales.kelleyautoplex.com` *(optional)* | Sales surface (same dist, self-routes by host) | Caddy `file_server` |

Postgres (`:5432`) and Redis (`:6379`) stay local (Day-0 baseline). Uploaded
vehicle photos persist at `/var/lib/kelley-autoplex/uploads`.

---

## ⚠️ Two blockers before this can go live (need YOU)

> *Historical (first-provisioning).* Production is now live behind DNS + TLS on
> this box; the DNS-cutover and passwordless-sudo notes below reflect the
> initial standup and are kept for reference.

1. **No passwordless sudo on this box.** Every step under "Privileged setup"
   needs root and a password the agent doesn't have. Run them yourself.
2. **DNS does not point here yet.** `kelleyautoplex.com` currently resolves to
   `198.185.165.141`; this server is **`50.28.114.31`**, and `api.`/`admin.`
   don't resolve at all. **Caddy cannot issue TLS until DNS points here.** Set
   the records below first, let them propagate, *then* start Caddy.

---

## DNS records (set at your DNS provider)

Point everything at **`50.28.114.31`**:

```
A   kelleyautoplex.com         50.28.114.31
A   www.kelleyautoplex.com     50.28.114.31
A   api.kelleyautoplex.com     50.28.114.31
A   admin.kelleyautoplex.com   50.28.114.31
A   sales.kelleyautoplex.com   50.28.114.31   # only if using the sales host
```

Verify: `dig +short api.kelleyautoplex.com` → `50.28.114.31`.

> Moving `kelleyautoplex.com` off the current host (`198.185.165.141`) is a
> cutover — do it when you're ready for this box to be the live site.

---

## Privileged setup (run once, as a sudoer)

```bash
# 1. Persistent upload dir (owned by the app user)
sudo install -d -o deploy -g deploy /var/lib/kelley-autoplex/uploads

# 2. Install Caddy (Debian/Ubuntu official repo)
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy

# 3. Reverse proxy config
sudo cp /opt/kelley/deploy/Caddyfile /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile

# 4. systemd units for the app services
sudo cp /opt/kelley/deploy/systemd/kelley-backend.service /etc/systemd/system/
sudo cp /opt/kelley/deploy/systemd/kelley-public.service  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable kelley-backend kelley-public

# 5. Firewall: allow SSH + HTTP/HTTPS (Caddy needs 80 for ACME + 443)
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable        # ensure SSH (22) is allowed FIRST or you'll lock out
```

> Caddy obtains/renews certs automatically once DNS resolves to this box and
> 80/443 are reachable. No certbot needed.

---

## Env files (install before building)

| Template (repo) | Install to | Notes |
|---|---|---|
| `deploy/env/backend.prod.env` | merge into `/opt/kelley/apps/api/.env` | overrides only; fill `SMTP_PASSWORD` (Resend key) |
| `deploy/env/public.env.production` | `/opt/kelley/apps/storefront/.env.production` | `NEXT_PUBLIC_*` baked at build |
| `deploy/env/admin.env.production` | `/opt/kelley/apps/admin/.env.production` | `VITE_API_URL` MUST end in `/api` |

Resend: create an API key + verify the sending domain, then set
`SMTP_PASSWORD` and `SMTP_FROM_EMAIL`. Leaving `SMTP_HOST` empty keeps the
dev "null" transport (lead emails are logged, not sent).

---

## Build + first start

```bash
# As deploy: stage and validate the exact release commit without touching live
# frontend artifacts. Use build.sh --api separately for dependency/migrations.
/opt/kelley/deploy/build.sh --api
/opt/kelley/deploy/stage-release.sh --sha <full-git-sha>

# Promote during an approved window. Admin uses a symlink; storefront uses a
# real .next directory because Next 15.5 cannot serve a symlink distDir.
sudo bash /opt/kelley/deploy/promote-release.sh <full-git-sha>
```

The legacy `build.sh --admin` and `--storefront` targets write in place; do not
use them against running production services.

---

## Verify

```bash
# Local (always works once services are up)
systemctl status kelley-backend kelley-public --no-pager
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/public/inventory | head -c 200
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/shop

# Public (after DNS + TLS)
curl -fsS https://api.kelleyautoplex.com/api/health
curl -fsSI https://kelleyautoplex.com/                  # homepage 200
curl -fsSI https://kelleyautoplex.com/shop              # inventory
curl -fsSI https://admin.kelleyautoplex.com/            # admin SPA loads
# photo media (replace with a real key from /api/public/inventory photos[])
curl -fsSI https://api.kelleyautoplex.com/api/public/media/vehicles/<id>/<file>
# lead submit creates a deal + fires the staff email (check Resend/logs)
curl -fsS -X POST https://api.kelleyautoplex.com/api/public/leads \
  -H 'Content-Type: application/json' \
  -d '{"name":"QA","email":"qa@example.com","listing_code":"BVX-00001","message":"test"}'
```

---

## Operations

```bash
# Restart / stop / start
sudo systemctl restart kelley-backend kelley-public
sudo systemctl reload caddy

# Logs
journalctl -u kelley-backend -f
journalctl -u kelley-public -f
journalctl -u caddy -f

# Rebuild artifacts + run migrations (does NOT pull or restart — do those separately).
# Builds write in place into the live apps/admin/dist and running .next; see
# ../docs/OPERATIONS.md §5 and §20 before running in production.
/opt/kelley/deploy/build.sh
```

---

## Known issues / follow-ups

- **Admin API base needs `/api`.** SPA calls are relative to `VITE_API_URL`
  with no `/api` prefix; backend routes are all under `/api`. Prod is set
  correctly (`…/api`); the **dev `.env.local` is missing it**
  (`http://127.0.0.1:8000`) so admin authenticated calls 404 in dev — fix to
  `http://127.0.0.1:8000/api` and restart vite. Test admin login after.
- **Hard-coded node path** in `kelley-public.service`
  (`/home/deploy/.nvm/.../v20.20.2/bin/node`). If you upgrade node, update the
  unit or symlink a stable `node`.
- **No native PG/Redis systemd dep.** If you move them off Docker to native
  units, add `After=`/`Requires=` to `kelley-backend.service`.
- **Secrets**: `SECRET_KEY`, DB creds, and the Resend key live only in the
  server's real `.env` — never commit them. The repo holds templates only.
