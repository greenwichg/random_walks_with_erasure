"""examples/publisher_wiki.py — Wikipedia/Wikidata lookup for publisher metadata.

The rule this module exists to keep: **a wrong match is worse than no match.** A publisher page
already carries a lean rating and counted coverage claims; attaching another organisation's founding
year and parent company to one would be a factual error wearing the same confidence as a counted
fact. So most of these tests are about REFUSING, not accepting.

No network: every test injects fetch_json over recorded API response shapes.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import publisher_wiki as pw   # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures shaped like the real APIs.
# --------------------------------------------------------------------------- #
def _page(title, *, qid="Q1", extract="An outlet.", image=None, disambiguation=False,
          missing=False):
    page = {"title": title}
    if missing:
        page["missing"] = True
    else:
        props = {"wikibase_item": qid}
        if disambiguation:
            props["disambiguation"] = ""
        page["pageprops"] = props
        page["extract"] = extract
        if image:
            page["original"] = {"source": image}
    return {"query": {"pages": [page]}}


def _entity(qid="Q1", *, website=None, inception=None, hq=None, country=None, parent=None,
            logo=None, label=None):
    claims = {}

    def _claim(prop, value):
        claims[prop] = [{"rank": "normal",
                         "mainsnak": {"snaktype": "value", "datavalue": {"value": value}}}]

    if website:
        _claim(pw.P_WEBSITE, website)
    if inception:
        _claim(pw.P_INCEPTION, {"time": inception})
    if hq:
        _claim(pw.P_HEADQUARTERS, {"entity-type": "item", "id": hq})
    if country:
        _claim(pw.P_COUNTRY, {"entity-type": "item", "id": country})
    if parent:
        _claim(pw.P_PARENT, {"entity-type": "item", "id": parent})
    if logo:
        _claim(pw.P_LOGO, logo)
    out = {"id": qid, "claims": claims}
    if label:
        out["labels"] = {"en": {"value": label}}
    return out


def _router(routes):
    """fetch_json that dispatches on substrings of the URL, so tests declare responses not order."""
    def fetch(url):
        for needle, payload in routes.items():
            if needle in url:
                return payload
        raise AssertionError(f"unexpected request: {url}")
    return fetch


# --------------------------------------------------------------------------- #
# Pure helpers.
# --------------------------------------------------------------------------- #
def test_registrable_domain_normalises_forms():
    assert pw.registrable_domain("https://www.bbc.co.uk/news") == "bbc.co.uk"
    assert pw.registrable_domain("BBC.CO.UK") == "bbc.co.uk"
    assert pw.registrable_domain("edition.cnn.com") == "edition.cnn.com"
    assert pw.registrable_domain("") is None and pw.registrable_domain(None) is None


def test_year_extraction_handles_wikidata_time_format():
    assert pw._year("+1922-10-18T00:00:00Z") == "1922"
    assert pw._year("-0044-03-15T00:00:00Z") == "44 BC"
    assert pw._year("") is None and pw._year(None) is None


def test_description_truncates_at_a_sentence():
    text = "The paper was founded in 1821. It is published daily in London and elsewhere abroad."
    out = pw.truncate_description(text, limit=40)
    assert out == "The paper was founded in 1821."


def test_description_without_a_sentence_break_ends_on_a_word():
    out = pw.truncate_description("supercalifragilistic " * 10, limit=40)
    assert out == "supercalifragilistic…"          # whole words only, no dangling fragment
    assert not out.rstrip("…").endswith(" ")


def test_commons_url_is_built_from_the_file_name():
    url = pw.commons_image_url("BBC News 2019.svg")
    # Underscores, not %20: that is Wikimedia's canonical file-path form.
    assert url == ("https://commons.wikimedia.org/wiki/Special:FilePath/"
                   "BBC_News_2019.svg?width=320")
    assert pw.commons_image_url(None) is None


def test_deprecated_and_valueless_claims_are_skipped():
    """Wikidata records superseded values explicitly. Rendering one would show a fact the source
    itself marks as wrong."""
    entity = {"claims": {pw.P_WEBSITE: [
        {"rank": "deprecated",
         "mainsnak": {"snaktype": "value", "datavalue": {"value": "https://old.example"}}},
        {"rank": "normal", "mainsnak": {"snaktype": "somevalue"}},
        {"rank": "normal",
         "mainsnak": {"snaktype": "value", "datavalue": {"value": "https://new.example"}}},
    ]}}
    assert pw._claims(entity, pw.P_WEBSITE) == ["https://new.example"]


def test_country_only_counts_when_it_resolves_to_an_iso_code():
    """Every other country field in the product is ISO alpha-2. A bare label would join with
    nothing, so it is dropped rather than shown."""
    entity = _entity(country="Q145")
    with_iso = pw.parse_entity(entity, {"Q145": _entity("Q145", label="United Kingdom") |
                                        {"claims": {pw.P_ISO_3166: [
                                            {"rank": "normal",
                                             "mainsnak": {"snaktype": "value",
                                                          "datavalue": {"value": "GB"}}}]}}})
    assert with_iso["country"] == "GB"
    without = pw.parse_entity(entity, {"Q145": _entity("Q145", label="United Kingdom")})
    assert without["country"] is None


def test_referenced_labels_resolve_to_names_never_q_ids():
    entity = _entity(hq="Q84", parent="Q42")
    facts = pw.parse_entity(entity, {"Q84": _entity("Q84", label="London"),
                                     "Q42": _entity("Q42", label="Example Group")})
    assert facts["headquarters"] == "London" and facts["parent"] == "Example Group"
    bare = pw.parse_entity(entity, {})
    assert bare["headquarters"] is None and bare["parent"] is None   # never a raw Q-id


# --------------------------------------------------------------------------- #
# Verification — the guard.
# --------------------------------------------------------------------------- #
def test_matching_domain_accepts():
    ok, reason = pw.verify(publisher="BBC News", page_title="BBC News",
                           facts={"website": "https://www.bbc.co.uk"}, observed_host="bbc.co.uk")
    assert ok and reason == "domain"


def test_conflicting_domain_rejects_even_when_the_name_matches():
    """The Fox Sports / Fox Corporation case: the names look right, the domains do not."""
    ok, reason = pw.verify(publisher="Fox Sports", page_title="Fox Sports",
                           facts={"website": "https://www.foxcorporation.com"},
                           observed_host="foxsports.com")
    assert not ok and reason == "domain_conflict"


def test_a_common_noun_masthead_does_not_bind_to_the_object_article():
    """'Mirror' hits the article about reflective surfaces first. It has no organisational claims,
    which is exactly how it is told apart from the newspaper."""
    ok, reason = pw.verify(publisher="Mirror", page_title="Mirror",
                           facts={"website": None, "founded": None, "headquarters": None,
                                  "parent": None}, observed_host=None)
    assert not ok and reason == "not_an_organisation"


def test_title_match_plus_an_org_claim_accepts_when_no_domain_is_known():
    ok, reason = pw.verify(publisher="The Guardian", page_title="The Guardian",
                           facts={"founded": "1821"}, observed_host=None)
    assert ok and reason == "title"


def test_a_different_title_is_never_accepted_without_domain_evidence():
    ok, reason = pw.verify(publisher="Example Post", page_title="Example Corporation",
                           facts={"founded": "1900"}, observed_host=None)
    assert not ok and reason == "unverified"


def test_leading_the_is_ignored_when_comparing_names():
    ok, _ = pw.verify(publisher="Guardian", page_title="The Guardian",
                      facts={"founded": "1821"}, observed_host=None)
    assert ok


# --------------------------------------------------------------------------- #
# End-to-end lookup.
# --------------------------------------------------------------------------- #
def test_lookup_returns_verified_facts():
    fetch = _router({
        "list=search": {"query": {"search": []}},
        "prop=pageprops": _page("BBC News", qid="Q9531", extract="BBC News is a division."),
        "ids=Q9531": {"entities": {"Q9531": _entity(
            "Q9531", website="https://www.bbc.co.uk/news", inception="+1922-10-18T00:00:00Z",
            hq="Q84", country="Q145", parent="Q9531P", logo="BBC News.svg")}},
        "ids=Q84": {"entities": {
            "Q84": _entity("Q84", label="London"),
            "Q145": _entity("Q145", label="United Kingdom") | {"claims": {pw.P_ISO_3166: [
                {"rank": "normal",
                 "mainsnak": {"snaktype": "value", "datavalue": {"value": "GB"}}}]}},
            "Q9531P": _entity("Q9531P", label="BBC")}},
    })
    res = pw.lookup("BBC News", fetch, observed_host="bbc.co.uk")

    assert res["status"] == "ok" and res["reason"] == "domain"
    assert res["founded"] == "1922" and res["headquarters"] == "London"
    assert res["country"] == "GB" and res["parent"] == "BBC"
    assert res["wikipediaUrl"] == "https://en.wikipedia.org/wiki/BBC_News"
    assert res["logo_source"] == "wikimedia" and "BBC_News.svg" in res["logo"]


def test_a_disambiguation_page_with_no_usable_entries_stays_ambiguous():
    fetch = _router({"prop=links": {"query": {"pages": [{"title": "Metro", "links": []}]}},
                     "prop=pageprops": _page("Metro", disambiguation=True),
                     "list=search": {"query": {"search": []}}})
    res = pw.lookup("Metro", fetch)
    assert res["status"] == "ambiguous" and res["reason"] == "disambiguation"


def test_lookup_reports_no_match_for_a_missing_page():
    fetch = _router({"prop=pageprops": _page("Nonesuch Daily", missing=True),
                     "list=search": {"query": {"search": []}}})
    res = pw.lookup("Nonesuch Daily", fetch)
    assert res["status"] == "no_match" and res["reason"] == "no_page"


def test_lookup_falls_back_to_search_when_the_title_is_not_an_article():
    calls = []

    def fetch(url):
        calls.append(url)
        if "list=search" in url:
            return {"query": {"search": [{"title": "The Example Post"}]}}
        if "titles=Example%20Post" in url:
            return _page("Example Post", missing=True)
        if "titles=The%20Example%20Post" in url:
            return _page("The Example Post", qid="Q7")
        if "ids=Q7" in url:
            return {"entities": {"Q7": _entity("Q7", website="https://examplepost.com")}}
        raise AssertionError(url)

    res = pw.lookup("Example Post", fetch, observed_host="examplepost.com")
    assert res["status"] == "ok" and res["wikipediaTitle"] == "The Example Post"
    assert any("list=search" in c for c in calls)


def test_lookup_uses_the_page_image_only_when_no_logo_claim_exists():
    fetch = _router({
        "list=search": {"query": {"search": []}},
        "prop=pageprops": _page("Example Post", qid="Q7",
                                image="https://upload.wikimedia.org/hq-photo.jpg"),
        "ids=Q7": {"entities": {"Q7": _entity("Q7", website="https://examplepost.com")}},
    })
    res = pw.lookup("Example Post", fetch, observed_host="examplepost.com")
    assert res["logo"] == "https://upload.wikimedia.org/hq-photo.jpg"
    assert res["logo_source"] == "wikipedia"


def test_lookup_without_a_wikidata_item_still_verifies_by_title():
    """Some articles carry no Wikidata link. The page's own facts must still be usable — or
    honestly refused — rather than crashing the enrichment."""
    page = _page("Example Post", qid=None)
    page["query"]["pages"][0]["pageprops"] = {}
    fetch = _router({"prop=pageprops": page, "list=search": {"query": {"search": []}}})
    res = pw.lookup("Example Post", fetch)
    assert res["status"] == "ambiguous" and res["reason"] == "not_an_organisation"


def test_empty_name_never_makes_a_request():
    def fetch(url):
        raise AssertionError("should not fetch")
    assert pw.lookup("   ", fetch)["status"] == "no_match"


# --------------------------------------------------------------------------- #
# Brand-label matching — measured against the live catalog's actual refusals.
# --------------------------------------------------------------------------- #
def test_domain_label_finds_the_brand_across_suffix_forms():
    assert pw.domain_label("https://www.bbc.co.uk/news") == "bbc"
    assert pw.domain_label("bbc.com") == "bbc"
    assert pw.domain_label("news.bbc.co.uk") == "bbc"
    assert pw.domain_label("newsinfo.inquirer.net") == "inquirer"
    assert pw.domain_label("http://www.inquirer.com.ph/") == "inquirer"
    assert pw.domain_label("thestar.com.my") == "thestar"
    assert pw.domain_label("") is None


def test_the_same_outlet_reached_by_two_domains_is_one_match():
    """Every one of these was refused as a domain_conflict in production before brand-label
    comparison — one organisation, two spellings."""
    for observed, site in [("bbc.co.uk", "https://bbc.com"),
                           ("dailymail.com", "http://www.dailymail.co.uk"),
                           ("aol.co.uk", "https://aol.com/"),
                           ("unitaid.eu", "http://www.unitaid.org/"),
                           ("newsinfo.inquirer.net", "http://www.inquirer.com.ph/")]:
        ok, reason = pw.verify(publisher="x", page_title="y", facts={"website": site},
                               observed_host=observed)
        assert ok and reason == "domain", f"{observed} vs {site}"


def test_different_organisations_still_conflict_at_the_label():
    """The precision half. These were CORRECT refusals in the same production run and must stay so
    — brand-label matching must not be a loophole."""
    for observed, site in [("aktiencheck.de", "https://www.tomshardware.com"),
                           ("pagesix.com", "https://nypost.com"),
                           ("decider.com", "https://nypost.com"),
                           ("foxsports.com", "https://www.foxcorporation.com")]:
        ok, reason = pw.verify(publisher="x", page_title="y", facts={"website": site},
                               observed_host=observed)
        assert not ok and reason == "domain_conflict", f"{observed} vs {site}"


def test_a_domain_shaped_publisher_name_matches_its_article_title():
    """Much of the catalog names publishers by domain. "marketbeat.com" was refused against the
    article titled "MarketBeat" purely on the .com."""
    ok, reason = pw.verify(publisher="marketbeat.com", page_title="MarketBeat",
                           facts={"founded": "2011"}, observed_host=None)
    assert ok and reason == "title"


def test_a_title_with_a_full_stop_is_not_treated_as_a_domain():
    """The domain reduction applies only to single dotted tokens, so ordinary prose is untouched."""
    assert pw._name_key("St. Louis Post-Dispatch") == "st louis post dispatch"


def test_instance_of_accepts_a_newspaper_carrying_no_other_claims():
    """The Hill: an exact title match on a real newspaper, refused for having no website, founding
    year, HQ or parent. Its P31 said "newspaper" the whole time."""
    facts = {"website": None, "founded": None, "headquarters": None, "parent": None}
    refused, why = pw.verify(publisher="The Hill", page_title="The Hill", facts=facts)
    assert not refused and why == "not_an_organisation"

    ok, reason = pw.verify(publisher="The Hill", page_title="The Hill", facts=facts,
                           classes=["Q11032"])       # newspaper
    assert ok and reason == "title"


def test_instance_of_does_not_rescue_a_non_organisation():
    """The guard that keeps "Mirror" off the reflective-surface article: an unrelated P31 class is
    not an organisation signal."""
    ok, reason = pw.verify(publisher="Mirror", page_title="Mirror",
                           facts={"website": None}, classes=["Q35197"])
    assert not ok and reason == "not_an_organisation"


def test_instance_of_never_overrides_a_domain_conflict():
    """Class evidence is only consulted when there is no domain to compare — being a newspaper does
    not make you THIS newspaper."""
    ok, reason = pw.verify(publisher="Fox Sports", page_title="Fox Sports",
                           facts={"website": "https://www.foxcorporation.com"},
                           observed_host="foxsports.com", classes=["Q11032"])
    assert not ok and reason == "domain_conflict"


def test_several_observed_hosts_are_all_considered():
    """A publisher often reaches us from more than one domain; matching any of them is a match."""
    ok, _ = pw.verify(publisher="x", page_title="y", facts={"website": "https://bbc.com"},
                      observed_host=["feeds.example.net", "bbc.co.uk"])
    assert ok


def test_instance_of_reads_the_claim():
    entity = {"claims": {pw.P_INSTANCE_OF: [
        {"rank": "normal", "mainsnak": {"snaktype": "value",
                                        "datavalue": {"value": {"entity-type": "item",
                                                                "id": "Q11032"}}}}]}}
    assert pw.instance_of(entity) == ["Q11032"]
    assert pw.instance_of({}) == []


# --------------------------------------------------------------------------- #
# Multi-candidate verification.
# --------------------------------------------------------------------------- #
def test_a_disambiguation_page_is_a_candidate_list_not_a_dead_end():
    """The Hill, end to end. "The Hill" is a disambiguation page; full-text search answers it with
    "King of the Hill". The page's own entries include "The Hill (newspaper)", which verifies on
    domain. Checking only the first plausible page lost this outlet entirely."""
    def fetch(url):
        if "prop=links" in url:
            return {"query": {"pages": [{"title": "The Hill", "links": [
                {"title": "King of the Hill"}, {"title": "The Hill (newspaper)"}]}]}}
        if "titles=The%20Hill&" in url or url.endswith("titles=The%20Hill"):
            return _page("The Hill", disambiguation=True)
        if "titles=King%20of%20the%20Hill" in url:
            return _page("King of the Hill", qid="Q_KOTH")
        if "titles=The%20Hill%20%28newspaper%29" in url:
            return _page("The Hill (newspaper)", qid="Q_HILL")
        if "list=search" in url:
            return {"query": {"search": [{"title": "King of the Hill"}]}}
        if "ids=Q_KOTH" in url:
            return {"entities": {"Q_KOTH": _entity("Q_KOTH", website="https://kingofthehill.tv")}}
        if "ids=Q_HILL" in url:
            return {"entities": {"Q_HILL": _entity("Q_HILL", website="https://thehill.com",
                                                   inception="+1994-01-01T00:00:00Z")}}
        raise AssertionError(f"unexpected: {url}")

    res = pw.lookup("The Hill", fetch, observed_host="thehill.com")
    assert res["status"] == "ok" and res["reason"] == "domain"
    assert res["wikipediaTitle"] == "The Hill (newspaper)"
    assert res["founded"] == "1994"


def test_search_candidates_are_all_tried_not_just_the_first():
    """The first search result being wrong must not end the search — the second may be right."""
    def fetch(url):
        if "list=search" in url:
            return {"query": {"search": [{"title": "Wrong Co"}, {"title": "The Example Post"}]}}
        if "titles=Example%20Post" in url:
            return _page("Example Post", missing=True)
        if "titles=Wrong%20Co" in url:
            return _page("Wrong Co", qid="Q_WRONG")
        if "titles=The%20Example%20Post" in url:
            return _page("The Example Post", qid="Q_RIGHT")
        if "ids=Q_WRONG" in url:
            return {"entities": {"Q_WRONG": _entity("Q_WRONG", website="https://wrongco.example")}}
        if "ids=Q_RIGHT" in url:
            return {"entities": {"Q_RIGHT": _entity("Q_RIGHT", website="https://examplepost.com")}}
        raise AssertionError(f"unexpected: {url}")

    res = pw.lookup("Example Post", fetch, observed_host="examplepost.com")
    assert res["status"] == "ok" and res["wikipediaTitle"] == "The Example Post"


def test_the_most_informative_refusal_is_the_one_reported():
    """When nothing verifies, the recorded reason should be the one a human can act on: "Wikipedia
    has this brand on a different domain" beats "search returned junk"."""
    def fetch(url):
        if "list=search" in url:
            return {"query": {"search": [{"title": "Junk Result"}, {"title": "Near Miss"}]}}
        if "titles=Example%20Post&" in url or url.endswith("titles=Example%20Post"):
            return _page("Example Post", missing=True)
        if "titles=Junk%20Result" in url:
            return _page("Junk Result", qid="Q_JUNK")
        if "titles=Near%20Miss" in url:
            return _page("Near Miss", qid="Q_NEAR")
        if "ids=Q_JUNK" in url:
            return {"entities": {"Q_JUNK": _entity("Q_JUNK")}}          # no website -> unverified
        if "ids=Q_NEAR" in url:
            return {"entities": {"Q_NEAR": _entity("Q_NEAR", website="https://different.example")}}
        raise AssertionError(f"unexpected: {url}")

    res = pw.lookup("Example Post", fetch, observed_host="examplepost.com")
    assert res["status"] == "ambiguous" and res["reason"] == "domain_conflict"
    assert res["wikipediaTitle"] == "Near Miss"


