"""Per-tier catalog retention — M2 of docs/SCALE_ROADMAP.md.

`RWE_RETENTION_MAX_COUNT` is a **count**, and a count cap is an age cap whose length nobody chose:
150,000 rows is ~32 days at today's rate, one day at 150k/day, seven hours at 500k/day. Since ① is
contractually responsible for being *complete and findable*, the same unchanged setting quietly
turns the searchable archive into hours as coverage grows. Age is the shape that does not move.

Retention is the one path in this system that destroys data, so these tests are weighted toward the
two questions that matter more than the feature: **does the default still delete exactly what it
deleted before**, and **can a per-tier rule slip past the fast path that exists to skip the
planner**.
"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import corpus_health              # noqa: E402
import retention_policy           # noqa: E402
import store as store_mod         # noqa: E402

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("RWE_CORPUS_TIER_B", "RWE_CORPUS_SHADOW",
              "RWE_RETENTION_MAX_AGE_DAYS", "RWE_RETENTION_MAX_COUNT",
              "RWE_RETENTION_MAX_AGE_DAYS_TIER_B", "RWE_RETENTION_MAX_AGE_DAYS_SHADOW"):
        monkeypatch.delenv(k, raising=False)


def _no_floors(**overrides):
    """Real thresholds with every floor released, so these tests measure the AGE rule rather than
    the repair pass. Built from `thresholds_from_env` rather than hand-rolled: a literal dict that
    is missing a key the planner reads fails as a KeyError, and one that is missing a key the
    planner reads *conditionally* would pass today and fail on the next floor added."""
    th = corpus_health.thresholds_from_env()
    th.update({"minArticles": 0, "minPublishers": 0, "minPerBucket": 0, "minFresh": 0,
               "freshMaxAgeDays": 3})
    th.update(overrides)
    return th


def _art(url, publisher, age_days, lean=0.0):
    return {"canonicalUrl": url, "url": url, "publisher": publisher,
            "publishedAt": (NOW - timedelta(days=age_days)).isoformat(),
            "title": f"headline {url}", "description": "ctx",
            "scored": {"outlet": publisher, "lean": lean, "category": "Politics"}}


def _seed_store(st, arts):
    for a in arts:
        st.upsert_feed_article(
            canonical_url=a["canonicalUrl"], url=a["url"], publisher=a["publisher"],
            source_publisher=a["publisher"], title=a["title"], description="ctx", body=None,
            published_at=a["publishedAt"], source_feed="feed://x", scored=a["scored"])


# --------------------------------------------------------------------------- #
# The default must not move. This is the assertion that matters most.
# --------------------------------------------------------------------------- #
def test_with_no_per_tier_age_the_resolver_is_none():
    """`None` is not an optimization — it is what makes `plan_retention` apply the scalar to every
    article exactly as it did before this existed. A resolver that returned the scalar per-article
    would be equivalent in output and would still have changed the code path a deletion runs
    through, for a deployment that configured nothing."""
    assert corpus_health._tier_age_resolver(retention_policy.load()) is None


def test_a_per_tier_age_does_not_widen_the_global_policy(monkeypatch):
    """Configuring a Tier B age must not change what Tier A loses. The tiers are separate rules,
    not a multiplier on one."""
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "tierb.example")
    monkeypatch.setenv("RWE_RETENTION_MAX_AGE_DAYS_TIER_B", "2")
    arts = [_art("https://npr.org/old", "NPR", 30),
            _art("https://npr.org/new", "NPR", 1)]
    policy = retention_policy.load()
    plan = corpus_health.plan_retention(
        arts, thresholds=_no_floors(), now=NOW,
        age_days_for=corpus_health._tier_age_resolver(policy))
    assert plan["prune"] == [], "a Tier B rule pruned a Tier A article"


def test_a_tier_b_age_prunes_only_tier_b(monkeypatch):
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "tierb.example")
    monkeypatch.setenv("RWE_RETENTION_MAX_AGE_DAYS_TIER_B", "2")
    arts = [_art("https://npr.org/a", "NPR", 30),
            _art("https://tierb.example/x", "tierb.example", 30),
            _art("https://tierb.example/y", "tierb.example", 1)]
    plan = corpus_health.plan_retention(
        arts, thresholds=_no_floors(), now=NOW,
        age_days_for=corpus_health._tier_age_resolver(retention_policy.load()))
    assert plan["prune"] == ["https://tierb.example/x"]


# --------------------------------------------------------------------------- #
# The fast path — the correctness-critical guard
# --------------------------------------------------------------------------- #
def test_a_tier_age_is_not_swallowed_by_the_count_only_fast_path(monkeypatch):
    """`run_retention` skips the whole planner when a COUNT-only policy is under its cap — a
    measured 3,433-4,543 ms saved per run. That gate keys on "no age policy", and a per-tier age is
    an age policy: it can have prunable rows at ANY catalog size.

    The comment at that gate calls it "the whole forward-compatibility contract". This is the first
    time the contract is cashed in, and this test is what proves it was honoured rather than
    assumed — it fails against a gate that only checks the scalar."""
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "tierb.example")
    monkeypatch.setenv("RWE_RETENTION_MAX_COUNT", "100000")       # far above the catalog
    monkeypatch.setenv("RWE_RETENTION_MAX_AGE_DAYS_TIER_B", "2")

    st = store_mod.Store("sqlite://")
    _seed_store(st, [_art("https://npr.org/a", "NPR", 1),
                     _art("https://tierb.example/x", "tierb.example", 30)])
    out = corpus_health.run_retention(st, thresholds=_no_floors(),
                                      log=lambda *a, **k: None, now=NOW)

    assert out.get("skipped") != "under_count_cap", (
        "the count-only fast path swallowed a per-tier AGE policy — it can prune under the cap")
    assert out["pruned"] == 1
    assert {r["canonicalUrl"] for r in st.list_feed_articles(limit=100)} == {"https://npr.org/a"}


def test_a_count_only_policy_still_takes_the_fast_path(monkeypatch):
    """The other direction: the optimization must survive. A count-only policy under its cap still
    skips the planner, which is 96-98% of the cleanup pass."""
    monkeypatch.setenv("RWE_RETENTION_MAX_COUNT", "100000")
    st = store_mod.Store("sqlite://")
    _seed_store(st, [_art("https://npr.org/a", "NPR", 1)])
    out = corpus_health.run_retention(st, log=lambda *a, **k: None, now=NOW)
    assert out.get("skipped") == "under_count_cap"


def test_no_policy_at_all_still_reports_no_policy():
    st = store_mod.Store("sqlite://")
    _seed_store(st, [_art("https://npr.org/a", "NPR", 1)])
    assert corpus_health.run_retention(st, log=lambda *a, **k: None,
                                       now=NOW).get("skipped") == "no_policy"


# --------------------------------------------------------------------------- #
# The floors outrank every age rule, per-tier included
# --------------------------------------------------------------------------- #
def test_the_floors_still_outrank_a_per_tier_age(monkeypatch):
    """`plan_retention`'s repair pass pulls the newest pruned articles back until each floor holds,
    and it runs over the same flags whatever produced them. So a per-tier age inherits the
    guarantee rather than needing its own: retention cannot breach a floor, whatever shape the
    policy is."""
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "tierb.example")
    monkeypatch.setenv("RWE_RETENTION_MAX_AGE_DAYS_TIER_B", "1")
    arts = [_art(f"https://tierb.example/{i}", "tierb.example", 30) for i in range(5)]
    plan = corpus_health.plan_retention(
        arts, thresholds=_no_floors(minArticles=3), now=NOW,
        age_days_for=corpus_health._tier_age_resolver(retention_policy.load()))
    assert len(plan["keep"]) >= 3, "the total floor must survive a per-tier age rule"
    assert plan["retainedForFloor"] >= 3


def test_the_policy_object_answers_which_rule_a_tier_is_under(monkeypatch):
    monkeypatch.setenv("RWE_RETENTION_MAX_AGE_DAYS", "90")
    monkeypatch.setenv("RWE_RETENTION_MAX_AGE_DAYS_TIER_B", "14")
    p = retention_policy.load()
    assert p.age_days_for_tier("A") == 90
    assert p.age_days_for_tier("B") == 14
    assert p.age_days_for_tier("shadow") == 90, "an unset tier falls back to the global rule"
    assert p.any_age_policy() is True
    assert p.catalog_enabled() is True
