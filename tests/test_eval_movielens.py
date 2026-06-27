"""Test the MovieLens-1M RQ2 driver (examples/eval_movielens.py) end-to-end on a
tiny synthetic interaction set."""

import importlib.util
import pathlib
import types

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "eval_movielens", ROOT / "examples" / "eval_movielens.py")
em = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(em)


def test_run_produces_rq2_table():
    from rwe import data
    rng = np.random.default_rng(0)
    pop = 1.0 / np.arange(1, 21)
    pop /= pop.sum()
    users, items = [], []
    for u in range(40):
        for it in rng.choice(np.arange(20), size=8, replace=False, p=pop):
            users.append(u)
            items.append(int(it))
    ds = data.from_interactions(np.array(users), np.array(items))

    args = types.SimpleNamespace(top_k=10, diversity_k=20, test_frac=0.3, seeds=1,
                                 rwed_beta=0.5, rwed_v=0.7, rp3_beta=0.5, itemknn_k=20)
    mean, std = em.run(ds, args)
    assert set(mean.index) == {"ItemKNN", "P3", "RP3-beta", "RWE-D"}
    assert "auc" in mean.columns and "gini_div@20" in mean.columns
    # RWE-D is the degree-suppressing variant -> at least as long-tail as P3
    assert mean.loc["RWE-D", "gini_div@20"] >= mean.loc["P3", "gini_div@20"] - 1e-9
