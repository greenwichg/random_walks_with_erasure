"""profile_merge.py — A/B the ``_merge_duplicates`` size bound on a real corpus.

The bound is an exact test (see ``story_service._merge_duplicates``): it only ever skips pairs that
were going to score below the threshold. Correctness is settled — 150,000 randomised
``(profile, threshold)`` combinations with zero false skips, and a byte-identical pipeline across
four corpora. What is NOT settled is the speedup, and it cannot be settled on a synthetic corpus:
that corpus has misled this stage twice, once with a 2,742-member mega-cluster against production's
482, and once with zero merges where production had fifteen.

So this runs BOTH arms in ONE process against the SAME admitted clusters, seconds apart. The
comparison then cannot be contaminated by a different catalog size, a different day, or a different
box. The copy of ``_merge_duplicates`` below is faithful to the shipped one with the bound
switchable; the shipped function is timed too, as a check that the copy has not drifted from it —
if ``after == shipped`` is ever False, the copy is lying and every number here is void.

    docker exec deploy-api-1 python examples/profile_merge.py          # live store (production)
    python examples/profile_merge.py --synthetic 22000                 # no store needed

Timings are best-of-N wall AND CPU. On a 2-core box a concurrent cache warm doubles them, so the
loadavg is printed before and after: if either end is above ~1.0, re-run on an idle box.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time
import tracemalloc

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import clustering                                            # noqa: E402
import discover                                              # noqa: E402
import outlet_registry                                       # noqa: E402
import story_service as ss                                   # noqa: E402


def _loadavg() -> str:
    try:
        return open("/proc/loadavg").read().split()[0]
    except OSError:
        return "n/a"


def _corpus(args) -> list:
    """The rows the pipeline would see, live or synthetic."""
    if args.synthetic:
        import os

        import perf_profile as pp

        for k, v in pp.PRODUCTION_ENV.items():
            os.environ.setdefault(k, v)
        rows = pp.synth_catalog(args.synthetic, events=1050, cluster_size=4)
        for r in rows:
            r.setdefault("eventCountries", [])
        return rows
    import store as store_mod

    return ss._fetch(store_mod.Store())


def _admitted(rows: list) -> list:
    """Everything ``build_stories`` does before the merge pass — the merge's actual input."""
    arts = [discover.feed_article_to_article(r) for r in rows]
    if ss.exclude_wire():
        arts = [a for a in arts if not outlet_registry.is_wire(a.get("publisher"))]
    if ss.exclude_aggregator():
        arts = [a for a in arts if not outlet_registry.is_aggregator(a.get("publisher"))]
    if ss.credibility_gate():
        for a in arts:
            a["lowCredibility"] = outlet_registry.is_low_credibility(a.get("publisher"))
    groups = clustering.cluster(
        arts,
        tokens=lambda a: clustering.title_tokens(a["headline"]),
        time=lambda a: clustering.parse_time(a["publishedAt"]),
        min_shared=ss.min_shared_tokens(), min_tokens=ss.min_title_tokens(),
        idf=ss.use_idf(), link_quorum=ss.link_quorum(),
    )
    return ss._admit(groups, arts, min_articles=2, min_publishers=2)


