#!/usr/bin/env python3
"""How much of this repository is the deployed product?

    python scripts/loc_report.py              # the report
    python scripts/loc_report.py --list-prod  # every file counted as production, one per line

**Reachability, not directory names.** "Production" here means *a file the running containers
actually load*, computed as the import closure from the entrypoints `deploy/docker-compose.yml`
names — not a hand-curated list, which is the usual way this measurement drifts from the truth. Two
consequences worth stating up front, because both invert the obvious guess:

* `examples/` is **not** examples. `deploy/Dockerfile.api` ends `CMD ["python",
  "examples/api_fastapi.py", ...]`: this directory is the entire backend. Excluding it by name — the
  conventional rule — deletes the product and leaves `rwe/`, a research library.
* Plenty of `examples/` genuinely *is* tooling (audits, probes, one-off analyses). It ships in the
  image but no served request imports it, so the closure leaves it out without anyone maintaining a
  list.

`rwe/` is counted only where it is reached. It is a research package with a large surface the served
app never touches, and counting all of it would inflate the "recommendation engine" several-fold.

Two numbers per bucket, because one is misleading on this codebase: **lines** (everything) and
**code** (non-blank, non-comment). Python is tokenized properly — `tokenize` distinguishes a
docstring from an expression statement — and TS/JS/CSS go through a small state machine that knows
strings and template literals, so a `//` inside a URL is not mistaken for a comment.
"""

from __future__ import annotations

import argparse
import ast
import collections
import io
import json
import pathlib
import re
import subprocess
import sys
import tokenize

ROOT = pathlib.Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------- entrypoints
#: Python processes `deploy/docker-compose.yml` actually starts. `api` is the served product;
#: `rss_ingest` is the ingest service; `db_backup` is the backup service and the scheduler's loop.
PY_ENTRYPOINTS = ("examples/api_fastapi.py", "examples/rss_ingest.py", "examples/db_backup.py")

#: Next.js has no explicit entry list — the router discovers files by CONVENTION, so the convention
#: is the entry list. Everything else in `web/` is reached only if one of these imports it.
WEB_ENTRY_GLOBS = ("app/**/page.tsx", "app/**/layout.tsx", "app/**/route.ts", "app/**/error.tsx",
                   "app/**/loading.tsx", "app/**/not-found.tsx", "app/**/template.tsx",
                   "middleware.ts", "instrumentation.ts")
#: Served verbatim from `public/`, so no import reaches it — but it is running code in the browser.
WEB_EXTRA = ("public/sw.js",)

#: Shipped in the image and executed by the operator (`docker exec`) or the host, not by a request.
#: Counted as infrastructure rather than as product.
INFRA_GLOBS = ("deploy/ops/*.sh", "deploy/host/**/*.sh", "deploy/*.yml", "deploy/Dockerfile*",
               "deploy/Caddyfile", ".github/workflows/*.yml")

# --------------------------------------------------------------------------- module map
#: Path prefix → module. FIRST match wins, so order is significant: the specific rules precede the
#: directory-wide fallbacks. Anything unmatched lands in "Unclassified", which is deliberately
#: visible — a silent default would hide drift as the codebase grows.
MODULES: "tuple[tuple[str, str], ...]" = (
    ("web/app/api/", "Frontend — BFF proxy routes"),
    ("web/app/", "Frontend — pages & routing"),
    ("web/components/", "Frontend — components"),
    ("web/hooks/", "Frontend — data hooks"),
    ("web/lib/", "Frontend — shared libraries"),
    ("web/services/", "Frontend — API client"),
    ("web/types/", "Frontend — domain types"),
    ("web/public/", "Frontend — service worker"),
    # Broken out because they are translation DATA, not logic: 851 keys x 5 locales is ~4.3k lines
    # that would otherwise sit in a "config" bucket and read as frontend engineering. The running
    # app cannot render without them, so they are production — but anyone comparing this codebase
    # to another should be able to see the number and subtract it.
    ("web/messages/", "Frontend — i18n catalogs (data)"),
    ("web/", "Frontend — styles & config"),
    ("examples/api_fastapi.py", "Backend — HTTP API"),
    ("examples/store.py", "Backend — persistence"),
    ("rwe/", "Recommendation engine"),
    ("examples/personalize.py", "Recommendation engine"),
    ("examples/baselines.py", "Recommendation engine"),
    ("examples/recommendation", "Recommendation engine"),
    ("examples/story_service.py", "Story clustering & intelligence"),
    ("examples/story_", "Story clustering & intelligence"),
    ("examples/coverage_comparison.py", "Story clustering & intelligence"),
    ("examples/evidence_resolver.py", "Story clustering & intelligence"),
    ("examples/insight", "AI & analysis"),
    ("examples/llm", "AI & analysis"),
    ("examples/coach", "AI & analysis"),
    ("examples/analy", "AI & analysis"),
    ("examples/notification", "Notifications & push"),
    ("examples/push_", "Notifications & push"),
    ("examples/rss_ingest.py", "Ingestion & sources"),
    ("examples/sources.py", "Ingestion & sources"),
    ("examples/ingest", "Ingestion & sources"),
    ("examples/gdelt", "Ingestion & sources"),
    ("examples/feed_service.py", "Ingestion & sources"),
    ("examples/db_backup.py", "Ops tooling (in image)"),
    ("scripts/", "Ops tooling (in image)"),
    ("deploy/", "Infrastructure"),
    (".github/", "Infrastructure"),
    ("examples/", "Backend — services & support"),
)

