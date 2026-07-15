# W8 — Final Architecture Decision (Canonical)

**Status:** Canonical decision. Documentation only — no code, no prototype, no implementation.
This document supersedes the *direction* set by the earlier W8 papers (which remain valid as
designs): `W8A_BEHAVIORAL_WARM_START_DESIGN`, `W8A_PHASE1_RESULTS`, `W8A_PHASE2_READINESS`,
`W8B_PRODUCTION_BEHAVIORAL_GRAPH_DESIGN`, `W8_EVALUATION_AND_DECISION_GATE`,
`W8_DATASET_RECOMMENDATION`.

## Decision (up front)

**Option A — Pause external-dataset W8; ship the product work (W3A first) now; resume W8 as
W8B when production reads exist.** Do **not** abandon W8 (W8B is the correct long-term mechanism
and is preserved). The single worthwhile external-W8 residual is a **cheap, synthetic-only
homogenization check** — no new dataset, no licensing, no month of engineering.

Rationale in one line: the algorithm is already validated, the transfer W8 was meant to enable is
**impossible**, and the remaining scientific value is small and mostly already reproducible — while
W3A/W3B/W4/W5 ship visible product value now.

---

## Part 1 — Is W8 still worth pursuing? **Largely no, for the external-dataset track.**

What we already have (evidence): the RWE algorithm is **peer-validated** (Paudel & Bernstein, WWW
'21); our implementation reproduces it; **`examples/eval_mind.py` already runs the full real-data
RQ2/RQ3 battery** (accuracy + long-tail + ideological diversity, baselines, multi-seed, Wilcoxon)
from a MIND npz with **one command**; the offline pipeline runs deterministically (W8A Phase 1 G1
PASS).

What W8-external can still add, honestly:
- **Nothing on transfer** — disproven. MIND/behavioral IDs don't overlap production; the graph does
  not transfer (`augmented_corpus.py:148-163`). W8's original reason to exist is gone.
- **Little on "does RWE work on real clicks"** — the paper answered it and `eval_mind` reproduces
  it. Re-running it on another dataset is confirmation, not discovery.
- **One genuinely novel thing:** **homogenization over repeated recommend→click cycles** — not in
  the paper, not implemented anywhere in the repo (design-only). That directly tests the product's
  anti-echo-chamber thesis. **But it does not need MIND or Reddit** — it runs on the *existing
  synthetic graph* (which is what production runs today anyway); a real graph only checks the
  synthetic generator isn't hiding something (a second-order check).

**Conclusion:** the external-dataset W8 effort has reached **clear diminishing returns**. The part
that mattered (transfer) is impossible; the part that's easy (real-data eval) is already done; the
part that's novel (homogenization) is cheap and needs no external data.

## Part 2 — Dataset strategy

| Option | Scientific strength | Architectural fit | Engineering cost | Production relevance | Long-term value |
|---|---|---|---|---|---|
| **A. MIND only** | Medium — real news reading, but axis unorientable (`lean_corr=None`) | Native (repo format) | Low (licensing) | **Low** (no transfer) | Low |
| **B. Reddit only** | Medium — validates *fit_ideology*, not the recommender | Repo-ready (user×subreddit) | Low–med (licensing gray area) | **Very low** (subreddits ≠ articles) | Low |
| **C. MIND + Reddit** | **Highest** (complementary) | Both ready | **2×** (two datasets, two licenses) | Low (neither transfers) | Low |
| **D. Skip external; wait for production data** | Forgoes a real-data homogenization check (cover it on synthetic) | Perfect — W8B builds from real reads | **~0 now** | **Highest** | **High** (W8B ready to activate) |
| **E. Synthetic-only homogenization check now, then stop** | Retires the one novel question cheaply | Uses existing `w8a_prototype` design | **Very low** (hours) | Medium | Medium |

**Best: D, with the E addendum.** C is the *research-optimal* answer and the one my earlier
`W8_DATASET_RECOMMENDATION` leaned toward — but for a team **launching a product**, C spends 2×
engineering to more rigorously confirm an already-confirmed algorithm that **cannot** reach
production. D+E aligns effort with when it actually pays off.

## Part 3 — Is Reddit actually representative? **Brutally: no — it validates `fit_ideology`, not the product.**

Reddit interactions are **User→Subreddit** (community co-participation; `ingest_politosphere.py`
item_ids = 605 subreddits). Production is **User→News Article** (reading). Validating on Reddit
would confirm exactly one thing: that the **ideal-point model recovers an orientable ideological
axis from co-participation** (and subreddit labels make `lean_corr` computable). It would **not**
tell us that RWE recommends good *articles*, that article-level co-*reading* behaves like
subreddit co-*joining*, or anything about the served feed, freshness, or outlets.

Worse, it is an **easy, non-representative case that likely *overstates* real-world performance**:
subreddit co-membership (r/Conservative ∧ r/Republican) is a dense, explicit, high-homophily
ideological signal; two strangers reading the same viral article is sparse and noisy. `fit_ideology`
will look *better* on Reddit than it will on real news clicks.

**Honest self-correction:** my earlier `W8_DATASET_RECOMMENDATION` named Reddit "primary" because it
is the best *axis validator*. That is true — but this review concludes **axis-validation is the
least production-relevant W8 objective**, so "best axis validator" must **not** drive engineering
investment now. Reddit would raise confidence in `fit_ideology`, not in the production recommender.

## Part 4 — Production perspective: another month on W8 vs W3A / W3B / W4 / W5? **W8 loses.**

- **W3A** (political mask) — near-zero cost (reuse `classify_topic`), fixes **empirically-confirmed**
  bugs corrupting the flagship metric: `looks_political("selection")=True` (FP),
  `looks_political("congress")=False` (FN). It directly sharpens Open-Mindedness, viewpoint
  diversity, and cross-cutting — the product's core claims.
- **W3B / W4 / W5** — story-level viewpoint / outlet-coverage / queued product features: all
  **user-visible**.
- **W8-external** — produces a validation number (mostly already known), ships **nothing** the user
  sees, and does **not** transfer to production.

A month on W8 external research would **not** improve the product more than W3A/W3B/W4/W5 — and W3A
**alone** almost certainly beats it on product impact per engineering-week. Said plainly: **do the
product work first.**

## Part 5 — Decision: **Option A**

```
Option A  (CHOSEN)
------------------
Implement W3A immediately (it is designed, near-zero cost, high product value).
Then proceed with the queued product work (W3B / W4 / W5) by its own priority.
PAUSE external-dataset W8 (MIND, Reddit, MIND+Reddit).
PRESERVE W8B as the real long-term mechanism; RESUME it when production reads exist.
(Low-cost residual: run the homogenization test on the EXISTING synthetic graph — no
 external dataset, no licensing, hours not weeks — to retire the one novel W8 question.)
```

Not **Option B** (complete W8 now) — it delays the launch to confirm an already-validated algorithm
that can't reach production. Not **Option C** (abandon W8 entirely) — W8B is a correct future design
and the synthetic homogenization check has real, cheap value; abandoning discards both.

**What "resume W8" is gated on:** production traffic sufficient to build a non-trivial behavioral
graph (k-core 5/5 on a real cohort, per `W8B`), at which point W8B — not an external dataset — is
the activation. External datasets re-enter the plan **only** if a specific, launch-relevant question
arises that the synthetic graph and production data cannot answer.

## Part 6 — Evidence ledger (kept separate)

### Evidence (verifiable in-repo or peer-reviewed)
- RWE's diversity/anti-homogenization is peer-validated (Paudel & Bernstein, WWW '21).
- `FeedbackGraph` is built from the click matrix alone (`rwe/graph.py:47-71`); positions only
  orient/re-rank.
