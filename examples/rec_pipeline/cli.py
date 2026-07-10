"""CLI for the Recommendation Validation Pipeline (Phase 1).

    python examples/validate_recs.py                     # all scenarios, text report
    python examples/validate_recs.py --scenario same_story
    python examples/validate_recs.py --report json
    python examples/validate_recs.py --fast              # skip the rebuild-based checks
    python examples/validate_recs.py --record            # append to the isolated run history
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
    ap.add_argument("--report", choices=["text", "json"], default="text")
    ap.add_argument("--fast", action="store_true",
                    help="skip the rebuild-based checks (pipeline determinism + history-sensitivity)")
    ap.add_argument("--record", action="store_true", help="append a summary to the run history")
    ap.add_argument("--history", type=int, metavar="N",
                    help="print the last N recorded runs and exit")
    args = ap.parse_args(argv)

    if args.history is not None:
        for row in history.tail(args.history):
            print(row)
        return 0

    run = pipeline.run_all(args.scenario, deep=not args.fast)
    print(report.to_json(run) if args.report == "json" else report.to_text(run))
    if args.record:
        history.record(run)
    return 0 if run["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
