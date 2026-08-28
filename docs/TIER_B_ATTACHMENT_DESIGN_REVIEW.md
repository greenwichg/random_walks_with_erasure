# Tier B attachment — a critical design review

**Review only. Nothing implemented.** This challenges `PATH_TO_50K_DECISION_REVIEW.md`, including a
claim in it that I got wrong, and asks whether Tier B attachment is the first binding milestone or a
plausible-sounding detour.

Labels: **[F]** fact verified from code or a production measurement · **[A]** assumption ·
**[R]** unmeasured risk · **[X]** proposed experiment.

---

## 0 · The correction, first

`PATH_TO_50K_DECISION_REVIEW.md` §3 says *"Tier B is a hole, not a tier"* and lists M4 as simply
missing. **That overstated it, and the overstatement matters** because it makes the work look bigger
than it is.

**The attachment algorithm exists, is production-quality, and is already tested** [F]:

```python
# examples/source_evaluation.py
def assignment_index(stories: list) -> tuple:      # (members, postings) over Tier A story members
def would_attach(title, published_at, index) -> Optional[str]:   # the story id it would join
def assignment_rate(rows: list, index: tuple) -> dict:           # {articles, attached, rate, stories}
```

It is blocked by an inverted token index for the same reason `clustering.cluster` is, it is
deterministic by construction (`sorted(...)`, with a docstring explaining that a frozenset iteration
would make results vary between runs), and **it decides with `clustering.pair_admits`** — the single
extracted definition of "same event". 24 tests in `tests/test_source_evaluation.py`.

The roadmap already drew the distinction I blurred [F]:

> **M4 (assignment as a production feature)** — M8 needs assignment as a **measurement**, computed
> offline over a built story set, not as a serving path.

So the honest statement is: **the algorithm is built; the serving wiring and the data shape are
not.** That is a materially smaller gap, and it changes the experiment (§6) from "build a thing" to
"run the thing we already have".

---

## 1 · Is Tier B truly missing an attachment path today?

**Partly. Algorithm: present [F]. Serving path: absent [F]. Data shape: absent [F].**

| piece | state | evidence |
|---|---|---|
| pairwise rule | **built** | `clustering.pair_admits`, extracted so there is one definition |
| attach one article to a story set | **built** | `source_evaluation.would_attach` |
| index over Tier A members | **built** | `source_evaluation.assignment_index` |
| rate/coverage reporting | **built** | `source_evaluation.assignment_rate` |
| *call site in the serving path* | **absent** | no caller outside `audit_shadow_cohort` / tests |
| *a field on the Story to hold the result* | **absent** | `_build_story` returns no such key |
| *fetching Tier B rows at all* | **absent** | `_fetch` passes `exclude_publishers=corpus.sql_exclusions()`, which removes them in SQL |

---

## 2 · What exactly happens to an article when its outlet is placed in Tier B?

Traced through the code, four steps [F]:

1. `corpus.tier_of(publisher, url)` returns `"B"` — identity-based, two-sided (publisher string *and*
   URL host), computed at query time, never stamped on the row.
2. `corpus.sql_exclusions()` includes it, and `story_service._fetch` passes that set as
   `exclude_publishers` — **the row is removed in SQL, before the builder ever sees it.**
3. `corpus.select` would drop it again on the Python side, catching what SQL cannot express (an alias
   the registry learned after ingest, a Tier B host appearing only in the URL).
4. `corpus.shadow_exclusions()` **does not** include Tier B — so the article stays visible to Search
   and Discover.

**Net effect: present in the catalogue, present in Search, invisible to stories.** Not as coverage,
not in `totalCoverage`, not in `publishers`, not in the lean distribution, not in a blindspot claim.

**This is the designed state, not a defect.** `corpus.py`'s contract says Tier B is *"searchable and
attributable; **never enters the story builder**"*, and M4 was always the separate step that lets it
attach *without* entering. My earlier phrasing — "would vanish and gain nothing" — is accurate about
the effect and wrong about the intent, and the difference matters when deciding whether this is a bug
to fix or a feature to finish.

---

## 3 · The smallest architecture change — and the trap in the obvious version

### The trap: attachment cannot mean "append to `members`"

Every field of a Story derives from `members`, and one of them is load-bearing for identity [F]:

```python
def _story_id(members: list) -> str:
    rep = min(members, key=lambda a: (a["publishedAt"] or "~", a["id"] or ""))
    return "st_" + hashlib.sha1(rep["id"].encode(...)).hexdigest()[:16]
```

