# Weekly Digest Email — provider setup, turning it on, and operations

**Status:** shipped, OFF in production (`RWE_EMAIL_ENABLED=0`).
**Scope:** the weekly digest, delivered as email, alongside the in-app card that already exists.

The in-app digest is unchanged by everything in this document. Email is a second **channel** for a
notification that already existed; it adds no notification kind, and turning it off returns the
product exactly to its previous behaviour.

---

## 1. Why SMTP and not a provider SDK

The engine speaks plain `smtplib`. Every mail service worth using — Amazon SES, Postmark, SendGrid,
Mailgun, a self-hosted relay — accepts SMTP, so **choosing a provider is a change to `deploy/.env`,
not a change to code**. It also keeps a large dependency out of an image that deliberately imports
`pywebpush` lazily so its absence cannot break the test suite; `botocore` alone is tens of megabytes
to gain nothing this needs.

The interesting part of a mail transport is not sending. It is classifying what happened, because
that decides whether a reader is written to again or never again — see §6.

---

## 2. Configuration

Every variable is listed in `deploy/docker-compose.yml` on the `api` service. That file has no
`env_file:`, so a variable absent from the compose allowlist never reaches the container whatever
`deploy/.env` says — an unlisted flag can be neither enabled nor rolled back.

| Variable | Meaning |
|---|---|
| `RWE_EMAIL_ENABLED` | The switch. Off ⇒ the worker does nothing at all. |
| `RWE_SMTP_HOST` / `RWE_SMTP_PORT` | The relay. 587 negotiates STARTTLS; 465 is implicit TLS. |
| `RWE_SMTP_USER` / `RWE_SMTP_PASSWORD` | Credentials, when the relay wants them. |
| `RWE_EMAIL_FROM` | The From address, e.g. `Hidden View <digest@hidden-view.com>`. |
| `RWE_EMAIL_SECRET` | Signs unsubscribe tokens. **Without it nothing is sent.** |
| `RWE_PUBLIC_URL` | Base for the report and unsubscribe links, e.g. `https://hidden-view.com`. |
| `RWE_EMAIL_MAX_PER_RUN` | Cap per pass (default 500). |
| `RWE_EMAIL_RETRY_MAX` | Attempts before a delivery is abandoned (default 3). |

Two of these are refusals rather than settings:

* **No `RWE_EMAIL_SECRET` ⇒ no mail.** A digest with a dead unsubscribe link is worse than a digest
  that never arrived: the reader's only remaining control is "report spam", which costs
  deliverability for everyone else. The worker logs `email_no_secret` and sends nothing.
* **A relay with no TLS ⇒ no mail.** If STARTTLS is unavailable the send is skipped rather than
  transmitted in the clear; a reading history is not something to put on the wire unencrypted.

---

## 3. What only you can do

Nothing below can be done from the repository. All of it is account and DNS work.

1. **Create SMTP credentials** with a provider. For SES that is *SMTP settings → Create SMTP
   credentials* — note that SES SMTP credentials are **not** your AWS access keys, and the host is
   `email-smtp.<region>.amazonaws.com`.
2. **Verify the sender domain and publish SPF + DKIM** (and ideally DMARC) for it. Unverified mail is
   rejected outright or filed as spam; both look identical to "the feature is broken" from inside the
   app.
3. **Leave the provider's sandbox.** A new SES account can only mail *verified* addresses. In the
   sandbox the relay answers `553` at `MAIL FROM`, which the transport reports as
   `email_sender_refused` and retries — deliberately, so a sandbox misconfiguration cannot suppress
   your readers (§6).
4. **Install the cron line** (§5).

---

## 4. Turning it on

Set the variables in `deploy/.env`, then:

```bash
cd /opt/ih
sudo bash deploy/ops/update.sh claude/sleepy-gates-oecof1
```

Verify from inside the container — the AWS override unpublishes port 8000, so a `curl` on the host
connects to nothing and prints nothing at all, which reads as an empty response rather than as no
listener:

```bash
source deploy/ops/_compose.sh
dc exec -T api python -c "import email_sender, email_consent; \
print('relay:', bool(email_sender.sender_from_env()), 'secret:', bool(email_consent.secret()))"
```

Expect `relay: True secret: True`. Then a dry pass — with the channel off for every reader, this
sends nothing and tells you why:

```bash
dc exec -T -e IH_AUTH="$(grep -m1 '^RWE_INTERNAL_SECRET=' deploy/.env | cut -d= -f2-)" \
  api python examples/email_run.py
```

Expect something like `{"bounced": 0, "considered": 0, "retried": 0, "sent": 0, "skipped":
{"email-channel-off": 12}}`. That is success: twelve readers have a digest and none has opted in.

**Nobody is mailed until they opt in.** `notifications.categories.digests.email` defaults to `false`
for every existing and new account. The switch is in Settings → Notifications, nested under the
weekly digest.

---

## 5. The schedule — hourly cron, for a weekly email

```cron
17 * * * * ubuntu /opt/ih/deploy/ops/send-digest-emails.sh >> /var/log/ih-email.log 2>&1
```

