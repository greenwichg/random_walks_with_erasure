# AI Coach M8a — Beta Validation Report

**Milestone:** M8a (launch validation — no new capabilities).
**Date:** 2026-07-13. **Verdict: READY FOR LAUNCH** (§12).
**Scope discipline:** two production changes were made, both fixes for defects discovered
during the walk (§9); everything else found is documented under the post-launch roadmap (§11).

## 1. Executive summary

AI Coach v2 was exercised end-to-end **through the public API exactly as a client would** —
46 recorded turns against a beta-replica corpus (215 articles ingested through the real
RSS pipeline), covering all 15 routable intent leaves, the proactive Weekly Review, every
greeting-ladder rung, both approved memory flows, and 13 adversarial follow-up/echo shapes.
The walk found **two genuine defects** (a 500 on a malformed echo; `None` rendered as outlet
names in one drivers line), both fixed minimally and pinned with wire-level regression tests.
The grounding audit found **zero fabricated numbers, articles, causes, or trends**: the
composer's grounding gate ran on all 39 POST turns and never fired (`fallback: None` ×39),
and every card/suggestion article resolved to a real catalog article. Deterministic-pipeline
latency is comfortably inside expectations (median 4.4 ms, p95 20.9 ms). Rollback remains one
environment variable, re-proven live on the same database. Full suite after fixes:
**1,109 passed** with `RWE_COACH_V2` unset.

**Environment constraint (recorded honestly):** live RSS fetch is blocked in this validation
sandbox (proxy 403 on every feed), so "the real beta corpus" was replicated by feeding the
SAME ingest pipeline (`rss_ingest.ingest_entries` → `ingest.Scorer` + baseline enricher) a
215-article, 8-publisher catalog with realistic headline register/emotion spread and two
multi-publisher story clusters. Everything downstream of ingestion — scoring, corpus build,
recommendations, stories, coach — is the production path, byte-for-byte.

## 2. Method

Beta flags replicated (`RWE_COACH_V2=1`, `RWE_STORY_SLOT=1`, `RWE_DEMO_ACCOUNT` set, live-feed
corpus). Six readers replicate the beta population: the locked demo exhibit, a cell-5-style
primary reader (8 reads, 4L/2C/2R incl. a story-cluster member, reading goal set), recap /
stale / goals / fresh readers for each greeting rung (`coachGoals` seeded via the store — it
has no production writer; see §11). Every assertion below is made from the wire response
(`intent`/`resolution` are on the v2 envelope) plus the engine's structured telemetry; no
implementation reading was needed to interpret any reply.

## 3. Part 1 — behavioral walkthrough: PASS

| Check | Result |
|---|---|
| Routing — all 15 routable leaves | PASS — each canonical phrasing routed to its leaf (wire `intent` verified per turn) |
| Entity binding (metric / article / want / mode) | PASS — e.g. `why is my source diversity low?` → `EXPLAIN.metric` cause-mode with drivers line; `why did you recommend <url>` → verdict for that URL |
| Follow-up resolution — pronoun | PASS — `why is it low?` + echo bound the prior metric ("As covered a moment ago — Your emotionalBalance is 100/100.") |
| Follow-up resolution — ordinal | PASS — `why the second one?` + echo → `EXPLAIN.why_article` for card #2 |
| Follow-up resolution — affirmative | PASS — `yes, show me` + echo → `ACT.suggest` with cards |
| Repeated question | PASS — identical grounded content, second turn prefixed "As covered a moment ago —", fresh evidence both times |
| Cards | PASS — cards/suggestions only from `ACT.suggest`/recommendation turns; **every** card URL resolved to a catalog article (0 violations) |
| Citations | PASS — present on every evidence-bearing turn (see §5 for the curation caveat) |
| Admitted-gap behavior | PASS — missing article → "verdict: not_in_catalog — no catalog article with this id/URL", cited, nothing invented |
| Grounding gate | PASS — `fallback: None` on all 39 turns (gate ran, never fired) |
| Echo behavior | PASS — round-trips turn to turn; binding-only; never cited as evidence |
| Clarification path | PASS — ambiguous/cold/unsupported-metric turns → `CHAT.general/unresolved` clarification menu with **zero numbers** |
| Cold conversations | PASS — pronoun/ordinal without echo → clarification, never a guess |
| Expired (stale) echo | PASS with documented semantics — a client sending an old echo binds against that echo's last coach turn (client-carried state is authoritative); no staleness inference is attempted |
| Unknown echo version (`{"v": 99}`) | PASS — ignored wholesale, cold turn |
| Malformed echo (`turns` not a list) | **FAIL → FIXED** — was HTTP 500 (defect D1, §9); now degrades to a cold turn (re-verified over the wire + regression test) |
| Greeting ladder — goals / recap / suppression / default+chips / below-threshold / anonymous | PASS — `weekly_review_goals` and `weekly_review_recap` fire for the right readers; stale reader falls back to the default greeting; fresh/anonymous get the v1-body greeting with weakest-metric chips (`Why is my echo chamber score low?` …) |

