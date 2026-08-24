"""clustering.py — the deterministic, dependency-free clustering primitive.

A reusable union-find grouping over items by token (Jaccard) similarity within a time window. No LLM,
no external dependency, fully deterministic (same input → same groups, same order). It groups **item
indices** and knows nothing about Stories or FeedArticle — story construction lives in
``story_service.py``; this module only decides *what clusters with what*.

Extracted from the original Discover implementation so both Discover and Stories share one algorithm.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from bisect import bisect_right
from collections import Counter
from typing import Callable, Optional, Sequence

DEFAULT_SIM = 0.28
DEFAULT_WINDOW_DAYS = 6.0

#: Fraction of a candidate merge's CROSS-PAIRS that must independently pass the similarity gate
#: before two clusters are joined. ``0.0`` = pure single linkage: A~B and B~C merges A, B and C even
#: when A and C share nothing. That transitive closure is what built the production mega-cluster,
#: and it is the measured production baseline — so the default stays 0.0 until a candidate value is
#: measured against the live catalog by ``examples/audit_clustering_change.py``.
DEFAULT_LINK_QUORUM = 0.0

#: Cross-pair scoring is O(|A|x|B|), so each side is sampled to at most this many members (lowest
#: indices first — deterministic). A quorum measured on 32x32 is a sample, not a census; that is a
#: deliberate cost bound, and it means the test gets *approximate* on very large clusters. Those are
#: exactly the clusters it is meant to stop forming.
LINK_SAMPLE = 32

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


def title_tokens(title: str, hyphen_compounds: bool = False) -> frozenset:
    """Content word tokens of a headline (lowercased, length > 2, stop-words removed).

    Pure numbers are dropped: in a headline a bare number is nearly always a count, a date or a
    listicle rank ("6 Best… Since 2010"), not the thing the story is about. It is a real trade —
    "737" in an aircraft story is lost — but a shared year linking two unrelated listicles is the
    commoner case by far.

    ``hyphen_compounds`` (candidate, default OFF — measured by
    ``examples/audit_clustering_change.py --hyphen-compounds`` before any adoption) ALSO emits
    each hyphenated compound joined: "X-Men" contributes "xmen" alongside whatever its fragments
    contribute. The defect it targets is real and exhibit-backed: ``[a-z0-9]+`` splits "x-men"
    into ("x", "men"), the length floor drops "x", and the franchise name the two X-Men
    announcement headlines actually share survives only as the generic token "men" — the
    xmen-pair false split. Additive only: fragments keep their existing treatment, so every
    current token survives and edges can only gain shared evidence, never lose it."""
    lower = (title or "").lower()
    toks = re.findall(r"[a-z0-9]+", lower)
    out = set(t for t in toks
              if len(t) > 2 and not t.isdigit() and t not in _STOPWORDS)
    if hyphen_compounds:
        for compound in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)+", lower):
            joined = compound.replace("-", "")
            if len(joined) > 2 and not joined.isdigit() and joined not in _STOPWORDS:
                out.add(joined)
    return frozenset(out)


#: Description tokens admitted per article when the dek joins the clustering signal. A cap, not a
#: preference: candidate generation walks token POSTINGS, so cost is O(Σ_t |postings(t)|²) and an
#: uncapped 60-token dek would multiply the posting lists that already dominate the build. Twelve is
#: the first N content words, which for a news dek is the who/what — the tail is context and
#: attribution, which is exactly the prose that makes unrelated stories look similar.
DESC_TOKEN_CAP = 12


def description_tokens(description: str, cap: int = DESC_TOKEN_CAP) -> frozenset:
    """The first ``cap`` content tokens of a dek, in order of appearance.

    Same filter as :func:`title_tokens` — the stop-list, the length floor, the pure-digit drop —
    then truncated. **Order of appearance, not rarity**: picking by IDF would need a corpus pass
    before the corpus exists, and would reintroduce the weighting whose measured revert is recorded
    in ``story_service.use_idf``. First-N is deterministic, needs no global state, and front-loads
    the entities a news dek leads with.

    Deduplication happens BEFORE the cap, not after: a repeated word is skipped and the scan
    continues, so ``cap`` counts DISTINCT tokens and a dek that says "budget" three times spends
    one of them. Repetition is not evidence, and the alternative — truncate first, dedupe into a
    frozenset after — would silently give the most repetitive deks the smallest signals.
    """
    if cap <= 0:
        return frozenset()
    seen: list = []
    for t in re.findall(r"[a-z0-9]+", (description or "").lower()):
        if len(t) > 2 and not t.isdigit() and t not in _STOPWORDS and t not in seen:
            seen.append(t)
            if len(seen) >= cap:
                break
    return frozenset(seen)


def jaccard(a: frozenset, b: frozenset) -> float:
    """|A ∩ B| / |A ∪ B|, or 0 for an empty set / no overlap."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b) if inter else 0.0


