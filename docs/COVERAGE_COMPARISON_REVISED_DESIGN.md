# Coverage Comparison — revised roadmap (insight-derived tiers)

**Status:** design only. No code in this change; nothing is implemented.
**Supersedes:** `docs/COVERAGE_COMPARISON_DESIGN.md` §5.2–§5.4 (tiers L1/L2/L3), which are hereby
retired. §1–§4, §6–§7 and §9 of that document remain in force and are extended here.
**Why:** `docs/COVERAGE_COMPARISON_VALUE_EVALUATION.md` measured the precondition the original
design set for itself and it failed — 23.7% of clustered articles carry a `body`, its median length
is 222 characters, and exactly **1 cluster out of 800** has three members with real article text.
**Builds on:** `docs/ARTICLE_INSIGHTS.md` (the per-article generator), `docs/INSIGHTS_TIERING_DESIGN.md`
(variants and `recipe_hash`), `docs/SIGNAL_INTEGRITY.md` (the honesty rules).
**Date:** 2026-08-03.

---

## 0. The revision in one paragraph

The original roadmap tried to compare **raw text** that the catalog does not have. This revision
compares **structured facets extracted from that same text, one article at a time, by the AI
Insights worker that already exists**. The division of labour is absolute and is the design's whole
point: *the model reads one article and fills in a schema; every comparison across articles is set
arithmetic over stored records, performed by code that has never seen a model.* The model is an
extractor, never a comparator, never an adjudicator, and never in the request path.

## 1. What the insight layer does and does not fix

An honest revision has to start by refusing a tempting claim.

**It does not create text.** `article_insights.article_text()` reads exactly the same
title + description + body the failed tiers read, capped at 6,000 characters. A model given a
222-character blurb cannot tell you what the full article omitted. **Nothing in this design makes
the catalog's text scarcity go away**, and any tier that would need full text is still unbuildable.

**What it does fix is the three problems that actually killed L1–L3:**

| Problem the old tiers hit | Why insights solve it |
|---|---|
| **Paraphrase blindness** — "40 killed" vs "the death toll reached 40" are one fact and set algebra saw two | Extraction into a controlled schema collapses them at write time. The original design (§8) already named this as the one place a model "would genuinely add power" and specified the shape: narrow, checkable, per-article. This is that. |
| **House style and boilerplate** swamping lexical deltas | Extraction discards furniture by construction; no per-publisher boilerplate subtraction step is needed. |
| **Wrong question for short text** — "what facts are missing" needs bodies | Short text carries *framing* better than anything else: emphasis, angle, and whose voice leads all live in the headline and lede. The new tiers ask what short text can actually answer. |

So the product question moves, deliberately, from **"what facts does this article omit?"** (needs
bodies; not viable) to **"how does this article frame and source the event relative to the rest of
the coverage?"** (answerable from a lede, and arguably more useful to a reader).

## 2. The invariant

> **The AI sees one article. Code does all comparison.**

Concretely, and non-negotiably:

1. **No prompt ever contains two articles.** No "compare these", no "what did A miss that B has",
   no cluster context in the input. A model asked to compare produces plausible, unverifiable,
   non-reproducible prose — the exact failure this product refuses everywhere else.
2. **No model output is ever shown as a cross-article claim.** Every sentence on the card is a
   template filled with counts computed by code.
3. **No model call in the request path.** Unchanged from today: cache read or nothing.
4. **The matching rule lives outside the model.** The model never says "these two are the same
   thing"; a versioned, deterministic normalizer does (§4.2).
5. **Determinism, correctly stated.** Extraction is not reproducible, but it happens **once per
   article and is cached forever**, so the comparison is a pure function of *stored records*: same
   rows in, byte-identical card out. That is the property tests and fixtures need, and it holds.

## 3. Extending the insight contract

### 3.1 Timing — this change is free today and expensive later

Article Insights is **dormant in production** (`RWE_INSIGHTS_ENABLED` unset; no key, no spend
sign-off), so `article_insights` holds **zero rows**. A prompt change bumps `prompt_version`, which
bumps `recipe_hash`, which invalidates every cached artifact. Right now that invalidates nothing.
**Extend the contract before enabling generation, or pay to regenerate the catalog later.**

