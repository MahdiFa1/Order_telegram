"""Entry point and top-level navigation of the admin panel."""

from __future__ import annotations

import time

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.admin import texts
from app.bot.filters import IsAdmin
from app.bot.handlers.admin.common import render
from app.bot.keyboards.admin import main_menu
from app.bot.keyboards.callbacks import Nav
from app.services.container import Services
from app.reports.service import ReportPeriod

router = Router(name="admin_menu")

START_TIME = time.monotonic()


@router.message(CommandStart(), IsAdmin())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.MAIN_TEXT, reply_markup=main_menu())


@router.message(CommandStart())
async def cmd_start_denied(message: Message) -> None:
    if message.chat.type != "private":
        return
    await message.answer(
        "⛔️ You are not authorised to use this bot's admin panel.\n"
        "Ask a Super Admin to add your Telegram user ID."
    )


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    """Convenience: tells anyone their user id and the current chat id."""
    user_id = message.from_user.id if message.from_user else "unknown"
    await message.answer(
        f"👤 Your user ID: <code>{user_id}</code>\n"
        f"💬 This chat ID: <code>{message.chat.id}</code>"
    )


@router.callback_query(Nav.filter(F.section == "main"), IsAdmin())
async def open_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render(callback, texts.MAIN_TEXT, main_menu())


@router.callback_query(Nav.filter(F.section == "dashboard"), IsAdmin())
async def open_dashboard(callback: CallbackQuery, services: Services) -> None:
    report = await services.reports.order_report(ReportPeriod.today())
    status = await services.reports.system_status()
    await render(callback, texts.dashboard(report, status, bot_online=True), main_menu())


@router.callback_query(Nav.filter(F.section == "system_status"), IsAdmin())
async def open_system_status(callback: CallbackQuery, services: Services) -> None:
    status = await services.reports.system_status()
    uptime = time.monotonic() - START_TIME
    await render(
        callback, texts.system_status(status, uptime, bot_online=True), main_menu()
    )