**A Tier B article published earlier than the current representative changes the story's id.** This
repository has already had to fix reader-feedback durability once for exactly this class of problem
(*"Feedback durable identity: canonical URL keys, generation translator"*). Appending would also move:

| field | why |
|---|---|
| `title`, `summary`, `image*` | `rep` is the earliest member; hero selection ranks members |
| `totalCoverage`, `publisherCount`, `publisherDiversity`, `publishers` | counted over members |
| `clusterTrust` | `_cluster_trust(total, coherence, located, …)` takes the member count |
| blindspot | `_rated_publishers(members) >= min_rated_for_blindspot()` gates it |
| `earliest`/`latest`/`timeline` | min/max over member timestamps |
| geography | `_country_votes`, `_event_consensus`, `_geo_coherence` all read members |

So the naive version silently changes story identity, titles, trust verdicts and blindspot gating —
the four things this codebase is most careful about.

### The change that avoids all of it

**A post-pass, not a builder change.** `build_stories` is not touched at all:

```
stories = build_stories(tier_A_rows)              # unchanged, byte-identical by construction
index   = assignment_index(stories)               # already exists
for row in tier_B_rows:                           # a SECOND fetch, currently not made
    sid = would_attach(row.title, row.publishedAt, index)   # already exists
    if sid: stories[sid]["attached"].append(row)  # a NEW field nothing else reads
```

Four pieces, three of which exist:

1. **fetch Tier B rows** — a second `search_feed_articles` call with the exclusion inverted. *(new)*
2. `assignment_index(stories)` — *(exists)*
3. `would_attach` per row — *(exists)*
4. **a new Story key** (`attached` / `attachedCoverage`) that **no existing field reads** — *(new)*

The containment property the roadmap asked for ("byte-identical") is then true **by construction
rather than by test**: the Tier A story objects are the same objects, with one key added. That is the
strongest form of the guarantee and it is available only if attachment stays out of `members`.

