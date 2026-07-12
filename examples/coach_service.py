"""coach_service.py — Coach v2: the intent-routed, tool-using coaching layer (RWE_COACH_V2).

Scope so far — M1: the ROUTER and the INTENT REGISTRY (pure functions over the message and the
structured echo). M2: the TOOL LAYER — typed ToolResults from thin wrappers over existing PUBLIC
engine surfaces, plus the within-turn plan executor. M3: the COMPOSER — per-leaf grounded
templates, the grounding gate (reusing narrate_report.check-style number extraction), admitted
gaps, and the ``coach_turn`` entry point (the proactive seam: the router is just one producer of
intents). Still imported by no production module; API wiring is M4 per docs/COACH_REDESIGN.md.

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


# --------------------------------------------------------------------------- #
# M2 — the tool layer: typed evidence from EXISTING engine surfaces only.
#
# D0 in practice: every tool below is a THIN WRAPPER over a named public surface —
# Personalizer.report / explanation_context / recommendations / explain / openmindedness,
# Backend.build_analytics, store.report_metric_series / get_reads / get_settings /
# list_rec_events / feed_article_facets, evidence_resolver.resolve / story_index.
# The only operations tools perform on engine outputs are SELECTION (picking fields,
# filtering, min/max/sort) and PRESENTATION aggregation (Counter over stored rows, the
# same class of formatting the auditor does). No score, rank, cluster, projection, or
# explanation is ever computed here. Forecasts expose the engine's existing PER-ARTICLE
# viewpointShift projections verbatim; aggregate multi-read forecasts would require new
# engine computation and are deliberately NOT provided.
# --------------------------------------------------------------------------- #
from collections import Counter
from datetime import datetime, timezone


@dataclasses.dataclass(frozen=True)
class Citation:
    """One number the composer may state, with the engine surface it came from."""
    key: str
    value: object
    source: str


@dataclasses.dataclass(frozen=True)
class ToolResult:
    """The uniform tool envelope (D4): JSON-safe facts, machine-checkable citations, cards
    that are VERBATIM serializer payloads, explicit caveats, and provenance."""
    tool: str
    facts: dict
    citations: tuple = ()
    cards: tuple = ()
    caveats: tuple = ()
    provenance: dict = dataclasses.field(default_factory=dict)


def _prov(store, uid: int) -> dict:
    return {"computedAt": datetime.now(timezone.utc).isoformat(),
            "reads": int(store.count_reads(uid))}


def _canon(url: str) -> str:
    from ingest import canonical_url
    return canonical_url(str(url or ""))


def _report_metrics(report: dict) -> dict:
    return {m["key"]: int(m["score"]) for m in report.get("metrics") or [] if "key" in m}


def _tool_report(pers, store, uid, deps, **_):
    """The Measured report exactly as the report page serves it (Personalizer.report ->
    api_server._serialize_report -> health_report.user_report)."""
    rep = pers.report(uid)
    scores = _report_metrics(rep)
    facts = {"overall": rep.get("overall"), "band": rep.get("band"), "scores": scores,
             "viewpoint": rep.get("viewpoint"), "coverage": rep.get("coverage"),
             "sources": (rep.get("sources") or [])[:5], "topics": (rep.get("topics") or [])[:5],
             "blindSpots": rep.get("blindSpots") or [],
             "improvements": rep.get("improvements") or []}
    cites = [Citation("overall", rep.get("overall"), "api_server._serialize_report")]
    cites += [Citation(k, v, "api_server._serialize_report") for k, v in scores.items()]
    return ToolResult("report", facts, tuple(cites), provenance=_prov(store, uid))


def _tool_shares(pers, store, uid, deps, **_):
    """The C6 parity shares — the same numbers the recommendation cards cite
    (Personalizer.explanation_context)."""
    ctx = pers.explanation_context(uid)
    facts = {"topicShares": ctx.get("topic_shares") or {},
             "leanShares": ctx.get("lean_shares") or {},
             "topTopics": ctx.get("top_topics") or [],
             "readerMeanLean": ctx.get("reader_mean_lean")}
    cites = [Citation(f"topicShare.{t}", round(float(v), 3), "Personalizer.explanation_context")
             for t, v in (facts["topicShares"] or {}).items()]
    cites += [Citation(f"leanShare.{side}", round(float(v), 3),
                       "Personalizer.explanation_context")
              for side, v in (facts["leanShares"] or {}).items()]
    cites.append(Citation("readerMeanLean", facts["readerMeanLean"],
                          "Personalizer.explanation_context"))
    return ToolResult("shares", facts, tuple(cites), provenance=_prov(store, uid))


def _tool_metric(pers, store, uid, deps, name=None, mode="value", **_):
    """One metric selected from the report; ``mode="cause"`` attaches the ENGINE's own driver
    surfaces for that metric (lean shares / top sources / attention / reception). ``name=None``
    selects the reader's lowest-scoring metric (the improvement-plan entry point)."""
    rep_res = deps.get("report") or _tool_report(pers, store, uid, deps)
    scores = rep_res.facts["scores"]
    if not scores:
        raise LookupError("no measured metrics yet")
    key = name if name in scores else ("overall" if name == "overall" else None)
    if key is None:
        key = min(scores, key=scores.get)          # selection, not computation
    value = rep_res.facts["overall"] if key == "overall" else scores[key]
    facts = {"metric": key, "score": value, "mode": mode, "lowestSelected": name not in scores}
    cites = [Citation(key, value, "api_server._serialize_report")]
    caveats = []
    if mode == "cause":
        if key in ("viewpointBalance", "echoChamber"):
            sh = _tool_shares(pers, store, uid, deps)
            facts["drivers"] = {"leanShares": sh.facts["leanShares"],
                                "viewpoint": rep_res.facts["viewpoint"]}
            cites += list(sh.citations)
        elif key == "sourceDiversity":
            facts["drivers"] = {"topSources": rep_res.facts["sources"]}
            cites += [Citation(f"sourceShare.{d.get('name')}", d.get("share"),
                               "api_server._serialize_report")
                      for d in rep_res.facts["sources"] if isinstance(d, dict)]
        elif key == "emotionalBalance":
            facts["drivers"] = {"attention": (pers.report(uid).get("attention") or {})}
        elif key == "openMindedness":
            om = pers.openmindedness(uid)
            facts["drivers"] = {"reception": om}
            cites += [Citation("openedCross", om.get("openedCross"),
                               "Personalizer.openmindedness"),
                      Citation("shownCross", om.get("shownCross"),
                               "Personalizer.openmindedness")]
    return ToolResult("metric", facts, tuple(cites), caveats=tuple(caveats),
                      provenance=_prov(store, uid))


