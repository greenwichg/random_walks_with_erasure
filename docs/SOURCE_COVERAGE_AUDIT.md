# Source coverage and corpus composition — four audits, and the first adoption

Companion to `CRAWLER_ARCHITECTURE_AUDIT.md`, which covers how we *fetch*. This one covers **which
publishers we carry and what they do once inside**. Parts 2-4 were commissioned as research-only and
all three ended in a rejection; Part 5 is the first cohort actually evaluated against the M1/M2 tier
boundary, and the first to end in an adoption. The numbers are recorded here because a measurement
that closes a line of work is worth as much as one that opens it, and because nearly every one of
these overturned a recommendation I had already made in writing.

**Forward reference:** `SCALE_ROADMAP.md` designs the architecture for a deliberately different
strategic goal — a ~50,000-source universe. Part 2's verdict below priced **blindspot claims only**
and said so; the roadmap is the case where that stated blind spot is the whole point. Part 4's
`news.google.com` finding is why the roadmap's validation stage carries an explicit
aggregator/proxy-host gate.

Same discipline as the clustering arc: register the question, measure on the live catalog, let the
bars decide, keep the retractions visible.

---

# Part 1 — How the comparables onboard sources

Researched 2026-08-25. The pattern is consistent enough to state as a single finding:

> **None of them automates *admission* on crawl signal alone, and the one that comes closest can
> afford to because it makes no political-lean claim.**

| System | Discovery | Admission | Quality metadata |
|---|---|---|---|
| **Google News** | fully algorithmic crawl; manual submission **removed** (2019 → Publisher Center stopped accepting feeds 2025) | policy + quality signals, algorithmic | none of our kind — Google does not rate lean |
| **SmartNews** | broad crawl of "millions of URLs" for *ranking* | **partnership**: ~300 media companies / ~3,000 brands; publisher submits a feed that must validate against their SmartFormat spec | in-house NLP tagging |
| **Inshorts** | partner feeds | editorial | **the top 20% most-read content is entirely editorially curated** and out-performs the 80% AI-curated remainder |
| **Ground News** | ~50k sources | broad, but **licenses ratings from AllSides / MBFC / Ad Fontes** rather than generating them; human-in-the-loop, "Report an Issue" | third-party, human-produced |
| **NewsGuard** | n/a | 9 apolitical criteria, **trained journalist analysts**, multi-layer editor review, right-of-reply | entirely human |

The decisive consequence for Hidden View: **the thing we would have to automate — a lean or
factuality rating — is exactly the thing every comparable system pays humans for.** Ground News is
our closest analogue and explicitly does not generate ratings; it overlays three raters' work.

---

# Part 2 — Source expansion and curation: both measured, both rejected

## The proposal

Use the crawler to discover missing outlets and automatically identify which ones materially
improve coverage.

## The architecture, audited first

**There is no admission gate.** `rss_ingest.ingest_entries` ingests an unknown outlet anyway:

```python
_lean = scored.lean                      # NaN when the registry doesn't know the outlet
if _lean is None or not math.isfinite(_lean):
    stats["unknown_outlet"] += 1         # observational: the article still ingests
```

Only `RWE_CATALOG_BLOCKED_OUTLETS` drops anything. We already ingest arbitrary publishers — GDELT
alone delivers arbitrary-domain URLs. **The registry is an enrichment layer, not a gate.**

`examples/data/outlet_registry.csv`, 573 rows:

| field | filled |
|---|---|
| canonical / aliases | 100% |
| country | 98.6% |
| scope | 97.4% |
| lean | 88.1% |
| **factuality** | **22.7%** (130/573) |
| **credibility** | **12.4%** (71/573) |

An unrated outlet's articles enter the catalog, clustering, Discover and Search — but
`_rated_publishers()` counts only members carrying a `leanBucket`, and a story may assert a
coverage gap only at `>= min_rated_for_blindspot()`. So **unrated coverage inflates story size
while contributing nothing to the blindspot claim that is the product's core value.**

## The measurement (`audit_registry_coverage.py`, 2026-08-25)

