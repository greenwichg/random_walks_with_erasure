"""Guardrails for the corpus architecture (docs/CORPUS_ARCHITECTURE.md).

These tests make the architectural boundaries *contracts*, so they cannot erode by accident:

  ① Full / Searchable Corpus (feed_articles)   — everything ingested; search/discover/stories read it
  ②′ Clustering Corpus (Tier A, examples/corpus.py) — the articles allowed to FORM stories
  ② Recommendation Corpus (qbias projection)   — lean-resolvable/fresh/capped; ONLY recommendations read it
  ③ User reads                                  — Information Health metrics derive from here

Principle: searchable != recommendable != **clusterable**; ingestion != recommendation; reads are a
separate concern.

②′ is the M1 addition (docs/SCALE_ROADMAP.md). It is the same shape of boundary as ② — a projection
of ① with its own admission rule — and it exists because there was previously nowhere to stand to
say "this outlet is searchable but does not form stories", which is what shadow ingest, promotion
and retirement all need. Before it, Stories read ① directly, so the clustering corpus was whatever
the fetch happened to return.

Low-cost by design: behavioral tests proving each contract end-to-end, plus source-level structural
checks that the surfaces stay on their own dataset.
"""
import csv
import importlib
import inspect
import pathlib
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import store as store_mod          # noqa: E402
import search                      # noqa: E402
import feed_source                 # noqa: E402
import story_service               # noqa: E402


def _add(st, cu, publisher, lean, title, *, category="Politics", desc="context"):
    """Insert one FeedArticle into the full corpus (mirrors tests/test_search.py). ``lean=None`` models
    an unknown-outlet article (e.g. a broad GDELT item) with no resolvable lean."""
    st.upsert_feed_article(
        canonical_url=cu, url=cu, publisher=publisher, source_publisher=publisher, title=title,
        description=desc, body=None, published_at=datetime.now(timezone.utc).isoformat(),
        source_feed="feed://x",
        scored={"article_id": cu, "outlet": publisher, "category": category, "lean": lean, "title": title})


# --------------------------------------------------------------------------- #
# ① vs ② — the core product contract: searchable != recommendable
# --------------------------------------------------------------------------- #
def test_no_lean_article_is_searchable_but_excluded_from_recommendation_corpus(tmp_path):
    st = store_mod.Store("sqlite://")
    _add(st, "https://npr.org/a", "NPR", -1.5, "Senate passes funding bill")        # resolvable lean
    _add(st, "https://unknown.example/x", "Unknown Blog", None, "Local roundup")    # no lean (GDELT-style)

    # ① FULL CORPUS: search returns BOTH — the no-lean article is fully searchable.
    titles = {r["headline"] for r in search.search(st)["results"]}
    assert "Senate passes funding bill" in titles
    assert "Local roundup" in titles, "a no-lean article must remain searchable (dataset ①)"

    # ② RECOMMENDATION CORPUS: the qbias serializer gives the no-lean row an EMPTY bias_rating, which
    # catalog_from_qbias drops (feed_source.py:47-64) — so it can never be recommended.
    path = tmp_path / "rec_corpus.csv"
    feed_source.export_candidate_csv(st.list_feed_articles(limit=10**6), str(path))
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header, data = rows[0], rows[1:]
    ti, bi = header.index("title"), header.index("bias_rating")
    bias = {r[ti]: r[bi] for r in data}
    assert bias["Senate passes funding bill"] != "", "a lean-resolvable article is recommendable"
    assert bias["Local roundup"] == "", "a no-lean article must be dropped from the recommendation corpus (②)"


# --------------------------------------------------------------------------- #
# Endpoint boundaries — browse reads ①, recommendations read ②. Source-level (cheap, no app boot).
# The forbidden strings are call-site identifiers that never appear in these functions' prose.
# --------------------------------------------------------------------------- #
_BROWSE_ENDPOINTS = ("search_feed", "discover_feed", "stories", "story_single",
                     "story_intelligence_endpoint")


def test_browse_endpoints_do_not_read_the_recommendation_corpus():
    import api_fastapi as api
    for name in _BROWSE_ENDPOINTS:
        src = inspect.getsource(getattr(api, name))
        for forbidden in ("active.backend", "active.personalizer", "_serve("):
            assert forbidden not in src, (
                f"/{name} must read the FULL corpus (①), not the recommendation corpus (②): "
                f"found '{forbidden}'. See docs/CORPUS_ARCHITECTURE.md.")


