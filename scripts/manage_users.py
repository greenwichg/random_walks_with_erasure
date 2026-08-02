"""manage_users.py — grant and revoke closed-beta access by email.

    python scripts/manage_users.py grant-access  alice@example.com
    python scripts/manage_users.py revoke-access alice@example.com
    python scripts/manage_users.py list-access
    python scripts/manage_users.py check         alice@example.com

WHY THIS EDITS A FILE AND NOT THE DATABASE
------------------------------------------
The beta gate already exists and is already enforced: ``web/lib/beta-access.ts``, called from the
NextAuth ``signIn`` callback, so a non-approved address never gets a session and therefore never an
engine account. It reads ``BETA_ALLOWLIST`` (env) plus ``BETA_ALLOWLIST_FILE`` — a path that
``docs/AWS_EC2_DEPLOYMENT_GUIDE.md`` already documents as *"re-read per sign-in (add testers with no
restart)"*.

So the access control is not missing; only a safe way to edit it was. Adding a `beta_access` table
would have meant a second source of truth, a second enforcement point, and a window where the CLI
says "granted" while the gate still says no. This edits the file the gate actually reads.

**It deliberately does NOT create user rows.** ``Store.upsert_user_by_identity`` keys on
``(provider, provider_account_id)``, not on email — a User row pre-created with only an email has no
Identity, so the first real Google sign-in would not find it and would create a SECOND user. The
pre-created row would sit there as an orphan silently splitting that person's history. The account is
created correctly by the OAuth flow on first sign-in; granting access is exactly what lets that
happen.

PARITY IS THE WHOLE RISK
------------------------
This file re-implements the allowlist parser in Python. If it drifts from the TypeScript one, the CLI
reports access that the gate does not grant — the worst possible failure for a tool whose entire job
is to tell you who can get in. ``tests/fixtures/beta_allowlist_parity.json`` is consumed by BOTH test
suites (``tests/test_manage_users.py`` and ``web/lib/beta-access.test.ts``) so drift fails a build
rather than surfacing as a locked-out tester.

Parity covers PATH RESOLUTION, not just the parser. ``loadAllowlist`` opens a file only when
``BETA_ALLOWLIST_FILE`` is a non-empty string — the gate has no default path. This CLI used to fall
back to ``/app/data/allowlist.txt`` when the variable was unset or empty, which is precisely the
drifted state: ``grant-access`` wrote the default path and promised "takes effect on their next
sign-in" while the gate consulted env entries only, and ``check`` read the same phantom file back as
ALLOW. The 2026-08-02 access investigation found that asymmetry in the tree (dormant in production
only because ``deploy/.env`` happens to name the path), so the fallback is gone: with no configured
file, grant/revoke refuse loudly and check/list answer from exactly what the gate would read.

Exit codes: 0 the requested state now holds · 1 invalid input · 2 cannot read/write the file (or no
allowlist file is configured) · 3 ``check`` says the gate would DENY (so it is scriptable).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import tempfile
from dataclasses import dataclass
from typing import Optional

EXIT_OK, EXIT_INVALID, EXIT_IO, EXIT_DENIED = 0, 1, 2, 3

#: The path deployments conventionally use — SUGGESTED in error messages, never silently assumed.
#: (It was once a fallback; see "parity covers path resolution" in the module docstring.)
SUGGESTED_ALLOWLIST_FILE = "/app/data/allowlist.txt"

#: Mirrors `parseAllowlist` in web/lib/beta-access.ts. Splitting on newline/comma/semicolon is what
#: lets one line hold several entries, which revoke has to handle without destroying its neighbours.
_SPLIT = re.compile(r"[\n,;]+")


@dataclass(frozen=True)
class Entry:
    kind: str          # "email" | "domain"
    value: str

    def render(self) -> str:
        return f"@{self.value}" if self.kind == "domain" else self.value


def parse_allowlist(raw: Optional[str]) -> "list[Entry]":
    """Port of ``parseAllowlist``. Lowercases, trims, drops blanks and whole-line ``#`` comments,
    and classifies a leading ``@`` as a domain entry.

    Note the comment rule is LINE-level in the TypeScript (`filter(s => !s.startsWith("#"))` runs
    after the split), so ``alice@example.com # added today`` is one token containing spaces and
    matches nobody. This CLI therefore never writes an inline comment."""
    if not raw:
        return []
    out = []
    for tok in _SPLIT.split(raw):
        s = tok.strip().lower()
        if not s or s.startswith("#"):
            continue
        e = Entry("domain", s[1:]) if s.startswith("@") else Entry("email", s)
        if e.value:
            out.append(e)
    return out


def matches(email: str, entries: "list[Entry]") -> bool:
    """Port of ``matches``: exact address, or its domain via an ``@domain`` entry."""
    e = email.strip().lower()
    at = e.rfind("@")
    if at <= 0 or at == len(e) - 1:
        return False
    domain = e[at + 1:]
    return any(x.value == e if x.kind == "email" else x.value == domain for x in entries)


def gate_enabled(env=None) -> bool:
    """Port of ``betaAccessEnabled``: explicit flag wins, else ON in production."""
    env = os.environ if env is None else env
    flag = env.get("BETA_ACCESS_ENABLED")
    if flag is not None and flag.strip() != "":
        return flag.strip().lower() in ("1", "true", "yes", "on")
    return env.get("RWE_ENV") in ("production", "prod")


def env_entries(env=None) -> "list[Entry]":
    """Entries from ``BETA_ALLOWLIST`` — visible here, but NOT editable by this tool: they live in
    ``deploy/.env`` and changing them needs a redeploy of `web`. Shown so a confusing "I granted it
    but they still can't get in" (or its opposite) is answerable from one command."""
    env = os.environ if env is None else env
    return parse_allowlist(env.get("BETA_ALLOWLIST"))


