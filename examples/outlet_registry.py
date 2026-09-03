"""Canonical outlet registry — the product layer's single source of truth for outlet identity.

Three subsystems need to agree on *who an outlet is*: the reading-ingestion scorer (which sees a
URL / domain, e.g. ``https://www.nytimes.com/…``), a reference corpus (which may label the same
outlet ``"New York Times (News)"``), and the onboarding UI (which shows ``"New York Times"``).
Left to their own normalisation these disagree — ``nytimes.com`` normalises to ``"nytimes"`` while
``"New York Times"`` normalises to ``"newyorktimes"`` — so a real read never lines up with the
population's outlet, and captured domains that start with ``w`` were even corrupted by a
``lstrip("www.")`` bug in the research helper.

This module fixes that **in the product layer only**. It loads
:data:`examples/data/outlet_registry.csv` (canonical name · AllSides lean · aliases) and resolves
any of those forms — display name, domain, full URL (incl. subdomains like ``edition.cnn.com``),
or a corpus ``"… (Online News)"`` variant — to one :class:`Outlet` (canonical name + lean).

Nothing here touches the research modules (``rwe/``, ``health_report``, ``simulate_users``,
``narrate_report``); it is a standalone lookup that later milestones will call from the scorer,
the Qbias preprocessor, and onboarding. **Not wired into anything yet.**

    from outlet_registry import default_registry
    reg = default_registry()
    reg.resolve("https://www.washingtonpost.com/2026/politics/x")  # Outlet("Washington Post", -1.0)
    reg.resolve("Fox News (Online News)")                          # Outlet("Fox News", 2.0)
    reg.lean("nytimes.com")                                        # -1.0   (NaN if unknown)
"""

from __future__ import annotations

import csv
import datetime
import math
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urlsplit

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "outlet_registry.csv")
_PARENS = re.compile(r"\([^)]*\)")           # "(Online News)", "(Opinion)", …
_NONALNUM = re.compile(r"[^a-z0-9]+")

#: Legal values for the ``credibility`` column. Blank is legal and means UNCURATED, which is not the
#: same as ``low`` — absence of a verdict never disqualifies an outlet, exactly as absence of a lean
#: never centres one (L2.2).
CREDIBILITY = ("high", "medium", "low")

#: Legal values for the ``factuality`` column — **the rater's own scale, not ours**.
#:
#: Deliberately six levels, and deliberately NOT the three of :data:`CREDIBILITY`. MBFC publishes
#: Very High / High / Mostly Factual / Mixed / Low / Very Low, and collapsing that into
#: high|medium|low destroys the distinction the rating exists to make: "Mostly Factual" is a mild
#: reservation and "Mixed" is a serious one, and a reader shown the same word for both has been
#: told something false. Storing the verdict as published also means a future display surface can
#: name the rater and its label rather than paraphrasing a paraphrase.
#:
#: The two columns are INDEPENDENT in this phase. ``credibility`` remains what the clustering
#: vote-gate reads (``is_low_credibility``); ``factuality`` is carried and read by nothing. Deriving
#: one from the other is a clustering change and belongs to its own commit with its own before/after.
FACTUALITY = ("very_high", "high", "mostly_factual", "mixed", "low", "very_low")

#: The operator switch that PUBLISHES the verdicts above — see :func:`factuality_published`. The
#: name lives beside the vocabulary it governs so the two can never drift apart.
_PUBLIC_FACTUALITY_ENV = "RWE_PUBLIC_FACTUALITY"
_PUBLISH_TRUE = {"1", "true", "yes", "on"}

#: Who published a factuality verdict. Required whenever ``factuality`` is set — an unattributed
#: rating is indistinguishable from a guess, and this file's whole discipline (L2.2) is that a
#: rating is either sourced or absent. Kept as a small closed set so a typo is a lint error rather
#: than a new "source" nobody can trace.
FACTUALITY_SOURCES = ("mbfc",)

#: ``factuality_asof`` — the ISO date the verdict was READ at the rater. Required whenever
#: ``factuality`` is set, for the same reason the source is: a third party's rating is a claim
#: about a named news organisation AT A MOMENT, and raters revise. MBFC prints its own revision
#: line on each profile (the New York Post's reads "Updated (M. Huitsing 09/26/2025)"), and this
#: file has no refresh mechanism — so a verdict with no date cannot be told apart from one that is
#: still current, and displaying it under the rater's name would put words in their mouth.
#:
#: Recorded per ROW rather than per file because tranches are refreshed piecemeal: the batch here
#: already spans 2026-07-28 to 2026-08-11, and a single file-level date would be wrong for most of
#: it. Where a tranche is known only to a range, the EARLIER date is recorded — that can understate
#: freshness but never overstates it, and only one of those two errors misleads a reader.
_ASOF_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Where a reader can check a factuality verdict at its source, keyed by ``factuality_source``.
#:
#: A SEARCH on the rater's own site, keyed on the outlet's curated domain — deliberately not a
#: stored deep link to the rating page. Three reasons, in order of weight:
#:   * A stored URL rots. MBFC re-slugs profiles freely (``usa-today-2``, ``fox-news-bias``,
#:     ``wxin-fox59-indianapolis-bias``), and a rotted deep link either 404s or, worse, lands on
#:     some other outlet's page while still carrying our outlet's name.
#:   * A search always resolves to whatever the rater publishes TODAY, which is exactly what a
#:     "check this yourself" link is for — and the more useful answer when our stored verdict is
#:     older than theirs.
#:   * We do not have page URLs for every row: they exist for the tranches fetched in 2026-08 and
#:     never for the earlier rows, whose verdicts were read while sourcing the lean. A column that
#:     is two-thirds populated is a worse contract than one derived uniformly.
#: Keyed off the same closed set as :data:`FACTUALITY_SOURCES` so a new source cannot be recorded
#: without someone deciding where its ratings can be read.
FACTUALITY_SOURCE_SEARCH = {"mbfc": "https://mediabiasfactcheck.com/?s={domain}"}

