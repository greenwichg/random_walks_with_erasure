"""M1 — the Coach v2 router + intent registry (examples/coach_service.py).

Pure and offline: no store, no engine, no HTTP. Covers the DoD: all 15 leaves route from >=3
phrasings each, modifiers detect, pronouns/ordinals/affirmatives bind against the STRUCTURED
echo, an unknown echo version is ignored wholesale, compound asks stay bounded, and anything
unmatched lands on CHAT.general flagged for clarification (never an exception, never a guess).
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import coach_service as cs  # noqa: E402


def _echo(**last_turn):
    return {"v": cs.ECHO_VERSION, "turns": [{"role": "coach", **last_turn}]}


# --------------------------------------------------------------------------- #
# Registry sanity.
# --------------------------------------------------------------------------- #
def test_registry_has_the_15_leaves_across_6_families():
    assert len(cs.INTENTS) == 15
    assert {s.family for s in cs.INTENTS.values()} == {
        "EXPLAIN", "ANALYZE", "COMPARE", "ACT", "PROJECT", "CHAT"}
    for name, spec in cs.INTENTS.items():
        assert name == f"{spec.family}.{spec.leaf}"
        for step in spec.plan:                       # plans are DATA: (tool, args_builder)
            tool, args = step
            assert isinstance(tool, str) and callable(args)


def test_flags_default_off(monkeypatch):
    monkeypatch.delenv("RWE_COACH_V2", raising=False)
    monkeypatch.delenv("RWE_COACH_LLM", raising=False)
    assert cs.coach_v2_enabled() is False and cs.coach_llm_enabled() is False
    monkeypatch.setenv("RWE_COACH_V2", "1")
    monkeypatch.setenv("RWE_COACH_LLM", "on")
    assert cs.coach_v2_enabled() is True and cs.coach_llm_enabled() is True


# --------------------------------------------------------------------------- #
# Every leaf routes from multiple phrasings (>=3 each).
# --------------------------------------------------------------------------- #
CASES = [
    ("EXPLAIN.metrics", "what do all these metrics mean?"),
    ("EXPLAIN.metrics", "explain my report scores"),
    ("EXPLAIN.metrics", "what do the numbers on my report mean"),
    ("EXPLAIN.metric", "what is my echo chamber score?"),
    ("EXPLAIN.metric", "why is my source diversity low?"),
    ("EXPLAIN.metric", "tell me about my emotional balance"),
    ("EXPLAIN.recommendations", "how does my feed work?"),
    ("EXPLAIN.recommendations", "explain my recommendations"),
    ("EXPLAIN.recommendations", "how are recommendations picked?"),
    ("EXPLAIN.why_article", "why did you recommend https://x.example/a?"),
    ("ANALYZE.political", "am I politically balanced?"),
    ("ANALYZE.political", "do I read both sides?"),
    ("ANALYZE.political", "analyze my political balance"),
    ("ANALYZE.sources", "how diverse are my outlets?"),
    ("ANALYZE.sources", "which publishers do I rely on?"),
    ("ANALYZE.sources", "analyze my source mix"),
    ("ANALYZE.topics", "what topics do I read?"),
    ("ANALYZE.topics", "break down my subjects"),
    ("ANALYZE.topics", "what do i read most, by topic?"),
    ("ANALYZE.blind_spots", "what am I missing?"),
    ("ANALYZE.blind_spots", "find my blind spots"),
    ("ANALYZE.blind_spots", "what am I not reading?"),
    ("COMPARE.over_time", "am I improving?"),
    ("COMPARE.over_time", "compare my reading to last month"),
    ("COMPARE.over_time", "show my progress over time"),
    ("ACT.suggest", "suggest something to read"),
    ("ACT.suggest", "recommend an article from the other side"),
    ("ACT.suggest", "show me outlets I've never read"),
    ("ACT.weekly_goals", "give me goals for this week"),
    ("ACT.weekly_goals", "set my weekly goal"),
    ("ACT.weekly_goals", "what should my reading goal be?"),
    ("ACT.improvement_plan", "how do I improve my viewpoint balance?"),
    ("ACT.improvement_plan", "how can I fix my echo chamber score?"),
    ("ACT.improvement_plan", "how do I get my score up? make a plan to improve"),
    ("PROJECT.forecast", "what happens if I read more center sources?"),
    ("PROJECT.forecast", "how could my viewpoint balance improve?"),
    ("PROJECT.forecast", "what would reading NPR do to my score? what if i read it daily"),
    ("PROJECT.compare_candidates", "which of these helps more?"),
    ("PROJECT.compare_candidates", "which article is better for me?"),
    ("PROJECT.compare_candidates", "which one helps more with balance?"),
    ("CHAT.general", "hello there"),
    ("CHAT.general", "thanks!"),
    ("CHAT.general", "hi coach"),
]


@pytest.mark.parametrize("expected,message", CASES)
def test_leaf_routing(expected, message):
    assert cs.classify(message).name == expected


# --------------------------------------------------------------------------- #
# Modifiers.
# --------------------------------------------------------------------------- #
def test_why_sets_cause_mode():
    it = cs.classify("why is my source diversity low?")
    assert it.name == "EXPLAIN.metric" and "cause" in it.modifiers
    assert it.entities["metric"] == "sourceDiversity" and it.entities["mode"] == "cause"


def test_what_is_stays_value_mode():
    it = cs.classify("what is my echo chamber score?")
    assert it.entities.get("mode") == "value"


def test_how_plus_improve_routes_to_plan():
    it = cs.classify("how do I improve my echo chamber score?")
    assert it.name == "ACT.improvement_plan" and "plan" in it.modifiers


# --------------------------------------------------------------------------- #
# Structured-echo binding (D6: binding-only, versioned).
# --------------------------------------------------------------------------- #
def test_pronoun_binds_last_metric():
    echo = _echo(intent="EXPLAIN.metric", entities={"metric": "sourceDiversity"})
    it = cs.classify("why is it low?", echo)
    assert it.name == "EXPLAIN.metric"
    assert it.entities["metric"] == "sourceDiversity" and "cause" in it.modifiers


def test_ordinal_binds_last_cards():
    echo = _echo(intent="ACT.suggest", cardIds=["https://a.example/1", "https://b.example/2"])
    it = cs.classify("why the first one?", echo)
    assert it.name == "EXPLAIN.why_article"
    assert it.entities["article"] == "https://a.example/1"
    it2 = cs.classify("and the second card?", echo)
    assert it2.entities.get("article") == "https://b.example/2"


def test_bare_affirmative_accepts_the_last_offer():
    echo = _echo(intent="EXPLAIN.metric", entities={"metric": "sourceDiversity"})
    it = cs.classify("yes please", echo)
    assert it.name == "ACT.suggest"


def test_unknown_echo_version_is_ignored_wholesale():
    stale = {"v": 999, "turns": [{"role": "coach", "entities": {"metric": "echoChamber"},
                                  "cardIds": ["https://a.example/1"]}]}
    it = cs.classify("why is it low?", stale)
    assert it.entities.get("metric") is None            # nothing bound from an unknown version
    it2 = cs.classify("yes", stale)
    assert it2.name == "CHAT.general"                   # no last turn -> no offer to accept


def test_want_extraction_for_suggestions():
    assert cs.classify("suggest outlets I've never read").entities["want"] == "new_publisher"
    assert cs.classify("recommend something from the other side").entities["want"] == "bridge"
    assert cs.classify("suggest more coverage of that story").entities["want"] == "story_match"


# --------------------------------------------------------------------------- #
# Compound asks stay bounded; the unresolved band clarifies, never guesses.
# --------------------------------------------------------------------------- #
def test_compound_ask_records_secondary_leaf():
    it = cs.classify("am I balanced, and what should I read?")
    assert it.name == "ANALYZE.political" and it.secondary == "ACT.suggest"


def test_unresolved_lands_on_chat_with_clarification():
    it = cs.classify("q9 zzz blorp")
    assert it.name == "CHAT.general"
    assert it.resolution == "unresolved" and it.needs_clarification is True


def test_router_never_raises_on_junk():
    for msg in ("", "   ", "🤷", "why?", "how?", None):
        it = cs.classify(msg or "")
        assert it.family in {"EXPLAIN", "ANALYZE", "COMPARE", "ACT", "PROJECT", "CHAT"}


def test_coach_service_is_wired_only_through_the_api_layer():
    """M4 contract: api_fastapi is the ONE sanctioned production consumer (the flag-gated
    route); no other production module may import the coach."""
    import subprocess
    r = subprocess.run(
        ["grep", "-rl", "--exclude-dir=__pycache__", "--exclude-dir=node_modules",
         "coach_service", str(ROOT / "examples"), str(ROOT / "web")],
        capture_output=True, text=True)
    hits = {pathlib.Path(l).name for l in r.stdout.splitlines()}
    assert hits <= {"coach_service.py", "api_fastapi.py"}, f"unexpected consumers: {hits}"
