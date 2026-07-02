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
- [x] **Article-level reliability decomposed** — `examples/lean_agreement.py`
      (notebook `# 7e`, unit-tested) turns that +0.38 into the per-article metric a
      reviewer asks for. Over **n=2,955** articles the two BERT bias models agree on the
      exact L/C/R bucket only at **Cohen's κ=0.14** ("slight", 59% raw) yet flip
      Left↔Right just **2.2%** of the time (**side-only κ=0.575**, ~80% raw): the
      disagreement is *centre-vs-lean*, not *which* lean, and the two are differently
      calibrated (politicalBiasBERT 77% centre; premsa right-skews, 794 R vs 329 L). This
      quantifies "well-oriented but weakly-resolved" — the *per-article* label is
      unreliable; the axis is usable only in **aggregate** (all RWE consumes). Even under
      *shared* method bias (both article-text BERTs, which should over-agree) the exact
      label is only "slight." **Folded into `RESULTS.md`/`PAPER.md`/`paper.tex`.**
- [~] **Blind, larger gold set (n≥100, 2–3 raters)** — the real unblocker for a
      trustworthy *MIND text-axis* number. **Harness built**: `validate_lean.py
      --sample 100` makes the blind stratified template; `--raters r1 r2 r3` now
      reports **inter-rater agreement** (mean pairwise Spearman + quadratic-weighted
      kappa) and validates each axis against the rater **consensus** (unit-tested).
      Remaining = the human labeling itself (blind, ≥100 items, 2–3 raters), then run.
- [x] **Large AllSides-gold check via Qbias — built (notebook `# 7g`, unit-tested).**
      `examples/validate_qbias.py` scores the *same* classifier on Qbias (~21.7k
      AllSides-labeled articles, 4 expert annotators; Haak & Schaer 2023) and reports
      agreement with the human L/C/R label (Spearman / Cohen's κ / accuracy / confusion) +
      an outlet-lean-vs-gold join (the branch 409-blocked on MIND, working here). Refactored
      `classify_lean.py` to expose `load_classifier`/`score_texts` so both use identical
      scoring. **RAN it (2026-07-02, n=3000) — and it OVERTURNED the domain-shift hypothesis.**
      The intended "high κ here + κ=0.14 on MIND ⇒ domain shift" reading is dead: headline-only
      κ = **0.007** (Spearman 0.02, 20 % acc, collapses to centre — 2896/3000 predicted C),
      even *in-distribution*. **Then the `--use-text` run (2026-07-02) overturned our NEXT
      guess too:** the article body's first **256 tokens** are **also** near-chance — κ =
      **0.001** (Spearman 0.065, 26 % acc, still 71 % predicted C). So it is **not headline
      length** either: this AllSides-trained classifier simply does not recover the AllSides
      *article* label from text. **Then `# 7g`/`# 7h` at ≤512 tok refined it a THIRD time:**
      politicalBiasBERT stays near-chance with the body (κ 0.001, identical 256 vs 512 — Qbias
      ships short excerpts, so 512 added no text), but a **2nd model, premsa, does better with
      the body** (Spearman **0.081→0.221**, side-only κ **0.05→0.30**). So the disambiguation
      answer is **both, partially**: text is *not* signal-free (premsa gets a weak-but-real
      signal), part of the extreme near-zero was a **politicalBiasBERT miscalibration**, and for
      premsa the headline *was* a limiter. Net: **text-lean is a *weak, model-sensitive* proxy**
      (best Spearman ~0.22 vs human gold), and the **outlet-lean** join dwarfs it — **κ = 0.841 /
      side-only κ = 1.000 / Spearman 0.918** (~4× the best text model) at 45 % coverage — so the
      outlet-first conclusion **holds and sharpens**. All runs done; folded into RESULTS.md /
      PAPER.md / paper.tex; validate_qbias docstring + CAVEAT + `# 7g`/`# 7h` cells corrected.
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
- [~] **Outlet-lean — software-unblocked; the data gap is *structural*, not a quick source.**
      Would lift the *MIND* RQ3 and the report's viewpoint/echo (not load-bearing — the
      **behavioral Politosphere axis already validates**, `lean_corr 0.57 ± 0.19`). **Built**:
      a curated `examples/data/outlet_lean.csv` (AllSides-style, ~55 outlets) +
      `examples/build_source_map.py` (turns *any* multi-publisher catalog into the
      `news_id→outlet` map `ingest_mind --source-map` consumes). Unit-tested end-to-end.
      **Corrected (2026-06-30): "point it at EB-NeRD" was wrong** — EB-NeRD (Ekstra Bladet)
      and Adressa are *single-publisher* (no lean variation), and multi-publisher *click*
      sets anonymise the provider. So a ready catalog doesn't exist; the only realistic
      unblock is **resolving MIND's MSN articles → original providers**. **Spike built AND
      RUN (2026-07-01) → confirmed unreachable.** `examples/resolve_msn_publisher.py` +
      notebook `# 7f` fetch each article's *real* MSN URL (from the `news.tsv` `url` column,
      e.g. `assets.msn.com/labs/mind/AAJgNxm.html` — note the id is the MSN id, **not** the
      `Nxxxx` news_id, which was an early bug) and parse the outlet (JSON-LD / og:site_name /
      provider JSON / canonical host / byline) into the `news_id→outlet` source-map. Ran on
      real MIND in Colab: **HTTP 409 (Conflict) on every snapshot** — a gated response a
      browser UA does not bypass (Microsoft returns the same 409 for the dataset blob, whose
      body reads "public access is not permitted" — `# 2` — so almost certainly the same
      public-access lockdown, not a bot block), so **the outlet path is a confirmed dead end
      on MIND without Microsoft-issued credentials.** The five-strategy parser is unit-tested (14 tests) and reusable on any
      *non-gated* catalog. This was the hybrid's high-confidence branch; the AI-path arm
      (confidence-*weighting*) ships in `health_report.py --confidence-csv` and stands as
      the axis MIND actually supports. _Recommendation: **close** — record the 409 finding;
      the ideology story rests on the validated behavioral Politosphere axis._

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

