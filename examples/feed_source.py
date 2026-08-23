"""Live recommendation SOURCE — build the recommender's article catalog from the RSS ``FeedArticle``
catalog instead of the static qbias CSV / synthetic generator.

The smallest additive seam that swaps the *article source* without touching a single recommendation
algorithm: it exports ``FeedArticle`` rows to a **qbias-format CSV** that the EXISTING corpus builder
(``simulate_users.run(qbias=<csv>)``) reads unchanged. The recommender, health metrics, diversity,
and personalization then operate over live articles **exactly** as they do over the static qbias
catalog — same simulated population, same ``recommender_inputs``, same RWE models. Nothing here (or
in the engine) changes ranking, scoring, diversity, health, or personalization; the protected
simulator is reused as-is.

Enable with ``RWE_RECS_SOURCE=feed``. If the catalog is too small to simulate a population, the
caller falls back to the existing corpus (so enabling it before any RSS ingest is safe).
"""

from __future__ import annotations

import csv
import math
import re
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # import sibling modules
import corpus_health                 # shared freshness gate (fresh_articles) — metrics only

# Enough of a catalog to sample a population + build a non-degenerate click matrix. Configurable.
DEFAULT_MIN_ARTICLES = 50
# Column names chosen to match what ``catalog_from_qbias`` fuzzy-picks (headline / bias / outlet /
# tags). ``url`` is written too (the builder ignores it) so a future URL pass-through can recover the
# real publisher URL from the same CSV by row order. ``political`` (Commit R1) carries the scored
# article-level flag ("1"/"0"; "" = unknown → the loader derives from tags+url) so the corpus mask
# is real per-article classification, never assumed.
_COLUMNS = ["title", "source", "bias_rating", "tags", "url", "political", "country"]


def enabled() -> bool:
    """Whether the recommender should source its catalog from the RSS FeedArticle store."""
    return os.environ.get("RWE_RECS_SOURCE", "").strip().lower() in {"feed", "rss", "catalog"}


def _data_dir() -> str:
    return str(Path(__file__).resolve().parent.parent / "data")


def _bias_label(lean, center: float = 0.5) -> str:
    """Map a numeric outlet lean to the AllSides-style label ``catalog_from_qbias`` parses.

    FIVE-point since the fractional-leans work (docs/RECOMMENDATION_STRENGTH_SLIDER.md): a strong
    lean stays ``left``/``right``; a moderate one emits ``lean left``/``lean right``, so the
    ranking space keeps the grade instead of collapsing AllSides *Lean Left* outlets onto the same
    point as the far poles — the measured reason every distance-graded recommendation knob was
    inert. The full/lean boundary is **1.5** — the midpoint of the AllSides lattice this scale
    declares, where the registry (the scorer's single lean source) writes Lean Left/Right as ±1
    and Left/Right as ±2; a boundary derived from ``center`` (an early draft used
    ``(1+center)/2 = 0.75``) leaves the lean band EMPTY on that integer lattice and nothing ever
    grades in production. The sided/centre PARTITION is unchanged: every |v| >= center is still
    sided, so cross-cutting membership and the report's bucket shares are untouched; only the
    grade WITHIN the sided bucket is new. An unknown lean yields ``""`` — the builder then drops
    the row, exactly as it does for a qbias row with no resolvable bias."""
    if lean is None:
        return ""
    try:
        v = float(lean)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(v):
        return ""
    full = 1.5                       # midway between AllSides Lean (±1) and full (±2)
    if v <= -full:
        return "left"
    if v <= -center:
        return "lean left"
    if v >= full:
        return "right"
    if v >= center:
        return "lean right"
    return "center"


def _name_index():
    """ISO code -> the set of names that denote it, from location's ICU-backed tables. Built once."""
    global _NAME_INDEX
    try:
        return _NAME_INDEX
    except NameError:
        pass
    import location
    idx: dict = {}
    for name, iso in location._COUNTRY_NAMES.items():
        n = str(name).strip().lower()
        if len(n) >= 4:               # "usa"/"uk" are handled by the explicit aliases below
            idx.setdefault(n, str(iso).upper())
    for alias, iso in (("us", "US"), ("u.s.", "US"), ("usa", "US"), ("uk", "GB"), ("u.k.", "GB"),
                       ("uae", "AE"), ("eu", None)):
        if iso:
            idx.setdefault(alias, iso)
    _NAME_INDEX = idx
    return idx