def allowlist_path(explicit: Optional[str], env=None) -> "Optional[str]":
    """The file the GATE will read, or ``None`` when it reads no file at all.

    Mirrors ``loadAllowlist``: a set-but-empty ``BETA_ALLOWLIST_FILE`` (exactly what compose renders
    from ``${BETA_ALLOWLIST_FILE:-}`` when ``deploy/.env`` lacks the key) means NO file — in both
    languages the empty string is falsy, so the two sides agree by construction. An explicit
    ``--file`` still wins: that is the operator naming a file, and grant warns if it is not the one
    the gate reads."""
    env = os.environ if env is None else env
    return explicit or env.get("BETA_ALLOWLIST_FILE") or None


def _no_file_error(verb: str) -> int:
    print(f"error: cannot {verb} — no allowlist file is configured here.\n"
          f"  BETA_ALLOWLIST_FILE is unset (or empty), and the sign-in gate reads a file ONLY when\n"
          f"  it names one; there is no default on either side. Anything written to an assumed path\n"
          f"  would grant nothing. Set it in deploy/.env for BOTH web and api (e.g.\n"
          f"  BETA_ALLOWLIST_FILE={SUGGESTED_ALLOWLIST_FILE}), `docker compose … up -d`, and retry —\n"
          f"  or pass --file to edit a specific file deliberately.", file=sys.stderr)
    return EXIT_IO


def read_lines(path: str) -> "list[str]":
    try:
        return pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def write_lines(path: str, lines: "list[str]") -> None:
    """Write atomically. The gate re-reads this file on every sign-in attempt, so a half-written
    file is a file somebody signs in against. Temp + rename in the same directory makes the swap
    atomic and leaves no window."""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".allowlist-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines).rstrip("\n") + "\n")
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def validate(arg: str) -> Entry:
    """Accept ``user@host.tld`` or ``@host.tld``. Raises ValueError with a usable message.

    Deliberately not RFC 5322 — that grammar accepts things Google will never issue and rejecting a
    real tester's address is the expensive error here. This checks the shape the gate actually
    matches on."""
    s = (arg or "").strip().lower()
    if not s:
        raise ValueError("empty address")
    if any(c.isspace() for c in s):
        raise ValueError(f"{arg!r} contains whitespace")
    if s.startswith("@"):
        domain = s[1:]
        if "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError(f"{arg!r} is not a valid @domain entry (expected e.g. @example.com)")
        return Entry("domain", domain)
    if s.count("@") != 1:
        raise ValueError(f"{arg!r} is not a valid email address (expected e.g. alice@example.com)")
    local, _, domain = s.partition("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError(f"{arg!r} is not a valid email address (expected e.g. alice@example.com)")
    return Entry("email", s)


def signed_in_emails() -> "Optional[set]":
    """Emails that already have an engine account, or None when the store is unreachable.

    Enrichment, never a gate: `list-access` is far more useful when it separates "invited" from
    "actually signed in", and that is the one question the allowlist file cannot answer on its own.
    Reaching the database is optional so the CLI still works from a laptop with no DB."""
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "examples"))
        import store as store_mod
        from sqlalchemy import text as _text
        st = store_mod.Store()
        with st.session() as s:
            rows = s.execute(_text("SELECT LOWER(email) FROM users WHERE email IS NOT NULL")).all()
        return {r[0] for r in rows if r[0]}
    except Exception:
        return None


