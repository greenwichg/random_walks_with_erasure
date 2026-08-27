"""Bar 1's premise has to be enforced, not assumed.

`audit_corpus_boundary.py`'s first bar asserts that the corpus boundary is *"a no-op with nothing
configured"* — with tiering off, `corpus.select()` must return the caller's own list, not a copy,
because off must cost nothing.

It read the LIVE environment to do that, which held for as long as both tier lists were empty — the
shipped state, and what every earlier run of the script saw. M7 put
``RWE_CORPUS_SHADOW=kait8.com,kwch.com`` on production on 2026-08-26, and the next run reported:

    *** FAIL: select() returned a new list while tiering is off

about a box where tiering was demonstrably on (`tiering configured : True`, two lines above it in
the same output). The assertion was still correct. Its **premise** had expired — the mirror of the
failure mode this series keeps finding, where a gate that cannot fire reads as a gate that passed.

`rows in` and `rows out` were both 27,764, so nothing was being dropped — only copied. Purely the
short-circuit not being taken, exactly as designed.

It is not cosmetic: `audit_shadow_cohort.py` instructs operators to run this script *"before
promoting anything"*, so a permanently-failing bar sits in M9's promotion path, and a check that
always fails is one people learn to skip.
"""
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import corpus  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("RWE_CORPUS_TIER_B", "RWE_CORPUS_SHADOW"):
        monkeypatch.delenv(k, raising=False)
    yield


ROWS = [{"publisher": "kait8.com", "url": "https://www.kait8.com/2026/08/26/x/", "id": 1},
        {"publisher": "BBC", "url": "https://bbc.co.uk/a", "id": 2}]


def _select():
    return corpus.select(ROWS, total=len(ROWS), cap=100, log=lambda *a, **k: None)


def test_off_really_does_cost_nothing_which_is_what_the_bar_asserts():
    assert corpus.enabled() is False
    assert _select() is ROWS, "the short-circuit is the property Bar 1 exists to protect"


def test_and_configured_tiering_really_does_copy_which_is_why_the_bar_broke(monkeypatch):
    """Both halves, so the diagnosis is pinned rather than asserted. The FAIL was not a regression
    in `select()` — it was the bar measuring the ON case and reporting it as the OFF case."""
    monkeypatch.setenv("RWE_CORPUS_SHADOW", "kait8.com")
    assert corpus.enabled() is True
    assert _select() is not ROWS


def test_the_bar_clears_the_tier_vars_and_puts_them_BACK(monkeypatch):
    """The restore matters as much as the clear: Bar 2 and Bar 3 run after this one against the
    same process, and a bar that silently disarmed production's tier configuration for the rest of
    the run would make every later bar measure the wrong thing."""
    monkeypatch.setenv("RWE_CORPUS_SHADOW", "kait8.com,kwch.com")
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "example.com")

    saved = {k: os.environ.pop(k, None) for k in ("RWE_CORPUS_TIER_B", "RWE_CORPUS_SHADOW")}
    try:
        assert corpus.enabled() is False, "cleared for the bar"
        assert _select() is ROWS, "so the bar tests what it claims to test"
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

    assert os.environ["RWE_CORPUS_SHADOW"] == "kait8.com,kwch.com"
    assert os.environ["RWE_CORPUS_TIER_B"] == "example.com"
    assert corpus.enabled() is True, "and the process is left exactly as it was found"


def test_the_audit_source_actually_does_this(monkeypatch):
    """Structural backstop. The three tests above pin the BEHAVIOUR of the pattern; this one pins
    that the audit still uses it, so restoring the live env cannot be quietly dropped."""
    src = (ROOT / "examples" / "audit_corpus_boundary.py").read_text()
    bar1 = src.split("=== BAR 1")[1].split("=== BAR 2")[0]
    assert "os.environ.pop" in bar1, "Bar 1 must clear the tier vars"
    assert "finally:" in bar1, "and restore them even if select() raises"
    assert "corpus.enabled()" in bar1, "and check the premise actually holds"
