#!/usr/bin/env bash
# Safe update: pull, rebuild, migrate, restart.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
info() { echo "${GREEN}==>${RESET} $*"; }
warn() { echo "${YELLOW}==>${RESET} $*"; }

compose() {
  if docker compose version >/dev/null 2>&1; then docker compose "$@"
  else docker-compose "$@"; fi
}

[ -f .env ] || { echo ".env not found. Run install.sh first."; exit 1; }

if [ "${SKIP_BACKUP:-false}" != "true" ]; then
  info "Taking a safety backup before updating"
  bash backup.sh || warn "Backup failed — continuing anyway (set SKIP_BACKUP=true to silence)."
fi

info "Pulling the latest code"
if [ -d .git ]; then
  git pull --ff-only
else
  warn "Not a git checkout; skipping git pull."
fi

info "Rebuilding images"
compose build

info "Ensuring PostgreSQL is up"
compose up -d postgres
for _ in $(seq 1 60); do
  if compose ps postgres | grep -qi healthy; then break; fi
  sleep 2
done

info "Applying database migrations"
# Runs in a one-off container so a failed migration never leaves a
# half-started bot behind.
compose run --rm bot alembic upgrade head

info "Restarting the bot"
compose up -d bot

sleep 4
info "Status"
compose ps
compose logs --tail 20 bot || true
info "Update complete"
