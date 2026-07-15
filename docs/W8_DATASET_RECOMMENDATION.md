# W8 — Behavioral Warm-Start Dataset Recommendation (Research, Docs Only)

**Status:** Research / architecture evaluation. No production code, no prototype, no
implementation. This document does **not** assume MIND is correct — it re-derives the choice from
our architecture and current (July 2026) dataset availability.

---

## 1. What our architecture actually needs (reason before listing datasets)

W8's goal is **not** generic news recommendation. It is a **behavioral User→item interaction
graph** that can replace/complement today's *simulated* graph, fed through the **existing**
pipeline:

- `rwe.graph.FeedbackGraph` — built from a **user×item click matrix alone** (binarized bipartite
  adjacency + transition matrix; `rwe/graph.py:47-71`). No text, no outlet, no labels required
  for the graph itself.
- `rwe.mind.fit_ideology` → `rwe.ideology.IdeologyModel` — fits latent user/item positions **from
  co-click structure alone** (`rwe/mind.py:322`). Outlet/lean labels are used **only to orient and
  validate** the (sign-arbitrary) axis via `lean_corr` — and need only a handful of labelled items.
- `eval_mind.py` / `rwe.experiment` — the eval harness, keyed on a `MINDData` container.

From this, the **three requirements that actually bind** (everything else is secondary):

1. **Graph substrate** — many users with *overlapping* items (co-click density), persistent user
   IDs. Without this the walk has nothing to propagate.
2. **A political subset** — so the recovered axis *can* be ideological.
3. **Ideological ground-truth labels** — even a small set — so `fit_ideology` can **orient** the
   axis and report `lean_corr`. **This is the requirement MIND fails** (established in the W8A
   audits: MSN URLs → no recoverable outlet → `lean_corr` is *structurally* `None`; the axis is
   unvalidatable as ideology). Our repo's own `rwe/mind.py` docstring says as much.

The datasets below are judged against these three — not against generic rec-sys leaderboards.

---

## 2. Dataset fact sheets (verified July 2026 where noted)

