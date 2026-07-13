"""Tests for examples/seed_demo_reader.py — the offline demo-account provisioner.

Verifies it (1) creates the persisted dev/demo@infodiet.local account, (2) records reads drawn
from the catalog across the political spectrum, (3) is idempotent (a second run adds nothing),
(4) is narrow (touches only the demo account — the catalog and other users are untouched), and
(5) resolves through rec_sandbox's own ``_persisted_demo_user_id`` afterwards. Fully offline."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import evidence_resolver as er          # noqa: E402
import rec_sandbox                      # noqa: E402
import seed_demo_reader as seeder       # noqa: E402
import store as store_mod               # noqa: E402

PUBS = ["AP", "Reuters", "NPR", "The Guardian", "The Hill", "Fox News", "CNN", "BBC News"]


def _catalog(store, n=40):
    """A small scored catalog spanning left/centre/right leans."""
    for k in range(n):
        pub = PUBS[k % len(PUBS)]
        url = f"https://{pub.split()[0].lower()}{k}.example.com/a/{k}"
        store.upsert_feed_article(
            canonical_url=er._canon(url), url=url, publisher=pub, source_publisher=pub,
            title=f"headline {k} about federal policy", description="d", body=None,
            published_at="2026-01-01T00:00:00+00:00", source_feed="f",
            scored={"article_id": er._canon(url), "outlet": pub, "category": "Politics",
                    "lean": (-1.0, 0.0, 1.0)[k % 3], "political": True,
                    "title": f"headline {k} about federal policy"})


def test_seed_creates_account_and_spectrum_reads(tmp_path):
    st = store_mod.Store(f"sqlite:///{tmp_path/'seed.db'}")
    _catalog(st)
    r = seeder.seed(st)
    # the account exists and is the one the resolver looks up
    assert rec_sandbox._persisted_demo_user_id(st) == r["userId"]
    # 8 reads were recorded, spanning more than one lean bucket (a real diet, not one-sided)
    assert r["added"] == 8 and r["totalReads"] == 8
    reads = st.list_reads(r["userId"])
    leans = {rd["scored"].get("lean") for rd in reads}
    assert len(leans) >= 2                                   # left + centre/right, not all identical
    # every seeded read points at a real catalog article (connected to the graph)
    for rd in reads:
        assert st.get_feed_article(rd["canonicalUrl"]) is not None


def test_seed_is_idempotent_and_narrow(tmp_path):
    st = store_mod.Store(f"sqlite:///{tmp_path/'seed.db'}")
    _catalog(st)
    other = st.upsert_user_by_identity("dev", "someone-else").id
    before_catalog = st.count_feed_articles()

    first = seeder.seed(st)
    second = seeder.seed(st)                                 # re-run
    assert second["added"] == 0                             # nothing new the second time
    assert second["totalReads"] == first["totalReads"]
    # narrow: the catalog is untouched and no unrelated user gained reads
    assert st.count_feed_articles() == before_catalog
    assert st.count_reads(other) == 0


def test_seed_empty_catalog_creates_account_but_no_reads(tmp_path):
    st = store_mod.Store(f"sqlite:///{tmp_path/'empty.db'}")
    r = seeder.seed(st)                                      # no catalog
    assert r["added"] == 0 and r["totalReads"] == 0
    assert rec_sandbox._persisted_demo_user_id(st) == r["userId"]   # account still created
    # the CLI-facing main() reports the empty catalog and exits non-zero
    assert seeder.main([f"sqlite:///{tmp_path/'empty.db'}"]) == 1


def test_main_seeds_and_resolves(tmp_path, capsys):
    dburl = f"sqlite:///{tmp_path/'seed.db'}"
    _catalog(store_mod.Store(dburl))
    assert seeder.main([dburl]) == 0
    out = capsys.readouterr().out
    assert "demo@infodiet.local" in out and "total 8" in out
    # and rec_sandbox now resolves it
    assert rec_sandbox._persisted_demo_user_id(store_mod.Store(dburl)) is not None


def test_reset_replaces_history_instead_of_accumulating(tmp_path):
    import random
    st = store_mod.Store(f"sqlite:///{tmp_path/'seed.db'}")
    _catalog(st)
    first = seeder.seed(st)                                  # deterministic 8, no reset
    assert first["totalReads"] == 8 and first["cleared"] == 0
    again = seeder.seed(st, rng=random.Random(1), reset=True)   # clear + re-pick
    assert again["cleared"] == 8                            # the old reads were removed
    assert again["totalReads"] == 8                         # replaced, NOT accumulated to 16
    assert again["added"] == again["picked"]               # everything is fresh after the clear


def test_random_seed_is_reproducible_and_varies(tmp_path):
    import random
    st = store_mod.Store(f"sqlite:///{tmp_path/'seed.db'}")
    _catalog(st)

    def picks(seed):
        r = seeder.seed(st, rng=random.Random(seed), reset=True)
        return tuple(sorted(rd["canonicalUrl"] for rd in st.list_reads(r["userId"])))

    assert picks(1) == picks(1)                             # same seed -> same set (reproducible)
    variants = {picks(sd) for sd in range(6)}
    assert len(variants) >= 2                               # different seeds -> different histories


def test_count_controls_history_size(tmp_path):
    import random
    st = store_mod.Store(f"sqlite:///{tmp_path/'seed.db'}")
    _catalog(st)
    r = seeder.seed(st, target_reads=12, rng=random.Random(3), reset=True)
    assert r["totalReads"] == 12
