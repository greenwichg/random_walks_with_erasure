# Suspend / Resume

Shut Hidden View down when it isn't needed and bring it back with no data loss, no DNS change, and
no manual recovery. Two approval-gated GitHub Actions workflows drive it; the host halves reuse the
existing ops scripts, so nothing about normal deploys changes.

**Cost: ~$40/month running → ~$7.50/month suspended (≈81% saved).**

---

## The architectural decision: stop, not destroy

The requirement said "destroy **or stop** compute". On this architecture stopping is decisively
right, and the reason is one line of `terraform/compute.tf`:

```hcl
root_block_device { delete_on_termination = true, volume_size = 30, ... }
```

**There is no separate data volume.** `IH_DATA_MOUNT=0`: the SQLite database at `/opt/ih/data`, the
Docker images, and Caddy's TLS certificates all live on the instance's *root* volume. Destroying the
instance destroys the database. (The existing `prevent_destroy = true` on the instance is what
currently prevents exactly that.)

Making a destroy-based suspend safe would require: provision a dedicated EBS volume, migrate the
live database onto it, wire attachment + mount automation, and re-point the app — a real data
migration on a production system. What it would buy:

| | Running | Stop-based suspend | Destroy-based suspend |
|---|---:|---:|---:|
| EC2 t3.medium | $30.37 | **$0** | **$0** |
| Root EBS 30 GiB gp3 | $2.40 | $2.40 | $0 (replaced by a data volume ~$1.60) |
| Public IPv4 (EIP) | $3.65 | $3.65 | $3.65 |
| Route 53 hosted zone | $0.50 | $0.50 | $0.50 |
| S3 backups | ~$1 | ~$1 | ~$1 |
| CloudWatch | ~$2 | ~$0 | ~$0 |
| **Total** | **~$40** | **~$7.55** | **~$6.75** |

Destroying buys about **$0.80/month more** than stopping, in exchange for a live data migration, an
attach/mount failure mode on every resume, and a `terraform destroy` in a workflow. That trade is
not worth taking. **Stop-based suspend captures 97% of the available saving with none of the risk**,
and because the volume is never detached there is no "reattach" step that can go wrong.

> If the economics ever change (a much larger volume, or many environments), the refactor is
> described under *State separation* below. The scripts here would not need to change.

---

## What is destroyed, stopped, and preserved

**Stopped (billing ends, state intact)**
- The EC2 instance `i-01d221c5b7b7920ed`. Compute charges stop at `stopped`.

**Nothing is destroyed.** No resource is deleted, detached, or terminated by either workflow.

**Preserved — every persistent thing**