### 3.2 The added object

One call, one cost, one cache row. The existing keys are preserved **byte-for-byte** — the UI
renders them today and must not branch:

```jsonc
{
  "summary": "…",                         // unchanged, 2–4 sentences
  "bias": { "framing": "…", "tone": "…",  // unchanged prose, unchanged no-label rule
            "loadedLanguage": [ "…" ], "omissions": "…", "viewpoint": "…" },

  "facets": {                             // NEW — the comparison surface
    "vocabVersion": 1,
    "frames":     [ { "key": "<enum>",  "evidence": "<verbatim span>" } ],          // ≤ 2
    "depth":      "episodic" | "thematic" | null,
    "aspects":    [ { "key": "<short noun phrase>", "evidence": "<verbatim span>" } ], // ≤ 8
    "voices":     [ { "role": "<enum>", "name": "<str|null>",
                      "evidence": "<verbatim span>" } ],                            // ≤ 6
    "centeredVoice": "<enum|null>",
    "quantities": [ { "kind": "<enum>", "value": <number>, "unit": "<str|null>",
                      "subject": "<short noun phrase>", "evidence": "<verbatim span>" } ], // ≤ 6
    "omissionAspects": [ { "key": "<short noun phrase>" } ]                         // ≤ 4
  }
}
```

`inputChars`, `recipeHash` and `vocabVersion` are stamped by **us** at write time, never by the
model.

### 3.3 Closed vocabularies — chosen, not invented

Comparability comes from keys that collide when they should. Where a genuine closed set exists,
use an externally grounded one rather than inventing a taxonomy:

- **`frames`** — the five generic news frames (Semetko & Valkenburg, 2000): `conflict`,
  `human_interest`, `economic_consequences`, `morality`, `responsibility`. Plus `null`. These are
  descriptive of *construction*, never of political valence — the no-label rule (§3.5) extends to
  this enum, and no value may encode a side.
- **`depth`** — `episodic` (a single incident) vs `thematic` (the incident in context) —
  Iyengar (1991). Two values, unambiguous, and genuinely visible in a lede.
- **`voices.role`** — `official_government`, `law_enforcement`, `corporate`, `expert_academic`,
  `worker_union`, `affected_person`, `witness`, `advocacy_ngo`, `political_opposition`,
  `anonymous_source`, `other`.
- **`quantities.kind`** — `casualties`, `money`, `percentage`, `people_count`, `duration`, `date`,
  `distance`, `vote_count`, `other`.

`aspects`, `omissionAspects`, `quantities.subject` and `voices.name` stay **open** — no fixed
taxonomy can cover world news — and are collided by the deterministic normalizer in §4.2.

### 3.4 The span-verification rule

**Every `evidence` value must appear verbatim in the input text.** The validator normalizes
whitespace and checks substring containment; an item whose span is not found is **dropped**
(the item, not the record — dropping the record would be brittle) and counted in
`insights_span_unverified_total`.

This is a cheap, deterministic anti-hallucination gate over the exact failure mode that matters:
an invented aspect becomes a false "reported elsewhere, not here" claim about a real publisher.
It also extends a discipline the contract already has — `loadedLanguage` is required to be quoted
from the text — to everything that can be quoted.

`omissionAspects` is the sole exception: absence has no span. That is precisely why it is the only
facet that may not be used on its own, and why it is gated behind corroboration (§5.5).

### 3.5 What the extension must not do

- No political labelling anywhere in the facets. `_LABEL_RX` currently guards the bias prose; it
  must be extended to cover `aspects`/`omissionAspects` keys and every enum value.
- No cross-article language ("unlike other coverage…") — the prompt has no other coverage.
- No confidence scores from the model. Confidence is computed from counts (§6).

### 3.6 One small port extension

`AIInsightsProvider.complete()` takes `(system, user, model, max_tokens)` — there is **no
temperature parameter**, so both adapters use the vendor default. `docs/ARTICLE_INSIGHTS.md` says
"temperature 0.2"; that is documentation of an intent the port cannot express. Extraction wants
temperature 0. The port should gain an optional `temperature` with a default that preserves today's
behaviour for existing callers — additive, vendor-neutral, one line per adapter.

