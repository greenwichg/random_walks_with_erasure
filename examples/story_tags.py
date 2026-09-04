"""story_tags.py — the topics/entities a story is ABOUT, ranked, with provenance.

WHAT THIS IS NOT: a new extraction pipeline. Every name here already exists in
``article_entities``, written per article by two providers the deployment already runs —
GDELT's GKG (``person``/``org``) and our own capitalised-span reader (``entity_spans``,
``span``). This module is the projection of those article rows onto the STORY, which is the
level a reader means when they ask what a story is about, plus the ranking that decides which
of them are worth showing.

Three rules carried over from the entity channel, not reinvented, because they are the ones
that were measured:

* **Corroboration.** A name counts only when >= 2 of the story's members carry it — the
  ``_story_entity_consensus`` discipline, whose receipt is that 93.1% of covered members share
  their own story's consensus. One member's testimony is a sample of one, and a tag built from
  it is a tag built from one outlet's phrasing.
* **Identity denoising.** ``story_service.entity_noise`` drops platforms, outlet names and bare
  country names by IDENTITY rather than by frequency, because a frequency floor punishes exactly
  the biggest events' entities.
* **Story frequency.** A name in a great many of the window's stories is a background name, not
  a subject — the same observation that gave ``ENTITY_MERGE_MAX_STORY_DF`` its ceiling. Here it
  does two jobs: it bars the most ubiquitous names outright (:data:`TAG_MAX_STORY_SHARE`) and,
  below that, it is the specificity term in the score, so "democratic republic of the congo"
  outranks a name half the catalog carries without either being hand-listed.

THE CATEGORY IS A TAG, AND IT SAYS SO. A story's ``topic`` is the mode of its members'
categories — real, useful for navigation, and NOT evidence about this story specifically, which
is the distinction the brief draws. It is emitted with ``source="topic"`` and scored below every
corroborated entity, so it can never displace one; a client that wants only evidence-derived
tags can filter on the source rather than guess from the string.

INHERITANCE is deliberately narrow. Two stories the similarity measure calls strongly related
still cover different events, so their tag sets are not interchangeable — the failure mode is a
tag that is true of the neighbour and false here, spreading outward one weak relation at a time.
A tag crosses only when the TARGET's own text corroborates it, or when several related stories
agree on it; see :func:`inherit_tags`.
"""

from __future__ import annotations

import math
import re

import entity_spans               # the same capitalised-span reader ingest writes rows with

#: Most tags kept per story. The rail shows a handful and offers the rest behind "Show All", so
#: this is the point past which a longer list stops being a description of the story.
TAG_CAP = 12

#: Members that must carry a name before it is a tag. The entity channel's corroboration rule.
TAG_MIN_MEMBERS = 2

#: A name carried by more than this SHARE of the window's stories is background, not subject, and
#: is dropped before scoring. Expressed as a share rather than a count because the window's story
#: total moves by an order of magnitude between a demo catalog and production, and a fixed count
#: calibrated on one is meaningless on the other — the lesson the Similar Stories floor cost.
TAG_MAX_STORY_SHARE = 0.15

#: …and a share alone is not enough either, which is the same lesson from the other end. On a
#: catalog of three stories the share is 0.45, truncates to a ceiling of ZERO, and every name two
#: stories agree on — which is to say every name worth having — is deleted as background. The
#: ceiling is therefore never lower than this, so "shared by a handful" stays a tag until the
#: window is big enough for the share to mean something. Six matches
#: ``story_service.ENTITY_MERGE_MAX_STORY_DF``, chosen there against the largest genuine duplicate
#: family ever measured.
TAG_MIN_DF_CEILING = 6

#: Floor on the final score. Below this a tag is technically corroborated and practically noise.
TAG_MIN_SCORE = 0.05

#: Score a story's own category is emitted at — under every corroborated entity by construction,
#: because an entity's score is its corroboration (>= 1.0 before specificity) times a specificity
#: term that only exceeds 1 for names rarer than the whole window.
TOPIC_TAG_SCORE = 0.04

