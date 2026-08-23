# Recommendation System Architecture Audit & Enhancement Proposal

**X's open-source For You algorithm (greenwichg/x-algorithm, Aug 2026 release) studied against the
Hidden View recommendation stack, as both actually exist in code.** Report only — no code has been
modified. Sources: a full clone of `x-algorithm` (read at commit of 2026-08; README, `home-mixer/`,
`candidate-pipeline/`, `phoenix/`, `thunder/`, `simclusters/`, `vm-ranker/`, `visibility-filtering/`,
`home-mixer/params/param.rs` production weights) and the Hidden View engine
(`rwe/`, `examples/api_server.py`, `examples/personalize.py`, `examples/api_fastapi.py`,
`examples/store.py`, ingestion/classification modules, and the repo's own decision record:
`RECOMMENDATION_SYSTEM_ASSESSMENT.md`, `PRE_PRODUCTION_RECOMMENDATION_ROADMAP.md` (W1–W3, W8),
`W8_FINAL_ARCHITECTURE_DECISION.md`, `RC2_4_FEEDBACK_RANKING.md`, `INTEREST_INTENSITY.md`,
`COUNTRY_RECOMMENDATIONS.md`).

**The one-sentence thesis:** X's architecture is worth copying in *shape* (a staged candidate
pipeline, multiple specialized sources blended into one feed, diversity applied as an explicit
re-ranking step, feedback signals that actually reach ranking) and worth rejecting in *objective*
(a single engagement-weighted scalar) — and Hidden View's slot-based blend is not a primitive
version of X's scorer but a structurally different mechanism that is *better* for Information
Health, because a quota survives pressure that a weight does not.

---

## Part I — The X architecture (Phase 1)

### Request path, end to end

```
viewer request
  → QUERY HYDRATION      ~20 hydrators: engagement-action sequence (the model's main input),
                         follows, blocks/mutes, muted keywords, impression bloom filter,
                         served history, followed topics, demographics
  → CANDIDATE SOURCES    in parallel:
                         IN-NETWORK   Thunder — recent posts from followed accounts, held in RAM
                         OUT-OF-NET   Phoenix retrieval — two-tower ANN over post embeddings
                                      SimClusters — engagement-community ANN
  → CANDIDATE HYDRATION  post text/media, author labels, engagement counts, semantic IDs
  → PRE-SCORING FILTERS  17 filters: cross-source dedup, age > 48 h, self, blocked/muted,
                         already-seen (×2 records) + already-served, muted keywords,
                         subscriber-gates, new-user engagement floor, inventory holdout
  → SCORING              PhoenixScorer: one transformer predicts P(action) for ~20 actions
                         RankingScorer: weighted sum → author-diversity decay → OON discount
                                        → new-author boost
                         VMRanker: greedy DPP re-rank over post embeddings (quality vs cosine
                                   similarity trade, θ-controlled)
  → SELECTION            top-K by final score
  → POST-SELECTION       visibility filtering (ALLOW / INTERSTITIAL / DROP), ancestor drops,
                         conversation dedup
  → BLENDING             ads, Who-to-Follow, prompts at fixed positions
  → SIDE EFFECTS         served-history writes, impression records, caches, event logs
```

The production weights (from `param.rs`, synced from production by cron): favorite 0.5, reply 5,
repost 1, quote 5, share 2, share-via-DM 5, **share-via-copy-link 20**, follow-author 4, click 0.4,
dwell-seconds 0.004/s — against **not-interested −43.2, block −31.2, mute −58.8, report −234**,
not-dwelled −0.02. Two structural notes worth carrying forward: the weights scale *predicted
probabilities of the viewer's own actions*, not raw counts; and the strongest signals in the entire
objective are the explicit negative-feedback actions.

### What each component is for — and whether Hidden View needs it