def _tool_recommendations(pers, store, uid, deps, want=None, **_):
    """The LIVE feed (story slot included) with each card's resolver explanation — verbatim
    Personalizer.recommendations + evidence_resolver.resolve. ``want`` filters by resolved
    explanation type (bridge / story_match / new_publisher / ...) or topic name."""
    import evidence_resolver as er
    served = pers.recommendations(uid)
    idx = er.story_index(store)
    ctx = pers.explanation_context(uid)
    resolved = []
    for r in served:
        exp = er.resolve(r, ctx, idx)
        r = dict(r)
        r["explanation"] = exp
        resolved.append(r)
    by_type = Counter((r["explanation"] or {}).get("type") for r in resolved)
    if want:
        w = str(want).lower()
        picked = [r for r in resolved
                  if (r["explanation"] or {}).get("type") == w
                  or str((r.get("article") or {}).get("topic", "")).lower() == w]
    else:
        picked = resolved
    cards = tuple(picked[:3])
    facts = {"served": len(served), "byType": dict(by_type), "want": want,
             "matched": len(picked), "returned": len(cards)}
    cites = [Citation("served", len(served), "Personalizer.recommendations"),
             Citation("matched", len(picked), "evidence_resolver.resolve")]
    caveats = () if picked else (f"no served card currently matches '{want}'",)
    return ToolResult("recommendations", facts, tuple(cites), cards=cards, caveats=caveats,
                      provenance=_prov(store, uid))


