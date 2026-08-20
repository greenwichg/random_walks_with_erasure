#!/usr/bin/env bash
# Write the weekly-digest email block into deploy/.env. DEPLOYMENT-ONLY.
#
#   bash deploy/ops/configure-email.sh you@gmail.com
#   bash deploy/ops/configure-email.sh you@gmail.com --dry-run
#   bash deploy/ops/configure-email.sh you@gmail.com --replace
#   bash deploy/ops/configure-email.sh digest@hidden-view.com --allowlist '*' \
#        --host email-smtp.eu-west-1.amazonaws.com --user AKIA... --name 'Hidden View'
#
# WHY THIS EXISTS RATHER THAN `nano deploy/.env`.
#
# Appending by hand is how BETA_ALLOWLIST lost the operator's own address: three lines of the same
# key, only the last one effective, the owner locked out of their own beta (2026-08-02). Compose
# keeps the LAST occurrence of a key and silently ignores every earlier one, so a second edit — or
# a re-run of the same paste — looks additive and is destructive. This script refuses to create a
# duplicate at all; `--replace` rewrites in place instead of appending a shadowing line.
#
# It also does three things a hand-edit reliably gets wrong: it generates RWE_EMAIL_SECRET rather
# than leaving a placeholder that silently disables sending, it quotes values containing spaces or
# `#` (an unquoted `#` truncates the rest of the line — that is a 16-character app password turning
# into 4), and it never puts the password in your shell history or on screen.
set -uo pipefail
# shellcheck source=deploy/ops/_compose.sh
source "$(dirname "$0")/_compose.sh"

ADDRESS=""; ALLOWLIST=""; ALLOWLIST_SET=0; HOST="smtp.gmail.com"; PORT="587"
USER_=""; NAME="Hidden View"
PUBLIC_URL=""; DRY=0; REPLACE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)   DRY=1 ;;
    --replace)   REPLACE=1 ;;
    --allowlist) ALLOWLIST="${2:-}"; ALLOWLIST_SET=1; shift ;;
    --host)      HOST="${2:-}"; shift ;;
    --port)      PORT="${2:-}"; shift ;;
    --user)      USER_="${2:-}"; shift ;;
    --name)      NAME="${2:-}"; shift ;;
    --url)       PUBLIC_URL="${2:-}"; shift ;;
    -h|--help)   sed -n '2,25p' "$0"; exit 0 ;;
    -*)          echo "unknown option: $1" >&2; exit 2 ;;
    *)           ADDRESS="$1" ;;
  esac
  shift
done

if [ -z "$ADDRESS" ]; then
  echo "usage: bash deploy/ops/configure-email.sh <from-address> [--dry-run] [--replace]" >&2
  echo "       the address is the mailbox digests are sent FROM, and (by default) the only" >&2
  echo "       address cleared to receive them." >&2
  exit 2
fi
case "$ADDRESS" in *@*.*) ;; *) echo "not an email address: $ADDRESS" >&2; exit 2 ;; esac

need_env
[ -n "$USER_" ] || USER_="$ADDRESS"

# WHO MAY RECEIVE IS NOT DERIVED FROM WHO IS SENDING — except on a first run, where "send from and
# to yourself" is what a beta means and is the only sane guess.
#
# On a --replace it must be KEPT, for the same reason RWE_EMAIL_SECRET is kept: it is a deliberate
# operator decision that this command is not being asked to revisit. Deriving it would make the
# domain cutover — `configure-email.sh digest@hidden-view.com --replace` — silently narrow the
# allowlist to the SENDER's address, which belongs to no reader at all. Every send would then skip
# as `not-in-allowlist` and the operator would be debugging a silent nothing immediately after a
# migration, with the allowlist the last place anyone would look.
if [ "$ALLOWLIST_SET" -eq 0 ]; then
  ALLOWLIST="$(env_val RWE_EMAIL_ALLOWLIST)"
  if [ -n "$ALLOWLIST" ]; then
    echo "keeping the existing RWE_EMAIL_ALLOWLIST (${ALLOWLIST}) — who may RECEIVE is a separate" >&2
    echo "  decision from who sends. Pass --allowlist to change it." >&2
  else
    ALLOWLIST="$ADDRESS"
  fi