def idf_weights(token_sets: "Sequence[frozenset]") -> dict:
    """token -> ``log(1 + N/df)`` over the input set. Rare tokens weigh more than common ones.

    Why this exists: plain Jaccard treats every shared word as equal evidence, so "trump" — in
    hundreds of headlines — counts the same as "buckenham", in two. That is what let SINGLE-LINKAGE
    chaining build a 203-article cluster out of ~12 unrelated stories: each link was individually
    plausible because it rested on words that are everywhere.

    Smoothed so weights stay strictly positive: a token present in EVERY item still scores
    ``log 2``, not zero, so a small corpus (a test with two items sharing every word) degrades to
    ordinary Jaccard rather than to no similarity at all.

    Deterministic: computed from the input set the caller already passed, so the same input yields
    the same weights and the same clusters. No corpus state, no cross-run coupling."""
    n = len(token_sets)
    df: dict = {}
    for toks in token_sets:
        for t in toks:
            df[t] = df.get(t, 0) + 1
    return {t: math.log(1.0 + n / c) for t, c in df.items()}


def weighted_jaccard(a: frozenset, b: frozenset, weights: Optional[dict]) -> float:
    """Jaccard over token WEIGHTS rather than counts. ``weights=None`` falls back to plain Jaccard,
    so one call site serves both modes."""
    if weights is None:
        return jaccard(a, b)
    if not a or not b:
        return 0.0
    inter = a & b
    if not inter:
        return 0.0
    den = sum(weights.get(t, 1.0) for t in (a | b))
    return (sum(weights.get(t, 1.0) for t in inter) / den) if den else 0.0


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


def _quorum_ok(a: "list[int]", b: "list[int]", *, pair_ok: Callable[[int, int], bool],
               quorum: float) -> bool:
    """Whether enough CROSS-PAIRS between two clusters independently pass the pairwise gate.

    This is the whole difference between single linkage and cluster-aware linkage. Single linkage
    asks "does the joining article match *any* member?"; this asks "does it match *enough* of
    them?". A genuine new article about the same event resembles most of the cluster. A chaining
    bridge resembles exactly one member — the one it shares boilerplate with — and fails here.

    Two singletons always pass (one cross-pair, and it is the pair that already passed the
    similarity gate), so the quorum never blocks a story from FORMING. It only constrains growth,
    which is where chaining lives."""
    sa, sb = a[:LINK_SAMPLE], b[:LINK_SAMPLE]
    total = len(sa) * len(sb)
    if not total:
        return False
    need = math.ceil(quorum * total - 1e-9)
    hits, seen = 0, 0
    for x in sa:
        for y in sb:
            seen += 1
            if pair_ok(x, y):
                hits += 1
                if hits >= need:
                    return True
            elif hits + (total - seen) < need:
                return False                            # cannot reach the quorum any more
    return hits >= need


