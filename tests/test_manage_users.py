"""scripts/manage_users.py — beta access by email.

The six cases the brief asked for, plus the one that actually matters: PARITY with the TypeScript
gate. This CLI re-implements the allowlist parser that `web/lib/beta-access.ts` uses to decide who
may sign in. If the two drift, the tool reports access the gate does not grant — the worst failure
available to something whose only job is to say who can get in.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import manage_users as mu           # noqa: E402

FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "beta_allowlist_parity.json").read_text())


@pytest.fixture()
def allowlist(tmp_path, monkeypatch):
    """An isolated allowlist file, with the gate ON and no env entries — production's shape."""
    path = tmp_path / "allowlist.txt"
    monkeypatch.setenv("BETA_ALLOWLIST_FILE", str(path))
    monkeypatch.setenv("BETA_ACCESS_ENABLED", "1")
    monkeypatch.delenv("BETA_ALLOWLIST", raising=False)
    return path


def run(*argv):
    return mu.main(list(argv))


def entries_in(path):
    return [e.render() for e in mu.parse_allowlist(path.read_text() if path.exists() else "")]


# --------------------------------------------------------------------------- parity


@pytest.mark.parametrize("case", FIXTURE["parse"], ids=[c["why"][:40] for c in FIXTURE["parse"]])
def test_parser_matches_the_typescript_gate(case):
    """Same fixture as web/lib/beta-access.test.ts. Drift fails a build, not a tester's sign-in."""
    got = [e.render() for e in mu.parse_allowlist(case["raw"])]
    assert got == case["entries"], case["why"]


@pytest.mark.parametrize("case", FIXTURE["matches"], ids=[c["why"][:40] for c in FIXTURE["matches"]])
def test_matching_rules_match_the_typescript_gate(case):
    entries = mu.parse_allowlist("\n".join(case["entries"]))
    assert mu.matches(case["email"], entries) is case["allowed"], case["why"]


# --------------------------------------------------------------------------- the six required cases


def test_grant_a_new_user(allowlist, capsys):
    assert run("grant-access", "alice@example.com") == mu.EXIT_OK
    assert entries_in(allowlist) == ["alice@example.com"]
    assert "granted" in capsys.readouterr().out


def test_grant_an_existing_user_is_idempotent(allowlist, capsys):
    """Running it twice must not double the entry, must not fail, and must say so plainly —
    an operator re-running a command after a dropped SSH session is the normal case, not an error."""
    assert run("grant-access", "alice@example.com") == mu.EXIT_OK
    capsys.readouterr()
    assert run("grant-access", "alice@example.com") == mu.EXIT_OK
    out = capsys.readouterr().out
    assert entries_in(allowlist) == ["alice@example.com"], "a repeat grant must not duplicate"
    assert "already granted" in out


def test_duplicate_grants_differing_only_in_case_are_the_same_grant(allowlist):
    """The gate lowercases before comparing, so `Alice@` and `alice@` are one person. A CLI that
    treated them as two would leave a second entry behind after a revoke."""
    run("grant-access", "Alice@Example.com")
    run("grant-access", "alice@example.com")
    assert entries_in(allowlist) == ["alice@example.com"]


def test_revoke_access(allowlist, capsys):
    run("grant-access", "alice@example.com")
    run("grant-access", "bob@example.com")
    capsys.readouterr()
    assert run("revoke-access", "alice@example.com") == mu.EXIT_OK
    assert entries_in(allowlist) == ["bob@example.com"], "revoke must not touch the neighbour"
    out = capsys.readouterr().out
    assert "revoked" in out
    assert "stateless JWT" in out, "an operator must be told the live session survives"


def test_revoking_someone_absent_is_a_no_op_not_an_error(allowlist):
    run("grant-access", "bob@example.com")
    assert run("revoke-access", "alice@example.com") == mu.EXIT_OK
    assert entries_in(allowlist) == ["bob@example.com"]


@pytest.mark.parametrize("bad", ["", "not-an-email", "alice@", "@", "a b@example.com",
                                 "alice@example", "two@@example.com", "@.com"])
def test_invalid_email_is_rejected_before_anything_is_written(allowlist, bad):
    """Validation runs first: a malformed argument must never reach the file. A junk line would be
    parsed by the gate as an entry that matches nobody, so the damage is silent."""
    assert run("grant-access", bad) == mu.EXIT_INVALID
    assert not allowlist.exists(), "nothing may be written when the input is rejected"


def test_list_access(allowlist, capsys):
    run("grant-access", "alice@example.com")
    run("grant-access", "@partner.dev")
    capsys.readouterr()
    assert run("list-access") == mu.EXIT_OK
    out = capsys.readouterr().out
    assert "alice@example.com" in out and "@partner.dev" in out
    assert "ENABLED" in out


