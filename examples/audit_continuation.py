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


def suggest(st, index: dict, uid: int, limit: int) -> int:
    """Articles this reader has NOT read that would produce a strip if they opened one now.

    The question every by-hand test actually has, and the one nothing else answered. The realized
    audit reports on reads that already happened; ``--ceiling`` ignores reader state entirely. Here
    the freshness gate passes for free — there is no stored read yet — so ``_attribute`` returning
    ELIGIBLE on an UNREAD member means exactly "open this and the strip appears"."""
    read_at, _by_pub, _total = sc._reader_state(st, uid)
    read_urls = set(read_at)
    hits = 0
    print(f"\nOPEN ONE OF THESE — unread, and every gate passes the moment you do "
          f"(reader {uid}):")
    for url, story in index.items():
        if hits >= limit:
            break
        if url in read_urls:
            continue
        if _attribute(st, uid, url, index) != "ELIGIBLE":
            continue
        member = next((m for m in (story.get("coverage") or [])
                       if er._canon(str(m.get("url") or "")) == url), {})
        print(f"  {str(member.get('publisher') or '')} — {str(member.get('headline') or '')[:64]}")
        print(f"    {member.get('url')}")
        hits += 1
    if not hits:
        print("  none — no unread cluster member currently clears every gate.")
    return 0


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
            # The anchor's own headline and URL, because the reason to run this is usually "which
            # article do I open to see the strip?" — and a story title is not something you can
            # search Discover for. Printed from the READ row, which is what the reader clicked.
            print(f"              open:  {str((r.get('scored') or {}).get('title') or '')[:66]}")
            print(f"                     {a['url']}")

    print(f"\n        RESULT offers={offers} null={nulls} errors={errors} of {len(reads):,} reads")
    m = metrics()
    print("\n        story-index metrics (the SERVING process, since its restart):")
    for grp in ("counters", "timers"):
        for k, v in sorted((k, v) for k, v in m.get(grp, {}).items() if "story_index" in k):
            print(f"          {k:32} {v}")
    return 0 if errors == 0 else 1


def report_counters(base: str) -> int:
    """What the LIVE endpoint answered real browsers, since the serving process last restarted.

    This is the one probe that needs no store, no index and no reader, and it is the one to run
    after a hand test: ``continuation_result_total`` splits "the strip did not appear" into a
    server-side failure and a browser-side one, which no other signal here can do.

    It exists as a mode rather than a shell one-liner because the obvious one-liner does not work on
    the production host: the api image has no ``curl``, and piping to ``python`` runs on the HOST,
    which has ``python3``. Both failures look like the metrics endpoint being broken.
    """
    import json
    hdr = {}
    secret = os.environ.get("RWE_INTERNAL_SECRET")
    if secret:
        hdr["x-ih-auth"] = secret
    code, body = _http(base, "/api/metrics", hdr, timeout=20)
    if code != 200:
        print(f"\n{base}/api/metrics answered HTTP {code}\n{body[:300]}")
        return 2
    try:
        counters = (json.loads(body) or {}).get("counters", {})
    except ValueError:
        print(f"\n/api/metrics returned a non-JSON body: {body[:200]}")
        return 2

    print(f"\nCOUNTERS  {base}   (reset on every restart — deploy, then test, then read)")
    print(f"          flag RWE_STORY_CONTINUATION="
          f"{os.environ.get('RWE_STORY_CONTINUATION') or '(unset -> off)'} in THIS process")

    outcomes = {k.split("|", 1)[1]: v for k, v in counters.items()
                if k.startswith("continuation_result_total|")}
    total = sum(outcomes.values())
    print(f"\n          GET /api/me/continuation answered {total:,} time(s):")
    if not total:
        print("            nothing yet — no browser has hit the endpoint since the restart.")
    for name, meaning in (("offer", "a payload went to the browser -> any loss left is CLIENT-side"),
                          ("null", "resolver declined -> run --inline for the gate"),
                          ("disabled", "RWE_STORY_CONTINUATION is not on in the container"),
                          ("error", "resolver raised -> continuation_resolve_failed in the api log")):
        n = int(outcomes.get(name, 0))
        if n or name in ("offer", "null"):
            print(f"            {name:<9} {n:>6,}   {meaning}")

    # The index is the endpoint's only dependency it cannot build itself, so a cold one explains a
    # run of nulls that has nothing to do with the reader or the gates.
    print("\n          story index in the serving process:")
    for k in ("rec_story_index_hit_total", "rec_story_index_miss_total"):
        print(f"            {k:<28} {int(counters.get(k, 0)):>6,}")
    if not counters.get("rec_story_index_hit_total"):
        if total:
            print("            COLD — every answer above is a null meaning 'no index', not "
                  "'no offer'.")
        else:
            # Nothing has been answered yet, so there are no answers to explain away. Saying it
            # anyway would be a confident claim about an empty set, which is how a probe misleads.
            print("            COLD — warm it by loading Recommendations, wait for hits above 0, "
                  "and only THEN read an article. A read taken now answers null for want of an "
                  "index.")
    return 0


