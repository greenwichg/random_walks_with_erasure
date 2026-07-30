# Onboarding Lifecycle & Architecture

How a reader becomes a Hidden View user, where that decision is made, and why it is made **there**
and nowhere else.

This document covers the *entry* lifecycle — visitor → account → onboarded. What happens after it,
the Estimate → Measured ladder, is [`PROGRESSIVE_JOURNEY.md`](PROGRESSIVE_JOURNEY.md).

---

## 1. The bug this architecture fixes

Onboarding had **two entry paths and one initializer**.

| Path | What it did | Left the reader |
|---|---|---|
| `/onboarding` → pick outlets → estimate → `/signin` | stashed the selection, signed in, flushed it to the account | onboarded ✅ |
| `/signin` directly | signed in, returned to `callbackUrl: "/"` | in the app with **no outlets and no reads** ❌ |

The second path is not hypothetical or rare. `/signin` is a public route and it is where three real
flows land:

- a **beta invite** ("here's the link, sign in"),
- an **`?error=AccessDenied` bounce** off the beta allowlist, which NextAuth sends to `/signin`,
- a **bookmark** or shared link, since the funnel's own last step is `/signin`.

`middleware.ts` guards the app pages, but `withAuth` only answers *"are you signed in?"*. Nothing
asked *"have you onboarded?"* — so a reader from path 2 reached every personalised surface with an
empty account. That is the state in which the Health Report, Reading History context,
Recommendations, the home health panel and the Guide each fell back to a *different* reader's
numbers. Those fallbacks are fixed at the source (see `is_sample` in `examples/api_fastapi.py`), but
the fallbacks were the symptom. **Reaching the app un-onboarded was the cause.**

## 2. The lifecycle

One path, whatever the sign-in route — OAuth, invite link, allowlist, direct `/signin`, or any auth
provider added later. Providers plug in below the gate, so a new one inherits it for free.

| State | Store says | Where the reader is sent | By |
|---|---|---|---|
| **Visitor**, no session | — | `/onboarding` (the funnel) | `middleware.ts` |
| **In the funnel**, anonymous | nothing yet | stays; selection held client-side + marker cookie | `onboarding-flow.tsx` |
| **Signed in, flush in flight** | no row *yet* | into the app; `OnboardingSync` lands the selection | the marker cookie (§5) |
| **Signed in, no onboarding, no reads** | `onboarding: absent`, `reads: 0` | `/onboarding` | the gate in `app/(app)/layout.tsx` |
| **Onboarded**, `reads < 5` | row + `reads < 5` | the app, in **Estimate** mode | `PROGRESSIVE_JOURNEY.md` |
| **Established**, `reads ≥ 5` | row + `reads ≥ 5` | the app, in **Measured** mode | idem |
| **Reads but no row** (extension-first, or an account predating the gate) | `onboarding: absent`, `reads > 0` | the app — treated as onboarded | the gate's `reads` clause |

That last row is why the gate reads two facts instead of one. Someone whose reading arrived through
the browser extension has onboarded in substance; bouncing them into an outlet picker would be a
worse bug than the one being fixed.

## 3. Where each decision lives

| File | Answers | Notes |
|---|---|---|
| `web/middleware.ts` | *Are you signed in?* | Unchanged. Sends anonymous visitors to `/onboarding`. |
| `web/app/(app)/layout.tsx` | *Have you onboarded?* | **The gate.** The only place this is decided. |
| `web/lib/onboarding.ts` | the client→server handoff | localStorage key + marker cookie, one owner. |
| `web/components/onboarding/onboarding-sync.tsx` | flushes an anonymous selection post-auth | Withdraws the marker when it lands. |
| `web/components/onboarding/onboarding-flow.tsx` | the funnel + **two** save paths | §6. |
| engine `GET /api/me` | the state the gate reads | `onboarding` + `reads`, one call. |
| engine `POST /api/me/onboarding` | the write | Recomputes and stores the estimate. Idempotent. |

## 4. Why the gate is a server component in the app-shell layout

`app/(app)/layout.tsx` is the one thing every protected page already renders through. Putting the
check there means:

