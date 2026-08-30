"""Unit tests for examples/outlet_registry.py — the canonical outlet registry (Commit 1).

The registry is the product layer's single source of truth for outlet identity + lean. These
tests pin the behaviours later milestones depend on: every browser-extension domain resolves,
the domain / display-name / URL / corpus-suffix forms of one outlet all collapse to the same
canonical outlet, and the two historical bugs this abstraction exists to fix stay fixed — the
``lstrip("www.")`` corruption of ``w``-domains, and the ``nytimes.com`` vs ``"New York Times"``
normalisation split. Pure + offline; nothing is wired into ingestion or the corpus yet.
"""

import math
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import outlet_registry as orx  # noqa: E402


@pytest.fixture(scope="module")
def reg():
    return orx.OutletRegistry.load()


# Every domain the browser-extension allowlist can capture -> the outlet it must resolve to.
EXTENSION_DOMAINS = {
    "nytimes.com": "New York Times", "washingtonpost.com": "Washington Post",
    "wsj.com": "Wall Street Journal", "foxnews.com": "Fox News", "cnn.com": "CNN",
    "nbcnews.com": "NBC News", "apnews.com": "Associated Press", "reuters.com": "Reuters",
    "npr.org": "NPR", "bbc.com": "BBC", "theguardian.com": "The Guardian",
    "politico.com": "Politico", "thehill.com": "The Hill", "usatoday.com": "USA Today",
    "cnbc.com": "CNBC", "bloomberg.com": "Bloomberg", "abcnews.go.com": "ABC News",
    "cbsnews.com": "CBS News", "aljazeera.com": "Al Jazeera", "axios.com": "Axios",
    "vox.com": "Vox", "theatlantic.com": "The Atlantic", "latimes.com": "Los Angeles Times",
}


@pytest.mark.parametrize("domain,canonical", EXTENSION_DOMAINS.items())
def test_every_extension_domain_resolves(reg, domain, canonical):
    """The whole point: no captured news domain falls through to n/a."""
    for form in (domain, f"https://www.{domain}/2026/07/05/section/a-story", f"www.{domain}"):
        out = reg.resolve(form)
        assert out is not None, f"{form!r} did not resolve"
        assert out.canonical == canonical
        assert -2.0 <= out.lean <= 2.0 and math.isfinite(out.lean)


def test_www_prefix_is_stripped_correctly(reg):
    """Regression: the research helper's lstrip('www.') ate leading 'w' chars, so
    washingtonpost.com -> 'ashingtonpost' and wsj.com -> 'sj' -> NaN lean. Must not recur."""
    assert reg.resolve("https://www.washingtonpost.com/x").canonical == "Washington Post"
    assert reg.resolve("https://www.washingtonpost.com/x").lean == -1.0
    assert reg.resolve("https://www.wsj.com/x").canonical == "Wall Street Journal"
    assert reg.resolve("https://www.wsj.com/x").lean == 1.0


def test_domain_and_display_name_collapse_to_one_outlet(reg):
    """Regression: nytimes.com normalised to 'nytimes' but 'New York Times' to 'newyorktimes'.
    The registry makes every form resolve to the same canonical Outlet."""
    forms = ["New York Times", "the new york times", "NYT", "nytimes.com",
             "https://www.nytimes.com/2026/us/politics/x", "New York Times (News)"]
    resolved = {reg.resolve(f) for f in forms}
    assert len(resolved) == 1
    # Identity = canonical name + lean. The Outlet dataclass also carries locality columns now
    # (Location Intelligence Phase 1), which this collapse test is deliberately agnostic to.
    one = next(iter(resolved))
    assert (one.canonical, one.lean) == ("New York Times", -1.0)


def test_corpus_parenthetical_suffixes(reg):
    """Qbias labels outlets 'Fox News (Online News)', 'Wall Street Journal (News)', etc. The
    loader strips parentheticals, so they match the canonical outlet."""
    assert reg.resolve("Fox News (Online News)").canonical == "Fox News"
    assert reg.resolve("CNN (Online News)").canonical == "CNN"
    assert reg.resolve("New York Post (News)").canonical == "New York Post"
    assert reg.resolve("USA TODAY").canonical == "USA Today"           # all-caps corpus form


def test_subdomains_and_multi_tld(reg):
    assert reg.resolve("https://edition.cnn.com/2026/x").canonical == "CNN"
    assert reg.resolve("https://amp.theguardian.com/us-news/x").canonical == "The Guardian"
    assert reg.resolve("https://www.bbc.co.uk/news/uk-123").canonical == "BBC"
    assert reg.resolve("bbc.co.uk").canonical == "BBC"


def test_abbreviations_and_variants(reg):
    assert reg.resolve("WaPo").canonical == "Washington Post"
    assert reg.resolve("AP").canonical == "Associated Press"
    assert reg.resolve("Huffington Post").canonical == "HuffPost"
    assert reg.resolve("The Daily Wire").canonical == "Daily Wire"


def test_case_and_whitespace_tolerant(reg):
    assert reg.resolve("  fox news  ").canonical == "Fox News"
    assert reg.resolve("FOX NEWS").canonical == "Fox News"
    assert reg.resolve("bit of noise") is None


def test_unknown_outlets_are_none_and_nan(reg):
    assert reg.resolve("some-local-blog.example") is None
    assert reg.resolve("https://not-a-known-outlet.example/x") is None
    assert reg.resolve("") is None and reg.resolve(None) is None
    assert math.isnan(reg.lean("some-local-blog.example"))
    assert math.isnan(reg.lean(None))


def test_lean_spans_the_spectrum(reg):
    assert reg.lean("Mother Jones") == -2.0        # left
    assert reg.lean("npr.org") == -1.0             # lean left
    assert reg.lean("reuters.com") == 0.0          # center
    assert reg.lean("wsj.com") == 1.0              # lean right
    assert reg.lean("foxnews.com") == 2.0          # right


def test_registry_integrity(reg):
    outs = reg.outlets()
    assert len(outs) == len(reg) >= 40             # the seeded AllSides table + extension outlets
    canon = [o.canonical for o in outs]
    assert len(canon) == len(set(canon))           # canonical names are unique
    rated = [o for o in outs if math.isfinite(o.lean)]
    assert len(rated) >= 40                        # the rated core never shrinks
    for o in outs:
        assert o.canonical
        if math.isfinite(o.lean):
            assert -2.0 <= o.lean <= 2.0
        else:
            # An unrated row must EARN its place. Two things qualify, and both are curated FACTS
            # rather than guesses: a home locality, or a kind (``wire`` — a machine-generated
            # market-data feed, not a news outlet). A row with a blank lean and neither of those
            # asserts nothing and should not exist.
            assert o.country or o.kind, f"{o.canonical}: unrated row with no locality and no kind"
    # rated ordered by (lean, name); locality-only rows deterministically last by name
    assert rated == sorted(rated, key=lambda o: (o.lean, o.canonical))
    unrated = outs[len(rated):]
    assert all(math.isnan(o.lean) for o in unrated)
    assert unrated == sorted(unrated, key=lambda o: o.canonical)


def test_all_extension_outlets_are_distinct_and_present(reg):
    """The 23 extension domains cover 21 distinct outlets, all in the registry."""
    canon = {reg.canonical(d) for d in EXTENSION_DOMAINS}
    assert None not in canon
    assert canon <= {o.canonical for o in reg.outlets()}


def test_module_level_helpers_and_caching():
    a = orx.default_registry()
    b = orx.default_registry()
    assert a is b                                   # built once, cached
    assert orx.resolve("cnn.com").canonical == "CNN"
    assert orx.resolve("nope.example") is None


def test_bare_domain_with_path_no_scheme(reg):
    assert reg.resolve("nytimes.com/2026/us/politics/x").canonical == "New York Times"
    assert reg.resolve("foxnews.com/politics").canonical == "Fox News"


# --------------------------------------------------------------------------- #
# Registry lint (W4 maintainability) — CSV well-formedness, read-only.
# --------------------------------------------------------------------------- #
def test_lint_passes_on_the_bundled_registry():
    """A CI tripwire: the shipped registry is well-formed, so lint returns no issues."""
    assert orx.lint_registry() == []


def test_lint_catches_every_defect_class(tmp_path):
    """Invalid lean, duplicate canonical, duplicate alias, and a malformed row are each reported."""
    csv = tmp_path / "reg.csv"
    csv.write_text("canonical,lean,aliases\n"
                   "Foo,9,foo.com\n"       # invalid lean (outside [-2, 2])
                   "Foo,-1,bar.com\n"      # duplicate canonical (Foo again)
                   "Baz,-1,foo.com\n"      # duplicate alias (foo.com already -> Foo)
                   "OnlyOneColumn\n",      # malformed row
                   encoding="utf-8")
    codes = {i["code"] for i in orx.lint_registry(str(csv))}
    assert {"invalid_lean", "duplicate_canonical", "duplicate_alias", "malformed_row"} <= codes


def test_lint_warns_on_repeated_alias_in_a_row(tmp_path):
    csv = tmp_path / "reg.csv"
    csv.write_text("canonical,lean,aliases\nFoo,-1,foo.com|foo.com\n", encoding="utf-8")
    issues = orx.lint_registry(str(csv))
    assert any(i["code"] == "repeated_alias_in_row" and i["severity"] == "warning" for i in issues)


def test_lint_never_raises_on_a_broken_file(tmp_path):
    """The whole point: it diagnoses a file too broken for ``load`` to parse, without raising."""
    csv = tmp_path / "reg.csv"
    csv.write_text("canonical,lean,aliases\nGood,-2,good.com\nBadLeanRow,not-a-number,x.com\n",
                   encoding="utf-8")
    issues = orx.lint_registry(str(csv))
    assert any(i["code"] == "invalid_lean" for i in issues)


# --------------------------------------------------------------------------- #
# Locality-only rows (Signal Integrity M1): identity + home are curated facts, lean stays unrated.
# --------------------------------------------------------------------------- #
def test_locality_only_row_loads_resolves_and_stays_unrated(tmp_path):
    """A BLANK lean is a deliberate locality-only row: the outlet resolves (canonical name,
    aliases, home country/scope) while its lean is NaN — the exact 'unknown' convention the
    scorer already speaks, so downstream L2.2 nulls apply with no special-casing."""
    csv = tmp_path / "reg.csv"
    csv.write_text("canonical,lean,aliases,country,region,city,scope\n"
                   "Daily Nation,,nation.africa,KE,,,national\n"
                   "NPR,-1,npr.org,US,,,national\n",
                   encoding="utf-8")
    reg = orx.OutletRegistry.load(str(csv))
    o = reg.resolve("nation.africa")
    assert o is not None and o.canonical == "Daily Nation"
    assert math.isnan(o.lean)                               # unrated, never a defaulted centre
    assert o.country == "KE" and o.scope == "national"
    assert math.isnan(reg.lean("https://nation.africa/kenya/news/x"))
    assert reg.resolve("npr.org").lean == -1                # rated rows unchanged


def test_outlets_ordering_is_deterministic_with_unrated_rows(tmp_path):
    """Rated rows keep the lean-then-name order; locality-only rows sort last by name (NaN keys
    would otherwise make the listing order-unstable)."""
    csv = tmp_path / "reg.csv"
    csv.write_text("canonical,lean,aliases\n"
                   "Zeta Unrated,,z.example\n"
                   "Alpha Unrated,,a.example\n"
                   "Righty,1.5,r.example\n"
                   "Lefty,-1.5,l.example\n",
                   encoding="utf-8")
    names = [o.canonical for o in orx.OutletRegistry.load(str(csv)).outlets()]
    assert names == ["Lefty", "Righty", "Alpha Unrated", "Zeta Unrated"]


def test_lint_accepts_blank_lean_but_rejects_nan_spelling(tmp_path):
    """Blank = unrated (legal). Writing 'NaN'/garbage is a data error, not a way to say unrated."""
    csv = tmp_path / "reg.csv"
    csv.write_text("canonical,lean,aliases\n"
                   "Locality Only,,lo.example\n"
                   "Bad,NaN,bad.example\n",
                   encoding="utf-8")
    issues = orx.lint_registry(str(csv))
    lines = {i["line"] for i in issues if i["code"] == "invalid_lean"}
    assert lines == {3}                                     # only the NaN row; blank row is clean


def test_wire_rows_are_marked_and_resolve_from_every_form(reg):
    """The five machine-generated market-data feeds found in production, reachable by the display
    name the feed path actually supplies as well as by domain."""
    for form in ["Lulegacy", "Marketbeat.Com", "marketbeat.com", "Americanbankingnews",
                 "Markets Daily", "Tickerreport.Com", "tickerreport.com"]:
        assert reg.is_wire(form), form


def test_news_outlets_are_not_wire(reg):
    for form in ["BBC News", "nytimes.com", "Fox News", "Reuters"]:
        assert not reg.is_wire(form), form


def test_an_unknown_outlet_is_not_wire(reg):
    """Absence of a row is unrated, never disqualified — the long tail must keep clustering."""
    assert not reg.is_wire("Some Local Gazette")
    assert not reg.is_wire(None)


def test_the_measured_alias_gap_is_closed(reg):
    """The one alias gap the identity audit found: Daily Mail is rated and was aliased only to its
    UK domain, while the feed sends the US one. 21 articles of a rated outlet counted as unrated."""
    assert reg.resolve("Dailymail.Com").canonical == "Daily Mail"
    assert reg.resolve("dailymail.co.uk").canonical == "Daily Mail"


def test_press_release_wires_are_marked(reg):
    """Found the same way as the market-data feeds, and the row does two jobs: it keeps press
    releases out of stories AND settles an identity the heuristic could not."""
    for form in ["Pr Newswire", "Prnewswire.Com", "prnewswire.co.uk",
                 "Globenewswire.Com", "Globe Newswire"]:
        assert reg.is_wire(form), form


def test_the_french_globenewswire_feed_label_is_the_same_wire(reg):
    """`Globenewswire_fr` is the French feed's provider KEY, not a domain — 33 articles in the
    2026-08-08 window arrived under it, every one on a www.globenewswire.com URL, so `is_wire_url`
    already kept them out of stories (measured: 191/191 across both name forms, 0 in stories).
    The alias settles IDENTITY: the coverage audits stop listing an untracked 33-article outlet,
    and an ingest under the label scores as the canonical wire instead of the raw string."""
    assert reg.resolve("Globenewswire_fr").canonical == "GlobeNewswire"
    assert reg.is_wire("Globenewswire_fr")


def test_syndicated_obituary_feeds_are_marked_wire(reg):
    """The obituary half of the template class (docs/CONTENT_MILL_STORY_EVALUATION.md: 41 stories /
    398 articles, 5.3% of covered), curated at the source instead of by a per-cluster threshold —
    a/p was measured against the whole catalog and rejected twice at 0% precision / 0% recall.

    Reachable by URL, by bare domain, and by the title-cased name form the feed path supplies,
    because the catalog carries all three."""
    for form in ["https://obits.oregonlive.com/us/obituaries/oregonian/name/jane-doe",
                 "obits.oregonlive.com", "Obits.Oregonlive",
                 "https://obits.lehighvalleylive.com/us/obituaries/lehighvalley/name/j-doe",
                 "obits.lehighvalleylive.com", "Obits.Lehighvalleylive",
                 "obituaries.albanyherald.com", "obituaries.paloaltoonline.com"]:
        assert reg.is_wire(form), form


def test_is_wire_url_reaches_a_feed_the_publisher_name_hides(reg):
    """The measured gap: an article ingested before its feed was curated keeps the canonical name
    the registry gave it THEN, so 499 of 671 obituary articles are stored as `The Oregonian` /
    `The Express-Times` with an `obits.*` URL. The publisher string is not wrong — it is just not
    the whole identity, and the URL still carries the feed's own host."""
    for u in ["https://obits.oregonlive.com/us/obituaries/oregonian/name/jane-doe",
              "https://obits.lehighvalleylive.com/us/obituaries/lehighvalley/name/j-doe",
              "https://obituaries.albanyherald.com/obituary/someone",
              "https://obituaries.paloaltoonline.com/obituary/someone",
              "https://www.prnewswire.com/news-releases/thing.html"]:
        assert reg.is_wire_url(u), u


