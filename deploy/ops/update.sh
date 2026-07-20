#!/usr/bin/env bash
# Update to (or roll back to) a specific release, then re-deploy. DEPLOYMENT-ONLY.
#
# IDEMPOTENT: checks out the requested ref and converges the running stack to it. User data is untouched
# (the DB lives on the host bind-mount, independent of the code checkout).
#
#   deploy/ops/update.sh v1.2.0        # deploy a new release tag
#   deploy/ops/update.sh <prev-tag>    # ROLLBACK: redeploy the previous good tag
#   deploy/ops/update.sh               # rebuild the current checkout (no ref change)
#
# For a DATA fault (corruption), use deploy/ops/restore.sh instead — this script only moves code.
set -euo pipefail
# shellcheck source=deploy/ops/_compose.sh
source "$(dirname "$0")/_compose.sh"

need_env

REF="${1:-}"
if [ -n "$REF" ]; then
  echo "== update: fetching + checking out '$REF' =="
  git fetch --tags --prune origin
  git checkout "$REF"
else
  echo "== update: rebuilding current checkout ($(git rev-parse --short HEAD)) =="
fi

echo "== update: rebuilding + restarting the stack =="
dc up -d --build
wait_ready 240

echo "== update: post-update smoke test =="
if "$OPS_DIR/smoke-test.sh"; then
  echo ""
  echo "✅ now serving $(git describe --tags --always 2>/dev/null || git rev-parse --short HEAD)."
else
  echo ""
  echo "⚠️  smoke test failed after update. To roll back: deploy/ops/update.sh <previous-good-tag>" >&2
  exit 1
fi