#: Sources, in the order a tie is broken. Direct evidence outranks an inherited claim outranks
#: the shelf the story sits on.
SOURCE_DIRECT = "direct"
SOURCE_INHERITED = "inherited"
SOURCE_TOPIC = "topic"
_SOURCE_RANK = {SOURCE_DIRECT: 0, SOURCE_INHERITED: 1, SOURCE_TOPIC: 2}

#: Share of a direct tag's score an inherited copy keeps, before the relation strength scales it
#: further. An inherited tag is a claim about a neighbour applied here, so it starts below the
#: weakest thing this story said about itself.
INHERIT_DECAY = 0.6

#: A tag must reach this score in the SOURCE story before it is eligible to travel at all. Only
#: high-confidence tags propagate — a marginal tag on the neighbour is not evidence here.
INHERIT_MIN_SOURCE_SCORE = 0.12

#: Related stories that must independently carry a tag for it to cross WITHOUT the target's own
#: text corroborating it. Two, for the reason the entity merge takes two: one neighbour agreeing
#: with itself is one source.
INHERIT_MIN_AGREEING = 2

#: Words left lower-case when a stored name is turned into a display label. Names are stored
#: lower-cased and whitespace-collapsed, so the label has to be rebuilt rather than remembered.
_LABEL_LOWER = frozenset("of the de del della di da do dos das du des la le les van von der den "
                         "al bin ibn for and in on at to a an".split())

#: Short all-consonant tokens that are almost certainly initialisms once uppercased ("who", "un"
#: and other real words are deliberately NOT here — they are indistinguishable from words at this
#: level, and a wrong uppercase reads worse than a wrong lowercase).
_ACRONYM_RE = re.compile(r"^(?:[bcdfghjklmnpqrstvwxz]{2,5}|[a-z]\.(?:[a-z]\.)+)$")

_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def label_for(name: str) -> str:
    """Display form of a stored (lower-cased) tag name.

    Title Case with the connector words left down, so "democratic republic of the congo" reads as
    it should. Initialisms are uppercased only when they cannot be an English word, because
    getting "Who" wrong on the World Health Organization is a smaller error than shouting "THE"."""
    words = name.split()
    out = []
    for i, w in enumerate(words):
        if _ACRONYM_RE.match(w):
            out.append(w.upper())
        elif i > 0 and w in _LABEL_LOWER:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def _tokens(text: str) -> set:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) > 2}


def _story_text(story: dict) -> str:
    """Everything the story says about itself: title, summary, and every coverage headline. The
    same span :func:`story_service._similar_profile` reads, for the same reason — a headline alone
    is the input the clusterer already failed on."""
    parts = [story.get("title") or "", story.get("summary") or ""]
    parts.extend((row.get("headline") or "") for row in story.get("coverage") or [])
    # Joined with a SENTENCE BREAK, not a space. Headlines carry no terminal punctuation, so a
    # plain join runs one into the next and :func:`phrases` reads straight across the seam — a
    # story whose headlines ended "…of the Congo" and began "Ebola outbreak grows…" produced the
    # phrase "Democratic Republic of the Congo Ebola" and offered it to readers as a topic.
    return " . ".join(p for p in parts if p)