#: Legal values for the ``ownership`` column — WHO CONTROLS the outlet, by controlling-owner
#: type (the Ground News comparison's taxonomy, adapted). Blank is legal and means UNCURATED —
#: absence of a classification never becomes a category, exactly as absence of a lean never
#: centres one (L2.2). Every category is a claim about a named news organisation, so the same
#: provenance discipline as ``factuality`` applies: a set value requires ``ownership_source``
#: and ``ownership_asof``, enforced by :func:`lint_registry`.
#:
#: ``independent``    self-owned newsroom, trust or nonprofit (AP's cooperative, Scott Trust)
#: ``individual``     controlled by one person or family (Bloomberg L.P.)
#: ``telecom``        a telecommunications parent (Comcast → NBCUniversal)
#: ``government``     state-owned, state-funded or public-charter (Xinhua, BBC's Royal Charter)
#: ``private_equity`` a financial sponsor / investment firm holds control
#: ``conglomerate``   a large multi-brand media group (Fox Corporation, Warner Bros. Discovery)
#: ``corporation``    a publicly traded company that is not primarily a media group
#: ``other``          a documented structure none of the above describes
OWNERSHIPS = ("independent", "individual", "telecom", "government",
              "private_equity", "conglomerate", "corporation", "other")

#: Who recorded an ownership classification. ``public_record`` = the controlling owner is the
#: outlet's own public identity (its about page, its parent's filings) and undisputed; kept as a
#: closed set like :data:`FACTUALITY_SOURCES` so a typo is a lint error rather than a new
#: untraceable "source". A future licensed dataset joins as its own named value.
OWNERSHIP_SOURCES = ("public_record",)

#: Legal values for the ``kind`` column. Blank = an ordinary news outlet, which is the vast
#: majority. Everything named here is a source that is NOT a newsroom covering a story, and each
#: name records a different reason a lean would be the wrong question:
#:
#: ``wire``       machine-generated market-data / press-release copy
#: ``aggregator`` republishes other outlets' articles — its "coverage" is already in the cluster
#: ``research``   a journal or preprint server. MBFC rates these ``Pro-Science``, a category it
#:               states is distinct from the left-right scale, so a blank lean here is SOURCED
#: ``forum``      user-generated posts, not reporting
#: ``org``        an organisation publishing its own announcements
KINDS = ("wire", "aggregator", "research", "forum", "org")

#: Sentinel for "not in the resolve cache" — distinct from a cached ``None``, which is a real and
#: very common answer (most feed publishers are unknown to the registry). A plain ``.get(text)``
#: would re-resolve every unknown name on every call, which is the majority of them.
_MISS = object()

#: Upper bound on the per-registry resolve memo. See ``OutletRegistry.resolve``.
_RESOLVE_CACHE_MAX = 100_000

#: Kinds removed from clustering entirely. Deliberately narrower than :data:`KINDS`: an aggregator's
#: articles ARE other outlets' articles, so counting one as a publisher double-counts coverage the
#: cluster already holds. A journal paper or an NGO release is original content — debatable, so it
#: is classified and left in.
EXCLUDED_KINDS = ("wire", "aggregator")


@dataclass(frozen=True)
class Outlet:
    """A resolved outlet: the canonical display name, its AllSides lean in ``[-2, 2]`` —
    ``NaN`` for a LOCALITY-ONLY row (identity + home are curated facts while the lean stays
    honestly unrated until a defensible public rating is sourced; locality must never require
    guessing a lean — L2.2) — and, Location Intelligence Phase 1, its home locality. Locality
    fields are curated facts (publisher-level, never inferred) and default to ``None`` when
    uncurated, so pre-existing callers and rows are untouched. The dataclass grows by optional
    columns, never by redesign — future publisher metadata (factuality, ownership, transparency)
    follows the same pattern."""
    canonical: str
    lean: float
    country: "str | None" = None    # ISO 3166-1 alpha-2 home country
    region: "str | None" = None     # state / province / ADM1 display name
    city: "str | None" = None
    scope: "str | None" = None      # international | national | regional | local | hyperlocal
    kind: "str | None" = None       # None = a news outlet | "wire" = machine-generated feed
    credibility: "str | None" = None    # high | medium | low — see CREDIBILITY / :func:`is_low_credibility`
    # The RATER'S OWN factuality verdict, on the rater's own scale — see FACTUALITY below. Carried
    # only: nothing reads it yet. `credibility` remains the coarse 3-level field the clustering
    # vote-gate uses, and the two are deliberately not derived from each other in this phase, so
    # writing factuality cannot move a single cluster.
    factuality: "str | None" = None
    factuality_source: "str | None" = None   # who said so — mandatory whenever `factuality` is set
    factuality_asof: "str | None" = None     # WHEN it was read (ISO date) — also mandatory
    # Controlling-owner type — see OWNERSHIPS. Same sourced-or-absent contract as factuality.
    ownership: "str | None" = None
    ownership_source: "str | None" = None
    ownership_asof: "str | None" = None
    # The controlling entity by NAME ("Fox Corporation", "Scott Trust Limited"), display text.
    # Optional even when the type is set: a type can be public knowledge while the exact entity
    # is mid-restructuring (CNN, 2026) — then the type ships and the name stays blank rather
    # than stale. Feeds the About block's `parent` as its CURATED candidate (publisher_metadata),
    # so a Wikidata parent only ever fills a gap this column left.
    ownership_owner: "str | None" = None


