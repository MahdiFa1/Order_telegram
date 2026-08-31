"""Bounded retry with exponential backoff for Telegram calls.

Retries are only ever applied to *idempotent-safe* wrappers. A send that
already succeeded is never retried, because the caller claims its outbox row
before issuing the call and marks the result afterwards.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from aiogram.exceptions import TelegramRetryAfter

from app.telegram.errors import describe, is_retryable
from app.utils.logging import get_logger

T = TypeVar("T")
logger = get_logger(__name__)


async def with_retry(
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    operation_name: str = "telegram_call",
) -> T:
    attempt = 0
    while True:
        attempt += 1
        try:
            return await operation()
        except Exception as error:  # noqa: BLE001 - classified below
            if attempt >= max_attempts or not is_retryable(error):
                raise
            if isinstance(error, TelegramRetryAfter):
                delay = float(error.retry_after)
            else:
                delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            logger.warning(
                "telegram_call_retry",
                operation=operation_name,
                attempt=attempt,
                max_attempts=max_attempts,
                delay=delay,
                error=describe(error),
            )
            await asyncio.sleep(delay)
