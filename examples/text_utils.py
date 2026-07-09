"""Canonical article-text normalization — the one HTML→readable-text helper.

Every ingestion adapter (RSS/Atom, NewsAPI, GDELT, and any future source) produces a
:class:`rss_ingest.FeedEntry`, and FeedEntry normalizes its ``title`` / ``description`` /
``body`` through :func:`clean_html` at construction (``FeedEntry.__post_init__``). So
FeedEntry is the **canonical normalized contract**: every downstream component — scoring,
dedup, persistence, media, Discover, Search, Stories, Recommendations, the AI coach — can
assume clean, tag-free, entity-decoded text without re-sanitizing. There is exactly one
sanitizer (this function); nobody sanitizes separately.

Design:
- stdlib only (``html.parser`` + ``html.unescape``) — no third-party dependency, matching the
  ingestion layer's dependency-light ethos.
- lenient with malformed HTML, idempotent on already-clean text, safe on ``None`` / empty.
- **never emits HTML**: ``<script>`` / ``<style>`` content is dropped, ``<img>`` is ignored
  (no alt text injected), block elements and ``<br>`` become readable line breaks, and HTML
  entities (``&amp;`` ``&#39;`` ``&nbsp;`` …) are decoded. Punctuation is preserved.
"""

from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser

# Block-level elements: their boundary reads better as a line break than as run-together text.
_BLOCK = frozenset({
    "address", "article", "aside", "blockquote", "dd", "div", "dl", "dt", "fieldset",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "li", "main", "nav", "ol", "p", "pre", "section", "table", "tbody", "td", "tfoot", "th",
    "thead", "tr", "ul",
})
# Close tags that read as a single newline (list items / table cells & rows), not a blank line.
_SINGLE_NL = frozenset({"li", "dd", "dt", "td", "th", "tr"})
# Tags whose entire text content is never article prose — dropped wholesale.
_DROP_CONTENT = frozenset({"script", "style", "noscript", "template", "head", "svg", "title"})
# Void tags that read as a line break.
_BREAK = frozenset({"br", "hr"})


class _ToText(HTMLParser):
    """Collect readable text from HTML: strip tags, drop script/style, ignore images, and turn
    block boundaries / ``<br>`` into newlines. ``convert_charrefs=True`` decodes entities into the
    data stream. Source-formatting whitespace inside a text node is insignificant (HTML
    semantics), so it is collapsed here — only tag boundaries create real line breaks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._drop = 0                       # >0 while inside a script/style/… subtree

    def handle_data(self, data: str) -> None:
        if self._drop == 0 and data:
            self._out.append(re.sub(r"\s+", " ", data))

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _DROP_CONTENT:
            self._drop += 1
        elif tag in _BREAK:
            self._out.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:   # <br/>, <img/>, …
        if tag in _BREAK:
            self._out.append("\n")
        # void tags such as <img/> contribute no text

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_CONTENT:
            self._drop = max(0, self._drop - 1)
        elif tag in _SINGLE_NL:
            self._out.append("\n")
        elif tag in _BLOCK:
            self._out.append("\n\n")

    def get_text(self) -> str:
        return "".join(self._out)


def _parse(text: str) -> str:
    p = _ToText()
    try:
        p.feed(text)
        p.close()
        return p.get_text()
    except Exception:                        # never let a pathological input break ingestion
        return re.sub(r"<[^>]+>", " ", text)


def _collapse_ws(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[^\S\n]+", " ", s)          # runs of non-newline whitespace (incl. NBSP) → one space
    s = re.sub(r" *\n *", "\n", s)           # drop spaces hugging a newline
    s = re.sub(r"\n{3,}", "\n\n", s)         # cap blank runs at a single blank line
    return s.strip()


def clean_html(raw: "str | None") -> str:
    """Normalize source article text to readable plain text (see the module docstring).

    Strips tags, decodes HTML entities, drops ``<script>`` / ``<style>``, ignores ``<img>``,
    turns block elements / ``<br>`` into line breaks, collapses redundant whitespace, and
    preserves punctuation. ``None`` / empty → ``""``. Idempotent on already-clean text.
    """
    if not raw:
        return ""
    # Decode entities first so entity-escaped markup ("&lt;p&gt;", or "&lt;b&gt;" nested inside real
    # tags) becomes real tags the parser then strips. A single level of unescaping (double-encoding is
    # intentionally left as-is). Any literal '&' this reveals (from "&amp;") is preserved by the parser.
    text = _html.unescape(raw)
    if "<" in text:
        text = _parse(text)
    return _collapse_ws(text)
