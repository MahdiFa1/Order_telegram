"""Drops Telegram updates that were already processed.

Telegram re-delivers an update when the bot dies before confirming the
polling offset. The ledger below makes that redelivery a no-op, so a restart
can never produce a duplicate order or a duplicate signal.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from app.database.engine import session_scope
from app.database.repositories import OrderRepository
from app.utils.logging import get_logger

logger = get_logger(__name__)

#: Update kinds that mutate order state and therefore must not run twice.
_GUARDED_EVENTS = ("message", "channel_post", "edited_channel_post", "message_reaction")


class IdempotencyMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)
        if not any(getattr(event, name, None) is not None for name in _GUARDED_EVENTS):
            return await handler(event, data)

        key = f"update:{event.update_id}"
        async with session_scope() as session:
            claimed = await OrderRepository(session).mark_update_processed(key)
        if not claimed:
            logger.info("duplicate_update_dropped", update_id=event.update_id)
            return None
        return await handler(event, data)
