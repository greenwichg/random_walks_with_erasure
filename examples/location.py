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

Phase 2 (event geography) extends the same layer without a parallel path: an article ALSO has
0..n :class:`EventLocation` rows — where the reported EVENT happened, provider-extracted and
normalized by :func:`resolve_event_locations` through the same country tables. Precedence for
"where is this article about": event locations when a provider supplied them, else the
publisher's home. We never extract places from article text ourselves (no NLP here, ever) and
never guess — an article without provider event geography simply has no event rows.

Read-only over its inputs; no network, no NLP.
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

# Full ISO 3166-1 English name set — generated from the canonical assigned-code list
# (pycountry {name, common_name, official_name} + ASCII-folded and "&"/"St." variant spellings;
# deprecated/meta codes excluded at the source, so "Germany" can only ever be DE). The GKG
# enricher meets EVERY country's name in real records, so the hand-curated entries above can't
# be the ceiling. setdefault: curated spellings always win.
_COUNTRY_NAMES_FULL = {
    "afghanistan": "AF", "aland islands": "AX", "albania": "AL", "algeria": "DZ",
    "american samoa": "AS", "andorra": "AD", "angola": "AO", "anguilla": "AI", "antarctica": "AQ",
    "antigua and barbuda": "AG", "arab republic of egypt": "EG", "argentina": "AR",
    "argentine republic": "AR", "armenia": "AM", "aruba": "AW", "australia": "AU",
    "austria": "AT", "azerbaijan": "AZ", "bahamas": "BS", "bahrain": "BH", "bangladesh": "BD",
    "barbados": "BB", "belarus": "BY", "belgium": "BE", "belize": "BZ", "benin": "BJ",
    "bermuda": "BM", "bhutan": "BT", "bolivarian republic of venezuela": "VE", "bolivia": "BO",
    "bosnia and herzegovina": "BA", "botswana": "BW", "bouvet island": "BV", "brazil": "BR",
    "british indian ocean territory": "IO", "british virgin islands": "VG",
    "brunei darussalam": "BN", "bulgaria": "BG", "burkina faso": "BF", "burundi": "BI",
    "cabo verde": "CV", "cambodia": "KH", "cameroon": "CM", "canada": "CA",
    "cayman islands": "KY", "central african republic": "CF", "chad": "TD", "chile": "CL",
    "china": "CN", "christmas island": "CX", "cocos (keeling) islands": "CC", "colombia": "CO",
    "commonwealth of dominica": "DM", "commonwealth of the bahamas": "BS",
    "commonwealth of the northern mariana islands": "MP", "comoros": "KM", "congo": "CG",
    "cook islands": "CK", "costa rica": "CR", "cote d'ivoire": "CI", "croatia": "HR",
    "cuba": "CU", "curacao": "CW", "cura\u00e7ao": "CW", "cyprus": "CY", "czech republic": "CZ",
    "czechia": "CZ", "c\u00f4te d'ivoire": "CI", "democratic people's republic of korea": "KP",
    "democratic republic of sao tome and principe": "ST",
    "democratic republic of timor-leste": "TL",
    "democratic socialist republic of sri lanka": "LK", "denmark": "DK", "djibouti": "DJ",
    "dominica": "DM", "dominican republic": "DO", "eastern republic of uruguay": "UY",
    "ecuador": "EC", "egypt": "EG", "el salvador": "SV", "equatorial guinea": "GQ",
    "eritrea": "ER", "estonia": "EE", "eswatini": "SZ", "ethiopia": "ET",
    "falkland islands (malvinas)": "FK", "faroe islands": "FO",
    "federal democratic republic of ethiopia": "ET", "federal democratic republic of nepal": "NP",
    "federal republic of germany": "DE", "federal republic of nigeria": "NG",
    "federal republic of somalia": "SO", "federated states of micronesia": "FM",
    "federative republic of brazil": "BR", "fiji": "FJ", "finland": "FI", "france": "FR",
    "french guiana": "GF", "french polynesia": "PF", "french republic": "FR",
    "french southern territories": "TF", "gabon": "GA", "gabonese republic": "GA", "gambia": "GM",
    "georgia": "GE", "germany": "DE", "ghana": "GH", "gibraltar": "GI",
    "grand duchy of luxembourg": "LU", "greece": "GR", "greenland": "GL", "grenada": "GD",
    "guadeloupe": "GP", "guam": "GU", "guatemala": "GT", "guernsey": "GG", "guinea": "GN",
    "guinea-bissau": "GW", "guyana": "GY", "haiti": "HT", "hashemite kingdom of jordan": "JO",
    "heard island and mcdonald islands": "HM", "hellenic republic": "GR",
    "holy see (vatican city state)": "VA", "honduras": "HN", "hong kong": "HK",
    "hong kong special administrative region of china": "HK", "hungary": "HU", "iceland": "IS",
    "independent state of papua new guinea": "PG", "independent state of samoa": "WS",
    "india": "IN", "indonesia": "ID", "iran": "IR", "iraq": "IQ", "ireland": "IE",
    "islamic republic of afghanistan": "AF", "islamic republic of iran": "IR",
    "islamic republic of mauritania": "MR", "islamic republic of pakistan": "PK",
    "isle of man": "IM", "israel": "IL", "italian republic": "IT", "italy": "IT", "jamaica": "JM",
    "japan": "JP", "jersey": "JE", "jordan": "JO", "kazakhstan": "KZ", "kenya": "KE",
    "kingdom of bahrain": "BH", "kingdom of belgium": "BE", "kingdom of bhutan": "BT",
    "kingdom of cambodia": "KH", "kingdom of denmark": "DK", "kingdom of eswatini": "SZ",
    "kingdom of lesotho": "LS", "kingdom of morocco": "MA", "kingdom of norway": "NO",
    "kingdom of saudi arabia": "SA", "kingdom of spain": "ES", "kingdom of sweden": "SE",
    "kingdom of thailand": "TH", "kingdom of the netherlands": "NL", "kingdom of tonga": "TO",
    "kiribati": "KI", "kuwait": "KW", "kyrgyz republic": "KG", "kyrgyzstan": "KG",
    "lao people's democratic republic": "LA", "laos": "LA", "latvia": "LV",
    "lebanese republic": "LB", "lebanon": "LB", "lesotho": "LS", "liberia": "LR", "libya": "LY",
    "liechtenstein": "LI", "lithuania": "LT", "luxembourg": "LU", "macao": "MO",
    "macao special administrative region of china": "MO", "madagascar": "MG", "malawi": "MW",
    "malaysia": "MY", "maldives": "MV", "mali": "ML", "malta": "MT", "marshall islands": "MH",
    "martinique": "MQ", "mauritania": "MR", "mauritius": "MU", "mayotte": "YT", "mexico": "MX",
    "moldova": "MD", "monaco": "MC", "mongolia": "MN", "montenegro": "ME", "montserrat": "MS",
    "morocco": "MA", "mozambique": "MZ", "myanmar": "MM", "namibia": "NA", "nauru": "NR",
    "nepal": "NP", "netherlands": "NL", "new caledonia": "NC", "new zealand": "NZ",
    "nicaragua": "NI", "niger": "NE", "nigeria": "NG", "niue": "NU", "norfolk island": "NF",
    "north korea": "KP", "north macedonia": "MK", "northern mariana islands": "MP",
    "norway": "NO", "oman": "OM", "pakistan": "PK", "palau": "PW", "panama": "PA",
    "papua new guinea": "PG", "paraguay": "PY", "people's democratic republic of algeria": "DZ",
    "people's republic of bangladesh": "BD", "people's republic of china": "CN", "peru": "PE",
    "philippines": "PH", "pitcairn": "PN", "plurinational state of bolivia": "BO", "poland": "PL",
    "portugal": "PT", "portuguese republic": "PT", "principality of andorra": "AD",
    "principality of liechtenstein": "LI", "principality of monaco": "MC", "puerto rico": "PR",
    "qatar": "QA", "republic of albania": "AL", "republic of angola": "AO",
    "republic of armenia": "AM", "republic of austria": "AT", "republic of azerbaijan": "AZ",
    "republic of belarus": "BY", "republic of benin": "BJ",
    "republic of bosnia and herzegovina": "BA", "republic of botswana": "BW",
    "republic of bulgaria": "BG", "republic of burundi": "BI", "republic of cabo verde": "CV",
    "republic of cameroon": "CM", "republic of chad": "TD", "republic of chile": "CL",
    "republic of colombia": "CO", "republic of costa rica": "CR",
    "republic of cote d'ivoire": "CI", "republic of croatia": "HR", "republic of cuba": "CU",
    "republic of cyprus": "CY", "republic of c\u00f4te d'ivoire": "CI",
    "republic of djibouti": "DJ", "republic of ecuador": "EC", "republic of el salvador": "SV",
    "republic of equatorial guinea": "GQ", "republic of estonia": "EE", "republic of fiji": "FJ",
    "republic of finland": "FI", "republic of ghana": "GH", "republic of guatemala": "GT",
    "republic of guinea": "GN", "republic of guinea-bissau": "GW", "republic of guyana": "GY",
    "republic of haiti": "HT", "republic of honduras": "HN", "republic of iceland": "IS",
    "republic of india": "IN", "republic of indonesia": "ID", "republic of iraq": "IQ",
    "republic of kazakhstan": "KZ", "republic of kenya": "KE", "republic of kiribati": "KI",
    "republic of latvia": "LV", "republic of liberia": "LR", "republic of lithuania": "LT",
    "republic of madagascar": "MG", "republic of malawi": "MW", "republic of maldives": "MV",
    "republic of mali": "ML", "republic of malta": "MT", "republic of mauritius": "MU",
    "republic of moldova": "MD", "republic of mozambique": "MZ", "republic of myanmar": "MM",
    "republic of namibia": "NA", "republic of nauru": "NR", "republic of nicaragua": "NI",
    "republic of north macedonia": "MK", "republic of palau": "PW", "republic of panama": "PA",
    "republic of paraguay": "PY", "republic of peru": "PE", "republic of poland": "PL",
    "republic of san marino": "SM", "republic of senegal": "SN", "republic of serbia": "RS",
    "republic of seychelles": "SC", "republic of sierra leone": "SL",
    "republic of singapore": "SG", "republic of slovenia": "SI", "republic of south africa": "ZA",
    "republic of south sudan": "SS", "republic of suriname": "SR", "republic of tajikistan": "TJ",
    "republic of the congo": "CG", "republic of the gambia": "GM",
    "republic of the marshall islands": "MH", "republic of the niger": "NE",
    "republic of the philippines": "PH", "republic of the sudan": "SD",
    "republic of trinidad and tobago": "TT", "republic of tunisia": "TN",
    "republic of turkiye": "TR", "republic of t\u00fcrkiye": "TR", "republic of uganda": "UG",
    "republic of uzbekistan": "UZ", "republic of vanuatu": "VU", "republic of yemen": "YE",
    "republic of zambia": "ZM", "republic of zimbabwe": "ZW", "reunion": "RE", "romania": "RO",
    "russian federation": "RU", "rwanda": "RW", "rwandese republic": "RW", "r\u00e9union": "RE",
    "saint barthelemy": "BL", "saint barth\u00e9lemy": "BL", "saint kitts and nevis": "KN",
    "saint lucia": "LC", "saint martin (french part)": "MF", "saint pierre and miquelon": "PM",
    "saint vincent and the grenadines": "VC", "samoa": "WS", "san marino": "SM",
    "sao tome and principe": "ST", "saudi arabia": "SA", "senegal": "SN", "serbia": "RS",
    "seychelles": "SC", "sierra leone": "SL", "singapore": "SG",
    "sint maarten (dutch part)": "SX", "slovak republic": "SK", "slovakia": "SK",
    "slovenia": "SI", "socialist republic of viet nam": "VN", "solomon islands": "SB",
    "somalia": "SO", "south africa": "ZA", "south georgia and the south sandwich islands": "GS",
    "south korea": "KR", "south sudan": "SS", "spain": "ES", "sri lanka": "LK",
    "state of israel": "IL", "state of kuwait": "KW", "state of qatar": "QA", "sudan": "SD",
    "sultanate of oman": "OM", "suriname": "SR", "svalbard and jan mayen": "SJ", "sweden": "SE",
    "swiss confederation": "CH", "switzerland": "CH", "syria": "SY", "syrian arab republic": "SY",
    "taiwan": "TW", "tajikistan": "TJ", "tanzania": "TZ", "thailand": "TH",
    "the state of eritrea": "ER", "the state of palestine": "PS", "timor-leste": "TL",
    "togo": "TG", "togolese republic": "TG", "tokelau": "TK", "tonga": "TO",
    "trinidad and tobago": "TT", "tunisia": "TN", "turkiye": "TR", "turkmenistan": "TM",
    "turks and caicos islands": "TC", "tuvalu": "TV", "t\u00fcrkiye": "TR", "uganda": "UG",
    "ukraine": "UA", "union of the comoros": "KM", "united arab emirates": "AE",
    "united kingdom": "GB", "united kingdom of great britain and northern ireland": "GB",
    "united mexican states": "MX", "united republic of tanzania": "TZ", "united states": "US",
    "united states minor outlying islands": "UM", "united states of america": "US",
    "uruguay": "UY", "uzbekistan": "UZ", "vanuatu": "VU", "venezuela": "VE", "viet nam": "VN",
    "vietnam": "VN", "virgin islands of the united states": "VI", "wallis and futuna": "WF",
    "western sahara": "EH", "yemen": "YE", "zambia": "ZM", "zimbabwe": "ZW",
    "\u00e5land islands": "AX",
}
for _k, _v in _COUNTRY_NAMES_FULL.items():
    _COUNTRY_NAMES.setdefault(_k, _v)

