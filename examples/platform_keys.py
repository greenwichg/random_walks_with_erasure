#!/usr/bin/env python3
"""platform_keys.py — tenants, API keys and the meter for the ``/v1`` platform surface.

    python examples/platform_keys.py tenant create acme --name "Acme Corp" [--kind developer]
    python examples/platform_keys.py tenant list
    python examples/platform_keys.py tenant suspend acme  |  tenant activate acme
    python examples/platform_keys.py mint --tenant acme [--plan developer] [--label "ci"]
                                          [--scopes articles:read,stories:read] [--classes metadata_public]
                                          [--rate 60] [--quota 10000] [--expires 2027-01-01T00:00:00+00:00]
    python examples/platform_keys.py list [--tenant acme]
    python examples/platform_keys.py revoke key_…
    python examples/platform_keys.py usage acme [--month 2026-09]

``mint`` prints the plaintext key ONCE, on its own line, and nothing else stores it: the database
holds the SHA-256 (exactly like a reader's extension token). Every other command prints JSON.
``--db`` defaults to ``RWE_DB_URL``. On the production host:

    cd /opt/ih && sudo docker exec -i deploy-api-1 python examples/platform_keys.py mint --tenant acme
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import store  # noqa: E402
from platform_api import metering, plans  # noqa: E402


def _store(args) -> "store.Store":
    return store.Store(args.db or store.default_db_url())


def _csv(raw: "str | None") -> "list | None":
    if raw is None:
        return None
    return [p.strip() for p in raw.split(",") if p.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="SQLAlchemy URL (default: RWE_DB_URL)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("tenant").add_subparsers(dest="tcmd", required=True)
    tc = t.add_parser("create")
    tc.add_argument("tenant_id")
    tc.add_argument("--name", required=True)
    tc.add_argument("--kind", default="developer", choices=("internal", "developer", "enterprise"))
    t.add_parser("list")
    t.add_parser("suspend").add_argument("tenant_id")
    t.add_parser("activate").add_argument("tenant_id")

    m = sub.add_parser("mint")
    m.add_argument("--tenant", required=True)
    m.add_argument("--plan", default="developer", choices=sorted(plans.PLANS))
    m.add_argument("--label", default=None)
    m.add_argument("--scopes", default=None, help="comma-separated; default = the plan's")
    m.add_argument("--classes", default=None, help="comma-separated licence classes; default = the plan's")
    m.add_argument("--rate", type=int, default=None, help="requests per minute; default = the plan's")
    m.add_argument("--quota", type=int, default=None, help="units per month (0 = unlimited); default = the plan's")
    m.add_argument("--expires", default=None, help="ISO timestamp")

    ls = sub.add_parser("list")
    ls.add_argument("--tenant", default=None)
    sub.add_parser("revoke").add_argument("key_id")
    u = sub.add_parser("usage")
    u.add_argument("tenant_id")
    u.add_argument("--month", default=None)

    args = ap.parse_args(argv)
    st = _store(args)

    if args.cmd == "tenant":
        if args.tcmd == "create":
            print(json.dumps(st.platform_create_tenant(args.tenant_id, args.name, kind=args.kind), indent=1))
        elif args.tcmd == "list":
            print(json.dumps(st.platform_list_tenants(), indent=1))
        elif args.tcmd in ("suspend", "activate"):
            ok = st.platform_set_tenant_status(args.tenant_id,
                                               "suspended" if args.tcmd == "suspend" else "active")
            print(json.dumps({"tenantId": args.tenant_id, "updated": ok}))
            return 0 if ok else 1
        return 0
    if args.cmd == "mint":
        scopes = _csv(args.scopes)
        if scopes is None:
            scopes = plans.plan(args.plan)["scopes"]
        unknown = sorted(set(scopes) - set(plans.SCOPES))
        if unknown:
            print(f"unknown scopes: {', '.join(unknown)} (known: {', '.join(plans.SCOPES)})",
                  file=sys.stderr)
            return 2
        try:
            secret, meta = st.platform_mint_key(tenant_id=args.tenant, plan=args.plan,
                                                label=args.label, scopes=scopes,
                                                licence_classes=_csv(args.classes),
                                                rate_per_min=args.rate, quota_month=args.quota,
                                                expires_at=args.expires)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(meta, indent=1), file=sys.stderr)
        print(secret)                       # the ONE time the plaintext exists outside the client
        return 0
    if args.cmd == "list":
        print(json.dumps(st.platform_list_keys(args.tenant), indent=1))
        return 0
    if args.cmd == "revoke":
        ok = st.platform_revoke_key(args.key_id)
        print(json.dumps({"keyId": args.key_id, "revoked": ok}))
        return 0 if ok else 1
    if args.cmd == "usage":
        month = args.month or metering.month_of()
        print(json.dumps({"tenantId": args.tenant_id, "month": month,
                          **st.platform_usage_month(args.tenant_id, month),
                          "daily": st.platform_usage(args.tenant_id, since_day=f"{month}-01")},
                         indent=1))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
