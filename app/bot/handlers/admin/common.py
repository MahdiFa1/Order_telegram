"""Shared helpers for admin panel handlers."""

from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message


async def render(
    event: CallbackQuery | Message, text: str, markup: InlineKeyboardMarkup | None = None
) -> None:
    """Edit in place for a callback, send fresh for a command."""
    if isinstance(event, CallbackQuery):
        message = event.message
        if message is None:
            await event.answer()
            return
        try:
            await message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as error:
            # "message is not modified" is expected when a toggle lands on the
            # same rendering; anything else falls back to a new message.
            if "message is not modified" not in str(error).lower():
                await message.answer(text, reply_markup=markup)
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup)


def parse_chat_id(raw: str) -> int | None:
    value = raw.strip()
    if value.startswith("@"):
        return None
    try:
        return int(value)
    except ValueError:
        return None
