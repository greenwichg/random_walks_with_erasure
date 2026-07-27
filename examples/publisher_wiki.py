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
P_INSTANCE_OF = "P31"   # what KIND of thing the item is — newspaper, company, …

#: Description length. Long enough to be a real summary, short enough that the page stays a profile.
MAX_DESCRIPTION = 400

#: How many candidate articles to try before giving up. Verifying only the FIRST plausible page is
#: what lost "The Hill": search returns "King of the Hill" first, that gets refused, and the real
#: article — "The Hill (newspaper)", which verifies on domain — is never reached.
MAX_CANDIDATES = 4

#: When nothing verifies, which refusal to report. Ranked by how much it tells a human triaging the
#: backlog: "Wikipedia has this brand on a different domain" is actionable, "search returned
#: something unrelated" is not.
_REASON_RANK = {"domain_conflict": 3, "not_an_organisation": 2, "disambiguation": 1,
                "unverified": 0}

_WS = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# Small pure helpers.
# --------------------------------------------------------------------------- #
def _clean(text) -> Optional[str]:
    s = _WS.sub(" ", str(text or "")).strip()
    return s or None


def registrable_domain(url_or_host) -> Optional[str]:
    """The host part of a URL, lowercased, with a leading ``www.`` removed. ``www.bbc.co.uk`` and
    ``https://bbc.co.uk/news`` both give ``bbc.co.uk``."""
    s = str(url_or_host or "").strip()
    if not s:
        return None
    host = urlsplit(s if "//" in s else f"//{s}").hostname or ""
    host = host.lower().strip(".")
    return host[4:] if host.startswith("www.") else host or None


#: Two-part public suffixes, enough to cover the ccTLD pattern news domains actually use. Not a full
#: Public Suffix List — that is a dependency plus a data file to keep current, and the only job here
#: is finding the brand label in a host we already believe belongs to a publisher. An unlisted suffix
#: degrades to taking one label too few, which makes a match FAIL rather than falsely succeed.
_TWO_PART_SUFFIXES = frozenset("""
co.uk org.uk me.uk ac.uk gov.uk net.uk
com.au net.au org.au co.nz com.nz
co.jp or.jp ne.jp co.kr co.in net.in org.in
com.br net.br org.br com.mx com.ar com.co com.pe com.ve
com.ph com.my com.sg com.hk com.tw com.cn com.vn co.id
co.za co.ke co.il com.tr com.ua com.pk com.bd com.ng com.eg
com.es com.pt com.pl com.gr com.cy
""".split())


def domain_label(url_or_host) -> Optional[str]:
    """The BRAND label of a host: ``bbc.com``, ``bbc.co.uk`` and ``news.bbc.co.uk`` all give ``bbc``.

    This exists because whole-domain comparison rejected real matches at a high rate. Measured on
    the live catalog's busiest publishers, 5 of 8 domain conflicts were one organisation reached by
    two spellings — ``bbc.co.uk`` vs ``bbc.com``, ``dailymail.com`` vs ``dailymail.co.uk``,
    ``aol.co.uk`` vs ``aol.com``, ``unitaid.eu`` vs ``unitaid.org``,
    ``newsinfo.inquirer.net`` vs ``inquirer.com.ph``.

    It stays safe because the label still separates genuinely different organisations: the same
    measurement's true refusals — ``aktiencheck`` vs ``tomshardware``, ``pagesix`` vs ``nypost``,
    ``foxsports`` vs ``foxcorporation`` — differ at the label too."""
    host = registrable_domain(url_or_host)
    if not host:
        return None
    parts = host.split(".")
    if len(parts) < 2:
        return host
    idx = -3 if ".".join(parts[-2:]) in _TWO_PART_SUFFIXES else -2
    return parts[idx] if len(parts) >= -idx else parts[0]


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


def instance_of(entity: dict) -> list:
    """The item's ``P31`` class ids. Already present in the claims we fetch, so reading it costs
    nothing extra — it is the cheapest available signal that an item is an organisation at all."""
    return [v["id"] for v in _claims(entity, P_INSTANCE_OF)
            if isinstance(v, dict) and v.get("id")]


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

