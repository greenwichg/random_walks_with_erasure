#!/usr/bin/env bash
# Enable the /v1 platform API on the running production stack and validate it against the live
# catalogue. DEPLOYMENT-ONLY: flips one env flag, reloads Caddy's already-deployed config, runs the
# idempotent identity backfill, mints two TEMPORARY validation keys (revoked at the end), and runs
# examples/platform_validate.py inside the api container. Application code is not changed.
#
#   sudo deploy/ops/platform-enable.sh              # enable + backfill + validate (idempotent; re-runnable)
#   sudo deploy/ops/platform-enable.sh --dry-run    # print every step, change nothing
#   sudo deploy/ops/platform-enable.sh --validate   # validation only (flag + backfill already done)
#
# Preconditions: the deployed checkout carries the /v1 route in deploy/Caddyfile (a release at or
# after the commit that added it — `deploy/ops/update.sh <sha>` first), and deploy/.env exists.
#
# What it never does: print a key. The validation keys live only in this shell's memory and in the
# api container's environment for the validation run; both are revoked before the script exits.
# For a durable key mint one yourself afterwards (the command is printed at the end).
#
# Outputs: the validation report on stdout and, as JSON, at $IH_DATA_DIR/platform_validation.json
# (the bind-mounted /app/data). Exit 0 = enabled and every hard check passed (WARNs allowed).
set -uo pipefail
# shellcheck source=deploy/ops/_compose.sh
source "$(dirname "$0")/_compose.sh"

MODE="enable"
case "${1:-}" in
  --dry-run) MODE="dry" ;;
  --validate) MODE="validate" ;;
  "") ;;
  *) echo "usage: $0 [--dry-run | --validate]" >&2; exit 2 ;;
esac

need_env
DATA_DIR="$(env_val IH_DATA_DIR)"; DATA_DIR="${DATA_DIR:-/opt/ih/data}"
DOMAIN="$(env_val APP_DOMAIN)"; DOMAIN="${DOMAIN:-hidden-view.com}"
fail=0
step() { printf '\n== %s ==\n' "$1"; }
ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=1; }

# ── 1. the route must already be deployed ────────────────────────────────────────────────────
step "1/7 deployed config carries the /v1 route"
if grep -q '@platform path /v1/\*' deploy/Caddyfile; then
  ok "deploy/Caddyfile routes /v1/* to api:8000 (checkout $(git rev-parse --short HEAD 2>/dev/null || echo ?))"
else
  bad "deploy/Caddyfile in this checkout has no /v1 route — deploy the release that adds it first: deploy/ops/update.sh <sha>"
  exit 1
fi

# ── 2. the flag ──────────────────────────────────────────────────────────────────────────────
step "2/7 RWE_PLATFORM_API=1 in $ENV_FILE"
cur="$(env_val RWE_PLATFORM_API)"
if [ "$cur" = "1" ]; then
  ok "already set"
elif [ "$MODE" = "dry" ]; then
  echo "  would append: RWE_PLATFORM_API=1   (currently '${cur:-unset}')"
elif [ "$MODE" = "validate" ]; then
  bad "RWE_PLATFORM_API is '${cur:-unset}' — run without --validate to enable it"
  exit 1
else
  if [ -n "$cur" ]; then
    # one line per key (see warn_env_dups): rewrite the existing line rather than append a second
    sed -i -E 's/^RWE_PLATFORM_API=.*$/RWE_PLATFORM_API=1/' "$ENV_FILE"
  else
    printf '\n# Commercial /v1 surface (docs/PLATFORM_API.md); enabled by deploy/ops/platform-enable.sh\nRWE_PLATFORM_API=1\n' >> "$ENV_FILE"
  fi
  ok "set (ratings / Wikipedia switches stay off: RWE_PLATFORM_PUBLISH_RATINGS / _WIKIPEDIA unset)"
fi

