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


def test_ndcg_at_k():
    recs = np.array([[10, 11, 12], [20, 21, 22]])
    test_pos = [np.array([10, 11]), np.array([22])]
    # user 0: both hits at ranks 0,1 -> perfect NDCG=1; user 1: one hit at rank 2
    # -> DCG=1/log2(4)=0.5, ideal=1 -> 0.5.  mean = 0.75.
    assert np.isclose(metrics.ndcg_at_k(recs, test_pos, k=3), 0.75)


def test_catalog_coverage():
    recs = np.array([[0, 1, -1], [1, 2, -1]])  # unique items {0,1,2} of 5
    assert np.isclose(metrics.catalog_coverage(recs, n_items=5), 0.6)


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


# --- ideological shift & weighted diversity (Appendix A.1) ----------------
# item positions: items 0,1 are left (-2,-1); items 2,3 are right (+1,+2).
_POS = np.array([-2.0, -1.0, 1.0, 2.0])


def test_ideological_shift_signed():
    # left user gets right-side items (+1,+2); right user gets left-side items.
    recs = np.array([[2, 3], [0, 1]])
    ref = np.array([-1.5, 1.5])           # users' own positions
    shift = metrics.ideological_shift(recs, _POS, ref)
    assert np.allclose(shift, [1.5 - (-1.5), -1.5 - 1.5])   # [+3.0, -3.0]


def test_directed_shift_rewards_bridging():
    ref = np.array([-1.5, 1.5])
    bridging = np.array([[2, 3], [0, 1]])   # each user pushed to the other side
    same_side = np.array([[0, 1], [2, 3]])  # each user kept on their own side
    assert metrics.directed_shift(bridging, _POS, ref) > 0
    assert (metrics.directed_shift(bridging, _POS, ref)
            > metrics.directed_shift(same_side, _POS, ref))


def test_weighted_shift_emphasizes_extreme_users():
    # Two left users: one extreme (-2), one mild (-0.5).
    ref = np.array([-2.0, -0.5])
    # Scenario A: bridge the EXTREME user strongly (+3) and the mild one weakly.
    recs_a = np.array([[3, 3], [2, 2]])     # extreme -> +2 (shift +4); mild -> +1 (shift +1.5)
    # Scenario B: bridge the MILD user strongly instead.
    recs_b = np.array([[2, 2], [3, 3]])     # extreme -> +1 (shift +3);   mild -> +2 (shift +2.5)
    # Unweighted directed shift is identical-ish; weighting by |ref| rewards A.
    assert metrics.weighted_shift(recs_a, _POS, ref) > metrics.weighted_shift(recs_b, _POS, ref)


def test_weighted_range_emphasizes_extreme_users():
    ref = np.array([-2.0, -0.5])            # extreme vs mild user
    wide_then_narrow = np.array([[0, 3], [2, 2]])  # extreme: range 4; mild: range 0
    narrow_then_wide = np.array([[2, 2], [0, 3]])  # extreme: range 0; mild: range 4
    assert (metrics.weighted_range(wide_then_narrow, _POS, ref)
            > metrics.weighted_range(narrow_then_wide, _POS, ref))


def test_weighted_position_rewards_central_recs_for_extremes():
    # UW-Recs/TW-Recs: lower = recs lean toward the centre (better).
    ref = np.array([-2.0, -0.5])            # extreme vs mild user (centre = 0)
    # extreme user gets central recs (mean 0), mild user gets extreme recs (mean +2)
    extreme_central = np.array([[1, 2], [3, 3]])   # _POS=[-2,-1,1,2] -> means 0 and +2
    extreme_extreme = np.array([[0, 0], [1, 2]])   # extreme -> mean -2; mild -> mean 0
    assert (metrics.weighted_position(extreme_central, _POS, ref)
            < metrics.weighted_position(extreme_extreme, _POS, ref))


def test_per_user_means_match_aggregates():
    rng = np.random.default_rng(0)
    scores = rng.random((6, 10))
    test_pos = [[0], [1], [], [2, 3], [5], [8]]
    train_pos = [[9]] * 6
    pu = metrics.auc_per_user(scores, test_pos, train_pos)
    assert pu.shape == (6,) and np.isnan(pu[2])                 # user 2 has no test
    assert np.isclose(metrics.auc(scores, test_pos, train_pos), np.nanmean(pu))

    recs = np.array([[0, 1, 2], [3, 4, -1], [-1, -1, -1], [5, 6, 7]])
    tp = [[0, 9], [4], [7], [5]]
    assert np.isclose(metrics.hit_rate_at_k(recs, tp),
                      np.nanmean(metrics.hit_rate_per_user(recs, tp)))
    assert np.isclose(metrics.ndcg_at_k(recs, tp, 3),
                      np.nanmean(metrics.ndcg_per_user(recs, tp, 3)))


def test_per_user_all_undefined_is_nan():
    recs = np.array([[-1, -1]])
    assert np.isnan(metrics.hit_rate_at_k(recs, [[]]))          # no test items anywhere
    assert np.all(np.isnan(metrics.hit_rate_per_user(recs, [[]])))
