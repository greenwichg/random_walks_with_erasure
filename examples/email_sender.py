"""SMTP transport for outbound email — the email channel's equivalent of ``push_sender``.

**Stdlib only, and provider-agnostic on purpose.** Every mail service worth using speaks SMTP:
Amazon SES, Postmark, SendGrid, Mailgun, a self-hosted relay. Writing to SMTP rather than to one
vendor's REST API means choosing a provider is a change to ``deploy/.env``, not a change to code —
and it keeps the engine free of an SDK (botocore alone is tens of megabytes) in a codebase that
imports ``pywebpush`` lazily precisely so its absence cannot break the test suite.

The interesting part of a mail transport is not sending; it is **classifying what happened**, which
is what decides whether a reader gets another attempt or is never written to again:

* a 4xx is the server saying "not now" — greylisting, a rate limit, a queue that is full. Retry.
* 550/551/552/553/554 is the server saying "not ever" **for this recipient** — no such mailbox,
  domain does not exist. Retrying is what turns a typo into a reputation problem, and reputation is
  what decides whether the mail anyone *does* want reaches them. Only these suppress an address.
* any other 5xx is a 5xx about US, not about them: a protocol error (500–504), or our own From
  address rejected because the sender domain is unverified. Retry, and log it as an operator
  problem — suppressing readers over our own misconfiguration is the expensive failure here.
* a socket error is the network, not the recipient. Retry.

    RWE_SMTP_HOST / RWE_SMTP_PORT     the relay (587 STARTTLS, 465 implicit TLS, 25 plain)
    RWE_SMTP_USER / RWE_SMTP_PASSWORD credentials, when the relay wants them
    RWE_EMAIL_FROM                    the envelope + header From ("Hidden View <digest@…>")
    RWE_EMAIL_ENABLED                 the switch; off ⇒ nothing is ever sent
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr

log = logging.getLogger(__name__)

#: SMTP status codes that mean "this RECIPIENT is bad", not "try later" — the only codes that may
#: put an address on the suppression list. 550/551/553 are the classic no-such-mailbox family, 552
#: is over-quota (which large providers return permanently), 554 is a transaction failure that at
#: the RCPT stage is a rejection of the recipient.
#:
#: Note what is NOT here: 500–504. Those are protocol errors — bad syntax, command not implemented,
#: bad sequence — and they are about OUR conversation with the relay, not about the mailbox. A
#: blanket "5xx ⇒ permanent" reads them as a dead address and suppresses a reader who was never
#: written to at all, forever, over a bug at our end.
_PERMANENT = frozenset({550, 551, 552, 553, 554})


class _NoTlsOffered(smtplib.SMTPException):
    """The relay advertises no STARTTLS, so we hang up instead of downgrading to plaintext.

    A distinct type rather than a reuse of ``SMTPNotSupportedError``: ``login`` raises that same
    class when the server has no AUTH extension, and the two want opposite handling — one is a
    refusal to proceed, the other is an ordinary credential problem to retry."""


@dataclass(frozen=True)
class SendResult:
    """What one send attempt did. Mirrors ``push_sender.SendResult`` so the two delivery workers
    can reason about outcomes identically."""

    status: str                      # "sent" | "retry" | "bounced" | "no-tls"
    code: "int | None" = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "sent"

    @property
    def retryable(self) -> bool:
        return self.status == "retry"

    @property
    def bounced(self) -> bool:
        """A hard rejection: this address must not be written to again. The caller suppresses it."""
        return self.status == "bounced"


def classify_code(code: "int | None") -> str:
    """An SMTP reply code → an outcome. Anything we do not recognise retries.

    The asymmetry is deliberate and is the whole design of this function. Retrying a genuinely dead
    mailbox costs three attempts a week, bounded by ``RWE_EMAIL_RETRY_MAX``, and then the delivery
    is marked failed anyway. Suppressing a live reader costs them every future digest, silently,
    with nothing in the UI to explain it. So only the recipient-rejection family in
    :data:`_PERMANENT` earns a bounce; every other 5xx is an operator problem wearing a 5xx."""
    if code is None:
        return "retry"
    if 200 <= code < 300:
        return "sent"
    if code in _PERMANENT:
        return "bounced"
    return "retry"                                   # 4xx, 5xx protocol errors, anything else


def classify_exception(exc: BaseException) -> SendResult:
    """Map an smtplib/socket failure onto an outcome, by exception TYPE NAME rather than by
    importing the classes — the same discipline ``push_sender`` uses, so this module keeps loading
    where an optional dependency does not."""
    name = type(exc).__name__
    code = getattr(exc, "smtp_code", None)
    if name == "SMTPSenderRefused":
        # OUR From address was rejected, not their mailbox — an unverified sender domain, or an SES
        # account still in the sandbox. Classifying by the code would read 553 as "no such mailbox"
        # and suppress every reader we tried to write to, permanently, for a misconfiguration that
        # a DNS record fixes. Retry, and say plainly whose problem it is.
        log.error("email_sender_refused: RWE_EMAIL_FROM was rejected by the relay (%s) — verify the "
                  "sender domain (SPF/DKIM), and leave the provider's sandbox if it is in one", exc)
        return SendResult("retry", code=int(code) if code is not None else None,
                          detail=f"sender refused: {str(exc)[:170]}")
    if name == "SMTPRecipientsRefused":
        # Per-recipient codes; a single recipient here, so take the worst one we were given.
        codes = [c for c, _msg in (getattr(exc, "recipients", {}) or {}).values()]
        code = max(codes) if codes else 550
    if name in ("SMTPDataError", "SMTPResponseException", "SMTPRecipientsRefused"):
        return SendResult(classify_code(int(code) if code is not None else None),
                          code=int(code) if code is not None else None, detail=str(exc)[:200])
    if name in ("SMTPAuthenticationError",):
        # Our credentials, not their mailbox: retrying every reader would hammer the relay and
        # suppress nobody, so this retries — but it is an operator problem and says so.
        log.error("email_smtp_auth_failed: check RWE_SMTP_USER / RWE_SMTP_PASSWORD")
        return SendResult("retry", code=int(code) if code is not None else None, detail="auth failed")
    if name in ("SMTPServerDisconnected", "SMTPConnectError", "SMTPHeloError", "SMTPNotSupportedError",
                "SMTPException", "TimeoutError", "socket.timeout", "OSError", "ConnectionError",
                "ConnectionRefusedError", "ConnectionResetError", "gaierror"):
        return SendResult("retry", detail=f"{name}: {str(exc)[:180]}")
    return SendResult("retry", detail=f"{name}: {str(exc)[:180]}")


def build_message(*, to: str, subject: str, text: str, html: str, sender: str,
                  headers: "dict[str, str] | None" = None) -> EmailMessage:
    """A multipart/alternative message: plain text first, HTML as the richer alternative.

    The text part is not a courtesy. It is what a screen reader, a text client, and every spam
    filter reads, and an HTML-only mail is scored accordingly."""
    msg = EmailMessage()
    name, addr = parseaddr(sender)
    msg["From"] = formataddr((name, addr)) if name else addr
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=(addr.split("@")[-1] or None) if "@" in addr else None)
    for key, value in (headers or {}).items():
        if value:
            msg[key] = value
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


class SmtpSender:
    """One SMTP relay, opened per send.

    Per send rather than a pooled connection: a weekly digest run is minutes of work once a week,
    a relay drops idle connections anyway, and a shared socket across threads is a source of
    interleaved-command bugs that only appear under load."""

    def __init__(self, *, host: str, port: int = 587, user: str = "", password: str = "",
                 sender: str = "", timeout: float = 20.0, use_tls: "bool | None" = None):
        self.host, self.port = host, int(port)
        self.user, self.password = user, password
        self.sender = sender
        self.timeout = timeout
        #: 465 is implicit TLS (SMTPS); everything else negotiates STARTTLS after connecting.
        self.implicit_tls = (self.port == 465) if use_tls is None else bool(use_tls)

    def _dial(self) -> smtplib.SMTP:
        """Connect, secure the channel, authenticate — everything a send does except the sending.

        Shared with :meth:`verify` deliberately. A preflight that dialled differently from a real
        send would prove nothing about whether a real send works, which is worse than no preflight:
        it is a green light with no wiring behind it."""
        ctx = ssl.create_default_context()
        client = (smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=ctx)
                  if self.implicit_tls
                  else smtplib.SMTP(self.host, self.port, timeout=self.timeout))
        try:
            if not self.implicit_tls:
                try:
                    client.starttls(context=ctx)
                except smtplib.SMTPNotSupportedError as exc:
                    # A relay with no TLS at all: refuse rather than send a reader's reading history
                    # in the clear. Its own exception type, because `login` raises the same class
                    # when AUTH is unsupported and the two need opposite responses.
                    log.error("email_smtp_no_tls: %s:%s offers no STARTTLS", self.host, self.port)
                    raise _NoTlsOffered(str(exc)) from exc
            if self.user:
                client.login(self.user, self.password)
        except BaseException:
            try:
                client.close()
            except Exception:                          # noqa: BLE001 — already failing; don't mask it
                pass
            raise
        return client

    def send(self, msg: EmailMessage) -> SendResult:
        """Deliver one message. Never raises — a delivery worker must not die on one bad address."""
        try:
            with self._dial() as client:
                client.send_message(msg)
            return SendResult("sent", code=250)
        except _NoTlsOffered:
            return SendResult("no-tls", detail="relay offers no TLS")
        except BaseException as exc:                  # noqa: BLE001 — classified, never propagated
            return classify_exception(exc)

    def verify(self) -> SendResult:
        """Prove the relay will have us, **without sending anything**.

        The point is to separate "my configuration is wrong" from "that message or that recipient
        was rejected", because from outside they look identical: a run reports nothing sent either
        way. This dials and hangs up, so a green answer means the next real send can only fail on
        the message or the address."""
        try:
            with self._dial() as client:
                client.noop()
            return SendResult("sent", code=250, detail="relay accepted the connection and login")
        except _NoTlsOffered:
            return SendResult("no-tls", detail="relay offers no TLS")
        except BaseException as exc:                  # noqa: BLE001 — a check must not raise either
            return classify_exception(exc)


def enabled() -> bool:
    return (os.environ.get("RWE_EMAIL_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


def sender_from_env() -> "SmtpSender | None":
    """The configured relay, or ``None`` when email is off or incompletely configured.

    ``None`` is a first-class answer: the delivery worker does nothing, the in-app digest is
    untouched, and no half-configured deployment starts mailing readers from a default address."""
    if not enabled():
        return None
    host = (os.environ.get("RWE_SMTP_HOST") or "").strip()
    from_addr = (os.environ.get("RWE_EMAIL_FROM") or "").strip()
    if not host or not from_addr:
        log.warning("email_not_configured: RWE_EMAIL_ENABLED is on but "
                    "RWE_SMTP_HOST / RWE_EMAIL_FROM are not both set — nothing will be sent")
        return None
    return SmtpSender(
        host=host,
        port=int((os.environ.get("RWE_SMTP_PORT") or "587").strip() or 587),
        user=(os.environ.get("RWE_SMTP_USER") or "").strip(),
        password=os.environ.get("RWE_SMTP_PASSWORD") or "",
        sender=from_addr,
    )
