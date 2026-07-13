# CLI Commands Reference

> **Scope:** every command-line entry point in the repository (`examples/*.py` and the two
> `python -m` package CLIs), grouped by purpose, with functionality, subcommands, and key flags.
> **Date:** 2026-07-13 · **Branch:** `claude/sleepy-gates-oecof1`

## Conventions

- **Run from the repository root** unless noted: `python examples/<script>.py [args]`.
  Scripts insert `examples/` on `sys.path` themselves, so the repo root is the correct working dir.
- **Every command supports `--help` / `-h`** — that is the authoritative flag list. This document
  summarizes each command's *purpose* and its *most-used* flags/subcommands.
- **Database:** commands that touch the store default to `RWE_DB_URL`, else the repo file
  `data/ih_beta.db` (absolute path). Most accept `--db <url>` to override.
- Two package CLIs also run as modules from `examples/`: `python -m metric_pipeline`,
  `python -m rec_pipeline`. The repo-root-friendly launchers `validate_metrics.py` / `validate_recs.py`
  wrap them and work from anywhere.

---

## A. Product & serving

### `rec_sandbox.py` — Recommendation Evaluation Engine (internal tool)
Evaluate counterfactual corpus compositions against the **unchanged** recommendation engine,
ephemerally and read-only; prints the sectioned investigation report (or raw JSON). No subcommands.

| Flag | Purpose |
|---|---|
| `--db <url>` | store URL (e.g. `sqlite:///data/ih_beta.db`) — **required** |
| `--reader <r>` | `demo` (persisted demo account if present, else synthetic) \| `user:<id>` \| `row:<n>` (repeatable) |
| `--strategy <s>` | `blend` \| `rwe-b` \| `rwe-d` \| `adaptive` (repeatable; default blend) |
| `--params <json>` | JSON hyperparameters, e.g. `{"beta":0.8}` or `{"epsilon":0.9}` (repeatable) |
| `--preset <name>` | append a canned injection scenario (repeatable) |
| `--inject-url / --inject-title / --inject-published / --inject-outlet` | ad-hoc single-article injection |
| `--ask <url/id>` | extra "why (not) this article?" probe (repeatable) |
| `--questions <q>` | restrict computed sections (repeatable; default all) |
| `--compare` | also build a baseline and diff the feeds |
| `--json` | print the raw REPORT CONTRACT v1 JSON (byte-identical to the library) |
| `--spec <file>` | JSON spec file (`-` = stdin); the flags above extend it |
| `--out <file>` | also write the report JSON to a file |

### `rss_ingest.py` — RSS/Atom ingestion (the live-news catalog foundation)
Pull articles from configured RSS/Atom feeds, score each through the same pipeline, and store them
in the `FeedArticle` catalog. **Subcommands:**

| Subcommand | Purpose | Flags |
|---|---|---|
| `run` | fetch + ingest the configured feeds | `--db`, `--feeds <file\|comma-list>` (else `RWE_RSS_FEEDS`) |
| `status` | catalog size + most-recent articles | `--db`, `--limit N` (default 20) |
| `parse <file>` | parse a local feed file offline (testing) | `--limit N` |

Example: `python examples/rss_ingest.py run --feeds deploy/rss_feeds.example.txt`

### `sources.py` — multi-source ingestion (RSS + NewsAPI + GDELT)
One-shot poll across every **enabled** source adapter into the catalog. **Commands:**
`poll` (default — ingest once) · `check` (per-adapter enabled/config status, no ingest). Flag: `--db`.
Example: `python examples/sources.py poll`

### `feed_service.py` — background RSS poller (standalone service)
Runs the poller in the foreground as a sidecar; periodically re-runs the `rss_ingest` pipeline to keep
the catalog fresh. Ctrl-C stops it gracefully. Configured by env (`RWE_DB_URL`, feed-poll settings);
no positional args.

### `api_fastapi.py` — FastAPI serving layer (production re-host)
Serves the engine's JSON API for the Next.js web app. Key flags: `--host`, `--port`, `--profile`
(`synthetic`|`qbias`|`mind`|`politosphere`), `--npz`, `--qbias`, `--n-users`, `--max-items`, `--seed`,
`--domain`, `--provider`, `--model`. Reads the same `RWE_*` env the app uses. Started by the deploy
notebook's cell 2.

