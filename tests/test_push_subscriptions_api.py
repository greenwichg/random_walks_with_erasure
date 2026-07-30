"""Tests for the B1 push endpoints — GET /api/push/config and the /api/me/push/subscriptions trio.

Drives the real FastAPI app. Registration only: nothing here sends a push, and no delivery, worker,
or fan-out exists yet (Phase B2). What is covered is the boundary a browser talks to — the
availability gate, payload validation, the idempotent upsert, user scoping, and the preference mirror
staying in step with the settings that own it.

The contract is `docs/BROWSER_PUSH_ARCHITECTURE.md` §7.
"""

import json
import logging
import pathlib
import sys
import uuid

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
from fastapi.testclient import TestClient   # noqa: E402
import api_fastapi                          # noqa: E402


VAPID_PUBLIC = "BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U"
_RUN = uuid.uuid4().hex[:8]   # the file-backed store persists across runs; keep accounts unique


@pytest.fixture(scope="module")
def client():
    with TestClient(api_fastapi.app) as c:
        yield c


@pytest.fixture()
def push_on(monkeypatch):
    """The configured, switched-on state. Everything but the availability tests assumes it."""
    monkeypatch.setenv("RWE_PUSH_ENABLED", "1")
    monkeypatch.setenv("RWE_VAPID_PUBLIC_KEY", VAPID_PUBLIC)


def _user(client, acct):
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": f"{acct}-{_RUN}"}).json()["userId"]
    return uid, {"X-IH-User-Id": str(uid)}


def _endpoint(tag):
    return f"https://fcm.googleapis.com/fcm/send/{tag}-{_RUN}"


def _body(tag, **kw):
    return {"endpoint": _endpoint(tag), "p256dh": "BPubKey_abcdef", "auth": "AuthSecret1", **kw}


# --------------------------------------------------------------------------------------------- #
# Availability — the gate every push route runs first.
# --------------------------------------------------------------------------------------------- #
def test_config_reports_unavailable_when_the_switch_is_off(client, monkeypatch):
    monkeypatch.setenv("RWE_PUSH_ENABLED", "0")
    monkeypatch.setenv("RWE_VAPID_PUBLIC_KEY", VAPID_PUBLIC)
    assert client.get("/api/push/config").json() == {"enabled": False, "publicKey": VAPID_PUBLIC}


def test_config_reports_unavailable_when_the_key_is_missing(client, monkeypatch):
    """Half-configured is unavailable, not half-live. An operator who set the switch without the key
    should be told the feature is off rather than watch browsers fail to subscribe."""
    monkeypatch.setenv("RWE_PUSH_ENABLED", "1")
    monkeypatch.delenv("RWE_VAPID_PUBLIC_KEY", raising=False)
    assert client.get("/api/push/config").json() == {"enabled": False, "publicKey": ""}


def test_config_serves_the_public_key_when_enabled(client, push_on):
    assert client.get("/api/push/config").json() == {"enabled": True, "publicKey": VAPID_PUBLIC}


def test_config_needs_no_authentication(client, push_on):
    """A browser must decide whether to offer push before asking the reader for anything, and the only
    value here is a public key."""
    assert client.get("/api/push/config").status_code == 200


def test_registration_is_503_when_push_is_off(client, monkeypatch):
    """503 rather than 404: the route exists and the reason it will not serve is configuration."""
    monkeypatch.setenv("RWE_PUSH_ENABLED", "0")
    _, h = _user(client, "push-off")
    assert client.post("/api/me/push/subscriptions", json=_body("off"), headers=h).status_code == 503


# --------------------------------------------------------------------------------------------- #
# Rollback safety (P4). Rows survive a rollback by design — so re-enabling does not ask everyone to
# opt in again — which is precisely why the way OUT must keep working while the way IN is closed.
# --------------------------------------------------------------------------------------------- #
def test_a_reader_can_still_see_and_remove_devices_after_a_rollback(client, push_on, monkeypatch):
    """The whole point of P4. Register while push is on, switch it off, and the reader must still be
    able to inspect and delete what is registered in their name."""
    _, h = _user(client, "push-rollback")
    client.post("/api/me/push/subscriptions", json=_body("rollback"), headers=h)

    monkeypatch.setenv("RWE_PUSH_ENABLED", "0")            # the rollback

    listed = client.get("/api/me/push/subscriptions", headers=h)
    assert listed.status_code == 200, "a reader must be able to see what is registered for them"
    assert [s["endpoint"] for s in listed.json()] == [_endpoint("rollback")]

    removed = client.delete("/api/me/push/subscriptions",
                            params={"endpoint": _endpoint("rollback")}, headers=h)
    assert removed.status_code == 200 and removed.json()["removed"] is True
    assert client.get("/api/me/push/subscriptions", headers=h).json() == []


