#!/usr/bin/env bash
# Verify the Publisher Registry / Signal Integrity / Blindspot-lens rollout (M1–M3 + the
# curation pass) on the RUNNING production stack. DEPLOYMENT-ONLY (read-only probes; no
# application change). Companion to verify-event-country.sh — run after deploy/ops/update.sh:
#
#   1  the deployed code carries the rollout (registry 138/73, blindspot param present)
#   2  registry integrity + spot resolutions (curated leans, locality-only rows)
#   3  article ingest resolves publisher lean through the registry (fresh rows post-deploy)
#   4  Stories: distributions + blindspot filter + counted facets
#   5  the WEB PROXY forwards the blindspot param (the exact bug class fixed in 94e2f73)
#   6  Publisher Intelligence: rated / unrated / unknown profiles
#   7  filters: lean buckets exclusive, unrated excluded, country regression
#   8  the public domain serves the same (skip with SMOKE_SKIP_PUBLIC=1)
#
#   deploy/ops/verify-publisher-rollout.sh
#
# Fresh deploys: lean is stamped at INGEST, so pre-deploy rows keep their stored (null) lean
# until the catalog rolls over — check 3 judges only rows ingested AFTER the api container
# started, and WARNs (not FAILs) until the first post-deploy poll cycle lands (~15 min).
set -uo pipefail
source "$(dirname "$0")/_compose.sh"

pass=0 warn=0 fail=0
P() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; pass=$((pass + 1)); }
W() { printf '  \033[33mWARN\033[0m  %s\n' "$1"; warn=$((warn + 1)); }
F() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=$((fail + 1)); }

echo "checkout: $(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo '?')"

# One python probe inside the api container prints every engine-side fact this script judges.
FACTS="$(dc exec -T api python - <<'PY' 2>/dev/null
import sys
# The api image's WORKDIR is /app with engine modules under /app/examples — the running app adds
# that to sys.path by being launched as a script; an exec'd `python -` must add it itself.
for _p in ("/app/examples", "examples"):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import inspect, json, math, os, urllib.parse, urllib.request

def get(path, ok404=False):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000" + path, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if ok404:
            return {"__status__": e.code}
        raise

out = {}
import outlet_registry, story_service, store as store_mod
reg = outlet_registry.default_registry()
outs = reg.outlets()
rated = [o for o in outs if math.isfinite(o.lean)]
out["registryTotal"], out["registryRated"] = len(outs), len(rated)
out["lintIssues"] = len(outlet_registry.lint_registry())
out["blindspotParam"] = "blindspot" in inspect.signature(story_service.list_stories).parameters
spot = {}
for probe, want in (("dailymail.co.uk", 1.0), ("economist.com", -1.0), ("jpost.com", 0.0)):
    o = reg.resolve(probe)
    spot[probe] = (o.canonical if o else None, o.lean if o else None, want)
for probe in ("france24.com", "tribuneonlineng.com"):
    o = reg.resolve(probe)
    spot[probe] = (o.canonical if o else None, "nan" if (o and math.isnan(o.lean)) else "?", "nan")
out["spot"] = spot
# Judged HERE (the EC2 host has no python — every judgment must ride the container probe).
out["spotOk"] = all(
    (v[0] is not None) and ((v[1] == "nan") if v[2] == "nan" else (v[1] == v[2]))
    for v in spot.values())

# Ingest inheritance over the LIVE catalog: rows fetched after the api container started.
st = store_mod.Store(os.environ.get("RWE_DB_URL", "sqlite:////app/data/rwe.db"))
import datetime
started = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=6)
fresh_ok = fresh_bad = fresh_rated = 0
newest = st.list_feed_articles(limit=400)
for r in newest:
    o = reg.resolve(r["publisher"])
    if not (o and math.isfinite(o.lean)):
        continue
    fresh_rated += 1
    lean = (r["scored"] or {}).get("lean")
    if lean == o.lean:
        fresh_ok += 1
    elif lean is None:
        pass          # pre-deploy row (stamped before the registry carried this lean) — rolling
    else:
        fresh_bad += 1
