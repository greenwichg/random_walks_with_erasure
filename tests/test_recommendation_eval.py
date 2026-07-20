"""RC2.5 — recommendation evaluation & attribution.

Pure deterministic attribution (three-way split, drift isolation, estimated-vs-realized, calibration,
per-rule quality), the store eval-snapshot projection, and the read-only API endpoints.
"""
import importlib.util
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "examples" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


re_eval = _load("recommendation_eval")
store = _load("store")
api_fastapi = _load("api_fastapi")


def _snap(date, reads, td=None, sd=None, est=None):
    metrics = {}
    if td is not None:
        metrics["topicDiversity"] = td
    if sd is not None:
        metrics["sourceDiversity"] = sd
    return {"date": f"2026-01-{date:02d}T00:00:00", "reads": reads, "metrics": metrics,
            "estimates": est or {}}


# --------------------------------------------------------------------------- #
# 1) pure attribution
# --------------------------------------------------------------------------- #
def test_attribution_sums_to_total_and_isolates_drift():
    series = [
        {"date": "2026-01-01T00:00:00", "reads": 5, "score": 20},
        {"date": "2026-01-02T00:00:00", "reads": 5, "score": 23},   # no new reads → +3 drift
        {"date": "2026-01-03T00:00:00", "reads": 8, "score": 30},   # reads grew, accepted → +7 attributed
    ]
    a = re_eval.attribute(series, accepted_at="2026-01-03T00:00:00")
    assert a["populationDrift"] == 3.0
    assert a["recommendationAttributed"] == 7.0 and a["organic"] == 0.0
    assert a["behavioralWindows"] == 1
    total = a["recommendationAttributed"] + a["organic"] + a["populationDrift"]
    assert round(total, 2) == series[-1]["score"] - series[0]["score"]   # telescopes to 10


def test_attribution_organic_when_not_yet_accepted():
    series = [
        {"date": "2026-01-01T00:00:00", "reads": 5, "score": 20},
        {"date": "2026-01-02T00:00:00", "reads": 9, "score": 28},   # reads grew, NOT accepted → organic
    ]
    a = re_eval.attribute(series, accepted_at=None)
    assert a["organic"] == 8.0 and a["recommendationAttributed"] == 0.0 and a["populationDrift"] == 0.0


def test_attribution_is_deterministic():
    series = [{"date": f"2026-01-0{i}T00:00:00", "reads": i, "score": 20 + i} for i in range(1, 5)]
    assert re_eval.attribute(series, "2026-01-02T00:00:00") == re_eval.attribute(series, "2026-01-02T00:00:00")


# --------------------------------------------------------------------------- #
# 2) estimated vs realized + calibration
# --------------------------------------------------------------------------- #
def test_evaluate_recommendation_calibration_and_realized():
    snaps = [_snap(1, 5, td=20, est={"topicDiversity": {"low": 4, "high": 8}}),
             _snap(2, 5, td=23),                                    # +3 drift
             _snap(3, 8, td=30), _snap(4, 11, td=33)]              # +7, +3 attributed (accepted from d3)
    row = {"recKey": "imp_topicDiversity", "metric": "topicDiversity", "state": "in_progress",
           "firstScore": 20, "currentScore": 33, "generatedAt": "2026-01-01T00:00:00",
           "acceptedAt": "2026-01-03T00:00:00"}
    ev = re_eval.evaluate_recommendation(snaps, row)
    assert ev["estimatedGain"] == 6 and ev["realizedGain"] == 13
    assert ev["attribution"] == {"recommendationAttributed": 10.0, "organic": 0.0, "populationDrift": 3.0}
    assert ev["calibrationError"] == 4.0                           # attributed 10 − estimated 6 (under)
    assert ev["attributionConfidence"] == "medium"                 # 2 behavioural windows


def test_calibration_none_when_not_acted():
    snaps = [_snap(1, 5, td=20, est={"topicDiversity": {"low": 4, "high": 8}}), _snap(2, 8, td=25)]
    row = {"recKey": "imp_topicDiversity", "metric": "topicDiversity", "state": "dismissed",
           "firstScore": 20, "currentScore": 25, "generatedAt": "2026-01-01T00:00:00",
           "acceptedAt": None, "dismissedAt": "2026-01-01T06:00:00"}
    ev = re_eval.evaluate_recommendation(snaps, row)
    assert ev["calibrationError"] is None and ev["attributionConfidence"] == "not_acted"


