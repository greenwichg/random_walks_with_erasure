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