## 4. The comparison machinery

### 4.1 Shape — unchanged from L0, which is the point

The existing `coverage_comparison._finding(kind, key, support, total, members, …)` already produces
evidence-carrying, publisher-identity-counted items. The new tiers are **new `kind` values feeding
the same function**. No second comparison engine, no second evidence format, no second UI contract.

```
story build seam (off the request path, as today)
    │
    ├─ gather members → join article_insights on canonical_url  (ok rows only)
    ├─ partition members by recipe_hash                     (§4.3 — the parity gate)
    ├─ normalize open keys                                  (§4.2)
    ├─ build the cluster FACET INDEX: key → [members]
    ├─ per member: set-difference + counting → findings
    └─ cache (story_id, article_id, algo_version, vocab_version, member_state_hash)
```

`member_state_hash` covers each member's `(article_id, recipe_hash, generated_at)`, so a
regenerated insight invalidates exactly the cards it can change — the same staleness discipline
`article_insights` already uses for `content_hash`.

### 4.2 The normalizer — where collisions are decided

Deterministic, versioned as `VOCAB_VERSION`, and built from the primitives the repo already has —
**do not write a second tokenizer** (original design §2):

1. lowercase, strip punctuation and possessives;
2. `clustering.title_tokens` for stopword removal and length filtering;
3. sort tokens, join → the canonical key.

Two open keys match if their canonical keys are identical, **or** if
`clustering.weighted_jaccard` over cluster-local `idf_weights` ≥ `0.8` (configurable). Cluster-local
IDF is what stops the event's own name ("avalanche", "Fed") from making every key look alike.

Quantities normalize magnitude words (`1.2 million` → `1200000`), currency symbols → ISO, and
percentages; they match on `(kind, canonical(subject))`.

### 4.3 Extraction parity — the successor to text parity

The original design's most important rule was: never say "omitted" when this article is a stub and
the comparison set has bodies. Its successor is stronger, because the tiering design ships
**variants** (`standard` = ollama/llama3.1, `premium` = anthropic/claude-opus-4-8):

**Recipe parity.** A finding may only count support from members sharing the target's
`recipe_hash`. A cluster where three members were extracted by llama and four by Opus is not a
coverage comparison — it is a model comparison wearing a coverage comparison's clothes. Members are
partitioned by `recipe_hash`; the comparison runs inside the target's partition; the partition size
is the denominator the reader sees. If no partition reaches `MIN_COMPARABLE`, the card refuses.

> **Corollary for the tiering design:** Coverage Comparison should be computed on **one designated
> variant** (`standard`) regardless of the reader's tier. Premium buys a better *prose* analysis of
> the article you are reading; it must not buy a different comparison, because a premium-only
> partition would usually be too small to compare and the numbers would silently differ by tier.

**Input parity, applied asymmetrically.** Carry `inputChars` per insight. Then:

> **Presence is evidence. Absence is evidence only when the text was long enough for absence to
> mean something.**

A member with 180 characters of input may *contribute* an aspect ("this outlet covered X") but may
never *withhold* one ("this outlet did not cover X"). Below the parity floor
(`inputChars ÷ median(partition) < 0.6`), a target article is never told it lacks anything; the
card switches to the honest low-parity copy of §7C.

## 5. The tiers

Ordered by (value × viability) ÷ precision risk. **Each ships independently and is separately
revertible.** L0 (counted metadata facts) remains tier zero and is unchanged.

### 5.1 I1 — Framing comparison *(ships first)*

Closed enums only, so there is no normalization risk at all.

- **Frame deltas** — "7 of 9 outlets frame this as economic consequences; this article frames it as
  conflict." Support = distinct publisher identities carrying each frame key.
- **Depth** — "This is the only piece placing the incident in context; the rest report the
  incident alone." (`thematic` among `episodic`.)
- **Loaded-language density** — `len(loadedLanguage)` per member, compared as a distribution.
  Reported as a count with the phrases shown, never as a "bias score".