**Cost** [F, from the code's own shape]: `assignment_index` is O(Tier A members); `would_attach` is
O(tokens × postings) per row and is blocked by the inverted index. Against 6,887 members and a few
hundred Tier B rows it is negligible; the *linearity* claim is about |new arrivals|, which this
satisfies by construction — but it has never been timed [R].

---

## 4 · Can the existing 8 Tier B outlets validate it? — **Yes, and they are the wrong population**

This is the sharpest challenge to my own earlier recommendation, which proposed exactly this.

The 8 outlets `audit_source_cohort` gives a TIER B verdict to were demoted for **two reasons only**,
and `verdict()` is explicit that these are the *only* two that demote [F]:

```
iHeartRadio      only 3% of articles on its main host — unstable identity
ETtoday          only 25% of articles on its main host — unstable identity
Brisbane Times   36% of headlines also run under another publisher — republisher
rhein-zeitung    36%   timesunion.com 36%   nürnberger 38%   abc11.com 40%   bbv-net 70%
```

Six of eight are **republishers**. And the measured *benefit* of demoting them was [F, production]:

> in-story articles carrying a title IDENTICAL to another member of the SAME story: **86**

**Attaching them back re-creates those 86 double-counts.** `SOURCE_COVERAGE_AUDIT`'s rationale for
excluding aggregators is precisely *"an aggregator's articles ARE other outlets' articles, so counting
one as a publisher double-counts coverage the cluster already holds."*

So this population would **validate the mechanism while demonstrating the harm**. It answers "does
attachment work" and actively misleads on "is attachment good".

**The population Tier B exists for is the legitimate long tail** — outlets that are fine but too
numerous for a bounded Tier A. That population **does not exist yet** [F: Tier B has 0 members, and
the only demotion reasons are syndication and instability). Any honest experiment must use a *proxy*
for it: legitimate, low-volume outlets currently in Tier A, demoted for capacity rather than for
misconduct. Those are available offline in quantity — 9,397 hosts, 1,173 candidates [F].

---

## 5 · Acceptance criteria

Both halves, or it is half a trade — the lesson `audit_source_cohort` recorded about itself.

### Preservation (Tier A must not move)

| criterion | threshold | instrument |
|---|---|---|
| story **ids** unchanged | **exactly**, 0 changes | set comparison; the identity risk in §3 |
| titles, `totalCoverage`, `publisherCount` unchanged | exactly | `_build_story` output diff |
| blindspot claims unchanged | exactly | `beforeClaims`/`afterClaims` |
| ratified exhibits unmoved | exactly | `_exhibit_outcomes` |
| clusters split / merged | **0 / 0** | `audit_clustering_change` |

These should all be *trivially* satisfied by the post-pass design. **If any of them moves, the
implementation has leaked into `members` and is wrong** — that is the test's real job.

### Benefit (coverage must actually increase)

| criterion | why |
|---|---|
| attached articles, and stories gaining ≥ 1 | the headline number; zero means the mechanism buys nothing |
| **duplicate-title rate among attached articles** | the §4 risk, priced. An attached article whose title matches an existing member is double-counted coverage, not new coverage |
| distinct publishers added per story | coverage breadth is the product claim, not article count |
| attach rate by outlet | separates an outlet feeding one running story from one covering the spread — `assignment_rate` already reports `stories` for this reason |

### Product integrity

**Attached coverage must be distinguishable from Tier A coverage in the payload** [R]. If it is
merged into `totalCoverage`, the product's own coverage claims inflate silently and no reader can
tell an event covered by 20 newsrooms from one covered by 5 plus 15 republishers. This is a design
constraint, not a nice-to-have, and it is the reason §3 insists on a separate key.

---

## 6 · Is Tier B attachment the first binding milestone?

**Among measured constraints: yes.** The Tier A row cap (60,000) binds at ~2× today's window [F], and
Tier B is its designed fix. Nothing else measured binds before that: throughput has 569× headroom,
discovery has 1,173 unspent candidates, admission is built and idle.

**But two unmeasured questions sit upstream of it, and I under-weighted both.**

### 6.1 · Can we even source 5× more outlets? — **completely unmeasured** [R]

The 9,397 hosts came from aggregators, not crawling [F: `RWE_CRAWL_ENABLED` defaults off, 0 sources
admitted]. Each adapter has *our* quotas (`MAX_ARTICLES`, `DAILY_BUDGET`), but the **vendor** quota —
what NewsAPI/NewsData/GNews/MediaStack/Currents will actually serve, and at what price — is nowhere in
the code and has never been measured.

**If aggregator headroom is small, Tier B is premature**: it builds capacity for outlets we cannot
obtain. This is cheap to answer and nobody has.

### 6.2 · Does outlet count improve the product at all? — **unmeasured, and I mis-cited M14 as evidence** [A]

`PATH_TO_50K_DECISION_REVIEW.md` leans on M14's refutation to suggest count may not matter. That is
weaker than I made it sound. M14 tested monotonicity across a stratum **dominated by tiny corpora**
(nine of twelve languages had ≤ 6 peers), where noise dominates. The one clean signal in that data
points the *other* way: English, with ~16× more publishers than any other language, has ~3× the
density of the next best (1.22 vs 0.38 mean partners) [F].

So M14 did **not** show that outlet count fails to help. It showed that it does not predict density
in the thin tail. Using it as evidence against the 50k premise was an overreach on my part.

### 6.3 · The honest ordering

1. **Aggregator headroom** [R] — cheapest to answer, and it can invalidate everything downstream.
2. **Tier B attachment** [F: cap binds at ~2×] — the first *engineering* constraint, and now known to
   be a post-pass rather than a rebuild.
3. Everything else — not binding.

---

## 7 · The smallest offline experiment

**It may require no new production code at all**, because `audit_shadow_cohort --as-if` already does
the hard half [F]:

> `--as-if` — evaluate outlets we ALREADY carry in Tier A, as though they were in shadow. **The Tier A
> story set is rebuilt without them first**, so the index they are scored against does not contain
> their own articles.

That is precisely the Tier B counterfactual: remove outlets from the build, rebuild, and ask
`would_attach` how much of their output would come back. It even asserts the self-assignment guard —
*"no cohort member may appear in the story set it is scored against"* — which is the trap this
experiment could most easily fall into.

**[X] The experiment.** Pick the *legitimate, low-volume* Tier A outlets — the §4 proxy for the real
Tier B population, **not** the 8 syndicators — and run the audit against them.

### 7.1 · The cohort must not be chosen by hand

The first draft of this section said "pick ~20 outlets", which is the experiment's weak point rather
than a detail of it. A population selected after looking at the data can be selected to produce a
result, and nothing in the audit's output would show that it had been. So the rule is pre-registered
in `examples/select_asif_population.py` and the selection is a run, not a judgement:

| filter | why |
|---|---|
| `3 <= articles <= 20` in the window | enough rows for a non-degenerate rate; few enough that removing the outlet cannot reshape the story set it is then scored against |
| `syndication < SYNDICATION_CEILING` | the republisher filter — §4's whole finding, at the policy module's own constant |
| `hostStability >= 90%` | `source_evaluation`'s other demotion cause |
| top host looks like a domain | kills feed-title artifacts without filtering on registry membership |

Registry-tracked is deliberately **not** a filter. Requiring it would bias the cohort toward majors
having a quiet week — the opposite of the low-volume tail being modelled — so tracked/untracked is
reported as a split, and a difference between the two strata becomes a finding instead of a hidden
selection. Eligible outlets are ordered by **name**, an ordering that cannot correlate with the
outcome the way volume can.

### 7.2 · A matching defect found while building the selector [F]

`measure` lower-cases the names the caller types, but `_identity` returns the registry canonical
unmodified — and **571 of the registry's 573 canonicals carry capitals** (`BBC`, `The Guardian`,
`Associated Press`). The canonical branch of the comparison therefore could never fire. An outlet was
reachable only by its raw publisher string, while the script's own unmatched-name message told the
reader the opposite:

