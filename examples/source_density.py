"""source_density.py — M14 Stage 0: is cross-publisher density the constraint, and who supplies it?

**M14 of `docs/SCALE_ROADMAP.md`, the offline half.** Pure: no store, no network, no environment, no
writes — the same contract `source_discovery.py` holds, and for the same reason. Everything here runs
on catalogue rows we already have.

## What this is for

`--unicode-words` rescued 78 articles and cost 149, reaching **3.0%** of the population it was built
for. The conclusion recorded in `docs/M14_LANGUAGE_DENSITY_DESIGN.md`: giving a Korean headline
tokens does not give it a Korean *peer*, and a story needs two **distinct publishers** covering one
event within six days (`min_publishers = 2`). So the question is not "how many articles does this
language have" but "how many of them have a cross-publisher partner".

## The one thing this module must not do

It must not re-implement "same event". Every pair decision goes through
`clustering.pair_admits` — the function extracted from `cluster`'s inner `pair_ok` *"precisely so
there is one definition"*. Token postings are used **only to generate candidates**, which is sound
rather than a shortcut: `MIN_SHARED_TOKENS` is 3, so an admissible pair must co-occur in at least
three postings lists and cannot be missed by walking them. The postings walk is the same
bisect+Counter shape `clustering.cluster` uses, and for the same measured reason.

## The strata are DERIVED, not a language list

`docs/M14_LANGUAGE_DENSITY_DESIGN.md` §3 separates three failure modes, and pooling them is what
made the original peer test fail. Hard-coding "ko, ar, zh are broken" would bake today's corpus into
the code, so the classification is computed from the headlines themselves:

``tokenizer-dead``  most articles yield fewer than `MIN_TITLE_TOKENS` under the SHIPPED tokenizer,
                    so `pair_admits` rejects them before any other test. Density is **unmeasurable**
                    here — a zero is the tokenizer's, not the corpus's.
``fragment``        tokens exist but are mostly *fragments*: strings the ASCII tokenizer produced
                    that are not words of the headline at all (`kündigt` -> `ndigt`). Their pairs
                    match on orthographic debris, so the numbers are untrustworthy in **both**
                    directions — Vietnamese measured 26.4% participation and lost all of it when the
                    tokenizer got more precise.
``healthy``         neither. The only stratum where a participation number means what it says, and
                    therefore the only one the peer hypothesis can be tested on.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from typing import Optional

import clustering

#: Share of a language's articles that must be below `MIN_TITLE_TOKENS` before the language is
#: called tokenizer-dead. A half is not a tuned value — it is the point at which the *typical*
#: article of that language cannot cluster, which is what makes the language's participation number
#: a fact about the tokenizer rather than about the corpus.
DEAD_SHARE = 0.5

#: Share of a language's ASCII tokens that must be fragments before its numbers are called
#: untrustworthy. Same reasoning: above a half, the typical token is debris.
FRAGMENT_SHARE = 0.5

#: Articles a language needs in the window before any of this is worth reporting. Below it the
#: strata classification is noise — one article decides the share.
MIN_LANGUAGE_ARTICLES = 20

STRATA = ("healthy", "fragment", "tokenizer-dead", "too-small")


# --------------------------------------------------------------------------- #
# Script + language identification
# --------------------------------------------------------------------------- #
#: Script ranges, in the order tested. Deliberately coarse: the question this answers is "is the
#: unlabelled quarter of the catalogue hiding a non-Latin corpus", which needs a script, not a
#: language. Anything finer would be a language guess wearing a script's clothes.
_SCRIPTS = (
    ("latin", ((0x0041, 0x024F), (0x1E00, 0x1EFF))),         # incl. Latin Extended Additional (vi)
    ("greek", ((0x0370, 0x03FF),)),
    ("cyrillic", ((0x0400, 0x04FF),)),
    ("hebrew", ((0x0590, 0x05FF),)),
    ("arabic", ((0x0600, 0x06FF), (0x0750, 0x077F))),
    ("devanagari", ((0x0900, 0x097F),)),
    ("bengali", ((0x0980, 0x09FF),)),
    ("tamil", ((0x0B80, 0x0BFF),)),
    ("telugu", ((0x0C00, 0x0C7F),)),
    ("thai", ((0x0E00, 0x0E7F),)),
    ("hangul", ((0xAC00, 0xD7AF), (0x1100, 0x11FF))),
    ("kana", ((0x3040, 0x30FF),)),
    ("han", ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF))),
)


#: Kana and Hangul are decided by PRESENCE, ahead of everything else. A Japanese headline is mostly
#: han — ``首相が新しい予算案`` is 6 han to 3 kana — so a plurality vote calls it Chinese. Kana appear
#: in no other language, so one is proof; the same holds for Hangul, which Chinese and Japanese never
#: carry.
_MARKER_SCRIPTS = ("kana", "hangul")


def script_of(text: "str | None") -> str:
    """The dominant script of ``text``, or ``""`` when it carries no letters.

    **Latin never wins a mixed headline, and that is deliberate.** A real production headline from a
    Greek outlet reads ``Champions League: Πέρασαν στη league phase Φενέρμπαχτσε…`` — 11 Latin
    characters to 10 Greek, so a plurality vote calls it Latin and a language strategy would count it
    as covered when it is not. Latin-script languages borrow brand names constantly; the reverse is
    rare, so **any non-Latin script present decides the headline**, with the plurality breaking ties
    between two non-Latin scripts.

    The error this direction admits is an English headline quoting one Arabic or Greek word being
    counted as non-Latin. That inflates the estimate of the hidden non-Latin corpus, which is the
    **safe** direction for the only decision this feeds: whether the unlabelled quarter can be
    ignored. Over-counting there costs a measurement; under-counting would license targeting a
    corpus we cannot see."""
    counts: Counter = Counter()
    for ch in text or "":
        o = ord(ch)
        for name, ranges in _SCRIPTS:
            if any(lo <= o <= hi for lo, hi in ranges):
                counts[name] += 1
                break
    for marker in _MARKER_SCRIPTS:
        if counts.get(marker):
            return marker
    non_latin = Counter({k: v for k, v in counts.items() if k != "latin"})
    if non_latin:
        return non_latin.most_common(1)[0][0]
    return counts.most_common(1)[0][0] if counts else ""


def looks_english(title: "str | None") -> bool:
    """**A heuristic, and named as one.** Whether a headline carries English function words.

    Script identification cannot separate English from German — both are Latin — so the unlabelled
    bucket needs one more bit to answer "is it mostly English?". This uses `clustering._STOPWORDS`,
    which is an English stop-list, and asks for **two** hits: one is a coincidence ("A Coruña"),
    two is a sentence.

    It is deliberately not a language identifier. It answers exactly one question — how much of the
    unlabelled quarter is plausibly English — and a wrong answer on a single headline does not move
    a share computed over thousands."""
    words = [w for w in (title or "").lower().replace("'", " ").split() if w]
    return sum(1 for w in words if w.strip(".,:;!?—–-") in clustering._STOPWORDS) >= 2


def fragment_rate(title: "str | None") -> "tuple[int, int]":
    """``(fragments, ascii_tokens)`` for one headline.

    A **fragment** is a token the shipped ASCII tokenizer produced that is not a token of the
    headline under real word segmentation — ``kündigt`` yields ``ndigt``, which is not a word of
    anything. That is the exact defect that made Vietnamese cluster on orthographic debris, and
    stating it as "ASCII token absent from the Unicode token set" makes it computable without a
    dictionary or a language list."""
    ascii_toks = clustering.title_tokens(title or "")
    if not ascii_toks:
        return 0, 0
    real = clustering.title_tokens(title or "", unicode_words=True)
    return sum(1 for t in ascii_toks if t not in real), len(ascii_toks)


# --------------------------------------------------------------------------- #
# Cross-publisher pairs — the raw material a story is made of
# --------------------------------------------------------------------------- #
def _pub(row) -> str:
    return (row.get("publisher") or "").strip().lower()


def _title(row) -> str:
    return row.get("title") or row.get("headline") or ""


def cross_publisher_pairs(rows: list, *, min_shared: int = clustering.MIN_SHARED_TOKENS,
                          uni=False) -> "list[tuple[int, int]]":
    """Index pairs ``(i, j)``, ``i < j``, that `pair_admits` accepts and whose publishers DIFFER.

    This is the quantity `min_publishers = 2` turns into stories, and it is the whole of M14's
    measurement. Two properties are load-bearing:

    * **the decision is `clustering.pair_admits`**, never a local re-derivation. Postings only
      propose;
    * **the postings walk cannot miss a pair.** An admissible pair shares at least ``min_shared``
      tokens, so it appears together in at least that many postings lists; walking every token's
      list therefore proposes every admissible pair. The `bisect_right` skip and the
      `Counter.update` tally are the shapes `clustering.cluster` measured at 49% of a build.

    ``uni`` threads the tokenizer mode so a stratum's density can be measured under the candidate
    tokenizer as well as the shipped one — which is the only way to get an honest number for a
    ``fragment`` language."""
    toks = [clustering.title_tokens(_title(r), unicode_words=uni) for r in rows]
    times = [clustering.parse_time(r.get("publishedAt")) for r in rows]
    pubs = [_pub(r) for r in rows]
    floor = max(1, clustering.MIN_TITLE_TOKENS)

    postings: dict = defaultdict(list)
    for i, t in enumerate(toks):
        if len(t) >= floor:
            for tok in t:
                postings[tok].append(i)

    out = []
    for i, ti in enumerate(toks):
        if len(ti) < floor:
            continue
        shared: Counter = Counter()
        for tok in ti:
            plist = postings[tok]
            tail = plist[bisect_right(plist, i):]
            if tail:
                shared.update(tail)
        for j, overlap in shared.items():
            if overlap < min_shared or pubs[i] == pubs[j] or not pubs[i] or not pubs[j]:
                continue
            if clustering.pair_admits(ti, toks[j], times[i], times[j], min_shared=min_shared):
                out.append((i, j))
    return out


def covered_articles(rows: list, pairs: list, publishers: "set | None" = None) -> set:
    """Indices with at least one cross-publisher partner, restricted to ``publishers`` when given.

    **A lower bound on story membership, and it must be read as one.** A pair is necessary for a
    story and not sufficient: the cluster still has to clear `min_articles`, `min_publishers`, the
    link quorum, the repair pass and the merge gates. What this measures is whether the *raw
    material* exists at all — which is precisely the thing a language with four publishers does not
    have, and the thing an admission campaign can change."""
    out = set()
    for i, j in pairs:
        if publishers is None or (_pub(rows[i]) in publishers and _pub(rows[j]) in publishers):
            out.add(i)
            out.add(j)
    return out


# --------------------------------------------------------------------------- #
# Strata
# --------------------------------------------------------------------------- #
def language_profile(rows: list) -> dict:
    """Per-language stratum and density, keyed by the row's ``language`` (``"?"`` when absent).

    Reports ``coCoverage`` — the share of a language's articles holding a cross-publisher partner —
    alongside the stratum, because the number is only meaningful for ``healthy``. For the other two
    it is reported and explicitly not to be compared: a ``tokenizer-dead`` zero is the tokenizer's,
    and a ``fragment`` figure counts matches on debris."""
    by_lang: dict = defaultdict(list)
    for r in rows:
        by_lang[(r.get("language") or "?").strip() or "?"].append(r)

    out = {}
    for lang, group in by_lang.items():
        dead = sum(1 for r in group
                   if len(clustering.title_tokens(_title(r))) < clustering.MIN_TITLE_TOKENS)
        frags = tot = 0
        for r in group:
            f, n = fragment_rate(_title(r))
            frags += f
            tot += n
        dead_share = dead / len(group)
        frag_share = frags / tot if tot else 0.0
        # ORDER MATTERS, and it is the conservative direction. A language can be both — Vietnamese
        # writes most words with diacritics, so many of its headlines yield no ASCII token at all
        # (dead) while the rest yield fragments. Testing `dead` first labels such a language
        # UNMEASURABLE rather than merely untrustworthy, which is the honest answer: density cannot
        # be measured on a corpus whose typical article cannot enter a cluster.
        if len(group) < MIN_LANGUAGE_ARTICLES:
            stratum = "too-small"
        elif dead_share > DEAD_SHARE:
            stratum = "tokenizer-dead"
        elif frag_share > FRAGMENT_SHARE:
            stratum = "fragment"
        else:
            stratum = "healthy"
        pairs = cross_publisher_pairs(group)
        covered = covered_articles(group, pairs)
        # `coCoverage` SATURATES, and reporting it alone would be a measure that stops
        # discriminating exactly where the question gets interesting: it asks only whether an
        # article has AT LEAST ONE cross-publisher partner, so a language with two publishers that
        # happen to overlap reads 100% — the same as one with fifty. It is the right number for
        # "can this language form a story at all" and the wrong one for "how dense is it".
        #
        # `meanPartners` is the depth: the average number of DISTINCT other publishers holding a
        # partner for an article. Two publishers cap it at 1.0 however complete their overlap, so it
        # keeps rising with peer count where the share cannot.
        _, partners = partner_index(group, pairs)
        mean_partners = sum(len(p) for p in partners) / len(group)
        out[lang] = {
            "language": lang, "articles": len(group), "stratum": stratum,
            "publishers": len({_pub(r) for r in group if _pub(r)}),
            "deadShare": round(dead_share, 4), "fragmentShare": round(frag_share, 4),
            "pairs": len(pairs), "covered": len(covered),
            "coCoverage": round(len(covered) / len(group), 4),
            "meanPartners": round(mean_partners, 4),
            "scripts": Counter(script_of(_title(r)) for r in group).most_common(2),
        }
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["articles"]))


# --------------------------------------------------------------------------- #
# The ranking: marginal cross-publisher coverage, greedily
# --------------------------------------------------------------------------- #
def marginal_gain(rows: list, pairs: list, admitted: set, host_publishers: set) -> int:
    """Articles that gain a cross-publisher partner when ``host_publishers`` join ``admitted``.

    **The reference implementation: obvious, and quadratic.** :func:`greedy_publishers` uses an
    indexed form that is far faster and far less obviously correct, and
    `tests/test_source_density.py` pins the two against each other over random inputs. That is the
    same discipline `clustering.pair_admits` records — one definition, and a fast path that has to
    prove it agrees with the slow one.

    Counts **both** directions, which is the point: a candidate's value includes the incumbent
    articles it partners, not only its own that find a partner. A publisher covering an event no one
    else covers scores 0 however many articles it files — which is why volume is the wrong order.
    `sportskeeda.com` is the largest candidate in the M11 pool at 5,089 articles."""
    before = covered_articles(rows, pairs, admitted)
    after = covered_articles(rows, pairs, admitted | host_publishers)
    return len(after) - len(before)


def partner_index(rows: list, pairs: list) -> "tuple[dict, list]":
    """``(by_publisher, partners)`` — the index the greedy runs on.

    ``by_publisher[p]``  article indices published by ``p``.
    ``partners[i]``      the set of OTHER publishers holding an admissible partner for article ``i``.

    With these, "is article ``i`` covered by publisher set ``S``" is ``pub(i) in S and partners[i] &
    S``, so a marginal gain costs a walk of one publisher's articles plus the still-uncovered ones
    rather than a walk of every pair. Pairs are cross-publisher by construction, so ``pub(i)`` never
    appears in ``partners[i]`` and the two conditions cannot alias."""
    by_publisher: dict = defaultdict(list)
    for i, r in enumerate(rows):
        if _pub(r):
            by_publisher[_pub(r)].append(i)
    partners: list = [set() for _ in rows]
    for i, j in pairs:
        partners[i].add(_pub(rows[j]))
        partners[j].add(_pub(rows[i]))
    return dict(by_publisher), partners


def greedy_publishers(rows: list, *, seed: "set | None" = None, k: int = 10,
                      pairs: "list | None" = None) -> list:
    """Publishers in marginal-gain order: ``[{publisher, gain, cumulative}]``.

    Greedy on a **submodular** coverage objective — a publisher's value falls as its peers are
    admitted, which is the correct shape (the second local paper covering a council is worth more
    than the twentieth) and is what makes greedy the standard, defensible rule rather than an
    invented threshold.

    Ties break on publisher name so the order is deterministic across runs; a selection that moved
    between runs could not be audited against the campaign that used it.

    Stops early when the best remaining publisher adds **nothing** — a corpus can run out of
    cross-publisher partners long before it runs out of publishers, and reporting a tail of
    zero-gain admissions as a ranking would suggest value that is not there.
    """
    pairs = cross_publisher_pairs(rows) if pairs is None else pairs
    by_pub, partners = partner_index(rows, pairs)
    admitted = set(seed or set())
    pool = set(by_pub) - admitted
    # Articles of `admitted` that do not yet have a partner inside `admitted`. Maintained
    # incrementally so each round costs a walk of this set rather than of every pair.
    uncovered = {i for p in admitted for i in by_pub.get(p, ())
                 if not (partners[i] & admitted)}
    covered_n = sum(len(by_pub.get(p, ())) for p in admitted) - len(uncovered)

    def _gain(pub: str) -> int:
        own = sum(1 for i in by_pub[pub] if partners[i] & admitted)
        theirs = sum(1 for i in uncovered if pub in partners[i])
        return own + theirs

    def _take(pub: str, gain: int) -> None:
        nonlocal covered_n, uncovered
        admitted.add(pub)
        pool.discard(pub)
        covered_n += gain
        uncovered = {i for i in uncovered if pub not in partners[i]}
        uncovered |= {i for i in by_pub[pub] if not (partners[i] & admitted)}
        out.append({"publisher": pub, "gain": gain, "cumulative": covered_n})

    out: list = []
    while len(out) < k and pool:
        best, best_gain = None, 0
        for pub in sorted(pool):
            g = _gain(pub)
            if g > best_gain:
                best, best_gain = pub, g
        if best is not None:
            _take(best, best_gain)
            continue

        # ---- the bootstrap, and it is not an edge case -------------------------------------
        #
        # A cross-publisher pair needs TWO publishers, so from a cold start every SINGLETON scores
        # zero and a pure greedy returns nothing at all — on a corpus that may be dense. The same
        # stall recurs mid-run whenever the remaining publishers partner only each other and not
        # what is already admitted.
        #
        # So when the singleton maximum is zero, take the best PAIR instead. Only publishers that
        # have at least one partner are considered, which keeps this O(pairs) rather than O(P^2),
        # and the two are reported as separate steps — the first carrying the joint gain and the
        # second zero — because a ranking that hid one of them inside the other would misstate what
        # admitting only the first would buy: nothing.
        partnered = {p: set() for p in pool}
        for i, ps in enumerate(partners):
            own = _pub(rows[i])
            if own in partnered:
                partnered[own] |= (ps & pool)
        best_pair, best_pair_gain = None, 0
        for a in sorted(p for p, ps in partnered.items() if ps):
            for b in sorted(partnered[a]):
                if b <= a:
                    continue
                g = len(covered_articles(rows, pairs, admitted | {a, b})) - covered_n
                if g > best_pair_gain:
                    best_pair, best_pair_gain = (a, b), g
        if best_pair is None:
            break                       # genuinely nothing left that covers anything
        a, b = best_pair
        _take(a, 0)
        if len(out) < k:
            _take(b, best_pair_gain)
    return out
