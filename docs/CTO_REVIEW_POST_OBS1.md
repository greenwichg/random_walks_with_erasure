# CTO Strategic Review — the single next workstream (post-OBS1)

**Read-only. No code.** The question: with RC2 (recommendation engine) and OBS1 (observability)
complete, and a **100–150-user closed beta a few weeks out**, what is the *single* highest-ROI action?

## Decision

> **Instrument the product-analytics activation funnel** (onboarding → sign-in → first read → *measured*
> threshold → recommendation engagement → return), delivered as a lightweight, vendor-agnostic event
> pipeline that reuses the OBS1 error-reporting pattern.
>
> It is the single thing that decides whether the beta achieves its *purpose*. A closed beta exists to
> **learn**, and right now we would launch **blind to the funnel** — unable to answer "do users finish
> onboarding? do they reach a real personalized report? do they act on the RC2 recommendations we just
> spent five phases building?" The first cohort's funnel is **unrepeatable**, so it must be captured at
> launch, not bolted on later.

**This differs from my previous architecture review — and that is the point.** That review chose
*observability* (operational visibility) as #1; I then shipped it as OBS1. With the operational-blindness
gap closed, the **binding constraint has moved** from *"can we see failures?"* to *"can we learn
anything?"* Observability tells you the beta didn't *crash*; analytics tells you whether it *worked*. The
recommendation is not a reversal — it is the next link in the same chain, now unblocked.

---

## Four-perspective readiness

| Perspective | Maturity | Verdict |
|---|---|---|
| **Product readiness** | **Medium** | Feature-complete and honest, but we can't yet *measure* whether users convert, activate, or engage. The RC2 engine is unvalidated by real behavior. **This is the gap.** |
| **Engineering readiness** | **High** | Clean leaf architecture, 1,400+ backend tests, deterministic disciplines, fail-closed security, OBS1 metrics/health/error-reporting. Residual: write-on-read on `GET /api/report`, no DB migrations, untested at scale. |
| **User experience** | **High** | Progressive onboarding, RC1 polish, honest empty states, i18n of the flagship report. Residual: chart-page weight on mobile, un-localized recommendation prose, onboarding conversion unmeasured. |
| **Operational readiness** | **Medium-High** | OBS1 gave error reporting, metrics, and liveness/readiness endpoints. Remaining is *config* (point an uptime monitor + 5xx alert at them) and a load smoke — not implementation. |

**The one low bar is Product readiness — specifically, the inability to measure the funnel.**

---

## Workstream catalog

Effort S ≈ days · M ≈ 1–2 wks · L ≈ multi-week · XL ≈ month+. Impact = user/business value.

| # | Workstream | Problem it solves | Effort | Impact | Tech risk | When |
|---|---|---|---|---|---|---|
| A | **Product analytics / activation funnel** | We can't see conversion, activation, or RC2 engagement — the beta's whole purpose | **M** | **Very High** | **Low** | **before** |
| B | Uptime monitor + 5xx/latency alerting | Nobody is *paged* when prod is down (OBS1 built the endpoints; this wires a monitor to them) | **S** (config) | High | Low | **before** |
| C | Load/perf smoke at ~150 users | Confirms SQLite write-contention (RC2 write-on-read + OBS1 per-query timing) holds at scale | S–M | Med-High | Low | **before/during** |
| D | Lightweight DB migration discipline | First non-additive schema change during beta iteration is unguarded (`create_all` only) | S–M | Medium | Med | **during** (before 1st non-additive change) |
| E | Onboarding conversion polish | Reduce drop-off in the value→estimate→sign-in funnel | M | High | Low | **during** (after A measures it) |
| F | Retire write-on-read on `GET /api/report` | RC2.3 made the report read path write the lifecycle ledger — non-idempotent, adds write load | M | Medium | Low | **during/after** |
| G | Lazy-load chart bundles (M2) | `/report`,`/analytics`,`/dashboard` ≈ 376 kB First-Load JS on mobile/slow networks | S–M | Medium | Low | **during** |
| H | Localize recommendation prose | RC2 evidence/impact/lifecycle text is backend-English under every locale | M | Medium | Low | **during/after** |
| I | Automated a11y gate in CI (axe) | a11y is maintained by discipline, not enforced | S | Medium | Low | **during** |
| J | Split `api_fastapi.py` monolith | ~2.7k-line file mixing routing/models/orchestration | M | Low (internal) | Low | **after** |
| K | PostgreSQL + shared limiter + 2nd engine (M5) | Horizontal scale beyond the beta window | XL | High (later) | Med | **after** |
| L | Recommendation learning loop (RC2.5 calibration → estimator) | Close the calibration feedback loop | L | Medium | Med | **after** (needs beta data + A) |
| M | Shorter / revocable sessions (L5) | 30-day non-revocable JWT | S | Low-Med | Low | **during/after** |
| N | Offline / PWA handling | Graceful offline; installable | L | Low-Med | Med | **after** |

---

## Top-10 ranking (Impact × Effort × Risk)

Ranked by ROI = value delivered per unit of effort and risk, weighted for a *pre-beta* window.

