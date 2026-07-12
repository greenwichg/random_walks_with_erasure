"""coach_service.py — Coach v2: the intent-routed, tool-using coaching layer (RWE_COACH_V2).

M1 scope: the ROUTER and the INTENT REGISTRY only — pure functions over the message and the
structured echo, imported by no production module yet. Tools (M2), templates + composer + gate
(M3), and API wiring (M4) land in later milestones per the approved plan in
docs/COACH_REDESIGN.md.

The prime invariant (D0), binding on everything in this module, forever:

    The coach never computes recommendations, scores, forecasts, explanations, or metrics.
    It only orchestrates and explains outputs produced by existing engine components
    (health_report.user_report, Personalizer.recommendations/explain/explanation_context,
    evidence_resolver, narrate_report.report_facts, report snapshots, catalog facets).
    If required evidence is unavailable, it says so — it never infers or fabricates.

Architecture (docs/COACH_REDESIGN.md): 6 intent families x 15 leaves in a data registry; a
deterministic rule cascade routes a message (tri-state resolution: "rule" | "llm" | "unresolved";
the optional LLM classifier is M7 and only fires on the unresolved band); static per-leaf
MicroPlans (plain tuples of tool steps) name which engine outputs answer the question.

Conversation memory is a client-carried STRUCTURED echo — {"v": 1, "turns": [...], "goals": ...}
— and is BINDING-ONLY: it may resolve what "it" / "the first one" / "yes" refer to, but nothing
in an echo is ever citable; every number in a reply is recomputed from engine tools each turn.
An echo with an unknown version is ignored entirely (cold turn).

Config (env):
    RWE_COACH_V2    enable the v2 coach pipeline (wired in M4)          default off
    RWE_COACH_LLM   allow LLM phrasing/classification on top of v2 (M7) default off
"""
from __future__ import annotations

import dataclasses
import os
import re
from typing import Callable, Optional

ECHO_VERSION = 1

#: Engine metric keys (health_report.user_report scores) the router can bind. The lexicon maps
#: user phrasings onto these keys — the ONLY place coach code names metrics.
METRIC_KEYS = ("overall", "echoChamber", "viewpointBalance", "emotionalBalance",
               "sourceDiversity", "openMindedness")

_METRIC_LEXICON = (
    ("echo chamber", "echoChamber"),
    ("echo-chamber", "echoChamber"),
    ("viewpoint balance", "viewpointBalance"),
    ("viewpoint", "viewpointBalance"),
    ("political balance", "viewpointBalance"),
    ("emotional balance", "emotionalBalance"),
    ("emotional", "emotionalBalance"),
    ("source diversity", "sourceDiversity"),
    ("sources score", "sourceDiversity"),
    ("open-minded", "openMindedness"),
    ("open minded", "openMindedness"),
    ("openmindedness", "openMindedness"),
    ("information health", "overall"),
    ("overall score", "overall"),
    ("my score", "overall"),
)

_ORDINALS = (("first", 0), ("1st", 0), ("second", 1), ("2nd", 1), ("third", 2), ("3rd", 2),
             ("last", -1))

_AFFIRMATIVES = ("yes", "yes please", "sure", "ok", "okay", "yep", "yeah", "please do",
                 "show me", "go ahead", "do it")

_URL_RE = re.compile(r"https?://\S+")


def coach_v2_enabled() -> bool:
    """RWE_COACH_V2 (default off) — the master switch, wired into the API at M4."""
    return os.environ.get("RWE_COACH_V2", "").strip().lower() in {"1", "true", "yes", "on"}


def coach_llm_enabled() -> bool:
    """RWE_COACH_LLM (default off) — LLM phrasing/classify on top of v2 (M7). Distinct from key
    presence by design: a configured key does not opt the coach into LLM output."""
    return os.environ.get("RWE_COACH_LLM", "").strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------- #
