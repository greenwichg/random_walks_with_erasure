# Hidden View — Beta User Onboarding Runbook (SOP)

**Status:** Standard Operating Procedure for inviting and onboarding closed-beta users.
**Applies to:** the production deployment at **https://hidden-view.com** (single EC2 host, Docker
Compose: `ingest → api → web → caddy`, behind Caddy/Let's Encrypt).
**Owner:** Hidden View operator. **Last updated:** 2026-07-22.

> This runbook is the checklist to follow **every time** you invite a beta user. Work top to bottom:
> verify preconditions → invite → let the user run their journey → run the admin checklist → collect
> feedback. It is operational only; it changes no application behavior.

Related docs: `docs/DEPLOYMENT_RUNBOOK.md` (AWS EC2), `docs/BETA_LAUNCH_CHECKLIST.md` (go-live gate),
`deploy/ops/README.md` (the ops toolbox this runbook drives).

---

## 1. Purpose

### Goal of the beta
Validate the **core information-diet product** with real users reading real, live news (GDELT-sourced),
before any wider launch. Concretely, confirm that:
- the end-to-end experience is **reliable** (sign-in → feed → read → history) on real devices;
- the **recommendation quality** and the **"Why this article?"** explanations are useful and trusted;
- the **Stories** clustering (one event across left/center/right) reads as coherent and valuable;
- the **value proposition** — *understand and balance your news diet* — lands without a manual.

### Target audience
A small, curated cohort — **start with 5–10 users, cap the first wave at ~30**. Ideal invitees:
- news-engaged readers who consume multiple sources per week;
- a deliberate **mix of political leanings and topic interests** (so lean-balancing and blind-spot
  surfacing actually get exercised);
- comfortable signing in with a **Google account** (the only auth method in beta);
- willing to give **candid, specific feedback** and tolerate rough edges.

Optional sub-cohort: **Chrome desktop users** who will also install the browser extension (v0.2.0) to
test passive reading-history capture. Keep this separate from the core web-only cohort.

### What we want to learn
| Question | Signal we're looking for |
|---|---|
| Does it work? | Zero blocker bugs; sign-in and feed succeed on first try |
| Is the value clear? | Users describe the product back correctly, unprompted |
| Are recommendations good? | Thumbs-up rate, "the explanations make sense," return visits |
| Are Stories coherent? | Users open Stories, understand the L/C/R framing |
| Where's the friction? | Drop-off points, confusion, support questions |
| Would they keep using it? | Day-2 / Day-7 return, qualitative "I'd use this" |

---

## 2. Preconditions — verify BEFORE inviting anyone

Do **not** send an invitation until every item below is green. Most are covered by two scripts; the rest
are quick manual confirmations. Access the box via **AWS SSM Session Manager** (no SSH key), then `cd`
to the repository root.

### 2.1 One-shot automated gate
```bash
# On the EC2 host, repo root, prod env loaded:
bash deploy/ops/preflight.sh        # PASS/WARN/FAIL: env, secrets, HTTPS, OAuth, DB, backup, monitoring
bash deploy/ops/smoke-test.sh       # running stack: containers up, engine live/ready, TLS, HTTP→HTTPS
```
Proceed only if **`preflight.sh` exits 0 (no FAILs)** and **`smoke-test.sh` reports all PASS** (WARNs are
acceptable pre-traffic, e.g. "no analytics data yet").

### 2.2 Precondition checklist (what each proves)

- [ ] **Application is deployed & running** — `docker compose … ps` shows `api`, `web`, `caddy`
      (and `ingest`) **running**; `smoke-test.sh` confirms all three edge containers up.