# Legacy / provider spellings seen in the wild (GDELT GKG et al.) that ICU's current names miss —
# same fail-honest rule: only KNOWN alternate names, never fuzzy matching.
for _k, _v in {
    "kosovo": "XK",     # not ISO-assigned; XK is the de-facto code every provider uses
    "turkey": "TR", "burma": "MM", "ivory coast": "CI", "czech republic": "CZ",
    "macedonia": "MK", "swaziland": "SZ", "east timor": "TL", "cape verde": "CV",
    "democratic republic of the congo": "CD", "dr congo": "CD", "drc": "CD",
    "republic of the congo": "CG", "congo republic": "CG", "congo-brazzaville": "CG",
    "congo-kinshasa": "CD", "palestine": "PS", "palestinian territory": "PS",
    "gaza strip": "PS", "west bank": "PS", "vatican": "VA", "vatican city": "VA",
    "the gambia": "GM", "the bahamas": "BS", "south korea": "KR", "north korea": "KP",
    "russian federation": "RU", "uae": "AE", "bosnia": "BA", "kyrgyz republic": "KG",
    "slovak republic": "SK",
}.items():
    _COUNTRY_NAMES.setdefault(_k, _v)

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


#: Code -> display name, INVERTED from the name maps above rather than typed out a second time.
#:
#: The maps are many-to-one (``republic of south africa`` and ``south africa`` both give ``ZA``), so
#: the shortest spelling wins — which is the everyday name in every case tested: ZA -> South Africa,
#: GB -> United Kingdom, DE -> Germany, GR -> Greece. A second hand-written table would be a
#: duplicate definition of a fact this module already holds, and this repository has had to correct
#: four of those.
def _invert(mapping: dict) -> dict:
    out: dict = {}
    for name, code in mapping.items():
        prev = out.get(code)
        if prev is None or (len(name), name) < (len(prev), prev):
            out[code] = name
    return out