# --------------------------------------------------------------------------- commands


def cmd_grant(args) -> int:
    try:
        entry = validate(args.email)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_INVALID
    path = allowlist_path(args.file)
    if path is None:
        return _no_file_error(f"grant {args.email}")
    lines = read_lines(path)
    existing = parse_allowlist("\n".join(lines))

    if entry in existing:
        print(f"already granted: {entry.render()} is in {path} — nothing to do")
        return EXIT_OK

    covered = [e for e in existing if e.kind == "domain" and entry.kind == "email"
               and matches(entry.value, [e])]
    try:
        write_lines(path, lines + [entry.render()])
    except OSError as e:
        print(f"error: cannot write {path}: {e}", file=sys.stderr)
        return EXIT_IO

    print(f"granted: {entry.render()} added to {path}")
    if covered:
        print(f"  note: already covered by {covered[0].render()}; the explicit entry keeps access "
              f"if that domain is ever removed")
    if not gate_enabled():
        print("  note: BETA_ACCESS_ENABLED is off here, so the gate is currently allowing everyone")
    # The promise below is only true for the file the gate actually reads. With --file pointing
    # anywhere else, saying it would repeat the exact lie this tool once told (see the docstring).
    gate_file = os.environ.get("BETA_ALLOWLIST_FILE") or None
    if gate_file == path:
        print("  takes effect on their next sign-in attempt — the file is re-read each time, no restart")
    else:
        target = gate_file if gate_file else "NO file (BETA_ALLOWLIST_FILE is not set here)"
        print(f"  WARNING: the sign-in gate reads {target} — this grant has no effect until "
              f"BETA_ALLOWLIST_FILE names the file just written")
    return EXIT_OK


def cmd_revoke(args) -> int:
    try:
        entry = validate(args.email)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_INVALID
    path = allowlist_path(args.file)
    if path is None:
        return _no_file_error(f"revoke {args.email}")
    lines = read_lines(path)

    kept, removed = [], 0
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            kept.append(line)                      # comments and blanks survive untouched
            continue
        toks, survivors = [t for t in _SPLIT.split(line)], []
        for tok in toks:
            parsed = parse_allowlist(tok)
            if parsed and parsed[0] == entry:
                removed += 1
            elif tok.strip():
                survivors.append(tok.strip())
        if survivors:
            kept.append(", ".join(survivors))
    if removed == 0:
        print(f"not present: {entry.render()} is not in {path} — nothing to do")
    else:
        try:
            write_lines(path, kept)
        except OSError as e:
            print(f"error: cannot write {path}: {e}", file=sys.stderr)
            return EXIT_IO
        print(f"revoked: removed {removed} entr{'y' if removed == 1 else 'ies'} for "
              f"{entry.render()} from {path}")

    still = [e for e in env_entries() if
             (e == entry) or (entry.kind == "email" and e.kind == "domain" and matches(entry.value, [e]))]
    if still:
        print(f"  WARNING: still allowed by BETA_ALLOWLIST ({still[0].render()}) in deploy/.env — "
              f"this tool cannot edit that; remove it there and redeploy `web`")
    file_left = parse_allowlist("\n".join(kept))
    if entry.kind == "email" and any(e.kind == "domain" and matches(entry.value, [e]) for e in file_left):
        print(f"  WARNING: still allowed by a domain entry in {path}")
    # Stateless JWT sessions: this stops the NEXT sign-in, it does not end a live one.
    print("  note: sessions are stateless JWTs — an already-signed-in user keeps their session "
          "until it expires. Revoking blocks the next sign-in, not the current one.")
    return EXIT_OK


