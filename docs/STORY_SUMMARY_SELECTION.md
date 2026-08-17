# Story summaries — why cards served coverage digests, and the measured fix

**Status: adopted 2026-08-16 (commit `b7baac9`); verification closed 2026-08-17.** The rule
lives in `examples/story_service.py` (`pick_story_summary` / `_pick_summary` and the shared
detectors), the ingest-side half in `examples/sources.py` (`GoogleNewsAdapter` now stores
`description=""`), and the instrument in `examples/audit_story_summary.py`. Regression tests
are pinned to production exhibits (`tests/test_story_service.py` — the GN digest, the
Guardian standfirst kept-and-clamped, echo both ways, the masthead strip, the ranking order,
the counted-fallback string, id stability, determinism; `tests/test_sources.py` — GN
descriptions blank). Display-layer only: descriptions feed no clustering signal
(`desc_tokens()` = 0, measured-and-not-adopted), and `_story_id` anchors on the
representative's URL, so story ids, membership, and order cannot move with summaries
(pinned by test).

**Verification verdict, recorded up front: every integrity bar passed; the fallback bar
failed at 12.8% against the pre-registered ≤ 12% — and the miss was accepted, not tuned
around. The hand-read (below) showed the residue is structural (no real dek exists) or
protective (the dek would lie under the title), and is not to be recovered by weakening the
rule.**

## The symptom

Story cards serving a run-on chain of other outlets' headlines as the "summary":
*"Drone strike sparks massive blaze at Libya's Zawiya refinery AP NewsSee more headlines &
perspectives on Google…"* — headline, outlet name, headline, outlet name, ending in Google
News navigation furniture. Reported from a story-page screenshot; measured to be 26.2% of
all served summaries.

## Root cause

The served summary was `rep["description"]` **verbatim** (`story_service._build_story`), and
the representative is the *earliest-published* member. Google News RSS stores its
`<description>` as an `<ol>` of related-coverage rows (headline + outlet name), which
`text_utils.clean_html` flattens into the run-on chain — a coverage DIGEST, not a dek, **by
construction**. GN is systematically among the fastest filers, so the earliest-filer rule
served its digest for whole clusters. Secondary defects in the same field: real deks that
merely restate the headline (echo-rate 31.5%) and deks carrying bare domains (212).

## The baseline (production, 2026-08-16, `audit_story_summary.py`)

27,891 window articles, 1,522 stories. Registered criteria: **Google News served 399
summaries (26.2%)**; echo-rate 31.5%; 212 url leaks; fallback 5.5%; length in 60–320 chars:
65.6%, over 500: 3.2% (p90 446, digests in the tail); determinism PASS. The member-name
digest detector caught only 0.9% — GN's related outlets are mostly *not* cluster members —
which is why the shipped rule leads with provider + structure evidence and keeps
member-names as the backstop it measured to be (13 catches).

## The pre-registered bars

Approved before implementation, with the binding rule attached at approval: *do not loosen
the bars based on the post-implementation result; if a bar fails, report it rather than
tuning around it.* That rule decided how the one failure was handled.

GN-served = 0 · echo ≤ 3% · url-leaks = 0 · fallback ≤ 12% · length ≥ 85% in 60–320 and
~0% over 500 · determinism PASS · change-count reported with hand-read exhibits.

## The shipped rule

**Reject tiers**, in measured order of reliability (`_digest_shaped` + `_pick_summary`):

1. **Provider** — a `googlenews` description is a digest by construction (the 399-story
   class). `feed_article_to_article` passes `sourceType` through internally for this
   (not in `ArticleModel`; pydantic drops it on the wire; never used by clustering).
2. **Structure** — ≥ 2 newline-separated headline-shaped rows (15–160 chars, ending without
   sentence punctuation). The Guardian standfirst exhibit has exactly one such line; every
   digest exhibit has two or more.
3. **Member-name backstop** — ≥ 2 *other* cluster publishers named (word-boundary matched,
   names under 4 chars skipped: "Time" must not convict "sometimes").
4. **Bare-domain junk** (`_SUMMARY_URL_RE`).
5. **Headline echo, both ways** — ≥ 80% of the story title's tokens, or of the member's own
   headline's tokens, in the dek's first 160 chars.

**Survivors rank** sentence-shaped → not a fragment (≥ 60 chars) → the representative as a
TIEBREAK → earliest-published → canonical URL (a total order; deterministic). The winner is
masthead-suffix-stripped (`discover._display_title`) and clamped to 1–2 sentences / ≤ 320
chars on a word boundary. **Extractive only** — every summary is a sentence some member
actually wrote, or the unchanged counted fallback ("N publishers covering X."), the same
honesty rule the coverage plate follows for images. Ingest-side, `GoogleNewsAdapter` stores
`description=""` going forward; the selection rejects the stored backlog the same way.

## Verification outcome (production, post-deploy `b7baac9`; 27,835 articles / 1,519 stories)

| bar | baseline | measured | verdict |
|---|---:|---:|---|
| Google News–served = 0 | 399 (26.2%) | **0** — no `googlenews` provenance row at all | **PASS** |
| echo-rate ≤ 3% | 31.5% | 0.0% | **PASS** |
| url-leaks = 0 | 212 | 0 | **PASS** |
| length ≥ 85% in 60–320 / ~0% over 500 | 65.6% / 3.2% | 85.6% / 0.0% | **PASS** |
| determinism | PASS | PASS | **PASS** |
| fallback ≤ 12% | 5.5% | **12.8% (195 / 1,519)** | **FAIL — 13 stories over** |

