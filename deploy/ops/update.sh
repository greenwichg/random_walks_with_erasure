#!/usr/bin/env bash
# Update to (or roll back to) a specific release, then re-deploy. DEPLOYMENT-ONLY.
#
# IDEMPOTENT: checks out the requested ref and converges the running stack to it. User data is untouched
# (the DB lives on the host bind-mount, independent of the code checkout).
#
#   deploy/ops/update.sh v1.2.0        # deploy a new release tag
#   deploy/ops/update.sh <prev-tag>    # ROLLBACK: redeploy the previous good tag
#   deploy/ops/update.sh <sha>         # deploy an exact commit
#   deploy/ops/update.sh <branch>      # deploy ORIGIN's tip of that branch (never the local branch)
#   deploy/ops/update.sh               # rebuild the current checkout (no ref change)
#
# A branch name means origin's tip, and the checkout is always DETACHED at a commit. See the comment
# at the ref-resolution step: taking a branch name literally once rolled production back 272 commits
# without a single failing check.
#
# For a DATA fault (corruption), use deploy/ops/restore.sh instead — this script only moves code.
#
# STAGED. Every failure names the stage it happened in, its root cause, the evidence, the recovery
# steps, and — the question that decides whether anyone needs to get out of bed — whether the
# previous deployment is still serving. See deploy/ops/_stages.sh for the state machine.
#
# `set -e` is deliberately NOT used here any more: every step is checked explicitly so a failure is
# reported as a STAGE rather than as an unexplained non-zero exit. Under `set -e` the script died
# before it could say anything, which is exactly how a refused `git checkout` came to be reported as
# a failed health gate.
set -uo pipefail
# shellcheck source=deploy/ops/_compose.sh
source "$(dirname "$0")/_compose.sh"
# shellcheck source=deploy/ops/_stages.sh
source "$(dirname "$0")/_stages.sh"

need_env
stage_clear_state

REF="${1:-}"
LAST_GOOD="$(git rev-parse HEAD 2>/dev/null || echo unknown)"

# ── GIT_FETCH ────────────────────────────────────────────────────────────────────────────────────
# Read-only with respect to the running stack AND the working tree: a failure here has changed
# nothing at all, anywhere.
if [ -n "$REF" ]; then
  stage_enter GIT_FETCH "fetching refs from origin"
  if ! git fetch --tags --prune origin 2>&1 | sed 's/^/    /'; then
    evidence_from "git remote -v" git remote -v
    stage_fail "could not fetch from origin — network, DNS, or credentials" \
"1. Check egress from this host:   curl -sS -o /dev/null -w '%{http_code}\\n' https://github.com
2. Check the remote is right:     git -C ${REPO_ROOT} remote -v
3. Retry. NOTHING has changed — the previous deployment is untouched and still serving." \
      "$EXIT_GIT_FETCH"
  fi

  # Resolve the ref BEFORE checking out, so an unknown ref is named as such rather than arriving as
  # a confusing checkout error two steps later.
  #
  # A BRANCH NAME RESOLVES TO THE REMOTE'S TIP, not to the local branch of the same name. `git fetch`
  # updates `origin/<branch>` and deliberately leaves the local branch alone, so a bare `git checkout
  # <branch>` here deployed whatever that local branch happened to point at — which, on a box that
  # normally deploys detached at a tag or sha, is wherever it was left months ago. That is not a
  # stale-code bug that announces itself: the checkout succeeds, the build is a cache hit because the
  # context matches an image already built, the smoke test passes because the old code is healthy,
  # and the script truthfully reports "now serving <old sha>". It rolled production BACKWARDS 272
  # commits once, and the only symptom was a feature being absent from a container that had just
  # been "deployed".
  TARGET="$REF"
  if git rev-parse --verify --quiet "refs/remotes/origin/${REF}^{commit}" >/dev/null; then
    TARGET="origin/${REF}"
    [ "$(git rev-parse --quiet --verify "refs/heads/${REF}" 2>/dev/null)" = \
      "$(git rev-parse "refs/remotes/origin/${REF}")" ] || \
      evidence "'${REF}' is a branch — deploying origin's tip" \
               "$(git log --oneline -1 "refs/remotes/origin/${REF}")"
  fi
  if ! git rev-parse --verify --quiet "${TARGET}^{commit}" >/dev/null; then
    evidence "requested ref: ${REF}"
    evidence_from "recent commits on origin" git log --oneline -5 FETCH_HEAD
    stage_fail "ref '${REF}' does not exist in this repository after fetching" \
