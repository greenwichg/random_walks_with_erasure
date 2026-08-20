"""Deploy-config hygiene — the two guards added after the 2026-08-02 access incident.

1. Duplicate keys in ``deploy/.env``. docker compose keeps only the LAST line of a key, so an
   appended "grant" line silently discarded every earlier ``BETA_ALLOWLIST`` line — including the
   operator's own address, who was then denied ``not_allowlisted`` by their own beta. The ops
   helpers now warn on every entry point (``need_env``); these tests drive the warner directly
   through bash, the way operators run it.

2. Durable audit logs. ``docker logs`` spans one container, and every deploy recreates containers —
   the investigation above was blind for exactly the two days of ``beta_access_denied`` lines it
   needed. Production pins the journald logging driver so audit lines outlive deployments; the
   assertion here is the same "shipped where documented" style as test_manage_users.py's compose
   checks, because a driver only present in a doc protects nothing.
"""
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run_warner(env_file: pathlib.Path):
    """Source the real helper and run the warner, exactly as the ops scripts do."""
    return subprocess.run(
        ["bash", "-c", "source deploy/ops/_compose.sh && warn_env_dups"],
        cwd=ROOT, env={"PATH": "/usr/bin:/bin", "IH_ENV_FILE": str(env_file)},
        capture_output=True, text=True, timeout=30,
    )


def test_a_clean_env_file_warns_about_nothing(tmp_path):
    f = tmp_path / ".env"
    f.write_text("A=1\nB=2\n# comment\nC=x y z\n")
    r = run_warner(f)
    assert r.returncode == 0
    assert r.stderr == ""


def test_duplicate_keys_are_named_with_their_line_counts(tmp_path):
    f = tmp_path / ".env"
    f.write_text("BETA_ALLOWLIST=a@x.com\nOTHER=1\nBETA_ALLOWLIST=b@y.com\nBETA_ALLOWLIST=c@z.com\n")
    r = run_warner(f)
    assert r.returncode == 0, "warn-only: hygiene must never block an operation"
    assert "duplicate keys" in r.stderr
    assert "BETA_ALLOWLIST" in r.stderr and "3 lines" in r.stderr
    assert "OTHER" not in r.stderr, "unique keys are not noise"


def test_the_warning_never_prints_values_because_the_file_holds_secrets(tmp_path):
    """The env-file carries NEXTAUTH_SECRET and friends. A hygiene warning that echoed values would
    leak them into every pasted terminal log — the warning names keys and counts, nothing else."""
    f = tmp_path / ".env"
    f.write_text("NEXTAUTH_SECRET=hunter2-super-secret\nNEXTAUTH_SECRET=hunter3-rotated\n")
    r = run_warner(f)
    assert "NEXTAUTH_SECRET" in r.stderr
    assert "hunter2" not in r.stderr and "hunter3" not in r.stderr


