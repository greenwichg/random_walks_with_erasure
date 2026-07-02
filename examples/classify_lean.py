"""Score MIND articles for political lean from TEXT (title + abstract).

Runs a pretrained political-bias text classifier over each article and writes a
``news_id,position,confidence`` CSV that ``ingest_mind.py`` consumes via
``--positions-csv`` (it reads the position and ignores the extra column), giving a
**text-grounded left<->right axis** -- no outlet labels, and no co-click topic
confound (the failure mode of the ``--ideology`` path on news). Best on a GPU (Colab).

The third column, ``confidence`` in [0, 1], is the **top-2 softmax margin**: how
peaked the L/C/R distribution is. It is low exactly for the ambiguous
centre-vs-side articles the two bias models disagree on (see
``examples/lean_agreement.py``, Cohen's kappa 0.14), so it is the natural
per-article confidence -- ``examples/health_report.py --confidence-csv`` uses it to
*down-weight* those noisy articles when aggregating a reader's viewpoint.

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


def _confidence_from_probs(probs):
    """Top-2 softmax margin per article -> confidence in [0, 1].

    A peaked distribution (one class dominant) -> ~1; a near-tie (e.g. centre vs a
    side) -> ~0. The low-margin articles are precisely the centre-boundary cases the
    two bias models disagree on (``examples/lean_agreement.py``), so this margin is a
    calibrated *per-article* reliability weight, not just a heuristic."""
    probs = np.asarray(probs, dtype=float)
    if probs.ndim == 1:
        probs = probs[None, :]
    top2 = np.sort(probs, axis=1)[:, ::-1][:, :2]
    return top2[:, 0] - top2[:, 1]


def load_classifier(model_name):
    """Load an HF sequence-classification model + tokenizer on GPU if available; returns
    ``(tokenizer, model, device)``. The torch/transformers import is lazy (only here)."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSequenceClassification.from_pretrained(model_name).eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    return tok, model, device


def score_texts(texts, tok, model, device, label_positions, scale=2.0,
                batch_size=32, max_length=256, progress=True):
    """Score texts -> ``(positions, confidences)`` (softmax-expected lean + top-2 margin).
    Shared by this CLI and ``examples/validate_qbias.py`` so both use identical scoring."""
    import torch
    lp = np.asarray([float(x) for x in label_positions], dtype=float)
    pos = np.empty(len(texts), dtype=float)
    conf = np.empty(len(texts), dtype=float)
    for s in range(0, len(texts), batch_size):
        batch = texts[s : s + batch_size]
        enc = tok(batch, truncation=True, max_length=max_length,
                  padding=True, return_tensors="pt").to(device)
        with torch.no_grad():
            probs = torch.softmax(model(**enc).logits, dim=-1).cpu().numpy()
        pos[s : s + len(batch)] = _positions_from_probs(probs, lp, scale)
        conf[s : s + len(batch)] = _confidence_from_probs(probs)
        if progress and s % (batch_size * 20) == 0:
            print(f"  {s + len(batch)}/{len(texts)}")
    return pos, conf


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

    tok, model, device = load_classifier(args.model)
    print("id2label:", model.config.id2label, "(check this matches --label-positions)")
    lp = [float(x) for x in args.label_positions.split(",")]
    if len(lp) != model.config.num_labels:
        raise ValueError(f"--label-positions has {len(lp)} values but the model has "
                         f"{model.config.num_labels} labels ({model.config.id2label})")
    pos, conf = score_texts(texts, tok, model, device, lp, scale=args.scale,
                            batch_size=args.batch_size, max_length=args.max_length)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("news_id,position,confidence\n")
        for nid, p, c in zip(nids, pos, conf):
            f.write(f"{nid},{p:.4f},{c:.4f}\n")
    print(f"wrote {args.out}  (mean={pos.mean():+.2f}, range=[{pos.min():+.2f}, "
          f"{pos.max():+.2f}]; mean confidence {conf.mean():.2f} = top-2 softmax "
          "margin, lower where the lean is ambiguous)")

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
