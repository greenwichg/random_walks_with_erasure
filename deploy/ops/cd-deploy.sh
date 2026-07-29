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

# PRE-FLIGHT: a dirty working tree, reported as itself.
#
# update.sh runs `git checkout "$REF"` under `set -euo pipefail`. If a tracked file has been
# modified locally AND the target commit also changes that file, git refuses the checkout, update.sh
# aborts, and cd-deploy rolls back — announcing that the deploy "failed its health/smoke gate" when
# no container ever moved and nothing was ever health-checked. That message cost a full round trip
# to diagnose: the symptom seen from outside was a missing database index, which looked like a
# database problem and was actually a checkout that never happened.
#
# It is an easy state to reach by accident. Grabbing a single newer file — `git checkout <ref> --
# deploy/ops/some-script.sh`, to run an updated ops script without a full deploy — leaves exactly
# this, and only bites when a later deploy happens to touch the same file.
#
# Refuse early, name the files, and print the command that clears it. Aborting is right rather than
# stashing on the operator's behalf: those edits might be a hotfix someone is mid-way through, and
# a deploy tool must not silently discard work it does not understand.
DIRTY="$(git status --porcelain --untracked-files=no 2>/dev/null || true)"
if [ -n "$DIRTY" ]; then
  echo "== cd-deploy: ABORTING — the working tree has local modifications ==" >&2
  printf '%s\n' "$DIRTY" | sed 's/^/    /' >&2
  echo "" >&2
  echo "  update.sh would run 'git checkout ${REF}', and git refuses that when a modified file is" >&2
  echo "  also changed by the target commit. Nothing has been deployed and nothing was rolled back." >&2
  echo "" >&2
  echo "  To discard these local edits and retry:" >&2
  # `git checkout HEAD -- .`, not `git checkout -- .`. The commonest way to reach this state is
  # `git checkout <ref> -- <path>`, which STAGES the file, and restoring the worktree from the index
  # leaves a staged change exactly where it was. Naming HEAD as the source resets both.
  echo "      git -C ${REPO_ROOT} checkout HEAD -- ." >&2
  echo "      bash deploy/ops/cd-deploy.sh ${REF}" >&2
  alert "cd-deploy: ABORTED before touching anything — ${REPO_ROOT} has local modifications that would block 'git checkout ${REF}'. Still serving ${LAST_GOOD}."
  echo "CD_RESULT=aborted ref=${REF} reason=dirty_worktree"
  exit 3
fi

echo "== cd-deploy: pre-deploy database snapshot =="
if [ -n "$(env_val IH_S3_BUCKET)" ]; then
  # Consistent backup + PRAGMA integrity check + off-host S3 sync (the hourly cron's own path).
  "$OPS_DIR/backup-offhost.sh" --backup-now || {
    alert "cd-deploy: pre-deploy backup FAILED — aborting before any code moved (still serving ${LAST_GOOD})"
    echo "CD_RESULT=aborted ref=${REF}"
    exit 3
  }
else
  backup_run backup || {
    alert "cd-deploy: pre-deploy backup FAILED — aborting before any code moved (still serving ${LAST_GOOD})"
    echo "CD_RESULT=aborted ref=${REF}"
    exit 3
  }
fi

echo "== cd-deploy: deploying ${REF} =="
if "$OPS_DIR/update.sh" "$REF"; then
  echo "CD_RESULT=deployed ref=$(git rev-parse HEAD)"
  exit 0
fi

echo "== cd-deploy: deploy FAILED — rolling back to ${LAST_GOOD} ==" >&2
if "$OPS_DIR/update.sh" "$LAST_GOOD"; then
  alert "cd-deploy: deploy of ${REF} failed its health/smoke gate — AUTO-ROLLED-BACK to ${LAST_GOOD} (serving, healthy)"
  echo "CD_RESULT=rolled_back from=${REF} to=${LAST_GOOD}"
  exit 1
fi

alert "cd-deploy: deploy of ${REF} failed AND rollback to ${LAST_GOOD} failed — MANUAL INTERVENTION REQUIRED (check 'dc ps' + api logs; DB snapshot from before the attempt is in the backups dir)"
echo "CD_RESULT=rollback_failed from=${REF} to=${LAST_GOOD}"
exit 2
