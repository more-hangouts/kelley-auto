#!/usr/bin/env bash
#
# recover-storefront.sh — EMERGENCY recovery of the storefront to its working
# Phase-2 build after a failed Window B promotion.
#
# Root cause of the failure: `next start` does NOT tolerate its distDir being a
# SYMLINK (it 500s with MODULE_NOT_FOUND), which is what the release design used
# (.next-current -> releases/<sha>/storefront-next). A REAL directory works.
#
# This script restores the storefront to exactly its pre-Window-B state:
#   - moves the preserved phase2-baseline build back to apps/storefront/.next
#     (a REAL directory),
#   - reinstalls the ORIGINAL unit (no NEXT_DIST_DIR),
#   - restarts kelley-public and polls readiness.
#
# The admin side is unaffected (static files served through a symlink work fine
# and were already rolled back to phase2-baseline by promote-release.sh).
#
# Run as root:  sudo bash deploy/recover-storefront.sh
#
set -euo pipefail

ROOT="/opt/kelley"
SF="$ROOT/apps/storefront"
BASELINE="$ROOT/releases/phase2-baseline/storefront-next"
UNIT_DST="/etc/systemd/system/kelley-public.service"

echo "==> recovering storefront to the Phase-2 baseline build (real .next dir)"

# 1. Stop the failing service.
systemctl stop kelley-public || true

# 2. Remove the broken symlink pointer.
rm -f "$SF/.next-current"

# 3. Restore the baseline build to a REAL .next directory.
[[ -d "$BASELINE" ]] || { echo "error: baseline build missing at $BASELINE" >&2; exit 1; }
if [[ -e "$SF/.next" && ! -L "$SF/.next" ]]; then
  echo "    apps/storefront/.next already exists as a real dir; leaving it in place"
else
  rm -f "$SF/.next"          # clear any stale symlink
  # Move the baseline back (it was moved here from .next during first conversion).
  mv "$BASELINE" "$SF/.next"
  echo "    restored $SF/.next from phase2-baseline"
fi

# 4. Reinstall the ORIGINAL unit (no NEXT_DIST_DIR) so next uses the default
#    distDir (.next), matching the baked build. Written explicitly to avoid any
#    dependency on the current repo unit's contents.
cat > "$UNIT_DST" <<'UNIT'
[Unit]
Description=Kelley Autoplex public website (Next.js, next start)
After=network-online.target kelley-backend.service
Wants=network-online.target

[Service]
Type=simple
User=deploy
Group=deploy
WorkingDirectory=/opt/kelley/apps/storefront
Environment=NODE_ENV=production
EnvironmentFile=/opt/kelley/apps/storefront/.env.production
ExecStart=/home/deploy/.nvm/versions/node/v20.20.2/bin/node /opt/kelley/apps/storefront/node_modules/next/dist/bin/next start -p 3000 -H 127.0.0.1
Restart=always
RestartSec=3
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
UNIT
chmod 0644 "$UNIT_DST"
systemctl daemon-reload

# 5. Start + poll.
systemctl start kelley-public
INSTALL_SH_LIB=1 source "$ROOT/deploy/install.sh"
READINESS_TIMEOUT=60 wait_for_endpoint "storefront" "http://127.0.0.1:3000/" \
  || { echo "error: storefront still not ready — check journalctl -u kelley-public" >&2; exit 1; }

echo "==> storefront recovered and serving the Phase-2 build."
echo "    BUILD_ID: $(cat "$SF/.next/BUILD_ID" 2>/dev/null)"
