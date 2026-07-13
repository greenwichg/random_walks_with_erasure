# Recommendation Evaluation Engine

**Status: FEATURE COMPLETE (v1)** — approved 2026-07-13.

- **S1 (library)** — complete: `evaluate(store, spec, baseline=None)` in
  [`examples/rec_sandbox.py`](../examples/rec_sandbox.py).
- **S2 (CLI client)** — complete: `rec_sandbox.main()` (same module; provably thin).
- **Documentation** — complete: this document.
- **S3 (behavioral regression suite)** — complete:
  [`tests/test_rec_regression.py`](../tests/test_rec_regression.py) (15 structural-invariant
  tests), alongside [`tests/test_rec_sandbox.py`](../tests/test_rec_sandbox.py) (18 tests
  pinning the engine's own invariants).
- **REPORT CONTRACT v1** — frozen; evolution per §4 (additive within v1, breaking → v2).
- The evaluation engine is **the supported mechanism for recommendation experimentation and
  regression validation**. New recommendation-behavior experiments and regressions should be
  expressed as `evaluate()` specs / regression-suite tests, not as ad-hoc scripts against the
  serving stack.

S4 (internal API + hidden developer page) is deliberately **post-launch** (§9): the CLI and
the regression suite already satisfy the engineering needs; a developer page is a convenience
layer that would add maintenance surface before launch. No further internal-tooling expansion
is planned pre-launch — focus returns to production-facing features and launch readiness.

This is engineering documentation for people extending or consuming the engine — not user
documentation.

---

## 1. Purpose

### Why this exists

The recommender is **graph-based**: RWE-B / RWE-D / adaptive rank items by random-walk mass
over a user × item click graph, not by content features. That makes a family of everyday
engineering questions structurally hard to answer:

- *Why wasn't this article recommended?* — answerable for **in-corpus** articles by the
  internal explain endpoint (`/api/internal/recommendations/explain?article=…`), but not for
  an article that hasn't been ingested yet.
- *How would this article rank / would it be a bridge / would Story Match detect it?* — for a
  **not-yet-ingested** article there is nothing to point the explain endpoint at.
- *What happens to the feed if the corpus contained an opposite-viewpoint article, a breaking
  story, a near-duplicate, a junk article?* — a **counterfactual composition** question; the
  serving corpus can't be used to answer it without perturbing production.

The Recommendation Evaluation Engine answers all three classes by building a **complete,
ephemeral second engine stack** from `current catalog ∪ injected articles`, interrogating it
with the engine's own observers, and returning one structured report.

### What it deliberately is not

- **Not a second recommender.** It contains no ranking, scoring, selection, dedup,
  clustering, or explanation logic — a parity test pins that a zero-injection evaluation
  serves byte-exactly what the engine's own entry points serve.
- **Not a user-facing feature.** Internal engineering/debugging + regression tooling only.
- **Not a predictor of real audiences.** Rankings are computed over the deterministic
  simulated population — like every interaction in a live-feed corpus — so a report describes
  *engine behavior*, not audience behavior.

### Why it is separate from production serving

Corpus composition is **identity-sensitive**: adding one article to a catalog can re-pick the
demo reader and shift every `Q{i}` item id (pinned by `tests/test_demo_determinism.py`). The
shared catalog is every user's graph. Evaluation therefore must never mutate the catalog, the
store, or the serving bundle (`app.state.active`) — and the evaluation stack must never be
the serving stack. Isolation is structural (build-aside + tempfile + `persist=False`), and for
the CLI it can be made kernel-enforced by opening the store read-only
(`sqlite:///file:path.db?mode=ro&uri=true`).

---

## 2. Architecture

```
Layer 1 — THE EXISTING ENGINE (unchanged; owns ALL recommendation logic)
   corpus_refresh.build_candidate_for / RefreshManager.build_active
   corpus_validation.validate_corpus · corpus_health (thresholds, freshness gate)
   api_server.Backend (+ recommendations / explain_recommendations / _serialize_*)
   personalize.Personalizer (measured augmented pipeline)
   rec_explain.explain (trace · per-strategy evidence · exclusion verdicts)
   evidence_resolver.resolve (the ONE explanation vocabulary)
   story_service.build_stories (production clustering, store-free half)
   ingest.Scorer + enrich (via rss_ingest.make_scorer — the ONE scoring config)

Layer 2 — RECOMMENDATION EVALUATION ENGINE   examples/rec_sandbox.py
   evaluate(store, spec, baseline=None) -> report      (the single public entry point)
   No argparse · no printing · no HTTP · no env mutation · no writes.

Layer 3 — CLIENTS (render / assert only; never add evaluation logic)
   ├── CLI (S2, shipped)            rec_sandbox.main() — same module, thin
   ├── regression suite (S3, shipped) tests/test_rec_regression.py over evaluate()
   ├── hidden developer page (S4)   flag-gated internal API over evaluate()
   └── Article Analyzer tooling     shares the URL→scored front-end
```

### What Layer 2 actually does (orchestration map)

| Step | Layer-1 call | Native Layer-2 logic |
|---|---|---|
| Score an injected URL | `ingest.Scorer.score` (never `score_with_cache` — no writes) | field passthrough |
| Candidacy gates | `corpus_health.fresh_articles`; dedup against `build_candidate_for` output | list concat + disposition labels |
| Ephemeral build | `RefreshManager.build_active` on a **detached** manager (tempfile CSV, explicit `DatasetProfile` — no env mutation, never activated, CSV self-deleted) | none |
| Feeds | `Personalizer.recommendations` / `Backend.recommendations` | none |
| Exclusion verdicts | `Personalizer.explain` / `Backend.explain_recommendations` (`rec_explain`) | subset extraction |
| Story membership | `story_service.build_stories(rows + injected)` | canonical-URL lookup |
| Explanations | `evidence_resolver.resolve` with a **locally built** story index | none |
| Baseline diff | — | set/rank comparison of two *finished* feed listings |
| Report | — | assembly + JSON scrubbing |

### The one non-obvious design decision: injection level

Injecting an article **into the candidate list of the live stack** would be degenerate: a
zero-click item has no position in a walk-based graph (that is exactly what the engine's
`not_in_graph` verdict describes). What gives a real new RSS article its graph position is the
**corpus build itself**: the qbias pipeline runs the repo's deterministic synthetic simulator
over the article set, assigning interactions by article features. Faithful injection is
therefore *corpus-composition-level* — rebuild ephemeral, with the injected article present —
which is precisely what `build_active` produces. Same composition in, same corpus, same
recommendations out.

### Two construction invariants Layer 2 owns

1. Every `Personalizer` it creates or wraps is **`persist=False`** — the constructor default
   (`True`) persists report snapshots on model builds, which would violate zero-writes.
2. A provided `baseline` object is consulted for its **`.backend` only** (plus its own
   `candidate_sig`/`item_count` metadata when it is a `corpus_refresh.Active`) — never its
   personalizer. This is what makes it safe for a future in-process client to pass the
   *serving* `app.state.active` as the diff baseline.

---

## 3. Core principles (and the test that pins each)

| Principle | Mechanism | Pinned by |
|---|---|---|
| Zero production writes | tempfile CSV (self-deleted); `persist=False`; store only read | `test_evaluation_writes_nothing_anywhere` — SHA-256 of the store file, a `data/` snapshot, and the tempdir are identical before/after a full compare-mode run |
| Deterministic | no timestamps anywhere in the report; deterministic builds | `test_report_is_deterministic_across_runs` — byte-identical reports |
| Reuse the existing engine | every recommendation-shaped number is a Layer-1 output | `test_zero_injection_feeds_match_the_engines_own_entry_points` |
| Ephemeral corpus | `build_active` never activates; nothing outlives the call | same zero-writes evidence + no serving-state access anywhere in the module |
| No duplicate recommendation logic | Layer 2's only native math is the diff + assembly | the parity test above + code review of the orchestration map |
| Honest gates | freshness, validation, lean-resolvability, read thresholds are *reported*, never bypassed | `test_stale_article_is_dropped_by_the_freshness_gate`, `test_unknown_outlet_is_dropped_by_the_builder_not_ranked`, `test_validation_failure_is_the_answer_not_an_exception`, `test_below_threshold_reader_is_reported_not_guessed` |

To relax a gate for an experiment, set the engine's own environment (e.g.
`RWE_FEED_MAX_AGE_DAYS=0`), exactly as production would — the sandbox inherits `RWE_*` sizing,
thresholds, and feature flags so that its builds are shaped like the deployment being studied.

