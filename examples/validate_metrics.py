#!/usr/bin/env python3
"""Repo-root-friendly launcher for the Metric Validation Pipeline (a developer tool).

Run from anywhere:

    python examples/validate_metrics.py --golden all
    python examples/validate_metrics.py --history reads.json --report json

Running this file puts ``examples/`` on ``sys.path`` (Python adds the script's own directory), so the
``metric_pipeline`` package imports cleanly; the package in turn puts the repo root on the path for the
unchanged ``rwe`` / ``health_report`` engine it cross-checks against. This launcher adds nothing to
production — it only wires the CLI.
"""
import sys

from metric_pipeline.cli import main

if __name__ == "__main__":
    sys.exit(main())
