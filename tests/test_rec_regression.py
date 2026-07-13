"""The Recommendation Regression Suite (S3) — the evaluation engine's second client.

Protects recommendation BEHAVIOR over time through REPORT CONTRACT v1, asserting only
STRUCTURAL fields (identities, verdicts, ranks, flags, memberships) — never copy-bearing ones
(``reason``, ``explanation.message``, ``detail``, ``notes``). Assertions lean on fixture
KNOWLEDGE MAPS (canonical URL -> lean / political / read-set truth), so behavioral invariants
are checked against ground truth the report itself doesn't carry.

Coverage, deliberately orthogonal to the existing suites (test_rec_sandbox pins the evaluation
engine's own invariants; the 21d pipeline validates evidence/explanations on recorded fixture
personas; the api_server tests pin slice mechanics unit-style):

    ranking          ranks are contiguous; exclusion verdicts MIRROR feed membership
                     (recommended <=> present in the same reader/params blend; below_cutoff
                     => absent) — membership, not raw-rank arithmetic, per the engine's own
                     admission docs; the blend plan's slice cutoffs (6/4/4) are pinned as a
                     deliberate tripwire
    admission        non-political articles are ranked by rwe-b yet NEVER occupy its slice
                     (inSlice False) and never appear as rwe-b cards; seen-exclusion: a
                     reader's read URLs never appear in any of their feeds
    bridge           for an all-left reader, a served card is crossCutting IFF the fixture
                     says the article is right-political (the derivation is a pure function
                     of article lean/political + reader side)
    explanations     every served card carries a resolver explanation whose TYPE is in the
                     resolver's closed vocabulary; bridge-type explanations only on
                     crossCutting cards (type is structural; message is copy and untouched)
    story            an injected near-duplicate PAIR clusters into one story with the
                     existing coverage (same storyId for both)
    parameter sweeps paramsUsed is honest per strategy; the strength slider (beta) cannot
                     move the blend's rwe-b prefix; the openness slider (epsilon) cannot
                     move the pure rwe-d feed
    determinism      the same scenario evaluated on two INDEPENDENTLY CONSTRUCTED stores
                     (same builder, fresh files) yields byte-identical reports
    contract         a deep structural validator (enums, types, rank contiguity, canonical
                     diff keys) runs over every report this suite produces
"""
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import evidence_resolver as er                              # noqa: E402
import rec_sandbox                                          # noqa: E402
import store as store_mod                                   # noqa: E402

_ENV = {"RWE_N_USERS": "120", "RWE_MAX_ITEMS": "300"}

PUBS = ["AP", "Reuters", "NPR", "BBC News", "The Guardian", "The Hill", "Fox News", "CNN"]
STORY = [("AP", "senate budget vote reaches bipartisan deal"),
         ("CNN", "senate passes budget vote after bipartisan deal"),
         ("Fox News", "bipartisan budget deal clears senate vote")]

# fixture knowledge maps (canonical URL -> ground truth), filled by _build_store
LEAN_OF: dict = {}
POLITICAL: set = set()
SPORTS: set = set()
READ_URLS: set = set()

PROBE_RIGHT = {"url": "https://foxnews.com/politics/regression-right-probe",
               "title": "governor signs sweeping election overhaul into law"}
PROBE_SIBLING = {"url": "https://apnews.com/article/regression-story-sibling",
                 "title": "senate budget vote bipartisan deal coverage expands"}
DUP_A = {"url": "https://reuters.com/world/regression-dup-a",
         "title": "wildfire evacuation orders expand across northern county"}
DUP_B = {"url": "https://bbc.com/news/regression-dup-b",
         "title": "evacuation orders expand as northern county wildfire grows"}


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


# One publishedAt per injected article, fixed at import so every scenario (and the
# two independently-built stores of the reproducibility test) sees identical specs.
STAMPS = {"probe": _iso(0.05), "sibling": _iso(0.3), "dup": _iso(0.25)}


