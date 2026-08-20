"""The weekly digest email: consent, localization, delivery, retries, bounces, idempotency.

The in-app digest is the product; this is a second CHANNEL for the same notification. Every test
here exists because the failure it prevents is silent: mail nobody consented to, mail nobody can
escape, the same reader mailed twice for one week, or a dead address written to until the sender's
reputation is gone.
"""

import importlib.util
import os
import pathlib
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import email_consent          # noqa: E402
import email_digest           # noqa: E402
import email_delivery         # noqa: E402
import email_sender           # noqa: E402
import notification_service as ns   # noqa: E402
import settings_service       # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RWE_EMAIL_SECRET", "test-secret-not-a-real-one")
    monkeypatch.setenv("RWE_PUBLIC_URL", "https://hidden-view.com")
    monkeypatch.setenv("RWE_EMAIL_ENABLED", "1")
    # The recipient gate is OPENED here on purpose, and it is the only thing in this fixture that
    # is not the production default: unset means nobody, so every test about delivery mechanics
    # would otherwise assert against a worker that returned before doing anything. The gate's own
    # behaviour — including that the default is shut — is tested explicitly below.
    monkeypatch.setenv("RWE_EMAIL_ALLOWLIST", email_delivery.ALLOW_ALL)


@pytest.fixture
def st(monkeypatch):
    monkeypatch.setenv("RWE_DB_URL", f"sqlite:///{tempfile.mktemp(suffix='.db')}")
    for name in ("store",):
        sys.modules.pop(name, None)
    import store as store_mod
    return store_mod.Store(None)


class FakeSender:
    """A relay that answers however the test needs, and remembers what it was asked to send."""

    sender = "Hidden View <digest@hidden-view.com>"

    def __init__(self, *results):
        self.results = list(results) or [email_sender.SendResult("sent", code=250)]
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
        return self.results[min(len(self.sent) - 1, len(self.results) - 1)]


def _reader(st, *, email="reader@example.com", digest=True, channel=True, lang="en"):
    user = st.upsert_user_by_identity("google", f"u-{email}", email=email, display_name="R")
    settings_service.update(st, user.id, {
        "language": lang,
        "notifications": {"weeklyDigest": digest, "categories": {"digests": {"email": channel}}}})
    return user.id


def _digest_notification(st, uid, *, week="2026-W34", reads=12, recorded_at=None):
    """One materialised weekly digest. `recorded_at` is the DB write time the age window reads —
    left at the default it is "now", which is what every test but the backlog ones wants."""
    import json
    from store import Notification
    with st.session() as s:
        row = Notification(user_id=uid, kind="weekly_digest", dedupe_key=f"weekly_digest:{week}",
                           body=json.dumps({"kind": "weekly_digest", "title": "Your week",
                                            "payload": {"reads": reads, "streakDays": 3,
                                                        "overall": 69}}),
                           created_at=datetime.now(timezone.utc).isoformat())
        if recorded_at is not None:
            row.recorded_at = recorded_at
        s.add(row)
        s.flush()
        return int(row.id)


# --------------------------------------------------------------------------- #
# Consent — nobody is mailed who did not ask to be.
# --------------------------------------------------------------------------- #
def test_the_email_channel_is_off_by_default():
    """Consent, not caution. `categories.digests.email` defaulting to True would mail every
    existing reader the moment this deploys — none of whom asked for email."""
    fresh = settings_service.normalize_settings(None)
    assert fresh["notifications"]["categories"]["digests"]["email"] is False
    assert fresh["notifications"]["weeklyDigest"] is True, "the in-app digest is unchanged"


@pytest.mark.parametrize("mutation,expected", [
    ({}, None),
    ({"notifications": {"weeklyDigest": False}}, "digest-off"),
    ({"notifications": {"categories": {"digests": {"email": False}}}}, "email-channel-off"),
])
def test_consent_requires_both_the_digest_and_the_channel(mutation, expected):
    base = settings_service.normalize_settings(None, {
        "notifications": {"weeklyDigest": True, "categories": {"digests": {"email": True}}}})
    prefs = settings_service.normalize_settings(base, mutation)
    assert email_consent.may_email_digest(prefs, "r@example.com") == expected


def test_the_channel_is_registered_so_its_consent_leaf_is_a_real_gate():
    """A transport that sends but is absent from `CHANNEL_SETTING_KEYS` is a toggle wired to
    nothing: `gate_path` resolves it to "", `_gated` denies, and the setting the reader sees has
    no effect on anything. Registration is what makes the leaf mean something."""
    assert ns.CHANNEL_SETTING_KEYS.get(ns.EMAIL) == "email"
    assert email_consent.EMAIL_LEAF == ns.CHANNEL_SETTING_KEYS[ns.EMAIL], "one source, not two"
    assert email_consent.DIGEST_KIND.setting_path == "notifications.weeklyDigest"


