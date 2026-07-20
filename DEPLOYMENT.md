# Deployment Guide

How to run the complete Information Health system — the **Next.js web app** and the
**FastAPI engine** — locally, switch between dataset profiles, and package it for
deployment. No feature configuration here; just running and shipping what exists.

## Architecture in one picture

```
Browser ──▶ Next.js web app ──▶ /api/* route handlers ──▶ FastAPI engine ──▶ real algorithms
 :3000       (BFF + proxy)        (lib/backend.ts)          :8000            (rwe, health_report,
                                                            /docs             narrate_report)
```

- The web app never calls the engine from the browser; its own `/api/*` routes proxy to it.
- In **development** those routes fall back to mock JSON if the engine is down; in
  **production** (`NODE_ENV=production`) they return a typed `503` instead of fabricated data.
- The engine picks its data source from a **dataset profile** (below) — configuration only.

## Quick demo in Google Colab (zero local setup)

The fastest way to *see* it running is [`deploy/information_health_colab.ipynb`](deploy/information_health_colab.ipynb):
open it in Colab and **Run all**. It clones the branch, starts the FastAPI engine + the Next.js
app, and prints a public URL — the onboarding → **Initial Information Health Estimate** flow
works with **no credentials** (Google sign-in is an optional cell). One click:

[**▶ Open in Colab**](https://colab.research.google.com/github/greenwichg/random_walks_with_erasure/blob/c41d26fccfa261f7b23a0666d3fa1756f3345f85/deploy/information_health_colab.ipynb)

Colab is for demos only: the runtime idles out and the tunnel URL changes each session. For
anything real, use the two-process setup below (or Docker).

## Prerequisites

- **Python** ≥ 3.9 (3.11 recommended) for the engine.
- **Node** ≥ 18.17 (20/22 fine) for the web app.
- Optional: **Docker** (24+) with Compose v2 for the packaged deployment.
- Optional: **`ANTHROPIC_API_KEY`** (or `GEMINI_API_KEY`) for the live AI‑coach narrative.
  Without a key the coach still works, using a deterministic grounded reply.

---

## Run locally (two processes)

### 1 · Engine (FastAPI)

```bash
# from the repo root
pip install -e ".[serve]"            # rwe + fastapi + uvicorn (once)
python examples/api_fastapi.py       # synthetic data, no downloads → http://127.0.0.1:8000
```

Verify: `curl -s localhost:8000/api/health` → `{"ok": true, ...}`, and open
**http://localhost:8000/docs** for the interactive OpenAPI docs.

### 2 · Web app (Next.js)

```bash
cd web
npm install
echo "RWE_BACKEND_URL=http://127.0.0.1:8000" > .env.local
npm run dev                          # → http://localhost:3000
```

Open **http://localhost:3000**. The Report, Recommendations, and AI Coach pages now serve
real engine output; the remaining pages serve mock data until Phase 3.

> Stop the engine and the dev app transparently falls back to mock — handy for frontend
> work. Set `RWE_ALLOW_MOCK_FALLBACK=false` to force the production behaviour locally.

---

## Dataset profiles

The engine switches data sources by **configuration only** — a CLI flag or an environment
variable, no code change. `synthetic` needs nothing; the others need a data file.

| Profile | Data source | Run it | Data prep |
| --- | --- | --- | --- |
| `synthetic` *(default)* | the repo's own simulator | `python examples/api_fastapi.py` | none |
| `qbias` | synthetic readers over a real Qbias AllSides catalog | `… --profile qbias --qbias allsides_balanced_news.csv` | download the Qbias CSV |
| `mind` | an ingested MIND release (news) | `… --profile mind --npz mind.npz` | `ingest_mind.py` (below) |
| `politosphere` | an ingested Reddit Politosphere (reddit) | `… --profile politosphere --npz politosphere.npz` | `ingest_politosphere.py` (below) |

Equivalent via environment (what deployments use):

```bash
RWE_PROFILE=mind RWE_NPZ=mind.npz python examples/api_fastapi.py
```

**Producing the `.npz` files** (MIND and Politosphere are external, licensed datasets — you
supply the raw data):

```bash
# MIND → mind.npz  (see the ingest_mind.py header for ideology/lean options)
python examples/ingest_mind.py --mind-dir data/MINDsmall_train --out mind.npz

# Reddit Politosphere → politosphere.npz
python examples/ingest_politosphere.py --comments-dir data/politosphere --out politosphere.npz
```

The web app is **identical** across profiles — it only ever sees the JSON contract, so
switching data never touches the frontend.

---

## Configuration reference

**Engine** (`examples/api_fastapi.py`) — CLI flag or env var (CLI > env > profile default):

| Env var | Flag | Meaning |
| --- | --- | --- |
| `RWE_PROFILE` | `--profile` | `synthetic` \| `qbias` \| `mind` \| `politosphere` |
| `RWE_NPZ` | `--npz` | dataset file for `mind` / `politosphere` |
| `RWE_QBIAS` | `--qbias` | Qbias AllSides CSV for the `qbias` profile |
| `RWE_DOMAIN` | `--domain` | `news` \| `reddit` (set by the profile) |
| `RWE_REGISTER_CSV` / `RWE_EMOTION_CSV` / `RWE_BEHAVIORS` | `--register-csv` / `--emotion-csv` / `--behaviors` | optional enrichment for `.npz` profiles |
| `RWE_LEAN_TAU` | `--lean-tau` | lean‑axis centre half‑width (default = engine `LEAN_TAU`) |
| `RWE_N_USERS` / `RWE_MAX_ITEMS` / `RWE_SEED` | `--n-users` / `--max-items` / `--seed` | synthetic corpus size + seed |
| `RWE_PROVIDER` | `--provider` | coach LLM provider: `anthropic` \| `gemini` |
| `RWE_LOG_LEVEL` | — | log level for structured logs (default `INFO`) |
| `RWE_ENV` | — | `production` turns on **fail-closed auth**: the engine requires `RWE_INTERNAL_SECRET` and refuses to start without it. Unset = local dev (trust local callers). |
| `RWE_REQUIRE_AUTH` | — | force fail-closed auth on/off independently of `RWE_ENV` (`1`/`0`); defaults to whatever `RWE_ENV` implies |
| `RWE_INTERNAL_SECRET` | — | shared secret authenticating the web tier's server-to-server calls. Unset = trust local callers (dev only); **required** in production. |
| `RWE_RATELIMIT_ENABLED` | — | rate limiting is on by default; set `0`/`false` to disable |
| `RWE_RATELIMIT_<SCOPE>_PER_MIN` | — | override a scope's sustained requests/minute. `SCOPE` ∈ `AUTH` (30), `AI` (15), `INGEST` (60), `WRITE` (60), `READ` (240), `DEFAULT` (120) — production defaults shown; relaxed ×50 outside production |
| `RWE_BODY_LIMIT_<SCOPE>_BYTES` | — | max request body per class: `AUTH` (4 KB), `AI` (16 KB), `INGEST` (1 MB), `WRITE` (32 KB), `DEFAULT` (16 KB) — production defaults; relaxed ×4 outside production. Oversized → `413` |
| `RWE_MAX_READS_PER_BATCH` / `RWE_MAX_URL_LEN` / `RWE_MAX_TITLE_LEN` / `RWE_MAX_TEXT_LEN` | — | ingestion batch-shape caps (default `100` / `2048` / `512` / `2048`); exceeded → `413` |
| `RWE_DB_URL` | — | durable store URL (default `sqlite:///<repo>/data/ih_beta.db`). Production refuses to start on an ephemeral value (in-memory, or a `/tmp` path). |
| `RWE_BACKUP_DIR` | — | where `db_backup.py` writes backups (default: `backups/` beside the DB file) |
| `RWE_CORS_ORIGINS` | — | comma-separated browser origins allowed to call the engine cross-origin. Default: `*` in dev, **none** in production (the engine is internal; the web tier calls it server-to-server). |
| `RWE_RSS_FEEDS` | — | RSS/Atom feeds for `rss_ingest.py` — a feeds file path or a comma-list of `url` / `Name\|url` (see "News ingestion") |
| `RWE_RECS_SOURCE` | — | `feed` sources the recommender's catalog from the RSS `FeedArticle` store (else the static corpus) |
| `RWE_FEED_MIN_ARTICLES` | — | min catalog size before the feed source activates (default `50`; below it, falls back to the static corpus) |
| `RWE_FEED_MAX_AGE_DAYS` | `60` | recommendation-candidate freshness window: articles published (or, undated, fetched) more than this many days ago never enter the recommendation corpus. `0` disables. Composition only — stale articles stay stored and visible to Search/Stories/History. Distinct from `RWE_RETENTION_MAX_AGE_DAYS`, which deletes rows. |
| `RWE_FEED_URL_DATE` | `1` (on) | trust a publication date embedded in the article URL path (`/YYYY/MM/DD/`, `/YYYY/mon/DD/` — 3-letter month, C4.3 —, `/YYYY/MM/`, trailing `-MM-DD-YY`) as the authoritative candidacy age — catches archived items a feed left undated or re-dated (C4.2). `0` disables — the instant rollback to pre-C4.2 candidacy. |
| `RWE_FEED_REQUIRE_DATED` | `0` (off) | require a parseable `publishedAt` for recommendation candidacy — excludes undated items instead of falling back to first-seen/fetch time. Only consulted while the age window is active. |
| `RWE_FEED_CORPUS_CSV` | — | where the `FeedArticle`→qbias export is written (default `data/feed_corpus.csv`) |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | — | enable the live coach narrative |

**Web app** (`web/.env.local`):

| Env var | Default | Meaning |
| --- | --- | --- |
| `RWE_BACKEND_URL` | `http://127.0.0.1:8000` | engine origin the proxy calls |
| `RWE_BACKEND_TIMEOUT_MS` | `6000` | proxy timeout before fallback/error |
| `RWE_ALLOW_MOCK_FALLBACK` | on in dev, off in prod | allow mock when the engine is down |
| `NODE_ENV` | — | `production` disables the mock fallback |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | *(empty)* | Google OAuth client for sign-in (NextAuth) |
| `NEXTAUTH_SECRET` / `NEXTAUTH_URL` | *(empty)* | session-JWT signing secret + this app's canonical URL |
| `RWE_INTERNAL_SECRET` | *(empty)* | shared secret sent as `X-IH-Auth`; must match the engine's (required in production) |
| `RWE_ENV` | *(empty)* | `production` = real deployment: disables the dev demo-login; pair with the engine's `RWE_ENV=production` |
| `RWE_CSP` | *(built-in)* | override the Content-Security-Policy string (build-time; see "Browser security") |
| `RWE_DISABLE_CSP` | *(off)* | `1` removes the CSP header only (escape hatch); other security headers stay |
| `NEXT_PUBLIC_API_BASE_URL` | *(empty)* | advanced: call a different API origin from the browser (folded into CSP `connect-src`) |
| `BETA_ALLOWLIST` | *(empty)* | BA1 invite-only access: comma/newline/`;`-separated approved emails and/or `@domain` entries. In production the sign-in gate is **on** and **fail-closed** — an empty list denies everyone. See `docs/BETA_ACCESS_CONTROL.md`. |
| `BETA_ALLOWLIST_FILE` | *(empty)* | optional file of the same format, appended to `BETA_ALLOWLIST`; re-read per sign-in (edit without a restart) |
| `BETA_ACCESS_ENABLED` | *(prod on)* | `1`/`0` to force the beta gate on/off; defaults to on when `RWE_ENV=production` |

---

## Production build without Docker

> **Fail-closed auth (required in production).** Set `RWE_ENV=production` and a shared
> `RWE_INTERNAL_SECRET` (identical on both services) for any real deployment. In this mode the
> engine authenticates every per-user call and **refuses to start** if the secret is missing —
> so a mis-configured deploy fails loudly instead of silently trusting any caller. Generate the
> secret with `openssl rand -base64 32`. Also keep the engine on a private network (don't expose
> its port publicly); the web app is the only client that should reach it.

```bash
# Engine — a real ASGI server; add workers to scale out (each worker builds its own
# in-memory engine at startup, so size memory accordingly).
pip install -e ".[serve]"
export RWE_ENV=production RWE_INTERNAL_SECRET="$(openssl rand -base64 32)"
python examples/api_fastapi.py --host 0.0.0.0 --port 8000
#   or, multi-worker:  uvicorn examples.api_fastapi:app --host 0.0.0.0 --port 8000 --workers 4

# Web — production Next.js build (mock fallback OFF). RWE_INTERNAL_SECRET must MATCH the engine's.
cd web && npm ci && npm run build
NODE_ENV=production RWE_ENV=production RWE_BACKEND_URL=https://engine.internal \
  RWE_INTERNAL_SECRET="$SAME_AS_ENGINE" NEXTAUTH_SECRET="$(openssl rand -base64 32)" npm start
```

---

## Data durability & backups

All product state — accounts, identities, onboarding, reading history, report snapshots, settings,
API tokens, recommendation events — lives in **one SQLite database** (`RWE_DB_URL`, default
`data/ih_beta.db`). No PostgreSQL, no Redis; simple and file-backed for the first 100 users.

### SQLite settings (pragmas)

Every connection is opened with these pragmas (`examples/store.py` → `SQLITE_PRAGMAS`):

| Pragma | Value | Why |
| --- | --- | --- |
| `journal_mode` | `WAL` | Write-Ahead Logging — readers never block the writer (and vice-versa); clean crash recovery. The durability/concurrency core. |
| `synchronous` | `NORMAL` | The WAL-recommended sync level: durable across an app/OS crash; a sudden power loss may drop only the last un-checkpointed transaction, never corrupting the file. (Far faster than `FULL`.) |
| `busy_timeout` | `5000` ms | Wait for a lock instead of immediately raising `database is locked`. |
| `foreign_keys` | `ON` | Enforce foreign keys (SQLite defaults them **off** per connection). |

WAL adds `-wal` / `-shm` sidecar files next to the database; keep them together.

### Development vs production storage

- **Development / Colab:** the default `data/ih_beta.db` on your local disk (git-ignored) — zero config.
- **Production:** a file on a **persistent volume**. `docker-compose.yml` mounts a named volume
  `ih-data` at `/app/data`, so the DB survives container recreation and redeploys. The engine
  **refuses to start** in production (`RWE_ENV=production`) if `RWE_DB_URL` is clearly ephemeral
  (in-memory, or under `/tmp`) — see "Startup validation".

### Backup & restore

`examples/db_backup.py` uses SQLite's **online backup** (consistent snapshot while the server runs):

```bash
# Consistent, timestamped backup (server can stay up). Writes <db-dir>/backups/ih_beta-<ts>.db
python examples/db_backup.py backup
docker compose run --rm backup            # same thing, against the compose data volume

# Inspect storage + list backups (also served at GET /api/internal/storage)
python examples/db_backup.py status
```

**Restore** validates the backup's integrity *before* touching the live DB, snapshots the current
file to `…​.pre-restore`, then atomically swaps — and refuses (leaving the active DB untouched) if
the backup is corrupt:

```bash
# STOP the engine first, then:
python examples/db_backup.py restore /path/to/ih_beta-<ts>.db
# start the engine again
```

### Disaster recovery

- **Schedule** `db_backup.py backup` from cron/systemd (e.g. hourly). Backups land on the data
  volume; **copy them off-host** (object storage / another machine) so a lost volume ≠ lost data.
- **Corruption check:** `GET /api/internal/storage` (or `db_backup.py status`) runs
  `PRAGMA quick_check`; restore from the newest good backup if it ever reports anything but `ok`.
- **Recovery drill:** `restore` a recent backup into a scratch path and diff — practise before you
  need it.

### Data-loss matrix

| Scenario | Before this milestone | Now |
| --- | --- | --- |
| Process restart | safe (file) | safe |
| Container restart (same container) | safe | safe |
| **Deployment / container recreate** | **DATA LOST** (no volume) | safe (named volume) |
| Host reboot | usually safe | safe |
| Failed / concurrent write | possible `database is locked` | WAL + `busy_timeout` retries |
| Power loss | durable, no concurrency | WAL+`NORMAL`: no corruption (may drop last txn) |
| Volume loss / corruption | unrecoverable | restore from an **off-host** backup |

---

## News ingestion (RSS catalog)

`examples/rss_ingest.py` pulls articles from operator-configured RSS/Atom feeds into a **news
catalog** (`store.FeedArticle`), scoring each through the **same** pipeline the reading path uses
(`ingest.Scorer` + the baseline enricher) and deduplicating by canonical URL. Dependency-free (stdlib
`xml.etree` + `urllib`). It is **ingestion only** — it does not touch the recommendation corpus, the
report, the recommendation algorithms, or the UI; the recommender keeps using the existing corpus.
The catalog is the data foundation a later milestone will draw real-article recommendations from.

```bash
# configure feeds (one per line, `url` or `Name|url`; `#` comments ok) — see deploy/rss_feeds.example.txt
python examples/rss_ingest.py run --feeds deploy/rss_feeds.example.txt
#   or:  RWE_RSS_FEEDS=deploy/rss_feeds.example.txt python examples/rss_ingest.py run
python examples/rss_ingest.py status                 # catalog size + most-recent articles
```

> Note: the one-shot `run` records **no per-feed health** (that is the poller's job — `RWE_FEED_POLL`
> / the multi-source poller), so a corpus-validation read right after a CLI ingest correctly reports
> `healthyFeeds: 0, unhealthyFeeds: 0`. Health rows appear once polling starts.

Schedule `run` from cron/systemd (feeds are operator-configured, not user input, so fetching them is
not a user-facing SSRF surface). Each catalog article preserves the real publisher URL, publisher,
publication timestamp, title, description, and (when the feed carries it) the body.

**Automatic polling (opt-in).** Instead of cron, the engine can keep the catalog fresh itself: set
**`RWE_FEED_POLL=1`** (alongside `RWE_RECS_SOURCE=feed`) and a background thread re-runs the ingest
pipeline every `RWE_POLL_INTERVAL` seconds (default 600). It reuses the exact `rss_ingest` pipeline —
incremental and deduplicated by canonical URL, so it only imports genuinely new articles — isolates
per-feed failures, retries transient fetch errors, logs each cycle (`feed_poll` events), and shuts
down gracefully with the app. It runs standalone too: `python examples/feed_service.py`.

| Env | Default | Meaning |
| --- | --- | --- |
| `RWE_FEED_POLL` | off | enable the background poller (needs `RWE_RECS_SOURCE=feed`) |
| `RWE_POLL_INTERVAL` | `600` | seconds between poll cycles |
| `RWE_POLL_TIMEOUT` | `15` | per-feed fetch timeout (seconds) |
| `RWE_POLL_RETRIES` | `2` | per-feed fetch retries (capped exponential backoff) |
| `RWE_POLL_BACKOFF` | `2` | base seconds for the retry backoff |

Polling keeps the **catalog** current; a *running* engine still serves the recommendation corpus it
built at startup until the validated hot-refresh (a later milestone) swaps it — so today, polling
benefits Discover/Search and the next restart's corpus, not the live recommendation set.

**Multi-source ingestion (RSS + NewsAPI + GDELT).** Ingestion is a **pluggable adapter layer**
(`examples/sources.py`). Every source — RSS/Atom, NewsAPI, GDELT, and future providers — normalizes its
data into the **one** `rss_ingest.FeedEntry` shape and terminates at the existing `ingest_entries`
pipeline; after that boundary the whole platform (scoring, canonical-URL dedup, media selection,
persistence, Search, Discover, Stories, Story Intelligence, recommendations, hot refresh, retention)
behaves **exactly as for RSS and never learns where an article came from.** A `SourceRegistry` holds the
adapters and a `MultiSourcePoller` runs one background thread **per enabled adapter**, each on its own
interval, isolated (one source's outage can't stop another). `FeedPoller` is unchanged (standalone CLI
still uses it). New providers register in one place with no poller change.

* **Deduplication is cross-source** — the same canonical publisher URL from RSS *and* NewsAPI *and*
  GDELT collapses to **one** `FeedArticle` (metadata merged additively; URL-only, never title/semantic).
* **Media priority** — when several sources supply an image for one URL, the higher-priority source
  wins: `SOURCE_PRIORITY = {rss:100, newsapi:80, gdelt:60}` (env `RWE_SOURCE_PRIORITY`). Precedence is
  derived from each row's `source_type` at merge time — nothing extra is persisted, so priorities change
  in one place with no migration. `media.py` selection is unchanged; images are never downloaded.
* **Per-source health** — each adapter reports under a stable key (`https://…` per RSS feed,
  `newsapi://top-headlines`, `gdelt://doc`) via the existing `feed_health` table (availability, latency,
  new/duplicates, staleness). Health is never combined across sources.
