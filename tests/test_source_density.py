"""M14 Stage 0 — cross-publisher density, and the ranking that is not volume.

`docs/M14_LANGUAGE_DENSITY_DESIGN.md`. The measured premise: `--unicode-words` rescued 78 articles
and cost 149, reaching **3.0%** of the population it was built for. Giving a Korean headline tokens
does not give it a Korean *peer*, and `min_publishers = 2` means a story needs two **distinct
publishers** on one event inside six days.

## The two load-bearing guarantees

**One definition of "same event".** Every pair decision goes through `clustering.pair_admits`.
Postings only *propose* — sound because `MIN_SHARED_TOKENS` is 3, so an admissible pair co-occurs in
at least three postings lists and cannot be missed. `test_postings_finds_every_pair_a_brute_force_scan_does`
pins that against an exhaustive scan.

**One definition of marginal gain.** `marginal_gain` is the obvious quadratic reference;
`greedy_publishers` runs an indexed form that is much faster and much less obviously right.
`test_the_fast_greedy_agrees_with_the_slow_reference` replays every selection through the reference
over randomised corpora. This is the discipline `clustering.pair_admits` itself records — a fast path
has to prove it agrees with the slow one.

## The bug this file's own fixture found

A cross-publisher pair needs **two** publishers, so from a cold start every singleton scores zero and
a pure greedy returns nothing — on a corpus with 121 admissible pairs and 100% co-coverage. The
first version did exactly that. `greedy_publishers` now takes the best *pair* when the singleton
maximum is zero, and reports the two as separate steps with the joint gain on the second, because a
ranking that hid the zero would misstate what admitting only the first buys: nothing.
"""
from __future__ import annotations

import pathlib
import random
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "examples"))

import clustering  # noqa: E402
import source_density as sd  # noqa: E402

_WHEN = "2026-08-27T12:00:00+00:00"


def _row(pub, title, lang="en", when=_WHEN):
    return {"publisher": pub, "title": title, "language": lang, "publishedAt": when}


def _corpus(n, pubs, vocab=40, seed=None, rng=None):
    r = rng or random.Random(seed)
    words = [f"w{i}" for i in range(vocab)]
    return [_row(f"p{r.randint(0, pubs - 1)}.ex",
                 " ".join(r.sample(words, r.randint(4, 7)))) for _ in range(n)]


# --------------------------------------------------------------------- script + language
@pytest.mark.parametrize("text,expected", [
    ("Senate passes the funding bill", "latin"),
    ("Bundeskanzler kündigt neues Gesetz", "latin"),
    ("Thủ tướng công bố kế hoạch", "latin"),        # Latin Extended Additional
    ("Президент объявил о плане", "cyrillic"),
    ("أعلن الرئيس عن خطة", "arabic"),
    ("대통령이 새로운 예산안을", "hangul"),
    ("总统宣布新的预算计划", "han"),
    ("首相が新しい予算案を発表した", "kana"),
    ("அதிபர் புதிய பட்ஜெட்", "tamil"),
    ("राष्ट्रपति ने नई बजट योजना", "devanagari"),
    ("Πέρασαν στη league phase", "greek"),          # 11 Latin chars to 10 Greek — see below
    ("", ""), ("12345 —", ""),
])
def test_script_identification(text, expected):
    assert sd.script_of(text) == expected


def test_latin_never_wins_a_mixed_headline():
    """A real production headline: ``Champions League: Πέρασαν στη league phase Φενέρμπαχτσε…`` is
    11 Latin characters to 10 Greek, so a plurality vote calls a Greek outlet's headline Latin — and
    a language strategy would then count it as covered when it is not.

    Latin-script languages borrow brand names constantly and the reverse is rare, so any non-Latin
    script present decides. The error this admits — an English headline quoting one Greek word — is
    the safe direction for the decision it feeds: over-counting the hidden non-Latin corpus costs a
    measurement, under-counting licenses targeting a corpus we cannot see."""
    assert sd.script_of("Champions League: Πέρασαν στη league phase Φενέρμπαχτσε") == "greek"
    assert sd.script_of("Apple iPhone 18 Pro launch event") == "latin"


