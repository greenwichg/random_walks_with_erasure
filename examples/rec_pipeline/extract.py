"""Stage 1 · Extract — a scenario fixture → a real, offline recommendation case.

This builds the recommender **exactly as production does** (Backend + the live-feed URL resolver +
Personalizer over the augmented corpus) but in-process, no HTTP: the pipeline's mandate for Phase 1
is to reuse the production graph *and* the production ranking, then validate the behaviour on top —
not to reimplement the linear algebra (that is the optional last stage, deferred to 21d.2).

A *scenario fixture* is a controlled catalog + the reader under test's history + a named ``target``
article, engineered so the target resolves to a specific explanation ``type`` (same_story, bridge,
long_tail, …). The pipeline then proves, over the REAL reader context and REAL story clusters, that
the explanation is licensed by evidence — the question a user actually asks ("why did I get this?").
"""
from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import sys
import tempfile
from types import SimpleNamespace
from typing import List, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import api_server as engine        # noqa: E402  Backend / resolve_profile (production build)
import personalize                 # noqa: E402  the augmented Measured recommender (production)
import evidence_resolver as er     # noqa: E402  the Evidence Resolver under validation
import feed_source                 # noqa: E402  FeedArticle → corpus CSV (production live-feed path)
import ingest                      # noqa: E402  the read scorer (cache-joins reads to catalog ids)
import enrich                      # noqa: E402
import store as store_mod          # noqa: E402

#: Feed-mode env for a small controlled catalog — the same knobs the value-chain / explain tests use,
#: so the qbias-simulated base population over the catalog gives the walk real structure.
_ENV = {"RWE_RECS_SOURCE": "feed", "RWE_FEED_MIN_ARTICLES": "5", "RWE_CORPUS_MIN_ARTICLES": "5",
        "RWE_FEED_MAX_PER_OUTLET": "40", "RWE_N_USERS": "80", "RWE_MAX_ITEMS": "200", "RWE_SEED": "0",
        # Fixtures carry fixed publication dates; the freshness gate (C4) is age-relative to *now*,
        # so it is pinned OFF here to keep the golden scenarios deterministic forever. Freshness has
        # its own unit tests (tests/test_freshness.py).
        "RWE_FEED_MAX_AGE_DAYS": "0",
        # The conditional Story-Match slot is pinned OFF so the golden scenarios keep validating
        # the ORGANIC feed; the slot has its own tests (tests/test_story_slot.py).
        "RWE_STORY_SLOT": "0"}


@dataclasses.dataclass
class Case:
    """Everything the later stages observe — built once, never mutated by a stage."""
    name: str
    description: str
    expected: dict
    store: object
    backend: object
    personalizer: object
    reader_uid: int
    measured: bool                    # reader has >= ESTIMATE_MIN_READS reads (augmented path)
    reads: list                       # the reader's canonical read URLs, oldest first
    context: dict                     # er context (reads / familiarity / top_topics)
    index: dict                       # er story index (url → {storyId, coverage})
    catalog_by_url: dict              # canonical url → serialized article (for target lookup)
    target_url: Optional[str]         # the scenario's "why this article?" probe
    target_strategy: str              # the strategy the target probe attributes (long_tail = rwe-d)
    recs: list                        # the served feed (production ranking)

    def target_rec(self) -> Optional[dict]:
        """A rec-shaped payload for the scenario's target article (as the serializer would emit)."""
        art = self.catalog_by_url.get(er._canon(self.target_url or ""))
        if art is None:
            return None
        return {"article": art, "crossCutting": self._cross(art), "strategy": self.target_strategy}

    def _cross(self, art: dict) -> bool:
        import numpy as np
        mean = float(self.context.get("_mean_lean") or 0.0)
        # Commit R1: same conservative rule as the serializer — unknown political never crosses.
        return engine._cross_of(np.sign(mean), float(art.get("lean") or 0.0),
                                bool(art.get("political")))


