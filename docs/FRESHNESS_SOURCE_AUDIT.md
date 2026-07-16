# Freshness Source Audit — URL-date trust per configured RSS source (read-only)

**Status:** Read-only audit. No code modified. Follows the C4.2 URL-date fix
(`docs/FRESHNESS_FIX_SHADOW.md`), which makes candidacy trust a date embedded in the **article** URL.
This audit asks, per configured source: does that trust hold?

## Method & scope

- **Sources audited:** the 9 feeds in `deploy/rss_feeds.example.txt` (the shipped cross-spectrum set)
  plus the 2 non-RSS adapters `default_registry` also registers (`sources.py:444` — NewsAPI, GDELT;
  both off unless their env flag + key are set).
- **What "URL-date override" actually is:** a *global* signal, not a per-source setting. `fresh_articles`
  calls `_url_date(article_url)` for every candidate regardless of source (`corpus_health.py`); there is
  **no per-source toggle** today. So "should this source use the override" reduces to: *does the parser
  fire on this publisher's article URLs, and is the date it reads trustworthy?* The only global switch is
  `RWE_FEED_URL_DATE` (default on).
- **The parser reads only** numeric `/YYYY/MM/DD/`, numeric `/YYYY/MM/`, and a trailing `-MM-DD-YY`
  slug, from the URL **path** (query/fragment dropped), calendar-valid, slug-year `00–39`.
- **Basis (honesty):** parser behavior below is **verified** — I ran `_url_date` on a representative
  current URL for each publisher. The *URL-format conventions* are **evidence** where the repo's qbias
  corpus carries real URLs (Fox, Washington Times — historical) and **engineering judgement** (stable,
  well-known publisher conventions) for current formats; the live feed is not inspectable here (empty
  store, network-restricted). Verify against production with
  `python examples/freshness_shadow.py --db <live>` — it prints `url-date <d>` vs `fallback <d>` per
  article, i.e. exactly which publishers' URLs get a date.

## Verified parser behavior (ran `_url_date` on each)

| Source | Representative article URL | `_url_date` result |
|---|---|---|
| Guardian | `…/us-news/2026/jul/15/senate-…` | **None** (3-letter month not parsed) |
| NPR | `…/2026/07/15/nx-s1-…/congress-vote` | 2026-07-15 |
| CNN | `…/2026/07/15/politics/…/index.html` | 2026-07-15 |
| CNN live-blog | `…/live-news/russia-ukraine-war-news-04-18-23/…` | 2023-04-18 |
| NYT | `…/2026/07/15/us/politics/….html` | 2026-07-15 |
| BBC | `…/news/articles/c0jq4z8lz9po` | None (opaque ID) |
| The Hill | `…/homenews/senate/5301234-budget-…/` | None (no FP on the ID) |
| Fox (current) | `…/politics/senate-passes-budget-bill` | None (dateless slug) |
| Fox (2013, qbias) | `…/politics/2013/07/11/house-…/` | 2013-07-11 |
| NY Post | `…/2026/07/15/us-news/…/` | 2026-07-15 |
| Washington Times | `…/news/2026/jul/15/budget-battle/` | **None** (3-letter month not parsed) |

## Required table

| Publisher | URL contains date? | Trustworthy? | Recommendation |
|---|---|---|---|
| **The Guardian** | Yes — `/YYYY/mon/DD/` (**alpha month**) | **Yes, high** — but **not parsed today** | **Override desired, currently inert.** Extend parser to read 3-letter months, else it silently uses feed date. |
| **NPR** | Yes — `/YYYY/MM/DD/` numeric | Yes | **Use override** (works today). |
| **CNN** | Yes — `/YYYY/MM/DD/` + `-MM-DD-YY` live-blog | Yes — incl. catching re-dated live blogs | **Use override** (works; the motivating fix). |
| **The New York Times** | Yes — `/YYYY/MM/DD/` numeric (articles + `/live/`) | Yes | **Use override** (works today). |
| **BBC News** | No — opaque story ID | N/A (no date) | **Rely on feed date.** Parser inert & safe (no FP). |
| **The Hill** | No — numeric story ID `NNNNNNN-slug` | N/A (no date) | **Rely on feed date.** Parser inert & safe (no FP). |
| **Fox News** | Current No (dateless); legacy Yes numeric | Current N/A; legacy Yes | **Rely on feed date** for the live feed; harmless if a legacy dated URL appears (correctly aged). |
| **New York Post** | Yes — `/YYYY/MM/DD/` numeric | Yes | **Use override** (works today). |
| **Washington Times** | Yes — `/news/YYYY/mon/DD/` (**alpha month**) | **Yes, high** — but **not parsed today** | **Override desired, currently inert.** Same alpha-month gap as Guardian. |
| *NewsAPI* (adapter) | Depends on underlying publisher | Per that publisher; NewsAPI `publishedAt` also reliable | Override safe where the embedded URL is dated; harmless otherwise. |
| *GDELT* (adapter) | Depends on domain (arbitrary) | URL date > GDELT `seendate` for true recency | Override **beneficial** (seendate ≠ pubdate); safe (unmatched → fallback). |

