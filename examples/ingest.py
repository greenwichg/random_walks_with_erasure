"""Reading-event ingestion — turn a raw read (a URL) into a scored read the engine can use.

Every ingestion source (browser extension, pasted URL, RSS) hands in a :class:`RawRead`; this
module scores it into the :class:`ScoredRead` interface from Milestone B4, which the
augmented-corpus seam then places into the reference corpus. No research algorithm is touched —
outlet identity + lean come from the product-layer :mod:`outlet_registry`.

Scoring strategy (approved): a **deterministic baseline** that needs no API key —

    outlet + lean     = the canonical OutletRegistry (the product layer's single source of truth
                        for outlet identity): a captured URL, a source-supplied outlet name, and a
                        corpus label all resolve to the SAME canonical outlet + AllSides lean.
                        An outlet the registry doesn't know still ingests — the outlet is the bare
                        domain and the lean is NaN (excluded from lean metrics, as the engine
                        already handles missing data).
    topic             = the ONE canonical deterministic classifier, :func:`classify_topic`:
                        the source's own category tags normalized into the closed taxonomy, else
                        the URL's topical section, else a headline/description keyword lexicon,
                        else the URL's *geographic* section (World / U.S.), else "" (uncategorized)
    political         = what the source supplies, else the shared :func:`looks_political`
                        heuristic over the URL/source category/classified topic

plus an **optional, pluggable** :class:`Enricher` (e.g. an LLM emotion/register/topic
classifier, cached) that improves a read *only when configured*. Absent an enricher, scoring is
fully offline and deterministic, and unscored fields (emotion/register/confidence) stay ``n/a``
exactly as the engine already handles missing data.

    scorer = Scorer()                                          # baseline, no enricher
    read = score_with_cache(RawRead(url=...), scorer, store)   # scored once per canonical URL
"""

from __future__ import annotations

import dataclasses
import functools
import os
import re
from dataclasses import asdict, dataclass, replace
from typing import Optional, Protocol
from urllib.parse import urlsplit, urlunsplit

from augmented_corpus import ScoredRead
from outlet_registry import OutletRegistry, default_registry

# Coarse URL-path segment -> section topic, consulted by :func:`classify_topic` AFTER the
# publisher's own category tags. A topical section (/politics/, /business/) is decisive; a
# *geographic* section (/us-news/, /world/) only wins when the headline lexicon finds no specific
# subject — geography says where a story happened, not what it is about.
_SECTIONS = {
    "politics": "Politics", "election": "Politics", "elections": "Politics", "law": "Politics",
    "business": "Business", "economy": "Business", "markets": "Business", "money": "Business",
    "technology": "Technology", "tech": "Technology",
    "science": "Science", "health": "Health", "climate": "Climate", "environment": "Climate",
    "world": "World", "world-news": "World", "uk-news": "World", "australia-news": "World",
    "global-development": "World",
    "us": "U.S.", "us-news": "U.S.",
    "opinion": "Opinion", "commentisfree": "Opinion",
    "sports": "Sports", "sport": "Sports", "football": "Sports", "soccer": "Sports",
    "entertainment": "Entertainment", "arts": "Arts", "culture": "Culture",
}
def looks_political(url: str = "", category: str = "", title: str = "") -> bool:
    """The shared article-level political flag — now derived from the canonical topic classifier
    (W3A), not a raw substring test.

    ONE definition product-wide (:func:`_political_from_topic`): the read scorer and the corpus
    loaders share it, so the Information Health metrics, the cross-cutting gate, and the bridge
    explanations can never disagree about what "political" means. Delegating to
    :func:`classify_topic` fixes the substring test's false positives (``"election"`` inside
    ``"selection"``) and false negatives (``"congress"`` / ``"white house"`` never contained the
    literal ``politic`` / ``election``). Deterministic; no network, no new model, no LLM.
    ``title`` is optional and, when supplied, lets a political op-ed be recognised (see
    :func:`_political_from_topic`)."""
    return _political_from_topic(
        classify_topic(url=url, source_category=category, title=title), title)


