"""Operator management and work-group assignment."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.filters import IsAdmin
from app.bot.handlers.admin.common import render
from app.bot.keyboards.admin import operator_detail, operator_list
from app.bot.keyboards.callbacks import Nav, OperatorCB
from app.bot.keyboards.common import back_keyboard
from app.bot.states.admin import AddOperator
from app.database.engine import session_scope
from app.database.repositories import (
    AuditRepository,
    OperatorRepository,
    WorkGroupRepository,
)
from app.utils.enums import AuditEvent

router = Router(name="admin_operators")


@router.callback_query(Nav.filter(F.section == "operators"), IsAdmin())
async def open_operators(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_list(callback)


async def _show_list(callback: CallbackQuery) -> None:
    async with session_scope() as session:
        operators = await OperatorRepository(session).list_all()
    if operators:
        lines = ["👤 <b>Operators</b>", "", "Only these users can change an order's status.", ""]
        for operator in operators:
            name = operator.display_name or (
                f"@{operator.username}" if operator.username else str(operator.telegram_user_id)
            )
            scope = "all groups" if operator.all_work_groups else "selected groups"
            lines.append(
                f"{'🟢' if operator.enabled else '🔴'} <b>{name}</b>\n"
                f"    <code>{operator.telegram_user_id}</code> · {scope}"
            )
        text = "\n".join(lines)
    else:
        text = (
            "👤 <b>Operators</b>\n\n"
            "No operator configured. Until one is added, replies and reactions "
            "in work groups will not change any order's status."
        )
    await render(callback, text, operator_list(operators))


@router.callback_query(OperatorCB.filter(F.action == "add"), IsAdmin())
async def prompt_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddOperator.waiting_for_user_id)
    await render(
        callback,
        "Send the operator's numeric <b>Telegram user ID</b>.\n\n"
        "They can get it by sending <code>/id</code> to this bot.",
        back_keyboard("operators"),
    )


@router.message(AddOperator.waiting_for_user_id, IsAdmin())
async def receive_user_id(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    try:
        user_id = int(raw)
    except ValueError:
        await message.answer("❌ Send a numeric Telegram user ID.")
        return
    async with session_scope() as session:
        operator = await OperatorRepository(session).add(user_id)
        await AuditRepository(session).log(
            AuditEvent.CONFIGURATION_CHANGED,
            actor_user_id=message.from_user.id if message.from_user else None,
            message=f"Operator added: {user_id}",
        )
        operators = await OperatorRepository(session).list_all()
    await state.clear()
    await message.answer(
        f"✅ Operator <code>{operator.telegram_user_id}</code> added.",
        reply_markup=operator_list(operators),
    )


@router.callback_query(OperatorCB.filter(F.action == "view"), IsAdmin())
async def view_operator(callback: CallbackQuery, callback_data: OperatorCB) -> None:
    await _render_detail(callback, callback_data.id)


async def _render_detail(callback: CallbackQuery, operator_id: int) -> None:
    async with session_scope() as session:
        operator = await OperatorRepository(session).get(operator_id)
        if operator is None:
            await callback.answer("Not found", show_alert=True)
            return
        groups = await WorkGroupRepository(session).list_all()
        assigned = {a.work_group_id for a in operator.assignments}
        name = operator.display_name or (
            f"@{operator.username}" if operator.username else str(operator.telegram_user_id)
        )
        detail = (
            f"👤 <b>{name}</b>\n\n"
            f"User ID: <code>{operator.telegram_user_id}</code>\n"
            f"Status: {'🟢 Enabled' if operator.enabled else '🔴 Disabled'}\n"
            f"Scope: {'All work groups' if operator.all_work_groups else 'Selected work groups'}\n"
            f"Assigned groups: {len(assigned)}"
        )
        markup = operator_detail(operator, groups, assigned)
    await render(callback, detail, markup)


@router.callback_query(OperatorCB.filter(F.action == "toggle"), IsAdmin())
async def toggle_operator(callback: CallbackQuery, callback_data: OperatorCB) -> None:
    async with session_scope() as session:
        repo = OperatorRepository(session)
        operator = await repo.get(callback_data.id)
        if operator is not None:
            await repo.set_enabled(callback_data.id, not operator.enabled)
    await _render_detail(callback, callback_data.id)


@router.callback_query(OperatorCB.filter(F.action == "scope"), IsAdmin())
async def toggle_scope(callback: CallbackQuery, callback_data: OperatorCB) -> None:
    async with session_scope() as session:
        operator = await OperatorRepository(session).get(callback_data.id)
        if operator is not None:
            operator.all_work_groups = not operator.all_work_groups
    await _render_detail(callback, callback_data.id)


@router.callback_query(OperatorCB.filter(F.action == "assign"), IsAdmin())
async def toggle_assignment(callback: CallbackQuery, callback_data: OperatorCB) -> None:
    async with session_scope() as session:
        repo = OperatorRepository(session)
        operator = await repo.get(callback_data.id)
        if operator is None:
            await callback.answer("Not found", show_alert=True)
            return
        assigned = {a.work_group_id for a in operator.assignments}
        if callback_data.arg in assigned:
            await repo.unassign_work_group(callback_data.id, callback_data.arg)
        else:
            await repo.assign_work_group(callback_data.id, callback_data.arg)
    await _render_detail(callback, callback_data.id)


@router.callback_query(OperatorCB.filter(F.action == "delete"), IsAdmin())
async def delete_operator(callback: CallbackQuery, callback_data: OperatorCB) -> None:
    async with session_scope() as session:
        await OperatorRepository(session).delete(callback_data.id)
        await AuditRepository(session).log(
            AuditEvent.CONFIGURATION_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"Operator #{callback_data.id} deleted",
        )
    await _show_list(callback)
