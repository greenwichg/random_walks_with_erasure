# W8A — Phase 2 Readiness (Prototype Prepared)

**Status:** The offline prototype `examples/w8a_prototype.py` now implements every Phase-2
prerequisite from the architecture audit. **The MIND-full evaluation has NOT been executed** —
this change only *prepares* the prototype. Still offline; still import-isolated (nothing imports
it; it imports only library code). No production / serving / API / report-contract /
explainability code was modified, and `eval_mind` / `rwe` are **reused, not changed** (MIND tests
still pass).

## What changed since Phase 1 (audit prerequisites → implemented)

| Audit item | Implementation |
|---|---|
| **Train/test leakage** | `leakfree_eval()` refits ideology on **each training split** (`IdeologyModel.fit(train_matrix)`), never the full matrix. Held-out clicks never inform the positions used to recommend or score. |
| **Reuse `eval_mind`** | The eval reuses `eval_mind._recommenders` (full baseline set: ItemKNN / P3 / RP3-beta / RWE-D / RWE-B / BPRMF), `rwe.experiment.compare`, and `eval_mind._wilcoxon_vs_ref` (paired significance). Only the seed loop is local — because `eval_mind._eval_across_seeds` reuses fixed full-dataset positions (the leak). |
| **Gold-vs-fitted asymmetry** | Both graphs (synthetic A, behavioral B) run the **same** leak-free refit path → fitted-vs-fitted → the comparison isolates *graph* quality, not label quality. |
| **Scalability to MIND full** | `ingest()` wires the existing strategy: k-core (`--min-user-clicks/--min-item-clicks`), `political_subset(require_lean=False)` (`--political-only`), `sample_users` (`--sample-users`). `preflight()` reports users×items vs the `max_cells` guard **and estimated dense-fit GB** before any fit; every fit stage skips gracefully (never crashes) when a matrix exceeds `--max-cells`. |
| **Runtime + memory** | Per-stage wall-time and tracemalloc peak via `_timed`; process **peak RSS** via `resource.getrusage` (the number that catches a dense-fit blow-up). |
| **Stability (gate metric 7)** | `stability_diagnostics()` refits under N seeds and reports **sign-invariant** agreement: mean \|Spearman\| of item positions + mean top-k Jaccard of RWE-B recs. |
| **Graph diagnostics** | `graph_diagnostics()` adds degree **percentiles**, component-**size** distribution, item-degree **Gini**, and **top-1% item click share** (popularity) — not just means. |
| **Convergence diagnostics** | Objective trace + monotonicity + **per-restart final-objective spread** (fit-stability signal). |
| **Axis interpretability** | `axis_proxy()` reports \|corr with the political flag\| and category **η²** — the substitute for `lean_corr` (structurally `None` on label-free MIND). |
| **`_DatasetLike` shim** | Removed — the real `MINDData.dataset` is passed to `train_test_split`. |

## Verified on the license-free fixture (`tests/fixtures/mind_demo`, `--seeds 3`)

- **Determinism (G1):** `--det-check` → PASS on the leak-free fit-on-train path.
- **Leak-free eval runs the full baseline set** (ItemKNN / P3 / RP3-beta / RWE-D / RWE-B) with
  Wilcoxon-vs-P3 wired; `positions = "refit on each training split"`, `leak_free = true`.
- **Stability:** \|Spearman\| = 1.0, top-k Jaccard = 1.0 (trivially stable at 4×8; the
  instrument works).
- **Axis-proxy immediately flags a real issue:** category **η² = 0.65** on the fixture — the
  fitted axis is **largely topical**, not obviously ideological. Exactly the interpretability
  signal the audit demanded; on MIND full this decides whether H1/H3 can claim "ideology."
- **Instrumentation present:** per-stage timings, tracemalloc peaks, peak RSS all recorded.
- **Preflight** sizes the fit (cells vs `max_cells`, estimated dense GB) with no fit executed.

The fixture remains a plumbing proof only — **no statistical claim** (held-out split is one
user). The value here is that every Phase-2 mechanism is exercised and green.

## The MIND-full command (to run in Phase 2, once unblocked — do not run yet)

```bash
# 1. size it first (no fit): confirm the filtered matrix sits under max_cells
python examples/w8a_prototype.py --fixture <MIND_dir> --preflight \
    --political-only --min-user-clicks 5 --min-item-clicks 5 --sample-users 8000

# 2. full leak-free run: 7 seeds, full baselines incl. BPRMF, larger cutoffs
python examples/w8a_prototype.py --fixture <MIND_dir> --out-dir <dir> \
    --political-only --min-user-clicks 5 --min-item-clicks 5 --sample-users 8000 \
    --seeds 7 --bprmf --top-k 10 --diversity-k 20
```

## Still blocking Phase 2 execution (unchanged)

- **MIND licensing** — Microsoft terms must be cleared before downloading MIND full.
- **Homogenization (gate metric 8)** — intentionally **not** implemented here; it needs a
  multi-round feedback loop at scale and belongs to Phase 3, not the prototype.
- **Cold-start bucketing (gate metric 6)** — not yet wired (the fixture cannot exercise it);
  add per-click-count buckets when MIND full is available.

## Scope confirmation

Files changed: `examples/w8a_prototype.py` (offline prototype), `docs/W8_EVALUATION_AND_DECISION_GATE.md`
(methodology refinements), and this readiness note. No production, serving, API, report-contract,
or explainability code; `eval_mind`/`rwe` reused unchanged. The prototype is ready; Phase 2
execution awaits licensing and an explicit go-ahead.
