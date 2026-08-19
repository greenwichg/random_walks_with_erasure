"""Tests for examples/settings_service.py — the dependency-free settings leaf.

Pins three things the C1 refactor promised:

* the module is a **leaf** — it imports nothing but the standard library, so it never drags the
  storage layer or the recommender into the import graph;
* :func:`settings_service.normalize_settings` / :data:`DEFAULT_SETTINGS` are the *same objects*
  ``api_server`` re-exports, so every existing caller keeps working unchanged; and
* the store-backed helpers (:func:`get` / :func:`update` / :func:`reading_goal_minutes` /
  :func:`theme` / :func:`language`) read and write through a real :class:`store.Store` correctly.

Behavioural equivalence with the pre-refactor engine is already covered by
``tests/test_api_server.py`` (which exercises ``api_server.normalize_settings`` via the re-export);
this file guards the *new* module boundary.
"""

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import notification_service as ns   # noqa: E402
import settings_service as ss   # noqa: E402
import store                    # noqa: E402


# --------------------------------------------------------------------------- #
# Leaf property — the whole point of the module.
# --------------------------------------------------------------------------- #
def test_settings_service_is_a_stdlib_only_leaf():
    """The source must import nothing but the standard library — no ``store``, no ``api_server``,
    no third-party package — so it can never introduce an import cycle."""
    src = (ROOT / "examples" / "settings_service.py").read_text()
    tops = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            tops.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:                       # a relative import would couple it to a package
                tops.add(f"<relative level {node.level}>")
            elif node.module:
                tops.add(node.module.split(".")[0])
    # The module imports only ``from __future__ import annotations``; nothing else is allowed.
    assert tops <= {"__future__"}, f"settings_service must stay a stdlib-only leaf; found: {sorted(tops)}"


# --------------------------------------------------------------------------- #
# Re-export identity — api_server's public surface is unchanged.
# --------------------------------------------------------------------------- #
def test_api_server_reexports_the_same_objects():
    import importlib.util
    spec = importlib.util.spec_from_file_location("api_server", ROOT / "examples" / "api_server.py")
    eng = importlib.util.module_from_spec(spec)
    sys.modules["api_server"] = eng
    spec.loader.exec_module(eng)

    # The names callers already import must resolve to the leaf's definitions, not copies.
    assert eng.normalize_settings is ss.normalize_settings
    assert eng.DEFAULT_SETTINGS is ss.DEFAULT_SETTINGS
    assert eng._SETTINGS_THEMES is ss._SETTINGS_THEMES
    assert eng._SETTINGS_LANGUAGES is ss._SETTINGS_LANGUAGES
    # The recommender-facing mapping deliberately stays in api_server (it depends on RWE vocabulary)…
    assert hasattr(eng, "rec_params_from_settings")
    # …and must NOT have leaked into the leaf.
    assert not hasattr(ss, "rec_params_from_settings")


# --------------------------------------------------------------------------- #
# Normalisation — defaults, coercion, allowlists, unknown-key drop.
# --------------------------------------------------------------------------- #
def test_normalize_settings_defaults_and_coercion():
    assert ss.normalize_settings(None) == ss.DEFAULT_SETTINGS
    assert ss.normalize_settings({}) == ss.DEFAULT_SETTINGS

    stored = {"theme": "dark", "readingGoalMinutes": 45,
              "notifications": {"weeklyDigest": False}, "bogus": 123}     # unknown key
    patch = {"politicalOpenness": 999, "language": " FR ",
             "notifications": {"streakReminders": True, "blindSpotAlerts": True}}
    m = ss.normalize_settings(stored, patch)

    assert m["theme"] == "dark" and m["readingGoalMinutes"] == 45          # from stored
    assert m["politicalOpenness"] == 100                                  # patch, clamped to [0, 100]
    assert m["language"] == "fr"                                          # trimmed + lower-cased
    assert m["notifications"]["weeklyDigest"] is False                    # stored (deep-merged)
    assert m["notifications"]["streakReminders"] is True                  # patch (deep-merged)
    assert m["notifications"]["blindSpotAlerts"] is True                  # a 2nd patch key, same group
    assert m["notifications"]["recommendations"] is True                  # untouched default survives
    assert "bogus" not in m                                               # unknown key dropped
    assert set(m) == set(ss.DEFAULT_SETTINGS)                             # stable shape

    # allowlist fallbacks
    assert ss.normalize_settings({"theme": "neon"})["theme"] == "system"
    assert ss.normalize_settings({}, {"language": "klingon"})["language"] == "en"
    assert ss.normalize_settings({"politicalOpenness": "abc"})["politicalOpenness"] == 50


