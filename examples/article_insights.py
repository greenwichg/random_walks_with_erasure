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

import hashlib
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

#: Output budget. Raised from 700 when the facets object was added: a truncated response is
#: invalid JSON, which books a failed attempt, and THREE truncations mark an article terminally
#: ``failed`` — so an undersized budget would have silently destroyed coverage on exactly the
#: richest articles (design §3.5). Confirm against the measured p99 before enablement.
MAX_TOKENS = 1000

#: Output contract keys (the bias object). Kept in one place so the validator and tests agree.
BIAS_KEYS = ("framing", "tone", "loadedLanguage", "omissions", "viewpoint")

# --------------------------------------------------------------------------------------------- #
# The FACETS contract (docs/COVERAGE_COMPARISON_REVISED_DESIGN.md §3).
#
# These are the comparison surface: the model reads ONE article and fills closed vocabularies;
# every cross-article operation is set arithmetic over the stored result, performed by code that
# has never seen a model. Comparability is therefore a property of the schema, not of a matching
# heuristic — which is why the vocabularies below are closed, and why the one open field
# (``quantities.subject``) is only ever used where a matching error lengthens a list rather than
# producing a claim.
# --------------------------------------------------------------------------------------------- #

#: Bump when the vocabularies or their meanings change. Records extracted under different
#: vocabularies are NOT comparable (a value the model could not have chosen is not evidence it
#: rejected the value), so a bump is a full re-extraction — see design §14.2.
VOCAB_VERSION = 1

#: Bump when the prompt changes in a way that changes the output distribution. Feeds
#: ``recipe_hash`` with the provider and model, which is what partitions the comparable set.
PROMPT_VERSION = 2

#: Article kind. A review and a box-office report are not alternative treatments of one event —
#: the production evaluation found exactly that pair inside one cluster — so this partitions the
#: comparable set (design §4) rather than merely annotating it.
FORMATS = ("news_report", "analysis", "review", "live_blog", "obituary", "listicle", "opinion",
           "other")

#: The five generic news frames (Semetko & Valkenburg, 2000). Chosen rather than invented, and
#: descriptive of CONSTRUCTION only: no value encodes a political side, and the no-label rule
#: applies to this enum as it does to the bias prose.
FRAMES = ("conflict", "human_interest", "economic_consequences", "morality", "responsibility")

#: Episodic (a single incident) vs thematic (the incident in context) — Iyengar (1991).
DEPTHS = ("episodic", "thematic")

#: Whose account the piece carries. Sourcing posture is signalled in the lede, which is the part
#: of the article this catalog actually has.
VOICE_ROLES = ("official_government", "law_enforcement", "corporate", "expert_academic",
               "worker_union", "affected_person", "witness", "advocacy_ngo",
               "political_opposition", "anonymous_source", "other")

#: Figure classes. News ledes are dense with numbers, so this works on short text where the
#: retired L2 tier (which needed bodies) could not.
QUANTITY_KINDS = ("casualties", "money", "percentage", "people_count", "duration", "date",
                  "distance", "vote_count", "other")

#: Per-facet item caps — the output budget, and a guard against a model padding a list.
FACET_CAPS = {"frames": 2, "voices": 6, "quantities": 6}

#: The no-label rule: bias prose must not reduce the article to a side. Word-bounded, case-
#: insensitive; "left-leaning readers may…" style phrases are exactly what we reject.
_LABEL_RX = re.compile(r"\b(left|right)[- ](wing|leaning)\b|\bfar[- ](left|right)\b"
                       r"|\b(leans?|is|clearly)\s+(left|right)\b", re.I)

