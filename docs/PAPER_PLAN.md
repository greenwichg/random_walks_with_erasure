# Paper plan — "bridging backfire + guardrails" as a workshop paper

A concrete path from *this repo* to a submittable workshop paper. Read
`NOVELTY_CHECK.md` first — it establishes that the contribution must be
**empirical/integrative on real data**, not a new mechanism.

---

## 0. Reframe the contribution (the honest version)

Do **not** pitch: "we invent adaptive exposure / closed-loop guardrails." (Both
exist — Steck 2018, Stray 2021, arXiv 2408.16899.)

Pitch instead one of these, and pick **one**:

- **(A) Empirical study / benchmark.** *"On real news-sharing data, which
  depolarization strategy inside the RWE framework best trades accuracy for
  ideological diversity without triggering backfire — fixed bounded bridging,
  satisfaction-calibrated bridging, or closed-loop dose control?"* Contribution =
  a controlled, reproducible comparison + an open toolkit. **← recommended.**
- **(B) Resource / reproducibility.** Reproduce RWE on a *public* dataset (the
  original used private Twitter data nobody else can run) + release the extensions
  as an open, tested toolkit. Contribution = reproducibility + artifact.
- **(C) One sharp hypothesis.** *"Dwell-calibrated bounded bridging depolarizes
  ideological extremes without the backfire that fixed bounded bridging causes."*
  Contribution = a tested causal-ish claim on real logs.

All three need real data and honest positioning. (A) reuses the most of what you have.

---

## 1. Datasets

