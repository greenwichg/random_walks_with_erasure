"""Tests for the Ollama provider — examples/insights_provider.py::OllamaProvider.

The point of these tests is the architectural claim, not the vendor: **switching providers is an
environment change and nothing else**. Every test drives the REAL adapter over a REAL HTTP
socket — a local server speaking Ollama's documented wire protocol (``GET /api/tags``,
``POST /api/chat``) — so the request body, headers, response parsing, error mapping and
reachability probe are exercised end to end rather than monkeypatched away.

What these do NOT prove (see docs/OLLAMA_PROVIDER_VERIFICATION.md): the quality of a real
model's output. No weights run here; that is a property of the model, not of the adapter.
"""

import json
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import article_insights as ai    # noqa: E402
import insights_provider as ip   # noqa: E402

VALID = {"summary": "First sentence here. Second sentence here.",
         "bias": {"framing": "Foregrounds the council's own account.",
                  "tone": "Measured, e.g. 'officials said'.",
                  "loadedLanguage": ["crackdown"],
                  "omissions": "No cost figures are given.",
                  "viewpoint": "Centres officials; residents appear once."}}

ARTICLE = {"headline": "City council passes budget after long debate",
           "description": "The council voted 7-2 on Tuesday evening. " * 12}


class _Handler(BaseHTTPRequestHandler):
    """Ollama's documented endpoints, faithfully: /api/tags for liveness, /api/chat for chat."""

    def log_message(self, *a):                      # keep pytest output clean
        pass

    def do_GET(self):
        if self.path == "/api/tags":
            self._send(200, {"models": [{"name": "llama3.1:latest"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/chat":
            return self._send(404, {"error": "not found"})
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"] or 0)) or b"{}")
        self.server.calls.append(body)
        mode = self.server.mode
        if mode == "http_error":
            return self._send(404, {"error": "model 'nope' not found, try pulling it first"})
        if mode == "error_in_200":
            return self._send(200, {"error": "an unexpected server error"})
        if mode == "garbage":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"<html>not json at all</html>")
            return
        # The real shape: an /api/chat envelope whose message.content is the model's text.
        content = self.server.content if self.server.content is not None else json.dumps(VALID)
        self._send(200, {"model": body.get("model"), "created_at": "2026-08-03T00:00:00Z",
                         "message": {"role": "assistant", "content": content},
                         "done": True, "eval_count": 128})

    def _send(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture()
def ollama(monkeypatch):
    """A live Ollama-protocol server on an ephemeral port, pointed at by OLLAMA_HOST."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.calls, srv.mode, srv.content = [], "ok", None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("OLLAMA_HOST", f"127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setenv("RWE_INSIGHTS_PROVIDER", "ollama")
    monkeypatch.delenv("RWE_INSIGHTS_MODEL", raising=False)
    yield srv
    srv.shutdown()


# ------------------------------------------------------------------ #
# Selection and configuration — env only
# ------------------------------------------------------------------ #

def test_ollama_is_selected_and_built_from_env_alone(ollama):
    p = ip.from_env()
    assert isinstance(p, ip.OllamaProvider) and p.name == "ollama"


def test_switching_providers_is_only_an_env_change(ollama, monkeypatch):
    """The headline claim: the SAME call site yields a different vendor purely from env.

    A stand-in ``anthropic`` module is installed so both branches are exercisable in one
    interpreter; nothing in the application changes between the two assertions but the value of
    ``RWE_INSIGHTS_PROVIDER``."""
    import types
    fake_sdk = types.ModuleType("anthropic")

    class _Msg:
        content = [types.SimpleNamespace(text=json.dumps(VALID))]

    class _Anthropic:
        def __init__(self, *a, **k):
            self.messages = types.SimpleNamespace(create=lambda **kw: _Msg())

    fake_sdk.Anthropic = _Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_sdk)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    monkeypatch.setenv("RWE_INSIGHTS_PROVIDER", "ollama")
    a = ip.from_env()
    monkeypatch.setenv("RWE_INSIGHTS_PROVIDER", "anthropic")
    b = ip.from_env()
    assert isinstance(a, ip.OllamaProvider) and isinstance(b, ip.AnthropicProvider)

    # …and the product output is identical through either: same prompt, same validation, same
    # contract. Only the transport differed.
    assert ai.generate(ARTICLE, a) == ai.generate(ARTICLE, b) == {
        "summary": VALID["summary"], "bias": VALID["bias"]}


def test_model_resolution_prefers_the_env_override_then_the_provider_default(ollama, monkeypatch):
    p = ip.from_env()
    ai.generate(ARTICLE, p)
    assert ollama.calls[-1]["model"] == "llama3.1"          # OllamaProvider.default_model
    monkeypatch.setenv("RWE_INSIGHTS_MODEL", "qwen2.5:0.5b")
    ai.generate(ARTICLE, p)
    assert ollama.calls[-1]["model"] == "qwen2.5:0.5b"      # RWE_INSIGHTS_MODEL wins


def test_host_forms_and_timeout_come_from_conventional_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "1.2.3.4:11434")
    assert ip.OllamaProvider.base_url() == "http://1.2.3.4:11434"
    monkeypatch.setenv("OLLAMA_HOST", "http://box.local:9999/")
    assert ip.OllamaProvider.base_url() == "http://box.local:9999"
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert ip.OllamaProvider.base_url() == "http://127.0.0.1:11434"
    monkeypatch.setenv("RWE_INSIGHTS_OLLAMA_TIMEOUT", "45")
    assert ip.OllamaProvider.timeout_s() == 45.0
    monkeypatch.setenv("RWE_INSIGHTS_OLLAMA_TIMEOUT", "not-a-number")
    assert ip.OllamaProvider.timeout_s() == 300.0            # documented default, never a crash


def test_unreachable_ollama_is_dormant_not_broken(monkeypatch):
    logged = []
    monkeypatch.setenv("RWE_INSIGHTS_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:1")         # nothing listens there
    assert ip.from_env(log=lambda lvl, ev, **f: logged.append(f)) is None
    assert "Ollama server" in logged[-1]["reason"]


# ------------------------------------------------------------------ #
# The wire contract
# ------------------------------------------------------------------ #

def test_request_body_matches_ollamas_chat_api(ollama):
    p = ip.from_env()
    ai.generate(ARTICLE, p)
    sent = ollama.calls[-1]
    assert sent["stream"] is False                            # a single, non-streamed answer
    assert sent["format"] == "json"                           # transport-level JSON constraint
    assert sent["options"]["num_predict"] == 700              # max_tokens, unchanged budget
    roles = [m["role"] for m in sent["messages"]]
    assert roles == ["system", "user"]
    # the grounding prompt reaches the model verbatim — no provider-side rewriting
    assert "ONLY the text provided" in sent["messages"][0]["content"]
    assert "Never label the article or outlet as left or right" in sent["messages"][0]["content"]
    assert ARTICLE["headline"] in sent["messages"][1]["content"]


def test_existing_validation_still_governs_a_local_models_output(ollama):
    """Business logic is untouched: the same validator judges Ollama output, and rejects the
    same things. A local model that breaks the contract fails the attempt — it is never served."""
    p = ip.from_env()
    ollama.content = json.dumps({**VALID, "summary": "Only one sentence."})
    with pytest.raises(ValueError, match="2-4 sentences"):
        ai.generate(ARTICLE, p)
    bad = json.loads(json.dumps(VALID))
    bad["bias"]["viewpoint"] = "The piece is clearly right of centre."
    ollama.content = json.dumps(bad)
    with pytest.raises(ValueError, match="label"):
        ai.generate(ARTICLE, p)
    ollama.content = "```json\n" + json.dumps(VALID) + "\n```"   # fenced answers still parse
    assert ai.generate(ARTICLE, p)["summary"] == VALID["summary"]


@pytest.mark.parametrize("mode, needle", [
    ("http_error", "ollama HTTP 404"),
    ("error_in_200", "ollama error"),
    ("garbage", "non-JSON envelope"),
])
def test_transport_failures_raise_so_the_worker_can_book_a_retry(ollama, mode, needle):
    p = ip.from_env()
    ollama.mode = mode
    with pytest.raises(RuntimeError, match=needle):
        ai.generate(ARTICLE, p)


# ------------------------------------------------------------------ #
# The worker, unchanged, driving a local model
# ------------------------------------------------------------------ #

class _FakeStore:
    def __init__(self, rows):
        self.rows, self.finished = rows, []

    def enqueue_insights(self, *, min_chars):
        return 0

    def claim_insights_batch(self, n, *, now):
        return self.rows[:n]

    def finish_insights(self, article_id, *, ok, **kw):
        self.finished.append((article_id, ok, kw))


def test_worker_cycle_over_ollama_stamps_provider_and_keeps_retry_semantics(ollama):
    rows = [{"article_id": f"https://x.test/{i}", "article": ARTICLE, "content_hash": f"h{i}"}
            for i in range(4)]
    st = _FakeStore(rows)
    stats = ai.run_cycle(st, limit=2)                      # provider resolved from env
    assert stats == {"enqueued": 0, "generated": 2, "failed": 0}
    assert st.finished[0][2]["model"] == "ollama:llama3.1"  # attributable cache entries

    ollama.mode = "http_error"
    st2 = _FakeStore(rows[:1])
    stats2 = ai.run_cycle(st2, limit=1)
    assert stats2["failed"] == 1
    _, ok, kw = st2.finished[0]
    assert not ok and "ollama HTTP 404" in kw["error"]
    assert kw["max_attempts"] == ai.MAX_ATTEMPTS and kw["backoff_base_s"] == ai.BACKOFF_BASE_S
