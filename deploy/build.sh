#!/usr/bin/env bash
# Kelley Autoplex — build + deploy artifacts. Run as `deploy` on the VPS.
# This does ONLY what the deploy user can do (pull, deps, migrate, build,
# restart). One-time privileged setup (apt, /etc, enabling units, TLS) is in
# deploy/README.md and is NOT performed here.
set -euo pipefail

ROOT=/opt/kelley
export PATH="/home/deploy/.nvm/versions/node/v20.20.2/bin:/home/deploy/.local/share/pnpm:$PATH"

echo "==> git pull"
git -C "$ROOT" pull --ff-only

echo "==> backend deps + migrations"
cd "$ROOT/backend"
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python -m database.migrations.runner

echo "==> admin SPA build -> backend/frontend/dist (needs .env.production with VITE_API_URL)"
cd "$ROOT/backend/frontend"
npm ci --no-audit --no-fund 2>/dev/null || npm install
npm run build

echo "==> public site build (Next; bakes NEXT_PUBLIC_API_BASE_URL from .env.production)"
cd "$ROOT/frontend"
pnpm install --frozen-lockfile
pnpm build

echo "==> restart services (units must already be installed; see README)"
sudo systemctl restart kelley-backend kelley-public \
  || echo "   (could not restart — install units + sudo access per README)"

echo "==> done. Verify:  systemctl status kelley-backend kelley-public"
