#!/usr/bin/env bash
# Restore one archive made by deploy/backup.sh.
#
#   RESTORE_CONFIRM=yes BACKUP_PASSPHRASE='...' ./deploy/restore.sh /var/backups/ascendplatform/ascendplatform-....tar.gz.enc
#
# This is deliberately fail-closed: it checks the checksum, stops the writers, restores
# the database and files, then brings the stack back and checks the public health URL.
# If anything fails the stack is left stopped; inspect the error before starting it.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
FILE="${1:-}"
if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
  echo "usage: RESTORE_CONFIRM=yes ./deploy/restore.sh <backup.tar.gz[.enc]>" >&2
  exit 2
fi
if [ "${RESTORE_CONFIRM:-}" != "yes" ]; then
  echo "refusing to overwrite live data without RESTORE_CONFIRM=yes" >&2
  exit 2
fi
if ! command -v docker >/dev/null 2>&1 || ! command -v rsync >/dev/null 2>&1; then
  echo "docker and rsync are required" >&2
  exit 2
fi

env_value() {
  local key="$1"
  if [ -f .env ]; then
    grep -E "^${key}=" .env | tail -1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//' || true
  fi
}
POSTGRES_USER="${POSTGRES_USER:-$(env_value POSTGRES_USER)}"
POSTGRES_DB="${POSTGRES_DB:-$(env_value POSTGRES_DB)}"
POSTGRES_USER="${POSTGRES_USER:-ascendplatform}"
POSTGRES_DB="${POSTGRES_DB:-ascendplatform}"
ADMIN_DOMAIN="${ADMIN_DOMAIN:-$(env_value ADMIN_DOMAIN)}"
ADMIN_DOMAIN="${ADMIN_DOMAIN:-localhost}"
if [ -z "${BACKUP_PASSPHRASE:-}" ] && [ -n "${BACKUP_PASSPHRASE_FILE:-}" ]; then
  BACKUP_PASSPHRASE="$(cat "$BACKUP_PASSPHRASE_FILE")"
  export BACKUP_PASSPHRASE
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/ascend-restore.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

if [ -f "$FILE.sha256" ]; then
  echo "== verifying checksum"
  ( cd "$(dirname "$FILE")" && sha256sum -c "$(basename "$FILE").sha256" )
fi

if [[ "$FILE" == *.enc ]]; then
  if [ -z "${BACKUP_PASSPHRASE:-}" ]; then
    echo "BACKUP_PASSPHRASE is required for an encrypted archive" >&2
    exit 2
  fi
  echo "== decrypting"
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
    -pass env:BACKUP_PASSPHRASE -in "$FILE" -out "$TMP/archive.tar.gz"
else
  cp "$FILE" "$TMP/archive.tar.gz"
fi

echo "== extracting"
tar -xzf "$TMP/archive.tar.gz" -C "$TMP"
if [ ! -f "$TMP/db.dump" ]; then
  echo "the archive has no db.dump; refusing to continue" >&2
  exit 2
fi

echo "== stopping writers (db stays up)"
docker compose stop app worker caddy
trap 'rm -rf "$TMP"' EXIT

echo "== restoring PostgreSQL ($POSTGRES_DB)"
docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  --clean --if-exists --no-owner --no-privileges < "$TMP/db.dump"

echo "== restoring client files"
for dir in artifacts sites sites-conf data; do
  if [ -d "$TMP/$dir" ]; then
    mkdir -p "$ROOT/$dir"
    rsync -a --delete "$TMP/$dir/" "$ROOT/$dir/"
  fi
done
for file in Caddyfile .env .secret_key; do
  if [ -f "$TMP/$file" ]; then
    cp -p "$TMP/$file" "$ROOT/$file"
  fi
done

echo "== starting the stack"
docker compose up -d
for attempt in $(seq 1 30); do
  if curl -fsS -o /dev/null --max-time 5 "https://$ADMIN_DOMAIN/api/health"; then
    echo "restore OK - https://$ADMIN_DOMAIN/api/health answers"
    exit 0
  fi
  sleep 2
done

echo "!! restore finished but the public health check did not answer" >&2
docker compose logs --tail=50 caddy app worker >&2 || true
echo "!! the stack is running; do not accept writes until the failure is understood" >&2
exit 1