def test_a_flat_toggle_is_not_consent_to_mail():
    """The in-app weekly digest predates the email channel, so `notifications.weeklyDigest` alone
    cannot authorise mail — a reader who ticked it years ago was answering WHETHER, not WHERE.

    Checked at the gate AND at the sender, because either one alone would be enough to leak: the
    gate refuses to hand email a path, and consent refuses the send."""
    prefs = settings_service.normalize_settings(None)          # weeklyDigest True, email False
    assert prefs["notifications"]["weeklyDigest"] is True
    assert ns.gate_path(email_consent.DIGEST_KIND, ns.EMAIL) == "", "no path for email to inherit"
    assert email_consent.may_email_digest(prefs, "r@example.com") == "email-channel-off"


# --------------------------------------------------------------------------- #
# The recipient allowlist — an OPERATOR gate, with the opposite safe default to consent.
# --------------------------------------------------------------------------- #
def test_the_sender_can_be_swapped_without_touching_the_digest(monkeypatch):
    """Beta sends from a personal address; later it sends from `digest@hidden-view.com`. That must
    be a line in `.env`, not an edit here.

    Two halves. The From is read at CALL time, so a restart applies a new one with no rebuild — and
    the Message-ID domain follows it, which matters because a Message-ID whose domain disagrees
    with the sender is a spam signal. And `email_digest` — the module that decides what the mail
    SAYS — never learns what address it goes out from, so the swap cannot reach the copy."""
    monkeypatch.setenv("RWE_SMTP_HOST", "smtp.example.test")
    seen = {}
    for addr in ("Beta <beta@example.com>", "Hidden View <digest@hidden-view.com>"):
        monkeypatch.setenv("RWE_EMAIL_FROM", addr)
        sender = email_sender.sender_from_env()
        msg = email_sender.build_message(to="r@example.com", subject="s", text="t", html="<p>h</p>",
                                         sender=sender.sender)
        seen[addr] = (msg["From"], msg["Message-ID"].rsplit("@", 1)[-1].rstrip(">"))
    assert seen["Beta <beta@example.com>"] == ("Beta <beta@example.com>", "example.com")
    assert seen["Hidden View <digest@hidden-view.com>"] == (
        "Hidden View <digest@hidden-view.com>", "hidden-view.com")

    # The rendering module is address-free. Checked on the CODE, with comments and the docstring
    # stripped: the docstring legitimately uses the word "sender" in prose, and a guard that reads
    # prose is a guard that fails for the wrong reason.
    import ast
    tree = ast.parse(pathlib.Path(ROOT / "examples" / "email_digest.py").read_text(encoding="utf-8"))
    tree.body = [n for n in tree.body
                 if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    code = ast.unparse(tree).lower()
    for token in ("rwe_email_from", "hidden-view", "@gmail", "sender"):
        assert token not in code, f"email_digest.py hardcodes {token!r} — the swap would reach it"


def test_an_unset_allowlist_clears_nobody(st, monkeypatch):
    """The one gate here that fails closed by being ABSENT rather than by being false.

    Consent answers "do I want this" and belongs to the reader. The allowlist answers "is this
    deployment cleared to write to real people yet", which is an operator's question — and during
    a beta the answer is a short list. If unset meant "everyone", a `.env` that lost a line would
    quietly start mailing the whole user base, which is the one mistake this feature cannot take
    back."""
    uid = _reader(st)
    _digest_notification(st, uid)
    monkeypatch.delenv("RWE_EMAIL_ALLOWLIST", raising=False)

    sender = FakeSender()
    stats = email_delivery.run_once(st, sender=sender)
    assert sender.sent == [] and stats.skipped["allowlist-empty"] == 1
    assert stats.considered == 0, "it should not even scan — there is nothing it could send"


def test_only_listed_recipients_are_mailed(st, monkeypatch):
    """Beta: the tester is mailed, everyone else is passed over however they set their toggles."""
    tester = _reader(st, email="tester@example.com")
    other = _reader(st, email="someone.else@example.com")
    _digest_notification(st, tester)
    _digest_notification(st, other)
    monkeypatch.setenv("RWE_EMAIL_ALLOWLIST", "tester@example.com")

    sender = FakeSender()
    stats = email_delivery.run_once(st, sender=sender)
    assert [m["To"] for m in sender.sent] == ["tester@example.com"]
    assert stats.skipped["not-in-allowlist"] == 1


@pytest.mark.parametrize("entry,address,cleared", [
    ("me@example.com", "me@example.com", True),
    ("me@example.com", "ME@Example.COM", True),          # case is not identity for a mailbox
    ("  me@example.com , b@x.test ", "b@x.test", True),  # whitespace around list entries
    ("me@example.com", "notme@example.com", False),
    ("@hidden-view.com", "anyone@hidden-view.com", True),   # a whole domain, for the later move
    ("@hidden-view.com", "anyone@hidden-view.com.evil.test", False),   # suffix, not substring
    ("@hidden-view.com", "anyone@example.com", False),
    ("me@example.com", "", False),
    ("me@example.com", "not-an-address", False),
])
def test_allowlist_matching(entry, address, cleared, monkeypatch):
    monkeypatch.setenv("RWE_EMAIL_ALLOWLIST", entry)
    assert email_delivery.allowed_recipient(address, email_delivery.allowlist()) is cleared


def test_general_delivery_takes_a_deliberate_sentinel(monkeypatch):
    """`*` and nothing else. The empty string is what a half-edited .env produces, and it must not
    be the value that opens the gate."""
    monkeypatch.setenv("RWE_EMAIL_ALLOWLIST", email_delivery.ALLOW_ALL)
    assert email_delivery.allowlist() is None
    assert email_delivery.allowed_recipient("anyone@example.com", None) is True
    for shut in ("", "   ", "all", "true", "1"):
        monkeypatch.setenv("RWE_EMAIL_ALLOWLIST", shut)
        assert email_delivery.allowlist() != None, f"{shut!r} must not mean everyone"  # noqa: E711


def test_the_allowlist_does_not_write_off_the_backlog(st, monkeypatch):
    """Being outside the list is our configuration, not the reader's choice — so widening it must
    let the waiting digest go out, not find it already marked failed."""
    uid = _reader(st, email="later@example.com")
    _digest_notification(st, uid)
    monkeypatch.setenv("RWE_EMAIL_ALLOWLIST", "someone@example.com")
    assert email_delivery.run_once(st, sender=FakeSender()).skipped["not-in-allowlist"] == 1

    monkeypatch.setenv("RWE_EMAIL_ALLOWLIST", "someone@example.com,later@example.com")
    assert email_delivery.run_once(st, sender=FakeSender()).sent == 1


def test_the_allowlist_cannot_override_consent(st, monkeypatch):
    """The gates are ANDed, and in the direction that matters: being on the list is permission from
    the operator, never from the reader. A tester who has not opted in is still not mailed."""
    uid = _reader(st, email="tester@example.com", channel=False)
    _digest_notification(st, uid)
    monkeypatch.setenv("RWE_EMAIL_ALLOWLIST", "tester@example.com")
    stats = email_delivery.run_once(st, sender=FakeSender())
    assert stats.sent == 0 and stats.skipped["email-channel-off"] == 1


def test_a_missing_or_malformed_address_is_never_mailed():
    prefs = settings_service.normalize_settings(None, {
        "notifications": {"weeklyDigest": True, "categories": {"digests": {"email": True}}}})
    for bad in ("", "   ", "not-an-email", "@example.com", "reader@"):
        assert email_consent.may_email_digest(prefs, bad) == "no-address"


def test_a_suppressed_address_is_never_mailed_again():
    prefs = settings_service.normalize_settings(None, {
        "notifications": {"weeklyDigest": True, "categories": {"digests": {"email": True}}}})
    assert email_consent.may_email_digest(prefs, "r@example.com", suppressed=True) == "suppressed"


# --------------------------------------------------------------------------- #
# Unsubscribe — it must work with no session, and authorise nothing else.
# --------------------------------------------------------------------------- #
def test_an_unsubscribe_token_round_trips():
    token = email_consent.make_token(42)
    assert email_consent.verify_token(token) == 42


def test_a_tampered_token_is_refused():
    token = email_consent.make_token(42)
    uid, purpose, sig = token.split(".")
    assert email_consent.verify_token(f"99.{purpose}.{sig}") is None, "cannot retarget another user"
    assert email_consent.verify_token(f"{uid}.{purpose}.{'A' * len(sig)}") is None
    assert email_consent.verify_token("") is None
    assert email_consent.verify_token("garbage") is None


def test_a_token_is_scoped_to_one_purpose():
    """A digest unsubscribe link must not silence a different category if one is added later."""
    token = email_consent.make_token(42, purpose="digest")
    assert email_consent.verify_token(token, purpose="breaking") is None


def test_no_secret_means_no_token_and_therefore_no_mail(monkeypatch):
    monkeypatch.delenv("RWE_EMAIL_SECRET", raising=False)
    assert email_consent.make_token(1) == ""
    assert email_consent.unsubscribe_url(1, "https://x.test") == ""


def test_unsubscribing_turns_the_channel_and_the_digest_off(st):
    uid = _reader(st)
    token = email_consent.make_token(uid)
    assert email_delivery.unsubscribe(st, token) == uid
    prefs = settings_service.get(st, uid)
    assert prefs["notifications"]["categories"]["digests"]["email"] is False
    assert prefs["notifications"]["weeklyDigest"] is False
    # The in-app inbox is the product, not a mailing: unsubscribing from email must not delete it.
    assert prefs["notifications"]["categories"]["digests"]["inApp"] is True


def test_a_bad_token_changes_nothing(st):
    uid = _reader(st)
    assert email_delivery.unsubscribe(st, "1.digest.forged") is None
    assert settings_service.get(st, uid)["notifications"]["weeklyDigest"] is True


# --------------------------------------------------------------------------- #
# Content — localized, and never disagreeing with the in-app card.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lang", ["en", "es", "fr", "de", "pt"])
def test_every_supported_language_renders_real_copy(lang):
    out = email_digest.render({"reads": 12, "streakDays": 3, "overall": 69}, lang=lang,
                              base_url="https://x.test", unsubscribe="https://x.test/u?t=1")
    assert out["subject"] and "{" not in out["subject"], "no unfilled placeholder reaches a reader"
    assert "12" in out["text"] and "12" in out["html"]
    for key in ("greeting", "why", "unsubscribe"):
        assert email_digest.strings(lang)[key] in out["text"]


def test_an_unknown_language_falls_back_to_english_not_to_a_key_name():
    out = email_digest.render({"reads": 1}, lang="qq")
    assert out["subject"] == email_digest.strings("en")["subject"].format(reads=1)


def test_the_numbers_come_from_the_notification_payload():
    """The email and the in-app card must never disagree about the same week, so the mail lifts the
    card's own payload rather than recomputing anything."""
    out = email_digest.render({"reads": 7, "streakDays": 2, "overall": 55}, lang="en")
    assert "7" in out["text"] and "55/100" in out["text"]


def test_a_week_with_no_reads_still_renders_a_sane_subject():
    out = email_digest.render({}, lang="en")
    assert out["subject"] == email_digest.strings("en")["subject_none"]
    assert "{" not in out["subject"]


def test_payload_values_are_escaped_into_the_html():
    """Integers today — but a template that is only accidentally safe becomes an injection the
    first time someone adds a topic name to the payload."""
    out = email_digest.render({"reads": "<script>alert(1)</script>"}, lang="en")
    assert "<script>" not in out["html"]


def test_the_mail_is_multipart_with_a_real_text_part():
    """The text part is what a screen reader, a text client and every spam filter reads."""
    msg = email_sender.build_message(to="r@example.com", subject="s", text="plain body",
                                     html="<p>rich</p>", sender="Hidden View <d@x.test>")
    assert msg.is_multipart()
    types = {p.get_content_type() for p in msg.walk() if p.get_content_maintype() == "text"}
    assert types == {"text/plain", "text/html"}
    assert "plain body" in msg.get_body(("plain",)).get_content()


# --------------------------------------------------------------------------- #
# Transport — what happened decides whether we ever write to this address again.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("code,status", [
    (250, "sent"), (200, "sent"),
    (421, "retry"), (450, "retry"), (451, "retry"), (452, "retry"),
    (550, "bounced"), (551, "bounced"), (552, "bounced"), (553, "bounced"), (554, "bounced"),
    (None, "retry"),
])
def test_smtp_codes_classify_into_retry_or_bounce(code, status):
    assert email_sender.classify_code(code) == status