def _tool_why_article(pers, store, uid, deps, article=None, **_):
    """The engine's truthful per-article verdict (Personalizer.explain(article=...)):
    served -> 'recommended' with the serving strategy; unserved -> the exclusion taxonomy
    (seen_excluded / below_cutoff with per-strategy ranks / not_in_graph / not_in_catalog)."""
    if not article:
        raise LookupError("no article bound — ask about a specific card or URL")
    ex = (pers.explain(uid, article=str(article)) or {}).get("exclusion") or {}
    if not ex:
        raise LookupError("the engine returned no verdict for this article")
    facts = {"article": ex.get("article"), "resolvedId": ex.get("resolvedId"),
             "verdict": ex.get("verdict"), "detail": ex.get("detail"),
             "byStrategy": ex.get("byStrategy") or {}}
    cites = [Citation("verdict", ex.get("verdict"), "Personalizer.explain")]
    for strat, d in (ex.get("byStrategy") or {}).items():
        if isinstance(d, dict) and d.get("rank") is not None:
            cites.append(Citation(f"rank.{strat}", d["rank"], "rec_explain._exclusion"))
    return ToolResult("why_article", facts, tuple(cites), provenance=_prov(store, uid))


def _tool_history(pers, store, uid, deps, days=None, **_):
    """Presentation aggregation (Counter) over the reader's STORED reads — the same class of
    tallying the auditor does; no scoring."""
    reads = store.get_reads(uid) or []
    outlets = Counter(str(r.get("outlet") or "?") for r in reads)
    topics = Counter(str(r.get("category") or "").strip() or "(uncategorized)" for r in reads)
    facts = {"totalReads": len(reads),
             "topOutlets": outlets.most_common(5), "topTopics": topics.most_common(5),
             "distinctOutlets": len(outlets)}
    cites = [Citation("totalReads", len(reads), "store.get_reads"),
             Citation("distinctOutlets", len(outlets), "store.get_reads")]
    return ToolResult("history", facts, tuple(cites), provenance=_prov(store, uid))


def _tool_trend(pers, store, uid, deps, metric=None, **_):
    """Score trends exactly as the analytics page computes them (Backend.build_analytics over
    stored report snapshots). No deltas are computed here — first/last points are cited and the
    composer phrases 'X -> Y'."""
    snaps = store.report_metric_series(uid)
    analytics = pers.backend.build_analytics(snaps, store.get_reads(uid),
                                             store.list_rec_events(uid))
    series_keys = {"viewpointBalance": "politicalDiversity", "sourceDiversity":
                   "publisherDiversity", "topicDiversity": "topicDiversity",
                   "overall": "healthImprovement"}
    wanted = ([series_keys[metric]] if metric in series_keys
              else ["healthImprovement", "politicalDiversity", "publisherDiversity"])
    facts, cites = {"series": {}, "snapshots": len(snaps)}, []
    for key in wanted:
        pts = analytics.get(key) or []
        if not pts:
            continue
        facts["series"][key] = {"first": pts[0], "last": pts[-1], "points": len(pts)}
        cites += [Citation(f"{key}.first", pts[0].get("overall"), "Backend.build_analytics"),
                  Citation(f"{key}.last", pts[-1].get("overall"), "Backend.build_analytics")]
    caveats = () if facts["series"] else ("no report snapshots recorded yet",)
    cites.append(Citation("snapshots", len(snaps), "store.report_metric_series"))
    return ToolResult("trend", facts, tuple(cites), caveats=caveats,
                      provenance=_prov(store, uid))


