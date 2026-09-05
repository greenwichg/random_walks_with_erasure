"""The boundary guards for the News Intelligence foundation (docs/NEWS_INTELLIGENCE_INFRASTRUCTURE.md §H.0).

Structural, in the mould of ``test_corpus_boundaries.py``: the platform package owns access and
shape and no intelligence; it never reaches reader state; the consumer path never reads the
platform's tables; the story builder stays pure; bodies never leave.
"""

import inspect
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

PLATFORM = ROOT / "examples" / "platform_api"

READER_MODULES = ("personalize", "api_server", "coach_service", "evidence_resolver", "health_report",
                  "notification_service", "notification_delivery", "push_delivery", "email_delivery",
                  "analysis_enrichment", "rec_context", "improvement_ledger")
READER_TABLES = ("reads", "saved_articles", "rec_events", "rec_feedback", "report_snapshots",
                 "notifications", "push_subscriptions", "api_tokens", "user_settings")


def _platform_sources() -> dict:
    return {p.name: p.read_text(encoding="utf-8") for p in PLATFORM.glob("*.py")}


def test_platform_package_imports_no_reader_relative_module():
    for name, src in _platform_sources().items():
        for mod in READER_MODULES:
            assert not re.search(rf"^\s*(import|from)\s+{mod}\b", src, re.M), f"{name} imports {mod}"


def test_platform_package_references_no_reader_table_or_me_route():
    for name, src in _platform_sources().items():
        for table in READER_TABLES:
            assert f'"{table}"' not in src and f"'{table}'" not in src, f"{name} names {table}"
        assert "/api/me" not in src, f"{name} references a reader route"
        assert "list_reads" not in src and "get_reads" not in src, f"{name} reads reader history"


def test_platform_calls_the_same_services_the_consumer_routes_call():
    src = (PLATFORM / "routes.py").read_text(encoding="utf-8")
    for call in ("search.search(", "story_service.list_stories(", "story_service.get_story(",
                 "story_service.similar_stories(", "story_intelligence.compute_intelligence(",
                 "publisher_service.get_publisher("):
        assert call in src, f"routes.py must reuse {call}"
    assert "build_stories(" not in src and "cluster(" not in src, "the platform must not re-cluster"


def test_engine_mounts_platform_only_behind_the_flag():
    src = (ROOT / "examples" / "api_fastapi.py").read_text(encoding="utf-8")
    assert "if platform_api.enabled():\n    platform_app.mount(app, _require_store" in src
    # the consumer routes never read the platform's tables
    for table in ("platform_keys", "platform_tenants", "platform_usage", "story_snapshots",
                  "story_membership", "article_provenance"):
        assert table not in src, f"api_fastapi references {table}"


def test_story_builder_stays_pure_and_history_runs_after_serving():
    import story_service
    build = inspect.getsource(story_service.build_stories)
    assert "story_history" not in build and "record_build" not in build
    cached = inspect.getsource(story_service._cached_build)
    assert "_record_history(store_, stories)" in cached
    assert cached.index("attach_tier_b(") < cached.index("_record_history(store_, stories)")
    assert "if topic is None and date_from is None and date_to is None:\n            _record_history" in cached
    hook = inspect.getsource(story_service._record_history)
    assert "except Exception" in hook and "return" in hook


def test_ingest_stamps_identity_at_the_one_choke_point():
    src = inspect.getsource(sys.modules.get("rss_ingest") or __import__("rss_ingest").ingest_entries)
    for needle in ("identity.publisher_id_for(scored.outlet)", "licence.class_for_channel(channel)",
                   "scorer_version=ingest.SCORER_VERSION"):
        assert needle in src


def test_bodies_never_reach_a_wire_shape():
    import discover
    src = inspect.getsource(discover.feed_article_to_article)
    assert '"body"' not in src
    shape_src = (PLATFORM / "shape.py").read_text(encoding="utf-8")
    assert '"body"' not in shape_src
    archive_src = (ROOT / "examples" / "archive.py").read_text(encoding="utf-8")
    assert 'r.pop("body", None)' in archive_src


def test_licence_and_identity_are_leaves_of_the_store():
    import identity
    import licence
    import store
    assert licence.LICENCE_CLASSES is store.LICENCE_CLASSES
    assert identity.article_id_for is store.article_id_for
    store_src = (ROOT / "examples" / "store.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import (identity|licence|platform_api|story_history|archive)\b", store_src, re.M)
