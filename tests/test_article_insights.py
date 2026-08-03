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
from sqlalchemy import select

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

    def complete(self, *, system, user, model, max_tokens, temperature=None):
        self.calls.append({"system": system, "user": user, "model": model,
                           "max_tokens": max_tokens, "temperature": temperature})
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

    def enqueue_insights(self, *, min_chars, scope="all", need=0):
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


# ------------------------------------------------------------------ #
# The FACETS contract (docs/COVERAGE_COMPARISON_REVISED_DESIGN.md §3)
#
# The comparison surface. The properties tested hardest are the ones that stop a generated record
# from becoming a false statement about a publisher once it is COUNTED: an evidence span must be
# verbatim in the article, a value outside the closed vocabulary is not evidence of anything, and
# a bad item is dropped without discarding the good record around it.
# ------------------------------------------------------------------ #

FACET_ARTICLE = {
    "headline": "Council approves harbour redevelopment after seven hour hearing",
    "description": ("Residents objected to the compensation offer while the developer defended "
                    "the cost. The council said the scheme would cost 340 million over three "
                    "decades. " * 3),
}


def facet_payload(**over):
    facets = {"format": "news_report",
              "frames": [{"key": "conflict", "evidence": "Residents objected"}],
              "depth": "episodic",
              "voices": [{"role": "affected_person", "name": "Residents",
                          "evidence": "Residents objected to the compensation offer"},
                         {"role": "corporate", "name": None,
                          "evidence": "the developer defended the cost"}],
              "centeredVoice": "official_government",
              "quantities": [{"kind": "money", "value": 340000000, "unit": "GBP",
                              "subject": "scheme cost",
                              "evidence": "cost 340 million over three decades"}]}
    facets.update(over)
    d = json.loads(good_payload())
    d["facets"] = facets
    return json.dumps(d)


def _facets(raw, article=FACET_ARTICLE):
    return ai.parse_and_validate(raw, ai.article_text(article))["facets"]


def test_facets_round_trip_with_every_vocabulary():
    f = _facets(facet_payload())
    assert f["vocabVersion"] == ai.VOCAB_VERSION
    assert f["format"] == "news_report" and f["depth"] == "episodic"
    assert [x["key"] for x in f["frames"]] == ["conflict"]
    assert [x["role"] for x in f["voices"]] == ["affected_person", "corporate"]
    assert f["centeredVoice"] == "official_government"
    assert f["quantities"][0]["value"] == 340000000
    assert f["quantities"][0]["subject"] == "scheme cost"


def test_evidence_span_must_be_verbatim_in_the_article():
    """The anti-hallucination gate (design §3.4). An invented span becomes a false statement
    about a named publisher the moment it is counted, so the item is dropped, not trusted."""
    f = _facets(facet_payload(frames=[{"key": "conflict",
                                       "evidence": "the mayor resigned in disgrace"}]))
    assert f["frames"] == []


def test_span_verification_is_whitespace_and_case_insensitive():
    """A model that re-wraps a quotation has not invented it."""
    f = _facets(facet_payload(frames=[{"key": "conflict",
                                       "evidence": "RESIDENTS\n   OBJECTED"}]))
    assert [x["key"] for x in f["frames"]] == ["conflict"]


def test_a_bad_item_is_dropped_without_discarding_the_record():
    f = _facets(facet_payload(voices=[
        {"role": "affected_person", "evidence": "Residents objected"},      # good
        {"role": "not_a_real_role", "evidence": "Residents objected"},      # bad enum
        {"role": "witness", "evidence": "nothing like this is in the text"},  # bad span
    ]))
    assert [x["role"] for x in f["voices"]] == ["affected_person"]


def test_values_outside_the_closed_vocabulary_become_null_not_guesses():
    f = _facets(facet_payload(format="think_piece", depth="both",
                              centeredVoice="the residents"))
    assert f["format"] is None and f["depth"] is None and f["centeredVoice"] is None


def test_missing_facets_yield_the_full_empty_shape():
    """Never a missing key: every consumer reads one shape, so the comparable set can never be
    built on an absent field — the defect class that made three L0 findings dead code."""
    f = _facets(good_payload())
    assert f == {"vocabVersion": ai.VOCAB_VERSION, "format": None, "frames": [], "depth": None,
                 "voices": [], "centeredVoice": None, "quantities": []}


def test_facet_lists_are_capped():
    many = [{"role": "witness", "evidence": "Residents objected"} for _ in range(20)]
    assert len(_facets(facet_payload(voices=many))["voices"]) == ai.FACET_CAPS["voices"]


