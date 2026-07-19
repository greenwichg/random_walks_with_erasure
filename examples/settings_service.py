"""Reader product preferences (settings) — the dependency-free source of truth.

This is a **leaf** module: it imports nothing but the standard library, so it can be shared by
the engine (:mod:`api_server`), the coach, and any future subsystem without dragging the storage
layer or the recommender into the import graph. It owns two things:

* the **schema** — :data:`DEFAULT_SETTINGS` plus the small allowlists (:data:`_SETTINGS_THEMES`,
  :data:`_SETTINGS_LANGUAGES`) that pin the contract; and
* **normalisation** — :func:`normalize_settings`, which layers server defaults < the user's stored
  preferences < an optional incoming patch and coerces every value to the stable contract, so a
  partial update from any client (web, iOS, Android, extension, RSS) is always safe.

The store-backed convenience helpers (:func:`get`, :func:`update`, :func:`reading_goal_minutes`,
:func:`theme`, :func:`language`) take the engine's ``store`` **by argument** — exactly like
``corpus_validation.evaluate(store)`` — so this module never imports :mod:`store` and stays a leaf.

What deliberately lives **elsewhere**: the mapping of the two recommendation sliders
(``politicalOpenness`` / ``recommendationStrength``) to per-request RWE-B/RWE-D hyperparameters is
``api_server.rec_params_from_settings`` — that is the only settings code that depends on recommender
vocabulary, so keeping it out of this leaf keeps the leaf free of RWE concepts.
"""

from __future__ import annotations


# --------------------------------------------------------------------------- #
# Schema — defaults + allowlists. This is the contract every client sees.
# --------------------------------------------------------------------------- #
DEFAULT_SETTINGS = {
    "theme": "system",
    "language": "en",
    "politicalOpenness": 50,          # 50 = the stack's default RWE-B epsilon (0.9)
    "recommendationStrength": 50,     # 50 = the stack's default RWE-D beta (0.5)
    "readingGoalMinutes": 20,
    "weeklyReport": True,
    "monthlyReport": False,
    "notifications": {"recommendations": True, "weeklyDigest": True,
                      "streakReminders": False, "blindSpotAlerts": False},
    # A `privacy` group (shareAnonymizedMetrics / personalizedAds) was removed in S1.2: neither
    # field was consumed by any behavior, and one contradicted the product's privacy policy. Legacy
    # stored blobs / patches carrying those keys normalize away safely — dropped like any unknown
    # key (see ``normalize_settings``), so no migration is needed.
}
_SETTINGS_THEMES = ("light", "dark", "system")
# Supported interface languages (Commit 20). An unsupported/garbage value falls back to English —
# the same allowlist the web LanguageProvider enforces, so the two never disagree.
_SETTINGS_LANGUAGES = ("en", "es", "fr", "de", "pt")


# --------------------------------------------------------------------------- #
# Normalisation — pure dict-in / dict-out, no I/O.
# --------------------------------------------------------------------------- #
def _clamp_int(value, lo, hi, default):
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _layered(key, layers, default):
    """The value of ``key`` from the last layer (dict) that defines it — defaults < stored < patch."""
    v = default
    for layer in layers:
        if isinstance(layer, dict) and key in layer:
            v = layer[key]
    return v


def _merge_bool_group(defaults: dict, layers, group: str) -> dict:
    subs = [layer[group] for layer in layers
            if isinstance(layer, dict) and isinstance(layer.get(group), dict)]
    return {k: bool(_layered(k, subs, dv)) for k, dv in defaults.items()}


def normalize_settings(stored: "dict | None", patch: "dict | None" = None) -> dict:
    """A complete, type-safe preferences object = server defaults, overlaid with the user's stored
    preferences, overlaid with an optional incoming ``patch``. Unknown keys are dropped and every
    value is coerced / clamped to the contract, so a partial update from any client (web, iOS,
    Android, extension, RSS) is safe and the response shape is always stable. ``patch=None`` reads
    (with honest defaults for anything unset); a patch merges an update. Normalisation only — the
    behavioural mapping of the two recommendation sliders lives in
    :func:`api_server.rec_params_from_settings`, and nothing here ever shapes the health report."""
    layers = [DEFAULT_SETTINGS, stored or {}, patch or {}]
    theme = _layered("theme", layers, DEFAULT_SETTINGS["theme"])
    return {
        "theme": theme if theme in _SETTINGS_THEMES else DEFAULT_SETTINGS["theme"],
        "language": (lambda v: v if v in _SETTINGS_LANGUAGES else "en")(
            str(_layered("language", layers, "en")).strip().lower()),
        "politicalOpenness": _clamp_int(_layered("politicalOpenness", layers, 50), 0, 100, 50),
        "recommendationStrength": _clamp_int(_layered("recommendationStrength", layers, 50), 0, 100, 50),
        "readingGoalMinutes": _clamp_int(_layered("readingGoalMinutes", layers, 20), 0, 600, 20),
        "weeklyReport": bool(_layered("weeklyReport", layers, True)),
        "monthlyReport": bool(_layered("monthlyReport", layers, False)),
        "notifications": _merge_bool_group(DEFAULT_SETTINGS["notifications"], layers, "notifications"),
        # The output is built ONLY from the keys above, so any layer key outside this set — an
        # unknown field, or the removed ``privacy`` group — is simply never read (dropped).
    }


# --------------------------------------------------------------------------- #
# Store-backed helpers. ``store`` is the engine's :class:`store.Store` (or any object exposing
# ``get_settings(uid) -> dict | None`` / ``save_settings(uid, dict)``), passed in by the caller so
# this module keeps zero import-time dependency on the storage layer. These are the reader/writer
# convenience layer the rest of the app is expected to route through; they add no behaviour over
# :func:`normalize_settings`, they just remove the repeated ``normalize_settings(get_settings(uid))``.
# --------------------------------------------------------------------------- #
def get(store, uid: int) -> dict:
    """The reader's complete, normalised preferences — stored values over honest server defaults."""
    return normalize_settings(store.get_settings(uid))


def update(store, uid: int, patch: "dict | None") -> dict:
    """Merge a (partial) ``patch`` over the reader's stored preferences, normalise to the stable
    contract, persist, and return the full result — the canonical settings-write flow."""
    updated = normalize_settings(store.get_settings(uid), patch)
    store.save_settings(uid, updated)
    return updated


def reading_goal_minutes(store, uid: int) -> int:
    """The reader's daily reading goal in minutes (server default where unset)."""
    return get(store, uid)["readingGoalMinutes"]


def theme(store, uid: int) -> str:
    """The reader's interface theme — ``light`` / ``dark`` / ``system``."""
    return get(store, uid)["theme"]


def language(store, uid: int) -> str:
    """The reader's interface language code (allowlisted; English where unset/unknown)."""
    return get(store, uid)["language"]