## Limitations → resolution roadmap (what can actually be fixed)

A prioritized split of the project's limitations by *how* they resolve. Full statements:
`docs/RESULTS.md` §Limitations and the paper's §Ethics/Limitations.

**✅ Resolvable within the project (effort / compute / data-source — not new science):**
- [ ] **Weak text-lean axis → outlet-first axis.** *Highest-leverage, already demonstrated.*
      Qbias shows an outlet lookup hits **κ=0.84** vs AllSides gold where the text classifier is
      near-chance (**κ≈0.007** headline, **0.001** at 256 tok). Adopt outlet-lean wherever
      publishers are recoverable — blocked on MIND by the 409 gate, but works on any corpus that
      ships outlets.
- [ ] **Firm up the behavioral axis** — label more than the current **n=20** subreddits to
      tighten `lean_corr = 0.57 ± 0.19`.
- [ ] **Generality of the ideological half** — add a **2nd news corpus** beyond US-2019.
- [x] **Qbias "labels vs classifier?" — RAN (`# 7h`).** Answer is *both, partially*: a 2nd
      AllSides model (premsa) gets Spearman 0.22 with the body (vs politicalBiasBERT's ~0), so
      text carries a **weak** signal and politicalBiasBERT was partly miscalibrated — but even
      the better model is ~4× below the outlet, so outlet-first holds. **Closed as won't-do:** a
      long-context / full-article test isn't even runnable on Qbias — its `text` is a short
      excerpt (that's why 512 == 256) — so it needs a *new* full-text+gold corpus; and even a
      win wouldn't change the design (MIND has only headlines, and the outlet already wins at 0.918).
- [ ] **Return/retention signal** (inconclusive, 3-file window) — a **longer Politosphere window**.
- [ ] **Register/emotion synthetic-or-noisy; Source Diversity n/a on MIND** — run the real
      classifiers on real full text / use a corpus with outlets (the sim already fills Source Div).

**🟡 Resolvable only via a real deployment (the product path — resolves as the MVP gets traffic):**
- [ ] **Satisfaction self-selection** (82% is an *upper bound* — only the ~9% who already cross)
      — needs an **A/B test** recommending bridges to *random* users for a causal reception estimate.
- [ ] **Depolarization claim** (rests on the simulation) — needs real longitudinal opinion-change data.
- [ ] **Simulator circularity + coarse per-user signal** — replaced by real behaviour *by design*.

**🔴 Inherent / externally blocked (frame honestly, don't chase):**
- MSN publisher on MIND (HTTP 409) → needs MS credentials; work around with an outlet-bearing corpus.
- "Full-article" text-lean → **not runnable on Qbias** (short excerpts, 512==256); would need a
  *new* full-text+gold corpus **and** a long-context model, and still wouldn't beat the outlet
  (0.918) or help MIND (headlines only). **Won't-do.**
- Base-RWE accuracy vs the original *private* Twitter data → not public; use public proxies.
- Centre-ward effect partly geometric → the honest "control mechanism, not measured depolarization"
  framing **is** the resolution.
- Accuracy vs bridging (P3 leads raw accuracy) → a Pareto choice, not a bug.
- Licensed-data non-redistribution + dual-use → documented / mitigated, not eliminable.

## 5. Optional / future research — lower priority

- [x] **Real-log satisfaction signal — RAN on Politosphere; it's measurable and mostly
      positive.** The most-distinctive idea (satisfaction-calibrated exposure) was
      synthetic everywhere; `examples/satisfaction_probe.py` + `# 7` measure a proxy on
      **14.7 M real comments**. Politosphere **keeps** the fields (`score`/`created_utc`/
      `parent_id`). Findings: cross-cutting is **rare** (9 % of sided users) but, when it
      happens, **mostly welcomed** — **82 %** net-upvoted (vs 95 % same-side) with a
      *higher* reply rate (73 % vs 59 %), engagement not dogpiling — at a modest reception
      penalty. **Caveat (leads):** observed cross-cutting is **self-selected**, an upper
      bound, not the counterfactual of a recommended bridge; the *return* signal was
      inconclusive (short window). Folded into `RESULTS.md` (a *measured-not-simulated*
      subsection), the paper, and `PAPER.md`. **Closed the loop**
      (`examples/adaptive_satisfaction.py` + `# 7b`): the measured **`cross_welcomed_frac`**
      (hardened — upvoted AND not `controversiality`-flagged, so own-side brigading no longer
      counts as a welcome) now drives `AdaptiveRWEB`'s per-user exposure/epsilon (not the
      simulated walk) — vs a uniform recommender at the same average dose, the rank-weighted
      opposite-content reach rises with measured tolerance while uniform does not track it
      (real run 2026-07-02: low tercile adaptive 0.35 < uniform 0.51 → spared; high 1.29 >
      0.74 → boosted; `Spearman +0.66`, 52 % of served users carry a signal). Unit-tested.
      _(Caveats kept: self-selected + coarse per-user. Optional next: a longer comment window
      for the *return* metric.)_
- [ ] Health-report polish (e.g. a "you vs the average reader" population view)

## Product PoC — SEPARATE from research (not paper evidence)

- [x] **Agent-based synthetic-user simulator** — `examples/simulate_users.py` +
      `notebooks/product_simulation.ipynb` + `docs/PRODUCT_SIMULATION.md` (unit-tested).
      Real article catalog (Qbias gold lean + real outlets), **synthetic** agents with
      independent traits (viewpoint / topic interests / openness / outlet trust / quality
      pref / curiosity / activity / reading time / save-share-ignore); a choice model where
      openness gates the ideology kernel (validated: open agents click more cross-cutting).
      Emits a `MINDData` npz + per-user metrics + a probe-shaped CSV, so RWE eval / health
      report / AI coach / closed loop all run end-to-end on synthetic traffic. **Strictly a
      product stress-test / demo — NOT evidence** (RQ2 on synthetic clicks is circular by
      construction). Health report's Source Diversity finally populates (real outlets).

---

**Critical path:** §1 (verify, [you]) → §2 (I fold the numbers) gives a fully
reproduced, per-user-significant, three-dataset result + a working health-report PoC.
§3 (lean axis) is **done** — the behavioral axis validates across 5 seeds (`lean_corr
= 0.57 ± 0.19`); §4 (paper) is the path to submission. The one remaining axis task is
more labeled subreddits (n>20) to tighten the estimate.

_Last updated: 2026-07-01 (article-level reliability of the MIND text-lean axis via
`examples/lean_agreement.py`/`# 7e`: the two BERT bias models agree on the exact L/C/R
label at Cohen's κ=0.14 but flip Left↔Right only 2.2% (side-only κ=0.575) over n=2,955 —
per-article label unreliable, axis usable only in aggregate; folded into RESULTS/PAPER/
paper.tex. Prior 2026-06-30: 5-seed robustness on the behavioral axis: a 1-restart fit was
seed-unstable (collapsed to ~0 on 2/5 seeds); an unsupervised 8-restart likelihood
selection fixes it → `lean_corr = 0.57 ± 0.19` (min 0.33, max 0.82), RQ3 bridging robust
(`uw_shift` 1.97 ± 0.04, 5/5). Earlier that day: LLM convergent-validity check on the MIND
text-lean axis, Spearman −0.28 (n=120), reinforcing the suggestive-only reading)._