def test_japanese_is_not_called_chinese():
    """**A plurality vote gets this wrong**, which is why kana is decided by presence.
    ``首相が新しい予算案`` is 6 han characters to 3 kana, so counting calls it Chinese. Kana appear in
    no other language, so one of them settles it — and mislabelling Japanese as Chinese would put two
    languages in one density bucket and make both numbers meaningless."""
    assert sd.script_of("首相が新しい予算案") == "kana"
    assert sd.script_of("总统宣布新的预算计划") == "han", "pure han must stay han"


@pytest.mark.parametrize("title,expected", [
    ("Senate passes the funding bill after debate", True),
    ("Mayor announces a new plan for the city", True),
    ("Bundeskanzler kündigt neues Haushaltsgesetz an", False),
    ("A Coruña", False),                     # one hit is a coincidence, not a sentence
    ("", False),
])
def test_the_english_heuristic_needs_two_function_words(title, expected):
    assert sd.looks_english(title) is expected


def test_a_fragment_is_a_token_that_is_not_a_word():
    """`kündigt` tokenizes to `ndigt` under the shipped ASCII class — a string that is a word of
    nothing. Defining a fragment as "ASCII token absent from the Unicode token set" makes the
    fragment rate computable with no dictionary and no language list, which is what keeps the strata
    derived rather than declared."""
    assert sd.fragment_rate("Mayor announces new budget plan") == (0, 4)
    frags, total = sd.fragment_rate("Cumhurbaşkanı yeni bütçe planını açıkladı")
    assert frags / total > 0.5, (frags, total)
    assert sd.fragment_rate("대통령이 새로운 예산안을") == (0, 0), "no ASCII tokens means no fragments"


# --------------------------------------------------------------------- the pair scan
def test_postings_finds_every_pair_a_brute_force_scan_does():
    """The completeness guarantee. Postings are a candidate generator, and the argument that they
    miss nothing (`MIN_SHARED_TOKENS` = 3 forces co-occurrence in ≥3 lists) is checked rather than
    trusted — against an exhaustive O(n²) scan using the same `pair_admits`."""
    rng = random.Random(4)
    for _ in range(25):
        rows = _corpus(rng.randint(8, 45), rng.randint(2, 5), rng=rng)
        fast = set(sd.cross_publisher_pairs(rows))
        toks = [clustering.title_tokens(r["title"]) for r in rows]
        times = [clustering.parse_time(r["publishedAt"]) for r in rows]
        brute = {(i, j) for i in range(len(rows)) for j in range(i + 1, len(rows))
                 if rows[i]["publisher"] != rows[j]["publisher"]
                 and clustering.pair_admits(toks[i], toks[j], times[i], times[j])}
        assert fast == brute


def test_same_publisher_pairs_are_never_counted():
    """`min_publishers = 2`. An outlet republishing itself creates no story, and counting it would
    make a single high-volume template look like density — the exact failure `sportskeeda.com` at
    5,089 articles would produce."""
    rows = [_row("solo.ex", f"Council approves the annual budget plan item {i}") for i in range(8)]
    assert sd.cross_publisher_pairs(rows) == []
    rows.append(_row("other.ex", "Council approves the annual budget plan item 0"))
    assert sd.cross_publisher_pairs(rows)


def test_a_row_with_no_publisher_cannot_form_a_pair():
    rows = [_row("", "Council approves the annual budget plan"),
            _row("", "Council approves the annual budget plan")]
    assert sd.cross_publisher_pairs(rows) == []


def test_the_window_still_applies():
    """Six days is part of `pair_admits`, so density is a question about a WINDOW, not a corpus."""
    a = _row("a.ex", "Council approves the annual budget plan today")
    b = _row("b.ex", "Council approves the annual budget plan today", when="2026-07-01T12:00:00+00:00")
    assert sd.cross_publisher_pairs([a, b]) == []