def test_normalize_settings_does_not_mutate_defaults():
    """A patch must never write through into the shared DEFAULT_SETTINGS constant."""
    import copy
    snapshot = copy.deepcopy(ss.DEFAULT_SETTINGS)
    ss.normalize_settings({"notifications": {"recommendations": False}},
                          {"notifications": {"blindSpotAlerts": True}})
    assert ss.DEFAULT_SETTINGS == snapshot


# --------------------------------------------------------------------------- #
# Interest Intensity — the eight per-interest sliders (1–10; 5 = neutral).
# --------------------------------------------------------------------------- #
def test_interest_defaults_are_all_neutral():
    m = ss.normalize_settings(None)
    assert m["interests"] == {k: ss.INTEREST_DEFAULT for k in ss.INTEREST_KEYS}
    assert len(ss.INTEREST_KEYS) == 8                     # the eight-slider contract


def test_interest_patch_merges_per_leaf_not_per_group():
    """A patch of one slider leaves the other seven exactly as stored — the same per-leaf rule
    the notification category matrix follows, and what makes two devices safe to edit at once."""
    m = ss.normalize_settings({"interests": {"sports": 9}}, {"interests": {"science": 2}})
    assert m["interests"]["sports"] == 9                  # stored survives the patch
    assert m["interests"]["science"] == 2                 # patch applies
    assert m["interests"]["business"] == ss.INTEREST_DEFAULT   # untouched default survives


def test_interest_values_clamp_and_junk_falls_to_neutral():
    m = ss.normalize_settings({"interests": {"sports": 99, "science": -3, "health": "loud",
                                             "python": 10}})
    assert m["interests"]["sports"] == ss.INTEREST_MAX    # clamped high
    assert m["interests"]["science"] == ss.INTEREST_MIN   # clamped low
    assert m["interests"]["health"] == ss.INTEREST_DEFAULT   # junk -> neutral, never an extreme
    assert "python" not in m["interests"]                 # unknown interest dropped
    # a malformed group (not a dict) falls back to the all-neutral defaults
    assert ss.normalize_settings({"interests": "loud"})["interests"] == \
        {k: ss.INTEREST_DEFAULT for k in ss.INTEREST_KEYS}


def test_a_legacy_blob_without_interests_gains_neutral_defaults():
    """A stored blob from before the group existed normalizes to all-5 — the value that maps to
    no recommender parameter at all — so no legacy reader's feed moves. No migration."""
    m = ss.normalize_settings({"theme": "dark", "politicalOpenness": 80})
    assert m["interests"] == {k: ss.INTEREST_DEFAULT for k in ss.INTEREST_KEYS}
    assert m["politicalOpenness"] == 80                   # the political control is untouched


def test_legacy_privacy_keys_normalize_away_safely():
    """S1.2 backward-compat: the removed ``privacy`` group (shareAnonymizedMetrics /
    personalizedAds) is handled like any unknown key — a stored blob OR a patch carrying it
    normalizes without error and simply drops it, so old data and old clients keep working."""
    # An old stored blob (written before S1.2) still loads cleanly.
    legacy_stored = {"theme": "dark",
                     "privacy": {"shareAnonymizedMetrics": True, "personalizedAds": True}}
    m = ss.normalize_settings(legacy_stored)
    assert "privacy" not in m                        # dropped like any unknown key
    assert m["theme"] == "dark"                       # surviving fields untouched
    assert set(m) == set(ss.DEFAULT_SETTINGS)         # stable, privacy-free shape

    # An old client still PATCHing the removed keys is ignored — no error, and the real part of the
    # patch still applies.
    m2 = ss.normalize_settings(None, {"privacy": {"personalizedAds": True}, "weeklyReport": False})
    assert "privacy" not in m2
    assert m2["weeklyReport"] is False


# --------------------------------------------------------------------------- #
# Store-backed helpers — real Store round-trip.
# --------------------------------------------------------------------------- #
def _store_with_user():
    st = store.Store("sqlite://")                 # in-memory
    uid = st.upsert_user_by_identity("google", "acct-1", email="r@example.com").id
    return st, uid


def test_get_returns_normalised_defaults_when_unset():
    st, uid = _store_with_user()
    assert st.get_settings(uid) is None            # nothing persisted yet
    assert ss.get(st, uid) == ss.DEFAULT_SETTINGS  # …but get() fills honest defaults


