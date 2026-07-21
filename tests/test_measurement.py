"""Unit tests for examples/measurement.py — the generic Measurement metadata envelope (ADR-001).

The Measurement model generalises the Viewpoint coverage pilot to any dimension: every metric's
value is wrapped with **coverage** (scope — of the eligible reads, how many carried the signal),
**provenance** (where the value comes from), and an optional **confidence** (certainty, omitted
unless it genuinely represents prediction uncertainty).

These tests pin two dimensions:
* **Viewpoint** — coverage over the reader's *political* reads (finite outlet-registry lean =
  observed); this preserves the exact counts the retired ``viewpoint_coverage`` pilot produced.
* **Emotion** — coverage over *all* reads (a present emotion vector = observed), with confidence
  deliberately absent.

Inputs are exercised as both plain scored dicts (``store.get_reads`` shape) and
:class:`augmented_corpus.ScoredRead` objects (the engine's projection), since the leaf duck-types
both.
"""
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import measurement as m          # noqa: E402
import augmented_corpus as ac    # noqa: E402


def _read(*, political=False, lean="__omit__", emotion="__omit__",
          category="__omit__", register="__omit__"):
    """A minimal scored-read dict shaped like ``store.get_reads`` output."""
    d = {"political": political}
    if lean != "__omit__":
        d["lean"] = lean
    if emotion != "__omit__":
        d["emotion"] = emotion
    if category != "__omit__":
        d["category"] = category
    if register != "__omit__":
        d["register"] = register
    return d


# --------------------------------------------------------------------------- #
# Viewpoint dimension                                                          #
# --------------------------------------------------------------------------- #
def test_viewpoint_full_coverage():
    """Every political read has a finite registry lean → observed == eligible, nothing unknown."""
    reads = [_read(political=True, lean=-1.5), _read(political=True, lean=0.0),
             _read(political=True, lean=2.0)]
    env = m.viewpoint_measurement(reads)
    assert env["dimension"] == "viewpoint"
    assert env["coverage"] == {"observed": 3, "eligible": 3, "basis": "political_reads"}
    assert env["provenance"] == {"kind": "authoritative", "source": "outlet_registry"}
    assert "confidence" not in env


def test_viewpoint_partial_coverage():
    """Mixed known/unknown outlets → the split is counted exactly (NaN / None / absent are unknown)."""
    reads = [_read(political=True, lean=-1.5),          # known
             _read(political=True, lean=1.0),           # known
             _read(political=True, lean=float("nan")),  # unknown (NaN)
             _read(political=True, lean=None),          # unknown (None)
             _read(political=True)]                     # unknown (lean absent)
    env = m.viewpoint_measurement(reads)
    assert env["coverage"] == {"observed": 2, "eligible": 5, "basis": "political_reads"}


def test_viewpoint_zero_coverage():
    """All political reads are unknown-lean → observed 0; the mix reflects none of them."""
    reads = [_read(political=True, lean=float("nan")), _read(political=True, lean=None),
             _read(political=True)]
    env = m.viewpoint_measurement(reads)
    assert env["coverage"] == {"observed": 0, "eligible": 3, "basis": "political_reads"}


def test_viewpoint_non_political_reads_not_eligible():
    """Only political reads form the denominator; non-political reads are ignored entirely."""
    reads = [_read(political=False, lean=-1.0),   # non-political known lean — excluded
             _read(political=False),              # non-political — excluded
             _read(political=True, lean=1.0)]     # the only eligible read
    env = m.viewpoint_measurement(reads)
    assert env["coverage"] == {"observed": 1, "eligible": 1, "basis": "political_reads"}


def test_viewpoint_absent_when_no_political_reads():
    """No reads, or no political reads → no Viewpoint mix to describe → measurement is absent (None)."""
    assert m.viewpoint_measurement([]) is None
    assert m.viewpoint_measurement([_read(political=False, lean=0.0)]) is None


