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


def test_media_platform_and_country_names_are_not_evidence(tmp_path, capsys):
    """The 2026-08-16 run's df table: reuters(86), instagram(127) and 'united states'(132)
    counted as RARE shared evidence under the df floor, inflating the confusable overlap. Noise
    is identity, not frequency — a registry outlet, platform chrome or a country name never
    reaches the pair stats, and the filter reports what it removed."""
    st = store_mod.Store(f"sqlite:///{tmp_path / 'noise.db'}")
    title = "Landmark ruling reshapes the harbor bridge project"
    for i, pub in enumerate(["A", "B"]):
        url = f"https://{pub.lower()}.example.com/harbor"
        _feed(st, url, pub, title)
        st.replace_article_entities(er._canon(url), {
            "person": ["jane doe"],
            "org": ["reuters", "instagram", "united states", f"acme corp {i}"]})

    rc = aes.main(["--db", f"sqlite:///{tmp_path / 'noise.db'}", "--ubiquity", "0.9"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "noise filtered" in out and "reuters(2)" in out
    assert "org 0.0%" in out, \
        "with the noise gone, only the distinct acme corps remain and they do not overlap"
    assert "shared person 100.0%" in out

    rc = aes.main(["--db", f"sqlite:///{tmp_path / 'noise.db'}", "--ubiquity", "0.9",
                   "--no-noise-filter"])
    out = capsys.readouterr().out
    assert rc == 0 and "org 100.0%" in out, \
        "the escape hatch reproduces the raw first-run view for comparison"


def test_consensus_level_separability_is_measured(tmp_path, capsys):
    """The X4-shaped question: pairwise overlap understates the signal (same-story articles
    quote different people), so the instrument must also measure story-consensus agreement and
    confusable-story disjointness. Two 3-member stories with confusable titles and disjoint
    corroborated consensuses: member agreement 100%, story pair DISJOINT."""
    st = store_mod.Store(f"sqlite:///{tmp_path / 'cons.db'}")
    for i, pub in enumerate(["A", "B", "C"]):
        url = f"https://{pub.lower()}.example.com/harvard"
        _feed(st, url, pub, "Judge dismisses antisemitism lawsuit against Harvard University")
        st.replace_article_entities(er._canon(url), {"org": ["harvard university"],
                                                     "person": [f"lawyer {i}"]})
    for i, pub in enumerate(["D", "E", "F"]):
        url = f"https://{pub.lower()}.example.com/minnesota"
        _feed(st, url, pub,
              "Judge dismisses lawsuit over Minnesota transgender student sports policy rules")
        st.replace_article_entities(er._canon(url), {"org": ["state of minnesota"],
                                                     "person": [f"official {i}"]})

    rc = aes.main(["--db", f"sqlite:///{tmp_path / 'cons.db'}", "--ubiquity", "0.9"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "consensus stories   : 2" in out
    assert "(100.0%)  [false-split proxy" in out, "every member shares its story's consensus"
    assert "consensus-DISJOINT 1 (100.0%)" in out, \
        "the two court cases share no corroborated name — the gate would fire"
    assert "DISJOINT" in out and "Harvard" in out