def test_update_merges_persists_and_returns():
    st, uid = _store_with_user()

    first = ss.update(st, uid, {"theme": "dark", "readingGoalMinutes": 45})
    assert first["theme"] == "dark" and first["readingGoalMinutes"] == 45
    # a fresh read reflects the persisted write (round-trips through the real store)
    assert ss.get(st, uid) == first

    # a second partial patch merges over the stored value, leaving unrelated fields intact
    second = ss.update(st, uid, {"language": "es"})
    assert second["language"] == "es"
    assert second["theme"] == "dark"               # preserved from the first update
    assert second["readingGoalMinutes"] == 45      # preserved from the first update


def test_update_normalises_on_write():
    """Junk / out-of-range / unknown keys are coerced before they are persisted."""
    st, uid = _store_with_user()
    ss.update(st, uid, {"politicalOpenness": 999, "language": "klingon", "bogus": 1})
    stored = st.get_settings(uid)
    assert stored["politicalOpenness"] == 100      # clamped before save
    assert stored["language"] == "en"              # unsupported -> English
    assert "bogus" not in stored                   # unknown key never persisted


def test_scalar_accessors():
    st, uid = _store_with_user()
    # defaults before anything is set
    assert ss.reading_goal_minutes(st, uid) == ss.DEFAULT_SETTINGS["readingGoalMinutes"]
    assert ss.theme(st, uid) == "system"
    assert ss.language(st, uid) == "en"

    ss.update(st, uid, {"readingGoalMinutes": 30, "theme": "light", "language": "de"})
    assert ss.reading_goal_minutes(st, uid) == 30
    assert ss.theme(st, uid) == "light"
    assert ss.language(st, uid) == "de"


# --------------------------------------------------------------------------- #
# notifications.categories — the category x channel matrix (A2).
#
# The four flat toggles above are per-KIND booleans: they do not compose, and they cannot express
# "in the app but not on my lock screen". These pin the nested shape that can, including the reason
# the unread `push` key ships now — so stored blobs already carry the channel dimension and adding
# it later needs no migration.
# --------------------------------------------------------------------------- #
CATEGORIES = ("breaking", "digests", "recommendations", "product")


def test_category_defaults_are_in_app_on_and_push_off():
    cats = ss.normalize_settings(None)["notifications"]["categories"]
    assert sorted(cats) == sorted(CATEGORIES)
    for name in CATEGORIES:
        assert cats[name] == {"inApp": True, "push": False}, name


def test_a_patch_merges_per_leaf_not_per_category():
    """The property `_merge_bool_group` could not provide. Setting one channel of one category must
    leave the other channel, the other categories, and the flat toggles untouched."""
    out = ss.normalize_settings(
        {"notifications": {"categories": {"breaking": {"inApp": False, "push": True}}}},
        {"notifications": {"categories": {"breaking": {"push": False}}}})
    cats = out["notifications"]["categories"]
    assert cats["breaking"] == {"inApp": False, "push": False}, "patched leaf changed, sibling kept"
    assert cats["digests"] == {"inApp": True, "push": False}, "other categories untouched"
    assert out["notifications"]["weeklyDigest"] is True, "flat toggles untouched"


def test_layering_order_is_defaults_then_stored_then_patch():
    stored = {"notifications": {"categories": {"breaking": {"push": True}}}}
    assert ss.normalize_settings(stored)["notifications"]["categories"]["breaking"]["push"] is True
    both = ss.normalize_settings(stored, {"notifications": {"categories": {"breaking": {"push": False}}}})
    assert both["notifications"]["categories"]["breaking"]["push"] is False, "patch wins over stored"


def test_unknown_categories_and_channels_are_dropped():
    """Built only from the defaults, like every other key in this module — so a client inventing a
    category or a channel cannot widen the contract or smuggle a value past the gate."""
    out = ss.normalize_settings(None, {"notifications": {"categories": {
        "breaking": {"inApp": False, "telepathy": True},
        "sports": {"inApp": True},
    }}})
    cats = out["notifications"]["categories"]
    assert sorted(cats) == sorted(CATEGORIES), "no new category appeared"
    assert set(cats["breaking"]) == {"inApp", "push"}, "no new channel appeared"
    assert cats["breaking"]["inApp"] is False, "the known leaf in the same patch still applied"


def test_non_boolean_channel_values_are_coerced_not_passed_through():
    out = ss.normalize_settings(None, {"notifications": {"categories": {
        "breaking": {"inApp": "yes", "push": 0}}}})
    assert out["notifications"]["categories"]["breaking"] == {"inApp": True, "push": False}


