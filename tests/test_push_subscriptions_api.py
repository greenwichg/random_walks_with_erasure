"""Tests for the B1 push endpoints — GET /api/push/config and the /api/me/push/subscriptions trio.

Drives the real FastAPI app. Registration only: nothing here sends a push, and no delivery, worker,
or fan-out exists yet (Phase B2). What is covered is the boundary a browser talks to — the
availability gate, payload validation, the idempotent upsert, user scoping, and the preference mirror
staying in step with the settings that own it.

The contract is `docs/BROWSER_PUSH_ARCHITECTURE.md` §7.
"""

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


def test_every_subscription_route_is_503_when_push_is_off(client, monkeypatch):
    """503 rather than 404: the route exists and the reason it will not serve is configuration."""
    monkeypatch.setenv("RWE_PUSH_ENABLED", "0")
    _, h = _user(client, "push-off")
    assert client.get("/api/me/push/subscriptions", headers=h).status_code == 503
    assert client.post("/api/me/push/subscriptions", json=_body("off"), headers=h).status_code == 503
    assert client.delete("/api/me/push/subscriptions",
                         params={"endpoint": _endpoint("off")}, headers=h).status_code == 503


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
