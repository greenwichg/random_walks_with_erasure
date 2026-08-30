"""Tests for the live recommendation source (examples/feed_source.py).

Proves the smallest-seam claim: FeedArticle -> a qbias-format CSV -> the EXISTING corpus builder
(simulate_users.run(qbias=...)) -> the recommender operates over live articles exactly as over the
static qbias catalog. No recommendation algorithm is touched."""

import csv as _csv
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import store          # noqa: E402
import feed_source    # noqa: E402


def _add(st, canonical, url, publisher, lean, *, title="A story about the vote and the economy",
         category="Politics"):
    st.upsert_feed_article(
        canonical_url=canonical, url=url, publisher=publisher, source_publisher=publisher,
        title=title, description="context", body=None,
        # now-relative: the C4 freshness gate (default 60 days) must see these as candidates
        published_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        source_feed="feed://x",
        scored={"article_id": canonical, "outlet": publisher, "category": category,
                "lean": lean, "political": True, "title": title})


def _seed(st, per_outlet=70):
    """A cross-spectrum FeedArticle catalog: left / center / right, enough to simulate a population."""
    for name, lean in [("The Guardian", -1.5), ("Associated Press", 0.0), ("Fox News", 1.6)]:
        for k in range(per_outlet):
            u = f"https://ex.com/{name.replace(' ', '').lower()}/{k}"
            _add(st, u, u, name, lean, title=f"{name} reports on the vote and the economy, item {k}")


# --------------------------------------------------------------------------- #
# Unit: bias mapping + CSV export + prepare threshold.
# --------------------------------------------------------------------------- #
def test_bias_label_matches_allsides_buckets():
    assert feed_source._bias_label(-1.5) == "left"
    assert feed_source._bias_label(1.6) == "right"
    assert feed_source._bias_label(0.2) == "center"
    assert feed_source._bias_label(float("nan")) == ""       # unknown -> dropped by the builder
    assert feed_source._bias_label(None) == ""


def test_bias_label_is_five_point_and_preserves_the_sided_partition():
    """Fractional leans: a moderate sided lean emits the *lean* label (the grade the ranking space
    was measured to be missing), a strong one stays at the pole — and the sided/centre PARTITION
    is byte-identical to the 3-point mapping, so cross-cutting membership and report bucket
    shares cannot move. Boundaries on the DECLARED [-2, 2] AllSides scale, where the registry (the
    scorer's single lean source) writes Lean Left/Right as ±1 and Left/Right as ±2: sided at
    |v| >= center (0.5, inclusive — unchanged), full at |v| >= 1.5 (the lattice midpoint). A cut
    derived from ``center`` instead — the first draft's 0.75 — leaves the lean band EMPTY on the
    registry's integer lattice, and nothing ever grades in production."""
    assert feed_source._bias_label(-1.0) == "lean left"      # AllSides Lean Left (CNN, NPR, …)
    assert feed_source._bias_label(1.0) == "lean right"      # AllSides Lean Right (Geo TV, …)
    assert feed_source._bias_label(-2.0) == "left"           # AllSides Left
    assert feed_source._bias_label(2.0) == "right"           # AllSides Right (Fox News, NY Post)
    assert feed_source._bias_label(-0.5) == "lean left"      # sided boundary, inclusive as before
    assert feed_source._bias_label(0.5) == "lean right"
    assert feed_source._bias_label(-1.5) == "left"           # full boundary, inclusive
    assert feed_source._bias_label(1.5) == "right"
    assert feed_source._bias_label(0.49) == "center"         # centre unchanged
    # the partition invariant, exhaustively over a fine sweep of the declared scale: sided iff
    # |v| >= 0.5, exactly as the 3-point mapping had it — the grade never crosses the centre line.
    for i in range(-40, 41):
        v = i / 20.0
        sided = feed_source._bias_label(v) in ("left", "lean left", "lean right", "right")
        assert sided == (abs(v) >= 0.5), v


