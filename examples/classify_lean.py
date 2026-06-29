"""Score MIND articles for political lean from TEXT (title + abstract).

Runs a pretrained political-bias text classifier over each article and writes a
``news_id,position`` CSV that ``ingest_mind.py`` consumes via ``--positions-csv``,
giving a **text-grounded left<->right axis** -- no outlet labels, and no co-click
topic confound (the failure mode of the ``--ideology`` path on news). Best on a
GPU (Colab).

    pip install transformers torch
    python examples/classify_lean.py --mind-dir MINDsmall_train --political-only --out lean.csv
    python examples/ingest_mind.py --mind-dir MINDsmall_train --political-only \
        --positions-csv lean.csv --min-user-clicks 10 --min-item-clicks 10 --out mind_text.npz
    python examples/eval_mind.py --npz mind_text.npz --out-csv results_text.csv --no-bprmf

The position is the softmax-expected class position: with
``--label-positions=-1,0,1`` (left, center, right) and ``--scale 2`` it lands in
``[-2, 2]``, matching the outlet-lean scale. **Verify the model's label order** from
the printed ``id2label`` and set ``--label-positions`` to match. Pass it with an
``=`` (``--label-positions=-1,0,1``): a leading-dash value is read as a flag otherwise.
"""

import argparse
from pathlib import Path

import numpy as np

from rwe.mind import DEFAULT_POLITICAL_TERMS, _is_political


def _read_articles(path):
    """``news.tsv`` -> list of ``(news_id, subcategory, title, abstract)``."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 5:
                continue
            rows.append((p[0], p[2], p[3], p[4]))
    return rows


def _text(title, abstract):
    """Classifier input: title, plus the abstract when present."""
    title, abstract = (title or "").strip(), (abstract or "").strip()
    return (title + ". " + abstract).strip(". ") if abstract else title


def _positions_from_probs(probs, label_positions, scale=1.0):
    """Softmax-expected lean per article: ``scale * (probs @ label_positions)``."""
    probs = np.asarray(probs, dtype=float)
    lp = np.asarray(label_positions, dtype=float)
    return scale * (probs @ lp)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mind-dir", required=True, help="directory with news.tsv")
    ap.add_argument("--out", default="lean.csv")
    ap.add_argument("--model", default="bucketresearch/politicalBiasBERT",
                    help="HF text-classification model (Left/Center/Right)")
    ap.add_argument("--label-positions", default="-1,0,1",
                    help="position per logit index, matching the model's label "
                         "order (default left,center,right = -1,0,1). Pass with '=' "
                         "(--label-positions=-1,0,1); a leading-dash value is else "
                         "read as a flag")
    ap.add_argument("--scale", type=float, default=2.0,
                    help="scale the expected position (default 2 -> [-2, 2])")
    ap.add_argument("--political-only", action="store_true",
                    help="score only political articles (faster; matches the pipeline)")
    ap.add_argument("--batch-size", type=int, default=32)
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
    print(f"scoring {len(texts)} articles with {args.model} ...")

    import torch                                          # lazy: only needed here
    from transformers import (AutoModelForSequenceClassification, AutoTokenizer)

    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForSequenceClassification.from_pretrained(args.model).eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    print("id2label:", model.config.id2label, "(check this matches --label-positions)")
    lp = [float(x) for x in args.label_positions.split(",")]
    if len(lp) != model.config.num_labels:
        raise ValueError(f"--label-positions has {len(lp)} values but the model has "
                         f"{model.config.num_labels} labels ({model.config.id2label})")

    pos = np.empty(len(texts), dtype=float)
    for s in range(0, len(texts), args.batch_size):
        batch = texts[s : s + args.batch_size]
        enc = tok(batch, truncation=True, max_length=args.max_length,
                  padding=True, return_tensors="pt").to(device)
        with torch.no_grad():
            probs = torch.softmax(model(**enc).logits, dim=-1).cpu().numpy()
        pos[s : s + len(batch)] = _positions_from_probs(probs, lp, args.scale)
        if s % (args.batch_size * 20) == 0:
            print(f"  {s + len(batch)}/{len(texts)}")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("news_id,position\n")
        for nid, p in zip(nids, pos):
            f.write(f"{nid},{p:.4f}\n")
    print(f"wrote {args.out}  (mean={pos.mean():+.2f}, range=[{pos.min():+.2f}, "
          f"{pos.max():+.2f}])")

    # quick eyeball: the most left- and right-scored headlines
    order = np.argsort(pos)
    print("\nMost LEFT-scored:")
    for i in order[:8]:
        print(f"  {pos[i]:+.2f}  {arts[i][2]}")
    print("Most RIGHT-scored:")
    for i in order[-8:]:
        print(f"  {pos[i]:+.2f}  {arts[i][2]}")


if __name__ == "__main__":
    main()
