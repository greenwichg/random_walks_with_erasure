"""coverage_comparison.py — what one article carries relative to the rest of its story cluster.

Design: docs/COVERAGE_COMPARISON_DESIGN.md. **This module implements PHASE 1 (tier L0) only** —
the attribute comparison that needs no article text at all. L1 (salient-term deltas), L2 (figures
and discrepancies) and L3 (quoted voices) are deliberately absent; the design gates them behind a
readiness measurement (``examples/audit_coverage_comparison.py``) and a manual precision read.

What L0 can and cannot say, stated once so the copy never drifts:

* It CAN report counted facts about the coverage set — how many outlets carried the event, which
  viewpoints are absent from the coverage, whether this report was first or late, which event
  countries the coverage places the story in, and what makes this article the only one of its kind
  in the set.
* It CANNOT say "this article omitted the cost figure". That is a claim about *text*, and no text
  is examined here. Nothing in this module produces a content-level omission, and the UI copy must
  not imply one. (Design §5.1, §9.1.)

Every emitted item carries ``support`` (distinct PUBLISHER IDENTITIES, so syndication of one wire
story cannot read as five outlets), ``coverageShare`` and its own ``evidence`` — the outlets and
URLs the count came from — so the reader is shown the basis rather than told a conclusion.

Determinism: a pure function of (article, story, target event countries, registry snapshot,
config). Same inputs, byte-identical output; no clock unless one is passed in, no randomness, no
network, no model. ``ALGO_VERSION`` changes whenever the output would change for unchanged inputs.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Optional

import clustering                    # parse_time only — the same timestamp parser stories use
import publisher_identity            # one outlet, one identity, whatever name form the feed used

#: Bump when the algorithm would produce different output for identical inputs. Cached
#: comparisons carry it, so a stale artifact is recognisable rather than silently served.
ALGO_VERSION = 1

#: Tier implemented here. The payload carries it so a client can tell L0 context from the
#: text-derived findings later tiers add.
TIER = "L0"

#: Design §7: no comparison below this many distinct publishers. A two-publisher "story" is not a
#: coverage set, and differences within it say nothing about what the wider press reported.
MIN_PUBLISHERS = 3

#: Design §7: template/mill genres, where members "omit" each other's numbers by construction and
#: any comparison is noise. Sourced from the measured class in
#: docs/CONTENT_MILL_STORY_EVALUATION.md — a display gate only: no article or story is dropped,
#: the card simply does not render. Overridable with RWE_COVERAGE_TEMPLATE_PATTERNS (regex, `|`).
_TEMPLATE_PATTERNS = (
    r"obituar",
    r"earnings call transcript|q[1-4] 20\d\d earnings call",
    r"betting tips|best bets|promo code|bonus code|\bodds\b|\bpicks\b|\bparlay\b",
    r"powerball|mega millions|jackpot|winning numbers",
    r"results & winners|live grades|match card",
    r"box office (collection )?day \d",
    r"release date and time|episode \d+ release",
)

#: A member share of the cluster at or above which a template match condemns the whole cluster.
#: One betting piece inside a real story must not suppress the card.
_TEMPLATE_SHARE = 0.5


def enabled() -> bool:
    """Kill switch. ON by default: every statement this module makes is a count already computed
    elsewhere in the product, it costs no network and no model, and it writes nothing. Set
    ``RWE_COVERAGE_COMPARISON=0`` to stop rendering it."""
    return os.environ.get("RWE_COVERAGE_COMPARISON", "").strip().lower() not in {
        "0", "false", "no", "off"}


def min_publishers() -> int:
    try:
        return max(2, int(os.environ.get("RWE_COVERAGE_MIN_PUBLISHERS", MIN_PUBLISHERS)))
    except (TypeError, ValueError):
        return MIN_PUBLISHERS


def _template_rx():
    raw = os.environ.get("RWE_COVERAGE_TEMPLATE_PATTERNS", "").strip()
    return re.compile(raw or "|".join(_TEMPLATE_PATTERNS), re.I)


def _identity_map(members: list) -> dict:
    """``publisher name -> identity key`` for this coverage set, via the product's own collapser.

    Support must be counted in OUTLETS, not name forms: a syndication network filing as ~100
    ``*.iheart.com`` hostnames, or one masthead arriving as both ``Sportskeeda`` and
    ``Sportskeeda.Com``, would otherwise read as broad corroboration for a finding. Built once per
    comparison and passed down, because ``groups`` resolves the whole set together."""
    names = {str(m.get("publisher") or "") for m in members if m.get("publisher")}
    try:
        return publisher_identity.groups(names)
    except Exception:                       # identity is an enhancement, never a hard dependency
        return {}


def _pub_key(name: "str | None", ident: "dict | None" = None) -> str:
    raw = str(name or "").strip()
    return (ident or {}).get(raw) or raw.lower()


def _is_template_cluster(members: list) -> bool:
    rx = _template_rx()
    if not members:
        return False
    hits = sum(1 for m in members if rx.search(m.get("headline") or ""))
    return (hits / len(members)) >= _TEMPLATE_SHARE


def _languages(members: list) -> dict:
    counts: dict = {}
    for m in members:
        lang = (m.get("language") or "").strip().lower()[:2]
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return counts


def gate(article: dict, story: dict, *, member: Optional[dict] = None) -> "str | None":
    """The design's §7 refusals, as one function. Returns a machine-readable reason to render
    NOTHING, or ``None`` to proceed. Refusing to answer is a feature: each reason is a case where
    a comparison would be unsound rather than merely empty."""
    if not enabled():
        return "disabled"
    members = list(story.get("coverage") or [])
    if not members:
        return "no_coverage"
    if (story.get("clusterTrust") or "ok") == "low":
        return "cluster_untrusted"
    ident = _identity_map(members)
    pubs = {_pub_key(m.get("publisher"), ident) for m in members if m.get("publisher")}
    if len(pubs) < min_publishers():
        return "too_few_publishers"
    if _is_template_cluster(members):
        return "template_genre"
    langs = _languages(members)
    if len(langs) > 1:
        majority = max(langs, key=lambda k: (langs[k], k))
        mine = (article.get("language") or (member or {}).get("language") or "").strip().lower()[:2]
        if mine and mine != majority:
            return "cross_language"
    return None


def _evidence(members: list, ident: "dict | None" = None, limit: int = 3) -> list:
    """The outlets a count came from — what the UI discloses so a claim can be checked."""
    out, seen = [], set()
    for m in members:
        if not m:
            continue
        key = _pub_key(m.get("publisher"), ident)
        if key in seen:
            continue
        seen.add(key)
        out.append({"publisher": m.get("publisher"), "url": m.get("url"),
                    "headline": m.get("headline")})
        if len(out) >= limit:
            break
    return out


def _finding(kind: str, key: str, support: int, total: int, members: list,
             ident: "dict | None" = None, **extra) -> dict:
    """One item, with its counted basis attached. ``confidence`` is 'high' for every L0 finding by
    construction (design §5.1): these are counts over metadata, with no text interpretation and so
    no text-parity risk. Later tiers introduce the graded scale."""
    item = {"kind": kind, "key": key, "support": support, "of": total,
            "coverageShare": round(support / total, 3) if total else 0.0,
            "confidence": "high", "evidence": _evidence(members, ident)}
    item.update(extra)
    return item


def compare(article: dict, story: dict, *, target_countries=None,
            member: Optional[dict] = None) -> "dict | None":
    """L0 comparison of one article against its own story cluster, or ``None`` when gated.

    ``article`` is the analyzer's article view (publisher, url, lean/leanBucket, language,
    publishedAt, register/emotion when scored); ``story`` is a built Story dict; ``member`` is the
    article's own entry in ``story['coverage']`` when the caller has already found it.
    ``target_countries`` are the article's own provider-extracted event countries — passed in
    rather than read here, so this stays a pure function.
    """
    reason = gate(article, story, member=member)
    if reason is not None:
        return {"available": False, "reason": reason, "algoVersion": ALGO_VERSION, "tier": TIER}

    members = list(story.get("coverage") or [])
    ident = _identity_map(members)
    canon = str(article.get("url") or "")
    me = member or next((m for m in members if str(m.get("url") or "") == canon), None)
    others = [m for m in members if m is not me]
    my_key = _pub_key((me or article).get("publisher"), ident)
    other_pubs = {_pub_key(m.get("publisher"), ident) for m in others if m.get("publisher")}
    other_pubs.discard(my_key)
    total_pubs = len({_pub_key(m.get("publisher"), ident) for m in members if m.get("publisher")})

    elsewhere: list = []
    unique: list = []

    # --- Coverage context: who else carried this, as evidence the reader can open ------------
    if other_pubs:
        elsewhere.append(_finding(
            "outlets", "other_outlets", len(other_pubs), total_pubs, others, ident=ident,
            label="other outlets covering this story"))

    # --- Event geography (source-attributed; never inferred from text) -----------------------
    # The story's consensus event countries the article's OWN location rows do not include. This
    # is a difference between provider-extracted location facts, not a claim about wording.
    story_countries = [str(c).upper() for c in (story.get("countries") or []) if c]
    mine_countries = {str(c).upper() for c in (target_countries or []) if c}
    if story_countries and mine_countries:
        extra = [c for c in story_countries if c not in mine_countries]
        if extra:
            elsewhere.append(_finding(
                "geography", "event_countries", len(extra), len(story_countries), others, ident=ident,
                label="event locations in the wider coverage", countries=extra,
                note="from provider-extracted locations, not from article text"))
    elif story_countries and not mine_countries:
        # Honest absence: we cannot compare what we do not have. Reported as context, never as
        # an omission by the article.
        elsewhere.append(_finding(
            "geography", "event_countries_unknown", 0, len(story_countries), others, ident=ident,
            label="this article has no extracted event location to compare",
            countries=story_countries, confidence_note="not comparable"))

    # --- Register / emotion mix -------------------------------------------------------------
    my_register = (me or {}).get("register") if (me or {}).get("register") is not None \
        else article.get("register")
    reg_vals = [m.get("register") for m in others if m.get("register") is not None]
    if my_register is not None and len(reg_vals) >= 2:
        # "Reporting" vs "opinion" on the product's own P(reporting) axis, at its own midpoint.
        reporting = [v for v in reg_vals if float(v) >= 0.5]
        if float(my_register) < 0.5 and len(reporting) >= 2:
            elsewhere.append(_finding(
                "register", "mostly_reporting", len(reporting), len(reg_vals), others, ident=ident,
                label="outlets covering this in a reporting register"))
        if float(my_register) >= 0.5 and len(reporting) <= len(reg_vals) // 3:
            unique.append(_finding(
                "register", "reporting_among_opinion", 1, total_pubs, [me] if me else [], ident=ident,
                label="one of the few reporting-register pieces in this coverage"))

    # --- Timing ------------------------------------------------------------------------------
    times = [(clustering.parse_time(m.get("publishedAt") or ""), m) for m in members]
    times = [(t, m) for t, m in times if t is not None]
    timing = None
    if times and me is not None:
        times.sort(key=lambda p: p[0])
        mine_t = clustering.parse_time(me.get("publishedAt") or "")
        if mine_t is not None:
            first_t, first_m = times[0]
            position = 1 + sum(1 for t, _ in times if t < mine_t)
            hours = round((mine_t - first_t).total_seconds() / 3600.0, 1)
            # A TIE is not a scoop. Feeds routinely stamp a whole batch with one timestamp (and
            # some publishers stamp the poll time, not the publication time), which would credit
            # every member of the cluster as the first report — a false claim about each of them.
            # "First" therefore requires being STRICTLY earliest.
            tied = sum(1 for t, _ in times if t == mine_t)
            sole_first = position == 1 and tied == 1
            timing = {"position": position, "of": len(times),
                      "hoursAfterFirst": hours, "isFirstReport": sole_first,
                      "tiedAtFirst": position == 1 and tied > 1,
                      "firstBy": first_m.get("publisher") if not sole_first else None}
            if sole_first and len(times) >= 3:
                unique.append(_finding(
                    "timing", "first_report", 1, total_pubs, [me], ident=ident,
                    label="first report in this coverage"))

    # --- Uniqueness: what only this article brings to the set --------------------------------
    my_bucket = (me or article).get("leanBucket")
    if my_bucket:
        same = [m for m in others if m.get("leanBucket") == my_bucket]
        if not same and len(other_pubs) >= 2:
            unique.append(_finding(
                "viewpoint", f"only_{my_bucket}", 1, total_pubs, [me] if me else [], ident=ident,
                label=f"the only {my_bucket}-of-centre outlet in this coverage",
                bucket=my_bucket))
    my_lang = (article.get("language") or (me or {}).get("language") or "").strip().lower()[:2]
    if my_lang:
        langs = _languages(members)
        if langs.get(my_lang, 0) == 1 and len(langs) > 1:
            unique.append(_finding(
                "language", f"only_{my_lang}", 1, total_pubs, [me] if me else [], ident=ident,
                label="the only report in this language in the coverage set", language=my_lang))
    if mine_countries and story_countries:
        only = sorted(c for c in mine_countries if c not in set(story_countries))
        if only:
            unique.append(_finding(
                "geography", "own_event_countries", len(only), len(story_countries), [me] if me else [], ident=ident,
                label="event locations only this article's coverage records", countries=only,
                note="from provider-extracted locations, not from article text"))

    return {
        "available": True,
        "algoVersion": ALGO_VERSION,
        "tier": TIER,
        "storyId": story.get("id"),
        "articles": story.get("totalCoverage") or len(members),
        "outlets": total_pubs,
        "clusterTrust": story.get("clusterTrust"),
        # Reused verbatim from the analyzer's own computation — not recomputed here (design §5.1).
        "missingViewpoints": list(story.get("missingViewpoints") or []),
        "reportedElsewhere": elsewhere,
        "uniqueHere": unique,
        "timing": timing,
        # L0 examines no article text, so there is no parity to report and no content-level
        # omission is claimed. The UI shows this as the card's standing caveat.
        "textParity": None,
        "textClaims": False,
    }
