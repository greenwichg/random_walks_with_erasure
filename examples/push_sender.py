"""push_sender.py — the Web Push transport and its failure classification (Phase B2).

Two things live here and nothing else: the call that hands an encrypted payload to a push service,
and the judgement about what its answer means. Everything about *which* notification goes to *which*
device is the worker's (``push_delivery``); everything about what the payload contains is
``push_payload``'s.

**pywebpush is imported lazily, and the transport is injectable.** The library pulls in a native
extension (``http-ece``) that not every environment can build, and the engine must import — and its
whole test suite must run — on a machine without it. So the import happens inside :func:`send`, and
:class:`WebPushSender` takes a ``transport`` callable that the tests replace with a fake. That is
also the better shape regardless: the classification below is the part with the judgement in it, and
it should be testable without a network stack.

**Classification is the whole point of this module.** A send that failed is not one thing:

* ``expired`` — the push service says the subscription is gone (404/410). The device is unreachable
  forever and the row must be removed; anything else means every future fan-out pays to attempt it.
* ``timeout`` — no answer inside the deadline. The notification may or may not have been delivered;
  nothing about the subscription is known to be wrong.
* ``transient`` — 429 or 5xx. The service is unwell, the subscription is fine, and a later attempt
  could succeed.
* ``permanent`` — 400/401/403/413. Something about *our* request is wrong (a malformed payload, a
  VAPID mismatch, a payload over the size limit). Retrying it unchanged cannot help, and each one is
  a defect on our side rather than weather.

Collapsing these to "failed" is what makes a push platform unoperable: a rising ``expired`` rate is
ordinary attrition, a rising ``permanent`` rate is a bug we shipped, and they need opposite responses.
"""

from __future__ import annotations

import dataclasses

#: The statuses a delivery attempt can resolve to. Mirrors `store.NotificationDelivery.status`.
SUCCESS = "success"
EXPIRED = "expired"
TIMEOUT = "timeout"
TRANSIENT = "transient"
PERMANENT = "permanent"

#: Push services report a subscription that no longer exists with one of these. RFC 8030 specifies
#: 404 (unknown) and 410 (gone); both mean the same thing to us — delete the row.
_EXPIRED_CODES = frozenset({404, 410})

#: Worth trying again later, if a later attempt existed. 429 is explicit backpressure; 5xx is the
#: service having a bad time. Neither says anything about the subscription.
_TRANSIENT_CODES = frozenset({429, 500, 502, 503, 504})


@dataclasses.dataclass(frozen=True)
class SendResult:
    """What one attempt resolved to. ``detail`` is short and non-sensitive — a code or an exception
    type, never a response body, because it lands in a log.

    ``retry_after`` is the raw ``Retry-After`` header when the push service sent one, left as the
    string it arrived as. Interpreting it is ``push_retry``'s job, not this module's: both legal forms
    (a delta and an HTTP-date) need a clock, and a transport has no business owning one."""
    status: str
    status_code: "int | None" = None
    detail: str = ""
    retry_after: "str | None" = None

    @property
    def ok(self) -> bool:
        return self.status == SUCCESS

    @property
    def subscription_gone(self) -> bool:
        return self.status == EXPIRED


def classify_status(code: int) -> str:
    """HTTP status → one of the five outcomes. 2xx is success; the rest follow the module docstring.

    An unrecognised code is ``permanent`` rather than ``transient``, deliberately: without retries a
    misclassification either way costs the same delivery, but with retries (a later phase) treating an
    unknown answer as retryable is how a sender starts hammering a service that is telling it no."""
    if 200 <= code < 300:
        return SUCCESS
    if code in _EXPIRED_CODES:
        return EXPIRED
    if code in _TRANSIENT_CODES:
        return TRANSIENT
    return PERMANENT


def classify_exception(exc: BaseException) -> SendResult:
    """A raised transport error → an outcome.

    Matched on the exception's own type name rather than by importing ``requests``: this module must
    load without the push stack installed, and the names are stable across the versions pywebpush
    accepts. A ``WebPushException`` carries the service's response, so its code is used when present.
    """
    code = _status_from_exception(exc)
    if code is not None:
        return SendResult(classify_status(code), code, f"http_{code}",
                          retry_after=_retry_after_from_exception(exc))

    name = type(exc).__name__
    if "Timeout" in name:
        return SendResult(TIMEOUT, None, "timeout")
    if "Connection" in name or "DNS" in name or name == "OSError":
        # The service was unreachable. Not the subscription's fault, and not ours.
        return SendResult(TRANSIENT, None, f"unreachable:{name}")
    return SendResult(PERMANENT, None, f"error:{name}")