def direct_votes(members: list, entities: dict, *, noise) -> dict:
    """``name -> member votes`` for one story's members, noise names excluded.

    Two sources, both already in the codebase, unioned because neither alone covers the catalog:

    * the stored ``article_entities`` rows, which saw the article's full dek at ingest time and
      include whatever GDELT's GKG contributed;
    * ``entity_spans.extract`` re-run over each member's HEADLINE here, which costs a regex pass
      and covers every member whether or not a row was ever written for it — a story ingested
      before the extractor was switched on, a deployment that has never run the backfill, and
      every test that seeds a catalog without touching the side table.

    One vote per MEMBER per name however many times that member repeats it, which is what makes
    the count a measure of corroboration between outlets rather than of one outlet's verbosity.
    ``noise`` is injected so this module holds no second opinion about what a noise name is."""
    votes: dict = {}
    for m in members:
        ents = entities.get(m.get("id") or m.get("url")) or {}
        raw = {n for names in ents.values() for n in names if n}
        raw.update(entity_spans.extract(m.get("headline") or ""))
        raw.update(phrases(m.get("headline") or ""))
        # Tidied AS THEY ARRIVE, so the stored side-table rows — written by an extractor that
        # trims less — are held to the same shape as the ones read here, and two spellings of one
        # entity do not become two tags.
        seen = {t for t in (tidy(n) for n in raw) if t}
        for name in seen:
            if noise(name):
                continue
            votes[name] = votes.get(name, 0) + 1
    return votes


_PHRASE_WORD_RE = re.compile(r"[^\W\d_][\w'’.\-]*", re.UNICODE)

#: Longest a tag name may be, in CONTENT words (connectors and other function words do not count,
#: so "Democratic Republic of the Congo" is three). A topic is a name, not a clause: past this the
#: string is describing an event rather than identifying one, and the reader is being offered a
#: sentence to click on.
#:
#: Five, not three: "New York City Police Department" and "Dolly Parton Imagination Library" are
#: real four-word entities, and a cap that cannot hold them would trade one wrong answer for
#: another. What actually kills the headlines is the Title Case guard in :func:`phrases`; this is
#: the backstop for a sentence-cased headline that slips past it.
TAG_MAX_CONTENT_WORDS = 5

#: …and a character bound for the same reason, because content words can be long. Comfortably
#: above every real entity name the catalog carries and comfortably below a headline.
TAG_MAX_CHARS = 48


def tidy(name: str) -> str:
    """Trim the grammar off a name's ends, leaving the name.

    A capitalised run can START with a preposition the sentence needed and the entity does not —
    "Beside the Dolly Parton statue" gave production the topic "Beside the Dolly Parton". REJECTING
    that string is easy and wrong: it throws away Dolly Parton along with the preposition. Trimming
    keeps the entity and drops the grammar, which is what the reader wanted from the run.

    ``entity_spans._normalise`` already trims connectors and calendar words for the clustering
    reader; this widens the same idea to every function word, which that reader cannot safely do
    (a trimmed name there changes which articles cluster) and this one can."""
    words = [_POSSESSIVE.sub("", w) for w in (name or "").split()]
    while words and words[0] in entity_spans._FUNCTION_WORDS:
        words.pop(0)
    while words and words[-1] in entity_spans._FUNCTION_WORDS:
        words.pop()
    return " ".join(w for w in words if w)


def well_formed(name: str) -> bool:
    """Whether a normalised name is shaped like a NAME rather than like a fragment or a clause.

    Four rejections, each one a defect seen in the live rail:

    * too many content words, or too many characters — a headline wearing a topic's clothes;
    * a leading function word — "Beside the Dolly Parton" got in because a preposition can begin a
      capitalised run and neither ``_LEADS`` (sentence openers) nor ``_CONNECTORS`` (words a name
      runs THROUGH) lists it. A name does not start with "beside", "amid" or "over";
    * a possessive — "Parton's" is not an entity, it is an entity with grammar attached;
    * nothing left after normalisation.
    """
    words = name.split()
    if not words or len(name) > TAG_MAX_CHARS:
        return False
    if len(words) > 1 and words[0] in entity_spans._FUNCTION_WORDS:
        return False
    if any(_POSSESSIVE.search(w) for w in words):
        return False
    content = [w for w in words if w not in entity_spans._FUNCTION_WORDS]
    return 1 <= len(content) <= TAG_MAX_CONTENT_WORDS


_POSSESSIVE = re.compile(r"['’]s$")


