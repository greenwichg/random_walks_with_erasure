"""event_identity.py — the banded semantic event-identity judge (production).

The measurement record that shaped this design, in one paragraph. The deterministic lexical
layer decides the overwhelming mass of pairs correctly and cheaply (S4: its filter band
auto-decides 78% of labeled pairs with 0/60 errors), and every clustering failure the exhibits
ever produced lives in the residue where token evidence is weak — the AMBIGUITY BAND (sized by
audit_verifier_band, ~451 pairs/day). Every purely lexical/statistical attempt to resolve that
band is measured and closed: manual shape lexicons work but need per-genre enumeration; IDF
re-weighting cost 10.5% of coverage; the corpus-derived df+day-spread gate cost 17.0% because
no distributional statistic separates "frequent because template" from "frequent because
important" (see ``story_service.derived_boilerplate_on``). What distinguishes "frozen fruit
bars recalled nationwide" from "eye drops recalled nationwide" is the REFERENT, and judging
referents is semantic work — done here, only inside the band, by a model behind an adapter.

**The build never waits on this.** ``story_service.build_stories`` stays a pure, deterministic,
offline function: it receives already-persisted verdicts as an input dict and consults them only
for in-band edges; a pair with no verdict behaves exactly as production always has (fail-open),
and the build emits that pair to a queue. A daemon (:class:`EventJudge`) drains the queue OUT OF
BAND through :class:`ClaudeAdapter` and persists verdicts, so a later build knows. Convergence is
fast because the band is small; between judgments nothing regresses, because absence-of-verdict
is byte-identical to today.

**Veto-only, one direction.** Only a confident ``different_event`` verdict removes an edge —
the evidence-hook contract every adopted gate shares ("it can only remove edges, never add
one"). ``same_event`` and ``uncertain`` change nothing. False merges are the failure class with
production exhibits; edge ADDITION (false-split repair) is deliberately out of scope for this
hook.

**Trust is earned, not assumed.** The kill-switch flag (``RWE_EVENT_JUDGE``) defaults OFF, and
the enablement decision belongs to the pre-registered V1 bars (docs/EVENT_IDENTITY_RUBRIC.md;
``audit_v1_verifier.py`` — one false-same on a labeled-different exhibit is disqualifying) plus
the standard clustering counterfactual, like every adoption before it. Anti-hallucination is
mechanical: a verdict must quote a span from EACH side, and a span that is not a substring of
its side demotes the verdict to ``uncertain`` (never to a veto).

Transport discipline — learned by the V1-prime Gemini run, which recorded 372/390 pairs as
transport errors before it was hardened: bounded attempts, fail-fast on non-429 4xx (a rejected
request cannot succeed by retrying), Retry-After honored on 429/529, exponential backoff capped
otherwise, and every failure fails CLOSED to an ``api-error`` uncertain that the worker retries
after a cooldown. Stdlib only: the serve image carries no SDK, and the coach narrative's
``ANTHROPIC_API_KEY`` convention is reused.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request

#: Bump when the rubric/prompt changes meaning — old verdicts stop matching and re-judge.
RUBRIC_VERSION = "v1"

ENDPOINT = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

#: The judgment prompt — the ratified rubric's operative core (docs/EVENT_IDENTITY_RUBRIC.md),
#: restated for a model that sees exactly two articles and must answer about their REFERENT.
SYSTEM = (
    "You judge whether two news articles report the SAME underlying real-world event.\n"
    "Same event: one occurrence in the world (same happening, same principal participants),\n"
    "including follow-ups and reactions to that occurrence. Different events: distinct\n"
    "occurrences, however similar their shape — two different product recalls, two different\n"
    "match previews, two different films' box-office runs, the same template about different\n"
    "subjects. Judge referents, not vocabulary overlap.\n"
    "Answer STRICT JSON only, no prose, exactly:\n"
    '{"verdict": "same_event" | "different_event" | "uncertain",\n'
    ' "quote_a": "<short span copied verbatim from article A>",\n'
    ' "quote_b": "<short span copied verbatim from article B>"}\n'
    "The two quotes must be copied exactly from the given texts and must be the evidence your\n"
    "verdict rests on. If the texts do not carry enough to decide, answer uncertain."
)

_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", (s or "")).strip().lower()


def pair_key(url_a: str, url_b: str) -> str:
    """Stable, order-independent key for one article pair under the CURRENT rubric version."""
    a, b = sorted((str(url_a), str(url_b)))
    digest = hashlib.sha1(f"{a}\n{b}".encode("utf-8")).hexdigest()
    return f"{RUBRIC_VERSION}:{digest}"


def side_text(art: dict) -> str:
    """What the judge sees for one side — the same fields the clusterer scores, plus the date."""
    bits = [f"headline: {art.get('headline') or ''}"]
    if art.get("description"):
        bits.append(f"summary: {art['description']}")
    bits.append(f"published: {art.get('publishedAt') or 'unknown'}")
    return "\n".join(bits)


def quote_ok(span: str, art: dict) -> bool:
    """Mechanical anti-hallucination check: the quoted span (whitespace-normalized, cased down)
    must be a substring of the side's own text. A verdict resting on words the article does not
    contain is demoted, never trusted."""
    s = _norm(span)
    if not s:
        return False
    return s in _norm(side_text(art))


class ClaudeAdapter:
    """The model adapter — ``name`` + ``verdict(a, b)``, the same two-member contract the V1
    benchmark harness uses, so the SAME adapter is benchmarkable offline and servable here."""

    def __init__(self, key: str, model: "str | None" = None, sleep: float = 0.2,
                 timeout: float = 60.0):
        self.key = key
        self.model = model or judge_model()
        self.name = self.model
        self.sleep = sleep
        self.timeout = timeout
        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0

    def verdict(self, a: dict, b: dict) -> dict:
        body = json.dumps({
            "model": self.model,
            "max_tokens": 300,
            "system": SYSTEM,
            "messages": [{"role": "user", "content":
                          f"ARTICLE A:\n{side_text(a)}\n\nARTICLE B:\n{side_text(b)}\n\n"
                          f"Do A and B report the same underlying real-world event?"}],
        }).encode("utf-8")
        headers = {"content-type": "application/json", "x-api-key": self.key,
                   "anthropic-version": ANTHROPIC_VERSION}
        last = None
        for attempt in range(6):
            try:
                req = urllib.request.Request(ENDPOINT, data=body, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    payload = json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                try:
                    detail = e.read().decode(errors="replace")
                except Exception:                    # noqa: BLE001 — body may be unreadable
                    detail = ""
                last = f"HTTP {e.code}: {(detail or str(e.reason))[:300]}"
                if e.code in (429, 529) or e.code >= 500:
                    retry_after = e.headers.get("retry-after") if e.headers else None
                    try:
                        delay = float(retry_after)
                    except (TypeError, ValueError):
                        delay = min(60.0, 5.0 * (2 ** attempt))
                    time.sleep(delay)
                    continue
                break                                 # 400/401/403/404: fail fast, fail closed
            except Exception as e:                    # noqa: BLE001 — network/timeout shapes
                last = f"{type(e).__name__}: {e}"
                time.sleep(min(30.0, 2.0 * (2 ** attempt)))
                continue
            self.calls += 1
            usage = payload.get("usage") or {}
            self.tokens_in += int(usage.get("input_tokens") or 0)
            self.tokens_out += int(usage.get("output_tokens") or 0)
            try:
                text = "".join(part.get("text", "") for part in payload.get("content", [])
                               if part.get("type") == "text")
                text = text.strip()
                if text.startswith("```"):
                    text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text)
                out = json.loads(text)
                if out.get("verdict") not in ("same_event", "different_event", "uncertain"):
                    raise ValueError("bad verdict enum")
            except Exception as e:                    # noqa: BLE001 — malformed body
                last = f"malformed response: {type(e).__name__}: {e}"
                continue
            # Quote demotion: a decisive verdict must quote real spans from BOTH sides.
            if out["verdict"] != "uncertain" and not (
                    quote_ok(out.get("quote_a", ""), a) and quote_ok(out.get("quote_b", ""), b)):
                out = {"verdict": "uncertain", "quote_a": "", "quote_b": "",
                       "_demoted": "quote-verification"}
            time.sleep(self.sleep)
            return out
        return {"verdict": "uncertain", "quote_a": "", "quote_b": "",
                "_api_error": f"api-error after retries: {last}"}


# --------------------------------------------------------------------------- #
# Configuration — every knob resolves like the clustering knobs: env, junk-safe.
# --------------------------------------------------------------------------- #
def judge_on() -> bool:
    """Master switch (``RWE_EVENT_JUDGE``) — OFF by default. Turning it on makes builds consult
    persisted verdicts and starts the worker (key permitting); it never makes a build wait."""
    return os.environ.get("RWE_EVENT_JUDGE", "").strip().lower() in {"1", "true", "yes", "on"}


def judge_model() -> str:
    """``RWE_EVENT_JUDGE_MODEL`` — default claude-haiku-4-5: the band is ~451 pairs/day, and the
    escalation path (Sonnet) is one env line after the bars say the cheap arm is not enough."""
    return os.environ.get("RWE_EVENT_JUDGE_MODEL", "").strip() or "claude-haiku-4-5"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def judge_budget() -> int:
    """Judgments per worker cycle (``RWE_EVENT_JUDGE_BUDGET``) — the cost governor."""
    return _env_int("RWE_EVENT_JUDGE_BUDGET", 120)


def judge_interval() -> float:
    """Seconds between worker cycles (``RWE_EVENT_JUDGE_INTERVAL``)."""
    return float(_env_int("RWE_EVENT_JUDGE_INTERVAL", 300))


def api_key() -> str:
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


# --------------------------------------------------------------------------- #
# The worker.
# --------------------------------------------------------------------------- #
def judge_pending(store_, adapter, budget: int = 120, log=None) -> dict:
    """Drain up to ``budget`` queued band pairs through the adapter, persisting every outcome.

    Judges exactly the SNAPSHOTS the build saw (queued rows carry headline/summary/date for both
    sides), so a verdict always describes the pair the clusterer asked about, even after the
    catalog rows rotate. Returns counters for the caller's log line."""
    done = {"judged": 0, "same": 0, "different": 0, "uncertain": 0, "api_error": 0}
    for row in store_.pending_event_pairs(limit=budget):
        a = {"headline": row.get("title_a"), "description": row.get("dek_a"),
             "publishedAt": row.get("published_a")}
        b = {"headline": row.get("title_b"), "description": row.get("dek_b"),
             "publishedAt": row.get("published_b")}
        out = adapter.verdict(a, b)
        if out.get("_api_error"):
            store_.record_event_verdict(row["pair_key"], "uncertain", source="api-error",
                                        model=getattr(adapter, "name", ""))
            done["api_error"] += 1
            continue
        v = out["verdict"]
        store_.record_event_verdict(row["pair_key"], v, source="model",
                                    model=getattr(adapter, "name", ""))
        done["judged"] += 1
        done[{"same_event": "same", "different_event": "different",
              "uncertain": "uncertain"}[v]] += 1
    if log:
        log(logging.INFO, "event_judge_cycle", **done)
    return done


