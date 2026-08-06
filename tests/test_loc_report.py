"""The production-LOC report — scripts/loc_report.py.

The number it produces is quoted at people, so the properties that make it honest need pinning: it
must follow REACHABILITY (not directory names, which in this repo point the wrong way), it must not
count tests, and its line counting must not mistake documentation for code — this codebase is
comment-dense enough that a naive count would overstate "code" by roughly a third.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import loc_report as lr    # noqa: E402


def test_python_closure_reaches_the_engine_from_the_real_entrypoints():
    """`examples/` is the backend, not examples — `Dockerfile.api` runs `examples/api_fastapi.py`.
    Excluding it by name, which is the conventional rule, would delete the product."""
    closure = {str(p.relative_to(ROOT)) for p in lr.python_closure()}
    for required in ("examples/api_fastapi.py", "examples/store.py", "examples/personalize.py",
                     "examples/story_service.py", "examples/notification_service.py"):
        assert required in closure, f"{required} unreachable — the closure is broken"


def test_the_closure_follows_lazily_imported_modules():
    """Several subsystems are imported INSIDE functions on purpose, to keep them out of an import
    graph. A top-level-only walk would silently drop the poller's whole post-cycle seam."""
    closure = {str(p.relative_to(ROOT)) for p in lr.python_closure()}
    assert "examples/push_delivery.py" in closure     # lazy inside feed_service._post_cycle
    assert "examples/story_events.py" in closure      # ditto


def test_audit_tools_ship_in_the_image_but_are_not_production():
    """They are COPYed into the api image and are runnable with `docker exec`, so a
    what-is-in-the-image measure would count them. Nothing a served request touches imports them."""
    closure = {str(p.relative_to(ROOT)) for p in lr.python_closure()}
    for tool in ("examples/audit_continuation.py", "examples/audit_notifications.py"):
        assert (ROOT / tool).exists()
        assert tool not in closure, f"{tool} is tooling, not production"


def test_web_closure_reaches_components_through_pages_and_skips_e2e():
    closure = {str(p.relative_to(ROOT)) for p in lr.web_closure()}
    assert "web/components/shared/continuation-strip.tsx" in closure
    assert "web/hooks/use-data.ts" in closure
    assert not any("/e2e/" in c or c.endswith(".test.ts") for c in closure)


def test_no_test_or_fixture_file_is_ever_production():
    prod = {str(p.relative_to(ROOT)) for p in (lr.python_closure() | lr.web_closure())}
    assert not any(lr._is_non_prod(p) for p in prod)


def test_node_modules_is_never_followed():
    """Bare specifiers are dependencies. Following them would count vendor code, which the brief
    excludes and which would dwarf everything else."""
    assert lr.web_closure(), "closure is empty — the test proves nothing"
    assert not any("node_modules" in str(p) for p in lr.web_closure())


# --------------------------------------------------------------------------- counting
def test_python_docstrings_count_as_documentation_not_code():
    src = '"""Module doc.\n\nTwo lines.\n"""\n\n\ndef f():\n    """Doc."""\n    return 1  # trailing\n'
    lines, code = lr.count_python(src)
    assert lines == 9
    assert code == 2, "only `def f():` and `return 1` are code"


def test_a_url_inside_a_string_is_not_a_comment():
    """The failure a regex-based counter makes, and it silently undercounts every file with a link."""
    src = 'const u = "https://x.example.com/a";\nconst v = 1;\n'
    _lines, code = lr.count_c_like(src)
    assert code == 2


def test_block_and_line_comments_are_excluded_and_template_literals_are_not():
    src = "// a\n/* b\n   c */\nconst t = `line1\nline2`;\n"
    lines, code = lr.count_c_like(src)
    assert lines == 5
    assert code == 2, "both rows of the template literal are code; the three comment rows are not"


def test_totals_are_internally_consistent():
    """Every production file lands in exactly one module and one language, so the two breakdowns
    must sum to the same total. A file silently dropped by the module map would break this."""
    prod = lr.python_closure() | lr.web_closure() | lr.infra_files()
    lang_lines, lang_code, mod_lines, mod_code = lr.tally(prod)
    assert sum(lang_lines.values()) == sum(mod_lines.values())
    assert sum(lang_code.values()) == sum(mod_code.values())


def test_every_production_file_is_classified():
    """`Unclassified` is a visible bucket rather than a silent default, and it must stay empty —
    an unclassified file means the module map has drifted from the tree."""
    prod = lr.python_closure() | lr.web_closure() | lr.infra_files()
    stray = sorted(str(p.relative_to(ROOT)) for p in prod
                   if lr.module_of(str(p.relative_to(ROOT))) == "Unclassified")
    assert not stray, f"unclassified: {stray}"


def test_production_is_a_strict_subset_of_the_repository():
    prod = lr.python_closure() | lr.web_closure() | lr.infra_files()
    repo = lr.repo_files()
    missing = sorted(str(p.relative_to(ROOT)) for p in prod - repo)
    assert not missing, f"counted as production but not tracked by git: {missing}"
    assert len(prod) < len(repo)