#: Demonyms -> ISO. Curated, never derived by suffix rules: "Turkey"->"Turkish" and
#: "Netherlands"->"Dutch" have no common rule, and a guessed demonym is a silent mis-label.
#: Deliberately ABSENT: "english" (the language reading dominates, and England/Britain/UK/British
#: already cover GB), "korean" (bare, it does not choose between KR and KP — the compounds below
#: do), "georgian" and "jordanian"-style names whose non-country reading is at least as common.
_DEMONYMS = {
    "american": "US", "british": "GB", "scottish": "GB", "welsh": "GB", "irish": "IE",
    "canadian": "CA", "australian": "AU", "indian": "IN", "pakistani": "PK",
    "bangladeshi": "BD", "sri lankan": "LK", "nepali": "NP", "nepalese": "NP",
    "chinese": "CN", "japanese": "JP", "south korean": "KR", "north korean": "KP",
    "taiwanese": "TW", "thai": "TH", "vietnamese": "VN", "filipino": "PH", "indonesian": "ID",
    "malaysian": "MY", "singaporean": "SG", "russian": "RU", "ukrainian": "UA",
    "german": "DE", "french": "FR", "spanish": "ES", "portuguese": "PT", "italian": "IT",
    "dutch": "NL", "belgian": "BE", "swiss": "CH", "austrian": "AT", "swedish": "SE",
    "norwegian": "NO", "danish": "DK", "finnish": "FI", "greek": "GR", "turkish": "TR",
    "israeli": "IL", "palestinian": "PS", "saudi": "SA", "emirati": "AE", "egyptian": "EG",
    "iranian": "IR", "iraqi": "IQ", "syrian": "SY", "lebanese": "LB", "qatari": "QA",
    "nigerian": "NG", "kenyan": "KE", "ethiopian": "ET", "ghanaian": "GH",
    "south african": "ZA", "moroccan": "MA", "algerian": "DZ", "tunisian": "TN",
    "brazilian": "BR", "argentine": "AR", "argentinian": "AR", "chilean": "CL",
    "colombian": "CO", "peruvian": "PE", "venezuelan": "VE", "mexican": "MX", "cuban": "CU",
    "polish": "PL", "czech": "CZ", "hungarian": "HU", "romanian": "RO",
}

#: Phrases in which a demonym does NOT denote its country. The known-counterexample discipline
#: this repo already applies to lexicons: each entry earned its place by being a real phrase,
#: and the list is checked as a bigram around the match rather than by fuzzy scoring.
_DEMONYM_BLOCK = frozenset((
    "african american", "latin american", "native american", "south american",
    "north american", "central american", "american airlines", "american express",
    "american idol", "american football", "pan american",
    "nail polish", "shoe polish", "polish off", "polish up",
    "french fries", "french toast", "french open", "french door", "french bulldog",
    "dutch oven", "going dutch", "danish pastry", "chinese takeaway",
    "indian ocean", "russian roulette", "turkish delight", "greek yogurt",
    "swiss cheese", "swiss army", "spanish flu", "german shepherd",
))

_WORDS = re.compile(r"[^a-z0-9.]+")


def mentioned_countries(text: str) -> frozenset:
    """Countries NAMED in a piece of text, as ISO codes.

    N-gram lookup against the country-name index rather than 250 substring scans, so it stays
    cheap over a whole catalog. Matching is on word boundaries by construction (the text is
    tokenized first), so "Indianapolis" cannot match "India".

    KNOWN LIMITS, stated because they decide how far this signal can be trusted:
      * Demonyms ARE matched from a curated table ("Indian" -> IN), never derived by suffix
        rule, and suppressed inside known non-country phrases (_DEMONYM_BLOCK). Bare "Korean"
        and "English" are deliberately absent — see _DEMONYMS.
      * Short country names that are also ordinary words or place names elsewhere (Chad, Georgia,
        Jordan, Turkey, Guinea) WILL over-match. Whether that matters is measurable, not
        guessable — audit_country_rerank reports the per-country counts so the damage is visible.
      * A MENTION is not a subject: "unlike India, China…" names India while reporting on China.
        That is the comparative-mention failure this repo already documented at story level
        (docs/EVENT_IDENTITY_RUBRIC.md rule 1), reappearing here.
    """
    idx = _name_index()
    toks = [t for t in _WORDS.split((text or "").lower()) if t]
    out = set()
    for i, t in enumerate(toks):
        for k in (1, 2, 3):
            if i + k > len(toks):
                break
            gram = " ".join(toks[i:i + k])
            iso = idx.get(gram)
            if iso:
                out.add(iso)
            dem = _DEMONYMS.get(gram)
            if dem:
                # A demonym only counts when neither the phrase it starts nor the one it ends is a
                # known non-country reading ("African American", "nail polish", "French fries").
                before = " ".join(toks[i - 1:i + k]) if i else ""
                after = " ".join(toks[i:i + k + 1])
                if before not in _DEMONYM_BLOCK and after not in _DEMONYM_BLOCK:
                    out.add(dem)
    return frozenset(out)


