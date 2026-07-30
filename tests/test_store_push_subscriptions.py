"""Tests for the B1 push-subscription persistence layer — the `push_subscriptions` table.

Covers the storage contract only: no delivery, no sending, no fan-out (Phase B2). What is asserted
here is the part that decides whether a later sender addresses the right device: idempotency on the
endpoint, refresh in place, REASSIGNMENT when a shared browser changes account, user-scoped deletion,
the denormalised preference mirror and its fail-closed default, and that the device's encryption keys
never appear in a returned view.

The contract this file pins is `docs/BROWSER_PUSH_ARCHITECTURE.md` §7.
"""

import pathlib
import sys
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import retention_policy             # noqa: E402
import settings_service as ss       # noqa: E402
import store as store_mod           # noqa: E402


ENDPOINT = "https://fcm.googleapis.com/fcm/send/cJKl-device-token-1"
OTHER = "https://updates.push.services.mozilla.com/wpush/v2/gAAAA-device-2"


@pytest.fixture()
def st():
    return store_mod.Store("sqlite://")                     # in-memory


def _user(st, account="push-1"):
    return st.upsert_user_by_identity("dev", account).id


def _sub(st, uid, endpoint=ENDPOINT, **kw):
    kw.setdefault("p256dh", "BPubKey_" + endpoint[-6:])
    kw.setdefault("auth", "AuthSecret1")
    return st.upsert_push_subscription(uid, endpoint, **kw)


def _cats(**overrides):
    """The `settings.notifications.categories` shape, normalised by the real settings service so the
    test cannot drift from the contract the API will pass in."""
    patch = {c: {"push": v} for c, v in overrides.items()}
    return ss.normalize_settings({"notifications": {"categories": patch}})["notifications"]["categories"]


# --------------------------------------------------------------------------------------------- #
# Registration, refresh, reassignment — the three situations that arrive at one method.
# --------------------------------------------------------------------------------------------- #
def test_a_new_device_is_stored_and_returned(st):
    uid = _user(st)
    row = _sub(st, uid, user_agent="Mozilla/5.0 (Macintosh)")
    assert row["endpoint"] == ENDPOINT
    assert row["userAgent"] == "Mozilla/5.0 (Macintosh)"
    assert row["contentEncoding"] == "aes128gcm"            # the default when unnegotiated
    assert row["id"] and row["createdAt"] and row["updatedAt"]
    assert [s["endpoint"] for s in st.list_push_subscriptions(uid)] == [ENDPOINT]


def test_re_registering_the_same_endpoint_refreshes_in_place(st):
    """A browser re-subscribes on its own schedule (key rotation, `pushsubscriptionchange`). Each one
    must update the row, not add another, or a long-lived device accumulates one row per rotation and
    every future fan-out pays to send to all of them."""
    uid = _user(st)
    first = _sub(st, uid, p256dh="BOldKey", auth="OldAuth")
    second = _sub(st, uid, p256dh="BNewKey", auth="NewAuth", user_agent="Firefox/130")

    assert second["id"] == first["id"], "same row"
    assert len(st.list_push_subscriptions(uid)) == 1
    assert second["userAgent"] == "Firefox/130"


def test_a_shared_browser_reassigns_the_endpoint_to_the_signed_in_reader(st):
    """THE case UNIQUE(endpoint) exists for. Sign out, sign in as someone else, subscribe again: the
    push service mints the SAME endpoint, and it will deliver one message to it. If both accounts
    owned a row, the previous reader would keep receiving notifications on a device that is no longer
    theirs — a privacy failure, not a duplicate-row problem."""
    alice, bob = _user(st, "alice"), _user(st, "bob")
    _sub(st, alice)
    _sub(st, bob)

    assert st.list_push_subscriptions(alice) == [], "the endpoint moved away from Alice"
    assert [s["endpoint"] for s in st.list_push_subscriptions(bob)] == [ENDPOINT]


def test_one_reader_may_hold_several_devices(st):
    uid = _user(st)
    _sub(st, uid, ENDPOINT)
    _sub(st, uid, OTHER)
    assert {s["endpoint"] for s in st.list_push_subscriptions(uid)} == {ENDPOINT, OTHER}


