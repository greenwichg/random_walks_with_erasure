"""The X5 separability instrument (examples/audit_entity_separability.py).

The first production run measured its own join instead of the entities: story coverage entries
carry the DISPLAY url, the index was keyed by canonical, and 150 pairs came back from ~1,500
stories with zero both-covered. These tests pin the two lessons: members join through either
url form, and pairs are formed over entity-covered members so the measurement is conditional
on evidence existing rather than diluted by its absence.
"""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import audit_entity_separability as aes   # noqa: E402
import evidence_resolver as er            # noqa: E402
import store as store_mod                 # noqa: E402

NOW = datetime.now(timezone.utc)


def _feed(st, url, publisher, title):
    st.upsert_feed_article(
        canonical_url=er._canon(url), url=url, publisher=publisher, source_publisher=publisher,
        title=title, description="d", body=None,
        published_at=(NOW - timedelta(hours=2)).isoformat(), source_feed="f",
        scored={"article_id": er._canon(url), "outlet": publisher, "category": "Politics",
                "lean": 0.0, "political": True, "title": title})


def test_members_join_through_display_urls_and_pairs_are_conditional(tmp_path, capsys):
    """Display urls carry tracking params the canonicalizer strips — the exact mismatch that
    zeroed the first production run. Three same-story members, all entity-covered, all with
    display != canonical: the instrument must form 3 within-story pairs, every one of them
    both-covered."""
    st = store_mod.Store(f"sqlite:///{tmp_path / 'sep.db'}")
    title = "Landmark ruling reshapes the harbor bridge project"
    for i, pub in enumerate(["A", "B", "C"]):
        url = f"https://{pub.lower()}.example.com/harbor?utm_source=feed&ref={i}"
        _feed(st, url, pub, title)
        st.replace_article_entities(er._canon(url),
                                    {"person": ["jane doe"], "org": [f"org {i}"]})

    rc = aes.main(["--db", f"sqlite:///{tmp_path / 'sep.db'}", "--ubiquity", "0.9"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "3 pairs, 3 both-covered" in out, \
        "display-url members must join and every pair must be entity-covered by construction"
    assert "shared person 100.0%" in out, "all three share jane doe"


def test_uncovered_members_do_not_dilute_the_pairs(tmp_path, capsys):
    """Six members, two covered: the old pair formation produced 15 pairs with 1 both-covered
    (7%); the conditional formation produces exactly the 1 pair the data supports."""
    st = store_mod.Store(f"sqlite:///{tmp_path / 'dil.db'}")
    title = "Landmark ruling reshapes the harbor bridge project"
    for i in range(6):
        url = f"https://p{i}.example.com/harbor"
        _feed(st, url, f"Outlet {i}", title)
        if i < 2:
            st.replace_article_entities(er._canon(url), {"person": ["jane doe"]})

    rc = aes.main(["--db", f"sqlite:///{tmp_path / 'dil.db'}", "--ubiquity", "0.9"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "1 pairs, 1 both-covered" in out
