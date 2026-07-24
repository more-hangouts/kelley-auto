#!/usr/bin/env bash
#
# promote-release.sh — promote a previously staged, validated frontend release.
# Privileged; run during a deployment window.
#
# Safety:
#   - Requires a validated release manifest; re-verifies a sample of checksums.
#   - Release ID is a strict allowlist (40-hex sha, or 'phase2-baseline').
#   - All target paths are realpath-resolved and confirmed under RELEASE_ROOT.
#   - The admin symlink swap is atomic (ln -s + mv -T = rename(2)).
#   - The storefront candidate is copied to a REAL directory before downtime.
#     Next 15.5 cannot serve request-time chunks from a symlink distDir.
#   - The storefront is STOPPED, its real .next is renamed aside, and the real
#     candidate is renamed into place. This is recoverable, not atomic.
#   - On readiness failure, admin and the previous real .next are restored.
#   - Old releases are never deleted.
#
# Usage:
#   sudo bash deploy/promote-release.sh <release-id> [--skip-backend-restart]
#
# Overridable for dry-run/testing (default to production):
#   ROOT, RELEASE_ROOT, ADMIN_LINK, STOREFRONT_APP, STORE_STATE,
#   SERVICE_CMD (default: "systemctl"), SKIP_SYSTEMD_INSTALL (default: 0)
#
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "$SELF_DIR/.." && pwd)}"
RELEASE_ROOT="${RELEASE_ROOT:-$ROOT/releases}"
ADMIN_LINK="${ADMIN_LINK:-$ROOT/apps/admin/dist}"
STOREFRONT_APP="${STOREFRONT_APP:-$ROOT/apps/storefront}"
STOREFRONT_LIVE="${STOREFRONT_LIVE:-$STOREFRONT_APP/.next}"
STORE_STATE="${STORE_STATE:-$RELEASE_ROOT/.active-storefront}"
SERVICE_CMD="${SERVICE_CMD:-systemctl}"

REL_ID="${1:-}"
SKIP_BACKEND_RESTART=0
shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-backend-restart) SKIP_BACKEND_RESTART=1; shift ;;
    *) echo "error: unknown argument: $1" >&2; exit 2 ;;
  esac
done

fail() { echo "error: $*" >&2; exit 1; }

# Resolve a node binary. Under sudo the PATH is root's and does not include the
# deploy user's nvm node, so `node` is often not found. Prefer an explicit
# NODE_BIN, then the deploy user's pinned nvm node (same path the systemd unit
# hard-codes), then whatever is on PATH.
resolve_node() {
  if [[ -n "${NODE_BIN:-}" && -x "$NODE_BIN" ]]; then echo "$NODE_BIN"; return 0; fi
  local nvm_node="/home/deploy/.nvm/versions/node/v20.20.2/bin/node"
  if [[ -x "$nvm_node" ]]; then echo "$nvm_node"; return 0; fi
  # newest nvm node, if the pinned version moved
  local newest
  newest="$(ls -d /home/deploy/.nvm/versions/node/*/bin/node 2>/dev/null | sort -V | tail -1)"
  if [[ -n "$newest" && -x "$newest" ]]; then echo "$newest"; return 0; fi
  command -v node 2>/dev/null || return 1
}
NODE="$(resolve_node)" || fail "could not find a node binary (set NODE_BIN=/path/to/node)"

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
"$NODE" -e '
  const fs=require("fs"), cp=require("child_process"), path=require("path");
  const [dir,manifest]=process.argv.slice(1);
  const m=JSON.parse(fs.readFileSync(manifest,"utf8"));
  const files=Object.keys(m.checksums || {}).slice(0,20);
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

# --- record current release (for rollback + partial-failure restore) ---
PREV_ADMIN=""
[[ -L "$ADMIN_LINK" ]] && PREV_ADMIN="$(readlink -f "$ADMIN_LINK" || true)"
PREV_STORE_ID="phase2-baseline"
if [[ -f "$STORE_STATE" ]]; then
  PREV_STORE_ID="$(tr -d '\r\n' < "$STORE_STATE")"
  [[ "$PREV_STORE_ID" =~ ^([0-9a-f]{40}|phase2-baseline)$ ]] \
    || fail "invalid active storefront state: '$PREV_STORE_ID'"
