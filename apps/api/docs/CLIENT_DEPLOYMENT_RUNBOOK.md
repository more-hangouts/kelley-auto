# Client Deployment Runbook

Phase H3 deliverable. Procedural recipe for provisioning a new white-label
client on the per-tenant deployment model locked in at H1. Follow it
top-to-bottom. Each section's "exit criteria" gates the next section.

The runbook assumes the operator has root access to a fresh VPS, can edit DNS
for the client's domains, and has the rebrand inputs collected per
Section 1. The H2 rebrand surface audit
([`docs/WHITE_LABEL_REBRAND_SURFACE.md`](WHITE_LABEL_REBRAND_SURFACE.md)) is
the authoritative inventory of what changes; this runbook is the
authoritative recipe for *how* and *in what order*.

**Closing acceptance:** a new same-vertical client can be provisioned from
the hardened codebase without accidentally shipping a stale or insecure
copy. The Go/No-Go Signoff in Section 8 is the operator's record that this
acceptance held for the deployment.

---

## 0. Decision Gate

Before anything else, classify the client.

| Question | Same-vertical | Different-vertical |
|---|---|---|
| Is the client another quinceañera dress boutique? | YES → proceed | — |
| Does the client need event types other than `quinceanera`? | — | YES → STOP |
| Does the client need different event statuses than `lead / consulted / sold / on_order / arrived / in_alterations / ready_for_pickup / picked_up / cancelled`? | — | YES → STOP |
| Does the client need participant roles other than `quinceanera / dama / chambelan / parent / other`? | — | YES → STOP |
| Does the fit-prep tool's style + budget vocabulary fit the client? | YES → proceed | NO → STOP |

**If any STOP fires:** this runbook cannot ship the deployment safely.
Halt and open a workflow-vocabulary generalization slice (see H2 Section 5
for the migration line-number list of CHECK constraints + service +
API + frontend touch points that the generalization must cover). Resume
this runbook only after that slice has shipped.

**If all answers are same-vertical:** record the determination in
Section 8's signoff table and proceed.

**Exit criteria:** Decision Gate result is recorded.

---

## 1. Provisioning Inputs

Collect every value below *before* starting Section 2. Fill in the
right-hand column from the client onboarding intake. Anything missing here
will block a later section.

### 1a. Identity

| Field | Value |
|---|---|
| Legal business name | |
| Display name (for receipts, may equal legal name) | |
| Street address (line 1) | |
| Street address (line 2, optional) | |
| City | |
| State (US two-letter, or per ISO) | |
| Postal code | |
| Country (ISO two-letter, default `US`) | |
| Primary phone | |
| Customer-facing email | |
| Public website URL | |
| Default tax rate (decimal, e.g. `0.0825`) | |
| Default tax name (e.g. `Sales Tax`) | |

### 1b. Domains

| Surface | Host | DNS provider |
|---|---|---|
| Marketing | | |
| Admin SPA | | |
| Sales SPA | | |
| API | | |

The four hosts should share a single eTLD+1 (e.g. `acme.com`) so the cookie
domain (D3) can be set to `.acme.com` and admin/sales/api share auth state.

### 1c. Outbound channel credentials

| Channel | Credential | Value (sensitive — store in a password manager, not in this doc) |
|---|---|---|
| SMTP | host, port, username, password, from-email | |
| SMTP | `SMTP_FROM_NAME` display name | |
| Twilio | account SID, auth token, from-number or messaging-service SID | |
| Internal notification CC list (booking copies) | comma-separated emails | |
| Meta Pixel | pixel ID, CAPI token, optional test event code | |
| Google Ads | conversion ID, label, developer token | |
| Plausible | domain | |

Any channel the client does not use stays unset. Empty env vars are fine
and degrade gracefully (per [config/settings.py](../config/settings.py)).

### 1d. Assets

| Asset | Path under `marketing/assets/` or `widgets/` | Provided? |
|---|---|---|
| Logo (color) | `marketing/assets/logo.svg` | |
| Wordmark | `marketing/assets/wordmark.svg`, `wordmark-light.svg` | |
| Hero photo (desktop) | `marketing/assets/hero-desktop.jpg`, `.webp` | |
| Hero photo (mobile) | `marketing/assets/hero-mobile.jpg`, `.webp` | |
| Booking placeholder | `marketing/assets/booking-placeholder.jpg`, `.webp` | |
| OG image (1200×630) | `marketing/assets/og-image.jpg` | |
| Booking widget logo | `widgets/bellas-logo.svg` (rename + replace) | |
| Favicon (SPA) | `frontend/public/vite.svg` (rename + replace) | |
| Business profile logo upload | uploaded via `POST /api/business-profile/logo` after Section 3 | |

### 1e. Brand tokens

| Token | Value (hex) |
|---|---|
| Primary | |
| Accent / secondary | |
| Background / cream | |
| Text / dark | |
| Divider / warm gray | |
| Optional: blush, highlight, etc. | |

### 1f. Infrastructure

| Field | Value |
|---|---|
| VPS provider + region | |
| VPS plan (CPU / RAM / disk) | |
| Backup destination (offsite path or provider) | |
| Document storage root (default `/var/lib/<client-slug>/uploads`) | |
| Provider out-of-band console URL | |
| DNS provider | |

