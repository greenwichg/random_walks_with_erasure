# AI Coach Launch Readiness

Canonical launch guide for the **deterministic AI Coach** (Coach v2, milestones M0–M6).
Documentation only — this file describes the system as built; it introduces no new
architecture. Primary sources: the launch-readiness review (2026-07-12), the approved design
(`docs/COACH_REDESIGN.md`), and the milestone commits listed in §2.

Terminology used throughout, to keep categories unambiguous:

- **Launch blocker** — must be true before the launch gate (§8) is passed.
- **Accepted technical debt** — known, bounded, deliberately carried through launch.
- **Intentionally deferred** — designed, scoped, and consciously postponed to post-launch (§7).

---

## 1. Executive Summary

**Verdict: the deterministic AI Coach is production-ready without an LLM**, contingent on one
already-planned gate: the M8a beta validation (walk every intent leaf and both memory flows
against the real corpus, then review the telemetry). No launch blocker was found in the
launch-readiness review; the remaining pre-launch items are documentation-sized, and this
document closes the largest of them (the ops/runbook gap).

**Why the coach is ready without an LLM.** Every property that could block a launch holds on
the deterministic path alone:

- Replies are question-aware (the defect v1 had is fixed) and every number in every reply is
  traceable to a named engine surface via its citation `source`.
- The grounding gate makes fabrication structurally impossible: a composed number absent from
  the evidence replaces the whole reply with the citation fact-list.
- Measured latency has an order of magnitude of headroom (§5, worst case 17 ms median).
- Rollback is one environment variable and returns byte-identical v1 behavior — proven by
  characterization tests, not asserted.

**Why the optional LLM (M7) remains intentionally deferred.** M7 is decorative by
construction: the gate guarantees it can only change prose, never facts. Its absence costs
polish ("Your sourceDiversity is 0/100" reads terse); its presence adds a dependency, latency
variance, cost, and a new failure mode. Those are things to introduce *after* the
deterministic baseline has real-user telemetry to compare against. The insertion point is
already built (`RWE_COACH_LLM` flag, `compose()` fallback reason, identity-except-content
contract), so deferral carries no architectural risk.

---

## 2. Architecture Status

### Completed milestones

| Milestone | Commit | Delivered |
|---|---|---|
| M0 | `24d6971` | Characterization tests pinning the v1 coach contract (the byte-identity baseline) |
| M1 | `1a558e3` | Intent router + registry: 6 families, rule cascade, structured-echo binding (pure, unwired) |
| M2 | `411126e` | 11 read-only tools — typed `ToolResult` evidence from existing engine surfaces, parity-tested |
| M3 | `0e280c9` | Composer + grounded templates + grounding gate + `coach_turn` (offline goldens) |
| M4 | `e16cdd6` | Flag-gated API wiring (`RWE_COACH_V2`) + structured per-turn observability |
| M5 | `e8d69ff` | Progressive web surface: echo plumbing, follow-up chips, `RecommendationCard` reuse |
| M6 | `3622717` | Proactive greeting: deterministic trigger ladder + shadow triggers (log-only) |

Registry: **16 intent leaves** (15 router-reachable + `COMPARE.weekly_review`, proactive-only)
composed from **11 tools**. One production consumer (`examples/api_fastapi.py`), pinned by
test.

### Architectural invariants

**D0 (the prime invariant, binding on every milestone):**

> The AI Coach must never compute recommendations, scores, forecasts, explanations, or
> metrics. It may only orchestrate and explain outputs produced by existing engine
> components. If the required evidence is unavailable, it must explicitly acknowledge that
> rather than infer or fabricate information.

Enforcement is structural, not conventional:

- every tool is a thin wrapper over a named engine function (no arithmetic in
  `coach_service.py` beyond formatting/rounding of engine outputs);
- tool parity tests prove each citation equals the surface it mirrors;
- a failed or absent tool renders as an **admitted gap**, never as inference;
- the echo is versioned, **binding-only, never citable** — every stated number is recomputed
  each turn; an unknown echo version is ignored wholesale.

### Read-only guarantees

A coach turn performs zero store writes — proven by row-count tests across all tables for
full tool runs (M2) and all five greeting ladder branches (M6). The only persistence on any
coach path is the engine's own report-snapshot behavior, identical to `/api/report`. Goal
persistence, when it ships, goes through the existing settings flow on explicit user
confirmation only.

### Deterministic behavior