def _tool_blind_spots(pers, store, uid, deps, **_):
    """The ENGINE's blind spots (health_report computes them; the report page serves them)
    plus never-read publishers = catalog facets MINUS read outlets (set difference)."""
    rep_res = deps.get("report") or _tool_report(pers, store, uid, deps)
    spots = rep_res.facts["blindSpots"]
    read_outlets = {str(r.get("outlet") or "") for r in store.get_reads(uid) or []}
    facets = store.feed_article_facets(include_provisional=False)
    never_read = [p for p in facets.get("publishers") or [] if p and p not in read_outlets][:5]
    facts = {"blindSpots": spots, "neverReadPublishers": never_read,
             "catalogPublishers": len(facets.get("publishers") or [])}
    cites = [Citation("blindSpots", len(spots), "api_server._serialize_report"),
             Citation("neverReadPublishers", len(never_read), "store.feed_article_facets"),
             Citation("catalogPublishers", facts["catalogPublishers"],
                      "store.feed_article_facets")]
    cites += [Citation(f"gap.{s.get('topic')}", round(float(s.get("gap", 0)), 2),
                       "health_report.user_report") for s in spots if isinstance(s, dict)]
    return ToolResult("blind_spots", facts, tuple(cites), provenance=_prov(store, uid))


def _tool_forecast(pers, store, uid, deps, action=None, k=3, **_):
    """The engine's OWN per-article projections, verbatim: each candidate card's
    ``viewpointShift`` (rec_explain — the report's viewpoint computation with that one article
    appended). Always estimated. Aggregate multi-read forecasts are deliberately not offered —
    the engine has no such primitive and the coach never invents one (D0)."""
    diag = (pers.explain(uid) or {}).get("recommendations") or []
    cands = [{"headline": d.get("headline"), "publisher": d.get("publisher"),
              "url": d.get("url"), "shift": d.get("viewpointShift")}
             for d in diag if d.get("viewpointShift")]
    if not cands:
        raise LookupError("the engine has no projectable candidates for this reader right now")
    cands = cands[: max(1, int(k))]
    current = cands[0]["shift"].get("current") or {}
    facts = {"action": action, "current": current,
             "candidates": [{"headline": c["headline"], "publisher": c["publisher"],
                             "after": c["shift"].get("after")} for c in cands],
             "estimated": True}
    cites = [Citation(f"current.{side}", current.get(side), "rec_explain._viewpoint_shift")
             for side in ("left", "center", "right") if side in current]
    for i, c in enumerate(cands):
        after = c["shift"].get("after") or {}
        for side in ("left", "right"):
            if side in after:
                cites.append(Citation(f"candidate{i}.after.{side}", after[side],
                                      "rec_explain._viewpoint_shift"))
    return ToolResult("forecast", facts, tuple(cites),
                      caveats=("estimated — the report's own computation with one article "
                               "appended; not a promise",),
                      provenance=_prov(store, uid))


def _tool_goals(pers, store, uid, deps, **_):
    """Stored settings, read-only (goal persistence stays in the existing settings flow)."""
    settings = store.get_settings(uid) or {}
    facts = {"readingGoalMinutes": settings.get("readingGoalMinutes", 20),
             "coachGoals": settings.get("coachGoals"),
             "hasStoredSettings": bool(settings)}
    return ToolResult("goals", facts,
                      (Citation("readingGoalMinutes", facts["readingGoalMinutes"],
                                "store.get_settings"),),
                      provenance=_prov(store, uid))


