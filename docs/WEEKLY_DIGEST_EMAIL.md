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
that decides whether a reader is written to again or never again — see §8.

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
| `RWE_EMAIL_ALLOWLIST` | Who may receive **at all**. Unset ⇒ nobody. `*` ⇒ everyone. |
| `RWE_PUBLIC_URL` | Base for the report and unsubscribe links, e.g. `https://hidden-view.com`. |
| `RWE_EMAIL_MAX_PER_RUN` | Cap per pass (default 500). |
| `RWE_EMAIL_RETRY_MAX` | Attempts before a delivery is abandoned (default 3). |

Three of these are refusals rather than settings:

* **No `RWE_EMAIL_SECRET` ⇒ no mail.** A digest with a dead unsubscribe link is worse than a digest
  that never arrived: the reader's only remaining control is "report spam", which costs
  deliverability for everyone else. The worker logs `email_no_secret` and sends nothing.
* **A relay with no TLS ⇒ no mail.** If STARTTLS is unavailable the send is skipped rather than
  transmitted in the clear; a reading history is not something to put on the wire unencrypted.
* **No `RWE_EMAIL_ALLOWLIST` ⇒ no mail, to anyone.** This is the one gate that fails closed by
  being *absent* rather than by being false, and the asymmetry is deliberate — see §3.

---

## 3. The recipient allowlist — and why unset means nobody

`RWE_EMAIL_ALLOWLIST` decides who may receive at all, whatever their settings say. It is an
**operator** gate, and it answers a different question from consent:

| | Question | Whose answer | Safe default |
|---|---|---|---|
| Consent (`digests.email`) | *Do I want this?* | the reader's | off |
| Allowlist | *Is this deployment cleared to write to real people yet?* | the operator's | **nobody** |

The two are ANDed, and in the direction that matters: being on the allowlist is permission from the
operator, never from the reader. A tester who has not opted in is still not mailed.

Unset means nobody because the failure to avoid is a `.env` that loses a line and quietly starts
mailing a live user base — the one mistake this feature cannot take back. Going general is
therefore a deliberate act:

```bash
RWE_EMAIL_ALLOWLIST=you@example.com              # beta: exactly one person
RWE_EMAIL_ALLOWLIST=you@example.com,qa@example.com   # a few
RWE_EMAIL_ALLOWLIST=@hidden-view.com             # a whole domain
RWE_EMAIL_ALLOWLIST='*'                          # general delivery — the only value that means it
```

Entries match case-insensitively, either as a full address or as an `@domain` suffix. Anything else
in the variable — an empty string, `all`, `true`, `1` — clears **nobody**, so a half-edited file
fails safe rather than open.

A run with the gate shut says so in one line rather than as a pile of per-reader skips:

```
send-digest-emails: {"considered": 0, ..., "skipped": {"allowlist-empty": 1}}
```

and one that is filtering says exactly who it passed over:

```
send-digest-emails: {"considered": 13, "sent": 1, ..., "skipped": {"not-in-allowlist": 12}}
```

Being outside the list never writes a delivery off. Widen the list and the waiting digests go out
on the next pass, as long as they are still inside the age window (§7).

---

## 4. Beta testing from a personal address

The engine speaks SMTP, so a personal Gmail is a valid relay for testing and needs no code change —
`RWE_EMAIL_FROM` is read at call time, and `examples/email_digest.py` (which decides what the mail
*says*) never learns what address it goes out from. Swapping in `digest@hidden-view.com` later is
one line in `deploy/.env` plus `deploy/ops/restart.sh api`;
`test_the_sender_can_be_swapped_without_touching_the_digest` fails if that ever stops being true.

Write the block with the script rather than by hand:

```bash
bash deploy/ops/configure-email.sh you@gmail.com          # --dry-run first, if you like
```

One address fills the whole block: it becomes the SMTP user, the From, and — by default — the only
address cleared to receive. It prompts for the app password (never an argument, so it stays out of
your shell history and the process list), strips Gmail's display spaces, generates
`RWE_EMAIL_SECRET`, quotes whatever needs quoting, and backs the file up first.

