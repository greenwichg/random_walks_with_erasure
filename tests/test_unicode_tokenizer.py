"""The Unicode-word tokenizer candidate — the audit's instrument, shipped OFF.

## The defect, which is not a tuning question

`clustering.title_tokens` matches ``[a-z0-9]+``. That yields **zero tokens** for a Korean, Arabic,
Chinese, Japanese, Russian, Tamil or Hindi headline, and `clustering.pair_admits` rejects anything
below `MIN_TITLE_TOKENS` *before any other test*:

    floor = max(1, min_tokens)
    if len(tx) < floor or len(ty) < floor or len(tx & ty) < min_shared:
        return False

So those articles **cannot join a story under any configuration**. There is no threshold that
admits them, and no other route into a cluster — the `evidence` hook is an additional veto, not an
alternative path. Production 2026-08-27, `audit_source_cohort.py` over the live window:

    ko  4 outlets   118 articles   0 in-story    0%
    ar  6 outlets    98 articles   0 in-story    0%
    ru  5 outlets    90 articles   1 in-story    1%
    zh  4 outlets    67 articles   0 in-story    0%
    ja  3 outlets    52 articles   0 in-story    0%
    ta  1 outlet     47 articles   0 in-story    0%
    hi  2 outlets    44 articles   2 in-story    5%
    --                                    --
        23 outlets  516 articles   3 in-story  0.6%   against 29% for English

The tool's own header calls this "a question about the tokenizer, not a finding". It is now both.

## Why this file exists rather than a fix

`title_tokens` decides the story partition for the entire product, and the last tokenizer candidate
— `hyphen_compounds` — was measured against the live catalogue and **rejected**: 121 clusters split,
2.6% of covered articles dropped, story count fell. So this lands the same way that one did: a
candidate plus an instrument, defaulted off, ships nothing, and
`audit_clustering_change.py --unicode-words` is what decides.

**The first test below is the load-bearing one.** With the flag off the token set must be
byte-identical to the shipped expression, or turning the instrument on is not what is being measured.

## One pass-through is wired but NOT pinned

`derived_boilerplate` receives the flag and no test fails when that is removed. Its observable is a
corpus-derived high-document-frequency token set, and a four-article fixture cannot exercise one
meaningfully — every token in it has document frequency 4, so the "derived boilerplate" of the
fixture is the whole vocabulary. Writing a test whose assertion depends on that would be measuring
the fixture. Recorded here rather than left for someone to discover as an untested path.
"""
from __future__ import annotations

import pathlib
import random
import re
import string
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "examples"))

import clustering  # noqa: E402
import store as store_mod  # noqa: E402
import story_service  # noqa: E402
from clustering import title_tokens as T  # noqa: E402

#: One sentence per script, all saying roughly "the leader announced the new budget plan".
HEADLINES = {
    "en": "Mayor announces new budget plan for the city",
    "de": "Bundeskanzler kündigt neues Haushaltsgesetz an",
    "tr": "Cumhurbaşkanı yeni bütçe planını açıkladı",
    "vi": "Thủ tướng công bố kế hoạch ngân sách mới",
    "ru": "Президент объявил о новом бюджетном плане",
    "ar": "أعلن الرئيس عن خطة الميزانية الجديدة",
    "ko": "대통령이 새로운 예산안을 발표했다",
    "ta": "அதிபர் புதிய பட்ஜெட் திட்டத்தை அறிவித்தார்",
    "hi": "राष्ट्रपति ने नई बजट योजना की घोषणा की",
    "zh": "总统宣布新的预算计划",
    "ja": "首相が新しい予算案を発表した",
    "th": "นายกรัฐมนตรีประกาศแผนงบประมาณใหม่",
}

#: The scripts that produce NOTHING today. Every one of these is a language with major newsrooms.
DEAD_TODAY = ("ru", "ar", "ko", "ta", "hi", "zh", "ja", "th")


def _shipped(title: str) -> frozenset:
    """The tokenizer exactly as it shipped, re-implemented here on purpose.

    A byte-identity test that called the function under test would prove nothing. This is the
    expression from before the change, so the guard compares against the old behaviour rather than
    against itself."""
    lower = (title or "").lower()
    return frozenset(t for t in re.findall(r"[a-z0-9]+", lower)
                     if len(t) > 2 and not t.isdigit() and t not in clustering._STOPWORDS)


