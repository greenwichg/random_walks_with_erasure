# Talk Walkthrough — *Random Walks with Erasure* (WWW 2021)

A slide-by-slide companion to Bibek Paudel's Web Conference 2021 talk
*"Random Walks with Erasure: Diversifying Personalized Ranking"*
(Bibek Paudel, Stanford University; Abraham Bernstein, University of Zürich).

For each slide this records **what is on the slide**, the **speaker's notes**
(from the talk narration), and **→ In this project** — how that point maps to
this repository, so the slides double as an implementation checklist.

> **Status:** slides 1–22 of the deck (introduction, ideology detection, the
> datasets/results, the diversification strategies, the RWE algorithm with its
> worked long-tail example, the experimental setup, and Result I). Remaining
> slides — Results II–IV and the conclusion — will be appended as they are added.
>
> *(The deck re-shows the "Normative goals" slide (= Slide 15) and the "Our
> Approach" slide (= Slides 6/14) as section transitions; they are not
> re-listed below.)*

---

## Slide 1 — Title

**On the slide.** *Random Walks with Erasure: Diversifying Personalized Ranking.*
Bibek Paudel (Stanford University) · Abraham Bernstein (Universität Zürich) ·
Web Conference (WWW) 2021.

**Speaker notes.** Introduces the work on *diversifying personalized ranking
using random walks with erasure*, joint work between Stanford and the University
of Zürich.

**→ In this project.** The whole repository is an implementation of this paper:
the core method in `rwe/` plus two extensions and a beginner guide
([`GUIDE.md`](../GUIDE.md)) and technical reference ([`README.md`](../README.md)).

---

## Slide 2 — "In the beginning …" (2010–2013)

**On the slide.** A protest placard listing *"REVOLUTION TOOLS: AK-47 ✗,
Machete ✗, Twitter ✓, Facebook ✓"*, beside *The Economist*'s 2013 cover
**"The march of protest."** Caption: **2010–2013**.

**Key point.** A decade ago, new social networks and online media were widely
**praised as enablers of democracy and social cohesion** — tools of protest and
participation.

**Speaker notes.** "Ten years ago … new social networks and online media were
being praised as enablers of democracy and social cohesion."

---

## Slide 3 — "Then something went wrong …"

**On the slide.** *The Economist*'s 2017 cover **"Social media's threat to
democracy,"** beside Jaron Lanier's book **"Ten Arguments for Deleting Your
Social Media Accounts Right Now."**

**Key point.** Within a few years the **same technologies were criticised** for
**increasing polarization and harming society** — raising questions about the
diversity, fairness and accountability of the information these systems serve.

**Speaker notes.** "In a matter of few years something seemed to go wrong. The
same technologies were now criticized for increasing polarization and harming
the society."

---

## Slide 4 — "How to bridge the divide?"

**On the slide.** Two overlapping blue/red heads and a fraying blue rope about to
snap. Three bullets:

- **High political polarization** increases suspicion, hostility and incivility.
- The **quality of democracy depends on political participation**.
- Political understanding and participation may **increase with cross-cutting
  awareness and discussions**.

**Key point.** Polarization is harmful and self-reinforcing; **cross-cutting
awareness** — exposure to viewpoints from different sides — can increase
political understanding and participation. This is the normative motivation for
*bridging*.

**Speaker notes.** "High political polarization leads to increase in suspicion
and hostility … the quality of democracy depends on political participation …
cross-cutting awareness and cross-cutting discussions lead to increased
political understanding and participation. By cross-cutting awareness we mean
exposure to viewpoints from different sides and different perspectives."

**→ In this project.** This is precisely what the **RWE-B bridging strategy**
targets: `rwe/random_walk.py::RWEB` promotes reachable *opposite-side* content
(weak-tie "bridges") rather than reinforcing a user's own side. The two
extensions go further — `satisfaction.py` and `agent_sim.py` model how much
cross-cutting content a user will actually engage with, so the "dose" can be
tuned per user.

---

## Slide 5 — "Challenge of political content"

**On the slide.** A screenshot of **AllSides** showing one story framed *From the
Left / From the Right / From the Center*. Below it, two **Telegraph** articles
about Brexit: *"Nigel Farage: £350 million pledge to fund the NHS was 'a
mistake'"* and *"Britain remains a great country with a great future."*

