# W1 — Openness (ε) Slider: Effectiveness Audit

> **Status:** audit complete · **Decision: DEFERRED** (no implementation) · **Date:** 2026-07-14
> **Branch:** `claude/sleepy-gates-oecof1`
> **Scope:** the `politicalOpenness` setting and its effect on the served recommendation feed —
> whether the control actually changes what a reader sees. Grounded in the repository implementation
> plus two faithful measurements (a serving-path ε-sweep and an RWE-B algorithm-level probe).
> **Companion:** this is the deep-dive behind weakness **W1** in
> `docs/RECOMMENDATION_ALGORITHM_DESIGN_REVIEW.md`.

## Decision

The audit found that **ε (political openness) does not change the served feed for any measured
reader profile**. Two fixes were compared — **I2** (make the slider honest) and **I1** (re-map
openness to a lever that actually moves the feed). **Neither is being implemented yet.** This review
is retained as documentation; implementation is deferred until the remaining low-risk improvements
are done and the product has been evaluated more thoroughly. **No code, algorithm, contract, or
behavior changes accompany this document.**

## Headline finding

The openness slider is **inert on the served feed for every reader profile measured** — not merely
for "bridge-rich" readers as the design review's one-liner suggested. It is neutralized by **two
independent mechanisms** that hit **two different reader populations**:

- **Centered / sideless readers** → inert by a **mathematical property** of RWE-B (they have no
  bridges, so ε scales every item uniformly and cancels under ranking).
