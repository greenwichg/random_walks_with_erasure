"""auth.py — who is calling ``/v1``: a platform key resolved to a Principal, or a typed refusal.

The key travels as ``Authorization: Bearer hv_live_…``; only its SHA-256 is stored
(``platform_keys.key_hash``), exactly like a reader's extension token. Revocation is a timestamp
on the row (a revoked key stays on record for the audit trail), expiry is a timestamp compared
here, and a suspended tenant refuses every key it owns. Every refusal is a :class:`PlatformError`
carrying a stable ``code`` the client can branch on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from starlette.exceptions import HTTPException as StarletteHTTPException

import platform_api
from platform_api import plans


class PlatformError(StarletteHTTPException):
    """An HTTP refusal with a stable machine-readable ``code`` — rendered by ``app.py`` into the
    engine's error envelope ``{"error": {"code", "message", "requestId"}}``."""

    def __init__(self, status_code: int, code: str, message: str,
                 headers: "dict | None" = None):
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code = code


@dataclass(frozen=True)
class Principal:
    key_id: str
    tenant_id: str
    tenant_kind: str
    plan: str
    scopes: frozenset
    licence_classes: frozenset
    rate_per_min: int
    quota_month: int


def bearer_from(request) -> Optional[str]:
    """The presented key: ``Authorization: Bearer …`` first, ``X-API-Key: …`` as the alternative
    many HTTP clients and gateways prefer. Query-string keys are deliberately not accepted — they
    land in access logs and browser histories."""
    raw = request.headers.get("authorization") or ""
    scheme, _, value = raw.strip().partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    alt = (request.headers.get("x-api-key") or "").strip()
    return alt or None


def authenticate(store_, request, *, now: "datetime | None" = None) -> Principal:
    """Resolve the request's key to a :class:`Principal`, or raise a :class:`PlatformError`."""
    if not platform_api.enabled():
        raise PlatformError(503, "platform_disabled",
                            "The platform API is not enabled on this deployment.")
    secret = bearer_from(request)
    if not secret:
        raise PlatformError(401, "unauthenticated",
                            "Provide an API key: Authorization: Bearer hv_live_…")
    row = store_.platform_resolve_key(secret)
    if row is None:
        raise PlatformError(401, "unauthenticated", "Unknown API key.")
    if row.get("revokedAt"):
        raise PlatformError(401, "key_revoked", "This API key has been revoked.")
    now_iso = (now or datetime.now(timezone.utc)).isoformat()
    if row.get("expiresAt") and str(row["expiresAt"]) <= now_iso:
        raise PlatformError(401, "key_expired", "This API key has expired.")
    tenant = row.get("tenant")
    if tenant is None or tenant.get("status") != "active":
        raise PlatformError(403, "tenant_suspended", "This account is not active.")
    eff = plans.effective(row)
    return Principal(key_id=row["keyId"], tenant_id=row["tenantId"],
                     tenant_kind=tenant.get("kind") or "developer", plan=eff["plan"],
                     scopes=eff["scopes"], licence_classes=eff["licence_classes"],
                     rate_per_min=eff["rate_per_min"], quota_month=eff["quota_month"])


def require_scope(principal: Principal, scope: str) -> None:
    if scope not in principal.scopes:
        raise PlatformError(403, "forbidden_scope",
                            f"This key does not carry the {scope!r} scope.")


__all__ = ["PlatformError", "Principal", "bearer_from", "authenticate", "require_scope"]