No LLM dependency exists anywhere in the M0–M6 path (`RWE_COACH_LLM` exists but is inert
until M7). Routing is a rule cascade; plans are data; templates are static format strings
over a presentation namespace; selection rules are deterministic with defined tie-breaks;
the greeting ladder is a pure function of stored state. Determinism is pinned by tests
(identical repeated turns, identical greeting reruns).

### Grounding guarantees

`compose()` enforces: every number in the reply must exist in the evidence (citations +
facts + presentation namespace). Violations replace the reply with the always-safe citation
fact-list (`fallback = "gate"`); missing template evidence does the same
(`fallback = "missing_evidence"`). The gate is adversarially tested (a doctored template with
a fabricated number never ships it).

### Rollback strategy

Unset `RWE_COACH_V2` (or set `0`) and restart the engine. The wire behavior returns to v1
**byte-identically**: additive response fields are Optional + `response_model_exclude_none`,
the v1 code paths are untouched, and `tests/test_coach_v1_contract.py` (M0) plus explicit
leak/telemetry-absence tests prove it. No data migration exists in either direction (the
coach owns no state).

---

## 3. Launch Checklist

Status as of 2026-07-12. Unchecked items are the open launch gate, not discovered blockers.

- [x] **M0–M6 complete** — commits in §2; each milestone's Definition of Done met.
- [x] **Full test suite passing** — 1,061 passed (`RWE_COACH_V2` unset), of which 122 are
      coach tests (M0 5, router 57, tools 14, conversations 22, API 9, greeting 15).
- [ ] **M8a beta validation complete** — walk all 15 routable leaves + the two memory flows
      ("suggest → yes → why the first one", repeat-question) on the real beta corpus; review
      `coach_turn` / `coach_greeting` logs (intent mix, resolution, fallback rate, shadow
      payloads). **This is the launch gate.**
- [x] **Ops/runbook available** — §4 of this document.
- [x] **Rollback verified** — M0 suite green with the flag unset on every milestone commit;
      explicit no-leak + telemetry-absence tests.
- [x] **Telemetry verified** — event emission asserted by tests; live lines observed in the
      M5 E2E and the beta engine log.
- [x] **Performance verified** — measured at beta scale (§5): 2–17 ms turns, 6.8 ms warm /
      57 ms cold greeting.
- [x] **Documentation updated** — this file + `docs/COACH_REDESIGN.md`. *(Accepted debt: the
      coach section of `docs/SYSTEM_ARCHITECTURE_GUIDE.md` still describes v1; slated for the
      M8b cleanup.)*
- [x] **No outstanding launch blockers** — per the launch-readiness review; the two optional
      pre-launch niceties (lexicon rows for `reportingRatio`/`confidence`) are accepted debt,
      not blockers.

---

## 4. Operational Verification

### Feature flags

| Flag | Default | Effect |
|---|---|---|
| `RWE_COACH_V2` | **off** | Master switch. On: the MEASURED (personal) path routes `POST /api/coach` through the intent-routed coach and `GET /api/coach` through the greeting ladder. Off: v1 everywhere, byte-identical. |
| `RWE_COACH_LLM` | **off** | Reserved for M7. **Currently inert** — setting it changes nothing in M0–M6. Independent of `RWE_COACH_V2` by design; a configured API key never opts the coach into LLM output. |

Truthy values: `1`, `true`, `yes`, `on` (case-insensitive). The flags are read per request —
no restart is needed to flip them in-process, but the beta notebook sets them at engine start.

### Rollout procedure (Colab beta)

1. Update the checkout in place — cell 1 of `deploy/information_health_colab.ipynb` fetches
   and hard-resets without touching the gitignored `data/` (the SQLite DB survives). Never
   delete `/content/app` on a runtime that holds real data.
2. Cell 2: leave the **COACH_V2** form toggle checked (default) and re-run — it kills the old
   engine, exports the flags, and starts the new engine. The cell prints the active coach
   mode as an unmissable diagnostic.
3. Cell 3: re-run to rebuild the web bundle; it kills the stale `next-server` before starting
   the new one (a rebuilt bundle cannot silently fail to take the port).
4. Sessions reset when cell 3 rotates `NEXTAUTH_SECRET` — sign in again via the demo button.

### Rollback procedure

Uncheck **COACH_V2** in cell 2 and re-run it (or `%env RWE_COACH_V2=0` before the engine
start). The API returns v1 responses byte-identically; the web tier needs no change (all v2
fields are optional and simply stop arriving). No data cleanup is required.

