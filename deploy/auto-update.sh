#!/usr/bin/env bash
# Pull the newest gallery image, restart, verify health, roll back on failure.
# Run from cron or the bundled systemd timer (deploy/artgallery-update.timer).
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
IMAGE="${GALLERY_IMAGE:?Set GALLERY_IMAGE in .env, e.g. ghcr.io/you/artgallery:latest}"
SERVICE=gallery
log() { echo "[$(date -Is)] $*"; }

old_id=$(docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null || true)
docker compose pull --quiet "$SERVICE"
new_id=$(docker image inspect --format '{{.Id}}' "$IMAGE")

if [ "$old_id" = "$new_id" ]; then
  log "Already up to date ($new_id)."
  exit 0
fi

log "Updating ${old_id:-<none>} -> $new_id"
docker compose up -d --no-build "$SERVICE"

container=$(docker compose ps -q "$SERVICE")
for _ in $(seq 1 45); do
  status=$(docker inspect --format '{{.State.Health.Status}}' "$container" 2>/dev/null || echo starting)
  if [ "$status" = healthy ]; then
    log "Healthy on new image. Cleaning up old images."
    docker image prune -f >/dev/null
    exit 0
  fi
  [ "$status" = unhealthy ] && break
  sleep 2
done

log "New image failed its health check — rolling back."
docker compose logs --tail 50 "$SERVICE" || true
if [ -n "$old_id" ]; then
  docker tag "$old_id" "$IMAGE"
  docker compose up -d --no-build "$SERVICE"
  log "Rolled back to $old_id."
fi
exit 1