def test_an_unrecognised_outcome_retries_rather_than_suppressing():
    """Suppressing a live reader on an outcome we do not understand is the costlier mistake."""
    assert email_sender.classify_code(999) == "retry"


@pytest.mark.parametrize("code", [500, 501, 502, 503, 504])
def test_a_protocol_error_never_suppresses_the_recipient(code):
    """500-504 are about OUR conversation with the relay — bad syntax, bad sequence, a command the
    server does not implement. A blanket "5xx means permanent" reads them as a dead mailbox and
    bans a reader who was never written to, forever, over a bug at our end."""
    assert email_sender.classify_code(code) == "retry", f"{code} is our problem, not their mailbox"


def test_a_rejected_SENDER_does_not_suppress_the_RECIPIENT():
    """The failure that would have banned an entire user base on deploy day.

    An unverified From domain — or an SES account still in the sandbox — makes the relay answer
    553 at MAIL FROM. Classified by code alone that is "no such mailbox", so the FIRST run would
    have suppressed every reader it tried, permanently, and a later fix to DNS would not bring any
    of them back: the suppression list is keyed by address and nothing clears it."""
    class SMTPSenderRefused(Exception):
        smtp_code = 553
        smtp_error = b"Email address is not verified"

    result = email_sender.classify_exception(SMTPSenderRefused("553 not verified"))
    assert result.retryable, "our sender, our problem — retry"
    assert not result.bounced, "must never reach store.suppress_email"