# ── 3. restart the engine (re-reads env) + reload Caddy (re-reads the bind-mounted Caddyfile) ──
step "3/7 engine restart + Caddy reload"
if [ "$MODE" = "dry" ]; then
  echo "  would run: deploy/ops/restart.sh api ; dc exec caddy caddy reload"
else
  if [ "$MODE" = "enable" ]; then
    "$OPS_DIR/restart.sh" api || { bad "engine restart failed"; exit 1; }
  fi
  # Caddy re-reads the bind-mounted Caddyfile on `reload`; the result is VERIFIED by routing a
  # request through it, because `docker compose up -d` does not recreate a container whose spec
  # is unchanged (a bind-mounted file is not part of the spec) — the first production run
  # declared the reload done and /v1 kept landing on the web tier. A failed probe restarts Caddy.
  if dc exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile 2>&1 | sed 's/^/    /'; then
    ok "caddy reload accepted"
  else
    warn "caddy reload returned non-zero"
  fi
  routed=""
  for attempt in 1 2; do
    rc="$(curl -sS -o /dev/null -w '%{http_code}' --resolve "$DOMAIN:443:127.0.0.1" --max-time 15 "https://$DOMAIN/v1/articles" 2>/dev/null || echo 000)"
    if [ "$rc" = "401" ]; then routed="yes"; break; fi
    if [ "$attempt" = "1" ]; then
      warn "https://$DOMAIN/v1/articles answered $rc through Caddy (expected the engine's 401) — restarting caddy"
      dc restart caddy 2>&1 | sed 's/^/    /'
      sleep 3
    fi
  done
  [ -n "$routed" ] && ok "Caddy routes /v1/* to the engine (keyless /v1/articles -> 401)" \
    || bad "Caddy still does not route /v1/* to the engine — check: dc logs caddy; grep -n '@platform' deploy/Caddyfile"
  wait_ready 180 || { bad "engine not ready"; exit 1; }
  # A generous timeout on purpose: right after a restart the engine may still be building the
  # search index (once, on the first start of a release) or the first story view.
  code="$(dc exec -T api python -c "
import urllib.request
try: print(urllib.request.urlopen('http://127.0.0.1:8000/v1/health', timeout=60).status)
except Exception as e: print(getattr(e, 'code', 'EXC:' + type(e).__name__))" 2>/dev/null | tr -d '[:space:]')"
  [ "$code" = "200" ] && ok "/v1/health answers 200 inside the engine" || bad "/v1/health returned '$code' (is RWE_PLATFORM_API on the api service?)"
fi

# ── 4. backup, then the identity backfill (idempotent, batched, safe beside the poller) ──────
step "4/7 backup + identity backfill"
if [ "$MODE" = "dry" ]; then
  echo "  would run: backup_run backup ; identity_backfill.py --dry-run ; identity_backfill.py"
else
  if backup_run backup >/dev/null 2>&1; then ok "pre-backfill backup written ($DATA_DIR/backups)"; else warn "backup step failed — continuing (the backfill is additive and idempotent)"; fi
  echo "  dry run:"
  dc exec -T api python examples/identity_backfill.py --dry-run 2>&1 | tail -1 | sed 's/^/    /'
  if [ "$MODE" = "enable" ]; then
    # stderr is SHOWN: the first production run lost its traceback to /dev/null and reported a
    # backfill that stopped at 37k of 150k rows as "rows still missing — re-run". The CLI now
    # writes a batch per transaction, retries lock contention, passes until nothing is missing,
    # and exits 1 if rows remain; its last line is the summary.
    echo "  applying (batched, retried, passes until complete):"
    dc exec -T api python examples/identity_backfill.py 2>&1 | tail -4 | sed 's/^/    /'
  fi
  echo "  after:"
  after="$(dc exec -T api python examples/identity_backfill.py --dry-run 2>/dev/null | tail -1 | tr -d ' ')"
  echo "    $after"
  case "$after" in
    *'"missingArticleId":0,'*'"missingLicence":0,'*'"missingPublisherId":0,'*) ok "every catalogue row carries article_id, publisher_id, licence_class" ;;
    *) bad "rows still missing identity — re-run; if it persists: dc exec -T api python examples/identity_backfill.py" ;;
  esac
  dc exec -T api python - <<'PY' 2>/dev/null | sed 's/^/    /'