# --------------------------------------------------------------------------- #
# Canonical topic classification — the ONE place a topic is ever assigned.
# --------------------------------------------------------------------------- #
# The closed product taxonomy. classify_topic returns a member or "" (uncategorized — the UI
# hides the topic segment); it never invents a junk drawer ("General") and never stores a raw
# publisher label verbatim.
TAXONOMY = ("Politics", "Business", "Technology", "Science", "Health", "Climate", "World",
            "U.S.", "Opinion", "Sports", "Entertainment", "Arts", "Culture")

# Sections that locate a story geographically rather than topically — used as the LAST fallback
# in classify_topic so "/us-news/" never hides a plainly political / business / science headline.
_GEO_TOPICS = frozenset({"World", "U.S."})

# Publisher/source category label -> canonical topic. Keys are lower-cased, whitespace-collapsed
# labels; the URL-segment names double as labels, extended with the multi-word spellings real
# feeds use. A label that maps nowhere contributes NO signal — junk drawers ("News",
# "Top Stories", "General", "Featured", "Latest") simply fall through to the URL and headline
# evidence instead of being stored verbatim.
_CATEGORY_ALIASES = {
    **_SECTIONS,
    # Politics — politicians, elections, legislation, government, courts, diplomacy, policy.
    "us politics": "Politics", "u.s. politics": "Politics", "uk politics": "Politics",
    "politics news": "Politics", "political news": "Politics", "government": "Politics",
    "government and politics": "Politics", "policy": "Politics", "public policy": "Politics",
    "justice": "Politics", "courts": "Politics", "supreme court": "Politics",
    "congress": "Politics", "white house": "Politics", "geopolitics": "Politics",
    "diplomacy": "Politics", "campaign 2024": "Politics", "election 2024": "Politics",
    # Business / economy / markets.
    "economics": "Business", "finance": "Business", "financial": "Business",
    "personal finance": "Business", "business news": "Business", "wall street": "Business",
    "stock market": "Business", "stocks": "Business", "investing": "Business",
    "companies": "Business", "earnings": "Business",
    # Technology.
    "sci-tech": "Technology", "science and technology": "Technology",
    "artificial intelligence": "Technology", "ai": "Technology", "cybersecurity": "Technology",
    "internet": "Technology", "software": "Technology", "gadgets": "Technology",
    "computing": "Technology", "technology news": "Technology",
    # Science.
    "space": "Science", "astronomy": "Science", "physics": "Science",
    # Health.
    "wellness": "Health", "medicine": "Health", "medical": "Health", "healthcare": "Health",
    "health care": "Health", "public health": "Health", "mental health": "Health",
    "health news": "Health", "fitness": "Health", "coronavirus": "Health", "covid": "Health",
    "covid-19": "Health",
    # Climate (the product keeps the "Climate" name; "Environment" normalizes into it).
    "climate change": "Climate", "climate crisis": "Climate", "environmental": "Climate",
    "global warming": "Climate", "sustainability": "Climate",
    "energy and environment": "Climate", "climate and environment": "Climate",
    "extreme weather": "Climate",
    # World (geographic; regional desks normalize here).
    "world news": "World", "international": "World", "international news": "World",
    "global": "World", "global news": "World", "foreign news": "World", "europe": "World",
    "asia": "World", "africa": "World", "middle east": "World", "americas": "World",
    "latin america": "World", "uk news": "World", "australia news": "World",
    "global development": "World",
    # U.S. (geographic).
    "us news": "U.S.", "u.s. news": "U.S.", "u.s.": "U.S.", "usa": "U.S.",
    "united states": "U.S.", "national": "U.S.", "nation": "U.S.",
    # Opinion.
    "opinions": "Opinion", "editorial": "Opinion", "editorials": "Opinion",
    "commentary": "Opinion", "comment": "Opinion", "comment is free": "Opinion",
    "op-ed": "Opinion", "op-eds": "Opinion", "voices": "Opinion", "perspectives": "Opinion",
    "letters": "Opinion",
    # Sports.
    "sports news": "Sports", "nfl": "Sports", "nba": "Sports", "mlb": "Sports", "nhl": "Sports",
    "baseball": "Sports", "basketball": "Sports", "tennis": "Sports", "golf": "Sports",
    "cricket": "Sports", "rugby": "Sports", "olympics": "Sports", "motorsport": "Sports",
    "formula 1": "Sports", "formula one": "Sports",
    # Entertainment.
    "celebrity": "Entertainment", "celebrities": "Entertainment", "tv": "Entertainment",
    "television": "Entertainment", "movies": "Entertainment", "film": "Entertainment",
    "films": "Entertainment", "music": "Entertainment", "showbiz": "Entertainment",
    "hollywood": "Entertainment", "tv and radio": "Entertainment",
    "entertainment news": "Entertainment",
    # Arts / Culture.
    "art": "Arts", "art and design": "Arts", "design": "Arts", "theater": "Arts",
    "theatre": "Arts", "dance": "Arts", "photography": "Arts",
    "books": "Culture", "literature": "Culture", "style": "Culture", "fashion": "Culture",
    "lifestyle": "Culture", "life and style": "Culture",
}

