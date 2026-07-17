# Recommendation Engine — Engineering Status

> **Status:** living engineering record · **Last updated:** 2026-07-14 · **Branch of record:** `claude/sleepy-gates-oecof1`
> **Audience:** future maintainers and reviewers of the recommendation subsystem (RWE-B / RWE-D /
> Adaptive, the blend, ideology, the story slot, and the offline evaluation tool).
> **Reads with:** `docs/RECOMMENDATION_ALGORITHM_DESIGN_REVIEW.md` (the full W1–W8 / I1–I11 review,
> commit `8a1964e`), `docs/W1_OPENNESS_SLIDER_AUDIT.md` (commit `7611c84`),
> `docs/SYSTEM_ARCHITECTURE_GUIDE.md`, and `docs/RECOMMENDATION_EVALUATION_ENGINE.md`.

## Why this document exists

The design review identified eight weaknesses (W1–W8) and eleven candidate improvements (I1–I11).
We then worked them **one at a time**, and deliberately shipped only some. This document is the
durable record of **what we decided and — more importantly — why**, so a future engineer can tell,
for any given weakness, whether silence means "done," "deliberately deferred," or "not yet
understood." It is not a changelog; the commits are. It is the *reasoning* behind the commits.

If you are about to "finally fix" one of the deferred items, read that item's section first. In
most cases the deferral is not laziness — it encodes a judgment about cost, risk, or missing data
that is still true until stated otherwise.

## The principles that drove every decision

These are the lenses we applied to all eight weaknesses. They are the real content of this
document; the per-item sections are just these principles applied.

1. **Audit before implementing.** Every weakness got a code-grounded trace *and* a faithful
   measurement through the real pipeline (`rec_sandbox.evaluate()`, the same engine the product
   serves) **before** any code was written. Decisions were driven by measured behavior, not
   intuition or the design-review one-liner. Several conclusions changed once measured (W1 turned
   out to be inert for *every* reader, not just "bridge-rich" ones; W5's allocation turned out to be
   an undecidable trade-off, not a mis-tuning).

2. **Separate a defect from a policy.** This is the single most important distinction in the whole
   effort. A **defect** is behavior that is objectively wrong against its own stated intent — an
   undated article that never ages out, a slider that cannot move the feed, a label printed twice, a
   silent article drop. Defects get fixed. A **policy / values choice** is a question with no
   correct answer absent data — how to split the blend, how much off-topic serendipity is good,
   how aggressively to dose cross-cutting per user. Policies are **deferred until there is ground
   truth to justify one answer over another.** Dressing a policy change up as a "fix" is how a
   recommender quietly drifts away from being explainable and measurable.

3. **Smallest contract-preserving change.** When we did fix something, we used the least-invasive
   change that resolved the defect, and we did **not** touch the protected surfaces (below) unless
   the audit proved they were the source. W6 is the model: the freshness bug lived in candidate
   generation, so the fix lived there — the RWE algorithms and the report contract were never opened.

4. **Hold the invariants.** `evaluate()`, **REPORT CONTRACT v1**, the recommendation JSON,
   determinism, and the RWE algorithms (`rwe/`) were held constant across all shipped work. The
   byte-identity guardrail, the explain-vs-served parity tests, and the determinism regression suite
   exist precisely to make an accidental violation fail loudly. (See *Protected invariants*.)

5. **Defer what needs data we don't have.** Anything that needs a real audience — a co-readership
   graph, an engagement/cross-cutting-reception signal, a population to tune a per-reader policy
   against — was deferred. One persisted demo reader is not a population. Building a control you
   cannot validate is worse than not having it.

6. **Instrument before you fix.** Where a problem has *no current impact* but *real future risk*
   (W4), we invested in cheaply **seeing** it first, so that when it becomes real we act on numbers
   rather than discovering it in production.

## At a glance