# --------------------------------------------------------------------- the ranking
def test_the_fast_greedy_agrees_with_the_slow_reference():
    """`greedy_publishers` maintains an incremental index; `marginal_gain` recomputes coverage from
    scratch. Every step of every selection is replayed through the reference."""
    rng = random.Random(3)
    for _ in range(40):
        rows = _corpus(rng.randint(10, 60), rng.randint(2, 6), rng=rng)
        pairs = sd.cross_publisher_pairs(rows)
        steps = sd.greedy_publishers(rows, k=8, pairs=pairs)
        admitted: set = set()
        for step in steps:
            ref = sd.marginal_gain(rows, pairs, admitted, {step["publisher"]})
            admitted.add(step["publisher"])
            assert len(sd.covered_articles(rows, pairs, admitted)) == step["cumulative"]
            if step["gain"] and ref != step["gain"]:
                # A bootstrap pair reports 0 then the joint gain, so only non-zero steps
                # correspond one-to-one with the singleton reference.
                assert ref == step["gain"], (step, ref)
        if steps:
            assert sum(s["gain"] for s in steps) == steps[-1]["cumulative"]


def test_greedy_takes_the_argmax_at_every_step():
    rng = random.Random(9)
    for _ in range(20):
        rows = _corpus(rng.randint(15, 50), rng.randint(3, 6), rng=rng)
        pairs = sd.cross_publisher_pairs(rows)
        admitted: set = set()
        for step in sd.greedy_publishers(rows, k=3, pairs=pairs):
            if step["gain"] > 0:
                best = max(sd.marginal_gain(rows, pairs, admitted, {p})
                           for p in {sd._pub(r) for r in rows} - admitted)
                assert step["gain"] == best
            admitted.add(step["publisher"])


def test_the_greedy_bootstraps_from_a_cold_start():
    """**The bug this file's fixture found.** Two publishers with full overlap: 121 admissible pairs
    and 100% co-coverage, and the first greedy returned an EMPTY ranking, because no single
    publisher creates a cross-publisher pair on its own.

    The accounting stays honest — the first step is credited 0, because admitting only it buys
    exactly nothing."""
    rows = [_row(f"es{i}.ex", f"Noticia local numero {i}{k} sobre el ayuntamiento pleno", "es")
            for i in range(2) for k in range(11)]
    pairs = sd.cross_publisher_pairs(rows)
    assert len(pairs) > 100

    steps = sd.greedy_publishers(rows, k=5, pairs=pairs)
    assert [s["publisher"] for s in steps] == ["es0.ex", "es1.ex"]
    assert steps[0]["gain"] == 0, "admitting one publisher alone must not be credited with coverage"
    assert steps[1]["gain"] == 22
    assert steps[-1]["cumulative"] == len(sd.covered_articles(rows, pairs, {"es0.ex", "es1.ex"}))


def test_volume_and_marginal_gain_disagree():
    """The premise of M14 in one fixture. `loud.ex` files the most articles and covers events
    nobody else covers; the two small papers cover each other. Volume ranks `loud` first and Δ ranks
    it last — and if that were not so, volume-ordered admission was right all along."""
    rows = [_row("loud.ex", f"Exclusive interview with the celebrity chef number {i}")
            for i in range(30)]
    rows += [_row("small1.ex", "City council approves the annual transport budget"),
             _row("small2.ex", "City council approves the annual transport budget plan")]
    pairs = sd.cross_publisher_pairs(rows)
    ranked = sd.greedy_publishers(rows, k=3, pairs=pairs)
    picked = [s["publisher"] for s in ranked]
    assert set(picked) == {"small1.ex", "small2.ex"}
    assert "loud.ex" not in picked, "the highest-volume publisher adds no cross-publisher coverage"


def test_a_publisher_that_partners_nobody_scores_zero_and_is_not_ranked():
    rows = [_row("a.ex", "Council approves the annual budget plan"),
            _row("b.ex", "Council approves the annual budget plan today"),
            _row("island.ex", "Completely unrelated headline about marine biology research")]
    ranked = sd.greedy_publishers(rows, k=5)
    assert "island.ex" not in [s["publisher"] for s in ranked]


