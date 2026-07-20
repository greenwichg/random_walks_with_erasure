#!/usr/bin/env bash
# BR1 — scheduled, retained, optionally off-host SQLite backup for the closed beta.
#
# Operational only: this REUSES `examples/db_backup.py` (the existing consistent-online backup) and
# changes no application behavior. Run it from the repo root on a host that has Python + access to the
# database (the non-Docker production path), or schedule it (cron / systemd / the compose
# `backup-scheduler` profile — see docs/BETA_LAUNCH_CHECKLIST.md).
#
# Environment (same DB selection the engine uses):
#   RWE_DB_URL            database to back up (default: the repo's data/ih_beta.db)
#   RWE_BACKUP_DIR        where backups are written (default: backups/ beside the DB)
#   BACKUP_KEEP           how many most-recent LOCAL backups to retain (default: 48 → ~2 days hourly)
#   BACKUP_OFFHOST_CMD    optional off-host shipment; runs once per backup with the file path as "$1"
#                         e.g. 'aws s3 cp "$1" s3://acme-ih-backups/'  or  'rclone copy "$1" remote:ih'
#
# Exit 0 on a successful local backup (off-host failure is logged but non-fatal — the local snapshot
# still exists); non-zero if the backup itself failed.
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root (deploy/ops -> ../..)

KEEP="${BACKUP_KEEP:-48}"
now() { date -u +%FT%TZ; }
log() { printf '{"event":"br1_backup","ts":"%s",%s}\n' "$(now)" "$1"; }

# Resolve the backup directory exactly as db_backup.py does (RWE_BACKUP_DIR, else beside the DB).
dir="${RWE_BACKUP_DIR:-$(python -c "import sys,os;sys.path.insert(0,'examples');import store;print(store.default_backup_dir(os.environ.get('RWE_DB_URL') or store.default_db_url()))")}"

# 1) Consistent online backup (integrity-checked inside db_backup.py). Fatal if this fails.
if ! python examples/db_backup.py backup ${RWE_BACKUP_DIR:+--out "$RWE_BACKUP_DIR"}; then
  log '"backup":"FAILED"'
  exit 1
fi
dest="$(ls -1t "$dir"/*.db 2>/dev/null | head -1 || true)"
if [ -z "$dest" ] || [ ! -f "$dest" ]; then
  log '"backup":"FAILED","reason":"no backup file found"'
  exit 1
fi

# 2) Off-host (optional but STRONGLY recommended — a lost volume must not take the only copy with it).
if [ -n "${BACKUP_OFFHOST_CMD:-}" ]; then
  if BACKUP_FILE="$dest" sh -c "$BACKUP_OFFHOST_CMD" _ "$dest"; then
    log "\"offhost\":\"ok\",\"file\":\"$dest\""
  else
    log "\"offhost\":\"FAILED\",\"file\":\"$dest\""   # local backup still succeeded → not fatal
  fi
fi

# 3) Retention — keep the newest $KEEP local backups; delete the rest (off-host copies are unaffected).
deleted=0
while IFS= read -r old; do
  [ -n "$old" ] || continue
  rm -f "$old" && deleted=$((deleted + 1))
done < <(ls -1t "$dir"/*.db 2>/dev/null | tail -n +"$((KEEP + 1))")

log "\"backup\":\"ok\",\"file\":\"$dest\",\"kept\":$KEEP,\"pruned\":$deleted"
