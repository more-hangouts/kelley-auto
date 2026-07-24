#!/usr/bin/env bash
#
# release_dryrun.sh — exercise promote-release.sh / rollback-release.sh against
# a throwaway fixture tree. No sudo, no live artifacts, no real services. A
# SERVICE_CMD stub records stop/start/restart ordering and can be told to fail.
#
# Proves: first conversion, normal promote (stop→swap→start order),
# partial-failure restore, rollback, invalid-ID rejection, and no-op guard.
#
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SELF_DIR/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

pass=0; fail=0
ok()   { echo "  PASS: $1"; pass=$((pass+1)); }
bad()  { echo "  FAIL: $1"; fail=$((fail+1)); }

# --- build a fixture release tree ---
REL_ROOT="$WORK/releases"
APP_ADMIN="$WORK/apps/admin"
APP_STORE="$WORK/apps/storefront"
mkdir -p "$REL_ROOT" "$APP_ADMIN" "$APP_STORE"

SHA_A="$(printf 'a%.0s' {1..40})"   # 40 hex 'a'
SHA_B="$(printf 'b%.0s' {1..40})"
make_release() {
  local id="$1" tag="$2"
  mkdir -p "$REL_ROOT/$id/admin" "$REL_ROOT/$id/storefront-next"
  echo "<html>admin $tag</html>" > "$REL_ROOT/$id/admin/index.html"
  echo "$tag" > "$REL_ROOT/$id/storefront-next/BUILD_ID"
  cat > "$REL_ROOT/$id/manifest.json" <<EOF
{ "sha": "$id", "validated": true, "checksums": {} }
EOF
}
make_release "$SHA_A" "A"
make_release "$SHA_B" "B"

# current live artifacts as REAL dirs (pre-first-conversion Phase-2 state)
mkdir -p "$APP_ADMIN/dist" "$APP_STORE/.next"
echo "<html>phase2 admin</html>" > "$APP_ADMIN/dist/index.html"
echo "phase2" > "$APP_STORE/.next/BUILD_ID"

# --- SERVICE_CMD stub: logs calls; fails 'start' when STUB_FAIL_START=1 ---
STUB_LOG="$WORK/svc.log"
cat > "$WORK/svc_stub.sh" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
if [[ "${STUB_FAIL_START:-0}" == "1" && "$1" == "start" ]]; then exit 1; fi
exit 0
STUB
chmod +x "$WORK/svc_stub.sh"

# readiness always "ready" in dry-run: point wait_for_endpoint at a always-200
# by overriding curl via a stub on PATH.
BIN="$WORK/bin"; mkdir -p "$BIN"
cat > "$BIN/curl" <<CURL
#!/usr/bin/env bash
# emulate the -w '%{http_code}' contract install.sh uses
if [[ "${STUB_READY:-1}" == "1" ]]; then echo "200"; else echo "000"; fi
CURL
chmod +x "$BIN/curl"

run_promote() {
  ROOT="$WORK" RELEASE_ROOT="$REL_ROOT" \
  ADMIN_LINK="$APP_ADMIN/dist" STOREFRONT_APP="$APP_STORE" STOREFRONT_LIVE="$APP_STORE/.next" \
  STORE_STATE="$REL_ROOT/.active-storefront" \
  SERVICE_CMD="$WORK/svc_stub.sh" STUB_LOG="$STUB_LOG" SKIP_SYSTEMD_INSTALL=1 \
  PATH="$BIN:$PATH" \
  bash "$DEPLOY_DIR/promote-release.sh" "$@"
}
run_rollback() {
  ROOT="$WORK" RELEASE_ROOT="$REL_ROOT" \
  ADMIN_LINK="$APP_ADMIN/dist" STOREFRONT_APP="$APP_STORE" STOREFRONT_LIVE="$APP_STORE/.next" \
  STORE_STATE="$REL_ROOT/.active-storefront" \
  SERVICE_CMD="$WORK/svc_stub.sh" STUB_LOG="$STUB_LOG" SKIP_SYSTEMD_INSTALL=1 \
  PATH="$BIN:$PATH" \
  bash "$DEPLOY_DIR/rollback-release.sh" "$@"
}

echo "== 1) invalid release IDs are rejected =="
for bad_id in "" "../etc" "HEAD" "aaa" "*" "$SHA_A/../../etc"; do
  if run_promote "$bad_id" >/dev/null 2>&1; then bad "accepted invalid id '$bad_id'"; else ok "rejected '$bad_id'"; fi
done

echo "== 2) missing/unvalidated release is rejected =="
if run_promote "$(printf 'c%.0s' {1..40})" >/dev/null 2>&1; then bad "accepted nonexistent release"; else ok "rejected nonexistent release"; fi