* **Per-source quotas** — `RWE_{RSS,NEWSAPI,GDELT}_MAX_ARTICLES` cap a batch **before** ingestion; they
  never touch retention, validation, or recommendations.
* **New columns (additive, idempotent `ALTER`)** — `source_type`, `source_provider`, `external_id` on
  `feed_articles`; all existing APIs stay backward compatible (legacy rows keep `NULL`).

| Env | Default | Meaning |
| --- | --- | --- |
| `RWE_RSS_ENABLED` | `RWE_FEED_POLL` | enable the RSS adapter (defaults to the existing poll flag) |
| `RWE_RSS_MAX_ARTICLES` | — | cap entries per RSS feed per cycle |
| `RWE_NEWSAPI_ENABLED` | off | enable the NewsAPI adapter (also needs a key) |
| `RWE_NEWSAPI_API_KEY` | — | NewsAPI key (sent as `X-Api-Key`; required) |
| `RWE_NEWSAPI_POLL_INTERVAL` | `900` | seconds between NewsAPI polls |
| `RWE_NEWSAPI_MAX_ARTICLES` | — | cap articles per NewsAPI poll |
| `RWE_NEWSAPI_ENDPOINT` / `_QUERY` / `_CATEGORY` / `_COUNTRY` / `_LANGUAGE` | `top-headlines` / … | NewsAPI query shape |
| `RWE_GDELT_ENABLED` | off | enable the GDELT adapter (keyless) |
| `RWE_GDELT_POLL_INTERVAL` | `900` | seconds between GDELT polls |
| `RWE_GDELT_MAX_ARTICLES` | — | cap articles per GDELT poll |
| `RWE_GDELT_QUERY` | `(politics OR economy OR election OR climate OR world) sourcelang:english` | GDELT DOC 2.0 query (topic keywords — a bare `sourcelang:english` returns non-news results) |
| `RWE_SOURCE_PRIORITY` | `rss:100,newsapi:80,gdelt:60` | media-merge source priority |