_CODE_TO_COUNTRY = {**_invert(_COUNTRY_NAMES_FULL), **{}}
for _code, _name in _invert(_COUNTRY_NAMES).items():
    _CODE_TO_COUNTRY.setdefault(_code, _name)
_CODE_TO_LANGUAGE = _invert(_LANGUAGE_NAMES)


def country_name(code: "str | None") -> str:
    """``"ZA"`` -> ``"South Africa"``, or ``""`` when the code is unknown.

    Title-cased, because the caller is building a phrase a person would type into a search box and
    ``south africa`` is not that. Empty rather than the bare code for an unknown one: a query reading
    "local news websites in XK" is worse than no query, and the caller can then skip it."""
    name = _CODE_TO_COUNTRY.get((code or "").strip().upper())
    return name.title() if name else ""


def language_name(code: "str | None") -> str:
    """``"el"`` -> ``"Greek"``, or ``""`` when unknown. Same reasoning as :func:`country_name`."""
    name = _CODE_TO_LANGUAGE.get((code or "").strip().lower())
    return name.title() if name else ""


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


@dataclass(frozen=True)
class EventLocation:
    """Where an article's EVENT happened (Phase 2) — one provider-extracted place, normalized.

    Distinct dimension from :class:`ResolvedLocation` (the publisher's home): an article has ONE
    publisher location and 0..n event locations. ``source`` is provider provenance ("gdelt-gkg",
    "georss", …) — stored so every located fact stays auditable, like the registry discipline."""
    country: str                     # ISO 3166-1 alpha-2, upper (required — v1 is country-level)
    region: Optional[str] = None
    city: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    source: str = "provider"


