# GDELT ingestion failures — investigation and fix (2026-07-27)

## Reported symptom

GDELT succeeding ~60% of the time, "the rest failing due to SSL handshake errors or request
timeouts."

## The premise was wrong

**There are no SSL failures, and no timeouts.** Every recorded failure is `HTTP 429: Too Many
Requests`. Measured from `feed_health`, which is cumulative and survives log rotation:

```
gdelt://doc: polls=314 ok=188 failed=126 (59.9%)
  avgLatencyMs  = 48,882      lastLatencyMs = 73,567
  lastError     = HTTPError: HTTP Error 429: Too Many Requests
gdelt://gkg: polls=128 ok=128 failed=0 (100.0%)
```

The 60% figure was right; the cause was not. (An earlier note in
`docs/STORY_PIPELINE_AUDIT.md` describing this as "~40% SSL timeouts" was unevidenced and has been
corrected.)

Two numbers reframe it. The DOC timeout is **15 s**, yet the average *cycle* takes **48.9 s** — that
is not network latency, it is our own retry backoff sleeping. And GKG, hitting the same provider
from the same host in the same period, is **128/128 healthy** — which rules out EC2 networking, DNS,
TLS and GDELT-wide instability in one stroke.

## Root cause: we polled GDELT ~19× harder than the design intends

```
RWE_GDELT_GKG_WINDOWS=96        ← steady-state default is 4
RWE_GDELT_GKG_INTERVAL=900
```

`gdelt_gkg._windows()` defaults to **4** (one hour of lookback). 96 is the *one-time* cold-start
depth, and `enrich_from_latest` performs **one HTTP download per window**:

| | Configured (96) | Intended (4) |
|---|---:|---:|
| Requests per cycle | 97 | 5 |
| **Requests per day** | **9,312** | 480 |

Each window is a multi-megabyte GKG zip, so this is also tens of GB/day from one EC2 IP. For scale,
the DOC adapter itself makes ~106 requests/day — **GKG was ~99% of our GDELT traffic, and the
endpoint being throttled was the one making 1% of it.**

The configuration also defeated its own safety net: `RWE_GDELT_GKG_BACKFILL_WINDOWS=96` exists so
you *never* need to set `WINDOWS` by hand, and the guard `_backfill_windows() > windows` is false
when both are 96 — so the manual override replaced an automatic one-shot deep scan with a permanent
one.

### The amplifier

`_get_json` treated 429 as retryable and slept 5/10/15 s: **four requests where one was refused**,
during exactly the window we were being throttled, and ~30 s of dead time per cycle.

## What was NOT the cause

Ruled out with evidence, so they are not re-investigated:

- **Timeouts / TLS / DNS** — zero such errors in 314 polls.
- **HTTP/2 vs 1.1** — urllib is 1.1-only; GDELT does not require 2.
- **Connection reuse** — genuinely absent, but irrelevant at one request per 30 minutes.
- **EC2 network / GDELT instability** — GKG was 100% healthy on the same host, same period.

## The fix

**1. Retry policy split by failure class** (`sources._request`, now shared by `_get_json` and
`_get_bytes`):

- **429** — retried *only* when the server sends a `Retry-After` we are willing to wait for
  (`RWE_SOURCE_RETRY_AFTER_MAX`, default 120 s); otherwise it raises at once. A background poller's
  next scheduled cycle is the right retry. Rate-limit hits are still counted through `on_transient`.
- **5xx** — genuinely transient; retried with exponential backoff + half-jitter.
- **Connection-level failures** — SSL handshake, DNS, connect/read timeout, reset, truncated body —
  now retried too. **They previously escaped the loop entirely**: only `HTTPError` was caught, and
  `URLError` is its *parent*, not its child, so an SSL or timeout failure was never retried however
  high `RWE_SOURCE_RETRIES` was set. Not the current failure mode, but a latent gap on every keyed
  provider, not just GDELT.

**2. Adaptive polling** (`MultiSourcePoller._effective_interval`). A provider's interval doubles per
consecutive failure (capped by `RWE_SOURCE_BACKOFF_STEPS`, ceiling `RWE_SOURCE_MAX_INTERVAL`,
default 6 h) and returns to the configured cadence the moment a cycle succeeds. When the refusal is
a rate limit, polling on schedule is what sustains it. `consecutive_failures` was already counted by
`record_feed_health`; this is its first consumer.

**3. A guard on the window cost** (`GDELTGKGEnricher._warn_if_window_cost_is_high`). One loud
`gkg_window_cost_high` line at startup when the steady-state lookback is left at backfill depth,
naming requests/cycle and requests/day. This setting is uniquely easy to leave behind — nothing else
about the system changes when you do.

## Operator action still required

The code default is already 4; **production's `deploy/.env` overrides it to 96**. That override is
the actual cause and code cannot fix it:

