# W3B — Value Assessment (Docs Only)

**Status:** Architecture review / value assessment. No code, no implementation. Decides whether the
W3B backend build in `docs/W3B_STORY_VIEWPOINT_DESIGN.md` is worth the engineering effort **after**
discovering how much already exists (and after W3A shipped).

**Bottom line:** the substantive story-viewpoint capability — aggregation **and** its user-facing
rendering — already ships. W3B's proposed backend `viewpoint` object mostly restates data the UI
already shows and cannot change ranking (forbidden), so its marginal value is small. Recommendation
at the end: **C — reduce W3B to a tiny additive UI enhancement**, and sequence **W4 ahead of it**.

---

## 1. What functionality already exists today?

- **Clustering + outlet aggregation:** `story_service.build_stories` → `clustering.py`;
  `_distribution` aggregates **outlet** lean into L/C/R over distinct publishers
  (`story_service.py:45-54`); `_blindspot` flags the under-covered side (`:57-64`).
- **Contracts:** `StoryModel.distribution` + `blindspotSide` (`api_fastapi.py`);
  `story_intelligence.compute_coverage_statistics → politicalDistribution` (`story_intelligence.py:181`).
- **Parity:** served (`list_stories`/`get_story`) and explain (`evidence_resolver.story_index` →
  `cluster_from_store`) share the one builder `story_service._build_story`.
- **User-facing rendering (the decisive part):** the web already shows it on **every story card** —
  `SpectrumBar distribution={story.distribution}` (`web/components/stories/story-card.tsx:50`,
  `spectrum-bar.tsx` = a full L/C/R segmented bar + legend) **and** the blindspot as text —
  `t("storyCard.thinOn", { side: story.blindspotSide })` ("Thin on the right",
  `story-card.tsx:54-60`).
- **Adjacent phrasing already exists:** `meanLean` is already a field for a *reader's* profile
  (`rec_explain.py:294`), and "political reading leans left" phrasing exists in
  `evidence_resolver.py:235`.

So the whole loop — cluster → aggregate outlet lean → distribution + blindspot → contract →
**rendered to users** — is already in production.

## 2. What functionality would W3B actually add?

Per `docs/W3B_STORY_VIEWPOINT_DESIGN.md`, the delta is a `viewpoint = {dominant, meanLean, spread}`
object plus wiring it through `evidence_resolver.story_index`, `rec_explain` (storyMatch), and
`StoryModel`:
- **`dominant`** — the majority side; this is exactly `argmax(distribution)`, which the spectrum bar
  already shows visually and the blindspot text already names.
- **`meanLean`** — a continuous scalar mean of distinct-publisher outlet leans. Genuinely *new as a
  number*, but **nothing consumes it**: it isn't rendered, and it can't feed ranking (forbidden).
- **`spread`** — breadth of outlet leans; also unrendered/unconsumed.
- **explain exposure** — surfaces the same value in the **internal** `storyMatch` diagnostic
  (a dev endpoint, not a user surface).

Net: one derivable label + two unconsumed scalars + a dev-endpoint field.

## 3. Is that additional value visible to users? **Essentially no.**

The user-facing distribution + blindspot already render (§1). The three new fields are backend-only
unless the web adds new rendering, and even then `dominant` duplicates the spectrum bar and the
"thin on X" line; `meanLean`/`spread` have no UI. Net new user-visible value ≈ 0 as scoped.

## 4. Does W3B improve recommendation quality, or only presentation? **Presentation only.**

The W3B constraints forbid ranking changes. The roadmap's *high-value* W3B use — "relative-coverage
bridges" that **recommend the under-covered side** (`W3_ROADMAP_REVISION.md:200`) — is a ranking
change and is explicitly out of scope here. What remains is presentation of a summary of data
already presented.

## 5. Would users make better reading decisions because of W3B? **No meaningful improvement.**

The decision-relevant information — which sides cover this event, and what's missing — is already on
the card (spectrum bar + "thin on the right"). A `dominant: "left"` label restates it in words; it
does not surface anything the reader cannot already see.

## 6. Is there a remaining item (W4/W5) with larger user impact for the same effort? **Yes — W4.**

