"""article_insights.py — AI summaries + bias analysis, generated off-request and cached forever.

Design: docs/ARTICLE_INSIGHTS.md. Two artifacts per article — a 2–4 sentence summary grounded
ONLY in the article's own text, and a bias analysis in prose (framing, tone, loaded language,
omissions, viewpoint) that deliberately never reduces to a left/right label (the scored registry
lean already covers placement).

Shape of the machinery (the push-delivery pattern):

* :func:`generate` is pure and injectable — it takes the article dict and an
  :class:`insights_provider.AIInsightsProvider`, builds the grounded prompt, and validates the
  model's JSON hard (sentence bound, no-label rule). The worker, storage, API, and UI depend
  only on that interface — never on a vendor SDK; tests pass a fake provider, no network.
* :func:`request_generation` hangs off the poller's post-cycle seam: enqueue new eligible
  catalog articles (idempotent — canonical URL is the dedup key), then process a bounded batch
  in a SINGLE-FLIGHT daemon thread. One run at a time; a request during a run is dropped, so a
  slow API cannot stack threads. An exception never reaches the poll loop.
* OFF unless ``RWE_INSIGHTS_ENABLED`` is truthy AND the configured provider can run (its key +
  SDK present; see ``insights_provider.from_env``) — dormant means zero table writes and a
  byte-identical request path.

The request path never generates: serving reads the cache (``store.get_insights``) or serves
``insights: null``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Callable, Optional

import obs_metrics
import insights_provider

#: Per-cycle generation cap — the spend bound. 6 articles/cycle ≈ the ingest rate of new,
#: eligible articles in steady state, so coverage catches up within hours without bursts.
DEFAULT_BATCH = 6

#: Eligibility floor: an article must carry at least this much text (title+description) to be
#: worth a call — the long tail of stub rows never spends a token.
DEFAULT_MIN_CHARS = 200

#: Attempts before a row is terminally ``failed``. Backoff between tries: 2^attempts × 10 min.
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 600.0

#: Input cap (chars) for the article text passed to the model.
INPUT_CAP = 6000

#: Output contract keys (the bias object). Kept in one place so the validator and tests agree.
BIAS_KEYS = ("framing", "tone", "loadedLanguage", "omissions", "viewpoint")

#: The no-label rule: bias prose must not reduce the article to a side. Word-bounded, case-
#: insensitive; "left-leaning readers may…" style phrases are exactly what we reject.
_LABEL_RX = re.compile(r"\b(left|right)[- ](wing|leaning)\b|\bfar[- ](left|right)\b"
                       r"|\b(leans?|is|clearly)\s+(left|right)\b", re.I)

_SYSTEM = (
    "You analyze a single news article. Use ONLY the text provided — no outside knowledge, no "
    "assumptions about the outlet or author, nothing the text does not itself support.\n"
    "Return strict JSON: {\"summary\": str, \"bias\": {\"framing\": str, \"tone\": str, "
    "\"loadedLanguage\": [str], \"omissions\": str, \"viewpoint\": str}}.\n"
    "summary: 2 to 4 sentences, factual, no opinion, grounded in the text alone.\n"
    "bias.framing: how the piece frames its subject (what is foregrounded, what is background).\n"
    "bias.tone: the register and emotional temperature, with a phrase of evidence.\n"
    "bias.loadedLanguage: up to 5 loaded or emotive phrases QUOTED from the text (empty list if "
    "none).\n"
    "bias.omissions: what a reader is not told that the text itself makes conspicuous.\n"
    "bias.viewpoint: whose perspective the piece centres and whose is absent.\n"
    "Never label the article or outlet as left or right, liberal or conservative — placement is "
    "handled elsewhere; you explain HOW the writing works, not where it sits."
)


def enabled() -> bool:
    """Feature gate: explicit env opt-in. Key/package presence is checked at client build."""
    return os.environ.get("RWE_INSIGHTS_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def model_name(provider=None) -> str:
    """The model to use: the env override, else the provider's own default. Provider-agnostic —
    the fallback travels with the provider, so switching vendors never leaves a stale model id."""
    override = os.environ.get("RWE_INSIGHTS_MODEL", "").strip()
    if override:
        return override
    return provider.default_model if provider is not None else ""


def batch_size() -> int:
    try:
        return max(1, int(os.environ.get("RWE_INSIGHTS_BATCH", DEFAULT_BATCH)))
    except (TypeError, ValueError):
        return DEFAULT_BATCH


def min_chars() -> int:
    try:
        return max(0, int(os.environ.get("RWE_INSIGHTS_MIN_CHARS", DEFAULT_MIN_CHARS)))
    except (TypeError, ValueError):
        return DEFAULT_MIN_CHARS


def build_provider(log=None):
    """The configured provider (``RWE_INSIGHTS_PROVIDER``), or ``None`` when it cannot run —
    a thin re-export so callers of this module never import the provider registry directly."""
    return insights_provider.from_env(log=log)


def article_text(article: dict) -> str:
    """The grounding text: title + description (+ body when the store carries one), capped."""
    parts = [article.get("headline") or article.get("title") or "",
             article.get("description") or "",
             article.get("body") or ""]
    return "\n\n".join(p.strip() for p in parts if p and p.strip())[:INPUT_CAP]


def eligible(article: dict) -> bool:
    return len(article_text(article)) >= min_chars()


def _sentences(text: str) -> int:
    return len([s for s in re.split(r"[.!?]+(?:\s|$)", (text or "").strip()) if s.strip()])


def parse_and_validate(raw: str) -> dict:
    """Parse the model's JSON and enforce the contract hard. Raises ``ValueError`` on any
    violation — a rejected output is a failed attempt, never a served artifact."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"not JSON: {e}")
    summary = (data.get("summary") or "").strip()
    bias = data.get("bias") or {}
    if not summary:
        raise ValueError("empty summary")
    n = _sentences(summary)
    if not 2 <= n <= 4:
        raise ValueError(f"summary must be 2-4 sentences, got {n}")
    if not isinstance(bias, dict) or any(k not in bias for k in BIAS_KEYS):
        raise ValueError("bias object incomplete")
    if not isinstance(bias.get("loadedLanguage"), list):
        raise ValueError("loadedLanguage must be a list")
    prose = " ".join(str(bias.get(k) or "") for k in BIAS_KEYS if k != "loadedLanguage")
    if _LABEL_RX.search(prose):
        raise ValueError("bias prose contains a left/right label")
    return {"summary": summary,
            "bias": {"framing": str(bias["framing"]).strip(),
                     "tone": str(bias["tone"]).strip(),
                     "loadedLanguage": [str(x).strip() for x in bias["loadedLanguage"]][:5],
                     "omissions": str(bias["omissions"]).strip(),
                     "viewpoint": str(bias["viewpoint"]).strip()}}


