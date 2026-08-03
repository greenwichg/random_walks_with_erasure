"""Story Continuation — the offer a reader gets when they come BACK from an article.

Design: ``docs/STORY_CONTINUATION_DESIGN.md`` (approved, frozen).

The reader clicks "Read article", the publisher's site opens in a new tab, and some minutes later
they return to the Hidden View tab. At that moment — and only at that moment — the card they read
from can offer one thing: another outlet's account of the SAME event, from the opposite side of the
rated spectrum. This module answers the single question that makes the offer possible:

    given a canonical URL the reader just opened, is there such a sibling, and which one?

It is a pure resolver: a dict lookup over the story index the feed path already builds and caches,
a scan of that cluster's members, and the registry leans those members already carry. **No new
table, no worker, no model, no network, no writes.** Every input is something another part of the
product already computed.

What this module is NOT: it touches no recommendation machinery. No blend-plan slot, no
``DEFAULT_BLEND_PLAN`` total, no ``blend_plan_for`` arithmetic, no ``rec_explain`` parity, no
explanation ladder. The feed's own one-card story slot (``personalize._apply_story_slot``) is
unchanged and remains the fallback for readers who return in a later session. That isolation is the
main architectural argument for this surface.

Gates (design §3). Seven of the nine are decidable from server state and live here; the last two
are client facts by construction and live in the browser:

    1 cluster membership    the read URL resolves in ``evidence_resolver.story_index``
    2 cluster trust         ``clusterTrust == "ok"`` — a welded cluster offers a DIFFERENT event
    3 genre                 not a template/mill cluster (``coverage_comparison._is_template_cluster``)
    4 sibling exists        unread, different publisher IDENTITY, usable absolute URL
    5 both rated            anchor AND sibling carry a registry lean — never infer opposition
    6 genuinely opposing    ``evidence_resolver.opposing_leans`` (+-0.5 buckets; centre opposes nothing)
    7 freshness             read age <= 4 h
    - 8 not dismissed       localStorage ``hv.continue`` — the server has no such state
    - 9 chain cap           sessionStorage — one continuation per story per session

Ranking (design §4) is deterministic and total, so the same reader asking twice gets the same
answer: openness-directed lean distance, then publisher novelty, then recency, then canonical URL.

Read-only over the store + the Story Service. Never clusters inline (``build_inline`` stays False):
this runs on a click path, and a boot-window miss must cost nothing rather than ~24 s.
"""
from __future__ import annotations

import os
import pathlib
import sys
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import clustering                      # noqa: E402  parse_time — the shared timestamp parser
import coverage_comparison             # noqa: E402  the template/mill genre gate, not a copy of it
import evidence_resolver as er         # noqa: E402  story_index + the +-0.5 opposition test
import publisher_identity              # noqa: E402  one outlet, one identity, whatever name form
from ingest import canonical_url as _canon   # noqa: E402

#: How long after a read the offer still makes sense, in hours (design §0.4). Four hours, not the
#: 90 minutes a strict memory-decay reading would give: a reader returning after lunch still
#: remembers the gist, and showing slightly stale costs less than never showing. The window's real
#: job is preventing a strip about something read on Tuesday from a tab left open all week.
#: Instrumented (``continuation_opened.minutesSinceRead``) so the decay curve can replace the guess.
FRESHNESS_HOURS = 4.0

#: Political Openness continues to control ONLY the RWE-B bridge-slot budget in the feed. It does
#: not gate this feature — a reader at slider 0 still sees continuations, because the strip responds
#: to their own reading act rather than injecting into their feed. Its sole influence here is WHICH
#: opposing candidate wins when several qualify (design §4.1):
#:
#:     0-37    nearest opposing outlet  — genuine opposition, gentlest available
#:     38-62   no distance preference; novelty ranks first (the default)
#:     63-100  furthest rated opposing outlet — the sharpest contrast available
#:
#: Plateaus rather than a continuous ramp because the output is a sort DIRECTION, not a magnitude:
#: there is no meaningful "0.3 of the way toward preferring distance".
_NEAREST_MAX = 37
_FURTHEST_MIN = 63

#: Publisher novelty ranks, best first — an outlet the reader has never read is the better offer.
_NOVELTY_NEVER, _NOVELTY_RARELY, _NOVELTY_FAMILIAR = 0, 1, 2

#: Reads at or below this share of the reader's history count as "rarely read".
_RARELY_SHARE = 0.05


