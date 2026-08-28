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
dc run --rm -T api python examples/audit_shadow_cohort.py --db "$RWE_DB_URL" --as-if-select
```

**One command, because two cost two production runs** [F]. The first version printed the
cohort for a human to paste into a second command, and both times the placeholder text in the
instructions reached the shell verbatim — `<~20 legitimate low-volume Tier A outlets>`, then
`<the list it printed>`. The unmatched-name guard caught both and refused to report, which is
the guard working; a guard firing twice on the same cause is also the argument for removing
the cause. `--as-if-select` calls the selector's `cohort_names()` on the rows the audit has
already fetched, so the list never crosses a shell and the corpus is read once. Naming a
cohort and deriving one are mutually exclusive — passing both is refused rather than resolved
by precedence, since the run's own header would otherwise describe a cohort nobody asked for.

`select_asif_population.py` remains runnable on its own; it is now the way to *inspect* the
cohort and the exclusion census, not a step the experiment depends on.

An empty selection refuses to print a command, because `--as-if ""` parses to an empty set and falls
back to the **default shadow-lane run** — a different question whose output reads like an answer to
this one.

### 7.3 · First production run of the selector — and why the cohort is capped [F]

```
Tier A window : 29,451 articles across 4,972 outlets
qualified     : 1,058 outlets, 6,445 articles (21.9% of Tier A)
  articles < 3   3,508     articles > 20   164     syndication >= 35%   435
  hostStability < 90%  266     host not a domain  0     comma in a spelling  2
```

Three things this settles, and one it breaks:

* **Tier A already carries 4,972 distinct outlets in a 6-day window** [F]. The 50k target is
  ~10× that, not the ~300× the roadmap's framing implies. Worth re-reading §6 against.
* **3,508 outlets — 71% — published fewer than 3 articles in six days.** The tail is not
  hypothetical; we are already ingesting it. This is the strongest available evidence that
  §6.2's question ("does outlet count improve the product?") is the live one.
* The syndication filter removed 435 outlets and the comma filter fired twice, so both do
  work on real data rather than only on fixtures.

**A false pass the run exposed** [F]. Barron's, the Charlotte Observer, 9news.com and the
Daily Beast all appeared with `topHost = news.google.com` at 100% host stability. An article
ingested through Google News RSS carries the aggregator's host, so a filter meant to catch
*scattered* rows was passing on a domain that is not the outlet's. `publisher_metadata`
already documents this exact trap — *"an aggregator's domain says who delivered the article,
not who wrote it"*, after comparing `news.google.com` against Wikidata refused the Associated
Press, Reuters, CBS News, Forbes, CNBC, Politico and the Washington Post. Host stability is
now measured over the outlet's **own** hosts, using `source_discovery.is_proxy_host` rather
than a third copy of the proxy list, with the denominator left as every article — so an
outlet reaching us half through an aggregator scores 50% and is excluded. Aggregator-only
outlets get their own census line, since how much of the catalogue arrives proxied is worth
seeing rather than filing beside genuine junk hosts.

**What it breaks: 21.9% is far too large a cohort.** `--as-if` rebuilds Tier A *without* the
cohort, so the cohort is also the perturbation. Removing a fifth of the corpus destroys
stories that only two cohort outlets carried (`min_publishers = 2`), and those articles then
cannot attach to a story that no longer exists. A low attach rate would be unreadable — the
corpus was gutted, or Tier B recovers nothing, and the run could not distinguish them.

So the selector now caps the cohort at `--share` (default 5%) via a **hash-ordered draw**.
Name order is neutral for listing and wrong for truncating: a prefix of an alphabetical list
takes everything beginning with a digit or an early Latin letter and drops the Cyrillic,
Greek, Arabic and CJK names outright — on this corpus, a language filter wearing a sampling
filter's clothes. Hashing the identity is deterministic, repeatable, and blind to script.

**The perturbation has a direct control.** The audit prints `Tier A built: N articles -> M
stories`. The un-perturbed baseline is **29,336 → 1,630 stories**. If the rebuild without a
5% cohort still prints ≈1,600, the perturbation is small and the attach rate is a clean read;
if it prints materially fewer, the cohort is still too large and `--share` must come down.

### 7.4 · The baseline, and why it is a ceiling rather than a target [F]

The run that caught the second placeholder still printed a full, unperturbed build on the
current window:

```
Tier A built  : 29,481 articles -> 1,644 stories (6,999 covered)
```

Two things follow.

**The perturbation control is now exact.** A rebuild minus a 5% cohort should land near 1,644
stories. Materially fewer means the cohort is still too large and `--share` must come down
before the attach rate can be read at all.

**Only 23.7% of Tier A articles are in a story** (6,999 of 29,481). Three quarters of what we
already ingest into Tier A joins nothing — which is worth holding next to §6.2's question
about whether outlet count is the constraint.

But 23.7% is **not** the number the cohort is trying to match. `would_attach` asks whether an
article would join an **existing** story; it cannot measure *founding*. Two cohort outlets
that would have started a story together score zero. That is correct for the Tier B contract —
Tier B never participates in formation — but it means the Tier A participation rate is an
**upper bound on what an ordinary outlet would score under this measurement**, not a target.
A cohort rate materially below 23.7% is therefore not evidence of a worse outlet; only a rate
near zero carries the falsifying weight, which is why §7 puts the decision there.

### 7.5 · The experiment ran — and the runner did not print its headline number [F]

```
mode          : --as-if: 254 of 254 named outlets, rebuilt without them
Tier A built  : 27,980 articles -> 1,582 stories (6,737 covered)
cohort        : 1,470 articles
outlets       : 254   tracked 29   rated 26   [membership guard passed: 0 self-scored]
```

Everything procedural held. All 254 names matched, the self-scoring guard passed, and the
**perturbation control came in at 1,582 stories against a 1,644 baseline — a 3.8% loss for a
5.0% cut**, close enough that the cohort is a fair sample rather than a corpus amputation.

Then the run could not answer the question. `attached` is printed **per outlet**, truncated to
`--show` (30) rows out of 254. Summing the visible rows gives 83 attachments; the invisible
224 outlets can each hold 0 or 1, so the population rate is bounded only to **5.6%–20.9%** —
useless for a decision. Summing the table's `stories` column would have been worse than
useless: it is distinct *per outlet*, so every story two cohort outlets both touch is
double-counted, and the error flatters Tier B.

**This is a defect in §7 of this document, not only in the runner.** §5 named the acceptance
criteria — attached count, stories gaining coverage, duplicate-title rate, publishers added,
attach rate — and §7 then asserted that the duplicate-title rate was *"the only piece not
already reported."* That was wrong, and checking it would have cost one reading of `main`. The
cohort-level attach rate, the story union and the publisher count were all absent too. An
acceptance criterion nobody checked the instrument can emit is the same defect class as a gate
that cannot fire.

`cohort_assignment` now computes all of them over the whole cohort, printed **before** the
per-outlet table, using the same `se.would_attach` the table uses so there is no second
definition of "attaches". The 3.8%/5.0% control above stands and the run is re-runnable; only
the reporting was missing, so nothing measured has to be discarded.

### 7.6 · The result [F]

```
Tier A built  : 27,999 articles -> 1,582 stories (6,735 covered)
cohort        : 1,472 articles across 254 outlets

