# W3A — Rollout-Process Audit (`migrate_topics.py`)

**Status:** Read-only audit of the rollout process only. No code written, no production code modified.
Scope: does running `migrate_topics.py` make W3A live on the existing production corpus, cleanly?

**Recommendation (up front): B — Additional work required before deployment.** The migration is
necessary but not sufficient: it does not refresh the live in-memory corpus, it does not achieve
byte-parity with the scorer, and it has no built-in backup. The extra work is bounded and operational
(the checklist below) plus one explicit decision on residual divergence.

---

## Answers

### 1. Does `migrate_topics.py` completely eliminate the old/new split-brain? **No — it narrows it, with two residual classes.**
`_reclassify` sets `new_pol = bool(old_pol) or ingest.looks_political(url, new_cat)`
(`migrate_topics.py:44-45`). Two divergences from the W3A scorer
(`_political_from_topic(category, raw.title)`):
- **Ratchet-up only (never downgrades):** an existing **false positive** (old `political=True`) stays
  `True`; the scorer on a fresh article would set `False`. (Tiny — ~9/21.7k in the Qbias proxy.)
- **Title dropped:** it calls `looks_political(url, new_cat)` **without the title**, so an
  Opinion-category **political op-ed** that the scorer recovers via the title
  (`_political_from_topic("Opinion", title)`) is **not** recovered here (its `new_cat` collapses to
  `"Opinion"`). (Small subset; institutional-category FNs — Congress/Supreme Court/Senate — *are*
  recovered because they surface through `new_cat`.)
Both residuals are **conservative** (never worse than today) but mean migrated rows are **not
byte-identical** to fresh W3A scoring.

### 2. Does it update every stored `scored.political` value? **Every row is processed; not every value equals the scorer's.**
It rewrites `scored` in `scored_articles`, `reads`, **and** `feed_articles` (`migrate_topics.py:90-106`).
So every table carrying the flag is covered. But per §1 the *value* is ratchet-up (FPs uncorrected),
so it is "every row re-derived," not "every value set to the scorer's result."

### 3. Does it leave any stale cached values? **DB cache: no. In-memory caches: yes.**
- **DB scored cache (`ScoredArticle`)** — updated (`:90-94`). Future `score_with_cache` hits return
  the migrated value. ✔
- **In-memory serving caches — NOT invalidated by a DB write:**
  - Base corpus (`Backend.mind`, built at startup) — `corpus_refresh` rebuilds only when the
    **candidate signature** changes, and that signature is **URL-only**
    (`_canonical(a)=canonicalUrl`, `corpus_health.py:132`; `candidate_signature`, `corpus_refresh.py`).
    An in-place `political` rewrite adds/removes/reorders no article → **same signature → no rebuild.**
  - Per-user models (`personalize`) — keyed on `reading_version = count_reads(uid)`
    (`personalize.py:27-28,90-95`); a migration doesn't change read counts → **no invalidation.**
  So the running server keeps serving the **OLD** `political` until a rebuild/restart.

### 4. Is the migration idempotent? **Yes.**
Documented and enforced: `_reclassify` returns `changed=False` when the category and (ratcheted)
political are already final (`:46`); `classify_topic` is idempotent on canonical categories. A second
run changes nothing (`:15`).

### 5. Can it be safely re-run? **Yes.** Idempotent, deterministic, offline; re-running is a no-op.

### 6. Does it require taking the system offline? **The migration: no. Making it take effect: yes (a restart/rebuild).**
The write is a SQLite transaction (WAL → concurrent reads fine; a large write may briefly contend the
single writer with the live process). But because it does **not** trigger a corpus rebuild or cache
invalidation (§3), the change is invisible to serving until the engine **rebuilds its base corpus and
clears in-memory caches** — reliably, a **restart** (or a forced `corpus_refresh` hot-swap if an admin
hook exists). So plan for a brief restart, not a live no-op.

### 7. After migration, will every recommendation use the W3A mask? **No — not without a restart, and not fully even then.**
- Until the server restarts/rebuilds, recs use the **stale in-memory** corpus (OLD flags).
- After restart, recs use the migrated DB — but per §1 existing **FPs** stay political and some
  **Opinion op-eds** stay non-political, so it is **≈** W3A, not identical. New ingestion is full-W3A.

