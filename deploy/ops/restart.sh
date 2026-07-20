#!/usr/bin/env bash
# Restart the stack, or a single service, without rebuilding. DEPLOYMENT-ONLY.
#
#   deploy/ops/restart.sh            # restart every running service
#   deploy/ops/restart.sh web        # restart just the web tier (or: api / caddy)
#
# Use after editing deploy/.env (e.g. adding a BETA_ALLOWLIST email): the container picks up the new
# environment on restart. Data and certificates are untouched.
set -euo pipefail
# shellcheck source=deploy/ops/_compose.sh
source "$(dirname "$0")/_compose.sh"

need_env

SVC="${1:-}"
if [ -n "$SVC" ]; then
  echo "== restart: $SVC =="
  # `up -d` (not `restart`) so an env change in deploy/.env is actually re-read by the container.
  dc up -d "$SVC"
else
  echo "== restart: all services =="
  dc up -d
fi

# Only wait on readiness if the engine is part of what we (re)started.
if [ -z "$SVC" ] || [ "$SVC" = "api" ] || [ "$SVC" = "web" ]; then
  wait_ready 180 || true
fi

dc ps