**It refuses to append a key that is already there.** That is not fussiness: Compose keeps only the
LAST occurrence of a key and silently ignores every earlier one, which is exactly how
`BETA_ALLOWLIST` lost the operator's own address and locked them out of their own beta
(2026-08-02). A re-run of the same paste looks additive and is destructive. Use `--replace` to
rewrite the keys in place; it preserves an existing `RWE_EMAIL_SECRET`, because rotating that would
break every unsubscribe link already sitting in someone's inbox.

For a different provider or general delivery:

```bash
bash deploy/ops/configure-email.sh digest@hidden-view.com --replace --allowlist '*' \
     --host email-smtp.eu-west-1.amazonaws.com --user AKIAXXXX --name 'Hidden View'
```

The block it writes, for reference:

```bash
# deploy/.env — beta
RWE_EMAIL_ENABLED=1
RWE_SMTP_HOST=smtp.gmail.com
RWE_SMTP_PORT=587
RWE_SMTP_USER=you@gmail.com
RWE_SMTP_PASSWORD="abcd efgh ijkl mnop"      # the 16-char app password, NOT your Google password
RWE_EMAIL_FROM="Hidden View <you@gmail.com>"
RWE_EMAIL_ALLOWLIST=you@gmail.com
RWE_EMAIL_SECRET="$(openssl rand -base64 32)"   # paste the OUTPUT; .env is not a shell script
RWE_PUBLIC_URL=https://hidden-view.com
```

**Quote any value containing a space or `#`.** `deploy/.env` is parsed, never sourced
(`deploy/ops/_compose.sh::env_val` greps it, and Compose has its own dotenv parser), so `<` and `>`
are safe unquoted — but an unquoted `#` is treated as the start of a comment, which would silently
truncate a display name or an app password. One layer of surrounding quotes is stripped by both
parsers, so quoting always round-trips.

Four things about Gmail specifically, each of which fails in a way that does not look like its
cause:

1. **An App Password is required**, and it requires 2-Step Verification on the account first
   (Google Account → Security → 2-Step Verification → App passwords). Your normal password is
   rejected — Google removed password auth for SMTP clients. The failure is
   `SMTPAuthenticationError`, which this worker logs as `email_smtp_auth_failed` and **retries**
   rather than treating as a bad recipient.
2. **`RWE_EMAIL_FROM` must be the Gmail account itself** (or an alias verified under *Settings →
   Accounts → Send mail as*). Gmail rewrites a From it does not recognise, so a mismatch does not
   error — it silently arrives from your Gmail address anyway, which is confusing when you are
   trying to test the From.
3. **Free Gmail allows roughly 500 messages a day.** Irrelevant at allowlist size, and a hard wall
   the moment the allowlist becomes `*`.
4. **Keep the allowlist narrow while the sender is a personal Gmail.** Product mail to third
   parties from a personal account has no SPF/DKIM alignment for your product domain, and bulk
   sending from a consumer account is against Gmail's policy. The allowlist is exactly the
   mitigation; general delivery is what the dedicated domain in §5 is for.

---

## 5. Moving to a dedicated sender (SES + `digest@hidden-view.com`)

The order matters: **authenticate the domain first, widen the allowlist last.** A sender with no
SPF/DKIM alignment that suddenly mails a whole user base is how a domain's reputation is spent
before it is earned, and reputation is not quickly recovered.

All of it is AWS and DNS work — nothing here can be done from the repository. Steps 1–5 must ALL be
finished before the cutover in step 6 has anything to point at; running it early writes placeholder
credentials over a working config (a five-minute recovery from the `.bak`, but an avoidable one).
Use **us-east-1**, the region the rest of the stack is in: SES identities, sandbox status and SMTP
endpoints are all per-region, so mixing regions means verifying twice and wondering why one of them
will not send.