_SYSTEM = (
    "You analyze a single news article. Use ONLY the text provided — no outside knowledge, no "
    "assumptions about the outlet or author, nothing the text does not itself support.\n"
    "Return strict JSON: {\"summary\": str, \"bias\": {\"framing\": str, \"tone\": str, "
    "\"loadedLanguage\": [str], \"omissions\": str, \"viewpoint\": str}, \"facets\": {...}}.\n"
    "summary: 2 to 4 sentences, factual, no opinion, grounded in the text alone.\n"
    "bias.framing: how the piece frames its subject (what is foregrounded, what is background).\n"
    "bias.tone: the register and emotional temperature, with a phrase of evidence.\n"
    "bias.loadedLanguage: up to 5 loaded or emotive phrases QUOTED from the text (empty list if "
    "none).\n"
    "bias.omissions: what a reader is not told that the text itself makes conspicuous.\n"
    "bias.viewpoint: whose perspective the piece centres and whose is absent.\n"
    "\n"
    "facets: a structured record of THIS article only. Never compare it to other coverage — you "
    "have not been shown any. Every listed item carries \"evidence\": a span copied VERBATIM from "
    "the article text; an item whose span is not a literal quotation will be discarded.\n"
    f"facets.format: exactly one of {list(FORMATS)}.\n"
    f"facets.frames: up to {FACET_CAPS['frames']} of {list(FRAMES)}, each "
    "{\"key\": ..., \"evidence\": ...}. Empty list if none fits — do not guess.\n"
    f"facets.depth: \"episodic\" if the piece reports a single incident, \"thematic\" if it places "
    "the incident in wider context, null if neither is clear.\n"
    f"facets.voices: up to {FACET_CAPS['voices']} sources the piece quotes or paraphrases, each "
    f"{{\"role\": one of {list(VOICE_ROLES)}, \"name\": str or null, \"evidence\": ...}}.\n"
    "facets.centeredVoice: the role whose perspective the piece centres, or null.\n"
    f"facets.quantities: up to {FACET_CAPS['quantities']} figures stated in the text, each "
    f"{{\"kind\": one of {list(QUANTITY_KINDS)}, \"value\": number, \"unit\": str or null, "
    "\"subject\": a 2-4 word noun phrase naming what is counted, \"evidence\": ...}}. "
    "Write value as a plain number: 1.2 million becomes 1200000.\n"
    "\n"
    "An empty list, null, or \"other\" is always an acceptable answer and is preferred to a "
    "forced choice. Never label the article or outlet as left or right, liberal or conservative "
    "— placement is handled elsewhere; you explain HOW the writing works, not where it sits."
)


class TruncatedOutput(ValueError):
    """The response ended mid-JSON — a budget problem, not a model error.

    Distinguished because the fixes differ and the old code could not tell them apart: a
    truncation means ``max_tokens`` is too small for this article, while malformed JSON means the
    model ignored the contract. Both book a failed attempt, but only one of them is a signal to
    raise the budget, and three of either mark the article terminally ``failed``."""


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


def concurrency() -> int:
    """How many generations run at once inside one cycle (design §9.4).

    Default 1 — strictly today's serial behaviour, so this change is provably inert until an
    operator raises it. Serial generation, not the batch cap, is the real ceiling: a batch large
    enough to keep up with ingestion would run past the poll interval, and the single-flight lock
    would then drop the next cycle's request. The lock stays: it guards against overlapping
    CYCLES, which is a different concern from parallelism within one."""
    try:
        return max(1, min(16, int(os.environ.get("RWE_INSIGHTS_CONCURRENCY", "1"))))
    except (TypeError, ValueError):
        return 1


def scope() -> str:
    """``all`` (every eligible catalog article) or ``clustered`` (only articles in a story).

    Coverage Comparison needs clusters saturated, not the catalog covered; ``clustered`` is how an
    operator spends the budget only where a card can result. Default ``all`` — today's set."""
    raw = os.environ.get("RWE_INSIGHTS_SCOPE", "all").strip().lower()
    return "clustered" if raw == "clustered" else "all"


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


def temperature() -> "float | None":
    """Sampling temperature for generation. Extraction into a closed schema wants 0; an empty
    value means "send nothing", leaving the vendor's own default — the behaviour every caller had
    before the port carried this at all."""
    raw = os.environ.get("RWE_INSIGHTS_TEMPERATURE", "0").strip()
    if not raw:
        return None
    try:
        return max(0.0, min(2.0, float(raw)))
    except (TypeError, ValueError):
        return 0.0


def _norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _span_ok(span: str, haystack: str) -> bool:
    """Design §3.4: an evidence span must appear VERBATIM in the article text.

    Whitespace-normalised, case-insensitive containment — nothing cleverer, deliberately. This is
    the anti-hallucination gate, and its whole value is that it cannot be argued with: an invented
    aspect becomes a false statement about a named publisher the moment it is counted, so an item
    whose span is not literally in the source is discarded rather than trusted."""
    span = _norm_space(span)
    return bool(span) and span in haystack


