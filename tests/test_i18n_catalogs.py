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
_PLURAL_HEAD = re.compile(r"^\{\s*(\w+)\s*,\s*plural\s*,")
_BRANCH_LABEL = re.compile(r"(=\d+|zero|one|two|few|many|other)\s*\{")


def _close_brace(s, start):
    """Index of the ``}`` closing a ``{`` that opened just before ``start``; -1 if unbalanced."""
    depth = 1
    for j in range(start, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return -1


def _branches(body):
    """``label {text}`` branches of a plural body, honouring braces inside a branch."""
    out, pos = [], 0
    while True:
        m = _BRANCH_LABEL.search(body, pos)
        if not m:
            return out
        end = _close_brace(body, m.end())
        if end == -1:
            return out              # unbalanced — stop rather than invent a branch
        out.append(body[m.end():end])
        pos = end + 1


def message_args(template):
    """The argument names a message needs — a port of ``messageArgs`` in
    ``packages/core/i18n/message-format.js``, which is the source of truth.

    A NAIVE ``\\{(\\w+)\\}`` SCAN IS WRONG HERE and this file shipped one. Under ICU plural syntax
    the branch bodies are text, so ``{n, plural, one {# story} other {# stories}}`` reports the
    arguments ``story`` and ``stories`` and misses ``n`` entirely — the exact inversion of the
    truth. Across the five catalogs that produced eighteen false parity failures (``are``, ``is``,
    ``einmal``, ``werden``, ``têm`` — all of them branch text) while hiding real ones. The same
    mistake was made twice in the JavaScript checker before it was replaced by one shared parser;
    this is the third copy, so its behaviour is pinned by :func:`test_message_args_reads_plurals`
    below rather than trusted.

    A branch body is a message in its own right, so it is scanned rather than skipped: that is how
    ``one {See # result for "{q}"}`` reports ``q``."""
    found = set()

    def scan(s):
        plain, i = "", 0
        while i < len(s):
            open_ = s.find("{", i)
            if open_ == -1:
                plain += s[i:]
                break
            head = _PLURAL_HEAD.match(s[open_:])
            if not head:
                plain += s[i:open_ + 1]
                i = open_ + 1
                continue
            found.add(head.group(1))
            body_start = open_ + head.end()
            end = _close_brace(s, body_start)
            if end == -1:
                break
            for branch in _branches(s[body_start:end]):
                scan(branch)
            plain += s[i:open_]
            i = end + 1
        found.update(_PLACEHOLDER.findall(plain))

    scan(template)
    return found


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


def test_message_args_reads_plurals(catalogs):
    """Pins the parser this file just re-implemented, on the cases that broke the other two.

    Branch text is text: `other {are}` is not an argument named `are`. And the plural's own
    argument must be reported even though it never appears as a bare `{n}`."""
    assert message_args("{n} of {total}") == {"n", "total"}
    assert message_args("{n, plural, one {# story} other {# stories}}") == {"n"}
    # Branch text is text. This is the real `home.briefing.blindspotHeadline` shape, and the two
    # regexes that came before this parser both read it as arguments named `is` and `are`.
    assert message_args("{n, plural, one {is} other {are}} covered") == {"n"}
    # A branch body is still a message, so a placeholder INSIDE one is a real argument.
    assert message_args('{n, plural, one {See # result for “{q}”} other {See # for “{q}”}}') == {"n", "q"}
    # Two counts in one string — the reason the catalog uses ICU rather than key suffixes at all.
    assert message_args(
        "{s, plural, one {# story} other {# stories}} across "
        "{p, plural, one {# publisher} other {# publishers}}") == {"s", "p"}
    # Unbalanced braces report what was parseable rather than raising: a malformed catalog value
    # should fail the PARITY assertion with a readable diff, not crash the collection.
    assert message_args("{n, plural, one {# story") == {"n"}
    # And it agrees with the real catalog: every en plural message names its own count.
    for key, value in catalogs["en"].items():
        if ", plural," in value:
            assert message_args(value), f"{key!r} parsed to no arguments"


def test_placeholder_parity(catalogs):
    for key in catalogs["en"]:
        base = message_args(catalogs["en"][key])
        for lang in LANGS:
            got = message_args(catalogs[lang][key])
            assert got == base, f"{lang}.json {key!r} placeholders {got} != en {base}"


def test_explanation_templates_present(catalogs):
    en = catalogs["en"]
    for key in REQUIRED_EXPLANATION_KEYS:
        assert key in en, f"missing required explanation template: {key}"
