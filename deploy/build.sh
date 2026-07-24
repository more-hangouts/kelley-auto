#!/usr/bin/env bash
# Kelley Autoplex — build deploy artifacts. Run as `deploy` on the VPS.
# Does ONLY what the deploy user can do (deps, migrate, build). One-time
# privileged setup (apt, /etc, enabling units, TLS) is in deploy/README.md.
# Service restarts are NOT done here — run deploy/install.sh (as root) or
# `sudo systemctl restart kelley-backend kelley-public` after building.
#
# Usage: build.sh [--api] [--admin] [--storefront] [--all]
#   --api         install requirements into apps/api/.venv + run migrations
#   --admin       build the admin SPA via the root pnpm workspace
#   --storefront  build the Next.js storefront via the root pnpm workspace
#   --all         all of the above, in dependency order (api, admin, storefront)
set -euo pipefail

# Resolve repo root from this script's own location (deploy/ -> repo root).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="/home/deploy/.nvm/versions/node/v20.20.2/bin:/home/deploy/.local/share/pnpm:$PATH"

# NOTE: this script intentionally does NOT `git pull`. During a restructure or
# any deploy, the operator pulls the intended ref explicitly before building;
# an implicit pull here could move the tree under running services.

do_api=false
do_admin=false
do_storefront=false

usage() {
  cat >&2 <<'EOF'
Usage: build.sh [--api] [--admin] [--storefront] [--all]
  --api         install requirements into apps/api/.venv + run migrations
  --admin       build the admin SPA via the root pnpm workspace
  --storefront  build the Next.js storefront via the root pnpm workspace
  --all         all of the above, in dependency order (api, admin, storefront)
EOF
}

if [[ $# -eq 0 ]]; then
  usage
  echo "error: no target given" >&2
  exit 1
fi

for arg in "$@"; do
  case "$arg" in
    --api) do_api=true ;;
    --admin) do_admin=true ;;
    --storefront) do_storefront=true ;;
    --all) do_api=true; do_admin=true; do_storefront=true ;;
    *) usage; echo "error: unknown argument: $arg" >&2; exit 1 ;;
  esac
done

if $do_api; then
  echo "==> api: deps + migrations (apps/api/.venv)"
  cd "$ROOT/apps/api"
  .venv/bin/pip install -q -r requirements.txt
  .venv/bin/python -m database.migrations.runner
fi

if $do_admin; then
  echo "==> admin: pnpm workspace build -> apps/admin/dist"
  cd "$ROOT"
  pnpm install --frozen-lockfile
  pnpm --filter ./apps/admin build
fi

if $do_storefront; then
  echo "==> storefront: pnpm workspace build (bakes NEXT_PUBLIC_* from .env.production)"
  cd "$ROOT"
  pnpm install --frozen-lockfile
  pnpm --filter ./apps/storefront build
fi

echo "==> build done. Restart services with:"
echo "    sudo systemctl restart kelley-backend kelley-public   (or: sudo bash deploy/install.sh)"