**Production gaps (multi-source):** live NewsAPI/GDELT polling requires outbound network to
`newsapi.org` / `api.gdeltproject.org` (allowlist them) plus a NewsAPI key; the free NewsAPI tier is
rate-limited, non-commercial, and truncates `content`; GDELT is keyless but rate-limited and returns
sparse bodies. Cross-source dedup is **canonical-URL only** — the same story under a syndicated/AMP/
redirect URL that canonicalizes differently lands as two rows. A declared image URL with no recognizable
image extension (some NewsAPI/GDELT images) is stored as `null` rather than guessed (media.py detects by
extension/MIME and never probes the network). Multi-source polls **serialize on a shared write lock** for
SQLite safety; parallel fetch is a future optimization.

**Retention (validation-aware).** To stop the catalog growing without bound, set a retention policy —
after each poll cycle it prunes old/excess `FeedArticle` rows. It prunes **only** `feed_articles`
(reads, dashboard history, analytics, health reports, and recommendation events are separate,
user-keyed tables with no foreign key to it, so they are never affected). Crucially it is
**validation-aware and monotonic**: it computes the raw age/count prune set, then *retains older
articles* until the kept catalog still meets the floors a healthy replacement corpus needs — so it
can never prune the catalog into a state from which no healthy corpus could ever be rebuilt. If the
feeds never supplied enough diversity to meet a floor, it keeps everything relevant (best effort) and
logs it. Runs standalone too: `python examples/corpus_health.py`.