def _tool_story_context(pers, store, uid, deps, article=None, **_):
    """The Story Service cluster behind an article (evidence_resolver.story_index)."""
    import evidence_resolver as er
    if not article:
        raise LookupError("no article bound")
    story = (er.story_index(store) or {}).get(_canon(article))
    if not story:
        return ToolResult("story_context", {"story": None},
                          (Citation("clusterMembers", 0, "evidence_resolver.story_index"),),
                          caveats=("this article is not part of a multi-publisher story",),
                          provenance=_prov(store, uid))
    pubs = sorted({m.get("publisher") for m in story.get("coverage") or [] if m.get("publisher")})
    facts = {"story": {"storyId": story.get("storyId"), "publishers": pubs,
                       "members": len(story.get("coverage") or [])}}
    return ToolResult("story_context", facts,
                      (Citation("clusterMembers", facts["story"]["members"],
                                "evidence_resolver.story_index"),
                       Citation("clusterPublishers", len(pubs),
                                "evidence_resolver.story_index")),
                      provenance=_prov(store, uid))


TOOLS = {"report": _tool_report, "shares": _tool_shares, "metric": _tool_metric,
         "recommendations": _tool_recommendations, "why_article": _tool_why_article,
         "history": _tool_history, "trend": _tool_trend, "blind_spots": _tool_blind_spots,
         "forecast": _tool_forecast, "goals": _tool_goals, "story_context": _tool_story_context}

MAX_PLAN_STEPS = 4


def run_plan(intent: Intent, pers, store, uid: int):
    """Execute the intent's MicroPlan (plus a bounded secondary) with a within-turn memo.
    Returns ``(results, gaps)``: a failed tool becomes an ADMITTED gap — never a fabricated
    result (D0). Read-only end to end."""
    steps = list(INTENTS[intent.name].plan)
    if intent.secondary and intent.secondary in INTENTS:
        extra = list(INTENTS[intent.secondary].plan)
        if len(steps) + len(extra) <= MAX_PLAN_STEPS:
            steps += extra
    done: dict = {}
    results, gaps = [], []
    for tool, args_builder in steps[:MAX_PLAN_STEPS]:
        args = args_builder(intent.entities)
        memo_key = (tool, tuple(sorted((k, str(v)) for k, v in args.items())))
        if memo_key in done:
            continue
        try:
            res = TOOLS[tool](pers, store, uid, {r.tool: r for r in results}, **args)
            done[memo_key] = res
            results.append(res)
        except Exception as e:                        # admitted gap, never invention
            gaps.append({"tool": tool, "reason": f"{type(e).__name__}: {e}"})
    return results, gaps


# --------------------------------------------------------------------------- #
# M3 — composer: per-leaf grounded templates + the grounding gate + coach_turn.
#
# The composer only PHRASES evidence. The render NAMESPACE is presentation of tool facts
# (joining lists, percent-formatting shares); the grounding gate then requires every number in
# the reply to appear in that namespace / the citations / the raw facts — so a template (or,
# in M7, an LLM) that states an un-evidenced number is replaced by the safe fact-list fallback.
# Merge rule: the namespace takes the FIRST result per tool name (plans are registry-controlled,
# so a same-tool collision is avoidable by construction and ignored thereafter).
# --------------------------------------------------------------------------- #
import json as _json
import string as _string


def _pct(x) -> str:
    try:
        return f"{round(float(x) * 100)}%"
    except (TypeError, ValueError):
        return "n/a"


def _label(key: str) -> str:
    return str(key).replace("_", " ")


