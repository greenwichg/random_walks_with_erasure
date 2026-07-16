# Recommendation System — Architectural Assessment (read-only)

**Scope:** the recommendation subsystem as it exists today (RWE-B / RWE-D / Adaptive blend, ideology,
story slot, explainability, freshness, the offline evaluation engine, and the serving/deployment
layer). **W8 (the synthetic collaborative base) is excluded by instruction.** No code modified.
Every score is anchored to repository evidence; work volume is explicitly *not* a scoring input.

**Headline: 3.5 / 5 — a research-grade, beta-hardened system whose ceiling is set by three things, none
of them W8: no live validation, a coarse outlet-level ideology signal, and a single-writer store with no
rec-quality observability.** It is genuinely strong where most production recommenders are weak
(evaluation rigor, faithful explanations, measured anti-echo bridging) and genuinely unproven where
production systems must be strong (real traffic, scale, online quality). Detail below.

---

## Per-dimension scores

### 1. Recommendation relevance — **3 / 5**
- **Evidence.** A real offline evaluation harness with standard metrics — AUC, nDCG@k, hit@k,
  precision@k (`rwe/metrics.py`, `rwe/experiment.py:52-67`) — against real baselines (ItemKNN, BPR-MF,
  P3, RP³-β; `rwe/baselines.py`). Measured on real data: MIND-small (AUC .743; `docs/RESULTS.md`) and
  MovieLens-1M (AUC .918, hit@10 .127 beating P3 .094). RWE-D sits at **AUC parity** with RP³-β
  (.743 vs .744).
- **Gaps.** The product's diversifiers **deliberately trade accuracy** — RWE-D hit@10 is **.065 vs
  P3's .196** (`RESULTS.md` RQ2), a 3× drop, by design. More decisively: **there is no relevance
  evaluation on the live corpus.** Every accuracy number is on public proxy datasets (`eval_mind.py`,
  `eval_movielens.py`); the live RSS/qbias feed served to real readers is measured only *structurally*
  (`validate_recs.py`, contract regression), never for relevance/engagement. No CTR, dwell, or online
  metric exists.
- **To reach 5.** An online A/B with real engagement metrics on live traffic, and a tuned
  accuracy↔diversity frontier — proving the served feed is relevant to actual users, not just
  competitive on offline AUC against a proxy dataset.

### 2. Diversity / anti-echo-chamber — **4 / 5**
- **Evidence — the system's strongest, best-validated area.** RWE-D is the **strongest long-tail
  diversifier on every axis** (gini .708, coverage .996, surprisal 8.74, lowest avg item degree),
  robust across 7 seeds at AUC parity (`RESULTS.md` RQ2). Dedicated ideological-diversity metrics exist
  (`RecRange@k`, KS-test, `directed_shift`, `weighted_shift`; `rwe/metrics.py:211-300`). Bounded
  ideological bridging (RWE-B) is validated **on a behaviorally-validated axis** — Reddit Politosphere,
  where the left↔right axis is learned from endorsement behaviour (`lean_corr 0.57 ± 0.19`), giving
  `uw_shift 1.97 ± 0.04`, beating every baseline on 5/5 seeds (`RESULTS.md` RQ3). The product levers are
  wired: openness → RWE-B **bridge-slot budget** (`api_server.py:383` `blend_plan_for`, W1) and
  adaptive cross-cutting exposure (`personalize.py:179-194` → `shrunk_exposure`, W2).
- **Gaps.** The ideology axis that drives bridging **in production** is a coarse ~70-outlet AllSides
  proxy (`outlet_lean.csv`, self-described "illustrative"), *not* the validated Politosphere axis; the
  MIND text-lean axis is κ=0.14 noise (`classify_lean.py`). Bridging is validated on research data, not
  on the live product with real readers.
- **To reach 5.** Demonstrated echo-chamber reduction for **real users on the live product**, on a
  validated (behavioral or article-level) ideology axis — which is exactly the data W8 would supply, so
  4/5 is arguably the ceiling until then.