def test_is_wire_url_never_touches_the_newsroom_it_sits_under(reg):
    """The property that keeps this strictly narrowing. `obits.oregonlive.com` is wire and
    `oregonlive.com` is a rated newspaper — the predicate must split them on the host, or the
    gate deletes The Oregonian from every story in the catalog."""
    for u in ["https://www.oregonlive.com/politics/2026/08/story.html",
              "https://www.lehighvalleylive.com/news/2026/08/story.html",
              "https://www.bbc.co.uk/news/123", "https://nytimes.com/2026/08/08/us/x.html"]:
        assert not reg.is_wire_url(u), u


def test_is_wire_url_is_inert_on_anything_that_is_not_a_url(reg):
    """It is handed `a.get("url")` from a catalog row, so it must be incapable of misfiring on a
    display name, an empty column or a null."""
    for v in [None, "", "   ", "The Oregonian", "not a url"]:
        assert not reg.is_wire_url(v), repr(v)
    # A host-LIKE name form is the deliberate exception, not a leak: `Obits.Oregonlive` is a
    # curated alias of the feed, so answering True is the same answer `is_wire` gives it. The
    # predicate narrows on identity, and this string carries one.
    assert reg.is_wire_url("Obits.Oregonlive") and reg.is_wire("Obits.Oregonlive")


def test_the_obituary_rows_do_not_reach_a_name_that_merely_contains_obit(reg):
    """`diariobitcoin.com` — diari-OBIT-coin — surfaced as a false positive when the live catalog
    was queried with a `%obit%` LIKE. These rows are exact aliases, not a substring rule, so it
    cannot happen here; this pins that the day someone is tempted by a pattern."""
    assert not reg.is_wire("diariobitcoin.com")


def test_curating_an_obituary_subdomain_leaves_its_newspaper_untouched(reg):
    """**The property that makes the row safe, and the one a careless edit would break.**

    Resolution matches by registrable-domain SUFFIX and is subdomain-tolerant, so before these rows
    `obits.oregonlive.com` resolved to The Oregonian — which is precisely why the evaluation
    measured that masthead at 90% "mill share". A newspaper was being credited with its
    syndication partner's obituary feed.

    The fix has to be surgical: the longer alias must win over the shorter suffix, moving ONLY the
    obituaries. Both papers keep their identity, their sourced lean and their clustering. Widening
    either row to the bare domain would silently delete a rated regional newspaper from every
    story in the catalog, and nothing else in the suite would notice."""
    oregonian = reg.resolve("https://www.oregonlive.com/politics/2026/08/story.html")
    assert oregonian.canonical == "The Oregonian" and oregonian.lean == 0.0
    assert not reg.is_wire("oregonlive.com") and not reg.is_wire("The Oregonian")

    express = reg.resolve("https://www.lehighvalleylive.com/news/2026/08/story.html")
    assert express.canonical == "The Express-Times" and express.lean == -1.0
    assert not reg.is_wire("lehighvalleylive.com") and not reg.is_wire("The Express-Times")

    # And the obituaries really did move off those mastheads, rather than merely gaining a flag.
    assert reg.resolve("obits.oregonlive.com").canonical == "OregonLive Obituaries"
    assert reg.resolve("obits.lehighvalleylive.com").canonical == "Lehigh Valley Live Obituaries"


def test_only_the_measured_obituary_feeds_are_curated(reg):
    """The eight other sources the evaluation lists are REAL NEWSROOMS whose ingested feed was
    template-heavy in one window. `kind=wire` is an identity claim about the source, and a
    one-window share is not evidence for it — MLive is the representative publisher of a real
    21-article / 18-publisher story in the 2026-08-08 audit. They stay uncurated until per-source
    article evidence exists; this test fails if someone adds them on the strength of the share
    alone."""
    for form in ["Mlive", "mlive.com", "Wkyc", "wkyc.com", "Daytondailynews",
                 "Springfieldnewssun", "Nwfdailynews", "Sportskeeda", "Seeking Alpha"]:
        assert not reg.is_wire(form), form


def test_contested_brand_words_are_settled_by_curation(reg):
    """ESPN and The Motley Fool each run more than one national domain, so a bare name could not be
    placed without guessing which. A curated row settles WHO the outlet is — identity first, and a
    rating only when one is sourced.

    Both have since been rated, which is why this now asserts only on IDENTITY. That is the point:
    an identity-only row is a stage, not a verdict, and the collapse it buys has to keep working
    across the transition. The unrated case is covered by the rows that are still blank, below."""
    for form in ["ESPN", "Espn.Com", "Espn.Ph", "espndeportes.espn.com"]:
        assert reg.resolve(form).canonical == "ESPN", form
    for form in ["Fool", "Fool.Com", "fool.co.uk"]:
        assert reg.resolve(form).canonical == "The Motley Fool", form
    assert reg.resolve("Fool").country == "US"


def test_curation_removed_the_last_unplaceable_names():
    """The audit's worklist was exactly three names. All three now RESOLVE, so none of them reaches
    the brand-label heuristic at all.

    Asserted against the audit's report rather than raw ``ambiguous_labels``: a label stays
    contested whether or not its domains are curated (that is what stops curation quietly licensing
    a guess — see test_curating_one_domain_does_not_uncontest_the_word). What curation removes is
    the NAME from the unplaceable list, because it no longer needs placing."""
    import audit_publisher_identity as api
    names = ["Espn.Com", "Espn.Ph", "ESPN", "Fool", "Fool.Com", "Fool.Co.Uk",
             "Pr Newswire", "Prnewswire.Com", "Prnewswire.Co.Uk"]
    story = {"title": "t", "totalCoverage": len(names),
             "coverage": [{"publisher": p, "url": f"u{i}"} for i, p in enumerate(names)]}
    assert api.analyse([story])["ambiguous"] == []


def test_curated_leans_for_the_measured_registry_gaps(reg):
    """Outlets the publisher-identity audit surfaced as having no row at all. Every value is Media
    Bias/Fact Check's published classification mapped to this file's -2..+2 scale, not an
    impression: two came back differently from what a guess would have produced."""
    assert reg.lean("Variety.Com") == -1.0                    # MBFC Left-Center
    assert reg.lean("Inquirer.Com") == -1.0                   # MBFC Left-Center
    assert reg.lean("Winnipegfreepress.Com") == 0.0           # MBFC LEAST BIASED, not left
    assert reg.lean("Manilatimes.Net") == 1.0                 # MBFC Right-Center
    assert reg.lean("Thewest.Com.Au") == 1.0                  # MBFC Right-Center
    assert reg.lean("Thestar.Com.My") == 2.0                  # MBFC RIGHT


def test_brisbane_times_stays_unrated(reg):
    """MBFC has no page for it. It shares an owner with the Sydney Morning Herald and The Age, and
    inheriting a sibling masthead's rating is exactly the guess this file refuses (L2.2). The row
    exists for identity and locality only."""
    import math
    o = reg.resolve("Brisbanetimes.Com.Au")
    assert o.canonical == "Brisbane Times" and math.isnan(o.lean)
    assert o.country == "AU" and o.city == "Brisbane"


def test_the_two_star_mastheads_do_not_collide(reg):
    """Toronto Star is thestar.com; the Malaysian Star is thestar.com.my. Different registrable
    domains, opposite ends of the scale — a collision here would mislabel one of them."""
    assert reg.resolve("thestar.com").canonical == "Toronto Star"
    assert reg.resolve("thestar.com.my").canonical == "The Star (Malaysia)"
    assert reg.lean("thestar.com") == 0.0 and reg.lean("thestar.com.my") == 2.0


def test_the_two_inquirers_do_not_collide(reg):
    """inquirer.com is Philadelphia; inquirer.net is the Philippine Daily Inquirer. Both are now
    rated Left-Center by MBFC, which makes the collision INVISIBLE in the lean and all the more
    worth pinning: they are different newspapers on different continents."""
    assert reg.resolve("inquirer.com").canonical == "Philadelphia Inquirer"
    assert reg.resolve("inquirer.net").canonical == "Philippine Daily Inquirer"
    assert reg.resolve("inquirer.com").country == "US"
    assert reg.resolve("inquirer.net").country == "PH"


def test_the_unlock_worklist_ratings(reg):
    """The second curation pass. These were not chosen by article volume but by the audit's UNLOCK
    worklist — the unrated outlets that would actually complete a coverage-gap claim (a claim needs
    >= 3 rated publishers), so a rating here changes what the product can SAY, not just what it
    knows. Every value is MBFC's published classification on this file's -2..+2 scale."""
    assert reg.lean("Pagesix.Com") == 1.0                     # MBFC Right-Center
    assert reg.lean("Espn.Com") == -1.0                       # MBFC Left-Center
    assert reg.lean("Inquirer.Net") == -1.0                   # MBFC Left-Center
    assert reg.lean("Ynetnews.Com") == -1.0                   # MBFC Left-Center
    assert reg.lean("Thenews.Com.Pk") == 1.0                  # MBFC Right-Center
    assert reg.lean("Standard.Co.Uk") == 1.0                  # MBFC Right-Center


def test_page_six_is_rated_apart_from_its_owner(reg):
    """Page Six is the New York Post's gossip desk and shares its newsroom, but MBFC rates it
    Right-Center where the Post is Right. Inheriting the owner's rating would have been the guess;
    a separate row is the reason the file has rows."""
    assert reg.resolve("Pagesix.Com").canonical == "Page Six"
    assert reg.resolve("Nypost.Com").canonical == "New York Post"
    assert reg.lean("Pagesix.Com") == 1.0 and reg.lean("Nypost.Com") == 2.0


def test_the_two_standards_do_not_collide(reg):
    """standard.co.uk is the London Evening Standard; standard.net.au is the Warrnambool Standard,
    a Victorian local paper with no rating anywhere. Rating one must never reach the other — the
    bare brand word 'standard' is the exact false positive that forced the brand-DOMAIN key."""
    assert reg.resolve("Standard.Co.Uk").canonical == "London Evening Standard"
    assert reg.resolve("standard.co.uk").country == "GB"
    assert reg.resolve("Standard.Net.Au") is None
    assert reg.resolve("Standard") is None


def test_the_blank_lean_sweep(reg):
    """Third pass: every row in the file that carried no lean was looked up rather than only the
    ones with traffic. Fourteen of twenty had a published rating. Two came back differently from
    what a guess would have produced — Mail & Guardian is the only LEFT in three passes, and NHK is
    rated right-of-centre, which few would predict of a public broadcaster."""
    assert reg.lean("Spiegel.De") == -1.0                     # MBFC Left-Center
    assert reg.lean("Zeit.De") == -1.0                        # MBFC Left-Center
    assert reg.lean("Sueddeutsche.De") == -1.0                # MBFC Left-Center
    assert reg.lean("France24.Com") == 0.0                    # MBFC LEAST BIASED
    assert reg.lean("Mirror.Co.Uk") == -1.0                   # MBFC Left-Center
    assert reg.lean("Nzherald.Co.Nz") == 0.0                  # MBFC LEAST BIASED
    assert reg.lean("Nhk.Or.Jp") == 1.0                       # MBFC Right-Center
    assert reg.lean("Asia.Nikkei.Com") == 1.0                 # MBFC Right-Center
    assert reg.lean("Punchng.Com") == -1.0                    # MBFC Left-Center
    assert reg.lean("Mg.Co.Za") == -2.0                       # MBFC LEFT
    assert reg.lean("English.Ahram.Org.Eg") == 1.0            # MBFC Right-Center
    assert reg.lean("Clarin.Com") == 1.0                      # MBFC Right-Center
    assert reg.lean("Lanacion.Com.Ar") == 1.0                 # MBFC Right-Center
    assert reg.lean("Fool.Com") == -1.0                       # MBFC Left-Center
    assert reg.lean("Yahoo.Com") == -1.0                      # MBFC Left-Center
    assert reg.lean("Clickondetroit.Com") == 0.0              # MBFC LEAST BIASED


def test_o_globo_is_not_rated_from_the_group_that_owns_it(reg):
    """MBFC rates GLOBO, the parent, not the newspaper. Reading a masthead's lean off its owner is
    the inference refused one commit earlier for Page Six, whose rating turned out a notch away from
    the New York Post's. Refusing it in the direction that costs coverage is the only way the
    refusal means anything."""
    import math
    o = reg.resolve("Oglobo.Globo.Com")
    assert o.canonical == "O Globo" and math.isnan(o.lean) and o.country == "BR"


def test_the_unrated_set_is_exactly_the_documented_one(reg):
    """A guard, not a fact: every blank lean in the file must be one the comments give a reason for.
    Adding a row with no rating is legal (identity and locality are curated facts) — adding one
    silently is what this catches. Wire rows are blank by construction: a machine-generated
    market-data feed has no editorial stance to rate."""
    import math
    blank = {o.canonical for o in reg.outlets() if math.isnan(o.lean)}
    # Any `kind` at all excuses a blank lean, not just `wire`. A source that is not a newsroom —
    # an aggregator, a journal, a forum, an organisation — is not a curation gap, and the kind
    # column is where that judgement is recorded. Only rows with NO kind have to be justified here.
    typed = {o.canonical for o in reg.outlets() if o.kind}
    assert typed <= blank, "a non-newsroom source must never carry a lean"
    assert blank - typed == {
        # No rating exists at any public rater. This set is now ONLY that — the eight outlets whose
        # lean was withheld for lack of a credibility column are rated, and carry the caveat in it.
        "Brisbane Times",      # no MBFC page; sibling mastheads are rated, which is not a source
        "Folha de S.Paulo",    # confirmed absent from MBFC, AllSides and Ad Fontes alike
        "Milenio",             # no MBFC page
        "Nigerian Tribune",    # no MBFC page
        "O Globo",             # only its owner is rated
        "The East African",    # only its owner, Nation Media Group, is rated
        "WAtoday",             # no MBFC page; Biasly's own summary contradicts itself
        # A THIRD reason for a blank lean: rated, and rated OFF the left-right axis. MBFC puts
        # MedPage Today in Pro-Science, the category it gives Nature and Frontiers and states is not
        # on the scale. Unlike them it gets no `kind` — it is a newsroom, so the reason is the
        # RATER's placement, not what the source is.
        "MedPage Today",
        # Not outlets to rate at all — identity is the whole reason each row exists.
        "BelTA",               # Belarus state agency; press-freedom scores are not an outlet lean
        "iHeartRadio",         # no rating for the network; the row names ~111 station hostnames
        # A FOURTH reason: a rating exists but not from a source this file accepts. "A number exists
        # somewhere" has never been the bar, and writing these down as blanks is what keeps that
        # true when the number is easy to find and would fill a hole.
        "Otago Daily Times",   # no MBFC page; Ground News reports Center
        "InDaily",             # no MBFC page; Biasly reports Center
        "Interest.co.nz",      # no MBFC page
        # A FIFTH: rated by MBFC, and MBFC says the rating is LOW CONFIDENCE because it could not
        # fully apply its methodology. There is no confidence column here, so importing the number
        # would present a hedge as settled. Same rule as Billboard and Hankyoreh.
        "The Saturday Paper",
        # Ordinary absence — Ad Fontes, AllSides and MBFC all have nothing.
        "Belfast Telegraph",
        # A SIXTH reason, and the one most easily mistaken for the first: the MBFC page EXISTS and
        # could not be read. The fetcher gets 403 and search returned only prose without the
        # categorical label this file maps from. A rating out of reach is not a rating that does not
        # exist, and the difference is what tells a future curator this row is retrievable.
        "Aberdeen Press and Journal",
        # The sixth tranche's identity-only rows (2026-08-23, the unlocks pass + international
        # majors). Each exists because a high-volume identity was resolving to a bare domain;
        # none has a usable rating at a rater this file accepts. The tranche comment in the CSV
        # carries the per-row provenance; the reasons here are the categorical ones.
        "NBC Sports",          # sports desk; NBC News's rating belongs to NBC News (the O Globo rule)
        "Sky Sports",          # sports desk; Sky News's rating likewise stays where it is
        "Sporting News",       # sports desk, no rater page
        "Field Gulls",         # single-team SB Nation fan site, no rater page
        "BBC Sky at Night Magazine",  # hobbyist astronomy magazine, no rater page
        "WVTM 13",             # Birmingham NBC affiliate, no rater page; locality is the fact
        "Temple Daily Telegram",  # Temple, TX daily, no rater page; locality is the fact
        "MyJoyOnline",         # Ghanaian portal (Joy FM), no rater page
        "NL Times",            # English-language Dutch site, no rater page
        "Thai Rath",           # Thailand's largest daily, no rater page
        "Dinamalar",           # Tamil daily; tranche five recorded "no MBFC page", now identity-only
        "PerthNow",            # no rater page; locality is the fact
        "BioBioChile",         # Chilean radio network's portal, no rater page
        "ETtoday",             # Taiwanese portal, no rater page
        "Obozrevatel",         # Ukrainian portal, no rater page
        "Index.hu",            # Hungarian portal, no rater page
        "G1",                  # only Globo, the parent group, is rated — the O Globo refusal again
        "CafeF",               # Vietnamese business portal, no rater page
        "CafeBiz",             # Vietnamese business portal, no rater page
        "Soha",                # Vietnamese portal, no rater page
        # The Billboard/Saturday Paper rule, third occurrence: AllSides rates Hankyoreh Left but
        # marks its own confidence LOW (initial, May 2026), and there is no confidence column here.
        "The Hankyoreh",
    }