> ### Where to run these
>
> **Steps 1–5: NOT on the EC2 box.** Use the AWS console, or a shell with your own admin
> credentials. The instance role (`ih-ec2-role`) has no SES permissions and **should not be given
> any**: it is scoped to what the running application needs, and the application never calls the SES
> API at all — it sends over SMTP with credentials from `deploy/.env`. Granting the instance
> `ses:CreateEmailIdentity` would let anything that compromised it verify domains and reconfigure
> your sending identity, in exchange for saving a one-time trip to the console. Attempting them on
> the box fails with `AccessDeniedException ... no identity-based policy allows the ses:* action`,
> which is the role behaving correctly rather than a misconfiguration to fix.
>
> **Step 6 onward: on the box.** The cutover only writes SMTP credentials into `deploy/.env`; it
> needs no AWS permissions whatsoever.

### 1. Verify the domain with Easy DKIM

Console: *SES → Identities → Create identity → Domain `hidden-view.com`*, Easy DKIM, RSA_2048, and
keep "Publish DNS records to Route 53" ticked if the zone is there — that writes the three CNAMEs
for you. Or:

```bash
aws sesv2 create-email-identity --region us-east-1 \
  --email-identity hidden-view.com \
  --dkim-signing-attributes NextSigningKeyLength=RSA_2048

aws sesv2 get-email-identity --region us-east-1 --email-identity hidden-view.com \
  --query 'DkimAttributes.Tokens' --output text
```

Each token becomes a CNAME `<token>._domainkey.hidden-view.com` → `<token>.dkim.amazonses.com`. On
Route 53, in one batch (substitute your zone id — `aws route53 list-hosted-zones-by-name --dns-name
hidden-view.com` — and the three tokens):

```bash
cat > /tmp/dkim.json <<'JSON'
{"Changes":[
 {"Action":"UPSERT","ResourceRecordSet":{"Name":"TOKEN1._domainkey.hidden-view.com","Type":"CNAME","TTL":300,"ResourceRecords":[{"Value":"TOKEN1.dkim.amazonses.com"}]}},
 {"Action":"UPSERT","ResourceRecordSet":{"Name":"TOKEN2._domainkey.hidden-view.com","Type":"CNAME","TTL":300,"ResourceRecords":[{"Value":"TOKEN2.dkim.amazonses.com"}]}},
 {"Action":"UPSERT","ResourceRecordSet":{"Name":"TOKEN3._domainkey.hidden-view.com","Type":"CNAME","TTL":300,"ResourceRecords":[{"Value":"TOKEN3.dkim.amazonses.com"}]}}
]}
JSON
aws route53 change-resource-record-sets --hosted-zone-id Z0123456789ABCD --change-batch file:///tmp/dkim.json
```

Then check rather than assume, and **do not continue until it is true** — everything after this step
fails in ways that look like other problems:

```bash
aws sesv2 get-email-identity --region us-east-1 --email-identity hidden-view.com \
  --query '{Sending:VerifiedForSendingStatus,Dkim:DkimAttributes.Status}'
```

You want `Sending: true`, `Dkim: SUCCESS`. Usually minutes; AWS allows itself up to 72 hours.

DKIM is what carries the alignment. With Easy DKIM the signature's `d=` is `hidden-view.com`, so
**DMARC passes on the DKIM leg** even though the envelope sender is Amazon's. That fact decides the
next step.

### 2. Custom MAIL FROM (recommended, not required)

```bash
aws sesv2 put-email-identity-mail-from-attributes --region us-east-1 \
  --email-identity hidden-view.com --mail-from-domain mail.hidden-view.com \
  --behavior-on-mx-failure USE_DEFAULT_VALUE
```

Two records on that subdomain:

| Name | Type | Value |
|---|---|---|
| `mail.hidden-view.com` | MX | `10 feedback-smtp.us-east-1.amazonses.com` |
| `mail.hidden-view.com` | TXT | `"v=spf1 include:amazonses.com ~all"` |

This aligns SPF as well as DKIM, and gives bounces somewhere of yours to land.

