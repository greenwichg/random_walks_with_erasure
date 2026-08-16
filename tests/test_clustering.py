"""Tests for examples/clustering.py — the reusable deterministic clustering primitive (Commit 7).

Proves related items cluster, unrelated stay separate, the time window splits look-alikes, and the
grouping is deterministic — with no Story/FeedArticle knowledge in this layer."""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import clustering as cl   # noqa: E402

T0 = datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc)


def _items(*specs):
    return [{"t": t, "when": T0 + timedelta(days=d)} for t, d in specs]


def _groups(items, **kw):
    g = cl.cluster(items, tokens=lambda x: cl.title_tokens(x["t"]), time=lambda x: x["when"], **kw)
    return sorted(sorted(grp) for grp in g)


def test_title_tokens_and_jaccard():
    a = cl.title_tokens("Senate passes the funding bill")
    b = cl.title_tokens("Senate funding bill vote")
    assert "senate" in a and "the" not in a          # stop-word dropped
    assert 0.0 < cl.jaccard(a, b) <= 1.0
    assert cl.jaccard(a, frozenset()) == 0.0


# --------------------------------------------------------------------------- #
# description_tokens — the dek as clustering signal.
#
# The clusterer sees 8-12 title tokens and nothing else, so "Fed holds rates steady" and "Central
# bank leaves borrowing costs unchanged" share ZERO tokens and can never meet min_shared however
# the thresholds move. The dek is already on the row. These pin the cap's contract; whether the
# signal is worth its cost is an audit question, not a unit-test one.
# --------------------------------------------------------------------------- #
DEK = ("The Federal Reserve left interest rates unchanged on Wednesday, citing persistent "
       "inflation and a cooling labour market that policymakers said needed more time.")


def test_description_tokens_applies_the_title_filter():
    toks = cl.description_tokens(DEK, cap=50)
    assert "federal" in toks and "reserve" in toks
    assert "the" not in toks and "and" not in toks, "stop-words dropped, same list as titles"
    assert "on" not in toks, "the length floor is > 2, same as titles"
    assert all(not t.isdigit() for t in toks)


def test_description_tokens_takes_the_first_n_in_order():
    """Order of appearance, NOT rarity. Rarity would need a corpus pass before the corpus exists,
    and would reintroduce the IDF weighting whose revert cost 10.5% of covered articles."""
    toks = cl.description_tokens(DEK, cap=4)
    assert toks == frozenset({"federal", "reserve", "left", "interest"})


def test_description_tokens_caps_and_dedupes():
    assert len(cl.description_tokens(DEK, cap=6)) == 6
    assert len(cl.description_tokens(DEK, cap=500)) < 500, "a dek is shorter than any large cap"
    # Repetition is not evidence: the cap counts DISTINCT tokens, so a word said twice buys nothing.
    assert cl.description_tokens("Budget budget budget talks", cap=12) == frozenset(
        {"budget", "talks"})


def test_description_tokens_is_off_at_zero_and_safe_on_nothing():
    """`cap=0` is the production default and must cost nothing — not "an empty-ish set", empty."""
    assert cl.description_tokens(DEK, cap=0) == frozenset()
    assert cl.description_tokens(DEK, cap=-1) == frozenset()
    assert cl.description_tokens("", cap=12) == frozenset()
    assert cl.description_tokens(None, cap=12) == frozenset()


def test_related_items_cluster_together():
    items = _items(("Senate passes funding bill after debate", 0),
                   ("Senate passes funding bill averting shutdown", 0),
                   ("Wildfires spread across western coast", 0))
    assert _groups(items, sim=0.28, window_days=6) == [[0, 1], [2]]


def test_unrelated_items_stay_separate():
    items = _items(("Markets rally on tech earnings", 0),
                   ("Local team wins the championship", 0),
                   ("New climate policy unveiled today", 0))
    assert _groups(items, sim=0.28, window_days=6) == [[0], [1], [2]]