## Per-source determinations (the five questions)

**The Guardian** — *Permanently encodes date?* Yes; Guardian URLs are immutable and always carry
`/section/YYYY/mon/DD/slug`. *Reused?* No (unique per article). *Old URL, updated content?* Rare;
updates keep the original dated URL; live blogs carry the day's date. *Trustworthy?* Yes, among the most
reliable date-in-URL publishers. *Use override?* Yes in principle — **but the numeric-only parser misses
the alpha month**, so today Guardian falls back to the feed date (the very failure mode the fix targets).
The one high-value follow-up: teach the parser 3-letter months.

**NPR** — *Encodes date?* Yes, numeric `/YYYY/MM/DD/id/slug` (verified). *Reused?* No. *Old URL updated?*
Occasionally, but URL/date stays. *Trustworthy?* Yes. *Override?* Yes — active and correct today.

**CNN** — *Encodes date?* Yes, `/YYYY/MM/DD/…/index.html`; live-news pages encode the creation date in the
`-MM-DD-YY` slug (both verified). *Reused?* Dated articles: no. *Old URL updated?* **Yes — live-news
blogs** (the reported bug): the page is re-served with a refreshed feed `pubDate` while its content stays
anchored to that slug's date. This is where the URL date is *most* valuable — it overrides the refreshed
feed date. *Trustworthy?* Yes, and specifically corrective for re-dated live blogs. *Override?* Yes — the
motivating case; active today.

**The New York Times** — *Encodes date?* Yes, numeric `/YYYY/MM/DD/…​.html` and `/live/YYYY/MM/DD/`
(verified). *Reused?* No. *Old URL updated?* NYT updates articles ("updated" stamps) but keeps the dated
URL; content anchored. *Trustworthy?* Yes. *Override?* Yes — active today.

**BBC News** — *Encodes date?* No — opaque IDs (`/news/articles/cXXXX`, legacy `/news/topic-NNNNNNNN`),
verified None. *Reused?* IDs aren't reused for different stories, **but BBC characteristically updates a
running story in place under one fixed ID.** *Old URL, updated content?* Yes — routinely. *Trustworthy?*
N/A, no date. *Override?* N/A — parser inert (no FP verified); the feed `pubDate` is the only and
appropriate signal.

**The Hill** — *Encodes date?* No — numeric story ID `/section/NNNNNNN-slug/` (verified None; no FP on the
7-digit ID). *Reused?* No. *Old URL updated?* Occasionally; no URL date involved. *Trustworthy?* N/A.
*Override?* N/A — feed date; parser inert & safe.

**Fox News** — *Encodes date?* **Era-dependent:** current articles are dateless `/section/slug` (verified
None); the 2012–13 qbias corpus shows numeric `/section/YYYY/MM/DD/slug/` (verified → 2013-07-11). The
live `politics.xml` feed serves the current dateless form. *Reused?* No. *Old URL updated?* Some updates;
current URLs carry no date. *Trustworthy?* Current N/A; legacy date was reliable. *Override?* For the live
feed: N/A → feed date. **Harmless** if a legacy dated Fox URL is ever ingested (via NewsAPI/GDELT) — it is
correctly aged out.

**New York Post** — *Encodes date?* Yes, WordPress-style `/YYYY/MM/DD/section/slug/` (verified
2026-07-15). *Reused?* No. *Old URL updated?* Rare; dated permalink stays. *Trustworthy?* Yes. *Override?*
Yes — active today.

