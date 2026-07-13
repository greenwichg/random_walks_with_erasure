# Recommendation Evaluation Engine — Usage Guide

A practical, task-oriented guide to using the Recommendation Evaluation Engine day to day. For
*why it is built this way* — the three-layer design, the frozen report contract, the isolation
invariants — read the companion [`RECOMMENDATION_EVALUATION_ENGINE.md`](RECOMMENDATION_EVALUATION_ENGINE.md).
This guide assumes that architecture and shows you how to drive it.

> **Audience:** developers, maintainers, contributors, and future-you. Everything here maps to
> the real CLI in [`examples/rec_sandbox.py`](../examples/rec_sandbox.py) — no invented flags.

---

## 1. Purpose

### When should I use the evaluation engine?

Reach for it whenever you need to answer a question about **recommendation behavior** that the
live product can't safely answer:

- *Would this article be recommended? As what — a bridge, a story match, long-tail?*
- *Why wasn't this article recommended to reader N?*
- *How does the feed change when I move the strength (beta) or openness (epsilon) slider?*
- *What happens to the feed if the corpus contained a breaking story / a near-duplicate / a
  junk article?*
- *Did my engine change alter rankings I didn't expect to touch?* (regression)

### What problems does it solve?

The recommender ranks by random-walk mass over a click graph, not by content features. That
makes "would *this new article* rank?" structurally hard: a not-yet-ingested article has no
node in the live graph. The engine answers by building a **complete, ephemeral second copy** of
the engine stack from `current catalog ∪ your injected articles`, running the real recommender
over it, and handing you one structured report. Nothing is persisted; the serving corpus is
never touched.

### When should I use it instead of the production API?

| Use the **production API** (`/api/internal/recommendations/explain`) when… | Use the **evaluation engine** when… |
|---|---|
| The article is already in the corpus and you want its live verdict | The article is **not yet ingested** and you want its would-be verdict |
| You want the exact feed a real request serves | You want a **counterfactual** ("what if the corpus also had X?") |
| You're debugging a live reader's real feed | You're **regression-testing** engine changes offline |
| — | You want to compare **before vs after** a corpus change without perturbing production |

### Typical workflows

Investigate one article → sweep a slider → inject a scenario (breaking / duplicate / junk) →
compare two corpus compositions → save a report as a regression baseline. Each is worked in
§6.

---

## 2. Prerequisites

- **Python:** the version the repo targets (3.11 in CI). Check with `python --version`.
- **Virtual environment** (recommended):
  ```bash
  python -m venv .venv && source .venv/bin/activate
  ```
- **Requirements:**
  ```bash
  pip install -r requirements.txt
  ```
  The engine imports only in-repo modules plus the project's existing dependencies (NumPy,
  SQLAlchemy, …) — no extra packages.
- **A database.** Any store the app uses works: your beta SQLite file, a dev DB, or a fixture
  built by the test suite. The engine only ever **reads** it. Point at it with a store URL:
  `sqlite:///data/ih.db`.
- **Read-only database (strongly recommended).** Because analysis must never mutate anything,
  open the DB read-only so isolation is **kernel-enforced**, not merely promised:
  ```
  sqlite:///file:data/ih.db?mode=ro&uri=true
  ```
  With this URL any accidental write fails at the OS layer.
- **Run from the project root.** All examples assume your working directory is the repo root
  (so `examples/rec_sandbox.py` and the module imports resolve). The CLI adds `examples/` to
  the path itself; you just need the relative path to be correct.

---

## 3. CLI overview

```
  CLI  (examples/rec_sandbox.py :: main)      ← flags in, text/JSON out
   │      builds a spec, formats a report — NO recommendation logic
   ▼
  evaluate(store, spec)  (Layer 2 library)    ← orchestration + report assembly only
   │
   ▼
  the existing recommendation engine (Layer 1) ← ALL ranking/scoring/selection/explanation
```

The CLI is a **thin client**. It translates flags into a *spec* (a plain dict), calls
`evaluate(store, spec)`, and renders the returned report. It contains no ranking, scoring,
selection, or explanation logic — those live entirely in the existing engine, reached through
`evaluate()`. A regression test pins that the CLI's `--json` output is byte-identical to
calling `evaluate()` directly, so the command line can never drift from the library.

---

## 4. Basic usage

Everything is one script, `examples/rec_sandbox.py`, with `--db` required.

