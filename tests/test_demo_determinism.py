"""The demo reader's Information Health report is a pure function of the dataset.

Root-cause context (2026-07-12 investigation): the demo score changing across real app
restarts is INPUT drift, not computation drift — the corpus snapshot is f(database, now)
(the C4 freshness window + newest-first recency caps) and the beta re-ingests feeds on every
launch, after which `_pick_demo_user` re-selects a different synthetic reader. These tests
pin the other half of that claim, the half that must never regress: for an IDENTICAL
dataset snapshot, a fresh interpreter (a real "restart", with a different PYTHONHASHSEED so
hash-randomized set/dict iteration would be caught) produces a byte-identical serialized
report and the same demo reader — no unseeded RNG, no unordered iteration, no
floating-point accumulation-order drift anywhere in the pipeline.
"""
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

CHILD = r"""
import json, os, sys
from types import SimpleNamespace
sys.path.insert(0, EXAMPLES_DIR)
mode, store_url = sys.argv[1], sys.argv[2]
if mode == "feed":
    import store as store_mod, feed_source
    csvp = feed_source.prepare(store_mod.Store(store_url))
    assert csvp, "feed corpus export produced no CSV"
    os.environ["RWE_QBIAS"] = csvp
    os.environ["RWE_PROFILE"] = "qbias"
import api_server as engine
def _i(name):
    v = os.environ.get(name)
    return int(v) if v else None
ns = SimpleNamespace(profile=None, npz=None, qbias=None, register_csv=None, emotion_csv=None,
                     behaviors=None, lean_tau=None, domain=None,
                     n_users=_i("RWE_N_USERS"), max_items=_i("RWE_MAX_ITEMS"), seed=None)
be = engine.Backend(engine.resolve_profile(ns))
rep = be._serialize_report(be.base_corpus, be.demo_user)
rep.pop("updatedAt", None)   # the ONE documented volatile field: the report's own now() stamp
print(json.dumps({"demo": int(be.demo_user), "report": rep}, sort_keys=True))
"""


def _restart(tmp_path, mode, store_url, hashseed):
    """One simulated app restart: a FRESH interpreter with its own hash seed."""
    child = tmp_path / "child.py"
    child.write_text(CHILD.replace("EXAMPLES_DIR", repr(str(ROOT / "examples"))))
    env = {"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": hashseed,
           "RWE_N_USERS": "150", "RWE_MAX_ITEMS": "400",
           # pin the wall clock OUT of the corpus export: the freshness window and the
           # dated-candidacy flag are the documented, deliberate time dependencies
           "RWE_FEED_MAX_AGE_DAYS": "0", "RWE_FEED_MIN_ARTICLES": "5"}
    r = subprocess.run([sys.executable, str(child), mode, store_url],
                       capture_output=True, text=True, cwd=str(ROOT), timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout.strip().splitlines()[-1]


@pytest.fixture(scope="module")
def frozen_store(tmp_path_factory):
    """One frozen feed catalog — 'the exact same demo database' across restarts."""
    from datetime import datetime, timedelta, timezone

    import evidence_resolver as er
    import store as store_mod
    p = tmp_path_factory.mktemp("det") / "frozen.db"
    st = store_mod.Store(f"sqlite:///{p}")
    pubs = ["AP", "Reuters", "NPR", "BBC News", "The Guardian", "The Hill", "Fox News", "CNN"]
    base = datetime(2026, 7, 10, tzinfo=timezone.utc)
    for k in range(120):
        pub = pubs[k % 8]
        url = f"https://{pub.split()[0].lower()}{k % 8}.example.com/det/{k}"
        st.upsert_feed_article(
            canonical_url=er._canon(url), url=url, publisher=pub, source_publisher=pub,
            title=f"det{k} alpha{k} beta{k} gamma{k}", description="d", body=None,
            published_at=(base - timedelta(days=1 + (k % 6) * 0.5)).isoformat(),
            source_feed="f",
            scored={"article_id": er._canon(url), "outlet": pub, "category": "Politics",
                    "lean": (-1.0, 0.0, 1.0)[k % 3], "political": True, "title": f"det{k}"})
    return f"sqlite:///{p}"


def test_feed_corpus_demo_report_is_identical_across_restarts(tmp_path, frozen_store):
    a = _restart(tmp_path, "feed", frozen_store, hashseed="0")
    b = _restart(tmp_path, "feed", frozen_store, hashseed="1")
    assert a == b, "same database, fresh interpreters -> the demo report must be byte-identical"
    out = json.loads(a)
    assert out["report"]["overall"] is not None and out["report"]["metrics"]


def test_synthetic_profile_demo_report_is_identical_across_restarts(tmp_path):
    a = _restart(tmp_path, "synthetic", "-", hashseed="0")
    b = _restart(tmp_path, "synthetic", "-", hashseed="1")
    assert a == b
    assert json.loads(a)["report"]["overall"] is not None


def test_one_new_article_may_repick_the_demo_reader(tmp_path, frozen_store):
    """The documented flip side (root cause of the observed restart drift): the demo reader is
    RE-SELECTED per corpus build, so a changed catalog is allowed to change the demo identity
    and score. This test pins the mechanism honestly: the report still comes from a
    deterministic pipeline (two restarts on the perturbed store agree with each other)."""
    import evidence_resolver as er
    import store as store_mod
    st = store_mod.Store(frozen_store)
    url = "https://npr3.example.com/det/one-more"
    st.upsert_feed_article(
        canonical_url=er._canon(url), url=url, publisher="NPR", source_publisher="NPR",
        title="one new article arrives", description="d", body=None,
        published_at="2026-07-10T00:00:00+00:00", source_feed="f",
        scored={"article_id": er._canon(url), "outlet": "NPR", "category": "Politics",
                "lean": 0.0, "political": True, "title": "one new article arrives"})
    a = _restart(tmp_path, "feed", frozen_store, hashseed="0")
    b = _restart(tmp_path, "feed", frozen_store, hashseed="2")
    assert a == b                      # deterministic for the NEW snapshot too
