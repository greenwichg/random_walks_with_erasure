# Coverage Comparison (L0) — production value evaluation

**Question asked:** after deploying Phase 1, does the Coverage Comparison card provide meaningful
value to a reader across a representative sample of production stories — or does it mostly repeat
what the reader can already see?

**Answer: mostly the latter, and for a fixable reason.** About 72% of the cards that render say
nothing beyond *"N other outlets covering this story"*, and 46% of them also carry a line about our
own extraction coverage that reads as noise. The cause is not that the idea is weak: **three of
L0's finding classes are structurally unreachable in production**, because the module was written
against a richer story/member shape than `story_service` actually emits. The card is thin because
most of it never runs.

**Recommendation: postpone L1, L2 and L3. Fix L0 first.** L2/L3 are unbuildable on this catalog
regardless — their addressable set is **1 cluster out of 800**.

Deployed commit under evaluation: `0b9bb30` (L0 + the register-shape fix), measured on `92214d1`.
Nothing in this document was changed in code; it is an evaluation only.

---

## 1. Method

Two read-only passes over the live catalog, both against the same default story view the product
serves (`story_service.default_story_view(build_inline=True)`).

| Pass | Command | What it answers |
|---|---|---|
| Reach + readiness | `examples/audit_coverage_comparison.py --show 12` | Every clustered article: does a card render, which gate refused, which findings fired. Plus the L1–L3 readiness measurement the design (§2) requires before building the text tiers. |
| Value sample | stratified 66-story sampler (scratch script, not committed) | For each sampled story, the card a reader would actually see, printed in full for reading. |

**Scale:** 1,799 stories, 7,502 clustered articles. The sample covers **66 stories across 14
topics** — politics, business, sports, technology, entertainment, world, U.S., climate, health,
science, culture, arts, opinion, and uncategorised — exceeding the 30-story, 7-genre requirement.

### One honest caveat about the sample

The sampler picks each story's **representative** member, which `story_service.py:279` defines as
the *earliest-published* article. So `first_report` fires on **54 of 57** rendered sample cards
(95%). That is my probe's doing, not production's: catalog-wide `first_report` fires on **675 of
5,004** cards (13.5%). **The per-story sample is therefore the best case, not the typical case.**
Wherever the two disagree in this report, the catalog-wide number is the finding and the sample is
the illustration.

---

## 2. Reach — who sees a card at all

```
clustered articles         : 7,502
renders a comparison       : 5,004  (66.7%)
refusals:
   too_few_publishers      : 2,441  (32.5%)
   template_genre          :    37  (0.5%)
   cluster_untrusted       :    20  (0.3%)
   cross_language          :     0            <- never fires; see §5
```

Two-thirds coverage is a healthy reach for a gated feature, and the refusals are the right ones:
a 2-outlet cluster genuinely has nothing to compare, and the `template_genre` refusal correctly
suppressed the card on *"Mega Millions $800M jackpot winning numbers for Tuesday, July 28"* — a
15-outlet cluster where a coverage comparison would be meaningless. **The gating works.**

---

## 3. What the cards actually say

Catalog-wide, across the 5,004 rendered cards:

| Finding | Cards | Share of rendered | Reader value |
|---|---:|---:|---|
| `other_outlets` — "N other outlets covering this story" | 5,004 | **100%** | Low — the story page already shows this |
| `event_countries_unknown` — "this article has no extracted event location to compare" | 2,307 | **46.1%** | **Negative** — see §6 |
| `first_report` | 675 | 13.5% | **High when true** |
| `only_right` / `only_left` / `only_center` | 615 | 12.3% | High if true — but see §7 |
| `own_event_countries` | 178 | 3.6% | Moderate |
| `event_countries` | 81 | 1.6% | Moderate |
| `mostly_reporting` | 26 | 0.5% | Moderate |
| `reporting_among_opinion` | 2 | 0.04% | Moderate |
| `only_<language>` | **0** | **0%** | Dead — see §5 |
| `missingViewpoints` | **0** | **0%** | Dead — see §5 |

The genuine findings sum to 1,577 card-slots. Since a card can carry several, the number of cards
with **at least one fact beyond the outlet count** is between 675 (13.5%) and 1,577 (31.5%); the
classes are largely independent, so **≈28% (~1,400 cards)** is the realistic figure.

> **≈72% of rendered cards — and ≈81% of all clustered articles — carry nothing a reader could not
> already see.** For those, the card is a section header, a sentence restating the outlet count, and
> a caveat.

---

## 4. Per-story verdicts (all 66 sampled stories)

