"""The scheduled discovery pass — `deploy/ops/discover-sources.sh`.

Run as a SCRIPT against a temporary env-file, not by reading its source. A cron job's failure mode
is silence, and every guard here exists because a previous cron in this repository failed quietly:
the unreadable env-file that makes every setting read as empty, the switch that has to be explicit,
and the /var/log redirect that fails before the script is reached.
"""
from __future__ import annotations

import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "ops" / "discover-sources.sh"


def _run(env_file, **extra):
    env = {**os.environ, "IH_ENV_FILE": str(env_file), **extra}
    return subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)


def _env(tmp_path, body: str):
    p = tmp_path / "env"
    p.write_text(body, encoding="utf-8")
    return p


def test_the_script_is_syntactically_valid_and_executable():
    assert os.access(SCRIPT, os.X_OK), "cron would not be able to run it"
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_it_does_nothing_until_the_switch_is_set(tmp_path):
    """Installed by bootstrap on every host. A cron that ran the moment it was installed would put
    an outbound publisher-facing job on a box nobody had configured for it."""
    r = _run(_env(tmp_path, "APP_DOMAIN=x\n"))
    assert r.returncode == 0
    assert "RWE_DISCOVERY_CRON is not 1" in r.stdout


def test_a_zero_and_a_typo_are_both_off(tmp_path):
    """Exactly ``1`` enables it. `true`/`yes` are the spellings an operator reaches for when a flag
    looks boolean, and reading them as on would turn a typo into outbound traffic.

    ``"1 "`` is deliberately NOT in this list: `_compose.sh`'s `env_val` trims trailing whitespace,
    so a stray space still enables — which is the right behaviour and the reason that trimming
    exists (a false RESTART NEEDED from an inline comment, 2026-07-26)."""
    for value in ("0", "true", "yes", "", "on", "enabled"):
        r = _run(_env(tmp_path, f"RWE_DISCOVERY_CRON={value}\n"))
        assert "is not 1" in r.stdout, f"{value!r} enabled the pass"


def test_an_unreadable_env_file_is_a_loud_failure_not_a_quiet_success(tmp_path):
    """THE cron-user trap. deploy/.env is written by root; the cron line runs as `ubuntu`. If the
    mode does not let the cron user read it, every `env_val` returns empty — so the switch reads as
    "not enabled" and the pass exits 0 hourly, forever, looking healthy."""
    if os.geteuid() == 0:
        import pytest
        pytest.skip("root can read anything — the trap cannot be reproduced")
    p = _env(tmp_path, "RWE_DISCOVERY_CRON=1\n")
    p.chmod(0o000)
    try:
        r = _run(p)
        assert r.returncode == 1, "an unreadable env-file exited 0 — the failure is silent"
        assert "cannot READ" in r.stderr and "chmod 640" in r.stderr
    finally:
        p.chmod(0o600)


def test_the_pass_never_admits():
    """Admission changes the partition readers see. `store.admit_source` refuses one on live rows
    without an explicit acknowledgement, and a cron cannot give one — so the schedulable pass fills
    the queue and validates it, and a person decides what serves.

    Source-level, because the assertion is about a command that must NOT be there."""
    src = SCRIPT.read_text()
    lines = [l for l in src.splitlines()
             if "campaign admit" in l or "source_campaign.py admit" in l]
    for line in lines:
        stripped = line.strip()
        assert stripped.startswith("#") or stripped.startswith("echo"), (
            f"the scheduled pass invokes admit: {stripped!r}")


def test_the_probe_batch_is_bounded():
    """The outbound budget, and it has to be in the command rather than in a comment."""
    src = SCRIPT.read_text()
    assert 'campaign probe --limit "$PROBE_LIMIT"' in src, \
        "an unbounded probe puts the whole candidate queue on one publisher-facing burst"
    assert 'PROBE_LIMIT="${PROBE_LIMIT:-20}"' in src, "no default bound"


def test_the_web_channel_is_skipped_when_no_provider_is_configured(tmp_path):
    """A half-configured provider must not produce an hourly failed search."""
    r = _run(_env(tmp_path, "RWE_DISCOVERY_CRON=1\nRWE_DISCOVERY_CHANNELS=web\n"))
    assert "web SKIPPED" in r.stdout, r.stdout + r.stderr


def test_an_unschedulable_channel_says_so_rather_than_failing(tmp_path):
    """`directory` imports a named file, so there is nothing for a recurring job to do."""
    r = _run(_env(tmp_path, "RWE_DISCOVERY_CRON=1\nRWE_DISCOVERY_CHANNELS=directory\n"))
    assert "not schedulable" in r.stderr, r.stdout + r.stderr


def test_bootstrap_installs_the_cron_and_pipes_it_to_logger():
    """/var/log is root-owned and the cron line runs as the target user, so a `>>` redirect fails
    at the shell before the script is reached and cron mails the error to a mailbox nobody reads."""
    src = (ROOT / "deploy" / "ops" / "bootstrap-ec2.sh").read_text()
    assert "/etc/cron.d/ih-discover" in src
    assert "discover-sources.sh 2>&1 | logger -t ih-discover" in src
    assert ">> /var/log/ih-discover" not in src