def test_devices_are_listed_newest_first(st):
    uid = _user(st)
    _sub(st, uid, ENDPOINT)
    _sub(st, uid, OTHER)
    assert [s["endpoint"] for s in st.list_push_subscriptions(uid)] == [OTHER, ENDPOINT]


# --------------------------------------------------------------------------------------------- #
# Deletion — user-scoped, idempotent.
# --------------------------------------------------------------------------------------------- #
def test_delete_removes_the_device_and_is_idempotent(st):
    uid = _user(st)
    _sub(st, uid)
    assert st.delete_push_subscription(uid, ENDPOINT) is True
    assert st.delete_push_subscription(uid, ENDPOINT) is False, "already gone"
    assert st.list_push_subscriptions(uid) == []


def test_delete_never_touches_another_readers_device(st):
    """Endpoints are guessable in the sense that they travel through a browser and a push service, so
    naming one exactly must not be enough to unregister it."""
    alice, bob = _user(st, "alice"), _user(st, "bob")
    _sub(st, alice, ENDPOINT)
    assert st.delete_push_subscription(bob, ENDPOINT) is False
    assert [s["endpoint"] for s in st.list_push_subscriptions(alice)] == [ENDPOINT]


# --------------------------------------------------------------------------------------------- #
# The denormalised preference mirror — an accelerator, never the authority.
# --------------------------------------------------------------------------------------------- #
def test_category_flags_are_mirrored_from_the_readers_preferences(st):
    uid = _user(st)
    row = _sub(st, uid, categories=_cats(breaking=True, product=True))
    assert row["categories"] == {"breaking": True, "digests": False,
                                 "recommendations": False, "product": True}


def test_absent_or_malformed_preferences_are_fail_closed(st):
    """Consent is never inferred. No categories, a junk shape, or a category we do not know all mean
    'not subscribed to that', because the alternative is sending on a preference nobody expressed."""
    uid = _user(st)
    assert _sub(st, uid)["categories"] == dict.fromkeys(
        ("breaking", "digests", "recommendations", "product"), False)
    assert _sub(st, uid, categories="not-a-dict")["categories"]["breaking"] is False
    assert _sub(st, uid, categories={"breaking": "yes"})["categories"]["breaking"] is False
    assert _sub(st, uid, categories={"unknown_category": {"push": True}})["categories"] == \
        dict.fromkeys(("breaking", "digests", "recommendations", "product"), False)


def test_refreshing_a_device_re_mirrors_its_flags(st):
    uid = _user(st)
    _sub(st, uid, categories=_cats(breaking=True))
    assert _sub(st, uid, categories=_cats(breaking=False, digests=True))["categories"] == {
        "breaking": False, "digests": True, "recommendations": False, "product": False}


def test_syncing_flags_updates_every_device_a_reader_owns(st):
    """A preference change happens once and must reach every device, or the accelerator drifts from
    the authority it accelerates."""
    uid = _user(st)
    _sub(st, uid, ENDPOINT)
    _sub(st, uid, OTHER)
    assert st.sync_push_subscription_flags(uid, _cats(breaking=True)) == 2
    assert all(s["categories"]["breaking"] for s in st.list_push_subscriptions(uid))


def test_syncing_flags_does_not_reach_another_reader(st):
    alice, bob = _user(st, "alice"), _user(st, "bob")
    _sub(st, alice, ENDPOINT)
    _sub(st, bob, OTHER)
    st.sync_push_subscription_flags(alice, _cats(breaking=True))
    assert st.list_push_subscriptions(bob)[0]["categories"]["breaking"] is False


# --------------------------------------------------------------------------------------------- #
# Disclosure + lifecycle.
# --------------------------------------------------------------------------------------------- #
def test_the_returned_view_never_carries_the_devices_encryption_keys(st):
    """`p256dh`/`auth` are the device's address. The sender reads them from the row; nothing outside
    the store needs them, and a response that carries them is a response that can be logged."""
    uid = _user(st)
    row = _sub(st, uid, p256dh="BSecretPublicKey", auth="SecretAuth")
    assert "p256dh" not in row and "auth" not in row
    assert "BSecretPublicKey" not in str(row) and "SecretAuth" not in str(row)


