#!/usr/bin/env bash
# Unattended CI/CD deployment — the ONE entry point GitHub Actions invokes on this host (via SSM
# RunShellScript). DEPLOYMENT-ONLY: a thin orchestration over the EXISTING lifecycle scripts —
# nothing here builds, probes, or rolls back in a new way; it sequences what operators already run
# by hand and makes the failure path automatic:
#
#   1. SNAPSHOT   a consistent SQLite backup BEFORE any code moves (backup-offhost.sh --backup-now
#                 when an S3 bucket is configured — integrity-checked + shipped off-host — else a
#                 local backup via the backup container). The DB is out-of-band of code deploys
#                 (host bind-mount), so this is belt-and-braces, not a migration step.
#   2. DEPLOY     deploy/ops/update.sh <ref> — checkout, build, converge, readiness gate (240 s),
#                 smoke test. Exactly the manual path; same idempotence.
#   3. ROLLBACK   on ANY deploy failure, update.sh <previously-serving-commit> automatically, then
#                 alert (ALERT_WEBHOOK, when configured). A rollback that itself fails alerts
#                 loudly and exits 2 — a human is needed, and the message says exactly where it
#                 stopped.
#
# Output contract (parsed by .github/workflows/deploy.yml — keep these lines stable):
#   CD_RESULT=deployed ref=<sha>            exit 0
#   CD_RESULT=rolled_back from=<sha> to=<sha>   exit 1
#   CD_RESULT=rollback_failed from=<sha> to=<sha>   exit 2
#
#   deploy/ops/cd-deploy.sh <git-ref>
set -uo pipefail
# shellcheck source=deploy/ops/_compose.sh
source "$(dirname "$0")/_compose.sh"
# shellcheck source=deploy/ops/_stages.sh
source "$(dirname "$0")/_stages.sh"

ROLLBACK_STATE="not reached"
# cd-deploy owns the CD_RESULT contract line; update.sh must never print one (see stage_fail).
EMIT_CD_RESULT=1

REF="${1:-}"
if [ -z "$REF" ]; then
  echo "usage: cd-deploy.sh <git-ref>" >&2
  exit 64
fi

need_env

# SSM runs this as root while /opt/ih is owned by the operator user — without this, every git
# command fails with "detected dubious ownership". Idempotent; scoped to this repo only.
git config --global --add safe.directory "$REPO_ROOT" 2>/dev/null || true

LAST_GOOD="$(git rev-parse HEAD)"
echo "== cd-deploy: currently serving ${LAST_GOOD} — requested ${REF} =="

stage_enter PREFLIGHT "checks that can abort before anything moves"
stage_clear_state

# Everything in this stage is READ-ONLY. If any of it fails, no code has moved, no container has
# been touched, and the previous deployment is still serving — which the report says explicitly, so
# nobody pages anybody.

# -- the docker daemon must be answering, or every later stage fails confusingly --
if ! docker info >/dev/null 2>&1; then
  evidence_from "systemctl status docker" systemctl is-active docker
  stage_fail "the docker daemon is not responding" \
"1. Check it:    systemctl status docker
2. Start it:    sudo systemctl start docker
3. Retry. Nothing has moved — but note the RUNNING SITE is also down if the daemon died under it." 3
fi

# -- a dirty working tree blocks `git checkout` and used to surface as a health failure --
DIRTY="$(git status --porcelain --untracked-files=no 2>/dev/null || true)"
if [ -n "$DIRTY" ]; then
  evidence "modified tracked files:"$'\n'"$(printf '%s\n' "$DIRTY" | sed 's/^/    /')"
  stage_fail "the working tree has local modifications that can block 'git checkout ${REF}'" \
"git refuses to check out a ref when a locally-modified file is also changed by that commit. The
commonest way in is 'git checkout <ref> -- path/to/script', to pick up one newer ops script without
a full deploy — it STAGES the file, and only bites when a later deploy touches the same path.

Aborting rather than stashing on your behalf: these edits could be a hotfix someone is mid-way
through, and a deploy tool must not silently discard work it does not understand.

