"""M3 / D1 — retention reads a narrow projection, and it changes nothing about what gets deleted.

`corpus_health.run_retention` used to call `list_feed_articles(limit=10_000_000)`: every column of
every row, with the full `scored` payload JSON-parsed per article. It reads six fields of that.

Measured at 150,000 rows, production's shape on 2026-08-27:

    list_feed_articles(limit=10M)   7.77 s   +888.9 MB RSS   (6.07 KB/row)
    list_retention_rows()           0.54 s   + 46.9 MB RSS   (0.32 KB/row)

That was **84% of the whole pass** — production logged `cleanupMs` of 11,144–20,890 ms while
deleting 58–140 rows, inside the global ingest lock.

**This is a deletion path, so speed is not the thing to test.** These tests exist to prove the
projection is *behaviourally identical*: for the same catalogue, the plan built from narrow rows and
the plan built from full rows must select the same articles, and the reported metrics must match.
The differential is run over randomised catalogues rather than hand-picked ones, because a
hand-picked case tests the case you thought of.
"""
from __future__ import annotations

import pathlib
import random
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "examples"))

import corpus_health  # noqa: E402
import store as store_mod  # noqa: E402

_LEANS = [-0.8, -0.3, 0.0, 0.35, 0.9, None]
_NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


def _seed_catalogue(st, rng, count, *, blank_titles=True, undated=True):
    """A catalogue with the awkward shapes the planner has to survive: unknown leans, blank titles,
    missing publication dates, duplicate publishers, and a wide age spread across the floors."""
    for i in range(count):
        published = None
        if not (undated and i % 23 == 0):
            published = (_NOW - timedelta(days=rng.randrange(0, 120),
                                          hours=rng.randrange(0, 24))).isoformat()
        publisher = f"outlet{rng.randrange(0, max(2, count // 4))}.example"
        st.upsert_feed_article(
            canonical_url=f"https://{publisher}/a/{i}",
            url=f"https://{publisher}/a/{i}",
            publisher=publisher,
            source_publisher=publisher,
            title="" if (blank_titles and i % 17 == 0) else f"Headline number {i}",
            description="d" * rng.randrange(0, 60),
            body=None,
            published_at=published,
            source_feed=f"https://{publisher}/feed",
            scored={"article_id": f"https://{publisher}/a/{i}", "outlet": publisher,
                    "lean": _LEANS[i % len(_LEANS)], "category": "Politics"},
            source_type="rss", source_provider=publisher)


@pytest.fixture()
def seeded(tmp_path):
    st = store_mod.Store(f"sqlite:///{tmp_path / 'c.db'}")
    _seed_catalogue(st, random.Random(7), 240)
    return st


def _key(rows):
    return sorted(r["canonicalUrl"] for r in rows)


def test_projection_returns_one_row_per_article(seeded):
    assert _key(seeded.list_retention_rows()) == _key(seeded.list_feed_articles(limit=10_000_000))


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("policy", [
    {"max_count": 50},
    {"max_count": 200},
    {"max_age_days": 30},
    {"max_age_days": 7},
    {"max_age_days": 60, "max_count": 120},
])
def test_the_plan_is_identical_from_narrow_and_full_rows(tmp_path, seed, policy):
    """The load-bearing assertion. Same catalogue, same policy, two representations — the prune set
    and the keep set must match exactly, including the floor repair pass's choices."""
    st = store_mod.Store(f"sqlite:///{tmp_path / f'c{seed}.db'}")
    _seed_catalogue(st, random.Random(seed), 180)

    full = corpus_health.plan_retention(st.list_feed_articles(limit=10_000_000), now=_NOW, **policy)
    narrow = corpus_health.plan_retention(st.list_retention_rows(), now=_NOW, **policy)

    assert sorted(narrow["prune"]) == sorted(full["prune"]), policy
    assert sorted(narrow["keep"]) == sorted(full["keep"]), policy
    assert (narrow["pruned"], narrow["kept"], narrow["rawPruned"], narrow["retainedForFloor"]) == \
           (full["pruned"], full["kept"], full["rawPruned"], full["retainedForFloor"])
    st.engine.dispose()


#: `plan_retention` has FOUR repair passes — per-bucket, publishers, fresh, then total — and three
#: of them are off in the default environment (`minPublishers`/`minPerBucket`/`minFresh` all
#: default to 0). A differential that only ever exercised `minArticles` would leave the other three
#: untested, so each is switched on explicitly here. The buckets one is the subtlest: it reads
#: `scored.lean`, which the projection gets from a `json_extract` rather than a parsed payload.
_FLOOR_SETTINGS = [
    pytest.param({}, id="default-floors"),
    pytest.param({"RWE_CORPUS_MIN_PUBLISHERS": "20"}, id="publishers-floor"),
    pytest.param({"RWE_CORPUS_MIN_PER_BUCKET": "15"}, id="per-bucket-floor"),
    pytest.param({"RWE_CORPUS_MIN_FRESH": "10", "RWE_CORPUS_FRESH_MAX_AGE_DAYS": "30"},
                 id="fresh-floor"),
    pytest.param({"RWE_CORPUS_MIN_PUBLISHERS": "25", "RWE_CORPUS_MIN_PER_BUCKET": "12",
                  "RWE_CORPUS_MIN_FRESH": "8", "RWE_CORPUS_FRESH_MAX_AGE_DAYS": "45",
                  "RWE_CORPUS_MIN_ARTICLES": "60"}, id="every-floor-at-once"),
]


