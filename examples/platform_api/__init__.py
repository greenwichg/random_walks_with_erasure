"""platform_api — the commercial access layer over the SAME engine (``/v1``).

Owns ACCESS (who: keys, tenants, scopes; how much: plans, rate, monthly quota, metering) and
SHAPE (versioned envelopes, licence-class withholding). Owns NO intelligence: every answer comes
from the service functions the consumer routes already call — ``search.search``,
``story_service.list_stories`` / ``get_story`` / ``similar_stories``,
``story_intelligence.intelligence_for``, ``publisher_service.get_publisher`` — or from the
persisted history those services write. A product that needs a new computation adds it to the
intelligence plane, where both front doors see it.

Mounted INTO the engine app behind ``RWE_PLATFORM_API=1`` (default off): the engine is never
internet-facing today and carries no third-party load, so a second process would be a second thing
to operate for no measured benefit. The package is self-contained — ``platform_api.app.create_app``
runs it standalone the day isolation is worth a process (docs/NEWS_INTELLIGENCE_INFRASTRUCTURE.md,
§D.2, revised in the implementation note).

Boundary, pinned by ``tests/test_platform_boundaries.py``: nothing under this package imports
the recommender, the report, the coach, notifications, or any reader-scoped state.
"""

from __future__ import annotations

import os


def enabled() -> bool:
    """Whether the ``/v1`` surface is served. Read at call time: a key presented while the
    switch is off is refused with ``503 platform_disabled``, never silently served."""
    return os.environ.get("RWE_PLATFORM_API", "").strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["enabled"]