```bash
# 1. See every option
python examples/rec_sandbox.py --help

# 2. Minimal run: build the corpus from the DB, evaluate the demo reader's blended feed
python examples/rec_sandbox.py --db "sqlite:///file:data/ih.db?mode=ro&uri=true"

# 3. Raw JSON instead of the human render (this IS the library report, verbatim)
python examples/rec_sandbox.py --db "sqlite:///data/ih.db" --json

# 4. Compare mode: also build a baseline (no injections) and diff the feeds
python examples/rec_sandbox.py --db "sqlite:///data/ih.db" --preset breaking --compare
```

Option reference (all optional except `--db`; repeatable options build lists):

| Option | Meaning |
|---|---|
| `--db URL` | **Required.** Store URL. Prefer the read-only URI form. |
| `--spec FILE` | Load a JSON spec (`-` = stdin). Flags below **extend** it. |
| `--preset NAME` | Append a canned injection scenario. Repeatable. Choices: `left`, `right`, `center`, `breaking`, `duplicate`, `low_quality`. |
| `--inject-url URL` | Ad-hoc single injection by URL. |
| `--inject-title T` | Title for the ad-hoc injection (helps scoring + story clustering). |
| `--inject-published ISO` | ISO timestamp for the ad-hoc injection (freshness). |
| `--inject-outlet NAME` | Outlet override for the ad-hoc injection. |
| `--ask URL_or_ID` | Extra "why (not) this article?" query. Repeatable. |
| `--reader R` | `demo` \| `user:<id>` \| `row:<n>`. Repeatable. Default `demo`. |
| `--strategy S` | `blend` \| `rwe-b` \| `rwe-d` \| `adaptive`. Repeatable. Default `blend`. |
| `--params JSON` | Hyperparameters, e.g. `{"beta": 0.8}` or `{"epsilon": 0.7}`. Repeatable. |
| `--questions Q` | Restrict computed sections: `feed`, `exclusion`, `story`, `explanation`. Repeatable. Default all. |
| `--compare` | Also build a baseline (no injections) and diff every feed. |
| `--json` | Print the raw report JSON. |
| `--out FILE` | Also write the report JSON to a file. |

---

## 5. Common commands

Every command below is real and derived from the implemented CLI. Substitute your own DB path
for `$DB` (e.g. `export DB="sqlite:///file:data/ih.db?mode=ro&uri=true"`).

| Purpose | Example command | Expected result |
|---|---|---|
| Help / list options | `python examples/rec_sandbox.py --help` | argparse usage with every flag above |
| JSON output | `python examples/rec_sandbox.py --db "$DB" --json` | the full report as pretty JSON (`analysisVersion`-style `reportVersion: 1`, all sections) |
| Save report | `python examples/rec_sandbox.py --db "$DB" --out report.json` | human render on screen **and** JSON written to `report.json` |
| Compare (before vs after) | `python examples/rec_sandbox.py --db "$DB" --preset breaking --compare` | evaluated + baseline corpora, plus a `diff` per feed |
| Preset scenario | `python examples/rec_sandbox.py --db "$DB" --preset duplicate` | injects a near-duplicate pair; see how they cluster/served |
| Reader selection | `python examples/rec_sandbox.py --db "$DB" --reader user:2 --reader demo` | feeds/verdicts for a real reader **and** the synthetic demo reader |
| Strategy selection | `python examples/rec_sandbox.py --db "$DB" --strategy rwe-d --strategy blend` | one feed per strategy per reader |
| Beta parameter (strength) | `python examples/rec_sandbox.py --db "$DB" --reader user:2 --params '{"beta":0.3}' --params '{"beta":0.8}'` | two feeds you can compare; RWE-D reorders |
| Epsilon parameter (openness) | `python examples/rec_sandbox.py --db "$DB" --reader user:2 --strategy rwe-b --params '{"epsilon":0.7}' --params '{"epsilon":0.97}'` | two RWE-B feeds under different openness |
| Restrict questions | `python examples/rec_sandbox.py --db "$DB" --preset left --questions feed --questions exclusion` | skip story/explanation computation for speed |
| Spec file | `python examples/rec_sandbox.py --db "$DB" --spec scenario.json` | evaluate a saved spec; flags still extend it |
| Output file only | `python examples/rec_sandbox.py --db "$DB" --spec scenario.json --json --out out.json` | JSON to stdout and to `out.json` |
| Ad-hoc injection | `python examples/rec_sandbox.py --db "$DB" --inject-url https://apnews.com/article/x --inject-title "senate budget vote" --inject-published 2026-07-13T09:00:00+00:00` | score + rank a specific pasted article |
| Ask about an article | `python examples/rec_sandbox.py --db "$DB" --reader user:2 --ask https://cnn.com/politics/some-slug` | the truthful verdict for that URL |