- [ ] **HTTPS is working** — `curl -I https://hidden-view.com` returns `HTTP/2 200`; the certificate is
      valid (Let's Encrypt, auto-renewed by Caddy); `http://` **redirects** to `https://`; `www.` redirects
      to the apex. *(Verify issuer if in doubt: `echo | openssl s_client -connect hidden-view.com:443
      -servername hidden-view.com 2>/dev/null | openssl x509 -noout -issuer -enddate` → `O = Let's Encrypt`.)*
- [ ] **Google OAuth is working** — perform a **real sign-in** in a fresh/incognito browser: visit the
      site → "Sign in with Google" → complete consent → land authenticated on the home feed. The Google
      OAuth **redirect URI** must include `https://hidden-view.com/...` and the OAuth consent screen must
      be **published** (or the tester's email added as an allowed test user).
- [ ] **Beta allowlist is configured** — the invite-only gate is ON and populated. In production
      `BETA_ACCESS_ENABLED=1` (default) and `BETA_ALLOWLIST` (or `BETA_ALLOWLIST_FILE`) contains at
      least the first wave's emails. **Fail-closed:** gate on + empty allowlist ⇒ *everyone* is denied.
      `preflight.sh` does **not** check this — confirm it manually (see §3.1 and
      `docs/BETA_ACCESS_CONTROL.md`).
- [ ] **Recommendations are loading** — the signed-in home feed renders cards in **Discovery** and/or
      **For You** with lean labels and "Why this article?" text (not an empty state or spinner-forever).
- [ ] **Stories page is working** — `/stories` renders clustered events with a non-trivial **count**
      (e.g. "138 stories") and cards showing L/C/R coverage. *(A count near 0 signals an ingestion or
      clustering problem — investigate before inviting.)*
- [ ] **GDELT ingestion is healthy** — fresh articles are flowing: the feed shows items with **recent
      timestamps** (hours, not days), and Stories are being formed. Confirm the `ingest` container is
      running and recent, and article volume is non-trivial. *(Baseline established 2026-07-22 — do not
      re-litigate the pipeline unless a concrete issue appears.)*
- [ ] **Engine health is green** — `/api/health/live` returns `{"status":"alive"}` and
      `/api/health/ready` returns **200** (internal probes, run by `smoke-test.sh` inside the `api`
      container; the engine has no public port).
- [ ] **No critical errors** — recent logs are clean:
      `docker compose … logs --since=1h api web caddy | grep -iE 'error|exception|traceback|fatal'`
      shows nothing alarming. The error-reporting sink is empty of new criticals.
- [ ] **Backups are running** — the hourly off-host backup to S3 has a **recent, verified** artifact
      (`deploy/ops/verify-restore.sh` proves the newest backup is intact and restorable). You must be
      able to recover a user's data if something goes wrong.
- [ ] **Monitoring is armed** — `monitor.sh` / `healthcheck.sh` cron (or external uptime monitor) is
      active and `ALERT_WEBHOOK` is set, so you learn about an outage before the user reports it.

If any box is red, **stop and fix it** — inviting a user into a broken state burns a scarce, curated
invitee and their goodwill.

---

## 3. Inviting a User

### 3.1 Process
1. **Choose the channel** — a personal email or direct message (not a mass blast). Beta invites are
   1:1 and personal.
2. **Grant access FIRST — add the invitee to the beta allowlist.** Hidden View is **invite-only**: a
   Google sign-in is rejected unless the exact email is on the allowlist. The gate is enforced in the
   NextAuth `signIn` callback (`web/lib/auth.ts`) via `isEmailAllowed` (`web/lib/beta-access.ts`); in
   production it is **ON by default and fail-closed**, so an email that isn't listed completes Google
   consent but then lands on the "invite-only" screen and **never gets an account**. Add them **before**
   sending the invite:
   - **Env var (needs a restart):** on the EC2 host, append the email — or `@theirdomain.com` to approve
     a whole domain — to `BETA_ALLOWLIST` in `deploy/.env`, then `bash deploy/ops/restart.sh web`.
   - **File (no restart):** if `BETA_ALLOWLIST_FILE` is set, add the email to that file; it is re-read on
     each sign-in attempt, so they can sign in immediately.
   - The invitee must sign in with **exactly** that Google email. Full reference + behavior matrix:
     `docs/BETA_ACCESS_CONTROL.md`.
3. **Send the invitation** using the template below. Include the URL, a one-line pitch, the beta caveat,
   and how to give feedback.
4. **Share the website URL:** **https://hidden-view.com**
5. **Log the invite** — record name, email, date invited, cohort (web-only / +extension), in your beta
   tracker (a spreadsheet is fine).

### 3.2 What to tell them (the pitch + expectations)

**What Hidden View does (elevator pitch):**
> Hidden View helps you understand and balance your news diet. It scores how diverse, calm, and
> cross-cutting your reading is, recommends articles that broaden your perspective (and tells you *why*),
> and clusters each major story across left, center, and right publishers so you can see one event from
> every viewpoint.

**Set expectations clearly:**
- **This is an early closed beta.** Things will be rough; **bugs are expected and welcome** — finding
  them is the point.
- **Sign in with your Google account** — that's the only login for now.
- **Your feedback is the deliverable.** Tell us what's confusing, broken, or delightful.
- **Privacy:** we record which articles you open to build your diet score and recommendations; see the
  privacy policy at https://hidden-view.com/privacy. Reading is local to your account.
- **How to report issues:** reply to this email / use the feedback form (Section 6). Screenshots help.

### 3.3 Invitation email template
```
Subject: You're invited to the Hidden View beta 👀

Hi <Name>,

I'd love your help testing Hidden View — a tool that helps you understand and balance your
news diet. It recommends articles that broaden your perspective (and explains why), and shows
each big story across left, center, and right publishers so you see the whole picture.

  → Try it: https://hidden-view.com
  → Sign in with your Google account (that's the only login for now).

A few honest notes:
  • This is an early beta. Expect rough edges and the occasional bug — that's exactly what
    I'm hoping you'll help me find.
  • Just read normally for a few days. Open articles that interest you; the recommendations
    and your "diet score" get better as you read.
  • Please tell me anything that's confusing, broken, or great — reply here, or use this form:
    <FEEDBACK_FORM_LINK>

Privacy: Hidden View records the articles you open (to build your recommendations and score);
details at https://hidden-view.com/privacy.

Thank you — this genuinely helps.
<Your name>
```

---

## 4. First-Time User Journey (what a new user experiences)

Walk this yourself in a fresh incognito profile before every invite wave, and share the "what good looks
like" notes with testers if helpful.

| # | Step | What happens | What "good" looks like |
|---|---|---|---|
| 1 | **Open the site** | Land on https://hidden-view.com | Loads over HTTPS in < 2–3s; clear sign-in call to action |
| 2 | **Sign in with Google** | Google consent → account auto-created on first sign-in | Returns authenticated to the home feed; no error screen |
| 3 | **See first recommendations** | Home feed renders. **Cold start:** with no history, the feed leans on **Discovery** (broadening/exploration); **For You** fills in as they read | Cards show title, publisher, **lean label** (Left/Center/Right), and a **"Why this article?"** rationale |
| 4 | **Read an article** | Clicking **Read article** opens the publisher's canonical URL in a new tab; the **read is recorded** to their history | Article opens; returning to the feed, the read is reflected (history/score updates) |
| 5 | **Understand political perspectives** | Each card/story is labeled Left/Center/Right; Stories show a spectrum across publishers, and a **blind-spot** hint when one side is uncovered | User can articulate "this is a right-leaning source" from the UI alone |
| 6 | **Understand the explanations** | "Why this article?" gives a plain-language reason — e.g. *another political perspective*, *Politics is 72% of your recent reading*, *broadens your sports coverage*, *you've never read this publisher* | The reason is legible and feels honest, not black-box |
| 7 | **Explore Stories** | `/stories` — "one event, every viewpoint," clustered across L/C/R; filter by Topic/Publisher/Lean, sort, paginate | A meaningful count of multi-publisher stories; clicking a story shows its cross-publisher coverage |
| 8 | **Open article/story detail** | Story detail shows the event's coverage list, distribution, and timeline | Detail loads; each source opens its own URL with the same Read flow |

**Cold-start expectation to communicate:** the feed is *broadening-heavy* on day 1 because there's no
reading history yet. Recommendations personalize (For You) and the diet score sharpens **after the user
reads a handful of articles** over a day or two. Encourage testers to "just read normally for a few
days" rather than judging personalization from a single session.

---

## 5. What the User Should Test — checklist

Give this to each tester (or walk it yourself as an acceptance pass). Tick what works; note anything that
doesn't in the feedback template.

**Login & session**
- [ ] "Sign in with Google" completes on first try
- [ ] Refreshing the page keeps me signed in
- [ ] Sign-out works; signing back in returns me to my account (history intact)

**Home page / feed**
- [ ] Home loads and shows recommendation cards (Discovery and/or For You)
- [ ] Cards show title, image (where available), publisher, and a lean label
- [ ] "Why this article?" text is present and understandable
- [ ] Like / dislike / save / history controls respond

**Recommendations**
- [ ] After reading a few articles, **For You** starts reflecting my interests
- [ ] "Read another perspective" surfaces a genuinely different-leaning source
- [ ] Recommendations feel relevant and are not repetitive/duplicated

**Story Browser (`/stories`)**
- [ ] Stories page loads with a story count and cards
- [ ] Topic / Publisher / Lean filters change the results sensibly
- [ ] Sort (Top / Latest / Oldest / Publishers) reorders as expected
- [ ] Opening a story shows cross-publisher coverage (L/C/R) for one event

**Search**
- [ ] Search (⌘K / the search box) opens and accepts a query
- [ ] Results are relevant; opening a result works

**Navigation**
- [ ] All nav items load (Home, Stories, History/Profile, etc.) — no dead links
- [ ] Back/forward browser buttons behave; deep links (a story URL) load directly
- [ ] Theme toggle (light/dark) works and persists

**Mobile responsiveness**
- [ ] The site is usable on a phone (portrait): cards, nav, and buttons are tappable
- [ ] No horizontal scrolling, cut-off text, or overlapping elements
- [ ] Sign-in works on mobile

**Performance**
- [ ] Pages load in a "feels fast" time (no multi-second blank screens)
- [ ] Scrolling the feed is smooth; images don't cause jank

**Broken links / errors**
- [ ] Every "Read article" opens a working publisher page (report any 404s)
- [ ] No visible error toasts, blank cards, or "something went wrong" screens

**Unexpected behaviour**
- [ ] Anything surprising, inconsistent, or confusing (note it, even if minor)

---

## 6. Feedback Collection

Collect feedback in **one structured place** (a Google Form or shared sheet is ideal — link it in the
invite as `<FEEDBACK_FORM_LINK>`). Use the template below; it maps 1:1 to what we want to learn.

### 6.1 Severity scale (for bugs)
| Level | Meaning | Example |
|---|---|---|
| **S1 – Blocker** | Can't use the product | Can't sign in; site down; feed never loads |
| **S2 – Major** | Core flow broken/painful | Stories empty; recommendations obviously wrong; mobile unusable |
| **S3 – Minor** | Annoyance, workaround exists | Misaligned card; slow image; confusing label |
| **S4 – Cosmetic** | Polish | Typo; spacing; wording |

### 6.2 Structured feedback template
```
── Hidden View — Beta Feedback ─────────────────────────────────────────────

Tester:            <name / email>
Date:              <yyyy-mm-dd>
Device / browser:  <e.g. iPhone 15 Safari / Windows Chrome 126>

1) BUGS  (repeat per bug)
   • Severity:     S1 / S2 / S3 / S4
   • What happened:
   • Steps to reproduce:
   • What you expected instead:
   • Screenshot/link:

2) MISSING FEATURES
   • What you looked for but couldn't find:

3) CONFUSING UI
   • What was unclear (label, screen, flow):
   • What you thought it would do vs. what it did:

4) RECOMMENDATION QUALITY  (1–5, 5 = excellent)
   • Rating:  ___
   • Were the "Why this article?" reasons believable/helpful?
   • Anything irrelevant, repetitive, or oddly biased?

5) STORY QUALITY  (1–5)
   • Rating:  ___
   • Did clustered stories group the SAME event correctly?
   • Was the left/center/right framing clear and fair?

6) PERFORMANCE  (1–5)
   • Rating:  ___
   • Anything slow, janky, or that failed to load?

7) OVERALL
   • Overall rating (1–5):  ___
   • Would you keep using it? (yes / maybe / no) — why?
   • One thing you'd fix first:

8) SUGGESTIONS
   • Anything else — ideas, wishes, reactions:
────────────────────────────────────────────────────────────────────────────
```

**Operator handling:** triage each submission into your tracker with severity; acknowledge S1/S2 to the
reporter within 24h; batch S3/S4. Close the loop — tell testers when their bug is fixed (huge for
goodwill and continued participation).

---

## 7. Troubleshooting Guide

Diagnose from the box via **SSM Session Manager**, repo root, prod env loaded.

| Symptom (user-reported) | Likely cause | Operator action |
|---|---|---|
| **"Can't sign in with Google"** / OAuth error screen | Redirect URI mismatch; consent screen not published / tester not an allowed test user; clock skew | Confirm the Google OAuth client's authorized redirect URIs include the prod callback; publish the consent screen or add the tester's email; re-test in incognito |
| **Completes Google sign-in but lands on an "invite-only" / access-denied screen** | Email isn't on the beta allowlist (or the allowlist is empty ⇒ fail-closed = everyone denied) | Add the exact email (or `@domain`) to `BETA_ALLOWLIST` in `deploy/.env`, then `bash deploy/ops/restart.sh web` — or to `BETA_ALLOWLIST_FILE` (no restart). Confirm the email matches exactly; check `web` logs for `beta_access_denied` (its `reason` says which). Ref: `docs/BETA_ACCESS_CONTROL.md` |
| **Signed in, but every personalised page shows the demo reader's data or errors** | The session has no engine user id — sign-in could not reach the engine (a deploy restarting `api` is the usual cause) | This now **self-repairs on the next request**. If it persists, recovery is failing: `docker logs deploy-web-1 2>&1 \| grep engine_identity_recovery_failed \| tail`. `http_401` ⇒ `RWE_INTERNAL_SECRET` differs between `web` and `api`; `timeout` ⇒ engine wedged; `unreachable` ⇒ engine down. Ref: `docs/SESSION_IDENTITY_RECOVERY_DESIGN.md` §5a |
| **One reader stays broken while everyone else is fine** | They were removed from the allowlist after signing in, so recovery refuses to attach them | `grep engine_identity_recovery_denied` — the line carries their email and a `reason`. Add them back to `BETA_ALLOWLIST` (takes effect within ~30 s), or leave them out deliberately. `reason: empty_allowlist` means **everyone** is denied — fix the allowlist immediately |
| **Recovery itself is suspected of causing load or errors** | It calls the engine from the auth path | Turn it off without a rebuild: `RWE_IDENTITY_RECOVERY=0` in `deploy/.env`, then `bash deploy/ops/restart.sh web`. Sessions that already have an id keep working; only the repair stops |
| **Signed in but feed is empty** / "no recommendations" | Cold start (no history yet — expected); or ingestion/engine issue | If brand-new account: expected — ask them to read a few and revisit. Else check `/api/health/ready` (200?), `ingest` container running, article volume, engine logs |
| **Stories page empty or count ~0** | Ingestion stalled, or too few multi-publisher clusters in the window | Verify recent article timestamps; confirm `ingest` running; check story diagnostics (`/api/stories?debug=1`); inspect `api` logs |
| **Site won't load / 502 / 503** | A container down or crash-looping; engine not ready | `docker compose … ps`; `bash deploy/ops/smoke-test.sh`; `docker compose … logs --since=15m`; `bash deploy/ops/restart.sh` (or a specific service) |
| **Certificate warning / "not secure"** | Caddy failed ACME (port 80 blocked, DNS not resolving) | Confirm 80/443 open and DNS A record → the Elastic IP; check `caddy` logs for ACME errors; ensure `caddy_data` volume persisted |
| **Page loads slowly** | Cold container, large feed scan, image weight | Check host CPU/credits and `t3.medium` load; confirm `monitor.sh` isn't alerting; consider a warm-up read |
| **"Read article" 404s** | Stale/expired publisher URL from the source feed | Note the URL; it's usually a publisher-side link expiry, not a Hidden View bug — log as S3 |
| **Signed out unexpectedly** | Session expiry / cookie issue | Confirm the auth session config; ask for browser + repro; check `web` logs |
| **Nothing obvious, user stuck** | Unknown | Reproduce in incognito on the same device class; capture console + network; open an issue with the tester's repro |

Fast operator triage (run these first, in order):
```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.aws.yml --env-file deploy/.env ps
bash deploy/ops/smoke-test.sh
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.aws.yml --env-file deploy/.env \
  logs --since=30m api web caddy | grep -iE 'error|exception|traceback|fatal' | tail -50
```

---

## 8. Admin Checklist — after inviting (and after the user's first session)

Run this once the user has signed in and read a few articles. All from the box (SSM), repo root.

- [ ] **Account created** — the new user exists. Confirm via the engine/store (a new user row appears
      after their first Google sign-in) or the analytics funnel:
      the sign-in shows up in `/api/analytics/funnel` (internal-only — send the `X-IH-Auth` internal
      secret; `smoke-test.sh` demonstrates the pattern).
- [ ] **Recommendations generated** — signed in as/observing the account, the feed returns cards (not an
      empty state). For a brand-new account this is Discovery-weighted; that's correct.
- [ ] **Reading history recorded** — after the user opens ≥1 article, their read is persisted (history
      count > 0; the profile/score reflects it). Verify via the History view or the store.
- [ ] **No backend errors for this session** — logs for the session window are clean:
      `docker compose … logs --since=<their session start> api web | grep -iE 'error|exception|traceback'`
      returns nothing tied to their activity; the error-reporting sink shows no new criticals.
- [ ] **Health still green** — `/api/health/ready` 200; `bash deploy/ops/healthcheck.sh` exits 0.
- [ ] **Ingestion still fresh** — new articles/stories continue to appear (the beta shouldn't degrade the
      pipeline).
- [ ] **Backup captured** — the user's new data is within the next hourly off-host backup (and
      `verify-restore.sh` still passes).

Record the outcome (pass/fail per item) next to the invitee in your beta tracker.

---

## 9. Beta Exit Criteria — when to move beyond the initial beta

Graduate from the **initial closed beta** to a wider/next phase only when **all** of the following hold,
sustained over a representative window (e.g. 2+ weeks with active testers):

**Reliability & stability**
- [ ] **Zero open S1 (blocker) bugs** and **no unresolved S2** for the core flows (sign-in, feed, read,
      stories).
- [ ] **Uptime ≥ 99%** over the window; no unattended outage (monitoring caught anything that occurred).
- [ ] Engine `/api/health/ready` green throughout; no crash-loops; error rate near-zero in logs.
- [ ] **Backup + restore proven** at least once end-to-end (`verify-restore.sh` green; a real
      restore rehearsed).

**Functionality & quality**
- [ ] Google OAuth sign-in succeeds reliably across the cohort's browsers/devices.
- [ ] Recommendations and Stories load for every active user; GDELT ingestion healthy the whole window.
- [ ] **Recommendation quality ≥ 3.5/5** and **Story quality ≥ 3.5/5** average across testers.
- [ ] Mobile experience acceptable (no S1/S2 mobile issues open).

**User signal**
- [ ] **≥ 60% of invited testers actually signed in and read ≥ 3 articles.**
- [ ] **Day-2 or Day-7 return** observed for a meaningful fraction (there's a retention pulse, not
      one-and-done).
- [ ] **Overall rating ≥ 3.5/5**, and a majority answer "yes/maybe" to "would you keep using it?"
- [ ] Testers can describe the product back correctly (the value prop is legible without a manual).

**Operational readiness for scale**
- [ ] Feedback triage process is working (submissions → tracker → fixes → closed loop).
- [ ] Known limitations documented; a prioritized backlog exists.
- [ ] Capacity headroom confirmed on the current `t3.medium` for the next cohort size (CPU/EBS/network).

If some criteria pass and others don't, **extend the current beta and iterate** rather than expanding the
cohort — widening a shaky beta multiplies support load and wastes invitees.

---

## 10. Future Improvements (make onboarding scale)

As the user base grows, invest here to reduce per-user manual effort:

**Onboarding & access**
- **Self-serve waitlist + invite codes** — replace 1:1 emails with a gated signup and a redeemable code,
  so you control volume without hand-sending each invite.
- **In-app onboarding tour** — a first-run overlay that explains lean labels, "Why this article?", and
  Stories, so testers need less hand-holding and the pitch lives in the product.
- **Cold-start primer** — a lightweight "pick a few topics/leans you follow" step (or a first-session
  Discovery emphasis with a visible "personalizes as you read" note) to make day-1 feel less empty.
- **More sign-in options** — add email magic-link or a second OAuth provider so a Google account isn't a
  hard gate.

**Feedback & support**
- **In-app feedback widget** — a persistent "Report a bug / suggest" button that captures the current
  page, device, and (optionally) console context automatically, replacing the external form.
- **Public status page** — surface uptime and known incidents so testers self-serve "is it down?".
- **Automated bug intake** — route the feedback widget into the issue tracker with severity + repro
  pre-filled.

**Operability at scale**
- **Synthetic monitoring** — an external uptime monitor hitting sign-in/feed/stories continuously, so
  regressions are caught before a user reports them (complements `healthcheck.sh`/`monitor.sh`).
- **Per-user admin/diagnostics view** — an operator dashboard (account created? reads recorded? errors?)
  so the Section 8 checklist becomes one screen instead of shell commands.
- **Staged rollout / cohorts** — invite in waves behind a flag, so a bad deploy affects a fraction.
- **Rate limiting & abuse protection** — before opening signups, add basic protections at the edge.
- **Capacity plan** — define the point at which the single `t3.medium` needs a larger instance,
  read replicas, or a managed database, and a migration path (Terraform-managed since 2026-07).

**Product**
- **Browser extension rollout** (v0.2.0, Chrome) — a documented opt-in track for passive reading-history
  capture, with its own onboarding once the web experience is solid.
- **Lifecycle email** — a gentle day-2 "come back and read a few" nudge to convert first-session testers
  into returning users (retention is a key beta signal).

---

*End of runbook. Keep this document current as the deployment, auth, and feedback process evolve; it is
the single SOP for onboarding every Hidden View beta user.*