LANGS = {".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
         ".jsx": "JavaScript", ".mjs": "JavaScript", ".css": "CSS", ".sql": "SQL",
         ".sh": "Shell", ".yml": "YAML", ".yaml": "YAML", ".json": "JSON",
         ".md": "Markdown", ".ipynb": "Notebook", ".toml": "TOML"}

#: Excluded from the PRODUCTION closure wherever they appear. The closure already excludes most of
#: this by construction (nothing served imports a test); these catch the cases where a production
#: file sits beside a non-production one in the same directory.
NON_PROD = re.compile(
    r"(^|/)(tests?|e2e|__tests__|fixtures|mocks?|benchmarks?|notebooks?)(/|$)"
    r"|\.test\.[jt]sx?$|\.spec\.[jt]sx?$|_test\.py$|^test_",
)


def _is_non_prod(rel: str) -> bool:
    return bool(NON_PROD.search(rel))


def module_of(rel: str) -> str:
    for prefix, name in MODULES:
        if rel.startswith(prefix):
            return name
    return "Unclassified"


# --------------------------------------------------------------------------- counting
def count_python(text: str) -> "tuple[int, int]":
    """``(lines, code)``. Uses ``tokenize`` so a docstring — a STRING that is the whole logical
    line — is counted as documentation rather than as code, which a regex cannot do."""
    lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    doc_or_comment: set[int] = set()
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return lines, sum(1 for ln in text.splitlines()
                          if ln.strip() and not ln.strip().startswith("#"))
    raw = text.splitlines()
    prev_end_row, prev_type = 0, tokenize.INDENT
    for tok in toks:
        if tok.type == tokenize.COMMENT:
            # Only a comment that OWNS its line. `return 1  # why` is code with an annotation, and
            # counting it as documentation would understate code by the repo's trailing-comment
            # density — which here is high enough to move the headline number.
            row, col = tok.start
            if row - 1 < len(raw) and not raw[row - 1][:col].strip():
                doc_or_comment.update(range(tok.start[0], tok.end[0] + 1))
        elif tok.type == tokenize.STRING and prev_type in (
                tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE, tokenize.NL, tokenize.ENCODING):
            doc_or_comment.update(range(tok.start[0], tok.end[0] + 1))
        if tok.type not in (tokenize.NL, tokenize.COMMENT):
            prev_type = tok.type
        prev_end_row = max(prev_end_row, tok.end[0])
    code = 0
    for i, ln in enumerate(text.splitlines(), start=1):
        if ln.strip() and i not in doc_or_comment:
            code += 1
    return lines, code


_C_LIKE = {"TypeScript", "JavaScript", "CSS", "SQL"}


def count_c_like(text: str) -> "tuple[int, int]":
    """``(lines, code)`` for `//`-and-`/* */` languages, with a state machine rather than a regex:
    `https://x` inside a string is not a comment, and a template literal may span lines."""
    lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    code_rows: set[int] = set()
    row, i, n = 1, 0, len(text)
    state = "code"          # code | line_comment | block_comment | ' | " | `
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "\n":
            row += 1
            if state == "line_comment":
                state = "code"
            i += 1
            continue
        if state == "code":
            if ch == "/" and nxt == "/":
                state, i = "line_comment", i + 2
                continue
            if ch == "/" and nxt == "*":
                state, i = "block_comment", i + 2
                continue
            if ch in "'\"`":
                state = ch
                code_rows.add(row)
                i += 1
                continue
            if not ch.isspace():
                code_rows.add(row)
            i += 1
            continue
        if state == "block_comment":
            if ch == "*" and nxt == "/":
                state, i = "code", i + 2
                continue
            i += 1
            continue
        if state == "line_comment":
            i += 1
            continue
        # inside a string/template
        code_rows.add(row)
        if ch == "\\":
            i += 2
            continue
        if ch == state:
            state = "code"
        i += 1
    return lines, len(code_rows)


