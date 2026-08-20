#!/usr/bin/env bash
# Weekly digest EMAIL delivery pass. DEPLOYMENT-ONLY.
#
#   deploy/ops/send-digest-emails.sh
#
# Install hourly (not weekly) from cron:
#
#   17 * * * * ubuntu /opt/ih/deploy/ops/send-digest-emails.sh 2>&1 | logger -t ih-email
#
# Piped to `logger`, NOT redirected to a file in /var/log. The cron line runs as `ubuntu`, and
# /var/log is root-owned — so `>> /var/log/ih-email.log` fails at the shell redirect, before this
# script is even executed, and the job produces nothing at all. Nothing reports that: cron mails
# the error to a local mailbox nobody reads. `logger` needs no file to exist and no permission to
# create one, and it lands in the journal, which is already this deployment's log store for exactly
# the same durability reason the compose file pins journald for the containers.
#
# Read it with:  journalctl -t ih-email --since today
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

# THE CRON-USER TRAP, and it must be checked FIRST. This runs as `ubuntu` from /etc/cron.d while
# deploy/.env is written by root (configure-email.sh, bootstrap). If the mode does not let the cron
# user read it, every `env_val` returns empty — so the switch below reads as "not enabled" and this
# exits 0 with "nothing to do", hourly, forever. A silent success is the worst possible shape for
# this failure: the weekly digest simply never arrives and the log looks healthy.
if [ ! -r "$ENV_FILE" ]; then
  echo "send-digest-emails: cannot READ ${ENV_FILE} as $(id -un) — every setting reads as empty," >&2
  echo "  so this would otherwise report 'nothing to do' and exit 0 forever. Fix with:" >&2
  echo "    sudo chgrp ubuntu ${ENV_FILE} && sudo chmod 640 ${ENV_FILE}" >&2
  echo "  640 keeps it unreadable to everyone else — it holds NEXTAUTH_SECRET and the SMTP" >&2
  echo "  password. Verify exactly as cron will run it:  sudo -u ubuntu ${0}" >&2
  exit 1
fi

if [ "$(env_val RWE_EMAIL_ENABLED)" != "1" ]; then
  echo "send-digest-emails: RWE_EMAIL_ENABLED is not 1 — nothing to do."
  exit 0
fi

# Driven through the API's own internal route rather than a second process building its own Store:
# one process owns the SQLite write lock, and a worker that opens its own connection alongside the
# server is how a busy-timeout becomes a 500 for a reader mid-request.
secret="$(env_val RWE_INTERNAL_SECRET)"
if [ -z "$secret" ] && grep -qE '^RWE_INTERNAL_SECRET=' "$ENV_FILE" 2>/dev/null; then
  echo "send-digest-emails: RWE_INTERNAL_SECRET is present in ${ENV_FILE} but read back empty." >&2
  echo "  Without it the internal route answers 401 and no digest is mailed." >&2
  echo "  Inspect with: grep -n '^RWE_INTERNAL_SECRET=' ${ENV_FILE}" >&2
  exit 1
fi


if out="$(dc exec -T -e IH_AUTH="$secret" api python examples/email_run.py)"; then
  echo "send-digest-emails: $out"
else
  echo "send-digest-emails: the run failed"
  echo "$out"
  exit 1
fi
