"""story_service.py — the single owner of Story construction.

Orchestrates: fetch FeedArticles → cluster them with the reusable ``clustering`` primitive → build
``Story`` objects → filter / sort / paginate → diagnostics. It **owns Story construction**; Discover
and Stories both consume it and never build a Story independently. It reuses
``discover.feed_article_to_article`` for article serialization (so a Story's coverage articles are the
exact Article shape, with the identical Read flow) and ``store.search_feed_articles`` for the
index-backed pre-filter. It does not implement clustering (that is ``clustering.py``) and never touches
the recommendation engine.

Story IDs are **stable across rebuilds as a cluster evolves**: the id is anchored to the cluster's
representative (earliest-published) article's canonical URL, so as more coverage of the same event
arrives the id does not change (unlike hashing all members, which would churn on every new article).

Future AI summarization + image enrichment build on this service: every Story already carries the
nullable image contract ``{image, imageSource, imageAttribution}``, so Commit 8 can populate it without
an API change.
"""

from __future__ import annotations

import hashlib
import multiprocessing
import os
import re
import threading
import weakref
import time as _time
from concurrent.futures import ProcessPoolExecutor
from typing import Optional

import clustering                 # the deterministic union-find Jaccard primitive (algorithm only)
import corpus                     # the clustering-corpus boundary (Tier A) + the budget report
import discover                   # feed_article_to_article — the shared Article serializer (Read flow)
import location                   # normalize_country — the entity noise filter's geo test (X5b)
import media                      # centralised hero-image selection (additive; no clustering change)
import obs_metrics                # OBS1 counters (stdlib-only leaf) — stale serves land in /api/metrics
import outlet_registry            # curated source identity — supplies the wire/news distinction
import publisher_identity         # one outlet, one identity, whatever form the feed used
import story_tags                 # the story-level projection of article_entities (topics/tags)
from pagination import OffsetPagination

SORTS = ("top", "latest", "oldest", "publishers")

# --------------------------------------------------------------------------- #
# Cluster trust — how much weight a surface may put on one cluster being one event.
#
# ``geoCoherence`` is the only INDEPENDENT quality signal we have: it is computed from
# provider-extracted locations and knows nothing about titles, tokens or publishers, so it can
# contradict the clusterer. These constants turn it into a verdict two surfaces consume — the
# blindspot claim (which must not be made from a cluster we doubt) and the default ranking (which
# must not lead with one).
# --------------------------------------------------------------------------- #

#: A cluster below this size cannot be a chained false merge: chaining needs A~B, B~C and A≁C,
#: which takes three members. Two-member stories are one pairwise decision — and they are the
#: catalog MEDIAN, so these rules leave the typical story untouched by construction.
MIN_CHAINABLE = 3

#: geoCoherence at or above this counts as independently corroborated. Below it, the located
#: members disagree about where the event happened. Measured in production: the clusters under 0.7
#: include the worst known false merge (0.61, 325 articles, members across a dozen countries).
DEFAULT_COHERENCE_FLOOR = 0.7

#: Located members required before a coherence score is ACTED ON. The ratio exists at two located
#: members, but it is not evidence there: one dissenter scores 0.50, and the commonest reason for a
#: single dissenter in a small cluster is a genuinely two-country story, not a false merge.
#:
#: This is not a theoretical worry — it is what the first production run of these gates did. It
#: withheld blindspots from "LIVE: F1 Hungarian Grand Prix … Lando Norris" (HU race, GB driver),
#: "Zelenskyy accuses Russia of assisting Iran", and "Mamdani reiterates ICC support after urging
#: US to…", all at 0.50 on two or three located members. Every one of them is legitimately about
#: more than one country, which is exactly the case ``_geo_coherence`` is designed to score highly
#: and cannot when the sample is this thin.
#:
#: At four, the 0.7 floor means "three of four located members agree", which is a real minority
#: rather than a coin flip. ``audit_publisher_concentration.py`` already required three for the
#: same reason; the precondition was in the audit and not in the gate, which is the defect this
#: fixes. Clusters that fall short are ``unverified``, not ``low``: too little evidence to condemn.
MIN_LOCATED_FOR_TRUST = 4

#: A cluster bigger than this, with no ACTIONABLE coherence score, is treated as unverified rather
#: than good. Set well above p90 story size (7) so it catches anomalies, not ordinary well-covered
#: news. Measured: it catches one cluster, the 103-article press-release template.
DEFAULT_UNVERIFIED_SIZE = 50

TRUST_OK = "ok"                     # corroborated, or too small to chain
TRUST_LOW = "low"                   # scored, and the score says the members disagree
TRUST_UNVERIFIED = "unverified"     # big enough to be a chain, with nothing to check it against

#: Distinct RATED publishers required before a story may assert a coverage gap.
#:
#: Three, because that is the point below which the claim is arithmetically FORCED. There are three
#: lean buckets, so fewer than three rated publishers cannot fill them all and an empty bucket is
#: guaranteed whatever the outlets actually did. A one-publisher story announcing "nobody on the
#: left covered this" is reporting the size of its own sample, not a fact about the press.
#:
#: Measured on the live catalog before this existed: 516 of 1,022 stories asserted a gap, and
#: **89.1% of those rested on one or two rated publishers** (254 on one, 206 on two). Only 56
#: claims had three or more behind them. The catalog median story is 2 articles, so the feature was
#: firing almost everywhere it possibly could.
#:
#: This is the same defect ``MIN_LOCATED_FOR_TRUST`` fixes for geoCoherence — a ratio acted on with
#: no sample-size floor — in the feature that gate was built to protect. Raise to 4 for a claim that
#: carries weight rather than merely being possible: an empty bucket across four rated outlets is
#: lopsided, across three it is thin.
MIN_RATED_FOR_BLINDSPOT = 3

#: Weighted profile similarity two clusters must reach before they are joined as one event.
#:
#: 0.33 because that is where the measured sample stops making mistakes. Reading all 23 candidate
#: pairs at ≥ 0.25 gave 16 true duplicates, 5 same-story-different-angle and **2 false positives**
#: (a French wildfire paired with a Californian one, two unrelated Nvidia stories) — and both false
#: positives sat at 0.31. Every pair at 0.33 and above was a true duplicate. n = 14, so this is a
#: floor picked from a small sample, not a tuned optimum.
#:
#: Not comparable to ``clustering.DEFAULT_SIM``: profiles are far larger token sets than headlines,
#: so the same number means something much stricter here.
DEFAULT_MERGE_SIM = 0.33

#: Coverage of one event arrives in a burst. Two clusters days apart that read alike are a
#: recurring topic — a weekly fixture, a monthly filing — not a duplicate.
DEFAULT_MERGE_MAX_GAP_HOURS = 48.0

#: No merge may produce a cluster larger than this. A backstop against a runaway, set just above
#: the largest legitimate cluster measured (100 articles, the French/Italian wildfires).
DEFAULT_MERGE_MAX_SIZE = 130

#: A targeted re-split must keep at least this share of the original cluster's articles, or the
#: original is kept whole instead. Repair is meant to separate conflated events, not to delete a
#: cluster it cannot make sense of — and without this floor the failure mode is silent, because
#: dissolving a cluster improves every aggregate the audit prints.
REPAIR_MIN_RETENTION = 0.5


# --------------------------------------------------------------------------- #
# Story construction (the single implementation).
# --------------------------------------------------------------------------- #
def _story_id(members: list) -> str:
    """A stable id anchored to the representative (earliest-published) article's canonical URL, so the
    id survives rebuilds as the cluster gains coverage of the same event."""
    rep = min(members, key=lambda a: (a.get("publishedAt") or "~", a.get("id") or ""))
    anchor = rep.get("id") or rep.get("url") or ""
    return "st_" + hashlib.sha1(anchor.encode("utf-8", "replace")).hexdigest()[:16]


def _votes(members: list) -> list:
    """The members whose lean may be COUNTED — rated, and not flagged low-credibility.

    Two different reasons a member does not vote, and they are deliberately handled in one place so
    they cannot drift apart:

    * **unrated** (``leanBucket`` null) — nobody has rated the outlet, so there is no vote to cast.
      Counting it as centre would fabricate a lean (L2.2).
    * **low credibility** — the outlet IS rated, and the rater also published a Questionable /
      Low Credibility verdict on it. The lean is real and is still shown on the article; what it
      may not do is help manufacture a claim about who covered a story. Without this, a coverage-gap
      claim could rest on two state broadcasters and nothing in the product would show it.

    Both are still real COVERAGE — they appear in ``coverage``, in ``publishers`` and in
    ``totalCoverage``. Only the vote is withheld."""
    return [m for m in members if not m.get("lowCredibility")]


def _distribution(members: list) -> dict:
    """L/C/R distribution over **distinct VOTING publishers** (one vote per outlet), normalised to
    sum 1 — see :func:`_votes` for who votes and why. All-unrated -> all-zero (blindspot: None)."""
    by_pub = {}
    for m in _votes(members):
        by_pub.setdefault(_pub_key(m), m["leanBucket"])
    counts = {"left": 0, "center": 0, "right": 0}
    for bucket in by_pub.values():
        if bucket in counts:
            counts[bucket] += 1
    total = sum(counts.values()) or 1
    return {k: counts[k] / total for k in ("left", "center", "right")}


def _blindspot(dist: dict) -> Optional[str]:
    """The under-covered side of an event — a bucket with no publishers while another side is well
    covered. Deterministic (left < center < right). A coverage gap, not an opinion metric.

    Computed over the cluster's members, which makes it only as true as the cluster is. Blend two
    unrelated events and the merged lean distribution is nobody's editorial gap — so ``_build_story``
    withholds this whenever ``_cluster_trust`` is not ``ok``. It is the product's most load-bearing
    claim about publisher behaviour, and the only one a clustering defect can turn into a false
    statement about the world rather than merely a poor grouping."""
    empties = [k for k in ("left", "center", "right") if dist[k] == 0.0]
    covered = [k for k in ("left", "center", "right") if dist[k] > 0.0]
    if empties and len(covered) >= 1 and max(dist.values()) >= 0.5:
        return empties[0]
    return None


def _cluster_trust(total: int, coherence: Optional[float], located: int = 0, *, floor: float,
                   unverified_size: int, min_located: int = MIN_LOCATED_FOR_TRUST) -> str:
    """How much this cluster can be trusted to be ONE event — ``ok`` / ``low`` / ``unverified``.

    Three rules, in order:

    * under ``MIN_CHAINABLE`` members → ``ok``. Not a judgement about quality; a structural fact.
      A two-member cluster is a single pairwise decision, so the failure this guards against
      cannot have occurred. This is what keeps the median story out of the gate entirely.
    * an ACTIONABLE coherence score below ``floor`` → ``low``. Actionable means backed by at least
      ``min_located`` located members — see ``MIN_LOCATED_FOR_TRUST`` for why the ratio alone is
      not enough.
    * otherwise, above ``unverified_size`` → ``unverified``. Both "nothing located" and "too few
      located to act on" land here, because they are the same thing: no independent read either
      way. Absence of evidence withholds claims (see ``_build_story``) but does not reorder the
      feed (see ``build_stories``) — only evidence of a problem does that.
    """
    if total < MIN_CHAINABLE:
        return TRUST_OK
    if coherence is not None and located >= min_located:
        return TRUST_LOW if coherence < floor else TRUST_OK
    return TRUST_UNVERIFIED if total > unverified_size else TRUST_OK


def _pub_key(m: dict) -> str:
    """The member's outlet identity — its annotated key, or its raw name if identity is off."""
    return m.get("publisherKey") or m["publisher"]


def _display_publishers(members: list) -> list:
    """One entry per OUTLET, named by the form that outlet used most often.

    Seventeen iHeart station hostnames are one publisher, and listing all seventeen would both
    overstate the coverage and read as noise. The commonest form wins so the label is the one a
    reader would recognise rather than whichever sorted first."""
    counts: dict = {}
    for m in members:
        counts.setdefault(_pub_key(m), {}).setdefault(m["publisher"], 0)
        counts[_pub_key(m)][m["publisher"]] += 1
    return sorted(max(forms.items(), key=lambda kv: (kv[1], kv[0]))[0]
                  for forms in counts.values())


def _rated_publishers(members: list) -> int:
    """Distinct publishers whose lean may be counted — the sample a blindspot claim rests on.

    Same rule as :func:`_distribution`, and that is the point: the FLOOR and the DISTRIBUTION must
    agree about who counts, or a story could clear "three rated publishers" on a sample the
    distribution then declines to use."""
    return len({_pub_key(m) for m in _votes(members) if m.get("leanBucket")})


def _low_credibility_publishers(members: list) -> list:
    """The outlets in this story whose lean was recorded but not counted, named for display.

    Emitted rather than silently dropped: the whole reason the registry grew a credibility column is
    that withholding these outlets entirely lost a true fact. A reader should see that TASS covered
    the story AND that its rating is not being voted."""
    return sorted({_pub_key(m) for m in members if m.get("lowCredibility")})


def _coverage(members: list) -> list:
    """One coverage entry per article, newest first — the canonical article list (carries the URL).

    ``ownership`` is resolved HERE from the registry rather than stored at ingest, for the same
    reason the credibility gate resolves live: a curation change takes effect on the next serve
    instead of waiting for a backfill. ``None`` = uncurated/unknown (L2.2 — never ``other``);
    the resolve memo makes the per-row call a dict hit after the first article of each outlet.

    ``factuality`` rides along the same way, with two differences that both come from WHOSE fact
    it is. It is the RATER's verdict, so it travels as ``{value, source, asOf, ratingUrl}`` — the
    identical object the publisher profile publishes — and never as a bare level a surface could
    render as ours. And the whole field is behind ``RWE_PUBLIC_FACTUALITY`` (default OFF): these
    are MBFC's commercial product and we hold no licence to redistribute them, so a disabled
    deployment puts no verdict on the wire at all, exactly as `publisher_service` already does for
    the profile. ``None`` on an unrated outlet, dropped by ``response_model_exclude_none`` — so a
    client cannot tell "unrated" from "gate off" out of the rows alone, which is why the story
    carries ``factualityPublished`` beside them (same rule, same reason, as the profile's)."""
    published = outlet_registry.factuality_published()
    out = []
    for m in sorted(members, key=lambda m: (m["publishedAt"] or "", m["id"]), reverse=True):
        out.append({
            "publisher": m["publisher"], "headline": m["headline"], "lean": m["lean"],
            "leanBucket": m["leanBucket"], "register": m["register"], "emotion": m["emotion"],
            "url": m["url"], "publishedAt": m["publishedAt"],
            "ownership": outlet_registry.ownership(m["publisher"]),
            "factuality": outlet_registry.factuality_record(m["publisher"]) if published else None,
        })
    return out


def _mode_topic(members: list) -> str:
    """The cluster's topic: the most common topic its members CARRY. ``""`` when none carries one.

    **Absence is not a competing category, and counting it as one was the bug.** `classify_topic`
    returns ``""`` for an article whose source published no category tag, whose URL has no topical
    section, and whose headline the subject lexicon does not hit — deliberately, because this
    system does not invent metadata. Those empties used to be tallied alongside real topics, so a
    story with 30 uncategorized members and 25 "World" ones resolved to ``""``: *we don't know*
    outvoted the evidence, and the card rendered with no chip.

    Measured on production (2026-08-30, the default 30-story front page): 7 stories showed no
    category, **all 7 had at least one categorized member**, and none was blank for want of
    evidence. 66.8% of catalogue articles carry a category, so the uncategorized third was winning
    pluralities outright. The losers skewed non-political — World, Entertainment, Culture, Science
    — which is what the political-leaning lexicon predicts: those clusters carry the largest share
    of members it cannot classify.

    The empty return is the contract, not a fallback. A cluster whose members are ALL uncategorized
    still yields ``""`` and still renders no chip, which is the honest answer and the one
    `classify_topic`, `discover.feed_article_to_article` and the card's ``{story.topic && …}`` all
    already agree on. (It replaces a ``"General"`` default that was unreachable — stories need two
    members — and that contradicted `classify_topic`'s explicit "never General".)"""
    counts: dict = {}
    for m in members:
        if m["topic"]:
            counts[m["topic"]] = counts.get(m["topic"], 0) + 1
    return sorted(counts, key=lambda t: (-counts[t], t))[0] if counts else ""


# --------------------------------------------------------------------------- #
# Story summary selection — adopted 2026-08-16 against the audit_story_summary baseline
# (27,891 articles / 1,522 stories: 26.2% of served summaries were Google News coverage
# DIGESTS, echo-rate 31.5%, 212 bare-domain leaks, fallback 5.5%). The served summary used to
# be the representative's description VERBATIM — the earliest filer's text, whatever it was.
# Extractive only: every summary is a sentence some member actually wrote, or the counted
# fallback. Display-layer only: descriptions do not feed clustering (desc_tokens()=0,
# measured-and-not-adopted), and _story_id anchors on the representative's URL, so ids never
# move when summaries do.
# --------------------------------------------------------------------------- #

_SUMMARY_MAX = 320            # clamp ceiling (chars) — p90 was 446 with digests in the tail
_SUMMARY_MIN_GOOD = 60        # below this a dek is a fragment; ranked down, not rejected
_SUMMARY_URL_RE = re.compile(
    r"\b[a-z0-9-]+\.(?:com|org|net|gov|edu|co\.[a-z]{2}|co|io|us|uk)\b", re.IGNORECASE)
_SUMMARY_SENT_RE = re.compile(r"(?<=[.!?…])\s+")
_SUMMARY_END_PUNCT = (".", "!", "?", "…", '"', "”", "'")


def summary_publisher_patterns(publishers) -> dict:
    """Word-boundary regex per publisher name (len >= 4 — a substring match on "Time" would
    convict the word "sometimes"). Shared with audit_story_summary so the instrument and the
    rule can never drift."""
    out = {}
    for p in publishers:
        name = (p or "").strip()
        if len(name) >= 4:
            out[name] = re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE)
    return out


def summary_digest_hits(text: str, other_pubs) -> list:
    """OTHER cluster publishers named inside a description — >= 2 is digest evidence. The
    BACKSTOP tier only: measured on production it catches 13 of the ~399-story Google News
    class, because GN's related-coverage outlets are mostly NOT cluster members."""
    return [name for name, pat in summary_publisher_patterns(other_pubs).items()
            if pat.search(text)]


def summary_echo_share(title: str, text: str) -> float:
    """Share of the title's tokens (len >= 3, needing >= 4 of them) present in the text's first
    160 chars — the dek that merely restates the headline. Shared with the audit."""
    toks = {t for t in re.findall(r"[a-z0-9']+", (title or "").lower()) if len(t) >= 3}
    if len(toks) < 4:
        return 0.0
    head = set(re.findall(r"[a-z0-9']+", (text or "")[:160].lower()))
    return sum(1 for t in toks if t in head) / len(toks)


def _headline_rows(text: str) -> int:
    """Lines shaped like digest rows: headline-length, ending WITHOUT sentence punctuation
    (they end in an outlet name). The Guardian standfirst exhibit has one such line; every
    digest exhibit has two or more — hence the >= 2 threshold at the caller."""
    n = 0
    for ln in text.splitlines():
        t = ln.strip()
        if 15 <= len(t) <= 160 and not t.endswith(_SUMMARY_END_PUNCT):
            n += 1
    return n


def _digest_shaped(desc: str, source_type, other_pubs) -> bool:
    """Whether a description is a coverage DIGEST rather than a dek — evidence in measured
    order of reliability: provider (a googlenews description is a digest BY CONSTRUCTION —
    399 of 1,522 served summaries in the baseline), structure (>= 2 newline-separated
    headline rows — clean_html turned the <ol> into line breaks), member-names (the
    registered backstop; see summary_digest_hits for why it is last)."""
    if (source_type or "") == "googlenews":
        return True
    if _headline_rows(desc) >= 2:
        return True
    return len(summary_digest_hits(desc, other_pubs)) >= 2


def _clamp_sentences(text: str, limit: int = _SUMMARY_MAX) -> str:
    """1-2 sentences, <= limit chars, single line. Truncation lands on a word boundary with an
    ellipsis — presentation, never invented words."""
    flat = " ".join(text.split())
    parts = _SUMMARY_SENT_RE.split(flat)
    out = parts[0] if parts else flat
    if len(parts) > 1 and len(out) + 1 + len(parts[1]) <= limit:
        out = f"{out} {parts[1]}"
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return out


def _pick_summary(members: list, representative) -> "tuple[str, Optional[dict]]":
    """The best member-written description for a story, or ``""`` when none survives — the
    caller's counted fallback ("N publishers covering X.") is the designed empty state, the
    same honesty rule the coverage plate follows for images.

    Reject tiers (each receipted from the production baseline): digest-shaped
    (:func:`_digest_shaped`), bare-domain junk, and headline-echo against BOTH the story title
    and the member's own headline (>= 80% token overlap). Survivors rank on: sentence-shaped →
    not a fragment (>= 60 chars) → the representative as a TIEBREAK → earliest-published →
    canonical URL (a total order; the same rows always pick the same summary). The winner is
    masthead-suffix-stripped (discover._display_title) and sentence-clamped."""
    pubs = {m.get("publisher") for m in members if m.get("publisher")}
    story_title = (representative or {}).get("headline") or ""
    cands: list = []
    for m in members:
        desc = (m.get("description") or "").strip()
        if not desc:
            continue
        if _digest_shaped(desc, m.get("sourceType"), pubs - {m.get("publisher")}):
            continue
        if _SUMMARY_URL_RE.search(desc):
            continue
        if summary_echo_share(story_title, desc) >= 0.8:
            continue
        if summary_echo_share(m.get("headline") or "", desc) >= 0.8:
            continue
        flat = " ".join(desc.split())
        cands.append((
            (not flat.endswith(_SUMMARY_END_PUNCT),          # sentence-shaped first
             len(flat) < _SUMMARY_MIN_GOOD,                  # fragments after real deks
             m is not representative,                        # the rep as a tiebreak, not a veto
             m.get("publishedAt") or "~",
             m.get("id") or m.get("url") or ""),
            m, desc))
    if not cands:
        return "", None
    cands.sort(key=lambda c: c[0])
    _, winner, desc = cands[0]
    flat = discover._display_title(" ".join(desc.split()), winner.get("publisher") or "")
    return _clamp_sentences(flat), winner


def pick_story_summary(members: list, representative) -> str:
    """The serving wrapper over :func:`_pick_summary` — audit_story_summary uses the underscore
    form to attribute the WINNER's ingestion source; production only needs the text."""
    return _pick_summary(members, representative)[0]


