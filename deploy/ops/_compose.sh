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

# Every function here uses the `function name() {` form DELIBERATELY: operators SOURCE this file
# in interactive login shells, where bash expands aliases at parse time — and stock Ubuntu's
# ~/.bashrc ships an `alert` alias, which turned the plain `alert() {` definition into a syntax
# error ("unexpected token `('", 2026-07-26 prod incident). The `function` keyword suppresses
# alias expansion of the name. Do not "simplify" these back to the POSIX form.

# Resolve paths from THIS file's location, regardless of the caller's CWD or how $0 was spelled.
OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # absolute deploy/ops (for sibling scripts)
REPO_ROOT="$(cd "$OPS_DIR/../.." && pwd)"
cd "$REPO_ROOT"

ENV_FILE="${IH_ENV_FILE:-deploy/.env}"
BASE_COMPOSE="deploy/docker-compose.yml"
AWS_COMPOSE="deploy/docker-compose.aws.yml"

# Every production compose invocation goes through here — one source of truth for the -f/-f/--env-file
# triple, so the base and the AWS override are always merged the same way.
function dc() {
  docker compose -f "$BASE_COMPOSE" -f "$AWS_COMPOSE" --env-file "$ENV_FILE" "$@"
}

# Run examples/db_backup.py INSIDE the one-shot `backup` container. That container already has Python +
# the store dependencies (SQLAlchemy) + the same bind-mounted /app/data — so backups, integrity checks,
# and restores never depend on host Python (which the EC2 host does not have). `--profile backup` enables
# the profiled service regardless of Compose version.
function backup_run() {
  dc --profile backup run --rm -T backup python examples/db_backup.py "$@"
}

# Read a scalar KEY=value from the env-file, matching docker compose's dotenv semantics: unquoted
# values end at an inline ` #` comment (whitespace before the hash — a bare `#` inside a token, e.g.
# a URL fragment, is kept) and are trimmed; quoted values keep everything inside the quotes and drop
# anything after the closing quote. One layer of surrounding quotes is stripped. Empty if absent.
# (Avoids sourcing the whole file, which could choke on values with special characters. The comment
# handling matters: compose parsed `RWE_X_ENABLED=1  # note` as "1" while this helper returned the
# whole tail — a false RESTART NEEDED from verify-sources.sh, 2026-07-26 prod incident.)
function env_val() {
  local key="$1" v=""
  [ -f "$ENV_FILE" ] || { printf ''; return 0; }
  v="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2-)"
  case "$v" in
    \"*) v="$(printf '%s' "$v" | sed -E 's/^("[^"]*").*/\1/')" ;;   # keep through the closing quote
    \'*) v="$(printf '%s' "$v" | sed -E "s/^('[^']*').*/\1/")" ;;
    *)   v="$(printf '%s' "$v" | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//')" ;;
  esac
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
  printf '%s' "$v"
}

# Keys defined more than once in the env-file. docker compose keeps only the LAST occurrence, so an
# appended line looks additive while silently discarding every earlier line of the same key — which
# is how BETA_ALLOWLIST lost the operator's own address (three lines, only the last effective; the
# owner locked out with not_allowlisted — 2026-08-02 prod incident). Prints key names only, never
# values: the same file holds NEXTAUTH_SECRET and friends, and a hygiene warning must not become the
# thing that leaks them into a pasted terminal log.
function env_dup_keys() {
  [ -f "$ENV_FILE" ] || return 0
  grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE" | cut -d= -f1 | sort | uniq -d
}

# Warn-only by design: a duplicate is always a mistake, but failing here would gate every deploy,
# restart and backup behind cleanup — turning an old cosmetic duplicate into an outage lever. The
# warning names each key, how many lines it has, and the one command that shows them.
function warn_env_dups() {
  local dups k n
  dups="$(env_dup_keys)"
  [ -n "$dups" ] || return 0
  echo "WARNING: duplicate keys in ${ENV_FILE} — docker compose keeps only the LAST line of each;" >&2
  echo "  every earlier line is silently ignored (this evicted BETA_ALLOWLIST entries, 2026-08-02):" >&2
  for k in $dups; do
    n="$(grep -cE "^${k}=" "$ENV_FILE")"
    echo "    ${k} — ${n} lines; inspect with:  grep -nE '^${k}=' ${ENV_FILE}" >&2
  done
  echo "  Collapse each key to ONE line, then re-run." >&2
}

