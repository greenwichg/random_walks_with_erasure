# Subscription-aware Article Insights — design & migration plan

**Status:** design only. No code in this change; nothing is implemented.
**Builds on:** `docs/ARTICLE_INSIGHTS.md` (the feature), `docs/OLLAMA_PROVIDER_VERIFICATION.md`
(the second provider), `docs/ARTICLE_INSIGHTS_VERIFICATION.md` (production state).
**Date:** 2026-08-03.

---

## 0. The recommendation in one line

Key the cache by **variant** — a named, config-defined recipe (`provider` + `model`) — never by
user and never by tier; map tiers *onto* variants in configuration, defaulting every tier to the
same variant so that tiering is opt-in and a deployment that ignores it is byte-identical to
today.

## 1. The tension in the requirements, and how it resolves

Two requirements pull against each other:

> Premium users should use a higher-quality configurable model.
> AI insights should continue to be generated once per article and cached; do not generate
> different summaries per user unless there is a compelling architectural reason.

If premium genuinely runs a different model, then more than one artifact per article must exist —
that is arithmetic, not architecture. The question is only **what the second axis is keyed on**.

| axis | artifacts per article | verdict |
|---|---|---|
| per **user** | O(users) | **rejected** — cost and storage scale with the audience, cache hit rate collapses, and per-user artifacts turn published-journalism analysis into personal data with a retention obligation. No product benefit: reader-relative content is already composed at read time (see §2.3). |
| per **tier** | O(tiers), forever | **rejected as the key** — tiers are a commercial concept that changes with pricing; the cache would be re-keyed by a marketing decision. Two tiers on the same model would also duplicate rows for nothing. |
| per **variant** (recipe) | O(distinct recipes actually enabled) | **recommended** — the cache records *how* an artifact was made. Tiers are a mapping onto variants, so two tiers sharing a model share a row, and re-pricing never touches storage. |

**"Once per article" survives, correctly restated:** once per article *per distinct way of making
it*. With one variant enabled that is literally once per article — today's behaviour, unchanged.

## 2. Recommended architecture

### 2.1 Three new concepts, all configuration

```
subscription tier ──(policy map)──▶ variant ──(recipe)──▶ provider + model ──▶ AIInsightsProvider
     free                            standard              ollama / llama3.1        (unchanged port)
     premium                         premium               anthropic / claude-opus-4-8
     enterprise (default)            premium
     enterprise (override)           tenant:<org>          <admin's choice>
```

1. **Variant** — an opaque name (`standard`, `premium`, `tenant:acme`). Storage, worker, API and
   UI treat it as a string; none of them learn what a provider is.
2. **Recipe** — `variant → {provider, model}`, plus a per-variant **coverage policy** (§4.2).
   Exactly the shape the benchmark harness already uses for its targets, which is evidence the
   shape works.
3. **Policy** — `tier → variant`, with an optional per-organization override. Config, reloadable,
   with a documented default that maps every tier to `standard`.

### 2.2 What stays untouched (the invariants this design must not break)

| component | today | after |
|---|---|---|
| `AIInsightsProvider` port | `complete(system, user, model, max_tokens) → str` | **identical** |
| Prompt, JSON contract, 2–4-sentence bound, no-label rule | one shared policy | **identical, for every variant** |
| Validator | provider-independent | **identical** — it is the equalizer: a premium artifact cannot break the UI because it passed the same gate |
| Storage semantics | cache-forever, canonical-URL dedup, backoff, terminal failure | **identical**, plus one opaque column |
| Request path | cache-only, one indexed read, `insights: null` on miss | **identical shape**, one extra equality predicate |
| Worker | poller seam, single-flight, bounded batch | **identical**, iterated per enabled variant |
| UI | renders when present, nothing otherwise | **identical**, plus an optional provenance line |

**Contract rule:** variants may differ in *model*, never in *contract*. A variant that changed the
prompt shape would fork the artifact schema and force the UI to branch — which is exactly the
provider-independence this architecture exists to protect.

### 2.3 Why per-user generation is not needed even for a "personalised" product

The product already personalises **at read time, by composition**: `analysis_enrichment` adds the
reader-relative `explanation` / `recommendation` sections, and the explain panel computes the
reader's own distance to the article. Those are cheap, deterministic, and reader-specific. The
insight itself is a property of the *article* — what it says, how it frames, what it omits — and
is identical for every reader. Personalisation belongs in the composition layer; generation stays
universal. That is the compelling-reason test the requirement asks for, and it fails: there is no
compelling reason.

