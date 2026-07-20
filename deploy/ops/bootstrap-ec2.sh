#!/usr/bin/env bash
# One-time (idempotent) EC2 host preparation for the Wave 0 deployment. DEPLOYMENT-ONLY.
#
# Safe to re-run on a brand-new Ubuntu 24.04 instance or an existing one — every step is guarded, so
# nothing is duplicated and Docker is restarted only if its config actually changed. Run it from the
# checked-out repo:
#
#   sudo deploy/ops/bootstrap-ec2.sh
#
# It installs Docker + Compose, an AWS CLI, 2 GB swap, container log rotation, creates the host data
# directory, and seeds deploy/.env from the template. It does NOT start the app — run deploy/ops/deploy.sh
# after filling in deploy/.env and pointing DNS at this host.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run with sudo: sudo deploy/ops/bootstrap-ec2.sh" >&2; exit 1; }

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
TARGET_USER="${SUDO_USER:-ubuntu}"
DATA_DIR="${IH_DATA_DIR:-/opt/ih/data}"
step() { printf '\n== %s ==\n' "$1"; }

step "System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y
apt-get install -y ca-certificates curl gnupg unzip

step "Swap (2 GB — OOM insurance for builds on small instances)"
if swapon --show 2>/dev/null | grep -q '/swapfile'; then
  echo "swap already active — skipping"
else
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo "swap enabled"
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab

step "Docker Engine + Compose plugin"
if command -v docker >/dev/null 2>&1; then
  echo "docker already installed: $(docker --version)"
else
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker
# Compose v2.24+ is required for the !reset/!override tags in docker-compose.aws.yml.
cver="$(docker compose version --short 2>/dev/null || echo 0)"
echo "docker compose version: ${cver:-unknown}"
awk -v v="$cver" 'BEGIN{split(v,a,".");exit !((a[1]>2)||(a[1]==2&&a[2]>=24))}' \
  && echo "compose supports !reset/!override ✓" \
  || echo "WARNING: docker compose < 2.24 — the AWS override needs 2.24+ for !reset/!override; upgrade Docker."

step "AWS CLI (off-host S3 backups via the instance IAM role)"
if command -v aws >/dev/null 2>&1; then
  echo "aws already installed: $(aws --version 2>&1)"
else
  apt-get install -y awscli || {
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o /tmp/awscliv2.zip
    unzip -q -o /tmp/awscliv2.zip -d /tmp && /tmp/aws/install --update && rm -rf /tmp/aws /tmp/awscliv2.zip
  }
fi

step "docker group for $TARGET_USER"
if id -nG "$TARGET_USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
  echo "$TARGET_USER already in the docker group"
else
  usermod -aG docker "$TARGET_USER"
  echo "added $TARGET_USER to docker (re-login or 'newgrp docker' to take effect)"
fi

step "Container log rotation (/etc/docker/daemon.json)"
# Caps container log growth (json-file max-size/max-file) so a long-running beta can't fill the 30 GiB
# root volume. deploy/host/daemon.json must be PURE Docker config — no "//" comment keys, or dockerd
# refuses to start ("directives don't match any configuration option"). Alternative: log-driver=awslogs.
SAMPLE="deploy/host/daemon.json"
TARGET="/etc/docker/daemon.json"
if [ ! -f "$TARGET" ]; then
  install -m 644 "$SAMPLE" "$TARGET" && echo "installed $TARGET" && systemctl restart docker
elif cmp -s "$SAMPLE" "$TARGET"; then
  echo "$TARGET already up to date — skipping"
else
  echo "NOTE: $TARGET exists and differs from $SAMPLE — not overwriting."
  echo "      Merge these log-opts in manually, then: sudo systemctl restart docker"
  echo "      Recommended: $(tr -d '\n' < "$SAMPLE")"
fi

step "Host data directory ($DATA_DIR)"
mkdir -p "$DATA_DIR/backups"
chown -R "$TARGET_USER":"$TARGET_USER" "$(dirname "$DATA_DIR")" 2>/dev/null || chown -R "$TARGET_USER":"$TARGET_USER" "$DATA_DIR"
echo "data dir ready (bind-mounted to /app/data)"

