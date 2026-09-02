"""Stage 0.2 — time decay inside the pairwise gate (``clustering.required_sim`` +
``story_service.time_decay``).

The hard six-day window is one number for every pair: it admits a six-day-old instance of a
recurring series on the same evidence as a same-hour paraphrase. The decay grades the
requirement inside the window — ``sim + decay * gap_days`` — so a pair far apart in time must
carry more than the template. Off (0.0) is byte-identical, which is the property every
production measurement of the candidate rests on, so it is pinned first and at every seam the
rule is threaded through: admission, quorum cross-pairs, the repair re-cluster, the environment.
"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "examples"))

import audit_clustering_change as acc   # noqa: E402
import clustering as cl                 # noqa: E402
import evidence_resolver as er          # noqa: E402
import store as store_mod               # noqa: E402
import story_service as ss              # noqa: E402

T0 = datetime(2026, 9, 1, 9, 0, 0, tzinfo=timezone.utc)


def test_required_sim_arithmetic_and_fail_open():
    day3 = T0 + timedelta(days=3)
    assert cl.required_sim(0.28, 0.02, T0, day3) == pytest.approx(0.34)
    assert cl.required_sim(0.28, 0.02, day3, T0) == pytest.approx(0.34), "symmetric"
    assert cl.required_sim(0.28, 0.0, T0, day3) == 0.28, "off returns sim untouched"
    assert cl.required_sim(0.28, 0.02, None, day3) == 0.28, "a missing date is never distance"
    assert cl.required_sim(0.28, 0.02, T0, T0) == 0.28


# Token sets at Jaccard 0.333: three shared of nine in the union. Every word survives
# `title_tokens` (length > 2, no stop-words, no digits) so the build-level tests below score the
# same sets the primitive tests do.
_X = frozenset("senate budget vote alpha gamma delta".split())
_Y = frozenset("senate budget vote sigma omega kappa".split())
_Z = frozenset("senate budget vote lambda theta rho".split())
assert len(_X & _Y) == 3 and len(_X | _Y) == 9


def test_pair_admits_is_byte_identical_at_zero_and_stricter_with_the_gap():
    far = T0 + timedelta(days=3)
    assert cl.pair_admits(_X, _Y, T0, far) is True
    assert cl.pair_admits(_X, _Y, T0, far, time_decay=0.0) is True, "0.0 is the shipped rule"
    assert cl.pair_admits(_X, _Y, T0, T0 + timedelta(hours=1), time_decay=0.02) is True, \
        "an hour apart needs (almost) nothing extra"
    assert cl.pair_admits(_X, _Y, T0, far, time_decay=0.02) is False, \
        "three days apart needs 0.34 and the pair carries 0.333"
    assert cl.pair_admits(_X, _Y, None, far, time_decay=0.02) is True, "undated fails open"


def _items():
    """A and B an hour apart, C four days later, every pair at Jaccard 0.333 — the shape of a
    late template instance riding into a same-day pair."""
    return [{"t": _X, "when": T0}, {"t": _Y, "when": T0 + timedelta(hours=1)},
            {"t": _Z, "when": T0 + timedelta(days=4)}]


def _groups(items, **kw):
    g = cl.cluster(items, tokens=lambda x: x["t"], time=lambda x: x["when"], **kw)
    return sorted(sorted(grp) for grp in g)


def test_cluster_separates_the_late_instance_only_when_the_decay_is_on():
    items = _items()
    assert _groups(items) == [[0, 1, 2]]
    assert _groups(items, time_decay=0.0) == [[0, 1, 2]], "byte-identical off state"
    assert _groups(items, time_decay=0.02) == [[0, 1], [2]]


def test_quorum_cross_pairs_consult_the_same_decayed_rule():
    """The quorum scores cross-pairs by exactly the rule that admitted the original pair; a weaker
    bar there would let a far pair count as support for a merge it could not make alone."""
    items = _items()
    assert _groups(items, link_quorum=0.9, time_decay=0.02) == [[0, 1], [2]]
    assert _groups(items, link_quorum=0.9) == [[0, 1, 2]]


# --------------------------------------------------------------------------- #
# Through story_service: the env knob, the build, the repair seam, the audit flag.
# --------------------------------------------------------------------------- #
def _row(url, words, hours, publisher):
    return {"canonicalUrl": url, "url": url, "title": " ".join(sorted(words)), "description": "",
            "publisher": publisher, "publishedAt": (T0 + timedelta(hours=hours)).isoformat(),
            "scored": {}}


def _rows():
    return [_row("https://a.example/1", _X, 0, "P1"), _row("https://b.example/2", _Y, 1, "P2"),
            _row("https://c.example/3", _Z, 96, "P3")]


def test_the_env_knob_resolves_and_junk_is_off(monkeypatch):
    monkeypatch.delenv("RWE_CLUSTER_TIME_DECAY", raising=False)
    assert ss.time_decay() == 0.0
    monkeypatch.setenv("RWE_CLUSTER_TIME_DECAY", "0.02")
    assert ss.time_decay() == pytest.approx(0.02)
    monkeypatch.setenv("RWE_CLUSTER_TIME_DECAY", "-1")
    assert ss.time_decay() == 0.0, "a negative decay would LOOSEN the gate; clamped to off"
    monkeypatch.setenv("RWE_CLUSTER_TIME_DECAY", "garbage")
    assert ss.time_decay() == 0.0


def test_the_build_threads_the_decay_from_the_environment(monkeypatch):
    monkeypatch.delenv("RWE_CLUSTER_TIME_DECAY", raising=False)
    assert [s["totalCoverage"] for s in ss.build_stories(_rows(), merge=0.0)] == [3]
    monkeypatch.setenv("RWE_CLUSTER_TIME_DECAY", "0.02")
    assert [s["totalCoverage"] for s in ss.build_stories(_rows(), merge=0.0)] == [2], \
        "the four-day-late article no longer clears the graded requirement"
    assert [s["totalCoverage"] for s in ss.build_stories(_rows(), merge=0.0, decay=0.0)] == [3], \
        "an explicit 0.0 overrides the environment — the audit's BEFORE side"


def test_the_repair_re_cluster_receives_the_same_decay(monkeypatch):
    """Pinned at the seam: a repair judging gaps by a different rule than the build would
    re-split on the passes' disagreement."""
    seen = {}
    real = cl.cluster

    def spy(items, **kw):
        seen["decay"] = kw.get("time_decay")
        return real(items, **kw)
    monkeypatch.setattr(ss.clustering, "cluster", spy)
    members = [{"id": "a", "url": "a", "headline": " ".join(sorted(_X)), "description": "",
                "publisher": "P1", "publishedAt": T0.isoformat()},
               {"id": "b", "url": "b", "headline": " ".join(sorted(_Y)), "description": "",
                "publisher": "P2", "publishedAt": T0.isoformat()}]
    ss._repair(members, quorum=0.5, sim=0.28, window_days=6.0, min_shared=3, min_tokens=3,
               idf=False, min_articles=1, min_publishers=1, decay=0.02)
    assert seen["decay"] == 0.02


