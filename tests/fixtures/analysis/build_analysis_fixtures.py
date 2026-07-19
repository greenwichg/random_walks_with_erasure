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
import evidence_resolver as er         # noqa: E402
import store as store_mod              # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
CASES = ("catalog_hit", "scored_url_only", "invalid_url")

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


def main() -> None:
    cases = build_from_fresh_store()
    assert set(cases) == set(CASES)
    for name in CASES:
        path = HERE / f"{name}.json"
        path.write_text(json.dumps(cases[name], indent=2, sort_keys=True) + "\n")
        print("wrote", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
