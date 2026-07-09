"""The orchestrator — run all ten stages over one dataset and assemble a :class:`PipelineResult`.

A *dataset* is a named population of one or more readers, each reader being a list of raw read rows.
The single golden persona ``balanced`` is a population of one (raw layer only); ``all`` is the pinned
six-persona population (raw + displayed). The order matters: Production Collection runs first so its
catalog-category count pins the Topic-Diversity denominator that the Independent Engine then uses, so
the two are compared on identical inputs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import compare, drift, history
from .engine import RAW_METRIC_KEYS, independent_metrics
from .normalize import normalize
from .production import production_metrics
from .quality import check_quality


@dataclass
class PipelineResult:
    dataset: str
    reader_labels: List[str]
    population: int
    catalog_categories: int
    quality: Dict[str, dict]
    independent_raw: Dict[str, dict]                       # label -> {metric: raw}
    raw_rows: List[dict]
    displayed_rows: List[dict]
    helper_rows: List[dict]
    raw_summary: dict
    displayed_summary: dict
    helper_summary: dict
    drift: dict
    passed: bool
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "dataset": self.dataset, "population": self.population,
            "catalogCategories": self.catalog_categories, "readers": self.reader_labels,
            "passed": self.passed, "quality": self.quality,
            "independentRaw": self.independent_raw,
            "comparison": {"raw": self.raw_rows, "displayed": self.displayed_rows,
                           "helperParity": self.helper_rows},
            "summary": {"raw": self.raw_summary, "displayed": self.displayed_summary,
                        "helperParity": self.helper_summary},
            "drift": self.drift, "meta": self.meta,
        }


def run_pipeline(dataset: str, readers: List[Tuple[str, List[dict]]], *, tol: float = 1e-9,
                 drift_threshold: float = 1e-9, record: bool = False,
                 history_file=history.DEFAULT_HISTORY_FILE, meta: Optional[dict] = None) -> PipelineResult:
    """Run Extract→…→Trend-History over one named dataset and return the assembled result.

    ``readers`` is a list of ``(label, raw_read_rows)`` — already Extracted (Stage 1). This function
    performs Stages 2–10. It computes and compares but writes nothing outside the isolated history
    file, and only when ``record=True``."""
    labels = [lbl for lbl, _ in readers]

    # Stage 2 (Normalize) + Stage 3 (Data Quality), per reader.
    normalized: List[List[dict]] = []
    quality: Dict[str, dict] = {}
    for label, rows in readers:
        norm = normalize(rows)
        normalized.append(norm)
        quality[label] = check_quality(norm).as_dict()

    if sum(len(n) for n in normalized) == 0:
        raise ValueError("no reads in any reader — nothing to validate")

    # Stage 6 (Production Collection) first, so its catalog-category count pins the shared C the
    # Independent Engine (Stages 4/5) then normalises Topic Diversity by — identical inputs both sides.
    prod = production_metrics(normalized)
    C = prod["catalog_categories"]

    # Stages 4/5 (Feature Engineering + Independent Metric Engine), per reader, on the shared catalog C.
    independent = [independent_metrics(norm, catalog_categories=C) for norm in normalized]
    independent_raw = {labels[r]: independent[r]["raw"] for r in range(len(labels))}

    # Stage 7 (Comparison): raw, displayed (population >= 2 only), supplementary helper-parity.
    raw_rows = compare.compare_raw([i["raw"] for i in independent], prod["raw"],
                                   tol=tol, reader_labels=labels)
    displayed_rows = compare.compare_displayed([i["raw"] for i in independent], prod["displayed"],
                                               tol=tol, reader_labels=labels)
    helper_rows = compare.helper_parity(normalized, tol=tol, reader_labels=labels)
    raw_summary = compare.summarize(raw_rows)
    displayed_summary = compare.summarize(displayed_rows)
    helper_summary = compare.summarize(helper_rows)

    # Stage 8 (Drift) vs the previous recorded run for this dataset.
    prev = history.last_run(dataset, history_file)
    drift_report = drift.compute_drift(independent_raw, prev, threshold=drift_threshold)

    quality_ok = all(q["ok"] for q in quality.values())
    passed = bool(quality_ok and raw_summary["allPass"] and displayed_summary["allPass"]
                  and helper_summary["allPass"] and not drift_report["drifted"])

    result = PipelineResult(
        dataset=dataset, reader_labels=labels, population=len(labels), catalog_categories=C,
        quality=quality, independent_raw=independent_raw, raw_rows=raw_rows,
        displayed_rows=displayed_rows, helper_rows=helper_rows, raw_summary=raw_summary,
        displayed_summary=displayed_summary, helper_summary=helper_summary, drift=drift_report,
        passed=passed, meta=meta or {})

    # Stage 10 (Trend History) — opt-in, isolated file only.
    if record:
        history.append_run({
            "timestamp": history.now_iso(), "dataset": dataset, "population": len(labels),
            "metrics": independent_raw,
            "summary": {"raw": raw_summary, "displayed": displayed_summary,
                        "helperParity": helper_summary},
            "passed": passed,
        }, history_file)

    return result
