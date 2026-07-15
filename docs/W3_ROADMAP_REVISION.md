# W3 — Article-Level Viewpoint: Canonical Roadmap Revision

> **Status:** this is the **canonical explanation of W3 going forward.** It supersedes the W3 section of
> `docs/PRE_PRODUCTION_RECOMMENDATION_ROADMAP.md` (which now carries the R1 deferral banner) and
> consolidates the evidence from the recommendation design reviews and the lean-axis investigation.
> **Reads with:** `docs/REGISTER_VALIDATION_EXPERIMENT.md` (the gate for one W3 track),
> `docs/PRE_PRODUCTION_RECOMMENDATION_ROADMAP.md` (W1/W2/W8), `docs/TODO.md` (the lean-axis log),
> `docs/RECOMMENDATION_ALGORITHM_DESIGN_REVIEW.md` (the original W1–W8 / I1–I11 review).
> **Invariants:** REPORT CONTRACT v1 (schema), determinism, explain↔served parity, and the product
> philosophy — **transparent, explainable, viewpoint-aware, user-steerable**.

---

## 1. Executive summary

**Original W3:** "ideology is outlet-level and coarse" (`RECOMMENDATION_ALGORITHM_DESIGN_REVIEW.md:57`);
proposed fix I10 — confidence-gated **article-level lean** via `classify_lean.py`.

**What we now know:** article-level *lean from text* is **not recoverable to production quality** — the
repo has measured it exhaustively and the outlet registry beats it ~4× (§3). So **W3 is not one thing.**
It splits into a **deferred research problem** (accurate article-level lean) and a **serviceable
pre-production need** (don't mis-place non-political content; distinguish opinion from news within an
outlet; express viewpoint at the story level) that can be met with **reliable or cheaply-testable
signals that route around the failed classifier.**

**Revised W3 = four tracks:**

| Track | What | Signal | Gated? | Pre-production? |
|---|---|---|---|---|
| **W3-Core** | Accurate article-level *lean* | text classifier / new science | — | **No — deferred research** |
| **W3-Lite/A — Political-mask** | Keep non-political content off the ideology axis | `looks_political` (exists) | no | **Yes** |
| **W3-Lite/B — Story-level viewpoint** | Viewpoint from *relative outlet coverage* of a story | outlet lean + story clustering (both exist) | no | **Yes** |
| **W3-Lite/C — Register-gated extremity** | Opinion placed more extreme than news within an outlet | `classify_register` | **yes** → `REGISTER_VALIDATION_EXPERIMENT.md` | **Conditional** |

---

## 2. Why article-level lean classification was removed from the pre-production roadmap

The removal is **evidence-driven**, and — importantly — the repo had *already* reached the same
conclusion before the roadmap proposed I10; the roadmap's R1 revision corrected that oversight.

**The evidence (all article-level, from text):**

| Attempt | Result | Source |
|---|---|---|
| politicalBiasBERT, headline (Qbias, in-distribution) | **κ = 0.007**, ~20% acc, collapses to centre | `TODO.md:126–129` |
| politicalBiasBERT, body 256 tok | **κ = 0.001** | `TODO.md:129–133` |
| premsa, full body (best text result) | **Spearman ~0.22** | `TODO.md:136–139` |
| Two-BERT agreement, n=2,955, exact L/C/R | **κ = 0.14**; side-only κ 0.575 | `lean_agreement.py`; `TODO.md:103–113` |
| Ensemble vs human gold | **no gain** (n=40 underpowered) | `TODO.md:96–102` |
| LLM (Gemini, n=120), headline | **Spearman −0.28** (negative) | `TODO.md:152–156` |
| text-lean vs human | **~0.27** ("weak, model-sensitive proxy") | `PRODUCT_SIMULATION.md:79` |
| **Outlet lookup** vs AllSides gold | **κ = 0.84 / side-only 1.000 / Spearman 0.918** | `TODO.md:140–141` |