def test_time_window_splits_lookalikes():
    # identical-ish titles, but 40 days apart -> the window keeps them in separate clusters
    items = _items(("Senate passes funding bill", 0), ("Senate passes funding bill", 40))
    assert _groups(items, sim=0.28, window_days=6) == [[0], [1]]
    assert _groups(items, sim=0.28, window_days=60) == [[0, 1]]   # widen the window -> they merge


def test_missing_time_never_blocks_a_match():
    g = cl.cluster([{"t": "Senate funding bill"}, {"t": "Senate funding bill vote"}],
                   tokens=lambda x: cl.title_tokens(x["t"]), time=lambda x: None, window_days=1)
    assert sorted(sorted(grp) for grp in g) == [[0, 1]]


def test_deterministic():
    items = _items(("Senate passes funding bill", 0), ("Senate funding bill vote", 0),
                   ("Wildfires spread west", 0), ("Wildfires spread rapidly west", 1))
    assert _groups(items) == _groups(items)          # same input -> same groups + order


# --------------------------------------------------------------------------- #
# Blocked candidate generation — an EXACT optimisation, not an approximation.
#
# cluster() no longer scores all pairs; it scores only pairs sharing >=1 token, via an inverted
# index. The guarantee that makes that safe: jaccard(a,b) >= sim > 0 requires |a & b| >= 1, so a
# pair sharing no token can never match. These tests hold the guarantee to the fire by comparing
# against a naive all-pairs reference on randomised input.
# --------------------------------------------------------------------------- #
def _cluster_naive(items, *, tokens, time, sim=cl.DEFAULT_SIM, window_days=cl.DEFAULT_WINDOW_DAYS,
                   min_shared=cl.MIN_SHARED_TOKENS, min_tokens=cl.MIN_TITLE_TOKENS):
    """The pre-optimisation all-pairs implementation, kept HERE as the reference oracle.

    It mirrors the REAL admission rules (min_tokens, min_shared) on purpose: an oracle that only
    checked the Jaccard would diverge the moment those gates were added and the equivalence test
    would be asserting nothing."""
    n = len(items)
    toks = [tokens(it) for it in items]
    times = [time(it) for it in items]
    dsu = cl.DSU(n)
    for i in range(n):
        if len(toks[i]) < max(1, min_tokens):
            continue
        for j in range(i + 1, n):
            if len(toks[j]) < max(1, min_tokens) or len(toks[i] & toks[j]) < min_shared:
                continue
            if (cl.jaccard(toks[i], toks[j]) >= sim
                    and cl.within_window(times[i], times[j], window_days)):
                dsu.union(i, j)
    groups = {}
    for i in range(n):
        groups.setdefault(dsu.find(i), []).append(i)
    return list(groups.values())


def _norm(groups):
    return sorted(sorted(g) for g in groups)


