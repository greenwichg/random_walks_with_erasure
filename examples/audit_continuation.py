#!/usr/bin/env python3
"""Story Continuation audit — "would this feature ever fire, and where does it die?"

Phase 1 ships the resolver dark: nothing calls it, so no user-visible behaviour can be observed to
verify it. This script is how it gets verified instead — by running the REAL resolver over the REAL
production store and reporting, mechanically, what it says.

It answers the question ``docs/STORY_CONTINUATION_DESIGN.md`` §0.1 raised: the gates are strict by
design (trusted cluster, non-template genre, unread different-outlet sibling, BOTH outlets rated,
genuinely opposing), and nobody knows what share of real reads clears all of them. A low number is
not a failure of this phase — it is the number that decides whether the next phases are worth
building, or whether registry lean coverage is the better investment.

Two populations, because they answer different questions:

  realized (default)  every stored read, per reader — what share of reads that ACTUALLY happened
                      would have armed a strip. The honest measure, but bounded by how much reading
                      the beta has done.
  ceiling (--ceiling) every cluster member as a hypothetical anchor, with no reader. Ignores the
                      unread and freshness gates (there is no reader to have read anything), so it
                      measures the STRUCTURAL ceiling the catalog allows: how many stories even
                      contain an opposing rated pair. Always >= the realized rate.

Attribution names the FIRST gate that stopped each anchor, in the resolver's own order. The verdict
itself always comes from ``story_continuation.resolve`` — attribution is diagnostic only, and the
script self-checks that the two agree (a disagreement means this audit has drifted from the module
it audits, and it says so loudly rather than reporting a comfortable number).

**Read-only end to end.** No writes, no clustering on a request thread, no network, no model.
By default it reads the story index the poller already warmed; ``--inline`` opts into a full
read-only clustering, which on a small production host costs real CPU — see the warning it prints.

    docker exec deploy-api-1 python examples/audit_continuation.py
    docker exec deploy-api-1 python examples/audit_continuation.py --ceiling --sample 400
    docker exec deploy-api-1 python examples/audit_continuation.py --serve --email me@example.com
    python examples/audit_continuation.py --db sqlite:///... --user 3 --openness 0

``--serve`` probes the RUNNING server's ``GET /api/me/continuation`` instead of resolving offline:
it warms the story index (a restarted server's is cold, and the continuation endpoint never builds
one inline), then asks the endpoint about each of one reader's stored reads and reports the offers,
the payload shape, and the serving process's own index metrics. That is the end-to-end check —
auth, flag, response contract — which the offline audit cannot make.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                     # import sibling modules (story_continuation, store, …)
sys.path.insert(0, os.path.dirname(_HERE))    # repo root, so `import rwe` works from a bare checkout

import coverage_comparison                    # noqa: E402
import evidence_resolver as er                # noqa: E402
import publisher_identity                     # noqa: E402
import story_continuation as sc               # noqa: E402
import store as store_mod                     # noqa: E402

#: Gates in the resolver's own evaluation order. Attribution reports the first one that fails, so
#: the counts partition the population exactly once.
#:
#: ``anchor_aged_out`` and ``not_clustered`` are one gate in the resolver (the index lookup) and two
#: buckets here, because they mean opposite things. An article that has left the catalog entirely
#: tells us NOTHING about live behaviour — it is an artifact of measuring reads from weeks ago
#: against today's index, and at prefetch time the reader has just clicked something that is in the
#: catalog by construction. An article still in the catalog but in no cluster is a real structural
#: limit. Reporting them as one number was the first draft's mistake and made the headline
#: uninterpretable.
GATES = ("anchor_aged_out", "not_clustered", "index_inconsistent", "cluster_untrusted",
         "template_genre", "anchor_unrated", "no_unread_other_outlet", "no_rated_sibling",
         "no_opposing_sibling", "stale_read", "ELIGIBLE")

#: ``stale_read`` is evaluated LAST, so it means "cleared every structural gate, failed only on
#: age". Every stored read older than the window fails it by construction, which makes the raw
#: eligible rate over historical reads ~0 no matter how good the feature is. The predictive number
#: is therefore ELIGIBLE + stale_read: what the resolver would have said at click time, when the
#: read age is zero by definition.
_AT_CLICK_TIME = ("ELIGIBLE", "stale_read")

#: The route whose handler warms the story index: ``_attach_explanations`` calls
#: ``evidence_resolver.story_index`` on every served feed. It is ``/api/recommendations`` — the
#: per-reader ``/api/me/recommendations`` does not exist, and a probe that guesses the path spends
#: two minutes retrying a 404 and then reports "no offers", which is a conclusion about nothing.
#: tests/test_audit_continuation.py pins this against the app's real route table.
WARM_PATH = "/api/recommendations"

#: Buckets that are artifacts of the measurement rather than facts about the feature.
_ARTIFACT = ("anchor_aged_out",)


def _attribute(st, user_id, url: str, index: dict, now=None) -> str:
    """The first gate that stops this anchor, or ``ELIGIBLE``.

    Deliberately re-walks the gates rather than parsing a reason out of ``resolve`` — ``resolve``
    returns a bare ``None`` because a caller must never branch on WHY, and giving it a reason
    string purely for this script would put audit vocabulary into the request path."""
    anchor_url = er._canon(str(url or ""))
    story = index.get(anchor_url)
    if not story:
        try:
            in_catalog = st.get_feed_article(anchor_url) is not None
        except Exception:
            in_catalog = True                # unknown -> the conservative (non-artifact) bucket
        return "not_clustered" if in_catalog else "anchor_aged_out"
    if str(story.get("clusterTrust") or "") != "ok":
        return "cluster_untrusted"
    members = story.get("coverage") or []
    if coverage_comparison._is_template_cluster(members):
        return "template_genre"
    anchor = next((m for m in members if er._canon(str(m.get("url") or "")) == anchor_url), None)
    if anchor is None:                       # the index says this url is a member, coverage disagrees
        return "index_inconsistent"
    if sc._lean_of(anchor) is None:
        return "anchor_unrated"

    read_at, _by_pub, _total = ((sc._reader_state(st, user_id)) if user_id is not None
                               else ({}, {}, 0))
    read_urls = set(read_at)

    # Split gate 4/5/6 apart, which _candidates fuses — the difference between "no other outlet
    # covered this" and "another outlet did, but nobody has rated it" is the whole decision about
    # where to invest next. Outlet identity via the same collapser the resolver uses, so a
    # syndicated reprint is not miscounted here as a second outlet.
    try:
        ident = publisher_identity.groups({str(m.get("publisher") or "")
                                           for m in members if m.get("publisher")})
    except Exception:
        ident = {}

    def pub_key(name) -> str:
        raw = str(name or "").strip()
        return ident.get(raw) or raw.lower()

    anchor_key = pub_key(anchor.get("publisher"))
    others = [m for m in members
              if sc._abs_url(m.get("url"))
              and er._canon(sc._abs_url(m.get("url"))) not in read_urls | {anchor_url}
              and pub_key(m.get("publisher")) != anchor_key]
    if not others:
        return "no_unread_other_outlet"
    rated = [m for m in others if sc._lean_of(m) is not None]
    if not rated:
        return "no_rated_sibling"
    if not any(er.opposing_leans(sc._lean_of(anchor), sc._lean_of(m)) for m in rated):
        return "no_opposing_sibling"

    at = read_at.get(anchor_url)
    if at is not None:
        from datetime import datetime, timezone
        ref = now or datetime.now(timezone.utc)
        if (ref - at).total_seconds() / 3600.0 > sc.freshness_hours():
            return "stale_read"
    return "ELIGIBLE"


def _reader_ids(st) -> list:
    from sqlalchemy import select
    with st.session() as s:
        return sorted({int(u) for u in s.scalars(select(store_mod.Read.user_id).distinct()).all()})


# ---------------------------------------------------------------- --serve (the live endpoint)
def _http(base: str, path: str, hdr: dict, timeout: float = 90.0) -> tuple:
    """``(status, body)``; status 0 with the exception name when the call could not be made."""
    import urllib.error
    import urllib.request
    try:
        r = urllib.request.urlopen(urllib.request.Request(base + path, headers=hdr), timeout=timeout)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]
    except Exception as e:                       # connection refused, timeout, DNS — all reportable
        return 0, f"{type(e).__name__}: {e}"


def serve_and_probe(st, base: str, uid: int, *, warm_tries: int = 6, samples: int = 3) -> int:
    """Probe the RUNNING server's ``GET /api/me/continuation`` for one reader's stored reads.

    The offline audit above proves the resolver's answer. This proves the *endpoint* — auth, the
    flag, the response shape, and the story index as the SERVING process sees it, which is a
    different object from the one this script builds (the index cache is a per-process global).

    Ordering matters and is the reason this is scripted rather than typed: a freshly restarted
    server has a COLD index, ``/api/me/continuation`` never builds one inline, and the
    recommendations path that does warm it kicks a BACKGROUND build on the first miss. So the warm
    loop retries until ``rec_story_index_hit_total`` moves before any probing begins — otherwise
    every answer is a null that means "cold cache", not "no offer"."""
    import json
    hdr = {"X-IH-User-Id": str(uid)}
    secret = os.environ.get("RWE_INTERNAL_SECRET")
    if secret:
        hdr["x-ih-auth"] = secret                # the header is only honoured by a trusted caller

    def metrics() -> dict:
        code, body = _http(base, "/api/metrics", hdr, timeout=20)
        try:
            return json.loads(body) if code == 200 else {}
        except ValueError:
            return {}

    print(f"\nSERVE   {base}   uid={uid}   secret={'yes' if secret else 'no'}")
    print(f"        flag RWE_STORY_CONTINUATION="
          f"{os.environ.get('RWE_STORY_CONTINUATION') or '(unset -> off)'}   "
          f"window={sc.freshness_hours()}h")

    import time as _t
    for attempt in range(warm_tries):
        code, _body = _http(base, WARM_PATH, hdr)
        c = metrics().get("counters", {})
        hits, miss = c.get("rec_story_index_hit_total", 0), c.get("rec_story_index_miss_total", 0)
        print(f"        warm {attempt + 1}/{warm_tries}: {WARM_PATH} HTTP {code}  "
              f"index hits={hits} misses={miss}")
        if hits:
            break
        if code in (0, 401, 404):     # unreachable, unauthorized, or wrong path — waiting won't fix
            print(f"        aborting the warm loop: {WARM_PATH} answered {code}, "
                  f"which no amount of waiting changes")
            return 2
        _t.sleep(20)

    reads = st.list_reads(uid)
    print(f"\n        probing {len(reads):,} stored reads ...")
    offers = nulls = errors = shown = 0
    for r in reads:
        url = str(r.get("canonicalUrl") or "")
        code, body = _http(base, "/api/me/continuation?url=" + _quote(url), hdr, timeout=30)
        if code != 200:
            errors += 1
            if errors <= 2:
                print(f"        HTTP {code}: {body}")
            continue
        if body.strip() == "null":
            nulls += 1
            continue
        offers += 1
        if shown < samples:
            shown += 1
            o = json.loads(body)
            a, sib = o["anchor"], o["sibling"]
            print(f"\n        OFFER {a['publisher']} ({a['lean']}, {a['leanBucket']}) -> "
                  f"{sib['publisher']} ({sib['lean']}, {sib['leanBucket']})")
            print(f"              distance={o['distance']} candidates={o['candidateCount']} "
                  f"outlets={o['outlets']}")
            print(f"              story: {str(o['storyTitle'] or '')[:66]}")
            print(f"              keys:  {sorted(o)}")

    print(f"\n        RESULT offers={offers} null={nulls} errors={errors} of {len(reads):,} reads")
    m = metrics()
    print("\n        story-index metrics (the SERVING process, since its restart):")
    for grp in ("counters", "timers"):
        for k, v in sorted((k, v) for k, v in m.get(grp, {}).items() if "story_index" in k):
            print(f"          {k:32} {v}")
    return 0 if errors == 0 else 1


def _quote(url: str) -> str:
    from urllib.parse import quote
    return quote(url, safe="")


def _resolve_reader(st, email: "str | None", user: "int | None") -> "int | None":
    """``--user`` wins; else the account for ``--email``. Prints what the store actually holds when
    the lookup fails — a probe that says "no such user" without saying which DB it opened sends the
    operator hunting for the wrong bug."""
    from sqlalchemy import select
    if user is not None:
        return user
    with st.session() as s:
        if email:
            row = s.scalar(select(store_mod.User).where(store_mod.User.email == email))
            if row is not None:
                return row.id
            print(f"no account for {email!r}")
        known = [(u.id, u.email) for u in s.scalars(select(store_mod.User)).all()]
    print(f"accounts in this store: {known[:10] or 'NONE'}")
    return known[0][0] if (not email and known) else None


def _run(st, index: dict, anchors: list, openness: int, samples: int) -> tuple:
    """``(counter, drift, examples)`` over ``[(user_id, url), …]``."""
    counter: Counter = Counter()
    drift, examples = [], []
    for uid, url in anchors:
        why = _attribute(st, uid, url, index)
        counter[why] += 1
        offer = sc.resolve(st, uid, url, openness=openness, index=index) if uid is not None else None
        if uid is not None and (offer is not None) != (why == "ELIGIBLE"):
            drift.append((uid, url[-12:], why, offer is not None))
        if offer is not None and len(examples) < samples:
            examples.append((uid, offer))
    return counter, drift, examples


def _pct(n: int, total: int) -> str:
    return f"{(100.0 * n / total):5.1f}%" if total else "    - "


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("RWE_DB_URL") or os.environ.get("DATABASE_URL"),
                    help="store URL (default: the server's own RWE_DB_URL / DATABASE_URL)")
    ap.add_argument("--user", type=int, default=None, help="one reader (default: every reader)")
    ap.add_argument("--openness", type=int, default=50, help="slider 0-100 (default 50)")
    ap.add_argument("--ceiling", action="store_true",
                    help="structural ceiling over cluster members, ignoring reader state")
    ap.add_argument("--sample", type=int, default=500,
                    help="max hypothetical anchors in --ceiling mode (default 500)")
    ap.add_argument("--examples", type=int, default=5, help="offers to print (default 5)")
    ap.add_argument("--inline", action="store_true",
                    help="build the story index inline if the warm cache misses (COSTS CPU)")
    ap.add_argument("--serve", action="store_true",
                    help="probe the RUNNING server's GET /api/me/continuation instead of resolving "
                         "offline (warms the index first, then reports offers + index metrics)")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="--serve: the engine's base URL")
    ap.add_argument("--email", default=None, help="--serve: the reader, by account email")
    args = ap.parse_args()

    st = store_mod.Store(args.db) if args.db else store_mod.Store()
    # Which database this process actually opened. Printed ALWAYS: a probe that reports "no such
    # user" without naming the store it looked in sends the operator hunting for the wrong bug.
    try:
        print(f"store          {st.engine.url}")
    except Exception:
        print(f"store          {args.db or os.environ.get('RWE_DB_URL') or '(default)'}")

    if args.serve:
        uid = _resolve_reader(st, args.email, args.user)
        return 2 if uid is None else serve_and_probe(st, args.base.rstrip("/"), uid,
                                                     samples=args.examples)
    if args.inline:
        print("! --inline: a full read-only clustering runs on THIS process. On a small host that "
              "is real CPU for tens of seconds.\n")
    index = er.story_index(st, build_inline=args.inline)
    if not index:
        print("story index is EMPTY — the poller has not warmed a story view yet (or the catalog "
              "has no clusters).\nNothing can be audited; re-run once the poller has built, or "
              "pass --inline to build one here.")
        return 2

    stories = {v["storyId"] for v in index.values()}
    print(f"catalog        {st.count_feed_articles():>7,} feed articles")
    print(f"story index    {len(index):>7,} member urls across {len(stories):,} stories")
    print(f"freshness      {sc.freshness_hours():>7.1f} h        openness slider {args.openness} "
          f"-> {('nearest', 'novelty-first', 'furthest')[sc.distance_preference(args.openness) + 1]}"
          f"\nflag           RWE_STORY_CONTINUATION={'on' if sc.enabled() else 'OFF (resolver is dark)'}")

    if args.ceiling:
        # STRIDE, never the head. The index is built in story_service's own ranked order
        # (`_size_rank`, reverse) — trusted first, then publisherCount, then totalCoverage — so
        # taking the first N urls samples only the biggest trusted stories and reports a ceiling
        # that no reader experiences. The first draft did exactly that and returned zero
        # `cluster_untrusted` over 800 anchors, which is not a fact about the catalog.
        # Members are NOT deduplicated per story on purpose: a 40-member story really is 20x more
        # likely to be the one a reader clicked than a 2-member one, so member-weighting is the
        # distribution the realized rate is drawn from.
        urls = list(index)
        step = max(1, len(urls) // max(1, args.sample))
        anchors = [(None, u) for u in urls[::step]][:args.sample]
        label = (f"CEILING — {len(anchors):,} hypothetical anchors (every {step:,}th member "
                 f"across all {len(stories):,} stories), no reader state")
    else:
        readers = [args.user] if args.user is not None else _reader_ids(st)
        anchors = [(uid, str(r.get("canonicalUrl") or ""))
                   for uid in readers for r in st.list_reads(uid)]
        label = f"REALIZED — {len(anchors):,} stored reads across {len(readers):,} readers"

    if not anchors:
        print("\nno anchors to audit (no stored reads).")
        return 2

    counter, drift, examples = _run(st, index, anchors, args.openness, args.examples)
    total = sum(counter.values())

    print(f"\n{label}\n" + "-" * 68)
    for gate in GATES:
        n = counter.get(gate, 0)
        if n or gate == "ELIGIBLE":
            note = "  <- measurement artifact" if gate in _ARTIFACT else ""
            print(f"  {gate:<24} {n:>7,}  {_pct(n, total)}{note}")
    print("-" * 68)

    elig = counter.get("ELIGIBLE", 0)
    at_click = sum(counter.get(g, 0) for g in _AT_CLICK_TIME)
    artifact = sum(counter.get(g, 0) for g in _ARTIFACT)
    live = total - artifact

    print(f"  {'eligible NOW':<24} {elig:>7,}  {_pct(elig, total)} of {total:,}")
    if not args.ceiling:
        # The number that predicts live behaviour. Historical reads fail the freshness gate by
        # construction, so `eligible NOW` over a backlog is ~0 however good the feature is.
        print(f"  {'eligible AT CLICK TIME':<24} {at_click:>7,}  {_pct(at_click, total)} of "
              f"{total:,}   <- predicts the live rate")
        if artifact:
            print(f"  {'  … of reads still live':<24} {at_click:>7,}  {_pct(at_click, live)} of "
                  f"{live:,} (excludes {artifact:,} aged out of the catalog)")
        clustered = total - sum(counter.get(g, 0) for g in
                                ("anchor_aged_out", "not_clustered", "index_inconsistent"))
        if clustered:
            print(f"  {'  … of reads in a cluster':<24} {at_click:>7,}  {_pct(at_click, clustered)}"
                  f" of {clustered:,}   <- conversion once clustering succeeds")
    else:
        print("  (ceiling ignores the unread + freshness gates — the realized rate is lower)")

    if drift:
        print(f"\n!! {len(drift)} anchor(s) where this audit and story_continuation.resolve "
              f"DISAGREE — the audit has drifted from the module and its numbers are not "
              f"trustworthy:\n   {drift[:5]}")

    if examples:
        print(f"\nsample offers (openness {args.openness}):")
        for uid, o in examples:
            a, s = o["anchor"], o["sibling"]
            print(f"  user {uid}: {a['publisher']} ({a['lean']}) -> {s['publisher']} ({s['lean']})"
                  f"  d={o['distance']}  of {o['candidateCount']} candidate(s), "
                  f"{o['outlets']} outlets\n      {str(o['storyTitle'] or '')[:70]}")
    return 0 if not drift else 1


if __name__ == "__main__":
    raise SystemExit(main())