- **Sided readers** → their RWE-B *raw* ranking **is** ε-responsive, but the **implementation**
  (`_slice_select`'s cross-cutting-first re-grouping) discards exactly the effect ε produces, so the
  *served* feed is unchanged.

A corollary: **widening the slider's ε range would not help** either population — so the naïve
"tune the numbers" non-fix is futile.

## What the slider controls

`politicalOpenness` (0–100, default 50) maps piecewise-linearly to RWE-B `epsilon`:

- `_OPENNESS_EPSILON = (0.70, 0.90, 0.97)` for slider 0 / 50 / 100 — `examples/api_server.py:350`.
- ε is consumed by **RWE-B only** — `examples/api_server.py:1213` (`if strategy == "rwe-b" and
  "epsilon" in params`). RWE-D (`beta`) and Adaptive do not read it.
- Therefore, in the default `6/4/4` blend, the slider can only ever reshape the **6 rwe-b columns**;
  the 4 rwe-d + 4 adaptive columns are ε-invariant by construction.

## 1–2. When the feed changes, and for whom (measured)

**Method.** Seed measured readers of distinct profiles into a spectrum catalog (6 outlets across
lean ∈ {−2,−1,0,+1,+2}, 3 topics), then drive the **real serving path** with
`params={"epsilon": e}` for `e ∈ {0.20, 0.50, 0.70, 0.90, 0.97}` (the product range is 0.70–0.97),
via `rec_sandbox.evaluate()`. Record the ordered served feed for both the rwe-b-only strategy
(ε fully in play) and the default blend; count **distinct feeds** across the sweep.

| reader profile | mean lean / side | rwe-b-only feed | blended feed |
|---|---|---|---|
| echo-left (strongly left) | −1.50 / −1 | **1 distinct** (no change) | **1 distinct** |
| mild-left (net −0.5) | −0.38 / −1 | **1 distinct** | **1 distinct** |
| balanced (sideless) | 0.00 / 0 | **1 distinct** | **1 distinct** |
| right (strongly right) | +1.50 / +1 | **1 distinct** | **1 distinct** |

**Result: the served feed changes for zero reader profiles across the entire ε range**, on both the
blend and the rwe-b-only strategy. This reproduces the originally observed "ε 0.2 vs 0.9 → identical
feed," and generalizes it to every profile tested.

## 3. Mathematical property vs implementation

The base score is `p_i·(1 − q_i) / (1 − Σ_j p_j·q_j)` — `rwe/random_walk.py:60` (`_score_batch`).
Three provable facts:

1. **The denominator `1/(1 − Σ p·q)` is a per-user scalar** → it multiplies every item equally →
   **cancels under `argsort`**. ε there never affects ranking. (True for all RWE strategies.)
2. **RWE-B sets `q_i = sim(u,i)` for a bridge (ε absent) and `q_i = ε` for a non-bridge**
   (`rwe/random_walk.py:285`, `_compute`). So ε multiplies **every non-bridge** by the same
   `(1 − ε)` and touches **no bridge**. ⇒ **ε can only shift the bridge↔non-bridge boundary; it can
   never reorder within the bridge set or within the non-bridge set.**
3. **A centered reader has no bridges.** `is_bridge` requires opposite sides of `center`
   (`rwe/random_walk.py:280`: `(θ − center)·(pos − center) < 0`); at `θ ≈ center` this is ≥ 0
   everywhere ⇒ every item is a non-bridge ⇒ uniform `(1 − ε)` scaling ⇒ **ε is mathematically
   inert.** (Production uses `max_distance = None` — `api_server.py:633` — so *every* opposite-side
   item is a bridge; the "no bridges" case is driven purely by the reader being centered.)

**Algorithm-level probe** (tiny co-readership graph + `RWEB`, no serving stack), ε = 0.20 vs 0.97:

| user | bridges | raw ranking @ ε=0.20 | raw ranking @ ε=0.97 | changes? |
|---|---|---|---|---|
| off-center (θ=−2) | items at +1, +2 | `[5, 1, 2, 0, 3, 4]` | `[5, 4, 1, 2, 0, 3]` | **yes** — the +1 bridge climbs 6th→2nd |
| centered (θ=0) | none | `[1, 2, 3, 4, 0, 5]` | `[1, 2, 3, 4, 0, 5]` | **no** |

So the **raw** RWE-B ranking *is* ε-responsive for an off-center reader (bridges float up as
non-bridges are suppressed by `(1−ε)`) — but that never reaches the served feed, because:

4. **`_slice_select` re-groups cross-cutting-first** for a sided reader (`api_server.py:1284` →
   `:1236`). It partitions the admitted list into `cross` (bridges) then `same`, discarding the
   raw interleaving that ε controls. Within `cross` order is ε-absent, within `same` order is
   uniform-in-ε ⇒ the **served** slice is ε-invariant. Bridge-saturation (`|cross| ≥ 6`) makes it
   doubly so.

**Conclusion:** it is **both** — a mathematical property (centered readers; ε only ever moves the
bridge/non-bridge boundary) **and** an implementation choice (the cross-first slice neutralizes the
one case where the math leaves ε room to act). The two together close every path from the slider to
the served feed.

## 4. Fix comparison (for a future decision)

### I2 — make the slider honest (minimal)

Reflect the limited reach in the copy/affordance (or gate it where inert), and/or freeze it as a
truthful no-op.

- **Impact:** removes a trust failure; **zero** feed change.
- **Risk:** near-zero — a UI/settings-copy change; no algorithm, `evaluate()`, REPORT CONTRACT v1,
  or JSON touch.
- **Validation:** byte-identity guardrail unaffected; a test asserting openness params → identical
  feed (documents the proven truth); settings/string tests.
- **Cost:** ~hours. **Downside:** gives up on the feature.

### I1 — re-map openness to a lever that moves the feed

Two viable targets, given the evidence:

- **I1(a) openness → rwe-b blend budget** (more openness ⇒ more of the `6/4/4` bridging slots). This
  is the honest meaning of "openness," and **W5 already proved it visibly moves the feed**
  (cross-cutting count ≈ the rwe-b budget). Orchestration-only — no algorithm change.
  - **Risk:** makes the blend plan **per-request**, touching the centralized `DEFAULT_BLEND_PLAN`
    and its **3-way parity** (`api_server` ↔ `rec_explain` ↔ `audit_story_coverage`) — the explain
    observer must thread the same per-request budget or explanations desync. Medium surface. It is
    *user-controlled*, so it is **not** the reader-adaptive policy declined in W5, but it is
    adjacent and deserves a conscious call.
  - **Validation:** `rec_sandbox --compare` across openness settings must show the feed changing
    (cross-cutting rising monotonically); determinism per setting; explain/served parity re-pinned;
    byte-identity of `evaluate()` per fixed params; full regression suite.
- **I1(b) openness → RWE-B `max_distance`** (currently `None`). An algorithm input like ε is today,
  so localized — but semantics are awkward ("how ideologically far a bridge may be"), reach is
  catalog-dependent, and *tightening* it **reduces** cross-cutting (the opposite of "openness").
  Weak fit.

## Recommendation (deferred, not acted on)

ε is the wrong lever, and no tuning of it will fix the slider — so this is a **fork, not a tweak**:

- Truthful, low-risk control now → **I2**.
- Genuine, visible control → **I1(a)** (openness → rwe-b budget), the only option the evidence shows
  will actually move the served feed, at the cost of re-opening the blend-plan surface (W5) and its
  parity tests.

Suggested sequencing when this is picked back up: **I2 first, I1(a) as a scoped follow-up.** Until
then, no change ships.

## Reproduction

Both measurements are offline and deterministic:

1. **Serving-path ε-sweep** — seed measured readers of varied lean into a spectrum catalog; for each
   `epsilon ∈ {0.20, 0.50, 0.70, 0.90, 0.97}` call
   `rec_sandbox.evaluate(store, {"readers": [{"kind": "user", "id": uid}], "questions": ["feed"],
   "strategies": [s], "params": [{"epsilon": e}]})` for `s ∈ {"rwe-b", None}`; compare the ordered
   `served` article ids. Expect **1 distinct feed** per (reader, strategy).
2. **Algorithm probe** — build a small `rwe.graph.FeedbackGraph` + `rwe.random_walk.RWEB` with known
   `user_positions` / `item_positions`; compare `np.argsort(-rweb.scores([u])[0])` at ε=0.20 vs 0.97
   for an off-center user (ranking changes) and a centered user (ranking identical);
   `rweb.is_bridge([u])` shows the centered user has no bridges.
