"""Consent for outbound email: who may be written to, and how they stop it.

Three rules, and the third is the one that is easy to get wrong.

**Consent is opt-in and already stored.** ``notifications.weeklyDigest`` is the reader's existing
toggle, and ``notifications.categories.digests.email`` is the channel dimension the settings schema
was built with (it shipped read-by-nothing precisely so this could arrive without migrating every
reader's stored blob). Both must be true, plus a real email address, plus no suppression.

**Unsubscribe must work with no login.** A reader who has forgotten the account, changed devices,
or is reading in a mail client cannot be asked to sign in first — and a link that demands it is
what gets mail marked as spam instead. So the link carries a SIGNED TOKEN naming the user and the
purpose, verified without a session.

**And the token must not be a password.** It authorises exactly one thing — turning a category off
— and nothing else. It is scoped by purpose, so a digest unsubscribe link cannot be replayed
against another category; it is derived from a server secret with HMAC, so it cannot be forged; and
it is compared in constant time. It deliberately does NOT expire: an unsubscribe link in a
two-year-old email must still work, because the alternative is a reader who cannot make it stop.

    RWE_EMAIL_SECRET   the signing key. Absent ⇒ no token can be minted or verified, so the
                       delivery worker refuses to send mail it could not let anyone escape.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

import notification_service as ns          # a pure, dependency-free leaf — the channel registry

#: The one thing a digest unsubscribe token may do. A purpose string in the signature means a token
#: minted for digests cannot be replayed to silence a different category.
PURPOSE_DIGEST = "digest"

#: The digest's two gates, both READ FROM THE REGISTRY rather than spelled out here.
#:
#: `DIGEST_KIND.setting_path` is the flat "do I want a weekly digest at all" toggle; the category
#: leaf is the channel dimension. Deriving both means the settings schema, the notification gate and
#: the send path cannot drift into disagreeing about which preference decides — the failure mode
#: where a reader turns something off in one vocabulary and keeps receiving it under the other.
DIGEST_KIND = next(k for k in ns.NOTIFICATION_KINDS if k.kind == "weekly_digest")
DIGEST_CATEGORY = "digests"
EMAIL_LEAF = ns.CHANNEL_SETTING_KEYS[ns.EMAIL]


def secret() -> str:
    return (os.environ.get("RWE_EMAIL_SECRET") or "").strip()


def _sign(uid: int, purpose: str, key: str) -> str:
    mac = hmac.new(key.encode("utf-8"), f"{purpose}:{int(uid)}".encode("utf-8"), hashlib.sha256)
    return base64.urlsafe_b64encode(mac.digest()[:18]).decode("ascii").rstrip("=")


def make_token(uid: int, purpose: str = PURPOSE_DIGEST) -> str:
    """A token authorising exactly one unsubscribe, or "" when no secret is configured."""
    key = secret()
    return f"{int(uid)}.{purpose}.{_sign(uid, purpose, key)}" if key else ""


def verify_token(token: str, purpose: str = PURPOSE_DIGEST) -> "int | None":
    """The user id a token authorises, or ``None``.

    Constant-time comparison: a token check that returns early on the first wrong byte leaks how
    much of a guess was right, which is how a forgeable token gets forged."""
    key = secret()
    if not key or not token:
        return None
    parts = str(token).split(".")
    if len(parts) != 3:
        return None
    raw_uid, got_purpose, sig = parts
    if got_purpose != purpose or not raw_uid.isdigit():
        return None
    uid = int(raw_uid)
    return uid if hmac.compare_digest(sig, _sign(uid, purpose, key)) else None


def unsubscribe_url(uid: int, base_url: str, purpose: str = PURPOSE_DIGEST) -> str:
    """The link that appears in the mail. Empty when no token can be minted — the caller treats
    that as "do not send", because mail nobody can unsubscribe from must not go out."""
    token = make_token(uid, purpose)
    return f"{base_url.rstrip('/')}/unsubscribe?t={token}" if token else ""


def _at(settings: dict, path: str) -> bool:
    """A boolean preference at a dotted path, fail-closed — the same read ``notification_service``
    performs, kept here so this module stays importable on its own."""
    node = settings
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return bool(node)


def may_email_digest(settings: dict, email: str, suppressed: bool = False) -> "str | None":
    """``None`` when the reader may be sent a weekly digest, else the REASON they may not.

    Returning the reason rather than a bare False is what makes a quiet run diagnosable: "sent 0"
    is indistinguishable from a broken worker until it can say 412 no-address, 38 unsubscribed."""
    if suppressed:
        return "suppressed"
    addr = (email or "").strip()
    if "@" not in addr or addr.startswith("@") or addr.endswith("@"):
        return "no-address"
    # BOTH gates, in order. The flat toggle answers "do I want a weekly digest"; the category leaf
    # answers "delivered where". `gate_path` deliberately refuses to fold the second into the first
    # (see its docstring), so the conjunction is spelled out here — the one place that sends mail.
    if not _at(settings, DIGEST_KIND.setting_path):
        return "digest-off"
    # Absent means off. A channel nobody has opted into is not consent, and defaulting it to on
    # would mail every existing reader the moment this deploys.
    if not _at(settings, f"notifications.categories.{DIGEST_CATEGORY}.{EMAIL_LEAF}"):
        return "email-channel-off"
    return None
