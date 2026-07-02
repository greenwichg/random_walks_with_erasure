# Synthetic-user simulation — internal product PoC (NOT research)

> **Strict research/product separation.** This is a **product** tool. Everything it
> produces is **simulated** and is **not evidence for the paper**. The research results
> live in `docs/RESULTS.md` and run on *real behaviour* — MIND clicks (`run_mind_eval.ipynb`)
> and Reddit Politosphere behaviour (`run_politosphere_eval.ipynb`). Keep them apart.

## Why it exists

We need to stress-test and iterate on the whole product — RWE recommender, Information
Health Report, AI Coach, recommendation evaluation, and user metrics — *before real users
exist*. `examples/simulate_users.py` generates a realistic synthetic user population reading
a **real** article catalog (Qbias: ~21.7k AllSides-labeled articles → gold lean + real
outlets + topics), so the entire pipeline can be exercised end-to-end today. Once the MVP
has real traffic, these synthetic interactions are replaced with real behaviour.

**Items are real; users and all interactions are synthetic.** Article lean is AllSides gold;
article *quality* and every user trait/click/dwell/action are simulated.

## The agents (independent traits)

Each agent is drawn from plausible population distributions with independent characteristics:

| Trait | Meaning | Distribution |
|---|---|---|
| viewpoint (θ) | political ideal point in [-2, 2] | bimodal (left/right clusters + smaller centre) |
| topic interests | preference over topics | Dirichlet (peaky per user) |
| openness | tolerance of opposing viewpoints | Beta, skewed low |
| outlet trust | per-publisher trust | congenial outlets trusted more, *widened by openness* |
| quality preference | sensitivity to article quality | Beta |
| curiosity | novelty seeking (unseen topics/outlets) | Beta |
| activity | scales number of sessions | Beta (long-ish tail) |
| reading speed | dwell multiplier | log-normal |
| save / share propensity | base action rates | Beta (low) |

**Choice model.** In a session the agent sees an organic slate (popularity × topic
relevance) and clicks each item with `sigmoid` of a utility combining: ideology alignment
*gated by openness* (open agents click cross-cutting), topic interest, outlet trust, article
quality × quality-preference, and curiosity × novelty. Clicks get a dwell time and a
**save / share / ignore** action driven by satisfaction (alignment × quality). Model validity
is unit-tested: higher-openness agents measurably click more cross-cutting content.

## Outputs (all stamped `sim_*` / SIMULATION)

- `sim_users.npz` — a `MINDData` that drops into the existing pipeline unchanged;
  `item_positions` = **gold** lean, `user_positions` = the **true** synthetic viewpoints.
- `sim_population.csv` — per-user traits + realised metrics (the "user metrics").
- `sim_satisfaction_probe.csv` — probe-shaped (`cross_welcomed_frac` = save/share vs ignore
  on cross-cutting clicks) so `adaptive_satisfaction.py` closes the loop on synthetic data.
- `sim_MANIFEST.txt` — config + seed + the SIMULATION stamp.

## Run it

Notebook: **`notebooks/product_simulation.ipynb`** (clone → optional Qbias download →
simulate → eval → health report → AI coach → closed loop → user metrics). Or:

```
python examples/simulate_users.py --qbias allsides_balanced_news_headlines-texts.csv \
    --n-users 3000 --max-items 5000 --out-tag sim          # omit --qbias for a synthetic catalog
python examples/eval_mind.py --npz sim_users.npz --no-bprmf
python examples/health_report.py --npz sim_users.npz --sample 3 --require-political --html sim_health.html
python examples/narrate_report.py --npz sim_users.npz
python examples/adaptive_satisfaction.py --npz sim_users.npz --probe-csv sim_satisfaction_probe.csv
```

On the sim catalog the health report's **Source Diversity populates** (real outlets) — the
section MIND could never fill (MSN URLs) — alongside Topic Diversity, Viewpoint, and Echo on
the gold axis: the most complete report in the project, as a *demo*.

## What it is and isn't

- ✅ A **system stress test** — does the whole pipeline run end-to-end and produce sane
  numbers on realistic traffic? A **demo** of the report + coach on a clean gold axis with
  real outlets. A **sandbox** to iterate on UX, thresholds, and the closed loop.
- ❌ **Not** an accuracy or bridging *result*. RQ2 on synthetic clicks recovers the generative
  model by construction; RQ3 bridging is a mechanism illustration. No number here is evidence.

The migration path: as real usage arrives, swap the synthetic interaction stream for real
clicks/dwell/actions — the same `MINDData`/probe interfaces, so the pipeline is unchanged.

_Product PoC. Not cited in the paper._
