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


def test_cross_cutting_dead_centre_reader():
    # A dead-centre reader (weighted mean exactly at the centre) is no longer n/a:
    assert hr.cross_cutting_share([0.0, 0.0, 0.0]) == 0.0        # reads only centre -> 0.0 (as asked)
    assert hr.cross_cutting_share([-1.0, 1.0]) == 1.0           # perfectly balanced L/R -> maximal, not 0
    assert abs(hr.cross_cutting_share([-1.0, 0.0, 1.0]) - 2 / 3) < 1e-9   # off-centre share
    # a reader with NO political items stays n/a (nothing to balance)
    assert np.isnan(hr.cross_cutting_share([]))


def test_confidence_weighting_shifts_viewpoint():
    pos = [-1.0, -1.0, -1.0, 1.0]                  # 3 left, 1 right
    base = hr.viewpoint_shares(pos)
    wtd = hr.viewpoint_shares(pos, weights=[1, 1, 1, 0.01])   # down-weight the right item
    assert wtd[2] < base[2] and wtd[0] > base[0]   # right share shrinks, left grows
    # the lone (down-weighted) opposite item counts less toward cross-cutting
    assert (hr.cross_cutting_share(pos, weights=[1, 1, 1, 0.01])
            < hr.cross_cutting_share(pos))
    # uniform weights reproduce the unweighted result exactly
    assert (hr.viewpoint_shares(pos, weights=[2, 2, 2, 2]) ==
            hr.viewpoint_shares(pos))


def test_finite_pw_fallbacks():
    _, w = hr._finite_pw([-1.0, 0.0, 1.0])                    # weights=None -> uniform
    np.testing.assert_allclose(w, [1 / 3, 1 / 3, 1 / 3])
    _, w0 = hr._finite_pw([-1.0, 1.0], weights=[0.0, 0.0])    # all-zero -> uniform, never vanish
    np.testing.assert_allclose(w0, [0.5, 0.5])
    p2, w2 = hr._finite_pw([-1.0, 1.0, 0.5], weights=[1.0, np.nan, 1.0])  # unknown -> mean, not dropped
    assert p2.size == 3 and abs(w2.sum() - 1.0) < 1e-9 and (w2 > 0).all()


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
    assert "weak text-lean axis" in html           # axis caveat on Balance & openness


def test_axis_note_override_replaces_and_suppresses_caveat():
    # The default "weak text-lean" caveat is MIND-specific; a caller on a gold axis
    # (e.g. the Qbias-backed simulator) overrides it — mirrors main()'s --axis-note.
    rep = dict(user=1, n_clicks=10, n_political=6,
               scores={"Topic Diversity": 50, "Viewpoint Balance": 40,
                       "Echo Chamber Score": 55, "Open-Mindedness": 61},
               overall=51, attention=None, top_categories=[("news", 1.0)],
               blind_spots=[], top_publishers=[("a", 1.0)], top_n_share=1.0,
               effective_sources=1.0, distinct_outlets=1, viewpoint=(0.4, 0.2, 0.4),
               mean_lean=0.0)
    gold = dict(hr._LABELS["news"])                 # copy, like the CLI, so the preset is intact
    gold["section_notes"] = {**hr._SECTION_NOTES, "Balance & openness": "on the gold axis"}
    html = hr.render_html([rep], labels=gold)
    assert "on the gold axis" in html and "weak text-lean" not in html
    # empty string suppresses the note but keeps the section heading
    quiet = dict(hr._LABELS["news"])
    quiet["section_notes"] = {**hr._SECTION_NOTES, "Balance & openness": ""}
    html2 = hr.render_html([rep], labels=quiet)
    assert "weak text-lean" not in html2 and "on the gold axis" not in html2
    assert "Balance &amp; openness" in html2 or "Balance & openness" in html2
    # the shared preset dict was not mutated by either override
    assert hr._SECTION_NOTES["Balance & openness"] == "rests on a weak text-lean axis — directional only"


def test_eligible_pool_filters_by_political_floor():
    pop = {"n_clicks": np.array([10, 8, 3, 12]),   # idx 2 below click floor
           "n_pol":    np.array([5, 1, 9, 4])}
    assert list(hr._eligible_pool(pop, min_clicks=5)) == [0, 1, 3]
    # adding the political floor drops the click-eligible user with too few politics
    assert list(hr._eligible_pool(pop, min_clicks=5, min_political=3)) == [0, 3]


def test_source_diversity_na_carries_reason_on_mind():
    # MIND has no publisher labels (MSN URLs) -> Source Diversity is structurally
    # n/a; the report should say *why* rather than show a bare, broken-looking n/a.
    rep = dict(user=1, n_clicks=20, n_political=0,
               scores={"Topic Diversity": 55, "Source Diversity": None,
                       "Reporting Ratio": None, "Emotional Balance": None,
                       "Echo Chamber Score": None, "Viewpoint Balance": None,
                       "Open-Mindedness": None},
               overall=55, attention=None, political_share=0.0,
               top_categories=[("news", 0.6), ("sports", 0.4)], blind_spots=[],
               top_publishers=[], top_n_share=None, effective_sources=None,
               distinct_outlets=0, viewpoint=(float("nan"),) * 3, mean_lean=None)
    html = hr.render_html([rep])
    assert "MIND URLs are MSN" in html              # honest reason on the n/a bar
    assert "no publisher labels" in hr.format_report(rep)
    # the reason is *only* attached to Source Diversity, not every n/a
    rep["distinct_outlets"] = 7                       # publishers known -> no reason
    rep["scores"]["Source Diversity"] = 33
    assert "MIND URLs are MSN" not in hr.render_html([rep])


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