def enabled() -> bool:
    """Kill switch, OFF by default. Unlike Coverage Comparison (a pure recount of numbers already
    on the page), this adds a surface the reader has never seen, so it ships dark and turns on per
    the rollout plan — the same pattern ``RWE_STORY_SLOT`` follows."""
    return os.environ.get("RWE_STORY_CONTINUATION", "").strip().lower() in {
        "1", "true", "yes", "on"}


def freshness_hours() -> float:
    """The freshness window, overridable while §7's decay curve is still being measured."""
    try:
        v = float(os.environ.get("RWE_CONTINUATION_MAX_AGE_H", FRESHNESS_HOURS))
    except (TypeError, ValueError):
        return FRESHNESS_HOURS
    return v if v > 0 else FRESHNESS_HOURS


def distance_preference(openness) -> int:
    """``-1`` prefer the NEAREST opposing outlet / ``0`` no preference / ``+1`` prefer the FURTHEST.

    A missing or unparseable slider is the default 50 — the untouched-slider value — so a reader who
    never moved it gets exactly the novelty-first ordering."""
    try:
        v = float(openness)
    except (TypeError, ValueError):
        return 0
    if v != v:                                           # NaN
        return 0
    if v <= _NEAREST_MAX:
        return -1
    if v >= _FURTHEST_MIN:
        return 1
    return 0


class _Desc:
    """Descending order for a string inside an otherwise-ascending sort key (newest first).

    The alternative — several stable passes in reverse key order — spreads one ordering rule across
    three statements, and this feature's whole ranking claim is that it is ONE readable total order.
    """

    __slots__ = ("s",)

    def __init__(self, s: str):
        self.s = s

    def __lt__(self, other: "_Desc") -> bool:
        return self.s > other.s

    def __eq__(self, other) -> bool:
        return isinstance(other, _Desc) and self.s == other.s

    def __hash__(self) -> int:
        return hash(self.s)

    def __repr__(self) -> str:                           # pragma: no cover - debugging aid
        return f"_Desc({self.s!r})"


def _abs_url(raw) -> str:
    """The member's URL if it is a usable absolute http(s) link, else ``""``. A relative or
    scheme-less string is not something we can open in a new tab, and offering one would be a
    broken promise rather than a missing feature."""
    u = str(raw or "").strip()
    low = u.lower()
    return u if low.startswith("http://") or low.startswith("https://") else ""


def _lean_of(member: dict):
    """The member's registry lean as a float, or ``None`` when unrated. Unrated licenses no claim
    (L2.2) — never a 0.0 default, which would read as "rated centre"."""
    try:
        v = float(member.get("lean"))
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def _reader_state(store_, user_id: int) -> tuple:
    """``(read_at_by_url, reads_by_publisher, total_reads)`` from ONE store query.

    Uses ``list_reads`` rather than ``get_reads`` because the freshness gate needs WHEN, and the
    display projection already carries it. Server ``createdAt`` is preferred over the
    client-supplied ``observedAt``: a device with a wrong clock must not be able to widen its own
    freshness window."""
    read_at: dict = {}
    by_pub: dict = {}
    total = 0
    for r in store_.list_reads(user_id) or []:           # newest first
        u = _canon(str(r.get("canonicalUrl") or ""))
        if u and u not in read_at:                       # first row for a URL is its newest read
            read_at[u] = (clustering.parse_time(r.get("createdAt"))
                          or clustering.parse_time(r.get("observedAt")))
        p = str((r.get("scored") or {}).get("publisher") or "")
        if p:
            by_pub[p] = by_pub.get(p, 0) + 1
        total += 1
    return read_at, by_pub, total


def _novelty_rank(publisher: str, by_pub: dict, total: int) -> int:
    """How unfamiliar this outlet is to this reader: never read < rarely read < familiar."""
    n = int(by_pub.get(publisher, 0))
    if n <= 0:
        return _NOVELTY_NEVER
    if total > 0 and (n / total) <= _RARELY_SHARE:
        return _NOVELTY_RARELY
    return _NOVELTY_FAMILIAR


