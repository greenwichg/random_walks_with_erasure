"""licence.py — the class one acquisition channel establishes, and what a plan may see."""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import licence  # noqa: E402


def test_channels_map_to_the_documented_classes():
    assert licence.class_for_channel("rss") == "metadata_public"
    assert licence.class_for_channel("crawl") == "metadata_public"
    assert licence.class_for_channel("gdelt") == "metadata_public"
    for ch in ("newsapi", "guardian", "newsdata", "gnews", "mediastack", "currents", "googlenews"):
        assert licence.class_for_channel(ch) == "provider_restricted", ch
    assert licence.class_for_channel("extension") == "reader_private"
    assert licence.class_for_channel(None) == "unknown"
    assert licence.class_for_channel("") == "unknown"
    assert licence.class_for_channel("something-new") == "unknown"
    assert licence.class_for_channel(" RSS ") == "metadata_public"


def test_env_overrides_move_a_channel_between_sets(monkeypatch):
    monkeypatch.setenv("RWE_LICENCE_PUBLIC_CHANNELS", "guardian")
    assert licence.class_for_channel("guardian") == "metadata_public"
    monkeypatch.setenv("RWE_LICENCE_PROVIDER_CHANNELS", "partnerfeed")
    assert licence.class_for_channel("partnerfeed") == "provider_restricted"


def test_a_set_of_observations_takes_the_most_permissive_class():
    assert licence.class_for_channels(["newsapi", "rss"]) == "metadata_public"
    assert licence.class_for_channels(["newsapi", "gnews"]) == "provider_restricted"
    assert licence.class_for_channels(["extension"]) == "reader_private"
    assert licence.class_for_channels(["extension", "rss"]) == "metadata_public"
    assert licence.class_for_channels([]) == "unknown"


def test_attribution_is_owed_per_channel():
    assert licence.attribution_for(["rss"]) == []
    assert licence.attribution_for(["gdelt", "gdelt", "rss"]) == ["GDELT Project (gdeltproject.org)"]


def test_servable_and_withheld_fields():
    allowed = {"metadata_public"}
    assert licence.servable("metadata_public", allowed)
    assert not licence.servable("provider_restricted", allowed)
    assert not licence.servable("reader_private", {"reader_private", "metadata_public"})   # never
    assert licence.withheld_fields("metadata_public", allowed) == ()
    assert "headline" in licence.withheld_fields("provider_restricted", allowed)
    assert "url" in licence.withheld_fields("unknown", allowed)
    assert licence.withheld_fields("provider_restricted", {"provider_restricted"}) == ()
