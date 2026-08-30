"""ONE lean vocabulary for the UI (docs/LEAN_CONSISTENCY.md, fixes F1/F3/F4).

The audit measured the same outlet serving two numbers and two labels depending on the page:
recommendation cards carried the corpus-internal ranking position (CNN −0.6) while Discover,
search, stories and the analyzer carried the scored registry lean (CNN −1.0). These tests pin the
repaired contract — every user-facing surface serves the SCORED registry value — plus the
scored→position lattice mapping that keeps the reader's click-mean in one space.
"""
import importlib.util
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import evidence_resolver as er   # noqa: E402
import store as store_mod        # noqa: E402
import validate_qbias as vq      # noqa: E402


def _iso(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# Registry-lattice scored leans, exactly what ingest.Scorer writes from outlet_registry.csv.
PUBS = [("CNN", -1.0), ("NPR", -1.0), ("BBC", 0.0), ("Associated Press", 0.0),
        ("Geo TV", 1.0), ("Fox News", 2.0), ("New York Post", 2.0), ("Reuters", 0.0)]


def _seed(st, n=240):
    for k in range(n):
        pub, lean = PUBS[k % len(PUBS)]
        u = f"https://{pub.split()[0].lower()}.example.com/a/{k}"
        st.upsert_feed_article(
            canonical_url=er._canon(u), url=u, publisher=pub, source_publisher=pub,
            title=f"filing{k} memo{k} briefing{k} notice{k} dossier{k}", description="d",
            body=None, published_at=_iso(1 + (k % 20) / 24), source_feed="f",
            scored={"article_id": er._canon(u), "outlet": pub, "category": "Politics",
                    "lean": lean, "political": True, "title": f"t{k}"})


def test_scored_to_position_is_the_same_grading_as_the_labels():
    """The number path and the label path must land on the same lattice point for every registry
    value, or the corpus and the novel columns drift apart again."""
    g = vq.LEAN_GRADE
    assert vq.scored_to_position(-2.0) == -1.0 and vq.scored_to_position(2.0) == 1.0
    assert vq.scored_to_position(-1.0) == -g and vq.scored_to_position(1.0) == g
    assert vq.scored_to_position(0.0) == 0.0 and vq.scored_to_position(0.49) == 0.0
    assert vq.scored_to_position(-1.5) == -1.0 and vq.scored_to_position(1.5) == 1.0   # boundary
    assert vq.scored_to_position(0.5) == g                                             # boundary
    assert np.isnan(vq.scored_to_position(None))
    assert np.isnan(vq.scored_to_position(float("nan")))
    assert np.isnan(vq.scored_to_position("junk"))
    # parity with the label pipeline for every lattice value
    import feed_source
    for v in (-2.0, -1.0, 0.0, 1.0, 2.0):
        via_label = vq.label_to_pos(feed_source._bias_label(v), graded=True)
        assert vq.scored_to_position(v) == pytest.approx(via_label), v


def test_novel_reads_land_on_the_position_lattice_not_the_registry_scale():
    """augment() concatenated raw [-2, 2] leans into the positions array — a novel Fox read
    weighed +2.0 in the reader's click-mean where a catalog-joined one weighed +1.0. Now every
    novel column lands on the corpus lattice, and an unknown lean stays NaN (no fabricated
    centre)."""
    import api_server
    import augmented_corpus as ac
    backend = api_server.Backend(api_server.DatasetProfile.synthetic(n_users=60, max_items=150,
                                                                     seed=0))
    bundle = ac.bundle_from_backend(backend)
    reads = [ac.ScoredRead(article_id="n-fox", outlet="Fox News", category="politics",
                           lean=2.0, political=True),
             ac.ScoredRead(article_id="n-cnn", outlet="CNN", category="politics",
                           lean=-1.0, political=True),
             ac.ScoredRead(article_id="n-unk", outlet="Nobody", category="politics",
                           lean=None, political=True)]
    aug = ac.augment(bundle, reads, user_id="42")
    tail = np.asarray(aug.bundle.mind.item_positions, dtype=float)[-3:]
    assert tail[0] == 1.0, "a full-pole novel read lands at ±1, not ±2"
    assert tail[1] == -vq.LEAN_GRADE, "a lean novel read keeps its grade"
    assert np.isnan(tail[2]), "an unknown lean stays NaN — never a fabricated centre"


@pytest.fixture(scope="module")
def app_client(tmp_path_factory, module_env):
    import os
    tmp = tmp_path_factory.mktemp("leancons")
    for k, v in {"RWE_DB_URL": f"sqlite:///{tmp}/lc.db", "RWE_RECS_SOURCE": "feed",
                 "RWE_FEED_MIN_ARTICLES": "5", "RWE_CORPUS_MIN_ARTICLES": "5",
                 "RWE_SEED": "0", "RWE_STORY_SLOT": "0"}.items():
        module_env.setenv(k, v)
    module_env.delenv("RWE_INTERNAL_SECRET", raising=False)
    module_env.delenv("RWE_FEED_MAX_AGE_DAYS", raising=False)
    st = store_mod.Store(os.environ["RWE_DB_URL"])
    _seed(st)
    spec = importlib.util.spec_from_file_location("api_leancons",
                                                  ROOT / "examples" / "api_fastapi.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["api_leancons"] = mod
    spec.loader.exec_module(mod)
    from fastapi.testclient import TestClient
    with TestClient(mod.app) as client:
        uid = client.post("/api/internal/users",
                          json={"provider": "google", "providerAccountId": "lc"}).json()["userId"]
        h = {"X-IH-User-Id": str(uid)}
        client.post("/api/me/reads", headers=h, json={"reads": [
            {"url": f"https://{p.split()[0].lower()}.example.com/a/{k}", "title": "t", "outlet": p}
            for k, (p, _l) in list(enumerate([PUBS[i % len(PUBS)] for i in range(32)]))]})
        yield client, h, st


def test_rec_cards_serve_the_scored_registry_lean(app_client):
    """F1+F3, the audit's headline: a recommendation card's lean, bucket, and publisherLean are
    the CATALOG's scored values — the same numbers Discover serves — never the corpus-internal
    ranking position. Mutation-checked: dropping the enrichment override re-serves positions
    (CNN −0.6) and this fails."""
    client, h, st = app_client
    scored_of = {}
    for a in client.get("/api/discover?limit=200").json()["articles"]:
        scored_of[a["id"]] = a.get("lean")

    recs = client.get("/api/recommendations", headers=h).json()
    assert recs, "the stack must serve a feed"
    checked = 0
    for r in recs:
        a = r["article"]
        cu = er._canon(str(a.get("url") or ""))
        if cu not in scored_of:
            continue                                    # not a catalog article (none expected here)
        checked += 1
        assert a["lean"] == scored_of[cu], \
            f"{a['publisher']}: rec card serves {a['lean']}, Discover serves {scored_of[cu]}"
        expected_bucket = ("center" if abs(scored_of[cu]) <= 0.5
                           else ("left" if scored_of[cu] < 0 else "right"))
        assert a["leanBucket"] == expected_bucket
        assert a["publisherLean"] == round(float(scored_of[cu]), 2)
        assert a["publisherLean"] == pytest.approx(round(a["publisherLean"], 2))   # no float noise
    assert checked >= 5, "the contract must actually be exercised across cards"


def test_cross_flag_and_scored_lean_can_never_disagree_on_sidedness(app_client):
    """The crossCutting flag is computed from the ranking position upstream of the enrichment;
    the served lean is scored. The two spaces share a byte-identical sided/centre partition, so a
    card can never claim 'another political perspective' while displaying a centre lean."""
    client, h, _st = app_client
    for r in client.get("/api/recommendations", headers=h).json():
        a = r["article"]
        if r.get("crossCutting") and a.get("lean") is not None:
            assert abs(float(a["lean"])) >= 0.5, \
                f"{a['publisher']}: cross-cutting card displays a centre lean {a['lean']}"
