"""Order intake: albums, duplicates and forward-header-free delivery
(spec tests 92, 93; requirements 8, 14, 16, 65)."""

from __future__ import annotations

import asyncio

import pytest

from app.database.engine import session_scope
from app.database.repositories import OrderRepository
from app.database.models import Order
from sqlalchemy import func, select
from tests.conftest import SOURCE_CHAT_ID, WORK_GROUP_CHAT_ID
from tests.helpers import deliver_order, photo_payload, text_payload

pytestmark = pytest.mark.asyncio


async def _order_count() -> int:
    async with session_scope() as session:
        result = await session.execute(select(func.count()).select_from(Order))
        return int(result.scalar_one())


async def test_album_of_five_photos_is_one_order(wired):
    """Spec test 92: five photos sharing a media_group_id => Total Orders = 1."""
    services = wired
    group_id = "album-1"
    order_ids = set()
    for index in range(5):
        payload = photo_payload(
            SOURCE_CHAT_ID,
            caption="Apple ID\nUS\n100$" if index == 0 else None,
            media_group_id=group_id,
        )
        result = await services.orders.ingest(payload)
        order_ids.add(result.order_id)

    assert await _order_count() == 1
    assert len(order_ids) == 1

    order_id = order_ids.pop()
    async with session_scope() as session:
        sources = await OrderRepository(session).list_source_messages(order_id)
    # Every album message is mapped to the single order.
    assert len(sources) == 5

    await services.orders.route_order(order_id)
    album_items = services.gateway.messages_in(WORK_GROUP_CHAT_ID)
    assert len(album_items) == 5
    assert all(m.kind == "album_item" for m in album_items)
    # The order number leads the album's single caption.
    assert album_items[0].caption.startswith("order1\n\n")
    assert "Apple ID" in album_items[0].caption
    assert [m.caption for m in album_items[1:]] == [None] * 4


async def test_concurrent_album_parts_still_produce_one_order(wired):
    services = wired
    group_id = "album-race"
    payloads = [
        photo_payload(SOURCE_CHAT_ID, caption="cap" if i == 0 else None, media_group_id=group_id)
        for i in range(4)
    ]
    results = await asyncio.gather(*(services.orders.ingest(p) for p in payloads))
    assert await _order_count() == 1
    assert len({r.order_id for r in results}) == 1


async def test_duplicate_source_message_creates_one_order(wired):
    """Spec test 93: the same Telegram update twice => Total Orders = 1."""
    services = wired
    payload = text_payload(SOURCE_CHAT_ID, "Apple ID US 100$")

    first = await services.orders.ingest(payload)
    second = await services.orders.ingest(payload)

    assert await _order_count() == 1
    assert first.created is True
    assert second.created is False
    assert second.order_id == first.order_id


async def test_message_from_unconfigured_source_is_ignored(wired):
    services = wired
    result = await services.orders.ingest(text_payload(-1009999999999, "not a source"))
    assert result.order_id is None
    assert await _order_count() == 0


async def test_text_order_is_resent_with_number_and_no_forward_header(wired):
    """Requirement 14: no forwardMessage, so no 'Forwarded from' header."""
    services = wired
    await deliver_order(services, text_payload(SOURCE_CHAT_ID, "Apple ID\nUS\n100$"))

    sent = services.gateway.messages_in(WORK_GROUP_CHAT_ID)
    assert len(sent) == 1
    assert sent[0].kind == "text"
    assert sent[0].text == "order1\n\nApple ID\nUS\n100$"
    # A forward would have been recorded as kind="forward"; the composer has
    # no such operation at all.
    assert {m.kind for m in services.gateway.sent} <= {"text", "media", "album_item", "copy"}


async def test_single_photo_order_carries_the_number_in_its_caption(wired):
    services = wired
    await deliver_order(services, photo_payload(SOURCE_CHAT_ID, caption="Apple ID US"))
    sent = services.gateway.messages_in(WORK_GROUP_CHAT_ID)
    assert len(sent) == 1
    assert sent[0].kind == "media"
    assert sent[0].caption == "order1\n\nApple ID US"


async def test_delivery_messages_are_mapped_back_to_the_order(wired):
    services = wired
    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "order body"))
    sent = services.gateway.messages_in(WORK_GROUP_CHAT_ID)[0]

    async with session_scope() as session:
        resolved = await OrderRepository(session).get_by_delivery_message(
            sent.chat_id, sent.message_id
        )
    assert resolved is not None and resolved.id == order_id


async def test_routing_to_two_work_groups_delivers_twice(services):
    from app.database.repositories import (
        RouteRepository,
        SourceChannelRepository,
        WorkGroupRepository,
    )

    second_group_chat = -1002000000099
    async with session_scope() as session:
        source = await SourceChannelRepository(session).add(SOURCE_CHAT_ID, "Src")
        group_a = await WorkGroupRepository(session).add(WORK_GROUP_CHAT_ID, "WG1")
        group_b = await WorkGroupRepository(session).add(second_group_chat, "WG2")
        routes = RouteRepository(session)
        await routes.add(source.id, group_a.id)
        await routes.add(source.id, group_b.id)

    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "multi"))
    assert len(services.gateway.messages_in(WORK_GROUP_CHAT_ID)) == 1
    assert len(services.gateway.messages_in(second_group_chat)) == 1

    async with session_scope() as session:
        order = await OrderRepository(session).get(order_id)
    assert len(order.deliveries) == 2


async def test_repeated_routing_does_not_resend(wired):
    services = wired
    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "once"))
    await services.orders.route_order(order_id)
    await services.orders.process_pending_deliveries(order_id)
    assert len(services.gateway.messages_in(WORK_GROUP_CHAT_ID)) == 1
