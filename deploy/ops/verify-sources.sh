#!/usr/bin/env bash
# Multi-source provider enablement checklist. DEPLOYMENT-ONLY (read-only: no ingest, no writes).
#
#   deploy/ops/verify-sources.sh
#
# For every API provider (NewsAPI, Guardian, NewsData, GNews, MediaStack, Currents, Google News RSS)
# this reports, in one run:
#   1. deploy/.env intent        — enable flag + whether an API key is present (NEVER the key itself)
#   2. running-container reality — the env the api process was actually created with; any drift vs
#                                  deploy/.env means the container predates the edit → RESTART NEEDED
#                                  (deploy/ops/restart.sh api — it uses `up -d`, which re-reads .env)
#   3. adapter truth             — enabled() / config_warning() exactly as the engine evaluates them
#   4. ingestion health          — the per-source health row (lastSuccess, imported, errors) from the
#                                  engine's /api/internal/feeds, proving fetches actually succeed
#   5. a per-provider VERDICT    — HEALTHY / ENABLED-awaiting-first-cycle / FAILING / KEY MISSING /
#                                  RESTART NEEDED / DISABLED — plus ready-to-paste .env lines for
#                                  anything still missing.
#
# Exit codes: 0 = nothing actionable; 1 = actionable misconfiguration (missing key, stale container,
# failing fetches); 2 = stack not running. Secrets discipline: key VALUES are never read into output —
# only presence and length.
set -euo pipefail
# shellcheck source=deploy/ops/_compose.sh
source "$(dirname "$0")/_compose.sh"

need_env

if [ -z "$(dc ps -q api 2>/dev/null)" ]; then
  echo "ERROR: the api container is not running — start the stack first (deploy/ops/deploy.sh or restart.sh)." >&2
  exit 2
fi

# Host-side facts from deploy/.env, passed to the in-container probe as NAME:FLAG:KEYLEN triples.
# The probe never sees the key material — bash computes lengths here and passes ONLY those.
FACTS=()
for p in NEWSAPI GUARDIAN NEWSDATA GNEWS MEDIASTACK CURRENTS; do
  flag="$(env_val "RWE_${p}_ENABLED")"
  key="$(env_val "RWE_${p}_API_KEY")"
  FACTS+=("${p}:${flag}:${#key}")
done
FACTS+=("GOOGLENEWS:$(env_val RWE_GOOGLENEWS_ENABLED):-")   # keyless: keylen is not applicable

echo "== multi-source provider enablement checklist ($(date -u +%Y-%m-%dT%H:%M:%SZ)) =="
rc=0
dc exec -T api python - "${FACTS[@]}" <<'PY' || rc=$?
import json, os, sys, urllib.request
for _p in ("/app/examples", "examples"):          # api image WORKDIR=/app; modules live in examples/
    if _p not in sys.path:
        sys.path.insert(0, _p)
import sources                                    # noqa: E402

_TRUE = {"1", "true", "yes", "on"}


def truthy(v):
    return (v or "").strip().lower() in _TRUE


# ---- host intent (argv triples from deploy/.env) -------------------------------------------------
host = {}
for arg in sys.argv[1:]:
    name, flag, keylen = arg.split(":", 2)
    host[name] = {"flag": truthy(flag), "keylen": None if keylen == "-" else int(keylen)}

# ---- the adapters, in registry order (the same objects the engine builds) ------------------------
PREFIXES = {"NewsAPI": "NEWSAPI", "Guardian": "GUARDIAN", "NewsData": "NEWSDATA", "GNews": "GNEWS",
            "MediaStack": "MEDIASTACK", "Currents": "CURRENTS", "GoogleNews": "GOOGLENEWS"}
adapters = [a for a in sources.default_registry().adapters() if a.provider in PREFIXES]

# ---- ingestion health from the engine (the served truth) -----------------------------------------
rows, api_err = [], None
try:
    req = urllib.request.Request("http://127.0.0.1:8000/api/internal/feeds",
                                 headers={"X-IH-Auth": os.environ.get("RWE_INTERNAL_SECRET", "")})
    rows = json.loads(urllib.request.urlopen(req, timeout=15).read())
except Exception as e:                            # engine mid-restart etc. — report, don't crash
    api_err = f"{type(e).__name__}: {e}"

actionable = []
fixes = []
print(f"{'provider':<11} {'.env flag':<9} {'.env key':<10} {'container':<10} verdict")
print("-" * 100)
for a in adapters:
    p = PREFIXES[a.provider]
    h = host.get(p, {"flag": False, "keylen": 0})
    keyless = h["keylen"] is None
    c_flag = truthy(os.environ.get(f"RWE_{p}_ENABLED"))
    c_keylen = None if keyless else len(os.environ.get(f"RWE_{p}_API_KEY", ""))
    drift = (h["flag"] != c_flag) or (h["keylen"] != c_keylen)

    health = next((r for r in rows if str(r.get("feedUrl", "")).startswith(f"{a.source_type}://")), None)
    if drift:
        verdict = "RESTART NEEDED — deploy/.env changed after the container started (restart.sh api)"
        actionable.append(p)
    elif h["flag"] and not keyless and h["keylen"] == 0:
        verdict = "KEY MISSING — flag is on but the adapter stays DISABLED until the key lands"
        actionable.append(p)
        fixes.append(f"RWE_{p}_API_KEY=<paste your key>")
    elif not a.enabled():
        note = " (key present — set the flag to turn on)" if (not keyless and h["keylen"]) else ""
        verdict = f"DISABLED{note}"
        w = a.config_warning()
        if w:
            verdict += f"  ! {w}"
    elif health is None:
        verdict = ("ENABLED — awaiting first cycle (poller fetches on start; if this persists past "
                   f"{a.interval():.0f}s, check the api logs)")
        if api_err:
            verdict = f"ENABLED — health unknown (engine API unreachable: {api_err})"
            actionable.append(p)
    elif health.get("healthy"):
        verdict = (f"HEALTHY — lastSuccess {health.get('lastSuccessAt')}  imported {health.get('imported')}"
                   f"  duplicates {health.get('duplicate')}  polls {health.get('totalPolls')}")
    else:
        err = (health.get("lastError") or "")[:120]
        verdict = (f"FAILING — {health.get('consecutiveFailures')} consecutive failure(s); "
                   f"lastError: {err}")
        actionable.append(p)

    flag_s = "1" if h["flag"] else "0"
    key_s = "n/a" if keyless else (f"set({h['keylen']})" if h["keylen"] else "MISSING")
    cont_s = ("stale" if drift else "in sync")
    print(f"{a.provider:<11} {flag_s:<9} {key_s:<10} {cont_s:<10} {verdict}")

    if a.provider == "MediaStack" and a.enabled() and truthy(os.environ.get("RWE_MEDIASTACK_HTTPS", "1")):
        print(f"{'':<11} note: on MediaStack's FREE plan https is paid-only — every fetch will fail "
              "with https_access_restricted until RWE_MEDIASTACK_HTTPS=0 is set (documented trade-off).")

if api_err:
    print(f"\nWARNING: engine health API unreachable ({api_err}) — health verdicts above are partial.")
if fixes:
    print("\nready-to-paste for deploy/.env (then: deploy/ops/restart.sh api):")
    for line in fixes:
        print(f"  {line}")
if actionable:
    print(f"\nACTIONABLE: {', '.join(dict.fromkeys(actionable))} — fix above, restart api, re-run this script.")
    raise SystemExit(1)
print("\nOK: no actionable misconfiguration. Disabled providers stay off until you flip their flags.")
PY

exit "$rc"