```
REGISTRY (the file)  : 573 rows, 505 rated
WINDOW   (the feed)  : 27,877 articles, 1,528 stories
  publisher names    : 4,494
  outlet identities  : 4,083   (411 names are another name's alias)
    rated            : 354   (151 rated registry outlets published nothing in this window)
    NOT rated        : 3,729
```

| bucket | outlets | articles | unlocks |
|---|---|---|---|
| untracked | 3,653 | 11,434 | **13** |
| locality-only | 30 | 609 | 14 |
| low-credibility | 11 | 237 | 3 |
| forum | 3 | 341 | 2 |
| wire | 8 | 665 | 0 |
| research | 5 | 504 | 0 |
| aggregator | 5 | 133 | 0 |
| ambiguous | 14 | 70 | 0 |

Articles from outlets that cannot vote on lean: **13,993 of 27,877 ≈ 50%.**

## Both directions fail

**Expansion fails** because 91% of outlet identities (3,729/4,083) are already unrated, and
crawler-discovered outlets arrive unrated by construction. Adding them lowers the rated share of
each story — moving Information Health in the wrong direction.

**Curation fails on the number that matters:**

> **WORKLIST: 3,653 untracked outlets, 13 of which sit in a one-short story and are worth 13
> claims between them.**

Curating the *entire* untracked backlog buys **13 blindspot claims across 1,528 stories — 0.85%**.
All buckets together: ~32 claims. The largest untracked outlet, sportskeeda.com at 992 articles,
unlocks **zero**.

The reason is structural: a blindspot claim needs ≥ N rated publishers, and stories reaching that
threshold already have them. Rating the tail does not convert stories that were never close.

> ## ⚠ CORRECTION 2026-08-25 — the 0.85% was an instrument defect, and it was 17× low
>
> **Every number in this section is an undercount. `sportskeeda.com` does not unlock zero; it
> unlocks ten.** Two defects in `audit_registry_coverage.py`, both fixed and both regression-tested:
>
> **The join.** `analyse` keyed its per-story sets on the coverage row's `publisher`, but that field
> carries `engine._prettify(outlet)` while the identity map is built from the RAW row publisher. For
> a registry-resolved outlet the two agree — `NPR` prettifies to `NPR` — so the numbers looked
> plausible. For an **untracked** outlet stored as a bare host they do not: `somdnews.com` becomes
> `Somdnews.Com` and never matches its `d:somdnews.com` identity. So every untracked outlet with a
> host-form name scored **zero unlocks by construction**, and only prettify-stable names (`BelTA`,
> `NL Times`, `PerthNow`) were ever counted. The audit was blind to most of the backlog it exists to
> measure. It now joins on the article URL, and refuses to report if any coverage row fails to join.
>
> **The question's shape.** `unlocks` prices ONE row at a time — only a story exactly one rating
> short. A batch is not one row at a time: a two-short story with two untracked members is converted
> by rating **both**, and the per-outlet column credits it to neither.
>
> Re-measured on 27,825 articles / 1,535 stories:
>
> ```
> untracked bucket unlocks              13  ->  146
> CEILING: curate ALL 3,639 untracked   13  ->  220 stories (14.3%)
>   (distinct one-short stories 100; the other +120 need a coordinated batch)
> stories touched and STILL unconvertible: 393 at 1 short, 18 at 2 short
> ```
>
> **220 against a current ~210 claims — curating everything would roughly DOUBLE them.**
>
> The tests could not have caught this: every fixture passed the identical publisher string to both
> sides and gave the rows no URL at all, so none of them could detect a transform applied to one
> side. `test_unlocks_survive_the_prettify_asymmetry` now models it and fails on the old code.
>
> **What this does NOT yet overturn is the verdict.** This section gave two reasons to decline
> curation, and only the first is dead. The second — that the unrated half is *structurally
> unrateable* rather than un-rated — is untouched, and the corrected data supports it: the top-20
> cohort by curation value is dominated by gaming and tech verticals, and 4 of its 5 sample unlocks
> are stories like *"All Lobby Hack Codes For Free Rewards"* and *"Call of Duty: Modern Warfare 4
> beta requires GTX 970"*. A coverage-gap claim on a Roblox codes article is not a product win.
>
> The open question is therefore **how much of the 220 sits on rateable NEWS outlets** — the local-TV
> affiliates and national dailies in the same list (`kait8.com` 6, `abc7.com` 4, `nysun.com` 2,
> `kwch.com` 1, `nbc29.com` 2) — rather than on verticals with no left/right axis. That is one
> `--cohort` run, and until it is done neither the old verdict nor its reversal is supported.

