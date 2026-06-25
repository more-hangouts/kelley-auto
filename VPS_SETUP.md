# Kelley Autoplex — VPS Provisioning, Hardening & Memory Runbook

This is the concrete, copy-pasteable runbook for **Phase 7 (MIGRATION_PLAN.md)** /
**Day 10 (SPRINT_ROADMAP.md)**. It assumes the app already builds and passes smokes
locally and that all config lives in `.env` (a Phase 0 constraint). Nothing here
should require code changes — only provisioning, DNS, TLS, and ops.

> **Do not start until the server is purchased and you have its IP + root access.**
> Everything before this is fully verifiable on localhost.

**Target server (selected):**

```text
$17/mo · 2 CPU cores · 4 GB RAM · 80 GB SSD · 3 TB bandwidth
Ubuntu 24.04 LTS · 4 GB swapfile
```

**Architecture on the box:**

```text
Caddy (auto-HTTPS, reverse proxy)
  kelleyautoplex.com        -> Next.js frontend container
  api.kelleyautoplex.com    -> FastAPI backend container
  admin.kelleyautoplex.com  -> built admin SPA (static dist)
  sales.kelleyautoplex.com  -> same admin SPA, sales host mode

Docker Compose
  frontend  backend  postgres  redis   (admin SPA served as static by Caddy)
```

---

## ⚠️ Hard constraints (read before deploying)

1. **Run the backend as ONE process / ONE worker.** The rate limiters and the two
   background workers (`workers/notifications`, `workers/daily`) live **in-process**
   in the FastAPI lifespan and store state in-process (see
   `backend/docs/ARCHITECTURE.md`). Running uvicorn/gunicorn with 2+ workers would:
   - run the background loops **once per worker** → duplicate/racy notification sends,
   - split rate-limit counters across processes → limits don't actually hold.
   Until that state is moved to Redis, deploy `uvicorn ... --workers 1`. This is also
   the cheaper memory choice on a 4 GB box.
2. **Postgres and Redis never face the public internet.** They bind to the Docker
   network / localhost only, and the firewall blocks their ports regardless.
3. **`.env` is never committed.** It lives at `/opt/kelley/.env`, mode `600`, owned by
   the deploy user. Secrets are generated on the box (commands below).
4. **Keep your first SSH session open** while changing SSH/firewall config. Test the
   new config from a *second* terminal before closing the first, or a typo locks you out.

---

## Step 0 — Provision & first contact

1. Create the VPS: Ubuntu 24.04 LTS, the selected size, in the region nearest the
   dealership. Add your SSH public key during creation if the host supports it.
2. Note the public IP. First login (host may start you as `root`):

   ```bash
   ssh root@SERVER_IP
   ```

3. If you didn't add a key at creation, add yours now (run locally):

   ```bash
   ssh-copy-id root@SERVER_IP
   ```

---

## Step 1 — Non-root sudo user

Never run the app or daily ops as root.

```bash
adduser deploy                 # set a strong password
usermod -aG sudo deploy
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy/   # copy your authorized key
```

Open a **second** terminal and confirm key login works before continuing:

```bash
ssh deploy@SERVER_IP
sudo whoami        # -> root
```

---

## Step 2 — System updates + automatic security patches

```bash
sudo apt update && sudo apt -y full-upgrade
sudo apt -y install unattended-upgrades needrestart curl ca-certificates gnupg ufw fail2ban
sudo dpkg-reconfigure -plow unattended-upgrades   # enable automatic security updates
```

Enable automatic reboot for kernel patches at a quiet hour (edit
`/etc/apt/apt.conf.d/50unattended-upgrades`):

```text
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:00";
```

Set `needrestart` to auto-restart services after library upgrades
(`/etc/needrestart/needrestart.conf`): `$nrconf{restart} = 'a';`

Timezone (matches `APP_TIMEZONE`):

```bash
sudo timedatectl set-timezone America/Chicago
timedatectl   # confirm NTP sync is active
```

---

## Step 3 — Swap (4 GB) + memory tuning

A 4 GB box building Next.js and running Postgres will touch its limits. Swap turns
an OOM **kill** into a **slowdown**.

