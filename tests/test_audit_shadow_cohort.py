"""The shadow evaluation runner — `examples/audit_shadow_cohort.py`, M8 of docs/SCALE_ROADMAP.md.

`test_source_evaluation.py` pins the policy. This pins the two things the RUNNER can get wrong,
both of which would produce confident, wrong numbers rather than an error:

1. **Syndication measured against the wrong population.** A shadow outlet's syndication partner is
   almost always a Tier A masthead it is republishing. Count carriers within the cohort alone and a
   lone republisher scores 0% — the exact outlet the ceiling exists to catch.
2. **Self-scoring.** If the assignment index contains the cohort's own coverage, every article
   attaches to itself and the rate is ~100% by construction.

Both are the same failure shape this audit series has already shipped three times: a lookup or a
population chosen slightly wrong, producing a plausible number that nothing contradicts.
"""
import pathlib
import sys
from datetime import datetime, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import audit_shadow_cohort as asc   # noqa: E402
import clustering                   # noqa: E402
import outlet_registry              # noqa: E402
import source_evaluation as se      # noqa: E402

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
HEADLINE = "Storm system brings record rainfall across the eastern seaboard"


def _row(publisher, host, title, *, created=NOW, published=NOW):
    return {"publisher": publisher, "title": title,
            "url": f"https://{host}/a{abs(hash(title)) % 9999}",
            "canonicalUrl": f"{host}/a{abs(hash(title)) % 9999}",
            "createdAt": created.isoformat(), "publishedAt": published.isoformat(),
            "fetchedAt": published.isoformat()}


def test_syndication_is_measured_against_the_tier_a_corpus_not_the_cohort():
    """The bug this function's docstring is about. A single republisher in the cohort has no peer
    inside it, so a cohort-only carrier index reports 0% syndication for an outlet that is running
    someone else's copy verbatim."""
    cohort = [_row("echodaily.example", "echodaily.example", HEADLINE)]
    tier_a = [_row("Reuters", "reuters.com", HEADLINE)]

    cohort_only = asc.carrier_index(cohort)
    both = asc.carrier_index(tier_a, cohort)
    toks = clustering.title_tokens(HEADLINE)

    assert len(cohort_only[toks]) == 1, "cohort-only sees one carrier — 0% syndication"
    assert len(both[toks]) == 2, "with Tier A in the population the republisher is visible"


def test_outlet_stats_flags_the_republisher_it_can_now_see():
    reg = outlet_registry.default_registry()
    cohort = [_row("echodaily.example", "echodaily.example", HEADLINE)]
    carriers = asc.carrier_index([_row("Reuters", "reuters.com", HEADLINE)], cohort)
    stats = asc.outlet_stats(cohort, reg, carriers, se.assignment_index([]), now=NOW)
    assert stats["echodaily.example"]["syndication"] == 1.0


def test_outlet_stats_reports_an_undatable_outlet_as_unknown_not_as_zero():
    """`observedDays=None` must survive into the table. Coerced to 0.0 it would read as "seen for
    zero days", which `evaluate` treats as a hard INSUFFICIENT DATA rather than an absent signal."""
    reg = outlet_registry.default_registry()
    row = _row("echodaily.example", "echodaily.example", HEADLINE)
    row["createdAt"] = None
    stats = asc.outlet_stats([row], reg, asc.carrier_index([row]), se.assignment_index([]), now=NOW)
    assert stats["echodaily.example"]["observedDays"] is None


def test_publisher_first_seen_reads_the_whole_catalog_and_survives_capitalisation(tmp_path):
    """The store side of the fix. `MIN(created_at)` per outlet, unbounded by any window, and an
    outlet arriving under two spellings keeps the EARLIEST — that is when we first saw it."""
    import store as store_mod
    st = store_mod.Store(f"sqlite:///{tmp_path}/fs.db")
    for i, (pub, when) in enumerate([("Echo Daily", "2026-06-01T00:00:00+00:00"),
                                     ("echo daily", "2026-08-01T00:00:00+00:00"),
                                     ("Other Outlet", "2026-07-01T00:00:00+00:00")]):
        st.upsert_feed_article(canonical_url=f"h{i}.example/a", url=f"https://h{i}.example/a",
                               publisher=pub, source_publisher=None, title="t", description="",
                               body=None, published_at=when, source_feed="t", scored={})
    seen = st.publisher_first_seen()
    assert set(seen) == {"echo daily", "other outlet"}

    narrowed = st.publisher_first_seen({"echo daily"})
    assert set(narrowed) == {"echo daily"}
    assert st.publisher_first_seen(set()) == {}


