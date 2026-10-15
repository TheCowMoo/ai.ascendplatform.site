#!/usr/bin/env bash
#
# Install AscendPlatform on an Ubuntu VPS (Docker + Caddy).
#
# Caddy obtains and renews TLS certificates automatically, so there is NO
# HestiaCP, no nginx and no certbot to configure. Just point your domain's
# A/AAAA record at this server, then run this script.
#
# Usage (from the repo root, as root):
#   sudo ADMIN_DOMAIN=ai.example.com bash deploy/install.sh
#
# Environment overrides (all optional, prompts fill the gaps):
#   APP_DIR, ADMIN_DOMAIN, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
#   DEEPSEEK_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, PEXELS_API_KEY
#   ADMIN_EMAIL, ADMIN_PASSWORD

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/ascendplatform}"
ADMIN_DOMAIN="${ADMIN_DOMAIN:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: This script must be run as root (use sudo)." >&2
  exit 1
fi

echo "==> Installing AscendPlatform into $APP_DIR"

# 1. Docker Engine + compose plugin
export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installing Docker"
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  ARCH="$(dpkg --print-architecture)"
  CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"
  echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $CODENAME stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi

# rsync copies the app below, and is not present on every minimal image.
command -v rsync >/dev/null 2>&1 || { apt-get update -y && apt-get install -y rsync; }

# 2. Application files
#
# The whole tree is copied, minus local state. An explicit file list was tried
# first and silently went stale: asgi.py (the container's entrypoint), tools/,
# settings.py, agents.py, actions.py, worker.py and the settings migration were
# all missing from it, so the app and worker containers could never start.
mkdir -p "$APP_DIR"
if [ "$REPO_ROOT" = "$APP_DIR" ]; then
  echo "==> Running from $APP_DIR itself; nothing to copy"
else
  echo "==> Copying application files into $APP_DIR"
  rsync -a \
    --exclude 'venv' --exclude '.venv' --exclude '__pycache__' \
    --exclude '*.pyc' --exclude '.git' --exclude '.antigravity' \
    --exclude 'data' --exclude 'artifacts' --exclude 'sites' \
    --exclude 'sites-conf' --exclude '.env' --exclude '.secret_key' \
    "$REPO_ROOT"/ "$APP_DIR"/
fi

# Directories the app writes into. They are host-mounted into a container running
# as uid 10001, so leaving them root-owned would make image generation, Pexels
# downloads and Caddy vhost writes fail with PermissionError.
mkdir -p "$APP_DIR/artifacts" "$APP_DIR/sites" "$APP_DIR/sites-conf"
chown -R 10001:10001 "$APP_DIR/artifacts" "$APP_DIR/sites" "$APP_DIR/sites-conf"

# 3. Environment file (.env) — read by both the app (env_file) and compose
ENV_FILE="$APP_DIR/.env"
touch "$ENV_FILE"

ensure_env() {  # key, prompt, default, current_value
  local key="$1" prompt="$2" default="$3" current="${4:-}"
  if ! grep -q "^${key}=" "$ENV_FILE"; then
    local val="$current"
    if [ -z "$val" ]; then
      if [ -n "$default" ]; then
        read -r -p "$prompt [$default]: " val
        val="${val:-$default}"
      else
        read -r -p "$prompt: " val
      fi
    fi
    echo "$key=$val" >> "$ENV_FILE"
  fi
}

random_hex() {  # bytes
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$1"
  else
    head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

ensure_env BRAND_NAME "Product name" "AscendPlatform" "${BRAND_NAME:-}"
ensure_env ADMIN_DOMAIN "Public domain (its A record must point here)" "" "$ADMIN_DOMAIN"
ensure_env POSTGRES_DB "Postgres database" "ascendplatform" "${POSTGRES_DB:-}"
ensure_env POSTGRES_USER "Postgres user" "ascendplatform" "${POSTGRES_USER:-}"
ensure_env POSTGRES_PASSWORD "Postgres password" "$(random_hex 16)" "${POSTGRES_PASSWORD:-}"
ensure_env DEEPSEEK_API_KEY "DeepSeek API key" "" "${DEEPSEEK_API_KEY:-}"
ensure_env ANTHROPIC_API_KEY "Anthropic (Claude) API key (optional)" "" "${ANTHROPIC_API_KEY:-}"
ensure_env GEMINI_API_KEY "Google Gemini API key (optional)" "" "${GEMINI_API_KEY:-}"
ensure_env PEXELS_API_KEY "Pexels API key (optional, for stock photos)" "" "${PEXELS_API_KEY:-}"
ensure_env ADMIN_EMAIL "Admin login email" "admin@example.com" "${ADMIN_EMAIL:-}"
ensure_env ADMIN_PASSWORD "Admin password" "$(random_hex 8)" "${ADMIN_PASSWORD:-}"
ensure_env SECRET_KEY "Session secret" "$(random_hex 32)" "${SECRET_KEY:-}"
chmod 600 "$ENV_FILE"

# 4. Build + start. Caddy provisions TLS automatically; the app container runs
#    `alembic upgrade head` before uvicorn starts.
cd "$APP_DIR"
docker compose pull db redis caddy
docker compose up -d --build

echo "==> Done!"
echo "    Admin panel:  https://${ADMIN_DOMAIN:-<your-domain>}/admin/"
echo "    Health check: https://${ADMIN_DOMAIN:-<your-domain>}/api/health"
echo "    Logs:         cd $APP_DIR && docker compose logs -f app"
echo "    Note:         DNS for ${ADMIN_DOMAIN:-<your-domain>} must resolve here for TLS."