def _present(res: ToolResult) -> dict:
    """Flatten one ToolResult into template-ready strings/numbers (presentation ONLY)."""
    f = res.facts
    ns: dict = {}
    if res.tool == "report":
        ns["report_overall"] = f.get("overall")
        ns["report_band"] = f.get("band")
        ns["report_scores_line"] = ", ".join(f"{k} {v}" for k, v in (f.get("scores") or {}).items())
    elif res.tool == "metric":
        ns["metric_label"] = f.get("metric")
        ns["metric_score"] = f.get("score")
        d = f.get("drivers") or {}
        line = ""
        if "leanShares" in d and d["leanShares"]:
            sh = d["leanShares"]
            line = (" Driven by your political mix: "
                    + " / ".join(f"{side} {_pct(sh[side])}" for side in ("left", "center", "right")
                                 if side in sh) + ".")
        elif "topSources" in d and d["topSources"]:
            tops = ", ".join(f"{x.get('name')} {_pct(x.get('share'))}" for x in d["topSources"][:3]
                             if isinstance(x, dict))
            line = f" Your most-read outlets: {tops}."
        elif "reception" in d:
            r = d["reception"]
            line = (f" Cross-perspective cards shown: {r.get('shownCross')}, opened: "
                    f"{r.get('openedCross')} (activates at {r.get('minOpened')} opened / "
                    f"{r.get('minShown')} shown).")
        ns["metric_drivers_line"] = line
    elif res.tool == "shares":
        lean = f.get("leanShares") or {}
        ns["shares_lean_line"] = (" / ".join(f"{s} {_pct(lean[s])}" for s in ("left", "center", "right")
                                             if s in lean) or "not yet measurable")
        tops = list((f.get("topicShares") or {}).items())[:3]
        ns["shares_topics_line"] = (", ".join(f"{t} {_pct(v)}" for t, v in tops)
                                    or "no topic shares yet")
    elif res.tool == "recommendations":
        ns["recs_served"] = f.get("served")
        ns["recs_matched"] = f.get("matched")
        ns["recs_bytype_line"] = ", ".join(f"{_label(k)} {v}"
                                           for k, v in (f.get("byType") or {}).items() if k)
        offers = []
        for i, c in enumerate(res.cards, 1):
            a = c.get("article") or {}
            offers.append(f"{i}. {a.get('publisher')} — “{a.get('headline')}” "
                          f"({_label((c.get('explanation') or {}).get('type'))})")
        ns["recs_offer_line"] = ("; ".join(offers) if offers
                                 else "no matching card is in your live feed right now")
    elif res.tool == "why_article":
        ranks = "; ".join(f"{s} #{d.get('rank')}" for s, d in (f.get("byStrategy") or {}).items()
                          if isinstance(d, dict) and d.get("rank") is not None)
        ns["why_line"] = (f"The engine's verdict for this article: {f.get('verdict')} — "
                          f"{f.get('detail')}." + (f" Ranks: {ranks}." if ranks else ""))
    elif res.tool == "story_context":
        st_ = f.get("story")
        ns["story_line"] = ((f" It belongs to story {st_['storyId']} with {st_['members']} "
                             f"articles across {len(st_['publishers'])} publishers.")
                            if st_ else " It is not part of a multi-publisher story.")
    elif res.tool == "history":
        ns["history_total"] = f.get("totalReads")
        ns["history_distinct"] = f.get("distinctOutlets")
        ns["history_outlets_line"] = ", ".join(f"{o} {n}" for o, n in (f.get("topOutlets") or []))
        ns["history_topics_line"] = ", ".join(f"{t} {n}" for t, n in (f.get("topTopics") or []))
    elif res.tool == "trend":
        lines = [f"{_label(k)}: {v['first'].get('overall')} → {v['last'].get('overall')} "
                 f"({v['points']} snapshots)" for k, v in (f.get("series") or {}).items()]
        ns["trend_lines"] = "; ".join(lines) if lines else \
            "no report snapshots recorded yet — trends appear once a few reports are saved"
    elif res.tool == "blind_spots":
        notes = [s.get("note") for s in (f.get("blindSpots") or []) if isinstance(s, dict)]
        ns["bs_spots_line"] = (" ".join(notes[:3]) if notes
                               else "no measured topic gaps right now.")
        never = f.get("neverReadPublishers") or []
        ns["bs_never_line"] = ", ".join(never) if never else "none"
    elif res.tool == "forecast":
        cur = f.get("current") or {}
        ns["fc_current_line"] = " / ".join(f"{s} {cur[s]}" for s in ("left", "center", "right")
                                           if s in cur)
        cands = []
        for c in (f.get("candidates") or [])[:2]:
            after = c.get("after") or {}
            aft = " / ".join(f"{s} {after[s]}" for s in ("left", "center", "right") if s in after)
            cands.append(f"after {c.get('publisher')} — “{c.get('headline')}”: {aft}")
        ns["fc_candidates_line"] = "; ".join(cands)
    elif res.tool == "goals":
        ns["goals_minutes"] = f.get("readingGoalMinutes")
        stored = f.get("coachGoals")
        ns["goals_line"] = (_json.dumps(stored) if stored
                            else "no stored coach goals yet — the gaps above are the candidates.")
    return ns