```bash
sed -i 's/^RWE_GDELT_GKG_WINDOWS=.*/RWE_GDELT_GKG_WINDOWS=4/' deploy/.env
grep RWE_GDELT_GKG_WINDOWS deploy/.env
bash deploy/ops/restart.sh api
```

## Expected effect, and the honest caveat

That DOC is throttled because of GKG's volume is an **inference**. DOC is `api.gdeltproject.org`;
GKG is `data.gdeltproject.org`. The supporting evidence is strong — unmetered bulk file serving is
100% healthy while the metered API 429s — but the alternative is that the DOC API has its own limit
we would hit anyway.

The config change is the experiment. Re-read `feed_health` a few hours after applying it:

- **Success rate recovers toward 90%+** → volume was the cause.
- **Unchanged** → the DOC limit is independent, and the next lever is DOC's own cadence
  (`RWE_GDELT_POLL_INTERVAL`), which adaptive polling now widens automatically while it is failing.

Either way, cycle latency should drop from ~49 s to a few seconds immediately, because that number
was almost entirely our own `time.sleep`.

---

# Correction and root cause (2026-07-27, later the same day)

**The section above is wrong where it says "no timeouts".** It was written from `feed_health`'s
`lastError`, which records only the exception that *ended* a cycle — not the ones retried past on
the way there. A cycle that times out twice and then gets a 429 records `429`, and every timeout
before it is invisible. `avgLatencyMs = 48,882` was the clue and it was misread: two 15 s read
timeouts plus backoff plus an 11 s refusal is ~48 s. The latency was the timeouts. The `lastError`
was the 429. Both numbers were in the same table.

## What the endpoint actually does

Ten live samples against `api.gdeltproject.org/api/v2/doc/doc`:

| outcome | latencies |
|---|---|
| `429` refusals | 9.4, 10.4, 10.8, 11.8, 11.9, 12.1, 12.7 s |
| `200` successes | 13.5, 14.7, 15.3 s |

Two facts follow, and together they are the whole diagnosis:

1. **GDELT is slow whatever the answer.** Even a refusal takes ~11 seconds. This is not a rate
   limiter — a rate limiter rejects in milliseconds. The request is queued and worked before it is
   refused.
2. **It refuses faster than it succeeds.** Every refusal returned in under 12.7 s; every success
   took over 13.5 s.

`RWE_GDELT_TIMEOUT` was never present in `deploy/docker-compose.yml`, so it sat at the code default
of **15.0 s** — directly between those two clusters. The timeout was therefore a filter that
**preferentially discarded successes**: a response GDELT would have completed at 15.3 s was killed
at 15.0 s and booked as a failure, while every 11 s refusal came back intact. The 58% success rate
was substantially our own configuration selecting against the outcome we wanted.

## What it is NOT

Ruled out by measurement, so nobody re-investigates them:

* **Not retry behaviour.** GDELT's 429s carry **no `Retry-After`**, so `_request` already raises on
  the first refusal — one request per refused cycle, no ladder. There were no unnecessary retries
  to remove.
* **Not query complexity.** A single-term query (`climate sourcelang:english`) 429'd at the same
  rate as the five-term OR query, and its successful response took **15.3 s** — *longer* than the
  complex query's 13.5 s.
* **Not request volume.** Cutting GKG from 9,312 to 480 requests/day (95%) left DOC's success rate
  unchanged at ~58–60%. If volume drove the 429s, that would have moved them.

## Fix

* `RWE_GDELT_TIMEOUT` default 15 s → **45 s**, and made explicit in compose with the distribution
  recorded beside it. Its absence is what made this invisible for two investigations.
* `RWE_SOURCE_MAX_WAIT` (60 s): a total sleep budget per request. Retry count and the per-wait
  ceiling did not bound wall-clock time between them — 3 retries × a 120 s `Retry-After` is six
  minutes of one poller thread.

## Expected impact, and how to check it

The 429s are GDELT shedding load and will not go away; they are not our defect. What should change
is that slow *successes* stop being thrown away. Watch `feed_health` for `gdelt://doc`:

* **success rate** should rise from ~58%; the honest expectation is the fraction of requests GDELT
  actually completes, which the samples put somewhere near 30–70% and variable.
* **avgLatencyMs** should FALL sharply — not because requests got faster, but because cycles stop
  spending 15 s on a timeout, sleeping, and trying again. A cycle is now one request.
* **consecutiveFailures** should stop reaching 6. That matters: at 6 the poll interval had doubled
  to the 6-hour `RWE_SOURCE_MAX_INTERVAL` ceiling, cutting DOC from ~28 ingests/day to ~2.

If `consecutiveFailures` still ratchets into multi-hour intervals after this, the next thing to fix
is the backoff itself — it doubles on *consecutive* failures, which assumes failures are correlated.
GDELT's are closer to independent, so runs happen by chance and park a source that would have served
us on the next attempt. Rate-based backoff would fit the source better. Deliberately not changed
here: one variable at a time, and the timeout is the one with evidence behind it.
