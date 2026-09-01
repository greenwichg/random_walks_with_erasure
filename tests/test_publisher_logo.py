"""Site-logo resolution (examples/publisher_logo.py) — discovery, verification, manners, caching.

Every fetch is a fake: a dict of URL -> bytes (or an exception to raise). Robots is a stub policy so
the refusal path is exercised without a network, and the rate limiter is given a recording sleep.

Mutation ledger (each red against the listed break of publisher_logo.py):
  - MIN_PX guard removed / usable() returns True   -> "a 16px favicon is not a logo" fails
  - TTL_DAYS["none"] raised to 90                  -> "a negative verdict is re-asked" fails
  - best_enriched prefers the site row              -> "Commons beats site" fails
  - discover ignores `sizes` (px = default)         -> "declared size ranks" fails
  - resolve keeps fetching after the first usable   -> "stops at the first usable mark" fails
"""
from __future__ import annotations

import io
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import crawler                       # noqa: E402
import publisher_logo as pl          # noqa: E402
import robots                        # noqa: E402
import store as store_mod            # noqa: E402

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _png(w: int, h: int) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


class _Allow:
    """A robots policy that says yes to everything, recording what it was asked."""
    def __init__(self):
        self.asked = []

    def check(self, url):
        self.asked.append(url)
        return robots.RobotsDecision(True, "")


class _Refuse:
    def check(self, url):
        return robots.RobotsDecision(False, "disallowed by robots.txt")


def _fetcher(pages: dict):
    """URL -> bytes; a value that is an Exception is raised. Records every request."""
    calls = []

    def fetch(url):
        calls.append(url)
        v = pages.get(url)
        if v is None:
            raise OSError(f"404 {url}")
        if isinstance(v, Exception):
            raise v
        return v
    fetch.calls = calls
    return fetch


def _limiter():
    return crawler.RateLimiter(sleep=lambda s: None)


def _seed(st, publisher, host, n=3):
    for i in range(n):
        u = f"https://{host}/a{i}"
        st.upsert_feed_article(canonical_url=u, url=u, publisher=publisher, source_publisher=publisher,
                               title=f"{publisher} {i}", description="", body=None,
                               published_at="2026-08-30T12:00:00+00:00", source_feed="f",
                               scored={"article_id": u, "outlet": publisher, "lean": 0.0,
                                       "category": "Politics"})


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
HTML = """<html><head><base href="https://ex.test/">
<link rel="icon" href="/favicon-32.png" sizes="32x32" type="image/png">
<link rel="icon" href="static/icon-192.png" sizes="192x192">
<link rel="apple-touch-icon" href="/apple.png">
<link rel="manifest" href="/site.webmanifest">
<link rel="mask-icon" href="/mark.svg" color="#000">
</head><body><link rel="icon" href="/late.png" sizes="999x999"></body></html>"""


def test_declared_size_ranks_and_the_body_is_never_read():
    cands = pl.discover_candidates(HTML, "https://ex.test/news/")
    urls = [c["url"] for c in cands]
    # mask-icon (vector, sentinel) > 192 declared > apple default 180 > 32 declared; the manifest
    # is its own kind; the two conventional guesses trail; the body's 999px link is never seen.
    assert urls[:4] == ["https://ex.test/mark.svg", "https://ex.test/static/icon-192.png",
                        "https://ex.test/apple.png", "https://ex.test/favicon-32.png"]
    assert [c["kind"] for c in cands].count("manifest") == 1
    assert urls[-2:] == ["https://ex.test/apple-touch-icon.png", "https://ex.test/favicon.ico"]
    assert not any("late.png" in u for u in urls)
    assert [c["px"] for c in cands[:4]] == [pl._SVG_PX, 192, 180, 32]


def test_manifest_icons_resolve_against_the_manifest_url_and_rank_by_size():
    text = '{"icons":[{"src":"icons/192.png","sizes":"192x192"},{"src":"/icons/512.png","sizes":"512x512","type":"image/png"}]}'
    icons = pl.parse_manifest(text, "https://ex.test/static/site.webmanifest")
    assert [c["url"] for c in icons] == ["https://ex.test/icons/512.png", "https://ex.test/static/icons/192.png"]
    assert pl.parse_manifest("not json", "https://ex.test/m") == []


