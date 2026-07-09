"""Stage 10 · Trend History — an isolated, append-only record of past validation runs.

Deliberately its **own** store, never the product's ``ReportSnapshot`` table: a newline-delimited JSON
file (one object per run) that this tool owns entirely, so recording a validation run can never touch,
grow, or corrupt any product data. The default lives beside the package and is git-ignored; the CLI
can point ``--history-file`` anywhere.

Each record captures the run's identity, the independent RAW metric per reader (the deterministic
quantity drift watches), and the pass/fail summary. Stage 8 (Drift) reads the previous record for the
same dataset to see whether a metric moved between runs.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

DEFAULT_HISTORY_FILE = Path(__file__).resolve().parent / ".runs.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_run(record: dict, path: "str | Path" = DEFAULT_HISTORY_FILE) -> Path:
    """Append one run record as a JSON line. Creates the file (and parents) if needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
    return p


def load_runs(path: "str | Path" = DEFAULT_HISTORY_FILE) -> List[dict]:
    """Every recorded run, oldest first (a missing file is an empty history, never an error)."""
    p = Path(path)
    if not p.exists():
        return []
    runs: List[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return runs


def last_run(dataset: str, path: "str | Path" = DEFAULT_HISTORY_FILE) -> Optional[dict]:
    """The most recent recorded run for ``dataset``, or ``None`` if there is no prior run."""
    for run in reversed(load_runs(path)):
        if run.get("dataset") == dataset:
            return run
    return None
