"""Request-size limits for the Information Health engine — reject oversized payloads early.

Complements the rate limiter (which bounds request *frequency*) with a *size* control that bounds
*memory*: a Content-Length check in the serving layer refuses a too-large body **before** it is
buffered or parsed, and the ingestion path additionally bounds batch shape (reads per request,
URL / title / text lengths). Nothing here changes an algorithm, a serialiser, or the contract for
*valid* requests — only oversized requests are refused, with a typed ``413``. Limits are per
endpoint class and configurable via environment variables (relaxed in development).

Endpoint class comes from the same ``(method, path) -> scope`` classifier the rate limiter uses
(:func:`ratelimit.scope_for`), so there is one place that knows what each endpoint is.
"""

from __future__ import annotations

import os
from typing import Optional

import ratelimit  # reuse the single (method, path) -> scope classification

# Maximum request body in BYTES per scope, in PRODUCTION. Generous for legitimate payloads (see the
# per-endpoint audit) while capping the worst case hard. Development multiplies by BODY_DEV_FACTOR.
BODY_LIMITS = {
    "auth": 4_096,          # tiny identity/token bodies
    "ai": 16_384,           # a coach prompt + envelope
    "ingest": 1_048_576,    # a full batch of reads (see MAX_READS_PER_BATCH below)
    "write": 32_768,        # settings / onboarding / small writes
    "default": 16_384,
}
BODY_DEV_FACTOR = 4

# Ingestion batch-shape limits (the browser-extension / paste / RSS path). Defaults are generous for
# real use — the extension posts one read; an RSS sync a few dozen — yet bound the batch hard.
MAX_READS_PER_BATCH = 100
MAX_URL_LEN = 2_048          # matches the stored canonical_url column; the URL spec's practical max
MAX_TITLE_LEN = 512          # headlines run well under 200 chars
MAX_TEXT_LEN = 2_048         # subtitle / description (og:description) each


def _int_env(name: str) -> Optional[int]:
    v = os.environ.get(name)
    if v is not None and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    return None


def _limit(name: str, default: int) -> int:
    override = _int_env(name)
    return max(1, override) if override is not None else default


def body_limit_for(scope: str, production: bool) -> int:
    """The max body size (bytes) for ``scope``. Env override wins; else the production default,
    multiplied by :data:`BODY_DEV_FACTOR` outside production."""
    override = _int_env(f"RWE_BODY_LIMIT_{scope.upper()}_BYTES")
    if override is not None:
        return max(1, override)
    base = BODY_LIMITS.get(scope, BODY_LIMITS["default"])
    return base if production else base * BODY_DEV_FACTOR


def max_body_bytes(method: str, path: str, production: bool) -> Optional[int]:
    """Byte cap for a request's body, or ``None`` when there is nothing to cap — an exempt path,
    a pre-flight, or a read-only method (no body)."""
    scope = ratelimit.scope_for(method, path)
    if scope is None or scope == "read":
        return None
    return body_limit_for(scope, production)


def reads_batch_error(reads) -> Optional[str]:
    """A human-readable reason a reads batch is too large, or ``None`` if it is acceptable.

    Operates on the already-parsed list (so it is bounded by the byte cap), with cheap structural
    checks only: batch count, then per-read URL / title / subtitle / description lengths. Shape and
    type validity are already guaranteed by the ``ReadsRequest`` model (a malformed batch is a 422
    before this runs); this adds the *size* limits, which surface as ``413``."""
    n_max = _limit("RWE_MAX_READS_PER_BATCH", MAX_READS_PER_BATCH)
    if len(reads) > n_max:
        return f"too many reads in one request (max {n_max})"
    url_max = _limit("RWE_MAX_URL_LEN", MAX_URL_LEN)
    title_max = _limit("RWE_MAX_TITLE_LEN", MAX_TITLE_LEN)
    text_max = _limit("RWE_MAX_TEXT_LEN", MAX_TEXT_LEN)
    for r in reads:
        if len(getattr(r, "url", "") or "") > url_max:
            return f"a read URL exceeds the maximum length ({url_max})"
        if len(getattr(r, "title", "") or "") > title_max:
            return f"a read title exceeds the maximum length ({title_max})"
        if (len(getattr(r, "subtitle", "") or "") > text_max
                or len(getattr(r, "description", "") or "") > text_max):
            return f"a read subtitle/description exceeds the maximum length ({text_max})"
    return None
