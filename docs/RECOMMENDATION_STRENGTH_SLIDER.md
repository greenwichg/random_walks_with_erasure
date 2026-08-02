# Why the Recommendation-strength slider barely moves political distance

**Scope:** the Settings slider "Recommendation strength — how aggressively we diversify away from
your usual diet" (0–100, UI labels Gentle/Balanced/Bold), and its effect on the political
distance |article lean − reader position| of the served recommendations.
**Status:** investigation complete; the smallest recommended fix (copy only) is
**implemented** — see "Implemented" at the end. Engine behaviour untouched.
**Date:** 2026-08-02.

The observation that started it (production screenshots): with the slider at Bold, the explain
panels show article Right (1.00) against a Center (−0.00) reader — and moving the slider barely
changes what is served or its political distances.

**The observation is accurate, and it is the designed behaviour of a mis-labelled knob.** The
slider works — it just does not do what its copy implies. Every claim below is measured on a
production-like local stack (real Backend + feed corpus + Personalizer, catalog with leans across
[−1, +1], readers past the measured threshold) or pinned to a specific line of code.

---

## The complete trace

| stage | what happens | verdict |
|---|---|---|
| UI slider | `recommendationStrength` 0–100, saved via `POST /api/me/settings` | works (measured: stored value round-trips) |
| persistence | `settings_service.normalize_settings` clamps, default 50 | works |
| params mapping | `api_server.rec_params_from_settings`: strength ≠ 50 → `beta = piecewise(0.30, 0.50, 0.80)` — **and nothing else** | **the first point of reduced influence** |
| model swap | `Backend._model_for`: `beta` reaches **only** the `rwe-d` strategy (`RWED(fg, beta)`); `rwe-b` keeps `epsilon=0.9`, `adaptive` is never touched by preferences | works as coded |
| blend | `DEFAULT_BLEND_PLAN = (rwe-b 6, rwe-d 4, adaptive 4)` — strength can influence at most **4 of 14 slots** | caps the reach |
| ranking | `RWED`: `q_d = 1 − deg^(−β)` — **pure item-popularity erasure; no lean term exists anywhere in it** (`rwe/random_walk.py:197`) | politically blind by construction |

End-to-end persistence was verified through the real HTTP app: `POST /api/me/settings` with
strength 0 and 100 stored correctly, produced `{'beta': 0.3}` / `{'beta': 0.8}`, and served
*different* feeds (overlap 8/15) — the params arrive; the plumbing is not the problem.

## Measured distributions

Center-balanced reader (position +0.086, mirroring the production screenshots) and a left-diet
reader (−0.658); story slot off to isolate the sliders; default feed:

### Strength (the slider under complaint)

| strength | beta | CENTER meanGap | LEFT meanGap |
|---:|---:|---:|---:|
| 0 (Gentle) | 0.30 | 0.793 | 1.146 |
| 50 | 0.50 | 0.892 | 1.071 |
| 100 (Bold) | 0.80 | 0.969 | **1.046** |

The deltas are real but **incidental and direction-inconsistent**: +0.18 for the center reader,
**−0.10 for the left reader** — Bold made the left reader's feed politically *closer*. A knob
whose sign flips with the reader's diet is not modulating politics; it is reshuffling popularity
(low-degree items happen to correlate with extremity in this corpus, in whichever direction the
corpus supplies). Feed overlap across the whole range: 0.67–0.86 Jaccard — mostly the same cards.

### Openness (the control — the slider that owns the political axis)

| openness | rwe-b slots | CENTER meanGap / cross | LEFT meanGap / cross |
|---:|---:|---|---|
| 0 | 4 of 14 | 0.793 / 6 | 1.022 / 5 |
| 50 | 6 of 14 | 0.892 / 8 | 1.071 / 6 |
| 100 | 8 of 14 | 0.983 / 10 | 1.225 / 8 |

Consistent, same-direction movement for both readers (+0.19 / +0.20 meanGap; cross-cutting count
6→10 and 5→8). **The blend budget is the one lever that measurably moves feed-level political
distance.**

### Isolations

* **rwe-d slice alone across beta**: slice overlap 0.26–0.50 — beta arrives and substantially
  reshapes *which long-tail items* fill its slots. What it moves is popularity, not politics.
* **rwe-b slice alone across epsilon (0.5 / 0.9 / 0.99)**: byte-identical slices — every card
  cross-cutting, `meanAbsLean = 1.000`, at every epsilon.