def test_export_catalog_csv_format(tmp_path):
    st = store.Store("sqlite://")
    _add(st, "https://foxnews.com/a", "https://www.foxnews.com/a", "Fox News", 1.6, title="Border plan")
    _add(st, "https://nytimes.com/b", "https://www.nytimes.com/b", "New York Times", -1.4, title="Senate vote")
    path = str(tmp_path / "c.csv")
    assert feed_source.export_catalog_csv(st, path) == 2

    rows = list(_csv.DictReader(open(path, encoding="utf-8")))
    # qbias-format + the Commit R1 political column (the scored article-level flag)
    assert set(rows[0].keys()) == {"title", "source", "bias_rating", "tags", "url", "political",
                                   # appended for the For You country preference; the corpus
                                   # builder reads columns by name, so a trailing field is inert
                                   "country",
                                   # appended for the corpus subsample's recency weighting — the
                                   # recommender still has no time feature, this only lets the
                                   # LOADER prefer newer rows (RWE_REC_RECENCY_HALFLIFE_DAYS)
                                   "published_at"}
    by = {r["source"]: r for r in rows}
    assert by["Fox News"]["bias_rating"] == "right"          # 1.6 -> past the 1.5 lattice midpoint
    assert by["New York Times"]["bias_rating"] == "lean left"  # -1.4 -> the grade now survives
    assert by["Fox News"]["url"].startswith("https://www.foxnews.com")  # url carried (builder ignores it)
    assert by["Fox News"]["tags"] == "Politics"
    assert by["Fox News"]["political"] == "1"     # the seed scores political=True
    # The real timestamp, carried through — never a placeholder. An article the feed dated is
    # dated; the loader's weighting is only as honest as this column.
    assert by["Fox News"]["published_at"], "the export must carry the publication date"
    assert by["Fox News"]["published_at"].startswith("20")


def test_export_caps_per_outlet(tmp_path):
    """max_per_outlet stops a high-volume ('firehose') feed from swamping the recommendation corpus:
    the dominant outlet is capped, thin outlets are kept whole, and the Q{i}->url map still aligns."""
    st = store.Store("sqlite://")
    for k in range(100):                                          # a firehose outlet
        u = f"https://wsj.com/{k}"
        _add(st, u, u, "Wall Street Journal", 0.8, title=f"wsj {k}")
    for name, lean in [("NPR", -1.0), ("Fox News", 1.4)]:        # two thin outlets
        for k in range(10):
            u = f"https://{name.replace(' ', '').lower()}.com/{k}"
            _add(st, u, u, name, lean, title=f"{name} {k}")

    path = str(tmp_path / "c.csv")
    n = feed_source.export_catalog_csv(st, path, max_per_outlet=15)
    counts = {}
    for r in _csv.DictReader(open(path, encoding="utf-8")):
        counts[r["source"]] = counts.get(r["source"], 0) + 1
    assert counts["Wall Street Journal"] == 15                   # firehose capped
    assert counts["NPR"] == 10 and counts["Fox News"] == 10      # thin outlets kept whole
    assert n == 35
    # no cap -> everything is exported (default behaviour is unchanged)
    assert feed_source.export_catalog_csv(st, str(tmp_path / "all.csv")) == 120
    # the url map still mirrors the (capped) exported rows one-to-one
    m = feed_source.load_url_map(path)
    assert len(m) == n and all(v.startswith("http") for v in m.values())


def test_prepare_threshold_and_fallback(tmp_path):
    st = store.Store("sqlite://")
    assert feed_source.prepare(st, str(tmp_path / "x.csv"), min_articles=5) is None   # below -> fallback
    for i in range(6):
        _add(st, f"https://x.com/{i}", f"https://x.com/{i}", "X", 0.0)
    out = feed_source.prepare(st, str(tmp_path / "x.csv"), min_articles=5)
    assert out and os.path.exists(out)                       # at/above -> exported


def test_enabled_flag(monkeypatch):
    monkeypatch.delenv("RWE_RECS_SOURCE", raising=False)
    assert feed_source.enabled() is False
    monkeypatch.setenv("RWE_RECS_SOURCE", "feed")
    assert feed_source.enabled() is True


def test_load_url_map_mirrors_qbias_ids(tmp_path):
    """Q{i} keys by CSV data-row index, matching catalog_from_qbias's enumerate — including the gap
    left by a row the builder drops (empty bias), whose (unused) entry is still present + harmless."""
    p = tmp_path / "c.csv"
    p.write_text("title,source,bias_rating,tags,url\n"
                 "a,Fox News,right,Politics,https://www.foxnews.com/a\n"
                 "b,Somewhere,,Politics,https://x.com/b\n"          # empty bias -> builder skips (no Q1 rec)
                 "c,CNN,left,Politics,https://www.cnn.com/c\n", encoding="utf-8")
    m = feed_source.load_url_map(str(p))
    assert m["Q0"] == "https://www.foxnews.com/a"
    assert m["Q2"] == "https://www.cnn.com/c"                        # row index 2 (0-based), not compacted
    assert m["Q1"] == "https://x.com/b"                             # present but never emitted as a rec id


