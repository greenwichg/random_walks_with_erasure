# Reader Position Spectrum — Audit & Presentation-Only Spec

> **Status:** canonical design review + implementation spec for exposing a **continuous reader
> position** (Strong Left → Center → Strong Right) as a **reading-pattern** indicator. **Documentation
> only — no code, no behaviour change.**
> **Verdict in one line:** the continuous position **already exists** and already drives ranking; a band
> is a **deterministic derivation of an existing signal, not new learning** — so this is a *framing and
> presentation* decision, and it must be shipped as an **observed reading pattern, never a political
> identity**.
> **Reads with:** `docs/W3_ROADMAP_REVISION.md` (outlet-first lean), `docs/HEALTH_REPORT.md`
> (viewpoint metrics), `docs/PRE_PRODUCTION_RECOMMENDATION_ROADMAP.md`.
> **Invariants preserved:** REPORT CONTRACT v1 (eval-engine schema), recommendation behaviour,
> determinism, explain↔served parity, and the product philosophy (transparent, non-judgmental,
> user-steerable).

---

## Part 1 — Audit

### 1.1 How the reader's position is represented today

| Stage | Representation | Continuous? | Code |
|---|---|:--:|---|
| Click-mean proxy (production `theta`) | read-weighted mean of known-lean item positions | **Yes** (float in `[-2,+2]`) | `rwe/mind.py:286` |
| Ideal-point model (eval/research) | latent `theta` on a standardised scale, oriented to lean | **Yes** | `rwe/mind.py:322,355` |
| Health-report `mean_lean` | confidence-weighted mean lean per reader; **exposed** in the user report | **Yes** | `health_report.py:261,424` |
| RWE-B `similarity` (ranking) | `1 − |pos−theta|/range` — weights bridge erasure | **Yes** | `rwe/random_walk.py:270` |
| RWE-B `is_bridge` (side test) | `sign(theta−center) ≠ sign(pos−center)`; `max_distance` off in prod | **No — sign** | `rwe/random_walk.py:280` |
| `user_side` (cross-cutting fact + slice) | `np.sign(mean_lean)` → −1/0/+1 | **No — sign** | `api_server.py:1365` |
| `viewpoint_shares` (report L/C/R) | buckets at `LEAN_TAU = 0.5` | **No — 3-way** | `health_report.py:58,128` |
| `readerPoliticalProfile` (exposed) | left/right **diet shares** | **No — shares** | `web/lib/rec-presentation.ts:72–78` |
| Input scale (`outlet_lean.csv`) | **−2..+2, 5-point** (strong/lean/center/lean/strong at outlet level) | quantized | `examples/data/outlet_lean.csv` |

### 1.2 Continuous, or collapsed? — **both, and that is the finding**

The model **represents** the reader as a **continuous float** (`mean_lean`/`theta`) with real resolution
(a read-weighted mean over a 5-point scale takes many values in `[-2,+2]`), and the recommender **uses**
it in ranking (`similarity`). But **every consumer that could express a spectrum collapses it** to a
sign or a 3-way. A Strong-Left reader (`mean_lean ≈ −1.8`) and a Lean-Left reader (`≈ −0.4`) are
**distinct numbers today** rendered **identically** as "left." The spectrum information exists; nothing
surfaces it.

### 1.3 Does exposing it require new learning? — **No.**

Bands are **deterministic cutpoints on the existing `mean_lean`**. `fit_ideology` offers a
behaviour-learned alternative to the click-mean, but banding needs neither it nor any new model.

### 1.4 Interaction with the W3 outlet-first redesign

Post-W3, `item_positions` stay **outlet-level** (article-level lean deferred). This **helps** here: the
reader position rests on the **validated** outlet signal (κ 0.84 / Spearman 0.918), not the failed
article classifier (κ 0.14). Averaging over many reads yields a continuous aggregate even from a
5-point input. The **only** limit: outlet lean can't distinguish *within-outlet* variation (NYT opinion
vs NYT news both `−1`), so the position is honestly "average lean of the **outlets** you read," not "of
the **articles**" — exactly what W3-Lite/C (register extremity) would later refine *if* it validates.
**Lack of article-level lean coarsens per-item resolution but does not prevent a trustworthy continuous
position.**

### 1.5 Would bands improve the five axes?

The distinction that governs everything: **presentation bands** (a label derived from `mean_lean`) vs
**behavioural use** of the magnitude (changing `is_bridge`/`user_side`).

| Axis | Presentation-only (this spec) | Behavioural (magnitude drives bridging) |
|---|---|---|
| Recommendation quality | **No change** (ranking already uses continuous `theta`) | Possible, but a rec-behaviour change |
| Bridge selection | No change (side/fact stay sign-based) | Changes which/how-far bridges — **couples to W1**; defer |
| Information Health metrics | Richer *presentation* of the same `mean_lean` | n/a |
| Personalization | None beyond what `theta` already does | Marginal |
| Explainability | **Improves** — *if framed as diet, not identity* | Harder to explain |

