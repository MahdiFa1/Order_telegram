"""Order lookup, inspection and manual override."""

from __future__ import annotations

from datetime import date

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.admin import strings as t
from app.admin import texts
from app.audit.formatting import format_page, truncate
from app.bot.filters import IsAdmin
from app.bot.handlers.admin.common import render
from app.bot.keyboards.admin import order_actions, override_options
from app.bot.keyboards.callbacks import Nav, OrderCB
from app.bot.keyboards.common import back_keyboard
from app.bot.states.admin import FindOrder
from app.database.engine import session_scope
from app.database.repositories import (
    AcknowledgementRepository,
    AuditRepository,
    OrderRepository,
    SourceChannelRepository,
    WooCommerceRepository,
)
from app.dispatch.policy import load_store_policy, load_telegram_policy
from app.services.container import Services
from app.utils.enums import OrderStatus
from app.utils.time import business_date, format_local

router = Router(name="admin_orders")


@router.callback_query(Nav.filter(F.section == "find_order"), IsAdmin())
async def open_find(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(FindOrder.waiting_for_query)
    await render(
        callback,
        t.FIND_ORDER_PROMPT,
        back_keyboard("main"),
    )


@router.message(Command("order"), IsAdmin())
async def cmd_order(
    message: Message, command: CommandObject, state: FSMContext
) -> None:
    await state.clear()
    await _search_and_reply(message, command.args or "")


@router.message(FindOrder.waiting_for_query, IsAdmin())
async def receive_query(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _search_and_reply(message, message.text or "")


def _parse_query(raw: str) -> tuple[date, int | None, str | None]:
    """Parse ``153`` / ``order153`` / ``2026-08-24 153``."""
    parts = raw.strip().split()
    day = business_date()
    token = ""
    if len(parts) == 2:
        try:
            day = date.fromisoformat(parts[0])
            token = parts[1]
        except ValueError:
            token = parts[-1]
    elif parts:
        token = parts[0]

    digits = "".join(ch for ch in token if ch.isdigit())
    number = int(digits) if digits else None
    return day, number, token or None


async def _search_and_reply(message: Message, raw: str) -> None:
    day, number, token = _parse_query(raw)
    if number is None:
        await message.answer(t.ORDER_QUERY_INVALID)
        return
    async with session_scope() as session:
        orders = await OrderRepository(session).search(
            business_day=day, daily_number=number, display_number=token
        )
    if not orders:
        await message.answer(
            t.ORDER_NOT_FOUND.format(
                number=t.fa_digits(number), day=t.fa_digits(day.isoformat())
            )
        )
        return
    if len(orders) == 1:
        text, markup = await _order_screen(orders[0].id)
        await message.answer(text, reply_markup=markup)
        return

    lines = [
        t.ORDER_MULTIPLE.format(
            count=t.fa_digits(len(orders)),
            number=t.fa_digits(number),
            day=t.fa_digits(day),
        ),
        "",
    ]
    for order in orders:
        lines.append(
            t.ORDER_MULTIPLE_ROW.format(
                display=order.display_number,
                status=t.status_name(order.status),
                scope=order.counter_scope_key,
                created=t.fa_digits(format_local(order.created_at)),
                id=order.id,
            )
        )
    await message.answer("\n".join(lines))


@router.message(F.text.regexp(r"^/orderid_(\d+)$"), IsAdmin())
async def open_by_id(message: Message) -> None:
    order_id = int((message.text or "").split("_", 1)[1])
    text, markup = await _order_screen(order_id)
    await message.answer(text, reply_markup=markup)


async def _order_screen(order_id: int) -> tuple[str, object]:
    async with session_scope() as session:
        order = await OrderRepository(session).get(order_id)
        if order is None:
            return t.ORDER_MISSING, back_keyboard("main")
        source = None
        if order.source_channel_id:
            channel = await SourceChannelRepository(session).get(order.source_channel_id)
            source = channel.title if channel else None
        signals = await OrderRepository(session).list_signals(order_id)
        dispatches = await AcknowledgementRepository(session).list_dispatches(order_id)
        store_call = await WooCommerceRepository(session).get_call(order_id)
        store_policy = await load_store_policy(session)
        telegram_policy = await load_telegram_policy(session)
        detail = texts.order_detail(
            order,
            source,
            signals,
            dispatches,
            store_call,
            store_policy.max_attempts,
            telegram_policy.max_attempts,
        )
        return truncate(detail), order_actions(order, store_call)


@router.callback_query(OrderCB.filter(F.action == "view"), IsAdmin())
async def view_order(callback: CallbackQuery, callback_data: OrderCB) -> None:
    text, markup = await _order_screen(callback_data.id)
    await render(callback, text, markup)


@router.callback_query(OrderCB.filter(F.action == "mark"), IsAdmin())
async def prompt_override(
    callback: CallbackQuery, callback_data: OrderCB, services: Services
) -> None:
    status = OrderStatus(callback_data.arg)
    if status is OrderStatus.PENDING:
        # Re-opening never dispatches or acknowledges anything.
        await _apply_override(callback, services, callback_data.id, status, False, False)
        return
    await render(
        callback,
        t.OVERRIDE_PROMPT.format(status=t.status_name(status)),
        override_options(callback_data.id, status.value),
    )


@router.callback_query(OrderCB.filter(F.action == "mark_go"), IsAdmin())
async def do_override(
    callback: CallbackQuery, callback_data: OrderCB, services: Services
) -> None:
    flags = callback_data.flags
    await _apply_override(
        callback,
        services,
        callback_data.id,
        OrderStatus(callback_data.arg),
        flags[:1] == "1",
        flags[1:2] == "1",
    )


async def _apply_override(
    callback: CallbackQuery,
    services: Services,
    order_id: int,
    status: OrderStatus,
    dispatch: bool,
    acknowledge: bool,
) -> None:
    await services.finalizer.manual_override(
        order_id,
        status,
        callback.from_user.id,
        dispatch_result=dispatch,
        apply_acknowledgement=acknowledge,
    )
    text, markup = await _order_screen(order_id)
    await render(callback, text, markup)


@router.callback_query(OrderCB.filter(F.action == "retry"), IsAdmin())
async def retry_pipeline(callback: CallbackQuery, callback_data: OrderCB, services: Services) -> None:
    """Try the whole pipeline again now, whatever each leg's backoff says.

    An admin presses this after fixing something -- re-adding the bot to a
    channel, allowing the emoji -- so every schedule starts over.
    """
    await callback.answer()
    await services.finalizer.run_pipeline(callback_data.id, force=True)
    text, markup = await _order_screen(callback_data.id)
    await render(callback, text, markup)


@router.callback_query(OrderCB.filter(F.action == "woo_retry"), IsAdmin())
async def retry_store(
    callback: CallbackQuery, callback_data: OrderCB, services: Services
) -> None:
    """Try the store again now, whatever the backoff or the last verdict said.

    An admin presses this after fixing something -- the keys, the order in
    the store -- so the schedule and the "permanent" mark start over.
    """
    if services.store is None:
        await callback.answer()
        return
    await callback.answer()
    outcome = await services.store.retry_now(callback_data.id)
    text, markup = await _order_screen(callback_data.id)
    if not outcome.ok and outcome.attempted:
        text = truncate(text, limit=3500) + "\n\n" + t.WOO_QUEUE_RETRY_FAILED.format(
            reason=outcome.reason[:200]
        )
    await render(callback, text, markup)


@router.callback_query(OrderCB.filter(F.action == "audit"), IsAdmin())
async def order_audit(callback: CallbackQuery, callback_data: OrderCB) -> None:
    async with session_scope() as session:
        entries = await AuditRepository(session).for_order(callback_data.id)
    text = format_page(
        entries,
        t.AUDIT_ORDER_TITLE.format(order_id=t.fa_digits(callback_data.id)),
        include_order=False,
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t.BTN_BACK_TO_ORDER,
                    callback_data=OrderCB(action="view", id=callback_data.id).pack(),
                )
            ]
        ]
    )
    await render(callback, text, markup)
