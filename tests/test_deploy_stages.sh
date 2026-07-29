#!/usr/bin/env bash
# Stage-machine tests for the deployment pipeline. Runs the REAL cd-deploy.sh and update.sh against
# a throwaway git repo with a stub `docker` on PATH, and asserts that each failure names its stage,
# says whether the previous deployment was ever stopped, and rolls back only when it should.
#
#   bash tests/test_deploy_stages.sh
#
# Why a shell test rather than pytest: the thing under test IS the shell. A python harness would
# assert on strings this file can check directly, and would not exercise `set -uo pipefail`, the
# sourcing order, or the exit-code plumbing — which is where the 2026-07-29 misreport actually lived.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0 fail=0
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; pass=$((pass + 1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=$((fail + 1)); }
check() { # $1 desc, $2 haystack, $3 needle
  case "$2" in *"$3"*) ok "$1" ;; *) bad "$1 — expected to find: $3" ;; esac
}
check_not() {
  case "$2" in *"$3"*) bad "$1 — did NOT expect: $3" ;; *) ok "$1" ;; esac
}

# ── a throwaway deployment host ──────────────────────────────────────────────────────────────────
setup_host() {
  rm -rf "$TMP/host" "$TMP/bin"; mkdir -p "$TMP/host" "$TMP/bin"
  cp -r "$REPO/deploy" "$TMP/host/deploy"
  cd "$TMP/host"
  git init -q -b main . && git config user.email t@t && git config user.name t
  printf 'APP_DOMAIN=example.test\nIH_DATA_MOUNT=0\n' > deploy/.env

  # smoke-test.sh is stubbed: its own behaviour is tested elsewhere, and here we only care that a
  # smoke FAILURE is reported as the SMOKE stage.
  cat > deploy/ops/smoke-test.sh <<'SMOKE'
#!/usr/bin/env bash
echo "  (stub smoke)"
[ "${SMOKE_FAIL:-0}" = "1" ] && { echo "  FAIL  stub check" >&2; exit 1; }
exit 0
SMOKE
  chmod +x deploy/ops/smoke-test.sh

  # BOTH commits must contain the stubs. An earlier version committed them only onto the BASE
  # commit, so checking out TARGET restored the REAL smoke-test.sh and the success case failed at
  # SMOKE — the fixture, not the code. Stubs first, then both commits.
  echo "v1" > app.txt
  git add -A && git commit -qm "first (serving)" >/dev/null
  BASE="$(git rev-parse HEAD)"
  echo "v2" > app.txt && git add -A && git commit -qm "second (target)" >/dev/null
  TARGET="$(git rev-parse HEAD)"
  git checkout -q "$BASE"
  git remote add origin "$TMP/host" 2>/dev/null || true

  # A stub docker whose behaviour each test controls through DOCKER_FAIL_AT.
  cat > "$TMP/bin/docker" <<'STUB'
#!/usr/bin/env bash
sub="${1:-}"; [ "$sub" = "compose" ] && { shift; while [ $# -gt 0 ] && [ "${1:0:1}" = "-" ]; do
    case "$1" in -f|--env-file|--profile) shift 2 ;; *) shift ;; esac; done; sub="${1:-}"; }
case "${DOCKER_FAIL_AT:-}" in
  daemon) exit 1 ;;
esac
case "$sub" in
  info) exit 0 ;;
  build)   [ "${DOCKER_FAIL_AT:-}" = "build" ] && { echo "ERROR: failed to solve: dockerfile"; exit 1; }; echo "built"; exit 0 ;;
  up)      [ "${DOCKER_FAIL_AT:-}" = "up" ] && { echo "ERROR: container exited"; exit 1; }; echo "started"; exit 0 ;;
  run)     echo "backup ok"; [ "${DOCKER_FAIL_AT:-}" = "backup" ] && exit 1; exit 0 ;;
  exec)    [ "${DOCKER_FAIL_AT:-}" = "ready" ] && exit 1; exit 0 ;;
  ps)      echo "api=running"; exit 0 ;;
  logs)    echo "(stub logs)"; exit 0 ;;
  system)  echo "TYPE TOTAL"; exit 0 ;;
  *) exit 0 ;;
esac
STUB
  chmod +x "$TMP/bin/docker"
  export PATH="$TMP/bin:$PATH"
}