TEMPLATES = {
    "EXPLAIN.metrics": ("Your Information Health is {report_overall}/100 ({report_band}). "
                        "The measured pieces: {report_scores_line}. Ask about any one of them — "
                        "or ask how to improve."),
    "EXPLAIN.metric": "Your {metric_label} is {metric_score}/100.{metric_drivers_line}",
    "EXPLAIN.recommendations": ("Your feed currently serves {recs_served} cards: "
                                "{recs_bytype_line}. Every card's explanation is provable — ask "
                                "why any specific one was picked."),
    "EXPLAIN.why_article": "{why_line}{story_line}",
    "ANALYZE.political": ("Your political reading splits {shares_lean_line} — Viewpoint Balance "
                          "{metric_score}/100."),
    "ANALYZE.sources": ("You've read {history_distinct} distinct outlets ({history_outlets_line})."
                        " Source Diversity {metric_score}/100.{metric_drivers_line}"),
    "ANALYZE.topics": ("Your reading by topic share: {shares_topics_line}. By stored reads: "
                       "{history_topics_line}."),
    "ANALYZE.blind_spots": ("Measured gaps: {bs_spots_line} Publishers in the catalog you've "
                            "never read: {bs_never_line}."),
    "COMPARE.over_time": "{trend_lines}.",
    "ACT.suggest": "From your live feed: {recs_offer_line}.",
    "ACT.weekly_goals": ("Grounded in your data — gaps: {bs_spots_line} {trend_lines}. Stored "
                         "goals: {goals_line} Your reading goal is {goals_minutes} minutes/day."),
    "ACT.improvement_plan": ("Your lowest metric is {metric_label} ({metric_score}/100)."
                             "{metric_drivers_line} Concrete, measured next steps from your live "
                             "feed: {recs_offer_line}. Stored goals: {goals_line}"),
    "PROJECT.forecast": ("Estimated — the report's own computation with one article appended, "
                         "not a promise. Now: {fc_current_line}. {fc_candidates_line}."),
    "PROJECT.compare_candidates": ("Estimated per candidate (the report's own computation): now "
                                   "{fc_current_line}; {fc_candidates_line}."),
    "CHAT.general": ("I can explain your metrics, analyze your political balance, sources or "
                     "topics, find blind spots, track your progress, or suggest reads — every "
                     "number measured, never invented. What would you like?"),
}
INTENTS = {k: dataclasses.replace(v, template=TEMPLATES.get(k, "")) for k, v in INTENTS.items()}

_CLARIFY = ("I want to answer precisely, but I'm not sure what you're asking. I can explain "
            "your metrics, analyze balance, find blind spots, track progress, or suggest reads "
            "— which would you like?")


def _numbers(text: str) -> set:
    """Number extraction for the grounding gate (same discipline as narrate_report's checker)."""
    return set(re.findall(r"\d+(?:\.\d+)?", str(text)))


def _fallback_content(results, gaps) -> str:
    """The always-safe reply: a fact list built ONLY from citations (grounded by construction),
    plus admitted gaps."""
    bits = [f"{c.key} = {c.value}" for r in results for c in r.citations][:8]
    line = ("Here's what I can measure right now: " + "; ".join(bits) + "."
            if bits else "I can't compute that right now.")
    if gaps:
        line += " (Unavailable: " + ", ".join(g["tool"] for g in gaps) + ".)"
    return line


