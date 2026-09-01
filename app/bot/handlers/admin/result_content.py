"""What the result destination receives, and the WooCommerce update."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.admin import strings as t
from app.bot.filters import IsAdmin
from app.bot.handlers.admin.common import render
from app.bot.keyboards.admin import (
    append_text_detail,
    result_content_menu,
    result_mode_picker,
    woo_detail,
    woo_store_detail,
)
from app.bot.keyboards.callbacks import Nav, ResultCB
from app.bot.keyboards.common import back_keyboard
from app.database.engine import session_scope
from app.database.repositories import (
    AuditRepository,
    ResultConfigRepository,
    SettingRepository,
)
from app.integrations.woocommerce import WooCommerceClient, WooCommerceCredentials
from app.utils.enums import AuditEvent, OrderStatus, ResultContentMode, SettingKey

router = Router(name="admin_result_content")


class EditResultText(StatesGroup):
    waiting_for_value = State()


@router.callback_query(Nav.filter(F.section == "result_content"), IsAdmin())
async def open_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_menu(callback)


async def _show_menu(callback: CallbackQuery, note: str = "") -> None:
    async with session_scope() as session:
        mode = await SettingRepository(session).result_content_mode()
    text = t.RESULT_CONTENT_INTRO + (f"\n\n{note}" if note else "")
    await render(callback, text, result_content_menu(mode.value))


@router.callback_query(ResultCB.filter(F.action == "mode"), IsAdmin())
async def prompt_mode(callback: CallbackQuery) -> None:
    await render(callback, t.RESULT_MODE_PROMPT, result_mode_picker())


@router.callback_query(ResultCB.filter(F.action == "set_mode"), IsAdmin())
async def set_mode(callback: CallbackQuery, callback_data: ResultCB) -> None:
    mode = ResultContentMode(callback_data.arg)
    async with session_scope() as session:
        await SettingRepository(session).set(SettingKey.RESULT_CONTENT_MODE, mode.value)
        await AuditRepository(session).log(
            AuditEvent.CONFIGURATION_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"Result content mode set to {mode.value}",
        )
    await _show_menu(callback)


# ---------------------------------------------------------------------------
# Text appended to the order in the result destination
# ---------------------------------------------------------------------------
@router.callback_query(ResultCB.filter(F.action == "text"), IsAdmin())
async def view_text(callback: CallbackQuery, callback_data: ResultCB) -> None:
    await _show_text(callback, OrderStatus(callback_data.status))


async def _show_text(callback: CallbackQuery, status: OrderStatus) -> None:
    async with session_scope() as session:
        config = await ResultConfigRepository(session).get(status)
        text = t.APPEND_TEXT_SCREEN.format(
            status=t.status_name(status),
            enabled=t.toggle_text(config.append_text_enabled),
            text=config.append_text or t.ACK_NOT_SET,
        )
        markup = append_text_detail(status, config)
    await render(callback, text, markup)


@router.callback_query(ResultCB.filter(F.action == "text_toggle"), IsAdmin())
async def toggle_text(callback: CallbackQuery, callback_data: ResultCB) -> None:
    status = OrderStatus(callback_data.status)
    async with session_scope() as session:
        repo = ResultConfigRepository(session)
        config = await repo.get(status)
        if not config.append_text_enabled and not config.append_text:
            await callback.answer(t.APPEND_NEEDS_TEXT_FIRST, show_alert=True)
            return
        await repo.update(status, append_text_enabled=not config.append_text_enabled)
    await _show_text(callback, status)


@router.callback_query(ResultCB.filter(F.action == "text_set"), IsAdmin())
async def prompt_text(
    callback: CallbackQuery, callback_data: ResultCB, state: FSMContext
) -> None:
    await state.set_state(EditResultText.waiting_for_value)
    await state.update_data(field="append_text", status=callback_data.status)
    await render(callback, t.APPEND_TEXT_PROMPT, back_keyboard("result_content"))


# ---------------------------------------------------------------------------
# WooCommerce
# ---------------------------------------------------------------------------
@router.callback_query(ResultCB.filter(F.action == "woo"), IsAdmin())
async def view_woo(callback: CallbackQuery, callback_data: ResultCB) -> None:
    await _show_woo(callback, OrderStatus(callback_data.status))


async def _show_woo(callback: CallbackQuery, status: OrderStatus) -> None:
    async with session_scope() as session:
        config = await ResultConfigRepository(session).get(status)
        text = t.WOO_SCREEN.format(
            status=t.status_name(status),
            enabled=t.toggle_text(config.woo_enabled),
            woo_status=config.woo_status or t.WOO_NOT_CONFIGURED,
            note_enabled=t.toggle_text(config.woo_note_enabled),
            note=config.woo_note or t.WOO_NOT_CONFIGURED,
        )
        markup = woo_detail(status, config)
    await render(callback, text, markup)


@router.callback_query(ResultCB.filter(F.action == "woo_toggle"), IsAdmin())
async def toggle_woo(callback: CallbackQuery, callback_data: ResultCB) -> None:
    status = OrderStatus(callback_data.status)
    async with session_scope() as session:
        settings = SettingRepository(session)
        repo = ResultConfigRepository(session)
        config = await repo.get(status)
        if not config.woo_enabled:
            # Without an order number the bot cannot tell the store which
            # order to update, so the dependency is enforced here.
            if not await settings.get_bool(SettingKey.ORDER_NUMBER_ENABLED, False):
                await callback.answer(t.WOO_NEEDS_ORDER_NUMBER, show_alert=True)
                return
            base_url, key, secret = await settings.woo_credentials()
            if not (base_url and key and secret):
                await callback.answer(t.WOO_NEEDS_STORE, show_alert=True)
                return
        await repo.update(status, woo_enabled=not config.woo_enabled)
        await AuditRepository(session).log(
            AuditEvent.CONFIGURATION_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"WooCommerce update for {status.value} "
            f"{'disabled' if config.woo_enabled else 'enabled'}",
        )
    await _show_woo(callback, status)


@router.callback_query(ResultCB.filter(F.action == "woo_note_toggle"), IsAdmin())
async def toggle_woo_note(callback: CallbackQuery, callback_data: ResultCB) -> None:
    status = OrderStatus(callback_data.status)
    async with session_scope() as session:
        repo = ResultConfigRepository(session)
        config = await repo.get(status)
        await repo.update(status, woo_note_enabled=not config.woo_note_enabled)
    await _show_woo(callback, status)


@router.callback_query(
    ResultCB.filter(F.action.in_({"woo_status", "woo_note_set"})), IsAdmin()
)
async def prompt_woo_field(
    callback: CallbackQuery, callback_data: ResultCB, state: FSMContext
) -> None:
    field = "woo_status" if callback_data.action == "woo_status" else "woo_note"
    await state.set_state(EditResultText.waiting_for_value)
    await state.update_data(field=field, status=callback_data.status)
    prompt = t.WOO_STATUS_PROMPT if field == "woo_status" else t.WOO_NOTE_PROMPT
    await render(callback, prompt, back_keyboard("result_content"))


# ---------------------------------------------------------------------------
# Store connection (shared by both statuses)
# ---------------------------------------------------------------------------
@router.callback_query(ResultCB.filter(F.action == "store"), IsAdmin())
async def view_store(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_store(callback)


async def _show_store(callback: CallbackQuery, note: str = "") -> None:
    async with session_scope() as session:
        base_url, key, secret = await SettingRepository(session).woo_credentials()
    text = t.WOO_STORE_SCREEN.format(
        base_url=base_url or t.WOO_NOT_CONFIGURED,
        key=key or t.WOO_NOT_CONFIGURED,
        # Never echo the secret back, even to an admin.
        secret=t.WOO_SECRET_MASK if secret else t.WOO_NOT_CONFIGURED,
    )
    if note:
        text += f"\n\n{note}"
    await render(callback, text, woo_store_detail())


@router.callback_query(
    ResultCB.filter(F.action.in_({"store_url", "store_key", "store_secret"})), IsAdmin()
)
async def prompt_store_field(
    callback: CallbackQuery, callback_data: ResultCB, state: FSMContext
) -> None:
    field = {
        "store_url": SettingKey.WOO_BASE_URL,
        "store_key": SettingKey.WOO_CONSUMER_KEY,
        "store_secret": SettingKey.WOO_CONSUMER_SECRET,
    }[callback_data.action]
    prompt = {
        "store_url": t.WOO_URL_PROMPT,
        "store_key": t.WOO_KEY_PROMPT,
        "store_secret": t.WOO_SECRET_PROMPT,
    }[callback_data.action]
    await state.set_state(EditResultText.waiting_for_value)
    await state.update_data(field=str(field), status="")
    await render(callback, prompt, back_keyboard("result_content"))


@router.callback_query(ResultCB.filter(F.action == "store_test"), IsAdmin())
async def test_store(callback: CallbackQuery) -> None:
    await callback.answer()
    async with session_scope() as session:
        base_url, key, secret = await SettingRepository(session).woo_credentials()
    detail = await WooCommerceClient(
        WooCommerceCredentials(base_url, key, secret)
    ).ping()
    ok = detail == "ok"
    await _show_store(
        callback, t.WOO_TEST_RESULT.format(icon="✅" if ok else "❌", detail=detail)
    )


@router.message(EditResultText.waiting_for_value, IsAdmin())
async def receive_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    field = data.get("field", "")
    value = (message.text or "").strip()
    if not value:
        await message.answer(t.SETTING_EMPTY)
        return

    store_keys = {
        str(SettingKey.WOO_BASE_URL),
        str(SettingKey.WOO_CONSUMER_KEY),
        str(SettingKey.WOO_CONSUMER_SECRET),
    }
    async with session_scope() as session:
        if field in store_keys:
            if field == str(SettingKey.WOO_BASE_URL):
                value = value.rstrip("/")
            await SettingRepository(session).set(field, value)
            await AuditRepository(session).log(
                AuditEvent.CONFIGURATION_CHANGED,
                actor_user_id=message.from_user.id if message.from_user else None,
                # The value itself is never written to the audit trail.
                message=f"WooCommerce setting {field} updated",
            )
        else:
            status = OrderStatus(data["status"])
            await ResultConfigRepository(session).update(status, **{field: value})
            await AuditRepository(session).log(
                AuditEvent.CONFIGURATION_CHANGED,
                actor_user_id=message.from_user.id if message.from_user else None,
                message=f"{status.value} {field} updated",
            )
        mode = await SettingRepository(session).result_content_mode()

    await state.clear()
    saved = t.APPEND_TEXT_SAVED if field == "append_text" else t.WOO_SAVED
    await message.answer(saved, reply_markup=result_content_menu(mode.value))
