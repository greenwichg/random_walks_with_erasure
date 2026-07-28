"""perf_profile.py — measure the engine's hot path against a catalog of a chosen size.

Why this exists: "the app got slower" is a claim about a CURVE, not a number. A single timing on
one catalog cannot distinguish "everything is uniformly a bit slow" from "one stage is quadratic
and the catalog crossed its knee". This runs the REAL code path — ``story_service._fetch`` →
``build_stories`` → ``clustering.cluster`` → ``stabilize_ids`` → response serialization — over a
synthetic catalog at several sizes and prints per-stage milliseconds and the growth exponent.

**What is real and what is not.** The CODE is production code, unmodified: the store is a real
SQLite file with the real schema, indexes and pragmas, and every stage is the one the API calls.
The DATA is synthetic — headlines are generated from a Zipf vocabulary with planted events, sized
to the shape of the live catalog (see ``--events``/``--cluster-size``). So absolute milliseconds
are indicative, and the SCALING EXPONENT is the finding: an exponent near 1.0 means a stage grows
with the catalog, near 2.0 means it grows with the square of it, and only the second explains a
system that was fine at 8k articles and slow at 22k.

    python examples/perf_profile.py                         # 5k, 10k, 20k, 40k
    python examples/perf_profile.py --sizes 20000           # just production scale
    python examples/perf_profile.py --json out.json         # machine-readable, for before/after
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import random
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

DEFAULT_SIZES = (5000, 10000, 20000, 40000)

# Headline vocabulary. Two pools on purpose: EVENT words are the distinctive tokens a real story
# shares across outlets ("tariffs", "wildfire"), FILLER is the ambient vocabulary that appears
# everywhere and inflates postings lists without ever carrying a merge. The ratio between them is
# what the clustering inverted index actually pays for.
_EVENT_WORDS = """tariffs wildfire earthquake senate election verdict merger outage ceasefire
summit recall indictment blizzard eruption protest strike ruling acquisition launch recession
inflation shooting hurricane flood drought vaccine outbreak scandal resignation impeachment
sanctions treaty referendum blackout derailment collision layoffs bankruptcy settlement subpoena
""".split()

_FILLER = """report says official sources claim amid growing concerns market analysts expect
government minister president committee response following statement decision leaders regional
national federal local council board agency department spokesman residents workers families
industry economy policy plan proposal measure effort program review inquiry investigation panel
""".split()

_PLACES = """seattle berlin london paris tokyo sydney toronto dublin madrid lagos nairobi delhi
ottawa glasgow belfast montreal auckland brisbane chicago boston denver phoenix austin portland
""".split()


#: Distinct content words the generated corpus draws from. THE CALIBRATION KNOB, and the first
#: version of this file got it badly wrong: a ~90-word vocabulary made every headline share tokens
#: with every other one, the clusterer merged the whole catalog into ONE story, and the resulting
#: timings measured a mega-cluster that production does not have. A real six-day news catalog runs
#: to tens of thousands of distinct content words. The generator is only trustworthy when its
#: story count and covered-article share land near the live ones — which ``--calibrate`` checks.
VOCAB_SIZE = 24000


def _vocabulary(rng, size: int) -> list:
    """A synthetic vocabulary of ``size`` distinct tokens, seeded with the real word lists so the
    head of the Zipf curve looks like news and the tail supplies the variety that keeps unrelated
    headlines apart."""
    seed_words = list(dict.fromkeys(_EVENT_WORDS + _FILLER + _PLACES))
    out = list(seed_words)
    while len(out) < size:
        out.append("".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(4, 9))))
    return out[:size]


ZIPF_S = [0.9]      # CALIBRATED against the live catalog shape, not chosen — see calibrate()


def _zipf_weights(n: int, s: float = None) -> list:
    s = ZIPF_S[0] if s is None else s
    return [1.0 / ((i + 1) ** s) for i in range(n)]


def _zipf_pick(rng, pool, weights=None, s=1.1):
    """Draw from ``pool`` with a Zipf-ish bias toward its head — real vocabularies are not uniform,
    and a uniform draw would understate the postings-list blowup this profile is meant to expose."""
    return rng.choices(pool, weights=weights or _zipf_weights(len(pool), s), k=1)[0]


def synth_catalog(n: int, *, events: int, cluster_size: int, seed: int = 7) -> list:
    """``n`` article dicts shaped like the live catalog: a planted set of multi-outlet events plus a
    long tail of singletons. Returns rows ready for ``Store.upsert_feed_articles``-shaped insert."""
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    publishers = [f"Publisher {i}" for i in range(400)]
    vocab = _vocabulary(rng, VOCAB_SIZE)
    weights = _zipf_weights(len(vocab))
    rows, i = [], 0

    # Planted events: the articles that SHOULD cluster. Each shares 4-6 distinctive tokens drawn
    # from the vocabulary TAIL (rare words), because that is what makes a real event's headlines
    # recognisable to each other — a shared common word is exactly what MIN_SHARED_TOKENS exists to
    # discount.
    tail = vocab[len(vocab) // 4:]
    countries = ["US", "GB", "DE", "IN", "FR", "AU", "CA", "JP", "BR", "ZA"]
    for _ in range(events):
        core = [rng.choice(tail) for _ in range(rng.randint(4, 6))]
        # ONE country per event. Without this the rows carry no event geography, geoCoherence has
        # nothing to score, and RWE_STORY_REPAIR_QUORUM — the rule that keeps production's largest
        # cluster at 111 articles — can never fire. The first calibration missed this and produced a
        # 2,521-article cluster: a corpus that disables the guard it is meant to be measured under.
        ev_country = rng.choice(countries)
        size = max(2, min(130, int(rng.paretovariate(1.25)) + 1))
        for _ in range(size):
            if i >= n:
                break
            extra = [_zipf_pick(rng, vocab, weights) for _ in range(rng.randint(3, 6))]
            words = core + extra
            rng.shuffle(words)
            row = _row(i, " ".join(words).title(), rng.choice(publishers), now, rng)
            row["eventCountries"] = [ev_country]
            rows.append(row)
            i += 1
        if i >= n:
            break

    # The tail: articles that cluster with nothing. They still cost full postings-list walks, which
    # is exactly why they belong in the measurement.
    while i < n:
        words = [_zipf_pick(rng, vocab, weights) for _ in range(rng.randint(6, 11))]
        row = _row(i, " ".join(words).title(), rng.choice(publishers), now, rng)
        row["eventCountries"] = [rng.choice(countries)] if rng.random() < 0.55 else []
        rows.append(row)
        i += 1
    return rows


def _row(i: int, title: str, publisher: str, now: datetime, rng) -> dict:
    ts = (now - timedelta(minutes=rng.randint(0, 6 * 24 * 60))).isoformat()
    lean = rng.choice([-2.0, -1.0, 0.0, 1.0, 2.0, None])
    scored = {"category": rng.choice(["politics", "world", "business", "tech", "health"]),
              "register": "report", "confidence": 0.8}
    if lean is not None:
        scored["lean"] = lean
    return {"canonicalUrl": f"https://example{i % 400}.com/a/{i}", "url": f"https://example{i % 400}.com/a/{i}",
            "publisher": publisher, "title": title,
            "description": title + " " + title, "publishedAt": ts,
            "sourceFeed": f"https://example{i % 400}.com/feed", "scored": scored,
            "country": rng.choice(["US", "GB", "DE", "IN", None])}


def _timed(fn):
    t0 = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - t0) * 1000.0


def profile_size(n: int, *, events: int, cluster_size: int, repeats: int) -> dict:
    """One catalog size, every stage timed separately. Returns a dict of stage → ms."""
    import store as store_mod
    import story_service
    import clustering

    tmp = tempfile.mkdtemp(prefix="perf-")
    db = pathlib.Path(tmp) / "perf.db"
    st = store_mod.Store(f"sqlite:///{db}")

    rows = synth_catalog(n, events=events, cluster_size=cluster_size)
    # Bulk-load for SETUP (per-row upsert at 40k would dominate the run and is not what this
    # measures). Ingestion cost is measured separately below, on a fixed sample, so it stays a
    # per-article number that does not move with ``n``.
    _, insert_ms = _timed(lambda: _bulk_load(st, rows))

    out = {"articles": n, "insertMs": round(insert_ms, 1),
           "dbBytes": db.stat().st_size if db.exists() else 0}

    # Ingestion cost per article, through the REAL upsert path (dedup, media merge, state
    # derivation). This is what the background poller pays inside the API process.
    sample = synth_catalog(200, events=20, cluster_size=3, seed=99)
    _, ing_ms = _timed(lambda: [_upsert(st, r) for r in sample])
    out["upsertMsPerArticle"] = round(ing_ms / len(sample), 3)

    # Stage 1 — fingerprint. Runs on EVERY request before the cache is even consulted.
    fp_ms = min(_timed(lambda: st.catalog_fingerprint())[1] for _ in range(max(3, repeats)))
    out["fingerprintMs"] = round(fp_ms, 2)

    # Stage 2 — the SQL fetch + event-country annotation.
    fetched, fetch_ms = _timed(lambda: story_service._fetch(st))
    out["fetchMs"] = round(fetch_ms, 1)
    out["fetchedRows"] = len(fetched)

    # Stage 3 — clustering alone, isolated from story construction.
    toks = [clustering.title_tokens(r.get("title") or "") for r in fetched]
    out["postingsCost"] = _postings_cost(toks)
    _, cluster_ms = _timed(lambda: clustering.cluster(
        fetched, tokens=lambda r: clustering.title_tokens(r.get("title") or ""),
        time=lambda r: clustering.parse_time(r.get("publishedAt") or "")))
    out["clusterMs"] = round(cluster_ms, 1)

    # Stage 4 — full story construction over the same rows (clustering + per-story assembly).
    stories, build_ms = _timed(lambda: story_service.build_stories(fetched))
    out["buildStoriesMs"] = round(build_ms, 1)
    out["stories"] = len(stories)

    # Stage 5 — id stabilization (a per-story DB round trip if it is not batched).
    if story_service.stable_ids():
        _, stab_ms = _timed(lambda: story_service.stabilize_ids(st, stories))
        out["stabilizeIdsMs"] = round(stab_ms, 1)

    # Stage 6 — cold vs warm end-to-end, the number a reader actually feels.
    story_service.clear_cache()
    _, cold_ms = _timed(lambda: story_service.list_stories(st, limit=20, offset=0))
    warm = min(_timed(lambda: story_service.list_stories(st, limit=20, offset=0))[1]
               for _ in range(max(3, repeats)))
    out["coldRequestMs"] = round(cold_ms, 1)
    out["warmRequestMs"] = round(warm, 2)

    # Stage 7 — JSON serialization of one page, the cost after the engine is done thinking.
    page = story_service.list_stories(st, limit=20, offset=0)
    _, ser_ms = _timed(lambda: json.dumps(page, default=str))
    out["serializeMs"] = round(ser_ms, 2)
    out["pageBytes"] = len(json.dumps(page, default=str))
    return out


def _upsert(st, r: dict) -> bool:
    return st.upsert_feed_article(
        canonical_url=r["canonicalUrl"], url=r["url"], publisher=r["publisher"],
        source_publisher=None, title=r["title"], description=r["description"], body=None,
        published_at=r["publishedAt"], source_feed=r["sourceFeed"], scored=r["scored"],
        country=r.get("country"), language="en")


def _bulk_load(st, rows: list) -> None:
    """Insert straight through SQLAlchemy core — the same table, indexes and pragmas, without
    paying the per-row upsert path N times during setup."""
    import store as store_mod
    from datetime import datetime as _dt
    now = _dt.now(timezone.utc)
    payload = [{"canonical_url": r["canonicalUrl"], "url": r["url"], "publisher": r["publisher"],
                "source_publisher": None, "title": r["title"], "description": r["description"],
                "body": None, "published_at": r["publishedAt"], "source_feed": r["sourceFeed"],
                "scored": json.dumps(r["scored"]), "country": r.get("country"), "language": "en",
                "fetched_at": now, "created_at": now}
               for r in rows]
    with st.session() as s:
        for chunk in (payload[i:i + 2000] for i in range(0, len(payload), 2000)):
            s.execute(store_mod.FeedArticle.__table__.insert(), chunk)


def _postings_cost(toks) -> int:
    """The clustering inner loop's true work unit: for each token, every PAIR of articles carrying
    it is one dict update. ``sum(len(postings[t]) ** 2)`` is therefore the cost the candidate walk
    pays, and it is dominated by the few highest-frequency tokens — which is why this grows with
    the square of the catalog while the article count grows linearly."""
    freq: dict = {}
    for t in toks:
        for tok in t:
            freq[tok] = freq.get(tok, 0) + 1
    return sum(v * v for v in freq.values())


def _exponent(sizes, values) -> float:
    """Least-squares slope of log(value) vs log(size) — the growth exponent. 1.0 = linear, 2.0 =
    quadratic. Reported instead of a ratio because two points can be explained by noise."""
    pts = [(math.log(s), math.log(v)) for s, v in zip(sizes, values) if s > 0 and v > 0]
    if len(pts) < 2:
        return float("nan")
    mx = sum(p[0] for p in pts) / len(pts)
    my = sum(p[1] for p in pts) / len(pts)
    num = sum((x - mx) * (y - my) for x, y in pts)
    den = sum((x - mx) ** 2 for x, _ in pts)
    return num / den if den else float("nan")


#: The live catalog, measured 2026-07-28 (audit_cluster_trust + audit_registry_coverage). The
#: generator is only worth timing if it reproduces this SHAPE — a corpus that collapses into one
#: mega-cluster measures a system nobody is running.
PRODUCTION_ENV = {"RWE_CLUSTER_MIN_SHARED": "3", "RWE_CLUSTER_MIN_TOKENS": "3",
                  "RWE_CLUSTER_IDF": "0", "RWE_CLUSTER_LINK_QUORUM": "0",
                  "RWE_STORY_REPAIR_QUORUM": "0.5", "RWE_STORY_MERGE_SIM": "0.33",
                  "RWE_STORY_MERGE_MAX_GAP": "48", "RWE_STORY_MERGE_MAX_SIZE": "130",
                  "RWE_STORY_COHERENCE_FLOOR": "0.7", "RWE_STORY_UNVERIFIED_SIZE": "50",
                  "RWE_STORY_TRUST_RANKING": "1", "RWE_STORY_MIN_RATED": "3",
                  "RWE_STORY_PUBLISHER_IDENTITY": "1", "RWE_STORY_STABLE_IDS": "1",
                  "RWE_STORIES_SCAN_DAYS": "6", "RWE_STORIES_MAX_SCAN": "60000",
                  "RWE_STORIES_CACHE_TTL": "120"}

PRODUCTION_SHAPE = {"articles": 19846, "stories": 1042, "articlesInStories": 4276,
                    "largestStoryArticles": 111, "largestStoryPublishers": 64}


def calibrate(n: int, *, events: int, cluster_size: int) -> dict:
    """Compare the generated corpus against the live catalog's shape. Printed BEFORE any timing,
    because a timing taken on the wrong corpus is worse than no timing — it is a wrong number with
    a decimal point on it."""
    import story_service
    rows = synth_catalog(n, events=events, cluster_size=cluster_size)
    stories = story_service.build_stories(rows)
    in_stories = sum(s["totalCoverage"] for s in stories)
    largest = max(stories, key=lambda s: s["totalCoverage"]) if stories else None
    scale = n / PRODUCTION_SHAPE["articles"]
    got = {"articles": n, "stories": len(stories), "articlesInStories": in_stories,
           "coveredShare": round(in_stories / n, 3) if n else 0,
           "largestStoryArticles": largest["totalCoverage"] if largest else 0}
    exp = {"stories": round(PRODUCTION_SHAPE["stories"] * scale),
           "articlesInStories": round(PRODUCTION_SHAPE["articlesInStories"] * scale),
           "coveredShare": round(PRODUCTION_SHAPE["articlesInStories"]
                                 / PRODUCTION_SHAPE["articles"], 3),
           "largestStoryArticles": PRODUCTION_SHAPE["largestStoryArticles"]}
    print(f"  calibration at n={n:,} (live shape scaled to the same catalog size)")
    for k in ("stories", "articlesInStories", "coveredShare", "largestStoryArticles"):
        ratio = (got[k] / exp[k]) if exp[k] else float("inf")
        verdict = "ok" if 0.4 <= ratio <= 2.5 else "OFF"
        print(f"    {k:<22} generated {got[k]:>8}   live-scaled {exp[k]:>8}   {ratio:>5.2f}x  {verdict}")
    return {"generated": got, "expected": exp}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--calibrate", action="store_true",
                    help="only check the corpus shape against the live catalog; take no timings")
    ap.add_argument("--sizes", type=int, nargs="*", default=list(DEFAULT_SIZES))
    ap.add_argument("--events", type=int, default=900,
                    help="planted multi-outlet events (live catalog: ~1,042 stories)")
    ap.add_argument("--cluster-size", type=int, default=4)
    ap.add_argument("--zipf", type=float, default=None,
                    help="vocabulary skew; lower = flatter. Calibrated, not guessed.")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--json", default=None, help="also write the raw measurements here")
    args = ap.parse_args(argv)

    os.environ.setdefault("RWE_LOG_LEVEL", "ERROR")
    # The deployed configuration (deploy/docker-compose.yml). Profiling under library defaults
    # would measure a system nobody runs — the repair quorum and merge threshold in particular
    # are what bound cluster size, and therefore what bound post-clustering cost.
    for k, v in PRODUCTION_ENV.items():
        os.environ.setdefault(k, v)
    if args.zipf is not None:
        ZIPF_S[0] = args.zipf
    if args.calibrate:
        calibrate(max(args.sizes), events=args.events, cluster_size=args.cluster_size)
        return 0
    results = []
    for n in sorted(args.sizes):
        r = profile_size(n, events=args.events, cluster_size=args.cluster_size,
                         repeats=args.repeats)
        results.append(r)
        print(f"  n={n:>6,}  fetch {r['fetchMs']:>7.1f}ms   cluster {r['clusterMs']:>8.1f}ms   "
              f"build {r['buildStoriesMs']:>8.1f}ms   cold {r['coldRequestMs']:>8.1f}ms   "
              f"warm {r['warmRequestMs']:>6.2f}ms   stories {r['stories']:>4}", flush=True)

    sizes = [r["articles"] for r in results]
    print("\n  stage                growth exponent (1.0 linear, 2.0 quadratic)")
    for key, label in [("fetchMs", "SQL fetch"), ("clusterMs", "clustering"),
                       ("buildStoriesMs", "build_stories"), ("stabilizeIdsMs", "stabilize_ids"),
                       ("coldRequestMs", "cold request"), ("warmRequestMs", "warm request"),
                       ("serializeMs", "serialize"), ("postingsCost", "postings work")]:
        vals = [r.get(key) for r in results]
        if any(v is None for v in vals):
            continue
        e = _exponent(sizes, vals)
        flag = "  <-- superlinear" if e >= 1.5 else ""
        print(f"  {label:<20} {e:>5.2f}{flag}")

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(results, indent=2))
        print(f"\n  wrote {args.json}")
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
