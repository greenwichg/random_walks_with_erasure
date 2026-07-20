#!/usr/bin/env bash
# BR1 — restore VERIFICATION (non-destructive). Proves the newest backup is actually restorable and
# intact WITHOUT touching the live database, so "we have backups" is never an untested assumption.
#
# Operational only: reuses `examples/db_backup.py status` (which runs PRAGMA quick_check on any chosen
# DB) plus a store-open sanity check. Changes no application behavior. Run from the repo root.
#
#   deploy/ops/verify-restore.sh                 # verify the newest backup in the default/RWE_BACKUP_DIR
#   deploy/ops/verify-restore.sh /path/backup.db # verify a specific backup file
#
# Exit 0 = the backup opens and passes integrity + a core-table sanity check; non-zero = investigate.
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root

dir="${RWE_BACKUP_DIR:-$(python -c "import sys,os;sys.path.insert(0,'examples');import store;print(store.default_backup_dir(os.environ.get('RWE_DB_URL') or store.default_db_url()))")}"
backup="${1:-$(ls -1t "$dir"/*.db 2>/dev/null | head -1 || true)}"
if [ -z "$backup" ] || [ ! -f "$backup" ]; then
  echo "verify-restore: no backup file found (looked in: $dir)" >&2
  exit 1
fi

scratch_dir="$(mktemp -d)"
scratch="$scratch_dir/verify.db"
trap 'rm -rf "$scratch_dir"' EXIT
cp "$backup" "$scratch"
echo "verify-restore: checking $backup  (non-destructive copy → $scratch)"

# 1) Integrity — db_backup.py status runs PRAGMA quick_check on the chosen DB (`--db` is a
#    top-level flag, so it precedes the subcommand).
status="$(python examples/db_backup.py --db "sqlite:///$scratch" status)"
echo "$status"
if ! echo "$status" | grep -i quickCheck | grep -qw ok; then
  echo "verify-restore: FAILED — integrity check did not report ok" >&2
  exit 2
fi

# 2) Sanity — the store opens the restored copy and core tables are queryable.
python -c "
import sys, os; sys.path.insert(0, 'examples'); import store
s = store.Store('sqlite:///$scratch')
s.get_user(1)                     # opens + queries users (None is fine)
n = s.count_analytics_events()    # queries a PA1 table
print(f'verify-restore: store opened; analytics_events={n}')
"

echo "verify-restore: OK — backup is intact and restorable"
