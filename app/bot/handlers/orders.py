"""Order intake from source channels."""

from __future__ import annotations

from aiogram import Router
from aiogram.enums import ChatType
from aiogram.types import Message

from app.bot.filters import IsSourceChannel
from app.services.container import Services
from app.telegram.payload import extract_payload
from app.utils.logging import get_logger

logger = get_logger(__name__)

router = Router(name="orders")


@router.channel_post(IsSourceChannel())
async def handle_channel_post(message: Message, services: Services) -> None:
    """Every new post in an enabled source channel becomes an order."""
    await _ingest(message, services)


@router.message(IsSourceChannel())
async def handle_group_source_post(message: Message, services: Services) -> None:
    """A source can also be a group/supergroup rather than a channel."""
    if message.chat.type == ChatType.PRIVATE:
        return
    await _ingest(message, services)


@router.edited_channel_post()
async def handle_edited_channel_post(message: Message) -> None:
    """An edit is never a new order.

    Re-editing the already delivered work-group copy is intentionally not
    done in this version; see README "Known project limitations".
    """
    logger.info(
        "edited_channel_post_ignored",
        chat_id=message.chat.id,
        message_id=message.message_id,
    )


async def _ingest(message: Message, services: Services) -> None:
    payload = extract_payload(message)
    result = await services.orders.ingest(payload)
    if result.order_id is None:
        logger.info(
            "source_message_skipped",
            chat_id=payload.chat_id,
            message_id=payload.message_id,
            reason=result.reason,
        )
        return
    if not (result.created or result.attached):
        return
    # Albums are routed once, after their last part has arrived.
    await services.orders.schedule_routing(
        result.order_id, payload.media_group_id, payload.chat_id
    )
