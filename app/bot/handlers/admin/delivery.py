"""How a failed Telegram delivery is retried, and what is still outstanding.

Sits under 📦 مقصد نتایج because that is where an admin goes when a result
did not arrive: the same screen that says where results go now also says
what happens when they do not get there.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.admin import strings as t
from app.admin import texts
from app.audit.formatting import truncate
from app.bot.filters import IsAdmin
from app.bot.handlers.admin.common import render
from app.bot.keyboards.admin import (
    delivery_queue,
    destinations_menu,
    telegram_alert_mode_picker,
    telegram_retry_detail,
)
from app.bot.keyboards.callbacks import ResultCB
from app.bot.keyboards.common import back_keyboard
from app.database.engine import session_scope
from app.database.repositories import (
    AcknowledgementRepository,
    AuditRepository,
    OrderRepository,
    SettingRepository,
)
from app.dispatch import policy as retry_policy
from app.services.container import Services
from app.utils.enums import AuditEvent, RetryAlertMode, SettingKey
from app.utils.time import utcnow

router = Router(name="admin_delivery")


class EditTelegramRetry(StatesGroup):
    waiting_for_value = State()


#: Each editable number: the setting it writes, its bounds, and its prompt.
RETRY_FIELDS: dict[str, tuple[str, tuple[int, int, int], str]] = {
    "max": (
        SettingKey.TELEGRAM_RETRY_MAX_ATTEMPTS,
        retry_policy.MAX_ATTEMPTS,
        t.TG_RETRY_MAX_PROMPT,
    ),
    "base": (
        SettingKey.TELEGRAM_RETRY_BASE_MINUTES,
        retry_policy.BASE_MINUTES,
        t.TG_RETRY_BASE_PROMPT,
    ),
    "cap": (
        SettingKey.TELEGRAM_RETRY_MAX_MINUTES,
        retry_policy.MAX_MINUTES,
        t.TG_RETRY_CAP_PROMPT,
    ),
}


# ---------------------------------------------------------------------------
# The retry policy
# ---------------------------------------------------------------------------
async def _show_retry(
    callback: CallbackQuery, services: Services, note: str = ""
) -> None:
    async with session_scope() as session:
        policy = await retry_policy.load_telegram_policy(session)
    # The in-call retries belong to the gateway and come from the
    # deployment's environment, so they are shown but not editable here.
    text = texts.telegram_retry_screen(policy, services.settings.telegram_max_retries)
    if note:
        text += f"\n\n{note}"
    await render(callback, text, telegram_retry_detail(policy))


@router.callback_query(ResultCB.filter(F.action == "tg_cfg"), IsAdmin())
async def view_retry(
    callback: CallbackQuery, services: Services, state: FSMContext
) -> None:
    await state.clear()
    await _show_retry(callback, services)


@router.callback_query(ResultCB.filter(F.action == "tg_toggle"), IsAdmin())
async def toggle_retry(callback: CallbackQuery, services: Services) -> None:
    async with session_scope() as session:
        settings = SettingRepository(session)
        enabled = await settings.get_bool(
            SettingKey.TELEGRAM_RETRY_ENABLED, default=True
        )
        await settings.set(
            SettingKey.TELEGRAM_RETRY_ENABLED, "false" if enabled else "true"
        )
        await AuditRepository(session).log(
            AuditEvent.CONFIGURATION_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"Telegram automatic retry {'disabled' if enabled else 'enabled'}",
        )
    await _show_retry(callback, services)


@router.callback_query(ResultCB.filter(F.action == "tg_set"), IsAdmin())
async def prompt_retry_number(
    callback: CallbackQuery, callback_data: ResultCB, state: FSMContext
) -> None:
    field = RETRY_FIELDS.get(callback_data.arg)
    if field is None:
        await callback.answer()
        return
    _key, (_default, low, high), prompt = field
    await state.set_state(EditTelegramRetry.waiting_for_value)
    await state.update_data(field=callback_data.arg)
    await render(
        callback,
        prompt.format(low=t.fa_digits(low), high=t.fa_digits(high)),
        back_keyboard("destinations"),
    )


@router.message(EditTelegramRetry.waiting_for_value, IsAdmin())
async def receive_retry_number(message: Message, state: FSMContext) -> None:
    from app.orders.order_number import normalise_digits

    data = await state.get_data()
    field = RETRY_FIELDS.get(data.get("field", ""))
    if field is None:
        await state.clear()
        return
    key, (_default, low, high), _prompt = field

    try:
        value = int(normalise_digits((message.text or "").strip()))
    except ValueError:
        value = -1
    if not low <= value <= high:
        await message.answer(
            t.RETRY_NUMBER_INVALID.format(low=t.fa_digits(low), high=t.fa_digits(high))
        )
        return

    async with session_scope() as session:
        await SettingRepository(session).set(key, str(value))
        await AuditRepository(session).log(
            AuditEvent.CONFIGURATION_CHANGED,
            actor_user_id=message.from_user.id if message.from_user else None,
            message=f"Telegram setting {key} set to {value}",
        )
    await state.clear()
    await message.answer(t.RETRY_SAVED, reply_markup=destinations_menu())


@router.callback_query(ResultCB.filter(F.action == "tg_alert"), IsAdmin())
async def prompt_alert_mode(callback: CallbackQuery) -> None:
    await render(callback, t.TG_ALERT_PROMPT, telegram_alert_mode_picker())


@router.callback_query(ResultCB.filter(F.action == "tg_set_alert"), IsAdmin())
async def set_alert_mode(
    callback: CallbackQuery, callback_data: ResultCB, services: Services
) -> None:
    try:
        mode = RetryAlertMode(callback_data.arg)
    except ValueError:
        await callback.answer()
        return
    async with session_scope() as session:
        await SettingRepository(session).set(SettingKey.TELEGRAM_ALERT_MODE, mode.value)
        await AuditRepository(session).log(
            AuditEvent.CONFIGURATION_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"Telegram alert mode set to {mode.value}",
        )
    await _show_retry(callback, services)


# ---------------------------------------------------------------------------
# The queue of deliveries that have not gone through
# ---------------------------------------------------------------------------
async def _queue_contents():
    """(dispatch rows, orders owing a reaction, counts, display numbers)."""
    async with session_scope() as session:
        policy = await retry_policy.load_telegram_policy(session)
        acks = AcknowledgementRepository(session)
        # One row and one retry button each: the keyboard shows the same ten.
        dispatches = await acks.unfinished_dispatches(limit=10)
        waiting, abandoned = await acks.dispatch_counts(policy.max_attempts)
        failed_acks = await acks.unfinished_acknowledgements(limit=10)
        orders = OrderRepository(session)
        display_numbers: dict[int, str] = {}
        for row in dispatches:
            order = await orders.get(row.order_id)
            if order is not None:
                display_numbers[row.order_id] = order.display_number
        for order in failed_acks:
            display_numbers[order.id] = order.display_number
    return policy, dispatches, failed_acks, waiting, abandoned, display_numbers


async def _show_queue(callback: CallbackQuery, note: str = "") -> None:
    now = utcnow()
    policy, dispatches, failed_acks, waiting, abandoned, display_numbers = (
        await _queue_contents()
    )
    text = texts.delivery_queue_screen(
        dispatches,
        failed_acks,
        display_numbers,
        waiting,
        abandoned,
        policy.max_attempts,
        now,
    )
    if note:
        text += f"\n\n{note}"
    order_ids: list[int] = []
    for row in dispatches:
        if row.order_id not in order_ids:
            order_ids.append(row.order_id)
    for order in failed_acks:
        if order.id not in order_ids:
            order_ids.append(order.id)
    await render(callback, truncate(text), delivery_queue(order_ids, display_numbers))


@router.callback_query(ResultCB.filter(F.action == "tg_queue"), IsAdmin())
async def view_queue(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_queue(callback)


@router.callback_query(ResultCB.filter(F.action == "tg_q_retry"), IsAdmin())
async def retry_one(
    callback: CallbackQuery, callback_data: ResultCB, services: Services
) -> None:
    if not callback_data.arg.isdigit():
        await callback.answer()
        return
    order_id = int(callback_data.arg)
    await callback.answer()
    await services.finalizer.run_pipeline(order_id, force=True)
    async with session_scope() as session:
        order = await OrderRepository(session).get(order_id)
    display = order.display_number if order else str(order_id)
    await _show_queue(callback, t.TG_QUEUE_RETRIED.format(display=display))


@router.callback_query(ResultCB.filter(F.action == "tg_q_all"), IsAdmin())
async def retry_all(callback: CallbackQuery, services: Services) -> None:
    await callback.answer()
    _policy, dispatches, failed_acks, _waiting, _abandoned, _names = (
        await _queue_contents()
    )
    order_ids: list[int] = []
    for row in dispatches:
        if row.order_id not in order_ids:
            order_ids.append(row.order_id)
    for order in failed_acks:
        if order.id not in order_ids:
            order_ids.append(order.id)
    for order_id in order_ids:
        await services.finalizer.run_pipeline(order_id, force=True)
    await _show_queue(
        callback, t.TG_QUEUE_RETRIED_ALL.format(count=t.fa_digits(len(order_ids)))
    )
