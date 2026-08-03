"""insights_provider.py — the provider port for Article Insights generation.

One small interface, :class:`AIInsightsProvider`, that the insights worker depends on; a
concrete provider adapts it to one vendor SDK. The product policy — the grounding prompt, the
output contract, the sentence bound, the no-label rule — lives in :mod:`article_insights` and
is enforced identically whatever provider produced the text. A provider is a *transport*, not
a policy owner: ``system + user + model → text`` and nothing else.

Selection is pure configuration (no application code changes to switch vendors):

* ``RWE_INSIGHTS_PROVIDER`` — which provider: ``anthropic`` (default, hosted Claude) or
  ``ollama`` (a local model over Ollama's HTTP API — no key, no egress). Names reserved for
  later implementations (``gemini``, ``openai``, ``grok``, ``local``) are recognized and
  reported honestly as not yet implemented, rather than crashing or silently falling back.
* ``RWE_INSIGHTS_MODEL`` — overrides the provider's own default model.
* Credentials and endpoints stay in each vendor's conventional variable (``ANTHROPIC_API_KEY``,
  ``OLLAMA_HOST``; ``GEMINI_API_KEY`` etc. when those providers land) — a provider switch never
  requires renaming secrets.

A provider that cannot run (missing key, missing package, unknown/unimplemented name) resolves
to ``None`` — the feature is dormant, never broken: :func:`article_insights.run_cycle` skips
with a reason and the request path is untouched.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Optional


class AIInsightsProvider(ABC):
    """The completion port. Implementations adapt exactly one vendor SDK.

    ``name`` identifies the provider in config and in stored rows (`model` is recorded as
    ``"<name>:<model>"`` so a cached artifact is forever attributable). ``default_model`` is
    what the provider uses when ``RWE_INSIGHTS_MODEL`` is unset."""

    name: str = "?"
    default_model: str = ""
    #: Why this provider would be unavailable, in operator language — logged when ``build()``
    #: declines, so a dormant feature says which knob is missing rather than a generic phrase.
    unavailable_hint: str = "missing credentials or SDK package"

    @classmethod
    def build(cls) -> "Optional[AIInsightsProvider]":
        """An instance, or ``None`` when the provider cannot run here (no key / no package).
        Never raises — absence means dormant, not broken."""
        return None

    @abstractmethod
    def complete(self, *, system: str, user: str, model: str, max_tokens: int) -> str:
        """One completion: the model's raw text. Raises on transport/API failure — the worker
        turns that into a failed attempt with backoff."""


class AnthropicProvider(AIInsightsProvider):
    """Claude via the official ``anthropic`` SDK (the first, and currently only, provider)."""

    name = "anthropic"
    default_model = "claude-opus-4-8"
    unavailable_hint = "missing ANTHROPIC_API_KEY or the anthropic package"

    def __init__(self, client):
        self._client = client

    @classmethod
    def build(cls) -> "Optional[AnthropicProvider]":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        try:
            import anthropic
        except ImportError:
            return None
        return cls(anthropic.Anthropic())

    def complete(self, *, system: str, user: str, model: str, max_tokens: int) -> str:
        msg = self._client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(getattr(b, "text", "") for b in (msg.content or []))


class OllamaProvider(AIInsightsProvider):
    """A local model served by Ollama, over its plain HTTP API — no SDK, no key, no egress.

    Uses ``POST /api/chat`` with ``stream: false``, which is the endpoint that takes a system
    and a user message separately — so the grounding prompt reaches the model exactly as
    written, unchanged from every other provider.

    Two vendor capabilities are used, both purely transport-level:

    * ``format: "json"`` constrains the server's sampler to emit syntactically valid JSON. This
      does not alter the contract, the prompt or the validation — the same
      ``article_insights.parse_and_validate`` still decides what is acceptable — it only stops a
      small local model from wrapping its answer in prose that would fail parsing for no good
      reason. (Ollama requires the prompt itself to ask for JSON, which the shared system prompt
      already does.)
    * ``options.num_predict`` carries ``max_tokens``, the same budget every provider gets.

    Configuration is env-only, and follows the rule the other providers follow: the endpoint
    lives in Ollama's OWN conventional variable, so switching to it never renames anything.

        RWE_INSIGHTS_PROVIDER=ollama          select this provider
        RWE_INSIGHTS_MODEL=<model>            e.g. llama3.1, qwen2.5, mistral (else default_model)
        OLLAMA_HOST=host:port | http://…      default http://127.0.0.1:11434
        RWE_INSIGHTS_OLLAMA_TIMEOUT=<seconds> default 300 — local CPU inference is slow, and a
                                              missing deadline would wedge the worker thread

    Dormancy is a reachability probe rather than a credential check: if nothing answers
    ``GET /api/tags`` the provider resolves to ``None`` and the feature is dormant, exactly as a
    missing API key does for a hosted vendor. A model that is not pulled is NOT checked here —
    that surfaces as a generation failure, which the worker already books with backoff."""

    name = "ollama"
    default_model = "llama3.1"
    unavailable_hint = "no Ollama server answering (check OLLAMA_HOST / `ollama serve`)"

    #: Reachability probe deadline. Local, so generous is still fast; never blocks a request path.
    PROBE_TIMEOUT_S = 3.0

    def __init__(self, base_url: str, timeout_s: float):
        self._base = base_url
        self._timeout = timeout_s

    @staticmethod
    def base_url() -> str:
        """``OLLAMA_HOST`` in any of the forms Ollama's own clients accept, normalised to a URL."""
        raw = (os.environ.get("OLLAMA_HOST") or "").strip() or "127.0.0.1:11434"
        if not raw.startswith(("http://", "https://")):
            raw = "http://" + raw
        return raw.rstrip("/")

    @staticmethod
    def timeout_s() -> float:
        try:
            return max(1.0, float(os.environ.get("RWE_INSIGHTS_OLLAMA_TIMEOUT", "300")))
        except (TypeError, ValueError):
            return 300.0

    @classmethod
    def build(cls) -> "Optional[OllamaProvider]":
        base = cls.base_url()
        try:
            req = urllib.request.Request(f"{base}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=cls.PROBE_TIMEOUT_S) as r:
                if r.status != 200:
                    return None
                r.read(1)
        except Exception:
            return None                       # unreachable → dormant, never broken
        return cls(base, cls.timeout_s())

    def complete(self, *, system: str, user: str, model: str, max_tokens: int) -> str:
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            "format": "json",
            "options": {"num_predict": int(max_tokens)},
        }
        req = urllib.request.Request(
            f"{self._base}/api/chat", method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                body = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200] if hasattr(e, "read") else ""
            raise RuntimeError(f"ollama HTTP {e.code}: {detail}") from None
        except Exception as e:                # connection reset, timeout, DNS…
            raise RuntimeError(f"ollama unreachable: {type(e).__name__}: {e}") from None
        try:
            data = json.loads(body)
        except ValueError:
            raise RuntimeError(f"ollama returned non-JSON envelope: {body[:200]}") from None
        # Ollama reports some failures as a 200 carrying an "error" key.
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"ollama error: {str(data['error'])[:200]}")
        msg = (data.get("message") or {}) if isinstance(data, dict) else {}
        # `message.content` is the /api/chat shape; `response` is /api/generate's, accepted so a
        # proxy or gateway in front of Ollama that speaks either shape still works.
        return msg.get("content") or (data.get("response") if isinstance(data, dict) else "") or ""


#: name → provider class. Adding a vendor = one class above + one row here; nothing else in
#: the application changes (worker/store/API/UI depend only on the interface).
_REGISTRY: dict = {AnthropicProvider.name: AnthropicProvider,
                   OllamaProvider.name: OllamaProvider}

#: Recognized-but-unimplemented names: configuring one is a plan, not a typo, and the log line
#: should say so. An unknown name gets the harsher "unknown" wording.
_PLANNED = ("gemini", "openai", "grok", "local")


def provider_name() -> str:
    return os.environ.get("RWE_INSIGHTS_PROVIDER", "").strip().lower() or AnthropicProvider.name


def from_env(log=None) -> Optional[AIInsightsProvider]:
    """The configured provider instance, or ``None`` (dormant) with the reason logged."""
    name = provider_name()
    cls = _REGISTRY.get(name)
    if cls is None:
        if log is not None:
            why = ("not implemented yet" if name in _PLANNED else "unknown provider")
            log(30, "insights_provider_unavailable", provider=name, reason=why)
        return None
    inst = cls.build()
    if inst is None and log is not None:
        log(30, "insights_provider_unavailable", provider=name, reason=cls.unavailable_hint)
    return inst
