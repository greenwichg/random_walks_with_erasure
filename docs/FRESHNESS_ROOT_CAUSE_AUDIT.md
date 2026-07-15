# Freshness Root-Cause Audit — CNN 2023 articles surfacing as bridge recommendations

**Status:** Read-only root-cause analysis. No code written, no code modified. **No fixes proposed** —
this document proves the root cause only, per the request.

**Observed symptom:** bridge (RWE-B) recommendations pointing at archived 2023 CNN articles, e.g.
- `edition.cnn.com/2023/04/18/opinions/2024-presidential-election-alternative-voters-lieberman`
- `edition.cnn.com/europe/live-news/russia-ukraine-war-news-04-18-23/index.html`

---

## Root cause (up front)

**The freshness gate exists and is on by default (60 days), but it keys on a publication date the
pipeline cannot trust, and it never reads the date embedded in the URL path.** Specifically the age
used for the gate is `publishedAt` (the **feed-supplied** `<pubDate>`) → else `createdAt`
(first-seen) → else `fetchedAt` (`corpus_health.py:114,210`). Given the **default-enabled 60-day
gate** (`RWE_FEED_MAX_AGE_DAYS`, default `60`, `corpus_health.py:165`), a CNN article carrying its
**true** 2023 date would have been **rejected**. So the fact that these 2023 articles reached
recommendations proves the date the gate saw was **not** 2023 — it was one of:

1. **Absent / unparseable in the feed → undated.** `_to_iso` returns `None` on a missing or
   unparseable date (`rss_ingest.py:111-126`); the row's `publishedAt` is then empty, so the gate
   falls back to `createdAt` = **first-seen at this ingest = today** (`corpus_health.py:114,210-212`).
   An undated 2023 article therefore looks brand-new and **passes**. The one defence
   (`RWE_FEED_REQUIRE_DATED`, which would exclude undated items) is **off by default**
   (`corpus_health.py:169-177`).
2. **Re-dated recent by the source.** A live-blog page (`…-04-18-23/index.html`) that CNN keeps
   updating can legitimately carry a **recent** `<pubDate>` in the feed → the gate passes it
   correctly, because by the date it was handed the item *is* fresh; the 2023 in the URL is content
   the gate has no way to see.
3. **Read-exempt.** Any article a user actually read is force-kept past the gate
   (`exempt=read_urls`, `corpus_health.py:205-206`; re-added in `corpus_refresh.py:106-111` and
   `feed_source.py:129-134`).
4. **Gate disabled.** If `RWE_FEED_MAX_AGE_DAYS=0` in the running environment, the gate returns every
   article unchanged (`corpus_health.py:198-200`) and nothing is filtered by age at all.

This is fundamentally an **ingestion / date-provenance** root cause: we trust the feed's date (and
fall back to our own first-seen/fetch time when it is missing) and never derive publication date from
the URL path or the article page. The recommendation-layer freshness gate **works as designed** but
is blind to stale content whose supplied date is missing or refreshed.

**One caveat I must be explicit about:** the live store in this environment is **empty**
(`count_feed_articles() == 0`), so I could not read the *actual* stored `publishedAt`/`createdAt` of
your two example rows. Which of cases 1–4 applies to each specific URL is therefore not provable from
code alone — it lives in the feed bytes / your DB. The **structural** root cause above holds
regardless of which case each URL is, and §"How to disambiguate" gives you the exact read-only
commands to pin each one.

---

## Answers to the seven questions

### 1. Why were these articles ingested?
**Because a configured feed listed them and ingestion stores whatever a feed hands it — with no age
check.** `ingest_entries` (`rss_ingest.py:311-344`) iterates the feed's items and, for every entry
with a resolvable host, scores it and calls `upsert_feed_article` (`:333-343`). There is **no
freshness, age, staleness, or cutoff test anywhere in the ingestion path** (a grep for
`fresh|max_age|stale|cutoff|reject|age_days|freshness` across `rss_ingest.py` returns nothing). An
undated entry is *counted* (`missing_metadata += 1`, `:313-314`) but **not dropped**. Ingestion is
intentionally store-everything; keeping stale rows is by design — they remain visible to Search,
Stories, and Reading History (`corpus_health.py:162-164`). Ingestion is **not** where staleness is
supposed to be filtered.