| Env | Default | Meaning |
| --- | --- | --- |
| `RWE_RETENTION_MAX_AGE_DAYS` | off | prune articles older than this many days |
| `RWE_RETENTION_MAX_COUNT` | off | keep at most this many articles (newest first) |
| `RWE_CORPUS_MIN_ARTICLES` | `RWE_FEED_MIN_ARTICLES` (50) | floor: never prune below this total |
| `RWE_CORPUS_MIN_PUBLISHERS` | `0` | floor: keep ≥ this many distinct publishers |
| `RWE_CORPUS_MIN_PER_BUCKET` | `0` | floor: keep ≥ this many per left / center / right |
| `RWE_CORPUS_MIN_FRESH` | `0` | floor: keep ≥ this many fresh articles |
| `RWE_CORPUS_FRESH_MAX_AGE_DAYS` | `3` | what counts as "fresh" |

Retention runs only when `RWE_RETENTION_MAX_AGE_DAYS` or `RWE_RETENTION_MAX_COUNT` is set; the
`RWE_CORPUS_MIN_*` floors are the same thresholds the corpus-validation gate (see below) enforces, so
retention never removes what validation will need.

**Feed health monitoring (observational).** When polling, the engine persists a per-feed health +
quality record (table `feed_health`) each cycle — availability (healthy · consecutive failures ·
last success/failure · last error · last + average latency) and quality (imported · duplicate ·
rejected · missing-metadata counts · newest/oldest article dates). A feed is marked **unhealthy**
after `RWE_FEED_UNHEALTHY_AFTER` consecutive failures (default 3) and **auto-recovers** on its next
successful poll; the poller keeps polling unhealthy feeds so they rejoin automatically. Partial
failures never stop the cycle.

