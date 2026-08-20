#!/usr/bin/env python3
"""Trigger one weekly-digest email pass through the running API. Operator entry point.

Calls the API's own internal route rather than building a second ``Store``: one process owns the
SQLite write lock, and a worker opening its own connection alongside the server is how a busy
timeout becomes a 500 for a reader mid-request. Prints the run's stats as JSON so a cron log says
what happened — "sent 0" is indistinguishable from a broken worker until it can say WHY.

    dc exec -T api python examples/email_run.py
"""

import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("IH_EMAIL_RUN_URL") or "http://127.0.0.1:8000/api/internal/email/digest-run"


def main() -> int:
    req = urllib.request.Request(URL, method="POST", data=b"",
                                 headers={"Content-Type": "application/json"})
    secret = (os.environ.get("IH_AUTH") or os.environ.get("RWE_INTERNAL_SECRET") or "").strip()
    if secret:
        req.add_header("X-IH-Auth", secret)
    # No proxy handler, explicitly: this is a call to our own container's loopback, and a
    # HTTP_PROXY inherited from the environment would send it out to the internet and back (or,
    # more likely, to a 403 that reads like the API is down).
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=600) as resp:
            print(json.dumps(json.load(resp), sort_keys=True))
        return 0
    except urllib.error.HTTPError as exc:
        print(f"digest-run failed: HTTP {exc.code} {exc.read()[:200]!r}", file=sys.stderr)
        return 1
    except Exception as exc:                      # noqa: BLE001 — a cron job reports, never traces
        print(f"digest-run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
