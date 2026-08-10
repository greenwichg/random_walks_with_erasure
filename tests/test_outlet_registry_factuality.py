"""The factuality columns — the RATER'S verdict, and the provenance rule that guards it.

Phase 2 wrote 41 already-sourced MBFC verdicts into the registry. The properties worth pinning are
the ones that keep a rating honest: that provenance is mandatory, that the rater's own six levels
survive instead of being collapsed into the older three, and — the one a data backfill must prove —
that writing them moved NOTHING that reads `credibility`.
"""
import csv
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import outlet_registry as reg   # noqa: E402


def _lint(tmp_path, *rows):
    p = tmp_path / "reg.csv"
    header = ("canonical,lean,aliases,country,region,city,scope,kind,credibility,"
              "factuality,factuality_source")
    p.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return {i["code"] for i in reg.lint_registry(str(p))}


def test_a_factuality_without_a_source_is_an_error(tmp_path):
    """The rule the column exists under. An unattributed rating is indistinguishable from a guess,
    and this file's whole discipline is that a rating is either sourced or absent."""
    assert "factuality_without_source" in _lint(tmp_path, "Some Outlet,0,x.example,,,,,,,high,")


def test_a_sourced_factuality_is_clean(tmp_path):
    assert _lint(tmp_path, "Some Outlet,0,x.example,,,,,,,high,mbfc") == set()


def test_an_unknown_level_or_source_is_rejected(tmp_path):
    """Both vocabularies are closed sets, so a typo is a lint error rather than a silent new
    category nobody can trace."""
    assert "invalid_factuality" in _lint(tmp_path, "A,0,a.example,,,,,,,quite good,mbfc")
    assert "invalid_factuality_source" in _lint(tmp_path, "B,0,b.example,,,,,,,high,some blog")


def test_a_source_with_no_verdict_is_flagged_as_half_finished(tmp_path):
    assert "source_without_factuality" in _lint(tmp_path, "C,0,c.example,,,,,,,,mbfc")


def test_the_shipped_registry_is_clean(tmp_path):
    """The real file, not a fixture — the backfill has to leave it lint-clean."""
    issues = reg.lint_registry()
    errors = [i for i in issues if i["severity"] == "error"]
    assert not errors, f"registry has lint errors: {errors[:5]}"


def test_the_raters_own_levels_survive(tmp_path):
    """The reason for a second column. MBFC publishes six levels; `credibility` has three. These
    five rows carry a published verdict of MIXED against a credibility of `medium` — collapsing
    them says something the rater did not."""
    for name in ("The Sun (UK)", "Daily Express", "Mirror", "The Australian", "Ahram Online"):
        o = reg.resolve(name)
        assert o is not None, name
        assert o.factuality == "mixed", f"{name} lost MBFC's verdict: {o.factuality!r}"
        assert o.credibility == "medium", f"{name} credibility was changed: {o.credibility!r}"
    # …and a level the 3-value column cannot express at all.
    assert reg.resolve("Nature").factuality == "very_high"


def test_the_backfill_changed_no_credibility_value():
    """A data backfill must not move anything that READS the old column. `credibility` is the
    clustering vote-gate's input (`is_low_credibility`), so a single changed cell here would be a
    silent clustering change smuggled in as data."""
    body = [ln for ln in (ROOT / "examples" / "data" / "outlet_registry.csv")
            .read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")]
    rows = [r for r in csv.DictReader(body) if (r.get("canonical") or "").strip()]
    cred = [r for r in rows if (r.get("credibility") or "").strip()]
    assert len(cred) == 70, f"credibility row count moved: {len(cred)} (was 70)"
    # The gate's own view of the file agrees.
    assert sum(1 for o in reg.default_registry().outlets() if o.credibility == "low") == 17


def test_every_written_verdict_carries_its_source():
    """The invariant, not the inventory.

    This asserted `len(outs) == 41` and failed the moment a second tranche was recorded — not
    because anything broke, but because the batch grew, which is the one thing a curation file is
    supposed to do. A count is a snapshot of how much of the backlog happened to be outstanding on
    the day it was written; the rule is that no verdict may be unattributed, and that holds at any
    size. The credibility test above still pins an exact number, and correctly so: that column is
    read by the clustering vote-gate, so a change in it IS the event worth failing on."""
    outs = [o for o in reg.default_registry().outlets() if o.factuality]
    assert outs, "the registry carries no factuality verdicts at all"
    assert all(o.factuality_source == "mbfc" for o in outs)
    assert all(o.factuality in reg.FACTUALITY for o in outs)
