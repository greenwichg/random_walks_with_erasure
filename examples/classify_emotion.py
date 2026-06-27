"""Classify each MIND article's emotional tone (EXPERIMENTAL) — the *attention*
signal for the Information Health Report's Attention Profile / Emotional Balance.

Zero-shot over title+abstract against a custom bucket set; runs on a GPU (Colab).

    *** Honesty caveat (read docs/HEALTH_REPORT_PLAN.md) ***
    Emotion-from-headline is NOISY, classifiers disagree, and this bucket set
    mixes *emotion* (fear, outrage, positive) with *register* (analysis).  Treat
    the output as a rough, LOW-CONFIDENCE signal, not a precise measurement.  The
    Health Report renders it labelled "experimental" for exactly this reason.

    pip install transformers torch
    python examples/classify_emotion.py --mind-dir MINDsmall_train --political-only --out emotion.csv
    python examples/health_report.py --npz mind_text.npz --emotion-csv emotion.csv

Outputs ``news_id,fear,outrage,analysis,positive,neutral`` — a per-article
distribution over the buckets (each row sums to 1).
"""

import argparse
from pathlib import Path

import numpy as np

from rwe.mind import DEFAULT_POLITICAL_TERMS, _is_political


def _read_articles(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 5:
                rows.append((p[0], p[2], p[3], p[4]))
    return rows


def _text(title, abstract):
    title, abstract = (title or "").strip(), (abstract or "").strip()
    return (title + ". " + abstract).strip(". ") if abstract else title


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mind-dir", required=True, help="directory with news.tsv")
    ap.add_argument("--out", default="emotion.csv")
    ap.add_argument("--model", default="facebook/bart-large-mnli",
                    help="HF zero-shot (NLI) model")
    ap.add_argument("--labels", default="fear,outrage,analysis,positive,neutral",
                    help="emotional buckets (comma-separated)")
    ap.add_argument("--political-only", action="store_true")
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--max-length", type=int, default=256)
    ap.add_argument("--limit", type=int, default=None, help="score only N articles (debug)")
    args = ap.parse_args()

    print("*** EXPERIMENTAL: emotion-from-headline is noisy; treat as low-confidence. ***")
    arts = _read_articles(Path(args.mind_dir) / "news.tsv")
    if args.political_only:
        arts = [a for a in arts if _is_political(a[1], a[2], DEFAULT_POLITICAL_TERMS)]
    if args.limit:
        arts = arts[: args.limit]
    nids = [a[0] for a in arts]
    texts = [_text(a[2], a[3]) for a in arts]
    labels = [s.strip() for s in args.labels.split(",")]
    print(f"zero-shot {len(texts)} articles with {args.model}  buckets={labels}")

    import torch                                          # lazy: only needed here
    from transformers import pipeline

    device = 0 if torch.cuda.is_available() else -1
    clf = pipeline("zero-shot-classification", model=args.model, device=device)

    M = np.full((len(texts), len(labels)), np.nan)
    for s in range(0, len(texts), args.batch_size):
        batch = texts[s: s + args.batch_size]
        res = clf(batch, candidate_labels=labels, multi_label=False,
                  truncation=True, max_length=args.max_length)
        if isinstance(res, dict):
            res = [res]
        for j, r in enumerate(res):
            d = dict(zip(r["labels"], r["scores"]))
            M[s + j] = [d.get(l, 0.0) for l in labels]
        if s % (args.batch_size * 10) == 0:
            print(f"  {s + len(batch)}/{len(texts)}")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("news_id," + ",".join(labels) + "\n")
        for nid, row in zip(nids, M):
            f.write(nid + "," + ",".join(f"{v:.4f}" for v in row) + "\n")
    means = np.nanmean(M, axis=0)
    print(f"wrote {args.out}  mean profile: "
          + "  ".join(f"{l} {m * 100:.0f}%" for l, m in zip(labels, means)))

    for k, lab in enumerate(labels[:2]):                  # eyeball the top two buckets
        order = np.argsort(-M[:, k])
        print(f"\nMost '{lab}'-scored headlines:")
        for i in order[:6]:
            print(f"  {M[i, k]:.2f}  {arts[i][2]}")
    print("\n(reminder: experimental — do not present these as precise measurements)")


if __name__ == "__main__":
    main()
