"""Blind-spot slice v1 — the report's measured gap topics lift within the RWE-D slice
(Tier 1, docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md; ``RWE_REC_BLINDSPOT``, default off).

The report has always *named* the reader's under-read topics (``health_report.blind_spot_gaps``
→ ``rep["blind_spots"]``) while no candidate source acted on them. This pins the closing of that
loop: topics come only from the same report the reader sees, the lift is a stable bounded nudge
in the discovery slice with the exact construction of the preference rerank, the flag defaults
to inert, and the explain observer applies the identical pass so an enabled flag cannot make the
explained feed drift from the served one.
"""

import pathlib
import sys
from types import SimpleNamespace

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import api_server  # noqa: E402

B = api_server.Backend


def _mind(cats):
    return SimpleNamespace(categories=np.asarray(cats, dtype=object))


def test_flag_off_yields_no_topics_even_when_the_report_names_gaps(monkeypatch):
    monkeypatch.delenv("RWE_REC_BLINDSPOT", raising=False)
    rep = {"blind_spots": [("Science", 0.01, 0.2), ("Health", 0.0, 0.1)]}
    assert B._blindspot_topics(rep) == ()


def test_topics_come_from_the_report_lowercased(monkeypatch):
    monkeypatch.setenv("RWE_REC_BLINDSPOT", "1")
    rep = {"blind_spots": [("Science", 0.01, 0.2), ("Health", 0.0, 0.1)]}
    assert B._blindspot_topics(rep) == ("science", "health")
    assert B._blindspot_topics({}) == ()
    assert B._blindspot_topics(None) == ()
    # A blank topic never spends a boost — mirrors blind_spot_gaps' own naming rule.
    assert B._blindspot_topics({"blind_spots": [("", 0.0, 0.1)]}) == ()


def test_rerank_is_identity_without_topics():
    m = _mind(["A", "B", "C"])
    cols = [0, 1, 2]
    assert B._blindspot_rerank(m, cols, ()) is cols
    assert B._blindspot_rerank(m, [], ("a",)) == []


def test_rerank_lifts_gap_topics_stably_and_boundedly():
    m = _mind(["Sports", "Science", "Sports", "Science", "Sports"])
    out = B._blindspot_rerank(m, [0, 1, 2, 3, 4], ("science",))
    # Keys: sports 1, 3, 5 at their positions; science (i+1)/4 → 0.5 and 1.0. The second science
    # item TIES the head sports item at 1.0 and the stable index tie-break keeps the original
    # order between them — a lift, never a leapfrog on equal footing. Nothing dropped.
    assert out == [1, 0, 3, 2, 4]
    assert B._blindspot_rerank(m, [0, 1, 2, 3, 4], ("science",)) == out   # deterministic
    # Bounded: from ~5x past the head the lift is not enough to overtake position 1.
    deep = _mind(["A"] * 8 + ["Science"])
    lifted = B._blindspot_rerank(deep, list(range(9)), ("science",))
    assert lifted[0] == 0 and lifted.index(8) == 2    # 9/4 = 2.25: past cols 2..8, not col 0/1


def test_serving_passes_the_report_topics_into_the_discovery_slice():
    import ast
    src = (ROOT / "examples" / "api_server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_serialize_recommendations")
    calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
             and isinstance(c.func, ast.Attribute) and c.func.attr == "_rec_cols_of"]
    assert calls and all(any(k.arg == "blindspot" for k in c.keywords) for c in calls), (
        "_serialize_recommendations must hand the report's blind-spot topics to _rec_cols_of")


def test_rec_cols_of_applies_the_lift_to_the_discovery_slice():
    import ast
    src = (ROOT / "examples" / "api_server.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_rec_cols_of")
    calls = {c.func.attr for c in ast.walk(fn) if isinstance(c, ast.Call)
             and isinstance(c.func, ast.Attribute)}
    assert "_blindspot_rerank" in calls, (
        "_rec_cols_of must apply the blind-spot lift — passing the topics in without applying "
        "them leaves the flag on and the feed unchanged, silently.")


def test_explain_applies_the_same_blindspot_pass():
    import ast
    src = (ROOT / "examples" / "rec_explain.py").read_text(encoding="utf-8")
    names = {c.func.attr for c in ast.walk(ast.parse(src))
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
    assert {"_blindspot_topics", "_blindspot_rerank"} <= names, (
        "rec_explain must replicate the blind-spot pass, or an enabled flag makes the "
        "explained feed drift from the served one.")