def article_countries(a: dict, source: str = "union") -> frozenset:
    """The countries an article belongs to, as a SET — an article about India and Pakistan is in
    both, which a single label cannot express.

    ``source`` selects what counts as belonging:
      event      where the reported event happened (``eventCountries``) — content, and the
                 strictest reading of "an article about this country".
      mention    the country is NAMED in the headline or dek — content, broader, noisier.
      content    event | mention: content-level only, never the publisher's home.
      publisher  the outlet's home country — provenance, NOT what the article is about.
      union      content | publisher (the widest, and what shipped first).
    """
    ev = frozenset(str(c).strip().upper() for c in (a.get("eventCountries") or ())
                   if len(str(c).strip()) == 2 and str(c).strip().isalpha())
    if source == "event":
        return ev
    scored = a.get("scored") or {}
    pub = str(a.get("country") or scored.get("country") or "").strip().upper()
    pub = frozenset({pub}) if len(pub) == 2 and pub.isalpha() else frozenset()
    if source == "publisher":
        return pub
    men = mentioned_countries(f"{a.get('title') or scored.get('title') or ''} "
                              f"{a.get('description') or ''}")
    if source == "mention":
        return men
    if source == "content":
        return ev | men
    return ev | men | pub


def article_country(a: dict) -> str:
    """The country an article counts as being "from", upper-cased ISO alpha-2, or "" when unknown.

    Event geography first (``eventCountries`` — where the thing actually happened, the same signal
    Discover's and Stories' country facets use), publisher home second. The fallback is deliberate
    and load-bearing: event geography resolves for a minority of articles (the X6 audit measured
    ~18% located), so an event-only rule would make a reader's country preference inert on most of
    the catalog. The union is the honest reading of "news from India" — an incident in India, or
    an Indian outlet's reporting — and the two sources are counted separately by
    ``examples/audit_country_rerank.py`` so the split is visible rather than assumed.

    A multi-country event resolves to its first listed country: this returns the single label the
    rank nudge keys on, never a claim that the event happened in exactly one place.
    """
    for c in (a.get("eventCountries") or ()):
        s = str(c).strip().upper()
        if len(s) == 2 and s.isalpha():
            return s
    scored = a.get("scored") or {}
    s = str(a.get("country") or scored.get("country") or "").strip().upper()
    return s if len(s) == 2 and s.isalpha() else ""


def country_source() -> str:
    """What counts as an article belonging to a country, for the For You country preference.

    ``content`` (the default) is subject-level — where the event happened, or the country named in
    the headline/dek — and NEVER the outlet's home. Publisher home was the first shipped rule and
    is provenance, not subject: it called a Delhi outlet's article about Washington "India news",
    which is 59.8% of the catalog's labels against event geography's 17.6% (measured 2026-08-19).
    ``union`` restores the old, wider behaviour without a deploy."""
    v = os.environ.get("RWE_REC_COUNTRY_SOURCE", "").strip().lower()
    return v if v in ("content", "event", "mention", "publisher", "union") else "content"


