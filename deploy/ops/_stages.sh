#!/usr/bin/env bash
# Deployment state machine — the vocabulary every deploy failure is reported in. SOURCED by
# deploy/ops/{cd-deploy,update}.sh; not run on its own. DEPLOYMENT-ONLY.
#
# WHY THIS EXISTS. cd-deploy reported every failure the same way: "deploy of <ref> failed its
# health/smoke gate — AUTO-ROLLED-BACK". On 2026-07-29 that message was produced by `git checkout`
# refusing to overwrite a locally-modified file. No container had moved, nothing had been
# health-checked, and the rollback was a no-op against a commit that was still checked out. The
# operator-visible symptom was a missing database index, which looked like a database problem for a
# full round trip. **A pipeline that can only say "it failed" makes you re-derive where.**
#
# THE TWO QUESTIONS AN OPERATOR HAS, IN ORDER:
#   1. Is the site up?      -> SERVICE_INTERRUPTED
#   2. What do I do now?    -> STAGE + ROOT_CAUSE + RECOVERY
# Everything here is arranged to answer those two first and the forensics second.
#
# THE STAGE BOUNDARY THAT MATTERS is `dc up -d`. Every stage before it is READ-ONLY with respect to
# the running stack: the old containers keep serving even while the new code sits checked out on
# disk. From `dc up -d` onward the previous deployment has been replaced and a failure means the
# site is down until rollback finishes. `docker compose up -d --build` straddles that boundary — it
# builds, then recreates — so update.sh splits it into `dc build` and `dc up -d`, which is what
# makes BUILD (harmless) distinguishable from CONTAINER_STARTUP (service down).
#
# CONTRACT WITH THE CALLER (.github/workflows/deploy.yml greps `CD_RESULT=[a-z_]*`):
#   CD_RESULT=deployed | rolled_back | rollback_failed | aborted
# Those four tokens are load-bearing and must not change. Everything else on the line is additive:
#   stage=<STAGE> service_interrupted=<0|1> rollback=<none|ok|failed>

# See the alias-expansion note in _compose.sh — the `function` keyword is deliberate here too.

#: The ordered stages. A deploy walks these in order; the index decides whether the previous
#: deployment was still serving when the failure happened.
DEPLOY_STAGES="PREFLIGHT GIT_FETCH GIT_CHECKOUT BACKUP BUILD CONTAINER_STARTUP READINESS SMOKE ROLLBACK SUCCESS"

#: The first stage at which the previous deployment is no longer serving.
FIRST_DISRUPTIVE_STAGE="CONTAINER_STARTUP"

#: Exit codes update.sh uses so cd-deploy can name the stage even if the state file is unreadable.
#: Distinct from bash's own (1, 2, 126-128, 130+) so a crash is never mistaken for a stage failure.
EXIT_GIT_FETCH=10
EXIT_GIT_CHECKOUT=11
EXIT_BUILD=12
EXIT_CONTAINER_STARTUP=13
EXIT_READINESS=14
EXIT_SMOKE=15

STAGE="PREFLIGHT"
SERVICE_INTERRUPTED=0          # flipped to 1 the moment `dc up -d` is invoked, never back
STAGE_EVIDENCE=""

#: Where update.sh leaves the detail for cd-deploy. In the repo root and git-ignored; a file rather
#: than an exit code alone because the ROOT CAUSE and the EVIDENCE are the parts worth having and
#: neither fits in 8 bits.
DEPLOY_STATE_FILE="${DEPLOY_STATE_FILE:-${REPO_ROOT:-.}/.deploy-stage-state}"

function stage_enter() {
  STAGE="$1"
  STAGE_EVIDENCE=""
  # Whether the previous deployment is still serving is a property of the STAGE, not of the error,
  # so it is decided here rather than at each failure site — one place to be right.
  case " ${DEPLOY_STAGES} " in *" ${STAGE} "*) : ;; *) echo "BUG: unknown stage '${STAGE}'" >&2 ;; esac
  echo ""
  echo "== [${STAGE}] ${2:-} =="
}

#: Mark the point of no return. Called immediately before `dc up -d`, which is the instant the old
#: containers stop. Deliberately a separate call rather than inferred from the stage name, so the
#: flag flips exactly when the containers do and never merely because a stage was entered.
function service_now_interrupted() {
  SERVICE_INTERRUPTED=1
}

function evidence() {
  # Append a line of supporting evidence. Multi-line command output is fine; it is indented in the
  # report and JSON-escaped in the alert.
  if [ -n "$STAGE_EVIDENCE" ]; then STAGE_EVIDENCE="${STAGE_EVIDENCE}"$'\n'"$1"; else STAGE_EVIDENCE="$1"; fi
}

#: Capture the tail of a command's output as evidence WITHOUT letting it fail the script. Used to
#: attach `docker compose logs`, `git status`, `df` — the things an operator would run next anyway.
function evidence_from() {
  local label="$1"; shift
  local out
  out="$("$@" 2>&1 | tail -n "${EVIDENCE_LINES:-25}")" || true
  [ -n "$out" ] && evidence "${label}:"$'\n'"$(printf '%s\n' "$out" | sed 's/^/    /')"
}

function _service_state_line() {
  if [ "$SERVICE_INTERRUPTED" -eq 1 ]; then
    printf 'STOPPED — the previous deployment was replaced; the site is DOWN until rollback completes'
  else
    printf 'NEVER STOPPED — the previous deployment is still running and serving traffic'
  fi
}

