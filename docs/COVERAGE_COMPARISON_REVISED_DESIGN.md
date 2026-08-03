# Coverage Comparison — insight-derived tiers (implementation-ready)

**Status:** design only. No code in this change; nothing is implemented.
**Supersedes:** `docs/COVERAGE_COMPARISON_DESIGN.md` §5.2–§5.4 (tiers L1/L2/L3), retired — they
compare raw article text the catalog does not have. §1–§4, §6–§7 and §9 of that document remain in
force and are extended here.
**Revision 2** — rewritten to resolve every finding in `docs/COVERAGE_COMPARISON_DESIGN_REVIEW.md`.
Revision 1 (`23d375f`) is superseded in full; §14 records what changed and why.
**Evidence base:** `docs/COVERAGE_COMPARISON_VALUE_EVALUATION.md` (why L1–L3 died),
`docs/ARTICLE_INSIGHTS.md` (the per-article generator), `docs/INSIGHTS_TIERING_DESIGN.md`
(variants and `recipe_hash`), `docs/SIGNAL_INTEGRITY.md` (the honesty rules).
**Date:** 2026-08-03.

---

## 0. The design in one page

The model reads **one article** and fills a small schema of **closed vocabularies**. Code does every
comparison, by counting, over a single explicitly-defined **comparable set**. The card renders each
finding as a **composition** — what this article contains beside what the comparable coverage
contains — and never as a claim that the article failed to do something.

Three changes from revision 1 carry most of the weight:

1. **Nothing claims an omission.** The open-vocabulary aspect tier and the corroborated-omission
   tier are deleted, not deferred. With them go the normalizer, the cluster-IDF matching, the
   collision-rate gate, and the entire false-accusation surface (§2).
2. **One comparable set** replaces three separate parity rules — recipe, format, input length and
   publication time are four conditions on one membership test, and its size is the only denominator
   the reader ever sees (§4).
3. **Composition copy, not gap copy.** *"Voices quoted here: officials (2). Elsewhere in this
   coverage: affected people (5 outlets), experts (3)."* The reader draws the inference; the product
   never states it (§6).

What remains is three tiers, one schema, one gate, one cache, and an operational plan whose
arithmetic closes.

## 1. What the insight layer fixes, and what it does not

**It does not create text.** `article_insights.article_text()` reads the same title + description
(+ body where present) the failed tiers read. A model given a 222-character blurb cannot tell you
what the full article omitted. Every limitation of short input survives this redesign.

**What it fixes is the reason the old tiers were unbuildable:** it moves the product question from
*"what facts does this article omit?"* — which needs bodies the catalog does not have — to
*"how does this article frame and source the event, and what figures does the coverage carry?"*,
which is answerable from a headline and a lede. Framing and sourcing posture are concentrated in
exactly the part of the article the catalog does have.

## 2. The invariant, and the finding class deleted to protect it

> **The AI reads one article. Code does every comparison.**

1. **No prompt ever contains two articles.** No cluster context, no "compare these", no other
   coverage in the input.
2. **No model output is ever rendered as a cross-article claim.** Every comparative sentence is a
   template filled with counts computed by code.
3. **No model call in the request path.** Unchanged: cache read, or nothing.
4. **No model judges equivalence.** Nothing in this design asks a model whether two things are the
   same.
5. **Determinism, correctly stated.** Extraction is not reproducible, but it runs **once per article
   and is cached**, so the comparison is a pure function of stored rows: same rows in, byte-identical
   card out.

### 2.1 Why the omission findings are deleted rather than deferred

To say *"five outlets covered X and this one did not"* you must know that this article's phrasing of
X and the others' phrasing are the same thing. Set algebra cannot know that. Revision 1 proposed a
token-overlap normalizer; the review showed it is effectively exact-match at any threshold that
avoids false merges — `"public cost figure"` vs `"cost to taxpayers"` scores **0.25** — so a large
fraction of genuine matches would be missed, and **every miss is a false public statement about a
named publisher**. Revision 1 also set two numbers that cannot both hold: a 0.6 collision gate and a
≤2% false-accusation bar.

There is exactly one principled route to the omission finding: the **equivalence oracle** the
original design sanctioned (§8) — a model asked only *"do these two spans state the same fact?"*,
over two short spans both shown to the reader. It is narrow, cacheable and falsifiable. **It is also
a weakening of invariant 4**, and adopting it silently while claiming the strong invariant would be
the worst available outcome.