# --------------------------------------------------------------------------- #
# End-to-end: the recommender, sourced from FeedArticle, behaves as it does over qbias.
# --------------------------------------------------------------------------- #
def test_recommender_sources_from_feed_catalog(tmp_path):
    pytest.importorskip("scipy")                             # simulate_users needs the science stack
    import api_server as engine

    st = store.Store("sqlite://")
    _seed(st, per_outlet=70)                                 # 210 live articles across L/C/R
    csv_path = str(tmp_path / "feed.csv")
    assert feed_source.export_catalog_csv(st, csv_path) == 210

    # Build the WHOLE engine over the FeedArticle-derived catalog via the unchanged qbias machinery.
    profile = engine.DatasetProfile.synthetic(n_users=120, max_items=500, seed=0, qbias_csv=csv_path)
    be = engine.Backend(profile)
    assert len(be.eligible) > 0                              # a viable simulated population was built

    recs = be.recommendations(be.demo_user)
    assert len(recs) > 0
    expected = {engine._prettify(o) for o in ("The Guardian", "Associated Press", "Fox News")}
    publishers = {r["article"]["publisher"] for r in recs}
    assert publishers and publishers <= expected            # recommendations come FROM the feed catalog
    assert all("Outlet" not in p for p in publishers)       # not the synthetic 'Outlet N'
    # sanity: the same report/metric machinery runs over the live catalog
    report = be.report(be.demo_user)
    assert 0 <= report["overall"] <= 100 and len(report["sources"]) > 0


def test_recommendations_carry_verified_url_and_degrade_gracefully(tmp_path):
    """The Honest URL Pass-through: recs from the live feed catalog carry the real publisher URL;
    the id-is-a-URL rule gives history its URL too; and with no resolver, no URL is emitted."""
    pytest.importorskip("scipy")
    import api_server as engine

    st = store.Store("sqlite://")
    _seed(st, per_outlet=70)
    csv_path = str(tmp_path / "feed.csv")
    feed_source.export_catalog_csv(st, csv_path)
    profile = engine.DatasetProfile.synthetic(n_users=120, max_items=500, seed=0, qbias_csv=csv_path)
    be = engine.Backend(profile)
    be.attach_url_resolver(feed_source.load_url_map(csv_path))

    recs = be.recommendations(be.demo_user)
    assert recs
    for r in recs:
        a = r["article"]
        assert a["id"].startswith("Q")                              # qbias-style corpus id
        assert a.get("url", "").startswith("https://ex.com/")       # ...resolved to a verified FeedArticle URL

    # id-is-a-URL rule: a real reader's stored read (history) carries its own canonical URL.
    hist = be.serialize_history([{
        "id": 1, "canonicalUrl": "https://www.foxnews.com/x",
        "scored": {"article_id": "https://www.foxnews.com/x", "outlet": "Fox News", "title": "t", "lean": 1.0},
        "observedAt": None, "createdAt": None}])
    assert hist[0]["article"]["url"] == "https://www.foxnews.com/x"

    # graceful: cleared resolver + a non-URL id -> no url emitted (never fabricated).
    assert be._resolve_url("S144") is None
    be.attach_url_resolver({})
    assert all("url" not in r["article"] for r in be.recommendations(be.demo_user))


def test_export_read_demand_exemption(tmp_path):
    """Commit 18 (D5): an article a user READ is never trimmed out of the corpus export — neither by
    the per-outlet cap nor by the max_items recency window — so a reader can't be disconnected from
    the recommendation graph by composition balancing."""
    st = store.Store("sqlite://")
    _seed(st, per_outlet=30)                                   # newest-first catalog, 3 outlets
    # the OLDEST Guardian article (first inserted -> last in recency, certain to be capped out)
    read_url = "https://ex.com/theguardian/0"
    u = st.upsert_user_by_identity("google", "reader").id
    st.add_read(u, read_url, {"article_id": read_url}, None)

    path = str(tmp_path / "c.csv")
    # tight caps: only 5/outlet and only 12 items total — without the exemption the read article
    # (old + beyond the per-outlet cap) cannot survive both trims
    feed_source.export_catalog_csv(st, path, max_items=12, max_per_outlet=5)
    urls = {r["url"] for r in _csv.DictReader(open(path, encoding="utf-8"))}
    assert read_url in urls
    # and the cap still binds for everything unread (composition stays balanced)
    guardian = [x for x in urls if "theguardian" in x]
    assert len(guardian) <= 5 + 1                              # capped set + the exempt read article