def _build_store(path) -> "tuple[store_mod.Store, int]":
    """The regression corpus: 60 political articles (known lean cycle), 12 sports articles
    (political=False — the rwe-b admission foil), a 3-publisher story cluster, and one
    all-left reader whose reads include a cluster member. Deterministic by construction."""
    st = store_mod.Store(f"sqlite:///{path}")

    def _put(url, pub, title, lean, political, category, days):
        st.upsert_feed_article(
            canonical_url=er._canon(url), url=url, publisher=pub, source_publisher=pub,
            title=title, description="d", body=None, published_at=_iso(days), source_feed="f",
            scored={"article_id": er._canon(url), "outlet": pub, "category": category,
                    "lean": lean, "political": political, "title": title})
        LEAN_OF[er._canon(url)] = lean
        (POLITICAL if political else SPORTS).add(er._canon(url))

    for k in range(60):
        pub = PUBS[k % 8]
        _put(f"https://{pub.split()[0].lower()}{k % 8}.example.com/reg/{k}", pub,
             f"reg{k}a reg{k}b reg{k}c reg{k}d", (-1.0, 0.0, 1.0)[k % 3], True, "Politics",
             0.5 + (k % 6) * 0.4)
    for k in range(60, 72):
        pub = PUBS[k % 8]
        _put(f"https://{pub.split()[0].lower()}{k % 8}.example.com/reg/{k}", pub,
             f"reg{k}a reg{k}b reg{k}c reg{k}d", (-1.0, 0.0, 1.0)[k % 3], False, "Sports",
             0.5 + (k % 6) * 0.4)
    for i, (pub, title) in enumerate(STORY):
        _put(f"https://{pub.split()[0].lower()}.example.com/story/{i}", pub, title,
             (-1.0, 0.0, 1.0)[i % 3], True, "Politics", 1.0 + 0.1 * i)

    uid = st.upsert_user_by_identity("dev", "regression-reader", display_name="Reg").id
    reads = [f"https://ap0.example.com/reg/{k}" for k in (0, 8, 16, 24, 32, 40)]
    reads.append("https://ap.example.com/story/0")           # a story-cluster member
    for i, url in enumerate(reads):
        st.add_read(uid, er._canon(url),
                    {"article_id": er._canon(url), "outlet": "AP", "category": "Politics",
                     "lean": -1.0, "political": True, "title": f"read{i}"},
                    _iso(0.2 + i * 0.05), read_source="test")
        READ_URLS.add(er._canon(url))
    return st, uid


@pytest.fixture(scope="module", autouse=True)
def _sized_population():
    import os
    old = {k: os.environ.get(k) for k in _ENV}
    os.environ.update(_ENV)
    yield
    for k, v in old.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    st, uid = _build_store(tmp_path_factory.mktemp("reg") / "reg.db")
    return st, uid


def _canonical_spec(uid: int) -> dict:
    return {"inject": [dict(PROBE_RIGHT, publishedAt=STAMPS["probe"]),
                       dict(PROBE_SIBLING, publishedAt=STAMPS["sibling"])],
            "ask": ["https://ap0.example.com/reg/0",          # a READ article
                    "https://reuters1.example.com/reg/65",    # a sports article (k=65: 65%8=1)
                    "https://fox6.example.com/reg/14"],       # right political (k=14: 14%3=2)
            "readers": [{"kind": "user", "id": uid}],
            "strategies": [None, "rwe-d"],
            "params": [None]}


@pytest.fixture(scope="module")
def canonical(stack):
    st, uid = stack
    return rec_sandbox.evaluate(st, _canonical_spec(uid))


@pytest.fixture(scope="module")
def sweep(stack):
    st, uid = stack
    return rec_sandbox.evaluate(st, {
        "inject": [dict(PROBE_RIGHT, publishedAt=STAMPS["probe"])],
        "readers": [{"kind": "user", "id": uid}],
        "strategies": [None, "rwe-d"],
        "params": [None, {"beta": 0.3}, {"beta": 0.8}, {"epsilon": 0.7}],
        "questions": ["feed", "exclusion"]})


@pytest.fixture(scope="module")
def duplicates(stack):
    st, _uid = stack
    return rec_sandbox.evaluate(st, {
        "inject": [dict(DUP_A, publishedAt=STAMPS["dup"]),
                   dict(DUP_B, publishedAt=STAMPS["dup"])],
        "readers": [{"kind": "demo"}], "questions": ["story"]})


def _feed(report, *, strategy, params):
    return next(f for f in report["feeds"]
                if f["strategy"] == strategy and f["params"] == params)


def _key(card) -> str:
    return er._canon(str(card.get("url") or card.get("id") or ""))


# --------------------------------------------------------------------------- #
# Deep contract validation over EVERY report this suite produces.
# --------------------------------------------------------------------------- #
_VERDICTS = {"recommended", "seen_excluded", "below_cutoff", "not_in_graph", "not_in_catalog"}
_DISPOSITIONS = {"evaluated", "already_in_candidate", "dropped_freshness"}