def test_viewpoint_non_numeric_lean_is_unknown():
    """A non-numeric lean (bad data) is unknown, never a crash."""
    reads = [_read(political=True, lean="left"), _read(political=True, lean=-1.0)]
    env = m.viewpoint_measurement(reads)
    assert env["coverage"]["observed"] == 1 and env["coverage"]["eligible"] == 2


def test_viewpoint_counts_are_consistent():
    """observed <= eligible always; unknown = eligible - observed is never negative."""
    reads = [_read(political=True, lean=-1.0), _read(political=True, lean=float("nan")),
             _read(political=False, lean=0.0), _read(political=True)]
    cov = m.viewpoint_measurement(reads)["coverage"]
    assert 0 <= cov["observed"] <= cov["eligible"]


# --------------------------------------------------------------------------- #
# Emotion dimension                                                           #
# --------------------------------------------------------------------------- #
def test_emotion_full_coverage():
    """Every read carries an emotion vector → observed == eligible; all reads are the denominator."""
    reads = [_read(emotion={"fear": 0.2, "neutral": 0.8}),
             _read(political=True, emotion={"neutral": 1.0})]
    env = m.emotion_measurement(reads)
    assert env["dimension"] == "emotion"
    assert env["coverage"] == {"observed": 2, "eligible": 2, "basis": "all_reads"}
    assert env["provenance"] == {"kind": "derived", "source": "baseline_lexical"}


def test_emotion_partial_coverage_all_reads_eligible():
    """Reads without a usable emotion vector are eligible (all reads) but not observed."""
    reads = [_read(emotion={"neutral": 1.0}),  # observed
             _read(emotion=None),              # eligible, not observed (no text -> n/a)
             _read(emotion={}),                # eligible, not observed (empty vector)
             _read(political=True)]            # eligible, not observed (emotion absent)
    env = m.emotion_measurement(reads)
    assert env["coverage"] == {"observed": 1, "eligible": 4, "basis": "all_reads"}


def test_emotion_confidence_is_omitted():
    """Confidence is intentionally absent for Emotion (ADR-001) — never a heuristic placeholder."""
    env = m.emotion_measurement([_read(emotion={"neutral": 1.0})])
    assert "confidence" not in env


def test_emotion_source_is_reported():
    """The provenance source names the configured emotion model."""
    env = m.emotion_measurement([_read(emotion={"neutral": 1.0})], source="llm_enricher")
    assert env["provenance"] == {"kind": "derived", "source": "llm_enricher"}


def test_emotion_absent_when_no_reads():
    """No reads at all → no dimension to describe → measurement is absent (None)."""
    assert m.emotion_measurement([]) is None


# --------------------------------------------------------------------------- #
# Topic dimension                                                             #
# --------------------------------------------------------------------------- #
def test_topic_full_coverage():
    """Every read has a resolved taxonomy topic → observed == eligible; all reads are the denominator."""
    reads = [_read(category="Politics"), _read(category="Sports"), _read(political=True, category="Business")]
    env = m.topic_measurement(reads)
    assert env["dimension"] == "topic"
    assert env["coverage"] == {"observed": 3, "eligible": 3, "basis": "all_reads"}
    assert env["provenance"] == {"kind": "derived", "source": "topic_classifier"}
    assert "confidence" not in env


def test_topic_uncategorized_reads_are_eligible_not_observed():
    """An empty category ("" — classify_topic's uncategorized) is eligible but not observed."""
    reads = [_read(category="Politics"),     # observed
             _read(category=""),             # eligible, not observed (uncategorized)
             _read(category="   "),          # eligible, not observed (whitespace only)
             _read(political=True)]          # eligible, not observed (category absent)
    env = m.topic_measurement(reads)
    assert env["coverage"] == {"observed": 1, "eligible": 4, "basis": "all_reads"}


def test_topic_absent_when_no_reads():
    """No reads at all → measurement is absent (None)."""
    assert m.topic_measurement([]) is None


