"""The clustering-change auditor (examples/audit_clustering_change.py).

This tool exists to decide whether a threshold change is worth keeping, so its numbers have to be
trustworthy in the one direction that matters: a change that "improves" the story count by quietly
dropping articles out of every story is not an improvement. The counter alone cannot tell a fix from
a regression — an article leaving a press-release template is the change working, an article leaving
a 40-publisher wire story is the change costing coverage — so the attribution table is what the call
actually rests on. These tests pin both.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import audit_clustering_change as acc   # noqa: E402
import evidence_resolver as er          # noqa: E402
import store as store_mod               # noqa: E402

WHEN = "2026-07-10T09:00:00+00:00"


def _feed(st, url, publisher, title, when=WHEN):
    st.upsert_feed_article(
        canonical_url=er._canon(url), url=url, publisher=publisher, source_publisher=publisher,
        title=title, description="d", body=None, published_at=when, source_feed="f",
        scored={"article_id": er._canon(url), "outlet": publisher, "category": "Politics",
                "lean": 0.0, "political": True, "title": title})


def _store(tmp_path, name="a.db"):
    return store_mod.Store(f"sqlite:///{tmp_path / name}")


def test_coverage_retention_is_reported(tmp_path):
    """Story counts alone hide coverage loss. beforeCovered/afterCovered/droppedOut must be present
    and must agree with each other arithmetically."""
    st = _store(tmp_path)
    for i, pub in enumerate(["Outlet A", "Outlet B", "Outlet C"]):
        _feed(st, f"https://{i}.example.com/harbor", pub,
              "Landmark ruling reshapes the harbor bridge project")

    res = acc.compare(st, before=(1, 1), after=(1, 1), show=5)

    assert res["beforeCovered"] == res["afterCovered"] == 3   # identical params → identical outcome
    assert res["droppedOut"] == 0 and res["newlyCovered"] == 0
    assert res["beforeStories"] == res["afterStories"] == 1


def test_dropped_articles_are_attributed_to_the_cluster_they_left(tmp_path):
    """The number the keep/revert decision rests on. A tightened gate that dissolves a cluster must
    report WHERE the lost articles came from, with the articles-per-publisher tell attached — that is
    what separates a template collapsing (good) from a real story shedding members (bad)."""
    st = _store(tmp_path, "b.db")
    # Two headlines sharing exactly two distinct tokens: enough to merge at shared>=2, not at >=3.
    _feed(st, "https://a.example.com/x", "Outlet A", "Senate committee opens inquiry")
    _feed(st, "https://b.example.com/x", "Outlet B", "Senate committee adjourns")

    res = acc.compare(st, before=(2, 1), after=(3, 1), show=5)

    assert res["beforeCovered"] == 2 and res["afterCovered"] == 0
    assert res["droppedOut"] == 2
    assert len(res["droppedFrom"]) == 1
    lost = res["droppedFrom"][0]
    assert lost["lost"] == 2
    assert lost["articles"] == 2 and lost["publishers"] == 2
    assert lost["perPublisher"] == 1.0        # a broad cluster, not a one-outlet template
    assert "Senate committee" in lost["title"]


def test_per_publisher_flags_a_single_outlet_template(tmp_path):
    """The discriminator itself: one outlet publishing a template many times scores high on
    articles-per-publisher, which is how a spam collapse is told apart from a coverage regression."""
    st = _store(tmp_path, "c.db")
    for i in range(6):
        _feed(st, f"https://wire.example.com/deal/{i}", "Wire Desk",
              f"Sass Capital LLC Makes New Investment in Holdings {i} Incorporated")
    _feed(st, "https://other.example.com/deal", "Other Desk",
          "Sass Capital LLC Makes New Investment in Holdings Incorporated")

    res = acc.compare(st, before=(3, 1), after=(9, 1), show=5)

    assert res["droppedOut"] == 7
    lost = res["droppedFrom"][0]
    assert lost["publishers"] == 2 and lost["articles"] == 7
    assert lost["perPublisher"] == 3.5        # >> the ~1.4 a real multi-outlet story scores


def test_dropped_table_is_ordered_by_loss_and_respects_show(tmp_path):
    st = _store(tmp_path, "d.db")
    # A big cluster (4 members) and a small one (2), both dissolved by the same tightened gate.
    for i in range(4):
        _feed(st, f"https://p{i}.example.com/harbor", f"Outlet {i}",
              "Harbor bridge inquiry opens")
    for i in range(2):                      # token-disjoint from the above, so it stays its own cluster
        _feed(st, f"https://q{i}.example.com/ferry", f"Ferry Outlet {i}",
              "Ferry terminal review begins")

    res = acc.compare(st, before=(2, 1), after=(99, 1), show=1)

    assert res["droppedOut"] == 6
    assert len(res["droppedFrom"]) == 1                 # show= caps the table
    assert res["droppedFrom"][0]["lost"] == 4           # biggest loss first


def test_idf_side_is_scored_independently(tmp_path):
    """--idf must apply to the AFTER side only, so before/after stays a real comparison."""
    st = _store(tmp_path, "e.db")
    for i, pub in enumerate(["Outlet A", "Outlet B"]):
        _feed(st, f"https://{i}.example.com/harbor", pub, "Harbor bridge inquiry opens today")

    plain = acc.compare(st, before=(1, 1), after=(1, 1), show=5)
    with_idf = acc.compare(st, before=(1, 1), after=(1, 1), show=5, after_idf=True)

    # Two items sharing every token degrade to ordinary Jaccard under the smoothed weights, so the
    # cluster survives either way — the point is that requesting idf does not error or empty out.
    assert plain["afterStories"] == with_idf["afterStories"] == 1
    assert with_idf["afterCovered"] == 2


# --------------------------------------------------------------------------- #
# The verdict — bars fixed in advance, computed rather than eyeballed.
#
# The IDF experiment looked good on its headline numbers (more stories, half the largest cluster)
# and cost 10.5% of covered articles. Nobody would have accepted that if asked first, so the
# instrument now states the answer instead of leaving it to a reading of the table.
# --------------------------------------------------------------------------- #
def _res(**kw):
    base = {"beforeCovered": 1000, "droppedOut": 0, "beforeStories": 100, "afterStories": 100}
    base.update(kw)
    return base


def test_verdict_adopts_a_change_that_holds_coverage():
    v = acc.verdict(_res(droppedOut=20, afterStories=110))
    assert v["adopt"] is True and v["fails"] == []


def test_verdict_rejects_the_idf_failure_mode():
    """10.5% dropped — the measured cost of the last change that tightened matching."""
    v = acc.verdict(_res(droppedOut=105))
    assert v["adopt"] is False
    assert "dropped 10.5%" in v["fails"][0]


def test_verdict_rejects_a_falling_story_count():
    """The min_publishers cliff: splitting a 4-article/2-publisher cluster into 2+2 leaves two
    single-publisher fragments and BOTH are dropped. Oversplitting deletes stories rather than
    shrinking them, and the article counter alone does not show it."""
    v = acc.verdict(_res(afterStories=88))
    assert v["adopt"] is False and "min_publishers cliff" in v["fails"][0]


def test_verdict_bar_is_the_share_not_the_count():
    """A fixed article count would quietly loosen as the corpus grows."""
    assert acc.verdict(_res(beforeCovered=100, droppedOut=6))["adopt"] is False
    assert acc.verdict(_res(beforeCovered=10000, droppedOut=6))["adopt"] is True


def test_split_pieces_are_reported_with_their_titles():
    """The aggregates cannot answer the question that decides a split. 336 articles into 56 pieces
    is a fix if they are 56 separate events and a regression if one story was shredded, and both
    look identical in every counter. So the titles are carried out of compare(), biggest first."""
    st = store_mod.Store("sqlite://")
    # Two genuine pairs joined only through one bridging article — single linkage welds all four
    # into one cluster, a quorum separates them back into the two events.
    _feed(st, "https://a.example/1", "A", "harbour bridge closed after tanker crash")
    _feed(st, "https://b.example/1", "B", "harbour bridge closed tanker crash downtown fuel")
    _feed(st, "https://c.example/1", "C", "tanker crash downtown fuel review widens")
    _feed(st, "https://d.example/1", "D", "tanker crash downtown fuel review deepens")
    res = acc.compare(st, before=(3, 3), after=(3, 3), after_quorum=0.9, show=10)
    assert res["splitInto"], "a split must carry its pieces, not just a count"
    grp = res["splitInto"][0]
    assert grp["pieces"] == sorted(grp["pieces"], key=lambda p: -p["articles"])
    assert all(p["title"] for p in grp["pieces"])


# --------------------------------------------------------------------------- #
# Merge bars — a split and a join cannot be judged by the same rules.
# --------------------------------------------------------------------------- #
def _mres(**kw):
    base = {"beforeCovered": 1000, "droppedOut": 0, "beforeStories": 100, "afterStories": 78,
            "afterLargest": 100,
            "beforeCoherence": {"scored": 60, "bad": 3, "mean": 0.96},
            "afterCoherence": {"scored": 60, "bad": 3, "mean": 0.97}}
    base.update(kw)
    return base


def test_a_falling_story_count_is_the_point_of_a_merge():
    """Applying the split rules to a join would reject every good merge: 45 duplicate stories
    becoming 22 events IS the intended outcome, and a merge cannot strand a single-publisher
    fragment the way an oversplit can."""
    assert acc.verdict(_mres(), merging=True)["adopt"] is True
    assert acc.verdict(_mres())["adopt"] is False, "same numbers fail the SPLIT bars"


def test_a_merge_that_loses_articles_is_a_bug_not_a_trade_off():
    v = acc.verdict(_mres(droppedOut=4), merging=True)
    assert v["adopt"] is False and "never lose it" in v["fails"][0]


def test_a_merge_may_not_rebuild_the_blob():
    v = acc.verdict(_mres(afterLargest=140), merging=True)
    assert v["adopt"] is False and "rebuilding the blob" in v["fails"][0]


def test_the_independent_signal_can_veto_a_merge():
    """The only check a text-similarity merge cannot mark its own homework on: geoCoherence knows
    nothing about the text that produced the merge."""
    worse_count = acc.verdict(_mres(afterCoherence={"scored": 60, "bad": 5, "mean": 0.97}),
                              merging=True)
    assert worse_count["adopt"] is False and "bad clusters rose" in worse_count["fails"][0]
    worse_mean = acc.verdict(_mres(afterCoherence={"scored": 60, "bad": 3, "mean": 0.94}),
                             merging=True)
    assert worse_mean["adopt"] is False and "mean coherence fell" in worse_mean["fails"][0]


def test_the_baseline_carries_every_configured_knob_not_just_the_gates(monkeypatch):
    """The BEFORE side must be production in full. When only the admission gates were corrected,
    `repair` and `merge` stayed pinned at 0.0, so a --merge-sim run compared UNREPAIRED against
    UNREPAIRED + merge — and the clusters a merge exists to join are still fused inside the
    mega-cluster there, so it had nothing to do by construction. The verdict then failed on a
    350-article cluster the merge had never touched."""
    seen = {}
    monkeypatch.setattr(acc.story_service, "repair_quorum", lambda: 0.5)
    monkeypatch.setattr(acc.story_service, "build_stories",
                        lambda rows, **kw: seen.setdefault(len(seen), kw) or [])
    acc.build([], min_shared=3, min_tokens=3)
    assert seen[0]["repair"] is None, "None means 'resolve from config', not 'disabled'"
    assert seen[0]["merge"] is None and seen[0]["quorum"] is None


def test_a_merge_is_not_rejected_for_moving_its_own_denominator():
    """Measured: a merge pooled located members, lifted a pair over MIN_LOCATED_FOR_TRUST, and grew
    the scored set 67 -> 68. The new entry sat below the mean and pulled it down 0.966 -> 0.964 —
    0.002, entirely mechanical. Rejecting on that rejects arithmetic, not a bad merge. The
    bad-cluster COUNT is the rule with a fixed meaning, and it did not move."""
    v = acc.verdict(_mres(beforeCoherence={"scored": 67, "bad": 2, "mean": 0.966},
                          afterCoherence={"scored": 68, "bad": 2, "mean": 0.964}), merging=True)
    assert v["adopt"] is True


def test_a_real_coherence_collapse_still_rejects():
    v = acc.verdict(_mres(beforeCoherence={"scored": 67, "bad": 2, "mean": 0.966},
                          afterCoherence={"scored": 68, "bad": 2, "mean": 0.90}), merging=True)
    assert v["adopt"] is False and "denominator tolerance" in v["fails"][0]


def test_one_new_bad_cluster_rejects_regardless_of_the_mean():
    """The count has a fixed meaning whatever the scored set does, so it needs no tolerance."""
    v = acc.verdict(_mres(beforeCoherence={"scored": 67, "bad": 2, "mean": 0.966},
                          afterCoherence={"scored": 68, "bad": 3, "mean": 0.99}), merging=True)
    assert v["adopt"] is False and "bad clusters rose" in v["fails"][0]


def test_claim_counts_are_compared_on_identical_rows():
    """The live catalog went 57 -> 62 claims across a merge deploy and gained ~100 articles in the
    same interval, so that delta is confounded. Both sides here are built from ONE row set, which
    is what makes a merge's contribution to bias-summary eligibility attributable at all."""
    st = store_mod.Store("sqlite://")
    _feed(st, "https://a.example/1", "A", "harbour bridge closed after tanker crash")
    _feed(st, "https://b.example/1", "B", "harbour bridge closed after a crash downtown")
    res = acc.compare(st, before=(3, 3), after=(3, 3), show=5)
    assert "beforeClaims" in res and "afterClaims" in res
    assert res["beforeClaims"] == res["afterClaims"], "an unchanged build cannot move claims"


