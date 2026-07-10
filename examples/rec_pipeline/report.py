"""Render a pipeline run as a developer-facing PASS/FAIL report (text or JSON).

The text form groups checks by stage under each scenario, so a failure reads as
``same_story · explanation · target resolves to story_match — FAIL`` — the explanation type names
the fault, not an opaque persona.
"""
from __future__ import annotations

import json

_STAGE_ORDER = ["evidence", "explanation", "determinism", "ranking"]
_STAGE_TITLE = {"evidence": "Stage 2 · Evidence Validation",
                "explanation": "Stage 3 · Explanation Validation",
                "determinism": "Stage 4 · Determinism",
                "ranking": "Stage 5 · Ranking Validation"}


def to_text(run: dict) -> str:
    lines = ["Recommendation Validation Pipeline — Phase 1",
             "=" * 52,
             f"scenarios: {run['fixtures']}    overall: "
             f"{'PASS' if run['passed'] else 'FAIL'}", ""]
    for r in run["results"]:
        head = f"{'PASS' if r['passed'] else 'FAIL'}  {r['name']}"
        meta = f"(served {r['served']}, {'measured' if r['measured'] else 'cold-start'})"
        lines.append(f"{head}   {meta}")
        lines.append(f"      {r['description']}")
        by_stage: dict = {}
        for c in r["checks"]:
            by_stage.setdefault(c["stage"], []).append(c)
        for stage in _STAGE_ORDER:
            for c in by_stage.get(stage, []):
                mark = "  ok " if c["passed"] else " FAIL"
                detail = f"  — {c['detail']}" if (c["detail"] and not c["passed"]) else ""
                lines.append(f"    [{mark}] {_STAGE_TITLE[stage].split('·')[1].strip()}: "
                             f"{c['check']}{detail}")
        lines.append("")
    failed = [c["check"] for r in run["results"] for c in r["checks"] if not c["passed"]]
    lines.append(f"Summary: {'ALL PASS' if run['passed'] else str(len(failed)) + ' check(s) FAILED'}")
    return "\n".join(lines)


def to_json(run: dict) -> str:
    return json.dumps(run, indent=1)