# Feeds pack several tags into one string ("Trump administration; US politics; Farming") and
# some sections nest ("News/Politics") — split on the common separators, never on ".".
_LABEL_SPLIT = re.compile(r"[;,|/&>»]+")


def _category_labels(source_category: str) -> "list[str]":
    """Normalized candidate labels from a source category string: split on separators,
    lower-case, collapse whitespace, drop empties. Order preserved (first tag = most specific
    in every feed dialect we ingest)."""
    out = []
    for part in _LABEL_SPLIT.split(source_category or ""):
        label = re.sub(r"\s+", " ", part).strip().lower()
        if label:
            out.append(label)
    return out


def _rx(*terms: str) -> "re.Pattern":
    """A case-insensitive word-boundary alternation over ``terms`` (each may be a phrase or a
    small regex fragment). Word boundaries keep precision high: "law" never fires on "lawn"."""
    return re.compile(r"\b(?:" + "|".join(terms) + r")\b", re.IGNORECASE)


# Deterministic subject lexicon, checked in order (first hit wins) against — in priority — the
# source category text, then the title, then the description. Politics comes first and is
# deliberately rich (politicians, institutions, processes, parties, courts, diplomacy/geopolitics)
# so an obviously political story is never filed under a generic label. Every pattern is chosen
# for precision over recall: a missed article degrades to a section/"" (honest), a wrong hit
# would be a lie.
_TOPIC_LEXICON = (
    ("Politics", _rx(
        r"politics?", r"political", r"politicians?", r"geopolitic\w*",
        # heads of state / government figures
        r"trump", r"biden", r"kamala harris", r"obama", r"putin", r"zelenskyy?", r"netanyahu",
        r"xi jinping", r"kim jong[- ]un", r"macron", r"starmer", r"modi",
        # institutions + agencies
        r"congress\w*", r"senate", r"senators?", r"house of representatives", r"capitol hill",
        r"white house", r"oval office", r"supreme court", r"scotus", r"pentagon",
        r"state department", r"justice department", r"homeland security", r"parliament\w*",
        r"downing street", r"kremlin",
        # offices + roles
        r"president\w*", r"vice[- ]president", r"prime minister", r"chancellors?",
        r"governors?", r"mayors?", r"lawmakers?", r"legislators?", r"legislat\w*",
        r"attorney general", r"secretary of state", r"ministers?",
        # elections + process
        r"elections?", r"electoral", r"ballots?", r"voters?", r"midterms?", r"primaries",
        r"caucus\w*", r"referend\w*", r"impeach\w*", r"inaugurat\w*", r"filibuster", r"veto\w*",
        r"executive order", r"government shutdown", r"coup", r"martial law", r"regimes?",
        r"authoritarian\w*",
        # courts as public power
        r"federal judge", r"federal court", r"appeals court", r"high court",
        # parties + movements
        r"democrats?", r"democratic party", r"republicans?", r"gop", r"labour party",
        r"labor party", r"tor(?:y|ies)", r"conservative party", r"far[- ]right", r"far[- ]left",
        r"brexit",
        # diplomacy + geopolitical events + policy
        r"sanctions?", r"tariffs?", r"treaty", r"treaties", r"diplomac\w*", r"diplomat\w*",
        r"embass\w*", r"nato", r"united nations", r"un security council", r"ceasefire",
        r"cease-fire", r"peace talks", r"immigration", r"asylum seekers?", r"deportations?",
    )),
    ("Business", _rx(
        r"stock market", r"stocks", r"wall street", r"dow jones", r"s&p 500", r"nasdaq",
        r"earnings", r"quarterly profits?", r"revenues?", r"ipos?", r"mergers?",
        r"acquisitions?", r"layoffs?", r"inflation", r"recession", r"gdp", r"federal reserve",
        r"interest rates?", r"central banks?", r"cryptocurrenc\w*", r"bitcoin", r"startups?",
        r"ceos?", r"econom(?:y|ic|ics|ist)\w*",
    )),
    ("Technology", _rx(
        r"artificial intelligence", r"ai", r"chatgpt", r"openai", r"machine learning",
        r"software", r"smartphones?", r"iphone", r"android", r"silicon valley",
        r"cybersecurity", r"hackers?", r"data breach", r"tiktok", r"google", r"microsoft",
        r"tesla", r"semiconductors?", r"apps?",
    )),
    ("Science", _rx(
        r"nasa", r"spacex", r"space station", r"asteroid", r"telescope", r"quantum",
        r"physicists?", r"dna", r"genome", r"fossils?", r"archaeolog\w*", r"astronomers?",
        r"spacecraft", r"rocket launch",
    )),
    ("Health", _rx(
        r"cancer", r"vaccines?", r"vaccination", r"virus", r"outbreak", r"pandemic",
        r"epidemic", r"covid(?:-19)?", r"coronavirus", r"mental health", r"diabetes",
        r"alzheimer\w*", r"opioid\w*", r"obesity", r"hospitals?", r"public health", r"fda",
        r"cdc", r"medicare", r"medicaid",
    )),
    ("Climate", _rx(
        r"climate", r"global warming", r"greenhouse gas\w*", r"emissions", r"carbon",
        r"wildfires?", r"heatwaves?", r"heat wave", r"drought", r"flooding",
        r"renewable energy", r"solar power", r"fossil fuels?", r"deforestation",
        r"biodiversity", r"hurricanes?", r"typhoons?",
    )),
    ("Sports", _rx(
        r"super bowl", r"world cup", r"olympics?", r"olympic", r"nfl", r"nba", r"mlb", r"nhl",
        r"playoffs?", r"touchdowns?", r"quarterbacks?", r"grand slam", r"wimbledon",
        r"premier league", r"champions league", r"formula (?:1|one)", r"home run",
        r"world series", r"fifa", r"uefa",
    )),
    ("Entertainment", _rx(
        r"box office", r"movie", r"film", r"netflix", r"hollywood", r"celebrity", r"grammys?",
        r"oscars?", r"academy awards?", r"emmys?", r"tv series", r"album", r"concert",
        r"singer", r"actors?", r"actress", r"rapper", r"sitcom", r"premiere",
    )),
    ("Arts", _rx(
        r"museums?", r"exhibitions?", r"opera", r"ballet", r"sculpture", r"art gallery",
    )),
)


