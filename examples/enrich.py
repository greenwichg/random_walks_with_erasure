"""Headline enrichment — register (reporting-vs-opinion) + emotional tone, behind the
``ingest.Enricher`` interface, so a real reader's Reporting Ratio and Emotional Balance populate.

The reading-ingestion scorer already fills outlet / lean / topic; what it can't fill from a URL
is *register* and *emotion*, which need the headline text. This module supplies them:

* :class:`BaselineEnricher` — a **deterministic, offline** lexical classifier (no API key, no
  network): ``register`` = P(reporting) in ``[0, 1]`` from opinion/reporting cues, ``emotion`` =
  a distribution over ``fear/outrage/analysis/positive/neutral`` (summing to 1) from a per-bucket
  lexicon smoothed toward neutral. Headline-only emotion is inherently noisy (see the caveat in
  ``classify_emotion.py``); treat it as a rough signal, exactly as the report already does.
* :class:`LLMEnricher` — an **optional** plug-in behind the same interface: it asks a text
  ``caller`` for a strict-JSON judgement and falls back to the baseline on any error, so it never
  fails a read. Selected only when configured (``RWE_ENRICH=llm`` + a provider key).

Enrichment runs *inside* the scorer, so the scorer's existing per-canonical-URL cache stores the
enriched read — an article is enriched **once** and reused across readers. No research module is
touched: this only fills fields the engine (``health_report``) already consumes; the augmented
corpus already carries per-read ``register`` / ``emotion`` into ``compute``.

    from ingest import Scorer
    from enrich import make_enricher
    scorer = Scorer(enricher=make_enricher())      # baseline by default; RWE_ENRICH=off disables
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from typing import Callable, Dict, Optional

from augmented_corpus import ScoredRead

LABELS = ("fear", "outrage", "analysis", "positive", "neutral")

# --- register cues (headline-level, coarse) --------------------------------- #
_OPINION_CUES = (
    "opinion", "op-ed", "op ed", "editorial", "commentary", "column", "perspective",
    "the case for", "in defense of", "here's why", "heres why", "why we", "we need",
    "we must", "should", "must", "ought to", "it's time", "its time", "stop", "let's",
    "i think", "my take", "in praise of", "the problem with", "how dare",
)
_REPORTING_CUES = (
    "says", "said", "reports", "reported", "announces", "announced", "confirms", "confirmed",
    "according to", "unveils", "unveiled", "releases", "released", "reveals", "revealed",
    "data", "study", "studies", "poll", "polls", "survey", "report", "figures", "findings",
    "results", "statement", "testifies", "filed", "charged",
)
# --- emotion lexicons (per bucket; higher-precision stems) ------------------- #
_EMOTION_LEX: Dict[str, tuple] = {
    "fear": ("threat", "threaten", "threatens", "danger", "dangerous", "warn", "warns", "warning",
             "risk", "risks", "fear", "fears", "crisis", "scare", "alarm", "deadly", "panic",
             "terror", "collapse", "catastrophe", "emergency", "dire", "looming"),
    "outrage": ("outrage", "slam", "slams", "blast", "blasts", "fury", "furious", "condemn",
                "condemns", "backlash", "scandal", "rips", "accuse", "accuses", "blame", "blames",
                "controversy", "uproar", "denounce", "denounces", "fumes", "lashes"),
    "analysis": ("analysis", "explained", "explainer", "what to know", "here's what", "heres what",
                 "breakdown", "deep dive", "fact check", "fact-check", "factcheck", "q&a",
                 "study finds", "data shows", "by the numbers", "explained:"),
    "positive": ("wins", "win", "hope", "breakthrough", "celebrate", "celebrates", "success",
                 "praise", "record", "boost", "boosts", "gains", "surge", "triumph", "rescue",
                 "rescued", "historic", "milestone", "thrive", "soars", "recovery"),
}
_NEUTRAL_PRIOR = 1.0          # smoothing: a cue-free headline reads as fully neutral


def _prep(text: str) -> str:
    return " " + str(text).lower().strip() + " "


def _has(text: str, kw: str) -> bool:
    return re.search(r"\b" + re.escape(kw) + r"\b", text) is not None


def combine_text(*parts: str) -> str:
    """Join the available text fields — headline + subtitle + description/abstract — into one
    string for enrichment. The SAME combiner is used for the reference corpus (headline + article
    abstract) and for ingested reads (og:title + og:description), so both feed one pipeline; it
    degrades gracefully to whatever is present (headline-only when nothing else exists). More text
    means more lexical signal, which is what spreads register/emotion beyond the headline's flat
    ~0.6 default."""
    return " ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())


class BaselineEnricher:
    """Deterministic, offline register + emotion from the headline. No API key, no network."""

    def enrich(self, scored: ScoredRead, raw) -> ScoredRead:
        text = combine_text(getattr(raw, "title", "") or getattr(scored, "title", ""),
                            getattr(raw, "subtitle", ""), getattr(raw, "description", ""))
        if not text:
            return scored                      # no text -> leave register/emotion n/a (honest)
        return replace(scored,
                       register=self.register(text, getattr(scored, "category", "")),
                       emotion=self.emotion(text))

    @staticmethod
    def register(text: str, category: str = "") -> float:
        """P(reporting) in [0, 1] — 1 = straight reporting, 0 = opinion/editorial."""
        t = _prep(text)
        score = 0.20 if (category or "").lower() == "opinion" else 0.60
        score += 0.06 * sum(1 for kw in _REPORTING_CUES if _has(t, kw))
        score -= 0.09 * sum(1 for kw in _OPINION_CUES if _has(t, kw))
        return max(0.05, min(0.95, round(score, 4)))

    @staticmethod
    def emotion(text: str) -> Dict[str, float]:
        """Distribution over LABELS (sums to 1), smoothed toward neutral."""
        t = _prep(text)
        counts = {b: float(sum(1 for kw in _EMOTION_LEX[b] if _has(t, kw)))
                  for b in ("fear", "outrage", "analysis", "positive")}
        counts["neutral"] = _NEUTRAL_PRIOR
        total = sum(counts.values()) or 1.0
        return {b: round(counts.get(b, 0.0) / total, 4) for b in LABELS}


_LLM_PROMPT = (
    "You judge a single news HEADLINE. Reply with ONLY compact JSON, no prose:\n"
    '{{"reporting": <0..1 float, 1=straight reporting 0=opinion/editorial>, '
    '"emotion": {{"fear": f, "outrage": f, "analysis": f, "positive": f, "neutral": f}}}}\n'
    "The five emotion values are shares that sum to 1.\nHEADLINE: {headline}"
)


class LLMEnricher:
    """Optional LLM enricher behind the same interface. ``caller`` maps a prompt -> text; the
    reply is parsed as strict JSON. Any failure falls back to the baseline, so a read never fails."""

    def __init__(self, caller: Callable[[str], str], fallback: Optional[BaselineEnricher] = None):
        self.caller = caller
        self.fallback = fallback or BaselineEnricher()

    def enrich(self, scored: ScoredRead, raw) -> ScoredRead:
        text = combine_text(getattr(raw, "title", "") or getattr(scored, "title", ""),
                            getattr(raw, "subtitle", ""), getattr(raw, "description", ""))
        if not text:
            return scored
        try:
            data = json.loads(_extract_json(self.caller(_LLM_PROMPT.format(headline=text))))
            register = max(0.0, min(1.0, float(data["reporting"])))
            raw_emo = {l: max(0.0, float(data["emotion"].get(l, 0.0))) for l in LABELS}
            total = sum(raw_emo.values()) or 1.0
            emotion = {l: round(raw_emo[l] / total, 4) for l in LABELS}
            return replace(scored, register=round(register, 4), emotion=emotion)
        except Exception:
            return self.fallback.enrich(scored, raw)    # never fail a read on the LLM path


def _extract_json(text: str) -> str:
    """The first ``{...}`` block in a model reply (tolerates code fences / surrounding prose)."""
    m = re.search(r"\{.*\}", str(text), re.DOTALL)
    return m.group(0) if m else "{}"


def make_enricher(kind: Optional[str] = None):
    """Select an enricher. ``RWE_ENRICH`` (or ``kind``): ``baseline`` (default) | ``off`` | ``llm``.
    ``llm`` uses ``narrate_report``'s text caller when a provider key is set, else the baseline."""
    kind = (kind or os.environ.get("RWE_ENRICH", "baseline")).strip().lower()
    if kind in ("off", "none", "disabled", ""):
        return None
    if kind == "llm":
        caller = _default_llm_caller()
        if caller is not None:
            return LLMEnricher(caller)
    return BaselineEnricher()


def _default_llm_caller() -> Optional[Callable[[str], str]]:
    """Best-effort text caller from ``narrate_report`` (imported, never modified), or ``None`` when
    no provider key is configured — in which case the factory uses the baseline."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY")):
        return None
    try:
        import narrate_report as nr
        provider = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "gemini"
        caller = nr.make_text_caller(provider, os.environ.get("RWE_MODEL"))
        return lambda prompt: caller(prompt)
    except Exception:
        return None