### Beta verification steps

1. `GET /api/health` — engine up; cell 2 prints `coach: v2 — intent-routed …`.
2. As the signed-in measured reader, ask **"why is my source diversity low?"** — expect a
   metric-specific answer with follow-up chips (not the generic report narration).
3. Reply **"yes, show me"** (or tap the chip) — expect real recommendation cards in the
   transcript, no duplicated plain suggestion rows.
4. Open the coach page fresh — the greeting carries chips; after saving any setting (e.g. a
   reading goal) and reading during the week, the greeting becomes the Weekly Review.
5. `!grep coach_turn engine.log | tail` and `!grep coach_greeting engine.log | tail` — one
   JSON line per turn/greeting with the fields below.
6. Negative check: a below-threshold (or signed-out/demo) session still gets the v1 narrator
   even with the flag on.

### Telemetry events

Logger `ih.api`, one JSON line per event, `requestId` included, **no message content ever
logged**.

`coach_turn` — every v2 reply:

```json
{"event": "coach_turn", "requestId": "…", "intent": "EXPLAIN.metric",
 "resolution": "rule", "tools": ["report", "metric"], "failures": [],
 "fallback": null, "ms": 3.8}
```

- `resolution` ∈ `rule | unresolved` (M7 adds `llm`); `failures` lists tools that raised
  (rendered as admitted gaps); `fallback` ∈ `null | "missing_evidence" | "gate"`.

`coach_greeting` — every v2 greeting:

```json
{"event": "coach_greeting", "requestId": "…", "trigger": null, "intent": null,
 "tools": [], "fallback": null,
 "shadow": {"metricChange": {"snapshots": 2, "wouldEvaluate": true,
                             "prevDate": "…", "lastDate": "…",
                             "values": {"overall": {"prev": 61, "last": 64}, "…": {}}},
            "storyUpdate": {"followedStories": 3, "storiesWithNewCoverage": 1,
                            "unreadNewerSiblings": 2, "wouldFire": true}},
 "ms": 6.8}
```

- `trigger` ∈ `null | "weekly_review_goals" | "weekly_review_recap"`; when non-null, `intent`
  is `COMPARE.weekly_review` and `tools` is `["goals", "history", "trend"]`.
- Shadow payloads are **log-only** (never rendered) and raw-valued by design: `metricChange`
  carries the last two snapshots' values verbatim (no thresholds, no deltas — thresholds get
  chosen from these distributions later); `storyUpdate` counts followed stories with unread
  newer coverage. A shadow that raises degrades to `{"error": "<ExceptionName>"}` and cannot
  touch the greeting.

### Expected behavior by flag state

| State | `POST /api/coach` | `GET /api/coach` | Telemetry |
|---|---|---|---|
| Flag off (any user) | v1 narrator, byte-identical | v1 canned greeting, byte-identical | none |
| Flag on, demo / below-threshold | v1 narrator (unchanged) | v1 greeting (unchanged) | none |
| Flag on, measured | intent-routed reply + additive fields (intent, resolution, followUps, cards, echo; citations gain `source`) | ladder greeting: Weekly Review turn, or v1 body + weakest-metric chips | one `coach_turn` / `coach_greeting` line per request |
| `RWE_COACH_LLM` on (today) | no effect (inert until M7) | no effect | none |

---

## 5. Success Metrics

All metrics below are computable from the two telemetry events — no new instrumentation is
required. Baselines cited are from the launch-readiness review's measurements (synthetic
beta-scale corpus, ~400 feed articles).

### Operational metrics

| Metric | Source | Baseline / expectation |
|---|---|---|
| Turn latency (`ms`, per intent) | `coach_turn.ms` | medians 2.0–17.0 ms (worst: `PROJECT.forecast` 17 ms); greeting 6.8 ms warm, ~57 ms on the once-per-60 s cold story-index rebuild. Investigate sustained regressions, not single cold spikes. |
| Error rate | HTTP 5xx on `/api/coach` (request log) | zero expected — tool failures degrade to gaps, they do not 500 |
| Tool failures | `coach_turn.failures` non-empty | rare; any recurring tool name indicates an engine-surface regression |
| Grounding fallback rate | `coach_turn.fallback` ∈ `missing_evidence`/`gate` | near-zero on template paths; a rising `gate` rate after M7 means the LLM is being caught (working as designed, but watch it) |

### Product metrics