def generate(article: dict, provider) -> dict:
    """One article → validated insights dict. Pure given (article, provider); raises on failure.
    ``provider`` is any :class:`insights_provider.AIInsightsProvider` — the policy here (prompt,
    contract, validation) is identical whichever vendor produced the text."""
    text = article_text(article)
    if not text:
        raise ValueError("no article text")
    raw = provider.complete(system=_SYSTEM, user=f"ARTICLE TEXT:\n\n{text}",
                            model=model_name(provider), max_tokens=700)
    # Models sometimes fence JSON; strip a fence if present before parsing.
    raw = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", (raw or "").strip())
    return parse_and_validate(raw)


# ------------------------------------------------------------------ #
# The worker — single-flight, bounded, seam-isolated.
# ------------------------------------------------------------------ #

_run_lock = threading.Lock()


def run_cycle(store, *, provider=None, limit: Optional[int] = None,
              log: Optional[Callable] = None, now: Optional[float] = None) -> dict:
    """Enqueue new eligible articles, then process one bounded batch. Synchronous — callers
    wanting the seam behaviour use :func:`request_generation`. Returns counters (tested).
    ``provider`` is any :class:`insights_provider.AIInsightsProvider`; ``None`` resolves the
    configured one (``RWE_INSIGHTS_PROVIDER``) — the worker never names a vendor SDK."""
    _log = log or (lambda lvl, ev, **f: None)
    provider = provider or build_provider(log=_log)
    if provider is None:
        return {"enqueued": 0, "generated": 0, "failed": 0, "skipped": "no provider"}
    now = time.time() if now is None else now
    enq = store.enqueue_insights(min_chars=min_chars())
    stats = {"enqueued": enq, "generated": 0, "failed": 0}
    # Stored rows record "<provider>:<model>" so a cached artifact is forever attributable to
    # what generated it, across provider switches.
    stamp = f"{provider.name}:{model_name(provider)}"
    for row in store.claim_insights_batch(limit or batch_size(), now=now):
        t0 = time.perf_counter()
        try:
            payload = generate(row["article"], provider)
            store.finish_insights(row["article_id"], ok=True, payload=payload,
                                  model=stamp, content_hash=row.get("content_hash"))
            stats["generated"] += 1
            obs_metrics.observe("insights_generate_ms", (time.perf_counter() - t0) * 1000.0)
            obs_metrics.incr("insights_generated_total")
        except Exception as e:            # one bad article never sinks the batch
            store.finish_insights(row["article_id"], ok=False, error=f"{type(e).__name__}: {e}",
                                  backoff_base_s=BACKOFF_BASE_S, max_attempts=MAX_ATTEMPTS,
                                  now=now)
            stats["failed"] += 1
            obs_metrics.incr("insights_failed_total")
            _log(logging.WARNING, "insights_generate_failed",
                 articleId=str(row["article_id"])[-12:], error=f"{type(e).__name__}: {e}")
    return stats


def request_generation(store, *, log: Optional[Callable] = None) -> bool:
    """The post-cycle seam entry: OFF unless enabled; single-flight daemon thread (a request
    during a run is dropped, not queued). Never raises into the poller."""
    if not enabled():
        return False
    if not _run_lock.acquire(blocking=False):
        return False

    def _run():
        try:
            run_cycle(store, log=log)
        except Exception:
            logging.getLogger("ih.insights").warning("insights_cycle_failed", exc_info=True)
        finally:
            _run_lock.release()

    threading.Thread(target=_run, name="article-insights", daemon=True).start()
    return True
