#!/usr/bin/env python3
"""identity_backfill.py — give every existing catalogue row its durable identity.

New rows get ``article_id`` / ``publisher_id`` / ``licence_class`` / ``scorer_version``, their
aliases and their first provenance row at ingest (``store.upsert_feed_article``), and a legacy row
still listed by a feed heals on its next re-poll. This fills everything else, from what each row
already carries: the same deterministic ids ingest would mint, the licence class its recorded
channel establishes, one provenance row spanning ``created_at``..``fetched_at``, and the
``publishers`` table from the registry.

    python examples/identity_backfill.py --db "$RWE_DB_URL" --dry-run     # counts only, writes nothing
    python examples/identity_backfill.py --db "$RWE_DB_URL"               # batched, resumable, idempotent

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

import identity  # noqa: E402
import licence  # noqa: E402
import store  # noqa: E402


def run(st, *, batch: int = 1000, dry_run: bool = False, publishers: bool = True,
        log=print) -> dict:
    t0 = time.perf_counter()
    stats = {"rows": 0, "missingArticleId": 0, "missingPublisherId": 0, "missingLicence": 0,
             "changed": 0, "batches": 0, "dryRun": bool(dry_run)}
    after = None
    while True:
        rows = st.identity_backfill_rows(limit=batch, after=after)
        if not rows:
            break
        stats["batches"] += 1
        for r in rows:
            stats["rows"] += 1
            stats["missingArticleId"] += int(not r["articleId"])
            stats["missingPublisherId"] += int(not r["publisherId"])
            stats["missingLicence"] += int(not r["licenceClass"])
            if dry_run:
                continue
            channel = (r["sourceType"] or "").strip().lower() or None
            changed = st.apply_identity_backfill(
                r["canonicalUrl"],
                article_id=identity.article_id_for(r["canonicalUrl"]),
                publisher_id=identity.publisher_id_for(r["publisher"]),
                publisher_key=identity.publisher_identity_key(r["publisher"]),
                licence_class=licence.class_for_channel(channel),
                channel=channel, provider=r["sourceProvider"], source_ref=r["sourceFeed"] or "",
                external_id=r["externalId"], published_at=r["publishedAt"],
                first_observed_at=r["createdAt"], last_observed_at=r["fetchedAt"], url=r["url"])
            stats["changed"] += int(bool(changed))
        after = rows[-1]["canonicalUrl"]
        log(json.dumps({"event": "identity_backfill_batch", "batches": stats["batches"],
                        "rows": stats["rows"], "changed": stats["changed"]}))
    if publishers and not dry_run:
        stats["publishers"] = identity.sync_publishers(st)
    stats["ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="SQLAlchemy URL (default: RWE_DB_URL)")
    ap.add_argument("--batch", type=int, default=1000)
    ap.add_argument("--dry-run", action="store_true", help="count what would change; write nothing")
    ap.add_argument("--no-publishers", action="store_true", help="skip the publishers table sync")
    args = ap.parse_args(argv)
    st = store.Store(args.db or store.default_db_url())
    stats = run(st, batch=max(1, args.batch), dry_run=args.dry_run,
                publishers=not args.no_publishers)
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