### 2. Did the RSS feed itself expose them? **Almost certainly yes — we have no other way to acquire a URL.**
Ingestion only ever stores URLs that arrive in a feed's `<item>`/`<entry>` list (`parse_feed` →
`_rss_item`/`_atom_entry`, `rss_ingest.py:186-227`); there is no crawler, no "related articles"
expansion, no URL discovery outside the feed. So a 2023 CNN URL in the store means **some configured
feed served it in its item list** at fetch time. What I **cannot** determine from code is *what date
the feed attached to it* — the `<pubDate>` is in the feed bytes, which are not in this repo. (CNN
feeds do re-surface older/evergreen opinion pieces and anchor live-blog pages; that is consistent
with what you saw, but it is an observation about CNN, not something the code proves.)

### 3. Does our ingestion validate publication date? **No — it only parses it.**
`_to_iso` (`rss_ingest.py:111-126`) *normalises* a feed date (RFC 822 via
`email.utils.parsedate_to_datetime`, else RFC 3339 via `datetime.fromisoformat`) and returns `None`
if it can't parse — but there is **no validation that the date is recent, plausible, or even
present**. A `None` (undated) result is stored as-is; nothing rejects it. There is likewise no
cross-check of the date against the **URL path** (`/2023/04/18/`) or the article page.

### 4. Which date is used — feed, page, or fetch time? **Feed date, with fallbacks to our own first-seen then fetch time. The page date is never read.**
- **Stored:** `published_at = e.published_at`, i.e. the **feed's** `<pubDate>`/`<date>`/`<published>`
  (RSS) or `<published>` else `<updated>` (Atom) (`rss_ingest.py:193-200,224`, `:336`). We never
  fetch the article HTML, so the **page's own publication date is never consulted**.
- **Used by the freshness gate:** `_CANDIDACY_TIME_KEYS = ("publishedAt", "createdAt", "fetchedAt")`
  (`corpus_health.py:114`) — feed `publishedAt` first; else `createdAt` (the row's **stable
  first-seen** time, stamped once at ingest); else `fetchedAt` (refreshed on every re-poll). The
  `createdAt`-before-`fetchedAt` order is a deliberate C4.1 fix (`corpus_health.py:108-113`) so an
  undated item ages from first discovery instead of resetting to "fresh" on every poll — **but for a
  freshly-ingested undated 2023 article, first-discovery is *today*, so it still reads as fresh.**
- **The URL-path date (`/2023/04/18/`) is never parsed** anywhere in ingestion or candidacy.

### 5. Should stale articles have been rejected? **Only at candidacy — and only when their date proves they're stale. For these, the date didn't.**
By design, staleness is filtered **at recommendation-candidate selection, not at ingest** (stored
rows are kept for Search/Stories/History). The candidacy gate *would* have rejected a correctly-dated
2023 article under the default 60-day window. It did **not** reject these because the date it was
handed did not say "2023": either undated → `createdAt=today` fallback (case 1), re-dated recent
(case 2), read-exempt (case 3), or the window is disabled (case 4). So: they *should* have been kept
out of recommendations, and the mechanism meant to do that was in force — it was **defeated by the
date provenance**, not absent.

### 6. Does recommendation candidate generation have a freshness filter? **Yes — one gate, on both corpus paths, enabled by default.**
- `corpus_validation.build_candidate` runs `ch.fresh_articles(...)` **before** the publisher cap
  (`corpus_validation.py:65`); this is what the live hot-swap corpus builds through
  (`corpus_refresh.build_candidate_for` → `build_candidate`, `corpus_refresh.py:105`).
- The qbias-export corpus path also filters: `feed_source.py:129`
  `corpus_health.fresh_articles(rows, exempt=read_urls)`.
- `fresh_articles` (`corpus_health.py:180-213`): window from `feed_max_age_days()` (**default 60**,
  `0` disables, `:158-166`); `require_dated` from `RWE_FEED_REQUIRE_DATED` (**default off**,
  `:169-177`); keep an article iff it is `exempt`, **or** (`require_dated` off) undated-by-fallback,
  **or** `dt >= cutoff`. So the filter is real and on — its blind spots are exactly cases 1–4 above.

### 7. Ingestion, corpus, or recommendation issue? **Primarily ingestion / date-provenance. The corpus store and the recommendation gate are behaving as designed.**
- **Ingestion (root):** trusts the feed's date, falls back to our own first-seen/fetch time for
  undated items, and never derives a date from the URL path or page — so a stale article can enter
  carrying a missing or misleadingly-recent date.