def test_member_key_uses_the_display_url_the_coverage_entry_carries():
    """`audit_source_cohort.member_key`'s bug, guarded in the second script that needs the same
    join. `canonicalUrl` is already lower-cased and stripped, so it misses on most real rows."""
    row = {"url": "https://Example.com/Path/?utm_source=x", "canonicalUrl": "example.com/path"}
    assert asc._member_key(row) == "https://Example.com/Path/?utm_source=x"


def test_outlet_stats_takes_observation_from_the_catalog_not_the_fetched_rows():
    """The production defect, at the runner's own seam. Rows fetched through a 6-day window report
    a 6-day history; `first_seen` is the catalog-wide MIN(created_at) and must win."""
    reg = outlet_registry.default_registry()
    row = _row("echodaily.example", "echodaily.example", HEADLINE,
               created=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc))
    args = (reg, asc.carrier_index([row]), se.assignment_index([]))

    windowed = asc.outlet_stats([row], *args, now=NOW)
    catalog = asc.outlet_stats([row], *args, now=NOW,
                               first_seen={"echodaily.example": "2026-06-01T00:00:00+00:00"})
    assert windowed["echodaily.example"]["observedDays"] == pytest.approx(6.0, abs=0.01)
    assert catalog["echodaily.example"]["observedDays"] > 80
    assert catalog["echodaily.example"]["firstSeen"] == "2026-06-01T00:00:00+00:00"


def test_identity_first_seen_finds_spellings_absent_from_the_window(tmp_path):
    """**The drift this fixes.** An outlet arriving under several publisher strings must contribute
    ALL of them, not only the ones the last 6 days happened to contain — otherwise its "first seen"
    moves whenever a variant ages out of the window, and the span shifts for a reason that has
    nothing to do with the outlet. The catalog is asked which strings belong to the identity."""
    import store as store_mod
    reg = outlet_registry.default_registry()
    st = store_mod.Store(f"sqlite:///{tmp_path}/ident.db")
    for i, pub in enumerate(["echodaily.example", "Echodaily.example"]):
        st.upsert_feed_article(canonical_url=f"h{i}.example/a", url=f"https://h{i}.example/a",
                               publisher=pub, source_publisher=None, title="t", description="",
                               body=None, published_at="2026-08-01T00:00:00+00:00",
                               source_feed="t", scored={})
    first, counts = asc.identity_first_seen(st, reg, {"echodaily.example"})
    assert set(first) == {"echodaily.example"}
    # Both spellings counted, though a window holding only one of them would have seen a single row.
    assert counts["echodaily.example"] == 2


@pytest.mark.parametrize("verdict, tier, expect", [
    # The production case: sportskeeda is in Tier A by grandfathering, and the run printed
    # "PROMOTE TO TIER B". For an outlet already in Tier A that is a DEMOTION wearing the word
    # "promote" — no number is wrong, the word is.
    ("PROMOTE TO TIER B", "A", "DEMOTION"),
    ("PROMOTE TO TIER B", "shadow", "UP from shadow"),
    ("PROMOTE TO TIER B", "B", "no change"),
    ("TIER A CANDIDATE", "shadow", "UP from shadow"),
    ("TIER A CANDIDATE", "A", "no change"),
    ("REJECT", "A", "DEMOTION"),
    ("REJECT", "shadow", "no change"),
])
def test_a_verdict_is_read_against_where_the_outlet_is_today(verdict, tier, expect):
    assert expect in asc.direction(verdict, tier)


@pytest.mark.parametrize("verdict", ["INSUFFICIENT DATA", "INSUFFICIENT VOLUME"])
def test_an_insufficient_verdict_has_no_direction(verdict):
    """It is not an instruction, so it must not be dressed as one in either direction."""
    assert asc.direction(verdict, "A") == ""


