"""audit_source_lifecycle.py — M9: act on M8's evidence, and emit the change rather than make it.

**Stages 5 and 6 of `docs/SCALE_ROADMAP.md`.** Evaluates outlets with M8's own measurements, records
each evaluation in a durable ledger, applies the `source_lifecycle` state machine, and prints the
**configuration diff** a transition implies. It never edits `RWE_CORPUS_TIER_B` or
`RWE_CORPUS_SHADOW`, never restarts anything, and never touches the serving path.

## Why it emits config instead of applying it

Tier membership is not database state. It is two environment variables read by `corpus.tier_index`,
which was M1's decision: tier is a property of the outlet, derived at selection time, no article
column and no migration. So "automate promotion" can only mean one of two things here — introduce a
second, competing source of truth for tiering, or automate the *decision* and hand a human the
config. This does the second, for three reasons:

1. The roadmap already states Tier A promotion is "gated, manual, and permanently narrow", bounded
   by rating throughput. A pipeline that promoted into Tier A on its own would contradict the
   milestone it implements.
2. Every crossing of the Tier A boundary changes the story partition — the one thing this repo never
   changes without a counterfactual.
3. Applying it is a deploy either way, since the value lives in the compose allowlist. Emitting it
   costs nothing and keeps a human in the loop for free.

The ledger is still the point. `store.SourceLifecycle` pins ``first_observed`` against retention
erosion (M8 Part 10 measured an outlet's apparent history moving 50 minutes in 18), and
`store.SourceLifecycleEvent` is append-only so a decision can be re-read against the numbers it was
actually made on.

## `--commit` writes the LEDGER, never the configuration

Without it the run is entirely read-only: it shows what it would record. With it, evaluations and
transitions are written to the ledger — and the emitted config still has to be deployed by hand. A
transition recorded with ``applied=False`` is a decision, not a claim about what the running system
is doing, and the two are kept apart deliberately.

    dc run --rm -T api python examples/audit_source_lifecycle.py --db "$RWE_DB_URL" \\
        --as-if "sportskeeda.com"
    dc run --rm -T api python examples/audit_source_lifecycle.py --db "$RWE_DB_URL" --ledger
"""

from __future__ import annotations

import argparse
import os
from collections import Counter

import audit_shadow_cohort as asc
import corpus
import outlet_registry
import source_evaluation as se
import source_lifecycle as sl
import story_service
import store as store_mod


def config_diff(current: dict, moves: dict) -> dict:
    """``{env var: new comma-separated value}`` for the transitions in ``moves``.

    ``current`` is ``{"B": [...], "shadow": [...]}`` as configured today; ``moves`` is
    ``{identity: target state}``. Only vars that actually change come back, so a run with nothing to
    do prints nothing to deploy rather than a no-op diff that invites a pointless restart.

    Tier A needs no variable: it is the default, so promoting into it means *removing* the outlet
    from both lists rather than adding it to a third."""
    lists = {"B": [x for x in current.get("B", []) if x not in moves],
             "shadow": [x for x in current.get("shadow", []) if x not in moves]}
    for ident, to in sorted(moves.items()):
        if to in lists:
            lists[to].append(ident)
        # "A", "dormant" and "retired" appear in no list: A is the default tier, and dormant/retired
        # have no serving consumer yet (the probe cadence they imply is M6/M7).
    out = {}
    for tier, env in (("B", "RWE_CORPUS_TIER_B"), ("shadow", "RWE_CORPUS_SHADOW")):
        before = sorted(x.lower() for x in current.get(tier, []))
        after = sorted(x.lower() for x in lists[tier])
        if before != after:
            out[env] = ",".join(sorted(set(lists[tier])))
    return out


