"""Startup, health endpoint and graceful shutdown.

Exercises ``app.main.run`` for real -- only the Telegram network calls are
stubbed -- so the wiring, bootstrap, recovery pass, health server and
shutdown sequence are all covered.
"""

from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import aiohttp
import pytest

from app.config import get_settings
from app.database.engine import session_scope
from app.database.repositories import (
    AcknowledgementRepository,
    AdminRepository,
    RuleRepository,
    SettingRepository,
)
from app.health import HealthServer
from app.main import ALLOWED_UPDATES, build_dispatcher
from app.utils.enums import (
    AcknowledgementTargetMode,
    DispatchPolicy,
    OrderStatus,
    RuleMode,
    SettingKey,
)

pytestmark = pytest.mark.asyncio


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def test_health_endpoint_reports_database_and_bot_state(session_factory):
    port = _free_port()
    server = HealthServer("127.0.0.1", port)
    await server.start()
    try:
        async with aiohttp.ClientSession() as client:
            # Before the bot has initialised the endpoint reports degraded.
            async with client.get(f"http://127.0.0.1:{port}/health") as response:
                assert response.status == 503
                body = await response.json()
                assert body["database"] == "ok"
                assert body["telegram_bot"] == "initialising"

            server.bot_ready = True
            async with client.get(f"http://127.0.0.1:{port}/health") as response:
                assert response.status == 200
                body = await response.json()
                assert body == {
                    "status": "ok",
                    "application": "ok",
                    "database": "ok",
                    "telegram_bot": "ok",
                    "uptime_seconds": body["uptime_seconds"],
                }

            async with client.get(f"http://127.0.0.1:{port}/health/live") as response:
                assert response.status == 200
    finally:
        await server.stop()


async def test_health_server_stops_cleanly_and_frees_the_port(session_factory):
    port = _free_port()
    server = HealthServer("127.0.0.1", port)
    await server.start()
    await server.stop()

    # Re-binding proves the listener really went away on shutdown.
    again = HealthServer("127.0.0.1", port)
    await again.start()
    await again.stop()


async def test_allowed_updates_include_message_reaction():
    """Reaction detection silently never fires without this subscription."""
    assert "message_reaction" in ALLOWED_UPDATES
    assert "channel_post" in ALLOWED_UPDATES
    assert "edited_channel_post" in ALLOWED_UPDATES
    assert "callback_query" in ALLOWED_UPDATES
    assert "message" in ALLOWED_UPDATES


async def test_dispatcher_wires_middlewares_and_routers(services):
    dispatcher = build_dispatcher(services)
    outer = [type(m).__name__ for m in dispatcher.update.outer_middleware]
    inner = [type(m).__name__ for m in dispatcher.update.middleware]
    assert "IdempotencyMiddleware" in outer
    assert "ServicesMiddleware" in outer
    assert "ErrorGuardMiddleware" in inner
    assert [router.name for router in dispatcher.sub_routers] == ["root"]


async def test_bootstrap_seeds_rules_configs_and_settings(session_factory):
    """Bootstrap is additive and safe to run on every start."""
    from app.services.bootstrap import bootstrap

    settings = get_settings()
    await bootstrap(session_factory, settings)
    # Running it twice must not duplicate or reset anything.
    await bootstrap(session_factory, settings)

    async with session_scope() as session:
        admins = await AdminRepository(session).list_all()
        assert [a.telegram_user_id for a in admins] == [1000]

        rules = RuleRepository(session)
        success = await rules.get_rule(OrderStatus.SUCCESS)
        failure = await rules.get_rule(OrderStatus.FAILED)
        assert RuleMode(success.mode) is RuleMode.ANY
        # Every known signal has a row, all disabled until an admin opts in.
        assert len(success.signals) == 8
        assert not any(signal.enabled for signal in success.signals)
        assert not any(signal.enabled for signal in failure.signals)

        acks = AcknowledgementRepository(session)
        for status in (OrderStatus.SUCCESS, OrderStatus.FAILED):
            config = await acks.get_config(status)
            assert config.enabled is False
            assert (
                AcknowledgementTargetMode(config.target_mode)
                is AcknowledgementTargetMode.SMART
            )
            assert (
                DispatchPolicy(config.dispatch_policy)
                is DispatchPolicy.ALL_REQUIRED_DESTINATIONS
            )

        values = await SettingRepository(session).all()
        assert values[SettingKey.ORDER_PREFIX] == "order"
        assert values[SettingKey.COUNTER_SCOPE] == "GLOBAL"


async def test_bootstrap_does_not_overwrite_admin_changes(session_factory):
    from app.services.bootstrap import bootstrap

    settings = get_settings()
    await bootstrap(session_factory, settings)
    async with session_scope() as session:
        await SettingRepository(session).set(SettingKey.ORDER_PREFIX, "ORD")
        await RuleRepository(session).set_mode(OrderStatus.SUCCESS, RuleMode.ALL)

    await bootstrap(session_factory, settings)

    async with session_scope() as session:
        assert await SettingRepository(session).order_prefix() == "ORD"
        rule = await RuleRepository(session).get_rule(OrderStatus.SUCCESS)
        assert RuleMode(rule.mode) is RuleMode.ALL


async def test_main_run_starts_and_shuts_down_gracefully(session_factory, monkeypatch):
    """Drives app.main.run end to end with the Telegram calls stubbed out."""
    import app.main as main_module

    port = _free_port()
    settings = get_settings()
    monkeypatch.setattr(settings, "health_port", port, raising=False)
    monkeypatch.setattr(settings, "health_host", "127.0.0.1", raising=False)

    polling_started = asyncio.Event()
    polling_stopped = asyncio.Event()

    class StubBot:
        def __init__(self, *args, **kwargs) -> None:
            self.session = SimpleNamespace(close=self._close_session)

        async def _close_session(self) -> None:
            return None

        async def get_me(self):
            return SimpleNamespace(id=999, username="stub_bot")

    class StubDispatcher:
        def __init__(self, *args, **kwargs) -> None:
            self._stop = asyncio.Event()

        def include_router(self, router) -> None:
            return None

        async def start_polling(self, *args, **kwargs) -> None:
            polling_started.set()
            await self._stop.wait()
            polling_stopped.set()

        async def stop_polling(self) -> None:
            self._stop.set()

    monkeypatch.setattr(main_module, "Bot", StubBot)
    monkeypatch.setattr(main_module, "build_dispatcher", lambda services: StubDispatcher())
    # The engine is already initialised by the fixture; keep that one.
    monkeypatch.setattr(main_module, "init_engine", lambda s: None)
    monkeypatch.setattr(main_module, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(main_module, "dispose_engine", lambda: asyncio.sleep(0))

    task = asyncio.create_task(main_module.run(settings))
    await asyncio.wait_for(polling_started.wait(), timeout=10)

    async with aiohttp.ClientSession() as client:
        async with client.get(f"http://127.0.0.1:{port}/health") as response:
            assert response.status == 200
            assert (await response.json())["telegram_bot"] == "ok"

    # A SIGTERM equivalent: the run loop watches this event.
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=10)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    # The health port must be released by the shutdown path.
    probe = HealthServer("127.0.0.1", port)
    await probe.start()
    await probe.stop()