## Retraction

**I recommended the curation backlog as where "nearly all the available value" would be. That was
wrong by roughly two orders of magnitude.** The unlocks metric — which already existed in this
repo, in this very instrument — says 0.85%. The recommendation was made before running the
instrument that refutes it.

## Verdict

**Neither expand nor curate.** Source coverage is not the constraint in either direction. The
crawler POC stays dormant — not for lack of quality, but because the constraint it addresses does
not exist.

The unrated half is mostly **structurally unrateable rather than un-rated**: sports and gaming
verticals (sportskeeda 992, goal.com, ign.com, eurogamer), press-release wire (MarketBeat 438,
GlobeNewswire 107, PR Newswire 92), research journals, forums, and non-English regionals. There is
no left/right axis for a gaming review or a preprint server, which is why `locality-only` and
`research` are recorded as decisions already taken rather than backlog.

**What this metric does not price:** unlocks measures *blindspot claims*. If source diversity has
value for its own sake — a reader seeing a Vietnamese outlet's angle on a story — that is a product
value this measurement is blind to, and the verdict above does not account for it.

---

# Part 3 — Research and forum inside the clustering corpus: measured, keep

## The question

`EXCLUDED_KINDS = ("wire", "aggregator")`, so `research` (504 articles) and `forum` (341) **do**
enter clustering. Should they?

## The bar you would reach for cannot answer it

`audit_clustering_change._coherence_stats` scores only stories with ≥ `MIN_LOCATED_FOR_TRUST` (4)
located members. Research papers and forum posts carry no event geography, so a weld involving them
is either unscored or scored purely from its news members. **This is the same blind spot documented
for entertainment in `STORY_TEMPLATE_GATE.md`** — "entertainment articles carry no event geography…
clusterTrust is honestly unknown, never LOW". Membership evidence had to carry the audit, and the
run confirmed the prediction exactly: the bar did not move at all.

## The measurement (read-only in-process probe, 2026-08-25)

```
window            : 27,889 articles
research+forum    : 846 from 9 outlets
stories touched   : 6 of 1,529
  PURE  (all members research/forum) : 0
  MIXED (welded to news)             : 6
```

**~840 of 846 never cluster at all.** They sit as singletons in Discover/Search and never form or
join a story — `min_publishers = 2` suppresses pure-vertical clusters, and PURE = 0 confirms no
non-news stories are being created.

### The six merges, read individually

| story (members) | the member | verdict |
|---|---|---|
| NASA Swift rescue called off (6) | Phys.org | **correct** — same event as Wired, Ars Technica, CBS |
| Near-total lunar eclipse (4) | Phys.org | **correct** — headline identical to NBC's |
| Record El Niño (3) | Phys.org | **correct** |
| OpenAI GPT-5.6 pricing cut (3) | DEV Community | **correct** — same event as Indian Express, Seeking Alpha |
| Jason Arday plagiarism (12) | 9GAG | **topically correct** — same event as Reuters, NYT, BBC, Guardian, Al Jazeera |
| "Too much RNA can starve cells" (2) | Phys.org | **FALSE** — welded to Vice's *"Watching Too Much TV Could Literally Shrink Your Brain, Study Finds"* |

The single false merge joins two entirely different studies on `{much, study, finds}`. That is the
**sole-template-evidence class** — "study finds" announcement boilerplate — not a research-source
defect. Two *news* outlets running "study finds" headlines would weld identically, so exclusion
cannot reach the class.

### The counterfactual

```
stories            : 1,529 -> 1,521      (-8 stories)
largest cluster    : 52 -> 52
covered articles   : 6,138 -> 6,109      (-29)
  removed rows themselves: 6
independent signal : 1/55 bad (mean 0.944) -> 1/55 bad (mean 0.944)     <- flat, as predicted
news articles that changed story  : 11
news articles that LOST their story: 24
exhibits           : 3 in window, all separated -> separated (unchanged)
```

