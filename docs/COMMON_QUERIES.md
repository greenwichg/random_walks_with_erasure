# Common Queries

> Everyday commands for the local demo: **refresh workflows** (update code, demo history, catalog,
> compare) plus the **common queries** you reach for most (who's in the DB, inspect a reader, explore
> the knobs, back up, verify).
>
> Run every command **from the repository root** (`.../random_walks_with_erasure`).
> Append `--help` to any command for its authoritative, always-current flag list.
> Store commands default to `RWE_DB_URL`, else `data/ih_beta.db`; pass `--db <url>` to override.

---

## Part 1 — Refresh workflows

### 1. Update the code

```bash
git fetch origin claude/sleepy-gates-oecof1 && git reset --hard FETCH_HEAD   # get 7fa0563
```

> `git reset --hard FETCH_HEAD` always moves you to the **current branch tip** (the `7fa0563`
> comment is just illustrative — you'll land on whatever is latest). `data/` is git-ignored, so your
> local database is untouched by the reset.

### 2. Refresh the demo reading history

```bash
python examples/seed_demo_reader.py --reset --random           # DIFFERENT history every run
python examples/rec_sandbox.py --db "sqlite:///data/ih_beta.db" --reader demo
```

> `--reset` clears the demo reader's old reads and `--random` picks a new left-leaning set, so the
> reading history (and the recommendations that follow from it) changes on every run.

### 3. Refresh the news catalog (latest news)

```bash
python examples/rss_ingest.py run --feeds deploy/rss_feeds.example.txt   # pull current RSS into the catalog
python examples/rss_ingest.py status                                     # see catalog size / growth
python examples/rec_sandbox.py --db "sqlite:///data/ih_beta.db" --reader demo
```

> The recommendation feed is drawn from the local catalog, which is a snapshot frozen at your last
> ingest. Re-run `rss_ingest run` to pull current articles, check growth with `status`, then re-run
> the sandbox to see the refreshed feed.

### 4. Compare experiment (inject + baseline diff)

```bash
python examples/rec_sandbox.py --db "sqlite:///data/ih_beta.db" --preset right --reader demo --compare
```

> Injects a canned right-leaning article and diffs the evaluated feed against the baseline
> (§7 Feed Changes: what entered / left / moved).

---

## Part 2 — Common queries

### Who's in the database? (readers you can pass to `--reader user:<id>`)

```bash
python examples/audit_story_coverage.py --db "sqlite:///data/ih_beta.db" --list-users
# --user 1   demo@infodiet.local   Demo Reader   54 reads
```

> The single most useful lookup — it answers "which reader id / account exists, and how many reads
> does it have?" Inline fallback (no extra script):
>
> ```bash
> python -c "import sys;sys.path.insert(0,'examples');import store,sqlalchemy as sa;c=store.Store('sqlite:///data/ih_beta.db').engine.connect();[print(f'user:{r[0]} {r[1]!r} reads={r[2]}') for r in c.execute(sa.text('SELECT u.id,i.provider_account_id,(SELECT COUNT(*) FROM reads WHERE user_id=u.id) FROM users u LEFT JOIN identities i ON i.user_id=u.id ORDER BY u.id'))]"
> ```

### Investigate a specific reader (real or synthetic)

```bash
python examples/rec_sandbox.py --db "sqlite:///data/ih_beta.db" --reader user:1     # a real stored reader
python examples/rec_sandbox.py --db "sqlite:///data/ih_beta.db" --reader row:3      # a synthetic corpus reader
```

### Raw JSON (pipe / save / diff the report)

```bash
python examples/rec_sandbox.py --db "sqlite:///data/ih_beta.db" --reader demo --json --out report.json
```

### Explore the algorithm knobs (single strategy / sliders)

```bash
python examples/rec_sandbox.py --db "sqlite:///data/ih_beta.db" --reader demo --strategy rwe-b
python examples/rec_sandbox.py --db "sqlite:///data/ih_beta.db" --reader demo \
    --params '{"beta":0.1}' --params '{"beta":0.95}' --compare
```

> `--strategy` is `blend` (default) | `rwe-b` (bridging) | `rwe-d` (discovery) | `adaptive`.
> `--params` takes a JSON knob dict — `beta` (discovery strength) or `epsilon` (openness).

### Show only very recent news (tighten the freshness window)

```bash
RWE_FEED_MAX_AGE_DAYS=7 python examples/rec_sandbox.py --db "sqlite:///data/ih_beta.db" --reader demo
```

> Default window is 60 days. **Caveat:** with a stale catalog, a tight window can drop *everything*
> → an empty feed. Re-ingest first (Part 1, §3), or widen the window.

### Back up before a destructive `--reset`

```bash
python examples/db_backup.py backup     # timestamped, consistent, server can stay up
python examples/db_backup.py status     # list backups + storage diagnostics
# python examples/db_backup.py restore <backup-file>   # STOP the engine first
```

### Reproducible / sized demo seed

```bash
python examples/seed_demo_reader.py --reset --seed 7          # a different but REPRODUCIBLE history
python examples/seed_demo_reader.py --reset --random --count 12
```

### Verify nothing broke (after a `git reset --hard` or a change)

```bash
python -m pytest -q                 # full test suite
python examples/validate_recs.py    # recommendation regression scenarios
python examples/validate_metrics.py --golden all   # metric validation
```

### Check the catalog is healthy / buildable

```bash
python examples/corpus_health.py       # totals, diversity, freshness, age span
python examples/corpus_validation.py   # is the candidate set recommendable?
```

### Multi-source ingest (RSS + optional NewsAPI / Guardian / NewsData / GNews / MediaStack / Currents / Google News RSS / GDELT)

```bash
python examples/sources.py check    # per-adapter enabled/config status (no ingest)
python examples/sources.py poll     # poll every ENABLED source once into the catalog
```

---

> Full command inventory (all 41 CLIs with flags): **`docs/CLI_COMMANDS.md`**.