### `api_server.py` — dependency-free JSON API (reference backend)
The stdlib-only machine-readable counterpart to `app.py`; same `--profile/--npz/--qbias/--host/--port`
family plus `--register-csv`, `--emotion-csv`, `--behaviors`, `--lean-tau`. Used for local/offline serving.

### `app.py` — thin web UI (demo artifact)
Dependency-free web server rendering the Information Health Report + LLM narrative. Flags: `--npz`,
`--domain`, `--provider`, `--model`, `--host`, `--port`.

---

## B. Ops & maintenance

### `db_backup.py` — backup / restore / status for the SQLite store
Consistent online backup (server can stay up) and safe restore. **Subcommands:**

| Subcommand | Purpose | Flags |
|---|---|---|
| `backup` | create a consistent, timestamped backup | `--db`, `--out <dir>` (else `RWE_BACKUP_DIR`) |
| `status` | storage diagnostics + list backups | `--db`, `--out <dir>` |
| `restore <file>` | restore from a backup (**stop the engine first**) | `--db` |

### `migrate_topics.py` — one-shot topic reclassification
Re-run every stored article through the canonical `ingest.classify_topic`. Flags: `--db`, `--dry-run`
(report changes without writing).

### `corpus_health.py` — catalog health + retention (ops/cron)
Run retention once against the default DB and print a health JSON snapshot (totals, diversity,
duplicates, freshness, age span). Also the shared library other commands import.

### `corpus_validation.py` — catalog→corpus eligibility gate (ops)
Validate the default DB's catalog once and print the eligibility result JSON — "is a candidate set
recommendable?" Also the library the serving path uses.

### `audit_story_coverage.py` — "why is no card a Story Match?"
Mechanically explains Story-Match coverage for a live store. Flags: `--db`, `--user <id>` (default 1),
`--serve` (build the serving stack + per-card diagnostics), `--report` (full feed/story breakdown),
`--list-users` (list users to pick `--user`).

---

## C. Validation & regression (developer tools)

### `validate_metrics.py`  (also `python -m metric_pipeline`) — Metric Validation Pipeline
Independently re-derive the Information-Health metrics and compare against production. Flags:
`--golden <all|name>`, `--history <reads.json>`, `--user <id>` (live via `Store()`), `--report <text|json>`,
`--tol <n>` (default 1e-9), `--drift-threshold <n>`, `--record`, `--history-file <path>`.
Example: `python examples/validate_metrics.py --golden all`

### `validate_recs.py`  (also `python -m rec_pipeline`) — Recommendation Validation Pipeline
Regression-check recommendation behaviour over golden scenarios (Phase 1). Flags: `--scenario <name>`
(repeatable; default all), `--history <reads.json>` (validate your own exported history), `--report <text|json>`,
`--fast` (skip rebuild-based determinism/history-sensitivity checks), `--record`, `--show-history [N]`.
Example: `python examples/validate_recs.py --scenario same_story`

### `extension_experiment.py` — browser-extension E2E probes
The automated half of the extension end-to-end experiment. Flags: `--engine <url>`, `--user <id>`
(demo reader = 1), `--url <article>`, `--stage <2..10>` (default all), `--simulate-read` (submit an
extension-shaped read when no browser is available).

---

## D. Datasets & research experiments (paper + PoCs)

### `ingest_mind.py` — ingest a MIND release → RWE-ready `.npz`
Build the click matrix + ideology from a MIND dataset. Key flags: `--mind-dir`, `--out`, `--source-map`,
`--lean-csv`, `--positions-csv`, `--political-only`, `--ideology` (estimate positions from behaviour,
no outlet lean), `--ideology-iters`, `--sample-users`, `--seed`.

### `ingest_politosphere.py` — ingest Reddit Politosphere → `.npz`
User×subreddit endorsement matrix + ideal-point ideology. Flags: `--comments-dir`, `--pattern`, `--out`,
`--lean-csv`, `--ideology`, `--ideology-restarts`, `--sample-users`, `--limit`, `--seed`.

### `eval_mind.py` — RQ2/RQ3 evaluation driver on a MIND `.npz`
Runs baselines + RWE-B/RWE-D, reports accuracy/diversity with significance. Notable flags: `--npz`,
`--top-k`, `--diversity-k`, `--seeds`, `--sig-ref`, `--epsilon`, `--rweb-max-distance`, `--rwed-beta`,
`--sweep-max-distance`, `--sweep-epsilon`, `--per-user-sig`, `--no-bprmf`.