- `fit_ideology` fits from co-clicks; labels only orient the axis and need ≥3 (`rwe/mind.py:322`).
- MIND has no recoverable outlet ⇒ `lean_corr` is structurally `None` (W8A audit;
  `resolve_msn_publisher.py` 409).
- Behavioral/MIND item & user IDs do not overlap production ⇒ the graph does **not** transfer
  (`augmented_corpus.py:148-163`).
- `eval_mind.py` already produces the full real-data RQ2/RQ3 + baselines + Wilcoxon from an npz.
- W8A Phase 1 passed G1 (runs + deterministic); homogenization is **design-only** (unbuilt).
- Reddit Politosphere item space = **subreddit** (`ingest_politosphere.py`); production = article.
- W3A fixes empirically-confirmed mask FP/FN (`looks_political("selection")=True`,
  `looks_political("congress")=False`).

### Engineering judgement (defensible inference, not proof)
- Subreddit co-participation is a denser, more explicit ideological signal than news co-reading, so
  Reddit validation likely **overstates** `fit_ideology`'s real-world performance.
- External-dataset W8's marginal value is small: transfer is impossible and real-data eval is
  already available.
- W3A/W4 deliver more visible product value per engineering-week than external W8 research.
- The homogenization question is best answered **first and cheaply** on the synthetic graph.
- W8B is the correct long-term mechanism and should be preserved, activated by traffic.

### Speculation (genuinely uncertain — do not treat as fact)
- Whether RWE's diversity properties will hold on the **eventual production** click graph (real
  reads may be sparser / more viral-skewed than any public dataset).
- Whether production co-read structure will be dense enough to fit a **stable** ideology axis at all.
- Whether the future production behavioral graph will actually beat the synthetic graph (the real
  W8 question — untestable until production data exists).
- The traffic volume / timeline needed before W8B is worth activating.

---

## Bottom line

W8 did its job as an **investigation**: it proved the behavioral graph cannot be transferred, that
the method is already validated, and that the real mechanism (W8B) is gated on production data we do
not yet have. Continuing to invest in **external datasets** now is polishing an answer we already
have at the expense of product value we don't. **Ship W3A and the product roadmap; pause external
W8; keep W8B ready; run one cheap synthetic homogenization check as the wind-down.** Revisit W8 only
when production traffic makes W8B real.

*Documentation only. No production code, prototype, or dataset was created or modified.*
