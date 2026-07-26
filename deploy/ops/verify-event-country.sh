#!/usr/bin/env bash
# Verify the Event Country rollout on the RUNNING production stack. DEPLOYMENT-ONLY (read-only
# probes; no application change). Run after deploy/ops/update.sh — companion to the general
# smoke test, covering the Location Intelligence checklist specifically:
#
#   1  RWE_GDELT_GKG reaches the api container (compose default: ON)
#   2  the GKG enricher is registered + has run (gdelt://gkg health row)
#   3  event-country data is populated (article_event_locations side table)
#   4  the Stories Country selector has options (public /api/places/countries, articles > 0)
#   5  ?country= filters by EVENT location — incl. the negative control: a registry-only
#      country with zero located articles must return zero stories (publisher homes never leak)
#   6  "All" (no filter) still returns the complete feed, ≥ every filtered view
#
#   deploy/ops/verify-event-country.sh
#   SMOKE_SKIP_PUBLIC=1 deploy/ops/verify-event-country.sh   # skip the public-domain probe
#
# Fresh deploys: the side table starts empty until the enricher's first cycles run. That prints
# as WARN (not FAIL) with the one-time backfill from docs/AWS_EC2_DEPLOYMENT_GUIDE.md §6a.
set -uo pipefail
source "$(dirname "$0")/_compose.sh"

pass=0 warn=0 fail=0
P() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; pass=$((pass + 1)); }
W() { printf '  \033[33mWARN\033[0m  %s\n' "$1"; warn=$((warn + 1)); }
F() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=$((fail + 1)); }

# One python probe inside the api container prints every fact this script judges (single exec,
# so the checks can't disagree about when they sampled the engine).
FACTS="$(dc exec -T api python - <<'PY' 2>/dev/null
import json, os, urllib.request

def get(path, secret=False):
    h = {"X-IH-Auth": os.environ.get("RWE_INTERNAL_SECRET", "")} if secret else {}
    req = urllib.request.Request("http://127.0.0.1:8000" + path, headers=h)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

out = {"flag": os.environ.get("RWE_GDELT_GKG", ""), "windows": os.environ.get("RWE_GDELT_GKG_WINDOWS", "4")}

try:
    feeds = get("/api/internal/feeds", secret=True)
    gkg = next((f for f in feeds if "gkg" in (f.get("feedUrl") or f.get("name") or "").lower()
                or "gkg" in json.dumps(f).lower()), None)
    out["gkgHealthRow"] = bool(gkg)
    out["gkgHealthy"] = bool(gkg and gkg.get("healthy"))
    out["gkgLastSuccess"] = (gkg or {}).get("lastSuccessAt") or (gkg or {}).get("lastSuccess")
except Exception as e:
    out["gkgHealthRow"] = out["gkgHealthy"] = False
    out["healthErr"] = f"{type(e).__name__}: {e}"

places = get("/api/places/countries")
located = [c for c in places if c["articles"] > 0]
out["locatedCountries"] = [c["country"] for c in located]
out["locatedArticles"] = sum(c["articles"] for c in located)
out["registryOnly"] = next((c["country"] for c in places
                            if c["articles"] == 0 and c["registryPublishers"] > 0), None)

out["allTotal"] = get("/api/stories?limit=1")["total"]
if located:
    top = located[0]["country"]
    out["topCountry"] = top
    out["topCountryStories"] = get(f"/api/stories?country={top}&limit=1")["total"]
if out["registryOnly"]:
    out["registryOnlyStories"] = get(f"/api/stories?country={out['registryOnly']}&limit=1")["total"]

print(json.dumps(out))
PY
)"
if [ -z "$FACTS" ]; then
  F "could not probe the api container (is the stack up? deploy/ops/deploy.sh first)"
  echo "SUMMARY: pass=$pass warn=$warn fail=$fail"; exit 1