| X component | Problem it solves | Signal in → out | HV equivalent | Adopt? |
|---|---|---|---|---|
| `candidate-pipeline` framework | one contract for source/hydrator/filter/scorer/selector/side-effect stages, run in parallel, individually toggleable | — | implicit: `_serve` → strategies → `_slice_admits` → reranks → `_select_diverse` → `record_recommendations_shown` | **Adopt the vocabulary** (Tier 1 refactor; no behavior change) |
| Thunder (in-network) | follow graph is enormous; recency demands RAM | follows → recent posts | none (no follow graph) | No — HV's "network" is topics/stories, not accounts |
| Phoenix retrieval (two-tower) | millions of posts → hundreds, by learned similarity | engagement history → user vector; posts → candidate index | RWE walk over the whole catalog (catalog is 10³–10⁴, so *every* candidate is already scored) | **No** at current scale — retrieval stages exist to avoid scoring everything; HV scores everything |
| SimClusters | interpretable community structure as a *second, cheap* OON source | engagement bipartite graph → ~10⁵ clusters → ANN | story clusters (content co-occurrence); `rwe/satisfaction.py` label-propagation communities over the item-item projection — literally SimClusters-shaped, already in the tree | **Adapt** — clusters as a candidate source, incl. *underrepresented* clusters (see Blind-spot slice) |
| Phoenix ranking (transformer) | predict ~20 action probabilities per candidate | action sequence + candidate → P(action) vector | RWE scores + slot plan | **No** — needs training data HV will not have for a long time, and predicts the wrong objective |
| RankingScorer | collapse predictions to one scalar; then author decay, OON discount, cold-start boost | P(actions) → score | blend plan + within-slice rank | Partially — the *adjustment* pattern (decay/boost as explicit multipliers on an auditable base) matches HV's interest/country rerank design |
| VMRanker (DPP) | punish similarity among neighbours at the top of the feed | embeddings + scores → re-ranked slate | publisher cap only | **Adapt** — as explicit-feature MMR, not embedding DPP (Part IV, Phase 8) |
| Visibility filtering + labeling path (grox, agatha, botmaker…) | open UGC firehose needs a safety layer off the request path | classifiers/rules → labels → ALLOW/DROP | curated RSS registry, cluster-trust gates, template gate, factuality audits | No — HV's supply side is curated; keep source curation as the "safety layer" |
| Seen/served filters + bloom | never reshow what the viewer saw | impressions → exclusion | **absent** (only *read* articles are excluded, via the reader's graph row) | **Adopt** (Tier 1) — `rec_events` already records shown/opened; nothing consumes it as a filter |
| Experimentation params + inventory holdout | tunables live in config; production defaults synced to code; deterministic holdouts | — | env-var kill switches; no A/B; offline counterfactual audits | **Adapt minimal** — per-user deterministic cohort flag + shadow harness (formalizing what `audit_country_rerank.py` etc. already do offline) |
| Side effects (served history) | feedback loop's denominator | serves → storage | `record_recommendations_shown` + feed-composition funnel | Present already |

---

## Part II — Hidden View as it actually is (Phase 2)

Traced through code, not docs. The full path:

```
INGESTION      rss_ingest (stdlib RSS/Atom, operator-configured feeds) · crawler · GDELT GKG
               → FeedArticle (SQLite, canonical-URL dedup)
ENRICHMENT     outlet_registry.csv (canonical outlet + AllSides lean — the product's ONE lean
               vocabulary) · classify_topic (deterministic taxonomy) · optional LLM enricher
               · event geography (article_event_locations) · entities · publisher_metadata
CLUSTERING     clustering.py (Jaccard union-find, time-windowed, link-quorum gate)
               → story_service (validated stories: trust gates, template gate, lean distribution,
               blindspot verdicts, freshness/lifecycle/momentum)
CORPUS         feed_source exports catalog → qbias CSV → simulate_users builds a SYNTHETIC
               population's co-click matrix over the REAL catalog → corpus_refresh validates,
               builds off-thread, atomically hot-swaps (freshness gate, eligibility gate)
SERVING        /api/recommendations → _serve (personal | demo-exhibit | anon showcase; a signed-in
               reader with no reads gets [] — never someone else's feed)
               → Personalizer: reader appended as one real row to the synthetic corpus
                 (PersonalModel cached by (reading_version, reception_version))
               → blend_plan_for: openness slider → RWE-B slot budget (4/6/8 of 14);
                 default blend: rwe-b 6 · rwe-d 4 · adaptive 4
               → per strategy: RWE walk scores ENTIRE catalog → _slice_admits (rwe-b: political
                 only) → interest rerank (8 sliders → bounded multiplier) → country rerank
                 (countryMatch + backfill honesty) → cross-first ordering (rwe-b)
               → _select_diverse: 3× overfetch, per-publisher cap across the feed,
                 budget-preserving, never-shrink, rank-preserving
               → story slot post-pass (RWE_STORY_SLOT: different-publisher sibling of a story
                 the reader actually read)
               → serialization: evidence-gated reasons (familiarity claims only when measured),
                 catalog-lean consistency, media join → Evidence Resolver → record shown
FEEDBACK       rec_events (shown/opened): drives Open-Mindedness, acceptance analytics, W2
               exposure shrinkage · rec_feedback (like/dislike/ignore/read_later): **written and
               never read** ("Recorded only (B1)… kept for a future consumer") · improvement
               lifecycle + RC2.4 feedback ranking (report improvements only, not the feed)
```

**Facts that frame everything below:**

1. **The collaborative base is synthetic by design.** Real readers are single appended rows;
   `W8_FINAL_ARCHITECTURE_DECISION.md` already concluded external datasets don't transfer and the
   real-behaviour graph (W8B) waits for production reads. Any proposal assuming rich real co-click
   data is fantasy at Wave-0 scale.
2. **The catalog is small.** 10³–10⁴ live articles. X's retrieval tier exists because scoring
   everything is impossible there; here it is what already happens. This kills the case for
   two-tower retrieval *now* and redirects the effort to what happens after scoring.
3. **Explicit feedback is captured and orphaned.** Four signal types, idempotent, indexed — and
   consumed by zero ranking paths. X's production weights say explicit negatives are the most
   informative signals that exist (−234 report vs 0.5 favorite). This is HV's cheapest large win.
4. **No repeat suppression.** `exclude_seen` removes *read* articles only. A card shown five
   sessions running and never opened is never suppressed; `rec_events` holds exactly the rows
   needed to fix this and is not consulted.
5. **Blind spots are computed and not acted on.** `health_report.blind_spot_gaps` names the
   reader's under-read topics on every report; no candidate source targets them. Stories has
   blindspot *verdicts* (coverage asymmetry) with trust gating — also not a feed input.
6. **There are no production embeddings.** Semantic similarity exists as offline audit tooling
   (`audit_semantic_arms.py`, the V1′ verifier work), not in serving. Claims of "article
   embeddings" in HV describe research artifacts, not the product.
7. **Explainability is a load-bearing asset.** Explain-vs-served byte-parity is pinned by tests;
   reasons are evidence-gated. Any ranking change that cannot state its reason truthfully
   regresses the product's core promise. This is a real constraint on adopting opaque scorers.

---

## Part III — Capability matrix (Phase 3)

Priorities: **P1** = do now, **P2** = after foundation, **P3** = research/only-at-scale, **—** = not wanted.

| X capability | HV equivalent | Current implementation | Gap | Adaptation | Priority |
|---|---|---|---|---|---|
| In-network candidates (Thunder) | followed topics / story continuations | story_continuation, Discover | no follow graph, by design | none — "in-network" for a news-health product is *your stories and topics*, which exists | — |
| Out-of-network candidates | RWE-D (long-tail), RWE-B (bridging) | production blend | none — HV's OON is deliberate diversity, not engagement-similarity | keep; add story + blind-spot sources beside them | P1/P2 |
| Two-tower retrieval | — | — | not a gap at 10³–10⁴ items | revisit only if catalog ≥ 10⁵ or real-user CF (W8B) lands | P3 |
| User embeddings (learned) | reader row + report distributions | user_report: topic/lean/outlet/emotion shares | not learned; doesn't need to be | formalize the existing report vector as the **reader profile** input to new sources | P1 |
| Candidate embeddings | — | offline audits only | real gap for semantic retrieval | only with W3 article-level work; not for serving now | P3 |
| Similarity retrieval (ANN) | story clustering | Jaccard union-find + quorum | n/a at scale | story-cluster retrieval as candidate source | P2 |
| Engagement history | reads + shown/opened + feedback | ingest.Scorer, rec_events, rec_feedback | feedback unconsumed; no dwell/completion | consume what exists before capturing more | P1 |
| Candidate hydration | media/lean/logo joins | `_enrich_rec_media`, evidence resolver | none material | formalize as hydrator stage | P1 (refactor) |
| Candidate filtering | slice admits + publisher cap | `_slice_admits`, `_select_diverse` | no seen/served, no story-dedup in feed | impression filter + story quota | P1 |
| Deduplication | first-seen article dedup | `_select_diverse` | near-dupes across publishers (same story) survive | story-cluster quota in selector | P1 |
| Seen-content filtering | — | **absent** | shown-unopened cards recur forever | repetition decay from `rec_events` (suppress ≠ hard-drop; decay per re-serve) | **P1** |
| Freshness | corpus gates + candidate gates | 60-day gate, first-seen anchoring, URL-date | Guardian/WT month-name parser (known) | close known parser gap; per-slice age targets later | P1 (small) |
| Ranking | RWE scores within slices | `random_walk.py` closed-form | — | keep; RWE stays the scoring core | — |
| Multi-objective scoring | slot-budget blend | `blend_plan_for` + slices | no *within-slice* composite (walk score only, then multipliers) | within-slice: walk × interest × country × freshness-novelty × repetition-decay — all bounded multipliers, X's adjustment pattern | P2 |
| Engagement prediction | — | — | no training data; wrong objective as a target | only ever as *one bounded input*, never the objective | P3 |
| Content understanding | registry lean + deterministic topic + emotion/register | outlet_registry, classify_* | article-level lean coarse (W3, κ=0.14 text models) | W3/I10 design stands: confidence-gated article lean around the **registry prior** (registry stays authoritative; content inference is the fallback) | P2 |
| Topic discovery | taxonomy + Discover facets | classify_topic | no per-reader topic exploration source | blind-spot slice covers the health case | P1/P2 |
| Similar-cluster retrieval | story siblings | story slot (one conditional slot) | slot is single and conditional | promote to a story *source* with diversity-within-story selection | P2 |
| Diversity re-ranking | publisher cap, cross-first, slot budgets | `_select_diverse` | no topic/story/lean repetition control across the feed | constrained selector + explicit-feature MMR (Phase 8) | P1/P2 |
| Safety filtering | curated sources, trust/template gates | story_service gates | different threat model (no UGC) | none beyond current curation | — |
| Quality filtering | registry curation, factuality audits | corpus validation | per-article quality score absent | keep publisher-level; don't fabricate article-level quality | — |
| User context hydration | settings + report + exposure | `rec_params_from_settings`, `_reader_exposure` | scattered; no single seam | one QueryContext built per request (refactor) | P1 |
| Real-time signals | corpus hot-swap (minutes) | corpus_refresh | no per-session adaptation | session-level: repetition decay is the real-time signal that matters here | P1 |
| Candidate blending | slot plan | `_select_diverse` budgets | — | keep — this *is* the blender, with health semantics | — |
| Experimentation | env flags; offline counterfactuals | audit_* scripts | no cohorts, no online A/B | deterministic per-user cohort + shadow serving | P2 |
| Feedback loops | W2 exposure shrinkage (partial) | personalize `shrunk_exposure` | rec_feedback orphaned; adaptive exposure not fed by live rec_events end-to-end | Phase 9 wiring | **P1** |
| Model training | — | — | none needed for Tier 1/2 | W8B when real reads exist | P3 |
| Offline evaluation | strong: eval_mind, RQ2/RQ3 battery, feed-composition funnel | rwe/metrics, RESULTS.md | live-corpus feed-quality metrics not first-class | Part V eval framework — compute per served feed | P1 |
| Online evaluation | acceptance analytics | `_recommendation_acceptance` | no diet-shift measurement, no cohort comparison | health-delta metrics + cohorts | P2 |

---

## Part IV — What to build (Phases 4–11 condensed into positions)

### The objective, settled first (Phase 10)

X: `score = Σ wᵢ·P(actionᵢ)` — personalization and value in one scalar, diversity as decay factors
and a DPP afterpass. The known failure mode of the scalar: anything that raises predicted
engagement outbids everything else, and the negative weights are the only brake.

**Hidden View's counter-design — keep the two questions in different mechanisms:**

- **"What would this reader find relevant?"** → *within-slice ordering*: the RWE walk score,
  shaped by bounded multipliers (interest sliders, country, freshness/novelty, repetition decay,
  feedback decay). Multipliers, not additive bonuses, and each with a floor/cap — X's own
  author-decay/OON-discount pattern, applied to health-relevant features.
- **"What should this reader be exposed to?"** → *slot structure*: the blend plan (bridge budget
  from openness, discovery budget, story slot, blind-spot slot). Quotas and floors, not weights.
  A reader's feedback can tune a dose; it cannot silently zero a slice. This is the property that
  makes the engagement trap structurally hard rather than tuning-dependent, and it is the single
  most important thing **not** to copy from X.

`FinalFeed = ConstrainedSelect( slices ordered by bounded-multiplier scores, quotas: per-publisher ∧ per-story ∧ per-topic ∧ slice budgets )`

### Top opportunities (Phase 4), each with its mechanism

1. **Close the feedback loop** (B, Phase 9 — P1). Consume `rec_feedback`:
   `dislike` → per-article exclusion + a *bounded, decaying* per-publisher/topic multiplier;
   `ignore` (and shown-unopened ≥ N) → repetition decay; `like`/`read_later` → interest-nudge-sized
   positive multiplier. Cap: no accumulation of dislikes may push any multiplier below a floor, and
   RWE-B's slice budget is not feedback-addressable (dose yes — via the existing openness/W2
   machinery — direction no).