def test_image_dims_measures_png_accepts_svg_and_rejects_an_html_error_page():
    assert pl.image_dims(_png(180, 180)) == (180, 180)
    assert pl.image_dims(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>") == (pl._SVG_PX, pl._SVG_PX)
    assert pl.image_dims(b"<html><body>404</body></html>") is None
    assert pl.image_dims(b"") is None


def test_usable_is_the_size_floor_and_the_banner_ceiling():
    assert pl.usable((48, 48)) and pl.usable((180, 60))
    assert not pl.usable((16, 16)), "a 16px favicon is not a logo"
    assert not pl.usable((1200, 100)), "a masthead banner is not a logo"
    assert not pl.usable(None)


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
def test_resolve_stops_at_the_first_usable_mark_and_reports_its_size():
    fetch = _fetcher({
        "https://ex.test/": HTML.encode(),
        "https://ex.test/mark.svg": b"<html>not really</html>",        # declared vector, actually HTML
        "https://ex.test/static/icon-192.png": _png(192, 192),
        "https://ex.test/apple.png": _png(180, 180),
    })
    policy = _Allow()
    r = pl.resolve("https://ex.test/", fetch, policy=policy, limiter=_limiter())
    assert r["status"] == "ok" and r["url"] == "https://ex.test/static/icon-192.png"
    assert (r["width"], r["height"]) == (192, 192)
    assert "https://ex.test/apple.png" not in fetch.calls, "stops at the first usable mark"
    assert all(u in policy.asked for u in fetch.calls), "every fetch was checked against robots first"


def test_a_16px_favicon_is_not_a_logo_and_the_verdict_is_none():
    html = '<html><head><link rel="icon" href="/favicon.ico"></head></html>'
    fetch = _fetcher({"https://ex.test/": html.encode(), "https://ex.test/favicon.ico": _png(16, 16)})
    r = pl.resolve("https://ex.test/", fetch, policy=_Allow(), limiter=_limiter())
    assert r["status"] == "none" and "no usable" in r["reason"]


def test_robots_refusal_is_a_verdict_and_makes_no_request():
    fetch = _fetcher({"https://ex.test/": HTML.encode()})
    r = pl.resolve("https://ex.test/", fetch, policy=_Refuse(), limiter=_limiter())
    assert r["status"] == "none" and r["reason"].startswith("robots:")
    assert fetch.calls == []


def test_an_unreachable_homepage_is_an_error_not_a_verdict():
    fetch = _fetcher({"https://ex.test/": OSError("timed out")})
    r = pl.resolve("https://ex.test/", fetch, policy=_Allow(), limiter=_limiter())
    assert r["status"] == "error" and "timed out" in r["reason"]


def test_verification_is_capped_so_a_page_declaring_twenty_icons_costs_at_most_max_verify():
    links = "".join(f'<link rel="icon" href="/i{i}.png" sizes="{200 - i}x{200 - i}">' for i in range(20))
    fetch = _fetcher({"https://ex.test/": f"<html><head>{links}</head></html>".encode()})  # every icon 404s
    r = pl.resolve("https://ex.test/", fetch, policy=_Allow(), limiter=_limiter())
    assert r["status"] == "none"
    assert len(fetch.calls) == 1 + pl.MAX_VERIFY


# --------------------------------------------------------------------------- #
# Scheduling + cache
# --------------------------------------------------------------------------- #
def test_pending_skips_fresh_rows_and_re_asks_a_negative_verdict_after_its_ttl():
    st = store_mod.Store("sqlite://")
    _seed(st, "Fresh Post", "fresh.test")
    _seed(st, "Stale None", "stalenone.test")
    _seed(st, "Never Asked", "never.test")
    st.upsert_publisher_logo("Fresh Post", status="ok", url="https://fresh.test/l.png", width=180,
                             height=180, at=NOW - timedelta(days=10))
    st.upsert_publisher_logo("Stale None", status="none", reason="no usable icon",
                             at=NOW - timedelta(days=31))
    names = [c["publisher"] for c in pl.pending(st, limit=10, now=NOW)]
    assert "Fresh Post" not in names
    assert "Stale None" in names, "a negative verdict is re-asked after TTL_DAYS['none']"
    assert "Never Asked" in names


def test_pending_skips_an_outlet_whose_mark_a_higher_tier_already_supplies():
    st = store_mod.Store("sqlite://")
    _seed(st, "Commons Daily", "commons.test")
    st.upsert_publisher_metadata("Commons Daily", status="ok", source="wikimedia",
                                 logo="https://upload.wikimedia.org/x/Commons_Daily.svg",
                                 logo_source="wikimedia")
    assert pl.pending(st, limit=10, now=NOW) == [], "no request to an origin for a mark we would never show"


def test_run_resolution_writes_verdicts_and_a_second_run_makes_no_requests():
    st = store_mod.Store("sqlite://")
    _seed(st, "Example Post", "ex.test")
    _seed(st, "Bare Herald", "bare.test")
    fetch = _fetcher({
        "https://ex.test/": HTML.encode(),
        "https://ex.test/mark.svg": b"<svg xmlns='http://www.w3.org/2000/svg'/>",
        "https://bare.test/": b"<html><head></head></html>",
    })
    summary = pl.run_resolution(st, fetch_bytes=fetch, limit=10, policy=_Allow(), limiter=_limiter(), now=NOW)
    assert summary["considered"] == 2 and summary["byStatus"] == {"ok": 1, "none": 1}
    ok = st.publisher_logo("Example Post")
    assert ok["status"] == "ok" and ok["url"] == "https://ex.test/mark.svg" and ok["source"] == "site"
    assert st.publisher_logo("Bare Herald")["status"] == "none"

    n = len(fetch.calls)
    again = pl.run_resolution(st, fetch_bytes=fetch, limit=10, policy=_Allow(), limiter=_limiter(), now=NOW)
    assert again["considered"] == 0 and len(fetch.calls) == n, "idempotent: fresh verdicts cost nothing"


def test_site_for_prefers_the_observed_host_and_falls_back_to_the_registry_domain():
    st = store_mod.Store("sqlite://")
    _seed(st, "Example Post", "ex.test")
    assert pl.site_for(st, "Example Post") == "https://ex.test/"
    # A curated outlet that has not published anything yet still has a home in the registry.
    assert pl.site_for(st, "Fox News") == "https://foxnews.com/"
    assert pl.site_for(st, "Totally Unknown Local Herald") is None


# --------------------------------------------------------------------------- #
# Serving — the tier order, and the story rows
# --------------------------------------------------------------------------- #
def test_commons_beats_site_and_site_beats_nothing():
    site = {"status": "ok", "url": "https://ex.test/icon.png"}
    assert pl.best_enriched(("https://commons/x.svg", "wikimedia"), site) == ("https://commons/x.svg", "wikimedia")
    assert pl.best_enriched(None, site) == ("https://ex.test/icon.png", "site")
    assert pl.best_enriched(None, {"status": "none"}) is None
    assert pl.best_enriched(None, None) is None


def test_attach_coverage_logos_puts_the_verified_mark_first_and_keeps_the_guesses_as_fallbacks():
    st = store_mod.Store("sqlite://")
    st.upsert_publisher_logo("Example Post", status="ok", url="https://ex.test/icon-192.png",
                             width=192, height=192, at=NOW)
    rows = [{"publisher": "Example Post", "url": "https://ex.test/a1"},
            {"publisher": "Unresolved Herald", "url": "https://herald.test/b1"}]
    pl.attach_coverage_logos(st, rows)
    assert rows[0]["publisherLogo"] == "https://ex.test/icon-192.png"
    assert rows[0]["publisherLogoFallbacks"] == ["https://ex.test/apple-touch-icon.png",
                                                 "https://ex.test/apple-touch-icon-precomposed.png",
                                                 "https://ex.test/favicon.ico"]
    # Nobody resolved the Herald: the client gets the same guessed walk it always had.
    assert rows[1]["publisherLogo"] == "https://herald.test/apple-touch-icon.png"


# --------------------------------------------------------------------------- #
# The poller adapter
# --------------------------------------------------------------------------- #
def test_the_adapter_is_off_unless_an_operator_says_so(monkeypatch):
    import sources
    monkeypatch.delenv("RWE_PUBLISHER_LOGOS", raising=False)
    assert sources.PublisherLogoResolver().enabled() is False
    monkeypatch.setenv("RWE_PUBLISHER_LOGOS", "1")
    assert sources.PublisherLogoResolver().enabled() is True


def test_the_adapter_reports_resolution_counters_and_fails_soft():
    import sources
    st = store_mod.Store("sqlite://")
    _seed(st, "Example Post", "ex.test")
    _seed(st, "Down Gazette", "down.test")
    fetch = _fetcher({"https://ex.test/": HTML.encode(),
                      "https://ex.test/mark.svg": b"<svg xmlns='http://www.w3.org/2000/svg'/>",
                      "https://down.test/": OSError("unreachable")})
    adapter = sources.PublisherLogoResolver(fetch_bytes=fetch, policy=_Allow(), limiter=_limiter())
    agg = adapter.poll_once(st, None)
    assert agg["considered"] == 2 and agg["resolved"] == 1 and agg["lookupErrors"] == 1
    # A per-publisher failure is a counter; the cycle succeeded.
    assert agg["errors"] == [] and agg["ok"] == 1
    assert adapter.health_key == "site://publisher-logos"


def test_an_outlet_is_never_given_our_own_mark_at_resolution_or_at_serving():
    """An outlet's logo is the outlet's. A page that (somehow) declares an icon on our host is
    skipped without a request; a stored row pointing at us is served as nothing at all."""
    html = '<html><head><link rel="icon" href="https://hidden-view.com/icon.png" sizes="192x192"></head></html>'
    fetch = _fetcher({"https://ex.test/": html.encode(), "https://hidden-view.com/icon.png": _png(192, 192)})
    r = pl.resolve("https://ex.test/", fetch, policy=_Allow(), limiter=_limiter())
    assert r["status"] == "none" and "https://hidden-view.com/icon.png" not in fetch.calls
    assert pl.logo_tuple({"status": "ok", "url": "https://app.hidden-view.com/x.png"}) is None
    assert pl.logo_tuple({"status": "ok", "url": "http://localhost:3100/x.png"}) is None
    assert pl.logo_tuple({"status": "ok", "url": "https://ex.test/icon.png"}) == ("https://ex.test/icon.png", "site")