### 2.4 Read path and fallback

`GET`-side resolution is: `tier(user) → variant → read (article_id, variant)`. Two misses are
possible and they need different answers:

| case | behaviour | why |
|---|---|---|
| premium variant not yet generated | serve `standard` and stamp `insights.servedVariant` | an empty panel is worse than a good-enough one; the stamp keeps the UI honest and lets it offer "a deeper analysis is being prepared" |
| no variant at all | serve `null`, exactly as today | unchanged contract |

The payload gains `servedVariant` (and optionally `model`, already present). **Whether a premium
reader is told they are seeing the standard artifact is a product decision**, not a technical one;
the architecture supports either.

### 2.5 The one seam that must extend (and why it is additive)

Today provider selection is `insights_provider.from_env()` — process-global environment. Two
variants generated in one process cannot both be described by process env, and the worker runs on
a **thread**, so mutating `os.environ` per variant (what the benchmark harness does around each
target, safely, because it is single-threaded) would be a data race in production.

The extension is a sibling, not a change: `from_recipe({"provider": …, "model": …})` returning the
same `AIInsightsProvider`. `from_env()` remains and keeps its behaviour, so single-variant
deployments are untouched. **This is the only production seam this design adds**, and it adds no
vendor knowledge — it reads the same registry.

## 3. Q2 — Storage implications

### 3.1 Schema

`article_insights` gains one column and a composite key:

```
PRIMARY KEY (article_id, variant)      -- today: (article_id)
variant       TEXT NOT NULL DEFAULT 'standard'
recipe_hash   TEXT                     -- hash(prompt_version, provider, model)
```

`model` (already `"<provider>:<model>"`) stays as the audit trail. `recipe_hash` is the
**staleness rule**, and it reuses machinery that already exists: today a changed `content_hash`
resets an `ok` row to `pending`; tomorrow a changed `recipe_hash` does the same, so "we upgraded
the premium model, regenerate" needs no new code path and no manual purge.

### 3.2 Size, honestly

A row is roughly: summary ~300 chars + five bias fields ~700 chars + keys/overhead ≈ **1.2–1.5 KB**
(estimate from the artifact shape, not a measurement). At the production catalog's ~35,800-article
rolling window:

| variants enabled | rows | approximate size |
|---|---:|---:|
| 1 (today) | ~36 k | ~50 MB |
| 2 (standard + premium, both catalog-wide) | ~72 k | ~100 MB |
| 2 + one tenant override, demand-driven | ~72 k + tenant reads | ~100 MB + small |

On a single EC2 box with SQLite this is material but not alarming. **It exposes a gap that exists
already:** insights rows are cache-forever while articles age out of the rolling window, so the
table grows without bound and only *looks* fine today because the feature is young. **Recommend a
retention pass keyed to the catalog window** (delete insights whose article is no longer in the
catalog) as part of Phase 1 — it is cheap now and awkward later, and it is needed whether or not
tiering ships.

### 3.3 Query cost

The request path becomes `WHERE article_id IN (…) AND variant = ? AND status = 'ok'`, served
directly by the composite primary key — the same single indexed lookup measured at **0.65 ms** in
production. No new index, no fan-out, no change to the measured request-path cost.

### 3.4 SQLite migration mechanics

SQLite cannot alter a primary key in place. The table must be rebuilt: create the new table, copy
rows with `variant = 'standard'`, drop, rename — inside one transaction. This is standard practice
and the table is small. The tempting shortcut (drop and let it regenerate, since it *is* a cache)
should be **refused for any deployment where a paid model generated the rows**: that is discarding
money. For an Ollama-only deployment it is a legitimate fast path.

## 4. Q3 — Cost implications

### 4.1 The model

```
monthly cost(variant) = new_eligible_articles_per_month × coverage(variant) × cost_per_article
cost_per_article ≈ (input_tokens × price_in + output_tokens × price_out) / 1e6
```

With the shipped prompt (~1–3 k input, ≤700 output) and the price table currently in
`data/insights_benchmark_targets.json` (**operator-maintained; verify before quoting**):