* **rwe-b slice alone across `max_distance` (0.6 / 0.8 / 1.0 / 1.2 / ∞)**: byte-identical slices
  — including at 0.6, which should exclude an item 1.09 away from the reader entirely.

## Why every distance-graded knob is inert (the structural finding)

Two stacked mechanisms, both measured:

1. **The ranking space carries only three political positions.** `feed_source._bias_label`
   collapses every article's numeric lean to `left` / `center` / `right` at a ±0.5 cut
   (`feed_source.py:47`), and `validate_qbias.label_to_pos` — the qbias corpus's label parser —
   snapped even the *lean* variants onto the poles. Measured in the recommender: `unique(item_positions) = {−1.0, 0.0, +1.0}`.
   CNN (−0.6) and Truthout (−1.0) are **the same point** in ranking space. "Different but not too
   far" (the paper's `max_distance` criterion) has nothing to grade — there is no intermediate
   distance to prefer, which is exactly why `max_distance` measured inert.
2. **Cross-first selection overrides ranking-level suppression.** `Backend._slice_select`
   (Commit R1.5) partitions the *whole* admitted list by a binary cross test (opposite sign,
   |lean| ≥ 0.5) and takes cross items first — so however hard `epsilon` or `max_distance`
   suppress an item's *score*, it still fills a bridge slot if it is cross-cutting and enough
   exist. The bridge slice therefore saturates at the far bucket (`meanAbsLean = 1.000`) in every
   configuration. (Also noted: the sim denominator is `_range = 4.0` — user positions span ±2
   against items at ±1 — compressing what little modulation the geometry has left.)

Consequently, per-card political distance in the bridge slice is effectively a constant (~the
maximum), and feed-level distance is decided by **slot arithmetic** — how many of the 14 slots
are rwe-b — which is the openness slider. The production screenshots' two gap-1.00 cards are
bridge cards doing exactly this.

## Eliminated alternatives

| hypothesis | verdict | evidence |
|---|---|---|
| The slider value never persists | refuted | HTTP round-trip: stored 0/100, params beta 0.3/0.8 |
| Params never reach the ranker | refuted | rwe-d slice overlap 0.26–0.50 across beta — the slice reshapes |
| A cache serves pre-slider results | refuted | same process, one cached model, different feeds per params (params are per-request by design, `personalize.recommendations`) |
| Beta weakly modulates politics | refuted | direction inverts between readers (+0.18 / −0.10) — incidental, not causal |
| Wiring epsilon would fix it | refuted | epsilon measured inert on the served slice (cross-first override) |
| Wiring `max_distance` would fix it | refuted | inert twice over: 3-bucket quantization + cross-first override |
| The story slot masks the slider | controlled | slot disabled for all measurements; it inserts ≤ 1 slider-independent card |

## Root cause

**Semantic mis-wiring.** "Recommendation strength" is wired to RWE-D's popularity-suppression
`beta` — a *long-tail* dial confined to ~4 of 14 slots with no political term — while its UI copy
("how aggressively we diversify away from your usual diet") and the viewpoint-framed explain
panels lead the reader to expect a *political-distance* dial. The political axis is entirely
owned by the openness slider's blend budget, and per-card distance modulation is structurally
impossible today because the ranking space quantizes leans to three buckets and cross-first
selection overrides score-level suppression.

## Smallest recommended fix

**Fix the labels, not the engine** — one i18n change, zero behavioural risk:

* *Recommendation strength* → copy that says what it measurably does: reach beyond the
  most-covered, mainstream stories (long-tail/popularity).
* *Political openness* → copy that plainly claims the political axis it measurably owns
  ("how often we bridge you to the other side"), since it is the lever that moved meanGap
  +0.19/+0.20 and cross-cutting 6→10 in both profiles.

**Not recommended as the fix:** wiring strength into the rwe-b budget (makes the two sliders
redundant), or wiring epsilon/`max_distance` (measured inert).

**Flagged as the enabling follow-up if the product decides a per-card "how far across" dial is
wanted** (larger, not the smallest fix): carry fractional registry leans through
`_bias_label` into the corpus positions (at least the 5-point AllSides scale that
`LEAN_LABELS` already supports — `lean-left`/`lean-right` exist there and are never emitted),
and make bridge-slice selection respect ranking-level suppression. Only then does a distance
knob have something to turn.

## Reproducing

