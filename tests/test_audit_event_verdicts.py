"""Stage 0.4 — the clustering audit's ``--event-verdicts`` counterfactual.

The judge's persisted verdicts are an INPUT to the build. This flag hands the AFTER side every
model verdict in the store so the vetoes the judge has earned can be priced by the same bars as
every other candidate — while the BEFORE side stays what production runs (no verdicts while
``RWE_EVENT_JUDGE`` is off). An empty verdict store must read as "not a measurement", never as
a quiet null result.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "examples"))

import audit_clustering_change as acc   # noqa: E402
import event_identity                   # noqa: E402
import evidence_resolver as er          # noqa: E402
import store as store_mod               # noqa: E402

AT = "2026-08-21T09:00:00+00:00"
ARTS = [
    ("https://x.com/eye1", "Nearly 40,000 bottles of eye drops recalled over possible "
                           "contamination nationwide", "P1"),
    ("https://x.com/eye2", "Eye drops recalled nationwide over possible contamination, FDA warns",
     "P2"),
    ("https://x.com/fruit", "Frozen fruit bars recalled nationwide over possible glass "
                            "contamination", "P3"),
]


def _seed(tmp_path, name):
    st = store_mod.Store(f"sqlite:///{tmp_path / name}")
    for url, title, pub in ARTS:
        st.upsert_feed_article(
            canonical_url=er._canon(url), url=url, publisher=pub, source_publisher=pub,
            title=title, description="", body=None, published_at=AT, source_feed="f",
            scored={"article_id": er._canon(url), "outlet": pub, "category": "Health",
                    "lean": 0.0, "title": title})
    return st


def test_an_empty_verdict_store_is_reported_not_measured(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RWE_CLUSTER_LINK_QUORUM", "0")
    monkeypatch.delenv("RWE_EVENT_JUDGE", raising=False)
    monkeypatch.setenv("RWE_STORIES_SCAN_DAYS", "100000")
    _seed(tmp_path, "empty.db")
    rc = acc.main(["--db", f"sqlite:///{tmp_path / 'empty.db'}", "--event-verdicts"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "judge telemetry    : model verdicts loaded 0" in out and "NO VERDICTS" in out
    assert "articles in a story: 3 -> 3" in out, "nothing to veto with: byte-identical"


def test_persisted_different_event_verdicts_veto_the_after_side_only(tmp_path, monkeypatch,
                                                                     capsys):
    monkeypatch.setenv("RWE_CLUSTER_LINK_QUORUM", "0")
    monkeypatch.delenv("RWE_EVENT_JUDGE", raising=False)
    monkeypatch.setenv("RWE_STORIES_SCAN_DAYS", "100000")
    st = _seed(tmp_path, "judged.db")
    fruit = er._canon("https://x.com/fruit")
    pairs = []
    for url, _, _ in ARTS[:2]:
        key = event_identity.pair_key(er._canon(url), fruit)
        pairs.append({"pair_key": key, "url_a": er._canon(url), "url_b": fruit})
    st.enqueue_event_pairs(pairs)
    for p in pairs:
        assert st.record_event_verdict(p["pair_key"], "different_event", source="model",
                                       model="claude-haiku-4-5")
    rc = acc.main(["--db", f"sqlite:///{tmp_path / 'judged.db'}", "--event-verdicts"])
    out = capsys.readouterr().out
    assert rc == 0
    before = next(l for l in out.splitlines() if l.startswith("before"))
    after = next(l for l in out.splitlines() if l.startswith("after"))
    assert "event-verdicts" in after and "event-verdicts" not in before
    assert "judge telemetry    : model verdicts loaded 2; in-band edges vetoed 2" in out
    assert "articles in a story: 3 -> 2" in out, \
        "the bridge article leaves the eye-drops story on the judge's word"
