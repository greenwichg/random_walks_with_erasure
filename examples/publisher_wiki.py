"""publisher_wiki.py — Wikipedia + Wikidata lookup for publisher metadata.

Supplies the facts the curated :mod:`outlet_registry` does not carry (logo, description, founding
year, headquarters, parent organisation, Wikipedia link) so the Publisher page stops rendering
half-empty for outlets nobody has hand-curated. It is a **fallback**: the registry is authoritative
for everything it knows, and the merge in :mod:`publisher_metadata` only ever fills gaps.

Two providers, and the distinction is kept because they answer different questions:

    wikipedia   the ARTICLE — prose description, the page's own lead image, the human-readable link
    wikimedia   WIKIDATA CLAIMS + Commons — structured facts (inception, HQ, country, website,
                parent, the logo file), machine-readable and language-independent

Design constraints that shaped this module:

* **Network access is injected.** Every entry point takes ``fetch_json``; nothing here opens a
  socket. Tests exercise real API response shapes as fixtures and never touch the network, and the
  caller supplies the shared retry/backoff discipline from ``sources._get_json``.
* **A wrong match is worse than no match.** Publisher pages carry lean ratings and coverage claims;
  attaching the wrong organisation's founding year and parent company to one would be a factual
  error presented with the same confidence as a counted one. So a candidate is only accepted when
  it can be VERIFIED (see :func:`verify`), and everything else is recorded as ``ambiguous`` for a
  human rather than guessed at.
* **Wikimedia requires a descriptive User-Agent** with contact details; requests without one are
  refused (HTTP 403). :data:`USER_AGENT` is not decoration.
"""

from __future__ import annotations

import os
import re
from typing import Callable, Optional
from urllib.parse import quote, urlsplit

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_FILEPATH = "https://commons.wikimedia.org/wiki/Special:FilePath/"

#: Wikimedia's User-Agent policy: bots and tools must identify themselves with a contact address,
#: or the API answers 403. Override the contact with RWE_WIKI_CONTACT in deployments.
_CONTACT = os.environ.get("RWE_WIKI_CONTACT", "https://hidden-view.com").strip()
USER_AGENT = f"HiddenView-PublisherEnrichment/1.0 ({_CONTACT})"

# Wikidata property ids. Named because "P571" at a call site is unreadable.
P_INCEPTION = "P571"
P_HEADQUARTERS = "P159"
P_COUNTRY = "P17"
P_WEBSITE = "P856"
P_PARENT = "P749"
P_LOGO = "P154"
P_ISO_3166 = "P297"     # on a COUNTRY item: its alpha-2 code

#: Description length. Long enough to be a real summary, short enough that the page stays a profile.
MAX_DESCRIPTION = 400

_WS = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# Small pure helpers.
# --------------------------------------------------------------------------- #
def _clean(text) -> Optional[str]:
    s = _WS.sub(" ", str(text or "")).strip()
    return s or None


def registrable_domain(url_or_host) -> Optional[str]:
    """The comparable part of a host: ``www.bbc.co.uk`` and ``https://bbc.co.uk/news`` both give
    ``bbc.co.uk``.

    Deliberately naive about public suffixes — it strips a leading ``www.`` and nothing else. A real
    PSL would be more correct, but it is a dependency, and the only job here is comparing two hosts
    that both describe the same outlet. Over-keeping a subdomain makes a match FAIL (safe: recorded
    ambiguous), never falsely SUCCEED."""
    s = str(url_or_host or "").strip()
    if not s:
        return None
    host = urlsplit(s if "//" in s else f"//{s}").hostname or ""
    host = host.lower().strip(".")
    return host[4:] if host.startswith("www.") else host or None


def truncate_description(text, limit: int = MAX_DESCRIPTION) -> Optional[str]:
    """Trim an article intro to ``limit`` characters, preferring a sentence boundary so the page
    never shows a summary cut mid-word."""
    s = _clean(text)
    if not s or len(s) <= limit:
        return s
    window = s[:limit]
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if cut > limit // 2:
        return window[:cut + 1].strip()
    space = window.rfind(" ")
    return (window[:space] if space > 0 else window).rstrip(" ,;:") + "…"