def test_quantities_without_a_usable_value_or_subject_are_dropped():
    """A figure nobody can match to anyone else's figure is not a comparison input."""
    bad = [{"kind": "money", "value": "lots", "subject": "cost",
            "evidence": "cost 340 million over three decades"},
           {"kind": "money", "value": 1, "subject": "",
            "evidence": "cost 340 million over three decades"}]
    assert _facets(facet_payload(quantities=bad))["quantities"] == []


def test_no_label_rule_extends_to_the_open_facet_fields():
    """The rule that guards the bias prose has to guard the free-text facets too."""
    f = _facets(facet_payload(
        quantities=[{"kind": "money", "value": 1, "subject": "far-left funding",
                     "evidence": "cost 340 million over three decades"}],
        voices=[{"role": "corporate", "name": "A right-wing lobby",
                 "evidence": "the developer defended the cost"}]))
    assert f["quantities"] == [] and f["voices"] == []


def test_parse_without_the_article_text_drops_every_span_rather_than_trusting_it():
    """Failing safe: a caller that forgets the text gets an empty facets object, never an
    unverified one."""
    f = ai.parse_and_validate(facet_payload())["facets"]
    assert f["frames"] == [] and f["voices"] == [] and f["quantities"] == []
    assert f["format"] == "news_report"          # enum fields need no span


def test_generate_passes_the_article_text_to_span_verification():
    p = FakeProvider(facet_payload())
    out = ai.generate(FACET_ARTICLE, p)
    assert [x["key"] for x in out["facets"]["frames"]] == ["conflict"]
    assert out["inputChars"] == len(ai.article_text(FACET_ARTICLE))


def test_generate_asks_for_temperature_zero_by_default(monkeypatch):
    monkeypatch.delenv("RWE_INSIGHTS_TEMPERATURE", raising=False)
    p = FakeProvider(good_payload())
    ai.generate(ARTICLE, p)
    assert p.calls[0]["temperature"] == 0.0 and p.calls[0]["max_tokens"] == ai.MAX_TOKENS


def test_empty_temperature_means_send_nothing(monkeypatch):
    """The port's None sends no temperature at all, leaving the vendor default — what every
    caller got before the parameter existed."""
    monkeypatch.setenv("RWE_INSIGHTS_TEMPERATURE", "")
    assert ai.temperature() is None


def test_prompt_names_no_other_article():
    """Invariant 1 (design §2): the model sees ONE article. The prompt must not merely omit other
    coverage — it must forbid comparison, because the facets read like a comparison schema."""
    p = FakeProvider(good_payload())
    ai.generate(ARTICLE, p)
    system = p.calls[0]["system"]
    assert "Never compare it to other coverage" in system
    assert "you have not been shown any" in system.lower()


# ------------------------------------------------------------------ #
# Truncation: a budget problem, not a model error
# ------------------------------------------------------------------ #

def test_truncated_json_is_distinguished_from_malformed_json():
    """Three failures mark an article terminally `failed`, so misfiling a budget problem as a
    contract violation would permanently destroy coverage on the richest articles."""
    with pytest.raises(ai.TruncatedOutput, match="ended mid-JSON"):
        ai.parse_and_validate('{"summary": "First. Second.", "bias": {"framing": "a')
    with pytest.raises(ValueError, match="not JSON"):
        ai.parse_and_validate("I'm sorry, I can't help with that.")
    # …and the truncation type is still a ValueError, so every existing caller still catches it
    assert issubclass(ai.TruncatedOutput, ValueError)


def test_prose_that_was_never_json_is_not_called_truncation():
    with pytest.raises(ValueError) as e:
        ai.parse_and_validate("not json at all")
    assert not isinstance(e.value, ai.TruncatedOutput)


# ------------------------------------------------------------------ #
# Worker scale (design §9.4)
# ------------------------------------------------------------------ #

def test_concurrency_defaults_to_serial_and_is_bounded(monkeypatch):
    monkeypatch.delenv("RWE_INSIGHTS_CONCURRENCY", raising=False)
    assert ai.concurrency() == 1                  # provably inert until an operator raises it
    monkeypatch.setenv("RWE_INSIGHTS_CONCURRENCY", "8")
    assert ai.concurrency() == 8
    monkeypatch.setenv("RWE_INSIGHTS_CONCURRENCY", "9999")
    assert ai.concurrency() == 16                 # bounded: never an unbounded fan-out
    monkeypatch.setenv("RWE_INSIGHTS_CONCURRENCY", "junk")
    assert ai.concurrency() == 1


