# Hidden View — Architecture & Code Quality Review (2026-07-27)

Every claim below is grounded in a measurement taken from the tree at `e2cbf78`. Where the codebase
is already well designed I say so explicitly, because a review that only lists problems
mis-prioritises the work.

## Scale

| Area | Files | Lines |
|---|---:|---:|
| Engine (`examples/`) | 108 | 31,068 |
| Research core (`rwe/`) | 13 | 3,131 |
| Web (`app`+`components`+`lib`+`services`+`hooks`) | 202 | 18,526 |
| Tests | 112 | 23,071 |
| Deploy + Terraform | 29 | 2,263 |
| Docs | 106 | 24 MB |

**Test-to-source ratio 0.74** — 1,545 engine tests, 147 web unit tests, 14 e2e specs. That is
unusually strong for a product at this stage and is the single best thing about this codebase.

---

## Scores

| Dimension | Score | One-line justification |
|---|:--:|---|
| **Architecture** | **7.5**/10 | Clean layering and a genuinely provider-agnostic ingestion seam; undermined by two oversized modules and a misleading `api_server.py` name |
| **Code quality** | **8**/10 | Consistent style, exceptional comment discipline (the *why*, not the *what*), fail-honest doctrine applied uniformly |
| **Maintainability** | **7**/10 | Excellent tests and docs; hurt by 3,216- and 2,260-line files and 112 env vars with no schema |
| **Scalability** | **5.5**/10 | Correct for one host; SQLite + single-process pollers + in-memory corpus are hard ceilings, not tunables |
| **Modularity** | **7**/10 | Real seams (`SourceAdapter`, `Store`, presentation modules); leaked by a 43-helper API layer |
| **Security** | **8.5**/10 | OIDC, no static keys, fail-closed prod, secrets host-only, tag-scoped IAM. Deductions: unencrypted EBS, 106 broad excepts |
| **Performance** | **7**/10 | Ingestion is 2.91 ms/article; queries are SQL-side and indexed. Frontend ships ~390 kB first-load with zero code splitting |
| **Technical debt** | **Moderate, well-managed** | Debt is *documented* rather than hidden — the rarest good sign |

---

## What is genuinely well designed (do not "fix" these)

1. **The ingestion seam.** `SourceAdapter` → `SourceRegistry` → `MultiSourcePoller` → one terminal
   `ingest_entries`. Nine providers, and downstream code cannot tell them apart. The
   `KeyedJSONAdapter` chassis reduced a new provider to ~40 lines. This is textbook.
2. **Fail-honest signal doctrine.** Unknown lean serialises `null`, never a default; unrated outlets
   cast no votes; modules omit rather than thin-render. It is applied *consistently* across engine,
   API, and web — a rare achievement, and the product's differentiator encoded in code.
3. **The `Store` boundary.** 88 methods, all filtering/sorting/paging in SQL, 17 indexes, no query
   inside a loop found in the service layer. No ORM leakage above it.
4. **Ratchet tests.** `request-params.test.ts` / `discover-params.test.ts` use `Required<T>` so
   adding a field fails typecheck until it is wired into cache identity. This class of test prevents
   whole bug families.
5. **Deployment ops.** Idempotent scripts, readiness gates, smoke tests, declarative
   `deployment-rules.json` drift guard, pre-deploy snapshots, automatic rollback. Better than most
   Series-A infrastructure.
6. **Comment culture.** Comments explain decisions and record incidents (`_compose.sh` documents the
   alias-expansion outage in-file). This is institutional memory that survives staff turnover.

---

## Findings by area

### Architecture

- **`api_fastapi.py` is 3,216 lines with 64 routes and 43 private helpers.** The API layer holds
  business logic that belongs in services (`_report_for`, `_notification_view`, evidence assembly).
  *Evidence:* `grep -c "^def _" = 43`. This is the #1 structural issue.
- **`api_server.py` (2,019 lines) is misnamed.** It contains the `Backend` class, `DatasetProfile`,
  `_Corpus`, `_Recommenders`, and serialisation helpers — it is the **domain/service layer**, not a
  server, and is imported by 8+ modules. New contributors will look for routes here and find none.
- **`store.py` is 2,260 lines / 88 methods** — a god-object trending. It mixes catalog, reads,
  users, settings, notifications, rec-events, health, backups, and tokens.
