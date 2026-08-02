# Political-position consistency audit (post graded-lean calibration)

**Scope:** every place a political position is computed, ranked on, or displayed — registry →
scoring → corpus positions → ranking → explanations → Information Health metrics → web UI.
**Status:** verification complete; fixes 1–4 are **implemented** — see
"Implemented" at the end.
**Date:** 2026-08-02, at `f2fb88d`.

**Verdict in one line:** every *computation* is internally consistent and scale-safe — but the
product serves **two different value spaces to the UI under one field name**, so the same outlet
displays two different numbers and two different labels depending on the page. That split
predates the graded calibration (it was visible for ±2 outlets all along); the calibration
widened it to every non-centre outlet.

---

## The two value spaces

| space | scale | values | produced by | consumed by |
|---|---|---|---|---|
| **Scored** (registry) | [−2, 2] AllSides lattice | −2, −1, 0, +1, +2 | `ingest.Scorer` ← `outlet_registry.csv` | Discover, search, story coverage, analyzer, history, publisher pages |
| **Position** (corpus) | [−1, 1] 5-point | −1, −0.6, 0, +0.6, +1 | `_bias_label` → `label_to_pos(graded=True)` | ranking (RWEB/adaptive), cross-cutting gate, hr metrics, explain panel, **recommendation cards** |

Every *internal* consumer stays inside one space with lattice-safe cuts — verified per component:

| component | space | cut | verdict |
|---|---|---|---|
| `_cross_of` (cross-cutting gate) | position | \|lean\| ≥ 0.5 | ✅ graded ±0.6 sided |
| `hr` viewpoint metrics (`LEAN_TAU`) | position | centre \|pos\| ≤ 0.5 inclusive | ✅ the reason `LEAN_GRADE` is 0.6 |
| `rec_explain` panel (Your position / Article / Gap / Estimated effect) | position, all four numbers | — | ✅ single space |
| `_opposing_leans` (story slot) | scored (coverage members) | ±0.5 buckets | ✅ lattice-safe |
| store search/discover lean facet SQL | scored | left ≤ −0.5 / right ≥ 0.5 | ✅ lattice-safe |
| analysis enrichment (readerShares / addsMissing) | scored, both sides | `_BUCKET_ORDER` @ τ 0.5 | ✅ single space |
| history insights | scored | τ 0.5 mirror | ✅ single space |
| user position (click-mean) | position | — | ✅ graded (measured +0.030 vs quantized +0.100) — but see F4 |

## Measured: the same outlet across surfaces (local stack, registry-lattice leans, at `f2fb88d`)

| outlet | registry (AllSides) | Discover / stories serve | web label | Rec card serves | web label |
|---|---|---:|---|---:|---|
| CNN | −1 (Lean Left) | **−1.0** | **"Left"** | **−0.6** | **"Lean Left"** |
| NPR | −1 (Lean Left) | −1.0 | "Left" | −0.6 | "Lean Left" |
| The Guardian | −1 (Lean Left) | −1.0 | "Left" | −0.6 | "Lean Left" |
| BBC / AP / Reuters | 0 (Center) | 0.0 | "Center" | 0.0 | "Center" |
| The Economic Times | +1 (Lean Right) | +1.0 | **"Right"** | +0.6 | "Lean Right" |
| Geo TV | +1 (Lean Right) | +1.0 | "Right" | +0.6 | "Lean Right" |
| Fox News | +2 (Right) | **+2.0** | **"Strong Right"** | **+1.0** | **"Right"** |
| New York Post | +2 (Right) | +2.0 | "Strong Right" | +1.0 | "Right" |

(The analyzer returned `Unknown` in this harness because the synthetic domains aren't in the
registry — a fixture artifact; production domains resolve and the analyzer serves scored space.)

## Findings

**F1 — Dual-space serving under one field name (the headline).** `article.lean` on a
recommendation card is a corpus *position*; on every other surface it is the *scored* registry
lean. Same outlet → different number and different badge depending on the page (CNN −0.6 "Lean
Left" vs −1.0 "Left"; Fox +1.0 "Right" vs +2.0 "Strong Right"). **Not a regression from the
calibration**: pre-change, ±2 outlets already diverged (rec +1.0 "Right" vs discover +2.0
"Strong Right"); ±1 outlets coincided only because quantization snapped both spaces to the same
number. The calibration extended the visible split to every non-centre outlet.

**F2 — Web label thresholds contradict the registry's declared semantics.** `leanLabelKey`
(cuts 0.9 / 1.4) renders scored ±1 — AllSides **Lean** Left/Right — as plain "Left/Right", and
scored ±2 — AllSides **Left/Right** — as "Strong Left/Right", a tier AllSides does not use. So
even within the scored space, Discover labels CNN "Left" while the registry (and now the rec
card) say Lean Left.

**F3 — `leanToPercent` clamps to [−2, 2] for both spaces.** The spectrum bar places rec-card
CNN (−0.6) at 35% but Discover CNN (−1.0) at 25% — same outlet, two bar positions.

**F4 — Mixed scales inside the user-position click-mean (pre-existing).**
`augmented_corpus.augment` appends *novel* (unjoined) reads with their **raw scored lean**
([−2, 2]) into the positions array ([−1, 1]): a novel Fox read contributes +2.0 where a joined
one contributes +1.0 — measured earlier as 9-valued position sets. Biases the measured position
of novel-heavy readers outward, and feeds RWEB's `_range` (the ±2-users-vs-±1-items compression
already documented in the slider report).

