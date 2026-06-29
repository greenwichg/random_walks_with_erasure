# Paper — LaTeX source

LaTeX build of the draft in [`../PAPER.md`](../PAPER.md), with the MovieLens-1M
replication and per-user significance folded in. Numbers come from
[`../RESULTS.md`](../RESULTS.md); figures from [`../images/`](../images).

## Files

| File | What |
|---|---|
| `paper.tex` | Main source — ACM `sigconf` (`acmart`), `nonacm` (draft, no copyright block) |
| `references.bib` | BibTeX — **read the header**: some entries need author/page verification |

## Compile

**No local TeX is required** — the easiest path is **Overleaf**:

1. New Project → Upload Project, and include **both** `docs/paper/` and
   `docs/images/` (keep the relative layout, so `../images/` resolves). The
   simplest way is to zip the whole `docs/` folder and upload that.
2. Set the main document to `paper.tex`, compiler **pdfLaTeX**.
3. `acmart` and the `ACM-Reference-Format` style are built into Overleaf — no
   extra install.

Locally (if you have a full TeX Live with `acmart`):

```bash
cd docs/paper
pdflatex paper && bibtex paper && pdflatex paper && pdflatex paper
```

`\graphicspath` already points at `../images/`, `./images/`, and `./figures/`, so
figures resolve whether you keep the repo layout or copy the PNGs in next to
`paper.tex`.

## Before submitting — checklist

- [ ] **Verify the BibTeX.** Page numbers / DOIs / author lists were drafted from
      secondary sources. The entries flagged `% TODO verify` (`drdw2025`,
      `network2024polarization`, `stray2023bridging`) need their author lists
      confirmed against the actual papers.
- [ ] **Pick the venue and reformat if needed.** `sigconf` fits the RecSys
      workshops (NORMalize, FAccTRec, RS4Good), FAccT, and EAAMO. For **ICWSM**
      switch to the AAAI style; for **ECIR/SIGIR** reproducibility tracks use
      their LNCS / ACM template. See [`../PAPER_PLAN.md`](../PAPER_PLAN.md) §4.
- [ ] **Author block** — confirm name, affiliation, and email in `paper.tex`.
- [ ] **Add CCS concepts / ACM reference** once the venue is fixed (currently
      suppressed via `printacmref=false` for the draft).
- [ ] **Regenerate figures** if any numbers change: `python docs/make_paper_figs.py`.

## What changed vs. `../PAPER.md`

- Added the **MovieLens-1M** long-tail replication (Table 2) — a second public
  dataset for RQ1.
- Added **per-user paired significance** (§6.4) — upgrades the significance claim
  from across-seed (`n=7`) to per-user (`n≈2.5k`).
- Updated the limitations: significance and single-dataset concerns are now
  partly addressed (only the *ideological* half rests on one corpus).
