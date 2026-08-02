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