def _lexicon_topic(text: str) -> str:
    """First lexicon topic whose pattern matches ``text``, or ""."""
    t = (text or "").strip()
    if not t:
        return ""
    for topic, rx in _TOPIC_LEXICON:
        if rx.search(t):
            return topic
    return ""


def _topic_from_path(path: str) -> str:
    """The URL path's section topic, or "". The first *topical* segment wins; a geographic
    segment (/us/, /world/) is returned only when no topical segment exists — so
    ``/us/politics/…`` is Politics, not U.S."""
    geo = ""
    for seg in path.split("/"):
        hit = _SECTIONS.get(seg.lower())
        if hit and hit not in _GEO_TOPICS:
            return hit
        if hit and not geo:
            geo = hit
    return geo


def classify_topic(url: str = "", source_category: str = "", title: str = "",
                   description: str = "") -> str:
    """THE canonical topic classifier — every stored/served article topic comes from here.

    Deterministic (no network, no LLM), resolving in confidence order:

    1. the source's own category tags (RSS ``<category>``, OpenGraph ``article:section``),
       normalized into the taxonomy — first by label alias, then by the subject lexicon over the
       tag text (so "Trump administration" is Politics even though it isn't a section name);
       labels that map nowhere ("News", "Top Stories", "General") contribute nothing
    2. a *topical* URL section (/politics/, /business/, /opinion/ …)
    3. the subject lexicon over the title, then the description
    4. a *geographic* URL section (/us-news/, /world/) — where a story happened is the weakest
       signal about what it is about, so it never outranks a plainly political headline

    Returns a :data:`TAXONOMY` member or "" (uncategorized; the UI hides the segment). Never
    "General", never a raw publisher label."""
    for label in _category_labels(source_category):
        hit = _CATEGORY_ALIASES.get(label)
        if hit:
            return hit
    hit = _lexicon_topic(source_category)
    if hit:
        return hit
    path = urlsplit(url).path.lower() if url else ""
    section = _topic_from_path(path)
    if section and section not in _GEO_TOPICS:
        return section
    hit = _lexicon_topic(title) or _lexicon_topic(description)
    if hit:
        return hit
    return section


