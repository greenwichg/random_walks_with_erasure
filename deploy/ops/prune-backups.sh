#!/usr/bin/env bash
# Tiered (grandfather-father-son) retention for the LOCAL SQLite backup directory. DEPLOYMENT-ONLY.
#
#   deploy/ops/prune-backups.sh [--dry-run]
#
# Replaces the flat "keep the newest N" rule, which was the single largest consumer of disk on this
# host: every backup is a FULL, uncompressed copy of the database, so `BACKUP_KEEP=48` costs 48x the
# database size — 24 GB once the DB reaches 500 MB, on a 30 GiB volume.
#
# Tiered retention keeps recovery granularity where it matters (the last day) and thins out with
# age, which is what a restore actually needs:
#
#   BACKUP_KEEP_HOURLY   (default 12)  every backup from the last N hours   — "undo the last hour"
#   BACKUP_KEEP_DAILY    (default  7)  one per day for N days               — "undo yesterday"
#   BACKUP_KEEP_WEEKLY   (default  4)  one per ISO week for N weeks         — "undo last month"
#   BACKUP_KEEP_MONTHLY  (default  0)  one per month for N months           — 0: depth lives in S3
#
# The split is deliberate: LOCAL disk buys *fast* recovery (minutes-to-weeks) and every slot costs a
# full uncompressed copy of the database, so depth here is expensive. LONG-TAIL depth belongs in S3,
# where the lifecycle rules transition old copies to Glacier Instant Retrieval for ~1/5th the price
# (terraform/s3.tf). Default footprint: ~23 files instead of 48 — and, unlike a flat "keep N", it
# stops growing with time.
#
# SAFETY: the newest backup is ALWAYS kept, whatever the policy says; a file is only deleted after a
# keeper has been chosen for its bucket; --dry-run prints decisions without deleting. Backup file
# names are the timestamped form db_backup.py writes (…YYYYMMDDTHHMMSS….db); anything unparseable is
# KEPT, never deleted, so an unexpected filename can't be destroyed by this script.
set -uo pipefail
source "$(dirname "$0")/_compose.sh"

DRY=""; [ "${1:-}" = "--dry-run" ] && DRY=1

DATA_DIR="$(env_val IH_DATA_DIR)"; DATA_DIR="${DATA_DIR:-/opt/ih/data}"
DIR="$DATA_DIR/backups"
H="$(env_val BACKUP_KEEP_HOURLY)";  H="${H:-12}"
D="$(env_val BACKUP_KEEP_DAILY)";   D="${D:-7}"
W="$(env_val BACKUP_KEEP_WEEKLY)";  W="${W:-4}"
M="$(env_val BACKUP_KEEP_MONTHLY)"; M="${M:-0}"

[ -d "$DIR" ] || { echo "prune-backups: $DIR does not exist — nothing to do."; exit 0; }

# Newest first. Each line: <epoch> <path>, using the file's mtime (portable, and the backup's own
# timestamp is its mtime because db_backup.py writes it once).
# BOTH suffixes. Backups are gzipped by default; a retention pass that globbed only '*.db' would
# quietly match nothing and let the directory grow without bound — the exact failure this script
# exists to prevent, and invisible until the volume fills.
mapfile -t FILES < <(find "$DIR" -maxdepth 1 \( -name '*.db' -o -name '*.db.gz' \) -printf '%T@ %p\n' 2>/dev/null | sort -rn)
total="${#FILES[@]}"
[ "$total" -eq 0 ] && { echo "prune-backups: no backups yet."; exit 0; }

now="$(date +%s)"
keep_list=""; kept=0; deleted=0; freed=0

