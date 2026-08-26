"""source_lifecycle.py — Stages 5 and 6 of the source pipeline: acting on M8's evidence.

**M9 of `docs/SCALE_ROADMAP.md`.** Pure policy: no store, no network, no environment, no writes. It
takes an outlet's current state, the verdict M8 reached, and how long that verdict has held, and
returns the transition that follows — or none.

## What M9 automates, and what it deliberately does not

M8 stops at evidence on purpose. M9 is the part that acts, and the first design question is *what
"act" can safely mean here.* Tier membership is not database state: it is `RWE_CORPUS_TIER_B` and
`RWE_CORPUS_SHADOW`, read from the environment by `corpus.tier_index`. That was M1's decision — tier
is a property of the outlet, derived at selection time, no article column and no migration.

So M9 **automates the decision and emits the configuration; it never mutates serving state.** The
state machine, the hysteresis and the ledger are automatic and durable; applying a transition is a
config change a human reviews and deploys. Three reasons, and none of them is timidity:

1. The roadmap already says Tier A promotion is "gated, manual, and permanently narrow" — bounded by
   rating throughput, which is a budget and not an algorithm. A pipeline that could promote into
   Tier A on its own would be contradicting the milestone it implements.
2. Every transition across the Tier A boundary **changes the story partition**, which is the one
   thing this repo never changes without a counterfactual.
3. A tier change is a deploy either way, because the value lives in the compose allowlist. Emitting
   it costs nothing and keeps a human in the loop for free.

## The rule that decides which transitions are automatic

> **Tier A's boundary is the only one that moves the partition, so every crossing of it — in either
> direction — needs the whole-corpus counterfactual and a human. Everything else is automatic.**

That is the same asymmetry the roadmap names as what makes 50,000 sources possible: a Tier B row
cannot alter what clusters, so admitting one needs no clustering bar. :func:`crosses_tier_a` is that
rule in one line, and every non-automatic transition in this module traces to it.

**The one exception is provable rather than argued.** An outlet silent longer than the clustering
window has no rows in the window, so moving it out of Tier A *cannot* change the build — there is
nothing of it there to remove. Dormancy from Tier A is therefore automatic, and
:func:`plan` refuses to apply that reasoning unless ``silent_days`` genuinely exceeds the window.

## Hysteresis: why two, and why it is not a threshold

A transition requires the same target on ``confirmations`` consecutive evaluations. This audit
series has had two invented thresholds die against data, so it is worth being precise that **this is
not one of them**: it is a repeat-measurement rule, not a quality bar. It asserts nothing about how
good an outlet is; it asserts that one sample is one sample.

The default of 2 is the smallest value that means anything, and it is the argument
`clustering.DEFAULT_MIN_SUPPORT` already makes in this codebase — one witness is an anecdote, two is
corroboration. It also costs nothing where the evidence is stable: M8's production verdicts were
identical across four consecutive runs.

## Retirement is not automatic, because nobody has measured the interval

Stage 6 says "no items in 30 days → dormant (daily probe), then retired" and gives no interval for
the second arrow. Dormancy is reversible, evidence-preserving and harmless, so it is automatic.
Retirement is neither reversible nor measured, so it stays a human action that this module records
and never initiates. Inventing "then retired after N" would be the third guess.
"""

from __future__ import annotations

from typing import NamedTuple, Optional

#: Lifecycle states. The first three are serving tiers and mirror ``corpus.TIERS``; the last two are
#: ledger-only today — ``dormant`` and ``retired`` describe an outlet we have stopped expecting items
#: from, and the probe-cadence change they imply belongs to the crawler (M6/M7), which is not built.
#: They are recorded now so the evidence exists when there is something to consume it.
STATES = ("shadow", "B", "A", "dormant", "retired")

#: Serving tiers, in increasing order of what they are allowed to do. ``shadow`` is stored and
#: surfaced nowhere, ``B`` is searchable and attributable but never clusters, ``A`` forms and votes
#: in stories.
TIER_ORDER = ("shadow", "B", "A")

#: Consecutive evaluations that must agree before a transition fires. See the module docstring: a
#: repeat-measurement rule, not a quality bar.
DEFAULT_CONFIRMATIONS = 2

#: Days without a new article before an outlet goes dormant. The roadmap's Stage 6 value. It is a
#: design value rather than a measured one, and :func:`plan` says so in the reason it returns.
DEFAULT_SILENT_DAYS = 30

#: What a human must supply before a Tier A crossing can be applied.
NEEDS_COUNTERFACTUAL = "clustering counterfactual (audit_clustering_change.py)"
NEEDS_LEAN = "a sourced lean — an unattributed rating is indistinguishable from a guess"


class Transition(NamedTuple):
    """A proposed move. ``automatic`` says whether M9 may apply it without a human.

    ``requires`` is never empty when ``automatic`` is False, so a blocked transition always says
    what would unblock it rather than simply refusing."""
    frm: str
    to: str
    automatic: bool
    reason: str
    requires: tuple = ()

    @property
    def is_move(self) -> bool:
        return self.frm != self.to


