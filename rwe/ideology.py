"""Political ideology detection (Section 6).

Recovers one-dimensional ideological positions on a shared latent scale for

* users    -- ``theta``   (m,)
* elites   -- ``phi``     (n_e,)   from the elite-endorsement graph ``R``
* content  -- ``psi``     (n_i,)   from the content-share graph ``S``

together with per-node bias terms (``alpha`` for users, ``beta`` for elites,
``gamma`` for content).

Model
-----
Endorsements follow a spatial (ideal-point) logistic model in which the log-odds
*decrease* with squared ideological distance::

    Pi^R_{u,e} = -||theta_u - phi_e||^2 + alpha_u + beta_e        (eq. 6)
    Pi^S_{u,i} = -||theta_u - psi_i||^2 + alpha_u + gamma_i       (eq. 9)
    p(R_{u,e}=1) = sigmoid(Pi^R_{u,e})

The joint objective (eq. 11) maximises the confidence-weighted Bernoulli
log-likelihood of both graphs with L2 regularisation on the positions; ``mu``
trades off the elite-endorsement contribution::

    L = mu * sum_{u,e} [ a_{u,e} Pi^R_{u,e} - log(1 + exp(Pi^R_{u,e})) ]
          + sum_{u,i} [ b_{u,i} Pi^S_{u,i} - log(1 + exp(Pi^S_{u,i})) ]
          - (lam/2) (||theta||^2 + ||phi||^2 + ||psi||^2)

Passing ``S = None`` fits the elite-only model of Section 6.1 (eq. 8).

The positions ``theta`` are standardised (zero mean, unit variance) after
fitting; ``phi`` and ``psi`` are placed on the same scale.  Because the model is
invariant to a global sign flip, an optional ``anchor`` user can be forced to
the negative (left) side for reproducible orientation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return np.where(x >= 0, 1.0 / (1.0 + np.exp(-x)),
                    np.exp(x) / (1.0 + np.exp(x)))


class _Adam:
    """Minimal Adam optimiser for a single parameter array (ascent).

    The all-pairs objective produces gradients whose scale varies widely across
    parameter blocks (``theta`` sums over many items, ``beta`` over many users),
    so an adaptive step size is much more robust than a single fixed rate.
    """

    def __init__(self, shape, lr, b1=0.9, b2=0.999, eps=1e-8):
        self.lr, self.b1, self.b2, self.eps = lr, b1, b2, eps
        self.m = np.zeros(shape)
        self.v = np.zeros(shape)
        self.t = 0

    def step(self, param, grad):
        self.t += 1
        self.m = self.b1 * self.m + (1 - self.b1) * grad
        self.v = self.b2 * self.v + (1 - self.b2) * grad * grad
        m_hat = self.m / (1 - self.b1 ** self.t)
        v_hat = self.v / (1 - self.b2 ** self.t)
        return param + self.lr * m_hat / (np.sqrt(v_hat) + self.eps)


@dataclass
class IdeologyResult:
    """Fitted ideological positions and bias terms."""

    theta: np.ndarray            # user positions
    phi: np.ndarray              # elite positions
    alpha: np.ndarray            # user biases
    beta: np.ndarray             # elite biases
    psi: np.ndarray | None = None    # content positions (joint model)
    gamma: np.ndarray | None = None  # content biases (joint model)
    history: list | None = None      # objective value per iteration


class IdeologyModel:
    """Joint / elite-only ideal-point estimator (Section 6).

    Parameters
    ----------
    n_iter:
        Number of alternating gradient-ascent sweeps.
    lr:
        Gradient-ascent step size.
    lam:
        L2 regularisation strength on positions (``theta``, ``phi``, ``psi``).
    mu:
        Trade-off weight on the elite-endorsement graph in the joint model.
    seed:
        RNG seed for the (small random) initialisation.
    """

    def __init__(self, n_iter: int = 300, lr: float = 0.05, lam: float = 0.1,
                 mu: float = 1.0, seed: int = 0):
        self.n_iter = n_iter
        self.lr = lr
        self.lam = lam
        self.mu = mu
        self.seed = seed

    @staticmethod
    def _as_confidence(M):
        """Dense confidence-weighted label matrix ``a`` (observed -> weight)."""
        return np.asarray(sp.csr_matrix(M, dtype=float).todense())

    def fit(self, R, S=None, anchor: int | None = None) -> IdeologyResult:
        """Estimate ideological positions.

        Parameters
        ----------
        R:
            ``(m, n_e)`` elite-endorsement matrix.  Non-zero entries are
            treated as confidence weights ``a_{u,e}`` (eq. 7); pass a binary
            matrix for unweighted endorsements.
        S:
            Optional ``(m, n_i)`` content-share matrix for the joint model
            (Section 6.2).  When ``None``, the elite-only model is fit.
        anchor:
            Optional user id forced to the left (negative) side to fix the
            global sign.
        """
        rng = np.random.default_rng(self.seed)
        A = self._as_confidence(R)
        m, n_e = A.shape

        theta = rng.normal(0, 0.1, m)
        phi = rng.normal(0, 0.1, n_e)
        alpha = np.zeros(m)
        beta = np.zeros(n_e)

        joint = S is not None
        if joint:
            B = self._as_confidence(S)
            if B.shape[0] != m:
                raise ValueError("R and S must share the same users (rows).")
            n_i = B.shape[1]
            psi = rng.normal(0, 0.1, n_i)
            gamma = np.zeros(n_i)
        else:
            B = psi = gamma = None

        opt = {name: _Adam(p.shape, self.lr) for name, p in
               (("theta", theta), ("phi", phi), ("alpha", alpha), ("beta", beta))}
        if joint:
            opt["psi"] = _Adam(psi.shape, self.lr)
            opt["gamma"] = _Adam(gamma.shape, self.lr)

        history = []
        for _ in range(self.n_iter):
            # --- elite-endorsement graph R -------------------------------
            diff_R = theta[:, None] - phi[None, :]          # (m, n_e)
            Pi_R = -(diff_R ** 2) + alpha[:, None] + beta[None, :]
            err_R = A - _sigmoid(Pi_R)                       # residual
            g_theta = self.mu * (err_R * (-2.0 * diff_R)).sum(axis=1)
            g_phi = self.mu * (err_R * (2.0 * diff_R)).sum(axis=0) - self.lam * phi
            g_alpha = self.mu * err_R.sum(axis=1)
            g_beta = self.mu * err_R.sum(axis=0)

            # --- content-share graph S (joint model) ---------------------
            if joint:
                diff_S = theta[:, None] - psi[None, :]       # (m, n_i)
                Pi_S = -(diff_S ** 2) + alpha[:, None] + gamma[None, :]
                err_S = B - _sigmoid(Pi_S)
                g_theta = g_theta + (err_S * (-2.0 * diff_S)).sum(axis=1)
                g_alpha = g_alpha + err_S.sum(axis=1)
                g_psi = (err_S * (2.0 * diff_S)).sum(axis=0) - self.lam * psi
                g_gamma = err_S.sum(axis=0)

            g_theta = g_theta - self.lam * theta

            # --- adaptive (Adam) gradient-ascent updates -----------------
            theta = opt["theta"].step(theta, g_theta)
            phi = opt["phi"].step(phi, g_phi)
            alpha = opt["alpha"].step(alpha, g_alpha)
            beta = opt["beta"].step(beta, g_beta)
            if joint:
                psi = opt["psi"].step(psi, g_psi)
                gamma = opt["gamma"].step(gamma, g_gamma)

            history.append(self._objective(A, theta, phi, alpha, beta,
                                           B, psi, gamma))

        # Standardise positions onto a common, comparable scale.
        scale = theta.std() or 1.0
        mean = theta.mean()
        theta = (theta - mean) / scale
        phi = (phi - mean) / scale
        if joint:
            psi = (psi - mean) / scale

        if anchor is not None and theta[anchor] > 0:
            theta, phi = -theta, -phi
            if joint:
                psi = -psi

        return IdeologyResult(theta=theta, phi=phi, alpha=alpha, beta=beta,
                              psi=psi, gamma=gamma, history=history)

    def _objective(self, A, theta, phi, alpha, beta, B, psi, gamma) -> float:
        diff_R = theta[:, None] - phi[None, :]
        Pi_R = -(diff_R ** 2) + alpha[:, None] + beta[None, :]
        ll = self.mu * (A * Pi_R - np.logaddexp(0.0, Pi_R)).sum()
        if B is not None:
            diff_S = theta[:, None] - psi[None, :]
            Pi_S = -(diff_S ** 2) + alpha[:, None] + gamma[None, :]
            ll += (B * Pi_S - np.logaddexp(0.0, Pi_S)).sum()
        reg = 0.5 * self.lam * (theta @ theta + phi @ phi)
        if psi is not None:
            reg += 0.5 * self.lam * (psi @ psi)
        return float(ll - reg)
