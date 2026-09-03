# Story hero images — why cards showed publisher branding, and the measured fix

**Status: adopted 2026-08-16.** `RWE_STORY_HERO_GUARD=1` is a compose default
(`deploy/docker-compose.yml`); the rule lives in `examples/media.py`
(`hero_rank` / `pick_story_hero(ranked=True)`), the per-build reuse index in
`examples/story_service.py::build_stories`, and the instrument that measured it in
`examples/audit_story_hero.py`. Presentation only: the guard cannot change story membership,
ids, ranking, or any trust signal.

## The symptom

Story cards fronting publisher furniture instead of news: an India Today "PTI: International"
flags card on a Trump Media story, The Spokesman-Review's masthead illustration on a Kennedy
Center story, a generic "NEWS FLASH" graphic on a Ukrainian-strike story. Three screenshots,
three different publishers — a pipeline property, not a publisher property.

## Root cause (three defects, one outcome)

1. **The representative override.** `media.pick_story_hero` returned the REPRESENTATIVE
   member's image unconditionally, and the representative is the *earliest-published* article
   (`story_service._build_story`). The fastest filer is systematically the outlet republishing
   wire copy within minutes under a generic house graphic — so a 29-source story showed
   whichever image the fastest filer attached, not the best of 29. Measured on production:
   **70.0% of all heroes (932 of 1,331) came from the representative.**
2. **Nothing rejected branding.** No tier anywhere judged an image; a masthead was as good as a
   photograph.
3. **The fallback ranking was inert.** When the representative had no image, ranking fell to
   declared area — which is 0 for every image except `media:content`/`media:thumbnail`, the only
   producers that supply dimensions. And the og:image inflow (`gdelt_gkg` backfill-when-empty)
   is precisely the tier where site-wide fallback graphics arrive.

## The pipeline (for orientation)

`media.pick_best_image` (per-feed-item, at ingest) → `FeedArticle.image` → multi-source dedup
merge by `store.SOURCE_PRIORITY` (`rss:100, newsapi:80, gdelt:60, extension:40`) →
`gdelt_gkg` og:image backfill-when-empty → **`media.pick_story_hero` (the defect)** → web
`ArticleImage` (self-hides on load error) / `story-card.tsx` (an imageless story renders the
coverage-distribution figure in the image slot — a **designed** fallback state).

## The measurement (production catalog, 2026-08-16, `audit_story_hero.py`)

28,381 window articles (72.4% with an image), 1,527 stories (87.2% with a hero), 19,007
distinct image URLs.

**Cross-story reuse is the publisher-agnostic branding signal** (the image analogue of
`ENTITY_MERGE_MAX_STORY_DF`): an image fronting many stories is by definition about none of
them. The measured story-df table:

| story-df | asset | verdict |
|---:|---|---|
| 20 | `media.spokesman.com/graphics/2020/08/sr_placeholder.png` (44 arts, **14 publishers**) | placeholder |
| 12 | `cdn.thestar.com.my/…/newTsol_logo_socmedia.png` | social logo |
| 10 | `winnipegfreepress.com/…/fb-og-image.png` | og fallback |
| 5 | `tribunadepetropolis.com.br/…/tpet-og.jpg` | og fallback |
| 4 | `dgabc.com.br/…/logo_dgabc_facebook.jpg` | social logo |
| 4 | `ichef.bbci.co.uk/ace/standard/240/…/live/…` | live-page graphic |
| **3** | `thehill.com/…/AP26225614336401.jpg` | **real AP file art** — a legitimate story family |
| 2 | Taipei Times / Korea Times square logos; Hill, WABC, CBS, Al Jazeera real photos | mixed |

Everything at df ≥ 4 is publisher furniture; the FIRST real photograph appears at exactly
df = 3. That sets the threshold — measured, not guessed.

Impact sweep (reject at ≥ T distinct stories, candidate = the shipped rule):

| T | urls rejected | heroes rejected | hero changes | → no hero |
|---:|---:|---:|---:|---:|
| 2 | 30 | 37 | 291 | 17 |
| 3 | 8 | 28 | 295 | 11 |
| **4** | **6** | **25** | **292** | **11** |
| 6 | 3 | 17 | 288 | 7 |

At the shipped cut: ~292 heroes improve, 25 branding heroes die, and only **11 of 1,331**
stories fall back to the coverage-figure card.

## The shipped rule

**L1 — rank, don't defer** (`media.hero_rank`, highest first):
not a known cross-story-reused asset → not suspect → photo-shaped declared dimensions
(area ≥ 90,000 **and** aspect ≥ 1.2) → area → the ingestion source's media priority
(`store.SOURCE_PRIORITY` — RSS media above adapter payloads above GDELT's og:image, the
ordering the dedup merge already trusts) → the representative → recency. The representative
survives as a **tiebreak**, which is all it was ever entitled to be.

