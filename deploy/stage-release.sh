#!/usr/bin/env bash
#
# stage-release.sh — build a versioned, checksummed, recoverable release into
# /opt/kelley/releases/<git-sha>/ WITHOUT touching any live artifact.
#
# Unprivileged. Never writes into the live served ARTIFACTS (apps/admin/dist,
# apps/storefront/.next) and never moves a live pointer. A build failure leaves
# those untouched and the partial release un-promotable (no validated
# manifest). Caveat: `pnpm install --frozen-lockfile` DOES touch the shared
# node_modules the running storefront imports from — run staging in a
# quiet/pre-window period, or from a separate checkout, if a release changes
# dependencies (the lockfile is frozen, so this is normally a no-op).
#
# Usage:
#   deploy/stage-release.sh [--sha <full40hexsha>] [--verify-only] [--skip-e2e]
#
#   --sha          Release SHA to build (default: current HEAD). Must be a full
#                  40-char hex commit that exists.
#   --verify-only  Allow a dirty working tree (for local verification builds).
#                  A clean tree matching --sha is required otherwise.
#   --skip-e2e     Skip the Playwright docker gate (HTTP preview checks still run).
#
# Overridable for dry-run/testing (default to production paths):
#   ROOT           repo root                (default: resolved from this script)
#   RELEASE_ROOT   where releases live      (default: $ROOT/releases)
#   BUILD_CMD_ADMIN / BUILD_CMD_STOREFRONT  build command overrides (testing)
#
set -euo pipefail

# --- resolve paths ---
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "$SELF_DIR/.." && pwd)}"
RELEASE_ROOT="${RELEASE_ROOT:-$ROOT/releases}"

SHA=""
VERIFY_ONLY=0
SKIP_E2E=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha) SHA="${2:-}"; shift 2 ;;
    --verify-only) VERIFY_ONLY=1; shift ;;
    --skip-e2e) SKIP_E2E=1; shift ;;
    *) echo "error: unknown argument: $1" >&2; exit 2 ;;
  esac
done

# --- derive + validate SHA ---
if [[ -z "$SHA" ]]; then
  SHA="$(git -C "$ROOT" rev-parse --verify HEAD^{commit})"
