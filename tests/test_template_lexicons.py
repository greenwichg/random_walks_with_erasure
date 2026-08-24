"""The candidate clustering instruments (2026-08-24): lexicon-extended sole-boilerplate gate and
hyphen-compound tokens.

Neither changes production by default — both resolve exactly like every adopted knob (None/unset
= today's behavior, byte-identical), and both exist to be MEASURED by
``audit_clustering_change.py`` against the live catalog before any adoption. What these tests pin
is the seams: the defaults are identical to the pre-candidate behavior, the exhibit-shaped pairs
the candidates were registered against behave as registered, and the same-event controls survive.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "examples"))

import clustering       # noqa: E402
import story_service    # noqa: E402


# --------------------------------------------------------------------------- #
# Hyphen compounds (the xmen-pair defect: "x-men" survives only as "men").
# --------------------------------------------------------------------------- #
def test_hyphenated_compounds_gain_their_joined_token():
    base = clustering.title_tokens("'X-Men' cast, release date revealed at D23")
    assert "xmen" not in base and "men" in base, "the defect this candidate exists for"
    on = clustering.title_tokens("'X-Men' cast, release date revealed at D23",
                                 hyphen_compounds=True)
    assert "xmen" in on, "the franchise name becomes shared evidence"
    assert base <= on, "additive only: every existing token survives"


def test_hyphen_compound_filters_still_apply():
    on = clustering.title_tokens("a so-so 6-4 win", hyphen_compounds=True)
    assert "soso" in on
    assert "64" not in on, "joined pure digits stay dropped"


def test_hyphen_default_is_off_everywhere(monkeypatch):
    monkeypatch.delenv("RWE_CLUSTER_HYPHEN_COMPOUNDS", raising=False)
    assert story_service.hyphen_compounds() is False
    assert story_service.article_tokens({"headline": "X-Men at D23"}) == \
        clustering.title_tokens("X-Men at D23")


# --------------------------------------------------------------------------- #
# Lexicon selection — defaults byte-identical to the adopted announce gate.
# --------------------------------------------------------------------------- #
def test_default_lexicons_are_exactly_the_adopted_announce_set(monkeypatch):
    monkeypatch.delenv("RWE_CLUSTER_TEMPLATE_LEXICONS", raising=False)
    assert story_service.template_lexicons() == ("announce",)
    assert story_service._lexicon_union(("announce",)) == story_service.TEMPLATE_TOKENS


def test_lexicon_env_parses_and_never_widens_on_junk(monkeypatch):
    monkeypatch.setenv("RWE_CLUSTER_TEMPLATE_LEXICONS", "announce, tracker,preview")
    assert story_service.template_lexicons() == ("announce", "tracker", "preview")
    monkeypatch.setenv("RWE_CLUSTER_TEMPLATE_LEXICONS", "bogus,,nonsense")
    assert story_service.template_lexicons() == ("announce",), \
        "junk falls back to the adopted gate, never to no-lexicon"


def test_lexicons_name_shapes_never_subjects():
    for name, lex in story_service.TEMPLATE_LEXICONS.items():
        assert lex, name
        for work in ("batwara", "vishwanath", "mirzapur", "sinner", "alcaraz"):
            assert work not in lex, f"{name} must never contain a subject token"


# --------------------------------------------------------------------------- #
# The gate rule over the registered exhibit shapes.
# --------------------------------------------------------------------------- #
def _closure(headlines, lexicon):
    arts = [{"headline": h} for h in headlines]
    return story_service._template_closure(arts, 0, None, lexicon=lexicon)


def test_tracker_lexicon_vetoes_the_day_counter_weld_and_keeps_the_same_film():
    union = story_service._lexicon_union(("announce", "tracker"))
    ok = _closure([
        "Batwara box office collection day 2 worldwide gross",     # 0
        "Vishwanath box office collection day 2 worldwide gross",  # 1: different film
        "Batwara box office collection day 3 worldwide gross",     # 2: same film, next day
    ], union)
    assert not ok(0, 1), "different films share ONLY tracker boilerplate — vetoed"
    assert ok(0, 2), "the same film shares its title outside the lexicon — survives"


def test_preview_lexicon_vetoes_the_fixture_preview_weld_and_keeps_the_same_match():
    union = story_service._lexicon_union(("announce", "preview"))
    ok = _closure([
        "Sinner vs Musetti preview, head to head, odds and picks",
        "Gauff vs Swiatek preview, head to head, odds and picks",
        "Sinner vs Musetti: preview and betting odds",
    ], union)
    assert not ok(0, 1), "different matches share only preview boilerplate — vetoed"
    assert ok(0, 2), "the same match shares its player names — survives"


def test_announce_only_union_is_the_production_gate():
    """With the default lexicon set, the closure is byte-identical to the adopted Phase B gate:
    the tracker exhibit pair is NOT vetoed (which is today's behavior — and the failure the
    tracker candidate exists to measure)."""
    union = story_service._lexicon_union(story_service.template_lexicons())
    ok = _closure([
        "Batwara box office collection day 2 worldwide gross",
        "Vishwanath box office collection day 2 worldwide gross",
    ], union)
    assert ok(0, 1), "production today does not veto the day-counter weld"


def test_build_stories_accepts_the_candidate_knobs():
    import inspect
    params = inspect.signature(story_service.build_stories).parameters
    assert "lexicons" in params and "hyphen" in params
    assert params["lexicons"].default is None and params["hyphen"].default is None, \
        "None = whatever production is configured with — the knob discipline"