Grades: **U** useful (tells the reader something they could not see) · **T** thin (true, but
restates what the page implies) · **O** obvious (outlet count only) · **—** no card.
Flags: **N** carries the unknown-location noise line · **X** an "only \<lean\>" claim over a cluster
that also holds unrated outlets · **F** the story is a fragment of a larger story split across
clusters.

| # | Topic | Story | Outlets | What the card added | Verdict |
|---:|---|---|---:|---|:--:|
| 1 | — | Rushdie stabbing trial — defendant declines to testify | 13 | first report | T·N |
| 2 | — | Aung San Suu Kyi meets Red Cross official | 10 | first report; "only right-of-centre" | T·N·X |
| 3 | — | Aqilah claims silver, Commonwealth Games | 10 | first report; "only right-of-centre" | T·N·X |
| 4 | — | FBI investigates Michigan/Minnesota cyberattacks | 9 | first report | T |
| 5 | — | Oil prices jump as US–Iran tensions escalate | 8 | first report | T·N |
| 6 | Politics | Todd Blanche nomination on the brink | 21 | first report | T |
| 7 | Politics | Hamas agrees to disarmament under Gaza deal | 20 | first report | T |
| 8 | Politics | Trump: US, Israel hold off on Iran strikes | 20 | first report | T·N |
| 9 | Politics | Senate to confirm Jay Clayton | 20 | first report | T |
| 10 | Politics | Fauci subpoenaed for Senate COVID hearing | 19 | first report | T·N·F |
| 11 | Sports | UEFA: FIFA private-investment plan "crosses line" | 22 | first report | T·N·F |
| 12 | Sports | Infantino warns England over World Cup sale | 19 | first report | T·F |
| 13 | Sports | FA admits concern over FIFA proposal | 18 | first report | T·N·F |
| 14 | Sports | Tony Romo may step back after arrest | 11 | first report | T |
| 15 | Sports | Skubal to face O's as deadline nears | 10 | first report | T·N |
| 16 | Business | Mega Millions $800M winning numbers | 15 | *(refused — template_genre)* | — |
| 17 | Business | NY sues prediction market Kalshi | 14 | first report | T·N |
| 18 | Business | 30-yr Treasury yield highest since 2007 | 12 | first report; **location list `IR`** | T·N |
| 19 | Business | Fed holds rates as dissent mounts | 11 | first report; "only right-of-centre" | T·X |
| 20 | Business | Anthropic models "broke free" in testing | 11 | first report | T·N |
| 21 | U.S. | Three dead in Twin Falls, Idaho shooting | 26 | **tied first** (correctly not claimed) | T·N |
| 22 | U.S. | Seattle police chief snaps over Chicago questions | 17 | first report | T·N |
| 23 | U.S. | Apalachee shooter sentenced to life | 16 | first report | T·N |
| 24 | U.S. | Ransom notes in Nancy Guthrie disappearance | 13 | first report | T·N |
| 25 | U.S. | Minnesota prediction-market ban halted | 13 | first report | T·N |
| 26 | World | **Ten missing incl. Nirmal Purja after avalanche** | 36 | **first report** — Explorersweb ahead of 35 outlets | **U**·N |
| 27 | World | 13 dead in Peru tourist plane crash | 21 | first report | T·N |
| 28 | World | Russia charges Durov with aiding terrorism | 20 | first report | T·N |
| 29 | World | Japan earthquake death toll rises | 18 | first report | T |
| 30 | World | Migrants enter Ceuta from Morocco | 17 | first report | T·N |
| 31 | Tech | Pixel Watch 5 shows up in Google Health | 9 | first report | T |
| 32 | Tech | Nvidia driver for Halo: Campaign Evolved *(ru)* | 8 | first report | T |
| 33 | Tech | Samsung Galaxy S26 FE charging speed *(it)* | 7 | first report | T |
| 34 | Tech | Zelda: OoT Switch 2 remake ESRB rating | 7 | first report | T |
| 35 | Tech | Nvidia GPU prices up 30% | 6 | first report | T |
| 36 | Ent. | Spider-Man: Brand New Day review | 39 | first report | T·N |
| 37 | Ent. | Vincent Pastore dies at 80 | 32 | first report | T·N |
| 38 | Ent. | Glen Hansard killed in crash | 19 | first report | T·N |
| 39 | Ent. | Four women accuse Jared Leto | 13 | first report | T |
| 40 | Ent. | Netflix sued for $105M by producer | 12 | first report; **event location `CH` only here** | **U** |
| 41 | Climate | Firefighting helicopters collide near Athens | 18 | first report; "only right-of-centre" (TASS); **location `FR`** | T·N·X |
| 42 | Climate | Spain and France battle record wildfires | 15 | first report | T |
| 43 | Climate | Wildfire off Westside Road near Vernon | 9 | *(nothing but the outlet count)* | **O** |
| 44 | Climate | Europe wildfire crisis shifts east to Greece | 7 | first report | T·N·F |
| 45 | Climate | B.C. horse-dung Trump portrait | 7 | *(nothing but the outlet count)* | **O** |
| 46 | Health | Ebola cases in Congo top 3,200 | 9 | first report | T·N·F |
| 47 | Health | Ebola death toll tops 1,500 | 8 | first report; **"only center-of-centre outlet"** | T·N·X·F |
| 48 | Health | Sixth death in NYC Legionnaires' outbreak | 4 | first report | T |
| 49 | Health | Toddler found alive in Arizona morgue | 4 | first report; "only left-of-centre" | T·N·X |
| 50 | Health | Fauci pleads the Fifth with Rand Paul | 3 | first report | T·F |
| 51 | Opinion | Mukunda: AI can create start-ups, just not good ones | 3 | first report; **reporting piece among opinion** | **U** |
| 52 | Opinion | Biden ghostwriter interviews | 3 | first report | T·N |
| 53 | Opinion | "A Mom Is on Trial for Killing Her Three Children" | 2 | *(refused — too_few_publishers)* | — |
| 54 | Opinion | Iran déjà vu: Trump threatens escalation | 2 | *(refused — too_few_publishers)* | — |
| 55 | Opinion | Rakhimova beats Venus Williams | 2 | *(refused — too_few_publishers)* | — |
| 56 | Science | Private mission to rescue NASA telescope | 6 | first report | T |
| 57 | Science | NASA fuels Roman Space Telescope | 5 | first report | T |
| 58 | Science | SpaceX rocket piece to hit the moon | 5 | first report; **locations `CA,CN,JP,US`** | T·N·F |
| 59 | Science | Mars rover finds polygon features | 5 | first report; **location `US`** for a Mars story | T·N |
| 60 | Science | SpaceX ship to smash into the moon | 3 | first report; "only right-of-centre" | T·X·F |
| 61 | Culture | Ariana Grande quits West End role | 9 | first report; **event location `US` only here** | **U** |
| 62 | Culture | Ferris Bueller star / Jimmy Kimmel | 2 | *(refused)* | — |
| 63 | Culture | Musafir Cafe filming locations | 2 | *(refused)* | — |
| 64 | Culture | Dutch dikes at risk from hot summers | 2 | *(refused)* | — |
| 65 | Culture | 'Little Rascals' star on child stardom | 2 | *(refused)* | — |
| 66 | Arts | Gunther von Hagens dies at 81 | 2 | *(refused)* | — |

