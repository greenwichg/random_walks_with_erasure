# Coverage Comparison (insight-derived) — implementation log

Running record of building `docs/COVERAGE_COMPARISON_REVISED_DESIGN.md` (revision 2), phase by
phase, with the production verification each phase is gated on and **every deviation from the
approved design stated and justified before it is taken**.

| phase | scope | state |
|---|---|---|
| **0a** | comparable-set readiness probe (design §11 items 1, 2, 3, 8) | **built; awaiting production run** |
| 0b | generation-dependent readiness (§11 items 4–7) | unblocked by Phase 1; runs at enablement |
| **1** | contract extension + validation + worker scale (dormant) | **built; awaiting production run** |
| 2 | enablement on the designated recipe | blocked on the 0a gate **and** 0b |
| 3–5 | tiers C1 / C2 / C3 | not started |

---

## D1 — Deviation: Phase 0 splits into 0a and 0b

**Approved design:** §13 lists Phase 0 as one gate over the eight measurements in §11.

**What was built:** the four measurements that need no generated insight (items 1, 2, 3, 8) ship
now as `examples/audit_coverage_readiness.py`. The other four (4 generation latency, 5 enum
reliability, 6 quantity yield and span-verification, 7 token distribution) run after Phase 1.

**Justification.** Items 4–7 measure the output of the extended prompt, which does not exist until
Phase 1 creates it — §11 itself says to run them with `benchmark_insights.py --sample-production`,
a harness that can only sample a contract that has shipped. The design's own gate wording is *"no
**tier** is built before these numbers exist"*, and Phase 1 is not a tier: it is a dormant contract
change with zero behavioural effect. Critically, **the measurement that can stop the roadmap is
entirely in 0a** — if fewer than 100 clusters can reach `MIN_COMPARABLE`, nothing further is built,
and no generation has been paid for. The split therefore preserves the gate's purpose exactly while
resolving an ordering dependency the design did not state.

**Effect on the design:** none, beyond sequencing. §13's Phase 0 row is read as 0a; §13's Phase 1
row gains 0b's measurements as an additional exit condition before Phase 2 enablement.

---

## Phase 0a — the readiness probe

### What was built

| file | role |
|---|---|
| `examples/coverage_insights.py` | **the comparable set** (design §4), model-free stage. `candidate`, `syndication_groups`, `support_units`, `comparable_stage1`, and the four config knobs. |
| `examples/audit_coverage_readiness.py` | the read-only probe. Builds the served story view, resolves the catalog through the **canonical** join, and reports §11 items 1, 2, 3, 8. |
| `tests/test_coverage_insights.py` | 23 tests, weighted to the honesty properties (below). |

### Why the rule lives in a module and not in the probe

The probe and production must apply the *same* membership test. The repo has been here before —
`publisher_identity` was extracted for exactly this reason, because "an audit that measured a
different rule than production applies would be measuring nothing". Every number the probe prints
comes from the functions the feature itself will call.

`coverage_insights.py` is a **new module** rather than an extension of `coverage_comparison.py`:
L0 is deployed and serving, and its module should not change while a new tier is under
construction. The design's "no second comparison engine" rule is preserved by reusing
`coverage_comparison`'s own `_identity_map` / `_pub_key` — deliberately the same collapser, not a
copy of it — and, when the tiers land, its `_finding` shape and evidence format.

### The rules implemented, and what each is defending against

| rule | implementation | the failure it prevents |
|---|---|---|
| **Time window** (design §4 cond. 4) | `comparable_stage1` drops members published after `target + grace` (default 6 h) | An article published in hour 1 cannot mention what happened in hour 30. Without this, an early report is measured against later developments and reported as lacking them — a false statement caused entirely by time. This is the rule revision 1 was missing. |
| **Input parity** (cond. 3) | median `inputChars` over the time-filtered set, then a 0.6 floor | A stub member must not make the comparison set look complete. Parity is *relative*, so a uniformly terse cluster still compares — the rule is about asymmetry, not about length. |
| **Syndication collapse** | union-find over `title_tokens` Jaccard ≥ 0.9 on **title + description** | Six outlets running one AP story are six publisher identities and one act of journalism. `publisher_identity` collapses many *names of one outlet*, never *one story across many outlets* — a hole both earlier designs claimed was closed. |
| **Support units** | syndication collapse, then `publisher_identity` | Counting either collapse alone overstates corroboration. |

### Deviation D2 — two texts per candidate, not one

**Approved design:** §4 specifies input parity on `inputChars` and syndication on "title +
description token overlap".

**What was built:** `candidate()` takes *both* texts explicitly — `gen_text` (what
`article_insights.article_text` would feed the model: title + description + body, capped) for
`inputChars`, and `dedup_text` (title + description only) for the syndication tokens.

**Justification.** A first cut used one text for both and the dry run exposed the error: including
a body inflates the token set, and since only 23.7% of the catalog carries a body, some pairs would
be compared on their full text and others on a blurb. An inconsistent duplicate test is worse than
a consistently weaker one. This is not a change to the rule — it is the rule as written, with the
two inputs it actually needs made explicit so a caller cannot conflate them.

