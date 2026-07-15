# W3A — Production-Readiness Audit (Read-Only)

**Status:** Read-only audit. No code written, no production code modified. W3A (political mask
delegates to `classify_topic`) is already merged and validated (unit / regression / parity /
REPORT CONTRACT v1 / qbias shadow). This audit decides how to enable it on the **live feed**.

**Recommendation (up front): C — Run shadow mode first**, using the existing
`migrate_topics.py --dry-run` over the live catalog to quantify the delta on production data and to
re-score existing rows before enabling — because the change is large (~2× political on real news),
its live-scale impact is currently unmeasured (the live store is empty in this environment), and
existing stored rows retain the OLD flag until re-scored.

---

## 1. Live-feed ingestion pipeline audit

**Where `classify_topic()` / `looks_political()` run.**
- Live ingestion: `rss_ingest.ingest_entries` (`rss_ingest.py:296-345`) builds a `RawRead` and calls
  `ingest.score_with_cache(raw, scorer, store_)` — the **same `ingest.Scorer`** the reading path uses
  (`make_scorer`, `:290`). `Scorer.score` computes `category = classify_topic(...)` and
  `political = _political_from_topic(category, raw.title)` (`ingest.py:414-416`, the W3A change).
- `looks_political()` (now delegating to `classify_topic`) is used by the qbias catalog builder
  (`simulate_users.py:201`) and `migrate_topics.py:45`; the live Scorer uses `_political_from_topic`
  directly (reusing the topic it just computed).

**Does W3A automatically apply to live-feed ingestion?** **For newly-ingested articles, yes.** The
Scorer is the single ingestion scorer, and it now carries the W3A logic — every *new* RSS article is
classified with the new mask, and `political` is persisted in `FeedArticle.scored`
(`upsert_feed_article(scored=…)`).

**For existing stored articles, no — not automatically.** `political` is persisted **inside the
`scored` JSON blob** (`FeedArticle.scored`, `store.py:350`; and the per-URL `ScoredArticle` cache,
`:242-245`). Two mechanisms freeze the old value:
- `score_with_cache` reuses a previously-scored article for the same canonical URL (cache hit → OLD
  scoring returned).
- `upsert_feed_article` on a re-poll refreshes `fetched_at` and backfills only *empty* fields — it
  **never rewrites `scored`** (`store.py`, duplicate branch). So a re-poll keeps the first-seen
  `scored.political`.
The serving corpus reads the stored value (`feed_source.py:73` `political = scored.get("political")`),
so **existing rows would keep the OLD flag** → a split-brain corpus (new rows new-flag, old rows
old-flag) until re-scored.

**Are additional code changes required?** **No changes to the classification logic** — it is
complete. But a **one-shot re-score of existing rows is a prerequisite** for a consistent live
corpus, and that tool **already exists**: `migrate_topics.py` re-runs every stored article
(`scored_articles`, `reads`, `feed_articles`) through the canonical scorer and **re-derives the
`political` flag** (its docstring, `:12-13`), with `--dry-run`. Caveat: it **"never downgrades"**
political (already-political stays political), so it recovers false **negatives** but does not remove
false **positives** on existing rows (minor — see §2). No new code is needed to enable W3A; the
migration is an operational (data) step run with an existing tool.

---

## 2. OLD vs NEW political classification on the live-feed corpus

**Environment caveat (important):** the live store in this environment is **empty** —
`count_feed_articles() == 0`. The only feed snapshot present, `data/feed_corpus.csv`, is a curated
12-row **all-political** sample (12/12 political under both masks; **0 flips**) — too small/curated
to characterize impact. So the representative-scale evidence is the **Qbias shadow** (21,754 real US
news articles), which W3A already ran and which mirrors the live RSS mix.

| Metric | Value (Qbias proxy, 21,754 real news articles) |
|---|---|
| total articles | 21,754 |
| political — OLD (substring) | 7,390 |
| political — NEW (W3A) | 14,326 |
| false positives removed | 9 |
| false negatives recovered | **6,945** |
| bridge candidates — left-lean political (for right readers) | 3,591 → **6,735** |
| bridge candidates — right-lean political (for left readers) | 2,467 → **4,793** |
| topic distribution change | flips concentrated in **institutional politics** the substring test missed — Courts / Supreme Court / Senate / gun-policy / immigration; non-political topics (Sports/Business/Entertainment) unaffected except the 9 FP |
| outlet distribution change | **none** — the mask never reads the outlet; outlet + lean are untouched |

**Read:** the dominant effect is **false-negative recovery** (~2× political), overwhelmingly genuine
institutional-politics stories; false-positive removal is negligible (9). Bridge candidates roughly
double. This is the intended, validated correction — but it is **large**, and unobserved on the
actual live feed (empty here).

---

## 3. Does any recommendation behaviour change unexpectedly?