### 8. Remaining production risks after migration
- **In-memory staleness** (med/med): no auto-rebuild → serving unchanged until restart.
- **Residual FP-not-downgraded** (low/low): ~9/21.7k stay wrongly political.
- **Residual Opinion-op-ed title-drop** (low/low): a small subset of political op-eds stay
  non-political vs fresh scoring.
- **No built-in backup / in-place rewrite** (med/high if skipped): the migration mutates `scored`
  directly; without a pre-backup there is no clean rollback.
- **Write contention during migration** (low/med): a large write vs the live writer.
- **Verification gap** (low/med): the tool prints category distributions, **not** a political-count
  before/after — so the political delta must be checked separately.

### 9. Is manual verification required after migration? **Yes.**
Back up first; `--dry-run` preview; re-run `--dry-run` after (expect **0 changed** = idempotence);
a read-only political-count before/after; restart and confirm the **served** feed reflects the new
flags on a known FN case; run the regression suite.

---

## Rollout checklist

| # | Step | Command | Expected result | Rollback |
|---|---|---|---|---|
| 0 | **Back up the DB** | `python examples/db_backup.py backup` | `…/backups/ih_beta-<ts>.db` written; `status` shows it | — (this *is* the rollback artifact) |
| 1 | **Preview (shadow)** | `python examples/migrate_topics.py --dry-run` | Prints `changed/rows` per table + before/after category dist; **writes nothing** | n/a (read-only) |
| 2 | **Political-count baseline** | read-only count of `political=True` across `feed_articles`/`reads`/`scored_articles` (SQL/one-liner) | A baseline number to diff against step 5 | n/a (read-only) |
| 2a | **Preflight: blob validity** *(added 2026-07-16, post-rehearsal)* | read-only scan: every `scored` blob in the three tables parses as JSON | **0 malformed rows.** If >0: **STOP** — the migration walker `json.loads`es each row unguarded, so one bad blob aborts that table's pass; a defensive fix must land before migrating | n/a (read-only) |
| 3 | **Run the migration** | `python examples/migrate_topics.py` | `changed/rows` per table (matches step 1); `scored` rewritten in place | `db_backup.py restore <backup>` **+ restart** |
| 4 | **Idempotence check** | `python examples/migrate_topics.py --dry-run` | **0 changed** in every table | n/a |
| 5 | **Political-count after** | same query as step 2 | ≈ the qbias-proportional increase (dominated by FN recovery); no unexpected drops | restore + restart if anomalous |
| 6 | **Rebuild serving corpus** | **restart the engine** (or force a `corpus_refresh` swap) | New base corpus built from the migrated DB; in-memory caches cleared; URL resolver re-attached — verify `source: "feed"` and `resolvedUrls > 0` on the diagnostics endpoint *(added 2026-07-16: the rebuild is Backend **plus** `attach_url_resolver`, `api_fastapi.py:226` — rehearsal-confirmed)* | restart on the restored backup |
| 7 | **Verify live** | `GET /api/recommendations` / `/api/report` for a known FN case (a Congress / Supreme Court story) + run the test suite | The story now reads political / cross-cutting; suites green | restore + restart |
| 8 | **Monitor** | watch Open-Mindedness / cross-cutting + the W1 bridge mix | Moves as the shadow (§ readiness doc) predicted; no error spike | restore + restart |

**Rollback summary:** every writing step (3, 6) is reversible by `db_backup.py restore <backup>` + a
restart. There is no partial-write hazard within a step (each table runs in one transaction).

---

## Recommendation: **B — Additional work required before deployment**

The classification is validated, and `migrate_topics.py` is the right tool — but "deploy = run the
migration" is **incomplete**:

1. **Add the restart/rebuild step** — the migration does not refresh the live in-memory corpus
   (URL-only signature; `reading_version`-keyed caches), so without a restart it is a live no-op.
2. **Decide the residual-parity question** — accept the tiny, conservative FP + Opinion-op-ed
   residuals **explicitly**, or (if strict scorer-parity is required) commission a title-aware,
   downgrade-capable re-score (that would be new code — out of this audit's scope).
3. **Add backup + verification to the runbook** — the migration rewrites in place with no built-in
   backup and reports category (not political) counts.

None of this is a code blocker to W3A; it is a bounded operational runbook (the checklist above) plus
one explicit residual decision. Complete those and it is safe to deploy.

*Documentation only. No production code was modified.*