**Exit criteria:** every field above is either filled or explicitly marked
"not applicable for this client."

---

## 2. Secure Deployment Recipe

Run every step on the fresh VPS as a non-root sudoer (the convention in
this repo is a user named per the operator, with `sudo` access).

### 2a. VPS base hardening (per Phase F)

```bash
# Disable root SSH (root login is provider-managed only)
sudo sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo sshd -t && sudo systemctl reload ssh

# Modern SSH crypto drop-in (A4)
sudo install -m 644 -o root -g root /dev/stdin /etc/ssh/sshd_config.d/10-modern-crypto.conf <<'EOF'
Ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com,aes128-gcm@openssh.com,aes256-ctr,aes192-ctr,aes128-ctr
MACs hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com,umac-128-etm@openssh.com
KexAlgorithms sntrup761x25519-sha512@openssh.com,curve25519-sha256,curve25519-sha256@libssh.org,diffie-hellman-group-exchange-sha256
GSSAPIAuthentication no
GSSAPIKeyExchange no
EOF
sudo sshd -t && sudo systemctl reload ssh

# Unattended security upgrades
sudo apt-get install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades

# UFW default-deny + allow 22/80/443
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

> **F3 is deferred for this deployment** per the parent plan. SSH stays open
> to the world; fail2ban (configured in 2g below) plus modern SSH crypto
> and the F2 scoped sudo profile are the compensating controls.

### 2b. Repo + dependencies

```bash
sudo apt-get install -y git python3 python3-venv python3-pip postgresql postgresql-contrib redis-server nginx certbot python3-certbot-nginx acl fail2ban

cd ~
git clone git@github.com:more-hangouts/bellasxv.git <client-slug>
cd <client-slug>
git checkout main
git pull

python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

> Pin to a specific commit SHA from `main`, not the moving tip, if the
> client's go-live is more than a few hours out — record the SHA in
> Section 8.

### 2c. Generate per-deployment secrets

Every secret below is unique to this deployment. **Do not copy any secret
from another deployment.**

```bash
# SECRET_KEY (JWT signing) — 64 hex chars
python3 -c "import secrets; print(secrets.token_hex(32))"

# INTEGRATION_TOKEN_KEYS (Fernet, encrypts integration_tokens columns per C1)
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# QUOTE_SIGNATURE_KEY (HMAC for signed-quote evidence per C3) — 64 hex chars
python3 -c "import secrets; print(secrets.token_hex(32))"

# RESCHEDULE_TOKEN_SECRET (booking reschedule links per G1) — 64 hex chars
python3 -c "import secrets; print(secrets.token_hex(32))"

# ENRICHMENT_TOKEN_SECRET (booking enrichment links per G1) — 64 hex chars
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### 2d. Build the `.env`

```bash
cp .env.example .env
chmod 600 .env
```

Fill in `.env` from Section 1 inputs + the 2c secrets. Required keys
(see `config/settings.py:_REQUIRED`): `DATABASE_URL`, `APP_TIMEZONE`,
`SECRET_KEY`. Plus all of:

- `APP_ENV=production`
- `LOG_LEVEL=INFO`
- `ACCESS_TOKEN_EXPIRE_MINUTES=1440` (default; override only with reason)
- `CORS_ORIGINS=https://admin.<apex>,https://sales.<apex>`
- `SESSION_COOKIE_DOMAIN=.<apex>`
- `PUBLIC_SITE_URL=https://<apex>` (used in cancellation rebooking URL — see 3c)
- `WIDGET_PUBLIC_BASE_URL=https://api.<apex>`
- `PORTAL_BASE_URL=https://<apex>`
- `BOOKING_WIDGET_ALLOWED_ORIGINS=https://<apex>,https://www.<apex>`
- `ATTRIBUTION_COOKIE_DOMAIN=.<apex>` (only if Meta Pixel is wired)
- `INTEGRATION_TOKEN_KEYS=<key from 2c>`
- `QUOTE_SIGNATURE_KEY=<key from 2c>`
- `RESCHEDULE_TOKEN_SECRET=<key from 2c>`
- `ENRICHMENT_TOKEN_SECRET=<key from 2c>`
- SMTP + Twilio + Meta + Google Ads + Plausible per 1c (set the keys you
  have; leave unused ones unset)
- `SMTP_FROM_NAME=<client display name>` (the default is `Bella's XV` —
  this is the only env that ships with a brand string)
- `BOOKING_INTERNAL_NOTIFICATION_EMAILS=<comma-separated>` per 1c
- `DOCUMENT_STORAGE_BACKEND=local`
- `DOCUMENT_STORAGE_ROOT=/var/lib/<client-slug>/uploads`
- `DOCUMENT_UPLOAD_MAX_MB=25`
- `REDIS_URL=redis://127.0.0.1:6379/0`
- `RATE_LIMIT_FAIL_OPEN=true` (B1 default; do not weaken)
- `WEBHOOK_EVENTS_RETENTION_DAYS=90` (C2 default)

### 2e. Postgres