def test_recommendations_endpoint_reads_the_projection_not_the_full_corpus():
    import api_fastapi as api
    src = inspect.getsource(api.recommendations)
    assert "list_feed_articles" not in src, (
        "/recommendations must read the recommendation corpus (②), never the full corpus (①) directly.")
    assert "active." in src, (
        "/recommendations must read the Active recommendation corpus (②).")


# --------------------------------------------------------------------------- #
# ① vs ②′ — searchable != clusterable (M1, docs/SCALE_ROADMAP.md)
# --------------------------------------------------------------------------- #
def _story_fingerprint(stories):
    """The byte-identical bar this repo already uses for a clustering-neutral change: every id,
    title, coverage count, publisher count, blindspot side, trust verdict and ORDERED member-URL
    list (`docs/PERFORMANCE.md`, the candidate-walk rewrite and the `_merge_duplicates` size bound
    — four corpora, 3,499 stories). Ordering is part of it because member order decides DSU union
    order, which decides group roots, which decides story ids."""
    return [(s["id"], s["title"], len(s["coverage"]),
             len({c["publisher"] for c in s["coverage"]}),
             s["blindspotSide"], s["clusterTrust"],
             tuple(c["url"] for c in s["coverage"]))
            for s in stories]


def _story_corpus(st, *, include_tier_b: bool):
    """Two Tier A outlets covering one event, plus a third outlet whose headline is near-identical
    — so if it were admitted it would join the cluster and change its size, publisher count and
    member list. That is what makes the byte-identity assertion mean something.

    ``_add`` stamps ``published_at`` at "now", which is inside the clustering window either way."""
    head = "Senate passes the federal funding bill after an all night debate"
    _add(st, "https://npr.org/a", "NPR", -1.0, head)
    _add(st, "https://foxnews.com/b", "Fox News", 2.0, head)
    if include_tier_b:
        _add(st, "https://aggregator.example/c", "Aggregator Example", None, head)


def test_a_tier_b_article_is_searchable_but_never_enters_the_clustering_corpus(monkeypatch):
    """The ①/②′ contract, end to end, and the containment invariant in miniature.

    A Tier B outlet's article is fully searchable — it is in ① like everything else — and the story
    set built with it present is **byte-identical** to the story set built in a catalog where the
    row never existed. Not "similar", not "one fewer member": identical, on the bar quoted in
    `_story_fingerprint`.

    That identity is what lets Tier B promotion be automatic at 50k sources while Tier A promotion
    stays gated: a row that cannot move the partition needs no clustering counterfactual."""
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "aggregator.example")

    with_b = store_mod.Store("sqlite://")
    _story_corpus(with_b, include_tier_b=True)
    without_b = store_mod.Store("sqlite://")
    _story_corpus(without_b, include_tier_b=False)

    # ① FULL CORPUS: the Tier B article is searchable, exactly like a no-lean article above.
    urls = {r["url"] for r in search.search(with_b)["results"]}
    assert "https://aggregator.example/c" in urls, (
        "a Tier B article must remain searchable (dataset ①) — tiering governs clustering, not visibility")

    # ②′ CLUSTERING CORPUS: it is not in the candidate set at all...
    fetched = {r["canonicalUrl"] for r in story_service._fetch(with_b)}
    assert "https://aggregator.example/c" not in fetched
    assert {"https://npr.org/a", "https://foxnews.com/b"} <= fetched

    # ...and the story set is byte-identical to one built without the row ever existing.
    assert _story_fingerprint(story_service.cluster_from_store(with_b)) == \
           _story_fingerprint(story_service.cluster_from_store(without_b))


def test_without_tiering_that_same_article_would_have_changed_the_story(monkeypatch):
    """The control arm. Without it the test above passes for the wrong reason — an article that
    would never have clustered anyway proves nothing about containment.

    This is the trap `docs/PERFORMANCE.md` records from the merge-bound work: a recall test that
    "looked exactly like the bound breaking recall" was exercising a switched-off code path, and
    running the failure against the unmodified tree first is the only reason that did not become an
    hour of debugging correct code."""
    monkeypatch.delenv("RWE_CORPUS_TIER_B", raising=False)

    with_b = store_mod.Store("sqlite://")
    _story_corpus(with_b, include_tier_b=True)
    without_b = store_mod.Store("sqlite://")
    _story_corpus(without_b, include_tier_b=False)

    assert _story_fingerprint(story_service.cluster_from_store(with_b)) != \
           _story_fingerprint(story_service.cluster_from_store(without_b)), (
        "the fixture's Tier B article must actually alter the story set when admitted, or the "
        "containment test above is vacuous")


