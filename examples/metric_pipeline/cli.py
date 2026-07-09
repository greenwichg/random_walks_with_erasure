"""The command-line surface — a developer tool, never exposed on any web/API route (decision D7).

    python examples/validate_metrics.py --golden all
    python examples/validate_metrics.py --golden echo_chamber --report json
    python examples/validate_metrics.py --history my_reads.json
    python examples/validate_metrics.py --user 1                 # live DB via Store()
    python examples/validate_metrics.py --golden all --record    # append to the isolated trend history

Exit code is 0 when the run passes (raw + displayed + helper-parity all match, no drift, no quality
error), 1 otherwise — so it can gate CI.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Tuple

from . import extract, history, report
from .pipeline import run_pipeline


def _golden_population() -> Tuple[str, List[Tuple[str, list]]]:
    pop = extract.extract_golden_population()
    return "all", [(name, rows) for name, rows in pop.items()]


def _build_source(args) -> Tuple[str, List[Tuple[str, list]], dict]:
    """Resolve CLI args to ``(dataset_name, [(reader_label, rows)], meta)`` — Stage 1 (Extract)."""
    if args.golden:
        if args.golden == "all":
            dataset, readers = _golden_population()
            return dataset, readers, {"source": "golden", "persona": "all"}
        rows = extract.extract_golden(args.golden)
        return args.golden, [(args.golden, rows)], {"source": "golden",
                                                    **extract.golden_meta(args.golden)}
    if args.history:
        rows = extract.extract_file(args.history)
        label = os.path.basename(args.history) or args.history
        return f"file:{args.history}", [(label, rows)], {"source": "file", "path": args.history}
    if args.user is not None:
        import store
        rows = extract.extract_user(store.Store(), args.user)
        return f"user:{args.user}", [(f"user_{args.user}", rows)], {"source": "user", "userId": args.user}
    raise SystemExit("choose a source: --golden <name|all>, --history <file>, or --user <id>")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="validate_metrics",
        description="Independently validate the Information-Health metrics against production.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--golden", metavar="NAME",
                     help=f"a golden persona ({', '.join(extract.GOLDEN_NAMES)}) or 'all' for the "
                          "pinned population")
    src.add_argument("--history", metavar="FILE", help="a JSON file of reads to validate")
    src.add_argument("--user", type=int, metavar="ID", help="a live user id (reads via Store())")
    ap.add_argument("--report", choices=["text", "json"], default="text")
    ap.add_argument("--tol", type=float, default=1e-9, help="numeric match tolerance (default 1e-9)")
    ap.add_argument("--drift-threshold", type=float, default=1e-9,
                    help="flag a metric that moved more than this since the last recorded run")
    ap.add_argument("--record", action="store_true",
                    help="append this run to the isolated trend history (Stage 10)")
    ap.add_argument("--history-file", default=str(history.DEFAULT_HISTORY_FILE),
                    help="where the trend history lives (isolated; git-ignored by default)")
    args = ap.parse_args(argv)

    dataset, readers, meta = _build_source(args)
    try:
        result = run_pipeline(dataset, readers, tol=args.tol, drift_threshold=args.drift_threshold,
                              record=args.record, history_file=args.history_file, meta=meta)
    except ValueError as e:
        print(f"nothing to validate: {e}", file=sys.stderr)
        return 1

    print(report.render_json(result) if args.report == "json" else report.render_text(result))
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