run_deploy() { # $@ passed to cd-deploy; captures stdout+stderr
  # A short readiness timeout by default: the ROLLBACK path runs update.sh a second time, and two
  # 240 s waits would make this suite unrunnable.
  ( cd "$TMP/host" && RWE_DEPLOY_READY_TIMEOUT="${RWE_DEPLOY_READY_TIMEOUT:-5}" bash deploy/ops/cd-deploy.sh "$@" 2>&1 )
}

echo "== deployment stage machine =="

# ── PREFLIGHT: dirty working tree ────────────────────────────────────────────────────────────────
setup_host
echo "locally edited" > app.txt   # the exact state that misreported on 2026-07-29
out="$(DOCKER_FAIL_AT= run_deploy "$TARGET")"
check     "dirty tree -> PREFLIGHT"              "$out" "[PREFLIGHT]"
check     "dirty tree -> names the blocked file" "$out" "app.txt"
check     "dirty tree -> CD_RESULT aborted"      "$out" "CD_RESULT=aborted"
check     "dirty tree -> service_interrupted=0"  "$out" "service_interrupted=0"
check     "dirty tree -> no rollback"            "$out" "rollback=none"
check     "dirty tree -> prints the real fix"    "$out" "checkout HEAD -- ."
check_not "dirty tree -> never says health gate" "$out" "health/smoke gate"

# ── PREFLIGHT: docker daemon down ────────────────────────────────────────────────────────────────
setup_host
out="$(DOCKER_FAIL_AT=daemon run_deploy "$TARGET")"
check     "daemon down -> PREFLIGHT"             "$out" "[PREFLIGHT]"
check     "daemon down -> names the daemon"      "$out" "docker daemon is not responding"
check     "daemon down -> aborted"               "$out" "CD_RESULT=aborted"

# ── GIT_FETCH: unknown ref ───────────────────────────────────────────────────────────────────────
setup_host
out="$(run_deploy "0000000000000000000000000000000000000000")"
check     "unknown ref -> GIT_FETCH"             "$out" "GIT_FETCH"
check     "unknown ref -> service untouched"     "$out" "service_interrupted=0"
check_not "unknown ref -> no rollback ran"       "$out" "rollback=ok"

# ── BUILD: image build fails, service must NOT be touched ────────────────────────────────────────
setup_host
out="$(DOCKER_FAIL_AT=build run_deploy "$TARGET")"
check     "build failure -> BUILD stage"         "$out" "[BUILD]"
check     "build failure -> service untouched"   "$out" "service_interrupted=0"
check     "build failure -> says no rollback"    "$out" "rollback=none"
check     "build failure -> says never stopped"  "$out" "NEVER STOPPED"
check     "build failure -> NO ROLLBACK IS NEEDED" "$out" "NO ROLLBACK IS NEEDED"

# ── CONTAINER_STARTUP: past the point of no return, rollback MUST run ────────────────────────────
setup_host
out="$(DOCKER_FAIL_AT=up run_deploy "$TARGET")"
check     "startup failure -> CONTAINER_STARTUP" "$out" "CONTAINER_STARTUP"
check     "startup failure -> service down"      "$out" "service_interrupted=1"
check     "startup failure -> rollback attempted" "$out" "[ROLLBACK]"
check     "startup failure -> says SITE IS DOWN" "$out" "SITE IS DOWN"

# ── READINESS ────────────────────────────────────────────────────────────────────────────────────
setup_host
out="$(DOCKER_FAIL_AT=ready RWE_DEPLOY_READY_TIMEOUT=5 run_deploy "$TARGET")"
check     "readiness failure -> READINESS"       "$out" "READINESS"
check     "readiness failure -> service down"    "$out" "service_interrupted=1"

# ── SMOKE: up, ready, but wrong ──────────────────────────────────────────────────────────────────
setup_host
out="$(SMOKE_FAIL=1 run_deploy "$TARGET")"
check     "smoke failure -> SMOKE stage"         "$out" "SMOKE"
check     "smoke failure -> service replaced"    "$out" "service_interrupted=1"
check     "smoke failure -> rollback ran"        "$out" "rollback="

# ── SUCCESS ──────────────────────────────────────────────────────────────────────────────────────
setup_host
out="$(run_deploy "$TARGET")"
check     "success -> CD_RESULT=deployed"        "$out" "CD_RESULT=deployed"
check     "success -> stage=SUCCESS"             "$out" "stage=SUCCESS"
check_not "success -> no failure block"          "$out" "DEPLOYMENT FAILED"

echo ""
echo "== deploy stages: $pass PASS, $fail FAIL =="
[ "$fail" -eq 0 ] || exit 1
