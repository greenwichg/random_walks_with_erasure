# Backup & Restore — Wave 0 (SQLite on EBS → S3)

The durability plan for the closed beta: consistent, integrity-checked SQLite backups on a schedule,
shipped **off-host to S3** (a lost EBS volume must never take the only copy), with a **verify-first**
restore that is rehearsed before launch. Everything reuses the existing `examples/db_backup.py` (the
app's own consistent online backup) — no application change.

> The database is a single SQLite file (`/opt/ih/data/ih_beta.db`, WAL mode) bind-mounted from the host,
> so backups land at `/opt/ih/data/backups/` — **visible on the host** for `aws s3` without adding an AWS
> CLI to the container image.

## Layers

| Layer | Mechanism | Retention |
|---|---|---|
| **Local, scheduled** | compose `backup-scheduler` profile (hourly `db_backup.py backup` + prune to `BACKUP_KEEP`) — started by `deploy/ops/deploy.sh` | newest `BACKUP_KEEP` (default 48 ≈ 2 days hourly) |
| **Off-host, S3** | host cron `deploy/ops/backup.sh` (or `aws s3 sync`) → versioned private S3 bucket via the instance IAM role | S3 lifecycle (default 30 days) |
| **On demand** | `docker compose … run --rm backup` or `deploy/ops/backup.sh` | as above |

## One-time S3 setup

Private, versioned bucket + a 30-day lifecycle; the instance IAM role writes to it (no static keys).
```bash
aws s3api create-bucket --bucket my-ih-beta-backups --region us-east-1
aws s3api put-bucket-versioning --bucket my-ih-beta-backups --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket my-ih-beta-backups \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
# 30-day lifecycle on backups/ — set via console or a put-bucket-lifecycle-configuration JSON rule.
```
Set `IH_S3_BUCKET=my-ih-beta-backups` in `deploy/.env`. The instance IAM role needs
`s3:PutObject`/`s3:GetObject`/`s3:ListBucket` on `arn:aws:s3:::my-ih-beta-backups[/*]` (deployment guide §2.2).

## Scheduled backups

Local recurring backups start with the deploy (scheduler profile). For the **off-host** copy, add a host
cron (survives even if Docker is down):
```bash
# /etc/cron.d/ih-backup  — hourly local backup + S3 sync (instance IAM role supplies creds)
17 * * * * ubuntu cd /opt/ih && set -a && . deploy/.env && set +a && \
  IH_S3_BUCKET="$IH_S3_BUCKET" BACKUP_OFFHOST_CMD='aws s3 cp "$1" s3://'"$IH_S3_BUCKET"'/backups/' \
  deploy/ops/backup.sh >> /var/log/ih-backup.log 2>&1
```
`deploy/ops/backup.sh` writes one integrity-checked backup, ships it off-host (`BACKUP_OFFHOST_CMD`), and
prunes to `BACKUP_KEEP`. Alternatively, sync the whole dir: `aws s3 sync /opt/ih/data/backups s3://$IH_S3_BUCKET/backups/`.

## Manual backup (anytime)
```bash
cd /opt/ih
deploy/ops/backup.sh                       # host path: writes + prunes + off-host (if configured)
# or, purely in-container:
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.aws.yml --env-file deploy/.env \
  run --rm backup python examples/db_backup.py backup
aws s3 ls s3://$IH_S3_BUCKET/backups/       # confirm the object landed off-host
```

## Verify a backup (non-destructive — do this regularly)
```bash
deploy/ops/verify-restore.sh               # newest local backup: copies to scratch, PRAGMA quick_check, opens the store
deploy/ops/verify-restore.sh /opt/ih/data/backups/ih_beta-<ts>.db   # a specific file
```
Exit 0 = the backup is intact and restorable. This never touches the live DB.

## Restore (recover from data loss / corruption)

**`deploy/ops/restore.sh` does the whole safe sequence** — verify → halt writes → snapshot current →
swap → restart → re-validate:
```bash
cd /opt/ih
deploy/ops/restore.sh s3://my-ih-beta-backups/backups/ih_beta-<ts>.db   # from S3
deploy/ops/restore.sh /opt/ih/data/backups/ih_beta-<ts>.db             # from a local backup
deploy/ops/restore.sh                                                   # newest local backup
```
What it does: downloads/locates the file under `/opt/ih/data`, runs an integrity check **first** (aborts
if it fails — live DB untouched), asks for confirmation (skip with `FORCE=1`), stops `web`+`api` to halt
writes, runs the app's safe restore (which snapshots the current DB to `*.pre-restore`, then swaps), brings
the stack back, and runs `smoke-test.sh`.

Manual equivalent (if you prefer step-by-step): stop `web`+`api`, `verify-restore.sh <file>`, then
`… run --rm backup python examples/db_backup.py restore /app/data/<file>`, then `up -d`.

## Restore drill (rehearse before go-live — a required gate)
1. Take a backup: `deploy/ops/backup.sh`.
2. Prove it restores non-destructively: `deploy/ops/verify-restore.sh` → exit 0.
3. (Optional, thorough) On a **scratch** copy of the instance or a temp `IH_DATA_DIR`, run a full
   `deploy/ops/restore.sh <backup>` and confirm the app comes up + a known user's data is present.
4. Record the **RTO** (how long the restore took) in the go-live checklist.

## Where things live

| Item | Path |
|---|---|
| Live DB | `/opt/ih/data/ih_beta.db` (+ `-wal`, `-shm`) |
| Local backups | `/opt/ih/data/backups/ih_beta-<ts>.db` |
| Pre-restore snapshot | `/opt/ih/data/ih_beta.db.pre-restore` (auto, on restore) |
| Off-host | `s3://$IH_S3_BUCKET/backups/` (versioned) |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `verify-restore.sh` non-zero | Backup corrupt/truncated | Use an older backup or the S3 copy; investigate the source |
| `restore.sh` aborts at "integrity check" | Chosen backup failed quick_check | Pick another (older / S3) backup |
| No objects in S3 | IAM role missing `s3:PutObject`, or `IH_S3_BUCKET` unset | Fix the role/policy; set `IH_S3_BUCKET`; re-run `backup.sh` |
| Disk filling | `BACKUP_KEEP` too high / no S3 offload / logs | Lower `BACKUP_KEEP`; enable S3 sync; log rotation (`deploy/host/daemon.json`) |
