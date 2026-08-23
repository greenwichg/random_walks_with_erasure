"""Every Tier-1 recommendation flag must be deployable — declared in the compose allowlist.

The production stack passes environment through an explicit ``environment:`` allowlist and has no
``env_file:`` (docker-compose.yml says so in as many words), so a variable the engine reads but
compose does not declare is a flag that can never reach the container. That is not hypothetical
twice over: RWE_STORY_SLOT's own comment records the first occurrence, and the Tier-1 enablement
repeated it — the flags were set in deploy/.env, compose detected no config change, did not even
recreate the api container, and the built, tested, smoke-passed feature was structurally
undeployable. verify-recs.sh caught it in production (env-file '1' vs container 'unset'); this
test catches it in CI, before a deploy exists to catch.

Pinned by NAME, not by a source sweep: most engine-read RWE_* vars are legitimately absent from
the base compose (they live in docker-compose.aws.yml, belong to CLI tools, or are dev-only), so
a general sweep needs an allowlist of deliberate absences that would itself rot. Six names, one
invariant each.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent

TIER1_FLAGS = (
    "RWE_REC_FEEDBACK",
    "RWE_REC_REPETITION",
    "RWE_REC_REPEAT_WINDOW_DAYS",
    "RWE_REC_MAX_PER_STORY",
    "RWE_REC_MAX_PER_TOPIC",
    "RWE_REC_BLINDSPOT",
)


def test_every_tier1_flag_is_declared_in_the_compose_allowlist():
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    declared = set(re.findall(r"^\s+(RWE_[A-Z0-9_]+):", compose, re.M))
    missing = [f for f in TIER1_FLAGS if f not in declared]
    assert not missing, (
        f"{missing} read by the engine but absent from docker-compose.yml's api environment "
        f"allowlist — the flag can never reach the container, so the feature is structurally "
        f"undeployable (the RWE_STORY_SLOT failure mode, third time)."
    )


def test_every_tier1_flag_defaults_off_or_neutral_in_compose():
    # The compose default is what a deployment WITHOUT a deploy/.env line gets. The Tier-1
    # contract is dark-by-default; the window is a neutral tunable, not a switch.
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    defaults = dict(re.findall(r"^\s+(RWE_REC_[A-Z0-9_]+):\s*\$\{RWE_REC_[A-Z0-9_]+:-([^}]*)\}",
                               compose, re.M))
    for flag in ("RWE_REC_FEEDBACK", "RWE_REC_REPETITION",
                 "RWE_REC_MAX_PER_STORY", "RWE_REC_MAX_PER_TOPIC", "RWE_REC_BLINDSPOT"):
        assert defaults.get(flag) == "0", (
            f"{flag} compose default is {defaults.get(flag)!r} — must be '0' (dark by default)")
    assert defaults.get("RWE_REC_REPEAT_WINDOW_DAYS") == "14", (
        "the repeat window's compose default must match rec_context.repeat_window_days()")