#: Wikidata classes (``P31`` instance-of) that make an item an organisation we could be reading.
#: Not exhaustive and does not need to be: an unlisted class simply falls back to the fact check
#: above, and a listed class is still refused when its domain conflicts. It exists because
#: "The Hill" — an exact title match on a real newspaper — was refused for carrying no website,
#: inception, HQ or parent claim, while its P31 said "newspaper" all along.
_ORG_CLASSES = frozenset("""
Q11032 Q1110794 Q1153191 Q192283 Q11033 Q1616075 Q14350 Q41298 Q1002697
Q43229 Q783794 Q891723 Q4830453 Q163740 Q2085381 Q15265344
""".split())


def _hosts(observed_host) -> list:
    """Accept one host or several — a publisher often reaches us from more than one domain."""
    if observed_host is None:
        return []
    if isinstance(observed_host, str):
        observed_host = [observed_host]
    return [h for h in (registrable_domain(h) for h in observed_host) if h]


def has_org_claim(entity: dict) -> bool:
    """Whether the item asserts ANY organisational claim, read from the item itself.

    Separate from the parsed ``facts`` because headquarters and parent are only *readable* after a
    second request that resolves their labels — and identity has to be decided before paying for
    that. Presence is all verification needs; the labels are only for display."""
    return any(_first_claim(entity, p) is not None
               for p in (P_WEBSITE, P_INCEPTION, P_HEADQUARTERS, P_PARENT))


def verify(*, publisher: str, page_title: str, facts: dict, observed_host=None,
           classes=None, org_claims: bool = False) -> "tuple[bool, str]":
    """Is this Wikipedia page really THIS publisher? Returns ``(accepted, reason)``.

    The evidence, strongest first:

    1. **Domain agreement** — Wikidata's official-website host matches a host we actually observe
       this publisher publishing from. Decisive, and it is why enrichment passes the catalog's
       counted host in: it is independent evidence, not another name string. Compared at the BRAND
       LABEL (:func:`domain_label`), so ``bbc.co.uk`` and ``bbc.com`` agree.
    2. **Domain conflict** — both known and different at the label. Rejected outright. This is the
       case that would otherwise put Fox Corporation's facts on a "Fox Sports" page, or Tom's
       Hardware's on a German stock-tips site.
    3. **No domain to compare** — the title must match the publisher's name AND the item must look
       like an organisation, either by carrying an organisational claim or by its ``instance of``.
       Both halves are load-bearing: the title match alone lets a common-noun masthead ("Mirror",
       "The Sun", "Metro") bind to the everyday-object article, and the organisation check alone
       lets any similarly-named company bind.

    Anything else is ``unverified`` — recorded as ambiguous for a human, never rendered."""
    site_domain = registrable_domain(facts.get("website"))
    observed = _hosts(observed_host)
    if site_domain and observed:
        # 1a. The same domain, or one a subdomain of the other. Decisive on its own.
        if any(o == site_domain or o.endswith("." + site_domain) or site_domain.endswith("." + o)
               for o in observed):
            return True, "domain"
        # 1b. The same BRAND on a different public suffix. NOT decisive on its own: `abcnews.com`
        # (the American network) and `abcnews.al` (an Albanian broadcaster) share a label and are
        # different organisations — accepting that pair was this module's first false positive.
        # Nothing structural separates it from bbc.com/bbc.co.uk, so the name has to corroborate.
        site_label = domain_label(facts.get("website"))
        if site_label and site_label in [domain_label(o) for o in observed]:
            if _name_key(page_title) == _name_key(publisher):
                return True, "domain_label"
            return False, "domain_conflict"
        return False, "domain_conflict"
    if _name_key(page_title) != _name_key(publisher):
        return False, "unverified"
    if not (org_claims or any(facts.get(f) for f in _ORG_FACTS)) \
            and not (set(classes or ()) & _ORG_CLASSES):
        return False, "not_an_organisation"
    return True, "title"


def _name_key(text) -> str:
    """Loose name comparison for the title check: case- and punctuation-insensitive, and blind to a
    leading "The" so "The Guardian" matches "Guardian".

    A large share of catalog publisher names ARE bare domains — ``marketbeat.com``, ``aol.co.uk``,
    ``thestar.com.my``, ``decider.com``. Those are reduced to their brand label first, so
    ``marketbeat.com`` can match the article titled "MarketBeat" instead of failing on the ``.com``.
    Only applied to single tokens containing a dot, so a real title with a full stop is untouched."""
    s = str(text or "").strip()
    if s and " " not in s and "." in s:
        s = domain_label(s) or s
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
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


#: Parenthetical qualifiers that mark a disambiguation entry as a publication rather than a film,
#: album or place — "The Hill (newspaper)" over "The Hill (1965 film)".
#: A trailing "(…)" disambiguator on a Wikipedia title — "The Hill (newspaper)".
_QUALIFIER = re.compile(r"\s*\([^)]*\)\s*$")

