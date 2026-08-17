"""Tests for examples/audit_v1_verifier.py — the V1-prime harness, with NO API calls ever.

The model sits behind an injected adapter, so these tests pin the harness semantics that must
not drift regardless of which model is plugged in: the verdict-store resume (a rerun makes zero
model calls), quote-verification demotion, the exhibit KILL gate, all four scoring sections,
the labeled-sheet guard, and the real adapter's fail-closed shape (urlopen monkeypatched — no
network is ever touched from a test).
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import audit_v1_verifier as v1   # noqa: E402


def _side(headline, dek="", published="2026-08-14T10:00:00Z", entities=(), countries=()):
    return {"headline": headline, "dek": dek, "publishedAt": published,
            "entities": list(entities), "countries": list(countries)}


def _row(pid, klass, a, b, label, source, draft=""):
    return {"pair_id": pid, "class": klass, "a": a, "b": b,
            "draft_label": draft, "draft_rule": "", "label": label,
            "label_source": source, "label_evidence": {}, "rubric_version": "v1", "notes": ""}


def _sheet():
    """Six rows covering every provenance tier; truth keyed by unordered headline pair so the
    scripted adapter answers identically under the symmetry swap."""
    a1 = _side("Marvel reveals new X-Men cast at D23", dek="Five actors join the MCU film.")
    b1 = _side("X-Men cast revealed by Marvel at D23 expo")
    a2 = _side("The Paper season 2 release date announced")
    b2 = _side("Mirzapur movie trailer and cast release date")
    a3 = _side("Antam gold price rises Monday", published="2026-08-10T09:00:00Z")
    b3 = _side("Cricket fixture list for September announced")
    a4 = _side("Storm Dora makes landfall in Portugal", entities=("Dora",))
    b4 = _side("Storm Dora landfall hits Portugal coast", entities=("Dora",))
    a5 = _side("Hayden Panettiere dies at 36", entities=("Hayden Panettiere",))
    b5 = _side("Hayden Panettiere: a life in photos", entities=("Hayden Panettiere",))
    a6 = _side("Quarterly earnings beat expectations")
    b6 = _side("Local marathon draws record crowd")
    rows = [
        _row("p_01", "exhibit:xmen_d23", a1, b1, "same_event", "human-exhibit"),
        _row("p_02", "exhibit:template_weld", a2, b2, "different_event", "human-exhibit"),
        _row("p_03", "router_edge", a3, b3, "different_event", "rule:no-affinity"),
        _row("p_04", "near_merge", a4, b4, "same_event", "rule:near-dup"),
        _row("p_05", "single_name", a5, b5, "", "draft-only", draft="same_event"),
        _row("p_06", "random_negative", a6, b6, "uncertain", "undetermined"),
    ]
    truth = {
        frozenset((a1["headline"], b1["headline"])): "same_event",
        frozenset((a2["headline"], b2["headline"])): "different_event",
        frozenset((a3["headline"], b3["headline"])): "different_event",
        frozenset((a4["headline"], b4["headline"])): "same_event",
        frozenset((a5["headline"], b5["headline"])): "same_event",
        frozenset((a6["headline"], b6["headline"])): "different_event",
    }
    return rows, truth


class FakeAdapter:
    """Implements exactly the two members the harness contracts on (name, verdict) plus the
    usage counters the report prints. Spans default to each side's own headline — real text in
    both call orders — so quote failures happen only where a test injects them."""

    name = "fake-model"

    def __init__(self, decide, spans=None):
        self.decide = decide
        self.spans = spans or {}
        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0

    def verdict(self, a, b):
        self.calls += 1
        k = frozenset((a["headline"], b["headline"]))
        sa, sb = self.spans.get(k, (a["headline"], b["headline"]))
        return {"verdict": self.decide(a, b),
                "quoted_span_a": sa, "quoted_span_b": sb, "reason": "scripted"}


def _decider(truth):
    return lambda a, b: truth[frozenset((a["headline"], b["headline"]))]


def _write(tmp_path, rows):
    p = tmp_path / "labeled.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


ARGS_SMALL = ["--stability-sample", "2", "--replays", "3", "--symmetry-sample", "2"]


def test_screening_pass_all_sections_and_resume(tmp_path, capsys):
    rows, truth = _sheet()
    pairs = _write(tmp_path, rows)
    out = tmp_path / "verdicts.jsonl"
    argv = ["--pairs", str(pairs), "--out", str(out)] + ARGS_SMALL

    ad = FakeAdapter(_decider(truth))
    assert v1.main(argv, adapter=ad) == 0
    text = capsys.readouterr().out
    for needed in ("MODEL UNDER TEST", "fake-model", "NOT Claude Opus",
                   "V1a", "V1b", "V1c", "V1d", "SCREENING PASS", "NOT production wiring"):
        assert needed in text
    # 6 judged + 2 sample pairs x (3-1) replays + 2 symmetry swaps
    assert ad.calls == 12
    assert len(out.read_text().splitlines()) == len(rows)
    assert len((tmp_path / "verdicts.jsonl.replays").read_text().splitlines()) == 4
    assert len((tmp_path / "verdicts.jsonl.sym").read_text().splitlines()) == 2
    # exhibit gate shows both authoritative pairs as hits; shortlist = the decided undetermined
    assert text.count("[           ok]") == 2
    assert "human shortlist (1 pairs" in text and "p_06" in text

    # resume: a second run must make ZERO model calls, append nothing, and reach the
    # same verdict from the stores alone
    ad2 = FakeAdapter(lambda a, b: "same_event")
    assert v1.main(argv, adapter=ad2) == 0
    assert ad2.calls == 0
    assert "SCREENING PASS" in capsys.readouterr().out
    assert len(out.read_text().splitlines()) == len(rows)
    assert len((tmp_path / "verdicts.jsonl.replays").read_text().splitlines()) == 4
    assert len((tmp_path / "verdicts.jsonl.sym").read_text().splitlines()) == 2


def test_unverifiable_span_demotes_to_uncertain(tmp_path, capsys):
    rows, truth = _sheet()
    pairs = _write(tmp_path, rows)
    out = tmp_path / "verdicts.jsonl"
    bad = frozenset((rows[4]["a"]["headline"], rows[4]["b"]["headline"]))
    ad = FakeAdapter(_decider(truth), spans={bad: ("this span appears nowhere", "nor this")})
    assert v1.main(["--pairs", str(pairs), "--out", str(out)] + ARGS_SMALL, adapter=ad) == 0
    text = capsys.readouterr().out

    rec = next(json.loads(l) for l in out.read_text().splitlines()
               if json.loads(l)["pair_id"] == "p_05")
    assert rec["verdict"] == "uncertain" and rec["demoted"] and not rec["quote_ok"]
    assert rec["raw"]["verdict"] == "same_event"          # the raw answer is preserved
    assert "failures: 1 decided verdicts demoted" in text
    assert "FAIL —" in text and "SCREENING PASS" not in text   # 5/6 quotes < 99% bar


def test_exhibit_false_same_is_a_kill(tmp_path, capsys):
    rows, truth = _sheet()
    weld = frozenset((rows[1]["a"]["headline"], rows[1]["b"]["headline"]))
    truth = {**truth, weld: "same_event"}                 # the disqualifying answer
    pairs = _write(tmp_path, rows)
    out = tmp_path / "verdicts.jsonl"
    assert v1.main(["--pairs", str(pairs), "--out", str(out)] + ARGS_SMALL,
                   adapter=FakeAdapter(_decider(truth))) == 0
    text = capsys.readouterr().out
    assert "DISQUALIFYING" in text
    assert "KILL" in text and "SCREENING PASS" not in text


def test_quote_ok_checks_the_full_presented_text():
    s = _side("Headline Words Here", dek="A longer dek sentence.",
              entities=("Hayden Panettiere",), countries=("us",))
    assert v1.quote_ok("headline words", s)
    assert v1.quote_ok("longer  dek", s)                  # whitespace-normalized
    assert v1.quote_ok("Hayden Panettiere", s)            # the entities line is presented text
    assert not v1.quote_ok("fabricated span", s)
    assert not v1.quote_ok("", s)
    assert not v1.quote_ok(None, s)


def test_refuses_a_sheet_without_provenance(tmp_path, capsys):
    raw = {"pair_id": "p_x", "class": "router_edge", "a": _side("A"), "b": _side("B"),
           "draft_label": "", "draft_rule": "", "label": "", "rubric_version": "v1",
           "notes": ""}
    p = tmp_path / "raw.jsonl"
    p.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    rc = v1.main(["--pairs", str(p), "--out", str(tmp_path / "o.jsonl")],
                 adapter=FakeAdapter(lambda a, b: "uncertain"))
    assert rc == 1
    assert "label_source" in capsys.readouterr().out


def test_gemini_adapter_fails_closed_without_network(monkeypatch):
    monkeypatch.setattr(v1.time, "sleep", lambda s: None)

    def boom(*a, **k):
        raise OSError("no network in tests")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    out = v1.GeminiAdapter("test-key").verdict(_side("A"), _side("B"))
    assert out["verdict"] == "uncertain" and out.get("_api_error")