**Net:** as presentation, bands are orthogonal to recommendation quality; their only real upside is
**explainability / self-awareness**. Behavioural coupling is a separate, W1-adjacent change and is
**out of scope** here.

### 1.6 The identity risk (the actual crux)

Labeling a person "Strong Right/Left" is inferentially **false in kind** (the signal is the average lean
of *outlets read*, not the person's politics — and the more a reader reads across the aisle, *the
behaviour this product exists to encourage*, the more wrong the label), **against the product's thesis**
(a viewpoint-broadening tool assigning political identities), **sensitive** (asserting political
identity from behaviour), and **fragile** at outlet-5-point × read-count resolution. The repo already
models the right discipline: `rec_explain.match_band` notes its "strong/good" is *ranking language, not
[identity]* (`rec_explain.py:15`). **We apply the same discipline: reading-pattern, never identity.**

### 1.7 Audit verdict

- **Capability:** the architecture **already distinguishes a continuous spectrum**; no new learning is
  required to expose it.
- **Product:** ship it **only** as an **observed reading-pattern position** (diet framing,
  confidence-gated, reversible), presented **with** the existing L/C/R diet shares — which are the more
  honest primary. Keep it **presentation-only**; do **not** couple it to bridging in v1.

---

## Part 2 — Smallest deterministic presentation-only spec

### 2.1 Design principles (what this spec is and is not)

- **A pure derivation of `mean_lean`**, gated by read volume + confidence, rendered as reading-pattern
  copy — nothing more.
- **Touches no recommender code:** no change to `theta` consumption, `is_bridge`, `user_side`,
  `max_distance`, ranking, or the blend.
- **Out of REPORT CONTRACT v1:** derive in the **presentation layer** from fields the report already
  emits; if a value must be added, it goes on the **product report** (`/api/report`), never inside the
  eval engine's pinned `evaluate()` JSON.
- **Deterministic:** same `(mean_lean, political_reads, viewpoint_confidence)` → same band, always.

### 2.2 Inputs (all already computed)

| Input | Source | Notes |
|---|---|---|
| `mean_lean` ∈ `[-2,+2]` (or `None`) | `health_report.py:261,424` | the continuous position |
| `political_reads` (count) | report's political-reader signal (`:363`) | volume gate |
| `viewpoint_confidence` ∈ `[0,1]` | `health_report.py:411,423` | reliability gate |
| L/C/R shares | `viewpoint_shares` (`:122`) | shown **beside** the band (honest primary) |

If `mean_lean is None` or `political_reads` is below the floor → the **insufficient** state (§2.5); no
band is asserted.

### 2.3 Cutpoints (symmetric; aligned to `LEAN_TAU=0.5` and the 5-point outlet scale)

Let `m = mean_lean`, `a = |m|`, side = "Left" if `m<0` else "Right". Center is `a < 0.5` — **identical
to the report's existing center definition** (`LEAN_TAU`), so the band and the metrics agree.

**Default — 5 bands** (the honest maximum for typical readers):

| Condition | Band |
|---|---|
| `a < 0.5` | **Center** |
| `0.5 ≤ a < 1.5` | **Left / Right** |
| `a ≥ 1.5` | **Strong Left / Strong Right** |

**7 bands** — unlocked **only** at high volume+confidence (§2.4); adds the innermost "Lean" tier.
Cutpoints at `0.5, 1.0, 1.5` sit exactly on the 5-point outlet grid:

| Condition | Band |
|---|---|
| `a < 0.5` | **Center** |
| `0.5 ≤ a < 1.0` | **Lean Left / Lean Right** |
| `1.0 ≤ a < 1.5` | **Left / Right** |
| `a ≥ 1.5` | **Strong Left / Strong Right** |

The 5-band map is exactly the 7-band map with the `1.0` cut removed — one coarsening, no separate logic.

### 2.4 Confidence / volume gate (deterministic thresholds — tunable policy)

| Gate | Rule (defaults) | Effect |
|---|---|---|
| **G1 — show anything** | `political_reads ≥ 5` **and** `mean_lean` finite | else → **insufficient** state |
| **G2 — granularity** | 5-band by default; **7-band only if** `political_reads ≥ 15` **and** `viewpoint_confidence ≥ 0.5` | avoids over-resolving sparse diets |
| **G3 — extreme caution** | the **"Strong"** label requires `political_reads ≥ 10`; otherwise clamp `a ≥ 1.5` down to **Left/Right** | prevents "Strong X" from a handful of reads |

Thresholds are documented defaults, not magic; they are deterministic and unit-testable (§2.7).

### 2.5 Reading-pattern copy (exact strings — diet, reversible, time-bounded)

Always render the band **with** the provenance caption and the L/C/R shares. Never second-person about
the *person*; always about the *reading*.