**Freshness / staleness** is a **separate axis from availability**: `/api/internal/feeds` also reports
`stale` + `newestAgeDays` against `staleThresholdDays`. A feed is **stale** when its newest published
article is older than `RWE_FEED_STALE_DAYS` (default 30; `0`/negative disables). This catches a
**retired/frozen feed that still responds but only serves old content** — it polls fine (`status:
healthy`) yet is flagged `stale: true` (e.g. a legacy `rss.cnn.com` feed stuck on 2023 articles). A
feed with no dated article is not stale (unknown, not old). Staleness is computed at read time from the
already-tracked newest-article date; it is **observational** — a stale feed keeps being polled so it can
recover automatically, and it is **not** excluded from the recommendation corpus (that decision stays
with Corpus Validation). **All of feed health is strictly observational** — it never removes articles,
modifies `FeedArticle`, or influences corpus construction or recommendations; a future Corpus Validation
milestone consumes it. Operators read it at:

```
GET /api/internal/feeds        # per-feed status/latency/quality — trusted (internal secret in prod)
```

| Env | Default | Meaning |
| --- | --- | --- |
| `RWE_FEED_UNHEALTHY_AFTER` | `3` | consecutive failures before a feed is marked unhealthy |
| `RWE_FEED_WARN_AFTER` | `1` | consecutive failures before a feed reads as "degraded" (diagnostics only) |
| `RWE_FEED_STALE_DAYS` | `30` | newest-article age (days) beyond which a feed reads as `stale` (diagnostics only; `0`/negative disables) |