**And two structural facts about the failed design (I10):**
- `classify_lean` is **not in the production path** — production lean is the outlet registry only
  (`examples/ingest.py:6,410,429`), so deferring it changes **no current behaviour**.
- I10's `confidence` (top-2 softmax margin, `classify_lean.py:62`) measures **self-certainty, not
  accuracy**; it is validated only for **aggregate** health-report down-weighting
  (`HEALTH_REPORT_PLAN.md:154–158`), never per-article decisions. Confidence-gated shrinkage would move
  the *most* where the model is *most confidently wrong* — the worst case.

**Conclusion:** the outlet registry is the trusted anchor; text-lean cannot improve on it per-article.
W3-Core is **deferred research**, not a pre-production deliverable.

## 3. The reframe — W3's *need* is not "estimate lean"

W3's motivating example, "a NYT sports piece and a NYT op-ed both `−1`," decomposes into needs that
**do not require an accurate article lean**:

1. **Sports on the ideology axis** → a **political-mask** problem. RWE-B already admits *political-only*
   candidates (`api_server.py:1260`), so a sports piece surfacing as a bridge is a `looks_political`
   completeness bug, not a lean bug. → **W3-Lite/A**.
2. **Op-ed indistinguishable from news within an outlet** → a **register** (genre) problem, not lean.
   → **W3-Lite/C** (gated on validation).
3. **Expressing a story's viewpoint mix** → *relative outlet coverage*, computable from **outlet lean +
   clustering** with no text-lean at all. → **W3-Lite/B**.

Each routes around the signal that failed and uses inputs that are either proven-reliable (outlet lean
κ 0.84, political mask) or cheaply testable (register).

---

## 4. Revised strategy

### 4.1 W3-Core — accurate article-level lean (DEFERRED RESEARCH)

- **Goal:** a trustworthy per-article ideological *position* from content.
- **Why deferred:** the evidence (§2) shows text is near-chance and ~4× below the outlet; ensemble and
  LLM both failed; closing it needs a **new full-text + human-gold corpus** and arguably new science —
  a long-context test isn't even runnable on Qbias's short excerpts (`TODO.md:216–218`).
- **What would un-defer it:** a validated article-level signal reaching, say, **κ ≥ 0.6 vs human gold**
  on the production corpus — from a better model on full text, a purpose-built gold set, or a
  multi-signal fusion. Until such a number exists, W3-Core stays out of the pre-production plan.
- **Explainability:** any future W3-Core signal must remain interpretable (a position on the lean axis
  with visible provenance), never an opaque embedding — the product explains on this axis.

### 4.2 W3-Lite/B — Story-level viewpoint aggregation (PRE-PRODUCTION, UN-GATED) — *lead track*

- **How it works:** on the existing story clustering (C5 story-match; `audit_story_coverage.py`; the
  story index), compute a **story's viewpoint distribution from the known outlet leans of its covering
  articles** ("this story: 5 left outlets, 1 right"). Surface the **minority-viewpoint** outlet's
  coverage as the cross-cutting bridge.
- **Why it improves W3:** it delivers a *new, reliable* viewpoint signal — **relative coverage** —
  using **only** outlet lean (Spearman 0.918) + clustering. No text-lean, no unvalidated dependency.
- **Explainability:** high — "most coverage of this story leans left; here is the right-leaning take."
- **Complexity:** Med (clustering exists; add the coverage-distribution aggregation + bridge selection).
- **Contracts:** golden *values* shift where the feed changes; schema/parity/determinism preserved.

### 4.3 W3-Lite/A — Political-mask improvements (PRE-PRODUCTION, UN-GATED) — *cheapest*

- **How it works:** verify and tighten `looks_political` (`examples/ingest.py:65`) so non-political
  content (sports, lifestyle, promos) is not admitted to the ideological bridge slice (already
  political-only, `api_server.py:1260`). Measure the false-political rate on the live catalog.
- **Why it improves W3:** removes the *sports half* of the motivating example at the correct layer —
  **exclusion, not lean estimation.**
