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

from app.admin import strings
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


async def test_compose_file_parses_without_environment_variables():
    """Coolify parses docker-compose.yaml before its env vars are saved.

    A `${VAR:?msg}` guard makes that parse fail outright, so the compose file
    must interpolate cleanly with nothing set. Runtime checks below carry the
    safety instead.
    """
    import re
    from pathlib import Path

    compose = Path(__file__).resolve().parents[1] / "docker-compose.yaml"
    text = compose.read_text()
    guarded = re.findall(r"\$\{[A-Z_]+:\?[^}]*\}", text)
    assert guarded == [], f"compose uses fail-on-missing interpolation: {guarded}"


async def test_missing_bot_token_fails_fast_with_a_clear_message(monkeypatch):
    import app.main as main_module

    settings = get_settings()
    monkeypatch.setattr(settings, "bot_token", "", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        await main_module.run(settings)
    assert "BOT_TOKEN is not set" in str(excinfo.value)


async def test_empty_superadmins_warns_rather_than_crashing(session_factory, monkeypatch):
    """An empty SUPERADMIN_IDS must not stop the bot, but must be loud."""
    import app.main as main_module

    settings = get_settings()
    monkeypatch.setattr(settings, "superadmin_ids", (), raising=False)
    monkeypatch.setattr(settings, "health_port", _free_port(), raising=False)
    monkeypatch.setattr(settings, "health_host", "127.0.0.1", raising=False)

    warnings: list[str] = []

    class SpyLogger:
        def warning(self, event, **kw):
            warnings.append(event)

        def __getattr__(self, name):
            return lambda *a, **k: None

    monkeypatch.setattr(main_module, "logger", SpyLogger())

    started = asyncio.Event()

    class StubBot:
        def __init__(self, *args, **kwargs) -> None:
            self.session = SimpleNamespace(close=self._close)

        async def _close(self) -> None:
            return None

        async def get_me(self):
            return SimpleNamespace(id=999, username="stub_bot")

    class StubDispatcher:
        def __init__(self, *a, **k) -> None:
            self._stop = asyncio.Event()

        def include_router(self, router) -> None:
            return None

        async def start_polling(self, *a, **k) -> None:
            started.set()
            try:
                await self._stop.wait()
            except asyncio.CancelledError:
                pass

        async def stop_polling(self) -> None:
            self._stop.set()

    monkeypatch.setattr(main_module, "Bot", StubBot)
    monkeypatch.setattr(main_module, "build_dispatcher", lambda s: StubDispatcher())
    monkeypatch.setattr(main_module, "init_engine", lambda s: None)
    monkeypatch.setattr(main_module, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(main_module, "dispose_engine", lambda: asyncio.sleep(0))

    task = asyncio.create_task(main_module.run(settings))
    # It must still reach polling: an empty admin list is a warning, not fatal.
    await asyncio.wait_for(started.wait(), timeout=10)
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=10)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    assert "no_super_admins_configured" in warnings


async def test_compose_file_avoids_flow_sequences():
    """Stricter YAML parsers than docker's reject a flow sequence written on
    the line after its key. Coolify reads this file with one of them."""
    import re
    from pathlib import Path

    compose = Path(__file__).resolve().parents[1] / "docker-compose.yaml"
    for number, line in enumerate(compose.read_text().splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert not stripped.startswith("["), (
            f"docker-compose.yaml:{number} starts a flow sequence on its own line"
        )
        assert not re.search(r":\s*\[", line), (
            f"docker-compose.yaml:{number} uses an inline flow sequence"
        )


async def test_compose_file_parses_with_a_strict_yaml_parser():
    """A second opinion from a non-Go parser, mirroring Coolify's."""
    from pathlib import Path

    yaml = pytest.importorskip("yaml")
    compose = Path(__file__).resolve().parents[1] / "docker-compose.yaml"
    parsed = yaml.safe_load(compose.read_text())

    assert set(parsed["services"]) == {"bot", "postgres"}
    assert parsed["services"]["postgres"]["healthcheck"]["test"][0] == "CMD-SHELL"
    # The named volume is what keeps orders across a redeploy.
    assert "postgres_data" in parsed["volumes"]
    assert (
        parsed["services"]["postgres"]["volumes"][0]
        == "postgres_data:/var/lib/postgresql/data"
    )


async def test_malformed_bot_token_exits_with_guidance(monkeypatch):
    """A truncated or quoted paste must not crash-loop with a traceback."""
    import app.main as main_module

    settings = get_settings()
    monkeypatch.setattr(settings, "bot_token", "not-a-real-token", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        await main_module.run(settings)

    message = str(excinfo.value)
    assert "BOT_TOKEN is malformed" in message
    assert "@BotFather" in message


async def test_rejected_bot_token_exits_with_guidance(session_factory, monkeypatch):
    """Correct shape but Telegram answers 401: say so, don't dump a traceback."""
    from aiogram.exceptions import TelegramUnauthorizedError
    from aiogram.methods import GetMe

    import app.main as main_module

    settings = get_settings()
    monkeypatch.setattr(settings, "health_port", _free_port(), raising=False)
    monkeypatch.setattr(settings, "health_host", "127.0.0.1", raising=False)

    class RejectingBot:
        def __init__(self, *args, **kwargs) -> None:
            self.session = SimpleNamespace(close=self._close)

        async def _close(self) -> None:
            return None

        async def get_me(self):
            raise TelegramUnauthorizedError(method=GetMe(), message="Unauthorized")

    class StubDispatcher:
        def include_router(self, router) -> None:
            return None

    monkeypatch.setattr(main_module, "Bot", RejectingBot)
    # root_router can only attach to one Dispatcher per process, and an
    # earlier test already did that.
    monkeypatch.setattr(main_module, "build_dispatcher", lambda s: StubDispatcher())
    monkeypatch.setattr(main_module, "init_engine", lambda s: None)
    monkeypatch.setattr(main_module, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(main_module, "dispose_engine", lambda: asyncio.sleep(0))

    with pytest.raises(SystemExit) as excinfo:
        await main_module.run(settings)

    message = str(excinfo.value)
    assert "rejected by Telegram" in message
    assert "revoked" in message


def real_notifier(services):
    """The production AdminNotifier, wired to the fake gateway."""
    from app.services.notifications import AdminNotifier

    return AdminNotifier(services.session_factory, services.gateway, services.settings)


async def test_startup_notification_lists_what_is_missing(services):
    """A fresh deployment must say plainly that it is not ready yet."""
    gateway = services.gateway
    await real_notifier(services).startup_completed("order_bot", 999)

    assert len(gateway.texts) == 1, "the one seeded super admin should be messaged"
    user_id, text = gateway.texts[0]
    assert user_id == 1000
    assert strings.STARTUP_TITLE in text
    assert "@order_bot" in text
    assert strings.STARTUP_NOT_READY.split("{")[0] in text
    for missing in (
        strings.STARTUP_MISSING_SOURCE,
        strings.STARTUP_MISSING_WORK_GROUP,
        strings.STARTUP_MISSING_ROUTE,
        strings.STARTUP_MISSING_OPERATOR,
    ):
        assert missing in text
    assert strings.STARTUP_SOURCES.format(mark="⚠️", count=strings.fa_digits(0)) in text


async def test_startup_notification_confirms_a_configured_deployment(destinations):
    services = destinations
    gateway = services.gateway
    gateway.reset()

    await real_notifier(services).startup_completed("order_bot", 999)

    _user_id, text = gateway.texts[0]
    assert strings.STARTUP_NOT_READY.split("{")[0] not in text
    assert strings.STARTUP_READY in text
    one = strings.fa_digits(1)
    for template in (
        strings.STARTUP_SOURCES,
        strings.STARTUP_WORK_GROUPS,
        strings.STARTUP_ROUTES,
        strings.STARTUP_OPERATORS,
        strings.STARTUP_SUCCESS_TARGETS,
        strings.STARTUP_FAILURE_TARGETS,
    ):
        assert template.format(mark="✅", count=one) in text


async def test_startup_notification_reaches_every_enabled_admin(services):
    from app.database.repositories import AdminRepository
    from app.utils.enums import AdminRole

    async with session_scope() as session:
        repo = AdminRepository(session)
        await repo.add(2001, AdminRole.ADMIN)
        disabled = await repo.add(2002, AdminRole.ADMIN)
        disabled.enabled = False

    services.gateway.reset()
    await real_notifier(services).startup_completed("order_bot", 999)

    messaged = {user_id for user_id, _text in services.gateway.texts}
    assert messaged == {1000, 2001}, "disabled admins must not be messaged"


async def test_startup_notification_survives_an_unreachable_admin(services):
    """An admin who never opened a chat with the bot must not break startup."""
    services.gateway.failing_chats.add(1000)

    await real_notifier(services).startup_completed("order_bot", 999)  # must not raise

    assert services.gateway.texts == []


async def test_update_ledger_is_pruned_at_startup(session_factory):
    from datetime import timedelta

    import app.main as main_module
    from app.database.models import ProcessedUpdate
    from app.database.repositories import OrderRepository
    from app.utils.time import utcnow
    from sqlalchemy import func, select

    async with session_scope() as session:
        repo = OrderRepository(session)
        await repo.mark_update_processed("update:recent")
        await repo.mark_update_processed("update:stale")

    async with session_scope() as session:
        stale = await session.execute(
            select(ProcessedUpdate).where(ProcessedUpdate.update_key == "update:stale")
        )
        stale.scalar_one().created_at = utcnow() - timedelta(days=30)

    removed = await main_module._purge_old_update_ledger()
    assert removed == 1

    async with session_scope() as session:
        remaining = await session.execute(select(func.count()).select_from(ProcessedUpdate))
        assert remaining.scalar_one() == 1


async def test_compose_pins_the_database_hostname_with_an_alias():
    """DATABASE_URL dials 'postgres'; platforms that rename services break
    that unless the alias is declared explicitly."""
    from pathlib import Path

    yaml = pytest.importorskip("yaml")
    root = Path(__file__).resolve().parents[1]
    parsed = yaml.safe_load((root / "docker-compose.yaml").read_text())

    aliases = parsed["services"]["postgres"]["networks"]["default"]["aliases"]
    # The DSN is assembled by the app from POSTGRES_HOST, not by compose.
    host = parsed["services"]["bot"]["environment"]["POSTGRES_HOST"]

    assert host in aliases, (
        f"the bot dials {host!r} but postgres only answers to {aliases}"
    )
    # Both services must sit on the network that alias belongs to.
    assert "default" in parsed["services"]["bot"]["networks"]
    assert "default" in parsed["networks"]


async def test_bot_command_menu_is_persian():
    """The command list Telegram shows must match the panel's language."""
    from app.admin import strings

    names = [name for name, _description in strings.BOT_COMMANDS]
    assert names == ["start", "order", "id"]
    for _name, description in strings.BOT_COMMANDS:
        assert description, "every command needs a description"
        assert not any(ch.isascii() and ch.isalpha() for ch in description)
        # Telegram rejects command descriptions longer than 256 characters.
        assert len(description) <= 256


async def test_every_user_facing_string_is_persian():
    """Guards against an English sentence creeping back into the panel."""
    import re

    from app.admin import strings

    # Placeholders, HTML tags and slash-commands are structural, not prose.
    structural = re.compile(r"\{[^}]*\}|<[^>]*>|/[a-z_]+")
    words = re.compile(r"[A-Za-z]{2,}")
    #: Literal identifiers an admin must see verbatim to act on them:
    #: environment variables, order-number examples, date format, the
    #: chat_id/message_id argument names, HTML entities, and "done" as a
    #: sample text pattern operators commonly configure.
    allowed = {
        "SUPERADMIN", "IDS", "ORD", "order", "YYYY", "MM", "DD",
        "start", "id", "chat", "message", "done", "lt", "gt",
        # WooCommerce's own dashboard path and field names, which the admin
        # has to follow verbatim in an English admin UI, plus a URL example.
        "WooCommerce", "Settings", "Advanced", "REST", "API",
        "Consumer", "key", "secret", "https", "com", "json", "ck", "cs",
        # WooCommerce order status slugs shown as examples.
        "completed", "processing", "cancelled", "refunded", "failed",
    }

    offenders: list[tuple[str, str]] = []
    for name, value in vars(strings).items():
        # ACCESS_DENIED is deliberately English: see its comment.
        if name.startswith("_") or name in {"BOT_COMMANDS", "ACCESS_DENIED"}:
            continue
        candidates = [value] if isinstance(value, str) else []
        if isinstance(value, dict):
            candidates = [v for v in value.values() if isinstance(v, str)]
        if isinstance(value, list):
            candidates = [
                item[0] for item in value if isinstance(item, tuple) and item
            ]
        for text in candidates:
            for word in words.findall(structural.sub(" ", text)):
                if word not in allowed:
                    offenders.append((name, word))

    assert offenders == [], f"untranslated text: {offenders[:10]}"
