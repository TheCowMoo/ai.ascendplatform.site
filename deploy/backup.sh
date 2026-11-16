#!/usr/bin/env bash
# Nightly backup for the production Compose stack.
#
#   BACKUP_PASSPHRASE='a long secret' ./deploy/backup.sh
#
# It archives the Postgres database *and* the files that are not in Postgres:
# generated media, published sites, Caddy vhosts, the database-backed configuration
# file and the session key. The archive is encrypted before it leaves the server.
#
# Suggested cron (as root):
#   15 2 * * * cd /opt/ascendplatform && BACKUP_PASSPHRASE='...' \
#       BACKUP_DIR=/var/backups/ascendplatform ./deploy/backup.sh >>/var/log/ascend-backup.log 2>&1
#
# Set BACKUP_ALLOW_UNENCRYPTED=1 only for a local test. Never use that for a client's
# data: the archive contains .env and the database.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/ascendplatform}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="ascendplatform-$STAMP"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/ascend-backup.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

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
if [ -z "${BACKUP_PASSPHRASE:-}" ] && [ -n "${BACKUP_PASSPHRASE_FILE:-}" ]; then
  BACKUP_PASSPHRASE="$(cat "$BACKUP_PASSPHRASE_FILE")"
  export BACKUP_PASSPHRASE
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
umask 077

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required" >&2
  exit 2
fi
if [ -n "${BACKUP_PASSPHRASE:-}" ] && ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required to encrypt the backup" >&2
  exit 2
fi
if ! docker compose ps --status running --services 2>/dev/null | grep -qx db; then
  echo "the db service is not running; refusing to make a partial backup" >&2
  exit 2
fi

echo "== dumping PostgreSQL ($POSTGRES_DB)"
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$TMP/db.dump"

# The app may be a local SQLite checkout; keep that path too when it exists.
mkdir -p artifacts sites sites-conf
paths=(artifacts sites sites-conf Caddyfile)
for extra in data .env .secret_key; do
  [ -e "$extra" ] && paths+=("$extra")
done

echo "== archiving files"
tar -czf "$TMP/archive.tar.gz" -C "$TMP" db.dump -C "$ROOT" "${paths[@]}"

archive="$BACKUP_DIR/$NAME.tar.gz"
mv "$TMP/archive.tar.gz" "$archive"

if [ -n "${BACKUP_PASSPHRASE:-}" ]; then
  echo "== encrypting"
  if ! openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 \
      -pass env:BACKUP_PASSPHRASE -in "$archive" -out "$archive.enc"; then
    rm -f "$archive" "$archive.enc"
    echo "encryption failed; no unencrypted archive was left behind" >&2
    exit 4
  fi
  rm -f "$archive"
  archive="$archive.enc"
elif [ "${BACKUP_ALLOW_UNENCRYPTED:-0}" = "1" ]; then
  echo "!! writing an UNENCRYPTED archive because BACKUP_ALLOW_UNENCRYPTED=1" >&2
else
  rm -f "$archive"
  echo "BACKUP_PASSPHRASE is required (or set BACKUP_ALLOW_UNENCRYPTED=1 for a local test)" >&2
  exit 3
fi

( cd "$BACKUP_DIR" && sha256sum "$(basename "$archive")" > "$(basename "$archive").sha256" )
chmod 600 "$archive" "$archive.sha256"

# Keep the newest backups. Only files made by this script are touched.
find "$BACKUP_DIR" -maxdepth 1 -type f \
  \( -name 'ascendplatform-*.tar.gz' -o -name 'ascendplatform-*.tar.gz.enc' \
     -o -name 'ascendplatform-*.sha256' \) \
  -mtime +"$RETENTION_DAYS" -delete

echo "backup: $archive"
echo "sha256: $archive.sha256"