def test_end_to_end_confidence_weighting(tmp_path):
    from rwe import load_mind
    fix = ROOT / "tests" / "fixtures" / "mind_demo"
    ids = [l.split("\t")[0] for l in open(fix / "news.tsv")]
    csv = tmp_path / "lean.csv"
    csv.write_text("news_id,position\n" + "\n".join(
        f"{i},{p}" for i, p in zip(ids, np.linspace(-1.8, 1.8, len(ids)))))
    d = load_mind(str(fix), positions_map=str(csv))
    n_items = len(np.asarray(d.dataset.item_ids))

    base = hr.compute(d, min_clicks=1, min_political=1)                    # no confidence
    withc = hr.compute(d, min_clicks=1, min_political=1,
                       confidence=np.full(n_items, 0.9))                   # uniform high
    # uniform confidence must not change the scores vs. unweighted
    np.testing.assert_allclose(np.nan_to_num(base["cross"]),
                               np.nan_to_num(withc["cross"]), atol=1e-9)

    u = int(np.argmax(withc["n_pol"]))                                     # a political reader
    assert withc["n_pol"][u] >= 1
    rep = hr.user_report(withc, d, u)
    assert abs(rep["viewpoint_confidence"] - 0.9) < 1e-9                   # mean of uniform conf
    assert "Axis confidence: 0.90 (high)" in hr.format_report(rep)
    assert "axis confidence 0.90 (high)" in hr.render_html([rep])
    # a report built without confidence carries None and renders no axis-confidence line
    base_rep = hr.user_report(base, d, u)
    assert base_rep["viewpoint_confidence"] is None
    assert "Axis confidence" not in hr.format_report(base_rep)


def test_population_summary_and_card(tmp_path):
    from rwe import load_mind
    fix = ROOT / "tests" / "fixtures" / "mind_demo"
    ids = [l.split("\t")[0] for l in open(fix / "news.tsv")]
    lean = tmp_path / "lean.csv"
    lean.write_text("news_id,position\n" + "\n".join(
        f"{i},{p}" for i, p in zip(ids, np.linspace(-1.8, 1.8, len(ids)))))
    d = load_mind(str(fix), positions_map=str(lean))
    pop = hr.compute(d, min_clicks=1, min_political=1)
    eligible = hr._eligible_pool(pop, 1)
    s = hr.population_summary(pop, eligible)
    assert s["n_users"] == eligible.size and 0.0 <= s["political_reader_frac"] <= 1.0
    m = s["metrics"]["Topic Diversity"]
    assert m is not None and m["p25"] <= m["median"] <= m["p75"]
    txt = hr.format_population(s)
    assert "POPULATION VIEW" in txt and "the typical reader" in txt
    # the population card renders at the top of the page; default (None) omits it
    html = hr.render_html([hr.user_report(pop, d, int(eligible[0]))], population=s)
    assert "Population view" in html and "Typical reader" in html
    assert "Population view" not in hr.render_html([hr.user_report(pop, d, int(eligible[0]))])


def test_load_item_csv_aligns_to_item_ids(tmp_path):
    ids = np.array(["N1", "N2", "N3"])
    p = tmp_path / "reg.csv"
    p.write_text("news_id,reporting\nN1,0.9\nN3,0.1\nN9,0.5\n")   # N9 absent, N2 missing
    arr = hr._load_item_csv(str(p), ids)["reporting"]
    assert arr[0] == 0.9 and np.isnan(arr[1]) and arr[2] == 0.1


def test_read_impressions_splits_shown_vs_clicked():
    fix = ROOT / "tests" / "fixtures" / "mind_demo"
    shown, clicked = hr._read_impressions(str(fix / "behaviors.tsv"))
    assert "N7" in shown["U1"] and "N7" in clicked["U1"]        # N7-1 (clicked)
    assert "N4" in shown["U1"] and "N4" not in clicked["U1"]    # N4-0 (shown, not clicked)


def test_selective_exposure_and_political_share(tmp_path):
    from rwe import load_mind
    fix = ROOT / "tests" / "fixtures" / "mind_demo"
    ids = [l.split("\t")[0] for l in open(fix / "news.tsv")]
    lean = tmp_path / "lean.csv"
    lean.write_text("news_id,position\n" + "\n".join(
        f"{i},{p}" for i, p in zip(ids, np.linspace(-1.8, 1.8, len(ids)))))
    d = load_mind(str(fix), positions_map=str(lean))
    uidx = {u: i for i, u in enumerate(np.asarray(d.dataset.user_ids).tolist())}

    sel = hr.selective_exposure_array(d, str(fix / "behaviors.tsv"))
    # U1 (left) was shown one opposite-side (right) article, N7, and clicked it -> 1.0
    assert sel[uidx["U1"]] == 1.0

    pop = hr.compute(d, min_clicks=1, min_political=1, selective=sel)
    rep = hr.user_report(pop, d, uidx["U1"])
    assert rep["scores"]["Open-Mindedness"] is not None
    assert rep["political_share"] is not None and 0.0 <= rep["political_share"] <= 1.0


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
