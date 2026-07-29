"""concurrency_report — the parts that are measurement rather than model.

A concurrency estimate is a model, and a model whose INPUTS are wrong is worse than no estimate
because it looks like a result. These cover the measured inputs and the honesty machinery: log
parsing, path collapsing, and the share-of-cost figure that says how much of a workload's price
came from real samples versus a fallback.
"""
import io
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "examples"))

import concurrency_report as cr          # noqa: E402


def _log(*records):
    lines = []
    for r in records:
        lines.append("2026-07-29T10:00:00Z " + json.dumps({"event": "request", **r}))
    lines.append('INFO:     172.18.0.3:47712 - "GET /api/stories HTTP/1.1" 200 OK')   # uvicorn noise
    lines.append("not json at all")
    return io.StringIO("\n".join(lines) + "\n")


def test_parses_request_durations_and_ignores_non_request_lines():
    """The whole estimate rests on per-endpoint service time, which is the one input that IS
    measured. Uvicorn's access lines and any non-JSON output must not become fake samples."""
    ep = cr.parse_request_log(_log(
        {"method": "GET", "path": "/api/stories", "durationMs": 40.0},
        {"method": "GET", "path": "/api/stories", "durationMs": 60.0},
        {"method": "POST", "path": "/api/events", "durationMs": 4.0},
        {"event": "post_cycle", "cleanupMs": 2000},          # a different event: not a request
    ))
    assert set(ep) == {"GET /api/stories", "POST /api/events"}
    assert ep["GET /api/stories"]["n"] == 2
    assert ep["GET /api/stories"]["meanMs"] == 50.0
    assert ep["GET /api/stories"]["maxMs"] == 60.0


def test_path_parameters_collapse_so_one_endpoint_is_one_bucket():
    """``/api/publishers/<name>`` would otherwise shatter into hundreds of one-sample buckets, and
    a mean over one sample is not a measurement."""
    ep = cr.parse_request_log(_log(
        {"method": "GET", "path": "/api/publishers/BBC%20News", "durationMs": 30.0},
        {"method": "GET", "path": "/api/publishers/The%20Guardian", "durationMs": 50.0},
        {"method": "POST", "path": "/api/me/notifications/12345/seen", "durationMs": 3.0},
    ))
    assert ep["GET /api/publishers/{}"]["n"] == 2
    assert ep["GET /api/publishers/{}"]["meanMs"] == 40.0
    assert "POST /api/me/notifications/{}/seen" in ep


def test_cost_reports_how_much_of_it_was_actually_measured():
    """The honesty machinery. A workload priced entirely from fallbacks must not be presented the
    same way as one priced from production samples — that difference is the whole reason to trust
    or distrust the number downstream."""
    ep = {"GET /api/stories": {"n": 5, "meanMs": 40.0, "p50Ms": 40.0, "p95Ms": 40.0, "maxMs": 40.0}}

    full = cr.cost_of({"GET /api/stories": 1}, ep, fallback_ms=25.0)
    assert full["ms"] == 40.0 and full["measuredShare"] == 1.0 and full["missing"] == []

    none = cr.cost_of({"GET /api/report": 1}, ep, fallback_ms=25.0)
    assert none["ms"] == 25.0 and none["measuredShare"] == 0.0
    assert none["missing"] == ["GET /api/report"]

    part = cr.cost_of({"GET /api/stories": 1, "GET /api/report": 1}, ep, fallback_ms=20.0)
    assert part["ms"] == 60.0
    assert abs(part["measuredShare"] - (40.0 / 60.0)) < 1e-9


def test_sqlite_write_benchmark_reports_a_rate(tmp_path):
    """SQLite permits one writer at a time, so this is a hard ceiling on write-bearing requests
    however much CPU is spare. It must be benchmarked, never assumed — and never against the live
    database."""
    res = cr.bench_sqlite_writes(str(tmp_path), n=50)
    assert "error" not in res, res
    assert res["transactions"] == 50 and res["writesPerSec"] > 0
    assert not list(tmp_path.iterdir()), "the scratch database must be cleaned up"