def test_concurrent_cycle_generates_every_row_exactly_once(monkeypatch):
    monkeypatch.setenv("RWE_INSIGHTS_CONCURRENCY", "4")
    st = FakeStore([_row(i) for i in range(9)])
    stats = ai.run_cycle(st, provider=FakeProvider(good_payload()), limit=9)
    assert stats["generated"] == 9 and stats["failed"] == 0
    assert len({aid for aid, _ok, _kw in st.finished}) == 9


def test_concurrent_cycle_still_isolates_one_failing_article(monkeypatch):
    monkeypatch.setenv("RWE_INSIGHTS_CONCURRENCY", "4")

    class Flaky(FakeProvider):
        def complete(self, *, system, user, model, max_tokens, temperature=None):
            if "boom" in user:
                raise RuntimeError("api down")
            return self.payload

    st = FakeStore([_row(1), {"article_id": "https://x.test/boom",
                              "article": {"headline": "boom", "description": "boom " * 60},
                              "content_hash": "hb"}])
    stats = ai.run_cycle(st, provider=Flaky(good_payload()), limit=2)
    assert stats == {"enqueued": 0, "generated": 1, "failed": 1}


def test_scope_is_all_by_default(monkeypatch):
    monkeypatch.delenv("RWE_INSIGHTS_SCOPE", raising=False)
    assert ai.scope() == "all"
    monkeypatch.setenv("RWE_INSIGHTS_SCOPE", "clustered")
    assert ai.scope() == "clustered"
    monkeypatch.setenv("RWE_INSIGHTS_SCOPE", "nonsense")
    assert ai.scope() == "all"


def test_run_cycle_stamps_the_recipe_hash():
    """What partitions the comparable set: records made different ways are not comparable."""
    st = FakeStore([_row(1)])
    ai.run_cycle(st, provider=FakeProvider(good_payload()), limit=1)
    _aid, ok, kw = st.finished[0]
    assert ok and kw["recipe_hash"] and len(kw["recipe_hash"]) == 16


def test_recipe_hash_changes_with_model_and_prompt(monkeypatch):
    p = FakeProvider(good_payload())
    monkeypatch.setenv("RWE_INSIGHTS_MODEL", "model-a")
    a = ai.recipe_hash(p)
    monkeypatch.setenv("RWE_INSIGHTS_MODEL", "model-b")
    assert ai.recipe_hash(p) != a


# ------------------------------------------------------------------ #
# The real store: facets persistence and cluster-first ordering (design §9.2)
# ------------------------------------------------------------------ #

def test_facets_and_parity_fields_survive_the_round_trip(seeded_store):
    st = seeded_store
    st.enqueue_insights(min_chars=100)
    row = st.claim_insights_batch(1, now=0.0)[0]
    payload = ai.parse_and_validate(facet_payload(), ai.article_text(FACET_ARTICLE))
    st.finish_insights(row["article_id"], ok=True, payload=payload, model="fake:m",
                       recipe_hash="abc123")
    served = st.get_insights([row["article_id"]])[row["article_id"]]
    assert served["facets"]["format"] == "news_report"
    assert [v["role"] for v in served["facets"]["voices"]] == ["affected_person", "corporate"]
    assert served["inputChars"] == payload["inputChars"]
    assert served["recipeHash"] == "abc123"


def test_a_legacy_row_without_facets_reads_as_null_not_as_an_empty_extraction(seeded_store):
    """The comparable set treats a member without facets as NOT comparable. An empty object would
    read as 'extracted, found nothing', which is a different and much more dangerous claim."""
    st = seeded_store
    st.enqueue_insights(min_chars=100)
    row = st.claim_insights_batch(1, now=0.0)[0]
    st.finish_insights(row["article_id"], ok=True, payload=json.loads(good_payload()),
                       model="fake:m")
    served = st.get_insights([row["article_id"]])[row["article_id"]]
    assert served["facets"] is None and served["recipeHash"] is None