**This design does not adopt it.** The omission finding is out of scope. If it is ever judged
essential, it returns as a deliberate, separately-argued amendment to §2 — not as a threshold tweak.

### 2.2 What else this removes

Deleting the omission class removes, in one move: `aspects`, `omissionAspects`, the normalizer, the
cluster-local IDF machinery, the `weighted_jaccard` threshold, the collision-rate Phase 0 gate, and
tiers I2 and I5 of revision 1. The remaining vocabularies are **closed**, so comparability is a
property of the schema rather than of a matching heuristic, and the residual risk moves from
*collision rate* (unmeasurable in advance, unbounded consequence) to *label reliability* (directly
measurable, §11.5).

## 3. The insight contract extension

### 3.1 Timing — free now, expensive later

`article_insights` is **dormant in production** (`RWE_INSIGHTS_ENABLED` unset): **zero rows**. A
prompt change bumps `prompt_version` → `recipe_hash` → invalidates every cached artifact. Today that
invalidates nothing. Everything in this section, plus the worker changes in §9 and the port change
in §3.5, must land **in one change before generation is enabled**.

### 3.2 The schema

One call, one cost, one cache row. Existing keys preserved **byte-for-byte** — the UI renders them
today and must not branch:

```jsonc
{
  "summary": "…",                          // unchanged: 2–4 sentences
  "bias": { "framing": "…", "tone": "…",   // unchanged prose, unchanged no-label rule
            "loadedLanguage": [ "…" ], "omissions": "…", "viewpoint": "…" },

  "facets": {                              // NEW — closed vocabularies only
    "vocabVersion": 1,
    "format":     "<enum>",                                          // §3.3
    "frames":     [ { "key": "<enum>", "evidence": "<verbatim span>" } ],   // ≤ 2
    "depth":      "episodic" | "thematic" | null,
    "voices":     [ { "role": "<enum>", "name": "<str|null>",
                      "evidence": "<verbatim span>" } ],             // ≤ 6
    "centeredVoice": "<enum|null>",
    "quantities": [ { "kind": "<enum>", "value": <number>, "unit": "<str|null>",
                      "subject": "<short noun phrase>",
                      "evidence": "<verbatim span>" } ]              // ≤ 6
  }
}
```

`inputChars`, `recipeHash` and `vocabVersion` are stamped by **us** at write time, never by the
model. `quantities.subject` is the **only** open-vocabulary field that survives, and §7 explains why
it is safe: a subject-matching error there degrades to a longer list, never to an accusation.

### 3.3 The vocabularies — chosen, not invented

- **`format`** — `news_report`, `analysis`, `review`, `live_blog`, `obituary`, `listicle`,
  `opinion`, `other`. **New in revision 2.** The evaluation found *"Spider-Man: Brand New Day
  review"* (The Guardian) clustered with *"Korea Box Office"* (Variety): comparing a review's
  framing against a box-office report's is meaningless. `format` is a precise version of the
  existing headline-regex `template_genre` gate, and it partitions rather than suppressing.
- **`frames`** — the five generic news frames (Semetko & Valkenburg, 2000): `conflict`,
  `human_interest`, `economic_consequences`, `morality`, `responsibility`, plus `null`. Descriptive
  of construction, never of political valence.
- **`depth`** — `episodic` (a single incident) vs `thematic` (the incident in context), Iyengar
  (1991). Two values, visible in a lede.
- **`voices.role`** — `official_government`, `law_enforcement`, `corporate`, `expert_academic`,
  `worker_union`, `affected_person`, `witness`, `advocacy_ngo`, `political_opposition`,
  `anonymous_source`, `other`.
- **`quantities.kind`** — `casualties`, `money`, `percentage`, `people_count`, `duration`, `date`,
  `distance`, `vote_count`, `other`.

Every enum permits `null`/`other`. **A forced choice must never be inferred from a coin flip** — the
prompt says so explicitly and §11.5 measures whether it was obeyed.

### 3.4 The span-verification rule

**Every `evidence` value must appear verbatim in the input text.** The validator normalizes
whitespace and checks substring containment; an item whose span is not found is **dropped** (the
item, not the record) and counted in `insights_span_unverified_total`.

