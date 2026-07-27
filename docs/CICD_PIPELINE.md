# CI/CD Pipeline

The pipeline automates the path from a pushed commit to a healthy production deployment — and the
path back when a deployment is not healthy. It is built as a thin control layer over the
**existing, operator-proven lifecycle scripts** (`deploy/ops/*`): CI proves the commit, CD asks the
host to run the same commands an operator would, and the host itself owns building, health-gating,
smoke-testing, and rolling back. Nothing in the pipeline can do anything an operator could not do
by hand, and every manual runbook keeps working unchanged.

```
                       ┌─────────────────────────── GitHub Actions ───────────────────────────┐
 push / PR ──► CI (ci.yml): pytest 3.11+3.12 · web typecheck/lint/i18n/tests/build · extension │
               e2e (real engine+web) · docker image builds · compose + deployment-rules guards │
                     │  ci-success (single branch-protection check)                            │
                     ▼                                                                        │
               Deploy (deploy.yml): default-branch CI green → auto; workflow_dispatch → manual│
                     │  OIDC → hidden-view-github-deploy (least-priv IAM role)                │
                     ▼                                                                        │
               Infra (infra.yml): terraform fmt + validate on terraform/** changes            │
               └──────────────────────────────────────────────────────────────────────────────┘
                     │ aws ssm send-command  (no SSH, no static keys, no app secrets in CI)
                     ▼
        ┌──────────────────────────── EC2 host (i-01d221c5b7b7920ed) ─────────────────────────┐
        │ deploy/ops/cd-deploy.sh <sha>                                                       │
        │   1. SNAPSHOT  backup-offhost.sh --backup-now  (consistent SQLite backup, PRAGMA    │
        │                integrity check, S3 off-host copy — before any code moves)           │
        │   2. DEPLOY    update.sh <sha>  (checkout → docker compose up -d --build →          │
        │                readiness gate 240 s → smoke-test.sh)                                │
        │   3. ROLLBACK  on failure: update.sh <previously-serving sha> + webhook alert       │
        │   prints CD_RESULT=deployed | rolled_back | rollback_failed | aborted               │
        └─────────────────────────────────────────────────────────────────────────────────────┘
```

## Architecture review — what existed, what was missing

| Layer | State before | Gap closed by |
|---|---|---|
| CI gates | ✅ complete (`ci.yml`: tests, builds, e2e, image builds, compose + rules guards) | — |
| Terraform | ✅ import-only codification, plan-to-zero, MFA-gated operator applies | `infra.yml` (fmt+validate in CI), `github-oidc.tf` (the one additive file) |
| Deploy scripts | ✅ idempotent `deploy.sh`/`update.sh` with readiness gate + smoke test | `cd-deploy.sh` (sequencing + automatic rollback) |
| Deployment trigger | ❌ manual SSM session, human-typed commands | `deploy.yml` (CI-green auto-deploy + manual dispatch) |
| Rollback | ❌ manual (update.sh printed instructions) | automatic in `cd-deploy.sh`, alerted via `ALERT_WEBHOOK` |
| Pre-deploy backup | ❌ hourly cron only (up to ~59 min exposure) | snapshot step 1 of `cd-deploy.sh` |
| Backups / monitoring | ✅ hourly off-host + integrity check; 5-min monitor cron + alerts | — (reused, not replaced) |
| Secrets | ✅ host-only `deploy/.env` (600, gitignored); fail-fast `${VAR:?}` guards | CI needs **zero** app secrets; AWS auth is OIDC-federated |

**"Rolling deployment", honestly stated:** this is a single-host stack with SQLite on a host
bind-mount. There is no multi-instance rolling window — a deploy is a rebuild-and-converge with a
brief service interruption while containers swap. What "database-safe" means here: the DB is never
touched by a code deploy (it lives outside the containers), a verified snapshot is taken before
every deploy anyway, schema management is additive (`create_all`), and a failed deploy converges
back to the last serving commit automatically. True zero-downtime would require a second host and
a DB move (Postgres or Litestream) — a deliberate future decision, not a hidden default.

## One-time setup (operator, ~15 minutes)

1. **Create the IAM pieces** (MFA flow, from `terraform/`):
   `source ./assume.sh && terraform apply -target=aws_iam_openid_connect_provider.github -target=aws_iam_role.github_deploy -target=aws_iam_role_policy.github_deploy_ssm`
   — the ONE deliberate exception to the import-only doctrine (documented in `github-oidc.tf`).
   Note the `github_deploy_role_arn` output.
2. **Repo variables** (Settings → Actions → Variables): `AWS_DEPLOY_ROLE_ARN` = that output;
   `EC2_INSTANCE_ID` = `i-01d221c5b7b7920ed`.
3. **`production` environment** (Settings → Environments): create it; add required reviewers if
   you want every deploy human-approved (recommended while the beta is small — automatic deploys
   then pause for one click).
4. Confirm the instance tag `Name=ih-beta` exists (it does — the IAM condition keys on it).

## Day-to-day

* **Ship:** merge to the default branch → CI runs → Deploy fires with that SHA → watch the run;
  the host output (build, gate, smoke, verdict) streams into the Actions log.
* **Hotfix / redeploy / roll back:** Actions → Deploy → Run workflow → enter any SHA/tag. A
  rollback is just deploying the previous good SHA through the same door.
* **Deploy fails:** the run goes red with the reason; the host has already rolled itself back and
  is serving the previous commit (verified by the same readiness+smoke gate). `ALERT_WEBHOOK`
  (when configured in `deploy/.env`) carries the same message to Slack/Discord.
* **Both fail** (`rollback_failed`, exit 2): the alert says so explicitly; connect via SSM and
  follow `docs/DEPLOYMENT_RUNBOOK.md` — the pre-deploy snapshot is in `data/backups/` and S3.
* **Infra change:** PR touching `terraform/**` gets fmt+validate in CI; plan/apply remain the
  MFA-gated human flow per `terraform/README.md` — CI deliberately holds no state-touching
  credentials.

## Properties

* **Idempotent** — every layer converges: `terraform apply`, `docker compose up -d`, `update.sh`,
  re-running a Deploy run. A re-deploy of the serving SHA is a no-op rebuild.
* **Secure** — no SSH, no static cloud keys, no app secrets off-host; the deploy role can run one
  document on one tagged instance and read its own output, nothing else; `production` environment
  approval optionally gates every run; SSM keeps a full audited command history.
* **Reuse-first** — `cd-deploy.sh` is ~40 lines of sequencing; build/health/smoke/backup/alert
  logic all live where they always did, so manual operations and CI/CD can never drift apart.
