#!/usr/bin/env bash
# M3 / D8 — `prune_build_cache` bounds the BuildKit cache by SIZE, and never by accident.
#
# The defect this pins is two-layered and both layers were measured on production 2026-08-27:
#
#   1. WRONG PLACE. The prune lived in cd-deploy.sh, which CALLS update.sh — so the manual deploy
#      path never reached it and the cache reached 8.037 GB on a 29 GB volume at 78% used.
#   2. WRONG POLICY. `--filter until=168h` filters on LAST ACCESSED, and BuildKit touches a record
#      every time a build reuses it. Against those 8 GB it reclaimed 458.5 kB — 0.006%.
#
# So a test that only asserted "a prune runs" would have passed against the broken version. These
# assert the FLAG SHAPE, which is what makes the cache bounded, and the three failure behaviours.
#
#   bash tests/test_build_cache_prune.sh
set -uo pipefail
cd "$(dirname "$0")/.."
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (want '$3', got '$2')"; fi; }
# `-e` rather than a caller-supplied `--`: every pattern here starts with a dash, and passing `--`
# as an argument made it the PATTERN. "bounds by SIZE" then grepped for "--", found it, and passed
# for the wrong reason — a vacuous assertion inside the test written to catch vacuous assertions.
has()  { if printf '%s' "$2" | grep -q -e "$3"; then ok "$1"; else bad "$1 (missing '$3' in: $2)"; fi; }
hasnt(){ if printf '%s' "$2" | grep -q -e "$3"; then bad "$1 (unexpected '$3' in: $2)"; else ok "$1"; fi; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"

# A `docker` stub that records its argv and behaves however the case under test needs.
cat > "$TMP/bin/docker" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$DOCKER_ARGV_LOG"
case "${DOCKER_STUB_MODE:-ok}" in
  ok)          echo "Total reclaimed space: 6.1GB"; exit 0 ;;
  unknownflag) echo "unknown flag: --keep-storage" >&2; exit 125 ;;
  broken)      echo "Cannot connect to the Docker daemon" >&2; exit 1 ;;
esac
STUB
chmod +x "$TMP/bin/docker"
export PATH="$TMP/bin:$PATH"

# `df` is called for the disk line; keep it real but harmless.
# shellcheck source=deploy/ops/_compose.sh
source deploy/ops/_compose.sh

echo "== prune_build_cache =="

# ── 1. the happy path asks for a SIZE bound, with the documented default ────────────────────────
export DOCKER_ARGV_LOG="$TMP/argv1"; : > "$DOCKER_ARGV_LOG"
export DOCKER_STUB_MODE=ok
unset DEPLOY_BUILD_CACHE_KEEP
out="$(prune_build_cache 2>&1)"; rc=$?
argv="$(cat "$DOCKER_ARGV_LOG")"
check "returns 0 on success" "$rc" "0"
has   "invokes builder prune"            "$argv" "builder prune"
has   "bounds by SIZE (--keep-storage)"  "$argv" "--keep-storage 2GB"
hasnt "does NOT bound by age (--filter until=)" "$argv" "--filter"
has   "reports what was reclaimed"       "$out"  "6.1GB"

# ── 2. the bound is configurable ─────────────────────────────────────────────────────────────────
export DOCKER_ARGV_LOG="$TMP/argv2"; : > "$DOCKER_ARGV_LOG"
DEPLOY_BUILD_CACHE_KEEP=512MB prune_build_cache >/dev/null 2>&1
has "DEPLOY_BUILD_CACHE_KEEP overrides the default" "$(cat "$DOCKER_ARGV_LOG")" "--keep-storage 512MB"

# ── 3. an unsupported flag is REPORTED, never silently downgraded ────────────────────────────────
# The tempting fallback is a bare `docker builder prune -f`, which frees everything and leaves the
# next build fully cold. An operator should be told they have no bound, not surprised by one.
export DOCKER_ARGV_LOG="$TMP/argv3"; : > "$DOCKER_ARGV_LOG"
export DOCKER_STUB_MODE=unknownflag
out="$(prune_build_cache 2>&1)"; rc=$?
argv="$(cat "$DOCKER_ARGV_LOG")"
check "returns 0 when the flag is unsupported (non-fatal)" "$rc" "0"
has   "says the cache is NOT bounded" "$out" "NOT bounded"
check "made exactly one docker call — no unbounded fallback" "$(grep -c . "$DOCKER_ARGV_LOG")" "1"
hasnt "never ran an unbounded prune" "$argv" "prune -f$"

# ── 4. any other failure is non-fatal — housekeeping must not turn a green deploy red ────────────
export DOCKER_ARGV_LOG="$TMP/argv4"; : > "$DOCKER_ARGV_LOG"
export DOCKER_STUB_MODE=broken
out="$(prune_build_cache 2>&1)"; rc=$?
check "returns 0 when docker is broken" "$rc" "0"
has   "says the deploy still succeeded" "$out" "the deploy succeeded"

# ── 5. update.sh actually calls it, and cd-deploy no longer carries its own policy ───────────────
has   "update.sh calls prune_build_cache" "$(cat deploy/ops/update.sh)" "prune_build_cache"
hasnt "cd-deploy.sh no longer runs its own builder prune" \
      "$(grep -v '^\s*#' deploy/ops/cd-deploy.sh)" "docker builder prune"

echo ""
echo "  ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