| Role | Dataset | Why / caveats |
|---|---|---|
| **Political news (primary)** | **MIND** (Microsoft News; 160k articles, 15M impressions, 1M users) + **outlet political-lean labels** (AllSides / Media Bias Fact Check), via a political/non-political → L/C/R classifier | Standard public news-rec benchmark; **caveat: limited ideological diversity, US-centric** — state this. <https://msnews.github.io/> |
| **News (non-US alt.)** | **EB-NeRD** (Ekstra Bladet, RecSys'24 Challenge) | Larger, non-US; check for lean labels. <https://arxiv.org/abs/2410.03432> |
| **Social graph (closest to original RWE)** | Public **Reddit** user–subreddit/post graph (political subreddits) or a public Twitter/X **polarization/controversy** dataset (Garimella et al.) | Recovers the user–item *graph* flavor RWE needs. **Caveat: Twitter/X redistribution is now restricted** — prefer Reddit dumps. |
| **Long-tail (non-political), as in the paper** | **MovieLens-1M**, **Yelp** | Public; directly supports the RWE-D long-tail claims (Gini/coverage/surprisal). |
| **Backfire ground truth (breaks circularity)** | **Bail et al. 2018** replication data (Dataverse); any **longitudinal panel** with repeated ideology measures | Lets you fit/validate the opinion model against a *real* experiment instead of your own simulator. |
| **Ideology ground truth** | **Barberá tweetscores** ideal points (users); **DW-NOMINATE / Voteview** (elites) | Validates the learned `IdeologyModel` positions against external scores. |

**Minimum viable**: MIND+lean (political bridging) **+** MovieLens-1M/Yelp (long-tail).
**Strong**: add a Reddit graph **and** validate the opinion model on Bail et al. data.

---

## 2. Real-data evaluation plan (claim → data → metric → baseline)

Metrics already implemented in `rwe/metrics.py`; reuse them.

| RQ | Claim | Dataset | Metrics (`rwe/metrics.py`) | Baselines |
|---|---|---|---|---|
| RQ1 | Positions are recovered accurately | MIND/Reddit + Barberá/DW-NOMINATE | corr. vs external ideal points; AUC | elite-only vs joint `IdeologyModel` |
| RQ2 | Long-tail diversity w/o killing accuracy | ML-1M, Yelp | Gini, AvgDeg, Coverage, Surprisal **vs** HR@10, NDCG | CF (`ItemKNN`), MF (`BPRMF`), `P3`, `RP3Beta`, `RWED` |
| RQ3 | Ideological diversity / shift | MIND+lean, Reddit | RecRange, `directed_shift`, UW/TW `weighted_*`, KS | above + `RWEB` (fixed ε) |
| RQ4a | **Adaptive** beats fixed ε & calibrated | MIND+lean | opposite-content fraction by user-tolerance bin; HR@10 | `RWEB` fixed-ε; **Steck calibrated rec**; "personalized diversity level" 2025 |
| RQ4b | **Guardrails** avoid backfire | Bail et al. / longitudinal; else published opinion model | polarization (std), ideology **drift** over rounds, dose trajectory | no-guardrail; **closed-loop controller (2408.16899)** if reproducible |

**Protocol**: temporal or leave-one-out split; ≥5 seeds; report mean±std and a
significance test (paired t / Wilcoxon); ablations (ε, `max_distance`, controller
gains). **Kill the circularity**: for RQ4b, fit the opinion model to *real* drift
(Bail et al.) or adopt **Chen et al. 2021** as-is and cite it; never evaluate a
self-designed controller only against a self-designed simulator.

---

## 3. Workshop-paper outline

> Working title: **"Bridging Without Backfire: Satisfaction-Calibrated Exposure and
> Closed-Loop Guardrails for Diversifying Recommendations."**

1. **Abstract** (~150w): problem (exposure can backfire) → approach (calibrated
   bounded bridging + monitored dose on RWE) → real-data result → caveat.
2. **Introduction**: filter bubbles → bridging → *backfire risk* (Bail 2018;
   heterogeneous effects) → gap: most bridging is un-calibrated and un-monitored →
   contributions (pick framing A/B/C; 3 bullets).
3. **Related work**: RWE & diversification (Paudel 2021; D-RDW 2025); calibrated/
   tolerance-aware diversity (Steck 2018; PFAR; 2025); depolarization & bridging
   systems (**Stray 2021/2023**); opinion dynamics w/ backfire (**Chen 2021**);
   closed-loop RS control (**2408.16899**, 2507.19792). *Make the delta explicit.*
4. **Background**: RWE recap (`P=D⁻¹Aᴳ`, `Pᵏ`, erase `Q`, closed form) — cite, brief.
5. **Method**: (5.1) satisfaction-calibrated ε `AdaptiveRWEB`; (5.2) bounded bridging
   `max_distance`; (5.3) closed-loop guardrails (drift / engagement controllers).
   Be explicit which parts are *yours* vs *adopted*.
6. **Experimental setup**: §1 datasets, §2 metrics/baselines/protocol.
7. **Results**: RQ1–RQ4 with the tables/plots from this repo *regenerated on real
   data* (the deck's diagrams become real figures).
8. **Ethics & limitations**: dual-use of polarization tooling; synthetic→real gap;
   US-centric data; satisfaction proxy validity; no claim to platform-scale effects.
9. **Conclusion** + **Reproducibility statement** (link the repo, tests, seeds).

What transfers from this repo with ~no change: the method code, `metrics.py`, the
8 diagrams (as figure *templates*), the worked example, the test suite. What must be
*redone*: every number, on real data.

---

## 4. Realistic venues (topically matched)

- **ICWSM** (incl. dataset/short papers) — strongest topical fit (polarization, social).
- **EAAMO** (Equity & Access in Algorithms, Mechanisms, and Optimization) — depolarization fits squarely.
- **RecSys workshops**: **NORMalize** (normative RS), **FAccTRec**, **PERSPECTIVES**, **RS4Good**; or RecSys **LBR** (short).
- **The Web Conf / WSDM / CIKM** workshops or **short/applied** tracks.
- **Reproducibility / resource tracks**: **ECIR** reproducibility; **SIGIR** resource.
- **FAccT**, **CHI LBW**, **CSCW**, **NeurIPS/ICML** algorithmic-fairness workshops.
- ⚠️ Avoid pay-to-publish venues that solicit you and promise fast acceptance; check
  against Beall's-list-style resources and the venue's real program committee.

---

## 5. Next steps (concrete)

1. **Decide framing** A / B / C (recommend **A**) and a target venue + deadline.
2. **Stand up MIND + lean labels** → reproduce RQ2/RQ3 numbers (1–2 weeks).
   *Status: the loader exists* — `rwe/mind.py` (`load_mind`, `MINDData`) +
   `examples/ingest_mind.py` parse a MIND release into a click `Dataset` + a
   political mask + outlet-lean `item_positions`, ready for `FeedbackGraph`/`RWEB`.
   Two positioning paths: **(1)** outlet-lean join (supply `--source-map`, since
   MIND URLs are MSN URLs without the publisher); **(2)** `--ideology` /
   `MINDData.fit_ideology`, which learns user+item positions from **clicks alone —
   no outlet labels needed**, plus a `lean_corr` check against any leans you do
   have. So the external-data dependency is now optional. **Eval driver ready:**
   `examples/eval_mind.py` takes an ingested `.npz`, runs the baselines + RWE-D/RWE-B
   and prints/saves the RQ2 (accuracy + long-tail) and RQ3 (ideological) tables. Next:
   download MINDsmall, ingest (`--ideology`), and run it to get the first real numbers.
3. **Add the calibrated-rec baseline** (Steck) and the **fixed-ε** baseline for RQ4a.
4. **Get a backfire anchor** (Bail et al. data or adopt Chen 2021) for RQ4b — this is
   the credibility linchpin.
5. **Write §3 related work early** — it's where reviewers decide if you have a delta.
6. **Loop in an advisor/collaborator** for authorship norms and a co-sign on novelty.

> Reality check: items 2–4 are *weeks-to-months* of real work and are the difference
> between "nice repo" and "submittable paper." The code is done; the empirical
> evidence is not.

_Last updated: 2026-06-23._
