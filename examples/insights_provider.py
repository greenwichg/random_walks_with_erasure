"""insights_provider.py — the provider port for Article Insights generation.

One small interface, :class:`AIInsightsProvider`, that the insights worker depends on; a
concrete provider adapts it to one vendor SDK. The product policy — the grounding prompt, the
output contract, the sentence bound, the no-label rule — lives in :mod:`article_insights` and
is enforced identically whatever provider produced the text. A provider is a *transport*, not
a policy owner: ``system + user + model → text`` and nothing else.

Selection is pure configuration (no application code changes to switch vendors):

* ``RWE_INSIGHTS_PROVIDER`` — which provider (default ``anthropic``). Names reserved for
  later implementations (``gemini``, ``openai``, ``grok``, ``local``) are recognized and
  reported honestly as not yet implemented, rather than crashing or silently falling back.
* ``RWE_INSIGHTS_MODEL`` — overrides the provider's own default model.
* Credentials stay in each vendor's conventional variable (``ANTHROPIC_API_KEY`` today;
  ``GEMINI_API_KEY`` etc. when those providers land) — a provider switch never requires
  renaming secrets.

A provider that cannot run (missing key, missing package, unknown/unimplemented name) resolves
to ``None`` — the feature is dormant, never broken: :func:`article_insights.run_cycle` skips
with a reason and the request path is untouched.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Optional


class AIInsightsProvider(ABC):
    """The completion port. Implementations adapt exactly one vendor SDK.

    ``name`` identifies the provider in config and in stored rows (`model` is recorded as
    ``"<name>:<model>"`` so a cached artifact is forever attributable). ``default_model`` is
    what the provider uses when ``RWE_INSIGHTS_MODEL`` is unset."""

    name: str = "?"
    default_model: str = ""

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


#: name → provider class. Adding a vendor = one class above + one row here; nothing else in
#: the application changes (worker/store/API/UI depend only on the interface).
_REGISTRY: dict = {AnthropicProvider.name: AnthropicProvider}

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
        log(30, "insights_provider_unavailable", provider=name,
            reason="missing API key or SDK package")
    return inst