def test_blocked_matches_naive_on_randomised_corpora():
    """The core equivalence claim, over many random shapes: identical clusters, every time."""
    import random
    from datetime import datetime, timedelta, timezone

    rnd = random.Random(20260727)
    vocab = [f"tok{i}" for i in range(120)]
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)

    for trial in range(25):
        n = rnd.randint(2, 60)
        events = [[rnd.choice(vocab) for _ in range(6)] for _ in range(max(2, n // 4))]
        items = []
        for _ in range(n):
            ev = rnd.choice(events)
            toks = frozenset(rnd.sample(ev, rnd.randint(2, 5)) + [rnd.choice(vocab)])
            items.append({"t": toks, "d": now - timedelta(hours=rnd.random() * 300)})
        kw = dict(tokens=lambda x: x["t"], time=lambda x: x["d"])
        assert _norm(cl.cluster(items, **kw)) == _norm(_cluster_naive(items, **kw)), \
            f"divergence on trial {trial} (n={n})"


def test_blocked_matches_naive_with_a_hub_token():
    """Worst case for blocking: one token every item carries, so the inverted index degenerates to
    all-pairs. It must still be correct (this is about correctness, not speed)."""
    items = [{"t": frozenset({"election", f"unique{i}"}), "d": None} for i in range(30)]
    kw = dict(tokens=lambda x: x["t"], time=lambda x: x["d"])
    assert _norm(cl.cluster(items, **kw)) == _norm(_cluster_naive(items, **kw))


def test_empty_token_sets_are_isolated_singletons():
    """A title that reduces to nothing (all stop-words) has no postings, so it must never be pulled
    into a cluster — and must still appear as its own group."""
    items = [{"t": cl.title_tokens("the and of it"), "d": None},
             {"t": cl.title_tokens("Senate passes funding bill"), "d": None},
             {"t": cl.title_tokens("Senate passes the funding bill"), "d": None}]
    kw = dict(tokens=lambda x: x["t"], time=lambda x: x["d"])
    assert _norm(cl.cluster(items, **kw)) == [[0], [1, 2]]
    assert _norm(cl.cluster(items, **kw)) == _norm(_cluster_naive(items, **kw))


def test_single_item_and_empty_input():
    kw = dict(tokens=lambda x: x["t"], time=lambda x: x["d"])
    assert cl.cluster([], **kw) == []
    assert _norm(cl.cluster([{"t": frozenset({"a"}), "d": None}], **kw)) == [[0]]


# --------------------------------------------------------------------------- #
# Admission gates — what stops boilerplate from being read as evidence.
#
# The ratio alone cannot tell evidence from coincidence. Measured on real merges:
#   "Berlin pride event canceled…" / "Vehicle drives into crowd at Berlin pride event"
#        jaccard 0.86, 6 shared -> same event
#   "Trump wins Ohio" / "Trump wins Iowa"
#        jaccard 0.50, 2 shared -> different events, and no stop-list can fix it
# --------------------------------------------------------------------------- #
def test_calendar_and_editorial_filler_is_not_content():
    """The production case: "Local news in brief, July 21" and "…July 22" reduced to the SAME four
    tokens and merged 65 articles from 42 publishers into one story."""
    a = cl.title_tokens("Local news in brief , July 21")
    b = cl.title_tokens("Local news in brief , July 22")
    assert a == b == frozenset({"local"}), "filler must not survive tokenisation"
    assert len(a) < cl.MIN_TITLE_TOKENS, "and what survives is too thin to cluster on"


def test_bare_numbers_are_dropped():
    """A number in a headline is a count, a date or a listicle rank far more often than the subject."""
    assert "2010" not in cl.title_tokens("6 Best Blockbuster Movies Released Since 2010")
    assert "gujarat" in cl.title_tokens("Gujarat bridge collapse kills 30")


def test_a_thin_headline_never_clusters():
    items = _items(("Weekly roundup", 0), ("Weekly roundup", 0))
    assert _groups(items) == [[0], [1]]


def test_two_shared_words_are_not_enough_to_merge_different_events():
    """Ohio is not Iowa. Jaccard 0.50 clears the ratio outright; only the shared-token floor
    separates them, which is why the floor exists at all."""
    a, b = cl.title_tokens("Trump wins Ohio"), cl.title_tokens("Trump wins Iowa")
    assert len(a & b) == 2 and cl.jaccard(a, b) >= cl.DEFAULT_SIM   # the ratio would have merged
    assert _groups(_items(("Trump wins Ohio", 0), ("Trump wins Iowa", 0))) == [[0], [1]]


def test_genuinely_matching_headlines_still_merge():
    """The guard against over-correcting: real co-coverage must survive both gates."""
    for a, b in [("Berlin pride event canceled after vehicle drives into crowd",
                  "Vehicle drives into crowd at Berlin pride event"),
                 ("Senate passes the funding bill after debate",
                  "Senate passes funding bill averting shutdown"),
                 ("Wildfires ravage parts of southern France and Spain",
                  "Wildfires ravage southern France, Italy and Spain")]:
        assert _groups(_items((a, 0), (b, 0))) == [[0, 1]], f"{a!r} no longer merges with {b!r}"


def test_gates_are_configurable_and_default_to_the_module_constants():
    items = _items(("Harbour pilots ratify their new contract", 0),
                   ("Harbour pilots ratify contract", 0))
    assert _groups(items) == [[0, 1]]
    assert _groups(items, min_shared=99) == [[0], [1]]           # nothing can clear an absurd floor
    assert _groups(items, min_tokens=99) == [[0], [1]]           # nor an absurd token minimum


# --------------------------------------------------------------------------- #
# Rarity weighting (idf) — shared COMMON words are weaker evidence than shared rare ones.
#
# The motivating failure is single-linkage CHAINING: union-find merges A~B and B~C even when A and
# C have nothing in common, so links resting on ubiquitous words ("trump", "says") glue unrelated
# stories together. Downweighting those tokens weakens the chain without touching rare-token
# matches. OFF by default — it changes what the sim threshold MEANS.
# --------------------------------------------------------------------------- #
def test_rare_tokens_outweigh_common_ones():
    corpus = [cl.title_tokens(t) for t in
              ["Trump defends tariffs on Canada", "Trump nominates FIFA boss for UN post",
               "Trump promises help for Lebanon", "Trump defends tariffs on Mexico",
               "Berlin pride event canceled after vehicle drives into crowd"]]
    w = cl.idf_weights(corpus)
    assert w["berlin"] > w["trump"], "a token in one headline must outweigh one in four"
    assert all(v > 0 for v in w.values()), "smoothing must keep every weight positive"


def test_weighting_degrades_to_plain_jaccard_when_every_token_is_equally_common():
    """A two-item corpus sharing every word has no rarity signal — the weighted score must equal the
    plain one rather than collapsing to zero, or small corpora (and tests) would stop clustering."""
    a = cl.title_tokens("Senate passes the funding bill")
    b = cl.title_tokens("Senate passes the funding bill")
    w = cl.idf_weights([a, b])
    assert abs(cl.weighted_jaccard(a, b, w) - cl.jaccard(a, b)) < 1e-9


def test_weighted_jaccard_falls_back_when_no_weights_given():
    a, b = cl.title_tokens("Ferry runs aground"), cl.title_tokens("Ferry runs aground near port")
    assert cl.weighted_jaccard(a, b, None) == cl.jaccard(a, b)


def test_weighting_lowers_a_hub_token_match_more_than_a_rare_token_match():
    """The discrimination the feature exists for, stated as a relative claim rather than an absolute
    threshold: weighting must cost a common-word match MORE than a rare-word match."""
    titles = ["Trump defends tariffs on Canada saying they need us",
              "Trump defends tariffs on Mexico saying they need trade",
              "Berlin pride event canceled after vehicle drives into crowd",
              "Vehicle drives into crowd at Berlin pride event",
              "Trump nominates FIFA boss for UN post",
              "Trump promises help for Lebanon at White House"]
    corpus = [cl.title_tokens(t) for t in titles]
    w = cl.idf_weights(corpus)
    hub_drop = cl.jaccard(corpus[0], corpus[1]) - cl.weighted_jaccard(corpus[0], corpus[1], w)
    rare_drop = cl.jaccard(corpus[2], corpus[3]) - cl.weighted_jaccard(corpus[2], corpus[3], w)
    assert hub_drop > rare_drop, "weighting must penalise the common-word match more"


def test_idf_clustering_is_deterministic_and_still_matches_the_oracle():
    """Weights come from the input set, so the same input must give the same clusters — and the
    blocked implementation must still agree with all-pairs under weighting."""
    import random
    from datetime import datetime, timedelta, timezone

    rnd = random.Random(4242)
    vocab = [f"tok{i}" for i in range(40)]
    hub = "everywhere"
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)
    items = []
    for _ in range(40):
        toks = frozenset(rnd.sample(vocab, rnd.randint(3, 6)) + [hub])
        items.append({"t": toks, "d": now - timedelta(hours=rnd.random() * 100)})
    kw = dict(tokens=lambda x: x["t"], time=lambda x: x["d"], idf=True)
    first = _norm(cl.cluster(items, **kw))
    assert first == _norm(cl.cluster(items, **kw))                    # deterministic
    naive_kw = dict(tokens=lambda x: x["t"], time=lambda x: x["d"])
    weights = cl.idf_weights([i["t"] for i in items])

    def naive_idf():
        n = len(items)
        toks = [i["t"] for i in items]
        times = [i["d"] for i in items]
        dsu = cl.DSU(n)
        for i in range(n):
            if len(toks[i]) < cl.MIN_TITLE_TOKENS:
                continue
            for j in range(i + 1, n):
                if len(toks[j]) < cl.MIN_TITLE_TOKENS or len(toks[i] & toks[j]) < cl.MIN_SHARED_TOKENS:
                    continue
                if (cl.weighted_jaccard(toks[i], toks[j], weights) >= cl.DEFAULT_SIM
                        and cl.within_window(times[i], times[j], cl.DEFAULT_WINDOW_DAYS)):
                    dsu.union(i, j)
        groups = {}
        for i in range(n):
            groups.setdefault(dsu.find(i), []).append(i)
        return _norm(list(groups.values()))

    assert first == naive_idf(), "blocked and all-pairs must agree under weighting too"
    assert naive_kw  # (kept for symmetry with the unweighted oracle above)


def test_idf_is_off_by_default_and_can_be_switched_on(monkeypatch):
    """Off after measuring it against the live catalog: the story count and largest-cluster numbers
    improved, but 361 of 3,431 covered articles fell out of stories and only 16% of that loss came
    from the templates weighting was meant to punish. The on switch stays reachable without a
    deploy so the experiment can be re-run."""
    import story_service
    monkeypatch.delenv("RWE_CLUSTER_IDF", raising=False)
    assert story_service.use_idf() is False
    monkeypatch.setenv("RWE_CLUSTER_IDF", "1")
    assert story_service.use_idf() is True
    monkeypatch.setenv("RWE_CLUSTER_IDF", "0")
    assert story_service.use_idf() is False


# --------------------------------------------------------------------------- #
# Cluster-aware linkage (link_quorum) — the fix for single-linkage chaining.
# --------------------------------------------------------------------------- #
# A~B and B~C both pass the pairwise gate; A~C shares nothing. Single linkage welds all three
# together — the mechanism behind the production mega-cluster. These titles are built so the
# chain is explicit rather than incidental.
_CHAIN = (
    ("alpha beta gamma delta", 0),          # A
    ("alpha beta gamma epsilon zeta eta", 0),   # B — bridges A and C
    ("epsilon zeta eta theta", 0),          # C
)


def test_single_linkage_chains_a_and_c_through_b():
    """The baseline defect, stated as a test so the fix has something to be measured against."""
    assert _groups(_items(*_CHAIN)) == [[0, 1, 2]]


def test_link_quorum_breaks_the_chain_but_keeps_the_genuine_pair():
    """B still joins A — they really do match. C does not, because it matches only half the
    cluster: one of the two cross-pairs, below a 0.6 quorum."""
    assert _groups(_items(*_CHAIN), link_quorum=0.6) == [[0, 1], [2]]


def test_link_quorum_zero_is_exactly_single_linkage():
    """The default must not drift. 0.0 takes a separate code path that never sorts or tracks
    membership, and it has to agree with the transitive closure on every input."""
    import random
    rnd = random.Random(11)
    vocab = [f"word{i}" for i in range(40)]
    items = [{"t": " ".join(rnd.sample(vocab, 6)), "when": T0} for _ in range(120)]
    kw = dict(tokens=lambda x: cl.title_tokens(x["t"]), time=lambda x: x["when"])

    def closure():
        toks = [cl.title_tokens(i["t"]) for i in items]
        dsu = cl.DSU(len(items))
        for a in range(len(items)):
            for b in range(a + 1, len(items)):
                if (len(toks[a]) >= cl.MIN_TITLE_TOKENS and len(toks[b]) >= cl.MIN_TITLE_TOKENS
                        and len(toks[a] & toks[b]) >= cl.MIN_SHARED_TOKENS
                        and cl.jaccard(toks[a], toks[b]) >= cl.DEFAULT_SIM):
                    dsu.union(a, b)
        groups: dict = {}
        for i in range(len(items)):
            groups.setdefault(dsu.find(i), []).append(i)
        return sorted(sorted(g) for g in groups.values())

    assert sorted(sorted(g) for g in cl.cluster(items, link_quorum=0.0, **kw)) == closure()


def test_link_quorum_never_blocks_a_story_from_forming():
    """Two singletons have exactly one cross-pair — the pair that already passed the similarity
    gate — so even a quorum of 1.0 admits it. The rule constrains GROWTH, not formation, which is
    what keeps it off the catalog's median two-article story."""
    pair = _items(("harbour bridge closed after crash", 0), ("crash closes harbour bridge", 0))
    assert _groups(pair, link_quorum=1.0) == [[0, 1]]


def test_link_quorum_is_deterministic():
    """Quorum linkage is order-dependent by nature, so merges are consumed best-first. The result
    must still be identical across runs, or story IDs churn."""
    import random
    rnd = random.Random(3)
    vocab = [f"tok{i}" for i in range(30)]
    items = [{"t": " ".join(rnd.sample(vocab, 5)), "when": T0} for _ in range(80)]
    first = _groups(items, link_quorum=0.5)
    assert first == _groups(items, link_quorum=0.5)


def test_link_quorum_defaults_to_off():
    """Shipped disabled: it targets a real production failure, but the last change that tightened
    matching on equally sound reasoning cost 10.5% of covered articles."""
    assert cl.DEFAULT_LINK_QUORUM == 0.0


# --------------------------------------------------------------------------- #
# Candidate-walk optimizations — profiled at 76% of a whole build, exponent 2.15
# --------------------------------------------------------------------------- #
def test_postings_walk_skips_lower_indices_without_changing_groups():
    """The candidate walk yields pairs `i < j` only. Postings lists are built by `enumerate`, so
    they are sorted ascending and the `j <= i` half can be skipped by bisection instead of tested
    and discarded — for a token carried by `d` articles that is `d**2` steps where `d**2 / 2` will
    do, and the highest-frequency tokens (measured: the top TEN account for 86.4% of the walk's
    cost at 20,000 articles) have the most to skip.

    Pinned as a property rather than a timing: the grouping must be unchanged, which is the only
    thing that makes the optimization admissible."""
    items = [{"t": "senate passes funding bill late vote", "d": "2026-07-20T10:00:00Z"},
             {"t": "senate approves funding bill after vote", "d": "2026-07-20T11:00:00Z"},
             {"t": "wildfire spreads across southern france", "d": "2026-07-20T12:00:00Z"},
             {"t": "wildfire crews battle southern france blaze", "d": "2026-07-20T13:00:00Z"},
             {"t": "unrelated headline about knitting patterns", "d": "2026-07-20T14:00:00Z"}]
    groups = cl.cluster(items, tokens=lambda a: cl.title_tokens(a["t"]),
                                time=lambda a: cl.parse_time(a["d"]))
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 2, 2], sizes
    # Roots stay the LOWER index, which is what makes output order stable across runs.
    assert all(g == sorted(g) for g in groups)


def test_counting_shared_tokens_is_order_preserving():
    """`Counter.update(list)` replaced a Python-level `shared.get(j, 0) + 1` per posting — 6.7M
    interpreted calls at 8,000 articles. Counter is a dict subclass and `update` inserts in
    first-seen order, so `shared.items()` yields exactly what the manual loop did.

    That ordering is not cosmetic: it decides the order pairs are yielded, which decides DSU union
    order, which decides group roots. If it changed, clusters would still be 'correct' and story
    ids would silently churn."""
    from collections import Counter
    from bisect import bisect_right
    postings = {"a": [0, 3, 7, 9], "b": [1, 3, 5, 9], "c": [2, 3]}
    i = 0
    manual: dict = {}
    for tok in ("a", "b", "c"):
        for j in postings[tok]:
            if j > i:
                manual[j] = manual.get(j, 0) + 1
    fast: Counter = Counter()
    for tok in ("a", "b", "c"):
        pl = postings[tok]
        tail = pl[bisect_right(pl, i):]
        if tail:
            fast.update(tail)
    assert list(manual.items()) == list(fast.items())


# --------------------------------------------------------------------------- #
# Non-lexical edge evidence (X4, docs/STORY_ENTITY_EVIDENCE_PLAN.md). This layer receives opaque
# yes/no callables and must not know what the evidence IS — the country semantics live in
# story_service and are tested there. What is pinned here: None is byte-identical, a permissive
# callable changes nothing (including through the forced bookkeeping path), evidence reaches BOTH
# admission and quorum cross-pair scoring through the one shared predicate, and merge_ok gates
# unions with the real member lists.
# --------------------------------------------------------------------------- #
def test_permissive_evidence_and_merge_ok_change_nothing():
    """The gates at their weakest must reproduce the ungated result exactly — merge_ok forces the
    bookkeeping path even at quorum 0, and that path must group identically to the fast path."""
    items = _items(("harbour bridge closed after tanker crash", 0),
                   ("harbour bridge closed tanker crash downtown", 0),
                   ("tanker crash downtown fuel spill review", 0),
                   ("city budget passes after long debate", 1))
    plain = _groups(items)
    gated = _groups(items, evidence=lambda x, y: True, merge_ok=lambda a, b: True)
    assert plain == gated


def test_evidence_vetoes_a_pair_that_lexically_matches():
    items = _items(("senate passes the funding bill", 0),
                   ("senate passes the funding bill", 0))
    assert _groups(items) == [[0, 1]]
    assert _groups(items, evidence=lambda x, y: False) == [[0], [1]]


def test_evidence_is_consulted_by_quorum_cross_pairs():
    """One predicate for admission AND quorum support, so evidence cannot gate one and not the
    other. (0,2) is lexically fine but evidence-blocked: at quorum 0.9 the {0,1}+{2} merge needs
    both cross-pairs and only (1,2) survives, so the merge is refused."""
    items = _items(("harbour bridge tanker crash inquiry", 0),
                   ("harbour bridge tanker crash inquiry", 0),
                   ("harbour bridge tanker crash inquiry latest", 0))
    blocked = lambda x, y: {x, y} != {0, 2}   # noqa: E731 — veto exactly the (0,2) pair
    assert _groups(items, link_quorum=0.9) == [[0, 1, 2]]
    assert _groups(items, link_quorum=0.9, evidence=blocked) == [[0, 1], [2]]


def test_merge_ok_gates_growth_and_receives_member_lists():
    seen = []

    def gate(a, b):
        seen.append((list(a), list(b)))
        return len(a) < 2 and len(b) < 2      # allow formation, refuse growth past a pair

    items = _items(("senate passes the funding bill", 0),
                   ("senate passes the funding bill", 0),
                   ("senate passes the funding bill today", 0))
    assert _groups(items, merge_ok=gate) == [[0, 1], [2]]
    assert all(a == sorted(a) and b == sorted(b) for a, b in seen), \
        "gates reason over member lists, which the bookkeeping keeps sorted"
    assert ([0, 1], [2]) in seen or ([2], [0, 1]) in seen, "the refused growth attempt was seen"


def test_merge_ok_is_deterministic_across_runs():
    items = _items(("harbour bridge closed after tanker crash", 0),
                   ("harbour bridge closed tanker crash downtown", 0),
                   ("tanker crash downtown fuel spill review", 0))
    gate = lambda a, b: len(a) + len(b) <= 2   # noqa: E731
    assert _groups(items, merge_ok=gate) == _groups(items, merge_ok=gate)