#: Every environment variable `thresholds_from_env` consults. Cleared before each floor case, so
#: the test asserts against the floors it sets rather than the ones the session happens to carry.
#:
#: This is not defensive padding — it caught a real leak. `test_coach_greeting`, `test_coach_tools`
#: and `test_coach_conversations` each set `RWE_FEED_MIN_ARTICLES=5` with a bare
#: `os.environ[...] =` inside a module-scoped fixture and never restored it, and `minArticles` falls
#: back to that variable. So this file passed alone and failed in the full suite, with `minArticles`
#: silently 5 instead of 50.
#:
#: That leak is now fixed at its source — those fixtures take conftest's `module_env`, and
#: `_no_env_leaks_between_modules` fails any module that configures the engine for its successors.
#: The clearing stays anyway: a test of a deletion policy should assert against the floors it sets,
#: not against whichever floors the session happens to be carrying, and that is true whether or not
#: anything is currently leaking.
_THRESHOLD_ENV = ("RWE_CORPUS_MIN_ARTICLES", "RWE_FEED_MIN_ARTICLES", "RWE_CORPUS_MIN_PUBLISHERS",
                  "RWE_CORPUS_MIN_PER_BUCKET", "RWE_CORPUS_MIN_FRESH",
                  "RWE_CORPUS_FRESH_MAX_AGE_DAYS")


@pytest.mark.parametrize("floors", _FLOOR_SETTINGS)
def test_every_floor_repair_pass_agrees_across_representations(tmp_path, floors, monkeypatch):
    """The repair passes are where a representation difference would actually bite: each one walks
    the pruned articles newest-first and pulls them back, so it depends on the outlet, the lean and
    the publication date all surviving the projection."""
    for key in _THRESHOLD_ENV:
        monkeypatch.delenv(key, raising=False)
    for key, value in floors.items():
        monkeypatch.setenv(key, value)
    st = store_mod.Store(f"sqlite:///{tmp_path / 'floors.db'}")
    _seed_catalogue(st, random.Random(77), 200)

    full = corpus_health.plan_retention(st.list_feed_articles(limit=10_000_000),
                                        max_age_days=10, now=_NOW)
    narrow = corpus_health.plan_retention(st.list_retention_rows(), max_age_days=10, now=_NOW)

    assert sorted(narrow["prune"]) == sorted(full["prune"])
    assert narrow["retainedForFloor"] == full["retainedForFloor"]
    # And the case has to be one where the repair actually did something, or it proves nothing.
    if floors:
        assert full["retainedForFloor"] > 0, (
            f"floors {floors} pulled nothing back — this case is not exercising a repair pass")
    st.engine.dispose()


@pytest.mark.parametrize("seed", [11, 12, 13])
def test_the_metrics_are_identical_from_narrow_and_full_rows(tmp_path, seed):
    """`corpus_metrics` is the other consumer, and it reads `title` (via `_missing_metadata`) —
    the one field that is in the projection for a reason other than the planner."""
    st = store_mod.Store(f"sqlite:///{tmp_path / f'm{seed}.db'}")
    _seed_catalogue(st, random.Random(seed), 150)
    full = corpus_health.corpus_metrics(st.list_feed_articles(limit=10_000_000), now=_NOW)
    narrow = corpus_health.corpus_metrics(st.list_retention_rows(), now=_NOW)
    assert narrow == full
    st.engine.dispose()


def test_the_fields_the_planner_reads_survive_the_projection(seeded):
    """Named individually, so a dropped column fails here with the field's name rather than as a
    mysterious plan difference somewhere else."""
    by_url = {r["canonicalUrl"]: r for r in seeded.list_retention_rows()}
    for full in seeded.list_feed_articles(limit=10_000_000):
        narrow = by_url[full["canonicalUrl"]]
        assert corpus_health._canonical(narrow) == corpus_health._canonical(full)
        assert corpus_health._outlet(narrow) == corpus_health._outlet(full)
        assert corpus_health._bucket(narrow) == corpus_health._bucket(full)
        assert corpus_health._published(narrow) == corpus_health._published(full)
        assert corpus_health._missing_metadata(narrow) == corpus_health._missing_metadata(full)
        assert corpus_health._has_publication_date(narrow) == corpus_health._has_publication_date(full)


def test_run_retention_deletes_the_same_rows_the_planner_chose(tmp_path):
    """End to end: the projection is wired into `run_retention`, and what it deletes matches what a
    plan over full rows says it should."""
    st = store_mod.Store(f"sqlite:///{tmp_path / 'e2e.db'}")
    _seed_catalogue(st, random.Random(99), 200)
    expected = corpus_health.plan_retention(
        st.list_feed_articles(limit=10_000_000), max_count=120, now=_NOW)

    res = corpus_health.run_retention(st, max_count=120, log=lambda *a, **k: None, now=_NOW)

    assert res["pruned"] == expected["pruned"]
    survivors = {r["canonicalUrl"] for r in st.list_retention_rows()}
    assert survivors == set(expected["keep"])
    assert survivors.isdisjoint(expected["prune"])
    st.engine.dispose()


def test_the_projection_is_not_a_feed_article_row(seeded):
    """It carries what retention reads and nothing else. `createdAt` is absent on purpose: it feeds
    `_CANDIDACY_TIME_KEYS`, which is candidate freshness rather than retention, so a caller handing
    these rows to that path would silently get different ages."""
    row = seeded.list_retention_rows()[0]
    assert set(row) == {"canonicalUrl", "url", "publisher", "title",
                        "publishedAt", "fetchedAt", "scored"}
    assert set(row["scored"]) == {"outlet", "lean"}
    assert "createdAt" not in row
    assert "body" not in row and "description" not in row
