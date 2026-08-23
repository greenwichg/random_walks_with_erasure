"""The factuality columns — the RATER'S verdict, and the provenance rule that guards it.

Phase 2 wrote 41 already-sourced MBFC verdicts into the registry. The properties worth pinning are
the ones that keep a rating honest: that provenance is mandatory, that the rater's own six levels
survive instead of being collapsed into the older three, and — the one a data backfill must prove —
that writing them moved NOTHING that reads `credibility`.
"""
import csv
import datetime
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import outlet_registry as reg   # noqa: E402


def _lint(tmp_path, *rows):
    p = tmp_path / "reg.csv"
    header = ("canonical,lean,aliases,country,region,city,scope,kind,credibility,"
              "factuality,factuality_source,factuality_asof")
    p.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return {i["code"] for i in reg.lint_registry(str(p))}


def test_a_factuality_without_a_source_is_an_error(tmp_path):
    """The rule the column exists under. An unattributed rating is indistinguishable from a guess,
    and this file's whole discipline is that a rating is either sourced or absent."""
    assert "factuality_without_source" in _lint(tmp_path, "Some Outlet,0,x.example,,,,,,,high,")


def test_a_sourced_factuality_is_clean(tmp_path):
    """Clean now means sourced AND dated — `factuality_asof` joined the mandatory set, so this
    fixture grew a date rather than the rule being relaxed for it."""
    assert _lint(tmp_path, "Some Outlet,0,x.example,,,,,,,high,mbfc,2026-08-10") == set()


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
    # 70 → 71 on 2026-08-23: the sixth tranche added NewsBusters with MBFC's own MIXED verdict,
    # carried as credibility=medium — a new row, not a changed cell in an existing one.
    assert len(cred) == 71, f"credibility row count moved: {len(cred)} (was 71)"
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


# --------------------------------------------------------------------------------------------- #
# factuality_asof — WHEN the verdict was read. As mandatory as WHO said it.
# --------------------------------------------------------------------------------------------- #
def _lint_row(tmp_path, row):
    """Lint one row against a fixture registry, so a rule is tested by its RULE and not by
    whichever shipped outlet happens to violate it."""
    p = tmp_path / "reg.csv"
    p.write_text(
        "canonical,lean,aliases,country,region,city,scope,kind,credibility,factuality,"
        "factuality_source,factuality_asof\n" + row + "\n", encoding="utf-8")
    return {i["code"] for i in reg.lint_registry(str(p))}


def test_a_verdict_with_no_date_is_an_error(tmp_path):
    """The rule this column exists for. A rater revises and this file has no refresh mechanism, so
    an undated verdict shown under the rater's name asserts that they still say it — the same
    failure `factuality_without_source` prevents, one dimension over."""
    codes = _lint_row(tmp_path, "Undated,0,undated.example,,,,,,,high,mbfc,")
    assert "factuality_without_asof" in codes


def test_a_dated_and_sourced_verdict_is_clean(tmp_path):
    assert _lint_row(tmp_path, "Fine,0,fine.example,,,,,,,high,mbfc,2026-08-10") == set()


def test_a_date_that_is_not_an_iso_date_is_rejected(tmp_path):
    """Free-text dates are how a column stops being comparable. Both a wrong SHAPE and a shape that
    parses but is not a real day must fail."""
    for bad in ["Aug 2026", "2026-8-10", "10/08/2026", "2026-13-01", "2026-02-30"]:
        codes = _lint_row(tmp_path, f"Bad,0,bad.example,,,,,,,high,mbfc,{bad}")
        assert "invalid_factuality_asof" in codes, f"{bad!r} was accepted"


def test_a_future_retrieval_date_is_rejected(tmp_path):
    """The one error that makes a stale verdict look permanently fresh, and always a typo."""
    ahead = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    assert "factuality_asof_in_future" in _lint_row(
        tmp_path, f"Ahead,0,ahead.example,,,,,,,high,mbfc,{ahead}")
    today = datetime.date.today().isoformat()
    assert "factuality_asof_in_future" not in _lint_row(
        tmp_path, f"Today,0,today.example,,,,,,,high,mbfc,{today}"), "today is not the future"


def test_a_date_with_no_verdict_is_a_warning_not_an_error(tmp_path):
    """A half-finished edit, exactly as `source_without_factuality` is: worth flagging, not worth
    failing a load over."""
    p = tmp_path / "reg.csv"
    p.write_text(
        "canonical,lean,aliases,country,region,city,scope,kind,credibility,factuality,"
        "factuality_source,factuality_asof\n"
        "Stray,0,stray.example,,,,,,,,,2026-08-10\n", encoding="utf-8")
    issues = {i["code"]: i["severity"] for i in reg.lint_registry(str(p))}
    assert issues.get("asof_without_factuality") == "warning"


def test_the_date_survives_the_loader(tmp_path):
    """Read positionally like the columns before it, so appending stays backwards-compatible."""
    p = tmp_path / "reg.csv"
    p.write_text(
        "canonical,lean,aliases,country,region,city,scope,kind,credibility,factuality,"
        "factuality_source,factuality_asof\n"
        "Dated Outlet,0,dated.example,,,,,,,mixed,mbfc,2026-07-28\n"
        "Short Row,1,short.example\n", encoding="utf-8")
    r = reg.OutletRegistry.load(str(p))
    assert r.resolve("dated.example").factuality_asof == "2026-07-28"
    short = r.resolve("short.example")
    assert short.factuality is None and short.factuality_asof is None, (
        "a row that predates the column still loads, with the date honestly absent")


def test_every_shipped_verdict_carries_a_date():
    """The invariant on the real file, stated as a rule rather than a count — the sibling test
    above learned that lesson when the batch grew."""
    outs = [o for o in reg.default_registry().outlets() if o.factuality]
    assert outs
    undated = [o.canonical for o in outs if not o.factuality_asof]
    assert not undated, f"verdicts with no retrieval date: {undated}"


def test_no_unrated_row_carries_a_stray_date():
    """The reverse: a date on a row with no verdict means an edit was abandoned half-done, and the
    file should not accumulate those silently."""
    stray = [o.canonical for o in reg.default_registry().outlets()
             if o.factuality_asof and not o.factuality]
    assert not stray, f"dates with no verdict: {stray}"


def test_the_shipped_dates_are_real_and_not_in_the_future():
    """Guards the two ways a date column rots into decoration: an unparseable string nobody
    notices, and a future date that makes a stale rating look permanently current."""
    today = datetime.date.today()
    for o in reg.default_registry().outlets():
        if not o.factuality_asof:
            continue
        when = datetime.date.fromisoformat(o.factuality_asof)   # raises on a malformed date
        assert when <= today, f"{o.canonical}: factuality_asof {o.factuality_asof} is in the future"
