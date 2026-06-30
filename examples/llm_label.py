"""Label MIND headlines for political lean with an LLM — as a SECOND MODEL, for
**convergent validity**, NOT as a human gold set.

Why this is not a gold set (read this): the text-lean axis we are validating is itself
a language model (politicalBiasBERT). An LLM labeling agrees with it partly because
both are language models keying on the same lexical cues — shared training-data bias,
not independent human ground truth. So the agreement number this enables is
**convergent validity (model-vs-model)**, weaker than a human gold set, and it must be
reported as such. The real trustworthy number still needs ≥2 human raters
(`validate_lean.py --raters …`). This tool is the cheap, auditable *second opinion*.

It labels from the **headline text only** (blind to the classifier's score) on a -1 / 0
/ +1 scale (left / centre-or-non-political / right), with a one-line reason per item so
every label is auditable. Output is a `news_id,position,reason` CSV that
`validate_lean.py --against` and `ingest_mind.py --positions-csv` read directly.

Usage (needs ANTHROPIC_API_KEY set)::

    python examples/validate_lean.py --lean lean.csv --news-dir MINDsmall_train \\
        --sample 120 --out label_template.tsv          # blind, stratified template
    python examples/llm_label.py --template label_template.tsv --out llm_labels.csv
    python examples/validate_lean.py --lean lean.csv --against llm_labels.csv  # convergent validity
"""

from __future__ import annotations

import argparse
import csv
import json

# the LLM's classification schema — structured outputs guarantee valid JSON back
_SCHEMA = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "lean": {"type": "integer", "enum": [-1, 0, 1]},
                    "reason": {"type": "string"},
                },
                "required": ["id", "lean", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["labels"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You are a careful political-science annotator. You label US news *headlines* for "
    "partisan lean from the headline text ALONE — no outside knowledge of the outlet. "
    "Use -1 = leans left/Democratic, +1 = leans right/Republican, 0 = centre, balanced, "
    "or non-political. Most general-interest or non-political headlines are 0; only mark "
    "-1/+1 when the framing, word choice, or topic selection clearly signals a side. Give "
    "a terse (<= 12 word) reason for each. Label every id you are given, exactly once."
)


def read_template(path: str) -> list:
    """Read a `news_id<TAB/comma>...<TAB/comma>title` file -> [(news_id, title)].
    Ignores any `position` column (we label blind)."""
    rows = []
    with open(path, encoding="utf-8") as f:
        head = f.readline()
        delim = "\t" if head.count("\t") >= head.count(",") else ","
        cols = [c.strip().lower() for c in head.rstrip("\n").split(delim)]
        ci = {c: i for i, c in enumerate(cols)}
        id_i = ci.get("news_id", 0)
        title_i = ci.get("title", len(cols) - 1)
        for line in f:
            parts = line.rstrip("\n").split(delim)
            if len(parts) > max(id_i, title_i) and parts[id_i].strip():
                rows.append((parts[id_i].strip(), parts[title_i].strip()))
    return rows


def parse_labels(text: str) -> dict:
    """Structured-output JSON -> {id: (lean, reason)}."""
    out = {}
    for o in json.loads(text).get("labels", []):
        try:
            out[str(o["id"])] = (int(o["lean"]), str(o.get("reason", "")))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def _format_batch(rows) -> str:
    return "Label each headline. Return one entry per id.\n\n" + "\n".join(
        f"{nid}\t{title}" for nid, title in rows)


def label_headlines(rows, call_fn, batch: int = 20) -> dict:
    """Label every (news_id, title) via ``call_fn(system, user)`` -> response text.
    ``call_fn`` is injected so the API client is swappable (and testable)."""
    labels = {}
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        text = call_fn(_SYSTEM, _format_batch(chunk))
        labels.update(parse_labels(text))
    return labels


def _anthropic_caller(model: str):
    """Return a ``call_fn(system, user)`` backed by the Anthropic SDK."""
    import anthropic
    client = anthropic.Anthropic()

    def call(system: str, user: str) -> str:
        resp = client.messages.create(
            model=model, max_tokens=8000, system=system,
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[{"role": "user", "content": user}],
        )
        return next(b.text for b in resp.content if b.type == "text")

    return call


def write_labels(labels: dict, rows, out: str, model: str) -> int:
    """Write the provenance-stamped `news_id,position,reason` CSV; return count."""
    n = 0
    with open(out, "w", newline="", encoding="utf-8") as f:
        f.write(f"# LLM-annotator labels (convergent validity, NOT human gold) — "
                f"model={model}. See examples/llm_label.py.\n")
        w = csv.writer(f)
        w.writerow(["news_id", "position", "reason"])
        for nid, _title in rows:
            if nid in labels:
                lean, reason = labels[nid]
                w.writerow([nid, lean, reason])
                n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--template", required=True,
                    help="news_id,title file (blind labeling template from validate_lean.py)")
    ap.add_argument("--out", default="llm_labels.csv")
    ap.add_argument("--model", default="claude-opus-4-8",
                    help="Anthropic model id (default claude-opus-4-8)")
    ap.add_argument("--batch", type=int, default=20, help="headlines per API call")
    args = ap.parse_args()

    rows = read_template(args.template)
    if not rows:
        raise SystemExit(f"no (news_id, title) rows read from {args.template}")
    print(f"labeling {len(rows)} headlines with {args.model} (blind, from titles only) ...")
    labels = label_headlines(rows, _anthropic_caller(args.model), batch=args.batch)
    n = write_labels(labels, rows, args.out, args.model)
    miss = len(rows) - n
    print(f"wrote {args.out}: {n} labels"
          + (f"  ({miss} headlines unlabeled — re-run or lower --batch)" if miss else ""))
    print("NOTE: these are an LLM second opinion (convergent validity), NOT a human gold "
          "set. Validate with:\n  python examples/validate_lean.py --lean lean.csv "
          f"--against {args.out}")


if __name__ == "__main__":
    main()
