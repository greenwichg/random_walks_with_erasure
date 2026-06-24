# The Math Behind Every Piece — Derivations & Notation

This is the **deep math companion** to the project. Where [`GUIDE.md`](../GUIDE.md)
tells the *story* in plain words and [`README.md`](../README.md) is the *API*
reference, this file derives **every formula we actually implement** and points
each one at the exact code that computes it.

**Source-of-truth rule.** The tested code in `rwe/` is authoritative. Every
equation below was checked against the implementation it cites (file · symbol),
and the worked numbers in §6 are *printed by the real code*, not hand arithmetic.
If a formula here ever disagrees with the code, the code is right and this file
is the bug.

### Contents

1. [Notation](#1-notation)
2. [The feedback graph and the random walk](#2-the-feedback-graph-and-the-random-walk) — eqs (1)–(2)
3. [Baselines: P³ and RP³-β](#3-baselines-p³-and-rp³-β)
4. [Random Walk with Erasure — the closed form](#4-random-walk-with-erasure--the-closed-form) — eq (3)
5. [RWE-D: long-tail erasure](#5-rwe-d-long-tail-erasure) — eq (4)
6. [RWE-B: bridging erasure](#6-rwe-b-bridging-erasure) — eq (5)
7. [A fully worked numeric example](#7-a-fully-worked-numeric-example)
8. [The ideology model (ideal points)](#8-the-ideology-model-ideal-points) — eqs (6)–(11)
9. [Text-grounded ideological positions](#9-text-grounded-ideological-positions)
10. [Evaluation metrics](#10-evaluation-metrics)
11. [The opinion-dynamics simulation](#11-the-opinion-dynamics-simulation)
12. [The satisfaction extension](#12-the-satisfaction-extension)
13. [Map: math → code](#13-map-math--code)

Equation numbers (6), (9), (11)… refer to Paudel & Bernstein, *"Random Walks with
Erasure: Diversifying Personalized Recommendations on Social and Information
Networks,"* WWW '21.

---

## 1. Notation

| symbol | meaning | code |
|---|---|---|
| $m,\ n$ | number of users, items | `FeedbackGraph.m`, `.n` |
| $A \in \{0,1\}^{m\times n}$ | user–item feedback matrix | `FeedbackGraph.A` |
| $A^G \in \{0,1\}^{(m+n)\times(m+n)}$ | bipartite adjacency, eq (1) | `FeedbackGraph.A_G` |
| $D$ | diagonal degree matrix, $D_{ii}=\sum_j A^G_{ij}$ | `FeedbackGraph.degree` |
| $P = D^{-1}A^G$ | row-stochastic transition matrix, eq (2) | `FeedbackGraph.P` |
| $v_s$ | one-hot start vector at node $s$ | (built in `k_step_distribution`) |
| $k$ | walk length (**odd**, so the walk ends on items) | `BaseRecommender.k` |
| $p = (v_s P^k)_{\text{items}}$ | landing distribution over items, $\sum_j p_j = 1$ | `item_distribution` |
| $q = Q[s,:]$ | per-item erasure probabilities, $q_j \in [0,1)$ | `_item_erasure` |
| $\deg_j$ | item $j$'s degree (popularity) | `item_degrees` |
| $\theta_u$ | user $u$'s ideological position | `IdeologyResult.theta` |
| $\phi_e,\ \psi_i$ | elite / content positions | `.phi`, `.psi` |
| $\odot$ | element-wise (Hadamard) product | — |

Indices: user $u$ is node $u$; item $i$ is node $m+i$ in the joint $(m+n)$ space.

---

## 2. The feedback graph and the random walk

We observe implicit feedback (a click, a read). Stack it as the binary matrix
$A$, then embed it as an **undirected bipartite graph** on $m+n$ nodes (eq 1):

$$
A^G = \begin{bmatrix} \mathbf{0}_{m\times m} & A \\ A^{\top} & \mathbf{0}_{n\times n} \end{bmatrix}.
$$

The zero diagonal blocks say *users never link to users and items never link to
items directly* — every edge crosses sides. Row-normalising gives a Markov
chain (eq 2):

$$
P = D^{-1} A^G, \qquad D_{ii} = \sum_j A^G_{ij},
$$

so $P_{ab}$ is the probability a walker at $a$ steps to a uniformly-random
neighbour $b$. Starting from a one-hot user vector $v_s$ and taking $k$ steps,

$$
v_s P^k
$$

is the distribution over all nodes after $k$ random steps. Because the graph is
bipartite, the walker alternates sides every step: from a **user** node, an
**odd** $k$ lands the mass entirely on **item** nodes. That is why every
recommender uses $k=3$ (`k` must be odd — `BaseRecommender.__init__` raises
otherwise), and why we slice out the item block:

$$
p \;=\; \big(v_s P^k\big)\big|_{\text{items } m..m+n}, \qquad \textstyle\sum_j p_j = 1 .
$$

`FeedbackGraph.k_step_distribution` does exactly this iteration ($k$ sparse
mat-vecs), and `item_distribution` returns the item slice $p$.

> **Why $P^3$ and not $P^1$?** $P^1$ from a user can only reach items the user
> *already* clicked (one hop). $P^3$ = user → item → other users who liked it →
> *their* items: the first step at which genuinely new, taste-related items
> appear. This is the standard collaborative-filtering random walk.

---

## 3. Baselines: P³ and RP³-β

**P³** (`class P3`) ranks items by the raw landing mass $p_j$. Popular items
have high degree, so the walk piles up on them — this is the accuracy-strong,
diversity-poor baseline.

**RP³-β** (`class RP3Beta`) divides the mass by a power of item degree:

$$
\text{score}^{\text{RP3}}_j \;=\; \frac{p_j}{\deg_j^{\,\beta}} .
$$

$\beta=0$ recovers P³; larger $\beta$ promotes low-degree (long-tail) items.
Implemented as `p * deg**(-beta)` with $\deg=0$ guarded to 1. This is the
classic re-ranking baseline RWE-D is measured against — and, as §5 shows, RWE-D
with $v=1$ is *mathematically identical* to it.

---

## 4. Random Walk with Erasure — the closed form

This is the heart of the method, and the one derivation worth doing slowly.

**The process (eq 3).** Run the walk and get the landing distribution $p$. Now
*erase* a fraction of the mass at each item: item $j$ keeps $p_j(1-q_j)$ and
returns $p_j q_j$ to the origin $s$. The returned mass **re-walks the same
$P^k$**, lands again, is erased again, and so on. The recommendation score is
the **total retained mass** an item accumulates over all passes.

**Claim.** Let $c = \sum_j p_j q_j = p\cdot q$ be the fraction of mass erased on
one pass ($0 \le c < 1$). Then the accumulated score is

$$
\boxed{\ \text{score}(s,\cdot) \;=\; \frac{p \odot (1-q)}{\,1 - \sum_j p_j q_j\,} \;=\; \frac{p\odot(1-q)}{1-c}\ }
$$

**Derivation.** Track the mass injected at $s$ at the start of pass $t$; call its
total $\sigma_t$, with $\sigma_0 = 1$. On pass $t$:

- the walk spreads it to items as $\sigma_t\, p$ (since $p$ is the unit-mass landing distribution);
- item $j$ **retains** $\sigma_t\, p_j (1-q_j)$, which we add to its score;
- the **erased** mass $\sum_j \sigma_t\, p_j q_j = \sigma_t\, c$ returns to $s$ and becomes the next pass's injection:

$$
\sigma_{t+1} = c\,\sigma_t \quad\Longrightarrow\quad \sigma_t = c^{\,t}.
$$

Summing the retained mass over every pass and using the geometric series
$\sum_{t\ge 0} c^t = \frac{1}{1-c}$ (valid because $c<1$):

$$
\text{score} \;=\; \sum_{t=0}^{\infty} \sigma_t\, p\odot(1-q)
\;=\; p\odot(1-q)\sum_{t=0}^{\infty} c^{\,t}
\;=\; \frac{p\odot(1-q)}{1-c}. \qquad\blacksquare
$$

**Reading the formula.**
- **Numerator** $p\odot(1-q)$: the retained mass at each item — the walk's
  relevance signal, *taxed* per item by $q_j$.
- **Denominator** $1-c$: a single per-user constant (the geometric-series sum).
  It is the *same* for every item $j$, so **it does not change the ranking** —
  but we keep it so the returned scores equal the converged power iteration.

The denominator's invariance is why erasure is a pure *re-ranking* knob: it
reshapes *which* items win without re-running the walk. The shape of $q$ is the
entire design space — §5 picks two shapes for two goals.

**Verification in code.** `BaseRecommender._score_batch` computes the closed form
directly:

```python
erased   = p * q
retained = p - erased
c        = erased.sum(axis=1, keepdims=True)
return retained / np.clip(1.0 - c, _EPS, None)
```

and `RWE.score_iterative` runs the *actual* pass-by-pass loop above. A test
(`tests/test_random_walk.py`) asserts the two agree — and §7 shows them matching
to machine precision.

---

## 5. RWE-D: long-tail erasure

**Goal:** suppress popular items so the tail surfaces. Make the erasure depend
only on the **destination item's degree** (eq 4):

$$
q^D_j \;=\; 1 - \deg_j^{-\beta}, \qquad \beta \ge 0 .
$$

A blockbuster ($\deg_j$ large) has $q^D_j \to 1$ — almost all its mass is erased
and recycled. A niche item ($\deg_j = 1$) has $q^D_j = 0$ — nothing erased. Plug
into the closed form: the retained mass is

$$
p_j\,(1-q^D_j) \;=\; p_j\,\deg_j^{-\beta},
$$

so up to the per-user constant $1/(1-c)$,

$$
\text{score}^{\text{RWE-D}}_j \;\propto\; \frac{p_j}{\deg_j^{\beta}} \;=\; \text{score}^{\text{RP3}}_j .
$$

**RWE-D with $v=1$ is exactly RP³-β.** That is not a bug — it's the sanity
anchor: the erasure framework *contains* the known baseline as a special case.
The generalisation is the exponent $v$ on the whole erasure matrix,
$Q \mapsto Q^{\odot v}$ (`RWE.v`), which lets RWE-D deviate from RP³-β during the
paper's grid search ($v\neq 1$ bends the degree response non-linearly). Code:
`class RWED` builds `q_d = 1 - safe_deg**(-beta)` and hands it to the generic
`RWE`.

---

## 6. RWE-B: bridging erasure

**Goal:** show a left-leaning user good *right*-leaning items (and vice versa) —
but only ones that are *"different, not too far."* Here the erasure depends on
**ideological position**, not degree.

Let $\theta_u$ be the user's position and $\mathrm{pos}_i$ the item's (an elite
$\phi$ or content $\psi$ position, §8, or a text-lean score, §9). Define a
population **center** $\kappa$ (default: median of $\theta$). Two ingredients:

**(a) Similarity** — a normalised closeness on the 1-D axis (Section 5.2):

$$
\mathrm{sim}(u,i) \;=\; 1 - \frac{|\mathrm{pos}_i - \theta_u|}{\mathrm{pos}_{\max}-\mathrm{pos}_{\min}} \;\in\; [0,1].
$$

**(b) The bridge test** — item $i$ is a *bridge* for user $u$ iff it is on the
**opposite side** of the center *and* within a distance bound $d$:

$$
\mathrm{bridge}(u,i) \;\Longleftrightarrow\; \underbrace{(\theta_u-\kappa)(\mathrm{pos}_i-\kappa) < 0}_{\text{opposite sides}} \ \wedge\ \underbrace{|\mathrm{pos}_i-\theta_u|\le d}_{\text{not too far}} .
$$

Then the erasure matrix (eq 5) is

$$
q^B_{u,i} \;=\; \begin{cases} \mathrm{sim}(u,i) & \text{if } i \text{ is a bridge for } u,\\[2pt] \varepsilon & \text{otherwise,} \end{cases}
$$

with $\varepsilon$ a high constant (paper uses $0.9$). **Why this surfaces
bridges:** recall retained mass $\propto p_{ij}(1-q^B_{u,i})$.

- **Non-bridge items** (same-side, or too far): $q=\varepsilon=0.9 \Rightarrow$
  keep only $10\%$ of their mass — heavily suppressed.
- **Bridge items** (opposite-side, close): $q=\mathrm{sim}$, which is *small*
  for the closest opposite items, so $1-q$ is large — they keep most of their
  mass and rise to the top.

So RWE-B promotes exactly the opposite-side-but-near items, the "weak ties"
that broaden a user without whiplash. Code: `class RWEB` — `similarity`,
`is_bridge`, and `_compute` assemble $q^B$.

**The bound $d$ is the control knob (the project's main extension).**
`max_distance = d` caps how far across the aisle a bridge may sit:

- $d=\infty$ (default): *any* opposite-side item qualifies → the walk can land
  on the opposite **extreme** ("naive opposite-blast," which §11 shows
  *backfires*).
- small $d$: only items *just* across the center qualify → recommendations sit
  **near the center** (the depolarising regime).

Sweeping $d$ traces a clean monotone curve from opposite-extreme to centre
exposure at near-constant accuracy (`docs/RESULTS.md`); $d\approx 1.5\text{–}2$
is the moderated-bridging sweet spot. `AdaptiveRWEB` (§12) instead tunes the
*per-user* $\varepsilon$ from a measured satisfaction signal.

---

## 7. A fully worked numeric example

A 3-user × 4-item graph, computed by `rwe/` end-to-end (script:
`scratchpad/worked_example.py`; every number below is printed by the code).

```
users' clicks:  u0 → {item0, item1}
                u1 → {item0, item2}
                u2 → {item0, item3}
item degrees:   [3, 1, 1, 1]      # item0 is the "hit", items1–3 are tail
```

**Step 1 — the walk** ($k=3$, user $u_0$):

$$
p = v_{u_0}P^3\big|_{\text{items}} = [\,0.5000,\ 0.3333,\ 0.0833,\ 0.0833\,], \quad \textstyle\sum_j p_j = 1.
$$

Item 0 (everyone's hit) collects half the mass; the user's own niche item 1
gets a third.

**Step 2 — RWE-D erasure** ($\beta=0.5$, $q^D_j = 1-\deg_j^{-1/2}$):

$$
q = [\,0.4226,\ 0,\ 0,\ 0\,].
$$

Only the popular item is taxed ($1-3^{-1/2}=0.4226$); the degree-1 tail items
are untouched.

**Step 3 — closed form.** Erased fraction $c = p\cdot q = 0.5(0.4226)=0.2113$:

$$
\text{score} = \frac{p\odot(1-q)}{1-c} = [\,0.3660,\ 0.4226,\ 0.1057,\ 0.1057\,].
$$

**Verification — closed form vs. the actual erasure loop:**

```
RWED.scores():          [0.3660  0.4226  0.1057  0.1057]
RWED.score_iterative(): [0.3660  0.4226  0.1057  0.1057]
closed == iterative?    True
```

**What erasure did.** P³ ranks by raw mass, so the hit wins:
$p_0/p_2 = 0.5/0.0833 = 6.0$. After RWE-D the hit is *demoted below the user's
own niche item* ($0.366 < 0.4226$) and its edge over the tail shrinks:
$\text{score}_0/\text{score}_2 = 3.46$. Same walk, same relevance signal —
popularity simply taxed away. (At recommendation time the already-seen items 0,1
are excluded; the four-item scores are shown here to expose the *mechanism*.)

---

## 8. The ideology model (ideal points)

To place users and items on a left↔right axis from behaviour alone, we fit a
**spatial / ideal-point logistic model** (Section 6, `rwe/ideology.py`).

**Model.** A user $u$ endorses an elite $e$ (e.g. follows a politician) with
probability that *decreases in squared ideological distance* (eq 6):

$$
\Pi^R_{u,e} = -\lVert\theta_u-\phi_e\rVert^2 + \alpha_u + \beta_e, \qquad \Pr(R_{u,e}=1)=\sigma(\Pi^R_{u,e}),
$$

where $\sigma$ is the logistic sigmoid, $\alpha_u,\beta_e$ are popularity/activity
biases, and $\theta_u,\phi_e\in\mathbb{R}$ are the **ideal points** we want. The
joint model adds a content-share graph $S$ with content positions $\psi_i$ (eq 9):

$$
\Pi^S_{u,i} = -\lVert\theta_u-\psi_i\rVert^2 + \alpha_u + \gamma_i .
$$

**Objective (eq 11).** Maximise the confidence-weighted Bernoulli log-likelihood
of both graphs with L2 regularisation on positions ($\mu$ weights the elite
graph):

$$
\mathcal{L} = \mu\!\!\sum_{u,e}\!\big[a_{u,e}\Pi^R_{u,e} - \log(1+e^{\Pi^R_{u,e}})\big] + \sum_{u,i}\!\big[b_{u,i}\Pi^S_{u,i} - \log(1+e^{\Pi^S_{u,i}})\big] - \tfrac{\lambda}{2}\big(\lVert\theta\rVert^2+\lVert\phi\rVert^2+\lVert\psi\rVert^2\big).
$$

The $a_{u,e}$ are confidence weights (non-zero entries of $R$); passing $S=\text{None}$
gives the elite-only model of Section 6.1. Note the biases $\alpha,\beta,\gamma$
are **not** regularised — only the positions are.

**Gradients (what the code actually ascends).** For the logistic term,
$\partial/\partial\Pi\,[a\Pi-\log(1+e^\Pi)] = a-\sigma(\Pi)$, the **residual**
$\text{err}=a-\sigma(\Pi)$. With $\partial\Pi^R_{u,e}/\partial\theta_u=-2(\theta_u-\phi_e)$
and the symmetric term for $\phi$:

$$
\begin{aligned}
\nabla_{\theta_u}\mathcal{L} &= \mu\sum_e \text{err}^R_{u,e}\,\big(-2(\theta_u-\phi_e)\big) + \sum_i \text{err}^S_{u,i}\,\big(-2(\theta_u-\psi_i)\big) - \lambda\theta_u,\\
\nabla_{\phi_e}\mathcal{L} &= \mu\sum_u \text{err}^R_{u,e}\,\big(2(\theta_u-\phi_e)\big) - \lambda\phi_e,\\
\nabla_{\alpha_u}\mathcal{L} &= \mu\sum_e \text{err}^R_{u,e} + \sum_i \text{err}^S_{u,i},\qquad \nabla_{\beta_e}\mathcal{L} = \mu\sum_u \text{err}^R_{u,e},
\end{aligned}
$$

and analogously $\nabla_{\psi_i},\nabla_{\gamma_i}$ for the content graph. These
are exactly the lines `g_theta`, `g_phi`, … in `IdeologyModel.fit`.

**Optimiser.** Each parameter block is ascended with its own **Adam** step
(`class _Adam`) — the blocks' gradients live on very different scales ($\theta$
sums over items, $\beta$ over users), so a single fixed rate is fragile. Adam's
per-coordinate adaptive step (1st/2nd-moment estimates $\hat m,\hat v$,
$\theta \leftarrow \theta + \mathrm{lr}\,\hat m/(\sqrt{\hat v}+\epsilon)$) fixes
that. The objective itself is logged only every `eval_every` sweeps (it needs an
extra `logaddexp` forward pass) — a pure speed knob, no effect on the fit.

**Identifiability fixes (post-processing).** The likelihood is invariant to (i) a
global shift/scale of the axis and (ii) a global **sign flip** ($\theta,\phi,\psi \mapsto -\theta,-\phi,-\psi$ leaves every $\lVert\theta-\phi\rVert^2$ unchanged).
So after fitting we **standardise** $\theta$ to zero-mean/unit-variance (moving
$\phi,\psi$ by the same shift/scale to stay comparable), and optionally flip the
sign so a known `anchor` user sits on the left. Without these two steps the
numbers would be arbitrary run-to-run.

> **Caveat that matters for results.** On MIND, fitting this model to *co-click*
> behaviour recovered a **topical** axis, not a left↔right one (both poles were
> 2019 political news, split by topic). That is why the headline results use the
> **text-lean** axis of §9 instead. See `docs/RESULTS.md` for the axis-quality
> number (Spearman ≈ 0.27).

---

## 9. Text-grounded ideological positions

When behaviour gives a topical axis, score each article's lean from its **text**
instead (`examples/classify_lean.py`). Run a pretrained political-bias classifier
(`bucketresearch/politicalBiasBERT`, labels LEFT/CENTER/RIGHT) over the
title+abstract to get class probabilities $\Pr = [\Pr_L,\Pr_C,\Pr_R]$, then take
the **softmax-expected position** against label anchors $\ell = [-1,0,+1]$:

$$
\mathrm{pos}_i \;=\; s \cdot \big(\Pr_i \cdot \ell\big) \;=\; s\,\big(-\Pr_L + \Pr_R\big) \;\in\; [-s,\,s],
$$

with scale $s=2$ to match the outlet-lean range $[-2,2]$. A confidently-left
headline $\to -2$, confidently-right $\to +2$, mixed/centre $\to 0$. This
$\mathrm{pos}_i$ is the $\mathrm{pos}$ used by RWE-B (§6) and the UW metrics (§10).
`_positions_from_probs` is a one-liner: `scale * (probs @ label_positions)`.

> Always verify the model's `id2label` order matches `--label-positions` before
> trusting the sign — the script prints it for exactly this reason.

---

## 10. Evaluation metrics

All in `rwe/metrics.py`. `recommendations` is an $(m,\text{top-}k)$ array of
ranked item ids ($-1$ pads empty slots); `score_rows` is the dense $(m,n)$ score
matrix.

### Accuracy

**AUC** (`auc`) — per user, the probability a held-out positive outranks a random
non-interacted item, via the Mann–Whitney $U$ statistic. With candidate ranks
$r$ (1 = lowest score), $n_+$ positives, $n_-$ negatives:

$$
\text{AUC}_u = \frac{\big(\sum_{j\in\text{pos}} r_j\big) - \tfrac{n_+(n_++1)}{2}}{n_+\,n_-}, \qquad \text{AUC}=\operatorname*{mean}_u \text{AUC}_u .
$$

Subtracting $\tfrac{n_+(n_++1)}{2}$ removes the positives' "self-ranking"; the
denominator is the number of pos–neg pairs. $0.5$ = random, $1$ = perfect.
Training items are excluded from the candidate set.

**Mean rank** (`mean_rank`) — mean 1-indexed rank of held-out items among
candidates (lower is better).

**Hit@k** (`hit_rate_at_k`) — recall-style: $\operatorname{mean}_u \tfrac{|\text{top-}k\,\cap\,\text{test}|}{|\text{test}|}$.

**Precision@k** (`precision_at_k`) — $\operatorname{mean}_u \tfrac{|\text{top-}k\,\cap\,\text{test}|}{k}$.

**NDCG@k** (`ndcg_at_k`) — binary-relevance ranking quality with log discount.
With a hit at 0-indexed rank $r$ contributing $1/\log_2(r+2)$:

$$
\text{NDCG}_u@k = \frac{\sum_{r:\,\text{rec}_r\in\text{test}} \frac{1}{\log_2(r+2)}}{\sum_{r=0}^{\min(|\text{test}|,k)-1}\frac{1}{\log_2(r+2)}} .
$$

The denominator (IDCG) is the best achievable DCG, so $\text{NDCG}\in[0,1]$.

### Long-tail diversity

**Gini diversity** (`gini_diversity`) — $1-\text{Gini}$ of the
recommendation-frequency distribution. With counts $x_{(1)}\le\dots\le x_{(n)}$
sorted ascending, total $T=\sum x$:

$$
\text{Gini} = \frac{2\sum_{j=1}^{n} j\,x_{(j)}}{n\,T} - \frac{n+1}{n}, \qquad \text{div} = 1-\text{Gini}.
$$

Gini $=0$ when every item is recommended equally (max diversity), so $1-\text{Gini}$
is *higher = more even spread = more diverse*.

**Catalog coverage** (`catalog_coverage`) — fraction of the catalog that appears
in *some* user's list: $|\bigcup_u \text{top-}k_u|/n$.

**Average item degree** (`average_item_degree`) — mean training popularity of
recommended items; *lower = more long-tail*.

**Personalization** (`personalization`) — $1-$ mean pairwise cosine between
users' top-$k$ sets, where the cosine of two sets is
$|A\cap B|/\sqrt{|A|\,|B|}$. Higher = users get more *different* lists.

**Surprisal** (`surprisal`) — mean self-information of recommended items,
$\operatorname{mean}\big[-\log_2(\deg_i/m)\big]$; higher = rarer/more novel.

### Ideological diversity (RQ3)

Let $\bar{r}_u = \operatorname{mean}$ position of user $u$'s recommended items,
a reference $\rho_u$ (the user's own $\theta_u$, for the **UW** family), and
center $\kappa$.

**RecRange@k** (`rec_range_at_k`) — mean top-$k$ spread $\max_i\mathrm{pos}_i - \min_i\mathrm{pos}_i$ per user.

**Directed shift** (`directed_shift`) — mean shift *toward the opposite side*:

$$
\text{dshift} = \operatorname*{mean}_u\Big[\, -\operatorname{sign}(\rho_u-\kappa)\,(\bar r_u-\rho_u)\,\Big].
$$

The $-\operatorname{sign}(\rho_u-\kappa)$ flips the sign per side so that
*crossing the centre* is positive **for both left and right users**. Higher = more
bridging.

**UW-shift** (`weighted_shift`) — the headline bridging measure: directed shift
weighted by extremity $w_u=|\rho_u-\kappa|$, so bridging an *extreme* user counts
more:

$$
\text{UW-shift} = \frac{\sum_u w_u\,\big[-\operatorname{sign}(\rho_u-\kappa)\big]\,(\bar r_u-\rho_u)}{\sum_u w_u}.
$$

**UW-range** (`weighted_range`) — extremity-weighted RecRange (same weights).

**UW-recs** (`weighted_position`) — extremity-weighted **distance of the
recommendations from the centre**, $\dfrac{\sum_u w_u\,|\bar r_u-\kappa|}{\sum_u w_u}$.
**Lower is better**: it is *low* when bridged recommendations land *near the
centre* and *high* when they land at the opposite *extreme*. This is the metric
that exposes the backfire regime — it is what the $d$-sweep drives from .77→.27.

**KS statistic** (`ks_statistic`) — Kolmogorov–Smirnov distance between two
recommenders' recommended-position distributions (how differently they spread
users ideologically).

> **UW vs TW.** Passing $\rho_u=\theta_u$ gives the *user-weighted* (UW) variant;
> passing the mean position of the user's training items gives *training-weighted*
> (TW). The appendix normalisation isn't in the paper's main text, so these
> follow its two stated desiderata (cross-centre shift; weight extreme users
> more) — see the comment block above `mean_recommended_position`.

---

## 11. The opinion-dynamics simulation

The metrics above measure *where recommendations land*. To argue that landing
near-centre actually **depolarises** (and the opposite extreme **backfires**), we
simulate opinion change under **assimilation–contrast / Social Judgment Theory**
(`rwe/opinion_dynamics.py`).

**Update rule.** Show user $\theta$ content at position $\mathrm{shown}$; let
$d=\mathrm{shown}-\theta$. With acceptance latitude $L_a$, rejection latitude
$L_r$, and step sizes $\mu_a,\mu_b$:

$$
\theta' = \mathrm{clip}\!\Big(\theta + \underbrace{\mu_a\,d\,\mathbf{1}[\,|d|\le L_a]}_{\text{assimilate (toward)}} \ \underbrace{-\,\mu_b\,d\,\mathbf{1}[\,|d|\ge L_r]}_{\text{backfire (away)}},\ -B,\ B\Big).
$$

- **Close** content ($|d|\le L_a$): the user moves *toward* it — persuasion.
- **Far** content ($|d|\ge L_r$): the user moves *away*, toward their own pole —
  the **backfire effect** (Bail et al. 2018).
- **In between**: ignored (latitude of non-commitment).

**Exposure policies** (what `shown` is, given $\theta$):

| policy | shown | effect |
|---|---|---|
| echo chamber | $1.3\,\theta$ | same side, more extreme → diverges |
| naive opposite-blast | $-\operatorname{sign}(\theta)\,B$ | far pole → triggers backfire |
| RWE-B bridging | $\theta-\operatorname{sign}(\theta)\,0.9 L_a$ | opposite side *within* $L_a$ → converges |
| adaptive (satisfaction) | step $=0.9 L_a\cdot\mathrm{clip}(1-\lvert\theta\rvert/B,0.15,1)$ | shrinks stretch for extremists → never backfires |

**Outcome measure.** Population **polarization** $= \operatorname{std}(\theta)$,
tracked over rounds (`polarization`, `run`, `compare_policies`). The result:
bounded bridging *reduces* the std while the naive blast *increases* it — same
goal, opposite outcome depending on *how far* you reach. This is the simulated
half of the contribution; the real-data half (§6, §10) is that the bound $d$ lets
RWE-B *choose* which regime it operates in.

---

## 12. The satisfaction extension

Rather than a single global bound, infer **per-user** tolerance for opposing
content from simulated browsing (`rwe/satisfaction.py`).

1. **Webpage graph** (`WebGraph`): project the bipartite graph to item–item
   co-occurrence $C = A^{\top}A$ (zero diagonal); row-normalise to a surfer
   chain $T=D^{-1}C$.
2. **Communities** (`detect_communities`): asynchronous **label propagation**
   (Raghavan et al.) on a kNN-sparsified $C$ — each node repeatedly adopts the
   weighted-most-frequent neighbour label until stable. Each community's
   **viewpoint** is its mean item position (`community_viewpoints`).
3. **Satisfaction score** (`satisfaction_score`): simulate a surfer walk from the
   user's own pages; count the length of the contiguous run spent inside the
   **first opposing-viewpoint community** entered, until the walk leaves it.
   Long dwell = comfortable with the other side.
4. **Exposure** (`SatisfactionModel.exposure`): min–max normalise scores to
   $[0,1]$.
5. **Adaptive erasure** (`AdaptiveRWEB`): map exposure linearly to the per-user
   non-bridge erasure,

$$
\varepsilon_u = \varepsilon_{\text{low}} + \text{exposure}_u\,(\varepsilon_{\text{high}}-\varepsilon_{\text{low}}),
$$

so tolerant users (high exposure) get same-side content suppressed *harder* →
more opposing items surfaced; sensitive users get a gentler dose. This is the
per-user realisation of "different, but not too far."

---

## 13. Map: math → code

| math | symbol / eq | file · object |
|---|---|---|
| bipartite adjacency, transition | (1), (2) | `rwe/graph.py` · `FeedbackGraph.A_G`, `.P` |
| $k$-step walk $v_sP^k$ | §2 | `rwe/graph.py` · `k_step_distribution`, `item_distribution` |
| P³, RP³-β | §3 | `rwe/random_walk.py` · `P3`, `RP3Beta` |
| **erasure closed form** | (3), §4 | `rwe/random_walk.py` · `BaseRecommender._score_batch` (`RWE.score_iterative` = the loop) |
| RWE-D $q^D=1-\deg^{-\beta}$ | (4), §5 | `rwe/random_walk.py` · `RWED` |
| RWE-B sim / bridge / bound | (5), §6 | `rwe/random_walk.py` · `RWEB.similarity`, `.is_bridge`, `._compute` |
| ideal-point logit, objective, gradients | (6),(9),(11), §8 | `rwe/ideology.py` · `IdeologyModel.fit`, `._objective` |
| Adam ascent | §8 | `rwe/ideology.py` · `_Adam` |
| text-lean position | §9 | `examples/classify_lean.py` · `_positions_from_probs` |
| accuracy / diversity / UW metrics | §10 | `rwe/metrics.py` |
| assimilation–contrast update | §11 | `rwe/opinion_dynamics.py` · `update`, `POLICIES` |
| satisfaction → per-user $\varepsilon$ | §12 | `rwe/satisfaction.py` · `SatisfactionModel`, `AdaptiveRWEB` |

For where these numbers land on real data, see [`RESULTS.md`](RESULTS.md); for
the plain-language version, [`GUIDE.md`](../GUIDE.md); for the writeup,
[`PAPER.md`](PAPER.md).

---

*Generated for the random-walks-with-erasure project. Equations verified against
the cited code; §7 figures printed by `rwe/` directly.*
