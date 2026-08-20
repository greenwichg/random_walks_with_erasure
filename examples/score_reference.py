"""The frozen reference cohort — what an Information Health score is ranked against.

Every metric in the report is a percentile: "you are more topic-diverse than N% of readers". The
population supplying that N used to be whoever was in the corpus at the moment of the request —
and ``corpus_refresh`` rebuilds the corpus from the LIVE catalog on every cycle, so the cohort
changed whenever ingest ran. A reader who read nothing still moved: measured at 6 points on Source
Diversity, 4 on Viewpoint Balance, 2 on the overall, with the dashboard attributing the change to
the reader ("+2 this month").

The catalog must keep moving — it is what we recommend from. The *benchmark* must not. This module
owns that separation: the reference is captured once, written to the data directory beside the
database, and reused by every later build. Refreshes no longer touch it; only a deliberate
re-version does.

**Captured from the corpus that is live when it is first written**, so switching this on costs no
visible jump — today's scores stay today's scores and simply stop drifting.

The file is plain JSON on purpose: a benchmark that decides what every reader is told about
themselves should be readable, diffable, and reviewable by a person, not an opaque blob.

    RWE_SCORE_REFERENCE   path override (default: <db dir>/score_reference.json)
    RWE_SCORE_REFERENCE_DISABLE=1   ignore any stored reference (pre-fix behaviour)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone

log = logging.getLogger(__name__)

#: Bumped only when the CAPTURE RULE changes (which metrics, how they are normalised) — not when a
#: reference is re-captured. A reader comparing scores across a version bump is comparing two
#: different questions, so the version travels with the file.
SCHEMA_VERSION = 1


def path() -> str:
    """Where the reference lives: beside the database, so it survives redeploys on the same
    bind-mounted volume that already holds user data."""
    override = (os.environ.get("RWE_SCORE_REFERENCE") or "").strip()
    if override:
        return override
    db = (os.environ.get("RWE_DB_URL") or "").strip()
    data_dir = "/app/data"
    if db.startswith("sqlite:///"):
        data_dir = os.path.dirname(db[len("sqlite:///"):]) or "."
    elif not os.path.isdir(data_dir):
        data_dir = "."
    return os.path.join(data_dir, "score_reference.json")


def disabled() -> bool:
    return (os.environ.get("RWE_SCORE_REFERENCE_DISABLE") or "").strip().lower() in ("1", "true", "yes")


def load() -> "dict | None":
    """The stored reference cohort, or ``None`` if there is not a usable one.

    Never raises. A missing, unreadable, or malformed file means "no reference", which degrades to
    the old population-relative behaviour — a corrupt benchmark must not take the report down."""
    if disabled():
        return None
    p = path()
    try:
        with open(p, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return None
    except Exception as exc:                      # noqa: BLE001 — any parse failure is "no reference"
        log.warning(json.dumps({"event": "score_reference_unreadable", "path": p,
                                "error": str(exc),
                                "effect": "scores fall back to the live corpus population"}))
        return None
    if int(doc.get("schemaVersion") or 0) != SCHEMA_VERSION:
        log.warning(json.dumps({"event": "score_reference_version_mismatch", "path": p,
                                "found": doc.get("schemaVersion"), "expected": SCHEMA_VERSION,
                                "effect": "ignored; capture rule changed since it was written"}))
        return None
    metrics = doc.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        return None
    return {k: v for k, v in metrics.items() if isinstance(v, list) and v}


def load_doc() -> "dict | None":
    """The whole stored document, provenance included — for callers that need to know WHERE a
    reference came from, not just what is in it."""
    p = path()
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:                             # noqa: BLE001 — same degradation as `load`
        return None


def provenance_warning(doc: "dict | None", profile_name: str) -> "str | None":
    """A reference captured from a DIFFERENT KIND of corpus than the one now serving.

    Capture is first-write-wins and permanent, and any process that builds a Backend can trigger
    it — including an audit script constructing a bare synthetic profile. That would freeze a
    benchmark from the wrong population, and every reader would be measured against it forever
    with nothing to show for it. The profile NAME is the right thing to compare: the corpus is
    supposed to grow (that is the whole point), so sizes drifting is expected and silent, while
    `synthetic` where `qbias` serves is a mistake."""
    if not doc or not profile_name:
        return None
    got = str((doc.get("provenance") or {}).get("profile") or "")
    if got and got != profile_name:
        return (f"the frozen score reference was captured from a '{got}' corpus but a "
                f"'{profile_name}' corpus is serving — every reader is being ranked against the "
                f"wrong population. Re-capture: stop the api, delete {path()}, start it, and load "
                f"a report so the serving process writes a new one.")
    return None


def save(metrics: dict, *, note: str = "", provenance: "dict | None" = None) -> str:
    """Write a reference atomically (temp file + rename), so a crash mid-write can never leave a
    half-written benchmark that every reader is then scored against."""
    p = path()
    doc = {"schemaVersion": SCHEMA_VERSION,
           "capturedAt": datetime.now(timezone.utc).isoformat(),
           "note": note or "captured from the live corpus",
           "provenance": dict(provenance or {}),
           "metrics": {k: list(v) for k, v in metrics.items()}}
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    log.info(json.dumps({"event": "score_reference_captured", "path": p,
                         "metrics": sorted(metrics), "sizes": {k: len(v) for k, v in metrics.items()}}))
    return p


def load_or_capture(freeze, provenance: "dict | None" = None) -> "dict | None":
    """The stored reference, capturing one from the current corpus the first time.

    ``freeze`` is a zero-argument callable returning ``{metric: [values]}`` — passed in rather than
    imported so this module stays free of the engine's import graph.

    Capture is first-write-wins and never overwrites: a later refresh must not silently re-point
    the benchmark, which is the whole defect this module exists to fix. Re-versioning is a
    deliberate act (delete the file, or point RWE_SCORE_REFERENCE at a new one)."""
    if disabled():
        return None
    existing = load()
    if existing:
        return existing
    try:
        metrics = freeze() or {}
    except Exception as exc:                      # noqa: BLE001
        log.warning(json.dumps({"event": "score_reference_capture_failed", "error": str(exc)}))
        return None
    if not metrics:
        return None
    try:
        save(metrics, note="first capture — from the corpus live at the time",
             provenance=provenance)
    except Exception as exc:                      # noqa: BLE001 — a read-only volume must not 500
        log.warning(json.dumps({"event": "score_reference_unwritable", "error": str(exc),
                                "effect": "using the captured values in memory for this process "
                                          "only; scores will drift again after a restart"}))
    return metrics