**F5 — Cosmetic:** rec-card `publisherLean` serializes unrounded float noise
(`-0.6000000000000001`) and lives in position space while Discover's `publisherLean` is scored —
F1's twin on the publisher field.

**No stale {−1, 0, +1} assumptions found** in engine or web code: every threshold is an
inequality (≥/≤ 0.5, > 0.9, > 1.4), none does an equality or three-way-set test against the old
quantized values. The grep sweep and 2,587-test suite agree.

## Smallest recommended fixes (ranked; implemented — see the section at the end)

1. **Serve ONE space to the UI — the scored registry lean — on recommendation cards too.** The
   rec enrichment pass (`_enrich_rec_media` / `_attach_published_at`) already joins each card to
   its `FeedArticle` by canonical URL; carrying `scored.lean` (+ bucket) onto the card there
   makes every user-facing surface scored-space, with positions remaining what they are: the
   *internal* ranking geometry. One enrichment-site change; the explain panel (deliberately
   position-space, self-consistent) is unaffected.
2. **Recalibrate `leanLabelKey` to the lattice** (with F1 done, only scored values reach it):
   "Lean Left/Right" for \|lean\| in [0.5, 1.5), "Left/Right" for ≥ 1.5, retire "Strong" — the
   web then speaks AllSides' own tiers. Two cut constants + i18n keys.
3. **Round `publisherLean` at serialization** and move it to scored/registry space with F1.
4. **Scale novel-read leans into position space in `augment`** (map the scored lattice through
   the same 0.5/1.5 grading: ±1 → ±0.6, ±2 → ±1.0) — engine-behaviour change, decide separately.

Order matters: 1 alone removes every user-visible number conflict; 2 makes the labels match
AllSides; 3–4 are hygiene behind the scenes.

## Reproducing

The per-surface measurement harness is in the session scratchpad; it seeds a registry-lattice
catalog, boots the real app, and prints the actual-vs-expected table above from
`/api/discover`, `/api/stories`, `/api/recommendations`, `/api/analyze`, and `/api/me`. On the
box, the same spot-check needs only two browser tabs: any CNN card on Discover (−1.0, "Left")
next to a CNN recommendation card (−0.6, "Lean Left") — if both tabs show the same number, F1
has been fixed.

---

## Implemented (2026-08-02): fixes 1–4, in the recommended order

1. **One UI value space (F1).** `store.feed_article_media` now carries the article's scored
   registry lean under its own `catalogLean` key, and the rec enrichment pass rewrites the card's
   `lean` / `leanBucket` with it — the same numbers Discover, search, stories, and the analyzer
   serve. Positions remain what they always were: the internal ranking geometry. The history
   attach copies only `publishedAt` and never sees the new key. The cross-surface contract is
   pinned by `tests/test_lean_consistency.py` (rec card lean == Discover lean for every catalog
   card, mutation-checked), plus a sidedness guard: the `crossCutting` flag (computed from the
   position upstream) can never disagree with the served scored lean, because the two spaces'
   sided/centre partition is byte-identical.
2. **AllSides tiers in the web labels (F2).** `leanLabelKey` cuts at the lattice midpoint 1.5:
   Lean Left/Right at ±1, Left/Right at ±2; the invented "Strong" tier is retired and its i18n
   keys removed from all five catalogs. CNN now reads "Lean Left" on every page; Fox News reads
   "Right" everywhere (it read "Strong Right" on Discover before).
3. **`publisherLean` unified and rounded (F3)** — scored space, no float noise, same enrichment
   site.
4. **Novel reads land on the position lattice (F4).** `validate_qbias.scored_to_position` maps
   the registry scale through the same 0.5/1.5 grading as the labels (parity pinned by test), and
   `augmented_corpus.augment` uses it for novel columns — a novel Fox read now weighs +1.0 in the
   reader's click-mean like a catalog-joined one, and an unknown lean stays NaN.

**Known residual, deliberate:** the explain panel's *viewpoint-impact* numbers (Your position /
Article / Gap / Estimated effect) remain in position space — they are the ranking's own
arithmetic and stay internally consistent; converting them to scored space would require
re-deriving the reader mean and shift math in scored space wholesale. The panel's metadata row
(bucket label) follows the card and is therefore scored-consistent. Flagged for a product
decision if the magnitude difference (card −1.0 vs panel −0.60 for CNN) proves confusing.

F3's spectrum-bar item (`leanToPercent`) dissolved with F1: only scored values reach the UI, so
each outlet has one bar position again.