---

## 6. Example workflows

`$DB` is your read-only store URL throughout.

### Investigate a recommendation

**Goal:** understand why a specific article is/isn't in a reader's feed.
```bash
python examples/rec_sandbox.py --db "$DB" --reader user:2 \
    --ask "https://cnn.com/politics/some-slug" --questions exclusion
```
**Interpret:** read `asked[0].verdict` — `recommended` (with `byStrategy` slot info),
`seen_excluded` (the reader already read it), `below_cutoff` (ranked by every strategy but
outside each served slice — the `byStrategy` ranks/scores/cutoffs tell you by how much),
`not_in_graph` (in the catalog but not a recommendable node — e.g. unresolved lean), or
`not_in_catalog`.

### Compare recommendation strength (beta)

**Goal:** see how the strength slider reshapes the feed.
```bash
python examples/rec_sandbox.py --db "$DB" --reader user:2 \
    --params '{"beta":0.3}' --params '{"beta":0.8}' --questions feed
```
**Interpret:** two `feed[...]` blocks. Beta parameterizes **RWE-D only**, so the leading RWE-B
slice is identical between them; differences appear in the RWE-D positions. `paramsUsed` in the
trace confirms the beta actually in effect.

### Compare political openness (epsilon)

**Goal:** see how openness reshapes the bridging slice.
```bash
python examples/rec_sandbox.py --db "$DB" --reader user:2 --strategy rwe-b \
    --params '{"epsilon":0.7}' --params '{"epsilon":0.97}'
```
**Interpret:** epsilon parameterizes **RWE-B only**; an explicit `rwe-d` feed is invariant under
it. Compare the two RWE-B feeds.

### Test a breaking-news article

**Goal:** would a fresh, high-velocity story surface, and displace what?
```bash
python examples/rec_sandbox.py --db "$DB" --preset breaking --reader user:2 --compare
```
**Interpret:** `injected[0].disposition` should be `evaluated` (fresh), `graphNode: true` if it
became recommendable; the `diff.perFeed` shows exactly what it pushed out (`entered` / `left` /
`moved`, keyed by canonical URL).

### Test a duplicate story

**Goal:** confirm near-duplicates cluster into one story, not two.
```bash
python examples/rec_sandbox.py --db "$DB" --preset duplicate --questions story
```
**Interpret:** both injected articles should report `story.matched: true` with the **same**
`storyId` (they merged), or `similarStory` pointing at each other — never two separate clusters.

### Regression investigation

**Goal:** prove an engine change didn't move a feed it shouldn't.
```bash
# before your change:
git stash && python examples/rec_sandbox.py --db "$DB" --preset left --reader user:2 \
    --json --out before.json
# after your change:
git stash pop && python examples/rec_sandbox.py --db "$DB" --preset left --reader user:2 \
    --json --out after.json
diff <(jq -S . before.json) <(jq -S . after.json)
```
**Interpret:** diff only the **structural** fields (identities, verdicts, ranks). Copy-bearing
fields (`reason`, `explanation.message`) may legitimately differ if you touched product copy —
don't treat those as regressions (see §10).

### Validate a recommendation explanation

**Goal:** confirm a served card's explanation *type* is licensed by its evidence.
```bash
python examples/rec_sandbox.py --db "$DB" --reader user:2 \
    --questions feed --questions explanation
```
**Interpret:** each served card shows `<type>` (e.g. `<bridge>`, `<story_match>`,
`<topic_continuity>`). A `bridge` type must sit on a cross-cutting card. The *type* is
structural and safe to assert; the *message* is product copy.

---

## 7. Understanding the report

The human render is a readable projection of the JSON report. Here is a real render (preset
`left`, demo reader):

```
corpus[evaluated]: built=True items=216 graph={'users': 479, 'items': 216, 'edges': 6779} eligible=True failures=[]

injected: https://theguardian.com/politics/sandbox-left-probe
  disposition=evaluated graphNode=True resolvedId=Q215 outlet='The Guardian' lean=-1.0 topic='Politics'
  story: matched=False
  verdict[demo params=-]: below_cutoff
    adaptive: rank=14 score=0.01005 inSlice=None
    rwe-b: rank=9 score=0.01194 inSlice=None
    rwe-d: rank=35 score=0.005032 inSlice=None

feed[demo strategy=blend params=-]: ok
  # 1 [rwe-b] Fox News — https://foxnews.com/politics/m8a-158 (cross)  <bridge>
  ...
  # 7 [rwe-d] CNN — https://cnn.com/politics/m8a-190  <topic_continuity>
  #11 [adaptive] CNN — https://cnn.com/business/m8a-202  <coverage_breadth>

note: rankings are computed over the deterministic simulated population — engine behavior, not
audience prediction; comparisons are keyed by canonical URL because Q{i} ids and the demo
reader are corpus-relative
```

