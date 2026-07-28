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
    """Neither form is in the registry, so the public-suffix brand label has to carry it.

    Compared as a SET, not name by name: whether a bare name may join a domain depends on how many
    domains carry that label, which is a property of the whole catalog and invisible to a
    one-name-at-a-time call."""
    g = api.identity_groups(["Sportskeeda.Com", "Sportskeeda"])
    assert g["Sportskeeda.Com"] == g["Sportskeeda"]
    w = api.identity_groups(["Thewest.Com.Au", "Thewest"])
    assert w["Thewest.Com.Au"] == w["Thewest"]


def test_the_registry_is_the_authority_when_it_knows_the_outlet():
    """A curated alias beats the heuristic: BBC News and bbc.co.uk share no brand label, and only
    the registry knows they are one masthead."""
    g = api.identity_groups(["BBC News", "bbc.co.uk"])
    assert g["BBC News"] == g["bbc.co.uk"]


def test_a_known_rating_reached_by_an_unknown_form_still_collapses():
    """The production case. Daily Mail is rated and aliased to dailymail.co.uk; the feed sends
    Dailymail.Com, which resolves to nothing. Keying the CANONICAL on one side and the brand label
    on the other still lands them together."""
    g = api.identity_groups(["Dailymail.Com", "Daily Mail"])
    assert g["Dailymail.Com"] == g["Daily Mail"]


def test_distinct_outlets_do_not_collapse():
    g = api.identity_groups(["Espn.Com", "Variety.Com", "BBC News", "CNN"])
    assert g["Espn.Com"] != g["Variety.Com"]
    assert g["BBC News"] != g["CNN"]


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


# --------------------------------------------------------------------------- #
# The brand-domain rule — and the false positive that forced it.
# --------------------------------------------------------------------------- #
def test_two_unrelated_papers_sharing_a_brand_word_stay_apart():
    """The first version of this key collapsed standard.net.au (the Warrnambool Standard) into
    standard.co.uk (the London Evening Standard) on the bare label 'standard'. Two unrelated
    newspapers, and acting on that finding would have merged them."""
    g = api.identity_groups(["Standard.Net.Au", "Standard.Co.Uk"])
    assert g["Standard.Net.Au"] != g["Standard.Co.Uk"]


def test_national_editions_of_one_brand_stay_apart():
    """The Local runs separate national editions. Conservative is right for a correctness audit."""
    g = api.identity_groups(["Thelocal.Es", "Thelocal.Fr", "Thelocal.De"])
    assert len({g["Thelocal.Es"], g["Thelocal.Fr"], g["Thelocal.De"]}) == 3


def test_a_syndication_network_collapses_across_its_subdomains():
    """The production case: ~100 iHeart station hostnames syndicating identical copy, which
    publisherCount reads as ~100 publishers."""
    stations = ["Kfbk.Iheart.Com", "Wjjs.Iheart.Com", "1051Thewolf.Iheart.Com", "Kogo.Iheart.Com"]
    assert len(set(api.identity_groups(stations).values())) == 1


def test_a_section_subdomain_joins_its_parent():
    g = api.identity_groups(["Obits.Oregonlive.Com", "Oregonlive.Com"])
    assert g["Obits.Oregonlive.Com"] == g["Oregonlive.Com"]
    y = api.identity_groups(["Finance.Yahoo.Com", "Yahoo.Com", "Sg.News.Yahoo.Com"])
    assert len(set(y.values())) == 1


def test_case_variants_of_one_host_collapse():
    """Videocardz.Com and Videocardz.com are the same string in different case — pure normalisation."""
    g = api.identity_groups(["Videocardz.Com", "Videocardz.com"])
    assert g["Videocardz.Com"] == g["Videocardz.com"]


def test_an_ambiguous_bare_name_is_left_alone():
    """A bare 'Standard' could be either paper. Bridging a name to a domain happens only where one
    domain carries the label — guessing here would merge the two newspapers through the name."""
    g = api.identity_groups(["Standard.Net.Au", "Standard.Co.Uk", "Standard"])
    assert g["Standard.Net.Au"] != g["Standard.Co.Uk"]
    assert g["Standard"] not in (g["Standard.Net.Au"], g["Standard.Co.Uk"])


def test_the_audit_scores_over_the_pipelines_name_universe():
    """The residual disagreement after the fix shipped: one story survived reading
    ['Pr Newswire', 'Prnewswire.Com']. The audit collapsed them; the pipeline, resolving over EVERY
    article rather than only those in a story, found the brand label carried by more than one
    domain and left the bare name alone. Same rule, different sets, different answers — so the
    audit takes the wider set."""
    story = _story("Cogent sued", ["Pr Newswire", "Prnewswire.Com"])
    narrow = api.analyse([story])
    assert narrow["fake"], "seen alone, the pair collapses"

    # A second domain carrying the same label makes the bare name ambiguous, exactly as it is in
    # the live build. The pipeline sees this; an audit over story publishers alone does not.
    wide = api.analyse([story], universe={"Prnewswire.Co.Uk"})
    assert wide["fake"] == [], "with the wider set the bare name is left alone, as production does"
    assert "Pr Newswire" in wide["ambiguous"]


def test_a_name_with_no_matching_domain_is_not_ambiguous():
    """The reporting bug this replaced: inferring "did this name join a domain" from the group key
    counted every standalone name as unplaced, because groups() roots a set at its lexicographic
    MINIMUM — usually the name itself, not the token. It reported 214 unplaced names where the real
    answer is a handful. Billboard is not ambiguous; it is simply uncurated."""
    res = api.analyse([_story("Chart news", ["Billboard", "Barron's", "9to5Mac"])])
    assert res["ambiguous"] == []


def test_only_a_contested_brand_word_is_reported():
    import publisher_identity
    assert publisher_identity.ambiguous_labels(["Standard.Net.Au", "Standard.Co.Uk"]) == {"standard"}
    assert publisher_identity.ambiguous_labels(["Sportskeeda.Com", "Billboard"]) == set()
