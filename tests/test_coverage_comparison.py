"""Tests for examples/coverage_comparison.py — Coverage Comparison, tier L0.

The properties that matter are the honesty ones, so they are tested first and hardest: the module
refuses to answer in every case the design says it must (§7), it never claims a content-level
omission (§5.1 — no text is examined at L0), support is counted in publisher IDENTITIES so
syndication cannot manufacture corroboration (§6), every finding carries the evidence its count
came from (§4), and the same inputs always produce byte-identical output (§3).
"""

import copy
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import coverage_comparison as cc   # noqa: E402


def member(pub, headline, *, url=None, bucket="center", published="2026-08-02T09:00:00Z",
           register="reporting", lang=None):
    m = {"publisher": pub, "headline": headline, "url": url or f"https://{pub}.example/x",
         "leanBucket": bucket, "lean": 0.0, "register": register, "emotion": None,
         "publishedAt": published}
    if lang:
        m["language"] = lang
    return m


def story(members, **kw):
    s = {"id": "st_1", "coverage": members, "totalCoverage": len(members),
         "publisherCount": len({m["publisher"] for m in members}),
         "clusterTrust": "ok", "countries": [], "missingViewpoints": []}
    s.update(kw)
    return s


CLUSTER = [
    member("Harbour Gazette", "Council approves the harbour redevelopment",
           published="2026-08-02T08:00:00Z"),
    member("Meridian Wire", "Councillors back harbour plan after long hearing",
           published="2026-08-02T09:00:00Z", bucket="left"),
    member("Ledger Daily", "Harbour redevelopment cleared by council",
           published="2026-08-02T10:00:00Z"),
    member("City Chronicle", "Harbour plan approved 7-2",
           published="2026-08-02T11:00:00Z"),
]
TARGET = {"publisher": "Harbour Gazette", "url": "https://Harbour Gazette.example/x",
          "leanBucket": "center", "register": "reporting"}


# ------------------------------------------------------------------ #
# §7 — refusing to answer
# ------------------------------------------------------------------ #

def test_untrusted_cluster_renders_nothing():
    out = cc.compare(TARGET, story(CLUSTER, clusterTrust="low"))
    assert out["available"] is False and out["reason"] == "cluster_untrusted"


def test_too_few_publishers_renders_nothing():
    two = [member("A Times", "x one"), member("B Post", "x two")]
    out = cc.compare({"publisher": "A Times", "url": two[0]["url"]}, story(two))
    assert out["available"] is False and out["reason"] == "too_few_publishers"


@pytest.mark.parametrize("headline", [
    "Edward Taylor Obituary (2026) - Ava, MO - Craig-Hurtt Funeral Home",
    "Brand New Day Box Office Collection Day 2",
    "Polymarket promo code NYPMAX1: deposit and get a bonus",
    "Mega Millions jackpot winning numbers for Tuesday",
])
def test_template_genre_clusters_render_nothing(headline):
    mill = [member(f"Outlet {i}", headline, url=f"https://o{i}.example/x") for i in range(4)]
    out = cc.compare({"publisher": "Outlet 0", "url": mill[0]["url"]}, story(mill))
    assert out["available"] is False and out["reason"] == "template_genre"


def test_one_template_member_does_not_suppress_a_real_story():
    mixed = CLUSTER + [member("Punt Daily", "Best bets and odds for the council vote")]
    out = cc.compare(TARGET, story(mixed))
    assert out["available"] is True          # a minority template member is not the genre


def test_cross_language_target_renders_nothing_when_members_carry_language():
    multi = [member("Le Monde", "Le conseil approuve le projet", lang="fr"),
             member("Meridian Wire", "Council approves plan", lang="en"),
             member("Ledger Daily", "Council backs plan", lang="en"),
             member("City Chronicle", "Plan approved", lang="en")]
    out = cc.compare({"publisher": "Le Monde", "url": multi[0]["url"], "language": "fr"},
                     story(multi))
    assert out["available"] is False and out["reason"] == "cross_language"


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("RWE_COVERAGE_COMPARISON", "0")
    assert cc.compare(TARGET, story(CLUSTER))["reason"] == "disabled"


# ------------------------------------------------------------------ #
# §5.1 / §9.1 — what L0 must never claim
# ------------------------------------------------------------------ #