**Corpus validation (candidate-corpus eligibility gate).** The engine can answer *"is the current
catalog healthy enough to become a recommendation corpus?"* without touching the live one. It builds a
**candidate** — a publisher-capped, newest-first **subset of `FeedArticle`** (never a copy; no article
is modified, duplicated, synthesized, re-titled, re-dated, or re-URL'd) — measures it, and reports
whether it *would* be eligible to activate, with every failing reason. **This milestone is validation
only:** it never rebuilds the `Backend`, activates a corpus, or performs a hot swap (that is a separate
milestone); the live recommendations are unaffected. It is read-only, never throws (an unexpected error
returns an *ineligible* result — fail-closed), and imports no recommendation code, so it cannot change
ranking, scoring, selection, or serialization. Runs standalone too: `python examples/corpus_validation.py`.

```
GET /api/internal/corpus       # candidate eligibility + metrics/failures — trusted (internal secret in prod)
```

The floors above (`RWE_CORPUS_MIN_*`, shared with retention) plus these ceilings define eligibility;
every check is independent and off by default (`0` / unset). Percentages are on a 0–100 scale.

| Env | Default | Meaning |
| --- | --- | --- |
| `RWE_CORPUS_MAX_PER_PUBLISHER` | off | ceiling + candidate cap: max articles from one publisher (newest kept) |
| `RWE_CORPUS_MAX_BUCKET_PERCENT` | off | ceiling: max share of any one political bucket (viewpoint balance) |
| `RWE_CORPUS_MAX_ARTICLE_AGE_DAYS` | off | ceiling: the newest article must be within this many days (staleness) |
| `RWE_CORPUS_MAX_DUPLICATE_PERCENT` | off | ceiling: max duplicate share |
| `RWE_CORPUS_MAX_MISSING_METADATA_PERCENT` | off | ceiling: max share missing a title or publication date |
| `RWE_CORPUS_REQUIRE_HEALTHY_FEEDS` | off | require zero unhealthy feeds (else ineligible — fail-closed) |

**Atomic hot refresh (activation).** With polling on, a validated corpus is activated **without a
restart**. After each cycle that changes the catalog, the engine rebuilds a `Backend` from the
validated candidate **entirely in the background** (in the poller thread — never on a request), runs
sanity checks, and swaps a single pointer so new requests immediately use it while in-flight requests
finish on the old one. The `Backend`, the personalizer, and the URL resolver swap **together**, so a
reader never mixes generations and Read Article keeps opening canonical publisher URLs. Activation
happens **only** when validation passed, the build succeeded, the resolver attached, and the built
item count matched the validated candidate; otherwise the current `Backend` keeps serving and the next
cycle retries. An unchanged catalog rebuilds nothing. The engine, ranking, scoring, selection, and
serializers are untouched — only the *data source* behind them is replaced. Operators watch it at:

```
GET /api/internal/refresh      # generation, timings, last success/failure — trusted (internal secret in prod)
```

The active corpus `generation` also appears on `GET /api/health` (`recommendationSource.generation`)
for a one-request check. No new configuration — it rides on `RWE_RECS_SOURCE=feed` + `RWE_FEED_POLL`.

**Live search & discovery.** Discover and the ⌘K search are backed by a live search **directly over
the `FeedArticle` catalog** — it queries the catalog in SQL and **never touches the recommendation
engine**. Results reuse the exact Article shape, so Read Article opens the canonical publisher URL and
the extension → reads → Dashboard/History/Analytics/Health flow is identical to recommendations.
Discover now shares the same `search_feed_articles()` path (one filtering implementation). Additive
indexes on `publisher`, `published_at`, `source_feed`, and an expression index on the JSON lean are
created idempotently at startup (existing DBs are upgraded in place, never rebuilt).

```
GET /api/search?query=climate&lean=left&topic=Politics&dateFrom=2026-07-01&sort=newest&limit=24&offset=0
```

Supports text (title / description / publisher / topic), publisher / lean / topic / date-range / source
filters, `newest` | `oldest` | `publisher` sort (`relevance` reserved), and `limit` / `offset` paging —
returning `{results, total, page, pageSize, hasMore, remainingPages}`. Pass `debug=1` (or set
`RWE_SEARCH_DEBUG`) to include `queryMs` and an `ftsAvailable` probe (FTS5 is **detected but not used
yet** — search is LIKE-based; FTS is a future optimization).

**Story clustering & the Story Service.** Discover and Stories are both backed by one **Story Service**
(`examples/story_service.py`) that clusters the live `FeedArticle` catalog into news events with a
deterministic, dependency-free algorithm (`examples/clustering.py` — union-find over headline Jaccard
similarity within a time window; **no LLM**). It is the single owner of Story construction — Discover
and Stories never build a Story independently — and it never touches the recommender.

```
GET /api/stories?topic=Politics&lean=left&sort=top&limit=24&offset=0    # paginated Story envelope
GET /api/story/{storyId}       # one Story  (backward-compatible alias: GET /api/stories/{storyId})
```

Each Story carries its cross-publisher coverage (every article opening its canonical publisher URL —
the identical Read flow), a publication window + timeline, publisher list/count/diversity, L/C/R
**coverage** (not opinion) with the blind-spot side, and a nullable `{image, imageSource,
imageAttribution}` contract a future enrichment step can populate without an API change. Story IDs are
**stable across rebuilds as coverage grows** (anchored to the event's representative article), so a
Story URL keeps working as more outlets cover the event. Supports topic/publisher/lean/date filters,
`top` | `latest` | `oldest` | `publishers` sort, and `limit`/`offset` paging. Pass `debug=1` (or set
`RWE_STORIES_DEBUG`) for `clusterMs` + cluster diagnostics (story count, average + largest cluster,
size distribution). Clustering is **O(n²)** over the bounded scan (~1 s at ~1,800 articles); a
per-topic/day pre-bucket or a poll-driven cache is the next optimization. `/api/discover` (article
search) remains for backward compatibility and provides the filter facets.

**Media enrichment (rich cards).** RSS ingestion extracts image metadata — `media:content`,
`media:thumbnail`, `enclosure`, and Atom image links — and stores it on `FeedArticle` (additive columns
`image` / `imageWidth` / `imageHeight` / `imageMimeType` / `imageSource` / `imageAttribution`, added in
place on existing DBs). **Metadata only — no image is ever downloaded and no Open Graph is fetched.**
All selection is centralised in `examples/media.py` (`pick_best_image`, `pick_article_media`,
`pick_story_hero`, `pick_best_logo`), so Discover, Search, Story heroes, and recommendation cards reuse
one implementation. A Story's hero is chosen **representative → highest-quality → most-recent → null**;
publisher logos derive from the publisher's own-domain favicon (privacy-preserving — no third party; a
curated override map with dark-mode variants is the extension point). Recommendations (whose corpus
carries no media) are enriched from the live `FeedArticle` catalog **after** the protected serializer
runs, so no recommendation logic changes. Cards render a lazy `<img>` when media is present and fall
back to the existing text-only layout otherwise — native browser caching only, image URLs canonical.
Media survives polling (backfilled on re-poll), retention (whole-row deletes), and hot refresh.

**Story Intelligence.** Deterministic intelligence computed **on top of** a Story
(`examples/story_intelligence.py`) — freshness, lifecycle, momentum, coverage statistics, an expanded
timeline, "new since your last visit", and informational alerts. It is a strict **consumer** of the
Story Service (dependency graph `FeedArticle → Story Service → Story Intelligence`, never the reverse):
it reads a Story's existing fields + the reader's existing browser-extension reads and derives
everything with no clustering, no recommender, no LLM, and **no new read tracking**. Read-only — it
changes no recommendation, report, or read.

```
GET /api/stories …             # every Story now also carries a lightweight { freshness, lifecycle } badge
GET /api/story/{storyId}/intelligence   # full intelligence for one event (reader-aware when signed in)
```

`/api/stories` attaches the cheap **freshness (band + score) + lifecycle** summary to every card, so
Story lists badge without an extra request. The detail endpoint adds **momentum** (Growing / Stable /
Declining), **coverage statistics** (velocity, growth, publisher distribution), an **expanded
timeline** (first report, publisher joins, article-count milestones, the latest update, and a
**Perspective Expansion** event when coverage broadens to a new political side), **coverage alerts**,
and **`newSinceLastVisit`** — for a signed-in reader, what's new since they last read this event
(baseline = their most recent `observedAt` among reads belonging to the Story; both `lastVisited` and
`lastUpdated` are returned so the UI can explain the comparison). Anonymous requests get an empty
`newSinceLastVisit`. Everything is deterministic and O(coverage); typical clustered stories are small,
so per-card summary cost is microseconds (a 200-article worst-case story computes full intelligence in
~1.5 ms). Thresholds are configurable (hours; article counts):

| Variable | Default | Meaning |
| --- | --- | --- |
| `RWE_STORY_BREAKING_HOURS` | `3` | latest coverage within this age (+ a ≥2-article burst) reads as **Breaking** |
| `RWE_STORY_DEVELOPING_HOURS` | `12` | freshness **Developing** ceiling |
| `RWE_STORY_ACTIVE_HOURS` | `48` | freshness **Active** ceiling (also the score half-life) |
| `RWE_STORY_COOLING_HOURS` | `168` | freshness **Cooling** ceiling; older is **Archived** |
| `RWE_STORY_MOMENTUM_WINDOW_HOURS` | `24` | recent-vs-prior comparison window for momentum + growth |
| `RWE_STORY_MILESTONES` | `2,5,10,20` | article-count milestones emitted on the timeline |

**URL coverage by ingestion source** — which sources can populate a real publisher `url` today:

| Source | Real URL? | Notes |
| --- | --- | --- |
| Browser extension | ✅ | captures the canonical article URL on recognized publisher domains |
| Pasted URL (`/api/me/reads`) | ✅ | the reader supplies a real URL |
| **RSS ingestion** (this milestone) | ✅ | preserves the feed's real publisher article URL |
| Qbias corpus | ❌ | dataset columns are `title,tags,heading,source,text,bias_rating` — no URL |
| MIND corpus | ❌ | ships MSN-aggregator URLs (not publisher article URLs); not carried into the corpus |
| Synthetic corpus | ❌ | fully generated tokens (e.g. `S144`) — no real articles |

*Future work for full coverage:* point the recommender / discover surface at the `feed_articles`
catalog (which carries real URLs), at which point the approved **Honest URL Pass-through** on
recommendations becomes live end-to-end (Read → record opened → open the real publisher URL →
extension captures the read → Dashboard/History/Analytics/Open-Mindedness update).

### Live recommendation source (opt-in)

Set **`RWE_RECS_SOURCE=feed`** and the engine builds the recommender's catalog from the ingested
`FeedArticle` store instead of the static corpus. It does this by exporting the catalog to a
qbias-format CSV that the **existing, unchanged** corpus machinery (`simulate_users.run(qbias=…)`)
reads — so the recommender, health metrics, diversity, and personalization operate over live
articles **exactly** as they do over the static qbias catalog (no algorithm, engine, or simulator
change). It requires at least `RWE_FEED_MIN_ARTICLES` (default 50) in the catalog; below that it
**falls back** to the existing corpus, so enabling it before any RSS ingest is safe.

> Recommendation item ids remain the qbias-style synthetic ids, but each feed-sourced recommendation
> now **also carries the real publisher `url`** (the engine maps `Q{i}` → the FeedArticle URL via the
> exported CSV and adds it to the article payload only when verified — never fabricated). This is the
> **Honest URL Pass-through**: the recommendation Read button records the open and then opens the real
> article, so the browser extension captures the read and Dashboard / History / Analytics / Health /
> Open-Mindedness update through the existing ingestion pipeline. A recommendation without a URL (the
> static/synthetic corpus) records the open but opens nothing — current behaviour, no broken link.

**Docker Compose wires this up automatically.** `deploy/docker-compose.yml` includes a one-shot
`ingest` service that fetches the feeds in `deploy/rss_feeds.example.txt` into the shared catalog
**before** the engine starts (`api` `depends_on` it), and sets `RWE_RECS_SOURCE=feed` on the engine —
so a fresh `docker compose up` serves URL-carrying recommendations out of the box. The ingest exits
cleanly even if a feed is unreachable (`|| true`), so it never blocks the deploy; below
`RWE_FEED_MIN_ARTICLES` the engine falls back to the static corpus. Edit the feeds file to change the
mix, or comment out `RWE_RECS_SOURCE` to always use the static corpus. The Colab notebook
(`deploy/information_health_colab.ipynb`) has an equivalent optional **“Live RSS feed”** cell.

---

## Startup validation (fail fast)

Both tiers validate configuration at startup when **production mode** is on (`RWE_ENV=production`)
and **refuse to boot** rather than come up mis-configured and fail at the first request. Production
mode is the cross-tier switch `RWE_ENV=production` — deliberately *not* `NODE_ENV`, so the Colab
demo (which serves a production build) and local dev stay zero-config.

- **Engine** (`examples/api_fastapi.py`) exits with a diagnostic and status `2` if production mode
  is set without `RWE_INTERNAL_SECRET`, or with an in-memory `RWE_DB_URL` (data would vanish on
  restart). Enforced for both the `python examples/api_fastapi.py` and `uvicorn …:app` entrypoints.
- **Web** (`instrumentation.ts`, run at server boot) exits before serving if any of these is missing
  in production: `NEXTAUTH_SECRET`, `RWE_INTERNAL_SECRET`, `RWE_BACKEND_URL`, and a complete Google
  OAuth setup (`GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` + `NEXTAUTH_URL`) — the dev demo-login is
  disabled in production, so OAuth is the only sign-in method. Outside production nothing is fatal;
  a half-configured OAuth pair is surfaced as a warning.
- **Extension** validates its stored config and, on install, opens its Options page and shows a
  persistent toolbar badge (with an explanatory tooltip) until the app URL + API token are set — an
  unconfigured extension never silently no-ops.

```
$ RWE_ENV=production python examples/api_fastapi.py
==========================================================================
FATAL: refusing to start — invalid configuration (1 problem(s)):
  ✗ Production mode is enabled ... but RWE_INTERNAL_SECRET is not set. ...
==========================================================================
```

---

## Browser security (headers, CORS, CSP, cookies)

The browser-facing **web tier** sets security headers via `next.config.mjs` → `securityHeaders()`
(`web/lib/security-headers.mjs`). Because Next serializes `headers()` at **build time**, run
`next build` in the target mode: a production build (`NODE_ENV=production`) bakes in the strict CSP
and HSTS; `next dev` evaluates live and relaxes the CSP for HMR.

**Pages + static assets** get: `Content-Security-Policy` (`default-src 'self'`; `frame-ancestors
'none'`; `object-src 'none'`; `script-src`/`style-src 'self' 'unsafe-inline'` for Next's inline
hydration; `connect-src 'self'`), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy` (camera/mic/geo/topics
denied), `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-origin`, and
`Strict-Transport-Security` (production builds only).

**Authenticated APIs** (`/api/*`) get `Cache-Control: no-store` + `nosniff` — and deliberately **no**
`Cross-Origin-Resource-Policy`, so the browser extension's privileged cross-origin `fetch` to
`/api/me/reads` is never blocked.

- **Compatibility (verified):** headless Chromium loads the pages with **zero CSP violations**;
  Google OAuth is a top-level redirect (not an embed), so no google origins are needed; the
  extension reaches the app via `host_permissions` (privileged fetch, exempt from page CORS/CSP).
  A nonce-based CSP (dropping `'unsafe-inline'`) is future work; `RWE_CSP` / `RWE_DISABLE_CSP` are
  the override / escape hatch.
- **Engine CORS:** `*` in dev, **locked** in production (the engine is internal — the web tier calls
  it server-to-server, which isn't subject to CORS). Set `RWE_CORS_ORIGINS` to allow specific
  browser origins. Engine JSON responses also carry `nosniff` + `no-referrer` + `no-store`.
- **Cookies:** the NextAuth session cookie is `HttpOnly` + `SameSite=Lax` (Lax is required so the
  OAuth callback receives it), and `Secure` with a `__Secure-` prefix when `NEXTAUTH_URL` is https
  (required in production — see "Startup validation"). These are NextAuth defaults; keep
  `NEXTAUTH_URL` on https in production so `Secure` is applied.

---

## Package with Docker

Ready-to-use artifacts live in `deploy/`:

- `deploy/Dockerfile.api` — the FastAPI engine (`python:3.11-slim`, `pip install ".[serve]"`).
- `deploy/Dockerfile.web` — the Next.js app (`node:20-slim`, `npm ci && npm run build`).
- `deploy/docker-compose.yml` — both services wired together, `web → api`.
- `.dockerignore` — keeps the build context small.

```bash
# from the repo root — builds both images and starts the system
docker compose -f deploy/docker-compose.yml up --build
# → web  http://localhost:3000
# → api  http://localhost:8000/docs
```

**Switch datasets in Compose** — uncomment the relevant lines in `docker-compose.yml`
(`RWE_PROFILE`, `RWE_NPZ`/`RWE_QBIAS`) and the `./data:/data:ro` volume, then place your
`.npz`/CSV under `./data`. No image rebuild is needed to change profiles.

Build the images individually if you deploy them separately (e.g. to a registry):

```bash
docker build -f deploy/Dockerfile.api -t ih-api .
docker build -f deploy/Dockerfile.web -t ih-web .
```

For a single-host or platform deployment (Fly, Render, Cloud Run, ECS): deploy the two
images as two services, set `RWE_BACKEND_URL` on the web service to the engine's URL, set
`NODE_ENV=production`, and mount/attach any dataset the engine profile needs.

---

## Health & observability

- **Readiness / liveness:** `GET /api/health` → `200` with the active profile and reader
  counts (the Compose file uses it as a healthcheck; wire it to your platform's probes).
- **Logs:** the engine emits one structured JSON line per request
  (`{"event":"request","method","path","status","durationMs","requestId"}`) plus a `startup`
  line. Set verbosity with `RWE_LOG_LEVEL`.
- **Rate limiting:** a per-process token-bucket limiter protects the engine (no Redis). Each
  request is keyed by the authenticated user (else client IP) and classified into a scope
  (auth / ai / ingest / write / read); over-limit requests get a typed `429` with a `Retry-After`
  header and are logged as `{"event":"rate_limited","scope","identityKind","path","retryAfter"}`.
  Tune per scope with `RWE_RATELIMIT_<SCOPE>_PER_MIN` (see the config reference). Note: limits are
  per engine process, so with `--workers N` the effective ceiling is N× the configured rate.
- **Request-size limits:** each body-bearing endpoint has a per-class byte cap (`RWE_BODY_LIMIT_*`)
  checked against `Content-Length` *before* the body is buffered — an oversized payload gets a typed
  `413` in ~1 ms with no memory allocated — plus batch-shape caps on ingestion (`RWE_MAX_READS_*`).
  The check reads `Content-Length` only, never the body, and logs `{"event":"payload_too_large",
  "contentLength","limitBytes"}` (never the body). A chunked upload that omits `Content-Length`
  bypasses the header check and is bounded only by the model limits — put a hard `client_max_body_size`
  (nginx) / body limit on the fronting proxy or platform LB in production as defense-in-depth.
- **Tracing:** every response carries an `X-Request-ID` (echoing an inbound one if provided),
  and every error body includes `error.requestId` — so a user‑visible failure maps to a log line.

## Notes & caveats

- The **synthetic** profile is a product PoC — generated readers, not real behaviour. It
  exists so the whole stack runs with zero external data; every metric is still computed by
  the real pipeline.
- The **AI coach** returns a deterministic, grounded reply when no LLM key is set; add
  `ANTHROPIC_API_KEY` to enable the generated narrative.
- Recommenders are built **once per engine process** at startup; with `--workers N` that cost
  and memory are paid per worker.
- MIND, Politosphere, and Qbias are **external datasets** with their own licenses/downloads;
  only the synthetic profile ships ready to run.
