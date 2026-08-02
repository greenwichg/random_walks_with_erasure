"""Validate the text-lean classifier against AllSides GOLD labels using the Qbias dataset.

Qbias (Haak & Schaer 2023, github.com/irgroup/Qbias) ships
``allsides_balanced_news_headlines-texts.csv``: ~21,747 news articles scraped from
AllSides, each with a human **left / center / right** bias label (four AllSides expert
annotators), the headline, full text, topic tags, and the publishing **outlet**. That is
the *largest human-gold check available* for the text-lean axis -- far beyond the
40-headline set or the model-vs-model LLM check (``docs/RESULTS.md`` Limitation 1). This
runs ``classify_lean``'s classifier on the headlines and reports agreement with the
AllSides label (Spearman / Cohen's kappa / 3-class accuracy / confusion), plus how well an
**outlet-lean** join (``examples/data/outlet_lean.csv``) predicts the same gold labels --
the outlet branch that was 409-blocked on MIND, working here because Qbias carries outlets.

**IMPORTANT caveat -- this is IN-DISTRIBUTION, an *upper bound*.** The default classifier
(``bucketresearch/politicalBiasBERT``) is itself trained on AllSides-sourced articles
(Baly et al. 2020), and Qbias is AllSides-sourced, so agreement here shares labeling
methodology (and possibly some articles).

**What the run actually shows (n=3000, 2026-07-02).** In-distribution, text-lean is a
**weak, model-sensitive** proxy vs the human AllSides label -- two AllSides-trained models
differ sharply:

* politicalBiasBERT : near-chance at every length (headline kappa ~0.007; body ~0.001,
  Spearman ~0.065 -- identical at 256 and 512 tokens, because Qbias ships short excerpts);
* premsa (2nd model): near-chance on the headline (Spearman ~0.08) but reaches Spearman
  **~0.22** / side-only kappa **~0.30** *with the body* -- weak, but clearly non-zero.

So (a) text is **not** signal-free -- a better model extracts a faint lean from the body;
(b) part of politicalBiasBERT's extreme near-zero is **model miscalibration**, not purely
"the label isn't in the text"; and (c) for premsa the headline *was* a limiter. But even the
better model is faint. The **outlet-lean** join dwarfs both: it recovers the same gold at
kappa ~0.84 / Spearman ~0.92 / side-only ~1.0 -- **~4x** the best text model -- so the lean
lives far more in the **publisher** than the words, and the outlet-first hybrid is the fix.
(NB ``--use-text`` feeds the first ``--max-length`` tokens, default **256**, up to BERT's
512; on Qbias the excerpts are short so 512 ~ 256 -- a true full-article test would need a
*new* full-text+gold corpus (Qbias has none) and a long-context model; out of scope, and it
would not change the outlet-first conclusion.)

    # download the CSV once from github.com/irgroup/Qbias, then (GPU recommended):
    python examples/validate_qbias.py --csv allsides_balanced_news_headlines-texts.csv \
        --lean-csv examples/data/outlet_lean.csv --limit 3000
    # decisive follow-up -- score more of the article body (--max-length up to BERT's 512):
    python examples/validate_qbias.py --csv allsides_balanced_news_headlines-texts.csv \
        --lean-csv examples/data/outlet_lean.csv --limit 3000 --use-text --max-length 512
"""

from __future__ import annotations

import argparse
import csv as _csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))     # import sibling example scripts
from classify_lean import load_classifier, score_texts       # noqa: E402
from lean_agreement import _fmt_confusion, pair_reliability   # noqa: E402

from rwe.mind import _norm, load_lean_table                   # noqa: E402

_HEADLINE_COLS = ("heading", "headline", "title")
_TEXT_COLS = ("text", "body", "content", "article", "article_text")
_BIAS_COLS = ("bias_rating", "bias", "label", "allsides_bias", "rating", "bias_label")
_OUTLET_COLS = ("source", "outlet", "news_outlet", "publisher", "source_name", "media")


def _pick_col(fieldnames, candidates, override=None):
    """First column in ``candidates`` present in ``fieldnames`` (case-insensitive)."""
    lower = {c.lower(): c for c in (fieldnames or [])}
    if override:
        return override if override in (fieldnames or []) else lower.get(override.lower())
    for cand in candidates:
        if cand in lower:
            return lower[cand]
    return None