fi
if [[ ! "$SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "error: SHA must be a full 40-char hex commit (got: '$SHA')" >&2
  exit 2
fi
if ! git -C "$ROOT" cat-file -e "${SHA}^{commit}" 2>/dev/null; then
  echo "error: commit $SHA does not exist in $ROOT" >&2
  exit 2
fi

# --- reject a dirty / mismatched tree unless --verify-only ---
if [[ "$VERIFY_ONLY" -eq 0 ]]; then
  if [[ -n "$(git -C "$ROOT" status --porcelain)" ]]; then
    echo "error: working tree is dirty; commit/stash first or pass --verify-only" >&2
    exit 3
  fi
  head_sha="$(git -C "$ROOT" rev-parse --verify HEAD^{commit})"
  if [[ "$head_sha" != "$SHA" ]]; then
    echo "error: HEAD ($head_sha) != requested --sha ($SHA); check out $SHA first or pass --verify-only" >&2
    exit 3
  fi
fi

REL_DIR="$RELEASE_ROOT/$SHA"
ADMIN_OUT="$REL_DIR/admin"
STORE_OUT="$REL_DIR/storefront-next"
MANIFEST="$REL_DIR/manifest.json"

# --- idempotency: refuse to clobber an existing validated release ---
if [[ -f "$MANIFEST" ]] && grep -q '"validated": true' "$MANIFEST" 2>/dev/null; then
  echo "release $SHA already staged and validated ($MANIFEST); nothing to do."
  exit 0
fi

echo "==> staging release $SHA into $REL_DIR (live artifacts untouched)"
rm -rf "$REL_DIR"          # only ever the release dir for THIS sha, never live paths
mkdir -p "$ADMIN_OUT" "$STORE_OUT"

# --- deps ---
echo "==> pnpm install --frozen-lockfile"
( cd "$ROOT" && pnpm install --frozen-lockfile )

# --- build admin into the versioned dir (never the live dist) ---
echo "==> build admin -> $ADMIN_OUT"
if [[ -n "${BUILD_CMD_ADMIN:-}" ]]; then
  ADMIN_OUT="$ADMIN_OUT" bash -c "$BUILD_CMD_ADMIN"
else
  ( cd "$ROOT" && pnpm --filter ./apps/admin exec vite build --outDir "$ADMIN_OUT" --emptyOutDir --manifest )
fi

# --- build storefront into the versioned .next dir (never the live .next) ---
# Next.js only honors a distDir RELATIVE to the storefront project root, so we
# build into a relative staging dir there and then move the result into the
# absolute release dir. The relative staging path is unique per-sha and lives
# under apps/storefront/ (gitignored), never overlapping the live `.next`.
echo "==> build storefront -> $STORE_OUT"
if [[ -n "${BUILD_CMD_STOREFRONT:-}" ]]; then
  STORE_OUT="$STORE_OUT" bash -c "$BUILD_CMD_STOREFRONT"
else
  STORE_REL=".next-stage-$SHA"
  STORE_STAGE_ABS="$ROOT/apps/storefront/$STORE_REL"
  rm -rf "$STORE_STAGE_ABS"
  # `next build` rewrites next-env.d.ts and tsconfig.json's `include` to point
  # at the build's types dir. Snapshot both and restore them via a trap so the
  # working tree is left pristine even if the build FAILS (otherwise a failed
  # build leaves mutated tracked files + .bak litter that block the clean-tree
  # gate on the next run). The backups use fixed names cleaned by the trap.
  SF="$ROOT/apps/storefront"
  cp "$SF/next-env.d.ts" "$SF/.next-env.d.ts.stagebak" 2>/dev/null || true
  cp "$SF/tsconfig.json" "$SF/.tsconfig.json.stagebak" 2>/dev/null || true
  restore_sf_config() {
    [[ -f "$SF/.next-env.d.ts.stagebak" ]] && mv -f "$SF/.next-env.d.ts.stagebak" "$SF/next-env.d.ts"
    [[ -f "$SF/.tsconfig.json.stagebak" ]] && mv -f "$SF/.tsconfig.json.stagebak" "$SF/tsconfig.json"
    return 0
  }
  trap restore_sf_config EXIT
  ( cd "$SF" && NEXT_DIST_DIR="$STORE_REL" pnpm exec next build )
  restore_sf_config
  trap - EXIT
  [[ -s "$STORE_STAGE_ABS/BUILD_ID" ]] || { echo "error: storefront build produced no BUILD_ID at $STORE_STAGE_ABS" >&2; rm -rf "$STORE_STAGE_ABS"; exit 4; }
  rm -rf "$STORE_OUT"
  mv "$STORE_STAGE_ABS" "$STORE_OUT"
fi

# --- structural verification ---
echo "==> verifying build outputs"
[[ -f "$ADMIN_OUT/index.html" ]] || { echo "error: admin index.html missing" >&2; exit 4; }
# every /assets/*.js|css referenced by index.html must exist on disk
missing=0
while IFS= read -r asset; do
  [[ -f "$ADMIN_OUT/$asset" ]] || { echo "error: admin asset missing: $asset" >&2; missing=1; }
done < <(grep -oE '/assets/[^"]+\.(js|css)' "$ADMIN_OUT/index.html" | sed 's#^/##' | sort -u)
[[ "$missing" -eq 0 ]] || exit 4
[[ -s "$STORE_OUT/BUILD_ID" ]] || { echo "error: storefront BUILD_ID missing/empty" >&2; exit 4; }
for f in build-manifest.json app-build-manifest.json required-server-files.json; do
  [[ -f "$STORE_OUT/$f" ]] || { echo "error: storefront $f missing" >&2; exit 4; }
done
BUILD_ID="$(cat "$STORE_OUT/BUILD_ID")"

# --- ensure world-readable so the caddy user can serve the admin dir ---
chmod -R a+rX "$REL_DIR"

# --- manifest with checksums (validated:false until previews/E2E pass) ---
echo "==> writing manifest + checksums"
CHECKSUMS="$REL_DIR/.checksums.sha256"
( cd "$REL_DIR" && find admin storefront-next -type f -print0 | sort -z | xargs -0 sha256sum > "$CHECKSUMS" )
node -e '
  const fs = require("fs");
  const [rel, sha, buildId, csPath, manifestPath] = process.argv.slice(1);
  const checksums = {};
  for (const line of fs.readFileSync(csPath, "utf8").split("\n")) {
    if (!line.trim()) continue;
    const sp = line.indexOf("  ");
    checksums[line.slice(sp + 2)] = line.slice(0, sp);
  }
  const manifest = {
    sha, build_id: buildId,
    admin_path: rel + "/admin",
    storefront_path: rel + "/storefront-next",
    node_version: process.version,
    file_count: Object.keys(checksums).length,
    checksums,
    validated: false,
  };
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + "\n");
' "$REL_DIR" "$SHA" "$BUILD_ID" "$CHECKSUMS" "$MANIFEST"

# --- preview servers on spare ports + HTTP checks (never live ports) ---
echo "==> preview HTTP checks"
ADMIN_PORT="${STAGE_ADMIN_PORT:-5174}"
STORE_PORT="${STAGE_STORE_PORT:-3001}"
pids=()
# next only honors a RELATIVE distDir, so preview it through a temporary
# relative symlink under apps/storefront/ that points at the release dir.
PREVIEW_LINK_REL=".next-preview-$SHA"
PREVIEW_LINK_ABS="$ROOT/apps/storefront/$PREVIEW_LINK_REL"
cleanup() {
  for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done
  rm -f "$PREVIEW_LINK_ABS"
}
trap cleanup EXIT

( cd "$ROOT/apps/admin" && pnpm exec vite preview --outDir "$ADMIN_OUT" --port "$ADMIN_PORT" --strictPort --host 127.0.0.1 ) >/dev/null 2>&1 &
pids+=($!)
rm -f "$PREVIEW_LINK_ABS"; ln -s "$STORE_OUT" "$PREVIEW_LINK_ABS"
( cd "$ROOT/apps/storefront" && NEXT_DIST_DIR="$PREVIEW_LINK_REL" pnpm exec next start -p "$STORE_PORT" -H 127.0.0.1 ) >/dev/null 2>&1 &
pids+=($!)

# reuse install.sh's bounded readiness poller
INSTALL_SH_LIB=1 source "$SELF_DIR/install.sh"
READINESS_TIMEOUT=40 wait_for_endpoint "admin preview" "http://127.0.0.1:$ADMIN_PORT/" || exit 5
READINESS_TIMEOUT=60 wait_for_endpoint "storefront preview" "http://127.0.0.1:$STORE_PORT/" || exit 5
# an admin asset resolves
first_asset="$(grep -oE '/assets/[^"]+\.js' "$ADMIN_OUT/index.html" | head -1)"
code="$(curl -fsS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$ADMIN_PORT$first_asset" || true)"
[[ "$code" == "200" ]] || { echo "error: admin asset $first_asset returned $code" >&2; exit 5; }

cleanup; trap - EXIT

# --- browser E2E gate (pinned docker image) ---
if [[ "$SKIP_E2E" -eq 0 ]]; then
  echo "==> browser E2E (pinned Playwright docker image)"
  ( cd "$ROOT/apps/admin" && pnpm run test:e2e:docker )
else
  echo "==> skipping browser E2E (--skip-e2e)"
fi

# --- mark validated ---
node -e '
  const fs = require("fs");
  const p = process.argv[1];
  const m = JSON.parse(fs.readFileSync(p, "utf8"));
  m.validated = true;
  fs.writeFileSync(p, JSON.stringify(m, null, 2) + "\n");
' "$MANIFEST"

echo "==> staged + validated release $SHA"
echo "    admin:      $ADMIN_OUT"
echo "    storefront: $STORE_OUT  (BUILD_ID=$BUILD_ID)"
echo "    manifest:   $MANIFEST"
echo "    promote with: sudo bash deploy/promote-release.sh $SHA"
