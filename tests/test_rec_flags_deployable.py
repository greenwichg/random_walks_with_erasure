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

# The Tier-2 sources + experiment harness — declared with the feature this time, so the third
# occurrence of the failure mode never ships at all.
TIER2_FLAGS = (
    "RWE_REC_STORY_SOURCE",
    "RWE_REC_EMERGING",
    "RWE_REC_BLINDSPOT_SLOTS",
    "RWE_REC_EXPERIMENT",
    "RWE_REC_SHADOW",
    # The corpus subsample's recency half-life. Same invariant, and it earns its line here for the
    # same reason the others do: the engine reads it in `simulate_users.recency_halflife_days`, so
    # a compose allowlist that omits it makes the knob unreachable from deploy/.env — the flag
    # would read 0 in the container no matter what an operator set, and the feed would stay as old
    # as it was while every check said the feature had shipped.
    "RWE_REC_RECENCY_HALFLIFE_DAYS",
)


def test_every_tier1_flag_is_declared_in_the_compose_allowlist():
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    declared = set(re.findall(r"^\s+(RWE_[A-Z0-9_]+):", compose, re.M))
    missing = [f for f in TIER1_FLAGS + TIER2_FLAGS if f not in declared]
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


def test_every_tier2_flag_defaults_dark_in_compose():
    # Same dark-by-default contract: source slot budgets default to 0 slots, and the experiment /
    # shadow specs default to empty (no experiment declared, nothing shadow-logged).
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    defaults = dict(re.findall(r"^\s+(RWE_REC_[A-Z0-9_]+):\s*\$\{RWE_REC_[A-Z0-9_]+:-([^}]*)\}",
                               compose, re.M))
    for flag in ("RWE_REC_STORY_SOURCE", "RWE_REC_EMERGING", "RWE_REC_BLINDSPOT_SLOTS"):
        assert defaults.get(flag) == "0", (
            f"{flag} compose default is {defaults.get(flag)!r} — must be '0' (no slots, dark)")
    for flag in ("RWE_REC_EXPERIMENT", "RWE_REC_SHADOW"):
        assert defaults.get(flag) == "", (
            f"{flag} compose default is {defaults.get(flag)!r} — must be empty (nothing declared)")


def test_the_recency_half_life_ships_at_fourteen_days():
    """The one rec flag that is deliberately ON by default, so the number is pinned.

    Every other flag here is dark-by-default because it changes what the reader is shown in a way
    somebody must opt into. This one is different: OFF is not a neutral state, it is the uniform
    draw over the whole retention window that served 5-14+ day-old articles in the first place.
    Leaving it off ships the reported bug; the value is therefore part of the contract.

    Fourteen days is where the live catalogue's trade turns (64,124 rows, 0-28.4 days): it takes
    the median served age from 6.2d to 3.7d and puts two thirds of the feed under a week, for a
    7.8% drop in distinct publishers. Every step tighter costs roughly twice as much breadth per
    day of freshness bought — 7d gives up 14.4% of publishers, 3d gives up 26.6% — and this
    product exists to WIDEN a reading diet. Change it from the live measurement
    (examples/rec_age_probe.py), not from taste; a fixture will tell you diversity holds, because
    a fixture has no rare-and-dormant outlets for the weighting to remove. And if you do change
    it, change it here too, which is the point of pinning it.
    """
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    m = re.search(r"^\s+RWE_REC_RECENCY_HALFLIFE_DAYS:\s*\$\{RWE_REC_RECENCY_HALFLIFE_DAYS:-([^}]*)\}",
                  compose, re.M)
    assert m, "the recency half-life must stay an overridable ${VAR:-default}, not a literal"
    assert m.group(1) == "14", (
        f"shipped recency half-life is {m.group(1)!r}, expected '14'. 0 would restore the uniform "
        f"draw over the whole retention window — the behaviour this flag exists to fix.")


def test_every_container_path_this_repo_tells_you_to_run_exists_in_the_image():
    """A documented command that cannot execute is worse than no command.

    `rec-age-probe.py` shipped under deploy/ops/ with instructions to run it as
    `/app/deploy/ops/rec-age-probe.py`. deploy/Dockerfile.api copies rwe, examples and scripts —
    never deploy/ — so that path does not exist in the api container and never could. The probe
    was correct; the only way to reach it was not.

    So the rule is checked instead of remembered: every `python <path>` this repo tells an operator
    to run inside the container must name a path the image actually contains.
    """
    dockerfile = (ROOT / "deploy" / "Dockerfile.api").read_text(encoding="utf-8")
    copied = set(re.findall(r"^COPY\s+(?:\S+\s+)*?(\S+)\s+\./?(\S*)$", dockerfile, re.M))
    roots = {(dst.strip("./") or src.strip("./")) for src, dst in copied}
    assert {"examples", "rwe", "scripts"} <= roots, f"unexpected image layout: {roots}"

    sources = [ROOT / "deploy" / "docker-compose.yml", ROOT / "examples" / "simulate_users.py",
               ROOT / "examples" / "rec_age_probe.py", pathlib.Path(__file__)]
    bad = []
    for path in sources:
        for m in re.finditer(r"(?:python3?\s+)(/app/)?((?:examples|deploy|scripts|rwe)/[\w./-]+\.py)",
                             path.read_text(encoding="utf-8")):
            target = m.group(2)
            if target.split("/", 1)[0] not in roots:
                bad.append(f"{path.name}: {m.group(0).strip()}")
            elif not (ROOT / target).exists():
                bad.append(f"{path.name}: {target} does not exist in the repo")
    assert not bad, (
        "these documented commands name a path the api image does not contain, so an operator "
        f"following them gets 'No such file or directory': {bad}")