| variant | per article (est.) | catalog-wide / month (est.) |
|---|---:|---:|
| Ollama local | **$0.00** (electricity + wall-clock only) | $0.00 |
| Claude Opus 4.8 @ $5/$25 per Mtok | ~$0.02 | ~$0.02 × new eligible articles |

At an order of 1,000 new eligible articles/day, a catalog-wide premium variant is **≈$600/month**;
the free variant on Ollama is ≈$0. The benchmark harness (`--sample-production`) is the instrument
for replacing these estimates with measured token counts before anyone commits budget.

### 4.2 The lever that actually matters: coverage policy per variant

Catalog-wide pre-generation is the right policy for a **free** variant (marginal cost zero). It is
the *wrong* policy for a paid one, because most articles are never opened by a premium reader.
Three coverage policies, all config:

| policy | what it generates | cost shape |
|---|---|---|
| `catalog` | every eligible article | O(catalog) — correct for local models |
| `prominent` | articles in stories above a size/publisher floor | O(hundreds/day) |
| `demand` | on a premium read-miss, enqueued asynchronously; the reader gets `standard` now and the premium artifact next visit | O(articles premium users actually open) |

**`demand` is the recommended default for paid variants.** It converts the premium bill from a
function of the catalog into a function of engaged usage — typically an order of magnitude less —
and it degrades gracefully because §2.4's fallback already serves something.

Guardrails worth having whatever the policy: a per-variant daily spend/row cap, and the existing
per-cycle batch cap applied **per variant** rather than shared (otherwise adding premium halves
free-tier throughput).

### 4.3 The cheaper alternative nobody asked for, stated because it is real

The same commercial differentiation can be bought at **zero marginal generation cost** by tiering
*presentation* rather than *model*: everyone gets one artifact; free readers see the summary,
premium readers additionally see the framing analysis, loaded-language evidence and omissions.
Storage stays at one row per article, the cost model stays at $0, and the migration is a UI change.