def _fold(text: str) -> str:
    """Lower-case and fold accents to their base letters: ``Clarín`` → ``clarin``, ``RTÉ`` → ``rte``.

    Both keys used to lower-case and then strip anything non-alphanumeric, which DELETES an accented
    letter rather than folding it. Two consequences, and the second is worse:

    * a feed sending the unaccented spelling of an accented masthead missed entirely —
      ``Clarín`` keyed as ``clarn`` while ``Clarin`` keyed as ``clarin``. Self-consistent, so
      resolution "worked", and the form a wire service actually sends never matched;
    * letters vanishing can make two different outlets collide. ``RTÉ`` lost its ``É`` and became
      ``rt`` — Ireland's public broadcaster landing on Russia Today's alias. Caught by
      ``lint_registry``'s duplicate_alias check when the alias was added, not by anything at
      runtime, which is the argument for that check existing at all."""
    return "".join(c for c in unicodedata.normalize("NFKD", str(text).lower())
                   if not unicodedata.combining(c))


def _name_key(text: str) -> str:
    """Comparison key for a *name* form: drop parentheticals, lower-case, fold accents, drop a
    leading ``the``, strip to alphanumerics. ``"The New York Times"`` and
    ``"New York Times (News)"`` → ``newyorktimes``."""
    s = _fold(_PARENS.sub(" ", str(text))).strip()
    if s.startswith("the "):
        s = s[4:]
    return _NONALNUM.sub("", s)


def _full_key(text: str) -> str:
    """Comparison key that KEEPS a parenthetical. ``"The Star (Malaysia)"`` → ``starmalaysia``.

    :func:`_name_key` drops parentheticals so a corpus label like ``"Fox News (Online News)"``
    reaches Fox News. That is right for a SUFFIX and wrong for a DISAMBIGUATOR: a canonical named
    ``The Star (Malaysia)`` normalises to the bare word ``star``, so it claimed every feed's bare
    "The Star" — including Toronto's, mislabelling a Canadian paper as Malaysian and its lean as
    +2 instead of 0. Measured in production: 4 articles arrived under the bare name.

    A canonical carrying a parenthetical is registered under THIS key only, so it answers to its
    full name and to its explicit aliases, and the generic word is left unclaimed."""
    s = _fold(text).strip()
    if s.startswith("the "):
        s = s[4:]
    return _NONALNUM.sub("", s)


def _host_of(text: str) -> str:
    """Bare host for a *domain/URL* form: handles an optional scheme, a path with no scheme, and
    strips userinfo / port / a leading ``www.``. ``"https://www.BBC.co.uk/news"`` → ``bbc.co.uk``."""
    s = str(text).strip()
    netloc = urlsplit(s).netloc if "://" in s else s.split("/", 1)[0]
    host = netloc.split("@")[-1].split(":", 1)[0].strip().lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def _looks_like_host(text: str) -> bool:
    """Whether ``text`` is a domain/URL rather than a display name. Domains never contain spaces;
    a display name that happens to contain a dot (rare) still routes to the name path."""
    s = str(text).strip()
    if "://" in s:
        return True
    if " " in s:
        return False
    return bool(re.search(r"[a-z0-9-]\.[a-z]{2,}", s.lower()))


def _domain_suffixes(host: str) -> List[str]:
    """Progressively shorter registrable-domain candidates, so a subdomain matches the base:
    ``edition.cnn.com`` → ``["edition.cnn.com", "cnn.com", "com"]``."""
    labels = [l for l in host.split(".") if l]
    return [".".join(labels[i:]) for i in range(len(labels))]


