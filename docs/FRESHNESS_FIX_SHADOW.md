# Freshness Fix (C4.2) — URL-Date Signal: Shadow Report & Design

**Status:** Implemented. Candidacy-only change. No recommendation algorithm (RWE / W1 / W2 / W3A),
explainability, or REPORT CONTRACT surface was touched. Root cause per
`docs/FRESHNESS_ROOT_CAUSE_AUDIT.md`, re-verified against the code before implementing (no
divergence).

## The one-line fix

The freshness gate's age was keyed on dates the **feed** supplies or that **we** stamp at first
sight (`publishedAt` → first-seen `createdAt` → `fetchedAt`). An archived article the feed left
**undated** fell back to today's `createdAt`; one the source **re-dated recent** (a re-surfaced live
blog) carried a fresh `publishedAt`. Both read as fresh and entered the candidate pool.

The fix adds the one date signal that neither a re-poll nor an undated archive can fake — **the date
embedded in the URL path** — and makes it the authoritative candidacy age when present:

```
/2023/04/18/opinions/…      -> 2023-04-18   (YYYY/MM/DD path segments)
…-russia-ukraine-04-18-23/  -> 2023-04-18   (trailing -MM-DD-YY live-blog slug)
/2026/07/10/politics/…      -> 2026-07-10   (a genuinely new URL stays fresh)
/guides/media-literacy      -> no signal    (dateless evergreen: untouched)
```

A URL with **no** date is left exactly as before (its feed/first-seen age), so genuine evergreen
content is preserved. Confined to `corpus_health.fresh_articles` (both corpus paths already route
through it); off-switch `RWE_FEED_URL_DATE=0`.

## Before / after shadow (`examples/freshness_shadow.py --demo`)

Representative sample — the five required scenarios plus the two archived CNN URLs reported in the
field. `now = 2026-07-15`, window `60d`. Verbatim output:

```
freshness shadow (C4.2 URL-date signal): built-in demo sample
  articles=6  window=60d  now=2026-07-15
  candidates: before=5  after=3  EXCLUDED=2  RESCUED=0

  EXCLUDED (kept before, dropped now):
    - https://edition.cnn.com/2023/04/18/opinions/2024-presidential-election-alternative-voters-lieberman
        url-date 2023-04-18
    - https://edition.cnn.com/europe/live-news/russia-ukraine-war-news-04-18-23/index.html
        url-date 2023-04-18
```

| # | Article (scenario) | Feed date it carried | Before | After | Why |
|---|---|---|---|---|---|
| 1 | `…/2023/04/18/opinions/…` — **archived opinion, undated** | none → `createdAt`=today | ✅ kept | ❌ **excluded** | URL date 2023-04-18 < cutoff |
| 2 | `…russia-ukraine-war-news-04-18-23/…` — **archived live blog, re-dated** | recent `publishedAt` | ✅ kept | ❌ **excluded** | URL slug 2023-04-18 overrides the refreshed date |
| 3 | `…/explainer/how-primaries-work` — **undated RSS, no URL date** | none | ✅ kept | ✅ kept | no URL signal → unchanged (evergreen) |
| 4 | `…/guides/media-literacy` — **dateless evergreen** | none | ✅ kept | ✅ kept | no URL signal → unchanged |
| 5 | `…/2026/07/10/politics/new-story/…` — **newly published** | recent | ✅ kept | ✅ kept | URL date 2026-07-10 ≥ cutoff |
| 6 | `…/plain/ancient-but-honest` — **plainly stale, correctly dated** | 90d old | ❌ excluded | ❌ excluded | already caught by the existing age gate |

**Exactly the two archived pages flip to excluded; nothing else moves.** Run without `--demo` (or with
`--db sqlite:///beta.db`) to produce the same diff over the live catalog.

## Why this solution (and why the alternatives were rejected)

**Chosen — URL-path date as the authoritative candidacy age.** It is the only signal that (a) reveals
staleness for *both* observed failure modes — undated and source-re-dated — because it is independent
of the feed's date and of our first-seen/fetch clock; (b) preserves evergreen content, since a URL
with no date produces no signal and nothing changes; (c) is deterministic and offline — a pure
function of the URL string, no page fetch, no network; (d) is minimal and localised — one helper plus
a few lines in the existing shared filter, touching neither ranking nor the contract; (e) is
self-correcting — a genuinely recent article has a recent URL date and stays fresh.

Rejected:

- **Turn on `RWE_FEED_REQUIRE_DATED` by default.** Excludes *all* undated items — killing the
  legitimate evergreen content the objective says to preserve — **and** misses the re-dated live blog
  entirely (it has a `publishedAt`). Both too blunt and insufficient.
- **Fetch each page and parse its `<meta>`/JSON-LD publish date.** Authoritative, but a network
  crawl per article: slow, rate-limit-prone, non-deterministic, and a large new dependency surface —
  disproportionate to a candidacy filter, and out of scope.
- **Reject stale at ingest.** Wrong layer: stored rows are intentionally kept for Search / Stories /
  Reading History; staleness belongs at candidate selection, where the gate already lives. Rejecting
  at ingest would also delete data users can still browse.
- **Trust only the URL date, ignore feed dates.** Loses the correctly-dated common case and the
  `createdAt` "immortal-undated" fix (C4.1). The URL date should *augment* the age keys, not replace
  the whole ladder — which is exactly what this does (URL date first, existing ladder underneath).
- **Blanket "drop anything whose URL contains an old year."** Too loose — would catch a `2023` that is
  a topic ("election-2024"), an ID, or a score. The fix instead requires a *calendar-valid* date in a
  *recognised position* (path segments or a trailing slug) with a constrained slug-year window, so a
  non-date number can't masquerade as one (`top-25-…`, `-…-99` are not dates).

## Precision & determinism

- **Deterministic:** `_url_date(url)` is a pure string→date function; `fresh_articles` takes `now`
  explicitly (tests pin it). Same inputs → same candidate set, always.
- **False-positive guarded:** month ∈ 01–12, day ∈ 01–31, 4-digit year `19|20`, slug year 00–39;
  matched against the URL **path only** (query/fragment dropped); an impossible calendar date
  (`2023/02/30`) yields *no signal* rather than a wrong one or a crash.
- **Metrics untouched:** the URL date feeds candidacy only. `_published` (health metrics + the
  newest-first sort) is unchanged, so no reported freshness/age metric shifts — the same discipline
  as C4.1's `createdAt` ordering.
- **Rollback:** `RWE_FEED_URL_DATE=0` restores byte-for-byte the pre-C4.2 candidacy
  (test: `test_toggle_off_is_byte_compatible_with_pre_fix_behaviour`).

## Tests (`tests/test_freshness_url_date.py`, 24 cases)

Parser units + false-positive guards; the env toggle; the **five required scenarios** (genuinely old,
undated RSS, evergreen, live blog, newly published); priority semantics (URL date overrides a recent
age; a recent URL rescues a wrong-old feed date); byte-compatible off-switch; determinism;
exempt/disabled/`require_dated` interactions; `build_candidate` integration; both reported URLs; and
proof the health metrics don't shift. The pre-existing `tests/test_freshness.py` (19 cases) still
passes unchanged.

*The change is candidacy-only; recommendation algorithms, explainability, and the report contract are
not modified.*