### MIND — Microsoft News Dataset
- **Availability / download:** Live — `msnews.github.io` + Azure blob (`mind201910small.blob.core.windows.net/release/`). Freely downloadable after accepting terms.
- **License / restrictions:** Microsoft Research License — **non-commercial research only**. Article **bodies withheld** (only title+abstract); the provided MSN-URL parser is **HTTP-409 gated** (our W8A audit: dead end without Microsoft credentials).
- **Interaction:** impression logs (shown + click) + user history. **~1M users, ~161k news, ~15M+ impressions** (MINDlarge; MINDsmall ~50k users).
- **Text:** title + abstract (no body). **Outlet:** ✗ (MSN aggregator). **Political subset:** ✓ (subcategories, e.g. `newspolitics`). **User history:** ✓. **Impressions:** ✓.
- **Actively used:** heavily (the field's default news-rec benchmark). **Papers:** Wu et al. 2020 (MIND); NRMS, LSTUR, NAML, Fastformer.

### Reddit Politosphere (+ Pushshift)
- **Availability / download:** Live — Zenodo `10.5281/zenodo.5851729`; underlying Pushshift dumps on Academic Torrents (2005→2025, updated Jul 2025) and `files.pushshift.io`.
- **License / restrictions:** Zenodo academic release; Pushshift-derived. **Gray area post-2023** — Reddit's data/API policy tightened bulk redistribution; academic use of historical dumps is widely practiced but not as clean as MIND's explicit research license. Pseudonymized.
- **Interaction:** comments as **user→subreddit endorsements** (author + subreddit). **605 political subreddits, 2008–2019**, millions of authors, hundreds of millions of comments. (Submissions variant → user→article-URL; see §6.)
- **Text:** comment text ✓ (article text only if a submission's link is fetched). **Outlet:** subreddit (or link-domain via submissions). **Political subset:** ✓ **100% by construction**. **User history:** ✓. **Impressions:** ✗ (no shown-not-clicked).
- **Actively used:** yes (computational social science / political NLP). **Papers:** Hofmann et al. 2022 (Politosphere); Waller & Anderson 2021 (community ideology embeddings).
- **Why it matters to us:** `examples/ingest_politosphere.py` **already** turns it into the `MINDData` container → the whole `FeedbackGraph`/`fit_ideology`/`eval_mind` pipeline "just works," and `examples/data/subreddit_lean.csv` gives **clean ideological ground truth** → `--ideology` **orients** the axis and reports `lean_corr` — exactly what MIND cannot do.

### EB-NeRD — Ekstra Bladet News Recommendation Dataset (RecSys Challenge 2024)
- **Availability / download:** Live — `recsys.eb.dk`. **License:** research portal terms. Anonymized (one-time salt).
- **Interaction:** impressions + history. **~1.1M users, ~125k articles, ~37.97M impressions** (6 weeks, 2023). **Text:** title + abstract + **body** + metadata ✓. **Impressions:** ✓. **User history:** ✓.
- **Outlet:** ✗ **single publisher** (Ekstra Bladet, Danish tabloid). **Political subset:** ✗ (no lean variation; Danish).
- **Actively used:** yes (RecSys Challenge 2024). **Papers:** Kruse et al. 2024 (EB-NeRD).
- **Verdict for us:** excellent modern news-reading dataset, **wrong axis** — single-publisher ⇒ no ideological variation to recover.

### Adressa (NTNU / Adresseavisen)
- **Availability:** research request from NTNU. **Interaction:** clicks + dwell. 1-week (~15k users) / 10-week (~2M users, ~48k articles, ~27M clicks). **Text:** ✓ (Norwegian). **Outlet:** ✗ single publisher. **Political subset:** ✗. **Impressions:** partial. **Papers:** Gulla et al. 2017.
- **Verdict:** single-publisher (our `rwe/mind.py` docstring already flags Adressa as no-lean-variation).

### Globo.com (Kaggle "News Portal User Interactions")
- **Availability:** Live on Kaggle. **License:** Kaggle terms. **Interaction:** sessions/clicks. ~314k users, ~46k articles, ~3M clicks. **Text:** pre-computed **embeddings only** (no raw text), Portuguese. **Outlet:** ✗ single portal. **Political subset:** ✗ (hard). **Impressions:** ✗. **Papers:** Moreira et al. 2018 (CHAMELEON).
- **Verdict:** single-portal, non-US, embeddings-only ⇒ no ideological axis.

### Plista (CLEF NewsREEL)
- **Availability:** **effectively defunct** — the NewsREEL challenge ended; no maintained public download. **License:** challenge terms. **Interaction:** German multi-publisher impression/click stream. **Text:** limited. **Outlet:** publisher present but access is the blocker. **Papers:** Kille et al. 2013.
- **Verdict:** not freely obtainable today; disqualified on availability/maintainability.

### Yahoo! R6A / R6B (Webscope, Front Page Today Module)
- **Availability:** Webscope portal (institutional / registered; access uncertain in 2024-26). **Interaction:** **contextual-bandit click log** — ~45.8M *visits* (May 2009), articles **anonymized** (feature vectors, no text/outlet), **no persistent user IDs / no user history**. **Political subset:** ✗. **Papers:** Li et al. 2010 (LinUCB).
- **Verdict:** **architecturally incompatible** — a bandit log, not a user×item graph; `FeedbackGraph` needs persistent co-click structure this dataset does not have.

### Outbrain Click Prediction (Kaggle 2016)
- **Availability:** Live on Kaggle. **License:** competition license — **redistribution/commercial restrictions** (legal risk). **Interaction:** massive — ~2B events / ~700M page views, cookie (uuid) users, documents across **many** publishers; `documents_meta` has `source_id`/`publisher_id` + topics/categories/entities. **Text:** ✗. **Outlet:** present but **numeric anonymized IDs (no names)** → cannot map to AllSides lean. **Political subset:** topic/category IDs exist but **anonymized** → hard to isolate "politics". **Impressions:** ✓ (shown recs). **Papers:** Kaggle solutions; NVTabular/DLRM benchmarks.
- **Verdict:** scale without interpretability — anonymized publishers/topics defeat both requirement 2 and 3.

### Twitter political / news-engagement datasets
- **Availability:** most ship **tweet IDs only** → require **rehydration via the X API** (paywalled since 2023) ⇒ violates "no proprietary APIs / freely obtainable." Some processed aggregates (e.g. ~6.5M news-engagement tweets, 7 yr, with source + partisan lean + anonymized users) exist. **Political subset:** ✓ strong. **Interaction:** social (retweet/URL-share), not article reading. **Papers:** Barberá 2015; recent engagement-forecasting work.
- **Verdict:** strongest political signal, but licensing/API risk is disqualifying for a freely-obtainable, offline, API-free requirement.

### Content-only corpora (not behavioral — noted for completeness / as label sources)
- **POLUSA** (0.9M US political articles, balanced by outlet + time), **AllSides** headline roundups (bias-labelled), a 28-US-agency 2023-24 crawl, and **Qbias** (which our repo **already uses** as the article catalog with gold AllSides lean). These have **outlet + lean but no user clicks** ⇒ they cannot be behavioral graphs; they are useful **lean-label / catalog** sources (Qbias is exactly what today's *simulated* graph is built over).

---

## 3. Scoring against OUR W8 architecture (1–5, 5 best)

| Dataset | Graph quality | Behavioral realism | Political suitability | Ease of integration | Licensing (low risk = high) | Maintainability | Fit for W8 |
|---|---|---|---|---|---|---|---|
| **Reddit Politosphere** | 4 | 3 | **5** | **5** | 3 | 3 | **Best axis + repo-ready** |
| **MIND** | **5** | **5** | 2 | **5** | 4 | 4 | Best reading behavior, no axis labels |
| EB-NeRD | 5 | 5 | **1** | 3 | 4 | 5 | Wrong axis (single-publisher) |
| Outbrain | 5 | 4 | 2 | 2 | 2 | 3 | Scale, no interpretability |
| Globo | 4 | 4 | 1 | 2 | 3 | 3 | Single-portal, non-US |
| Adressa | 4 | 4 | 1 | 3 | 3 | 3 | Single-publisher |
| Twitter (news-engagement) | 3 | 3 | 5 | 2 | 1 | 2 | API/ToS locked |
| Yahoo R6 | 1 | 3 | 1 | 1 | 2 | 2 | Not a user×item graph |
| Plista | 2 | 3 | 3 | 2 | 2 | 1 | Effectively defunct |

Only **two** datasets clear every hard requirement for *our* architecture, and they are **exactly
complementary**: MIND has the behavior but not the labels; Reddit has the labels but coarser
(social) behavior. Every other candidate fails a hard gate (single-publisher, anonymized,
not-a-graph, or API-locked).

---

## 4. Recommendation

### Primary — **Reddit Politosphere**
It is the only freely-obtainable, **pipeline-ready** dataset that supplies *both* real behavior and
the **clean ideological ground truth** needed to orient/validate the axis. It is the RWE paper's
native **elite-endorsement** setting, `examples/ingest_politosphere.py` already emits a `MINDData`,
and `subreddit_lean.csv` makes `lean_corr` **computable** — retiring the single risk that killed
MIND for W8. Caveat to state plainly: items are **subreddits** (social co-participation), so it
validates the **method + the ideological axis**, not the article-item space directly, and its
licensing is a **gray area** (Reddit post-2023 policy) rather than a clean research grant.

### Secondary — **MIND**
The best freely-available **real user→article news-reading** graph (impressions + history + text at
scale, native repo format). Use it to validate the **article-graph substrate + reading realism**,
accepting that its axis stays **unoriented** (`lean_corr = None`) — so on MIND we claim "a coherent
latent axis drives diversity," **not** "it is ideological." The axis-interpretability instrument
already added to `examples/w8a_prototype.py` (category η² / political-flag correlation) is the
substitute check on MIND.

These two together are not a hedge — they are the **correct experimental design**: each covers the
other's fatal weakness.

---

## 5. Final questions

1. **Should we continue with MIND?** **Yes — but demoted to SECONDARY**, as the news-reading-realism
   / article-graph check, with the explicit caveat that it cannot validate the ideological axis.
2. **Should we replace MIND?** **No — reprioritize, don't replace.** Add Reddit Politosphere as the
   *primary* axis-and-method validator (it is already wired in the repo); keep MIND for reading
   realism. "Replace" is only warranted for the *ideological* claim, which MIND never supported.
3. **Should we validate on two datasets?** **Yes — strongly.** MIND (behavior ✓ / labels ✗) and
   Reddit Politosphere (labels ✓ / reading-realism ✗) are complementary; a result that holds on
   **both** — RWE's diversity/anti-homogenization on real click topology (MIND) *and* an oriented,
   `lean_corr`-validated ideological axis on real political behavior (Reddit) — is far stronger than
   either alone, and directly satisfies decision-gate H1/H3.
4. **What would I choose designing W8 today?** Reddit Politosphere **primary** + MIND **secondary**
   for the near-term validation — and I would then **construct the objectively-best dataset for our
   exact goal**, which no off-the-shelf release provides: a **Reddit *submissions* user×article
   graph**, where each edge is a user engaging a submission that **links a news article** and the
   **outlet is the link's domain → a real AllSides lean** (via our existing `outlet_registry`). That
   yields **user×article + real multi-outlet lean + political + free + no proprietary API** — the
   only freely-obtainable path to *all three* binding requirements at once, and the closest public
   analogue to what production will actually be (real reads over a real multi-outlet catalog, per
   `docs/W8B_…`). It is obtainable today (Pushshift submissions carry URLs) but needs a new
   ingestion, so it is a **fast-follow**, not the day-one dataset.

---

## 6. Is anything "objectively better than MIND" for us? — Yes, on the dimension that matters

On raw news-reading behavior, MIND is excellent and remains our secondary. But on **the single
requirement that blocks W8's scientific claim — an ideological axis you can *orient and validate* —
Reddit Politosphere is objectively better**: it carries lean ground truth (`subreddit_lean.csv` →
`lean_corr`), it is political by construction, it needs **no** outlet-recovery (MIND's 409 dead
end), and it is **already integrated** in our codebase. The *ideal* target (Reddit-submissions
user×article with domain-derived AllSides lean) dominates MIND on **all three** binding
requirements simultaneously; it simply requires an ingestion we would build after the two-dataset
validation passes its gate.

---

## Sources

- [MIND: MIcrosoft News Dataset](https://msnews.github.io/) · [MIND license (MSR)](https://github.com/msnews/MIND/blob/master/MSR%20License_Data.pdf) · [Microsoft Research MIND paper](https://www.microsoft.com/en-us/research/publication/mind-a-large-scale-dataset-for-news-recommendation/)
- [EB-NeRD (RecSys Challenge 2024)](https://www.recsyschallenge.com/2024/) · [EB-NeRD paper (arXiv 2410.03432)](https://arxiv.org/abs/2410.03432)
- [The Reddit Politosphere (Zenodo 5851729)](https://doi.org/10.5281/zenodo.5851729) · [Politosphere paper (LMU ePub)](https://epub.ub.uni-muenchen.de/107434/1/19377-Article%20Text-23390-1-2-20220531.pdf) · [Pushshift Reddit dumps 2005–2025 (Academic Torrents)](https://academictorrents.com/details/30dee5f0406da7a353aff6a8caa2d54fd01f2ca1)
- [Outbrain Click Prediction (Kaggle)](https://www.kaggle.com/c/outbrain-click-prediction/data)
- [Yahoo! Front Page Today Module (Webscope R6)](https://webscope.sandbox.yahoo.com/catalog.php?datatype=r&did=49)
- [POLUSA dataset](https://dl.acm.org/doi/10.1145/3383583.3398567) · [Qbias dataset (arXiv 2311.17780)](https://arxiv.org/pdf/2311.17780)

*Documentation only. No production code, prototype, or dataset was created or modified. Availability
and licensing verified July 2026; re-check the portal terms before any download.*