def _political_from_topic(topic: str, title: str = "") -> bool:
    """W3A: the political flag derived from the canonical topic (already computed by
    :func:`classify_topic`). Political ⇔ the topic is ``"Politics"``, OR it is an ``"Opinion"``
    piece whose headline is itself about politics — so a political op-ed stays political while a
    sports / entertainment / business op-ed does not. Reuses the existing precision-first lexicon
    (:func:`_lexicon_topic`); no new model, no network. The single source of truth for the mask,
    shared by :func:`looks_political` and the scorer."""
    return topic == "Politics" or (topic == "Opinion" and _lexicon_topic(title or "") == "Politics")


@dataclass(frozen=True)
class RawRead:
    """A reading event as an ingestion source observes it — before scoring.

    ``url`` is required; the rest are hints a source may already know (the extension reads the
    outlet, section, headline, and the article's ``og:description`` off the page). ``subtitle`` /
    ``description`` give the enricher more text than the headline alone (register/emotion spread
    much better on an abstract); anything left empty / ``None`` is derived by the scorer or
    degrades gracefully to headline-only."""

    url: str
    title: str = ""
    outlet: str = ""
    category: str = ""
    political: Optional[bool] = None
    read_at: Optional[str] = None
    subtitle: str = ""          # optional richer text for enrichment (deck / kicker)
    description: str = ""        # optional richer text for enrichment (og:description / abstract)


class Enricher(Protocol):
    """Optional refinement of a baseline-scored read (e.g. an LLM emotion/register classifier).
    Implementations return a possibly-updated :class:`ScoredRead` and must be side-effect free."""

    def enrich(self, scored: ScoredRead, raw: RawRead) -> ScoredRead: ...


def canonical_url(url: str) -> str:
    """Canonical form used as the dedup / cache key: lower-cased host without ``www.``, no query
    or fragment, no trailing slash. Query params are dropped (usually tracking); a source that
    needs a query-identified article should pass an already-clean URL."""
    p = urlsplit(url.strip())
    scheme = (p.scheme or "https").lower()
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path.rstrip("/") or "/"
    return urlunsplit((scheme, host, path, "", ""))


def normalize_url(url: str) -> str:
    """Best-effort tidy of a user-supplied URL: trim, and prepend ``https://`` when no scheme is
    present so a bare ``example.com/x`` parses with a host."""
    u = (url or "").strip()
    if u and "://" not in u:
        u = "https://" + u
    return u


def has_host(url: str) -> bool:
    """A cheap validity check: the URL has a plausible host (a dot, no spaces) — enough to
    reject pasted junk before it becomes a reading event."""
    host = urlsplit(url).netloc
    return bool(host) and "." in host and " " not in host


# --------------------------------------------------------------------------- #
# Catalog block list — outlets configured never to enter the catalog at all.
# --------------------------------------------------------------------------- #
#: The setting, comma-separated. `RWE_CATALOG_BLOCKED_OUTLETS` is the house-convention name (every
#: other switch in this codebase is `RWE_`-prefixed, and an unprefixed name is easier to collide
#: with in a shared container environment); the bare `CATALOG_BLOCKED_OUTLETS` is accepted as an
#: alias so either spelling works.
_BLOCKED_ENV_KEYS = ("RWE_CATALOG_BLOCKED_OUTLETS", "CATALOG_BLOCKED_OUTLETS")


