"""robots.py — who we say we are, and whether a publisher has told us not to.

**Stdlib only, and deliberately so.** ``crawler.py`` imports ``rss_ingest``, so the robots rules
cannot live in ``crawler`` if the live ingestion path is to use them — that is a cycle. They live
here, both sides import this, and there is **one** definition of "may we fetch this" rather than a
strict one in a POC that has never run and none at all in the poller that runs every cycle.

## Why this module exists at all (F1/F2 of the M7 Stage 2 audit)

Two findings, both about the code that is live **today** rather than the code that is blocked:

* **F1 — the live ingestion path had no robots gate.** ``rss_ingest``, ``sources``, ``feed_service``
  and ``feed_schedule`` contained no reference to robots. It existed only in ``crawler.py``, in M7's
  validation modules, and in ``verify_crawler_config.py`` — **none of which has ever run against a
  real host.** The unrun proof-of-concept was more compliant than production.
* **F2 — we misidentified ourselves.** The RSS poller's User-Agent was
  ``InformationHealth-RSS/0.1 (+https://code.claude.com)``, pointing at a documentation site
  belonging to another organisation entirely. A publisher trying to find out who was polling them,
  or to ask us to stop, was sent to the wrong company. The crawler POC's agent named
  ``hidden-view.com/crawler``, which did not exist.

"We respect robots.txt" was not a true statement about Hidden View. This module is what makes it
one; the ``/crawler`` page and ``/robots.txt`` route make the agent string resolve to something.

## The posture, and where it deliberately differs from the crawler POC

``crawler.RobotsPolicy`` is **fail-closed**: an absent or unparseable policy is a refusal, on the
stated grounds that "no robots.txt means crawl freely" is a search engine's decades-old norm and not
a reasonable reading for a commercial reader of newsrooms it has never spoken to. That is right for
**discovery** — a one-shot probe of a stranger.

A recurring poll of an operator-chosen feed is a different act with a different failure cost, so the
outcomes are separated into three rather than two:

``allowed``               a policy exists and permits us.
``disallowed``            a policy exists and refuses us. **Enforced by default.** This is a real
                          answer from the publisher and honouring it is the whole point.
``unknown``               no readable policy — unreachable, 5xx, TLS failure, or a 200 that is not a
                          robots policy. **Reported, not enforced, by default.**

The split matters because the two failure modes are not symmetric. Refusing on *unknown* means a
CDN hiccup silently stops ingestion from a publisher who never objected; allowing on *unknown* means
a brief window where we poll someone whose objection we could not read, which self-corrects the
moment the file is readable again. RFC 9309 treats unreachability and prohibition as distinct for
the same reason.

``RWE_ROBOTS_STRICT=1`` adopts the crawler's fail-closed posture on the live path too, for an
operator who wants it. ``RWE_ROBOTS_ENFORCE=0`` disables the gate entirely — a kill switch, because
a change that can stop ingestion needs one, and the flag being *present* in the compose allowlist is
what makes it usable in an incident.

## Caching

RFC 9309 §2.4 asks crawlers not to use a cached robots.txt for more than 24 hours, and permits a
longer fallback when the file is unreachable. :data:`CACHE_TTL_SECONDS` is that 24 hours. Without a
cache, gating every feed fetch would fetch robots.txt once per feed per poll cycle — hundreds of
needless requests a day onto the newsrooms this is meant to be considerate of.
"""

from __future__ import annotations

import os
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from typing import Callable, Optional

#: Where a publisher goes to find out who we are and how to stop us. This URL **must serve a real
#: page** — an agent string pointing at a 404 is barely better than one pointing at the wrong
#: company. Served by `web/app/crawler/page.tsx`.
CONTACT_URL = "https://hidden-view.com/crawler"


def user_agent(product: str, version: str = "0.1") -> str:
    """The one place a User-Agent is composed, so every path identifies the same organisation.

    Format follows the convention publishers' log-analysis tooling expects: a product token, a
    version, and a ``+URL`` pointing at a page describing the agent."""
    return f"HiddenView-{product}/{version} (+{CONTACT_URL})"


#: How long a fetched policy may be reused. RFC 9309 §2.4: not more than 24 hours.
CACHE_TTL_SECONDS = 24 * 3600

_ENFORCE_ENV = "RWE_ROBOTS_ENFORCE"
_STRICT_ENV = "RWE_ROBOTS_STRICT"


class RobotsRefused(Exception):
    """A publisher's robots.txt refuses this fetch. Raised at the transport seam, so a caller that
    catches it can count a refusal separately from a network failure — they mean opposite things and
    an aggregate that merged them would hide the one that matters."""