def test_a_network_failure_is_the_network_not_the_recipient():
    assert email_sender.classify_exception(TimeoutError("timed out")).retryable
    assert email_sender.classify_exception(ConnectionResetError("reset")).retryable


def test_the_sender_is_absent_unless_fully_configured(monkeypatch):
    monkeypatch.delenv("RWE_SMTP_HOST", raising=False)
    monkeypatch.delenv("RWE_EMAIL_FROM", raising=False)
    assert email_sender.sender_from_env() is None, "a half-configured deploy must not mail anyone"
    monkeypatch.setenv("RWE_EMAIL_ENABLED", "0")
    monkeypatch.setenv("RWE_SMTP_HOST", "smtp.test")
    monkeypatch.setenv("RWE_EMAIL_FROM", "d@x.test")
    assert email_sender.sender_from_env() is None, "the switch is off"


# --------------------------------------------------------------------------- #
# Delivery — the run, and everything that must not happen twice.
# --------------------------------------------------------------------------- #
def test_a_consenting_reader_is_mailed_once_per_week(st):
    uid = _reader(st)
    _digest_notification(st, uid)
    sender = FakeSender()

    first = email_delivery.run_once(st, sender=sender)
    assert first.sent == 1 and len(sender.sent) == 1

    # THE property: the worker runs on a schedule, and every run passes over the same notification.
    second = email_delivery.run_once(st, sender=sender)
    assert second.sent == 0, "a second pass must not mail the same week again"
    assert len(sender.sent) == 1