2. **Repetition/impression suppression** (P1). Per-(user, article) decay from `rec_events.shown_at`
   count and recency; per-story version of the same so five outlets' takes on one story don't
   serially occupy a slot across sessions.
3. **Story-level recommendation** (E — P2). Promote the story slot to a source: reader profile →
   relevant *validated* stories (topic match, continuation, geography) → within each story choose
   the article that maximizes marginal diversity (publisher not yet served, lean bucket
   underrepresented in the feed, reporting register preferred over opinion when both exist) →
   "Here is the story, this is the take you haven't seen." Uses only existing primitives
   (story_service verdicts + lean distributions).
4. **Blind-spot slice** (D — P1/P2). One or two slots sourced from `blind_spot_gaps` topics,
   filled with *fresh, reporting-register, registry-rated* articles. The RWE-D machinery already
   suppresses popularity; this targets topic gaps the report already names — and the card's reason
   is honest by construction ("you've read 41 technology articles and no climate coverage").
5. **Constrained selector + explicit-feature MMR** (C, F, Phase 8 — P1 quotas, P2 MMR). Extend
   `_select_diverse`'s publisher cap with story and topic quotas (same skip-and-spill pattern, same
   never-shrink guarantee). If measurement shows residual sameness, add greedy MMR where
   `sim(i,j) = w·[same story, same publisher, same topic, same lean bucket, same country]` —
   deterministic, explainable, testable. **DPP rejected for now**: it needs embeddings HV doesn't
   serve, is opaque to the explain contract, and at k=14 from ~40 candidates a quota'd greedy pass
   captures nearly all of the diversity gain. Trade-off recorded: DPP handles *graded* similarity
   better than binary features; revisit if W3 article-level representations land.
