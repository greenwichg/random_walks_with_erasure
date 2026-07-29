# Deployment stages — the failure taxonomy

Every deployment failure now names the stage it happened in. This is the reference for what each
stage means, what can fail in it, and — the question that decides whether anyone gets woken up —
**whether the previous deployment was ever stopped.**

## Why

`cd-deploy` used to report every failure identically: *"deploy of `<ref>` failed its health/smoke
gate — AUTO-ROLLED-BACK"*. On 2026-07-29 that message was produced by `git checkout` refusing to
overwrite a locally-modified file. No container had moved. Nothing had been health-checked. The
"rollback" re-deployed the commit that was already checked out.

The visible symptom was a missing database index, which looked like a database problem, and cost a
full round trip to diagnose. **A pipeline that can only say "it failed" makes you re-derive where.**

## The two questions, in order

1. **Is the site up?** → `service_interrupted`
2. **What do I do now?** → `stage` + root cause + recovery

Everything in the report is arranged to answer those first and the forensics second.

## The stages

| # | Stage | What it does | Previous deploy stopped? | Rollback |
|---|---|---|---|---|
| 1 | `PREFLIGHT` | env file, docker daemon, clean worktree, data mount, free disk | **No** | never |
| 2 | `GIT_FETCH` | fetch from origin; verify the ref exists | **No** | never |
| 3 | `GIT_CHECKOUT` | move the working tree to the ref | **No** (mixed: code new, service old) | never |
| 4 | `BACKUP` | pre-deploy DB snapshot (+ integrity check, + S3) | **No** | never |
| 5 | `BUILD` | `dc build` — build images only | **No** | never |
| 6 | `CONTAINER_STARTUP` | `dc up -d` — **the point of no return** | **YES** | automatic |
| 7 | `READINESS` | `/api/health/ready` within the timeout | **YES** | automatic |
| 8 | `SMOKE` | contract checks against the running stack | **YES** | automatic |
| 9 | `ROLLBACK` | restore the previously-serving commit | (already stopped) | — |
| 10 | `SUCCESS` | serving the new ref, smoke green | — | — |

### The boundary that matters

**`dc up -d`.** Every stage before it is read-only with respect to the running stack: the old
containers keep serving even while new code sits checked out on disk. From `dc up -d` onward the
previous deployment has been replaced.

`docker compose up -d --build` **straddles that boundary** — it builds, then recreates. Reported as
one step, a build failure (harmless, fix it tomorrow) and a startup failure (site down, act now) are
indistinguishable. `update.sh` therefore splits it into `dc build` and `dc up -d`, and that split is
the single most useful thing in this design.

### Rollback only runs when the service is actually down

It used to run unconditionally. That is how a refused `git checkout` produced a "rollback" that
redeployed a healthy stack — **stopping containers that were serving perfectly well, turning a
harmless failure into real downtime.** Stages 1–5 now abort with `CD_RESULT=aborted` and touch
nothing.

## What a failure looks like

```
┌───────────────────────────────────────────────────────────────────────────────
│ DEPLOYMENT FAILED
├───────────────────────────────────────────────────────────────────────────────
│ STAGE            BUILD
│ REQUESTED        1272b6b…
│ WAS SERVING      03f547d…
│ SERVICE          NEVER STOPPED — the previous deployment is still running and serving traffic
│ ROLLBACK         NOT NEEDED — the previous deployment never stopped
├───────────────────────────────────────────────────────────────────────────────
│ ROOT CAUSE       one or more images failed to build
├───────────────────────────────────────────────────────────────────────────────
│ EVIDENCE
│   disk:
│       /dev/root  29G  19G  9.1G  68% /
│   docker disk usage:
│       …
├───────────────────────────────────────────────────────────────────────────────
│ RECOVERY
│   1. Read the build output above — the failing step names the service and the command.
│   …
│   NO ROLLBACK IS NEEDED. The previous deployment was never stopped.
└───────────────────────────────────────────────────────────────────────────────
CD_RESULT=aborted ref=1272b6b stage=BUILD service_interrupted=0 rollback=none
```

## The machine contract

One line, parsed by `.github/workflows/deploy.yml`. The four `CD_RESULT` tokens are load-bearing;
everything after them is additive.

