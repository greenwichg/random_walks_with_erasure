# Novelty check — are the extensions publishable contributions?

A focused literature scan (June 2026) of the four extensions in this repo against
prior work, to answer: *what here is actually novel, and what is already published?*

> **Honesty note.** Characterizations below come from abstracts, snippets, and
> surveys, not always a full read. Verify each closest-prior-work by reading the
> paper before citing it as the baseline you beat. The bottom line, however, is
> robust: **the individual ideas are largely already in the literature — several
> very recently and very closely.** A publishable paper therefore cannot claim to
> *invent* them; it must contribute something empirical/integrative on top (see
> `PAPER_PLAN.md`).

---

## TL;DR verdict per extension

| Extension (this repo) | Closest prior art | Novel? |
|---|---|---|
| Base RWE / RWE-D / RWE-B | Paudel & Bernstein 2021 (the paper itself); **D-RDW 2025** already extends RWE-walks for news | ❌ reimplementation |
| **Adaptive exposure** (`satisfaction.py`, per-user ε) | personalized **diversity-tolerance** & **calibrated** recommendation (Steck 2018; PFAR; "personalized diversity level" 2025) | ⚠️ concept exists; only the *signal* is new (and synthetic) |
| **Bounded bridging** (`max_distance`, "not too far") | already in the RWE paper; **bounded-confidence / latitude-of-acceptance**; "two-sided" interventions; heterogeneous backfire (moderates vs extremes) | ❌ not new |
| **Opinion dynamics w/ backfire** (`opinion_dynamics.py`) | **Chen et al. 2021**, "Opinion Dynamics with Backfire Effect and Biased Assimilation" (first model to capture backfire; bounded-confidence + backfire threshold) | ❌ established model family |
| **Closed-loop guardrails** (`guardrails.py`) | **Stray 2021** explicitly proposes monitoring polarization in a feedback loop; **2408.16899 (2024)** implements a closed-loop controller; closed-loop opinion+RS (2507.19792, 2504.07105, 2025) | ❌ concept proposed + recently implemented by others |

**Net:** none of the four is a novel *method* on its own. Residual novelty, if any,
is in the **specific integration + operationalization on real data** — not the ideas.

---

## 1. Extending RWE is itself being done

- **Paudel & Bernstein, "Random Walks with Erasure," WWW 2021** — the base method.
  arXiv: <https://arxiv.org/abs/2102.09635>
- **D-RDW: Diversity-Driven Random Walks for News Recommender Systems (2025)** —
  builds on RWE-style random walks for news, adding *editor-controllable* diversity
  dimensions (which earlier RWE-style approaches lacked).
  arXiv: <https://arxiv.org/abs/2508.13035>
  → *Implication:* "we extend RWE with random walks" is already occupied; your delta
  must be sharper than "more knobs on RWE."

## 2. Adaptive / satisfaction-aware exposure — concept exists

- **Calibrated Recommendations**, Steck, RecSys 2018 — match per-user category
  proportions; the canonical "personalize the diversity level" idea.
- **PFAR — Personalized Fairness-aware Re-ranking** — assumes users have *different
  tolerance* to diversification and personalizes accordingly.
- **"Leveraging personalized diversity level for recommendations with knowledge
  graph," J. Intelligent Information Systems 2025** — estimates a *personalized
  diversity tolerance* and dynamically trades diversity vs accuracy.
  <https://link.springer.com/article/10.1007/s10844-025-01013-8>
