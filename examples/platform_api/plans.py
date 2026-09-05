"""plans.py — what a key may read and how much: scopes, licence classes, rate, monthly quota.

A PLAN is a named bundle of defaults; a KEY carries a plan and may override any of the four
numbers/sets individually (``platform_keys.py mint --scopes … --quota …``). Plans are data — a new
commercial tier is a row here, not a code path — and the vocabulary is closed on purpose: a
scope the router does not check is a promise nobody keeps.

Scopes
    articles:read       /v1/articles, /v1/articles/{id}
    stories:read        /v1/stories, /v1/stories/{id}, /similar, /intelligence
    stories:history     /v1/stories/{id}/history — the persisted snapshots + membership deltas
    publishers:read     /v1/publishers, /v1/publishers/{id}
    usage:read          /v1/usage — the tenant's own meter

Licence classes (``licence.py``): which rows a plan receives in full. ``reader_private`` is never
in any plan — it is not ours to license. Third-party ratings are NOT a plan property: publishing
them is a deployment decision (``RWE_PLATFORM_PUBLISH_RATINGS``), because it is a licence the
operator holds or does not, whatever the customer pays.
"""

from __future__ import annotations

SCOPES = ("articles:read", "stories:read", "stories:history", "publishers:read", "usage:read")

PLANS = {
    # The operator's own keys: every scope, every licensable class, no quota.
    "internal": {"scopes": SCOPES,
                 "licence_classes": ("metadata_public", "provider_restricted", "unknown"),
                 "rate_per_min": 600, "quota_month": 0},
    # Product 1 — the developer API. Public-licence rows only.
    "developer": {"scopes": ("articles:read", "stories:read", "publishers:read", "usage:read"),
                  "licence_classes": ("metadata_public",),
                  "rate_per_min": 60, "quota_month": 10_000},
    # Products 2 / 5 — enterprise intelligence and premium B2B: history, higher limits.
    "enterprise": {"scopes": SCOPES, "licence_classes": ("metadata_public",),
                   "rate_per_min": 300, "quota_month": 250_000},
}


def plan(name: str) -> dict:
    """A plan's defaults; an unknown name gets the most restrictive plan, never the loosest."""
    return PLANS.get(name) or PLANS["developer"]


def effective(key_row: dict) -> dict:
    """The limits a key actually runs under: its plan's defaults, overridden by the key's own
    non-null values. Scopes and classes are intersected with the closed vocabularies."""
    base = plan(key_row.get("plan") or "developer")
    scopes = key_row.get("scopes") or base["scopes"]
    classes = key_row.get("licenceClasses")
    if classes is None:
        classes = base["licence_classes"]
    rate = key_row.get("ratePerMin")
    quota = key_row.get("quotaMonth")
    return {
        "plan": key_row.get("plan") or "developer",
        "scopes": frozenset(s for s in scopes if s in SCOPES),
        "licence_classes": frozenset(c for c in classes if c != "reader_private"),
        "rate_per_min": int(base["rate_per_min"] if rate is None else rate),
        "quota_month": int(base["quota_month"] if quota is None else quota),
    }


__all__ = ["SCOPES", "PLANS", "plan", "effective"]