#: Graded position for the *lean* variants under ``label_to_pos(graded=True)``. 0.6, not 0.5, and
#: the value is load-bearing: the report's centre bucket is ``|pos| <= 0.5`` INCLUSIVE
#: (health_report.LEAN_TAU), cross-cutting needs ``|pos| >= 0.5``, and the web buckets cut strictly
#: at ``> 0.5`` — an article AT ±0.5 would count "centre" in the report while "cross-cutting" in
#: the feed, an inconsistency no position may occupy. ±0.6 is sided under every cut in the system.
LEAN_GRADE = 0.6


def label_to_pos(label, graded: bool = False) -> float:
    """AllSides bias label -> gold position ``-1`` (left) / ``0`` (center) / ``+1`` (right);
    ``nan`` if unrecognised. Tolerant of 'lean left', 'centrist', 'neutral', etc.

    ``graded=True`` (the corpus-construction path) resolves the *lean* variants to
    ``±LEAN_GRADE`` instead of snapping them onto the poles — the fractional-leans work
    (docs/RECOMMENDATION_STRENGTH_SLIDER.md): with only three positions in ranking space,
    every distance-graded recommendation knob measured inert. The DEFAULT stays 3-point
    because this CLI's gold enumeration — and the Qbias dataset itself — are 3-class."""
    s = str(label).strip().lower()
    if not s or s in ("nan", "none", "mixed", "n/a"):
        return float("nan")
    if graded and "lean" in s:          # 'lean left', 'leans right', 'left-leaning', 'lean-left'
        if "left" in s:
            return -LEAN_GRADE
        if "right" in s:
            return LEAN_GRADE
    if s in ("left", "lean left", "leans left", "left-leaning", "-1"):
        return -1.0
    if s in ("right", "lean right", "leans right", "right-leaning", "1", "+1"):
        return 1.0
    if s in ("center", "centre", "centrist", "neutral", "least biased", "0"):
        return 0.0
    if "left" in s:                                          # substring fallbacks
        return -1.0
    if "right" in s:
        return 1.0
    if "cent" in s or "neutral" in s:
        return 0.0
    return float("nan")


def load_qbias(path, headline_col=None, text_col=None, bias_col=None, outlet_col=None,
               use_text=False, limit=None):
    """Read the Qbias CSV -> ``(texts, gold_positions, outlets, cols)``.

    Columns are auto-detected (override with ``--*-col``). ``texts`` is the headline, or
    ``headline + text`` when ``use_text``. Rows with no recognisable gold label are dropped.
    """
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        rd = _csv.DictReader(f)
        fn = rd.fieldnames
        hc = _pick_col(fn, _HEADLINE_COLS, headline_col)
        tc = _pick_col(fn, _TEXT_COLS, text_col)
        bc = _pick_col(fn, _BIAS_COLS, bias_col)
        oc = _pick_col(fn, _OUTLET_COLS, outlet_col)
        if hc is None or bc is None:
            raise SystemExit(f"could not find a headline/bias column in {fn}; pass "
                             "--headline-col / --bias-col explicitly")
        texts, gold, outlets = [], [], []
        for row in rd:
            g = label_to_pos(row.get(bc, ""))
            if not np.isfinite(g):
                continue
            head = (row.get(hc) or "").strip()
            body = (row.get(tc) or "").strip() if (use_text and tc) else ""
            texts.append((head + ". " + body).strip(". ") if body else head)
            gold.append(g)
            outlets.append((row.get(oc) or "").strip() if oc else "")
            if limit and len(texts) >= limit:
                break
    return texts, np.asarray(gold, dtype=float), outlets, dict(headline=hc, text=tc,
                                                               bias=bc, outlet=oc)


def outlet_positions(outlets, lean_table) -> np.ndarray:
    """Per-article outlet-lean from ``lean_table`` (keyed by ``_norm``); ``nan`` if the
    outlet is absent -- so a name OR a domain both join (``_norm`` unifies them)."""
    out = np.full(len(outlets), np.nan)
    for i, o in enumerate(outlets):
        if o:
            v = lean_table.get(_norm(o))
            if v is not None:
                out[i] = float(v)
    return out


def _report_block(title, r, names=("classifier", "AllSides gold")):
    L = [f"=== {title}  (n={r['n']}) ===",
         f"  continuous : Spearman {r['spearman']:+.3f}   Pearson {r['pearson']:+.3f}",
         f"  L/C/R      : Cohen kappa {r['kappa3']:+.3f}   accuracy {100*r['exact']:.0f}%",
         f"  side only  : Cohen kappa {r['kappa_side']:+.3f}  (Left vs Right, Centers dropped)",
         _fmt_confusion(r["confusion"])]
    return "\n".join(L)