def phrases(text: str) -> list:
    """Capitalised runs that may chain THROUGH consecutive connectors — "Democratic Republic of
    the Congo", "Bank of the West", "Museo Nacional de Bellas Artes".

    ``entity_spans`` deliberately cannot do this: it admits a connector only when the very next
    word is capitalised, so "Democratic Republic of the Congo" comes back as "Democratic Republic"
    and "Congo", two fragments of one country. That restraint is right where it lives — a longer
    run is a bigger claim, and a wrong one there can weld two events into one story. Nothing here
    can weld anything, and a reader offered "Democratic Republic" as a topic has been offered a
    mistake. So the tag side reads the longer phrase, and the subset rule in :func:`extract_tags`
    then absorbs the fragments the other extractors produced from the same words.

    Everything else is borrowed from ``entity_spans`` rather than restated — the connector list,
    the sentence-initial leads, the trim words, the normalisation — so the two readers cannot
    develop different opinions about what a name looks like."""
    out: list = []
    for segment in entity_spans._SEGMENT_RE.split(text or ""):
        words = _PHRASE_WORD_RE.findall(segment)
        # A TITLE CASE segment capitalises every content word, so capitalisation says nothing about
        # names in it — and a reader who chained through it got the headline back AS a topic:
        # production offered "Dolly Parton Laid to Rest Privately Days After Her Death" and "Bad
        # Wolves Guitarist Quits Band Over Dolly Parton Tribute" in the Similar News Topics rail.
        # `entity_spans.extract` has always refused these (it returns [] for such a headline); this
        # reader did not, which is the whole of that defect. Per SEGMENT rather than per string,
        # because this is also called on the story's whole text, where one title-cased headline
        # must not silence the sentence-cased ones beside it.
        if entity_spans._title_cased(words):
            continue
        run: list = []
        caps = 0

        def flush() -> None:
            nonlocal run, caps
            if caps >= 2:
                name = tidy(entity_spans._normalise(run))
                if (len(name.split()) >= 2 and name not in entity_spans._NOISE_SPANS
                        and well_formed(name) and name not in out):
                    out.append(name)
            run, caps = [], 0

        pending: list = []
        for idx, w in enumerate(words):
            low = w.lower()
            if w[:1].isupper():
                if idx == 0 and not run and low in entity_spans._LEADS:
                    continue                       # capitalised by position, not identity
                run.extend(pending)
                pending = []
                run.append(w)
                caps += 1
            elif run and low in entity_spans._CONNECTORS:
                pending.append(w)                  # held: kept only if a capital follows
            else:
                pending = []
                flush()
        pending = []
        flush()
    return out


#: Highest share of a token's window-wide occurrences that may be lower-case before it is treated
#: as a common noun rather than a name. "Markets rallied" and "markets rallied" both occur, so
#: `markets` fails; "Ebola" is never written lower-case, so it passes.
SINGLETON_MAX_LOWER_SHARE = 0.2
#: Shortest single-word name kept. Below four characters a capitalised token is far more often an
#: initialism of something already tagged, a ticker, or a headline artefact than a subject.
SINGLETON_MIN_LEN = 4

_CAP_TOKEN_RE = re.compile(r"\b([^\W\d_][\w'’\-]*)", re.UNICODE)


def _case_profile(stories: list) -> set:
    """Tokens the WINDOW writes as names: capitalised almost everywhere they appear.

    The problem this solves is that a headline capitalises its first word by position, so
    "Markets rallied…" and "Ebola spreads…" are indistinguishable inside one story — and the
    corroboration rule cannot separate them either, because every outlet covering the story
    writes the same word first. Across the whole window they separate cleanly: a common noun
    turns up lower-case in somebody's sentence and a name does not. Evidence from the catalog
    rather than a word list, which is the only kind of answer that survives a new subject."""
    upper: dict = {}
    lower: dict = {}
    for story in stories:
        for raw in _CAP_TOKEN_RE.findall(_story_text(story)):
            token = raw.lower().strip("'’-")
            if len(token) < SINGLETON_MIN_LEN:
                continue
            if raw[:1].isupper():
                upper[token] = upper.get(token, 0) + 1
            else:
                lower[token] = lower.get(token, 0) + 1
    out = set()
    for token, ups in upper.items():
        total = ups + lower.get(token, 0)
        if total and (lower.get(token, 0) / total) <= SINGLETON_MAX_LOWER_SHARE:
            out.add(token)
    return out