step "Environment file (deploy/.env)"
if [ -f deploy/.env ]; then
  echo "deploy/.env already exists — leaving it untouched"
else
  cp deploy/.env.production.example deploy/.env
  chmod 600 deploy/.env
  chown "$TARGET_USER":"$TARGET_USER" deploy/.env
  echo "seeded deploy/.env from the template (chmod 600) — FILL IN THE REQUIRED VALUES"
fi

step "Reboot safety: Docker starts AFTER the data mount"
# If the DB lives on a DEDICATED EBS volume mounted at $DATA_DIR, this makes dockerd (and therefore the
# restart:unless-stopped containers) wait for that mount at boot — so a boot-before-mount race can never
# start the app on an empty root-disk directory. On the default root-EBS layout $DATA_DIR resolves to the
# root mount and this is a harmless no-op.
mkdir -p /etc/systemd/system/docker.service.d
DROPIN=/etc/systemd/system/docker.service.d/10-ih-data-mount.conf
printf '[Unit]\nRequiresMountsFor=%s\n' "$DATA_DIR" > "$DROPIN.tmp"
if ! cmp -s "$DROPIN.tmp" "$DROPIN" 2>/dev/null; then
  mv "$DROPIN.tmp" "$DROPIN" && systemctl daemon-reload
  echo "installed $DROPIN (docker requires the $DATA_DIR mount before start)"
else
  rm -f "$DROPIN.tmp"; echo "$DROPIN already up to date — skipping"
fi

step "Scheduled off-host backup + health monitoring (cron)"
# Off-host S3 backup (hourly, at :23) and health monitor (every 5 min). Both run as $TARGET_USER, which is
# in the docker group, and read deploy/.env for IH_S3_BUCKET / ALERT_WEBHOOK. They no-op quietly until the
# stack is deployed and deploy/.env is filled in.
install -d -m 755 /etc/cron.d
CRON_PATH='PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
{
  echo "# Wave 0 — off-host backup verify + S3 sync, hourly. Installed by bootstrap-ec2.sh."
  echo "$CRON_PATH"
  echo "23 * * * * $TARGET_USER $REPO_ROOT/deploy/ops/backup-offhost.sh >> /var/log/ih-backup.log 2>&1"
} > /etc/cron.d/ih-offhost-backup
{
  echo "# Wave 0 — deployment health monitor, every 5 minutes. Installed by bootstrap-ec2.sh."
  echo "$CRON_PATH"
  echo "*/5 * * * * $TARGET_USER $REPO_ROOT/deploy/ops/monitor.sh >> /var/log/ih-monitor.log 2>&1"
} > /etc/cron.d/ih-monitor
chmod 644 /etc/cron.d/ih-offhost-backup /etc/cron.d/ih-monitor
touch /var/log/ih-backup.log /var/log/ih-monitor.log
chown "$TARGET_USER":"$TARGET_USER" /var/log/ih-backup.log /var/log/ih-monitor.log
echo "cron installed: hourly off-host backup (ih-offhost-backup), 5-min health monitor (ih-monitor)"

cat <<EOF

✅ bootstrap complete (idempotent — safe to re-run).
   Installed: Docker+Compose, AWS CLI, swap, log rotation, data dir, docker-after-mount ordering,
   and cron for off-host S3 backup (hourly) + health monitoring (5-min). The crons stay quiet until the
   stack is deployed and deploy/.env is filled.

Next:
  1. Edit deploy/.env — set the REQUIRED secrets, APP_DOMAIN, NEXTAUTH_URL, Google OAuth, BETA_ALLOWLIST,
     IH_S3_BUCKET (off-host backups), ALERT_WEBHOOK (monitoring).  (openssl rand -base64 32 for secrets.)
     • Data on the ROOT EBS volume (default): leave IH_DATA_MOUNT=0.
     • Data on a DEDICATED EBS volume: mount it at $DATA_DIR via /etc/fstab (add 'nofail'), then set
       IH_DATA_MOUNT=1 so deploy refuses to start if the volume isn't mounted (prevents an empty DB).
  2. Point Route 53 A records (apex + www) for your domain at this host's Elastic IP, and confirm:
        dig +short <your-domain>
  3. Deploy:   deploy/ops/deploy.sh
EOF