| Surface | Change | Verdict |
|---|---|---|
| **W1 bridge-slot** | Slot **budget** (4/6/8 via `blend_plan_for`) is **unchanged**. The `political` flag is "the flag behind the cross-cutting gate" (`api_fastapi.py:624-626`), so the **bridge-eligible candidate pool sharpens** (more genuine political cross-cutting items). Which items fill the slots shifts. | Intended, not a regression; W1 tests green |
| **W2 adaptive-exposure inputs** | Cross-cutting reception (`shownCross`/`openedCross` via `_cross_of(…, political)`) is counted over the sharper mask → the exposure **input distribution shifts** (measures real political cross-cutting). Mechanism, κ, and the three invariants are **unchanged**. | Intended input sharpening; W2 tests green |
| **Story clustering** | **Unaffected.** `clustering.cluster` keys on title tokens + time (`clustering.py`); the story `distribution`/`blindspot` use `leanBucket` (outlet lean), **not** the political mask (`story_service.py:49,73`). | Byte-identical |
| **Explainability** | Political-perspective explanations become more accurate (more genuine political items); **explain↔served parity holds** (both consume the same mask). No contract change. | Safe; parity green |
| **REPORT CONTRACT v1** | **Schema byte-identical** (rec_sandbox tests green; the report's story block selects fields explicitly). For a *real* live reader the Open-Mindedness / cross-cutting **values** reflect the new mask — a value change, not a schema change. | Contract preserved |

**Nothing changes unexpectedly.** Every shift is confined to the political-flag consumers (W1 bridge
pool, W2 reception input, political-perspective explanations) and is the intended sharpening; the
recommendation *algorithms*, story clustering, parity, and the contract schema are untouched.

---

## 4. Rollout recommendation — **C. Run shadow mode first**

Not **A** (safe immediately): the change is large (~2× political on real news) and its live-scale
impact is unmeasured here, and existing rows would be split-brain until re-scored. Not **B** (feature
flag): W3A is a Scorer-level logic change already merged — there is no flag, adding one is new code,
and a flag alone does not fix the split-brain corpus or measure the live delta. Not **D** (more
investigation): the *classification* is fully validated offline.

**C, concretely (all with existing tooling):**
1. **Shadow the live corpus** — run `python examples/migrate_topics.py --dry-run` against the live
   DB (writes nothing; reports exactly which stored articles flip) and/or `examples/w3a_shadow.py`
   over the live catalog. Quantify the real political-count, topic, W1 bridge-candidate, and W2
   reception deltas on production data.
2. **Re-score existing rows** — run `python examples/migrate_topics.py` to re-derive `political`
   across `feed_articles` / `reads` / `scored_articles`, eliminating the split-brain (accepting the
   "never-downgrade" caveat — a handful of existing FPs stay political; ~9/21.7k in the proxy).
3. **New ingestion already carries W3A** — no further action for fresh articles.
4. **Enable / observe** — after the migration, confirm the live Open-Mindedness / cross-cutting and
   the W1 bridge mix moved as the shadow predicted, then treat W3A as fully live.

---

## Evidence (verifiable)
- Live ingestion scores via the one `ingest.Scorer` (`rss_ingest.py:290,330`), which carries W3A
  (`ingest.py:414-416`); `political` is persisted in `FeedArticle.scored` (`store.py:350`).
- `score_with_cache` reuses cached scoring; `upsert_feed_article` never rewrites `scored` on re-poll
  → existing rows keep the OLD flag; the corpus reads the stored value (`feed_source.py:73`).
- `migrate_topics.py` re-derives `political` for stored rows (never downgrades), with `--dry-run`.
- Qbias shadow (21,754): political 7,390 → 14,326 (+6,945 FN, −9 FP); bridge pools ≈ double.
- Live store empty here (`count_feed_articles() == 0`); `feed_corpus.csv` = 12 curated all-political
  rows, 0 flips.
- Story clustering is mask-independent (`clustering.py`, `story_service.py:49,73`); W1 budget via
  `blend_plan_for` unchanged; W2 invariants + REPORT CONTRACT v1 tests green (W3A validation).

## Engineering judgement
- The classification is production-correct and auto-applies to new ingestion; the only real gap is
  the **existing-corpus re-score**, for which a tool already exists.
- The magnitude (~2× political) is the intended false-negative recovery, but it is large enough that
  a **shadow measurement on the real live feed** before enabling is prudent — the offline qbias proxy
  is representative, not identical to the live RSS mix.
- Story clustering, ranking algorithms, parity, and the contract are genuinely untouched; risk is
  confined to the political-flag consumers and is the intended sharpening.

## Risks
- **Split-brain corpus** (med likelihood / med impact): existing rows keep the OLD flag until
  `migrate_topics` runs → inconsistent cross-cutting classification. *Mitigation: run the migration.*
- **Magnitude shift unobserved at live scale** (med / med): ~2× political → materially more
  cross-cutting recs surfaced (W1) and counted (W2). *Mitigation: shadow the live feed first.*
- **FP-never-downgrade on existing rows** (low / low): `migrate_topics` won't unflag old false
  positives (~9/21.7k). *Mitigation: accept, or a one-off downgrade pass later; negligible.*
- **Unknown-outlet interaction** (low / low): mask sharpening surfaces more political items, but
  unknown-outlet items still have NaN lean and remain non-candidates (W4). No new risk from W3A.

*Documentation only. No production code was modified.*