Supporting reads: the digest/echo/url worst-exhibit sections came back **empty** — not just
under threshold but unpopulated — and the longest summaries are clean clamped sentences
≤ 320 chars. Digest-rate (registered criterion) 0.9% → 0.0%. Provenance: currents 33.0%,
rss 21.7%, fallback 12.8%, newsdata 12.3%, gnews 11.1% (**gnews.io — a different provider
than Google News**), newsapi 7.2%, guardian 1.7%, gdelt 0.1%. Length p10 fell to 33 chars
because the counted-fallback strings now populate the distribution — not short deks; the
band bar absorbs them. Change-count: not derivable per-story across shifted windows
(1,522 → 1,519 stories); the honest measure is the provenance shift (26.2% GN → 0;
fallback 5.5% → 12.8%) plus the hand-read below.

## The failed bar, diagnosed (read-only replay of the shipped rule)

Composition of the 179 diagnosable fallback stories (a slightly later window — an ingest
cycle ran between the audit and the replay; the audit's 195 additionally counts stories
whose coverage URLs resolve to no rows, an instrument accounting present identically in the
baseline — see "Deliberately not changed"):

| stories | what killed every candidate |
|---:|---|
| 104 | every dek is a Google News digest (provider tier) |
| 33 | no member wrote any dek |
| 23 | story-title echo (12 alone, 11 alongside GN) |
| 15 | **own-headline echo alone** — the marginal class |
| 4 | url junk / headline-rows structure (incl. combinations) |

Reading: 104 + 33 = **137 stories (77%) are structurally unreachable by any extractive
rule** — serving them means serving digests or inventing text — a floor of ≈ 9% against the
12% bar. The 23 title-echo stories are recoverable only by spending the echo bar's headroom.
The 4 junk rejections are correct. That leaves the 15-story own-headline class as the only
candidate for recovery, and it is smaller than it looks:

## The hand-read disposition — why the miss stands

All 15 own-headline stories were read with the member's headline and dek side by side:

- **~6 real deks wrongly rejected** (e.g. the NYC prediction-markets dek naming all four
  platforms and the Council Speaker; a data-center lede adding the project name and grid
  detail). Their stories serve the counted fallback — honest, just less informative.
  Bounded: ≈ 0.4% of stories.
- **2 the exact designed target** — the dek IS its own headline, character for character.
- **~5 protective** — the member is **mis-clustered** and its dek faithfully describes a
  *different event*: Toronto human remains under a Palomar Mountain (California) title;
  Ireland's 800 m gold under "Armbruster wins Australia's first gold"; an Indonesian
  gold-price *rise* dek under a *fall* headline; a Japanese Garmin dek under an English
  title; Netherlands eclipse videos under a UK-timing title. Serving these puts a factually
  wrong sentence under the story title.
- **2 junk/borderline** (affiliate promo copy; a source-truncated restatement).

The structural insight: because the tiers run in order, the own-headline check only ever
sees deks whose own headline *differs* from the displayed story title (identical ones die at
title-echo first). That population is a mixture of same-event rephrasings (legit) and
wrong-event members (mismatch), and **no token-level discriminator separates them** —
applying the tier only to the representative serves the Toronto dek; a dek↔story-title
overlap floor passes on exactly the shared vocabulary that mis-clustered the pair.
Distinguishing them *is* the clustering problem.

**Decision (2026-08-17): the 0.8-point miss is accepted and recorded. The remaining
fallback is structural or protective and is not to be recovered by weakening the rule.** A
counted fallback is honest; a mis-clustered dek under the story title is not
(`docs/SIGNAL_INTEGRITY.md`). The bar turned out to sit slightly below what an honest
extractive rule can reach on this catalog mix — a finding about the catalog, recorded here
instead of a threshold moved after the fact.

## Deliberately not changed

- **Clustering.** Out of scope by the approved plan. The diagnosis surfaced six concrete
  mis-clustering exhibits for the future clustering audit harness: Lexington/Portland
  shootings merged; Toronto/Palomar remains; the athletics-results fixture family; the daily
  gold-price fixture family; a cross-language Garmin merge; the eclipse-angle merge.
- **No LLM generation.** Extractive only, per the plan.
- **The GN backlog.** Stored digests remain in their rows; the selection rejects them at
  serve time. Only future ingestion is blanked. Migration note: as blanked rows replace the
  backlog, the 104-story all-GN class becomes the all-empty class — the fallback *rate* does
  not move. Real recovery for those stories would be dek enrichment from another source — a
  separate change with its own measurement.
- **The instrument's join accounting.** `audit_story_summary.py` counts a story as fallback
  when its coverage URLs resolve to no rows (members without an absolute URL — the same
  display-URL join every instrument here inherits). Identical in baseline and verification,
  so bar comparisons hold; it inflates the absolute level slightly. A future audit revision
  should split it into its own class before absolute rates are compared across instruments.

## Residual risks (named, bounded)

- The ~6 wrongly-rejected real deks (≈ 0.4% of stories) serve the counted fallback.
- A non-GN provider could someday ship digest-shaped text: the structure tier and the
  member-name backstop stay armed regardless of provider.
- Length p10 = 33 is the fallback strings, visible in any length monitoring — expected, not
  a regression.

## Rollback

`git revert b7baac9`: serving returns to representative-verbatim for stored rows. GN rows
ingested after 2026-08-16 carry `description=""`, so under a rollback they serve the counted
fallback rather than a digest. No data was migrated and no stored row was changed.

## Verification runbook (post-deploy)

```bash
cd /opt/ih && source deploy/ops/_compose.sh
dc run --rm -T api python examples/audit_story_summary.py
```

Expected: no `googlenews` row in provenance; digest-rate and echo-rate ≈ 0; url-leaks 0;
fallback ≈ 12–13% (drifts with catalog mix — the composition table above is the reference
for what it contains); determinism PASS.