### 3. Explainability — **4 / 5**
- **Evidence — unusually rigorous.** A dedicated explanation subsystem (`rec_explain.py`,
  `evidence_resolver.py`) with per-recommendation trace, evidence, strategy contribution, and
  exclusions; a **REPORT CONTRACT v1** with **explain-vs-served parity tests** (byte-identity
  guardrail); a truthful serializer that *removed* a fabricated `healthImpact` field; share-backed
  reader facts that are parity-correct **by construction**. Determinism regression backs it. Most
  production recommenders ship **no** faithful explanation at all.
- **Gaps.** Explanations are **template/rule-based** — they name the strategy that placed an item
  (bridge / new-publisher / topic-continuity / story-match) but do **not** causally attribute the
  random-walk score itself; "confidence" is heuristic. No user-comprehension testing.
- **To reach 5.** Counterfactual/causal attribution tied to the actual walk contribution, plus evidence
  that users understand the explanations.

### 4. Engineering quality — **4 / 5**
- **Evidence.** 1201 passing tests across 92 files; determinism + explain/served parity + contract
  guardrails that fail loudly; clean separation of a **held-constant** pure algorithm package (`rwe/`)
  from the product layer (`examples/`); documented defect-vs-policy discipline and audit-before-implement
  (`RECOMMENDATION_ENGINE_STATUS.md`); smallest-contract-preserving-change practice (the freshness fix
  lived in candidacy, never touching the RWE algorithms or the contract).
- **Gaps.** **SQLite single-writer** + each ASGI worker builds its **own in-memory corpus**
  (`DEPLOYMENT.md:166-171`) — a real horizontal-scale ceiling. `examples/` mixes product code, one-off
  scripts, and demos (73 files). No load/perf test evidence. Minor doc/code drift (the STATUS doc's
  W1/W2 "deferred" verdicts are **superseded by the wired code** above).
- **To reach 5.** A horizontally-scalable store, perf/load tests, and product/script separation.

### 5. Production readiness — **3 / 5**
- **Evidence for.** Genuine hardening: fail-closed auth that **refuses to start** without
  `RWE_INTERNAL_SECRET` in production; per-scope rate limits and body limits (`413`); CORS locked in
  prod; refuses an ephemeral DB; backup tooling (`db_backup.py`); a corpus-validation eligibility gate
  and freshness gate; health reports (`DEPLOYMENT.md:121-175`).
- **Evidence against.** The live store is **empty in the audited environment** — the system has never
  run at real scale/traffic. My own `W3A_PRODUCTION_READINESS.md` recommended **shadow-first** (a ~2×
  political-magnitude shift unmeasured live; a split-brain corpus until re-scored). The C4.2 freshness
  fix just shipped with a **known open gap** (Guardian/Washington Times alpha-month URLs,
  `FRESHNESS_SOURCE_AUDIT.md`). **No online rec-quality observability, no A/B framework, no model-change
  rollback** beyond a DB backup. Personalization is unvalidated at population scale.
- **To reach 5.** Real traffic behind a flag with online quality + guardrail monitoring, a scale-proven
  store, a populated+validated corpus, and an A/B/rollback path for model changes.

### 6. Personalization — **3 / 5**
- **Evidence.** Per-user `PersonalModel` cached by `(reading_version, reception_version)` and rebuilt on
  each new read (`personalize.py:82-263`); reading-history-driven; **cold-start-safe** — an unmeasured
  reader gets the neutral population prior (`_reader_exposure` returns `None` → 0.5; `has_measured`
  gate); W2 shrinks the reader's exposure toward neutral until enough impressions accrue
  (`shrunk_exposure`, κ=10). Two live sliders (openness, strength).