def test_the_mail_carries_a_working_unsubscribe_and_one_click_headers(st):
    uid = _reader(st)
    _digest_notification(st, uid)
    sender = FakeSender()
    email_delivery.run_once(st, sender=sender)
    msg = sender.sent[0]
    assert msg["List-Unsubscribe"], "RFC 8058: the mail client shows its own unsubscribe control"
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    token = msg["List-Unsubscribe"].split("t=")[1].rstrip(">")
    assert email_consent.verify_token(token) == uid, "the link in the mail must actually work"


def test_a_reader_who_never_opted_in_is_not_mailed(st):
    uid = _reader(st, channel=False)
    _digest_notification(st, uid)
    sender = FakeSender()
    stats = email_delivery.run_once(st, sender=sender)
    assert stats.sent == 0 and sender.sent == []
    assert stats.skipped["email-channel-off"] == 1


def test_unsubscribing_between_materialisation_and_send_is_honoured(st):
    """Consent is checked at SEND time, not when the notification was created — the gap between
    the two is exactly when someone clicks unsubscribe."""
    uid = _reader(st)
    _digest_notification(st, uid)
    email_delivery.unsubscribe(st, email_consent.make_token(uid))
    sender = FakeSender()
    stats = email_delivery.run_once(st, sender=sender)
    assert sender.sent == [] and stats.sent == 0


def test_a_hard_bounce_suppresses_the_address_forever(st):
    uid = _reader(st, email="gone@example.com")
    _digest_notification(st, uid)
    sender = FakeSender(email_sender.SendResult("bounced", code=550, detail="no such user"))
    stats = email_delivery.run_once(st, sender=sender)
    assert stats.bounced == 1
    assert st.email_suppressed("gone@example.com")

    # A later week must not try again: retrying a dead mailbox is what costs sender reputation.
    _digest_notification(st, uid, week="2026-W35")
    again = email_delivery.run_once(st, sender=FakeSender())
    assert again.sent == 0 and again.skipped["suppressed"] == 1


def test_a_soft_failure_is_retried_later_not_dropped(st):
    uid = _reader(st)
    _digest_notification(st, uid)
    now = datetime.now(timezone.utc)
    stats = email_delivery.run_once(
        st, now=now, sender=FakeSender(email_sender.SendResult("retry", code=451)))
    assert stats.retried == 1 and stats.sent == 0

    # Not due yet: a backoff that ignores its own schedule is not a backoff.
    assert email_delivery.run_once(st, now=now + timedelta(minutes=1),
                                   sender=FakeSender()).sent == 0
    # Due: the same notification is sent, on the delivery row already claimed for it.
    later = email_delivery.run_once(st, now=now + timedelta(hours=2), sender=FakeSender())
    assert later.sent == 1


def test_opting_in_does_not_mail_the_readers_entire_history(st):
    """Measured before it was fixed: **30 emails in one pass**, to one reader, on the day they
    ticked the box.

    "A digest with no email delivery row" is not the same as "new work". For every reader with the
    channel off — which is every reader, by default — each week's digest accumulates a row that
    will never have a delivery. Unbounded, the first person to opt in gets all of it at once, and
    so does anyone who unsubscribes and later returns. `MAX_AGE` is what makes the scan mean
    "this week's digest" rather than "everything ever"."""
    uid = _reader(st)
    now = datetime.now(timezone.utc)
    for week in range(1, 31):                            # 30 weeks, oldest first
        _digest_notification(st, uid, week=f"2026-W{week:02d}",
                             recorded_at=now - timedelta(weeks=31 - week))

    sender = FakeSender()
    stats = email_delivery.run_once(st, now=now, sender=sender)
    assert len(sender.sent) == 1, f"one week's digest, not a backlog: {stats.as_dict()}"

    # And the ones it passed over are passed over for good — not queued up for the next run.
    assert email_delivery.run_once(st, now=now + timedelta(hours=1),
                                   sender=FakeSender()).sent == 0


