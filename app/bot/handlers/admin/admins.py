"""Admin account management.

Only a Super Admin may change who has access. Super admins listed in
``SUPERADMIN_IDS`` are marked 🔒 and cannot be demoted, disabled or removed
from the panel, which makes a lockout impossible.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.admin import strings as t
from app.bot.filters import IsAdmin, IsSuperAdmin
from app.bot.handlers.admin.common import render
from app.bot.keyboards.admin import admin_detail, admin_list
from app.bot.keyboards.callbacks import AdminCB
from app.bot.keyboards.common import back_keyboard
from app.bot.states.admin import AddAdmin
from app.database.engine import session_scope
from app.database.repositories import AdminRepository, AuditRepository
from app.utils.enums import AdminRole, AuditEvent

router = Router(name="admin_admins")

_LIST_HEADER = t.ADMINS_HEADER


@router.callback_query(AdminCB.filter(F.action == "list"), IsAdmin())
async def list_admins(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_list(callback)


async def _show_list(callback: CallbackQuery) -> None:
    async with session_scope() as session:
        admins = await AdminRepository(session).list_all()
    lines = [_LIST_HEADER]
    for admin in admins:
        name = admin.display_name or (
            f"@{admin.username}" if admin.username else str(admin.telegram_user_id)
        )
        lines.append(
            f"{'👑' if admin.role == AdminRole.SUPER_ADMIN else '👮'} "
            f"{'🟢' if admin.enabled else '🔴'} <b>{name}</b>\n"
            f"    <code>{admin.telegram_user_id}</code>"
            f"{' 🔒' if admin.from_env else ''}"
        )
    await render(callback, "\n".join(lines), admin_list(admins))


@router.callback_query(AdminCB.filter(F.action == "view"), IsAdmin())
async def view_admin(callback: CallbackQuery, callback_data: AdminCB) -> None:
    await _render_detail(callback, callback_data.id)


async def _render_detail(callback: CallbackQuery, admin_id: int) -> None:
    async with session_scope() as session:
        admin = await AdminRepository(session).get_by_user_id_pk(admin_id)
    if admin is None:
        await callback.answer(t.NOT_FOUND, show_alert=True)
        return
    name = admin.display_name or (
        f"@{admin.username}" if admin.username else str(admin.telegram_user_id)
    )
    is_super = admin.role == AdminRole.SUPER_ADMIN
    detail = t.ADMIN_DETAIL.format(
        badge="👑" if is_super else "👮",
        name=name,
        user_id=admin.telegram_user_id,
        role=t.ROLE_SUPER_ADMIN if is_super else t.ROLE_ADMIN,
        status=t.toggle_text(admin.enabled),
        source=t.ADMIN_SOURCE_ENV if admin.from_env else t.ADMIN_SOURCE_PANEL,
    )
    await render(callback, detail, admin_detail(admin))


@router.callback_query(AdminCB.filter(F.action == "add"), IsSuperAdmin())
async def prompt_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddAdmin.waiting_for_user_id)
    await render(
        callback,
        t.ADD_ADMIN_PROMPT,
        back_keyboard("settings"),
    )


@router.message(AddAdmin.waiting_for_user_id, IsSuperAdmin())
async def receive_user_id(message: Message, state: FSMContext) -> None:
    try:
        user_id = int((message.text or "").strip())
    except ValueError:
        await message.answer(t.ADD_OPERATOR_INVALID)
        return
    async with session_scope() as session:
        repo = AdminRepository(session)
        await repo.add(user_id, AdminRole.ADMIN)
        await AuditRepository(session).log(
            AuditEvent.CONFIGURATION_CHANGED,
            actor_user_id=message.from_user.id if message.from_user else None,
            message=f"Admin added: {user_id}",
        )
        admins = await repo.list_all()
    await state.clear()
    await message.answer(
        t.ADMIN_ADDED.format(user_id=user_id),
        reply_markup=admin_list(admins),
    )


@router.callback_query(AdminCB.filter(F.action == "role"), IsSuperAdmin())
async def toggle_role(callback: CallbackQuery, callback_data: AdminCB) -> None:
    async with session_scope() as session:
        repo = AdminRepository(session)
        admin = await repo.get_by_user_id_pk(callback_data.id)
        if admin is None:
            await callback.answer(t.NOT_FOUND, show_alert=True)
            return
        if admin.from_env:
            await callback.answer(t.ADMIN_LOCKED_ROLE, show_alert=True)
            return
        admin.role = (
            AdminRole.ADMIN
            if admin.role == AdminRole.SUPER_ADMIN
            else AdminRole.SUPER_ADMIN
        )
        await AuditRepository(session).log(
            AuditEvent.CONFIGURATION_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"Admin {admin.telegram_user_id} role set to {admin.role}",
        )
    await _render_detail(callback, callback_data.id)


@router.callback_query(AdminCB.filter(F.action == "toggle"), IsSuperAdmin())
async def toggle_enabled(callback: CallbackQuery, callback_data: AdminCB) -> None:
    async with session_scope() as session:
        admin = await AdminRepository(session).get_by_user_id_pk(callback_data.id)
        if admin is None:
            await callback.answer(t.NOT_FOUND, show_alert=True)
            return
        if admin.from_env:
            await callback.answer(t.ADMIN_LOCKED_DISABLE, show_alert=True)
            return
        admin.enabled = not admin.enabled
    await _render_detail(callback, callback_data.id)


@router.callback_query(AdminCB.filter(F.action == "delete"), IsSuperAdmin())
async def delete_admin(callback: CallbackQuery, callback_data: AdminCB) -> None:
    async with session_scope() as session:
        repo = AdminRepository(session)
        removed = await repo.delete(callback_data.id)
        if not removed:
            await callback.answer(t.ADMIN_LOCKED_DELETE, show_alert=True)
            return
        await AuditRepository(session).log(
            AuditEvent.CONFIGURATION_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"Admin #{callback_data.id} removed",
        )
    await _show_list(callback)


@router.callback_query(AdminCB.filter(), IsAdmin())
async def super_admin_only(callback: CallbackQuery) -> None:
    """Any admin action not caught above needs Super Admin rights."""
    await callback.answer(t.SUPER_ADMIN_ONLY, show_alert=True)
