# Wave 0 Success Plan — first 5 users (closed beta)

**Owner:** CPO / Beta Program Manager · **Objective:** *maximize learning* from the first five users.
Documentation only — no product feature, no code change. Grounds every metric in the shipped PA1
analytics (`/api/analytics/*`) and OBS1 observability (`/api/health/*`, `/api/metrics`, structured logs),
and follows `docs/BETA_LAUNCH_PLAYBOOK.md`.

> **Read this first — the n=5 reality.** With five users, **every user is 20%** and no funnel number is
> statistically meaningful. Wave 0's real deliverable is **qualitative**: watch five real humans use the
> product end-to-end, find where they stall, hear *why* in their words, and confirm the instruments
> (analytics + observability + backups) actually work before we scale. Treat the quantitative targets
> below as **directional sanity checks and conversation triggers**, not KPIs to optimize. A "miss" is a
> learning, not a failure.

---

## 1 · Success Criteria

Targets are expressed as **count out of 5** and the implied %, with the PA1 signal that measures each.
Two tiers: **funnel targets** (does the journey work for real people?) and **meta-success** (did Wave 0
achieve its *purpose* — learning + working instruments?). Meta-success is the true bar.

### Funnel targets (directional)

| Stage | PA1 signal (funnel `key`) | Target | What a miss teaches |
|---|---|---:|---|
| Invited → opened the app | `app_opened` | **5/5 (100%)** | hand-picked users who don't show up ⇒ invite/access friction |
| Completed onboarding (connected sources) | `source_connected` | **4/5 (80%)** | drop here ⇒ onboarding is too long / unclear value |
| Signed in | `login_success` | **4/5 (80%)** | drop ⇒ OAuth friction or unclear why-sign-in |
| First article read | `article_read` | **3/5 (60%)** | no reads ⇒ no obvious next action after the report |
| Health Report generated | `health_report_viewed` | **4/5 (80%)** | report available even in Estimate ⇒ most should reach it |
| **Measured Mode reached (the "aha", ≥5 reads)** | `measured_report` (`mode=measured`) | **2/5 (40%)** | the effort gate — do users do the work? *the key Wave-0 question* |
| Recommendation viewed | `recommendations_viewed` | **3/5 (60%)** | not viewed ⇒ recs aren't discoverable / compelling |
| Recommendation accepted (opened or like/read-later) | `recommendation_accepted` | **2/5 (40%)** | viewed-but-not-accepted ⇒ relevance/trust problem |
| Returned within 48 h | `returned_next_day` (a later-day session) | **2/5 (40%)** | no return ⇒ no reason to come back (retention hypothesis) |

Pull the live funnel any time:
```bash
curl -fsS -H "X-IH-Auth:$SECRET" $ENGINE/api/analytics/funnel      # reachers + conversion + topDropOff
curl -fsS -H "X-IH-Auth:$SECRET" $ENGINE/api/analytics/metrics     # activation, time-to-first-report, D1
```

### Meta-success (the real Wave-0 bar — all must be TRUE)

