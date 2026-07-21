"""Dimensional coverage for the Viewpoint dimension (docs/DIMENSIONAL_COVERAGE.md).

The coverage pilot: make the *scope* of the Viewpoint mix explicit — how many of a reader's
political reads carry an authoritative (outlet-registry / AllSides) lean, vs. how many are
unknown-lean and therefore not represented in the mix. Coverage is scope, not confidence.

These tests pin the three cases the RFC calls out — full (100%), partial, and zero authoritative
coverage — plus the eligibility boundary and the finite-lean (NaN → unknown) predicate.
"""
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import viewpoint_coverage as vc  # noqa: E402


def _read(*, political, lean="__omit__"):
    """A minimal read row shaped like store.list_reads output (only the fields coverage reads)."""
    scored = {"political": political}
    if lean != "__omit__":
        scored["lean"] = lean
    return {"scored": scored}


def test_full_authoritative_coverage():
    """Every political read has a finite registry lean → 100% covered, nothing unknown."""
    reads = [_read(political=True, lean=-1.5), _read(political=True, lean=0.0),
             _read(political=True, lean=2.0)]
    cov = vc.viewpoint_coverage(reads)
    assert cov["eligiblePoliticalReads"] == 3
    assert cov["authoritativeLeanReads"] == 3
    assert cov["unknownLeanReads"] == 0
    assert cov["provenance"] == "outlet_registry"


def test_partial_coverage():
    """Mixed known/unknown outlets → the split is counted exactly."""
    reads = [_read(political=True, lean=-1.5),          # known
             _read(political=True, lean=1.0),           # known
             _read(political=True, lean=float("nan")),  # unknown outlet (NaN)
             _read(political=True, lean=None),          # unknown (None)
             _read(political=True)]                     # unknown (lean absent)
    cov = vc.viewpoint_coverage(reads)
    assert cov["eligiblePoliticalReads"] == 5
    assert cov["authoritativeLeanReads"] == 2
    assert cov["unknownLeanReads"] == 3


def test_zero_authoritative_coverage():
    """All political reads are unknown-lean → 0% authoritative; the mix reflects none of them."""
    reads = [_read(political=True, lean=float("nan")), _read(political=True, lean=None),
             _read(political=True)]
    cov = vc.viewpoint_coverage(reads)
    assert cov["eligiblePoliticalReads"] == 3
    assert cov["authoritativeLeanReads"] == 0
    assert cov["unknownLeanReads"] == 3


def test_non_political_reads_are_not_eligible():
    """Only political reads form the denominator; non-political reads are ignored entirely."""
    reads = [_read(political=False, lean=-1.0),   # non-political known lean — excluded
             _read(political=False),              # non-political — excluded
             _read(political=True, lean=1.0)]     # the only eligible read
    cov = vc.viewpoint_coverage(reads)
    assert cov["eligiblePoliticalReads"] == 1
    assert cov["authoritativeLeanReads"] == 1
    assert cov["unknownLeanReads"] == 0


def test_empty_and_no_political_reads():
    """No reads, or no political reads, yields an all-zero (honest) envelope — never an error."""
    for reads in ([], [_read(political=False, lean=0.0)]):
        cov = vc.viewpoint_coverage(reads)
        assert cov["eligiblePoliticalReads"] == 0
        assert cov["authoritativeLeanReads"] == 0
        assert cov["unknownLeanReads"] == 0


def test_non_numeric_lean_is_unknown():
    """A non-numeric lean (bad data) is unknown, not a crash."""
    reads = [_read(political=True, lean="left"), _read(political=True, lean=-1.0)]
    cov = vc.viewpoint_coverage(reads)
    assert cov["eligiblePoliticalReads"] == 2
    assert cov["authoritativeLeanReads"] == 1
    assert cov["unknownLeanReads"] == 1


def test_counts_are_internally_consistent():
    """authoritative + unknown always equals eligible (the invariant the UI relies on)."""
    reads = [_read(political=True, lean=-1.0), _read(political=True, lean=float("nan")),
             _read(political=False, lean=0.0), _read(political=True)]
    cov = vc.viewpoint_coverage(reads)
    assert cov["authoritativeLeanReads"] + cov["unknownLeanReads"] == cov["eligiblePoliticalReads"]


def test_pure_does_not_mutate_reads():
    """Read-only: the input rows are untouched."""
    reads = [_read(political=True, lean=-1.0)]
    before = {"scored": {"political": True, "lean": -1.0}}
    vc.viewpoint_coverage(reads)
    assert reads[0] == before