def test_catalog_first_seen_is_the_retention_floor(tmp_path):
    """The disambiguation `observedDays` needs. An outlet whose first-seen equals the oldest
    surviving row in the catalog has not been observed for that long — it just has not been trimmed,
    and its true first-seen is unknowable from what we still hold."""
    import store as store_mod
    st = store_mod.Store(f"sqlite:///{tmp_path}/floor.db")
    assert st.catalog_first_seen() is None
    st.upsert_feed_article(canonical_url="h.example/a", url="https://h.example/a",
                           publisher="Echo", source_publisher=None, title="t", description="",
                           body=None, published_at="2026-08-01T00:00:00+00:00", source_feed="t",
                           scored={})
    floor = st.catalog_first_seen()
    assert floor and floor == st.publisher_first_seen({"echo"})["echo"]


def test_window_bound_observation_is_detected():
    """A gate that cannot fire is worse than no gate — it reads as a measurement. The runner checks
    rather than trusting, because this exact shape has now appeared three times in its instruments."""
    bound = {"a": {"observedDays": 6.0}, "b": {"observedDays": 5.2}}
    free = {"a": {"observedDays": 6.0}, "b": {"observedDays": 41.0}}
    assert asc.observation_is_window_bound(bound, 6.0) is True
    assert asc.observation_is_window_bound(free, 6.0) is False


def test_window_bound_check_says_nothing_when_no_outlet_is_datable():
    """No spans is not evidence of the defect — claiming it would be its own false measurement."""
    assert asc.observation_is_window_bound({"a": {"observedDays": None}}, 6.0) is False


def test_self_scoring_guard_catches_a_cohort_scored_against_its_own_coverage():
    """The `--as-if` trap. Forget to rebuild the story set without the cohort and every article
    attaches to itself — a ~100% rate that looks like a strong result and measures nothing."""
    row = _row("echodaily.example", "echodaily.example", HEADLINE)
    with_it = [{"id": "s1", "coverage": [{"headline": HEADLINE, "url": asc._member_key(row),
                                          "publishedAt": NOW.isoformat()}]}]
    without = [{"id": "s1", "coverage": [{"headline": HEADLINE, "url": "https://reuters.com/a1",
                                          "publishedAt": NOW.isoformat()}]}]
    assert asc.self_scored([row], with_it) == 1
    assert asc.self_scored([row], without) == 0


def test_the_runner_scores_against_the_clusterer_not_a_local_rule():
    """Structural. The whole justification for extracting `clustering.pair_admits` is that there is
    ONE definition of "same event". A similarity expression appearing in this runner would be a
    second one, and it would drift."""
    src = (ROOT / "examples" / "audit_shadow_cohort.py").read_text()
    for banned in ("weighted_jaccard", "jaccard(", "DEFAULT_SIM", "within_window"):
        assert banned not in src, f"the runner must not re-implement the pair rule — found {banned!r}"
    assert "se.assignment_rate" in src


@pytest.mark.parametrize("field", ["assignmentRate", "assignmentStories", "attached"])
def test_the_runner_reports_assignment_but_never_branches_on_it(field):
    """`evaluate` is the only thing that turns stats into a verdict, and `test_source_evaluation`
    pins that it ignores these fields. This pins that the runner does not add its own gate around
    the outside — an `if s["attached"] > N` here would reintroduce exactly the threshold the policy
    module refuses to invent."""
    src = (ROOT / "examples" / "audit_shadow_cohort.py").read_text()
    for line in src.splitlines():
        stripped = line.strip()
        if field in stripped and stripped.startswith(("if ", "elif ", "assert ")):
            pytest.fail(f"runner branches on {field}: {stripped}")


def _seed(st, publisher, host, titles, when=NOW):
    for i, t in enumerate(titles):
        st.upsert_feed_article(canonical_url=f"{host}/a{i}", url=f"https://{host}/a{i}?utm=x",
                               publisher=publisher, source_publisher=None, title=t,
                               description="", body=None, published_at=when.isoformat(),
                               source_feed="f", scored={})