- **One choke point.** A new page under `(app)` is gated the day it is added; there is no list to
  remember to update.
- **No flash.** It is a server component, so the redirect happens before any HTML reaches the
  browser — not a rendered dashboard that jumps away.
- **A loop is structurally impossible.** `/onboarding` lives *outside* the `(app)` group, so the
  destination of the redirect cannot itself be gated. This is a property of the route tree, not a
  guard condition someone has to maintain.
- **One extra engine call.** `GET /api/me` is three keyed reads — the onboarding row by primary key,
  the latest report snapshot, and an indexed `COUNT` over `reads` — plus a JSON parse of that
  snapshot, of which the gate uses none. Next memoizes `fetch` within a render pass, so a page that
  also reads `/api/me` shares the response, and the pending-cookie path (§5) skips the call
  entirely. The residual cost is one keyed query and a small parse on app pages that did not
  previously call it.

Alternatives, and why not:

| Instead | Why not |
|---|---|
| Extend `middleware.ts` | It runs before every request including assets, and would need an engine round trip per request to answer a question about stored state. The session is a stateless JWT; it does not carry onboarding status. |
| Check in each page | N places to forget, and the newest page is always the one that forgets. |
| A client-side `useEffect` redirect | A visible flash of a page the reader shouldn't see, and trivially bypassed with JS off. |
| Put it in the NextAuth `session`/`jwt` callback | Onboarding state changes *after* sign-in; a JWT minted at sign-in would be stale, and forcing a re-mint is a bigger mechanism than the gate. |

## 5. Fails open — deliberately, in two ways

The gate does **not** redirect when the engine is unreachable, and does not redirect when a
selection is in flight.

Compare with the **beta access gate**, which fails *closed*: there, letting the wrong person in is
the harm. Here the harm runs the other way — an engine blip that bounced every signed-in reader into
a funnel they finished months ago would be far worse than the bug being fixed. And a reader who does
slip through un-onboarded now sees honest empty states rather than someone else's data, because that
was fixed separately.

**The blind spot, and the cookie that covers it.** An anonymous visitor picks outlets before an
account exists, so the selection goes to `localStorage` and `OnboardingSync` flushes it once a
session appears. `localStorage` is invisible to a server component. Without help, the primary
acquisition path would break: funnel → `/signin` → back to `/` with the selection still client-side
and no row in the store → the gate sends them through the funnel *again*.

So the funnel also drops a **marker cookie** (`ih_pending_onboarding`, `Max-Age` 30 min,
`SameSite=Lax`), and the gate treats it as "a flush is pending — let them through". Properties that
matter:

- **No payload.** It carries `1`, not a selection, so it grants a short delay and nothing else.
- **Self-healing.** `OnboardingSync` withdraws it the moment the flush lands, *and* whenever it finds
  the marker with no payload behind it. If a flush never lands, the cookie expires and the gate
  re-arms on its own.
- **Set after the payload.** It is a claim about `localStorage`; it must not outrun it.

## 6. Two persistence paths, because there are two situations

| Reader | Path | Why |
|---|---|---|
| **Anonymous** (unchanged) | `localStorage` → `OnboardingSync` → `POST /api/me/onboarding` | There is nowhere server-side to put a selection made before the account exists. |
| **Authenticated** (new) | `POST /api/me/onboarding` directly, then `/` | The account already exists, so the round trip through `localStorage` is pure risk: a stale pending item could clobber the fresh choice, and the sync only fires on the anonymous → authenticated transition, which for this reader never happens again. |

The authenticated path exists because the gate creates a reader who is *signed in and on the
funnel* — a combination that could not happen before. If its `POST` fails, the flow stays on the
estimate screen, says so (`onboarding.saveFailed`), stashes the selection anyway, and re-enables the
button. It deliberately does **not** navigate to `/`: the gate would only send it back, which would
read as a loop. The one case where `/` *would* work — an unreachable engine — is covered by the
stash plus fail-open.

## 7. What was deliberately not built