def _facet_items(raw, *, cap: int, key_field: str, allowed: tuple, text: str,
                 extra: "Callable | None" = None) -> list:
    """Validate one list-shaped facet: enum membership, verbatim span, cap.

    Invalid ITEMS are dropped, never the whole record (design §3.4): one bad row in a list is not
    a reason to throw away a good summary and pay for it again. Every drop is counted, so a model
    that systematically fails a facet is visible in OBS1 rather than silently producing thin
    records."""
    out: list = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            obs_metrics.incr("insights_facet_dropped_total")
            continue
        key = str(item.get(key_field) or "").strip().lower()
        if key not in allowed:
            obs_metrics.incr("insights_facet_dropped_total")
            continue
        span = str(item.get("evidence") or "")
        if not _span_ok(span, text):
            obs_metrics.incr("insights_span_unverified_total")
            continue
        rec = {key_field: key, "evidence": span.strip()}
        if extra is not None:
            more = extra(item)
            if more is None:                  # the item's own rule rejected it
                obs_metrics.incr("insights_facet_dropped_total")
                continue
            rec.update(more)
        out.append(rec)
        if len(out) >= cap:
            break
    return out


def _quantity_extra(item: dict) -> "dict | None":
    """``value`` must be a real number and ``subject`` a short noun phrase; either failing drops
    the figure, because a figure with no subject cannot be matched to anyone else's."""
    try:
        value = float(item.get("value"))
    except (TypeError, ValueError):
        return None
    if value != value or value in (float("inf"), float("-inf")):     # NaN / inf
        return None
    subject = str(item.get("subject") or "").strip()
    if not subject or len(subject) > 80:
        return None
    if _LABEL_RX.search(subject):
        return None
    unit = str(item.get("unit") or "").strip() or None
    return {"value": value, "unit": unit, "subject": subject}


def _voice_extra(item: dict) -> "dict | None":
    name = str(item.get("name") or "").strip() or None
    if name and (len(name) > 120 or _LABEL_RX.search(name)):
        return None
    return {"name": name}


def parse_facets(data: dict, text: str) -> dict:
    """The facets object, validated (design §3.2–§3.4). Always returns the full shape — a model
    that omitted facets entirely yields empty lists and nulls, not a missing key, so every
    consumer reads one shape and the comparable set can never be built on an absent field.

    ``text`` is the article text the model was given; spans are checked against it."""
    raw = data.get("facets")
    raw = raw if isinstance(raw, dict) else {}
    hay = _norm_space(text)

    fmt = str(raw.get("format") or "").strip().lower()
    depth = str(raw.get("depth") or "").strip().lower()
    centered = str(raw.get("centeredVoice") or "").strip().lower()
    return {
        "vocabVersion": VOCAB_VERSION,
        "format": fmt if fmt in FORMATS else None,
        "frames": _facet_items(raw.get("frames"), cap=FACET_CAPS["frames"], key_field="key",
                               allowed=FRAMES, text=hay),
        "depth": depth if depth in DEPTHS else None,
        "voices": _facet_items(raw.get("voices"), cap=FACET_CAPS["voices"], key_field="role",
                               allowed=VOICE_ROLES, text=hay, extra=_voice_extra),
        "centeredVoice": centered if centered in VOICE_ROLES else None,
        "quantities": _facet_items(raw.get("quantities"), cap=FACET_CAPS["quantities"],
                                   key_field="kind", allowed=QUANTITY_KINDS, text=hay,
                                   extra=_quantity_extra),
    }