**Band headlines:**
- Strong Left — "Your recent reading leans **strongly** toward left-of-center outlets."
- Left — "Your recent reading leans toward left-of-center outlets."
- Lean Left — "Your recent reading leans **slightly** left-of-center."
- Center — "Your recent reading draws fairly **evenly across** the spectrum."
- Lean Right — "Your recent reading leans **slightly** right-of-center."
- Right — "Your recent reading leans toward right-of-center outlets."
- Strong Right — "Your recent reading leans **strongly** toward right-of-center outlets."

**Provenance caption (always shown):**
> "Based on the average lean of the **outlets you've read recently** — a reading pattern, **not a
> political label**. It shifts as your reading does."

**Insufficient state (G1 fails):**
> "Keep reading — we'll show where your recent reading sits once we've seen a bit more."

**Supporting line (always):** the existing L/C/R shares, e.g. "Recently: 60% left · 30% center · 20%
right" — the more honest primary; the band is a summary of it.

All strings are catalog keys (i18n), consistent with the repo's externalized-copy convention.

### 2.6 Where it lives + explainability

- **Compute in `web/lib/rec-presentation.ts`** (beside `readerPoliticalProfile`) as a pure function of
  the already-served `mean_lean` / `political_reads` / `viewpoint_confidence`. Zero contract risk.
- **Explainability:** the band cites the same `mean_lean` the report already shows, plus the shares it
  summarizes, plus the provenance caption — fully traceable, no opaque step.

### 2.7 Determinism & validation (documentation of the test plan)

- **Boundary table test:** `m ∈ {−2.0, −1.5, −1.0, −0.5, −0.3, 0.0, +0.3, +0.5, +1.0, +1.5, +2.0}` →
  expected band, at both granularities.
- **Symmetry test:** band(`−m`) is the mirror of band(`+m`) for all `m` (Center maps to Center).
- **Gate tests:** `political_reads < 5` → insufficient; `a ≥ 1.5` with `political_reads = 8` → Left/Right
  (not Strong); 7-band only when `reads ≥ 15 ∧ conf ≥ 0.5`.
- **Invariance assertions:** the recommender's served feed and REPORT CONTRACT v1 `evaluate()` JSON are
  **byte-identical** with the band feature on vs off (it is presentation-only) — the guardrail that
  proves no behaviour leaked.

### 2.8 What this spec must NOT do

- Not change ranking, bridging, `user_side`, `is_bridge`, `max_distance`, or the blend.
- Not enter the eval engine's REPORT CONTRACT v1 JSON.
- Not assert political identity, and not show a "Strong" band below the volume gate.
- Not couple the band to recommendations (behavioural use is deferred, §3).

---

## Part 3 — Trade-offs, risks, recommendation

**Trade-offs**
- Cheap & safe technically (presentation-only, no learning, no contract change) **vs.** costly in trust
  terms if framed as identity — hence the strict diet framing.
- A single band is legible **vs.** the L/C/R shares are more honest — so ship **both**, shares primary.
- Presentation bands (no rec change) **vs.** behavioural magnitude (rec change, W1-coupled) — the latter
  is out of scope.

**Risks**
- **Primary risk is ethical/UX**, not technical: mislabeling cross-reading users, identity assertion,
  extreme-label overreach — mitigated by diet framing (§2.5) and the G3 gate.
- **Resolution overclaim** with 7 bands on a 5-point input — mitigated by G2 (7-band gated).
- **Scope creep** into behavioural coupling — explicitly deferred (§3).

**Recommendation**
1. **Feasible before production** as specified: a deterministic, presentation-only, reading-pattern band
   derived from the existing `mean_lean`, gated and framed as above.
2. **Preferred surface:** a **spectrum point + L/C/R shares + reading-pattern caption**, with the band as
   a soft one-line summary — **not** a standalone "Strong Left/Right" identity chip.
3. **Ship the 5-band default;** unlock 7-band only under G2. Keep it **out of the recommender and out of
   REPORT CONTRACT v1**.

**This should become part of the product only in the reading-pattern form.** A political-identity
taxonomy claims resolution the data doesn't robustly support and a stance the product shouldn't adopt;
the reading-pattern position advances the mission (self-awareness of one's own diet) at near-zero
technical risk.

---

## Part 4 — Deferred / open questions

1. **Behavioural coupling** (band → bridge distance / openness) — a real rec change that re-opens **W1**;
   deferred until W1's openness lever lands.
2. **`fit_ideology` position vs click-mean** — a behaviour-learned `theta` could replace the click-mean
   as the band's input at scale; needs the W8 real-graph work first.
3. **Multi-dimensional viewpoint** — lean is 1-D; framing/epistemic/geographic axes are the deeper
   ceiling (shared with W3-Core); must stay interpretable.
4. **Shares vs band** — a product-research question: do users find the band or the shares more useful
   and less presumptuous? Resolve with the same honesty bar, not aesthetics.
