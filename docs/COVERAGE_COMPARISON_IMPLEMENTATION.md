# Coverage Comparison (insight-derived) — implementation log

Running record of building `docs/COVERAGE_COMPARISON_REVISED_DESIGN.md` (revision 2), phase by
phase, with the production verification each phase is gated on and **every deviation from the
approved design stated and justified before it is taken**.

| phase | scope | state |
|---|---|---|
| **0a** | comparable-set readiness probe (design §11 items 1, 2, 3, 8) | **built; awaiting production run** |
| 0b | generation-dependent readiness (§11 items 4–7) | blocked on Phase 1's contract — see §D1 |
| 1 | contract extension + validation + worker scale (dormant) | not started |
| 2 | enablement on the designated recipe | not started |
| 3–5 | tiers C1 / C2 / C3 | not started |

---

## D1 — Deviation: Phase 0 splits into 0a and 0b

**Approved design:** §13 lists Phase 0 as one gate over the eight measurements in §11.

**What was built:** the four measurements that need no generated insight (items 1, 2, 3, 8) ship
now as `examples/audit_coverage_readiness.py`. The other four (4 generation latency, 5 enum
reliability, 6 quantity yield and span-verification, 7 token distribution) run after Phase 1.

**Justification.** Items 4–7 measure the output of the extended prompt, which does not exist until
Phase 1 creates it — §11 itself says to run them with `benchmark_insights.py --sample-production`,
a harness that can only sample a contract that has shipped. The design's own gate wording is *"no
**tier** is built before these numbers exist"*, and Phase 1 is not a tier: it is a dormant contract
change with zero behavioural effect. Critically, **the measurement that can stop the roadmap is
entirely in 0a** — if fewer than 100 clusters can reach `MIN_COMPARABLE`, nothing further is built,
and no generation has been paid for. The split therefore preserves the gate's purpose exactly while
resolving an ordering dependency the design did not state.

**Effect on the design:** none, beyond sequencing. §13's Phase 0 row is read as 0a; §13's Phase 1
row gains 0b's measurements as an additional exit condition before Phase 2 enablement.

---

## Phase 0a — the readiness probe

### What was built

| file | role |
|---|---|
| `examples/coverage_insights.py` | **the comparable set** (design §4), model-free stage. `candidate`, `syndication_groups`, `support_units`, `comparable_stage1`, and the four config knobs. |
| `examples/audit_coverage_readiness.py` | the read-only probe. Builds the served story view, resolves the catalog through the **canonical** join, and reports §11 items 1, 2, 3, 8. |
| `tests/test_coverage_insights.py` | 23 tests, weighted to the honesty properties (below). |

### Why the rule lives in a module and not in the probe

The probe and production must apply the *same* membership test. The repo has been here before —
`publisher_identity` was extracted for exactly this reason, because "an audit that measured a
different rule than production applies would be measuring nothing". Every number the probe prints
comes from the functions the feature itself will call.

`coverage_insights.py` is a **new module** rather than an extension of `coverage_comparison.py`:
L0 is deployed and serving, and its module should not change while a new tier is under
construction. The design's "no second comparison engine" rule is preserved by reusing
`coverage_comparison`'s own `_identity_map` / `_pub_key` — deliberately the same collapser, not a
copy of it — and, when the tiers land, its `_finding` shape and evidence format.

### The rules implemented, and what each is defending against

| rule | implementation | the failure it prevents |
|---|---|---|
| **Time window** (design §4 cond. 4) | `comparable_stage1` drops members published after `target + grace` (default 6 h) | An article published in hour 1 cannot mention what happened in hour 30. Without this, an early report is measured against later developments and reported as lacking them — a false statement caused entirely by time. This is the rule revision 1 was missing. |
| **Input parity** (cond. 3) | median `inputChars` over the time-filtered set, then a 0.6 floor | A stub member must not make the comparison set look complete. Parity is *relative*, so a uniformly terse cluster still compares — the rule is about asymmetry, not about length. |
| **Syndication collapse** | union-find over `title_tokens` Jaccard ≥ 0.9 on **title + description** | Six outlets running one AP story are six publisher identities and one act of journalism. `publisher_identity` collapses many *names of one outlet*, never *one story across many outlets* — a hole both earlier designs claimed was closed. |
| **Support units** | syndication collapse, then `publisher_identity` | Counting either collapse alone overstates corroboration. |

### Deviation D2 — two texts per candidate, not one

**Approved design:** §4 specifies input parity on `inputChars` and syndication on "title +
description token overlap".

**What was built:** `candidate()` takes *both* texts explicitly — `gen_text` (what
`article_insights.article_text` would feed the model: title + description + body, capped) for
`inputChars`, and `dedup_text` (title + description only) for the syndication tokens.

**Justification.** A first cut used one text for both and the dry run exposed the error: including
a body inflates the token set, and since only 23.7% of the catalog carries a body, some pairs would
be compared on their full text and others on a blurb. An inconsistent duplicate test is worse than
a consistently weaker one. This is not a change to the rule — it is the rule as written, with the
two inputs it actually needs made explicit so a caller cannot conflate them.

### Tests

23 tests (`tests/test_coverage_insights.py`), weighted to the properties that keep counts honest:
later coverage is never comparable; the grace window absorbs feed jitter but is configurable to
zero; unparseable timestamps are not evidence (consistent with L0's timing block); a target cannot
compare with itself even when the caller rebuilt the dict; stubs cannot pad a set but a uniformly
terse set still compares; wire copy collapses to one unit while distinct reporting does not;
grouping is order-independent (DSU, lower-index roots); and stage 1 is a pure function.

One test failed on first run and the **fixture** was wrong, not the module: `title_tokens` treats
"tonight" as a stop word, so the two strings I chose as "similar but not identical" tokenized
identically. Replaced with a pair whose measured Jaccard is 0.71.

### Gates at this commit

- `tests/test_coverage_insights.py` — 23 passed.
- Full engine suite — **2,696 passed**, 1 skipped. `tests/test_plot_axis.py` cannot collect in this
  container (`matplotlib` not installed); pre-existing and unrelated — the file was last touched by
  `3aa0ca7` and nothing in this change imports it.
- Dry-run against a synthetic 120-story view exercises every print path before the probe touches
  production.

### Production verification — required before Phase 1

```bash
cd /opt/ih && sudo bash deploy/ops/update.sh <sha>
sudo docker exec -i deploy-api-1 python examples/audit_coverage_readiness.py --show 10
```

**The gate (design §13, phase 0): clusters reaching `MIN_COMPARABLE` ≥ 100.** The probe prints
`PASS`/`FAIL` itself. If it fails, the insight-derived roadmap stops here, before any generation is
paid for — which is the entire reason this phase exists.

Read alongside the gate:

- **Item 2** decides whether `RWE_INSIGHTS_MIN_CHARS` should move off 200. The measured median
  description is 154 characters, so eligibility is sharply threshold-sensitive.
- **Item 3** produces the `RWE_INSIGHTS_BATCH` figure Phase 1 must implement, replacing the
  default of 6 that the review showed cannot keep up.
- **Item 8** is a calibration check on the 0.9 syndication threshold. A very high fold rate means
  the threshold is too low for real coverage sets, not that the catalog is all wire copy — that is a
  finding about the constant, and it is cheaper to learn now than after the tiers count on it.

*(Results are recorded here once the run comes back.)*