def commons_image_url(filename, width: int = 320) -> Optional[str]:
    """A stable URL for a Commons file. ``Special:FilePath`` redirects to the current thumbnail, so
    this keeps working when a file is re-uploaded — unlike a hashed upload.wikimedia.org path."""
    name = _clean(filename)
    if not name:
        return None
    return f"{COMMONS_FILEPATH}{quote(name.replace(' ', '_'))}?width={int(width)}"


def _year(time_value) -> Optional[str]:
    """Wikidata times look like ``+1922-10-18T00:00:00Z`` (and BCE dates start ``-``). Only the year
    is shown, because that is the only part consistently precise across items."""
    s = str(time_value or "").strip()
    m = re.match(r"^([+-])(\d{1,5})-", s)
    if not m:
        return None
    year = int(m.group(2))
    if year == 0:
        return None
    return str(year) if m.group(1) == "+" else f"{year} BC"


# --------------------------------------------------------------------------- #
# Claim extraction.
# --------------------------------------------------------------------------- #
def _claims(entity: dict, prop: str) -> list:
    out = []
    for claim in (entity.get("claims") or {}).get(prop) or []:
        # Deprecated claims are the ones Wikidata itself marks as wrong-but-recorded. Skipping them
        # is the difference between "founded 1922" and a superseded value nobody believes.
        if claim.get("rank") == "deprecated":
            continue
        snak = claim.get("mainsnak") or {}
        if snak.get("snaktype") != "value":
            continue          # "no value" / "unknown value" are assertions of ABSENCE, not values
        out.append((snak.get("datavalue") or {}).get("value"))
    return out


def _first_claim(entity: dict, prop: str):
    vals = _claims(entity, prop)
    return vals[0] if vals else None


def entity_ids(entity: dict, props) -> list:
    """The referenced item ids (``Q…``) a set of properties points at — collected so their labels
    can be resolved in ONE batched request instead of one per property."""
    ids = []
    for prop in props:
        for v in _claims(entity, prop):
            if isinstance(v, dict) and v.get("entity-type") == "item" and v.get("id"):
                ids.append(v["id"])
    return ids


def _label(entity: dict, lang: str = "en") -> Optional[str]:
    labels = entity.get("labels") or {}
    entry = labels.get(lang) or {}
    return _clean(entry.get("value"))


def _referenced_label(entity: dict, prop: str, resolved: dict) -> Optional[str]:
    v = _first_claim(entity, prop)
    if not isinstance(v, dict) or not v.get("id"):
        return None
    return _label(resolved.get(v["id"]) or {})


def parse_entity(entity: dict, resolved: "dict | None" = None) -> dict:
    """Wikidata entity -> the flat fact set the profile shows. ``resolved`` maps referenced item
    ids to their entities (for HQ / country / parent labels); without it those come back None
    rather than as raw Q-ids, because "Q9531" on a publisher page is worse than nothing."""
    resolved = resolved or {}
    website = _first_claim(entity, P_WEBSITE)
    country_id = _first_claim(entity, P_COUNTRY)
    country_entity = resolved.get(country_id["id"]) if isinstance(country_id, dict) else None
    iso = _first_claim(country_entity or {}, P_ISO_3166)
    inception = _first_claim(entity, P_INCEPTION)
    return {
        "founded": _year((inception or {}).get("time") if isinstance(inception, dict) else None),
        "headquarters": _referenced_label(entity, P_HEADQUARTERS, resolved),
        # An ISO alpha-2 is what every other country field in this product speaks (the location
        # platform, the story consensus, the registry), so a country only counts when it resolves
        # to one. A bare label would not join with anything.
        "country": (_clean(iso) or "").upper()[:2] or None,
        "website": _clean(website) if isinstance(website, str) else None,
        "parent": _referenced_label(entity, P_PARENT, resolved),
        "logo": commons_image_url(_first_claim(entity, P_LOGO)),
    }