def count_shell_like(text: str) -> "tuple[int, int]":
    lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    code = sum(1 for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#"))
    return lines, code


def measure(path: pathlib.Path) -> "tuple[str, int, int]":
    lang = LANGS.get(path.suffix, "Other")
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return lang, 0, 0
    if lang == "Python":
        return (lang, *count_python(text))
    if lang in _C_LIKE:
        return (lang, *count_c_like(text))
    return (lang, *count_shell_like(text))


# --------------------------------------------------------------------------- closures
def python_closure() -> "set[pathlib.Path]":
    """Every module the deployed Python processes can import, transitively.

    Walks ALL `Import`/`ImportFrom` nodes, not just top-level ones: this codebase imports lazily
    inside functions on purpose (to keep heavy subsystems out of an import graph), and a top-level-
    only walk would miss `push_delivery`, `story_events` and the rest of the poller's seam.
    """
    roots = {"examples": ROOT / "examples", "rwe": ROOT / "rwe", "scripts": ROOT / "scripts"}
    seen: set[pathlib.Path] = set()
    queue = [ROOT / p for p in PY_ENTRYPOINTS]

    def resolve(name: str) -> "pathlib.Path | None":
        head = name.split(".")[0]
        # a sibling module inside examples/ or scripts/ (they are on sys.path at runtime)
        for base in (roots["examples"], roots["scripts"]):
            cand = base / (name.replace(".", "/") + ".py")
            if cand.exists():
                return cand
        if head == "rwe":
            cand = ROOT / (name.replace(".", "/") + ".py")
            if cand.exists():
                return cand
            pkg = ROOT / name.replace(".", "/") / "__init__.py"
            if pkg.exists():
                return pkg
        return None

    while queue:
        path = queue.pop()
        if path in seen or not path.exists():
            continue
        seen.add(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for nm in names:
                target = resolve(nm)
                if target is not None and target not in seen:
                    queue.append(target)
    return {p for p in seen if not _is_non_prod(str(p.relative_to(ROOT)))}


_IMPORT_RE = re.compile(
    r"""(?:^|\n)\s*(?:import\s[^'"\n]*?from\s*|export\s[^'"\n]*?from\s*|import\s*)['"]([^'"]+)['"]"""
    r"""|import\(\s*['"]([^'"]+)['"]\s*\)""",
    re.MULTILINE,
)


def web_closure() -> "set[pathlib.Path]":
    """Every module the Next.js build pulls in from the router's conventional entrypoints.

    Only relative and `@/` specifiers are followed: a bare specifier is a dependency in
    `node_modules`, which is vendor code and out of scope by the brief.
    """
    web = ROOT / "web"
    seen: set[pathlib.Path] = set()
    queue: list[pathlib.Path] = []
    for pattern in WEB_ENTRY_GLOBS:
        queue += [p for p in web.glob(pattern) if not _is_non_prod(str(p.relative_to(ROOT)))]
    queue += [web / p for p in WEB_EXTRA]

    def resolve(spec: str, importer: pathlib.Path) -> "pathlib.Path | None":
        if spec.startswith("@/"):
            base = web / spec[2:]
        elif spec.startswith("."):
            base = (importer.parent / spec).resolve()
        else:
            return None                     # node_modules — vendor, excluded
        for cand in (base, *(base.with_suffix(s) for s in (".ts", ".tsx", ".js", ".jsx", ".mjs",
                                                           ".css", ".json")),
                     *(base / f"index{s}" for s in (".ts", ".tsx", ".js", ".jsx"))):
            if cand.is_file():
                return cand
        return None

    while queue:
        path = queue.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        if path.suffix in (".json", ".css"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in _IMPORT_RE.finditer(text):
            spec = m.group(1) or m.group(2)
            target = resolve(spec, path) if spec else None
            if target is not None and target not in seen:
                queue.append(target)
    return {p for p in seen if not _is_non_prod(str(p.relative_to(ROOT)))}


def infra_files() -> "set[pathlib.Path]":
    out: set[pathlib.Path] = set()
    for pattern in INFRA_GLOBS:
        out |= {p for p in ROOT.glob(pattern) if p.is_file()}
    return out


def repo_files() -> "set[pathlib.Path]":
    """Everything git tracks — the denominator. Git is the authority, so untracked build output and
    `node_modules` are excluded by construction rather than by a pattern list that can rot."""
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True,
                         check=True).stdout.splitlines()
    return {ROOT / f for f in out if (ROOT / f).is_file()}


# --------------------------------------------------------------------------- report
def tally(paths) -> "tuple[collections.Counter, collections.Counter, collections.Counter, collections.Counter]":
    by_lang_lines, by_lang_code = collections.Counter(), collections.Counter()
    by_mod_lines, by_mod_code = collections.Counter(), collections.Counter()
    for p in paths:
        rel = str(p.relative_to(ROOT))
        lang, lines, code = measure(p)
        by_lang_lines[lang] += lines
        by_lang_code[lang] += code
        by_mod_lines[module_of(rel)] += lines
        by_mod_code[module_of(rel)] += code
    return by_lang_lines, by_lang_code, by_mod_lines, by_mod_code


def _table(title: str, lines: collections.Counter, code: collections.Counter, files=None) -> None:
    print(f"\n{title}")
    print(f"  {'':<34} {'files':>7} {'lines':>9} {'code':>9}  {'code %':>7}")
    total_l, total_c = sum(lines.values()), sum(code.values())
    for key in sorted(lines, key=lambda k: -code[k]):
        pct = (100.0 * code[key] / total_c) if total_c else 0.0
        nf = f"{files[key]:>7,}" if files else f"{'':>7}"
        print(f"  {key:<34} {nf} {lines[key]:>9,} {code[key]:>9,}  {pct:>6.1f}%")
    print(f"  {'TOTAL':<34} {'':>7} {total_l:>9,} {total_c:>9,}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list-prod", action="store_true", help="print every production file and exit")
    ap.add_argument("--json", action="store_true", help="machine-readable totals")
    args = ap.parse_args()

    py, web, infra = python_closure(), web_closure(), infra_files()
    product = py | web
    prod_all = product | infra
    repo = repo_files()

    if args.list_prod:
        for p in sorted(prod_all):
            print(p.relative_to(ROOT))
        return 0

    p_lang_l, p_lang_c, p_mod_l, p_mod_c = tally(prod_all)
    r_lang_l, r_lang_c, _, _ = tally(repo)

    files_by_lang = collections.Counter(LANGS.get(p.suffix, "Other") for p in prod_all)
    files_by_mod = collections.Counter(module_of(str(p.relative_to(ROOT))) for p in prod_all)

    prod_code, repo_code = sum(p_lang_c.values()), sum(r_lang_c.values())
    prod_lines, repo_lines = sum(p_lang_l.values()), sum(r_lang_l.values())

    if args.json:
        print(json.dumps({"production": {"files": len(prod_all), "lines": prod_lines,
                                         "code": prod_code},
                          "repository": {"files": len(repo), "lines": repo_lines,
                                         "code": repo_code}}, indent=2))
        return 0

    print(f"Production reachability from {len(PY_ENTRYPOINTS)} python entrypoints + the Next.js "
          f"router\n{'=' * 78}")
    print(f"  python closure   {len(py):>4} files")
    print(f"  web closure      {len(web):>4} files")
    print(f"  infrastructure   {len(infra):>4} files")

    _table("BY LANGUAGE — production", p_lang_l, p_lang_c, files_by_lang)
    _table("BY MODULE — production", p_mod_l, p_mod_c, files_by_mod)
    _table("BY LANGUAGE — whole repository (git ls-files)", r_lang_l, r_lang_c)

    share_code = 100.0 * prod_code / repo_code if repo_code else 0.0
    share_lines = 100.0 * prod_lines / repo_lines if repo_lines else 0.0
    print(f"\n{'=' * 78}")
    print(f"  production   {prod_lines:>9,} lines   {prod_code:>9,} code   ({len(prod_all):,} files)")
    print(f"  repository   {repo_lines:>9,} lines   {repo_code:>9,} code   ({len(repo):,} files)")
    print(f"  share        {share_lines:>8.1f}%           {share_code:>8.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