def test_a_rollback_does_not_delete_anything_by_itself(client, push_on, monkeypatch):
    """Rows outlive the switch: turning push off must not silently unregister devices, or turning it
    back on would ask every reader to opt in again."""
    _, h = _user(client, "push-survive")
    client.post("/api/me/push/subscriptions", json=_body("survive"), headers=h)
    monkeypatch.setenv("RWE_PUSH_ENABLED", "0")
    assert len(client.get("/api/me/push/subscriptions", headers=h).json()) == 1


def test_reads_and_deletes_still_require_authentication_when_push_is_off(client, monkeypatch):
    """Ungating the feature switch must not ungate anything else."""
    monkeypatch.setenv("RWE_PUSH_ENABLED", "0")
    assert client.get("/api/me/push/subscriptions").status_code in (401, 403)
    assert client.delete("/api/me/push/subscriptions",
                         params={"endpoint": _endpoint("anon")}).status_code in (401, 403)


# --------------------------------------------------------------------------------------------- #
# Authentication.
# --------------------------------------------------------------------------------------------- #
def test_subscription_routes_require_a_real_user(client, push_on):
    assert client.get("/api/me/push/subscriptions").status_code in (401, 403)
    assert client.post("/api/me/push/subscriptions", json=_body("anon")).status_code in (401, 403)


# --------------------------------------------------------------------------------------------- #
# Validation — this payload arrives from a client and becomes a URL the engine will POST to.
# --------------------------------------------------------------------------------------------- #
@pytest.mark.parametrize("patch,why", [
    ({"endpoint": "http://fcm.googleapis.com/fcm/send/x"}, "http would carry the payload in clear transport"),
    ({"endpoint": "https:///no-host"}, "hostless"),
    ({"endpoint": "not-a-url"}, "not a URL"),
    ({"endpoint": "https://x.example/" + "a" * 1100}, "over the length bound"),
    ({"p256dh": "has spaces and !"}, "not base64url"),
    ({"auth": "tilde~chars"}, "not base64url"),
    ({"p256dh": ""}, "empty"),
    ({"auth": "ab"}, "too short"),
])
def test_a_malformed_subscription_is_rejected(client, push_on, patch, why):
    """Rejected here rather than discovered at send time, when the failure is asynchronous and looks
    like a delivery bug."""
    _, h = _user(client, "push-validate")
    body = _body("validate")
    body.update(patch)
    assert client.post("/api/me/push/subscriptions", json=body, headers=h).status_code == 422, why


def test_a_valid_subscription_is_accepted_and_echoed_without_its_keys(client, push_on):
    _, h = _user(client, "push-create")
    r = client.post("/api/me/push/subscriptions",
                    json=_body("create", userAgent="Mozilla/5.0 (Macintosh)"), headers=h)
    assert r.status_code == 200
    got = r.json()
    assert got["endpoint"] == _endpoint("create")
    assert got["userAgent"] == "Mozilla/5.0 (Macintosh)"
    assert got["contentEncoding"] == "aes128gcm"
    assert "p256dh" not in got and "auth" not in got, "the device's keys never leave the store"
    assert set(got["categories"]) == {"breaking", "digests", "recommendations", "product"}


def test_the_user_agent_falls_back_to_the_request_header(client, push_on):
    """So the operator's device list is populated even for a client that does not send the field."""
    _, h = _user(client, "push-ua")
    r = client.post("/api/me/push/subscriptions", json=_body("ua"),
                    headers={**h, "user-agent": "TestClient/9"})
    assert r.json()["userAgent"] == "TestClient/9"


def test_an_out_of_range_expiration_does_not_cost_the_subscription(client, push_on):
    """`expirationTime` is advisory — a 410 is the authoritative end of a subscription — so nonsense
    in it is dropped rather than rejected."""
    _, h = _user(client, "push-exp")
    r = client.post("/api/me/push/subscriptions",
                    json=_body("exp", expirationTime=99_999_999_999_999_999), headers=h)
    assert r.status_code == 200 and r.json()["expiresAt"] is None


def test_a_plausible_expiration_is_stored_as_iso(client, push_on):
    _, h = _user(client, "push-exp2")
    r = client.post("/api/me/push/subscriptions",
                    json=_body("exp2", expirationTime=1_800_000_000_000), headers=h)
    assert (r.json()["expiresAt"] or "").startswith("2027-01-15T")


