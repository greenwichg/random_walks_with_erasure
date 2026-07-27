#!/usr/bin/env bash
# Bring the application back after the instance has been started. DEPLOYMENT-ONLY.
#
#   deploy/ops/resume.sh [ref]     # optional git ref to deploy on the way back up
#
# The host half of the resume workflow (.github/workflows/resume.yml), which starts the instance and
# waits for SSM before invoking this. Safe to run by hand at any time — including on a machine that
# was started from the AWS console, which is exactly the case it exists to rescue.
#
# Why the app does NOT come back on its own: suspend stops the containers deliberately, and
# `restart: unless-stopped` honours an explicit stop across a reboot. That is the safer default — a
# stray console start can't silently resume serving stale content behind a live TLS certificate —
# and it gives resume a place to VERIFY before anything is exposed:
#
#   1. INTEGRITY FIRST — a PRAGMA quick_check on the newest backup, inside the backup container (the
#      same non-destructive check the hourly cron runs). If the disk came back wrong we stop here,
#      before serving a corrupt database.
#   2. Optional CHECKOUT of a ref, then bring the stack up and gate on engine readiness.
#   3. SMOKE TEST the running stack end-to-end, then clear the suspend marker.
#
# IDEMPOTENT: `compose up -d` converges, so re-running on an already-serving host is a no-op that
# still re-verifies integrity and re-runs the smoke test.
set -uo pipefail
# shellcheck source=deploy/ops/_compose.sh
source "$(dirname "$0")/_compose.sh"

need_env

REF="${1:-}"
DATA_DIR="$(env_val IH_DATA_DIR)"; DATA_DIR="${DATA_DIR:-/opt/ih/data}"
MARKER="$DATA_DIR/.suspended"

# SSM runs as root while /opt/ih belongs to the operator — same guard cd-deploy.sh needs.
git config --global --add safe.directory "$REPO_ROOT" 2>/dev/null || true

if [ -f "$MARKER" ]; then
  echo "== resume: suspend marker found =="
  cat "$MARKER"
else
  echo "resume: no suspend marker (host may have been started outside the workflow) — continuing."
fi

# The data directory must exist and, on the dedicated-volume layout, actually be mounted before the
# bind-mount can see the database. Reuses the same guard deploy.sh runs.
assert_data_mount "$DATA_DIR"

echo "== resume: verifying database integrity BEFORE serving =="
newest="$(ls -1t "$DATA_DIR"/backups/*.db 2>/dev/null | head -1 || true)"
if [ -z "$newest" ]; then
  echo "resume: WARNING — no local backup to verify (fresh volume?). Continuing; the live DB is checked by the smoke test."
else
  base="$(basename "$newest")"
  echo "resume: quick_check on $base (non-destructive, in-container)"
  status="$(backup_run --db "sqlite:////app/data/backups/$base" status 2>&1)" || {
    echo "$status"; echo "RESUME_RESULT=failed reason=integrity_command_failed" >&2
    alert "resume ABORTED: integrity verification could not run — NOT serving"; exit 1; }
  echo "$status"
  echo "$status" | grep -i quickcheck | grep -qiw ok || {
    echo "RESUME_RESULT=failed reason=integrity_failed" >&2
    alert "resume ABORTED: DATABASE INTEGRITY CHECK FAILED — NOT serving; restore from S3 (deploy/ops/restore.sh)"
    exit 1; }
fi

if [ -n "$REF" ]; then
  echo "== resume: checking out $REF =="
  git fetch --tags --prune origin && git checkout "$REF" || {
    echo "RESUME_RESULT=failed reason=checkout_failed" >&2; exit 1; }
fi

echo "== resume: starting the stack =="
dc up -d --build || { echo "RESUME_RESULT=failed reason=up_failed" >&2; exit 1; }
dc --profile scheduler up -d backup-scheduler || true    # recurring local backups, as deploy.sh does

wait_ready 300 || {
  echo "RESUME_RESULT=failed reason=not_ready" >&2
  alert "resume FAILED: the engine did not become ready — check 'dc logs api'"
  exit 1; }

echo "== resume: post-resume smoke test =="
if "$OPS_DIR/smoke-test.sh"; then
  rm -f "$MARKER" 2>/dev/null || true
  echo ""
  echo "resume: serving $(git describe --tags --always 2>/dev/null || git rev-parse --short HEAD)."
  echo "RESUME_RESULT=ok"
  exit 0
fi

echo "RESUME_RESULT=failed reason=smoke_failed" >&2
alert "resume FAILED: the stack started but the smoke test did not pass — investigate before announcing availability"
exit 1
