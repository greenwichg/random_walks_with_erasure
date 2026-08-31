"""Crawl policy — pause + per-host cadence + channel selection, orthogonal to admission.

The contract under test, as frozen in review:

* **Admission IS crawl authorization.** There is no separate crawl-approved state; an admitted
  host crawls by default, and nothing here adds a gate in front of that.
* **Policy is two columns and can only reduce or slow contact.** ``crawl_paused_at`` removes a
  host from the config build AND short-circuits its next poll (the live half — configs are built
  once per process, and the use case is pausing a misbehaving host NOW, not at the next restart).
  ``crawl_interval_seconds`` re-times an authorized crawl, floored at ``MIN_CRAWL_INTERVAL`` at
  READ so no stored value can cycle a publisher faster than the floor.
* **Selection is not authorization.** ``RWE_CRAWL_CHANNELS`` picks which acquisition channels'
  admissions get configs; hosts outside the filter stay admitted. Legacy rows with no recorded
  channel match only the empty filter — guessing their channel is what the channel column's own
  docstring refuses to do.
* **Policy writes cannot touch the admission state machine** — `state`, `tier`, and the probe
  ledger are byte-identical across pause/resume/interval, because pausing a misbehaving host must
  never look like withdrawing it.

Every guard is mutation-verified (see the session ledger): the paused filter, the channel WHERE,
the read-side clamp, and the live short-circuit were each reverted and their test went red.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "examples"))

import corpus  # noqa: E402
import crawler  # noqa: E402
import store as store_mod  # noqa: E402


def _cand(host, *, channel="web", language="en"):
    return {"host": host, "articles": 40, "language": language, "publishers": [host],
            "eligible": True, "channel": channel}


@pytest.fixture()
def st(tmp_path):
    return store_mod.Store(f"sqlite:///{tmp_path / 'cp.db'}")


@pytest.fixture()
def wired(st, monkeypatch):
    """Two admitted hosts (channels web + catalogue) with feeds, corpus wired and always unwired,
    and the channel filter cleared — the state `admitted_configs` reads."""
    monkeypatch.delenv("RWE_CRAWL_CHANNELS", raising=False)
    st.record_admission_candidates([_cand("web.example"),
                                    _cand("cat.example", channel="catalogue")])
    for host in ("web.example", "cat.example"):
        assert st.claim_admission_probe(host) is not None
        st.record_admission_probe(host, verdict="ADMIT", gates=[], samples=[],
                                  feed_url=f"https://{host}/feed", discovered_via="feed")
        st.admit_source(host, tier="B", accept_partition_change=True)
    corpus.wire_admissions(st.admitted_shadow_hosts)
    corpus.wire_tier_b_admissions(st.admitted_tier_b_hosts)   # _wire resets the tier cache itself
    try:
        yield st
    finally:
        corpus.wire_admissions(None)
        corpus.wire_tier_b_admissions(None)


def _config_hosts(st):
    return sorted(c.domains[0] for c in crawler.admitted_configs(st))


# --------------------------------------------------------------------- pause / resume
def test_a_pause_removes_the_config_and_a_resume_restores_it(wired):
    assert _config_hosts(wired) == ["cat.example", "web.example"]
    wired.set_crawl_policy("web.example", paused=True)
    assert _config_hosts(wired) == ["cat.example"], "a paused host must emit no crawl config"
    wired.set_crawl_policy("web.example", paused=False)
    assert _config_hosts(wired) == ["cat.example", "web.example"]


def test_policy_writes_leave_the_admission_state_machine_byte_identical(wired):
    def snapshot():
        row = wired.admission_row("web.example")
        return {k: v for k, v in row.items()
                if k not in ("crawlPausedAt", "crawlIntervalSeconds")}
    before = snapshot()
    wired.set_crawl_policy("web.example", paused=True)
    wired.set_crawl_policy("web.example", interval_seconds=600)
    wired.set_crawl_policy("web.example", paused=False)
    assert snapshot() == before, \
        "pause/resume/interval must not move state, tier, or the probe ledger"


def test_a_paused_poll_short_circuits_before_any_fetch(wired):
    """The LIVE half: a pause takes effect within one cycle, not at the next restart."""
    def bomb(url):
        raise AssertionError(f"a paused host fetched {url}")
    cfg = crawler.PublisherCrawlConfig(publisher="Web Example", domains=("web.example",),
                                       sources=(crawler.DiscoverySource("rss", "https://web.example/feed"),))
    adapter = crawler.CrawlAdapter(cfg, fetch=bomb, store_=wired)
    wired.set_crawl_policy("web.example", paused=True)
    agg = adapter.poll_once(wired, None)
    assert agg.get("crawlPaused") is True
    assert agg["failed"] == 0 and agg["new"] == 0


def test_a_store_hiccup_on_the_pause_read_fails_open_to_crawling(wired):
    """Pause is a convenience; robots is the safety. A store error must not stop ingestion —
    the poll proceeds (and here fails on the FETCH, proving the short-circuit did not fire)."""
    class Hiccup:
        def crawl_paused(self, host):
            raise RuntimeError("db locked")

    class Paused:
        def crawl_paused(self, host):
            return "2026-08-31T00:00:00+00:00"

    def bomb(url):
        raise RuntimeError("unreachable network in this test")

    cfg = crawler.PublisherCrawlConfig(publisher="Web Example", domains=("web.example",),
                                       sources=(crawler.DiscoverySource("rss", "https://web.example/feed"),))
    control = crawler.CrawlAdapter(cfg, fetch=bomb, store_=Paused()).poll_once(Paused(), None)
    assert control.get("crawlPaused") is True, "control: a readable pause short-circuits"
    agg = crawler.CrawlAdapter(cfg, fetch=bomb, store_=Hiccup()).poll_once(Hiccup(), None)
    assert "crawlPaused" not in agg, \
        "an errored pause read must run the NORMAL cycle (whose own gates then apply), never " \
        "report the host as paused — fail-open: pause is a convenience, robots is the safety"


# --------------------------------------------------------------------- interval
def test_the_per_host_interval_reaches_the_adapter_clamped_at_the_floor(wired):
    wired.set_crawl_policy("web.example", interval_seconds=600)
    by_host = {c.domains[0]: c for c in crawler.admitted_configs(wired)}
    assert by_host["web.example"].interval_seconds == 600
    assert crawler.CrawlAdapter(by_host["web.example"]).interval() == 600.0
    assert crawler.CrawlAdapter(by_host["cat.example"]).interval() == 900.0, \
        "no override -> the global default"
    # The record keeps what the operator wrote; the READ clamps. (The CLI refuses <300 too, but
    # the clamp is the invariant — a raw store write must still be unable to hammer a publisher.)
    wired.set_crawl_policy("web.example", interval_seconds=30)
    by_host = {c.domains[0]: c for c in crawler.admitted_configs(wired)}
    assert crawler.CrawlAdapter(by_host["web.example"]).interval() == crawler.MIN_CRAWL_INTERVAL


# --------------------------------------------------------------------- channel selection
def test_the_channel_filter_selects_without_deauthorizing(wired, monkeypatch):
    monkeypatch.setenv("RWE_CRAWL_CHANNELS", "web")
    assert _config_hosts(wired) == ["web.example"]
    row = wired.admission_row("cat.example")
    assert row["state"] == "admitted" and row["tier"] == "B", \
        "an unselected host stays admitted — selection is not authorization"
    monkeypatch.setenv("RWE_CRAWL_CHANNELS", "web_search")   # operator-facing alias
    assert _config_hosts(wired) == ["web.example"]
    monkeypatch.delenv("RWE_CRAWL_CHANNELS")
    assert _config_hosts(wired) == ["cat.example", "web.example"], "empty filter = all admitted"


def test_a_legacy_row_with_no_channel_matches_only_the_empty_filter(wired, monkeypatch):
    with wired.session() as s:
        from store import SourceAdmission
        s.get(SourceAdmission, "cat.example").channel = None
    monkeypatch.setenv("RWE_CRAWL_CHANNELS", "web,catalogue")
    assert _config_hosts(wired) == ["web.example"], \
        "a NULL channel must not be guessed into a filter match"
    monkeypatch.delenv("RWE_CRAWL_CHANNELS")
    assert "cat.example" in _config_hosts(wired)


# --------------------------------------------------------------------- status join
def test_the_health_join_key_has_one_implementation(wired):
    cfg = crawler.PublisherCrawlConfig(publisher="Kenh14 Vn", domains=("kenh14.vn",))
    assert crawler.CrawlAdapter(cfg).health_key == crawler.crawl_health_key("Kenh14 Vn")
    assert crawler.crawl_health_key("Kenh14 Vn") == "crawl://kenh14-vn"


def test_the_columns_survive_a_reopened_store(tmp_path):
    """The additive-migration list carries both columns: a store reopened on an existing file
    must read them back rather than fail with `no such column`."""
    db = f"sqlite:///{tmp_path / 'mig.db'}"
    st = store_mod.Store(db)
    st.record_admission_candidates([_cand("x.example")])
    st.set_crawl_policy("x.example", paused=True, interval_seconds=600)
    again = store_mod.Store(db)
    row = again.admission_row("x.example")
    assert row["crawlPausedAt"] and row["crawlIntervalSeconds"] == 600
