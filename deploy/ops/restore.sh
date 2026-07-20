#!/usr/bin/env bash
# Restore the live SQLite database from a backup — VERIFY-FIRST. DEPLOYMENT-ONLY.
#
# Reuses examples/db_backup.py inside the `backup` container (which has Python + the store deps and the
# same bind-mounted /app/data). The restore itself is the app's existing safe restore: it re-checks
# integrity, snapshots the current DB to *.pre-restore, then atomically swaps. This wrapper adds an
# up-front non-destructive integrity check, halts writes during the swap, and re-validates after.
#
#   deploy/ops/restore.sh s3://my-ih-beta-backups/backups/ih_beta-<ts>.db   # pull from S3 and restore
#   deploy/ops/restore.sh /opt/ih/data/backups/ih_beta-<ts>.db             # restore a local backup
#   deploy/ops/restore.sh                                                   # restore the NEWEST local backup
#   FORCE=1 deploy/ops/restore.sh <src>                                     # skip the confirmation prompt
set -euo pipefail
# shellcheck source=deploy/ops/_compose.sh
source "$(dirname "$0")/_compose.sh"

need_env

DATA_DIR="$(env_val IH_DATA_DIR)"; DATA_DIR="${DATA_DIR:-/opt/ih/data}"
mkdir -p "$DATA_DIR/backups"

SRC="${1:-}"

# 1) Resolve the source into a file that lives under $DATA_DIR (so the container sees it at /app/data/…).
if [ -z "$SRC" ]; then
  SRC="$(ls -1t "$DATA_DIR"/backups/*.db 2>/dev/null | head -1 || true)"
  [ -n "$SRC" ] || { echo "restore: no local backups in $DATA_DIR/backups" >&2; exit 1; }
  echo "restore: using newest local backup: $SRC"
fi

case "$SRC" in
  s3://*)
    dest="$DATA_DIR/restore-$(date -u +%Y%m%dT%H%M%SZ).db"
    echo "restore: downloading $SRC → $dest"
    aws s3 cp "$SRC" "$dest"
    LOCAL="$dest" ;;
  "$DATA_DIR"/*)
    LOCAL="$SRC" ;;                                   # already reachable by the container
  *)
    [ -f "$SRC" ] || { echo "restore: file not found: $SRC" >&2; exit 1; }
    dest="$DATA_DIR/restore-$(date -u +%Y%m%dT%H%M%SZ)-$(basename "$SRC")"
    cp "$SRC" "$dest"
    LOCAL="$dest" ;;
esac

CONTAINER_PATH="/app/data/${LOCAL#"$DATA_DIR"/}"
CONTAINER_DB_URL="sqlite:///$CONTAINER_PATH"          # 3 slashes + absolute path = sqlite:////app/data/…

# 2) Prove the backup is intact BEFORE touching the live DB (non-destructive; reads the copy only).
echo "== restore: integrity check on $CONTAINER_PATH =="
status="$(backup_run --db "$CONTAINER_DB_URL" status)"
echo "$status"
echo "$status" | grep -i quickcheck | grep -qiw ok || {
  echo "restore: ABORT — backup failed the integrity check; live DB untouched." >&2; exit 2; }

# 3) Confirm (destructive to the live DB, though the current DB is snapshotted to *.pre-restore first).
if [ "${FORCE:-0}" != "1" ]; then
  printf 'Restore %s over the LIVE database? Current DB will be snapshotted first. [y/N] ' "$CONTAINER_PATH"
  read -r ans; case "$ans" in y|Y|yes|YES) ;; *) echo "restore: cancelled."; exit 0 ;; esac
fi

# 4) Halt writes, swap, bring the stack back, re-validate.
echo "== restore: stopping web + api to halt writes =="
dc stop web api

echo "== restore: swapping in the backup (snapshots current → *.pre-restore) =="
backup_run restore "$CONTAINER_PATH"

echo "== restore: restarting the stack =="
dc up -d
wait_ready 240

echo "== restore: post-restore smoke test =="
"$OPS_DIR/smoke-test.sh" || { echo "restore: smoke test reported issues — investigate." >&2; exit 1; }
echo "✅ restore complete and verified."
