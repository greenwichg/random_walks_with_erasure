"""profile_clustering.py — a stage-by-stage timing breakdown of story clustering.

The cache investigation ended by showing the scheduler is not the problem: every rebuild serves a
genuinely changed catalog, and the ~5.6 s it costs is the CLUSTERER's. This profiles that 5.6 s.

Three views, because each answers a different question:

* ``--stages`` — wall + CPU + peak allocations for every stage of ``build_stories``, at several
  catalog sizes, with a fitted growth exponent per stage. This is the flame-graph substitute: it
  says WHERE the time goes and, more usefully, which parts get worse faster than the catalog grows.
* ``--functions`` — cProfile over one build, cumulative time by function. This is the flame graph.
* ``--redundancy`` — counts of work done more than once: registry resolutions per article,
  ``title_tokens`` calls per headline, ``_build_story`` calls per cluster. A repeated computation is
  the only kind of cost that can be removed without changing a single output.

    python examples/profile_clustering.py --stages
    python examples/profile_clustering.py --functions --size 20000
    python examples/profile_clustering.py --redundancy --size 20000
"""

from __future__ import annotations

import argparse
import cProfile
import io
import json
import math
import os
import pathlib
import pstats
import sys
import time
import tracemalloc

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import perf_profile as pp                                    # noqa: E402

STAGE_SIZES = (5000, 10000, 20000, 40000)


def _corpus(n: int) -> list:
    rows = pp.synth_catalog(n, events=1050, cluster_size=4)
    for r in rows:
        r.setdefault("eventCountries", [])
    return rows


class Timer:
    """Wall, CPU and peak-allocation for a block. CPU and wall are both reported because they
    diverge for different reasons: a gap means waiting (I/O, locks), equality means the stage is
    pure compute and only an algorithm change will move it."""

    def __init__(self, name, out, trace=False):
        self.name, self.out, self.trace = name, out, trace

    def __enter__(self):
        if self.trace:
            tracemalloc.start()
        self.w0, self.c0 = time.perf_counter(), time.process_time()
        return self

    def __exit__(self, *a):
        w = (time.perf_counter() - self.w0) * 1000.0
        c = (time.process_time() - self.c0) * 1000.0
        peak = 0
        if self.trace:
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        self.out[self.name] = {"wallMs": round(w, 1), "cpuMs": round(c, 1),
                               "peakMiB": round(peak / 1024 / 1024, 1)}
        return False


def profile_stages(n: int, trace: bool = False) -> dict:
    """Re-implements ``build_stories``' sequence with a timer around each stage.

    Deliberately a COPY of the sequence rather than instrumentation injected into the real
    function: build_stories is a pure function the whole suite depends on, and threading profiling
    hooks through it would risk changing what it computes. The copy is checked against the real
    thing below — same story count, or the profile is not describing production."""
    import clustering
    import discover
    import outlet_registry
    import publisher_identity
    import story_service as ss

    rows = _corpus(n)
    out: dict = {"articles": n}

    with Timer("1_feed_article_to_article", out, trace):
        arts = [discover.feed_article_to_article(r) for r in rows]

    with Timer("2_registry_filters", out, trace):
        if ss.exclude_wire():
            arts = [a for a in arts if not outlet_registry.is_wire(a.get("publisher"))]
        if ss.exclude_aggregator():
            arts = [a for a in arts if not outlet_registry.is_aggregator(a.get("publisher"))]
        if ss.credibility_gate():
            for a in arts:
                a["lowCredibility"] = outlet_registry.is_low_credibility(a.get("publisher"))

    with Timer("3_publisher_identity", out, trace):
        if ss.publisher_identity_enabled():
            keys = publisher_identity.groups({a["publisher"] for a in arts})
            for a in arts:
                a["publisherKey"] = keys.get(a["publisher"], a["publisher"])

    with Timer("4_tokenize", out, trace):
        toks = [clustering.title_tokens(a["headline"]) for a in arts]
    out["tokenStats"] = _token_stats(toks)

    with Timer("5_cluster", out, trace):
        groups = clustering.cluster(
            arts, tokens=lambda a: clustering.title_tokens(a["headline"]),
            time=lambda a: clustering.parse_time(a["publishedAt"]),
            sim=clustering.DEFAULT_SIM, window_days=clustering.DEFAULT_WINDOW_DAYS,
            min_shared=ss.min_shared_tokens(), min_tokens=ss.min_title_tokens(),
            idf=ss.use_idf(), link_quorum=ss.link_quorum())

    with Timer("6_admit", out, trace):
        admitted = ss._admit(groups, arts, min_articles=2, min_publishers=2)

    with Timer("7_trust_check_and_repair", out, trace):
        mend = ss.repair_quorum()
        kept = []
        trust_calls = 0
        for members in admitted:
            if mend > 0.0:
                trust_calls += 1
                if ss._build_story(members)["clusterTrust"] == ss.TRUST_LOW:
                    pieces = ss._repair(members, quorum=mend, sim=clustering.DEFAULT_SIM,
                                        window_days=clustering.DEFAULT_WINDOW_DAYS,
                                        min_shared=ss.min_shared_tokens(),
                                        min_tokens=ss.min_title_tokens(), idf=ss.use_idf(),
                                        min_articles=2, min_publishers=2)
                    if pieces is not None:
                        kept.extend(pieces)
                        continue
            kept.append(members)
    out["trustCheckBuildStoryCalls"] = trust_calls

    with Timer("8_merge_duplicates", out, trace):
        join = ss.merge_similarity()
        if join > 0.0:
            kept = ss._merge_duplicates(kept, min_sim=join,
                                        max_gap_hours=ss.merge_max_gap_hours(),
                                        max_size=ss.merge_max_size())

    with Timer("9_build_story", out, trace):
        stories = [ss._build_story(m) for m in kept]

    with Timer("10_sort", out, trace):
        trust_aware = ss.trust_ranking()
        stories.sort(key=lambda s: ss._size_rank(s, trust_aware=trust_aware), reverse=True)

    out["stories"] = len(stories)
    out["totalWallMs"] = round(sum(v["wallMs"] for k, v in out.items()
                                   if isinstance(v, dict) and "wallMs" in v), 1)
    # Fidelity check: the copied sequence must produce what the real function produces, or every
    # number above describes something nobody runs.
    real = ss.build_stories(_corpus(n))
    out["realStories"] = len(real)
    out["fidelityOk"] = (len(real) == len(stories))
    return out