"1. Confirm the sha/tag was pushed:  git -C ${REPO_ROOT} log --oneline -5 origin/<branch>
2. Re-run with a ref that exists. NOTHING has changed — the previous deployment is still serving." \
      "$EXIT_GIT_FETCH"
  fi
  # Always land on a COMMIT, never on a branch. A deployment is a specific build of specific code;
  # leaving the box on a branch invites the next `git checkout <same branch>` to mean something
  # different from what it meant today.
  TARGET="$(git rev-parse "${TARGET}^{commit}")"

  # ── GIT_CHECKOUT ───────────────────────────────────────────────────────────────────────────────
  # Moves code ON DISK. The running containers still serve the OLD image until `dc up -d`, so a
  # failure here leaves a MIXED state — checkout possibly new, service definitely old — and the
  # recovery text has to say that out loud rather than leave it to be inferred.
  stage_enter GIT_CHECKOUT "checking out ${REF} ($(git rev-parse --short "$TARGET"))"
  if ! co_out="$(git checkout --detach "$TARGET" 2>&1)"; then
    evidence "git checkout said:"$'\n'"$(printf '%s\n' "$co_out" | sed 's/^/    /')"
    evidence_from "working tree" git status --porcelain --untracked-files=no
    stage_fail "git refused to check out '${REF}'" \
"Most often: a tracked file is modified locally AND is also changed by the target commit. That state
is easy to reach by grabbing one newer file — 'git checkout <ref> -- path/to/script' — which STAGES
it, and it only bites when a later deploy happens to touch the same file.

1. See what is modified:  git -C ${REPO_ROOT} status --porcelain
2. Discard local edits:   git -C ${REPO_ROOT} checkout HEAD -- .
     (HEAD as the source is required. 'git checkout -- .' restores the worktree from the INDEX and
      leaves a staged change exactly where it was.)
3. Retry the deploy.

The previous deployment was never stopped — the containers are still running the old image." \
      "$EXIT_GIT_CHECKOUT"
  fi
  printf '%s\n' "$co_out" | sed 's/^/    /'
else
  stage_enter GIT_CHECKOUT "rebuilding current checkout ($(git rev-parse --short HEAD))"
fi

# ── BUILD ────────────────────────────────────────────────────────────────────────────────────────
# Split out of `dc up -d --build` ON PURPOSE. Compose builds and THEN recreates, so the combined
# command straddles the only boundary an operator cares about: a build failure leaves the old
# containers serving, a startup failure does not. Reported as one step they are indistinguishable,
# and the difference is "fix it tomorrow" versus "the site is down".
stage_enter BUILD "building images"
# --profile backup --profile scheduler: those services are profile-gated, so a bare `dc build`
# SKIPS them and their images go stale while the host scripts that drive them keep moving with the
# repo. That skew is not hypothetical — it deadlocked a deploy on 2026-07-29: cd-deploy's BACKUP
# stage runs BEFORE the build, so the new host script called a `db_backup.py verify` subcommand that
# the 8-day-old backup image did not have, and the deploy that would have fixed the image could
# never get past the backup that needed it. Building every service keeps host and container code on
# the same commit, which is the only durable fix.
if ! dc --profile backup --profile scheduler build 2>&1 | sed 's/^/    /'; then
  evidence_from "disk" df -h "${REPO_ROOT}"
  evidence_from "docker disk usage" docker system df
  stage_fail "one or more images failed to build" \
"1. Read the build output above — the failing step names the service and the command.
2. Common causes: a compile or lint error in the new code, a base image that cannot be pulled, or a
   full disk ('docker system df' and 'df -h' are attached above).
3. If the disk is full:  docker system prune -af   (removes UNUSED images/build cache only)

NO ROLLBACK IS NEEDED. The previous deployment was never stopped — the old containers are still
running and serving traffic. Fix forward at your own pace." \
  "$EXIT_BUILD"
fi