def test_l0_makes_no_text_claims_at_all():
    out = cc.compare(TARGET, story(CLUSTER))
    assert out["textClaims"] is False and out["textParity"] is None
    assert out["tier"] == "L0"
    # nothing in the payload may be a content-level omission finding
    kinds = {f["kind"] for f in out["reportedElsewhere"] + out["uniqueHere"]}
    assert kinds <= {"outlets", "geography", "register", "timing", "viewpoint", "language"}
    assert not (kinds & {"term", "figure", "entity", "quote-voice"})


def test_a_missing_event_location_is_reported_as_not_comparable_never_as_omission():
    s = story(CLUSTER, countries=["JP", "US"])
    out = cc.compare(TARGET, s, target_countries=[])          # article has no located rows
    geo = [f for f in out["reportedElsewhere"] if f["kind"] == "geography"]
    assert geo and geo[0]["key"] == "event_countries_unknown"
    assert geo[0]["support"] == 0                              # nothing is asserted against it
    assert "no extracted event location" in geo[0]["label"]


# ------------------------------------------------------------------ #
# The shape the PRODUCT actually carries (regression: a wrong assumption here shipped a
# ValueError to production, and the analyzer's catch-all made the failure silent — the card
# simply never rendered on any clustered article whose members carry a register signal)
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("value, expected", [
    ("reporting", "reporting"), ("opinion", "opinion"), ("mixed", "mixed"),   # the product enum
    (0.9, "reporting"), (0.2, "opinion"), (0.5, "mixed"),                     # raw P(reporting)
    (None, None), ("nonsense", None), (float("nan"), None),                   # no signal
])
def test_register_is_read_through_the_products_own_bucketing(value, expected):
    assert cc._register_of(value) is expected or cc._register_of(value) == expected


def test_a_string_register_never_raises_and_still_compares():
    """The exact production shape: story coverage carries the ENUM, not a float."""
    enum_cluster = [member(m["publisher"], m["headline"], url=m["url"], bucket=m["leanBucket"],
                           published=m["publishedAt"], register="reporting") for m in CLUSTER]
    out = cc.compare(TARGET, story(enum_cluster))
    assert out["available"] is True


def test_an_opinion_piece_among_reporting_is_placed_as_context():
    others = [member(f"Outlet {i}", f"Council approves the plan number {i}",
                     url=f"https://o{i}.example/x", register="reporting",
                     published=f"2026-08-02T{9 + i:02d}:00:00Z") for i in range(3)]
    mine = member("Comment Desk", "Why the council was right to approve the plan",
                  url="https://comment.example/x", register="opinion",
                  published="2026-08-02T08:00:00Z")
    out = cc.compare({"publisher": "Comment Desk", "url": mine["url"], "leanBucket": "center",
                      "register": "opinion"}, story([mine] + others))
    reg = [f for f in out["reportedElsewhere"] if f["kind"] == "register"]
    assert reg and reg[0]["key"] == "mostly_reporting" and reg[0]["support"] == 3


# ------------------------------------------------------------------ #
# §6 — counted evidence
# ------------------------------------------------------------------ #

def test_support_counts_outlets_not_name_forms():
    """A masthead arriving in two name forms is ONE outlet; three name forms of one syndicator
    must not look like broad corroboration."""
    synd = [member("Sportskeeda", "Council approves plan", url="https://s1.example/x"),
            member("Sportskeeda.Com", "Council approves plan", url="https://s2.example/x"),
            member("Meridian Wire", "Council backs plan", url="https://m.example/x"),
            member("Ledger Daily", "Council clears plan", url="https://l.example/x")]
    out = cc.compare({"publisher": "Meridian Wire", "url": "https://m.example/x"}, story(synd))
    assert out["available"] is True
    assert out["outlets"] == 3                                  # not 4
    other = next(f for f in out["reportedElsewhere"] if f["kind"] == "outlets")
    assert other["support"] == 2                                # Sportskeeda counted once