# Intents: the registry (data, not code paths).
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class Intent:
    """A routed turn: family.leaf + modifiers + bound entities + how it was resolved."""
    family: str
    leaf: str
    modifiers: frozenset = frozenset()
    entities: dict = dataclasses.field(default_factory=dict)
    resolution: str = "rule"              # "rule" | "llm" (M7) | "unresolved"
    secondary: Optional[str] = None       # bounded compound ask: one extra leaf, plan-size capped

    @property
    def name(self) -> str:
        return f"{self.family}.{self.leaf}"

    @property
    def needs_clarification(self) -> bool:
        return self.resolution == "unresolved"


@dataclasses.dataclass(frozen=True)
class IntentSpec:
    """One registry row. ``plan`` is DATA: (tool_name, args_builder) steps executed in order by
    the M3 executor (within-turn memo; later steps may read earlier results by tool name).
    ``template`` is the grounded no-LLM composer text (filled in M3 — the registry self-check
    test pins template fields ⊆ the plan's advertised fact keys)."""
    family: str
    leaf: str
    plan: tuple                            # ((tool_name, args_builder(entities) -> dict), ...)
    budget: int = 120                      # composer word budget
    template: str = ""                     # M3
    follow_ups: tuple = ()


def _args(**fixed) -> Callable[[dict], dict]:
    """Args builder for plan steps: fixed kwargs, with ``"$key"`` values resolved from the
    routed entities at execution time (kept trivial on purpose — plans stay data)."""
    def build(entities: dict) -> dict:
        return {k: (entities.get(v[1:]) if isinstance(v, str) and v.startswith("$") else v)
                for k, v in fixed.items()}
    return build


INTENTS: dict[str, IntentSpec] = {s.family + "." + s.leaf: s for s in (
    # -- EXPLAIN ---------------------------------------------------------- #
    IntentSpec("EXPLAIN", "metrics", plan=(("report", _args()),),
               follow_ups=("Ask about any metric", "How do I improve?")),
    IntentSpec("EXPLAIN", "metric",
               plan=(("report", _args()), ("metric", _args(name="$metric", mode="$mode"))),
               follow_ups=("Suggest reads that would help",)),
    IntentSpec("EXPLAIN", "recommendations", plan=(("recommendations", _args()),),
               follow_ups=("Why was a specific card recommended?",)),
    IntentSpec("EXPLAIN", "why_article",
               plan=(("why_article", _args(article="$article")),
                     ("story_context", _args(article="$article"))),
               follow_ups=("What else covers this story?",)),
    # -- ANALYZE ---------------------------------------------------------- #
    IntentSpec("ANALYZE", "political",
               plan=(("report", _args()), ("shares", _args()),
                     ("metric", _args(name="viewpointBalance", mode="cause"))),
               follow_ups=("Suggest a cross-perspective read",)),
    IntentSpec("ANALYZE", "sources",
               plan=(("report", _args()),
                     ("metric", _args(name="sourceDiversity", mode="cause")),
                     ("history", _args())),
               follow_ups=("Suggest outlets I've never read",)),
    IntentSpec("ANALYZE", "topics", plan=(("shares", _args()), ("history", _args())),
               follow_ups=("Where are my blind spots?",)),
    IntentSpec("ANALYZE", "blind_spots", plan=(("blind_spots", _args()),),
               follow_ups=("Suggest a read from a gap",)),
    # -- COMPARE ---------------------------------------------------------- #
    IntentSpec("COMPARE", "over_time", plan=(("trend", _args(metric="$metric")),),
               follow_ups=("What moved my scores?",)),
    # -- ACT --------------------------------------------------------------- #
    IntentSpec("ACT", "suggest", plan=(("recommendations", _args(want="$want")),),
               budget=90, follow_ups=("Why the first one?",)),
    IntentSpec("ACT", "weekly_goals",
               plan=(("goals", _args()), ("blind_spots", _args()), ("trend", _args(metric=None))),
               follow_ups=("Check my progress next week",)),
    IntentSpec("ACT", "improvement_plan",
               plan=(("report", _args()), ("metric", _args(name="$metric", mode="cause")),
                     ("recommendations", _args(want="$want")), ("goals", _args())),
               budget=160, follow_ups=("Set these as weekly goals",)),
    # -- PROJECT ----------------------------------------------------------- #
    IntentSpec("PROJECT", "forecast",
               plan=(("report", _args()), ("forecast", _args(action="$action"))),
               follow_ups=("Suggest the reads that get me there",)),
    IntentSpec("PROJECT", "compare_candidates",
               plan=(("recommendations", _args()), ("forecast", _args(action="per_card"))),
               follow_ups=("Why does the winner help more?",)),
    # -- CHAT --------------------------------------------------------------- #
    IntentSpec("CHAT", "general", plan=(), budget=60,
               follow_ups=("Explain my metrics", "Suggest something to read")),
)}