def cmd_list(args) -> int:
    path = allowlist_path(args.file)
    file_entries = parse_allowlist("\n".join(read_lines(path))) if path else []
    env_e = env_entries()
    users = signed_in_emails()

    if args.json:
        print(json.dumps({
            "gateEnabled": gate_enabled(), "file": path,
            "fileEntries": [{"kind": e.kind, "value": e.render()} for e in file_entries],
            "envEntries": [{"kind": e.kind, "value": e.render()} for e in env_e],
            "signedIn": sorted(users) if users is not None else None,
        }, indent=2))
        return EXIT_OK

    print(f"beta gate: {'ENABLED' if gate_enabled() else 'DISABLED (everyone is allowed)'}")
    if path is None:
        print("allowlist file: NONE — file mode is OFF (BETA_ALLOWLIST_FILE is not set; "
              "the gate reads BETA_ALLOWLIST env entries only)")
    else:
        print(f"allowlist file: {path}"
              f"{'' if pathlib.Path(path).exists() else '  (does not exist yet)'}")
    if not file_entries and not env_e and gate_enabled():
        print("\n  ! FAIL-CLOSED: the gate is on and the allowlist is empty, so NOBODY can sign in.")

    def show(title, entries, editable):
        if not entries:
            return
        print(f"\n{title} ({len(entries)}){'' if editable else '  — not editable by this tool'}")
        for e in sorted(entries, key=lambda x: (x.kind, x.value)):
            mark = ""
            if users is not None and e.kind == "email":
                mark = "  signed in" if e.value in users else "  not yet signed in"
            print(f"  {e.render():<44}{mark}")
    show("file entries", file_entries, True)
    show("env entries (BETA_ALLOWLIST)", env_e, False)
    if users is None:
        print("\n  (database not reachable — sign-in status omitted)")
    return EXIT_OK


def cmd_check(args) -> int:
    """The decision the gate would actually make, by the same rules it uses — including which file
    (if any) it reads: with no configured file, only env entries count, exactly as in the gate."""
    email = (args.email or "").strip()
    path = allowlist_path(args.file)
    entries = (parse_allowlist("\n".join(read_lines(path))) if path else []) + env_entries()
    if not gate_enabled():
        print(f"ALLOW {email}: the gate is disabled here (BETA_ACCESS_ENABLED / RWE_ENV)")
        return EXIT_OK
    if not email:
        print("DENY (no email supplied)", file=sys.stderr)
        return EXIT_DENIED
    if not entries:
        print(f"DENY {email}: the allowlist is empty and the gate is on (fail-closed)",
              file=sys.stderr)
        return EXIT_DENIED
    if matches(email, entries):
        print(f"ALLOW {email}: matches the allowlist")
        return EXIT_OK
    print(f"DENY {email}: not on the allowlist", file=sys.stderr)
    return EXIT_DENIED


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="manage_users.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default=None,
                    help="allowlist file (default: $BETA_ALLOWLIST_FILE — when that is unset the "
                         "gate reads no file, and grant/revoke refuse rather than write one)")
    sub = ap.add_subparsers(dest="command", required=True)

    g = sub.add_parser("grant-access", help="allow an email (or @domain) to sign in")
    g.add_argument("email")
    g.set_defaults(func=cmd_grant)

    r = sub.add_parser("revoke-access", help="remove an email (or @domain) from the allowlist")
    r.add_argument("email")
    r.set_defaults(func=cmd_revoke)

    ls = sub.add_parser("list-access", help="show who is allowed, and who has actually signed in")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=cmd_list)

    c = sub.add_parser("check", help="would this address be allowed right now? (exit 3 = denied)")
    c.add_argument("email")
    c.set_defaults(func=cmd_check)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
