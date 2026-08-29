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


# --------------------------------------------------------------------------- #
# D1 stage 2 — the SQL arm. A DELETE path, so the tests lead with what it must NEVER do.
# --------------------------------------------------------------------------- #
def _store(tmp_path):
    return store_mod.Store(f"sqlite:///{tmp_path / 'ret.db'}")


def _urls(st):
    return {a["canonicalUrl"] for a in st.list_retention_rows()}


def test_the_sql_arm_never_deletes_a_row_outside_the_publisher_set(tmp_path):
    """The one property that matters. Everything else about this method is performance; this is
    correctness, and it is irreversible when wrong."""
    st = _store(tmp_path)
    _seed_store(st, [_art("https://npr.org/old", "NPR", 90),
                     _art("https://tierb.example/old", "tierb.example", 90),
                     _art("https://tierb.example/new", "tierb.example", 1)])

    n = st.prune_tier_articles_older_than({"tierb.example"}, 30, now=NOW)
    assert n == 1
    assert _urls(st) == {"https://npr.org/old", "https://tierb.example/new"}, \
        "the SQL arm deleted outside its publisher set, or missed inside it"


def test_an_empty_publisher_set_deletes_nothing(tmp_path):
    """The load-bearing default. Tier lists are empty on a stock deployment, and a predicate that
    fell through to "every row" would turn an unconfigured tier into a catalogue wipe."""
    st = _store(tmp_path)
    _seed_store(st, [_art("https://npr.org/old", "NPR", 900)])
    assert st.prune_tier_articles_older_than(frozenset(), 1, now=NOW) == 0
    assert st.prune_tier_articles_older_than({"", "   "}, 1, now=NOW) == 0
    assert st.prune_tier_articles_older_than({"npr"}, 0, now=NOW) == 0, "age 0 means keep forever"
    assert _urls(st) == {"https://npr.org/old"}


def test_a_row_with_no_publication_date_is_never_aged_out(tmp_path):
    """An age rule cannot act on a date it does not have, and guessing would delete the newest
    rows as readily as the oldest. The same fail-closed reading the crawler's age filter takes."""
    st = _store(tmp_path)
    _seed_store(st, [_art("https://tierb.example/dated", "tierb.example", 90)])
    st.upsert_feed_article(canonical_url="https://tierb.example/undated",
                           url="https://tierb.example/undated", publisher="tierb.example",
                           source_publisher="tierb.example", title="t", description="",
                           body=None, published_at=None, source_feed="feed://x", scored={})
    assert st.prune_tier_articles_older_than({"tierb.example"}, 30, now=NOW) == 1
    assert _urls(st) == {"https://tierb.example/undated"}


def test_the_batch_limit_bounds_one_pass(tmp_path):
    """`RWE_RETENTION_BATCH_LIMIT` keeps the write lock short. A pass that ignored it would hold
    the global ingest lock for the length of the backlog."""
    st = _store(tmp_path)
    _seed_store(st, [_art(f"https://tierb.example/{i}", "tierb.example", 90) for i in range(12)])
    assert st.prune_tier_articles_older_than({"tierb.example"}, 30, limit=5, now=NOW) == 5
    assert len(_urls(st)) == 7
    assert st.prune_tier_articles_older_than({"tierb.example"}, 30, limit=5, now=NOW) == 5
    assert len(_urls(st)) == 2


