"""Tests for examples/article_insights.py + examples/insights_provider.py.

Proves the provider-agnostic contract of docs/ARTICLE_INSIGHTS.md: the worker/policy layer
depends only on the AIInsightsProvider interface (a fake provider drives every path — no
vendor SDK, no network); the grounding prompt and output validation apply identically whatever
provider produced the text; provider and model are pure env configuration; and a provider that
cannot run means dormant, never broken.
"""

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import article_insights as ai    # noqa: E402
import insights_provider as ip   # noqa: E402


class FakeProvider(ip.AIInsightsProvider):
    """Interface-conformant test double: records calls, returns a canned payload (or raises)."""

    name = "fake"
    default_model = "fake-model-1"

    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.calls = payload, error, []

    def complete(self, *, system, user, model, max_tokens):
        self.calls.append({"system": system, "user": user, "model": model,
                           "max_tokens": max_tokens})
        if self.error is not None:
            raise self.error
        return self.payload


def good_payload(summary="First sentence. Second sentence."):
    return json.dumps({"summary": summary,
                       "bias": {"framing": "Foregrounds the mayor's response.",
                                "tone": "Urgent, e.g. 'chaos erupted'.",
                                "loadedLanguage": ["chaos erupted"],
                                "omissions": "No cost figures are given.",
                                "viewpoint": "Centres city officials; residents are quoted once."}})


ARTICLE = {"headline": "City council passes budget", "description": "The council voted 7-2 " * 20}


# ------------------------------------------------------------------ #
# generate(): the policy layer over the provider port
# ------------------------------------------------------------------ #

def test_generate_round_trip_through_the_interface(monkeypatch):
    monkeypatch.delenv("RWE_INSIGHTS_MODEL", raising=False)
    p = FakeProvider(good_payload())
    out = ai.generate(ARTICLE, p)
    assert out["summary"] == "First sentence. Second sentence."
    assert set(out["bias"]) == set(ai.BIAS_KEYS)
    call = p.calls[0]
    # grounding: the prompt carries ONLY the article's own text, and says so
    assert "City council passes budget" in call["user"]
    assert "ONLY the text provided" in call["system"]
    # the model resolves to the PROVIDER's default when the env override is unset
    assert call["model"] == "fake-model-1"


def test_model_env_override_beats_the_provider_default(monkeypatch):
    monkeypatch.setenv("RWE_INSIGHTS_MODEL", "some-other-model")
    p = FakeProvider(good_payload())
    ai.generate(ARTICLE, p)
    assert p.calls[0]["model"] == "some-other-model"


def test_generate_accepts_a_fenced_json_answer():
    p = FakeProvider("```json\n" + good_payload() + "\n```")
    assert ai.generate(ARTICLE, p)["summary"].startswith("First")


@pytest.mark.parametrize("raw, why", [
    ("not json at all", "not JSON"),
    (good_payload("One."), "2-4 sentences"),
    (good_payload("A. B. C. D. E."), "2-4 sentences"),
    (json.dumps({"summary": "One. Two.", "bias": {"framing": "x"}}), "incomplete"),
])
def test_validator_rejects_contract_violations(raw, why):
    with pytest.raises(ValueError, match=why):
        ai.parse_and_validate(raw)


def test_validator_rejects_left_right_labels():
    d = json.loads(good_payload())
    d["bias"]["viewpoint"] = "The article leans left throughout."
    with pytest.raises(ValueError, match="label"):
        ai.parse_and_validate(json.dumps(d))
    d["bias"]["viewpoint"] = "A right-wing framing dominates."
    with pytest.raises(ValueError, match="label"):
        ai.parse_and_validate(json.dumps(d))


# ------------------------------------------------------------------ #
# provider selection: pure env configuration
# ------------------------------------------------------------------ #

def test_default_provider_is_anthropic_and_keyless_means_dormant(monkeypatch):
    monkeypatch.delenv("RWE_INSIGHTS_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ip.provider_name() == "anthropic"
    assert ip.from_env() is None                      # no key -> dormant, never an error


def test_planned_and_unknown_providers_are_dormant_with_a_reason(monkeypatch):
    logged = []
    monkeypatch.setenv("RWE_INSIGHTS_PROVIDER", "gemini")
    assert ip.from_env(log=lambda lvl, ev, **f: logged.append(f)) is None
    assert logged[-1]["reason"] == "not implemented yet"
    monkeypatch.setenv("RWE_INSIGHTS_PROVIDER", "no-such-vendor")
    assert ip.from_env(log=lambda lvl, ev, **f: logged.append(f)) is None
    assert logged[-1]["reason"] == "unknown provider"


def test_anthropic_provider_builds_only_with_key_and_sdk(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    built = ip.AnthropicProvider.build()
    try:
        import anthropic  # noqa: F401
        assert isinstance(built, ip.AnthropicProvider)
        assert built.default_model == "claude-opus-4-8"
    except ImportError:
        assert built is None                          # missing SDK -> dormant, not broken


# ------------------------------------------------------------------ #
# run_cycle(): the worker sees only the interface + the store contract
# ------------------------------------------------------------------ #

class FakeStore:
    """Pins the store-accessor contract the real implementation must satisfy."""

    def __init__(self, rows):
        self.rows, self.finished = rows, []

    def enqueue_insights(self, *, min_chars):
        return 0

    def claim_insights_batch(self, n, *, now):
        return self.rows[:n]

    def finish_insights(self, article_id, *, ok, **kw):
        self.finished.append((article_id, ok, kw))


def _row(i, article=None):
    return {"article_id": f"https://x.test/{i}", "article": article or ARTICLE,
            "content_hash": f"h{i}"}


def test_run_cycle_respects_the_batch_cap_and_stamps_provider_model():
    st = FakeStore([_row(i) for i in range(10)])
    stats = ai.run_cycle(st, provider=FakeProvider(good_payload()), limit=3)
    assert stats == {"enqueued": 0, "generated": 3, "failed": 0}
    aid, ok, kw = st.finished[0]
    assert ok and kw["model"] == "fake:fake-model-1"
    assert kw["payload"]["summary"].startswith("First")


def test_run_cycle_isolates_a_failing_article_and_books_a_failed_attempt():
    boom = FakeProvider(error=RuntimeError("api down"))
    st = FakeStore([_row(1)])
    stats = ai.run_cycle(st, provider=boom, limit=1)
    assert stats["failed"] == 1 and stats["generated"] == 0
    aid, ok, kw = st.finished[0]
    assert not ok and "api down" in kw["error"]
    assert kw["max_attempts"] == ai.MAX_ATTEMPTS


def test_run_cycle_without_a_runnable_provider_is_a_no_op(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RWE_INSIGHTS_PROVIDER", raising=False)
    st = FakeStore([_row(1)])
    stats = ai.run_cycle(st)
    assert stats["skipped"] == "no provider" and st.finished == []


def test_request_generation_is_gated_by_the_env_flag(monkeypatch):
    monkeypatch.delenv("RWE_INSIGHTS_ENABLED", raising=False)
    # store=None proves the gate fires before any store access
    assert ai.request_generation(None) is False