- **Gaps.** Personalization is **built but unvalidated with a real population** — the STATUS doc is
  explicit that "one persisted demo reader is not a population," and the adaptive control's validation
  is deferred to real traffic. The per-user position is a click-mean heuristic; the collaborative base
  is synthetic (W8, excluded, but it *is* what personalization sits on).
- **To reach 5.** Demonstrated per-user lift on real traffic over a non-personalized control.

### 7. Freshness — **4 / 5**
- **Evidence.** A freshness gate on **both** corpus paths (default 60 d, `RWE_FEED_MAX_AGE_DAYS`);
  C4.1 anchors an undated article's age to its **stable first-seen** time (immortal-undated fix); C4.2
  adds a **URL-embedded-date** signal that excludes archived `/2023/…` and `-MM-DD-YY` live-blog URLs
  (24 new tests, before/after shadow report); real `publishedAt` is **never fabricated** for real
  articles (truthfulness tests). A full root-cause + per-source audit exists.
- **Gaps.** The source audit found **2 of 9 feeds** (Guardian, Washington Times) use an alpha-month URL
  the parser misses → they still fall back to feed date; no page-date extraction; unmeasured at live
  scale (empty store).
- **To reach 5.** The month-name parser extension (closes Guardian/WT), an optional page-date fallback,
  and live measurement.

### 8. Maintainability — **4 / 5**
- **Evidence.** ~60 design/audit docs; a *living decision record* that captures **why**, not just what
  (`RECOMMENDATION_ENGINE_STATUS.md`); `rwe/` vs `examples/` separation; guardrails that make an
  accidental contract/determinism violation fail loudly; `MATH.md`/`PAPER.md` grounding the algorithms;
  1201 tests as a refactor safety net.
- **Gaps.** `examples/` sprawl (product + scripts + demos intermixed); a **dual naming scheme**
  (design-review W1–W8 vs the productized W1/W2/W3A) that already caused doc/code drift; tight SQLite
  coupling in the store layer.
- **To reach 5.** Consolidate `examples/` (separate the shipped product from one-off tooling), reconcile
  the naming/docs with the code, and package the store behind an interface.

| # | Dimension | Score |
|---|---|---|
| 1 | Recommendation relevance | 3 / 5 |
| 2 | Diversity / anti-echo | 4 / 5 |
| 3 | Explainability | 4 / 5 |
| 4 | Engineering quality | 4 / 5 |
| 5 | Production readiness | 3 / 5 |
| 6 | Personalization | 3 / 5 |
| 7 | Freshness | 4 / 5 |
| 8 | Maintainability | 4 / 5 |

---

## A. Overall — **3.5 / 5**
Unweighted mean 3.6; held at **3.5** because the two lowest scores (relevance, production readiness) are
the two that gate a *launch*, and both are capped by the same root cause — **nothing has been validated
with real traffic.** This is a system that is excellent at the things you can prove offline and unproven
at the things you can only prove online. It is well above a prototype and below a shipped, scaled
product.

## B. vs a typical production news recommender
- **Where this system is *stronger*:** faithful, contract-tested explainability (most have none);
  *measured* ideological diversity and bridging as a first-class objective (most don't measure echo
  chambers at all); evaluation rigor + determinism + defect/policy discipline; honest limitation
  disclosure.
- **Where it is *weaker*:** scale (SQLite single-writer + per-worker corpus vs distributed stores and
  streaming updates); online experimentation + engagement optimization (absent here); operational
  observability of rec quality; content-embedding cold-start; freshness at high volume.
- **Net:** **below** a mature production system on operational maturity and scale; **above** it on
  transparency, diversity science, and evaluation discipline. It is a research-grade engine with beta
  hardening, not an operated product.

## C. vs state-of-the-art research systems
- SOTA neural news recommenders (NRMS/LSTUR and transformer/LLM/graph-neural families) **beat this
  system's accuracy** substantially on MIND — the RWE family is a **classical random-walk** approach
  (P3/RP³ lineage) plus an ideal-point model, not a deep content+behavior model.