fi

[[ -d "$STOREFRONT_LIVE" && ! -L "$STOREFRONT_LIVE" ]] \
  || fail "live storefront must be a real directory: $STOREFRONT_LIVE"

TARGET_BUILD_ID="$(cat "$STORE_SRC/BUILD_ID" 2>/dev/null)"
CURRENT_BUILD_ID="$(cat "$STOREFRONT_LIVE/BUILD_ID" 2>/dev/null || true)"
if [[ "$PREV_STORE_ID" == "$REL_ID" && "$CURRENT_BUILD_ID" == "$TARGET_BUILD_ID" ]] \
   && [[ -L "$ADMIN_LINK" && "$(readlink -f "$ADMIN_LINK")" == "$ADMIN_SRC" ]]; then
  fail "release $REL_ID is already active"
fi

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

# The admin may still need its one-time conversion from a real dist directory
# to an atomic symlink. Storefront always remains a real .next directory.
BASELINE_DIR="$RELEASE_ROOT/phase2-baseline"
ADMIN_FIRST_CONVERSION=0
[[ ! -L "$ADMIN_LINK" && -d "$ADMIN_LINK" ]] && ADMIN_FIRST_CONVERSION=1

# Materialize the storefront before stopping the service. The candidate lives
# on the same filesystem as .next, so the final rename is fast and cannot
# expose a partially copied build.
STORE_NEW="$STOREFRONT_APP/.next.promote.$REL_ID.$$"
STORE_PREV="$STOREFRONT_APP/.next.previous.$$"
STORE_FAILED="$STOREFRONT_APP/.next.failed.$REL_ID.$(date +%Y%m%d-%H%M%S)"
[[ ! -e "$STORE_NEW" && ! -e "$STORE_PREV" ]] \
  || fail "temporary storefront path already exists"
mkdir -p "$STORE_NEW"
cp -a --reflink=auto "$STORE_SRC/." "$STORE_NEW/"
[[ "$(cat "$STORE_NEW/BUILD_ID" 2>/dev/null)" == "$TARGET_BUILD_ID" ]] \
  || fail "materialized storefront BUILD_ID mismatch"

TRANSACTION_ACTIVE=0
transaction_cleanup() {
  local status=$?
  if [[ "$TRANSACTION_ACTIVE" -eq 1 ]]; then
    echo "error: unexpected promotion failure — restoring previous artifacts" >&2
    svc stop kelley-public || true
    [[ -n "$PREV_ADMIN" ]] && swap_symlink "$PREV_ADMIN" "$ADMIN_LINK" || true
    if [[ -d "$STORE_PREV" ]]; then
      if [[ -d "$STOREFRONT_LIVE" ]]; then
        mv "$STOREFRONT_LIVE" "$STORE_FAILED" || true
      fi
      mv "$STORE_PREV" "$STOREFRONT_LIVE" || true
    elif [[ -d "$RELEASE_ROOT/$PREV_STORE_ID/storefront-next" ]]; then
      if [[ -d "$STOREFRONT_LIVE" ]]; then
        mv "$STOREFRONT_LIVE" "$STORE_FAILED" || true
      fi
      mkdir -p "$STOREFRONT_LIVE"
      cp -a --reflink=auto "$RELEASE_ROOT/$PREV_STORE_ID/storefront-next/." "$STOREFRONT_LIVE/" || true
    fi
    if [[ "$SKIP_BACKEND_RESTART" -eq 0 ]]; then
      svc restart kelley-backend || true
    fi
    svc start kelley-public || true
  elif [[ -d "$STORE_NEW" ]]; then
    rm -rf -- "$STORE_NEW"
  fi
  return "$status"
}
trap transaction_cleanup EXIT

echo "==> promoting release $REL_ID"
echo "    admin:      $ADMIN_SRC"
echo "    storefront: $STORE_SRC -> real $STOREFRONT_LIVE"

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

TRANSACTION_ACTIVE=1

# Preserve the current real storefront until the candidate passes readiness.
mv "$STOREFRONT_LIVE" "$STORE_PREV"
mv "$STORE_NEW" "$STOREFRONT_LIVE"

