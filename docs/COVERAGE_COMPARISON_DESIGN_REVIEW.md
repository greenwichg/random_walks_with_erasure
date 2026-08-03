# Coverage Comparison (insight-derived tiers) — pre-implementation design review

**Reviews:** `docs/COVERAGE_COMPARISON_REVISED_DESIGN.md` (commit `23d375f`).
**Status:** review only. No code in this change; nothing is implemented.
**Date:** 2026-08-03.

**Verdict: do not implement as written.** The central principle holds, and the framing and
quantity-discrepancy work is sound and worth building. But the design contains **one internal
contradiction that invalidates its flagship tier**, **one arithmetic blocker that would prevent the
feature from ever reaching enough coverage to render**, and **one missing rule that would produce
false accusations against publishers for reasons that have nothing to do with editorial choice**.
Eight changes are required first; three are blocking.

---

## 1. Does the design preserve the stated principle?

> *The AI analyzes only one article; all cross-article comparison is deterministic and
> evidence-based.*

**Structurally: yes.** No prompt contains two articles. The comparison operates on stored records
through set arithmetic. Extraction happens once per article and is cached, so the comparison is a
pure function of stored rows — the determinism claim is correctly stated and holds. The
span-verification rule is a genuine strengthening: it makes each extracted item falsifiable against
its own source text, which is more than the original text-based design ever achieved.

**But there are two leaks, and one of them is load-bearing.**

### Leak 1 — `omissionAspects` is not grounded in the article (blocking)

The insights system prompt says *"Use ONLY the text provided — no outside knowledge."* The
`omissions` field asks *"what a reader is not told."* **These instructions contradict each other:
you cannot identify what is missing from a text using only that text.** Any answer draws on the
model's prior about what a story of this kind normally contains.

That is tolerable for the existing prose field, which is displayed as one model's impression of one
article. It is **not** tolerable when the same judgement is promoted to a `key` that enters set
algebra, because the output then looks like a counted fact and is not one. This launders a world-
knowledge prior into the product's most authoritative-looking surface.

### Leak 2 — closed-enum labels are model judgements with unmeasured reliability

`frames`, `depth`, `voices.role` and `centeredVoice` are single-article judgements. The design says
confidence is computed from counts, which is true and insufficient: **counting unreliable labels
yields a precise-looking number over noisy input.** "7 of 9 outlets frame this as economic
consequences" is only meaningful if a re-extraction of the same nine articles produces the same
nine labels. That number is unknown, and Phase 0 does not measure it — §10.5 measures collision
rate for *open* keys only.

Both leaks are fixable (§6), and neither breaks the one-article invariant. But the design overstates
its own guarantees, and the overstatement is exactly the kind that produced the last roadmap's
failure.

---

## 2. Blocking flaw A — I2's paraphrase circularity, and an internal contradiction

This is the most serious problem in the document.

**The circularity.** To say "5 outlets covered X and this one did not," you must know that this
article's phrasing of X and the others' phrasing of X are the same thing. Set algebra cannot know
that. A model could — but only by seeing both, which the invariant forbids. The design resolves
this with a deterministic normalizer (§4.2): token-normalize, then match on `weighted_jaccard ≥ 0.8`.

**It does not work at the specified threshold.** Take two real phrasings of one aspect:

```
"public cost figure"  → title_tokens → {public, cost, figure}
"cost to taxpayers"   → title_tokens → {cost, taxpayers}
jaccard = 1/4 = 0.25          weighted_jaccard, even with high IDF on "cost" ≈ 0.4–0.5
```

Both are far below 0.8. At that threshold the normalizer is **effectively exact-match**, and every
near-miss becomes a false "reported elsewhere, not here" claim about a real publisher. Lowering the
threshold trades those for false merges, which corrupt support counts instead. Short noun-phrase
keys carry too few tokens for overlap matching to separate the cases.

**The internal contradiction.** The design sets two numbers that cannot both hold:

| | |
|---|---|
| §10.5 / §12 Phase 0 gate | proceed if normalized-key collision rate **≥ 0.6** |
| §12 Phase 4 gate | proceed if false "not here" rate **≤ 2%** |