### `eval_movielens.py` — long-tail (RQ2) replication on MovieLens-1M
Accuracy + long-tail-diversity half of the benchmark (no ideological axis). Flags: `--ratings`, `--out-csv`,
`--seeds`, `--top-k`, `--diversity-k`, `--rwed-beta`, `--rwed-v`, `--rp3-beta`, `--itemknn-k`.

### `demo_movielens.py` — MovieLens-1M long-tail benchmark (paper Result II)
Compares RWE-D against baselines on ML-1M. Flags: `--ratings`, `--seed`.

### `demo_synthetic.py` — synthetic ideology-detection + recommendation demo
Runs two parts (ideology detection, then recommendation) on generated data. No args.

### `demo_agent_sim.py` — agent-based newsfeed browsing simulation
Opposing-viewpoint satisfaction score per user on a polarized graph (step-by-step model). No args.

### `demo_satisfaction.py` — satisfaction-driven adaptive exposure demo
Demonstrates the AdaptiveRWEB feedback loop on top of RWE-B. No args.

### `health_report.py` — Information Health Report (v1) over a MIND `.npz`
Descriptive consumption profile per user. Flags: `--npz`, `--user`, `--sample`, `--min-clicks`,
`--min-political`, `--top-n`, `--html`, `--register-csv`, `--emotion-csv`, `--confidence-csv`,
`--behaviors`, `--require-political`, `--domain`, `--population`, `--subject-label`, `--axis-note`.

### `narrate_report.py` — LLM narrative over a health report
Turns the deterministic metrics into a plain-language narrative (grounded). Flags: `--npz`, `--domain`,
`--user`, `--provider` (`gemini` free / `anthropic` paid), `--model`, `--recs`, `--min-clicks`.

### `plot_axis.py` — plot users/items on the left↔right ideology scale
Loads an ingested `.npz` and renders the axis. Flags: `--npz`, `--out`, `--center` (`0` | `median`).

### `simulate_users.py` — synthetic-user simulator (product PoC)
Generates realistic reading sessions over a real catalog (e.g. Qbias). Flags: `--qbias`, `--n-users`,
`--max-items`, `--sessions`, `--slate-size`, `--seed`, `--out-tag`. (Internal PoC, not research evidence.)

### `satisfaction_probe.py` — measured opposite-perspective engagement (Politosphere)
Feasibility probe: recover a *measured* cross-cutting reception signal from Reddit comments on the
validated axis. Flags: `--comments-dir`, `--npz`, `--sub-tau`, `--user-tau`, `--min-score`, `--limit`, `--out`.

### `adaptive_satisfaction.py` — drive AdaptiveRWEB from the measured probe
Closes the loop: per-user exposure from `satisfaction_probe.py` output instead of a simulated walk.
Flags: `--npz`, `--probe-csv`, `--k`, `--sample`, `--epsilon-low`, `--epsilon-high`.

---

## E. Lean / axis classification & validation toolchain

> How outlet/article ideology is *researched* (the serving path itself uses the `outlet_registry`
> lookup; these are the offline tools that build and validate lean signals).

### `classify_lean.py` — text lean classifier (title + abstract)
Score MIND articles for political lean from text; writes `news_id,position` (+ confidence). Flags:
`--mind-dir`, `--out`, `--model`, `--label-positions`, `--scale`, `--political-only`, `--batch-size`,
`--max-length`, `--limit`.

### `classify_emotion.py` — emotional-tone classifier (attention signal)
Zero-shot emotion buckets per article. Flags: `--mind-dir`, `--out`, `--model`, `--labels`,
`--political-only`, `--batch-size`, `--max-length`, `--limit`.

### `classify_register.py` — news-vs-opinion register classifier (reporting ratio)
Zero-shot "reporting vs opinion" per article. Flags: `--mind-dir`, `--out`, `--model`, `--labels`,
`--political-only`, `--batch-size`, `--max-length`, `--limit`.

### `ensemble_lean.py` — ensemble multiple lean CSVs into one axis
Average independent bias models to reduce noise. Positional: `files` (2+ `news_id,position` CSVs).
Flags: `--out`, `--target-std`.

### `lean_agreement.py` — article-level agreement between two lean models
How much two independent models agree on a single headline. Positional: `files` (2+ CSVs). Flags:
`--band`, `--terciles`, `--out`.

