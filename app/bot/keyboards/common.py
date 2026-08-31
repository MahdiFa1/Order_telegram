"""Shared keyboard builders."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards.callbacks import Nav


def back_button(section: str = "main", label: str = "⬅️ Back") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=label, callback_data=Nav(section=section).pack())


def back_keyboard(section: str = "main") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(back_button(section))
    return builder.as_markup()


def toggle_label(enabled: bool) -> str:
    return "🟢 Enabled" if enabled else "🔴 Disabled"


def toggle_icon(enabled: bool) -> str:
    return "🟢" if enabled else "🔴"


def yes_no(value: bool) -> str:
    return "YES" if value else "NO"