**Why first:** closed vocabulary, no false-omission surface (a frame claim is about emphasis, not
about facts), works on a headline, and it is the single most reader-legible thing on the card.

### 5.2 I2 — Aspect coverage

Open keys through the §4.2 normalizer.

- **Only here** — aspects present in this article and no other member. *Emitted first, deliberately*:
  the original card was read as a deficit report, and the inversion is where reader goodwill lives.
- **Widely covered, not here** — an aspect carried by ≥ `K` publisher identities (default 3) and
  absent from this member. **Subject to input parity** (§4.3): below the floor this finding is
  suppressed entirely, not softened.

### 5.3 I3 — Quantity discrepancies

The original L2 assumed figures need bodies. They do not: news ledes are dense with numbers
("13 dead", "$105M", "top 3,200", "30-year yield"). Extraction from a lede is viable.

- **Discrepancy** — the same `(kind, subject)` with different values across outlets, both shown
  with their sources and spans. The product **never adjudicates** which is right; presenting the
  disagreement *is* the finding, and it is the most valuable output in the whole feature.
- **Figure absent here** — same rules as I2, same parity gate.

Tolerance: exact for counts, 5% relative for money and percentages, configurable. Below tolerance
is agreement, not a finding.

### 5.4 I4 — Voice comparison

Closed role enum. "Six outlets carry an affected person's account; this article carries only
official statements." Plus `centeredVoice` deltas.

This is the honest successor to the retired L3 (quoted voices), and it is a *better* fit for short
text than the original was for long text: sourcing posture is signalled in the lede, which is
exactly the part of the article the catalog has.

It also finally answers the question `missingViewpoints` was meant to and never did (it has been
structurally dead since launch — evaluation §5): "whose viewpoint is absent" as a sourcing fact
about the coverage, separate from and complementary to the registry's lean-bucket gaps.

### 5.5 I5 — Corroborated omissions *(ships last, hardest gate)*

`omissionAspects` is the only unverifiable facet, so it is the only one that may not stand alone.
The rule that makes it safe:

> An omission claim about article A is emitted **only if** at least `K` other members' **positive**
> aspects contain the same normalized key.

The model's per-article observation supplies the *candidate*; other articles' positive, span-verified
coverage supplies the *corroboration*. Uncorroborated omission claims are **discarded**, not shown
with lower confidence — nobody is told an article omitted something no one else reported.

Copy is bounded correspondingly: *"Five of nine outlets address the compensation objection; the
summary we have for this article does not."* Never *"this article omits…"*.

## 6. Confidence, restated for insight-derived findings

| component | definition |
|---|---|
| **support** | distinct **publisher identities** carrying the facet, **within the recipe partition** |
| **comparable** | size of the recipe partition — the denominator the reader is shown |
| **input parity** | target `inputChars` ÷ median partition `inputChars` |
| **span-verified** | whether the evidence span was found in the source text (always true for shown items; `omissionAspects` carry `false` and rely on corroboration) |
| **corroboration** | for I5 only: members whose positive aspects contain the key |
| **salience** | cluster-local IDF of the key — filters the event's own name |
| **cluster trust** | existing `clusterTrust` / `geoCoherence` |

Ordinal roll-up unchanged in spirit: **high** = support ≥ 3 identities, parity ≥ 0.7, trust `ok`,
recipe partition ≥ 3; **medium**; **low** collapses behind a disclosure.

## 7. Gating — the new refusals

L0's existing gates (`disabled`, `no_coverage`, `cluster_untrusted`, `too_few_publishers`,
`template_genre`, `cross_language`) apply first and unchanged. Insight tiers add:

| reason | when |
|---|---|
| `no_insights` | the target article has no `ok` insight row |
| `insufficient_insight_coverage` | fewer than `MIN_COMPARABLE` (default 3) members with `ok` insights |
| `mixed_recipes` | no single `recipe_hash` partition reaches `MIN_COMPARABLE` |
| `vocab_mismatch` | the partition spans `vocabVersion` values |
| `low_input_parity` | target below the parity floor — I1 still renders; I2/I3/I5 do not |

