# Bridging Without Backfire: Bounded Opposite-View Exposure in Random-Walk Recommenders

**Sai Sanath Erram** — Amrita Vishwa Vidyapeetham — `erram.sanath@gmail.com`

> **Draft (workshop / short-paper length).** Built on the implementation in this
> repository; numbers are from `docs/RESULTS.md`. Honest-framing notes to the author
> are marked **[note]**. Recreates and *extends* Paudel & Bernstein (WWW'21); the
> base RWE method is theirs, the empirical study and the bounded-bridging analysis
> are this paper's.
>
> **➡️ The submission build is the LaTeX in [`docs/paper/`](paper/)** (ACM `sigconf`
> + `references.bib`), which additionally folds in the **MovieLens-1M** replication
> and the **per-user significance**. This Markdown is the prose source of record;
> keep the two in sync when the numbers change.

## Abstract

Personalized recommenders can be steered to expose users to opposing viewpoints
("bridging"), but naively pushing users toward the other side may *backfire* and
increase polarization (Bail et al., 2018). We study **how far** opposite-view
content should be pushed. Using Random Walks with Erasure (RWE; Paudel & Bernstein,
2021) on the public MIND news dataset — with article ideology estimated from text
— we show that (i) the long-tail variant RWE-D is the strongest long-tail
diversifier at accuracy parity with a degree-reweighted baseline, and (ii) the
bridging variant RWE-B produces by far the largest weighted shift of *extreme*
users across the political centre while preserving accuracy. Critically, RWE-B's
"not too far" bound `d` acts as a **control knob**: as `d` tightens, recommendations
move monotonically from the *opposite extreme* toward the *centre* (uw-recs
0.77→0.27, 7-seed runs) while accuracy rises. Pairing this with an
assimilation–contrast opinion-dynamics simulation — in which near-centre exposure
depolarizes and opposite-extreme exposure backfires — we argue `d` is the lever
between the backfire and depolarizing regimes. We release an open, reproducible
pipeline. The long-tail result replicates on **three independent datasets** (MIND,
MovieLens-1M, Reddit Politosphere). We are explicit about the central limitation: of
three axis constructions, text-lean and news co-click come out *topical*, but a
**Reddit behavioral ideal point validates** (`lean_corr = 0.57 ± 0.19`, cleanly ideological
extremes) once low-signal subreddits are filtered — giving an independent RQ3 on a
validated axis where RWE-B again bridges hardest. The flagship MIND RQ3 still rests on
the weak text-lean axis (so those reads are suggestive), and the centre-ward effect is
partly geometric, so the durable contribution is the bounded-bridging *mechanism*
(robust on any 1-D axis), now demonstrated on a validated ideological axis.

## 1. Introduction

Recommenders that optimize engagement tend to narrow what users see ("filter
bubbles"), and a large literature now studies whether and how to **bridge** users
across ideological divides [Stray 2021; Stray et al. 2023]. The catch is that
exposure to opposing views can *increase* affective polarization rather than reduce
it — the **backfire effect** [Bail et al. 2018] — and the effect is heterogeneous:
moderates depolarize while extremes polarize. This raises a design question that is
rarely quantified on real recommendation data: **not whether to bridge, but how far**
— how aggressively opposite-side content should be surfaced.

We study this within **Random Walks with Erasure (RWE)** [Paudel & Bernstein 2021],
a diversification framework in which an "erasure" matrix `Q` re-weights a bipartite
user–item random walk to favour long-tail (RWE-D) or opposite-side "bridge" items
(RWE-B). RWE-B already includes a "different but not too far" bound; we make that
bound the object of study.

**Contributions.** This is an *empirical, integrative* study, not a new mechanism
(see §2). Concretely:

1. An **open, reproducible pipeline** for evaluating RWE diversification on the
   public MIND news corpus, with article ideology estimated from text (no private
   data, no outlet labels required).
2. An **empirical characterization of the bounded-bridging knob** on real data:
   tightening RWE-B's `max_distance` monotonically pulls recommendations from the
   opposite extreme toward the centre, *without* costing accuracy.
3. A **link to opinion dynamics**: an assimilation–contrast simulation in which
   near-centre exposure depolarizes and opposite-extreme exposure backfires,
   identifying `d` as the control between the two regimes.

## 2. Related work and our delta

**RWE and diversification.** RWE [Paudel & Bernstein 2021] is our base method; we
reuse it and do not claim it. Diversity-driven random walks for news have since been
explored (D-RDW, 2025). **Calibrated / tolerance-aware diversification** [Steck 2018;
PFAR] personalizes how much diversity each user gets — we *use* this idea, not invent
it. **Depolarization and bridging systems** are an active agenda: Stray (2021)
explicitly proposes monitoring polarization in a feedback loop, and network-aware
closed-loop controllers for polarization exist (e.g. arXiv:2408.16899, 2024).
**Opinion dynamics with backfire** is formalized by Chen et al. (2021)
(bounded-confidence assimilation plus a backfire threshold); our simulation is in
that family. **[note]** Because each ingredient has close prior work, our framing is
deliberately *empirical*: the contribution is the real-data study of the
"how far" question and the reproducible toolkit, not a novel algorithm.

## 3. Background: Random Walks with Erasure

For a bipartite user–item feedback graph with row-normalized transition matrix
`P = D⁻¹Aᴳ`, a `k`-step walk from user `s` gives item visitation `p = Pᵏ[s,·]`. RWE
introduces an erasure matrix `Q ∈ [0,1)`: each round, a `q`-fraction of mass is
removed and restarts, which (telescoping a geometric series) yields the closed-form
score `score(i) = p_i(1−q_i) / (1 − Σ_j p_j q_j)`. Two strategies instantiate `Q`:

- **RWE-D (long-tail):** `Qᴰ_i = 1 − 1/deg(i)ᵝ` suppresses popular items.
- **RWE-B (bridging):** non-bridge items get a high erasure `ε`; *bridge* items —
  on the opposite side of the population centre from the user and within a bound
  `d = max_distance` ("not too far") — are kept with weight proportional to their
  ideological similarity to the user.

`d = ∞` recovers unbounded bridging; small `d` admits only opposite-side items
*close to* the user (hence near the centre, for an extreme user).

![**Figure 1.** The RWE "tax" mechanism: a `k`-hop walk reaches items; an erasure
`q` is removed each round and restarts, so high-tax items are suppressed and low-tax
"bridge" items surface.](images/rwe_flow.png)

## 4. Method: three extensions (one evaluated on real data)

On top of base RWE we implement three extensions; this paper evaluates the second on
real data and the others in simulation.

1. **Satisfaction-calibrated exposure** (`AdaptiveRWEB`): a per-user `ε` set from a
   browsing-walk "satisfaction" signal, so low-tolerance users receive less opposite
   content. *(Controller simulated, but the signal is now empirically motivated: on
   14.7M Politosphere comments, cross-cutting participation is rare (9% of sided users)
   yet mostly welcomed — 82% net-upvoted vs 95% same-side, higher reply rate, not
   dogpiling; self-selected, so an upper bound. See `RESULTS.md`.)*
2. **Bounded bridging** (`max_distance`): the focus of this paper — see §3, §6.
3. **Closed-loop guardrails** (`BackfireMonitor`, `EngagementGuardrail`): per-user
   controllers that cut the bridging "dose" when an ideology-drift or
   engagement-drop signal indicates backfire — an implementation of the monitoring
   loop proposed by Stray (2021). *(Simulation.)*

## 5. Experimental setup

- **Data:** MIND-small (public). Political articles only (sub-category
  *politics*/*elections*); minimum 10 clicks per user/item; 15 000 users sampled.
  Evaluated subset: **8 415 users, 1 019 political articles, 29 635 clicks**.
- **Ideology positions:** a pretrained political-bias text classifier
  (`politicalBiasBERT`, LEFT/CENTER/RIGHT) over each article's title+abstract, mapped
  to a continuous lean in ≈[−2, 2]. **[note]** This axis is a *weak proxy*: 743/1019
  articles scored near-centre; validated against 40 independently-labelled headlines
  it gives **Spearman 0.27, 75 % sign-agreement on non-neutral items**. User
  positions are the mean lean of their clicked articles.
- **Baselines:** ItemKNN, P3, RP³-β; plus RWE-D and RWE-B (`ε = 0.9`).
- **Metrics:** accuracy (AUC, HR@10, NDCG@10), long-tail diversity (Gini, coverage,
  avg item degree, surprisal), and ideological diversity (RecRange, directed shift,
  and the user-weighted UW-shift / UW-recs of the talk's Result IV).
- **Protocol:** 70/30 per-user split; **7 random seeds**, reported as mean ± std,
  with a Wilcoxon signed-rank test vs P3.

## 6. Results

### 6.1 RQ1 — Long-tail diversity (RWE-D)

| model | HR@10 | AUC | Gini | coverage | avg-deg ↓ | surprisal |
|---|---|---|---|---|---|---|
| ItemKNN | .108±.004 | .719±.002 | .406±.004 | .989±.002 | 118±2 | 8.51±.04 |
| P3 | **.196±.003** | **.771±.001** | .151±.001 | .909±.005 | 265±1 | 5.91±.01 |
| RP³-β | .070±.003 | .744±.002 | .683±.003 | .996±.002 | 93±1 | 8.61±.01 |
| **RWE-D** | .065±.003 | .743±.002 | **.708±.003** | **.996±.002** | **87±1** | **8.74±.01** |

RWE-D is the best long-tail diversifier on every axis, at AUC parity with RP³-β;
the diversity gaps are far larger than the seed std (all vs-P3 differences Wilcoxon
`p = 0.016`, the n=7 floor). Top-`k` hit-rate drops, the standard
diversity↔accuracy trade-off. **The long-tail win replicates on two further public
datasets** — MovieLens-1M (movies) and Reddit Politosphere (social) — so it holds on
**three independent corpora**, not just MIND (see `docs/RESULTS.md` for both tables).
Politosphere additionally carries a **validated** behavioral left–right axis
(`lean_corr = 0.57 ± 0.19`) and an independent RQ3 where RWE-B again bridges hardest
(§7, limitation 1).

That same validated axis powers the per-user **Information Health Report**
(`examples/health_report.py --domain reddit`): on Politosphere its **Viewpoint
Balance** and **Echo Chamber** metrics rest on the validated axis — the inverse of
MIND, where Variety works but the political metrics ride the weak text-lean proxy. It
cleanly separates a one-sided reader (#9553, Echo 45) from cross-cutting ones
(#7667 / #12757, Echo ≈ 88), so the recommender's diversification objective is made
legible per reader. The per-user reads stay directional (they inherit the axis's
imperfections — `lean_corr 0.57 ± 0.19` over 5 seeds, n=20 labels). See `docs/RESULTS.md` for
the table.

![Politosphere Information Health Report — two contrasting readers on the validated behavioral axis](images/polito_health_card.png)

### 6.2 RQ2 — Ideological bridging (RWE-B)

| model | RecRange | shift | **UW-shift** | UW-recs |
|---|---|---|---|---|
| ItemKNN | 2.57±.03 | .245±.007 | .379±.010 | .247±.007 |
| P3 | 2.48±.03 | .213±.003 | .339±.005 | .278±.006 |
| RP³-β | 2.54±.01 | .263±.002 | .401±.005 | .233±.003 |
| RWE-D | 2.55±.01 | .266±.003 | .405±.005 | .231±.004 |
| **RWE-B** | 2.10±.01 | **.637±.006** | **1.044±.007** | .768±.007 |

RWE-B produces ~2.6× the weighted bridging shift of any baseline (UW-shift 1.04 vs
≈0.4) while retaining accuracy (AUC .753, HR@10 .139). It *concentrates*
recommendations on the opposite side rather than widening the range, and unbounded
it overshoots to the opposite **extreme** (UW-recs .768, the highest) — the "naive
opposite-blast" failure mode.

![**Figure 2.** Accuracy vs long-tail diversity (left) and vs ideological bridging
(right) on MIND (7 seeds, mean ± std). RWE-D sits in the high-diversity region at AUC
parity with RP³-β; RWE-B achieves by far the largest bridging shift at competitive
accuracy.](images/paper_tradeoff.png)

### 6.3 RQ3 — How far? The bounded-bridging sweep

7-seed mean ± std as the bound `d` tightens:

| `d` | HR@10 | AUC | UW-shift | **UW-recs ↓** |
|---|---|---|---|---|
| ∞ | .139±.004 | .753±.002 | 1.044±.007 | **.768±.007** |
| 2 | .151±.004 | .756±.002 | .742±.006 | **.475±.006** |
| 1.5 | .162±.003 | .760±.002 | .551±.004 | **.334±.004** |
| 1 | .180±.004 | .766±.002 | .404±.004 | **.268±.005** |
| 0.5 | .194±.003 | .769±.002 | .343±.005 | **.276±.006** |
| *P3* | .196±.003 | .771±.001 | .339±.005 | .278±.006 |

**Tightening `d` monotonically pulls UW-recs 0.77 → 0.27** (each step far exceeds the
±.005 std) — recommendations move from the opposite extreme toward the centre —
while accuracy *rises* toward P3. The bridging magnitude softens with it, and by
`d ≲ 1` RWE-B collapses to ≈ P3. The **moderated-bridging window is `d ≈ 1.5–2`**:
still clearly bridging, recs pulled centre-ward, accuracy preserved.

![**Figure 3.** The bounded-bridging sweep (MIND, 7 seeds). As `d` tightens
(left → right), UW-recs falls from the opposite extreme toward the centre and the
bridging shift softens (left), while accuracy rises toward P3 (right). The shaded
band marks the moderated-bridging window `d ≈ 1.5–2`.](images/paper_sweep.png)

### 6.4 The opinion-dynamics link

In an assimilation–contrast simulation (Social Judgment Theory; cf. Chen et al.
2021), repeated **near-centre** exposure makes a population *converge* (depolarize),
whereas repeated **opposite-extreme** exposure makes it *diverge* (backfire). Since
§6.3 shows `d` controls exactly where RWE-B's recommendations land relative to the
centre, we read `d` as the **control between the backfire and depolarizing regimes**:
bounded RWE-B produces the near-centre exposure the model predicts is depolarizing,
at no accuracy cost. **[note]** This is a *combination* of real-data control and a
simulated outcome — not a measured opinion change in real users.

![**Figure 4.** Opinion-dynamics simulation (assimilation–contrast): bounded /
adaptive bridging lowers population polarization, while a naive opposite-blast raises
it.](images/opinion_dynamics.png)

## 7. Ethics and limitations

1. **The ideological axis is hard to recover from text — but behaviour validates
   it.** Of three axis constructions, two come out *topical* and one *validates*:
   (i) the **text-lean** classifier is a weak proxy (Spearman ≈ 0.27 vs human labels,
   ≈ 0 on a second blind set, and **−0.28 vs a 120-headline blind LLM second opinion** —
   it conflates *topic* with *stance*, coding anti-Trump *content* as left on an
   impeachment-heavy slice; and two independent bias classifiers over **n = 2,955**
   articles agree on the exact L/C/R label only at **Cohen's κ = 0.14** while flipping
   Left↔Right just **2.2 %** of the time (**side-only κ = 0.58**) — the *per-article*
   label is unreliable though its *direction* is moderately stable, usable only in
   aggregate; and on **human AllSides gold** (Qbias, *n* = 3,000) the classifier lands at
   near-chance from the *text at any length* — **κ = 0.007** from the headline and **κ = 0.001**
   from the full article body, collapsing to *centre* both ways — whereas an **outlet lookup**
   recovers the same gold at **κ = 0.84 / side-only κ = 1.00**: the lean lives in the
   *publisher*, not the words, which is why text-lean is a weak proxy and an outlet-first axis
   is the fix); (ii) the **MIND
   co-click** ideal point is topical (`r = 0.37`); (iii) a **Reddit Politosphere
   behavioral** ideal point built to fix this **validates** (`lean_corr = 0.57 ± 0.19`
   against labeled subreddit leans, cleanly ideological extremes — communism/anarchism
   left, Trump/Farage/conservatives right). The lesson is itself a finding: a
   left–right axis is recoverable from explicit community-endorsement behaviour, not
   from text or news co-clicks. Honest caveats on the validated axis: it is
   threshold-sensitive (at a looser filter the same fit gave `lean_corr = 0.13` with
   scrambled extremes), was fit-sensitive until we kept the highest-likelihood of 8
   restarts (a 1-restart fit collapsed to ~0 on 2/5 seeds; the restart selection is
   unsupervised — data likelihood, not labels), and rests on n=20 labels. The bridging
   itself is robust (`uw_shift` 1.97 ± 0.04, 5/5 seeds). **So the flagship
   MIND RQ3 (text-lean) is suggestive**, but the bounded-bridging *mechanism*
   (limitation 2, robust on any 1-D axis) is independently demonstrated on the
   *validated* Politosphere axis, where RWE-B again bridges hardest (UW-shift 1.932).
2. **The centre-ward effect is partly geometric.** A smaller bound mechanically
   forces opposite-side items closer to the user on *any* 1-D axis (it also appears
   on an unsupervised co-click axis). So §6.3 demonstrates a robust *control
   mechanism*, not by itself a measured ideological depolarization; the
   depolarization claim rests on the simulation.
3. **Significance and generality.** Differences are stable across 7 random splits
   (Wilcoxon `p = 0.016`, the n=7 floor) **and** per-user (paired Wilcoxon, n ≈ 2,546,
   `p ≪ 1e-60`); the long-tail result replicates on **three datasets** (MIND,
   MovieLens, Politosphere). The flagship *ideological* half rests on the weak MIND
   text-lean axis (so it is suggestive), but is independently confirmed on the
   *validated* Politosphere behavioral axis (limitation 1).
4. **Scope and dual use.** US-2019 news for the ideological analysis; the base RWE
   accuracy is not validated against the paper's private Twitter data. Tools that
   steer ideological exposure are dual-use; we frame this as *reducing* backfire and
   recommend deployment only with the kind of monitoring Stray (2021) proposes.

## 8. Conclusion

On real news-recommendation data, RWE-B bridges users across the political centre
far more than strong baselines while preserving accuracy, and its "not too far"
bound is a clean knob that moves recommendations between the opposite-extreme
("blast") and near-centre ("depolarizing") regimes. Combined with an
assimilation–contrast simulation, this argues that *bounded* bridging — not
maximal opposite-view exposure — is the safer design. We release the full pipeline.

## Reproducibility

All experiments reproduce via `notebooks/run_mind_eval.ipynb` (downloads MIND from
the official source) → `examples/classify_lean.py` → `examples/ingest_mind.py
--positions-csv` → `examples/eval_mind.py [--seeds 7] [--sweep-max-distance …]` →
`examples/validate_lean.py`. Code, tests, and `docs/RESULTS.md` accompany this paper.

## References (to complete)

- B. Paudel and A. Bernstein. Random Walks with Erasure. *WWW* 2021.
- C. Bail et al. Exposure to opposing views on social media can increase political
  polarization. *PNAS* 2018.
- J. Stray. Designing Recommender Systems to Depolarize. *First Monday* 2021.
- J. Stray et al. Bridging Systems. Knight First Amendment Institute, 2023.
- X. Chen et al. Opinion Dynamics with Backfire Effect and Biased Assimilation.
  *PLOS ONE* 2021.
- H. Steck. Calibrated Recommendations. *RecSys* 2018.
- Mitigating Polarization in Recommender Systems via Network-aware Feedback
  Optimization. arXiv:2408.16899, 2024.
- F. Wu et al. MIND: A Large-scale Dataset for News Recommendation. *ACL* 2020.
- D-RDW: Diversity-Driven Random Walks for News Recommender Systems. 2025.

_Draft generated from docs/RESULTS.md and docs/NOVELTY_CHECK.md._
