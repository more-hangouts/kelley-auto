#!/usr/bin/env bash
#
# rollback-release.sh — switch the live admin dist and storefront back to a
# known, validated release (or phase2-baseline). Privileged; a rollback target
# is just a promote target, so the validation is identical.
#
# Safety:
#   - Release ID strict allowlist (40-hex sha or 'phase2-baseline').
#   - realpath containment under RELEASE_ROOT.
#   - Requires a validated manifest (phase2-baseline is validated at creation).
#   - Storefront stopped before its artifact is switched, then started.
#   - Never infers the target from an empty var or a wildcard.
#   - Refuses a no-op (target already active).
#   - The Phase-2 BACKEND code is restored ONLY with --with-backend-phase2;
#     never implied by a frontend rollback.
#   - Failed release artifacts are left in place for diagnosis.
#
# Usage:
#   sudo bash deploy/rollback-release.sh <release-id> [--with-backend-phase2]
#
# Overridable for dry-run/testing: ROOT, RELEASE_ROOT, ADMIN_LINK,
#   STOREFRONT_LINK, STOREFRONT_APP, SERVICE_CMD, PHASE2_BACKEND_SHA
#
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "$SELF_DIR/.." && pwd)}"
RELEASE_ROOT="${RELEASE_ROOT:-$ROOT/releases}"
ADMIN_LINK="${ADMIN_LINK:-$ROOT/apps/admin/dist}"
STOREFRONT_APP="${STOREFRONT_APP:-$ROOT/apps/storefront}"
STOREFRONT_LINK="${STOREFRONT_LINK:-$STOREFRONT_APP/.next-current}"
SERVICE_CMD="${SERVICE_CMD:-systemctl}"
PHASE2_BACKEND_SHA="${PHASE2_BACKEND_SHA:-4ab6c5d}"

REL_ID="${1:-}"
WITH_BACKEND=0
shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-backend-phase2) WITH_BACKEND=1; shift ;;
    *) echo "error: unknown argument: $1" >&2; exit 2 ;;
  esac
done

fail() { echo "error: $*" >&2; exit 1; }

[[ -n "$REL_ID" ]] || fail "usage: rollback-release.sh <release-id> [--with-backend-phase2]"
[[ "$REL_ID" =~ ^([0-9a-f]{40}|phase2-baseline)$ ]] || fail "invalid release id: '$REL_ID'"

REL_REAL="$(realpath -e "$RELEASE_ROOT/$REL_ID" 2>/dev/null)" || fail "release dir not found: $RELEASE_ROOT/$REL_ID"
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

# no-op guard
if [[ -L "$ADMIN_LINK" && "$(readlink -f "$ADMIN_LINK")" == "$ADMIN_SRC" ]] \
   && [[ -L "$STOREFRONT_LINK" && "$(readlink -f "$STOREFRONT_LINK")" == "$STORE_SRC" ]]; then
  fail "release $REL_ID is already the active target (nothing to roll back)"
fi

svc() { echo "    [$SERVICE_CMD $*]"; "$SERVICE_CMD" "$@"; }
swap_symlink() { local t="$1" l="$2" tmp="${2}.tmp.$$"; ln -s "$t" "$tmp"; mv -T "$tmp" "$l"; }

echo "==> rolling back to release $REL_ID"
svc stop kelley-public
swap_symlink "$ADMIN_SRC" "$ADMIN_LINK"
swap_symlink "$STORE_SRC" "$STOREFRONT_LINK"

if [[ "$WITH_BACKEND" -eq 1 ]]; then
  echo "==> restoring Phase-2 backend code ($PHASE2_BACKEND_SHA -- apps/api)"
  git -C "$ROOT" checkout "$PHASE2_BACKEND_SHA" -- apps/api
fi

svc restart kelley-backend
svc start kelley-public

INSTALL_SH_LIB=1 source "$SELF_DIR/install.sh"
ok=1
READINESS_TIMEOUT=45 wait_for_endpoint "api" "http://127.0.0.1:8000/api/health" || ok=0
READINESS_TIMEOUT=60 wait_for_endpoint "storefront" "http://127.0.0.1:3000/" || ok=0
[[ "$ok" -eq 1 ]] || fail "readiness failed after rollback to $REL_ID — manual intervention required"

echo "==> rollback complete. active release: $REL_ID"
