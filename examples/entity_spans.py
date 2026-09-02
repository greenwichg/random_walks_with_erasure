"""entity_spans.py — stdlib pseudo-entity extraction from a headline and its dek (Stage 0.3).

The entity channel works where it exists and is blind where it does not: X5c's veto fired on 24
of the 461 merges it could see and was silent on the other 93.8%, because only 24% of articles
carry a GDELT-extracted name (``story_service.entity_veto``, measured 2026-08-25). GDELT's GKG
only ever sees the articles GDELT happens to monitor; the rest of the catalog has no entity rows
at all. This module reads a second, deliberately weaker source of names from the text we already
hold — CAPITALISED MULTI-WORD SPANS — so coverage stops being the binding constraint on a channel
whose rules are already measured and adopted.

It is a heuristic, and every downstream rule already treats an entity as a heuristic: a name
counts only when >= 2 members corroborate it (``_story_entity_consensus``), a merge proposal
needs >= 2 shared names mutually anchored by both stories' tops (``_merge_by_entities``), a
name in more than six story consensuses cannot propose anything, and the noise filter drops
platforms, outlets and country names by identity. Those guards were designed for GKG noise; they
are the reason a weaker extractor can be tried at all.

Provenance stays honest: rows are written under their own ``source`` (:data:`SOURCE`) and their
own ``kind`` (:data:`KIND`, neither person nor org, because a span cannot tell which), the
store returns them ONLY when a caller asks for that kind, and the build consumes them only
under ``RWE_STORY_ENTITY_SPANS``. With that off the build is byte-identical whatever the table
holds. Two switches, on purpose: ``RWE_INGEST_ENTITY_SPANS`` decides whether rows are WRITTEN
(ingest + the one-shot backfill), ``RWE_STORY_ENTITY_SPANS`` whether the build READS them — so
the table could fill while the counterfactual was measured against a baseline that did not
consume it. **Both are ON in production since 2026-09-02** (compose defaults; ``0`` kills
either): measured twice on the live catalog, the record is on ``story_service.entity_spans``.

What it does NOT do: named-entity recognition. No model, no gazetteer, no dependency — stdlib
regex over capitalisation, which is why German (every noun capitalised) is skipped by language.
CJK and other caseless scripts yield nothing, which is the honest answer rather than a wrong one.
"""

from __future__ import annotations

import os
import re

SOURCE = "headline-caps"
KIND = "span"
#: Names kept per article — the GKG enricher's cap, for the same reason: a long dek is not more
#: evidence, and the consumers only ever ask whether a name is SHARED.
CAP = 24
MIN_WORDS = 2
MAX_NAME_LEN = 60

#: Languages whose orthography capitalises every noun: capitalisation carries no entity signal
#: there and the extractor would return the sentence's nouns as "names".
NOUN_CAPITALISING = frozenset({"de", "lb"})

#: Lower-case words a name may run THROUGH ("Bank of England", "Museo del Prado", "Van der
#: Sar"), included only when another capitalised word follows. ``and``/``&`` are deliberately
#: absent: "Trump and Putin" is two names, not one.
_CONNECTORS = frozenset("of the de del della delle di da do dos das du des la le les van von "
                        "der den al bin ibn for".split())

#: Sentence-initial words that are capitalised by POSITION and would otherwise lead a span
#: ("Why Donald Trump…", "Breaking: …"). Also weekday/month words, which lead datelines.
#: ``new`` is deliberately NOT here: it begins real names (New York Times, New Delhi) far more
#: often than it decorates one, and a stray "new senate report" costs nothing downstream — a
#: name counts only when two members carry it, and two outlets writing "New Senate report" are
#: writing about the same report.
_LEADS = frozenset("""
a an the this that these those why how what when where who whom which whose after before as
at by for from in into on to with without is are was were be been has have had will would
could should can may might must do does did not no says said say breaking live watch
video photos photo opinion analysis exclusive report update updated review preview explained
explainer inside meet here there now then also but and or so if while amid over under
monday tuesday wednesday thursday friday saturday sunday january february march april may june
july august september october november december jan feb mar apr jun jul aug sep sept oct nov
dec today tonight yesterday tomorrow
""".split())

#: Words a Title Case style guide leaves lower-case (articles, conjunctions, prepositions, the
#: copula): they say nothing about whether a headline is title-cased, so the detector ignores
#: them and asks only whether every CONTENT word is capitalised.
_FUNCTION_WORDS = _CONNECTORS | frozenset("""
a an and or but nor as at by in on to up vs via with from into onto over under after before about
above across against along among around because behind below beneath beside between beyond during
except inside outside since through toward towards until upon versus within without while where
when than that this these those is are was were be been has have had will would could should can
may might must not no amid near past plus per off out its it his her their our your my
""".split())

#: Whole spans that are page furniture, never a referent.
_NOISE_SPANS = frozenset({
    "breaking news", "live updates", "live blog", "read more", "photo gallery", "editor's note",
    "editors note", "what we know", "top stories", "news brief", "in brief", "morning briefing",
    "evening briefing", "daily briefing", "latest news", "full story", "full coverage",
    "key takeaways", "opinion editorial", "letters editor", "associated press",
})

