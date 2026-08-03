"""Tests for examples/article_insights.py + examples/insights_provider.py.

Proves the provider-agnostic contract of docs/ARTICLE_INSIGHTS.md: the worker/policy layer
depends only on the AIInsightsProvider interface (a fake provider drives every path — no
vendor SDK, no network); the grounding prompt and output validation apply identically whatever
provider produced the text; provider and model are pure env configuration; and a provider that
cannot run means dormant, never broken.
"""

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import article_insights as ai    # noqa: E402
import insights_provider as ip   # noqa: E402


class FakeProvider(ip.AIInsightsProvider):
    """Interface-conformant test double: records calls, returns a canned payload (or raises)."""

    name = "fake"
    default_model = "fake-model-1"

    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.calls = payload, error, []

    def complete(self, *, system, user, model, max_tokens):
        self.calls.append({"system": system, "user": user, "model": model,
                           "max_tokens": max_tokens})
        if self.error is not None:
            raise self.error
        return self.payload


def good_payload(summary="First sentence. Second sentence."):
    return json.dumps({"summary": summary,
                       "bias": {"framing": "Foregrounds the mayor's response.",
                                "tone": "Urgent, e.g. 'chaos erupted'.",
                                "loadedLanguage": ["chaos erupted"],
                                "omissions": "No cost figures are given.",
                                "viewpoint": "Centres city officials; residents are quoted once."}})


ARTICLE = {"headline": "City council passes budget", "description": "The council voted 7-2 " * 20}


# ------------------------------------------------------------------ #
# generate(): the policy layer over the provider port
# ------------------------------------------------------------------ #

def test_generate_round_trip_through_the_interface(monkeypatch):
    monkeypatch.delenv("RWE_INSIGHTS_MODEL", raising=False)
    p = FakeProvider(good_payload())
    out = ai.generate(ARTICLE, p)
    assert out["summary"] == "First sentence. Second sentence."
    assert set(out["bias"]) == set(ai.BIAS_KEYS)
    call = p.calls[0]
    # grounding: the prompt carries ONLY the article's own text, and says so
    assert "City council passes budget" in call["user"]
    assert "ONLY the text provided" in call["system"]
    # the model resolves to the PROVIDER's default when the env override is unset
    assert call["model"] == "fake-model-1"


def test_model_env_override_beats_the_provider_default(monkeypatch):
    monkeypatch.setenv("RWE_INSIGHTS_MODEL", "some-other-model")
    p = FakeProvider(good_payload())
    ai.generate(ARTICLE, p)
    assert p.calls[0]["model"] == "some-other-model"


def test_generate_accepts_a_fenced_json_answer():
    p = FakeProvider("```json\n" + good_payload() + "\n```")
    assert ai.generate(ARTICLE, p)["summary"].startswith("First")


@pytest.mark.parametrize("raw, why", [
    ("not json at all", "not JSON"),
    (good_payload("One."), "2-4 sentences"),
    (good_payload("A. B. C. D. E."), "2-4 sentences"),
    (json.dumps({"summary": "One. Two.", "bias": {"framing": "x"}}), "incomplete"),
])
def test_validator_rejects_contract_violations(raw, why):
    with pytest.raises(ValueError, match=why):
        ai.parse_and_validate(raw)


def test_validator_rejects_left_right_labels():
    d = json.loads(good_payload())
    d["bias"]["viewpoint"] = "The article leans left throughout."
    with pytest.raises(ValueError, match="label"):
        ai.parse_and_validate(json.dumps(d))
    d["bias"]["viewpoint"] = "A right-wing framing dominates."
    with pytest.raises(ValueError, match="label"):
        ai.parse_and_validate(json.dumps(d))


# ------------------------------------------------------------------ #
# provider selection: pure env configuration
# ------------------------------------------------------------------ #

