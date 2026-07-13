# Refresh Cheatsheet

> Quick commands to **refresh** the local demo: update the code, refresh the demo reader's reading
> history, pull the latest news into the catalog, and run a compare experiment.
> Run every command **from the repository root** (`.../random_walks_with_erasure`).

## 1. Update the code

```bash
git fetch origin claude/sleepy-gates-oecof1 && git reset --hard FETCH_HEAD   # get 7fa0563
```

> `git reset --hard FETCH_HEAD` always moves you to the **current branch tip** (the `7fa0563`
> comment is just illustrative — you'll land on whatever is latest). `data/` is git-ignored, so your
> local database is untouched by the reset.

## 2. Refresh the demo reading history

```bash
python examples/seed_demo_reader.py --reset --random           # DIFFERENT history every run
python examples/rec_sandbox.py --db "sqlite:///data/ih_beta.db" --reader demo
```

> `--reset` clears the demo reader's old reads and `--random` picks a new left-leaning set, so the
> reading history (and the recommendations that follow from it) changes on every run.

## 3. Refresh the news catalog (latest news)

```bash
python examples/rss_ingest.py run --feeds deploy/rss_feeds.example.txt   # pull current RSS into the catalog
python examples/rss_ingest.py status                                     # see catalog size / growth
python examples/rec_sandbox.py --db "sqlite:///data/ih_beta.db" --reader demo
```

> The recommendation feed is drawn from the local catalog, which is a snapshot frozen at your last
> ingest. Re-run `rss_ingest run` to pull current articles, check growth with `status`, then re-run
> the sandbox to see the refreshed feed.

## 4. Compare experiment (inject + baseline diff)

```bash
python examples/rec_sandbox.py --db "sqlite:///data/ih_beta.db" --preset right --reader demo --compare
```

> Injects a canned right-leaning article and diffs the evaluated feed against the baseline
> (§7 Feed Changes: what entered / left / moved).