Every facet in this schema is span-verifiable — there is no longer any exception, because the one
field that could not carry a span (`omissionAspects`) is deleted. **Nothing enters a comparison
without a quotable basis in the article's own text.**

### 3.5 Validation, token budget and the port

- **`max_tokens` must rise.** `generate()` passes `700` (`article_insights.py:166`). Adding the
  facets object plausibly pushes output past that; a truncated response is invalid JSON → a failed
  attempt → **three truncations mark the article terminally `failed`**, silently destroying coverage
  on the richest articles. Set `max_tokens` from the measured distribution (§11.7), provisionally
  **1,000**, and raise it with a margin above p99.
- **Truncation is a distinct failure.** `parse_and_validate` must distinguish "response ended
  mid-JSON" from "model returned bad JSON" and count it separately
  (`insights_truncated_total`), because the fixes differ and the current code cannot tell them apart.
- **The port gains `temperature`.** `AIInsightsProvider.complete()` takes
  `(system, user, model, max_tokens)` — there is no temperature parameter, so both adapters use the
  vendor default. `docs/ARTICLE_INSIGHTS.md` documents "temperature 0.2", an intent the port cannot
  express. Extraction wants **0**. Add an optional parameter with a default that preserves today's
  behaviour for existing callers: additive, vendor-neutral, one line per adapter.
- **The no-label rule extends to the facets.** `_LABEL_RX` currently guards the bias prose; it must
  also reject any left/right language in `voices.name` and `quantities.subject`.

## 4. The comparable set — one rule, four conditions

Revision 1 had recipe parity, input parity and (missing) temporal parity as separate bolt-ons. They
are one membership test. **Every count in every tier is over the comparable set, and its size is the
only denominator the reader ever sees.**

A member `m` is comparable to target `t` when all four hold:

| condition | rule | why |
|---|---|---|
| **Recipe** | `m.recipe_hash == t.recipe_hash` | A cluster with three llama members and four Opus members is a model comparison wearing a coverage comparison's clothes. |
| **Format** | `m.format == t.format` | A review and a box-office report are not alternative treatments of one event. |
| **Input parity** | `m.inputChars ≥ 0.6 × median(inputChars)` of the candidate set | A 180-character member may be *counted in* a composition it participates in, but it may not make the set look complete. |
| **Time** | `m.publishedAt ≤ t.publishedAt + grace` (default 6 h) | **The rule revision 1 was missing.** An article published in hour 1 cannot mention what happened in hour 30. The evaluation's own sample is full of six-day clusters — the Purja avalanche (36 outlets), the Ebola case counts, the FIFA row — where a later development would otherwise be counted against an earlier report. |

Then, **before any counting**:

**Syndication collapse.** Two members are near-duplicates when `clustering.title_tokens` Jaccard over
`title + description` ≥ 0.9. Near-duplicates collapse into **one support unit**, and the card names
the relationship rather than hiding it: *"reported by 4 outlets, 3 of them carrying the same wire
copy."* This is model-free, available today, and closes a real hole: `publisher_identity.groups()`
collapses *many names of one outlet*, **not six outlets running one AP story** — both earlier designs
claimed otherwise.

The comparison then runs over `comparable(t)` with a single gate: **`|comparable(t)| ≥
MIN_COMPARABLE`** (default 3, distinct publisher identities after collapse). One condition, one
number, one refusal reason.

## 5. The tiers

Ordered by (value × safety). **Each ships independently and is separately revertible.** L0 (counted
metadata facts) remains tier zero, unchanged, and is a prerequisite (§12).

### C1 — Framing composition *(ships first)*

Closed enums; no matching heuristic anywhere.

- **Frame composition** — the distribution of `frames` across the comparable set, beside this
  article's own.
- **Depth** — `thematic` among `episodic` or vice versa.
- **Loaded-language density** — `len(loadedLanguage)` per member as a distribution, with the phrases
  shown. Never a "bias score".

**Divergence-only emission.** A finding is emitted **only when the target differs from the
comparable majority**. *"This article frames it as economic consequences, like the other eight"* is
the L0 obviousness failure in a new costume, and §13's gate is written so it cannot pass.

### C2 — Figure distribution

The highest-value output in the design and the safest, because its failure mode is silence.

