"""source_campaign.py — M11: run a source-admission campaign, resumably.

**M11 of `docs/SCALE_ROADMAP.md`.** This is the writing counterpart to `audit_source_discovery.py`,
which stays exactly what its first docstring line says it is — *"Read-only: no writes, no ingestion,
no curation"*. Adding writes there would have falsified that sentence, and the sentence is the
contract a ToS reviewer reads.

    seed          Stage 1, offline. Upsert candidate rows from the retained catalogue. Idempotent.
    status        What the table holds and what a resume would do. No writes, no requests.
    probe         Stage 2. Touches publishers. Resumable, and skips everything already answered.
    admit         validated -> admitted: the shadow lane, a crawl config, a lifecycle transition.
    withdraw      admitted -> withdrawn: stop crawling. The shadow assignment is KEPT.
    reopen        Put a rejected/incomplete host back in the queue. Deliberate, not on a timer.
    emit-config   Print the equivalent env + JSON, for auditing against what is actually deployed.

## What "resumable" means here, precisely

Not "start where the list left off". The candidate ordering is by article count over a catalogue
that keeps growing, so position *k* is a different host on every run and an offset would silently
skip the wrong hosts. Resume is a **set difference over per-host state**: every host that has been
answered is in `source_admission.COMPLETED` and is not probed again, whatever order it comes back in.

Three properties follow, and each has a test that fails when the product is reverted:

* **duplicate-run idempotence** — a second `probe` over an unchanged table makes **zero** requests,
  because every host it touched is completed;
* **interruption/resume** — kill the process at host *k* and the next run probes *k+1..n*; the hosts
  before *k* cost nothing and the interrupted one is visible as `probing` rather than as untouched;
* **never re-probed unnecessarily** — `probe_count` is on the row, so this is measured rather than
  asserted: two full campaigns leave every host at exactly 1.

## What is preserved, deliberately unchanged

`--probe` still calls `source_validation.validate` with `crawler._fetch_text`, `crawler.RobotsPolicy`
(fail-closed) and `crawler.RateLimiter` (per host). The table changes **which** hosts are asked,
never **how** they are asked. The offline gates still run first in `source_discovery`, so a host the
registry already tracks or an aggregator never reaches the queue at all.

And admission assigns exactly one tier. `source_admission.check_admission_tier` refuses anything but
`shadow`, at the policy; `store.admit_source` refuses it again at the write. Tier A is M9's decision,
on M8's evidence, with a clustering counterfactual — see `source_lifecycle.crosses_tier_a`.

    # Offline. Seed the queue from the catalogue, then look at it.
    dc run --rm -T api python examples/source_campaign.py seed   --db "$RWE_DB_URL"
    dc run --rm -T api python examples/source_campaign.py status --db "$RWE_DB_URL"

    # Stage 2. Touches publishers. Requires the ToS/robots review. Start small; run it again to
    # continue — it will not re-ask anyone.
    dc run --rm -T api python examples/source_campaign.py probe  --db "$RWE_DB_URL" --limit 5
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

import corpus
import crawler
import outlet_registry
import source_admission as sa
import source_discovery as sd
import source_validation as sv
import store as store_mod


def _store(args):
    st = store_mod.Store(args.db)
    # The campaign is the other place that serves (the API's startup is the first). `admit` writes a
    # shadow assignment and `emit-config` reports one, and both would be wrong against an unwired
    # corpus — `crawler.admitted_configs` filters through `corpus.is_shadow`, which without this
    # reports every admitted host as Tier A.
    corpus.wire_admissions(st.admitted_shadow_hosts)
    return st


# --------------------------------------------------------------------------- seed
def cmd_seed(args) -> int:
    st = _store(args)
    reg = outlet_registry.default_registry()
    rows = st.list_discovery_rows(exclude_publishers=corpus.sql_exclusions())
    cands = sd.candidates(rows, reg, floor=args.floor)
    stats = sd.census(cands)
    counts = st.record_admission_candidates(cands)

    print(f"catalogue          : {len(rows):,} articles")
    print(f"hosts seen         : {stats.get('total', 0):,}")
    print(f"  already tracked  : {stats.get('tracked', 0):,}")
    print(f"  aggregator/proxy : {stats.get('proxy', 0):,}")
    print(f"  below the {args.floor}-article floor : {stats.get('belowFloor', 0):,}")
    print(f"  ELIGIBLE         : {stats.get('eligible', 0):,}")
    print(f"\n=== seeding ===")
    print("    Idempotent: a state is never downgraded and probe accounting is never reset, so")
    print("    re-seeding an ever-growing catalogue re-offers the same hosts and changes nothing")
    print("    about what has already been answered. Only the EVIDENCE is refreshed.")
    print(f"  inserted (new candidates) : {counts['inserted']:,}")
    print(f"  refreshed (evidence moved): {counts['refreshed']:,}")
    print(f"  unchanged                 : {counts['unchanged']:,}")
    print(f"  skipped (a gate rejects)  : {counts['skipped']:,}")
    _print_status(st, args)
    return 0


# --------------------------------------------------------------------------- status
def _print_status(st, args) -> None:
    census = st.admission_census()
    print(f"\n=== the admission table ===")
    for state in sa.STATES:
        n = census.get(state, 0)
        if n:
            print(f"  {n:>7,}  {state}")
    print(f"  {'-' * 7}")
    print(f"  {census.get('total', 0):>7,}  total")
    print(f"\n  probes made so far      : {census.get('probes', 0):,}")
    print(f"  requests spent on publishers: {census.get('requests', 0):,}")
    print("    From the record, not an estimate. This is the number a ToS review is asking about.")

    queue = _queue(st, args, limit=0)
    print(f"\n=== what a probe run would do now ===")
    print(f"  {len(queue):,} host(s) would be probed"
          + (f", capped at {args.limit:,} by --limit" if args.limit else ""))
    stale = [r for r in st.admission_rows(states=["probing"])
             if st.admission_skip_reason(r["host"], stale_minutes=args.stale_minutes)]
    if stale:
        print(f"\n  *** {len(stale):,} host(s) are `probing` and still inside the "
              f"{args.stale_minutes:g}-minute in-flight window.")
        print("      They were claimed by a run that has not reported. There is no way to tell a")
        print("      crashed run from a live one without a liveness channel, so they are held back")
        print("      rather than risk two campaigns hitting one publisher at once. They become")
        print("      probeable when the window expires; `--stale-minutes 0` overrides it if you are")
        print("      certain nothing else is running.")
        for r in stale[:10]:
            print(f"      {r['host']:<40} claimed {r['claimedAt']}")


def cmd_status(args) -> int:
    st = _store(args)
    _print_status(st, args)
    validated = st.admission_rows(states=["validated"])
    if validated:
        impacts = [st.admission_partition_impact(r["host"]) for r in validated]
        win = sum(i["window"] for i in impacts)
        cat = sum(i["catalogue"] for i in impacts)
        print(f"\n=== what admitting the {len(validated):,} validated host(s) would move ===")
        print("    Admission is an A -> shadow move on rows that are LIVE, because every candidate")
        print("    is a host we already ingest. It is a partition change, not an addition.")
        print(f"  {win:,} article(s) would leave the {impacts[0]['windowDays']:g}-day story partition")
        print(f"  {cat:,} article(s) would leave Search and Discover")
    if args.show:
        for state in ("validated", "rejected", "incomplete"):
            rows = st.admission_rows(states=[state], limit=args.show)
            if not rows:
                continue
            print(f"\n=== {state} ({st.admission_census().get(state, 0):,}) ===")
            for r in rows:
                failed = [g for g in r["gates"] if g.get("status") == sv.FAIL]
                unknown = [g for g in r["gates"] if g.get("status") == sv.UNKNOWN]
                note = (f"gate {failed[0]['number']}: {failed[0].get('detail') or failed[0]['name']}"
                        if failed else
                        f"{len(unknown)} gate(s) unanswered" if unknown else
                        (r["feedUrl"] or ""))
                print(f"  {r['articles']:>6,}  {r['host'][:38]:<38} {note[:70]}")
    return 0


# --------------------------------------------------------------------------- probe
def _queue(st, args, *, limit=None) -> list:
    """The hosts a probe run would claim, in table order, WITHOUT claiming them.

    A dry read of the same predicate the claim uses, so `status` and `probe` cannot disagree about
    what is next — the two would drift the moment either grew a condition the other lacked."""
    cap = args.limit if limit is None else limit
    wanted = ({h.strip().lower() for h in args.hosts.split(",") if h.strip()}
              if args.hosts else None)
    out = []
    for row in st.admission_rows(states=sa.PROBEABLE, hosts=wanted):
        decision = sa.may_probe(row, now=datetime.now(timezone.utc), force=args.force,
                                stale_minutes=args.stale_minutes)
        if decision.allowed:
            out.append(row)
        if cap and len(out) >= cap:
            break
    return out


def cmd_probe(args) -> int:
    st = _store(args)
    queue = _queue(st, args)
    if args.hosts:
        wanted = {h.strip().lower() for h in args.hosts.split(",") if h.strip()}
        for host in sorted(wanted - {r["host"] for r in queue}):
            print(f"*** NOT PROBED  {host}: {st.admission_skip_reason(host, force=args.force, stale_minutes=args.stale_minutes)}")

    census = st.admission_census()
    print(f"\n=== STAGE 2: PROBING {len(queue):,} HOSTS ===")
    print(f"    User-Agent: {crawler.USER_AGENT}")
    print(f"    robots.txt is fail-CLOSED: absent or unparseable is a refusal, not permission.")
    print(f"    Rate limited to {args.interval:g}s per host. No ingestion, no writes to the catalogue.")
    print(f"    {census.get('total', 0) - len(queue):,} of {census.get('total', 0):,} hosts in the "
          f"table are NOT being probed this run — already answered, cooling off, in flight, or "
          f"beyond --limit.")
    if args.dry_run:
        for r in queue:
            print(f"  would probe  {r['articles']:>6,}  {r['host']}")
        print(f"\n  --dry-run: no request was made.")
        return 0

    limiter = crawler.RateLimiter(default_interval=args.interval)
    robots = crawler.RobotsPolicy()
    spent, done = 0, {"validated": 0, "rejected": 0, "incomplete": 0}
    for row in queue:
        claim = st.claim_admission_probe(row["host"], force=args.force,
                                         stale_minutes=args.stale_minutes)
        if claim is None:
            # Lost the race to a concurrent campaign between the queue read and the claim. Exactly
            # what the claim is for; skipping is the correct outcome and it is reported, not hidden.
            print(f"  skipped  {row['host']}: {st.admission_skip_reason(row['host'], stale_minutes=args.stale_minutes)}")
            continue
        cand = {"host": row["host"], "language": row["language"] or "",
                "tracked": False, "proxy": False}
        try:
            result = sv.validate(cand, fetch=crawler._fetch_text, robots=robots, limiter=limiter)
        except Exception as exc:
            # OUR failure, not the publisher's: recorded as INCOMPLETE, which is retryable, rather
            # than as a rejection, which is not. A transport error must never become a permanent
            # verdict about a source. `KeyboardInterrupt`/`SystemExit` are BaseExceptions and are
            # deliberately NOT caught — a real interruption leaves the row `probing`, which is what
            # makes it visible as an interruption on the next run.
            result = {"verdict": "INCOMPLETE", "requests": 0, "feed": "", "discoveredVia": "",
                      "samples": [], "gates": [sv.Gate(0, "the probe itself", sv.UNKNOWN,
                                                       f"{type(exc).__name__}: {exc}")]}
        gates = [{"number": g.number, "name": g.name, "status": g.status, "detail": g.detail}
                 for g in sorted(result["gates"], key=lambda g: g.number)]
        st.record_admission_probe(row["host"], verdict=result["verdict"], gates=gates,
                                  feed_url=result.get("feed", ""),
                                  discovered_via=result.get("discoveredVia", ""),
                                  samples=result.get("samples", []),
                                  requests=result.get("requests", 0))
        spent += result.get("requests", 0)
        done[sa.state_for_verdict(result["verdict"])] += 1
        print(f"\n  {result['verdict']:<11} {row['host']}   ({result.get('requests', 0)} request(s))")
        for g in gates:
            mark = {sv.PASS: "ok  ", sv.FAIL: "FAIL", sv.UNKNOWN: "??  "}[g["status"]]
            print(f"      [{mark}] gate {g['number']} {g['name']}"
                  + (f" — {g['detail']}" if g["detail"] else ""))
        if result.get("feed"):
            print(f"      discovered via  : {result['discoveredVia']} — {result['feed']}")
        for u in result.get("samples", []):
            print(f"      sample article  : {u}")

    print(f"\n=== results ===")
    for state, n in sorted(done.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>5} hosts  {state}")
    print(f"\n  requests spent this run : {spent:,}")
    print(f"  waited for politeness   : {limiter.waited_seconds:.1f}s")
    _print_status(st, args)
    if done["validated"]:
        print(f"\n=== the {done['validated']} validated host(s) ===")
        print("  Still nothing serving. `admit` is a separate command on purpose: a passed probe")
        print("  says a source is technically ingestible, not that we have decided to carry it.")
        print("  python examples/source_campaign.py admit --db ... --hosts <comma-separated>")
    return 0


# --------------------------------------------------------------------------- admit
def cmd_admit(args) -> int:
    st = _store(args)
    if args.hosts:
        wanted = [h.strip().lower() for h in args.hosts.split(",") if h.strip()]
    elif args.all_validated:
        wanted = [r["host"] for r in st.admission_rows(states=["validated"], limit=args.limit)]
    else:
        print("Refusing to admit nothing. Pass --hosts, or --all-validated to take the whole "
              "validated set.\nAdmission puts a source into serving configuration; it is not a "
              "default.")
        return 2
    if not wanted:
        print("no hosts to admit")
        return 0

    print(f"=== ADMITTING {len(wanted)} HOST(S) TO THE {sa.ADMISSION_TIER} LANE ===")
    print("    Not Tier A, and there is no flag that would make it Tier A. Tier A promotion is M9's")
    print("    decision on M8's evidence with a clustering counterfactual — source_lifecycle.plan.")
    print("    Shadow means stored, deduped, attributed, and surfaced NOWHERE, pending evaluation.")

    # ---------------------------------------------------------------- the pre-flight
    #
    # Every candidate is a host we ALREADY ingest — that is what discovery is, and the 10-article
    # floor guarantees a history. Its articles are Tier A today, so admission is an A -> shadow move
    # on live rows: they leave the story partition AND every reader surface. "Admit this source" and
    # "take N articles out of Search" are the same command, and only one of them is in the name.
    impacts = [st.admission_partition_impact(h) for h in wanted]
    live = [i for i in impacts if i["window"] or i["catalogue"]]
    if live:
        win = sum(i["window"] for i in live)
        cat = sum(i["catalogue"] for i in live)
        print(f"\n  *** THIS IS A PARTITION CHANGE, NOT AN ADDITION ***")
        print(f"      {len(live)} of these {len(wanted)} host(s) are already in the catalogue.")
        print(f"      {win:,} article(s) inside the {live[0]['windowDays']:g}-day clustering window "
              f"would LEAVE the story partition.")
        print(f"      {cat:,} article(s) in the catalogue would LEAVE Search and Discover.")
        print(f"      source_lifecycle.crosses_tier_a('A', 'shadow') is True; M9 marks this move")
        print(f"      automatic=False and requires a clustering counterfactual for it.")
        for i in sorted(live, key=lambda i: -i["catalogue"])[:10]:
            print(f"        {i['host']:<38} {i['window']:>7,} in window   {i['catalogue']:>7,} in catalogue")
        if not args.accept_partition_change:
            print(f"\n  REFUSING. Measure it first, then say so explicitly:")
            print(f"    python examples/audit_clustering_change.py --db ...")
            print(f"    python examples/source_campaign.py admit ... --accept-partition-change")
            return 2

    ok, failed = 0, 0
    for host in wanted:
        try:
            row = st.admit_source(host, tier=sa.ADMISSION_TIER, publisher=args.publisher or None,
                                  article_pattern=args.pattern, force=args.force,
                                  reason=args.reason or "",
                                  accept_partition_change=args.accept_partition_change)
        except ValueError as exc:
            print(f"  REFUSED  {host}: {exc}")
            failed += 1
            continue
        ok += 1
        fields = sa.crawl_config_fields(row)
        print(f"  admitted {host}")
        print(f"      publisher       : {fields['publisher']}"
              + ("" if fields["publisher"] != host
                 else "   (the host — the registry has no outlet for it, which is expected here)"))
        print(f"      discovery       : {row['discoveredVia']} — {row['feedUrl']}")
        print(f"      max_age_days    : {fields['max_age_days']} "
              f"(an archive sitemap and a news sitemap are the same file format)")
        if not fields["article_pattern"]:
            print(f"      article_pattern : NONE — every on-domain URL is accepted. Write one from")
            print(f"                        the sample URLs and re-admit with --pattern if this")
            print(f"                        source's discovery document lists non-articles.")
    print(f"\n  {ok} admitted, {failed} refused")
    if ok:
        print("\n  The crawl still needs RWE_CRAWL_ENABLED=1, which defaults to OFF, and the API")
        print("  process must be restarted to pick up the new adapters — the registry is built once")
        print("  at startup. The tier assignment is live immediately (within corpus's 60s snapshot).")
    return 0 if not failed else 1


def cmd_withdraw(args) -> int:
    st = _store(args)
    hosts = [h.strip().lower() for h in (args.hosts or "").split(",") if h.strip()]
    if not hosts:
        print("--hosts is required")
        return 2
    for host in hosts:
        try:
            st.withdraw_source(host, reason=args.reason or "")
        except ValueError as exc:
            print(f"  REFUSED  {host}: {exc}")
            continue
        print(f"  withdrawn {host} — the crawl stops; the shadow assignment is KEPT so its existing")
        print(f"            articles do not fall back to the Tier A default.")
    return 0


def cmd_reopen(args) -> int:
    st = _store(args)
    hosts = [h.strip().lower() for h in (args.hosts or "").split(",") if h.strip()]
    if not hosts:
        print("--hosts is required")
        return 2
    for host in hosts:
        try:
            row = st.reopen_admission(host, reason=args.reason or "")
        except ValueError as exc:
            print(f"  REFUSED  {host}: {exc}")
            continue
        print(f"  reopened {host} (probed {row['probeCount']} time(s) before; the count is kept)")
    return 0


# --------------------------------------------------------------------------- emit-config
def cmd_emit_config(args) -> int:
    """What the table's admissions would look like as the configuration they replace.

    Not a migration step — the table IS the source of truth now. This exists so the two can be
    compared: an operator with `RWE_CORPUS_SHADOW` still set can see exactly what the table adds,
    and a reviewer can read the crawl configs as the same JSON shape they already know."""
    st = _store(args)
    hosts = sorted(st.admitted_shadow_hosts())
    configs = crawler.admitted_configs(st)
    print("# The shadow assignments the admission table contributes. UNIONED with")
    print("# RWE_CORPUS_SHADOW, never replacing it — see corpus.admitted_shadow_hosts.")
    print(f"# {len(hosts)} host(s).")
    print(f"RWE_CORPUS_SHADOW_FROM_TABLE={','.join(hosts)}" if hosts else "# (none)")
    print(f"\n# The crawl configs, in crawler_publishers.json shape. {len(configs)} publisher(s).")
    print(json.dumps({"publishers": [
        {"publisher": c.publisher, "domains": list(c.domains),
         "discovery_domains": list(c.discovery_domains),
         "sources": [{"kind": s.kind, "url": s.url} for s in c.sources],
         "article_pattern": c.article_pattern, "max_age_days": c.max_age_days,
         "max_urls": c.max_urls, "enabled": c.enabled} for c in configs]},
        indent=2, sort_keys=True))
    return 0


# --------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.environ.get("RWE_DB_URL"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, *, probing=False):
        p.add_argument("--db", default=os.environ.get("RWE_DB_URL"))
        p.add_argument("--show", type=int, default=0, help="rows to list per state")
        p.add_argument("--limit", type=int, default=0)
        p.add_argument("--hosts", default="")
        p.add_argument("--force", action="store_true",
                       help="override the state guard. Re-probes an answered host, or admits an "
                            "unvalidated one. Deliberately loud in the output.")
        p.add_argument("--stale-minutes", type=float, default=sa.STALE_PROBE_MINUTES,
                       help="minutes after which a `probing` claim is presumed dead rather than "
                            "in flight (default: %(default)s). 0 disables the in-flight guard — "
                            "only correct when nothing else is running.")
        p.add_argument("--floor", type=int, default=sd.VOLUME_FLOOR)
        p.add_argument("--interval", type=float, default=crawler.DEFAULT_MIN_INTERVAL)
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--publisher", default="")
        p.add_argument("--pattern", default=None)
        p.add_argument("--reason", default="")
        p.add_argument("--all-validated", action="store_true")
        p.add_argument("--accept-partition-change", action="store_true",
                       help="acknowledge that admitting a host we already ingest REMOVES its "
                            "articles from the story partition and from Search. Required whenever "
                            "the hosts being admitted have live rows — which, since discovery "
                            "mines the crawl exhaust, is nearly always.")
        return p

    common(sub.add_parser("seed", help="upsert candidate rows from the catalogue (offline)"))
    common(sub.add_parser("status", help="what the table holds and what a probe would do"))
    common(sub.add_parser("probe", help="STAGE 2: probe candidates. Touches publishers."))
    common(sub.add_parser("admit", help="validated -> admitted, into the shadow lane"))
    common(sub.add_parser("withdraw", help="admitted -> withdrawn; the shadow assignment is kept"))
    common(sub.add_parser("reopen", help="put a rejected/incomplete host back in the queue"))
    common(sub.add_parser("emit-config", help="the table's admissions as env + JSON, for auditing"))

    args = ap.parse_args(argv)
    return {"seed": cmd_seed, "status": cmd_status, "probe": cmd_probe, "admit": cmd_admit,
            "withdraw": cmd_withdraw, "reopen": cmd_reopen,
            "emit-config": cmd_emit_config}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