def test_list_access_json_is_machine_readable(allowlist, capsys):
    run("grant-access", "alice@example.com")
    capsys.readouterr()
    assert run("list-access", "--json") == mu.EXIT_OK
    d = json.loads(capsys.readouterr().out)
    assert d["gateEnabled"] is True
    assert [e["value"] for e in d["fileEntries"]] == ["alice@example.com"]


# --------------------------------------------------------------------------- the sharp edges


def test_empty_allowlist_with_the_gate_on_is_reported_as_fail_closed(allowlist, capsys):
    """The gate denies EVERYONE when it is enabled and the list is empty. That is the correct
    behaviour and the most confusing possible state to debug, so `list-access` names it."""
    assert run("list-access") == mu.EXIT_OK
    assert "FAIL-CLOSED" in capsys.readouterr().out


def test_revoke_warns_when_env_still_grants_access(allowlist, monkeypatch, capsys):
    """BETA_ALLOWLIST lives in deploy/.env and this tool cannot edit it. Silently removing the file
    entry while the env still admits them would report success and change nothing."""
    monkeypatch.setenv("BETA_ALLOWLIST", "alice@example.com")
    run("grant-access", "alice@example.com")
    capsys.readouterr()
    run("revoke-access", "alice@example.com")
    assert "still allowed by BETA_ALLOWLIST" in capsys.readouterr().out


def test_revoke_warns_when_a_domain_entry_still_grants_access(allowlist, capsys):
    run("grant-access", "@example.com")
    run("grant-access", "alice@example.com")
    capsys.readouterr()
    run("revoke-access", "alice@example.com")
    assert "still allowed by a domain entry" in capsys.readouterr().out


def test_comments_and_neighbours_survive_a_revoke(allowlist):
    """Operators hand-edit this file. Rewriting it must preserve their comments and any other entry
    sharing a line, or the tool destroys context every time it is used."""
    allowlist.write_text("# wave 0\nalice@example.com, bob@example.com\n# wave 1\ncarol@example.com\n")
    assert run("revoke-access", "bob@example.com") == mu.EXIT_OK
    text = allowlist.read_text()
    assert "# wave 0" in text and "# wave 1" in text
    assert entries_in(allowlist) == ["alice@example.com", "carol@example.com"]


def test_check_exits_nonzero_when_denied(allowlist):
    """`check` is the scriptable form: exit 3 means the gate would refuse this address."""
    run("grant-access", "alice@example.com")
    assert run("check", "alice@example.com") == mu.EXIT_OK
    assert run("check", "mallory@example.com") == mu.EXIT_DENIED


def test_check_reports_allow_when_the_gate_is_disabled(allowlist, monkeypatch, capsys):
    monkeypatch.setenv("BETA_ACCESS_ENABLED", "0")
    assert run("check", "anyone@example.com") == mu.EXIT_OK
    assert "gate is disabled" in capsys.readouterr().out


def test_the_file_is_written_atomically(allowlist, monkeypatch):
    """The gate re-reads this file on every sign-in, so a half-written file is one somebody signs in
    against. If the write fails, the previous contents must remain intact."""
    run("grant-access", "alice@example.com")
    original = allowlist.read_text()

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(mu.os, "replace", boom)
    assert run("grant-access", "bob@example.com") == mu.EXIT_IO
    assert allowlist.read_text() == original, "a failed write must not corrupt the allowlist"
    leftovers = list(allowlist.parent.glob(".allowlist-*.tmp"))
    assert not leftovers, f"temp files left behind: {leftovers}"


# --------------------------------------------------------------------------- path parity with the gate
#
# `loadAllowlist` (web/lib/beta-access.ts) opens a file only when BETA_ALLOWLIST_FILE is a non-empty
# string — the gate has no default path. The CLI once fell back to /app/data/allowlist.txt, so with
# the variable unset it maintained (and `check` read back as ALLOW) a file the gate never consulted.
# Found live in the tree during the 2026-08-02 access investigation; these pin the repaired parity.


@pytest.mark.parametrize("env", [{}, {"BETA_ALLOWLIST_FILE": ""}],
                         ids=["unset", "set-but-empty (compose ${VAR:-} default)"])
def test_allowlist_path_mirrors_the_gates_file_resolution(env):
    """Empty means NO FILE on both sides — never a silently assumed default. The set-but-empty case
    is what every container gets when deploy/.env lacks the key."""
    assert mu.allowlist_path(None, env) is None
    assert mu.allowlist_path("/x/y.txt", env) == "/x/y.txt", "--file must still win"