would attach   : 120 of 1,472 articles (8.2%)
distinct stories: 95   outlets landing at least one: 67 of 254
publishers added: 67
duplicate titles: 19 of 120 attached (15.8%)
```

**The control reproduced exactly**: 1,582 stories on both runs against a 1,644 baseline — a
3.8% story loss for a 5.0% article cut, and the same figure twice, so the cohort really is
deterministic. Nothing about the corpus was mangled to get this number.

**Against the pre-registered bars:**

| criterion | bar | result | verdict |
|---|---|---|---|
| attach rate | near 0 falsifies | 8.2% | **not falsified** |
| stories touched | ≈1 per outlet = feeding one running story | 95 stories from 67 outlets, 1.26 articles per touched story | **broad, not concentrated** |
| duplicate titles | high = restored double-counting | 15.8%, well under the 35% ceiling | **mostly real coverage** |

So Tier B attachment is **not falsified**, and the coverage it would add is mostly genuine
rather than syndication returning by the back door.

**But the rate is a floor, not a measurement**, and the reason is a defect this run exposed in
the instrument. `would_attach` returns `None` when a title yields fewer than `MIN_TITLE_TOKENS`
tokens, and the shipped tokenizer is ASCII. The cohort carries 東森新聞 (18 articles), عدن الغد
(19), youm7.com (15), al bayan (13), alyaum (13), 뉴시스, 日テレnews nnn and more. **Every one of
those articles scores zero before the pair rule runs.** Counting them in the denominator
reports "tested and did not match" for an article that could never have matched — a gate that
cannot fire, read as a gate that passed, which is the failure this series keeps correcting.
`cohort_assignment` now reports the rate over reachable articles beside the floor, so the
re-run separates evidence about Tier B from evidence about the tokenizer.

**The reading that matters for the roadmap, and it cuts against Tier B.** 92% of tail articles
and **187 of 254 outlets (74%) contribute nothing to any story** even with attachment built.
For three quarters of the tail, Tier B is search-only. Attachment adds ~1.26 articles to 95 of
1,582 stories — **6.0% of the story set gains one more source**. Whether that is worth building
is a product judgement, not a measurement, and this document should not pretend otherwise. The
experiment did what it was for: it removed the possibility that the answer was zero, and it
bounded the upside at something modest.

### 7.7 · The corrected result, and what it recommends [F]

```
Tier A built  : 28,011 articles -> 1,586 stories (6,745 covered)
cohort        : 1,473 articles across 255 outlets

