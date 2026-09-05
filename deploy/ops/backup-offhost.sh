#!/usr/bin/env bash
# Off-host backup + integrity verification for the AWS deployment. DEPLOYMENT-ONLY.
#
# NO host Python: the backup and the PRAGMA integrity check run INSIDE the `backup` container (which has
# Python + SQLAlchemy + the same bind-mounted /app/data); the off-host copy is plain `aws s3 sync` on the
# host using the instance IAM role. This replaces the host-Python deploy/ops/backup.sh + verify-restore.sh
# on the Docker/EC2 path (the EC2 host has neither `python` nor SQLAlchemy).
#
# Used by the hourly cron installed by bootstrap-ec2.sh, and manually at go-live / on demand:
#   deploy/ops/backup-offhost.sh              # verify the newest local backup + sync all backups → S3
#   deploy/ops/backup-offhost.sh --backup-now # ALSO take a fresh consistent backup first (go-live/manual)
#
# Env (deploy/.env): IH_S3_BUCKET (off-host target, bucket name only), IH_DATA_DIR (default /opt/ih/data),
# ALERT_WEBHOOK (optional — failures are alerted). The in-container `backup-scheduler` profile still makes
# and prunes local backups on its own schedule; this script verifies + ships them off-host.
set -uo pipefail
# shellcheck source=deploy/ops/_compose.sh
source "$(dirname "$0")/_compose.sh"
need_env

DATA_DIR="$(env_val IH_DATA_DIR)"; DATA_DIR="${DATA_DIR:-/opt/ih/data}"
S3="$(env_val IH_S3_BUCKET)"
mkdir -p "$DATA_DIR/backups"

fail() { alert "backup-offhost: $1"; exit 1; }

# 1) Optionally take a fresh consistent, integrity-checked backup (in-container → /app/data/backups).
if [ "${1:-}" = "--backup-now" ]; then
  echo "== backup-offhost: taking a fresh in-container backup =="
  backup_run backup || fail "in-container backup failed"
fi

# 2) Verify the NEWEST local backup non-destructively, INSIDE the container (reads the backup copy only).
newest="$(ls -1t "$DATA_DIR"/backups/*.db "$DATA_DIR"/backups/*.db.gz 2>/dev/null | head -1 || true)"
if [ -z "$newest" ]; then
  echo "backup-offhost: no local backups yet (scheduler may not have run) — nothing to verify/ship."
else
  base="$(basename "$newest")"
  echo "== backup-offhost: verifying $base (container, non-destructive) =="
  # `verify` rather than `status --db sqlite:///…`: a .db.gz cannot be opened as a database, and
  # an exit code is a contract a shell can trust where a grep for "quickCheck ok" is not.
  #
  # But PROBE for it instead of assuming it. This script lives on the host and moves with every
  # `git checkout`; the image it drives is profile-gated and, until update.sh began building
  # profiles, was rebuilt by nobody. On 2026-07-29 that skew deadlocked a deploy — the new host
  # script called `verify` against an 8-day-old image, cd-deploy's BACKUP stage aborted, and the
  # deploy that would have refreshed the image could never run because it needed that backup.
  #
  # The fallback is coherent rather than a guess: an image without `verify` also predates
  # compression, so its backups are plain `.db` and the older `status` check can read them. The two
  # capabilities shipped together.
  if backup_run --help 2>&1 | grep -qw verify; then
    backup_run verify "/app/data/backups/$base" || fail "integrity check FAILED for $base"
  else
    echo "backup-offhost: image predates \`db_backup.py verify\` — falling back to the older check."
    case "$base" in
      *.gz) fail "cannot verify $base: the image predates BOTH verify and gzip, so a .gz here means an image/script mismatch. Rebuild: docker compose --profile backup build" ;;
    esac
    status="$(backup_run --db "sqlite:////app/data/backups/$base" status 2>&1)" || fail "verify command failed for $base"
    echo "$status"
    echo "$status" | grep -i quickcheck | grep -qiw ok || fail "integrity check FAILED for $base"
  fi
fi

# 3) Ship all local backups off-host to S3 (instance IAM role; no --delete so S3 keeps full history until
#    its own lifecycle expires objects).
if [ -n "$S3" ]; then
  echo "== backup-offhost: syncing $DATA_DIR/backups → s3://$S3/backups/ =="
  aws s3 sync "$DATA_DIR/backups/" "s3://$S3/backups/" || fail "aws s3 sync to s3://$S3/backups/ failed"
else
  echo "backup-offhost: IH_S3_BUCKET unset — skipping off-host sync (set it in deploy/.env before go-live)."
fi

# 4) Ship the archive (docs/NEWS_INTELLIGENCE_INFRASTRUCTURE.md §E.5) — written by retention's
#    archive-before-delete and examples/archive_export.py. Under archive/, OUTSIDE the backups/ prefix the
#    S3 lifecycle rule tiers and expires, so history is never tiered away. Absent directory = nothing to ship.
if [ -n "$S3" ] && [ -d "$DATA_DIR/archive" ]; then
  echo "== backup-offhost: syncing $DATA_DIR/archive → s3://$S3/archive/ =="
  aws s3 sync "$DATA_DIR/archive/" "s3://$S3/archive/" || fail "aws s3 sync to s3://$S3/archive/ failed"
fi

echo "backup-offhost: done."