A 0.6 collision rate means ~40% of the aspects a target genuinely shares with its cluster fail to
collide — and each of those is a candidate false omission. **A 0.6 collision rate cannot produce a
2% false-positive rate.** To reach ≤2% you need collision ≳0.98 on shared aspects, which open
noun-phrase keys under token matching will not deliver.

**Required change.** Split I2 by failure mode and drop the unsound half:

- **Keep "only here"** (failure mode: a false uniqueness claim — wrong, but it credits rather than
  accuses) at a raised collision bar.
- **Drop "widely covered, not here"** from the deterministic-only path. It cannot be made sound by
  tuning a threshold.
- If the omission finding is judged essential, the only principled route is the one the *original*
  design already sanctioned (§8): a narrow **equivalence oracle** asked solely *"do these two spans
  state the same fact — yes or no?"*, over two short spans that are both shown to the reader. That
  is checkable, cacheable per span-pair, and falsifiable. **It also requires amending the invariant
  honestly** — from "the model never sees text from two articles" to "the model never *judges* two
  articles; it may only answer a yes/no equivalence question about two spans, both of which are
  displayed." Adopting the oracle silently while claiming the stronger invariant would be the worst
  outcome.

---

## 3. Blocking flaw B — the throughput arithmetic does not close

The design's Phase 2 gate is "coverage of the top 100 clusters ≥ 80% within a week of cycles." The
existing worker cannot deliver that, and at steady state it cannot even hold position.

```
RWE_POLL_INTERVAL   = 600 s          → 144 cycles/day     (feed_service.py:19)
RWE_INSIGHTS_BATCH  = 6 per cycle    → 864 articles/day    (article_insights.py:41)

clustered articles in the 6-day window : 7,502  → ~1,250/day arriving
worker ceiling                          :          864/day generated
```

**The queue diverges** — and that is before counting eligible catalog articles that never cluster,
which `enqueue_insights` also enqueues. Backfilling just the clustered set takes **8.7 days at full
throughput with nothing else in the queue**, which never happens.

Raising `RWE_INSIGHTS_BATCH` alone does not fix it: `run_cycle` generates **sequentially** under a
single-flight lock, so a batch large enough to keep up would run past the cycle interval and the
next cycle's request would be dropped. With a hosted API (~3–5 s/call) a batch of 15–20 fits inside
600 s comfortably. With `ollama` on modest hardware (the adapter's timeout is 300 s per call for a
reason) it does not.

**Required change.** Before Phase 2: (a) measure real end-to-end generation latency per variant on
the box; (b) size `RWE_INSIGHTS_BATCH` from that measurement rather than from the current default;
(c) add **bounded concurrency** inside `run_cycle` if the local-model path is to be used at all —
the single-flight lock protects against overlapping *cycles* and should stay, but serial generation
inside a cycle is the actual ceiling. This is a change to the insights worker, not to this feature,
and it must land first.

---

## 4. Blocking flaw C — no temporal parity

The design has no rule preventing a comparison from punishing an article for being early.

A story cluster spans up to six days. An article published in hour 1 cannot mention the arrest that
happened in hour 30. Under I2/I3 as written, the hour-1 article is compared against the full
membership and reported as lacking an aspect that **did not exist when it was written**. That is a
false statement about a publisher caused entirely by time.

The evaluation's sample is full of clusters where this would fire: the Nirmal Purja avalanche (36
outlets, "ten missing" → later confirmations), the Ebola outbreak (case counts rising across
members), the FIFA row (three days of developments).

**Required change.** Add **temporal parity** alongside input parity:

> Support for an *absence* finding may only be counted from members published at or before the
> target's `publishedAt` (plus a configurable grace window).

Presence findings ("only here", discrepancies) are unaffected. This mirrors the asymmetric-evidence
rule the design already gets right for input length, and it is the same shape: *absence is only
evidence under conditions where absence could have meant a choice.*

Note that L0's `timing` block already computes everything needed; nothing new must be extracted.

---

## 5. Non-blocking but required changes

### 5.1 I5 (corroborated omissions) is redundant under its own rule — delete it