_MEDIA_QUALIFIER = re.compile(
    r"\((newspaper|magazine|website|news\b|news website|periodical|publisher|publishing|"
    r"journal|media|broadcaster|TV channel|television channel|radio station|news agency)",
    re.IGNORECASE)

#: How many of a disambiguation page's links to pull. MediaWiki returns a page's links
#: ALPHABETICALLY and includes EVERY wikilink on it, not just the disambiguation entries — so a
#: small limit is not a sample of the candidates, it is the start of the alphabet. Measured: "The
#: Hill" at limit 10 returned "Allison Hill (Harrisburg)" through "Edmonton Folk Music Festival",
#: and never reached "The Hill (newspaper)" at all.
DISAMBIGUATION_LINKS = 200


def disambiguation_links(title: str, fetch_json: Callable[[str], dict], *,
                         limit: int = DISAMBIGUATION_LINKS) -> list:
    """The article titles a disambiguation page links to.

    Enumerating candidates is *literally what a disambiguation page is for*, so treating one as a
    dead end throws away the best-curated candidate list Wikipedia has. But the raw list is
    alphabetical and full of incidental links, so :func:`rank_candidates` is what makes it usable."""
    url = _api(WIKIPEDIA_API, {
        "action": "query", "format": "json", "formatversion": "2",
        "prop": "links", "plnamespace": "0", "pllimit": str(int(limit)), "titles": title,
    })
    data = fetch_json(url) or {}
    pages = ((data.get("query") or {}).get("pages")) or []
    if not pages:
        return []
    return [ln["title"] for ln in (pages[0].get("links") or []) if ln.get("title")]


def rank_candidates(publisher: str, titles) -> list:
    """Order candidate titles by how likely each is to BE this publisher.

    Needed because a disambiguation page's links arrive in alphabetical order, which carries no
    information about relevance — without ranking, a four-candidate budget on "The Hill" is spent
    on "Allison Hill (Harrisburg)", "California State Route 17" and two Capitol Hills.

    Two signals, in order: the title IS the publisher's name — bare, or with a parenthetical
    qualifier — and then whether that qualifier looks like a publication.

    The qualifier must be stripped rather than merely tolerated as a suffix. A word-boundary
    ``startswith`` still ranked "Mirror Mirror (film)" as a name match for "Mirror", because
    "mirror mirror film" does begin with "mirror ". Removing the parenthetical first makes it
    "mirror mirror", which is simply a different name."""
    key = _name_key(publisher)

    def score(title: str):
        tk = _name_key(title)
        bare = _name_key(_QUALIFIER.sub("", title))
        if key and tk == key:
            name = 2                       # exact: "The Guardian"
        elif key and bare == key:
            name = 1                       # qualified: "The Hill (newspaper)"
        else:
            name = 0
        return (-name, 0 if _MEDIA_QUALIFIER.search(title) else 1, title)

    return sorted(titles, key=score)


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
def fallback_titles(publisher: str, first_page, fetch_json: Callable[[str], dict], *,
                    search: bool = True, limit: int = MAX_CANDIDATES) -> list:
    """Candidate titles to try when the direct hit did not verify, best first.

    A disambiguation page's own entries outrank full-text search: the page exists precisely to
    enumerate what the name can mean, and it is curated, while search is a relevance guess."""
    titles: list = []
    if first_page and not first_page.get("missing") and first_page.get("disambiguation"):
        # RANKED, not raw: the API returns these alphabetically, so their own order is noise.
        titles.extend(rank_candidates(publisher,
                                      disambiguation_links(first_page["title"], fetch_json)))
    if search:
        # Search results arrive in MediaWiki's relevance order, which IS meaningful — left alone.
        titles.extend(search_titles(publisher, fetch_json, limit=limit))
    seen, out = set(), []
    for t in titles:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:limit]