Match on `(kind, canonical(subject))`; normalize magnitudes (`1.2 million` → `1200000`), currency
symbols → ISO, percentages. Below tolerance (exact for counts, 5% relative for money and
percentages) values agree and there is no finding.

**Rendered as a distribution, never as a contradiction:**

```
Figures in this coverage
   ten missing     Explorersweb, +1 outlet     "…Ten Missing, Including Nirmal Purja…"
   13 dead         Today.Rtl.Lu, +3 outlets    "…13 dead after…"
```

The product never adjudicates which figure is right, and never asserts that two figures contradict —
it lists what the coverage carries, with spans. A subject-matching error therefore produces a
slightly longer list, not a false claim that outlets disagree.

### C3 — Voice composition

Closed role enum, rendered as composition on both sides:

```
Voices quoted here      officials (2)
Elsewhere in this coverage   affected people (5 outlets) · experts (3) · officials (7)
```

This is the honest successor to the retired L3 (quoted voices), and it finally answers the question
`missingViewpoints` was meant to and never did — it has been structurally dead since launch
(evaluation §5). Sourcing posture is signalled in the lede, which is the part of the article the
catalog actually has.

### Explicitly not shipping

| finding | why |
|---|---|
| "widely covered, not here" (aspects) | unsound without an equivalence oracle (§2.1) |
| "figure absent here" | same failure mode: accusation |
| corroborated omissions | redundant — under its own corroboration rule it is a subset of the aspect finding it depended on, contributing only the model's ungrounded opinion that a gap is conspicuous |
| open-vocabulary aspects | their only surviving use was a uniqueness claim whose soundness needs a collision rate token matching cannot deliver |

## 6. The copy rule: show the composition, not the accusation

**Every insight-derived finding renders as a side-by-side distribution over the comparable set. The
product never states that this article lacks something.**

This single rule is what allows the remaining tiers to ship. C3's underlying fact — this article
quotes only officials while five comparable outlets quote affected people — is accusation-shaped if
asserted and merely informative if displayed. The reader draws the inference from two lists; a
mislabelled voice degrades one row of a distribution instead of producing a false statement about a
publisher.

It also resolves the prose/facet contradiction the review found: **prose speaks about the single
article, the card speaks about the coverage, and the card cannot contradict the prose because it
never discusses omissions at all.**

## 7. Confidence

| component | definition |
|---|---|
| **support** | distinct publisher identities carrying the value, **after syndication collapse**, **within the comparable set** |
| **comparable** | `|comparable(t)|` — the denominator shown to the reader, beside the raw outlet count |
| **span-verified** | always true for displayed items (§3.4) |
| **divergence** | how far the target sits from the comparable majority — the emission condition itself |
| **cluster trust** | existing `clusterTrust` / `geoCoherence` |

Ordinal roll-up: **high** = support ≥ 3 identities, comparable ≥ 3, trust `ok`; **medium**; **low**
collapses behind a disclosure. There is no "text parity" component — nothing here claims absence.

## 8. Gating

L0's existing gates (`disabled`, `no_coverage`, `cluster_untrusted`, `too_few_publishers`,
`template_genre`, `cross_language`) apply first and unchanged. The insight tiers add exactly two:

| reason | when |
|---|---|
| `no_insights` | the target has no `ok` insight row for the designated recipe |
| `insufficient_comparable` | `|comparable(t)| < MIN_COMPARABLE` — covers missing insights, recipe mismatch, format mismatch, input parity and the time window in one number |

Refusals render nothing, as today. The card always displays both counts: *"9 outlets covering this
story · 6 comparable."*

## 9. Operating the generator — the arithmetic that has to close

Revision 1's Phase 2 gate was unreachable with the existing worker. This section replaces optimism
with arithmetic.

### 9.1 The ceiling today

```
RWE_POLL_INTERVAL  = 600 s      → 144 cycles/day          (feed_service.py:19)
RWE_INSIGHTS_BATCH = 6/cycle    → 864 articles/day         (article_insights.py:41)
clustered arrivals ≈ 7,502 / 6 days ≈ 1,250/day
```

**The queue diverges** — before counting eligible catalog articles that never cluster, which
`enqueue_insights` also enqueues. Three changes fix it, in this order:

### 9.2 Scope the work (the largest win, and free)

The feature does not need catalog coverage. It needs **clusters saturated**. Two orderings, same
budget:

> Six generations sprinkled across six clusters produce **zero** cards.
> Six that complete one 6-member cluster produce **six**.

`enqueue_insights` orders by article eligibility. It must order **cluster-first**: members of L0-gated
clusters, prioritised by how close the cluster is to `MIN_COMPARABLE` and then by publisher count.
`RWE_INSIGHTS_SCOPE=clustered` restricts the queue to that set — ~5,000 articles rather than the
whole catalog. Standalone `/analyze` insights for unclustered articles remain available under
`RWE_INSIGHTS_SCOPE=all`; they are simply not this feature's concern.

Deliberate target: **the top 200 clusters by publisher count** ≈ 2,000 articles ≈ 2.3 days at
today's ceiling, then maintenance. Full coverage is not a goal.

### 9.3 Size the batch from measurement, not from the default

Required steady-state batch:

```
batch ≥ ceil( arrival_rate_per_day × poll_interval_seconds / 86,400 ) × headroom(1.5)
```

Both inputs are Phase 0 measurements (§11.3, §11.4), not estimates.

### 9.4 Add bounded concurrency

`run_cycle` generates **sequentially** under the single-flight lock, so a batch large enough to keep
up would run past the cycle interval and the next request would be dropped. At ~4 s/call a hosted
batch of 15–20 fits inside 600 s; at local-model latencies (the ollama adapter's timeout is 300 s per
call for a reason) it does not.

Add a bounded worker pool inside `run_cycle` — `RWE_INSIGHTS_CONCURRENCY`, default 1 so today's
behaviour is preserved. **The repo already has this exact pattern**: push delivery (B2c) runs a
bounded pool on the same seam with the same failure isolation. Keep the single-flight lock: it
protects against overlapping *cycles*, which is a different concern.

### 9.5 Cost

Recomputed for the smaller schema; still to be confirmed with `count_tokens` on the real prompt
(§11.7) before any spend sign-off:

| variant | ≈$/article | 5,000 clustered (one-off) | steady state ≈1,250/day |
|---|---:|---:|---:|
| `claude-opus-4-8` | ~$0.029 | ~$145 | ~$1,080/mo |
| `claude-haiku-4-5` | ~$0.006 | ~$29 | ~$215/mo |
| `ollama` local | $0 | $0 (latency-bound) | $0 |

Extraction into closed enums is a task small models do well. The variant choice should be **measured**
with `examples/benchmark_insights.py` — which already compares providers on a golden set and takes
new targets as config — not assumed. Revision 1's figures were roughly half these; the system prompt
carrying the schema and enums is itself 600–900 tokens.

## 10. Storage, caching and the join

**Cache with the story, not with the article.** Revision 1 keyed the cache on a `member_state_hash`
covering every member — but news clusters gain members on every ingest cycle, so that hash changes
continuously and **every card in a cluster is invalidated whenever any member is added**. A 39-outlet
cluster would recompute 39 cards per new member, and the cache would be least effective exactly when
a story is most read.

The comparison is pure CPU over rows already loaded during story build. Compute **all members' cards
in one pass** on the story-build seam and cache them with the story view, whose lifecycle already
tracks membership — as the original design specified (§3). Key: `(story_id, algo_version,
vocab_version, recipe)`.

**The join is canonical.** `article_insights` is keyed by **canonical URL**; story coverage members
carry the **publisher** URL, and `_coverage()` emits no `id`. Joining on the raw member URL resolves
~8% of members — this is the exact defect that invalidated the first audit run (evaluation §1). The
join must use `ingest.canonical_url()`, and the shape-contract test (§12.2) must cover it.

Storage: facets add ~1 KB/article. `article_insights` is cache-forever with no retention policy;
that remains a decision to make, not urgent at this volume.

## 11. Phase 0 — the measurements that gate everything

The original design specified a readiness probe, it was deferred, and the roadmap it protected had to
be retired. **Phase 0 is a hard gate: no tier is built before these numbers exist.** A read-only probe
in the style of `examples/audit_coverage_comparison.py` must report:

1. **Comparable-set size per cluster** — for the 800 L0-gated clusters, how many would reach
   `MIN_COMPARABLE` at full coverage, after format partitioning and syndication collapse. *(This is
   the direct analogue of the "1 cluster out of 800" that killed L1–L3. If it is small, this roadmap
   dies here too, and that is the process working.)*