Removing 846 articles — of which **six** were in stories — destroys **8 stories** and strips **24
news articles** of their coverage. The mechanism is the one recorded for `min_support` in
`STORY_LINK_SUPPORT.md`: dropping a member can push a story below `min_articles`/`min_publishers`,
and `_merge_duplicates`/`_repair` then receive different inputs and recompose. The side effects
dwarf the intended effect.

Pre-registered criterion, fixed before the run: *"Neither excluded if `news articles that LOST
their story` is materially > 0."* **24 against a benefit of 6.**

## Retraction

**I predicted forum would be excludable and research would not. The data is closer to the
opposite.** Both forum instances (9GAG, DEV Community) were correct merges; the one false merge
came from research (Phys.org). The prediction was stated before the run and is wrong on the
attribution.

## Verdict

**Remain in the clustering corpus, unchanged — not "handled separately".**

* exposure is 6 stories in 1,529 (0.4%);
* 5 of 6 merges are correct, and several are *valuable* — Phys.org putting the primary source
  inside a science story is coverage a bias-comparison product should want;
* removal costs 24 news articles their coverage and 8 stories, to fix 1 false merge;
* that false merge is a template-lexicon class exclusion would not solve.

The `EXCLUDED_KINDS` framing does not fit these kinds. `wire` and `aggregator` are excluded because
their content is **structurally redundant** — machine-generated copy, or someone else's article
republished. Research and forum are neither: original material about real events, and the data
shows them behaving that way.

## Two smaller findings, reported not proposed

**The "study finds" weld** is a real instance of the announcement-template class. The standing
constraint is that we cannot add a lexicon per discovered pattern, and the corpus-statistical route
is closed (`story_service.derived_boilerplate`), so this is recorded as one observation, not a
candidate. It is also the shape the dark `event_identity` judge would resolve.

**A presentation asymmetry, not a clustering one.** 9GAG sits in a 12-member story alongside
Reuters and the NYT. `totalCoverage` counts it; `_distribution` and `_rated_publishers` do not. The
story therefore shows one more publisher than actually votes on its bias. That is a display
question about what "12 publishers" means, and the machinery to distinguish the two counts already
exists.

---

# Part 4 — A URL fallback in outlet resolution: measured, rejected

## The candidate

`ingest.Scorer._resolve_outlet` resolves `self.registry.resolve(raw.outlet or raw.url)` — the URL is
consulted only when the outlet name is **absent**, never when it is present and *fails*. Every
adapter supplies a name, so in practice the URL is never tried: a name the registry does not know
falls straight through to unrated, even when its host is already in the registry's domain index.
Measured: **431 identities carrying 1,615 articles sit on a host a tracked outlet already owns.**

The candidate was one line, with name-first ordering preserved:

```python
out = self.registry.resolve(raw.outlet) or self.registry.resolve(raw.url)
```

## The measurement (`audit_outlet_resolution.py`, 2026-08-25)

```
window                    : 27,855 articles
articles gaining an outlet: 1,246 (4.5%)   RATED 164 · locality-only 1,082
     996  Google News   <- 10tv.com @ news.google.com
                           12news.com @ news.google.com
                           13abc.com @ news.google.com
