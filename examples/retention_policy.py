"""retention_policy.py — the ONE typed, validated description of how long Hidden View keeps data.

Every retention period in the product is declared here, in one frozen dataclass, loaded from the
environment once and validated. Nothing else reads a ``RWE_RETENTION_*`` variable: modules take a
:class:`RetentionPolicy` (or call :func:`load`) so a period can never be read two different ways in
two different places, and a typo can never silently mean "off".

Design rules, in priority order:

1. **User data is never pruned by a policy.** Reads, saved articles, settings, accounts, tokens,
   feedback, and the improvement audit trail have no retention period and cannot get one here —
   they are the reader's own record. Deleting them is a *user* action (account deletion), not a
   storage decision.
2. **Derived and operational data is prunable**, because it is either regenerable (the score cache),
   observational (analytics events, notifications), or bounded-value (very old catalog articles and
   rec-events).
3. **Zero means keep forever**, uniformly. Every field defaults to a safe value; an unparseable or
   negative value falls back to the default rather than to "delete everything" — the failure mode of
   a bad config must be *keeping too much*, never losing data.
4. **A prune is incremental.** ``batch_limit`` caps rows deleted per table per run so a cleanup pass
   can never hold a long write lock against ingestion (SQLite has one writer).

Env surface (all optional; defaults below are the shipped policy):

    RWE_RETENTION_MAX_AGE_DAYS      catalog articles older than this            (0 = off)
    RWE_RETENTION_MAX_COUNT         keep at most this many catalog articles     (0 = off)
    RWE_RETENTION_SCORED_DAYS       scored-article cache entries                (default 30)
    RWE_RETENTION_ANALYTICS_DAYS    product-analytics events                    (default 180)
    RWE_RETENTION_REC_EVENT_DAYS    recommendation surface/open events          (default 365)
    RWE_RETENTION_SNAPSHOTS_PER_USER  report snapshots kept per user            (default 500)
    RWE_RETENTION_NOTIFICATIONS_PER_USER  notifications kept per user           (default 200)
    RWE_RETENTION_BATCH_LIMIT       max rows deleted per table per run          (default 5000)
"""
from __future__ import annotations

import dataclasses
import os


def _int_env(name: str, default: int, *, minimum: int = 0) -> int:
    """A non-negative int from the environment. Junk, negatives, and blanks fall back to the
    default — a malformed retention setting must never widen what gets deleted."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


@dataclasses.dataclass(frozen=True)
class RetentionPolicy:
    """How long each prunable table keeps data. 0 = keep forever, everywhere."""

    # --- catalog (validation-aware; floors in corpus_health still protect the serving corpus) ---
    article_max_age_days: int = 0
    article_max_count: int = 0
    # --- derived / operational -------------------------------------------------------------- #
    scored_cache_days: int = 30        # pure cache: re-scoring is deterministic and cheap
    analytics_event_days: int = 180    # product funnel needs a window, not history forever
    rec_event_days: int = 365          # Open-Mindedness reads these; a year is generous
    snapshots_per_user: int = 500      # report trend history (analytics charts)
    notifications_per_user: int = 200  # settled inbox history; UNSEEN rows are never pruned
    # --- safety ----------------------------------------------------------------------------- #
    batch_limit: int = 5000            # per table, per run — keeps write locks short

    def catalog_enabled(self) -> bool:
        return bool(self.article_max_age_days or self.article_max_count)

    def describe(self) -> dict:
        """JSON-safe view for logs, the ops probe, and the docs — one place to read the truth."""
        return {k: v for k, v in dataclasses.asdict(self).items()}


def load() -> RetentionPolicy:
    """The active policy from the environment. Cheap; call it per run rather than caching, so an
    operator's ``deploy/.env`` edit takes effect on the next cycle after a restart."""
    return RetentionPolicy(
        article_max_age_days=_int_env("RWE_RETENTION_MAX_AGE_DAYS", 0),
        article_max_count=_int_env("RWE_RETENTION_MAX_COUNT", 0),
        scored_cache_days=_int_env("RWE_RETENTION_SCORED_DAYS", 30),
        analytics_event_days=_int_env("RWE_RETENTION_ANALYTICS_DAYS", 180),
        rec_event_days=_int_env("RWE_RETENTION_REC_EVENT_DAYS", 365),
        snapshots_per_user=_int_env("RWE_RETENTION_SNAPSHOTS_PER_USER", 500),
        notifications_per_user=_int_env("RWE_RETENTION_NOTIFICATIONS_PER_USER", 200),
        batch_limit=_int_env("RWE_RETENTION_BATCH_LIMIT", 5000, minimum=1),
    )


#: Tables this policy will NEVER touch, and why. Referenced by the docs and asserted by tests so
#: adding a prune for one of these fails loudly rather than quietly shipping.
PROTECTED_TABLES = {
    "users": "account identity",
    "identities": "sign-in linkage",
    "onboarding": "account state",
    "user_settings": "reader preferences",
    "reads": "the reader's own reading history + every report's input",
    "saved_articles": "explicit user saves",
    "api_tokens": "credentials",
    "rec_feedback": "explicit user feedback (like/dislike/ignore)",
    "improvement_lifecycle": "audit trail of suggested improvements",
    "feed_health": "one row per feed — naturally bounded, and the ops diagnostic",
}