2. **Eligibility sensitivity** — members passing `min_chars` at 150 / 200 / 250. The measured median
   description is **154 characters**; with a ~60-character headline the typical article sits just
   above the 200 floor, so eligibility is sharply threshold-sensitive.
3. **Arrival rate** — articles/day entering the comparable scope (§9.3 input).
4. **Generation latency per variant**, measured on the box (§9.4 input).
5. **Enum reliability** — extract the same production sample **twice under one recipe** and **once
   under a second recipe**; report agreement and Cohen's κ per field (`format`, `frames`, `depth`,
   `voices.role`, `centeredVoice`). **A tier ships only if κ ≥ 0.6 for the fields it uses.** Counting
   unreliable labels produces a precise-looking number over noisy input, and C1 is the tier that
   ships first.
6. **Quantity yield and span-verification pass rate** on the same sample.
7. **Token distribution** via `count_tokens` on the real prompt → `max_tokens` (§3.5) and the cost
   model (§9.5).
8. **Syndication rate** — near-duplicate share within clusters, since it moves every denominator.

Use `examples/benchmark_insights.py --sample-production` for 5–8; it exists and already samples
production without modifying it.

## 12. Prerequisites outside this design

None of these is this feature's own work, and none is optional:

1. **Fix L0 first** (evaluation §10) — the card is currently 46% reader-facing noise, its viewpoint
   row is dead, and its "only \<lean\> outlet" claim is unsupported 86% of the time. Layering tiers on
   that compounds a precision problem.
2. **The shape-contract test** — every defect this feature has had (the register crash, three dead
   paths, the audit join) was a module reading fields its producer does not emit. These tiers read
   across one more boundary (story members → `article_insights`). A test binding consumer expectations
   to producer output is a precondition.
3. **Story fragmentation** — every tier counts "N of M". The FIFA row is split across three clusters,
   so denominators are wrong before any tier runs. Comparison quality is capped by clustering quality.
4. **Insights enablement** — a key and spend sign-off. Nothing here exists until generation runs.

## 13. Build order and pre-registered gates

| phase | scope | gate to proceed |
|---|---|---|
| **0** | the §11 probe | clusters reaching `MIN_COMPARABLE` ≥ 100; κ ≥ 0.6 for `format` and `frames`; span-verification ≥ 0.9 — else stop |
| **1** | contract + validation + `max_tokens` + `temperature` + concurrency + cluster-first enqueue (all dormant) | contract tests green; goldens regenerated; zero rows invalidated (still dormant) |
| **2** | enablement on the designated recipe, scope `clustered` | top 200 clusters ≥ 80% covered within 5 days of cycles, measured |
| **3** | **C1 framing** | ≥50% of rendered cards carry a finding that **distinguishes this article from its comparable set**; ≤10% reader-facing noise; manual read of 20 clusters |
| **4** | **C2 figures** | manual read: every listed figure traceable to its span; no pair listed together that a reader would call unrelated |
| **5** | **C3 voices** | manual read of 20 clusters; κ for `voices.role` re-confirmed on live data |
| **6** | story-page matrix (original §10E) | only after 3–5 have been read in anger |

The Phase 3 bar is deliberately reworded. "≥50% carry a finding beyond the outlet count" was
diagnostic for L0 because L0's classes were mostly dead; **every article has a frame**, so that bar
would pass at ~100% while the card said nothing. The metric must be unable to pass the failure it was
built to catch.

Every phase after 1 is gated on a **manual read**, not an aggregate — with omission claims gone the
worst case is no longer a false accusation, but a misleading composition still is not something an
aggregate catches.

## 14. Migration and operations

### 14.1 Model switchover (the highest operational risk)

Recipe parity is correct — comparing across recipes measures models, not outlets. But when
`claude-opus-4-8` is superseded the catalog splits, and **every active cluster returns
`insufficient_comparable` until old members age out of the 6-day window**: days of silence triggered
by a routine upgrade.