def singleton_votes(members: list, names: set, *, noise) -> dict:
    """``name -> member votes`` for ONE-WORD names — the "Ebola" case.

    ``entity_spans`` requires two capitalised words, deliberately: a lone capitalised word is too
    weak a signal to move an article between stories, which is what that extractor feeds. Nothing
    here can move an article, so the same word is admissible on weaker evidence — but only when
    every other guard still holds: the window says it is written as a name (``names``, from
    :func:`_case_profile`), it is not a noise identity, and >= 2 of the story's own members carry
    it. Those three together are what keep this from being a capitalisation scraper."""
    votes: dict = {}
    for m in members:
        seen = set()
        for raw in _CAP_TOKEN_RE.findall(m.get("headline") or ""):
            # Possessive stripped HERE and not only in the phrase reader's normaliser, which is
            # the whole of the "Parton's" defect: that rail entry came down this path, where a
            # trailing `strip("'’-")` cannot reach an apostrophe that has an "s" after it.
            token = _POSSESSIVE.sub("", raw.lower()).strip("'’-.")
            if (raw[:1].isupper() and token in names and not noise(token)
                    # Both lists, not just `_LEADS`: a sentence opener is one way a word gets
                    # capitalised without being a name, and a preposition mid-headline is another.
                    and token not in entity_spans._LEADS
                    and token not in entity_spans._FUNCTION_WORDS
                    and well_formed(token)):
                seen.add(token)
        for token in seen:
            votes[token] = votes.get(token, 0) + 1
    return votes


def _canonicalise(votes: dict, canon: list) -> dict:
    """Fold fragment names onto the fullest form the story's own text supports.

    THE NORMALISATION STEP, and it is needed because three extractors read overlapping text and
    disagree about where a name ends. A story whose headlines say "Congo province" and whose dek
    says "Democratic Republic of the Congo" produces "congo", "democratic republic" AND the full
    phrase — one country, three tags, ranked against each other as though they were rivals.

    ``canon`` is the phrase list read from everything the story says about itself, so the fold is
    evidence-based rather than a synonym table: a fragment is rewritten only when THIS story
    somewhere spells the longer name out. Votes transfer by max, not sum — the same member saying
    "Congo" and "Democratic Republic of the Congo" is one member, not two."""
    if not canon:
        return votes
    by_words = [(frozenset(c.split()), c) for c in canon]
    out: dict = {}
    for name, v in votes.items():
        words = frozenset(name.split())
        target = name
        best = len(words)
        for cwords, cname in by_words:
            # Onto the LONGEST well-formed phrase the story's own text spells out, and no further
            # condition. A vote test was tried here — fold only onto a container at least as
            # corroborated as the fragment — to stop "Dolly Parton" being absorbed by "Dolly Parton
            # Imagination Library" when a summary mentions the charity once. It works for that and
            # breaks the case the fold exists for: a story whose headlines say "Congo province"
            # while only its dek spells the country out has NO votes on the full name, so the
            # country came back as "Congo" AND "Democratic Republic" AND the full phrase — one
            # country, three rival tags, measured on the fixture.
            #
            # The trade is taken knowingly and in the direction asked for: keep the most specific
            # name. What it costs is a story whose main subject is a short name that happens to be
            # contained in a longer one mentioned in passing. `well_formed` is what keeps the
            # target from being a phrase the shape rules would reject — folding into one of those
            # would lose the entity entirely, which is worse than either outcome here.
            if words < cwords and len(cwords) > best and well_formed(cname):
                target, best = cname, len(cwords)
        out[target] = max(out.get(target, 0), v)
    return out


