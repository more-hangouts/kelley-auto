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

# Poll a single HTTP endpoint until it answers 2xx/3xx, up to a finite deadline.
# Fresh-restarted uvicorn/next take a few seconds to bind their sockets, so a
# one-shot curl races the restart; retry on a short interval instead of a fixed
# sleep and succeed the moment the endpoint is ready.
# Args: <label> <url>. Honors READINESS_TIMEOUT / READINESS_INTERVAL (seconds).
wait_for_endpoint() {
  local label="$1" url="$2"
  local timeout="${READINESS_TIMEOUT:-30}" interval="${READINESS_INTERVAL:-1}"
  local elapsed=0 code
  while true; do
    code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 3 "$url" 2>/dev/null || true)"
    if [[ "$code" =~ ^[23][0-9][0-9]$ ]]; then
      echo "    $label ready ($code) after ${elapsed}s"
      return 0
    fi
    if (( elapsed >= timeout )); then
      echo "    $label NOT ready after ${timeout}s (last: ${code:-no-response})" >&2
      return 1
    fi
    sleep "$interval"
    elapsed=$(( elapsed + interval ))
  done
}

# Allow sourcing the file to unit-test wait_for_endpoint without running any of
# the privileged install steps:  INSTALL_SH_LIB=1 source deploy/install.sh
if [[ "${INSTALL_SH_LIB:-0}" == "1" ]]; then
  return 0
fi

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

# 3b. Safety guard: the repo kelley-public.service sets NEXT_DIST_DIR=.next-current,
# which only exists once the box has been converted to staged releases (the
# first promote-release.sh run creates the symlink). Installing this unit onto
# an UNCONVERTED box would make `next start` look for a nonexistent distDir and
# crash-loop. Refuse unless the symlink exists (or the operator overrides).
if grep -q 'NEXT_DIST_DIR=.next-current' "$PUBLIC_UNIT_SRC" 2>/dev/null; then
  if [[ ! -e "/opt/kelley/apps/storefront/.next-current" && "${ALLOW_UNCONVERTED_PUBLIC_UNIT:-0}" != "1" ]]; then
    echo "error: $PUBLIC_UNIT_SRC sets NEXT_DIST_DIR=.next-current but" >&2
    echo "       /opt/kelley/apps/storefront/.next-current does not exist." >&2
    echo "       Convert to staged releases first (deploy/promote-release.sh," >&2
    echo "       which installs this unit itself), or set ALLOW_UNCONVERTED_PUBLIC_UNIT=1" >&2
    echo "       if you have created the symlink manually." >&2
    exit 1
  fi
fi

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

# 7. Report + bounded readiness polling.
echo "==> status"
systemctl --no-pager --lines=5 status kelley-backend kelley-public caddy || true
echo

echo "==> waiting for services to become ready (up to ${READINESS_TIMEOUT:-30}s each)"
ready=0
wait_for_endpoint "api    /api/health" "http://127.0.0.1:8000/api/health" || ready=1
wait_for_endpoint "public 127.0.0.1:3000" "http://127.0.0.1:3000/" || ready=1
if (( ready != 0 )); then
  echo >&2
  echo "==> readiness check FAILED — inspect with:" >&2
  echo "    systemctl status kelley-backend kelley-public --no-pager" >&2
  echo "    journalctl -u kelley-backend -u kelley-public --since '-2 min' --no-pager" >&2
  echo "    (units are installed; if this is a real failure, use the rollback below)" >&2
  exit 1
fi

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
