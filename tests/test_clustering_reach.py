"""Who a clustering change REACHED, against who it cost — the benefit half of the measurement.

## Why this exists

`audit_clustering_change.py --unicode-words` ran on production and printed **ADOPT**: 65 clusters
split, 2.2% of covered articles dropped, story count up 11. Strictly better than `hyphen_compounds`,
which was rejected at 121 splits and 2.6%.

It should not have been adopted on that, and the tool's own docstring says why — *"The VERDICT line
is a COST check, not the whole criterion… Two candidates have now printed ADOPT and been rejected on
the rest of it."* `droppedOut` and `newlyCovered` are one number each, and one number cannot
adjudicate a change whose entire purpose is to rescue a population: **106 newly covered** does not
say whether any of them are the Korean, Arabic or Chinese articles the change exists for, or 106 more
English wire duplicates.

`audit_source_cohort` had the same defect and named the fix: *"Every previous version of this script
measured COST precisely and BENEFIT not at all, which is why the five-outlet cohort could not be
adjudicated: 29 collateral losses against an unquantified good is not a trade, it is half a trade."*

## The split is by the defect, not by language

An article whose headline yields fewer than `MIN_TITLE_TOKENS` under the **shipped** tokenizer is
rejected by `pair_admits` before any other test, so it can be in no story at all. That is exactly the
population a tokenizer change exists to reach, it is derivable from the title alone, and it needs no
`language` field — which is populated for ~80% of rows and 0% of some adapters, and would have made
the benefit measurement depend on the same metadata gap M10 was about.

Two consequences pin the arithmetic, and both are asserted below:

* an `excluded` article's `dropped` is **0 by construction** — it was in no story to be dropped from;
* an `excluded` article's `newly` is the **entire** measured benefit.
"""
from __future__ import annotations

import contextlib
import io
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "examples"))

import audit_clustering_change as ach  # noqa: E402
import audit_source_cohort as asc  # noqa: E402
import clustering  # noqa: E402
import store as store_mod  # noqa: E402

_NOW = datetime.now(timezone.utc)

_ENGLISH = ["after debate", "averting shutdown", "to avert shutdown", "in late vote"]
_KOREAN = ["발표했다", "공개했다", "설명했다", "확정했다"]


@pytest.fixture()
def catalogue(tmp_path):
    """One English event that already clusters, and one Korean event that structurally cannot."""
    db = f"sqlite:///{tmp_path / 'reach.db'}"
    st = store_mod.Store(db)

    def add(cu, pub, title, lang):
        # The DISPLAY url differs from the canonical one, which is the production case and the
        # whole point of `member_key`: a fixture where they are equal cannot tell the two lookups
        # apart, and a mutation swapping them passed silently until this was fixed.
        st.upsert_feed_article(
            canonical_url=cu, url=cu + "?utm_source=newsletter", publisher=pub,
            source_publisher=pub, title=title,
            description="context", body=None, published_at=_NOW.isoformat(), source_feed="feed://x",
            language=lang,
            scored={"article_id": cu, "outlet": pub, "category": "Politics", "lean": 0.0,
                    "title": title})

    for i, w in enumerate(_ENGLISH):
        add(f"https://e{i}.example/a", f"e{i}.example", f"Senate passes the funding bill {w}", "en")
    for i, t in enumerate(_KOREAN):
        add(f"https://k{i}.example/a", f"k{i}.example", f"대통령이 새로운 예산안을 {t}", "ko")
    st.engine.dispose()
    return db


