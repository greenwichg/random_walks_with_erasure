"""Closed-loop exposure guardrails: detect backfire and dial the dose back.

These turn the "measure it" safeguards into running code. Each guardrail is a
per-user controller that watches a signal and cuts a user's **dose** (how far
opposite content is pushed) to keep exposure in the safe, depolarizing zone:

* :class:`BackfireMonitor` — a *lagging* guardrail driven by **ideology drift**
  (``|theta_new| - |theta_old|``): if a user's position is becoming more extreme,
  cut their dose. In production the drift comes from re-running
  :class:`rwe.ideology.IdeologyModel` across time windows.
* :class:`EngagementGuardrail` — a *leading* guardrail driven by **engagement /
  satisfaction**: if a user is no longer comfortably engaging with what they are
  shown (their dwell / satisfaction drops), cut their dose *before* the position
  drifts. In production the signal is the satisfaction score
  (:mod:`rwe.satisfaction`) or dwell / click logs.

A controller is a pure ``step(dose, signal) -> new_dose``, so it can drive either
the simulated environment here or a real system. :func:`compare_guardrails` runs
them against the assimilation–contrast opinion model
(:mod:`rwe.opinion_dynamics`) and shows that an un-guarded aggressive dose
backfires (polarizes), while both guardrails detect it and pull the dose down
into the depolarizing zone.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .opinion_dynamics import OpinionParams, polarization, update


@dataclass
class GuardrailParams:
    """Controller settings.  ``dose`` is the distance of opposing content from a
    user (the bridging "stretch"); larger = more aggressive = more backfire risk."""

    dose0: float = 2.2       # initial (deliberately aggressive) per-user dose
    dose_min: float = 0.2
    dose_max: float = 2.5
    cut: float = 0.6         # multiply dose by this when the guardrail fires
    drift_tol: float = 0.01  # |drift| below this counts as "no meaningful drift"
    engage_low: float = 0.5  # engagement below this = the user is bouncing out
    engage_drop: float = 0.03  # round-over-round engagement drop that trips a warning


def comfort(dose: np.ndarray, op: OpinionParams) -> np.ndarray:
    """Engagement / comfort proxy in ``[0, 1]`` (a stand-in for the satisfaction
    / dwell signal): 1 = content right at the user, 0 = at the rejection edge."""
    return np.clip(1.0 - np.asarray(dose) / op.latitude_reject, 0.0, 1.0)


# --------------------------------------------------------------------------- #
# Controllers (pure: current per-user dose + signal -> new dose)
# --------------------------------------------------------------------------- #
class BackfireMonitor:
    """Lagging guardrail: cut a user's dose unless they are *assimilating*
    (drifting toward the centre).  Signal = ideology drift per user."""

    def __init__(self, gp: GuardrailParams | None = None):
        self.gp = gp or GuardrailParams()

    def reset(self):
        pass

    def step(self, dose: np.ndarray, drift: np.ndarray) -> np.ndarray:
        gp = self.gp
        assimilating = drift < -gp.drift_tol      # |position| shrinking -> healthy
        new = np.where(assimilating, dose, dose * gp.cut)
        return np.clip(new, gp.dose_min, gp.dose_max)


class EngagementGuardrail:
    """Leading guardrail: cut a user's dose when their engagement is **low** or
    **falling** -- before the position drifts.  Signal = engagement per user."""

    def __init__(self, gp: GuardrailParams | None = None):
        self.gp = gp or GuardrailParams()
        self.prev = None

    def reset(self):
        self.prev = None

    def step(self, dose: np.ndarray, engagement: np.ndarray) -> np.ndarray:
        gp = self.gp
        engagement = np.asarray(engagement, dtype=float)
        weak = engagement < gp.engage_low
        falling = (self.prev is not None) & (engagement < np.asarray(self.prev) - gp.engage_drop) \
            if self.prev is not None else np.zeros_like(engagement, dtype=bool)
        new = np.where(weak | falling, dose * gp.cut, dose)
        self.prev = engagement
        return np.clip(new, gp.dose_min, gp.dose_max)


# --------------------------------------------------------------------------- #
# Closed-loop simulation against the opinion-dynamics environment
# --------------------------------------------------------------------------- #
def simulate_guardrail(theta0, monitor=None, signal: str = "none", n_rounds: int = 40,
                       op: OpinionParams | None = None,
                       gp: GuardrailParams | None = None) -> dict:
    """Run the opinion-dynamics environment with a per-user dose controller.

    Each round every user is shown opposite-side content at distance ``dose``;
    positions update by the assimilation–contrast rule; the controller then
    adjusts each user's dose from the chosen ``signal`` (``"drift"`` or
    ``"engagement"``).  Returns histories of ``polarization`` and mean ``dose``.
    """
    op = op or OpinionParams()
    gp = gp or GuardrailParams()
    theta = np.clip(np.asarray(theta0, dtype=float), -op.bound, op.bound)
    dose = np.full(theta.shape, gp.dose0, dtype=float)
    if monitor is not None:
        monitor.reset()
    pol_hist = [polarization(theta)]
    dose_hist = [float(dose.mean())]
    for _ in range(n_rounds):
        shown = np.clip(theta - np.sign(theta) * dose, -op.bound, op.bound)
        theta_new = update(theta, shown, op)
        drift = np.abs(theta_new) - np.abs(theta)
        engagement = comfort(dose, op)
        if monitor is not None and signal == "drift":
            dose = monitor.step(dose, drift)
        elif monitor is not None and signal == "engagement":
            dose = monitor.step(dose, engagement)
        theta = theta_new
        pol_hist.append(polarization(theta))
        dose_hist.append(float(dose.mean()))
    return {"polarization": pol_hist, "dose": dose_hist, "final": theta}


def compare_guardrails(theta0, n_rounds: int = 40, op: OpinionParams | None = None,
                       gp: GuardrailParams | None = None) -> dict:
    """Run the same aggressive dose with no guardrail vs. the two guardrails.

    Returns ``{label: simulate_guardrail(...) result}``.
    """
    gp = gp or GuardrailParams()
    return {
        "no guardrail": simulate_guardrail(theta0, None, "none", n_rounds, op, gp),
        "backfire monitor (drift)":
            simulate_guardrail(theta0, BackfireMonitor(gp), "drift", n_rounds, op, gp),
        "engagement early-warning":
            simulate_guardrail(theta0, EngagementGuardrail(gp), "engagement", n_rounds, op, gp),
    }
