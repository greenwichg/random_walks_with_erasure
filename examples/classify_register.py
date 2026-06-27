"""Classify each MIND article as news-reporting vs opinion/editorial — the
*register* signal for the Information Health Report's **Reporting Ratio**.

Zero-shot (NLI) over title+abstract, so no fine-tuning or labels needed; runs on
a GPU (Colab).  Headline-only signal -> treat the score as approximate.

    pip install transformers torch
    python examples/classify_register.py --mind-dir MINDsmall_train --political-only --out register.csv
    python examples/health_report.py --npz mind_text.npz --register-csv register.csv

Outputs ``news_id,reporting`` where ``reporting`` = P("news report") in [0, 1]
(1 = looks like straight reporting, 0 = looks like opinion/editorial).
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
                rows.append((p[0], p[2], p[3], p[4]))   # id, subcat, title, abstract
    return rows


def _text(title, abstract):
    title, abstract = (title or "").strip(), (abstract or "").strip()
    return (title + ". " + abstract).strip(". ") if abstract else title


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mind-dir", required=True, help="directory with news.tsv")
    ap.add_argument("--out", default="register.csv")
    ap.add_argument("--model", default="facebook/bart-large-mnli",
                    help="HF zero-shot (NLI) model")
    ap.add_argument("--labels", default="news report,opinion or editorial",
                    help="candidate labels; the FIRST is the 'reporting' class")
    ap.add_argument("--political-only", action="store_true")
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--max-length", type=int, default=256)
    ap.add_argument("--limit", type=int, default=None, help="score only N articles (debug)")
    args = ap.parse_args()

    arts = _read_articles(Path(args.mind_dir) / "news.tsv")
    if args.political_only:
        arts = [a for a in arts if _is_political(a[1], a[2], DEFAULT_POLITICAL_TERMS)]
    if args.limit:
        arts = arts[: args.limit]
    nids = [a[0] for a in arts]
    texts = [_text(a[2], a[3]) for a in arts]
    labels = [s.strip() for s in args.labels.split(",")]
    print(f"zero-shot {len(texts)} articles with {args.model}  labels={labels}")

    import torch                                          # lazy: only needed here
    from transformers import pipeline

    device = 0 if torch.cuda.is_available() else -1
    clf = pipeline("zero-shot-classification", model=args.model, device=device)

    rep = np.full(len(texts), np.nan)
    for s in range(0, len(texts), args.batch_size):
        batch = texts[s: s + args.batch_size]
        res = clf(batch, candidate_labels=labels, multi_label=False,
                  truncation=True, max_length=args.max_length)
        if isinstance(res, dict):
            res = [res]
        for j, r in enumerate(res):
            rep[s + j] = float(dict(zip(r["labels"], r["scores"])).get(labels[0], np.nan))
        if s % (args.batch_size * 10) == 0:
            print(f"  {s + len(batch)}/{len(texts)}")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("news_id,reporting\n")
        for nid, r in zip(nids, rep):
            f.write(f"{nid},{r:.4f}\n")
    print(f"wrote {args.out}  (mean reporting = {np.nanmean(rep):+.2f})")

    order = np.argsort(rep)
    print("\nMost OPINION-like headlines:")
    for i in order[:8]:
        print(f"  {rep[i]:.2f}  {arts[i][2]}")
    print("Most REPORTING-like headlines:")
    for i in order[-8:][::-1]:
        print(f"  {rep[i]:.2f}  {arts[i][2]}")


if __name__ == "__main__":
    main()
