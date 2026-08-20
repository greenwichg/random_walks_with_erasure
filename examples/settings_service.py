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

What deliberately lives **elsewhere**: the mapping of the recommendation preferences
(``politicalOpenness`` / ``recommendationStrength`` / the ``interests`` sliders) to per-request
recommender parameters is ``api_server.rec_params_from_settings`` — that is the only settings code
that depends on recommender vocabulary, so keeping it out of this leaf keeps the leaf free of RWE
concepts.
"""

from __future__ import annotations


# --------------------------------------------------------------------------- #
# Schema — defaults + allowlists. This is the contract every client sees.
# --------------------------------------------------------------------------- #
#: Interest Intensity — the eight per-interest sliders, 1–10 with 5 = neutral. The keys are the
#: eight NON-political interest areas of the closed product taxonomy (``ingest.TAXONOMY``);
#: ``artsCulture`` is one slider spanning the taxonomy's adjacent Arts + Culture topics. Politics
#: deliberately has NO intensity slider: the feed's political composition is the
#: ``politicalOpenness`` control's contract (the rwe-b slice admits political items only, W1), and
#: an interest knob on the same axis would fight it. Opinion is a register lens, not a subject;
#: the two geographic desks (World / U.S.) belong to the Places settings. The slider→topic mapping
#: is recommender vocabulary and lives in ``api_server._INTEREST_TOPICS`` — the same split this
#: module already makes for the two recommendation sliders.
INTEREST_KEYS = ("business", "technology", "science", "health", "climate",
                 "sports", "entertainment", "artsCulture")
INTEREST_MIN, INTEREST_MAX, INTEREST_DEFAULT = 1, 10, 5

DEFAULT_SETTINGS = {
    "theme": "system",
    "language": "en",
    "politicalOpenness": 50,          # 50 = the stack's default RWE-B epsilon (0.9)
    "recommendationStrength": 50,     # 50 = the stack's default RWE-D beta (0.5)
    # Interest Intensity (see INTEREST_KEYS above): 5 everywhere = the untouched feed, byte for
    # byte — the same "an unmoved slider changes nothing" rule the two sliders above follow.
    "interests": {k: INTEREST_DEFAULT for k in INTEREST_KEYS},
    "readingGoalMinutes": 20,
    "weeklyReport": True,
    "monthlyReport": False,
    "notifications": {"recommendations": True, "weeklyDigest": True,
                      "streakReminders": False, "blindSpotAlerts": False,
                      # The forward-looking shape: a CATEGORY (what the notification is about) crossed
                      # with a CHANNEL (how it reaches the reader). The four flat booleans above are
                      # per-KIND toggles and do not compose — a fifth kind means a fifth checkbox, and
                      # they have no way to say "in the app but not on my lock screen", which is the
                      # normal preference once push exists.
                      #
                      # BOTH shapes are live and they govern different kinds. The flat toggles remain
                      # authoritative for the six kinds that already ship; ``categories`` gates new
                      # ones, starting with breaking stories. Pointing the existing kinds at a category
                      # is a behaviour change for every current reader and needs its own migration
                      # decision — deliberately not made here.
                      #
                      # ``push`` ships now and is READ BY NOTHING. That is the point: the nesting has to
                      # exist before stored blobs contain it, or adding the channel dimension later
                      # means migrating everyone's settings. Costing nothing now buys that.
                      "categories": {
                          "breaking":        {"inApp": True, "push": False},
                          # `email` joins the channel row for digests only. Default OFF, and that
                          # is consent, not caution: a channel nobody opted into is not permission,
                          # and defaulting it on would mail every existing reader on deploy day.
                          "digests":         {"inApp": True, "push": False, "email": False},
                          "recommendations": {"inApp": True, "push": False},
                          "product":         {"inApp": True, "push": False},
                      }},
    # Location Intelligence Phase 1 — prepared, not yet surfaced in any UI. ``edition`` is the
    # reader's default place scope (ISO 3166-1 alpha-2 country, or None = global); ``locations``
    # is the followed-places list, each ``{"placeId": str, "level": country|region|city}``.
    # Future capabilities (GPS, radius, travel mode) extend the entry shape additively — the list
    # container never needs redesign.
    "edition": None,
    "locations": [],
    # For You country preference (ISO 3166-1 alpha-2, or None = Global). DELIBERATELY separate
    # from ``edition`` above: ``edition`` is the reader's place scope for Local Pulse, and
    # repointing it at the recommender would silently re-rank the feed of every reader who has
    # ever set an edition — a behaviour change for existing readers, which is its own decision
    # (the same reasoning the notification categories above record). None = the untouched feed,
    # byte for byte: the "an unmoved control changes nothing" rule the sliders already follow.
    "recommendationCountry": None,
    # The reader's IANA time zone, e.g. "Asia/Kolkata". AUTO-DETECTED, not a preference: the web
    # client reports `Intl.DateTimeFormat().resolvedOptions().timeZone` when it records a read, and
    # no settings screen exposes it. It exists because a *day* is a local idea — a Delhi reader's
    # 02:00 read belongs to that reader's day, not to the UTC day that ended two hours earlier —
    # and streaks are counted in days. `None` means "not reported", which buckets by UTC: exactly
    # the old behaviour, so a client that never sends one is unaffected.
    "timeZone": None,
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
    return {k: bool(_layered(k, subs, dv)) for k, dv in defaults.items()
            if not isinstance(dv, dict)}    # nested sub-groups are merged by their own helper


def _merge_notification_categories(layers) -> dict:
    """The ``notifications.categories`` matrix — one nested level deeper than
    :func:`_merge_bool_group`, which coerces with ``bool()`` and would flatten a dict to ``True``.

    Layering is per LEAF, not per category: a patch of ``{"breaking": {"push": True}}`` must leave
    ``breaking.inApp`` alone, so each channel resolves through its own ``_layered`` call. Built only
    from the defaults, so an unknown category or an unknown channel is dropped exactly like any other
    unknown key — the same fail-safe the rest of this module relies on."""
    defaults = DEFAULT_SETTINGS["notifications"]["categories"]
    groups = [layer["notifications"]["categories"] for layer in layers
              if isinstance(layer, dict) and isinstance(layer.get("notifications"), dict)
              and isinstance(layer["notifications"].get("categories"), dict)]
    out = {}
    for category, channels in defaults.items():
        subs = [g[category] for g in groups if isinstance(g.get(category), dict)]
        out[category] = {ch: bool(_layered(ch, subs, dv)) for ch, dv in channels.items()}
    return out


def _merge_notifications(layers) -> dict:
    """The whole ``notifications`` group: the flat per-kind toggles plus the nested category matrix."""
    merged = _merge_bool_group(DEFAULT_SETTINGS["notifications"], layers, "notifications")
    merged["categories"] = _merge_notification_categories(layers)
    return merged


def _merge_interests(layers) -> dict:
    """The ``interests`` map — per-LEAF layering exactly like the notification matrix, so a patch
    of one interest ("sports": 9) leaves the other seven alone. Built only from the default keys,
    so an unknown interest is dropped like any other unknown key; every value is clamped to the
    1–10 scale with junk falling back to the neutral 5 (never to an extreme). A stored blob from
    before the group existed simply gains the all-neutral defaults — no migration."""
    defaults = DEFAULT_SETTINGS["interests"]
    subs = [layer["interests"] for layer in layers
            if isinstance(layer, dict) and isinstance(layer.get("interests"), dict)]
    return {k: _clamp_int(_layered(k, subs, dv), INTEREST_MIN, INTEREST_MAX, dv)
            for k, dv in defaults.items()}


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
        "interests": _merge_interests(layers),
        "readingGoalMinutes": _clamp_int(_layered("readingGoalMinutes", layers, 20), 0, 600, 20),
        "weeklyReport": bool(_layered("weeklyReport", layers, True)),
        "monthlyReport": bool(_layered("monthlyReport", layers, False)),
        "notifications": _merge_notifications(layers),
        "edition": _normalize_edition(_layered("edition", layers, None)),
        "locations": _normalize_locations(_layered("locations", layers, [])),
        "recommendationCountry": _normalize_edition(
            _layered("recommendationCountry", layers, None)),
        "timeZone": _normalize_timezone(_layered("timeZone", layers, None)),
        # The output is built ONLY from the keys above, so any layer key outside this set — an
        # unknown field, or the removed ``privacy`` group — is simply never read (dropped).
    }


_LOCATION_LEVELS = ("country", "region", "city")
_MAX_LOCATIONS = 10


def _normalize_timezone(value) -> "str | None":
    """A resolvable IANA zone name, or ``None``.

    Validated by actually constructing the zone rather than by pattern: "Asia/Kolkata" and "UTC"
    both pass, "Mars/Olympus" and "UTC+5:30" do not, and neither does a name the running system's
    tz database has never heard of — which is the case that matters, since a name we cannot resolve
    at read time is a name we cannot bucket a day with either. Storing only resolvable zones means
    every consumer can assume the stored value works.

    An unresolvable value degrades to ``None`` (UTC bucketing), never to an exception: a client
    sending nonsense must not be able to break a reader's settings read."""
    if value is None:
        return None
    name = str(value).strip()
    if not name or len(name) > 64:
        return None
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
    except Exception:
        return None
    return name


def _normalize_edition(value) -> "str | None":
    """ISO 3166-1 alpha-2 (upper) or None — anything else falls back to None (global).

    Shared by ``edition`` and ``recommendationCountry``: two independent preferences that happen
    to carry the same kind of value, so they normalize through one function rather than two that
    could drift on what counts as a valid code."""
    s = str(value).strip().upper() if value is not None else ""
    return s if len(s) == 2 and s.isalpha() else None


def _normalize_locations(value) -> list:
    """The followed-places list, coerced to the contract: at most ``_MAX_LOCATIONS`` entries of
    ``{"placeId": non-empty str, "level": country|region|city}``; malformed entries drop."""
    out = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        place = str(item.get("placeId") or "").strip()
        level = str(item.get("level") or "").strip().lower()
        if place and level in _LOCATION_LEVELS:
            out.append({"placeId": place[:128], "level": level})
        if len(out) >= _MAX_LOCATIONS:
            break
    return out


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
