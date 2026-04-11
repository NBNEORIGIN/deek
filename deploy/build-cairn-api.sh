#!/usr/bin/env bash
# deploy/build-cairn-api.sh
#
# Rebuild the cairn-api Docker image with a guaranteed fresh copy
# of the code layers. Solves the "docker compose build finished in
# 0.5 seconds and the new code isn't inside the container" problem
# that has bitten us repeatedly: BuildKit's content-addressable
# layer cache sometimes serves stale COPY layers after a git pull,
# especially when the host filesystem has unusual mtime semantics
# (bind mounts, network filesystems, etc).
#
# Usage: run from /opt/nbne/cairn/deploy on the Hetzner host, or
# from D:/claw/deploy on the dev box:
#
#   ./build-cairn-api.sh          # build only
#   ./build-cairn-api.sh deploy   # build + recreate container
#   ./build-cairn-api.sh full     # git pull + build + recreate
#
# Sets the GIT_COMMIT build arg to the current repo HEAD, which
# invalidates the cache-busting RUN layer in Dockerfile.api every
# time the commit changes. Layers UP TO that RUN (apt, pip) are
# still cached, so dep rebuilds remain fast.

set -euo pipefail

MODE="${1:-build}"

cd "$(dirname "$0")"
REPO_ROOT=$(cd .. && pwd)
cd "$REPO_ROOT"

if [ "$MODE" = "full" ]; then
  echo "[build-cairn-api] git pull..."
  git pull --ff-only 2>&1 | tail -5 || {
    echo "[build-cairn-api] git pull failed — refusing to build from stale code"
    exit 1
  }
fi

GIT_COMMIT=$(git rev-parse HEAD)
echo "[build-cairn-api] building with GIT_COMMIT=${GIT_COMMIT}"

cd "$REPO_ROOT/deploy"
GIT_COMMIT="$GIT_COMMIT" docker compose build cairn-api

if [ "$MODE" = "deploy" ] || [ "$MODE" = "full" ]; then
  echo "[build-cairn-api] recreating container..."
  docker compose up -d cairn-api --force-recreate
  sleep 10
  echo "[build-cairn-api] verifying build_commit inside container..."
  INSIDE_COMMIT=$(docker exec deploy-cairn-api-1 cat /app/.build_commit 2>/dev/null || echo "unknown")
  echo "[build-cairn-api] container reports: ${INSIDE_COMMIT}"
  if ! echo "$INSIDE_COMMIT" | grep -q "${GIT_COMMIT:0:8}"; then
    echo "[build-cairn-api] WARNING: container git_commit does not match host — cache miss?"
    exit 1
  fi
  echo "[build-cairn-api] health check..."
  curl -sS --max-time 8 -o /dev/null -w "HTTP: %{http_code}\n" \
    http://127.0.0.1:8765/health
fi

echo "[build-cairn-api] done"
