"""M10 — discovery observes the whole retained catalogue, not the clustering window.

Stage 1 called `story_service._fetch(st)`, the *clustering* candidate set. That borrowed the tier
exclusions, which are right for discovery (a shadow or Tier B host is one we already found), and
`scan_days()` — the **6-day clustering window** — which is not.

Measured on production 2026-08-27, same database, same moment:

    through `_fetch`      28,217 articles   4,238 hosts     751 above the floor   177 candidates
    the whole catalogue  150,076 articles   9,397 hosts   1,525 above the floor  ~950 candidates

A host publishing twice a week has ~1.7 articles in six days. It could never reach the 10-article
floor however long we carried it — and that is the publication rate of the local and regional long
tail a 50,000-source corpus is made of. **The floor was never the problem; the window was.**

The tests below are shaped around that: the load-bearing one builds a host at exactly that cadence
and asserts it is invisible through the old window and a candidate through the new one. Asserting
"more rows come back" would pass for any change that widened anything.

## Why every window test drops `RWE_STORIES_SCAN_DAYS`

`tests/conftest.py` sets it to 36500 — a century — session-wide and autouse, so that fixtures
pinned to fixed calendar dates do not age out of the clustering window and collapse. That is right
for the rest of the suite and **fatal here**: under it, `story_service._fetch` sees everything, so
the 6-day window this milestone is about does not exist inside pytest.

The first version of this file did not drop it. Mutation-testing caught it: reverting the product to
`rows = story_service._fetch(st)` — undoing the entire milestone — left all seven tests green,
because the environment had disabled the difference they were written to detect. A test whose
premise is switched off by the harness it runs in is the defect this repository keeps finding in its
own instruments, and this is one I wrote.

`conftest.py` names the remedy in its own docstring — *"the window's OWN behaviour is covered
explicitly in test_story_service.py (which drops this override to exercise the real default)"* — and
`_real_window` below is that, for this file.
"""
from __future__ import annotations

import contextlib
import io
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "examples"))

import audit_source_discovery as asd  # noqa: E402
import outlet_registry  # noqa: E402
import source_discovery as sd  # noqa: E402
import store as store_mod  # noqa: E402

_NOW = datetime.now(timezone.utc)


@pytest.fixture()
def _real_window(monkeypatch):
    """Restore the SHIPPED clustering window. See the module docstring: the suite-wide conftest
    opens it to a century, which makes every assertion in this file vacuous."""
    monkeypatch.delenv("RWE_STORIES_SCAN_DAYS", raising=False)
    import story_service
    assert story_service.scan_days() == 6.0, (
        f"expected the shipped 6-day window, got {story_service.scan_days()} — the override is "
        f"still in force and these tests would prove nothing")


def _publish(st, host, n, *, every_days, source_type="rss", language="en"):
    for i in range(n):
        st.upsert_feed_article(
            canonical_url=f"https://{host}/a/{i}", url=f"https://{host}/a/{i}",
            publisher=host, source_publisher=host, title=f"headline {i}", description="d",
            body=None, published_at=(_NOW - timedelta(days=every_days * i)).isoformat(),
            source_feed=f"https://{host}/feed",
            scored={"outlet": host, "lean": None, "category": "Politics"},
            source_type=source_type, language=language)


@pytest.fixture()
def slow_publisher(tmp_path):
    """24 articles over 12 weeks — twice a week, the long tail's actual cadence.

    Well over the 10-article floor in total, and 1–2 articles inside any six-day window.
    """
    st = store_mod.Store(f"sqlite:///{tmp_path / 'c.db'}")
    _publish(st, "slowlocal.example", 24, every_days=3.5)
    return st


def _candidates(rows):
    return [c for c in sd.candidates(rows, outlet_registry.default_registry()) if c["eligible"]]


def test_a_twice_weekly_publisher_is_invisible_through_the_clustering_window(
        slow_publisher, _real_window):
    """The defect, stated as a test. This is what production was doing to 3,487 hosts.

    `_fetch` with NO `date_from`, so the assertion is about the window the product actually ships
    rather than one the test supplied."""
    import story_service
    windowed = story_service._fetch(slow_publisher)
    assert len(windowed) < 10, "the fixture must not reach the floor inside six days"
    assert _candidates(windowed) == [], "a 24-article host should be below the floor in a 6-day window"


