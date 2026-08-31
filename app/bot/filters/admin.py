"""Admin authorisation filters."""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from app.database.engine import session_scope
from app.database.repositories import AdminRepository
from app.utils.enums import AdminRole


async def resolve_role(user_id: int) -> AdminRole | None:
    async with session_scope() as session:
        admin = await AdminRepository(session).get_by_user_id(user_id)
    if admin is None or not admin.enabled:
        return None
    return AdminRole(admin.role)


class IsAdmin(BaseFilter):
    """Passes for any enabled Admin or Super Admin."""

    async def __call__(self, event: Message | CallbackQuery) -> bool | dict:
        user = event.from_user
        if user is None:
            return False
        role = await resolve_role(user.id)
        if role is None or role is AdminRole.OPERATOR:
            return False
        return {"admin_role": role}


class IsSuperAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        if user is None:
            return False
        return await resolve_role(user.id) is AdminRole.SUPER_ADMIN
