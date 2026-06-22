import numpy as np

from rwe import metrics


def test_precision_and_hit_rate():
    # user 0: 2/3 of top-3 are relevant; user 1: 1/3.
    recs = np.array([[10, 11, 12], [20, 21, 22]])
    test_pos = [np.array([10, 11]), np.array([22])]
    assert np.isclose(metrics.precision_at_k(recs, test_pos, k=3), (2 / 3 + 1 / 3) / 2)
    # hit rate is recall-style: user 0 hits 2/2, user 1 hits 1/1.
    assert np.isclose(metrics.hit_rate_at_k(recs, test_pos), 1.0)


def test_auc_perfect_and_random():
    # Two users, 4 items. Positive items get the highest scores -> AUC = 1.
    scores = np.array([[0.1, 0.2, 0.9, 0.8],
                       [0.9, 0.8, 0.1, 0.2]])
    test_pos = [np.array([2, 3]), np.array([0, 1])]
    train_pos = [np.array([], dtype=int), np.array([], dtype=int)]
    assert np.isclose(metrics.auc(scores, test_pos, train_pos), 1.0)


def test_mean_rank():
    scores = np.array([[0.5, 0.4, 0.3, 0.2]])  # ranking: items 0,1,2,3
    test_pos = [np.array([1])]                 # item 1 is at rank 2
    train_pos = [np.array([], dtype=int)]
    assert np.isclose(metrics.mean_rank(scores, test_pos, train_pos), 2.0)


def test_gini_diversity_extremes():
    n_items = 5
    # All recommendations on a single item -> least diverse -> low GiniD.
    concentrated = np.array([[0], [0], [0], [0]])
    # Each item recommended equally -> most diverse -> GiniD ~ 1.
    uniform = np.array([[0], [1], [2], [3]])  # item 4 never recommended
    gd_conc = metrics.gini_diversity(concentrated, n_items)
    gd_unif = metrics.gini_diversity(uniform, n_items)
    assert gd_unif > gd_conc


def test_average_item_degree_and_surprisal():
    recs = np.array([[0, 1]])
    item_deg = np.array([100.0, 1.0])
    # Average degree is the mean popularity of recommended items.
    assert np.isclose(metrics.average_item_degree(recs, item_deg), 50.5)
    # The rare item (deg 1) is more surprising than the popular one.
    surp = metrics.surprisal(recs, item_deg, n_users=100)
    assert surp > 0


def test_rec_range():
    recs = np.array([[0, 1, 2]])
    pos = np.array([-1.5, 0.0, 2.0])
    assert np.isclose(metrics.rec_range_at_k(recs, pos), 3.5)


def test_ks_statistic_distinguishes_distributions():
    pos = np.linspace(-2, 2, 20)
    left = np.array([[0, 1, 2, 3, 4]])       # only left-leaning items
    spread = np.array([[0, 5, 10, 15, 19]])  # across the spectrum
    stat, pval = metrics.ks_statistic(left, spread, pos)
    assert 0.0 <= stat <= 1.0


def test_personalization_disjoint_lists():
    recs = np.array([[0, 1], [2, 3]])  # no overlap -> max personalization
    assert np.isclose(metrics.personalization(recs, n_items=4), 1.0)
