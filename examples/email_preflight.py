#!/usr/bin/env python3
"""Check the weekly-digest email configuration. **Sends nothing.**

    dc exec -T api python examples/email_preflight.py

Exists because every way this configuration can be wrong looks the same from outside: a run
reports ``sent: 0`` whether the app password is wrong, the From is unverified, the allowlist is
empty, or simply nobody has opted in yet. This separates them, and it dials the relay through the
same code path a real send uses (:meth:`email_sender.SmtpSender.verify`), so a green answer means
the configuration is not what is stopping mail.

Exit status is 0 when the deployment could send to someone, 1 otherwise — so it is usable as a
gate in a script, not only as something to read.

**Prints no secret.** The password is reported as a length, never a value: an operator pasting a
preflight into a chat or a ticket must not be pasting their credentials with it.
"""

from __future__ import annotations

import os
import sys

import email_consent
import email_delivery
import email_sender

TICK, CROSS, DASH = "OK  ", "FAIL", "--  "


def _set(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _report_config() -> "list[str]":
    """One line per variable. Returns the problems found, in the order they must be fixed."""
    problems = []

    enabled = email_sender.enabled()
    print(f"{TICK if enabled else DASH} RWE_EMAIL_ENABLED     {_set('RWE_EMAIL_ENABLED') or '(unset)'}")
    if not enabled:
        problems.append("RWE_EMAIL_ENABLED is not 1 — the worker does nothing at all.")

    # Required vs defaulted, because marking something FAIL that the code has a working default
    # for teaches an operator to ignore the FAIL column.
    for name, fallback in (("RWE_SMTP_HOST", None), ("RWE_SMTP_PORT", "587 (default)"),
                           ("RWE_SMTP_USER", "(none — an open relay wants no login)"),
                           ("RWE_EMAIL_FROM", None),
                           # Optional by design — a From that IS a mailbox wants no Reply-To. Shown
                           # anyway, because "not configured" and "configured but not reaching the
                           # container" look identical from outside, and the second is what happened:
                           # it was written to deploy/.env and missing from the compose environment
                           # block, so the header silently never appeared.
                           ("RWE_EMAIL_REPLY_TO", "(none — replies follow From)"),
                           ("RWE_PUBLIC_URL", None)):
        value = _set(name)
        mark = TICK if value else (DASH if fallback else CROSS)
        print(f"{mark} {name:<21} {value or fallback or '(unset)'}")
        if not value and fallback is None:
            problems.append(f"{name} is unset.")

    # Length only. The value is a credential and this output gets pasted into tickets.
    pw = os.environ.get("RWE_SMTP_PASSWORD") or ""
    print(f"{TICK if pw else DASH} RWE_SMTP_PASSWORD     "
          f"{f'set, {len(pw)} characters' if pw else '(unset)'}")
    if pw and pw != pw.strip():
        problems.append("RWE_SMTP_PASSWORD has leading or trailing whitespace — quote it in .env.")

    secret = email_consent.secret()
    print(f"{TICK if secret else CROSS} RWE_EMAIL_SECRET      "
          f"{f'set, {len(secret)} characters' if secret else '(unset)'}")
    if not secret:
        problems.append("RWE_EMAIL_SECRET is unset — nothing is sent, because no unsubscribe "
                        "link could be signed.")
    return problems


def _report_allowlist() -> "list[str]":
    raw = _set("RWE_EMAIL_ALLOWLIST")
    allow = email_delivery.allowlist()
    if allow is None:
        print(f"{TICK} RWE_EMAIL_ALLOWLIST   {raw} — GENERAL DELIVERY: every consenting reader "
              f"is mailed")
        return []
    if not allow:
        shown = repr(raw) if raw else "(unset)"
        print(f"{CROSS} RWE_EMAIL_ALLOWLIST   {shown} — nobody is cleared to receive")
        return ["RWE_EMAIL_ALLOWLIST clears nobody. Set it to a comma-separated list of testers, "
                f"or to {email_delivery.ALLOW_ALL!r} for general delivery."]
    print(f"{TICK} RWE_EMAIL_ALLOWLIST   {len(allow)} entr{'y' if len(allow) == 1 else 'ies'}: "
          f"{', '.join(sorted(allow))}")
    return []


def diagnose(result) -> str:
    """A failed dial → what to actually go and change. ``""`` when we have nothing useful to add.

    Pure, and separated from the printing on purpose: the routing is the part worth testing, and
    the failures it routes (a wrong app password, an unverified From, an unreachable host) cannot
    be reproduced against a local relay without a trusted certificate."""
    detail = (result.detail or "").lower()
    if result.status == "no-tls":
        return ("The relay offers no STARTTLS and we refuse to send in the clear. Check the port: "
                "587 for STARTTLS, 465 for implicit TLS.")
    if "auth" in detail or "535" in detail or "534" in detail:
        return ("Authentication failed. For Gmail this is almost always the password: it must be a "
                "16-character App Password (Google Account -> Security -> 2-Step Verification -> "
                "App passwords), not your Google password.")
    if "sender refused" in detail or "not verified" in detail:
        return ("The relay rejected RWE_EMAIL_FROM. The sender domain must be verified with the "
                "provider, and for Gmail the From must be the authenticated account or an alias "
                "verified under Settings -> Accounts -> Send mail as.")
    if any(token in detail for token in ("gaierror", "connectionrefused", "timeout", "connect")):
        return ("Could not reach the relay at all. Check RWE_SMTP_HOST / RWE_SMTP_PORT, and that "
                "the host's egress on that port is open — many networks block outbound 25/587.")
    if "certificate" in detail or "ssl" in detail:
        return ("The TLS handshake failed. The relay's certificate did not verify; check the "
                "hostname is the one the certificate is issued for.")
    return ""


def _report_relay() -> "list[str]":
    """Dial the relay and hang up. The one check that touches the network."""
    sender = email_sender.sender_from_env()
    if sender is None:
        print(f"{CROSS} relay                 not configured — cannot dial")
        return ["The relay is not fully configured; fix the variables above first."]

    print(f"{DASH} relay                 dialling {sender.host}:{sender.port} "
          f"({'implicit TLS' if sender.implicit_tls else 'STARTTLS'})…")
    result = sender.verify()
    if result.ok:
        print(f"{TICK} relay                 {result.detail}")
        return []

    print(f"{CROSS} relay                 {result.status}: {result.detail}")
    hint = diagnose(result)
    if hint:
        print(f"     {hint}")
    return [f"The relay refused us ({result.status})."]


def main() -> int:
    print("weekly digest email — configuration preflight (sends nothing)\n")
    problems = _report_config()
    problems += _report_allowlist()
    print()
    problems += _report_relay()

    print()
    if problems:
        print("NOT READY:")
        for i, problem in enumerate(problems, 1):
            print(f"  {i}. {problem}")
        print("\nSee docs/WEEKLY_DIGEST_EMAIL.md")
        return 1
    print("READY — the configuration is sound. Readers still receive nothing until they turn the "
          "email toggle on themselves (Settings -> Notifications -> Weekly digest).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