def parse_and_validate(raw: str, text: str = "") -> dict:
    """Parse the model's JSON and enforce the contract hard. Raises ``ValueError`` on any
    violation — a rejected output is a failed attempt, never a served artifact.

    ``text`` is the article text the model was shown; facet evidence spans are checked against it
    (design §3.4). Called without it, span verification cannot pass, so every facet item is
    dropped — a caller that forgets the text gets an empty facets object rather than an unchecked
    one, which is the safe direction to fail in."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as e:
        # A response that simply stopped is a BUDGET problem, and telling it apart from a model
        # that ignored the contract is the difference between raising max_tokens and rejecting
        # the article forever (design §3.5). Truncation means the answer STARTED as JSON and did
        # not finish — prose that was never JSON is a contract failure, not a budget one, and
        # testing only the tail would misfile every one of those as truncation.
        body = (raw or "").strip()
        if body.startswith("{") and not body.endswith("}"):
            obs_metrics.incr("insights_truncated_total")
            raise TruncatedOutput(f"response ended mid-JSON after {len(body)} chars: {e}")
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
                     "viewpoint": str(bias["viewpoint"]).strip()},
            "facets": parse_facets(data, text),
            "inputChars": len(text)}


def recipe_hash(provider=None) -> str:
    """``hash(prompt_version, provider, model)`` — what partitions the comparable set.

    Design §4: comparing records made different ways measures the MODELS, not the outlets, so
    this travels with every stored artifact and members whose recipe differs are not comparable.
    Short and stable, in the style of ``store._insights_hash``."""
    raw = f"{PROMPT_VERSION}|{VOCAB_VERSION}|{getattr(provider, 'name', '')}|{model_name(provider)}"
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


def generate(article: dict, provider) -> dict:
    """One article → validated insights dict. Pure given (article, provider); raises on failure.
    ``provider`` is any :class:`insights_provider.AIInsightsProvider` — the policy here (prompt,
    contract, validation) is identical whichever vendor produced the text.

    **The model sees ONE article** (design §2, invariant 1). There is no cluster context in this
    call and no comparison in the prompt; everything cross-article happens later, in code, over
    the stored result."""
    text = article_text(article)
    if not text:
        raise ValueError("no article text")
    raw = provider.complete(system=_SYSTEM, user=f"ARTICLE TEXT:\n\n{text}",
                            model=model_name(provider), max_tokens=MAX_TOKENS,
                            temperature=temperature())
    # Models sometimes fence JSON; strip a fence if present before parsing.
    raw = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", (raw or "").strip())
    return parse_and_validate(raw, text)


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
    try:
        import coverage_insights
        need = coverage_insights.min_comparable()
    except Exception:                     # the comparison is an enhancement, never a dependency
        need = 0
    enq = store.enqueue_insights(min_chars=min_chars(), scope=scope(), need=need)
    stats = {"enqueued": enq, "generated": 0, "failed": 0}
    # Stored rows record "<provider>:<model>" so a cached artifact is forever attributable to
    # what generated it, across provider switches. ``recipe`` additionally partitions the
    # comparable set — records made different ways are not comparable (design §4).
    stamp = f"{provider.name}:{model_name(provider)}"
    recipe = recipe_hash(provider)
    rows = store.claim_insights_batch(limit or batch_size(), now=now)

    def _one(row) -> str:
        """Generate and record one article. Returns 'generated' | 'failed'; never raises, so one
        bad article can never sink the batch — the same isolation the serial loop had."""
        t0 = time.perf_counter()
        try:
            payload = generate(row["article"], provider)
            store.finish_insights(row["article_id"], ok=True, payload=payload,
                                  model=stamp, content_hash=row.get("content_hash"),
                                  recipe_hash=recipe)
            obs_metrics.observe("insights_generate_ms", (time.perf_counter() - t0) * 1000.0)
            obs_metrics.incr("insights_generated_total")
            return "generated"
        except Exception as e:
            store.finish_insights(row["article_id"], ok=False, error=f"{type(e).__name__}: {e}",
                                  backoff_base_s=BACKOFF_BASE_S, max_attempts=MAX_ATTEMPTS,
                                  now=now)
            obs_metrics.incr("insights_failed_total")
            _log(logging.WARNING, "insights_generate_failed",
                 articleId=str(row["article_id"])[-12:], error=f"{type(e).__name__}: {e}")
            return "failed"

    workers = min(concurrency(), len(rows)) if rows else 0
    if workers > 1:
        # A bounded pool, exactly the shape push delivery (B2c) already runs on this seam.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="insights") as pool:
            outcomes = list(pool.map(_one, rows))
    else:
        outcomes = [_one(r) for r in rows]
    for outcome in outcomes:
        stats[outcome] += 1
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
