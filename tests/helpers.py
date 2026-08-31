"""Helpers that simulate Telegram events without a network."""

from __future__ import annotations

from app.database.engine import session_scope
from app.database.repositories import OrderRepository
from app.rules.extractor import extract_from_reaction, extract_from_reply
from app.telegram.payload import MessagePayload
from app.utils.enums import ContentType

_message_counter = {"value": 1}


def next_message_id() -> int:
    _message_counter["value"] += 1
    return _message_counter["value"]


def text_payload(chat_id: int, text: str, message_id: int | None = None) -> MessagePayload:
    return MessagePayload(
        chat_id=chat_id,
        message_id=message_id if message_id is not None else next_message_id(),
        content_type=ContentType.TEXT,
        text=text,
    )


def photo_payload(
    chat_id: int,
    caption: str | None = None,
    message_id: int | None = None,
    media_group_id: str | None = None,
    file_id: str | None = None,
) -> MessagePayload:
    mid = message_id if message_id is not None else next_message_id()
    return MessagePayload(
        chat_id=chat_id,
        message_id=mid,
        content_type=ContentType.PHOTO,
        file_id=file_id or f"file-{mid}",
        caption=caption,
        media_group_id=media_group_id,
    )


async def deliver_order(services, payload: MessagePayload) -> int:
    """Ingest a source message and route it immediately."""
    result = await services.orders.ingest(payload)
    assert result.order_id is not None, result.reason
    if payload.media_group_id is None:
        await services.orders.route_order(result.order_id)
    return result.order_id


async def primary_work_group_message(order_id: int, chat_id: int) -> tuple[int, int]:
    """The (chat_id, message_id) an operator would reply to or react on."""
    async with session_scope() as session:
        row = await OrderRepository(session).primary_delivery_message(order_id, chat_id)
    assert row is not None, "order was never delivered to the work group"
    return row.chat_id, row.message_id


async def operator_replies(
    services, order_id: int, payload: MessagePayload, operator_id: int
):
    """Simulate an operator reply landing on the order's work-group message."""
    async with session_scope() as session:
        signals = await extract_from_reply(session, payload, operator_id)
    return await services.signals.apply(order_id, signals)


async def operator_reacts(
    services,
    order_id: int,
    chat_id: int,
    message_id: int,
    emoji: str,
    operator_id: int,
):
    """Simulate a ``message_reaction`` update from an operator."""
    async with session_scope() as session:
        signals = await extract_from_reaction(
            session,
            chat_id=chat_id,
            message_id=message_id,
            emojis=[emoji],
            actor_user_id=operator_id,
            bot_user_id=services.bot_user_id,
        )
    return await services.signals.apply(order_id, signals)


async def get_order(order_id: int):
    async with session_scope() as session:
        return await OrderRepository(session).get(order_id)