stories                : 1,528 -> 1,410        (-118)
covered articles       : 6,132 -> 5,668        (-464)
articles that LOST their story: 473
rated story members    : 4,409 -> 4,363        <- went DOWN
BLINDSPOT CLAIMS       : 214 -> 215            <- +1
```

**+1 blindspot claim for 473 articles losing their story and 118 stories destroyed.**

The dominant effect is mass **mis-attribution**, not the `min_publishers` collapse the probe was
built to catch. 996 of 1,246 newly-attributed articles — 80% — are real local broadcasters proxied
through `news.google.com`, which resolves to a registry `kind=aggregator`. Since
`EXCLUDED_KINDS = ("wire", "aggregator")`, attributing them to Google News plausibly evicts them from
the clustering corpus outright; the magnitudes line up with that, though the exclusion-vs-quorum
split was not isolated. The decisive tell needs no mechanism at all: 164 articles gained a *rated*
outlet and rated story members still **fell**.

## Retraction

**I wrote that name-first ordering "is what makes it safe rather than clever".** Ordering is
genuinely preserved and genuinely does protect the AP-on-cnn.com case. It protects nothing when the
failing name is `10tv.com` and the host belongs to an aggregator.

The probe also named the wrong cost mechanism in its own docstring — it argued `min_publishers`
double-counting. It still worked, because the line it printed for a different reason —
*"READ THIS: a host shared by many publishers is where mis-attribution would happen"* — put
`news.google.com` on screen in one glance.

## Verdict

**Do not ship the URL fallback.** `_resolve_outlet` stays as written.

What survives is narrower and needs no code: strip the Google News block and ~250 articles remain
(`Express @ express.co.uk`, `Index @ index.hr`, `Telegraaf @ telegraaf.nl`) — real publishers already
in the registry under a longer canonical, failing only because the feed's short name-form is not in
their alias list. **Three CSV alias rows, strictly additive, zero clustering risk.** A kind-gated
fallback (fall back to the host only when it resolves to a non-aggregator, non-wire outlet) would
recover the same ~250 through a conditional inside resolution; the alias rows are cheaper and carry
no new code path.

---

# Part 5 — The first evaluated cohort: host instability adopt, syndication reject

The first use of the M1/M2 tier boundary, and the first time the outlets we already ingest were
measured. `examples/audit_source_cohort.py`, live catalog 2026-08-25.

## The setup, and why the cohort was already inside

`ingest_entries` has no admission gate, so **4,403 outlet identities are already in the catalog,
3,988 untracked** — every one of them in Tier A by grandfathering. The first cohort was never a list
of publishers to add. It was the ones we already carry, measured against the question the boundary
was built to ask.

Only two criteria survived to demote: **syndication** (share of headlines whose exact title-token
set also runs under another publisher) and **host instability** (share of articles on the outlet's
own main host). Both are language-independent. Five outlets, 137 articles.

## The split is the whole result

```
                          rows   stories        covered   LOST   claims
  SYNDICATION only          70   1,505->1,479   -67        26     208->207
  HOST INSTABILITY only     67   1,505->1,502   -13         3     208->207
  both                     137   1,505->1,476   -80        29     208->206