**Sample totals:** 4 **U** · 51 **T** · 2 **O** · 9 **—** · 32 carry the **N** noise line (56% of
rendered) · 7 **X** claims, 6 of them unsupported (86%) · at least 11 stories are **F** fragments.

Read against the catalog-wide rates in §3: **only 4 of 66 cards told a reader something they could
not otherwise see**, and that is with `first_report` firing 7× more often than it does in production.

---

## 5. Root cause — three finding classes never run

The single most important finding in this evaluation. `coverage_comparison.py` reads fields that
`story_service` does not emit, so the code paths are unreachable in production:

| Feature | Reads | Producer | Status |
|---|---|---|---|
| Viewpoint gap (`missingViewpoints`) | `story["missingViewpoints"]` | `_build_story` (`story_service.py:304-360`) never sets it — `article_analyzer._story_block:135` computes it into its **own** block | **Always `[]`** |
| Language uniqueness (`only_<lang>`) | `member["language"]` | `_coverage()` (`story_service.py:252-261`) emits publisher, headline, lean, leanBucket, register, emotion, url, publishedAt — **no language** | **Never fires** |
| Cross-language refusal gate | `member["language"]` | same | **Never fires** |

Confirmed three independent ways:

1. **Production, 66 live stories:** `stories carrying a 'missingViewpoints' key: 0/66`.
2. **Catalog-wide, 5,004 cards:** zero `only_<lang>` findings, zero `cross_language` refusals.
3. **The repo's own goldens:** every fixture shows the divergence side by side —

   | fixture | `story.missingViewpoints` | `coverageComparison.missingViewpoints` |
   |---|---|---|
   | `catalog_hit.json` | `["right"]` | `[]` |
   | `authed_bridge.json` | `["right"]` | `[]` |
   | `authed_familiar.json` | `["right"]` | `[]` |
   | `authed_following.json` | `["right"]` | `[]` |