**corpus** — the built stack(s). `built` (did validation + build succeed), `items` (rows the
builder kept), `graph {users, items, edges}`, `validation {eligible, failures, perBucket}`. In
`--compare`, both `evaluated` and `baseline` appear. `candidateSize` (JSON) is rows handed to
the builder; `items` is what survived — the delta is the lean-unresolvable drop.

**injected** — one entry per injected article: identity (`url`, `canonicalUrl`), `scored`
(`outlet`, `lean`, `topic`, `political`), `disposition` (`evaluated` / `already_in_candidate` /
`dropped_freshness`), `resolvedId` (its `Q{i}` in **this** corpus), `graphNode` (recommendable
node?), `story`, and `exclusions` (the verdict per reader × params).

**feeds** — one per reader × strategy × params. `status` (`ok` / `below_threshold` /
`not_built` / `error:<Type>`) and the served cards with `rank`, `strategy`, `crossCutting`
(`(cross)`), and — when you asked for `explanation` — the explanation `<type>`.

**diff** (compare mode) — per feed: `identical`, `entered`, `left`, and `moved [{key, from,
to}]`. **Keyed by canonical URL** (the `key` field) — never `Q{i}`.

**story** — for a catalog article, real membership: `matched: true`, `storyId`, `articleCount`,
`publisherCount`, `distribution`. For a non-catalog article, at most `similarStory` (a
best-Jaccard advisory) — never claimed membership.

**explanation** — on served cards when requested: the resolver's `type` (structural, safe to
assert) and `message` (product copy, do not pin).

**paramsUsed** — inside the JSON trace / exclusion `byStrategy`: the hyperparameters the engine
*actually applied* per strategy. This is your honest confirmation that `--params` took effect.

**notes** — honest caveats always worth reading: the simulated-population caveat, the
canonical-URL-keying caveat, and (when it fires) a `max_items` subsampling warning.

---

## 8. Common scenarios → command

