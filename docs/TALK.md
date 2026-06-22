# Verification: the talk vs this implementation

A condensed record of checking Bibek Paudel's WWW'21 talk, part by part, against
this codebase.

**The slides themselves are now the deck** — see [`RWE_talk.pptx`](RWE_talk.pptx)
(run [`make_deck.py`](make_deck.py) to regenerate). This file is the
*verification verdict*: what matches the talk, and the honest caveats.

## Bottom line

The **algorithm and framework match the talk faithfully and completely** — every
formula maps to code, and the worked erasure example is reproduced to the
decimal. The **only** gap is the result *tables / figures*, which are computed on
the paper's private Twitter datasets; those are validated *directionally* on
synthetic data instead (a data-availability limit, not an implementation gap).

## Slide → code

| Talk section (slides) | Verdict | Code |
|---|---|---|
| Motivation — filter bubbles, bridging (1–5) | ✅ framing | `GUIDE.md` §1 · `RWEB` |
| Ideology of users / elites / content (6–10) | ✅ exact | `ideology.py`: `Pi_R`/`Pi_S` (eqs 6/9), `_objective` (eq 11) |
| Datasets UK/US/DE × RT/URL (11) | ⚠️ synthetic stand-in | `data.py` (real Twitter data not redistributable) |
| Estimated positions, Figs 3–5 (12–13) | ⚠️ result on private data | `IdeologyModel` — validated on synthetic (recovers planted positions) |
| Normative goals + strategies (15–16) | ✅ | `RWED` (long-tail) · `RWEB` (bridging) · `RWE(item_erasure=…)` (general; contrarian = one-liner) |
| RWE algorithm + worked example (17–19) | ✅ to the decimal | `FeedbackGraph` · `RWE.score_iterative` (== closed form) |
| Baselines CF / MF / P³ / RP³_β (21) | ✅ | `ItemKNN` · `BPRMF` · `P3` · `RP3Beta` |
| Result I — RecRange (22) | ✅ | `metrics.rec_range_at_k` |
| Result II — quadrant diversity (24) | ✅ | `quadrant_scatter.png` (`metrics.mean_recommended_position`) |
| Result III — shift (25) | ✅ exact definition | `metrics.ideological_shift` = `Pos(Recs) − reference` |
| Result IV — weighted UW/TW (26–27) | ✅ | `weighted_position` · `weighted_shift` · `weighted_range` |
| Contributions (28) | ✅ all four | `ideology.py` · `RWE` · `RWED`/`RWEB` · `RWE(item_erasure=…)` |

## Notable verifications

- **Worked long-tail example (slides 18–19) reproduced to the decimal:**
  `Q = 1 − [1/3, 1/2, 1/2]`, the per-round erased mass, and the start-mass
  sequence `1 → 0.51 → 0.26`; the slide's `w_s = Σ vₛᵢ Pᵏ ∘ (1−Q)` *is*
  `score_iterative`.
- **`RWE-D` with `v=1` equals `RP³_β`** — encoded as a test.
- **Ideal-point formulas** (eqs 6/9/11) match `ideology.py` line-for-line.
- **Shift definitions** `User/Train-Shift = Pos(Recs) − Pos(u / Train)` match
  `metrics.ideological_shift` exactly.

## Honest caveats

- **Private data.** The result tables/figures (Tables 5–8, Figs 3–6) cannot be
  regenerated here; they are validated *directionally* on synthetic data.
- **Contrarian (Example III).** Supported by the generalized framework as a
  one-liner on the `RWE` base; not shipped as a named class (the paper itself
  ships only long-tail + bridging).
- **Weighted measures (App. A.1).** The exact appendix normalisation is not in
  the paper, so `weighted_*` follow the two stated properties; `weighted_shift`
  is the negation of the slide's `UW-Shift` (higher-is-better vs lower-is-better).

---

*Presentation: [`RWE_talk.pptx`](RWE_talk.pptx)  ·  Beginner guide:
[`../GUIDE.md`](../GUIDE.md)  ·  API reference: [`../README.md`](../README.md)*
