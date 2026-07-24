#!/usr/bin/env bash
#
# promote-release.sh — atomically point the live admin dist and storefront at a
# previously-staged, validated release, then restart the services. Privileged;
# run during a deployment window (Window B).
#
# Safety:
#   - Requires a validated release manifest; re-verifies a sample of checksums.
#   - Release ID is a strict allowlist (40-hex sha, or 'phase2-baseline').
#   - All target paths are realpath-resolved and confirmed under RELEASE_ROOT.
#   - The admin symlink swap is atomic (ln -s + mv -T = rename(2)).
#   - The storefront is STOPPED before its artifact is switched (next start
#     holds .next open), then started — a few seconds of downtime is inherent
#     and unavoidable; this is NOT an atomic switch.
#   - First conversion moves (not copies) the current real dist/.next into
#     releases/phase2-baseline/ so the pre-restructure build stays recoverable.
#   - On readiness failure after the switch, both pointers are restored.
#   - Old releases are never deleted.
#
# Usage:  sudo bash deploy/promote-release.sh <release-id>
#
# Overridable for dry-run/testing (default to production):
#   ROOT, RELEASE_ROOT, ADMIN_LINK, STOREFRONT_LINK, STOREFRONT_APP,
#   SERVICE_CMD (default: "systemctl"), SKIP_SYSTEMD_INSTALL (default: 0)
#
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "$SELF_DIR/.." && pwd)}"
RELEASE_ROOT="${RELEASE_ROOT:-$ROOT/releases}"
ADMIN_LINK="${ADMIN_LINK:-$ROOT/apps/admin/dist}"
STOREFRONT_APP="${STOREFRONT_APP:-$ROOT/apps/storefront}"
STOREFRONT_LINK="${STOREFRONT_LINK:-$STOREFRONT_APP/.next-current}"
SERVICE_CMD="${SERVICE_CMD:-systemctl}"

REL_ID="${1:-}"

fail() { echo "error: $*" >&2; exit 1; }

# --- validate release id (strict allowlist) ---
[[ -n "$REL_ID" ]] || fail "usage: promote-release.sh <release-id>"
[[ "$REL_ID" =~ ^([0-9a-f]{40}|phase2-baseline)$ ]] || fail "invalid release id: '$REL_ID'"