echo "== 3) first conversion: admin symlink + real storefront + phase2-baseline =="
: > "$STUB_LOG"
if run_promote "$SHA_A" >/dev/null 2>&1; then
  [[ -L "$APP_ADMIN/dist" ]] && ok "admin dist is now a symlink" || bad "admin dist not a symlink"
  [[ -d "$APP_STORE/.next" && ! -L "$APP_STORE/.next" ]] && ok "storefront .next remains a real directory" || bad "storefront .next is not real"
  [[ "$(cat "$APP_STORE/.next/BUILD_ID")" == "A" ]] && ok "storefront A is active" || bad "storefront A not active"
  [[ "$(cat "$REL_ROOT/.active-storefront")" == "$SHA_A" ]] && ok "active storefront state records A" || bad "active state missing A"
  [[ -f "$REL_ROOT/phase2-baseline/admin/index.html" ]] && ok "phase2-baseline admin preserved" || bad "phase2-baseline admin missing"
  [[ -f "$REL_ROOT/phase2-baseline/storefront-next/BUILD_ID" ]] && ok "phase2-baseline .next preserved" || bad "phase2-baseline .next missing"
  grep -q "phase2 admin" "$REL_ROOT/phase2-baseline/admin/index.html" && ok "phase2 bytes intact" || bad "phase2 bytes changed"
  [[ "$(readlink -f "$APP_ADMIN/dist")" == "$REL_ROOT/$SHA_A/admin" ]] && ok "admin points at A" || bad "admin not pointing at A"
  # phase2-baseline must get a VALIDATED manifest so it is rollback-able (H1)
  [[ -f "$REL_ROOT/phase2-baseline/manifest.json" ]] && grep -q '"validated": true' "$REL_ROOT/phase2-baseline/manifest.json" && ok "phase2-baseline manifest is validated" || bad "phase2-baseline manifest missing/unvalidated"
  # stop must precede start in the log
  if [[ "$(grep -nE 'stop kelley-public|start kelley-public' "$STUB_LOG" | head -1)" == *stop* ]]; then ok "storefront stop precedes start"; else bad "start before stop"; fi
else
  bad "first-conversion promote failed"
fi

echo "== 4) promote B records previous=A and points at B =="
: > "$STUB_LOG"
if run_promote "$SHA_B" >/dev/null 2>&1; then
  [[ "$(readlink -f "$APP_ADMIN/dist")" == "$REL_ROOT/$SHA_B/admin" ]] && ok "admin points at B" || bad "admin not at B"
  [[ "$(cat "$APP_STORE/.next/BUILD_ID")" == "B" ]] && ok "storefront real .next is B" || bad "storefront not at B"
  [[ "$(cat "$REL_ROOT/.active-storefront")" == "$SHA_B" ]] && ok "active storefront state records B" || bad "active state not B"
  [[ -d "$REL_ROOT/$SHA_A" ]] && ok "old release A retained (not deleted)" || bad "release A deleted"
else
  bad "promote B failed"
fi

echo "== 5) partial-failure: storefront start fails -> both pointers restored to A =="
: > "$STUB_LOG"
# currently active is B; attempt promote back to A with start failing
if STUB_FAIL_START=1 run_promote "$SHA_A" >/dev/null 2>&1; then
  bad "promote reported success despite start failure"
else
  # after a failed promote to A, BOTH pointers should be restored to B
  [[ "$(readlink -f "$APP_ADMIN/dist")" == "$REL_ROOT/$SHA_B/admin" ]] && ok "admin restored to B after partial failure" || bad "admin not restored (got $(readlink -f "$APP_ADMIN/dist"))"
  [[ "$(cat "$APP_STORE/.next/BUILD_ID")" == "B" ]] && ok "real storefront restored to B after partial failure" || bad "storefront not restored"
  [[ "$(cat "$REL_ROOT/.active-storefront")" == "$SHA_B" ]] && ok "active state remains B after failure" || bad "active state changed after failure"
fi

echo "== 6) rollback to A succeeds and no-op guard rejects repeat =="
: > "$STUB_LOG"
if run_rollback "$SHA_A" >/dev/null 2>&1; then
  [[ "$(readlink -f "$APP_ADMIN/dist")" == "$REL_ROOT/$SHA_A/admin" ]] && ok "rollback switched to A" || bad "rollback did not switch"
  [[ "$(cat "$APP_STORE/.next/BUILD_ID")" == "A" ]] && ok "rollback restored real storefront A" || bad "rollback storefront not A"
  if run_rollback "$SHA_A" >/dev/null 2>&1; then bad "no-op rollback accepted"; else ok "no-op rollback rejected"; fi
else
  bad "rollback to A failed"
fi

echo "== 7) rollback to phase2-baseline works (H1: baseline is a valid target) =="
: > "$STUB_LOG"
if run_rollback "phase2-baseline" >/dev/null 2>&1; then
  [[ "$(readlink -f "$APP_ADMIN/dist")" == "$REL_ROOT/phase2-baseline/admin" ]] && ok "rolled back to phase2-baseline admin" || bad "did not switch to baseline admin"
  [[ "$(cat "$APP_STORE/.next/BUILD_ID")" == "phase2" ]] && ok "rolled back to real phase2 storefront" || bad "did not restore baseline storefront"
  grep -q "phase2 admin" "$APP_ADMIN/dist/index.html" && ok "phase2-baseline bytes served after rollback" || bad "baseline bytes wrong"
else
  bad "rollback to phase2-baseline failed (H1 regression)"
fi

echo ""
echo "release dry-run: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]] || exit 1