def _token_stats(toks) -> dict:
    freq: dict = {}
    for t in toks:
        for tok in t:
            freq[tok] = freq.get(tok, 0) + 1
    if not freq:
        return {}
    vals = sorted(freq.values(), reverse=True)
    return {"distinctTokens": len(freq), "totalTokenSlots": sum(vals),
            # sum(df^2) is the candidate walk's true work unit: for each token, every PAIR of
            # articles carrying it is one dict update.
            "postingsWork": sum(v * v for v in vals),
            "top10Df": vals[:10],
            "workFromTop10Pct": round(100.0 * sum(v * v for v in vals[:10])
                                      / sum(v * v for v in vals), 1)}


def profile_functions(n: int, top: int = 30) -> str:
    import story_service as ss
    rows = _corpus(n)
    pr = cProfile.Profile()
    pr.enable()
    ss.build_stories(rows)
    pr.disable()
    buf = io.StringIO()
    pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(top)
    return buf.getvalue()


def profile_redundancy(n: int) -> dict:
    """Count work done more than once. A repeated computation is the only cost that can be removed
    without changing a single output, so this is where a safe optimization has to come from."""
    import clustering
    import outlet_registry
    import story_service as ss

    rows = _corpus(n)
    counts = {"resolve": 0, "titleTokens": 0, "buildStory": 0}
    seen_publishers: dict = {}
    seen_headlines: dict = {}

    # Patch the METHOD, not the module-level convenience function. The first version of this
    # profiler patched `outlet_registry.resolve` and reported 400 calls for 20,000 articles — but
    # `is_wire`/`is_aggregator`/`is_low_credibility` call `default_registry().is_wire(...)`, which
    # reaches `OutletRegistry.resolve` directly and never passes through the module function. The
    # instrumentation was measuring a path the hot loop does not take.
    real_resolve = outlet_registry.OutletRegistry.resolve
    def counting_resolve(self, text):
        counts["resolve"] += 1
        seen_publishers[text] = seen_publishers.get(text, 0) + 1
        return real_resolve(self, text)
    outlet_registry.OutletRegistry.resolve = counting_resolve

    real_tokens = clustering.title_tokens
    def counting_tokens(title):
        counts["titleTokens"] += 1
        seen_headlines[title] = seen_headlines.get(title, 0) + 1
        return real_tokens(title)
    clustering.title_tokens = counting_tokens

    real_build = ss._build_story
    def counting_build(members):
        counts["buildStory"] += 1
        return real_build(members)
    ss._build_story = counting_build

    try:
        stories = ss.build_stories(rows)
    finally:
        outlet_registry.OutletRegistry.resolve = real_resolve
        clustering.title_tokens = real_tokens
        ss._build_story = real_build

    distinct_pub = len(seen_publishers)
    distinct_head = len(seen_headlines)
    return {
        "articles": n, "stories": len(stories),
        "resolveCalls": counts["resolve"], "distinctPublisherStrings": distinct_pub,
        "resolvePerArticle": round(counts["resolve"] / n, 2),
        "resolveWasteFactor": round(counts["resolve"] / distinct_pub, 1) if distinct_pub else 0,
        "titleTokensCalls": counts["titleTokens"], "distinctHeadlines": distinct_head,
        "titleTokensPerHeadline": round(counts["titleTokens"] / distinct_head, 2) if distinct_head else 0,
        "buildStoryCalls": counts["buildStory"],
        "buildStoryPerStory": round(counts["buildStory"] / len(stories), 2) if stories else 0,
    }