def test_the_coverage_pass_ratings(reg):
    """Fourth pass: outlets with no row at all, rather than blanks in rows that existed. Large
    mastheads a global feed carries and this file had simply never listed."""
    assert reg.lean("Newsweek.Com") == 1.0                    # MBFC Right-Center
    assert reg.lean("Thesun.Co.Uk") == 2.0                    # MBFC RIGHT
    assert reg.lean("Theaustralian.Com.Au") == 1.0            # MBFC Right-Center
    assert reg.lean("Nationalpost.Com") == 1.0                # MBFC Right-Center
    assert reg.lean("Globalnews.Ca") == -1.0                  # MBFC Left-Center
    assert reg.lean("Indiatoday.In") == 1.0                   # MBFC Right-Center
    assert reg.lean("Afp.Com") == -1.0                        # MBFC Left-Center
    assert reg.lean("Rollingstone.Com") == -2.0               # MBFC LEFT
    assert reg.lean("Metro.Co.Uk") == -1.0                    # MBFC Left-Center
    assert reg.lean("Express.Co.Uk") == 2.0                   # MBFC RIGHT
    assert reg.lean("Rnz.Co.Nz") == 0.0                       # MBFC LEAST BIASED
    assert reg.lean("Oregonlive.Com") == 0.0                  # MBFC LEAST BIASED
    assert reg.lean("Barrons.Com") == 1.0                     # MBFC Right-Center
    assert reg.lean("Aa.Com.Tr") == 2.0                       # MBFC RIGHT


def test_the_us_metro_dailies(reg):
    """Twenty of the largest US city papers had no row at all — a real hole for a product whose
    claim is showing who covered a story, because a US story reaching five metro dailies was
    reaching five UNRATED publishers and could not support a coverage-gap claim.

    The three Right-Center ones are asserted individually because they are the surprising ones. A
    big-city daily rated RIGHT-Center is counterintuitive, and it is MBFC's rating, not an
    impression — the same reason NHK is +1."""
    assert reg.lean("Chicagotribune.Com") == 1.0              # MBFC Right-Center
    assert reg.lean("Dallasnews.Com") == 1.0                  # MBFC Right-Center
    assert reg.lean("Detroitnews.Com") == 1.0                 # MBFC Right-Center
    for host in ["Bostonglobe.Com", "Miamiherald.Com", "Houstonchronicle.Com", "Seattletimes.Com",
                 "Sfchronicle.Com", "Sfgate.Com", "Denverpost.Com", "Startribune.Com", "Ajc.Com",
                 "Freep.Com", "Azcentral.Com", "Tampabay.Com", "Newsday.Com", "Stltoday.Com",
                 "Cleveland.Com", "Sacbee.Com", "Charlotteobserver.Com", "Kansascity.Com"]:
        assert reg.lean(host) == -1.0, host                   # MBFC Left-Center


def test_the_two_detroit_papers_are_rated_apart(reg):
    """One city, two dailies, opposite sides. The Free Press has endorsed Democrats since 1980; the
    Detroit News has never endorsed one for president. Collapsing them on the city would erase the
    only interesting thing about the pair."""
    assert reg.resolve("Freep.Com").canonical == "Detroit Free Press"
    assert reg.resolve("Detroitnews.Com").canonical == "The Detroit News"
    assert reg.lean("Freep.Com") == -1.0 and reg.lean("Detroitnews.Com") == 1.0
    assert reg.resolve("Freep.Com").city == reg.resolve("Detroitnews.Com").city == "Detroit"


def test_a_masthead_rating_reaches_that_mastheads_own_website(reg):
    """cleveland.com is where The Plain Dealer publishes and chron.com is where the Houston
    Chronicle publishes — the same publication under its own domain, which is NOT the ownership
    inference refused for Page Six and O Globo. Both are pinned because Ad Fontes rates the website
    separately from the paper, so the two are easy to mistake for different outlets."""
    assert reg.resolve("Cleveland.Com").canonical == "The Plain Dealer"
    assert reg.resolve("Chron.Com").canonical == "Houston Chronicle"
    assert reg.lean("Chron.Com") == reg.lean("Houstonchronicle.Com") == -1.0


def test_sfgate_is_its_own_outlet(reg):
    """MBFC rates SFGate and the San Francisco Chronicle separately. They agree at Left-Center here,
    which is exactly why a shared row would look harmless — until one of them is re-rated."""
    assert reg.resolve("Sfgate.Com").canonical == "SFGate"
    assert reg.resolve("Sfchronicle.Com").canonical == "San Francisco Chronicle"


def test_the_uk_canada_australia_india_tranche(reg):
    """The next tranches of the same coverage audit. India is worth noting: MBFC rates three of the
    four Right-Center for the same stated reason — coverage favouring the ruling party. That is a
    property of the market, not of this file."""
    for host, lean in [
        ("Inews.Co.Uk", -1.0), ("Newstatesman.Com", -1.0), ("Heraldscotland.Com", -1.0),
        ("Scotsman.Com", 0.0), ("Spectator.Co.Uk", 1.0),
        ("Financialpost.Com", 1.0), ("Montrealgazette.Com", 1.0), ("Vancouversun.Com", 1.0),
        ("Torontosun.Com", 2.0),
        ("Sbs.Com.Au", -1.0), ("Thenewdaily.Com.Au", -1.0), ("Crikey.Com.Au", -2.0),
        ("7News.Com.Au", 1.0), ("9News.Com.Au", 1.0), ("Afr.Com", 1.0),
        ("Scroll.In", -1.0), ("Business-Standard.Com", 1.0), ("Firstpost.Com", 1.0),
        ("Theprint.In", 1.0),
    ]:
        assert reg.lean(host) == lean, host


def test_the_us_nationals_agencies_europe_and_latin_america(reg):
    """The last of the coverage audit's findable set."""
    for host, lean in [
        ("Newyorker.Com", -2.0), ("Thedailybeast.Com", -2.0), ("People.Com", -2.0),
        ("Rawstory.Com", -2.0), ("Commondreams.Org", -2.0),
        ("Propublica.Org", -1.0), ("Pbs.Org", -1.0), ("Hollywoodreporter.Com", -1.0),
        ("Voanews.Com", 0.0), ("Semafor.Com", 0.0), ("Themarshallproject.Org", 0.0),
        ("Theconversation.Com", 0.0),
        ("Thebulwark.Com", 1.0), ("Realclearpolitics.Com", 1.0),
        ("Theamericanconservative.Com", 1.0), ("Freebeacon.Com", 2.0),
        ("Aftonbladet.Se", -2.0), ("Liberation.Fr", -1.0), ("Lastampa.It", -1.0),
        ("Ilfattoquotidiano.It", -1.0), ("Nrc.Nl", -1.0),
        ("Welt.De", 1.0), ("Bild.De", 1.0), ("Leparisien.Fr", 1.0), ("Dn.Se", 1.0),
        ("Telegraaf.Nl", 2.0),
        ("Infobae.Com", -1.0), ("Reforma.Com", 1.0), ("Elfinanciero.Com.Mx", 1.0),
    ]:
        assert reg.lean(host) == lean, host


def test_the_news_agencies_are_all_least_biased(reg):
    """Four wires, all 0. Worth pinning precisely because it is unsurprising: a centre with real
    weight is what makes a lean distribution mean anything, and agencies are where it comes from.

    Note these are news AGENCIES, not the ``kind=wire`` rows — that flag means machine-generated
    market-data and press-release copy, which has no editorial stance to rate at all."""
    for host in ["Dpa.Com", "Efe.Com", "Pamediagroup.Com", "Kyodonews.Net",
                 "Apnews.Com", "Reuters.Com"]:
        assert reg.lean(host) == 0.0, host
        assert reg.resolve(host).kind != "wire", host


def test_the_dutch_and_swedish_pairs_straddle_the_centre(reg):
    """The most useful thing the European tranche buys. Each country lands a pair on opposite sides,
    so a Dutch or Swedish story can show a real spread instead of a row of unrated names."""
    assert reg.lean("Nrc.Nl") == -1.0 and reg.lean("Telegraaf.Nl") == 2.0
    assert reg.lean("Aftonbladet.Se") == -2.0 and reg.lean("Dn.Se") == 1.0


def test_the_two_toronto_dailies_are_rated_apart(reg):
    """Same shape as Detroit, wider gap: the Toronto Star is 0 and the Toronto Sun is +2. Both are
    reached by a `thestar`/`torontosun` brand word that shares nothing, so only the rows keep them
    apart — and the Star ALSO has to stay clear of the Malaysian Star, which is +2."""
    assert reg.resolve("Thestar.Com").canonical == "Toronto Star"
    assert reg.resolve("Torontosun.Com").canonical == "Toronto Sun"
    assert reg.lean("Thestar.Com") == 0.0 and reg.lean("Torontosun.Com") == 2.0
    assert reg.resolve("Thestar.Com.My").canonical == "The Star (Malaysia)"


def test_the_uk_spectator_is_not_the_us_one(reg):
    """MBFC rates The Spectator (UK), The Spectator (USA) and Spectator World as three outlets. Only
    the UK domain is aliased, so spectatorworld.com resolves to nothing rather than inheriting a
    rating written for a different magazine."""
    assert reg.resolve("Spectator.Co.Uk").canonical == "The Spectator (UK)"
    assert reg.resolve("Spectatorworld.Com") is None
    assert reg.resolve("Spectator.Us") is None


def test_a_bare_cbc_reaches_the_row_that_already_had_the_rating(reg):
    """An alias, not a rating: CBC News was rated all along and the feed's commonest form simply did
    not resolve. The cheapest class of fix in this file."""
    assert reg.resolve("CBC").canonical == "CBC News"
    assert reg.lean("CBC") == reg.lean("Cbc.Ca") == -1.0


def test_a_questionable_source_is_rated_and_flagged(reg):
    """What the credibility column bought. These eight carry a published MBFC lean AND an MBFC
    Questionable / Low Credibility verdict. With one column the only honest move was to withhold the
    lean — which threw away a true fact to avoid a misleading one. With two, the lean is recorded
    and the caveat travels with it."""
    import math
    for name, country in [("Xinhuanet.Com", "CN"), ("Globaltimes.Cn", "CN"), ("Rt.Com", "RU"),
                          ("Economictimes.Indiatimes.Com", "IN"), ("Dailystar.Co.Uk", "GB"),
                          ("Gbnews.Com", "GB"), ("Sputnikglobe.Com", "RU"), ("Tass.Com", "RU")]:
        o = reg.resolve(name)
        assert o is not None and not math.isnan(o.lean), name
        assert o.credibility == "low", name
        assert reg.is_low_credibility(name), name
        assert o.country == country, name


def test_blank_credibility_is_not_low(reg):
    """The asymmetry that keeps the column honest. Absence of a verdict never disqualifies an
    outlet, exactly as absence of a lean never centres one (L2.2). Only ~30 of 255 rows carry a
    verdict, so treating blank as suspect would silence almost the whole file."""
    for name in ["Reuters.Com", "Apnews.Com", "Bbc.Com", "Foxnews.Com"]:
        assert reg.credibility(name) is None, name
        assert not reg.is_low_credibility(name), name
    assert not reg.is_low_credibility("Somenewspapernobodyhascurated.Com")


def test_the_credibility_bar_is_the_raters_verdict_not_an_impression(reg):
    """State-aligned outlets MBFC rates at Medium or better vote normally. Without that constraint
    the column drifts into "outlets I distrust", which is the fabrication this file exists to
    prevent, pointed the other way."""
    for name in ["Dailysabah.Com", "English.Ahram.Org.Eg", "Aa.Com.Tr"]:
        assert not reg.is_low_credibility(name), name
    assert reg.credibility("English.Ahram.Org.Eg") == "medium"
    assert reg.credibility("Bostonglobe.Com") == "high"