# ── CONTAINER_STARTUP ────────────────────────────────────────────────────────────────────────────
# THE POINT OF NO RETURN. `dc up -d` stops the old containers and starts the new ones. From here on
# a failure means the site is down until rollback completes, and every message below says so.
stage_enter CONTAINER_STARTUP "starting containers — the previous deployment stops here"
service_now_interrupted
if ! dc up -d 2>&1 | sed 's/^/    /'; then
  evidence_from "container states" dc ps
  evidence_from "api logs" dc logs --tail 40 api
  evidence_from "ingest logs (runs to completion before api starts)" dc logs --tail 20 ingest
  stage_fail "containers failed to start after the old ones were stopped" \
"THE SITE IS DOWN until this is resolved or rolled back.

1. Container states and the last logs are attached above.
2. Frequent causes: a missing or invalid value in deploy/.env (the engine refuses to start with
   RWE_ENV=production and no RWE_INTERNAL_SECRET), the one-shot 'ingest' service exiting non-zero
   (api waits on it completing successfully), or a host port already bound.
3. To roll back by hand:  bash deploy/ops/update.sh ${LAST_GOOD}
   cd-deploy does this automatically when it is the caller." \
  "$EXIT_CONTAINER_STARTUP"
fi

# A RUNNING container keeps its old image after a rebuild — `up -d` only recreates services it
# manages, and `backup-scheduler` is profile-gated so a bare `up -d` does not manage it. Left alone
# it runs 8-day-old code indefinitely: the same skew that deadlocked the 2026-07-29 deploy, one
# layer down. Recreated ONLY when it is already running, so this never STARTS an opt-in service that
# an operator deliberately left off — and never touches the one-shot `backup` service, which is a
# `run --rm` job and would become a crash-looping container if `up -d` ever managed it.
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q 'backup-scheduler'; then
  echo "    refreshing backup-scheduler onto the newly built image"
  dc --profile scheduler up -d backup-scheduler 2>&1 | sed 's/^/    /' || \
    echo "    (backup-scheduler refresh failed — recurring backups may still run older code)" >&2
fi

# ── READINESS ────────────────────────────────────────────────────────────────────────────────────
# The timeout is configurable so the stage tests can exercise a readiness FAILURE without waiting
# four minutes for it, and so an operator on a slow box can widen it without editing the script.
READY_TIMEOUT="${RWE_DEPLOY_READY_TIMEOUT:-240}"
stage_enter READINESS "waiting for the engine to report ready (timeout ${READY_TIMEOUT}s)"
if ! wait_ready "$READY_TIMEOUT"; then
  evidence_from "container states" dc ps
  evidence_from "api logs" dc logs --tail 60 api
  stage_fail "the engine did not report /api/health/ready within ${READY_TIMEOUT}s" \
"THE SITE IS DOWN until this is resolved or rolled back.

1. The api logs are attached above. A traceback at the end is a startup fault; no output at all
   means the container is not running — check 'container states'.
2. SLOW is not the same as BROKEN. Measured cold-start on a populated catalog is ~5 s, so this
   timeout expiring means something is wrong rather than merely loaded — but if the box was already
   busy, check whether it came ready just after the timeout before rolling back.
   Widen it for a slow host with RWE_DEPLOY_READY_TIMEOUT=<seconds>.
3. To roll back by hand:  bash deploy/ops/update.sh ${LAST_GOOD}" \
  "$EXIT_READINESS"
fi

# ── SMOKE ────────────────────────────────────────────────────────────────────────────────────────
stage_enter SMOKE "post-deploy smoke test"
if ! "$OPS_DIR/smoke-test.sh"; then
  evidence "the smoke test's PASS/WARN/FAIL lines are above — each FAIL names its own check"
  evidence_from "container states" dc ps
  stage_fail "the new deployment started and became ready, but failed a hard smoke check" \
"The stack is UP and answering, but a contract check failed — it is running and WRONG, which is
worse than being down. Roll back unless you know the failing check is a false alarm.

1. Each FAIL above names its check (container, liveness, readiness, analytics gating, TLS).
2. Re-run it alone once fixed:  bash deploy/ops/smoke-test.sh
3. To roll back by hand:  bash deploy/ops/update.sh ${LAST_GOOD}" \
  "$EXIT_SMOKE"
fi

stage_enter SUCCESS "deployment complete"
stage_clear_state
echo ""
echo "✅ now serving $(git describe --tags --always 2>/dev/null || git rev-parse --short HEAD)."