class OutletRegistry:
    """An immutable set of outlets with alias resolution. Build via :meth:`load`."""

    def __init__(self, outlets: List[Outlet], aliases: Dict[str, str]):
        # `outlets` are the distinct canonical outlets; `aliases` maps every lookup key
        # (name keys AND domain keys) to a canonical name.
        self._outlets: Dict[str, Outlet] = {o.canonical: o for o in outlets}
        self._by_name: Dict[str, str] = {}       # name key (parentheticals dropped) -> canonical
        self._by_full: Dict[str, str] = {}       # full key (parentheticals KEPT)    -> canonical
        self._by_domain: Dict[str, str] = {}     # domain key -> canonical
        # Per-instance memo for `resolve`. On the instance rather than a module global so that a
        # reloaded registry starts empty — a curation change can never be served from a stale memo.
        self._resolve_cache: Dict[str, Optional[Outlet]] = {}
        # Per-instance memo for `factuality_record`, keyed by CANONICAL name (see that method):
        # building one costs a walk of the whole domain index, and a story asks per article.
        self._factuality_cache: Dict[str, dict] = {}
        for key, canonical in aliases.items():
            if _looks_like_host(key):
                self._by_domain[_host_of(key)] = canonical
                continue
            # A name form carrying a parenthetical is registered ONLY under its full key. For a
            # canonical that is the whole point — `The Star (Malaysia)` must not claim the bare word
            # `star` and mislabel Toronto's paper. For an ALIAS it costs nothing, since an alias
            # with a parenthetical is written to match one specific form.
            self._by_full[_full_key(key)] = canonical
            if "(" not in key:
                self._by_name[_name_key(key)] = canonical

    # -- construction ----------------------------------------------------- #
    @classmethod
    def load(cls, path: "str | None" = None) -> "OutletRegistry":
        """Load the registry CSV (defaults to the bundled ``data/outlet_registry.csv``).

        ``#`` lines and blanks are skipped. Each row is ``canonical, lean, aliases`` plus the
        optional Phase-1 locality columns ``country, region, city, scope``, then ``kind`` and
        ``credibility`` (missing / blank → ``None`` — a two-column legacy file still loads
        unchanged, and so does every row written before a column existed).
        ``aliases`` is ``|``-separated. The canonical name is itself registered as a lookup key."""
        path = path or _DATA
        outlets: List[Outlet] = []
        aliases: Dict[str, str] = {}

        def _opt(row: list, i: int) -> "str | None":
            v = row[i].strip() if len(row) > i and row[i] and row[i].strip() else None
            return v

        with open(path, encoding="utf-8") as f:
            reader = csv.reader(l for l in f if l.strip() and not l.lstrip().startswith("#"))
            header = next(reader, None)            # skip the column header
            for row in reader:
                if len(row) < 2 or not row[0].strip():
                    continue
                canonical = row[0].strip()
                # Blank lean = a deliberate locality-only row (unrated -> NaN, the same "unknown"
                # convention the scorer already speaks). Garbage still raises — fail loudly.
                raw_lean = row[1].strip()
                lean = float(raw_lean) if raw_lean else float("nan")
                cred = _opt(row, 8)
                fact = _opt(row, 9)
                own = _opt(row, 12)
                outlets.append(Outlet(canonical=canonical, lean=lean,
                                      country=_opt(row, 3), region=_opt(row, 4),
                                      city=_opt(row, 5), scope=_opt(row, 6),
                                      kind=_opt(row, 7),
                                      credibility=cred.lower() if cred else None,
                                      factuality=fact.lower() if fact else None,
                                      factuality_source=_opt(row, 10),
                                      factuality_asof=_opt(row, 11),
                                      ownership=own.lower() if own else None,
                                      ownership_source=_opt(row, 13),
                                      ownership_asof=_opt(row, 14),
                                      ownership_owner=_opt(row, 15)))
                aliases[canonical] = canonical      # the name itself resolves
                if len(row) >= 3 and row[2].strip():
                    for alias in row[2].split("|"):
                        alias = alias.strip()
                        if alias:
                            aliases[alias] = canonical
        return cls(outlets, aliases)

    # -- resolution ------------------------------------------------------- #
    def resolve(self, text: "str | None") -> Optional[Outlet]:
        """Resolve any form (name / domain / URL / corpus variant) to an :class:`Outlet`, or
        ``None`` if unknown. Domain forms match by registrable-domain suffix (subdomain-tolerant).

        **Memoized per registry instance.** Resolution is a pure function of the input string and
        the registry's contents, and the contents never change after ``load`` — so the same name
        can only ever produce the same answer, and remembering it changes nothing but the cost.

        Measured, story clustering at 20,000 articles: **60,400 calls over 400 distinct publisher
        strings**, a 151x waste factor. Three of those calls are per article by construction —
        ``is_wire``, ``is_aggregator`` and ``is_low_credibility`` each resolve independently — and
        each one pays ``_fold`` (NFKD normalize + a combining-mark filter + a join) twice, once for
        ``_full_key`` and once for ``_name_key``. cProfile put resolve at 10% of a whole build.

        The cache lives on the INSTANCE, not in a module global: a reloaded registry is a new
        object and starts empty, so a curation change can never be served from a stale memo."""
        if not text:
            return None
        hit = self._resolve_cache.get(text, _MISS)
        if hit is not _MISS:
            return hit
        out = self._resolve_uncached(text)
        # Bounded, because the key space is FEED-CONTROLLED: publisher strings arrive from remote
        # sources and a hostile or merely broken one could otherwise grow this without limit. The
        # cap is far above any real catalog's distinct-publisher count (production: ~5,200), so in
        # practice it never evicts; it exists so that "never" is a property rather than a hope.
        if len(self._resolve_cache) < _RESOLVE_CACHE_MAX:
            self._resolve_cache[text] = out
        return out

    def _resolve_uncached(self, text: str) -> Optional[Outlet]:
        if _looks_like_host(text):
            host = _host_of(text)
            for cand in _domain_suffixes(host):
                canonical = self._by_domain.get(cand)
                if canonical:
                    return self._outlets[canonical]
            # a bare word that looked host-ish but isn't a known domain: fall through to name
        # Full key first: it is the exact form, so `The Star (Malaysia)` and a corpus variant of it
        # both land. Then the parenthetical-stripped key, which is what makes
        # `Fox News (Online News)` reach Fox News — but which only DISAMBIGUATED canonicals decline
        # to register, so a bare `The Star` finds nothing rather than the wrong newspaper.
        canonical = self._by_full.get(_full_key(text)) or self._by_name.get(_name_key(text))
        return self._outlets.get(canonical) if canonical else None

    def canonical(self, text: "str | None") -> Optional[str]:
        """The canonical outlet name for ``text``, or ``None``."""
        o = self.resolve(text)
        return o.canonical if o else None

    def domains(self, canonical: str) -> List[str]:
        """The curated domains for one canonical outlet, shortest first.

        Read back out of the domain index rather than kept on :class:`Outlet`, so the aliases the
        registry actually resolves on and the domains a caller can show are the same list by
        construction — a second copy could disagree with the index after an edit.

        Shortest first because the registrable domain is what identifies the masthead:
        ``bbc.com`` before ``bbc.co.uk``, and a rating search wants the former."""
        return sorted((d for d, c in self._by_domain.items() if c == canonical), key=lambda d: (len(d), d))

    def rating_url(self, outlet: "Outlet | None") -> "str | None":
        """Where to read ``outlet``'s factuality verdict at the rater that issued it.

        ``None`` unless the outlet actually carries a verdict AND its source is one we know how to
        link — never a bare link to the rater's home page, which would imply a rating exists for
        an outlet that has none."""
        if outlet is None or not outlet.factuality or not outlet.factuality_source:
            return None
        template = FACTUALITY_SOURCE_SEARCH.get(outlet.factuality_source)
        if not template:
            return None
        doms = self.domains(outlet.canonical)
        return template.format(domain=doms[0]) if doms else None

    def lean(self, text: "str | None") -> float:
        """The AllSides lean for ``text``, or ``NaN`` if unknown OR the row is locality-only
        (unrated) — either way the engine excludes it from lean-based metrics, never defaults."""
        o = self.resolve(text)
        return o.lean if o else float("nan")

    def is_wire(self, text: "str | None") -> bool:
        """Whether ``text`` resolves to a machine-generated feed rather than a news outlet.

        A ``wire`` row means the source publishes auto-generated market-data and press-release
        copy — "X LLC Makes New Investment in Y Inc", "Z Posts Quarterly Earnings Results". Such
        copy clusters *correctly* (a template repeated 115 times really is about one template), so
        no clustering signal can find it: measured, ``geoCoherence`` rates it perfectly coherent
        and articles-per-publisher was tested against the whole catalog and rejected at 0%
        precision and 0% recall (docs/PUBLISHER_CONCENTRATION_EVALUATION.md).

        It is an identity fact about the SOURCE, so it belongs here — explicit, auditable, one
        cell to reverse, and incapable of misfiring on a government-funding story the way a
        threshold in the clustering path would. Unknown outlets are never wire: absence of a row
        means unrated, not disqualified."""
        o = self.resolve(text)
        return bool(o and o.kind == "wire")

    def is_wire_url(self, url: "str | None") -> bool:
        """Whether a URL's HOST is a machine-generated feed — the same question as :meth:`is_wire`,
        asked of the other string the caller holds.

        Needed because the two strings DISAGREE, measured on the live catalog 2026-08-08. A
        syndicated obituary arrives with the masthead as its publisher name and the feed's own
        subdomain in its URL: 499 of 671 obituary articles were stored as ``The Oregonian`` /
        ``The Express-Times`` with an ``obits.*`` URL. Curating the feed reached the other 172 and
        removed no story at all, because those 14 clusters are built entirely from the
        masthead-labelled half. A registry row cannot fix that — the publisher string is not wrong,
        it is just not the whole identity.

        Resolves the HOST, never the URL. ``resolve`` memoizes per input string, and a catalog
        holds ~5,000 distinct hosts against ~34,000 distinct URLs — passing the URL would defeat
        the memo and put a full resolve (two ``_fold`` passes each) on every article in the build,
        in a stage cProfile already puts at 10% of it.

        Strictly narrowing, by construction: it can only exclude an article SERVED FROM a wire
        host, and an article served from prnewswire.com is a press release whatever its byline
        says. No row is reachable this way that was not already curated as a machine-generated
        feed."""
        if not url or not _looks_like_host(url):
            return False
        return self.is_wire(_host_of(url))

    def is_aggregator(self, text: "str | None") -> bool:
        """Whether ``text`` republishes other outlets rather than reporting.

        Worth its own predicate because an aggregator is the one non-newsroom source that can be
        RATED: MBFC gives Google News a Left-Center lean, derived from the sources it surfaces. The
        rating is real and voting it would still be wrong — the outlets it mirrors are already in
        the cluster, so its vote is a second copy of theirs."""
        o = self.resolve(text)
        return bool(o and o.kind == "aggregator")

    def credibility(self, text: "str | None") -> Optional[str]:
        """The curated credibility verdict for ``text`` — ``high`` / ``medium`` / ``low``, or
        ``None`` when the outlet is unknown OR the column is uncurated."""
        o = self.resolve(text)
        return o.credibility if o else None

    def is_low_credibility(self, text: "str | None") -> bool:
        """Whether ``text`` resolves to an outlet whose LEAN should not be voted at full weight.

        This column exists because a lean and a credibility verdict are different facts, and the
        file could previously express only one of them. Eight outlets — Xinhua, Global Times, RT,
        Sputnik, TASS, The Economic Times, Daily Star (UK), GB News — have a published MBFC lean and
        an MBFC ``Questionable`` / ``Low Credibility`` verdict beside it. With one column the only
        honest option was to withhold the lean entirely, which lost a true fact to avoid a
        misleading one. With two, the lean is recorded AND the caveat travels with it.

        The bar is the RATER's own verdict, never an impression of the outlet: state-aligned outlets
        MBFC rates at Medium or better are rated and voted normally here (Daily Sabah, Ahram Online,
        Anadolu Agency). Without that constraint the column would drift into "outlets I distrust",
        which is the fabrication this file exists to prevent, pointed the other way.

        Unknown and uncurated outlets are never low: absence of a verdict is not a verdict."""
        return self.credibility(text) == "low"

    def factuality(self, text: "str | None") -> Optional[str]:
        """The RATER'S factuality verdict for ``text`` — one of :data:`FACTUALITY`, or ``None``
        when the outlet is unknown OR the column is uncurated.

        The rater's own six-level scale, never collapsed: see :data:`FACTUALITY`. ``None`` means
        *nobody we carry has rated this outlet*, which is the normal case (479 of 609 rows), and
        never "middling" — the same L2.2 rule the lean follows."""
        o = self.resolve(text)
        return o.factuality if o else None

    def factuality_record(self, text: "str | None") -> Optional[dict]:
        """``text``'s factuality verdict WITH the provenance needed to attribute it, or ``None``.

        ``{value, source, asOf, ratingUrl}`` — the same object the publisher profile publishes,
        so one client type serves both surfaces and neither can render a verdict without saying
        who issued it and when. Never a bare level: presented alone a third party's rating reads
        as ours, and read a year later it asserts the rater still says so.

        Callers are responsible for the publication decision (:func:`factuality_published`); this
        method answers what the registry HOLDS, which is a different question."""
        o = self.resolve(text)
        if o is None or not o.factuality:
            return None
        # Memoized on the canonical, not the input string: `rating_url` walks the whole domain
        # index and sorts it, and a story's coverage asks for the same handful of outlets over and
        # over. The key space is the registry's own outlets (bounded by the file), not the
        # feed-controlled publisher strings `_resolve_cache` guards against.
        record = self._factuality_cache.get(o.canonical)
        if record is None:
            record = {"value": o.factuality, "source": o.factuality_source,
                      "asOf": o.factuality_asof, "ratingUrl": self.rating_url(o)}
            self._factuality_cache[o.canonical] = record
        return record

    def outlets(self) -> List[Outlet]:
        """All distinct outlets: rated ones ordered by lean then name, locality-only (NaN lean)
        rows deterministically last by name (NaN sort keys would otherwise be order-unstable)."""
        return sorted(self._outlets.values(),
                      key=lambda o: (math.isnan(o.lean),
                                     0.0 if math.isnan(o.lean) else o.lean, o.canonical))

    def __len__(self) -> int:
        return len(self._outlets)

    def __contains__(self, text) -> bool:
        return self.resolve(text) is not None


