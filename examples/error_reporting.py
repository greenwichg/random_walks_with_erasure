"""Backend exception-reporting abstraction (OBS1) — vendor-agnostic.

The app reports unexpected exceptions through :func:`report_exception`; by default they become a
structured JSON log line (correlatable by ``requestId``) via :class:`LoggingReporter`. A deployment can
call :func:`set_reporter` once at startup to forward exceptions to **Sentry**, **Azure Application
Insights**, **OpenTelemetry**, or a **custom sink** — the application code never names a vendor. Reporting
is best-effort and **never raises into the caller**, so instrumenting a code path can't change its
behavior."""
from __future__ import annotations

import json
import logging
import traceback
from typing import Protocol, runtime_checkable

logger = logging.getLogger("ih.errors")


@runtime_checkable
class ErrorReporter(Protocol):
    """The one method a reporter implements. A Sentry / App Insights / OTel adapter satisfies this."""
    def report(self, exc: BaseException, *, context: dict) -> None: ...


class LoggingReporter:
    """Default reporter: one structured JSON line per exception — type, message, correlation context, and
    a trimmed traceback — on the ``ih.errors`` logger. No external dependency; an aggregator scrapes it."""

    def __init__(self, level: int = logging.ERROR, trace_lines: int = 20) -> None:
        self.level = level
        self.trace_lines = trace_lines

    def report(self, exc: BaseException, *, context: dict) -> None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        tb = "\n".join(tb.splitlines()[-self.trace_lines:])
        payload = {"event": "exception", "error": type(exc).__name__,
                   "message": str(exc)[:500], "traceback": tb}
        payload.update({k: v for k, v in (context or {}).items() if v is not None})
        logger.log(self.level, json.dumps(payload, default=str))


_reporter: ErrorReporter = LoggingReporter()


def set_reporter(reporter: ErrorReporter) -> None:
    """Install the process-wide reporter (call once at startup). Later phases plug a vendor adapter here."""
    global _reporter
    _reporter = reporter


def get_reporter() -> ErrorReporter:
    return _reporter


def report_exception(exc: BaseException, **context) -> None:
    """Report an unexpected exception through the configured reporter. Best-effort — a failure inside the
    reporter is swallowed (and logged) so it can never break request handling."""
    try:
        _reporter.report(exc, context=context)
    except Exception:                       # pragma: no cover — a reporter must never break the caller
        logger.error("error reporter raised", exc_info=True)


def report_message(event: str, **fields) -> None:
    """Report a non-exception operational concern (e.g. an auxiliary subsystem degraded) as a structured
    warning line — the same shape as an exception report, so one aggregator query catches both."""
    try:
        logger.warning(json.dumps({"event": event, **{k: v for k, v in fields.items()
                                                       if v is not None}}, default=str))
    except Exception:                       # pragma: no cover
        pass