def _restore_env(saved: dict) -> None:
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def build(fixture: dict, *, keep_env: bool = False) -> Case:
    """Build a real offline recommendation case from a scenario fixture. Isolated per call: a fresh
    temp SQLite store + a saved/restored env, so fixtures never leak into one another or the host."""
    saved = {k: os.environ.get(k) for k in list(_ENV) + ["RWE_QBIAS", "RWE_PROFILE", "RWE_DB_URL"]}
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_url = f"sqlite:///{tmp.name}"
    os.environ.update(_ENV)
    os.environ["RWE_DB_URL"] = db_url
    er._INDEX_CACHE.update(key=None, index=None)      # never reuse a prior fixture's story index

    try:
        st = store_mod.Store(db_url)
        # -- catalog ---------------------------------------------------------
        for a in fixture["catalog"]:
            sc = dict(a["scored"])
            sc.setdefault("article_id", a["url"])
            st.upsert_feed_article(
                canonical_url=ingest.canonical_url(a["url"]), url=a["url"],
                publisher=a["publisher"], source_publisher=a["publisher"],
                title=a.get("title", sc.get("title", "")), description=a.get("description", "d"),
                body=None, published_at=a.get("publishedAt"), source_feed="fixture",
                source_type="rss", scored=sc)
        catalog_by_url = {ingest.canonical_url(a["url"]): _article_of(a) for a in fixture["catalog"]}

        # -- readers + reads (real scorer → cache-hit → catalog-id join) -----
        scorer = ingest.Scorer(enricher=enrich.make_enricher())
        reader_uid = None
        for reader in fixture["readers"]:
            uid = st.upsert_user_by_identity("dev", reader["id"]).id
            if reader.get("underTest"):
                reader_uid = uid
            for r in reader["reads"]:
                raw = ingest.RawRead(url=ingest.normalize_url(r["url"]), title=r.get("title", ""),
                                     outlet=r.get("publisher", ""), read_at=r.get("readAt"))
                scored = ingest.score_with_cache(raw, scorer, st)
                st.add_read(uid, scored.article_id, dataclasses.asdict(scored), scored.read_at)
        assert reader_uid is not None, "fixture has no reader marked underTest"

        # -- production recommender build (Backend + live-feed resolver + Personalizer) -----
        feed_csv = feed_source.prepare(st)
        assert feed_csv, "catalog below the feed minimum — add articles or lower RWE_FEED_MIN_ARTICLES"
        # The feed catalog becomes the corpus by ARGUMENT, which `resolve_profile` ranks above the
        # environment — not by writing RWE_QBIAS/RWE_PROFILE, which also reconfigured every later
        # reader in the process. (api_fastapi._profile_for is the serving path's version.) The other
        # env this function sets is deliberate and is what `keep_env` is about; these two were not.
        ns = SimpleNamespace(profile="qbias", npz=None, qbias=feed_csv,
                             register_csv=None, emotion_csv=None,
                             behaviors=None, lean_tau=None, domain=None,
                             n_users=int(_ENV["RWE_N_USERS"]), max_items=int(_ENV["RWE_MAX_ITEMS"]),
                             seed=int(_ENV["RWE_SEED"]))
        be = engine.Backend(engine.resolve_profile(ns))
        be.attach_url_resolver(feed_source.load_url_map(feed_csv))
        pers = personalize.Personalizer(be, st, persist=False)

        reads = [er._canon(str(r.get("article_id") or "")) for r in st.get_reads(reader_uid)]
        measured = st.count_reads(reader_uid) >= engine.ESTIMATE_MIN_READS
        if measured:
            ctx = pers.explanation_context(reader_uid)
            recs = pers.recommendations(reader_uid)
        else:
            # cold start: no augmented model — resolve against the real (sparse) context, and take
            # the served feed from the base/demo path so the invariant checks still see real recs.
            ctx = _base_context(be, st, reader_uid, reads)
            recs = be.recommendations(be.demo_user)
        ctx["_mean_lean"] = _mean_lean(reads, catalog_by_url)
        # Offline one-shot harness: no poller ever warms the story cache in this process, so the
        # read-only inline build is the only way the scenario's clusters exist.
        idx = er.story_index(st, build_inline=True)

        # Mirror the API's serialization-time enrichment (api_fastapi feed-article join): attach the
        # catalog's REAL publication timestamp to each served article, so explanation resolution
        # sees the same truthful dates production sees — the serializer itself no longer fabricates
        # a date for a real (URL-resolved) article (Commit C4).
        for r in recs:
            a = r.get("article") or {}
            ca = catalog_by_url.get(er._canon(str(a.get("url") or a.get("id") or "")))
            if ca and ca.get("publishedAt"):
                a["publishedAt"] = ca["publishedAt"]

        return Case(name=fixture["name"], description=fixture.get("description", ""),
                    expected=fixture.get("expected", {}), store=st, backend=be, personalizer=pers,
                    reader_uid=reader_uid, measured=measured, reads=reads, context=ctx, index=idx,
                    catalog_by_url=catalog_by_url, target_url=fixture.get("target"),
                    target_strategy=fixture.get("targetStrategy", "rwe-b"), recs=recs)
    finally:
        if not keep_env:
            _restore_env(saved)
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