§5.5 emits an omission only when ≥K other members' **positive** aspects contain the key. But if K
members positively cover an aspect and the target does not, **I2 already emits that finding**, from
positive evidence alone, with no world-knowledge input. I5 therefore contributes nothing except the
model's opinion that the gap is conspicuous — the ungrounded judgement of Leak 1.

Under its own corroboration rule, **I5 ⊆ I2**. It is the riskiest tier in the design and it is also
the empty one. Delete it, and delete `omissionAspects` from the contract with it.

### 5.2 `max_tokens=700` will truncate the facets object and burn articles permanently

`generate()` passes `max_tokens=700` (`article_insights.py:166`). The current output is a summary
plus five prose fields. Adding up to 2 frames + 8 aspects + 6 voices + 6 quantities, **each with a
verbatim evidence span**, plausibly doubles the output. A truncated response is invalid JSON →
`parse_and_validate` raises → the row takes a failed attempt → **three truncations mark the article
terminally `failed`**, and a terminal negative cache is not retried.

This would silently destroy coverage on exactly the longest, richest articles — the ones most worth
comparing.

**Required:** raise `max_tokens` (≈1,500) from a measured token distribution on the production
sample, and add a distinct validation failure for truncation so it is visible rather than counted
as a model error.

### 5.3 The cost estimate is roughly half the real figure

§9.1 assumes ~400 input / ~500 output tokens. The system prompt must now carry the full facets
schema, four enums and the span rule — realistically 600–900 tokens on its own.

| | design says | realistic |
|---|---|---|
| tokens per article | ~400 in / ~500 out | ~1,000 in / ~1,000 out |
| `claude-opus-4-8` one-off (7,502) | ~$109 | **~$225** |
| `claude-opus-4-8` steady state | ~$544/mo | **~$1,125/mo** |
| `claude-haiku-4-5` steady state | ~$109/mo | **~$225/mo** |

The conclusion ("which variant, not whether") survives, but the number presented to whoever signs
off on spend must be the right one. Confirm with `count_tokens` on the real prompt before enablement.

### 5.4 The Phase 3 gate no longer discriminates

The gate is "≥50% of rendered cards carry a finding beyond the outlet count." That bar was
diagnostic for L0 because L0's finding classes were mostly dead. **Every article has a frame**, so
I1 will satisfy it on ~100% of cards while potentially saying nothing useful — *"this article frames
it as economic consequences, like the other eight"* is the L0 obviousness failure in a new costume.

**Required:** emit frame/depth/voice findings **only on divergence from the cluster majority**, and
restate the gate as *"≥50% of cards carry a finding that distinguishes this article from its
cluster."* Design the metric so the failure mode it was built to catch cannot pass it.

### 5.5 Syndication defeats support counting, and `publisher_identity` does not help

Both designs claim support counted in publisher identities prevents "syndication of one wire story
reading as five outlets." **It does not.** `publisher_identity.groups()` collapses *many names of
one outlet* (registry aliases, host forms sharing a brand domain, bare name ↔ host). Six different
outlets each running the same AP copy are six genuine identities, and their facets will be
identical because the text is identical — producing "6 of 9 outlets report X" from **one** act of
journalism.

This flaw is inherited, not introduced. But the insight layer makes it **detectable for the first
time**: near-identical facet sets plus near-identical summaries across members is a strong
syndication signal, computable deterministically.