| Metric | Source | What it tells us |
|---|---|---|
| Intent distribution | `coach_turn.intent` | what readers actually ask; informs lexicon/template investment |
| Follow-up usage | consecutive turns whose message equals a prior reply's chip text | whether chips drive the conversation (they are the coach's primary affordance) |
| Recommendation card opens | existing reception signal (`/api/me/recommendations/opened`) attributed alongside `ACT.suggest` turns | whether coach-attached cards get read — the coach's concrete behavioral outcome |
| Clarification rate | `coach_turn.resolution == "unresolved"` | router coverage; a high rate argues for lexicon growth or M7's classify-fallback |
| Weekly Review usage | `coach_greeting.trigger` non-null rate | whether the proactive rung fires at a digest-like cadence or nags (input to the deferred cooldown decision) |

### Quality metrics

| Metric | Source | What it tells us |
|---|---|---|
| Admitted gaps | gap clauses per reply (`failures` proxy) | honesty events; should be rare and transient |
| Shadow trigger frequencies | `coach_greeting.shadow.metricChange.values` distributions; `storyUpdate.wouldFire` rate | THE dataset for deciding the metric-change threshold and whether a visible story trigger would be tolerable |
| Tool execution distribution | `coach_turn.tools` | plan health; unexpected shapes indicate registry drift |

---

## 6. Known Limitations

Genuine limitations of the shipped system — all **accepted technical debt**, none launch
blockers:

1. **Deterministic template prose.** Replies are grounded but terse ("Your sourceDiversity is
   0/100"). This is the M7 target; until then, plainness is the cost of provable grounding.
2. **English-only coach strings** in a five-locale product. Coach `content`, chips, and
   followUps are engine-produced English (a pre-existing v1 condition, not a v2 regression).
   Localization is a deliberate post-launch decision (template catalogs or M7).
3. **Partial metric lexicon.** Six of eight report metrics are askable by name.
   "Topic diversity" degrades gracefully to `ANALYZE.topics`; "reporting ratio" and
   "confidence" land on honest clarification. A small lexicon extension is post-launch work.
4. **Dual v1/v2 path until M8b.** The v1 narrator remains in `api_server.py`; every coach
   change until cleanup must keep both paths in mind. The M0 suite is what makes this safe.
5. **Recap cadence.** The Weekly Review fires on every coach-page visit while its gates hold
   (per the approved scope). Content recomputes each visit (digest semantics), but there is
   no cooldown — deliberately, because a polite cooldown needs either clock-windowing or
   state, and that decision wants real trigger-rate telemetry first.
6. **Deferred proactive triggers.** Metric-change and story-update run in shadow only; the
   greeting never surfaces them. Their visible forms are consciously not part of launch.
7. **Minor code debt.** `IntentSpec.budget` is declared but unenforced (remove or implement
   at M7); the coach section of `docs/SYSTEM_ARCHITECTURE_GUIDE.md` predates v2; telemetry
   events carry `requestId` but not the user id (per-user trajectories need a request-log
   join — acceptable at beta scale).

---

## 7. Deferred Work

Everything below is **intentionally deferred** — designed and scoped, with named re-entry
conditions. Nothing here is required for launch.

| Item | Why deferred | Re-entry condition |
|---|---|---|
| **M7 — optional LLM composer + classifier** (`RWE_COACH_LLM`) | Decorative by construction; adds dependency/latency/cost/failure modes; deterministic baseline should accumulate comparison telemetry first | Post-launch, after M8a review; ships behind its own flag with identity-except-content + gate-fallback tests |
| **Metric-change trigger (visible)** | Threshold would be a guess today | Choose the threshold (likely band-transition form) from `shadow.metricChange` distributions in real logs |
| **Story-update trigger (visible)** | Never self-clears; politeness needs "already offered" memory the read-only greeting refuses; third surface for the same fact | `storyUpdate.wouldFire` rates + story-slot engagement data + an explicit cooldown/memory design decision |
| **`coachGoals` writer** (save-goals affordance via the existing settings flow) | Conversational-UX work, cleaner alongside M7; needs a structured goal schema decision | Post-launch; unblocks the goals rung of the Weekly Review (live and tested, but nothing writes `coachGoals` yet) |
| **Cadence tuning** (recap windowing / cooldown) | Needs annoyance evidence, not intuition | `coach_greeting.trigger` rates from the beta |
| **v1 removal (M8b)** | v1 is the rollback target until v2 is default-on and proven | After default-on: delete `_serialize_coach_reply`/`_serialize_coach_greeting` v1 paths, retire the M0 suite, refresh the architecture guide |
| **Lexicon growth** (`reportingRatio`, `confidence`; localization) | Small, non-blocking | Post-launch, guided by clarification-rate telemetry |

---

## 8. Definition of Launch

"Launch" means **enabling `RWE_COACH_V2` by default** (flag flips from opt-in to opt-out; the
M8b cleanup may follow but is not part of the gate). All of the following must be true first:

1. **M8a complete on the real beta corpus:** every one of the 15 routable leaves exercised
   with a real measured reader; both memory flows verified over the wire (chip → `ACT.suggest`
   → "why the first one?" binds the served card; repeated question acknowledged with fresh
   identical citations); the greeting verified in all three ladder states (default + chips,
   recap, suppressed).
2. **Telemetry review of the M8a walk:** zero unexplained `coach_turn.failures`; grounding
   `fallback` events zero (or each one individually explained); latency medians consistent
   with §5 on real hardware; clarification rate examined against the known lexicon gaps.
3. **Full suite green at the launch commit** with `RWE_COACH_V2` unset — the M0
   characterization suite still passes byte-identically (the rollback property holds to the
   last moment it matters).
4. **Rollback rehearsal on the beta:** flip the flag off, confirm v1 replies and zero coach
   telemetry, flip it back on.
5. **No new launch blockers** discovered during M8a (anything found is fixed or explicitly
   accepted into §6 by the same review process that produced this document).

Explicitly **not** part of the gate: M7, any visible proactive trigger beyond the Weekly
Review, localization, and the M8b deletion.

---

## 9. Definition of Success

Observed over the first weeks after the §8 gate, using only §5 instrumentation:

- **Stability.** Zero coach-attributed 5xx; `failures` empty except for explained transients;
  grounding fallback effectively zero on the deterministic path; no rollback flip needed
  after the rehearsal.
- **User engagement.** The question-blindness fix shows up behaviorally: varied intent
  distribution (not one dominant leaf), follow-up chips actually clicked (chip-text turns
  present in the logs), clarification rate low and attributable to the known lexicon gaps
  rather than router misses.
- **Recommendation usage.** `ACT.suggest` turns attach cards, and those cards get opened at a
  rate comparable to the recommendations page — the coach becomes a second door into the
  same measured reading loop, not a dead end.
- **Telemetry health.** Every turn and greeting emits exactly one well-formed event; shadow
  payloads accumulate enough volume to make the §7 threshold decisions data-driven rather
  than guessed.
- **Rollout confidence.** The Weekly Review fires at a cadence readers tolerate (no
  complaints, stable greeting engagement), giving confidence to expand the proactive ladder;
  the M7 decision can then be made as an A/B against a *known-good* deterministic baseline.

Success is explicitly **not** defined as prose quality — that is M7's metric, measured
against this baseline.

---

## 10. Post-Launch Roadmap

In planned order; every step follows the same evidence-first flag ladder used by
`RWE_FEED_REQUIRE_DATED`, `RWE_STORY_SLOT`, and `RWE_COACH_V2` itself:

1. **M7 — optional LLM composer + classifier** (`RWE_COACH_LLM`, default off): the two
   allowed operations only — rephrase the composed evidence pack and classify the router's
   `unresolved` band; the grounding gate enforces template fallback on any ungrounded number;
   payload identical except `content` (tested with stub LLMs before any key is configured).
2. **Proactive trigger expansion:** promote the metric-change trigger using shadow-derived
   thresholds (band-transition form first); design the offered-memory/cooldown mechanism the
   story-update trigger requires before it may become visible; add cadence windowing to the
   Weekly Review if telemetry shows repetition fatigue.
3. **Richer coaching:** the `coachGoals` writer via the existing settings flow (explicit
   confirmation, structured goal schema), completing the goals rung; then the D10 catalog
   candidates (debate mode, source comparison, timeline replay) — each one registry entry +
   at most one new tool.
4. **Localization:** template catalogs (or gated LLM phrasing) for the five product locales;
   chips and followUps included; the lexicon grows to all eight metrics.
5. **v1 removal (M8b):** flag default-on having proven stable → delete the v1 narrator
   paths, retire the M0 characterization suite (its job is done), refresh
   `docs/SYSTEM_ARCHITECTURE_GUIDE.md`, and collapse this document's checklist into a
   changelog entry.