_DEFAULT: "OutletRegistry | None" = None


def default_registry() -> OutletRegistry:
    """The process-wide registry loaded from the bundled data (built once, then cached)."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = OutletRegistry.load()
    return _DEFAULT


def resolve(text: "str | None") -> Optional[Outlet]:
    """Convenience: resolve against the default registry."""
    return default_registry().resolve(text)


def is_wire(text: "str | None") -> bool:
    """Convenience: :meth:`OutletRegistry.is_wire` against the default registry."""
    return default_registry().is_wire(text)


def is_wire_url(url: "str | None") -> bool:
    """Convenience: :meth:`OutletRegistry.is_wire_url` against the default registry."""
    return default_registry().is_wire_url(url)


def is_aggregator(text: "str | None") -> bool:
    """Convenience: :meth:`OutletRegistry.is_aggregator` against the default registry."""
    return default_registry().is_aggregator(text)


#: Reader-facing SOURCE TYPE, projected from the curated :data:`KINDS` column. The Stories "Type"
#: filter is this and nothing else — it is a curated identity fact about the publisher, one cell
#: per row to reverse, never inferred from an article's text, topic or transport.
#:
#: The mapping is the registry's own vocabulary, not a new classification:
#:
#:   ``news``       a curated row with no ``kind`` — the column's documented meaning of a news outlet
#:   ``research``   ``kind = research``  (a journal or preprint server)
#:   ``community``  ``kind = forum``     (user-generated posts, not reporting)
#:
#: ``org``, ``wire`` and ``aggregator`` map to NOTHING. An NGO's own announcement is not reporting,
#: research or a community post, and saying otherwise would be inventing a verdict; the other two
#: are :data:`EXCLUDED_KINDS` and never reach a story at all.
_TYPE_OF_KIND = {"": "news", "research": "research", "forum": "community"}

#: The three the UI offers, in the order it offers them.
SOURCE_TYPES = ("news", "research", "community")


def source_type(text: "str | None") -> Optional[str]:
    """The reader-facing source type of ``text``, or ``None`` when the registry does not say.

    ``None`` covers two genuinely different situations, and neither may be reported as a type:
    an outlet with a ``kind`` outside the mapping above, and — far more often — an outlet the
    registry has never heard of. Most feed publishers are unknown to it, and absence of a row is
    not evidence of anything, exactly as it is not for :func:`is_wire`. An unclassified publisher
    therefore matches no type rather than defaulting into ``news``, so the filter can only ever
    narrow to sources somebody actually curated.
    """
    outlet = resolve(text)
    if outlet is None:
        return None
    return _TYPE_OF_KIND.get((outlet.kind or "").strip().lower())


def credibility(text: "str | None") -> Optional[str]:
    """Convenience: :meth:`OutletRegistry.credibility` against the default registry."""
    return default_registry().credibility(text)


def ownership(text: "str | None") -> Optional[str]:
    """The curated controlling-owner type for ``text`` — one of :data:`OWNERSHIPS`, or ``None``
    when the outlet is unknown OR the column is uncurated (unknown, never ``other``)."""
    o = default_registry().resolve(text)
    return o.ownership if o else None


def factuality(text: "str | None") -> Optional[str]:
    """Convenience: :meth:`OutletRegistry.factuality` against the default registry."""
    return default_registry().factuality(text)


def factuality_record(text: "str | None") -> Optional[dict]:
    """Convenience: :meth:`OutletRegistry.factuality_record` against the default registry."""
    return default_registry().factuality_record(text)


def factuality_published() -> bool:
    """Whether THIS DEPLOYMENT publishes third-party factuality verdicts. **Default OFF.**

    Holding a verdict and publishing it are different decisions (docs/SIGNAL_INTEGRITY.md). The
    ratings are MBFC's commercial product and we hold no licence to redistribute them, so
    publication is an explicit operator act — ``RWE_PUBLIC_FACTUALITY=1`` — rather than a
    consequence of the data existing in the registry. Curation, provenance and linting all keep
    working while it is off; only publication stops.

    The SWITCH is defined here, beside the data it governs, so there is exactly one env read and
    one spelling of it. The GATE stays at each serializer — `publisher_service` for the profile,
    `story_service._coverage` for a story's rows — because a client-side hide would still ship
    the rater's data to anyone reading the payload."""
    return (os.environ.get(_PUBLIC_FACTUALITY_ENV) or "").strip().lower() in _PUBLISH_TRUE