_WORD_RE = re.compile(r"[^\W\d_][\w'’.\-]*", re.UNICODE)
#: Segment boundaries: sentence ends, headline separators (" - ", " | ", ": "), and — measured
#: on the first production backfill — COMMAS and semicolons. Without them a cast list is one
#: "name" ("Julia Stiles, Jenna Dewan, Harry Shum Jr" came back as a single 26-character span),
#: which can never corroborate and only pads the count. A comma ends a run; "Washington, D.C."
#: loses its span to that rule and the trade is taken knowingly. Each segment's first word is
#: position-capitalised.
_SEGMENT_RE = re.compile(r"(?:[.!?]+\s+|[:;,]\s*|\s+[-–—|]\s+|\s*\|\s*)")

#: Words trimmed from a name's ENDS after the run is formed: calendar words ("Tuesday Sept 2"
#: yielded the pseudo-name "tuesday sept" on the first backfill — and a dateline is exactly the
#: kind of string that corroborates ACROSS unrelated stories, which is the one failure the
#: consumers cannot absorb) and the series/format words a headline capitalises beside a real
#: name ("‘Dancing With the Stars’ Season 35" is about the show, not about "the stars season").
_TRIM_WORDS = frozenset("""
monday tuesday wednesday thursday friday saturday sunday january february march april may june
july august september october november december jan feb mar apr jun jul aug sep sept oct nov
dec today tonight yesterday tomorrow season episode live update updates review recap preview
report exclusive video photos photo watch breaking
""".split())
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")
_POSSESSIVE_RE = re.compile(r"['’]s$")


def enabled() -> bool:
    """Whether INGEST writes span rows (``RWE_INGEST_ENTITY_SPANS``). The library fallback is
    off and junk is off; the deploy compose defaults it ON (adopted 2026-09-02). Reading them
    is ``story_service.entity_spans``, a separate switch."""
    return os.environ.get("RWE_INGEST_ENTITY_SPANS", "").strip().lower() in {"1", "true", "yes", "on"}


def _is_cap(word: str) -> bool:
    return word[:1].isupper()


def _title_cased(words: list) -> bool:
    """A headline in Title Case capitalises EVERY content word, so capitalisation says nothing
    about names there. Three or more content words (function words ignored) and not one of them
    lower-case. All-or-nothing on purpose: a share-based test ("three-quarters capitalised")
    misread every name-dense sentence-case headline — "Donald Trump meets Vladimir Putin in
    Helsinki" is five capitalised content words out of six — as Title Case and returned nothing
    for exactly the headlines with the most to say. One lower-case verb is the tell."""
    content = [w for w in words if len(w) >= 3 and w.lower() not in _FUNCTION_WORDS]
    if len(content) < 3:
        return False
    return all(_is_cap(w) for w in content)


def _normalise(words: list) -> str:
    parts = []
    for w in words:
        w = _POSSESSIVE_RE.sub("", w.lower()).strip(".-'’")
        if w:
            parts.append(w)
    # Connectors and calendar/format words cannot BEGIN or END a name; trimmed until a real
    # word holds each end, so "tuesday sept" empties out and "dancing with the stars season"
    # keeps the show.
    while parts and (parts[0] in _CONNECTORS or parts[0] in _TRIM_WORDS):
        parts.pop(0)
    while parts and (parts[-1] in _CONNECTORS or parts[-1] in _TRIM_WORDS):
        parts.pop()
    return " ".join(parts)


def _spans_in(segment: str, *, drop_lead: bool) -> list:
    """Capitalised runs in one segment, connectors allowed inside a run."""
    words = _WORD_RE.findall(segment)
    out: list = []
    run: list = []
    caps_in_run = 0

    def flush() -> None:
        nonlocal run, caps_in_run
        if caps_in_run >= MIN_WORDS:
            out.append(list(run))
        run, caps_in_run = [], 0

    for idx, w in enumerate(words):
        low = w.lower()
        if _is_cap(w):
            if idx == 0 and drop_lead and low in _LEADS:
                continue                                  # capitalised by position, not identity
            run.append(w)
            caps_in_run += 1
        elif run and low in _CONNECTORS and idx + 1 < len(words) and _is_cap(words[idx + 1]):
            run.append(w)                                 # "Bank OF England"
        else:
            flush()
    flush()
    return out


def extract(title: str, description: str = "", *, language: "str | None" = None) -> list:
    """Normalised pseudo-entity names from a headline and the first two sentences of its dek —
    lower-cased, whitespace-collapsed, possessives stripped, a leading article dropped, deduped
    in order of appearance and capped at :data:`CAP`. ``[]`` for caseless scripts, for the
    noun-capitalising languages, and for a Title Case headline with no dek."""
    if (language or "").strip().lower()[:2] in NOUN_CAPITALISING:
        return []
    seen: list = []

    def take(spans: list) -> None:
        for span in spans:
            name = _normalise(span)
            if name.startswith("the "):
                name = name[4:]
            if (len(name.split()) < MIN_WORDS or len(name) < 3 or len(name) > MAX_NAME_LEN
                    or name in _NOISE_SPANS or name in seen):
                continue
            seen.append(name)
            if len(seen) >= CAP:
                return

    head = (title or "").strip()
    if head and not _title_cased(_WORD_RE.findall(head)):
        for segment in _SEGMENT_RE.split(head):
            if segment.strip():
                take(_spans_in(segment, drop_lead=True))
    dek = (description or "").strip()
    if dek and len(seen) < CAP:
        for sentence in _SENTENCE_END_RE.split(dek)[:2]:
            for segment in _SEGMENT_RE.split(sentence):
                if segment.strip():
                    take(_spans_in(segment, drop_lead=True))
    return seen[:CAP]
