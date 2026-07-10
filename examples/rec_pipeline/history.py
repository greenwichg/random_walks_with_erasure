"""Opt-in, isolated run history — a newline-delimited JSON file the pipeline appends to when asked,
so a reviewer can watch PASS/FAIL counts over time. Deliberately its OWN file (never the product's
tables), gitignored, and never read by anything in production — exactly like the Metric Validation
Pipeline's history.
"""
from __future__ import annotations

import json
import pathlib
import time

RUNS = pathlib.Path(__file__).resolve().parent / ".runs.jsonl"


def record(run: dict) -> dict:
    """Append a one-line summary of a run; returns the recorded row."""
    failed = sum(1 for r in run["results"] for c in r["checks"] if not c["passed"])
    total = sum(len(r["checks"]) for r in run["results"])
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "passed": run["passed"], "fixtures": run["fixtures"],
           "checks": total, "failed": failed,
           "failing": [r["name"] for r in run["results"] if not r["passed"]]}
    with RUNS.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def tail(n: int = 10) -> list:
    if not RUNS.exists():
        return []
    return [json.loads(x) for x in RUNS.read_text().splitlines()[-n:]]
