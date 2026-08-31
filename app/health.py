"""Internal health endpoint.

Listens on a private port inside the container; no public domain or TLS is
required. Coolify / Docker health checks hit ``/health``.
"""

from __future__ import annotations

import time

from aiohttp import web
from sqlalchemy import text

from app.database.engine import get_engine
from app.utils.logging import get_logger

logger = get_logger(__name__)

_STARTED_AT = time.monotonic()


class HealthServer:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.bot_ready = False
        self._runner: web.AppRunner | None = None

    def _build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/health", self.handle_health)
        app.router.add_get("/health/live", self.handle_live)
        app.router.add_get("/", self.handle_health)
        return app

    async def handle_live(self, _request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def handle_health(self, _request: web.Request) -> web.Response:
        database_ok = await self._check_database()
        healthy = database_ok and self.bot_ready
        payload = {
            "status": "ok" if healthy else "degraded",
            "application": "ok",
            "database": "ok" if database_ok else "error",
            "telegram_bot": "ok" if self.bot_ready else "initialising",
            "uptime_seconds": round(time.monotonic() - _STARTED_AT, 1),
        }
        return web.json_response(payload, status=200 if healthy else 503)

    async def _check_database(self) -> bool:
        try:
            engine = get_engine()
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception as error:  # noqa: BLE001 - reported, never raised
            logger.warning("health_database_check_failed", error=str(error))
            return False

    async def start(self) -> None:
        self._runner = web.AppRunner(self._build_app(), access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info("health_server_started", host=self.host, port=self.port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            logger.info("health_server_stopped")