def _status_from_exception(exc: BaseException) -> "int | None":
    """The HTTP status a ``WebPushException`` is carrying, if any. pywebpush attaches the ``requests``
    response as ``.response``; older releases only stringify it, so neither is assumed."""
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    return int(code) if isinstance(code, int) else None


def _retry_after_from_exception(exc: BaseException) -> "str | None":
    """The ``Retry-After`` a rejection is carrying, if any. This is the header that matters most on a
    ``429``, and it arrives on the exception rather than on a return value because that is how
    pywebpush reports a non-2xx."""
    return _header(getattr(exc, "response", None), "Retry-After")


def _header(response, name: str) -> "str | None":
    """One header off a response object, case-insensitively, tolerating anything that is not one.

    ``requests`` gives a case-insensitive mapping; a hand-rolled stub or an older release may give a
    plain dict or nothing at all. A header is a nice-to-have on the retry path — failing to read one
    must never cost the classification that surrounds it."""
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    try:
        value = headers.get(name)
        if value is None:
            lowered = name.lower()
            value = next((v for k, v in headers.items() if str(k).lower() == lowered), None)
        return None if value is None else str(value)
    except Exception:                             # noqa: BLE001 — not worth a failed classification
        return None


class WebPushSender:
    """Sends one encrypted payload to one subscription, with a deadline.

    ``transport`` exists so the tests can drive every branch of the classification without a network
    stack or a native extension. Left ``None``, it resolves to :func:`_pywebpush_transport` on first
    use — so production needs no wiring and a machine without the library still imports this module.
    """

    def __init__(self, *, private_key: str, subject: str, timeout: float = 10.0,
                 transport=None) -> None:
        self.private_key = private_key
        self.subject = subject
        self.timeout = timeout
        self._transport = transport

    def send(self, subscription: dict, data: str) -> SendResult:
        """Deliver ``data`` to one device. Never raises: every outcome is a :class:`SendResult`, because
        the caller is a worker pool and one device's failure must not end the fan-out."""
        transport = self._transport or _pywebpush_transport
        info = {
            "endpoint": subscription.get("endpoint"),
            "keys": {"p256dh": subscription.get("p256dh"), "auth": subscription.get("auth")},
        }
        try:
            returned = transport(info, data, self.private_key, self.subject, self.timeout)
        except BaseException as exc:              # noqa: BLE001 — classification IS the handling
            return classify_exception(exc)
        code, retry_after = _code_and_retry_after(returned)
        status = classify_status(code)
        return SendResult(status, code, "" if status == SUCCESS else f"http_{code}",
                          retry_after=retry_after)


def _code_and_retry_after(returned) -> "tuple[int, str | None]":
    """Normalise what a transport returned into ``(status code, Retry-After)``.

    Two shapes are accepted, deliberately. The real transport returns the push service's **response**,
    which is where a ``Retry-After`` on a throttled-but-accepted send would live. A test transport
    returns a bare **int**, because the overwhelming majority of what needs driving here is "answer
    with this status" and making every one of those construct a response object would be noise around
    the thing actually under test.

    Anything else is a bug in the transport rather than an answer from a push service, and
    ``int(...)`` raising is the right outcome — :meth:`WebPushSender.send` catches it and classifies it
    ``permanent``, which is exactly what a broken transport is."""
    if isinstance(returned, int):
        return returned, None
    code = getattr(returned, "status_code", None)
    return int(code if code is not None else returned), _header(returned, "Retry-After")


def _pywebpush_transport(subscription_info: dict, data: str, private_key: str, subject: str,
                         timeout: float):
    """The real call. Imported here so the module — and the test suite — load without the library.

    Returns the **response**, not just its code: a push service may accept a send and still ask for a
    slower rate, and that header is only readable here.

    ``ttl`` bounds how long the push service may hold an undelivered message for an offline device.
    Four hours: long enough to survive a commute or a night's sleep, short enough that a notification
    does not arrive describing something that has stopped being true. It is the transport's echo of the
    per-event ``expires_at`` the platform already applies — and, since B3, of ``push_retry``'s own age
    bound, which is set to the same number so the ladder never outlives the message it is delivering.
    """
    from pywebpush import webpush            # noqa: PLC0415 — deliberate, see the docstring

    return webpush(
        subscription_info=subscription_info,
        data=data,
        vapid_private_key=private_key,
        vapid_claims={"sub": subject},
        ttl=14400,
        timeout=timeout,
    )