def test_the_same_publisher_is_a_candidate_over_the_whole_catalogue(slow_publisher):
    rows = slow_publisher.list_discovery_rows()
    assert len(rows) == 24
    hosts = [c["host"] for c in _candidates(rows)]
    assert hosts == ["slowlocal.example"]


def test_the_runner_defaults_to_the_catalogue_and_window_days_restores_the_old_behaviour(
        slow_publisher, tmp_path, _real_window):
    """`--window-days` exists so the change is auditable against what it replaced, not because a
    narrow window is ever what discovery wants."""
    db = f"sqlite:///{tmp_path / 'c.db'}"
    slow_publisher.engine.dispose()

    def run(*extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            asd.main(["--db", db, "--show", "1", *extra])
        return buf.getvalue()

    assert "CANDIDATES                      : 1" in run()
    assert "CANDIDATES                      : 0" in run("--window-days", "6")


def test_the_tier_exclusions_are_kept(tmp_path, monkeypatch):
    """Inheriting `_fetch`'s exclusions was the RIGHT half of the old call. A shadow host is one
    discovery already found; re-reporting it as a candidate would be noise, and worse, it would
    invite admitting it twice."""
    import corpus
    st = store_mod.Store(f"sqlite:///{tmp_path / 'c.db'}")
    _publish(st, "already.example", 12, every_days=1)
    _publish(st, "fresh.example", 12, every_days=1)
    monkeypatch.setenv("RWE_CORPUS_SHADOW", "already.example")

    rows = st.list_discovery_rows(exclude_publishers=corpus.sql_exclusions())
    hosts = {c["host"] for c in _candidates(rows)}
    assert hosts == {"fresh.example"}, hosts
    assert len(st.list_discovery_rows()) == 24, "without the exclusion set, nothing is filtered"


def test_a_row_with_no_publisher_survives_the_exclusion(tmp_path):
    """Discovery is exactly where an unidentified row matters — it is a host we have no name for
    yet, which is the population discovery works on.

    `feed_articles.publisher` is NOT NULL with a `""` default, so "no publisher" reaches SQL as an
    empty string rather than NULL; `lower('') NOT IN (…)` is TRUE and the row survives. The
    `IS NULL` arm in the query is therefore **unreachable for this column as modelled** — it is kept
    because `search_feed_articles` documents the same guard for the same set, and two queries over
    one exclusion list disagreeing about NULL is worse than one redundant term. Asserted at the
    reachable case, and this docstring is the record that the other one was checked rather than
    assumed.
    """
    st = store_mod.Store(f"sqlite:///{tmp_path / 'c.db'}")
    _publish(st, "named.example", 2, every_days=1)
    with st.session() as s:
        from sqlalchemy import text
        s.execute(text("UPDATE feed_articles SET publisher = ''"))
    rows = st.list_discovery_rows(exclude_publishers={"somethingelse.example"})
    assert len(rows) == 2, "an empty publisher was dropped by the exclusion"
    # And the host still resolves from the URL, which is what discovery keys on anyway.
    assert {sd._host(r) for r in rows} == {"named.example"}


def test_the_projection_carries_what_the_report_groups_by(slow_publisher):
    """`sourceType` is read by no gate and by the runner's language-coverage table.

    Leaving it out did not fail — it printed `(none)` for every row, which is how a projection
    quietly deletes a report rather than breaking one.
    """
    row = slow_publisher.list_discovery_rows()[0]
    assert set(row) == {"canonicalUrl", "url", "publisher", "language",
                        "publishedAt", "sourceType"}
    assert row["sourceType"] == "rss"


def test_every_field_the_gates_read_survives_the_projection(slow_publisher):
    """Named individually, so a dropped column fails with the field's name."""
    rows = slow_publisher.list_discovery_rows()
    for r in rows:
        assert sd._host(r) == "slowlocal.example"
    cand = _candidates(rows)[0]
    assert cand["articles"] == 24
    assert cand["publishers"] == ["slowlocal.example"]
    assert cand["language"] == "en"
    assert cand["datedShare"] == 1.0
    assert cand["sampleUrls"] and all(u.startswith("https://slowlocal.example/") for u in cand["sampleUrls"])
