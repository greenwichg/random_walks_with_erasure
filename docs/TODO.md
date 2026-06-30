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

## 3. Stronger lean axis — ✅ VALIDATED on the behavioral axis (Politosphere, mi200)

We pursued a behavioral ideal-point axis (the principled fix) all the way to a real
run **and a 5-seed robustness check**. **Outcome: it validates** — `lean_corr =
0.57 ± 0.19` over 5 seeds (min 0.33, max 0.82) with cleanly ideological extremes, once
low-signal subreddits are filtered **and** the non-convex fit is stabilized by keeping
the highest-likelihood of 8 restarts (a 1-restart fit collapsed to ~0 on 2/5 seeds).
The RQ3 bridging is robust regardless (`uw_shift` 1.97 ± 0.04, beats best baseline 5/5).
Folded into `RESULTS.md` + the paper, with honest caveats (threshold-sensitive, n=20
labels, a fit-sensitivity now fixed by the unsupervised multi-restart).

- [x] **Reddit Politosphere ingest + eval** — `examples/ingest_politosphere.py`
      (user×subreddit endorsement → `IdeologyModel` ideal point) + turnkey
      `notebooks/run_politosphere_eval.ipynb`. Synthetic proof worked (`lean_corr=0.94`).
      On the **real** US-2016 slice the axis is **threshold-sensitive**: at the loose
      filter (295 subs, `--min-item-clicks 20`) it came out non-ideological
      (`lean_corr=0.13`, scrambled — niche-subreddit noise), **but at
      `--min-item-clicks 200` (114 subs with ≥200 distinct commenters) it VALIDATES:
      `lean_corr = 0.57 ± 0.19` over 5 seeds, clean left↔right extremes** (communism/
      anarchism/socialism left; Trump/Farage/conservatives right). int-coded ingest fixed
      a 35-min→0.2s hang; unit-tested.
- [x] **Politosphere RQ3 on the validated axis** — `# 3b` cell: RWE-B bridges hardest
      (`uw_shift 1.932` vs P3 1.125, RP³-β 0.558), highest `shift@10` (1.224) and
      `rec_range` (4.418) without over-shooting (`uw_recs` .664). A genuine
      ideological-bridging result on a behaviorally-validated axis.