def test_a_recycled_push_subscription_id_cannot_erase_email_deliveries(st):
    """The two channels share one ledger, so one channel's cleanup must not reach the other's rows.

    `_discard_inherited_deliveries` exists because SQLite reuses rowids: a device pruned by a 410
    and then re-subscribed gets an id the ledger already thinks was delivered to. That is a fact
    about `push_subscriptions`. Email has no subscriptions — it stores a fixed sentinel in that
    column — so an unscoped delete is one id collision away from erasing every email delivery
    record, and with it the idempotency that stops a reader being mailed twice."""
    uid = _reader(st)
    _digest_notification(st, uid)
    assert email_delivery.run_once(st, sender=FakeSender()).sent == 1

    with st.session() as s:
        deleted = st._discard_inherited_deliveries(s, email_delivery.ACCOUNT_DESTINATION)
    assert deleted == 0, "a push-subscription sweep reached the email channel's rows"

    # Still remembered as delivered, so the next pass does not mail the same week again.
    assert email_delivery.run_once(st, sender=FakeSender()).sent == 0


def test_a_digest_older_than_the_window_is_never_mailed(st):
    """"Here is your reading week" about a month nobody remembers is not a digest, it is a
    surprise. The window is the age bound, so a digest that missed it stays missed."""
    uid = _reader(st)
    now = datetime.now(timezone.utc)
    _digest_notification(st, uid, week="2026-W20",
                         recorded_at=now - email_delivery.MAX_AGE - timedelta(hours=1))
    assert email_delivery.run_once(st, now=now, sender=FakeSender()).considered == 0


def test_unsubscribing_mid_retry_closes_the_delivery_instead_of_re_leasing_it(st):
    """A soft failure, then the reader unsubscribes. The pending delivery must be CLOSED on the
    next pass, not declined-and-left-open three times over.

    `_retry_job` takes the row's lease before consent is consulted, so a bare skip leaves it due
    again: the following run leases it again, declines it again, and spends a slot of its budget
    each time until `attempts` hits the ceiling. The decision was made on the first pass; the
    ledger should say so on the first pass."""
    uid = _reader(st)
    _digest_notification(st, uid)
    now = datetime.now(timezone.utc)
    assert email_delivery.run_once(
        st, now=now, sender=FakeSender(email_sender.SendResult("retry", code=451))).retried == 1

    email_delivery.unsubscribe(st, email_consent.make_token(uid))

    due = now + timedelta(hours=2)
    first = email_delivery.run_once(st, now=due, sender=FakeSender())
    assert first.sent == 0 and first.skipped["digest-off"] == 1

    # The row is settled, so a later pass has nothing left to consider at all — the proof that it
    # was closed rather than merely passed over.
    second = email_delivery.run_once(st, now=due + timedelta(hours=12), sender=FakeSender())
    assert second.considered == 0, f"delivery re-leased after being declined: {second.as_dict()}"


def test_a_broken_run_leaves_the_backlog_open_rather_than_writing_it_off(st, monkeypatch):
    """The other half of the rule: only decisions ABOUT THE READER close a delivery.

    With no `RWE_PUBLIC_URL` there is no unsubscribe link, so nothing is sent — but that is our
    misconfiguration, not the reader's choice. Setting the variable must let the backlog go out,
    not find every delivery already marked failed."""
    uid = _reader(st)
    _digest_notification(st, uid)
    now = datetime.now(timezone.utc)
    assert email_delivery.run_once(
        st, now=now, sender=FakeSender(email_sender.SendResult("retry", code=451))).retried == 1

    monkeypatch.setenv("RWE_PUBLIC_URL", "")
    blocked = email_delivery.run_once(st, now=now + timedelta(hours=2), sender=FakeSender())
    assert blocked.sent == 0 and blocked.skipped["no-unsubscribe-url"] == 1

    monkeypatch.setenv("RWE_PUBLIC_URL", "https://hidden-view.com")
    healed = email_delivery.run_once(st, now=now + timedelta(hours=12), sender=FakeSender())
    assert healed.sent == 1, f"the fix should drain the backlog: {healed.as_dict()}"


def test_the_run_does_nothing_when_email_is_not_configured(st, monkeypatch):
    uid = _reader(st)
    _digest_notification(st, uid)
    monkeypatch.setenv("RWE_EMAIL_ENABLED", "0")
    stats = email_delivery.run_once(st)          # no sender injected: reads the env
    assert stats.sent == 0 and stats.skipped["not-configured"] == 1