def test_a_direct_hit_that_verifies_costs_three_requests():
    """The common path must not get more expensive to serve the hard one: page, item, labels."""
    calls = []

    def fetch(url):
        calls.append(url)
        if "prop=pageprops" in url:
            return _page("Example Post", qid="Q7")
        if "ids=Q7" in url:
            return {"entities": {"Q7": _entity("Q7", website="https://examplepost.com",
                                               hq="Q84")}}
        if "ids=Q84" in url:
            return {"entities": {"Q84": _entity("Q84", label="Springfield")}}
        raise AssertionError(f"unexpected: {url}")

    res = pw.lookup("Example Post", fetch, observed_host="examplepost.com")
    assert res["status"] == "ok" and res["headquarters"] == "Springfield"
    assert len(calls) == 3 and not any("list=search" in c for c in calls)


def test_a_refused_candidate_does_not_pay_for_label_resolution():
    """Identity is decided from the item's own claims; the extra request that resolves headquarters
    and parent labels is deferred to the winner, so trying candidates stays affordable."""
    calls = []

    def fetch(url):
        calls.append(url)
        if "prop=pageprops" in url:
            return _page("Example Post", qid="Q7")
        if "ids=Q7" in url:
            return {"entities": {"Q7": _entity("Q7", website="https://elsewhere.example",
                                               hq="Q84", parent="Q99")}}
        if "list=search" in url:
            return {"query": {"search": []}}
        raise AssertionError(f"unexpected: {url}")

    res = pw.lookup("Example Post", fetch, observed_host="examplepost.com")
    assert res["status"] == "ambiguous" and res["reason"] == "domain_conflict"
    assert not any("ids=Q84" in c for c in calls)      # never resolved labels for a rejected item


def test_candidate_count_is_bounded():
    """A bounded budget is what keeps this polite; an unmatched name must not walk a search page."""
    fetched = []

    def fetch(url):
        if "list=search" in url:
            return {"query": {"search": [{"title": f"Candidate {i}"} for i in range(10)]}}
        if "titles=Nobody" in url:
            return _page("Nobody", missing=True)
        if "prop=pageprops" in url:
            fetched.append(url)
            return _page("Some Page", qid=None) | {}
        raise AssertionError(f"unexpected: {url}")

    pw.lookup("Nobody", fetch, max_candidates=3)
    assert len(fetched) <= 3


def test_org_claim_presence_is_read_without_resolving_labels():
    entity = _entity(hq="Q84")
    assert pw.has_org_claim(entity) is True
    assert pw.has_org_claim(_entity()) is False


def test_headquarters_only_item_counts_as_an_organisation():
    """parse_entity leaves HQ/parent as None until labels are resolved, so the org test would have
    read a headquarters-only item as "not an organisation" without the presence check."""
    ok, reason = pw.verify(publisher="Example Post", page_title="Example Post",
                           facts={"website": None, "founded": None, "headquarters": None,
                                  "parent": None}, org_claims=True)
    assert ok and reason == "title"
