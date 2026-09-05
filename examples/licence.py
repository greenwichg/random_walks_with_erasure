"""licence.py — which terms govern a catalogue row, derived from how we ACQUIRED it.

The vocabulary (``store.LICENCE_CLASSES``, most restrictive first) and the one rule that decides
a row's class: it is the most PERMISSIVE class among the channels the row was observed through.
An article we hold from the publisher's own machine-readable offer (RSS, sitemap crawl) is ours
to describe even if a keyed provider delivered it first; an article we hold only through a
provider API is governed by that provider's terms; an article that exists in the catalogue only
because one reader's browser reported it is that reader's, and never leaves.

    reader_private       extension-born, uncorroborated — never served outside the reader's own account
    unknown              no channel recorded (legacy rows) — treated as restricted until re-observed
    provider_restricted  held only via a keyed provider (NewsAPI, Guardian, NewsData, GNews,
                         MediaStack, Currents) or an aggregator feed (Google News RSS)
    metadata_public      URL / headline / time / publisher observed from the publisher's own offer
                         (RSS, crawl) or an open dataset with attribution (GDELT)

Classes are DATA: a row stores its class, an export or an API plan filters on it with a WHERE, and
a licensing decision changes a mapping here plus a backfill — never a code path. The channel sets
are overridable (``RWE_LICENCE_PUBLIC_CHANNELS`` / ``RWE_LICENCE_PROVIDER_CHANNELS``,
comma-separated) so a signed provider agreement can move a channel without a deploy.

Design: docs/NEWS_INTELLIGENCE_INFRASTRUCTURE.md §I.2. Enforcement: ``platform_api.shape``.
"""

from __future__ import annotations

import os

from store import LICENCE_CLASSES, merge_licence_class

READER_PRIVATE = "reader_private"
UNKNOWN = "unknown"
PROVIDER_RESTRICTED = "provider_restricted"
METADATA_PUBLIC = "metadata_public"

#: Channels whose content we hold under the publisher's own machine-readable offer, or under an
#: open licence that permits redistribution with attribution.
PUBLIC_CHANNELS = frozenset({"rss", "crawl", "gdelt"})
#: Channels governed by a third party's terms. Google News RSS is here deliberately: the feed is
#: an aggregator's delivery of publisher headlines, and until a row is re-observed from the
#: publisher itself we hold it on the aggregator's terms, not the publisher's.
PROVIDER_CHANNELS = frozenset({"newsapi", "guardian", "newsdata", "gnews", "mediastack",
                               "currents", "googlenews"})
READER_CHANNELS = frozenset({"extension"})

#: Attribution a served row must carry for the channel it came from (``meta.licence.attribution``).
ATTRIBUTION = {"gdelt": "GDELT Project (gdeltproject.org)"}

#: Article fields a plan without a row's class does not receive. The identity, the publisher, the
#: time and the story membership are ours (derived or observed); the headline, the snippet, the
#: link and the image are the provider's delivery.
RESTRICTED_FIELDS = ("headline", "description", "url", "image", "imageWidth", "imageHeight",
                     "imageMimeType", "imageSource", "imageAttribution", "imageSuspect")


def _env_set(name: str) -> frozenset:
    raw = os.environ.get(name, "")
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def public_channels() -> frozenset:
    return PUBLIC_CHANNELS | _env_set("RWE_LICENCE_PUBLIC_CHANNELS")


def provider_channels() -> frozenset:
    return (PROVIDER_CHANNELS | _env_set("RWE_LICENCE_PROVIDER_CHANNELS")) - _env_set(
        "RWE_LICENCE_PUBLIC_CHANNELS")


def class_for_channel(channel: "str | None") -> str:
    """The licence class ONE observation through ``channel`` establishes."""
    ch = (channel or "").strip().lower()
    if not ch:
        return UNKNOWN
    if ch in READER_CHANNELS:
        return READER_PRIVATE
    if ch in public_channels():
        return METADATA_PUBLIC
    if ch in provider_channels():
        return PROVIDER_RESTRICTED
    return UNKNOWN


def merge(*classes: "str | None") -> "str | None":
    """The most permissive of several classes (``None`` = absent)."""
    out = None
    for c in classes:
        out = merge_licence_class(out, c)
    return out


def class_for_channels(channels) -> str:
    """The class a set of observations establishes together — the backfill's rule."""
    return merge(*(class_for_channel(c) for c in channels)) or UNKNOWN


def attribution_for(channels) -> list:
    """Attribution lines owed for the channels a row was observed through, deduplicated."""
    out: list = []
    for ch in channels or ():
        line = ATTRIBUTION.get((ch or "").strip().lower())
        if line and line not in out:
            out.append(line)
    return out


def servable(licence_class: "str | None", allowed) -> bool:
    """Whether a row of this class may appear at all for a caller allowed ``allowed`` classes.
    ``reader_private`` is never servable, whatever the plan says — it is not ours to license."""
    cls = licence_class or UNKNOWN
    if cls == READER_PRIVATE:
        return False
    return cls in set(allowed or ())


def withheld_fields(licence_class: "str | None", allowed) -> tuple:
    """The article fields to withhold for a caller allowed ``allowed`` classes: nothing when the
    row's class is in the set, the provider's delivery otherwise."""
    cls = licence_class or UNKNOWN
    if cls in set(allowed or ()):
        return ()
    return RESTRICTED_FIELDS


__all__ = ["LICENCE_CLASSES", "READER_PRIVATE", "UNKNOWN", "PROVIDER_RESTRICTED",
           "METADATA_PUBLIC", "PUBLIC_CHANNELS", "PROVIDER_CHANNELS", "READER_CHANNELS",
           "ATTRIBUTION", "RESTRICTED_FIELDS", "class_for_channel", "class_for_channels",
           "merge", "attribution_for", "servable", "withheld_fields"]