Every refusal is machine-readable and renders nothing, exactly as today.

### 7C. The low-parity card (the honest state)

```
┌──────────────────────────────────────────────────────────────────────┐
│  Coverage comparison                        9 outlets · 12 articles  │
│  We only have this article's headline and summary, so we can't say   │
│  what its full text covers. Here is how the rest of the coverage     │
│  frames the same event:                                              │
│    Economic consequences  6 of 9      Conflict  2 of 9               │
└──────────────────────────────────────────────────────────────────────┘
```

## 8. Why summaries themselves are not compared

The brief asks how generated summaries can be compared. The answer is that they should not be,
and the reason is worth recording because the opposite is tempting.

**The case for:** every summary is written in one neutral register, so lexical differences between
two summaries reflect content rather than house style — it solves the boilerplate problem the old
L1 needed a whole subtraction step for.

**Why it still loses:** a summary is a rewriting of the same ~220 characters. It carries no
information the source text lacked, so a lexical delta between two summaries is largely the
model's arbitrary word choice — fixed by caching, but arbitrary. Arbitrary-but-fixed phrasing noise
is *precisely* the generator of false "only this article mentions X" findings, and a false
uniqueness claim is as damaging as a false omission.

The summary's roles are therefore: **display** (the reader reads it) and **span source** for
evidence. The comparison surface is the facets — the model's own extraction into keys we control.

## 9. Cost and coverage — the arithmetic that decides scope

### 9.1 Per-article cost

~400 input tokens (system prompt + a median 220-character article) and ~500 output tokens with
facets:

| variant | $/article | 7,502 clustered articles (one-off) | steady state (~1,250/day) |
|---|---:|---:|---:|
| `claude-opus-4-8` ($5/$25 per 1M) | ~$0.0145 | ~$109 | ~$544/mo |
| `claude-haiku-4-5` ($1/$5 per 1M) | ~$0.0029 | ~$22 | ~$109/mo |
| `ollama` local | $0 | $0 (latency-bound) | $0 |

Backfilling the entire clustered catalog is a **one-off in the low hundreds of dollars**, which
reframes the question from "can we afford this" to "which variant". Extraction into a closed schema
is a task small models do well; that should be **measured** (the benchmark harness at
`examples/benchmark_insights.py` already compares providers on a golden set and takes new targets
as config) rather than assumed.

### 9.2 The highest-leverage change: enqueue cluster-first

`store.enqueue_insights` currently scans catalog articles by eligibility, article by article. For
this feature that is close to worst-case:

> Six generations sprinkled across six clusters produce **zero** comparisons.
> Six generations that complete one 6-member cluster produce **six** cards.

A comparison needs `MIN_COMPARABLE` members of the *same* cluster. The enqueue ordering should
therefore prioritise **members of clusters that are closest to crossing the threshold**, and among
those, the clusters with the most publisher identities. This is a change to an `ORDER BY`, not an
architecture, and it is the difference between the feature appearing in weeks and appearing in
months.

### 9.3 The eligibility floor collides with the catalog

`RWE_INSIGHTS_MIN_CHARS` is 200. The measured median description is **154 characters**; with a
~60-character headline the typical article sits *just above* the floor. Small changes to that
threshold will therefore swing eligibility sharply in both directions. **Phase 0 must measure the
distribution, not assume it.**

## 10. Phase 0 — the measurement that gates everything

The original design specified a readiness probe, it was deferred, and the roadmap it was meant to
protect was built and then had to be retired. **This time Phase 0 is a hard gate: no tier is built
before these numbers exist.** A read-only probe (in the style of `examples/audit_coverage_comparison.py`)
must report, for the 800 clusters that pass L0's gates:

1. **Insight-eligible members per cluster** — the distribution of members with
   `len(title+description) ≥ min_chars`, and how many clusters reach ≥3.
2. **The addressable set** — clusters that would reach `MIN_COMPARABLE` at full coverage. *(This
   number is the direct analogue of the "1 cluster out of 800" that killed L1–L3. If it is small,
   this roadmap dies here too, and that is a success of the process.)*
