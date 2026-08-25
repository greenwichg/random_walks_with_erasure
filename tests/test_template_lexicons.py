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


# --------------------------------------------------------------------------- #
# The recall lexicon (production exhibit 2026-08-25: fruit-bars welded into eye-drops).
# --------------------------------------------------------------------------- #
def test_the_recall_bridge_is_vetoed_and_the_genuine_pairs_survive():
    """The weld could never happen directly (one shared token) — it rode a boilerplate BRIDGE.
    Under the recall lexicon that bridge's entire shared set is shape vocabulary, so the gate
    vetoes it; the genuine eye-drop pair keeps {eye, drops} and the genuine fruit-bar chain
    keeps {frozen, fruit, bars} as distinctive evidence."""
    fruit = {"headline": "Frozen fruit bars recalled nationwide over possible glass contamination"}
    eye = {"headline": "Nearly 40,000 bottles of eye drops recalled. See the affected product"}
    bridge = {"headline": "Eye drops recalled nationwide over possible contamination, FDA warns"}
    fruit2 = {"headline": "Frozen fruit bars sold in 30 states recalled over glass fears"}
    arts = [fruit, eye, bridge, fruit2]
    union = story_service._lexicon_union(("announce", "tracker", "preview", "recall"))
    ok = story_service._template_closure(arts, 0, lexicon=union)
    assert not ok(0, 2), "the bridge shares ONLY recall-shape tokens — vetoed"
    assert ok(1, 2), "the genuine eye-drop pair survives on {eye, drops}"
    assert ok(0, 3), "the genuine fruit-bar chain survives on {frozen, fruit, bars/glass}"


def test_recall_tokens_name_shape_never_subject():
    """Hazards and packaging are the SUBJECT half of a recall headline and must stay out of the
    lexicon, or genuine same-recall coverage loses its evidence."""
    for subject in ("glass", "listeria", "salmonella", "bottles", "bars", "eye", "drops"):
        assert subject not in story_service.RECALL_TOKENS


# --------------------------------------------------------------------------- #
# The corpus-derived boilerplate set — the generalisation of the manual lexicons.
# --------------------------------------------------------------------------- #
def _recall_week():
    """A six-day synthetic window with the recall genre's real shape: boilerplate vocabulary
    every day across genres, event names bursting in their own days."""
    arts = []
    shapes = ("recalled nationwide over possible contamination",
              "recall issued as warning to consumers nationwide",
              "recalled after possible contamination found, consumers urged")
    subjects = (("frozen fruit bars", "2026-08-19"), ("eye drops bottles", "2026-08-21"),
                ("toy trucks", "2026-08-20"), ("ground beef", "2026-08-22"),
                ("dog food", "2026-08-23"), ("lettuce salad kits", "2026-08-24"))
    for subject, day in subjects:
        for shape in shapes:
            arts.append({"headline": f"{subject} {shape}",
                         "publishedAt": f"{day}T09:00:00+00:00"})
    return arts


def test_the_derivation_finds_shape_and_spares_bursting_event_names():
    arts = _recall_week()
    der = story_service.derived_boilerplate(arts, min_df=6, min_days=4)
    assert {"recalled", "nationwide", "possible", "contamination", "consumers"} <= der, \
        "every-day, every-genre vocabulary is boilerplate"
    for name in ("fruit", "eye", "drops", "beef", "lettuce"):
        assert name not in der, "an event's name tokens burst in its own day and stay evidence"


def test_the_recall_bridge_dies_with_no_manual_lexicon_at_all():
    """The production weld, resolved by derivation alone: the bridge's entire shared set is
    derived boilerplate, while the genuine pairs keep their bursting product tokens."""
    arts = _recall_week()
    fruit = {"headline": "Frozen fruit bars recalled nationwide over possible glass contamination"}
    bridge = {"headline": "Eye drops recalled nationwide over possible contamination, FDA warns"}
    eye = {"headline": "Nearly 40,000 bottles of eye drops recalled. See the affected product"}
    der = story_service.derived_boilerplate(arts, min_df=6, min_days=4)
    ok = story_service._template_closure([fruit, bridge, eye], 0, lexicon=der)
    assert not ok(0, 1), "fruit<->bridge shares only derived boilerplate — vetoed"
    assert ok(1, 2), "the genuine eye-drop pair survives on {eye, drops}"


def test_the_derivation_is_deterministic_and_off_by_default(monkeypatch):
    arts = _recall_week()
    assert story_service.derived_boilerplate(arts, min_df=6, min_days=4) == \
        story_service.derived_boilerplate(arts, min_df=6, min_days=4)
    monkeypatch.delenv("RWE_CLUSTER_DERIVED_BOILERPLATE", raising=False)
    assert story_service.derived_boilerplate_on() is False
    # single-day corpora (every fixture) can never meet the day-spread floor: derived is empty
    one_day = [{"headline": a["headline"], "publishedAt": "2026-08-19T09:00:00+00:00"}
               for a in arts]
    assert story_service.derived_boilerplate(one_day, min_df=6, min_days=4) == frozenset()


def test_the_derivation_rediscovers_manual_lexicon_tokens():
    """The self-check the audit prints, in miniature: vocabulary the manual lexicons enumerate
    by hand falls out of the derivation when it behaves like boilerplate in the corpus."""
    arts = []
    for i, day in enumerate(("19", "20", "21", "22", "23")):
        arts.append({"headline": f"Show {i} season cast revealed, release date and trailer",
                     "publishedAt": f"2026-08-{day}T09:00:00+00:00"})
        arts.append({"headline": f"Film {i} box office collection day {i} worldwide gross",
                     "publishedAt": f"2026-08-{day}T09:00:00+00:00"})
    der = story_service.derived_boilerplate(arts, min_df=5, min_days=5)
    assert {"season", "cast", "release", "trailer"} <= der, "announce tokens rediscovered"
    assert {"box", "office", "collection", "worldwide"} <= der, "tracker tokens rediscovered"


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