```
CD_RESULT=deployed        stage=SUCCESS            service_interrupted=0 rollback=none
CD_RESULT=aborted         stage=<PREFLIGHT|…|BUILD> service_interrupted=0 rollback=none
CD_RESULT=rolled_back     stage=<CONTAINER_STARTUP|READINESS|SMOKE> service_interrupted=1 rollback=ok
CD_RESULT=rollback_failed stage=<…>                service_interrupted=1 rollback=failed
```

`aborted` is the new one and it carries the most operational value: **a failed deploy that never
touched production.** CI still fails the run — it is a failed build — but the alert says explicitly
that nobody needs to be paged.

## Alerts

Every alert now leads with the stage and the service state:

```
cd-deploy [BUILD] deploy of 1272b6b failed BEFORE any container moved.
  Site UNAFFECTED, still serving 03f547d. No rollback attempted. Cause: one or more images failed to build
```

versus

```
cd-deploy [READINESS] deploy of 1272b6b failed AFTER containers were replaced —
  AUTO-ROLLED-BACK to 03f547d, now serving and smoke-green. Cause: the engine did not report ready within 240s
```

The first is a work item. The second is an incident that resolved itself. They used to read the same.

## Failure classification

**PREFLIGHT** — missing `deploy/.env`; docker daemon down; dirty working tree; `IH_DATA_MOUNT=1`
with the volume unmounted (the data-loss guard); less than `CD_MIN_FREE_MB` (2048) free.

**GIT_FETCH** — no egress/DNS/credentials for origin; the requested ref does not exist after
fetching (resolved explicitly, so it is named rather than surfacing as a confusing checkout error).

**GIT_CHECKOUT** — git refuses because a tracked file is locally modified *and* changed by the
target commit. The recovery names `git checkout HEAD -- .`, **not** `git checkout -- .`: the usual
way in is `git checkout <ref> -- path`, which *stages* the file, and restoring the worktree from the
index leaves a staged change exactly where it was.

**BACKUP** — snapshot failed, integrity check failed, or disk full. Deliberately fatal: the snapshot
is what makes the deploy reversible without data risk.

**BUILD** — compile/lint error in new code, base image unpullable, disk full mid-build.

**CONTAINER_STARTUP** — missing/invalid `deploy/.env` value (the engine refuses to boot with
`RWE_ENV=production` and no `RWE_INTERNAL_SECRET`); the one-shot `ingest` service exiting non-zero
(`api` waits on it completing); a host port already bound.

**READINESS** — engine never reports `/api/health/ready` within `RWE_DEPLOY_READY_TIMEOUT` (240s).
Measured cold start on a populated catalog is ~5 s, so the timeout expiring means *broken* rather
than *loaded* — but on a busy box, check whether it came ready just after before rolling back.

**SMOKE** — up and answering but a contract check failed: running and *wrong*, which is worse than
down.

## Tests

`bash tests/test_deploy_stages.sh` — 30 assertions driving the **real** `cd-deploy.sh` and
`update.sh` against a throwaway git repo with a stub `docker`. It asserts each failure names its
stage, reports the service state correctly, and rolls back only when the service is actually down.

It is a shell test because the thing under test *is* the shell: a python harness would not exercise
`set -uo pipefail`, the sourcing order, or the exit-code plumbing — which is where the original
misreport lived.

Two bugs in this design were caught by those tests rather than by review: `stage_fail` originally
exited **without emitting the `CD_RESULT` line**, so every stage failure was invisible to CI; and
the first fixture committed its stubs onto only one of the two commits, so the success case failed
at SMOKE for the fixture's own reason.


## A note on suite flakiness found while building this

Three consecutive full runs of the engine suite failed on three DIFFERENT tests
(`test_demo_determinism`, then two in `test_story_slot`), each of which passes in isolation. That is
test-order pollution, and it is **pre-existing**, not introduced here:

* the same suite at `03f547d` — before any of this work — fails the same way
  (`test_synthetic_profile_demo_report_is_identical_across_restarts`, 1 failed / 2,043 passed);
* disabling the `outlet_registry.resolve` memo, the most plausible new suspect, made it *worse*
  (3 failed), not better.

The memo cannot be the cause on inspection either: `default_registry()` is cached and **nothing
anywhere mutates a live registry's maps** — every write is in `__init__` — so a memoised answer can
never disagree with a recomputed one.

Recorded rather than fixed. It is a real problem worth its own pass (shared module-level state
leaking between tests), and folding it into a deployment-pipeline change would have hidden both.
