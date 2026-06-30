"""Tests for examples/app.py (the stdlib web UI). No server, no API calls -- we test the
pure HTML helpers and the render(path, query) closure end-to-end on a tiny built npz."""

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("app", ROOT / "examples" / "app.py")
app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app)


def test_checks_html_ok_and_warn():
    assert "✓ every number" in app._checks_html([], [], True)
    assert "⚠ numbers not found" in app._checks_html(["99"], [], True)
    assert "every recommended title" in app._checks_html([], [], True)
    assert "titles not in the list" in app._checks_html([], ["Some Fake Headline"], True)


def test_index_body_lists_picks():
    b = app._index_body([3, 7], "reddit")
    assert "reader 3" in b and "reader 7" in b and "reddit" in b


def _tiny_reddit_npz(tmp_path):
    import bz2, json, sys
    sys.path.insert(0, str(ROOT / "examples"))
    import ingest_politosphere as ip
    rows = []
    for u in range(24):
        # >=6 subreddits/user so they clear the app's >=5-click eligibility floor
        subs = (["socialism", "Anarchism", "communism101", "DebateCommunism",
                 "COMPLETEANARCHY", "DebateAnarchism"] if u < 12
                else ["Conservative", "Republican", "The_Donald", "Libertarian",
                      "randpaul", "AskTrumpSupporters"])
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
    return str(npz)


def test_app_renders_index_and_report(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)        # no live API in tests
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    render = app.make_renderer(_tiny_reddit_npz(tmp_path), "reddit", "gemini", None)

    home = render("/", {})
    assert "Information Health Report" in home and "reader" in home

    rep = render("/report", {})                                # auto-pick a reader
    assert "<iframe" in rep                                    # the health card is embedded
    assert "chosen by the RWE-B recommender" in rep            # real recommender output
    assert "Reader " in rep and "r/" in rep                    # a community shows in the card
    assert "GEMINI_API_KEY" in rep                             # graceful no-key note, not a crash