## 4. Part 2 — memory flows: PASS

**Flow A** (`how is my source diversity?` → `suggest something to read` → `why the first
one?`): each turn routed correctly (`EXPLAIN.metric` → `ACT.suggest` → `EXPLAIN.why_article`),
the ordinal bound card #1 from the echo, and the final turn produced the engine's real verdict
("recommended — in the served feed via rwe-b. It belongs to story st_… with 10 articles across
8 publishers."), fully cited. **Flow B** (goals greeting → chip text as message → `why the
first one?`): the Weekly Review fired from stored `coachGoals`, its follow-up chip routed as a
normal message, and the explanation turn resolved against the fresh suggestion echo. Evidence
was recomputed every turn (tool runs per turn in telemetry; repeated topics re-cite current
values); no stale citations, no hallucinated context (all numbers gate-checked, all cards
catalog-resolved).

## 5. Part 3 — grounding audit: PASS (no blockers)

- **Invented numbers: zero.** The composer's gate (`numbers(content) ⊆ numbers(evidence)`)
  executed on every turn and never fired. An automated audit additionally extracted every
  numeric token from all 46 responses.
- **Invented articles: zero.** Every card/suggestion URL canonicalized into the catalog.
- **Invented causes/trends: zero.** Trend turns state honest gaps ("no report snapshots
  recorded yet — trends appear once a few reports are stored"); cause turns render only
  evidence-backed drivers.
- **Number → wire-citation traceability: partial, by design — documented, not a blocker.**
  20 of 46 turns contain grounded numbers whose backing citation is not in the wire
  `citations` array, for two reasons: the wire caps citations at 8 (4 turns hit the cap),
  and tools emit *curated key-fact* citations rather than a per-number provenance ledger
  (e.g. per-type feed counts, per-strategy ranks, topic-share percentages). Every such
  number is traceable to tool evidence (gate-guaranteed; `fallback` would read `"gate"`
  otherwise — it never did). Remediation options are deferred (§11.3).

## 6. Part 4 — telemetry review: PASS

39 `coach_turn` events: intent mix spans all 15 routable leaves (`EXPLAIN.metric` 8,
`CHAT.general` 8 — the 7 deliberate clarification probes + greeting-chip echo, `why_article`
6, `suggest` 5, …); resolution `rule` 32 / `unresolved` 7 (all 7 are the intentional
ambiguous/cold/malformed probes); `failures: []` on every turn; `fallback: None` ×39.
7 `coach_greeting` events: triggers `weekly_review_recap` ×2, `weekly_review_goals` ×2,
`default` ×3 — exactly the seeded rungs. Shadow payloads are present, structurally complete,
and never user-visible; notably `storyUpdate.wouldFire: true` appears on most greetings —
useful threshold data for the deferred visible trigger. The two M8a defects were themselves
telemetry-visible (`unhandled_exception` event; `failures`/`fallback` recorded correctly under
injection), which is the observability working as designed. **No telemetry changes needed.**

## 7. Part 5 — performance: PASS

| Metric | Measured | Expectation (deterministic, no-LLM pipeline) |
|---|---|---|
| coach_turn median | 4.4 ms | well under interactive budgets (<100 ms) |
| coach_turn p95 | 20.9 ms | ✓ |
| Worst turn | 70.7 ms (first turn for a reader — augmented-model build) | ✓ explained: one-time per (reader, reads-version) model build; subsequent turns 4–20 ms |
| Greeting median / max | 6.2 ms / 65.6 ms | ✓ same first-build explanation |

HTTP round-trip adds ~2–5 ms over the engine-side `ms`. No deviation requires action.

## 8. Part 6 — failure injection: PASS

| Injection | Result |
|---|---|
| Missing tool (removed from registry) | 200; gap admitted; **zero numbers** in the reply; telemetry `failures: ["trend"]`, `fallback: missing_evidence` |
| Failing tool (raises) | 200; "I can't compute that right now. (Unavailable: report.)"; same honest telemetry |
| Missing article | 200; truthful `not_in_catalog` verdict |
| Malformed echo (post-fix) | 200; cold turn; fresh echo rebuilt |
| Expired/stale echo | 200; binds the provided echo's last coach turn (documented client-state semantics) |
| Unsupported entity | 200; clarification with zero numbers |

No crashes (post-fix), no fabricated content anywhere.

## 9. Issues found, severity, and fixes applied

| ID | Severity | Issue | Evidence | Outcome |
|---|---|---|---|---|
| D1 | **High** (crash on untrusted input) | `POST /api/coach` with echo `{"v":1,"turns":"garbage"}` → HTTP 500 (`str + list` TypeError at the echo-append; `_valid_echo` checked only the version) | engine traceback; walkthrough turn `echo:malformed-turns` | **Fixed** (`coach_service._valid_echo`: `turns` must be a list, else the echo is ignored wholesale). Regression: `test_malformed_echo_turns_degrades_cold_never_500` |
| D2 | **Medium** (wrong user-visible content) | sourceDiversity cause turn rendered "Your most-read outlets: **None** 25%, None 12%…" and citation labels `sourceShare.None` — the report's `sources` rows key outlets under `"source"`, two coach readers used `.get("name")` | first walkthrough transcript; fixed run renders "Associated Press 25%, CNN 12%, Fox…" | **Fixed** (two one-line key corrections in `coach_service`). Regression: `test_source_diversity_cause_names_real_outlets` |

Production diff: **2 files** (`examples/coach_service.py` — 3 lines of behavior across the two
fixes; `tests/test_coach_api.py` — 2 regression tests). Nothing else in the production path
changed. Full suite: 1,109 passed, `RWE_COACH_V2` unset (M0 byte-identity intact).

## 10. Part 7 — production safety: PASS (re-verified live)

- **Flag OFF byte-identical:** engine restarted on the same beta DB without the flag — reply
  and greeting carry zero v2 fields; zero coach telemetry events; the M0 characterization
  suite plus the full 1,109-test run (flag unset) remain the byte-level proof.
- **Demo/anonymous unchanged:** the synthetic demo path stays v1 (pinned suite); with
  `RWE_DEMO_ACCOUNT` set, anonymous traffic receives the exhibit's greeting (v1 body + chips)
  and the exhibit was never mutated.
- **Below-threshold readers:** v1 without a demo account (re-proven live). With the demo
  account configured (beta default), below-threshold readers are served the exhibit's
  *measured* experience — including v2 turns — which is the approved demo-account design, not
  a coach regression; recorded here so launch expectations are configuration-explicit.
- **No writes:** SHA-256 of the database is identical before/after all 46 coach turns.
- **Rollback:** unset `RWE_COACH_V2`, restart — demonstrated, one environment variable.

## 11. Deferred improvements (documented, deliberately NOT implemented)

1. **`coachGoals` writer** (already on the roadmap): the goals Weekly-Review rung is
   production-unreachable until a writer ships. Additional note recorded for that work:
   `normalize_settings` drops unknown keys, so the writer must also make the API settings
   path *preserve* `coachGoals`, or any settings save will erase stored goals.
2. **Metric-change / story-update visible triggers:** shadow telemetry (`wouldFire: true` on
   most greetings) is accumulating the threshold data as designed.
3. **Wire citation completeness (§5):** options — lift the 8-cap, per-number provenance, or a
   "full evidence" debug flag. UX/product choice, post-launch.
4. **`emotionalBalance` cause-mode drivers line:** the attention driver has no renderer, so a
   cause ask reads like a value answer (never false, just less causal). Template polish.
5. **Stale-echo UX:** consider a soft age/turn-count bound on accepted echoes if real-user
   transcripts show confusing old-context binding (none observed here).

## 12. Recommendation

**READY FOR LAUNCH.** Objective basis: every launch criterion in
`AI_COACH_LAUNCH_READINESS.md` §8 is now checked — all 15 leaves + both memory flows walked
through the public API on a beta-shaped corpus with zero unexplained tool failures; the
grounding gate never fired across the entire walk and the audit found zero fabricated
numbers/articles/causes/trends; failure injection degrades honestly with no crashes
(post-D1-fix) and no invention; performance is an order of magnitude inside budget; flag-off
remains byte-identical, coach turns write nothing, and rollback is one environment variable.
The two defects found were fixed minimally and are regression-pinned; everything else is
documented above as post-launch work. Launch = flip `RWE_COACH_V2` default-on per the
launch-readiness document's §8 procedure.