def _blocked_setting() -> str:
    for key in _BLOCKED_ENV_KEYS:
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return ""


@functools.lru_cache(maxsize=8)
def _blocked_index(setting: str) -> tuple:
    """Parse one setting string into ``(canonical names, hosts)``.

    Each entry is resolved through the REGISTRY first, so a blocked outlet is blocked by IDENTITY —
    every alias, every corpus variant, every domain the registry knows for it — rather than by the
    one string somebody happened to type. Blocking "bbc.co.uk" therefore also blocks an article
    that arrives labelled "BBC News", which blocking on raw name strings could never do.

    An entry the registry does not know falls back to a DOMAIN, which is the case that matters
    most in practice: the outlets worth keeping out are usually the ones nobody has curated a row
    for. An entry that is neither (an unregistered bare name — no dot, no row) matches nothing;
    `blocked_catalog_index` exists so that is discoverable rather than silent.

    Keyed on the setting string, so a test or an operator changing the env re-parses instead of
    being served a memo of the old value."""
    canonicals, hosts = set(), set()
    for entry in setting.split(","):
        entry = entry.strip()
        if not entry:
            continue
        outlet = default_registry().resolve(entry)
        if outlet is not None:
            canonicals.add(outlet.canonical)
        elif "." in entry:                      # unknown to the registry -> treat it as a domain
            host = _domain_of(entry)
            if host:
                hosts.add(host)
    return frozenset(canonicals), frozenset(hosts)


def blocked_catalog_index() -> tuple:
    """What the current setting was understood to mean: ``(canonical names, hosts)``.

    Exposed so an operator can confirm an entry was actually understood. A misspelling, or an
    unregistered outlet named rather than domained, silently matches nothing — and a block list
    that quietly does nothing is the worst way to find that out."""
    return _blocked_index(_blocked_setting())


def is_blocked_from_catalog(outlet_hint: "str | None", url: str) -> bool:
    """Whether this article's outlet is configured out of the catalog.

    TWO-SIDED: the hint and the URL are each resolved, and EITHER landing on a blocked identity is
    enough. ``hint or url`` — resolving the hint and never looking at the URL — was not enough, and
    the registry has the measurement on file (live catalog, 2026-08-08): of 671 obituary articles,
    only 172 arrive under the obituary feed's own identity. The other 499 arrive with the parent
    NEWSPAPER as their publisher string and an ``obits.*`` URL, so the hint resolves to an allowed
    masthead and the article walks straight in. ``story_service`` has always tested this class
    two-sided (``is_wire(publisher) or is_wire_url(url)``); this brings the block list in line.

    Checking the URL too cannot over-block a real masthead article: that article's URL resolves to
    the masthead as well (``www.oregonlive.com`` -> ``The Oregonian``), which is not in the blocked
    set. Only a URL that itself resolves to a BLOCKED identity blocks — which is precisely the
    obituary subdomain, because it carries its own registry row.

    An empty setting returns False before doing any work, so a deployment that sets nothing behaves
    exactly as it did before this existed."""
    setting = _blocked_setting()
    if not setting:
        return False
    canonicals, hosts = _blocked_index(setting)
    if canonicals:
        for text in (outlet_hint, url):
            if not text:
                continue
            outlet = default_registry().resolve(text)
            if outlet is not None and outlet.canonical in canonicals:
                return True
    if hosts:
        # Subdomain-tolerant, matching how the registry itself resolves domain forms: blocking
        # `example.com` blocks `news.example.com`, and never `notexample.com`.
        host = _domain_of(url)
        return any(host == h or host.endswith("." + h) for h in hosts)
    return False


def _domain_of(url: str) -> str:
    """Bare host of a URL (lower-cased, ``www.`` removed) — the fallback outlet label when the
    registry doesn't know the outlet. Correct prefix handling: the research helper's
    ``lstrip("www.")`` ate leading ``w`` characters (``washingtonpost.com`` -> ``ashingtonpost``,
    ``wsj.com`` -> ``sj``); this strips the ``www.`` prefix only."""
    s = (url or "").strip()
    netloc = urlsplit(s).netloc if "://" in s else s.split("/", 1)[0]
    host = netloc.split("@")[-1].split(":", 1)[0].lower()
    return host[4:] if host.startswith("www.") else host