def crosses_tier_a(frm: str, to: str) -> bool:
    """Whether this move puts an outlet into, or takes it out of, the clustering corpus.

    **The whole automatic/manual split reduces to this.** Tier A is the only membership that changes
    what clusters, so it is the only one whose change needs the whole-corpus counterfactual — in
    both directions. Adding an outlet can merge stories that were separate; removing one can strand
    articles whose only link ran through it, which is exactly the bar
    ``audit_source_cohort`` reports as *"OTHER articles that LOST their story"*."""
    return (frm == "A") != (to == "A")


def target_for(verdict: str, state: str) -> Optional[str]:
    """The state a verdict points at, or ``None`` when it points nowhere.

    ``INSUFFICIENT DATA`` and ``INSUFFICIENT VOLUME`` return ``None`` deliberately, and it is the
    same rule ``audit_shadow_cohort.direction`` applies: they are not instructions, and a verdict
    that cannot be reached yet must not be dressed as one in either direction."""
    if verdict == "PROMOTE TO TIER B":
        return "B"
    if verdict == "TIER A CANDIDATE":
        return "A"
    if verdict == "REJECT":
        # One step DOWN from wherever it is — a confirmed republisher in Tier A becomes Tier B (the
        # roadmap's "it became a syndicator"), and one in Tier B is withheld from readers while it is
        # re-examined. Never below shadow: shadow already surfaces nowhere, and there is nothing
        # further down that is still reversible.
        if state in TIER_ORDER:
            i = TIER_ORDER.index(state)
            return TIER_ORDER[max(0, i - 1)]
        return None
    return None


def plan(state: str, verdict: str, *, streak: int = 1,
         days_silent: Optional[float] = None,
         confirmations: int = DEFAULT_CONFIRMATIONS,
         silent_days: int = DEFAULT_SILENT_DAYS,
         window_days: float = 6.0,
         rated: bool = False) -> Optional[Transition]:
    """The transition that follows, or ``None`` when nothing does.

    ``streak`` is how many consecutive evaluations have pointed at the same target — the hysteresis
    the module docstring justifies. ``days_silent`` is days since the outlet's newest article;
    ``window_days`` is the clustering window, and it is a parameter because the dormancy argument
    below is only valid when ``silent_days`` exceeds it.

    Order matters: silence is read before the verdict. An outlet that has published nothing for a
    month has no current evidence to evaluate, and a verdict computed from its last few articles
    describes a source that has stopped."""
    if state not in STATES:
        raise ValueError(f"unknown lifecycle state: {state!r}")

    # ---------------------------------------------------------------- silence
    if days_silent is not None and silent_days and days_silent >= silent_days:
        if state in TIER_ORDER:
            # Provable, not argued: nothing published in `silent_days` can appear in a window of
            # `window_days`, so this outlet contributes no rows to the build and removing it from
            # Tier A cannot change the partition. The guard is what keeps that true — if the
            # dormancy interval were shorter than the window, the outlet WOULD still have rows in
            # it and this would be a silent partition change.
            provable = silent_days > window_days
            return Transition(
                state, "dormant", automatic=provable,
                reason=(f"no article in {days_silent:.0f}d (threshold {silent_days}d, a roadmap "
                        f"design value, not a measured one)"),
                requires=() if provable else (
                    f"silent_days ({silent_days}) must exceed the clustering window "
                    f"({window_days:g}) for dormancy to be partition-neutral", ))
        return None

    # ---------------------------------------------------------------- resumption
    if state == "dormant" and days_silent is not None and days_silent < silent_days:
        # Reversible by default. The ledger holds what it was, so this restores rather than guesses;
        # the caller supplies the prior state as `state` is only "dormant" here.
        return Transition(state, "shadow", automatic=True,
                          reason=(f"publishing again ({days_silent:.0f}d since the last article) — "
                                  f"re-enters evaluation rather than its old tier, because the "
                                  f"evidence that justified that tier is now stale"))

    if state == "retired":
        return None                     # a human retired it; a human un-retires it

    # ---------------------------------------------------------------- verdict
    to = target_for(verdict, state)
    if to is None or to == state:
        return None
    if streak < confirmations:
        return Transition(state, state, automatic=False,
                          reason=(f"points at {to}, but on {streak} of {confirmations} consecutive "
                                  f"evaluations — one sample is one sample"),
                          requires=(f"{confirmations - streak} more evaluation(s) agreeing", ))

    if crosses_tier_a(state, to):
        needs = [NEEDS_COUNTERFACTUAL]
        if to == "A" and not rated:
            needs.append(NEEDS_LEAN)
        direction = "into" if to == "A" else "out of"
        return Transition(state, to, automatic=False,
                          reason=(f"{verdict} confirmed on {streak} consecutive evaluations, but "
                                  f"this moves {direction} the clustering corpus and changes the "
                                  f"story partition"),
                          requires=tuple(needs))

    return Transition(state, to, automatic=True,
                      reason=(f"{verdict} confirmed on {streak} consecutive evaluations; neither "
                              f"side of this move clusters, so the partition cannot change"))