# --------------------------------------------------------------------- the guarantee
@pytest.mark.parametrize("title", list(HEADLINES.values()) + [
    "Erdoğan meets Orbán in Budapest", "X-Men: Days of Future Past re-released",
    "6 Best Laptops Since 2010", "", "   ", "Beyoncé announces world tour dates"])
def test_off_is_byte_identical_to_the_shipped_tokenizer(title):
    """**The load-bearing test.** If the default path moved, every measurement taken with the
    instrument is against a baseline that is not production — the exact defect
    `audit_clustering_change`'s own docstring records having shipped twice."""
    assert T(title) == _shipped(title)


def test_off_is_byte_identical_over_random_input():
    random.seed(20260827)
    alphabet = string.ascii_letters + string.digits + string.punctuation + " " + "áéíöşğüñ中日한"
    for _ in range(500):
        s = "".join(random.choices(alphabet, k=random.randint(0, 60)))
        assert T(s) == _shipped(s), repr(s)


def test_the_flag_defaults_off_everywhere(monkeypatch):
    monkeypatch.delenv("RWE_CLUSTER_UNICODE_WORDS", raising=False)
    assert story_service.unicode_words() is False
    monkeypatch.setenv("RWE_CLUSTER_UNICODE_WORDS", "garbage")
    assert story_service.unicode_words() is False, "junk must fall back to off, never to a guess"
    monkeypatch.setenv("RWE_CLUSTER_UNICODE_WORDS", "1")
    assert story_service.unicode_words() is True


# --------------------------------------------------------------------- the defect
@pytest.mark.parametrize("lang", DEAD_TODAY)
def test_these_scripts_cannot_join_a_story_today(lang):
    """Not "cluster poorly" — cannot cluster. Asserted through `pair_admits`, the gate itself,
    against an identical headline: if a headline cannot cluster with ITSELF the exclusion is
    structural and no threshold reaches it."""
    toks = T(HEADLINES[lang])
    assert len(toks) < clustering.MIN_TITLE_TOKENS
    assert clustering.pair_admits(toks, toks, None, None) is False


@pytest.mark.parametrize("lang", DEAD_TODAY)
def test_the_candidate_gives_them_enough_tokens_to_cluster(lang):
    toks = T(HEADLINES[lang], unicode_words=True)
    assert len(toks) >= clustering.MIN_TITLE_TOKENS, (lang, sorted(toks))
    assert clustering.pair_admits(toks, toks, None, None) is True


def test_two_outlets_on_one_korean_event_now_cluster():
    """The product question, not the tokenizer question: can two newsrooms covering one event be
    recognised as covering one event?"""
    a = T("대통령이 새로운 예산안을 발표했다", unicode_words=True)
    b = T("대통령이 예산안을 발표 새로운 계획", unicode_words=True)
    assert len(a & b) >= clustering.MIN_SHARED_TOKENS
    assert clustering.pair_admits(a, b, None, None) is True


def test_unrelated_headlines_in_the_same_script_still_do_not_cluster():
    """The check that stops this reading as "it makes everything match". Bigrams are a coarse
    signal, and a candidate that clustered any two Chinese headlines would be worse than the
    exclusion it replaces."""
    a = T("总统宣布新的预算计划", unicode_words=True)
    b = T("科学家发现深海新物种", unicode_words=True)
    assert clustering.pair_admits(a, b, None, None) is False, sorted(a & b)


# --------------------------------------------------------------------- the two mechanisms
def test_hangul_is_word_split_not_bigrammed():
    """Korean uses spaces, so it segments like any other script. Bigramming it would replace four
    real words with eleven syllable fragments and make Korean headlines match on coincidence.
    Grouping "non-Latin" into one bucket is the error `_UNSEGMENTED` exists to avoid — the question
    is whether a script has word separators, not what it looks like."""
    toks = T("대통령이 새로운 예산안을 발표했다", unicode_words=True)
    assert toks == {"대통령이", "새로운", "예산안을", "발표했다"}


@pytest.mark.parametrize("lang,expected_kind", [("zh", "bigram"), ("ja", "bigram"),
                                                ("th", "bigram"), ("ko", "word"),
                                                ("ar", "word"), ("ru", "word")])
