#!/usr/bin/env bash
# Deploy (or re-deploy) the full production stack on the EC2 host. DEPLOYMENT-ONLY.
#
# IDEMPOTENT: `docker compose up -d` converges to the desired state, so running this repeatedly — or on a
# brand-new instance after deploy/ops/bootstrap-ec2.sh — is safe. Certificates (caddy_data) and the DB
# (host bind-mount) persist across runs.
#
#   deploy/ops/deploy.sh            # build + start ingest→api→web→caddy, gate on readiness, then smoke-test
#
# Prereqs: bootstrap-ec2.sh has run (Docker + swap + data dir + log rotation), deploy/.env is filled in,
# and DNS for APP_DOMAIN resolves to this host (so Caddy can complete the ACME challenge).
set -euo pipefail
# shellcheck source=deploy/ops/_compose.sh
source "$(dirname "$0")/_compose.sh"

need_env

# Host data dir must exist before the bind-mount (idempotent), and — if the DB lives on a DEDICATED EBS
# volume (IH_DATA_MOUNT=1) — must actually be mounted, so a boot-before-mount race can't make the app
# start on an empty database.
DATA_DIR="$(env_val IH_DATA_DIR)"; DATA_DIR="${DATA_DIR:-/opt/ih/data}"
assert_data_mount "$DATA_DIR"
mkdir -p "$DATA_DIR/backups"

echo "== deploy: building + starting the stack =="
dc up -d --build

# Do not proceed (scheduler, smoke) until the engine is actually serving.
wait_ready 240

echo "== deploy: enabling recurring local backups (scheduler profile) =="
dc --profile scheduler up -d backup-scheduler

echo "== deploy: post-deploy smoke test =="
# Auto-validate the running deployment. Non-fatal to the deploy itself (the stack is already up), but a
# non-zero exit is surfaced loudly so the operator investigates before inviting users.
if "$OPS_DIR/smoke-test.sh"; then
  echo ""
  echo "✅ deploy complete and smoke test passed."
else
  echo ""
  echo "⚠️  deploy is up but the smoke test reported problems — review the output above before go-live." >&2
  exit 1
fi

cat <<EOF

Next steps:
  • Run the production config preflight (env + secrets + HTTPS + OAuth + DB):
        set -a; . $ENV_FILE; set +a
        deploy/ops/preflight.sh          # do NOT set IH_BASE_URL — the engine port is unpublished;
                                         # smoke-test.sh (already run above) does the live checks in-container
  • Take + verify + ship a backup off-host (container-based; no host Python):
        deploy/ops/backup-offhost.sh --backup-now
  • Confirm the health monitor + off-host backup crons are installed (by bootstrap-ec2.sh):
        ls -l /etc/cron.d/ih-monitor /etc/cron.d/ih-offhost-backup
  • Then follow docs/WAVE0_GO_LIVE_CHECKLIST.md before sending invites.
EOF
