"""coverage_insights.py — the COMPARABLE SET, the one membership test the insight tiers count over.

Design: docs/COVERAGE_COMPARISON_REVISED_DESIGN.md (revision 2). All four conditions live here:
:func:`comparable_stage1` is the model-free half (conditions 3 and 4, plus syndication collapse),
usable before a single insight exists, and :func:`comparable_set` adds recipe and format parity
once members carry facets. The stage-1 result is always an UPPER BOUND on the full set, which is
what makes it a sound readiness measurement.

Why this is a module and not a private helper: the readiness probe
(``examples/audit_coverage_readiness.py``) and production must apply the SAME rule. The repo has
been here before — ``publisher_identity`` was extracted for exactly this reason, because "an audit
that measured a different rule than production applies would be measuring nothing". Every number
in the Phase 0 report is produced by the functions the feature itself will call.

The rule, in one place (design §4). A member ``m`` is comparable to target ``t`` when:

    1. recipe   m.recipeHash == t.recipeHash           comparable_set   (needs insights)
    2. format   m.format == t.format                   comparable_set   (needs insights)
    3. input    m.inputChars >= parity x median(...)    comparable_stage1
    4. time     m.publishedAt <= t.publishedAt + grace  comparable_stage1

and, before anything is counted, near-duplicate members collapse into ONE support unit, because
``publisher_identity`` collapses many NAMES of one outlet and not six outlets running one wire
story — a hole both earlier designs claimed was closed and was not.

Everything here is a pure function of its arguments: no clock, no store, no network, no model.
"""

from __future__ import annotations

import os
import statistics
from typing import Optional

import clustering                     # title_tokens / jaccard / parse_time — one tokenizer, reused
import coverage_comparison as cc      # _identity_map / _pub_key — deliberately the SAME collapser

#: Design §4: the comparison refuses below this many support units. Three is the L0 floor too, and
#: for the same reason — a two-outlet "coverage set" says nothing about what the press reported.
MIN_COMPARABLE = 3

#: Design §4, condition 3. A member carrying far less text than the set may be COUNTED IN a
#: composition it takes part in, but must not make the set look complete.
DEFAULT_INPUT_PARITY = 0.6

#: Design §4, condition 4. An article published in hour 1 cannot mention what happened in hour 30;
#: the grace window allows for feed timestamp jitter without opening that door.
DEFAULT_TIME_GRACE_H = 6.0

#: Design §4: title+description token overlap at or above which two members are one wire story.
DEFAULT_SYNDICATION_SIM = 0.9


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def min_comparable() -> int:
    try:
        return max(2, int(os.environ.get("RWE_COVERAGE_MIN_COMPARABLE", MIN_COMPARABLE)))
    except (TypeError, ValueError):
        return MIN_COMPARABLE


def input_parity() -> float:
    return _env_float("RWE_COVERAGE_INPUT_PARITY", DEFAULT_INPUT_PARITY)


def time_grace_hours() -> float:
    return _env_float("RWE_COVERAGE_TIME_GRACE_H", DEFAULT_TIME_GRACE_H)


def syndication_sim() -> float:
    return _env_float("RWE_COVERAGE_SYNDICATION_SIM", DEFAULT_SYNDICATION_SIM)


def candidate(member: dict, gen_text: str, dedup_text: Optional[str] = None) -> dict:
    """One coverage member reduced to the fields the comparable-set rules read.

    The two texts are deliberately different, because they answer different questions:

    ``gen_text`` — what the GENERATOR sees (``article_insights.article_text``: title + description
    + body, capped). Input parity is about what the model had to work with, so nothing else will
    do; the story member's headline alone would measure the wrong thing entirely.

    ``dedup_text`` — **title + description only** (design §4), the wire-copy signal. A body is
    excluded on purpose: only 23.7% of the catalog carries one, so including it would compare some
    pairs on their full text and others on a blurb, and an inconsistent duplicate test is worse
    than a consistently weaker one. Defaults to ``gen_text`` only for callers that have no
    separate blurb."""
    dedup = gen_text if dedup_text is None else dedup_text
    return {"publisher": member.get("publisher"),
            "url": member.get("url"),
            "headline": member.get("headline"),
            "publishedAt": member.get("publishedAt"),
            "inputChars": len(gen_text or ""),
            "tokens": clustering.title_tokens(dedup or "")}