# --------------------------------------------------------------------------- #
# Register dimension (reporting-vs-opinion)                                   #
# --------------------------------------------------------------------------- #
def test_register_full_coverage():
    """Every read carries a register score → observed == eligible; all reads are the denominator."""
    reads = [_read(register=0.8), _read(political=True, register=0.2)]
    env = m.register_measurement(reads)
    assert env["dimension"] == "register"
    assert env["coverage"] == {"observed": 2, "eligible": 2, "basis": "all_reads"}
    assert env["provenance"] == {"kind": "derived", "source": "baseline_lexical"}
    assert "confidence" not in env                                # omitted, as for the other derived dims


def test_register_partial_coverage_all_reads_eligible():
    """A NaN / None / absent register (no text -> n/a) is eligible but not observed."""
    reads = [_read(register=0.6),               # observed
             _read(register=float("nan")),      # eligible, not observed (no text)
             _read(register=None),              # eligible, not observed
             _read(political=True)]             # eligible, not observed (register absent → NaN default)
    env = m.register_measurement(reads)
    assert env["coverage"] == {"observed": 1, "eligible": 4, "basis": "all_reads"}


def test_register_source_shared_with_emotion():
    """Register and Emotion name the SAME enricher source (both set in one enrich call)."""
    reads = [_read(register=0.5, emotion={"neutral": 1.0})]
    reg = m.register_measurement(reads, source="llm_enricher")
    emo = m.emotion_measurement(reads, source="llm_enricher")
    assert reg["provenance"] == emo["provenance"] == {"kind": "derived", "source": "llm_enricher"}


def test_register_absent_when_no_reads():
    """No reads at all → measurement is absent (None)."""
    assert m.register_measurement([]) is None


# --------------------------------------------------------------------------- #
# Combined entry point + duck-typing + purity                                 #
# --------------------------------------------------------------------------- #
def test_measurements_for_reads_keys_and_absence():
    """The combined mapping keys each envelope by MetricKey. The three all-reads dimensions (topic,
    register, emotion) are present whenever there are reads; viewpoint is present only when there are
    political reads (its basis)."""
    reads = [_read(political=True, lean=-1.0, category="Politics", register=0.5,
                   emotion={"neutral": 1.0})]
    out = m.measurements_for_reads(reads, enricher_source="baseline_lexical")
    assert set(out) == {"topicDiversity", "reportingRatio", "emotionalBalance", "viewpointBalance"}
    assert out["topicDiversity"]["dimension"] == "topic"
    assert out["reportingRatio"]["dimension"] == "register"
    assert out["emotionalBalance"]["dimension"] == "emotion"
    assert out["viewpointBalance"]["dimension"] == "viewpoint"

    # No political reads → viewpointBalance absent; the three all-reads dimensions remain present.
    out2 = m.measurements_for_reads([_read(category="Sports", register=0.6, emotion={"neutral": 1.0})])
    assert set(out2) == {"topicDiversity", "reportingRatio", "emotionalBalance"}

    # No reads at all → empty mapping (every dimension absent).
    assert m.measurements_for_reads([]) == {}


def test_scored_read_objects_are_supported():
    """The leaf duck-types :class:`ScoredRead` objects (the engine's projection), not just dicts."""
    reads = [ac.ScoredRead(article_id="a", political=True, lean=-1.0,
                           emotion={"fear": 0.3, "neutral": 0.7}),
             ac.ScoredRead(article_id="b", political=True, lean=float("nan"))]  # unknown lean, no emotion
    out = m.measurements_for_reads(reads)
    assert out["viewpointBalance"]["coverage"] == {"observed": 1, "eligible": 2,
                                                   "basis": "political_reads"}
    assert out["emotionalBalance"]["coverage"] == {"observed": 1, "eligible": 2, "basis": "all_reads"}


def test_pure_does_not_mutate_reads():
    """Read-only: the input rows are untouched."""
    reads = [_read(political=True, lean=-1.0, emotion={"neutral": 1.0})]
    before = {"political": True, "lean": -1.0, "emotion": {"neutral": 1.0}}
    m.measurements_for_reads(reads)
    assert reads[0] == before