def test_run_retention_uses_the_sql_arm_and_leaves_tier_a_to_the_planner(tmp_path, monkeypatch):
    """End-to-end: the per-tier age is served by the indexed delete, the Tier A rows are untouched,
    and the pass reports what it removed.

    The `mid` rows sit BETWEEN the two horizons (20 days, against shadow's 14 and Tier B's 30) and
    they are what makes this test able to fail. With every tier row outside both horizons — the
    first version of this fixture — a mutation that gave Tier B shadow's age, or built Tier B's
    predicate from the shadow publisher set, produced an identical catalogue and the test passed
    on it."""
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "tierb.example")
    monkeypatch.setenv("RWE_CORPUS_SHADOW", "shadowy.example")
    monkeypatch.setenv("RWE_RETENTION_MAX_AGE_DAYS_TIER_B", "30")
    monkeypatch.setenv("RWE_RETENTION_MAX_AGE_DAYS_SHADOW", "14")
    st = _store(tmp_path)
    _seed_store(st, [_art("https://npr.org/ancient", "NPR", 900),
                     _art("https://tierb.example/old", "tierb.example", 90),
                     _art("https://tierb.example/mid", "tierb.example", 20),   # < 30: survives
                     _art("https://tierb.example/new", "tierb.example", 1),
                     _art("https://shadowy.example/mid", "shadowy.example", 20),  # > 14: pruned
                     _art("https://shadowy.example/new", "shadowy.example", 1)])

    res = corpus_health.run_retention(st, thresholds=_no_floors(), now=NOW)
    assert res["pruned"] == 2, f"expected the two aged tier rows, got {res['pruned']}"
    assert _urls(st) == {"https://npr.org/ancient",          # Tier A: no global age configured
                         "https://tierb.example/mid",        # 20d survives Tier B's 30-day rule
                         "https://tierb.example/new",
                         "https://shadowy.example/new"}


def test_the_sql_arm_stays_off_until_a_per_tier_age_is_configured(tmp_path, monkeypatch):
    """A deployment that has not asked for a per-tier horizon must run exactly the pass it ran
    before this existed — including on tier-assigned publishers.

    Asserted by making the exclusion lookups **explode**, not by checking that nothing was deleted.
    An unguarded arm still deletes nothing when both ages are zero, so a row-count assertion passes
    on the mutation that removes the guard — and the guard's whole job is to keep an unconfigured
    pass from paying for `import corpus` and two tier-index builds on every cycle."""
    import corpus

    def _boom():
        raise AssertionError("the SQL arm consulted corpus with no per-tier age configured")

    monkeypatch.setattr(corpus, "shadow_exclusions", _boom)
    monkeypatch.setattr(corpus, "tier_b_exclusions", _boom)
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "tierb.example")
    monkeypatch.setenv("RWE_RETENTION_MAX_COUNT", "10")
    st = _store(tmp_path)
    _seed_store(st, [_art("https://tierb.example/old", "tierb.example", 900)])
    res = corpus_health.run_retention(st, thresholds=_no_floors(), now=NOW)
    assert res["pruned"] == 0
    assert _urls(st) == {"https://tierb.example/old"}


def test_the_planner_stops_applying_a_tier_age_the_sql_arm_owns(tmp_path, monkeypatch):
    """The SQL arm exists to take Tier B and shadow OUT of the Python planner. If the planner keeps
    its per-tier resolver, both arms act, the O(n) walk still happens, and stage 2 has bought a
    second deletion path and nothing else.

    Asserted on the argument rather than the outcome, because the outcome is identical either way:
    the SQL arm runs first, so the planner simply finds nothing left to prune. That is exactly how
    this shipped wrong the first time and was only caught by mutation."""
    seen = {}
    real = corpus_health.plan_retention

    def _spy(articles, **kw):
        seen.update(kw)
        return real(articles, **kw)

    monkeypatch.setattr(corpus_health, "plan_retention", _spy)
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "tierb.example")
    monkeypatch.setenv("RWE_RETENTION_MAX_AGE_DAYS_TIER_B", "30")
    st = _store(tmp_path)
    _seed_store(st, [_art("https://tierb.example/old", "tierb.example", 90)])

    corpus_health.run_retention(st, thresholds=_no_floors(), now=NOW)
    assert seen["age_days_for"] is None, (
        "the planner was handed a per-tier age resolver while the SQL arm was also applying it — "
        "both arms are pruning the same tiers and the O(n) walk was not removed")
    assert _urls(st) == set(), "the SQL arm should have taken the aged Tier B row"
