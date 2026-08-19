"""Tests for examples/audit_semantic_arms.py — the three-arm comparison, with NO model weights.

The arms are injected, so these pin the methodology that makes the comparison trustworthy:
the threshold is calibrated on rule tiers only (never on the exhibits it is then graded
against), the exhibit gate counts false merges in the right direction, the S4 filter band is a
kill criterion rather than a knob, and an unavailable checkpoint is reported rather than
silently substituted.
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import audit_semantic_arms as sa   # noqa: E402


def _row(pid, klass, ha, hb, label, source):
    return {"pair_id": pid, "class": klass,
            "a": {"headline": ha, "dek": "", "publishedAt": "2026-08-14T10:00:00Z"},
            "b": {"headline": hb, "dek": "", "publishedAt": "2026-08-14T11:00:00Z"},
            "label": label, "label_source": source, "draft_label": "", "notes": ""}


def _sheet():
    """4 exhibits + 10 no-affinity (different) + 4 near-dup (same)."""
    rows = [
        _row("p_ex1", "exhibit:xmen-pair", "X-Men cast revealed", "X-Men cast at D23",
             "same_event", "human-exhibit"),
        _row("p_ex2", "exhibit:xmen-paper", "X-Men cast revealed", "The Paper S2 release date",
             "different_event", "human-exhibit"),
        _row("p_ex3", "exhibit:hayden-family", "Hayden dies at 36", "Hayden: a life in photos",
             "same_event", "human-exhibit"),
        _row("p_ex4", "exhibit:remains", "Remains found Toronto", "Remains found Palomar",
             "different_event", "human-exhibit"),
    ]
    rows += [_row(f"p_na{i}", "random_negative", f"Alpha story {i}", f"Beta story {i}",
                  "different_event", "rule:no-affinity") for i in range(10)]
    rows += [_row(f"p_nd{i}", "near_merge", f"Storm hits coast {i}", f"Storm strikes coast {i}",
                  "same_event", "rule:near-dup") for i in range(4)]
    return rows


class StubArm:
    """Implements the arm contract with scripted scores keyed by pair_id."""

    has_spans = False
    kind = "bi-encoder"

    def __init__(self, name, table, default=0.5, fail=None):
        self.name = name
        self.table = table
        self.default = default
        self.fail = fail
        self.encode_seconds = 0.01
        self.pair_seconds = 0.01
        self.n_texts = 8

    def load(self):
        if self.fail:
            raise OSError(self.fail)
        return self

    def scores(self, rows, governor=None, batch=8):
        for i in range(0, len(rows), batch):        # the governor sees a decision point per batch
            (governor or sa.NullGovernor()).guard()
        return [self.table.get(r["pair_id"], self.default) for r in rows]


def _separable():
    """A well-behaved arm: rule tiers cleanly separated, exhibits all correct."""
    t = {"p_ex1": 0.95, "p_ex2": 0.08, "p_ex3": 0.90, "p_ex4": 0.05}
    t.update({f"p_na{i}": 0.05 + i * 0.005 for i in range(10)})
    t.update({f"p_nd{i}": 0.90 + i * 0.005 for i in range(4)})
    return t


def _template_confused():
    """The hypothesized real shape: template/place pairs score HIGH (false merges) and the
    reactive-coverage family scores as low as unrelated pairs (false split), while the rule
    tiers still separate cleanly — which is exactly why calibrating on the rule tiers and
    grading on the exhibits is the honest test rather than a circular one."""
    t = _separable()
    t.update({"p_ex2": 0.93, "p_ex4": 0.91, "p_ex3": 0.07})
    return t


def test_threshold_is_calibrated_on_rule_tiers_only():
    rows = _sheet()
    table = _template_confused()
    scores = [table[r["pair_id"]] for r in rows]
    thr = sa.calibrate(rows, scores)
    # it must land in the gap between the tiers (no-affinity tops out at 0.095, near-dup
    # starts at 0.90); the exhibits' confused scores must not move it at all
    assert 0.095 <= thr <= 0.90
    exhibit_free = [r for r in rows if r["label_source"] != "human-exhibit"]
    assert thr == sa.calibrate(exhibit_free, [table[r["pair_id"]] for r in exhibit_free])


def test_false_merges_are_counted_and_reported(tmp_path, capsys):
    rows = _sheet()
    arm = StubArm("stub/confused", _template_confused())
    res = sa.run_arm(arm, rows, str(tmp_path))
    text = capsys.readouterr().out
    assert res["s1"] == 2                      # xmen-paper and remains, both labeled different
    assert res["s2"] == 1                      # only xmen-pair survives
    assert "FALSE MERGE" in text and "false split" in text
    assert "S1 false merges on exhibits : 2" in text and "FAIL" in text
    # the per-arm score file is written for later hand-reading
    written = (tmp_path / "scores_stub_confused.jsonl").read_text().splitlines()
    assert len(written) == len(rows)
    assert json.loads(written[0])["model"] == "stub/confused"


def test_a_separable_arm_passes_its_bars(tmp_path, capsys):
    rows = _sheet()
    res = sa.run_arm(StubArm("stub/clean", _separable()), rows, str(tmp_path))
    text = capsys.readouterr().out
    assert res["s1"] == 0 and res["s2"] == 4 and res["s3"] == 0
    assert "S1 false merges on exhibits : 0" in text
    assert res["s4"] is not None               # a usable filter band exists


def test_filter_band_is_a_kill_criterion_not_a_knob():
    rows = _sheet()
    # scores carry no information: every pair sits at the same value, so no (lo, hi) can
    # decide 30% of pairs without errors
    flat = {r["pair_id"]: 0.5 for r in rows}
    scores = [flat[r["pair_id"]] for r in rows]
    assert sa.widest_band(rows, scores) is None
    good = [_separable()[r["pair_id"]] for r in rows]
    band = sa.widest_band(rows, good)
    assert band is not None and band[2] >= 0.30 and band[3] == 0


def test_unavailable_checkpoint_is_reported_never_substituted(tmp_path, capsys):
    res = sa.run_arm(StubArm("stub/missing", {}, fail="404 not found"), _sheet(), str(tmp_path))
    text = capsys.readouterr().out
    assert res["available"] is False
    assert "CHECKPOINT UNAVAILABLE" in text and "No substitute checkpoint" in text
    assert "S1" not in text                    # no bars are reported for an unmeasured arm


def test_cross_script_detection_flags_translation_pairs():
    en = {"headline": "Garmin CIRQA smart ring listed in database", "dek": ""}
    ja = {"headline": "ガーミンのスマートリング「CIRQA」が認証データベースに登録される", "dek": ""}
    assert sa.cross_script(en, ja)
    assert not sa.cross_script(en, {"headline": "Garmin ring appears in filing", "dek": ""})


def test_llm_column_reports_pending_when_the_store_is_void(tmp_path, capsys):
    rows = _sheet()
    pairs = tmp_path / "labeled.jsonl"
    pairs.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    void = tmp_path / "llm.jsonl"
    void.write_text("\n".join(json.dumps(
        {"pair_id": r["pair_id"], "verdict": "uncertain", "api_error": True}) for r in rows),
        encoding="utf-8")
    rc = sa.main(["--pairs", str(pairs), "--out-dir", str(tmp_path / "o"),
                  "--llm-store", str(void)],
                 arms=[StubArm("stub/clean", _separable())])
    text = capsys.readouterr().out
    assert rc == 0
    assert "the LLM store is VOID" in text and "PENDING, not zero" in text


def test_governor_aborts_the_arm_and_discards_partial_work(tmp_path, capsys):
    class TrippedGovernor(sa.NullGovernor):
        def __init__(self):
            self.calls = 0

        def guard(self):
            self.calls += 1
            if self.calls > 2:                     # trip partway through the batches
                raise sa.ResourceAbort("host memory fell to 1.10 GB available (floor 1.50)")

        def report(self):
            return "tripped"

    res = sa.run_arm(StubArm("stub/clean", _separable()), _sheet(), str(tmp_path),
                     TrippedGovernor())
    text = capsys.readouterr().out
    assert res["aborted"] is True and res["available"] is False
    assert "ABORTED MID-RUN" in text and "1.10 GB" in text
    assert "Partial work is discarded rather than reported as a result" in text
    assert "S1" not in text                        # no bars are reported from an aborted arm
    assert not list(tmp_path.glob("scores_*.jsonl"))


def test_governor_thresholds_fire_on_each_resource(tmp_path):
    g = sa.Governor(str(tmp_path), min_avail_gb=0.0, min_disk_gb=0.0, max_load_ratio=99.0)
    assert g.sample() is None                      # a healthy host trips nothing
    g.guard()                                      # and guard() is a no-op
    assert sa.Governor(str(tmp_path), min_avail_gb=10 ** 6).sample().startswith("host memory")
    assert sa.Governor(str(tmp_path), min_disk_gb=10 ** 6).sample().startswith("free disk")
    assert sa.Governor(str(tmp_path), max_load_ratio=0.0).sample().startswith("host load")


def test_lexical_arm_needs_no_dependencies_and_ranks_by_shared_idf():
    rows = _sheet()
    arm = sa.LexicalArm().load()
    scores = arm.scores(rows)
    assert len(scores) == len(rows)
    by_id = {r["pair_id"]: s for r, s in zip(rows, scores)}
    # near-dup fixtures share most tokens; the no-affinity fixtures share none
    assert by_id["p_nd0"] > by_id["p_na0"]
    assert all(0.0 <= s <= 1.0000001 for s in scores)
    assert arm.n_texts > 0


def test_probe_is_labeled_synthetic_and_kept_out_of_the_benchmark(tmp_path, capsys):
    rows = _sheet()
    pairs = tmp_path / "labeled.jsonl"
    pairs.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    sa.main(["--pairs", str(pairs), "--out-dir", str(tmp_path / "o"), "--probe"],
            arms=[StubArm("stub/clean", _separable())])
    text = capsys.readouterr().out
    assert "CONSTRUCTED pairs, NOT production text, NOT benchmark evidence" in text
    assert "multilingual/translation" in text
    assert "NOT measurable on this sheet" in text      # no cross-script pairs in the fixture
