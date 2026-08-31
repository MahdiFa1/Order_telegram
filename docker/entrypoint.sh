#!/usr/bin/env bash
# Container entrypoint: wait for PostgreSQL, migrate, then exec the bot.
set -euo pipefail

log() { printf '{"level":"info","event":"entrypoint","message":"%s"}\n' "$1"; }

if [ "${RUN_MIGRATIONS_ON_START:-true}" = "true" ]; then
  log "waiting for the database"
  attempt=0
  until python - <<'PY'
import asyncio, os, sys

async def probe() -> None:
    from app.config import get_settings
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()

try:
    asyncio.run(probe())
except Exception as error:
    print(error, file=sys.stderr)
    sys.exit(1)
PY
  do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then
      log "database did not become reachable in time"
      exit 1
    fi
    sleep 2
  done

  log "running database migrations"
  # Migrations are additive; no schema is ever dropped, so redeploying
  # never destroys existing orders.
  alembic upgrade head
  log "migrations complete"
fi

log "starting application"
exec "$@"