def test_every_finding_carries_its_evidence():
    out = cc.compare(TARGET, story(CLUSTER, countries=["GB"]))
    for f in out["reportedElsewhere"] + out["uniqueHere"]:
        assert "support" in f and "of" in f and "coverageShare" in f
        assert f["confidence"] == "high"                        # L0: counts only (§5.1)
        for e in f["evidence"]:
            assert e["publisher"] and e["url"]                  # openable by the reader


def test_missing_viewpoints_is_reused_not_recomputed():
    s = story(CLUSTER, missingViewpoints=["right"])
    assert cc.compare(TARGET, s)["missingViewpoints"] == ["right"]


# ------------------------------------------------------------------ #
# §5.5 — balance: what only this article brings
# ------------------------------------------------------------------ #

def test_first_report_is_credited():
    out = cc.compare(TARGET, story(CLUSTER))
    assert out["timing"]["isFirstReport"] is True and out["timing"]["position"] == 1
    assert any(f["key"] == "first_report" for f in out["uniqueHere"])


def test_a_tie_at_the_earliest_timestamp_credits_nobody():
    """Feeds batch-stamp whole sets (and some stamp poll time, not publication time). Crediting
    every member as 'first report' would be a false claim about each of them."""
    same = [member(f"Outlet {i}", f"Council approves the plan number {i}",
                   url=f"https://o{i}.example/x", published="2026-08-02T09:00:00Z")
            for i in range(4)]
    out = cc.compare({"publisher": "Outlet 0", "url": same[0]["url"]}, story(same))
    assert out["timing"]["isFirstReport"] is False
    assert out["timing"]["tiedAtFirst"] is True
    assert not any(f["key"] == "first_report" for f in out["uniqueHere"])


def test_a_later_report_is_placed_not_blamed():
    target = {"publisher": "City Chronicle", "url": CLUSTER[3]["url"], "leanBucket": "center"}
    out = cc.compare(target, story(CLUSTER))
    assert out["timing"]["position"] == 4 and out["timing"]["hoursAfterFirst"] == 3.0
    assert out["timing"]["firstBy"] == "Harbour Gazette"
    assert not any("omit" in json.dumps(f).lower() for f in out["reportedElsewhere"])


def test_the_only_outlet_of_its_viewpoint_is_credited():
    target = {"publisher": "Meridian Wire", "url": CLUSTER[1]["url"], "leanBucket": "left"}
    out = cc.compare(target, story(CLUSTER))
    assert any(f["key"] == "only_left" and f["kind"] == "viewpoint" for f in out["uniqueHere"])


def test_event_locations_only_this_article_records():
    s = story(CLUSTER, countries=["GB"])
    out = cc.compare(TARGET, s, target_countries=["GB", "IE"])
    uniq = [f for f in out["uniqueHere"] if f["kind"] == "geography"]
    assert uniq and uniq[0]["countries"] == ["IE"]


# ------------------------------------------------------------------ #
# §3 — determinism
# ------------------------------------------------------------------ #

def test_identical_inputs_give_byte_identical_output():
    s = story(CLUSTER, countries=["GB"], missingViewpoints=["right"])
    a = cc.compare(TARGET, copy.deepcopy(s), target_countries=["GB"])
    b = cc.compare(TARGET, copy.deepcopy(s), target_countries=["GB"])
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["algoVersion"] == cc.ALGO_VERSION


def test_member_order_does_not_change_the_findings():
    s1 = story(CLUSTER)
    s2 = story(list(reversed(CLUSTER)))
    a, b = cc.compare(TARGET, s1), cc.compare(TARGET, s2)
    assert a["outlets"] == b["outlets"] and a["timing"] == b["timing"]
    assert {f["key"] for f in a["reportedElsewhere"]} == {f["key"] for f in b["reportedElsewhere"]}


# ------------------------------------------------------------------ #
# Integration: through the real analyzer and the real /api/analyze
# ------------------------------------------------------------------ #

HEADLINES = [
    ("Harbour Gazette", "Council approves the harbour redevelopment after a long hearing"),
    ("Meridian Wire", "Council approves harbour redevelopment following lengthy hearing"),
    ("Ledger Daily", "Council approves the harbour redevelopment plan after hearing"),
    ("City Chronicle", "Council approves harbour redevelopment at a long council hearing"),
]


