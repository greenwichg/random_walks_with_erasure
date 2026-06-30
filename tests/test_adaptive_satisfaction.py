"""Tests for examples/adaptive_satisfaction.py (measured reception -> AdaptiveRWEB)."""

import importlib.util
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "adaptive_satisfaction", ROOT / "examples" / "adaptive_satisfaction.py")
asat = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(asat)


def test_read_probe_csv(tmp_path):
    p = tmp_path / "probe.csv"
    p.write_text("user,side,cross_n,cross_upvoted_frac,cross_reply_frac\n"
                 "alice,1,4,0.9,0.5\nbob,-1,0,,\n")
    rows = asat.read_probe_csv(str(p))
    assert rows["alice"]["cross_upvoted_frac"] == "0.9" and rows["bob"]["cross_n"] == "0"


def test_measured_exposure_maps_reception_else_default():
    rows = {
        "alice": {"cross_n": "5", "cross_upvoted_frac": "0.90"},   # welcomed -> high exposure
        "bob":   {"cross_n": "3", "cross_upvoted_frac": "0.20"},   # downvoted -> low exposure
        "carol": {"cross_n": "0", "cross_upvoted_frac": ""},       # no signal -> default
    }
    exp, measured = asat.measured_exposure(rows, ["alice", "bob", "carol", "dave"],
                                           min_cross=1, default=0.5)
    assert np.allclose(exp, [0.90, 0.20, 0.50, 0.50])
    assert list(measured) == [True, True, False, False]            # carol(no cross), dave(absent)


def test_measured_exposure_respects_min_cross():
    rows = {"x": {"cross_n": "1", "cross_upvoted_frac": "0.8"}}
    exp, measured = asat.measured_exposure(rows, ["x"], min_cross=3, default=0.5)
    assert exp[0] == 0.5 and not measured[0]                       # too few cross comments


def test_opposite_reach_rewards_higher_ranked_opposite():
    class _Fake:
        def __init__(self, R):
            self.R = np.asarray(R)

        def recommend(self, users, top_k):
            return self.R[:len(users), :top_k]
    theta = np.array([1.0])                                        # right-leaning user
    item_pos = np.array([-2.0, -1.0, 1.0, 2.0])                    # items 0,1 left (opposite)
    opp_first = asat.opposite_reach(_Fake([[0, 1, 2, 3]]), [0], theta, item_pos, k=4)[0]
    same_first = asat.opposite_reach(_Fake([[2, 3, 0, 1]]), [0], theta, item_pos, k=4)[0]
    # rank-weighted: the same items, but opposite-ranked-higher scores more
    assert opp_first > same_first > 0


def _tiny_npz(tmp_path):
    import bz2, json, sys
    sys.path.insert(0, str(ROOT / "examples"))
    import ingest_politosphere as ip
    rows = []
    for u in range(30):
        subs = (["socialism", "Anarchism", "communism101", "DebateCommunism",
                 "COMPLETEANARCHY"] if u < 15
                else ["Conservative", "Republican", "The_Donald", "Libertarian", "randpaul"])
        rows += [{"author": f"u{u}", "subreddit": s} for s in subs]
    with bz2.open(tmp_path / "comments_2016-01.bz2", "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    uc, ic, un, sn = ip._build_inputs(
        ip._read_comments(ip._comment_files(tmp_path, "comments_*.bz2")))
    uc, ic = ip._filter_min_codes(uc, ic, 1, 2)
    d = ip.build_mind(uc, ic, un, sn, lean=ip.load_subreddit_lean(ip._DEFAULT_LEAN))
    d = d.with_ideology(d.fit_ideology(n_iter=80, seed=0))
    npz = tmp_path / "tiny.npz"
    d.save(str(npz))
    return str(npz), d


def test_run_end_to_end_closes_the_loop(tmp_path):
    npz, d = _tiny_npz(tmp_path)
    # synthetic probe CSV: every user has a measured signal, varied tolerance
    uids = [str(u) for u in d.dataset.user_ids]
    lines = ["user,side,cross_n,cross_upvoted_frac"]
    for i, u in enumerate(uids):
        lines.append(f"{u},1,3,{(i % 10) / 10.0:.2f}")             # tolerance 0.0..0.9
    (tmp_path / "probe.csv").write_text("\n".join(lines) + "\n")

    report = asat.run(npz, str(tmp_path / "probe.csv"), k=5, sample=1000)
    assert "CLOSED-LOOP" in report and "measured cross-cutting signal" in report
    assert "adaptive reach" in report and "uniform reach" in report  # the redistribution table
    assert "Spearman" in report and "CAVEAT" in report               # honesty kept
