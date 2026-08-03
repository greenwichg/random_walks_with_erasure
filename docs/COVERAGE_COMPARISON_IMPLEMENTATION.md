# Coverage Comparison (insight-derived) — implementation log

Running record of building `docs/COVERAGE_COMPARISON_REVISED_DESIGN.md` (revision 2), phase by
phase, with the production verification each phase is gated on and **every deviation from the
approved design stated and justified before it is taken**.

| phase | scope | state |
|---|---|---|
| **0a** | comparable-set readiness probe (design §11 items 1, 2, 3, 8) | **PASS on `f72f4e9`** — 339/779 clusters (bar: 100) |
| 0b | generation-dependent readiness (§11 items 4–7) | harness built; run blocked on a reachable provider |
| **1** | contract extension + validation + worker scale (dormant) | **verified dormant on `f72f4e9`** — 0 rows, `insights: null` |
| 2 | enablement on the designated recipe | blocked on 0b |
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

---

## Phase 1 — the contract extension and the worker (dormant)

Built before the Phase 0a gate result is in, deliberately and within D1's reasoning: Phase 1 is not
a tier. It changes no behaviour while `RWE_INSIGHTS_ENABLED` is unset, and **Phase 2 (enablement)
remains blocked on both the 0a gate and the 0b measurements**. The design's §3.1 argument also
applies with force: `article_insights` holds **zero rows**, so a contract change invalidates
nothing today and would invalidate the whole catalog after enablement. This was the free moment.

### What was built

| file | change |
|---|---|
| `examples/insights_provider.py` | the port gains optional `temperature`; both adapters pass it only when set |
| `examples/article_insights.py` | the `facets` contract: vocabularies, prompt, validator, span verification, `recipe_hash`, `TruncatedOutput`, `MAX_TOKENS`, `concurrency()`, `scope()`, a bounded worker pool |
| `examples/store.py` | `article_insights` gains `facets` / `input_chars` / `recipe_hash`; `finish_insights` stores them; `get_insights` returns them; `enqueue_insights` orders **cluster-first** |
| `examples/api_fastapi.py` | the wire projection — reader content only |
| `deploy/docker-compose.yml` | `RWE_INSIGHTS_CONCURRENCY` / `_SCOPE` / `_TEMPERATURE`, dormant defaults |
| `tests/test_article_insights.py` | +25 tests; `tests/test_insights_ollama.py` updated for the port |

### The invariant, enforced in code

`generate()` builds the prompt from **one article's** text and nothing else, and the prompt now
says so explicitly — *"a structured record of THIS article only. Never compare it to other
coverage — you have not been shown any."* A test asserts that sentence is present, because the
facets schema *looks* like a comparison schema and a later prompt edit could quietly invite the
model to compare. Nothing in this phase reads two articles.

### Span verification is the load-bearing rule

Every facet item carries an `evidence` span, and an item whose span is not verbatim in the article
text is **dropped** — the item, not the record. Whitespace-normalised, case-insensitive
containment; nothing cleverer, deliberately, because the value of this gate is that it cannot be
argued with. An invented aspect becomes a false statement about a named publisher the moment it is
counted.

Two consequences worth stating:

- `parse_and_validate(raw)` **without** the article text drops every span-bearing item rather than
  trusting it. A caller that forgets the text gets an empty facets object, never an unchecked one.
- The one open field that survives (`quantities.subject`) is validated for shape and passed through
  the no-label rule, which now covers `voices.name` too.

### Deviation D3 — `insights` on the wire is projected, not passed through

**Approved design:** §3.2 defines the stored artifact; it does not say what `/api/analyze` serves.

**What was built:** the endpoint serves `summary`, `bias`, `model`, `generatedAt` only. `facets`,
`inputChars` and `recipeHash` stay server-side.

**Justification.** `AnalysisModel.insights` is a free `dict`, so the new fields would have reached
the wire automatically. No client reads them — the tiers consume facets on the story-build seam and
emit findings into `coverageComparison` — so shipping them would add roughly a kilobyte of
internals to every analyze response with a cached insight, and would publish `recipeHash`, an
operational detail, to anyone with a browser. The store still returns the full record to
server-side consumers; only the boundary projects.

### Deviation D4 — `PROMPT_VERSION` starts at 2