def is_low_credibility(text: "str | None") -> bool:
    """Convenience: :meth:`OutletRegistry.is_low_credibility` against the default registry."""
    return default_registry().is_low_credibility(text)


def lint_registry(path: "str | None" = None) -> List[dict]:
    """Read-only well-formedness check on the registry CSV. Returns a list of issue dicts
    ``{severity, code, line, message}`` (empty ⇒ clean); NEVER raises on a malformed file (that is
    the point) and never mutates anything. Mirrors :meth:`OutletRegistry.load`'s parsing (``#`` and
    blank lines skipped, the first content line is the column header) and checks:

      * ``malformed_row``      — fewer than two columns, or a blank canonical
      * ``invalid_lean``       — column 2 is not a finite number in ``[-2, 2]``
      * ``duplicate_canonical``— the same canonical name defined on two rows
      * ``duplicate_alias``    — one alias key mapped to two different canonicals (resolution would
                                 depend on row order — a real bug)
      * ``repeated_alias_in_row`` (warning) — the same alias listed twice in one row's alias list
      * ``invalid_kind``       — column 8 is neither blank nor one of :data:`KINDS`. This column was
        unvalidated until it grew past a single value, and a typo in it silently un-excludes a wire.
      * ``invalid_credibility``— column 9 is neither blank nor one of :data:`CREDIBILITY`
      * ``unrated_low_credibility`` (warning) — a ``low`` row with no lean. Legal, but it means the
        row is asserting a caveat about a rating that is not there, which is almost always a
        half-finished edit: the point of ``low`` is to let the LEAN be recorded.
      * factuality columns (10–12) and ownership columns (13–15) each enforce the sourced-or-absent
        contract: value in the closed vocabulary, source mandatory and from the closed source set,
        asof mandatory, ISO-dated, and never in the future.
    """
    path = path or _DATA
    issues: List[dict] = []
    seen_canonical: Dict[str, int] = {}     # canonical -> first line number
    alias_owner: Dict[str, str] = {}        # normalized alias key -> canonical
    with open(path, encoding="utf-8") as f:
        lines = list(enumerate(f, 1))
    header_skipped = False
    for lineno, raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not header_skipped:              # the loader consumes the first content line as the header
            header_skipped = True
            continue
        cells = next(csv.reader([raw]), [])
        if len(cells) < 2 or not cells[0].strip():
            issues.append({"severity": "error", "code": "malformed_row", "line": lineno,
                           "message": f"line {lineno}: expected 'canonical,lean,aliases', got {raw.strip()!r}"})
            continue
        canonical = cells[0].strip()
        raw_lean = cells[1].strip()
        # A BLANK lean is legal: a locality-only row (unrated). A non-blank lean must be a
        # finite number in [-2, 2] — "NaN"/garbage is a data error, not a way to say unrated.
        if raw_lean:
            try:
                lean = float(raw_lean)
                if not math.isfinite(lean) or not (-2.0 <= lean <= 2.0):
                    raise ValueError
            except ValueError:
                issues.append({"severity": "error", "code": "invalid_lean", "line": lineno,
                               "message": f"line {lineno} ({canonical}): lean {raw_lean!r} "
                                          "is not a finite number in [-2, 2] (blank = unrated)"})
        kind = cells[7].strip().lower() if len(cells) > 7 else ""
        if kind and kind not in KINDS:
            issues.append({"severity": "error", "code": "invalid_kind", "line": lineno,
                           "message": f"line {lineno} ({canonical}): kind {kind!r} is not one of "
                                      f"{'/'.join(KINDS)} (blank = an ordinary news outlet)"})
        cred = cells[8].strip().lower() if len(cells) > 8 else ""
        if cred and cred not in CREDIBILITY:
            issues.append({"severity": "error", "code": "invalid_credibility", "line": lineno,
                           "message": f"line {lineno} ({canonical}): credibility {cred!r} is not one "
                                      f"of {'/'.join(CREDIBILITY)} (blank = uncurated)"})
        elif cred == "low" and not raw_lean:
            issues.append({"severity": "warning", "code": "unrated_low_credibility", "line": lineno,
                           "message": f"line {lineno} ({canonical}): credibility 'low' with no lean — "
                                      "the point of 'low' is to let the lean be recorded WITH the caveat"})
        fact = cells[9].strip().lower() if len(cells) > 9 else ""
        fact_src = cells[10].strip().lower() if len(cells) > 10 else ""
        if fact and fact not in FACTUALITY:
            issues.append({"severity": "error", "code": "invalid_factuality", "line": lineno,
                           "message": f"line {lineno} ({canonical}): factuality {fact!r} is not one of "
                                      f"{'/'.join(FACTUALITY)} (blank = unrated)"})
        # PROVENANCE IS MANDATORY. An unattributed rating is indistinguishable from a guess, and
        # this file's whole discipline is that a rating is either sourced or absent (L2.2). The
        # check runs whether or not the verdict itself parsed, so a row cannot lose its attribution
        # by also having a typo'd level.
        if fact and not fact_src:
            issues.append({"severity": "error", "code": "factuality_without_source", "line": lineno,
                           "message": f"line {lineno} ({canonical}): factuality {fact!r} with no "
                                      "factuality_source — an unattributed rating cannot be told "
                                      "apart from a guess"})
        if fact_src and fact_src not in FACTUALITY_SOURCES:
            issues.append({"severity": "error", "code": "invalid_factuality_source", "line": lineno,
                           "message": f"line {lineno} ({canonical}): factuality_source {fact_src!r} "
                                      f"is not one of {'/'.join(FACTUALITY_SOURCES)}"})
        if fact_src and not fact:
            issues.append({"severity": "warning", "code": "source_without_factuality", "line": lineno,
                           "message": f"line {lineno} ({canonical}): factuality_source {fact_src!r} "
                                      "with no factuality — a half-finished edit"})
        # WHEN is as mandatory as WHO, and for the same reason. A rater revises; this file has no
        # refresh mechanism; so an undated verdict shown under the rater's name asserts that they
        # still say it. Checked independently of the level and the source so a row cannot lose its
        # date by also having some other defect.
        fact_asof = cells[11].strip() if len(cells) > 11 else ""
        if fact and not fact_asof:
            issues.append({"severity": "error", "code": "factuality_without_asof", "line": lineno,
                           "message": f"line {lineno} ({canonical}): factuality {fact!r} with no "
                                      "factuality_asof — an undated rating cannot be told apart "
                                      "from a current one"})
        if fact_asof:
            bad = not _ASOF_RE.match(fact_asof)
            if not bad:
                try:
                    when = datetime.date(*(int(p) for p in fact_asof.split("-")))
                except ValueError:
                    bad = True
                else:
                    # A retrieval date in the future is always a typo, and it is the one error that
                    # makes a stale verdict look permanently fresh.
                    if when > datetime.date.today():
                        issues.append({"severity": "error", "code": "factuality_asof_in_future",
                                       "line": lineno,
                                       "message": f"line {lineno} ({canonical}): factuality_asof "
                                                  f"{fact_asof!r} is in the future"})
            if bad:
                issues.append({"severity": "error", "code": "invalid_factuality_asof", "line": lineno,
                               "message": f"line {lineno} ({canonical}): factuality_asof "
                                          f"{fact_asof!r} is not an ISO date (YYYY-MM-DD)"})
        if fact_asof and not fact:
            issues.append({"severity": "warning", "code": "asof_without_factuality", "line": lineno,
                           "message": f"line {lineno} ({canonical}): factuality_asof {fact_asof!r} "
                                      "with no factuality — a half-finished edit"})
        # Ownership (columns 13–15) carries the same discipline as factuality, for the same reason:
        # a controlling-owner type is a claim about a named news organisation, so it is either
        # sourced and dated or absent. One deliberate difference: no future/format leniency —
        # the asof checks are shared verbatim.
        own = cells[12].strip().lower() if len(cells) > 12 else ""
        own_src = cells[13].strip().lower() if len(cells) > 13 else ""
        own_asof = cells[14].strip() if len(cells) > 14 else ""
        if own and own not in OWNERSHIPS:
            issues.append({"severity": "error", "code": "invalid_ownership", "line": lineno,
                           "message": f"line {lineno} ({canonical}): ownership {own!r} is not one of "
                                      f"{'/'.join(OWNERSHIPS)} (blank = uncurated)"})
        if own and not own_src:
            issues.append({"severity": "error", "code": "ownership_without_source", "line": lineno,
                           "message": f"line {lineno} ({canonical}): ownership {own!r} with no "
                                      "ownership_source — an unattributed classification cannot "
                                      "be told apart from a guess"})
        if own_src and own_src not in OWNERSHIP_SOURCES:
            issues.append({"severity": "error", "code": "invalid_ownership_source", "line": lineno,
                           "message": f"line {lineno} ({canonical}): ownership_source {own_src!r} "
                                      f"is not one of {'/'.join(OWNERSHIP_SOURCES)}"})
        if own_src and not own:
            issues.append({"severity": "warning", "code": "source_without_ownership", "line": lineno,
                           "message": f"line {lineno} ({canonical}): ownership_source {own_src!r} "
                                      "with no ownership — a half-finished edit"})
        if own and not own_asof:
            issues.append({"severity": "error", "code": "ownership_without_asof", "line": lineno,
                           "message": f"line {lineno} ({canonical}): ownership {own!r} with no "
                                      "ownership_asof — an undated classification cannot be told "
                                      "apart from a current one"})
        if own_asof:
            bad = not _ASOF_RE.match(own_asof)
            if not bad:
                try:
                    when = datetime.date(*(int(p) for p in own_asof.split("-")))
                except ValueError:
                    bad = True
                else:
                    if when > datetime.date.today():
                        issues.append({"severity": "error", "code": "ownership_asof_in_future",
                                       "line": lineno,
                                       "message": f"line {lineno} ({canonical}): ownership_asof "
                                                  f"{own_asof!r} is in the future"})
            if bad:
                issues.append({"severity": "error", "code": "invalid_ownership_asof", "line": lineno,
                               "message": f"line {lineno} ({canonical}): ownership_asof "
                                          f"{own_asof!r} is not an ISO date (YYYY-MM-DD)"})
        # An owner NAME with no owner TYPE is a half-finished edit: the name is only ever shown
        # under the type's provenance (source + asof), so alone it would be an unsourced claim.
        own_owner = cells[15].strip() if len(cells) > 15 else ""
        if own_owner and not own:
            issues.append({"severity": "warning", "code": "owner_without_ownership", "line": lineno,
                           "message": f"line {lineno} ({canonical}): ownership_owner {own_owner!r} "
                                      "with no ownership type — the name has no provenance without it"})
        if canonical in seen_canonical:
            issues.append({"severity": "error", "code": "duplicate_canonical", "line": lineno,
                           "message": f"line {lineno}: canonical {canonical!r} already defined at "
                                      f"line {seen_canonical[canonical]}"})
        else:
            seen_canonical[canonical] = lineno
        alias_cell = cells[2] if len(cells) >= 3 else ""
        local: set = set()
        for alias in alias_cell.split("|"):
            alias = alias.strip()
            if not alias:
                continue
            if alias in local:
                issues.append({"severity": "warning", "code": "repeated_alias_in_row", "line": lineno,
                               "message": f"line {lineno} ({canonical}): alias {alias!r} repeated in the row"})
            local.add(alias)
            # Mirrors the loader's registration rule exactly (see OutletRegistry.__init__): a form
            # with a parenthetical is keyed on its FULL text, so two disambiguated names that share
            # a bare word are not reported as colliding when resolution keeps them apart.
            key = (_host_of(alias) if _looks_like_host(alias)
                   else _full_key(alias) if "(" in alias else _name_key(alias))
            if key in alias_owner and alias_owner[key] != canonical:
                issues.append({"severity": "error", "code": "duplicate_alias", "line": lineno,
                               "message": f"line {lineno}: alias {alias!r} maps to {canonical!r} but "
                                          f"already maps to {alias_owner[key]!r}"})
            else:
                alias_owner[key] = canonical
    return issues
