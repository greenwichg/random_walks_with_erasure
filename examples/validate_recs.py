#!/usr/bin/env python3
"""Repo-root-friendly launcher for the Recommendation Validation Pipeline — Phase 1 (a dev tool).

Run from anywhere:

    python examples/validate_recs.py                     # all scenarios, PASS/FAIL report
    python examples/validate_recs.py --scenario same_story
    python examples/validate_recs.py --report json
    python examples/validate_recs.py --fast              # skip the rebuild-based checks

Proves every recommendation is justified by real reading-history evidence, that explanations are
truthful and never invent facts, that the recommender is deterministic, and that the feed changes
when the history changes. Reuses the production graph + ranking (Phase 1); independent RWE score
recomputation is Phase 2 (21d.2). Adds nothing to production — it only wires the CLI.
"""
import sys

from rec_pipeline.cli import main

if __name__ == "__main__":
    sys.exit(main())