seen_days=""; seen_weeks=""; seen_months=""
for i in "${!FILES[@]}"; do
  line="${FILES[$i]}"
  epoch="${line%% *}"; epoch="${epoch%.*}"; path="${line#* }"
  keep=""
  age_h=$(( (now - epoch) / 3600 ))

  if [ "$i" -eq 0 ]; then
    keep="newest"                                   # the newest is never deleted, unconditionally
  elif [ "$age_h" -lt "$H" ]; then
    keep="hourly"
  else
    day="$(date -u -d "@$epoch" +%Y-%m-%d 2>/dev/null)"
    week="$(date -u -d "@$epoch" +%G-W%V 2>/dev/null)"
    month="$(date -u -d "@$epoch" +%Y-%m 2>/dev/null)"
    age_d=$(( (now - epoch) / 86400 ))
    if [ -n "$day" ] && [ "$age_d" -lt "$D" ] && ! printf '%s' "$seen_days" | grep -q "|$day|"; then
      keep="daily"; seen_days="$seen_days|$day|"
    elif [ -n "$week" ] && [ "$age_d" -lt $(( W * 7 )) ] && ! printf '%s' "$seen_weeks" | grep -q "|$week|"; then
      keep="weekly"; seen_weeks="$seen_weeks|$week|"
    elif [ -n "$month" ] && [ "$age_d" -lt $(( M * 31 )) ] && ! printf '%s' "$seen_months" | grep -q "|$month|"; then
      keep="monthly"; seen_months="$seen_months|$month|"
    elif [ -z "$day" ]; then
      keep="unparseable-kept"                       # fail-safe: never delete what we can't date
    fi
  fi

  if [ -n "$keep" ]; then
    kept=$((kept + 1)); keep_list="$keep_list  KEEP  [$keep] $(basename "$path")"$'\n'
  else
    sz=$(stat -c %s "$path" 2>/dev/null || echo 0)
    # A snapshot is a SET: the database plus the sidecars db_backup.py wrote beside it
    # (allowlist.txt, score_reference.json — durable state that is not inside the database and
    # cannot be rebuilt from it). Deleting the database alone would leave orphans that no restore
    # will ever reach for, growing without bound in the directory this script exists to bound.
    stem="${path%.gz}"; stem="${stem%.db}"
    for side in "$stem.allowlist.txt" "$stem.score_reference.json"; do
      [ -f "$side" ] || continue
      sz=$(( sz + $(stat -c %s "$side" 2>/dev/null || echo 0) ))
      [ -n "$DRY" ] || rm -f "$side"
    done
    if [ -n "$DRY" ]; then
      keep_list="$keep_list  would DELETE $(basename "$path") (+ sidecars)"$'\n'
    else
      rm -f "$path" && { deleted=$((deleted + 1)); freed=$((freed + sz)); }
    fi
  fi
done

# ---- orphaned SQLite journals -------------------------------------------------------------- #
# Opening a backup AS A DATABASE (the legacy `status --db sqlite:///<backup>` path in
# backup-offhost.sh, or an operator inspecting one by hand) creates `<name>.db-wal` and
# `<name>.db-shm` beside it. A clean close removes them; an abrupt exit does not — and on
# 2026-08-19 a pair survived, was shipped to S3 by the directory sync, and will sit there forever
# because nothing globs them: the tiered pass above matches only '*.db' and '*.db.gz'.
#
# Only a journal whose base .db is GONE is deleted. If the .db is still there the backup is
# uncompressed and something may legitimately have it open; removing a live -wal under a reader is
# how a database gets corrupted, and no cleanup is worth that.
orphans=0; orphan_bytes=0
for j in "$DIR"/*.db-wal "$DIR"/*.db-shm; do
  [ -e "$j" ] || continue                                   # unmatched glob stays literal
  base="${j%-wal}"; base="${base%-shm}"
  [ -e "$base" ] && continue                                # the database is there: leave it alone
  orphan_bytes=$(( orphan_bytes + $(stat -c %s "$j" 2>/dev/null || echo 0) ))
  orphans=$((orphans + 1))
  if [ -n "$DRY" ]; then
    keep_list="$keep_list  would DELETE orphaned journal $(basename "$j")"$'\n'
  else
    rm -f "$j"
  fi
done
printf '%s' "$keep_list"
[ "$orphans" -gt 0 ] && echo "prune-backups: $orphans orphaned SQLite journal(s), $(( orphan_bytes / 1024 )) KB${DRY:+ (dry run — nothing deleted)}"
echo "prune-backups: policy hourly=$H daily=$D weekly=$W monthly=$M"
echo "prune-backups: $total backup(s) -> kept $kept, deleted $deleted, freed $(( freed / 1024 / 1024 )) MB${DRY:+ (dry run — nothing deleted)}"