# --------------------------------------------------------------------------- #
# Verification — the guard that keeps a wrong page off a publisher's profile.
# --------------------------------------------------------------------------- #
#: Claims that only an ORGANISATION carries. Their presence is what separates the newspaper "Mirror"
#: from the article about reflective surfaces — a title match alone cannot, and a direct Wikipedia
#: title hit cannot either, because the common-noun article is exactly what a common-noun masthead
#: hits first.
_ORG_FACTS = ("website", "founded", "headquarters", "parent")


def verify(*, publisher: str, page_title: str, facts: dict, observed_host=None) -> "tuple[bool, str]":
    """Is this Wikipedia page really THIS publisher? Returns ``(accepted, reason)``.

    The evidence, strongest first:

    1. **Domain agreement** — Wikidata's official-website host matches the host we actually observe
       this publisher publishing from. Decisive, and it is why enrichment passes the catalog's
       counted host in: it is independent evidence, not another name string.
    2. **Domain conflict** — both hosts known and different. Rejected outright. This is the case
       that would otherwise put Fox Corporation's facts on a "Fox Sports" page.
    3. **No domain to compare** — the title must match the publisher's name AND the item must carry
       at least one organisational claim. Both halves are load-bearing: the title match alone lets
       a common-noun masthead ("Mirror", "The Sun", "Metro") bind to the everyday-object article,
       and the org claim alone lets any organisation with a similar name bind.

    Anything else is ``unverified`` — recorded as ambiguous for a human, never rendered."""
    site_domain = registrable_domain(facts.get("website"))
    observed = registrable_domain(observed_host)
    if site_domain and observed:
        if site_domain == observed or site_domain.endswith("." + observed) \
                or observed.endswith("." + site_domain):
            return True, "domain"
        return False, "domain_conflict"
    if _name_key(page_title) != _name_key(publisher):
        return False, "unverified"
    if not any(facts.get(f) for f in _ORG_FACTS):
        return False, "not_an_organisation"
    return True, "title"


def _name_key(text) -> str:
    """Loose name comparison for the title check: case- and punctuation-insensitive, and blind to a
    leading "The" so "The Guardian" matches "Guardian"."""
    s = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()
    s = re.sub(r"^the\s+", "", s)
    return _WS.sub(" ", s)


# --------------------------------------------------------------------------- #
# API calls (network injected).
# --------------------------------------------------------------------------- #
def _api(base: str, params: dict) -> str:
    query = "&".join(f"{k}={quote(str(v), safe='|')}" for k, v in params.items())
    return f"{base}?{query}"


def fetch_page(title: str, fetch_json: Callable[[str], dict]) -> Optional[dict]:
    """Look a title up on Wikipedia. Returns ``{title, url, wikidataId, extract, pageImage,
    disambiguation, missing}`` or None when the API answers with nothing usable.

    ``redirects=1`` matters: half of all publisher names are redirects to the canonical article
    ("NYT" -> "The New York Times"), and without it every one of those looks like a miss."""
    url = _api(WIKIPEDIA_API, {
        "action": "query", "format": "json", "formatversion": "2", "redirects": "1",
        "prop": "pageprops|extracts|pageimages", "exintro": "1", "explaintext": "1",
        "piprop": "original", "titles": title,
    })
    data = fetch_json(url) or {}
    pages = ((data.get("query") or {}).get("pages")) or []
    if not pages:
        return None
    page = pages[0]
    if page.get("missing"):
        return {"missing": True, "title": page.get("title") or title}
    props = page.get("pageprops") or {}
    page_title = page.get("title") or title
    return {
        "missing": False,
        "title": page_title,
        "url": f"https://en.wikipedia.org/wiki/{quote((page_title).replace(' ', '_'))}",
        "wikidataId": _clean(props.get("wikibase_item")),
        "extract": truncate_description(page.get("extract")),
        "pageImage": _clean(((page.get("original") or {}).get("source"))),
        # A disambiguation page is a LIST of candidates, not an outlet. Parsing one would attach
        # whichever organisation happened to be described first.
        "disambiguation": "disambiguation" in props,
    }


def search_titles(name: str, fetch_json: Callable[[str], dict], *, limit: int = 3) -> list:
    """Wikipedia full-text search, for when the name is not itself an article title."""
    url = _api(WIKIPEDIA_API, {
        "action": "query", "format": "json", "formatversion": "2",
        "list": "search", "srsearch": name, "srlimit": int(limit),
    })
    data = fetch_json(url) or {}
    return [r["title"] for r in ((data.get("query") or {}).get("search") or []) if r.get("title")]