def _check_contract(report):
    assert report["reportVersion"] == 1
    for e in report["injected"]:
        assert e["disposition"] in _DISPOSITIONS
        assert e["canonicalUrl"]
        for x in e["exclusions"]:
            if x["status"] == "ok":
                assert x["verdict"] in _VERDICTS
    for x in report["asked"]:
        if x["status"] == "ok":
            assert x["verdict"] in _VERDICTS
    for f in report["feeds"]:
        assert [c["rank"] for c in f["served"]] == list(range(1, len(f["served"]) + 1))
        for c in f["served"]:
            assert isinstance(c["crossCutting"], bool)
            assert c["strategy"] in {"rwe-b", "rwe-d", "adaptive", "story"}
    for d in (report.get("diff") or {}).get("perFeed", []):
        for k in d["entered"] + d["left"] + [m["key"] for m in d["moved"]]:
            assert not k.startswith("Q")
    json.dumps(report, allow_nan=False)


def test_every_scenario_report_satisfies_the_contract(canonical, sweep, duplicates):
    for report in (canonical, sweep, duplicates):
        _check_contract(report)


# --------------------------------------------------------------------------- #
# Ranking + verdict membership semantics.
# --------------------------------------------------------------------------- #
def test_verdicts_mirror_feed_membership(canonical):
    """The exclusion verdict and the served feed can never disagree: recommended <=> the
    article is in the blend feed the same reader/params produced; below_cutoff => absent.
    (Raw per-strategy ranks are NOT slot arithmetic — admission can skip ranks — so
    membership is the truthful cross-check.)"""
    blend_keys = {_key(c) for c in _feed(canonical, strategy=None, params=None)["served"]}
    checked = 0
    pairs = [(e["canonicalUrl"], x) for e in canonical["injected"] for x in e["exclusions"]]
    pairs += [(er._canon(x["article"]), x) for x in canonical["asked"]]
    for canon, x in pairs:
        if x["status"] != "ok":
            continue
        if x["verdict"] == "recommended":
            assert canon in blend_keys
        if x["verdict"] in ("below_cutoff", "seen_excluded", "not_in_graph"):
            assert canon not in blend_keys
        checked += 1
    assert checked >= 4


def test_blend_slices_are_served_in_plan_order(canonical):
    """The blend concatenates strategy slices in plan order — strategies never interleave."""
    blend = _feed(canonical, strategy=None, params=None)
    order = {"rwe-b": 0, "rwe-d": 1, "adaptive": 2, "story": 3}
    stages = [order[c["strategy"]] for c in blend["served"]]
    assert stages == sorted(stages), f"interleaved plan stages: {stages}"


# --------------------------------------------------------------------------- #
# Admission + seen-exclusion.
# --------------------------------------------------------------------------- #
def test_non_political_articles_never_occupy_the_rwe_b_slice(canonical):
    for f in canonical["feeds"]:
        for c in f["served"]:
            if c["strategy"] == "rwe-b":
                assert _key(c) not in SPORTS, f"sports article served as rwe-b: {c['url']}"
    # non-vacuity + the structural distinction: a sports article IS a ranked graph node for
    # every strategy (byStrategy ranks exist) — exclusion happens at slice admission, which
    # the card loop above proves. The cutoffs pin the blend plan itself (6/4/4): a plan
    # change is a behavior change and SHOULD trip this suite.
    sports_ask = next(x for x in canonical["asked"] if "/reg/65" in x["article"])
    assert sports_ask["status"] == "ok" and sports_ask["verdict"] == "below_cutoff"
    bs = sports_ask["byStrategy"]
    assert all(v["rank"] is not None for v in bs.values())
    assert {s: v["cutoff"] for s, v in bs.items()} == {"rwe-b": 6, "rwe-d": 4, "adaptive": 4}


def test_a_readers_own_reads_are_never_served_back(canonical):
    for f in canonical["feeds"]:
        for c in f["served"]:
            assert _key(c) not in READ_URLS, f"read article re-served: {c['url']}"
    read_ask = next(x for x in canonical["asked"] if "/reg/0" in x["article"])
    assert read_ask["verdict"] == "seen_excluded"


# --------------------------------------------------------------------------- #
# Bridge (cross-cutting) eligibility.
# --------------------------------------------------------------------------- #
def test_cross_cutting_is_exactly_right_political_for_an_all_left_reader(canonical):
    """crossCutting is a pure function of (article lean/political, reader side): for this
    all-left reader a served CATALOG card is crossCutting IFF the fixture knows the article
    as right-political (|lean| >= 0.5, opposite sign, political)."""
    checked = 0
    for f in canonical["feeds"]:
        for c in f["served"]:
            k = _key(c)
            if k not in LEAN_OF:
                continue                                    # injected probes checked below
            expected = k in POLITICAL and LEAN_OF[k] >= 0.5
            assert c["crossCutting"] == expected, (c["url"], LEAN_OF[k])
            checked += 1
    assert checked >= 12