- **Explainability:** high — a non-political article simply isn't treated as a viewpoint.
- **Complexity:** Low.

### 4.4 W3-Lite/C — Register-gated within-outlet extremity (CONDITIONAL on the experiment)

- **How it works:** keep the outlet prior (κ 0.84) as the anchor; use **register** (`classify_register.py`
  — zero-shot NLI, already computed and wired into the report, `api_server.py:472,534`) to modulate
  *magnitude* within an outlet — an **opinion** piece placed more extreme than the outlet's **news**
  piece. Monotone adjustment around the anchor.
- **Why it improves W3:** fixes the *op-ed half* **without estimating lean** — register is a *genre*
  task, categorically different from (and plausibly easier than) ideology-from-text.
- **The gate:** register accuracy is **unvalidated** and headline-trained ("treat the score as
  approximate"). **Ship this only if `docs/REGISTER_VALIDATION_EXPERIMENT.md` returns GO or
  CONDITIONAL-GO.** On NO-GO, W3-Lite is A + B only.
- **Explainability:** high — the `opinion`/`news` enum already exists (`api_server.py:205`); "opinion
  piece from a left outlet → stronger left" is human-legible.
- **Complexity:** Low–Med (register already produced; add a monotone extremity map).
- **Honest magnitude:** register changes bridge *magnitude/reach*, not *side* (the outlet sets the
  side), so the effect is a **sharpening**, not a transformation.

---

## 5. Decision history

| Stage | What we believed | What we discovered / proved | Why the direction changed |
|---|---|---|---|
| Design review | W3 = "ideology is coarse"; fix with article-level lean (I10) | — | Intuitive: outlets are coarse |
| Roadmap v0 | The classifier "already exists and is validated" → integration, not research | **Wrong** — only its *confidence* is validated (aggregate down-weight); its article-level *positions* are not | Conflated "classifier exists" with "classifier is accurate" |
| **R1** | Article-level lean is unreliable; defer W3 | κ 0.14 exact, ~0.27 vs human, outlet 4× better (`TODO.md`, `lean_agreement.py`, `PRODUCT_SIMULATION.md`); repo had already chosen outlet-first (`TODO.md:204`); `classify_lean` not in production (`ingest.py:429`) | Evidence is decisive: text→article-lean is a dead end |
| **This revision** | "Defer W3" over-generalised — W3's *need* ≠ the failed signal | W3 decomposes into political-mask + story-level viewpoint + register-gated extremity, all on reliable/testable signals; bridges already political-only (`api_server.py:1260`); register is a *genre* task (`classify_register.py`) | There **is** a transparent pre-production path — just not article-level lean |

## 6. Rationale

- **Do no harm to the trusted anchor.** Outlet lean is the validated signal (κ 0.84); nothing here
  degrades it — W3-Lite *adds* around it or *excludes* from it.
- **Route around the failure, don't fight it.** Every retained track uses a signal that is proven
  (outlet, political mask) or falsifiable cheaply (register), not the one measured near-chance.
- **Gate the one uncertain dependency.** Register is the only unproven input, so it is the only gated
  track, behind a pre-registered experiment with a number on the same κ scale that condemned lean.
- **Preserve the philosophy.** Every track is human-explainable; none introduces an opaque estimator.

## 7. Risks

- **Story-level (B):** clustering errors and sparse stories → mitigate with a minimum-coverage floor;
  fall back to outlet lean when a story is a singleton.
- **Political-mask (A):** over-exclusion (dropping genuinely political content) as well as under-
  exclusion → measure both error directions on the live catalog before tightening.
- **Register (C):** the whole risk is register accuracy → the §experiment gate; on CONDITIONAL-GO,
  restrict to high-confidence extremes and per-outlet passers. Also: *accuracy ≠ improvement* — a
  passing gate authorises building the adjustment; a **separate rec_sandbox downstream check** (bridges
  change defensibly, parity + contract-v1 held) authorises defaulting it on.
- **Scope creep** toward W3-Core → hold the line: no per-article lean ships without a validated ≥0.6 signal.
- **Contract risk:** all tracks shift golden *values* where feeds change; **schema, determinism, and
  explain↔served parity are invariant** and are the acceptance bar.

## 8. Implementation order

```
  W3-Lite/A  political-mask completeness ─┐  (cheapest, un-gated, reliable)
                                          ├─► both proceed pre-production now
  W3-Lite/B  story-level viewpoint ───────┘  (lead track — new reliable signal)

  REGISTER VALIDATION EXPERIMENT  (docs/REGISTER_VALIDATION_EXPERIMENT.md)
        │  GO / CONDITIONAL-GO ─────────────► W3-Lite/C  register-gated extremity
        │  NO-GO ───────────────────────────► stop C; A + B stand
        ▼
  W3-Core  (accurate article-level lean) ── DEFERRED until a validated ≥0.6 signal exists
```

1. **A + B now** (un-gated, reliable, no new science). B is the lead track — biggest new signal.
2. **Register experiment** in parallel (cheap; the harness exists — `validate_lean --raters`).
3. **C** only on GO / CONDITIONAL-GO; then a downstream rec_sandbox check before default-on.
4. **W3-Core** stays deferred research.

**Relative to the master roadmap (`PRE_PRODUCTION_RECOMMENDATION_ROADMAP.md`):** W3 remains *off* the
W1 → W2 → W8 critical path; A + B and the register experiment are independent, low-risk work that can
run alongside W1/W2 without blocking them.

## 9. Expected impact

| Track | Recommendation quality | Information Health | Explainability | Effort | Certainty |
|---|:--:|:--:|:--:|:--:|:--:|
| **A — political-mask** | ▲ (no false bridges) | ▲ (cleaner viewpoint metrics) | ▲ (no bogus leans) | Low | High (reliable signal) |
| **B — story-level viewpoint** | ▲▲ (real relative-coverage bridges) | ▲▲ (new viewpoint signal) | ▲▲ (coverage is legible) | Med | High (outlet lean) |
| **C — register extremity** | ▲ (sharper bridge magnitude) | ▲ (opinion-aware) | ▲ (opinion/news legible) | Low–Med | **Gated** (register unproven) |
| **Core — article lean** | (▲▲ if ever solved) | (▲▲) | (must stay interpretable) | High | Deferred |

*(○ neutral · ▲/▲▲ positive.)* **Net:** the un-gated A + B deliver most of W3's realistic value now;
C adds a modest, transparent sharpening if register validates; Core's larger prize stays behind real
science.

## 10. Open research questions

1. **Register accuracy** on the production text modality — the immediate, decisive unknown
   (`REGISTER_VALIDATION_EXPERIMENT.md`).
2. **Is register *enough*?** Even if accurate, does within-outlet extremity change bridges in a way
   users find *better* (the downstream rec_sandbox question)?
3. **Can article-level lean ever reach κ ≥ 0.6** from content — full-text models, purpose-built gold,
   or multi-signal fusion — without sacrificing interpretability? (W3-Core un-defer condition.)
4. **Multi-dimensional viewpoint** — lean is 1-D; framing / epistemic / geographic axes are the deeper
   ceiling on the diversity mission. Must stay interpretable. (Long-horizon.)
5. **Story-clustering quality at scale** — does relative-coverage viewpoint hold as story volume and
   outlet diversity grow?

---

## 11. Canonical status

This document is the **single source of truth for W3**. When W3 is referenced elsewhere:
- "W3 deferred" means **W3-Core** (article-level lean) — deferred research.
- "W3 progressing" means **W3-Lite/A + /B** — reliable, pre-production, un-gated.
- "W3-C pending" means **register-gated extremity** — awaiting the validation experiment's GO.

Any change to W3's direction updates this file and the decision history above; the master roadmap's W3
section points here.