would attach    : 122 of 1,473 articles (8.3%)
... of REACHABLE: 122 of 1,156 (10.5%) — 317 articles yield fewer than 3 tokens
distinct stories: 96   outlets landing at least one: 68 of 255
duplicate titles: 20 of 122 attached (16.4%)
```

**The measurement is 10.5%**, not the 8.3% floor. The control held again — 1,586 stories
against a 1,644 baseline, a 3.5% story loss for a 5.0% article cut, the third consistent
reading.

**The tokenizer gap is 21.5% of the cohort, and it is symmetric.** `assignment_index` applies
the same `MIN_TITLE_TOKENS` filter to *story members*, so a story built entirely from unspaced
scripts never enters the index either — verified directly: a two-member Chinese story yields
**0 indexed members**. Those 317 articles therefore have nothing to attach **to**. Counting
them differently is not the fix; the tokenizer is, and it has to change on both sides. That is
`story_service.unicode_words()` in `"fallback"` mode, which fires *only* when ASCII yields
fewer than `MIN_TITLE_TOKENS` and so cannot disturb any headline that already tokenizes.

This is not a new finding — `unicode_words`'s own docstring records it from the other
direction: those languages contributed *"472 window articles and **one** in-story article —
0.2%, against 29% for English… not a participation problem with a tuning answer; it is a
structural exclusion."* The Tier B cohort reached the same conclusion by an independent route.

**Recommendation: do not build Tier B attachment next.** [X]

The experiment did its job and the answer is "real but small". Attachment would add ~1.27
articles to 96 of 1,586 stories — **6.1% of the story set gains one more source** — while
**187 of 255 tail outlets (73%) contribute nothing to any story even with it built**. Those
outlets are already ingested and already searchable, so attachment does not unlock the tail;
it improves a twentieth of the story set.

**Run `--unicode-fallback` instead.** It is already built, defaulted off, and unmeasured; it
targets exactly the 21.5% this run found structurally excluded; it affects **Tier A quality
directly** rather than only Tier B's marginal contribution; and it is one command with no new
code:

```
dc run --rm -T api python examples/audit_clustering_change.py --db "$RWE_DB_URL" \
    --unicode-fallback
```

Read it on the reach table, the same way `--unicode-words` was read and **rejected** (78
rescued against 149 lost). Fallback is the milder variant and may fail the same bar — but it
is the cheapest open question on this path, and unlike Tier B attachment it cannot be answered
by argument.

### 7.8 · The tokenizer fix is live [F]

`--unicode-fallback` ran on the live catalogue and was **ADOPTED**:

```
  population    articles  covered before    after  dropped   newly
  reachable       26,870           7,019    7,019        0       0
  excluded         2,641               0       79        0      79
```

79 structurally-excluded articles reached a story against **0 lost** — 0 splits, 0 merges,
blindspot 220 → 220, exhibits unchanged, independent signal identical. The reachable row is the
guard that matters and it moved nothing. Compare `--unicode-words` (replace), rejected at 78
rescued for 149 lost.

**Enabled in production 2026-08-28** (`RWE_CLUSTER_UNICODE_WORDS=fallback`), and confirmed by
two checks that answer different questions:

* `story_service.unicode_words()` prints `fallback` — the **process** sees the variable;
* re-running the audit shows the baseline arm at **1,686 stories** with the excluded population
  already at **79 covered** rather than 0, and applying the flag again buys nothing — the
  **builder** uses it. The instrument reports this as `*** THE BENEFIT IS ZERO`, which is what a
  confirmed adoption looks like from a before/after tool, not a failure.

Two things this did **not** settle. 79 of 2,653 excluded articles is **3.0% reach**: the other
97% still join nothing, because giving a Korean headline tokens does not give it a Korean peer.
And the whole gain is 79 articles against a 29,611-article window. The tokenizer was blocking
the mechanism; **M14's density question is now askable, not answered.**

### 7.9 · What this cohort cannot tell us [A]

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
| "the duplicate-title rate is the only piece not already reported" (§7) | **wrong.** The cohort-wide attach rate, the story union and the publisher count were absent too. One reading of `main` would have caught it before a production run |
| the attach rate is a measurement | **it was a floor.** Unspaced-script titles score zero before the pair rule runs, and I designed a multilingual cohort without checking that the tokenizer could reach it — after spending M14 on exactly this tokenizer |
| Tier B attachment is the first binding milestone (§6) | **no.** Measured at 10.5% attach: 6.1% of stories gain one source, and 73% of tail outlets contribute nothing to stories even with it built. §7.7 recommends `--unicode-fallback` first — it is already built, targets the 21.5% found structurally excluded, and improves Tier A rather than only Tier B |
