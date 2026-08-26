"""The M9 runner and its ledger — `examples/audit_source_lifecycle.py` + the store tables.

`test_source_lifecycle.py` pins the policy. This pins the three things that make acting on it safe:

1. **The ledger is durable and append-only**, and `first_observed` only ever moves EARLIER. That is
   the retention-erosion fix: M8 measured an outlet's apparent history advancing 50 minutes in 18
   minutes of wall clock, and an observation window that shortens on its own would let a
   long-observed outlet fall back below the evaluation gate.
2. **A decision is not a claim about the running system.** A transition recorded with
   `applied=False` says what was decided; the emitted config is what makes it real.
3. **The emitted config is a diff of what is actually configured**, so a run with nothing to do
   prints nothing to deploy.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import audit_source_lifecycle as asl   # noqa: E402
import store as store_mod              # noqa: E402


@pytest.fixture
def st(tmp_path):
    return store_mod.Store(f"sqlite:///{tmp_path}/m9.db")


# --------------------------------------------------------------------------- the ledger

def test_streak_counts_consecutive_agreement_and_resets_on_disagreement(st):
    """Hysteresis needs memory across runs, and a run is a fresh process — so the streak lives in
    the store. Two evaluations pointing at DIFFERENT states must never add up to a confirmation."""
    for expected in (1, 2, 3):
        row = st.record_source_evaluation("a.example", target="B", verdict="PROMOTE TO TIER B")
        assert row["streak"] == expected
    assert st.record_source_evaluation("a.example", target="A",
                                       verdict="TIER A CANDIDATE")["streak"] == 1


def test_the_store_enforces_sample_spacing_through_the_same_arithmetic(st):
    """The store must not carry its own copy of the streak rule. Both it and the runner's dry-run
    path go through `source_lifecycle.next_streak`, because two copies is how the four drifted
    definitions in this series started."""
    a = st.record_source_evaluation("a.example", target="B", verdict="v",
                                    at="2026-08-01T00:00:00+00:00", min_spacing_days=6.0)
    soon = st.record_source_evaluation("a.example", target="B", verdict="v",
                                       at="2026-08-01T00:05:00+00:00", min_spacing_days=6.0)
    later = st.record_source_evaluation("a.example", target="B", verdict="v",
                                        at="2026-08-08T00:00:00+00:00", min_spacing_days=6.0)
    assert (a["streak"], a["held"]) == (1, False)
    assert (soon["streak"], soon["held"]) == (1, True), "five minutes later is the same sample"
    assert (later["streak"], later["held"]) == (2, False), "a week later is a new one"


def test_first_observed_only_ever_moves_earlier(st):
    """**The retention-erosion fix.** `MIN(created_at)` shrinks an outlet's apparent history as its
    oldest rows are trimmed. Once seen, the date is pinned, so an outlet cannot fall back below the
    14-day gate because retention ate the evidence that it had cleared it."""
    st.record_source_evaluation("a.example", target="B", verdict="v",
                                first_observed="2026-06-01T00:00:00+00:00")
    st.record_source_evaluation("a.example", target="B", verdict="v",
                                first_observed="2026-08-01T00:00:00+00:00")   # eroded — ignored
    assert st.source_lifecycle("a.example")["firstObserved"] == "2026-06-01T00:00:00+00:00"

    st.record_source_evaluation("a.example", target="B", verdict="v",
                                first_observed="2026-05-01T00:00:00+00:00")   # earlier — accepted
    assert st.source_lifecycle("a.example")["firstObserved"] == "2026-05-01T00:00:00+00:00"


def test_a_transition_is_a_decision_not_a_claim_about_the_running_system(st):
    """M9 never writes tier configuration. `applied=False` keeps the ledger honest about that: it
    records what was decided, not a state the running system is in."""
    st.record_source_evaluation("a.example", target="B", verdict="PROMOTE TO TIER B")
    st.apply_source_transition("a.example", to="B", reason="confirmed", automatic=True)
    event = st.source_lifecycle_events("a.example")[0]
    assert event["applied"] is False and event["automatic"] is True
    assert event["from"] == "shadow" and event["to"] == "B"


def test_the_event_log_is_append_only_and_carries_the_evidence(st):
    """"A retirement that deletes evidence cannot be audited later." The current-state row is
    overwritten by design; the events are not, and each carries the numbers the decision was made
    on — not today's, which will have changed."""
    st.apply_source_transition("a.example", to="B", reason="first", evidence={"articles": 999})
    st.apply_source_transition("a.example", to="shadow", reason="second", evidence={"articles": 12})
    events = st.source_lifecycle_events("a.example")
    assert [e["reason"] for e in events] == ["second", "first"]      # newest first
    assert events[1]["evidence"] == {"articles": 999}                # the original, not overwritten
    assert st.source_lifecycle("a.example")["state"] == "shadow"