# --------------------------------------------------------------------------- #
# Echo (structured memory) — BINDING-ONLY, versioned, never citable.
# --------------------------------------------------------------------------- #
def _valid_echo(echo: "dict | None") -> dict:
    """An echo with an unknown/missing version is ignored wholesale (cold turn)."""
    if isinstance(echo, dict) and echo.get("v") == ECHO_VERSION:
        return echo
    return {}


def _last_coach_turn(echo: dict) -> dict:
    for turn in reversed(echo.get("turns") or []):
        if isinstance(turn, dict) and turn.get("role") == "coach":
            return turn
    return {}


def bind_entities(message: str, echo: "dict | None") -> dict:
    """Resolve references in ``message`` against the STRUCTURED echo: explicit metrics/URLs win;
    pronouns bind the last turn's metric; ordinals bind the last turn's cards. Returns only
    entities that resolved — never guesses. (D6: binding-only; nothing here is citable.)"""
    t = " " + message.strip().lower() + " "
    echo = _valid_echo(echo)
    last = _last_coach_turn(echo)
    out: dict = {}

    m = _URL_RE.search(message)
    if m:
        out["article"] = m.group(0).rstrip(".,;)")
    for phrase, key in _METRIC_LEXICON:
        if phrase in t:
            out["metric"] = key
            break
    if "metric" not in out and re.search(r"\b(it|that score|this score)\b", t):
        prior = (last.get("entities") or {}).get("metric")
        if prior in METRIC_KEYS:
            out["metric"] = prior
    if "article" not in out:
        cards = last.get("cardIds") or []
        for word, idx in _ORDINALS:
            if re.search(rf"\b{word}\b.{{0,12}}\b(one|card|article|that)\b", t) or \
               re.search(rf"\bthe {word}\b", t):
                if cards and -len(cards) <= idx < len(cards):
                    out["article"] = cards[idx]
                    break
        if "article" not in out and re.search(r"\b(this|that) (article|card|story)\b", t) and cards:
            out["article"] = cards[0]
    # what kind of suggestions ("want"): resolver explanation types, from phrasing
    if re.search(r"never read|new (outlet|publisher|source)", t):
        out["want"] = "new_publisher"
    elif re.search(r"other side|opposite|another perspective|cross[- ]?(cutting|perspective)|"
                   r"outside my bubble", t):
        out["want"] = "bridge"
    elif re.search(r"\bstor(y|ies)\b[^.?!]*\b(follow|more|coverage)\b|"
                   r"\b(follow|more|coverage)\b[^.?!]*\bstor(y|ies)\b", t):
        out["want"] = "story_match"
    return out


# --------------------------------------------------------------------------- #
# The router: a deterministic rule cascade (first match wins).
# --------------------------------------------------------------------------- #
def _has(t: str, *words: str) -> bool:
    return any(w in t for w in words)


def _asks_future(t: str) -> bool:
    return bool(re.search(r"what (would|will|happens) |what if |\bif i read\b|"
                          r"\b(could|would)\b[^.?!]*\bimprove\b|"
                          r"\bforecast\b|\bproject(ion)?\b", t))


def _is_affirmative(t: str) -> bool:
    return t.strip(" !.") in _AFFIRMATIVES


