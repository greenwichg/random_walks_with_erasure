"""clustering.py — the deterministic, dependency-free clustering primitive.

A reusable union-find grouping over items by token (Jaccard) similarity within a time window. No LLM,
no external dependency, fully deterministic (same input → same groups, same order). It groups **item
indices** and knows nothing about Stories or FeedArticle — story construction lives in
``story_service.py``; this module only decides *what clusters with what*.

Extracted from the original Discover implementation so both Discover and Stories share one algorithm.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from bisect import bisect_right
from collections import Counter
from typing import Callable, Optional, Sequence

DEFAULT_SIM = 0.28
DEFAULT_WINDOW_DAYS = 6.0

#: Fraction of a candidate merge's CROSS-PAIRS that must independently pass the similarity gate
#: before two clusters are joined. ``0.0`` = pure single linkage: A~B and B~C merges A, B and C even
#: when A and C share nothing. That transitive closure is what built the production mega-cluster,
#: and it is the measured production baseline — so the default stays 0.0 until a candidate value is
#: measured against the live catalog by ``examples/audit_clustering_change.py``.
DEFAULT_LINK_QUORUM = 0.0

#: Cross-pair scoring is O(|A|x|B|), so each side is sampled to at most this many members (lowest
#: indices first — deterministic). A quorum measured on 32x32 is a sample, not a census; that is a
#: deliberate cost bound, and it means the test gets *approximate* on very large clusters. Those are
#: exactly the clusters it is meant to stop forming.
LINK_SAMPLE = 32

#: Distinct members EACH SIDE must contribute to the passing cross-pairs before two clusters join —
#: the merge's **support breadth**. ``1`` (the default) is off and byte-identical to the linkage
#: rules above; ``2`` is the smallest value that means anything, and it says the thing
#: ``GEO_MIN_CONSENSUS`` says about geography: one witness is an anecdote, two is corroboration.
#:
#: This is a DIFFERENT question from ``link_quorum``, and the difference is the whole point.
#: The quorum asks *what fraction of cross-pairs pass*; breadth asks *how many distinct articles
#: those passing pairs involve*. A comparative round-up — "'Spider-Man' tops box office in fourth
#: weekend; 'The Odyssey' becomes Nolan's highest-grossing film" — is genuinely similar to BOTH of
#: two unrelated stories, so it passes the pairwise gate on each side honestly, and no vocabulary
#: rule can or should kill those edges. But every cross-pair supporting the resulting merge runs
#: through that ONE article. Breadth 1 is the signature of a bridge weld, whatever the domain, and
#: it is invisible to a fraction.
#:
#: It also explains why raising the quorum could not do this job. On a 60-article story most
#: cross-pairs legitimately fail (coverage diverges as a story runs), so the passing FRACTION is
#: low exactly where the cluster is largest — 0.3 and 0.4 were measured and rejected for that.
#: Breadth does not degrade with size: a genuine story has many distinct members participating
#: even when the fraction is small. The two rules are ANDed and neither subsumes the other.
DEFAULT_MIN_SUPPORT = 1

#: Minimum DISTINCTIVE tokens two headlines must share before similarity is even considered.
#: The ratio alone cannot tell evidence from coincidence — measured on real merges:
#:   "Berlin pride event canceled…" vs "Vehicle drives into crowd at Berlin pride event"
#:        jaccard 0.86, 6 shared tokens  -> the same event
#:   "Trump wins Ohio" vs "Trump wins Iowa"
#:        jaccard 0.50, 2 shared tokens  -> DIFFERENT events, and no stop-list can fix it
#: Shared-token COUNT separates those two; the ratio does not.
MIN_SHARED_TOKENS = 3

#: A headline with fewer content tokens than this does not cluster at all. Below ~3 words the
#: Jaccard of a tiny set is dominated by whichever few words survive, so it measures little.
MIN_TITLE_TOKENS = 3

# Small English stop-list for title-similarity — enough to keep function words from inflating overlap
# without pulling in a stemming dependency.
_STOPWORDS = frozenset("""
a an and the of to in on for with from by at as is are was were be been being this that these those
it its his her their our your my we you they he she who what when where why how than then so but or
not no nor into over under after before amid amto about says say said new latest live update updates
""".split())

# Calendar and editorial filler. These are what made recurring columns and daily round-ups collapse
# into single clusters: "Local news in brief, July 21" and "…July 22" reduced to the SAME four
# tokens {brief, july, local, news} — jaccard 1.00 on nothing but boilerplate. Measured in
# production, that merged 65 articles from 42 publishers into one "story".
_STOPWORDS = _STOPWORDS | frozenset("""
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec
monday tuesday wednesday thursday friday saturday sunday
news brief briefs briefing roundup round wrap recap digest bulletin headlines
today yesterday tomorrow week weekly daily morning evening tonight edition
best top since things
""".split())


#: Codepoint ranges with no word separator, so a word regex returns a whole clause as one token: CJK
#: ideographs and their extensions, Hiragana, Katakana, and Thai. For these, :func:`title_tokens`
#: emits character BIGRAMS — the standard information-retrieval treatment for unsegmented scripts,
#: and the only option that does not add a segmenter dependency.
#:
#: **Hangul is deliberately NOT here.** Korean uses spaces between words, so it segments like any
#: other script; bigramming it would replace 4 real words with 11 fragments and make Korean match
#: Korean on syllable coincidence. Grouping "non-Latin" into one bucket is the error this list
#: exists to avoid — the question is whether a script has word separators, not what it looks like.
_UNSEGMENTED = (
    (0x3040, 0x30FF),    # Hiragana + Katakana
    (0x3400, 0x4DBF),    # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs
    (0x0E00, 0x0E7F),    # Thai
    (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
)


#: Combining-mark ranges that ``\\w`` does **not** match, because Python classifies categories ``Mn``
#: and ``Mc`` as non-word characters. For an abugida that is fatal: Tamil ``அதிபர்`` contains U+0BBF
#: (Mc) and U+0BCD (Mn), so ``\\w+`` returns the fragments ``அத`` and ``பர`` — both two characters,
#: both then dropped by the length floor. Measured: ``\\w+`` alone leaves Tamil and Hindi at zero
#: usable tokens, which would have made this candidate look like it fixed "non-Latin scripts" while
#: leaving two of the largest ones exactly as broken as before.
_MARKS = (
    "̀-ͯ"      # combining diacriticals (Latin, Greek)
    "҃-҉"      # Cyrillic
    "֑-ׇֽֿׁׂׅׄ"    # Hebrew points
    "ؐ-ًؚ-ٰٟۖ-ۜ"        # Arabic
    "ऀ-ःऺ-ॏ॑-ॗॢॣ"  # Devanagari
    "ঁ-ঃ়-্"                           # Bengali
    "ஂா-்ௗ"                            # Tamil
    "ఀ-ఄా-ౖ"                           # Telugu
    "ัิ-ฺ็-๎"                     # Thai
    "‌‍"       # ZWNJ / ZWJ — orthographic in Devanagari and Persian, not separators
)

#: One word: a ``\\w`` run that may carry combining marks anywhere inside it.
_WORD_RE = re.compile(r"(?:\w|[" + _MARKS + r"])+", re.UNICODE)


def _unsegmented(ch: str) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in _UNSEGMENTED)


def _keep(toks, *, wide: bool) -> set:
    """Content tokens: length > 2, not a bare number, not a stop-word.

    ``wide`` exempts unsegmented runs from the length floor. A 2-character bigram **is** the unit
    for Chinese, Japanese and Thai, and holding those to a 3-character floor would re-create the
    zero-token defect one layer down — the gate would simply move from `title_tokens` to this
    filter and the symptom would be identical."""
    return set(t for t in toks
               if (len(t) > 2 or (wide and _unsegmented(t[0])))
               and not t.isdigit() and t not in _STOPWORDS)


def _script_tokens(lower: str) -> list:
    """``\\w+`` words, with unsegmented runs replaced by their character bigrams.

    Two different problems, handled in one pass because they occur in one string — a Japanese
    headline routinely carries Latin brand names, and a Korean one carries numerals.

    * **Space-separated non-ASCII** (Cyrillic, Arabic, Greek, Hebrew, Devanagari, Tamil, Hangul
      words, and every accented Latin alphabet) needs only a wider character class. ``[a-z0-9]+``
      splits ``kündigt`` into ``k``/``ndigt`` and yields **nothing at all** for a script with no
      ASCII letters.
    * **Unsegmented** scripts have no word boundary to find, so ``\\w+`` returns the whole clause as
      a single token — which passes the length filter and then matches only a byte-identical
      headline. Bigrams give them a token set with the granularity the Jaccard rule assumes.
    """
    out = []
    for word in _WORD_RE.findall(lower):
        for run in _same_script_runs(word):
            if _unsegmented(run[0]):
                # Bigrams. A one-character run has none, so it is kept whole rather than dropped —
                # a single ideograph is a real word in both Chinese and Japanese.
                out.extend(run[i:i + 2] for i in range(len(run) - 1)) if len(run) > 1 \
                    else out.append(run)
            else:
                out.append(run)
    return out


def _same_script_runs(word: str) -> list:
    """``word`` split into maximal runs of one kind, segmented or not.

    A Japanese headline routinely carries a Latin brand name with no space around it —
    ``iPhone17発表`` is one ``\\w+`` match — and bigramming that whole blob would destroy the Latin
    word while leaving ``e17`` style junk. Splitting first keeps each half under the rule that suits
    it."""
    runs, run = [], ""
    for ch in word:
        if run and _unsegmented(ch) != _unsegmented(run[-1]):
            runs.append(run)
            run = ""
        run += ch
    if run:
        runs.append(run)
    return runs


def title_tokens(title: str, hyphen_compounds: bool = False,
                 unicode_words: bool = False) -> frozenset:
    """Content word tokens of a headline (lowercased, length > 2, stop-words removed).

    ``unicode_words`` (**candidate; the audit's instrument, defaulted OFF — see
    `story_service.unicode_words`**) replaces the ASCII-only ``[a-z0-9]+`` class with ``\\w+`` plus
    bigrams for unsegmented scripts. The defect it targets is measured and severe: ``[a-z0-9]+``
    yields **zero tokens** for Korean, Arabic, Chinese, Japanese, Russian and Tamil headlines, and
    :func:`pair_admits` rejects anything under :data:`MIN_TITLE_TOKENS` before any other test — so
    those articles **cannot join a story under any configuration**. Production 2026-08-27: those six
    languages contributed 472 window articles and **1** in-story article, 0.2%, against 29% for
    English.

    It is not only an international defect. ``Erdoğan`` tokenizes to ``erdo`` and ``Orbán`` to
    ``orb``, so two ENGLISH headlines about one event — one keeping the diacritics, one not — share
    only ``budapest`` and ``meets`` and fall below :data:`MIN_SHARED_TOKENS`.

    Two modes, and the production measurement is why there are two:

    ``True`` (*replace*)   **MEASURED 2026-08-27 AND REJECTED.** Every headline takes the Unicode
                           path. It rescued **78** articles and cost **149** that were already in
                           stories — 1.9x the benefit — and reached only 78 of the **2,630**
                           structurally-excluded articles in the window, 3.0%. Vietnamese coverage
                           went 32 -> **0** and Turkish 22 -> 11: accented Latin fragments into
                           short ASCII pieces today, many articles share those pieces, and replacing
                           them with whole words dissolves the clusters built on that coincidence.
    ``"fallback"``         the Unicode path fires **only when the ASCII tokenizer yields fewer than**
                           :data:`MIN_TITLE_TOKENS`. An article that already clusters keeps its exact
                           token set, so it cannot lose one — the 149-article cost is zero by
                           construction, and what remains is the 78-article gain plus whatever the
                           newly-tokenized rows join.

    Defaulted off and shipped off either way, exactly as ``hyphen_compounds`` is: this function
    decides the story partition for the whole product. Measure with
    ``audit_clustering_change.py --unicode-words`` / ``--unicode-fallback`` before proposing either.

    Pure numbers are dropped: in a headline a bare number is nearly always a count, a date or a
    listicle rank ("6 Best… Since 2010"), not the thing the story is about. It is a real trade —
    "737" in an aircraft story is lost — but a shared year linking two unrelated listicles is the
    commoner case by far.

    ``hyphen_compounds`` (**measured 2026-08-24 and REJECTED — the record lives on
    ``story_service.hyphen_compounds``; retained as the audit's instrument only**) ALSO emits
    each hyphenated compound joined: "X-Men" contributes "xmen" alongside whatever its fragments
    contribute. The defect it targeted is real ("x-men" survives only as the generic "men"),
    but the cure measured worse than the disease: adding a token to both sets grows the UNION
    even when the compound is not shared, so pairs sharing fragments but not compounds lose
    Jaccard — 121 clusters split on the live catalog, 2.6% of covered articles dropped, and
    the story count fell. An earlier revision of this docstring claimed the change was
    "additive only" because tokens are only added; the union growth is what that reasoning
    missed, and it is kept here so the next tokenizer candidate meets it."""
    lower = (title or "").lower()
    out = _keep(re.findall(r"[a-z0-9]+", lower), wide=False)
    if unicode_words and (unicode_words != "fallback" or len(out) < MIN_TITLE_TOKENS):
        out = _keep(_script_tokens(lower), wide=True)
    if hyphen_compounds:
        for compound in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)+", lower):
            joined = compound.replace("-", "")
            if len(joined) > 2 and not joined.isdigit() and joined not in _STOPWORDS:
                out.add(joined)
    return frozenset(out)


#: Description tokens admitted per article when the dek joins the clustering signal. A cap, not a
#: preference: candidate generation walks token POSTINGS, so cost is O(Σ_t |postings(t)|²) and an
#: uncapped 60-token dek would multiply the posting lists that already dominate the build. Twelve is
#: the first N content words, which for a news dek is the who/what — the tail is context and
#: attribution, which is exactly the prose that makes unrelated stories look similar.
DESC_TOKEN_CAP = 12


def description_tokens(description: str, cap: int = DESC_TOKEN_CAP) -> frozenset:
    """The first ``cap`` content tokens of a dek, in order of appearance.

    Same filter as :func:`title_tokens` — the stop-list, the length floor, the pure-digit drop —
    then truncated. **Order of appearance, not rarity**: picking by IDF would need a corpus pass
    before the corpus exists, and would reintroduce the weighting whose measured revert is recorded
    in ``story_service.use_idf``. First-N is deterministic, needs no global state, and front-loads
    the entities a news dek leads with.

    Deduplication happens BEFORE the cap, not after: a repeated word is skipped and the scan
    continues, so ``cap`` counts DISTINCT tokens and a dek that says "budget" three times spends
    one of them. Repetition is not evidence, and the alternative — truncate first, dedupe into a
    frozenset after — would silently give the most repetitive deks the smallest signals.
    """
    if cap <= 0:
        return frozenset()
    seen: list = []
    for t in re.findall(r"[a-z0-9]+", (description or "").lower()):
        if len(t) > 2 and not t.isdigit() and t not in _STOPWORDS and t not in seen:
            seen.append(t)
            if len(seen) >= cap:
                break
    return frozenset(seen)


def jaccard(a: frozenset, b: frozenset) -> float:
    """|A ∩ B| / |A ∪ B|, or 0 for an empty set / no overlap."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b) if inter else 0.0


def idf_weights(token_sets: "Sequence[frozenset]") -> dict:
    """token -> ``log(1 + N/df)`` over the input set. Rare tokens weigh more than common ones.

    Why this exists: plain Jaccard treats every shared word as equal evidence, so "trump" — in
    hundreds of headlines — counts the same as "buckenham", in two. That is what let SINGLE-LINKAGE
    chaining build a 203-article cluster out of ~12 unrelated stories: each link was individually
    plausible because it rested on words that are everywhere.

    Smoothed so weights stay strictly positive: a token present in EVERY item still scores
    ``log 2``, not zero, so a small corpus (a test with two items sharing every word) degrades to
    ordinary Jaccard rather than to no similarity at all.

    Deterministic: computed from the input set the caller already passed, so the same input yields
    the same weights and the same clusters. No corpus state, no cross-run coupling."""
    n = len(token_sets)
    df: dict = {}
    for toks in token_sets:
        for t in toks:
            df[t] = df.get(t, 0) + 1
    return {t: math.log(1.0 + n / c) for t, c in df.items()}


def weighted_jaccard(a: frozenset, b: frozenset, weights: Optional[dict]) -> float:
    """Jaccard over token WEIGHTS rather than counts. ``weights=None`` falls back to plain Jaccard,
    so one call site serves both modes."""
    if weights is None:
        return jaccard(a, b)
    if not a or not b:
        return 0.0
    inter = a & b
    if not inter:
        return 0.0
    den = sum(weights.get(t, 1.0) for t in (a | b))
    return (sum(weights.get(t, 1.0) for t in inter) / den) if den else 0.0


def pair_admits(tx: frozenset, ty: frozenset,
                time_x: Optional[datetime], time_y: Optional[datetime], *,
                sim: float = DEFAULT_SIM, window_days: float = DEFAULT_WINDOW_DAYS,
                min_shared: int = MIN_SHARED_TOKENS, min_tokens: int = MIN_TITLE_TOKENS,
                weights: Optional[dict] = None, time_decay: float = 0.0) -> bool:
    """Whether two items are close enough to belong to the same story — **the** pairwise rule.

    Extracted from :func:`cluster`'s inner ``pair_ok`` so it can be asked OUTSIDE a build, which is
    what source evaluation needs: "would this article have joined a story?" is the same question the
    clusterer answers, and answering it with a second implementation is how two definitions of "same
    event" quietly drift apart. ``cluster`` now delegates here, so there is one definition and any
    change to it moves both.

    ``time_decay`` (**candidate, default 0.0 = off and byte-identical**) is the similarity a pair
    must ADDITIONALLY reach per day of publication gap — see :func:`required_sim`. The hard window
    stays; the decay is a graded requirement inside it, so a pair three hours apart is judged at
    ``sim`` and a pair three days apart at ``sim + 3 * time_decay``.

    The ``evidence`` hook stays at ``cluster``'s call site rather than here: it is keyed on item
    INDICES into a specific build, which has no meaning to a caller holding two token sets."""
    floor = max(1, min_tokens)
    if len(tx) < floor or len(ty) < floor or len(tx & ty) < min_shared:
        return False
    return (weighted_jaccard(tx, ty, weights) >= required_sim(sim, time_decay, time_x, time_y)
            and within_window(time_x, time_y, window_days))


def parse_time(iso: str) -> Optional[datetime]:
    s = (iso or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def within_window(a: Optional[datetime], b: Optional[datetime], days: float) -> bool:
    """Whether two times are within ``days`` of each other. Missing timestamps never block a match."""
    if a is None or b is None:
        return True
    return abs((a - b).total_seconds()) <= days * 86400.0


def required_sim(sim: float, decay: float, a: Optional[datetime], b: Optional[datetime]) -> float:
    """The similarity a pair published ``|a - b|`` apart must reach: ``sim`` plus ``decay`` per
    day of gap. **Time decay inside the gate**, the Stage-0 candidate from
    ``docs/CLUSTERING_APPROACHES_RESEARCH.md`` §2.7.

    Why a graded requirement and not a shorter window: the hard window is one number for every
    pair, so it either admits a six-day-old recurring-series instance on the same evidence as a
    same-hour paraphrase, or it cuts the sagas that legitimately run for days. Coverage of one
    event is burst-shaped — the same finding behind ``DEFAULT_MERGE_MAX_GAP_HOURS`` — so a pair
    far apart in time SHOULD need more lexical evidence than a pair minutes apart; two instances of
    a daily column share exactly the boilerplate, and that boilerplate does not grow with the gap.

    ``decay <= 0`` returns ``sim`` untouched, and a missing timestamp on either side returns
    ``sim`` too: absence of a date is never evidence of distance, the same fail-open rule
    :func:`within_window` applies. At ``decay = 0.02`` a pair 3 days apart needs 0.34 against the
    0.28 floor, and a 6-day pair 0.40. Deterministic; a pure function of its inputs."""
    if decay <= 0.0 or a is None or b is None:
        return sim
    return sim + decay * (abs((a - b).total_seconds()) / 86400.0)


# --------------------------------------------------------------------------- #
# Instance anchors — the identity that pure digits carry, kept OUT of the similarity tokens.
#
# `title_tokens` drops bare numbers on purpose (a shared year linking two listicles is the commoner
# case), and the price of that trade is a whole failure class the rubric names in rules 3, 3b and 6:
# "Wordle hints for September 2" and "…for September 3", "Week 3 picks" and "Week 4 picks", "Q2
# results" and "Q3 results" reduce to IDENTICAL token sets — Jaccard 1.00 on the template — and no
# threshold, lexicon or quorum can separate them, because the only thing that differs is the number
# the tokenizer threw away. These functions read that number back as an ANCHOR, a slot->value
# fact carried beside the tokens rather than inside them, so the similarity trade stands and the
# evidence hook can refuse an edge whose two sides name different instances.
# --------------------------------------------------------------------------- #

#: Month names -> month number for the calendar-date anchor: English plus the catalog's other
#: large Latin-script languages (de, fr, es, pt, it), accented and ASCII-folded forms both, because
#: headlines carry both. A month word is an anchor ONLY beside a day number ("Sept. 2",
#: "2 September", "2. September", "2 de septiembre", "2026-09-02"), never on its own.
_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
    # de
    "januar": 1, "februar": 2, "märz": 3, "marz": 3, "maerz": 3, "mai": 5, "juni": 6, "juli": 7,
    "oktober": 10, "okt": 10, "dezember": 12, "dez": 12,
    # fr
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "juin": 6, "juillet": 7,
    "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
    "decembre": 12,
    # es / pt
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12, "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "maio": 5,
    "junho": 6, "julho": 7, "setembro": 9, "outubro": 10, "dezembro": 12,
    # it
    "gennaio": 1, "febbraio": 2, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7,
    "settembre": 9, "ottobre": 10, "dicembre": 12,
}

#: Month words that are also ordinary words ("Trump may 2 …", "march 5 miles", Spanish "mar"):
#: these count only when the headline capitalises them.
_AMBIGUOUS_MONTHS = frozenset({"may", "march", "mar", "sep", "jun", "jul"})

#: Enumerated instance slots — the words that turn a number into WHICH ONE. Two headlines carrying
#: the same slot with different values name different instances of a series: rubric rule 3
#: (recurring series instances are different events), 3b (different fixtures of one competition)
#: and 6 (numbers and ordinals are identity anchors when they name the instance). The value is the
#: slot's canonical name; synonyms map onto one slot so "GW 3" and "gameweek 3" agree.
#:
#: What is deliberately ABSENT matters as much as what is here, and each absence has a rubric
#: receipt. ``day`` — rule 2: "Batwara day 2 collection" and "Batwara day 3 collection" are ONE
#: film's run (the ``batwara-days`` exhibit, same_event), while "Batwara day 2" vs "Vishwanath day
#: 2" are two films, and the number cannot tell those apart (the template gate's ``tracker`` lexicon
#: already handles the second). ``round``, ``lap``, ``half`` and the word ``quarter`` — a fight's
#: round 3 and round 9, a race's lap 5 and lap 30, a match's first and second half or third and
#: fourth quarter are updates of ONE occurrence. A slot whose consecutive values are usually the
#: same event would split real stories, so those slots are excluded and the exclusion is pinned by
#: test. Fiscal quarters keep their COMPACT form only (``Q1``–``Q4``), which basketball never uses.
_ANCHOR_SLOTS = {
    "week": "week", "gameweek": "week", "gw": "week",
    "matchday": "matchday",
    "game": "game",
    "episode": "episode", "ep": "episode",
    "season": "season",
    "part": "part", "chapter": "chapter",
    "leg": "leg",
    "test": "test", "odi": "odi", "t20i": "t20i",
    "volume": "volume", "vol": "volume", "issue": "issue", "edition": "edition",
}

_SLOT_ALT = "|".join(sorted(map(re.escape, _ANCHOR_SLOTS), key=len, reverse=True))
#: "week 3", "Ep. 5", "Season #2", "vol 4" — the slot word THEN the number (1–3 digits, so a year
#: can never be read as a slot value).
_SLOT_AFTER_RE = re.compile(r"\b(" + _SLOT_ALT + r")\s*\.?\s*#?\s*(\d{1,3})\b(?![,.]\d)")
#: "2nd Test", "1st leg", "3rd episode" — a numeric ordinal THEN the slot word.
_SLOT_BEFORE_RE = re.compile(r"\b(\d{1,3})(?:st|nd|rd|th)\s+(" + _SLOT_ALT + r")\b")
#: "first leg" / "second leg" — the one slot whose WORD ordinal is standard, unambiguous phrasing.
_LEG_WORD_RE = re.compile(r"\b(first|second)[ -]leg\b")
#: Fiscal quarters and TV episodes in their compact forms: "Q3", "Q3 2026", "S2E5", "S02E05".
_QUARTER_RE = re.compile(r"\bq([1-4])\b")
_SEASON_EPISODE_RE = re.compile(r"\bs(\d{1,2})\s*e(\d{1,3})\b")

_MONTH_ALT = "|".join(sorted(map(re.escape, _MONTHS), key=len, reverse=True))
#: "September 2", "Sept. 2nd", "Sept 2, 2026" — month THEN day.
_DATE_MD_RE = re.compile(r"(?<![a-zäöüéèêàç])(" + _MONTH_ALT + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?![,.:]\d)")
#: "2 September", "2. September", "2nd of September", "2 de septiembre" — day THEN month.
_DATE_DM_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\.?\s+(?:of\s+|de\s+|d['’]\s*)?(" + _MONTH_ALT
                         + r")(?![a-zäöüéèêàç])")
_DATE_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def instance_anchors(title: str) -> dict:
    """``{slot: frozenset(values)}`` — the instance anchors a headline carries.

    Slots are ``"date"`` (``"MM-DD"`` strings from explicit calendar dates) and the enumerated
    series slots in :data:`_ANCHOR_SLOTS` (integers). A headline with no anchor returns ``{}``,
    which every consumer treats as "nothing to say" — absence is never evidence.

    Years are deliberately NOT a slot. Inside a six-day window two articles about one event
    routinely carry different years as CONTEXT ("the 2024 campaign fine" / "ahead of the 2026
    midterms"), while two different events differing only by year almost never share a window;
    the precision of a year veto is poor exactly where the clusterer looks. Bare counts are not
    anchors either — rule 6's second clause: conflicting figures for one occurrence (early counts
    vs updated counts) are still one event, so "12 dead" and "15 dead" must stay joinable.

    Pure and deterministic; ASCII case-folded once, with the month-word ambiguity check reading
    the ORIGINAL capitalisation ("Trump may 2…" is not a date; "May 2" is)."""
    raw = title or ""
    lower = raw.lower()
    out: dict = {}

    def add(slot: str, value) -> None:
        out[slot] = out.get(slot, frozenset()) | frozenset([value])

    for m in _DATE_MD_RE.finditer(lower):
        word, day = m.group(1), int(m.group(2))
        if word in _AMBIGUOUS_MONTHS and not raw[m.start(1)].isupper():
            continue
        if 1 <= day <= 31:
            add("date", f"{_MONTHS[word]:02d}-{day:02d}")
    for m in _DATE_DM_RE.finditer(lower):
        day, word = int(m.group(1)), m.group(2)
        if word in _AMBIGUOUS_MONTHS and not raw[m.start(2)].isupper():
            continue
        if 1 <= day <= 31:
            add("date", f"{_MONTHS[word]:02d}-{day:02d}")
    for m in _DATE_ISO_RE.finditer(lower):
        month, day = int(m.group(2)), int(m.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            add("date", f"{month:02d}-{day:02d}")
    for m in _SLOT_AFTER_RE.finditer(lower):
        add(_ANCHOR_SLOTS[m.group(1)], int(m.group(2)))
    for m in _SLOT_BEFORE_RE.finditer(lower):
        add(_ANCHOR_SLOTS[m.group(2)], int(m.group(1)))
    for m in _LEG_WORD_RE.finditer(lower):
        add("leg", 1 if m.group(1) == "first" else 2)
    for m in _QUARTER_RE.finditer(lower):
        add("quarter", int(m.group(1)))
    for m in _SEASON_EPISODE_RE.finditer(lower):
        add("season", int(m.group(1)))
        add("episode", int(m.group(2)))
    return out


def anchors_conflict(a: dict, b: dict) -> Optional[str]:
    """The first slot on which two anchor dicts DISAGREE — present on both sides with no value in
    common — or ``None``. A slot missing on either side is silence, not disagreement, and a shared
    value on a slot is agreement however many other values sit beside it (a "Sept 2–3" range
    agrees with "Sept 3")."""
    for slot in sorted(a):
        va, vb = a[slot], b.get(slot)
        if va and vb and not (va & vb):
            return slot
    return None


def anchor_consensus(anchor_sets: "Sequence[dict]", min_votes: int = 2) -> dict:
    """A cluster's CORROBORATED anchors: ``{slot: frozenset(values carried by >= min_votes
    members)}``. The same corroboration discipline as the geo and entity consensuses — one
    member's headline is a sample of one — so a singleton has no consensus and fails every
    cluster-level test open, while a two-article story's consensus is exactly what its two
    members agree on."""
    votes: dict = {}
    for anchors in anchor_sets:
        for slot, values in anchors.items():
            per = votes.setdefault(slot, {})
            for v in values:
                per[v] = per.get(v, 0) + 1
    out: dict = {}
    for slot, per in votes.items():
        agreed = frozenset(v for v, c in per.items() if c >= min_votes)
        if agreed:
            out[slot] = agreed
    return out


class DSU:
    """Union-find with lower-index roots, so cluster roots (and therefore output order) are stable."""

    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)   # attach to the lower index → deterministic roots


#: Members a side must already have before ``groups`` scope asks it for breadth.
SUPPORT_GROUP_MIN = 2


def _link_ok(a: "list[int]", b: "list[int]", *, pair_ok: Callable[[int, int], bool],
             quorum: float, min_support: int = DEFAULT_MIN_SUPPORT,
             support_scope: str = "any") -> bool:
    """Whether two clusters may join — the CLUSTER-level linkage test, in one cross-pair scan.

    Two independent criteria over the same cross-pairs, ANDed:

    * **quorum** — what FRACTION of cross-pairs independently pass the pairwise gate. Single
      linkage asks "does the joining article match *any* member?"; this asks "does it match
      *enough* of them?"
    * **support breadth** — how many DISTINCT members of each side those passing pairs involve
      (``min_support``). A genuine new article about the same event resembles several members of
      the cluster it joins. A bridging article resembles each side through itself alone, so one
      side's breadth is 1 however many cross-pairs it wins.

    The breadth requirement is capped at what a side can possibly supply (``min(min_support,
    |side|)``), which is what keeps the rule off story FORMATION: two singletons have one member
    each to offer, so their requirement is 1 and the pair that already passed the similarity gate
    satisfies it. A story still forms from one pair and still grows one article at a time — but a
    growing article must now match ``min_support`` distinct members of the cluster receiving it,
    and a cluster of two or more can no longer be annexed through a single member.

    ``min_support <= 1`` skips the breadth bookkeeping entirely rather than computing a requirement
    it would always meet. That is not just an optimisation: participants are counted over the
    SAMPLED sides, and the candidate pair that triggered this merge is not guaranteed to be inside
    a 32-member sample, so a "trivially satisfied" breadth test could refuse a merge today's rule
    admits. Skipping keeps the off state byte-identical by construction."""
    sa, sb = a[:LINK_SAMPLE], b[:LINK_SAMPLE]
    total = len(sa) * len(sb)
    if not total:
        return False
    need = math.ceil(quorum * total - 1e-9) if quorum > 0.0 else 0
    breadth = min_support > 1
    if breadth and support_scope == "groups" and not (
            len(sa) >= SUPPORT_GROUP_MIN and len(sb) >= SUPPORT_GROUP_MIN):
        # ``groups``: corroboration is demanded when two bodies of coverage claim to be one
        # event, not when a single article claims to belong to one. Measured 2026-08-25, the
        # latter is where the ``any`` scope's whole 8.7% cost came from.
        breadth = False
    need_a = min(min_support, len(sa)) if breadth else 0
    need_b = min(min_support, len(sb)) if breadth else 0
    hits, seen = 0, 0
    seen_a: set = set()
    seen_b: set = set()
    for k, x in enumerate(sa):
        for y in sb:
            seen += 1
            if pair_ok(x, y):
                hits += 1
                if breadth:
                    seen_a.add(x)
                    seen_b.add(y)
                if hits >= need and len(seen_a) >= need_a and len(seen_b) >= need_b:
                    return True
            elif hits + (total - seen) < need:
                return False                            # cannot reach the quorum any more
        # Same early abort for breadth: every remaining row can contribute at most one more
        # distinct member to side A, so once that ceiling is below the requirement, stop.
        if breadth and len(seen_a) + (len(sa) - 1 - k) < need_a:
            return False
    return hits >= need and len(seen_a) >= need_a and len(seen_b) >= need_b


def cluster(items: Sequence, *, tokens: Callable[[object], frozenset],
            time: Callable[[object], Optional[datetime]],
            sim: float = DEFAULT_SIM, window_days: float = DEFAULT_WINDOW_DAYS,
            min_shared: int = MIN_SHARED_TOKENS,
            min_tokens: int = MIN_TITLE_TOKENS,
            idf: bool = False,
            link_quorum: float = DEFAULT_LINK_QUORUM,
            min_support: int = DEFAULT_MIN_SUPPORT,
            support_scope: str = "any",
            evidence: Optional[Callable[[int, int], bool]] = None,
            merge_ok: Optional[Callable[[list, list], bool]] = None,
            time_decay: float = 0.0) -> "list[list[int]]":
    """Group item **indices** into clusters. ``tokens(item) → frozenset`` and
    ``time(item) → datetime | None`` are the accessors. Two items join the same cluster when their
    token Jaccard ≥ ``sim`` **and** their times are within ``window_days``. Returns a list of clusters,
    each a list of indices into ``items``; deterministic in membership and order.

    Candidate generation is **blocked by an inverted token index** rather than all-pairs. This is an
    exact optimisation, not an approximation: ``jaccard(a, b) ≥ sim`` for any ``sim > 0`` requires
    ``|a ∩ b| ≥ 1``, so a pair sharing no token can never match and is safe to skip. Only pairs that
    share at least one token are scored, which is what makes the *whole* catalog clusterable —
    all-pairs made the caller cap the input, and that cap (counted in items, not time) silently
    narrowed as ingestion grew, collapsing the story count.

    Cost is O(Σ_t |postings(t)|²) rather than O(n²) — near-linear for headlines, whose content tokens
    are mostly rare. It degrades toward all-pairs only if every item shares a token with every other,
    which cannot be worse than the previous behaviour.

    ``link_quorum`` switches the LINKAGE RULE. At ``0.0`` (the default, and the measured production
    baseline) grouping is the transitive closure of the pairwise relation — pure single linkage.
    Above ``0.0`` a merge additionally requires that fraction of cross-pairs between the two
    clusters to pass the same pairwise gate. ``min_support`` adds the orthogonal requirement that
    the passing cross-pairs involve that many DISTINCT members on each side, which is what stops
    one bridging article from welding two unrelated events together even when it wins enough
    cross-pairs to satisfy a fraction. ``support_scope`` narrows WHERE that requirement applies —
    ``"any"`` asks it of every side with two or more members, ``"groups"`` only when both sides
    are already groups. Both are evaluated in one scan — see ``_link_ok``.

    The two modes differ in a property worth stating plainly. Single linkage is **order-independent**:
    transitive closure is unique, so the answer does not depend on which merge is attempted first.
    A quorum rule is not — accepting one merge changes the membership a later quorum is measured
    against. Merges are therefore consumed **best-first** (highest similarity, ties by index), so the
    ordering is a defensible one rather than an artefact of item order, and the result stays
    deterministic. It is still a greedy result, not a global optimum.

    ``evidence`` and ``merge_ok`` admit NON-LEXICAL edge evidence (X4,
    docs/STORY_ENTITY_EVIDENCE_PLAN.md) without this module learning what the evidence is — both
    receive item indices and answer yes/no, and this layer stays free of Story/country knowledge:

    * ``evidence(x, y) -> bool`` is ANDed into the pairwise gate — candidate admission, quorum
      cross-pair scoring and any caller-side re-cluster all consult the SAME predicate, so the
      evidence cannot gate one and not another. It can only remove edges, never add one.
    * ``merge_ok(a_members, b_members) -> bool`` is a CLUSTER-level gate consulted before every
      union (member index lists, cheapest test first — before any quorum scoring). Setting it
      forces the bookkeeping path even at ``link_quorum 0.0``, because a gate needs memberships;
      that path consumes merges best-first, so the result is deterministic for the same reason
      the quorum's is. ``min_support > 1`` forces that path for the same reason.

    Both default to ``None``, and ``None`` is byte-identical to the previous behaviour — the same
    opt-in discipline as ``idf`` and ``link_quorum``.

    ``time_decay`` raises the similarity a pair must reach by that much per day of publication
    gap (:func:`required_sim`); candidate admission and quorum cross-pair scoring both consult it,
    through the one ``pair_admits`` rule. ``0.0`` is byte-identical.
    """
    n = len(items)
    toks = [tokens(it) for it in items]
    times = [time(it) for it in items]
    decaying = time_decay > 0.0

    # token -> ascending item indices carrying it. Built once; membership tests below stay exact.
    postings: dict = {}
    for i, t in enumerate(toks):
        for tok in t:
            postings.setdefault(tok, []).append(i)
    # Rarity weighting (opt-in): shared common words stop counting as much evidence as shared rare
    # ones. Computed from THIS input set, so determinism is unaffected.
    weights = idf_weights(toks) if idf else None
    floor = max(1, min_tokens)

    def pair_ok(x: int, y: int) -> bool:
        """The pairwise admission gate, as one predicate. The quorum test scores cross-pairs by
        exactly the rule that admitted the original pair — a weaker bar there would let cross-pairs
        that could never merge on their own count as support for merging."""
        return (pair_admits(toks[x], toks[y], times[x], times[y],
                            sim=sim, window_days=window_days, min_shared=min_shared,
                            min_tokens=min_tokens, weights=weights, time_decay=time_decay)
                and (evidence is None or evidence(x, y)))

    def candidates():
        """Yield ``(i, j, score)`` for every pair passing the pairwise gate, ``i < j``."""
        for i in range(n):
            ti = toks[i]
            if len(ti) < floor:
                continue                                # too little to say anything: stays a singleton
            # Walking the postings counts SHARED TOKENS per candidate as a by-product, so the
            # min_shared gate costs nothing extra — and it prunes most pairs before any Jaccard.
            # Two mechanical changes, both output-preserving, from profiling this loop at 49% of a
            # whole build with 6.7M interpreted `dict.get` calls behind it:
            #
            # 1. BISECT past `j <= i`. Postings lists are built by `enumerate`, so they are sorted
            #    ascending; the old code walked every entry and discarded the first half with an
            #    `if`. For a token carried by `d` articles that is `d` steps per occurrence and
            #    `d**2` overall, when `d**2 / 2` will do. The high-frequency tokens that dominate
            #    the cost are exactly the ones with the most to skip.
            # 2. COUNT IN C. `Counter.update(list)` dispatches to `_count_elements`, so the tally
            #    that was a Python-level `shared.get(j, 0) + 1` per posting becomes one C call per
            #    token. Counter is a dict subclass and `update` inserts in first-seen order, so
            #    `shared.items()` below yields exactly what it did before.
            shared: Counter = Counter()
            for tok in ti:
                plist = postings[tok]
                tail = plist[bisect_right(plist, i):]
                if tail:
                    shared.update(tail)
            for j, overlap in shared.items():
                if overlap < min_shared or len(toks[j]) < floor:
                    continue
                score = weighted_jaccard(ti, toks[j], weights)
                # The decay is resolved per pair only when it is on: the off path compares
                # against the same `sim` it always did, so the baseline stays byte-identical.
                need = required_sim(sim, time_decay, times[i], times[j]) if decaying else sim
                if (score >= need and within_window(times[i], times[j], window_days)
                        and (evidence is None or evidence(i, j))):
                    yield i, j, score

    dsu = DSU(n)
    if link_quorum <= 0.0 and merge_ok is None and min_support <= 1:
        # Single linkage — union on sight. Kept as its own path so the default cannot drift: no
        # sort, no membership bookkeeping, byte-identical grouping to the measured baseline.
        for i, j, _ in candidates():
            dsu.union(i, j)
    else:
        members = {i: [i] for i in range(n)}
        for i, j, _ in sorted(candidates(), key=lambda p: (-p[2], p[0], p[1])):
            ra, rb = dsu.find(i), dsu.find(j)
            if ra == rb:
                continue                                # already together via a stronger merge
            # Cheapest gate first: merge_ok is one aggregate test over data the caller already
            # holds, the quorum is up to LINK_SAMPLE² weighted Jaccards.
            if merge_ok is not None and not merge_ok(members[ra], members[rb]):
                continue
            if ((link_quorum > 0.0 or min_support > 1)
                    and not _link_ok(members[ra], members[rb], pair_ok=pair_ok,
                                     quorum=link_quorum, min_support=min_support,
                                     support_scope=support_scope)):
                continue
            dsu.union(i, j)
            root, other = (ra, rb) if ra < rb else (rb, ra)   # DSU keeps the lower index as root
            members[root] = sorted(members[root] + members.pop(other))

    groups: dict = {}
    for i in range(n):
        groups.setdefault(dsu.find(i), []).append(i)
    return list(groups.values())