- **But accuracy is not this system's claim.** Its contribution is **long-tail diversity + bounded
  ideological bridging**, honestly evaluated *with the axis caveat stated in the open* (`RESULTS.md`):
  the MIND ideology axis is a noisy proxy; the behavioral axis validates only moderately (lean_corr 0.57,
  range 0.13–0.82, seed/threshold-sensitive). That intellectual honesty is itself above the field norm.
- **Net:** **behind** SOTA on accuracy; a **distinct, honestly-evaluated** contribution on
  diversity/bridging; the ideology-axis validation is *moderate, not definitive*.

## D. Three biggest remaining weaknesses (excluding W8)
1. **No live/online validation of relevance or echo-chamber reduction.** Every number is offline on
   proxy datasets; the live product has never been measured with real users (empty store, no A/B, no
   online metrics). The core product claim is unproven *in situ*. **This is the single biggest gap.**
2. **Coarse, outlet-level ideology signal (design-review W3).** The axis the whole anti-echo thesis
   rests on is a ~70-outlet illustrative AllSides proxy in production; article-level lean is deferred
   (W3B); the text-lean axis is κ=0.14. The product's central mechanism runs on a coarse proxy, validated
   only on a *different* (Politosphere) axis.
3. **Scaling + operational ceiling.** SQLite single-writer, each worker holding its own in-memory
   corpus, and **no rec-quality observability / A/B / model-rollback**. Fine for a controlled beta; a
   blocker for GA at scale.

## E. Staff/Principal verdict — **Approve for a gated beta; block GA at scale.**
I would **approve a limited launch behind a feature flag, to a small real cohort, with monitoring** — the
engineering discipline, fail-closed auth, determinism/parity guardrails, and evaluation rigor clear that
bar, and the only way to close weakness #1 is *some* real traffic. I would **not approve GA** until four
concrete, mostly-operational conditions are met:
1. **Online quality + guardrail metrics exist** and a real cohort validates both relevance and
   echo-chamber reduction (closes D#1).
2. **The store is proven at target load** or replaced (closes D#3 scale).
3. **The freshness Guardian/WT gap is closed** and freshness is measured live (closes the one known open
   defect).
4. **The coarse-ideology limitation is disclosed to stakeholders** — the product markets echo-chamber
   reduction, and today that rests on a proxy axis (D#2); leadership must sign off on that with eyes open.

These are conditions, not a rejection: the architecture is sound and the discipline is real, so this is
"green-light a careful beta, gate GA on live evidence," not "send it back." What would make me reject
outright is absent here — there are no correctness landmines in the served path, the contract is
guarded, and the limitations are documented rather than hidden.

---

## Evidence / Engineering judgement / Speculation

**Evidence (verifiable in-repo):** the metric/experiment/baseline harness and its numbers
(`rwe/metrics.py`, `rwe/experiment.py`, `rwe/baselines.py`, `docs/RESULTS.md`); the wired product levers
(`api_server.py:383`, `personalize.py:179-194`); explainability + parity + contract machinery;
deployment hardening (`DEPLOYMENT.md`); the freshness gate + C4.2 fix + source audit; 1201 passing tests;
the honest engine-status and results docs; the empty live store.

**Engineering judgement (defensible inference):** the accuracy↔diversity posture is deliberate, not a
defect; SQLite + per-worker corpus is a genuine scale ceiling; template explanations are faithful but not
causal; "beta-ready, GA-gated" is the right operational reading; classical RWE trails neural SOTA on
accuracy. Scores 3–4 reflect "works and is tested, but unproven live / bounded by known gaps," not any
discount for scope.

**Speculation (genuinely uncertain):** live relevance/engagement of the served feed (needs traffic); the
real magnitude of echo-chamber reduction for actual readers; how far the coarse outlet-proxy axis
degrades bridging quality vs the validated behavioral axis; the store's actual breaking load. None of
these can be settled from the repository alone.

*Read-only assessment. No code was modified.*
