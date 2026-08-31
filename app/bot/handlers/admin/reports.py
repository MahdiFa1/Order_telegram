"""Reporting screens."""

from __future__ import annotations

from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.admin import texts
from app.admin import strings as t
from app.bot.filters import IsAdmin
from app.bot.handlers.admin.common import render
from app.bot.keyboards.admin import report_result_keyboard, reports_menu
from app.bot.keyboards.callbacks import Nav, ReportCB
from app.bot.keyboards.common import back_keyboard
from app.bot.states.admin import CustomRange
from app.database.engine import session_scope
from app.database.repositories import SourceChannelRepository, WorkGroupRepository
from app.reports.service import ReportPeriod
from app.services.container import Services

router = Router(name="admin_reports")

def _group_row(name: object, report) -> str:
    return t.BY_GROUP_ROW.format(
        name=name,
        total=t.fa_digits(report.total),
        success=t.fa_digits(report.success),
        failed=t.fa_digits(report.failed),
        pending=t.fa_digits(report.pending),
        conflict=t.fa_digits(report.conflict),
        rate=t.fa_digits(f"{report.success_rate:.2f}"),
    )


_PERIODS = {
    "today": ReportPeriod.today,
    "yesterday": ReportPeriod.yesterday,
    "last7": lambda: ReportPeriod.last_days(7),
    "last30": lambda: ReportPeriod.last_days(30),
}


@router.callback_query(Nav.filter(F.section == "reports"), IsAdmin())
async def open_reports(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render(
        callback,
        t.REPORTS_INTRO,
        reports_menu(),
    )


@router.callback_query(ReportCB.filter(F.action.in_(list(_PERIODS))), IsAdmin())
async def show_period(
    callback: CallbackQuery, callback_data: ReportCB, services: Services
) -> None:
    period = _PERIODS[callback_data.action]()
    report = await services.reports.order_report(period)
    await render(callback, texts.order_report(report), report_result_keyboard())


@router.callback_query(ReportCB.filter(F.action == "operators"), IsAdmin())
async def show_operators(callback: CallbackQuery, services: Services) -> None:
    period = ReportPeriod.last_days(7)
    reports = await services.reports.operator_reports(period)
    await render(
        callback, texts.operator_report(reports, period.label), report_result_keyboard()
    )


@router.callback_query(ReportCB.filter(F.action == "sources"), IsAdmin())
async def show_sources(callback: CallbackQuery, services: Services) -> None:
    period = ReportPeriod.today()
    async with session_scope() as session:
        sources = await SourceChannelRepository(session).list_all()
    lines = [t.BY_SOURCE_TITLE.format(period=period.label), ""]
    if not sources:
        lines.append(t.NO_SOURCE_CONFIGURED)
    for source in sources:
        report = await services.reports.order_report(period, source_channel_id=source.id)
        lines.append(_group_row(source.title or source.chat_id, report))
    await render(callback, "\n".join(lines), report_result_keyboard())


@router.callback_query(ReportCB.filter(F.action == "workgroups"), IsAdmin())
async def show_work_groups(callback: CallbackQuery, services: Services) -> None:
    period = ReportPeriod.today()
    async with session_scope() as session:
        groups = await WorkGroupRepository(session).list_all()
    lines = [t.BY_WORK_GROUP_TITLE.format(period=period.label), ""]
    if not groups:
        lines.append(t.NO_WORK_GROUP_CONFIGURED)
    for group in groups:
        report = await services.reports.order_report(period, work_group_id=group.id)
        lines.append(_group_row(group.title or group.chat_id, report))
    await render(callback, "\n".join(lines), report_result_keyboard())


@router.callback_query(ReportCB.filter(F.action == "custom"), IsAdmin())
async def prompt_custom(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CustomRange.waiting_for_range)
    await render(
        callback,
        t.CUSTOM_RANGE_PROMPT,
        back_keyboard("reports"),
    )


@router.message(CustomRange.waiting_for_range, IsAdmin())
async def receive_custom(message: Message, state: FSMContext, services: Services) -> None:
    parts = (message.text or "").split()
    try:
        if len(parts) == 1:
            first = last = date.fromisoformat(parts[0])
        elif len(parts) == 2:
            first, last = date.fromisoformat(parts[0]), date.fromisoformat(parts[1])
        else:
            raise ValueError
    except ValueError:
        await message.answer(t.CUSTOM_RANGE_INVALID)
        return
    if first > last:
        first, last = last, first
    report = await services.reports.order_report(ReportPeriod.custom(first, last))
    await state.clear()
    await message.answer(texts.order_report(report), reply_markup=report_result_keyboard())
