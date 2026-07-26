"""Location Intelligence Platform — the provider-agnostic location layer (Phase 0 + 1).

Every ingestion source already normalizes into :class:`rss_ingest.FeedEntry`; this module is the
matching **Location Resolver**: it turns whatever publisher-level location metadata a provider
supplied (``FeedEntry.country`` / ``FeedEntry.language``, in whatever form that provider uses)
into ONE canonical model, so persistence, search, stories, Information Health and the API never
see provider-specific values.

    Provider adapter -> FeedEntry(country=?, language=?) -> resolve_article_location() -> store

Canonical model (deliberately tiny — publisher-level only, per the Phase-1 scope):
  * ``country``  — ISO 3166-1 alpha-2, upper-case ("US"), or None when unresolvable.
  * ``language`` — ISO 639-1, lower-case ("en"), or None.

Provider forms handled WITHOUT provider branching: ISO2/ISO3 codes, BCP-47 tags ("en-US"), and
the human-readable names GDELT's DOC API returns ("United States", "English"). A future provider
that emits any of those forms needs NO resolver change; one that emits something new adds entries
to the two mapping tables below — nothing downstream moves (see docs/LOCATION_PLATFORM.md).

Precedence: the **publisher locality registry** (data/outlet_registry.csv) outranks provider
metadata — the registry states where an outlet is from as a curated fact; a provider's
``sourcecountry`` is an inference about the domain. Fail-honest: anything unresolvable stays
``None`` (never a guessed country), matching the registry's NaN-lean discipline.

Also here (Information Health readiness, not yet surfaced): :func:`reader_geography` — the
counted facts a future Geographic Diversity metric needs (countries read, local-vs-national
exposure), derived from the reader's stored reads joined to the located catalog.

Read-only over its inputs; no network, no NLP, no coordinates (explicitly out of Phase-1 scope).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import outlet_registry  # noqa: E402  — the publisher locality registry (product layer)

# --------------------------------------------------------------------------- #
# Canonicalisation tables. Names cover what live providers actually emit (GDELT DOC returns full
# English names); codes pass through. Additive by design — extending a table IS the integration
# work for a provider with new forms.
# --------------------------------------------------------------------------- #
_COUNTRY_NAMES = {
    "united states": "US", "united states of america": "US", "usa": "US",
    "united kingdom": "GB", "great britain": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "canada": "CA", "australia": "AU", "new zealand": "NZ", "ireland": "IE",
    "germany": "DE", "france": "FR", "spain": "ES", "portugal": "PT", "italy": "IT",
    "netherlands": "NL", "belgium": "BE", "switzerland": "CH", "austria": "AT",
    "sweden": "SE", "norway": "NO", "denmark": "DK", "finland": "FI", "iceland": "IS",
    "poland": "PL", "czech republic": "CZ", "czechia": "CZ", "ukraine": "UA", "russia": "RU",
    "india": "IN", "pakistan": "PK", "bangladesh": "BD", "sri lanka": "LK", "nepal": "NP",
    "china": "CN", "hong kong": "HK", "taiwan": "TW", "japan": "JP", "south korea": "KR",
    "north korea": "KP", "singapore": "SG", "malaysia": "MY", "indonesia": "ID",
    "philippines": "PH", "thailand": "TH", "vietnam": "VN",
    "israel": "IL", "palestine": "PS", "iran": "IR", "iraq": "IQ", "saudi arabia": "SA",
    "united arab emirates": "AE", "qatar": "QA", "turkey": "TR", "türkiye": "TR",
    "egypt": "EG", "south africa": "ZA", "nigeria": "NG", "kenya": "KE", "ghana": "GH",
    "ethiopia": "ET", "morocco": "MA", "tunisia": "TN", "algeria": "DZ",
    "mexico": "MX", "brazil": "BR", "argentina": "AR", "chile": "CL", "colombia": "CO",
    "peru": "PE", "venezuela": "VE", "cuba": "CU", "greece": "GR", "hungary": "HU",
    "romania": "RO", "bulgaria": "BG", "serbia": "RS", "croatia": "HR", "slovakia": "SK",
    "slovenia": "SI", "estonia": "EE", "latvia": "LV", "lithuania": "LT",
}
# Common ISO 3166-1 alpha-3 -> alpha-2 (providers that emit alpha-3).
_ISO3 = {
    "usa": "US", "gbr": "GB", "can": "CA", "aus": "AU", "nzl": "NZ", "irl": "IE", "deu": "DE",
    "fra": "FR", "esp": "ES", "prt": "PT", "ita": "IT", "nld": "NL", "che": "CH", "ind": "IN",
    "chn": "CN", "jpn": "JP", "kor": "KR", "bra": "BR", "mex": "MX", "rus": "RU", "ukr": "UA",
    "isr": "IL", "zaf": "ZA", "nga": "NG", "ken": "KE", "egy": "EG", "tur": "TR", "sau": "SA",
}
_LANGUAGE_NAMES = {
    "english": "en", "spanish": "es", "french": "fr", "german": "de", "portuguese": "pt",
    "italian": "it", "dutch": "nl", "russian": "ru", "ukrainian": "uk", "polish": "pl",
    "arabic": "ar", "hebrew": "he", "turkish": "tr", "persian": "fa", "farsi": "fa",
    "hindi": "hi", "urdu": "ur", "bengali": "bn", "tamil": "ta", "chinese": "zh",
    "mandarin": "zh", "japanese": "ja", "korean": "ko", "indonesian": "id", "malay": "ms",
    "thai": "th", "vietnamese": "vi", "swedish": "sv", "norwegian": "no", "danish": "da",
    "finnish": "fi", "greek": "el", "czech": "cs", "hungarian": "hu", "romanian": "ro",
}

#: Publisher scope vocabulary (registry `scope` column) — closed set, broad -> narrow.
SCOPES = ("international", "national", "regional", "local", "hyperlocal")


def normalize_country(raw: "str | None") -> Optional[str]:
    """Any provider country form -> ISO 3166-1 alpha-2 (upper), or None. Never guesses."""
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip()
    low = s.lower()
    if low in _COUNTRY_NAMES:
        return _COUNTRY_NAMES[low]
    if len(s) == 2 and s.isalpha():
        return s.upper()
    if len(s) == 3 and s.isalpha() and low in _ISO3:
        return _ISO3[low]
    return None


def normalize_language(raw: "str | None") -> Optional[str]:
    """Any provider language form (name / ISO code / BCP-47 tag) -> ISO 639-1 (lower), or None."""
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip().lower()
    if s in _LANGUAGE_NAMES:
        return _LANGUAGE_NAMES[s]
    primary = s.split("-")[0].split("_")[0]
    if len(primary) == 2 and primary.isalpha():
        return primary
    if primary in _LANGUAGE_NAMES:
        return _LANGUAGE_NAMES[primary]
    return None


@dataclass(frozen=True)
class ResolvedLocation:
    """The canonical publisher-level location for one article."""
    country: Optional[str] = None    # ISO 3166-1 alpha-2
    language: Optional[str] = None   # ISO 639-1


def resolve_article_location(entry_country: "str | None", entry_language: "str | None",
                             *, outlet: "str | None" = None,
                             registry: "outlet_registry.OutletRegistry | None" = None,
                             ) -> ResolvedLocation:
    """The Location Resolver: provider metadata (+ the resolved outlet) -> canonical location.

    Precedence for ``country``: the locality registry's curated home country for ``outlet``
    (when the registry knows one) beats the provider's value; the provider fills the gap for
    outlets the registry doesn't know — which is exactly the GDELT long tail.
    """
    reg = registry if registry is not None else outlet_registry.default_registry()
    country = None
    if outlet:
        try:
            resolved = reg.resolve(outlet)
        except Exception:
            resolved = None
        if resolved is not None and getattr(resolved, "country", None):
            country = normalize_country(resolved.country)
    if country is None:
        country = normalize_country(entry_country)
    return ResolvedLocation(country=country, language=normalize_language(entry_language))


# --------------------------------------------------------------------------- #
# Information Health readiness — Geographic Diversity inputs (counted, not scored).
# --------------------------------------------------------------------------- #
def reader_geography(store_, user_id: int,
                     registry: "outlet_registry.OutletRegistry | None" = None) -> dict:
    """Counted geographic facts about a reader's stored reads — the inputs a future Geographic
    Diversity metric will consume. Facts only (no 0–100 score here): per-country read counts,
    per-language counts, and local-vs-national exposure via the registry's publisher scope.

    ``unknown`` buckets are explicit: a read whose article the located catalog doesn't know (or
    whose outlet has no registry scope) is counted as unknown, never redistributed.
    """
    reg = registry if registry is not None else outlet_registry.default_registry()
    reads = store_.list_reads(user_id)
    urls = []
    for r in reads:
        u = (r.get("canonicalUrl") or r.get("canonical_url")
             or (r.get("scored") or {}).get("article_id") or r.get("url"))
        if u:
            urls.append(u)
    located = store_.feed_article_locations(urls) if urls else {}

    countries: dict = {}
    languages: dict = {}
    scope_counts = {"international": 0, "national": 0, "regional": 0,
                    "local": 0, "hyperlocal": 0, "unknown": 0}
    located_n = 0
    for u in urls:
        row = located.get(u)
        if not row:
            scope_counts["unknown"] += 1
            continue
        located_n += 1
        c, lang = row.get("country"), row.get("language")
        if c:
            countries[c] = countries.get(c, 0) + 1
        if lang:
            languages[lang] = languages.get(lang, 0) + 1
        outlet = reg.resolve(row.get("publisher")) if row.get("publisher") else None
        scope = getattr(outlet, "scope", None)
        scope_counts[scope if scope in SCOPES else "unknown"] += 1

    return {"reads": len(urls), "located": located_n,
            "countries": countries, "languages": languages, "scope": scope_counts}