**Procedure, to be in place before first enablement:** the *designated comparison recipe* is config
(`RWE_COVERAGE_RECIPE`). To switch, generate the new recipe for newly-arriving articles while the old
recipe remains designated (dual-write, ~2× cost for the window's duration ≈ 6 days), then flip the
designation once the new recipe covers ≥80% of the active window. No cluster ever mixes recipes, and
the feature never goes dark.

`INSIGHTS_TIERING_DESIGN` interaction: **Coverage Comparison runs on one designated recipe regardless
of the reader's tier.** Premium buys a better *prose* analysis of the article being read; it must not
buy different numbers, and a premium-only partition would usually be too small to compare.

### 14.2 Vocabulary changes are paid regenerations

Adding one value to an enum changes the choice set the model had, so old records are not comparable to
new ones. `vocabVersion` detects this; it does not make it cheap — a bump costs a full re-extraction
(§9.5) plus days of worker time. **Validate the vocabularies in Phase 0, while the table is still
empty and regeneration is free.**

### 14.3 Config surface

`RWE_COVERAGE_INSIGHT_TIERS` (empty = off; `c1,c2,c3`) · `RWE_COVERAGE_MIN_COMPARABLE` (3) ·
`RWE_COVERAGE_INPUT_PARITY` (0.6) · `RWE_COVERAGE_TIME_GRACE_H` (6) · `RWE_COVERAGE_RECIPE` ·
`RWE_INSIGHTS_CONCURRENCY` (1) · `RWE_INSIGHTS_SCOPE` (`all`) · `RWE_INSIGHTS_TEMPERATURE` (0) ·
`RWE_INSIGHTS_BATCH` (resized per §9.3) — all through the compose allowlist, all dormant-safe.

## 15. Limitations, stated plainly

1. **This does not add text.** Every limitation of short input survives.
2. **No omission claim is made at all.** A reader wanting "what did this article leave out" will not
   get it from this feature. That is a deliberate refusal, not an oversight (§2.1).
3. **Extraction is not reproducible**, only cached. A regenerated insight changes a card; the
   story-scoped cache key makes that visible, not impossible.
4. **Closed vocabularies force choices.** Five frames will misfit some stories. `null`/`other` are
   always permitted, and §11.5 measures whether the model uses them honestly.
5. **Label reliability is empirical.** If κ is low, the tier does not ship, whatever the rest of this
   document says.
6. **Cross-language is still out of scope**, and the gate meant to enforce it is itself dead today
   (evaluation §5) — prerequisite 1's work.
7. **Structural asymmetry is not fault.** A wire brief legitimately carries less than a feature; the
   composition copy is what keeps the UI from implying otherwise.
8. **Cost is real and recurring**, and §9.5 remains an estimate until §11.7 measures it.

## 16. What changed from revision 1

| revision 1 | revision 2 | why |
|---|---|---|
| I2 aspects (open keys, normalizer, IDF matching) | **deleted** | token matching cannot support the precision bar; 0.6 collision gate and ≤2% false-accusation bar were mutually exclusive |
| I5 corroborated omissions | **deleted** | subset of the tier it depended on; contributed only an ungrounded judgement |
| "widely covered, not here" / "figure absent here" | **deleted** | accusation-shaped failure mode |
| recipe parity + input parity (+ no temporal rule) | **one comparable set**, four conditions | simpler, and it adds the missing temporal rule |
| gap-shaped copy | **composition copy** (§6) | removes the false-accusation surface from the tiers that remain |
| — | **`format` facet + partitioning** | a real production case (review × box-office) the regex gate could not catch |
| — | **syndication collapse** | `publisher_identity` does not defend against wire copy; both earlier designs claimed it did |
| per-article `member_state_hash` cache | **story-scoped cache** | membership churn invalidated whole clusters continuously |
| `max_tokens` 700 | **measured, ≈1,000** + truncation-specific failure | truncation would terminally fail the richest articles |
| ~$109 one-off / ~$544 mo | **~$145 / ~$1,080 mo** (Opus), pending `count_tokens` | prompt schema tokens were unaccounted |
| batch 6, sequential | **scoped + measured batch + bounded concurrency** | 864/day against ~1,250/day arrivals: the queue diverged |
| Phase 3 gate "a finding beyond the outlet count" | **"a finding that distinguishes this article"** | every article has a frame; the old bar could not fail |
| collision-rate Phase 0 gate | **enum reliability (κ ≥ 0.6)** | the surviving risk is label reliability, which is measurable |
| model switchover unaddressed | **dual-write procedure** (§14.1) | recipe parity made a routine upgrade a multi-day outage |