def _score(votes: int, members: int, df: int, total_stories: int) -> float:
    """Corroboration x specificity.

    Corroboration is the share of the story's members that carry the name, so a name every outlet
    used outranks one two of twenty did. Specificity is ``log(1 + N/df)`` normalised by
    ``log(1 + N)`` — the clusterer's own IDF shape, scaled into 0..1 so the product is readable as
    a confidence and comparable between builds of different sizes."""
    if members <= 0 or votes <= 0:
        return 0.0
    corroboration = min(1.0, votes / members)
    df = max(1, df)
    specificity = math.log(1 + total_stories / df) / math.log(1 + max(2, total_stories))
    return round(corroboration * specificity, 4)


def _sorted(tags: list) -> list:
    """Best first: score, then direct before inherited before the category, then name — so the
    order is total and a rebuild cannot reshuffle equal tags."""
    return sorted(tags, key=lambda t: (-t["score"], _SOURCE_RANK.get(t["source"], 9), t["name"]))


def extract_tags(stories: list, entities: dict, *, noise, cap: int = TAG_CAP) -> dict:
    """``story id -> ranked direct tags`` for a whole build.

    A BUILD at a time, not a story at a time, because two of the three rules are properties of
    the window: story frequency decides both what is background and how specific a name is, and
    neither can be known from one story. This is also why tags are computed where the build is
    and not on the request path.

    Each tag is ``{name, label, source, score, members}`` — the stored name for joins, the display
    label for a client that should not have to re-derive it, and the member count as the evidence
    behind the score."""
    names = _case_profile(stories)
    votes_by_story = {}
    df: dict = {}
    for story in stories:
        members = story.get("coverage") or []
        votes = direct_votes(members, entities, noise=noise)
        for name, v in singleton_votes(members, names, noise=noise).items():
            votes[name] = max(votes.get(name, 0), v)
        votes = _canonicalise(votes, phrases(_story_text(story)))
        # ONE gate on every name, whatever produced it. Three extractors and a stored side table
        # feed this dict, and a shape rule enforced in three of the four places is a rule that
        # holds until the fourth is edited — which is how a headline reached the rail.
        kept = {n: v for n, v in votes.items() if v >= TAG_MIN_MEMBERS and well_formed(n)}
        # A longer name absorbs any shorter one built from its own words: "Congo" and "Democratic
        # Republic" beside "Democratic Republic of the Congo" are the same tag three times, and
        # the full one is what the reader means. Three extractors read the same headline, so this
        # is the normalisation step that makes them one answer rather than three. Dropped here
        # rather than at render, so the dedup holds for every consumer of the projection.
        # A shorter name is absorbed by a longer one built from its own words ONLY when it has no
        # independent support — that is, when it never appears anywhere the longer one does not.
        # Vote counts say which: "Congo" and "Democratic Republic of the Congo" carried by the same
        # members are one country written twice, while "Dolly Parton" carried by more members than
        # "Dolly Parton Imagination Library" is a person who also happens to have a charity. The
        # first version absorbed on subset alone and deleted the person.
        words = {n: set(n.split()) for n in kept}
        for name in list(kept):
            if any(other != name and words[name] < words[other] and kept[name] <= kept[other]
                   for other in kept):
                kept.pop(name)
        votes_by_story[story["id"]] = kept
        for name in kept:
            df[name] = df.get(name, 0) + 1

    total = len(stories)
    ceiling = max(TAG_MIN_DF_CEILING, int(total * TAG_MAX_STORY_SHARE))
    out: dict = {}
    for story in stories:
        members = len(story.get("coverage") or []) or 1
        tags = []
        for name, votes in votes_by_story[story["id"]].items():
            if df.get(name, 0) > ceiling:
                continue                       # background name: in too much of the window
            score = _score(votes, members, df.get(name, 1), total)
            if score < TAG_MIN_SCORE:
                continue
            tags.append({"name": name, "label": label_for(name), "source": SOURCE_DIRECT,
                         "score": score, "members": votes})
        topic = (story.get("topic") or "").strip()
        if topic:
            # The shelf, marked as the shelf. Emitted last and scored under every entity so it
            # never displaces evidence, and carried at all because a reader navigating by topic
            # is doing something real.
            tags.append({"name": topic.lower(), "label": topic, "source": SOURCE_TOPIC,
                         "score": TOPIC_TAG_SCORE, "members": members})
        out[story["id"]] = _sorted(tags)[:cap]
    return out