def classify(message: str, echo: "dict | None" = None) -> Intent:
    """Route one message. Deterministic; the ONLY message-aware component of the coach.
    Never raises: anything unmatched lands on CHAT.general (unresolved -> the composer asks a
    one-line clarifying question with capability chips). The optional LLM classifier (M7) will
    run only on the unresolved band, behind RWE_COACH_LLM."""
    raw = message or ""
    t = " " + raw.strip().lower() + " "
    e = bind_entities(raw, echo)
    echo_ok = _valid_echo(echo)
    last = _last_coach_turn(echo_ok)

    mods = set()
    if re.search(r"\bwhy\b|\bhow come\b|\bwhat.s (causing|driving)\b", t):
        mods.add("cause")
    if re.search(r"\bhow (do|can|should) i\b|\bhow to\b", t):
        mods.add("plan")
    if re.search(r"\bsuggest\b|\brecommend\b(?!ations?\b)|\bshow me\b|\bgive me\b|"
                 r"\bfind me\b", t):
        mods.add("attach_cards")

    def intent(family, leaf, extra_entities=None, secondary=None):
        ents = dict(e)
        if extra_entities:
            ents.update(extra_entities)
        return Intent(family, leaf, frozenset(mods), ents, "rule", secondary)

    # 0. bare affirmative -> accept the last turn's offer (suggestions are the common offer)
    if _is_affirmative(t) and last:
        return intent("ACT", "suggest")

    # 1. a specific article ("why this/that/first one", URL) -> the per-article chain
    if e.get("article") and ("cause" in mods or _has(t, "recommend", "why")):
        return intent("EXPLAIN", "why_article")

    # 2. explicit metric talk
    if e.get("metric"):
        if _asks_future(t):
            return intent("PROJECT", "forecast", {"action": "improve:" + e["metric"]})
        if "plan" in mods and _has(t, "improve", "better", "fix", " up "):
            return intent("ACT", "improvement_plan", {"mode": "cause"})
        mode = "cause" if "cause" in mods else "value"
        if e["metric"] == "viewpointBalance" and _has(t, "balanced", "balance") \
                and "cause" not in mods:
            return intent("ANALYZE", "political")
        return intent("EXPLAIN", "metric", {"mode": mode})

    # 2.5 future-tense questions with no bound metric are still forecasts
    if _asks_future(t):
        return intent("PROJECT", "forecast", {"action": "generic"})

    # 3. suggestions (goals have their own leaf — "give me goals" is not a card ask)
    if "attach_cards" in mods and "goal" not in t:
        secondary = None
        if _has(t, "balanced", "balance") or e.get("want") == "bridge":
            secondary = "ANALYZE.political" if _has(t, "am i", "analysis") else None
        return intent("ACT", "suggest", secondary=secondary)

    # 4. analysis families
    if _has(t, "blind spot", "missing", "not reading", "haven't read", "havent read"):
        return intent("ANALYZE", "blind_spots")
    if _has(t, "balanced", "political balance", " left ", " right ", "both sides"):
        # compound: "...and what should I read?" stays one bounded turn
        secondary = "ACT.suggest" if _has(t, "what should i read", "and suggest") else None
        return intent("ANALYZE", "political", secondary=secondary)
    if _has(t, "source", "outlet", "publisher") and not _has(t, "score"):
        return intent("ANALYZE", "sources")
    if _has(t, "topic", "subject", "categories", "what do i read"):
        return intent("ANALYZE", "topics")

    # 5. time / goals / plans / forecasts
    if _has(t, "improving", "compare", "last week", "last month", "trend", "changed",
            "progress", "over time"):
        return intent("COMPARE", "over_time")
    if _has(t, "goal") :
        return intent("ACT", "weekly_goals")
    if "plan" in mods and _has(t, "improve", "better", "fix"):
        return intent("ACT", "improvement_plan")
    if _asks_future(t):
        return intent("PROJECT", "forecast", {"action": "generic"})
    if _has(t, "which") and _has(t, "helps more", "better for me"):
        return intent("PROJECT", "compare_candidates")

    # 6. explain the system itself
    if _has(t, "metric", "score", "report") and _has(t, "explain", "what do", "mean"):
        return intent("EXPLAIN", "metrics")
    if _has(t, "recommendation", "my feed") and _has(t, "how", "work", "picked", "explain"):
        return intent("EXPLAIN", "recommendations")

    # 7. small talk vs. genuinely unresolved
    if _has(t, "hello", "hi ", "hey", "thanks", "thank you"):
        return intent("CHAT", "general")
    return Intent("CHAT", "general", frozenset(mods), dict(e), "unresolved")