```bash
sudo fallocate -l 4G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=4096
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Tune VM behavior — prefer RAM, fall to swap only under real pressure
(`/etc/sysctl.d/99-kelley.conf`):

```text
vm.swappiness = 10
vm.vfs_cache_pressure = 50
vm.overcommit_memory = 1
```

```bash
sudo sysctl --system
free -h        # confirm 4.0Gi swap is live
```

---

## Step 4 — Firewall (UFW)

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH          # 22 — do this BEFORE enabling
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
```

Postgres (5432) and Redis (6379) are intentionally **not** opened. Verify nothing
extra is listening on a public interface later with `sudo ss -tlnp`.

---

## Step 5 — SSH hardening

Edit `/etc/ssh/sshd_config` (or a drop-in in `/etc/ssh/sshd_config.d/`):

```text
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
ChallengeResponseAuthentication no
AllowUsers deploy
X11Forwarding no
MaxAuthTries 3
```

```bash
sudo systemctl restart ssh
```

**Test from a second terminal** (`ssh deploy@SERVER_IP`) before closing your current
session. Then harden brute-force protection with fail2ban:

```bash
sudo tee /etc/fail2ban/jail.d/sshd.local >/dev/null <<'EOF'
[sshd]
enabled = true
maxretry = 4
bantime = 1h
findtime = 10m
EOF
sudo systemctl enable --now fail2ban
sudo fail2ban-client status sshd
```

---

## Step 6 — Docker + log rotation (disk-leak guard)

Unbounded container logs are the most common way a "fine" box fills its disk and
falls over. Cap them in the daemon config.

```bash
# Official Docker repo
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt update
sudo apt -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker deploy     # log out/in to take effect
```

Daemon-wide log rotation (`/etc/docker/daemon.json`):

```json
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" },
  "live-restore": true
}
```

```bash
sudo systemctl restart docker
```

---

## Step 7 — Memory-leak mitigation (the part that keeps it alive unattended)

You can't pre-fix leaks you haven't found, but you can make them non-fatal and
self-healing. Apply all of these:

### 7a. Recycle backend workers periodically
A slow leak in a long-lived Python process is bounded by restarting it on a schedule.
Run uvicorn behind a process that recycles after N requests:

```bash
# in the backend container command — single worker per the hard constraint above
gunicorn api.server:app \
  -k uvicorn.workers.UvicornWorker \
  --workers 1 \
  --max-requests 2000 --max-requests-jitter 400 \
  --timeout 60 --graceful-timeout 30 \
  --bind 0.0.0.0:8000
```

`--max-requests` retires the worker after ~2000 requests (jittered), reclaiming any
leaked memory. Because the worker is idempotent and the background loops are
restart-safe (ARCHITECTURE.md), this is safe mid-day.

> Note: gunicorn `--max-requests` recycles the **worker**, which also restarts the
> in-process background loops. With `--workers 1` that's exactly one set of loops,
> which is correct. Do not raise `--workers`.

### 7b. Per-container memory limits + auto-restart
In `docker-compose.yml`, cap each service and let Docker restart it if the kernel
OOM-kills it. Caps must sum to **less than ~3.2 GB** to leave headroom for the OS:

```yaml
services:
  backend:
    restart: unless-stopped
    mem_limit: 1024m
    memswap_limit: 1536m     # allow some swap before OOM-kill
  frontend:
    restart: unless-stopped
    mem_limit: 768m
  postgres:
    restart: unless-stopped
    mem_limit: 1024m
  redis:
    restart: unless-stopped
    mem_limit: 256m
```

`restart: unless-stopped` turns an OOM-kill into a few seconds of downtime instead of
a dead service.

### 7c. Redis: bound its memory and evict
Redis here is a cache / rate-limit store, not a system of record — cap it and let it
evict rather than grow forever (`redis.conf` or compose command):

```text
--maxmemory 200mb --maxmemory-policy allkeys-lru --save "" --appendonly no
```

(`--save "" --appendonly no` because nothing in Redis needs to survive a restart;
that also removes a disk-growth source.)