def compose(intent: Intent, results: list, gaps: list,
            already_covered: bool = False) -> "tuple[str, str | None]":
    """Deterministic phrasing of the evidence — the no-LLM path that must always work.
    Renders the leaf template over the presentation namespace; a missing key (failed/absent
    tool) or a grounding violation falls back to the citation fact-list; gaps are ADMITTED in
    one clause; nothing is ever inferred (D0). Returns ``(content, fallback_reason)`` where
    fallback_reason is None | "missing_evidence" | "gate" — the observability signal M4 logs."""
    if intent.needs_clarification:
        return _CLARIFY, None
    spec = INTENTS[intent.name]
    ns: dict = {}
    for r in results:
        for k, v in _present(r).items():
            ns.setdefault(k, v)                       # first result per key wins (merge rule)
    fallback = None
    try:
        content = spec.template.format(**ns) if spec.template else _fallback_content(results, gaps)
    except (KeyError, IndexError):
        content, fallback = _fallback_content(results, gaps), "missing_evidence"
    # grounding gate: every number in the reply must exist in the evidence
    evidence = " ".join([_json.dumps([dataclasses.asdict(c) for r in results
                                      for c in r.citations]),
                         _json.dumps([r.facts for r in results]),
                         " ".join(str(v) for v in ns.values())])
    if not _numbers(content) <= _numbers(evidence):
        content, fallback = _fallback_content(results, gaps), "gate"
    if gaps and "Unavailable" not in content:
        content += " (I couldn't compute: " + ", ".join(g["tool"] for g in gaps) + " right now.)"
    if already_covered:
        content = "As covered a moment ago — " + content
    return content, fallback


def _template_fields(template: str) -> set:
    return {f for _, f, _, _ in _string.Formatter().parse(template or "") if f}


def coach_turn(pers, store, uid: int, message: str = None, intent: Intent = None,
               echo: "dict | None" = None) -> dict:
    """ONE coach turn — the internal entry point (and the proactive seam: callers may pass a
    ready-made ``intent`` instead of a ``message``; the router is just one producer). Read-only;
    returns the structured reply the API serializes at M4."""
    import time as _time
    t0 = _time.perf_counter()
    if intent is None:
        intent = classify(message or "", echo)
    valid = _valid_echo(echo)
    last = _last_coach_turn(valid)
    already = (last.get("intent") == intent.name
               and (last.get("entities") or {}) == {k: v for k, v in intent.entities.items()
                                                    if k in ("metric", "article", "want")})
    results, gaps = run_plan(intent, pers, store, uid)
    content, fallback = compose(intent, results, gaps, already_covered=already)
    citations = [dataclasses.asdict(c) for r in results for c in r.citations]
    cards = [c for r in results for c in r.cards]
    follow_ups = list(INTENTS[intent.name].follow_ups)
    turn_artifact = {"role": "coach", "intent": intent.name,
                     "entities": {k: v for k, v in intent.entities.items()
                                  if k in ("metric", "article", "want")},
                     "cardIds": [str((c.get("article") or {}).get("url") or "")
                                 for c in cards if isinstance(c, dict)]}
    out_echo = {"v": ECHO_VERSION,
                "turns": (valid.get("turns") or [])[-4:] + [turn_artifact],
                "goals": valid.get("goals")}
    return {"content": content, "intent": intent.name, "resolution": intent.resolution,
            "citations": citations, "cards": cards, "followUps": follow_ups,
            "echo": out_echo, "gaps": gaps,
            # observability (M4): read-only turn telemetry, logged by the API layer
            "toolsRun": [r.tool for r in results], "fallback": fallback,
            "ms": round((_time.perf_counter() - t0) * 1000.0, 1)}