def test_need_env_runs_the_duplicate_check_on_every_ops_entry_point(tmp_path):
    """The warner is only worth having if operators cannot miss it: need_env is the one gate every
    ops wrapper (deploy/update/restart/restore/smoke-test) already passes through."""
    f = tmp_path / ".env"
    f.write_text("K=1\nK=2\n")
    r = subprocess.run(
        ["bash", "-c", "source deploy/ops/_compose.sh && need_env"],
        cwd=ROOT, env={"PATH": "/usr/bin:/bin", "IH_ENV_FILE": str(f)},
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    assert "duplicate keys" in r.stderr and "K" in r.stderr


# --------------------------------------------------------------------------- durable audit logs


def _service_block(compose: str, name: str) -> str:
    block = compose.split(f"\n  {name}:", 1)[1]
    # cut at the next top-level service (two-space indent + word + colon), crudely but stably
    for marker in ("\n  web:", "\n  api:", "\n  caddy:", "\n  backup", "\nvolumes:"):
        if marker != f"\n  {name}:" and marker in block:
            block = block.split(marker, 1)[0]
    return block


def test_production_pins_journald_so_audit_lines_survive_deploys():
    compose = (ROOT / "deploy" / "docker-compose.aws.yml").read_text()
    for svc in ("web", "api"):
        block = _service_block(compose, svc)
        assert "journald" in block, (
            f"the {svc} service must keep the journald logging driver: json-file logs die with the "
            f"container on every deploy, which erased the beta_access_denied audit trail the "
            f"2026-08-02 access investigation needed")


def test_compose_defaults_the_verified_link_quorum():
    """The adopted clustering linkage must be the DEFAULT, not an env-file override.

    `RWE_CLUSTER_LINK_QUORUM=0.2` was adopted and twice verified against the live catalog
    (docs/STORY_CLUSTER_QUORUM_VERIFICATION.md) — largest cluster 196 -> 67, loose members -88%,
    chain depth >= 5 -93%. Through that whole sequence the compose default stayed at 0, so the
    verified configuration existed only in `deploy/.env`: a lost or regenerated env file silently
    reverts production to the single-linkage chaining that merged a US-Iran war, a mass shooting,
    tariffs and a funeral into one 336-article "story".

    Pinned here rather than in the clustering tests on purpose. `clustering.DEFAULT_LINK_QUORUM`
    and `story_service.link_quorum()` are LIBRARY defaults and stay 0.0 — the research package has
    callers that are not this deployment. What must not drift is what the CONTAINER gets.
    """
    import pathlib
    import re
    compose = (pathlib.Path(__file__).resolve().parent.parent
               / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    m = re.search(r"RWE_CLUSTER_LINK_QUORUM:\s*\$\{RWE_CLUSTER_LINK_QUORUM:-([^}]*)\}", compose)
    assert m, "RWE_CLUSTER_LINK_QUORUM is not in the compose environment allowlist"
    assert float(m.group(1)) == 0.2, (
        f"compose defaults the link quorum to {m.group(1)!r}; the verified value is 0.2"
    )


def test_compose_keeps_idf_off():
    """`RWE_CLUSTER_IDF` stays 0. It was briefly enabled and REVERTED on measurement: 361 of 3,431
    covered articles (10.5%) fell out of stories entirely, and only 16% of that loss was the
    press-release templates the weighting was meant to punish — the rest was real stories shedding
    real coverage (Nolan Wells autopsy -12 of 58, French wildfires -9 of 75, Berlin pride -6 of 66).

    Pinned because the headline numbers READ well (766 -> 777 stories, largest 194 -> 93) and invite
    exactly that re-adoption; `story_service.use_idf()` documents why they mislead. Coverage is the
    binding constraint on Story Continuation, Coverage Comparison and the feed's story slot, so this
    would cost the three features that need it most.
    """
    import pathlib
    import re
    compose = (pathlib.Path(__file__).resolve().parent.parent
               / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    m = re.search(r"RWE_CLUSTER_IDF:\s*\$\{RWE_CLUSTER_IDF:-([^}]*)\}", compose)
    assert m, "RWE_CLUSTER_IDF is not in the compose environment allowlist"
    assert m.group(1).strip() in ("", "0"), (
        f"compose defaults IDF to {m.group(1)!r}; the measured revert says it must stay off"
    )


# --------------------------------------------------------------------------- #
# 3. The email-config writer. The same 2026-08-02 failure, approached from the other
#    side: the warner NOTICES duplicates after the fact; this refuses to create them.
# --------------------------------------------------------------------------- #
def run_configure(env_file: pathlib.Path, *args, password="abcd efgh ijkl mnop"):
    """Drive the writer exactly as an operator does, with the password off the command line."""
    return subprocess.run(
        ["bash", "deploy/ops/configure-email.sh", *args],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "IH_ENV_FILE": str(env_file),
             "RWE_SMTP_PASSWORD_INPUT": password},
    )


def _seed(tmp_path) -> pathlib.Path:
    f = tmp_path / ".env"
    f.write_text("RWE_ENV=production\nNEXTAUTH_URL=https://hidden-view.com\n"
                 "NEXTAUTH_SECRET=hunter2-super-secret\n")
    return f


def _values(env_file: pathlib.Path) -> dict:
    out = {}
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v.strip().strip('"')
    return out


def test_the_writer_fills_the_block_from_one_address(tmp_path):
    f = _seed(tmp_path)
    r = run_configure(f, "you@gmail.com")
    assert r.returncode == 0, r.stderr
    v = _values(f)
    assert v["RWE_EMAIL_ENABLED"] == "1"
    assert v["RWE_SMTP_USER"] == "you@gmail.com"
    assert v["RWE_EMAIL_FROM"] == "Hidden View <you@gmail.com>"
    assert v["RWE_EMAIL_ALLOWLIST"] == "you@gmail.com", "beta scope: the sender, and only them"
    assert len(v["RWE_EMAIL_SECRET"]) >= 40, "a real generated secret, not a placeholder"
    # Taken from what the web tier already serves rather than invented: an unsubscribe link on the
    # wrong host is a link that 404s inside someone's mail client.
    assert v["RWE_PUBLIC_URL"] == "https://hidden-view.com"
    assert v["NEXTAUTH_SECRET"] == "hunter2-super-secret", "unrelated keys are untouched"


def test_the_writer_refuses_to_append_a_second_line_for_a_key(tmp_path):
    """The 2026-08-02 failure, prevented rather than reported.

    Compose keeps the LAST occurrence of a key and silently ignores the earlier ones, so a re-run
    of the same paste looks additive and is destructive. Refusing is right rather than merely
    warning: unlike the general hygiene warner, this tool is the thing about to write the file."""
    f = _seed(tmp_path)
    assert run_configure(f, "you@gmail.com").returncode == 0
    before = f.read_text()

    again = run_configure(f, "someone.else@gmail.com")
    assert again.returncode == 1, "a second append must fail, not shadow the first"
    assert "REFUSING" in again.stderr and "2026-08-02" in again.stderr
    assert "--replace" in again.stderr, "the refusal must name the way forward"
    assert f.read_text() == before, "a refusal must not have written anything"


def test_replace_rewrites_in_place_and_leaves_no_duplicates(tmp_path):
    f = _seed(tmp_path)
    run_configure(f, "you@gmail.com")
    secret = _values(f)["RWE_EMAIL_SECRET"]

    r = run_configure(f, "you@gmail.com", "--allowlist", "*", "--replace")
    assert r.returncode == 0, r.stderr
    text = f.read_text()
    keys = [ln.split("=", 1)[0] for ln in text.splitlines()
            if "=" in ln and not ln.startswith("#")]
    assert len(keys) == len(set(keys)), f"--replace created duplicates: {keys}"
    assert _values(f)["RWE_EMAIL_ALLOWLIST"] == "*"
    assert _values(f)["RWE_EMAIL_SECRET"] == secret, (
        "rotating the secret would invalidate every unsubscribe link already sitting in an inbox")


def test_the_writer_never_echoes_the_password_or_the_secret(tmp_path):
    """Its output is meant to be pasted into a ticket. The file it writes holds NEXTAUTH_SECRET
    too, so a writer that echoed what it wrote would be worse than the hand-edit it replaces."""
    f = _seed(tmp_path)
    r = run_configure(f, "you@gmail.com", password="hunter2-app-pw")
    combined = r.stdout + r.stderr
    assert "hunter2-app-pw" not in combined
    assert _values(f)["RWE_EMAIL_SECRET"] not in combined
    assert "characters, hidden" in combined, "reported as a length, so a typo is still visible"


def test_a_dry_run_writes_nothing(tmp_path):
    f = _seed(tmp_path)
    before = f.read_text()
    r = run_configure(f, "you@gmail.com", "--dry-run")
    assert r.returncode == 0 and f.read_text() == before
    assert "RWE_EMAIL_FROM" in r.stdout, "it should still show what it would have written"


@pytest.mark.parametrize("password,stored", [
    ("abcd efgh ijkl mnop", "abcdefghijklmnop"),   # Gmail shows four groups of four
    ("pa#ss word", "pa#ssword"),                    # a `#` must be quoted, not truncate the line
])
def test_the_password_survives_the_env_file(tmp_path, password, stored):
    """An unquoted `#` starts a comment: a 16-character app password silently becomes 4, and the
    only symptom is an authentication failure that looks like a wrong password. Gmail's display
    spaces are presentation, so they are stripped rather than quoted."""
    f = _seed(tmp_path)
    assert run_configure(f, "you@gmail.com", password=password).returncode == 0
    read_back = subprocess.run(
        ["bash", "-c", "source deploy/ops/_compose.sh && env_val RWE_SMTP_PASSWORD"],
        cwd=ROOT, env={"PATH": "/usr/bin:/bin", "IH_ENV_FILE": str(f)},
        capture_output=True, text=True, timeout=30)
    assert read_back.stdout == stored, "what the deploy scripts read back must be what was meant"
