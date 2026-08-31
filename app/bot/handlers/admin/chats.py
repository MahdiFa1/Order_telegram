"""Source channels, work groups and result destinations."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.filters import IsAdmin
from app.bot.handlers.admin.common import parse_chat_id, render
from app.bot.keyboards.admin import (
    chat_detail,
    chat_list,
    confirm_delete,
    destinations_menu,
)
from app.bot.keyboards.callbacks import ChatCB, Nav
from app.bot.keyboards.common import back_keyboard
from app.bot.states.admin import AddChat, EditChat
from app.database.engine import session_scope
from app.database.repositories import (
    AuditRepository,
    ResultDestinationRepository,
    SourceChannelRepository,
    WorkGroupRepository,
)
from app.services.container import Services
from app.utils.enums import AuditEvent, OrderStatus

router = Router(name="admin_chats")

_SECTION_FOR_KIND = {"source": "sources", "workgroup": "workgroups", "dest": "destinations"}
_TITLE_FOR_KIND = {
    "source": "📥 Source Channels",
    "workgroup": "👥 Work Groups",
    "dest": "📦 Result Destinations",
}


def _repo(session, kind: str):
    if kind == "source":
        return SourceChannelRepository(session)
    if kind == "workgroup":
        return WorkGroupRepository(session)
    return ResultDestinationRepository(session)


async def _list_entities(session, kind: str, arg: str):
    repo = _repo(session, kind)
    if kind == "dest":
        return await repo.list_for_status(OrderStatus(arg))
    return await repo.list_all()


def _list_text(kind: str, arg: str, entities) -> str:
    header = _TITLE_FOR_KIND[kind]
    if kind == "dest":
        header = f"📦 {OrderStatus(arg).value.title()} Destinations"
    if not entities:
        return f"{header}\n\nNothing configured yet. Use ➕ Add."
    lines = [header, ""]
    for entity in entities:
        title = entity.title or (
            f"@{entity.username}" if entity.username else str(entity.chat_id)
        )
        extra = ""
        if kind == "dest":
            extra = f" · {'required' if entity.required else 'optional'}"
            if entity.is_primary:
                extra += " · ⭐ primary"
        lines.append(
            f"{'🟢' if entity.enabled else '🔴'} <b>{title}</b>\n"
            f"    <code>{entity.chat_id}</code>{extra}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
@router.callback_query(Nav.filter(F.section == "sources"), IsAdmin())
async def open_sources(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_list(callback, "source", "")


@router.callback_query(Nav.filter(F.section == "workgroups"), IsAdmin())
async def open_work_groups(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_list(callback, "workgroup", "")


@router.callback_query(Nav.filter(F.section == "destinations"), IsAdmin())
async def open_destinations(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render(
        callback,
        "📦 <b>Result Destinations</b>\n\n"
        "Where finalised orders are sent. Groups, supergroups and channels are "
        "all supported, and each status can have several destinations.",
        destinations_menu(),
    )


@router.callback_query(ChatCB.filter(F.action == "list"), IsAdmin())
async def list_chats(callback: CallbackQuery, callback_data: ChatCB, state: FSMContext) -> None:
    await state.clear()
    await _show_list(callback, callback_data.kind, callback_data.arg)


async def _show_list(callback: CallbackQuery, kind: str, arg: str) -> None:
    async with session_scope() as session:
        entities = await _list_entities(session, kind, arg)
    back = "main" if kind != "dest" else "destinations"
    await render(callback, _list_text(kind, arg, entities), chat_list(kind, entities, back, arg))


# ---------------------------------------------------------------------------
# Add
# ---------------------------------------------------------------------------
@router.callback_query(ChatCB.filter(F.action == "add"), IsAdmin())
async def prompt_add(callback: CallbackQuery, callback_data: ChatCB, state: FSMContext) -> None:
    await state.set_state(AddChat.waiting_for_chat_id)
    await state.update_data(kind=callback_data.kind, arg=callback_data.arg)
    await render(
        callback,
        "Send the numeric <b>chat ID</b> to add.\n\n"
        "Tip: add the bot to the chat, then run <code>/id</code> inside it, "
        "or forward a message from it to @userinfobot.\n"
        "Channel and supergroup IDs start with <code>-100</code>.",
        back_keyboard(_SECTION_FOR_KIND[callback_data.kind]),
    )


@router.message(AddChat.waiting_for_chat_id, IsAdmin())
async def receive_chat_id(message: Message, state: FSMContext, services: Services) -> None:
    data = await state.get_data()
    kind = data.get("kind", "source")
    arg = data.get("arg", "")
    chat_id = parse_chat_id(message.text or "")
    if chat_id is None:
        await message.answer(
            "❌ That is not a numeric chat ID. Usernames are not accepted here "
            "because they can change; send the numeric ID."
        )
        return

    title: str | None = None
    username: str | None = None
    probe_note = ""
    try:
        info = await services.gateway.get_chat_info(chat_id)
        title = info.get("title")
        username = info.get("username")
        available = info.get("available_reactions")
        if available is not None:
            probe_note = (
                f"\nAllowed reactions in this chat: {' '.join(available[:12]) or 'none'}"
            )
    except Exception as error:  # noqa: BLE001 - surfaced to the admin
        probe_note = (
            f"\n⚠️ Could not read the chat: {error}\n"
            "It was saved anyway — add the bot to the chat and use 🧪 Test Access."
        )

    async with session_scope() as session:
        repo = _repo(session, kind)
        if kind == "dest":
            await repo.add(OrderStatus(arg), chat_id, title, username)
        else:
            await repo.add(chat_id, title, username)
        await AuditRepository(session).log(
            AuditEvent.CONFIGURATION_CHANGED,
            actor_user_id=message.from_user.id if message.from_user else None,
            message=f"{kind} added: {chat_id}",
            data={"kind": kind, "chat_id": chat_id, "status": arg or None},
        )
        entities = await _list_entities(session, kind, arg)

    await state.clear()
    back = "main" if kind != "dest" else "destinations"
    await message.answer(
        f"✅ Saved <b>{title or chat_id}</b>{probe_note}",
    )
    await message.answer(
        _list_text(kind, arg, entities), reply_markup=chat_list(kind, entities, back, arg)
    )


# ---------------------------------------------------------------------------
# View / toggle / test / edit / delete
# ---------------------------------------------------------------------------
@router.callback_query(ChatCB.filter(F.action == "view"), IsAdmin())
async def view_chat(callback: CallbackQuery, callback_data: ChatCB) -> None:
    await _render_detail(callback, callback_data)


async def _render_detail(callback: CallbackQuery, callback_data: ChatCB, note: str = "") -> None:
    async with session_scope() as session:
        entity = await _repo(session, callback_data.kind).get(callback_data.id)
    if entity is None:
        await callback.answer("Not found", show_alert=True)
        return
    back = (
        f"{_SECTION_FOR_KIND[callback_data.kind]}"
        if callback_data.kind != "dest"
        else "destinations"
    )
    title = entity.title or (f"@{entity.username}" if entity.username else str(entity.chat_id))
    detail = (
        f"<b>{title}</b>\n\n"
        f"Chat ID: <code>{entity.chat_id}</code>\n"
        f"Username: {('@' + entity.username) if entity.username else '—'}\n"
        f"Status: {'🟢 Enabled' if entity.enabled else '🔴 Disabled'}\n"
        f"Created: {entity.created_at:%Y-%m-%d %H:%M}\n"
    )
    if callback_data.kind == "dest":
        detail += (
            f"Required: {'YES' if entity.required else 'NO'}\n"
            f"Primary: {'YES' if entity.is_primary else 'NO'}\n"
        )
    if note:
        detail += f"\n{note}"
    await render(callback, detail, chat_detail(callback_data.kind, entity, back, callback_data.arg))


@router.callback_query(ChatCB.filter(F.action == "toggle"), IsAdmin())
async def toggle_chat(callback: CallbackQuery, callback_data: ChatCB) -> None:
    async with session_scope() as session:
        repo = _repo(session, callback_data.kind)
        entity = await repo.get(callback_data.id)
        if entity is None:
            await callback.answer("Not found", show_alert=True)
            return
        await repo.set_enabled(callback_data.id, not entity.enabled)
        await AuditRepository(session).log(
            AuditEvent.CONFIGURATION_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"{callback_data.kind} {entity.chat_id} "
            f"{'disabled' if entity.enabled else 'enabled'}",
        )
    await _render_detail(callback, callback_data)


@router.callback_query(ChatCB.filter(F.action == "required"), IsAdmin())
async def toggle_required(callback: CallbackQuery, callback_data: ChatCB) -> None:
    async with session_scope() as session:
        repo = ResultDestinationRepository(session)
        entity = await repo.get(callback_data.id)
        if entity is None:
            await callback.answer("Not found", show_alert=True)
            return
        await repo.set_required(callback_data.id, not entity.required)
    await _render_detail(callback, callback_data)


@router.callback_query(ChatCB.filter(F.action == "primary"), IsAdmin())
async def make_primary(callback: CallbackQuery, callback_data: ChatCB) -> None:
    async with session_scope() as session:
        await ResultDestinationRepository(session).set_primary(callback_data.id)
    await _render_detail(callback, callback_data)


@router.callback_query(ChatCB.filter(F.action == "test"), IsAdmin())
async def test_access(
    callback: CallbackQuery, callback_data: ChatCB, services: Services
) -> None:
    async with session_scope() as session:
        entity = await _repo(session, callback_data.kind).get(callback_data.id)
    if entity is None:
        await callback.answer("Not found", show_alert=True)
        return
    ok, detail = await services.gateway.check_can_post(entity.chat_id)
    reactions_note = ""
    try:
        info = await services.gateway.get_chat_info(entity.chat_id)
        available = info.get("available_reactions")
        if available is None:
            reactions_note = "\nReactions: all emoji allowed"
        else:
            reactions_note = f"\nReactions allowed: {' '.join(available[:16]) or 'none'}"
    except Exception:  # noqa: BLE001
        pass
    note = f"{'✅' if ok else '❌'} Access test: {detail}{reactions_note}"
    await _render_detail(callback, callback_data, note)


@router.callback_query(ChatCB.filter(F.action == "edit"), IsAdmin())
async def prompt_edit(callback: CallbackQuery, callback_data: ChatCB, state: FSMContext) -> None:
    await state.set_state(EditChat.waiting_for_title)
    await state.update_data(
        kind=callback_data.kind, entity_id=callback_data.id, arg=callback_data.arg
    )
    await render(
        callback,
        "Send the new title for this chat.",
        back_keyboard(_SECTION_FOR_KIND[callback_data.kind]),
    )


@router.message(EditChat.waiting_for_title, IsAdmin())
async def receive_title(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    async with session_scope() as session:
        await _repo(session, data["kind"]).update_meta(
            data["entity_id"], title=(message.text or "").strip()
        )
        entities = await _list_entities(session, data["kind"], data.get("arg", ""))
    await state.clear()
    back = "main" if data["kind"] != "dest" else "destinations"
    await message.answer(
        _list_text(data["kind"], data.get("arg", ""), entities),
        reply_markup=chat_list(data["kind"], entities, back, data.get("arg", "")),
    )


@router.callback_query(ChatCB.filter(F.action == "delete"), IsAdmin())
async def prompt_delete(callback: CallbackQuery, callback_data: ChatCB) -> None:
    back = (
        _SECTION_FOR_KIND[callback_data.kind]
        if callback_data.kind != "dest"
        else "destinations"
    )
    await render(
        callback,
        "⚠️ Delete this entry?\n\nExisting orders keep their history; only the "
        "configuration row is removed.",
        confirm_delete(callback_data.kind, callback_data.id, back, callback_data.arg),
    )


@router.callback_query(ChatCB.filter(F.action == "delete_confirm"), IsAdmin())
async def do_delete(callback: CallbackQuery, callback_data: ChatCB) -> None:
    async with session_scope() as session:
        await _repo(session, callback_data.kind).delete(callback_data.id)
        await AuditRepository(session).log(
            AuditEvent.CONFIGURATION_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"{callback_data.kind} #{callback_data.id} deleted",
        )
    await _show_list(callback, callback_data.kind, callback_data.arg)
