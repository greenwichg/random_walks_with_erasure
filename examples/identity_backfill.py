#!/usr/bin/env python3
"""identity_backfill.py — give every existing catalogue row its durable identity.

New rows get ``article_id`` / ``publisher_id`` / ``licence_class`` / ``scorer_version``, their
aliases and their first provenance row at ingest (``store.upsert_feed_article``), and a legacy row
still listed by a feed heals on its next re-poll. This fills everything else, from what each row
already carries: the same deterministic ids ingest would mint, the licence class its recorded
channel establishes, one provenance row spanning ``created_at``..``fetched_at``, and the
``publishers`` table from the registry.

    python examples/identity_backfill.py --db "$RWE_DB_URL" --dry-run     # counts only, writes nothing
    python examples/identity_backfill.py --db "$RWE_DB_URL"               # batched, resumable, idempotent;
                                                                          # passes until nothing is missing
                                                                          # (exit 1 if rows still are)

Idempotent: a filled row is left exactly as it is (an id is never rewritten), an existing
provenance row is not re-counted. Batched (``--batch``, default 1000) so the write lock is held
briefly; safe to run beside the live poller. On the production host:

    cd /opt/ih && sudo docker exec -i deploy-api-1 python examples/identity_backfill.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.exc import OperationalError  # noqa: E402

import identity  # noqa: E402
import licence  # noqa: E402
import store  # noqa: E402


#: Attempts per batch before it is recorded as failed and the run moves on. A batch competes with
#: the live poller for SQLite's single write lock; ``busy_timeout`` (5 s) already absorbs the
#: ordinary case, so a raised ``OperationalError`` means the contention outlasted it.
RETRIES = 3
#: Batches between progress lines. The production catalogue is 151 batches — a line each buried
#: the run's own summary in the operator's terminal.
LOG_EVERY = 10


def _write_batch(st, prepared: list, *, retries: int = RETRIES, log=print) -> "tuple[int, bool]":
    """One batch in one transaction, retried on lock contention. ``(changed, ok)``; a batch that
    never lands is REPORTED and skipped, never fatal — the run is resumable and the next pass
    picks the rows up. (The first production run died on a single row's lock error at ~37k of
    150k rows, and the operator's script had sent the traceback to /dev/null.)"""
    for attempt in range(1, max(1, retries) + 1):
        try:
            return st.apply_identity_backfill_batch(prepared), True
        except OperationalError as exc:
            if attempt >= retries:
                log(json.dumps({"event": "identity_backfill_batch_failed", "rows": len(prepared),
                                "error": f"{type(exc).__name__}: {str(exc)[:160]}"}))
                return 0, False
            time.sleep(0.5 * attempt)
    return 0, False


def run(st, *, batch: int = 1000, dry_run: bool = False, publishers: bool = True,
        log=print) -> dict:
    t0 = time.perf_counter()
    stats = {"rows": 0, "missingArticleId": 0, "missingPublisherId": 0, "missingLicence": 0,
             "changed": 0, "batches": 0, "failedBatches": 0, "dryRun": bool(dry_run)}
    after = None
    while True:
        rows = st.identity_backfill_rows(limit=batch, after=after)
        if not rows:
            break
        stats["batches"] += 1
        prepared = []
        for r in rows:
            stats["rows"] += 1
            stats["missingArticleId"] += int(not r["articleId"])
            stats["missingPublisherId"] += int(not r["publisherId"])
            stats["missingLicence"] += int(not r["licenceClass"])
            if dry_run:
                continue
            channel = (r["sourceType"] or "").strip().lower() or None
            prepared.append({
                "canonical_url": r["canonicalUrl"],
                "article_id": identity.article_id_for(r["canonicalUrl"]),
                "publisher_id": identity.publisher_id_for(r["publisher"]),
                "publisher_key": identity.publisher_identity_key(r["publisher"]),
                "licence_class": licence.class_for_channel(channel),
                "channel": channel, "provider": r["sourceProvider"],
                "source_ref": r["sourceFeed"] or "", "external_id": r["externalId"],
                "published_at": r["publishedAt"], "first_observed_at": r["createdAt"],
                "last_observed_at": r["fetchedAt"], "url": r["url"]})
        if prepared:
            changed, ok = _write_batch(st, prepared, log=log)
            stats["changed"] += changed
            stats["failedBatches"] += int(not ok)
        after = rows[-1]["canonicalUrl"]
        if stats["batches"] % LOG_EVERY == 0:
            log(json.dumps({"event": "identity_backfill_batch", "batches": stats["batches"],
                            "rows": stats["rows"], "changed": stats["changed"],
                            "failedBatches": stats["failedBatches"]}))
    if publishers and not dry_run:
        stats["publishers"] = identity.sync_publishers(st)
    stats["ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
    return stats


def run_until_complete(st, *, batch: int = 1000, passes: int = 5, publishers: bool = True,
                       log=print) -> dict:
    """Passes until every batch landed (or a pass stops making progress), then ONE counting pass
    for the truth after the writes. One writing pass suffices when no batch fails; this is the
    operator-facing guarantee that a contended run still finishes. The summary carries the
    total ``changed`` and ``failedBatches`` across passes and the post-write ``missing*``
    counts — the numbers the exit code and the enable script read."""
    t0 = time.perf_counter()
    changed = failed = 0
    n = 0
    for n in range(1, max(1, passes) + 1):
        stats = run(st, batch=batch, publishers=publishers and n == 1, log=log)
        changed += stats["changed"]
        failed += stats["failedBatches"]
        log(json.dumps({"event": "identity_backfill_pass", "pass": n, "changed": stats["changed"],
                        "failedBatches": stats["failedBatches"]}))
        if stats["failedBatches"] == 0 or stats["changed"] == 0:
            break                            # everything read was written, or nothing moves
    final = run(st, batch=batch, dry_run=True, publishers=False, log=lambda s: None)
    final.update(changed=changed, failedBatches=failed, passes=n, dryRun=False,
                 ms=round((time.perf_counter() - t0) * 1000.0, 1))
    return final


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="SQLAlchemy URL (default: RWE_DB_URL)")
    ap.add_argument("--batch", type=int, default=1000)
    ap.add_argument("--dry-run", action="store_true", help="count what would change; write nothing")
    ap.add_argument("--no-publishers", action="store_true", help="skip the publishers table sync")
    ap.add_argument("--passes", type=int, default=5,
                    help="re-run until nothing is missing, at most this many passes (1 = one pass)")
    args = ap.parse_args(argv)
    st = store.Store(args.db or store.default_db_url())
    if args.dry_run:
        stats = run(st, batch=max(1, args.batch), dry_run=True, publishers=False)
    else:
        stats = run_until_complete(st, batch=max(1, args.batch), passes=max(1, args.passes),
                                   publishers=not args.no_publishers)
    # ONE line, last: the operator's script reads it with `tail -1`.
    print(json.dumps(stats, sort_keys=True))
    missing = stats["missingArticleId"] + stats["missingPublisherId"] + stats["missingLicence"]
    return 0 if (args.dry_run or missing == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