```bash
# Create role + database
sudo -u postgres createuser --pwprompt <client_db_user>
sudo -u postgres createdb -O <client_db_user> <client_db_name>

# Lock down pg_hba.conf per F7
sudo cp ~/<client-slug>/scripts/pg_hba.conf.template /etc/postgresql/16/main/pg_hba.conf  # if a template exists; otherwise edit directly per the F7 change-log entry
sudo systemctl reload postgresql
```

Update `DATABASE_URL` in `.env` to
`postgresql://<client_db_user>:<password>@localhost:5432/<client_db_name>`.

```bash
# Run migrations
./venv/bin/python -m database.migrations.runner
```

Verify migration count matches the source-of-truth from `main`:

```bash
./venv/bin/python -c "from database.connection import SessionLocal; from sqlalchemy import text; db = SessionLocal(); print(db.execute(text('SELECT count(*) FROM applied_migrations')).scalar()); db.close()"
```

Expected count: 65 (as of the H1/H2/H3 commits — verify against
`database/migrations/` at the deployment's commit SHA).

### 2f. nginx + TLS

Copy the four nginx server-block files from this VPS to the new VPS,
substituting hostnames:

```bash
# Template files live at /etc/nginx/sites-available/ on the Bella's VPS;
# copy each to the new VPS under its client domain name and rewrite hosts.
sudo install -m 644 -o root -g root /dev/stdin /etc/nginx/sites-available/marketing.<apex> <<'EOF'
# Marketing static host — serves marketing/ folder
EOF
# Repeat for admin.<apex>, sales.<apex>, api.<apex>

# Symlink each into sites-enabled
sudo ln -sf /etc/nginx/sites-available/{marketing,admin,sales,api}.<apex> /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# nginx.conf global tightening (A3): TLSv1.2+1.3 only
sudo sed -i 's/^\s*ssl_protocols.*/    ssl_protocols TLSv1.2 TLSv1.3;/' /etc/nginx/nginx.conf

# sysctl tightening (A3)
sudo install -m 644 -o root -g root /dev/stdin /etc/sysctl.d/99-<client-slug>-hardening.conf <<'EOF'
kernel.unprivileged_userns_clone=0
EOF
sudo sysctl --system

sudo nginx -t
sudo systemctl reload nginx

# certbot — provision a cert for each host (or one SAN cert)
sudo certbot --nginx -d marketing.<apex> -d admin.<apex> -d sales.<apex> -d api.<apex>
```

### 2g. systemd unit + Redis + fail2ban

```bash
# systemd unit
sudo install -m 644 -o root -g root /dev/stdin /etc/systemd/system/<client-slug>-api.service <<EOF
[Unit]
Description=<client-slug> FastAPI Backend
After=network.target postgresql.service redis-server.service

[Service]
Type=simple
User=$USER
WorkingDirectory=/home/$USER/<client-slug>
EnvironmentFile=/home/$USER/<client-slug>/.env
ExecStart=/home/$USER/<client-slug>/venv/bin/uvicorn api.server:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# F1 sandbox drop-in
sudo mkdir -p /etc/systemd/system/<client-slug>-api.service.d
sudo install -m 644 -o root -g root /dev/stdin /etc/systemd/system/<client-slug>-api.service.d/hardening.conf <<'EOF'
[Service]
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectHostname=true
ProtectClock=true
LockPersonality=true
RestrictRealtime=true
RestrictNamespaces=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
UMask=0077
CapabilityBoundingSet=
AmbientCapabilities=
SystemCallArchitectures=native
RemoveIPC=true
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now redis-server
sudo systemctl enable --now <client-slug>-api

# F2 scoped sudo (deploy operator only — keep password sudo for everything else)
sudo install -m 0440 -o root -g root /dev/stdin /etc/sudoers.d/<operator>-<client-slug> <<EOF
Cmnd_Alias API_OPS = /usr/bin/systemctl restart <client-slug>-api.service, /usr/bin/systemctl status <client-slug>-api.service, /usr/bin/systemctl is-active <client-slug>-api.service, /usr/bin/journalctl -u <client-slug>-api.service *
Cmnd_Alias NGINX_OPS = /usr/sbin/nginx -t, /usr/bin/systemctl reload nginx.service, /usr/bin/systemctl status nginx.service, /usr/bin/systemctl is-active nginx.service
Cmnd_Alias REDIS_OPS = /usr/bin/systemctl restart redis-server.service, /usr/bin/systemctl status redis-server.service, /usr/bin/systemctl is-active redis-server.service
Cmnd_Alias SYSTEMD_OPS = /usr/bin/systemctl daemon-reload
$USER ALL=(root) NOPASSWD: API_OPS, NGINX_OPS, REDIS_OPS, SYSTEMD_OPS
EOF
sudo visudo -c

# F5 fail2ban jails (sshd + nginx)
sudo install -m 644 -o root -g root /dev/stdin /etc/fail2ban/jail.d/<client-slug>.local <<'EOF'
[nginx-botsearch]
enabled = true
backend = auto
maxretry = 2
findtime = 3600
bantime = 3600

[<client-slug>-api-auth]
enabled = true
backend = auto
filter = <client-slug>-api-auth
logpath = /var/log/nginx/access.log
maxretry = 10
findtime = 600
bantime = 900

[<client-slug>-sales-pin]
enabled = true
backend = auto
filter = <client-slug>-sales-pin
logpath = /var/log/nginx/access.log
maxretry = 10
findtime = 600
bantime = 900
EOF

# Filter files (copy from the F5 change-log on the Bella's VPS)
# /etc/fail2ban/filter.d/<client-slug>-api-auth.conf  — matches POST /api/auth/login 401
# /etc/fail2ban/filter.d/<client-slug>-sales-pin.conf — matches POST /api/sales/auth/pin 401

sudo fail2ban-client reload
sudo fail2ban-client status
```

### 2h. File permissions (F4)

```bash
sudo install -d -m 750 -o $USER -g $USER /var/lib/<client-slug>/uploads
sudo install -d -m 750 -o $USER -g $USER /home/$USER/backups

# Tighten home + repo
chmod 750 /home/$USER /home/$USER/<client-slug>

# ACL for nginx static-asset paths so www-data can traverse without
# being in the operator's group
sudo setfacl -m u:www-data:--x /home/$USER
sudo setfacl -m u:www-data:r-x /home/$USER/<client-slug>
sudo setfacl -m u:www-data:r-x /home/$USER/<client-slug>/frontend
sudo setfacl -R -m u:www-data:r-X /home/$USER/<client-slug>/frontend/dist
sudo setfacl -R -m u:www-data:r-X /home/$USER/<client-slug>/marketing
sudo setfacl -dR -m u:www-data:r-X /home/$USER/<client-slug>/frontend/dist
sudo setfacl -dR -m u:www-data:r-X /home/$USER/<client-slug>/marketing
```

### 2i. Frontend build

```bash
cd ~/<client-slug>/frontend
npm install
echo "VITE_API_URL=https://api.<apex>/api" > .env.production
npm run build
```

### 2j. Health check

```bash
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS https://api.<apex>/api/health
```

Expected: both return `{"status":"ok","database":"connected","migrations_applied":N,"timezone":"..."}`.

**Exit criteria:**
- Both health probes return 200.
- `journalctl -u <client-slug>-api -n 200 --no-pager` shows no errors.
- `fail2ban-client status` lists four active jails.
- `nginx -t` clean.
- `pg_isready` reports accepting connections.

---

## 3. Rebrand Steps

Order matters: DB first (so renders pick up the new identity), then assets,
then code edits.

### 3a. Business profile DB UPDATE

```sql
-- Run as <client_db_user> against <client_db_name>
INSERT INTO business_profile (
  id, legal_name, display_name,
  address_line1, address_line2, city, state, postal_code, country,
  phone, email, website,
  default_tax_rate, default_tax_name,
  default_invoice_terms, default_invoice_footer, default_payment_instructions,
  reminder1_enabled, reminder1_days_offset, reminder1_offset_basis,
  reminder2_enabled, reminder2_days_offset, reminder2_offset_basis,
  reminder3_enabled, reminder3_days_offset, reminder3_offset_basis,
  reminder_late_fee_cents, reminder_late_fee_pct,
  discount_presets,
  default_payment_plan_count, default_deposit_percent,
  attendance_gate_enabled, selfie_policy, selfie_retention_days,
  biweekly_anchor_date
) VALUES (
  1,
  '<Section 1a legal name>',
  '<Section 1a display name>',
  '<address 1>', '<address 2 or NULL>', '<city>', '<state>', '<zip>', 'US',
  '<phone>', '<email>', '<website>',
  <tax decimal>, '<tax name>',
  '<invoice terms or empty string>', '<footer or empty>', '<payment instructions or empty>',
  TRUE, 7, 'before_due',     -- reminder 1: 7 days before due (adjust per client)
  TRUE, 0, 'before_due',     -- reminder 2: on due date
  TRUE, 14, 'after_due',     -- reminder 3 (with late fee)
  0, 0.0,                    -- late fee cents + pct (adjust)
  '[]'::jsonb,               -- discount presets — adjust via UI later
  3, 30.00,                  -- payment plan default count + deposit %
  TRUE, 'optional', 365,     -- attendance gate + selfie policy
  NULL                       -- biweekly anchor (set later if used)
)
ON CONFLICT (id) DO UPDATE SET
  legal_name = EXCLUDED.legal_name,
  display_name = EXCLUDED.display_name,
  -- ... (mirror every column above)
  updated_at = NOW();
```

**Verify:**

```sql
SELECT legal_name, display_name, phone, email, website FROM business_profile WHERE id = 1;
```

Logo upload happens after the API is running — in 3b.

### 3b. Asset replacement

Replace every file from Section 1d. Keep the same filename + extension so
no template path edits are needed.

```bash
cd ~/<client-slug>

# Marketing assets — drop in pre-sized files
cp <staging>/logo.svg marketing/assets/logo.svg
cp <staging>/wordmark.svg marketing/assets/wordmark.svg
cp <staging>/wordmark-light.svg marketing/assets/wordmark-light.svg
cp <staging>/hero-desktop.jpg marketing/assets/hero-desktop.jpg
cp <staging>/hero-desktop.webp marketing/assets/hero-desktop.webp
cp <staging>/hero-mobile.jpg marketing/assets/hero-mobile.jpg
cp <staging>/hero-mobile.webp marketing/assets/hero-mobile.webp
cp <staging>/booking-placeholder.jpg marketing/assets/booking-placeholder.jpg
cp <staging>/booking-placeholder.webp marketing/assets/booking-placeholder.webp
cp <staging>/og-image.jpg marketing/assets/og-image.jpg

# Booking widget logo — same path
cp <staging>/widget-logo.svg widgets/bellas-logo.svg

# Favicon
cp <staging>/favicon.svg frontend/public/vite.svg
```

Business profile logo (uploaded via API after Section 2j passed):

```bash
# Log in as the client's first admin user, copy the cookie + CSRF, then:
curl -X POST https://api.<apex>/api/business-profile/logo \
  -H "Cookie: __Secure-bellas_xv_session=<jwt>" \
  -H "X-CSRF-Token: <csrf>" \
  -F "file=@<staging>/logo-for-pdf.png"
```

### 3c. Code edits per H2 hotspots

Reference [`docs/WHITE_LABEL_REBRAND_SURFACE.md`](WHITE_LABEL_REBRAND_SURFACE.md)
for the full inventory; the runbook orders the edits by file.

**Notification templates** (the largest concentration — 20+ string sites):

```bash
# Single grep-then-edit pass; every match needs review.
grep -nE "Bella's XV|Bellas XV|_BOUTIQUE_ADDRESS|_BOUTIQUE_PHONE|quinceañera|quinceanera|boutique" services/notification_templates.py
```

Edit `services/notification_templates.py`:

- Line 34: `_BOUTIQUE_ADDRESS` constant → client's full street address
- Line 35: `_BOUTIQUE_PHONE` constant → client's phone
- Lines 255-256: HTML header brand + subheader. Subheader currently reads
  "Quinceanera appointments and styling" — replace with the client's
  vertical phrase (still applies for same-vertical: keep "Quinceañera")
- Line 262: footer (autopopulates from the constants — verify after the
  edit lands)
- Every `"Bella's XV"` literal in `subject =` lines (298, 443, 500, 529) →
  client's display name
- Every `"The Bella's XV team"` (314, 414, 464, 506, 537) → client's sign-off
- Line 559, 569: SMS senders — replace `"Bella's XV:"` prefix
- Line 367: internal email subject "Quinceanera: {name}" — same-vertical
  keeps "Quinceañera"; different-vertical was blocked at the Decision Gate
- Lines 505, 511: rebooking URL hardcoded as `https://shopbellasxv.com/#book`
  — replace with `os.environ["PUBLIC_SITE_URL"] + "/#book"` or hardcode the
  client's value (parameterize in a future slice if it accumulates)
- Lines 395-426: enrichment-invitation email mentions "Boutique Experience
  Profile" — same-vertical keeps the phrase; otherwise replace

**Password reset + attendance pre-close:**

```bash
grep -n "Bella" services/password_reset.py services/attendance_pre_close.py
```

- `services/password_reset.py:71` — subject line, replace brand
- `services/attendance_pre_close.py:128,132` — subject + sign-off

**Marketing site:**

Edit `marketing/index.html`:
- Line 6: `<title>`
- Line 7: meta description
- Line 10: OG title
- Line 21: schema.org name
- Line 71: hero headline
- Line 88: JS-disabled fallback phone
- Lines 111-116: footer address + phone
- Line 132: copyright

Edit `marketing/fit-prep.html`:
- Lines 6, 7, 10: title, meta, OG
- Lines 8, 13, 14: canonical + OG URLs
- Lines 93, 96, 98-100, 122-124: breadcrumb + heading + lede + CTA

Edit `marketing/styles.css`:
- Lines 3-13: brand color tokens per Section 1e
- Lines 14-15: fonts (only if the client wants a different pair)

**Portal CSS:**

Edit `templates/portal/static/portal.css`:
- `--primary` and `--text` token values per Section 1e

**SPA chrome:**

```bash
grep -rn "Bella's XV\|Bellas XV" frontend/src frontend/index.html
```

Replace each match with the client's display name:
- `frontend/index.html:7` — `<title>`
- `frontend/src/pages/Login.jsx:58` — img alt
- `frontend/src/sales/PinLogin.jsx:170` — typography
- `frontend/src/components/DashboardLayout.jsx:73` — sidebar logo text
- `frontend/src/sales/SalesLayout.jsx:53` — app bar title
- `frontend/src/pages/BookingWidgetSettings.jsx:49,54,864` — embed-code
  snippet hardcodes `https://api.shopbellasxv.com/widgets/...` (the snippet
  the admin operator copies for the marketing site embed)
- `frontend/src/pages/SalesStaffSettings.jsx:170` — `"sales.shopbellasxv.com"`
  in copy

**Booking widget bundle:**

The widget bundles in `widgets/bellas-booking-widget.js` and
`widgets/bellas-fit-prep-tool.js` are compiled IIFE artifacts. The runtime
config from the admin Settings page already overrides most copy fields.
For values that survive as hardcoded fallbacks:

- `bellas-booking-widget.js:762` — error message phone fallback. Either
  rebuild the bundle from source (if available) with the client's phone,
  or treat the fallback as an unlikely-path acceptance.
- `bellas-fit-prep-tool.js:64` — size-chart label.
- `bellas-fit-prep-tool.js:87-107` — style/budget vocabulary. Same-vertical
  clients may accept it as-is; the Decision Gate confirmed fit at Section 0.

Rebuild the frontend after these edits:

```bash
cd ~/<client-slug>/frontend && npm run build
sudo systemctl restart <client-slug>-api
```

### 3d. Verify rebrand landed

Spot-check from the VPS:

```bash
curl -fsS https://api.<apex>/api/business-profile | jq '.legal_name, .phone, .email'
grep "<client display name>" marketing/index.html | head -3
grep "<client display name>" services/notification_templates.py | head -3
```

**Exit criteria:**
- `business_profile` row populated and `/api/business-profile` returns it.
- All assets replaced; `ls -la marketing/assets/ widgets/bellas-logo.svg frontend/public/vite.svg` shows the new files with recent mtimes.
- `grep -r "Bella's XV\|Bellas XV\|7723 Guilbeau\|210.\?670.\?5845\|shopbellasxv.com" --include="*.py" --include="*.html" --include="*.jsx" --include="*.js" --include="*.css" --exclude-dir=venv --exclude-dir=node_modules --exclude-dir=dist .` returns ONLY:
  - Comments in code (cosmetic; flag for a future docs slice)
  - Test files under `tests/` (test data, not customer-facing)
  - The `SECURITY_REMEDIATION_PLAN.md` / `WHITE_LABEL_HANDOFF.md` / `docs/WHITE_LABEL_REBRAND_SURFACE.md` history (Bella's-as-source-of-truth context)

Anything else surfaces a missed rebrand site — fix before proceeding.

---

## 4. Security Gate

Verify every Phase A through G control is present on the new deployment.
Each row gates Section 5; if any row fails, fix before running smokes.

| Phase | Control | Verification command | Expected |
|---|---|---|---|
| A1 | FastAPI ≥ 0.115 / Starlette ≥ 0.40 | `./venv/bin/pip show starlette \| grep Version` | `Version: 1.0.0` (or current) |
| A2 | Refund route requires admin scope | `grep -n require_admin_scope api/routers/payments.py` | Match on `record_refund` |
| A3 | nginx TLS ≥ 1.2 only + `kernel.unprivileged_userns_clone=0` | `grep ssl_protocols /etc/nginx/nginx.conf; sysctl kernel.unprivileged_userns_clone` | `TLSv1.2 TLSv1.3`; `= 0` |
| A4 | Modern SSH crypto drop-in | `cat /etc/ssh/sshd_config.d/10-modern-crypto.conf` | Present |
| A5 | CI actions SHA-pinned | `grep -E "uses:.*@v[0-9]" .github/workflows/*.yml` | No matches (all should be SHA-pinned) |
| B1 | Redis reachable + limiter wired | `redis-cli ping; grep RATE_LIMIT_FAIL_OPEN .env` | `PONG`; `RATE_LIMIT_FAIL_OPEN=true` |
| B2-B4 | Login + sales PIN + booking + portal token limiters | `grep -rn rate_limit api/routers/ \| wc -l` | ≥ 4 |
| C1 | `INTEGRATION_TOKEN_KEYS` set + integration_tokens encrypted | `grep INTEGRATION_TOKEN_KEYS .env; psql -c "\d integration_tokens" \| grep ciphertext` | Key set; ciphertext columns present |
| C2 | Webhook header redaction + 90-day retention | `grep WEBHOOK_EVENTS_RETENTION_DAYS .env` | `=90` (or per client) |
| C3 | `QUOTE_SIGNATURE_KEY` set | `grep QUOTE_SIGNATURE_KEY .env \| wc -c` | > 70 (64 hex + key name) |
| C4 | Audit append-only triggers | `psql -c "\df pg_temp.audit_append_only_trigger" \|\| psql -c "SELECT tgname FROM pg_trigger WHERE tgname LIKE '%audit%' LIMIT 5"` | Triggers exist on audit tables |
| D1 | Confirmation code entropy ≥ 64 bits | `grep -n token_urlsafe services/booking_service.py` | Match using ≥ 8 bytes |
| D2 | `users.token_version` column present | `psql -c "\d users" \| grep token_version` | Match |
| D3 | Cookie auth wired (this slice) | `grep -n SESSION_COOKIE_DOMAIN .env; grep -n set_session_cookies api/routers/auth.py api/routers/sales_auth.py` | Env set; matches in both routers |
| D4 | Password reset endpoint live | `curl -isS -X POST https://api.<apex>/api/auth/password-reset/request -H "Content-Type: application/json" -d '{"email":"nobody@example.com"}'` | 204 |
| D5 | PyJWT (not python-jose) | `./venv/bin/pip show python-jose 2>&1 \| head -1; ./venv/bin/pip show pyjwt \| head -1` | jose: WARNING; pyjwt: name present |
| D6 | bcrypt direct (no passlib) | `./venv/bin/pip show passlib 2>&1 \| head -1; ./venv/bin/pip show bcrypt \| head -1` | passlib: WARNING; bcrypt: name present |
| E1 | Document download forces attachment disposition | `grep -n attachment api/routers/event_documents.py` | Match |
| E2 | Magic-byte upload validation | `grep -n Pillow services/document_upload.py` | Match |
| E3 | Security headers middleware registered | `grep -n SecurityHeadersMiddleware api/server.py; curl -sI https://api.<apex>/api/health \| grep -iE "permissions-policy\|strict-transport"` | All present |
| F1 | systemd sandbox drop-in | `cat /etc/systemd/system/<client-slug>-api.service.d/hardening.conf` | Present + non-empty |
| F2 | Scoped sudo allowlist | `sudo -n -l \| grep -c "NOPASSWD: " ` | At least one Cmnd_Alias |
| F3 | (deferred — accepted residual) | — | — |
| F4 | Home + repo mode 750, uploads dir 750 | `stat -c %a /home/$USER /home/$USER/<client-slug> /var/lib/<client-slug>/uploads` | All `750` |
| F5 | fail2ban jails | `sudo fail2ban-client status \| grep "Jail list"` | sshd + nginx-botsearch + api-auth + sales-pin |
| F6 | certbot privkey 600 | `sudo stat -c %a /etc/letsencrypt/archive/<apex>/privkey1.pem` | `600` |
| F7 | pg_hba locked down | `sudo grep -E "^local\|^host" /etc/postgresql/16/main/pg_hba.conf \| head -10` | `postgres peer`, `<client_db_user> scram-sha-256`, no broad `local all all peer` |
| G1 | Booking token TTL + revocation | `psql -c "\d appointments" \| grep tokens_invalidated_at` | Column present |
| G2 | Attendance geo retention sweep | `grep -n geo_retention workers/daily.py` | Match |
| G3 | Delete-policy guardrail smoke present | `ls tests/test_delete_policy_guardrail_smoke.py` | Present |

**Exit criteria:** every row in this table is verified ✓ or explicitly
marked as the F3 deferred row. Any other failure stops the deployment.

---

## 5. Smoke Gate

Run the handoff smoke suite serially. The runner stops on the first failure
unless `--keep-going` is passed. Serial execution is mandatory per project
policy (several smokes mutate singleton/numbering rows and step on each
other in parallel). See [docs/SMOKE_TEST_AUDIT.md](SMOKE_TEST_AUDIT.md)
for the suite definition and the rationale behind what is excluded.

### 5a. Backend handoff smoke suite

```bash
cd ~/<client-slug>
PYTHON=./venv/bin/python scripts/smoke_handoff.sh
```

Pass `--keep-going` to run every smoke and report failures at the end
instead of stopping on first failure.

### 5b. Wire-level probes through nginx

```bash
# Health
curl -fsS https://api.<apex>/api/health

# Bad creds → 401, no Set-Cookie
curl -isS -X POST https://api.<apex>/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"nobody@example.com","password":"wrong"}' | grep -iE "^HTTP|set-cookie"

# CSRF middleware: cookie POST with no CSRF → 403 csrf_token_missing
curl -isS -X POST https://api.<apex>/api/auth/logout \
  --cookie "__Secure-bellas_xv_session=fake" | head -5

# CORS preflight from admin host
curl -isS -X OPTIONS https://api.<apex>/api/auth/login \
  -H "Origin: https://admin.<apex>" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type,x-csrf-token" \
  | grep -iE "access-control"

# Security headers present
curl -sI https://api.<apex>/api/health | grep -iE "permissions-policy|strict-transport|x-frame-options|nosniff|referrer-policy"
```

### 5c. Browser checks (the operator opens DevTools)

Run each against the new deployment's hosts (admin.<apex>, sales.<apex>).
Treat each row as a pass/fail gate.

| Flow | Expected behavior |
|---|---|
| `admin.<apex>` login with valid creds | 200; DevTools Application → Cookies → `api.<apex>` shows `__Secure-bellas_xv_session` (HttpOnly ✓, Secure ✓, SameSite Lax, Domain=`.<apex>`) and `__Secure-bellas_xv_csrf` (HttpOnly unchecked); Local Storage has no `bellas_xv_token` key |
| `admin.<apex>` any authenticated GET | Network tab shows no `Authorization` header |
| `admin.<apex>` any POST/PATCH/DELETE | Network tab shows `X-CSRF-Token` header matching the CSRF cookie value |
| `admin.<apex>` page refresh | Stays logged in |
| `admin.<apex>` logout | Cookies disappear; redirected to `/login` |
| `sales.<apex>` PIN login | Same checks as admin but with `__Secure-bellas_xv_sales_*` cookies |
| Public booking on `<apex>` (marketing site) | Widget loads, customer completes a real booking; confirmation email received at the test customer's mailbox |
| Customer portal link from invoice email | Opens the portal; invoice + quote render with the new client's branding |
| Admin document upload + download | Upload a PDF; download returns the same bytes with `Content-Disposition: attachment` (E1) |
| Password reset flow | Request a reset → email arrives → reset link sets a new password → old password no longer works |
| Quote sign + payment record | Customer signs a quote; admin records a payment; payment receipt PDF renders |

**Exit criteria:** all backend smokes green AND all wire probes match
expected AND all browser checks pass. Any failure here halts the
deployment.

---

## 6. Cutover and Rollback

### 6a. Pre-cutover backup snapshot

```bash
# Postgres dump
pg_dump -U <client_db_user> -h localhost -F c -f ~/backups/pre-cutover-$(date +%Y%m%d-%H%M%S).pgdump <client_db_name>

# .env snapshot (sensitive — store off-VPS in a password manager)
cp .env ~/backups/.env-pre-cutover-$(date +%Y%m%d-%H%M%S)

# Tag the deploy commit
cd ~/<client-slug>
git rev-parse HEAD | tee ~/backups/pre-cutover-commit.sha
```

### 6b. DNS cutover

Update DNS records at the client's DNS provider:

| Host | Type | Value |
|---|---|---|
| `<apex>` | A or ALIAS | <new VPS IP> |
| `www.<apex>` | CNAME | `<apex>` |
| `admin.<apex>` | A | <new VPS IP> |
| `sales.<apex>` | A | <new VPS IP> |
| `api.<apex>` | A | <new VPS IP> |

Wait for propagation (5-30 minutes typical, but the TTL on existing records
dictates worst case). Verify from at least two geographically separate
networks:

```bash
dig +short <apex> @8.8.8.8
dig +short admin.<apex> @1.1.1.1
```

### 6c. Post-cutover health

```bash
# From the operator's machine (not the VPS)
for h in <apex> www.<apex> admin.<apex> sales.<apex> api.<apex>; do
  echo "=== $h ==="
  curl -fsS -o /dev/null -w "%{http_code}\n" "https://$h/api/health" 2>/dev/null \
    || curl -fsS -o /dev/null -w "%{http_code}\n" "https://$h/"
done
```

All hosts must return 200 (api.<apex>/api/health) or 200 (the marketing/SPA
hosts serving their static index).

Browser-verify one more time:
- Admin login + DevTools cookies on the production hostnames (NOT on
  `<vps-ip>` directly — the cookie's Domain attribute won't match
  a bare IP).
- Sales PIN login the same way.
- A real test booking from the marketing site.

### 6d. Rollback procedure

If post-cutover checks fail and the issue cannot be diagnosed within
the rollback window the client has agreed to (typically 60 minutes from
cutover):

**Fast rollback (point DNS back):**

```bash
# Revert the DNS records to the previous values
# Wait for propagation
# Then stop the new VPS's API so any straggler requests fail loud
sudo systemctl stop <client-slug>-api
```

**Slow rollback (this deployment had a previous successful go-live):**

```bash
# Restore the last known-good DB snapshot
pg_restore -U <client_db_user> -h localhost -d <client_db_name> \
  --clean --if-exists ~/backups/<last-good>.pgdump

# Roll the repo back to the previous commit
cd ~/<client-slug>
git reset --hard <previous-known-good-SHA>
./venv/bin/pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..
sudo systemctl restart <client-slug>-api

# Verify health, then re-cutover DNS if needed
```

**Maintenance page (last resort):**

```bash
# Stop nginx upstream + serve a static "maintenance" page on every host
sudo install -m 644 -o root -g root /dev/stdin /etc/nginx/sites-available/maintenance <<'EOF'
server {
  listen 443 ssl;
  server_name <apex> www.<apex> admin.<apex> sales.<apex> api.<apex>;
  ssl_certificate /etc/letsencrypt/live/<apex>/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/<apex>/privkey.pem;
  return 503 "We'll be right back.";
}
EOF
sudo rm /etc/nginx/sites-enabled/*.<apex>
sudo ln -sf /etc/nginx/sites-available/maintenance /etc/nginx/sites-enabled/maintenance
sudo nginx -t && sudo systemctl reload nginx
```

**Exit criteria:** post-cutover health is green from at least two networks
AND the operator has verified the browser flow on the production hostnames
AND the rollback procedure is documented and the operator has confirmed
they can execute it.

---

## 7. Go/No-Go Signoff

| Field | Value |
|---|---|
| Client name | |
| Deployment date | |
| Operator | |
| Commit SHA | |
| Migration count (Section 2e) | |
| Decision Gate outcome (Section 0) | Same-vertical / Different-vertical |
| Provisioning inputs collected (Section 1) | ✓ / ✗ |
| Secure deployment recipe completed (Section 2) | ✓ / ✗ |
| Rebrand steps verified (Section 3) | ✓ / ✗ |
| Security Gate all rows verified (Section 4) | ✓ / ✗ — list any F-prefix exceptions |
| Backend smoke subset green (Section 5a) | count green / count run |
| Wire probes match expected (Section 5b) | ✓ / ✗ |
| Browser checks pass (Section 5c) | ✓ / ✗ |
| Pre-cutover backup snapshot taken (Section 6a) | timestamp |
| DNS cutover completed (Section 6b) | timestamp |
| Post-cutover health green (Section 6c) | ✓ / ✗ |
| Rollback procedure rehearsed | ✓ / ✗ |
| Accepted residual risks | F3 SSH exposure (per parent plan); other: |
| Sign-off | operator initials + date |

A "NO-GO" on any row means the deployment is NOT live and the client is
NOT pointed at the new VPS. Resolve the failing row, re-run the affected
section, re-sign the table.

---

## Acceptance

A new same-vertical client can be provisioned from the hardened codebase
without accidentally shipping a stale or insecure copy.

This runbook is the operator's working document. Update it in place when a
step turns out to be wrong, ambiguous, or incomplete during a real
deployment — the next deployment benefits from the correction. The
SECURITY_REMEDIATION_PLAN.md change log carries the audit trail of which
H3 commit shipped which version.
