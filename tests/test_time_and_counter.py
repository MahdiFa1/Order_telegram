"""Business-date and counter behaviour (spec tests 90, 91)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.database.engine import session_scope
from app.database.repositories import CounterRepository, OrderRepository
from app.database.repositories.orders import GLOBAL_SCOPE_KEY, scope_key_for
from app.utils.enums import CounterScope
from app.utils.time import business_date, day_bounds_utc
from tests.conftest import SOURCE_CHAT_ID
from tests.helpers import deliver_order, text_payload

pytestmark = pytest.mark.asyncio


async def test_business_date_follows_tehran_wall_clock(frozen_clock):
    tz = get_settings().tz
    frozen_clock.set(datetime(2026, 8, 24, 23, 59, 59, tzinfo=tz))
    assert business_date() == date(2026, 8, 24)

    frozen_clock.set(datetime(2026, 8, 25, 0, 0, 1, tzinfo=tz))
    assert business_date() == date(2026, 8, 25)


async def test_day_bounds_are_local_midnight_not_utc_midnight():
    start, end = day_bounds_utc(date(2026, 8, 24))
    # Tehran is UTC+03:30, so the local day starts at 20:30 UTC the day before.
    assert (start.hour, start.minute) == (20, 30)
    assert (end - start).total_seconds() == 24 * 3600


async def test_counter_resets_on_the_next_business_day(wired, frozen_clock):
    """Spec test 90: order150 at 23:59:59, then order1 at 00:00:01."""
    tz = get_settings().tz
    services = wired

    frozen_clock.set(datetime(2026, 8, 24, 12, 0, tzinfo=tz))
    async with session_scope() as session:
        counter = CounterRepository(session)
        for _ in range(149):
            await counter.allocate(date(2026, 8, 24), GLOBAL_SCOPE_KEY)

    frozen_clock.set(datetime(2026, 8, 24, 23, 59, 59, tzinfo=tz))
    late_order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "late order"))
    async with session_scope() as session:
        late = await OrderRepository(session).get(late_order_id)
    assert late.daily_number == 150
    assert late.display_number == "order150"
    assert late.business_date == date(2026, 8, 24)

    frozen_clock.set(datetime(2026, 8, 25, 0, 0, 1, tzinfo=tz))
    first_order_id = await deliver_order(
        services, text_payload(SOURCE_CHAT_ID, "first of the new day")
    )
    async with session_scope() as session:
        first = await OrderRepository(session).get(first_order_id)
    assert first.daily_number == 1
    assert first.display_number == "order1"
    assert first.business_date == date(2026, 8, 25)


async def test_concurrent_allocation_never_duplicates(db_engine, session_factory):
    """Spec test 91: two simultaneous orders get N and N+1, never N twice."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    day = date(2026, 8, 24)

    async def allocate() -> int:
        async with factory() as session:
            number = await CounterRepository(session).allocate(day, GLOBAL_SCOPE_KEY)
            await session.commit()
            return number

    # Pre-seed to 19 so the contended pair should come out as 20 and 21.
    async with factory() as session:
        counter = CounterRepository(session)
        for _ in range(19):
            await counter.allocate(day, GLOBAL_SCOPE_KEY)
        await session.commit()

    first, second = await asyncio.gather(allocate(), allocate())
    assert {first, second} == {20, 21}


async def test_high_concurrency_allocation_is_gapless_and_unique(db_engine):
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    day = date(2026, 9, 1)

    async def allocate() -> int:
        async with factory() as session:
            number = await CounterRepository(session).allocate(day, GLOBAL_SCOPE_KEY)
            await session.commit()
            return number

    numbers = await asyncio.gather(*(allocate() for _ in range(50)))
    assert sorted(numbers) == list(range(1, 51))


async def test_per_source_scope_numbers_each_source_independently():
    assert scope_key_for(CounterScope.GLOBAL, -100) == GLOBAL_SCOPE_KEY
    assert scope_key_for(CounterScope.PER_SOURCE, -100) == "SOURCE:-100"


async def test_per_source_counter_scope_end_to_end(services, frozen_clock):
    from app.database.repositories import (
        RouteRepository,
        SettingRepository,
        SourceChannelRepository,
        WorkGroupRepository,
    )
    from app.utils.enums import SettingKey
    from tests.conftest import WORK_GROUP_CHAT_ID

    second_source_chat = -1001000000009
    async with session_scope() as session:
        await SettingRepository(session).set(
            SettingKey.COUNTER_SCOPE, CounterScope.PER_SOURCE.value
        )
        sources = SourceChannelRepository(session)
        a = await sources.add(SOURCE_CHAT_ID, "Source A")
        b = await sources.add(second_source_chat, "Source B")
        group = await WorkGroupRepository(session).add(WORK_GROUP_CHAT_ID, "WG")
        routes = RouteRepository(session)
        await routes.add(a.id, group.id)
        await routes.add(b.id, group.id)

    a1 = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "a1"))
    b1 = await deliver_order(services, text_payload(second_source_chat, "b1"))
    a2 = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "a2"))

    async with session_scope() as session:
        repo = OrderRepository(session)
        numbers = {
            "a1": (await repo.get(a1)).daily_number,
            "b1": (await repo.get(b1)).daily_number,
            "a2": (await repo.get(a2)).daily_number,
        }
    assert numbers == {"a1": 1, "b1": 1, "a2": 2}
