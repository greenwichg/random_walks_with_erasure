"""Shared golden-fixture builder for the Article Analyzer — the cross-language contract anchor (F2).

Produces canonical ANALYSIS CONTRACT v1 outputs from the REAL analyzer for the three source states —
catalog hit, scored-url-only, invalid URL — from one deterministic in-memory seed. Both sides of the
contract consume the committed JSON:

  * the backend test (``tests/test_analysis_fixtures.py``) asserts the analyzer still reproduces them;
  * the web mapper test (``web/lib/analysis-presentation.test.ts``) feeds them through
    ``analysisPresentation`` instead of handwritten objects.

So a change to the analyzer's output shape breaks a test on at least one side. Regenerate ONLY after
an intentional contract change::

    python tests/fixtures/analysis/build_analysis_fixtures.py
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]   # tests/fixtures/analysis/ -> repo root
sys.path.insert(0, str(ROOT / "examples"))

import article_analyzer as aa          # noqa: E402
import analysis_enrichment as ae       # noqa: E402
import evidence_resolver as er         # noqa: E402
import store as store_mod              # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
CASES = ("catalog_hit", "scored_url_only", "invalid_url")
#: Authenticated (measured-reader) enrichment goldens — the FULL analysis with the reader-relative
#: explanation + recommendation sections filled. Built from a fixed reader context (not the heavy
#: model) so they're deterministic; the endpoint's measured path is covered by the enrichment tests.
AUTHED_CASES = ("authed_bridge", "authed_familiar", "authed_following")
GUARDIAN_URL = "https://theguardian.com/story/an-cluster-0"   # the seeded left cluster member
#: A fixed blind spot (not the article's topic), in the production shape (`_blind_spot_topics`
#: returns topic -> gap from the stored report).
_BLIND_SPOTS = {"Economy": 0.4}

# A deterministic 3-publisher story (left + two center, NO right) so the catalog hit exercises a real
# membership with a derived missing "right" viewpoint. Mirrors the shape the analyzer's own tests use.
_CLUSTER = [
    ("The Guardian", "theguardian.com", -1.0, "senate budget vote reaches bipartisan deal"),
    ("AP", "apnews.com", 0.0, "senate passes budget vote after bipartisan deal"),
    ("Reuters", "reuters.com", 0.0, "bipartisan budget deal clears senate vote"),
]
CATALOG_HIT_URL = "https://apnews.com/story/an-cluster-1"     # the AP member seeded below
SCORED_URL = "https://apnews.com/live/budget-liveblog"        # known outlet, NOT in the catalog
INVALID_URL = "not a url"


def seed(store) -> None:
    """Seed the deterministic story cluster the golden cases resolve against."""
    for i, (pub, dom, lean, title) in enumerate(_CLUSTER):
        url = f"https://{dom}/story/an-cluster-{i}"
        store.upsert_feed_article(
            canonical_url=er._canon(url), url=url, publisher=pub, source_publisher=pub,
            title=title, description="Lawmakers reached a deal.", body=None,
            published_at="2026-07-18T12:00:00+00:00", source_feed="fixture",
            scored={"article_id": er._canon(url), "outlet": pub, "category": "Politics",
                    "lean": lean, "political": True, "title": title,
                    "emotion": {"fear": 0.1, "outrage": 0.1, "analysis": 0.3,
                                "positive": 0.1, "neutral": 0.4},
                    "register": 0.8, "selective": 0.72})


def build(store) -> "dict[str, dict]":
    """The three canonical analyses, produced by the REAL analyzer against ``store``."""
    return {
        "catalog_hit": aa.analyze(store, CATALOG_HIT_URL),
        "scored_url_only": aa.analyze(store, SCORED_URL),
        "invalid_url": aa.analyze(store, INVALID_URL),
    }


def build_from_fresh_store() -> "dict[str, dict]":
    """Seed a fresh in-memory store and build all cases — the single source both tests call."""
    st = store_mod.Store("sqlite://")   # in-memory, deterministic; no fetch, no write
    seed(st)
    return build(st)


# --------------------------------------------------------------------------- #
# Authenticated (measured-reader) enrichment fixtures.
# --------------------------------------------------------------------------- #
def _familiarity(shares: "dict[str, dict]"):
    """A fixed familiarity lookup (the Evidence Resolver's context contract): publisher -> band."""
    def fam(publisher: str) -> dict:
        s = shares.get(publisher) or {"reads": 0, "share": 0.0}
        band = "never" if s["reads"] <= 0 else ("rarely" if s["share"] < 0.05 else "familiar")
        return {"reads": s["reads"], "share": s["share"], "band": band}
    return fam


def _reader_ctx(*, mean_lean, reads, fam_shares, top_topics, topic_shares, lean_shares) -> dict:
    """A deterministic measured-reader context in the exact shape ``explanation_context`` returns."""
    return {"reads": [{"url": er._canon(u), "publisher": p, "publishedAt": t} for (u, p, t) in reads],
            "familiarity": _familiarity(fam_shares), "top_topics": list(top_topics),
            "reader_mean_lean": mean_lean, "topic_shares": dict(topic_shares),
            "lean_shares": dict(lean_shares)}


def _authed_contexts() -> "dict[str, dict]":
    # bridge: a RIGHT reader analysing the LEFT Guardian article they've never read -> cross-cutting.
    right = _reader_ctx(
        mean_lean=1.5,
        reads=[("https://foxnews.com/politics/older-take", "Fox News", "2026-07-10T12:00:00+00:00")],
        fam_shares={"Fox News": {"reads": 20, "share": 0.8}},   # Guardian absent -> "never"
        top_topics=["Politics"], topic_shares={"Politics": 0.85},
        lean_shares={"left": 0.10, "center": 0.05, "right": 0.85})
    # familiar: a LEFT reader who reads the Guardian often -> not cross-cutting, a top-topic read.
    left = _reader_ctx(
        mean_lean=-1.5,
        reads=[("https://theguardian.com/politics/unrelated-older", "The Guardian", "2026-07-10T12:00:00+00:00")],
        fam_shares={"The Guardian": {"reads": 30, "share": 0.9}},
        top_topics=["Politics"], topic_shares={"Politics": 0.90},
        lean_shares={"left": 0.85, "center": 0.10, "right": 0.05})
    # following (A4.1): a RIGHT reader who already read the AP member of the SAME cluster ->
    # story_match explanation, and a `personal` with real story standing (1 center member read;
    # this left article would add a bucket) plus an honestly-zero left share -> addsMissing.
    # Internally consistent: reads are AP once + Fox often; no left read, so left share 0.0.
    following = _reader_ctx(
        mean_lean=1.5,
        reads=[("https://apnews.com/story/an-cluster-1", "AP", "2026-07-18T13:00:00+00:00"),
               ("https://foxnews.com/politics/an-older-take", "Fox News", "2026-07-12T09:00:00+00:00")],
        fam_shares={"AP": {"reads": 1, "share": 0.03}, "Fox News": {"reads": 30, "share": 0.9}},
        top_topics=["Politics"], topic_shares={"Politics": 0.75},
        lean_shares={"left": 0.0, "center": 0.25, "right": 0.75})
    return {"authed_bridge": right, "authed_familiar": left, "authed_following": following}


def build_authed_from_fresh_store() -> "dict[str, dict]":
    """The full enriched analyses for the authenticated cases, from a fresh seed + fixed contexts."""
    st = store_mod.Store("sqlite://")
    seed(st)
    er._INDEX_CACHE.update(key=None, index=None)     # force a fresh story index for this store
    index = er.story_index(st)
    out = {}
    for name, ctx in _authed_contexts().items():
        analysis = aa.analyze(st, GUARDIAN_URL)
        # A3.3a: store supplies the story-sibling hydration; no feed fallback in the goldens (the
        # Guardian member always has cluster siblings, so the story path is the pinned one).
        sections = ae.enrich(analysis, ctx, index=index, blind_spot_topics=_BLIND_SPOTS, store=st)
        out[name] = {**analysis, **sections}
    return out


def main() -> None:
    cases = build_from_fresh_store()
    assert set(cases) == set(CASES)
    authed = build_authed_from_fresh_store()
    assert set(authed) == set(AUTHED_CASES)
    for name, data in {**cases, **authed}.items():
        path = HERE / f"{name}.json"
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        print("wrote", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
