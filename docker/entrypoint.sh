#!/usr/bin/env bash
# Container entrypoint: wait for PostgreSQL, migrate, then exec the bot.
set -euo pipefail

log() { printf '{"level":"info","event":"entrypoint","message":"%s"}\n' "$1"; }

if [ "${RUN_MIGRATIONS_ON_START:-true}" = "true" ]; then
  log "waiting for the database"
  attempt=0
  until python - <<'PY'
import asyncio, socket, sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings

settings = get_settings()


def target() -> tuple[str, int]:
    from sqlalchemy.engine import make_url

    url = make_url(settings.database_url)
    return url.host or "?", url.port or 5432


async def probe() -> None:
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


host, port = target()
try:
    asyncio.run(probe())
except socket.gaierror:
    # The classic symptom of a platform that renamed the database service:
    # the name in DATABASE_URL no longer exists on the network.
    print(
        f"cannot resolve database host '{host}': no such name on this network. "
        f"Check that the postgres service is running and reachable as '{host}'.",
        file=sys.stderr,
    )
    sys.exit(1)
except Exception as error:
    reason = str(error) or type(error).__name__
    if "Name or service not known" in reason or "Temporary failure" in reason:
        print(
            f"cannot resolve database host '{host}': no such name on this network. "
            f"Check that the postgres service is running and reachable as '{host}'.",
            file=sys.stderr,
        )
    else:
        print(f"database at {host}:{port} not ready yet: {reason}", file=sys.stderr)
    sys.exit(1)
PY
  do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then
      log "database never became reachable - see the errors above for the host that failed"
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