The UI honours the empty value correctly (`analysis-result.tsx:492`), so the "viewpoints absent"
row simply never renders. **This is the highest-value finding L0 has** — a reader being told which
side of the story is missing from the coverage entirely — and it has never once reached a reader.

The cross-language gate matters too: the sample contains a Russian-language cluster (3Dnews, 8
outlets) and an Italian one (Hdblog, 7 outlets). The gate was designed to refuse comparison in
mixed-language clusters and is not protecting them.

This is the **same defect class** as the register crash fixed in `0b9bb30`: the module encodes an
assumed shape rather than the shape the producer emits, and the unit tests encode the same
assumption, so they pass while production does nothing.

---

## 6. The noise line — 46% of cards

When the story has consensus event countries but the article has no extracted location, L0 emits:

```
this article has no extracted event location to compare  (IR)
```

It fires on **2,307 of 5,004 cards (46.1%)** — 28× more often than the geography finding it is the
fallback for (81 cards). Three problems, in ascending severity:

1. **It is about us, not the article.** It reports a gap in our own extraction coverage inside a
   card that is supposed to describe the article. A reader has no use for it.
2. **It renders a country list next to that sentence** (`analysis-result.tsx:521`), so the
   honest label is followed by data that reads as a claim about the story.
3. **That country list is frequently wrong-looking**, because it surfaces the story's consensus
   event countries, which are themselves noisy:

   | Story | Countries shown | Problem |
   |---|---|---|
   | 30-year Treasury yield after Fed decision (CNBC) | `IR` | A Fed/Treasury story labelled Iran |
   | Firefighting helicopters collide **west of Athens** (TASS) | `FR` | Athens is in Greece |
   | Mars rover finds polygon features | `US` | The event is on Mars |
   | Ebola cases **in Congo** top 3,200 (AP) | `UG` | Uganda, not Congo |
   | SpaceX rocket to hit **the moon** | `CA, CN, JP, US` | Four countries for a lunar impact |
   | Biden memoir ghostwriter interviews | `AF, IQ` | Topics mentioned, not the event's location |

The label is technically honest; the presentation is not. This alone puts the card at **4.6× the
pre-registered 10% noise ceiling**.

---

## 7. Precision defects

**"The only \<lean\> outlet" is unguarded against unrated members.** The claim is computed over
`leanBucket`, which is null for outlets the registry does not rate. In the sample, **6 of 7**
such claims (86%) sit in clusters that also contain unrated outlets — so the card asserts
uniqueness over a set where the comparison was never possible. Catalog-wide that is ~615 claims,
of which the same proportion would be unsupported.

Example: *The West Australian* is called **"the only right-of-centre outlet in this coverage"** in a
10-outlet Aung San Suu Kyi cluster. If most of the other nine are unrated, the true statement is
"the only *rated* right-of-centre outlet" — a much weaker claim, and the difference is exactly the
kind of overreach the design's §7 and the project's standing "never guess a lean" rule exist to
prevent.

**A copy defect reaches readers verbatim.** `coverage_comparison.py:304` builds
`f"the only {my_bucket}-of-centre outlet"`, and `my_bucket` can be `center`, producing
**"the only center-of-centre outlet in this coverage"** — seen in production on the Ebola death-toll
story. `FindingRow` renders `{f.label}` directly (`analysis-result.tsx:520`).

**The findings are not internationalised.** Because labels are English strings built server-side and
rendered raw, every non-English reader sees English finding text, in a product that otherwise
maintains 854 keys across 5 locales.

---

## 8. The one universal finding is also unreliable

`other_outlets` appears on 100% of cards, so the outlet count *is* the feature for most readers.
Story fragmentation makes that count wrong — in the undercounting direction:

| Real story | Split across | Card says |
|---|---|---|
| FIFA World Cup private-investment row | 3 clusters (22, 19, 18 outlets) | "20 / 18 / 16 other outlets" — the true field is ~50 |
| Congo Ebola outbreak | 2 clusters (9, 8) | "8" and "7" |
| SpaceX stage lunar impact | 2 clusters (5, 3) | "4" and "2" |
| Fauci Senate testimony | 2 clusters (19, 3) | "18" and "2" |

At least 11 of the 66 sampled stories are fragments. A reader told "18 other outlets covering this
story" about the FIFA row is being given a number that is wrong by roughly a factor of three.
**Improving the comparison tiers does not touch this; improving clustering does.**

---

## 9. L1–L3 readiness — the design's own precondition

`docs/COVERAGE_COMPARISON_DESIGN.md` §2 requires this measurement before building the text tiers.
It is now made, on all 7,502 members (the earlier run resolved only 8% of them and its numbers were
discarded):