@dataclass
class RobotsDecision:
    allowed: bool
    reason: str
    crawl_delay: Optional[float] = None
    #: Whether a policy was actually READ. ``False`` means we do not know rather than that we were
    #: refused. Appended last so every existing positional construction is unaffected, and the
    #: crawler's fail-closed reading of ``allowed`` alone is byte-identical to what it was.
    known: bool = True


def _looks_like_robots(body: "str | None") -> bool:
    """Whether a 200 response is actually a robots policy.

    This check is load-bearing, not defensive tidiness. ``RobotFileParser`` parses an HTML 404 page,
    a captive-portal login, or a CDN error into a policy with **no rules**, and a policy with no
    rules answers ``can_fetch`` with *True* — so without this, the most common way for robots.txt to
    be unavailable (a server that returns 200 and a web page for everything) reads as blanket
    permission. That is fail-OPEN wearing the costume of fail-closed.

    The bar is one ``User-agent:`` line. A whitespace-only body is refused too: it is a valid
    allow-all in principle, but an empty 200 is also what a broken origin returns, and a publisher
    who means "allow everything" writes ``User-agent: *``.
    """
    for line in (body or "").splitlines():
        if line.split("#", 1)[0].strip().lower().startswith("user-agent:"):
            return True
    return False


def _plain_fetch(url: str, *, timeout: float = 10.0) -> str:
    """A dependency-free GET, so this module stays stdlib-only and importable from anywhere.

    Callers that want the shared 429/5xx retry ladder inject their own fetcher —
    ``crawler.RobotsPolicy`` does exactly that. A retry ladder is not obviously right here anyway:
    robots.txt is small, and a failure produces ``unknown``, which is already the honest answer."""
    req = urllib.request.Request(url, headers={"User-Agent": user_agent("Robots"),
                                               "Accept": "text/plain, */*;q=0.5"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _why_unreachable(exc: Exception) -> str:
    """A reason precise enough to act on.

    The first live probe reported ``robots.txt unavailable (HTTPError)`` for a host, and that string
    cannot be acted on: ``HTTPError`` covers 404, 403 and 5xx, which mean entirely different things.

      * **404** — there is no robots.txt. RFC 9309 reads that as no restrictions, and it is the
        single most common case on small sites.
      * **403** — the origin refused *us*. That is a **stronger** signal than a ``Disallow`` line,
        not a weaker one, and it should never be filed under "temporarily unavailable".
      * **5xx / timeout / TLS** — an outage. It says nothing about permission either way.

    The posture does not change here: `CRAWLER_DESIGN.md` deliberately declines the
    404-means-crawl-freely convention for a commercial reader of newsrooms, so all three still fail
    closed for discovery. What changes is that the operator can now tell them apart, which is the
    difference between "this publisher has no robots.txt" and "this publisher blocked us"."""
    code = getattr(exc, "code", None)
    if code is not None:
        return f"HTTP {code}"
    reason = getattr(exc, "reason", None)
    return f"{type(exc).__name__}: {reason}" if reason else type(exc).__name__


def read_policy(host: str, fetch: Callable[[str], str]) -> tuple:
    """``(RobotFileParser | None, reason)`` for one host. The single fetch-and-parse definition,
    shared by :class:`RobotsPolicy` and the live gate so the two cannot drift on what counts as a
    readable policy."""
    url = f"https://{host}/robots.txt"
    try:
        body = fetch(url)
    except Exception as e:                       # unreachable, 4xx/5xx, TLS failure, timeout
        return None, f"robots.txt unavailable ({_why_unreachable(e)})"
    if not _looks_like_robots(body):
        return None, "robots.txt is not a robots policy"
    rp = urllib.robotparser.RobotFileParser()
    try:
        rp.parse(body.splitlines())
    except Exception as e:                       # a body that is not robots.txt at all
        return None, f"robots.txt unparseable ({type(e).__name__})"
    return rp, ""


class RobotsPolicy:
    """Per-host robots.txt, fetched once and cached.

    ``fetch(url) -> str`` is injected so this is testable without a network, and so the one place
    that talks to a publisher's origin stays visible.
    """

    def __init__(self, fetch: "Callable[[str], str] | None" = None, *,
                 user_agent: "str | None" = None):
        self._fetch = fetch or _plain_fetch
        self._user_agent = user_agent or globals()["user_agent"]("Crawler")
        self._cache: "dict[str, tuple]" = {}

    def _policy_for(self, host: str):
        if host in self._cache:
            return self._cache[host]
        self._cache[host] = entry = read_policy(host, self._fetch)
        return entry

    def check(self, url: str) -> RobotsDecision:
        """Whether we may fetch ``url``, how long to wait between requests to its host, and — via
        ``known`` — whether a policy was read at all.

        An absent policy still reports ``allowed=False``, so every existing fail-closed caller is
        unchanged. What is new is that it also reports ``known=False``, which lets the live poller
        tell "the publisher said no" from "we could not ask".
        """
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if not host:
            return RobotsDecision(False, "no host", known=False)
        rp, err = self._policy_for(host)
        if rp is None:
            return RobotsDecision(False, err or "no robots policy", known=False)
        try:
            ok = rp.can_fetch(self._user_agent, url)
        except Exception as e:
            return RobotsDecision(False, f"robots evaluation failed ({type(e).__name__})",
                                  known=False)
        delay = None
        try:
            d = rp.crawl_delay(self._user_agent)
            delay = float(d) if d is not None else None
        except Exception:
            delay = None
        return RobotsDecision(bool(ok), "" if ok else "disallowed by robots.txt", delay)


# --------------------------------------------------------------------------- #
# The live gate — a TTL'd module singleton, because the poller is a recurring caller.
# --------------------------------------------------------------------------- #
#: host -> (RobotFileParser | None, reason, fetched_at). A failed refresh NEVER evicts a policy we
#: previously read: RFC 9309 permits using a cached policy beyond the TTL while robots.txt is
#: unreachable, and it is what /crawler promises publishers ("we keep to the last policy we
#: successfully read"). A page that says one thing while the code does another is worse than no page.
_live: "dict[str, tuple]" = {}
_live_fetch: "Callable[[str], str]" = _plain_fetch


def _flag(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def enforcing() -> bool:
    """Whether the gate blocks at all. Default on; ``RWE_ROBOTS_ENFORCE=0`` is the kill switch."""
    return _flag(_ENFORCE_ENV, True)


def strict() -> bool:
    """Whether an UNKNOWN policy also refuses — the crawler POC's fail-closed posture, off by
    default on the live path. See the module docstring for why the two paths differ."""
    return _flag(_STRICT_ENV, False)


def reset_cache(*, fetch: "Callable[[str], str] | None" = None) -> None:
    """Drop the cached policies. For tests, and for an operator who has just changed configuration.
    ``fetch`` swaps the transport the live gate uses — the seam tests drive it through."""
    global _live_fetch
    _live.clear()
    _live_fetch = fetch or _plain_fetch


def _live_entry(host: str) -> tuple:
    """The cached policy for ``host``, refreshed when stale, retaining the last good one on failure."""
    entry = _live.get(host)
    now = time.monotonic()
    if entry is not None and (now - entry[2]) <= CACHE_TTL_SECONDS:
        return entry
    rp, reason = read_policy(host, _live_fetch)
    if rp is None and entry is not None and entry[0] is not None:
        # Refresh failed but we HAVE read this publisher's policy before. Keep it and reset the
        # clock so we try again next cycle rather than discarding a real answer over a transient.
        entry = (entry[0], entry[1], now)
    else:
        entry = (rp, reason, now)
    _live[host] = entry
    return entry


def check(url: str, *, policy: "RobotsPolicy | None" = None) -> RobotsDecision:
    """The decision for ``url``, without acting on it. ``policy`` is injectable for tests."""
    if policy is not None:
        return policy.check(url)
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if not host:
        return RobotsDecision(False, "no host", known=False)
    rp, reason, _at = _live_entry(host)
    if rp is None:
        return RobotsDecision(False, reason or "no robots policy", known=False)
    agent = user_agent("RSS")
    try:
        ok = rp.can_fetch(agent, url)
    except Exception as e:
        return RobotsDecision(False, f"robots evaluation failed ({type(e).__name__})", known=False)
    try:
        d = rp.crawl_delay(agent)
        delay = float(d) if d is not None else None
    except Exception:
        delay = None
    return RobotsDecision(bool(ok), "" if ok else "disallowed by robots.txt", delay)


def enforce(url: str, *, policy: "RobotsPolicy | None" = None) -> RobotsDecision:
    """Raise :class:`RobotsRefused` when this fetch must not happen; otherwise return the decision.

    Refuses on an explicit ``Disallow`` always, and on an unreadable policy only under
    :func:`strict`. Returns the decision either way so a caller can report ``unknown`` without
    having to re-ask."""
    decision = check(url, policy=policy)
    if not enforcing():
        return decision
    if decision.allowed:
        return decision
    if decision.known or strict():
        raise RobotsRefused(f"{url}: {decision.reason}")
    return decision
