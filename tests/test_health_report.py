"""Tests for examples/health_report.py (Information Health Report v1)."""

import importlib.util
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "health_report", ROOT / "examples" / "health_report.py")
hr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hr)


def test_normalized_entropy():
    assert abs(hr.normalized_entropy([0.5, 0.5], 2) - 1.0) < 1e-9   # uniform over all
    assert abs(hr.normalized_entropy([0.5, 0.5], 4) - 0.5) < 1e-9   # 2 of 4 topics
    assert abs(hr.normalized_entropy([1.0], 2)) < 1e-9              # single topic -> 0
    assert np.isnan(hr.normalized_entropy([], 2))


def test_concentration():
    assert hr.hhi([0.5, 0.5]) == 0.5
    assert hr.effective_number([0.5, 0.5]) == 2.0
    assert hr.effective_number([1.0]) == 1.0
    assert abs(hr.top_n_share([0.5, 0.3, 0.2], 2) - 0.8) < 1e-9


def test_viewpoint_and_echo():
    left, centre, right = hr.viewpoint_shares([-1.0, -1.0, 1.0])
    assert abs(left - 2 / 3) < 1e-9 and abs(centre) < 1e-9 and abs(right - 1 / 3) < 1e-9
    assert abs(hr.cross_cutting_share([-1.0, -1.0, 1.0]) - 1 / 3) < 1e-9
    assert hr.echo_score(0.5, 0.5) == 0.0          # balanced
    assert hr.echo_score(1.0, 0.0) == 1.0          # one-sided
    assert np.isnan(hr.echo_score(0.0, 0.0))


def test_percentiles():
    assert list(hr.percentiles([1.0, 2.0, 3.0])) == [0.0, 50.0, 100.0]
    assert np.isnan(hr.percentiles([np.nan])[0])


def test_render_html(tmp_path):
    rep = dict(user=7, n_clicks=42, n_political=12,
               scores={"Topic Diversity": 72, "Source Diversity": 41,
                       "Reporting Ratio": None, "Emotional Balance": None,
                       "Echo Chamber Score": 63, "Viewpoint Balance": 58},
               overall=58, attention=None,
               top_categories=[("news", 0.5), ("sports", 0.2)],
               blind_spots=[("health", 0.0, 0.12)],
               top_publishers=[("a", 0.4), ("b", 0.3), ("c", 0.1), ("d", 0.05)],
               top_n_share=0.82, effective_sources=3.1, distinct_outlets=9,
               viewpoint=(0.3, 0.2, 0.5), mean_lean=0.4)
    out = tmp_path / "r.html"
    html = hr.render_html([rep], out=str(out))
    assert out.exists()
    assert "Reader #7" in html and "82%" in html and "health" in html
    assert "n/a" in html                           # un-enriched reporting / emotional
    assert "mirror, not a verdict" in html         # honesty disclaimer


def test_end_to_end_on_fixture(tmp_path):
    from rwe import load_mind
    fix = ROOT / "tests" / "fixtures" / "mind_demo"
    ids = [l.split("\t")[0] for l in open(fix / "news.tsv")]
    csv = tmp_path / "lean.csv"
    csv.write_text("news_id,position\n" + "\n".join(
        f"{i},{p}" for i, p in zip(ids, np.linspace(-1.8, 1.8, len(ids)))))
    d = load_mind(str(fix), positions_map=str(csv))
    pop = hr.compute(d, min_clicks=1, min_political=1)
    assert pop["topic"].shape[0] == d.n_users
    rep = hr.user_report(pop, d, 0)
    assert "scores" in rep and rep["n_clicks"] >= 1
    assert "INFORMATION HEALTH REPORT" in hr.format_report(rep)
    # without enrichment, the v2 lines are n/a
    assert rep["scores"]["Reporting Ratio"] is None and rep["attention"] is None


def test_load_item_csv_aligns_to_item_ids(tmp_path):
    ids = np.array(["N1", "N2", "N3"])
    p = tmp_path / "reg.csv"
    p.write_text("news_id,reporting\nN1,0.9\nN3,0.1\nN9,0.5\n")   # N9 absent, N2 missing
    arr = hr._load_item_csv(str(p), ids)["reporting"]
    assert arr[0] == 0.9 and np.isnan(arr[1]) and arr[2] == 0.1


def test_enrichment_populates_reporting_and_attention(tmp_path):
    from rwe import load_mind
    fix = ROOT / "tests" / "fixtures" / "mind_demo"
    ids = [l.split("\t")[0] for l in open(fix / "news.tsv")]
    lean = tmp_path / "lean.csv"
    lean.write_text("news_id,position\n" + "\n".join(
        f"{i},{p}" for i, p in zip(ids, np.linspace(-1.8, 1.8, len(ids)))))
    d = load_mind(str(fix), positions_map=str(lean))
    item_ids = d.dataset.item_ids
    register = {nid: 0.8 for nid in item_ids}                    # all reporting
    emo_labels = ["fear", "outrage", "analysis", "positive", "neutral"]
    emotion = {l: np.full(len(item_ids), 0.2) for l in emo_labels}   # uniform tone
    pop = hr.compute(d, min_clicks=1, min_political=1,
                     register=np.array([register[n] for n in item_ids]), emotion=emotion)
    rep = hr.user_report(pop, d, 0)
    assert rep["scores"]["Reporting Ratio"] is not None
    assert rep["attention"] is not None and abs(sum(rep["attention"].values()) - 1.0) < 1e-6
    html = hr.render_html([rep])
    assert "Attention profile" in html and "experimental" in html