def test_graded_positions_flow_into_the_corpus_and_stay_consistent(tmp_path):
    """End to end: catalog leans -> 5-point labels -> catalog_from_qbias positions. A lean outlet
    (0.6/0.7-grade) lands at ±LEAN_GRADE in ranking space, a pole outlet at ±1.0 — five distinct
    positions where the pre-fractional pipeline produced three. And the consistency triple that
    picked LEAN_GRADE = 0.6 holds for every graded position: sided for the report
    (|pos| > LEAN_TAU, strict), cross-cutting-eligible (|pos| >= 0.5), sided for the web
    (strict > 0.5) — no position may be centre on one surface and cross-cutting on another."""
    import numpy as np
    import sys
    sys.path.insert(0, str(ROOT / "examples"))
    from simulate_users import catalog_from_qbias
    from validate_qbias import LEAN_GRADE
    import health_report as hr

    st = store.Store("sqlite://")
    for name, lean in [("Truthout", -2.0), ("CNN", -1.0), ("AP", 0.0),
                       ("Geo TV", 1.0), ("Daily Caller", 2.0)]:
        for k in range(8):
            u = f"https://ex.com/{name.replace(' ', '').lower()}/{k}"
            _add(st, u, u, name, lean, title=f"{name} covers the vote and the economy, item {k}")
    out = feed_source.prepare(st, str(tmp_path / "graded.csv"), min_articles=5)
    cat = catalog_from_qbias(out)

    by_outlet = {}
    for o, p in zip(cat.outlets, cat.positions):
        by_outlet.setdefault(str(o), set()).add(round(float(p), 2))
    assert by_outlet["Truthout"] == {-1.0} and by_outlet["Daily Caller"] == {1.0}
    assert by_outlet["CNN"] == {-LEAN_GRADE}, "an AllSides Lean Left outlet keeps its grade"
    assert by_outlet["Geo TV"] == {LEAN_GRADE}
    assert by_outlet["AP"] == {0.0}
    assert len(set(np.round(cat.positions, 2))) == 5

    for p in set(float(x) for x in cat.positions):
        if p == 0.0:
            continue
        assert abs(p) > hr.LEAN_TAU, f"{p} would sit in the report's centre bucket"
        assert abs(p) >= 0.5, f"{p} would fail the cross-cutting gate"


def test_graded_positions_give_the_bridge_geometry_something_to_grade(tmp_path):
    """The point of the exercise (docs/RECOMMENDATION_STRENGTH_SLIDER.md): with 3-point positions,
    RWEB's max_distance had nothing to grade — measured byte-identical slices at every setting.
    With graded positions a bound that admits the near side but not the far side must change the
    bridge erasure: the far pole stays suppressed at epsilon while the lean article becomes a
    bridge. Mutation-checked: collapse the labels back to 3-point and this fails."""
    import numpy as np
    import sys
    sys.path.insert(0, str(ROOT / "examples"))
    from simulate_users import catalog_from_qbias
    from validate_qbias import LEAN_GRADE
    from rwe import RWEB, FeedbackGraph

    st = store.Store("sqlite://")
    for name, lean in [("Truthout", -2.0), ("CNN", -1.0), ("AP", 0.0),
                       ("Geo TV", 1.0), ("Daily Caller", 2.0)]:
        for k in range(8):
            u = f"https://ex.com/{name.replace(' ', '').lower()}/{k}"
            _add(st, u, u, name, lean, title=f"{name} covers the vote and the economy, item {k}")
    cat = catalog_from_qbias(feed_source.prepare(st, str(tmp_path / "g2.csv"), min_articles=5))

    pos = np.asarray(cat.positions, dtype=float)
    n = len(pos)
    A = np.zeros((2, n))
    A[0, np.flatnonzero(pos >= LEAN_GRADE)[:4]] = 1     # a right-diet reader
    A[1, np.flatnonzero(pos <= -LEAN_GRADE)[:4]] = 1    # and a left one, so center isn't degenerate
    fg = FeedbackGraph(A)
    theta = np.array([0.8, -0.8])
    i_lean, i_pole = int(np.argmin(np.abs(pos + LEAN_GRADE))), int(np.argmin(np.abs(pos + 1.0)))

    bounded = RWEB(fg, theta, pos, epsilon=0.9, max_distance=1.5)   # reader@0.8: -0.6 in, -1.0 out
    q = np.asarray(bounded._compute(np.array([0])))[0]
    assert q[i_pole] == 0.9, "beyond the bound: suppressed at epsilon, not treated as a bridge"
    assert q[i_lean] < 0.9, "within the bound: a graded bridge with sim-based erasure"

    unbounded = RWEB(fg, theta, pos, epsilon=0.9, max_distance=None)
    q2 = np.asarray(unbounded._compute(np.array([0])))[0]
    assert q2[i_pole] < q2[i_lean] < 0.9, \
        "unbounded: both are bridges and the farther one is preferred (lower erasure)"