1. Inspect:  git -C ${REPO_ROOT} status --porcelain
2. Discard:  git -C ${REPO_ROOT} checkout HEAD -- .
     ('git checkout -- .' is NOT enough — it restores the worktree from the index and leaves a
      staged change exactly where it was.)
3. Retry:    bash deploy/ops/cd-deploy.sh ${REF}" 3
fi

# -- the data volume, if a dedicated one is configured, must actually be mounted --
DATA_DIR="$(env_val IH_DATA_DIR)"; DATA_DIR="${DATA_DIR:-/opt/ih/data}"
if [ "$(env_val IH_DATA_MOUNT)" = "1" ] && ! mountpoint -q "$DATA_DIR" 2>/dev/null; then
  stage_fail "IH_DATA_MOUNT=1 but '${DATA_DIR}' is not a mounted filesystem" \
"Refusing to deploy: the bind-mount would land on an empty directory of the root disk and the app
would create a FRESH EMPTY DATABASE. This is the data-loss guard, working.

1. Mount it:  sudo mount '${DATA_DIR}'   (check /etc/fstab)
2. Verify:    mountpoint '${DATA_DIR}'
3. Retry. Nothing has moved." 3
fi

# -- free disk, because a build that fills the disk fails in the least legible way available --
AVAIL_MB="$(df -Pm "${REPO_ROOT}" 2>/dev/null | awk 'NR==2 {print $4}')"
if [ -n "${AVAIL_MB:-}" ] && [ "$AVAIL_MB" -lt "${CD_MIN_FREE_MB:-2048}" ]; then
  evidence_from "disk" df -h "${REPO_ROOT}"
  evidence_from "docker disk usage" docker system df
  stage_fail "only ${AVAIL_MB} MB free on the deploy volume (need >= ${CD_MIN_FREE_MB:-2048} MB)" \
"An image build needs headroom; running out mid-build produces an error that names a compiler or a
package manager rather than the disk, which is a slow thing to diagnose at speed.

1. Reclaim:  docker system prune -af      (UNUSED images and build cache only — no volumes)
2. Check:    df -h ${REPO_ROOT}
3. Retry. Nothing has moved." 3
fi

echo "  preflight OK — worktree clean, docker up, data mount present, $((AVAIL_MB)) MB free"

stage_enter BACKUP "pre-deploy database snapshot"
BACKUP_OK=1
if [ -n "$(env_val IH_S3_BUCKET)" ]; then
  # Consistent backup + PRAGMA integrity check + off-host S3 sync (the hourly cron's own path).
  "$OPS_DIR/backup-offhost.sh" --backup-now || BACKUP_OK=0
else
  backup_run backup || BACKUP_OK=0
fi
if [ "$BACKUP_OK" -ne 1 ]; then
  evidence_from "disk" df -h "${REPO_ROOT}"
  alert "cd-deploy [BACKUP] ABORTED before any code moved — pre-deploy snapshot failed. Site UNAFFECTED, still serving ${LAST_GOOD}."
  stage_fail "the pre-deploy database snapshot failed" \
"Deliberately fatal. The snapshot is what makes the deploy reversible without data risk, so a deploy
that cannot take one does not proceed.

1. Read the backup output above — a failed PRAGMA integrity check means a DATA fault, and
   deploy/ops/restore.sh is the tool for that, not this one.
2. Disk full is the other common cause (attached above).
3. Retry once the snapshot succeeds. Nothing has moved — the previous deployment is still serving." 3
fi

echo ""
echo "== cd-deploy: handing off to update.sh for ${REF} =="
"$OPS_DIR/update.sh" "$REF"
UPDATE_RC=$?
if [ "$UPDATE_RC" -eq 0 ]; then
  stage_clear_state
  alert "cd-deploy [SUCCESS] deployed ${REF} — smoke green."
  echo "CD_RESULT=deployed ref=$(git rev-parse HEAD) stage=SUCCESS service_interrupted=0 rollback=none"
  exit 0
fi

