#!/usr/bin/env bash
#
# rollback-release.sh — restore a known, validated frontend release. It delegates
# frontend activation to promote-release.sh so promotion and rollback always use
# the same real-directory storefront transaction.
#
# Safety:
#   - Release ID strict allowlist (40-hex sha or 'phase2-baseline').
#   - realpath containment under RELEASE_ROOT.
#   - Requires a validated manifest (phase2-baseline is validated at creation).
#   - Storefront is materialized as a real .next directory, never a symlink.
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
#   STOREFRONT_APP, STORE_STATE, SERVICE_CMD, PHASE2_BACKEND_SHA
#
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "$SELF_DIR/.." && pwd)}"
RELEASE_ROOT="${RELEASE_ROOT:-$ROOT/releases}"
ADMIN_LINK="${ADMIN_LINK:-$ROOT/apps/admin/dist}"
SERVICE_CMD="${SERVICE_CMD:-systemctl}"
STORE_STATE="${STORE_STATE:-$RELEASE_ROOT/.active-storefront}"
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

# no-op guard
active_store=""
[[ -f "$STORE_STATE" ]] && active_store="$(tr -d '\r\n' < "$STORE_STATE")"
if [[ "$active_store" == "$REL_ID" ]] \
   && [[ -L "$ADMIN_LINK" && "$(readlink -f "$ADMIN_LINK")" == "$(realpath -e "$RELEASE_ROOT/$REL_ID/admin" 2>/dev/null || true)" ]]; then
  fail "release $REL_ID is already the active target (nothing to roll back)"
fi

echo "==> rolling back to release $REL_ID"
bash "$SELF_DIR/promote-release.sh" "$REL_ID" --skip-backend-restart

if [[ "$WITH_BACKEND" -eq 1 ]]; then
  echo "==> restoring Phase-2 backend code ($PHASE2_BACKEND_SHA -- apps/api)"
  git -C "$ROOT" checkout "$PHASE2_BACKEND_SHA" -- apps/api
  echo "    [$SERVICE_CMD restart kelley-backend]"
  "$SERVICE_CMD" restart kelley-backend
  INSTALL_SH_LIB=1 source "$SELF_DIR/install.sh"
  READINESS_TIMEOUT=45 wait_for_endpoint "api" "http://127.0.0.1:8000/api/health"
fi

echo "==> rollback complete. active release: $REL_ID"