```

**Host instability costs 3 collateral articles. Syndication costs 26 — nearly all of the 29.**
Largest cluster is *unchanged* (60) under host instability alone and falls to 58 under syndication.
Coherence flat both ways (0.959 → 0.958). Exhibits unmoved.

## Syndication: REJECTED, and the mechanism is why

The benefit measured for all five together is **33 in-story articles carrying a title identical to
another member of the same story** — publisher counts a story should never have had. Most of that
is the two syndicators (Brisbane Times 42%, the brunswick news 50%); the split is inferred from
their syndication rates rather than measured per-criterion.

So syndication is roughly **30 inflated publisher counts fixed against 26 articles losing coverage
entirely** — break-even at best. But the arithmetic is not the reason to refuse it. **The mechanism
is wrong:**

> Removing a syndicated copy from a 2-publisher story does not correct the publisher count. It
> deletes the story — `min_publishers = 2` fails — and takes the *legitimate* article with it.

That is exactly what 26 lost articles and 26 destroyed stories are. Tier B is the wrong instrument
for a syndicator, and this repo already recorded the right one, in Part 3's second smaller finding:

> "9GAG sits in a 12-member story alongside Reuters and the NYT. `totalCoverage` counts it;
> `_distribution` and `_rated_publishers` do not… **the machinery to distinguish the two counts
> already exists**."

A syndicator should count as *coverage* and not as a *publisher*. That is a display-and-quorum
change, not a corpus-membership one, and it costs nobody their article.

## Host instability: ADOPT, marginally

`iHeartRadio` 6%, `jpnn` 44%, `ETtoday` 42% — at 6% stability, 94% of an outlet's articles sit on
domains that are not its own. These are not low-value outlets; they are **unattributable** ones, and
they vote in stories under a name that is not theirs.

That is the mirror image of the `news.google.com` finding in Part 4, where a change was refused
precisely because it would attribute local broadcasters to an aggregator. Here the mis-attribution
is already happening.

**Cost: 3 articles, 3 stories, and one blindspot claim of 208.** The 3 is inside every bar this repo
has used. **The 1 claim is the open question** — the entity veto was adopted at zero claims lost —
and it is 0.5%, most likely recomposition rather than a genuine claim disappearing. Marginal, and
recorded as marginal.

## The more valuable output was not the demotions

| verdict | outlets | articles | |
|---|---:|---:|---|
| LOW PARTICIPATION | 110 | 2,553 | reported, never acted on — see below |
| **CURATE** | **71** | **2,369** | no registry row; the curation worklist |
| **RATE** | 6 | 185 | has a row, needs a sourced lean |
| ALREADY EXCLUDED | 5 | 731 | wire/aggregator; no action |
| TIER B | 5 | 137 | the cohort above |

`CURATE` surfaced US local TV at 25–59% participation with ~0% syndication — `kait8.com`,
`kwch.com`, `abc7.com`, `6abc.com`, `abc7ny.com` — alongside `macrumors`, `engadget`, `9to5mac`.
Real newsrooms, no registry row. Since Tier A promotion requires a lean, **curation is the only path
to Tier A**, which makes this list the actual expansion work.

## Two retractions from this audit

**Participation does not demote, and two justifications for acting on it failed.** First "these are
low-value sources" — the list held The Hankyoreh, cooperativa.cl, BelTA and dailymemphian.com, plus
9GAG, DEV Community, `Nature` and `Space.com`, four of which Part 3 measured individually and KEPT.
Then "these lack same-language peers" — refuted by its own measurement: English at 214 peers
participates at 27%, Vietnamese at **six** peers at 30%.

**A broken membership lookup invalidated two production runs before either was readable.**
`index_by_member` keys on the article's DISPLAY url; the audit looked up `canonicalUrl`, which has
already been lower-cased and stripped of `www.`, the query and the trailing slash. 0 of 3 hits on a
fixture; on production it reported 292 in-story articles against a window that covered 6,121 —
every participation figure low by ~20×. The script now reconciles per-outlet in-story counts against
the build's own covered total and **refuses to report** on mismatch.

## Open question, deliberately not promoted to a finding

Every non-Latin-script language in the window sits at **exactly 0%** participation (ru, ko, ar, ja,
zh, ta) while every Latin-script one is nonzero. That looks like a property of `title_tokens`, but
two hypotheses have already died in this audit and it is recorded as a question.

---

# Re-running these audits

```bash
cd /opt/ih && source deploy/ops/_compose.sh

# Part 2 — registry census, buckets, and the unlocks worklist
dc run --rm -T api python examples/audit_registry_coverage.py

# Part 4 — the outlet-resolution counterfactual, both sides
dc run --rm -T api python examples/audit_outlet_resolution.py --db "$RWE_DB_URL"

# Part 5 — per-outlet cohort evaluation, per-criterion counterfactuals, curation worklist
dc run --rm -T api python examples/audit_source_cohort.py --db "$RWE_DB_URL"
```

Part 3 has no committed instrument — it was a one-off in-process probe, deliberately, because the
question it answers is not expected to recur. Its shape, should it be needed again: tag each window
row with `outlet_registry.resolve(publisher).kind`, build `story_service.build_stories` twice (all
rows vs rows minus the kinds), then diff `audit_clustering_change.index_by_member` between the two
builds and print the stories whose membership mixes the kinds. `_coherence_stats` and
`_exhibit_outcomes` from the same module supply the standard bars — with the caveat above that the
coherence bar is near-blind to this particular question.

## Instrument lesson

All three audits were answered by instruments that **already existed**
(`audit_registry_coverage.py`) or by a hundred lines composing functions that already existed —
`story_service.build_stories`, `audit_clustering_change.index_by_member`, `_coherence_stats`,
`_exhibit_outcomes`. None needed new production code, and all three overturned a written
recommendation. The cost of checking was an order of magnitude below the cost of building what was
recommended.

Two of the three instruments also carried a defect that would have inverted their verdict, and both
were caught by reading the tool rather than by reasoning about it: Part 3's coherence bar is
structurally near-blind to research/forum members, and Part 4's first draft keyed on a
plausible-looking `blindspot` field when the story dict's field is `blindspotSide` — which would
have returned 0 on both sides and reported a real effect as no effect.
