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
    assert next(iter(resolved)) == orx.Outlet("New York Times", -1.0)


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
    for o in outs:
        assert o.canonical and math.isfinite(o.lean) and -2.0 <= o.lean <= 2.0
    # ordered by (lean, name)
    assert outs == sorted(outs, key=lambda o: (o.lean, o.canonical))


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