def test_as_if_matches_an_outlet_named_by_its_registry_canonical(tmp_path):
    """**The trap the unmatched-name message walked the reader into.** `main` lower-cases what the
    caller typed; `_identity` returns the registry canonical unmodified, and 571 of the registry's
    573 canonicals carry capitals. So the canonical branch of the comparison could never fire, and
    naming an outlet the documented way ("or it resolves to a registry canonical") reported it as
    NOT IN THE CATALOG — a wrong name and an unmatchable one are indistinguishable in that output.

    The outlet is seeded under a raw string that is NOT its canonical, so only the canonical branch
    can select it. Revert the `.lower()` in `_names` and this fails with an empty cohort."""
    import store as store_mod
    import story_service

    reg = outlet_registry.default_registry()
    resolved = reg.resolve("theguardian.com")
    assert resolved is not None and resolved.canonical != "theguardian.com", \
        "fixture needs a tracked outlet whose canonical differs from the raw string"

    st = store_mod.Store(f"sqlite:///{tmp_path}/asif.db")
    _seed(st, "theguardian.com", "theguardian.com",
          [f"Ferry service resumes after harbour dredging {k}" for k in range(3)],
          when=story_service._now() if hasattr(story_service, "_now") else NOW)

    m = asc.measure(st, reg, as_if={resolved.canonical.lower()})
    assert m["cohort"], "naming the outlet by its registry canonical selected nothing"
    assert m["unmatched"] == [], f"canonical reported as unmatched: {m['unmatched']}"
    assert all(r.get("publisher") == "theguardian.com" for r in m["cohort"])


def test_as_if_still_matches_an_outlet_named_by_its_raw_publisher_string(tmp_path):
    """The path that did work must keep working — the fix widens matching, it does not move it."""
    import store as store_mod

    reg = outlet_registry.default_registry()
    st = store_mod.Store(f"sqlite:///{tmp_path}/asif_raw.db")
    _seed(st, "Coastal Herald", "coastalherald.example",
          [f"Sea wall repairs begin at north quay {k}" for k in range(3)])

    m = asc.measure(st, reg, as_if={"coastal herald"})
    assert m["cohort"], "the raw publisher string no longer selects the outlet"
    assert m["unmatched"] == []


def test_as_if_select_derives_the_same_cohort_the_selector_would_print(tmp_path):
    """**The copy-paste step, removed.** Two production runs were spent on a placeholder
    string that reached the shell verbatim in place of a 254-name list; both times the
    unmatched-name guard refused to report. A guard firing twice on the same cause is an
    argument for removing the cause, so the audit can derive the cohort itself.

    Pinned as an EQUIVALENCE: the flag must select exactly what the selector prints, or the
    two paths answer different questions and the printed command stops being a check on it."""
    import select_asif_population as sel
    import store as store_mod
    import story_service

    reg = outlet_registry.default_registry()
    st = store_mod.Store(f"sqlite:///{tmp_path}/sel.db")
    for pub, host, stem in [("Coastal Herald", "coastalherald.example", "Sea wall repairs"),
                            ("theguardian.com", "theguardian.com", "Ferry service resumes")]:
        _seed(st, pub, host, [f"{stem} at north quay {k}" for k in range(4)])

    m = asc.measure(st, reg, as_if_share=0.9)
    printed = sel.cohort_names(story_service._fetch(st), reg, share=0.9)

    assert printed, "fixture must produce a non-empty cohort"
    assert m["cohort"], "--as-if-select selected nothing the selector would have named"
    assert m["unmatched"] == []
    assert {(r.get("publisher") or "").strip().lower() for r in m["cohort"]} == printed