The constant is introduced at 2 rather than 1 because the prompt this phase ships is the *second*
prompt the product has had. Records generated by the original prompt (none exist in production, but
they can exist in a developer's database) are not comparable to these, and a version starting at 1
would claim they were.

### The throughput fix

The review's blocking finding was arithmetic: 144 cycles/day × batch 6 = **864/day** against
~1,250 clustered articles/day arriving. Three changes, in the order that matters:

1. **Scope** — `RWE_INSIGHTS_SCOPE=clustered` restricts the queue to articles that are in a story,
   which is where a card can result.
2. **Cluster-first ordering** — `enqueue_insights` now sorts by `(has deficit, deficit, -size)`.
   Clusters closest to crossing `MIN_COMPARABLE` come first, then the largest. A test isolates the
   rule that size alone would get backwards: a 4-member cluster two artifacts short beats a
   6-member cluster that has none, because only the first turns the next two generations into a
   comparison.
3. **Bounded concurrency** — `RWE_INSIGHTS_CONCURRENCY` (default **1**, exactly today's serial
   behaviour) runs generations in a pool inside one cycle. The single-flight lock stays: it guards
   overlapping *cycles*, which is a different concern from parallelism within one. This is the same
   shape push delivery (B2c) already runs on this seam.

The join that makes ordering possible is canonical: `story_member.url` is the coverage member's
**publisher** url while insights are keyed **canonical**, so `_story_of` canonicalizes rather than
joining raw columns — the defect that invalidated the first coverage audit, and the one join that
has to be right for cluster-first to order anything at all.

### Truncation

`max_tokens` rose from 700 to 1,000, and truncation is now its own failure type. The old code could
not tell "the response stopped mid-JSON" from "the model ignored the contract", and since three
failures mark an article terminally `failed`, misfiling a budget problem as a contract violation
would have permanently destroyed coverage on the richest articles. `TruncatedOutput` subclasses
`ValueError`, so every existing caller still catches it, and `insights_truncated_total` makes the
budget visible.

The first heuristic checked only the tail and classified *any* non-JSON prose as truncation — an
existing test caught it. Truncation now requires that the answer **started** as JSON and did not
finish.

### Tests and gates

- `tests/test_article_insights.py` — 48 passed (25 new: facets round-trip, span verification and
  its whitespace tolerance, item-level dropping, closed-vocabulary rejection, caps, the no-label
  extension, truncation vs malformed JSON, concurrency, scope, recipe stamping, cluster-first
  ordering, the deficit rule, the wire projection).
- Full engine suite — **2,725 passed**, 1 skipped (`test_plot_axis.py` still cannot collect here:
  `matplotlib` absent, pre-existing).
- `npx tsc --noEmit` — clean.
- Two of my own tests were wrong before they were right: a helper used `str.rstrip` with a
  character set (turning "small" into "s"), and the deficit test passed only because the rows it
  checked were no longer *pending*. Both rewritten to test the rule rather than a side effect.

### Production verification — required before Phase 2

```bash
cd /opt/ih && sudo bash deploy/ops/update.sh <sha>
# 1. still dormant: no rows, no behaviour change
sudo docker exec -i deploy-api-1 python - <<'PY'
import sys
sys.path.insert(0, "/app/examples")
from sqlalchemy import func, select
import article_insights as ai, insights_provider as ip, store as store_mod
st = store_mod.Store()
print("enabled            :", ai.enabled())
print("provider           :", ip.from_env())
print("batch/concurrency  :", ai.batch_size(), "/", ai.concurrency())
print("scope/temperature  :", ai.scope(), "/", ai.temperature())
print("max_tokens         :", ai.MAX_TOKENS)
print("recipe (dormant)   :", ai.recipe_hash(None))
with st._Session() as s:
    print("article_insights rows:", s.execute(
        select(func.count()).select_from(store_mod.ArticleInsight)).scalar())
PY
# 2. the analyze contract is unchanged while dormant
sudo docker exec -i deploy-api-1 python -c "
import sys; sys.path.insert(0,'/app/examples')
import article_analyzer, store; st=store.Store()
r=article_analyzer.analyze(st,'https://www.bbc.com/news')
print('insights:', r['insights'], '| coverageComparison key:', 'coverageComparison' in r)"
```

Expected: `enabled False`, `provider None`, `concurrency 1`, `scope all`, **0 rows**, and
`insights: None` — the feature inert and the request path byte-identical. The columns exist and are
empty, which is the point of landing the contract before enablement.

*(Results are recorded here once the run comes back.)*

---

## Phase 0b — the extraction-quality harness (built, not yet run)

Phase 0b cannot be *run* without a provider, but all of it can be **built**, so that enabling the
pipeline and getting the five numbers is one command rather than a project. Nothing here changes
production behaviour.

### What was built

| file | role |
|---|---|
| `examples/facet_quality.py` | the measures: Cohen's κ, set-field Jaccard, throughput arithmetic, and `KAPPA_SHIP_BAR` |
| `examples/benchmark_insights.py` | records span drops, facet drops, truncation and repeat index per call; reports them against the bar |
| `examples/coverage_insights.py` | `with_insight` + `comparable_set` — stage 2 (recipe + format parity) |
| `examples/audit_coverage_readiness.py` | `--with-insights`: the TRUE comparable set instead of the upper bound |
| `tests/test_facet_quality.py` (26), `tests/test_benchmark_insights.py` (+6), `tests/test_coverage_insights.py` (+8) | |

### The five numbers, and where each comes from

| metric | source | bar |
|---|---|---|
| **inter-rater agreement** | `--repeats 2`: extraction 0 and extraction 1 are the two raters | **κ ≥ 0.6** per field, pre-registered |
| **span-verification success** | production counters read around each call, not recomputed | reported; a high drop rate means the model invents quotations |
| **truncation rate** | `TruncatedOutput` counted apart from validation failures | ~0; non-zero ⇒ raise `max_tokens` |
| **throughput** | p50 latency projected onto the 600 s cycle at 1 and 8 concurrency | must exceed the arrival rate from probe §3 |
| **comparable-set coverage** | `audit_coverage_readiness.py --with-insights` | the design's ≥ 100 clusters, now measured for real |

### Why κ and not raw agreement

A model that answers `news_report` every time agrees with itself 100% of the time and has
discriminated nothing. κ discounts chance agreement, which is exactly that flattery. The report
prints the category count beside κ so the degenerate case is visible: κ = 1.00 with
`categories: 1` is a constant, not a signal, and the honest response is to drop the field from a
tier rather than celebrate it. `None` is treated as a real category throughout — stability of the
model's refusal matters as much as stability of its choice.

Set-valued fields (`frames`, `voices`, `quantities`) are scored by mean Jaccard instead, because κ
does not apply when a rater assigns several labels at once. Free text is excluded from both: a
voice's `name` and a quantity's `subject` would make every extraction disagree and would measure
prose style, not label stability — and no tier counts them.

### Deviation D5 — `comparable_set` (stage 2) lands in Phase 0b, not with the tiers

**Approved design:** §4 defines the full rule; §13 puts the tiers in phases 3–5.

**What was built:** stage 2 now, unused by any serving path.

**Justification.** "Comparable-set coverage" is one of the five numbers Phase 0b has to report, and
the *true* figure requires recipe and format parity — the structural upper bound cannot answer it.
Implementing the rule inside the measurement would have created a second copy of production logic,
which is precisely what `coverage_insights.py` exists to prevent. The function is pure, tested, and
read by nothing but the probe until the tiers arrive.

### Span verification measured from production counters

The harness reads `insights_span_unverified_total` and `insights_facet_dropped_total` around each
call rather than re-deriving the drop rate. A second implementation of the rule would eventually
disagree with the first, and then the report would be about the harness. A test pins this: a stub
model returning facets whose evidence is *not* in the article must show every item dropped and zero
items kept.

### Local model: not runnable here

`ollama` is absent from this container and `registry.ollama.ai` is unreachable through the proxy
(both re-checked, not recalled). Phase 0b therefore has to run where a provider exists — the box
with `OLLAMA_HOST` set, or with a key. The harness reports `skipped — no Ollama server answering`
rather than failing, which is what a benchmark should do when a target is unavailable.

### Gates

- `tests/test_facet_quality.py` 26 · `tests/test_benchmark_insights.py` 21 ·
  `tests/test_coverage_insights.py` 31.
- Full engine suite — **2,764 passed**, 1 skipped.
- The harness was smoke-run end to end; with no provider available every target skipped, so the
  new section is exercised by tests against a local stub server instead.

### Running Phase 0b

```bash
# quality, against whatever provider is configured (ollama needs no key)
sudo docker exec -e RWE_INSIGHTS_PROVIDER=ollama -e OLLAMA_HOST=<host:port> -i deploy-api-1 \
  python examples/benchmark_insights.py --repeats 2 --sample-production 40 --seed 7 \
  --out /tmp/insights_0b.md
sudo docker exec -i deploy-api-1 sed -n '/Extraction quality/,$p' /tmp/insights_0b.md

# comparable-set coverage, AFTER a generation run has produced facets
sudo docker exec -i deploy-api-1 python examples/audit_coverage_readiness.py --with-insights
```

`--repeats 2` is not optional: one extraction cannot tell you whether a label is stable, and the
report says so rather than printing a number it cannot support.

---

## Runbook — the operator steps for Phases 0a, 1 and 0b

Deploys and box commands are run by the operator; this section is the exact sequence, with the
decision each step feeds. **Stop where a gate says stop** — that is the point of having gates.

### Step 1 — deploy

```bash
cd /opt/ih && sudo bash deploy/ops/update.sh <sha>
```

### Step 2 — Phase 0a gate

```bash
sudo docker exec -i deploy-api-1 python examples/audit_coverage_readiness.py --show 10
```

`GATE ... PASS` needs ≥ 100 clusters reaching `MIN_COMPARABLE`. **On FAIL the roadmap stops here**
and no generation has been paid for. Also read: item 2 (whether `RWE_INSIGHTS_MIN_CHARS` should
move off 200), item 3 (the batch size the arrival rate demands), item 8 (whether the 0.9
syndication threshold is calibrated — a very high fold rate means the constant is wrong, not that
the catalog is all wire copy).

### Step 3 — Phase 1 dormancy

```bash
sudo docker exec -i deploy-api-1 python - <<'PY'
import sys; sys.path.insert(0, "/app/examples")
from sqlalchemy import func, select
import article_insights as ai, insights_provider as ip, store as store_mod
st = store_mod.Store()
print("enabled           :", ai.enabled())
print("provider          :", ip.from_env())
print("batch/concurrency :", ai.batch_size(), "/", ai.concurrency())
print("scope/temperature :", ai.scope(), "/", ai.temperature())
print("max_tokens        :", ai.MAX_TOKENS)
with st._Session() as s:
    print("article_insights rows:", s.execute(
        select(func.count()).select_from(store_mod.ArticleInsight)).scalar())
PY
sudo docker exec -i deploy-api-1 python -c "
import sys; sys.path.insert(0,'/app/examples')
import article_analyzer, store
print('insights:', article_analyzer.analyze(store.Store(),'https://www.bbc.com/news')['insights'])"
```

Expect `enabled False`, `provider None`, `concurrency 1`, `scope all`, **0 rows**, `insights: None`.

### Step 4 — a local provider, for a free Phase 0b

Ollama on the **host**, not in the api container:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2:3b && ollama pull qwen2.5:3b
# `systemctl edit` opens an editor; write the drop-in directly instead, and avoid heredocs —
# a mangled paste leaves the override unwritten and the failure only shows up as "skipped".
sudo mkdir -p /etc/systemd/system/ollama.service.d
printf '[Service]\nEnvironment="OLLAMA_HOST=0.0.0.0:11434"\n' \
  | sudo tee /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

**The container cannot reach `127.0.0.1`** — inside the container that is the container. It needs
the gateway of **its own network**, and for a compose stack that is NOT `docker0`: the api service
sits on the project's user-defined bridge (`deploy_default`), whose gateway is `172.18.0.1` on this
box. Reading `docker0` gives the wrong address, and on a host with no default-bridge containers it
gives an empty one — which silently becomes `OLLAMA_HOST=:11434` and fails in 0.2 s.

Ask the container what its own gateway is, then prove it can reach the port:

```bash
GW=$(sudo docker exec -i deploy-api-1 python -c "
import socket, struct
with open('/proc/net/route') as f:
    for line in f.readlines()[1:]:
        p = line.split()
        if p[1] == '00000000':
            print(socket.inet_ntoa(struct.pack('<L', int(p[2], 16)))); break")
sudo docker exec -i deploy-api-1 python -c "
import urllib.request
print(urllib.request.urlopen('http://$GW:11434/api/tags', timeout=5).status)"
```

A 200 means the next step will run; anything else means the benchmark reports every target
`skipped` and measures nothing. Two failures to check for first, in this order:

```bash
systemctl show ollama -p Environment    # must contain OLLAMA_HOST=0.0.0.0:11434
sudo ss -lntp | grep 11434              # must LISTEN on 0.0.0.0, not 127.0.0.1
```

The installer binds to `127.0.0.1` by default, so the drop-in above is required, not optional.
`ConnectionRefused` from the container means the bind; a *hang* means a host firewall
(`sudo ufw allow from 172.16.0.0/12 to any port 11434`).

### Step 5 — Phase 0b

```bash
GW=$(sudo docker exec -i deploy-api-1 python -c "
import socket, struct
with open('/proc/net/route') as f:
    for line in f.readlines()[1:]:
        p = line.split()
        if p[1] == '00000000':
            print(socket.inet_ntoa(struct.pack('<L', int(p[2], 16)))); break")
sudo docker exec -e OLLAMA_HOST=$GW:11434 -e RWE_INSIGHTS_PROVIDER=ollama \
  -e RWE_INSIGHTS_OLLAMA_TIMEOUT=300 -i deploy-api-1 \
  python examples/benchmark_insights.py --targets ollama/llama3.2:3b,ollama/qwen2.5:3b \
    --repeats 2 --sample-production 40 --seed 7 --out /tmp/insights_0b.md
sudo docker exec -i deploy-api-1 sed -n '/Extraction quality/,$p' /tmp/insights_0b.md
```

`--repeats 2` is not optional: extraction 0 and extraction 1 are the two raters, and one
extraction cannot tell you whether a label is stable. This run **writes nothing** — the harness
calls `generate` directly, never `run_cycle`, so no `article_insights` row is created.

Read against the pre-registered bars:

| number | bar | what a miss means |
|---|---|---|
| κ per field | ≥ 0.6 | that field's tier does not ship. `format`/`frames` gate C1, `quantities` gates C2, `voices` gates C3 |
| κ = 1.00 with `categories: 1` | — | a constant, not a signal: the field is stable and uninformative, so drop it from the tier |
| span drops | reported | high = the model invents quotations, and the gate is doing its job |
| truncation rate | ~0 | raise `MAX_TOKENS`; do not read it as a quality problem |
| throughput @1 / @8 | > the arrival rate from step 2 | sets `RWE_INSIGHTS_BATCH` and `RWE_INSIGHTS_CONCURRENCY` |

### Step 6 — comparable-set coverage (needs real rows)

Only after steps 2–5 pass. This is the first step that **writes**:

```bash
# deploy/.env — sized from steps 2 and 5, not guessed
RWE_INSIGHTS_ENABLED=1
RWE_INSIGHTS_PROVIDER=ollama
RWE_INSIGHTS_MODEL=llama3.2:3b
OLLAMA_HOST=172.17.0.1:11434
RWE_INSIGHTS_SCOPE=clustered      # spend only where a card can result
RWE_INSIGHTS_BATCH=<from step 2 item 3>
RWE_INSIGHTS_CONCURRENCY=<from step 5 throughput>

sudo bash deploy/ops/restart.sh api
# let it run, then:
sudo docker exec -i deploy-api-1 python examples/audit_coverage_readiness.py --with-insights
```

That prints the TRUE comparable set — recipe and format parity applied — rather than the
structural upper bound. **Phase 2 begins only if that still clears 100 clusters and step 5's κ
cleared 0.6 for the fields C1 uses.**

*(Results are recorded here as they come back.)*

---

## Results — Phase 0a and Phase 1 dormancy, on `f72f4e9` (2026-08-03)

### Phase 0a: **PASS**

```
stories in the served view : 1,772
clustered articles         : 7,440   (window 6 days)
clusters past the L0 gates :   779
members resolved in catalog: 7,440 / 7,440        <- the canonical join, 100%

comparable-set size (UPPER BOUND)
  support units per cluster : p10 0  median 2  p90 6  max 29
  clusters reaching >= 3    : 339 / 779 (43.5%)
  GATE                      : PASS  (needs >= 100)
```

**The gate clears by 3.4×, and the distribution is the more important number.** The median gated
cluster reaches **2** support units — below the threshold — and the bottom decile reaches zero. So
43.5% of gated clusters can carry an insight-derived card, against L0's 66.7% of *members*, and
this is the **upper bound**: recipe and format parity can only reduce it. The reach of C1–C3 will
be materially narrower than L0's, and that expectation belongs here rather than being discovered at
Phase 3. The samples show the reaching set is dominated by large clusters (29 units on the
Spider-Man cluster, 22 on the Purja avalanche), so member-weighted coverage is plausibly higher
than 43.5% — but that is an inference, not a measurement, and the probe reports clusters.

### Eligibility sensitivity — hold `RWE_INSIGHTS_MIN_CHARS` at 200

```
generator input length : p10 134  median 310  p90 549
  min_chars=150  eligible 6,549 (88.0%)   clusters reaching: 384
  min_chars=200  eligible 5,670 (76.2%)   clusters reaching: 339
  min_chars=250  eligible 4,379 (58.9%)   clusters reaching: 274
```

Loosening to 150 buys 45 clusters (+13%) by feeding the model 134-character inputs; tightening to
250 costs 65. **Hold at 200 until Phase 0b reports κ**, because whether extraction survives short
input is exactly what that measures — moving the floor first would change the thing being measured.

### Arrival rate — the review's arithmetic, confirmed on the box

```
clustered articles/day        : 1,240
…eligible at min_chars=200    :   945/day
required RWE_INSIGHTS_BATCH   :    11   (600 s cycle, 1.5x headroom)
today's default 6             →   864/day capacity
```

**864 < 945: the queue diverges by ~81 articles/day.** The pre-implementation review predicted this
from ~1,250/day and a 864/day ceiling; the measured eligible rate is lower than that estimate but
still above capacity, so the conclusion holds and `RWE_INSIGHTS_BATCH` must be ≥ 11 at enablement.

### Syndication — the 0.9 threshold is well calibrated

```
members in gated clusters : 4,977
after wire collapse       : 4,650   (6.6% folded)
clusters with wire copy   :   190 / 779 (24.4%)
largest groups            :  11x:1  7x:1  6x:1  5x:6  4x:16  3x:52
```

This was the constant most at risk of being wrong, and it is not. A quarter of gated clusters
contain wire copy, and folding removes 6.6% of members — it is finding real syndication without
flattening distinct reporting. The single 11× group is one act of journalism that would otherwise
have counted as eleven corroborating outlets, which is precisely the overstatement
`publisher_identity` cannot catch.

### Phase 1: **dormant, confirmed**

```
enabled           : False
provider          : None
batch/concurrency : 6 / 1
scope/temperature : all / 0.0
max_tokens        : 1000
article_insights rows: 0
insights: None                     <- /api/analyze, unchanged
```

Zero rows, the request path byte-identical, and the new columns present and empty — which is the
whole point of landing the contract while `article_insights` is empty.

### Phase 0b: blocked on provider reachability, not on the harness

Ollama installed on the box (CPU-only, `llama3.2:3b` + `qwen2.5:3b` pulled) but its installer binds
to `127.0.0.1`, and the container cannot reach that. Two errors on the way, both now fixed in the
runbook above:

1. **My instruction read `docker0`.** The api service is on the compose bridge `deploy_default`,
   whose gateway is `172.18.0.1`; `docker0` is a different network. Worse, when `docker0` does not
   resolve, `$GW` is empty and `OLLAMA_HOST=:11434` fails in 0.2 s — which looks identical to "the
   provider is down".
2. **The `systemctl edit` drop-in never applied** (`systemctl show ollama -p Environment` returned
   only `PATH`, and `ss` still showed `LISTEN 127.0.0.1:11434`). The runbook now writes the drop-in
   with `printf | tee` on one line rather than a heredoc, because a mangled multi-line paste leaves
   the override unwritten and the failure only surfaces later as `skipped`.

The harness behaved correctly throughout: an unreachable provider is a **reported outcome**
(`skipped — no Ollama server answering`), not a crash, and no row was written.