def configured() -> dict:
    """What the two tier variables name today, as identities the registry understood.

    Read through `corpus.tier_index` rather than by splitting the raw strings, so the diff is
    against what the system actually resolved — a misspelling that silently matches nothing must not
    reappear in the emitted value as though it were live configuration."""
    idx = corpus.tier_index()
    return {tier: sorted(set(idx[tier][0]) | set(idx[tier][1])) for tier in ("B", "shadow")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.environ.get("RWE_DB_URL"))
    ap.add_argument("--as-if", default="",
                    help="evaluate these outlets AS IF shadow (M8's --as-if), then run the state "
                         "machine over the result")
    ap.add_argument("--commit", action="store_true",
                    help="write evaluations and transitions to the LEDGER. Never writes config.")
    ap.add_argument("--ledger", action="store_true", help="print the ledger and exit")
    ap.add_argument("--confirmations", type=int, default=sl.DEFAULT_CONFIRMATIONS,
                    help="consecutive agreeing evaluations before a transition fires (default: "
                         "%(default)s)")
    ap.add_argument("--silent-days", type=int, default=sl.DEFAULT_SILENT_DAYS,
                    help="days without an article before an outlet goes dormant (default: "
                         "%(default)s)")
    ap.add_argument("--show", type=int, default=30, help="rows to list")
    args = ap.parse_args(argv)

    st = store_mod.Store(args.db)

    if args.ledger:
        events = st.source_lifecycle_events(limit=args.show)
        print(f"=== lifecycle ledger — {len(events)} most recent transitions ===")
        print("    Append-only. A decision is re-readable against the numbers it was made on,")
        print("    which is the whole reason the evidence snapshot travels with the row.")
        if not events:
            print("\n  (empty — no transition has been recorded)")
        for e in events:
            mark = "applied" if e["applied"] else "DECIDED, not applied"
            how = "auto" if e["automatic"] else "needs a human"
            print(f"\n  {e['at']}  {e['identity']}")
            print(f"    {e['from']} -> {e['to']}   [{how}; {mark}]")
            print(f"    {e['reason']}")
        return 0

    reg = outlet_registry.default_registry()
    as_if = {p.strip().lower() for p in args.as_if.split(",") if p.strip()}
    m = asc.measure(st, reg, as_if=as_if)
    table = m["table"]

    print(f"window        : from {m['windowStart']}")
    print(f"mode          : {m['mode']}")
    print(f"cohort        : {len(m['cohort']):,} articles, {len(table):,} outlets")
    if m["selfScored"]:
        # M8 refuses to REPORT on this; M9 refuses to ACT on it, which matters more.
        print(f"\n*** SELF-SCORING: {m['selfScored']:,} cohort articles are in the story set they")
        print("    are scored against. Every assignment rate would be ~100% by construction.")
        print("    Refusing to evaluate, let alone transition.")
        return 1
    if not table:
        print("\nVERDICT: INCOMPLETE — nothing to evaluate. See audit_shadow_cohort.py.")
        return 0

    scan = story_service.scan_days()
    if asc.observation_is_window_bound(table, scan):
        print(f"\n*** OBSERVATION LOOKS WINDOW-BOUND: no outlet exceeds {scan:g}d. Nothing can")
        print(f"    clear the {se.OBSERVATION_DAYS}d gate, so every verdict is INSUFFICIENT DATA")
        print("    and no transition below means anything. Refusing to act.")
        return 1

    last_seen = st.publisher_last_seen(
        {(r.get("publisher") or "").strip().lower() for r in m["cohort"]})
    states = st.source_lifecycle_states()
    now = story_service._window_start()          # any stable ISO; only used for the ledger stamp

    plans, moves = [], {}
    for ident, s in sorted(table.items(), key=lambda kv: -kv[1]["articles"]):
        verdict, why = se.evaluate(s)
        target = sl.target_for(verdict, s["tier"])

        # Days since the newest article, across every spelling of this identity in the window.
        stamps = [last_seen[k] for k in
                  {(a.get("publisher") or "").strip().lower() for a in m["cohort"]
                   if asc._identity(reg, a) == ident} if k in last_seen]
        silent = se.days_since(max(stamps)) if stamps else None

        if args.commit:
            row = st.record_source_evaluation(
                ident, target=target, verdict=verdict, evidence=s,
                first_observed=s.get("firstSeen"), last_seen=max(stamps) if stamps else None,
                initial_state=s["tier"])
        else:
            # Dry run: what the streak WOULD become, without writing it.
            prior = states.get(ident, {})
            row = {"state": prior.get("state", s["tier"]),
                   "streak": (int(prior.get("streak", 0)) + 1)
                             if prior.get("lastTarget") == target else 1}

        t = sl.plan(row["state"], verdict, streak=row["streak"], days_silent=silent,
                    confirmations=args.confirmations, silent_days=args.silent_days,
                    window_days=scan, rated=bool(s["rated"]))
        plans.append((ident, s, verdict, why, row, silent, t))
        if t is not None and t.is_move and t.automatic:
            moves[ident] = t.to

    print(f"\n=== evaluations ===")
    print("    `state` is the ledger's record; `now` is the tier the running config puts it in.")
    print("    They differ exactly when a decision has been recorded and not yet deployed — which")
    print("    is a fact worth seeing, not an inconsistency to paper over.")
    print(f"\n  {'arts':>6} {'obs_d':>6} {'silent':>7} {'state':>7} {'now':>5} {'streak':>7}  "
          f"outlet")
    for ident, s, verdict, _why, row, silent, _t in plans[:args.show]:
        obs = f"{s['observedDays']:.1f}" if s["observedDays"] is not None else "?"
        sil = f"{silent:.0f}d" if silent is not None else "?"
        print(f"  {s['articles']:>6} {obs:>6} {sil:>7} {row['state']:>7} {s['tier']:>5} "
              f"{row['streak']:>7}  {s['canonical'][:28]:<28} {verdict}")

    print(f"\n=== transitions ===")
    auto = [(i, t) for i, _s, _v, _w, _r, _sl, t in plans if t and t.is_move and t.automatic]
    held = [(i, t) for i, _s, _v, _w, _r, _sl, t in plans if t and t.is_move and not t.automatic]
    waiting = [(i, t) for i, _s, _v, _w, _r, _sl, t in plans if t and not t.is_move]
    if not (auto or held or waiting):
        print("  none — every outlet is where the evidence says it belongs.")
    for ident, t in auto:
        print(f"\n  AUTOMATIC   {ident}   {t.frm} -> {t.to}")
        print(f"              {t.reason}")
    for ident, t in held:
        print(f"\n  NEEDS A HUMAN   {ident}   {t.frm} -> {t.to}")
        print(f"              {t.reason}")
        for need in t.requires:
            print(f"              requires: {need}")
    for ident, t in waiting:
        print(f"\n  WAITING     {ident}   {t.reason}")

    if args.commit:
        for ident, _s, _v, _w, _r, _sl, t in plans:
            if t is not None and t.is_move:
                st.apply_source_transition(
                    ident, to=t.to if t.automatic else t.frm, reason=t.reason,
                    automatic=t.automatic, applied=False,
                    evidence=dict(table[ident], requires=list(t.requires)))
        print(f"\n  [--commit] ledger written. Configuration NOT changed — see below.")
    else:
        print(f"\n  [dry run] nothing written. Re-run with --commit to record the ledger.")

    print(f"\n=== the configuration change these imply ===")
    diff = config_diff(configured(), moves)
    if not diff:
        print("  none. No automatic transition changes either tier variable.")
    else:
        print("  Put these in deploy/.env and redeploy. M9 does not write them, on purpose:")
        print("  a tier change is a deploy, so emitting it keeps a human in the loop for free.")
        for env, val in sorted(diff.items()):
            print(f"\n    {env}={val}")
    print("\n  Transitions marked NEEDS A HUMAN are deliberately absent from this diff. Every")
    print("  crossing of the Tier A boundary changes the story partition, and that is the one")
    print("  thing this repo does not change without a counterfactual.")

    census = Counter(se.evaluate(s)[0] for _i, s, _v, _w, _r, _sl, _t in plans)
    print(f"\n=== verdict census ===")
    for name, n in census.most_common():
        print(f"  {n:>5} outlets  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