- [ ] **Instruments proven:** the funnel populates for real users (not just the RM's smoke), OBS1 shows
      clean logs, and at least one backup was taken **and** `verify-restore.sh`-passed during Wave 0.
- [ ] **Top drop-off identified** from `topDropOff` **and** corroborated by user words (not guessed).
- [ ] **Qualitative signal captured:** ≥3/5 users gave substantive feedback (§3).
- [ ] **Operational integrity:** **0 P0/P1** bugs open at wave end; 100% readiness; **0** data-integrity
      failures (`/api/internal/storage` quick_check = ok throughout).
- [ ] **A decision:** Wave 0 ends with an explicit, data-backed **expand / hold / fix** call (§5).

**Wave 0 is a success if the meta-success list is all-TRUE — even if several funnel targets are missed.**

---

## 2 · Daily Monitoring Dashboard

Reviewed once/day (twice on Launch Day). Every value is a real PA1/OBS1 signal. `$ENGINE` = private engine
origin; internal endpoints need `-H "X-IH-Auth:$SECRET"`.

### 🔴 Critical — investigate immediately; may pause/rollback (see §4)

| Signal | Command | Investigate when |
|---|---|---|
| Readiness | `curl -s -o /dev/null -w '%{http_code}' $ENGINE/api/health/ready` | ≠ `200` for >2 min |
| Unhandled exceptions | logs `grep '"event":"unhandled_exception"'` / `/api/metrics` 5xx counters | **any** occurrence |
| Data integrity | `curl -H "X-IH-Auth:$SECRET" $ENGINE/api/internal/storage` (quick_check) | ≠ `ok` |
| Backup freshness + restorability | `deploy/ops/verify-restore.sh`; newest backup age | no backup <25 h, or verify fails |
| **Learning pipeline** (Wave-0-critical) | `/api/analytics/events` total vs. known active users | **flat while users are active** (analytics dead ⇒ Wave 0 can't learn) |

### 🟡 Warning — track the trend; throttle/defer, don't rollback for these alone

| Signal | Command | Investigate when |
|---|---|---|
| DB latency (contention) | `/api/metrics` → `db_query_ms` p95 | climbing day-over-day (the write-on-read + analytics-write risk) |
| Request / report latency | `/api/metrics` → `request_ms|…`, `report_generate_ms` p95 | p95 > ~2× the T-1 baseline |
| Rate limiting | logs `grep '"event":"rate_limited"'` | recurring for a normal user |
| Client errors | `/api/metrics` `client_errors_total`; logs `client_error` | any cluster (esp. same `url`) |
| Feed health | `curl -H "X-IH-Auth:$SECRET" $ENGINE/api/internal/feeds` | many unhealthy/stale (recs go static) |
| Disk headroom | host | trending toward full (SQLite writes fail when full) |

### 🔵 Informational — the daily learning readout (no threshold; this is the story)

- **Funnel snapshot** (`/api/analytics/funnel`): reachers per stage + `topDropOff`.
- **Metrics** (`/api/analytics/metrics`): activation rate, measured-activation, time-to-first-report,
  time-to-measured, recommendation engagement/acceptance, D1 retention.
- **Event mix** (`/api/analytics/events`): which events fire, in what volume.
- **Uptime / throughput** (`/api/metrics`): `uptimeSeconds`, `requests_total|…`, `analytics_events_total`.

> Daily habit: paste the funnel + metrics + `db_query_ms` p95 into the running log; note **one thing that
> changed** and **one question it raises**. That log becomes §Analytics of the weekly report.

---

## 3 · User Feedback Plan (qualitative-first)

Five users = five conversations. Feedback is **contextual** (triggered by where each user is in the
funnel, read from PA1) and **lightweight** (a few open questions, never a survey). Keep it human.

### Contact cadence per user (U1–U5)

Assign each invited user a row; drive outreach off their live funnel position, not a fixed calendar.

| Moment | Trigger (from `/api/analytics/funnel` per identity + OBS1) | Channel | Purpose |
|---|---|---|---|
| **Welcome** | on invite (T+0) | personal DM/email | set expectations: "you're 1 of 5; I want your honest reactions" |
| **First-session check-in** | within a few hours of their first `app_opened`/`login_success` | DM | catch first-impression friction while fresh |
| **Stall nudge** | signed in but **no `article_read` in 24 h**, or `health_report_viewed` but **no `measured_report` in 48 h** | DM | targeted: "what stopped you from …?" |
| **Depth interview** | Day 2–3, ideally after they've seen a report + a recommendation | 15-min call | the core qualitative session (script below) |
| **Wrap** | Day 6–7 | DM/call | would-you-return, one-thing-to-change, NPS-style gut check |

Per-user tracking grid (fill during the wave):

| User | Invited | First session | Furthest funnel stage | Stalled at? | Interviewed | Sentiment | Top quote |
|---|---|---|---|---|---|---|---|
| U1 | | | | | | | |
| U2 | | | | | | | |
| U3 | | | | | | | |
| U4 | | | | | | | |
| U5 | | | | | | | |

### Interview script (ask few, listen more)

1. **Onboarding:** "Walk me through what you thought this was in the first minute. Anything confusing?"
2. **The report:** "When you saw your Information Health Report — did it make sense? Did you **believe** it?"
   *(trust is the product's core claim — probe honesty/estimate-vs-measured.)*
3. **Effort/aha:** "Did you read any articles through it? What would make you read five?" *(the Measured gate.)*
4. **Recommendations:** "Were the suggestions relevant? Did the *Why?* explanation help you trust them?"
5. **Return:** "Would you come back in a few days? What would bring you back?"
6. **The one thing:** "If you could change one thing, what is it?"
7. (silence — let them add the thing you didn't ask.)

### Categorize responses

Tag every comment on **two axes**:

- **Theme:** `Onboarding` · `Report comprehension` · `Trust/Honesty` · `Recommendation relevance` ·
  `Performance/Mobile` · `Bug` · `Value/Retention`.
- **Type:** `Blocker` (stopped them) · `Friction` (slowed them) · `Delight` (protect it) · `Suggestion`.

### Prioritize issues

`Priority = frequency (how many of 5) × type-weight (Blocker 3 / Friction 2 / Suggestion 1) ×
hypothesis-alignment (does it speak to a §7 hypothesis?)`. A **Blocker hit by ≥2/5** is top of the list.
A **Delight hit by ≥2/5** is a moat — name it and protect it. Bugs route to §4.

---

## 4 · Bug Triage Process

Severity, response time, and whether the rollout **pauses** or **rolls back**. Reproduce with the OBS1
`X-Request-ID` (every error body + log line carries it) to correlate a user report to a log line.

| Sev | Definition (examples) | Response time | Pause rollout? | Rollback? |
|---|---|---|---|---|
| **P0** | Data loss/corruption; security breach; total outage; auth broken for **all**; `quick_check ≠ ok` | **Immediate** | **Yes — halt all invites** | **Yes** — app image; **data restore** if integrity is the fault (per playbook) |
| **P1** | Core flow broken for **multiple** users (report won't load, recs broken, sign-in fails intermittently); sustained 5xx | **< 4 h** | **Yes** until fixed or mitigated | If no fast fix/mitigation → app rollback |
| **P2** | Significant but **single-user / single-surface** or has a workaround (a chart glitch, a mobile layout bug, a wrong label) | **< 2 business days** | No | No |
| **P3** | Cosmetic / minor / polish | Backlog | No | No |

**Rules.** (1) Any P0 → invoke the playbook's Standing Rollback Rule (app fault → previous image tag;
data fault → restore newest verified off-host backup; never hand-edit the live DB). (2) A P1 keeps the
cohort **frozen at its current size** until resolved. (3) Every bug gets a `requestId` (or a repro) and a
theme tag so it feeds the weekly report. (4) At n=5, one user hitting a blocker is **20% of the cohort** —
treat single-user blockers seriously even if severity is P2.

---

## 5 · Wave Expansion Criteria (data-only)

Expansion is a **gate**, not a schedule. Advance only when **every** criterion for the step is met on
**observed data**; otherwise hold and remediate. Each step adds a **minimum soak** at the current size.

**Universal gate (all steps):** 0 P0/P1 open · readiness 200 throughout the soak · 5xx ~0 ·
data-integrity ok · a **verified backup** taken during the soak · funnel is **populating** (learning
works) · no unresolved **Blocker theme** hitting ≥⅓ of users.

| Step | Min soak | Additional, size-specific gates |
|---|---:|---|
| **5 → 15** | 3–5 days | Meta-success (§1) all TRUE for Wave 0; **top drop-off named + a hypothesis for it**; ≥3/5 gave feedback; instruments proven. |
| **15 → 30** | 3–4 days | Activation (report generated) trending toward target on the larger n; `db_query_ms` p95 **flat** vs Wave 0; ≥1 user reached **Measured** and confirmed the "aha" qualitatively. |
| **30 → 50** | 3–4 days | Recommendation engagement measured (not 0); no new P1 in the last soak; latency p95 within budget as data grows. |
| **50 → 100** | 4–5 days | **150-VU load smoke passed** (or observed p95 under real 50-user load within budget); backup **restore drill** rehearsed since launch; disk projection safe for the full window. |
| **100 → 150** | 5–7 days | Everything above holds at 100; retention (D1, emerging D7) is **readable and not collapsing**; no unresolved Blocker theme; on-call + rollback assets still staged. |

Latency/contention is the physical constraint that tightens with N (SQLite single-writer + write-on-read
+ analytics writes) — that's why `db_query_ms` p95 stability is an explicit gate at every step and the
load smoke gates the 50→100 jump.

---

## 6 · Weekly Beta Report

The reusable template lives in **`docs/WEEKLY_BETA_REPORT_TEMPLATE.md`** — copy it per week to
`docs/beta-reports/week-N.md` and fill from PA1 (`/api/analytics/*`) + OBS1 (`/api/metrics`, logs) +
the §3 feedback grid + the §4 bug list. Sections: Executive Summary · Product Health · Operational Health
· Analytics Summary · Recommendation Performance · Top User Feedback · Top Bugs · Decisions Made ·
Open Risks · Next-Week Priorities.

---

## 7 · Recommendations — the top 5 hypotheses Wave 0 must validate

Learning only — **no new features.** Each hypothesis names the PA1/qual signal, a **validate** signal, and
a **pivot/investigate** signal. These are the questions five users can actually answer.

**H1 — Onboarding delivers a credible first value.**
*Claim:* a visitor completes onboarding and reaches a report they find believable within one session.
*Measure:* `source_connected → health_report_viewed` conversion + interview Q1–Q2 (trust).
*Validate:* ≥4/5 reach the report **and** describe it as understandable/credible.
*Pivot signal:* drop at `source_connected`, or users reach the report but distrust it ("where do these
numbers come from?").

**H2 — Measured Mode is the activation "aha" — and users will do the work to reach it.**
*Claim:* reaching Measured (≥5 reads) is what turns curiosity into value.
*Measure:* `measured_report` reachers, `timeToMeasuredModeSeconds`, interview Q3.
*Validate:* ≥2/5 reach Measured **and** say it felt more valuable than the Estimate.
*Pivot signal:* nobody reads 5 articles ⇒ the path from report→read is unclear, or the effort is unjustified
by perceived payoff (a positioning/《learning》problem, not a feature gap).

**H3 — Recommendations are relevant and their evidence earns acceptance.**
*Claim:* users find recs relevant and the *Why?* evidence makes them trust and act.
*Measure:* `recommendation_accepted / recommendations_viewed` (acceptance rate) + interview Q4.
*Validate:* ≥2/5 accept a recommendation **and** cite the evidence/《Why?》as trust-building.
*Pivot signal:* viewed-but-not-accepted, or "the suggestions felt random / I didn't get why."

**H4 — There is a reason to come back within 48 hours.**
*Claim:* the product creates a pull to return (streak, fresh recs, updated report).
*Measure:* `returned_next_day`, `day1Retention`, interview Q5.
*Validate:* ≥2/5 return within 48 h **and** can articulate why.
*Pivot signal:* no returns ⇒ Wave 0's clearest signal that the loop isn't yet compelling (a retention
learning to carry into product strategy — **not** a prompt to build something new).

**H5 — The honesty framing (Estimate vs Measured, evidence-bound recs) builds trust rather than confusion.**
*Claim:* the product's core differentiator — *honest, evidence-grounded* health framing — is felt as
trustworthy, not as a black box or a downgrade.
*Measure:* interviews Q2 + Q4 (trust themes); watch for confusion between Estimate and Measured in feedback.
*Validate:* users spontaneously mention trust/honesty positively; nobody is confused about what's estimated
vs measured.
*Pivot signal:* "why is it just an estimate?" framed as a negative, or users treat the score as arbitrary —
a comprehension/《trust》learning about how we *communicate* the existing model.

**Meta-hypothesis (H0, always-on): the instruments are trustworthy.** Before we believe any of H1–H5, the
analytics funnel, OBS1 signals, and backups must demonstrably work at n=5 — otherwise every later wave is
flying blind. Wave 0 proves the instruments first.

---

*Documentation only. Reuses the shipped PA1 analytics and OBS1 observability and the Beta Launch Playbook.
No product feature and no application-code change was made to produce this plan.*
