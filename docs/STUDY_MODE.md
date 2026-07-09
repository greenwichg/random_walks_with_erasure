# Study Mode — Metric Formula Documentation (raw layer)

A developer learning + verification reference for **every raw Information-Health metric**: what it
consumes from your Reading History, the exact formula, a fully worked example, edge cases, cost, and —
kept strictly separate — how the raw value becomes the **displayed 0–100 score**.

The companion calculator is [`examples/study_metrics.py`](../examples/study_metrics.py): it recomputes
each raw value *independently* (no production imports in the calculators) and
`verify_against_production()` checks it against the engine's raw functions on identical inputs.

---

## Raw Metric vs Displayed Score — read this first

Every metric has **two layers**, and Study Mode is about the first:

| | Raw Metric | Displayed Score |
|---|---|---|
| What | a deterministic number from *your* reads | a **percentile rank** of that raw value vs a population |
| Example | Source Diversity = `1/HHI` = **3.571** effective sources | **41** ("more concentrated than ~59% of readers") |
| Reproducible from Reading History alone? | **Yes** | **No** — needs the whole population |
| Owned by | `health_report.py` raw helpers | `health_report.percentiles` |

> **The displayed score usually differs from the raw value**, because it is a *relative standing*, not
> the quantity itself. That is why this framework validates the raw layer and documents the percentile
> transformation as its own thing (last section). Your `unique/total = 0.80` intuition is a *different*
> raw measure than the one the app actually uses (`1/HHI`) — Study Mode shows both, side by side.

The worked examples below use one shared history:

| # | Topic | Publisher | lean | political | register |
|---|---|---|---|---|---|
| 1 | Politics | BBC | −1.2 | yes | reporting |
| 2 | Politics | BBC | −0.3 | yes | opinion |
| 3 | Business | CNN | +0.9 | yes | reporting |
| 4 | Politics | Fox | +1.4 | yes | opinion |
| 5 | Health | NPR | — | no | reporting |

---

## 1 · Topic Diversity

- **Purpose:** do you read across many topics, or a few?
- **Inputs:** the `category` of every read.
- **Formula (raw):** normalised Shannon entropy
  `H = −Σ pₖ·ln(pₖ)` (nats) then `raw = H / ln(C)`, where `pₖ` = your share of topic *k* and `C` = the
  number of categories. **`C` is a catalog property** — the engine normalises by the whole catalog's
  category count; from history alone we use your distinct-topic count and flag it.
- **Worked example:** counts `{Politics:3, Business:1, Health:1}` → shares `{0.6, 0.2, 0.2}` →
  `H = −(0.6·ln0.6 + 0.2·ln0.2 + 0.2·ln0.2) = 0.9503` nats → `C = 3` → `raw = 0.9503 / ln 3 = 0.865`.