def test_the_seed_makes_the_ranking_INCREMENTAL(catalogue_free=None):
    """The real M14 question is not "who is best" but "who adds most to what we already carry".
    With `a.ex` seeded, `b.ex` scores the articles it newly partners — including `a.ex`'s own."""
    rows = [_row("a.ex", "Council approves the annual budget plan"),
            _row("b.ex", "Council approves the annual budget plan today")]
    pairs = sd.cross_publisher_pairs(rows)
    seeded = sd.greedy_publishers(rows, seed={"a.ex"}, k=3, pairs=pairs)
    assert [s["publisher"] for s in seeded] == ["b.ex"]
    assert seeded[0]["gain"] == 2, "both articles gain a partner, not just b's"


def test_the_ranking_is_deterministic():
    """A selection that moved between runs could not be audited against the campaign that used it."""
    rows = _corpus(40, 5, seed=17)
    a = sd.greedy_publishers(rows, k=5)
    b = sd.greedy_publishers(rows, k=5)
    assert a == b


# --------------------------------------------------------------------- strata
def test_a_language_whose_headlines_yield_no_tokens_is_UNMEASURABLE_not_zero():
    """The distinction the whole design rests on. A Korean 0% is the tokenizer's, not the corpus's,
    and reporting it beside German's 19% as though they were comparable is what made the original
    peer test fail."""
    rows = [_row(f"ko{i}.ex", f"대통령이 새로운 예산안을 발표했다 {k}", "ko")
            for i in range(3) for k in range(8)]
    prof = sd.language_profile(rows)
    assert prof["ko"]["stratum"] == "tokenizer-dead"
    assert prof["ko"]["deadShare"] == 1.0
    assert prof["ko"]["coCoverage"] == 0.0


def test_a_language_of_fragments_is_flagged_untrustworthy():
    rows = [_row(f"tr{i}.ex", f"Cumhurbaşkanı yeni bütçe planını açıkladı {k}", "tr")
            for i in range(3) for k in range(8)]
    prof = sd.language_profile(rows)
    assert prof["tr"]["stratum"] == "fragment"
    assert prof["tr"]["fragmentShare"] > sd.FRAGMENT_SHARE


def test_a_healthy_language_is_the_only_one_whose_number_is_comparable():
    rows = [_row(f"en{i}.ex", f"Senate passes the funding bill after debate {k}", "en")
            for i in range(3) for k in range(8)]
    prof = sd.language_profile(rows)
    assert prof["en"]["stratum"] == "healthy"
    assert prof["en"]["deadShare"] == 0.0 and prof["en"]["fragmentShare"] == 0.0


def test_a_language_too_small_to_classify_says_so():
    """One article decides the share below the floor, so a stratum there would be noise wearing a
    label."""
    rows = [_row("x.ex", "Senate passes the funding bill", "sv") for _ in range(5)]
    assert sd.language_profile(rows)["sv"]["stratum"] == "too-small"


def test_the_dead_test_runs_before_the_fragment_test():
    """Vietnamese writes most words with diacritics, so many of its headlines yield NO ascii token
    (dead) while the rest yield fragments. Testing dead first labels such a language unmeasurable
    rather than merely untrustworthy — the conservative direction, because density cannot be
    measured on a corpus whose typical article cannot enter a cluster at all."""
    rows = ([_row(f"vi{i}.ex", "Thủ tướng công bố kế hoạch ngân sách mới", "vi")
             for i in range(3) for _ in range(7)]
            + [_row(f"vi{i}.ex", f"Cumhurbaşkanı yeni bütçe planını açıkladı {k}", "vi")
               for i in range(3) for k in range(3)])
    prof = sd.language_profile(rows)
    # BOTH conditions hold, which is what makes the ordering decide rather than be incidental —
    # a fixture where only one fires cannot detect a swap, and the first version of this test was
    # exactly that.
    assert prof["vi"]["deadShare"] > sd.DEAD_SHARE, prof["vi"]
    assert prof["vi"]["fragmentShare"] > sd.FRAGMENT_SHARE, prof["vi"]
    assert prof["vi"]["stratum"] == "tokenizer-dead"