> `NOT IN THE CATALOG under this exact string — the name is wrong, **or it resolves to a registry
> canonical**`

So naming an outlet the documented way reported it as absent from the catalogue, and a wrong name
and an unmatchable one were indistinguishable in the output. Fixed by folding both spellings, with
the message corrected and a test that fails on the un-folded comparison. The selector emits **raw
lower-cased publisher strings** regardless, which match on a currently deployed image as well as a
rebuilt one — so the experiment does not wait on a deploy.

```
# 1. choose the cohort by rule (runs inside the image without being baked into it)
dc run --rm -T api python - < examples/select_asif_population.py

# 2. run the command it prints
dc run --rm -T api python examples/audit_shadow_cohort.py --db "$RWE_DB_URL" --as-if "…"
```

An empty selection refuses to print a command, because `--as-if ""` parses to an empty set and falls
back to the **default shadow-lane run** — a different question whose output reads like an answer to
this one.

### 7.3 · What this cohort cannot tell us [A]

The syndication filter is load-bearing for §4's reason, and it also **suppresses the duplicate-title
risk by construction**. A cohort selected to be below the ceiling will attach cleanly more often than
a genuine 50k tail would, so the duplicate-title rate this run reports is a *floor*, not an estimate.
Read it as "even the clean case double-counts this much", never as the rate to expect at scale.

The falsification direction survives intact — a near-zero attach rate on the *most favourable*
population available is decisive against Tier B. A high attach rate is correspondingly weaker
evidence for it, and the honest follow-up is a second run with the syndication filter relaxed, read
against the first.

Read three numbers:

| | falsifies Tier B if | validates if |
|---|---|---|
| **attach rate** | near 0 — Tier B recovers nothing and the tier is a pure demotion | materially > 0 |
| **stories touched** | ≈ 1 per outlet — they feed one running story, not the spread | broad |
| **duplicate titles among attached** | high — attachment restores double-counting, §4's risk generalised beyond the syndicators | low |

The third is the only piece not already reported, and `audit_source_cohort` already computes exactly
that quantity for its own cohort — so it is a small addition to a read-only audit, not production
code.

**Cost:** one command, no network, no ToS exposure, no writes, no new sources. **Decision value:** if
attach rate is near zero, the entire Tier B plan collapses and the roadmap's central claim —
*"Tier B scales to 50k"* — is false for a reason nobody has checked. That is the largest uncertainty
per unit of effort available anywhere on this path.

**Run 6.1 alongside it**, since it is a documentation exercise rather than a measurement and can
invalidate the whole direction.

---

## 8 · What I got wrong, and what remains assumption

| claim | status now |
|---|---|
| "Tier B is a hole, not a tier" | **overstated.** The algorithm is built and tested; the wiring and data shape are not |
| "M4 is not built" | **true but incomplete** — the roadmap already separates the measurement from the serving path, and only the latter is missing |
| M14 as evidence that outlet count may not matter | **overreach.** M14 tested the thin tail; its one clean signal points the other way |
| "the 8 TIER B outlets are the natural test population" | **wrong.** Six are republishers; attaching them re-creates the 86 double-counts their demotion removed |
| Tier B attachment is the first binding milestone | **holds among measured constraints**, but aggregator headroom is unmeasured and sits upstream |
| attachment cost is linear | **[A]** — the shape supports it; never timed |
| a story with attached Tier B coverage is still the product | **[R]** — unanswered, and it is a product decision, not a measurement |
| "pick ~20 outlets" for the experiment | **too loose.** A hand-picked cohort can be picked to produce a result; §7.1 replaces it with a pre-registered rule |
| `--as-if` accepts the registry canonical | **was false** — the case fold made it unmatchable for 571 of 573 outlets. Fixed in this commit |