- **W4 (outlet coverage / registry expansion).** Unknown-outlet articles get a `NaN` lean and are
  **dropped as recommendation candidates** — at `catalog_from_qbias` and `rwe.mind.recommender_inputs`
  (documented in `outlet_coverage.py:1-12`). W4 uses the **existing** read-only diagnostic to rank
  unknown outlets by article volume and add the top ones to `outlet_registry.csv`, which **recovers
  recommendable articles** — a direct catalog-coverage and recommendation-quality gain, low risk
  (registry data, not serving logic), and it reuses tooling that already exists. This is larger user
  impact than a viewpoint label, for comparable-or-less effort.
- **W5 (blend-plan surface).** More serving work and it touches the ranking mix
  (`PRE_PRODUCTION_RECOMMENDATION_ROADMAP.md:137`) — higher risk, and out of step with "don't change
  ranking." Not the cheap win W4 is.
- **W3A already banked the related win.** W3A sharpened the political mask feeding the *same*
  cross-cutting / Open-Mindedness / viewpoint metrics W3B would inform, so W3B's incremental accuracy
  contribution is even smaller now.

## 7. Starting today, would you still build W3B? **Not the backend build.**

Most of its value already exists and already renders. I would **not** build the `viewpoint` object +
explain/contract wiring. The only residual worth anything is a **plain-language coverage-lean
one-liner** ("Mostly left-leaning coverage — thin on the right"), which aids scannability /
accessibility / coach phrasing and can be derived **web-only from the existing `distribution`** at
near-zero cost and zero backend/contract/parity risk. And I would put **W4 ahead of even that.**

---

## Evidence / Engineering judgement / Speculation

### Evidence (verifiable in-repo)
- Story clustering, `_distribution` (outlet L/C/R over distinct publishers), `_blindspot`,
  `StoryModel.distribution`, `story_intelligence.politicalDistribution`, and the shared builder all
  exist.
- The web renders the distribution (`story-card.tsx:50` `SpectrumBar`) **and** the blindspot
  (`story-card.tsx:54-60` "thin on X") on every story card.
- W3B's proposed delta = `{dominant, meanLean, spread}` + explain/contract wiring; `dominant` =
  `argmax(distribution)`.
- W3B constraints forbid ranking changes ⇒ presentation-only.
- Unknown-outlet articles are dropped as recommendation candidates (`outlet_coverage.py:1-12`); W4
  recovers them via registry expansion.
- W3A already sharpened the political mask feeding the same metrics.

### Engineering judgement (defensible inference)
- The backend `viewpoint` object duplicates already-aggregated, already-rendered data; marginal
  user-visible value ≈ 0.
- `meanLean` / `spread` are unconsumed with ranking off-limits; `dominant` restates the UI.
- W4 delivers a larger, lower-risk user impact (more recommendable articles) for comparable effort,
  reusing existing tooling.
- The only real W3B residual is a plain-language coverage-lean line, deliverable web-only.
- Building the full backend W3B is effort mismatched to its value.

### Speculation (genuinely uncertain)
- Whether a text "mostly left coverage" line measurably improves engagement over the existing bar
  (needs real usage data).
- The exact number of articles W4 would recover (run `outlet_coverage.py` on the live catalog to
  quantify).
- Whether a future ranking feature ("relative-coverage bridges") would eventually need a story-lean
  scalar — that would be a *different*, ranking-changing project (not this W3B).

---

## Recommendation

**C — Reduce W3B to a tiny additive UI enhancement.**

Do **not** build the backend `viewpoint` object + explain/contract wiring described in
`W3B_STORY_VIEWPOINT_DESIGN.md`; its value is already delivered by the existing distribution +
blindspot, and the new fields are unrendered or duplicative. If W3B is touched at all, right-size it
to a **web-only, plain-language coverage-lean summary** ("Mostly left-leaning coverage — thin on the
right") computed from the **existing** `story.distribution` — no backend, no contract, no
parity/determinism surface, near-zero risk.

And sequence **W4 (outlet-coverage / registry expansion) ahead of even that**: it recovers
recommendable articles the catalog currently drops — a real recommendation-quality gain — using
tooling that already exists. That is the higher-value use of the next engineering block before
launch.

*Documentation only. No code was written or modified.*
