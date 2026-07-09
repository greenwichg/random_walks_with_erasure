# Notebooks

## `run_mind_eval.ipynb` — real-data RQ2/RQ3 on MIND-small

A one-click way to produce the paper's accuracy / long-tail (RQ2) and
ideological-diversity (RQ3) tables on **real** data, with no local setup and
without touching the dataset license (MIND is downloaded from Microsoft's
official source at runtime — research use, not redistributed).

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/greenwichg/random_walks_with_erasure/blob/c41d26fccfa261f7b23a0666d3fa1756f3345f85/notebooks/run_mind_eval.ipynb)

The notebook: clones this branch → `pip install -e .` → downloads & unzips
`MINDsmall_train` → `ingest_mind.py --ideology` (learns user+item positions from
clicks, capped with `--sample-users` so the dense fit fits) → `eval_mind.py` →
prints the tables and offers `results.csv` for download.

Notes:

- If this repo is **private**, the "Open in Colab" button needs you signed into
  GitHub in Colab, and the in-notebook `git clone` needs a token in the URL
  (`https://<TOKEN>@github.com/...`) — see the first cell's comment.
- First pass uses `--no-bprmf` for speed; remove it to add the BPRMF baseline.
- Check the printed `lean_corr`: closer to ±1 means the latent axis really is
  left–right (validate against external scores before trusting RQ3).

## `product_simulation.ipynb` — synthetic-user product PoC (NOT research)

An **internal product stress-test**, kept strictly separate from the research
notebook above. **Every user and interaction is simulated — none of it is evidence
for the paper.** An agent-based simulator (`examples/simulate_users.py`) generates a
synthetic user population with independent traits (viewpoint, topic interests,
openness to opposing views, per-outlet trust, quality preference, curiosity, activity,
reading time, save/share/ignore) reading a **real** article catalog (Qbias — gold
AllSides lean + real outlets + topics), then runs the whole product on that traffic.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/greenwichg/random_walks_with_erasure/blob/c41d26fccfa261f7b23a0666d3fa1756f3345f85/notebooks/product_simulation.ipynb)

The notebook: clone → (optional) Qbias download → `simulate_users.py` →
`eval_mind.py` (a system stress test, **not** an accuracy claim — synthetic clicks
recover the generative model) → `health_report.py` (Source Diversity finally
populates: real outlets, unlike MIND) → `narrate_report.py` (AI Coach) →
`adaptive_satisfaction.py` (closed loop) → user-metrics summary. Outputs are stamped
`SIMULATION`. See [`../docs/PRODUCT_SIMULATION.md`](../docs/PRODUCT_SIMULATION.md).