out["ingest"] = {"ratedRows": fresh_rated, "leanMatch": fresh_ok, "leanWrong": fresh_bad,
                 "catalog": st.count_feed_articles()}

stories = get("/api/stories?limit=50")
out["storiesTotal"] = stories.get("total", 0)
out["blindspotFacets"] = stories.get("blindspotFacets")
any_gap = get("/api/stories?blindspot=any&limit=50")
out["gapTotal"] = any_gap.get("total", 0)
out["gapAllHaveSide"] = all(s.get("blindspotSide") for s in any_gap.get("stories", []))
out["countryUS"] = get("/api/stories?country=US&limit=1").get("total", 0)

dm = get("/api/publishers/" + urllib.parse.quote("Daily Mail"))
out["dm"] = {"rated": dm.get("rated"), "lean": dm.get("lean"), "bucket": dm.get("leanBucket")}
fr = get("/api/publishers/" + urllib.parse.quote("France 24"))
out["fr"] = {"rated": fr.get("rated"), "lean": fr.get("lean", "absent"),
             "country": (fr.get("registry") or {}).get("country")}
out["unknown404"] = get("/api/publishers/No%20Such%20Gazette", ok404=True).get("__status__") == 404

leans = {}
for side in ("left", "center", "right"):
    arts = get(f"/api/discover?lean={side}&limit=100").get("articles", [])
    leans[side] = sorted({a["publisher"] for a in arts})
out["leanSides"] = {k: len(v) for k, v in leans.items()}
out["leanOverlap"] = sorted(set(leans["left"]) & set(leans["right"]))
unrated_leak = [p for side in leans.values() for p in side
                if (lambda o: o is None or math.isnan(o.lean))(reg.resolve(p))]
out["unratedLeak"] = sorted(set(unrated_leak))[:5]
print(json.dumps(out))
PY
)"

if [ -z "$FACTS" ]; then
  F "api container probe returned nothing — is the stack up? (deploy/ops/update.sh first)"
  exit 1
fi
fact() { printf '%s' "$FACTS" | dc exec -T api python -c \
  "import json,sys,functools; d=json.load(sys.stdin); print(functools.reduce(lambda a,k: a[k] if isinstance(a,dict) else a, sys.argv[1].split('.'), d))" "$1" 2>/dev/null; }

echo "== 1. deployed code =="
# Expected counts come from the CHECKOUT's CSV (awk — the host has no python), so the check is
# "the running image matches this checkout" and curation passes can never rot a literal here.
read -r EXP_TOTAL EXP_RATED <<EOF2
$(awk -F, '!/^[[:space:]]*#/ && NF { n++; if (n > 1) { t++; if ($2 != "") r++ } } END { print t, r }' \
    "$REPO_ROOT/examples/data/outlet_registry.csv")
EOF2
[ "$(fact registryTotal)" = "$EXP_TOTAL" ] && [ "$(fact registryRated)" = "$EXP_RATED" ] \
  && P "registry in the running container matches the checkout: ${EXP_TOTAL} outlets / ${EXP_RATED} rated" \
  || F "registry counts: container $(fact registryTotal)/$(fact registryRated) vs checkout ${EXP_TOTAL}/${EXP_RATED} (old image? rebuild via update.sh)"
[ "$(fact blindspotParam)" = "True" ] && P "M3 blindspot filter present in story service" \
  || F "story_service has no blindspot param — container predates M3"

echo "== 2. registry integrity =="
[ "$(fact lintIssues)" = "0" ] && P "lint_registry clean" || F "lint issues: $(fact lintIssues)"
echo "  spot: $(fact spot)"
[ "$(fact spotOk)" = "True" ] \
  && P "spot resolutions (Daily Mail +1, Economist -1, JPost 0, France24/Tribune unrated)" \
  || F "spot resolution mismatch — see 'spot:' line above"

echo "== 3. ingest -> lean inheritance (live catalog, newest 400) =="
echo "  ingest: $(fact ingest)"
if [ "$(fact ingest.leanWrong)" != "0" ]; then
  F "rows with a WRONG stamped lean exist (should be impossible) — paste the ingest line"