**On MX failure, fall back — do not reject.** An earlier draft of this document said the opposite,
on the reasoning that a silent fallback is a silent loss of alignment. That reasoning is wrong:
Easy DKIM (step 1) already aligns `d=hidden-view.com`, and DMARC passes if **either** leg aligns.
So a fallback costs the SPF leg while DMARC still passes, whereas `REJECT_MESSAGE` turns a DNS
lapse on a subdomain into every digest failing to send. The lesser failure is the right default.

### 3. DMARC

| Name | Type | Value |
|---|---|---|
| `_dmarc.hidden-view.com` | TXT | `"v=DMARC1; p=none; rua=mailto:yerram.saisanath@gmail.com"` |

`p=none` asks receivers to *report*, not to act. Move to `p=quarantine` once the reports show your
own mail passing — which is the point of starting at `none`. Publishing `p=reject` on day one is how
a misconfiguration becomes silent total failure.

Point `rua` at a mailbox that **exists**. A reports address on a domain with no inbox — say
`dmarc@hidden-view.com` before you have created it — means the reports that would tell you something
is wrong bounce instead.

### 4. SMTP credentials

*SES → SMTP settings → Create SMTP credentials.* This makes an IAM user with `ses:SendRawEmail` and
derives an SMTP password from its secret key.

**These are not your AWS access keys**, and the password cannot be re-derived later — it is shown
once. Username is an `AKIA…` id; the password is 44 characters. Endpoint
`email-smtp.us-east-1.amazonaws.com`, port 587 (STARTTLS). Port 25 is throttled on EC2 by default;
587 is not.

### 5. Leave the sandbox

A new SES account can only mail *verified* addresses, at 200/day and one per second. In the sandbox
the relay answers `553` at `MAIL FROM` for anyone else — which this transport reports as
`email_sender_refused` and **retries** rather than treating as a bad address, deliberately, so a
sandbox account cannot suppress your readers (§8).

*SES → Account dashboard → Request production access.* You are asked how you handle bounces and
complaints; the honest answer is that this system suppresses hard bounces permanently (§8) and
honours one-click unsubscribe, RFC 8058 (§9), which is what they are checking for. Usually approved
within a day.

While you wait, verify your own address as a recipient so you can test end to end:

```bash
aws sesv2 create-email-identity --region us-east-1 --email-identity yerram.saisanath@gmail.com
```

### 6. Cut over

**Run the configure command on its own** — nothing else on the clipboard, because the prompt reads
from the terminal and would otherwise swallow the next line:

```bash
cd /opt/ih
sudo bash deploy/ops/configure-email.sh digest@hidden-view.com --replace \
     --host email-smtp.us-east-1.amazonaws.com --user AKIA_YOUR_SMTP_USERNAME \
     --reply-to you@example.com
```

**`--reply-to` matters here specifically.** SES verifies `hidden-view.com` for *sending* — that is a
DKIM key and a DNS record, not a mailbox. Nothing receives at `digest@hidden-view.com`, so without
this a reader who hits Reply is writing into a void. Point it at an address a human reads.

It is written once and then KEPT across later `--replace` runs that do not mention it, on the same
reasoning as the allowlist and the secret: changing relay host is not a decision about where replies
go. Pass it again to change it. A malformed value is refused rather than written — an empty or
broken `Reply-To` is a header clients honour, so the failure is silent.

Paste the 44-character SMTP password at the prompt. Then, separately:

```bash
bash deploy/ops/restart.sh api
bash deploy/ops/check-email.sh
```

`--replace` rewrites the sender and keeps both `RWE_EMAIL_SECRET` (rotating it would break every
unsubscribe link already in an inbox) and `RWE_EMAIL_ALLOWLIST` (who may RECEIVE is a different
decision from who sends — re-deriving it would narrow the list to the sender's own mailbox, which
belongs to no reader, and every send would skip as `not-in-allowlist`).

### 7. Send one to yourself, and read the headers