### `validate_lean.py` — how ideological is a lean file vs a reference
Quantify a lean-position file against gold/second model; can emit a labeling template. Flags: `--lean`,
`--against`, `--raters`, `--news-dir`, `--sample`, `--out`.

### `validate_qbias.py` — validate the text-lean classifier vs AllSides gold (QBias)
Score the classifier against AllSides-labeled QBias headlines. Flags: `--csv`, `--lean-csv`, `--model`,
`--label-positions`, `--scale`, `--use-text`, `--max-length`, `--limit`, `--headline-col`, `--text-col`,
`--bias-col`, `--outlet-col`.

### `llm_label.py` — LLM lean labeling (second model, convergent validity)
Label headlines with an LLM as an independent model (**not** a gold set). Flags: `--template`, `--out`,
`--provider` (`gemini` free / `anthropic` paid), `--model`, `--batch`.

### `build_source_map.py` — build `news_id → outlet` source map
From any catalog with a publisher column. Flags: `--catalog`, `--id-col`, `--source-col`, `--out`.

### `resolve_msn_publisher.py` — MIND `news_id → publisher` from MSN snapshots
Parse MSN aggregator pages to recover the original publisher. Flags: `--mind-dir`, `--political-only`,
`--limit`, `--out`, `--lean-csv`, `--url-template`, `--sleep`.

### `prepare_qbias.py` — canonicalize the raw QBias dataset
Normalize QBias outlet labels for the Qbias reference profile. Flags: `--in`, `--out`, `--report`,
`--registry`, `--no-enrich`, `--register-out`, `--emotion-out`.

---

## Quick index

| Command | One line |
|---|---|
| `rec_sandbox.py` | evaluate recommendations/corpus counterfactually (read-only) |
| `rss_ingest.py` | ingest RSS/Atom into the catalog (`run`/`status`/`parse`) |
| `sources.py` | multi-source one-shot ingest (`poll`/`check`) |
| `feed_service.py` | background RSS poller service |
| `seed_demo_reader.py` | seed / re-seed the demo reader's reads |
| `api_fastapi.py` | FastAPI production serving layer |
| `api_server.py` | dependency-free JSON API |
| `app.py` | thin web UI for the health report |
| `db_backup.py` | backup / restore / status of the store |
| `migrate_topics.py` | one-shot topic reclassification |
| `corpus_health.py` | catalog health + retention (ops) |
| `corpus_validation.py` | catalog→corpus eligibility (ops) |
| `audit_story_coverage.py` | explain Story-Match coverage |
| `validate_metrics.py` | metric validation pipeline (`-m metric_pipeline`) |
| `validate_recs.py` | recommendation regression pipeline (`-m rec_pipeline`) |
| `extension_experiment.py` | extension E2E probes |
| `ingest_mind.py` | ingest MIND → `.npz` |
| `ingest_politosphere.py` | ingest Reddit Politosphere → `.npz` |
| `eval_mind.py` | RQ2/RQ3 evaluation on MIND |
| `eval_movielens.py` | long-tail replication on MovieLens-1M |
| `demo_movielens.py` | ML-1M long-tail benchmark |
| `demo_synthetic.py` | synthetic ideology + recommendation demo |
| `demo_agent_sim.py` | agent-based browsing simulation |
| `demo_satisfaction.py` | adaptive-exposure feedback demo |
| `health_report.py` | Information Health Report (v1) |
| `narrate_report.py` | LLM narrative over the report |
| `plot_axis.py` | plot users/items on the ideology axis |
| `simulate_users.py` | synthetic-user simulator (PoC) |
| `satisfaction_probe.py` | measured cross-cutting engagement probe |
| `adaptive_satisfaction.py` | drive AdaptiveRWEB from the probe |
| `classify_lean.py` | text lean classifier |
| `classify_emotion.py` | emotional-tone classifier |
| `classify_register.py` | news-vs-opinion register classifier |
| `ensemble_lean.py` | ensemble lean CSVs |
| `lean_agreement.py` | two-model lean agreement |
| `validate_lean.py` | lean-file validation vs reference |
| `validate_qbias.py` | classifier vs AllSides gold (QBias) |
| `llm_label.py` | LLM lean labeling (2nd model) |
| `build_source_map.py` | build `news_id → outlet` map |
| `resolve_msn_publisher.py` | MIND `news_id → publisher` from MSN |
| `prepare_qbias.py` | canonicalize raw QBias |