3. **Sensitivity** of (1) to `min_chars` at 150 / 200 / 250.
4. **Cost to saturate** the top N clusters by publisher count, under both enqueue orderings.
5. **Facet yield on real articles** — run the extended prompt over a production sample via the
   benchmark harness (`--sample-production`): facets per article, span-verification pass rate, and
   normalized-key collision rate across members of the same cluster. **The collision rate is the
   single most important number in this design** — if members of one cluster do not produce
   colliding keys for the same aspect, set algebra over those keys is noise.

## 11. Prerequisites outside this design

These are not optional, and none of them is this feature's own work:

1. **Fix L0 first** (evaluation §10). Layering new tiers onto a card that is currently 46%
   reader-facing noise, whose viewpoint row is dead, and whose "only \<lean\> outlet" claim is
   unsupported 86% of the time, compounds a precision problem rather than fixing one.
2. **The shape-contract test.** Every defect this feature has had — the register crash, and the
   three dead paths — was a module reading fields its producer does not emit. These tiers read
   *across one more boundary* (story members → `article_insights` rows). A test that binds the
   consumer's expected keys to the producer's actual output is a precondition, not a nicety.
3. **Story fragmentation.** Every tier here counts "N of M outlets". The evaluation found the FIFA
   row split across three clusters, so the denominator is wrong before any tier runs. Comparison
   quality is capped by clustering quality.
4. **Insights enablement** — a key and spend sign-off. The feature is dormant; none of this exists
   until generation runs.

## 12. Build order and pre-registered gates

Bars are registered **before** each phase's data is collected, and are the ones the evaluation
established, because those are the ones the last roadmap failed:

| phase | scope | gate to proceed |
|---|---|---|
| **0** | the §10 probe | numbers exist; addressable set ≥ 100 clusters and key-collision rate ≥ 0.6, else stop |
| **1** | contract extension + span validation + temperature (dormant; no behaviour change) | contract tests green; goldens regenerated; zero rows invalidated (still dormant) |
| **2** | cluster-first enqueue + enablement on one variant | coverage of the top 100 clusters ≥ 80% within a week of cycles |
| **3** | **I1 framing** | ≥50% of rendered cards carry a finding beyond the outlet count; ≤10% reader-facing noise; manual read of 20 clusters |
| **4** | **I2 aspects** | manual precision read: ≤2% false "not here" on a hand-read sample (original §12.1's bar) |
| **5** | **I3 quantities** | same bar; discrepancies hand-checked against sources |
| **6** | **I4 voices** | same bar |
| **7** | **I5 corroborated omissions** | same bar, plus an explicit review of every uncorroborated-discard case |
| **8** | story-page matrix (original §10E) | only after 3–7 have been read in anger |

Every phase after 1 is gated on a **manual precision read**, not on an aggregate — the failure mode
is a plausible false accusation about a named publisher, and no aggregate catches those.

## 13. Limitations, stated plainly

1. **This does not add text.** Every limitation of short input survives; the design changes what is
   extracted, not how much there is to extract from.
2. **Extraction is not reproducible**, only cached. A regenerated insight can change a card. The
   `member_state_hash` makes that visible; it does not make it not happen.
3. **The model is a lossy extractor.** A missed aspect becomes a false "only here" for someone else
   in the cluster. Multi-publisher support thresholds are the containment, not a cure.
4. **Closed vocabularies force choices.** A frame taxonomy of five values will misfit some stories.
   `null` is always allowed, and a forced-choice enum must never be inferred from a coin flip.
5. **Normalized-key collision is empirical.** §10.5 measures it; if it is low the aspect tiers do
   not ship, whatever the rest of this document says.
6. **Cross-language is still out of scope**, and the gate meant to enforce that is itself dead
   today (evaluation §5) — fixing it is prerequisite #1's work.
7. **Structural asymmetry is not fault.** A wire brief legitimately carries less than a feature;
   copy must never imply otherwise.
8. **Cost is real and recurring.** §9.1 is an estimate from token arithmetic, not a measurement;
   the harness should confirm it before enablement.