import os, sys
sys.path.insert(0, "examples")
import store
st = store.Store(os.environ.get("RWE_DB_URL") or store.default_db_url())
pubs, total = st.list_publishers(limit=1)
builds = st.story_builds(limit=1)
print(f"publishers table: {total} rows; story builds recorded: {'yes, newest ' + str(builds[0]['builtAt']) if builds else 'none yet (recorded on the next served build)'}")
print(f"search index: {st.search_index_status()}")
PY
fi

# ── 4b. enrichment backfill: provider entities (GDELT GKG, network) + our headline spans (local) ──
step "4b/7 enrichment backfill (what /v1/entities, /v1/countries and the comparison's geography answer over)"
if [ "$MODE" = "dry" ]; then
  echo "  would run: gdelt_entity_backfill.py --hours ${PLATFORM_GKG_HOURS:-48} ; entity_span_backfill.py ; then report /v1/health enrichment"
else
  if [ "$MODE" = "enable" ] && [ "${PLATFORM_GKG_HOURS:-48}" != "0" ]; then
    echo "  provider entities from GDELT GKG (last ${PLATFORM_GKG_HOURS:-48}h of files; idempotent; a missing window is skipped; PLATFORM_GKG_HOURS=0 skips):"
    if dc exec -T api python examples/gdelt_entity_backfill.py --hours "${PLATFORM_GKG_HOURS:-48}" 2>&1 | tail -3 | sed 's/^/    /'; then
      ok "entity backfill finished"
    else
      warn "entity backfill returned non-zero (egress to data.gdeltproject.org? re-run later; the steady-state enricher keeps filling)"
    fi
  elif [ "$MODE" = "enable" ]; then
    ok "GDELT entity backfill skipped (PLATFORM_GKG_HOURS=0) — the last run's entities stand; the steady-state enricher keeps filling"
  fi
  if [ "$MODE" = "enable" ]; then
    echo "  headline spans (local rule extractor):"
    dc exec -T api python examples/entity_span_backfill.py 2>&1 | tail -2 | sed 's/^/    /' || warn "span backfill returned non-zero"
  fi
  dc exec -T api python - <<'PY' 2>/dev/null | sed 's/^/    /'
import json, os, sys
sys.path.insert(0, "examples")
import store
st = store.Store(os.environ.get("RWE_DB_URL") or store.default_db_url())
e = st.enrichment_coverage()
r = e["recent"]
print(f"enrichment, last {r['days']} days: {r['articles']} articles; entities on {r['withEntities']} ({r['entityCoverage']}), spans on {r['withSpans']} ({r['spanCoverage']}), event countries on {r['withEventCountries']} ({r['geoCoverage']})")
print(f"enrichment, whole catalogue: {json.dumps(e['catalogue'])}")
PY
fi

# ── 5. temporary keys ─────────────────────────────────────────────────────────────────────────
step "5/7 temporary validation keys (revoked at the end; never printed)"
KEY="" KEY_DEV="" KEY_ID="" KEY_DEV_ID=""
if [ "$MODE" = "dry" ]; then
  echo "  would run: platform_keys.py tenant create platform-validate ; mint --plan internal ; mint --plan developer"
else
  dc exec -T api python examples/platform_keys.py tenant create platform-validate --name "platform-enable.sh (temporary)" --kind internal >/dev/null 2>&1 || true
  KEY="$(dc exec -T api python examples/platform_keys.py mint --tenant platform-validate --plan internal --label "platform-enable.sh" 2>/dev/null | tail -1 | tr -d '[:space:]')"
  KEY_DEV="$(dc exec -T api python examples/platform_keys.py mint --tenant platform-validate --plan developer --label "platform-enable.sh" 2>/dev/null | tail -1 | tr -d '[:space:]')"
  case "$KEY:$KEY_DEV" in
    hv_live_*:hv_live_*) ok "internal + developer keys minted for tenant platform-validate" ;;
    *) bad "could not mint the validation keys"; exit 1 ;;
  esac