**Required:** collapse near-duplicate members into a single support unit before counting, and show
the reader the wire relationship rather than hiding it (*"reported by 4 outlets, 3 of them carrying
the same wire copy"* is a better fact than either alternative).

### 5.6 A missing facet: `format`

The evaluation found *"Spider-Man: Brand New Day review"* (The Guardian) clustered with *"Korea Box
Office"* (Variety). Comparing a **review's** aspects against a **box-office report's** is
meaningless, and the current defence — a regex over headlines (`_TEMPLATE_PATTERNS`) — is a blunt
instrument that fires on the whole cluster or not at all.

A closed `format` enum (`news_report`, `analysis`, `review`, `live_blog`, `obituary`, `listicle`,
`opinion`, `other`) costs nothing extra to extract and enables a **format-parity gate**: compare an
article only against members of the same format, and report the partition size. This is a better
version of the `template_genre` gate and would have suppressed a real production case.

### 5.7 Phase 0 must measure enum reliability, not just key collision

Add to §10: extract the **same** production sample twice (same recipe) and once under a second
recipe, then report per-field agreement — `frames`, `depth`, `voices.role`, `centeredVoice`,
`format`. If frame labels are not stable under re-extraction, I1's counts are noise dressed as
consensus, and I1 is the tier the design ships **first**.

### 5.8 Prose and facets can contradict each other on screen

`bias.omissions` (prose, rendered today) and the facet-derived findings come from one call but are
validated independently. A reader can see prose saying *"the article does not tell you the cost"*
directly above a comparison card that emits no such finding (correctly — it was uncorroborated).

**Required:** a consistency check at validation time, or an explicit UI rule about which surface
speaks about omissions. With §5.1 deleting `omissionAspects`, the cleanest answer is that **prose
speaks about the single article, the card speaks about the cluster, and the card never contradicts
prose because it never discusses omissions at all.**

---

## 6. Scalability

| concern | assessment |
|---|---|
| Comparison compute | Fine. Index build is O(members × facets); per-member diff is the same. Trivial next to story building. |
| Generation throughput | **The binding constraint** — §3. |
| Storage | Facets add ~1–2 KB/article. Negligible, but `article_insights` is cache-forever with no retention policy and now grows faster. Worth a decision, not urgent. |
| **Cache churn** | **A regression from the original design.** The revision keys the cache on a `member_state_hash` covering every member. News clusters gain members on every ingest cycle, so the hash changes continuously and **every card in the cluster is invalidated whenever any member is added or regenerated**. A 39-outlet cluster recomputes 39 cards per new member. The cache is least effective exactly when a story is most read. The original design (§3) cached with the story view, whose lifecycle already tracks membership; that was correct and should be restored. Since the computation is pure CPU over rows already loaded during story build, computing all members' cards in one pass is both simpler and cheaper than per-article caching. |
| Cluster-local IDF | Correct, but it makes every member's salience depend on the whole cluster — a second, independent churn source, reinforcing the point above. |
| The join | `article_insights` is keyed by **canonical URL**; story coverage members carry the **publisher** URL. This is the exact defect that invalidated the first audit run (evaluation §1). The design must specify `ingest.canonical_url()` at the join and the shape-contract test must cover it. |

---

## 7. Migration risks

### 7.1 A model upgrade turns the feature off (highest operational risk)

Recipe parity is correct — comparing across recipes measures models, not outlets. But it means that
when `claude-opus-4-8` is superseded, the catalog splits into old-recipe and new-recipe members, and
**no cluster can be compared across the boundary**. Every active cluster returns `mixed_recipes`
until the old members age out of the 6-day window — days of the feature silently rendering nothing,
triggered by a routine model upgrade.

**Required:** a documented switchover procedure before the first enablement. Options, in preference
order: (a) **dual-write** during migration (generate both recipes for new articles; compare within
each; switch the designated comparison recipe once the new one dominates the window) at ~2× cost for
the window's duration; (b) a **compatibility class** allowing recipes declared equivalent to be
compared, where the equivalence is *measured* by the benchmark harness on the golden set, never
assumed. Option (b) is cheaper and is the reason §5.7's cross-recipe agreement measurement should
exist from the start.

### 7.2 Every vocabulary change is a paid catalog-wide regeneration

Adding one value to the `frames` enum changes the choice set the model had, so old records are not
comparable to new ones. `vocabVersion` correctly *detects* this; it does not make it cheap. A bump
costs a full re-extraction (~$225 at Opus, §5.3) plus 8.7+ days of worker time (§3).

**Required:** state this explicitly in the design, and validate the vocabularies against a
production sample in Phase 0 — before the first generation run, while regeneration is still free
because the table is empty.

### 7.3 The dormancy window is the only free moment, and it is being spent

The design correctly notes that the contract change is free while `article_insights` is empty. That
argument extends further than the document admits: **`max_tokens`, `temperature`, the vocabularies,
the span rule, the `format` facet and the enqueue ordering are all free to change today and all
expensive after the first generation run.** Everything in §5 should land in the same contract
change, not incrementally.

---

## 8. The organizing principle the design should adopt

The tiers do not differ mainly in what they extract. They differ in **what happens when extraction
or matching fails**:

| failure mode | consequence | tolerable collision/reliability |
|---|---|---|
| **Silence** — a finding is missed | a card is slightly less useful | low bar is fine |
| **False uniqueness** — "only this article has X" when others do | wrong, credits falsely | moderate bar |
| **False accusation** — "outlets covered X, this one didn't" | a public statement about a named publisher | very high bar, or don't ship |

Sorting the proposed tiers by that column changes the roadmap materially — and, usefully, splits
tiers that the design treated as single units:

| tier | failure mode | verdict |
|---|---|---|
| I1 frames / depth (closed enum) | silence | **ship first** — with divergence-only emission (§5.4) and reliability measured (§5.7) |
| I3 **quantity discrepancies** | silence — a missed collision means a missed disagreement, harmless | **ship second** — the highest-value output in the design, and the safest |
| I2 **"only here"** | false uniqueness | ship third, raised collision bar |
| I4 voices (closed enum) | accusation-shaped, but enum-based so reliability, not collision, is the risk | ship fourth, gated on §5.7 |
| I3 **"figure absent here"** | accusation | **do not ship** under deterministic-only matching |
| I2 **"widely covered, not here"** | accusation | **do not ship** without the equivalence oracle (§2) |
| I5 corroborated omissions | — | **delete** (redundant, §5.1) |

Note this reverses the design's own ordering on one point: it had quantity discrepancies third and
aspects second. Discrepancies are both more valuable and strictly safer, and they should come
earlier.

---

## 9. Required changes before implementation

**Blocking:**

1. **Resolve I2's omission half** — drop it, or adopt the equivalence oracle and amend the stated
   invariant honestly (§2).
2. **Fix worker throughput** — measure latency, size the batch from it, add bounded concurrency
   (§3). Without this the feature never reaches renderable coverage.
3. **Add temporal parity** — absence findings may only count support from members published at or
   before the target (§4).

**Required in the same contract change (all free only while the table is empty):**

4. Delete I5 and `omissionAspects` (§5.1).
5. Raise `max_tokens` from a measured distribution; add a truncation-specific failure (§5.2).
6. Add the `format` facet and a format-parity gate (§5.6).
7. Restore story-scoped caching in place of the per-article `member_state_hash` (§6).
8. Add near-duplicate/syndication collapsing before support counting (§5.5).

**Required in Phase 0, before any generation run:**

9. Measure **enum reliability** under re-extraction and across recipes, not only open-key collision
   (§5.7); raise the collision gate to a bar consistent with the precision target (§2).
10. Correct the cost model with `count_tokens` on the real prompt (§5.3).
11. Document the model-switchover procedure (§7.1).

**Also required, and independent of this design:** the four prerequisites already listed in the
revised design §11 — fix L0's precision defects, add the shape-contract test, address story
fragmentation, and obtain insights enablement.

---

## 10. What survives review unchanged

To be clear about what is right, since most of this document is criticism:

- **The core division of labour.** Model extracts one article; code compares. This is the correct
  architecture and it is what makes the feature defensible at all.
- **Span verification.** The strongest idea in the design and a genuine advance over the text-based
  original — every displayed item is falsifiable against its own source.
- **Asymmetric evidence.** *Presence is evidence; absence is evidence only above a parity floor.*
  Correct, and §4 extends it to time rather than replacing it.
- **Recipe parity.** Correct and necessary; §7.1 addresses its cost, not its validity.
- **Reusing `_finding()` and the existing evidence format.** No second comparison engine.
- **Rejecting summary-text comparison** (§8 of the design), with the reasoning recorded.
- **Externally grounded vocabularies** rather than invented taxonomies.
- **Phase 0 as a hard gate.** The discipline that killed L1–L3 was the process working; keeping it
  is right. This review's changes make Phase 0 measure the right things.
