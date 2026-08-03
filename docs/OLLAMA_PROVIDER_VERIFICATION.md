# Ollama provider — implementation & pipeline verification report

**Scope:** a second `AIInsightsProvider` implementation (`examples/insights_provider.py::OllamaProvider`)
that reaches a local model over Ollama's HTTP API, plus verification of the complete pipeline —
generation, storage, API, UI, latency, cache behaviour — driven through it.
**Constraint honoured:** no business logic changed. The prompt, the JSON contract, the 2–4
sentence bound, the no-label rule, the store, the API, the worker and the UI are byte-identical;
the diff touches one file (`insights_provider.py`) plus a new test file and docs.
**Anthropic was not enabled and no API key was used** at any point in this verification.
**Commit:** `e05b8e8`. **Date:** 2026-08-03.

---

## 1. Read this first: what was and was not verified

The pipeline was verified end to end against a server on `127.0.0.1:11434` speaking Ollama's
documented wire protocol (`GET /api/tags`, `POST /api/chat`, `stream:false`, the
`message.content` envelope, and its error shapes). Every application component in the path was
the real one — the real worker, the real validator, real SQLite storage, the real FastAPI app,
a real Chromium browser.

**Weights-backed inference was not run, and no claim is made about model output quality.**
This container cannot obtain Ollama or any model: the network policy denies `ollama.com`
(the proxy logged its own `connect_rejected` for it), and `registry.ollama.ai` and
`huggingface.co` are unreachable (`000`), so neither `ollama serve` nor any GGUF could be
fetched. §7 gives the exact commands to re-run this same verification against a real model on a
machine that has one; nothing in the adapter changes for that run.

To keep the exercise honest rather than hollow, the local endpoint derives its answer *from the
article text it is sent* (extractive), so the validator, the store, the API and the UI all
handled genuinely article-derived content rather than a constant.

## 2. The implementation

One class, ~70 lines, implementing exactly `complete(system, user, model, max_tokens) → str`:

| aspect | choice |
|---|---|
| endpoint | `POST /api/chat`, `stream: false` — the endpoint that takes system and user separately, so the grounding prompt arrives verbatim |
| JSON | `format: "json"` — a transport-level sampler constraint, explicitly anticipated by the design ("a vendor whose SDK offers structured-output modes can adopt them inside its adapter"). The unchanged `parse_and_validate` still decides what is acceptable |
| budget | `options.num_predict` = the same `max_tokens` (700) every provider gets |
| endpoint config | `OLLAMA_HOST` — Ollama's own conventional variable, accepting `host:port` or a full URL; default `http://127.0.0.1:11434` |
| deadline | `RWE_INSIGHTS_OLLAMA_TIMEOUT`, default 300 s — local CPU inference is slow, and a missing deadline would wedge the worker thread |
| dormancy | a `GET /api/tags` reachability probe: nothing answering ⇒ `build()` returns `None` ⇒ feature dormant, exactly as a missing API key is for a hosted vendor |
| errors | HTTP status, `{"error": …}` inside a 200, and non-JSON envelopes all raise `RuntimeError`, which the **existing** worker books as a failed attempt with backoff |
| dependencies | none — stdlib `urllib`; no SDK, no key, no egress |

`from_env()` now logs each provider's own `unavailable_hint` ("no Ollama server answering…" vs
"missing ANTHROPIC_API_KEY…") instead of one generic phrase — the only other line touched.

## 3. Tests: switching providers is an environment change and nothing else

11 new tests (`tests/test_insights_ollama.py`), every one driving the real adapter over a real
socket against an Ollama-protocol server — the adapter itself is never monkeypatched:

- **`test_switching_providers_is_only_an_env_change`** — the headline claim. From one call site,
  flipping `RWE_INSIGHTS_PROVIDER` between `ollama` and `anthropic` yields the two different
  provider classes, and `article_insights.generate()` returns the **identical validated dict**
  through either. Same prompt, same contract, same output; only the transport differed.
- Model resolution: `RWE_INSIGHTS_MODEL` beats the provider default (`llama3.1`), verified from
  the actual request body the server received.
- `OLLAMA_HOST` forms (`host:port`, full URL, trailing slash, unset) and timeout parsing
  including a garbage value falling back to the documented 300 s.