| # | Weakness | Nature | Disposition | Where |
|---|----------|--------|-------------|-------|
| W7 | Redundant "Cross-cutting" + "Bridge Article" labels | Defect (cosmetic) | **Implemented** | `d619539` |
| W6 | Discovery can surface stale / undated-immortal items | Defect (candidacy) | **Implemented (Tier A)**; topic-aware deferred | `482fdd9` |
| W5 | Static 6/4/4 blend, triple-hardcoded | Maintainability defect + policy | **Implemented (Tier 0)**; reader-adaptive deferred | `6b17424` |
| W4 | Unknown outlets silently dropped | Latent risk (0% today) | **Observability implemented**; fix deferred | `96c33ce` |
| W1 | "Openness" (ε) slider does nothing | Defect (inert control) | **Implemented** — openness re-mapped to the RWE-B bridge-slot budget (I1) | `8be5e55` |
| W2 | Adaptive isn't adaptive (`exposure=0.5`) | Incomplete feature | **Implemented (wiring)** — measured reception, shrunk toward the neutral prior; the dosing *retune* stays traffic-gated | `3f29c7f` |
| W3 | Ideology is outlet-level & coarse | Design limitation | **Deferred** (major research) | — |
| W8 | Collaborative base is synthetic | Stage-of-product | **Deferred** (needs traffic) | — |

---

## Implemented

### W7 — Redundant bridge label (commit `d619539`)

**What was wrong.** The evaluation tool's feed printed two "Why" chips that say the same thing for a
cross-cutting RWE-B pick — `Cross-cutting` and `Bridge Article` — on top of the `[RWE-B]` tag that
already means "bridging." For a reader with a side, *every* rwe-b card is cross-cutting, so the pair
was pure duplication.

**What we did.** Collapsed it to the single, clearer `Cross-cutting` chip via one pure renderer
helper, `rec_sandbox._why_labels`. A non-cross-cutting bridge still reads `Bridge Article`, and
distinct reasons (Story Match, New Publisher) still show alongside Cross-cutting.

**Why this way.** It is purely cosmetic — the helper reads the report's own `crossCutting` flag and
`explanation.type` and recomputes nothing, so the JSON is byte-identical. A zero-risk clarity win
has no reason to be deferred, and no reason to be anything larger than a renderer change. This is
principle 2 (a clear defect) meeting principle 3 (the smallest possible change).

### W6 Tier A — Undated articles now age out (commit `482fdd9`)

**What was wrong.** Discovery (RWE-D) is inverse-degree and topic-blind, and separately, the
freshness gate had a hole: an **undated** article (no `publishedAt`) fell back to `fetchedAt` for its
candidacy age, and `fetchedAt` is refreshed on every re-poll. So a re-polled undated article's age
never grew and it stayed a recommendation candidate **forever** — the audited "Way Day 2023"
stale-commerce pick. Two independent gaps: *staleness* (candidate generation) and *topic-blindness*
(ranking/orchestration).

**What we did.** Anchored candidacy age to the stable first-seen `created_at` (stamped once at
ingest, never refreshed): `publishedAt` → `createdAt` → `fetchedAt`. An undated article now ages out
`RWE_FEED_MAX_AGE_DAYS` (60) after **first discovery**, while a genuinely new undated article stays
fresh. The change is confined to `corpus_health.fresh_articles` (candidate generation); the default
`_published` order used by health metrics is untouched, so no reported metric shifts.

**Why *only* Tier A, and why this exact fix.** Undated-immortality is a **defect** — an article
whose eligibility ignores its true age is objectively wrong — so it was fixed. We rejected the blunt
alternative (`RWE_FEED_REQUIRE_DATED`, which drops *all* undated content) because it also discards
genuinely fresh undated articles; anchoring to `createdAt` fixes the "indefinitely eligible" problem
precisely while preserving fresh-from-discovery coverage. We **deferred** the topic side (Tier B,
= I6) because "how much off-topic discovery is good?" is a **policy** question (principle 2): it
shifts feeds, forces a golden-value change, and needs evaluation, not a bug fix. The metadata for it
already exists (`mind.categories`), so Tier B is unblocked whenever it is justified.

> Nature-of-change note for reviewers: **W6 is the one shipped item that intentionally changes
> recommendation behavior** — specifically *which articles are eligible candidates*. It changes no
> ranking, scoring, algorithm, `evaluate()`, contract, or JSON. That is the boundary we held.