@pytest.fixture(scope="module")
def analyze_client(tmp_path_factory):
    import importlib.util
    import os
    import store as store_mod
    tmp = tmp_path_factory.mktemp("covcmp")
    os.environ.update({"RWE_DB_URL": f"sqlite:///{tmp}/cc.db", "RWE_RECS_SOURCE": "feed",
                       "RWE_FEED_MIN_ARTICLES": "4", "RWE_CORPUS_MIN_ARTICLES": "4",
                       "RWE_SEED": "0", "RWE_STORY_SLOT": "0"})
    os.environ.pop("RWE_INTERNAL_SECRET", None)
    os.environ.pop("RWE_COVERAGE_COMPARISON", None)
    st = store_mod.Store(os.environ["RWE_DB_URL"])
    desc = "Councillors voted seven to two on Tuesday evening to approve the plan. " * 3
    for i, (pub, headline) in enumerate(HEADLINES):
        url = f"https://{pub.replace(' ', '').lower()}.example.com/harbour/{i}"
        st.upsert_feed_article(
            canonical_url=url, url=url, publisher=pub, source_publisher=None, title=headline,
            description=desc, body=None, published_at=f"2026-08-02T{8 + i:02d}:00:00Z",
            source_feed="f",
            scored={"article_id": url, "outlet": pub, "category": "Politics",
                    "lean": [0.0, -1.0, 0.0, 0.0][i], "political": True, "title": headline})
    # a second, unrelated 2-publisher story: below the publisher floor, must render nothing
    for i, pub in enumerate(["Solo Post", "Duo News"]):
        url = f"https://{pub.replace(' ', '').lower()}.example.com/ferry/{i}"
        st.upsert_feed_article(
            canonical_url=url, url=url, publisher=pub, source_publisher=None,
            title="Ferry terminal refurbishment contract awarded to local firm",
            description="The ferry terminal refurbishment contract was awarded on Monday. " * 3,
            body=None, published_at="2026-08-02T12:00:00Z", source_feed="f",
            scored={"article_id": url, "outlet": pub, "category": "Politics", "lean": 0.0,
                    "political": True, "title": "ferry"})
    spec = importlib.util.spec_from_file_location("api_covcmp", ROOT / "examples" / "api_fastapi.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["api_covcmp"] = mod
    spec.loader.exec_module(mod)
    from fastapi.testclient import TestClient
    with TestClient(mod.app) as client:
        yield client, st


def test_analyze_serves_a_counted_comparison_for_a_clustered_article(analyze_client):
    client, _ = analyze_client
    body = client.post("/api/analyze",
                       json={"url": "https://harbourgazette.example.com/harbour/0"}).json()
    assert body["status"] == "analyzed" and body["story"]["matched"] is True
    comp = body["coverageComparison"]
    assert comp["available"] is True and comp["tier"] == "L0"
    assert comp["outlets"] >= 3 and comp["textClaims"] is False
    outlets = next(f for f in comp["reportedElsewhere"] if f["kind"] == "outlets")
    assert outlets["support"] == comp["outlets"] - 1
    assert all(e["publisher"] and e["url"] for e in outlets["evidence"])
    assert comp["timing"]["isFirstReport"] is True          # published first, 08:00
    assert any(f["key"] == "first_report" for f in comp["uniqueHere"])


def test_analyze_refuses_below_the_publisher_floor(analyze_client):
    client, _ = analyze_client
    body = client.post("/api/analyze",
                       json={"url": "https://solopost.example.com/ferry/0"}).json()
    comp = body["coverageComparison"]
    # either not clustered at all, or clustered but gated — never a comparison
    assert comp is None or comp["available"] is False


def test_a_non_catalog_url_gets_no_comparison(analyze_client):
    client, _ = analyze_client
    body = client.post("/api/analyze", json={"url": "https://nowhere.example.com/unknown/9"}).json()
    assert body["coverageComparison"] is None


def test_the_kill_switch_reaches_the_served_payload(analyze_client, monkeypatch):
    client, _ = analyze_client
    monkeypatch.setenv("RWE_COVERAGE_COMPARISON", "0")
    body = client.post("/api/analyze",
                       json={"url": "https://harbourgazette.example.com/harbour/0"}).json()
    assert body["coverageComparison"]["available"] is False
    assert body["coverageComparison"]["reason"] == "disabled"