def cluster(items: Sequence, *, tokens: Callable[[object], frozenset],
            time: Callable[[object], Optional[datetime]],
            sim: float = DEFAULT_SIM, window_days: float = DEFAULT_WINDOW_DAYS,
            min_shared: int = MIN_SHARED_TOKENS,
            min_tokens: int = MIN_TITLE_TOKENS,
            idf: bool = False,
            link_quorum: float = DEFAULT_LINK_QUORUM,
            evidence: Optional[Callable[[int, int], bool]] = None,
            merge_ok: Optional[Callable[[list, list], bool]] = None) -> "list[list[int]]":
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

    ``link_quorum`` switches the LINKAGE RULE. At ``0.0`` (the default, and the measured production
    baseline) grouping is the transitive closure of the pairwise relation — pure single linkage.
    Above ``0.0`` a merge additionally requires that fraction of cross-pairs between the two
    clusters to pass the same pairwise gate, which is what stops one bridging article from welding
    two unrelated events together. See ``_quorum_ok``.

    The two modes differ in a property worth stating plainly. Single linkage is **order-independent**:
    transitive closure is unique, so the answer does not depend on which merge is attempted first.
    A quorum rule is not — accepting one merge changes the membership a later quorum is measured
    against. Merges are therefore consumed **best-first** (highest similarity, ties by index), so the
    ordering is a defensible one rather than an artefact of item order, and the result stays
    deterministic. It is still a greedy result, not a global optimum.

    ``evidence`` and ``merge_ok`` admit NON-LEXICAL edge evidence (X4,
    docs/STORY_ENTITY_EVIDENCE_PLAN.md) without this module learning what the evidence is — both
    receive item indices and answer yes/no, and this layer stays free of Story/country knowledge:

    * ``evidence(x, y) -> bool`` is ANDed into the pairwise gate — candidate admission, quorum
      cross-pair scoring and any caller-side re-cluster all consult the SAME predicate, so the
      evidence cannot gate one and not another. It can only remove edges, never add one.
    * ``merge_ok(a_members, b_members) -> bool`` is a CLUSTER-level gate consulted before every
      union (member index lists, cheapest test first — before any quorum scoring). Setting it
      forces the bookkeeping path even at ``link_quorum 0.0``, because a gate needs memberships;
      that path consumes merges best-first, so the result is deterministic for the same reason
      the quorum's is.

    Both default to ``None``, and ``None`` is byte-identical to the previous behaviour — the same
    opt-in discipline as ``idf`` and ``link_quorum``.
    """
    n = len(items)
    toks = [tokens(it) for it in items]
    times = [time(it) for it in items]

    # token -> ascending item indices carrying it. Built once; membership tests below stay exact.
    postings: dict = {}
    for i, t in enumerate(toks):
        for tok in t:
            postings.setdefault(tok, []).append(i)
    # Rarity weighting (opt-in): shared common words stop counting as much evidence as shared rare
    # ones. Computed from THIS input set, so determinism is unaffected.
    weights = idf_weights(toks) if idf else None
    floor = max(1, min_tokens)

    def pair_ok(x: int, y: int) -> bool:
        """The pairwise admission gate, as one predicate. The quorum test scores cross-pairs by
        exactly the rule that admitted the original pair — a weaker bar there would let cross-pairs
        that could never merge on their own count as support for merging."""
        tx, ty = toks[x], toks[y]
        if len(tx) < floor or len(ty) < floor or len(tx & ty) < min_shared:
            return False
        return (weighted_jaccard(tx, ty, weights) >= sim
                and within_window(times[x], times[y], window_days)
                and (evidence is None or evidence(x, y)))

    def candidates():
        """Yield ``(i, j, score)`` for every pair passing the pairwise gate, ``i < j``."""
        for i in range(n):
            ti = toks[i]
            if len(ti) < floor:
                continue                                # too little to say anything: stays a singleton
            # Walking the postings counts SHARED TOKENS per candidate as a by-product, so the
            # min_shared gate costs nothing extra — and it prunes most pairs before any Jaccard.
            # Two mechanical changes, both output-preserving, from profiling this loop at 49% of a
            # whole build with 6.7M interpreted `dict.get` calls behind it:
            #
            # 1. BISECT past `j <= i`. Postings lists are built by `enumerate`, so they are sorted
            #    ascending; the old code walked every entry and discarded the first half with an
            #    `if`. For a token carried by `d` articles that is `d` steps per occurrence and
            #    `d**2` overall, when `d**2 / 2` will do. The high-frequency tokens that dominate
            #    the cost are exactly the ones with the most to skip.
            # 2. COUNT IN C. `Counter.update(list)` dispatches to `_count_elements`, so the tally
            #    that was a Python-level `shared.get(j, 0) + 1` per posting becomes one C call per
            #    token. Counter is a dict subclass and `update` inserts in first-seen order, so
            #    `shared.items()` below yields exactly what it did before.
            shared: Counter = Counter()
            for tok in ti:
                plist = postings[tok]
                tail = plist[bisect_right(plist, i):]
                if tail:
                    shared.update(tail)
            for j, overlap in shared.items():
                if overlap < min_shared or len(toks[j]) < floor:
                    continue
                score = weighted_jaccard(ti, toks[j], weights)
                if (score >= sim and within_window(times[i], times[j], window_days)
                        and (evidence is None or evidence(i, j))):
                    yield i, j, score

    dsu = DSU(n)
    if link_quorum <= 0.0 and merge_ok is None:
        # Single linkage — union on sight. Kept as its own path so the default cannot drift: no
        # sort, no membership bookkeeping, byte-identical grouping to the measured baseline.
        for i, j, _ in candidates():
            dsu.union(i, j)
    else:
        members = {i: [i] for i in range(n)}
        for i, j, _ in sorted(candidates(), key=lambda p: (-p[2], p[0], p[1])):
            ra, rb = dsu.find(i), dsu.find(j)
            if ra == rb:
                continue                                # already together via a stronger merge
            # Cheapest gate first: merge_ok is one aggregate test over data the caller already
            # holds, the quorum is up to LINK_SAMPLE² weighted Jaccards.
            if merge_ok is not None and not merge_ok(members[ra], members[rb]):
                continue
            if link_quorum > 0.0 and not _quorum_ok(members[ra], members[rb], pair_ok=pair_ok,
                                                    quorum=link_quorum):
                continue
            dsu.union(i, j)
            root, other = (ra, rb) if ra < rb else (rb, ra)   # DSU keeps the lower index as root
            members[root] = sorted(members[root] + members.pop(other))

    groups: dict = {}
    for i in range(n):
        groups.setdefault(dsu.find(i), []).append(i)
    return list(groups.values())
