#!/usr/bin/env bash
# Scheduled source discovery: seed candidates, probe a bounded batch. DEPLOYMENT-ONLY.
#
#   deploy/ops/discover-sources.sh
#
# Install from cron (installed by bootstrap-ec2.sh; no-ops until RWE_DISCOVERY_CRON=1):
#
#   41 * * * * ubuntu /opt/ih/deploy/ops/discover-sources.sh 2>&1 | logger -t ih-discover
#
# Piped to `logger`, NOT redirected into /var/log. The cron line runs as `ubuntu` and /var/log is
# root-owned, so `>> /var/log/ih-discover.log` fails at the shell redirect before this script runs
# at all, and cron mails the error to a mailbox nobody reads. The same trap `send-digest-emails.sh`
# documents; `logger` needs no file and no permission, and lands in the journal this deployment
# already treats as its log store.
#
#   Read it with:  journalctl -t ih-discover --since today
#
# ## What this DOES and does NOT do
#
#   seed    discover candidates and register them. Offline for `catalogue`; one search request per
#           query for `web`.
#   probe   contact publishers to validate a BOUNDED batch. Resumable, and it never re-asks a host
#           that has been answered.
#
#   admit   NEVER. Admission is what changes the partition readers see, and this repository does
#           not make partition changes without a human — `store.admit_source` refuses one on live
#           rows without an explicit acknowledgement, and a cron cannot give one. So this pass fills
#           the queue and validates it; a person decides what serves.
#
# ## Why hourly, and why bounded rather than exhaustive
#
# Hourly because the work is idempotent and resumable: `probe` skips every host already answered, so
# running often costs nothing extra and a missed hour loses nothing. Bounded because the cost is
# other people's bandwidth — RWE_DISCOVERY_PROBE_LIMIT hosts x ~3 requests per run, stated here
# rather than discovered from a log. An exhaustive nightly pass would put the whole candidate queue
# on one publisher-facing burst, which is the shape the politeness ceiling exists to prevent.
set -uo pipefail
# shellcheck source=deploy/ops/_compose.sh
source "$(dirname "$0")/_compose.sh"

# THE CRON-USER TRAP, checked FIRST. This runs as `ubuntu` from /etc/cron.d while deploy/.env is
# written by root. If the mode does not let the cron user read it, every `env_val` returns empty —
# so the switch below reads as "not enabled" and this exits 0 with "nothing to do", hourly, forever.
# A silent success is the worst shape for this failure: discovery simply never runs and the log
# looks healthy.
if [ ! -r "$ENV_FILE" ]; then
  echo "discover-sources: cannot READ ${ENV_FILE} as $(id -un) — every setting reads as empty," >&2
  echo "  so this would otherwise report 'nothing to do' and exit 0 forever. Fix with:" >&2
  echo "    sudo chgrp ubuntu ${ENV_FILE} && sudo chmod 640 ${ENV_FILE}" >&2
  echo "  Verify exactly as cron will run it:  sudo -u ubuntu ${0}" >&2
  exit 1
fi

need_env

if [ "$(env_val RWE_DISCOVERY_CRON)" != "1" ]; then
  echo "discover-sources: RWE_DISCOVERY_CRON is not 1 — nothing to do."
  exit 0
fi

DB="$(env_val RWE_DB_URL)"
DB="${DB:-sqlite:////app/data/ih_beta.db}"
PROBE_LIMIT="$(env_val RWE_DISCOVERY_PROBE_LIMIT)"; PROBE_LIMIT="${PROBE_LIMIT:-20}"
WEB_LIMIT="$(env_val RWE_DISCOVERY_WEB_LIMIT)";     WEB_LIMIT="${WEB_LIMIT:-20}"
CHANNELS="$(env_val RWE_DISCOVERY_CHANNELS)";       CHANNELS="${CHANNELS:-catalogue}"

# One pass at a time. `probe`'s per-host claim already makes overlapping runs safe — that is what
# the `probing` state is for — but two passes still double the outbound rate a publisher sees, and
# the claim cannot know about a run that has not reached its host yet. The lock is about the RATE,
# not about correctness, which is why it is here and not in the campaign.
LOCK=/tmp/ih-discover.lock
exec 9>"$LOCK" || { echo "discover-sources: cannot open $LOCK" >&2; exit 1; }
if ! flock -n 9; then
  echo "discover-sources: another pass is still running — skipping this hour."
  exit 0
fi

function campaign() {
  dc run --rm -T api python examples/source_campaign.py "$@" --db "$DB"
}

echo "== discover-sources: channels=[${CHANNELS}] probeLimit=${PROBE_LIMIT} =="

for chan in ${CHANNELS//,/ }; do
  case "$chan" in
    catalogue)
      echo "-- seed: catalogue (offline; no request leaves this host) --"
      campaign seed --channel catalogue || echo "discover-sources: catalogue seed failed" >&2
      ;;
    web)
      # `--search` is what turns a plan into a request, and it is only passed when a provider is
      # actually configured. A half-configured provider must not produce an hourly failed search.
      if [ -z "$(env_val RWE_WEB_SEARCH_PROVIDER)$(env_val RWE_WEB_SEARCH_ENDPOINT)" ]; then
        echo "-- seed: web SKIPPED — no search provider configured --"
        continue
      fi
      echo "-- seed: web (one search request per query) --"
      campaign seed --channel web --search --limit "$WEB_LIMIT" \
        || echo "discover-sources: web seed failed" >&2
      ;;
    *)
      # `directory` is deliberately not schedulable: it imports a named file, so there is nothing
      # for a recurring job to do until a human supplies a new register.
      echo "discover-sources: channel '${chan}' is not schedulable — skipping" >&2
      ;;
  esac
done

echo "-- probe: at most ${PROBE_LIMIT} host(s), ~3 requests each --"
campaign probe --limit "$PROBE_LIMIT" || echo "discover-sources: probe failed" >&2

echo "-- status --"
campaign status

echo "== discover-sources: done. NOTHING WAS ADMITTED — that is a human decision. =="
echo "   Review and admit with:"
echo "     dc run --rm -T api python examples/source_campaign.py admit --all-validated --tier B --db \"\$RWE_DB_URL\""