- **No circular dependencies in the production path.** The one cycle (`discover` ↔ `story_service`)
  is broken by a documented lazy import. Verified: no other non-stdlib deferred imports in serving
  modules.
- **`examples/` is a misleading home for production code.** 108 files mixing the live engine with
  one-off research scripts (`w8a_prototype`, `demo_movielens`, `eval_mind`). A reader cannot tell
  what is load-bearing.

### Backend

- **Configuration is the weakest subsystem.** 112 distinct `RWE_*` variables read at 58 call sites
  across 19 files, with no schema, no central validation, and defaults duplicated inline. A typo in
  a variable name fails silently — the exact class of bug that caused the 2026-07-21 ingestion
  incident and motivated `deployment-rules.json` (a good mitigation of a design problem).
- **Caching is nearly absent.** Only `score_with_cache` (article scoring) and the `Personalizer`
  per-user model cache exist. No caching on `/api/stories` clustering, publisher profiles, or
  facets — all recomputed per request.
- **106 broad `except Exception`** handlers. Many are deliberate (metrics must never break a fetch)
  and commented as such, but the pattern is unbounded and can mask real faults.
- **Error handling at the API is strong:** 60 of 64 routes declare the typed `_ERR_RESPONSES`
  envelope.
- **Background jobs run in-process** as daemon threads inside the API container. Simple and
  observable, but a poller crash-loop and the request path share a process and a GIL.

### Frontend

- **97 of 118 components are `"use client"` (82%) and there are ZERO dynamic imports.** The App
  Router is being used as a client-side SPA. *Evidence:* first-load JS is 289–393 kB per route,
  87.5 kB shared. `/report` (22.4 kB page, 388 kB first load) and `/` (393 kB) are the worst.
- **State management is appropriate** — React Query for server state, `useState` for local, one
  context (i18n). No premature Redux/Zustand. Correct call.
- **Presentation logic is properly extracted** into pure, node-testable modules (`lib/*-presentation.ts`,
  `history-insights.ts`, `home.ts`). This is the frontend's best trait.
- **Zero component tests.** 16 `lib/*.test.ts` files cover pure logic; no rendering, interaction, or
  hook tests. e2e (14 specs) covers flows but not component contracts.
- **Accessibility is partial:** 43 of 94 component files use `aria-*`/`role`. Good patterns exist
  (spectrum bars carry real text, not colour alone) but coverage is inconsistent.
- **`settings/page.tsx` is 579 lines** — the largest frontend file, mixing form state, diffing, and
  layout.

### Infrastructure

- **Terraform is import-only with a plan-to-zero guarantee** — disciplined and unusual. Flat
  10-file layout is appropriate at this size; module extraction would add ceremony without benefit.
- **CI is comprehensive** (6 jobs, real-stack e2e, image builds, compose + rules validation);
  **CD is OIDC→SSM with snapshot + rollback**. No static cloud credentials anywhere.
- **Docker images are single-stage.** `Dockerfile.web` (22 lines) copies the full `node_modules`;
  a multi-stage build with Next's standalone output would cut image size substantially.
- **EBS root volume is unencrypted** (`encrypted = false`, documented as deliberate at import time).
  It holds the entire user database. This is the top security item.
- **Secrets are host-only** (`deploy/.env`, 600, gitignored, fail-fast `${VAR:?}` guards). Correct.

### Testing

- **1,545 engine tests** with strong behavioural focus (semantics, not implementation).
- **Six production-path modules have no direct tests:** `error_reporting`, `obs_metrics`,
  `improvement_ledger`, `improvement_ranking`, `narrate_report`, `product_analytics`. Some are
  exercised indirectly via API tests, but none has a dedicated contract.
- **No load/performance tests** anywhere; no concurrency tests around the SQLite writer.

### Performance & scalability

- **Ingestion is cheap:** 2.91 ms CPU/article measured — 0.02% of a vCPU at current volume.
- **The hard ceilings are structural**, not tunable: SQLite single-writer, corpus held in memory and
  rebuilt per poll cycle, pollers in the API process, one host with no horizontal path.
- **Storage retention remains the nearest operational cliff** (documented separately in
  `CAPACITY_AND_COST.md`): 48 hourly full-copy backups exhaust the 30 GiB volume in ~3.5 weeks.

---

## Top 20 improvement opportunities (ranked by impact ÷ effort)

