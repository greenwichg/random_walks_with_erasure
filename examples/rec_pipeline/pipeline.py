"""The orchestrator — run the Phase-1 stages over one or all scenario fixtures.

A *scenario fixture* (``golden/<name>.json``) is a controlled catalog + the reader-under-test's
history + a named ``target`` article, engineered so the target resolves to a specific explanation
type. ``run_fixture`` builds the real offline case (Stage 1) and runs Stages 2–5; ``run_all``
sweeps the golden set. Writes nothing outside the opt-in, isolated run history.
"""
from __future__ import annotations

import copy
import json
import pathlib
from typing import List, Optional

from . import Check
from . import extract, evidence, explanation, determinism, ranking

GOLDEN = pathlib.Path(__file__).resolve().parent / "golden"

#: Distinctive reads appended to probe history-sensitivity (Stage 5) — a real base catalog article
#: from an outlet/topic a fixture reader is unlikely to already have, so the augmented graph shifts.
_PERTURB = [("https://reuters.example.com/base/business-3", "Reuters"),
            ("https://associatedpress.example.com/base/world-3", "Associated Press"),
            ("https://npr.example.com/base/health-3", "NPR")]


def load_fixture(name: str) -> dict:
    return json.loads((GOLDEN / f"{name}.json").read_text())


def fixture_names() -> List[str]:
    return sorted(p.stem for p in GOLDEN.glob("*.json"))


def _perturbed(fixture: dict) -> dict:
    """A copy of the fixture with distinctive reads appended to the reader under test."""
    fx = copy.deepcopy(fixture)
    already = set()
    for reader in fx["readers"]:
        if reader.get("underTest"):
            already = {r["url"] for r in reader["reads"]}
            reader["reads"].extend({"url": u, "publisher": p} for u, p in _PERTURB
                                   if u not in already)
    return fx


def run_fixture(fixture: dict, *, deep: bool = True) -> dict:
    """Stages 1–5 for one scenario. ``deep`` enables the two rebuild-based checks (pipeline
    determinism + history-sensitivity), which cost extra corpus builds; off for a fast smoke."""
    case = extract.build(fixture)
    checks: List[Check] = []
    checks += evidence.run(case)
    checks += explanation.run(case)

    rebuild = (lambda name: extract.build(fixture)) if deep else None
    perturb = (lambda name: extract.build(_perturbed(fixture))) if deep else None
    checks += determinism.run(case, rebuild=rebuild)
    checks += ranking.run(case, rebuild_perturbed=perturb)

    passed = all(c.passed for c in checks)
    return {"name": case.name, "description": case.description, "passed": passed,
            "measured": case.measured, "served": len(case.recs),
            "target": case.target_url, "checks": [c.as_dict() for c in checks]}


def run_all(names: Optional[List[str]] = None, *, deep: bool = True) -> dict:
    names = names or fixture_names()
    results = [run_fixture(load_fixture(n), deep=deep) for n in names]
    return {"passed": all(r["passed"] for r in results),
            "fixtures": len(results), "results": results}