REL_DIR="$RELEASE_ROOT/$REL_ID"
# realpath-resolve and confirm containment under RELEASE_ROOT (traversal guard)
REL_REAL="$(realpath -e "$REL_DIR" 2>/dev/null)" || fail "release dir not found: $REL_DIR"
RR_REAL="$(realpath -e "$RELEASE_ROOT")"
case "$REL_REAL/" in
  "$RR_REAL"/*) : ;;
  *) fail "resolved release path escapes RELEASE_ROOT: $REL_REAL" ;;
esac

MANIFEST="$REL_REAL/manifest.json"
[[ -f "$MANIFEST" ]] || fail "manifest missing: $MANIFEST"
grep -q '"validated": true' "$MANIFEST" || fail "release $REL_ID is not validated"

ADMIN_SRC="$REL_REAL/admin"
STORE_SRC="$REL_REAL/storefront-next"
[[ -d "$ADMIN_SRC" ]] || fail "admin artifact missing: $ADMIN_SRC"
[[ -d "$STORE_SRC" ]] || fail "storefront artifact missing: $STORE_SRC"

# --- checksum spot-check (first 20 files) ---
echo "==> verifying checksums (sample)"
node -e '
  const fs=require("fs"), cp=require("child_process"), path=require("path");
  const [dir,manifest]=process.argv.slice(1);
  const m=JSON.parse(fs.readFileSync(manifest,"utf8"));
  const files=Object.keys(m.checksums).slice(0,20);
  for (const f of files){
    const full=path.join(dir,f);
    const got=cp.execSync("sha256sum "+JSON.stringify(full)).toString().split(" ")[0];
    if(got!==m.checksums[f]){ console.error("checksum mismatch: "+f); process.exit(1); }
  }
  console.log("    "+files.length+" sampled checksums OK");
' "$REL_REAL" "$MANIFEST"

svc() { echo "    [$SERVICE_CMD $*]"; "$SERVICE_CMD" "$@"; }

# atomic symlink swap: create a temp symlink then rename over the target
swap_symlink() {
  local target="$1" link="$2"
  local tmp="${link}.tmp.$$"
  ln -s "$target" "$tmp"
  mv -T "$tmp" "$link"
}

# --- record current pointers (for rollback + partial-failure restore) ---
PREV_ADMIN=""
PREV_STORE=""
[[ -L "$ADMIN_LINK" ]] && PREV_ADMIN="$(readlink -f "$ADMIN_LINK" || true)"
[[ -L "$STOREFRONT_LINK" ]] && PREV_STORE="$(readlink -f "$STOREFRONT_LINK" || true)"

# Write a validated manifest for a directory-only release (e.g.
# phase2-baseline) so promote/rollback accept it as a target. Checksums are
# best-effort; the marker that matters is "validated": true.
write_baseline_manifest() {
  local dir="$1"
  [[ -f "$dir/manifest.json" ]] && return 0
  ( cd "$dir" && find admin storefront-next -type f -print0 2>/dev/null | sort -z \
      | xargs -0 sha256sum > .checksums.sha256 2>/dev/null || true )
  local build_id=""
  [[ -f "$dir/storefront-next/BUILD_ID" ]] && build_id="$(cat "$dir/storefront-next/BUILD_ID")"
  cat > "$dir/manifest.json" <<EOF
{ "sha": "phase2-baseline", "build_id": "$build_id", "admin_path": "phase2-baseline/admin", "storefront_path": "phase2-baseline/storefront-next", "note": "pre-restructure Phase-2 build captured at first conversion", "validated": true }
EOF
}

# --- first conversion: current dist/.next are real dirs, not symlinks ---
# Detect it up front so the failure/restore path can distinguish it. On a
# first conversion there are NO previous symlinks to restore, so if promotion
# fails we must restore the baseline dirs, not leave the services on the
# failed release.
BASELINE_DIR="$RELEASE_ROOT/phase2-baseline"
FIRST_CONVERSION=0
[[ ! -L "$ADMIN_LINK" && -d "$ADMIN_LINK" ]] && FIRST_CONVERSION=1
[[ ! -L "$STOREFRONT_LINK" && -d "$STOREFRONT_APP/.next" ]] && FIRST_CONVERSION=1

echo "==> promoting release $REL_ID"
echo "    admin:      $ADMIN_SRC"
echo "    storefront: $STORE_SRC"

# --- stop storefront BEFORE switching its artifact ---
# Guard the stop: on first conversion the admin dir has not moved yet, so an
# aborted stop leaves the box exactly as it was. Fall through to the failure
# handler on stop failure rather than aborting under set -e.
echo "==> stopping storefront"
ok=1
svc stop kelley-public || ok=0
if [[ "$ok" -ne 1 ]]; then
  echo "error: could not stop kelley-public; aborting before any artifact move (box unchanged)" >&2
  exit 6
fi

# --- perform first-conversion moves now (storefront is stopped) ---
if [[ "$FIRST_CONVERSION" -eq 1 ]]; then
  echo "==> first conversion: preserving current Phase-2 build as phase2-baseline"
  mkdir -p "$BASELINE_DIR"
  if [[ ! -L "$ADMIN_LINK" && -d "$ADMIN_LINK" && ! -e "$BASELINE_DIR/admin" ]]; then
    mv "$ADMIN_LINK" "$BASELINE_DIR/admin"
  fi
  if [[ ! -L "$STOREFRONT_LINK" && -d "$STOREFRONT_APP/.next" && ! -e "$BASELINE_DIR/storefront-next" ]]; then
    mv "$STOREFRONT_APP/.next" "$BASELINE_DIR/storefront-next"
  fi
  write_baseline_manifest "$BASELINE_DIR"
  # On first conversion the "previous" target IS the baseline we just made.
  PREV_ADMIN="$(realpath -e "$BASELINE_DIR/admin" 2>/dev/null || true)"
  PREV_STORE="$(realpath -e "$BASELINE_DIR/storefront-next" 2>/dev/null || true)"
fi

# --- swap both pointers ---
echo "==> switching admin (atomic) + storefront pointers"
swap_symlink "$ADMIN_SRC" "$ADMIN_LINK"
swap_symlink "$STORE_SRC" "$STOREFRONT_LINK"

# --- install updated systemd/Caddy if the repo copies differ from installed ---
if [[ "${SKIP_SYSTEMD_INSTALL:-0}" != "1" ]]; then
  if ! cmp -s "$SELF_DIR/systemd/kelley-public.service" /etc/systemd/system/kelley-public.service 2>/dev/null; then
    echo "==> installing updated kelley-public.service (adds NEXT_DIST_DIR)"
    install -m 0644 "$SELF_DIR/systemd/kelley-public.service" /etc/systemd/system/kelley-public.service
    svc daemon-reload
  fi
fi

# --- restart backend once + start storefront, then poll readiness ---
# A start/restart failure must fall through to the restore block, not abort
# under `set -e` — so capture its status instead of letting it exit.
echo "==> restarting backend + starting storefront"
ok=1
svc restart kelley-backend || ok=0
svc start kelley-public || ok=0

INSTALL_SH_LIB=1 source "$SELF_DIR/install.sh"
if [[ "$ok" -eq 1 ]]; then
  READINESS_TIMEOUT=45 wait_for_endpoint "api" "http://127.0.0.1:8000/api/health" || ok=0
  READINESS_TIMEOUT=60 wait_for_endpoint "storefront" "http://127.0.0.1:3000/" || ok=0
fi

if [[ "$ok" -ne 1 ]]; then
  echo "error: readiness failed after promotion — restoring previous pointers" >&2
  svc stop kelley-public || true
  [[ -n "$PREV_ADMIN" ]] && swap_symlink "$PREV_ADMIN" "$ADMIN_LINK"
  [[ -n "$PREV_STORE" ]] && swap_symlink "$PREV_STORE" "$STOREFRONT_LINK"
  svc restart kelley-backend || true
  svc start kelley-public || true
  echo "error: promotion rolled back to previous pointers; investigate before retrying" >&2
  exit 6
fi

echo "==> promotion complete."
echo "    active release:   $REL_ID"
echo "    previous admin:   ${PREV_ADMIN:-<first conversion / none>}"
echo "    previous store:   ${PREV_STORE:-<first conversion / none>}"
if [[ -n "$PREV_ADMIN" || -n "$PREV_STORE" ]]; then
  prev_id="$(basename "$(dirname "${PREV_ADMIN:-$PREV_STORE}")")"
  echo "    rollback with:    sudo bash deploy/rollback-release.sh $prev_id"
else
  echo "    rollback with:    sudo bash deploy/rollback-release.sh phase2-baseline"
fi