| # | Opportunity | Impact | Effort |
|---|---|---|---|
| 1 | Apply retention + backup caps + S3 lifecycle (config only) | Prevents disk exhaustion; saves ~$600/mo at 12 mo | S |
| 2 | Centralise configuration into a typed, validated settings module | Kills the silent-typo bug class | M |
| 3 | Split `api_fastapi.py` into route modules by domain (stories/publishers/coach/me/meta) | Unblocks parallel work; shrinks the biggest file | M |
| 4 | Rename `api_server.py` → `backend_service.py`; move helpers to services | Removes the worst naming trap | S |
| 5 | Encrypt the EBS root volume (snapshot → new encrypted volume) | Closes the top security gap | M |
| 6 | Split `examples/` into `engine/` (production) + `research/` (offline) | Makes load-bearing code obvious | M |
| 7 | Add dynamic imports for heavy routes (`/report`, `/analytics`, `/`) | ~30–40% first-load reduction | S |
| 8 | Decompose `store.py` into repositories (catalog, users, notifications, ops) | Ends god-object growth | L |
| 9 | Cache story clustering + publisher profiles (TTL, in-process) | Largest API latency win | M |
| 10 | Component tests for the shared UI primitives | Covers the biggest untested surface | M |
| 11 | Contract tests for the 6 untested production modules | Removes blind spots in serving code | M |
| 12 | Multi-stage `Dockerfile.web` with Next standalone output | Faster deploys, smaller images | S |
| 13 | Move pollers out of the API process (separate container) | Isolates ingestion from serving | M |
| 14 | Registry `kind` + `lean_policy` schema (Phase-1 roadmap) | Unblocks the 55% unrated problem | M |
| 15 | Audit the 106 broad excepts; narrow or annotate each | Stops masking real faults | M |
| 16 | Accessibility pass to reach parity across components | Inclusion + legal posture | M |
| 17 | Split `settings/page.tsx` into sections | Removes the largest frontend file | S |
| 18 | Add a maintenance/error page at the edge | Better failure UX | S |
| 19 | Load test the API at 10× current traffic | Finds the real ceiling before users do | M |
| 20 | Prune/index the 106-file, 24 MB docs tree | Docs are becoming hard to navigate | S |

---

## Prioritised roadmap

### Critical — do before any new feature work
1. **Storage retention + backup caps + S3 lifecycle** (#1). A dated, arithmetic-certain outage.
2. **Configuration consolidation** (#2). Every new provider/flag widens the silent-failure surface.
3. **EBS encryption** (#5). The database is unencrypted at rest today.

### High — do within the next two milestones
4. `api_fastapi.py` split (#3) and the `api_server.py` rename (#4) — cheap, and they compound.
5. `examples/` → `engine/` + `research/` (#6).
6. Frontend code splitting (#7) — one afternoon for a large user-visible win.
7. Tests for the six untested production modules (#11).
8. Registry `kind`/`lean_policy` schema (#14) — the unlock for recommendation coverage.

### Medium — the next quarter
9. `store.py` repository split (#8) · caching layer (#9) · component tests (#10) · poller isolation
   (#13) · multi-stage web image (#12) · broad-except audit (#15) · accessibility parity (#16).

### Low — opportunistic
10. `settings/page.tsx` decomposition (#17) · maintenance page (#18) · load testing (#19) · docs
    pruning (#20).

---

## Technical debt assessment

**Moderate and, unusually, well-managed.** The distinguishing feature of this codebase is that its
debt is *documented rather than hidden*: incidents are recorded in the files where they occurred,
deliberate exceptions are labelled as such, and known limitations (Google News redirect URLs,
MediaStack's monthly quota, the unrated-publisher share) are written down instead of discovered
later. That is worth more than a lower absolute debt figure.

The debt that matters is **structural, not stylistic**: four files (`api_fastapi`, `store`,
`api_server`, `sources`) hold 27% of the engine, configuration has no schema, and the frontend has
not yet used the framework it is built on. None of it is urgent *today*; all of it gets more
expensive with every feature added on top.

**Verdict: this is a well-built product codebase carrying normal early-stage structural debt, with
an exceptional test and documentation culture that makes the debt safe to pay down incrementally.**
The single most valuable habit to preserve is the one already in place — every change lands with
tests, docs, and an honest note about what it does not do.