#: Version of the deterministic scorer (topic classifier + political flag + registry resolution +
#: baseline enricher). Stamped on every catalogue row it scores (``feed_articles.scorer_version``)
#: so a downstream consumer can tell a data change from an algorithm change. Bump it when
#: ``classify_topic``, ``looks_political``, ``_resolve_outlet`` or the baseline enricher would
#: produce different output for identical input.
SCORER_VERSION = "1"


class Scorer:
    """Baseline, deterministic scorer: a URL -> :class:`ScoredRead`, with an optional enricher.

    Outlet identity + lean come from the canonical :class:`OutletRegistry` (the product layer's
    single source of truth), so a captured URL, a source-supplied outlet name, and a corpus label
    all collapse to the same canonical outlet. An outlet the registry doesn't know still ingests —
    the outlet is the bare domain and the lean is NaN. No API key, no network."""

    def __init__(self, registry: Optional[OutletRegistry] = None,
                 enricher: Optional[Enricher] = None):
        self.registry = registry if registry is not None else default_registry()
        self.enricher = enricher

    def score(self, raw: RawRead) -> ScoredRead:
        category = classify_topic(url=raw.url, source_category=raw.category, title=raw.title,
                                  description=f"{raw.subtitle} {raw.description}".strip())
        political = (raw.political if raw.political is not None
                     else _political_from_topic(category, raw.title))   # reuse the topic just computed (W3A)
        outlet, lean = self._resolve_outlet(raw)
        scored = ScoredRead(
            article_id=canonical_url(raw.url),
            outlet=outlet,
            category=category,
            title=raw.title or "",
            lean=lean,
            political=political,
            read_at=raw.read_at,
        )
        return self.enricher.enrich(scored, raw) if self.enricher is not None else scored

    def _resolve_outlet(self, raw: RawRead):
        """Canonical outlet + AllSides lean via the registry — the outlet name first, then the URL.

        The URL fallback fires ONLY when the supplied name resolves to nothing, so it can attach
        identity but never override one: an obituary arriving under its masthead's name still
        resolves by that name. It exists because a registered HOST can arrive under a display name
        the registry cannot key — non-Latin labels fold to empty name keys (alkhaleej.ae's rows
        carried "جريدة الخليج", etoday.co.kr's "이투데이"; production 2026-09-01) — and a host alias
        that loses to the very label it was meant to heal is a registry row that does nothing.
        Unknown on both -> (the source's label or the bare domain, NaN): the read still ingests,
        just without a lean."""
        out = self.registry.resolve(raw.outlet or raw.url)
        if out is None and raw.outlet and raw.url:
            out = self.registry.resolve(raw.url)
        if out is not None:
            return out.canonical, out.lean
        return (raw.outlet or _domain_of(raw.url)), float("nan")


# --------------------------------------------------------------------------- #
# Scored-article cache — score once per canonical URL, shared across users.
# --------------------------------------------------------------------------- #
def _to_cache(scored: ScoredRead) -> dict:
    d = asdict(scored)
    d["read_at"] = None          # the cache holds article scoring, not a per-read timestamp
    return d


def _from_cache(d: dict) -> ScoredRead:
    names = {f.name for f in dataclasses.fields(ScoredRead)}
    return ScoredRead(**{k: v for k, v in d.items() if k in names})


def score_with_cache(raw: RawRead, scorer: Scorer, store) -> ScoredRead:
    """Score ``raw``, reusing a previously-scored article for the same canonical URL so a
    popular link is scored once and shared. ``store`` is any object exposing
    ``get_scored_article(url)`` / ``save_scored_article(url, dict)`` (the SQLite store). The
    returned read carries *this* read's timestamp, while its scoring comes from the cache."""
    key = canonical_url(raw.url)
    cached = store.get_scored_article(key)
    if cached is not None:
        sr = _from_cache(cached)
        return replace(sr, read_at=raw.read_at, title=(sr.title or raw.title or ""))
    scored = scorer.score(raw)
    store.save_scored_article(key, _to_cache(scored))
    return scored
