# Viewpoint (Political openness) slider: full-range verification

**Question:** does the Political openness slider meaningfully influence recommendation selection
across its full range — set, ranking, diversity, viewpoint distribution — monotonically and per
its stated semantics ("How much of your feed reaches across the political spectrum to the other
side")?
**Method:** read-only probe on the box (2026-08-03): the production recommender stack rebuilt
standalone (`Personalizer(persist=False)`, same construction as the serving path), the busiest
real reader (uid 1, 99 reads), feed built at slider values 0 / 25 / 40 / 50 / 75 / 100 with
strength untouched, measuring the ranking layer's output (leans below are corpus *positions*,
the ranking's own geometry — the handler's enrichment pass later rewrites card leans to scored
registry values, which does not change membership or ordering).
**Prior art:** the original openness→epsilon wiring was proven inert on the served feed
(`docs/W1_OPENNESS_SLIDER_AUDIT.md`) and replaced by the bridge-budget mechanism
(`api_server.blend_plan_for`); this is the direct measurement of the replacement.

**Verdict in one line:** verified working — five designed plateaus (bridge budgets 4→8 of 14
slots), each step changes the feed (adjacent Jaccard 0.86, exactly the budgeted swap), the
cross-cutting count tracks the budget perfectly and monotonically (4/5/6/7/8), the swapped-in
cards at maximum are all opposite-side bridge picks, determinism and settings persistence hold,
and the only "no-effect regions" are the *designed* quantization plateaus.

## The mechanism (measured, not inferred)

The 0–100 slider quantizes through `round(piecewise(v, 4, 6, 8))` into an integer RWE-B
bridge-slot budget over the 14-slot plan `(rwe-b 6, rwe-d 4, adaptive 4)`:

| slider positions | 0–12 | 13–37 | 38–62 | 63–87 | 88–100 |
|---|---:|---:|---:|---:|---:|
| bridge budget | 4 | 5 | 6 (default) | 7 | 8 |

Five effective settings; within-plateau movement is a no-op **by design** (confirmed on the real
corpus: v=40 and v=50 produce identical sets).

## Results at the five effective settings (same reader, same corpus)

| v | plan bridges | served bridges | cross-cutting | leans L/C/R | mean \|pos\| | outlets | topics |
|---:|---:|---:|---:|---|---:|---:|---:|
| 0 | 4 | 4 | 4 | 5/3/5 | 0.49 | 13 | 7 |
| 25 | 5 | 5 | 5 | 4/3/6 | 0.49 | 13 | 7 |
| 50 | 6 | 6 | 6 | 4/3/6 | 0.49 | 12 | 6 |
| 75 | 7 | 7 | 7 | 3/3/7 | 0.52 | 12 | 5 |
| 100 | 8 | 8 | 8 | 3/2/8 | 0.60 | 12 | 5 |

- **Served == planned at every setting** — the bridge pool never runs dry; the freed/added slots
  redistribute across rwe-d/adaptive exactly per `blend_plan_for`.
- **Cross-cutting count == bridge budget at every setting, strictly monotone** (4→8).
- **Adjacent settings overlap at Jaccard 0.86** (one card swaps per plateau step — the budgeted
  slot); **min vs max overlap 0.53** — roughly a third of the feed changes across the full range.
- **The swaps are the stated semantics verbatim**: the reader is left-of-centre; the cards
  present only at v=100 are all `rwe-b`, cross-cutting, opposite-side (+0.6 to +1.0 — Dagens
  Nyheter, Evening Standard, Washington Times, NY Post); the cards present only at v=0 are
  same-side/centre popularity and adaptive picks.
- **Determinism**: v=50 built twice → byte-identical sequence.
- **Diversity trade at the top**: topics 7→5 and one fewer outlet at maximum — the bridge pool
  concentrates; expected, small, and visible.

## Hypotheses eliminated

| hypothesis | evidence |
|---|---|
| settings don't persist / aren't read on serve | stored `politicalOpenness=14, recommendationStrength=92` → `{'openness': 14, 'beta': 0.752}` — the reader's real sliders flow to params (and match the screenshot's "Essential bridges only" / "Bold" labels) |
| epsilon-style inertness (the W1 failure) survives | that mapping was replaced; the budget path measurably moves the feed at every plateau step |
| cross-first slice / argsort cancels the effect | cross count tracks the budget 1:1 — nothing cancels |
| bridge-pool exhaustion flattens high settings | served bridges == plan at 7 and 8 |
| story slot masks the effect | `RWE_STORY_SLOT=1`, constant one slot at every setting (13 cards served at all v) |
| quantized item positions blunt the axis | positions are graded 5-point since `f2fb88d`; mean \|pos\| moves 0.49→0.60 across the range |

## Observations (not defects)

1. **The UI communicates four label bands over five engine plateaus**, and the boundaries don't
   coincide (labels change at 25/55/80; budgets at 13/38/63/88). A reader at v=20 and one at
   v=30 see different labels but get identical feeds; v=10 vs v=20 same label, different feeds.
   Cosmetic; flagged for a product pass if slider legibility ever comes up.
2. The probe's trailing `forkserver ... /app/<stdin>` traceback is stdin-script noise from the
   story-build subprocess machinery, emitted after all sections completed; results unaffected.
3. One adaptive pick at v=0 was an obituary-feed article — the content-mill class
   (`docs/CONTENT_MILL_STORY_EVALUATION.md`) leaking into recommendations at low openness;
   evidence for the curation option there, not a slider defect.