def test_load_country_map_mirrors_the_url_map_indexing(tmp_path):
    """The Q{i} row rule, and the fail-honest cases: rows with no country simply have no entry
    (neutral in the nudge, never a mismatch), and a catalog written before the column existed
    yields an empty map — which disables the preference rather than mis-ranking on absent data."""
    import csv as _csv
    p = tmp_path / "cat.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(feed_source._COLUMNS)
        w.writerow(["t0", "P", "center", "Sports", "https://a.example/0", "0", "IN"])
        w.writerow(["t1", "P", "center", "Sports", "https://a.example/1", "0", ""])
        w.writerow(["t2", "P", "center", "Sports", "https://a.example/2", "0", "gb"])
        w.writerow(["t3", "P", "center", "Sports", "https://a.example/3", "0", "XYZ"])
        w.writerow(["t4", "P", "center", "Sports", "https://a.example/4", "0", "IN|pk|1|"])
    m = feed_source.load_country_map(str(p))
    assert m["Q0"] == frozenset({"IN"}) and m["Q2"] == frozenset({"GB"})   # normalized up
    assert "Q1" not in m and "Q3" not in m                  # blank and non-ISO rows carry nothing
    # pipe-separated and upper-cased; entries that are not two letters are dropped.
    # (Shape is the whole contract here — the WRITER decides which codes are real.)
    assert m.get("Q4") == frozenset({"IN", "PK"})
    assert set(m) <= set(feed_source.load_url_map(str(p)))  # same id space as the URL map

    legacy = tmp_path / "legacy.csv"
    with open(legacy, "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(["title", "source", "bias_rating", "tags", "url", "political"])
        w.writerow(["t", "P", "center", "Sports", "https://a.example/x", "0"])
    assert feed_source.load_country_map(str(legacy)) == {}
    assert feed_source.load_country_map(str(tmp_path / "missing.csv")) == {}


def test_mentioned_countries_matches_names_not_substrings():
    """Content-level country detection, with its limits pinned rather than assumed."""
    m = feed_source.mentioned_countries
    assert m("India and Pakistan resume trade talks") == frozenset({"IN", "PK"})
    assert m("Indianapolis 500 results") == frozenset()      # word boundary, not substring
    assert m("UK and US sign a deal") == frozenset({"GB", "US"})
    assert m("") == frozenset() and m(None) == frozenset()
    assert m("Indian markets rally") == frozenset({"IN"})    # demonyms count


def test_article_countries_separates_content_from_provenance():
    """`content` is what the article is ABOUT; `publisher` is where the outlet lives. The two are
    different questions and the union of them is a third — a country selector that conflates them
    tells the reader something untrue."""
    a = {"eventCountries": ["IN"], "title": "Floods hit Nepal border districts",
         "description": "", "country": "US"}
    f = feed_source.article_countries
    assert f(a, "event") == frozenset({"IN"})
    assert f(a, "publisher") == frozenset({"US"})
    assert "NP" in f(a, "mention") and "US" not in f(a, "mention")
    assert f(a, "content") == frozenset({"IN", "NP"})        # never the publisher
    assert f(a, "union") == frozenset({"IN", "NP", "US"})
    # a set, not a label: an article about two countries belongs to both
    assert len(f({"eventCountries": ["IN", "PK"]}, "event")) == 2


def test_demonyms_match_but_not_inside_known_non_country_phrases():
    """Demonyms carry real supply ("Indian markets" is India news), and each guard below is a
    phrase where the demonym does NOT denote its country. The table is curated, never derived by
    suffix rule — "Turkey"->"Turkish" and "Netherlands"->"Dutch" share no rule, and a guessed
    demonym is a silent mis-label."""
    m = feed_source.mentioned_countries
    assert m("British PM faces confidence vote") == frozenset({"GB"})
    assert m("Turkish lira hits record low") == frozenset({"TR"})
    assert m("Polish parliament passes the bill") == frozenset({"PL"})
    assert m("South Korean chipmakers gain") == frozenset({"KR"})     # the compound disambiguates
    assert m("North Korean missile test") == frozenset({"KP"})

    for blocked in ("African American voters shift", "nail polish sales climb",
                    "French fries price war", "Indian Ocean shipping lanes",
                    "German shepherd rescued from lake", "Dutch oven recipes",
                    "Turkish delight exports"):
        assert m(blocked) == frozenset(), blocked

    # bare "Korean" and "English" are deliberately absent: one cannot choose between KR and KP,
    # the other reads as the language far more often than the country
    assert m("Korean drama series") == frozenset()
    assert m("English language exams") == frozenset()