def _build_story(members: list, *, hero_ranked: bool = False,
                 hero_rejected: "Optional[frozenset]" = None) -> dict:
    """Build one Story object from a cluster of Article dicts. Coverage only — no opinion metrics.

    ``hero_ranked``/``hero_rejected`` (the hero guard, :func:`hero_guard`) affect ONLY the hero
    image fields; every other field is computed identically. ``hero_rejected`` is the per-build
    set of cross-story-reused image identities — a property of the whole build, so only
    ``build_stories`` can supply it; the trust-probe call site passes neither and is unaffected."""
    times = [clustering.parse_time(m["publishedAt"]) for m in members]
    times = [t for t in times if t is not None]
    earliest = min(times).isoformat() if times else ""
    latest = max(times).isoformat() if times else ""
    span_hours = round((max(times) - min(times)).total_seconds() / 3600.0, 2) if len(times) >= 2 else 0.0
    # Representative = earliest-published article (deterministic); its headline titles the event.
    rep = min(members, key=lambda m: (m["publishedAt"] or "~", m["id"]))
    dist = _distribution(members)
    publishers = _display_publishers(members)
    total = len(members)
    # Optional hero image (additive; centralised in media.py). Legacy: representative → best →
    # most recent → None. Under the hero guard the members compete on evidence instead, and a
    # branding/reused asset yields None — the imageless card's coverage figure is the designed
    # fallback. Still the only media touch here — clustering/filter/sort/pagination unchanged.
    hero = media.pick_story_hero(members, representative=rep, ranked=hero_ranked,
                                 rejected=hero_rejected) or {}
    timeline = []
    if earliest:
        timeline.append({"date": earliest, "label": "First report"})
    if latest and latest != earliest:
        timeline.append({"date": latest, "label": "Latest"})
    votes = _country_votes(members)
    consensus = _event_consensus(members, votes)
    coherence, located_members = _geo_coherence(members, votes)
    trust = _cluster_trust(total, coherence, located_members, floor=coherence_floor(),
                           unverified_size=unverified_size())
    # The blindspot is asserted only from a cluster we can stand behind. Keeping the raw verdict
    # separately means the audit can count what the gate withheld rather than guess at it.
    # Two independent reasons to say nothing. Too few rated publishers means the claim was never
    # valid — an empty bucket is forced below three, so there is nothing to withhold. A distrusted
    # cluster means the claim may be valid but rests on a grouping we cannot stand behind, which
    # IS a withholding and is counted as one.
    raw_blindspot = (_blindspot(dist)
                     if _rated_publishers(members) >= min_rated_for_blindspot() else None)
    return {
        "id": _story_id(members),
        "title": rep["headline"],
        # Ranked member-dek selection (pick_story_summary) instead of the representative's
        # description verbatim — the baseline measured the old rule serving Google News
        # coverage digests as 26.2% of all summaries. Extractive only; the counted fallback
        # below is unchanged and still handles an EMPTY topic (uncategorized stays "" by
        # design — the naive interpolation once shipped "18 publishers covering ." with an
        # orphaned period).
        "summary": pick_story_summary(members, rep) or (
            f"{len(publishers)} publishers covering {rep['topic'].lower()}." if rep["topic"]
            else f"{len(publishers)} publishers covering this story."),
        # Hero image contract (nullable) — selected from the cluster's articles' RSS media.
        "image": hero.get("image"),
        "imageWidth": hero.get("imageWidth"),
        "imageHeight": hero.get("imageHeight"),
        "imageMimeType": hero.get("imageMimeType"),
        "imageSource": hero.get("imageSource"),
        "imageAttribution": hero.get("imageAttribution"),
        "topic": _mode_topic(members),
        "updatedAt": latest or rep["publishedAt"],
        "totalCoverage": total,                 # article count
        "publisherCount": len(publishers),      # distinct OUTLETS, not distinct name forms
        "publishers": publishers,               # explicit publisher list
        "publisherDiversity": round(len(publishers) / total, 3) if total else 0.0,
        "earliest": earliest,
        "latest": latest,
        "firstPublished": earliest,
        "latestUpdate": latest,
        "newest": latest,
        "oldest": earliest,
        "timeSpanHours": span_hours,
        "distribution": dist,
        "coverage": _coverage(members),
        # Says "this deployment publishes factuality", NOT "these outlets are rated" — the same
        # distinction the publisher profile draws, for the same reason: without it a client cannot
        # tell an unrated panel from a switched-off feature, and would label 130 outlets we hold
        # verdicts for as unrated. Absent (exclude_none) when the gate is off, so the wire payload
        # of a disabled deployment is byte-identical to before this field existed.
        "factualityPublished": True if outlet_registry.factuality_published() else None,
        "timeline": timeline,
        "blindspotSide": raw_blindspot if trust == TRUST_OK else None,
        "blindspotWithheld": bool(raw_blindspot) and trust != TRUST_OK,
        # Outlets whose lean is recorded but not voted (registry credibility = low). Named, not
        # silently dropped: the point of the credibility column is that withholding these outlets
        # entirely lost a true fact. A reader should see that TASS covered the story AND that its
        # rating is not counted toward the coverage-gap claim.
        "lowCredibilityPublishers": _low_credibility_publishers(members),
        # Location Intelligence — the story's EVENT geography (counted facts, never guessed).
        # ``countries`` is what ?country= matches: the member-consensus leaders of the EVENT
        # dimension only — a story with no event-located members matches no country (it still
        # appears under "All"). Publisher homes are deliberately NOT a fallback here: they are a
        # PROVENANCE fact, preserved separately as ``publisherCountries`` for publisher
        # intelligence/analytics. All internal until a card consumes them (the response model
        # omits undeclared fields).
        "countries": consensus,
        "primaryCountry": consensus[0] if len(consensus) == 1 else None,
        "eventCountries": sorted({c for m in members for c in (m.get("eventCountries") or ())}),
        "publisherCountries": sorted({str(m["country"]).upper() for m in members
                                      if m.get("country")}),
        # Cluster-geography coherence (diagnostic; internal like the fields above until a surface
        # consumes it). Measured on the INCIDENT dimension only — see _geo_coherence.
        "geoCoherence": coherence,              # None = nothing located, NOT zero
        "locatedMembers": located_members,
        "clusterTrust": trust,                  # ok | low | unverified — see _cluster_trust
        "countryVotes": dict(sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def _member_countries(m: dict) -> set:
    """One member's EVENT countries, upper-cased. The INCIDENT's location — never the publisher's
    home (``m["country"]``), which is provenance and lives separately as ``publisherCountries``. A
    US outlet reporting an incident in India votes IN here, which is the whole point."""
    return {str(c).upper() for c in (m.get("eventCountries") or ()) if c}


def _country_votes(members: list) -> dict:
    """country -> how many MEMBERS were located there. One vote per member per country, so a
    prolific outlet cannot outvote the rest by filing more copy. Unlocated members abstain — they
    are not evidence either way."""
    votes: dict = {}
    for m in members:
        for c in _member_countries(m):
            votes[c] = votes.get(c, 0) + 1
    return votes


def _event_consensus(members: list, votes: Optional[dict] = None) -> list:
    """The story's event countries by member consensus: each event-located member votes for its
    (already dominance-filtered) event countries; the plurality leader(s) win — ties are kept,
    because a genuinely two-country event IS in both places. No event-located members → no
    countries (fail-honest: publisher homes never substitute for where an event happened)."""
    votes = _country_votes(members) if votes is None else votes
    if not votes:
        return []
    top = max(votes.values())
    return sorted(c for c, n in votes.items() if n == top)


def _geo_coherence(members: list, votes: dict) -> "tuple[Optional[float], int]":
    """``(coherence, locatedMembers)`` — the share of LOCATED members backing the single
    strongest incident country. ``None`` when nothing is located: absence of evidence is not
    incoherence, and a story nobody located must not be scored as though it were.

    Deliberately measured against the TOP vote, not against the consensus set. ``_event_consensus``
    keeps ties, so a cluster whose members each name a *different* country produces an n-way tie in
    which every member "backs a winner" — scoring maximal disagreement as perfect agreement, the
    exact inverse of the truth. Against the top vote, four members in four countries score 0.25.

    The distinction that makes this useful: a member located in BOTH places of a genuine two-country
    event votes for both and lifts the top count, so real border/multi-site events stay coherent —
    while members that each name a different single place do not.

    This measures whether a cluster's members are *about the same place*, which turns out to be a
    sharp detector of FALSE MERGES rather than of geography errors. Measured in production: a
    105-publisher cluster titled "Thune on Trump's Canada tariffs" whose members were located
    across CN, CU, DJ, GB, IL, IR, OM, PH, SA, SG, US and YE — articles with nothing to do with
    each other, merged on shared title tokens. ``publisherDiversity`` rated that cluster healthy
    (0.53); this does not.

    Crucially it is a MEMBER-AGREEMENT measure, not a country count. A genuine multi-country story
    scores high: an explainer citing fires in AU/ES/FR/GB/SK/US is coherent when its members all
    mention the same lead country, however many others each adds. A false merge scores low because
    its members name *different* places."""
    located = sum(1 for m in members if _member_countries(m))
    if not located or not votes:
        return None, located
    return round(max(votes.values()) / located, 3), located


def article_tokens(a: dict, cap: int = 0, hyphen: bool = False,
                   uni: bool = False) -> frozenset:
    """The clustering signal for one article: its headline, plus ``cap`` dek tokens when > 0.

    One function so the primary build, the repair re-split and the audit cannot drift into scoring
    different things — a repair that saw a different signal from the build that produced the
    cluster would re-split on a disagreement rather than on a defect. ``hyphen`` threads the
    hyphen-compound candidate (see :func:`hyphen_compounds`) for the same reason, and ``uni``
    threads the Unicode-word candidate (see :func:`unicode_words`)."""
    toks = clustering.title_tokens(a.get("headline") or "", hyphen_compounds=hyphen,
                                   unicode_words=uni)
    if cap > 0:
        toks = toks | clustering.description_tokens(a.get("description") or "", cap)
    return toks


def min_shared_tokens() -> int:
    """Distinctive tokens two headlines must share to be considered the same event. Tunable without
    a deploy because the right value is an empirical question about the live headline mix — see
    ``examples/audit_clustering_change.py``, which measures a candidate against the real catalog."""
    return _env_int("RWE_CLUSTER_MIN_SHARED", clustering.MIN_SHARED_TOKENS)


def min_title_tokens() -> int:
    return _env_int("RWE_CLUSTER_MIN_TOKENS", clustering.MIN_TITLE_TOKENS)


def desc_tokens() -> int:
    """How many DESCRIPTION tokens join each article's clustering signal — 0 (off).

    **Measured and NOT adopted. Do not turn this on globally.** It is retained as an instrument —
    ``audit_clustering_change.py --desc-tokens 12`` is how the paraphrase/template mix gets
    quantified on the live catalog — not as a candidate default. The evidence is in
    ``tests/test_story_service.py::test_no_shared_token_floor_can_separate_paraphrase_from_template``.

    **Why it was built.** The clusterer sees 8-12 title tokens and nothing else, so "Fed holds
    rates steady" and "Central bank leaves borrowing costs unchanged" share ZERO tokens and can
    never meet ``min_shared`` however the thresholds move. Roughly four in five catalog articles
    are in no story at all; a paraphrased headline is a large part of that, and the dek is already
    in the row (``_profile`` has used it for the merge pass since the Seattle clusters).

    **Why it does not work.** The premise was that a higher floor (:func:`desc_min_shared`) buys
    back the precision the deks cost. Measured over four realistic paraphrase pairs and four
    realistic template pairs — same wire boilerplate, different event: election results by state,
    earnings by ticker, scores by game, weather by county — the template pairs score STRICTLY
    HIGHER than the paraphrase pairs on both shared-token count and Jaccard, at every cap. At
    cap 12: paraphrases 8-10 shared / 0.35-0.48, templates 10-15 shared / 0.69-0.88. The
    distributions are not merely overlapping, they are inverted, so *no* floor admits the
    paraphrases without admitting the templates.

    That is structural, not a fixture accident. A template pair shares its prose BY CONSTRUCTION —
    one sentence with an entity substituted — while a paraphrase pair shares only the entities two
    desks independently chose to lead with. Lengthening the token set therefore helps the template
    more, monotonically. Separating them needs information bag-of-words does not carry: that *Ohio*
    and *Iowa* are alternatives rather than variants. That is a representation change, not a
    threshold."""
    return _env_int("RWE_CLUSTER_DESC_TOKENS", 0)


def desc_min_shared() -> int:
    """Shared-token floor used INSTEAD of ``min_shared_tokens()`` when deks are in the signal — 5.

    A separate constant rather than a scaled one, because the two are calibrated against different
    distributions and coupling them hides that. Three-of-eight is a strong signal; three-of-twenty
    is noise.

    **Five is not a safe value, and neither is any other** — see :func:`desc_tokens` for the
    measurement. It survives as the titration knob for the audit, so the paraphrase and template
    curves can be traced against a real catalog rather than against eight constructed pairs."""
    return _env_int("RWE_CLUSTER_DESC_MIN_SHARED", 5)


def use_idf() -> bool:
    """Rarity-weighted similarity — OFF, after measurement said it costs more than it buys. Set
    ``RWE_CLUSTER_IDF=1`` to enable without a deploy.

    It was briefly enabled on the strength of the headline numbers, measured against 13,305 live
    articles with the admission gates held fixed on both sides so weighting was the only variable:

        plain jaccard : 766 stories, largest 194
        idf-weighted  : 777 stories, largest  93

    More stories and half the largest cluster looks like separating conflated events. It is not what
    happened. Attributing every lost article to the cluster it left (see
    ``audit_clustering_change.py``) showed 361 of 3,431 covered articles — 10.5% — falling out of
    stories entirely, and only 16% of that loss came from the press-release templates the weighting
    was supposed to punish. Even crediting BOTH chained mega-clusters as correct fragmentation, 67%
    of the loss is real stories shedding real coverage: the Nolan Wells autopsy story lost 12 of 58
    articles, the French wildfires 9 of 75, Berlin pride 6 of 66, and a long tail of small
    three-article stories dissolved outright.

    The tell that this was the wrong instrument: the single worst template in the catalog — 101
    articles from 4 publishers, one outlet repeating "X LLC Makes New Investment in Y Inc" — lost
    only 9 members and survived as a story. Weighting rare words does not find one outlet repeating
    itself.

    An earlier revision of this docstring went on to claim publisher concentration *does* find it,
    on the strength of ten hand-read rows. That claim was **measured against the whole catalog and
    falsified** — see ``docs/PUBLISHER_CONCENTRATION_EVALUATION.md``: 0% precision and 0% recall at
    every threshold, because every independently-bad cluster sits at ≤ 2.0 articles per publisher
    while the catalog's 99th percentile is 2.29. The note is kept rather than deleted so the
    heuristic does not get re-proposed from this file.

    The machinery stays because it is correct and tested, and because the real target — SINGLE-LINKAGE
    chaining, which is what built the 194-article cluster — needs a linkage fix, not a weighting one.
    That fix now exists as ``clustering``'s ``link_quorum``; see ``link_quorum()`` below."""
    v = os.environ.get("RWE_CLUSTER_IDF", "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def link_quorum() -> float:
    """Cluster-aware linkage strength — **0.2 in production**, adopted 2026-08-03
    (``docs/STORY_CLUSTER_MERGES.md``; the deploy compose defaults it, so a lost env-file line no
    longer reverts it). ``RWE_CLUSTER_LINK_QUORUM`` overrides; UNSET falls back to
    ``clustering.DEFAULT_LINK_QUORUM`` = 0.0 = single linkage — which is what a container gets
    when it carries none of the deploy's environment. That divergence is not hypothetical: a
    backup-profile container (no ``environment:`` block) ran the audit on 2026-08-16 and its
    single-linkage numbers wore the "[PRODUCTION BASELINE]" tag until the missing quorum/repair/
    merge tags gave them away. ``audit_clustering_change.py`` now warns on that state. (An earlier
    revision of this docstring called 0.0 "the measured production baseline" — already stale when
    it helped misdirect that run.)

    It targets single-linkage CHAINING, the one clustering failure with direct production
    evidence: the mega-cluster grew 194 → 208 → 318 while the corpus grew 23%, geoCoherence 0.62,
    members located across twelve countries. The last change that tightened matching on equally
    sound reasoning (``use_idf``) cost 10.5% of covered articles and was reverted; the bar was set
    before that measurement and applies to every candidate unchanged:

        adopt   : largest cluster well down, droppedOut ≤ 5% of covered articles, no story-count fall
        reject  : droppedOut > 10%, or total story count falls (the min_publishers cliff — splitting
                  a 4-article/2-publisher cluster into 2+2 can leave two single-publisher fragments,
                  BOTH of which are then dropped, so oversplitting deletes stories rather than
                  merely shrinking them)

    Re-baselined 2026-08-16 (X0) against 28,437 live articles with the full production stack
    (quorum 0.2, repair 0.5, merge 0.33): 1,541 stories, largest cluster 64, 6,260 covered,
    4/73 independently bad at mean 0.924. The same-day counterfactual — the catalog re-clustered
    on the library fallbacks (single linkage, no repair, no merge; the wire/aggregator exclusions
    fail closed and were identical; 28,464 articles at its run) — regrew the blob to **787
    articles**, a Colombian
    earthquake, the Congo Ebola outbreak and a Zimbabwe ferry capsizing under one White House
    staffing headline. The setting is load-bearing, not vestigial. And the knob is spent upward:
    0.3 shaved the largest cluster 64 → 62 for 3.0% of covered articles dropped and a bad-cluster
    count rising 4 → 5 (fails the "well down" prong); 0.4 dropped 5.6% — over the bar — and
    shrank the scored set 73 → 58. Both measured, neither adopted. If the blob returns, the next
    lever is what the linkage graph is made of, not this threshold.

    Measure any candidate with ``examples/audit_clustering_change.py --link-quorum <q>`` — from a
    container that carries the deploy environment (``dc run --rm -T api …``), or the baseline it
    prints is fiction."""
    v = os.environ.get("RWE_CLUSTER_LINK_QUORUM", "").strip()
    try:
        q = float(v)
    except (TypeError, ValueError):
        return clustering.DEFAULT_LINK_QUORUM
    return q if 0.0 <= q <= 1.0 else clustering.DEFAULT_LINK_QUORUM


def min_support() -> int:
    """Merge SUPPORT BREADTH — distinct members each side must contribute to the passing
    cross-pairs before two clusters join. ``RWE_CLUSTER_MIN_SUPPORT`` overrides; UNSET falls back
    to ``clustering.DEFAULT_MIN_SUPPORT`` = 1 = off and byte-identical.

    **The failure it targets** (production, 2026-08-25): a Guardian article about *The Odyssey*
    becoming Nolan's highest-grossing film served inside the Spider-Man *Brand New Day* box-office
    story. The probe, verbatim::

        odyssey<->spider (direct): shared=[]                                        n=0 j=0.000
        odyssey<->bridge:  shared=[becomes, film, grossing, highest, odyssey]        n=5 j=0.312
        bridge<->spider:   shared=[box, fourth, man, office, spider, tops]           n=6 j=0.286

    The bridge is a real comparative round-up — "'Spider-Man' tops box office in fourth weekend;
    'The Odyssey' becomes Nolan's highest-grossing film" — and it is genuinely, correctly similar
    to both sides. Every edge here is legitimate; the two events simply have no edge to each other.
    So this is not a vocabulary defect and no lexicon can address it: there is nothing to
    stop-list, and stop-listing the film names would break the real stories. It is a GRAPH defect —
    two dense components joined through a single article — and the fix has to be structural.

    **Why breadth and not a higher quorum.** Both existing linkage rules are blind here. The
    quorum measures the passing FRACTION of cross-pairs, and the bridge wins several of them
    honestly (it shares six real tokens with the Spider-Man side), so the fraction is satisfied
    while every one of those passing pairs runs through the same single article. Raising the
    fraction was already measured and spent: 0.3 cost 3.0% of covered articles and raised the
    bad-cluster count, 0.4 cost 5.6% — over the bar — because a long-running story's coverage
    legitimately diverges, so the fraction falls exactly where clusters are largest. Breadth has
    no such size coupling: a 60-article story has many distinct members participating however low
    the fraction runs, and a bridge weld has exactly one. That is why this knob can be spent where
    the quorum could not.

    **Why it does not disturb correct clustering.** The requirement is capped at each side's own
    size, so a two-article story still forms from the one pair that founded it, and a cluster still
    absorbs a matching new article — that article must simply resemble ``min_support`` distinct
    members rather than one. It can only ever REFUSE a merge, never create one, so no existing
    story can grow or change composition because of it; the reachable effects are a split or no
    change. 1 = off. 2 = "two is corroboration", the same standard ``GEO_MIN_CONSENSUS`` already
    applies to event geography.

    **MEASURED 2026-08-25 AND REJECTED at 2.** Live catalog, 27,856 articles, full production
    stack: 371 clusters split, covered articles 6,122 → 5,607, **droppedOut 534 = 8.7%** against
    the 5% bar. Stories 1,499 → 1,550 and largest 60 → 56 (the intended direction), and the
    independent signal improved (0/63 bad at mean 0.953 → 0/51 at 0.967) — but that scored set
    shrank because clusters left it, which is cost presenting as quality. Blindspot claims
    203 → 149 on the same rows: a third of the product feature, gone.

    The mechanism, recorded because the reasoning that justified this rule is what it refutes.
    The docstring above argued that "a genuine new article resembles several members of the
    cluster it joins". On the catalog that is false often enough to matter: coverage of a real
    story diverges in vocabulary as it runs, so a legitimate late article routinely matches
    exactly ONE member — the one phrased like it. The dropped list is the receipt, and it is not
    template chaff: the Harry/Meghan story lost 5 of 60, the England v Pakistan Test live blog
    lost 6, the Diamondbacks/Ketel Marte story lost 6 across 4 publishers. Requiring breadth of
    the RECEIVING side taxes exactly the growth that makes a story a story.

    What the run could NOT show: the ``odyssey-spiderman`` exhibit read ``separated → separated``
    on both sides, i.e. the weld had aged out of the window. So this measurement priced the rule
    without ever exercising the defect it was built for. The 8.7% is real; the benefit is
    unmeasured, which is the weaker half of a case that already fails its bar.

    ``support_scope`` (below) is the registered follow-up: the same corroboration question asked
    only where the measured cost is not — see its docstring.

    Measure any candidate with ``examples/audit_clustering_change.py --min-support <n>`` from a
    container carrying the deploy environment, against the bars registered on ``link_quorum``:
    adopt on droppedOut ≤ 5% of covered articles with no story-count fall."""
    v = os.environ.get("RWE_CLUSTER_MIN_SUPPORT", "").strip()
    try:
        n = int(v)
    except (TypeError, ValueError):
        return clustering.DEFAULT_MIN_SUPPORT
    return n if n >= 1 else clustering.DEFAULT_MIN_SUPPORT


#: The announcement-template lexicon (Phase A registration, 2026-08-17 — the twelve tokens,
#: verbatim; ``audit_template_edges.py`` measures this exact set). Tokens that name the SHAPE of
#: a reveal headline rather than its subject. They keep counting toward Jaccard — recall inside
#: real stories is untouched — but under the template gate they cannot be the ENTIRE case for an
#: edge. The production-confirmed anchor exhibit: "'X-Men' cast, release date revealed at D23"
#: welded to The Paper / Mirzapur / DJI Osmo over shared sets drawn wholly from this vocabulary
#: (j up to 0.444, zero distinctive tokens), while the genuine X-Men pair also shared
#: {men, d23} and survives the rule. Phase A census over 26,565 live articles: exactly 3 edges
#: of 19,001 rest solely on this set — all three are that exhibit's false edges.
TEMPLATE_TOKENS = frozenset(("cast", "date", "episode", "everything", "know", "premiere",
                             "release", "revealed", "season", "specs", "teaser", "trailer"))

#: CANDIDATE lexicons (registered 2026-08-24, BEFORE measurement — the Phase A discipline).
#: Same rule, same hook, new vocabularies: tokens naming the SHAPE of a headline, never its
#: subject, so a shared work title or venue always counts as distinctive evidence and only
#: pure-boilerplate edges can be vetoed. Neither ships until
#: ``examples/audit_clustering_change.py --template-lexicons …`` measures it against the live
#: catalog under the split bars.
#:
#: ``tracker`` — box-office / OTT day-counter chains. The exhibits: "Batwara box office
#: collection day 2" welded to "Vishwanath box office collection day 2" (different films,
#: rubric rules 5+6) and the comparative "Vishwanath trails Jana Nayagan…" chain (rule 1) —
#: while "Batwara day 3" vs "Batwara day 2" (same film, rule 2) shares its title token OUTSIDE
#: this set and must survive.
TRACKER_TOKENS = frozenset((
    "box", "office", "collection", "collections", "day", "days", "worldwide", "gross",
    "grosses", "crore", "crores", "earns", "earned", "earnings", "weekend", "occupancy",
    "advance", "booking", "bookings", "ott", "streaming", "online", "debut", "opening"))

#: ``preview`` — recurring-fixture previews. The exhibit: two tennis matches, different days,
#: different players, welded on "preview, head-to-head, odds" (rules 3b+5). A genuine
#: same-match pair shares its player names outside this set.
PREVIEW_TOKENS = frozenset((
    "preview", "previews", "prediction", "predictions", "odds", "pick", "picks", "head",
    "live", "stream", "streams", "channel", "lineup", "lineups", "injury", "betting",
    "tips", "start", "time", "times", "watch", "kickoff", "fixture", "fixtures",
    "schedule", "highlights"))

#: ``recall`` — consumer-safety recall coverage (registered 2026-08-25, BEFORE measurement).
#: The production exhibit that named the genre: "Frozen fruit bars recalled nationwide over
#: possible glass contamination" (Fox Business) welded into the Prestige eye-drops recall
#: story. The two could never edge DIRECTLY (one shared token, j 0.067) — the weld is a
#: boilerplate BRIDGE: against an in-cluster headline of the shape "Eye drops recalled
#: nationwide over possible contamination", the fruit-bar headline shares
#: {contamination, nationwide, possible, recalled} — four tokens, all recall-shape, zero
#: distinctive, clearing both the shared floor and Jaccard (0.333). Shape only, never
#: subject: hazards (glass, listeria) and packaging (bottles, bars) stay OUT, so genuine
#: same-recall pairs keep their product tokens as evidence ({eye, drops} survives the gate).
RECALL_TOKENS = frozenset((
    "recall", "recalled", "recalls", "recalling", "contamination", "contaminated",
    "nationwide", "possible", "potential", "affected", "product", "products",
    "warning", "warns", "sold", "stores", "urged", "consumers"))

#: Name -> vocabulary. ``announce`` adopted Phase B (2026-08-17); ``tracker``/``preview``
#: adopted 2026-08-24; ``recall`` adopted 2026-08-25 (measured: the one split was the
#: fruit-bars/eye-drops weld itself resolving, 0.0% dropped, signal untouched).
TEMPLATE_LEXICONS = {"announce": TEMPLATE_TOKENS, "tracker": TRACKER_TOKENS,
                     "preview": PREVIEW_TOKENS, "recall": RECALL_TOKENS}


def template_lexicons() -> "tuple[str, ...]":
    """Which lexicons the sole-template-evidence rule consults — ``("announce",)`` unless
    ``RWE_CLUSTER_TEMPLATE_LEXICONS`` names a comma-separated set. Unknown names are dropped;
    an empty survivor list falls back to the default, never to "no lexicon" — junk must not
    silently widen or disable an adopted gate.

    **Measured 2026-08-24, ADOPT** (``audit_clustering_change.py --template-lexicons
    announce,tracker,preview``, twice against the live catalog at ~27,990 articles, stable
    across both windows): stories +1 (1,517 → 1,518), largest cluster unchanged at 60,
    covered articles NET +3 (2 dropped, 5 newly covered — 0.0% against the 5% bar), the
    independent signal untouched (0/68 bad, mean 0.958), blindspot claims unchanged. The one
    split is the failure class the candidates were registered against: a 10-article/3-publisher
    "picks, odds: MLB best bets, predictions" betting-preview chain (a/p 3.3) dissolved, its
    genuine members re-covered elsewhere. The tennis-previews exhibit stayed separated.
    Adopted 2026-08-24: ``deploy/docker-compose.yml`` defaults the full set (the template-gate
    adoption path); ``announce`` alone is the revert."""
    raw = os.environ.get("RWE_CLUSTER_TEMPLATE_LEXICONS", "").strip().lower()
    names = tuple(n for n in (p.strip() for p in raw.split(",")) if n in TEMPLATE_LEXICONS)
    return names or ("announce",)


def _lexicon_union(names: "tuple[str, ...]") -> frozenset:
    out: frozenset = frozenset()
    for n in names:
        out = out | TEMPLATE_LEXICONS.get(n, frozenset())
    return out or TEMPLATE_TOKENS


def derived_boilerplate_on() -> bool:
    """Corpus-derived boilerplate gate — **measured 2026-08-25 and REJECTED. Do not turn this
    on.** (The flag survives as the audit's instrument only.)

    The idea was the generalisation the manual lexicons approximate one exhibit at a time:
    derive the shape vocabulary from the build's own articles — boilerplate is frequent across
    the window AND present on essentially every day of it, while event tokens BURST in their
    story's own days. Two registered conditions: ``df >= boilerplate_df()`` and days-present
    ``>= boilerplate_days()``.

    The measurement (``audit_clustering_change.py --derived-boilerplate``, 27,817 live
    articles): 1,601 derived tokens, 335 clusters split, **1,036 articles dropped — 17.0% of
    covered against the 5% bar** — story count fell, and the damage list is every genuine
    running story: Harry/Meghan (60 articles, 36 publishers) shattered into 7 fragments,
    Hickerson −21, the week's Trump stories −20/−18/−17. The subsumption self-check PASSED
    (with only ``announce`` beside it the numbers were identical — the derivation swallowed the
    manual lexicons whole), which is exactly what makes the rejection informative:

    **The mechanism, kept so this avenue is not re-walked:** day-spread separates *burst*
    events from boilerplate, but a WEEK-LONG RUNNING STORY is present every day by definition —
    "harry", "tariffs", "canada" meet both conditions just as "recalled" and "odds" do. No
    distributional statistic over tokens distinguishes "frequent because template" from
    "frequent because important"; the manual lexicons work precisely because they encode the
    human judgment the corpus cannot make. With ``use_idf`` (re-weighting form, −10.5%) and
    this (gate form, −17.0%), the corpus-statistical family is now closed in both its forms.
    What separates "recalled nationwide" from "harry meghan" is MEANING, not distribution —
    the registered next lever is the banded semantic verifier
    (docs/EVENT_IDENTITY_RUBRIC.md; V0 sized the band at ~451 pairs/day)."""
    v = os.environ.get("RWE_CLUSTER_DERIVED_BOILERPLATE", "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def boilerplate_df() -> int:
    """Distinct headlines a token must appear in before it can be boilerplate (window df)."""
    return _env_int("RWE_CLUSTER_BOILERPLATE_DF", 25)


def boilerplate_days() -> int:
    """Distinct UTC days a token must appear on before it can be boilerplate — the burst
    separator: an event's name tokens concentrate in its own days; shape tokens span the
    window. 5 of the default 6-day window."""
    return _env_int("RWE_CLUSTER_BOILERPLATE_DAYS", 5)


def derived_boilerplate(arts: list, cap: int = 0, hyphen: bool = False, uni: bool = False, *,
                        min_df: int = 25, min_days: int = 5) -> frozenset:
    """The corpus-derived boilerplate set: tokens meeting BOTH registered conditions over the
    build's own articles (the same token sets the clusterer scores — ``article_tokens`` at the
    build's cap and hyphen mode). One deterministic pass, no external state, no curated list:
    same articles → same set."""
    df: dict = {}
    days: dict = {}
    for a in arts:
        d = (a.get("publishedAt") or "")[:10]
        for t in article_tokens(a, cap, hyphen, uni):
            df[t] = df.get(t, 0) + 1
            if d:
                days.setdefault(t, set()).add(d)
    return frozenset(t for t, n in df.items()
                     if n >= min_df and len(days.get(t, ())) >= min_days)


def event_band_hi() -> float:
    """The ambiguity band's upper edge (``RWE_EVENT_BAND_HI``, default 0.5): an admitted edge
    with Jaccard below this is IN BAND and may consult a persisted semantic verdict; at or above
    it the lexical evidence decides alone (S4: the high-overlap region auto-decides with zero
    measured errors, so the judge is never asked what tokens already answer)."""
    v = os.environ.get("RWE_EVENT_BAND_HI", "").strip()
    try:
        q = float(v)
    except (TypeError, ValueError):
        return 0.5
    return q if 0.0 < q <= 1.0 else 0.5


#: Band pairs one build may emit to the judge queue — a cost bound, not a correctness one:
#: unjudged pairs behave exactly as production regardless, and the next build re-emits.
EVENT_BAND_OUT_CAP = 2000


def _event_identity_closure(arts: list, cap: int, hyphen: bool, verdicts: dict,
                            band_hi: float, stats: Optional[dict] = None,
                            band_out: Optional[dict] = None, *, uni: bool = False):
    """``evidence(x, y)`` for the banded semantic judge (``event_identity``): consult a persisted
    verdict ONLY for in-band edges, veto ONLY on a confident ``different_event``, and note every
    in-band edge that has no verdict yet so the out-of-band worker can earn one. Everything else
    — high-overlap edges, unjudged pairs, ``same_event``, ``uncertain`` — is byte-identical to
    production. Deterministic: a pure function of the build's rows and the verdict dict."""
    import event_identity
    toks = [article_tokens(a, cap, hyphen, uni) for a in arts]

    def _url(i: int) -> str:
        return str(arts[i].get("url") or arts[i].get("id") or arts[i].get("headline") or "")

    def ok(x: int, y: int) -> bool:
        if clustering.jaccard(toks[x], toks[y]) >= band_hi:
            return True                                   # decided lexically, judge never asked
        key = event_identity.pair_key(_url(x), _url(y))
        v = verdicts.get(key)
        if v == "different_event":
            if stats is not None:
                stats["eventEdgeVetoed"] = stats.get("eventEdgeVetoed", 0) + 1
            return False
        if v is None and band_out is not None and key not in band_out \
                and len(band_out) < EVENT_BAND_OUT_CAP:
            a, b = arts[x], arts[y]
            band_out[key] = {
                "pair_key": key, "url_a": _url(x), "url_b": _url(y),
                "title_a": a.get("headline") or "", "dek_a": a.get("description") or "",
                "published_a": a.get("publishedAt") or "",
                "title_b": b.get("headline") or "", "dek_b": b.get("description") or "",
                "published_b": b.get("publishedAt") or ""}
        return True
    return ok


def unicode_words() -> bool:
    r"""Unicode word segmentation for headline tokens (``RWE_CLUSTER_UNICODE_WORDS``).

    **Two modes with opposite verdicts, so read the mode before the name.** ``fallback`` was
    measured on the live catalogue 2026-08-28, ADOPTED, and is **ON in production**. ``True``
    (replace) was measured 2026-08-27 and is **REJECTED** — do not turn that one on.

    The defect, measured 2026-08-27 and not in dispute: `clustering.title_tokens` matches
    ``[a-z0-9]+``, which yields **zero tokens** for Korean, Arabic, Chinese, Japanese, Russian,
    Tamil and Hindi headlines. `clustering.pair_admits` rejects anything below
    ``MIN_TITLE_TOKENS`` before any other test, so those articles **cannot join a story under any
    configuration**. Production, same run: those languages contributed 472 window articles and
    **one** in-story article — 0.2%, against 29% for English. It is not a participation problem
    with a tuning answer; it is a structural exclusion.

    The candidate widens the class to ``\w`` plus combining marks (abugidas fragment without them)
    and emits character bigrams for scripts with no word separator (CJK, Thai). What it does
    **not** do is fold diacritics, so ``Erdoğan`` and ``Erdogan`` remain different tokens and two
    English headlines about one event still fail to cluster. That is a separate candidate with a
    separate risk profile — folding merges Turkish ``ı``/``i`` and German ``ö``/``o`` — and pairing
    them would make one measurement unable to attribute either result.

    ## The production measurement, and why there are now two modes

    ``--unicode-words`` (**replace**) ran on the live catalogue 2026-08-27 and is **REJECTED**:

        rescued 78 articles, cost 149 that were already in stories   (1.9x the benefit)
        reached 78 of the 2,630 structurally-excluded articles       (3.0%)
        Vietnamese covered 32 -> 0,  Turkish 22 -> 11,  net coverage -44

    Two findings, and the second is the larger one. **The cost lands on accented Latin**: Vietnamese
    and Turkish words fragment into short ASCII pieces today, many unrelated articles share those
    pieces, and replacing them with whole words dissolves the clusters that coincidence built. And
    **the fix does not fix the thing it was for** — 3% reach. Giving a Korean headline tokens does
    not give it a Korean peer to cluster WITH: the binding constraint on international stories is
    corpus density per language, not only the tokenizer.

    ``fallback`` takes the Unicode path only when the ASCII tokenizer yields fewer than
    ``MIN_TITLE_TOKENS``. An article that already clusters keeps its exact token set, so the
    149-article cost is **zero by construction** — and the live run confirmed it rather than
    assuming it:

        79 structurally-excluded articles reached a story,  0 lost
        reachable population 7,019 -> 7,019 covered         (0 in, 0 out)
        0 clusters split, 0 merged, blindspot 220 -> 220, both in-window exhibits unchanged
        ar 0 -> 9,  ja 1 -> 13,  ko 0 -> 4,  ru 0 -> 2

    **Live in production since 2026-08-28**, confirmed by re-running the audit afterwards: the
    baseline arm now reports the excluded population at 79 covered rather than 0, and applying
    the flag again buys nothing (``THE BENEFIT IS ZERO``, which is what a confirmed adoption
    looks like from this instrument).

    What it did NOT fix: 79 of 2,653 excluded articles is 3.0% reach. Giving a Korean headline
    tokens does not give it a Korean peer to cluster WITH, and the other 97% still join nothing.
    The binding constraint on international stories is corpus density per language; the tokenizer
    was blocking the mechanism underneath it. M14's density question becomes askable now, not
    answered.

    This function decides the story partition for the whole product, and a tokenizer candidate has
    already measured worse than the disease once — see :func:`hyphen_compounds`."""
    v = os.environ.get("RWE_CLUSTER_UNICODE_WORDS", "").strip().lower()
    if v == "fallback":
        return "fallback"
    return v in {"1", "true", "yes", "on"}


def hyphen_compounds() -> bool:
    """Candidate tokenizer extension — **measured 2026-08-24 and REJECTED. Do not turn this
    on.** (``RWE_CLUSTER_HYPHEN_COMPOUNDS`` survives as the audit's instrument only.)

    The idea: hyphenated compounds also contribute their joined form, so "X-Men" carries
    "xmen" instead of surviving only as the generic fragment "men" (the xmen-pair false
    split). The measurement (``audit_clustering_change.py --hyphen-compounds``, 27,995 live
    articles): 121 clusters split, 28 merged, 162 articles dropped (2.6% of covered), story
    count FELL 1,516 → 1,511 — the min_publishers cliff — with collateral far from any
    hyphen exhibit ("Eagles vs Patriots Postgame" −6, Kuril Islands −4, Russian drone strike
    −4). The mechanism, worth keeping so the next tokenizer idea meets it: adding a token to
    BOTH sets grows the UNION even when the compound is not shared, so every pair that shared
    fragments but not compounds saw its Jaccard fall — a global token-set change has diffuse
    blast radius, exactly like the IDF revert (``use_idf``). Evidence RULES (the lexicon
    gate) stay targeted; representation changes do not."""
    v = os.environ.get("RWE_CLUSTER_HYPHEN_COMPOUNDS", "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def template_gate() -> bool:
    """The sole-template-evidence rule — **OFF by default** (Phase B: measured, not yet
    adopted; ``RWE_CLUSTER_TEMPLATE_GATE=1`` enables without a deploy). When on, a pairwise
    edge must share at least one token OUTSIDE :data:`TEMPLATE_TOKENS` — the same
    "distinctive evidence" concept ``MIN_SHARED_TOKENS`` was designed around, enforced at the
    edge level through the ``evidence`` hook, so admission, quorum cross-pair scoring and the
    repair re-cluster all consult one rule. Junk values fall back to off, never to a guess."""
    v = os.environ.get("RWE_CLUSTER_TEMPLATE_GATE", "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def _template_closure(arts: list, cap: int, stats: Optional[dict] = None,
                      lexicon: frozenset = TEMPLATE_TOKENS, hyphen: bool = False,
                      uni: bool = False):
    """``evidence(x, y)`` for the template gate: True iff the pair shares >= 1 token outside
    ``lexicon`` (the union of the active lexicons — announce alone in production). Token sets
    are the build's own (:func:`article_tokens` at the build's dek cap and hyphen mode), so the
    gate judges exactly the signal that admitted the pair. Pure and deterministic."""
    toks = [article_tokens(a, cap, hyphen, uni) for a in arts]

    def ok(x: int, y: int) -> bool:
        if (toks[x] & toks[y]) - lexicon:
            return True
        if stats is not None:
            stats["templateEdgeVetoed"] = stats.get("templateEdgeVetoed", 0) + 1
        return False
    return ok


def _and_evidence(*fns):
    """AND-compose optional ``evidence`` closures (None entries drop; empty -> None)."""
    live = [f for f in fns if f is not None]
    if not live:
        return None
    if len(live) == 1:
        return live[0]

    def ok(x: int, y: int) -> bool:
        return all(f(x, y) for f in live)
    return ok


def _and_merge_ok(*fns):
    """AND-compose optional CLUSTER-level ``merge_ok`` gates (None entries drop; empty -> None).

    Structurally the same fold as :func:`_and_evidence`, kept separate because the callables have
    a different contract — member index LISTS rather than a pair of indices — and one composer
    silently accepting both is how a pairwise closure ends up wired into a cluster-level hook."""
    live = [f for f in fns if f is not None]
    if not live:
        return None
    if len(live) == 1:
        return live[0]

    def ok(a: list, b: list) -> bool:
        return all(f(a, b) for f in live)
    return ok


_GEO_VETO_MODES = ("pair", "growth")

#: Votes the WINNING country of a cluster side's located consensus needs before that side's
#: testimony may veto a merge (V-growth-2). The veto fires iff the two consensuses are disjoint
#: and EITHER side clears this bar — corroboration on one side is enough to reject a
#: thinly-located joiner, because a false merge is the catastrophic direction while a rejected
#: true dissenter is bounded (a single article, and the 1% coverage bar measures the aggregate).
#:
#: Three measured failures shaped this rule, each one edit older than the last:
#:
#: * Run C's SIZE-based gate (either side ≥ MIN_CHAINABLE members) let a side whose consensus
#:   rested on ONE located member veto — a sample of one, the ``MIN_LOCATED_FOR_TRUST`` defect
#:   in miniature — and split Ronaldo's wedding into two stories that the duplicate-merge pass
#:   then could not rejoin. Under this rule two uncorroborated samples never veto each other.
#: * The same gate's "both sides small" exemption let two 2-member seeds of DIFFERENT
#:   earthquakes (Colombia, Indonesia) fuse ungated on template vocabulary, and the poisoned
#:   {CO, ID} tie-consensus then overlapped everything. Under this rule the US pair's
#:   corroborated consensus (2 votes) rejects the disagreeing seed.
#: * The first V-growth-2 draft — a symmetric ≥2-located floor per side — never survived to the
#:   box: its own unit test showed a SINGLETON always fails a per-side floor, so clusters absorb
#:   located-disagreeing singletons one at a time and single linkage is rebuilt by absorption.
#:   Corroboration is asymmetric on purpose.
GEO_MIN_CONSENSUS = 2


def geo_veto() -> str:
    """X4 entity-evidence veto — **``growth`` in production** (adopted 2026-08-16 with X5b;
    ``deploy/docker-compose.yml`` defaults it, measurement record in
    ``docs/STORY_ENTITY_EVIDENCE_PLAN.md``: runs C/E — bad clusters 4 → 2, 0.3% coverage at
    net −2 articles, Ronaldo-safe under corroboration). ``RWE_CLUSTER_GEO_VETO`` overrides;
    UNSET falls back to "" = off, so an environment without the deploy's variables loses the
    veto — the same library-vs-deploy divergence ``link_quorum`` documents, guarded by the same
    audit environment check. Junk values fall back to off, never to a guess.

    The evidence is ``eventCountries`` — already batched onto every clustering row by ``_fetch``
    and carried through ``discover.feed_article_to_article``, consumed until now only AFTER
    clusters form (geoCoherence, trust, blindspot withholding, repair targeting). The veto asks
    whether the same fact can arrive at EDGE time. It is **fail-open**: it fires only on positive
    disagreement — both sides located, country sets disjoint — so an unlocated pair is
    byte-identical to production and a GKG outage silently disables it rather than clustering.
    Being a veto, it can only remove edges: it cannot create a false merge, and its entire risk
    budget is false splits (the axis the audit's split tables and must-keep set measure).

    * ``pair``: the veto joins the pairwise gate — admission, quorum cross-pair scoring and the
      repair re-cluster all consult it, because they share ``pair_ok`` by construction. Measured
      2026-08-16 (run D) and REJECTED: it dissolves legitimate multi-country stories and the
      story count falls.
    * ``growth`` (V-growth-2): a merge is vetoed iff the two sides' located consensuses are
      disjoint AND either side's winning vote is corroborated (``GEO_MIN_CONSENSUS`` — the three
      measured failures the rule is built from are documented there). No size rule: formation
      pairs are two samples of one and fail open by construction. The two-country-event guard is
      the consensus overlap: a member located in both countries of a genuine cross-border story
      overlaps either side's consensus (the ``_geo_coherence`` mechanism) and is admitted. The
      same rule gates the duplicate-merge pass, because one-sided corroboration can leave the
      pooled located set at 3 — under ``MIN_LOCATED_FOR_TRUST``, where that pass's coherence
      guard is silent and would otherwise rejoin what the veto severed."""
    v = os.environ.get("RWE_CLUSTER_GEO_VETO", "").strip().lower()
    return v if v in _GEO_VETO_MODES else ""


def entity_merge_min() -> int:
    """X5b entity-corroborated merge recall — **2 in production** (adopted 2026-08-16;
    ``deploy/docker-compose.yml`` defaults it; run-3 record in
    ``docs/STORY_ENTITY_EVIDENCE_PLAN.md``: 44 joins, zero dropped, largest 71 = the Mangione
    family, ten of twelve exhibits clean with the two residual riders named). UNSET falls back
    to 0 = off, and the pass ALSO requires the entity mapping — every serving call site fetches
    it through ``_entities_for`` when this is > 0, so an environment without the deploy's
    variables (or without backfilled ``article_entities``) degrades to the lexical build rather
    than erroring. The value is the MINIMUM shared corroborated non-noise consensus names two
    stories need before a join is even proposed — 2 by design: one shared name can be a
    type-level responder agency (the USGS receipt at :func:`entity_noise`), two independent
    corroborated names is the measured signature of the same event family (Farage/Clacton
    shared 3, Mangione's court stories 2-3)."""
    v = os.environ.get("RWE_STORY_ENTITY_MERGE", "").strip()
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return n if n >= 0 else 0


def hero_guard() -> bool:
    """Ranked story-hero selection + cross-story reuse rejection — **on in production** (adopted
    2026-08-16; ``deploy/docker-compose.yml`` defaults ``RWE_STORY_HERO_GUARD=1``; measurement
    record in ``docs/STORY_HERO_IMAGES.md``). UNSET falls back to off — the legacy
    representative-first hero — so an environment without the deploy's variables changes nothing,
    the same library-vs-deploy divergence every clustering knob documents. Junk values fall back
    to off, never to a guess.

    Presentation only: the flag cannot move an article between stories or change a story's
    membership, id, rank, or any trust signal — it only changes WHICH member's image fronts the
    card, and whether a branding asset is allowed to (it is not: the imageless card renders the
    coverage-distribution figure instead, a designed state)."""
    v = os.environ.get("RWE_STORY_HERO_GUARD", "").strip().lower()
    return v in {"1", "true", "yes", "on"}


#: Platform/share-chrome names GDELT extracts from page furniture rather than the story. The
#: 2026-08-16 production df table is the receipt: instagram 127, facebook 63, youtube 32 in a
#: catalog where the single most-covered story's entities reached df 30. One definition, shared
#: by the X5 separability instrument and the X5b merge pass, so the noise contract cannot drift.
ENTITY_NOISE_PLATFORMS = frozenset({"instagram", "facebook", "youtube", "twitter", "tiktok",
                                    "whatsapp", "telegram", "linkedin", "reddit", "pinterest"})


def entity_noise(name: str) -> bool:
    """Names that are ABOUT the page or the press, not the event — identified by IDENTITY, not
    frequency. A df floor punishes exactly the biggest events' entities ("luigi mangione"
    reached df 30 because the story was big); an outlet-registry resolve catches bylines and
    media names (reuters, associated press, cnn) whatever their df, and a country normalization
    catches geography extracted as entities ("united states" as an organization, df 638).

    KNOWN residual, named so it is not rediscovered: type-level responder agencies. The two
    Colombia-quake stories' consensuses intersect in nothing but "u s geological" — USGS attends
    every earthquake — and the X5b minimum of TWO shared names is what keeps that class from
    proposing merges, because curating every agency is a slope this filter refuses to start
    down."""
    if name in ENTITY_NOISE_PLATFORMS:
        return True
    if location.normalize_country(name):
        return True
    return outlet_registry.resolve(name) is not None


def tag_noise(name: str) -> bool:
    """:func:`entity_noise`, MINUS the geography test — the noise contract for TOPICS/TAGS.

    Same first two rules: a platform name and an outlet name are about the page or the press, not
    about the event, and that is true whatever the name is being used for.

    The third rule is not. ``entity_noise`` drops anything that resolves to a country because
    geography is a SEPARATE clustering channel (``_geo_closures``) — a place arriving through the
    entity channel there is a duplicate vote with none of the geo channel's guards, which is why
    "united states" at df 638 had to go. A tag has no second channel and no vote to duplicate: the
    place a story happens in is one of the most useful things to know about it, and dropping it
    would delete "Democratic Republic of the Congo" from an outbreak story — the single most
    specific tag it has.

    Kept as a separate function rather than a parameter on the original because the two rules are
    genuinely different, and a shared one with a flag would invite the next caller to guess."""
    if name in ENTITY_NOISE_PLATFORMS:
        return True
    return outlet_registry.resolve(name) is not None


def _located_consensus(members: list) -> "tuple[frozenset, int]":
    """``(top-vote countries, winning vote count)`` over MEMBER DICTS — the dup-merge pass's
    counterpart of the index-based closure in ``_geo_closures``, same vote semantics (one vote
    per located member per country, ties kept)."""
    votes: dict = {}
    for m in members:
        for c in _member_countries(m):
            votes[c] = votes.get(c, 0) + 1
    if not votes:
        return frozenset(), 0
    top = max(votes.values())
    return frozenset(c for c, v in votes.items() if v == top), top


def _geo_closures(arts: list, mode: str, stats: Optional[dict] = None) -> tuple:
    """``(evidence, merge_ok)`` for :func:`clustering.cluster` — ``(None, None)`` when off, so the
    clusterer's fast path survives byte-identical.

    Country sets are precomputed over EXACTLY the list handed to ``cluster``, because both
    callables receive indices into it — a repair pass must build its own closures over its own
    sublist, and does. ``stats`` (when given) is incremented in place; that is the audit's
    telemetry, and passing ``None`` (every non-audit caller) skips the counting entirely."""
    if not mode:
        return None, None
    countries = [frozenset(_member_countries(a)) for a in arts]

    def bump(key: str) -> None:
        if stats is not None:
            stats[key] = stats.get(key, 0) + 1

    if mode == "pair":
        def evidence(x: int, y: int) -> bool:
            cx, cy = countries[x], countries[y]
            bump("pairChecked")
            if not cx or not cy:
                return True                     # fail-open: absence is never disagreement
            bump("pairBothLocated")
            if cx & cy:
                return True
            bump("pairVetoed")
            return False
        return evidence, None

    def consensus(idxs: list) -> "tuple[frozenset, int]":
        """``(top-vote countries, winning vote count)`` — ``_geo_coherence``'s logic, not the
        union. A union only widens as a false merge grows, so the more wrong a cluster the less a
        union-based test could say; the mode country stays put while the tail accumulates."""
        votes: dict = {}
        for i in idxs:
            for c in countries[i]:
                votes[c] = votes.get(c, 0) + 1
        if not votes:
            return frozenset(), 0
        top = max(votes.values())
        return frozenset(c for c, v in votes.items() if v == top), top

    def merge_ok(a: list, b: list) -> bool:
        """V-growth-2: veto iff the located consensuses are disjoint and EITHER side's winning
        vote is corroborated — see ``GEO_MIN_CONSENSUS`` for the three measured failures this
        rule is built from. No size rule at all: formation pairs are two uncorroborated samples
        and fail open by construction."""
        bump("mergeChecked")
        ca, ta = consensus(a)
        cb, tb = consensus(b)
        if not ca or not cb or (ta < GEO_MIN_CONSENSUS and tb < GEO_MIN_CONSENSUS):
            return True                 # fail-open: no testimony, or nothing but samples of one
        bump("mergeGated")
        if ca & cb:
            return True
        bump("mergeVetoed")
        return False
    return None, merge_ok


def entity_veto() -> bool:
    """X5c — whether a corroborated ENTITY disagreement can refuse a cluster merge.
    ``RWE_STORY_ENTITY_VETO=1`` enables it; unset/0 is off and byte-identical.

    **The asymmetry this closes.** The clustering stack has always had two independent evidence
    channels, and has only ever spent one of them in both directions. Geography can *refuse* a
    merge (``_geo_closures``' growth veto at build time, the located-consensus block inside
    ``_merge_duplicates`` at the aggregate stage). Entities could only ever *propose* one — X5b
    (``_merge_by_entities``) adds merges the text missed and has no way to say a text-similar
    merge is wrong. So a text signal with no geography behind it (entertainment, business,
    sport — precisely the domains where the recorded welds live) had no second opinion at all.

    This is the same rule as the geo veto, over the other channel: veto iff BOTH sides carry a
    corroborated entity consensus and those consensuses share NO name. Everything else fails
    open — one side unextracted, one side uncorroborated, any overlap at all.

    **Why the coverage objection does not apply here.** X6 Phase 0 killed entities as an edge
    *admission* channel on coverage: 24% of articles carry extracted entities, so a pairwise
    entity test is blind most of the time. Two things make the cluster-level veto a different
    proposition. Coverage AGGREGATES — ``_story_entity_consensus`` asks for a name carried by
    ≥ 2 members, so the question is whether a CLUSTER has extraction, not whether an article
    does, and that odds improve with exactly the cluster size where a weld does damage. And the
    direction is safe: absence of evidence fails open, so the uncovered majority is untouched
    rather than mis-served. The same Phase 0 run is also the receipt that the signal works where
    it exists — in the Mirzapur weld the two articles that carried entities shared ZERO names,
    discriminating perfectly on the pair the lexical gate needed a whole lexicon to reach.

    **MEASURED 2026-08-25 AND ADOPTED.** Live catalog, 27,876 articles, full production stack:
    **droppedOut 0 of 6,127 covered articles (0.0%)**, stories 1,501 → 1,502, largest cluster 60
    unchanged, independent signal identical (0/63 bad at mean 0.953 both sides), blindspot claims
    202 → 202. Exactly ONE cluster split — a 15-article/11-publisher Trump-Iran economic
    announcement resolving into two — and no article left a story, so both halves cleared
    ``min_articles``/``min_publishers``. A rule that costs nothing and moves one cluster is the
    shape a veto should have at this coverage: quiet where extraction is absent, decisive where
    two clusters genuinely name different people.

    **Measured bite, 2026-08-25 telemetry over a full build:** 7,466 merge decisions consulted,
    of which **461 (6.2%) had a corroborated consensus on BOTH sides** — the coverage reality,
    quantified: the rule fails open on 93.8% of merges and is simply silent there. Of the 461 it
    could speak to, **24 were vetoed**, and the dup-merge stage vetoed 0. A rule that is quiet on
    fourteen merges in fifteen and decisive on the rest is the shape a 24%-extraction signal
    should have.

    **The split, read 2026-08-25** (baseline forced off with ``-e RWE_STORY_ENTITY_VETO=0``, so
    the comparison was meaningful after adoption). One cluster moved and it came apart cleanly:
    "Trump announces 'most crushing economic operation ever' against Iran", 15 articles from 11
    publishers, into 11/8 + 4/3 — **zero articles dropped, and no piece of two articles or
    fewer**, which is the shape that distinguishes a separation from a shredding.

    What the 4-article piece IS matters more than the arithmetic: "Trump declares economic warfare
    on Iran. **And, SCOTUS to rule on** …" — four two-topic daily briefings from three
    publishers. That is the ROUND-UP BRIDGE class, the same shape as the Odyssey/Spider-Man weld:
    an article genuinely about two events, welded to one of them by the vocabulary it honestly
    shares. The entity channel reached it because the briefings' corroborated consensus and the
    single-event story's do not intersect.

    So the conclusion recorded on ``support_scope`` — that the deterministic line on comparative
    bridges is closed — needs one qualification. It is closed for the DETERMINISTIC-LEXICAL
    approaches; the entity channel reaches the subset of round-up bridges where both sides happen
    to carry distinct corroborated consensuses, for free, at zero measured cost. That is a
    fraction of the class (both sides must clear extraction), not a solution to it. The banded
    judge remains the answer for the 94% of merges where this rule is silent.

    Deliberately NOT registered in ``audit_verifier_band.V1_EXHIBITS``: the two sides here are
    "an article about the Iran announcement" and "a briefing that reports the Iran announcement
    alongside a SCOTUS case", and whether the rubric calls that pair ``different_event`` is a
    genuine judgement rather than an obvious one. The exhibit table is ratified ground truth and
    does not take contestable labels.

    A veto is the only direction offered. Entity evidence proposing merges is X5b's job and is
    measured separately; this knob cannot create a cluster, only decline one.

    **Measuring it after adoption.** ``--entity-veto`` only sets the AFTER side; the BEFORE side
    resolves from the environment, which now carries the adopted default, so a bare run compares
    the rule against itself and prints an honest 0/0/0. To re-measure, turn the baseline off for
    that container:

        dc run --rm -T -e RWE_STORY_ENTITY_VETO=0 api python \
            examples/audit_clustering_change.py --entity-veto --pieces 5"""
    v = os.environ.get("RWE_STORY_ENTITY_VETO", "").strip().lower()
    return v in {"1", "true", "yes", "on"}


#: Members a side must already have before ``groups`` scope asks it for breadth. Two, because
#: "already a group" is the whole distinction the scope draws — one article is a claim, two is a
#: body of coverage.
SUPPORT_GROUP_MIN = 2


def support_scope() -> str:
    """WHERE the ``min_support`` breadth requirement applies. ``RWE_CLUSTER_SUPPORT_SCOPE``:

    * ``any`` (default, and what was measured at 8.7%) — every side with ≥ 2 members must supply
      breadth, including a cluster absorbing a single new article.
    * ``groups`` — breadth is required only when BOTH sides already have ≥ 2 members, so a lone
      article joining a story is never gated.

    Registered 2026-08-25 as the follow-up to the ``min_support`` rejection, and aimed squarely
    at where that cost came from. The measured loss was GROWTH: legitimate late coverage matching
    exactly one member of the story it belongs to. Every one of those is a singleton joining a
    cluster. ``groups`` exempts that case entirely and keeps the requirement for the merge of two
    already-established groups, which is the shape the comparative-bridge weld actually has —
    corroboration is demanded when two bodies of coverage claim to be one event, not when one
    article claims to belong.

    **MEASURED 2026-08-25 at ``--min-support 2 --support-scope groups``: 1.8% dropped, and NOT
    adopted.** Live catalog, 27,885 articles, baseline already carrying the adopted X5c veto:
    106 clusters split (vs 371 under ``any``), covered 6,135 → 6,044, droppedOut 113 = **1.8%**,
    stories 1,508 → 1,553, blindspot claims 200 → 192, independent signal 0/63 at 0.953 → 0/59
    at 0.956. The scope does what it was designed to do — it buys back four-fifths of the ``any``
    variant's cost.

    Adoption is held anyway, on the criterion as registered rather than on the harness's printed
    verdict. That verdict line is a COST check; the bar on ``link_quorum`` reads "largest cluster
    well down, droppedOut ≤ 5%, no story-count fall", and largest cluster here is 60 → 60 —
    unchanged. The ``odyssey-spiderman`` exhibit again read ``separated → separated`` on both
    sides, so the weld was not in the window and the benefit is unobserved for a second run. That
    is 113 articles and 8 blindspot claims spent on something no instrument in the run can see.
    The precedent is X6, recorded in ``docs/STORY_TEMPLATE_GATE.md``: a printed PASS overruled by
    the criterion as registered.

    **The split read settled it: REJECTED 2026-08-25.** ``--pieces 12`` over the biggest split
    clusters, and the 1.9% turns out to be same-event fragmentation, not template separation:

      * "US national debt passes $40tn after doubling in a decade" split from "US debt tops $40
        trillion, doubled under Donald Trump, Biden" — 6 and 6. One event, two phrasings; joining
        exactly that is what clustering is for.
      * "Progressive wins special election to fill Swalwell's California seat" (25) shed "Aisha
        Wahab makes history as the first Afghan American elected to Congress" (2). One election.
      * "US hits Canadian goods with 50% tariffs" fragmented three ways — the tariffs (6),
        Canada matching "dollar for dollar" (6), Canada suspending talks (2). One escalation.
      * "Darline Graham pilloried…" (41) shed "Lindsey Graham's sister makes security blunder in
        car-crash debate" (2). Same debate, same person, named two ways.

    Of the twelve, only the Kalshi promo-code chain (14 articles / 2 publishers) was a separation
    worth having. The story count rising 1,512 → 1,555 is fragments, not events.

    It also INTRODUCED false merges, by the mechanism documented on the containment property:
    refusal is subtractive inside ``cluster()``, but ``_merge_duplicates`` and ``_repair`` then
    receive different inputs and complete joins they previously declined. The run merged a
    Vietnamese football story ("HLV Kim Sang Sik… 24 trận bất bại") into a leukaemia-transplant
    story, "Everton Starting XI vs Crystal Palace" into "Isak returns to Newcastle with
    Liverpool", and "Rory McIlroy… BMW Championship" into "Wyndham Clark's girlfriend reveals
    huge relationship step". A rule that fragments real events and manufactures new welds is not
    paying for its 1.9% however the cost bar reads.

    With both scopes measured and rejected, the structural LINKAGE line on comparative bridges is
    closed. Two things still reach that class. X5c (``entity_veto``) catches the subset where both
    sides carry distinct corroborated entity consensuses — its production split was exactly such a
    case, a two-topic daily briefing separated from a single-event Iran story at zero cost. And
    the banded semantic judge (``event_identity``) is the general answer, implemented and dark for
    want of an API key.

    **Known weaker, stated plainly.** Under ``any`` the rule refuses the Odyssey/Spider-Man weld
    in BOTH merge orders. Under ``groups`` it refuses it in the order production actually takes
    (the bridge's strongest edge is to the Odyssey article at j=0.312 vs 0.286 to Spider-Man, and
    merges are consumed best-first, so the bridge lands on the Odyssey side first and the
    remaining merge is group-to-group) — but a bridge whose strongest edge points the other way
    would join as a singleton, unGated, and the weld would stand. This trades generality for the
    8.7%. Whether that trade is worth taking is a measurement, not an argument:
    ``audit_clustering_change.py --min-support 2 --support-scope groups``."""
    v = os.environ.get("RWE_CLUSTER_SUPPORT_SCOPE", "").strip().lower()
    return v if v in _SUPPORT_SCOPES else "any"


_SUPPORT_SCOPES = ("any", "groups")


def _entity_closures(arts: list, entities: "Optional[dict]", on: bool,
                     stats: Optional[dict] = None) -> tuple:
    """``(evidence, merge_ok)`` for :func:`clustering.cluster` — ``(None, None)`` when off or
    when no entity mapping was fetched, so the clusterer's fast path survives byte-identical.

    ``evidence`` is always ``None``, and that is a statement about the rule rather than an
    omission: a consensus needs ≥ 2 corroborating members, so this test has nothing to say about
    a single pair. It is a cluster-level judgement by construction — which is precisely the
    patent's shape, an independent evidence source consulted when two *clusters* are proposed for
    merging, not when two documents are compared."""
    if not on or not entities:
        return None, None

    def bump(key: str) -> None:
        if stats is not None:
            stats[key] = stats.get(key, 0) + 1

    def consensus(idxs: list) -> frozenset:
        return _story_entity_consensus([arts[i] for i in idxs], entities)

    def merge_ok(a: list, b: list) -> bool:
        bump("entityMergeChecked")
        ca, cb = consensus(a), consensus(b)
        if not ca or not cb:
            return True                     # fail-open: no corroborated testimony on one side
        bump("entityMergeGated")
        if ca & cb:
            return True
        bump("entityMergeVetoed")
        return False
    return None, merge_ok


def anchor_veto() -> bool:
    """Instance-anchor veto (``RWE_CLUSTER_ANCHOR_VETO``) — **candidate, OFF by default**. Stage 0
    item 1 of ``docs/CLUSTERING_APPROACHES_RESEARCH.md``; the rule lives in
    :func:`clustering.instance_anchors` and is spent through :func:`_anchor_closure`.

    The failure class it targets is the one the tokenizer's digit-drop leaves open by
    construction. ``title_tokens`` discards bare numbers (the right trade: a shared year would
    otherwise weld unrelated listicles), so "Wordle hints for September 2" and "…for September 3",
    "Week 3 picks" and "Week 4 picks", "Q2 results" and "Q3 results" reduce to IDENTICAL token sets
    and score Jaccard 1.00 on nothing but the template. No threshold, quorum or lexicon can
    separate them, because the only differing token was thrown away before any rule looked. Every
    lexical lever against this class has been measured and rejected (``derived_boilerplate_on``,
    ``use_idf``, the calendar stop-list that merely moved the collision from "july 21" to "news in
    brief"). The rubric names the fix in rules 3, 3b and 6: numbers and ordinals are identity
    anchors WHEN THEY NAME THE INSTANCE.

    The rule: read the instance anchors (explicit calendar dates; enumerated series slots such as
    week, game, episode, season, Q1–Q4, 2nd Test) SEPARATELY from the similarity tokens, and
    refuse an edge whose two sides carry the same slot with no value in common. It composes
    through the ``evidence`` hook (admission, quorum cross-pairs and the repair re-cluster consult
    one rule) AND the cluster-level ``merge_ok`` hook, and the same corroborated-consensus test is
    applied inside ``_merge_duplicates`` and ``_merge_by_entities`` — because a refusal inside
    ``cluster()`` alone would be quietly undone one stage later by the profile merge, whose
    IDF-weighted profiles of two same-template instances are near-identical (the containment
    failure recorded on ``support_scope``).

    What it deliberately does NOT anchor on, each with its receipt in ``clustering._ANCHOR_SLOTS``:
    ``day`` (rule 2 — the ``batwara-days`` exhibit is one film's run across day 2 and day 3),
    rounds/laps/halves/the word quarter (updates of one occurrence), years (context, not identity,
    inside a six-day window), and bare counts (rule 6's second clause — "12 dead" and "15 dead"
    are one disaster). Fail-open everywhere: a headline with no anchor has nothing to say.

    Pre-registered bars, fixed before any production number: the ``batwara-days`` exhibit stays
    together and ``batwara-vishwanath`` stays separated; droppedOut ≤ 1% of covered articles; no
    story-count fall; the ``--pieces`` read shows series instances separating, not one event
    shredding. Measure with ``audit_clustering_change.py --anchor-veto --pieces 8`` from a
    container carrying the deploy environment. Junk values fall back to off.

    **MEASURED 2026-09-02 AND REJECTED on the bar as registered.** Live catalog, 45,173
    articles, full production stack: 182 edges vetoed (127 on dates, 23 week, 16 season, 14
    quarter, 2 episode), 2 merges vetoed; 16 clusters split, 2 merged; droppedOut 31 of 9,771
    (0.3%, inside the bar); largest 86 unchanged; independent signal 1/155 → 1/156; blindspot
    claims 242 → 240; **stories 2,410 → 2,405** — the fall the bar forbids. Both exhibits were
    out of the window (unobserved). The pieces read cuts both ways, and both halves are recorded:

    * What it separated was the class it was built for — lottery draws by date (Bonoloto 28 Aug
      from Chontico 30 Aug and Dorado 31 Aug), Q2 from Q3 earnings-call transcripts, one show's
      episode recap from another's — and the five dissolved stories were all daily series
      ("This Day in Rock History: August 28", "Tarot Card Reading Today, August 30", "Sismos en
      Perú del 27 de agosto", "Oklahoma Lottery Pick 3 for Aug. 27", "Walang Pasok: class
      suspensions for August 28"): rule-3 instances that were never events. The story-count fall
      IS those pseudo-stories dissolving.
    * It also **manufactured two welds downstream**, by the containment mechanism recorded on
      ``support_scope``: refusal is subtractive inside ``cluster()``, the freed singletons are
      then absorbed by whatever template edge remains. A Kerala Lottery result joined the
      Powerball numbers story (both are "winning numbers" boilerplate, and neither headline's
      slash date — "08/26/26", "29/08" — is parsed, so no anchor could refuse it), and two
      rattlesnake-advisory game reports joined a 16-article "players to watch" listicle. Three
      articles, but the shape the precedent rejects: a rule that fragments series and
      manufactures welds is not paying for its 0.3%.

    Held OFF, and per ``tests/test_env_hygiene.AUDIT_ONLY_CLUSTER_FLAGS`` no longer forwarded
    into the container: the flag survives as the audit's instrument. The one registered
    follow-up is parsing unambiguous slash dates (a component above 12 fixes the order) and
    re-measuring; it would refuse the Powerball/Kerala edge but does nothing for singleton
    absorption in general, so it is a hypothesis, not a fix."""
    v = os.environ.get("RWE_CLUSTER_ANCHOR_VETO", "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def time_decay() -> float:
    """Time decay inside the pairwise gate (``RWE_CLUSTER_TIME_DECAY``) — **candidate, 0.0 = OFF
    by default and byte-identical**. Stage 0 item 2 of ``docs/CLUSTERING_APPROACHES_RESEARCH.md``;
    the arithmetic is :func:`clustering.required_sim`.

    The value is the extra similarity a pair must reach PER DAY of publication gap, on top of
    ``DEFAULT_SIM``: at 0.02 a pair three hours apart is judged at 0.28 as today, a pair three
    days apart at 0.34 and a six-day pair at 0.40. The hard six-day window stays; this grades the
    requirement inside it. Coverage of one event is burst-shaped (the finding behind
    ``DEFAULT_MERGE_MAX_GAP_HOURS``), so two headlines far apart in time that still clear the
    floor on their template alone are the recurring-series shape — the daily price report, the
    weekly column — and a same-event pair days apart carries more than the template.

    Threaded to candidate admission, quorum cross-pair scoring and the repair re-cluster through
    the one ``pair_admits`` rule, so no pass judges a gap the others ignore. The merge pass keeps
    its own 48-hour cap unchanged — one variable per measurement. Missing timestamps fail open.

    Pre-registered bars: recurring-series chains separate in the ``--pieces`` read; the
    Fauci-class sagas do not fragment further than today; droppedOut ≤ 1%; no story-count fall.
    Measure with ``audit_clustering_change.py --time-decay 0.02 --pieces 8``. Junk and negative
    values fall back to 0.0.

    **MEASURED 2026-09-02 AT 0.02 AND REJECTED — do not titrate.** Live catalog, 45,244
    articles, full production stack: droppedOut **731 of 9,778 (7.5%)** against the 1% bar
    (and the harness's 5%); stories 2,412 → 2,288; 437 clusters split, 28 merged; blindspot
    claims 241 → 221. The pieces read is the failure the bar named, exactly: the sagas
    fragmented — the Lake Ontario renaming (73/50) into three, the Lindsay Clancy trial
    (65/26) into five, the Nepal flood (55/37) into four — and the freed pieces then welded
    downstream into same-actor-different-event blobs (three unrelated Dolly Parton stories into
    one, a Dearborn pastor story into a Texas polling-sites story, a Fernández transfer into a
    "how to watch" listing). The mechanism is structural rather than a matter of the constant:
    the coverage that legitimately spans days is precisely the coverage whose vocabulary
    diverges over those days, so demanding MORE lexical similarity with the gap is backwards
    for every saga and right only for series, and the series were already the smaller
    population. A lower decay moves the same trade along the same line. Held at 0.0 and, per
    ``tests/test_env_hygiene.AUDIT_ONLY_CLUSTER_FLAGS``, no longer forwarded into the
    container — the knob survives as the audit's instrument only."""
    return max(0.0, _env_float_allowing_zero("RWE_CLUSTER_TIME_DECAY", 0.0))


def _anchor_closure(arts: list, on: bool, stats: Optional[dict] = None) -> tuple:
    """``(evidence, merge_ok)`` for the instance-anchor veto — ``(None, None)`` when off, so the
    clusterer's fast path survives byte-identical.

    Anchors are computed once over EXACTLY the list handed to ``cluster`` (both callables receive
    indices into it; a repair pass builds its own closures over its own sublist). The pairwise
    rule refuses an edge whose sides disagree on a slot; the cluster rule refuses a merge whose
    sides' CORROBORATED anchors (``clustering.anchor_consensus``, ≥ 2 members per value) disagree —
    a singleton has no consensus and fails open, exactly like the geo and entity consensuses.
    ``stats`` counts per slot so the ``--pieces`` read can attribute a split to dates or to a
    series slot."""
    if not on:
        return None, None
    anchors = [clustering.instance_anchors(a.get("headline") or "") for a in arts]

    def bump(key: str) -> None:
        if stats is not None:
            stats[key] = stats.get(key, 0) + 1

    def evidence(x: int, y: int) -> bool:
        ax, ay = anchors[x], anchors[y]
        if not ax or not ay:
            return True                                 # fail-open: nothing to say
        slot = clustering.anchors_conflict(ax, ay)
        if slot is None:
            return True
        bump("anchorEdgeVetoed")
        bump("anchorEdgeVetoed:" + slot)
        return False

    def merge_ok(a: list, b: list) -> bool:
        ca = clustering.anchor_consensus([anchors[i] for i in a])
        cb = clustering.anchor_consensus([anchors[i] for i in b])
        if not ca or not cb or clustering.anchors_conflict(ca, cb) is None:
            return True
        bump("anchorMergeVetoed")
        return False
    return evidence, merge_ok


def _anchor_consensus_members(members: list) -> dict:
    """The corroborated anchors of a member list — the aggregate passes' form of the same test
    :func:`_anchor_closure` applies inside the build."""
    return clustering.anchor_consensus(
        [clustering.instance_anchors(m.get("headline") or "") for m in members])


def publisher_identity_enabled() -> bool:
    """Whether publisher counts collapse the name forms of one outlet. ON —
    ``RWE_STORY_PUBLISHER_IDENTITY=0`` counts raw strings again.

    Measured before this existed: 181 of 1,367 publisher names were duplicates of another, 60
    stories carried an inflated ``publisherCount`` (``Samsung Galaxy Z Fold 8`` showed 26
    publishers and has 8), and **35 stories cleared ``min_publishers`` only because one outlet was
    counted twice** — the largest being 17 articles from 17 ``*.iheart.com`` station hostnames
    syndicating identical copy. Those 35 stop being stories, which is correct: they never were."""
    v = os.environ.get("RWE_STORY_PUBLISHER_IDENTITY", "").strip().lower()
    return v not in {"0", "false", "no", "off"}


def stable_ids() -> bool:
    """Whether a story keeps the id it was last served under. ON — ``RWE_STORY_STABLE_IDS=0``
    reverts to deriving the id from the earliest member every build.

    Measured before this existed: **5.1% of surviving stories changed id per day**, 72 of 81 cases
    because the representative aged out of the rolling window and 9 because a backfilled article
    displaced it. Each one is a saved or shared link that stops resolving, and the rate is
    structural — every long-lived story eventually loses its oldest member."""
    v = os.environ.get("RWE_STORY_STABLE_IDS", "").strip().lower()
    return v not in {"0", "false", "no", "off"}


def merge_similarity() -> float:
    """Second-pass duplicate merge — **0.33 in production** (deploy compose default;
    ``RWE_STORY_MERGE_SIM`` overrides, and UNSET falls back to 0.0 = off).

    Targets the one defect axis nothing shipped so far touches: RECALL. Measured on the live
    catalog, 22 duplicated events across 45 stories hold 172 articles (4.3% of covered), and the
    mechanism is structural rather than a tuning miss — "Mass shooting reported at Seattle Center"
    and "…gunfire erupts near Seattle" share ONE token against a floor of three. No linkage rule
    reaches that; only richer text does.

    First shipped disabled, because a merge pass is precisely the operation that built the
    mega-cluster; the 2026-08-16 re-baseline shows it living inside its bars in production
    (largest cluster 64 against the ~120 ceiling, 4/73 bad). The guards are in
    ``_merge_duplicates``; the bar it must hold is measured, not argued:

        adopt   : largest cluster stays under ~120, mean actionable coherence does not fall,
                  bad-cluster count does not rise, and the merged pairs read correctly by hand
        reject  : any of those, or largest ÷ p90 rising above 20×

    Measure with ``audit_clustering_change.py --merge-sim 0.33 --pieces 5``."""
    v = os.environ.get("RWE_STORY_MERGE_SIM", "").strip()
    try:
        s = float(v)
    except (TypeError, ValueError):
        return 0.0
    return s if 0.0 <= s <= 1.0 else 0.0


def merge_max_gap_hours() -> float:
    return _env_float("RWE_STORY_MERGE_MAX_GAP", DEFAULT_MERGE_MAX_GAP_HOURS)


def merge_max_size() -> int:
    return _env_int("RWE_STORY_MERGE_MAX_SIZE", DEFAULT_MERGE_MAX_SIZE)


def exclude_aggregator() -> bool:
    """Whether a registry ``kind = aggregator`` source is kept out of clustering (default on).

    Google News is the case that shows why a RATING is not enough: MBFC gives it a Left-Center lean,
    derived from the sources it surfaces. The rating is real and voting it would still be wrong,
    because those sources are already in the cluster. Identity, not credibility, is what settles
    this one."""
    return os.environ.get("RWE_STORY_EXCLUDE_AGGREGATOR", "1").strip().lower() not in ("0", "false", "no")


def credibility_gate() -> bool:
    """Whether a registry ``credibility = low`` outlet is barred from voting its lean (default on).

    Off, every rated outlet votes — which is the behaviour from before the column existed, and is
    what the eight affected rows would get if the verdicts turn out to be wrong. One env var to
    reverse, and the leans stay in the file either way."""
    return os.environ.get("RWE_STORY_CREDIBILITY_GATE", "1").strip().lower() not in ("0", "false", "no")


def min_rated_for_blindspot() -> int:
    """Rated publishers a story needs before it may assert a coverage gap.
    ``RWE_STORY_MIN_RATED`` — 1 restores the pre-2026-07-28 behaviour of claiming on any sample."""
    return _env_int("RWE_STORY_MIN_RATED", MIN_RATED_FOR_BLINDSPOT)


def exclude_wire() -> bool:
    """Whether ``kind=wire`` outlets are kept out of story clustering. ON — set
    ``RWE_STORY_EXCLUDE_WIRE=0`` to disable.

    On by default because the mechanism does nothing on its own: an outlet is excluded only if a
    human wrote ``wire`` in its registry row, so the blast radius is exactly what someone curated
    and nothing else. Unknown outlets are never wire.

    Stories only. The articles stay in the catalog, on Discover, in search and on their publisher
    pages — they are real articles, and it is their newsworthiness that is in question, not their
    existence."""
    v = os.environ.get("RWE_STORY_EXCLUDE_WIRE", "").strip().lower()
    return v not in {"0", "false", "no", "off"}


def repair_quorum() -> float:
    """TARGETED cluster-aware linkage — **0.5 in production** (deploy compose default;
    ``RWE_STORY_REPAIR_QUORUM`` overrides, and UNSET falls back to 0.0 = off, so an environment
    without the deploy's variables silently loses the repair pass).

    Applies the quorum rule only to clusters ``_cluster_trust`` has already condemned, instead of
    to the whole catalog. Measured on the live catalog (16,857 articles), a GLOBAL quorum is the
    right idea aimed too broadly:

        quorum 0.3 : largest cluster 486 -> 45, story count 964 -> 1,081, mean coherence
                     0.967 -> 0.974 — and 599 articles dropped out of stories entirely
        quorum 0.5 : largest 486 -> 45, stories 964 -> 1,122, 677 dropped

    The mega-cluster split into 61 pieces, which is the intent. But Berlin pride — 77 articles from
    54 publishers at coherence 0.94, nothing whatever wrong with it — split into six, and a dozen
    other well-covered stories shed real coverage. Size cannot tell those apart; both are large.
    The independent signal can: 0.61 against 0.94.

    So this variant restricts the stricter rule to where that signal already objects. On the same
    catalog that is 3 clusters holding 380 articles (9.1% of covered), which bounds the worst case:
    every other story is byte-identical to production. Measure with
    ``audit_clustering_change.py --repair-quorum <q>`` before changing it anywhere."""
    v = os.environ.get("RWE_STORY_REPAIR_QUORUM", "").strip()
    try:
        q = float(v)
    except (TypeError, ValueError):
        return 0.0
    return q if 0.0 <= q <= 1.0 else 0.0


def coherence_floor() -> float:
    """geoCoherence below which a cluster is independently suspect. ``RWE_STORY_COHERENCE_FLOOR``."""
    return _env_float("RWE_STORY_COHERENCE_FLOOR", DEFAULT_COHERENCE_FLOOR)


def unverified_size() -> int:
    """Cluster size above which having NO coherence score is itself notable.
    ``RWE_STORY_UNVERIFIED_SIZE``."""
    return _env_int("RWE_STORY_UNVERIFIED_SIZE", DEFAULT_UNVERIFIED_SIZE)


def trust_ranking() -> bool:
    """Whether the default ranking demotes independently-suspect clusters. ON — set
    ``RWE_STORY_TRUST_RANKING=0`` to rank purely by size again."""
    v = os.environ.get("RWE_STORY_TRUST_RANKING", "").strip().lower()
    return v not in {"0", "false", "no", "off"}


def _size_rank(s: dict, *, trust_aware: bool) -> tuple:
    """The "biggest story" ordering key, descending.

    Size alone is not a safe ranking signal here, and the reason is mechanical rather than
    aesthetic: single-linkage chaining ACCUMULATES publishers, so a cluster's wrongness and its
    rank have the same cause. Ranking purely on ``publisherCount`` therefore promotes the one
    defect class we have evidence for — measured in production, a 106-publisher cluster at
    geoCoherence 0.62 sorts ahead of every correctly-clustered story in the catalog.

    So a cluster the independent signal actively contradicts sorts last. Only ``low`` is demoted,
    never ``unverified``: we reorder on evidence, and merely having nothing to check against is
    not evidence. The trade this accepts is real — the 208-article cluster does contain a genuine,
    well-covered story alongside the contamination, and burying it makes that coverage harder to
    reach from this surface (it stays on Discover, search and the publisher pages). Splitting the
    cluster is the actual fix; see ``link_quorum()``. This is containment until that measures out."""
    trusted = s.get("clusterTrust") != TRUST_LOW if trust_aware else True
    return (trusted, s["publisherCount"], s["totalCoverage"], s["latest"] or "")


def _admit(groups: list, arts: list, *, min_articles: int, min_publishers: int) -> list:
    """Cluster index groups → member lists that clear the admission gates."""
    out = []
    for idxs in groups:
        members = [arts[i] for i in idxs]
        if len(members) < min_articles:
            continue
        if len({_pub_key(m) for m in members}) < min_publishers:
            continue
        out.append(members)
    return out


def _repair(members: list, *, quorum: float, sim: float, window_days: float, min_shared: int,
            min_tokens: int, idf: bool, min_articles: int, min_publishers: int,
            support: int = clustering.DEFAULT_MIN_SUPPORT, s_scope: str = "any",
            desc: int = 0, veto: str = "", veto_stats: Optional[dict] = None,
            template: bool = False, lexicon: frozenset = TEMPLATE_TOKENS,
            hyphen: bool = False, uni: bool = False, ent_veto: bool = False,
            entities: "Optional[dict]" = None, event_verdicts: "Optional[dict]" = None,
            band_out: "Optional[dict]" = None, anchor: bool = False,
            decay: float = 0.0) -> Optional[list]:
    """Re-cluster ONE condemned cluster's members under a stricter linkage rule.

    Why targeted rather than global: measured on the live catalog, a global quorum splits the
    486-article mega-cluster into 61 pieces (largest cluster 486 → 45, exactly the intent) but also
    fragments stories nothing is wrong with — Berlin pride, 77 articles from 54 publishers at
    coherence 0.94, went to six pieces. Size cannot separate those two; they are both large. What
    separates them is the independent signal, 0.61 against 0.94. So the stricter rule is applied
    only where that signal already says the cluster is wrong, and every other story in the catalog
    is left byte-identical.

    Returns the replacement stories, or ``None`` to keep the original whole — when the split
    yields one piece (nothing was separated) or loses more than ``REPAIR_MIN_RETENTION`` of the
    articles (it destroyed the cluster rather than resolving it)."""
    # Closures are rebuilt over THIS member list — the veto callables receive indices, and the
    # indices of a condemned cluster's sublist are not the indices of the whole build. The
    # template gate composes here exactly as in the primary build (one rule, or the repair
    # re-splits on a disagreement rather than a defect — the article_tokens discipline).
    r_evidence, r_merge_ok = _geo_closures(members, veto, veto_stats)
    # Same discipline as the lexicon and the support breadth: the repair re-clusters under a
    # stricter quorum, so it must consult the SAME evidence channels or it re-splits on a
    # disagreement between the passes rather than on a defect in the cluster.
    _, r_ent_ok = _entity_closures(members, entities, ent_veto, veto_stats)
    r_merge_ok = _and_merge_ok(r_merge_ok, r_ent_ok)
    # Anchors and decay thread here for the same reason: a repair that ignored a rule the
    # primary build applied would re-split on the passes' disagreement, not on a defect.
    r_anc_ev, r_anc_ok = _anchor_closure(members, anchor, veto_stats)
    r_evidence = _and_evidence(r_anc_ev, r_evidence)
    r_merge_ok = _and_merge_ok(r_merge_ok, r_anc_ok)
    if template:
        r_evidence = _and_evidence(
            _template_closure(members, desc, veto_stats, lexicon=lexicon, hyphen=hyphen, uni=uni),
            r_evidence)
    if event_verdicts is not None:
        r_evidence = _and_evidence(
            _event_identity_closure(members, desc, hyphen, event_verdicts, event_band_hi(),
                                    veto_stats, band_out, uni=uni), r_evidence)
    pieces = _admit(
        clustering.cluster(members, tokens=lambda a: article_tokens(a, desc, hyphen, uni),
                           time=lambda a: clustering.parse_time(a["publishedAt"]),
                           sim=sim, window_days=window_days, min_shared=min_shared,
                           min_tokens=min_tokens, idf=idf, link_quorum=quorum,
                           min_support=support, support_scope=s_scope,
                           evidence=r_evidence, merge_ok=r_merge_ok, time_decay=decay),
        members, min_articles=min_articles, min_publishers=min_publishers)
    if len(pieces) < 2:
        return None
    if sum(len(p) for p in pieces) < REPAIR_MIN_RETENTION * len(members):
        return None
    return pieces


#: Share of a story's articles that must already carry a prior id before that id is inherited.
#:
#: A majority, so an id follows the cluster that kept most of the coverage. Below a half, two
#: clusters could each have a decent claim on the same id and which one won would depend on
#: ordering rather than on the data.
MIN_ID_CARRYOVER = 0.5


def reassign_ids(prior: dict, stories: list) -> dict:
    """``story index -> the id it should keep``, from ``url -> previously served id``.

    The fix for a measured 5.1%/day id churn. ``_story_id`` anchors to the earliest member, and the
    failure is that anchor LEAVING — the rolling window drops it, or a backfilled article displaces
    it. No member-derived anchor survives that, so ids are given back rather than recomputed: a
    cluster that still holds most of some previous story's articles IS that story, whatever its
    earliest member is now.

    Best-first with two exclusivity rules, which is what makes splits and merges behave:

    * one story may claim only one prior id — so a MERGE keeps the id of its larger contributor
      and the smaller contributor's id retires, rather than both surviving on one story.
    * one prior id may go to only one story — so a SPLIT gives the id to the piece holding most of
      the original coverage, and the other pieces are new stories, which is what they are.

    Deterministic in ``(prior, stories)``: ties break on overlap, then article count, then index."""
    claims = []
    for i, s in enumerate(stories):
        urls = [c["url"] for c in s["coverage"]]
        if not urls:
            continue
        votes: dict = {}
        for u in urls:
            pid = prior.get(u)
            if pid:
                votes[pid] = votes.get(pid, 0) + 1
        for pid, n in votes.items():
            claims.append((n / len(urls), n, i, pid))
    claims.sort(key=lambda c: (-c[0], -c[1], c[2], c[3]))
    taken_story: set = set()
    taken_id: set = set()
    out: dict = {}
    for share, _n, i, pid in claims:
        if share < MIN_ID_CARRYOVER or i in taken_story or pid in taken_id:
            continue
        taken_story.add(i)
        taken_id.add(pid)
        out[i] = pid
    return out


def stabilize_ids_readonly(store_, stories: list) -> list:
    """Give each story back the id it was last served under — reading the ledger, never writing it.

    The read half of :func:`stabilize_ids`, split out for the FILTERED builds. A filtered view sees
    a subset of each cluster, so it must never write the identity map (partial clusters would claim
    ids the next unfiltered build takes back — churn caused by the fix for churn). But not READING
    the map either meant filtered lists rendered raw ``_story_id`` output while ``get_story``
    searches only the stabilized default view — so every cluster whose anchor had ever churned was
    a dead link from every topic-filtered surface. Measured in production (2026-08-02): 110 of
    1,257 rendered topic-filtered links (8.8%) returned 404 on click; one 93-member cluster's urls
    voted 93/93 for a ledger id that served 200 while its rendered raw id served 404.

    Fails soft like its writing sibling: an unreadable identity table keeps derived ids."""
    if not stories:
        return stories
    try:
        prior = store_.story_member_ids()
    except Exception:
        return stories
    for i, pid in reassign_ids(prior, stories).items():
        stories[i] = dict(stories[i], id=pid)
    return stories


def stabilize_ids(store_, stories: list) -> list:
    """Give each story back the id it was last served under, then record the new mapping.

    Deliberately NOT part of ``build_stories``. That function is pure — same rows in, same stories,
    ids and order out — and the whole test suite plus every audit depends on it staying that way.
    Identity is a property of what was PUBLISHED before, not of the input rows, so it is applied
    where the product is served and nowhere else.

    Fails soft: if the identity table cannot be read or written, stories keep their derived ids.
    A churned id is a broken link; a 500 is a broken page."""
    stories = stabilize_ids_readonly(store_, stories)
    if not stories:
        return stories
    try:
        store_.replace_story_members({c["url"]: s["id"] for s in stories for c in s["coverage"]})
    except Exception:
        pass
    return stories


def story_tags_enabled() -> bool:
    """Whether a served story carries its topics/tags. ON — ``RWE_STORY_TAGS=0`` turns it off.

    On by default because a story without them is not wrong, it is silent: the Similar News Topics
    rail simply has nothing to show, and every consumer of the projection (tag retrieval, the
    ``tag`` filter, its facets) degrades to empty rather than to something misleading. The switch
    exists so an operator can take the cost — one batched entity query and a wholesale table
    rewrite per build — out of the path without a deploy."""
    return os.environ.get("RWE_STORY_TAGS", "").strip().lower() not in {"0", "false", "no", "off"}


#: Entity provenances a TAG may be built from: every kind the table holds, span rows included and
#: unconditionally — which is a deliberate departure from :func:`entity_kinds`, so the reason is
#: recorded here rather than inferred.
#:
#: ``entity_kinds`` gates spans behind :func:`entity_spans` because they feed CLUSTERING, where a
#: weaker provenance can move an article between stories, change a story's id, or weld two events
#: together. None of that is reachable from here: tags are computed after the build is finished and
#: after identity, read by presentation and retrieval, and cannot alter a single membership. What
#: they would lose to that gate is most of their evidence — the provider covers 17% of the window
#: against the span reader's 65% — so a deployment with the clustering switch off would get tags on
#: one story in six and no signal that the rest are simply unextracted.
TAG_ENTITY_KINDS = ("person", "org", "span")

#: Direct tags two stories must share before their relation is even scored for INHERITANCE. A
#: blocking rule, not a quality bar: an all-pairs similarity over the window is quadratic (2,852
#: production stories is four million pairs of set intersections, on every build), and two stories
#: sharing no corroborated name are not going to clear the relation threshold anyway. Two rather
#: than one for ``_merge_by_entities``' reason — one shared name can be a type-level responder
#: agency, two independent ones is the signature of a shared subject.
TAG_RELATION_MIN_SHARED = 2

#: Relation strength a pair needs before any tag crosses it. Deliberately ABOVE the Similar
#: Stories rail's own cut: showing a reader a card is a suggestion they can dismiss, and copying a
#: tag asserts that a story is ABOUT something. The stricter bar is what keeps a tag from
#: spreading outward through a chain of merely-plausible relations.
TAG_RELATION_MIN_SCORE = 0.30


def _tag_relations(stories: list, direct: dict) -> dict:
    """``story id -> [(related id, strength)]`` for tag inheritance only.

    Scored with the SAME measure the Similar Stories rail uses — IDF-weighted Jaccard over each
    story's whole profile — so "strongly related" means one thing in this codebase and not two.
    What differs is the candidate set and the bar: candidates are blocked on
    :data:`TAG_RELATION_MIN_SHARED` shared direct tags, and the bar is
    :data:`TAG_RELATION_MIN_SCORE` rather than the rail's relative cut, because a tag is an
    assertion where a card is a suggestion."""
    by_tag: dict = {}
    for sid, tags in direct.items():
        for tag in tags:
            if tag["source"] == story_tags.SOURCE_DIRECT:
                by_tag.setdefault(tag["name"], []).append(sid)

    shared: dict = {}
    for sids in by_tag.values():
        # A name in a great many stories proposes nothing: it would make every pair a candidate
        # and hand the quadratic cost straight back. `extract_tags` has already dropped the
        # window's background names, so this only catches the next tier down.
        if len(sids) > 40:
            continue
        for i in range(len(sids)):
            for j in range(i + 1, len(sids)):
                key = (sids[i], sids[j]) if sids[i] < sids[j] else (sids[j], sids[i])
                shared[key] = shared.get(key, 0) + 1

    candidates = [k for k, n in shared.items() if n >= TAG_RELATION_MIN_SHARED]
    if not candidates:
        return {}
    by_id = {s["id"]: s for s in stories}
    profiles = {sid: _similar_profile(by_id[sid]) for sid in by_id}
    weights = clustering.idf_weights(list(profiles.values()))
    totals = {sid: sum(weights.get(t, 1.0) for t in p) for sid, p in profiles.items()}

    out: dict = {}
    for a, b in candidates:
        pa, pb = profiles.get(a), profiles.get(b)
        if pa is None or pb is None:
            continue
        inter = pa & pb
        w = sum(weights.get(t, 1.0) for t in inter)
        den = totals[a] + totals[b] - w
        score = (w / den) if den else 0.0
        if score < TAG_RELATION_MIN_SCORE:
            continue
        out.setdefault(a, []).append((b, score))
        out.setdefault(b, []).append((a, score))
    return out


def attach_tags(store_, stories: list) -> list:
    """Attach each story's ranked topics/tags, and record the story -> tag map.

    Applied HERE rather than inside ``build_stories`` for that function's stated reason: it is
    pure, the whole suite depends on it staying so, and a tag is a property of the SERVED window
    (its story frequencies, its relations) rather than of the input rows.

    Ordered after :func:`stabilize_ids` on purpose — tags are keyed on the id a reader will see,
    so computing them before identity would file them under an id nothing links to.

    Fails soft in the two ways it can. Without ``article_entities`` (or with the query failing) a
    story keeps only its category tag, which is the honest degradation: no evidence, no claims.
    Without a writable table it still SERVES tags and simply does not persist them — the table is
    the durable copy for callers outside a build, never the source of truth."""
    if not stories or not story_tags_enabled():
        return stories
    entities = {}
    try:
        urls = [c.get("url") for s in stories for c in (s.get("coverage") or [])]
        entities = store_.entities_for_urls(urls, kinds=TAG_ENTITY_KINDS) or {}
    except Exception:
        entities = {}
    direct = story_tags.extract_tags(stories, entities, noise=tag_noise)
    tags = story_tags.inherit_tags(stories, direct, _tag_relations(stories, direct))
    try:
        store_.replace_story_tags(tags)
    except Exception:
        pass
    return [dict(s, tags=tags.get(s["id"], [])) for s in stories]


def attach_tags_readonly(store_, stories: list) -> list:
    """Tags for a FILTERED view, read from the table the unfiltered build wrote.

    The identity split, for the identity reason. A filtered build sees a subset of the window, and
    both halves of the ranking are properties of the whole: story frequency decides what counts as
    background and how specific a name is. Recomputing over the subset would give the same story
    different tags on the Technology page than on the front page — so a filtered view reports what
    the full build concluded, or nothing."""
    if not stories or not story_tags_enabled():
        return stories
    try:
        stored = store_.story_tags([s["id"] for s in stories])
    except Exception:
        return stories
    return [dict(s, tags=stored.get(s["id"], [])) for s in stories]


def _profile(members: list) -> frozenset:
    """A cluster's vocabulary: every member's headline AND description as one token set.

    Deliberately not the headline alone — that is the input the clusterer already failed on, so a
    merge scored the same way could only re-derive the same answer. Measured: the four Seattle
    clusters score 0.15 on headlines and 0.56 on profiles."""
    toks: set = set()
    for m in members:
        toks |= clustering.title_tokens(m.get("headline") or "")
        toks |= clustering.title_tokens(m.get("description") or "")
    return frozenset(toks)


def _span(members: list) -> tuple:
    times = [clustering.parse_time(m.get("publishedAt")) for m in members]
    times = [t for t in times if t is not None]
    return (min(times), max(times)) if times else (None, None)


def _gap_hours(a: list, b: list) -> float:
    """Hours between two clusters' coverage windows; 0 when they overlap."""
    (ae, al), (be, bl) = _span(a), _span(b)
    if not (ae and al and be and bl):
        return 0.0
    if ae <= bl and be <= al:
        return 0.0
    delta = (be - al) if be > al else (ae - bl)
    return abs(delta.total_seconds()) / 3600.0


def _merge_duplicates(groups: list, *, min_sim: float, max_gap_hours: float, max_size: int,
                      veto: str = "", veto_stats: Optional[dict] = None,
                      ent_veto: bool = False,
                      entities: "Optional[dict]" = None, anchor: bool = False) -> list:
    """Join clusters that are the same event described in different words.

    The recall failure the repair exposed: "Mass shooting reported at Seattle Center" and "…gunfire
    erupts near Seattle" share ONE token against ``MIN_SHARED_TOKENS = 3`` and score 0.08 against
    ``sim = 0.28``. They can never merge pairwise, at any threshold, so the clusterer cannot reach
    this and a second pass over richer text is the only route.

    A merge pass is exactly the operation that built the mega-cluster, so it carries three guards:

    * **Complete linkage.** A group merges only when EVERY pair inside it clears ``min_sim`` — not
      just some chain of them. This is the direct fix for a case the audit found: a "Houthi attacks
      in the Red Sea: what to know" explainer paired with two SEPARATE Houthi events at 0.30 and
      0.27, and single linkage would have glued those two events together through it.
    * **Coherence must not degrade.** If the merged cluster carries an actionable geoCoherence
      score below the floor, the merge is refused. The independent signal gets a veto over a
      text-similarity decision.
    * **A size cap**, so no merge can start a runaway.

    Best-first and deterministic. Returns the new member lists."""
    n = len(groups)
    if n < 2 or min_sim <= 0.0:
        return groups
    profiles = [_profile(g) for g in groups]
    weights = clustering.idf_weights(profiles)

    # Candidate generation blocks on shared tokens, but skips tokens carried by more than half the
    # clusters: they are this corpus's stop-words, they carry the minimum IDF weight, and they
    # dominate the cost. Two clusters sharing only such tokens cannot reach min_sim anyway.
    postings: dict = {}
    for i, toks in enumerate(profiles):
        for t in toks:
            postings.setdefault(t, []).append(i)
    common = max(2, n // 2)
    # Each profile's total weight, precomputed. weighted_jaccard would otherwise re-sum the UNION
    # on every pair, and the union is the expensive half; with totals in hand only the intersection
    # needs summing, since |A ∪ B| = total[i] + total[j] - |A ∩ B|. Same arithmetic, measured 4x
    # faster over a 1,000-cluster catalog — which is what makes this affordable in a request path
    # rather than only in an audit.
    total = [sum(weights.get(t, 1.0) for t in p) for p in profiles]

    def score(i: int, j: int) -> float:
        inter = profiles[i] & profiles[j]
        if not inter:
            return 0.0
        w = sum(weights.get(t, 1.0) for t in inter)
        den = total[i] + total[j] - w
        return (w / den) if den else 0.0

    # SIZE BOUND — an O(1) test that rejects a candidate before its intersection is ever computed.
    #
    # ``score`` is ``w / (Ti + Tj - w)`` and is increasing in ``w``, so ``score >= s`` requires
    # ``w >= s(Ti + Tj) / (1 + s)``. The intersection is a subset of both profiles, so
    # ``w <= min(Ti, Tj)``. Therefore when ``min(Ti, Tj) * (1 + s) < s * (Ti + Tj)`` the pair CANNOT
    # reach ``s`` whatever its intersection turns out to be — at ``min_sim = 0.33`` that rules out
    # every pair whose profile weights differ by more than ~3x.
    #
    # EXACT, not a heuristic: it only ever skips pairs that were going to score below the threshold,
    # so the surviving set is identical. Measured in production: candidate generation was 84.7% of
    # this stage, making 247,718 ``score`` calls — each a frozenset intersection plus a weight sum
    # over it — to keep SEVENTEEN pairs. The bound replaces most of those with two multiplications.
    bound = 1.0 + min_sim
    pairs = []
    for i in range(n):
        seen: set = set()
        for t in profiles[i]:
            if len(postings[t]) > common:
                continue
            for j in postings[t]:
                if j > i:
                    seen.add(j)
        ti = total[i]
        for j in seen:
            tj = total[j]
            if (ti if ti < tj else tj) * bound < min_sim * (ti + tj):
                continue                              # cannot reach min_sim — skip the intersection
            s = score(i, j)
            if s >= min_sim and _gap_hours(groups[i], groups[j]) <= max_gap_hours:
                pairs.append((s, i, j))
    if not pairs:
        return groups

    member_of = {i: (i,) for i in range(n)}          # index -> the group tuple it belongs to
    for _, i, j in sorted(pairs, key=lambda p: (-p[0], p[1], p[2])):
        gi, gj = member_of[i], member_of[j]
        if gi == gj:
            continue
        if sum(len(groups[x]) for x in gi + gj) > max_size:
            continue
        if not all(score(a, b) >= min_sim for a in gi for b in gj):
            continue                                  # complete linkage, never a chain
        merged_members = [m for x in sorted(gi + gj) for m in groups[x]]
        coherence, located = _geo_coherence(merged_members, _country_votes(merged_members))
        if (coherence is not None and located >= MIN_LOCATED_FOR_TRUST
                and coherence < coherence_floor()):
            continue                                  # the independent signal vetoes the merge
        if veto:
            # X4: the same corroborated-disagreement rule the growth veto applies at build time.
            # Needed HERE because one-sided corroboration can leave the pooled located set at 3 —
            # under MIN_LOCATED_FOR_TRUST, where the coherence guard above is silent — and a
            # profile-similar pair the veto severed (two same-vocabulary earthquakes) would be
            # quietly rejoined through that crack.
            ca, ta = _located_consensus([m for x in gi for m in groups[x]])
            cb, tb = _located_consensus([m for x in gj for m in groups[x]])
            if (ca and cb and not (ca & cb)
                    and (ta >= GEO_MIN_CONSENSUS or tb >= GEO_MIN_CONSENSUS)):
                if veto_stats is not None:
                    veto_stats["dupMergeVetoed"] = veto_stats.get("dupMergeVetoed", 0) + 1
                continue
        if ent_veto and entities:
            # X5c at the AGGREGATE stage. The geo block above is the same rule over the other
            # channel, and it is silent for any story family without located consensus — which is
            # most of entertainment, business and sport. Profile similarity is a text signal
            # scored over a token UNION, so one member's vocabulary can carry a whole cluster's
            # profile; this is the independent second opinion on that decision.
            ea = _story_entity_consensus([m for x in gi for m in groups[x]], entities)
            eb = _story_entity_consensus([m for x in gj for m in groups[x]], entities)
            if ea and eb and not (ea & eb):
                if veto_stats is not None:
                    veto_stats["dupMergeEntityVetoed"] = (
                        veto_stats.get("dupMergeEntityVetoed", 0) + 1)
                continue
        if anchor:
            # Instance anchors at the AGGREGATE stage. Two instances of one template ("Wordle
            # hints for Sept 2" / "…Sept 3") that the edge-level veto kept apart have
            # near-identical IDF profiles, so this pass is exactly where they would be rejoined
            # — the containment failure `support_scope` records. Same corroborated-consensus
            # test as the build-time hook; a side without consensus fails open.
            ca = _anchor_consensus_members([m for x in gi for m in groups[x]])
            cb = _anchor_consensus_members([m for x in gj for m in groups[x]])
            if ca and cb and clustering.anchors_conflict(ca, cb) is not None:
                if veto_stats is not None:
                    veto_stats["dupMergeAnchorVetoed"] = (
                        veto_stats.get("dupMergeAnchorVetoed", 0) + 1)
                continue
        combined = tuple(sorted(gi + gj))
        for x in combined:
            member_of[x] = combined

    out, done = [], set()
    for i in range(n):
        key = member_of[i]
        if key in done:
            continue
        done.add(key)
        out.append([m for x in key for m in groups[x]])
    return out


#: A name present in MORE story consensuses than this cannot help propose an entity merge.
#: Six, because the floor must clear the largest GENUINE duplicate family ever measured
#: (Farage/Clacton, 5 stories, X5b run 1) while sitting far under the ubiquitous political
#: names that rebuilt a 130-article blob in the same run ("donald trump" spans dozens of
#: consensuses — see the rule-v2 comment in ``_merge_by_entities``).
ENTITY_MERGE_MAX_STORY_DF = 6


def _story_entity_consensus(members: list, entities: dict) -> frozenset:
    """A story's corroborated entity consensus: non-noise names carried by >= 2 members, one
    vote per member per name. The same corroboration discipline as ``GEO_MIN_CONSENSUS`` — one
    member's testimony is a sample of one — and the X5 phase-0 receipt for why it is safe:
    93.1% of covered members share their own story's consensus."""
    votes: dict = {}
    for m in members:
        ents = entities.get(m.get("id") or m.get("url")) or {}
        # Every kind the FETCH returned counts: the store hands back provider kinds by default
        # and the rule-extracted spans only when `entity_kinds()` asked for them, so which
        # provenances take part is decided once, at the query, never here.
        seen = {name for names in ents.values() for name in names
                if name and not entity_noise(name)}
        for name in seen:
            votes[name] = votes.get(name, 0) + 1
    return frozenset(n for n, c in votes.items() if c >= 2)


def _merge_by_entities(groups: list, *, entities: dict, min_names: int,
                       max_gap_hours: float, max_size: int,
                       stats: Optional[dict] = None, anchor: bool = False) -> list:
    """Join stories that are the same event according to corroborated ENTITY consensus (X5b).

    The recall population this exists for is measured, not assumed: 65% of confusable story
    pairs share corroborated names because they ARE the same family (Farage/Clacton x3,
    Mangione's court stories, the cross-language Air Force One pair) — the duplicate problem
    ``_merge_duplicates`` targets, reached through evidence its lexical profiles cannot see
    ("Mass shooting reported at Seattle Center" vs "gunfire erupts near Seattle" share ONE
    token; they share every entity).

    A merge pass is precisely the operation that built the mega-cluster, so this one carries
    every guard the lexical pass taught us, plus X4's:

    * **>= ``min_names`` shared corroborated names to PROPOSE** — one shared name can be a
      type-level responder agency (the USGS receipt), so a single name proposes nothing.
    * **Complete linkage** over constituent stories: a group joins only when EVERY pair of
      original stories inside it clears ``min_names`` — never a chain.
    * **X4's geo-consensus veto**, unconditional here: disjoint corroborated located
      consensuses refuse the join (the Colombia↔Indonesia protection), whatever the entities
      say.
    * **The coherence guard**: a merged cluster whose actionable geoCoherence falls below the
      floor is refused — the independent signal keeps its veto over an entity decision exactly
      as it holds one over a text decision.
    * **The size cap and the gap window**, same constants as the lexical pass.

    Best-first (most shared names, ties by index) and deterministic. Returns new member lists."""
    n = len(groups)
    if n < 2 or min_names <= 0 or not entities:
        return groups

    def bump(key: str, by: int = 1) -> None:
        if stats is not None:
            stats[key] = stats.get(key, 0) + by

    def profile(members: list) -> dict:
        """name -> member votes, the counted form of ``_story_entity_consensus`` (same
        corroboration floor) — kept because rule v3 needs to know which name is a story's TOP,
        not merely which names are corroborated."""
        votes: dict = {}
        for m in members:
            ents = entities.get(m.get("id") or m.get("url")) or {}
            seen = {name for names in ents.values() for name in names
                    if name and not entity_noise(name)}
            for name in seen:
                votes[name] = votes.get(name, 0) + 1
        return {n: c for n, c in votes.items() if c >= 2}

    profiles = [profile(g) for g in groups]
    cons = [frozenset(p) for p in profiles]
    postings: dict = {}
    for i, names in enumerate(cons):
        for name in names:
            postings.setdefault(name, []).append(i)
    # UBIQUITY at the altitude that matters (rule v2 — the first production run's receipt). A
    # name's ability to propose a join is its ability to DISCRIMINATE between stories, and that
    # is its STORY-consensus df, not its article df: "luigi mangione" sits in ~4 consensuses and
    # every one is genuinely him (join them); "donald trump" sits in dozens of consensuses that
    # are dozens of different events. Run 1 (2026-08-16) rebuilt a 130-article blob from ELEVEN
    # stories through {donald trump, white house}-class pairs — complete linkage held, because
    # every pair really does share those names. This is the USGS lesson generalized: type-level
    # attendance is not evidence, and the political equivalent of a responder agency is the
    # president. The floor sits just above the largest GENUINE duplicate family that run
    # measured (Farage/Clacton, 5 stories), computed from THIS build's own consensuses — no
    # external state, deterministic, self-calibrating as the catalog grows.
    discriminative = frozenset(name for name, sids in postings.items()
                               if len(sids) <= ENTITY_MERGE_MAX_STORY_DF)
    bump("entityMergeUbiquitous", by=len(postings) - len(discriminative))
    cons = [c & discriminative for c in cons]
    # Rule v3 — MUTUAL ANCHORING, from run 2's hand-read (2026-08-16). The automated bars passed
    # and the exhibits split 7 clean / 5 dubious, with one structure separating them exactly: in
    # every true family the shared names include each side's TOP consensus entity ("mangione" is
    # #1 on all four sides, "jackie" on both), while in every dubious join the shared names are
    # peripheral on at least one side — Leavitt's resignation joined the visa purge on
    # {rubio, state department}-class names, and "karoline leavitt", the resignation story's
    # top, appears nowhere in the visa story. So a join must be anchored by BOTH tops (ties
    # kept): what each story is chiefly ABOUT must be part of what the pair shares.
    tops = []
    for i in range(n):
        kept = {name: profiles[i][name] for name in cons[i]}
        peak = max(kept.values()) if kept else 0
        tops.append(frozenset(name for name, c in kept.items() if c == peak))

    def anchored(a: int, b: int, shared: frozenset) -> bool:
        return bool(tops[a] & shared) and bool(tops[b] & shared)

    pairs = []
    for i in range(n):
        counts: dict = {}
        for name in cons[i]:
            for j in postings[name]:
                if j > i:
                    counts[j] = counts.get(j, 0) + 1
        for j, shared_n in sorted(counts.items()):
            if shared_n < min_names:
                continue
            shared = cons[i] & cons[j]
            if not anchored(i, j, shared):
                bump("entityMergeUnanchored")
                continue
            pairs.append((shared_n, i, j))
            bump("entityMergeCandidates")
    if not pairs:
        return groups

    member_of = {i: (i,) for i in range(n)}
    for shared, i, j in sorted(pairs, key=lambda t: (-t[0], t[1], t[2])):
        gi, gj = member_of[i], member_of[j]
        if gi == gj:
            continue
        if sum(len(groups[x]) for x in gi + gj) > max_size:
            bump("entityMergeSizeCapped")
            continue
        if _gap_hours(groups[i], groups[j]) > max_gap_hours:
            bump("entityMergeGapBlocked")
            continue
        if not all(len(cons[a] & cons[b]) >= min_names
                   and anchored(a, b, cons[a] & cons[b])
                   for a in gi for b in gj):
            continue                                  # complete linkage AND anchoring, never a chain
        side_a = [m for x in gi for m in groups[x]]
        side_b = [m for x in gj for m in groups[x]]
        ca, ta = _located_consensus(side_a)
        cb, tb = _located_consensus(side_b)
        if (ca and cb and not (ca & cb)
                and (ta >= GEO_MIN_CONSENSUS or tb >= GEO_MIN_CONSENSUS)):
            bump("entityMergeGeoVetoed")
            continue                                  # X4's rule: geography outranks entities
        if anchor:
            # Instance anchors outrank entities the way geography does: two instances of a
            # series share every entity (the puzzle's maker, the league) and differ only in
            # the slot the tokenizer dropped.
            aa, ab = _anchor_consensus_members(side_a), _anchor_consensus_members(side_b)
            if aa and ab and clustering.anchors_conflict(aa, ab) is not None:
                bump("entityMergeAnchorVetoed")
                continue
        merged_members = side_a + side_b
        coherence, located = _geo_coherence(merged_members, _country_votes(merged_members))
        if (coherence is not None and located >= MIN_LOCATED_FOR_TRUST
                and coherence < coherence_floor()):
            bump("entityMergeCoherenceVetoed")
            continue                                  # the independent signal keeps its veto
        combined = tuple(sorted(gi + gj))
        for x in combined:
            member_of[x] = combined
        bump("entityMergeJoined")

    out, done = [], set()
    for i in range(n):
        key = member_of[i]
        if key in done:
            continue
        done.add(key)
        out.append([m for x in key for m in groups[x]])
    return out


def build_stories(rows: list, *, min_articles: int = 2, min_publishers: int = 2,
                  sim: float = clustering.DEFAULT_SIM,
                  window_days: float = clustering.DEFAULT_WINDOW_DAYS,
                  min_shared: Optional[int] = None,
                  min_tokens: Optional[int] = None,
                  idf: Optional[bool] = None,
                  quorum: Optional[float] = None,
                  support: Optional[int] = None,
                  s_scope: "Optional[str]" = None,
                  repair: Optional[float] = None,
                  merge: Optional[float] = None,
                  merge_gap: Optional[float] = None,
                  desc: Optional[int] = None,
                  veto: Optional[str] = None,
                  veto_stats: Optional[dict] = None,
                  entity_merge: Optional[int] = None,
                  ent_veto: Optional[bool] = None,
                  entities: Optional[dict] = None,
                  template: Optional[bool] = None,
                  lexicons: "Optional[tuple[str, ...]]" = None,
                  hyphen: Optional[bool] = None,
                  uni: Optional[bool] = None,
                  derived: Optional[bool] = None,
                  derived_df: Optional[int] = None,
                  derived_days: Optional[int] = None,
                  event_verdicts: "Optional[dict]" = None,
                  band_out: "Optional[dict]" = None,
                  anchor: Optional[bool] = None,
                  decay: Optional[float] = None) -> list:
    """Cluster FeedArticle rows into Story objects (the pure builder). Keeps clusters with
    ≥ ``min_articles`` from ≥ ``min_publishers`` distinct outlets; sorted biggest+freshest first,
    with independently-suspect clusters demoted (see ``_size_rank``).
    Deterministic: same rows → same stories, ids, and order.

    Three passes, and the order is deliberate. Clustering forms groups from headline tokens;
    ``_repair`` splits the ones the independent signal condemns; ``_merge_duplicates`` joins the
    ones that are the same event in different words. Split runs before join so the two constrain
    each other — anything the repair over-separates that is genuinely near-identical gets rejoined,
    and anything the merge would over-join has already been vetted by coherence."""
    arts = [discover.feed_article_to_article(r) for r in rows]
    if exclude_wire():
        # Machine-generated market-data copy never enters clustering, so it can neither form a
        # story nor join one. This is the ONE defect class no clustering signal can catch: a
        # template repeated 115 times really is about one template, so geoCoherence rates it
        # perfectly coherent. Filtering by curated source identity is explicit and reversible;
        # the threshold that was proposed for the same job measured 0% precision, 0% recall.
        #
        # BOTH strings are asked, because they disagree. `publisher` is the canonical registry name
        # resolved at INGEST, so an article ingested before its feed was curated keeps the name the
        # registry gave it then: 499 of 671 obituary articles are stored as `The Oregonian` /
        # `The Express-Times` with an `obits.*` URL, and curating the feed removed 172 articles and
        # zero stories because those 14 clusters are built entirely from the masthead-labelled half.
        # The URL still carries the feed's own host, so it answers the question the stale name
        # cannot. Strictly narrowing — see `is_wire_url`, which resolves the HOST to keep the
        # resolve memo effective.
        #
        # The aggregator gate below is deliberately NOT given the same treatment: no host/name
        # mismatch has been measured there, and this file adds gates on evidence, not symmetry.
        arts = [a for a in arts
                if not (outlet_registry.is_wire(a.get("publisher"))
                        or outlet_registry.is_wire_url(a.get("url")))]
    if exclude_aggregator():
        # An aggregator's articles ARE other outlets' articles, republished with a reference link.
        # Counting one as a publisher double-counts coverage the cluster already holds, inflates
        # publisherCount, and lifts the story up a ranking that sorts on it. Measured: Zazoom alone
        # contributed 815 articles to a six-day window.
        #
        # A separate switch from the wire gate on purpose. They exclude for different reasons —
        # a wire has no editorial stance, an aggregator has someone else's — and an operator who
        # wants one back should not have to take the other with it.
        arts = [a for a in arts if not outlet_registry.is_aggregator(a.get("publisher"))]
    if credibility_gate():
        # Resolved HERE, from the registry, rather than read from the article's stored `scored`
        # JSON — so tightening or reversing a credibility verdict takes effect on the next build
        # instead of waiting for a backfill. The lean itself is stored at ingest and does need one;
        # this deliberately does not.
        for a in arts:
            a["lowCredibility"] = outlet_registry.is_low_credibility(a.get("publisher"))
    if publisher_identity_enabled():
        # Resolved once over the WHOLE build: whether a bare name may join a domain depends on how
        # many domains carry that label, which is a property of the catalog and invisible to a
        # per-article call.
        keys = publisher_identity.groups({a["publisher"] for a in arts})
        for a in arts:
            a["publisherKey"] = keys.get(a["publisher"], a["publisher"])
    cap = desc_tokens() if desc is None else desc
    # The dek changes what "3 shared tokens" MEANS, so the floor moves with it rather than being
    # inherited — see `desc_min_shared`. An explicit --min-shared still wins, so the audit can
    # titrate one variable at a time.
    default_shared = desc_min_shared() if cap > 0 else min_shared_tokens()
    shared = default_shared if min_shared is None else min_shared
    tokens_floor = min_title_tokens() if min_tokens is None else min_tokens
    weighting = use_idf() if idf is None else idf
    # X4 entity-evidence veto (docs/STORY_ENTITY_EVIDENCE_PLAN.md) — resolves like every other
    # knob: None = whatever production is configured with, which today is off. The closures are
    # built over the post-exclusion `arts`, the exact list the clusterer indexes into.
    veto_mode = geo_veto() if veto is None else (veto if veto in _GEO_VETO_MODES else "")
    g_evidence, g_merge_ok = _geo_closures(arts, veto_mode, veto_stats)
    # X5c: the SECOND independent evidence source, spent in the veto direction. Composed onto the
    # same cluster-level hook as the geo gate, so a merge must satisfy both channels; each fails
    # open on its own absence, so a domain with neither (no geography, no extracted entities)
    # behaves exactly as it does today.
    ent_on = entity_veto() if ent_veto is None else bool(ent_veto)
    _, e_merge_ok = _entity_closures(arts, entities, ent_on, veto_stats)
    g_merge_ok = _and_merge_ok(g_merge_ok, e_merge_ok)
    # Instance anchors (Stage 0.1, `anchor_veto`): the number the tokenizer drops, read back as a
    # slot->value fact and spent on BOTH hooks — the edge (admission, quorum cross-pairs, repair)
    # and the cluster merge — and again inside the aggregate passes below. Fail-open on absence
    # like every channel before it; None resolves to production, which is off.
    anc_on = anchor_veto() if anchor is None else bool(anchor)
    a_evidence, a_merge_ok = _anchor_closure(arts, anc_on, veto_stats)
    g_evidence = _and_evidence(a_evidence, g_evidence)
    g_merge_ok = _and_merge_ok(g_merge_ok, a_merge_ok)
    # Time decay (Stage 0.2, `time_decay`): resolved once, threaded to the clusterer and the
    # repair re-cluster so both judge a gap by one rule. 0.0 is byte-identical.
    dec = time_decay() if decay is None else max(0.0, float(decay))
    # The sole-template-evidence rule (Phase B; template_gate) — an edge must share >= 1
    # non-template token. Composed through the SAME evidence hook as the geo veto, so admission,
    # quorum scoring and repair consult one rule; None/off is byte-identical by construction.
    t_gate = template_gate() if template is None else bool(template)
    # Candidate knobs, resolved like every other: None = whatever production is configured with.
    lex_union = _lexicon_union(template_lexicons() if lexicons is None else tuple(lexicons))
    hyph = hyphen_compounds() if hyphen is None else bool(hyphen)
    # Resolved ONCE for the build, never per article: reading the environment inside a per-row
    # loop is the cost `corpus.tier_resolver` exists to document.
    uni_on = unicode_words() if uni is None else (uni if uni == "fallback" else bool(uni))
    # The corpus-derived boilerplate set joins the SAME gate as the manual lexicons — computed
    # once over the full build's articles and threaded to the repair re-cluster unchanged, so
    # both passes judge edges against one vocabulary.
    der_on = derived_boilerplate_on() if derived is None else bool(derived)
    if der_on:
        der = derived_boilerplate(
            arts, cap, hyph, uni_on,
            min_df=boilerplate_df() if derived_df is None else int(derived_df),
            min_days=boilerplate_days() if derived_days is None else int(derived_days))
        if veto_stats is not None:
            manual = _lexicon_union(tuple(TEMPLATE_LEXICONS))
            veto_stats["derivedBoilerplate"] = len(der)
            veto_stats["derivedManualOverlap"] = len(der & manual)
        lex_union = lex_union | der
    use_gate = t_gate or der_on
    if use_gate:
        g_evidence = _and_evidence(
            _template_closure(arts, cap, veto_stats, lexicon=lex_union, hyphen=hyph, uni=uni_on),
            g_evidence)
    # The banded semantic judge (event_identity): verdicts are an INPUT — the build never calls a
    # network — and ``event_verdicts is None`` means the judge is off for this build, byte-identical
    # to production. Composed through the same evidence hook as every gate, so admission, quorum
    # cross-pair scoring and the repair re-cluster consult one rule.
    if event_verdicts is not None:
        g_evidence = _and_evidence(
            _event_identity_closure(arts, cap, hyph, event_verdicts, event_band_hi(),
                                    veto_stats, band_out, uni=uni_on), g_evidence)
    # Support breadth is resolved once and threaded to BOTH passes for the same reason the
    # evidence lexicon is: the repair re-clusters under a stricter quorum, and if it linked on a
    # different corroboration rule than the primary build it would re-split on the disagreement.
    prop = min_support() if support is None else max(1, int(support))
    scope = support_scope() if s_scope is None else (s_scope if s_scope in _SUPPORT_SCOPES
                                                     else "any")
    groups = clustering.cluster(
        arts, tokens=lambda a: article_tokens(a, cap, hyph, uni_on),
        time=lambda a: clustering.parse_time(a["publishedAt"]), sim=sim, window_days=window_days,
        min_shared=shared, min_tokens=tokens_floor, idf=weighting,
        link_quorum=link_quorum() if quorum is None else quorum, min_support=prop,
        support_scope=scope, evidence=g_evidence, merge_ok=g_merge_ok, time_decay=dec)
    mend = repair_quorum() if repair is None else repair
    admitted = []
    for members in _admit(groups, arts, min_articles=min_articles, min_publishers=min_publishers):
        if mend > 0.0 and _build_story(members)["clusterTrust"] == TRUST_LOW:
            pieces = _repair(members, quorum=mend, support=prop, s_scope=scope, sim=sim, window_days=window_days,
                             min_shared=shared, min_tokens=tokens_floor, idf=weighting,
                             min_articles=min_articles, min_publishers=min_publishers, desc=cap,
                             veto=veto_mode, veto_stats=veto_stats, template=use_gate,
                             lexicon=lex_union, hyphen=hyph, uni=uni_on,
                             ent_veto=ent_on, entities=entities,
                             event_verdicts=event_verdicts, band_out=band_out,
                             anchor=anc_on, decay=dec)
            if pieces is not None:
                admitted.extend(pieces)
                continue
        admitted.append(members)

    join = merge_similarity() if merge is None else merge
    if join > 0.0:
        admitted = _merge_duplicates(
            admitted, min_sim=join,
            max_gap_hours=merge_max_gap_hours() if merge_gap is None else merge_gap,
            max_size=merge_max_size(), veto=veto_mode, veto_stats=veto_stats,
            ent_veto=ent_on, entities=entities, anchor=anc_on)
    # X5b entity-corroborated merge recall — dormant twice over: the env default is 0 AND the
    # entity mapping must be injected by the caller (the audit does; _fetch never queries it,
    # so a production build costs nothing whatever the env says).
    em = entity_merge_min() if entity_merge is None else max(0, entity_merge)
    if em > 0 and entities:
        admitted = _merge_by_entities(
            admitted, entities=entities, min_names=em,
            max_gap_hours=merge_max_gap_hours() if merge_gap is None else merge_gap,
            max_size=merge_max_size(), stats=veto_stats, anchor=anc_on)
    # Story-hero guard (docs/STORY_HERO_IMAGES.md) — presentation only, resolved AFTER membership
    # is final because the reuse index is a property of the whole build: an image fronting more
    # than HERO_MAX_CLUSTER_REUSE distinct clusters is by definition about none of them. Measured
    # 2026-08-16: every such asset was a placeholder/logo/og-fallback, and the first real photo
    # (The Hill's AP file art, legitimately shared across a story family) sits at exactly 3
    # stories — which the threshold keeps. Env-resolved here (not a parameter) because nothing
    # needs to titrate it: it cannot change membership, and audit_story_hero.py measures it by
    # comparing heroes, not builds.
    hero_ranked = hero_guard()
    hero_rejected = None
    if hero_ranked:
        reuse: dict = {}
        for mems in admitted:
            for key in {media.image_identity(m.get("image")) for m in mems}:
                if key:
                    reuse[key] = reuse.get(key, 0) + 1
        hero_rejected = frozenset(k for k, n in reuse.items()
                                  if n > media.HERO_MAX_CLUSTER_REUSE)
    stories = [_build_story(m, hero_ranked=hero_ranked, hero_rejected=hero_rejected)
               for m in admitted]
    trust_aware = trust_ranking()
    stories.sort(key=lambda s: _size_rank(s, trust_aware=trust_aware), reverse=True)
    return stories


# --------------------------------------------------------------------------- #
# Store-backed orchestration — the surface Discover + Stories consume.
# --------------------------------------------------------------------------- #
def _env_float(name: str, default: float) -> float:
    """A positive float from the environment, else the default. Junk never widens or narrows the
    window silently — it falls back."""
    try:
        v = float(os.environ.get(name, "").strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, "").strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def scan_days() -> float:
    """How many days back the clustering candidate set reaches. Defaults to the clustering window
    itself — a threshold that pairs articles up to ``window_days`` apart is meaningless if the
    candidate set spans less than that."""
    return _env_float("RWE_STORIES_SCAN_DAYS", clustering.DEFAULT_WINDOW_DAYS)


def max_scan_default() -> int:
    """Backstop on candidate-set SIZE. This is a memory guard, NOT the relevance rule — the window
    above decides what is in scope.

    It was described here as sitting "far above a normal window so it only ever engages if ingestion
    volume spikes far beyond projections". Two corrections, both from `docs/SCALE_ROADMAP.md`:

    * 60,000 covers 12.9 days at today's ~4,650 articles/day, **9.6 hours at 150k/day and 2.9 hours
      at 500k/day** — so "far above" is a statement about the current ingestion rate, not about the
      cap;
    * it sits BELOW ``corpus.tier_a_budget()`` (83,000, the size at which the build stops fitting
      its poll cycle), so the "backstop" is in fact the binding constraint, not the safety net.

    Engaging it is no longer silent. ``corpus.select`` reports the window actually achieved against
    the one asked for — see its docstring for why a truncation here reads as a clustering
    regression rather than as a bound being hit."""
    return _env_int("RWE_STORIES_MAX_SCAN", 60000)


def _window_start(now=None) -> str:
    from datetime import datetime, timedelta, timezone
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=scan_days())).isoformat()


def _fetch(store_, *, topic=None, date_from=None, date_to=None, max_scan=None,
           report_out=None) -> list:
    """The clustering candidate set: a TIME-bounded, pre-filtered article slice (topic/date narrow
    it in SQL first). Each row is annotated with its EVENT countries (one batched side-table
    lookup) so story construction can locate members by best-known location.

    The bound is a **time window**, not a row count. It used to be ``max_scan=2000`` rows ordered
    newest-first, which made story yield a function of ingestion RATE: every provider added shrank
    the hours those 2000 rows covered, so integrating more sources produced FEWER stories (measured:
    a 12.5-hour effective window against a 6-day clustering threshold, 89 stories from a
    12,790-article catalog). A caller-supplied ``date_from`` still wins — an explicit request for a
    date range is never silently narrowed.

    **The result is a SELECTED corpus, not merely a fetched one.** ``corpus.select`` applies the
    Tier A boundary and reports what bound — the row cap truncating the window, or Tier A exceeding
    the size at which the build fits its poll cycle. Both were silent before; the ``total`` this
    function used to discard as ``_total`` is what makes the first one detectable, and it was
    always right here. See `docs/SCALE_ROADMAP.md` (M1) and ``examples/corpus.py``.

    The boundary is applied in TWO layers and the order is the point (M2):

    1. ``corpus.sql_exclusions()`` goes into the query, so an excluded row never consumes the row
       cap. Without it the cap fills with Tier B and Tier A gets whatever is left — at 50,000
       sources, where Tier B is most of the corpus, that truncates the clustering window to a
       sliver while the tier filter reports that it removed them.
    2. ``corpus.select`` is the contract, catching what SQL cannot express (an alias the registry
       learned after ingest, a Tier B host that appears only in the URL). Those are the residue;
       the report counts them.

    Tier selection also runs BEFORE the event-countries lookup, so an excluded row costs no
    side-table read. With no tier configured — the shipped state — the exclusion set is empty (no
    SQL term at all) and ``select`` returns the list it was handed, so this function is
    byte-identical to what it was.

    ``report_out`` is the optional sink for that selection report (what bound, the window actually
    achieved, headroom). It exists so a caller can have the numbers WITHOUT running the query twice:
    the window start is recomputed per call and the catalog is written continuously, so two fetches
    a few seconds apart legitimately disagree — the first production run of
    ``audit_corpus_boundary.py`` reported 27,809 rows in one section and 27,808 in the next for
    exactly that reason."""
    if date_from is None:
        date_from = _window_start()
    cap = max_scan or max_scan_default()
    rows, total = store_.search_feed_articles(
        topic=topic, date_from=date_from, date_to=date_to, sort="newest",
        pagination=OffsetPagination.from_params(cap, 0, max_limit=cap),
        exclude_publishers=corpus.sql_exclusions())
    rows = corpus.select(rows, total=total, cap=cap, window_start=date_from,
                         report_out=report_out)
    events = store_.event_countries_for_urls([r.get("canonicalUrl") for r in rows])
    for r in rows:
        r["eventCountries"] = events.get(r.get("canonicalUrl"), [])
    return rows


# --------------------------------------------------------------------------- #
# Clustered-result cache.
#
# Clustering is the expensive step and its input only changes when the poller ingests (every
# RWE_POLL_INTERVAL, default 600 s), so recomputing it per request is pure waste. Filters, sort and
# pagination stay OUTSIDE the cache — they are cheap list operations over the cached clusters, so
# every filter combination is served from one cached build.
# --------------------------------------------------------------------------- #
# Keyed by the STORE OBJECT itself, weakly. Identity cannot collide (an ``id()`` in the key would:
# CPython reuses addresses after collection, so a dead store's clusters could be served to a new one
# allocated at the same address), and a store's cache is collected with the store.
_CACHE: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 16

#: One build lock per cache key, so concurrent readers of the SAME cold key wait for one build
#: instead of each running their own. Keyed by the cache key (which carries the catalog
#: fingerprint), not by the store, so a rebuild of one filtered view never blocks another.
_BUILD_LOCKS: dict = {}


def cache_ttl() -> float:
    """Seconds a clustered build stays servable. 0 disables the cache entirely.

    600, not 120, and the reason is measured. A cold build is quadratic in catalog size — 0.4 s of
    clustering at 5k articles, 1.6 s at 10k, 7.4 s at 20k, 32 s at 40k (``examples/perf_profile.py``)
    — while a warm hit is 0.65 ms. At 20k that is a **15,600x** difference between the two paths,
    so what matters for felt speed is not the build's cost but how often a READER pays it.

    Two mechanisms already keep the cache honest, and neither one needs the TTL:

    * the catalog **fingerprint** is part of the key, so any write invalidates immediately, and
    * the poller calls :func:`warm_cache` right after it ingests, so the rebuild the write forces
      is paid on the poller's thread rather than a reader's request.

    That left the TTL doing one job — bounding drift of the rolling ``date_from`` window — and one
    unintended one: expiring a still-correct build every 120 s. With ``RWE_POLL_INTERVAL`` at 600 s
    that is FOUR extra reader-visible rebuilds per poll cycle, each of them the full cold cost, for
    no correctness gain. Matching the poll interval removes those four and keeps the one that a
    genuine catalog change requires.

    The drift this trades away is immaterial: ``date_from`` is ``now - 6 days``, and a window start
    up to 10 minutes stale instead of 2 changes which articles are in scope by well under a percent
    of the window. Set ``RWE_STORIES_CACHE_TTL`` to restore any other value; 0 disables caching."""
    try:
        v = float(os.environ.get("RWE_STORIES_CACHE_TTL", "").strip())
        return v if v >= 0 else 600.0
    except (TypeError, ValueError):
        return 600.0


def clear_cache() -> None:
    """Drop every cached build (tests, and any caller that has just mutated the catalog)."""
    with _CACHE_LOCK:
        _CACHE.clear()
        # The build locks go too. Leaving them would leak one Lock per key across a test suite that
        # clears between cases, and a lock whose entry is gone protects nothing.
        _BUILD_LOCKS.clear()
        # And the pending-refresh set: a test that cleared the cache must not have its first stale
        # serve silently coalesced into a refresh a PREVIOUS test left in flight.
        _REFRESH_PENDING.clear()
        # The Similar Stories profile memo is derived from a build, so it belongs to the build's
        # lifetime. Its key already carries a per-story fingerprint, so this is belt-and-braces
        # rather than a correctness fix — but a memo that outlives every cache it was computed
        # from is the kind of thing that is only ever wrong once, in production.
        _SIMILAR_PROFILES.clear()


def build_subprocess_enabled() -> bool:
    """Whether an eligible story build runs in the dedicated build subprocess.
    ON — ``RWE_STORY_BUILD_SUBPROCESS=0`` runs every build on the calling thread, as before.

    **Why a subprocess and not a thread (P0-2′):** the build is pure-Python clustering, and a
    thread running it holds the GIL against every request handler in the process. Measured in
    production (2026-08-01): during the post-cycle warm, ``/api/health`` — a no-op — inflated from
    2–3 ms to 55 ms (~20×), and the warm itself ran 11,476 ms; the serving process spent that whole
    window degrading every endpoint at once. A subprocess has its own GIL, so the second core does
    the clustering while the first keeps serving. The worker is persistent (spawn cost and the
    ``api_server`` import are paid once, not per build) and single (`max_workers=1` — two
    concurrent builds on a 2-core box would starve the OS scheduler the same way the GIL was).

    **Eligibility is strict, and silently falling back is the design.** The child opens its OWN
    store on the database URL, so the database must be a real file: an in-memory store
    (``sqlite://``) is invisible to another process, and a child building from it would return an
    EMPTY story list that looks exactly like a quiet catalog. File-backed SQLite in WAL mode is
    built for concurrent readers in separate processes — the child only ever reads.

    Identity (``stabilize_ids``) stays in the PARENT, deliberately: it writes the id map, and
    keeping the child read-only means a crashed or killed worker can never leave a half-written
    row behind — the failure mode is always "the build happened on the caller's thread instead"."""
    v = os.environ.get("RWE_STORY_BUILD_SUBPROCESS", "").strip().lower()
    return v not in {"0", "false", "no", "off"}


def _subprocess_eligible(store_) -> bool:
    """Only a file-backed store can be re-opened by another process — see the enable switch."""
    if not build_subprocess_enabled():
        return False
    url = str(getattr(store_, "url", "") or "")
    return url.startswith("sqlite:///") and ":memory:" not in url


def _event_inputs(store_) -> "tuple[dict | None, dict | None]":
    """``(event_verdicts, band_out)`` for a store-backed build — ``(None, None)`` when the judge
    is off, which makes the build byte-identical to production by construction. Fail-open: any
    store trouble reads as "judge off" rather than as a failed build."""
    import event_identity
    if not event_identity.judge_on():
        return None, None
    try:
        return store_.event_verdicts(), {}
    except Exception:                                # noqa: BLE001 — the judge must never fail a build
        return None, None


def _event_flush(store_, band_out: "dict | None") -> None:
    """Queue the band pairs a build emitted (best-effort; the next build re-emits on failure)."""
    if not band_out:
        return
    try:
        store_.enqueue_event_pairs(list(band_out.values()))
    except Exception:                                # noqa: BLE001 — same fail-open posture
        pass


#: The provider-extracted entity kinds — what every build has consumed since X5b.
ENTITY_KINDS_PROVIDER = ("person", "org")


def entity_spans() -> bool:
    """Whether the build CONSUMES the rule-extracted ``span`` entity rows
    (``RWE_STORY_ENTITY_SPANS``) — **candidate, OFF by default**. Stage 0.3 of
    ``docs/CLUSTERING_APPROACHES_RESEARCH.md``; the extractor is ``entity_spans.extract``.

    The problem is coverage, not rule design. X5c is silent on 93.8% of the merges it is
    consulted about because only 24% of articles carry a provider-extracted name (GDELT's GKG
    sees only the articles GDELT monitors), and X5b can only propose a join between two stories
    that BOTH cleared extraction. Every adopted entity rule already treats a name as a heuristic
    — corroborated by >= 2 members, mutually anchored, story-df-floored, identity-denoised — so a
    weaker but far broader extractor (capitalised multi-word spans from the headline and dek,
    stdlib, no dependency) can be measured against the same bars through the same rules.

    Two switches on purpose. ``RWE_INGEST_ENTITY_SPANS`` (``entity_spans.enabled``) decides
    whether rows are WRITTEN; this one decides whether a build READS them — so the table can be
    filled (ingest + ``entity_span_backfill.py``) and the counterfactual measured against a
    baseline that does not consume it. With this off, ``_entities_for`` fetches the provider
    kinds only and the build is byte-identical whatever the table holds.

    Pre-registered bars: entity coverage of the window rises from 24% toward 70%+ on English
    (the backfill prints it); on ``audit_clustering_change.py --entity-spans --pieces 8``, X5c's
    consulted-with-consensus share rises materially above 6.2%, droppedOut ≤ 1%, no rise in bad
    clusters, largest cluster within noise, the recorded exhibits unmoved; X5b's joins are read
    by hand under the merge bars (a span-driven join must be a duplicate family, never a
    same-name weld). Junk values fall back to off.

    **MEASURED 2026-09-02: every registered bar met; adoption held for one extractor fix and a
    re-run.** Backfill over 45,306 window articles: provider-covered 7,733 (17.1% — the window
    has grown past the 24% measurement with crawl sources GDELT does not see), with spans 29,483
    (65.1%), English 15,393 of 23,240 (66.2%). Audit, 45,294 articles, full production stack:
    X5c consulted with consensus on both sides **3,033 of 13,180 merges (23.0%) against 1,090 of
    12,440 (8.8%) on the same day's baseline**, vetoes 160 → 1,249; droppedOut 63 of 9,790
    (0.6%); bad clusters 1 → 1; largest 86 → 79; blindspot claims 241 → 241; the one in-window
    exhibit unmoved; stories 2,416 → 2,414 with 45 merges and 68 splits (a merge-direction change
    as much as a split one, so the −2 is the merges' arithmetic, not the cliff). The joins read
    as duplicate families every time: the Lake Ontario order and its reaction pieces, King
    Harald's death and the succession coverage, Messi's retirement in English AND Vietnamese, the
    Tupac trial in English AND Dutch, the Iran strikes — the cross-language recall X5b was
    designed for and could not reach at 17% coverage. No same-name weld; the ubiquity floor
    held (largest cluster fell).

    What held adoption: the backfill's sample showed two precision defects in the extractor —
    comma-separated cast lists glued into one "name" ("julia stiles jenna dewan harry shum jr")
    and calendar words forming spans ("tuesday sept"), the second of which is exactly the kind
    of pseudo-name that could corroborate ACROSS unrelated stories. Both were fixed in
    ``entity_spans`` (a comma ends a run; calendar and slot words are trimmed from a name's
    ends), and the discipline is to adopt what was measured, so the run was repeated.

    **MEASURED AGAIN 2026-09-02 AFTER THE FIX, AND ADOPTED — ON in production**
    (``deploy/docker-compose.yml`` defaults both switches to 1; ``0`` is the kill switch).
    Backfill: 26,196 window articles with spans (57.8%, 53,319 names — 1,800 fewer than before
    the fix, the noise it removed), coverage with spans 65.2%, English 64.9%. Audit, 45,453
    articles: X5c consulted with consensus **3,066 of 13,241 merges (23.2%)**, vetoes 1,194;
    droppedOut 57 of 9,801 (0.6%); **stories 2,421 → 2,422** (no fall this time); largest
    86 → 79; independent signal **1/156 bad → 0/159 bad** (mean 0.974 → 0.976); blindspot claims
    239 → 240; the one in-window exhibit unmoved; the harness's own line read ADOPT. The joins
    repeated the first run's duplicate families, the cross-language ones included.

    One join is recorded as contestable rather than clean, so it is not rediscovered as a
    surprise: a two-publisher Dutch "LIVE Deadline Day" live-blog family (18 articles) joined
    the Enzo Fernández-to-Manchester City story (14/9). A transfer live blog genuinely covers
    that move among a dozen others — the ROUND-UP BRIDGE class (rubric rule 1), which no
    structural rule reaches and the banded judge exists for. The spans moved that bridge from
    its own template family onto one of its subjects; they did not create it. Everything else
    in both pieces reads was a duplicate family or a correct separation (a Senate-primary spat
    out of a climate-campaign analysis, a broadcaster's joke out of the Lake Ontario order).

    **Measuring it after adoption.** ``--entity-spans`` only widens the AFTER side; the
    baseline now consumes spans too, so a bare run compares the rule against itself. Turn the
    baseline off for that container instead::

        dc run --rm -T -e RWE_STORY_ENTITY_SPANS=0 api python \\
            examples/audit_clustering_change.py --entity-spans --pieces 8"""
    v = os.environ.get("RWE_STORY_ENTITY_SPANS", "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def entity_kinds() -> tuple:
    """Which entity kinds a build fetches: the provider kinds, plus ``span`` under
    :func:`entity_spans`. Resolved once per build and handed to the store, so the choice of
    provenance is made at the query and nowhere else."""
    return ENTITY_KINDS_PROVIDER + (("span",) if entity_spans() else ())


def _entities_for(store_, rows: list) -> "dict | None":
    """The entity mapping for a build — fetched only when a pass that CONSUMES it is enabled,
    one batched side-table query per build, ``None`` (free) when both are off. Every serving-path
    call site goes through here so none can silently diverge from what the audit measured.

    Two consumers now, in opposite directions: X5b (``entity_merge_min``) proposes merges from
    entity corroboration, X5c (``entity_veto``) refuses them on entity disagreement. Either one
    alone is reason enough to pay for the query; neither on means the build never touches the
    side table."""
    if entity_merge_min() <= 0 and not entity_veto():
        return None
    return store_.entities_for_urls([r.get("canonicalUrl") for r in rows], kinds=entity_kinds())


def _subprocess_build(db_url: str, topic, date_from, date_to, max_scan,
                      min_articles: int, min_publishers: int) -> list:
    """The child's whole job: open the database, fetch the slice, cluster it, return plain dicts.

    Top-level (spawn must import it by name), read-only (identity is the parent's), and it opens a
    fresh store per call so a long-lived worker never accumulates connection state against a
    database the parent may be migrating."""
    import store as _store_mod
    st = _store_mod.Store(db_url)
    try:
        rows = _fetch(st, topic=topic, date_from=date_from, date_to=date_to, max_scan=max_scan)
        ev, band = _event_inputs(st)     # the child reads/queues symmetrically with the parent
        stories = build_stories(rows, min_articles=min_articles, min_publishers=min_publishers,
                                entities=_entities_for(st, rows),
                                event_verdicts=ev, band_out=band)
        _event_flush(st, band)
        return stories
    finally:
        try:
            st.engine.dispose()
        except Exception:
            pass


_BUILD_POOL: "ProcessPoolExecutor | None" = None
_BUILD_POOL_LOCK = threading.Lock()


def _build_pool() -> ProcessPoolExecutor:
    """The persistent single-worker pool, created on first use.

    ``forkserver``, never plain ``fork``: this process runs a dozen threads (pollers, warmer, push
    delivery), and forking a threaded process copies locks in whatever state some other thread held
    them — a child that inherits a held lock deadlocks in ways that reproduce roughly never.
    Forkserver's workers fork from a clean single-threaded helper instead, and the helper preloads
    this module (below) so forks start with the import graph already warm.

    One prepare-step behaviour is shared by every non-fork start method and is accepted here rather
    than fought: **each worker re-initialises the parent's ``__main__``** (as ``__mp_main__`` —
    measured with a pid-printing probe, then confirmed in ``multiprocessing/spawn.py``'s
    ``get_preparation_data``, which sends the main module for both spawn and forkserver). Guarded
    entry points make this a non-event: the production launch (``python examples/api_fastapi.py``)
    re-imports the app module once per worker without starting anything, and console scripts like
    pytest's carry the ``__name__`` guard. A guard-less script that reaches this pool would run its
    own body inside the worker — one more reason :func:`_offloaded_build` treats ANY worker failure
    as "build inline instead" rather than as an error worth surfacing to a caller."""
    global _BUILD_POOL
    with _BUILD_POOL_LOCK:
        if _BUILD_POOL is None:
            ctx = multiprocessing.get_context("forkserver")
            # Preload THIS module, not the default `__main__`. The default re-executes whatever
            # script started the process inside the helper — measured here: a guard-less probe ran
            # its entire body twice, and in production the helper would import the whole FastAPI
            # app it never uses. Naming the real dependency instead means the helper pays this
            # module's import graph once, and every forked worker starts warm.
            ctx.set_forkserver_preload(["story_service"])
            _BUILD_POOL = ProcessPoolExecutor(max_workers=1, mp_context=ctx)
        return _BUILD_POOL


def shutdown_build_pool() -> None:
    """Stop the worker (lifespan shutdown, and test teardown). Idempotent; the next eligible build
    simply creates a fresh pool."""
    global _BUILD_POOL
    with _BUILD_POOL_LOCK:
        pool, _BUILD_POOL = _BUILD_POOL, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


def _offloaded_build(store_, *, topic, date_from, date_to, max_scan,
                     min_articles, min_publishers) -> "list | None":
    """Run the build in the subprocess, or return ``None`` to say "build inline instead".

    ``None`` on ANY failure — a broken pool (worker OOM-killed), a spawn refusal, or the build
    itself raising in the child. The caller's thread then does the work exactly as it always did:
    offloading is a scheduling optimisation, and no scheduling optimisation is allowed to become
    the reason a build fails. A broken pool is torn down so the next call starts a fresh one."""
    try:
        future = _build_pool().submit(
            _subprocess_build, str(store_.url), topic, date_from, date_to, max_scan,
            min_articles, min_publishers)
        stories = future.result()
        obs_metrics.incr("story_build_subprocess_total")
        return stories
    except Exception:
        obs_metrics.incr("story_build_subprocess_failed_total")
        shutdown_build_pool()
        return None


def serve_stale() -> bool:
    """Whether a stale-but-inside-TTL build is served while one background rebuild replaces it.
    ON — ``RWE_STORIES_SERVE_STALE=0`` restores the reader-paid rebuild.

    Measured before this existed (production, 2026-08-01): the rebuild was **11,476 ms** at a
    47k-article catalog, the web tier abandons every engine call at **6,000 ms**, and the cache was
    invalidated by every ingest — so several times an hour, readers of the highest-traffic pages
    drew a guaranteed timeout and the app rendered "We couldn't load this. Please try again." The
    11.5 s did not need to be faster; it needed to not be in front of a reader.

    The trade is ~12 s of extra staleness on content already declared tolerant of 600 s of drift
    (:func:`cache_ttl`). The kill switch exists because that trade is a product judgment, and
    reverting it must not require a deploy."""
    v = os.environ.get("RWE_STORIES_SERVE_STALE", "").strip().lower()
    return v not in {"0", "false", "no", "off"}


#: Logical keys with a background rebuild in flight — the single-flight guard for stale refreshes.
#: Guarded by ``_CACHE_LOCK``. Without it, every reader who lands on the same stale entry during
#: the ~12 s rebuild would spawn another identical rebuild — recreating, as background load, the
#: exact convoy of duplicate builds the build-lock exists to prevent inline.
_REFRESH_PENDING: set = set()


def _request_stale_refresh(store_, logical) -> bool:
    """Queue exactly one background rebuild for this logical key. Non-blocking. Returns whether a
    refresh was spawned (``False`` = coalesced into one already in flight, which is the common case
    for every stale hit after the first)."""
    with _CACHE_LOCK:
        if logical in _REFRESH_PENDING:
            obs_metrics.incr("story_stale_refresh_coalesced_total")
            return False
        _REFRESH_PENDING.add(logical)
    obs_metrics.incr("story_stale_refresh_spawned_total")
    _spawn_refresh(store_, logical)
    return True


def _spawn_refresh(store_, logical) -> None:
    """Start :func:`_run_refresh` on a daemon thread. Separated from the request so tests can run
    the refresh inline (monkeypatching this) and assert on the state it leaves behind."""
    threading.Thread(target=_run_refresh, args=(store_, logical),
                     name="story-stale-refresh", daemon=True).start()


def _run_refresh(store_, logical) -> None:
    """The background rebuild's body: build fresh for one logical key, then release the key.

    ``allow_stale=False`` or this would serve the stale entry to itself and re-queue forever.
    A build that raises is swallowed: the stale entry keeps serving inside its TTL, and the next
    stale hit requests again — a failed refresh degrades to the previous behaviour minus the
    reader-visible cost, never to a wedged key (the ``finally`` releases it on every path)."""
    topic, date_from, date_to, max_scan, min_articles, min_publishers = logical
    try:
        _cached_build(store_, topic=topic, date_from=date_from, date_to=date_to, max_scan=max_scan,
                      min_articles=min_articles, min_publishers=min_publishers, allow_stale=False)
    except Exception:
        pass
    finally:
        with _CACHE_LOCK:
            _REFRESH_PENDING.discard(logical)


_WARM_LOCK = threading.Lock()


def warm_cache(store_) -> Optional[int]:
    """Build and cache the default (unfiltered) view; returns the story count, or ``None`` if
    another warm was already in flight and this one stood down.

    Called by the poller right after it ingests, on the poller's own thread. Without this the FIRST
    reader after every poll pays the whole clustering cost — measured at 5.4 s in production, once
    per poll cycle, which on low traffic is a large share of requests. The rebuild is unavoidable
    (the catalog genuinely changed); paying it on the thread that caused the change, rather than on
    a reader's request, is the whole point.

    **Single-flight.** ``MultiSourcePoller`` runs one thread PER ADAPTER — eight of them can finish
    a cycle at once, and without this guard that is eight concurrent multi-second clustering runs on
    a small instance. A skipped warm is not a lost one: the winner's build covers the same catalog,
    and the next adapter to finish warms again.

    Warms the exact key ``/api/stories`` uses with no filters — filters, sort and pagination are
    applied outside the cache, so this one build serves every filter combination too."""
    if not _WARM_LOCK.acquire(blocking=False):
        return None
    try:
        # allow_stale=False: the warm's one purpose is a FRESH build. Left at the default it would
        # find the entry it is here to replace, serve it to itself, and queue a background refresh
        # — a warm that never warms.
        return len(_cached_build(store_, topic=None, date_from=None, date_to=None, max_scan=None,
                                 min_articles=2, min_publishers=2, allow_stale=False))
    finally:
        _WARM_LOCK.release()


# --------------------------------------------------------------------------- #
# Coalesced warming.
#
# WHY THIS IS SAFE, stated first because everything else depends on it: the cached entry carries
# the catalog fingerprint, so the lookup always KNOWS whether it is fresh. Historically the
# fingerprint was part of the key and a mismatch meant the reader rebuilt; since serve-stale
# (`serve_stale`, measured rationale on that function) a mismatch inside the TTL is served as-is
# while one background rebuild replaces it. Either way, a warm can never make a reader see data the
# policy forbids — it decides who PAYS for a build and how soon staleness ends, never whether the
# staleness bound holds. That keeps deferring, coalescing or skipping a warm a pure scheduling
# question. (Serve a fingerprint the lookup cannot check, or an entry past its TTL, and every word
# of this stops being true.)
#
# WHAT IT FIXES. `MultiSourcePoller` runs one thread per adapter and holds a global lock across
# `poll_once` + `_post_cycle`, so the adapters' warms are SERIALIZED, never concurrent — which
# means `warm_cache`'s single-flight guard, written for the concurrent case, has never once fired
# for them. Each provider that ingests anything triggers its own full rebuild: measured in
# production, `story_cache_warm` at 5.6 s, several times per polling window, on a 2-core box.
# Every one of those rebuilds but the last is superseded before a reader can use it.
#
# It also unblocks ingestion. The warm ran INSIDE the poller's lock, so a 5.6 s rebuild stalled
# every other adapter's ingest behind it. Requesting instead of warming moves the build to this
# thread and the lock is released immediately. No new concurrency class is introduced: API requests
# already read the store while adapters write, and SQLite is in WAL mode for exactly that reason.
# --------------------------------------------------------------------------- #
def _env_float_allowing_zero(name: str, default: float) -> float:
    """Like ``_env_float`` but ZERO IS A VALUE, not junk.

    ``_env_float`` returns the default for anything <= 0, which is right for a window that must be
    positive to mean anything. It is wrong for a kill switch: setting the flag to 0 to disable
    coalescing silently gave back the default and left it enabled. Caught by the kill-switch test,
    which is the reason to write one."""
    try:
        v = float(os.environ.get(name, "").strip())
        return v if v >= 0 else default
    except (TypeError, ValueError):
        return default


def warm_coalesce_window() -> float:
    """Seconds of quiet after the last write before a coalesced warm fires.

    **DEFAULT 0 — coalescing is OFF, and that is the measured conclusion, not an oversight.**

    This module was built to batch "redundant" provider warms. The hypothesis was that eight
    providers each invalidating the catalog fingerprint produce eight rebuilds where one would do.
    Two independent measurements rejected it:

    * **Production is not bunched.** The live logs put ``story_cache_warm`` events ~60 s apart —
      four docker healthchecks at 15 s separate them. No quiet window short enough to be safe
      merges anything, so there is no burst to coalesce in steady state.
    * **Delaying the warm costs more than it saves.** Benchmarked over a six-provider window
      (``examples/bench_cache_warm.py``), against warming inline:

          quiet=5.0s   rebuilds 10 -> 12   build CPU 57.0 -> 67.8 s   reader miss 13.0 -> 22.2%
          quiet=0.5s   rebuilds 11 -> 12   build CPU 63.9 -> 71.0 s   reader miss 18.0 -> 24.5%

      Rebuilds went UP. Deferring the warm simply lets a READER arrive first and build it instead,
      which costs a rebuild *and* a slow request. The one metric that improved — adapter time
      blocked on the poller lock — moved 3.4 -> 1.5 s at 5 s and 1.8 -> 1.6 s at 0.5 s, which is
      inside this machine's run-to-run variance.

    * **There is no burst even at startup**, which was the last regime left. ``start()`` makes every
      adapter poll immediately, so eight simultaneous writes looked like the one case coalescing
      would win. Measured, it is a dead heat — 2 rebuilds either way, CPU and reader latency flat.
      The reason is structural and was in front of me the whole time: ``poll_adapter_once`` holds a
      GLOBAL lock across poll + post-cycle, so adapters can never finish simultaneously. By the time
      the second one is inside ``_post_cycle`` the first one's warm has already returned. **The lock
      that made single-flight useless is also what makes coalescing useless.**

    So the answer is that the invalidations are not redundant. Each rebuild serves a genuinely
    changed catalog for the ~60 s until the next write. The 5.6 s cost is the clusterer's, not the
    scheduler's, and it belongs to the quadratic-clustering finding instead.

    The machinery stays because it is correct, tested, and free when off — and because if the poller
    lock is ever narrowed (which is its own worthwhile change), simultaneous finishes become
    possible and this becomes relevant. ``story_cache_warm`` logs ``coalesced``, so that question
    can be answered from production rather than from a model of it."""
    return _env_float_allowing_zero("RWE_STORY_WARM_COALESCE", 0.0)


def warm_max_delay() -> float:
    """The starvation cap: however busy ingestion is, a warm fires at least this often once dirty.

    Without it, quiescence-based debouncing has a real failure mode — a catalog written to more
    often than the quiet window never goes quiet, so the warm never fires and EVERY reader pays a
    cold build. That is strictly worse than the behaviour being replaced, so the cap is not a
    refinement; it is what makes the design admissible."""
    return _env_float_allowing_zero("RWE_STORY_WARM_MAX_DELAY", 60.0)


class _Warmer:
    """One daemon thread that collapses a burst of write notifications into a single rebuild.

    Fires when EITHER the catalog has been quiet for ``warm_coalesce_window`` seconds OR
    ``warm_max_delay`` has passed since the burst began, whichever comes first."""

    def __init__(self, store_, log=None):
        self._store = store_
        self._log = log
        self._cv = threading.Condition()
        self._dirty_at: Optional[float] = None      # last request
        self._burst_at: Optional[float] = None      # first request of the current burst
        self._stop = False
        self.warms = 0                              # diagnostics; nothing reads it for control
        self.requests = 0
        self._served = 0
        self.requests_absorbed = 0
        self._thread = threading.Thread(target=self._run, name="story-warmer", daemon=True)
        self._thread.start()

    def request(self) -> None:
        with self._cv:
            now = _time.monotonic()
            self._dirty_at = now
            self.requests += 1
            if self._burst_at is None:
                self._burst_at = now
            self._cv.notify()

    def stop(self, timeout: float = 5.0) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._stop and self._dirty_at is None:
                    self._cv.wait()
                if self._stop:
                    return
                # Wait for quiescence, but never past the starvation cap.
                while not self._stop:
                    now = _time.monotonic()
                    quiet = now - (self._dirty_at or now)
                    burst = now - (self._burst_at or now)
                    window, cap = warm_coalesce_window(), warm_max_delay()
                    if quiet >= window or burst >= cap:
                        break
                    self._cv.wait(timeout=max(0.05, min(window - quiet, cap - burst)))
                if self._stop:
                    return
                self._dirty_at = None
                self._burst_at = None
                # How many write notifications this single rebuild is about to absorb. 1 means it
                # coalesced nothing; >1 is the whole point, and logging it is what will tell us
                # whether coalescing earns its keep in production rather than only on a bench.
                coalesced = self.requests - self._served
                self._served = self.requests
                self.requests_absorbed = coalesced
            try:
                t0 = _time.perf_counter()
                stories = warm_cache(self._store)
                if stories is not None:
                    self.warms += 1
                    # Keep emitting `story_cache_warm` with a duration. That log line is how this
                    # problem was found in the first place — a change that fixed the cost and
                    # removed the evidence would be a bad trade. `coalesced` counts the write
                    # notifications this single rebuild absorbed.
                    if self._log is not None:
                        self._log("story_cache_warm", stories=stories,
                                  durationMs=round((_time.perf_counter() - t0) * 1000.0, 1),
                                  coalesced=coalesced)
            except Exception as e:
                # A warm that cannot be built is a slow next request, never a dead warmer thread.
                if self._log is not None:
                    try:
                        self._log("story_cache_warm_failed", error=f"{type(e).__name__}: {e}")
                    except Exception:
                        pass


_WARMER: Optional["_Warmer"] = None
_WARMER_LOCK = threading.Lock()


def request_warm(store_, log=None) -> bool:
    """Ask for the story cache to be rebuilt soon. NON-BLOCKING — returns immediately.

    This is what pollers should call instead of :func:`warm_cache`. With coalescing disabled
    (``RWE_STORY_WARM_COALESCE=0``) it warms inline on the caller's thread, which is exactly the
    previous behaviour, so the flag is a true kill switch rather than a different code path.

    Returns True if the request was queued to the background warmer, False if it warmed inline.

    The inline branch logs ``story_cache_warm`` too. It did not, and that was a real regression:
    ``_Warmer._run`` emits the event, coalescing is OFF in production, so the ONE log line that
    makes the dominant rebuild cost visible was only ever emitted on the path nobody runs. Nine
    hours of production forensics — I/O, CPU credits, corpus refresh — were spent re-deriving a
    number this line would have printed. The comment above the other call site says a change that
    fixed the cost and removed the evidence would be a bad trade; the kill switch made exactly
    that trade, silently, because the switch and the instrumentation lived on opposite branches."""
    global _WARMER
    if warm_coalesce_window() <= 0:
        t0 = _time.perf_counter()
        stories = warm_cache(store_)
        if stories is not None and log is not None:
            # coalesced=1 matches _Warmer's meaning: this rebuild absorbed one write notification.
            log("story_cache_warm", stories=stories,
                durationMs=round((_time.perf_counter() - t0) * 1000.0, 1), coalesced=1)
        return False
    with _WARMER_LOCK:
        if _WARMER is None or _WARMER._store is not store_ or not _WARMER._thread.is_alive():
            if _WARMER is not None:
                _WARMER.stop(timeout=0.1)
            _WARMER = _Warmer(store_, log=log)
        _WARMER.request()
    return True


def shutdown_warmer() -> None:
    """Stop the background warmer (tests, benchmarks, and a clean process exit)."""
    global _WARMER
    with _WARMER_LOCK:
        if _WARMER is not None:
            _WARMER.stop()
            _WARMER = None


def tier_b_attach_enabled() -> bool:
    """Whether Tier B articles ATTACH to built stories as coverage. OFF — ``RWE_STORY_TIER_B_ATTACH=1``
    turns it on.

    This is M4 of `docs/SCALE_ROADMAP.md`, the mechanism the whole Tier B lane exists for: an
    admitted Tier B outlet is searchable and attributable, and without attachment its articles can
    never appear beside the stories they cover — admitting a source just made it vanish from the
    Stories surface. Dark by default like every flag that changes what a reader sees; the admit
    command is the human gate on WHICH outlets, this flag is the gate on WHETHER the lane renders.
    """
    v = os.environ.get("RWE_STORY_TIER_B_ATTACH", "").strip().lower()
    return v in ("1", "true", "yes")


def attach_tier_b(store_, stories: list, *, date_from=None, max_scan=None) -> list:
    """Attach Tier B articles to already-built stories, as coverage that never votes.

    The one non-negotiable property, from the roadmap's own claim ("assignment is linear in new
    arrivals and **cannot alter the partition**"): every field a built story already carries is
    byte-identical after this pass. Attachment may only APPEND — marked entries at the END of
    ``coverage`` (the existing list is a strict prefix), plus an ``attachedCoverage`` count that
    exists only when something attached. Membership, ids, distribution, blindspot, publisher
    counts and chips are computed from Tier A members upstream and are not recomputed here: a
    Tier B outlet is unrated, so it adds coverage and no lean vote — and `stabilize_ids` has
    already synced the member table from the pre-attachment coverage, so downstream member
    consumers (rec story quotas, coverage comparison) never see an attached row.

    The assignment rule is the shadow harness's, imported rather than restated
    (:func:`source_evaluation.would_attach` — exact against ``clustering.pair_admits``), so "would
    this article have joined?" and "attach it" can never drift apart. The Tier B rows come from
    ``include_publishers`` — the same term set ``corpus.sql_exclusions`` removed from the
    clustering fetch, on the same column, so the two sets are disjoint by construction (the alias
    residue `corpus.select` drops post-SQL is missed here too; measured as small, and a missed
    attachment is an absent addendum, never a wrong story).

    Deterministic (rows visited newest-first with id tiebreak; `would_attach` is order-stable),
    and fail-soft: this decorates a build that is already correct, so any failure returns the
    stories untouched rather than costing a reader the page."""
    # Local import, one level down from the audits: `source_evaluation` is the shadow harness's
    # module and nothing else in the serving path needs it — the flag-off deployment never pays
    # for loading it.
    import source_evaluation
    try:
        terms = corpus.tier_b_exclusions()
        if not terms or not stories:
            return stories
        if date_from is None:
            date_from = _window_start()
        cap = max_scan or max_scan_default()
        rows, _total = store_.search_feed_articles(
            date_from=date_from, sort="newest",
            pagination=OffsetPagination.from_params(cap, 0, max_limit=cap),
            include_publishers=terms)
        if not rows:
            return stories
        index = source_evaluation.assignment_index(stories)
        by_id = {s.get("id"): s for s in stories}
        member_urls = {c.get("url") for s in stories for c in s.get("coverage", ())}
        attached = 0
        # The SAME serializer `build_stories` feeds its rows through (line ~2284), so an attached
        # entry's publisher prettification, L2.2 nulls and lean bucketing can never diverge from a
        # member's — one row shape in, one Article shape out, on both sides of the tier boundary.
        arts = [discover.feed_article_to_article(r) for r in rows]
        for a in sorted(arts, key=lambda a: (a.get("publishedAt") or "", a.get("id") or ""),
                        reverse=True):
            if a.get("url") in member_urls or a.get("id") in member_urls:
                continue                    # an alias twin of an existing member is not new coverage
            sid = source_evaluation.would_attach(a.get("headline"), a.get("publishedAt"), index)
            story = by_id.get(sid)
            if story is None:
                continue
            story.setdefault("coverage", []).append({
                "publisher": a.get("publisher"), "headline": a.get("headline"),
                "lean": a.get("lean"), "leanBucket": a.get("leanBucket"),
                "register": a.get("register"), "emotion": a.get("emotion"),
                "url": a.get("url"), "publishedAt": a.get("publishedAt"),
                "tierB": True,
            })
            story["attachedCoverage"] = story.get("attachedCoverage", 0) + 1
            member_urls.add(a.get("url"))
            attached += 1
        # BOTH counters register even on a zero-attach pass (incr(0) creates the key). Measured
        # cost of the old `if attached:` guard on production 2026-08-31: a healthy pass that
        # matched nothing left /api/metrics with no tier_b keys at all, indistinguishable from
        # the flag being off or the pass never running. Three states, three signatures now:
        # runs>0 & attached=0 = healthy-no-match · no keys = not running · error>0 = broken.
        obs_metrics.incr("story_tier_b_attach_runs_total")
        obs_metrics.incr("story_tier_b_attached_total", attached)
        return stories
    except Exception:
        # Fail-soft for the reader, never silent for the operator: during development this
        # swallowed a NameError and the tests read "nothing attached" — the counter is what
        # separates "no Tier B article matched" from "the attach pass is broken" in /api/metrics.
        obs_metrics.incr("story_tier_b_attach_error_total")
        return stories


def _cached_build(store_, *, topic, date_from, date_to, max_scan, min_articles, min_publishers,
                  allow_stale: bool = True) -> list:
    """``build_stories(_fetch(...))`` behind a cache with TWO independent invalidation conditions,
    because either alone is wrong:

    * **A catalog fingerprint** ``(row count, newest fetched_at)`` decides freshness, so any
      catalog write immediately marks the entry stale. A pure TTL cache would keep serving
      pre-ingest clusters indefinitely — a reader could open a story link the list had just
      rendered and get a stale member set. A bare row COUNT is not enough either: a retention
      prune plus an ingest in the same interval leaves the count identical while the content
      differs entirely. Between polls (``RWE_POLL_INTERVAL``, default 600 s) the fingerprint is
      stable, so this is a long-lived cache in practice, not a permanently-cold one.
    * **TTL** bounds staleness on the other axis. ``date_from`` defaults to a rolling ``now −
      scan_days``, so a quiet catalog would otherwise pin an ever-older window.

    **A stale entry is SERVED, and the rebuild happens behind the reader** (``serve_stale``, on by
    default). The fingerprint used to live in the cache key itself, which made invalidation an
    eviction: the instant any adapter ingested one article, the next reader owned the whole
    rebuild. Measured in production (2026-08-01): the rebuild is **11,476 ms** against a web tier
    that abandons the call at **6,000 ms** — so every ingest handed some reader a guaranteed
    failure, on the highest-traffic pages, several times an hour. Now the entry keyed by the
    LOGICAL parameters is returned as it stands and one background thread rebuilds it; the reader
    who found the stale entry gets the previous poll's stories in under a millisecond instead of a
    timeout. The staleness this admits is bounded by the rebuild duration (~12 s today) on content
    this deployment already declares tolerant of 600 s of drift (``RWE_STORIES_CACHE_TTL``) — two
    percent of the envelope, spent to delete the one deterministic failure in the request path.
    An entry past its TTL is *never* served stale: the TTL bounds rolling-window drift, and
    serve-stale must not stretch a bound it did not set.

    ``allow_stale=False`` is for callers whose PURPOSE is a fresh build — the poller's
    :func:`warm_cache` and the background refresh itself. Without it the warm would find the stale
    entry, serve it to itself, request another refresh, and never build anything.

    The store's identity still scopes every entry: two stores must never share a build. One process
    serves one database in production, but tests and any future multi-tenant caller would silently
    read each other's clusters without it."""
    # '' and None must be ONE value from here down. The row fetch treats '' as "no filter"
    # (truthiness), but the cache KEY and the identity gate compared against None — so a
    # ``?topic=`` request built a full-catalog TWIN of the default view under the key ('', …),
    # unstabilized. Probe-measured in production (2026-08-02): that twin rendered a raw id for a
    # 93-member cluster whose ledger id was what the default view (and the detail endpoint) served.
    topic = topic or None
    date_from = date_from or None
    date_to = date_to or None

    def _build():
        # The clustering runs in the build subprocess when it can (P0-2′ — see
        # `build_subprocess_enabled` for why, and why falling back inline is silent): the caller
        # here is a background thread (warmer, stale refresh) or a cold-start reader, and either
        # way the CPU belongs off this process's GIL. `None` means "do it here after all".
        stories = None
        if _subprocess_eligible(store_):
            stories = _offloaded_build(store_, topic=topic, date_from=date_from, date_to=date_to,
                                       max_scan=max_scan, min_articles=min_articles,
                                       min_publishers=min_publishers)
        if stories is None:
            rows = _fetch(store_, topic=topic, date_from=date_from,
                          date_to=date_to, max_scan=max_scan)
            ev, band = _event_inputs(store_)
            stories = build_stories(rows, min_articles=min_articles,
                                    min_publishers=min_publishers,
                                    entities=_entities_for(store_, rows),
                                    event_verdicts=ev, band_out=band)
            _event_flush(store_, band)
        # Identity is applied HERE — in the parent, never the child — and not inside build_stories,
        # which stays a pure function of its rows. Only the unfiltered build WRITES identity: a
        # topic- or date-filtered view sees a subset of each cluster, so letting it write the map
        # would hand ids to partial clusters and then hand them back to the full ones on the next
        # unfiltered build — churn caused by the fix for churn. But filtered views must still READ
        # the map: they render links, and `get_story` resolves ids against the stabilized default
        # view only — a filtered list serving raw ids is a list of dead links for every cluster
        # whose anchor ever churned (measured 8.8% of rendered topic-filtered links in production,
        # 2026-08-02). Parent-side also keeps the child read-only, which is what makes a killed
        # worker recoverable by construction.
        if stable_ids():
            if topic is None and date_from is None and date_to is None:
                stories = stabilize_ids(store_, stories)
            else:
                stories = stabilize_ids_readonly(store_, stories)
        # Topics/tags, on the same split and for the same reason: only the UNFILTERED build knows
        # the window's story frequencies, which decide both what counts as a background name and
        # how specific each one is, so only it computes and writes. A filtered view reports what
        # the full build concluded rather than recomputing over its subset and describing the same
        # story two different ways on two pages.
        if topic is None and date_from is None and date_to is None:
            stories = attach_tags(store_, stories)
        else:
            stories = attach_tags_readonly(store_, stories)
        # Tier B attachment (M4) runs LAST, in the parent, after identity: `stabilize_ids` has
        # already synced the member table from the pre-attachment coverage, so an attached row can
        # never become a "member" downstream — the ordering IS the containment proof. Parent-side
        # also covers the subprocess path with the same single implementation.
        if tier_b_attach_enabled():
            stories = attach_tier_b(store_, stories, date_from=date_from, max_scan=max_scan)
        return stories

    ttl = cache_ttl()
    if ttl <= 0:
        return _build()
    try:
        fingerprint = store_.catalog_fingerprint()
    except Exception:                       # a store without the fingerprint is simply uncached
        return _build()

    # The LOGICAL key: what the caller asked for, minus the catalog generation it is answered from.
    # The fingerprint moved out of the key and into the entry — that one change is the whole fix,
    # because it turns "the catalog changed" from an eviction (next reader rebuilds) into a state
    # the lookup can see and route around (serve the previous build, rebuild behind them).
    logical = (topic, date_from, date_to, max_scan, min_articles, min_publishers)

    def _lookup():
        """('fresh' | 'stale' | None, stories). Expired entries answer None, never 'stale' —
        the TTL bounds rolling-window drift and stale-serving must not stretch it."""
        with _CACHE_LOCK:
            entries = _CACHE.get(store_)
            hit = entries.get(logical) if entries else None
            if hit is None:
                return None, None
            built_at, built_fp, stories = hit
            if (_time.time() - built_at) >= ttl:
                return None, None
            return ("fresh" if built_fp == fingerprint else "stale"), stories

    state, stories = _lookup()
    if state == "fresh":
        return stories
    if state == "stale" and allow_stale and serve_stale():
        _request_stale_refresh(store_, logical)
        obs_metrics.incr("story_stale_served_total")
        return stories

    # SINGLE-FLIGHT the inline build — the true cold start (boot, TTL expiry, kill switch, and the
    # two fresh-on-purpose callers). `warm_cache` has always guarded the POLLER's threads against
    # each other; the reader path had no such guard, so every request that arrived during a rebuild
    # started a rebuild of its own. At the measured 20k-article cost that is ~10 s of CPU each, on a
    # box with far fewer cores than that has concurrent readers — the requests do not merely wait,
    # they compete, and each one makes the others slower. Three readers cost thirty seconds of work
    # to produce three copies of one identical answer.
    #
    # The waiters re-check the cache after acquiring: by then the winner has usually stored the
    # build, so they return it instead of repeating it. A build that RAISES releases the lock via
    # `with` and the next waiter tries — a failure is never cached and never wedges the key.
    with _CACHE_LOCK:
        lock = _BUILD_LOCKS.setdefault(logical, threading.Lock())
    with lock:
        # Re-read the fingerprint under the lock, THEN re-check. A waiter that kept its entry-time
        # fingerprint would judge the winner's just-stored build "stale" — the winner read a newer
        # catalog — and rebuild an answer it is already holding. Re-reading makes the winner's
        # build "fresh" to every waiter, which is the entire point of them having waited.
        try:
            fingerprint = store_.catalog_fingerprint()
        except Exception:                   # transient — keep the entry-time fingerprint;
            pass                            # worst case is one redundant background refresh later
        state, stories = _lookup()
        if state == "fresh":
            return stories
        # 'stale' here is NOT served, deliberately: every arrival at this lock wants a fresh build.
        # The fresh-on-purpose callers (warm, background refresh) came for exactly that; a reader
        # with the kill switch off asked for the old behaviour back; and a reader who found nothing
        # servable at entry only sees 'stale' now because the catalog moved again while they waited.
        built = _build()
        with _CACHE_LOCK:
            entries = _CACHE.setdefault(store_, {})
            # Bounded per store: evict oldest first rather than grow without limit across
            # topics/dates. Replacing an existing logical key needs no eviction — the map only
            # grows when the KEY is new, so only then may a neighbour be dropped for room.
            if len(entries) >= _CACHE_MAX and logical not in entries:
                for old in sorted(entries, key=lambda k: entries[k][0])[: len(entries) - _CACHE_MAX + 1]:
                    entries.pop(old, None)
            entries[logical] = (_time.time(), fingerprint, built)
            # Logical keys are a small, stable set (one per filter combination in actual use), but
            # the map is still bounded the same way the entry map is — an unbounded map of Locks
            # outliving their entries is the leak the previous fingerprint-keyed scheme had.
            if len(_BUILD_LOCKS) > _CACHE_MAX * 4:
                for dead in [k for k in _BUILD_LOCKS if k != logical][: len(_BUILD_LOCKS) // 2]:
                    _BUILD_LOCKS.pop(dead, None)
    return built


def _sort_stories(stories: list, sort: str) -> list:
    if sort == "latest":
        return sorted(stories, key=lambda s: (s["latest"] or "", s["id"]), reverse=True)
    if sort == "oldest":
        return sorted(stories, key=lambda s: (s["earliest"] or "~", s["id"]))
    if sort == "publishers":
        # Same "biggest" semantic as "top", so it gets the same demotion — otherwise this sort is a
        # one-click route back to the exact card the default ordering exists to keep off the top.
        # "latest"/"oldest" are untouched: a reader asking for newest wants newest.
        trust_aware = trust_ranking()
        return sorted(stories, key=lambda s: _size_rank(s, trust_aware=trust_aware), reverse=True)
    return stories       # "top" — build_stories already ordered biggest+freshest first


#: The default (unfiltered) build's logical cache key — the one the poller warms, ``list_stories``
#: serves, and every ``default_story_view`` consumer reads. One definition, so the peek and the
#: boot-window refresh kick can never drift onto different keys.
_DEFAULT_LOGICAL = (None, None, None, None, 2, 2)


def default_story_view(store_, *, build_inline: bool = False) -> list:
    """The cached default clustered view — the SAME build the poller warms, serve-stale protects,
    and the build subprocess computes. For request-path consumers that need the story layer as
    data (co-coverage counting, analyzer membership, the evidence index) rather than the paginated
    envelope.

    This exists because of a measured production outage: every publisher profile called
    :func:`cluster_from_store` — a full, cold, uncached clustering on the request thread — and the
    cost crossed the web tier's 6 s deadline as the window grew (283 ms at 600 articles, 4,891 ms
    at 15.3k on a fast idle box; slower still on the production host), turning every publisher
    page into "Try again". The cached view answers the same question in ~2 ms once warm, and a
    caller here inherits every protection the cache path has.

    Served from the cache (fresh, or stale-within-TTL with the usual background refresh) in the
    overwhelmingly common case — the poller warms this exact key on every ingest. Ids are then the
    STABILIZED ones ``/api/stories`` serves, which is what a caller linking a reader to a story
    page should have been using all along.

    The TRUE-COLD case (pre-first-warm boot; expired TTL on a quiet catalog) FORKS on the caller's
    contract, and the fork was decided by a production measurement, not taste. The post-deploy
    probe of 2026-08-02 caught FOUR inline clusterings on request threads in the first ~2 minutes
    after a restart, ~24 s each at 51.8k articles — uncached by design, so every consumer repeated
    the cost until the poller's first warm, and two at once starved a 2-vCPU box for everything
    else (docs/RECOMMENDATION_LATENCY.md, "Post-deploy verification").

    * **Request-path consumers (the default)** never build here. They get ``[]`` — a shape every
      consumer already tolerates, because it is exactly what an empty catalog produces — and the
      existing single-flighted background refresh is kicked, so the FIRST consumer heals the
      window for everyone at one build's cost, off the request threads.
    * **``build_inline=True``** keeps the pre-existing read-only inline build: uncached, raw ids,
      no identity write, and NO background spawn (a kick would eventually write the cache + the
      ledger, which the flag's one caller has contracted never to cause). That is ``/api/analyze``:
      it documents itself as writing nothing anywhere, and ``test_analysis_writes_nothing_anywhere``
      failed the first draft of this function for caching on its cold path. Offline CLI audits use
      it too — no cache-warming poller runs in a one-shot process.
    * With the cache disabled entirely (``RWE_STORIES_CACHE_TTL=0``) everyone builds inline: there
      is no cache to kick a refresh into, and the opt-out asks for exactly the uncached behaviour.

    Callers must treat the list as read-only: it is (usually) the cache's own object."""
    stories = _peek_default_view(store_)
    if stories is not None:
        obs_metrics.incr("story_default_view_peek_hit_total")
        return stories
    if build_inline or cache_ttl() <= 0:
        obs_metrics.incr("story_default_view_inline_build_total")
        _t = _time.perf_counter()
        try:
            rows = _fetch(store_)
            return build_stories(rows, min_articles=2, min_publishers=2,
                                 entities=_entities_for(store_, rows))
        finally:
            obs_metrics.observe("story_default_view_inline_build_ms",
                                (_time.perf_counter() - _t) * 1000.0)
    obs_metrics.incr("story_default_view_async_kick_total")
    _request_stale_refresh(store_, _DEFAULT_LOGICAL)
    return []


def _peek_default_view(store_) -> "list | None":
    """The cached default build if servable, else ``None`` — a LOOK, never a build.

    Mirrors ``_cached_build``'s lookup semantics for the default key, including the serve-stale
    contract: a stale-within-TTL entry is served AND a background refresh is requested, so a
    consumer arriving through the peek keeps the staleness bound a reader arriving through
    ``list_stories`` gets. Expired, missing, or stale-with-the-kill-switch-off answers ``None``
    and the caller decides what a miss means (``default_story_view``: a read-only inline build)."""
    ttl = cache_ttl()
    if ttl <= 0:
        return None
    try:
        fingerprint = store_.catalog_fingerprint()
    except Exception:
        return None
    logical = _DEFAULT_LOGICAL
    with _CACHE_LOCK:
        entries = _CACHE.get(store_)
        hit = entries.get(logical) if entries else None
    if hit is None:
        return None
    built_at, built_fp, stories = hit
    if (_time.time() - built_at) >= ttl:
        return None
    if built_fp == fingerprint:
        return stories
    if not serve_stale():
        return None
    _request_stale_refresh(store_, logical)
    obs_metrics.incr("story_stale_served_total")
    return stories


def cluster_from_store(store_, *, min_articles: int = 2, min_publishers: int = 2,
                       sim: float = clustering.DEFAULT_SIM,
                       window_days: float = clustering.DEFAULT_WINDOW_DAYS, max_scan: int = None) -> list:
    """The bare Story list for the current window, built FRESH on the calling thread.

    Uncached on purpose: it takes ``sim``/``window_days`` overrides the cache key does not carry —
    and that rationale is the whole contract. A caller passing NO overrides is asking the default
    question and belongs on :func:`default_story_view`; three request-path callers sat here anyway
    and one of them took every publisher page down when the catalog outgrew the web deadline.
    Operator diagnostics and parameterised audits are what this function is for."""
    rows = _fetch(store_, max_scan=max_scan)
    ev, band = _event_inputs(store_)
    stories = build_stories(rows, min_articles=min_articles,
                            min_publishers=min_publishers, sim=sim, window_days=window_days,
                            event_verdicts=ev, band_out=band,
                            entities=_entities_for(store_, rows))
    _event_flush(store_, band)
    return stories


def list_stories(store_, *, topic=None, publisher=None, lean=None, country=None, blindspot=None,
                 story_type=None, date_from=None, date_to=None,
                 sort: str = "top", limit: int = 30, offset: int = 0, min_articles: int = 2,
                 min_publishers: int = 2, max_scan: int = None, debug: bool = False) -> dict:
    """The paginated, filtered Story envelope Discover + Stories consume:
    ``{stories, total, page, pageSize, hasMore, remainingPages, sort, countryFacets,
    blindspotFacets, typeFacets}`` (+ ``clusterMs`` + ``diagnostics`` when ``debug``). topic/date are
    pre-filtered in SQL; publisher/lean/country/blindspot/story_type are coverage post-filters on
    the built stories. ``story_type`` is the curated SOURCE type — news / research / community,
    projected from the outlet registry's ``kind`` column by
    :func:`outlet_registry.source_type` — and matches a story with at least one member publisher
    of that type. A publisher the registry does not carry is unclassified and matches no type. ``country`` matches EVENT location only — the story's member-consensus event
    countries (``_event_consensus``); publisher homes never substitute, so an unlocated story
    appears under "All" and under no country. ``blindspot`` is the coverage-gap lens:
    ``"any"`` matches stories with a DETECTED gap (``blindspotSide`` set), a side matches that
    thin side exactly; ``blindspotSide`` None means balanced-OR-unknown (an all-unrated story
    casts no votes) and never matches — a gap is a counted finding, not a default. Both facet
    dicts are STORY counts under the other active filters, computed BEFORE their own filters and
    pagination — each picker's source of truth, so an option is only offered when selecting it
    returns ≥1 story."""
    sort = sort if sort in SORTS else "top"
    pg = OffsetPagination.from_params(limit, offset)
    t0 = _time.perf_counter()
    # ONE story universe (2026-08-02). `topic` used to be a BUILD parameter — a separate clustering
    # over the topic's rows — and clustering is corpus-relative (IDF weights, token-commonness
    # blocking, merge decisions), so a topic build could compose stories the default build splits
    # differently: a probe found a 12-member Business story whose ledger votes fractured 4/4/2
    # across three default-view ids (best share 0.33 < the 0.5 carryover), unresolvable by any id
    # mapping — 59 of 1,257 rendered topic links (4.7%) were dead this way even after filtered
    # builds learned to read the ledger. `get_story` resolves against the default view only, so a
    # listed story must BE a default-view story: topic now post-filters the one build by the
    # dominant `story.topic`, exactly as publisher/lean/country/blindspot always have. An explicit
    # date range still gets its own (read-only-stabilized) build: it is the one filter whose rows
    # can lie outside the default window, and no UI surface sends it.
    topic_filter = (topic or "").strip().lower()
    if date_from or date_to:
        stories = _cached_build(store_, topic=topic, date_from=date_from, date_to=date_to,
                                max_scan=max_scan, min_articles=min_articles,
                                min_publishers=min_publishers)
        topic_filter = ""                     # the build already narrowed by topic
    else:
        stories = _cached_build(store_, topic=None, date_from=None, date_to=None,
                                max_scan=max_scan, min_articles=min_articles,
                                min_publishers=min_publishers)
    cluster_ms = round((_time.perf_counter() - t0) * 1000.0, 2)

    if topic_filter:
        stories = [s for s in stories if (s.get("topic") or "").lower() == topic_filter]
    if publisher and publisher.strip():
        want = publisher.strip().lower()
        stories = [s for s in stories if want in {p.lower() for p in s["publishers"]}]
    if lean in ("left", "center", "right"):
        stories = [s for s in stories if s["distribution"][lean] > 0.0]
    # Story-level country + blindspot + type facets: counted after topic/publisher/lean narrowed
    # the set, before their own filters (standard faceting — a picker must not collapse to the
    # current selection) and before pagination.
    country_facets: dict = {}
    blindspot_facets: dict = {}
    # Seeded with every type, so a lens that currently matches nothing reports 0 rather than going
    # missing: the reader is told the option exists and is empty, which is the whole point of
    # showing the number. `source_type` memoises its registry resolves, so the per-publisher walk
    # costs one lookup per distinct name for the life of the process.
    type_facets: dict = {t: 0 for t in outlet_registry.SOURCE_TYPES}
    for s in stories:
        for c in s["countries"]:
            country_facets[c] = country_facets.get(c, 0) + 1
        if s["blindspotSide"]:
            blindspot_facets[s["blindspotSide"]] = blindspot_facets.get(s["blindspotSide"], 0) + 1
        # A story counts under EVERY type that covers it, never once under a "dominant" one. The
        # filter is "has coverage from", so a Nature+BBC event is one Research story AND one News
        # story; each count is what selecting that lens would actually return, which is the only
        # number a picker may show. They therefore do not sum to `total`, by design.
        for kind in {outlet_registry.source_type(p) for p in s["publishers"]}:
            if kind in type_facets:
                type_facets[kind] += 1
    if country and country.strip():
        want = country.strip().upper()
        stories = [s for s in stories if want in s["countries"]]
    if blindspot == "any":
        stories = [s for s in stories if s["blindspotSide"]]
    elif blindspot in ("left", "center", "right"):
        stories = [s for s in stories if s["blindspotSide"] == blindspot]
    if story_type in outlet_registry.SOURCE_TYPES:
        # Coverage post-filter, the same shape as `publisher` and `lean` above: a story matches
        # when at least ONE of its member publishers is curated as that type of source. "Has
        # coverage from" is the honest reading of a cluster — a story is many publishers, so it has
        # no single type of its own, and the "Covered by" lens already means exactly this for lean.
        # An uncurated publisher matches nothing (see `outlet_registry.source_type`), so this can
        # only narrow to sources somebody classified.
        #
        # Applied HERE, below the facet pass, for the same reason country and blindspot are: a
        # filter counted after itself collapses its own picker to the current selection. Filters are
        # conjunctive, so the position changes the facets and never the result.
        stories = [s for s in stories
                   if any(outlet_registry.source_type(p) == story_type for p in s["publishers"])]

    stories = _sort_stories(stories, sort)
    total = len(stories)
    page = stories[pg.offset: pg.offset + pg.limit] if pg.limit > 0 else stories
    out = {"stories": page, "total": total, "sort": sort, "countryFacets": country_facets,
           "blindspotFacets": blindspot_facets, "typeFacets": type_facets,
           **pg.meta(total)}
    if debug:
        out["clusterMs"] = cluster_ms
        out["diagnostics"] = _diagnostics(stories, cluster_ms)
    return out


def get_story(store_, story_id: str, *, min_articles: int = 2, min_publishers: int = 2,
              max_scan: int = None, **kwargs) -> Optional[dict]:
    """One Story by id — re-derive the deterministic clusters and return the match (its stable,
    anchored id means the lookup survives new coverage of the same event). ``None`` if it no longer
    exists (the catalog changed enough that the event dissolved).

    Shares the list's cached build, which matters twice: a detail page costs no extra clustering,
    and the two surfaces cannot disagree about which stories exist — a narrower scan here than in
    ``list_stories`` would 404 links the list had just rendered."""
    for s in _cached_build(store_, topic=None, date_from=None, date_to=None, max_scan=max_scan,
                           min_articles=min_articles, min_publishers=min_publishers):
        if s["id"] == story_id:
            return s
    return None


#: Noise floor for the Similar Stories rail, measured on the live catalog (2,852 stories).
#:
#: This is a BACKSTOP, not the selector — :func:`similar_rel_ratio` does the selecting. Its job is
#: the one case a relative rule cannot handle: a story with no related coverage at all, whose best
#: candidate is noise. A ratio would happily keep the top few of nothing; an absolute floor says
#: there is nothing. Production p90 (the noise level) runs 0.011-0.017 and genuine matches start
#: around 0.048, so this sits between them.
SIMILAR_NOISE_FLOOR = 0.035

#: Share of the BEST candidate's score another must reach to be shown. The actual selector.
#:
#: Measured against three production stories, reading the titles at each rank to find where genuine
#: matches stop:
#:
#:     story           best     last good   first bad                      ratio at the break
#:     Lake Ontario    0.1122   0.0617      0.0424 (NFL boycott)           0.55
#:     Kyiv strike     0.2456   0.1467      0.1210 (Iran wedding)          0.60
#:     Venezuela oil   0.0678   0.0488      0.0480 (meat tariffs)          0.72
#:
#: 0.5 sits under every break, so nothing genuine is cut, and above the noise in the two stories
#: with a clear gap. The Venezuela distribution is flat — its top five span 0.048-0.068 and four of
#: them are the same event — so a ratio cannot isolate its one marginal card, and a fixed threshold
#: could not either. Fewer false negatives is the right trade for a rail.
SIMILAR_REL_RATIO = 0.5


def similar_min_score() -> float:
    """Absolute noise floor for the Similar Stories rail; ``RWE_STORY_SIMILAR_MIN`` overrides.

    TWO EARLIER DEFAULTS WERE WRONG HERE, both because a single absolute number cannot do this job.
    0.33 was :func:`merge_similarity` — the score at which the clusterer MERGES two clusters, so
    nothing above it survives as a separate story and the rail rendered nothing. 0.25 was the
    duplicate audit's near-threshold band, still far above anything a 2,852-story catalog produces:
    with IDF at ``log(1 + N/df)`` and a median pair score of 0, real matches there land at
    0.05-0.25.

    And the top score itself varies per story — 0.246 for a Kyiv strike, 0.068 for the Venezuela
    oil deal — so no fixed value keeps one story's genuine matches without admitting another's
    noise. :func:`similar_rel_ratio` is the selector; this is only the backstop that lets a story
    with NO related coverage return nothing instead of the best few of nothing."""
    v = os.environ.get("RWE_STORY_SIMILAR_MIN", "").strip()
    if v:
        try:
            return max(0.0, min(1.0, float(v)))
        except ValueError:
            pass
    return SIMILAR_NOISE_FLOOR


def similar_rel_ratio() -> float:
    """Share of the best candidate's score another must reach; ``RWE_STORY_SIMILAR_RATIO`` overrides.

    The selector, and relative BY NECESSITY: see :data:`SIMILAR_REL_RATIO` for the measurement.
    0 disables it and leaves only the absolute floor."""
    v = os.environ.get("RWE_STORY_SIMILAR_RATIO", "").strip()
    if v:
        try:
            return max(0.0, min(1.0, float(v)))
        except ValueError:
            pass
    return SIMILAR_REL_RATIO


#: Profiles are rebuilt from every coverage headline in the catalog, which at 2,852 stories is tens
#: of thousands of tokenizations. Memoised on a per-story fingerprint that changes whenever its
#: coverage does, so a warm process pays it once per story per rebuild rather than once per request.
_SIMILAR_PROFILES: dict = {}
#: Bound: the memo is cleared wholesale past this, so it cannot grow without limit across rebuilds.
_SIMILAR_PROFILE_MAX = 20000


def _similar_profile(s: dict) -> frozenset:
    """A BUILT story's vocabulary: title, summary, and every coverage headline.

    The same shape as :func:`_profile`, from what a built story carries. Not the title alone, for
    the measured reason recorded there: the four Seattle clusters score 0.15 on headlines and 0.56
    on profiles, and headline-only matching is the failure this rail was reported for.

    Memoised: the key carries the coverage count and the last update, so a story whose coverage
    grew is re-tokenized and one that did not is not."""
    key = f"{s.get('id')}:{s.get('totalCoverage')}:{s.get('updatedAt')}"
    hit = _SIMILAR_PROFILES.get(key)
    if hit is not None:
        return hit
    toks: set = set()
    toks |= clustering.title_tokens(s.get("title") or "")
    toks |= clustering.title_tokens(s.get("summary") or "")
    for row in s.get("coverage") or []:
        toks |= clustering.title_tokens(row.get("headline") or "")
    out = frozenset(toks)
    if len(_SIMILAR_PROFILES) >= _SIMILAR_PROFILE_MAX:
        _SIMILAR_PROFILES.clear()
    _SIMILAR_PROFILES[key] = out
    return out


def similar_stories(store_, story_id: str, *, limit: int = 10, min_score: Optional[float] = None,
                    min_shared: Optional[int] = None, rel_ratio: Optional[float] = None,
                    max_scan: int = None,
                    min_articles: int = 2, min_publishers: int = 2) -> Optional[list]:
    """Stories about the same or a closely related event, best first. ``None`` if the id is gone.

    WHY THIS IS AN ENDPOINT and not a client-side pass over ``/api/stories``. The rail used to be
    filled by a same-topic query plus the day's top events — topic is a shelf, not a subject, and
    "also busy today" is not a relationship, so a Venezuelan oil deal sat beside a Supreme Court
    ruling about a ballroom on the strength of sharing the word "Trump". Fixing the RANKING alone
    cannot help when the candidate pool was chosen that way; the pool has to be the catalog. Doing
    that in the browser would mean shipping the whole 60-story list to the story page, which a RUM
    investigation measured at ~200 KB and a third of the page's API transfer and which was removed
    from this page for exactly that reason. Scored here, the wire carries ``limit`` stories.

    Scored with the clusterer's own measure: IDF-weighted Jaccard over profiles, the arithmetic
    :func:`_merge_duplicates` uses to decide whether two clusters are one story. The IDF weighting
    is what makes this work — plain overlap treats every shared word as equal evidence, so "trump",
    in hundreds of headlines, counts for as much as a name in two.

    Costs no clustering: reads the SAME cached build the list and detail pages serve, so the ids
    agree with what the reader just clicked and a cold build is never forced onto this request."""
    floor = similar_min_score() if min_score is None else min_score
    shared_floor = clustering.MIN_SHARED_TOKENS if min_shared is None else min_shared
    ratio = similar_rel_ratio() if rel_ratio is None else rel_ratio
    stories = _cached_build(store_, topic=None, date_from=None, date_to=None, max_scan=max_scan,
                            min_articles=min_articles, min_publishers=min_publishers)
    target = None
    for s in stories:
        if s["id"] == story_id:
            target = s
            break
    if target is None:
        return None

    others = [s for s in stories if s["id"] != story_id]
    if not others:
        return []
    tp = _similar_profile(target)
    profiles = [_similar_profile(s) for s in others]
    # The target joins the corpus the weights are computed over: its own vocabulary is part of what
    # makes a token common or rare in this catalog.
    weights = clustering.idf_weights([tp] + profiles)
    tt = sum(weights.get(t, 1.0) for t in tp)

    scored = []
    for s, p in zip(others, profiles):
        inter = tp & p
        if len(inter) < shared_floor:
            continue
        w = sum(weights.get(t, 1.0) for t in inter)
        den = tt + sum(weights.get(t, 1.0) for t in p) - w
        score = (w / den) if den else 0.0
        scored.append((score, s))
    # SELECTION IS RELATIVE, gated by an absolute backstop. The best candidate's score varies by
    # nearly 4x between stories on the live catalog (0.246 for a Kyiv strike, 0.068 for the
    # Venezuela oil deal), so a fixed cut keeps one story's real matches only by admitting
    # another's noise. Everything within `ratio` of the best is kept; the floor is what lets a
    # story with nothing related return nothing rather than the best few of nothing.
    best = max((sc for sc, _ in scored), default=0.0)
    cut = max(floor, best * ratio)
    scored = [(sc, st) for sc, st in scored if sc >= cut]
    # Ties broken by breadth of coverage then id, so the order is stable build to build rather than
    # dependent on catalog order.
    scored.sort(key=lambda r: (-r[0], -(r[1].get("totalCoverage") or 0), r[1]["id"]))
    return [s for _, s in scored[:max(0, limit)]]


def similar_diagnostics(store_, story_id: str, *, top: int = 8, max_scan: int = None,
                        min_articles: int = 2, min_publishers: int = 2) -> Optional[dict]:
    """The score distribution behind :func:`similar_stories`, with NO floor applied. ``None`` if the
    id is gone.

    Exists because an absolute similarity floor does not transfer between catalogs, and shipping
    one that did not was this feature's second bug. Weighted Jaccard is ``w / (Ta + Tb - w)``: both
    totals grow with how many coverage headlines a story carries, and the IDF weight
    ``log(1 + N/df)`` grows with catalog size, so the same relationship scores 0.32-0.77 across nine
    short demo profiles and an order of magnitude lower across sixty production ones. A floor
    calibrated on the first is meaningless on the second — which is what emptied the rail.

    So this reports the numbers a floor has to be chosen against: profile sizes, and the top pairs
    ranked with the floors ignored. Runs in the SERVING process against the warm cached build, so
    it costs no clustering — the reason it is an endpoint rather than a `docker exec` snippet,
    which would start cold and force a full rebuild on the request path."""
    stories = _cached_build(store_, topic=None, date_from=None, date_to=None, max_scan=max_scan,
                            min_articles=min_articles, min_publishers=min_publishers)
    target = None
    for s in stories:
        if s["id"] == story_id:
            target = s
            break
    if target is None:
        return None
    others = [s for s in stories if s["id"] != story_id]
    tp = _similar_profile(target)
    profiles = [_similar_profile(s) for s in others]
    weights = clustering.idf_weights([tp] + profiles)
    tt = sum(weights.get(t, 1.0) for t in tp)

    rows = []
    for s, p in zip(others, profiles):
        inter = tp & p
        w = sum(weights.get(t, 1.0) for t in inter)
        den = tt + sum(weights.get(t, 1.0) for t in p) - w
        rows.append(((w / den) if den else 0.0, len(inter), len(p), s))
    rows.sort(key=lambda r: -r[0])
    sizes = sorted(len(p) for p in profiles) or [0]
    scores = sorted((r[0] for r in rows), reverse=True) or [0.0]

    def at(frac: float) -> float:
        return round(scores[min(len(scores) - 1, int(frac * (len(scores) - 1)))], 4)

    return {
        "candidates": len(others),
        "targetProfileTokens": len(tp),
        "targetTotalWeight": round(tt, 2),
        "candidateProfileTokens": {"min": sizes[0], "median": sizes[len(sizes) // 2], "max": sizes[-1]},
        "floorInEffect": similar_min_score(),
        "ratioInEffect": similar_rel_ratio(),
        "cutInEffect": round(max(similar_min_score(), (scores[0] if scores else 0.0) * similar_rel_ratio()), 4),
        "minSharedInEffect": clustering.MIN_SHARED_TOKENS,
        # The shape a floor must be chosen against: best first, then where the mass sits.
        "scoreQuantiles": {"max": at(0.0), "p90": at(0.1), "p50": at(0.5), "min": round(scores[-1], 4)},
        "top": [
            {"score": round(sc, 4), "shared": sh, "profileTokens": pt,
             "title": (s.get("title") or "")[:90], "topic": s.get("topic"),
             "coverage": s.get("totalCoverage")}
            for sc, sh, pt, s in rows[:max(0, top)]
        ],
    }


def _diagnostics(stories: list, cluster_ms: float) -> dict:
    sizes = sorted((s["totalCoverage"] for s in stories), reverse=True)
    dist: dict = {}
    for sz in sizes:
        dist[sz] = dist.get(sz, 0) + 1
    trust: dict = {TRUST_OK: 0, TRUST_LOW: 0, TRUST_UNVERIFIED: 0}
    for s in stories:
        key = s.get("clusterTrust") or TRUST_OK
        trust[key] = trust.get(key, 0) + 1
    covered = sum(sizes)
    # Chaining's tell is that the biggest cluster outgrows the catalog. Both ratios are dimensionless,
    # so they stay comparable as the corpus grows — which raw counts do not.
    p90 = sorted(sizes)[int(0.9 * (len(sizes) - 1))] if sizes else 0
    return {
        "storyCount": len(stories),
        "avgArticlesPerStory": round(sum(sizes) / len(sizes), 2) if sizes else 0.0,
        "largestStory": sizes[0] if sizes else 0,
        "clusterBuildMs": cluster_ms,
        "sizeDistribution": {str(k): v for k, v in sorted(dist.items())},
        "clusterTrust": trust,
        "blindspotsWithheld": sum(1 for s in stories if s.get("blindspotWithheld")),
        # Launch monitors — see docs/CLUSTER_TRUST.md for the agreed trigger levels.
        "largestOverP90": round(sizes[0] / p90, 1) if p90 else 0.0,
        "largestShareOfCovered": round(sizes[0] / covered, 4) if covered else 0.0,
    }


def diagnostics(store_, *, max_scan: int = None) -> dict:
    """Story-layer diagnostics for operators: counts, average + largest cluster, build time, and the
    cluster-size distribution."""
    t0 = _time.perf_counter()
    stories = cluster_from_store(store_, max_scan=max_scan)
    return _diagnostics(stories, round((_time.perf_counter() - t0) * 1000.0, 2))
