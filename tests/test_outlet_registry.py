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
    wire = {o.canonical for o in reg.outlets() if o.kind == "wire"}
    assert wire <= blank, "a wire feed must never carry a lean"
    assert blank - wire == {
        # No rating exists at any public rater.
        "Brisbane Times",      # no MBFC page; sibling mastheads are rated, which is not a source
        "Folha de S.Paulo",    # confirmed absent from MBFC, AllSides and Ad Fontes alike
        "Milenio",             # no MBFC page
        "Nigerian Tribune",    # no MBFC page
        "O Globo",             # only its owner is rated — see the test above
        "The East African",    # only its owner, Nation Media Group, is rated
        # A rating EXISTS and is deliberately withheld — see the test below.
        "Xinhua",
        "Global Times",
        "RT (Russia Today)",
        "The Economic Times",
        "Daily Star (UK)",
        "GB News",
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


def test_a_questionable_source_is_identified_but_not_rated(reg):
    """The rule this pass added. MBFC publishes a lean AND a credibility verdict; for these four the
    verdict is Questionable / Low Credibility. The lean exists and is deliberately not imported,
    because this file has no credibility column — the vote would reach _distribution and the
    >= 3 rated publishers floor carrying exactly Reuters' weight, and a coverage-gap claim could
    come to rest on two state broadcasters with nothing in the product showing it.

    Identity and country stay curated: those are facts, and they still settle who the outlet is."""
    import math
    for name, country in [("Xinhuanet.Com", "CN"), ("Globaltimes.Cn", "CN"),
                          ("Rt.Com", "RU"), ("Economictimes.Indiatimes.Com", "IN")]:
        o = reg.resolve(name)
        assert o is not None and math.isnan(o.lean), name
        assert o.country == country, name


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
    so the more specific subdomain wins — and the Times of India keeps its rating while the Economic
    Times keeps its deliberate blank."""
    import math
    assert reg.resolve("Timesofindia.Indiatimes.Com").canonical == "The Times of India"
    assert reg.lean("Timesofindia.Indiatimes.Com") == 1.0
    assert math.isnan(reg.lean("Economictimes.Indiatimes.Com"))


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