# --------------------------------------------------------------------------------------------- #
# Concurrency (P3). The lookup is an optimisation; UNIQUE(endpoint) is the arbiter. Two callers
# registering the same NEW endpoint at once is not exotic here — the page's `subscribe()` and the
# service worker's `pushsubscriptionchange` are exactly the pair that can fire together.
# --------------------------------------------------------------------------------------------- #
def test_a_racing_duplicate_registration_does_not_raise(tmp_path, monkeypatch):
    """Deterministic simulation of the race window: a concurrent writer commits this endpoint between
    our pre-check SELECT and our flush. Before P3 the losing insert propagated an IntegrityError and
    the request 500ed; now it retries, takes the update branch, and returns the row."""
    url = f"sqlite:///{tmp_path / 'race.db'}"
    st = store_mod.Store(url)
    uid = _user(st, "racer")
    rival = store_mod.Store(url)             # its own pool -> a genuine second connection
    real_utcnow = store_mod._utcnow
    fired = {"done": False}

    def inject():
        # `_utcnow()` is called once inside the upsert, immediately before the flush — i.e. after the
        # pre-check has already concluded "no such row". Fire the rival exactly there.
        if not fired["done"]:
            fired["done"] = True
            rival.upsert_push_subscription(uid, ENDPOINT, p256dh="BRival", auth="RivalAuth",
                                           user_agent="rival")
        return real_utcnow()

    monkeypatch.setattr(store_mod, "_utcnow", inject)
    row = st.upsert_push_subscription(uid, ENDPOINT, p256dh="BOurs", auth="OursAuth",
                                      user_agent="ours")
    monkeypatch.undo()

    assert fired["done"], "the simulation must actually have injected a racing writer"
    assert row["endpoint"] == ENDPOINT
    assert row["userAgent"] == "ours", "the retry re-applies our update over the winner's row"
    assert len(st.list_push_subscriptions(uid)) == 1, "one endpoint, one row"


def test_concurrent_registrations_of_one_endpoint_yield_one_row(tmp_path):
    """The same thing under real threads: whichever order they interleave in, neither call raises and
    exactly one row exists afterwards."""
    url = f"sqlite:///{tmp_path / 'threads.db'}"
    st = store_mod.Store(url)
    uid = _user(st, "threaded")

    barrier = threading.Barrier(2)
    errors: dict = {}

    def worker(name):
        s = store_mod.Store(url)
        barrier.wait()                       # both threads register together
        try:
            s.upsert_push_subscription(uid, ENDPOINT, p256dh=f"BKey{name}", auth=f"Auth{name}",
                                       user_agent=name)
        except Exception as exc:             # a race loser must retry, never raise
            errors[name] = exc

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == {}
    rows = st.list_push_subscriptions(uid)
    assert len(rows) == 1 and rows[0]["endpoint"] == ENDPOINT


def test_the_upsert_reports_which_of_the_three_cases_happened(st):
    """The caller cannot tell created from updated from reassigned without a second query, and the
    difference is exactly what makes the operational log worth reading — a reassignment means a shared
    browser changed hands."""
    alice, bob = _user(st, "alice"), _user(st, "bob")
    assert _sub(st, alice)["outcome"] == "created"
    assert _sub(st, alice)["outcome"] == "updated"

    handed_over = _sub(st, bob)
    assert handed_over["outcome"] == "reassigned"
    assert handed_over["previousUserId"] == alice


def test_a_long_user_agent_is_truncated_to_the_column(st):
    """The API bounds the `userAgent` FIELD, but its fallback is the raw `User-Agent` header, which
    nothing bounds — so this truncation is the only guard on that path. SQLite ignores VARCHAR limits,
    which is exactly why an unbounded value would pass here and fail on a stricter engine."""
    uid = _user(st)
    row = _sub(st, uid, user_agent="U" * 400)
    assert row["userAgent"] == "U" * 255


def test_push_subscriptions_are_protected_from_retention(st):
    """Removed by exactly one thing, and it is not age: a 410 from the push service. A device the
    reader connected six months ago is still their device."""
    assert "push_subscriptions" in retention_policy.PROTECTED_TABLES
    assert not hasattr(store_mod.Store, "prune_push_subscriptions")


def test_subscriptions_appear_in_storage_stats(st):
    uid = _user(st)
    _sub(st, uid)
    assert st.storage_stats()["rows"]["push_subscriptions"] == 1