def _candidates(members: list, anchor_url: str, anchor: dict, read_urls: set) -> list:
    """Every cluster member that clears gates 4-6 against this anchor, unranked.

    Publisher IDENTITY, not publisher name: a syndication network filing as ~100 hostnames, or one
    masthead arriving as both ``Sportskeeda`` and ``Sportskeeda.Com``, is ONE outlet and offers no
    second account of anything."""
    anchor_lean = _lean_of(anchor)
    if anchor_lean is None:                              # gate 5, anchor half
        return []

    names = {str(m.get("publisher") or "") for m in members if m.get("publisher")}
    try:
        ident = publisher_identity.groups(names)
    except Exception:                       # identity is an enhancement, never a hard dependency
        ident = {}

    def key(name) -> str:
        raw = str(name or "").strip()
        return ident.get(raw) or raw.lower()

    anchor_key = key(anchor.get("publisher"))
    out = []
    for m in members:
        url = _abs_url(m.get("url"))
        if not url:                                      # gate 4: no usable link
            continue
        cu = _canon(url)
        if cu == anchor_url or cu in read_urls:          # gate 4: unread, and not the anchor itself
            continue
        if key(m.get("publisher")) == anchor_key:        # gate 4: a different OUTLET
            continue
        lean = _lean_of(m)
        if lean is None:                                 # gate 5, sibling half
            continue
        if not er.opposing_leans(anchor_lean, lean):     # gate 6
            continue
        out.append({"url": url, "canonicalUrl": cu, "publisher": str(m.get("publisher") or ""),
                    "headline": str(m.get("headline") or ""), "lean": lean,
                    "leanBucket": m.get("leanBucket"),
                    "publishedAt": str(m.get("publishedAt") or ""),
                    "distance": abs(lean - anchor_lean)})
    return out


def rank(cands: list, direction: int, by_pub: dict, total: int) -> list:
    """Design §4's sort key, applied as a TOTAL order so the result is reproducible.

    ``(-direction * distance, novelty, newest-first, canonical url)``. With ``direction == 0`` the
    distance term is constant and novelty leads — exactly the 38-62 plateau's rule. The canonical
    URL is the final tiebreak: two members with the same distance, novelty and timestamp must still
    order deterministically, or the same reader asking twice gets different answers."""
    return sorted(cands, key=lambda c: (-direction * c["distance"],
                                        _novelty_rank(c["publisher"], by_pub, total),
                                        _Desc(c["publishedAt"]),
                                        c["canonicalUrl"]))


def resolve(store_, user_id: int, url: str, *, openness=50, index: Optional[dict] = None,
            now=None) -> Optional[dict]:
    """The one continuation offer for ``url``, or ``None`` when any server-side gate fails.

    ``None`` is the overwhelmingly common answer and is not an error: the strict gates are the
    point. The caller renders nothing — no placeholder, no "no comparison available".

    ``index`` lets a caller pass a story index it already holds; the default reads the TTL-cached
    one the feed path warms. ``now`` is injectable for tests only.
    """
    if store_ is None:
        return None
    anchor_url = _canon(str(url or ""))
    if not anchor_url:
        return None

    idx = er.story_index(store_) if index is None else index
    story = (idx or {}).get(anchor_url)
    if not story:                                        # gate 1
        return None
    if str(story.get("clusterTrust") or "") != "ok":     # gate 2
        return None

    members = story.get("coverage") or []
    if coverage_comparison._is_template_cluster(members):   # gate 3
        return None

    anchor = next((m for m in members
                   if _canon(str(m.get("url") or "")) == anchor_url), None)
    if anchor is None:                       # index and coverage disagree — say nothing
        return None

    read_at, by_pub, total_reads = _reader_state(store_, user_id)

    # Gate 7 — freshness. A read this reader has NOT recorded yet is the prefetch race, not a stale
    # read: the click IS the read, and the POST that records it is in flight alongside this lookup.
    # Treating that as fresh is the honest reading; treating it as ineligible would fail the common
    # case. A read we DO know about is measured against the server's own clock.
    at = read_at.get(anchor_url)
    if at is not None:
        ref = now or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        if (ref - at).total_seconds() / 3600.0 > freshness_hours():
            return None

    cands = _candidates(members, anchor_url, anchor, set(read_at))
    if not cands:                                        # gates 4-6
        return None

    best = rank(cands, distance_preference(openness), by_pub, total_reads)[0]
    return {
        "storyId": story.get("storyId"),
        "storyTitle": story.get("title"),
        "outlets": int(story.get("publisherCount") or 0),
        "anchor": {"url": str(anchor.get("url") or ""),
                   "publisher": str(anchor.get("publisher") or ""),
                   "lean": _lean_of(anchor), "leanBucket": anchor.get("leanBucket")},
        "sibling": {"url": best["url"], "publisher": best["publisher"],
                    "headline": best["headline"], "lean": best["lean"],
                    "leanBucket": best["leanBucket"], "publishedAt": best["publishedAt"]},
        "distance": round(best["distance"], 3),
        "candidateCount": len(cands),
    }