### W5 Tier 0 — The blend plan is now a single source of truth (commit `6b17424`)

**What was wrong.** The default blend `[("rwe-b",6),("rwe-d",4),("adaptive",4)]` — a founding
default from `10f80aa` with no recorded rationale — was hardcoded in **three** places
(`api_server`, `rec_explain`, `audit_story_coverage`), and only two were pinned equal by a parity
test. The audit's copy could drift silently and make explanations disagree with the served feed.

**What we did.** Lifted it into one named `api_server.DEFAULT_BLEND_PLAN`, imported by the other two,
with a comment recording the rationale the measurement established: **the rwe-b budget is the
guaranteed cross-cutting-cards-per-feed floor** (each rwe-b column is an opposite-viewpoint pick for
a sided reader), while the rwe-d/adaptive columns buy source diversity. Value unchanged → feed
byte-identical.

**Why we centralized but did not re-tune, and did not make it reader-adaptive.** The measurement
(via `rec_sandbox`) showed the allocation is a genuine lever — more rwe-b mechanically means more
cross-cutting, at the cost of fewer distinct publishers — but the two axes trade off and **no ground
truth exists to prove one split is "better"** without engagement data. Re-tuning the value would
therefore be a values judgment disguised as a fix (principle 2). The triple-hardcoding, by contrast,
was a real **maintainability defect** with a provably-safe fix, so we did that and left the value
exactly where it was. A **reader-adaptive** blend (I7) is a speculative *policy* that would need a
reader population and an engagement signal to tune and validate — deferred under principle 5.

### W4 — Unknown-outlet observability (commit `96c33ce`)

**What was wrong.** An outlet the registry doesn't know scores `lean = NaN` and is dropped from the
recommendation corpus (at `simulate_users.catalog_from_qbias` and `rwe.mind.recommender_inputs`).
The article still ingests and is searchable; it simply never becomes a candidate. Measured impact on
the **curated** feeds today: **0%** (all 9 configured outlets resolve). The risk is entirely
forward-looking — it bites only when sources broaden.

**What we did.** Added five additive, read-only diagnostics: an ingest-time unknown-outlet counter
(+ per-outlet breakdown), a `corpus_health` `unknownOutlet` count/pct, a coverage CLI
(`examples/outlet_coverage.py`) that ranks unknown outlets by article volume with the operational
impact, a registry linter (`outlet_registry.lint_registry`), and a warn-only
`RWE_CORPUS_MAX_UNKNOWN_OUTLET_PERCENT` threshold. No recommendation behavior changed.

**Why observability instead of the fix — and why not a classifier.** Fixing a 0%-impact problem is
solving a non-problem; but the drop was previously **invisible**, so we could not even tell when it
was starting to happen. Principle 6: instrument first, cheaply, so the eventual fix is driven by
numbers. The actual fix — expanding `outlet_registry.csv` (I3) — is a *data* task that the coverage
CLI now supports (it produces the frequency-ranked worklist), deferred until real ingestion shows a
non-trivial unknown rate. We explicitly **rejected the classifier fallback (I4)**: inferring lean
from article text injects model error into a signal the whole product treats as ground truth, and
the registry is deliberately the trusted anchor. Broadening coverage should add *known* facts (rows
in the registry), not *estimated* ones in the serving path.

---

## Deferred, with the analysis on record

### W1 — The "openness" (ε) slider is inert (audited; commit `7611c84`, no code)

**Finding.** `politicalOpenness` maps to RWE-B `epsilon`, and **ε does not change the served feed
for any reader profile measured** — a stronger result than the design review's "inert for
bridge-rich readers." The full analysis (math + two measurements) lives in
`docs/W1_OPENNESS_SLIDER_AUDIT.md`. In brief: ε cancels in the score denominator; it is absent within
the bridge set and a uniform scale within the non-bridge set, so it can only move the
bridge↔non-bridge boundary; a **centered** reader has no bridges (mathematically inert), and for a
**sided** reader the raw ranking *is* ε-responsive but `_slice_select`'s cross-cutting-first
re-grouping discards that effect before it reaches the feed.

