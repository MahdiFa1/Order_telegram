"""Source channels, work groups and result destinations."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.admin import strings as t
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
    "source": t.SOURCES_TITLE,
    "workgroup": t.WORKGROUPS_TITLE,
    "dest": t.DESTINATIONS_TITLE,
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
        header = t.DESTINATIONS_FOR.format(status=t.status_name(OrderStatus(arg)))
    if not entities:
        return f"{header}{t.CHAT_LIST_EMPTY}"
    lines = [header, ""]
    for entity in entities:
        title = entity.title or (
            f"@{entity.username}" if entity.username else str(entity.chat_id)
        )
        extra = ""
        if kind == "dest":
            extra = f" · {t.LABEL_REQUIRED if entity.required else t.LABEL_OPTIONAL}"
            if entity.is_primary:
                extra += f" · {t.LABEL_PRIMARY}"
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
        t.DESTINATIONS_INTRO,
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
        t.ADD_CHAT_PROMPT,
        back_keyboard(_SECTION_FOR_KIND[callback_data.kind]),
    )


@router.message(AddChat.waiting_for_chat_id, IsAdmin())
async def receive_chat_id(message: Message, state: FSMContext, services: Services) -> None:
    data = await state.get_data()
    kind = data.get("kind", "source")
    arg = data.get("arg", "")
    chat_id = parse_chat_id(message.text or "")
    if chat_id is None:
        await message.answer(t.ADD_CHAT_INVALID)
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
            probe_note = t.CHAT_ALLOWED_REACTIONS.format(
                reactions=" ".join(available[:12]) or t.CHAT_NONE
            )
    except Exception as error:  # noqa: BLE001 - surfaced to the admin
        probe_note = t.CHAT_PROBE_FAILED.format(error=error)

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
        t.CHAT_SAVED.format(title=title or chat_id) + probe_note
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
        await callback.answer(t.NOT_FOUND, show_alert=True)
        return
    back = (
        f"{_SECTION_FOR_KIND[callback_data.kind]}"
        if callback_data.kind != "dest"
        else "destinations"
    )
    title = entity.title or (f"@{entity.username}" if entity.username else str(entity.chat_id))
    detail = t.CHAT_DETAIL.format(
        title=title,
        chat_id=entity.chat_id,
        username=("@" + entity.username) if entity.username else t.DASH,
        status=t.toggle_text(entity.enabled),
        created=t.fa_digits(f"{entity.created_at:%Y-%m-%d %H:%M}"),
    )
    if callback_data.kind == "dest":
        detail += t.CHAT_DETAIL_DESTINATION_EXTRA.format(
            required=t.yes_no(entity.required), primary=t.yes_no(entity.is_primary)
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
            reactions_note = t.CHAT_REACTIONS_ALL
        else:
            reactions_note = t.CHAT_REACTIONS_LIST.format(
                reactions=" ".join(available[:16]) or t.CHAT_NONE
            )
    except Exception:  # noqa: BLE001
        pass
    note = t.ACCESS_TEST_RESULT.format(
        icon="✅" if ok else "❌", detail=detail
    ) + reactions_note
    await _render_detail(callback, callback_data, note)


@router.callback_query(ChatCB.filter(F.action == "edit"), IsAdmin())
async def prompt_edit(callback: CallbackQuery, callback_data: ChatCB, state: FSMContext) -> None:
    await state.set_state(EditChat.waiting_for_title)
    await state.update_data(
        kind=callback_data.kind, entity_id=callback_data.id, arg=callback_data.arg
    )
    await render(
        callback,
        t.EDIT_TITLE_PROMPT,
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
        t.CONFIRM_DELETE_CHAT,
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