```
members resolved                : 7,502 (100%)
with a body                     : 1,778 (23.7%)
body length                     : p10 101   median 222   p90 266     <- characters
description length              : median 154 characters
clusters past L0 gates          : 800
  …with >=3 bodied (400+) members:     1 (0.1%)   <- the L2/L3 addressable set
  …multilingual                 :    17 (2.1%)
```

Two conclusions, both hard:

- **L2 (figure discrepancies) and L3 (quoted voices) are unbuildable.** Their addressable set is
  **one cluster in the entire catalog**. Comparing numbers or quotations across articles requires
  three or more members with real article text; the catalog has that for 0.1% of eligible clusters.
- **L1 (salient-term deltas from title + description) has ~154–222 characters per member** — a
  headline and one sentence. Term deltas over documents that short measure headline phrasing, not
  substance. Building "this article does not mention X" on that input would manufacture precisely
  the false omission claims the design forbids and the brief explicitly rules out.

The "with a body" figure is itself misleading: a median body of **222 characters** is an RSS
summary, not an article. The catalog does not currently contain article text.

---

## 10. Recommendation

Measured against the bars registered before the data was collected:

| Pre-registered bar | Result | Met? |
|---|---|---|
| Proceed unchanged if **≥50%** of cards are useful | ≈28% | ✗ |
| …and **≤10%** noise | 46.1% | ✗ (4.6×) |
| …and the complaint is "not enough detail" | The complaint is precision | ✗ |
| Redesign if the failure mode is **precision** | 86% of "only \<lean\>" claims unsupported; wrong-looking geography on 46% of cards; outlet counts wrong under fragmentation | ✓ **fires** |
| Postpone if L0 saturates value or the text tiers' addressable set is **too small** | L2/L3 addressable set = 1 cluster; L1 input = a 154-char description | ✓ **fires** |

### Postpone L1, L2 and L3. Fix L0 first.

Both postponement triggers fire, and they agree. Building another tier on top of a card that is
currently 46% noise, whose one universal claim is unreliable, and half of whose finding classes
never execute, would add surface area to a precision problem rather than fix it.

**Ordered next actions — all small, all inside L0, none implemented here:**

1. **Wire `missingViewpoints` through.** The highest-value finding in the design, dead since launch.
   Either have `_build_story` carry it, or pass the analyzer's computed block. Cheapest fix, largest
   value gain.
2. **Drop `event_countries_unknown` from reader-facing output.** It is diagnostics, not reader
   content, and removing it takes the card from 46% noise to under 2%.
3. **Guard the `only_<lean>` claim** on the share of members actually rated — or restate it as "the
   only *rated* …". This is the precision failure the redesign trigger identifies.
4. **Fix the "center-of-centre" label**, and move finding labels behind the i18n catalogs so the
   card is not English-only.
5. **Decide the language fields deliberately** — either carry `language` on coverage members (which
   revives the cross-language gate *and* the language-uniqueness finding) or delete both paths. Dead
   code that looks live is what produced this report.
6. **Add a shape-contract test** binding `coverage_comparison`'s expected member/story keys to what
   `story_service._coverage()` and `_build_story()` actually emit. All four defects found so far —
   the register crash, and these three dead paths — are the same failure: tests that encode the
   assumed shape instead of the produced one.

**Then re-measure.** With (1) and (2) done, the useful share should be re-counted before any L1
decision is revisited. If it clears 50% with noise under 10%, the "not enough detail" complaint
becomes real and L1 can be reconsidered — **but only after ingestion carries article text**, which
is the actual prerequisite for every text tier.

### What is genuinely working, and should not be touched

- **The gates.** 66.7% reach with correct refusals; `template_genre` correctly suppressed the
  lottery-numbers cluster.
- **The tie guard.** Three sampled stories showed `tied first` rather than a false scoop — the
  regression this fixed would have credited every member of a batch-stamped cluster as first.
- **`first_report` when it fires.** *"Breaking: Ten Missing, Including Nirmal Purja"* — Explorersweb,
  a specialist outlet, ahead of 35 others. This is the card at its best, and it is genuinely good.
- **Evidence links.** Every finding carries openable publisher evidence; no claim is unfalsifiable.
- **The refusal contract.** `available: false` + a machine-readable reason, rendering as nothing.

---

## 11. Scope

No production code, prompts, storage, API contracts, worker or UI were modified for this
evaluation. The two probes are read-only; the sampler was run from a scratch script and is not
committed. The defects in §5–§7 are reported, not fixed, per the brief.
