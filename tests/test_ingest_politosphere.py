"""Tests for examples/ingest_politosphere.py (Reddit Politosphere -> RWE .npz)."""

import bz2
import importlib.util
import json
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ingest_politosphere", ROOT / "examples" / "ingest_politosphere.py")
ip = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ip)


def _write_bz2(path, rows):
    with bz2.open(path, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_read_and_build_dedups_and_skips(tmp_path):
    _write_bz2(tmp_path / "comments_2016-01.bz2", [
        {"author": "u1", "subreddit": "Conservative"},
        {"author": "u1", "subreddit": "Conservative"},          # dup line
        {"author": "u1", "subreddit": "Republican"},
        {"author": "u2", "subreddit": "democrats"},
        {"author": "[deleted]", "subreddit": "politics"},        # skipped
        {"author": "u3", "subreddit": "Libertarian"},
    ])
    _write_bz2(tmp_path / "comments_2016-02.bz2", [
        {"author": "u2", "subreddit": "progressive"},
        {"author": "u1", "subreddit": "Conservative"},           # cross-file dup
        {"author": "u3", "subreddit": "Conservative"},
    ])
    files = ip._comment_files(tmp_path, "comments_*.bz2")
    assert len(files) == 2
    pairs = list(ip._read_comments(files))
    assert len(pairs) == 8                                       # [deleted] dropped
    uc, ic, un, sn = ip._build_inputs(iter(pairs))
    assert uc.size == 6                                          # unique (user, subreddit)
    u_of, s_of = un[uc], sn[ic]                                  # decode names per row
    assert set(s_of[u_of == "u1"]) == {"Conservative", "Republican"}


def test_read_comments_respects_limit(tmp_path):
    _write_bz2(tmp_path / "comments_2016-01.bz2",
               [{"author": f"u{i}", "subreddit": "democrats"} for i in range(10)])
    files = ip._comment_files(tmp_path, "comments_*.bz2")
    assert len(list(ip._read_comments(files, limit=4))) == 4


def test_filter_min_codes_drops_low_degree(tmp_path):
    uc = np.array([0, 0, 1, 2, 2]); ic = np.array([0, 1, 0, 2, 3])
    fuc, fic = ip._filter_min_codes(uc, ic, min_user=1, min_item=2)
    assert set(fic.tolist()) == {0}            # only subreddit 0 has >= 2 users
    assert set(fuc.tolist()) == {0, 1}         # users 2's subreddits were dropped
    # no-op fast path
    a, b = ip._filter_min_codes(uc, ic, 1, 1)
    assert a.size == uc.size


def test_build_mind_seeds_positions_and_roundtrips(tmp_path):
    uc = np.array([0, 0, 1, 2]); ic = np.array([0, 1, 2, 3])
    u_names = np.array(["u1", "u2", "u3"], dtype=object)
    s_names = np.array(["Conservative", "Republican", "democrats", "Libertarian"], dtype=object)
    lean = ip.load_subreddit_lean(str(ROOT / "examples" / "data" / "subreddit_lean.csv"))
    d = ip.build_mind(uc, ic, u_names, s_names, lean=lean)
    assert d.n_users == 3 and d.n_items == 4 and d.political.all()
    pos = {s: p for s, p in zip(np.asarray(d.dataset.item_ids), d.item_positions)}
    assert pos["Conservative"] == 2 and pos["democrats"] == -2 and pos["Libertarian"] == 1
    # no "publisher" for subreddits -> outlets blank; the report uses titles ("r/<sub>")
    assert list(d.outlets) == [""] * d.n_items
    assert list(d.titles) == [f"r/{s}" for s in np.asarray(d.dataset.item_ids)]
    out = tmp_path / "p.npz"
    d.save(out)
    d2 = ip.MINDData.load(str(out))                              # full MIND container
    assert d2.n_items == 4
    assert np.allclose(np.nan_to_num(d2.item_positions), np.nan_to_num(d.item_positions))


def test_load_subreddit_lean_skips_comments_header_and_garbage(tmp_path):
    p = tmp_path / "l.csv"
    p.write_text("# a comment\nsubreddit,lean\nConservative,2\ndemocrats,-2\nbad,xx\n")
    t = ip.load_subreddit_lean(str(p))
    assert t["conservative"] == 2 and t["democrats"] == -2 and "bad" not in t


def test_bundled_lean_table_is_well_formed():
    t = ip.load_subreddit_lean(str(ROOT / "examples" / "data" / "subreddit_lean.csv"))
    assert t["conservative"] > 0 and t["democrats"] < 0      # oriented L<0<R
    assert all(-2 <= v <= 2 for v in t.values()) and len(t) >= 20


def test_health_report_reddit_domain():
    """The Information Health Report runs on a Politosphere container: Source Diversity
    (community breadth) + the political metrics (on the behavioral axis) populate, the
    news-only metrics go n/a, and `--domain reddit` swaps the MIND nouns."""
    _hr = importlib.util.spec_from_file_location(
        "health_report", ROOT / "examples" / "health_report.py")
    hr = importlib.util.module_from_spec(_hr); _hr.loader.exec_module(hr)
    # 8 users x 6 subreddits (3 left, 3 right); even users left, odd right, user 0 cross-cuts.
    uc, ic = [], []
    for u in range(8):
        picks = list(range(0, 3) if u % 2 == 0 else range(3, 6))
        if u == 0:
            picks.append(5)                              # user 0 reaches into the right
        for i in picks:
            uc.append(u); ic.append(i)
    uc, ic = np.array(uc), np.array(ic)
    un = np.array([f"u{u}" for u in range(8)], dtype=object)
    sn = np.array(["socialism", "communism", "democrats",
                   "Conservative", "Republican", "randpaul"], dtype=object)
    d = ip.build_mind(uc, ic, un, sn, lean=None)
    # simulate an OLDER cached ingest: blank `outlets`, but the subreddit is still in
    # `subcategories` + a finite axis position for every subreddit (post-`--ideology`).
    d = type(d)(**{**d.__dict__,
                   "outlets": np.array([""] * d.n_items, dtype=object),
                   "item_positions": np.array([-1.6, -1.5, -1.0, 1.0, 1.5, 1.6])})

    lab = hr._LABELS["reddit"]
    src = np.asarray(getattr(d, lab["source_attr"]))          # -> titles ("r/<sub>")
    pop = hr.compute(d, min_clicks=3, min_political=3, source=src)
    rep = hr.user_report(pop, d, 0)
    # source + axis metrics defined even with blank outlets; news-only ones are n/a
    assert rep["scores"]["Source Diversity"] is not None      # community breadth
    assert rep["top_publishers"]                              # top subreddits populated
    assert all(name.startswith("r/") for name, _ in rep["top_publishers"])
    assert rep["scores"]["Viewpoint Balance"] is not None
    assert rep["scores"]["Echo Chamber Score"] is not None
    assert rep["scores"]["Topic Diversity"] is None           # single 'political' category
    assert rep["scores"]["Reporting Ratio"] is None
    assert rep["scores"]["Open-Mindedness"] is None
    # reddit wording, no MIND nouns / MIND-specific n/a reason
    txt = hr.format_report(rep, lab)
    assert "subreddits, all political" in txt and "Top subreddits" in txt
    assert "articles read" not in txt and "Top topics" not in txt
    assert "MIND URLs are MSN" not in txt
    html = hr.render_html([rep], labels=lab)
    assert "Reddit" in html and "distinct communities" in html and "MSN-News" not in html
    # the Balance section note is the *validated*-axis one, not MIND's weak-axis caveat
    assert "validated behavioral axis" in html and "weak text-lean axis" not in html
    # text-derived metrics are *structurally* n/a on Reddit -> say why, don't prompt a
    # classifier that can't apply (no article text for subreddits)
    assert "no article text to classify on Reddit" in html       # attention block
    assert "run classify_emotion.py" not in html
    assert "no article text on Reddit" in txt                    # reporting/emotional n/a
    assert "no impressions data in Politosphere" in html          # open-mindedness n/a
    # every n/a line carries a reason in parens, and none nests parens awkwardly
    na_lines = [l for l in txt.splitlines() if "n/a" in l]
    assert na_lines and all("(" in l for l in na_lines), "a bare n/a slipped through"
    assert not any("((" in l or "))" in l for l in na_lines), "nested-paren reason"


def test_cli_end_to_end(tmp_path):
    _write_bz2(tmp_path / "comments_2016-01.bz2", [
        {"author": f"u{u}", "subreddit": s}
        for u in range(10)
        for s in (["Conservative", "Republican"] if u % 2 else ["democrats", "progressive"])
    ])
    out = tmp_path / "p.npz"
    r = subprocess.run(
        [sys.executable, str(ROOT / "examples" / "ingest_politosphere.py"),
         "--comments-dir", str(tmp_path), "--min-user-clicks", "1",
         "--min-item-clicks", "1", "--out", str(out)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    d = ip.MINDData.load(str(out))
    assert d.n_users == 10 and d.n_items == 4
