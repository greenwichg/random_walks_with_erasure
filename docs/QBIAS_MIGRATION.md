# Migrating the reference corpus to Qbias

InfoDiet's reports rank a reader against a **reference population**. The default profile,
`synthetic`, generates that population over made-up outlets (`outlet_0`, `outlet_1`, …), so
onboarding shows placeholder names and a reader's real reads never line up with the population.
This guide switches the reference to **Qbias** — a real AllSides-rated news corpus whose outlets
(Fox, CNN, NYT, WaPo, NPR, WSJ, …) are exactly the ones the browser extension captures.

The switch is **configuration only**. The one preprocessing step canonicalizes Qbias's outlet
names through the same registry ingestion uses, so a reader's `nytimes.com` reads and the
population's `New York Times` articles share one outlet vocabulary. No research code changes.

---

## 1. Obtain the raw Qbias dataset

Qbias is public (IR Group, University of Cologne). Download the AllSides balanced-news CSV
(~14 MB, ~21.8k articles):

```bash
mkdir -p data
curl -L -o data/qbias_raw.csv \
  https://raw.githubusercontent.com/irgroup/Qbias/main/allsides_balanced_news_headlines-texts.csv
```

Columns used: `heading` (headline), `source` (outlet), `bias_rating` (AllSides left/center/right).
Everything under `data/` is git-ignored, so raw and cleaned datasets are never committed.

> Cite Qbias per its repository if you publish results; the AllSides lean labels are coarse,
> contested, and time-dependent (same caveat as the bundled `outlet_lean.csv`).

## 2. Preprocess (canonicalize the outlet column)

```bash
python examples/prepare_qbias.py --in data/qbias_raw.csv --out data/qbias_clean.csv
```

This rewrites `source` to the canonical outlet name from `examples/data/outlet_registry.csv`
(`"Fox News (Online News)"` → `"Fox News"`), leaving outlets the registry doesn't know untouched
(no article is dropped). The cleaned CSV keeps Qbias's schema, so the existing
`simulate_users.catalog_from_qbias` reads it unchanged.

**Verify before switching** (writes nothing — this is the verification script):

```bash
python examples/prepare_qbias.py --in data/qbias_raw.csv --report
```

It reports the number of canonical outlets, how many Qbias sources matched, the percentage of
articles canonicalized, and any unmatched outlets (kept as-is).

## 3. Configure the application

Point the engine at the cleaned dataset via environment variables (read identically by the CLI
`examples/api_fastapi.py` and by any deployment):

| Variable | Value | Purpose |
|---|---|---|
| `RWE_PROFILE` | `qbias` | Select the Qbias reference profile |
| `RWE_QBIAS` | `data/qbias_clean.csv` | Path to the **cleaned** dataset |
| `RWE_SEED` | e.g. `0` | Simulated-population seed — **pin it** for reproducible reports |
| `RWE_N_USERS` | e.g. `2000` | Size of the simulated reference population |
| `RWE_MAX_ITEMS` | e.g. `4000` | Catalog size (subsample of the corpus) |

```bash
export RWE_PROFILE=qbias
export RWE_QBIAS=data/qbias_clean.csv
export RWE_SEED=0
export RWE_N_USERS=2000
export RWE_MAX_ITEMS=4000
python examples/api_fastapi.py --port 8000
```

### Deterministic builds

The reference population is simulated over the real Qbias catalog. `RWE_SEED`, `RWE_N_USERS`, and
`RWE_MAX_ITEMS` fully determine it, so **pinning all three keeps every reader's percentile stable
across restarts and deploys**. Changing any of them re-draws the population and will shift scores;
treat them as release-pinned config, not per-run knobs.

## 4. Switching between synthetic and Qbias

Config-only, no redeploy of code:

```bash
# Qbias (real outlets + population)
RWE_PROFILE=qbias RWE_QBIAS=data/qbias_clean.csv RWE_SEED=0 python examples/api_fastapi.py

# Synthetic (default; no external data, placeholder outlets)
RWE_PROFILE=synthetic python examples/api_fastapi.py
```

Leaving `RWE_PROFILE` unset defaults to `synthetic`, so a clean checkout still runs with zero
external data. The CLI flags `--profile qbias --qbias data/qbias_clean.csv` are equivalent to the
env vars for local runs.

---

## What this migration does and doesn't change

- **Fixes:** real, recognizable outlet names in onboarding; a realistic population baseline;
  reads merging with the population's outlet buckets (ingestion + corpus share one vocabulary).
- **Unchanged:** all research algorithms and the JSON API contract; the population is still
  *simulated* over the real articles (a real click-log population, e.g. MIND, is a later option).
- **Still deferred:** Reporting Ratio / Emotional Balance for real reads need a register/emotion
  enricher on ingested headlines — independent of the corpus, tracked separately.
