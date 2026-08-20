#!/usr/bin/env bash
# Weekly digest EMAIL delivery pass. DEPLOYMENT-ONLY.
#
#   deploy/ops/send-digest-emails.sh
#
# Install hourly (not weekly) from cron:
#
#   17 * * * * ubuntu /opt/ih/deploy/ops/send-digest-emails.sh >> /var/log/ih-email.log 2>&1
#
# HOURLY, for a WEEKLY email, on purpose. The schedule is not this cron: the weekly digest is a
# `cadence` notification deduped on the ISO week (`weekly_digest:2026-W34`), materialised by the
# existing evaluator, and the ledger's UNIQUE(notification_id, channel, subscription_id) is what
# guarantees one email per reader per week. This pass simply mails whatever exists and has not been
# mailed yet, so:
#
#   * running it more often costs nothing and delivers sooner after a digest is materialised;
#   * running it late (a reboot, a deploy, a missed window) loses nothing — the work is still there;
#   * a weekly cron, by contrast, has exactly one chance per week, and a host that happens to be
#     down at that minute silently skips a week for every reader.
#
# It also drives the retry queue, which needs to run on a cadence far shorter than a week for a
# backoff measured in minutes and hours to mean anything.
set -uo pipefail
# shellcheck source=deploy/ops/_compose.sh
source "$(dirname "$0")/_compose.sh"
need_env

if [ "$(env_val RWE_EMAIL_ENABLED)" != "1" ]; then
  echo "send-digest-emails: RWE_EMAIL_ENABLED is not 1 — nothing to do."
  exit 0
fi

# Driven through the API's own internal route rather than a second process building its own Store:
# one process owns the SQLite write lock, and a worker that opens its own connection alongside the
# server is how a busy-timeout becomes a 500 for a reader mid-request.
secret="$(env_val RWE_INTERNAL_SECRET)"

if out="$(dc exec -T -e IH_AUTH="$secret" api python examples/email_run.py)"; then
  echo "send-digest-emails: $out"
else
  echo "send-digest-emails: the run failed"
  echo "$out"
  exit 1
fi