def syndication_groups(cands: list) -> list:
    """Indices of ``cands`` partitioned into wire-copy groups; each group is ONE support unit.

    Union-find over pairwise ``title_tokens`` Jaccard, the same DSU idiom (and the same lower-index
    root rule) the clustering uses, so the grouping is deterministic and order-independent. At the
    0.9 default the relation is close to identity, so transitive chaining is not a practical risk;
    the threshold is configurable precisely so Phase 0 can measure that claim rather than assume it.
    """
    n = len(cands)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    floor = syndication_sim()
    for i in range(n):
        for j in range(i + 1, n):
            if clustering.jaccard(cands[i]["tokens"], cands[j]["tokens"]) >= floor:
                union(i, j)
    groups: dict = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [groups[k] for k in sorted(groups)]


def support_units(cands: list) -> int:
    """Distinct publisher identities after wire-copy collapse — the denominator a reader is shown.

    Two collapses, in order and for different reasons: syndication folds several OUTLETS carrying
    one story into one unit; ``publisher_identity`` folds several NAMES of one outlet into one key.
    Counting either alone overstates corroboration."""
    if not cands:
        return 0
    ident = cc._identity_map(cands)
    keys = set()
    for group in syndication_groups(cands):
        # one unit per wire group, keyed by its lowest-index member's identity
        keys.add(cc._pub_key(cands[group[0]].get("publisher"), ident))
    return len(keys)


def comparable_stage1(target: dict, cands: list, *, parity: Optional[float] = None,
                      grace_h: Optional[float] = None) -> list:
    """The model-free half of the comparable set: conditions 3 and 4 of design §4.

    Returns the members of ``cands`` (excluding ``target`` itself) that a comparison may count.
    **This is an upper bound on the true comparable set** — recipe and format parity can only
    remove members, never add them — which is exactly what the readiness probe needs to report.

    The parity median is taken over the members that pass the TIME window, then applied: the
    denominator has to be fixed before it can be filtered by, or the rule is not a function.
    """
    floor_ratio = input_parity() if parity is None else parity
    grace = (time_grace_hours() if grace_h is None else grace_h) * 3600.0

    t_time = clustering.parse_time(target.get("publishedAt") or "")
    t_url = str(target.get("url") or "")
    timed = []
    for c in cands:
        if c is target or (t_url and str(c.get("url") or "") == t_url):
            continue
        ct = clustering.parse_time(c.get("publishedAt") or "")
        if ct is None or t_time is None:
            continue                      # an unverifiable timestamp is not evidence, as in L0
        if (ct - t_time).total_seconds() <= grace:
            timed.append(c)
    if not timed:
        return []
    med = statistics.median([c["inputChars"] for c in timed])
    floor = floor_ratio * med
    return [c for c in timed if c["inputChars"] >= floor]


def with_insight(cand: dict, insight: "dict | None") -> dict:
    """Attach a stored insight to a candidate, reading only what the comparable set needs.

    A member with no insight, no facets, or a facets object from a different ``vocabVersion`` is
    marked NOT comparable rather than given a default: a value the model could not have chosen is
    not evidence it rejected the value, and silently treating an absent field as agreement is the
    defect class that made three L0 findings dead code."""
    facets = (insight or {}).get("facets") or {}
    ok = bool(insight) and bool(facets)
    return {**cand,
            "hasInsight": ok,
            "recipeHash": (insight or {}).get("recipeHash") if ok else None,
            "format": facets.get("format") if ok else None,
            "vocabVersion": facets.get("vocabVersion") if ok else None,
            "facets": facets if ok else None}


def comparable_set(target: dict, cands: list, *, vocab_version: "int | None" = None) -> list:
    """The full comparable set (design §4): stage 1 plus recipe and format parity.

    ``target`` and ``cands`` are candidates that have been through :func:`with_insight`. All four
    conditions in one place, and the result's size is the only denominator a reader is shown.

    Recipe parity is not a nicety: a cluster whose members were extracted by different models is a
    MODEL comparison wearing a coverage comparison's clothes, and its numbers would differ by tier
    rather than by outlet. Format parity is the production case the evaluation found — a film
    review and a box-office report inside one cluster are not alternative treatments of one event.
    """
    if not target.get("hasInsight"):
        return []
    stage1 = comparable_stage1(target, cands)
    want_vocab = target.get("vocabVersion") if vocab_version is None else vocab_version
    out = []
    for c in stage1:
        if not c.get("hasInsight"):
            continue
        if c.get("recipeHash") != target.get("recipeHash"):
            continue
        if c.get("vocabVersion") != want_vocab:
            continue
        # A member whose format the model declined to name cannot be shown to share the target's,
        # so it is excluded — the same "absence is not agreement" rule as everywhere else here.
        if not c.get("format") or c.get("format") != target.get("format"):
            continue
        out.append(c)
    return out
