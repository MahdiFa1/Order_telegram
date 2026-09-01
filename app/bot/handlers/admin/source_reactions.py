"""Lifecycle reactions placed on the original source message."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.admin import strings as t
from app.bot.filters import IsAdmin
from app.bot.handlers.admin.common import render
from app.bot.keyboards.admin import (
    progress_reaction_list,
    source_reactions_menu,
    source_stage_detail,
)
from app.bot.keyboards.callbacks import Nav, SourceCB
from app.bot.keyboards.common import back_keyboard
from app.database.engine import session_scope
from app.database.repositories import AuditRepository, SourceReactionRepository
from app.utils.enums import AuditEvent, SourceReactionStage

router = Router(name="admin_source_reactions")


class SetSourceReaction(StatesGroup):
    waiting_for_emoji = State()


class AddProgressReaction(StatesGroup):
    waiting_for_emoji = State()


@router.callback_query(Nav.filter(F.section == "source_reactions"), IsAdmin())
async def open_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_menu(callback)


async def _show_menu(callback: CallbackQuery) -> None:
    async with session_scope() as session:
        configs = await SourceReactionRepository(session).all_configs()
    await render(callback, t.SOURCE_REACTIONS_INTRO, source_reactions_menu(configs))


@router.callback_query(SourceCB.filter(F.action == "stage"), IsAdmin())
async def view_stage(callback: CallbackQuery, callback_data: SourceCB) -> None:
    await _show_stage(callback, SourceReactionStage(callback_data.stage))


async def _show_stage(callback: CallbackQuery, stage: SourceReactionStage) -> None:
    async with session_scope() as session:
        config = await SourceReactionRepository(session).get_config(stage)
        text = t.SOURCE_STAGE_SCREEN.format(
            stage=t.SOURCE_STAGE_NAMES.get(stage.value, stage.value),
            help=t.SOURCE_STAGE_HELP.get(stage.value, ""),
            enabled=t.toggle_text(config.enabled),
            reaction=config.reaction_value or t.ACK_NOT_SET,
        )
        markup = source_stage_detail(config)
    await render(callback, text, markup)


@router.callback_query(SourceCB.filter(F.action == "toggle"), IsAdmin())
async def toggle_stage(callback: CallbackQuery, callback_data: SourceCB) -> None:
    stage = SourceReactionStage(callback_data.stage)
    async with session_scope() as session:
        repo = SourceReactionRepository(session)
        config = await repo.get_config(stage)
        if not config.enabled and not config.reaction_value:
            await callback.answer(t.SOURCE_NEEDS_REACTION_FIRST, show_alert=True)
            return
        await repo.update_config(stage, enabled=not config.enabled)
        await AuditRepository(session).log(
            AuditEvent.REACTION_CONFIGURATION_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"Source reaction stage {stage.value} "
            f"{'disabled' if config.enabled else 'enabled'}",
        )
    await _show_stage(callback, stage)


@router.callback_query(SourceCB.filter(F.action == "set"), IsAdmin())
async def prompt_emoji(
    callback: CallbackQuery, callback_data: SourceCB, state: FSMContext
) -> None:
    await state.set_state(SetSourceReaction.waiting_for_emoji)
    await state.update_data(stage=callback_data.stage)
    await render(callback, t.SET_SOURCE_REACTION_PROMPT, back_keyboard("source_reactions"))


@router.message(SetSourceReaction.waiting_for_emoji, IsAdmin())
async def receive_emoji(message: Message, state: FSMContext) -> None:
    emoji = (message.text or "").strip()
    if not emoji or len(emoji) > 16:
        await message.answer(t.REACTION_INVALID)
        return
    data = await state.get_data()
    stage = SourceReactionStage(data["stage"])
    async with session_scope() as session:
        await SourceReactionRepository(session).update_config(stage, reaction_value=emoji)
        await AuditRepository(session).log(
            AuditEvent.REACTION_CONFIGURATION_CHANGED,
            actor_user_id=message.from_user.id if message.from_user else None,
            message=f"Source reaction for {stage.value} set to {emoji}",
        )
        configs = await SourceReactionRepository(session).all_configs()
    await state.clear()
    await message.answer(
        t.SOURCE_REACTION_SAVED.format(
            stage=t.SOURCE_STAGE_NAMES.get(stage.value, stage.value), emoji=emoji
        ),
        reply_markup=source_reactions_menu(configs),
    )


# ---------------------------------------------------------------------------
# Emoji that mean "an operator picked this up"
# ---------------------------------------------------------------------------
@router.callback_query(SourceCB.filter(F.action == "progress"), IsAdmin())
async def list_progress(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_progress(callback)


async def _show_progress(callback: CallbackQuery) -> None:
    async with session_scope() as session:
        reactions = await SourceReactionRepository(session).list_progress_reactions()
    lines = [t.PROGRESS_REACTIONS_TITLE, "", t.PROGRESS_REACTIONS_INTRO, ""]
    if reactions:
        lines.extend(f"{'🟢' if r.enabled else '🔴'} {r.emoji}" for r in reactions)
    else:
        lines.append(t.PROGRESS_REACTIONS_EMPTY)
    await render(callback, "\n".join(lines), progress_reaction_list(reactions))


@router.callback_query(SourceCB.filter(F.action == "p_add"), IsAdmin())
async def prompt_progress(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddProgressReaction.waiting_for_emoji)
    await render(callback, t.ADD_REACTION_PROMPT, back_keyboard("source_reactions"))


@router.message(AddProgressReaction.waiting_for_emoji, IsAdmin())
async def receive_progress(message: Message, state: FSMContext) -> None:
    emoji = (message.text or "").strip()
    if not emoji or len(emoji) > 16:
        await message.answer(t.REACTION_INVALID)
        return
    async with session_scope() as session:
        repo = SourceReactionRepository(session)
        await repo.add_progress_reaction(emoji)
        await AuditRepository(session).log(
            AuditEvent.REACTION_CONFIGURATION_CHANGED,
            actor_user_id=message.from_user.id if message.from_user else None,
            message=f"Progress reaction added: {emoji}",
        )
        reactions = await repo.list_progress_reactions()
    await state.clear()
    await message.answer(
        t.REACTION_ADDED.format(emoji=emoji),
        reply_markup=progress_reaction_list(reactions),
    )


@router.callback_query(SourceCB.filter(F.action == "p_toggle"), IsAdmin())
async def toggle_progress(callback: CallbackQuery, callback_data: SourceCB) -> None:
    async with session_scope() as session:
        await SourceReactionRepository(session).toggle_progress_reaction(callback_data.id)
    await _show_progress(callback)


@router.callback_query(SourceCB.filter(F.action == "p_del"), IsAdmin())
async def delete_progress(callback: CallbackQuery, callback_data: SourceCB) -> None:
    async with session_scope() as session:
        await SourceReactionRepository(session).delete_progress_reaction(callback_data.id)
    await _show_progress(callback)