This is the step that proves the four before it. In Gmail: ⋮ → **Show original**. You want
`SPF: PASS`, `DKIM: PASS`, `DMARC: PASS`, and `signed-by: hidden-view.com`.

A DKIM `d=amazonses.com` means Easy DKIM is not actually in use and DMARC is passing on Amazon's
reputation rather than yours — which works until it doesn't, and is exactly what this migration was
supposed to fix.

### 8. Only then widen

```bash
sudo bash deploy/ops/configure-email.sh digest@hidden-view.com --replace --allowlist '*'
```

Watch `examples/email_status.py` and the suppression list over the first few runs. A rising
suppression count is the signal to stop and look, not to keep sending.

### If it goes wrong

Every run of the writer leaves a timestamped backup beside the file:

```bash
ls -t /opt/ih/deploy/.env.*.bak | head -3
sudo cp /opt/ih/deploy/.env.<stamp>.bak /opt/ih/deploy/.env
bash deploy/ops/restart.sh api && bash deploy/ops/check-email.sh
```

`cp` onto the existing file keeps its owner and mode. Gmail keeps working while you sort SES out;
there is no deadline on this migration.

### Two things to decide, not steps

* **`digest@hidden-view.com` need not be a real mailbox to send from — but replies to it will
  bounce.** Either make it a forwarding alias, or accept that replies are lost. The mail carries no
  `Reply-To` today.
* **SES suspends accounts with high bounce or complaint rates.** The app's suppression list (§8) and
  one-click unsubscribe (§9) are what keep those down; SES also keeps its own account-level
  suppression list, on by default — a second net rather than a duplicate.

---

## 6. Turning it on

Set the variables in `deploy/.env`, then:

```bash
cd /opt/ih
sudo bash deploy/ops/update.sh claude/sleepy-gates-oecof1
```

Then check it, before believing anything:

```bash
bash deploy/ops/check-email.sh
```

That reports every variable as the **api container** sees it (the password as a length, never a
value), dials the relay through the same code path a real send uses, and hangs up without sending.
It exits non-zero when the deployment could not send to anyone. A failure names the thing to
change rather than the error it got — a `535` says "use a 16-character App Password", an
unreachable host says "check egress on that port" — because each of those lives in a different
place and an operator reading an SMTP code should not have to know which.

Then a dry pass — with the channel off for every reader, this
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

## 7. The schedule — hourly cron, for a weekly email

```cron
17 * * * * ubuntu /opt/ih/deploy/ops/send-digest-emails.sh 2>&1 | logger -t ih-email
```

Piped to `logger` rather than redirected into `/var/log`: the job runs as `ubuntu`, `/var/log` is
root-owned, and `>> /var/log/ih-email.log` therefore fails at the *shell redirect* — before the
script runs, producing no output and no log entry to explain why. Read the runs with:

```bash
journalctl -t ih-email --since today
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

## 8. Outcomes — and the one that must not be got wrong

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
source deploy/ops/_compose.sh
dc exec -T api python examples/email_status.py
```

which also reports what is waiting to be mailed, how much of it the allowlist clears, and the
ledger's depth — the questions that follow "why has no mail arrived" once `check-email.sh` is
green. A SCRIPT rather than `python -c`: the image's WORKDIR is `/app` and the modules live in
`/app/examples`, so Python only puts them on `sys.path` when it is a script's own directory. A
documented one-liner that got this wrong is what sent an operator a `ModuleNotFoundError` instead
of an answer; `test_a_documented_one_liner_can_actually_import_the_engine` now fails on any
recurrence.

---

## 9. Consent and unsubscribe

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

## 10. Rolling it back

```bash
# in deploy/.env
RWE_EMAIL_ENABLED=0
```

then `sudo bash deploy/ops/restart.sh api`. The worker becomes a no-op, the in-app digest continues
untouched, and readers' stored preferences are preserved — turning it back on resumes exactly where
it left off. No data is deleted, and the suppression list is not cleared (it should not be: those
addresses bounced).

---

## 11. Where the code lives

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
