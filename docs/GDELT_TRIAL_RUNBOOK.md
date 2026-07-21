# GDELT Controlled-Trial Runbook (hidden-view.com)

Operational runbook for a **controlled trial** of the GDELT ingestion source. GDELT is a keyless second
provider; its adapter is already registered (`examples/sources.py`) and the config is **wired but
disabled by default** (`RWE_GDELT_ENABLED=0`). Enabling it is a **deliberate operational decision** — do
not enable without reading this end-to-end.

> Why GDELT is safe-by-construction: GDELT is high-volume and mostly unknown-outlet. Those articles have
> no resolvable lean, so they are **searchable but NOT recommendable** — the qbias recommendation corpus
> drops no-lean rows (`docs/CORPUS_ARCHITECTURE.md`, enforced by `tests/test_corpus_boundaries.py`). The
> risk is therefore *storage volume*, not recommendation quality — which the retention cap bounds.

Conservative trial config (defaults in `deploy/docker-compose.yml`): `RWE_GDELT_MAX_ARTICLES=25`,
`RWE_GDELT_POLL_INTERVAL=1800` (30 min), built-in topic query. **Retention is OFF by default and MUST be
set before enabling.**

---

## 0. Prerequisites

- The compose/rules/`.env`-template changes are deployed (this commit) and pulled on the box (`cd /opt/ih && git pull`).
- You can reach the box via SSM and run `docker` as `ubuntu`.

## 1. Baseline capture (BEFORE enabling)

Snapshot the pre-GDELT state so you can measure GDELT's contribution and growth:

```bash
echo "== catalog + storage =="; docker exec deploy-api-1 python -c "
import sqlite3; c=sqlite3.connect('/app/data/ih_beta.db')
print('feed_articles :', c.execute('select count(*) from feed_articles').fetchone()[0])
print('with_lean     :', c.execute(\"select count(*) from feed_articles where json_extract(scored,'\$.lean') is not null\").fetchone()[0])
"
echo "== per-source health =="; docker exec deploy-api-1 python -c "
import urllib.request,os,json; h={'X-IH-Auth':os.environ['RWE_INTERNAL_SECRET']}
d=json.load(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/api/internal/feeds',headers=h),timeout=15))
print('feed_health rows:', len(d), '| sources:', sorted({r.get('feedUrl') for r in d})[:12])
"
echo "== DB size =="; docker exec deploy-api-1 python -c "
import urllib.request,os,json; h={'X-IH-Auth':os.environ['RWE_INTERNAL_SECRET']}
print('sizeBytes:', json.load(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/api/internal/storage',headers=h),timeout=15))['sizeBytes'])
"
```
Record: `feed_articles`, `with_lean`, `feed_health rows`, `sizeBytes`.

## 2. Enable sequence

```bash
cd /opt/ih && git pull origin claude/sleepy-gates-oecof1
# (a) set the REQUIRED retention cap + enable GDELT in deploy/.env (editor-free, idempotent):
for kv in RWE_RETENTION_MAX_COUNT=20000 RWE_GDELT_ENABLED=1; do
  k=${kv%%=*}; grep -q "^$k=" deploy/.env && sed -i "s|^$k=.*|$kv|" deploy/.env || echo "$kv" >> deploy/.env
done
grep -nE 'RWE_GDELT_ENABLED|RWE_RETENTION_MAX_COUNT' deploy/.env
# (b) prove the drift guard fires + passes for the GDELT-on case (retention wired):
RWE_GDELT_ENABLED=1 deploy/ops/validate-deployment.py
# (c) recreate ONLY the api container with the new env (no rebuild, nothing else touched):
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.aws.yml --env-file deploy/.env up -d --no-deps api
```

## 3. Verification (immediately, then at ~2 min)

```bash
echo "== flags reached the container =="; docker exec deploy-api-1 printenv | grep -E 'RWE_GDELT|RWE_RETENTION' | sort
echo "== poller started GDELT =="; docker logs deploy-api-1 2>&1 | grep -E 'multi_source_start|source_poll_start' | tail -4
echo "== first GDELT poll cycle (wait ~30-60s) =="; docker logs --timestamps deploy-api-1 2>&1 | grep -E 'provider": "GDELT"|source_poll' | tail -6
echo "== GDELT feed health =="; docker exec deploy-api-1 python -c "
import urllib.request,os,json; h={'X-IH-Auth':os.environ['RWE_INTERNAL_SECRET']}
d=json.load(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/api/internal/feeds',headers=h),timeout=20))
g=[r for r in d if 'gdelt' in (r.get('feedUrl') or '').lower()]
print(json.dumps(g, indent=2) if g else 'no GDELT row yet — wait for the first cycle')
"
```
**Pass:** `RWE_GDELT_ENABLED=1` + `RWE_RETENTION_MAX_COUNT=20000` in the container; `multi_source_start … adapters` includes `GDELT`; a `gdelt://doc` health row appears `healthy` with `totalOk ≥ 1`; a `source_poll` line for `GDELT` shows `failed:0`.