def test_a_served_right_political_injection_presents_as_cross_cutting(canonical):
    probe_canon = er._canon(PROBE_RIGHT["url"])              # Fox News: registry lean +2.0
    served = [c for f in canonical["feeds"] for c in f["served"] if _key(c) == probe_canon]
    for c in served:
        assert c["crossCutting"] is True
    e = next(e for e in canonical["injected"] if e["canonicalUrl"] == probe_canon)
    assert e["graphNode"] is True and e["scored"]["lean"] is not None


# --------------------------------------------------------------------------- #
# Explanations (structural: type vocabulary + type/evidence relations; never message).
# --------------------------------------------------------------------------- #
def test_every_served_card_carries_a_resolver_explanation_type(canonical):
    for f in canonical["feeds"]:
        for c in f["served"]:
            assert c.get("explanation"), f"card without explanation: {c['url']}"
            assert c["explanation"]["type"] in er.TYPES


def test_bridge_explanations_only_on_cross_cutting_cards(canonical):
    for f in canonical["feeds"]:
        for c in f["served"]:
            if c["explanation"]["type"] == "bridge":
                assert c["crossCutting"] is True


# --------------------------------------------------------------------------- #
# Story membership.
# --------------------------------------------------------------------------- #
def test_injected_sibling_joins_the_existing_story_cluster(canonical):
    e = next(e for e in canonical["injected"]
             if e["canonicalUrl"] == er._canon(PROBE_SIBLING["url"]))
    assert e["story"]["matched"] is True
    assert e["story"]["publisherCount"] >= 3


def test_injected_near_duplicates_cluster_into_one_story(duplicates):
    a, b = duplicates["injected"]
    assert a["story"]["matched"] and b["story"]["matched"]
    assert a["story"]["storyId"] == b["story"]["storyId"]


# --------------------------------------------------------------------------- #
# Parameter sweeps (slider semantics at the contract boundary).
# --------------------------------------------------------------------------- #
def test_params_used_reports_the_exact_hyperparameters_in_effect(sweep):
    by_params = {json.dumps(x["params"], sort_keys=True): x["paramsUsed"]
                 for e in sweep["injected"] for x in e["exclusions"] if x["status"] == "ok"}
    assert by_params[json.dumps({"beta": 0.3})]["rwe-d"]["beta"] == 0.3
    assert by_params[json.dumps({"beta": 0.8})]["rwe-d"]["beta"] == 0.8
    assert by_params[json.dumps({"epsilon": 0.7})]["rwe-b"]["epsilon"] == 0.7


def test_strength_slider_cannot_move_the_blends_rwe_b_prefix(sweep):
    """beta parameterizes RWE-D only: the blend's leading rwe-b slice must be identical
    across the whole beta sweep (the openness slider is the one that owns it)."""
    def prefix(params):
        f = _feed(sweep, strategy=None, params=params)
        out = []
        for c in f["served"]:
            if c["strategy"] != "rwe-b":
                break
            out.append(_key(c))
        return out
    assert prefix({"beta": 0.3}) == prefix({"beta": 0.8}) == prefix(None)
    assert len(prefix(None)) >= 4                            # non-vacuous: a real slice


def test_openness_slider_cannot_move_the_pure_rwe_d_feed(sweep):
    """epsilon parameterizes RWE-B only: an explicit rwe-d feed is invariant under it."""
    base = [_key(c) for c in _feed(sweep, strategy="rwe-d", params=None)["served"]]
    eps = [_key(c) for c in _feed(sweep, strategy="rwe-d", params={"epsilon": 0.7})["served"]]
    assert base == eps and len(base) >= 8


# --------------------------------------------------------------------------- #
# Reproducibility: the scenario is a pure function of the fixture RECIPE.
# --------------------------------------------------------------------------- #
def test_identical_reports_from_two_independently_built_stores(stack, tmp_path, canonical):
    knowledge = (dict(LEAN_OF), set(POLITICAL), set(SPORTS), set(READ_URLS))
    try:
        st2, uid2 = _build_store(tmp_path / "rebuild.db")
        report2 = rec_sandbox.evaluate(st2, _canonical_spec(uid2))
    finally:                                                 # rebuilding mutates the maps
        LEAN_OF.clear(); LEAN_OF.update(knowledge[0])
        POLITICAL.clear(); POLITICAL.update(knowledge[1])
        SPORTS.clear(); SPORTS.update(knowledge[2])
        READ_URLS.clear(); READ_URLS.update(knowledge[3])
    assert json.dumps(report2, sort_keys=True) == json.dumps(canonical, sort_keys=True)