def _assess(publisher: str, page: dict, fetch_json: Callable[[str], dict],
            observed_host) -> "tuple[bool, str, dict]":
    """Decide whether one candidate page IS this publisher.

    Costs at most ONE Wikidata request: identity is decided from the item's own claims, and the
    second request that resolves headquarters/parent LABELS is deferred to the winner. A candidate
    we are about to reject should not cost the same as one we keep."""
    facts = {"founded": None, "headquarters": None, "country": None, "website": None,
             "parent": None, "logo": None}
    classes: list = []
    org_claims = False
    entity: dict = {}
    qid = page.get("wikidataId")
    if qid:
        entity = fetch_entities([qid], fetch_json).get(qid) or {}
        facts = parse_entity(entity, {})          # labels unresolved — presence is what matters here
        classes = instance_of(entity)
        org_claims = has_org_claim(entity)
    accepted, reason = verify(publisher=publisher, page_title=page["title"], facts=facts,
                              observed_host=observed_host, classes=classes, org_claims=org_claims)
    return accepted, reason, entity


def _accepted_result(page: dict, entity: dict, reason: str,
                     fetch_json: Callable[[str], dict]) -> dict:
    """Build the winning row — and only now pay for the labels identity did not need."""
    refs = entity_ids(entity, (P_HEADQUARTERS, P_COUNTRY, P_PARENT))
    facts = parse_entity(entity, fetch_entities(refs, fetch_json) if refs else {})
    # Logo: the Commons file a claim names is the outlet's ACTUAL logo; the article's lead image is
    # a fallback that is often a headquarters photo, so it is used only as one.
    logo, logo_source = facts.get("logo"), "wikimedia"
    if not logo and page.get("pageImage"):
        logo, logo_source = page["pageImage"], "wikipedia"
    if not logo:
        logo_source = None
    return {
        "status": "ok", "reason": reason, "source": "wikipedia",
        "wikidataId": page.get("wikidataId"), "wikipediaTitle": page.get("title"),
        "wikipediaUrl": page.get("url"), "description": page.get("extract"),
        "founded": facts.get("founded"), "headquarters": facts.get("headquarters"),
        "country": facts.get("country"), "website": facts.get("website"),
        "parent": facts.get("parent"), "logo": logo, "logo_source": logo_source,
    }


def lookup(publisher: str, fetch_json: Callable[[str], dict], *, observed_host=None,
           search: bool = True, max_candidates: int = MAX_CANDIDATES) -> dict:
    """Resolve one publisher to a verified fact set.

    Returns ``{"status": ..., ...}`` where status is ``ok`` / ``no_match`` / ``ambiguous``. It never
    raises for a *lookup* outcome — only a genuine transport failure propagates, so the caller can
    tell "Wikipedia says no" (cache it) from "the network failed" (retry sooner).

    **Every candidate is verified, not just the first.** Checking one page and stopping is what lost
    "The Hill": search answers it with "King of the Hill", that is correctly refused, and
    "The Hill (newspaper)" — which verifies on domain — is never reached.

    Candidates are generated LAZILY, so the common case does not subsidise the hard one: a direct
    title hit that verifies costs 3 requests (page, item, labels) and never runs a search. Only when
    that fails do we pay for a disambiguation page's entries or a search, then 2 requests per
    additional candidate tried — roughly 11 at the cap."""
    name = (publisher or "").strip()
    if not name:
        return {"status": "no_match", "reason": "empty_name"}

    best_reason: Optional[str] = None
    best_page: Optional[dict] = None
    tried: set = set()

    def consider(page: Optional[dict]) -> Optional[dict]:
        """Assess one page. Returns the winning row, or None — recording the best refusal so far."""
        nonlocal best_reason, best_page
        if page is None or page.get("missing"):
            return None
        if page.get("disambiguation"):
            # Its entries become candidates below; the page itself is not an outlet.
            if best_reason is None:
                best_reason, best_page = "disambiguation", page
            return None
        accepted, reason, entity = _assess(name, page, fetch_json, observed_host)
        if accepted:
            return _accepted_result(page, entity, reason, fetch_json)
        if best_reason is None or _REASON_RANK.get(reason, -1) > _REASON_RANK.get(best_reason, -1):
            best_reason, best_page = reason, page
        return None

    first = fetch_page(name, fetch_json)
    if first and not first.get("missing"):
        tried.add(first["title"])
        won = consider(first)
        if won is not None:
            return won

    for title in fallback_titles(name, first, fetch_json, search=search, limit=max_candidates):
        if title in tried:
            continue
        tried.add(title)
        won = consider(fetch_page(title, fetch_json))
        if won is not None:
            return won

    if best_reason is None:
        return {"status": "no_match", "reason": "no_page"}
    return {"status": "ambiguous", "reason": best_reason,
            "wikipediaTitle": (best_page or {}).get("title"),
            "wikipediaUrl": (best_page or {}).get("url"),
            "wikidataId": (best_page or {}).get("wikidataId")}