- Surveys confirm "adaptive, personalized diversification by user tolerance" is an
  active, named direction: *Fairness and Diversity in RS: A Survey* (TIST 2024,
  <https://dl.acm.org/doi/10.1145/3664928>); *Ideological Isolation* survey 2026
  (<https://arxiv.org/abs/2601.07884>).

→ *Residual novelty:* your **satisfaction signal** — "pages read in the first
opposing community before exiting," derived from a browsing random walk — is a
specific operationalization I did not find named elsewhere. But (a) it is currently
**synthetic**, and (b) "tolerance-calibrated dose" is the prior-art frame it sits in.
To be a contribution it must be computed from **real behavioral logs** (dwell/return)
and shown to beat a fixed-ε and a calibrated-recommendation baseline.

## 3. Bounded bridging ("different but not too far") — not new

- The **"not too far" criterion is already in the RWE paper** (RWE-B weak-tie bridges).
- It is the **bounded-confidence / Social Judgment Theory latitude-of-acceptance**
  idea: influence only within ε of one's own position (assimilation), rejection/
  backfire beyond a threshold.
- Empirically motivated by **heterogeneous backfire**: balanced exposure depolarizes
  *moderates* but polarizes *extremes* — "Putting filter-bubble effects to the test"
  and panel experiments (<https://www.sciencedirect.com/science/article/pii/S2451958823000763>).
- **"Two-sided" interventions** (acknowledge a user's position before introducing the
  other side) are an explicitly proposed remedy to the reinforce-vs-backfire tension.

→ This is background, not contribution.

## 4. Opinion dynamics with backfire — established model

- **Chen et al., "Opinion Dynamics with Backfire Effect and Biased Assimilation,"
  PLOS ONE 2021** (arXiv 1903.11535) — *the first model to capture the backfire
  effect*; bounded-confidence positive interaction within a threshold, negative
  (diverging) interaction beyond a backfire threshold. Your `opinion_dynamics.py`
  (assimilation–contrast, acceptance/rejection latitudes) is in **exactly this
  family**. <https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0256922>
- Canonical empirical anchor: **Bail et al., "Exposure to opposing views on social
  media can increase political polarization," PNAS 2018** (the Twitter-bot study).

→ Your model is a re-derivation of a known family; cite Chen et al. and Bail et al.
and frame your simulation as *using* an established model, not proposing one. This
also directly answers the "circularity" reviewer critique — only if you adopt a
*published* model rather than your own.

## 5. Closed-loop guardrails — the idea is published; implementations exist

This is the most important finding, because it's your most distinctive-feeling piece.

- **Stray, "Designing Recommender Systems to Depolarize," First Monday 2021**
  (arXiv 2107.04953) — explicitly proposes **continuously monitoring affective
  polarization and driving recommender outcomes in a feedback loop**, at both
  managerial and algorithmic levels. That *is* your `BackfireMonitor`, conceptually.
  <https://arxiv.org/abs/2107.04953>
- **Stray et al., "Bridging Systems," Knight First Amendment Institute 2023** — the
  bridging-ranking research agenda your whole project sits inside.
  <https://knightcolumbia.org/content/bridging-systems>
- **"Mitigating Polarization in RS via Network-aware Feedback Optimization," 2024**
  (arXiv 2408.16899) — designs the recommender as a **dynamic feedback controller**
  that minimizes polarization using click feedback, with **closed-loop stability**
  proofs, validated on an **extended Friedkin–Johnsen** population. This is your
  "monitor a signal, adjust the dose, close the loop" — formalized and proven.
- **"Modelling the Closed-Loop Dynamics Between a Social-Media RS and Users'
  Opinions," 2025** (arXiv 2507.19792) and **"The Feedback Loop Between RS and
  Reactive Users," 2025** (arXiv 2504.07105) — closed-loop RS↔opinion modeling.

→ *Implication:* you **cannot** claim closed-loop backfire monitoring as novel. At
most you can claim a *specific, open-source, reproducible* controller (drift- and
engagement-triggered dose-cutting) **on top of RWE-B**, compared empirically against
these. Even then the bar is "useful integration + evidence," not "new idea."

---

## Adjacent depolarization-RS work you must cite (and are partly reinventing)

- *Bridging Viewpoints in News with RS*, RecSys 2024 workshop —
  <https://dl.acm.org/doi/fullHtml/10.1145/3640457.3688008>
- *FaDeRS: Fairness and Depolarization in RS* (2025).
- *Socially-Aware RS Mitigate Opinion Clusterization* (arXiv 2601.02412).
- *Recommender Systems for Good (RS4Good)* survey (arXiv 2411.16645).
- *Result Diversification in Search & Recommendation: A Survey* (arXiv 2212.14464).

---

## So where could a *real* contribution be?

Not in the mechanisms. Plausible, defensible angles:

1. **Empirical, reproducible comparison** of depolarization strategies *inside one
   framework (RWE)* on **real data**, with the satisfaction signal from **real
   behavioral logs** and backfire measured by **longitudinal ideology drift** — i.e.
   a *systems/benchmark/repro* contribution, not a method one.
2. **Operationalizing Stray's monitoring loop** end-to-end (signal → controller →
   ranker) as an **open-source toolkit**, and reporting what breaks on real data.
3. A **single sharp hypothesis** the literature hasn't nailed, e.g. *"satisfaction-
   (dwell-)calibrated bounded bridging depolarizes extremes without the backfire that
   fixed bounded bridging causes,"* tested on real users/logs with significance.

All three require real data and honest positioning. See `PAPER_PLAN.md`.

_Last updated: 2026-06-23._
