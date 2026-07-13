# Recommendation Algorithm — Design Review

> **Status:** internal design review · **Date:** 2026-07-13 · **Branch:** `claude/sleepy-gates-oecof1`
> **Scope:** the recommendation subsystem (RWE-B / RWE-D / Adaptive, the blend, ideology, the story
> slot), grounded in the repository implementation and observable behaviour in the Recommendation
> Evaluation Engine (`examples/rec_sandbox.py`).

## How this review was traced

_Captured verbatim from the tracing pass that produced the review — read directly from the
implementation before anything was written:_

> This deserves a proper read of the parts I haven't yet traced. Let me pull the remaining core pieces — the RWE base walk, RWED, Adaptive, the blend assembly, cross-cutting, and story clustering — before I write anything.
>
> The base signal is a k-hop random walk on the **co-readership graph** (`_score_batch`: `p = item_distribution`), with erasure `Q` overlaying bridging (RWEB) or long-tail (RWED) — and RWED ≡ inverse-degree^β reweighting. Now the last pieces: Adaptive, cross-cutting, the blend/slot assembly, and the story slot.
>
> `_cross_of`: cross-cutting ⇔ political ∧ reader has a side ∧ `sign(lean) == -user_side` ∧ `|lean| ≥ 0.5`. Now the blend assembly and Adaptive:
>
> The blend is a **fixed slot budget**: `[("rwe-b", 6), ("rwe-d", 4), ("adaptive", 4)]`, deduped and truncated (line 1360–1361). Let me read Adaptive and the story slot to finish the picture:
>
> Confirmed: `exposure = np.full(..., 0.5)` — Adaptive runs with a **constant neutral exposure** unless a probe CSV is supplied (line 613). Last piece — the story slot:
>
> I've now traced the full path — base walk, the three strategies, the blend, cross-cutting, ideology, and stories.

Here's the design review, grounded in the implementation.

## 1. Architecture (how it actually works)

**Base signal — collaborative, not ideological.** Every strategy starts from `BaseRecommender._score_batch` (`rwe/random_walk.py:60`): `p = graph.item_distribution(user_ids, k=3)` — a 3-hop random walk over the **co-readership `FeedbackGraph`** (user↔item bipartite). That's a P³-style collaborative-filtering relevance signal. Erasure `Q` then reshapes it: `retained = p·(1−Q)`, renormalized by `1/(1−Σ p·Q)`. **Ideology enters only through `Q`, never as the base relevance.**