def merge(groups: list, min_sim: float, max_gap_hours: float, max_size: int,
          use_bound: bool, count: bool = False):
    """``story_service._merge_duplicates`` with the size bound switchable.

    Kept deliberately as a COPY rather than a flag on the real function: a benchmark switch in the
    request path is a way to ship the slow arm by accident. The ``after == shipped`` check is what
    keeps the copy honest."""
    n = len(groups)
    if n < 2 or min_sim <= 0.0:
        return groups, {}
    profiles = [ss._profile(g) for g in groups]
    weights = clustering.idf_weights(profiles)
    postings: dict = {}
    for i, toks in enumerate(profiles):
        for t in toks:
            postings.setdefault(t, []).append(i)
    common = max(2, n // 2)
    total = [sum(weights.get(t, 1.0) for t in p) for p in profiles]

    def _score(i: int, j: int) -> float:
        inter = profiles[i] & profiles[j]
        if not inter:
            return 0.0
        w = sum(weights.get(t, 1.0) for t in inter)
        den = total[i] + total[j] - w
        return (w / den) if den else 0.0

    calls = 0
    if count:
        def score(i: int, j: int) -> float:                  # instrumented; never on a timed run
            nonlocal calls
            calls += 1
            return _score(i, j)
    else:
        score = _score

    bound = 1.0 + min_sim
    skipped = seen_total = 0
    pairs = []
    for i in range(n):
        seen: set = set()
        for t in profiles[i]:
            if len(postings[t]) > common:
                continue
            for j in postings[t]:
                if j > i:
                    seen.add(j)
        seen_total += len(seen)
        ti = total[i]
        for j in seen:
            if use_bound:
                tj = total[j]
                if (ti if ti < tj else tj) * bound < min_sim * (ti + tj):
                    skipped += 1
                    continue
            s = score(i, j)
            if s >= min_sim and ss._gap_hours(groups[i], groups[j]) <= max_gap_hours:
                pairs.append((s, i, j))
    stats = {"seen": seen_total, "skipped": skipped, "pairs": len(pairs), "merges": 0, "score": 0}
    if not pairs:
        stats["score"] = calls
        return groups, stats

    member_of = {i: (i,) for i in range(n)}
    for _, i, j in sorted(pairs, key=lambda p: (-p[0], p[1], p[2])):
        gi, gj = member_of[i], member_of[j]
        if gi == gj:
            continue
        if sum(len(groups[x]) for x in gi + gj) > max_size:
            continue
        if not all(score(a, b) >= min_sim for a in gi for b in gj):
            continue
        merged_members = [m for x in sorted(gi + gj) for m in groups[x]]
        coherence, located = ss._geo_coherence(merged_members, ss._country_votes(merged_members))
        if (coherence is not None and located >= ss.MIN_LOCATED_FOR_TRUST
                and coherence < ss.coherence_floor()):
            continue
        combined = tuple(sorted(gi + gj))
        for x in combined:
            member_of[x] = combined
        stats["merges"] += 1
    stats["score"] = calls

    out, done = [], set()
    for i in range(n):
        key = member_of[i]
        if key in done:
            continue
        done.add(key)
        out.append([m for x in key for m in groups[x]])
    return out, stats


def signature(out: list) -> list:
    """Order-independent identity of a clustering: which articles ended up together."""
    def key(m):
        return str(m.get("id") or m.get("url") or m.get("headline") or "")
    return sorted(tuple(sorted(key(m) for m in g)) for g in out)


def bench(arms: list, reps: int, warmup: int = 1) -> dict:
    """Round-robin every arm, rotating the order each round.

    Running arm A's reps, then B's, then C's is exactly what produced the first unusable run on the
    production box: three blocks measured under a load that moved between them, so the blocks
    reported the load as much as the code — the two arms running IDENTICAL algorithms came out
    243 ms apart. Interleaving puts every arm in the same weather; rotating the order stops any arm
    from being permanently first (cold) or last (warm).

    Returns ``{name: {"samples", "best", "cpu", "median", "out"}}``, times in ms."""
    for _, fn in arms:
        for _ in range(warmup):
            fn()
    acc = {name: [] for name, _ in arms}
    outs: dict = {}
    k = len(arms)
    for r in range(reps):
        for name, fn in arms[r % k:] + arms[:r % k]:
            c0, w0 = time.process_time(), time.perf_counter()
            outs[name] = fn()
            acc[name].append(((time.perf_counter() - w0) * 1000,
                              (time.process_time() - c0) * 1000))
    res = {}
    for name, xs in acc.items():
        walls = sorted(s[0] for s in xs)
        res[name] = {"samples": walls, "best": walls[0], "median": walls[len(walls) // 2],
                     "cpu": min(s[1] for s in xs), "out": outs[name]}
    return res


def peak_mib(fn) -> float:
    tracemalloc.start()
    try:
        fn()
        return tracemalloc.get_traced_memory()[1] / 1024 / 1024
    finally:
        tracemalloc.stop()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--synthetic", type=int, metavar="N",
                    help="use a synthetic catalog of N rows instead of the live store")
    ap.add_argument("--reps", type=int, default=5, help="timing repetitions (default 5)")
    ap.add_argument("--no-pipeline", action="store_true",
                    help="skip the whole-pipeline build_stories timing")
    args = ap.parse_args()

    la0 = _loadavg()
    t0 = time.perf_counter()
    rows = _corpus(args)
    fetch_ms = (time.perf_counter() - t0) * 1000
    adm = _admitted(rows)
    ms, gap, cap = ss.merge_similarity(), ss.merge_max_gap_hours(), ss.merge_max_size()
    sizes = sorted((len(g) for g in adm), reverse=True) or [0]
    print(f"corpus  {len(rows):,} rows -> {len(adm):,} clusters, {sum(sizes):,} members, "
          f"largest {sizes[0]:,}, median {sizes[len(sizes) // 2]:,}")
    print(f"config  merge_sim={ms}  max_gap={gap}h  max_size={cap}   "
          f"(corpus load {fetch_ms:,.0f} ms)\n")
    if ms <= 0.0:
        print("RWE_STORY_MERGE_SIM is 0 here — the merge pass is DISABLED and there is nothing to\n"
              "measure. Set it (production uses 0.33) and re-run.")
        return 2
    if len(adm) < 2:
        print("fewer than two admitted clusters — nothing to merge.")
        return 2

    r = bench([
        ("off", lambda: merge(adm, ms, gap, cap, False)[0]),
        ("on", lambda: merge(adm, ms, gap, cap, True)[0]),
        # The CONTROL arm. It runs the same algorithm as "on", so whatever separates them is the
        # harness's own error bar — and no claimed delta is allowed to be smaller than it.
        ("ship", lambda: ss._merge_duplicates(adm, min_sim=ms, max_gap_hours=gap, max_size=cap)),
    ], args.reps)
    off, on, ship = r["off"], r["on"], r["ship"]

    _, off_stats = merge(adm, ms, gap, cap, False, count=True)
    _, on_stats = merge(adm, ms, gap, cap, True, count=True)
    off_mem = peak_mib(lambda: merge(adm, ms, gap, cap, False))
    on_mem = peak_mib(lambda: merge(adm, ms, gap, cap, True))

    same_ab = signature(off["out"]) == signature(on["out"])
    same_ship = signature(on["out"]) == signature(ship["out"])

    print(f"  {'_merge_duplicates':<22}{'best ms':>9}{'median':>9}{'cpu ms':>9}"
          f"{'peak MiB':>10}{'clusters':>10}")
    for label, a, mem in (("NO bound (before)", off, f"{off_mem:.1f}"),
                          ("size bound (after)", on, f"{on_mem:.1f}"),
                          ("shipped (control)", ship, "—")):
        print(f"  {label:<22}{a['best']:>9,.0f}{a['median']:>9,.0f}{a['cpu']:>9,.0f}"
              f"{mem:>10}{len(a['out']):>10,}")
    for label, a in (("before", off), ("after", on), ("control", ship)):
        print(f"    {label:<10} reps  " + "  ".join(f"{s:,.0f}" for s in a["samples"]))

    delta = on["best"] - off["best"]
    noise = abs(ship["best"] - on["best"])
    print(f"\n  stage delta          {delta:>+9,.0f} ms ({100 * delta / off['best']:+.1f}%), "
          f"cpu {on['cpu'] - off['cpu']:+,.0f} ms, mem {on_mem - off_mem:+.1f} MiB")
    print(f"  harness error bar    {noise:>9,.0f} ms — 'after' vs 'control', two runs of the SAME "
          f"algorithm ({100 * noise / on['best']:.1f}%)")
    if noise > 0.05 * on["best"]:
        print("  VERDICT              UNUSABLE. Two identical algorithms disagree by more than 5%, "
              "so the box\n                       was contended. Re-run when idle; do not quote "
              "the delta above.")
    elif abs(delta) < 2 * noise:
        print("  VERDICT              NOT SEPARABLE. The delta is within 2x the error bar.")
    else:
        print(f"  VERDICT              REAL. {-delta:,.0f} ms is {abs(delta) / max(noise, 1e-9):.1f}x "
              f"the error bar.")
    print(f"  output equality      before == after: {same_ab}    after == control: {same_ship}")
    if not (same_ab and same_ship):
        print("  ^^ OUTPUT DIFFERS — the bound is not exact on this corpus, or the copy has drifted "
              "from the shipped function. Every timing above is void until this is explained.")

    print(f"\n  {'':<22}{'before':>12}{'after':>12}")
    for k, label in (("seen", "candidate pairs seen"), ("skipped", "skipped by bound"),
                     ("score", "score() calls"), ("pairs", "pairs kept"),
                     ("merges", "merges applied")):
        print(f"  {label:<22}{off_stats.get(k, 0):>12,}{on_stats.get(k, 0):>12,}")
    saved = off_stats.get("score", 0) - on_stats.get("score", 0)
    if off_stats.get("score"):
        print(f"  {'score() eliminated':<22}{'':>12}{saved:>11,} "
              f"({100 * saved / off_stats['score']:.1f}%)")

    if not args.no_pipeline:
        p = bench([("pipe", lambda: ss.build_stories(rows))], args.reps)["pipe"]
        print(f"\n  build_stories (whole pipeline)  {p['best']:,.0f} ms best / "
              f"{p['median']:,.0f} median / {p['cpu']:,.0f} cpu  -> {len(p['out']):,} stories")
        print(f"  merge share of pipeline         {100 * on['best'] / p['best']:.1f}%")
    print(f"\n  loadavg {la0} -> {_loadavg()}   "
          f"(above ~1.0 on a 2-core box means contended — re-run when idle)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