def test_a_malformed_categories_group_falls_back_to_defaults():
    """A stored blob whose `categories` is not a dict (or whose category is not a dict) must
    normalise away rather than raise — the same fail-safe as any other malformed layer."""
    for bad in [{"notifications": {"categories": "nope"}},
                {"notifications": {"categories": ["breaking"]}},
                {"notifications": {"categories": {"breaking": "on"}}},
                {"notifications": "nope"}]:
        cats = ss.normalize_settings(bad)["notifications"]["categories"]
        assert cats["breaking"] == {"inApp": True, "push": False}, bad


def test_a_legacy_blob_without_categories_gains_them_with_no_loss():
    """Every stored blob in production predates this commit. It must keep its flat toggles exactly
    and acquire the category defaults — no migration, no reset."""
    legacy = {"theme": "dark", "readingGoalMinutes": 45,
              "notifications": {"recommendations": False, "weeklyDigest": False,
                                "streakReminders": True, "blindSpotAlerts": True}}
    out = ss.normalize_settings(legacy)
    assert out["notifications"]["recommendations"] is False
    assert out["notifications"]["streakReminders"] is True
    assert out["theme"] == "dark" and out["readingGoalMinutes"] == 45
    assert out["notifications"]["categories"]["breaking"] == {"inApp": True, "push": False}


def test_the_removed_group_still_normalises_away_alongside_the_new_one():
    """Regression guard for the reverse direction: reverting this commit must leave stored blobs
    containing `categories` harmless, exactly as the removed `privacy` group already is."""
    out = ss.normalize_settings({"privacy": {"personalizedAds": True},
                                 "notifications": {"categories": {"breaking": {"push": True}}}})
    assert "privacy" not in out
    assert out["notifications"]["categories"]["breaking"]["push"] is True


def test_gating_a_category_path_is_fail_closed_before_the_group_exists():
    """`notification_service._gated` walks a dotted path and returns False on any missing segment.
    That is the second safety layer under a category-gated kind: until this commit is deployed the
    path does not exist, so the kind cannot fire even if its events do."""
    normalised = ss.normalize_settings(None)
    assert ns._gated(normalised, "notifications.categories.breaking.inApp") is True
    assert ns._gated(normalised, "notifications.categories.breaking.push") is False
    assert ns._gated(normalised, "notifications.categories.sports.inApp") is False
    assert ns._gated({"notifications": {}}, "notifications.categories.breaking.inApp") is False
    assert ns._gated({}, "notifications.categories.breaking.inApp") is False


def test_merge_bool_group_skips_nested_subgroups():
    """`_merge_bool_group` is re-exported (api_server imports it), so its contract matters beyond
    this module's own call path: it merges a group of BOOLEANS, and `bool({...})` is `True` — a
    nested sub-group handed to it would silently become a truthy scalar and destroy the structure.

    Tested directly because `_merge_notifications` overwrites `categories` immediately afterwards,
    so through the public function the guard is invisible. A second consumer would not be so lucky."""
    defaults = {"flat": True, "nested": {"inApp": True}}
    out = ss._merge_bool_group(defaults, [{"g": {"flat": False}}], "g")
    assert out == {"flat": False}, "the nested sub-group must be skipped, not coerced to True"


def test_recommendation_country_normalizes_and_stays_independent_of_edition():
    """The For You country: ISO alpha-2 upper, anything else -> None (Global). It must NOT be
    coupled to ``edition`` — repointing that at the recommender would silently re-rank the feed
    of every reader who ever set a Local Pulse edition."""
    n = ss.normalize_settings
    assert n({})["recommendationCountry"] is None                 # default = Global
    assert n({"recommendationCountry": "in"})["recommendationCountry"] == "IN"
    for junk in ("", "IND", "1N", None, 7, {"a": 1}):
        assert n({"recommendationCountry": junk})["recommendationCountry"] is None

    # independence, both directions
    s = n({"edition": "GB"})
    assert s["edition"] == "GB" and s["recommendationCountry"] is None
    s = n({"recommendationCountry": "JP"})
    assert s["recommendationCountry"] == "JP" and s["edition"] is None
    s = n({"edition": "GB", "recommendationCountry": "JP"})
    assert (s["edition"], s["recommendationCountry"]) == ("GB", "JP")


def test_recommendation_country_patches_and_resets():
    """Persistence semantics: a patch sets it, and patching null resets to Global."""
    n = ss.normalize_settings
    assert n({"recommendationCountry": "FR"}, {"recommendationCountry": "DE"})[
        "recommendationCountry"] == "DE"
    assert n({"recommendationCountry": "FR"}, {"recommendationCountry": None})[
        "recommendationCountry"] is None
    assert n({"recommendationCountry": "FR"}, {})["recommendationCountry"] == "FR"  # untouched
