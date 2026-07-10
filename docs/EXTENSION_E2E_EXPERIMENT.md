# Extension Pipeline — End-to-End Experiment

A hands-on validation that an article discovered through the **browser extension** propagates through
the whole system: Read → History → FeedArticle (provisional) → Search/Stories → refresh → graph node →
recommended to another reader → promoted into Discover. One developer, real browser, no mocks (one
documented exception in Stage 12b).

**The automated half lives in [`examples/extension_experiment.py`](../examples/extension_experiment.py)** —
after each browser action, run it and read PASS / WAIT / FAIL per stage:

```bash
python examples/extension_experiment.py --url https://www.npr.org/<the-article-you-opened>
```

## Setup (once)

- Engine in **feed mode with the poller**: Colab cell 2 → `CORPUS="live-feed"`, `AUTO_REFRESH=True`
  (`RWE_FEED_POLL=1`). For a fast experiment set `RWE_POLL_INTERVAL=60` (default 600 s) before the
  engine starts, so "next refresh cycle" ≈ 1 minute.
- Extension installed + token configured (see `extension/README.md`); signed in as the demo reader
  (**Reader A**, engine user `1`). **Reader B** is a second engine user the probe script creates and
  drives via the API (dev mode has a single web login, so B's reads go through the same
  `/api/me/reads` contract by header — the code path is identical).
- Engine log at hand: `grep -E "feed_poll|corpus_refresh_activated|extension_catalog_failed" engine.log`.

**Recommended order:** 1 → 2 → 3 → 4 → 7 → 10 → 5 → 6 → 8 → 9 → 11 → 12.

## The stages

| # | You do (browser) | Then verify | Expected |
|---|---|---|---|
| **1** | Open a fresh article on an allowlisted site (npr.org is og-rich, no paywall). Watch the toolbar icon. | Badge + service-worker console (`chrome://extensions` → InfoDiet → service worker) | Green **✓** flash; console silent on failure reasons. `auth` = bad token; `err` = wrong app URL / dead tunnel; **no badge at all** = not detected (section page or non-allowlisted site — by design) |
| **2** | — | `--stage 2` (or app → Reading History) | Read at top of history, `readSource="extension"`, classified article fields |
| **3** | — | `--stage 3` | FeedArticle exists: `sourceType="extension"`, `articleState="provisional"`, canonical URL, category/lean/register from the **same scorer** (cache hit). If `sourceType` is a feed type, the article pre-existed → Stage 11 semantics |
| **4** | App → Search, type 2–3 headline words | `--stage 4` | Found immediately (LIKE over title/description/publisher) |
| **5** | Open the **same news event on a second allowlisted outlet** (clustering needs ≥ 2 articles from ≥ 2 publishers sharing headline tokens — `story_service.cluster_from_store`) | `--stage 5` / Stories page | A story containing both your articles. WAIT until the second outlet is read |
| **6** | wait one poll interval (or restart the engine) | `--stage 6` + log | Your read set `catalogDirty: true` (`/api/internal/refresh`); the next cycle — **even if the feeds bring nothing new** — runs the check: **`corpus_refresh_activated`**, generation +1, dirty consumed. No activation + `lean: null` → Stage 12b. Node-hood is *proven* by Stage 8 |
| **7** | App → Dashboard / Report | eyes | `today.articlesRead` +1, minutes up; Report `coverage.reads` +1; A's feed may reorder (taste updated) but never shows the article itself (seen-exclusion) |
| **8** | — | `--stage 8` | Reader B (auto-seeded with 5 catalog reads) **receives the article** across the strategy family — the complete value chain |
| **9** | — | `--stage 9`, then promote: have B read the same URL (script Stage 10 body with B, or curl) **or** catch a brand-new headline pre-poll so RSS re-discovers it | Provisional → hidden from Discover (Search still finds it). After 2nd distinct reader or feed merge: `articleState="verified"`, visible in Discover |
| **10** | Re-open the same article (within 6 h the extension skips locally — console `skipped:duplicate`) | `--stage 10` | Server re-submit: `accepted=0, duplicates=1`; still **one** FeedArticle; one history entry |
| **11** | Use the Stage-9 RSS path article | `--stage 3` before/after the poll | One row: `articleState` → `verified`, empty fields backfilled, **image upgraded** (rss 100 > extension 40 in `SOURCE_PRIORITY`), `sourceType` stays `extension` (first-seen), `fetchedAt` bumped |
| **12a** | Open an article on a **non-allowlisted** site | badge/console/history | Nothing happens anywhere — the manifest allowlist working |
| **12b** | *(the one synthetic step — unreachable from a real browser because of 12a)* `--simulate-read --url https://unknown-blog.example/post` | `--stage 3`, then after refresh `--stage 6/8` | Read ✓, FeedArticle ✓ (`lean: null`, provisional forever), Search ✓ — but **never a graph node** (corpus builder drops lean-unresolvable rows). The documented unknown-outlet gap, deferred to an outlet-resolution milestone |

## Checklist

```
□ 1 badge ✓            □ 4 searchable          □ 7 dashboard/report moved   □ 10 dup: 0 accepted / 1 article
□ 2 history row        □ 5 two-outlet story    □ 8 B receives it            □ 11 merge upgraded → verified
□ 3 provisional row    □ 6 refresh activated   □ 9 hidden → promoted        □ 12 no detection / graph gap
```

## Common failure modes

| Symptom | Likely cause | Look at |
|---|---|---|
| No badge ever | section page / site not allowlisted / extension not reloaded | service-worker console; `manifest.json` |
| Badge `auth` | token revoked | Settings → tokens; regenerate |
| Badge `err` unreachable | Colab tunnel URL changed | extension Options → new URL |
| Read ✓ but no FeedArticle | producer exception | `grep extension_catalog_failed engine.log` |
| Never a graph node | poller off / lean null / validation failed | `/api/internal/refresh`; `--stage 3` lean; `corpus_refresh_error` |
| No `source_poll` events at all | no feeds configured — cycles only run with a feeds spec | set `RWE_RSS_FEEDS` (e.g. `deploy/rss_feeds.example.txt`); a cycle must run for the refresh check to fire |
| B never gets it | refresh not activated / B saw it / only one strategy checked | Stage 6 first; B's history; script checks all strategies |
| In Discover "too early" | article pre-existed via RSS (born active) | `--stage 3` `sourceType` |

Happy path (1–8) ≈ 10 minutes at `RWE_POLL_INTERVAL=60`; all 12 stages ≈ 45 minutes.