def test_lint_rejects_a_credibility_value_outside_the_vocabulary(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("canonical,lean,aliases,country,region,city,scope,kind,credibility\n"
                 "Widget Times,1,widgettimes.com,US,,,national,,dubious\n", encoding="utf-8")
    codes = {i["code"] for i in orx.lint_registry(str(p))}
    assert "invalid_credibility" in codes


def test_lint_warns_when_low_credibility_has_no_lean_to_qualify(tmp_path):
    """A 'low' row with a blank lean asserts a caveat about a rating that is not there — almost
    always a half-finished edit, since the whole point of 'low' is to let the lean be recorded."""
    p = tmp_path / "r.csv"
    p.write_text("canonical,lean,aliases,country,region,city,scope,kind,credibility\n"
                 "Widget Times,,widgettimes.com,US,,,national,,low\n", encoding="utf-8")
    issues = orx.lint_registry(str(p))
    assert [i["code"] for i in issues] == ["unrated_low_credibility"]
    assert issues[0]["severity"] == "warning"


def test_a_row_written_before_the_column_existed_still_loads(tmp_path):
    """Trailing columns are optional and most rows in the bundled file stop at `scope`. A schema
    that broke them would have to rewrite 255 rows to add one field to eight."""
    p = tmp_path / "r.csv"
    p.write_text("canonical,lean,aliases\n"
                 "Widget Times,1,widgettimes.com\n"
                 "Widget Post,-1,widgetpost.com,US\n", encoding="utf-8")
    reg2 = orx.OutletRegistry.load(str(p))
    assert reg2.lean("widgettimes.com") == 1.0
    assert reg2.credibility("widgettimes.com") is None
    assert reg2.resolve("widgetpost.com").country == "US"
    assert orx.lint_registry(str(p)) == []


def test_the_questionable_line_is_mbfcs_own_flag_not_an_impression(reg):
    """State-aligned outlets that MBFC rates at Medium credibility or better ARE rated here. Without
    this the rule would drift into "outlets I distrust", which is the fabrication it exists to
    prevent, pointed the other way."""
    assert reg.lean("Dailysabah.Com") == 2.0                  # Turkish state-aligned, MBFC Right
    assert reg.lean("English.Ahram.Org.Eg") == 1.0            # Egyptian state-owned, MBFC RC
    assert reg.lean("Aa.Com.Tr") == 2.0                       # Turkish state agency, MBFC Right


def test_mastheads_that_share_a_name_across_countries_stay_apart(reg):
    """MBFC rates The Sun (UK) and The US Sun separately, and the Daily Express separately from The
    Express US. Only the domain that was actually rated is aliased — a bare thesun.com must not
    inherit the UK paper's RIGHT rating."""
    assert reg.resolve("Thesun.Co.Uk").canonical == "The Sun (UK)"
    assert reg.resolve("Thesun.Com") is None
    assert reg.resolve("Express.Co.Uk").canonical == "Daily Express"
    assert reg.resolve("The-Express.Com") is None


def test_the_economic_times_does_not_capture_the_times_of_india(reg):
    """Both live under indiatimes.com. Resolution walks registrable-domain suffixes longest-first,
    so the more specific subdomain wins. Both are rated +1 today, which is exactly what would make a
    collision invisible — the credibility verdicts differ, and only the rows keep them apart."""
    assert reg.resolve("Timesofindia.Indiatimes.Com").canonical == "The Times of India"
    assert reg.resolve("Economictimes.Indiatimes.Com").canonical == "The Economic Times"
    assert reg.credibility("Timesofindia.Indiatimes.Com") is None
    assert reg.credibility("Economictimes.Indiatimes.Com") == "low"


def test_every_yahoo_property_is_one_outlet(reg):
    """The alias covers yahoo.com, not just news.yahoo.com, so the finance/news/regional subdomains
    all land on one row. Deliberate: publisher_identity already collapses every yahoo.com host into
    a single publisher, so rating only news.yahoo.com would SPLIT the identity — one form keyed by a
    canonical, the other by its domain. The cost is that a Yahoo Finance article carries the Yahoo
    News lean, which is stated in the file."""
    for form in ["Yahoo.Com", "News.Yahoo.Com", "Finance.Yahoo.Com", "Sg.News.Yahoo.Com", "Yahoo"]:
        assert reg.resolve(form).canonical == "Yahoo News", form
    import publisher_identity
    g = publisher_identity.groups(["Yahoo.Com", "Finance.Yahoo.Com", "Yahoo Entertainment"])
    assert len(set(g.values())) == 1


def test_nhk_carries_the_rating_of_the_entity_its_domain_names(reg):
    """MBFC rates NHK (domestic) Right-Center and NHK World-Japan Left-Center — two points apart,
    both under nhk.or.jp. Resolution is host-based, so one row has to answer for both and it answers
    with the domestic rating. Pinned so the limitation is visible rather than discovered."""
    assert reg.resolve("Nhk.Or.Jp").canonical == "NHK"
    assert reg.lean("www3.nhk.or.jp/nhkworld/en/news/") == 1.0


def test_curating_one_domain_does_not_uncontest_the_word():
    """The regression this curation could have caused, and the reason _label_domains reads the form
    the FEED sent rather than the resolved canonical.

    Curating standard.co.uk moves that name off its domain token. Counting labels after resolution
    would then leave standard.net.au as the only 'standard' domain in the set — unambiguous — and a
    bare 'Standard' would be placed with the Warrnambool paper. Which brand words are contested is a
    fact about the domains, not about who has been curated."""
    import publisher_identity
    names = ["Standard.Co.Uk", "Standard.Net.Au", "Standard"]
    assert publisher_identity.ambiguous_labels(names) == {"standard"}
    g = publisher_identity.groups(names)
    assert len({g["Standard.Co.Uk"], g["Standard.Net.Au"], g["Standard"]}) == 3


# --------------------------------------------------------------------------- #
# A disambiguating parenthetical must not claim the bare word.
#
# `_name_key` drops parentheticals so a corpus label like "Fox News (Online News)" reaches Fox News.
# That is right for a SUFFIX and wrong for a DISAMBIGUATOR: `The Star (Malaysia)` normalised to the
# bare word `star` and claimed every feed's bare "The Star". Found in production — 4 articles.
# --------------------------------------------------------------------------- #
def test_a_bare_generic_name_no_longer_reaches_a_disambiguated_masthead(reg):
    """The live defect. A bare "The Star" resolved to the Malaysian paper: lean +2 instead of the
    Toronto Star's 0, and country MY instead of CA. Two points and a continent."""
    assert reg.resolve("The Star (Malaysia)").canonical == "The Star (Malaysia)"
    assert reg.resolve("thestar.com.my").canonical == "The Star (Malaysia)"
    assert reg.resolve("The Star") is None
    assert reg.resolve("Star") is None
    # The paper that WAS being mislabelled still resolves by its own name and domain.
    assert reg.resolve("thestar.com").canonical == "Toronto Star"
    assert reg.resolve("Toronto Star").lean == 0.0


def test_every_disambiguated_canonical_declines_the_bare_word(reg):
    """Not a one-off fix for one row: the rule is structural, so every parenthetical canonical in
    the file behaves the same way. Each of these bare words belongs to more than one real outlet."""
    for bare in ["Metro", "Vanguard", "Daily Star", "The Herald", "The Spectator"]:
        assert reg.resolve(bare) is None, bare
    for full, lean in [("Metro (UK)", -1.0), ("Vanguard (Nigeria)", -1.0),
                       ("Daily Star (UK)", 1.0), ("The Herald (Scotland)", -1.0),
                       ("The Spectator (UK)", 1.0)]:
        assert reg.resolve(full) is not None and reg.lean(full) == lean, full


def test_corpus_suffixes_still_resolve(reg):
    """The behaviour the parenthetical-stripping exists for, and which the fix must not break: a
    corpus label is a SUFFIX on a canonical that has none of its own."""
    assert reg.resolve("Fox News (Online News)").canonical == "Fox News"
    assert reg.resolve("Wall Street Journal (News)").canonical == "Wall Street Journal"
    assert reg.resolve("New York Post (News)").canonical == "New York Post"


def test_an_explicit_alias_still_wins_for_a_disambiguated_row(reg):
    """Declining the bare word is about the CANONICAL's normalisation, not about refusing short
    names. Where a bare form really is unambiguous it is written down and it resolves."""
    assert reg.resolve("RT").canonical == "RT (Russia Today)"
    assert reg.resolve("Russia Today").canonical == "RT (Russia Today)"
    assert reg.resolve("rt.com").canonical == "RT (Russia Today)"


def test_the_two_outlets_the_production_identity_audit_named(reg):
    """Found by the live audit rather than a probe list — both arrived under two name forms."""
    assert reg.lean("Fortune.Com") == 1.0 and reg.lean("Fortune") == 1.0   # MBFC RC (AllSides: C)
    o = reg.resolve("Watoday.Com.Au")
    assert o.canonical == "WAtoday" and math.isnan(o.lean)                 # no rating anywhere
    assert o.city == "Perth" and o.country == "AU"                         # locality earns the row


# --------------------------------------------------------------------------- #
# Ninth pass — driven by the LIVE production audit rather than a probe list.
# --------------------------------------------------------------------------- #
def test_the_production_measured_ratings(reg):
    """Every outlet here was measured in the clustering window: high-volume, or sitting in a story
    exactly one rating short. Curating from what the feed actually carries rather than from a guess
    about what it might."""
    for host, lean in [
        ("Theverge.Com", -1.0), ("King5.Com", 0.0), ("Masslive.Com", 0.0),
        ("Komonews.Com", 1.0), ("Mb.Com.Ph", -1.0), ("Koreatimes.Co.Kr", -1.0),
        ("Buzzfeed.Com", -1.0), ("Aol.Com", -1.0), ("Seekingalpha.Com", 1.0),
        ("Thestreet.Com", 1.0), ("Breakingnews.Ie", -1.0), ("Detik.Com", 0.0),
        ("Onlineathens.Com", -1.0), ("Lehighvalleylive.Com", -1.0),
        ("Dailybulletin.Com", 0.0), ("Ard.De", 0.0),
    ]:
        assert reg.lean(host) == lean, host


def test_editorial_subdomains_reach_their_paper(reg):
    """The audit found these as separate high-volume names — `obits.lehighvalleylive.com` at 76
    articles and `news.detik.com` at 28. Subdomain resolution means one row covers the masthead's
    whole surface, which is why the volume ranking and the registry disagree on how many outlets
    there are.

    **The obituary halves of this test were REVERSED on 2026-08-08, deliberately.** Folding
    `obits.*` into the masthead is right for identity coverage — it is what stopped 76 articles
    counting as an unknown outlet — and wrong for what the masthead then appears to publish:
    docs/CONTENT_MILL_STORY_EVALUATION.md measured The Oregonian at 90% "mill share" precisely
    because a newspaper was being credited with its syndication partner's obituary feed. The
    obituaries now carry their own `kind=wire` rows. Editorial subdomains like `news.detik.com`
    are unaffected — they really are the masthead."""
    assert reg.resolve("News.Detik.Com").canonical == "Detik"
    assert reg.resolve("Obits.Lehighvalleylive.Com").canonical == "Lehigh Valley Live Obituaries"
    assert reg.resolve("Obits.Oregonlive.Com").canonical == "OregonLive Obituaries"


def test_sportschau_reaches_ard_but_tagesschau_is_not_claimed(reg):
    """Sportschau is ARD's sports programme — the same broadcaster under its own programme name,
    which is the cleveland.com case, not the ownership-inference case. Tagesschau is deliberately
    NOT aliased: MBFC rates it as a separate outlet, so folding it in would assert a rating nobody
    published for it."""
    assert reg.resolve("Sportschau ARD").canonical == "ARD"
    assert reg.resolve("sportschau.de").canonical == "ARD"
    assert reg.resolve("tagesschau.de") is None


def test_news18_is_the_ninth_withheld_lean(reg):
    """MBFC rates it Right-Center and classes it Questionable with Low Credibility. 58 articles in
    the window — the largest low-credibility source in the catalog by volume."""
    o = reg.resolve("News18.Com")
    assert o.lean == 1.0 and o.credibility == "low"
    assert reg.is_low_credibility("CNN-News18")


def test_the_market_data_feeds_found_by_volume_are_wire(reg):
    """478 articles between them in a six-day window. Template copy clusters *correctly*, so no
    clustering signal can find it — only curated source identity can."""
    for form in ["Aktiencheck", "aktiencheck.de", "Finanznachrichten", "finanznachrichten.de"]:
        assert reg.is_wire(form), form


def test_the_aggregator_and_the_network_are_identified_not_rated(reg):
    """Three rows that exist for identity alone. Zazoom republishes other outlets' headlines, BelTA
    is a state agency no rater covers, and iHeartRadio is ~111 station hostnames in one window —
    the row gives that group a name a reader recognises."""
    import math
    for form, canonical in [("Zazoom", "Zazoom"), ("Eng.Belta.By", "BelTA"),
                            ("Ktrh.Iheart.Com", "iHeartRadio"), ("Wjjs.Iheart.Com", "iHeartRadio")]:
        o = reg.resolve(form)
        assert o.canonical == canonical and math.isnan(o.lean), form
        assert o.kind != "wire", f"{form}: an aggregator is not machine-generated copy"


def test_lint_rejects_a_kind_outside_the_vocabulary(tmp_path):
    """The kind column was unvalidated until it grew past a single value. A typo in it silently
    un-excludes a wire — the row loads, `is_wire` returns False, and 400 articles of template copy
    rejoin clustering with nothing to show for it."""
    p = tmp_path / "r.csv"
    p.write_text("canonical,lean,aliases,country,region,city,scope,kind,credibility\n"
                 "Widget Feed,,widgetfeed.com,US,,,national,wyre\n", encoding="utf-8")
    codes = {i["code"] for i in orx.lint_registry(str(p))}
    assert "invalid_kind" in codes


def test_only_wire_and_aggregator_are_excluded_from_clustering(reg):
    """A narrower set than KINDS on purpose. An aggregator's article IS another outlet's article, so
    counting it double-counts. A journal paper or an NGO release is original content — classified,
    and left in."""
    assert set(orx.EXCLUDED_KINDS) == {"wire", "aggregator"}
    assert reg.is_aggregator("Zazoom") and reg.is_aggregator("news.google.com")
    for form in ["Nature.Com", "Reddit.Com", "Unitaid.Eu", "Arxiv.Org"]:
        assert not reg.is_wire(form) and not reg.is_aggregator(form), form


def test_pro_science_sources_are_blank_because_the_rater_said_so(reg):
    """Not a curation gap. MBFC rates Nature and Frontiers PRO-SCIENCE and states that category is
    distinct from the left-right scale — so the blank lean here is SOURCED, which is the opposite of
    an outlet nobody has assessed."""
    import math
    for form, canonical in [("Nature.Com", "Nature"), ("Frontiersin.Org", "Frontiers")]:
        o = reg.resolve(form)
        assert o.canonical == canonical and math.isnan(o.lean) and o.kind == "research", form


# --------------------------------------------------------------------------- #
# Eleventh pass — a second wide probe (199 well-known outlets, 193 missing).
# --------------------------------------------------------------------------- #
def test_the_second_wide_probe_ratings(reg):
    for host, lean in [
        ("Mediaite.Com", -1.0), ("Qz.Com", -1.0), ("Fastcompany.Com", -1.0),
        ("Investopedia.Com", 0.0), ("Military.Com", 0.0), ("Stripes.Com", 1.0),
        ("Thejournal.Ie", -1.0), ("Irishexaminer.Com", -1.0), ("Independent.Ie", 1.0),
        ("Observer.Co.Uk", -1.0), ("Manchestereveningnews.Co.Uk", -1.0),
        ("Walesonline.Co.Uk", -1.0), ("Liverpoolecho.Co.Uk", 0.0),
        ("Calgaryherald.Com", 1.0), ("Ottawacitizen.Com", 1.0),
        ("Asahi.Com", -1.0), ("Mainichi.Jp", -1.0), ("Japantoday.Com", -1.0),
        ("Yomiuri.Co.Jp", 1.0), ("Chosun.Com", 1.0),
        ("Al-Monitor.Com", -1.0), ("Middleeastmonitor.Com", -2.0),
        ("Timeslive.Co.Za", 1.0), ("Iol.Co.Za", 0.0),
        ("Derstandard.At", -1.0), ("Politiken.Dk", -1.0), ("Nzz.Ch", 1.0),
        ("Volkskrant.Nl", 1.0), ("Aftenposten.No", 1.0),
        ("Latercera.Com", 1.0), ("Emol.Com", 1.0),
    ]:
        assert reg.lean(host) == lean, host


def test_japans_two_largest_dailies_land_on_opposite_sides(reg):
    """Asahi and Yomiuri are the two biggest circulations in the country and MBFC puts them either
    side of centre. A Japanese story can now show a spread instead of a row of unrated names."""
    assert reg.lean("Asahi.Com") == -1.0 and reg.lean("Yomiuri.Co.Jp") == 1.0
    assert reg.resolve("Asahi.Com").country == reg.resolve("Yomiuri.Co.Jp").country == "JP"


def test_the_uk_observer_is_not_the_new_york_one(reg):
    """MBFC rates The Observer (UK) and the New York Observer separately, and `Observer` alone is
    ambiguous between them — so the parenthetical canonical declines the bare word, as designed."""
    assert reg.resolve("Observer.Co.Uk").canonical == "The Observer (UK)"
    assert reg.resolve("Observer") is None
    assert reg.resolve("Observer.Com") is None


def test_three_more_questionable_sources_are_rated_and_withheld(reg):
    """Two Gulf state-aligned papers and a US financial title MBFC calls questionable for promoting
    right-wing conspiracy theories. Twelve withheld leans now — the rule is not an edge case."""
    for host, lean in [("Gulfnews.Com", 2.0), ("Thenational.Ae", 1.0), ("Investors.Com", 2.0)]:
        assert reg.lean(host) == lean and reg.is_low_credibility(host), host
    # A floor, not an exact count: this set grows with every curation pass, and an equality here
    # broke twice in one afternoon while asserting nothing anyone cared about. What matters is that
    # a source the rater called Questionable is never quietly voted, and that is asserted per-row.
    low = {o.canonical for o in reg.outlets() if o.credibility == "low"}
    assert len(low) >= 12
    assert {"Xinhua", "Global Times", "RT (Russia Today)", "TASS", "Sputnik",
            "Gulf News", "The National (UAE)", "News18"} <= low


def test_the_twelfth_pass_ratings(reg):
    for host, lean in [
        ("Theweek.Com", -2.0), ("Theroot.Com", -2.0), ("Grist.Org", -1.0), ("Statnews.Com", -1.0),
        ("Rollcall.Com", 0.0), ("Defenseone.Com", 0.0), ("Ktla.Com", 0.0), ("Wgntv.Com", 0.0),
        ("Entrepreneur.Com", 1.0), ("Birminghammail.Co.Uk", -1.0), ("Yorkshirepost.Co.Uk", 0.0),
        ("Thetyee.Ca", -1.0), ("Edmontonjournal.Com", 1.0), ("Gmanetwork.Com", -1.0),
        ("Abs-Cbn.Com", 0.0), ("Thediplomat.Com", 0.0), ("Khaleejtimes.Com", 1.0),
        ("Israelhayom.Com", 2.0), ("Francetvinfo.Fr", -1.0), ("Yle.Fi", 0.0),
        ("Jyllands-Posten.Dk", 1.0), ("Berlingske.Dk", 1.0), ("Expressen.Se", 1.0),
    ]:
        assert reg.lean(host) == lean, host


def test_denmarks_three_majors_straddle_the_centre(reg):
    """All three now rated and they do not agree: Politiken -1, Berlingske +1, Jyllands-Posten +1.
    A Danish story can show a spread, which is the whole product applied to a country that had no
    coverage at the start of the day."""
    assert reg.lean("Politiken.Dk") == -1.0
    assert reg.lean("Berlingske.Dk") == 1.0 and reg.lean("Jyllands-Posten.Dk") == 1.0


def test_the_philippines_now_has_a_centre_point(reg):
    """Four Philippine outlets across three positions — ABS-CBN at 0 against Manila Bulletin and the
    Inquirer on the left and The Manila Times on the right. Before today the country had one row."""
    assert reg.lean("Abs-Cbn.Com") == 0.0
    assert reg.lean("Mb.Com.Ph") == -1.0 and reg.lean("Inquirer.Net") == -1.0
    assert reg.lean("Manilatimes.Net") == 1.0


def test_a_masthead_reached_by_a_second_corporate_domain(reg):
    """MBFC rates Insider and Business Insider separately and lands both Left-Center; likewise
    Nikkei Asia is Nikkei's English edition. Same masthead, own domain — the cleveland.com case,
    not the ownership inference refused for Page Six."""
    assert reg.resolve("Insider.Com").canonical == "Business Insider"
    assert reg.resolve("Nikkei.Com").canonical == "Nikkei Asia"


def test_the_week_us_does_not_claim_the_bare_name(reg):
    """The Week India is a different magazine. The parenthetical canonical declines the bare word,
    as every disambiguated row does."""
    assert reg.resolve("Theweek.Com").canonical == "The Week (US)"
    assert reg.resolve("The Week") is None


def test_the_thirteenth_pass_ratings(reg):
    for host, lean in [
        ("Meduza.Io", -2.0), ("Pravda.Com.Ua", -1.0), ("Themoscowtimes.Com", -1.0),
        ("Balkaninsight.Com", -1.0), ("Thedailystar.Net", -1.0), ("Malaysiakini.Com", -1.0),
        ("Irrawaddy.Com", -1.0), ("Tribune.Com.Pk", 0.0),
        ("Freemalaysiatoday.Com", 1.0), ("Geo.Tv", 1.0),
    ]:
        assert reg.lean(host) == lean, host


def test_three_daily_stars_stay_apart(reg):
    """Bangladesh, the UK, and a bare name that belongs to neither. MBFC rates the Bangladeshi and
    British papers separately and they land two points apart — the parenthetical canonicals decline
    the bare word, which is the only thing keeping them from swapping ratings."""
    assert reg.resolve("Thedailystar.Net").canonical == "The Daily Star (Bangladesh)"
    assert reg.resolve("Dailystar.Co.Uk").canonical == "Daily Star (UK)"
    assert reg.lean("Thedailystar.Net") == -1.0 and reg.lean("Dailystar.Co.Uk") == 1.0
    assert reg.resolve("The Daily Star") is None


def test_exile_outlets_record_where_the_publisher_is(reg):
    """The country column means the publisher's home, not its subject. Meduza has operated from Riga
    since 2014 and The Moscow Times from Amsterdam since 2022 — both Russian journalism, neither
    published from Russia."""
    assert reg.resolve("Meduza.Io").country == "LV"
    assert reg.resolve("Themoscowtimes.Com").country == "NL"


def test_the_us_local_pass_ratings(reg):
    for host, lean in [
        ("Baltimoresun.Com", 1.0), ("Courant.Com", -1.0), ("Orlandosentinel.Com", -1.0),
        ("Sun-Sentinel.Com", 0.0), ("Post-Gazette.Com", 1.0), ("Dispatch.Com", -1.0),
        ("Reviewjournal.Com", 1.0), ("Sltrib.Com", -1.0), ("Jsonline.Com", -1.0),
        ("Indystar.Com", 0.0), ("Statesman.Com", -1.0), ("Expressnews.Com", -1.0),
        ("Sandiegouniontribune.Com", 0.0), ("Ocregister.Com", 1.0), ("Bostonherald.Com", 1.0),
        ("Tennessean.Com", 0.0), ("Courier-Journal.Com", -1.0), ("Newsobserver.Com", -1.0),
        ("Thestate.Com", -1.0), ("Nj.Com", -1.0), ("Buffalonews.Com", 0.0),
        ("Texastribune.Org", -1.0),
    ]:
        assert reg.lean(host) == lean, host


def test_same_market_rivals_are_rated_apart(reg):
    """The argument for per-masthead rows, three times over. Any scheme that grouped outlets by
    city would have merged each of these pairs and erased the only interesting thing about them."""
    assert reg.lean("Bostonglobe.Com") == -1.0 and reg.lean("Bostonherald.Com") == 1.0
    assert reg.lean("Latimes.Com") == -1.0 and reg.lean("Ocregister.Com") == 1.0
    assert reg.lean("Houstonchronicle.Com") == -1.0 and reg.lean("Dallasnews.Com") == 1.0
    assert reg.lean("Freep.Com") == -1.0 and reg.lean("Detroitnews.Com") == 1.0


def test_thestate_and_thestar_are_different_papers(reg):
    """One letter apart, two continents. `thestate.com` is South Carolina's daily and `thestar.com`
    is the Toronto Star — and the parenthetical canonical keeps a bare "The State" from claiming
    either, which matters because there is also a Kenyan Standard and a Malaysian Star in here."""
    assert reg.resolve("Thestate.Com").canonical == "The State (South Carolina)"
    assert reg.resolve("Thestar.Com").canonical == "Toronto Star"
    assert reg.resolve("The State") is None


# --------------------------------------------------------------------------- #
# India — the registry's own selection bias, corrected.
# --------------------------------------------------------------------------- #
def test_the_india_pass_ratings(reg):
    for host, lean in [
        ("Altnews.In", -2.0), ("Thewire.In", -1.0), ("Thequint.Com", -1.0),
        ("Telegraphindia.Com", -1.0), ("Deccanherald.Com", -1.0), ("Outlookindia.Com", -1.0),
        ("Newindianexpress.Com", -1.0), ("Newslaundry.Com", -1.0), ("Thenewsminute.Com", -1.0),
        ("Livemint.Com", 0.0), ("Wionews.Com", 0.0),
        ("Indiatvnews.Com", 1.0), ("Tribuneindia.Com", 1.0),
        ("Timesnownews.Com", 2.0), ("Opindia.Com", 2.0), ("Swarajyamag.Com", 2.0),
    ]:
        assert reg.lean(host) == lean, host


def test_indias_coverage_is_no_longer_one_sided(reg):
    """The registry held 12 Indian outlets and 8 were +1 or above — not India's media landscape but
    an artefact of which outlets got curated first, since the earlier passes reached India through
    business and English-language nationals. A story covered by five Indian outlets would have shown
    a wall of right-of-centre names and read as consensus."""
    import math
    ind = [o.lean for o in reg.outlets() if o.country == "IN" and not math.isnan(o.lean)]
    assert len(ind) >= 25
    assert sum(1 for v in ind if v < 0) >= 10, "the left side must be represented"
    assert sum(1 for v in ind if v > 0) >= 10, "and so must the right"
    assert sum(1 for v in ind if v == 0) >= 2


def test_the_two_tribunes_are_different_papers(reg):
    """tribuneindia.com is Chandigarh's, tribune.com.pk is Karachi's, and they are rated three
    points apart across a contested border. The parenthetical canonical keeps a bare "The Tribune"
    from claiming either."""
    assert reg.resolve("Tribuneindia.Com").canonical == "The Tribune (India)"
    assert reg.resolve("Tribune.Com.Pk").canonical == "The Express Tribune"
    assert reg.lean("Tribuneindia.Com") == 1.0 and reg.lean("Tribune.Com.Pk") == 0.0
    assert reg.resolve("The Tribune") is None


def test_swarajya_is_the_lowest_factual_rating_in_the_file(reg):
    """MBFC rates it RIGHT and Questionable with LOW factual reporting — numerous failed fact
    checks. The lean is recorded; it does not vote."""
    assert reg.lean("Swarajyamag.Com") == 2.0
    assert reg.is_low_credibility("Swarajyamag.Com")


def test_the_one_sided_markets_pass(reg):
    for host, lean in [
        ("Cumhuriyet.Com.Tr", -1.0), ("Dailytrust.Com", -1.0), ("Philstar.Com", -1.0),
        ("Nationalobserver.Com", -1.0), ("Ilgiornale.It", 1.0), ("Thisdaylive.Com", 1.0),
    ]:
        assert reg.lean(host) == lean, host


def test_no_market_of_three_or_more_is_completely_one_sided(reg):
    """The India lesson, generalised into a guard. A registry curated by whoever had volume inherits
    the feed's bias and then reports it back as a property of the world. Any country with three or
    more rated outlets must have at least one on each side — or an explicit exemption saying why.

    The exemptions are not laziness. Russia's only rows are RT, TASS and Sputnik, all withheld as
    Questionable, and the rated independents (Meduza, The Moscow Times) publish from Riga and
    Amsterdam so they sit under LV and NL. Korea's missing side exists only as a LOW-CONFIDENCE
    AllSides rating, which this file will not import."""
    import collections, math
    by = collections.defaultdict(list)
    for o in reg.outlets():
        if o.country and not math.isnan(o.lean):
            by[o.country].append(o.lean)
    # Every exemption carries its reason. An undocumented one is how a curation gap disguises
    # itself as a fact about the world, which is the exact mistake this test exists to prevent.
    exempt = {
        "AE": "no independent press — every rated Emirati outlet is state-aligned and MBFC rates "
              "all of them right of centre. Not a gap in the registry; a fact about the market.",
        "RU": "the only Russian rows are RT, TASS and Sputnik, all withheld as Questionable. The "
              "rated independents (Meduza, The Moscow Times) publish from Riga and Amsterdam and "
              "so sit under LV and NL.",
        "KR": "Hankyoreh is AllSides-Left at LOW CONFIDENCE and Kyunghyang has only an "
              "encyclopaedia description — neither is importable into a file with no confidence "
              "column.",
        "UA": "no rated outlet exists on the missing side.",
        # NZ's exemption was REMOVED, not weakened. It read "no rated outlet exists on the missing
        # side" when the only NZ rows were Stuff, NZ Herald and RNZ — a claim about the world that
        # was really a claim about how far curation had got. Newstalk ZB is MBFC Right-Center with
        # High factual reporting, and closes it. An exemption whose reason is falsifiable should be
        # re-tested, not inherited.
        # Fewer than four rated outlets each: too small for the shape to mean anything yet.
        "CL": "n=2", "SE": "n=3", "DK": "n=3", "MY": "n=3", "AR": "1 left (Infobae) against 3 right. Pagina/12, the obvious counterweight, has no "
              "rating at MBFC, AllSides or Ad Fontes — searched twice. The missing side was "
              "looked for and does not exist in any source this file accepts.",
        "CN": "n=3", "NL": "n=5, straddles via NRC -1 and De Telegraaf +2 — see the pairs test",
    }
    for cc, v in by.items():
        if len(v) < 3 or cc in exempt:
            continue
        assert any(x < 0 for x in v), f"{cc}: {len(v)} rated outlets and none left of centre"
        assert any(x > 0 for x in v), f"{cc}: {len(v)} rated outlets and none right of centre"


# --------------------------------------------------------------------------- #
# Accent folding — found by re-running the probe and asking "how many completed?"
# --------------------------------------------------------------------------- #
def test_an_unaccented_spelling_reaches_the_accented_masthead(reg):
    """The keys lower-cased and then STRIPPED anything non-alphanumeric, which deletes an accented
    letter rather than folding it. `Clarín` keyed as `clarn` while `Clarin` keyed as `clarin` — so a
    feed sending the unaccented spelling, which wire services routinely do, missed entirely. It was
    invisible because canonical and lookup used the same broken function and agreed with each other."""
    for plain, canonical in [("Clarin", "Clarín"), ("La Nacion", "La Nación"),
                             ("El Pais", "El País"),
                             ("Suddeutsche Zeitung", "Süddeutsche Zeitung")]:
        assert reg.resolve(plain).canonical == canonical, plain
        assert reg.resolve(canonical).canonical == canonical


def test_rte_and_rt_are_not_the_same_broadcaster(reg):
    """The collision the fold prevents, and the reason it was found at all: stripping the accent
    turned `RTÉ` into `rt`, landing Ireland's public broadcaster on Russia Today's alias. Nothing at
    runtime would have caught it — lint_registry's duplicate_alias check did, when the alias was
    added, which is the argument for that check existing."""
    assert reg.resolve("RTÉ").canonical == "RTE"
    assert reg.resolve("RTE News").canonical == "RTE"
    assert reg.resolve("RT").canonical == "RT (Russia Today)"
    assert reg.resolve("rt.com").canonical == "RT (Russia Today)"


def test_the_forms_a_feed_actually_sends_now_resolve(reg):
    """Ten outlets were curated but did not answer to the name anyone would type. Found by
    re-running the coverage probe and asking why only 57 of 199 resolved when 73 were curated — the
    gap was never missing outlets, it was missing ALIASES."""
    for form, canonical in [
        ("Nikkei", "Nikkei Asia"), ("Nikkei Asian Review", "Nikkei Asia"),
        ("Mainichi", "Mainichi Shimbun"), ("SCMP", "South China Morning Post"),
        ("Yle", "Yle News"), ("WGN", "WGN News"), ("The Journal.ie", "TheJournal.ie"),
        ("Hurriyet", "Hurriyet Daily News"), ("Caixin", "Caixin Global"),
        ("El Mercurio", "Emol"),
    ]:
        assert reg.resolve(form).canonical == canonical, form


def test_a_disambiguated_canonical_still_declines_the_bare_word(reg):
    """The three that stayed unresolved in that probe, and correctly so. Aliasing these would have
    been the wrong fix — `The Week`, `The Observer` and `The National` each belong to more than one
    real outlet."""
    for bare in ["The Week", "The Observer", "The National"]:
        assert reg.resolve(bare) is None, bare


def test_the_seventeenth_pass_ratings(reg):
    for host, lean in [
        ("Jornada.Com.Mx", -2.0), ("972Mag.Com", -1.0), ("Newarab.Com", -1.0),
        ("Taipeitimes.Com", -1.0), ("Focustaiwan.Tw", -1.0), ("Monitor.Co.Ug", -1.0),
    ]:
        assert reg.lean(host) == lean, host


def test_mexico_now_straddles(reg):
    """It held one left outlet against two right. La Jornada at -2 made it 2-2, and Animal Politico
    later took it to 3-2 — the one-sided guard would not have caught the original shape because a
    single left row already satisfied it, which is why that test is a floor and not the whole job.

    Asserted as a PROPERTY, not as counts. The first version of this pinned "exactly 2 and 2" and
    broke the moment the next rating landed — the second brittle exact-count assertion in this file
    after the withheld-set one. A count of a growing set is a tripwire with no hazard behind it."""
    import math
    mx = [o.lean for o in reg.outlets() if o.country == "MX" and not math.isnan(o.lean)]
    assert len(mx) >= 4
    assert sum(1 for v in mx if v < 0) >= 2 and sum(1 for v in mx if v > 0) >= 2


def test_the_last_three_alias_gaps_from_the_probe(reg):
    """RFI, Politico EU and CTV were all curated under longer canonical names and did not answer to
    the short form. Same class as Nikkei and SCMP — the registry knowing an outlet is not the same
    as the outlet being reachable."""
    assert reg.resolve("RFI").canonical == "Radio France Internationale"
    assert reg.resolve("Politico EU").canonical == "Politico Europe"
    assert reg.resolve("CTV").canonical == "CTV News"


# --------------------------------------------------------------------------- #
# US local television — the block where the RESULT SHAPE is the finding.
# --------------------------------------------------------------------------- #
def test_the_local_tv_block(reg):
    for host, lean in [
        ("Wbztv.Com", -1.0), ("Wsvn.Com", -1.0),
        ("Wfaa.Com", 0.0), ("Khou.Com", 0.0), ("Wcvb.Com", 0.0), ("Kcra.Com", 0.0),
        ("Wplg.Com", 0.0), ("Wsbtv.Com", 0.0), ("Ksat.Com", 0.0), ("Pix11.Com", 0.0),
    ]:
        assert reg.lean(host) == lean, host


def test_local_television_clusters_on_the_centre(reg):
    """Eight of ten are Least Biased, and MBFC's reasoning is the same each time: neutral wording,
    minimal editorial content. A local station runs straight news and has no editorial page, so
    there is little for a left-right scale to grip.

    Asserted as a SHAPE rather than per-row because the shape is the product-relevant fact: a
    coverage-gap claim fires on an EMPTY lean bucket, so a block of centre-rated publishers makes
    the centre harder to leave empty and should reduce claim count while improving claim support."""
    import math
    tv = [o.lean for o in reg.outlets()
          if o.scope == "local" and o.country == "US" and not math.isnan(o.lean)]
    assert len(tv) >= 10
    assert sum(1 for v in tv if v == 0) / len(tv) >= 0.6, "local TV should sit on the centre"
    assert all(abs(v) <= 1 for v in tv), "no local station should be rated at either extreme"


def test_the_two_left_leaning_stations_are_rated_for_syndication(reg):
    """The exceptions prove the rule by their reasoning. WSVN is Left-Center only because it
    syndicates CNN and WBZ because it carries CBS network content — neither is a judgement about
    the newsroom in Miami or Boston. Both are still their own rows, because the rating attaches to
    what the station BROADCASTS."""
    assert reg.lean("Wsvn.Com") == -1.0 and reg.lean("Wbztv.Com") == -1.0
    assert reg.resolve("CBS Boston").canonical == "WBZ-TV"
    # The networks they syndicate are separate outlets and keep their own ratings.
    assert reg.resolve("cnn.com").canonical == "CNN"
    assert reg.resolve("cbsnews.com").canonical == "CBS News"


def test_the_rest_of_the_local_tv_block(reg):
    for host, lean in [
        ("Whdh.Com", -1.0), ("Scrippsnews.Com", -1.0), ("Cheddar.Com", -1.0),
        ("Boston25News.Com", 0.0), ("Wmur.Com", 0.0), ("Click2Houston.Com", 0.0),
        ("Koat.Com", 0.0),
    ]:
        assert reg.lean(host) == lean, host
    assert reg.resolve("Newsy").canonical == "Scripps News"   # former name, one outlet


def test_bostons_apparent_lean_split_is_two_syndication_contracts(reg):
    """Boston has four rated stations and looks split — WCVB 0, WFXT 0, WBZ −1, WHDH −1. Both
    negatives are network CARRIAGE: WBZ carries CBS, WHDH syndicates CNN. Neither is a judgement
    about a Boston newsroom.

    Pinned because the lean column cannot express it. A reader sees a spread; the spread is two
    contracts. It is the clearest case in the file of a number being true and still not meaning
    what it looks like."""
    boston = {o.canonical: o.lean for o in reg.outlets()
              if o.city == "Boston" and o.scope == "local"}
    assert boston == {"WCVB": 0.0, "WFXT": 0.0, "WBZ-TV": -1.0, "WHDH": -1.0}


def test_the_europe_regional_pass(reg):
    for host, lean in [
        ("Demorgen.Be", -1.0), ("Trouw.Nl", -1.0), ("Vrt.Be", 0.0),
        ("Diepresse.Com", 1.0), ("Kurier.At", 1.0), ("Kathimerini.Gr", 1.0),
        ("Milliyet.Com.Tr", 2.0),
    ]:
        assert reg.lean(host) == lean, host
    assert reg.resolve("Ekathimerini.Com").canonical == "Kathimerini"   # English edition


def test_sabah_is_daily_sabah_not_a_second_turkish_outlet(reg):
    """MBFC's `Sabah` page IS `/daily-sabah/` — Daily Sabah is Sabah's English edition, one
    masthead. Adding a row would have double-counted a publisher and handed Turkey a phantom fifth
    outlet, which would then have skewed the per-country balance this file now measures."""
    for form in ["Sabah", "sabah.com.tr", "dailysabah.com"]:
        assert reg.resolve(form).canonical == "Daily Sabah", form
    import math
    tr = [o.canonical for o in reg.outlets() if o.country == "TR" and not math.isnan(o.lean)]
    assert len(tr) == len(set(tr)) == 5


def test_two_single_outlet_countries_gained_a_spread(reg):
    """Austria held only Der Standard and Belgium only Politico Europe. A country with one rated
    outlet can never show a spread, which makes it indistinguishable from consensus."""
    import math
    for cc in ("AT", "BE"):
        v = [o.lean for o in reg.outlets() if o.country == cc and not math.isnan(o.lean)]
        assert len(v) >= 3, cc
        assert len(set(v)) >= 2, f"{cc}: every rated outlet on the same point"


def test_belgium_was_balanced_by_searching_not_by_exempting(reg):
    """The guard failed the moment De Morgen and VRT went in — three Belgian outlets at -1, -1, 0.
    The tempting fix is an exemption; the right one was to search for the missing side. De Standaard
    came back LEAST BIASED and did not fix the shape, Euractiv came back Left-Center and made it
    worse, and Brussels Signal at +1 finally closed it.

    Worth pinning because an exemption would have been indistinguishable in the test file from the
    UAE's — and the UAE's is a fact about the market, while Belgium's would have been a fact about
    how hard I looked."""
    import math
    be = [o.lean for o in reg.outlets() if o.country == "BE" and not math.isnan(o.lean)]
    assert len(be) >= 6
    assert any(v < 0 for v in be) and any(v > 0 for v in be) and any(v == 0 for v in be)


def test_the_belgian_standaard_is_none_of_the_standards(reg):
    """standaard.be, standard.co.uk and standard.net.au — three mastheads, one brand word, and only
    the brand-DOMAIN key keeps them apart. The Australian one is still deliberately unresolved."""
    assert reg.resolve("Standaard.Be").canonical == "De Standaard"
    assert reg.resolve("Standard.Co.Uk").canonical == "London Evening Standard"
    assert reg.resolve("Standard.Net.Au") is None


def test_the_us_national_trade_pass(reg):
    for host, lean in [
        ("19Thnews.Org", -2.0), ("Insideclimatenews.Org", -1.0), ("Kffhealthnews.Org", -1.0),
        ("Govexec.Com", -1.0), ("Taskandpurpose.Com", -1.0), ("Benzinga.Com", 0.0),
    ]:
        assert reg.lean(host) == lean, host
    assert reg.resolve("Kaiser Health News").canonical == "KFF Health News"   # former name


def test_lean_and_factuality_are_independent_axes(reg):
    """The 19th News is -2 with one of the highest factual scores MBFC gives (HIGH 1.9, full
    transparency, clean record). A reader who treats a strong lean as a reliability signal has the
    two axes confused, and this file only carries one of them."""
    assert reg.lean("19Thnews.Org") == -2.0
    assert not reg.is_low_credibility("19Thnews.Org")


def test_the_withheld_rule_has_now_fired_on_both_sides(reg):
    """Courier Newsroom is the first LEFT-of-centre outlet to be withheld — MBFC gives it Low
    Credibility with Mixed factual reporting. Until now every withheld lean was right of centre,
    which left the rule looking like it might be tracking a direction rather than the rater's
    verdict. It is not."""
    lows = [o.lean for o in reg.outlets() if o.credibility == "low"]
    assert any(v < 0 for v in lows) and any(v > 0 for v in lows)
    assert reg.lean("Couriernewsroom.Com") == -1.0 and reg.is_low_credibility("Couriernewsroom.Com")


def test_a_newsroom_rated_off_the_axis_carries_no_kind(reg):
    """MedPage Today is Pro-Science — MBFC's off-scale category — but it is a daily medical
    newsroom, not a journal. Nature gets `kind=research` because that describes what it IS;
    MedPage Today gets none, because the reason its lean is blank is where the RATER put it."""
    import math
    o = reg.resolve("Medpagetoday.Com")
    assert math.isnan(o.lean) and o.kind is None and o.country == "US"
    assert reg.resolve("Nature.Com").kind == "research"


def test_the_latam_pass(reg):
    """Two ratings from fourteen probed — the lowest yield of any group in this session."""
    assert reg.lean("Animalpolitico.Com") == -1.0
    assert reg.lean("Batimes.Com.Ar") == 1.0
    assert reg.resolve("Batimes.Com.Ar").country == "AR"


def test_brazil_has_no_rated_outlet_at_all(reg):
    """Three Brazilian rows — Folha de S.Paulo, O Globo, and neither rated. MBFC's Brazil profile is
    behind its paid tier, so Veja and Exame could not be read even though pages exist, and O Globo
    is only reachable through its PARENT's rating, which this file refuses.

    Pinned as a fact rather than a target: the biggest country in South America is invisible to the
    lean distribution, and a Brazilian story shows no spread at all."""
    import math
    br = [o for o in reg.outlets() if o.country == "BR"]
    assert br, "the rows exist"
    assert all(math.isnan(o.lean) for o in br), "and not one of them is rated"


def test_the_asia_pass(reg):
    for host, lean in [
        ("Jakartaglobe.Id", -1.0), ("Kompas.Com", 0.0),
        ("Nst.Com.My", 0.0), ("Tribunnews.Com", 1.0),
    ]:
        assert reg.lean(host) == lean, host


def test_indonesia_was_balanced_by_searching_not_by_exempting(reg):
    """The Belgium case a second time. Jakarta Globe and Kompas would have left Indonesia at
    -1, -1, 0, 0 — four rated outlets, nothing right of centre — and the guard rejects that.
    Tribunnews was searched for and closed it."""
    import math
    idn = [o.lean for o in reg.outlets() if o.country == "ID" and not math.isnan(o.lean)]
    assert len(idn) >= 5
    assert any(v < 0 for v in idn) and any(v > 0 for v in idn) and any(v == 0 for v in idn)


def test_two_outlets_of_one_owner_are_rated_two_points_apart(reg):
    """Tribunnews and Kompas.com are both Kompas Gramedia and MBFC puts them at +1 and 0. A registry
    that collapsed outlets by OWNER — the shortcut already refused for Page Six and O Globo — would
    have merged a Least Biased outlet with a Right-Center one and reported the average as fact."""
    assert reg.lean("Kompas.Com") == 0.0 and reg.lean("Tribunnews.Com") == 1.0
    assert reg.resolve("Kompas.Com").canonical != reg.resolve("Tribunnews.Com").canonical


def test_the_two_straits_times_are_different_papers(reg):
    """nst.com.my is Malaysia's New Straits Times; straitstimes.com is Singapore's. One brand
    phrase, two countries, and they are rated a point apart."""
    assert reg.resolve("Nst.Com.My").canonical == "New Straits Times"
    assert reg.resolve("Straitstimes.Com").canonical == "The Straits Times"
    assert reg.resolve("Nst.Com.My").country == "MY"
    assert reg.resolve("Straitstimes.Com").country == "SG"


def test_the_me_africa_pass(reg):
    for host, lean in [
        ("Standardmedia.Co.Ke", 1.0), ("Citizen.Co.Za", 1.0),
        ("Ghanaweb.Com", 0.0), ("Globes.Co.Il", 0.0),
    ]:
        assert reg.lean(host) == lean, host


def test_four_standards_and_none_of_them_claims_the_word(reg):
    """standardmedia.co.ke, standard.co.uk, standaard.be, standard.net.au — four mastheads, one
    brand word, four different answers. The bare "The Standard" resolves to nothing, which is the
    parenthetical rule and the brand-domain key working together. This is the widest that
    particular trap has been stretched in the file."""
    assert reg.resolve("Standardmedia.Co.Ke").canonical == "The Standard (Kenya)"
    assert reg.resolve("Standard.Co.Uk").canonical == "London Evening Standard"
    assert reg.resolve("Standaard.Be").canonical == "De Standaard"
    assert reg.resolve("Standard.Net.Au") is None
    assert reg.resolve("The Standard") is None


def test_two_citizens_stay_apart(reg):
    """MBFC rates The Citizen (South Africa) and The Citizen (Tanzania) separately. Only the South
    African one is curated, and a bare "The Citizen" claims neither."""
    assert reg.resolve("Citizen.Co.Za").canonical == "The Citizen (South Africa)"
    assert reg.resolve("The Citizen") is None


def test_globes_is_rated_and_was_wrongly_recorded_as_absent(reg):
    """An earlier pass concluded Globes was unrated, on a search that returned GLOBE MAGAZINE — a US
    supermarket tabloid. The near-miss was real; the conclusion drawn from it was not. A better
    query finds the page immediately.

    Pinned as a regression test on the PROCESS, not the data: a bad search result should end a
    query, not a question."""
    o = reg.resolve("Globes.Co.Il")
    assert o.canonical == "Globes" and o.lean == 0.0 and o.country == "IL"


# --------------------------------------------------------------------------- #
# Australia / New Zealand — the twenty-fifth pass
# --------------------------------------------------------------------------- #
def test_the_au_nz_pass(reg):
    for host, lean in [
        ("Canberratimes.Com.Au", -1.0), ("Newcastleherald.Com.Au", -1.0),
        ("Illawarramercury.Com.Au", -1.0), ("Heraldsun.Com.Au", 1.0),
        ("Couriermail.Com.Au", 1.0), ("Dailytelegraph.Com.Au", 2.0),
        ("Skynews.Com.Au", 2.0),
        ("1News.Co.Nz", -1.0), ("Thepost.Co.Nz", -1.0), ("Thespinoff.Co.Nz", -1.0),
        ("Newshub.Co.Nz", -1.0), ("Newstalkzb.Co.Nz", 1.0), ("Newsroom.Co.Nz", 1.0),
    ]:
        assert reg.lean(host) == lean, host


def test_sky_news_australia_is_not_sky_news(reg):
    """Two channels, one brand word, and the registry must keep them three points and a voting
    right apart: MBFC rates Sky News (UK) Left-Center, and Sky News Australia RIGHT with credibility
    low. Aliasing the Australian channel onto the British one — the obvious shortcut, since the
    logo is the same — would have given a non-voting Right outlet a Left-Center vote."""
    uk, au = reg.resolve("News.Sky.Com"), reg.resolve("Skynews.Com.Au")
    assert uk.canonical == "Sky News" and au.canonical == "Sky News Australia"
    assert uk.lean == -1.0 and au.lean == 2.0
    assert not reg.is_low_credibility("News.Sky.Com")
    assert reg.is_low_credibility("Skynews.Com.Au")


def test_new_zealand_no_longer_needs_an_exemption(reg):
    """The balance guard exempted NZ with the reason "no rated outlet exists on the missing side".
    That reason was falsifiable, and false — it described how far curation had got, not the market.
    Searching for the missing side rather than for more of the same found Newstalk ZB at MBFC
    Right-Center, High factual, HIGH credibility. Pinned here so the exemption cannot come back.

    Third time this move has worked: Belgium, Indonesia, now New Zealand."""
    import math
    leans = [o.lean for o in reg.outlets() if o.country == "NZ" and not math.isnan(o.lean)]
    assert len(leans) >= 9
    assert any(x < 0 for x in leans) and any(x > 0 for x in leans)
    assert reg.lean("Newstalkzb.Co.Nz") == 1.0


def test_a_generic_new_zealand_masthead_does_not_claim_its_word(reg):
    """"Newsroom" and "The Post" are both ordinary English phrases and both are registered under a
    parenthetical, so the bare form resolves to nothing. `Courier Newsroom` — a different, American
    outlet already in the file — must be untouched by the New Zealand row."""
    assert reg.resolve("Newsroom.Co.Nz").canonical == "Newsroom (New Zealand)"
    assert reg.resolve("Newsroom") is None
    assert reg.resolve("Thepost.Co.Nz").canonical == "The Post (New Zealand)"
    assert reg.resolve("The Post") is None
    assert reg.resolve("Couriernewsroom.Com").canonical == "Courier Newsroom"


def test_the_australian_daily_telegraph_does_not_answer_for_the_british_one(reg):
    """Two unrelated papers, one on +2 with MIXED factual reporting and one — registered here as
    "The Telegraph" — on 0. A feed sending the bare "The Daily Telegraph" is genuinely ambiguous
    between them, so it resolves to nothing rather than to whichever was curated first."""
    assert reg.resolve("Dailytelegraph.Com.Au").canonical == "The Daily Telegraph (Australia)"
    assert reg.resolve("Telegraph.Co.Uk").canonical == "The Telegraph"
    assert reg.resolve("The Daily Telegraph") is None


def test_the_saturday_paper_is_withheld_for_low_confidence(reg):
    """MBFC gives it Left-Center (-3.8) and factual High (1.2), and says in the review that the
    assessment is LOW CONFIDENCE because the methodology could not be fully applied. This file has
    no confidence column, so writing -1 would present a hedge as settled. The row survives on its
    locality, which is the whole reason locality-without-lean rows exist.

    Same rule as Billboard and Hankyoreh."""
    import math
    o = reg.resolve("Thesaturdaypaper.Com.Au")
    assert math.isnan(o.lean)
    assert o.country == "AU" and o.city == "Melbourne"


def test_ratings_from_sources_this_file_does_not_accept_are_not_imported(reg):
    """Otago Daily Times and InDaily both have a Center rating available — from Ground News and
    Biasly respectively. Neither is a source this file accepts, and "a number exists somewhere" is
    not the bar. Both keep their locality and neither gets a lean."""
    import math
    for host, country in [("Odt.Co.Nz", "NZ"), ("Indaily.Com.Au", "AU")]:
        o = reg.resolve(host)
        assert math.isnan(o.lean), host
        assert o.country == country and o.scope == "regional"


# --------------------------------------------------------------------------- #
# Canada — the twenty-sixth pass, and the francophone hole a feed probe cannot see
# --------------------------------------------------------------------------- #
def test_the_canada_pass(reg):
    for host, lean in [
        ("Lapresse.Ca", -1.0), ("Ledevoir.Com", 0.0), ("Lesoleil.Com", -1.0),
        ("Ledroit.Com", 0.0), ("Journaldemontreal.Com", 2.0), ("Tvanouvelles.Ca", -1.0),
        ("Thecanadianpress.Com", 0.0), ("Citynews.Ca", 0.0), ("Cp24.Com", 0.0),
        ("Thespec.Com", -1.0), ("Timescolonist.Com", -1.0), ("Theprovince.Com", 1.0),
        ("Bnnbloomberg.Ca", -1.0), ("Ipolitics.Ca", -1.0), ("Hilltimes.Com", -1.0),
        ("Dailyhive.Com", -1.0), ("Thenarwhal.Ca", -2.0), ("Rabble.Ca", -2.0),
        ("Westernstandard.News", 2.0), ("Rebelnews.Com", 2.0),
    ]:
        assert reg.lean(host) == lean, host


def test_canada_had_sixteen_rows_and_no_french_press(reg):
    """The gap a feed probe structurally cannot report. Canada looked well covered — sixteen rated
    rows, straddling cleanly — and every one of them was anglophone. Quebec's press was represented
    by the Montreal Gazette, an ENGLISH paper. A probe only lists names the feed already sent, so an
    entire language's absence reads as zero outstanding work."""
    import math
    qc = [o for o in reg.outlets() if o.country == "CA" and o.region == "Quebec"]
    french = {o.canonical for o in qc if o.canonical.startswith(("La ", "Le ", "TVA"))}
    assert len(french) >= 5, french
    leans = [o.lean for o in qc if not math.isnan(o.lean)]
    assert any(x < 0 for x in leans) and any(x > 0 for x in leans), "Quebec must straddle too"


def test_one_owner_two_mastheads_three_points_apart(reg):
    """Quebecor owns Le Journal de Montréal and TVA Nouvelles. MBFC rates them +2 and -1. A registry
    that read a masthead's lean off its proprietor — the shortcut refused for Page Six, O Globo and
    Kompas — would have collapsed a Right tabloid and a Left-Center broadcaster into one number and
    published the average as a fact about both. Widest owner-gap in the file."""
    assert reg.lean("Journaldemontreal.Com") == 2.0
    assert reg.lean("Tvanouvelles.Ca") == -1.0


def test_citynews_is_eight_concurring_ratings_not_one_inherited_one(reg):
    """Looks like the O Globo inference and is its opposite. MBFC rates eight CityNews editions
    SEPARATELY and all eight come back Least Biased / High. One canonical carrying that is eight
    measurements summarised — not one parent's rating handed to children never measured. The test
    is whether the individual ratings exist, not whether a corporate box surrounds them.

    Every city subdomain must reach it, which is the brand-domain key doing the work."""
    for host in ("toronto.citynews.ca", "vancouver.citynews.ca", "montreal.citynews.ca",
                 "kitchener.citynews.ca", "680news.com"):
        assert reg.resolve(host).canonical == "CityNews", host


def test_a_news_agency_is_not_a_press_release_feed(reg):
    """`wire` in this file means a machine-generated press-release or market-data feed with no
    editorial stance — PR Newswire, MarketBeat, GlobeNewswire — and rows carrying it are excluded
    from clustering entirely. The Canadian Press is a newsroom, and is rated and votes exactly as
    Reuters, AP and AFP do. Calling it a wire because both are called wires in ordinary speech
    would silently drop Canada's most syndicated reporting out of every cluster."""
    cp = reg.resolve("Thecanadianpress.Com")
    assert cp.lean == 0.0 and not cp.kind
    assert not reg.is_wire("Thecanadianpress.Com")
    assert reg.is_wire("Prnewswire.Com")
    for agency in ("Reuters.Com", "Apnews.Com", "Afp.Com"):
        assert not reg.resolve(agency).kind, agency


def test_a_think_tanks_rating_is_not_imported_because_it_is_available(reg):
    """MBFC rates the True North Centre for Public Policy — a registered Calgary think tank — at
    Right with Mixed factual reporting. This file does not give `org` rows a lean, and that rule was
    not written to be suspended when the rating happens to be there for the taking. The row exists
    so the identity resolves; the lean stays blank."""
    import math
    o = reg.resolve("Tnc.News")
    assert o.kind == "org" and math.isnan(o.lean) and o.country == "CA"


def test_the_two_left_twos_canada_produced_are_both_high_factual(reg):
    """MBFC's LEFT band usually arrives with Mixed factual reporting. The Narwhal (-6.6, High 1.7)
    and rabble.ca (-6.9, High 1.8) are both strongly left AND highly factual, which is the pairing
    that shows lean and credibility are genuinely separate columns rather than one dressed as two."""
    for host in ("Thenarwhal.Ca", "Rabble.Ca"):
        assert reg.lean(host) == -2.0
        assert not reg.is_low_credibility(host), host


# --------------------------------------------------------------------------- #
# UK / Ireland — the twenty-seventh pass
# --------------------------------------------------------------------------- #
def test_the_uk_ireland_pass(reg):
    for host, lean in [
        ("Irishnews.Com", -1.0), ("Belfastlive.Co.Uk", 0.0),
        ("Dailyrecord.Co.Uk", -2.0), ("Thenational.Scot", -1.0),
        ("Businesspost.Ie", 0.0), ("Gript.Ie", 2.0),
        ("Morningstaronline.Co.Uk", -2.0), ("Thecanary.Co", -2.0),
        ("Bylinetimes.Com", -2.0), ("Novaramedia.Com", -2.0),
        ("Opendemocracy.Net", -1.0), ("Theweek.Co.Uk", -1.0),
        ("Theregister.Com", 0.0), ("Unherd.Com", 1.0),
        ("Thecritic.Co.Uk", 2.0), ("Talktv.Co.Uk", 2.0),
    ]:
        assert reg.lean(host) == lean, host


def test_northern_ireland_was_a_market_the_file_did_not_have(reg):
    """The Canada lesson applied on purpose rather than discovered. Before working the probe list,
    ask what a probe cannot see: Northern Ireland had ZERO rows — 1.9m people whose newspapers
    divide on the most legible axis in these islands — and a probe reports zero outstanding work for
    a market that sends nothing because nothing was ever tracked."""
    ni = [o for o in reg.outlets() if o.region == "Northern Ireland"]
    assert len(ni) >= 3
    assert reg.resolve("Irishnews.Com").country == "GB"


def test_morning_star_is_the_second_globes(reg):
    """An earlier pass recorded the Morningstar / The Morning Star confusion as a near-miss
    successfully AVOIDED, and moved on without asking whether the British communist daily was itself
    rated. It is. The trap was real and the question was left unanswered underneath it — the exact
    shape of the Globes error, found by re-checking what that error was standing next to.

    Two instances now, which makes it a pattern rather than an incident: spotting a bad result and
    answering the question are separate acts, and the first feels like the second."""
    assert reg.lean("Morningstaronline.Co.Uk") == -2.0


def test_the_morning_star_does_not_claim_the_word(reg):
    """`Morning Star` collides with Morningstar (US financial data) and the Vernon Morning Star
    (British Columbia). No Morningstar row exists today — the parenthetical keeps the word unclaimed
    anyway, so the trap stays documented rather than silently won by whichever was curated first."""
    assert reg.resolve("Morning Star") is None
    assert reg.resolve("Morningstar") is None
    assert reg.resolve("Morningstaronline.Co.Uk").canonical == "Morning Star (UK)"


def test_the_scale_saturates_and_credibility_still_separates(reg):
    """Five British rows sit on the -2 floor: three at MBFC LEFT and Novara Media at FAR LEFT. The
    file cannot tell those apart and must not pretend to. What it CAN tell apart is credibility —
    Novara is MIXED where the Morning Star is MOSTLY FACTUAL. The clearest demonstration that lean
    and credibility are two columns doing different jobs rather than one wearing two names."""
    floor = [o for o in reg.outlets() if o.country == "GB" and o.lean == -2.0]
    assert len(floor) >= 4
    assert min(o.lean for o in reg.outlets() if not __import__("math").isnan(o.lean)) == -2.0
    assert reg.credibility("Novaramedia.Com") == "medium"


def test_the_week_is_the_anti_citynews(reg):
    """Canada's pass made CityNews ONE canonical because MBFC rated eight editions separately and
    all eight agreed. The Week's two editions DISAGREE — MBFC has the US edition at LEFT and the UK
    edition at Left-Center. Editions get rated separately because they can come apart, and looking
    is the only way to find out which case you are in. A bare "The Week" resolves to neither."""
    assert reg.lean("Theweek.Com") == -2.0
    assert reg.lean("Theweek.Co.Uk") == -1.0
    assert reg.resolve("The Week") is None


def test_a_rating_out_of_reach_is_not_a_rating_that_does_not_exist(reg):
    """Two blank Scottish/NI rows, two different facts. Belfast Telegraph: Ad Fontes, AllSides and
    MBFC all have no rating — ordinary absence. Aberdeen Press & Journal: THE MBFC PAGE EXISTS and
    could not be read; the fetcher gets 403 and search returned only prose without the categorical
    label this file maps from.

    The Brazil/paywall case again. A future curator should know which of these is retrievable."""
    import math
    for host in ("Belfasttelegraph.Co.Uk", "Pressandjournal.Co.Uk"):
        o = reg.resolve(host)
        assert math.isnan(o.lean) and o.country == "GB", host
    assert reg.resolve("Pressandjournal.Co.Uk").region == "Scotland"


def test_ireland_has_a_right_side_that_is_not_one_paper(reg):
    """Ireland's rated set was six rows with exactly one outlet right of centre — the Irish
    Independent. Gript at MBFC RIGHT means the Irish spread no longer rests on a single masthead,
    which is the difference between a market that straddles and a market that happens to."""
    import math
    right = [o.canonical for o in reg.outlets()
             if o.country == "IE" and not math.isnan(o.lean) and o.lean > 0]
    assert len(right) >= 2, right


# --------------------------------------------------------------------------- #
# Resolution memo — profiled at 60,400 calls over 400 distinct publisher strings
# --------------------------------------------------------------------------- #
def test_resolution_is_memoized_per_registry_instance(reg):
    """Clustering resolves every article's publisher THREE times — `is_wire`, `is_aggregator` and
    `is_low_credibility` each call `resolve` independently — and each call pays `_fold` twice
    (NFKD normalize, combining-mark filter, join) for `_full_key` and `_name_key`. Measured over a
    20,000-article build: 60,400 calls against 400 distinct publisher strings, a 151x waste factor.

    Resolution is a pure function of the input string and the registry's contents, and the contents
    never change after `load` — so remembering the answer cannot change one."""
    reg._resolve_cache.clear()
    first = reg.resolve("bbc.com")
    assert len(reg._resolve_cache) == 1
    for _ in range(50):
        assert reg.resolve("bbc.com") is first          # identity: the same object, not a rebuild
    assert len(reg._resolve_cache) == 1


def test_is_wire_url_resolves_the_host_so_one_feed_costs_one_memo_entry(reg):
    """**Why the predicate splits the host itself instead of handing `resolve` the URL.**

    `resolve` would give the same ANSWER either way — it host-splits internally — so no behavioural
    test can tell the two apart. The difference is the memo, which is keyed on the input string: a
    catalog holds ~34,000 distinct URLs against ~5,000 distinct hosts, so passing the URL turns a
    memo hit into a full resolve (two `_fold` passes) on every article of the build, in a stage
    cProfile already puts at 10% of it.

    One obituary feed publishes hundreds of distinct URLs and must cost exactly one entry."""
    reg._resolve_cache.clear()
    for i in range(200):
        assert reg.is_wire_url(f"https://obits.oregonlive.com/us/obituaries/name/person-{i}")
    assert len(reg._resolve_cache) == 1, \
        "200 URLs from one feed must collapse to one host; resolving the URL would store 200"


def test_is_wire_url_asks_about_a_HOST_and_not_about_any_identity_string(reg):
    """The `_looks_like_host` guard, and what it is for.

    `is_wire` answers for ANY identity form — a display name resolves just as well as a domain. The
    URL column is not an identity field though, so a display name landing in it must not be read as
    a curation claim: the predicate's contract is "the host this was served from", and a string
    with no host has no answer. Without the guard `_host_of("PR Newswire")` yields the bare name
    and the name path resolves it, quietly making the two predicates synonyms."""
    assert reg.is_wire("PR Newswire"), "the name form IS wire, asked as an identity"
    assert not reg.is_wire_url("PR Newswire"), "but it is not a host, so the URL question has no yes"


def test_the_memo_remembers_misses_too(reg):
    """Most feed publishers are UNKNOWN to the registry — production sees ~5,200 distinct names
    against 505 rows. A cache that stored only hits would re-resolve the majority of calls on every
    article, which is the opposite of the intended effect. `None` is a real answer and is cached,
    which is why the sentinel exists."""
    reg._resolve_cache.clear()
    assert reg.resolve("definitely-not-an-outlet-xyz") is None
    assert len(reg._resolve_cache) == 1, "a miss must be remembered"
    assert reg.resolve("definitely-not-an-outlet-xyz") is None


def test_the_memo_never_outlives_its_registry(reg):
    """The cache is per-INSTANCE, so a reloaded registry starts empty. If it were a module global,
    a curation change would be shadowed by answers computed from the previous file — a silent,
    hard-to-see staleness in exactly the data this repo spends its time getting right."""
    import outlet_registry as orx
    reg.resolve("bbc.com")
    assert reg._resolve_cache
    fresh = orx.OutletRegistry(list(reg.outlets()), {})
    assert fresh._resolve_cache == {}, "a new registry must not inherit a memo"


def test_memoized_and_uncached_resolution_agree_on_every_canonical(reg):
    """The property that makes the memo safe, checked against the whole file rather than a sample:
    for every canonical in the registry, the cached path and the uncached path return the same
    outlet. A cache that is fast and wrong is worse than no cache."""
    reg._resolve_cache.clear()
    for o in reg.outlets():
        assert reg.resolve(o.canonical) == reg._resolve_uncached(o.canonical), o.canonical
    for odd in ("", None, "The Star", "Morning Star", "not-real-xyz", "Fox News (Online News)"):
        assert reg.resolve(odd) == (reg._resolve_uncached(odd) if odd else None), repr(odd)


def test_untracked_pass_2026_08_09_carries_mbfc_labels_not_composites(reg):
    """Every lean here is MBFC's own published label mapped to -2..+2 — never Ground News'
    average of three raters, which the file rejects because a composite hides the disagreement
    that IS the signal (see the Fortune row and the Ground News block in the CSV)."""
    expected = {"TechCrunch": -1.0, "Deadline": -1.0, "Bleacher Report": -1.0,
                "Gizmodo": -2.0, "Ars Technica": 0.0, "UPI": 0.0,
                "Fox Business": 1.0, "TMZ": 1.0}
    for name, lean in expected.items():
        assert reg.lean(name) == lean, name


def test_the_new_rows_resolve_from_the_forms_the_catalog_actually_sends(reg):
    """The feed sends bare names AND title-cased hosts for the same outlet — the audit counted
    2-4 name forms each. Both routes must land on one identity or the outlet stays split."""
    for form, canonical in [("techcrunch.com", "TechCrunch"), ("Techcrunch.Com", "TechCrunch"),
                            ("deadline.com", "Deadline"), ("Deadline Hollywood", "Deadline"),
                            ("gizmodo.com", "Gizmodo"), ("Gizmodo.com", "Gizmodo"),
                            ("arstechnica.com", "Ars Technica"), ("tmz.com", "TMZ"),
                            ("upi.com", "UPI"), ("United Press International", "UPI"),
                            ("bleacherreport.com", "Bleacher Report"),
                            ("foxbusiness.com", "Fox Business")]:
        assert reg.resolve(form).canonical == canonical, form


def test_fox_business_is_not_fox_news(reg):
    """Two mastheads, two MBFC ratings (Right-Center vs Right). A bare-name or domain collision
    would silently move one outlet's coverage onto the other's lean."""
    assert reg.resolve("foxbusiness.com").canonical == "Fox Business"
    assert reg.resolve("foxnews.com").canonical == "Fox News"
    assert reg.lean("Fox Business") != reg.lean("Fox News")


def test_pro_science_never_becomes_a_political_lean(reg):
    """MBFC rates Science Daily PRO-SCIENCE, which is off the left/right axis. Recording that as
    a lean would put a science-vs-pseudoscience judgement on a political scale, so it takes the
    `research` kind and a blank lean like Nature/Frontiers/arXiv."""
    o = reg.resolve("sciencedaily.com")
    assert o.canonical == "Science Daily" and o.kind == "research"
    assert math.isnan(reg.lean("Science Daily")), "pro-science is not a centre rating"


def test_mixed_factuality_is_not_the_low_credibility_flag(reg):
    """The flag means a rater called the source QUESTIONABLE. Fox Business and TMZ are MBFC
    'Mixed' factual / Medium credibility — imperfect, not questionable — so flagging them would
    silently drop their votes from blindspot claims."""
    for n in ("Fox Business", "TMZ"):
        assert not reg.is_low_credibility(n), n


def test_untracked_pass_tranche_two_labels(reg):
    expected = {"Northwest Florida Daily News": 0.0, "Springfield News-Sun": 0.0,
                "Investing.com": 0.0, "OilPrice": 0.0, "Kotaku": -1.0,
                "Vice": -1.0, "Malay Mail": 1.0}
    for name, lean in expected.items():
        assert reg.lean(name) == lean, name


def test_both_pro_science_outlets_stay_off_the_political_axis(reg):
    """Science Daily and Phys.org are both MBFC PRO-SCIENCE. Neither may take a lean — a
    science-vs-pseudoscience verdict is not a left/right position, and 0 would read as 'centre'."""
    for host, canonical in (("sciencedaily.com", "Science Daily"), ("phys.org", "Phys.org")):
        o = reg.resolve(host)
        assert o.canonical == canonical and o.kind == "research", host
        assert math.isnan(reg.lean(canonical)), canonical


def test_business_wire_is_classified_by_what_it_distributes_not_by_a_rating(reg):
    """`wire` is a content-type call (press releases), not a bias inference — MBFC has no page for
    Business Wire at all. Its peers are curated identically, and MBFC actually rates PR Newswire
    Least Biased while this file still excludes it, which is the proof the kind is about content."""
    assert reg.is_wire("businesswire.com") and reg.is_wire("Business Wire")
    assert math.isnan(reg.lean("Business Wire")), "a wire row carries no lean"
    assert reg.is_wire("prnewswire.com"), "the peer this call is modelled on"


def test_malay_mail_does_not_collide_with_the_star_malaysia(reg):
    """Two Malaysian mastheads with different MBFC ratings (+1 vs +2); a bare-name or domain
    collision would move one outlet's coverage onto the other's lean."""
    assert reg.resolve("malaymail.com").canonical == "Malay Mail"
    assert reg.resolve("thestar.com.my").canonical == "The Star (Malaysia)"
    assert reg.lean("Malay Mail") != reg.lean("The Star (Malaysia)")


def test_mixed_factuality_for_opacity_is_still_not_low_credibility(reg):
    """Investing.com is MBFC Mixed/Medium for opaque ownership — not a Questionable verdict."""
    assert not reg.is_low_credibility("Investing.com")


def test_the_shipped_registry_file_lints_clean():
    """The file's own linter — duplicate canonicals, duplicate aliases (resolution would depend on
    row order), invalid leans, invalid kinds. Run against the REAL file, not a fixture."""
    import pathlib
    csv = pathlib.Path(__file__).resolve().parent.parent / "examples" / "data" / "outlet_registry.csv"
    assert orx.lint_registry(str(csv)) == []


def test_untracked_pass_tranche_three_local_news_labels(reg):
    """The US local-news bucket turned out well covered by MBFC, contradicting the earlier
    assumption that raters skip small locals — it holds for tiny weeklies, not for local TV or
    Lee/Gray-owned dailies."""
    expected = {"Fox59": 0.0, "Daily Post Nigeria": 0.0, "North Platte Telegraph": 0.0,
                "Political Wire": 0.0, "WMTV": 0.0, "KSTP": 0.0,
                "KOCO": -1.0, "Kenosha News": -1.0,
                "Chronicle-Tribune": 1.0, "Goldsboro News-Argus": 1.0}
    for name, lean in expected.items():
        assert reg.lean(name) == lean, name


def test_local_tv_resolves_from_callsign_and_from_the_feeds_label(reg):
    """The catalog sends call signs, station domains and prose labels for the same station."""
    for form, canonical in [("WXIN", "Fox59"), ("fox59.com", "Fox59"),
                            ("nbc15.com", "WMTV"), ("wmtv15news.com", "WMTV"),
                            ("Kstp Television", "KSTP"), ("kstp.com", "KSTP"),
                            ("Koco News Channel Five", "KOCO"), ("koco.com", "KOCO"),
                            ("Goldsboro News Argus", "Goldsboro News-Argus"),
                            ("Marion Chronicle-Tribune", "Chronicle-Tribune")]:
        assert reg.resolve(form).canonical == canonical, form


def test_fox59_is_neither_fox_news_nor_fox_business(reg):
    """Three unrelated mastheads sharing a brand word and three different MBFC ratings
    (0 / +2 / +1). A bare-name collision would move a local station onto a network's lean."""
    assert reg.resolve("fox59.com").canonical == "Fox59"
    assert reg.resolve("foxnews.com").canonical == "Fox News"
    assert reg.resolve("foxbusiness.com").canonical == "Fox Business"
    assert reg.lean("Fox59") == 0.0 and reg.lean("Fox Business") == 1.0


def test_the_pass_landed_rows_on_both_sides_of_the_spectrum(reg):
    """A curation pass that only ever adds one side quietly skews every downstream balance
    metric. Tranche 3 is the corrective: local dailies supply the right-of-centre rows the
    tech/entertainment verticals could not."""
    added = ["Fox59", "Daily Post Nigeria", "North Platte Telegraph", "Political Wire", "WMTV",
             "KSTP", "KOCO", "Kenosha News", "Chronicle-Tribune", "Goldsboro News-Argus"]
    leans = [reg.lean(n) for n in added]
    assert any(x < 0 for x in leans) and any(x > 0 for x in leans), leans


def test_untracked_pass_tranche_four_labels(reg):
    assert reg.lean("Daily Dispatch") == 1.0
    assert reg.lean("Aaj Tak") == 1.0
    for form in ("hendersondispatch.com", "Hendersondispatch", "Henderson Daily Dispatch"):
        assert reg.resolve(form).canonical == "Daily Dispatch", form


def test_aaj_tak_is_not_india_today(reg):
    """Same owner (TV Today Network / India Today Group), different mastheads and different MBFC
    pages — the sibling inheritance this file refuses everywhere (Brisbane Times, O Globo)."""
    assert reg.resolve("aajtak.in").canonical == "Aaj Tak"
    assert reg.resolve("India Today").canonical == "India Today"


def test_no_row_was_taken_from_a_similarly_named_masthead(reg):
    """The tranche-4 misses were all near-miss identities. These MUST stay unresolved rather than
    inherit a rating from the paper MBFC actually rates:
      Oneida Dispatch  vs Utica Observer-Dispatch / "Oneida Times" (Low Credibility)
      yoursun.com      vs the Port Charlotte Sun (one masthead on a multi-masthead group domain)
    """
    for absent in ("Oneida Dispatch", "oneidadispatch.com", "yoursun.com", "Columbia Gorge News"):
        assert reg.resolve(absent) is None, f"{absent} must not resolve — no rating is its own"


def test_abp_live_covers_its_language_editions_by_registrable_domain(reg):
    """The catalog sends `bengali.abplive.com`. That is the SAME registrable domain as the masthead
    MBFC rates, not a sibling brand — the opposite of the Brisbane Times / O Globo case, where the
    shared thing was an OWNER. One alias therefore covers every language edition."""
    for form in ("abplive.com", "bengali.abplive.com", "ABP Live"):
        assert reg.resolve(form).canonical == "ABP Live", form
    assert reg.lean("ABP Live") == 1.0
    assert reg.resolve("India Today").canonical == "India Today", "sibling brand stays separate"


def test_9gag_is_forum_not_wire_and_carries_no_lean(reg):
    """`forum` is a content-type call with peers already here (Reddit, DEV Community), not a
    rating. Unlike `wire` it does NOT exclude from clustering, so the row only fixes identity."""
    o = reg.resolve("9gag.com")
    assert o.canonical == "9GAG" and o.kind == "forum"
    assert math.isnan(reg.lean("9GAG")) and not reg.is_wire("9gag.com")


def test_globo_group_rating_was_not_taken_for_g1(reg):
    """This file already refused reading O Globo's lean off GLOBO the parent group. G1 is that
    group's portal, which makes the same inference tempting and no more valid. The sixth tranche
    moved the refusal from "no row" to "a row with no lean" — identity and locality are curated
    facts, the group's rating still is not — so G1 now resolves, blank, exactly like O Globo."""
    o = reg.resolve("g1.globo.com")
    assert o is not None and o.canonical == "G1" and math.isnan(o.lean), "G1 is identity-only"
    assert reg.resolve("G1").canonical == "G1"
    # The parent group itself still resolves to nothing: globo.com is not G1's host, and "Globo"
    # is not a masthead this file lists — taking either would be the group-rating inference.
    for absent in ("globo.com", "Globo"):
        assert reg.resolve(absent) is None, absent


# --------------------------------------------------------------------------- #
# Reader-facing source type — the Stories "Type" filter's whole basis.
# --------------------------------------------------------------------------- #
def test_source_type_projects_the_curated_kind(reg):
    """News / Research / Community are the registry's own vocabulary, re-labelled — not a new
    classification and not anything inferred from an article."""
    assert orx.source_type("Nature") == "research"        # kind = research
    assert orx.source_type("arxiv.org") == "research"     # …by domain too
    assert orx.source_type("Reddit") == "community"       # kind = forum
    assert orx.source_type("BBC News") == "news"          # a curated row with no kind
    assert reg.resolve("BBC News").kind is None, "the news case is the BLANK kind, not a value"


def test_an_unknown_publisher_has_no_type_rather_than_defaulting_to_news():
    """The load-bearing distinction, and the easy thing to get wrong.

    Most feed publishers have no registry row at all — the catalogue runs to thousands of hosts
    against 573 curated ones. Both an unknown outlet and a curated news outlet have `kind = None`,
    so a mapping that reads the kind alone calls every stranger News, and the filter would assert a
    classification nobody made. Absence of a row is not evidence, exactly as it is not for
    `is_wire`, so it must resolve first and only then read the kind.
    """
    assert orx.resolve("Definitely Not A Curated Outlet") is None
    assert orx.source_type("Definitely Not A Curated Outlet") is None
    assert orx.source_type(None) is None and orx.source_type("") is None


def test_kinds_outside_the_three_map_to_nothing(reg):
    """`org`, `wire` and `aggregator` are none of the three, and are not forced into one.

    An NGO's own announcement is not reporting, research, or a community post; saying otherwise
    would be inventing a verdict a curator never gave. (`wire`/`aggregator` are EXCLUDED_KINDS and
    never reach a story anyway — asserted here so the mapping stays explicit about them.)"""
    assert reg.resolve("Unitaid").kind == "org"
    assert orx.source_type("Unitaid") is None
    for name in ("PR Newswire", "Google News"):
        o = reg.resolve(name)
        if o is not None and o.kind in ("wire", "aggregator"):
            assert orx.source_type(name) is None, name
    assert set(orx.SOURCE_TYPES) == {"news", "research", "community"}
    # Every value the mapping can emit is one the UI offers — no orphan type can reach a chip.
    assert set(orx._TYPE_OF_KIND.values()) <= set(orx.SOURCE_TYPES)
