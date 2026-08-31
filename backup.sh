#!/usr/bin/env bash
# PostgreSQL dump into ./backups/YYYY-MM-DD_HH-MM-SS.sql.gz
#
# Restore:
#   gunzip -c backups/<file>.sql.gz | docker compose exec -T postgres \
#       psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN=$'\033[32m'; RESET=$'\033[0m'
info() { echo "${GREEN}==>${RESET} $*"; }

compose() {
  if docker compose version >/dev/null 2>&1; then docker compose "$@"
  else docker-compose "$@"; fi
}

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

DB_NAME="${POSTGRES_DB:-telegram_orders}"
DB_USER="${POSTGRES_USER:-telegram_orders}"
BACKUP_DIR="${BACKUP_DIR:-backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y-%m-%d_%H-%M-%S)"
TARGET="${BACKUP_DIR}/${STAMP}.sql.gz"

info "Dumping database '${DB_NAME}' to ${TARGET}"
compose exec -T postgres pg_dump -U "$DB_USER" -d "$DB_NAME" --clean --if-exists \
  | gzip -9 > "$TARGET"

if [ ! -s "$TARGET" ]; then
  echo "Backup is empty — removing it." >&2
  rm -f "$TARGET"
  exit 1
fi

info "Backup written: $(du -h "$TARGET" | cut -f1)"

info "Pruning backups older than ${RETENTION_DAYS} days"
find "$BACKUP_DIR" -name '*.sql.gz' -type f -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null || true

info "Done. Restore with:"
echo "  gunzip -c ${TARGET} | docker compose exec -T postgres psql -U ${DB_USER} -d ${DB_NAME}"