def _qbias_record(a: dict, center: float) -> list:
    """One qbias-format CSV row from a FeedArticle-shaped dict. The single definition of the row
    shape, shared by both exporters below so the CSV format (and the row order the ``Q{i}`` -> URL
    map depends on) lives in exactly one place.

    ``country`` is an APPENDED column: ``catalog_from_qbias`` reads columns by name, so a trailing
    field is inert for the recommender and the protected simulator, exactly as the map it feeds is
    (see :func:`load_country_map`)."""
    scored = a.get("scored") or {}
    outlet = a.get("publisher") or scored.get("outlet") or ""
    political = scored.get("political")
    return [
        a.get("title") or scored.get("title") or "",
        outlet,
        _bias_label(scored.get("lean"), center),
        scored.get("category") or "",
        a.get("url") or a.get("canonicalUrl") or "",
        "" if political is None else ("1" if political else "0"),
        # Pipe-separated: an article about India AND Pakistan belongs to both, and a single label
        # would silently drop one of them.
        "|".join(sorted(article_countries(a, country_source()))),
    ]


def export_candidate_csv(rows, path: str, *, center: float = 0.5) -> int:
    """Write an explicit, already-composed list of FeedArticle-shaped dicts to a qbias-format CSV at
    ``path`` in the given order; returns the row count. This is **the** qbias serializer — the catalog
    exporter (below) and the hot-refresh Backend builder (``corpus_refresh``) both go through it, so
    the CSV format and the ``Q{i}`` row indexing that :func:`load_url_map` relies on are defined once.

    It writes every row verbatim and applies no filtering or cap: composition is the caller's job
    (``corpus_validation.build_candidate`` already balanced + capped the candidate). No article is
    modified — only projected onto the five qbias columns."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_COLUMNS)
        for a in rows:
            w.writerow(_qbias_record(a, center))
            n += 1
    return n


def export_catalog_csv(store_, path: str, *, max_items: Optional[int] = None,
                       center: float = 0.5, max_per_outlet: Optional[int] = None) -> int:
    """Write the FeedArticle catalog to a qbias-format CSV at ``path``. Returns the row count.

    ``max_per_outlet`` (optional) keeps at most that many articles per outlet — the most-recently
    fetched, since :meth:`Store.list_feed_articles` is ordered newest-first — so a single high-volume
    feed (e.g. a "world news" firehose) can't dominate the recommendations. It is corpus *composition*
    only (like ``max_items``); it does not touch ranking, scoring, diversity, or selection. Serializes
    through :func:`export_candidate_csv` so the CSV format is single-sourced.

    Freshness gate (Commit C4): articles older than ``RWE_FEED_MAX_AGE_DAYS`` (default 60 days;
    ``0`` disables) are excluded from the export, so a stale article can never become a
    recommendation candidate. Age comes from the real publication timestamp (``fetchedAt`` when
    the feed carried none); the gate is corpus *composition* only — stale articles stay stored
    and visible to Search / Stories / History.

    Read-demand exemption (Commit 18): an article a user actually **read** is never trimmed out —
    not by the ``max_items`` recency window, the freshness gate, or the per-outlet cap — because
    trimming a read article would disconnect that reader from the recommendation graph (it can
    still never be re-recommended to that reader: already-read articles are excluded per reader).
    Composition only, bounded by the distinct-read-URL set."""
    rows = store_.list_feed_articles(limit=max_items or 1_000_000)
    read_urls = store_.distinct_read_urls()
    rows = corpus_health.fresh_articles(rows, exempt=read_urls)
    if read_urls:                            # union in read articles the recency window missed
        have = {a.get("canonicalUrl") for a in rows}
        missing = [u for u in read_urls if u not in have]
        if missing:
            rows = rows + store_.feed_articles_by_urls(missing)
    if max_per_outlet:
        kept = []
        per_outlet: dict = {}
        for a in rows:
            scored = a.get("scored") or {}
            if a.get("canonicalUrl") in read_urls:
                kept.append(a)               # read-demand exemption: never capped out
                continue
            key = (a.get("publisher") or scored.get("outlet") or "").strip().lower()
            per_outlet[key] = per_outlet.get(key, 0) + 1
            if per_outlet[key] > max_per_outlet:
                continue                     # this outlet already hit its cap — keep the corpus balanced
            kept.append(a)
        rows = kept
    return export_candidate_csv(rows, path, center=center)


def prepare(store_, path: Optional[str] = None, *, min_articles: Optional[int] = None,
            max_items: Optional[int] = None) -> Optional[str]:
    """Export the FeedArticle catalog to a qbias-format CSV and return its path — or ``None`` when
    the catalog is too small (fewer than ``min_articles``), so the caller keeps the existing corpus.

    Path resolution: explicit ``path`` > ``RWE_FEED_CORPUS_CSV`` > ``<repo>/data/feed_corpus.csv``.
    Threshold: ``min_articles`` > ``RWE_FEED_MIN_ARTICLES`` > :data:`DEFAULT_MIN_ARTICLES`."""
    total = store_.count_feed_articles()
    threshold = (min_articles if min_articles is not None
                 else _int_env("RWE_FEED_MIN_ARTICLES", DEFAULT_MIN_ARTICLES))
    if total < threshold:
        return None
    out = path or os.environ.get("RWE_FEED_CORPUS_CSV") or os.path.join(_data_dir(), "feed_corpus.csv")
    # Optional per-outlet cap (RWE_FEED_MAX_PER_OUTLET, 0/unset = no cap) so one firehose feed can't
    # dominate the recommendation corpus. Applied to the corpus export only; the full catalog is kept.
    export_catalog_csv(store_, out, max_items=max_items,
                       max_per_outlet=_int_env("RWE_FEED_MAX_PER_OUTLET", 0) or None)
    return out


def load_url_map(csv_path: str) -> dict:
    """The corpus item-id -> publisher URL map implied by the exported catalog.

    ``catalog_from_qbias`` labels the i-th CSV *data* row ``Q{i}`` (``for i, row in enumerate(...)``)
    and drops rows with no resolvable lean without renumbering, so mapping each data-row index ``i``
    to that row's ``url`` reproduces the exact ids the recommender emits. Rows the builder later
    drops simply never appear as a recommendation id — their entries here are harmless. Reads the
    same CSV ``export_catalog_csv`` wrote (no duplicate storage)."""
    out: dict = {}
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            for i, row in enumerate(csv.DictReader(f)):
                url = (row.get("url") or "").strip()
                if url:
                    out[f"Q{i}"] = url
    except OSError:
        pass
    return out


def load_story_maps(store_, csv_path: str) -> "tuple[dict, dict]":
    """Story-membership inputs for the per-story feed quota (Tier 1,
    docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md): ``(item_id → story_id, canonical_url → story_id)``.

    The first map joins :func:`load_url_map`'s row-indexed ids to the Story Service's own
    clusters (``store.story_member_ids``, canonical-URL-keyed) through ``ingest.canonical_url``
    — the SAME canonicalisation the media join uses, because a raw feed URL (``www.``, tracking
    params) never equals its canonical form and an uncanonicalised join silently maps nothing.
    The second map covers the augmented corpus's novel columns, whose item id IS the reader's
    read URL. An article in no current story is simply absent from both — uncapped, never
    grouped by guess. Empty store / old catalog → empty maps → the quota has no input, which
    disables it rather than mis-grouping."""
    if store_ is None:
        return {}, {}
    import ingest
    by_url = {str(u): sid for u, sid in (store_.story_member_ids() or {}).items()}
    by_id: dict = {}
    if by_url:
        for item_id, url in load_url_map(csv_path).items():
            sid = by_url.get(ingest.canonical_url(url))
            if sid is not None:
                by_id[item_id] = sid
    return by_id, by_url


def load_country_map(csv_path: str) -> dict:
    """The corpus item-id -> ISO country SET implied by the exported catalog.

    The exact mirror of :func:`load_url_map`, and for the same reason: the i-th CSV *data* row is
    labelled ``Q{i}``, so mapping each data-row index to that row's ``country`` reproduces the ids
    the recommender emits. Rows without a country simply have no entry — the rank nudge treats a
    missing country as neutral, never as a mismatch, so unlocated articles keep their model order
    exactly. Older catalogs written before the column existed yield an empty map, which disables
    the nudge rather than mis-ranking on absent data."""
    out: dict = {}
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            for i, row in enumerate(csv.DictReader(f)):
                codes = frozenset(
                    c for c in (x.strip().upper()
                                for x in (row.get("country") or "").split("|"))
                    if len(c) == 2 and c.isalpha())
                if codes:
                    out[f"Q{i}"] = codes
    except OSError:
        pass
    return out


def _int_env(name: str, default: int) -> int:
    v = os.environ.get(name)
    return int(v) if v and v.lstrip("-").isdigit() else default