def load_history(path: str) -> List[dict]:
    """A lenient reader for an exported reading history — the notebook's "run on my own reads"
    path. Accepts the app's ``/api/me/history`` response (a list of ``{article: {...}}``), a
    ``{"reads": [...]}`` envelope, or the metric-notebook ``{"scored": {...}}`` rows. Normalizes
    each to ``{url, publisher, topic, lean, title}``; rows without a usable URL are skipped."""
    data = json.loads(pathlib.Path(path).read_text())
    rows = data.get("reads") if isinstance(data, dict) else data
    out = []
    for r in rows or []:
        a = r.get("article") if isinstance(r, dict) and isinstance(r.get("article"), dict) else \
            r.get("scored") if isinstance(r, dict) and isinstance(r.get("scored"), dict) else r
        url = (a.get("url") or a.get("article_id") or (r.get("url") if isinstance(r, dict) else None))
        if not url:
            continue
        out.append({"url": str(url), "publisher": a.get("publisher") or a.get("outlet") or "",
                    "topic": a.get("topic") or a.get("category") or "",
                    "lean": float(a.get("lean") or 0.0),
                    "title": a.get("headline") or a.get("title") or ""})
    return out


def fixture_from_history(reads: List[dict], name: str = "my-reading-history") -> dict:
    """Turn an exported reading history into a scenario fixture (no target): a base catalog for
    graph structure + the reader's own read-articles + the reader under test. Every recommendation
    the pipeline then surfaces is validated by the same universal invariants — the target-specific
    scenario checks are simply skipped. A small or single-outlet history yields a thin feed, but
    each recommendation in it is still proven evidence-backed."""
    base = json.loads((pathlib.Path(__file__).resolve().parent / "golden" / "mixed_feed.json")
                      .read_text())["catalog"]
    seen = {er._canon(a["url"]) for a in base}
    extra = []
    for r in reads:
        c = er._canon(r["url"])
        if c in seen:
            continue
        seen.add(c)
        extra.append({"url": r["url"], "publisher": r["publisher"] or "Unknown",
                      "title": r.get("title", ""), "publishedAt": "2026-07-05T00:00:00+00:00",
                      "scored": {"outlet": r["publisher"] or "Unknown",
                                 "category": r["topic"] or "News", "lean": r["lean"],
                                 "political": abs(r["lean"]) > 0.4, "title": r.get("title", ""),
                                 "register": 0.8,
                                 "emotion": {"fear": 0.1, "outrage": 0.1, "analysis": 0.5,
                                             "positive": 0.1, "neutral": 0.2}}})
    reader = {"id": name, "underTest": True,
              "reads": [{"url": r["url"], "publisher": r["publisher"]} for r in reads]}
    return {"name": name, "description": f"Your exported reading history ({len(reads)} reads).",
            "catalog": base + extra, "readers": [reader], "expected": {}}


def _article_of(a: dict) -> dict:
    """A catalog fixture entry → the article fields the resolver reads (mirrors the serializer)."""
    sc = a["scored"]
    lean = float(sc.get("lean") or 0.0)
    bucket = "left" if lean < -0.5 else "right" if lean > 0.5 else "center"
    art = {"id": a["url"], "url": a["url"], "publisher": a["publisher"],
           "topic": sc.get("category") or "", "lean": lean, "leanBucket": bucket,
           "headline": a.get("title", sc.get("title", "")), "publishedAt": a.get("publishedAt")}
    if sc.get("political") is not None:                 # Commit R1: mirror the additive flag
        art["political"] = bool(sc["political"])
    return art


def _base_context(be, st, uid, reads) -> dict:
    """A resolver context for a below-threshold reader from their raw stored reads (no augmented
    model): the history priorities see exactly the few reads they have, nothing invented."""
    rows = st.get_reads(uid)
    return {"reads": [{"url": er._canon(str(r.get("article_id") or "")),
                       "publisher": str(r.get("outlet") or ""), "publishedAt": r.get("read_at")}
                      for r in rows],
            "familiarity": _familiarity_from_reads(rows), "top_topics": _top_topics(rows)}


def _familiarity_from_reads(rows):
    counts: dict = {}
    for r in rows:
        counts[str(r.get("outlet") or "")] = counts.get(str(r.get("outlet") or ""), 0) + 1
    total = sum(counts.values()) or 1

    def fam(publisher):
        c = counts.get(str(publisher), 0)
        share = c / total
        return {"reads": c, "share": share, "band": engine._familiarity_band(c, share)}

    return fam


def _top_topics(rows):
    counts: dict = {}
    for r in rows:
        cat = str((r.get("category") or "")).strip()
        if cat:
            counts[cat] = counts.get(cat, 0) + 1
    return [t for t, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:3]]


def _mean_lean(reads, catalog_by_url) -> float:
    """The reader's mean lean over their catalog reads — the sign the resolver's cross-cutting gate
    uses. Computed from the same catalog leans the report would, so a probed target's crossCutting
    flag matches what the reader would actually see."""
    leans = [float(catalog_by_url[u]["lean"]) for u in reads if u in catalog_by_url]
    return sum(leans) / len(leans) if leans else 0.0