def test_without_a_signing_secret_nothing_is_sent(st, monkeypatch):
    """Mail a reader cannot escape is worse than mail they never got."""
    uid = _reader(st)
    _digest_notification(st, uid)
    monkeypatch.delenv("RWE_EMAIL_SECRET", raising=False)
    sender = FakeSender()
    stats = email_delivery.run_once(st, sender=sender)
    assert sender.sent == [] and stats.skipped["no-unsubscribe-secret"] == 1


def test_one_bad_reader_does_not_stop_the_run(st):
    """A daemon-thread worker that raises dies silently, taking everyone behind it."""
    bad = _reader(st, email="")                   # no address at all
    good = _reader(st, email="good@example.com")
    _digest_notification(st, bad)
    _digest_notification(st, good)
    sender = FakeSender()
    stats = email_delivery.run_once(st, sender=sender)
    assert stats.sent == 1 and len(sender.sent) == 1
    assert stats.skipped["no-address"] == 1


def test_the_in_app_digest_is_untouched_by_the_email_channel(st):
    """The inbox is the product. Mailing must not consume, mark, or delete the notification."""
    uid = _reader(st)
    _digest_notification(st, uid)
    before = st.list_notifications(uid)
    email_delivery.run_once(st, sender=FakeSender())
    after = st.list_notifications(uid)
    assert [n["id"] for n in before] == [n["id"] for n in after]
    assert all(n.get("seenAt") is None for n in after), "an email must not mark the card as seen"


# --------------------------------------------------------------------------- #
# Against a REAL socket. Everything above injects a fake relay, so nothing above
# exercises smtplib itself — the layer where a malformed message, a header the
# library rejects, or a TLS policy that does not hold would actually show up.
# --------------------------------------------------------------------------- #
class _Relay(threading.Thread):
    """A minimal SMTP server on localhost. Stdlib only (asyncio streams); `smtpd` was removed in
    3.12 and `aiosmtpd` is a dependency this suite does not need.

    `advertise_tls` is the interesting knob: with it off the server offers no STARTTLS, which is
    exactly the production relay we must refuse rather than fall back to plaintext."""

    def __init__(self, advertise_tls: bool = False):
        super().__init__(daemon=True)
        self.advertise_tls, self.received, self.port = advertise_tls, [], None
        self._ready = threading.Event()

    def run(self):
        import asyncio
        relay = self

        class Proto(asyncio.Protocol):
            def connection_made(self, t):
                self.t, self.buf, self.data, self.in_data = t, b"", [], False
                t.write(b"220 localhost ESMTP\r\n")

            def data_received(self, chunk):
                self.buf += chunk
                while b"\r\n" in self.buf:
                    line, self.buf = self.buf.split(b"\r\n", 1)
                    self._line(line)

            def _line(self, line):
                if self.in_data:
                    if line == b".":
                        self.in_data = False
                        relay.received.append(b"\r\n".join(self.data).decode("utf-8", "replace"))
                        self.t.write(b"250 OK queued\r\n")
                    else:
                        self.data.append(line[1:] if line.startswith(b"..") else line)
                    return
                cmd = line.upper()
                if cmd.startswith((b"EHLO", b"HELO")):
                    ext = b"250-STARTTLS\r\n" if relay.advertise_tls else b""
                    self.t.write(b"250-localhost\r\n" + ext + b"250 HELP\r\n")
                elif cmd.startswith(b"DATA"):
                    self.in_data = True
                    self.t.write(b"354 go ahead\r\n")
                elif cmd.startswith(b"QUIT"):
                    self.t.write(b"221 bye\r\n")
                    self.t.close()
                else:                                   # MAIL FROM, RCPT TO, RSET, NOOP
                    self.t.write(b"250 OK\r\n")

        async def main():
            loop = asyncio.get_running_loop()
            server = await loop.create_server(Proto, "127.0.0.1", 0)
            relay.port = server.sockets[0].getsockname()[1]
            relay._ready.set()
            async with server:
                await asyncio.sleep(30)

        asyncio.run(main())

    def start_and_wait(self):
        self.start()
        assert self._ready.wait(5), "relay did not start"
        return self