# --------------------------------------------------------------------------------------------- #
# The registration lifecycle.
# --------------------------------------------------------------------------------------------- #
def test_re_registering_refreshes_rather_than_duplicating(client, push_on):
    _, h = _user(client, "push-refresh")
    first = client.post("/api/me/push/subscriptions", json=_body("refresh"), headers=h).json()
    second = client.post("/api/me/push/subscriptions",
                         json=_body("refresh", p256dh="BRotatedKey", userAgent="Firefox/130"),
                         headers=h).json()
    assert second["id"] == first["id"]
    assert second["userAgent"] == "Firefox/130"
    assert len(client.get("/api/me/push/subscriptions", headers=h).json()) == 1


def test_a_reader_sees_only_their_own_devices(client, push_on):
    _, alice = _user(client, "push-alice")
    _, bob = _user(client, "push-bob")
    client.post("/api/me/push/subscriptions", json=_body("alice-dev"), headers=alice)
    client.post("/api/me/push/subscriptions", json=_body("bob-dev"), headers=bob)
    assert [s["endpoint"] for s in client.get("/api/me/push/subscriptions", headers=alice).json()] \
        == [_endpoint("alice-dev")]


def test_delete_is_user_scoped_and_idempotent(client, push_on):
    _, alice = _user(client, "push-del-a")
    _, bob = _user(client, "push-del-b")
    client.post("/api/me/push/subscriptions", json=_body("del-dev"), headers=alice)

    # Bob naming Alice's endpoint exactly must not unregister it.
    assert client.delete("/api/me/push/subscriptions",
                         params={"endpoint": _endpoint("del-dev")}, headers=bob).json()["removed"] is False
    assert len(client.get("/api/me/push/subscriptions", headers=alice).json()) == 1

    assert client.delete("/api/me/push/subscriptions",
                         params={"endpoint": _endpoint("del-dev")}, headers=alice).json()["removed"] is True
    assert client.delete("/api/me/push/subscriptions",
                         params={"endpoint": _endpoint("del-dev")}, headers=alice).json()["removed"] is False
    assert client.get("/api/me/push/subscriptions", headers=alice).json() == []


# --------------------------------------------------------------------------------------------- #
# The preference mirror — settings are the authority; this is the accelerator staying in step.
# --------------------------------------------------------------------------------------------- #
def test_registration_mirrors_the_readers_current_preferences(client, push_on):
    _, h = _user(client, "push-mirror")
    client.post("/api/me/settings",
                json={"notifications": {"categories": {"breaking": {"push": True}}}}, headers=h)
    got = client.post("/api/me/push/subscriptions", json=_body("mirror"), headers=h).json()
    assert got["categories"] == {"breaking": True, "digests": False,
                                 "recommendations": False, "product": False}


def test_changing_a_preference_re_mirrors_every_registered_device(client, push_on):
    """The one place preferences change is the one place the mirror is refreshed, so it cannot drift."""
    _, h = _user(client, "push-sync")
    client.post("/api/me/push/subscriptions", json=_body("sync-1"), headers=h)
    client.post("/api/me/push/subscriptions", json=_body("sync-2"), headers=h)
    assert not any(s["categories"]["breaking"]
                   for s in client.get("/api/me/push/subscriptions", headers=h).json())

    client.post("/api/me/settings",
                json={"notifications": {"categories": {"breaking": {"push": True}}}}, headers=h)
    subs = client.get("/api/me/push/subscriptions", headers=h).json()
    assert len(subs) == 2 and all(s["categories"]["breaking"] for s in subs)

    client.post("/api/me/settings",
                json={"notifications": {"categories": {"breaking": {"push": False}}}}, headers=h)
    assert not any(s["categories"]["breaking"]
                   for s in client.get("/api/me/push/subscriptions", headers=h).json())


def test_a_settings_save_still_succeeds_when_the_mirror_cannot_be_written(client, push_on, monkeypatch):
    """Fail-soft: the mirror is an optimisation, and losing it must never cost the reader a
    preference change."""
    _, h = _user(client, "push-soft")

    def boom(*a, **k):
        raise RuntimeError("disk hiccup")
    monkeypatch.setattr(api_fastapi.state.store.__class__, "sync_push_subscription_flags", boom)
    r = client.post("/api/me/settings", json={"readingGoalMinutes": 42}, headers=h)
    assert r.status_code == 200 and r.json()["readingGoalMinutes"] == 42