#: The structured failure report. One block, always the same shape, so an operator learns where to
#: look once. Written to stderr so it cannot be swallowed by a caller capturing stdout for the
#: CD_RESULT contract.
function stage_report_failure() {
  local root_cause="$1" recovery="$2"
  {
    echo ""
    echo "┌───────────────────────────────────────────────────────────────────────────────"
    echo "│ DEPLOYMENT FAILED"
    echo "├───────────────────────────────────────────────────────────────────────────────"
    printf '│ %-16s %s\n' "STAGE"       "${STAGE}"
    printf '│ %-16s %s\n' "REQUESTED"   "${REF:-<none>}"
    printf '│ %-16s %s\n' "WAS SERVING" "${LAST_GOOD:-<unknown>}"
    printf '│ %-16s %s\n' "SERVICE"     "$(_service_state_line)"
    printf '│ %-16s %s\n' "ROLLBACK"    "${ROLLBACK_STATE:-not reached}"
    echo "├───────────────────────────────────────────────────────────────────────────────"
    printf '│ %-16s %s\n' "ROOT CAUSE"  "${root_cause}"
    if [ -n "$STAGE_EVIDENCE" ]; then
      echo "├───────────────────────────────────────────────────────────────────────────────"
      echo "│ EVIDENCE"
      printf '%s\n' "$STAGE_EVIDENCE" | sed 's/^/│   /'
    fi
    echo "├───────────────────────────────────────────────────────────────────────────────"
    echo "│ RECOVERY"
    printf '%s\n' "$recovery" | sed 's/^/│   /'
    echo "└───────────────────────────────────────────────────────────────────────────────"
    echo ""
  } >&2
}

#: Persist the stage detail for the parent process. Written before exiting so cd-deploy can compose
#: one alert carrying the same facts the console already showed.
function stage_persist() {
  local root_cause="$1" recovery="$2"
  {
    echo "STAGE=${STAGE}"
    echo "SERVICE_INTERRUPTED=${SERVICE_INTERRUPTED}"
    echo "ROOT_CAUSE=${root_cause}"
    echo "RECOVERY<<'EOF_RECOVERY'"
    printf '%s\n' "$recovery"
    echo "EOF_RECOVERY"
    echo "EVIDENCE<<'EOF_EVIDENCE'"
    printf '%s\n' "$STAGE_EVIDENCE"
    echo "EOF_EVIDENCE"
  } > "$DEPLOY_STATE_FILE" 2>/dev/null || true
}

#: Fail the current stage: report, persist, emit the machine contract, exit with the stage's code.
#
# EMIT_CD_RESULT is set by cd-deploy.sh and ONLY by it. The CD_RESULT line is the contract with
# .github/workflows/deploy.yml, and exactly one process must own it — update.sh printing its own
# would give the workflow two lines to `tail -1` between, and the wrong one would win. The first
# version of this function omitted the line entirely, so every stage failure exited silently as far
# as CI was concerned; the stage tests caught it.
function stage_fail() {
  local root_cause="$1" recovery="$2" code="${3:-1}"
  stage_report_failure "$root_cause" "$recovery"
  stage_persist "$root_cause" "$recovery"
  if [ "${EMIT_CD_RESULT:-0}" = "1" ]; then
    local rb="none"
    case "${ROLLBACK_STATE:-}" in OK*) rb="ok" ;; FAILED*) rb="failed" ;; esac
    echo "CD_RESULT=aborted ref=${REF:-} stage=${STAGE} service_interrupted=${SERVICE_INTERRUPTED} rollback=${rb}"
  fi
  exit "$code"
}

#: Read back what update.sh persisted. Sets STAGE / SERVICE_INTERRUPTED / ROOT_CAUSE, leaving the
#: caller's values untouched when the file is absent — a missing file means update.sh died before it
#: could write one, which is itself worth saying rather than papering over.
function stage_load_state() {
  CHILD_STAGE=""; CHILD_ROOT_CAUSE=""; CHILD_INTERRUPTED=""
  [ -f "$DEPLOY_STATE_FILE" ] || return 1
  CHILD_STAGE="$(grep -m1 '^STAGE=' "$DEPLOY_STATE_FILE" 2>/dev/null | cut -d= -f2-)"
  CHILD_INTERRUPTED="$(grep -m1 '^SERVICE_INTERRUPTED=' "$DEPLOY_STATE_FILE" 2>/dev/null | cut -d= -f2-)"
  CHILD_ROOT_CAUSE="$(grep -m1 '^ROOT_CAUSE=' "$DEPLOY_STATE_FILE" 2>/dev/null | cut -d= -f2-)"
  return 0
}

function stage_clear_state() {
  rm -f "$DEPLOY_STATE_FILE" 2>/dev/null || true
}

#: Map an update.sh exit code back to a stage name, for the case where the state file is missing.
function stage_from_exit_code() {
  case "$1" in
    "$EXIT_GIT_FETCH")         printf 'GIT_FETCH' ;;
    "$EXIT_GIT_CHECKOUT")      printf 'GIT_CHECKOUT' ;;
    "$EXIT_BUILD")             printf 'BUILD' ;;
    "$EXIT_CONTAINER_STARTUP") printf 'CONTAINER_STARTUP' ;;
    "$EXIT_READINESS")         printf 'READINESS' ;;
    "$EXIT_SMOKE")             printf 'SMOKE' ;;
    *)                         printf 'UNKNOWN' ;;
  esac
}

#: Did a failure at this stage stop the previous deployment? Used when only the exit code survived.
function stage_is_disruptive() {
  case "$1" in
    CONTAINER_STARTUP|READINESS|SMOKE|ROLLBACK) return 0 ;;
    *) return 1 ;;
  esac
}
