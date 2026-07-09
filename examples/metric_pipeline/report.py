"""Stage 9 · Validation Report — render a :class:`PipelineResult` as text or JSON.

The text form is the developer-facing artifact: per-metric Expected (production) vs Application
(independent), the pass/fail tally for each layer, data-quality findings, and any drift — everything a
reviewer needs to trust (or distrust) a metric at a glance. The JSON form is the same content for CI.
"""
from __future__ import annotations

import json
import math

from .pipeline import PipelineResult

_SEV = {"error": "ERR ", "warn": "warn", "info": "info"}


def _fmt(x) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        if math.isnan(x):
            return "n/a"
        return f"{x:.6g}"
    return str(x)


def _result_glyph(ok) -> str:
    return "·" if ok is None else ("PASS" if ok else "FAIL")


def _rows_block(rows) -> str:
    if not rows:
        return "    (none)\n"
    mw = min(44, max(6, *(len(str(r["metric"])) for r in rows)))
    rw = min(20, max(6, *(len(str(r["reader"])) for r in rows)))
    out = [f"    {'metric':<{mw}}  {'reader':<{rw}}  {'expected':>12}  {'application':>12}  "
           f"{'Δ':>10}   result"]
    for r in rows:
        out.append(f"    {str(r['metric']):<{mw}}  {str(r['reader']):<{rw}}  {_fmt(r['expected']):>12}  "
                   f"{_fmt(r['application']):>12}  {_fmt(r.get('delta')):>10}   {_result_glyph(r['pass'])}")
    return "\n".join(out) + "\n"


def render_text(result: PipelineResult) -> str:
    r = result
    L = [f"METRIC VALIDATION PIPELINE — {r.dataset}",
         "=" * 60,
         f"population: {r.population} reader(s)   ·   catalog C = {r.catalog_categories}   ·   "
         f"result: {'PASS' if r.passed else 'FAIL'}",
         ""]

    L.append("DATA QUALITY")
    for label, q in r.quality.items():
        issues = q["issues"]
        head = "OK" if q["ok"] else "STRUCTURAL ERROR"
        L.append(f"  {label}: {head} ({len(issues)} finding(s), {q['nReads']} reads)")
        for i in issues:
            L.append(f"    [{_SEV.get(i['severity'], i['severity'])}] {i['rule']}: {i['message']}"
                     + (f" ×{i['count']}" if i['count'] else ""))
    L.append("")

    L.append(f"RAW LAYER — independent vs health_report.compute (corpus-driven)   "
             f"PASS {r.raw_summary['passed']}/{r.raw_summary['total']}")
    L.append(_rows_block(r.raw_rows))

    if r.population >= 2:
        L.append(f"DISPLAYED LAYER — percentile: health_report.percentiles vs independent rankdata   "
                 f"PASS {r.displayed_summary['passed']}/{r.displayed_summary['total']}")
        L.append(_rows_block(r.displayed_rows))
    else:
        L.append("DISPLAYED LAYER — n/a (a lone reader percentile-ranks to 50 by convention; "
                 "run --golden all for the pinned population)\n")

    L.append(f"HELPER PARITY — study_metrics.verify_against_production   "
             f"PASS {r.helper_summary['passed']}/{r.helper_summary['total']}")
    L.append(_rows_block(r.helper_rows))

    d = r.drift
    L.append("DRIFT")
    if d.get("baseline"):
        L.append(f"  baseline — {d.get('note', '')}")
    elif not d.get("drifted"):
        L.append(f"  no drift vs previous run ({d.get('previousTimestamp', '?')})")
    else:
        L.append(f"  DRIFTED vs {d.get('previousTimestamp', '?')}:")
        for row in d["rows"]:
            L.append(f"    {row['reader']} · {row['metric']}: {_fmt(row['previous'])} → "
                     f"{_fmt(row['current'])} (Δ {_fmt(row.get('delta'))})")
    L.append("")
    L.append(f"RESULT: {'PASS' if r.passed else 'FAIL'}")
    return "\n".join(L)


def render_json(result: PipelineResult) -> str:
    return json.dumps(result.as_dict(), indent=2, default=str)