# --------------------------------------------------------------------------------------------- #
# Operational logging (P6). The events an operator reads to answer "is registration working, did a
# rotation repair devices, and did a browser change hands" — carrying a digest, never the endpoint.
# --------------------------------------------------------------------------------------------- #
@pytest.fixture()
def logged(caplog):
    """Structured log lines emitted during the test, decoded back into dicts."""
    caplog.set_level(logging.INFO, logger=api_fastapi.logger.name)

    def events(name=None):
        out = []
        for rec in caplog.records:
            try:
                payload = json.loads(rec.getMessage())
            except (TypeError, ValueError):
                continue
            if name is None or payload.get("event") == name:
                # The severity lives on the record, not in the JSON body, so carry it alongside —
                # a reassignment being a WARNING rather than an INFO is part of the contract.
                out.append({**payload, "_level": rec.levelname})
        return out
    return events


def test_a_new_device_logs_created_with_a_digest_not_the_endpoint(client, push_on, logged):
    _, h = _user(client, "log-created")
    client.post("/api/me/push/subscriptions", json=_body("log-created"), headers=h)

    line = logged("push_subscription_created")[-1]
    assert line["subscriptionId"] and line["userId"]
    assert line["reason"] == "user"
    assert len(line["endpointDigest"]) == 12
    # The two things that must never appear in a shipped, rotated, human-read log.
    blob = json.dumps(logged())
    assert _endpoint("log-created") not in blob, "the endpoint URL is a capability, not a log field"
    assert "BPubKey_abcdef" not in blob and "AuthSecret1" not in blob


def test_re_registration_logs_updated_and_carries_the_same_digest(client, push_on, logged):
    _, h = _user(client, "log-updated")
    client.post("/api/me/push/subscriptions", json=_body("log-updated"), headers=h)
    created = logged("push_subscription_created")[-1]
    client.post("/api/me/push/subscriptions",
                json=_body("log-updated", reason="worker"), headers=h)

    line = logged("push_subscription_updated")[-1]
    assert line["reason"] == "worker", "a browser-initiated refresh is distinguishable from a reader's"
    assert line["endpointDigest"] == created["endpointDigest"], "the same device correlates"


def test_a_shared_browser_changing_hands_is_logged_as_a_warning(client, push_on, logged):
    """The event nobody should see often, and which happened silently until now: one browser's
    endpoint moving between accounts. WARNING, and it names the account that lost it."""
    alice_uid, alice = _user(client, "log-alice")
    bob_uid, bob = _user(client, "log-bob")
    client.post("/api/me/push/subscriptions", json=_body("log-shared"), headers=alice)
    client.post("/api/me/push/subscriptions", json=_body("log-shared"), headers=bob)

    line = logged("push_subscription_reassigned")[-1]
    assert line["userId"] == bob_uid and line["previousUserId"] == alice_uid
    assert line["_level"] == "WARNING", "a browser changing hands is not routine INFO traffic"
    assert logged("push_subscription_created")[-1]["_level"] == "INFO", "a new device is routine"


def test_deletion_is_logged_with_its_reason(client, push_on, logged):
    _, h = _user(client, "log-deleted")
    client.post("/api/me/push/subscriptions", json=_body("log-deleted"), headers=h)
    client.delete("/api/me/push/subscriptions",
                  params={"endpoint": _endpoint("log-deleted"), "reason": "repair_retire"}, headers=h)

    line = logged("push_subscription_deleted")[-1]
    assert line["removed"] is True and line["reason"] == "repair_retire"
    assert _endpoint("log-deleted") not in json.dumps(line)


def test_an_unknown_deletion_reason_is_clamped_rather_than_trusted(client, push_on, logged):
    """A query parameter cannot be validated by the request model, and an arbitrary string in a log
    field is a log-injection vector — so it degrades to `user` rather than being echoed."""
    _, h = _user(client, "log-reason")
    client.delete("/api/me/push/subscriptions",
                  params={"endpoint": _endpoint("nope"), "reason": "../../evil"}, headers=h)
    assert logged("push_subscription_deleted")[-1]["reason"] == "user"


def test_a_rejected_registration_logs_the_failing_fields_and_no_values(client, push_on, logged):
    """A browser producing subscriptions the engine will not accept is invisible from both ends
    otherwise: the reader sees "could not enable" and the operator sees a bare 422 count."""
    _, h = _user(client, "log-invalid")
    body = _body("log-invalid")
    body["endpoint"] = "http://insecure.example/x"
    assert client.post("/api/me/push/subscriptions", json=body, headers=h).status_code == 422

    line = logged("push_subscription_rejected")[-1]
    assert line["fields"] == ["endpoint"] and line["errors"] == 1
    assert "insecure.example" not in json.dumps(line), "field NAMES only — never the submitted value"


