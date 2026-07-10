"""CLI for the Recommendation Validation Pipeline (Phase 1).

    python examples/validate_recs.py                     # all scenarios, text report
    python examples/validate_recs.py --scenario same_story
    python examples/validate_recs.py --history my_reads.json   # validate YOUR reading history
    python examples/validate_recs.py --report json
    python examples/validate_recs.py --fast              # skip the rebuild-based checks
    python examples/validate_recs.py --record            # append to the isolated run history
    python examples/validate_recs.py --show-history 10   # last 10 recorded runs, then exit
"""
from __future__ import annotations

import argparse
import sys

from . import pipeline, report, history


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", action="append",
                    help="one scenario name (repeatable); default = all golden scenarios")
    ap.add_argument("--history", metavar="PATH",
                    help="validate YOUR exported reading history instead of the golden scenarios "
                         "(a /api/me/history export, {reads:[...]}, or {scored} rows)")
    ap.add_argument("--report", choices=["text", "json"], default="text")
    ap.add_argument("--fast", action="store_true",
                    help="skip the rebuild-based checks (pipeline determinism + history-sensitivity)")
    ap.add_argument("--record", action="store_true", help="append a summary to the run history")
    ap.add_argument("--show-history", type=int, metavar="N",
                    help="print the last N recorded runs and exit")
    args = ap.parse_args(argv)

    if args.show_history is not None:
        for row in history.tail(args.show_history):
            print(row)
        return 0

    if args.history:
        from . import extract
        reads = extract.load_history(args.history)
        if not reads:
            print(f"No usable reads found in {args.history} "
                  "(expected a /api/me/history export, {reads:[...]}, or {scored} rows).")
            return 2
        result = pipeline.run_fixture(extract.fixture_from_history(reads), deep=not args.fast)
        run = {"passed": result["passed"], "fixtures": 1, "results": [result]}
    else:
        run = pipeline.run_all(args.scenario, deep=not args.fast)

    print(report.to_json(run) if args.report == "json" else report.to_text(run))
    if args.record:
        history.record(run)
    return 0 if run["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
