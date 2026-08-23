"""Python mirror of web/scripts/check-i18n.mjs — guards the message catalogs from the backend/CI
side so a broken translation set fails pytest too (not only the Node build).

Checks: key parity across the five languages, no empty values, `{placeholder}` parity per key, and
that every resolver (type, variant) has an explanation template (mirrors explanationKey())."""
import json
import pathlib
import re

import pytest

MESSAGES = pathlib.Path(__file__).resolve().parent.parent / "packages" / "core" / "i18n" / "messages"
LANGS = ("en", "es", "fr", "de", "pt")

# Mirrors explanationKey() in packages/core/i18n/core.ts — every supported (type, variant) needs a template.
REQUIRED_EXPLANATION_KEYS = (
    "explanation.story_match.same_event",
    "explanation.story_match.follow_up",
    "explanation.story_match.following",
    "explanation.topic_continuity.perspective",
    "explanation.topic_continuity.outlet",
    "explanation.new_publisher.never",
    "explanation.new_publisher.rarely",
    "explanation.bridge",
    "explanation.long_tail",
    "explanation.coverage_breadth.topic",
    "explanation.coverage_breadth.generic",
)

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _load(lang):
    return json.loads((MESSAGES / f"{lang}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def catalogs():
    return {lang: _load(lang) for lang in LANGS}


def test_all_catalog_files_exist():
    for lang in LANGS:
        assert (MESSAGES / f"{lang}.json").is_file(), f"missing catalog: {lang}.json"


def test_key_parity(catalogs):
    en_keys = set(catalogs["en"])
    assert len(en_keys) > 100, "en catalog should be substantial"
    for lang in LANGS:
        assert set(catalogs[lang]) == en_keys, f"{lang}.json key set differs from en.json"


def test_no_empty_values(catalogs):
    for lang in LANGS:
        for key, val in catalogs[lang].items():
            assert isinstance(val, str) and val.strip(), f"{lang}.json empty value for {key!r}"


def test_placeholder_parity(catalogs):
    for key in catalogs["en"]:
        base = set(_PLACEHOLDER.findall(catalogs["en"][key]))
        for lang in LANGS:
            got = set(_PLACEHOLDER.findall(catalogs[lang][key]))
            assert got == base, f"{lang}.json {key!r} placeholders {got} != en {base}"


def test_explanation_templates_present(catalogs):
    en = catalogs["en"]
    for key in REQUIRED_EXPLANATION_KEYS:
        assert key in en, f"missing required explanation template: {key}"