---

## 4. REPORT CONTRACT v1 (frozen)

The authoritative, always-current specification is the module docstring of
[`examples/rec_sandbox.py`](../examples/rec_sandbox.py). Summary:

| Section | Content |
|---|---|
| `reportVersion` | `1`. Clients dispatch on this. |
| `spec` | Normalized echo of what was evaluated (injections reduced to `{url, title}` identity). |
| `corpus.evaluated` | `built`, `error`, `candidateSize` (rows handed to the builder), `candidateSig`, `items` (rows the builder kept — the delta is the lean-resolvability drop), `graph {users, items, edges}`, `validation {eligible, failures, perBucket}`. |
| `corpus.baseline` | Same shape (compare mode); `provided: true` when a baseline object was supplied — its own `candidate_sig`/`item_count` are reported and validation is not re-run. |
| `injected[]` | Per injected article: identity (`url`, `canonicalUrl`, `title`, `publisher`), `scored {outlet, lean, category, political}`, `disposition` (`evaluated` \| `already_in_candidate` \| `dropped_freshness`), `resolvedId`, `graphNode`, `story` membership, `exclusions[]` (per reader × params: the engine's truthful verdict — `recommended` / `seen_excluded` / `below_cutoff` with per-strategy `{rank, score, inSlice}` / `not_in_graph` / `not_in_catalog` — plus `paramsUsed` per strategy). |
| `asked[]` | The same verdict shape for arbitrary extra articles (`spec.ask`). |
| `feeds[]` | Per reader × strategy × params: `status` (`ok` \| `below_threshold` \| `not_built` \| `error:<Type>`) and the served cards `{rank, id, url, publisher, strategy, crossCutting, reason, explanation?}`. |
| `diff.perFeed[]` | Compare mode: `identical`, `entered`, `left`, `moved [{key, from, to}]` — **keyed by canonical URL** (raw-id fallback only on URL-less corpora). |
| `notes[]` | Honest caveats (e.g. `max_items` subsampling in effect, corpus-relative identities). |

### Stability classes

- **STRUCTURAL** — stable within v1: every identity, verdict, rank, count, flag, and section
  shape. **Regression goldens must be built from these.**
- **COPY** — carried verbatim from Layer 1 and allowed to evolve with product copy without a
  version bump: `served[].reason` (the serializer's evidence-gated template),
  `served[].explanation.message` (the resolver's final sentence — the public API mirrors
  *this* into its own `reason`; the sandbox deliberately reports both), exclusion `detail`
  strings, `notes`. Don't pin these unless copy itself is under test.
- **CORPUS-RELATIVE VALUES** — `resolvedId` and feed `id` are stable *fields* whose `Q{i}`
  values are meaningful only within one report's evaluated corpus. Cross-report and
  cross-corpus identity is **always the canonical URL**.

### Versioning philosophy

v1 is frozen. Evolution is **additive-only within v1** (new optional fields, new notes);
any rename, removal, or meaning change bumps `reportVersion` to 2. Clients must tolerate
unknown fields and must never infer meaning from a field's absence.
`test_report_contract_v1_is_pinned` is the tripwire: it locks the version and the section
shapes, so an accidental breaking change fails CI instead of shipping silently.

---

## 5. Current clients

### `evaluate()` — the library (S1)

```python
import store as store_mod, rec_sandbox

st = store_mod.Store("sqlite:///file:data/ih.db?mode=ro&uri=true")   # enforced read-only
report = rec_sandbox.evaluate(st, {
    "inject":   [{"url": "https://apnews.com/article/…", "title": "…",
                  "publishedAt": "2026-07-13T09:00:00+00:00"}],
    "ask":      ["https://cnn.com/2026/politics/…"],
    "readers":  [{"kind": "demo"}, {"kind": "user", "id": 7}],
    "strategies": [None, "rwe-d"],
    "params":   [None, {"beta": 0.8}],
    "questions": ["feed", "exclusion", "story", "explanation"],
    "compare":  True,
})
```

Reader kinds: `demo` (the corpus's synthetic demo reader), `row` (an explicit synthetic
reader), `user` (a real stored reader through the measured augmented pipeline; below the read
threshold the report says `below_threshold` instead of guessing). `params` use the exact
serving shape (`api_server.rec_params_from_settings` output, or a plain `{"beta": …}` /
`{"epsilon": …}`).

### The CLI (S2) — first client, provably thin

`rec_sandbox.main()` builds a spec from flags (optionally merged over `--spec` JSON), calls
`evaluate()`, renders. `--json` prints the library report **byte-for-byte** (pinned by
`test_cli_json_is_byte_identical_to_the_library_report`); the human render is display
truncation only. Exit codes: `0` report produced and corpus built; `2` report produced but
the evaluated corpus did not build (`corpus.evaluated.error` says why) — usable as a script
gate.

```bash
python examples/rec_sandbox.py --db "sqlite:///file:data/ih.db?mode=ro&uri=true" \
    --preset left --reader user:1 --params '{"beta": 0.8}' --compare
```

Presets are **spec-side injection templates** (`left`, `right`, `center`, `breaking`,
`duplicate`, `low_quality`) — synthetic probes with fresh timestamps. For story-specific
probes (matching a real cluster's tokens), craft the article via `--spec` / `--inject-url`.

---

## 6. Future clients

- ~~Regression suite (S3)~~ — **shipped**: `tests/test_rec_regression.py`, the second
  client. Structural-invariant tests (never copy fields) over fixture knowledge maps:
  verdict/feed-membership mirroring, rwe-b political admission, seen-exclusion,
  cross-cutting biconditional for a sided reader, resolver type vocabulary, duplicate-pair
  story clustering, slider-sweep invariances (beta can't move the rwe-b prefix; epsilon
  can't move pure rwe-d), blend-plan cutoffs (6/4/4) as a deliberate tripwire, and
  byte-identical reports from two independently built stores. No overlap with the 21d
  pipeline was found to promote — injection counterfactuals are orthogonal to its
  recorded-persona validation.
- **Hidden developer page (S4, POST-LAUNCH).** Deferred by decision (2026-07-13): the CLI +
  regression suite satisfy current engineering needs, and a developer page is purely a
  convenience layer that adds maintenance surface before launch. Requirements recorded for
  when it is picked up: a trusted internal route (`_require_trusted`) behind
  `RWE_REC_SANDBOX` (default off, flag-off byte-identity suite), returning the report dict
  verbatim as the response body; single-flight (one build at a time; seconds-long request;
  transient second stack in memory — precedented by the hot swap); and
  `baseline=app.state.active` for cheap diffs under the `.backend`-only rule.
- **Article Analyzer.** Shares the URL→scored front-end today (`ingest.Scorer` via
  `rss_ingest.make_scorer`); a potential dev-mode counterfactual panel ("what if this article
  were in the corpus?") would call `evaluate()` — the analyzer's *user-facing* surface stays
  on the live corpus and the explain endpoint.

All clients consume REPORT CONTRACT v1. Client-specific report structures are prohibited —
that is the point of the freeze.

---

## 7. Example workflows

**Why wasn't this article recommended to reader 7?** (in-corpus article)
```bash
python examples/rec_sandbox.py --db "$DB_URL" --reader user:7 \
    --ask "https://reuters.com/world/…" --questions exclusion
```
→ `asked[0].verdict` with per-strategy rank/score/cutoff, or `seen_excluded`, etc.

**Would this new article surface — and as what?**
```bash
python examples/rec_sandbox.py --db "$DB_URL" --reader user:7 \
    --inject-url "https://apnews.com/article/…" --inject-title "…" \
    --inject-published "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
```
→ `injected[0]`: `graphNode`, verdict + per-strategy evidence; if served, the card carries
`crossCutting` and the resolver `explanation` (a `bridge` explanation = it presented as a
cross-cutting recommendation for that reader).

**Would Story Match cluster it?** Inject an article whose title shares tokens with live
coverage → `injected[0].story` = `{matched, storyId, articleCount, publisherCount,
distribution}` from the production clustering. The `duplicate` preset probes near-duplicate
merging generically.

**Do the preference sliders move this feed?**
```bash
python examples/rec_sandbox.py --db "$DB_URL" --reader user:7 --strategy rwe-d \
    --params '{"beta": 0.3}' --params '{"beta": 0.8}' --questions feed
```
→ two `feeds[]` entries to compare; `paramsUsed` in any exclusion confirms the
hyperparameters in effect.

**Composition sensitivity / regression gate.**
```bash
python examples/rec_sandbox.py --db "$DB_URL" --preset breaking --reader user:7 \
    --compare --json --out /tmp/report.json || echo "corpus did not build"
```
→ `diff.perFeed` (canonical-URL keyed) shows exactly what the injection displaced; the saved
report is golden-ready; exit code 2 gates scripts on corpus health.

---

## 8. Known limitations

1. **Simulated-population semantics.** Rankings describe the engine over the deterministic
   synthetic population (as in every live-feed corpus). Not audience prediction. (Emitted in
   `notes` on every report.)
2. **`demo` reader identity is corpus-relative.** In compare mode, a `demo` diff compares
   each corpus's *own* demo reader (which injection can re-pick). Prefer `user:`/`row:`
   readers for regression diffs.
3. **`Q{i}` ids are corpus-relative.** Hence canonical-URL keying everywhere it matters;
   `resolvedId` is for correlating *within* one report (e.g. with a matching live explain).
4. **Clock-day dependence.** The C4 freshness gate reads the wall clock; determinism is per
   (store, spec, env, clock-day). Fixture stores should use now-relative dates.
5. **Story facts are membership, not reader licensing.** For an injected (unread) article,
   `story.matched` reports clustering; a `story_match` *explanation* additionally requires
   the reader to have read a cluster sibling — the resolver enforces that itself.
6. **`evidence_resolver.story_index` memo.** The personal-path exclusion
   (`Personalizer.explain`) touches the resolver's process-wide story-index memo, keyed by
   `(count_feed_articles, TTL bucket)` — not by store identity. Same-store in-process use
   (the S4 dev page) is safe; multi-store tooling in one process can cross-contaminate that
   memo within its TTL. The sandbox's own explanation path deliberately builds a *local*
   index to avoid adding pressure here.
7. **Cost.** One full engine build per evaluated composition (two in compare mode without a
   provided baseline): seconds of CPU and a transient second stack in memory. Serialize
   builds; never run them concurrently in the serving process.
8. **Subsampling ceiling.** If the composition exceeds the effective `max_items`, the qbias
   builder subsamples deterministically and may drop injected articles — the report emits a
   note when this is in effect.
9. **Baseline-only enrichment.** Injected scoring uses the offline baseline enricher
   (register/emotion lexicons), matching bulk ingest — not the optional LLM enricher.
10. **URL-less corpora.** On synthetic (no-URL) profiles, diff identity falls back to raw
    ids — best-effort across builds.

---

## 9. Roadmap

### v1 — COMPLETE (2026-07-13)

| Phase | Content | Status |
|---|---|---|
| S1 | `evaluate()` library + REPORT CONTRACT v1 + 13 invariant tests | ✅ shipped (`764e034`) |
| S2 | Thin CLI client (presets, `--json`/`--out`, exit codes) + contract freeze + 5 tests | ✅ shipped (`242800f`) |
| Docs | This engineering guide + README index entry | ✅ shipped (`48565d7`) |
| S3 | Recommendation Regression Suite as the second client (`tests/test_rec_regression.py`: 15 structural-invariant tests over contract v1) | ✅ shipped (`d29afe3`) |

v1 is the supported mechanism for recommendation experimentation and regression validation.
Pre-launch, the subsystem is in maintenance-only mode: contract-additive fixes and new
regression-suite scenarios are in scope; new clients and new capabilities are not.

### Post-launch roadmap

| Item | Content | Rationale for deferral |
|---|---|---|
| S4 | Flag-gated internal API + hidden developer page (`RWE_REC_SANDBOX`, trusted, single-flight, flag-off byte-identity; requirements in §6) | CLI + regression suite already satisfy engineering needs; a developer page is a convenience layer that adds maintenance surface before launch |
| — | Article Analyzer counterfactual dev panel over `evaluate()` | depends on the Analyzer shipping |

**Permanently out of scope:** any recommendation logic in Layer 2 or any client; any
persistence introduced by evaluation; any client-specific report structure. Contract changes
follow §4's versioning policy.