**Why deferred.** ε is the *wrong lever* — no tuning of it helps, so there is no small fix. The real
options are a **fork**: I2 (make the slider honest — zero behavior change) or I1 (re-map "openness"
onto the rwe-b blend budget — the only lever the evidence shows will actually move the feed, but it
re-opens the W5 blend surface and its parity tests). Choosing and building either is worth doing
*deliberately*, after the low-risk items are done and the product has been evaluated — not as a
reflex to a trust-only control. Deferring here is principle 2 (don't tune a broken lever) plus a
sequencing call.

> **Update (2026-07-16):** the fork was resolved and **shipped** — openness now drives the RWE-B
> **bridge-slot budget** (4/6/8 via `blend_plan_for`; I1, commit `8be5e55`), which also makes the
> slider honest by construction (I2 absorbed). The audit above is retained as the motivating record.

### W2 — Adaptive isn't adaptive (`exposure = 0.5` constant)

**Finding.** `AdaptiveRWEB` is served with a constant exposure, so per-user dosing does nothing in
production. The intended fix (I8) wires exposure to a *measured cross-cutting-reception* signal —
i.e., whether the reader actually opens the opposing-viewpoint articles they're shown.

**Why deferred.** That signal only exists with real traffic; with one demo reader there is nothing
to tune the policy against, so we would be shipping a control we cannot validate (principle 5).
Compounding this, Adaptive's exposure feeds an ε-like mechanism, so its *visible* effect is capped
by the same inertness proven in W1 — meaning even a correctly-wired exposure would move the served
feed less than expected until W1's slice interaction is addressed. Wiring W2 is therefore both
premature (no data) and partially blocked (by W1). It should follow real engagement data.

> **Update (2026-07-16):** the **wiring shipped** (I8, commit `3f29c7f`) — the reader's measured
> cross-cutting reception, shrunk toward the neutral prior (`shrunk_exposure`, κ=10, cold-start-safe),
> now feeds `AdaptiveRWEB` per reader; the W1 re-map removed the blocking slice interaction. The
> *dosing policy* (κ / anchor tuning) remains traffic-gated exactly as argued above.

### W3 — Ideology is outlet-level and coarse

**Finding.** Every article inherits its outlet's single AllSides house lean (55 outlets, five
discrete values via `outlet_registry.csv`). A NYT op-ed and a NYT sports piece are placed
identically. The real fix (I10) is **article-level** lean — a per-article placement, likely from a
text classifier.

**Why deferred.** This is the deepest limitation but also the highest-cost: multi-week research that
reshapes bridging (bridges are defined by ideological position) and forces a large golden-value
migration. Sequencing it ahead of the cheap wins, and ahead of any usage data that would justify the
investment, would be poor engineering economics. The outlet-level proxy is coarse but **principled
and deterministic** today, and the system is honest about it (the report language frames lean as a
directional outlet signal, not a per-article measurement). Revisit when article-level precision is
demonstrably the bottleneck.

### W8 — The collaborative base is synthetic

**Finding.** The co-readership graph the walk runs over is a simulated population (`RWE_N_USERS`),
not real readers. The fix (I11) seeds the graph with real reads and learned ideological positions at
scale (`fit_ideology`).

**Why deferred.** This is not a defect to fix — it is a **stage-of-product reality**. Collaborative
relevance requires an audience; with one demo reader there is no co-readership to learn from.
Building the θ-fitting pipeline before there is data to feed it is premature (principle 5). It
resolves naturally as usage accrues; the honest framing today is that rankings reflect a
deterministic *simulated* population and are engine behavior, not audience prediction.

---

## The improvement catalog (I1–I11), mapped

