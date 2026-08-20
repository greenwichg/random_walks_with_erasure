#!/usr/bin/env bash
# Weekly digest email — configuration preflight. SENDS NOTHING. DEPLOYMENT-ONLY.
#
#   bash deploy/ops/check-email.sh
#
# Run it after every change to the email block in deploy/.env, and before believing a run that
# reported `sent: 0`. Every way this configuration can be wrong looks identical from outside — a
# wrong app password, an unverified From, an empty allowlist, and simply nobody having opted in all
# produce the same silence. This separates them.
#
# Runs INSIDE the api container on purpose: the question is what that process can see, and an
# answer derived from the host's shell would be about a different environment. `deploy/.env` is
# read by compose, not by this script, so a variable missing from the compose allowlist shows up
# here as unset — which is exactly the failure to catch.
set -uo pipefail
# shellcheck source=deploy/ops/_compose.sh
source "$(dirname "$0")/_compose.sh"
need_env

dc exec -T api python examples/email_preflight.py
status=$?

if [ "$status" -ne 0 ]; then
  echo
  echo "check-email: not ready — fix the items above, then:"
  echo "  bash deploy/ops/restart.sh api      # deploy/.env is read at container start"
  echo "  bash deploy/ops/check-email.sh      # and check again"
fi
exit "$status"