fi

# ── 6. the battery, inside the api container, against the live engine ───────────────────────
step "6/7 validation battery (examples/platform_validate.py)"
if [ "$MODE" = "dry" ]; then
  echo "  would run: platform_validate.py --base-url http://127.0.0.1:8000 --json /app/data/platform_validation.json"
else
  if dc exec -T -e "RWE_PLATFORM_KEY=$KEY" -e "RWE_PLATFORM_KEY_DEV=$KEY_DEV" api \
       python examples/platform_validate.py --base-url http://127.0.0.1:8000 --repeat 5 \
       --json /app/data/platform_validation.json 2>/dev/null; then
    ok "battery passed (report: $DATA_DIR/platform_validation.json)"
  else
    bad "battery reported FAILs (report: $DATA_DIR/platform_validation.json)"
  fi
  # revoke the temporary keys whatever happened above
  dc exec -T api python - <<'PY' 2>/dev/null | sed 's/^/    /'
import os, sys
sys.path.insert(0, "examples")
import store
st = store.Store(os.environ.get("RWE_DB_URL") or store.default_db_url())
n = 0
for k in st.platform_list_keys("platform-validate"):
    if not k.get("revokedAt"):
        st.platform_revoke_key(k["keyId"]); n += 1
print(f"revoked {n} temporary key(s) on tenant platform-validate")
PY
fi

# ── 7. the public edge ────────────────────────────────────────────────────────────────────────
step "7/7 public edge: https://$DOMAIN/v1/* reaches the engine and refuses without a key"
if [ "$MODE" = "dry" ]; then
  echo "  would probe: https://$DOMAIN/v1/health (200) and https://$DOMAIN/v1/articles (401 unauthenticated)"
else
  hcode="$(curl -sS -o /dev/null -w '%{http_code}' --resolve "$DOMAIN:443:127.0.0.1" --max-time 15 "https://$DOMAIN/v1/health" 2>/dev/null || echo 000)"
  [ "$hcode" = "200" ] && ok "https://$DOMAIN/v1/health -> 200 (local Caddy)" || bad "https://$DOMAIN/v1/health -> $hcode"
  body="$(curl -sS --resolve "$DOMAIN:443:127.0.0.1" --max-time 15 "https://$DOMAIN/v1/articles" 2>/dev/null || true)"
  case "$body" in
    *unauthenticated*) ok "https://$DOMAIN/v1/articles without a key -> unauthenticated envelope" ;;
    *) bad "keyless /v1/articles did not answer the platform's 401 envelope: ${body:0:120}" ;;
  esac
  wcode="$(curl -sS -o /dev/null -w '%{http_code}' --resolve "$DOMAIN:443:127.0.0.1" --max-time 15 "https://$DOMAIN/" 2>/dev/null || echo 000)"
  case "$wcode" in 200|30[1278]) ok "consumer site still served (HTTP $wcode)" ;; *) bad "consumer site returned $wcode" ;; esac
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "== platform: ENABLED and validated =="
  echo "Mint a durable key for a real caller (printed ONCE; store it in your secret manager):"
  echo "  cd /opt/ih && sudo docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.aws.yml --env-file deploy/.env \\"
  echo "     exec -T api python examples/platform_keys.py tenant create <tenant> --name \"<Name>\" --kind developer"
  echo "  … exec -T api python examples/platform_keys.py mint --tenant <tenant> --plan developer --label \"<who>\""
else
  echo "== platform: NOT fully validated — resolve the FAILs above ==" >&2
fi
exit "$fail"