def test_each_script_takes_the_treatment_its_orthography_calls_for(lang, expected_kind):
    toks = T(HEADLINES[lang], unicode_words=True)
    longest = max(len(t) for t in toks)
    if expected_kind == "bigram":
        assert longest <= 2, f"{lang} should be bigrammed, got {sorted(toks)[:5]}"
    else:
        assert longest > 2, f"{lang} should be word-split, got {sorted(toks)[:5]}"


def test_abugida_vowel_signs_are_kept_inside_the_word():
    """`\\w` excludes categories Mn and Mc, so without `_MARKS` the Tamil word ``அதிபர்`` splits at
    U+0BBF and U+0BCD into two-character fragments that the length floor then drops.

    Measured while building this: `\\w+` alone left Tamil and Hindi at zero usable tokens — the
    candidate would have looked like it fixed "non-Latin scripts" while leaving two of the largest
    ones exactly as broken as before."""
    assert T("அதிபர் புதிய பட்ஜெட்", unicode_words=True) == {"அதிபர்", "புதிய", "பட்ஜெட்"}
    assert "राष्ट्रपति" in T("राष्ट्रपति ने नई बजट योजना", unicode_words=True)


def test_a_latin_word_inside_an_unsegmented_run_survives_whole():
    """A Japanese headline routinely carries a Latin brand name with no space around it, so
    ``iPhone17発表`` is one word match. Bigramming the whole blob would destroy the Latin word."""
    toks = T("iPhone17発表 Apple が新型を公開", unicode_words=True)
    assert "iphone17" in toks and "apple" in toks
    assert any(len(t) == 2 and clustering._unsegmented(t[0]) for t in toks)


def test_accented_latin_stops_fragmenting():
    """Not only a non-Latin problem. ``kündigt`` currently becomes ``ndigt`` and
    ``cumhurbaşkanı`` becomes ``cumhurba``: real words replaced by fragments that match nothing a
    differently-spelled outlet produces."""
    assert "ndigt" in T("Bundeskanzler kündigt neues Gesetz")
    assert "kündigt" in T("Bundeskanzler kündigt neues Gesetz", unicode_words=True)
    assert "cumhurbaşkanı" in T(HEADLINES["tr"], unicode_words=True)


def test_the_candidate_does_NOT_fold_diacritics():
    """Stated as a test so the limit is not mistaken for an oversight.

    ``Erdoğan``/``Erdogan`` remain different tokens, so two ENGLISH headlines about one event —
    one keeping the diacritics, one not — still fail to cluster. Folding is a separate candidate
    with a separate risk profile (it merges Turkish ``ı``/``i`` and German ``ö``/``o``), and pairing
    the two would make one measurement unable to attribute either result."""
    a = T("Erdoğan meets Orbán in Budapest", unicode_words=True)
    b = T("Erdogan meets Orban in Budapest", unicode_words=True)
    assert "erdoğan" in a and "erdogan" in b
    assert clustering.pair_admits(a, b, None, None) is False


# --------------------------------------------------------------------- the plumbing
def test_the_build_resolves_the_flag_once_and_threads_it(monkeypatch):
    """`article_tokens` is the one place tokens are made — "so the primary build, the repair
    re-split and the audit cannot drift into scoring different things". The flag has to reach all
    three, and it is resolved ONCE per build rather than per article: reading the environment inside
    a per-row loop is the cost `corpus.tier_resolver` exists to document."""
    a = {"headline": "대통령이 새로운 예산안을 발표했다"}
    assert story_service.article_tokens(a) == frozenset()
    assert len(story_service.article_tokens(a, 0, False, True)) == 4

    reads = []
    real = story_service.unicode_words
    monkeypatch.setattr(story_service, "unicode_words", lambda: (reads.append(1), real())[1])
    story_service.build_stories(_korean_rows())
    assert len(reads) == 1, f"the environment was read {len(reads)} times for one build"


