# Export Reading History

> **Scope:** how to use `examples/export_reading_history.py` — a developer-only, **read-only**
> utility that exports one or more users' reading history to a portable, versioned JSON file for
> offline notebook experimentation.
> **Tool:** `examples/export_reading_history.py` · **Branch:** `claude/sleepy-gates-oecof1`

The utility opens the store, reads reading history via the existing `Store` APIs (`list_reads` +
`get_user`), and writes a JSON file. It **never writes to the database** and never touches the
recommendation engine, `evaluate()`, the report contract, or serving behaviour.

---

## 1. Export it

Run from the repository root. Pick exactly one selector — `--user` **or** `--all-users`:

```bash
# one user: the persisted demo account
python examples/export_reading_history.py --user demo --out reading_history.json

# one user: by id (both "2" and "user:2" work)
python examples/export_reading_history.py --user 2 --out reading_history.json

# every user in the database
python examples/export_reading_history.py --all-users --out reading_history.json
```

Each run prints a one-line confirmation to **stderr**, e.g.:

```
exported 3 read(s) for user 1 -> reading_history.json
```

The file lands at whatever path you pass to `--out` (relative to the repo root). Pass
`--out data/reading_history.json` to place it under `data/`.

### Options

| Flag | Purpose |
|---|---|
| `--user <who>` | one reader — `demo`, `N`, or `user:N` (mutually exclusive with `--all-users`) |
| `--all-users` | every user in the database (mutually exclusive with `--user`) |
| `--db <url>` | store URL, e.g. `sqlite:///data/ih_beta.db` (default: `RWE_DB_URL`, else the repo file) |
| `--out <path>` | output JSON path, or `-` for stdout (default: `reading_history.json`) |

`--help` is the authoritative flag list:

```bash
python examples/export_reading_history.py --help
```

### Which database does it read?

By default (no `--db`) it uses your real app database — `RWE_DB_URL` if set, otherwise the repo file
`data/ih_beta.db`. To export from a specific database, point `--db` at it:

```bash
python examples/export_reading_history.py --all-users --db sqlite:///data/ih_beta.db --out reading_history.json
```

---

## 2. Look at it

Preview without writing a file by sending the JSON to stdout (`--out -`):

```bash
python examples/export_reading_history.py --user demo --out -
```

Or pretty-print the file you just wrote (Python):

```python
import json
doc = json.load(open("reading_history.json"))
print(json.dumps(doc, indent=2)[:800])
```

---

## 3. Output format

The envelope is **versioned** (`version` bumps only if the shape changes in a way a consumer must
notice) and is strict, portable JSON — non-finite floats such as an unknown outlet's `NaN` lean are
serialized as `null`.

**Single user** (`--user`):

```json
{
  "version": 1,
  "exportedAt": "2026-07-14T13:15:47.258592+00:00",
  "user": { "id": 2, "provider": "dev", "providerAccountId": "reader-two" },
  "readingHistory": [
    {
      "readAt": "2026-07-01T00:00:00+00:00",
      "canonicalUrl": "https://cnn.com/d",
      "articleId": "https://cnn.com/d",
      "title": "…",
      "outlet": "CNN",
      "category": "Politics",
      "lean": -1.0,
      "emotion": "calm",
      "readSource": "seed"
    }
  ]
}
```

**All users** (`--all-users`) — same per-user block, wrapped in a `users` array (no top-level `user`):

```json
{
  "version": 1,
  "exportedAt": "2026-07-14T13:15:47.258592+00:00",
  "users": [
    { "user": { "id": 1, "provider": "dev", "providerAccountId": "demo@infodiet.local" },
      "readingHistory": [ ... ] },
    { "user": { "id": 2, "provider": "dev", "providerAccountId": "reader-two" },
      "readingHistory": [ ... ] }
  ]
}
```

Per-read fields: `readAt`, `canonicalUrl`, `articleId`, `title`, `outlet`, `category`, `lean`
(unknown outlet → `null`), `emotion` (present only when an enricher ran; else `null`), `readSource`.
Reads are ordered **oldest first**. A user with no reads exports a valid envelope with
`"readingHistory": []`.

---

## 4. In a Colab / Jupyter notebook

Prefix the CLI with `!` to run it as a shell command in a notebook cell:

```python
!python examples/export_reading_history.py --user demo --out reading_history.json
```

The steps above only write the file **inside the notebook environment**. To download it to your
computer from Colab:

```python
from google.colab import files
files.download("reading_history.json")
```

So the notebook flow is: **export** (`!python examples/export_reading_history.py … --out
reading_history.json`) → optionally **preview** → **download** (`files.download(...)`).

---

## Notes

- **Read-only.** The utility performs only `SELECT`s; it adds, changes, and deletes zero rows. (Under
  the store's WAL journaling the raw `.db` file's bytes can shift on open as the write-ahead log is
  checkpointed, but no data changes — verify read-only-ness against the **logical row content**, not
  the raw file hash.)
- **Deterministic.** Re-running the same export produces byte-identical output apart from the
  `exportedAt` wall-clock timestamp.
- **Errors** exit non-zero with a clear message (no traceback) and write no output file — e.g. an
  unknown id (`no user with id N`), an unparseable `--user`, or `--user demo` against a database with
  no demo account.