It does not satisfy the stated requirement ("premium should use a higher-quality model"), so it is
not the recommendation — but it is a legitimate first move, and it composes with this design
rather than competing: ship presentation tiering now, and turn on a premium *variant* later if
measured quality (via the benchmark's golden + production suites) actually justifies the spend.
Deciding that on evidence rather than assumption is what the benchmark harness exists for.

## 5. Q5 — The smallest architecture that supports Claude, Gemini, GPT, Grok and Ollama

**It already exists, and this design adds nothing per-vendor.** A vendor is one adapter class
implementing `complete()` plus one registry row (`insights_provider._REGISTRY`); Anthropic and
Ollama demonstrate the two shapes — hosted SDK and local HTTP — and Gemini, GPT and Grok are all
one of those two.

The complete inventory of what tiering adds on top:

1. one opaque column (`variant`) + composite key + `recipe_hash`;
2. one config file: variants → recipes, tiers → variants, coverage policy per variant;
3. one additive resolver: `from_recipe(...)` beside `from_env()`;
4. worker loops over enabled variants; API resolves the caller's variant.

No vendor branches anywhere. Adding Gemini for premium after this lands is: write the adapter,
add the registry row, change one line of config. Nothing else moves.

## 6. Q6 — Recommendation and trade-offs

| option | artifacts/article | premium quality | marginal cost | storage | complexity | verdict |
|---|---|---|---|---|---|---|
| **A** one shared artifact | 1 | none | $0 | 1× | none | fails the requirement |
| **A′** shared artifact, tiered *presentation* | 1 | none (perceived: yes) | $0 | 1× | UI only | strong interim move (§4.3) |
| **B** variant-keyed cache, tiers mapped on **(recommended)** | 1 per enabled recipe | yes | controlled by coverage policy | 1–2× | one column + config + resolver | **recommended** |
| **C** per-tier key | 1 per tier always | yes | duplicates when tiers share a model | ≥ tiers × | same as B plus churn on re-pricing | rejected |
| **D** per-user | O(users) | yes | unbounded | unbounded | high, plus personal-data obligations | rejected |

**Trade-offs accepted by choosing B:**

- *A premium reader may briefly see the standard artifact* (§2.4 fallback). Accepted: an empty
  panel is worse, and the alternative — blocking on generation — puts an LLM call on the request
  path, which the whole architecture exists to prevent.
- *Storage roughly doubles per enabled variant.* Accepted, and it forces the retention policy that
  is overdue anyway.
- *Two variants mean two failure surfaces.* Mitigated: per-variant metrics and the existing
  backoff/terminal-failure machinery apply unchanged; a dead premium provider degrades to
  standard rather than to nothing.
- *A config file becomes load-bearing.* Mitigated by defaults: absent config ⇒ one variant ⇒
  today's behaviour exactly.

## 7. Q4 — Migration plan

Each phase is independently shippable, dormant by default, and separately reversible. No phase
requires the next one.

### Phase 0 — prerequisite: there is no subscription entity today (verified)

`users` holds `id / email / display_name / created_at`; there is no organization, tenant, plan or
subscription table anywhere in the store. **Tiering has no input until something records a tier.**
Smallest sufficient step: a `tier` attribute per user (nullable, default free) plus, for
enterprise, an organization entity and membership — the latter is a real piece of product work and
is why enterprise is last. *Do not* infer tier from the beta allowlist or from settings JSON; a
commercial fact deserves its own column.

### Phase 1 — storage becomes variant-aware (no behaviour change)

Rebuild `article_insights` with `PRIMARY KEY (article_id, variant)`, backfill existing rows as
`variant='standard'`, add `recipe_hash`, and add the retention pass from §3.2. Accessors take an
optional `variant` defaulting to `'standard'`. Worker, API and UI are untouched; every read and
write continues to hit exactly one row.
*Verify:* row count unchanged after migration; the production dormant-mode probe still reports
0.65 ms lookups and `insights` served identically. *Rollback:* the old table is retained under a
suffix for one release.

### Phase 2 — configuration and resolution (still one variant)

Introduce the variants/recipes/policy config with a default that maps every tier to `standard`,
and `from_recipe()` beside `from_env()`. The worker resolves `standard` through the new path.
*Verify:* generated artifacts and the `"<provider>:<model>"` stamp are unchanged; the benchmark's
golden suite shows no pass-rate change. *Rollback:* config absent ⇒ `from_env()` path.

### Phase 3 — the premium variant, behind its own flag

Enable a second variant with `coverage: demand`, a spend cap, and its own batch budget. The API
resolves tier → variant and applies the §2.4 fallback; the UI optionally shows provenance.
*Verify on a small cohort first:* premium pass rate and latency from the benchmark's production
sample, measured spend against the cap, and that free-tier throughput did not move.
*Rollback:* disable the variant — premium readers fall back to `standard` automatically, and the
rows already paid for remain valid if it is re-enabled.

### Phase 4 — enterprise administrator choice (optional, last)

Requires the organization entity from Phase 0. Adds tenant-scoped variants (`tenant:<org>`) with
demand-only coverage and a mandatory spend cap. **One hard constraint:** articles ingested from a
tenant's own private sources (the extension path creates provisional articles) must never be
generated by a shared variant or written into a globally-readable row — tenant scope is a
correctness boundary there, not a preference.
*Verify:* a tenant's artifacts are invisible to other tenants; global artifacts remain shared.
*Rollback:* clear the override; the tenant falls back to the premium/standard chain.

## 8. Open questions for the product owner

1. **Is premium's differentiator model quality, or content depth?** (§4.3) — the cheaper answer
   may be the better product answer, and it changes the phasing.
2. **Should a premium reader be told they are seeing the standard artifact** during the fallback
   window, or should the difference be silent?
3. **What is the acceptable monthly ceiling** for paid generation? That single number picks the
   coverage policy in §4.2.
4. **Does enterprise "choose the model" mean choose from a curated list, or supply arbitrary
   credentials?** The latter turns the config into a secrets-management problem and deserves its
   own design.

## 9. What would change this recommendation

- If measured quality on the benchmark's golden + production suites showed a **small** gap between
  the free and premium models, the honest move is A′ (tier presentation, not model) and this
  design stays on the shelf.
- If tiers were ever to need **different contracts** (not just different models), the variant
  abstraction is the wrong tool — that is a schema-versioning problem and should be designed as
  one.
- If per-reader artifacts ever became genuinely necessary (e.g. a summary written *for* the
  reader's prior knowledge), the cache key would change from variant to (variant, reader-segment)
  — segments, still never individual users, and only with measured evidence that it beats read-time
  composition.