- Unreachable endpoint ⇒ dormant with the honest reason, never an exception.
- The exact `/api/chat` body: `stream:false`, `format:"json"`, `num_predict:700`,
  `[system, user]` roles, and the grounding prompt **verbatim** (both the "ONLY the text
  provided" and the no-left/right-label clauses asserted).
- The unchanged validator still governs a local model's output: a 1-sentence summary and a
  "clearly right of centre" label leak both rejected; a fenced answer still parses.
- Transport failures (HTTP 404, error-in-200, non-JSON) raise so the worker retries.
- A worker cycle over Ollama stamps `ollama:llama3.1` and preserves `MAX_ATTEMPTS` /
  `BACKOFF_BASE_S`.

Suite at this commit: **engine 2,621 passed**; the committed insights e2e spec still green.

## 4. Full-pipeline verification (measured)

Run with `RWE_INSIGHTS_PROVIDER=ollama`, `RWE_INSIGHTS_MODEL=llama3.2:1b`,
`OLLAMA_HOST=127.0.0.1:11434`, **`ANTHROPIC_API_KEY` unset**, against real SQLite and the real app.

| stage | result |
|---|---|
| **Provider resolution** | `provider_name()=ollama`, built `OllamaProvider`, endpoint `http://127.0.0.1:11434`, model `llama3.2:1b`, no API key present |
| **Generation** | `run_cycle` → `{enqueued: 12, generated: 6, failed: 0}` — the batch cap (6) held; 28.3 ms for 6 articles = **4.7 ms/article** of pipeline overhead (transport + validate + store; *excludes* model inference) |
| **Storage** | 6 `ok` / 6 `pending`; every row stamped `model=ollama:llama3.2:1b`; `bias` JSON round-trips all five keys; summaries are the articles' own sentences ("Councillors voted 7-2 on Tuesday evening…") |
| **Dedup** | re-`enqueue_insights` added **0**; the next cycle drained the pending backlog (6 more) and regenerated **nothing** — `ok` rows 6 → 12 with `generated == delta` |
| **Failure handling** | endpoint flipped to a 404: cycle → `{generated: 0, failed: 1}`; row `status=pending attempts=1 next_attempt_at=1600` (now was 1000 ⇒ the 600 s base backoff), error text preserved, and the article is **not served** |
| **API — cache hit** | `/api/analyze` returns `insights` with `model=ollama:llama3.2:1b` and the generated summary/bias |
| **API — cache miss** | the failed article returns `insights: null` — a non-`ok` row is never served |
| **Latency** | hit median **7.1 ms** (min 6.4) vs miss median **7.0 ms** (min 6.3) over 25 calls each ⇒ **+0.02 ms** for serving the artifact; `get_insights` **0.41 ms/lookup** over 200 iterations |
| **UI** | a real Chromium via Playwright: the "AI summary & framing" card renders the ollama-generated summary, the framing/tone/omissions/viewpoint rows, the loaded-language chips ("council", "approves") and the AI disclaimer — screenshot captured |
| **Provider swap mid-flight** | switching to `anthropic` with no key ⇒ dormant with the honest reason, while **already-generated rows keep serving** (3/3) — the cache is provider-independent; switching back restores `OllamaProvider` |

The UI leg used a throwaway Playwright spec that generated its row through the real provider and
then drove the browser; it was deleted after the run (the repo keeps only the provider-agnostic
`article-insights.spec.ts`, which asserts the render/absent contract and still passes).

## 5. Cost and privacy note

The reason to want this provider: an Ollama run has **zero per-article API cost and no egress** —
article text never leaves the host. That reverses the enablement blocker recorded in
`docs/ARTICLE_INSIGHTS_VERIFICATION.md` §4 (spend sign-off before turning the feature on). The
trade is quality and throughput: a small local model on CPU generates in seconds, not
milliseconds, and is likelier to trip the validator — which is exactly why the validator is
provider-independent and why failures cost a retry rather than a bad artifact.

## 6. Production enablement (unchanged shape, no key)

```bash
# on a host running `ollama serve` with a model pulled, e.g. `ollama pull llama3.2:1b`
cd /opt/ih
printf 'RWE_INSIGHTS_ENABLED=1\nRWE_INSIGHTS_PROVIDER=ollama\nRWE_INSIGHTS_MODEL=llama3.2:1b\nOLLAMA_HOST=http://host.docker.internal:11434\n' | sudo tee -a deploy/.env
sudo bash deploy/ops/restart.sh api
```

`OLLAMA_HOST` and `RWE_INSIGHTS_OLLAMA_TIMEOUT` are already in the compose allowlist (added with
this change) so both reach the engine. The one deployment fact to respect: **`127.0.0.1` inside
the api container is the container, not the host** — a host-run Ollama must be addressed by the
host's LAN IP, or by adding `extra_hosts: ["host.docker.internal:host-gateway"]` to the api
service. Verify after restart with the same probe shape used elsewhere:

```bash
sudo docker exec -i deploy-api-1 python -c "
import sys; sys.path.insert(0,'/app/examples')
import insights_provider as ip, article_insights as ai
p = ip.from_env(log=lambda l,e,**f: print(e,f))
print('provider:', p, 'model:', ai.model_name(p), 'enabled:', ai.enabled())"
```

## 7. Reproducing §4 against a real model

Same script, same env, a real server — nothing in the adapter changes:

```bash
ollama serve &                 # or systemd
ollama pull llama3.2:1b
python - <<'PY'
import os; os.environ.update(RWE_INSIGHTS_PROVIDER="ollama", RWE_INSIGHTS_MODEL="llama3.2:1b")
import sys; sys.path.insert(0, "examples")
import article_insights, insights_provider, store as store_mod
p = insights_provider.from_env(); print("provider:", p)
print(article_insights.run_cycle(store_mod.Store(), provider=p, limit=3))
PY
```

The numbers that will differ: per-article generation latency (seconds, model- and
hardware-dependent, versus the 4.7 ms of pipeline overhead measured here) and the validator's
pass rate on that model's output. The numbers that will not: storage, dedup, retry, API, cache
and UI behaviour — all of which are provider-independent by construction and verified above.