def inherit_tags(stories: list, direct: dict, related: dict, *, cap: int = TAG_CAP) -> dict:
    """``story id -> ranked tags``, direct plus the ones worth inheriting from related stories.

    ``related`` is ``story id -> [(related id, relation score in 0..1)]`` and is supplied by the
    caller, which is what keeps this module free of the similarity measure and separately
    testable. It should already be the STRONG relations — the same set the Similar Stories rail
    shows — because everything below assumes the relation itself has been earned.

    A tag crosses on one of two pieces of evidence, never on the relation alone:

    * **The target says it too.** Every token of the name appears somewhere in this story's own
      title, summary or coverage headlines. It was not a tag here only because no two members
      carried it as an extracted entity — a real gap, since extraction is a heuristic over
      capitalisation and one outlet writing "the Ebola outbreak" mid-sentence loses the span.
    * **Several neighbours agree.** :data:`INHERIT_MIN_AGREEING` related stories carry it
      independently, which is the corroboration rule applied one level up.

    Neither test can be met by a single strong neighbour asserting a single tag, which is exactly
    the blind copy the brief rules out. The score decays by :data:`INHERIT_DECAY` and by the
    relation strength, so an inherited tag ranks below what the story said about itself and a tag
    two hops out would rank below one hop — though there is no second hop: inheritance reads only
    DIRECT tags, so nothing propagates transitively and a tag cannot walk the graph."""
    by_id = {s["id"]: s for s in stories}
    out: dict = {}
    for story in stories:
        sid = story["id"]
        own = list(direct.get(sid) or [])
        have = {t["name"] for t in own}
        text_tokens = _tokens(_story_text(story))

        # What the neighbours offer, and how strongly. Best relation wins for a repeated tag.
        offers: dict = {}
        agreeing: dict = {}
        for rid, strength in related.get(sid) or []:
            if rid == sid or rid not in by_id or strength <= 0:
                continue
            for tag in direct.get(rid) or []:
                if tag["source"] != SOURCE_DIRECT or tag["score"] < INHERIT_MIN_SOURCE_SCORE:
                    continue
                name = tag["name"]
                agreeing[name] = agreeing.get(name, 0) + 1
                best = offers.get(name)
                cand = (tag["score"] * strength, tag)
                if best is None or cand[0] > best[0]:
                    offers[name] = cand

        inherited = []
        for name, (weighted, tag) in offers.items():
            if name in have:
                continue                                   # direct evidence always wins
            corroborated = _tokens(name) <= text_tokens
            if not corroborated and agreeing.get(name, 0) < INHERIT_MIN_AGREEING:
                continue                                   # neither test met — do not copy
            score = round(weighted * INHERIT_DECAY, 4)
            if score < TAG_MIN_SCORE:
                continue
            inherited.append({"name": name, "label": tag["label"], "source": SOURCE_INHERITED,
                              "score": score, "members": tag.get("members", 0)})
        out[sid] = _sorted(own + inherited)[:cap]
    return out


def tag_index(tags_by_story: dict) -> dict:
    """``tag name -> [story ids]``, best-scoring story first — the retrieval side of the same
    projection, so "show me other stories tagged Ebola" is a lookup rather than a scan."""
    index: dict = {}
    for sid, tags in tags_by_story.items():
        for tag in tags:
            index.setdefault(tag["name"], []).append((tag["score"], sid))
    return {name: [sid for _, sid in sorted(rows, key=lambda r: (-r[0], r[1]))]
            for name, rows in index.items()}
