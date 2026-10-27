#!/usr/bin/env bash
# Deploy the current branch, then prove it works - and roll back if it does not.
#
#   ./deploy.sh            # pull, recreate, verify
#   SKIP_PULL=1 ./deploy.sh
#
# Why this exists: `docker compose up -d` succeeding only means a container was
# created. The site can still be unreachable - a Caddyfile with a syntax error makes
# Caddy exit immediately, and a bind mount whose source was missing leaves Caddy with
# no config at all. Both look like "deployed" and both produce ERR_CONNECTION_REFUSED
# in a browser. So this waits for the health endpoint *through Caddy* and reverts the
# commit if it never answers.
set -euo pipefail

cd "$(dirname "$0")"
BRANCH="${BRANCH:-main}"

ADMIN_DOMAIN="${ADMIN_DOMAIN:-}"
if [ -z "$ADMIN_DOMAIN" ] && [ -f .env ]; then
  ADMIN_DOMAIN="$(grep -E '^ADMIN_DOMAIN=' .env | head -1 | cut -d= -f2- | tr -d '"' || true)"
fi
ADMIN_DOMAIN="${ADMIN_DOMAIN:-localhost}"

if [ "${SKIP_PULL:-0}" != "1" ]; then
  echo "== pulling $BRANCH"
  git pull --ff-only origin "$BRANCH"
fi

# Name the build in the container, so /api/version reports the commit that is actually
# running rather than whatever happens to be checked out later. The version counter lives
# on this server (and in .gitignore) because it describes a deploy, not the code: keeping
# it in git would mean every deploy creates a commit here, which is what diverged the
# branch and blocked every later pull.
bump_version() {
  current="1.00"
  [ -f .build-version ] && current="$(tr -d '[:space:]' < .build-version)"
  major="${current%%.*}"
  minor="${current##*.}"
  minor=$((10#$minor + 1))
  if [ "$minor" -ge 100 ]; then
    major=$((major + 1))
    minor=0
  fi
  printf -v next "%s.%02d" "$major" "$minor"
  echo "$next" > .build-version
  echo "$next"
}

export APP_VERSION="$(bump_version)"
export GIT_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
export BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "== Version $APP_VERSION ($GIT_COMMIT) at $BUILD_TIME"

echo "== recreating caddy, app and worker (--build: the code is baked into the image, so a"
echo "   plain recreate would happily serve the previous build; --progress=plain so a long"
echo "   pip install shows its steps instead of a spinner)"
docker compose up -d --build --progress=plain --force-recreate caddy app worker

echo "== waiting for https://$ADMIN_DOMAIN/api/health"
for attempt in $(seq 1 30); do
  if curl -fsS -o /dev/null --max-time 5 "https://$ADMIN_DOMAIN/api/health"; then
    echo "deploy OK - $ADMIN_DOMAIN answers, ports and TLS fine"
    docker compose ps
    exit 0
  fi
  sleep 2
done

echo "!! $ADMIN_DOMAIN does not answer after 60s - last caddy lines:" >&2
docker compose logs --tail=40 caddy >&2 || true
echo "!! rolling back to the previous commit" >&2
git revert --no-edit HEAD
docker compose up -d --build --force-recreate caddy app worker
echo "!! rolled back; the site should be serving the previous version" >&2
exit 1
