"""Admin and operator repositories."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.database.models import Admin, Operator, OperatorWorkGroup, WorkGroup
from app.database.repositories.base import BaseRepository
from app.utils.enums import AdminRole


class AdminRepository(BaseRepository):
    async def get_by_user_id(self, telegram_user_id: int) -> Admin | None:
        result = await self.session.execute(
            select(Admin).where(Admin.telegram_user_id == telegram_user_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[Admin]:
        result = await self.session.execute(select(Admin).order_by(Admin.id))
        return list(result.scalars())

    async def list_enabled(self) -> list[Admin]:
        result = await self.session.execute(
            select(Admin).where(Admin.enabled.is_(True)).order_by(Admin.id)
        )
        return list(result.scalars())

    async def upsert_super_admin(self, telegram_user_id: int) -> Admin:
        """Bootstrap a super admin coming from ``SUPERADMIN_IDS``.

        A super admin defined in the environment can never be demoted or
        disabled from inside the panel, which prevents an operator lockout.
        """
        stmt = (
            insert(Admin)
            .values(
                telegram_user_id=telegram_user_id,
                role=AdminRole.SUPER_ADMIN,
                enabled=True,
                from_env=True,
            )
            .on_conflict_do_update(
                index_elements=[Admin.telegram_user_id],
                set_={"role": AdminRole.SUPER_ADMIN, "enabled": True, "from_env": True},
            )
            .returning(Admin)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def add(
        self,
        telegram_user_id: int,
        role: AdminRole = AdminRole.ADMIN,
        username: str | None = None,
        display_name: str | None = None,
    ) -> Admin:
        stmt = (
            insert(Admin)
            .values(
                telegram_user_id=telegram_user_id,
                role=role,
                username=username,
                display_name=display_name,
                enabled=True,
            )
            .on_conflict_do_update(
                index_elements=[Admin.telegram_user_id],
                set_={"role": role, "enabled": True},
            )
            .returning(Admin)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def delete(self, admin_id: int) -> bool:
        admin = await self.session.get(Admin, admin_id)
        if admin is None or admin.from_env:
            return False
        await self.session.delete(admin)
        return True

    async def touch_identity(
        self, telegram_user_id: int, username: str | None, display_name: str | None
    ) -> None:
        admin = await self.get_by_user_id(telegram_user_id)
        if admin is None:
            return
        if username and admin.username != username:
            admin.username = username
        if display_name and admin.display_name != display_name:
            admin.display_name = display_name


class OperatorRepository(BaseRepository):
    async def get(self, operator_id: int) -> Operator | None:
        return await self.session.get(Operator, operator_id)

    async def get_by_user_id(self, telegram_user_id: int) -> Operator | None:
        result = await self.session.execute(
            select(Operator).where(Operator.telegram_user_id == telegram_user_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[Operator]:
        result = await self.session.execute(select(Operator).order_by(Operator.id))
        return list(result.scalars())

    async def count_enabled(self) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(Operator).where(Operator.enabled.is_(True))
        )
        return int(result.scalar_one())

    async def add(
        self,
        telegram_user_id: int,
        username: str | None = None,
        display_name: str | None = None,
    ) -> Operator:
        stmt = (
            insert(Operator)
            .values(
                telegram_user_id=telegram_user_id,
                username=username,
                display_name=display_name,
                enabled=True,
                all_work_groups=True,
            )
            .on_conflict_do_update(
                index_elements=[Operator.telegram_user_id], set_={"enabled": True}
            )
            .returning(Operator)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def delete(self, operator_id: int) -> bool:
        operator = await self.session.get(Operator, operator_id)
        if operator is None:
            return False
        await self.session.delete(operator)
        return True

    async def set_enabled(self, operator_id: int, enabled: bool) -> Operator | None:
        operator = await self.session.get(Operator, operator_id)
        if operator is not None:
            operator.enabled = enabled
        return operator

    async def assign_work_group(self, operator_id: int, work_group_id: int) -> None:
        stmt = (
            insert(OperatorWorkGroup)
            .values(operator_id=operator_id, work_group_id=work_group_id)
            .on_conflict_do_nothing(index_elements=["operator_id", "work_group_id"])
        )
        await self.session.execute(stmt)

    async def unassign_work_group(self, operator_id: int, work_group_id: int) -> None:
        result = await self.session.execute(
            select(OperatorWorkGroup).where(
                OperatorWorkGroup.operator_id == operator_id,
                OperatorWorkGroup.work_group_id == work_group_id,
            )
        )
        assignment = result.scalar_one_or_none()
        if assignment is not None:
            await self.session.delete(assignment)

    async def is_authorized_in_chat(self, telegram_user_id: int, chat_id: int) -> bool:
        """True when the user may drive order status inside ``chat_id``."""
        operator = await self.get_by_user_id(telegram_user_id)
        if operator is None or not operator.enabled:
            return False
        if operator.all_work_groups:
            return True
        result = await self.session.execute(
            select(func.count())
            .select_from(OperatorWorkGroup)
            .join(WorkGroup, WorkGroup.id == OperatorWorkGroup.work_group_id)
            .where(
                OperatorWorkGroup.operator_id == operator.id,
                WorkGroup.chat_id == chat_id,
            )
        )
        return int(result.scalar_one()) > 0