| Rank | Workstream | Why it ranks here |
|---|---|---|
| **1** | **A — Product analytics / activation funnel** | Very-High impact, Low risk, Medium effort — and it's the beta's *reason to exist*. Unrepeatable first-cohort data. |
| 2 | B — Uptime + alerting | Highest impact-per-effort (config), but it's ops wiring on top of OBS1, not an implementation workstream. |
| 3 | C — Load/perf smoke | Cheap insurance now *measurable* thanks to OBS1; the risk it covers is modest at 150 users. |
| 4 | E — Onboarding conversion polish | High impact, but you must **measure first (A)** before optimizing — A is its prerequisite. |
| 5 | D — DB migration discipline | Protects beta data on the first non-additive change; not needed *at* launch. |
| 6 | G — Lazy-load charts | Real mobile win, cheap; not launch-gating. |
| 7 | F — Retire write-on-read | Correctness/scaling hygiene; the try/except makes it non-urgent. |
| 8 | H — Localize rec prose | Broadens the beta's reach; deferred i18n debt. |
| 9 | I — a11y CI gate | Enforce what's already good; cheap. |
| 10 | K — Postgres / scaling | Highest long-term value, but XL and explicitly post-beta. |

**Detail on the podium:**

**A — Product analytics.** *Why now:* the beta's ROI is dominated by what we learn, and the acquisition→
activation funnel is unrepeatable — you cannot reconstruct "where the first 50 users dropped off." *What
it unlocks:* every product decision (onboarding fixes E, whether RC2 recommendations actually get
accepted, retention), and eventually the RC2.5 calibration loop (L). *Cost of postponing:* the first
cohort is spent blind; RC2's five phases stay unvalidated by real behavior. *Debt/findings addressed:*
Beta-Audit **H2** (no product/funnel analytics), and it operationalizes the RC2.3 lifecycle ledger +
RC2.5 evaluation that already capture *post-activation* signal.

**B — Uptime + alerting.** *Why now:* you must be paged when prod is down. *Unlocks:* safe unattended
operation. *Postponing:* an outage goes unnoticed until users complain. *Addresses:* **M-obs** — but
OBS1 already built `/api/health/live`,`/ready`,`/metrics`; this is a ~30-minute monitor config, so it's
a **launch checklist item, not a workstream**.

**C — Load smoke.** *Why now:* RC2 added write-on-read and OBS1 added per-query timing; a 150-VU smoke
confirms SQLite holds. *Unlocks:* launch confidence. *Postponing:* a small, now-*monitorable* risk (OBS1
shows p95/DB latency live). *Addresses:* **M4**.

---

## The single choice — and the CTO justification

**Product analytics: the activation funnel + core engagement events.**

Concretely (design, not built here): a vendor-agnostic `track(event, props)` client abstraction with a
swappable provider — **the exact OBS1 error-reporting pattern** — beaconing to a backend `/api/events`
sink that writes structured, pseudonymous events, with a dev/operator funnel read-back (like the OBS1
`/api/metrics` and RC2.5 cohort endpoints). Instrument the funnel: onboarding step completion → sign-in
→ first read → **measured threshold crossed (the "aha")** → recommendation shown/accepted/completed
(RC2.3 already records this) → return visit. Much of the *post-activation* signal already exists in the
ledger, snapshots, reads, and rec_events; the net-new work is the **pre-activation onboarding funnel** and
a **unified pipeline to read it back**.

**Why this is the highest-ROI action as if it were my own product:**

1. **It is the beta's purpose.** A closed beta is a learning instrument. Launching without funnel
   analytics is running the experiment without recording the results — you get anecdotes, not answers.
2. **The data is unrepeatable.** First-impression onboarding drop-off and first-cohort activation can be
   captured **only** at launch. Everything else (load tests, migrations, refactors) can be added later
   with no loss; this cannot.
3. **It validates the last five phases.** We invested RC2.1–RC2.5 in a sophisticated recommendation
   engine on the faith that users would engage. Analytics is how we find out — otherwise that investment
   is unmeasured.
4. **It is low-risk and architecturally consistent.** Additive instrumentation, no behavior change, and
   it **reuses OBS1's proven vendor-agnostic beacon/provider seam** — so it's a small, well-understood
   build, not a new architecture.
5. **It sequences everything else.** You cannot optimize onboarding (E), justify the learning loop (L),
   or prioritize post-beta work without the funnel data this produces. It is the enabler at the top of
   the dependency graph.

**One honest caveat, handled by the launch checklist, not by changing the choice:** before inviting real
users, also (i) point an uptime monitor + 5xx alert at the OBS1 endpoints (B — ~30 min of ops config) and
(ii) run one 150-VU load smoke (C — a day). These are cheap, non-implementation safeguards; they do not
compete with analytics for the "single workstream" slot. If the app fell over at 150 users it would be a
disaster — but that failure mode is *low-probability at closed-beta concurrency and now monitorable*,
whereas launching without analytics is a *certain* loss of the beta's value.

**Net:** ship product analytics before the first invite. Observability told us the beta won't be
*invisible*; analytics is what makes it *worth running*.

---

*Read-only strategic review. No code was written.*