Hourly is not a mistake. **The schedule is not this cron.** The weekly digest is a `cadence`
notification deduped on the ISO week (`weekly_digest:2026-W34`), materialised by the existing
evaluator, and `UNIQUE(notification_id, channel, subscription_id)` in the delivery ledger is what
guarantees one email per reader per week. This pass mails whatever exists and has not been mailed,
so:

* running it more often costs nothing and delivers sooner after a digest is materialised;
* running it late — a reboot, a deploy, a missed window — loses nothing, because the work is still
  there;
* a *weekly* cron gets exactly one chance per week, and a host that happens to be down at that
  minute silently skips a week for every reader.

It also drives the retry queue, which needs a cadence far shorter than a week for a backoff measured
in minutes and hours to mean anything.

### The age window

A pass only considers digests materialised in the **last 8 days** (`email_delivery.MAX_AGE`), and
that bound is what makes opting in safe. "A digest with no email delivery row" is not the same as
"new work": every reader with the channel off — which is every reader, by default — accumulates one
such row per week forever. Without the window, the first person to tick the box has their entire
notification history mailed in a single pass; measured at **30 emails for a 30-week account**,
arriving at once. The same applies to anyone who unsubscribes and later returns.

Eight rather than seven days so a run that is a day late still catches the current week, and so the
window outlasts the retry ladder's own reach. A digest older than that is never mailed, which is
correct: "here is your reading week" about a month nobody remembers is not a digest.

---

## 6. Outcomes — and the one that must not be got wrong

| Outcome | What the relay said | What happens |
|---|---|---|
| `sent` | 2xx | Ledger row `sent`. Done for the week. |
| `retry` | 4xx, a socket error, an unknown code | Ledger row stays `pending` with `next_attempt_at` set; 15 min → 1 h → 6 h. |
| `bounced` | **550, 551, 552, 553, 554** | Ledger row `failed`, and the address goes on the suppression list permanently. |

**Only the recipient-rejection family suppresses.** A blanket "5xx means permanent" would read a
`503 bad sequence of commands` — a bug in *our* SMTP conversation — as a dead mailbox, and would read
a `553` from a rejected *sender* as a dead *recipient*. That second one is the expensive failure: an
unverified From domain would have banned every reader on the first run, permanently, and fixing DNS
afterwards would not bring any of them back, because the suppression list is keyed by address and
nothing clears it. `SMTPSenderRefused` is therefore always a retry, logged as
`email_sender_refused` with the operator action spelled out.

Inspecting the list:

```bash
# Store() with no argument resolves RWE_DB_URL, or the default file — the same database the API
# is using. A read, so it does not contend with the server's write lock.
dc exec -T api python -c "import store, json; \
print(json.dumps(store.Store().list_email_suppressions(50), indent=2))"
```

---

## 7. Consent and unsubscribe

Two gates, both required, checked **at send time** rather than at materialisation — so a reader who
unsubscribes between the digest being created and the mail going out is not mailed:

* `notifications.weeklyDigest` — do I want a weekly digest at all;
* `notifications.categories.digests.email` — delivered where.

The flat toggle alone is deliberately **not** enough. It was ticked when the app was the only place
a notification could appear: it answers *whether*, never *where*, and treating it as consent to mail
is consent laundering. `gate_path` enforces this at the framework level — a kind with a flat
`setting_path` gates in-app only and fails closed on every other channel.

Unsubscribe is **unauthenticated on purpose**. The link carries an HMAC over `(purpose, user id)`:
it authorises exactly one category off and nothing else, cannot be forged without
`RWE_EMAIL_SECRET`, is compared in constant time, and **never expires** — a link in a two-year-old
email must still work, because the alternative is a reader who cannot make it stop. The endpoint
always answers `200` with a boolean; distinguishing "no such user" from "bad signature" would
enumerate users. `List-Unsubscribe` + `List-Unsubscribe-Post` (RFC 8058) mean most mail clients show
their own one-click control and the reader never has to find ours.

`/unsubscribe` and `/api/unsubscribe` sit **outside** the auth matcher in `web/middleware.ts`, and
`web/lib/unsubscribe-public.test.ts` fails if they ever fall inside it.

---

## 8. Rolling it back

```bash
# in deploy/.env
RWE_EMAIL_ENABLED=0
```

then `sudo bash deploy/ops/restart.sh api`. The worker becomes a no-op, the in-app digest continues
untouched, and readers' stored preferences are preserved — turning it back on resumes exactly where
it left off. No data is deleted, and the suppression list is not cleared (it should not be: those
addresses bounced).

---

## 9. Where the code lives

| File | Job |
|---|---|
| `examples/email_sender.py` | SMTP transport and outcome classification. No provider SDK. |
| `examples/email_consent.py` | Who may be written to; unsubscribe tokens. Reads the channel registry. |
| `examples/email_digest.py` | Subject, text and HTML in 5 languages. Composes; never measures. |
| `examples/email_delivery.py` | One pass: claim, send, resolve, retry. Never raises. |
| `examples/email_run.py` | Operator entry point; calls the API's internal route. |
| `deploy/ops/send-digest-emails.sh` | The cron wrapper. |
| `tests/test_email_digest.py` | The suite for all of the above. |

The numbers in the mail are **lifted from the notification payload the in-app card already carries**,
never recomputed — so the email and the inbox cannot disagree about the same week.
