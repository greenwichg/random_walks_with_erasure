"""storage_lifecycle.py — the single cleanup pass that keeps the database bounded.

One function, :func:`run_cleanup`, applies every policy in :mod:`retention_policy` and returns what
it did. It is called post-cycle by the pollers (beside the existing catalog retention) and is
runnable on its own:

    python examples/storage_lifecycle.py            # one pass, prints JSON
    python examples/storage_lifecycle.py --stats    # report sizes only, prune nothing

Safety properties, which are the whole point:

* **Incremental.** Every prune is capped at ``policy.batch_limit`` rows, so a single pass holds the
  one SQLite write lock for a bounded time and ingestion is never starved. A backlog drains over
  successive cycles instead of in one long stall.
* **Never touches user data.** It calls only the bounded prunes on derived/operational tables;
  :data:`retention_policy.PROTECTED_TABLES` names what is off-limits and a test asserts this module
  never prunes one of them.
* **Ordered so nothing is orphaned.** Catalog retention runs FIRST (it is the thing that creates
  orphans), then the event-location reaper, then the rest. Running it the other way round would
  leave a cycle's worth of orphans behind every time.
* **Fail-soft.** A failure in one table's prune is logged and the pass continues: a cleanup job must
  never take down ingestion, and a partially-completed pass is always safe to repeat.
* **Idempotent.** Re-running when there is nothing to prune deletes nothing.

  It is NOT, however, free — this docstring used to claim it "costs a few indexed COUNTs", and
  production measured the pass at 75-84 s per poll cycle, ~0.8 of a core on a two-core box, dwarfing
  every other thing the process does. A pass that deletes nothing still has to PROVE there is
  nothing to delete, and proving a negative over a growing catalog is not a COUNT. Every step is
  therefore timed and reported in ``storage_cleanup.ms``: an unbounded scan hiding behind a bounded
  DELETE is exactly the shape that claim concealed.
"""
from __future__ import annotations

import json
import logging
import time

import corpus_health
import retention_policy

_logger = logging.getLogger("ih.storage")


def _default_log(level: int, event: str, **fields) -> None:
    _logger.log(level, json.dumps({"event": event, **fields}, default=str))


def run_cleanup(store_, *, policy: "retention_policy.RetentionPolicy | None" = None,
                log=None) -> dict:
    """Apply every retention policy once. Returns ``{table: rows_deleted}`` plus post-pass stats."""
    policy = policy or retention_policy.load()
    log = log or _default_log
    limit = policy.batch_limit
    deleted: dict = {}
    errors: dict = {}
    ms: dict = {}

    def step(name, fn):
        t0 = time.perf_counter()
        try:
            deleted[name] = fn()
        except Exception as e:                       # a cleanup pass must never break the poller
            deleted[name] = 0
            errors[name] = f"{type(e).__name__}: {e}"
        finally:
            ms[name] = round((time.perf_counter() - t0) * 1000.0, 1)

    # 1. Catalog FIRST — it is what produces orphaned side rows. Validation-aware (floors protect
    #    the serving corpus), and delegated to the module that already owns that logic.
    if policy.catalog_enabled():
        step("feed_articles", lambda: corpus_health.run_retention(
            store_, max_age_days=policy.article_max_age_days or None,
            max_count=policy.article_max_count or None, log=log).get("pruned", 0))
    else:
        deleted["feed_articles"] = 0
        ms["feed_articles"] = 0.0                    # disabled, but the contract is every step reports

    # 2. Then the reaper for anything the catalog prune (now or in the past) left behind.
    step("article_event_locations", lambda: store_.prune_orphan_event_locations(limit))
    # 3. Derived / operational tables, each independently bounded.
    step("scored_articles", lambda: store_.prune_scored_cache(policy.scored_cache_days, limit))
    step("analytics_events", lambda: store_.prune_analytics_events(policy.analytics_event_days, limit))
    step("rec_events", lambda: store_.prune_rec_events(policy.rec_event_days, limit))
    step("report_snapshots", lambda: store_.prune_report_snapshots(policy.snapshots_per_user, limit))

    total = sum(deleted.values())
    t0 = time.perf_counter()
    stats = store_.storage_stats()                   # timed too: it is a per-table scan, not free
    ms["storage_stats"] = round((time.perf_counter() - t0) * 1000.0, 1)
    result = {"deleted": deleted, "total": total, "errors": errors, "ms": ms,
              "policy": policy.describe(), "stats": stats}
    log(logging.INFO, "storage_cleanup", total=total, deleted=deleted, ms=ms,
        totalMs=round(sum(ms.values()), 1),
        dbBytes=stats.get("dbBytes"), errors=errors or None)
    return result


def main(argv=None) -> int:
    import argparse
    import store as store_mod
    ap = argparse.ArgumentParser(description="Apply the storage retention policy once.")
    ap.add_argument("--db", default=None, help="RWE_DB_URL override")
    ap.add_argument("--stats", action="store_true", help="report sizes only; prune nothing")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    st = store_mod.Store(args.db)
    if args.stats:
        print(json.dumps({"policy": retention_policy.load().describe(),
                          "stats": st.storage_stats()}, indent=2, default=str))
        return 0
    print(json.dumps(run_cleanup(st), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