# --- perform admin first conversion now (storefront is stopped) ---
if [[ "$ADMIN_FIRST_CONVERSION" -eq 1 ]]; then
  echo "==> first conversion: preserving current Phase-2 admin as phase2-baseline"
  mkdir -p "$BASELINE_DIR"
  if [[ ! -L "$ADMIN_LINK" && -d "$ADMIN_LINK" && ! -e "$BASELINE_DIR/admin" ]]; then
    mv "$ADMIN_LINK" "$BASELINE_DIR/admin"
  fi
  PREV_ADMIN="$(realpath -e "$BASELINE_DIR/admin" 2>/dev/null || true)"
fi

# --- switch admin pointer; storefront real directory is already in place ---
echo "==> switching admin pointer"
swap_symlink "$ADMIN_SRC" "$ADMIN_LINK"

# --- install the real-.next systemd unit if the repo copy differs ---
if [[ "${SKIP_SYSTEMD_INSTALL:-0}" != "1" ]]; then
  if ! cmp -s "$SELF_DIR/systemd/kelley-public.service" /etc/systemd/system/kelley-public.service 2>/dev/null; then
    echo "==> installing updated kelley-public.service (real .next)"
    install -m 0644 "$SELF_DIR/systemd/kelley-public.service" /etc/systemd/system/kelley-public.service
    svc daemon-reload
  fi
fi

# --- optionally restart backend, start storefront, then poll readiness ---
# A start/restart failure must fall through to the restore block, not abort
# under `set -e` — so capture its status instead of letting it exit.
echo "==> starting promoted services"
ok=1
if [[ "$SKIP_BACKEND_RESTART" -eq 0 ]]; then
  svc restart kelley-backend || ok=0
fi
svc start kelley-public || ok=0

INSTALL_SH_LIB=1 source "$SELF_DIR/install.sh"
if [[ "$ok" -eq 1 ]]; then
  READINESS_TIMEOUT=45 wait_for_endpoint "api" "http://127.0.0.1:8000/api/health" || ok=0
  READINESS_TIMEOUT=60 wait_for_endpoint "storefront" "http://127.0.0.1:3000/" || ok=0
fi

if [[ "$ok" -ne 1 ]]; then
  echo "error: readiness failed after promotion — restoring previous artifacts" >&2
  svc stop kelley-public || true
  [[ -n "$PREV_ADMIN" ]] && swap_symlink "$PREV_ADMIN" "$ADMIN_LINK"
  if [[ -d "$STOREFRONT_LIVE" ]]; then
    mv "$STOREFRONT_LIVE" "$STORE_FAILED"
  fi
  mv "$STORE_PREV" "$STOREFRONT_LIVE"
  if [[ "$SKIP_BACKEND_RESTART" -eq 0 ]]; then
    svc restart kelley-backend || true
  fi
  svc start kelley-public || true
  TRANSACTION_ACTIVE=0
  echo "error: promotion restored the previous real .next; failed build kept at $STORE_FAILED" >&2
  exit 6
fi

# Keep the previous live bytes. The recovered Phase-2 build was consumed from
# its release directory, so put it back there; later releases already retain a
# canonical staged artifact and receive a timestamped live backup as well.
if [[ "$PREV_STORE_ID" == "phase2-baseline" && ! -e "$BASELINE_DIR/storefront-next" ]]; then
  mkdir -p "$BASELINE_DIR"
  mv "$STORE_PREV" "$BASELINE_DIR/storefront-next"
  write_baseline_manifest "$BASELINE_DIR"
else
  prev_release="$RELEASE_ROOT/$PREV_STORE_ID"
  mkdir -p "$prev_release"
  mv "$STORE_PREV" "$prev_release/storefront-live-backup-$(date +%Y%m%d-%H%M%S)"
fi

# Record the active storefront only after readiness and backup preservation.
state_tmp="${STORE_STATE}.tmp.$$"
printf '%s\n' "$REL_ID" > "$state_tmp"
mv -T "$state_tmp" "$STORE_STATE"

TRANSACTION_ACTIVE=0
trap - EXIT
echo "==> promotion complete."
echo "    active release:   $REL_ID"
echo "    previous admin:   ${PREV_ADMIN:-<first conversion / none>}"
echo "    previous store:   $PREV_STORE_ID"
echo "    rollback with:    sudo bash deploy/rollback-release.sh $PREV_STORE_ID"