def resolve_event_locations(raw_locations) -> "tuple[EventLocation, ...]":
    """Normalize provider-supplied EVENT places into canonical :class:`EventLocation` rows.

    Input: an iterable of mappings, each with ``country`` in whatever form the provider uses
    (ISO2/ISO3/full name — the same :func:`normalize_country` forms) plus optional
    ``region``/``city``/``lat``/``lon``/``source``. Fail-honest like everything here: an entry
    whose country can't be normalized is DROPPED (never guessed), duplicates collapse, and this
    function never looks at article text — extraction belongs to providers, normalization to us.
    """
    out: list[EventLocation] = []
    seen: set = set()
    for item in raw_locations or ():
        if not isinstance(item, dict):
            continue
        country = normalize_country(item.get("country"))
        if country is None:
            continue
        region = (str(item["region"]).strip() or None) if item.get("region") else None
        city = (str(item["city"]).strip() or None) if item.get("city") else None
        key = (country, region, city)
        if key in seen:
            continue
        seen.add(key)
        try:
            lat = float(item["lat"]) if item.get("lat") is not None else None
            lon = float(item["lon"]) if item.get("lon") is not None else None
        except (TypeError, ValueError):
            lat = lon = None
        out.append(EventLocation(country=country, region=region, city=city, lat=lat, lon=lon,
                                 source=str(item.get("source") or "provider")))
    return tuple(out)


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