def _exponent(sizes, values) -> float:
    pts = [(math.log(s), math.log(v)) for s, v in zip(sizes, values) if s > 0 and v > 0]
    if len(pts) < 2:
        return float("nan")
    mx = sum(p[0] for p in pts) / len(pts)
    my = sum(p[1] for p in pts) / len(pts)
    den = sum((x - mx) ** 2 for x, _ in pts)
    return (sum((x - mx) * (y - my) for x, y in pts) / den) if den else float("nan")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stages", action="store_true")
    ap.add_argument("--functions", action="store_true")
    ap.add_argument("--redundancy", action="store_true")
    ap.add_argument("--size", type=int, default=20000)
    ap.add_argument("--sizes", type=int, nargs="*", default=list(STAGE_SIZES))
    ap.add_argument("--trace-alloc", action="store_true", help="peak allocations per stage (slow)")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    os.environ.setdefault("RWE_LOG_LEVEL", "ERROR")
    for k, v in pp.PRODUCTION_ENV.items():
        os.environ.setdefault(k, v)

    if not (args.stages or args.functions or args.redundancy):
        args.stages = True

    results = []
    if args.stages:
        for n in sorted(args.sizes):
            r = profile_stages(n, trace=args.trace_alloc)
            results.append(r)
            stages = [(k, v) for k, v in r.items() if isinstance(v, dict) and "wallMs" in v]
            print(f"\n  n={n:,}  total {r['totalWallMs']:,.0f} ms   stories {r['stories']}"
                  f"   fidelity {'OK' if r['fidelityOk'] else 'MISMATCH'}")
            for k, v in sorted(stages, key=lambda kv: -kv[1]["wallMs"]):
                share = 100.0 * v["wallMs"] / r["totalWallMs"] if r["totalWallMs"] else 0
                alloc = f"  peak {v['peakMiB']:>6.1f} MiB" if args.trace_alloc else ""
                print(f"      {k:<28} {v['wallMs']:>9.1f} ms  {share:>5.1f}%"
                      f"   cpu {v['cpuMs']:>9.1f} ms{alloc}")
            print(f"      postings work {r['tokenStats']['postingsWork']:,}"
                  f"   top-10 tokens are {r['tokenStats']['workFromTop10Pct']}% of it")

        if len(results) > 1:
            print("\n  growth exponent per stage (1.0 linear, 2.0 quadratic)")
            sizes = [r["articles"] for r in results]
            keys = [k for k, v in results[0].items() if isinstance(v, dict) and "wallMs" in v]
            rows_ = []
            for k in keys:
                vals = [r[k]["wallMs"] for r in results]
                rows_.append((k, _exponent(sizes, vals), vals[-1]))
            for k, e, last in sorted(rows_, key=lambda t: -t[2]):
                flag = "  <-- superlinear" if e >= 1.5 else ""
                print(f"      {k:<28} {e:>5.2f}   ({last:,.0f} ms at n={sizes[-1]:,}){flag}")

    if args.functions:
        print(f"\n=== cProfile, cumulative, n={args.size:,} ===")
        print(profile_functions(args.size))

    if args.redundancy:
        r = profile_redundancy(args.size)
        print(f"\n=== repeated work, n={args.size:,} ===")
        print(f"  outlet_registry.resolve   {r['resolveCalls']:>9,} calls   "
              f"{r['resolvePerArticle']} per article   over {r['distinctPublisherStrings']:,} distinct "
              f"publisher strings = {r['resolveWasteFactor']}x")
        print(f"  clustering.title_tokens   {r['titleTokensCalls']:>9,} calls   "
              f"{r['titleTokensPerHeadline']} per distinct headline")
        print(f"  story_service._build_story{r['buildStoryCalls']:>9,} calls   "
              f"{r['buildStoryPerStory']} per story returned")
        results.append(r)

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
