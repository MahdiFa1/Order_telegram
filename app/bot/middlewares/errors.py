"""Keeps a failing handler from taking the bot down."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject

from app.admin import strings as t
from app.utils.logging import get_logger

logger = get_logger(__name__)


class ErrorGuardMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception:  # noqa: BLE001 - logged, never propagated to polling
            logger.exception("handler_failed", event_type=type(event).__name__)
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer(t.GENERIC_ERROR, show_alert=True)
                except Exception:  # noqa: BLE001
                    pass
            return None