def test_a_relay_offering_no_tls_is_refused_over_a_real_socket(st, monkeypatch):
    """The refusal that only a real SMTP conversation can prove.

    A reading history is not something to put on the wire in the clear, so a relay with no STARTTLS
    is refused rather than downgraded to plaintext. Every other test here injects a fake sender and
    would pass whatever `SmtpSender.send` actually did with a socket."""
    relay = _Relay(advertise_tls=False).start_and_wait()
    monkeypatch.setenv("RWE_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("RWE_SMTP_PORT", str(relay.port))
    monkeypatch.setenv("RWE_EMAIL_FROM", "Hidden View <beta@example.test>")

    uid = _reader(st, email="tester@example.com")
    _digest_notification(st, uid)
    stats = email_delivery.run_once(st)              # no injected sender: the real SmtpSender
    assert stats.sent == 0
    assert stats.skipped["no-tls"] == 1, f"expected a named TLS refusal: {stats.as_dict()}"
    assert relay.received == [], "a message reached the wire despite no TLS"


def test_the_allowlist_filters_before_the_wire(st, monkeypatch):
    """An address outside the list must never reach a socket at all — not be sent and discarded,
    and not appear in a relay's logs. Proven against a server that records everything it is
    handed."""
    relay = _Relay(advertise_tls=False).start_and_wait()
    monkeypatch.setenv("RWE_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("RWE_SMTP_PORT", str(relay.port))
    monkeypatch.setenv("RWE_EMAIL_FROM", "Hidden View <beta@example.test>")
    monkeypatch.setenv("RWE_EMAIL_ALLOWLIST", "tester@example.com")

    _digest_notification(st, _reader(st, email="stranger@example.com"))
    stats = email_delivery.run_once(st)
    assert stats.skipped["not-in-allowlist"] == 1
    assert stats.skipped.get("no-tls", 0) == 0, "it should not have got as far as the transport"
    assert relay.received == []


# --------------------------------------------------------------------------- #
# Preflight — the operator's "is my configuration wrong, or has nobody opted in?"
# --------------------------------------------------------------------------- #
def test_the_preflight_dials_the_same_way_a_send_does(st, monkeypatch):
    """`verify` shares `_dial` with `send`, and that sharing is the whole value.

    A preflight that connected differently from a real send would be a green light with no wiring
    behind it — the most expensive kind of check, because it is trusted. Asserted against a relay
    with no STARTTLS: the check must refuse for the same reason, and with the same status, that a
    send refuses."""
    relay = _Relay(advertise_tls=False).start_and_wait()
    monkeypatch.setenv("RWE_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("RWE_SMTP_PORT", str(relay.port))
    monkeypatch.setenv("RWE_EMAIL_FROM", "Hidden View <beta@example.test>")
    sender = email_sender.sender_from_env()

    checked = sender.verify()
    sent = sender.send(email_sender.build_message(to="r@example.com", subject="s", text="t",
                                                  html="<p>h</p>", sender=sender.sender))
    assert checked.status == sent.status == "no-tls"
    assert relay.received == [], "the preflight must not put a message on the wire"


def test_the_preflight_never_puts_a_message_on_the_wire(monkeypatch):
    """A check is not a send, and against a healthy relay that distinction stops being obvious.

    Driven through a spy on `_dial` rather than a live TLS relay: the TLS policy is unconditional
    by design, so a plaintext local server can only ever exercise the refusal path, and giving the
    suite a certificate the client is told to trust would mean putting a seam into exactly the code
    that must not have one."""
    dialled, sent = [], []

    class _Client:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def noop(self): return (250, b"OK")
        def send_message(self, msg): sent.append(msg)

    sender = email_sender.SmtpSender(host="relay.example.test", port=587, sender="a@b.test")
    monkeypatch.setattr(type(sender), "_dial", lambda self: dialled.append(1) or _Client())

    assert sender.verify().ok
    assert dialled == [1], "verify must actually dial — the same way a send does"
    assert sent == [], "verify must not hand the relay a message"

    sender.send(email_sender.build_message(to="r@example.com", subject="s", text="t",
                                           html="<p>h</p>", sender=sender.sender))
    assert len(dialled) == 2 and len(sent) == 1, "send uses the same dial, and does send"


@pytest.mark.parametrize("status,detail,expected", [
    ("no-tls", "relay offers no TLS", "587 for STARTTLS"),
    ("retry", "auth failed", "App Password"),
    ("retry", "535 5.7.8 Username and Password not accepted", "App Password"),
    ("retry", "sender refused: 553 5.7.1 not verified", "RWE_EMAIL_FROM"),
    ("retry", "gaierror: Name or service not known", "RWE_SMTP_HOST"),
    ("retry", "something nobody has seen", ""),
])
def test_the_preflight_names_the_thing_to_change(status, detail, expected):
    """An error string is not a diagnosis. Each of these failures has a different cause in a
    different place — the account's 2FA settings, a DNS record, a security group — and an operator
    reading `535` should not have to know which.

    Tested as pure routing because the real failures cannot be reproduced against a local relay:
    a wrong password needs a TLS handshake to get as far as AUTH, and that needs a certificate this
    suite has no business trusting."""
    import email_preflight
    hint = email_preflight.diagnose(email_sender.SendResult(status, detail=detail))
    assert expected in hint if expected else hint == ""