def test_default_provider_is_anthropic_and_keyless_means_dormant(monkeypatch):
    monkeypatch.delenv("RWE_INSIGHTS_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ip.provider_name() == "anthropic"
    assert ip.from_env() is None                      # no key -> dormant, never an error


def test_planned_and_unknown_providers_are_dormant_with_a_reason(monkeypatch):
    logged = []
    monkeypatch.setenv("RWE_INSIGHTS_PROVIDER", "gemini")
    assert ip.from_env(log=lambda lvl, ev, **f: logged.append(f)) is None
    assert logged[-1]["reason"] == "not implemented yet"
    monkeypatch.setenv("RWE_INSIGHTS_PROVIDER", "no-such-vendor")
    assert ip.from_env(log=lambda lvl, ev, **f: logged.append(f)) is None
    assert logged[-1]["reason"] == "unknown provider"


def test_anthropic_provider_builds_only_with_key_and_sdk(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    built = ip.AnthropicProvider.build()
    try:
        import anthropic  # noqa: F401
        assert isinstance(built, ip.AnthropicProvider)
        assert built.default_model == "claude-opus-4-8"
    except ImportError:
        assert built is None                          # missing SDK -> dormant, not broken


# ------------------------------------------------------------------ #
# run_cycle(): the worker sees only the interface + the store contract
# ------------------------------------------------------------------ #

class FakeStore:
    """Pins the store-accessor contract the real implementation must satisfy."""

    def __init__(self, rows):
        self.rows, self.finished = rows, []

    def enqueue_insights(self, *, min_chars):
        return 0

    def claim_insights_batch(self, n, *, now):
        return self.rows[:n]

    def finish_insights(self, article_id, *, ok, **kw):
        self.finished.append((article_id, ok, kw))


def _row(i, article=None):
    return {"article_id": f"https://x.test/{i}", "article": article or ARTICLE,
            "content_hash": f"h{i}"}


def test_run_cycle_respects_the_batch_cap_and_stamps_provider_model():
    st = FakeStore([_row(i) for i in range(10)])
    stats = ai.run_cycle(st, provider=FakeProvider(good_payload()), limit=3)
    assert stats == {"enqueued": 0, "generated": 3, "failed": 0}
    aid, ok, kw = st.finished[0]
    assert ok and kw["model"] == "fake:fake-model-1"
    assert kw["payload"]["summary"].startswith("First")


def test_run_cycle_isolates_a_failing_article_and_books_a_failed_attempt():
    boom = FakeProvider(error=RuntimeError("api down"))
    st = FakeStore([_row(1)])
    stats = ai.run_cycle(st, provider=boom, limit=1)
    assert stats["failed"] == 1 and stats["generated"] == 0
    aid, ok, kw = st.finished[0]
    assert not ok and "api down" in kw["error"]
    assert kw["max_attempts"] == ai.MAX_ATTEMPTS


def test_run_cycle_without_a_runnable_provider_is_a_no_op(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RWE_INSIGHTS_PROVIDER", raising=False)
    st = FakeStore([_row(1)])
    stats = ai.run_cycle(st)
    assert stats["skipped"] == "no provider" and st.finished == []


def test_request_generation_is_gated_by_the_env_flag(monkeypatch):
    monkeypatch.delenv("RWE_INSIGHTS_ENABLED", raising=False)
    # store=None proves the gate fires before any store access
    assert ai.request_generation(None) is False


# ------------------------------------------------------------------ #
# The real store: enqueue / claim / finish / read round-trip
# ------------------------------------------------------------------ #

@pytest.fixture()
def seeded_store(tmp_path):
    import store as store_mod
    st = store_mod.Store(f"sqlite:///{tmp_path}/insights.db")
    long_desc = "A real description with plenty of grounding text. " * 8
    for i, desc in enumerate([long_desc, long_desc, "stub"]):
        url = f"https://outlet{i}.example.com/a/{i}"
        st.upsert_feed_article(
            canonical_url=url, url=url, publisher=f"Outlet {i}", source_publisher=None,
            title=f"Headline number {i} about a real event", description=desc, body=None,
            published_at="2026-08-01T00:00:00Z", source_feed="f",
            scored={"article_id": url, "outlet": f"Outlet {i}", "category": "Politics",
                    "lean": 0.0, "political": True, "title": f"Headline {i}"})
    return st


def test_store_round_trip_enqueue_claim_finish_read(seeded_store):
    st = seeded_store
    # enqueue is idempotent and honours the eligibility floor (the "stub" article stays out)
    assert st.enqueue_insights(min_chars=100) == 2
    assert st.enqueue_insights(min_chars=100) == 0
    rows = st.claim_insights_batch(10, now=0.0)
    assert len(rows) == 2 and rows[0]["article"]["headline"].startswith("Headline")
    # one success, one failure
    ok_id, bad_id = rows[0]["article_id"], rows[1]["article_id"]
    st.finish_insights(ok_id, ok=True, payload=json.loads(good_payload()),
                       model="fake:fake-model-1", content_hash=rows[0]["content_hash"])
    st.finish_insights(bad_id, ok=False, error="boom", backoff_base_s=600.0,
                       max_attempts=3, now=1000.0)
    served = st.get_insights([ok_id, bad_id, "https://never.seen/x"])
    assert set(served) == {ok_id}                       # ok rows only, batched
    assert served[ok_id]["summary"].startswith("First")
    assert served[ok_id]["bias"]["loadedLanguage"] == ["chaos erupted"]
    assert served[ok_id]["model"] == "fake:fake-model-1"
    # the failed row backs off (not claimable now), then becomes claimable after the backoff
    assert st.claim_insights_batch(10, now=1000.0) == []
    assert [r["article_id"] for r in st.claim_insights_batch(10, now=1000.0 + 601)] == [bad_id]


def test_store_terminal_failure_after_max_attempts(seeded_store):
    st = seeded_store
    st.enqueue_insights(min_chars=100)
    row = st.claim_insights_batch(1, now=0.0)[0]
    aid = row["article_id"]
    for i in range(3):                                   # three strikes -> terminal failed
        st.finish_insights(aid, ok=False, error=f"e{i}", max_attempts=3, now=0.0)
    # terminal: never claimable again, even in the distant future
    assert all(r["article_id"] != aid for r in st.claim_insights_batch(10, now=10**12))


def test_store_content_hash_regeneration_on_description_backfill(seeded_store):
    """The one real path where an article's text changes: ``upsert_feed_article`` never rewrites
    first-seen metadata, but it DOES backfill a field that was empty — and a description arriving
    late must reset an already-generated row to pending (the regeneration rule)."""
    st = seeded_store
    bid = "https://outlet9.example.com/late-description"

    def _upsert(desc):
        st.upsert_feed_article(
            canonical_url=bid, url=bid, publisher="Outlet 9", source_publisher=None,
            title="A sufficiently long standalone headline about one real event", description=desc,
            body=None, published_at="2026-08-01T00:00:00Z", source_feed="f",
            scored={"article_id": bid, "outlet": "Outlet 9", "category": "Politics",
                    "lean": 0.0, "political": True, "title": "late"})

    _upsert("")                                          # first seen WITHOUT a description
    assert st.enqueue_insights(min_chars=40) >= 1        # title alone clears the floor
    st.finish_insights(bid, ok=True, payload=json.loads(good_payload()), model="m")
    assert bid in st.get_insights([bid])
    _upsert("The description arrives on a later poll. " * 10)   # backfill into the empty field
    assert st.enqueue_insights(min_chars=40) >= 1        # hash changed -> reset to pending
    assert bid not in st.get_insights([bid])             # no longer served until regenerated
    assert any(r["article_id"] == bid for r in st.claim_insights_batch(10, now=10**12))


# ------------------------------------------------------------------ #
# The API: /api/analyze attaches insights (cache-only, nullable)
# ------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def analyze_client(tmp_path_factory):
    import importlib.util
    import os
    import store as store_mod
    tmp = tmp_path_factory.mktemp("insightsapi")
    os.environ.update({"RWE_DB_URL": f"sqlite:///{tmp}/ins.db", "RWE_RECS_SOURCE": "feed",
                       "RWE_FEED_MIN_ARTICLES": "5", "RWE_CORPUS_MIN_ARTICLES": "5",
                       "RWE_SEED": "0", "RWE_STORY_SLOT": "0"})
    os.environ.pop("RWE_INTERNAL_SECRET", None)
    os.environ.pop("RWE_INSIGHTS_ENABLED", None)         # feature dormant: reads are cache-only
    st = store_mod.Store(os.environ["RWE_DB_URL"])
    long_desc = "A seeded article description with plenty of grounding text. " * 6
    for i in range(6):
        url = f"https://seeded{i}.example.com/story/{i}"
        st.upsert_feed_article(
            canonical_url=url, url=url, publisher=f"Seeded {i}", source_publisher=None,
            title=f"Seeded headline {i} about one distinct event entirely", description=long_desc,
            body=None, published_at="2026-08-01T00:00:00Z", source_feed="f",
            scored={"article_id": url, "outlet": f"Seeded {i}", "category": "Politics",
                    "lean": 0.0, "political": True, "title": f"Seeded {i}"})
    spec = importlib.util.spec_from_file_location("api_insights_test",
                                                  ROOT / "examples" / "api_fastapi.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["api_insights_test"] = mod
    spec.loader.exec_module(mod)
    from fastapi.testclient import TestClient
    with TestClient(mod.app) as client:
        yield client, st


def test_analyze_attaches_cached_insights_and_nulls_when_absent(analyze_client):
    client, st = analyze_client
    with_row = "https://seeded0.example.com/story/0"
    without = "https://seeded1.example.com/story/1"
    st.enqueue_insights(min_chars=40)
    st.finish_insights(with_row, ok=True, payload=json.loads(good_payload()),
                       model="fake:fake-model-1")
    a = client.post("/api/analyze", json={"url": with_row}).json()
    assert a["status"] == "analyzed"
    assert a["insights"]["summary"].startswith("First sentence")
    assert a["insights"]["bias"]["loadedLanguage"] == ["chaos erupted"]
    assert a["insights"]["model"] == "fake:fake-model-1"
    b = client.post("/api/analyze", json={"url": without}).json()
    assert b["status"] == "analyzed" and b["insights"] is None