**Key point.** The obvious fix — *mix content using the known political slant of
each outlet* (as AllSides does) — is **not enough**. Two articles **from the same
outlet** (The Telegraph) were shared by people with **opposite** positions on
Brexit. So **pre-existing labels / fixed outlet slants are unreliable**:
ideological positions are **specific to issues and events** and must be learned.

**Speaker notes.** "The solution seems really simple — we know the political
slant of different news outlets … we can just mix and recommend information from
all these outlets … but this approach suffers from a major problem. These two
articles from the same newspaper … were shared by people with radically
different positions about the Brexit referendum. This shows that pre-existing
labels or known slants are not enough; these positions can change based on the
issues and based on specific events, and we need to know them."

**→ In this project.** This is the motivation for learning positions rather than
hard-coding them. `rwe/ideology.py::IdeologyModel.fit(R, S)` estimates the
ideological position of every user, elite and piece of **content** *from the
event-specific endorsement (`R`) and content-share (`S`) graphs* — exactly the
"learn issue-specific positions" requirement this slide argues for. (The
Telegraph example here is Table 1 of the paper.)

---

## Slide 6 — "Our Approach"

**On the slide.** Three numbered steps:

1. **Identify ideological positions** of *both political elites AND political
   content* using social media discussions about specific political events.
2. **Choose a diversification strategy** — simply recommending content from
   different political sides may not work.
3. **Random-Walk with Erasure (RWE)** — recommend diverse content using *weak
   ties*, based on the diversification strategy and the identified positions.

**Key point.** The method has three parts: *learn positions → pick a strategy →
run RWE*. Crucially, positions are learned for **content too**, not just elites,
and naive "mix both sides" recommendation is rejected in favour of **weak-tie**
bridging.

**→ In this project.** The three steps are the three pillars of the codebase:
(1) `rwe/ideology.py` (`IdeologyModel` → `φ` for elites, `ψ` for content);
(2) the diversification strategies `rwe/random_walk.py::RWED` / `RWEB` plus the
generalised erasure matrix `Q`; (3) `RWE`/`RWEB` themselves. `GUIDE.md` is
organised around the same three steps.

---

## Slide 7 — "Ideological positions of Users, Elites, Content"

**On the slide.** Two bullets and the 1-D scale, plus an annotated tweet:

- Users **U** endorse elites **E** → endorsement graph **R**.
- Users **U** endorse content **I** → endorsement graph **S**.
- The number line places `u1` on the left and `u2, u3, u4` on the right (the
  paper's Figure 1).
- A retweet is dissected into three roles: the **User** (the retweeter, *Stefan
  Rahmstorf*), the **Elite** (the original author, *@PIK_Klima*), and the
  **Content** (the URL, *www1.wdr.de/…*).

**Key point.** Every retweet yields two signals — a *user→elite* endorsement
(graph **R**) and a *user→content* endorsement (graph **S**) — and the goal is to
place users, elites and content on one shared 1-D ideological scale.

**Speaker notes.** "A tweet shared by a user … originally posted by the account
we refer to as the elite … the URL in the post is the content. This gives rise
to two kinds of graphs — the user-elite endorsement graph and the user-content
endorsement graph — and in the end we put all of them on this one-dimensional
scale."

**→ In this project.** `IdeologyModel.fit(R, S)` consumes exactly these two
graphs (`R` = retweet/elite-endorsement, `S` = URL/content-share, documented in
`rwe/data.py`). The 1-D scale is the `ideology_scale.png` diagram in `GUIDE.md`,
and we separately verified that our code reproduces every relationship the paper
states about this Figure 1 (leaning, distances, similarities, bridges).

---

## Slide 8 — "Identify ideological positions of users and items" (build 1)

**On the slide.** An animation of the **user-content endorsement graph**: users
*Anne, Bert, Alice, Party* on the left linked to content items on the right.

**Key point.** Learning works on this bipartite endorsement graph: users who
endorse the same content are pulled close together, and a user is assumed to sit
near the content they endorse.

**Speaker notes.** "Based on content endorsement we want to minimize the distance
between similar users, and we assume that a user and content have similar
ideological positions … smaller [distance] in this ideological space."

**→ In this project.** This bipartite endorsement graph is the input matrix the
model fits (`S` for content, `R` for elites); the same bipartite structure is
`rwe/graph.py::FeedbackGraph`.

---

## Slide 9 — "Identify ideological positions of users and items" (build 2: the models)

**On the slide.** The two spatial-following (ideal-point) models:

- **Content endorsement:** `p(S_{u,c}=1 | θ_u, ψ_c, α_u, γ_c) =
  1 / exp(−‖θ_u − ψ_c‖² + α_u + γ_c)`
- **Elite endorsement:** `p(R_{u,e}=1 | θ_u, φ_e, α_u, β_e) =
  1 / exp(−‖θ_u − φ_e‖² + α_u + β_e)`

**Key point.** The probability that a user endorses an item *decreases with the
squared ideological distance* between them (plus per-node bias terms). Closer in
ideology ⇒ more likely to endorse.

**→ In this project.** These are `Pi_R` and `Pi_S` in `rwe/ideology.py`
(`Pi = -(theta - phi/psi)**2 + alpha + beta/gamma`), matching paper eqs. 6 and 9.
*(The slide writes `1/exp(·)`; the `log(1 + exp(Π))` term in the joint objective
on the next slide confirms it is the logistic `σ(Π)`, which is what the code
uses. The bias subscripts on the slide, e.g. `α_c`, read as minor typos — the
user bias is `α_u`.)*

---

## Slide 10 — "Identify ideological positions of users and items" (build 3: joint learning)

**On the slide.** Both models plus the **joint learning** objective:

```
arg min  p(θ, φ, ψ, α, β, γ | R, S)  ∝
 θ,φ,ψ,α,β,γ
        μ · Σ_(u,e)∈U×E [ a_{u,e} Π_{u,e} − log(1 + exp(Π_{u,e})) ]
          + Σ_(u,c)∈U×C [ b_{u,c} Π_{u,c} − log(1 + exp(Π_{u,c})) ]
          − (λ/2)‖θ‖₂ − (λ/2)‖φ‖₂ − (λ/2)‖ψ‖₂
```

and the conclusion **`⟹ sim(u, c)` — similarity of political stance of `u` and
`c`**, with users/items collapsed onto the shared horizontal axis.

**Key point.** The elite and content models are **learned jointly** (sharing the
user positions `θ` and biases `α`), trading them off with `μ` and regularising
the positions with `λ`. The output is a similarity between any user and any item.

**Speaker notes.** "We use a joint learning framework using both content and
elite endorsement graphs, and in the end we get the similarity scores between
different entities like users and content."

**→ In this project.** The objective is `IdeologyModel._objective`
(`mu*(A*Pi_R − logaddexp(0, Pi_R)).sum() + (B*Pi_S − logaddexp(0, Pi_S)).sum() −
0.5*lam*(θ@θ + φ@φ + ψ@ψ)`), i.e. paper eq. 11 exactly; `fit(R, S)` performs the
joint optimisation (Adam). The resulting `sim(u, i)` is `RWEB.similarity`.
Verified empirically: the model recovers planted positions (|corr| > 0.8) and
**joint learning beats elite-only** (`examples/demo_synthetic.py`).

---

## Slide 11 — "Datasets on specific political events"

**On the slide.** Datasets of Twitter discussions around three events:

- **Brexit referendum (2016)** · **US presidential elections (2016)** ·
  **German federal elections (2017)**.

For each, two feedback graphs: **elite-endorsement** (based on **retweets**, RT)
and **content-endorsement** (based on **URL shares**, URL).

**Key point.** Positions are learned per-event from real Twitter behaviour, with
two complementary signals (who you retweet, what links you share).

**→ In this project.** `rwe/data.py` documents exactly these datasets
(`UK2016 / US2016 / DE2017`) and the RT (elite) / URL (content) split. The raw
Twitter data is **not redistributable**, so the repo ships generic loaders
(`load_csv`, `from_interactions`) plus synthetic generators
(`synthetic_political`, `synthetic_ideology`) that reproduce the same two-graph
structure, so the whole pipeline runs out of the box. *(This is the one place
the project necessarily substitutes synthetic data for the paper's private
datasets.)*

---

## Slide 12 — "Estimated positions: UK"

**On the slide.** A dot plot of UK politicians (conservatives = red, labour =
green, libdems = cyan, others = purple) along the −2…+2 scale, with notes:

- Most **Conservative** politicians on the **right**, most **Labour** on the **left**.
- **Nick Hurd** and **Ed Vaizey** (Conservative) and the **SNP** supported the
  *Remain* campaign — and are estimated on the **left** (highlighted boxes).
- **Ideological positions can be specific to issues/events; pre-existing labels
  can be unreliable.**

**Key point.** This is the headline ideology-detection result (the paper's
Figure 4): the method recovers left/right correctly *and* captures
event-specific exceptions that fixed party labels would get wrong.

**→ In this project.** This is an *evaluation result* on the private UK dataset,
so the exact figure can't be regenerated here. The model that produces it
(`IdeologyModel`) is implemented and validated on **synthetic** data, where it
recovers planted positions (|corr| > 0.8) and joint learning beats elite-only —
and the "issue-specific, not fixed labels" behaviour is the direct consequence
of learning from endorsement behaviour (verified for the Figure 1 example).

---

## Slide 13 — "Estimated positions: Germany and US"

**On the slide.** Two more dot plots:

- **Germany** (paper's Figure 5): parties ALDE, CSU, Green, AfD, FDP, Linke
  separate cleanly (e.g. *Linke/Green* left, *AfD* far right).
- **US** (paper's Figure 3): media and figures span *HillaryClinton / MSNBC /
  thinkprogress* on the left to *tedcruz / GOP / realDonaldTrump* on the right.

**Key point.** The method generalises across countries and across both
politicians and media outlets.

**→ In this project.** As with Slide 12, these are results on private datasets;
the implementation is validated on synthetic data instead. (Same `IdeologyModel`,
no special-casing per country.)

---

## Slide 14 — "Our Approach" (recap)

**On the slide.** The three-step "Our Approach" figure from Slide 6 is shown
again, marking the transition: step 1 (identify positions) has just been
demonstrated working, so the talk now turns to steps 2–3 (diversification
strategy + RWE).

**→ In this project.** See Slide 6 — steps map to `ideology.py`, `RWED`/`RWEB`,
and `RWE`.

---

## Slide 15 — "Normative goals for diversification"

**On the slide.** What a good diversified recommender should do:

- **Personalized:** high accuracy.
- **Diverse:** according to the chosen diversification strategy.
- For **long-tail**: expose users to more **low-degree** items.
- For **bridging**: recommendations should
  - expose users to viewpoints from the **other side**,
  - but **not be very different / polarizing**, and
  - use **weak ties** for diversification (**bridges**).

A scale shows items and a highlighted (circled) user on the spectrum.

**Key point.** Diversity is not "show the opposite extreme" — it is calibrated:
accurate, strategy-specific, and (for politics) *reachable* opposite-side content
via weak ties, not jarring opposite extremes.

**Speaker notes.** "It should be accurate and it should be diverse … for long-tail
diversity the users should be exposed to more low-degree items … for political
ideology we define a bridging strategy in which we'd like to expose users to
viewpoints from the other side, but that should not be very polarizing … we use
what we call bridges, which are essentially weak ties for diversification."

**→ In this project.** Every goal is realised and measured:

| Goal | Code | Measured by |
|------|------|-------------|
| Personalized / accurate | all recommenders | `auc`, `hit_rate`, `precision`, `mean_rank` |
| Long-tail (low-degree) | `RWED` (degree-based `Q`, eq. 4) | `average_item_degree`, `gini_diversity`, `surprisal` |
| Bridging (other side, not too far, weak ties) | `RWEB` (opposite-side bridges within `max_distance`) | `rec_range`, `directed_shift`, `weighted_shift/range`, `ks_statistic` |

---

## Slide 16 — "Diversification strategies"

**On the slide.**

- The **erase matrix `Q`** on the user-item graph *prefers certain random walks
  over others*.
  - **Example I — Bridging:** prefer walks to items whose ideological positions
    are *to the left for right-leaning users* and *to the right for left-leaning
    users*.
  - **Example II — Long-tail:** prefer walks to *low-degree* items.
  - **Example III — Contrarian:** prefer walks to items whose positions are
    *opposite* of the user.
- A **generalized framework** for diversifying personalization: usable for
  long-tail, political-content, *or any other property defined on items or
  user-item pairs*.

**Key point.** `Q` is the single knob. Different choices of `Q` give different
diversification strategies, and the framework is open-ended.

**→ In this project.** `Q` is the `item_erasure` argument of
`rwe/random_walk.py::RWE`, which accepts a per-item vector, a per-user matrix,
*or a callable* — i.e. any property on items or user-item pairs.

| Slide | In this project |
|-------|-----------------|
| Example I — Bridging | `RWEB` (opposite-side weak-tie bridges) |
| Example II — Long-tail | `RWED` (degree-based `Q`, eq. 4) |
| Example III — Contrarian | one expression on the `RWE` base — no new class needed |
| Generalized framework | `RWE(item_erasure=…)` accepts vector / matrix / callable |

The contrarian example is literally a few lines, confirming the "any property"
claim (here it lifts opposite-content from 9% under plain `P3` to ~81%):

```python
# Example III: erase ideologically *similar* items, keep *opposite* ones.
sim = 1 - np.abs(item_pos[None, :] - user_pos[uids][:, None]) / pos_range
contrarian = RWE(graph, item_erasure=lambda uids: sim)   # Q = similarity
```

---

## Slide 17 — "Random Walks with Erasure (RWE)"

**On the slide.**

- Bi-partite graph with **m users, n items**.
- **k-step** transition probabilities specified by **`Pᵏ`**.
- **Erase matrix `Q ∈ [0, 1)^{(m+n)×(m+n)}`**.
- **Sample multiple rounds** of k-step random walks.
- **Initial-state probabilities are modified in each round, based on `Q`.**

**Key point.** RWE = repeated k-step walks where, each round, the mass *erased*
according to `Q` becomes the next round's starting mass — so `Q` reshapes where
the walk concentrates.

**Speaker notes.** "First we do a normal k-step random walk … additionally we
have the erase matrix Q … we sample multiple rounds of k-step random walks, but
at each round the initial-state probabilities are modified based on Q."

**→ In this project.** Implemented exactly, in `rwe/graph.py` + `rwe/random_walk.py`:

| Slide element | Code |
|---------------|------|
| bipartite `m × n` graph | `FeedbackGraph` (adjacency `A^G`, eq. 1) |
| `Pᵏ` k-step transition | `graph.item_distribution(users, k)` (`P = D⁻¹A^G`) |
| `Q ∈ [0, 1)` | erasure clipped to `[0, 1−ε)` (`random_walk.py:169,172`) |
| multiple rounds, init-state modified by `Q` | `RWE.score_iterative` (start mass ← erased mass each round) |

Two faithful-implementation notes:

1. **`Q` is `(m+n)×(m+n)` on the slide; we store the item slice.** Because `k` is
   odd, a walk from a user lands on **item** nodes, so only the item-destination
   erasures ever act — the code computes those `(users × items)` entries.
2. **We also derived the closed form** of the "multiple rounds" loop
   (`score = p·(1−q)/(1−Σpq)`), so production scoring needs no iteration; the
   `score_iterative` round-by-round version is kept and **tested to match** the
   closed form, mirroring the slide's description.

---

## Slides 18–19 — "Diversification Strategy: Long-tail" (worked example)

**On the slides.** A 3-round walk on a small user-item graph, starting from user
`u₄` with mass 1:

- The 3-hop landing distribution is `P³_{u₄,i*} = [0.16, 0.16, 0.66]`.
- The erasure is `Q_{·,i*} = 1 − [1/3, 1/2, 1/2] = [0.66, 0.5, 0.5]` (item `i₁`
  has degree 3, `i₂`/`i₃` degree 2 — so popular `i₁` is taxed most).
- **Erased** mass `= P³ ∘ Q`; the **new initial-state probability** is
  `(P³ ∘ Q)·𝟙 ∘ I_{·,u₄}` (all erased mass returned to `u₄`); the **modified
  transition** is `v_{s,0} P³ ∘ (1 − Q)`.
- The boxed start masses fall `1 → 0.51 → 0.26` across rounds, and the final
  score is `w_s = Σ_{i=0}^{m} v_{s,i} Pᵏ ∘ (1 − Q)`.

**Key point.** A plain random walk would score `i₁` and `i₂` equally; the
degree-based erasure suppresses the high-degree node and lifts the low-degree
ones — long-tail diversification, computed by re-walking the erased mass.

**→ In this project — reproduced numerically.** This is exactly `RWED` (β=1) run
through `RWE.score_iterative`. Feeding the slide's `P³` and degrees into the
code's update rule reproduces the slide:

| Quantity | Slide | This project |
|----------|-------|--------------|
| `Q = 1 − 1/deg` | `[0.66, 0.5, 0.5]` | `[0.67, 0.5, 0.5]` (RWED, eq. 4) |
| Erased, round 1 | `[0.10, 0.08, 0.33]` | `[0.11, 0.08, 0.33]` |
| Start mass `1 → … → …` | `1 → 0.51 → 0.26` | `1 → 0.52 → 0.27` |
| Final score | `w_s = Σ v_{s,i} Pᵏ∘(1−Q)` | `score_iterative` (== closed form) |

(The 0.51-vs-0.52 difference is just the slide rounding `1/3 ≈ 0.66`.) The
update `reached = start·P³; erased = reached·Q; acc += reached·(1−Q);
start ← erased.sum()` in `rwe/random_walk.py::score_iterative` *is* the slide's
recurrence, and the derived closed form `p·(1−q)/(1−Σpq)` is the value the
"Score" column converges to.

---

## Slide 20 — "Experimental Evaluation"

**On the slide.** Three evaluation questions:

1. Can our **joint-learning framework** accurately identify ideological positions?
2. Can RWE generate **accurate and diverse long-tail** recommendations?
3. Can RWE generate **accurate and ideologically diverse** recommendations?

**→ In this project.** All three axes are implemented and exercised by the demos:

| Question | Code | Evidence |
|----------|------|----------|
| 1 — ideology | `IdeologyModel.fit(R, S)` | recovers planted positions; joint > elite-only |
| 2 — long-tail | `RWED` + accuracy/long-tail metrics | `demo_movielens.py` |
| 3 — ideological diversity | `RWEB` + `rec_range`/`shift`/`ks_statistic` | `demo_synthetic.py` |

---

## Slide 21 — "Datasets and Baselines"

**On the slide.** The dataset statistics (Table 5) and the baseline set:

- **Elite-endorsement:** UK-RT (10 547 / 2 439 / 71 310), US-RT (7 913 / 968 /
  71 310), DE-RT (5 305 / 837 / 23 561).
- **Content-endorsement:** UK-URL (10 547 / 1 244 / 37 036), US-URL (7 913 /
  698 / 30 801), DE-URL (5 305 / 424 / 8 212).
- **Benchmarks:** ML-1M (6 040 / 3 706 / 998 087), Yelp (6 945 / 11 274 /
  316 162).
- **Baselines:** state-of-the-art recommenders from a recent evaluation study
  (RecSys 2019) — **Collaborative Filtering**, **Matrix Factorization**,
  **graph-based P³**, and **long-tail-diversifying RP³_β**.

**→ In this project.** All four baselines are implemented:

| Slide baseline | Code |
|----------------|------|
| Collaborative Filtering | `rwe/baselines.py::ItemKNN` |
| Matrix Factorization | `rwe/baselines.py::BPRMF` |
| Graph-based P³ | `rwe/random_walk.py::P3` |
| Long-tail-diversifying RP³_β | `rwe/random_walk.py::RP3Beta` |

The datasets are documented in `rwe/data.py` (UK/US/DE × RT/URL, plus ML-1M /
Yelp loaders); the Twitter data is not redistributable, so synthetic generators
stand in for the pipeline. *(The exact Table-5 sizes are recorded here for
reference but not shipped as data.)*

---

## Slide 22 — "Result I: Ideological Range of top-10 items"

**On the slide.** A table of the average ideological spread of the top-10
recommendations (the paper's Table 7), RWE-B vs RP³_β vs P3:

| Dataset | RWE-B | RP³_β | P3 |
|---------|-------|-------|----|
| US-RT | **1.71*** | 1.61 | 1.43 |
| US-URL | **2.15*** | 1.73 | 2.0 |
| UK-RT | **2.83*** | 2.45 | 2.58 |
| UK-URL | **2.02*** | 1.87 | 1.93 |
| DE-RT | **3.14*** | 2.61 | 2.98 |
| DE-URL | 1.53 | **1.69*** | 1.47 |

**Key point.** RWE-B produces the widest ideological range (most viewpoint-spread
recommendations) on all but the smallest dataset.

**→ In this project.** This measure is `metrics.rec_range_at_k` (mean over users
of `max(pos) − min(pos)` in the top-k), surfaced as `rec_range@10` in
`experiment.evaluate`. On synthetic political data the directional result
reproduces: `demo_synthetic.py` shows **RWE-B with the largest `rec_range@10`**
among the recommenders (the exact per-dataset numbers are on the private Twitter
data and are not reproducible here).

---

*More slides to follow: Results II–IV (the ideological-diversity scatter/shift
measures and the weighted measures) and the conclusion.*