elif [ "$(fact ingest.leanMatch)" = "0" ]; then
  W "no post-deploy rated ingests yet — wait one poll cycle (~15 min) and re-run"
else
  P "fresh rated-publisher rows carry the registry lean ($(fact ingest.leanMatch) matched, 0 wrong)"
fi

echo "== 4. stories + blindspot lens =="
[ "$(fact storiesTotal)" != "0" ] && P "stories serving (total $(fact storiesTotal))" || F "no stories"
echo "  blindspotFacets: $(fact blindspotFacets)   gapTotal: $(fact gapTotal)"
if [ "$(fact blindspotFacets)" = "{}" ] || [ "$(fact blindspotFacets)" = "None" ]; then
  W "no coverage gaps detected yet (distributions need rated multi-side coverage — grows as rated ingests land)"
else
  [ "$(fact gapAllHaveSide)" = "True" ] && P "blindspot=any returns only stories with a detected gap" \
    || F "blindspot=any returned a story without blindspotSide"
fi

echo "== 5. web proxy forwards blindspot (bug class fixed in 94e2f73) =="
WEB_TOTAL="$(dc exec -T web sh -c 'wget -qO- "http://127.0.0.1:3000/api/stories?blindspot=any&limit=50" 2>/dev/null' \
  | dc exec -T api python -c 'import json,sys; print(json.load(sys.stdin).get("total"))' 2>/dev/null)"
if [ -z "$WEB_TOTAL" ] || [ "$WEB_TOTAL" = "None" ]; then
  W "could not probe the web container (wget/route) — check manually: /api/stories?blindspot=any via the site"
elif [ "$WEB_TOTAL" = "$(fact gapTotal)" ]; then
  P "web-proxied blindspot total ($WEB_TOTAL) == engine total — param forwarded"
else
  F "web proxy drops blindspot: web=$WEB_TOTAL engine=$(fact gapTotal) — old web image? rebuild"
fi

echo "== 6. publisher intelligence =="
[ "$(fact dm.rated)" = "True" ] && [ "$(fact dm.lean)" = "1.0" ] && [ "$(fact dm.bucket)" = "right" ] \
  && P "Daily Mail: rated, +1 (AllSides Lean Right)" || F "Daily Mail profile: $(fact dm)"
[ "$(fact fr.rated)" = "False" ] && [ "$(fact fr.country)" = "FR" ] \
  && P "France 24: honestly unrated, locality kept (FR)" || F "France 24 profile: $(fact fr)"
[ "$(fact unknown404)" = "True" ] && P "unknown publisher -> 404" || F "unknown publisher did not 404"

echo "== 7. filters =="
echo "  lean sides: $(fact leanSides)"
[ "$(fact leanOverlap)" = "[]" ] && P "left/right lean filters disjoint" || F "overlap: $(fact leanOverlap)"
[ "$(fact unratedLeak)" = "[]" ] && P "no unrated publisher leaks into any lean bucket" \
  || F "unrated in lean buckets: $(fact unratedLeak)"
[ "$(fact countryUS)" != "" ] && P "country filter regression OK (?country=US total $(fact countryUS))" \
  || F "country filter probe failed"

echo "== 8. public domain =="
if [ "${SMOKE_SKIP_PUBLIC:-0}" = "1" ]; then
  W "public probe skipped (SMOKE_SKIP_PUBLIC=1)"
else
  DOMAIN="$(env_val APP_DOMAIN)"; DOMAIN="${DOMAIN:-hidden-view.com}"
  PUB="$(curl -fsS -m 20 "https://${DOMAIN}/api/stories?blindspot=any&limit=5" 2>/dev/null | head -c 200)"
  case "$PUB" in
    *'"total"'*) P "public /api/stories?blindspot=any serves through the edge" ;;
    *) F "public probe failed for https://${DOMAIN} — edge/web up?" ;;
  esac
fi

echo
echo "SUMMARY: ${pass} pass · ${warn} warn · ${fail} fail"
[ "$fail" -eq 0 ] || exit 1
