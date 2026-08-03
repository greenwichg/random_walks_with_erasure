"""The Story Continuation audit — examples/audit_continuation.py.

Phase 1 ships dark, so this audit is the ONLY way its resolver gets verified against real production
data. That makes the audit itself load-bearing, and two properties have to hold:

  * every gate label is reachable — a bucket that can never be attributed would silently fold its
    population into a neighbouring one and misdirect the "where do we invest next" decision;
  * attribution never disagrees with ``story_continuation.resolve`` — the audit re-walks the gates
    to name WHICH one fired, and a re-walk that has drifted from the module reports a comfortable
    number that is not true. The script self-checks this; these tests prove the self-check works.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import audit_continuation as ac       # noqa: E402
import evidence_resolver as er        # noqa: E402
import store as store_mod             # noqa: E402

from test_story_continuation import ANCHOR, FAR, NEAR, _index, _member, _story   # noqa: E402


@pytest.fixture()
def st(tmp_path):
    return store_mod.Store(f"sqlite:///{tmp_path / 'audit.db'}")


@pytest.fixture()
def uid(st):
    return st.upsert_user_by_identity("dev", "audit-reader").id


def _read(st, uid, url, publisher):
    st.add_read(uid, er._canon(url),
                {"article_id": er._canon(url), "outlet": publisher, "publisher": publisher,
                 "category": "Politics", "lean": 0.0, "political": True, "title": "t"})


#: One constructed story per gate, so every bucket is exercised by a case that ONLY trips that gate.
def _cases():
    ok = _story([_member(ANCHOR, "CNN", -0.6), _member(NEAR, "The Wall Street Journal", 0.6)])
    return [
        ("not_in_any_story", _story([_member(NEAR, "The Wall Street Journal", 0.6)]), False),
        ("cluster_untrusted", _story([_member(ANCHOR, "CNN", -0.6),
                                      _member(NEAR, "The Wall Street Journal", 0.6)],
                                     trust="low"), False),
        ("template_genre", _story([
            _member(ANCHOR, "CNN", -0.6, headline="Powerball winning numbers for Saturday"),
            _member(NEAR, "The Wall Street Journal", 0.6,
                    headline="Mega Millions jackpot winning numbers")]), False),
        ("anchor_unrated", _story([_member(ANCHOR, "CNN", None),
                                   _member(NEAR, "The Wall Street Journal", 0.6)]), False),
        ("no_unread_other_outlet", _story([_member(ANCHOR, "CNN", -0.6)]), False),
        ("no_rated_sibling", _story([_member(ANCHOR, "CNN", -0.6),
                                     _member(NEAR, "The Wall Street Journal", None)]), False),
        ("no_opposing_sibling", _story([_member(ANCHOR, "CNN", -0.6),
                                        _member(NEAR, "MSNBC", -1.2)]), False),
        ("ELIGIBLE", ok, True),
    ]


@pytest.mark.parametrize("expected,story,eligible", _cases(), ids=[c[0] for c in _cases()])
def test_every_gate_label_is_reachable(st, uid, expected, story, eligible):
    assert ac._attribute(st, uid, ANCHOR, _index(story)) == expected


@pytest.mark.parametrize("expected,story,eligible", _cases(), ids=[c[0] for c in _cases()])
def test_attribution_agrees_with_the_resolver(st, uid, expected, story, eligible):
    """The audit's verdict must be the module's verdict — the whole point of the self-check."""
    counter, drift, _ = ac._run(st, _index(story), [(uid, ANCHOR)], 50, 5)
    assert not drift, drift
    assert counter[expected] == 1
    assert bool(counter.get("ELIGIBLE")) is eligible


def test_a_syndicated_reprint_is_not_a_second_outlet(st, uid):
    """The audit collapses outlet identity the same way the resolver does; without that it would
    over-report the eligible rate on exactly the clusters syndication dominates."""
    story = _story([_member(ANCHOR, "Sportskeeda", -0.6), _member(NEAR, "Sportskeeda.Com", 0.6)])
    assert ac._attribute(st, uid, ANCHOR, _index(story)) == "no_unread_other_outlet"
    _, drift, _ = ac._run(st, _index(story), [(uid, ANCHOR)], 50, 5)
    assert not drift


def test_an_already_read_sibling_is_attributed_not_counted(st, uid):
    story = _story([_member(ANCHOR, "CNN", -0.6), _member(NEAR, "The Wall Street Journal", 0.6)])
    _read(st, uid, NEAR, "The Wall Street Journal")
    assert ac._attribute(st, uid, ANCHOR, _index(story)) == "no_unread_other_outlet"


def test_the_drift_self_check_actually_fires(st, uid, monkeypatch):
    """Prove the guard is live: if resolve and attribution ever disagree, the audit must SAY so
    rather than print a number nobody can trust."""
    story = _story([_member(ANCHOR, "CNN", -0.6), _member(NEAR, "The Wall Street Journal", 0.6)])
    monkeypatch.setattr(ac.sc, "resolve", lambda *a, **k: None)
    _, drift, _ = ac._run(st, _index(story), [(uid, ANCHOR)], 50, 5)
    assert drift, "attribution said ELIGIBLE while resolve said None — that must be reported"


def test_ceiling_mode_ignores_reader_state(st, uid):
    """With no reader there is nothing read and nothing stale, so the ceiling counts only the
    structural gates — which is exactly what makes it an upper bound, not a prediction."""
    story = _story([_member(ANCHOR, "CNN", -0.6), _member(NEAR, "The Wall Street Journal", 0.6)])
    _read(st, uid, NEAR, "The Wall Street Journal")
    assert ac._attribute(st, uid, ANCHOR, _index(story)) == "no_unread_other_outlet"
    assert ac._attribute(st, None, ANCHOR, _index(story)) == "ELIGIBLE"


def test_gate_list_matches_what_attribution_can_return(st, uid):
    """The printed table iterates GATES; a label attribution can return but GATES omits would be
    counted and never shown."""
    returned = {ac._attribute(st, uid, ANCHOR, _index(s)) for _n, s, _e in _cases()}
    assert returned <= set(ac.GATES)
    assert returned == set(ac.GATES) - {"stale_read"}      # stale_read needs a clock, tested below


def test_stale_read_is_attributed(st, uid):
    from datetime import datetime, timedelta, timezone
    story = _story([_member(ANCHOR, "CNN", -0.6), _member(NEAR, "The Wall Street Journal", 0.6)])
    _read(st, uid, ANCHOR, "CNN")
    idx, now = _index(story), datetime.now(timezone.utc)
    assert ac._attribute(st, uid, ANCHOR, idx, now=now + timedelta(hours=3)) == "ELIGIBLE"
    assert ac._attribute(st, uid, ANCHOR, idx, now=now + timedelta(hours=5)) == "stale_read"


def test_empty_index_exits_without_auditing(tmp_path, monkeypatch, capsys):
    """A cold cache must say so, not report a 0% eligible rate that reads as a product finding."""
    monkeypatch.setattr(sys, "argv", ["audit_continuation.py", "--db",
                                      f"sqlite:///{tmp_path / 'empty.db'}"])
    assert ac.main() == 2
    assert "story index is EMPTY" in capsys.readouterr().out


def test_end_to_end_over_a_real_store(tmp_path, monkeypatch, capsys):
    """The whole script, against a real clustered store and the REAL story index — the path the
    production run takes."""
    monkeypatch.setenv("RWE_FEED_MIN_ARTICLES", "2")
    db = f"sqlite:///{tmp_path / 'e2e.db'}"
    st = store_mod.Store(db)
    title = "Landmark ruling reshapes the harbor bridge oversight case"
    for url, pub, lean, extra in ((ANCHOR, "CNN", -0.9, ""),
                                  (NEAR, "The Wall Street Journal", 0.6, " today"),
                                  (FAR, "Breitbart", 1.2, " tonight")):
        st.upsert_feed_article(
            canonical_url=er._canon(url), url=url, publisher=pub, source_publisher=pub,
            title=title + extra, description="d", body=None,
            published_at="2026-08-03T09:00:00+00:00", source_feed="f",
            scored={"article_id": er._canon(url), "outlet": pub, "category": "Politics",
                    "lean": lean, "political": True, "title": title + extra})
    uid = st.upsert_user_by_identity("dev", "e2e-reader").id
    _read(st, uid, ANCHOR, "CNN")

    er._INDEX_CACHE.update(key=None, index=None)
    monkeypatch.setattr(sys, "argv", ["audit_continuation.py", "--db", db, "--inline"])
    assert ac.main() == 0                                  # 0 == no drift
    out = capsys.readouterr().out
    assert "REALIZED" in out and "eligible rate" in out
    assert "DISAGREE" not in out
    er._INDEX_CACHE.update(key=None, index=None)
