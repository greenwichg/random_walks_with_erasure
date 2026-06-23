# Notebooks

## `run_mind_eval.ipynb` — real-data RQ2/RQ3 on MIND-small

A one-click way to produce the paper's accuracy / long-tail (RQ2) and
ideological-diversity (RQ3) tables on **real** data, with no local setup and
without touching the dataset license (MIND is downloaded from Microsoft's
official source at runtime — research use, not redistributed).

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/greenwichg/random_walks_with_erasure/blob/claude/sleepy-gates-oecof1/notebooks/run_mind_eval.ipynb)

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