### 7d. Postgres tuning for 4 GB
Defaults assume a bigger box. Conservative values for shared 4 GB
(`postgresql.conf` or container env):

```text
shared_buffers = 512MB
effective_cache_size = 1GB
work_mem = 16MB
maintenance_work_mem = 128MB
max_connections = 40
```

Keep `max_connections` low — each connection costs RAM, and a single uvicorn worker
needs few. Use a pool in the app, not hundreds of connections.

### 7e. earlyoom — avoid the whole-box freeze
Under true memory exhaustion Linux can thrash for minutes before the OOM killer acts.
`earlyoom` kills the worst offender early so the box stays responsive:

```bash
sudo apt -y install earlyoom
sudo systemctl enable --now earlyoom
```

### 7f. Next.js build is the spikiest moment
`next build` is the single most likely thing to OOM a 4 GB box. Options, cheapest first:
- Build with the swap already in place (Step 3) and **stop the backend during build**
  to free RAM: `docker compose stop backend && docker compose build frontend`.
- Cap Node heap so it spills to swap instead of being killed:
  `NODE_OPTIONS=--max-old-space-size=1536`.
- If it still fails, build the frontend image in CI / on your laptop and push the
  image; the VPS only pulls and runs it.

### 7g. Watch it
```bash
docker stats --no-stream     # live per-container memory
free -h                      # RAM + swap headroom
df -h                        # disk (logs/uploads/pg) — the silent killer
```
Add a tiny cron alert (optional) that emails when disk >85% or swap >75% used.

---

## Step 8 — App config & secrets

```bash
sudo mkdir -p /opt/kelley && sudo chown deploy:deploy /opt/kelley
cd /opt/kelley
git clone git@github.com:more-hangouts/kelley-auto.git .   # deploy key or HTTPS token
cp backend/.env.example backend/.env
chmod 600 backend/.env
```

Generate real secrets and paste them into `backend/.env`:

```bash
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))"
python3 -c "import secrets; print('RESCHEDULE_TOKEN_SECRET=' + secrets.token_urlsafe(48))"
python3 -c "import secrets; print('ENRICHMENT_TOKEN_SECRET=' + secrets.token_urlsafe(48))"
python3 -c "import secrets; print('QUOTE_SIGNATURE_KEY=' + secrets.token_hex(32))"
python3 -c "from cryptography.fernet import Fernet; print('INTEGRATION_TOKEN_KEYS=' + Fernet.generate_key().decode())"
```

Production `.env` values that differ from local:

```text
APP_ENV=production
DATABASE_URL=postgresql://kelley:STRONG_DB_PASS@postgres:5432/kelley
REDIS_URL=redis://redis:6379/0
RATE_LIMIT_FAIL_OPEN=false                 # flip off in prod
SESSION_COOKIE_DOMAIN=.kelleyautoplex.com
ATTRIBUTION_COOKIE_DOMAIN=.kelleyautoplex.com
CORS_ORIGINS=https://kelleyautoplex.com,https://admin.kelleyautoplex.com,https://sales.kelleyautoplex.com
DOCUMENT_STORAGE_ROOT=/var/lib/kelley/uploads
# Frontend image build/runtime:
NEXT_PUBLIC_API_BASE_URL=https://api.kelleyautoplex.com
API_BASE_URL=https://api.kelleyautoplex.com
```

Create the uploads dir the API service owns:

```bash
sudo mkdir -p /var/lib/kelley/uploads && sudo chown deploy:deploy /var/lib/kelley/uploads
```

---

## Step 9 — Bring it up

```bash
cd /opt/kelley
docker compose up -d postgres redis
# wait for postgres healthy, then:
docker compose run --rm backend python -m database.migrations.runner
docker compose run --rm backend python scripts/seed_admin.py   # interactive: first admin
docker compose up -d backend frontend
docker compose ps
```

Seed the business profile (Kelley NAP/branding) via the admin UI or seed path, and
load the first 5–10 vehicles (CSV import from Day 9).

---

## Step 10 — DNS + TLS (Caddy)