### Tests

23 tests (`tests/test_coverage_insights.py`), weighted to the properties that keep counts honest:
later coverage is never comparable; the grace window absorbs feed jitter but is configurable to
zero; unparseable timestamps are not evidence (consistent with L0's timing block); a target cannot
compare with itself even when the caller rebuilt the dict; stubs cannot pad a set but a uniformly
terse set still compares; wire copy collapses to one unit while distinct reporting does not;
grouping is order-independent (DSU, lower-index roots); and stage 1 is a pure function.

One test failed on first run and the **fixture** was wrong, not the module: `title_tokens` treats
"tonight" as a stop word, so the two strings I chose as "similar but not identical" tokenized
identically. Replaced with a pair whose measured Jaccard is 0.71.

### Gates at this commit

- `tests/test_coverage_insights.py` — 23 passed.
- Full engine suite — **2,696 passed**, 1 skipped. `tests/test_plot_axis.py` cannot collect in this
  container (`matplotlib` not installed); pre-existing and unrelated — the file was last touched by
  `3aa0ca7` and nothing in this change imports it.
- Dry-run against a synthetic 120-story view exercises every print path before the probe touches
  production.

### Production verification — required before Phase 1

```bash
cd /opt/ih && sudo bash deploy/ops/update.sh <sha>
sudo docker exec -i deploy-api-1 python examples/audit_coverage_readiness.py --show 10
```

**The gate (design §13, phase 0): clusters reaching `MIN_COMPARABLE` ≥ 100.** The probe prints
`PASS`/`FAIL` itself. If it fails, the insight-derived roadmap stops here, before any generation is
paid for — which is the entire reason this phase exists.

Read alongside the gate:

- **Item 2** decides whether `RWE_INSIGHTS_MIN_CHARS` should move off 200. The measured median
  description is 154 characters, so eligibility is sharply threshold-sensitive.
- **Item 3** produces the `RWE_INSIGHTS_BATCH` figure Phase 1 must implement, replacing the
  default of 6 that the review showed cannot keep up.
- **Item 8** is a calibration check on the 0.9 syndication threshold. A very high fold rate means
  the threshold is too low for real coverage sets, not that the catalog is all wire copy — that is a
  finding about the constant, and it is cheaper to learn now than after the tiers count on it.

*(Results are recorded here once the run comes back.)*

---

## Phase 1 — the contract extension and the worker (dormant)

Built before the Phase 0a gate result is in, deliberately and within D1's reasoning: Phase 1 is not
a tier. It changes no behaviour while `RWE_INSIGHTS_ENABLED` is unset, and **Phase 2 (enablement)
remains blocked on both the 0a gate and the 0b measurements**. The design's §3.1 argument also
applies with force: `article_insights` holds **zero rows**, so a contract change invalidates
nothing today and would invalidate the whole catalog after enablement. This was the free moment.

### What was built

| file | change |
|---|---|
| `examples/insights_provider.py` | the port gains optional `temperature`; both adapters pass it only when set |
| `examples/article_insights.py` | the `facets` contract: vocabularies, prompt, validator, span verification, `recipe_hash`, `TruncatedOutput`, `MAX_TOKENS`, `concurrency()`, `scope()`, a bounded worker pool |
| `examples/store.py` | `article_insights` gains `facets` / `input_chars` / `recipe_hash`; `finish_insights` stores them; `get_insights` returns them; `enqueue_insights` orders **cluster-first** |
| `examples/api_fastapi.py` | the wire projection — reader content only |
| `deploy/docker-compose.yml` | `RWE_INSIGHTS_CONCURRENCY` / `_SCOPE` / `_TEMPERATURE`, dormant defaults |
| `tests/test_article_insights.py` | +25 tests; `tests/test_insights_ollama.py` updated for the port |

### The invariant, enforced in code

`generate()` builds the prompt from **one article's** text and nothing else, and the prompt now
says so explicitly — *"a structured record of THIS article only. Never compare it to other
coverage — you have not been shown any."* A test asserts that sentence is present, because the
facets schema *looks* like a comparison schema and a later prompt edit could quietly invite the
model to compare. Nothing in this phase reads two articles.

### Span verification is the load-bearing rule

Every facet item carries an `evidence` span, and an item whose span is not verbatim in the article
text is **dropped** — the item, not the record. Whitespace-normalised, case-insensitive
containment; nothing cleverer, deliberately, because the value of this gate is that it cannot be
argued with. An invented aspect becomes a false statement about a named publisher the moment it is
counted.

Two consequences worth stating:

- `parse_and_validate(raw)` **without** the article text drops every span-bearing item rather than
  trusting it. A caller that forgets the text gets an empty facets object, never an unchecked one.
- The one open field that survives (`quantities.subject`) is validated for shape and passed through
  the no-label rule, which now covers `voices.name` too.

### Deviation D3 — `insights` on the wire is projected, not passed through

**Approved design:** §3.2 defines the stored artifact; it does not say what `/api/analyze` serves.

**What was built:** the endpoint serves `summary`, `bias`, `model`, `generatedAt` only. `facets`,
`inputChars` and `recipeHash` stay server-side.

**Justification.** `AnalysisModel.insights` is a free `dict`, so the new fields would have reached
the wire automatically. No client reads them — the tiers consume facets on the story-build seam and
emit findings into `coverageComparison` — so shipping them would add roughly a kilobyte of
internals to every analyze response with a cached insight, and would publish `recipeHash`, an
operational detail, to anyone with a browser. The store still returns the full record to
server-side consumers; only the boundary projects.

### Deviation D4 — `PROMPT_VERSION` starts at 2

The constant is introduced at 2 rather than 1 because the prompt this phase ships is the *second*
prompt the product has had. Records generated by the original prompt (none exist in production, but
they can exist in a developer's database) are not comparable to these, and a version starting at 1
would claim they were.

### The throughput fix

The review's blocking finding was arithmetic: 144 cycles/day × batch 6 = **864/day** against
~1,250 clustered articles/day arriving. Three changes, in the order that matters:

1. **Scope** — `RWE_INSIGHTS_SCOPE=clustered` restricts the queue to articles that are in a story,
   which is where a card can result.
2. **Cluster-first ordering** — `enqueue_insights` now sorts by `(has deficit, deficit, -size)`.
   Clusters closest to crossing `MIN_COMPARABLE` come first, then the largest. A test isolates the
   rule that size alone would get backwards: a 4-member cluster two artifacts short beats a
   6-member cluster that has none, because only the first turns the next two generations into a
   comparison.
3. **Bounded concurrency** — `RWE_INSIGHTS_CONCURRENCY` (default **1**, exactly today's serial
   behaviour) runs generations in a pool inside one cycle. The single-flight lock stays: it guards
   overlapping *cycles*, which is a different concern from parallelism within one. This is the same
   shape push delivery (B2c) already runs on this seam.

The join that makes ordering possible is canonical: `story_member.url` is the coverage member's
**publisher** url while insights are keyed **canonical**, so `_story_of` canonicalizes rather than
joining raw columns — the defect that invalidated the first coverage audit, and the one join that
has to be right for cluster-first to order anything at all.

### Truncation

`max_tokens` rose from 700 to 1,000, and truncation is now its own failure type. The old code could
not tell "the response stopped mid-JSON" from "the model ignored the contract", and since three
failures mark an article terminally `failed`, misfiling a budget problem as a contract violation
would have permanently destroyed coverage on the richest articles. `TruncatedOutput` subclasses
`ValueError`, so every existing caller still catches it, and `insights_truncated_total` makes the
budget visible.

The first heuristic checked only the tail and classified *any* non-JSON prose as truncation — an
existing test caught it. Truncation now requires that the answer **started** as JSON and did not
finish.

### Tests and gates

- `tests/test_article_insights.py` — 48 passed (25 new: facets round-trip, span verification and
  its whitespace tolerance, item-level dropping, closed-vocabulary rejection, caps, the no-label
  extension, truncation vs malformed JSON, concurrency, scope, recipe stamping, cluster-first
  ordering, the deficit rule, the wire projection).
- Full engine suite — **2,725 passed**, 1 skipped (`test_plot_axis.py` still cannot collect here:
  `matplotlib` absent, pre-existing).
- `npx tsc --noEmit` — clean.
- Two of my own tests were wrong before they were right: a helper used `str.rstrip` with a
  character set (turning "small" into "s"), and the deficit test passed only because the rows it
  checked were no longer *pending*. Both rewritten to test the rule rather than a side effect.

### Production verification — required before Phase 2

```bash
cd /opt/ih && sudo bash deploy/ops/update.sh <sha>
# 1. still dormant: no rows, no behaviour change
sudo docker exec -i deploy-api-1 python - <<'PY'
import sys
sys.path.insert(0, "/app/examples")
from sqlalchemy import func, select
import article_insights as ai, insights_provider as ip, store as store_mod
st = store_mod.Store()
print("enabled            :", ai.enabled())
print("provider           :", ip.from_env())
print("batch/concurrency  :", ai.batch_size(), "/", ai.concurrency())
print("scope/temperature  :", ai.scope(), "/", ai.temperature())
print("max_tokens         :", ai.MAX_TOKENS)
print("recipe (dormant)   :", ai.recipe_hash(None))
with st._Session() as s:
    print("article_insights rows:", s.execute(
        select(func.count()).select_from(store_mod.ArticleInsight)).scalar())
PY
# 2. the analyze contract is unchanged while dormant
sudo docker exec -i deploy-api-1 python -c "
import sys; sys.path.insert(0,'/app/examples')
import article_analyzer, store; st=store.Store()
r=article_analyzer.analyze(st,'https://www.bbc.com/news')
print('insights:', r['insights'], '| coverageComparison key:', 'coverageComparison' in r)"
```

Expected: `enabled False`, `provider None`, `concurrency 1`, `scope all`, **0 rows**, and
`insights: None` — the feature inert and the request path byte-identical. The columns exist and are
empty, which is the point of landing the contract before enablement.

*(Results are recorded here once the run comes back.)*
