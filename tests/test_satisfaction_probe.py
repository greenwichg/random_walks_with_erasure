"""Tests for examples/satisfaction_probe.py (measured opposite-side engagement)."""

import bz2
import datetime as dt
import importlib.util
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "satisfaction_probe", ROOT / "examples" / "satisfaction_probe.py")
sp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sp)


def _ts(y, m, d=1):
    return int(dt.datetime(y, m, d, tzinfo=dt.timezone.utc).timestamp())


def _write(path, rows):
    with bz2.open(path, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


SUB_POS = {"socialism": -1.5, "communism": -1.6, "Conservative": 1.5,
           "Republican": 1.4, "news": 0.1}


def test_month_code_maps_to_year_month():
    assert sp._month_code(_ts(2016, 9)) == sp._month_code(_ts(2016, 9, 20))   # same month
    assert sp._month_code(_ts(2016, 9)) != sp._month_code(_ts(2016, 11))      # diff month
    assert sp._month_code(None) == -1 and sp._month_code("garbage") == -1


def _corpus(tmp_path):
    rows = []
    # L1 (left): 5 own-side, 2 cross-cutting into Conservative (upvoted, replies, 2 months)
    rows += [{"author": "L1", "subreddit": s, "score": 5, "created_utc": _ts(2016, 9),
              "parent_id": "t3_a"} for s in ("socialism", "socialism", "socialism")]
    rows += [{"author": "L1", "subreddit": "communism", "score": 5,
              "created_utc": _ts(2016, 10), "parent_id": "t3_a"} for _ in range(2)]
    rows += [{"author": "L1", "subreddit": "Conservative", "score": 3,
              "created_utc": _ts(2016, 9), "parent_id": "t1_b"},
             {"author": "L1", "subreddit": "Conservative", "score": 3,
              "created_utc": _ts(2016, 11), "parent_id": "t1_b"}]
    # R1 (right): 3 own-side, 1 cross-cutting into socialism (downvoted = flame war)
    rows += [{"author": "R1", "subreddit": "Conservative", "score": 4,
              "created_utc": _ts(2016, 9), "parent_id": "t3_c"} for _ in range(3)]
    rows += [{"author": "R1", "subreddit": "socialism", "score": -2,
              "created_utc": _ts(2016, 10), "parent_id": "t1_d"}]
    # C1: only centrist subreddit -> no clear side, excluded from cross/same
    rows += [{"author": "C1", "subreddit": "news", "score": 1,
              "created_utc": _ts(2016, 9), "parent_id": "t3_e"} for _ in range(2)]
    # noise: skipped author + an unpositioned subreddit
    rows += [{"author": "[deleted]", "subreddit": "socialism", "score": 9},
             {"author": "L1", "subreddit": "AskReddit", "score": 9}]
    p = tmp_path / "comments_2016-09.bz2"
    _write(p, rows)
    return [str(p)]


def test_read_engagement_keeps_fields_and_filters(tmp_path):
    files = _corpus(tmp_path)
    a, pos, score, month, is_reply, fields = sp.read_engagement(files, SUB_POS)
    assert a.size == 13                                  # 7 L1 + 4 R1 + 2 C1; noise dropped
    assert fields == {"score": True, "created_utc": True, "parent_id": True}
    # the t1_ comments are flagged replies; t3_ are not
    assert is_reply.sum() == 3                            # 2 L1-cross + 1 R1-cross
    assert "AskReddit" not in [s for s in a]             # unpositioned sub excluded by sub_pos


def test_probe_classifies_cross_vs_same_and_aggregates(tmp_path):
    files = _corpus(tmp_path)
    a, pos, score, month, is_reply, _ = sp.read_engagement(files, SUB_POS)
    res = sp.probe(a, pos, score, month, is_reply, sub_tau=0.5, user_tau=0.3)
    s = res["summary"]
    assert s["n_users"] == 3 and s["n_sided"] == 2       # C1 has no side
    assert s["n_with_cross"] == 2                         # L1 and R1 both cross-cut
    assert s["cross_comments"] == 3 and s["same_comments"] == 8
    # reception: 2 of 3 cross-cutting comments upvoted (L1's two), R1's is downvoted
    assert abs(s["cross_upvoted_frac"] - 2 / 3) < 1e-9
    assert s["same_upvoted_frac"] == 1.0
    assert s["cross_reply_frac"] == 1.0                  # all cross-cutting are replies
    # return: L1 cross spans 2 months, R1 spans 1 -> median 1.5
    assert abs(s["cross_return_median"] - 1.5) < 1e-9
    assert "SENSIBLE" in sp._verdict(s)                  # majority upvoted


def test_min_score_threshold_excludes_default_plus_one(tmp_path):
    # a cross-cutting comment sitting at the auto +1 (score 1) = nobody engaged.
    # default min_score=1 -> NOT welcomed; min_score=0 recovers the legacy score>0 rule.
    rows = [{"author": "L1", "subreddit": "socialism", "score": 5, "parent_id": "t3_a"},
            {"author": "L1", "subreddit": "socialism", "score": 5, "parent_id": "t3_a"},
            {"author": "L1", "subreddit": "Conservative", "score": 1, "parent_id": "t1_b"}]
    p = tmp_path / "comments_2016-09.bz2"
    _write(p, rows)
    a, pos, score, month, is_reply, _ = sp.read_engagement([str(p)], SUB_POS)
    default = sp.probe(a, pos, score, month, is_reply)                # min_score=1 (default)
    assert default["summary"]["min_score"] == 1
    assert default["summary"]["cross_upvoted_frac"] == 0.0           # score-1 comment doesn't count
    legacy = sp.probe(a, pos, score, month, is_reply, min_score=0)    # old score>0 behaviour
    assert legacy["summary"]["cross_upvoted_frac"] == 1.0            # now it counts


def test_verdict_flags_flamewar_and_missing_score():
    assert "CONFOUNDED" in sp._verdict(
        {"cross_upvoted_frac": 0.1, "same_upvoted_frac": 0.9})
    assert "MIXED" in sp._verdict(
        {"cross_upvoted_frac": 0.4, "same_upvoted_frac": 0.9})
    assert "not measurable" in sp._verdict(
        {"cross_upvoted_frac": float("nan"), "same_upvoted_frac": float("nan")})


def test_missing_score_field_degrades_gracefully(tmp_path):
    # comments with no score/created_utc/parent_id -> probe still runs, flags fields
    rows = [{"author": "L1", "subreddit": "socialism"},
            {"author": "L1", "subreddit": "Conservative"},
            {"author": "L1", "subreddit": "socialism"}]
    p = tmp_path / "comments_2016-09.bz2"
    _write(p, rows)
    a, pos, score, month, is_reply, fields = sp.read_engagement([str(p)], SUB_POS)
    assert fields == {"score": False, "created_utc": False, "parent_id": False}
    res = sp.probe(a, pos, score, month, is_reply)
    assert np.isnan(res["summary"]["cross_upvoted_frac"])   # no reception signal
    assert "not measurable" in sp._verdict(res["summary"])