def _run(db, *argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ach.main(["--db", db, *argv])
    return buf.getvalue()


# --------------------------------------------------------------------- the key
def test_the_member_key_agrees_with_the_cohort_audits(catalogue):
    """Differential, against the real function rather than a restatement.

    `audit_source_cohort.member_key`'s docstring records that looking up `canonicalUrl` instead of
    the display url "invalidated the first two production runs of this script" and reported
    participation 20x low. A second copy of that rule which drifted would make the reach table
    silently report everything as uncovered — and an all-zero `before` column reads exactly like a
    change that rescued the whole corpus."""
    st = store_mod.Store(catalogue)
    import story_service
    rows = story_service._fetch(st)
    assert rows
    assert any(r["url"] != r["canonicalUrl"] for r in rows), (
        "the fixture must carry a display url that differs from the canonical one, or the two "
        "lookups are indistinguishable and this test proves nothing")
    for r in rows:
        assert ach.member_key(r) == asc.member_key(r)


# --------------------------------------------------------------------- the split
def test_the_excluded_population_is_the_one_the_defect_defines(catalogue):
    """Not a language list. The bucket is "the shipped tokenizer gave it too few tokens", which is
    the condition `pair_admits` rejects on."""
    st = store_mod.Store(catalogue)
    import story_service
    rows = story_service._fetch(st)
    excluded = [r for r in rows
                if len(clustering.title_tokens(r.get("title") or "")) < clustering.MIN_TITLE_TOKENS]
    assert {r["publisher"] for r in excluded} == {f"k{i}.example" for i in range(4)}


def test_the_report_attributes_the_gain_to_the_excluded_population(catalogue):
    out = _run(catalogue, "--unicode-words")
    assert "=== who the change reached ===" in out
    body = out[out.index("population"):]
    reachable = next(l for l in body.splitlines() if l.strip().startswith("reachable"))
    excluded = next(l for l in body.splitlines() if l.strip().startswith("excluded"))
    # articles, before, after, dropped, newly
    assert reachable.split()[1:] == ["4", "4", "4", "0", "0"], reachable
    assert excluded.split()[1:] == ["4", "0", "4", "0", "4"], excluded
    assert "4 article(s) reached a story that structurally could not, against 0 lost" in out


def test_an_excluded_article_can_never_show_a_DROP(catalogue):
    """0 by construction, and worth asserting rather than reasoning about: a non-zero here would
    mean the bucket is misclassifying reachable articles as excluded, and the benefit column would
    be measuring the wrong population."""
    res = _reach_of(catalogue, uni=True)
    assert res["byReach"]["excluded"]["dropped"] == 0
    assert res["byReach"]["excluded"]["before"] == 0


def _reach_of(db, *, uni):
    st = store_mod.Store(db)
    out = ach.compare(st, before=(3, 3), after=(3, 3), after_uni=True if uni else None, show=0)
    st.engine.dispose()
    return out["reach"]


def test_a_change_that_reaches_nobody_says_so_loudly(catalogue):
    """The line that stops the next candidate being adopted on its cost bar alone. With no
    tokenizer change the excluded population is still excluded, so the benefit is zero — and a
    zero benefit has to be stated, not left to be inferred from a table."""
    out = _run(catalogue, "--min-shared", "3")      # a no-op change
    assert "THE BENEFIT IS ZERO" in out
    assert "Do not adopt on the VERDICT line" in out


def test_the_benefit_line_is_absent_when_the_change_does_reach_someone(catalogue):
    assert "THE BENEFIT IS ZERO" not in _run(catalogue, "--unicode-words")


# --------------------------------------------------------------------- the secondary view
def test_the_language_table_is_reported_but_named_as_secondary(catalogue):
    out = _run(catalogue, "--unicode-words")
    lines = [l.split() for l in out.splitlines() if l.strip().startswith(("ko ", "en "))]
    by_lang = {l[0]: l[1:] for l in lines}
    assert by_lang["ko"] == ["4", "0", "4", "0", "4"]
    assert by_lang["en"] == ["4", "4", "4", "0", "0"]
    assert "populated for ~80% of rows, so this is the secondary view" in out


def test_rows_with_no_language_still_reach_the_primary_table(tmp_path):
    """The reach split must not depend on `language`, which most feeds do not supply — that gap is
    what M10 was about, and a benefit measurement that inherited it would report nothing for the
    adapters that carry no language at all."""
    db = f"sqlite:///{tmp_path / 'nolang.db'}"
    st = store_mod.Store(db)
    for i, t in enumerate(_KOREAN):
        cu = f"https://k{i}.example/a"
        st.upsert_feed_article(
            canonical_url=cu, url=cu + "?utm_source=newsletter", publisher=f"k{i}.example",
            source_publisher=f"k{i}.example",
            title=f"대통령이 새로운 예산안을 {t}", description="context", body=None,
            published_at=_NOW.isoformat(), source_feed="feed://x",
            scored={"article_id": cu, "outlet": f"k{i}.example", "category": "Politics",
                    "lean": 0.0, "title": "t"})
    st.engine.dispose()
    reach = _reach_of(db, uni=True)
    assert reach["byReach"]["excluded"]["newly"] == 4
    assert set(reach["byLanguage"]) == {"?"}, "a missing language must not vanish from the report"
