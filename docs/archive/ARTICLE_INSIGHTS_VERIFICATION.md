# Article Insights — production verification report

**Scope:** verify the Article Insights deployment (`docs/ARTICLE_INSIGHTS.md`) in production:
schema, dormant-mode safety, request-path cost, contract integrity, and the enablement gate.
**Deployed:** `f0d33ee` (chain `471e6eb` generator/worker → `e39eb34` provider port →
`e3c7493` store/seam/API/web → `f0d33ee` contract parity), 2026-08-03; post-deploy smoke test
10 PASS / 0 WARN / 0 FAIL.
**State verified:** the feature as shipped — **dormant by default**. Enablement (a spend + key
decision) is deliberately a separate, env-only step; its gate and probe are §4.

**Verdict in one line:** verified — the schema landed, the dormant chain holds end to end
(gate off → provider unrunnable → zero table writes → no worker thread), the request path
serves the new nullable `insights` contract field with a measured added cost of **0.65 ms per
lookup** (~0.5% of the analyze endpoint's 112–122 ms), and the analysis contract is byte-intact.

## 1. What was verified on the box (read-only probe, in-container)

| check | result | reading |
|---|---|---|
| feature gate chain | `enabled=False`, `provider=anthropic`, `runnable=False` | dormant for two independent reasons (flag unset AND no key) — either alone suffices |
| `article_insights` table | exists, **empty** | schema created by the boot migration; zero writes while dormant — the "provably off" contract |
| `get_insights` cache-miss cost | **0.65 ms / lookup** (200 iterations) | the *entire* request-path cost the feature adds — one indexed primary-key read |
| `/api/analyze` ×5 | 111.9–122.2 ms; `insights` key present, value `null` | the endpoint serves the new contract field; a miss costs the 0.65 ms above and renders nothing |
| contract integrity | `status=analyzed`, `source=catalog` | wire == service byte parity holds (the pinned-null key lives in `article_analyzer`, filled only on cache hit) |
| deployment smoke | 10 PASS / 0 WARN / 0 FAIL | containers, engine liveness/readiness, analytics gating, metrics, TLS edge |

**Page-performance claim, stated honestly:** no pre-change box baseline of `/api/analyze` was
captured, so the before/after rests on the measured *component* cost: the only code added to the
request path is one batched `status='ok'` primary-key read, measured at 0.65 ms against the live
catalog — noise-level against the endpoint's 112–122 ms. The worker can never run on a request
thread by construction (poller seam, single-flight daemon).

## 2. What was verified before deploy (dev gates, at `f0d33ee`)

Engine suite **2,610 passed** (+19 for the feature; contract pins updated and analysis goldens
regenerated via their build script). Web: `tsc`, ESLint, `check:i18n` (844 keys × 5 catalogs),
359 unit tests. E2E: the insights spec passes (seeds through the real store accessors; cached
artifact renders the "AI summary & framing" section, absence renders nothing); the two failing
specs in the dev container (`saved`, `recommendation-feedback`) were proven **pre-existing** by
running them against the pre-change engine from a git worktree — identical failures, an
empty-recommendations mechanism this feature's diff does not touch.

## 3. Architecture as deployed (summary; full design in `docs/ARTICLE_INSIGHTS.md`)

Generation is asynchronous (poller post-cycle seam, bounded batch, single-flight), cached
forever (`article_insights`, canonical-URL dedup, content-hash regeneration on description
backfill), failure-isolated (attempts + exponential backoff, terminal `failed`), and
provider-agnostic (`AIInsightsProvider` port; Anthropic adapter first; vendor and model are
pure env configuration). The request path is cache-only and the UI renders only what exists —
no placeholders, no loading states, no request-path generation, ever.

## 4. The remaining step: enablement (a product/spend decision, env-only)

Not enabled during this verification — it requires an `ANTHROPIC_API_KEY` and a cost sign-off:

- **Cost model:** ~`RWE_INSIGHTS_BATCH` (6) articles per ingest cycle × (~1–3k input + ≤700
  output tokens) at the configured model's pricing. On `claude-opus-4-8` with a short poll
  cycle this compounds to a real daily spend; the dials are `RWE_INSIGHTS_BATCH`,
  `RWE_INSIGHTS_MIN_CHARS`, and `RWE_INSIGHTS_MODEL` — all env, no code.
- **Enable:** `RWE_INSIGHTS_ENABLED=1` + the key in `deploy/.env`, then
  `sudo bash deploy/ops/restart.sh api`.
- **Verify after ≥1 cycle:** row counts by status, a timed bounded batch for real per-article
  generation latency, `/api/analyze` serving a generated artifact, and a hand read of ~3
  samples against the grounding and no-label rules (the quality gate from the design's test
  plan). This section of the report is to be appended when that measurement is taken.
- **Rollback:** remove the env line, restart — the cache keeps serving already-generated rows
  (or delete them; the request path treats both identically).

## 5. Conclusion

The feature is production-deployed, dormant, and verified safe in that state: zero behavior
change, zero writes, ~0.65 ms of request-path cost serving an explicit `null`. Turning it on is
one env decision away, with the measurement plan and rollback already written down.
