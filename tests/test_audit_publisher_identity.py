"""The publisher-identity auditor (examples/audit_publisher_identity.py).

``publisherCount`` counts distinct publisher STRINGS, and feeds do not agree on how to name an
outlet. Sportskeeda arrives as both ``Sportskeeda.Com`` and ``Sportskeeda``; the registry knows
``Daily Mail`` while the feed sends ``Dailymail.Com``. Each pair inflates the count — and
``min_publishers = 2`` means one outlet under two names can be admitted as a story, which is the
failure these tests exist to keep visible.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import audit_publisher_identity as api   # noqa: E402


def _story(title, pairs, *, arts=None):
    return {"title": title, "totalCoverage": arts if arts is not None else len(pairs),
            "coverage": [{"publisher": p, "url": f"u{i}"} for i, p in enumerate(pairs)]}


def test_domain_and_name_forms_of_an_unknown_outlet_collapse():
    """Neither form is in the registry, so the public-suffix brand label has to carry it."""
    assert api.identity_key("Sportskeeda.Com") == api.identity_key("Sportskeeda")
    assert api.identity_key("Thewest.Com.Au") == api.identity_key("Thewest")


def test_the_registry_is_the_authority_when_it_knows_the_outlet():
    """A curated alias beats the heuristic: BBC News and bbc.co.uk share no brand label, and only
    the registry knows they are one masthead."""
    assert api.identity_key("BBC News") == api.identity_key("bbc.co.uk")


def test_a_known_rating_reached_by_an_unknown_form_still_collapses():
    """The production case. Daily Mail is rated and aliased to dailymail.co.uk; the feed sends
    Dailymail.Com, which resolves to nothing. Keying the CANONICAL on one side and the brand label
    on the other still lands them together."""
    assert api.identity_key("Dailymail.Com") == api.identity_key("Daily Mail")


def test_distinct_outlets_do_not_collapse():
    assert api.identity_key("Espn.Com") != api.identity_key("Variety.Com")
    assert api.identity_key("BBC News") != api.identity_key("CNN")


def test_a_story_that_is_one_outlet_twice_is_flagged():
    """The correctness failure, not a cosmetic one: this cleared min_publishers = 2 because the
    same masthead was counted under two names."""
    res = api.analyse([_story("Cricket roundup", ["Sportskeeda.Com", "Sportskeeda"])])
    assert len(res["fake"]) == 1
    assert res["fake"][0]["was"] == 2 and res["fake"][0]["now"] == 1


def test_a_genuine_two_publisher_story_is_untouched():
    res = api.analyse([_story("Senate bill", ["BBC News", "CNN"])])
    assert res["fake"] == [] and res["shrunk"] == []


def test_an_inflated_but_still_valid_story_is_counted_separately():
    """Three names, two outlets: the count was wrong but the story is real. Reporting it as fake
    would overstate the damage."""
    res = api.analyse([_story("Match report", ["Sportskeeda.Com", "Sportskeeda", "BBC News"])])
    assert len(res["shrunk"]) == 1 and res["fake"] == []
    assert res["shrunk"][0]["was"] == 3 and res["shrunk"][0]["now"] == 2


def test_missing_aliases_are_named_with_the_row_that_already_exists():
    """The cheapest fix in the set — the rating exists, the row just lacks the form the feed sends,
    so the output is directly pasteable into outlet_registry.csv."""
    res = api.analyse([_story("Royal story", ["Daily Mail", "Dailymail.Com"])])
    assert len(res["missingAlias"]) == 1
    m = res["missingAlias"][0]
    assert m["canonical"] == "Daily Mail" and m["add"] == ["Dailymail.Com"]


def test_two_unknown_forms_are_not_reported_as_a_missing_alias():
    """Nothing to alias TO — that pair is a curation gap, not an alias gap, and conflating them
    would send someone to edit a row that does not exist."""
    res = api.analyse([_story("Cricket", ["Sportskeeda.Com", "Sportskeeda"])])
    assert res["missingAlias"] == []
    assert len(res["collisions"]) == 1


def test_empty_catalog_is_not_a_crash():
    res = api.analyse([])
    assert res["names"] == 0 and res["identities"] == 0 and res["fake"] == []
