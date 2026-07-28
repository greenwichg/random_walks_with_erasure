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
        # Not outlets to rate at all — identity is the whole reason each row exists.
        "BelTA",               # Belarus state agency; press-freedom scores are not an outlet lean
        "iHeartRadio",         # no rating for the network; the row names ~111 station hostnames
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


def test_the_obituary_and_regional_subdomains_reach_their_paper(reg):
    """The audit found these as separate high-volume names — `obits.lehighvalleylive.com` at 76
    articles and `news.detik.com` at 28. Subdomain resolution means one row covers the masthead's
    whole surface, which is why the volume ranking and the registry disagree on how many outlets
    there are."""
    assert reg.resolve("Obits.Lehighvalleylive.Com").canonical == "The Express-Times"
    assert reg.resolve("News.Detik.Com").canonical == "Detik"
    assert reg.resolve("Obits.Oregonlive.Com").canonical == "The Oregonian"


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
