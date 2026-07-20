#!/usr/bin/env bash
# Shared helpers for the AWS production ops wrappers. SOURCED by deploy/ops/{deploy,update,restart,
# restore,smoke-test}.sh — not run on its own. DEPLOYMENT-ONLY (orchestration over docker compose; no
# application change).
#
# Provides:
#   REPO_ROOT   absolute repo root (this file is deploy/ops/_compose.sh → ../..)
#   ENV_FILE    the compose env-file (deploy/.env; override with IH_ENV_FILE)
#   dc ...      `docker compose` with the base + AWS override + env-file already wired
#   wait_ready  block until the engine reports readiness (or time out)
#   need_env    fail early with a clear message if deploy/.env is missing

# Resolve paths from THIS file's location, regardless of the caller's CWD or how $0 was spelled.
OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # absolute deploy/ops (for sibling scripts)
REPO_ROOT="$(cd "$OPS_DIR/../.." && pwd)"
cd "$REPO_ROOT"

ENV_FILE="${IH_ENV_FILE:-deploy/.env}"
BASE_COMPOSE="deploy/docker-compose.yml"
AWS_COMPOSE="deploy/docker-compose.aws.yml"

# Every production compose invocation goes through here — one source of truth for the -f/-f/--env-file
# triple, so the base and the AWS override are always merged the same way.
dc() {
  docker compose -f "$BASE_COMPOSE" -f "$AWS_COMPOSE" --env-file "$ENV_FILE" "$@"
}

# Read a scalar KEY=value from the env-file, stripping one layer of surrounding quotes. Empty if absent.
# (Avoids sourcing the whole file, which could choke on values with special characters.)
env_val() {
  local key="$1" v=""
  [ -f "$ENV_FILE" ] || { printf ''; return 0; }
  v="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2-)"
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
  printf '%s' "$v"
}

need_env() {
  if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found." >&2
    echo "  Create it from the template:  cp deploy/.env.production.example $ENV_FILE && chmod 600 $ENV_FILE" >&2
    echo "  Then fill in the REQUIRED values (secrets, domain, OAuth, allowlist)." >&2
    exit 1
  fi
}

# Poll the engine's OBS1 readiness endpoint from INSIDE the api container (no host port needed — works
# even though the AWS override unpublishes 8000). Idempotent; safe to call repeatedly.
wait_ready() {
  local timeout="${1:-180}" waited=0
  echo "waiting for engine readiness (timeout ${timeout}s)…"
  while : ; do
    if dc exec -T api python -c \
        "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready').status==200 else 1)" \
        >/dev/null 2>&1; then
      echo "engine ready."
      return 0
    fi
    waited=$((waited + 5))
    if [ "$waited" -ge "$timeout" ]; then
      echo "ERROR: engine not ready after ${timeout}s. Recent api logs:" >&2
      dc logs --tail 40 api >&2 || true
      return 1
    fi
    sleep 5
  done
}