- [x] **Folded into the writeup** — `RESULTS.md` (third-dataset RQ2+RQ3 tables, the
      validated axis + threshold-sensitivity, Limitation 1 rewritten "behaviour
      validates the axis") and the paper (abstract, §6 third dataset, §ethics). RQ2
      long-tail holds on **3 datasets**; the bridging mechanism is now demonstrated on
      a **validated** axis (MIND text-lean RQ3 stays *suggestive*).
- [x] **Multi-seed robustness — RAN, and it surfaced + fixed a real instability.**
      Notebook `# 3c` (read-once, in-memory; 2.5 h → ~40 min) re-fits the validated
      ingest across 5 seeds and aggregates `lean_corr` + RWE-B `uw_shift` vs the best
      baseline. **First run (1-restart) exposed seed-instability**: `lean_corr` =
      {0.82, 0.01, 0.09, 0.69, 0.64}, mean 0.45 ± 0.33 — the non-convex ideal-point fit
      collapsed to ~0 on 2/5 seeds. **Fix**: `IdeologyModel.fit(restarts=N)` keeps the
      highest-likelihood of N inits — an *unsupervised* selection (data log-likelihood,
      never the labels; validated on synthetic at corr(objective, recovery)=+0.94).
      **Re-run (8 restarts)**: `lean_corr = 0.57 ± 0.19` (min 0.33, max 0.82) — collapses
      gone. RWE-B bridging robust throughout (`uw_shift` 1.97 ± 0.04, beats best baseline
      5/5). Threaded through `fit_ideology`/`ingest_politosphere --ideology-restarts`;
      unit-tested; folded into all the docs. _(Remaining, low-priority: more labeled
      subreddits, n>20, to tighten the estimate.)_

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
- [~] **Blind, larger gold set (n≥100, 2–3 raters)** — the real unblocker for a
      trustworthy *MIND text-axis* number. **Harness built**: `validate_lean.py
      --sample 100` makes the blind stratified template; `--raters r1 r2 r3` now
      reports **inter-rater agreement** (mean pairwise Spearman + quadratic-weighted
      kappa) and validates each axis against the rater **consensus** (unit-tested).
      Remaining = the human labeling itself (blind, ≥100 items, 2–3 raters), then run.
- [x] **Automated convergent validity (LLM second opinion) — built AND run.**
      `examples/llm_label.py` labels the blind template with a second model
      (`--provider gemini`, **free** via Google AI Studio, default — or `--provider
      anthropic`/Claude, paid; structured outputs, from titles only) and writes a
      provenance-stamped `news_id,position,reason` CSV that `validate_lean.py --against`
      reads directly; notebook `# 7d` runs the whole free flow (retry/backoff on transient
      503s). **Honesty caveat baked in** (docstring, output stamp, notebook print): it's
      **convergent validity (model-vs-model), weaker than human ground truth** — kept
      separate from the `--raters` consensus path. **Ran it (Gemini 2.5 Flash, n=120):
      Spearman −0.28, sign-acc 0.41** — a *negative* result. Inspection: the MIND political
      slice is impeachment-dominated, where the LLM codes anti-Trump *content* as left
      while the article-classifier keys on surface lexicon → neither recovers editorial
      slant from a bare headline. **Folded into `RESULTS.md`/`PAPER.md`/`paper.tex`** as
      concrete evidence the MIND text-lean RQ3 is *suggestive only*, reinforcing the
      behavioral Politosphere axis (`lean_corr 0.57 ± 0.19` over 5 seeds) as the primary
      ideology result. (The
      `# 7b` two-BERT +0.38 is *shared* method bias, not independent validation.)
- [~] **Outlet-lean — software-unblocked; only a publisher-carrying catalog remains.**
      Would lift RQ3 *and* the report's viewpoint/echo. The blocker is purely *data*:
      MIND ships MSN URLs with no publisher. **Built**: a curated `examples/data/
      outlet_lean.csv` (AllSides-style, ~55 outlets) + `examples/build_source_map.py`
      (turns any publisher-carrying catalog — EB-NeRD `.parquet`, or a resolved
      MSN-provider table — into the `news_id→outlet` map `ingest_mind --source-map`
      consumes). Unit-tested end-to-end. Remaining = point it at EB-NeRD (or an MSN→
      provider resolution); MIND alone stays blocked.

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

- [~] **Real-log satisfaction signal** — currently synthetic; computing it from real
      engagement logs is the move that turns the most-distinctive idea into genuine
      novelty (MIND lacks the logs, but **Reddit/Politosphere may carry them**). Built a
      **feasibility probe** (`examples/satisfaction_probe.py` + notebook `# 7`): it reads
      the comment `score`/`created_utc`/`parent_id` we currently discard and compares
      **cross-cutting vs same-side** engagement (reception via upvotes, depth via reply
      threads, return via months) on the **validated** axis — printing a verdict on
      whether the measured signal is a real satisfaction proxy or adversarial flame-war
      noise. Unit-tested. **Pending: run on the real slice** (does Politosphere keep the
      fields, and are cross-cutting comments welcomed or dogpiled?). If sensible → promote
      to a measured metric + wire into `AdaptiveRWEB`; if flame-war-dominated → report as
      *why* real satisfaction is hard here (itself a finding).
- [ ] Health-report polish (e.g. a "you vs the average reader" population view)

---

**Critical path:** §1 (verify, [you]) → §2 (I fold the numbers) gives a fully
reproduced, per-user-significant, three-dataset result + a working health-report PoC.
§3 (lean axis) is **done** — the behavioral axis validates across 5 seeds (`lean_corr
= 0.57 ± 0.19`); §4 (paper) is the path to submission. The one remaining axis task is
more labeled subreddits (n>20) to tighten the estimate.

_Last updated: 2026-06-30 (5-seed robustness on the behavioral axis: a 1-restart fit was
seed-unstable (collapsed to ~0 on 2/5 seeds); an unsupervised 8-restart likelihood
selection fixes it → `lean_corr = 0.57 ± 0.19` (min 0.33, max 0.82), RQ3 bridging robust
(`uw_shift` 1.97 ± 0.04, 5/5). Earlier same day: LLM convergent-validity check on the MIND
text-lean axis, Spearman −0.28 (n=120), reinforcing the suggestive-only reading)._