**No `skipped_at` (or any second onboarding state).** Checked against the code rather than assumed:
the funnel's "See a sample first" button calls `sample()`, which does
`setSelected(new Set(spread))` and builds an estimate from six pre-picked outlets — it *pre-fills*,
it does not skip. Every exit from `/onboarding` runs through the same `save()` carrying `outletIds`,
so "onboarded with no outlets" and "half-onboarded" are states no current journey can reach. A column
for them would be untested, unreachable state.

What would change that: a real *Skip* control that lands a reader in the app with no outlets, or a
multi-step funnel where a partial selection must survive a page close. Neither exists today.

**No new onboarding UI.** No tour, no coach marks, no tooltips. The verified problem was that a
signed-in reader could reach the app without an account being initialised — an architecture bug, not
a comprehension bug. Adding guidance on top of it would have decorated the failure.

## 8. Edge cases considered

| Case | Behaviour |
|---|---|
| Beta invite link → `/signin` → `/` | Gate → funnel. This is the path that was broken. |
| `?error=AccessDenied` bounce, then a successful sign-in | Same as above; no special case needed. |
| Anonymous funnel → Google → `/` | Marker cookie lets them through; `OnboardingSync` flushes (§5). |
| Reader clears storage between the funnel and sign-in | Marker with no payload: `OnboardingSync` withdraws it, the gate re-arms, the funnel is shown again. Correct — there is no selection to honour. |
| Two tabs, both signed in | The flush is idempotent (`POST /api/me/onboarding` overwrites); `OnboardingSync` also guards with a per-mount ref. |
| Extension-only reader (reads, no row) | Passes the gate on the `reads` clause. |
| Accounts created before the gate existed | Same clause: any reading at all is enough. An account with neither reads nor outlets sees the funnel once, which is the correct outcome. |
| Engine down | Fails open; the app renders with honest empty states. |
| Signed in, visits `/onboarding` directly | Sees the funnel and saves through the authenticated path. Nothing forces them out — `/onboarding` is public by design. |

## 9. Tests that hold it in place

| Test | Locks in |
|---|---|
| `tests/test_api_fastapi.py::test_me_carries_the_two_facts_the_onboarding_gate_reads` | `/api/me` carries `onboarding` **and** `reads`; a fresh account reports `reads: 0`, and reading alone clears the gate without an onboarding row. Fails on the pre-fix engine. |
| `web/lib/onboarding.test.ts` | The marker cookie's name (the gate looks it up by name), its TTL, `SameSite`/`Secure`, and that clearing actually expires. |
| `web/e2e/specs/auth.spec.ts` — *"signing in without onboarding lands on the funnel, not the app"* | The bypass itself: sign in at `/signin` with no prior funnel → `/onboarding`. |
| … *"completing the funnel then signing in lands in the app, not back at the funnel"* | The regression the marker cookie exists to prevent. |
| … *"an onboarded reader goes straight to the dashboard"* | The gate not over-firing — the failure mode worse than the bug. |
| `web/e2e/fixtures.ts` | `authedPage` is an **onboarded** reader, which is what all nine feature specs were always testing. |

The e2e suite needs a built web app and a live engine: `cd web && npm run e2e`.

## 10. What to measure before adding onboarding UI

The activation events already exist ([`PA1_PRODUCT_ANALYTICS.md`](PA1_PRODUCT_ANALYTICS.md)):
`onboarding_started` → `onboarding_step_completed` → `source_connected` → `signin_started` →
`account_created` → `article_read` → `health_report_viewed` (`mode=measured`).

Read the funnel *after* this fix, because until now the denominator was wrong: readers who arrived
through `/signin` were counted as accounts while never having connected a source. Specifically:

1. **`source_connected` ÷ `account_created`** should now be ~1.0. If it isn't, someone is still
   reaching the app un-onboarded and the gate has a hole.
2. **`onboarding_started` → `source_connected`** is the funnel's real drop-off. If readers abandon
   *inside* the picker, that is a UI problem and coach marks or tooltips are worth discussing.
3. **`article_read` after onboarding** — if readers onboard and never read, the gap is between
   onboarding and the first read, which no amount of onboarding UI fixes.

Only (2) argues for tour-style UX, and only with numbers attached.