| Resource | How it survives |
|---|---|
| SQLite database (`/opt/ih/data`) | on the root EBS volume, still attached to the stopped instance |
| Root EBS volume (30 GiB) | never detached; `delete_on_termination` never comes into play because the instance is never terminated |
| Docker images + named volumes (incl. Caddy's `caddy_data`) | on the same volume — TLS certificates survive, so no re-issuance and no Let's Encrypt rate-limit exposure |
| S3 off-host backups | untouched; `prevent_destroy` added |
| Elastic IP `3.86.118.17` | stays **associated** across stop/start (an EIP is not released by stopping); `prevent_destroy` added |
| Route 53 hosted zone + A record | untouched; already `prevent_destroy` |
| Secrets (`deploy/.env`) | a host file on the preserved volume — never leaves the instance |
| Terraform state (S3) | untouched; stopping an instance produces **no** Terraform drift (instance state is not a managed argument) |
| IAM roles, security group, instance profile | untouched |

The deploy role's IAM policy grants `ec2:StartInstances` / `ec2:StopInstances` **only**, tag-scoped
to `Name=ih-beta`. It has no `TerminateInstances`, no `DeleteVolume`, no `DetachVolume` — the
workflows *cannot* destroy the data volume even if a future edit asked them to.

---

## Operating it

### Suspend

**Actions → Suspend Environment → Run workflow →** type `SUSPEND` → approve the `production`
environment prompt.

1. **Final backup first** — `backup-offhost.sh --backup-now`: a consistent online backup, a PRAGMA
   integrity check, and an S3 copy. **If this fails the workflow aborts and the app keeps serving** —
   we never suspend a system whose last known-good backup failed.
2. **Graceful stop** — `docker compose stop` (not `down`): SQLite closes cleanly and the WAL is
   checkpointed. Containers, volumes, networks and certificates all remain.
3. **Marker** — `/opt/ih/data/.suspended` records who, when, and which commit.
4. **Then** the control-plane `aws ec2 stop-instances`, waiting for `stopped`.

### Resume

**Actions → Resume Environment → Run workflow →** type `RESUME` (optionally a git ref to deploy on
the way up) → approve.

1. Start the instance, wait for `running`, then **wait for the SSM agent to register** (a fresh boot
   isn't immediately reachable — the workflow polls up to 10 minutes).
2. **Integrity before serving** — a non-destructive `quick_check` on the newest backup inside the
   backup container. A failure aborts *before* anything is exposed, with a pointer to
   `deploy/ops/restore.sh`.
3. Optional checkout, `compose up -d --build`, backup scheduler re-enabled.
4. **Readiness gate** (300 s) then the full **smoke test** — containers, engine live/ready, PA1
   gating, metrics, public HTTPS + redirect. Only then is the marker cleared.

Both scripts are runnable by hand (`deploy/ops/suspend.sh`, `deploy/ops/resume.sh <ref>`) — useful
if an instance was started from the console.

### Why the app does not auto-start on boot

Suspend stops the containers deliberately, and `restart: unless-stopped` honours an explicit stop
across a reboot. That is the safer default: a stray console start cannot silently resume serving
behind a live certificate without the integrity check and smoke test having run. **Starting the
instance from the AWS console alone leaves the application down** — run `deploy/ops/resume.sh` (or
the workflow). The 5-minute `monitor.sh` cron will alert if a host is up with the app down.

---

## Idempotence

| Action | Run twice |
|---|---|
| Suspend workflow | second run backs up again, finds the stack stopped, `stop-instances` on a stopped instance is a no-op |
| Resume workflow | `start-instances` on a running instance is a no-op; `compose up -d` converges; integrity + smoke re-run |
| `resume.sh` by hand | safe on an already-serving host — re-verifies and re-smokes |
| `terraform plan` | unchanged either way — no drift from a stopped instance |

---

## Risks and limitations

1. **Ingestion stops while suspended.** No polling, so the catalog has a gap for the suspended
   window. Providers serve *current* news on resume; the gap is not backfilled. For a long
   suspension the corpus will be stale for the first cycle or two (RSS 10 min, most APIs 15 min).
2. **Reads while suspended fail.** The site is down — Caddy isn't running, so it's connection-refused
   rather than a friendly page. If a maintenance page matters, that's a separate (small) piece of
   work: a static page on a second host or an S3/CloudFront fallback.
3. **The MediaStack daily budget resets by UTC day, not by uptime.** A suspend/resume inside one day
   still consumes that day's allowance for cycles already spent.
4. **Instance-store / public DNS**: none — the EIP means the address is stable. Stopping and starting
   *does* move the instance to different underlying hardware; that is transparent for EBS-backed
   instances.
5. **A stop is not a snapshot.** The volume persists, but nothing protects against volume-level
   failure while suspended. The pre-suspend S3 backup is that protection, which is why a failed
   backup aborts the suspension. For a long suspension, consider an explicit EBS snapshot as well.
6. **Approval is the only gate on suspending a live system.** The typed confirmation plus the
   `production` environment reviewer are deliberate; don't remove them.
7. **Clock/cert edges**: Let's Encrypt certificates renew ~30 days before expiry. A suspension longer
   than ~60 days could resume with an expired certificate; Caddy re-issues on start, but DNS must
   still resolve to the EIP (it will). Suspensions beyond a month deserve a post-resume check of
   the public HTTPS smoke line.

---

## State separation / module restructuring — not required

For stop-based suspend, **Terraform is not part of the cycle at all**: nothing is created or
destroyed, and a stopped instance produces no plan drift. The current single-state layout is
therefore fine as-is, and this change adds only lifecycle guards (S3 bucket, EIP) to the ones the
instance and Route 53 record already had.

Splitting state *would* be justified if the destroy-based approach is ever adopted:

```
terraform/persistent/   S3 bucket, Route 53 zone + record, EIP allocation, IAM, data EBS volume
terraform/compute/      instance, EIP association, volume attachment, security group
```

…so that `terraform destroy` in `compute/` cannot even reference a persistent resource, with the
persistent state referenced through `terraform_remote_state`. That is a real refactor of a live,
import-only configuration whose current guarantee is plan-to-zero — worth doing only alongside the
data-volume migration it exists to support, and not for the $0.80/month it would save today.
