"""Opinion-dynamics simulation: does exposing opposite views help or hurt?

This module answers a question the bridging strategy raises: *exposing people to
opposing viewpoints can backfire and increase polarization* (Bail et al., PNAS
2018). It lets us simulate the **outcome** — not just the mechanism — of
different exposure policies on a population's ideological positions.

Model (assimilation–contrast / Social Judgment Theory)
------------------------------------------------------
Each round, every user is shown a piece of content at some ideological position.
The user updates their own position ``theta`` by:

* **assimilating** content within their *acceptance latitude* — they move
  *toward* it (``|shown - theta| <= latitude_accept``);
* **contrasting / backfiring** on content beyond their *rejection latitude* —
  they move *away* from it (``|shown - theta| >= latitude_reject``), i.e. further
  toward their own pole. This is the backfire effect;
* ignoring content in between (the latitude of non-commitment).

We run four exposure policies on the *same* starting population and track
population polarization (the spread / standard deviation of positions) over
rounds:

* ``echo chamber`` — show same-side, slightly more extreme content;
* ``naive opposite-blast`` — show the far opposite pole (triggers backfire);
* ``RWE-B bridging`` — show opposite-side content **within** the acceptance
  latitude ("different but not too far");
* ``adaptive (satisfaction)`` — bridging whose stretch shrinks for extreme users
  (the satisfaction extension), so it never tips them into backfire.

The result (see ``docs/make_diagrams.py::opinion_dynamics``): bounded bridging
**reduces** polarization while the naive opposite-blast **increases** it — same
goal, opposite outcome depending on *how* the exposure is done.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class OpinionParams:
    """Parameters of the assimilation–contrast opinion-update model."""

    bound: float = 2.5             # positions are clipped to [-bound, bound]
    latitude_accept: float = 1.0   # assimilate content within this distance
    latitude_reject: float = 2.0   # backfire on content beyond this distance
    mu_assim: float = 0.20         # step size toward accepted content
    mu_backfire: float = 0.15      # step size away from rejected content
    content_noise: float = 0.15    # jitter on shown-content positions (realism)


# --------------------------------------------------------------------------- #
# Opinion-update rule
# --------------------------------------------------------------------------- #
def update(theta: np.ndarray, shown: np.ndarray, p: OpinionParams) -> np.ndarray:
    """One round of assimilation–contrast updates.

    ``theta`` and ``shown`` are per-user arrays (current position, and the
    position of the content shown to that user). Returns the new positions.
    """
    d = shown - theta
    ad = np.abs(d)
    move = np.zeros_like(theta, dtype=float)
    accept = ad <= p.latitude_accept
    reject = ad >= p.latitude_reject
    move[accept] = p.mu_assim * d[accept]          # move toward accepted content
    move[reject] = -p.mu_backfire * d[reject]      # backfire: move away (own pole)
    return np.clip(theta + move, -p.bound, p.bound)


# --------------------------------------------------------------------------- #
# Exposure policies: theta (+ rng) -> position of content shown to each user
# --------------------------------------------------------------------------- #
def _jitter(shown, rng, p):
    return shown + rng.normal(0.0, p.content_noise, size=shown.shape)


def echo_chamber(theta, rng, p):
    """Same-side content, slightly more extreme (engagement amplification)."""
    return np.clip(_jitter(1.3 * theta, rng, p), -p.bound, p.bound)


def opposite_blast(theta, rng, p):
    """The far opposite pole — beyond most users' rejection latitude."""
    return np.clip(_jitter(-np.sign(theta) * p.bound, rng, p), -p.bound, p.bound)


def rwe_b_bridging(theta, rng, p):
    """Opposite-side content kept *within* the acceptance latitude."""
    step = 0.9 * p.latitude_accept
    return np.clip(_jitter(theta - np.sign(theta) * step, rng, p), -p.bound, p.bound)


def adaptive_bridging(theta, rng, p):
    """Bridging whose stretch shrinks for extreme users (never backfires)."""
    tolerance = np.clip(1.0 - np.abs(theta) / p.bound, 0.15, 1.0)
    step = 0.9 * p.latitude_accept * tolerance
    return np.clip(_jitter(theta - np.sign(theta) * step, rng, p), -p.bound, p.bound)


POLICIES = {
    "echo chamber": echo_chamber,
    "naive opposite-blast": opposite_blast,
    "RWE-B bridging": rwe_b_bridging,
    "adaptive (satisfaction)": adaptive_bridging,
}


# --------------------------------------------------------------------------- #
# Simulation
# --------------------------------------------------------------------------- #
def polarization(theta: np.ndarray) -> float:
    """Population polarization = standard deviation of positions (higher = more
    spread toward the extremes)."""
    return float(np.std(theta))


def run(theta0, policy, n_rounds: int = 40, p: OpinionParams | None = None,
        seed: int = 0):
    """Run one exposure policy; return ``(final_positions, polarization_history)``.

    ``policy`` is a callable ``(theta, rng, params) -> shown positions`` (one of
    :data:`POLICIES`, or your own).
    """
    p = p or OpinionParams()
    rng = np.random.default_rng(seed)
    theta = np.clip(np.asarray(theta0, dtype=float), -p.bound, p.bound)
    history = [polarization(theta)]
    for _ in range(n_rounds):
        theta = update(theta, policy(theta, rng, p), p)
        history.append(polarization(theta))
    return theta, history


def compare_policies(theta0, n_rounds: int = 40, p: OpinionParams | None = None,
                     seed: int = 0) -> dict:
    """Run every policy in :data:`POLICIES` on the same population.

    Returns ``{policy_name: polarization_history}`` (each a length ``n_rounds+1``
    list), so divergence vs convergence can be compared directly.
    """
    return {name: run(theta0, pol, n_rounds, p, seed)[1]
            for name, pol in POLICIES.items()}


def initial_population(n_users: int = 400, seed: int = 0,
                       p: OpinionParams | None = None) -> np.ndarray:
    """A mildly polarized starting population (two soft clusters around ±0.8)."""
    p = p or OpinionParams()
    rng = np.random.default_rng(seed)
    half = n_users // 2
    theta = np.concatenate([rng.normal(-0.8, 0.4, half),
                            rng.normal(0.8, 0.4, n_users - half)])
    return np.clip(theta, -p.bound, p.bound)
