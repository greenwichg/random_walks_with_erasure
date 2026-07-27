"""The publisher enrichment pass — idempotence, budget, and fail-soft behaviour.

Enrichment runs inside the poll loop, so its failure modes matter more than its success one: a
provider outage must produce log lines, not a broken poll cycle, and a rerun must be free rather
than merely harmless. No network — fetch_json is injected throughout.
"""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import publisher_metadata as pm   # noqa: E402
import publisher_wiki as pw       # noqa: E402
import store as store_mod         # noqa: E402

NOW = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)


def _seed(st, publisher, host, n=1):
    for i in range(n):
        st.upsert_feed_article(
            canonical_url=f"https://{host}/{i}", url=f"https://{host}/{i}", publisher=publisher,
            source_publisher=publisher, title=f"Headline number {i} about something",
            description="d", body=None, published_at="2026-07-20T09:00:00+00:00", source_feed="f",
            scored={"article_id": f"https://{host}/{i}", "outlet": publisher,
                    "category": "Politics", "lean": 0.0, "political": True, "title": "t"})


def _wiki(title, qid="Q7", website="https://examplepost.com"):
    """A fetch_json that answers a full successful lookup for one outlet."""
    def fetch(url):
        if "list=search" in url:
            return {"query": {"search": []}}
        if "prop=pageprops" in url:
            return {"query": {"pages": [{"title": title, "pageprops": {"wikibase_item": qid},
                                         "extract": "A daily paper."}]}}
        if f"ids={qid}" in url:
            return {"entities": {qid: {"id": qid, "claims": {pw.P_WEBSITE: [
                {"rank": "normal",
                 "mainsnak": {"snaktype": "value", "datavalue": {"value": website}}}]}}}}
        raise AssertionError(f"unexpected: {url}")
    return fetch


# --------------------------------------------------------------------------- #
# Worklist + idempotence.
# --------------------------------------------------------------------------- #
def test_worklist_is_busiest_publishers_first():
    """A bounded budget should be spent where readers will actually see the result."""
    st = store_mod.Store("sqlite://")
    _seed(st, "Small Outlet", "small.example", n=1)
    _seed(st, "Big Outlet", "big.example", n=5)
    assert [c["publisher"] for c in pm.pending(st, limit=10)] == ["Big Outlet", "Small Outlet"]


def test_a_fresh_row_is_not_re_fetched():
    """Idempotence: the second run does no work and makes no requests."""
    st = store_mod.Store("sqlite://")
    _seed(st, "Example Post", "examplepost.com")
    st.upsert_publisher_metadata("Example Post", status="ok", at=NOW)
    assert pm.pending(st, limit=10, now=NOW + timedelta(days=1)) == []


def test_a_stale_row_comes_back_into_the_worklist():
    st = store_mod.Store("sqlite://")
    _seed(st, "Example Post", "examplepost.com")
    st.upsert_publisher_metadata("Example Post", status="ok", at=NOW)
    due = pm.pending(st, limit=10, now=NOW + timedelta(days=31))
    assert [c["publisher"] for c in due] == ["Example Post"]


def test_errors_are_retried_far_sooner_than_misses():
    """An error says nothing about the outlet, only about the minute it happened in — so it is not
    allowed to park a publisher for a month the way a real 'no such article' does."""
    st = store_mod.Store("sqlite://")
    _seed(st, "Example Post", "examplepost.com")
    st.upsert_publisher_metadata("Example Post", status="error", at=NOW)
    assert pm.pending(st, limit=10, now=NOW + timedelta(hours=8))

    st.upsert_publisher_metadata("Example Post", status="no_match", at=NOW)
    assert pm.pending(st, limit=10, now=NOW + timedelta(hours=8)) == []


def test_a_row_without_a_timestamp_is_treated_as_stale():
    assert pm.is_stale({"status": "ok", "fetchedAt": None}) is True
    assert pm.is_stale(None) is True


# --------------------------------------------------------------------------- #
# The pass itself.
# --------------------------------------------------------------------------- #
def test_enrichment_caches_a_verified_match():
    st = store_mod.Store("sqlite://")
    _seed(st, "Example Post", "examplepost.com")

    summary = pm.run_enrichment(st, fetch_json=_wiki("Example Post"), limit=5)

    assert summary["considered"] == 1 and summary["byStatus"] == {"ok": 1}
    row = st.publisher_metadata("Example Post")
    assert row["status"] == "ok" and row["website"] == "https://examplepost.com"
    assert row["wikipediaUrl"].endswith("Example_Post")


def test_rerunning_immediately_makes_no_requests():
    """The property that makes this safe on a cron or after a partial failure."""
    st = store_mod.Store("sqlite://")
    _seed(st, "Example Post", "examplepost.com")
    pm.run_enrichment(st, fetch_json=_wiki("Example Post"), limit=5)

    def explode(url):
        raise AssertionError("a fresh row must not be re-fetched")

    assert pm.run_enrichment(st, fetch_json=explode, limit=5)["considered"] == 0


def test_the_batch_budget_is_respected():
    st = store_mod.Store("sqlite://")
    for i in range(6):
        _seed(st, f"Outlet {i}", f"outlet{i}.example", n=6 - i)
    summary = pm.run_enrichment(st, fetch_json=_wiki("Outlet 0"), limit=2)
    assert summary["considered"] == 2