@pytest.mark.parametrize("cmd", ["grant-access", "revoke-access"])
def test_writes_refuse_when_the_gate_reads_no_file(cmd, monkeypatch, capsys):
    """Writing to an assumed path would grant nothing; the tool must say so instead of succeeding."""
    monkeypatch.setenv("BETA_ALLOWLIST_FILE", "")
    monkeypatch.setenv("BETA_ACCESS_ENABLED", "1")
    monkeypatch.delenv("BETA_ALLOWLIST", raising=False)
    assert run(cmd, "alice@example.com") == mu.EXIT_IO
    err = capsys.readouterr().err
    assert "BETA_ALLOWLIST_FILE" in err and "no allowlist file" in err


def test_check_answers_from_env_only_when_no_file_is_configured(monkeypatch):
    """`check` must mirror the gate's sources exactly: no configured file ⇒ env entries decide."""
    monkeypatch.setenv("BETA_ALLOWLIST_FILE", "")
    monkeypatch.setenv("BETA_ACCESS_ENABLED", "1")
    monkeypatch.setenv("BETA_ALLOWLIST", "bob@example.com")
    assert run("check", "bob@example.com") == mu.EXIT_OK
    assert run("check", "alice@example.com") == mu.EXIT_DENIED


def test_list_access_names_file_mode_off(monkeypatch, capsys):
    monkeypatch.setenv("BETA_ALLOWLIST_FILE", "")
    monkeypatch.setenv("BETA_ACCESS_ENABLED", "1")
    monkeypatch.setenv("BETA_ALLOWLIST", "bob@example.com")
    assert run("list-access") == mu.EXIT_OK
    assert "file mode is OFF" in capsys.readouterr().out


def test_grant_promises_effect_only_for_the_file_the_gate_reads(allowlist, tmp_path, monkeypatch, capsys):
    """The takes-effect line was the lie the old fallback told. It may appear only when the written
    file IS the gate's file; an explicit --file elsewhere gets a warning naming the divergence."""
    run("grant-access", "alice@example.com")
    assert "takes effect on their next sign-in" in capsys.readouterr().out

    other = tmp_path / "other.txt"
    assert run("--file", str(other), "grant-access", "bob@example.com") == mu.EXIT_OK
    out = capsys.readouterr().out
    assert "takes effect" not in out
    assert "no effect until" in out and str(allowlist) in out


# --------------------------------------------------------------------------- shipped where documented


def test_the_cli_is_in_the_api_image():
    """`docs/BETA_ACCESS_CONTROL.md` tells operators to run this with `docker exec deploy-api-1`.
    Dockerfile.api copies only `rwe` and `examples` by default, so without an explicit COPY the
    documented command fails with "No such file or directory" — which is exactly how
    `capacity_report.py` failed in production earlier, and how a stale profile-gated image deadlocked
    a deploy. A tool is not shipped because a doc says to run it."""
    dockerfile = (ROOT / "deploy" / "Dockerfile.api").read_text()
    assert "COPY scripts ./scripts" in dockerfile, \
        "scripts/ must be copied into the api image or the documented docker exec cannot work"


def test_the_api_service_resolves_the_same_allowlist_file_as_the_web_gate():
    """The CLI runs in `api`; the gate runs in `web`. If only `web` receives BETA_ALLOWLIST_FILE the
    CLI falls back to its default and can silently maintain a file nothing consults — granting
    access that never takes effect."""
    compose = (ROOT / "deploy" / "docker-compose.aws.yml").read_text()
    api_block = compose.split("\n  api:", 1)[1].split("\n  web:", 1)[0]
    assert "BETA_ALLOWLIST_FILE" in api_block, \
        "the api service must receive BETA_ALLOWLIST_FILE so the CLI edits the file web reads"


def test_the_web_service_can_actually_read_the_allowlist_file():
    """The bug that made the first real grant fail.

    `BETA_ALLOWLIST_FILE` is documented as `/app/data/allowlist.txt`, and `web` is where the gate
    runs — but `web` had no volumes at all, so that path did not exist inside the container.
    `loadAllowlist` catches the read error by design (a missing file must not break sign-in), so the
    file-based allowlist silently did nothing: every grant was ignored and every tester saw
    "invite-only".

    It was invisible from both ends. The CLI runs in `api`, where /app/data IS mounted, so it
    reported success against a real file. The gate reported `empty_allowlist` without naming the
    file it could not read. Two containers, one path, no shared reality — the same shape as the
    profile-gated image skew that deadlocked a deploy earlier.

    Setting the env var without mounting the path is worse than leaving both unset, because it looks
    configured."""
    compose = (ROOT / "deploy" / "docker-compose.aws.yml").read_text()
    web_block = compose.split("\n  web:", 1)[1].split("\n  api:", 1)[0]
    assert "BETA_ALLOWLIST_FILE" in web_block, "the gate needs the path"
    assert "/app/data" in web_block, (
        "web must mount /app/data or BETA_ALLOWLIST_FILE points at a path that does not exist "
        "inside the container, and every file-based grant is silently ignored")
