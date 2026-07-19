#!/usr/bin/env bash
# Kelley Autoplex — install systemd units + Caddyfile and restart services.
# Run as ROOT on the VPS:  sudo bash deploy/install.sh
#
# This is the ONLY privileged deploy step. It is idempotent and safe to rerun:
# each run re-validates the Caddy config, writes a fresh timestamped backup of
# the live units/Caddyfile (keeping a one-time .orig as the immutable rollback
# copy), installs the in-repo copies, reloads systemd, restarts the two app
# units, and reloads Caddy only after its config validates.
#
# It does NOT build anything (run deploy/build.sh first) and does NOT touch the
# database. Rollback commands are printed at the end.
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "error: must run as root (use: sudo bash deploy/install.sh)" >&2
  exit 1
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_UNIT_SRC="$SRC/systemd/kelley-backend.service"
PUBLIC_UNIT_SRC="$SRC/systemd/kelley-public.service"
CADDY_SRC="$SRC/Caddyfile"

BACKEND_UNIT_DST="/etc/systemd/system/kelley-backend.service"
PUBLIC_UNIT_DST="/etc/systemd/system/kelley-public.service"
CADDY_DST="/etc/caddy/Caddyfile"

# 1. Validate all sources exist.
for f in "$BACKEND_UNIT_SRC" "$PUBLIC_UNIT_SRC" "$CADDY_SRC"; do
  [[ -f "$f" ]] || { echo "error: missing source file: $f" >&2; exit 1; }
done

# 2. Validate the candidate Caddyfile BEFORE touching the live one.
if command -v caddy >/dev/null 2>&1; then
  echo "==> validating candidate Caddyfile"
  caddy validate --config "$CADDY_SRC" --adapter caddyfile
else
  echo "error: caddy binary not found; cannot validate config" >&2
  exit 1
fi

# 3. Back up live files. Timestamped .bak per run (never clobbers a prior one);
#    a one-time .orig is the immutable pre-restructure rollback copy.
TS="$(date +%Y%m%d-%H%M%S)"
backup() {
  local live="$1"
  if [[ -f "$live" ]]; then
    cp -p "$live" "$live.$TS.bak"
    cp -pn "$live" "$live.orig" 2>/dev/null || true   # first run only; -n = no clobber
    echo "    backed up $live -> $live.$TS.bak"
  fi
}
echo "==> backing up live units + Caddyfile"
backup "$BACKEND_UNIT_DST"
backup "$PUBLIC_UNIT_DST"
backup "$CADDY_DST"

# 4. Install with explicit modes/owner.
echo "==> installing units + Caddyfile"
install -m 0644 -o root -g root "$BACKEND_UNIT_SRC" "$BACKEND_UNIT_DST"
install -m 0644 -o root -g root "$PUBLIC_UNIT_SRC"  "$PUBLIC_UNIT_DST"
install -m 0644 -o root -g root "$CADDY_SRC"        "$CADDY_DST"

# 5. Reload systemd + restart the two app units.
echo "==> daemon-reload + restart app services"
systemctl daemon-reload
systemctl restart kelley-backend kelley-public

# 6. Reload Caddy (config already validated in step 2).
echo "==> reload caddy"
systemctl reload caddy

# 7. Report.
echo "==> status"
systemctl --no-pager --lines=5 status kelley-backend kelley-public caddy || true
echo
echo "==> local health checks"
curl -fsS -o /dev/null -w 'api    /api/health -> %{http_code}\n' http://127.0.0.1:8000/api/health || echo "    api health FAILED"
curl -fsS -o /dev/null -w 'public 127.0.0.1:3000 -> %{http_code}\n' http://127.0.0.1:3000/ || echo "    storefront FAILED"

cat <<EOF

==> done. Rollback (if needed), using this run's timestamp TS=$TS:
    sudo cp -p $BACKEND_UNIT_DST.$TS.bak $BACKEND_UNIT_DST
    sudo cp -p $PUBLIC_UNIT_DST.$TS.bak $PUBLIC_UNIT_DST
    sudo cp -p $CADDY_DST.$TS.bak $CADDY_DST
    sudo systemctl daemon-reload
    sudo systemctl restart kelley-backend kelley-public
    sudo systemctl reload caddy
  (Rollback assumes the app directories are back at their old paths, i.e.
   the git mv was also reverted — units point at whatever paths they contain.)
EOF
