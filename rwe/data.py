"""Data loading, train/test splitting and synthetic generators.

The Twitter datasets of the paper (UK2016 / US2016 / DE2017) are not
redistributable, so this module provides:

* generic loaders for any ``(user, item[, rating])`` interaction table,
* a MovieLens-1M loader (``ratings.dat``) for the benchmark experiments,
* the train/test split protocol of Section 7.3, and
* synthetic generators with *planted* ideological structure, used by the demos
  and tests to exercise the full pipeline end to end.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import scipy.sparse as sp


@dataclass
class Dataset:
    """A user-item interaction matrix with id <-> index mappings."""

    matrix: sp.csr_matrix                 # (m, n) interactions
    user_ids: np.ndarray                  # original user id per row
    item_ids: np.ndarray                  # original item id per column

    @property
    def n_users(self) -> int:
        return self.matrix.shape[0]

    @property
    def n_items(self) -> int:
        return self.matrix.shape[1]


def from_interactions(users, items, values=None) -> Dataset:
    """Build a :class:`Dataset` from parallel ``users`` / ``items`` arrays."""
    users = np.asarray(users)
    items = np.asarray(items)
    uniq_u, u_idx = np.unique(users, return_inverse=True)
    uniq_i, i_idx = np.unique(items, return_inverse=True)
    vals = np.ones(len(users)) if values is None else np.asarray(values, dtype=float)
    matrix = sp.coo_matrix((vals, (u_idx, i_idx)),
                           shape=(uniq_u.size, uniq_i.size)).tocsr()
    matrix.sum_duplicates()
    return Dataset(matrix=matrix, user_ids=uniq_u, item_ids=uniq_i)


def load_csv(path, user_col="user", item_col="item", value_col=None,
             sep=",", **read_kwargs) -> Dataset:
    """Load an interaction table from a delimited file."""
    df = pd.read_csv(path, sep=sep, **read_kwargs)
    values = df[value_col].to_numpy() if value_col else None
    return from_interactions(df[user_col].to_numpy(), df[item_col].to_numpy(), values)


def load_movielens_1m(path) -> Dataset:
    """Load MovieLens-1M ``ratings.dat`` (``user::item::rating::timestamp``)."""
    df = pd.read_csv(path, sep="::", engine="python", header=None,
                     names=["user", "item", "rating", "ts"])
    return from_interactions(df["user"].to_numpy(), df["item"].to_numpy())


def train_test_split(dataset: Dataset, test_frac: float = 0.3, min_interactions: int = 3,
                     seed: int = 0):
    """Split per Section 7.3.

    For every user with **more than** ``min_interactions`` items, ``test_frac``
    of their items are moved to the test set; all items of users at or below the
    threshold stay in training.

    Returns
    -------
    (train_matrix, test_pos)
        ``train_matrix`` is a ``csr_matrix`` of the same shape; ``test_pos`` is
        a list (per user) of held-out item-index arrays.
    """
    rng = np.random.default_rng(seed)
    A = dataset.matrix.tocsr()
    m, n = A.shape
    train = A.tolil(copy=True)
    test_pos = [np.array([], dtype=int) for _ in range(m)]
    for u in range(m):
        items = A.indices[A.indptr[u]:A.indptr[u + 1]]
        if items.size <= min_interactions:
            continue
        n_test = max(1, int(round(test_frac * items.size)))
        held = rng.choice(items, size=n_test, replace=False)
        test_pos[u] = np.sort(held)
        train[u, held] = 0
    train = train.tocsr()
    train.eliminate_zeros()
    return train, test_pos


# --------------------------------------------------------------------------- #
# Synthetic generators (planted ideological structure)
# --------------------------------------------------------------------------- #
def synthetic_ideology(n_users=300, n_elites=40, n_content=60, seed=0,
                       endorse_per_user=8, share_per_user=6):
    """Generate endorsement / share graphs with planted ideological positions.

    Users, elites and content get latent positions on ``[-2, 2]``; an
    interaction is sampled with probability proportional to the spatial logistic
    model (closer positions -> more likely), so that
    :class:`rwe.ideology.IdeologyModel` can recover the planted positions.

    Returns
    -------
    dict with keys ``R, S, theta, phi, psi`` (sparse matrices + true positions).
    """
    rng = np.random.default_rng(seed)
    theta = rng.uniform(-2, 2, n_users)
    phi = rng.uniform(-2, 2, n_elites)
    psi = rng.uniform(-2, 2, n_content)

    def _sample(positions, per_user):
        rows, cols = [], []
        for u in range(n_users):
            logit = -((theta[u] - positions) ** 2) + 2.0
            prob = 1.0 / (1.0 + np.exp(-logit))
            prob /= prob.sum()
            k = min(per_user, positions.size)
            chosen = rng.choice(positions.size, size=k, replace=False, p=prob)
            rows.extend([u] * k)
            cols.extend(chosen.tolist())
        return sp.coo_matrix((np.ones(len(rows)), (rows, cols)),
                             shape=(n_users, positions.size)).tocsr()

    return {
        "R": _sample(phi, endorse_per_user),
        "S": _sample(psi, share_per_user),
        "theta": theta, "phi": phi, "psi": psi,
    }


def synthetic_political(n_users=200, n_items=80, seed=0, homophily=2.0,
                        close_per_user=8, weak_ties_per_user=2):
    """Generate a *homophilous* feedback graph with weak cross-side ties.

    Each user mostly connects to ideologically close items (homophily), plus a
    few uniformly-random "weak tie" items.  This is the structure the bridging
    strategy (RWE-B) is designed for: plain random walks stay on the user's own
    side, while erasure can reach the opposite side through the weak ties.

    Returns
    -------
    dict with keys ``matrix`` (csr feedback), ``user_positions`` (``theta``)
    and ``item_positions`` (``phi`` / ``psi``).
    """
    rng = np.random.default_rng(seed)
    user_positions = rng.uniform(-2, 2, n_users)
    item_positions = rng.uniform(-2, 2, n_items)

    rows, cols = [], []
    for u in range(n_users):
        aff = np.exp(-homophily * (user_positions[u] - item_positions) ** 2)
        aff /= aff.sum()
        close = rng.choice(n_items, size=min(close_per_user, n_items),
                           replace=False, p=aff)
        weak = rng.choice(n_items, size=min(weak_ties_per_user, n_items),
                          replace=False)
        chosen = np.unique(np.concatenate([close, weak]))
        rows.extend([u] * chosen.size)
        cols.extend(chosen.tolist())
    matrix = sp.coo_matrix((np.ones(len(rows)), (rows, cols)),
                           shape=(n_users, n_items)).tocsr()
    matrix.data[:] = 1.0
    return {"matrix": matrix, "user_positions": user_positions,
            "item_positions": item_positions}


def synthetic_recsys(n_users=200, n_items=120, seed=0, items_per_user=10,
                     popularity_skew=1.3):
    """Generate a skewed implicit-feedback dataset for recommender experiments.

    Item exposure follows a power law (so long-tail metrics are meaningful) and
    each item carries a one-dimensional ideological position (so ideological
    metrics are meaningful).

    Returns
    -------
    (Dataset, item_positions)
    """
    rng = np.random.default_rng(seed)
    pop = (np.arange(1, n_items + 1) ** -popularity_skew)
    pop /= pop.sum()
    item_positions = rng.uniform(-2, 2, n_items)
    user_positions = rng.uniform(-2, 2, n_users)

    rows, cols = [], []
    for u in range(n_users):
        affinity = np.exp(-0.5 * (user_positions[u] - item_positions) ** 2)
        prob = pop * affinity
        prob /= prob.sum()
        k = min(items_per_user, n_items)
        chosen = rng.choice(n_items, size=k, replace=False, p=prob)
        rows.extend([u] * k)
        cols.extend(chosen.tolist())
    matrix = sp.coo_matrix((np.ones(len(rows)), (rows, cols)),
                           shape=(n_users, n_items)).tocsr()
    dataset = Dataset(matrix=matrix, user_ids=np.arange(n_users),
                      item_ids=np.arange(n_items))
    return dataset, item_positions