# --------------------------------------------------------------------------- #
# The shadow lane — M5. Tier B is searchable; shadow is surfaced NOWHERE.
# --------------------------------------------------------------------------- #
def test_a_shadow_outlet_is_stored_but_reaches_no_reader_surface(monkeypatch):
    """The distinction the whole tier split rests on: **Tier B is searchable, shadow is not.**

    A Tier B outlet is a real source that simply does not form stories, so hiding it would delete
    the point of the tier. A shadow outlet has not been evaluated yet, so nothing about it should
    reach a reader — it is being watched, not published.

    Before M5 this was documented and not true. `corpus.tier_of` returned "shadow" and its docstring
    said "surfaced nowhere", but the boundary was enforced in `story_service._fetch` alone, leaving
    every shadow article fully searchable."""
    monkeypatch.setenv("RWE_CORPUS_SHADOW", "shadow.example")
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "tierb.example")
    st = store_mod.Store("sqlite://")
    _add(st, "https://npr.org/a", "NPR", -1.0, "Senate passes the funding bill tonight")
    _add(st, "https://tierb.example/b", "tierb.example", None, "Senate passes the funding bill tonight")
    _add(st, "https://shadow.example/c", "shadow.example", None, "Senate passes the funding bill tonight")

    urls = {r["url"] for r in search.search(st)["results"]}
    assert "https://npr.org/a" in urls
    assert "https://tierb.example/b" in urls, "Tier B is SEARCHABLE — that is what distinguishes it"
    assert "https://shadow.example/c" not in urls, "a shadow article must reach no reader surface"

    # ...and it is still in the catalog. Shadow withholds, it does not discard.
    assert "https://shadow.example/c" in {
        r["canonicalUrl"] for r in st.list_feed_articles(limit=100)}
    assert st.search_feed_articles(include_shadow=True)[1] == 3, (
        "an evaluation path must be able to see the lane it evaluates")


def test_a_shadow_publisher_is_not_offered_as_a_filter(monkeypatch):
    """A facet list is where a half-enforced boundary shows first: naming an outlet in the dropdown
    that returns nothing advertises the lane and then fails the reader."""
    monkeypatch.setenv("RWE_CORPUS_SHADOW", "shadow.example")
    st = store_mod.Store("sqlite://")
    _add(st, "https://npr.org/a", "NPR", -1.0, "A headline about the funding bill")
    _add(st, "https://shadow.example/c", "shadow.example", None, "Another headline entirely")
    pubs = st.feed_article_facets()["publishers"]
    assert "NPR" in pubs and "shadow.example" not in pubs
    assert "shadow.example" in st.feed_article_facets(include_shadow=True)["publishers"]


def test_shadow_exclusion_is_the_store_DEFAULT_not_a_caller_opt_in():
    """The design decision M5 turns on, pinned so it cannot quietly invert.

    Seven reader surfaces funnel through `search_feed_articles`. Enforcing shadow at each of them
    is how it came to be half implemented the first time. Defaulting to exclusion means a NEW
    surface is safe the day it is written, and the failure mode of forgetting the flag is "the
    evaluation harness cannot see what it evaluates" — loud — instead of "unvetted sources reached
    readers" — silent."""
    import inspect
    sig = inspect.signature(store_mod.Store.search_feed_articles)
    assert sig.parameters["include_shadow"].default is False, (
        "shadow must be excluded by DEFAULT; an opt-in default fails toward publishing unvetted "
        "sources, which is the failure this milestone exists to remove")
    for name in ("feed_article_facets", "feed_article_country_facets"):
        assert inspect.signature(getattr(store_mod.Store, name)).parameters[
            "include_shadow"].default is False, f"{name} must exclude shadow by default too"