@pytest.fixture()
def clustered_store(tmp_path):
    """Three stories of different sizes, with the story map the product itself writes."""
    import store as store_mod
    st = store_mod.Store(f"sqlite:///{tmp_path}/clustered.db")
    desc = "A real description with plenty of grounding text for the generator. " * 6
    members = {}
    for story, size in (("big", 6), ("small", 2), ("mid", 4)):
        for i in range(size):
            url = f"https://{story}{i}.example.com/a"
            st.upsert_feed_article(
                canonical_url=url, url=url, publisher=f"{story} outlet {i}",
                source_publisher=None, title=f"{story} headline {i} about one event",
                description=desc, body=None, published_at="2026-08-01T00:00:00Z",
                source_feed="f",
                scored={"article_id": url, "outlet": f"{story}{i}", "category": "Politics",
                        "lean": 0.0, "political": True, "title": f"{story}{i}"})
            members[url] = story
    # one orphan article that belongs to no story at all
    st.upsert_feed_article(
        canonical_url="https://orphan.example.com/a", url="https://orphan.example.com/a",
        publisher="Orphan", source_publisher=None, title="Orphan headline about nothing",
        description=desc, body=None, published_at="2026-08-01T00:00:00Z", source_feed="f",
        scored={"article_id": "o", "outlet": "Orphan", "category": "Politics", "lean": 0.0,
                "political": True, "title": "Orphan"})
    st.replace_story_members(members)
    return st


def _story_of_queue(st, n):
    """The stories the first ``n`` claimed rows belong to, in claim order.

    The story name is the host label minus its trailing index — matched, not stripped, because
    ``str.rstrip`` removes a character SET and quietly turned "small" into "s"."""
    import re
    return [re.match(r"https://([a-z]+)\d*\.", r["article_id"]).group(1)
            for r in st.claim_insights_batch(n, now=0.0)]


def test_enqueue_is_cluster_first_biggest_cluster_leading(clustered_store):
    """Six generations spread over six clusters produce zero comparisons; six that complete one
    cluster produce six. The ordering is the whole return on the budget."""
    st = clustered_store
    assert st.enqueue_insights(min_chars=100, limit=4, need=3) == 4
    got = _story_of_queue(st, 4)
    assert set(got) == {"big"}          # the budget went entirely into one cluster


def test_clustered_scope_excludes_articles_in_no_story(clustered_store):
    st = clustered_store
    st.enqueue_insights(min_chars=100, scope="clustered", need=3)
    claimed = {r["article_id"] for r in st.claim_insights_batch(50, now=0.0)}
    assert "https://orphan.example.com/a" not in claimed
    assert len(claimed) == 12           # 6 + 2 + 4, the orphan left out


def test_default_scope_still_enqueues_unclustered_articles(clustered_store):
    st = clustered_store
    st.enqueue_insights(min_chars=100, need=3)
    claimed = {r["article_id"] for r in st.claim_insights_batch(50, now=0.0)}
    assert "https://orphan.example.com/a" in claimed


def test_the_cluster_closest_to_a_comparable_set_wins_over_the_biggest(clustered_store):
    """The deficit rule, isolated. A SMALL cluster two members short of rendering a card is worth
    more than a BIG one that is six short, because only the first turns the next two generations
    into a comparison. Size alone would order these the other way round."""
    import store as store_mod
    st = clustered_store
    # "mid" (4 members) already carries 2 artifacts → deficit 1.
    # "big" (6 members) carries none → deficit 3, despite being the larger cluster.
    with st._Session() as s:
        for i in range(2):
            url = f"https://mid{i}.example.com/a"
            row = s.execute(select(store_mod.FeedArticle)
                            .where(store_mod.FeedArticle.canonical_url == url)).scalar_one()
            s.add(store_mod.ArticleInsight(
                article_id=url, status="ok",
                content_hash=store_mod._insights_hash(row.title, row.description)))
        s.commit()

    assert st.enqueue_insights(min_chars=100, limit=2, need=3) == 2
    assert _story_of_queue(st, 2) == ["mid", "mid"]


def test_the_wire_carries_reader_content_only_not_the_comparison_inputs(analyze_client):
    """facets/inputChars/recipeHash exist for the Coverage Comparison tiers, which run on the
    story-build seam. No client reads them, and shipping them would put a kilobyte of internals
    on every analyze response."""
    client, st = analyze_client
    url = "https://seeded0.example.com/story/0"
    st.enqueue_insights(min_chars=40)
    payload = ai.parse_and_validate(facet_payload(), ai.article_text(FACET_ARTICLE))
    st.finish_insights(url, ok=True, payload=payload, model="fake:m", recipe_hash="abc123")
    served = client.post("/api/analyze", json={"url": url}).json()["insights"]
    assert set(served) == {"summary", "bias", "model", "generatedAt"}
    # …while the store still hands the full record to server-side consumers
    assert st.get_insights([url])[url]["facets"]["format"] == "news_report"