# --------------------------------------------------------------------------- #
# The baseline label — "[PRODUCTION BASELINE]" is a claim about the ENVIRONMENT, not just about
# before == configured. Every environment is self-consistent with its own defaults, so the old
# label certified any container as production: a backup-profile container (no `environment:`
# block, hence none of the deploy's clustering variables) ran the audit on 2026-08-16 and its
# single-linkage numbers — a 787-article mega-cluster — stood as the production baseline until
# the missing quorum/repair/merge tags gave them away.
# --------------------------------------------------------------------------- #
def _clear_deploy_env(monkeypatch):
    for k in acc._DEPLOY_CLUSTER_ENV:
        monkeypatch.delenv(k, raising=False)


def _seeded(tmp_path, name):
    st = _store(tmp_path, name)
    for i, pub in enumerate(["Outlet A", "Outlet B", "Outlet C"]):
        _feed(st, f"https://{i}.example.com/harbor", pub,
              "Landmark ruling reshapes the harbor bridge project")
    return f"sqlite:///{tmp_path / name}"


def test_an_environment_without_the_deploy_vars_cannot_claim_production(
        tmp_path, monkeypatch, capsys):
    """All eight variables absent → the run is the library fallbacks, and the output must say so
    loudly instead of certifying itself as production."""
    _clear_deploy_env(monkeypatch)
    rc = acc.main(["--db", _seeded(tmp_path, "envless.db")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "WARNING" in out and "deploy" in out
    assert "[LIBRARY FALLBACKS — no deploy env]" in out
    assert "[PRODUCTION BASELINE]" not in out


def test_one_deploy_var_is_evidence_enough_for_the_label(tmp_path, monkeypatch, capsys):
    """Compose sets every variable in the tuple, so even one present proves the env plumbing ran —
    and an operator's deliberate single override must not be shouted down as unconfigured."""
    _clear_deploy_env(monkeypatch)
    monkeypatch.setenv("RWE_CLUSTER_LINK_QUORUM", "0.2")
    rc = acc.main(["--db", _seeded(tmp_path, "envfull.db")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "WARNING" not in out
    assert "[PRODUCTION BASELINE]" in out


def test_presence_is_the_test_not_truthiness(monkeypatch):
    """A set-but-EMPTY variable is still evidence of the deploy env — compose writes `KEY=` for
    unset-valued vars (RWE_STORIES_SERVE_STALE does exactly this), and "" is falsy, so a
    truthiness check would treat an injected-but-empty environment as absent and re-open the
    trap."""
    _clear_deploy_env(monkeypatch)
    assert acc.deploy_env_present() is False
    monkeypatch.setenv("RWE_CLUSTER_IDF", "")
    assert acc.deploy_env_present() is True


def test_an_override_is_still_not_production_whatever_the_env(tmp_path, monkeypatch, capsys):
    """--before-min-shared moves the baseline off the configured values, and that axis keeps its
    own label: a configured environment with a synthetic BEFORE side is [not production]."""
    monkeypatch.setenv("RWE_CLUSTER_LINK_QUORUM", "0.2")
    rc = acc.main(["--db", _seeded(tmp_path, "override.db"), "--before-min-shared", "1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[not production]" in out
    assert "[PRODUCTION BASELINE]" not in out


# --------------------------------------------------------------------------- #
# X4 --geo-veto (docs/STORY_ENTITY_EVIDENCE_PLAN.md) — the AFTER-side flag and its telemetry.
# The veto semantics are pinned in test_story_service; here: threading (None = production, which
# is off), the after-only application, and that the telemetry the adoption decision reads is
# actually printed.
# --------------------------------------------------------------------------- #
import location as loc_mod   # noqa: E402


def test_veto_threads_as_none_meaning_production(monkeypatch):
    """Same contract as quorum/repair/merge: an unflagged run must hand build_stories None, so
    the baseline resolves to whatever production is configured with."""
    seen = {}
    monkeypatch.setattr(acc.story_service, "build_stories",
                        lambda rows, **kw: seen.setdefault(len(seen), kw) or [])
    acc.build([], min_shared=3, min_tokens=3)
    assert seen[0]["veto"] is None and seen[0]["veto_stats"] is None


def test_geo_veto_applies_to_the_after_side_and_prints_telemetry(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RWE_CLUSTER_LINK_QUORUM", "0")   # env present -> no fallback warning
    st = _store(tmp_path, "veto.db")
    title = "Massive earthquake strikes the region overnight rescue"
    for i, (pub, country) in enumerate([("US One", "US"), ("US Two", "US"),
                                        ("CO One", "CO"), ("CO Two", "CO")]):
        url = f"https://{i}.example.com/quake"
        _feed(st, url, pub, title)
        st.replace_article_event_locations(
            er._canon(url), [loc_mod.EventLocation(country=country, source="test")])

    rc = acc.main(["--db", f"sqlite:///{tmp_path / 'veto.db'}", "--geo-veto", "pair"])
    out = capsys.readouterr().out
    assert rc == 0
    before = next(l for l in out.splitlines() if l.startswith("before"))
    after = next(l for l in out.splitlines() if l.startswith("after"))
    assert "veto pair" in after and "veto" not in before, "the veto is an AFTER-side change"
    assert "geo-veto telemetry" in out and "vetoed" in out
    assert "clusters split     : 1" in out, "one lexical cluster severed into US and CO events"


def test_no_flag_no_telemetry(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RWE_CLUSTER_LINK_QUORUM", "0")
    st = _store(tmp_path, "quiet.db")
    _feed(st, "https://a.example.com/x", "A", "Landmark ruling reshapes the harbor bridge project")
    rc = acc.main(["--db", f"sqlite:///{tmp_path / 'quiet.db'}"])
    out = capsys.readouterr().out
    assert rc == 0 and "geo-veto telemetry" not in out, \
        "a None passthrough is production and must not imply the veto was measured"
