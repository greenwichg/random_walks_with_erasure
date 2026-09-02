"""Stage 0.4 — the V1 harness's ``claude`` arm scores the PRODUCTION judge adapter.

``RWE_EVENT_JUDGE=1`` is earned by ``event_identity.ClaudeAdapter`` — its own prompt, its own
quote demotion, the deployed model — clearing the V1 bars, not by the Gemini research arm having
cleared them. These pin the wrapper's field mapping (the sheet's sides become exactly the
article shape the worker hands the adapter; the adapter's quotes come back as the harness's
spans), the fail-closed passthroughs, the key guard, and that ``--adapter claude`` runs the
whole scoring pass over the wrapper. No network is ever touched.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "examples"))

import audit_v1_verifier as v1   # noqa: E402
import event_identity            # noqa: E402


def _side(headline, dek="", published="2026-08-14T10:00:00Z"):
    return {"headline": headline, "dek": dek, "publishedAt": published,
            "entities": ["should not be shown"], "countries": ["ZZ"]}


class FakeProductionAdapter:
    """Answers in the PRODUCTION adapter's shape: quote_a/quote_b, _demoted, _api_error."""
    name = "claude-haiku-4-5"

    def __init__(self, script):
        self.script = script
        self.seen = []
        self.calls = 0
        self.tokens_in = 7
        self.tokens_out = 3

    def verdict(self, a, b):
        self.seen.append((a, b))
        self.calls += 1
        return self.script(a, b)


def test_the_wrapper_shows_the_adapter_what_production_shows_and_maps_its_answer():
    inner = FakeProductionAdapter(lambda a, b: {"verdict": "different_event",
                                                "quote_a": "eye drops", "quote_b": "fruit bars"})
    ad = v1.ClaudeHarnessAdapter(inner)
    out = ad.verdict(_side("Eye drops recalled", dek="FDA warns."), _side("Fruit bars recalled"))
    a, b = inner.seen[0]
    assert a == {"headline": "Eye drops recalled", "description": "FDA warns.",
                 "publishedAt": "2026-08-14T10:00:00Z"}, \
        "the article shape the worker hands the adapter — no entities, no countries"
    assert b["headline"] == "Fruit bars recalled" and b["description"] == ""
    assert out["verdict"] == "different_event"
    assert out["quoted_span_a"] == "eye drops" and out["quoted_span_b"] == "fruit bars"
    assert "_api_error" not in out
    assert ad.name == "claude-haiku-4-5" and ad.calls == 1 and (ad.tokens_in, ad.tokens_out) == (7, 3)


def test_adapter_demotion_and_api_error_pass_through_fail_closed():
    demoted = v1.ClaudeHarnessAdapter(FakeProductionAdapter(
        lambda a, b: {"verdict": "uncertain", "quote_a": "", "quote_b": "",
                      "_demoted": "quote-verification"}))
    out = demoted.verdict(_side("x"), _side("y"))
    assert out["verdict"] == "uncertain" and "quote-verification" in out["reason"]
    assert not out["quoted_span_a"] and "_api_error" not in out

    errored = v1.ClaudeHarnessAdapter(FakeProductionAdapter(
        lambda a, b: {"verdict": "uncertain", "quote_a": "", "quote_b": "",
                      "_api_error": "api-error after retries: HTTP 529"}))
    out = errored.verdict(_side("x"), _side("y"))
    assert out["_api_error"] is True and out["verdict"] == "uncertain"
    assert "HTTP 529" in out["reason"]


def _sheet(tmp_path):
    a1, b1 = _side("Marvel reveals new X-Men cast at D23"), _side("X-Men cast revealed at D23")
    a2, b2 = _side("The Paper season 2 date announced"), _side("Mirzapur trailer and cast date")
    rows = [
        {"pair_id": "p_01", "class": "exhibit:xmen", "a": a1, "b": b1, "draft_label": "",
         "draft_rule": "", "label": "same_event", "label_source": "human-exhibit",
         "label_evidence": {}, "rubric_version": "v1", "notes": ""},
        {"pair_id": "p_02", "class": "exhibit:weld", "a": a2, "b": b2, "draft_label": "",
         "draft_rule": "", "label": "different_event", "label_source": "human-exhibit",
         "label_evidence": {}, "rubric_version": "v1", "notes": ""},
    ]
    p = tmp_path / "labeled.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def test_the_claude_arm_refuses_to_run_without_the_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rc = v1.main(["--adapter", "claude", "--pairs", str(_sheet(tmp_path)),
                  "--out", str(tmp_path / "v.jsonl")])
    assert rc == 1
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().out


def test_the_claude_arm_runs_the_production_adapter_through_the_whole_pass(tmp_path, monkeypatch,
                                                                            capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-never-used")
    made = {}

    def fake_ctor(key, model=None, sleep=0.2, timeout=60.0):
        made["key"], made["model"], made["sleep"] = key, model, sleep
        truth = {frozenset(("Marvel reveals new X-Men cast at D23", "X-Men cast revealed at D23")):
                 "same_event"}

        def script(a, b):
            v = truth.get(frozenset((a["headline"], b["headline"])), "different_event")
            return {"verdict": v, "quote_a": a["headline"], "quote_b": b["headline"]}
        return FakeProductionAdapter(script)
    monkeypatch.setattr(event_identity, "ClaudeAdapter", fake_ctor)

    rc = v1.main(["--adapter", "claude", "--model", "claude-sonnet-4-6", "--sleep", "0",
                  "--pairs", str(_sheet(tmp_path)), "--out", str(tmp_path / "v.jsonl"),
                  "--stability-sample", "1", "--replays", "2", "--symmetry-sample", "1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert made == {"key": "test-key-never-used", "model": "claude-sonnet-4-6", "sleep": 0.0}
    assert "PRODUCTION judge adapter on claude-haiku-4-5" in out
    assert "Stage 0.4 gate" in out
    assert "V1b" in out and "DISQUALIFYING" not in out, "the weld exhibit was judged different"
    assert "SCREENING PASS" in out