def test_nothing_in_shadow_changes_nothing(monkeypatch):
    """Shipped state. With no shadow configured the exclusion set is empty, no SQL term is added,
    and every surface returns exactly what it did before M5."""
    monkeypatch.delenv("RWE_CORPUS_SHADOW", raising=False)
    import corpus as corpus_mod
    assert corpus_mod.shadow_exclusions() == frozenset()
    st = store_mod.Store("sqlite://")
    _add(st, "https://npr.org/a", "NPR", -1.0, "A headline about the funding bill")
    _add(st, "https://other.example/b", "other.example", None, "Another headline entirely")
    assert st.search_feed_articles()[1] == st.search_feed_articles(include_shadow=True)[1] == 2
    assert st.feed_article_facets()["publishers"] == \
           st.feed_article_facets(include_shadow=True)["publishers"]


def test_the_clustering_corpus_is_selected_not_merely_fetched():
    """Structural, so the seam cannot erode. Every story build funnels through `_fetch`; if a later
    change re-reads the store beside it, the boundary quietly stops applying and Tier B rows return
    to the O(n^2) builder with nothing failing."""
    src = inspect.getsource(story_service._fetch)
    assert "corpus.select(" in src, (
        "story_service._fetch must route its rows through corpus.select — that call IS the ①/②′ "
        "boundary. See docs/SCALE_ROADMAP.md (M1) and docs/CORPUS_ARCHITECTURE.md.")
    assert "rows, total = " in src, (
        "the pre-pagination total must be kept, not discarded as `_total`: it is the only thing "
        "that makes a truncated clustering window detectable.")


#: Modules that walk a whole catalogue or cohort and ask for each row's tier. Every one of them must
#: hoist `corpus.tier_resolver()` out of its loop rather than calling `corpus.tier_of` per row.
_PER_ROW_TIER_CALLERS = ("audit_retention_horizon", "audit_shadow_cohort",
                         "select_asif_population", "stress_50k", "corpus_health", "story_service")


def test_no_row_loop_calls_tier_of_per_row():
    """`tier_of` re-reads the settings on every call; `tier_resolver()` reads them once.

    The gap is not stylistic. `tier_of` is linear in the number of configured sources, and since
    admission gained a Tier B table it also composes two frozensets per call. **Measured at a
    50,000-host assignment: 1,032 us per `tier_of` against 3.1 us per resolved call plus a 2.5 ms
    one-time build — 103 s versus 0.31 s over 100,000 articles.**

    This is a structural check because the defect is invisible at today's size and only appears at
    the size the whole 50,000-source programme is aiming at. `corpus_health._tier_age_resolver`
    already carries the comment "ONE resolver for the whole pass, not a `tier_of` per article"; this
    is that comment made enforceable, for the modules that walk a catalogue.

    Not a ban on `tier_of` — it is the right call for a handful of lookups, which is why
    `source_campaign` and the API surfaces are not listed here. The rule is about row LOOPS."""
    offenders = []
    for name in _PER_ROW_TIER_CALLERS:
        mod = importlib.import_module(name)
        src = inspect.getsource(mod)
        if "corpus.tier_of(" in src or "\ntier_of(" in src:
            offenders.append(name)
    assert not offenders, (
        "these modules walk rows and call corpus.tier_of per row — hoist corpus.tier_resolver() out "
        f"of the loop instead: {offenders}. See corpus.tier_resolver for the measurement.")


def test_the_resolver_is_flat_in_the_number_of_configured_sources():
    """The property the hoist buys, asserted rather than assumed.

    A resolver that re-read the settings internally would satisfy the structural check above while
    changing nothing — so this measures the thing the check is a proxy for."""
    import corpus as corpus_mod
    small = frozenset(f"h{i}.example" for i in range(50))
    large = frozenset(f"h{i}.example" for i in range(20_000))

    def _time(hosts):
        corpus_mod.wire_tier_b_admissions(lambda: hosts)
        corpus_mod.admitted_tier_b_hosts(refresh=True)
        resolve = corpus_mod.tier_resolver()
        resolve("target.example", "https://target.example/a")       # warm
        t0 = time.perf_counter()
        for _ in range(2000):
            resolve("target.example", "https://target.example/a")
        return (time.perf_counter() - t0) / 2000

    try:
        per_small, per_large = _time(small), _time(large)
        assert per_large < per_small * 5, (
            f"resolved lookups are supposed to be independent of the assignment size, but 20,000 "
            f"hosts cost {per_large * 1e6:.1f} us against {per_small * 1e6:.1f} us for 50 — the "
            f"resolver is reading the settings per call again")
    finally:
        corpus_mod.wire_tier_b_admissions(None)