**Washington Times** — *Encodes date?* Yes — `/news/YYYY/mon/DD/slug/` (evidence: qbias `/news/2013/feb/12/`;
verified current `/news/2026/jul/15/` → **None**). *Reused?* No. *Old URL updated?* Low. *Trustworthy?*
Yes, high. *Override?* Yes in principle — **but the alpha month blocks the parser**, same gap as Guardian.

**NewsAPI (adapter)** — returns the underlying publisher's article URL plus a reliable `publishedAt`. URL
date present/trustworthy per that publisher's convention; override applies to the embedded URL with the
same per-publisher logic, and NewsAPI's `publishedAt` is a good fallback. Low risk.

**GDELT (adapter)** — returns arbitrary-domain URLs plus `seendate` (when GDELT first saw it, **not** the
publication date; GDELT surfaces older articles). Where a domain embeds a parseable date, the URL date is
**more** trustworthy than `seendate` for true recency; unmatched domains fall back to `seendate`. Override
is beneficial and safe here.

## Cross-cutting findings

1. **Two of nine feeds have a real, trustworthy date the parser can't read — Guardian & Washington
   Times** — because both use a **3-letter month** (`/YYYY/mon/DD/`). They silently fall back to the feed
   `pubDate`, i.e. they remain exposed to exactly the undated/re-dated staleness the fix was built to
   stop. This is the single highest-value follow-up: a numeric-*or*-month-name variant of `_URL_YMD`.
   (Evidence: qbias `washingtontimes.com/news/2013/feb/12/…`; verified parser → None.)
2. **The fix is inert-safe on the no-date sources — BBC, The Hill, Fox (current).** Verified: their
   opaque/numeric IDs and dateless slugs yield **None** (no false positives), so those feeds correctly
   keep using the feed date. No source is *harmed* by the global override.
3. **Fox's format is era-dependent.** The live feed is dateless (→ feed date); only legacy Fox URLs carry
   a numeric date, which the parser would age out correctly. No action.
4. **The override helps exactly where it should today: CNN, NPR, NYT, NY Post** — all numeric
   `/YYYY/MM/DD/`, verified — and CNN's `-MM-DD-YY` live-blog slug, the reported root-cause case.

## Recommendations

1. **Keep `RWE_FEED_URL_DATE` on (global).** It is active-and-correct for CNN/NPR/NYT/NY Post and
   inert-safe for BBC/Hill/Fox — no per-source disable is warranted.
2. **(Follow-up, code — out of this read-only audit)** Extend `_url_date` to accept a 3-letter-month
   `/YYYY/mon/DD/`, unlocking Guardian & Washington Times. Highest value/lowest risk next step; it only
   *adds* a recognized format (still calendar-validated, still path-only), matching two shipped feeds.
3. **Verify on production data** with `python examples/freshness_shadow.py --db <live>` once the live
   catalog is populated — confirm which publishers show `url-date` vs `fallback`, and that no publisher's
   slug false-positives.

## Evidence / Engineering judgement / Speculation

**Evidence (verifiable)**
- The 9 feeds + NewsAPI/GDELT adapters (`deploy/rss_feeds.example.txt`, `sources.py:444`).
- Parser behavior per publisher (ran `_url_date`; table above) — including no false positive on The
  Hill/BBC numeric IDs.
- Historical URL formats for Fox (`/section/YYYY/MM/DD/`) and Washington Times (`/news/YYYY/mon/DD/`) from
  the in-repo qbias corpus.
- The override is global (`corpus_health.fresh_articles`), gated only by `RWE_FEED_URL_DATE`; no
  per-source toggle exists.

**Engineering judgement (stable conventions, not live-verified here)**
- Current article-URL formats for Guardian, NPR, CNN, NYT, BBC, The Hill, Fox, NY Post, Washington Times.
- Guardian/NYT/NPR/CNN update-in-place-but-keep-URL behavior; BBC's update-under-one-ID habit; that the
  live Fox feed serves the current dateless format.

**Speculation (genuinely uncertain)**
- Exact live-feed URL shapes today (publishers occasionally re-platform — verify with the shadow tool).
- Residual false-positive risk from a word-slug that coincidentally ends `-MM-DD-YY` on a dateless-path
  publisher (constrained, low; quantify by running the shadow tool over the live catalog).

*Read-only. No code was modified.*
