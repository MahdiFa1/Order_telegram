"""Success / failure rule configuration."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.admin import texts
from app.bot.filters import IsAdmin
from app.bot.handlers.admin.common import render
from app.bot.keyboards.admin import (
    match_mode_picker,
    rule_reaction_list,
    rules_menu,
    text_pattern_list,
)
from app.bot.keyboards.callbacks import Nav, RuleCB
from app.bot.keyboards.common import back_keyboard
from app.bot.states.admin import AddRuleReaction, AddTextPattern
from app.database.engine import session_scope
from app.database.repositories import AuditRepository, RuleRepository
from app.rules.matching import validate_pattern
from app.utils.enums import AuditEvent, MatchMode, OrderStatus, RuleMode, SignalKey

router = Router(name="admin_rules")


def _section(status: OrderStatus) -> str:
    return "rules_success" if status is OrderStatus.SUCCESS else "rules_failed"


@router.callback_query(Nav.filter(F.section == "rules_success"), IsAdmin())
async def open_success_rules(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_rules(callback, OrderStatus.SUCCESS)


@router.callback_query(Nav.filter(F.section == "rules_failed"), IsAdmin())
async def open_failure_rules(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_rules(callback, OrderStatus.FAILED)


async def _show_rules(callback: CallbackQuery, status: OrderStatus) -> None:
    async with session_scope() as session:
        repo = RuleRepository(session)
        rule = await repo.get_rule(status)
        enabled = {s.signal_key for s in rule.signals if s.enabled}
        patterns = list(rule.text_patterns)
        reactions = list(rule.reactions)
        text = texts.rules_screen(status, rule, enabled, patterns, reactions)
        markup = rules_menu(status, rule, enabled)
    await render(callback, text, markup)


@router.callback_query(RuleCB.filter(F.action == "toggle_rule"), IsAdmin())
async def toggle_rule(callback: CallbackQuery, callback_data: RuleCB) -> None:
    status = OrderStatus(callback_data.status)
    async with session_scope() as session:
        repo = RuleRepository(session)
        rule = await repo.get_rule(status)
        await repo.set_enabled(status, not rule.enabled)
        await AuditRepository(session).log(
            AuditEvent.RULE_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"{status.value} detection {'disabled' if rule.enabled else 'enabled'}",
        )
    await _show_rules(callback, status)


@router.callback_query(RuleCB.filter(F.action == "mode"), IsAdmin())
async def toggle_mode(callback: CallbackQuery, callback_data: RuleCB) -> None:
    status = OrderStatus(callback_data.status)
    async with session_scope() as session:
        repo = RuleRepository(session)
        rule = await repo.get_rule(status)
        enabled = {s.signal_key for s in rule.signals if s.enabled}
        new_mode = RuleMode.ALL if RuleMode(rule.mode) is RuleMode.ANY else RuleMode.ANY
        if new_mode is RuleMode.ALL and not enabled:
            # Configuration validation: ALL with zero signals can never match.
            await callback.answer(
                "Cannot switch to ALL while no signal is enabled — the rule "
                "could never match. Enable at least one signal first.",
                show_alert=True,
            )
            return
        await repo.set_mode(status, new_mode)
        await AuditRepository(session).log(
            AuditEvent.RULE_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"{status.value} mode set to {new_mode.value}",
        )
    await _show_rules(callback, status)


@router.callback_query(RuleCB.filter(F.action == "signal"), IsAdmin())
async def toggle_signal(callback: CallbackQuery, callback_data: RuleCB) -> None:
    status = OrderStatus(callback_data.status)
    try:
        key = SignalKey(callback_data.arg)
    except ValueError:
        await callback.answer("Unknown signal", show_alert=True)
        return
    async with session_scope() as session:
        repo = RuleRepository(session)
        rule = await repo.get_rule(status)
        enabled = {s.signal_key for s in rule.signals if s.enabled}
        turning_off = key.value in enabled
        if turning_off and RuleMode(rule.mode) is RuleMode.ALL and len(enabled) == 1:
            await callback.answer(
                "Mode is ALL: at least one signal must stay enabled.", show_alert=True
            )
            return
        await repo.toggle_signal(status, key)
        await AuditRepository(session).log(
            AuditEvent.RULE_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"{status.value} signal {key.value} "
            f"{'disabled' if turning_off else 'enabled'}",
        )
    await _show_rules(callback, status)


# ---------------------------------------------------------------------------
# Text patterns
# ---------------------------------------------------------------------------
@router.callback_query(RuleCB.filter(F.action == "texts"), IsAdmin())
async def list_texts(callback: CallbackQuery, callback_data: RuleCB, state: FSMContext) -> None:
    await state.clear()
    await _show_texts(callback, OrderStatus(callback_data.status))


async def _show_texts(callback: CallbackQuery, status: OrderStatus) -> None:
    async with session_scope() as session:
        patterns = await RuleRepository(session).list_text_patterns(status)
    lines = [f"📝 <b>{status.value.title()} Text Patterns</b>", ""]
    if patterns:
        for pattern in patterns:
            lines.append(
                f"{'🟢' if pattern.enabled else '🔴'} <code>{pattern.pattern}</code>\n"
                f"    {pattern.match_mode} · "
                f"{'case sensitive' if pattern.case_sensitive else 'case insensitive'}"
            )
    else:
        lines.append("No pattern yet. A reply-text rule needs at least one.")
    await render(callback, "\n".join(lines), text_pattern_list(status, patterns))


@router.callback_query(RuleCB.filter(F.action == "text_add"), IsAdmin())
async def prompt_text(callback: CallbackQuery, callback_data: RuleCB, state: FSMContext) -> None:
    await state.set_state(AddTextPattern.waiting_for_pattern)
    await state.update_data(status=callback_data.status)
    await render(
        callback,
        "Send the text to match.\n\n"
        "Examples: <code>done</code>, <code>انجام شد</code>, <code>ناموفق</code>\n"
        "You will pick the match mode next.",
        back_keyboard(_section(OrderStatus(callback_data.status))),
    )


@router.message(AddTextPattern.waiting_for_pattern, IsAdmin())
async def receive_pattern(message: Message, state: FSMContext) -> None:
    pattern = (message.text or "").strip()
    if not pattern:
        await message.answer("❌ Empty pattern.")
        return
    data = await state.get_data()
    await state.update_data(pattern=pattern)
    await state.set_state(AddTextPattern.waiting_for_mode)
    await message.answer(
        f"Pattern: <code>{pattern}</code>\n\nChoose the match mode:",
        reply_markup=match_mode_picker(OrderStatus(data["status"])),
    )


@router.callback_query(RuleCB.filter(F.action == "text_mode"), IsAdmin())
async def save_pattern(
    callback: CallbackQuery, callback_data: RuleCB, state: FSMContext
) -> None:
    data = await state.get_data()
    pattern = data.get("pattern")
    status = OrderStatus(callback_data.status)
    if not pattern:
        await callback.answer("Start again from ➕ Add Pattern.", show_alert=True)
        await _show_texts(callback, status)
        return
    mode = MatchMode(callback_data.arg)
    error = validate_pattern(pattern, mode)
    if error:
        await callback.answer(error, show_alert=True)
        return
    async with session_scope() as session:
        await RuleRepository(session).add_text_pattern(status, pattern, mode)
        await AuditRepository(session).log(
            AuditEvent.RULE_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"{status.value} text pattern added: {pattern} ({mode.value})",
        )
    await state.clear()
    await _show_texts(callback, status)


@router.callback_query(RuleCB.filter(F.action == "text_toggle"), IsAdmin())
async def toggle_pattern(callback: CallbackQuery, callback_data: RuleCB) -> None:
    async with session_scope() as session:
        await RuleRepository(session).toggle_text_pattern(callback_data.id)
    await _show_texts(callback, OrderStatus(callback_data.status))


@router.callback_query(RuleCB.filter(F.action == "text_del"), IsAdmin())
async def delete_pattern(callback: CallbackQuery, callback_data: RuleCB) -> None:
    async with session_scope() as session:
        await RuleRepository(session).delete_text_pattern(callback_data.id)
        await AuditRepository(session).log(
            AuditEvent.RULE_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"Text pattern #{callback_data.id} deleted",
        )
    await _show_texts(callback, OrderStatus(callback_data.status))


# ---------------------------------------------------------------------------
# Detection reactions
# ---------------------------------------------------------------------------
@router.callback_query(RuleCB.filter(F.action == "reactions"), IsAdmin())
async def list_reactions(
    callback: CallbackQuery, callback_data: RuleCB, state: FSMContext
) -> None:
    await state.clear()
    await _show_reactions(callback, OrderStatus(callback_data.status))


async def _show_reactions(callback: CallbackQuery, status: OrderStatus) -> None:
    async with session_scope() as session:
        reactions = await RuleRepository(session).list_reactions(status)
    lines = [
        f"😀 <b>{status.value.title()} Detection Reactions</b>",
        "",
        "An <b>authorized operator</b> reacting with one of these emoji on an "
        "order message produces a signal. Reactions from anyone else are ignored.",
        "",
    ]
    if reactions:
        lines.extend(
            f"{'🟢' if r.enabled else '🔴'} {r.emoji}" for r in reactions
        )
    else:
        lines.append("None configured yet.")
    await render(callback, "\n".join(lines), rule_reaction_list(status, reactions))


@router.callback_query(RuleCB.filter(F.action == "reaction_add"), IsAdmin())
async def prompt_reaction(
    callback: CallbackQuery, callback_data: RuleCB, state: FSMContext
) -> None:
    await state.set_state(AddRuleReaction.waiting_for_emoji)
    await state.update_data(status=callback_data.status)
    await render(
        callback,
        "Send a single emoji to accept as a detection reaction (for example ✅ or 👍).",
        back_keyboard(_section(OrderStatus(callback_data.status))),
    )


@router.message(AddRuleReaction.waiting_for_emoji, IsAdmin())
async def receive_reaction(message: Message, state: FSMContext) -> None:
    emoji = (message.text or "").strip()
    if not emoji or len(emoji) > 16:
        await message.answer("❌ Send one emoji.")
        return
    data = await state.get_data()
    status = OrderStatus(data["status"])
    async with session_scope() as session:
        await RuleRepository(session).add_reaction(status, emoji)
        await AuditRepository(session).log(
            AuditEvent.RULE_CHANGED,
            actor_user_id=message.from_user.id if message.from_user else None,
            message=f"{status.value} detection reaction added: {emoji}",
        )
        reactions = await RuleRepository(session).list_reactions(status)
    await state.clear()
    await message.answer(
        f"✅ Added {emoji}", reply_markup=rule_reaction_list(status, reactions)
    )


@router.callback_query(RuleCB.filter(F.action == "reaction_toggle"), IsAdmin())
async def toggle_reaction(callback: CallbackQuery, callback_data: RuleCB) -> None:
    async with session_scope() as session:
        await RuleRepository(session).toggle_reaction(callback_data.id)
    await _show_reactions(callback, OrderStatus(callback_data.status))


@router.callback_query(RuleCB.filter(F.action == "reaction_del"), IsAdmin())
async def delete_reaction(callback: CallbackQuery, callback_data: RuleCB) -> None:
    async with session_scope() as session:
        await RuleRepository(session).delete_reaction(callback_data.id)
    await _show_reactions(callback, OrderStatus(callback_data.status))