# Recover the child's stage. The state file carries the detail; the exit code is the fallback for
# the case where update.sh was killed before it could write one — which is itself worth naming
# rather than reporting as a generic failure.
if stage_load_state && [ -n "${CHILD_STAGE:-}" ]; then
  FAILED_STAGE="$CHILD_STAGE"
  FAILED_CAUSE="${CHILD_ROOT_CAUSE:-unknown}"
  INTERRUPTED="${CHILD_INTERRUPTED:-0}"
else
  FAILED_STAGE="$(stage_from_exit_code "$UPDATE_RC")"
  FAILED_CAUSE="update.sh exited ${UPDATE_RC} without leaving a stage record (killed, or crashed before reporting)"
  if stage_is_disruptive "$FAILED_STAGE"; then INTERRUPTED=1; else INTERRUPTED=0; fi
fi
STAGE="$FAILED_STAGE"
SERVICE_INTERRUPTED="$INTERRUPTED"

# ROLLBACK IS ONLY RUN WHEN THE SERVICE IS ACTUALLY DOWN.
#
# It used to run unconditionally, which is how a refused `git checkout` produced a "rollback" that
# re-deployed the commit already checked out and then announced a health-gate failure. Redeploying
# a healthy stack is not free: it stops containers that were serving perfectly well, turning a
# harmless failure into real downtime.
if [ "$SERVICE_INTERRUPTED" -eq 0 ]; then
  ROLLBACK_STATE="NOT NEEDED — the previous deployment never stopped"
  alert "cd-deploy [${FAILED_STAGE}] deploy of ${REF} failed BEFORE any container moved. Site UNAFFECTED, still serving ${LAST_GOOD}. No rollback attempted. Cause: ${FAILED_CAUSE}"
  echo ""
  echo "  Not rolling back: the failure happened at ${FAILED_STAGE}, before the running stack was" >&2
  echo "  touched. Redeploying a healthy stack would cause the downtime this failure avoided." >&2
  echo "CD_RESULT=aborted ref=${REF} stage=${FAILED_STAGE} service_interrupted=0 rollback=none"
  exit 3
fi

stage_enter ROLLBACK "restoring ${LAST_GOOD} — the site is down until this completes"
SERVICE_INTERRUPTED=1
if "$OPS_DIR/update.sh" "$LAST_GOOD"; then
  ROLLBACK_STATE="OK — ${LAST_GOOD} restored and smoke-green"
  alert "cd-deploy [${FAILED_STAGE}] deploy of ${REF} failed AFTER containers were replaced — AUTO-ROLLED-BACK to ${LAST_GOOD}, now serving and smoke-green. Cause: ${FAILED_CAUSE}"
  echo "CD_RESULT=rolled_back from=${REF} to=${LAST_GOOD} stage=${FAILED_STAGE} service_interrupted=1 rollback=ok"
  exit 1
fi

ROLLBACK_STATE="FAILED — ${LAST_GOOD} did not come back"
alert "cd-deploy [${FAILED_STAGE}] deploy of ${REF} failed AND rollback to ${LAST_GOOD} FAILED — THE SITE IS DOWN, MANUAL INTERVENTION REQUIRED. Cause: ${FAILED_CAUSE}"
stage_report_failure "rollback to ${LAST_GOOD} did not restore service after a failure at ${FAILED_STAGE}" \
"THE SITE IS DOWN AND AUTOMATION HAS RUN OUT OF MOVES.

1. Container states:  cd ${REPO_ROOT} && docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.aws.yml --env-file deploy/.env ps
2. Engine logs:       ... logs --tail 100 api
3. The rollback ran update.sh ${LAST_GOOD} and it also failed — read ITS stage report above; the
   stage it names is the real fault and it is not specific to ${REF}.
4. A pre-deploy DB snapshot from before this attempt is in the backups directory. Code faults do
   not need it; use deploy/ops/restore.sh only for a DATA fault."
echo "CD_RESULT=rollback_failed from=${REF} to=${LAST_GOOD} stage=${FAILED_STAGE} service_interrupted=1 rollback=failed"
exit 2