- **RWE-D** (`random_walk.py:192`): `Q^D = 1 − deg^(−β)` ⇒ `retained = p·deg^(−β)`. Identical to `RP3Beta`. Suppresses high-degree (popular) items → promotes long-tail. **β is the discovery knob** (default 0.5).
- **RWE-B** (`random_walk.py:214`, `_compute:285`): `Q^B = sim(u,i)` for **bridge** items, `ε` otherwise. `sim = 1 − |pos_i − θ_u|/range`; a bridge is opposite side of `center` within `max_distance` (default `None` = any opposite-side). So `retained_bridge = p·(1−sim) = p·|pos_i−θ|/range` → **reachable AND far-opposite** items win. Non-bridge → `p·(1−ε)`.
- **Adaptive** (`rwe/satisfaction.py:304`): RWE-B with per-user `ε = 0.5 + exposure·0.45`. **But serving sets `exposure = 0.5` constant** (`api_server.py:613`) unless a probe CSV exists → in production it's RWE-B at a fixed `ε≈0.725`; the measured-satisfaction input is **not wired**.
- **Blend** (`api_server.py:1360`): a **fixed plan** `[("rwe-b",6),("rwe-d",4),("adaptive",4)]`, deduped first-come, truncated. A single-strategy request becomes `[(strategy,12)]`.
- **Bridge selection / cross-cutting**: `_slice_admits` (`:1209`) lets **only political items** into the rwe-b slice (non-political backfilled); `_slice_select` (`:1225`) orders **cross-cutting first**, then same-side. Cross-cutting itself is `_cross_of` (`:146`): `political ∧ user_side≠0 ∧ sign(lean)=−user_side ∧ |lean|≥0.5`.
- **β and ε in ranking**: β genuinely reorders the discovery slice (inverse-degree). **ε cannot reorder bridges** — bridge scores use `sim` (ε-independent), and the `1/(1−Σp·Q)` factor is a per-user constant that cancels under `argsort`. ε only sets the non-bridge floor.
- **Reader ideology θ** (`rwe/mind.py:366` `recommender_inputs`): `user_positions` from `fit_ideology`, else `user_positions_from_clicks` (mean of clicked items' leans); `user_side = sign(mean_lean)` (`api_server.py:1354`).
- **Article ideology ψ**: the **outlet's AllSides house lean** (`outlet_registry.csv`, ~55 outlets, discrete `{−2,−1,0,1,2}`), inherited by every article. **Unknown outlet → NaN → the item is dropped** from recommender inputs (`recommender_inputs:381`).
- **Story clustering** (`story_service` + `personalize._apply_story_slot:277`): a *post-ranking* insertion of ≤1 unread sibling from a **different publisher** covering a validated multi-publisher story the reader read, displacing the lowest-priority card. It's gated so it's P1-explainable by construction; it does **not** flow through the RWE walk.

**One caveat that colors everything:** for the demo/beta corpus the `FeedbackGraph` is built over the **deterministic simulated population** (~479 synthetic users; the report note says so). Until real traffic exists, the collaborative base `p` is synthetic.

## 2. Strengths (design is genuinely good here)

- **Transparent + auditable end to end.** Ideology is a published lookup, bridges are pure geometry, explanations are re-derived by `evidence_resolver.validate()` and pinned to the served card by a *shared* `_cross_of` (`:146`) so a card and its "Why" can't disagree. This is rare and valuable.
- **Deterministic + regression-guarded.** Same store+spec → byte-identical report; the regression suite asserts structural invariants over REPORT CONTRACT v1. You can evaluate offline before shipping — that's the whole `rec_sandbox` value.
- **Honest about ignorance.** Unknown outlets → NaN (excluded, not guessed); freshness/candidacy gates inherited by the story slot; Adaptive copy literally says "measured exposure not wired."
- **Sound separation of concerns.** Relevance (co-read graph) is decoupled from diversity (erasure overlays). Bridging is a principled "reachable-but-opposite" weak-tie, not a naive "show the other side."
- **Cheap, offline, no external dependency** in the serving path.

## 3. Real weaknesses (implementation- or behavior-supported only)

| # | Weakness | Evidence |
|---|---|---|
| W1 | **Openness (ε) is inert for bridge-rich readers** — cannot reorder the visible bridge slice | Your ε 0.2 vs 0.9 sweep → `identical=True`; math of `_compute` + scale-invariant `argsort` |
| W2 | **Adaptive isn't adaptive in production** — `exposure=0.5` constant | `api_server.py:613`; params echo "measured exposure not wired" |
| W3 | **Ideology is outlet-level & coarse** — every article inherits one of 5 discrete house leans | `ingest.py`→`outlet_registry` (~55 outlets); a NYT sports piece and a NYT op-ed both `−1` |
| W4 | **Unknown outlets silently dropped** — shrinks the recommendable/bridgeable corpus | `recommender_inputs:381`; live RSS will hit many unrated outlets |
| W5 | **Static blend** — 6/4/4 regardless of the reader's state (echo-chambered vs already-diverse) | `api_server.py:1360` |
| W6 | **Discovery is topic-blind & can surface stale/low-relevance items** | RWE-D is pure inverse-degree; your feed's "Way Day 2023" (stale commerce) as a `New Publisher` pick |
| W7 | **Redundant explanation labels** — every rwe-b card shows both "Cross-cutting" and "Bridge Article" | `_slice_select` cross-first + `_cross_of`; observable in your feed (all 6 identical) |
| W8 | **Collaborative base is synthetic** for the beta corpus | report note; `RWE_N_USERS` simulated population |

## 4. Evaluation findings — classified

| Finding | Classification | Why |
|---|---|---|
| ε sweep → identical feed | **Expected behavior** (surfaced as a **product/UI** problem) | ε provably can't reorder bridges; but a slider that does nothing misleads users |
| Bridge saturation (top-6 all Right) | **Expected behavior** | left reader + `right=94` items ⇒ slice fills with genuine bridges by design |
| Outlet-level ideology | **Algorithmic/design limitation** | one axis, one value per outlet, by construction |
| Unknown outlets excluded | **Implementation limitation** | a deliberate NaN-drop; fixable in preprocessing without touching RWE |
| Recommendation diversity | **Expected + product limitation** | diversity is real (cross-cutting, new publishers) but the *blend mix* is static |
| Long-tail discovery quality | **Implementation limitation** | RWE-D degree-only; no topic/freshness shaping of the candidate set |
| Explanation quality | **Strength** (minor **product** redundancy) | grounded + parity-pinned; only the double-label is cosmetic |

## 5. Proposed improvements

Every item stays inside the current architecture (no generic ML/LLM swap — where I suggest ML it's the repo's **own** QBias-validated classifier, justified by W3/W4). "Algo?" = changes an RWE algorithm vs preprocessing/orchestration only. "Contracts?" = preserves REPORT CONTRACT v1 + explainability + determinism (golden *values* shift where feeds change, but schema/invariants hold).

| ID | Improvement | Problem it solves | Benefit | Eng | Research risk | Algo? | Contracts |
|----|-------------|-------------------|---------|-----|---------------|-------|-----------|
| I1 | **Make "openness" map to something visible** (blend slot budget or RWE-B `max_distance`) | W1 no-op slider | slider actually reshapes the feed | Low–Med | Low–Med | `max_distance`=algo input; slot budget=orchestration | preserved |
| I2 | **Honest slider UI** (if a control can't move *this* reader, say so) | W1 trust | no false affordance | Low | Low | No (UI) | preserved |
| I3 | **Expand the outlet registry** (more outlets/aliases) | W4 drops | more of the catalog recommendable | Low | Low | No (data) | preserved |
| I4 | **Wire `classify_lean.py` as a fallback lean for unknown outlets** (registry stays the trusted anchor) | W3/W4 | coverage + article-level nuance for un-rated sources | Med | Med (classifier error) | No (preprocessing/scoring) | preserved (lean is an input) |
| I5 | **Freshness/recency shaping of the RWE-D candidate set** (tighten existing C4 gate) | W6 stale picks | fresher, less embarrassing discovery | Low–Med | Low | No (candidate preprocessing) | preserved |
| I6 | **Topic-aware discovery** (constrain/boost RWE-D toward reader/adjacent topics) | W6 topic tunnel | relevant long-tail | Med | Med | slice filter (algo-adjacent) | preserved |
| I7 | **Reader-state-adaptive blend** (echo-chambered → more bridge slots; diverse → more discovery) | W5 static mix | feed matches need | Med | Med | No (orchestration) | preserved |
| I8 | **Wire Adaptive `exposure` to a measured cross-cutting-reception signal** | W2 | Adaptive becomes real per-user dosing | Med–High | Med | No (input wiring; `AdaptiveRWEB` already consumes it) | preserved |
| I9 | **De-duplicate rwe-b explanation labels; fill category gaps via `classify_topic`** | W7 + metadata gaps | cleaner cards | Low | Low | No (UI/preprocessing) | preserved |
| I10 | **Article-level lean (news vs opinion)** | W3 core | true per-article placement | High | High | preprocessing, but reshapes bridging | preserved schema; big golden shift |
| I11 | **Seed the graph with real reads + learned positions at scale** (`fit_ideology`) | W8 | real collaborative relevance | High | High | data + θ pipeline | preserved |

## 6. Prioritization

- **Quick wins (Low effort):** I2 (honest slider), I3 (registry expansion), I5 (discovery freshness), I9 (label/category cleanup).
- **Medium-term:** I1 (re-map openness), I4 (classifier fallback), I6 (topic-aware discovery), I7 (adaptive blend), I8 (wire exposure).
- **Major research:** I10 (article-level lean), I11 (real graph + learned ideology at scale). Multi-dimensional health (tone/factuality beyond one axis) sits here too.

## 7. Estimates

| ID | Rec-quality Δ | UX Δ | Effort |
|----|----|----|----|
| I2 | none | **High** (trust) | ~hours |
| I3 | Low–Med (coverage) | Low | ~hours–1 day |
| I5 | Med | Med | ~1–2 days |
| I9 | none | Low–Med | ~hours |
| I1 | Low (visible, not better) | Med–High | ~2–4 days |
| I4 | **Med–High** (coverage + nuance) | Med | ~1 week + validation |
| I6 | Med–High | Med | ~1 week |
| I7 | Med | Med–High | ~3–5 days |
| I8 | Low–Med (ε caveat limits visible gain) | Low–Med | ~1–2 weeks |
| I10 | **High** | Med | multi-week research |
| I11 | **High** (real relevance) | High | needs traffic + weeks |

## 8. Launch decision — public beta today

**Would I launch the current algorithm? Yes — as a clearly-scoped beta.** The core promises hold: bridging is principled, explanations are grounded and can't contradict their cards, everything is deterministic and regression-guarded, and the system is honest where it's uncertain. That's a defensible, differentiated beta.

**Launch blockers (fix or gate before shipping):**
1. **I2 — the openness slider must not lie.** A user-facing control that provably does nothing for most readers is a trust failure. Cheapest fix: make the copy honest, or re-map it (I1). *Blocks the slider, not the feed.*
2. **Measure & bound W4 (unknown-outlet drop) on the *live* catalog.** If a large share of ingested articles are unrated, the feed silently narrows to ~55 outlets and skews. Pre-launch: instrument the NaN-drop rate; if high, ship I3 (and plan I4). *Conditional blocker — decide with the number, which `rec_sandbox`/§10 can produce.*
3. **I5 — freshness on discovery.** A stale "2023 Wayfair sale" in a breaking-news feed is a credibility hit for a product about *information quality*. Low cost, high embarrassment-avoidance.

**Explicitly OK to ship as-is (communicate, don't fix):**
- Bridge saturation and ε-inertness are **correct behavior** — just don't over-promise the slider.
- Adaptive at neutral exposure is fine **because the copy already says so** — but don't market "adaptive personalization."
- Synthetic collaborative base is acceptable cold-start — the report is honest that rankings are "engine behavior, not audience prediction."

**Post-launch:** I4, I6, I7, I8 (medium-term quality), then I10/I11 (the real research bets). None require abandoning the RWE design — they're coverage, orchestration, and signal-wiring around a sound core.

---

_This is grounded entirely in the modules cited. Suggested next step: actually **measure** the live
unknown-outlet drop rate (blocker #2) with the sandbox so the launch call is data-backed rather than
a judgment._