## 4. Monitoring (ongoing — 24h / 72h)

```bash
# per-source contribution (imported vs duplicate vs failures) + catalog growth + DB size:
docker exec deploy-api-1 python -c "
import sqlite3,urllib.request,os,json; h={'X-IH-Auth':os.environ['RWE_INTERNAL_SECRET']}
c=sqlite3.connect('/app/data/ih_beta.db')
print('feed_articles :', c.execute('select count(*) from feed_articles').fetchone()[0])
d=json.load(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/api/internal/feeds',headers=h),timeout=20))
for r in d:
    if 'gdelt' in (r.get('feedUrl') or '').lower():
        print('GDELT: ok=%s fail=%s imported=%s duplicate=%s rejected=%s stale=%s' % (
            r.get('totalOk'), r.get('totalFailed'), r.get('imported'), r.get('duplicate'), r.get('rejected'), r.get('stale')))
s=json.load(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/api/internal/storage',headers=h),timeout=15))
print('sizeBytes     :', s['sizeBytes'])
"
```
Compare against the §1 baseline. `imported` is GDELT's genuine new-article contribution; `duplicate` is overlap; growth should trend toward the `RWE_RETENTION_MAX_COUNT` ceiling and plateau.

## 5. Rollback (immediate disable)

```bash
cd /opt/ih
sed -i 's|^RWE_GDELT_ENABLED=.*|RWE_GDELT_ENABLED=0|' deploy/.env
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.aws.yml --env-file deploy/.env up -d --no-deps api
docker logs deploy-api-1 2>&1 | grep -E 'multi_source_start' | tail -1   # adapters should be ["RSS"] again
```
GDELT stops polling immediately. Already-ingested GDELT articles remain **searchable** (dataset ①) and are pruned over time by retention; no purge is required. RSS is unaffected throughout.

## 6. Expected healthy behaviour

- `gdelt://doc` health: `healthy`, `totalFailed` low, `stale:false` (GDELT sorts newest-first).
- `source_poll` for GDELT: `new>0` early, `duplicates` rising as the query saturates, `failed:0`.
- Catalog grows for a while, then **plateaus at `RWE_RETENTION_MAX_COUNT`**.
- Recommendation candidate grows only **modestly** — only lean-resolvable (known-outlet) GDELT enters ②; the unknown-outlet majority stays searchable-only.
- **RSS is unchanged** — its feed-health rows and cadence look exactly as before.

## 7. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| No `gdelt://doc` row in `/api/internal/feeds` | GDELT not enabled/wired | `docker exec deploy-api-1 printenv RWE_GDELT_ENABLED` → must be `1`; `docker logs … grep multi_source_start` → adapters must include `GDELT`; if missing, confirm `.env` + recreate `api`. |
| GDELT `totalFailed` climbing / 429s | Upstream rate-limit or network | Transient (retry/backoff handles it); if persistent, **rollback** (§5). Fault-isolated — RSS keeps polling. |
| Catalog / DB growing without bound | `RWE_RETENTION_MAX_COUNT=0` | Set it non-zero in `.env` (the `gdelt-bounded-catalog` drift rule flags the wiring) and recreate `api`. |
| GDELT `new:0` after warm-up | Query saturated or too narrow | Expected as it saturates; to widen coverage set `RWE_GDELT_QUERY` (or accept steady state). |
| Recommendation quality looks worse | Known-outlet GDELT diluting recs | **Rollback** (§5). The no-lean drop should prevent this; if it happens, disable and review candidate composition. |
| Validation fails on enable | Retention not wired | `RWE_GDELT_ENABLED=1 deploy/ops/validate-deployment.py` names the missing dependency + fix. |

---

*Related: `docs/CORPUS_ARCHITECTURE.md` (why GDELT is searchable-not-recommendable), `docs/DEPLOYMENT_RUNBOOK.md` (lifecycle), `docs/PRODUCTION_ENVIRONMENT.md` (env reference).*