def run(csv_path, score_fn=None, lean_csv=None, model="bucketresearch/politicalBiasBERT",
        label_positions=(-1, 0, 1), scale=2.0, use_text=False, limit=None, max_length=256,
        headline_col=None, text_col=None, bias_col=None, outlet_col=None):
    texts, gold, outlets, cols = load_qbias(csv_path, headline_col, text_col, bias_col,
                                            outlet_col, use_text=use_text, limit=limit)
    if not texts:
        return "no labelled rows loaded — check the CSV / column overrides."
    dist = {int(v): int((gold == v).sum()) for v in (-1, 0, 1)}
    lines = [f"loaded {len(texts)} labelled articles from {csv_path}",
             f"  columns: {cols}",
             f"  AllSides gold: L={dist[-1]}  C={dist[0]}  R={dist[1]}", ""]

    if score_fn is None:                                     # real model (GPU)
        span = f"headline+body <=" + str(max_length) + " tok" if use_text else "headlines"
        print(f"scoring {len(texts)} {span} with {model} ...")
        tok, mdl, device = load_classifier(model)
        score_fn = lambda t: score_texts(t, tok, mdl, device, label_positions,
                                         scale=scale, max_length=max_length)[0]
    text_pos = np.asarray(score_fn(texts), dtype=float)

    r = pair_reliability(text_pos, gold, band=1.0)
    lines.append(_report_block("TEXT classifier  vs  AllSides gold", r))
    lines.append("")

    if lean_csv:
        table = load_lean_table(lean_csv)
        opos = outlet_positions(outlets, table)
        cov = int(np.isfinite(opos).sum())
        lines.append(f"outlet-lean join ({lean_csv}): {cov}/{len(outlets)} articles have an "
                     f"outlet in the table ({100*cov/max(len(outlets),1):.0f}% coverage)")
        if cov >= 3:
            ro = pair_reliability(opos, gold, band=1.0)
            lines.append(_report_block("OUTLET-lean  vs  AllSides gold", ro))
        lines.append("")

    cond = (f"headline + article body, first {max_length} tokens (--use-text)" if use_text
            else "HEADLINE ONLY — MIND's condition")
    lines.append(
        f"CAVEAT: IN-DISTRIBUTION, scored on {cond}. This model ({model}) is AllSides-trained "
        "and Qbias is AllSides-sourced, so this shares labeling method (possibly articles). NB "
        "across two AllSides models, text-lean is a WEAK, MODEL-SENSITIVE proxy vs human gold: "
        "politicalBiasBERT is near-chance (kappa ~0.007 headline / ~0.001 body), while premsa "
        "reaches only Spearman ~0.22 with the body — so part of the near-zero is model "
        "miscalibration, not purely 'the label isn't in the text' (and Qbias ships short "
        "excerpts, so 512 tok ~ 256). Either way the OUTLET-lean number (Spearman ~0.92, kappa "
        "~0.84) DWARFS the best text model (~4x), which is why the outlet-first hybrid is the "
        "fix. (The MIND kappa=0.14 is classifier-vs-classifier; this is classifier-vs-human-gold "
        "— different references.)")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True, help="Qbias allsides_balanced_news_*.csv")
    ap.add_argument("--lean-csv", default=None,
                    help="outlet-lean table to also test vs gold (examples/data/outlet_lean.csv)")
    ap.add_argument("--model", default="bucketresearch/politicalBiasBERT")
    ap.add_argument("--label-positions", default="-1,0,1",
                    help="position per logit index (match the model's id2label; pass with '=')")
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--use-text", action="store_true",
                    help="score headline + article text (default: headline only, comparable "
                         "to MIND's headline-level axis -- the domain-shift control)")
    ap.add_argument("--max-length", type=int, default=256,
                    help="token cap fed to the classifier (default 256). With --use-text, "
                         "raise to the model's max (BERT: 512) to score more of the body; note "
                         "even 512 is only the opening for long articles, and it doubles runtime")
    ap.add_argument("--limit", type=int, default=None, help="cap #articles (start small)")
    ap.add_argument("--headline-col", default=None)
    ap.add_argument("--text-col", default=None)
    ap.add_argument("--bias-col", default=None)
    ap.add_argument("--outlet-col", default=None)
    args = ap.parse_args()

    lp = [float(x) for x in args.label_positions.split(",")]
    print(run(args.csv, lean_csv=args.lean_csv, model=args.model, label_positions=lp,
              scale=args.scale, use_text=args.use_text, limit=args.limit,
              max_length=args.max_length, headline_col=args.headline_col,
              text_col=args.text_col, bias_col=args.bias_col, outlet_col=args.outlet_col))


if __name__ == "__main__":
    main()