def test_the_unlabelled_bucket_keys_on_question_mark():
    rows = [_row("a.ex", "Senate passes the funding bill", "") for _ in range(25)]
    assert "?" in sd.language_profile(rows)


def test_co_coverage_saturates_and_mean_partners_does_not():
    """**The defect a rehearsal of the runner found in this instrument.**

    `coCoverage` asks only whether an article has AT LEAST ONE cross-publisher partner, so a
    language with two fully-overlapping publishers reads 100% — identical to one with eight. Three
    languages at 8, 4 and 2 publishers all reported 100%, and the runner's `>=` monotonicity test
    scored that FLAT column as "the hypothesis survives". A flat relationship is the null
    hypothesis, and a test that passes on it is a gate that cannot fail.

    `meanPartners` is the depth — distinct other publishers holding a partner, averaged over
    articles — and two publishers cap it at 1.0 however complete their overlap."""
    def lang_rows(code, pubs, events):
        return [_row(f"{code}{i}.ex", f"{code} council approves the transport budget measure {e}",
                     code)
                for i in range(pubs) for e in range(events)]

    prof = sd.language_profile(lang_rows("aa", 8, 4) + lang_rows("bb", 4, 4) + lang_rows("cc", 2, 4))
    cov = [prof[k]["coCoverage"] for k in ("aa", "bb", "cc")]
    depth = [prof[k]["meanPartners"] for k in ("aa", "bb", "cc")]

    assert cov == [1.0, 1.0, 1.0], f"the saturation this test exists for is gone: {cov}"
    assert depth[0] > depth[1] > depth[2], depth
    assert depth == [7.0, 3.0, 1.0], depth
    # A `>=` test over the saturated column passes; over the depth column it discriminates.
    assert all(cov[i] >= cov[i + 1] for i in range(2)), "the flat column passes a >= test"
    assert max(depth) - min(depth) > 0, "the depth column varies, so a flat result means something"


def test_mean_partners_is_capped_by_the_publisher_count():
    """The property that makes it a density measure: N publishers cannot give an article more than
    N-1 distinct partners, so the ceiling rises only by admitting more of them."""
    rows = [_row(f"p{i}.ex", "Council approves the annual transport budget plan", "xx")
            for i in range(2) for _ in range(9)]
    assert sd.language_profile(rows)["xx"]["meanPartners"] == 1.0


def test_peers_counts_only_publishers_that_could_co_cover():
    """**The axis the first production run got wrong.**

    `docs/M14_LANGUAGE_DENSITY_DESIGN.md` specifies "above-floor peers (>= 10 articles per 6-day
    window)" in four places and was committed before the runner. The runner plotted raw distinct
    publishers instead, and on production Spanish showed **153 publishers for 342 articles** — 2.2
    each — against the **7** that clear the floor. A publisher filing twice a week cannot co-cover
    an event, so counting it as density fills the denominator with rows that could never pair, and
    the hypothesis was tested against a quantity nobody had proposed.

    Both columns are reported now, because the gap between them IS the finding."""
    rows = [_row(f"big{i}.ex", f"Council approves the transport budget measure {k}", "xx")
            for i in range(3) for k in range(12)]
    rows += [_row(f"tiny{i}.ex", f"One off report number {i}", "xx") for i in range(40)]
    prof = sd.language_profile(rows)["xx"]
    assert prof["publishers"] == 43
    assert prof["peers"] == 3, "the floor did not remove the one-off publishers"
    assert prof["articlesPerPublisher"] < 2.0


def test_the_peer_floor_is_the_discovery_floor():
    """Same value, deliberately: the evidence that justifies spending a request on a host is the
    evidence that it files enough to overlap with anybody. A second, drifting floor would be the
    fifth converged definition this audit series has had to chase."""
    import source_discovery
    assert sd.PEER_FLOOR == source_discovery.VOLUME_FLOOR
