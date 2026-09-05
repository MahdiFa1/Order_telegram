"""Forum topics, per-source result destinations and the startup backlog guard."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.database.engine import session_scope
from app.database.repositories import (
    ResultDestinationRepository,
    SettingRepository,
    SourceChannelRepository,
    WorkGroupRepository,
)
from app.orders import startup_guard
from app.utils.enums import OrderStatus, SettingKey, SignalKey, StartupBacklogMode

from tests.conftest import (
    FAILURE_CHAT_ID,
    SOURCE_CHAT_ID,
    SUCCESS_CHAT_ID,
    OPERATOR_ID,
    WORK_GROUP_CHAT_ID,
    configure_rule,
)
from tests.helpers import (
    deliver_order,
    get_order,
    operator_replies,
    photo_payload,
    text_payload,
)

pytestmark = pytest.mark.asyncio

SECOND_SOURCE_CHAT_ID = -1005000000005
SOURCE_TOPIC = 25
GROUP_TOPIC = 7
DEST_TOPIC = 12


async def _configure_success_on_text(services):
    await configure_rule(
        OrderStatus.SUCCESS, signals=(SignalKey.REPLY_TEXT,), texts=("done",)
    )


# ---------------------------------------------------------------------------
# Topics on the way in
# ---------------------------------------------------------------------------
async def test_a_source_bound_to_a_topic_ignores_the_rest_of_the_group(wired):
    """A group registered per topic must not swallow the whole group."""
    async with session_scope() as session:
        sources = SourceChannelRepository(session)
        # Replace the chat-wide source with one scoped to a single topic.
        existing = await sources.get_exact(SOURCE_CHAT_ID, 0)
        await sources.delete(existing.id)
        await sources.add(SOURCE_CHAT_ID, "Topic source", topic_id=SOURCE_TOPIC)

    async with session_scope() as session:
        sources = SourceChannelRepository(session)
        assert (
            await sources.get_enabled_by_chat_id(SOURCE_CHAT_ID, SOURCE_TOPIC)
        ) is not None
        # Another topic in the same group, and the group's main view, are not
        # this source.
        assert await sources.get_enabled_by_chat_id(SOURCE_CHAT_ID, 99) is None
        assert await sources.get_enabled_by_chat_id(SOURCE_CHAT_ID, 0) is None


async def test_a_chat_wide_source_still_accepts_every_topic(wired):
    """Registering the group alone keeps working once topics are enabled."""
    async with session_scope() as session:
        sources = SourceChannelRepository(session)
        for topic in (0, 3, 250):
            found = await sources.get_enabled_by_chat_id(SOURCE_CHAT_ID, topic)
            assert found is not None, topic


async def test_the_same_group_can_feed_two_sources_through_two_topics(wired):
    async with session_scope() as session:
        sources = SourceChannelRepository(session)
        a = await sources.add(SOURCE_CHAT_ID, "Topic A", topic_id=11)
        b = await sources.add(SOURCE_CHAT_ID, "Topic B", topic_id=22)
        assert a.id != b.id

    async with session_scope() as session:
        sources = SourceChannelRepository(session)
        assert (await sources.get_enabled_by_chat_id(SOURCE_CHAT_ID, 11)).title == "Topic A"
        assert (await sources.get_enabled_by_chat_id(SOURCE_CHAT_ID, 22)).title == "Topic B"


async def test_an_order_records_the_topic_it_was_posted_in(wired):
    payload = replace(text_payload(SOURCE_CHAT_ID, "1234567"), topic_id=SOURCE_TOPIC)
    order_id = await deliver_order(wired, payload)
    assert order_id is not None


# ---------------------------------------------------------------------------
# Topics on the way out
# ---------------------------------------------------------------------------
async def test_orders_are_delivered_into_the_work_group_topic(services):
    async with session_scope() as session:
        await SourceChannelRepository(session).add(SOURCE_CHAT_ID, "Orders Source")
        group = await WorkGroupRepository(session).add(
            WORK_GROUP_CHAT_ID, "Work Group", topic_id=GROUP_TOPIC
        )
        from app.database.repositories import RouteRepository

        source = await SourceChannelRepository(session).get_by_chat_id(SOURCE_CHAT_ID)
        await RouteRepository(session).add(source.id, group.id)

    await deliver_order(services, text_payload(SOURCE_CHAT_ID, "New order"))

    sent = services.gateway.messages_in(WORK_GROUP_CHAT_ID)
    assert sent, "order never reached the work group"
    assert all(m.topic_id == GROUP_TOPIC for m in sent)


async def test_results_are_sent_into_the_destination_topic(destinations):
    services = destinations
    await _configure_success_on_text(services)
    async with session_scope() as session:
        repo = ResultDestinationRepository(session)
        target = (await repo.list_for_status(OrderStatus.SUCCESS))[0]
        await repo.set_topic(target.id, DEST_TOPIC)

    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "an order"))
    await operator_replies(services, order_id, text_payload(WORK_GROUP_CHAT_ID, "done"), OPERATOR_ID)

    results = services.gateway.messages_in(SUCCESS_CHAT_ID)
    assert results, "result never dispatched"
    assert all(m.topic_id == DEST_TOPIC for m in results)


async def test_a_destination_without_a_topic_posts_to_the_chat_itself(destinations):
    services = destinations
    await _configure_success_on_text(services)
    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "an order"))
    await operator_replies(services, order_id, text_payload(WORK_GROUP_CHAT_ID, "done"), OPERATOR_ID)
    assert all(m.topic_id == 0 for m in services.gateway.messages_in(SUCCESS_CHAT_ID))


# ---------------------------------------------------------------------------
# Result destinations scoped to one source
# ---------------------------------------------------------------------------
async def test_a_source_with_its_own_destination_bypasses_the_shared_one(destinations):
    """Results of a bound source go only to its own destination."""
    services = destinations
    await _configure_success_on_text(services)

    dedicated = -1006000000006
    async with session_scope() as session:
        source = await SourceChannelRepository(session).get_by_chat_id(SOURCE_CHAT_ID)
        await ResultDestinationRepository(session).add(
            OrderStatus.SUCCESS,
            dedicated,
            "Dedicated success",
            source_channel_id=source.id,
        )

    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "an order"))
    await operator_replies(services, order_id, text_payload(WORK_GROUP_CHAT_ID, "done"), OPERATOR_ID)

    assert services.gateway.orders_in(dedicated), "dedicated destination got nothing"
    assert services.gateway.orders_in(SUCCESS_CHAT_ID) == [], (
        "the shared destination must be skipped once the source has its own"
    )


async def test_a_source_without_its_own_destination_uses_the_shared_one(destinations):
    services = destinations
    await _configure_success_on_text(services)

    # A dedicated destination exists, but for a *different* source.
    async with session_scope() as session:
        other = await SourceChannelRepository(session).add(
            SECOND_SOURCE_CHAT_ID, "Second source"
        )
        await ResultDestinationRepository(session).add(
            OrderStatus.SUCCESS,
            -1007000000007,
            "Other source only",
            source_channel_id=other.id,
        )

    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "an order"))
    await operator_replies(services, order_id, text_payload(WORK_GROUP_CHAT_ID, "done"), OPERATOR_ID)

    assert services.gateway.orders_in(SUCCESS_CHAT_ID), "shared destination was skipped"
    assert services.gateway.orders_in(-1007000000007) == []


async def test_binding_applies_per_status(destinations):
    """A source may take over SUCCESS while leaving FAILED shared."""
    async with session_scope() as session:
        source = await SourceChannelRepository(session).get_by_chat_id(SOURCE_CHAT_ID)
        repo = ResultDestinationRepository(session)
        await repo.add(
            OrderStatus.SUCCESS, -1008000000008, "Only success", source_channel_id=source.id
        )
        chosen_success = await repo.list_for_source(OrderStatus.SUCCESS, source.id)
        chosen_failed = await repo.list_for_source(OrderStatus.FAILED, source.id)

    assert [d.chat_id for d in chosen_success] == [-1008000000008]
    assert [d.chat_id for d in chosen_failed] == [FAILURE_CHAT_ID]


async def test_unbound_orders_fall_back_to_shared_destinations(destinations):
    async with session_scope() as session:
        chosen = await ResultDestinationRepository(session).list_for_source(
            OrderStatus.SUCCESS, None
        )
    assert [d.chat_id for d in chosen] == [SUCCESS_CHAT_ID]


# ---------------------------------------------------------------------------
# Backlog guard
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_guard():
    startup_guard.reset()
    yield
    startup_guard.reset()


def _aged(chat_id: int, minutes: int):
    return replace(
        text_payload(chat_id, "an order"),
        sent_at=datetime.now(timezone.utc) - timedelta(minutes=minutes),
    )


async def test_a_stale_post_is_skipped_and_consumes_no_order_number(wired):
    async with session_scope() as session:
        repo = SettingRepository(session)
        await repo.set(SettingKey.STARTUP_BACKLOG_MODE, StartupBacklogMode.MAX_AGE.value)
        await repo.set(SettingKey.STARTUP_BACKLOG_MAX_AGE_MINUTES, "15")

    stale = await wired.orders.ingest(_aged(SOURCE_CHAT_ID, 240))
    assert stale.order_id is None
    assert "backlog" in stale.reason

    fresh = await wired.orders.ingest(_aged(SOURCE_CHAT_ID, 1))
    assert fresh.order_id is not None
    order = await get_order(fresh.order_id)
    # The skipped post must not have burned order1.
    assert order.daily_number == 1


async def test_ignore_downtime_skips_anything_posted_before_startup(wired):
    async with session_scope() as session:
        await SettingRepository(session).set(
            SettingKey.STARTUP_BACKLOG_MODE, StartupBacklogMode.IGNORE_DOWNTIME.value
        )
    startup_guard.mark_started(datetime.now(timezone.utc))

    before = await wired.orders.ingest(_aged(SOURCE_CHAT_ID, 30))
    assert before.order_id is None

    after = await wired.orders.ingest(_aged(SOURCE_CHAT_ID, -1))
    assert after.order_id is not None


async def test_mode_all_processes_the_whole_backlog(wired):
    async with session_scope() as session:
        await SettingRepository(session).set(
            SettingKey.STARTUP_BACKLOG_MODE, StartupBacklogMode.ALL.value
        )
    result = await wired.orders.ingest(_aged(SOURCE_CHAT_ID, 60 * 24))
    assert result.order_id is not None


async def test_a_post_without_a_timestamp_is_never_dropped(wired):
    """Missing data must not silently lose an order."""
    async with session_scope() as session:
        await SettingRepository(session).set(
            SettingKey.STARTUP_BACKLOG_MODE, StartupBacklogMode.MAX_AGE.value
        )
    result = await wired.orders.ingest(text_payload(SOURCE_CHAT_ID, "no timestamp"))
    assert result.order_id is not None


async def test_media_backlog_is_skipped_too(wired):
    async with session_scope() as session:
        await SettingRepository(session).set(
            SettingKey.STARTUP_BACKLOG_MODE, StartupBacklogMode.MAX_AGE.value
        )
    payload = replace(
        photo_payload(SOURCE_CHAT_ID, "old photo"),
        sent_at=datetime.now(timezone.utc) - timedelta(hours=6),
    )
    assert (await wired.orders.ingest(payload)).order_id is None


async def test_a_two_day_backlog_is_skipped_with_no_setting_configured(wired):
    """The exact situation after an outage: nothing configured, posts replayed.

    A database upgraded from an older version has no row for the backlog
    setting at all, so this asserts the *default* is the safe one -- a
    redeploy after days of downtime must not push old posts through.
    """
    async with session_scope() as session:
        # Prove the setting really is absent, not merely unset to the default.
        from app.database.models import Setting
        from sqlalchemy import select

        rows = (
            await session.execute(
                select(Setting).where(
                    Setting.key.in_(
                        [
                            SettingKey.STARTUP_BACKLOG_MODE,
                            SettingKey.STARTUP_BACKLOG_MAX_AGE_MINUTES,
                        ]
                    )
                )
            )
        ).scalars().all()
        assert rows == []

    for hours in (48, 36, 24, 2, 1):
        result = await wired.orders.ingest(_aged(SOURCE_CHAT_ID, hours * 60))
        assert result.order_id is None, f"a {hours}h old post was replayed"

    # A post from right now still becomes order1: the counter was untouched.
    fresh = await wired.orders.ingest(_aged(SOURCE_CHAT_ID, 0))
    assert fresh.order_id is not None
    assert (await get_order(fresh.order_id)).daily_number == 1