The measurement harness lives in the session scratchpad (`slider_distance.py`); it seeds a
2,000-article catalog across ten publishers with leans in [−1, +1], builds the real stack, and
prints every table above. The persistence check runs through the real FastAPI app via
`TestClient`. No production data was touched; production persistence semantics are the same code
path (`POST /api/me/settings` → `store.save_settings` → per-request `rec_params_from_settings`),
verified working.

---

## Implemented (2026-08-02): the smallest fix — labels, not engine

Two i18n keys across all five catalogs (`web/messages/{en,es,fr,de,pt}.json`); no engine, API, or
behaviour change of any kind:

* `settings.opennessDesc` → *"How much of your feed reaches across the political spectrum to the
  other side."* — plainly claiming the political axis this slider measurably owns (the bridge-slot
  budget; meanGap +0.19/+0.20, cross-cutting 6→10 and 5→8 across its range). The previous copy
  ("…how strongly the rest of your political mix leans…") also mis-described the mechanism — the
  slider moves how MANY slots bridge, not how strongly the rest leans.
* `settings.strengthDesc` → *"How far we go beyond the most-covered, mainstream stories."* — what
  RWE-D `beta` measurably does (popularity suppression in its slice), replacing "how aggressively
  we diversify away from your usual diet", which promised the other slider's behaviour.

Slider titles, value labels (Gentle/Balanced/Bold…), ranges, defaults, and every engine mapping
are untouched — `rec_params_from_settings` still produces byte-identical parameters. Verified:
`check-i18n` (838 keys × 5 languages, parity + placeholders), `tsc`, `eslint`, 357 web unit tests.

The structural follow-up (fractional leans through `_bias_label` to enable a true per-card
distance dial) remains flagged above, unimplemented by design.

---

## Implemented (2026-08-02): the fractional-leans enabling work

The flagged follow-up, at the approved scope — the ranking space now carries the grade:

* **`feed_source._bias_label` is 5-point**: a moderate sided lean (|v| in [0.5, **1.5**)) emits
  `lean left`/`lean right`; strong leans (|v| ≥ 1.5) keep the poles. The 1.5 boundary is the
  midpoint of the **declared [−2, 2] AllSides lattice** — the registry (the scorer's single lean
  source) writes Lean Left/Right as ±1 and Left/Right as ±2. An earlier draft cut at 0.75, which
  the registry audit caught before deploy: on the integer lattice that band is EMPTY, and nothing
  would ever have graded in production.
  The sided/centre **partition is byte-identical** to the 3-point mapping (pinned by an
  exhaustive-sweep test), so cross-cutting membership and report bucket shares cannot move.
* **`validate_qbias.label_to_pos(graded=True)`** resolves the lean variants to `±LEAN_GRADE`
  (**0.6** — and the value is load-bearing: the report's centre bucket is `|pos| ≤ 0.5`
  *inclusive* while cross-cutting needs `|pos| ≥ 0.5`, so an article AT ±0.5 would be centre on
  one surface and cross-cutting on another; ±0.6 is sided under every cut in the system). The
  **default stays 3-point**: the validation CLI's gold enumeration and the Qbias dataset itself
  are 3-class. `catalog_from_qbias` opts in.
* Verified end to end: catalog leans → 5 CSV labels → **5 distinct corpus positions**
  ({−1, −0.6, 0, +0.6, +1}); CNN-grade outlets keep their grade instead of the pole; the
  consistency triple holds for every graded position. The augmented reader's own position is
  graded too (the click-mean now averages graded values — a balanced-diet reader measured +0.030
  where the quantized corpus said +0.100).
* **The geometry now grades** (the point of the exercise, mutation-checked): with a
  `max_distance` bound that admits the near side but not the far side, RWEB suppresses the pole
  at ε while the lean article becomes a sim-graded bridge; unbounded, both are bridges with the
  farther preferred — where the quantized corpus measured byte-identical erasure at every
  setting.

**What a serving-visible dial still needs, measured honestly:** the *served* bridge slice's
ordering is dominated by walk mass (`p`) and cross-first inclusion — a 3.4× erasure spread did
not reorder the slice on the production-like harness. Wiring a knob (which the shipped slider
copy deliberately does not promise) therefore additionally needs a selection-level distance
preference in the bridge slice, a design decision reserved for when a product surface actually
asks for it. No knob was wired here; served feeds change only in that graded positions flow into
ranking, explain panels (an article can now read `Right (0.60)` instead of a false `(1.00)`),
and the reader's measured position.

Full suite after the change: 2,587 passed, zero failures — the partition invariant held across
every report, filter, and recommendation test.
