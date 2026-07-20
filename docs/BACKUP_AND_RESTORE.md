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
| **Off-host, S3 (automatic)** | **hourly cron** `deploy/ops/backup-offhost.sh` (installed by `bootstrap-ec2.sh`): verifies the newest backup **in the container**, then `aws s3 sync … → s3://$IH_S3_BUCKET/backups/` via the instance IAM role | S3 lifecycle (default 30 days) |
| **On demand** | `deploy/ops/backup-offhost.sh --backup-now` (backup + verify + ship) | as above |

> **No host Python.** Backups and the integrity check run **inside the `backup` container** (which has
> Python + SQLAlchemy); the EC2 host only runs `aws s3 sync`. The host-Python scripts (`deploy/ops/backup.sh`,
> `verify-restore.sh`) remain for the *non-Docker* path and are **not** used on EC2.

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

## Scheduled backups (automatic)

Two cooperating layers are set up automatically — **no manual cron editing**:
1. **Local backups**: the compose `backup-scheduler` profile (started by `deploy/ops/deploy.sh`) makes and
   prunes local backups hourly, in-container.
2. **Off-host S3**: `bootstrap-ec2.sh` installs `/etc/cron.d/ih-offhost-backup`, which runs
   `deploy/ops/backup-offhost.sh` hourly (at :23). That script **verifies** the newest backup inside the
   container, then `aws s3 sync`s the backups dir to `s3://$IH_S3_BUCKET/backups/` via the instance IAM role.

Confirm both after deploy:
```bash
ls -l /etc/cron.d/ih-offhost-backup            # the off-host cron
tail -f /var/log/ih-backup.log                 # its output
```

## Manual backup (anytime)
```bash
cd /opt/ih
deploy/ops/backup-offhost.sh --backup-now      # in-container backup + integrity check + S3 sync (no host Python)
aws s3 ls s3://$IH_S3_BUCKET/backups/           # confirm the object landed off-host
```

## Verify a backup (non-destructive — done every hour by the cron, or on demand)
```bash
deploy/ops/backup-offhost.sh                   # verifies the NEWEST local backup in the container + syncs to S3
# a specific file, directly in the container:
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.aws.yml --env-file deploy/.env \
  --profile backup run --rm backup python examples/db_backup.py --db sqlite:////app/data/backups/ih_beta-<ts>.db status
```
`quickCheck ok` = the backup is intact and restorable; this never touches the live DB. (The host-Python
`deploy/ops/verify-restore.sh` is for the non-Docker path only — the EC2 host has no `python`/SQLAlchemy.)

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

Manual equivalent (if you prefer step-by-step): stop `web`+`api`, verify with `… --profile backup run --rm
backup python examples/db_backup.py --db sqlite:////app/data/<file> status`, then `… run --rm backup python
examples/db_backup.py restore /app/data/<file>`, then `up -d`.

## Restore drill (rehearse before go-live — a required gate)
1. Take + verify + ship a backup: `deploy/ops/backup-offhost.sh --backup-now` → exit 0.
2. Confirm it's intact off-host: `aws s3 ls s3://$IH_S3_BUCKET/backups/`.
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
| `backup-offhost.sh` verify FAIL | Backup corrupt/truncated | Use an older backup or the S3 copy; investigate the source |
| `restore.sh` aborts at "integrity check" | Chosen backup failed quick_check | Pick another (older / S3) backup |
| No objects in S3 | IAM role missing `s3:PutObject`, or `IH_S3_BUCKET` unset | Fix the role/policy; set `IH_S3_BUCKET`; re-run `backup.sh` |
| Disk filling | `BACKUP_KEEP` too high / no S3 offload / logs | Lower `BACKUP_KEEP`; enable S3 sync; log rotation (`deploy/host/daemon.json`) |
