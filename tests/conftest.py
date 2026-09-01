"""Test fixtures.

The suite runs against a REAL PostgreSQL database, because the guarantees
under test -- atomic counter allocation, partial unique indexes, conditional
``UPDATE ... RETURNING`` claims -- are database behaviour, not Python
behaviour. Point ``TEST_DATABASE_URL`` at a scratch database.
"""

from __future__ import annotations

import os
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("SUPERADMIN_IDS", "1000")
os.environ.setdefault("TZ", "Asia/Tehran")
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://postgres@127.0.0.1:55432/order_bot_test",
    ),
)

from app.config import get_settings  # noqa: E402
from app.database import engine as engine_module  # noqa: E402
from app.database.engine import session_scope  # noqa: E402
from app.database.models import Base  # noqa: E402
from app.database.repositories import (  # noqa: E402
    AcknowledgementRepository,
    OperatorRepository,
    ResultDestinationRepository,
    RouteRepository,
    RuleRepository,
    SourceChannelRepository,
    WorkGroupRepository,
)
from app.services.bootstrap import bootstrap  # noqa: E402
from app.services.container import Services  # noqa: E402
from app.acknowledgements.service import AcknowledgementService  # noqa: E402
from app.dispatch.service import DispatchService  # noqa: E402
from app.dispatch.store import StoreDispatchService  # noqa: E402
from app.orders.service import OrderService  # noqa: E402
from app.reports.service import ReportService  # noqa: E402
from app.services.finalizer import OrderFinalizer  # noqa: E402
from app.services.signals import SignalService  # noqa: E402
from app.services.source_reactions import SourceReactionService  # noqa: E402
from app.utils.enums import (  # noqa: E402
    AcknowledgementTargetMode,
    DispatchPolicy,
    MatchMode,
    OrderStatus,
    RuleMode,
    SignalKey,
)
from tests.fakes import (  # noqa: E402
    FakeGateway,
    FakeWooCommerceClient,
    RecordingNotifier,
)

SOURCE_CHAT_ID = -1001000000001
WORK_GROUP_CHAT_ID = -1002000000002
SUCCESS_CHAT_ID = -1003000000003
FAILURE_CHAT_ID = -1004000000004
OPERATOR_ID = 555000001
STRANGER_ID = 555000999


@pytest_asyncio.fixture(scope="session")
async def db_engine():
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine):
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    engine_module._engine = db_engine
    engine_module.set_session_factory(factory)

    # A truncate between tests is much faster than recreating the schema and
    # still resets every sequence, which matters for the counter tests.
    table_names = ", ".join(f'"{name}"' for name in Base.metadata.tables)
    async with factory() as session:
        await session.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
        await session.commit()
    yield factory


@pytest.fixture
def gateway() -> FakeGateway:
    FakeWooCommerceClient.reset()
    return FakeGateway()


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


@pytest_asyncio.fixture
async def services(session_factory, gateway, notifier) -> Services:
    settings = get_settings()
    source_reactions = SourceReactionService(session_factory, gateway, settings)
    orders = OrderService(session_factory, gateway, settings, source_reactions)
    dispatch = DispatchService(session_factory, gateway, settings, notifier)
    acknowledgements = AcknowledgementService(session_factory, gateway, settings, notifier)
    store = StoreDispatchService(
        session_factory, settings, notifier, client_factory=FakeWooCommerceClient
    )
    finalizer = OrderFinalizer(
        session_factory,
        dispatch,
        acknowledgements,
        settings,
        notifier,
        store=store,
        source_reactions=source_reactions,
    )
    container = Services(
        settings=settings,
        session_factory=session_factory,
        gateway=gateway,
        notifier=notifier,
        orders=orders,
        dispatch=dispatch,
        acknowledgements=acknowledgements,
        finalizer=finalizer,
        signals=SignalService(session_factory, finalizer),
        reports=ReportService(session_factory),
        bot_user_id=42,
        store=store,
        source_reactions=source_reactions,
    )
    await bootstrap(session_factory, settings)
    return container


@pytest_asyncio.fixture
async def wired(services):
    """A complete, realistic configuration: source → work group, operator, rules."""
    async with session_scope() as session:
        source = await SourceChannelRepository(session).add(
            SOURCE_CHAT_ID, "Orders Source"
        )
        group = await WorkGroupRepository(session).add(WORK_GROUP_CHAT_ID, "Work Group 1")
        await RouteRepository(session).add(source.id, group.id)
        await OperatorRepository(session).add(OPERATOR_ID, display_name="Operator One")
    return services


@pytest_asyncio.fixture
async def destinations(wired):
    async with session_scope() as session:
        repo = ResultDestinationRepository(session)
        await repo.add(OrderStatus.SUCCESS, SUCCESS_CHAT_ID, "Successful Orders")
        await repo.add(OrderStatus.FAILED, FAILURE_CHAT_ID, "Failed Orders")
    return wired


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------
async def configure_rule(
    status: OrderStatus,
    *,
    mode: RuleMode = RuleMode.ANY,
    signals: tuple[SignalKey, ...] = (),
    texts: tuple[str, ...] = (),
    match_mode: MatchMode = MatchMode.CONTAINS,
    reactions: tuple[str, ...] = (),
    enabled: bool = True,
) -> None:
    async with session_scope() as session:
        repo = RuleRepository(session)
        await repo.set_enabled(status, enabled)
        for signal in SignalKey:
            await repo.set_signal_enabled(status, signal, signal in signals)
        await repo.set_mode(status, mode)
        for pattern in texts:
            await repo.add_text_pattern(status, pattern, match_mode)
        for emoji in reactions:
            await repo.add_reaction(status, emoji)


async def configure_acknowledgement(
    status: OrderStatus,
    *,
    enabled: bool = True,
    reaction: str = "✅",
    target_mode: AcknowledgementTargetMode = AcknowledgementTargetMode.SMART,
    policy: DispatchPolicy = DispatchPolicy.ALL_REQUIRED_DESTINATIONS,
    retry_enabled: bool = True,
    max_retry_count: int = 3,
) -> None:
    async with session_scope() as session:
        await AcknowledgementRepository(session).update_config(
            status,
            enabled=enabled,
            reaction_value=reaction,
            target_mode=target_mode,
            dispatch_policy=policy,
            retry_enabled=retry_enabled,
            max_retry_count=max_retry_count,
        )


@pytest.fixture
def frozen_clock(monkeypatch):
    """Controls the wall clock used to derive the business date."""

    class Clock:
        def __init__(self) -> None:
            self.value = datetime(2026, 8, 24, 12, 0, tzinfo=get_settings().tz)

        def set(self, moment: datetime) -> None:
            self.value = moment

    clock = Clock()
    monkeypatch.setattr("app.utils.time.local_now", lambda: clock.value)
    return clock
