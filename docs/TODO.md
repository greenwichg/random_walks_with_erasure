# Project TODO / status tracker

> Living checklist of what's done and what remains. Companion to
> [`PAPER_PLAN.md`](PAPER_PLAN.md), [`HEALTH_REPORT_PLAN.md`](HEALTH_REPORT_PLAN.md),
> and [`NOVELTY_CHECK.md`](NOVELTY_CHECK.md).
>
> **Implementation is complete** — what remains is *verification* (running the
> finished code on real data), *folding results*, *data sourcing*, and the *writeup*.
> Owner tags: **[you]** = your Colab/data side · **[me]** = code/docs I do here.

## Done — implementation complete (tested on the branch)

- [x] RWE / RWE-D / RWE-B + baselines (ItemKNN, P3, RP³-β, BPRMF), tested
- [x] MIND pipeline (ingest, text-lean axis) + reproduced 7-seed RQ2/RQ3 results
- [x] axis-alignment diagnostic (`--per-user-sig` axis check) + `plot_axis.py` figure
- [x] `MATH.md` deep-math guide (+ diagrams, cheat-sheet polish, skip-tags)
- [x] per-user paired significance test (`eval_mind.py --per-user-sig`)
- [x] MovieLens-1M RQ2 harness (`eval_movielens.py`)
- [x] Information Health Report **v1** + standalone **HTML rendering**
- [x] reporting-vs-opinion + emotional-tone classifiers (wired into the report)

## 1. Verification — [you] · Colab · ✅ COMPLETE

- [x] Eyeball the **classifiers** — `# 8e`/`# 8f`: register clean (editorials/op-eds
      vs wire reports); emotion sane at the tails but surface-keyed → **keep, stays
      experimental**
- [x] Sanity-check the **enriched health report** on real users — `# 8d`; surfaced
      that it needs the full-catalog `mind_full.npz` (+ `--require-political` for the
      demo); fixed
- [x] **Per-user significance** on real MIND — cell `# 8b2`: every method's
      accuracy gap to P3 is per-user significant (RWE-D `p ≈ 8e-235`, n ≈ 2,546) →
      folded into `RESULTS.md` limitation #3
- [x] **MovieLens RQ2** — ran here (5 seeds); RWE-D ties RP³-β on accuracy, edges it
      on coverage/surprisal → folded into `RESULTS.md`
- [x] **Full notebook** clean top-to-bottom run on a fresh runtime — passed: every
      heavy artifact restored from the Drive cache (gated MIND download bypassed),
      no errors, 7-seed RQ2/RQ3 + per-user-sig + sweep + axis all reproduce to the
      printed precision. **Validation phase complete.**

## 2. Fold results into the docs — [me] · after you paste outputs

- [x] Per-user-sig p-values → `RESULTS.md` limitation #3
- [x] New **"Second dataset (MovieLens-1M)"** section in `RESULTS.md`
- [x] Decide emotion-metric framing — **keep, labelled experimental** (eyeball
      confirmed the documented behaviour)

## 3. Stronger lean axis — behavioral ideal-point is the real fix · [you+me]

The MIND text-lean axis is a weak / construct-mismatched proxy (it conflates
*topic* with *stance*; a 40-item human check came out ≈ 0). The principled fix is a
**behavioral** axis from political-endorsement data — now built and proven:

- [x] **Reddit Politosphere ingest — the real fix** — `examples/ingest_politosphere.py`
      parses the public Politosphere comments (bz2/JSON-lines) into a **user×subreddit
      endorsement** `.npz` in the MINDData container; `IdeologyModel` then fits a latent
      L↔R axis **from behaviour alone** (no text, no Twitter API). `examples/data/
      subreddit_lean.csv` orients + validates it. **Synthetic proof: `lean_corr=0.94`**,
      axis splits progressive < democrats < center < Conservative < Republican.
      Unit-tested (6 tests). The whole MIND eval/plot pipeline runs on it unchanged.
- [ ] **Run it on real Politosphere** — [you] download a slice from
      <https://zenodo.org/records/5851729> (confirm its license), then
      `ingest_politosphere.py --ideology` → `eval_mind` / `plot_axis`. This gives RQ3
      a *genuine* ideological axis — the headline-proxy limitation gone — and a clean
      `lean_corr` validation number. Then I fold it into RESULTS / the paper.

_MIND text-axis attempts, kept for the record:_
- [x] **Ensemble tooling** — `examples/ensemble_lean.py` (z-score + average
      independent bias models; prints pairwise convergent validity) + notebook
      `# 7b`. Averaging cuts single-model noise — the *codeable* lever for the axis
      (calibration can't help: Spearman is rank-based). Unit-tested.
- [x] **Ran it (inconclusive — ensemble not adopted)** — built `lean_b.csv`
      (premsa/AllSides) + `lean_ens.csv`; cross-model agreement Spearman **+0.38**.
      Against a 40-item human-labeled set: ensemble **−0.05** vs single **−0.09**
      (sign-acc 0.50 vs 0.30) → **no meaningful gain**, both ≈ 0. But **n=40 is
      underpowered** (need |r|>~0.31 for p<.05, so the original 0.27 isn't
      significant either) and those labels were *anchored*. So: not a clean "fails,"
      just no signal at this sample size. **Not folded into RESULTS/paper.**
- [ ] **Blind, larger gold set (n≥100, 2–3 raters)** — the real unblocker: the
      only way to a trustworthy axis number *and* a real ensemble-vs-single verdict.
      Label without seeing any model output, then `validate_lean.py` all axes.
- [ ] **Outlet-lean (blocked on MIND)** — would lift RQ3 *and* the report's
      viewpoint/echo, but needs a `news_id→publisher` source-map MIND omits (MSN
      URLs); only then does an AllSides/MBFC `--lean-csv` attach

## 4. Paper / publication — [me + you] · when ready

- [x] Convert `PAPER.md` → a venue **LaTeX template** — `docs/paper/paper.tex`
      (ACM `sigconf`, Overleaf-ready; static-checked: envs/braces/cites/figures OK)
- [x] Flesh out Related Work into prose + **BibTeX** — `docs/paper/references.bib`
      (12 entries; a few flagged `% TODO verify` for author lists). Paper also folds
      in the MovieLens replication + per-user significance.
- [ ] **Verify BibTeX** (`drdw2025`, `network2024polarization`, `stray2023bridging`
      author lists / pages) + add CCS once the venue is fixed
- [ ] **Advisor review** of the reproduced results + draft
- [ ] **Pick the target venue** (workshop / short / reproducibility track) + format to it

## 5. Optional / future research — lower priority

- [ ] **Real-log satisfaction signal** — currently synthetic; computing it from real
      dwell/return logs is the move that turns the most-distinctive idea into genuine
      novelty (needs a dataset with browsing logs — MIND lacks them)
- [ ] Health-report polish (e.g. a "you vs the average reader" population view)

---

**Critical path:** §1 (verify, [you]) → §2 (I fold the numbers) gives a fully
reproduced, per-user-significant, two-dataset result + a working health-report PoC.
§3 (lean axis) is the highest-value *data* task; §4 (paper) is the path to submission.

_Last updated: 2026-06-27._
