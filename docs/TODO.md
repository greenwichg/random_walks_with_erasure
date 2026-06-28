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

## 1. Verification — [you] · Colab · next

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
- [ ] **Full notebook** clean top-to-bottom run on a fresh runtime

## 2. Fold results into the docs — [me] · after you paste outputs

- [x] Per-user-sig p-values → `RESULTS.md` limitation #3
- [x] New **"Second dataset (MovieLens-1M)"** section in `RESULTS.md`
- [x] Decide emotion-metric framing — **keep, labelled experimental** (eyeball
      confirmed the documented behaviour)

## 3. Data-dependent — [you] source → then quick (no code needed)

- [ ] **Stronger lean axis** — a license-clean outlet-lean table (AllSides /
      Media Bias-Fact-Check) → `ingest --lean-csv`. **Highest leverage:** lifts
      both the paper's RQ3 (Spearman 0.27) *and* the report's viewpoint/echo metrics
- [ ] *(alternative)* multi-rater human-label gold set for the axis

## 4. Paper / publication — [me + you] · when ready

- [ ] Convert `PAPER.md` → a venue's **LaTeX template**
- [ ] Flesh out Related Work into prose + **BibTeX**
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