def test_as_if_select_reads_the_corpus_once(monkeypatch, tmp_path):
    """The selector needs the Tier A rows and so does `measure`. Fetching twice would double
    the cost of the run and, worse, could select against a window that has since moved."""
    import store as store_mod
    import story_service

    st = store_mod.Store(f"sqlite:///{tmp_path}/once.db")
    _seed(st, "Coastal Herald", "coastalherald.example",
          [f"Sea wall repairs at north quay {k}" for k in range(4)])

    calls = []
    real = story_service._fetch
    monkeypatch.setattr(story_service, "_fetch", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    asc.measure(st, outlet_registry.default_registry(), as_if_share=0.9)
    assert len(calls) == 1, f"the corpus was fetched {len(calls)} times"


def test_naming_a_cohort_and_deriving_one_is_refused_rather_than_guessed(tmp_path):
    """`--as-if` names the cohort and `--as-if-select` derives it. Silently letting one win
    would mean the run's own header describes a cohort the reader did not ask for."""
    import store as store_mod
    st_path = f"sqlite:///{tmp_path}/both.db"
    _seed(store_mod.Store(st_path), "Coastal Herald", "coastalherald.example",
          [f"Sea wall repairs at north quay {k}" for k in range(4)])
    assert asc.main(["--db", st_path, "--as-if", "coastal herald", "--as-if-select"]) == 2


def test_the_share_flag_reaches_the_selector(tmp_path):
    """A `--share` that is read but not applied would report a cap it never enforced."""
    import store as store_mod
    reg = outlet_registry.default_registry()
    st = store_mod.Store(f"sqlite:///{tmp_path}/share.db")
    _seed(st, "Coastal Herald", "coastalherald.example",
          [f"Sea wall repairs at north quay {k}" for k in range(4)])
    _seed(st, "theguardian.com", "theguardian.com",
          [f"Ferry service resumes after dredging {k}" for k in range(4)])

    assert asc.measure(st, reg, as_if_share=0.9)["cohort"], "a wide cap must admit something"
    assert asc.measure(st, reg, as_if_share=0.01)["cohort"] == [], "a tiny cap must admit nothing"


# ── cohort-wide assignment ───────────────────────────────────────────────────────────────

HEAD_A = "Storm system brings record rainfall across the eastern seaboard"
HEAD_B = "Central bank holds interest rates steady for a third consecutive meeting"


def _story(sid, headline):
    return {"id": sid, "coverage": [{"headline": headline, "publishedAt": NOW.isoformat()},
                                    {"headline": headline, "publishedAt": NOW.isoformat()}]}


def test_cohort_assignment_takes_the_UNION_of_stories_not_the_per_outlet_sum():
    """**Why the per-outlet table cannot simply be summed.** `assignmentStories` is distinct
    *per outlet*, so two cohort outlets that both land on the same story contribute 1 each and
    adding the column reports 2 stories touched where the truth is 1. On a 254-outlet cohort
    that error compounds silently in the direction that flatters Tier B."""
    index = se.assignment_index([_story("s1", HEAD_A)])
    cohort = [_row("Outlet One", "one.example", HEAD_A),
              _row("Outlet Two", "two.example", HEAD_A)]

    per_outlet = [se.assignment_rate([r], index)["stories"] for r in cohort]
    whole = asc.cohort_assignment(cohort, index, asc.carrier_index(cohort))

    assert sum(per_outlet) == 2, "fixture must have both outlets landing, or there is no union"
    assert whole["stories"] == 1, "the cohort-wide count double-counted a shared story"
    assert whole["attached"] == 2 and whole["rate"] == 1.0


def test_cohort_assignment_reports_the_population_rate_over_every_article():
    """The number the experiment exists to produce. Half the cohort matches a story, half does
    not, so a rate that ignored the misses would read 100%."""
    index = se.assignment_index([_story("s1", HEAD_A)])
    cohort = ([_row("Outlet One", "one.example", HEAD_A) for _ in range(2)]
              + [_row("Outlet One", "one.example", "Local library extends its weekend hours")
                 for _ in range(2)])
    whole = asc.cohort_assignment(cohort, index, asc.carrier_index(cohort))
    assert whole["articles"] == 4 and whole["attached"] == 2 and whole["rate"] == 0.5


def test_duplicate_titles_are_counted_only_among_the_articles_that_attached():
    """§5's criterion separating new coverage from restored double-counting. Counted over the
    ATTACHED rows, not the whole cohort: a duplicate headline that lands nowhere costs nothing,
    and including it would dilute exactly the signal the measure exists to raise."""
    index = se.assignment_index([_story("s1", HEAD_A)])
    tier_a = [_row("Wire Service", "wire.example", HEAD_A)]
    # attaches AND is a duplicate of the wire copy
    dupe = _row("Echo Daily", "echo.example", HEAD_A)
    # a duplicate that attaches to nothing -- must not be counted
    orphan_dupe = _row("Echo Daily", "echo.example", HEAD_B)
    orphan_peer = _row("Wire Service", "wire.example", HEAD_B)

    carriers = asc.carrier_index(tier_a, [dupe, orphan_dupe, orphan_peer])
    whole = asc.cohort_assignment([dupe, orphan_dupe], index, carriers)

    assert whole["attached"] == 1
    assert whole["duplicateTitles"] == 1 and whole["duplicateRate"] == 1.0


def test_original_coverage_is_not_counted_as_a_duplicate():
    """The positive case. Without it the duplicate measure could report 100% always and every
    result would read as syndication."""
    index = se.assignment_index([_story("s1", HEAD_A)])
    # shares enough tokens to attach, but no other publisher ran this exact headline
    original = _row("Local Paper", "local.example",
                    "Storm system brings record rainfall to coastal counties overnight")
    whole = asc.cohort_assignment([original], index, asc.carrier_index([original]))
    assert whole["attached"] == 1, "fixture must attach for the duplicate check to mean anything"
    assert whole["duplicateTitles"] == 0 and whole["duplicateRate"] == 0.0


def test_the_population_block_is_printed(tmp_path, capsys):
    """The aggregate existing in `measure`'s dict but not on screen would be the same defect
    it fixes: the run that prompted it printed 30 of 254 outlets and no population rate."""
    import store as store_mod
    st_path = f"sqlite:///{tmp_path}/pop.db"
    st = store_mod.Store(st_path)
    _seed(st, "Coastal Herald", "coastalherald.example",
          [f"Sea wall repairs begin at north quay {k}" for k in range(4)])
    _seed(st, "theguardian.com", "theguardian.com",
          [f"Ferry service resumes after dredging {k}" for k in range(4)])

    asc.main(["--db", st_path, "--as-if-select", "--share", "0.9"])
    out = capsys.readouterr().out
    assert "the cohort as a POPULATION" in out
    assert "would attach" in out and "duplicate titles" in out


def test_articles_the_tokenizer_cannot_reach_are_split_out_of_the_rate():
    """**A zero that was never tested, reported as a zero that was.** `se.would_attach`
    returns None whenever a title yields fewer than `MIN_TITLE_TOKENS` tokens, and the shipped
    tokenizer is ASCII — a CJK, Arabic, Korean or Thai headline produces nearly none. The
    first production run's cohort carried 東森新聞, عدن الغد, youm7.com, 뉴시스 and 日テレnews nnn,
    all of which scored zero BEFORE the pair rule ran. Counting them in the denominator makes
    the population rate a floor and reads as evidence about Tier B when it is evidence about
    the tokenizer."""
    index = se.assignment_index([_story("s1", HEAD_A)])
    cohort = [_row("Outlet One", "one.example", HEAD_A),                    # reachable, lands
              _row("Outlet One", "one.example", "Council extends library hours downtown"),
              _row("東森新聞", "ebc.example", "台北市長宣布新政策"),          # 0 tokens
              _row("عدن الغد", "adenghad.example", "الحكومة تعلن خطة جديدة")]  # 0 tokens

    whole = asc.cohort_assignment(cohort, index, asc.carrier_index(cohort))
    assert whole["articles"] == 4 and whole["reachable"] == 2, \
        "the unspaced-script rows must not count as reachable"
    assert whole["attached"] == 1
    assert whole["rate"] == 0.25, "the floor is over every article"
    assert whole["reachableRate"] == 0.5, "the measurement is over the ones that could match"


def test_the_reachable_split_is_printed_beside_the_floor(tmp_path, capsys):
    """Both numbers, or a reader takes the floor for the measurement — which is exactly what
    happened when only one was on screen."""
    import store as store_mod
    st_path = f"sqlite:///{tmp_path}/reach.db"
    st = store_mod.Store(st_path)
    _seed(st, "Coastal Herald", "coastalherald.example",
          [f"Sea wall repairs begin at north quay {k}" for k in range(4)])
    _seed(st, "theguardian.com", "theguardian.com",
          [f"Ferry service resumes after dredging {k}" for k in range(4)])

    asc.main(["--db", st_path, "--as-if-select", "--share", "0.9"])
    out = capsys.readouterr().out
    assert "of REACHABLE" in out
    assert "score zero BEFORE anything is tested" in out
