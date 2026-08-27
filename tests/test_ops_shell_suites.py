"""Run the shell-driven ops tests as part of the Python suite.

`tests/test_build_cache_prune.sh` and `tests/test_backup_formats.sh` drive the real deploy scripts
against real fixtures — the only way to test shell that must not be re-implemented in Python. Both
were runnable only by hand, and a test nobody runs is the same thing as a test that does not exist:
this repository has now found that shape four times (a diagnostic with no caller, a bar whose premise
had expired, a gate that could not fire, a prune in a file the deploy never reached). Wiring them
here means CI notices.

They are skipped rather than failed when their prerequisites are missing, and the skip says which
one — a silent skip would reintroduce exactly the problem.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

_TESTS = pathlib.Path(__file__).resolve().parent
_SUITES = ("test_build_cache_prune.sh", "test_backup_formats.sh")


@pytest.mark.parametrize("script", _SUITES)
def test_shell_suite_passes(script):
    path = _TESTS / script
    if not path.exists():
        pytest.skip(f"{script} is not present")
    if shutil.which("bash") is None:
        pytest.skip("bash is not available")
    proc = subprocess.run(["bash", str(path)], capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        pytest.fail(f"{script} failed:\n{proc.stdout}\n{proc.stderr}")
