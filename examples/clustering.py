"""clustering.py — the deterministic, dependency-free clustering primitive.

A reusable union-find grouping over items by token (Jaccard) similarity within a time window. No LLM,
no external dependency, fully deterministic (same input → same groups, same order). It groups **item
indices** and knows nothing about Stories or FeedArticle — story construction lives in
``story_service.py``; this module only decides *what clusters with what*.

Extracted from the original Discover implementation so both Discover and Stories share one algorithm.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

DEFAULT_SIM = 0.28
DEFAULT_WINDOW_DAYS = 6.0

#: Minimum DISTINCTIVE tokens two headlines must share before similarity is even considered.
#: The ratio alone cannot tell evidence from coincidence — measured on real merges:
#:   "Berlin pride event canceled…" vs "Vehicle drives into crowd at Berlin pride event"
#:        jaccard 0.86, 6 shared tokens  -> the same event
#:   "Trump wins Ohio" vs "Trump wins Iowa"
#:        jaccard 0.50, 2 shared tokens  -> DIFFERENT events, and no stop-list can fix it
#: Shared-token COUNT separates those two; the ratio does not.
MIN_SHARED_TOKENS = 3

#: A headline with fewer content tokens than this does not cluster at all. Below ~3 words the
#: Jaccard of a tiny set is dominated by whichever few words survive, so it measures little.
MIN_TITLE_TOKENS = 3

# Small English stop-list for title-similarity — enough to keep function words from inflating overlap
# without pulling in a stemming dependency.
_STOPWORDS = frozenset("""
a an and the of to in on for with from by at as is are was were be been being this that these those
it its his her their our your my we you they he she who what when where why how than then so but or
not no nor into over under after before amid amto about says say said new latest live update updates
""".split())

# Calendar and editorial filler. These are what made recurring columns and daily round-ups collapse
# into single clusters: "Local news in brief, July 21" and "…July 22" reduced to the SAME four
# tokens {brief, july, local, news} — jaccard 1.00 on nothing but boilerplate. Measured in
# production, that merged 65 articles from 42 publishers into one "story".
_STOPWORDS = _STOPWORDS | frozenset("""
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec
monday tuesday wednesday thursday friday saturday sunday
news brief briefs briefing roundup round wrap recap digest bulletin headlines
today yesterday tomorrow week weekly daily morning evening tonight edition
best top since things
""".split())


def title_tokens(title: str) -> frozenset:
    """Content word tokens of a headline (lowercased, length > 2, stop-words removed).

    Pure numbers are dropped: in a headline a bare number is nearly always a count, a date or a
    listicle rank ("6 Best… Since 2010"), not the thing the story is about. It is a real trade —
    "737" in an aircraft story is lost — but a shared year linking two unrelated listicles is the
    commoner case by far."""
    toks = re.findall(r"[a-z0-9]+", (title or "").lower())
    return frozenset(t for t in toks
                     if len(t) > 2 and not t.isdigit() and t not in _STOPWORDS)


def jaccard(a: frozenset, b: frozenset) -> float:
    """|A ∩ B| / |A ∪ B|, or 0 for an empty set / no overlap."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b) if inter else 0.0


def parse_time(iso: str) -> Optional[datetime]:
    s = (iso or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def within_window(a: Optional[datetime], b: Optional[datetime], days: float) -> bool:
    """Whether two times are within ``days`` of each other. Missing timestamps never block a match."""
    if a is None or b is None:
        return True
    return abs((a - b).total_seconds()) <= days * 86400.0


class DSU:
    """Union-find with lower-index roots, so cluster roots (and therefore output order) are stable."""

    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)   # attach to the lower index → deterministic roots


def cluster(items: Sequence, *, tokens: Callable[[object], frozenset],
            time: Callable[[object], Optional[datetime]],
            sim: float = DEFAULT_SIM, window_days: float = DEFAULT_WINDOW_DAYS,
            min_shared: int = MIN_SHARED_TOKENS,
            min_tokens: int = MIN_TITLE_TOKENS) -> "list[list[int]]":
    """Group item **indices** into clusters. ``tokens(item) → frozenset`` and
    ``time(item) → datetime | None`` are the accessors. Two items join the same cluster when their
    token Jaccard ≥ ``sim`` **and** their times are within ``window_days``. Returns a list of clusters,
    each a list of indices into ``items``; deterministic in membership and order.

    Candidate generation is **blocked by an inverted token index** rather than all-pairs. This is an
    exact optimisation, not an approximation: ``jaccard(a, b) ≥ sim`` for any ``sim > 0`` requires
    ``|a ∩ b| ≥ 1``, so a pair sharing no token can never match and is safe to skip. Only pairs that
    share at least one token are scored, which is what makes the *whole* catalog clusterable —
    all-pairs made the caller cap the input, and that cap (counted in items, not time) silently
    narrowed as ingestion grew, collapsing the story count.

    Cost is O(Σ_t |postings(t)|²) rather than O(n²) — near-linear for headlines, whose content tokens
    are mostly rare. It degrades toward all-pairs only if every item shares a token with every other,
    which cannot be worse than the previous behaviour.
    """
    n = len(items)
    toks = [tokens(it) for it in items]
    times = [time(it) for it in items]

    # token -> ascending item indices carrying it. Built once; membership tests below stay exact.
    postings: dict = {}
    for i, t in enumerate(toks):
        for tok in t:
            postings.setdefault(tok, []).append(i)

    dsu = DSU(n)
    for i in range(n):
        ti = toks[i]
        if len(ti) < max(1, min_tokens):
            continue                                    # too little to say anything: stays a singleton
        # Walking the postings counts SHARED TOKENS per candidate as a by-product, so the
        # min_shared gate costs nothing extra — and it prunes most pairs before any Jaccard.
        shared: dict = {}
        for tok in ti:
            for j in postings[tok]:
                if j > i:
                    shared[j] = shared.get(j, 0) + 1
        for j, overlap in shared.items():
            if overlap < min_shared or len(toks[j]) < max(1, min_tokens):
                continue
            if jaccard(ti, toks[j]) >= sim and within_window(times[i], times[j], window_days):
                dsu.union(i, j)

    groups: dict = {}
    for i in range(n):
        groups.setdefault(dsu.find(i), []).append(i)
    return list(groups.values())