def _korean_rows(n: int = 4) -> list:
    """One Korean event, covered by `n` distinct outlets with varied wording.

    Built through a real `Store` and `story_service._fetch`, because `build_stories` reads fields a
    hand-written dict does not carry — a hand-built fixture produced zero stories for an ENGLISH
    control of the same shape, which is how this was caught rather than mistaken for the defect
    under test.

    Distinct publishers because a story needs `min_publishers`; varied wording because identical
    headlines would cluster on a degenerate exact match rather than on the token overlap this is
    about."""
    from datetime import datetime, timedelta, timezone
    tails = ["발표했다", "공개했다", "설명했다", "확정했다", "제시했다", "밝혔다"]
    st = store_mod.Store("sqlite://")
    now = datetime.now(timezone.utc)
    for i in range(n):
        cu = f"https://k{i}.example/a"
        st.upsert_feed_article(
            canonical_url=cu, url=cu, publisher=f"k{i}.example", source_publisher=f"k{i}.example",
            title=f"대통령이 새로운 예산안을 {tails[i % len(tails)]}", description="context",
            body=None, published_at=(now - timedelta(hours=i)).isoformat(), source_feed="feed://x",
            scored={"article_id": cu, "outlet": f"k{i}.example", "category": "Politics",
                    "lean": 0.0, "title": "t"})
    return story_service._fetch(st)


def test_the_flag_actually_reaches_the_CLUSTERER_not_just_the_config(monkeypatch):
    """**The gap a mutation found.** Every other test here exercises `title_tokens` directly, so
    dropping `uni_on` from the `clustering.cluster` call in `build_stories` left all fifty of them
    green: the instrument would have reported healthily while changing nothing — this repository's
    signature defect, in the tests written to catch it.

    So this asserts the product outcome. Eight Korean outlets on one event form NO story today and
    one story with the candidate on."""
    rows = _korean_rows()
    assert story_service.build_stories(rows) == [], \
        "eight outlets on one Korean event already cluster — the premise of this file is wrong"

    stories = story_service.build_stories(rows, uni=True)
    assert len(stories) == 1, f"the flag did not reach the clusterer: {len(stories)} stories"
    assert len(stories[0]["coverage"]) == len(rows)


def test_the_flag_reaches_the_EVENT_IDENTITY_closure_too(monkeypatch):
    """A second gap a mutation found: `_event_identity_closure` builds its OWN token sets to decide
    which edges are ambiguous enough to ask the semantic judge about.

    If the flag stops here, the veto scores every non-Latin pair at similarity 0 while the build
    scores them on real tokens — the two disagreeing about "the signal that admitted the pair",
    which is the exact drift `article_tokens`' docstring exists to prevent ("so the primary build,
    the repair re-split and the audit cannot drift into scoring different things").

    The assertion runs in the direction that actually separates the two, which took two attempts to
    find. Counting in-band edges does not: with the flag lost the closure scores every pair at
    similarity 0, which is *below* any band ceiling, so edges get banded either way and a
    "band is non-empty" test passes on the broken build.

    The fixture's pairs score **0.6**, above the shipped 0.5 band ceiling, so a closure that sees
    the candidate's tokens bands **nothing**. A closure that lost the flag sees empty token sets,
    scores 0.0, and bands all six pairs. Asserting the story still formed is what keeps the empty
    band from being vacuous — otherwise "no pairs at all" would pass it too."""
    rows = _korean_rows()
    band: dict = {}
    stories = story_service.build_stories(rows, uni=True, event_verdicts={}, band_out=band)
    assert len(stories) == 1, "no pairs were scored, so an empty band proves nothing"
    assert band == {}, (
        f"the judge scored {len(band)} pair(s) as ambiguous that the build scored at 0.6 — it is "
        f"not seeing the tokens the build clustered on")


def test_the_flag_reaches_the_TEMPLATE_GATE_closure_too(monkeypatch):
    """Third pass-through, same drift risk. The template gate requires an edge to share at least one
    token outside the boilerplate lexicon; a closure computing empty token sets finds no such token
    and vetoes **every** edge, so the story vanishes rather than merely scoring differently.

    Off by default, which is why the mutation was invisible until the gate is switched on here."""
    monkeypatch.setenv("RWE_CLUSTER_TEMPLATE_GATE", "1")
    stories = story_service.build_stories(_korean_rows(), uni=True)
    assert len(stories) == 1, "the template gate vetoed every edge — it is scoring empty token sets"


def test_the_env_flag_reaches_the_clusterer_too(monkeypatch):
    """`uni=None` is the production path — resolved from the environment rather than passed. A
    parameter that works while the setting does not would be a switch that cannot reach the
    container, which is a defect this series has already had to correct once."""
    rows = _korean_rows()
    monkeypatch.setenv("RWE_CLUSTER_UNICODE_WORDS", "1")
    assert len(story_service.build_stories(rows)) == 1
    monkeypatch.setenv("RWE_CLUSTER_UNICODE_WORDS", "0")
    assert story_service.build_stories(rows) == []