- **Edge cases:** one topic → `H=0` → raw `0`. `C ≤ 1` → **NaN** (can't be diverse across one bucket).
  Zero reads → NaN.
- **Time complexity:** `O(n)` over reads (+ `O(t)` over distinct topics).
- **Confidence:** high formula confidence; the *taxonomy* is coarse and `C` is catalog-dependent.
- **Raw → Displayed:** `raw ∈ [0,1]` → percentile vs population.

## 2 · Source Diversity

- **Purpose:** how many distinct publishers, and how evenly?
- **Inputs:** the `outlet` of every read (blank/unparsed publishers excluded).
- **Formula (raw):** **effective number of sources** `= 1 / HHI`, `HHI = Σ sₒ²` over publisher shares.
- **Worked example:** BBC, BBC, CNN, Fox, NPR → shares `{BBC 0.4, CNN 0.2, Fox 0.2, NPR 0.2}` →
  `HHI = 0.4² + 0.2² + 0.2² + 0.2² = 0.28` → `raw = 1/0.28 = 3.571` effective sources.
  *(Contrast your intuition:* `unique/total = 4/5 = 0.80` — a different measure; the app uses `1/HHI`.)
- **Edge cases:** all one publisher → `HHI=1` → raw `1`. No parseable publisher → NaN (this is why
  MIND, whose URLs are MSN, shows Source Diversity `n/a`).
- **Time complexity:** `O(n)`.
- **Confidence:** high; depends on publisher parsing quality.
- **Raw → Displayed:** effective-count → percentile. Also feeds **Biggest Insight** (top-N share:
  here top-2 = `0.6`).

## 3 · Political Exposure — Viewpoint Balance & Echo Chamber

Both operate on the **political subset** only (reads with `political = true`), using the article
`lean` position and the band `LEAN_TAU = 0.5` (|lean| ≤ 0.5 = centre), `CENTER = 0.0`.

- **Inputs:** `lean` of every political read.
- **Bands (worked):** political leans `[−1.2, −0.3, +0.9, +1.4]` → left(`<−0.5`)=1, centre=1, right(`>0.5`)=2
  → **shares L/C/R = 0.25 / 0.25 / 0.50**; mean lean `= (−1.2−0.3+0.9+1.4)/4 = 0.20`.

**Viewpoint Balance (raw = cross-cutting share)**
- **Formula:** share of political reading on the *opposite* side of your **mean lean**. If the mean is
  exactly centre, every off-centre read counts.
- **Worked:** mean `0.20 > 0` → "own side" = right → opposite = leans `< 0` → `{−1.2, −0.3}` = **0.50**.
- **Edge cases:** no political reads → NaN; mean exactly at centre → share of non-centre reads.

**Echo Chamber (raw = echo)**
- **Formula:** `echo = 1 − 2·min(L,R)/(L+R) ∈ [0,1]` (0 = balanced, 1 = one-sided).
- **Worked:** `L=0.25, R=0.50` → `echo = 1 − 2·0.25/0.75 = 0.333`; balance `= 1 − echo = 0.667`.
- **Edge cases:** no left/right reading → NaN.
- **Raw → Displayed:** the score ranks **`1 − echo`**, so **higher displayed = LESS echo-chambered**
  (the name reads backwards). Viewpoint/Echo/Open-Mindedness rest on a **weak text-lean axis** —
  directional signals, not measurements (see `docs/HEALTH_REPORT.md`).
- **Confidence:** formula high; the **lean axis** underneath is weak (Spearman ≈ 0.27 vs human labels).

## 4 · Emotional Exposure — Attention Profile & Emotional Balance

- **Purpose:** is your reading calm or emotionally charged, and what's the mix?
- **Inputs:** per-read `emotion` shares over `{fear, outrage, analysis, positive, neutral}`.
- **Formula (raw):** Attention Profile = the **mean per-bucket share**; Emotional Balance =
  `1 − (mean fear + mean outrage)`.
- **Worked:** mean(fear+outrage) across the 5 reads `= 0.28` → **balance = 0.72**.
- **Edge cases:** no emotion data → NaN (all-`n/a`).
- **Time complexity:** `O(n·5)`.
- **Confidence:** **experimental — the noisiest metric.** Emotion-from-headline is unreliable and the
  buckets mix emotion (fear/outrage/positive) with register (analysis).
- **Raw → Displayed:** balance → percentile; Attention Profile is **descriptive, not scored**.

## 5 · Reporting vs Opinion (Reporting Ratio)

- **Purpose:** how much straight reporting vs opinion/editorial?
- **Inputs:** the `register` label (reporting | opinion | mixed) of every read.
- **Formula (raw):** share of reads labelled `reporting`.
- **Worked:** `{reporting:3, opinion:2}` → **0.60**.
- **⚠ Fidelity note:** the *engine's* Reporting Ratio is the mean of a classifier's **P(reporting)
  probability** per article; a stored read keeps only the discrete **label**, so from Reading History
  we reproduce the *label share* (an honest, reproducible proxy), not the probability mean.
- **Edge cases:** no register data → NaN.
- **Raw → Displayed:** ratio → percentile.

## 6 · Reading Time

- **Purpose:** total time spent reading.
- **Inputs:** per-read reading minutes (or an estimate).
- **Formula (raw):** `Σ per-article minutes`, each `≈ max(1, min(20, round(words/220)))`.
- **⚠ Fidelity note:** stored reads keep no article body, so per-read minutes fall back to a title-word
  estimate or the neutral default (2). This is the one metric whose *raw inputs* are lossy from history.
- **Edge cases:** empty history → 0.
- **Raw → Displayed:** shown directly (a sum), not percentile-ranked.

## 7 · Blind Spot *(needs a catalog reference)*
- **Purpose:** the topic you most under-read vs the catalog.
- **Formula:** the category with the largest `catalog_share − your_share`.
- **Note:** requires the catalog's topic distribution, so it is **not** computable from Reading History
  alone — documented here; the framework will accept a catalog fixture in a later step.

## 8 · Overall Score
- **Formula:** the **unweighted mean of the available displayed scores** (percentiles). It lives at the
  *displayed* layer, so it is population-dependent and not a raw quantity.

---

## The Percentile Transformation (separate layer)

```
DISPLAYED = percentile rank of RAW vs the population (0–100):
    score_u = (rank(raw_u among all readers) − 1) / (k − 1) × 100      # k readers; lone reader → 50
```
- Turns each raw measure into a **relative standing**, so the number moves as the population changes and
  is **not reproducible from one reader's rows**.
- Echo Chamber ranks `1 − echo` (higher = better). Overall = mean of available displayed scores.
- Implementation: `health_report.percentiles` (scipy `rankdata`, method `average`).

**Bottom line:** Study Mode proves the **raw** values are computed correctly (cross-checked to 1e-9 vs
the engine). The **displayed** score is those raw values ranked against everyone else — a deliberate,
separate transformation, documented but not reproduced here.