def test_a_registration_refused_by_the_feature_gate_is_logged(client, monkeypatch, logged):
    """A burst of these is how an operator learns browsers are still trying to register against a
    deployment where push was switched off."""
    monkeypatch.setenv("RWE_PUSH_ENABLED", "0")
    _, h = _user(client, "log-gated")
    assert client.post("/api/me/push/subscriptions", json=_body("gated"), headers=h).status_code == 503

    line = logged("push_registration_rejected")[-1]
    assert line["enabled"] is False and line["userId"]


def test_reads_and_deletes_during_a_rollback_are_not_logged_as_gate_rejections(client, monkeypatch,
                                                                              logged):
    """They are not rejected, so they must not appear as rejections — otherwise the signal that means
    'browsers are failing to register' is diluted by ordinary rollback traffic."""
    monkeypatch.setenv("RWE_PUSH_ENABLED", "0")
    _, h = _user(client, "log-rollback")
    client.get("/api/me/push/subscriptions", headers=h)
    client.delete("/api/me/push/subscriptions", params={"endpoint": _endpoint("x")}, headers=h)
    assert logged("push_registration_rejected") == []


# --------------------------------------------------------------------------------------------- #
# Claiming another reader's endpoint (P5) and the per-reader device cap (P7).
# --------------------------------------------------------------------------------------------- #
def test_claiming_another_readers_endpoint_without_its_secret_is_409(client, push_on, logged):
    """Knowing an endpoint is not evidence of holding the subscription, and an endpoint leaks far
    more easily than the secret: a log, a HAR file, a screenshot."""
    _, alice = _user(client, "claim-alice")
    _, bob = _user(client, "claim-bob")
    client.post("/api/me/push/subscriptions",
                json=_body("claimed", auth="TheRealSecret"), headers=alice)

    stolen = client.post("/api/me/push/subscriptions",
                         json=_body("claimed", auth="GuessedSecret"), headers=bob)
    assert stolen.status_code == 409
    assert [s["endpoint"] for s in client.get("/api/me/push/subscriptions", headers=alice).json()] \
        == [_endpoint("claimed")], "the victim keeps their device"
    assert client.get("/api/me/push/subscriptions", headers=bob).json() == []

    line = logged("push_subscription_claim_refused")[-1]
    assert line["_level"] == "WARNING"
    assert _endpoint("claimed") not in json.dumps(line) and "GuessedSecret" not in json.dumps(line)


def test_a_genuine_shared_browser_handover_still_works(client, push_on):
    """The same browser signing in as someone else carries the same subscription, so the same secret.
    The check must cost that nothing — it is a real and supported flow."""
    _, alice = _user(client, "hand-alice")
    _, bob = _user(client, "hand-bob")
    body = _body("handover", auth="SameBrowserSecret")
    assert client.post("/api/me/push/subscriptions", json=body, headers=alice).status_code == 200
    assert client.post("/api/me/push/subscriptions", json=body, headers=bob).status_code == 200
    assert client.get("/api/me/push/subscriptions", headers=alice).json() == []
    assert len(client.get("/api/me/push/subscriptions", headers=bob).json()) == 1


def test_devices_are_capped_per_reader_and_the_eviction_is_logged(client, push_on, monkeypatch,
                                                                  logged):
    monkeypatch.setenv("RWE_PUSH_MAX_DEVICES", "2")
    _, h = _user(client, "cap")
    for i in range(3):
        client.post("/api/me/push/subscriptions", json=_body(f"cap-{i}"), headers=h)

    kept = [s["endpoint"] for s in client.get("/api/me/push/subscriptions", headers=h).json()]
    assert kept == [_endpoint("cap-2"), _endpoint("cap-1")], "the quietest device goes"

    line = logged("push_subscription_evicted")[-1]
    assert line["cap"] == 2 and line["userId"]
    assert _endpoint("cap-0") not in json.dumps(line), "a digest, not the endpoint"


def test_the_cap_falls_back_to_its_default_on_junk(client, push_on, monkeypatch):
    monkeypatch.setenv("RWE_PUSH_MAX_DEVICES", "not-a-number")
    assert api_fastapi._push_max_devices() == 10
    monkeypatch.setenv("RWE_PUSH_MAX_DEVICES", "0")
    assert api_fastapi._push_max_devices() == 0, "0 is a deliberate 'unbounded', not junk"