fi
fact() { printf '%s' "$FACTS" | dc exec -T api python -c "import json,sys; d=json.load(sys.stdin); v=d.get('$1'); print('' if v is None else v)"; }

# 1 · flag
[ "$(fact flag)" = "1" ] && P "RWE_GDELT_GKG=1 reaches the api container" \
                         || F "RWE_GDELT_GKG is '$(fact flag)' in the api container (expected 1)"

# 2 · enricher ran
if [ "$(fact gkgHealthRow)" = "True" ]; then
  [ "$(fact gkgHealthy)" = "True" ] && P "gdelt://gkg health row present + healthy (last success: $(fact gkgLastSuccess))" \
                                    || W "gdelt://gkg health row present but not healthy — check: dc logs api | grep -i gkg"
else
  W "no gdelt://gkg health row yet — first cycle pending (interval: 15 min) $(fact healthErr)"
fi

# 3 · event data populated
LOCATED_N="$(fact locatedArticles)"
if [ "${LOCATED_N:-0}" -gt 0 ] 2>/dev/null; then
  P "event-country data populated: $LOCATED_N located articles across [$(fact locatedCountries)]"
else
  W "0 located articles — the AUTO-BACKFILL runs on the enricher's next cycle (≤15 min: a deep
          first pass over an unlocated catalog, no manual steps). Re-run this script after; if it
          still reads 0, see docs/AWS_EC2_DEPLOYMENT_GUIDE.md §6a for the manual override + the
          non-overlap diagnosis."
fi

# 4 · the Stories Country selector (renders iff options exist; options = articles > 0)
if [ "${LOCATED_N:-0}" -gt 0 ] 2>/dev/null; then
  P "Country selector will render on /stories — options: [$(fact locatedCountries)]"
else
  W "Country selector hidden (by design) until event data exists"
fi

# 5 · event-location semantics
TOP="$(fact topCountry)"
if [ -n "$TOP" ]; then
  TS="$(fact topCountryStories)"; ALL="$(fact allTotal)"
  if [ "${TS:-0}" -le "${ALL:-0}" ] 2>/dev/null; then
    P "?country=$TOP returns $TS stories (⊆ the $ALL-story feed)"
  else
    F "?country=$TOP returned $TS > unfiltered $ALL — filter broken"
  fi
fi
RO="$(fact registryOnly)"
if [ -n "$RO" ]; then
  ROS="$(fact registryOnlyStories)"
  [ "${ROS:-0}" -eq 0 ] 2>/dev/null \
    && P "negative control: $RO (publishers in registry, 0 located articles) returns 0 stories — publisher homes never match" \
    || F "negative control FAILED: $RO has 0 located articles but ?country=$RO returned $ROS stories"
else
  W "no registry-only country available for the negative control (all registry countries have located coverage)"
fi

# 6 · "All" = the complete feed
ALL="$(fact allTotal)"
[ "${ALL:-0}" -gt 0 ] 2>/dev/null && P "unfiltered feed serves $ALL stories (\"All\" intact)" \
                                  || F "unfiltered /api/stories returned $ALL stories"

# public probe: what a reader's browser fetches for the dropdown
if [ "${SMOKE_SKIP_PUBLIC:-}" != "1" ]; then
  DOMAIN="$(env_val APP_DOMAIN)"; DOMAIN="${DOMAIN:-hidden-view.com}"
  CODE="$(curl -fsS -o /dev/null -w '%{http_code}' "https://$DOMAIN/api/places/countries" 2>/dev/null || echo 000)"
  [ "$CODE" = "200" ] && P "public https://$DOMAIN/api/places/countries -> 200 (the dropdown's own fetch path)" \
                      || W "public places probe returned $CODE (SMOKE_SKIP_PUBLIC=1 to silence before DNS/TLS is live)"
fi

echo ""
echo "SUMMARY: pass=$pass warn=$warn fail=$fail"
[ "$fail" -eq 0 ] || exit 1