| "I want to know…" | Command |
|---|---|
| …why an article wasn't recommended | `--reader user:N --ask <url> --questions exclusion` → read `asked[].verdict` |
| …whether an article would enter the graph | `--inject-url <url> --inject-title "…" --inject-published <iso>` → `injected[0].graphNode` |
| …whether an article is cross-cutting | inject it, look for `(cross)` on its served card / `crossCutting` in JSON (it's reader-relative) |
| …how beta changes recommendations | `--reader user:N --params '{"beta":0.3}' --params '{"beta":0.8}' --questions feed` |
| …before vs after a corpus change | add `--compare`; read `diff.perFeed` |
| …whether a story would be detected | inject with a real title; `--questions story` → `injected[0].story` |
| …which strategies would recommend it | `--ask <url> --questions exclusion` → `byStrategy` ranks + `inSlice` |

---

## 9. Troubleshooting

| Symptom | What it means | What to do |
|---|---|---|
| `sqlalchemy` / connection error on `--db` | invalid or missing database URL/path | check the path exists; use the `sqlite:///…` (or read-only URI) form; run from the repo root |
| `disposition=dropped_freshness` | the injected article is older than the C4 freshness window, so production would never rank it | give a recent `--inject-published`, or relax the gate for the experiment via `RWE_FEED_MAX_AGE_DAYS=0` (env, engine's own knob) |
| `verdict=not_in_graph` | in the catalog/composition but not a recommendable node — usually unresolved political lean (unknown outlet) | expected for unknown-outlet articles; use a registry-known outlet or `--inject-outlet` |
| `corpus.evaluated.built=False`, `error=validation_failed` | the composition failed the corpus-validation gate (too few articles/publishers/buckets, etc.) | the `validation.failures` codes say which floor failed; use a fuller DB, fewer restrictive thresholds, or accept that this composition can't build |
| `status=below_threshold` on a feed | the `user:` reader hasn't crossed the measured-report read threshold | choose a reader with more reads, or use `demo` / `row:` |
| unsupported / bad `--reader` value | must be `demo`, `user:<id>`, or `row:<n>` | fix the form; ids/rows must be integers |
| compare shows nothing / no `diff` | you didn't pass `--compare`, or a feed's status isn't `ok` in both corpora | add `--compare`; ensure the reader builds in both |
| exit code `2` | a report was produced but the **evaluated corpus did not build** (see `corpus.evaluated.error`) | intended for scripts — treat as "composition unbuildable," inspect the error/validation codes |

Exit codes: **0** = report produced and evaluated corpus built; **2** = report produced but the
evaluated corpus did not build. (An unhandled crash would surface as a normal Python traceback,
not exit 2.)

---

## 10. Best practices

- **Always open production/beta DBs read-only** (`?mode=ro&uri=true`) so isolation is enforced
  by the OS, not just by the engine's design.
- **Never modify production data.** The engine never writes; keep it that way by not pointing
  other tools at the same file mid-run.
- **Use compare mode for experiments.** A raw feed is hard to reason about; a `diff` against the
  no-injection baseline shows exactly what your change did.
- **Reach for presets before custom specs.** `left/right/center/breaking/duplicate/low_quality`
  cover the common archetypes; drop to `--inject-url` / `--spec` only when you need a specific
  article.
- **Save reports (`--out`) for regression debugging.** A committed `before.json` / `after.json`
  pair is the fastest way to see what an engine change moved.
- **Automate on structural fields only.** Identities, verdicts, ranks, counts, flags are stable
  within report v1. Never assert on `reason` / `explanation.message` / `detail` / `notes` — they
  carry product copy and may evolve without a version bump.
- **Prefer `user:`/`row:` readers over `demo` for reproducible diffs.** The demo reader is
  re-selected per corpus build, so injecting an article can change *which* reader `demo` is.

---

## 11. FAQ

**Why didn't my article enter the graph?** Most often the outlet isn't in the registry, so its
political lean is unresolved and the corpus builder keeps only recommendable nodes — you'll see
`graphNode: false` / `not_in_graph`. Use a known outlet or `--inject-outlet`. Second most
common: it was `dropped_freshness` (too old).

**Why didn't recommendations change when I moved a parameter?** Beta only parameterizes RWE-D
and epsilon only RWE-B, and each occupies a bounded slice of the blend — so a change may only
move tail positions, or none on a given corpus. Confirm the parameter *reached* the engine via
`paramsUsed` (it will read your value even when the visible feed doesn't move), then compare the
single-strategy feed (`--strategy rwe-d`) where the effect is largest.

**Why is the demo reader different between runs / corpora?** The demo reader is **re-selected per
corpus build**, so any composition change (including an injection) can pick a different synthetic
reader. Use `user:`/`row:` readers when you need a fixed identity.

**Why are the `Q` IDs different between reports?** `Q{i}` ids are **corpus-relative** — assigned
during each build. A one-article composition change can shift every id. They're for correlating
*within* one report; never compare them across reports.

**Why does compare use canonical URLs?** Precisely because `Q{i}` ids are corpus-relative — the
diff must survive the id churn an injection causes, so it keys on canonical URL (the `key`
field).

**Why is story matching unavailable / `matched: false`?** Real membership requires the article
to be a **catalog** article inside a multi-publisher cluster. A pasted, non-catalog article gets
at most a `similarStory` advisory (best title-token Jaccard above the clustering threshold) —
never claimed membership, because the resolver's story gates require the real thing.

**Why is my report deterministic?** The report is a pure function of (store snapshot, spec,
`RWE_*` env, freshness clock-day). It carries no timestamps. Same inputs → byte-identical
report — which is exactly what makes it usable as a regression baseline.

---

## 12. Future capabilities

These are on the roadmap and do not change today's CLI:

- **Regression suite** — already shipped as the second client:
  [`tests/test_rec_regression.py`](../tests/test_rec_regression.py) drives `evaluate()` with
  structural-invariant tests. Add scenarios there rather than scripting the CLI for CI.
- **Hidden developer page (post-launch)** — a flag-gated internal API + UI over the same
  `evaluate()`; deferred so it doesn't add maintenance surface before launch.
- **Article Analyzer integration** — the Analyzer shares the URL→scored front-end today; a
  future dev-mode "what if this article were in the corpus?" panel would call `evaluate()`
  directly.
- **Post-launch tooling** — any further clients consume the frozen REPORT CONTRACT v1; see the
  architecture doc's roadmap for the evolution policy (additive within v1; breaking changes bump
  `reportVersion`).
