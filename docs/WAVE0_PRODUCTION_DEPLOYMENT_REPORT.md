# Wave 0 Production Deployment Report — hidden-view.com

**Status:** ✅ **GO for closed beta** · **Deployed:** 2026-07-20 · **Region:** us-east-1

The execution record of the first real AWS production deployment of Information Health. This is the
"what happened / what we validated / what we fixed" log; the **as-built system of record** (architecture,
AWS resources, security model, disaster-recovery, GA roadmap) is
[`WAVE0_DEPLOYMENT_CLOSEOUT.md`](WAVE0_DEPLOYMENT_CLOSEOUT.md).

> DEPLOYMENT-ONLY. No application logic (recommendation engine, BA1, PA1, OBS1, analytics) was changed.
> The three code changes below are a web→engine auth-header fix, a compose feature-flag wiring, and an
> ops healthcheck/alerting improvement — all deployment-layer.

---

## 1. Deployment summary

A single-instance, two-tier Docker Compose stack was provisioned from scratch on AWS EC2 and brought to a
verified production-ready state for the Wave 0 closed beta. All ten verification phases passed:

| Phase | Area | Result |
|---|---|---|
| 1 | EC2 provisioning (instance, EIP, security group, IAM role, S3, Route 53) | ✅ |
| 2 | Bootstrap (`bootstrap-ec2.sh` — Docker, swap, log rotation, data dir, crons) | ✅ |
| 3 | Environment configuration (`deploy/.env`, secrets, OAuth, allowlist) | ✅ |
| 4 | Docker deployment (`deploy.sh` — build + readiness gate) | ✅ |
| 5 | HTTPS (Let's Encrypt cert, TLS, HTTP→HTTPS redirect) | ✅ smoke 10/10 |
| 6 | OAuth (Google sign-in, BA1 allowlist, per-user pages) | ✅ (after fix) |
| 7 | Backup (fresh backup + integrity + off-host S3 ship; hourly cron flowing) | ✅ |
| 8 | Monitoring (health probes green, 5-min cron firing) | ✅ |
| 9 | Reboot (self-heal, **zero data loss**, HTTPS recovery) | ✅ |
| 10 | Restore / DR (S3 pull → integrity → real data, non-destructive) | ✅ |
| + | Coach v2 enabled + verified engaging for measured readers | ✅ |

The full user journey — **Google OAuth → per-user pages → AI Coach v2**, over valid HTTPS — works, and the
stack survives an unplanned reboot with no operator action and no data loss.

## 2. Infrastructure

| Component | Value |
|---|---|
| Instance | `i-01d221c5b7b7920ed` — t3.medium, Ubuntu 24.04, 30 GiB gp3, AZ `us-east-1a` |
| Public IP | Elastic IP `3.86.118.17` (persists across stop/reboot) |
| DNS | Route 53 zone `Z03237571N84XNOE3T8QU`; apex + `www` A records → the EIP |
| Ingress | Security group `sg-02de782b0941bc1dd` — **only** 80/443 open; shell access via SSM only (no SSH port) |
| Identity | IAM instance role `ih-ec2-role` (S3 backup bucket + SSM + CloudWatch); no static keys on the box |
| Backups | S3 `hidden-view-ih-backups-652615011843` (versioning **enabled**), account `652615011843` |
| App dir | `/opt/ih` (repo checkout); DB + backups bind-mounted from `/opt/ih/data` |
| Runtime | Docker Compose: `caddy` (TLS edge) → `web` (Next.js) → `api` (FastAPI) → SQLite; one-shot `ingest`; `backup-scheduler` |

Container topology (private Docker network; only Caddy publishes host ports):

```
Internet ──443/80──▶ caddy ──http──▶ web:3000 ──http (X-IH-Auth)──▶ api:8000 ──▶ /app/data/ih_beta.db
                     (TLS)          (Next.js)                       (FastAPI)      (host bind-mount)
```

## 3. Validation results

| Check | Evidence |
|---|---|
| HTTPS + redirect | `smoke-test.sh`: **10 PASS / 0 WARN / 0 FAIL**; HTTPS 307, HTTP→HTTPS 308 |
| Engine internal wiring | liveness/readiness 200 in-container; PA1 analytics 404 without secret / 200 with it; OBS1 metrics 200 |
| OAuth + per-user | `/api/internal/users` 200 on sign-in; `/api/me/{history,analytics,profile,settings,saved}` all 200 |
| Backup | `backup ok … integrity check passed`; `quickCheck ok`; `upload: … → s3://…/backups/` |
| Off-host automation | 3 objects in S3, two shipped autonomously by the hourly `ih-offhost-backup` cron |
| Monitoring | `monitor: healthy (api + web + caddy running; engine live + ready)`, exit 0; 5-min cron log confirms |
| Reboot | host uptime reset to minutes; all containers `Up` with no manual action; `users=1 reads=10` preserved; HTTPS 307 |
| Restore (DR) | pulled newest S3 backup → `quick_check: ok`, `users=1 reads=10` (matched live) — non-destructive scratch restore |

## 4. Issues encountered

| # | Symptom | Root cause |
|---|---|---|
| 1 | `bootstrap-ec2.sh` halted; dockerd would not start | `deploy/host/daemon.json` contained an invalid `"//"` comment key — dockerd rejects non-config keys ("directives don't match any configuration option") |
| 2 | Every per-user page (`Saved`, `Reading History`, `Analytics`, `Profile`, `Settings`) returned 401 after a successful Google sign-in | `web/lib/auth.ts` `upsertEngineUser()` POSTed `/api/internal/users` **without** the `X-IH-Auth` secret. In fail-closed production the engine 401'd, so `engineUserId` never landed on the session and every subsequent `/api/me/*` call went out with no credentials → 401. Only manifests in production mode; the dev-login e2e path never exercised it. |
| 3 | AI Coach served the old (v1) experience despite the intent to run v2 | `RWE_COACH_V2` was never wired into the AWS compose. Both compose files pass env via explicit `environment:` blocks (not `env_file`), so a value in `deploy/.env` could not reach the `api` container. |
| — | Coach still "v1" after enabling the flag | **Not a bug.** Coach v2 gates to *measured* readers (`≥ 5 reads`, `ESTIMATE_MIN_READS`); with no seeded demo account in prod, a below-threshold account correctly falls back to v1. Confirmed by adding reads. |

Operational friction (no code): the SSM terminal mangled multi-line pastes (worked around with single-line
commands); a text editor corrupted `deploy/.env` (rebuilt via a `printf` one-liner); Google Cloud required
2-Step Verification + a configured consent screen before an OAuth client could be created.

## 5. Fixes applied

| Root cause | Fix | Commit |
|---|---|---|
| Invalid `daemon.json` key | Removed the `"//"` key → pure Docker config | `ac71fb8` |
| Missing web→engine sign-in auth header | Send `X-IH-Auth` when `RWE_INTERNAL_SECRET` is set (mirrors `engine-auth`'s `internalSecretHeaders`); `tsc --noEmit` clean, web lib tests 103/103 | `1bbdda0` |
| `RWE_COACH_V2` not reaching the container | Wired the flag onto the `api` service in `docker-compose.aws.yml` (**default `0`** — an unset var is a no-op); documented in the env template + `PRODUCTION_ENVIRONMENT.md` | `4565a4c` |
| Caddy healthcheck false-negative; alerting Slack-only + unescaped | Caddy healthcheck → admin-API probe (`127.0.0.1:2019/config/`); `alert()` now JSON-escapes and sends both `text` (Slack) + `content` (Discord); docs + example `.env` | *(this hardening commit)* |

None of the fixes alter application behavior: #2 restores the intended authenticated path, #3 is a config
wiring, and the healthcheck/alerting changes are ops-only.

## 6. Final production readiness assessment

> ## ✅ GO for closed beta.
> All ten verification phases pass. The stack **self-heals across reboots with zero data loss**, backups
> flow off-host hourly and are **proven restorable**, health monitoring runs unattended, and the complete
> Google OAuth → per-user → Coach v2 journey works over valid HTTPS. The remaining risks (below) suit a
> single-instance closed-beta footprint and none block go-live.

**Remaining risks** (full treatment + GA roadmap in the closeout):

| Risk | Severity | Recommendation |
|---|---|---|
| `ALERT_WEBHOOK` empty → failures logged, not pushed | Medium | Set a Slack/Discord webhook in `deploy/.env` (now fully supported) before wider launch |
| Single instance / single AZ | Medium (OK for beta) | Add horizontal redundancy before GA |
| SQLite single-file DB | Low | Mitigated by hourly integrity-checked off-host backups + proven restore |
| Secrets in 600-mode `deploy/.env` | Low | Move to SSM Parameter Store / Secrets Manager for GA |
| Corpus = RSS only (GDELT/NewsAPI off) | Low (by choice) | Enable the flags + add a NewsAPI key if a broader corpus is wanted |

---

*See [`WAVE0_DEPLOYMENT_CLOSEOUT.md`](WAVE0_DEPLOYMENT_CLOSEOUT.md) for the architecture diagram, AWS
resource inventory, security model, disaster-recovery summary, known limitations, and the pre-GA roadmap.*