- **Corpus store (not the bug):** storing stale rows is intentional; they are meant to be kept and
  simply excluded from *recommendations*, which is the gate's job.
- **Recommendation gate (working, but blind):** the freshness filter exists, is enabled, and applies
  on both paths; it correctly rejects articles whose date proves them stale. It cannot reject an
  article whose supplied date is absent (→ fresh-by-fallback), refreshed (→ fresh-legitimately),
  read-exempt, or when the operator disabled the window. Those are the failure modes — all rooted in
  date provenance, not in the ranking/bridge algorithm, which never sees or uses article age.

---

## How to disambiguate (read-only, on the live DB — I could not, the store here is empty)

These pin *which* of cases 1–4 produced each URL. All read-only; none is a fix.

1. **What date is stored for the two URLs** (case 1 vs 2/3):
   `sqlite3 <db> "select canonical_url, published_at, created_at, fetched_at from feed_articles where canonical_url like '%2023/04/18/opinions%' or canonical_url like '%russia-ukraine-war-news-04-18-23%';"`
   - `published_at` empty → **case 1** (undated → `createdAt`/today fallback).
   - `published_at` a **recent** timestamp → **case 2** (source re-dated).
   - `published_at` = a **2023** timestamp **and** the row is present in recommendations → it was
     **read** (case 3) or the window is off (case 4).
2. **Is the window enabled** (case 4): `echo "$RWE_FEED_MAX_AGE_DAYS"` in the serving environment —
   `0` (or negative/unset-to-0) disables the gate.
3. **Were they read** (case 3): check `reads` / `distinct_read_urls()` for those canonical URLs — a
   read URL is force-kept (`exempt`) regardless of age.
4. **What the feed actually served:** re-fetch the offending feed and inspect the item's `<pubDate>`
   for those links — this shows whether the feed omitted, kept, or refreshed the date.

---

## Evidence (verifiable in-repo)
- Ingestion has **no** age check; store-everything (`rss_ingest.py:311-344`; grep for freshness terms
  in that file: none). Undated only increments `missing_metadata` (`:313-314`).
- Feed date is what's stored (`rss_ingest.py:193-200,224,336`); `_to_iso` → `None` on missing/bad date
  (`:111-126`); page/URL-path date never read.
- Freshness gate exists on both corpus paths and is default-on:
  `corpus_validation.build_candidate` → `fresh_articles` (`corpus_validation.py:65`;
  `corpus_refresh.py:105`); `feed_source.py:129`.
- Gate keys `("publishedAt","createdAt","fetchedAt")` (`corpus_health.py:114`), keeps undated/`dt>=cutoff`
  (`:210-212`); window default 60, `0` disables (`:158-166`); `require_dated` default off (`:169-177`);
  read URLs exempt (`:205-206`, re-added `corpus_refresh.py:106-111`, `feed_source.py:129-134`).
- Live store empty here (`count_feed_articles() == 0`) → the specific rows' stored dates are not
  inspectable from this environment.

## Engineering judgement (defensible inference)
- The default-60-day gate rules out "a correctly-dated 2023 article passed" (it would be rejected) —
  so the surfaced 2023 articles necessarily fall into cases 1–4, all date-provenance failures.
- The opinion evergreen (`/2023/04/18/opinions/…`) is most consistent with **case 1** (undated →
  first-seen-today fallback); the continuously-updated live-blog anchor
  (`…russia-ukraine-war-news-04-18-23`) is most consistent with **case 2** (source-refreshed date).
  Both remain hypotheses until the DB rows in §"How to disambiguate" are read.
- The root cause is upstream of ranking: the bridge/RWE-B algorithm never reads article age; it can
  only recommend from the candidate pool the freshness gate produced.

## Speculation (genuinely uncertain — not proven)
- Exactly which feed served each URL and what `<pubDate>` it attached (needs the feed bytes / DB).
- Whether either URL was read (would make it exempt).
- Whether the environment overrides `RWE_FEED_MAX_AGE_DAYS`/`RWE_FEED_REQUIRE_DATED` from their
  defaults.

*Documentation only. No code was modified. No fix is proposed here — the next step is to confirm which
of cases 1–4 applies via the read-only checks above, then decide a fix.*