fi
# Default the public URL to the site the web tier already knows it serves, rather than inventing
# one: an unsubscribe link on the wrong host is a link that 404s in someone's mail client.
[ -n "$PUBLIC_URL" ] || PUBLIC_URL="$(env_val NEXTAUTH_URL)"
[ -n "$PUBLIC_URL" ] || PUBLIC_URL="https://hidden-view.com"

# --- the password: never an argument, so it cannot reach the shell history or a process list ----
PASSWORD="${RWE_SMTP_PASSWORD_INPUT:-}"
if [ -z "$PASSWORD" ]; then
  if [ -t 0 ]; then
    # DISCARD TYPE-AHEAD FIRST. A hidden prompt swallows whatever is already sitting in the
    # terminal's input buffer, invisibly. Paste a three-line block — the command, then a restart,
    # then a check — and line 2 silently becomes the "password": the config is written, the script
    # reports success, and the only clue is a character count nobody has a reference for. Observed
    # exactly that, with `bash deploy/ops/restart.sh api` stored as an SES credential.
    while read -r -t 0 2>/dev/null; do read -r _ 2>/dev/null || break; done
    printf 'App password for %s (input hidden; paste and press Enter): ' "$USER_" >&2
    read -rs PASSWORD; echo >&2
  else
    echo "no TTY: pass the password as RWE_SMTP_PASSWORD_INPUT=... for non-interactive use" >&2
    exit 2
  fi
fi
# Gmail displays app passwords in four groups of four. Both forms authenticate; the spaces are
# presentation. Stripping them removes the quoting question entirely.
PASSWORD="$(printf '%s' "$PASSWORD" | tr -d '[:space:]')"
if [ -z "$PASSWORD" ]; then
  echo "empty password — nothing written" >&2; exit 2