| ID | Improvement | Weakness | Disposition & reasoning |
|----|-------------|----------|--------------------------|
| I1 | Re-map "openness" to a real lever (rwe-b budget / `max_distance`) | W1 | **Shipped** (`8be5e55`) — openness drives the rwe-b slot budget via `blend_plan_for`; parity goldens updated with it |
| I2 | Make the openness slider honest | W1 | **Resolved by I1** — the slider now moves the served feed, honest by construction |
| I3 | Expand `outlet_registry.csv` | W4 | Deferred (data task) — the W4 coverage CLI produces the worklist; do it when the unknown rate is non-trivial |
| I4 | Text-classifier fallback for unknown lean | W3/W4 | **Rejected** — injects model error into the trusted lean signal; registry stays the anchor |
| I5 | Freshness / recency shaping of discovery | W6 | **Partially shipped** — the undated-immortal defect is fixed (Tier A); broader recency *boosting* remains open |
| I6 | Topic-aware discovery | W6 | Deferred (Tier B) — a serendipity *policy*, not a defect |
| I7 | Reader-adaptive blend | W5 | Deferred — speculative policy; needs an engagement signal |
| I8 | Wire Adaptive `exposure` to reception signal | W2 | **Shipped (wiring)** (`3f29c7f`) — per-reader shrunk reception feeds `AdaptiveRWEB`; the dosing-policy *retune* remains traffic-gated |
| I9 | De-duplicate the bridge labels | W7 | **Shipped** (`d619539`) |
| I10 | Article-level lean | W3 | Deferred — major research; large golden shift |
| I11 | Real graph + learned ideology at scale | W8 | Deferred — needs traffic |

---

## Protected invariants (do not break these silently)

Everything above was done without violating the following. If you change the recommender, keep them
green — they are the contract that makes this engine explainable and testable.

- **REPORT CONTRACT v1 / `evaluate()`.** The evaluation library's output schema is frozen. The
  guardrail `test_cli_json_is_byte_identical_to_the_library_report` proves the CLI renders exactly
  what the library returns — presentation changes (like W7) must never leak into the JSON.
- **Determinism.** A rebuild must reproduce the same feed order and the same explanations
  (`examples/validate_recs.py`; the regression suite). Anything that introduces run-to-run variance
  is a regression.
- **explain == served parity.** The explanation observer (`rec_explain`) must reproduce the served
  feed exactly (`tests/test_rec_explain.py`). The blend plan is now single-sourced
  (`api_server.DEFAULT_BLEND_PLAN`) specifically to keep the three consumers from drifting — if you
  change the blend, change the constant, and let the parity tests confirm.
- **The RWE algorithms (`rwe/`).** Untouched by all of the above. A weakness whose true source is an
  algorithm (e.g. W3) is a research effort, not a patch; do not reach into `rwe/` for a product tweak.
- **Additive, read-only diagnostics.** The W4 observability is all additive and never influences
  serving. Keep new diagnostics on the diagnostic surfaces (ingest stats, `corpus_health`,
  `corpus_validation`, the coverage CLI) — not on the recommendation contract.

## How to re-verify (offline, deterministic)

Each shipped item has a fast, offline check; the QA walkthrough these came from is reproducible from
the repo root:

- **W4** — `python examples/outlet_coverage.py --lint` (registry well-formed) and a `scan` over a
  catalog with unknown outlets (ranked worklist + operational impact). Tests:
  `tests/test_outlet_coverage.py`, `tests/test_outlet_registry.py`.
- **W5** — `rec_explain.BLEND_PLAN is api_server.DEFAULT_BLEND_PLAN` (single source) and
  `tests/test_rec_explain.py -k served` (parity). Exactly one `("rwe-b", 6)` literal exists.
- **W6** — build an undated article with a back-dated `created_at` but fresh `fetched_at`; confirm
  `corpus_refresh.build_candidate_for` excludes it while a fresh-undated one is kept. Tests:
  `tests/test_freshness.py`.
- **W7** — `rec_sandbox._why_labels` collapses the cross-cutting bridge case to `['Cross-cutting']`;
  a live rwe-b render shows zero "Bridge Article" chips. Test: `tests/test_rec_sandbox.py`.

Full suite: `python -m pytest -q` (1201 passing as of 2026-07-16, `4bc0863`).

## Maintenance note

When a deferred item is picked up, move it out of the relevant "Deferred" section into "Implemented"
with the same structure — *what was wrong, what we did, why this way* — and update the at-a-glance
table and the improvement catalog. Keep the reasoning, not just the status: the next maintainer will
need to know why, exactly as you did.