6. **Pipeline formalization** (Phase 7 — P1). Name the existing stages (`Source`, `Filter`,
   `Scorer`, `Selector`, `SideEffect`) as seams in `_serialize_recommendations` so items 1–5 are
   pluggable units with per-stage tests and kill switches, X-style. No behavior change; the
   refactor is the enabler.
7. **Reader-profile context object** (P1). One `QueryContext` assembled per request: settings,
   report shares, exposure, blind-spot gaps, feedback state, impression state. Every source/scorer
   reads it; nothing reaches into the store mid-pipeline.
8. **Feed-quality side-effect metrics** (Phase 13.8 — P1). `record_feed_composition` exists;
   extend it: per-feed publisher HHI, story-dup count, topic coverage vs blind spots,
   cross-cutting share, median age, repetition rate vs prior serves, novelty share. These are the
   offline eval and the production dashboards, from one code path.
9. **Cohorts + shadow** (Phase 12/13.9 — P2). Deterministic per-user hash cohort; new sources run
   in shadow (logged, not served) first — the discipline `W3A_PRODUCTION_READINESS.md` already
   prescribes, made reusable.
10. **W3 article-level lean, confidence-gated** (P2, already designed as I10). The registry prior
    stays authoritative; content inference adjusts within confidence bounds. Unblocks
    within-story viewpoint selection (#3) beyond outlet lean.
11. **Interest-cluster retrieval over real co-reads** (Phase 6 — P3/W8B). When real reads exist:
    label-propagation communities (the `satisfaction.py` machinery) over the *real* item graph as
    a SimClusters-equivalent source, including retrieval from clusters *adjacent to but outside*
    the reader's set — the underrepresented-cluster idea, which is where HV would genuinely go
    beyond X (X retrieves from clusters you're in; HV should also retrieve from clusters you're
    provably not in).
12. **Emerging-story discovery** (A — P2). Stories' momentum/lifecycle intelligence exists;
    a "developing story, first reporting" source is a natural quota'd slice.

### Engagement traps and their specific guards (Phase 11)

| Trap | Guard, concretely |
|---|---|
| Outrage/fear optimization | emotion classification may only ever *penalize or inform*, never boost; Emotional Balance is a report metric and an eval metric, not a positive ranking weight |
| Clickbait | opened-without-return is not a positive signal anywhere; no CTR term exists in scoring, and none is proposed |
| Filter bubble via feedback | feedback tunes bounded multipliers with floors; slice budgets are not feedback-addressable; bridge dose adapts through W2's shrunken-exposure path only |
| Popularity bias / rich-get-richer | RWE-D's erasure *is* the anti-popularity mechanism; keep no global-popularity term in any scorer |
| Publisher concentration | cap exists; HHI measured per served feed (item 8) so drift is seen, not suspected |
| Repetitive content | items 2 and 5 |
| Polarization | RWE-B bridges by construction ("different but not too far"); cross-cutting share tracked per feed; the floor keeps a minimum bridge budget at every openness setting |
| Metric gaming of "acceptance" | acceptance metrics are always paired with diet metrics (report deltas); a change that raises acceptance while topic coverage narrows fails eval |

---

## Part V — Target architecture, changes, evaluation, migration (Phases 12–13)

### Target pipeline (the current one, formalized, with the new units in place)

```
QueryContext        settings · report shares · exposure · blind-spot gaps · feedback state ·
                    impression state · country · openness
  ↓
Sources (parallel)  RWE-B bridge · RWE-D long-tail · Adaptive · Story source · Blind-spot
                    source · [shadow: candidates logged, unserved]
  ↓
Hydrators           media/date/logo · catalog lean · story membership · evidence
  ↓
Filters             slice admits · impression/repetition decay (soft) · feedback exclusions ·
                    freshness
  ↓
Scorers             RWE walk score × interest × country × freshness-novelty × repetition ×
                    feedback multipliers   (all bounded, all explainable)
  ↓
Selector            slice budgets (openness-driven) + publisher cap + story quota + topic quota,
                    skip-and-spill, never-shrink, rank-preserving
  ↓
Serializer          evidence-gated reasons (extended for story/blind-spot cards)
  ↓
Side effects        record shown · feed-quality metrics (HHI, dup, coverage, cross-share, age,
                    repetition, novelty) · cohort/shadow logs
  ↓
Feedback loop       rec_feedback + rec_events → multipliers/decays → next QueryContext
```

### Schema changes (Phase 13.4)

- `rec_events`: add `shown_count` (or derive), `last_position`; index `(user_id, shown_at)`.
- New `rec_feedback_state` (or computed view): per-(user, topic|publisher) decayed multiplier —
  derivable from `rec_feedback`; materialized only if per-request derivation measures slow.
- `feed_articles`: `lean_confidence` + `article_lean` columns (W3, nullable, registry stays the
  default).
- New `experiment_assignments` (user_id, experiment, cohort, assigned_at) — deterministic hash,
  table exists for audit.
- Feed-quality metrics ride the existing analytics/obs path; no new table required.

### ML changes (Phase 13.5)

Tier 1 requires **none**. Tier 2: the W3 confidence-gated article-lean classifier (already
designed; offline batch at ingest, X's "labeling path off the request path" pattern). Tier 3 only:
embeddings, two-tower, engagement prediction, W8B real-graph training.

### API changes (Phase 13.6)

- Extend `RECOMMENDATION_FEEDBACK_TYPES` with `another_viewpoint`, `already_know`,
  `too_repetitive`, `fewer_from_source`, `more_topic` (wire vocabulary; storage is shape-ready).
- `/api/recommendations`: no contract change (new cards are new `strategy` values — the model
  already carries `strategy` per card; catalogs gain `rec.strategy.story` / `rec.strategy.blindspot` keys).
- Explain endpoint: extend for the new sources — parity contract unchanged.

### Frontend changes (Phase 13.7)

Feedback chips exist; add the new actions and — the important part — *visible consequence*
("Fewer from this publisher — undo", and a settings page listing active feedback effects with
removal). An invisible feedback loop reads as surveillance; a visible one is a control.

### Evaluation framework (Phase 13.8) — CTR is not a metric here

Per served feed (computed in the side effect, aggregated daily): topic coverage vs the reader's
blind-spot set · publisher HHI · story-duplicate rate · cross-cutting share vs openness setting ·
lean-bucket distribution · reporting-vs-opinion share · median/max age · repetition rate vs the
reader's last N serves · novelty share (never-served publisher/topic) · emotional-tone mix.
Per reader over time: **Information Health report deltas** (the seven metrics + overall) — the
product's own score is the primary online metric. Plus reception honesty: opened-share of
cross-cutting cards (exists), feedback rates by type, empty-feed and backfill rates.

### Experiments (Phase 13.9)

Shadow first, cohort second, always against the current blend as control: (1) repetition decay —
expect repeat-rate ↓, acceptance flat-or-up; (2) story source vs one-slot status quo — story-dup ↓,
source-diversity ↑; (3) blind-spot slice on/off — topic coverage ↑, acceptance of those cards ≥
RWE-D baseline; (4) feedback wiring — dislike-repeat rate → ~0, no cross-share erosion (guard
metric); (5) MMR pass only if quota metrics show residual sameness.

### Migration (Phase 13.10)

Strangler pattern, mirroring corpus_refresh discipline: formalize stages (no behavior change,
parity-tested) → land Tier 1 units each behind an env flag default-off → shadow-log → enable for a
beta cohort → default-on with kill switch. The current blend remains the fallback at every step;
nothing replaces RWE-D/RWE-B — every proposal here *feeds* them or *selects after* them.

### Tiers (Phase 12)

| Tier | Items |
|---|---|
| **1 — now** | pipeline formalization · QueryContext · feedback wiring · repetition decay · story+topic quotas in selector · blind-spot slice (v1: topic-targeted multiplier on RWE-D slice) · feed-quality metrics · freshness parser gap |
| **2 — after foundation** | story source proper · MMR pass (if metrics demand) · W3 article lean · cohorts/shadow harness · emerging-story source · blind-spot slice v2 (own source) · new feedback vocabulary end-to-end |
| **3 — research / at-scale** | embeddings & two-tower · engagement prediction (bounded input only) · W8B real-graph clusters · DPP |

---

## What we have → what X does → adopt → improve beyond → build first

**Have:** a peer-validated diversity recommender (RWE-D/RWE-B/Adaptive) with slot-blend health
structure, evidence-gated explanations, strong offline eval — running on a synthetic collaborative
base, with orphaned feedback, no repeat suppression, and computed-but-unused blind spots.
**X does:** staged parallel candidate pipeline; multi-source blending; explicit negative feedback
as the strongest ranking signals; impression bloom filters; diversity as explicit adjustments +
DPP; labeling off the request path; params/holdout experimentation.
**Adopt:** the pipeline vocabulary; feedback-into-ranking; seen/served suppression; the
bounded-multiplier adjustment pattern; side-effect metrics; cohort/shadow discipline.
**Improve beyond X:** quotas-not-weights as the health mechanism; retrieval from clusters the
reader *isn't* in; story-level "here is the take you haven't seen"; explanations that survive
byte-parity tests; an eval framework whose primary metric is the reader's information diet, not
their engagement.
**Build first:** Tier 1, in the order listed — formalization and QueryContext unlock the rest;
feedback wiring and repetition decay are the largest reader-visible wins per line of code.