# --------------------------------------------------------------------------- #
# 3) per-rule cohort quality
# --------------------------------------------------------------------------- #
def test_rule_quality_rates_and_calibration_direction():
    rows = [
        {"recKey": "imp_topicDiversity", "metric": "topicDiversity", "state": "completed"},
        {"recKey": "imp_topicDiversity", "metric": "topicDiversity", "state": "dismissed"},
        {"recKey": "imp_topicDiversity", "metric": "topicDiversity", "state": "accepted"},
        {"recKey": "imp_topicDiversity", "metric": "topicDiversity", "state": "shown"},
    ]
    evals = [
        {"metric": "topicDiversity", "realizedGain": 12, "estimatedGain": 6, "calibrationError": 6.0,
         "sustainedImprovement": True},
        {"metric": "topicDiversity", "realizedGain": 0, "estimatedGain": 6, "calibrationError": None,
         "sustainedImprovement": None},
        {"metric": "topicDiversity", "realizedGain": 4, "estimatedGain": 6, "calibrationError": -2.0,
         "sustainedImprovement": None},
        {"metric": "topicDiversity", "realizedGain": 1, "estimatedGain": 6, "calibrationError": None,
         "sustainedImprovement": None},
    ]
    q = re_eval.rule_quality(evals, rows)["topicDiversity"]
    assert q["instances"] == 4
    assert q["acceptanceRate"] == 0.5 and q["completionRate"] == 0.25 and q["dismissalRate"] == 0.25
    # calibration mean over the two present: (6 + −2)/2 = 2 → under_estimates
    assert q["calibrationError"] == 2.0 and q["calibrationDirection"] == "under_estimates"


# --------------------------------------------------------------------------- #
# 4) store eval-snapshot projection
# --------------------------------------------------------------------------- #
def test_store_eval_snapshots_extract_reads_scores_estimates(tmp_path):
    s = store.Store(f"sqlite:///{tmp_path / 'ih.db'}")
    uid = s.upsert_user_by_identity("google", "eval-1").id
    s.save_report(uid, {"mode": "measured", "overall": 40,
                        "coverage": {"reads": 7, "threshold": 5, "sufficient": True},
                        "metrics": [{"key": "topicDiversity", "score": 22, "available": True}],
                        "improvements": [{"metric": "topicDiversity",
                                          "impactEstimate": {"low": 3, "high": 7}}]})
    snaps = s.report_eval_snapshots(uid)
    assert len(snaps) == 1
    assert snaps[0]["reads"] == 7 and snaps[0]["metrics"]["topicDiversity"] == 22
    assert snaps[0]["estimates"]["topicDiversity"] == {"low": 3, "high": 7}
    assert uid in s.list_users_with_improvement_lifecycle() or s.list_users_with_improvement_lifecycle() == []


# --------------------------------------------------------------------------- #
# 5) API end-to-end
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def client():
    with TestClient(api_fastapi.app) as c:
        yield c


def _measured_uid(client, tag):
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": tag}).json()["userId"]
    reads = [{"url": f"https://ev-{tag}-{i}.com/politics/story-{i}"} for i in range(6)]
    client.post("/api/me/reads", json={"reads": reads}, headers={"X-IH-User-Id": str(uid)})
    return uid


def test_reader_evaluation_endpoint(client):
    uid = _measured_uid(client, "rc25-a")
    hdr = {"X-IH-User-Id": str(uid)}
    client.get("/api/report", headers=hdr)                         # generate + persist lifecycle
    ev = client.get("/api/me/recommendations/evaluation", headers=hdr).json()
    assert "recommendations" in ev and "outcomes" in ev
    for r in ev["recommendations"]:
        assert set(r["attribution"]) == {"recommendationAttributed", "organic", "populationDrift"}
        assert r["attributionConfidence"] in {"high", "medium", "low", "not_acted"}
        assert r["recKey"] == f"imp_{r['metric']}"


def test_evaluation_requires_auth(client):
    assert client.get("/api/me/recommendations/evaluation").status_code == 401


def test_dev_quality_endpoint_and_404_in_production(client, monkeypatch):
    uid = _measured_uid(client, "rc25-b")
    client.get("/api/report", headers={"X-IH-User-Id": str(uid)})
    q = client.get("/api/dev/recommendations/quality").json()
    assert "ruleQuality" in q and "cohortSize" in q and q["cohortSize"] >= 1
    monkeypatch.setattr(api_fastapi, "_production", lambda: True)
    assert client.get("/api/dev/recommendations/quality").status_code == 404