def test_the_audit_flag_is_after_side_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RWE_CLUSTER_LINK_QUORUM", "0")
    monkeypatch.delenv("RWE_CLUSTER_TIME_DECAY", raising=False)
    st = store_mod.Store(f"sqlite:///{tmp_path / 'decay.db'}")
    for r in _rows():
        st.upsert_feed_article(
            canonical_url=er._canon(r["url"]), url=r["url"], publisher=r["publisher"],
            source_publisher=r["publisher"], title=r["title"], description="",
            body=None, published_at=r["publishedAt"], source_feed="f",
            scored={"article_id": er._canon(r["url"]), "outlet": r["publisher"],
                    "category": "Politics", "lean": 0.0, "title": r["title"]})
    monkeypatch.setenv("RWE_STORIES_SCAN_DAYS", "100000")
    rc = acc.main(["--db", f"sqlite:///{tmp_path / 'decay.db'}", "--time-decay", "0.02"])
    out = capsys.readouterr().out
    assert rc == 0
    before = next(l for l in out.splitlines() if l.startswith("before"))
    after = next(l for l in out.splitlines() if l.startswith("after"))
    assert "decay 0.02" in after and "decay" not in before
    assert "articles in a story: 3 -> 2" in out, \
        "the late instance leaves the story; the split bars (not the merge bars) judge it"