def fetch_entities(ids, fetch_json: Callable[[str], dict], *, props: str = "claims|labels") -> dict:
    """Batched Wikidata entity fetch -> ``{id: entity}``. Batching is the whole point: a publisher
    needs its own item plus up to three referenced items (HQ, country, parent), and that is two
    requests total instead of four."""
    wanted = [i for i in dict.fromkeys(ids) if i]
    if not wanted:
        return {}
    url = _api(WIKIDATA_API, {
        "action": "wbgetentities", "format": "json", "props": props,
        "languages": "en", "ids": "|".join(wanted[:50]),   # API caps a batch at 50
    })
    data = fetch_json(url) or {}
    return {k: v for k, v in (data.get("entities") or {}).items() if isinstance(v, dict)}


# --------------------------------------------------------------------------- #
# The lookup, end to end.
# --------------------------------------------------------------------------- #
def lookup(publisher: str, fetch_json: Callable[[str], dict], *, observed_host=None,
           search: bool = True) -> dict:
    """Resolve one publisher to a verified fact set.

    Returns ``{"status": ..., ...}`` where status is ``ok`` / ``no_match`` / ``ambiguous``. It never
    raises for a *lookup* outcome — only a genuine transport failure propagates, so the caller can
    tell "Wikipedia says no" (cache it) from "the network failed" (retry sooner).

    Costs 2 requests for a direct title hit, 3 when referenced labels are needed, +1 if a search is
    required first. Budgeted per cycle by the caller."""
    name = (publisher or "").strip()
    if not name:
        return {"status": "no_match", "reason": "empty_name"}

    page = fetch_page(name, fetch_json)
    tried = [name]
    if (page is None or page.get("missing") or page.get("disambiguation")) and search:
        for title in search_titles(name, fetch_json):
            if title in tried:
                continue
            tried.append(title)
            candidate = fetch_page(title, fetch_json)
            if candidate and not candidate.get("missing") and not candidate.get("disambiguation"):
                page = candidate
                break
    if page is None or page.get("missing"):
        return {"status": "no_match", "reason": "no_page"}
    if page.get("disambiguation"):
        return {"status": "ambiguous", "reason": "disambiguation",
                "wikipediaTitle": page.get("title"), "wikipediaUrl": page.get("url")}

    facts = {"founded": None, "headquarters": None, "country": None, "website": None,
             "parent": None, "logo": None}
    qid = page.get("wikidataId")
    if qid:
        entities = fetch_entities([qid], fetch_json)
        entity = entities.get(qid) or {}
        refs = entity_ids(entity, (P_HEADQUARTERS, P_COUNTRY, P_PARENT))
        resolved = fetch_entities(refs, fetch_json) if refs else {}
        facts = parse_entity(entity, resolved)

    accepted, reason = verify(publisher=name, page_title=page["title"], facts=facts,
                              observed_host=observed_host)
    if not accepted:
        return {"status": "ambiguous", "reason": reason,
                "wikipediaTitle": page.get("title"), "wikipediaUrl": page.get("url"),
                "wikidataId": qid}

    # Logo: the Commons file a claim names is the outlet's ACTUAL logo; the article's lead image is
    # a fallback that is often a headquarters photo, so it is used only when no logo claim exists.
    logo, logo_source = facts.get("logo"), "wikimedia"
    if not logo and page.get("pageImage"):
        logo, logo_source = page["pageImage"], "wikipedia"
    if not logo:
        logo_source = None

    return {
        "status": "ok", "reason": reason, "source": "wikipedia",
        "wikidataId": qid, "wikipediaTitle": page.get("title"), "wikipediaUrl": page.get("url"),
        "description": page.get("extract"),
        "founded": facts.get("founded"), "headquarters": facts.get("headquarters"),
        "country": facts.get("country"), "website": facts.get("website"),
        "parent": facts.get("parent"), "logo": logo, "logo_source": logo_source,
    }
