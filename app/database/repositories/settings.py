"""Key/value application settings stored in PostgreSQL.

Everything the admin panel can change lives here rather than in the
environment, so a redeploy never resets configuration.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.database.models import Setting
from app.database.repositories.base import BaseRepository
from app.utils.enums import CounterScope, SettingKey

DEFAULTS: dict[str, str] = {
    SettingKey.COUNTER_SCOPE: CounterScope.GLOBAL,
    SettingKey.ORDER_PREFIX: "order",
    SettingKey.ORDER_NUMBER_FORMAT: "{prefix}{number}",
    SettingKey.ADMIN_NOTIFICATIONS_ENABLED: "true",
}


class SettingRepository(BaseRepository):
    async def get(self, key: str, default: str | None = None) -> str | None:
        result = await self.session.execute(select(Setting).where(Setting.key == key))
        setting = result.scalar_one_or_none()
        if setting is None or setting.value is None:
            if default is not None:
                return default
            return DEFAULTS.get(key)
        return setting.value

    async def get_bool(self, key: str, default: bool = False) -> bool:
        raw = await self.get(key)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    async def set(self, key: str, value: str) -> None:
        stmt = (
            insert(Setting)
            .values(key=key, value=value)
            .on_conflict_do_update(index_elements=[Setting.key], set_={"value": value})
        )
        await self.session.execute(stmt)

    async def all(self) -> dict[str, str | None]:
        result = await self.session.execute(select(Setting))
        stored = {s.key: s.value for s in result.scalars()}
        return {**DEFAULTS, **stored}

    async def counter_scope(self) -> CounterScope:
        raw = await self.get(SettingKey.COUNTER_SCOPE) or CounterScope.GLOBAL
        try:
            return CounterScope(raw)
        except ValueError:
            return CounterScope.GLOBAL

    async def order_prefix(self) -> str:
        return await self.get(SettingKey.ORDER_PREFIX) or "order"

    async def order_number_format(self) -> str:
        return await self.get(SettingKey.ORDER_NUMBER_FORMAT) or "{prefix}{number}"
