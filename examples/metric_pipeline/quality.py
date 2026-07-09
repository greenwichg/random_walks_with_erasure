"""Stage 3 · Data Quality — rule checks over the normalized reads, with severities.

Each rule inspects the reads and reports how many rows trip it, why it matters, and a sample. The
point is *observability*: a metric that comes out ``n/a`` or looks off should be explainable by a
data-quality finding rather than a mystery. Quality never edits the data; it only annotates it.

Severities
    ERROR  — the input is structurally unusable (no reads): the run still completes but is flagged.
    WARN   — rows will be silently dropped or bucketed by the engine (blank publisher, political
             read with no lean, off-by emotion vector): the metric is still computed, just on less.
    INFO   — an unavoidable, expected ``n/a`` (no political reads → no viewpoint/echo; no emotion
             data → no emotional balance): context, not a problem.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from study_metrics import EMOTION_BUCKETS, _scored

ERROR, WARN, INFO = "error", "warn", "info"
_KNOWN_REGISTERS = {"reporting", "opinion", "mixed"}


@dataclass
class Issue:
    rule: str
    severity: str
    count: int
    message: str
    sample: List[str] = field(default_factory=list)


@dataclass
class QualityReport:
    n_reads: int
    issues: List[Issue]

    @property
    def errors(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == WARN]

    @property
    def ok(self) -> bool:
        """Structurally usable — no ERROR-severity findings (warnings do not block a run)."""
        return not self.errors

    def as_dict(self) -> dict:
        return {"nReads": self.n_reads, "ok": self.ok,
                "issues": [vars(i) for i in self.issues]}


def _title(read: dict) -> str:
    return (_scored(read).get("title") or _scored(read).get("category") or "<untitled>")[:60]


def check_quality(reads: List[dict]) -> QualityReport:
    """Run every data-quality rule over the normalized reads and return a :class:`QualityReport`."""
    issues: List[Issue] = []
    n = len(reads)

    if n == 0:
        issues.append(Issue("empty_history", ERROR, 0,
                            "no reads — every metric is undefined"))
        return QualityReport(n_reads=0, issues=issues)

    # WARN: a read marked political but with no usable lean is dropped from viewpoint/echo.
    pol_no_lean = [r for r in reads if _scored(r).get("political")
                   and not isinstance(_scored(r).get("lean"), (int, float))]
    if pol_no_lean:
        issues.append(Issue("political_without_lean", WARN, len(pol_no_lean),
                            "political reads with no lean are excluded from Viewpoint/Echo",
                            [_title(r) for r in pol_no_lean[:3]]))

    # WARN: blank publisher → dropped from Source Diversity (matches the engine).
    no_pub = [r for r in reads if not (_scored(r).get("outlet") or "").strip()]
    if no_pub:
        issues.append(Issue("missing_publisher", WARN, len(no_pub),
                            "reads with no publisher are excluded from Source Diversity",
                            [_title(r) for r in no_pub[:3]]))

    # WARN: blank category → bucketed as '(uncategorised)' in Topic Diversity.
    no_cat = [r for r in reads if not (_scored(r).get("category") or "").strip()]
    if no_cat:
        issues.append(Issue("missing_category", WARN, len(no_cat),
                            "reads with no category fall into a single '(uncategorised)' bucket",
                            [_title(r) for r in no_cat[:3]]))

    # WARN: an emotion vector that does not sum to ~1 signals a miscomputed share.
    off = []
    for r in reads:
        emo = _scored(r).get("emotion")
        if isinstance(emo, dict) and emo:
            total = sum(float(emo.get(b, 0.0) or 0.0) for b in EMOTION_BUCKETS)
            if abs(total - 1.0) > 0.05:
                off.append((r, total))
    if off:
        issues.append(Issue("emotion_sum_off", WARN, len(off),
                            "emotion shares should sum to ~1.0 across the five buckets",
                            [f"{_title(r)} (Σ={t:.2f})" for r, t in off[:3]]))

    # INFO: an unrecognised register label won't count toward the reporting share.
    unknown_reg = [r for r in reads if (reg := (_scored(r).get("register") or "").strip())
                   and reg not in _KNOWN_REGISTERS]
    if unknown_reg:
        issues.append(Issue("unknown_register", INFO, len(unknown_reg),
                            f"register labels outside {sorted(_KNOWN_REGISTERS)} are ignored by "
                            "Reporting Ratio",
                            [(_scored(r).get('register') or '') for r in unknown_reg[:3]]))

    # INFO: no political reads at all → viewpoint/echo are legitimately n/a.
    if not any(_scored(r).get("political") for r in reads):
        issues.append(Issue("no_political_reads", INFO, 0,
                            "no political reads — Viewpoint Balance / Echo Chamber are n/a"))

    # INFO: no emotion data → emotional balance is legitimately n/a.
    if not any(isinstance(_scored(r).get("emotion"), dict) and _scored(r).get("emotion")
               for r in reads):
        issues.append(Issue("no_emotion_data", INFO, 0,
                            "no emotion vectors — Emotional Balance is n/a"))

    return QualityReport(n_reads=n, issues=issues)