**L2 — per-build reuse rejection** (`story_service.build_stories`): after membership is final,
index every member image identity (`media.image_identity` — scheme+host lower-cased, path kept
case-sensitive, query/fragment dropped so cache-busters can't mint identities) across the
build's clusters; an identity on **more than `HERO_MAX_CLUSTER_REUSE = 3`** distinct clusters
is rejected for every story.

**Suspect tier** (`media.hero_suspect`, metadata only, never downloads): URL-**path** tokens
receipted from the measured table — `logo`, `placeholder`, `og-image`/`og_image`/`ogimage`,
`socmedia`, `masthead`, `favicon`, `apple-touch` — or exactly-square declared dimensions (the
1200×1200 social-logo shape). The host is never token-matched. Suspicion demotes in ranking;
it costs a story its hero only when no clean candidate exists.

**All-rejected / all-suspect → `None`.** The imageless card renders the coverage-distribution
figure — a designed state. No hero is more honest than a masthead pretending to be news
(`docs/SIGNAL_INTEGRITY.md`, applied to images). This is how the screenshots' single-story
branding (which reuse can never see) dies: the India Today flags card and the Spokesman
masthead lose to any real photo in the cluster, and to *nothing* when the cluster has none.

**Flag semantics** (`story_service.hero_guard`): code default OFF — `pick_story_hero` with
`ranked=False` is byte-identical to the pre-guard behaviour, so an environment without the
deploy's variables changes nothing (the same library-vs-deploy divergence every clustering knob
documents). Compose defaults it ON. Junk values fall back to off, never to a guess.

## Deliberately not changed

- **`store.normalize_image_source` and the six keyed-JSON adapters.** 774 heroes report
  ingestion source `unknown` because the adapters' `source_type`s are not in the closed map.
  That map is the dedup **merge contract** (`upsert_feed_article` precedence), so widening it
  changes which image survives a multi-source merge — a separate change with its own
  measurement, not a rider on this one. Under the guard, `unknown` simply ranks at priority 0.
- **The web tier** was untouched by the guard itself (`ArticleImage` self-hiding and the
  figure-card fallback already handled `image: null`). The no-image state was then *redesigned*
  in a follow-up: the COVERAGE PLATE (`web/components/stories/coverage-plate.tsx`) replaced the
  bare coverage figure on cards, added a blindspot variant, and gave the story page a coverage
  masthead where the hero previously self-hid with no designed state. (It first sat in the
  image's slot above the headline; the desktop editorial audit — `docs/DESKTOP_EDITORIAL_AUDIT.md`
  — moved it below the headline block as a closing coverage strip, so the story leads and the
  statistic follows.) Same principle, better composition — every mark is still a counted fact
  of the story. The plate is also the
  **load-failure** fallback: a hero URL that 404s or blocks hotlinking is invisible to the engine
  (heroes are selected from metadata; nothing ever downloads an image), so the reader's browser
  is the only place it can be caught — `ArticleImage` hands the slot to the plate on error and
  emits a `story_hero_error` beacon (`host`, `surface`; allow-listed in
  `examples/product_analytics.py`), so the failure rate and the failing CDNs are measured before
  anyone tunes loading behaviour (e.g. referrer policy) on a guess.
- **Clustering.** Nothing here touches membership; the audit pins ids/order equality under the
  flag.

## Article surfaces (Discover / Search / Saved / Recommendations) — adopted 2026-08-16

Story heroes are guarded; article cards served each row's image **raw**, so the same furniture
could still front a Discover card. Closed with two changes, deliberately smaller than the hero
guard:

- **`imageSuspect`** (`discover.feed_article_to_article` → `ArticleModel`): the suspect tier
  (`media.hero_suspect` — receipted URL tokens + exact-square dims) serialized as a **field**,
  not enforced — the URL ships beside the verdict, and which surfaces demote it is presentation.
  The reuse tier is *not* ported: article-level image-df needs a window index at serve time and
  its thresholds were never measured — a separate, measured step if the suspect tier proves
  insufficient. `audit_story_hero.py` §2b measures the flagged share on production data.
- **Web**: article surfaces render text-first when the image is absent, engine-flagged, or fails
  to load (`onHidden`) — one state, three causes. Discover's LAYOUT went through a same-day arc —
  uniform grid → masonry → "front page, then river" → river rhythm (landmarks/beats) — and was
  then **reverted to the original uniform card grid by product decision (2026-08-16)**. What
  survived the revert, because it is layout-independent: this guard and its text-first fallback,
  the display-title hygiene, the publisher interleave (now ordering the card grid), the visible
  lean-pill tint (`Badge` lean variants), lean-said-once on Discover (`DiscoverCard leanDot`),
  and the shared read/save flows (`useReadArticleAction`, compact-capable `SaveButton`).
  `DiscoverCard` + masonry remain on Search, unchanged throughout.

## Residual risks (named, bounded)

- BBC's `live/` 240px graphics sit at df 3–4: the df-4 one is rejected, the df-3 one may still
  hero a story. Bounded: it is at least story-adjacent, and the sweep shows T=3 buys no
  meaningful precision over T=4.
- A real photograph legitimately syndicated across > 3 same-build stories would be rejected —
  not observed in the measured window (the widest real family, The Hill's, sat at 3).
- Exactly-square real photos (1:1 crops) are demoted when dimensions are declared; they lose
  the hero only when nothing else exists.

## Rollback

`RWE_STORY_HERO_GUARD=0` in `deploy/.env` (or removing the compose default) restores the
representative-first hero exactly; no data was migrated and no stored row changed.

## Verification runbook (post-deploy)

```bash
cd /opt/ih && source deploy/ops/_compose.sh
dc run --rm -T api python examples/audit_story_hero.py --top 25 --examples 10
```

Expected: `hero guard : ON`, and the marked `T=4` impact row reads **~0 hero changes / ~0 → no
hero** — production now implements the candidate, so the instrument measuring "what would the
rule change?" finds nothing left to change. Provenance shifts from 70% representative toward
evidence-ranked members; `sr_placeholder.png` and its peers vanish from the hero exhibits
(they remain in the reuse table — they are still on articles, just never fronting a card).
