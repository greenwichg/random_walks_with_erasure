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


def test_lookup_records_a_disambiguation_page_as_ambiguous_not_a_match():
    fetch = _router({"prop=pageprops": _page("Metro", disambiguation=True),
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
