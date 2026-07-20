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

# Host data dir must exist before the bind-mount (idempotent).
DATA_DIR="$(env_val IH_DATA_DIR)"; DATA_DIR="${DATA_DIR:-/opt/ih/data}"
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
  • Run the full production preflight (env + secrets + live probes):
        set -a; . $ENV_FILE; set +a
        IH_BASE_URL=http://127.0.0.1:8000 deploy/ops/preflight.sh
  • Verify a backup round-trips:  deploy/ops/backup.sh && deploy/ops/verify-restore.sh
  • Then follow docs/WAVE0_GO_LIVE_CHECKLIST.md before sending invites.
EOF