_FUNNEL = ("continuation_eligible", "continuation_armed", "continuation_shown",
           "continuation_opened")


def report_events(st, uid: "int | None") -> int:
    """The CLIENT-side funnel, from the analytics events the browser sent.

    ``--counters`` ends where the payload leaves the engine. Everything after that — storage,
    mounting, the dwell gate, the dismissal and impression caps — happens in a browser nobody can
    attach a debugger to, and these four events are the only witnesses to it:

        eligible  the engine said yes            (equals `offer` in --counters)
        armed     sessionStorage accepted it     (a gap here is quota / private mode)
        shown     it rendered on a return        (a gap here is the dwell gate, an unmounted card,
                                                  a previous dismissal, or the impression cap)
        opened    the reader took it

    Read-only. Reads the store directly rather than the internal HTTP route, so it works from a
    shell without the internal secret.
    """
    try:
        rows = st.list_analytics_events()
    except Exception as exc:
        print(f"\ncould not read analytics events: {type(exc).__name__}: {exc}")
        return 2

    rows = [r for r in rows if str(r.get("event") or "").startswith("continuation_")]
    if uid is not None:
        rows = [r for r in rows if r.get("userId") == uid]

    print(f"\nCLIENT EVENTS  {len(rows):,} continuation event(s)"
          f"{'' if uid is None else f' for reader {uid}'}")
    if not rows:
        print("  none recorded — which is the EXPECTED result unless --counters shows offer > 0.\n"
              "  The first event fires only when a payload reaches the browser, so an engine that\n"
              "  declined every read leaves nothing here to find. Check --counters before reading\n"
              "  anything into this.\n"
              "  Two other ways to be empty: an older deploy (before 2026-08-05 the sink's\n"
              "  allow-list dropped all six, so silence says nothing about the browser), or\n"
              "  /api/events being unreachable from the web tier.")
        return 0

    counts: dict = {}
    for r in rows:
        counts[r.get("event")] = counts.get(r.get("event"), 0) + 1
    prev = None
    for name in _FUNNEL:
        n = counts.get(name, 0)
        drop = "" if prev is None or prev == 0 else f"   ({n}/{prev} of the stage above)"
        print(f"  {name:<26} {n:>6,}{drop}")
        prev = n
    for name in ("continuation_suppressed", "continuation_dismissed", "continuation_all_outlets"):
        print(f"  {name:<26} {counts.get(name, 0):>6,}")

    # WHY a qualifying return rendered nothing. `capped` and `dismissed` are localStorage state that
    # OUTLIVES the session and accumulated while these events were being dropped, so a story can sit
    # at the cap with no record of ever having been shown.
    reasons: dict = {}
    hidden_arms = 0
    for r in rows:
        props = r.get("props") or {}
        if r.get("event") == "continuation_suppressed":
            why = props.get("reason") or "unknown"
            reasons[why] = reasons.get(why, 0) + 1
        elif r.get("event") == "continuation_armed" and props.get("hidden"):
            hidden_arms += 1
    if reasons:
        print("\n  suppressed because:")
        for why, n in sorted(reasons.items()):
            print(f"    {why:<10} {n:>6,}")

    armed_n = counts.get("continuation_armed", 0)
    if armed_n:
        print(f"\n  armed while the tab was ALREADY hidden: {hidden_arms:,} of {armed_n:,}")
        if hidden_arms and not counts.get("continuation_shown"):
            print("    The card enables its visibility listener in a backgrounded tab. If the "
                  "browser\n    defers that work past the reader's return, the hide is never "
                  "observed and the\n    return is ignored — which looks exactly like this.")

    # The stage that actually failed, named — the reason to run this at all.
    if counts.get("continuation_eligible") and not counts.get("continuation_armed"):
        print("\n  LOST AT ARMING — the offer arrived and sessionStorage refused it "
              "(private mode, full quota, or storage disabled).")
    elif counts.get("continuation_armed") and not counts.get("continuation_shown"):
        print("\n  LOST BEFORE RENDER — the offer armed and never displayed. In order of "
              "likelihood:\n"
              "    * the return was under the 20 s dwell gate;\n"
              "    * the reader came back to a DIFFERENT page, so no card was mounted for it;\n"
              "    * this story was dismissed before, or has already had its 2 impressions\n"
              "      (localStorage `hv.continue` — per story, and it outlives the session).")

    # `surface` is the measurement design §9.1.1 says would overturn the primary-surface decision.
    by_surface: dict = {}
    for r in rows:
        if r.get("event") != "continuation_shown":
            continue
        s = (r.get("props") or {}).get("surface") or "unknown"
        by_surface[s] = by_surface.get(s, 0) + 1
    if by_surface:
        print("\n  shown by surface:")
        for s, n in sorted(by_surface.items()):
            print(f"    {s:<10} {n:>6,}")
    return 0


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
    """``(counter, drift, examples, testable)`` over ``[(user_id, url), …]``.

    ``testable`` lists anchors in the ``stale_read`` bucket — cleared every structural gate and
    failed only on age. They are a POST-MORTEM, not a to-do list: ``store.add_read`` is idempotent
    per (user, url) and never refreshes the timestamp, so re-reading one of these leaves it exactly
    as stale as it was. Use ``--suggest`` for articles that would actually fire."""
    counter: Counter = Counter()
    drift, examples, testable = [], [], []
    for uid, url in anchors:
        why = _attribute(st, uid, url, index)
        counter[why] += 1
        offer = sc.resolve(st, uid, url, openness=openness, index=index) if uid is not None else None
        if uid is not None and (offer is not None) != (why == "ELIGIBLE"):
            drift.append((uid, url[-12:], why, offer is not None))
        if offer is not None and len(examples) < samples:
            examples.append((uid, offer))
        if why == "stale_read" and len(testable) < samples:
            story = (index or {}).get(er._canon(url)) or {}
            member = next((m for m in (story.get("coverage") or [])
                           if er._canon(str(m.get("url") or "")) == er._canon(url)), {})
            testable.append((str(member.get("headline") or ""), url))
    return counter, drift, examples, testable


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
    ap.add_argument("--email", default=None, help="--serve / --suggest: the reader, by email")
    ap.add_argument("--suggest", action="store_true",
                    help="list UNREAD articles that would produce a strip if opened now")
    ap.add_argument("--counters", action="store_true",
                    help="what the LIVE endpoint answered browsers since the last restart "
                         "(offer / null / disabled / error) — no store, no index, instant")
    ap.add_argument("--events", action="store_true",
                    help="the CLIENT-side funnel from stored analytics events "
                         "(eligible -> armed -> shown -> opened) — where --counters stops")
    args = ap.parse_args()

    # Before the store: this mode asks the SERVER what it answered, and a store that will not open
    # is not a reason to withhold the one number that says which side the failure is on.
    if args.counters:
        return report_counters(args.base.rstrip("/"))

    st = store_mod.Store(args.db) if args.db else store_mod.Store()
    # Which database this process actually opened. Printed ALWAYS: a probe that reports "no such
    # user" without naming the store it looked in sends the operator hunting for the wrong bug.
    try:
        print(f"store          {st.engine.url}")
    except Exception:
        print(f"store          {args.db or os.environ.get('RWE_DB_URL') or '(default)'}")

    if args.events:
        # An unresolvable reader is not fatal here: the funnel over EVERY reader still names the
        # stage that failed, and refusing to print it would withhold the answer over a detail.
        uid = _resolve_reader(st, args.email, args.user) if (args.email or args.user) else None
        return report_events(st, uid)

    if args.suggest:
        uid = _resolve_reader(st, args.email, args.user)
        if uid is None:
            return 2
        idx = er.story_index(st, build_inline=args.inline)
        if not idx:
            print("story index is EMPTY — pass --inline, or wait for the poller to warm it.")
            return 2
        return suggest(st, idx, uid, args.examples)

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

    counter, drift, examples, testable = _run(st, index, anchors, args.openness,
                                                 args.examples)
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

    if testable:
        print("\nthese cleared every gate but age — the offer was real and the moment passed.\n"
              "Re-reading them does NOT help: add_read is idempotent and keeps the original\n"
              "timestamp, so they stay stale forever. Use --suggest for what to open now:")
        for headline, url in testable:
            print(f"  {headline[:72]}\n    {url}")

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