function need_env() {
  if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found." >&2
    echo "  Create it from the template:  cp deploy/.env.production.example $ENV_FILE && chmod 600 $ENV_FILE" >&2
    echo "  Then fill in the REQUIRED values (secrets, domain, OAuth, allowlist)." >&2
    exit 1
  fi
  warn_env_dups
}

# Poll the engine's OBS1 readiness endpoint from INSIDE the api container (no host port needed — works
# even though the AWS override unpublishes 8000). Idempotent; safe to call repeatedly.
function wait_ready() {
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

# JSON-escape a string for safe embedding in a webhook payload. Pure bash — the EC2 host has no python,
# and an alert message can contain a container name, a quoted path, or a newline; an unescaped one would
# produce invalid JSON and the POST would silently fail. Handles backslash, double-quote, CR, LF, tab.
function _json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\r'/\\r}"; s="${s//$'\n'/\\n}"; s="${s//$'\t'/\\t}"
  printf '%s' "$s"
}

# Send an alert to ALERT_WEBHOOK if it is configured; ALWAYS log to stderr first. When ALERT_WEBHOOK is
# UNSET the only effect is the stderr log (unchanged behavior) — the stack never depends on an alert
# channel. When SET, POST a concise JSON message carrying BOTH "text" (Slack, Mattermost, Google Chat) and
# "content" (Discord), so one ALERT_WEBHOOK works with either service without a format flag. The message is
# prefixed with the app domain (from deploy/.env) and JSON-escaped. Used by monitor.sh (5-min health cron)
# and backup-offhost.sh so unattended failures reach a human. See docs/PRODUCTION_ENVIRONMENT.md → Alerting.
function alert() {
  local msg="$1" hook domain esc
  echo "ALERT: $msg" >&2
  hook="$(env_val ALERT_WEBHOOK)"
  [ -n "$hook" ] || return 0                      # unset → log-only (documented default)
  domain="$(env_val APP_DOMAIN)"; domain="${domain:-hidden-view.com}"
  esc="$(_json_escape "[Information Health · ${domain}] ${msg}")"
  curl -fsS -m 10 -X POST -H 'content-type: application/json' \
    -d "{\"text\":\"${esc}\",\"content\":\"${esc}\"}" "$hook" >/dev/null 2>&1 \
    || echo "alert: webhook POST to ALERT_WEBHOOK failed (check the URL / egress)" >&2
}

# Reboot-safety guard against the "dedicated EBS data volume not mounted yet" disaster: if the operator
# runs the DB on a SEPARATE volume (IH_DATA_MOUNT=1 in deploy/.env), refuse to start unless $1 is actually
# a mounted filesystem — otherwise the bind-mount would hit an empty directory on the root disk and the
# app would silently create a fresh, EMPTY database. On the default (root-EBS) layout IH_DATA_MOUNT is 0
# and this is a no-op beyond checking the directory exists.
function assert_data_mount() {
  local dir="$1" want
  [ -d "$dir" ] || { echo "ERROR: data dir '$dir' does not exist — run sudo deploy/ops/bootstrap-ec2.sh first." >&2; exit 1; }
  want="$(env_val IH_DATA_MOUNT)"
  if [ "${want:-0}" = "1" ] && ! mountpoint -q "$dir"; then
    echo "ERROR: IH_DATA_MOUNT=1 but '$dir' is NOT a mounted filesystem." >&2
    echo "  Refusing to start: a fresh EMPTY database would be created on the root disk (data-loss risk)." >&2
    echo "  Mount the data volume first (check /etc/fstab), e.g.:  sudo mount '$dir'   then retry." >&2
    exit 1
  fi
}
