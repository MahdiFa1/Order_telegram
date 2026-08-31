"""Global settings and audit log browsing."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.filters import IsAdmin
from app.bot.handlers.admin.common import render
from app.bot.keyboards.admin import audit_keyboard, counter_scope_picker, settings_menu
from app.bot.keyboards.callbacks import AuditCB, Nav, SettingCB
from app.bot.keyboards.common import back_keyboard
from app.bot.states.admin import EditSetting
from app.database.engine import session_scope
from app.audit.formatting import format_page
from app.database.repositories import AuditRepository, SettingRepository
from app.orders.numbering import render_display_number
from app.utils.enums import AuditEvent, CounterScope, SettingKey

router = Router(name="admin_settings")


@router.callback_query(Nav.filter(F.section == "settings"), IsAdmin())
async def open_settings(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show(callback)


async def _show(callback: CallbackQuery, note: str = "") -> None:
    async with session_scope() as session:
        values = await SettingRepository(session).all()
    scope = values.get(SettingKey.COUNTER_SCOPE)
    preview = render_display_number(
        125,
        values.get(SettingKey.ORDER_PREFIX) or "order",
        values.get(SettingKey.ORDER_NUMBER_FORMAT) or "{prefix}{number}",
    )
    text = (
        "⚙️ <b>Settings</b>\n\n"
        f"<b>Counter Scope:</b> {scope}\n"
        "<i>GLOBAL — one shared daily sequence across all sources.\n"
        "PER_SOURCE — each source channel numbers its own orders.</i>\n\n"
        f"<b>Order Prefix:</b> {values.get(SettingKey.ORDER_PREFIX)}\n"
        f"<b>Number Format:</b> <code>{values.get(SettingKey.ORDER_NUMBER_FORMAT)}</code>\n"
        f"Preview: <b>{preview}</b>\n\n"
        "<i>The counter resets to 1 on the first order of each new business "
        "day in the configured timezone — no cron job is involved, so it is "
        "correct even if the bot was offline at midnight.</i>"
    )
    if note:
        text += f"\n\n{note}"
    await render(callback, text, settings_menu(values))


@router.callback_query(SettingCB.filter(F.action == "counter_scope"), IsAdmin())
async def prompt_scope(callback: CallbackQuery) -> None:
    await render(
        callback,
        "Choose the counter scope.\n\n"
        "Changing it does not renumber existing orders; the new scope applies "
        "from the next order onwards.",
        counter_scope_picker(),
    )


@router.callback_query(SettingCB.filter(F.action == "set_scope"), IsAdmin())
async def set_scope(callback: CallbackQuery, callback_data: SettingCB) -> None:
    scope = CounterScope(callback_data.arg)
    async with session_scope() as session:
        await SettingRepository(session).set(SettingKey.COUNTER_SCOPE, scope.value)
        await AuditRepository(session).log(
            AuditEvent.CONFIGURATION_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"Counter scope set to {scope.value}",
        )
    await _show(callback)


@router.callback_query(SettingCB.filter(F.action == "notifications"), IsAdmin())
async def toggle_notifications(callback: CallbackQuery) -> None:
    async with session_scope() as session:
        repo = SettingRepository(session)
        current = await repo.get_bool(SettingKey.ADMIN_NOTIFICATIONS_ENABLED, True)
        await repo.set(
            SettingKey.ADMIN_NOTIFICATIONS_ENABLED, "false" if current else "true"
        )
    await _show(callback)


@router.callback_query(SettingCB.filter(F.action.in_({"prefix", "format"})), IsAdmin())
async def prompt_value(
    callback: CallbackQuery, callback_data: SettingCB, state: FSMContext
) -> None:
    await state.set_state(EditSetting.waiting_for_value)
    await state.update_data(field=callback_data.action)
    if callback_data.action == "prefix":
        prompt = (
            "Send the new order prefix.\n\n"
            "Default is <code>order</code>, producing <code>order125</code>."
        )
    else:
        prompt = (
            "Send the number format template.\n\n"
            "Available placeholders: <code>{prefix}</code> and <code>{number}</code>.\n\n"
            "Examples:\n"
            "<code>{prefix}{number}</code> → order125\n"
            "<code>ORD-{number}</code> → ORD-125\n"
            "<code>{prefix}-{number}</code> → order-125"
        )
    await render(callback, prompt, back_keyboard("settings"))


@router.message(EditSetting.waiting_for_value, IsAdmin())
async def receive_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    field = data.get("field")
    value = (message.text or "").strip()
    if not value:
        await message.answer("❌ Empty value.")
        return

    if field == "format":
        try:
            probe = value.format(prefix="order", number=1)
        except (KeyError, IndexError, ValueError):
            await message.answer(
                "❌ Invalid template. Use only <code>{prefix}</code> and "
                "<code>{number}</code>."
            )
            return
        if "1" not in probe:
            await message.answer("❌ The template must include <code>{number}</code>.")
            return
        key = SettingKey.ORDER_NUMBER_FORMAT
    else:
        key = SettingKey.ORDER_PREFIX

    async with session_scope() as session:
        await SettingRepository(session).set(key, value)
        await AuditRepository(session).log(
            AuditEvent.CONFIGURATION_CHANGED,
            actor_user_id=message.from_user.id if message.from_user else None,
            message=f"Setting {key} set to {value}",
        )
        values = await SettingRepository(session).all()
    await state.clear()
    preview = render_display_number(
        125,
        values.get(SettingKey.ORDER_PREFIX) or "order",
        values.get(SettingKey.ORDER_NUMBER_FORMAT) or "{prefix}{number}",
    )
    await message.answer(
        f"✅ Saved. New orders will look like <b>{preview}</b>",
        reply_markup=settings_menu(values),
    )


# ---------------------------------------------------------------------------
# Audit logs
# ---------------------------------------------------------------------------
@router.callback_query(Nav.filter(F.section == "audit"), IsAdmin())
async def open_audit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_audit(callback, 0)


@router.callback_query(AuditCB.filter(F.action == "page"), IsAdmin())
async def page_audit(callback: CallbackQuery, callback_data: AuditCB) -> None:
    await _show_audit(callback, callback_data.offset)


async def _show_audit(callback: CallbackQuery, offset: int) -> None:
    async with session_scope() as session:
        entries = await AuditRepository(session).recent(limit=11, offset=offset)
    has_more = len(entries) > 10
    entries = entries[:10]
    text = format_page(entries, f"📝 <b>Audit Logs</b> (from #{offset + 1})")
    await render(callback, text, audit_keyboard(offset, has_more))
