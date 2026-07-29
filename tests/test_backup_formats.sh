#!/usr/bin/env bash
# Backups are gzipped now, and SIX shell consumers globbed '*.db'. Each would have silently matched
# nothing — and a retention pass that matches nothing does not error, it just stops pruning, which
# is how a volume fills. This drives the real scripts against a real mixed directory.
#
#   bash tests/test_backup_formats.sh
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (want '$3', got '$2')"; fi; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
DATA="$TMP/data"; mkdir -p "$DATA/backups"

# A real database, and real backups of it in BOTH formats.
python - "$DATA" <<'PY'
import sys, os
sys.path.insert(0, "examples")
import store
data = sys.argv[1]
db = os.path.join(data, "ih_beta.db")
s = store.Store(f"sqlite:///{db}")
s.upsert_user_by_identity("dev", "fmt-1", email="fmt@x", display_name="R")
os.environ["RWE_BACKUP_COMPRESS"] = "1"
gz = store.create_backup(f"sqlite:///{db}", out_dir=os.path.join(data, "backups"))
os.environ["RWE_BACKUP_COMPRESS"] = "0"
pl = store.create_backup(f"sqlite:///{db}", out_dir=os.path.join(data, "backups"))
print(os.path.basename(gz)); print(os.path.basename(pl))
PY
GZ="$(ls "$DATA/backups" | grep '\.db\.gz$' | head -1)"
PL="$(ls "$DATA/backups" | grep '\.db$' | head -1)"
[ -n "$GZ" ] && ok "fixture: a .db.gz backup exists" || bad "fixture: no .db.gz written"
[ -n "$PL" ] && ok "fixture: a .db backup exists"    || bad "fixture: no .db written"

echo ""
echo "-- store.list_backups sees both --"
SEEN="$(python -c "
import sys; sys.path.insert(0,'examples'); import store
b = store.list_backups('$DATA/backups')
print(len(b), sorted(x['compressed'] for x in b))")"
check "list_backups returns both formats" "$SEEN" "2 [False, True]"

echo ""
echo "-- db_backup.py verify handles both --"
python examples/db_backup.py verify "$DATA/backups/$GZ" >/dev/null 2>&1
check "verify accepts .db.gz" "$?" "0"
python examples/db_backup.py verify "$DATA/backups/$PL" >/dev/null 2>&1
check "verify accepts .db" "$?" "0"
printf 'not a backup' > "$TMP/corrupt.db.gz"
python examples/db_backup.py verify "$TMP/corrupt.db.gz" >/dev/null 2>&1
check "verify REJECTS a corrupt .db.gz" "$?" "2"

echo ""
echo "-- prune-backups.sh finds compressed backups --"
# Age the two real backups well past every retention bucket, and add decoys so the pass has
# something it is allowed to delete. Without the glob fix it reports 0 candidates and deletes none.
for i in 1 2 3; do
  cp "$DATA/backups/$GZ" "$DATA/backups/ih_beta-2026010${i}T0300$((i))0Z.db.gz"
  touch -d "2026-01-0${i} 03:0${i}:00" "$DATA/backups/ih_beta-2026010${i}T0300$((i))0Z.db.gz"
done
cat > "$TMP/env" <<EOF
IH_DATA_DIR=$DATA
BACKUP_KEEP_HOURLY=1
BACKUP_KEEP_DAILY=1
BACKUP_KEEP_WEEKLY=0
BACKUP_KEEP_MONTHLY=0
EOF
mkdir -p "$TMP/repo/deploy/ops"
cp deploy/ops/prune-backups.sh deploy/ops/_compose.sh "$TMP/repo/deploy/ops/"
cp "$TMP/env" "$TMP/repo/deploy/.env"
OUT="$(cd "$TMP/repo" && bash deploy/ops/prune-backups.sh --dry-run 2>&1)"
if echo "$OUT" | grep -qi 'db\.gz'; then ok "prune-backups sees .db.gz files"
else bad "prune-backups did not mention any .db.gz — the glob is still '*.db' only"; fi
BEFORE="$(ls "$DATA/backups" | wc -l)"
(cd "$TMP/repo" && bash deploy/ops/prune-backups.sh >/dev/null 2>&1)
AFTER="$(ls "$DATA/backups" | wc -l)"
if [ "$AFTER" -lt "$BEFORE" ]; then ok "prune-backups actually deletes compressed backups ($BEFORE -> $AFTER)"
else bad "prune-backups deleted nothing ($BEFORE -> $AFTER) — retention is silently inert"; fi
if ls "$DATA/backups" | grep -q "$GZ"; then ok "the newest backup is always kept"
else bad "the newest backup was deleted"; fi

echo ""
echo "-- verify-restore.sh decompresses into its scratch copy --"
export RWE_BACKUP_DIR="$DATA/backups"
OUT="$(cd "$ROOT" && bash deploy/ops/verify-restore.sh "$DATA/backups/$GZ" 2>&1)"; RC=$?
check "verify-restore.sh exits 0 on a .db.gz" "$RC" "0"
if echo "$OUT" | grep -q "store opened"; then ok "verify-restore.sh opened the decompressed copy"
else bad "verify-restore.sh did not open the copy: $OUT"; fi
unset RWE_BACKUP_DIR

echo ""
echo "== backup formats: $PASS PASS, $FAIL FAIL =="
[ "$FAIL" -eq 0 ]