def test_publisher_last_seen_is_the_silence_signal(st):
    """``MAX(created_at)``, and `created_at` for the same reason `first_seen` uses it: a backfilling
    provider inserting a month-old article means we heard from the source TODAY. `published_at`
    would call that source silent."""
    for i, pub in enumerate(["Echo", "echo", "Other"]):
        st.upsert_feed_article(canonical_url=f"h{i}.example/a", url=f"https://h{i}.example/a",
                               publisher=pub, source_publisher=None, title="t", description="",
                               body=None, published_at="2026-08-01T00:00:00+00:00",
                               source_feed="t", scored={})
    seen = st.publisher_last_seen()
    assert set(seen) == {"echo", "other"}
    assert st.publisher_last_seen({"echo"}).keys() == {"echo"}
    assert st.publisher_last_seen(set()) == {}


# --------------------------------------------------------------------------- the config diff

def test_config_diff_moves_an_outlet_between_the_two_variables():
    """A promotion out of shadow must REMOVE it from `RWE_CORPUS_SHADOW` as well as adding it to
    `RWE_CORPUS_TIER_B`. Adding without removing would leave it in both, and shadow wins — the
    outlet would stay invisible while the config claimed it had been promoted."""
    diff = asl.config_diff({"B": [], "shadow": ["a.example"]}, {"a.example": "B"})
    assert diff == {"RWE_CORPUS_SHADOW": "", "RWE_CORPUS_TIER_B": "a.example"}


def test_promotion_to_tier_a_removes_from_both_lists():
    """Tier A is the DEFAULT tier, so it has no variable of its own: an outlet reaches it by being
    named in neither list."""
    diff = asl.config_diff({"B": ["a.example"], "shadow": []}, {"a.example": "A"})
    assert diff == {"RWE_CORPUS_TIER_B": ""}


def test_dormant_and_retired_appear_in_no_serving_list():
    """They are ledger states. The probe-cadence change they imply belongs to the crawler (M6/M7),
    which is not built — so they must not silently mean "Tier A" by omission from both lists."""
    diff = asl.config_diff({"B": ["a.example"], "shadow": []}, {"a.example": "dormant"})
    assert diff == {"RWE_CORPUS_TIER_B": ""}


def test_a_run_with_nothing_to_do_emits_nothing():
    """No no-op diff, because a no-op diff invites a pointless redeploy."""
    assert asl.config_diff({"B": ["a.example"], "shadow": []}, {}) == {}


def test_config_diff_is_stable_under_reordering():
    """The emitted value must not churn because a set iterated differently — a diff that changes
    without a decision changing would train the reader to ignore it."""
    a = asl.config_diff({"B": ["b.example", "a.example"], "shadow": []}, {"c.example": "B"})
    b = asl.config_diff({"B": ["a.example", "b.example"], "shadow": []}, {"c.example": "B"})
    assert a == b == {"RWE_CORPUS_TIER_B": "a.example,b.example,c.example"}


# --------------------------------------------------------------------------- the runner's guards

def test_the_runner_never_writes_tier_configuration():
    """Structural, because this is the load-bearing promise of M9. The runner may write the LEDGER;
    it may not write the environment, open a file, or shell out to restart anything.

    Read from the AST rather than the text — the module's own instruction telling a human where to
    put the emitted value must not fail the test that enforces it never puts it there itself."""
    import ast
    tree = ast.parse((ROOT / "examples" / "audit_source_lifecycle.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""])
            for n in names:
                assert n.split(".")[0] not in {"subprocess", "shutil", "pathlib"}, \
                    f"M9 must not apply configuration — imports {n}"
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            assert name not in {"open", "system", "putenv", "setenv", "run", "Popen"}, \
                f"M9 must not apply configuration — calls {name}()"
        # `os.environ[...] = ...` — writing the very variables it is supposed to only emit.
        for tgt in (node.targets if isinstance(node, ast.Assign) else []):
            if isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Attribute):
                assert tgt.value.attr != "environ", "M9 must not write os.environ"


def test_the_runner_measures_with_m8_rather_than_redefining_it():
    """A lifecycle runner that re-derived the cohort, the counterfactual index or the syndication
    population would be a second definition of "what this outlet is worth", and M8's guards —
    self-scoring, the window bound, identity-resolved first-seen — would silently not apply to it.
    Four drifted definitions have already been corrected in this series."""
    src = (ROOT / "examples" / "audit_source_lifecycle.py").read_text()
    assert "asc.measure(" in src
    for banned in ("build_stories(", "assignment_index(", "carrier_index(", "pair_admits"):
        assert banned not in src, f"M9 must not re-derive M8's measurements — found {banned!r}"