class EventJudge:
    """One daemon thread, one cycle per interval: drain the band queue, persist verdicts.

    Starts only when the flag is on AND a key is present; a missing key logs once and the
    feature degrades to exactly today's clustering (fail-open end to end). Never touches the
    build; the build reads whatever this has persisted so far."""

    def __init__(self, store_, log=None):
        self._store = store_
        self._log = log
        self._stop = threading.Event()
        self._thread: "threading.Thread | None" = None

    def start(self) -> bool:
        if not (judge_on() and api_key()):
            if self._log and judge_on():
                self._log(logging.WARNING, "event_judge_inactive",
                          detail="RWE_EVENT_JUDGE=1 but ANTHROPIC_API_KEY is not set")
            return False
        self._thread = threading.Thread(target=self._run, name="event-judge", daemon=True)
        self._thread.start()
        if self._log:
            self._log(logging.INFO, "event_judge_started", model=judge_model(),
                      budget=judge_budget(), intervalSeconds=judge_interval())
        return True

    def _run(self) -> None:
        while not self._stop.wait(judge_interval()):
            try:
                adapter = ClaudeAdapter(api_key())
                judge_pending(self._store, adapter, budget=judge_budget(), log=self._log)
            except Exception:                        # noqa: BLE001 — a cycle must never kill the thread
                if self._log:
                    self._log(logging.WARNING, "event_judge_cycle_failed", exc_info=True)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