Point DNS A records at the VPS IP **before** Caddy requests certs (or ACME fails):

```text
kelleyautoplex.com        A   SERVER_IP
www.kelleyautoplex.com    A   SERVER_IP
api.kelleyautoplex.com    A   SERVER_IP
admin.kelleyautoplex.com  A   SERVER_IP
sales.kelleyautoplex.com  A   SERVER_IP
```

`Caddyfile` (Caddy fetches + renews Let's Encrypt certs automatically):

```text
kelleyautoplex.com, www.kelleyautoplex.com {
    reverse_proxy frontend:3000
}
api.kelleyautoplex.com {
    reverse_proxy backend:8000
}
admin.kelleyautoplex.com {
    root * /srv/admin
    file_server
    try_files {path} /index.html
}
sales.kelleyautoplex.com {
    root * /srv/admin
    file_server
    try_files {path} /index.html
}
```

The admin/sales SPA is one built `dist/` served statically; host-mode routing
(admin vs sales) is the app's existing hostname logic.

---

## Step 11 — Backups (before launch, not after)

```bash
sudo mkdir -p /opt/kelley/backups
```

Nightly cron (`crontab -e` as deploy):

```cron
30 3 * * *  docker compose -f /opt/kelley/docker-compose.yml exec -T postgres \
              pg_dump -U kelley kelley | gzip > /opt/kelley/backups/db-$(date +\%F).sql.gz
45 3 * * *  tar czf /opt/kelley/backups/uploads-$(date +\%F).tgz -C /var/lib/kelley uploads
0  4 * * *  find /opt/kelley/backups -mtime +14 -delete
```

- Copy at least weekly backups **off the VPS** (object storage / another host) — a
  backup on the same disk doesn't survive a disk failure.
- **Run one restore test before launch** if any real customer data exists. An
  untested backup is a guess.

Restore drill:

```bash
gunzip < backups/db-YYYY-MM-DD.sql.gz | \
  docker compose exec -T postgres psql -U kelley -d kelley_restore_test
```

---

## Step 12 — Post-deploy verification

```bash
curl https://api.kelleyautoplex.com/api/health        # -> ok
# from a browser: home, shop, a vehicle detail, submit an inquiry,
# admin login, sales PIN login, move a deal across the board.
```

Run the full Day 10 end-to-end QA script (SPRINT_ROADMAP.md) against production.

---

## Rollback

Tag images per release so you can step back instantly:

```bash
# roll back to the previous image tag
docker compose pull             # or: docker tag kelley-backend:prev kelley-backend:latest
docker compose up -d
# DB rollback: the migration runner is forward-only — restore from the nightly dump
# into a fresh DB, do not hand-edit schema under pressure.
```

Document the exact rollback command in the deploy notes the day you cut over.

---

## Security checklist (sign off before launch)

- [ ] Root SSH login disabled; password auth disabled; `AllowUsers deploy`.
- [ ] UFW: only 22/80/443 inbound; Postgres/Redis not reachable from internet
      (`sudo ss -tlnp` shows them on `127.0.0.1`/docker net only).
- [ ] fail2ban active on sshd.
- [ ] Automatic security upgrades enabled; auto-reboot window set.
- [ ] 4 GB swap live; `vm.swappiness=10`; earlyoom running.
- [ ] Docker log rotation capped (10m × 3); container `mem_limit`s set and sum < 3.2 GB.
- [ ] Backend runs `--workers 1` with `--max-requests` recycling.
- [ ] Redis `maxmemory` + eviction set; Postgres tuned for 4 GB.
- [ ] All secrets generated on-box; `backend/.env` is `600`, not in git.
- [ ] `RATE_LIMIT_FAIL_OPEN=false` in production.
- [ ] HTTPS active on all four hostnames; HTTP redirects to HTTPS.
- [ ] Cookie domains set to `.kelleyautoplex.com` deliberately (not browser default).
- [ ] CORS allows only the real frontend/admin/sales origins.
- [ ] Nightly DB + uploads backup running; one restore test passed; weekly copy offsite.
- [ ] Old Payload admin is gone/unreachable; no public page leaks internal fields.
```
