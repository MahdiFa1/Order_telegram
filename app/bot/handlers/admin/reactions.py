"""Result Reactions: the acknowledgement configuration screens."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.admin import texts
from app.admin import strings as t
from app.bot.filters import IsAdmin
from app.bot.handlers.admin.common import render
from app.bot.keyboards.admin import (
    acknowledgement_detail,
    dispatch_policy_picker,
    reactions_menu,
    target_mode_picker,
)
from app.bot.keyboards.callbacks import AckCB, Nav
from app.bot.keyboards.common import back_keyboard
from app.bot.states.admin import SetAckReaction, TestAckReaction
from app.database.engine import session_scope
from app.database.repositories import (
    AcknowledgementRepository,
    AuditRepository,
    ResultDestinationRepository,
)
from app.services.container import Services
from app.utils.enums import (
    AcknowledgementTargetMode,
    AuditEvent,
    DispatchPolicy,
    OrderStatus,
    RESULT_STATUSES,
)

router = Router(name="admin_reactions")


@router.callback_query(Nav.filter(F.section == "reactions"), IsAdmin())
async def open_reactions(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render(
        callback,
        t.REACTIONS_INTRO,
        reactions_menu(),
    )


async def _warnings(session, status: OrderStatus, config) -> list[str]:
    """Configuration validation surfaced directly on the screen."""
    warnings: list[str] = []
    if config.enabled and not config.reaction_value:
        warnings.append(t.ACK_WARN_NO_REACTION)

    destinations = await ResultDestinationRepository(session).list_for_status(
        status, only_enabled=True
    )
    if config.enabled and not destinations:
        warnings.append(
            t.ACK_WARN_NO_DESTINATION.format(status=t.status_name(status))
        )

    acks = AcknowledgementRepository(session)
    other_status = next(s for s in RESULT_STATUSES if s is not status)
    other = await acks.get_config(other_status)
    if (
        config.enabled
        and other.enabled
        and config.reaction_value
        and config.reaction_value == other.reaction_value
    ):
        warnings.append(
            t.ACK_WARN_SAME_EMOJI.format(emoji=config.reaction_value)
        )
    return warnings


@router.callback_query(AckCB.filter(F.action == "view"), IsAdmin())
async def view_config(callback: CallbackQuery, callback_data: AckCB, state: FSMContext) -> None:
    await state.clear()
    await _render(callback, OrderStatus(callback_data.status))


async def _render(callback: CallbackQuery, status: OrderStatus, note: str = "") -> None:
    async with session_scope() as session:
        config = await AcknowledgementRepository(session).get_config(status)
        warnings = await _warnings(session, status, config)
        text = texts.acknowledgement_screen(status, config, warnings)
        markup = acknowledgement_detail(status, config)
    if note:
        text = f"{text}\n\n{note}"
    await render(callback, text, markup)


@router.callback_query(AckCB.filter(F.action == "toggle"), IsAdmin())
async def toggle(callback: CallbackQuery, callback_data: AckCB) -> None:
    status = OrderStatus(callback_data.status)
    async with session_scope() as session:
        acks = AcknowledgementRepository(session)
        config = await acks.get_config(status)
        if not config.enabled and not config.reaction_value:
            await callback.answer(t.ACK_NEEDS_REACTION_FIRST, show_alert=True)
            return
        await acks.update_config(status, enabled=not config.enabled)
        await AuditRepository(session).log(
            AuditEvent.REACTION_CONFIGURATION_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"{status.value} acknowledgement "
            f"{'disabled' if config.enabled else 'enabled'}",
        )
    await _render(callback, status)


@router.callback_query(AckCB.filter(F.action == "set_reaction"), IsAdmin())
async def prompt_reaction(
    callback: CallbackQuery, callback_data: AckCB, state: FSMContext
) -> None:
    await state.set_state(SetAckReaction.waiting_for_emoji)
    await state.update_data(status=callback_data.status)
    await render(
        callback,
        t.SET_ACK_REACTION_PROMPT,
        back_keyboard("reactions"),
    )


@router.message(SetAckReaction.waiting_for_emoji, IsAdmin())
async def receive_reaction(message: Message, state: FSMContext, services: Services) -> None:
    emoji = (message.text or "").strip()
    if not emoji or len(emoji) > 16:
        await message.answer(t.REACTION_INVALID)
        return
    data = await state.get_data()
    status = OrderStatus(data["status"])

    async with session_scope() as session:
        acks = AcknowledgementRepository(session)
        await acks.update_config(status, reaction_value=emoji)
        await AuditRepository(session).log(
            AuditEvent.REACTION_CONFIGURATION_CHANGED,
            actor_user_id=message.from_user.id if message.from_user else None,
            message=f"{status.value} acknowledgement reaction set to {emoji}",
        )
        destinations = await ResultDestinationRepository(session).list_for_status(
            status, only_enabled=True
        )

    # Best-effort availability check against the configured destinations.
    notes: list[str] = []
    for destination in destinations:
        try:
            info = await services.gateway.get_chat_info(destination.chat_id)
            available = info.get("available_reactions")
            if available is not None and emoji not in available:
                notes.append(
                    "⚠️ "
                    + t.ACK_REACTION_NOT_ALLOWED.format(
                        chat=destination.title or destination.chat_id,
                        emoji=emoji,
                        allowed=" ".join(available[:12]) or t.CHAT_NONE,
                    )
                )
        except Exception:  # noqa: BLE001 - probing is optional
            continue

    await state.clear()
    body = t.ACK_REACTION_SAVED.format(status=t.status_name(status), emoji=emoji)
    if notes:
        body += "\n\n" + "\n".join(notes)
    await message.answer(body, reply_markup=reactions_menu())


@router.callback_query(AckCB.filter(F.action == "target"), IsAdmin())
async def prompt_target(callback: CallbackQuery, callback_data: AckCB) -> None:
    await render(
        callback,
        t.TARGET_PROMPT,
        target_mode_picker(OrderStatus(callback_data.status)),
    )


@router.callback_query(AckCB.filter(F.action == "set_target"), IsAdmin())
async def set_target(callback: CallbackQuery, callback_data: AckCB) -> None:
    status = OrderStatus(callback_data.status)
    mode = AcknowledgementTargetMode(callback_data.arg)
    async with session_scope() as session:
        await AcknowledgementRepository(session).update_config(status, target_mode=mode)
        await AuditRepository(session).log(
            AuditEvent.REACTION_CONFIGURATION_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"{status.value} acknowledgement target set to {mode.value}",
        )
    await _render(callback, status)


@router.callback_query(AckCB.filter(F.action == "policy"), IsAdmin())
async def prompt_policy(callback: CallbackQuery, callback_data: AckCB) -> None:
    await render(
        callback,
        t.POLICY_PROMPT,
        dispatch_policy_picker(OrderStatus(callback_data.status)),
    )


@router.callback_query(AckCB.filter(F.action == "set_policy"), IsAdmin())
async def set_policy(callback: CallbackQuery, callback_data: AckCB) -> None:
    status = OrderStatus(callback_data.status)
    policy = DispatchPolicy(callback_data.arg)
    async with session_scope() as session:
        await AcknowledgementRepository(session).update_config(status, dispatch_policy=policy)
        await AuditRepository(session).log(
            AuditEvent.REACTION_CONFIGURATION_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"{status.value} acknowledgement policy set to {policy.value}",
        )
    await _render(callback, status)


@router.callback_query(AckCB.filter(F.action == "test"), IsAdmin())
async def prompt_test(callback: CallbackQuery, callback_data: AckCB, state: FSMContext) -> None:
    await state.set_state(TestAckReaction.waiting_for_target)
    await state.update_data(status=callback_data.status)
    await render(
        callback,
        t.TEST_REACTION_PROMPT,
        back_keyboard("reactions"),
    )


@router.message(TestAckReaction.waiting_for_target, IsAdmin())
async def run_test(message: Message, state: FSMContext, services: Services) -> None:
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer(t.TEST_REACTION_FORMAT)
        return
    try:
        chat_id, message_id = int(parts[0]), int(parts[1])
    except ValueError:
        await message.answer(t.TEST_REACTION_NUMERIC)
        return

    data = await state.get_data()
    status = OrderStatus(data["status"])
    async with session_scope() as session:
        config = await AcknowledgementRepository(session).get_config(status)
        reaction = config.reaction_value
    if not reaction:
        await message.answer(t.TEST_REACTION_NO_EMOJI)
        return

    ok, detail = await services.acknowledgements.test_reaction(chat_id, message_id, reaction)
    await state.clear()
    await message.answer(
        t.TEST_REACTION_RESULT.format(icon="✅" if ok else "❌", detail=detail),
        reply_markup=reactions_menu(),
    )