fi
# Second net, because a flush cannot catch input that arrives mid-prompt. No credential this script
# accepts can contain these: a Gmail app password is 16 lowercase letters, and an SES SMTP password
# is base64 (A-Za-z0-9+/=), which has no "." and no spaces. A shell command has both.
case "$PASSWORD" in
  *.sh*|bash*|sudo*|cd/*|*deploy/ops*)
    echo "REFUSING: that does not look like a password — it looks like a shell command:" >&2
    echo "    ${PASSWORD:0:24}…" >&2
    echo "  A pasted multi-line block feeds its later lines into this prompt as type-ahead." >&2
    echo "  Run the command on its own, and type or paste ONLY the password at the prompt." >&2
    exit 2 ;;
esac
if [ "$HOST" = "smtp.gmail.com" ] && [ "${#PASSWORD}" -ne 16 ]; then
  echo "NOTE: a Gmail app password is 16 characters; got ${#PASSWORD}. If authentication fails," >&2
  echo "      that length is the first thing to check (your Google password is not an app password)." >&2
fi

SECRET="$(env_val RWE_EMAIL_SECRET)"
if [ -z "$SECRET" ]; then
  SECRET="$(openssl rand -base64 32 2>/dev/null)" || SECRET=""
  [ -n "$SECRET" ] || { echo "could not generate RWE_EMAIL_SECRET (no openssl)" >&2; exit 1; }
  echo "generated a new RWE_EMAIL_SECRET (32 random bytes)" >&2
else
  echo "keeping the existing RWE_EMAIL_SECRET — rotating it would invalidate every unsubscribe" >&2
  echo "  link already sitting in someone's inbox." >&2
fi

# --- render ------------------------------------------------------------------------------------
# Quote only when the value needs it. An unquoted `#` starts a comment and eats the rest of the
# line; a bare space is fine for compose but not for every reader of this file, so quote that too.
function render() {
  case "$2" in
    *[\ \#\"\']*) printf '%s="%s"\n' "$1" "$2" ;;
    *)            printf '%s=%s\n'   "$1" "$2" ;;
  esac
}

KEYS=(RWE_EMAIL_ENABLED RWE_SMTP_HOST RWE_SMTP_PORT RWE_SMTP_USER RWE_SMTP_PASSWORD
      RWE_EMAIL_FROM RWE_EMAIL_ALLOWLIST RWE_EMAIL_SECRET RWE_PUBLIC_URL)

BLOCK="$(
  echo "# --- weekly digest email (deploy/ops/configure-email.sh, $(date -u +%Y-%m-%dT%H:%MZ)) ---"
  render RWE_EMAIL_ENABLED   "1"
  render RWE_SMTP_HOST       "$HOST"
  render RWE_SMTP_PORT       "$PORT"
  render RWE_SMTP_USER       "$USER_"
  render RWE_SMTP_PASSWORD   "$PASSWORD"
  render RWE_EMAIL_FROM      "$NAME <$ADDRESS>"
  render RWE_EMAIL_ALLOWLIST "$ALLOWLIST"
  render RWE_EMAIL_SECRET    "$SECRET"
  render RWE_PUBLIC_URL      "$PUBLIC_URL"
)"

# What the operator sees. The password is a length, never a value: this output gets pasted.
function redacted() {
  printf '%s\n' "$BLOCK" | sed -E "s|^(RWE_SMTP_PASSWORD=).*|\1<${#PASSWORD} characters, hidden>|; \
                                   s|^(RWE_EMAIL_SECRET=).*|\1<32 random bytes, hidden>|"
}

echo
echo "would write to ${ENV_FILE}:"
echo
redacted | sed 's/^/    /'
echo

# --- the duplicate check, which is the whole point ---------------------------------------------
EXISTING=()
for k in "${KEYS[@]}"; do
  if grep -qE "^${k}=" "$ENV_FILE" 2>/dev/null; then EXISTING+=("$k"); fi
done

if [ "${#EXISTING[@]}" -gt 0 ] && [ "$REPLACE" -eq 0 ]; then
  echo "REFUSING: ${#EXISTING[@]} of these keys are already in ${ENV_FILE}:" >&2
  for k in "${EXISTING[@]}"; do
    echo "    ${k} — $(grep -cE "^${k}=" "$ENV_FILE") line(s)" >&2
  done
  echo >&2
  echo "  Appending would ADD a second line for each. Compose keeps only the LAST occurrence and" >&2
  echo "  silently ignores the earlier ones — which is how BETA_ALLOWLIST lost your own address" >&2
  echo "  and locked you out (2026-08-02). Nothing has been written." >&2
  echo >&2
  echo "  Re-run with --replace to rewrite those keys in place instead of appending." >&2
  exit 1
fi

if [ "$DRY" -eq 1 ]; then
  echo "--dry-run: nothing written."
  exit 0
fi

# --- write -------------------------------------------------------------------------------------
BACKUP="${ENV_FILE}.$(date -u +%Y%m%dT%H%M%SZ).bak"
cp -p "$ENV_FILE" "$BACKUP" || { echo "could not back up ${ENV_FILE}" >&2; exit 1; }
chmod 600 "$BACKUP" 2>/dev/null || true
echo "backed up to ${BACKUP}"

TMP="$(mktemp)" || exit 1
trap 'rm -f "$TMP"' EXIT
# Drop any existing line for the keys we are setting (awk, not sed: no escaping questions about
# values containing / or &), then append the block. With --replace this is a rewrite; without it,
# the refusal above already guaranteed there is nothing to drop.
awk -v keys="${KEYS[*]}" '
  BEGIN { n = split(keys, a, " "); for (i = 1; i <= n; i++) drop[a[i]] = 1 }
  /^[A-Za-z_][A-Za-z0-9_]*=/ {
    split($0, kv, "=");
    if (kv[1] in drop) next
  }
  { print }
' "$ENV_FILE" > "$TMP" || { echo "rewrite failed; ${ENV_FILE} untouched" >&2; exit 1; }

printf '\n%s\n' "$BLOCK" >> "$TMP"
cat "$TMP" > "$ENV_FILE" || { echo "write failed; restore with: cp ${BACKUP} ${ENV_FILE}" >&2; exit 1; }
echo "wrote ${#KEYS[@]} keys to ${ENV_FILE}"

# Prove it, rather than assert it.
DUPS="$(env_dup_keys)"
if [ -n "$DUPS" ]; then
  echo >&2
  echo "WARNING: ${ENV_FILE} still has duplicate keys (pre-existing, not from this run):" >&2
  printf '    %s\n' $DUPS >&2
  echo "  Collapse each to one line — compose ignores all but the last." >&2
fi

echo
echo "next:"
echo "  sudo bash deploy/ops/update.sh claude/sleepy-gates-oecof1   # deploy the code + new env"
echo "  bash deploy/ops/check-email.sh                              # dials the relay, sends nothing"
echo
echo "Then turn YOUR OWN toggle on: Settings -> Notifications -> Weekly digest -> 'Also email it"
echo "to me'. The allowlist is your permission as operator; that toggle is your consent as a"
echo "reader, and both are required."