def test_a_transport_failure_is_recorded_not_raised():
    """One unreachable outlet must not abort the batch or the poll cycle."""
    st = store_mod.Store("sqlite://")
    _seed(st, "Example Post", "examplepost.com")

    def boom(url):
        raise OSError("connection reset")

    summary = pm.run_enrichment(st, fetch_json=boom, limit=5)
    assert summary["byStatus"] == {"error": 1}
    row = st.publisher_metadata("Example Post")
    assert row["status"] == "error" and "connection reset" in row["error"]


def test_a_later_failure_does_not_erase_an_earlier_success():
    """The cache asymmetry, end to end: a bad minute upstream must not empty a publisher page."""
    st = store_mod.Store("sqlite://")
    _seed(st, "Example Post", "examplepost.com")
    pm.run_enrichment(st, fetch_json=_wiki("Example Post"), limit=5)

    def boom(url):
        raise OSError("503")

    pm.enrich_publisher(st, "Example Post", fetch_json=boom)
    row = st.publisher_metadata("Example Post")
    assert row["status"] == "ok" and row["website"] == "https://examplepost.com"


def test_the_observed_host_is_passed_to_verification():
    """The catalog host is the independent evidence that a match is the right organisation, so it
    has to actually reach the verifier — a mismatch must be refused, not cached as fact."""
    st = store_mod.Store("sqlite://")
    _seed(st, "Example Post", "examplepost.com")
    assert pm.observed_host(st, "Example Post") == "examplepost.com"

    fetch = _wiki("Example Post", website="https://a-totally-different-company.com")
    pm.run_enrichment(st, fetch_json=fetch, limit=5)
    row = st.publisher_metadata("Example Post")
    assert row["status"] == "ambiguous" and row["website"] is None


def test_enrichment_is_off_until_something_asks_for_it(monkeypatch):
    """A module that reaches a third-party API must not do so merely because it was imported. Off in
    code (so tests and local pollers are silent), on in production via compose — the same convention
    the GKG enricher follows."""
    monkeypatch.delenv("RWE_PUBLISHER_WIKI", raising=False)
    assert pm.enabled() is False
    monkeypatch.setenv("RWE_PUBLISHER_WIKI", "1")
    assert pm.enabled() is True
    monkeypatch.setenv("RWE_PUBLISHER_WIKI", "0")
    assert pm.enabled() is False


def test_batch_size_is_tunable(monkeypatch):
    monkeypatch.setenv("RWE_PUBLISHER_WIKI_BATCH", "12")
    assert pm.batch_size() == 12
    monkeypatch.setenv("RWE_PUBLISHER_WIKI_BATCH", "junk")
    assert pm.batch_size() == pm.DEFAULT_BATCH        # junk never silently widens the budget


def test_logging_reports_the_pass():
    st = store_mod.Store("sqlite://")
    _seed(st, "Example Post", "examplepost.com")
    lines = []
    pm.run_enrichment(st, fetch_json=_wiki("Example Post"), limit=5,
                      log=lambda level, event, **f: lines.append((event, f)))
    assert lines and lines[0][0] == "publisher_enrichment"
    assert lines[0][1]["byStatus"] == {"ok": 1}


# --------------------------------------------------------------------------- #
# The poller adapter.
# --------------------------------------------------------------------------- #
def test_the_adapter_is_registered_last_and_is_off_by_default(monkeypatch):
    import sources
    monkeypatch.delenv("RWE_PUBLISHER_WIKI", raising=False)
    reg = sources.default_registry()
    assert reg.adapters()[-1].provider == "Wikipedia"       # enrichment after the sources it annotates
    assert "Wikipedia" not in [a.provider for a in reg.enabled()]


def test_the_adapter_never_reaches_the_network_unbidden(monkeypatch):
    """Regression: this enrichment first lived in MultiSourcePoller._post_cycle, where it built its
    own HTTP call. Every poller test then made real Wikipedia requests — the suite went from 60s to
    194s and an unrelated adapter-isolation test failed on timing. As an adapter with an injectable
    fetch it is inert unless a caller supplies one."""
    import sources
    st = store_mod.Store("sqlite://")
    _seed(st, "Example Post", "examplepost.com")
    monkeypatch.setenv("RWE_PUBLISHER_WIKI", "1")

    calls = []

    def fake(url):
        calls.append(url)
        return {"query": {"pages": [{"title": "Example Post", "missing": True}]},
                "search": []}

    adapter = sources.PublisherMetadataEnricher(fetch_json=fake)
    agg = adapter.poll_once(st, None)

    assert calls and all("wikipedia.org" in c or "wikidata.org" in c for c in calls)
    assert agg["considered"] == 1 and agg["noMatch"] == 1
    assert agg["errors"] == [] and agg["ok"] == 1


def test_the_adapter_reports_a_failure_as_health_not_an_exception():
    import sources
    st = store_mod.Store("sqlite://")
    _seed(st, "Example Post", "examplepost.com")

    def boom(url):
        raise OSError("unreachable")

    agg = sources.PublisherMetadataEnricher(fetch_json=boom).poll_once(st, None)
    # The lookup failure is a per-publisher COUNTER; the cycle itself still succeeds, because one
    # unreachable outlet is not a broken enrichment source. `errors` stays the cycle's error LIST,
    # the shape every other adapter's aggregate uses.
    assert agg["lookupErrors"] == 1
    assert agg["errors"] == [] and agg["ok"] == 1
