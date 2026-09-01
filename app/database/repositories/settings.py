"""Key/value application settings stored in PostgreSQL.

Everything the admin panel can change lives here rather than in the
environment, so a redeploy never resets configuration.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.database.models import Setting
from app.database.repositories.base import BaseRepository
from app.utils.enums import CounterScope, ResultContentMode, SettingKey

DEFAULTS: dict[str, str] = {
    SettingKey.COUNTER_SCOPE: CounterScope.GLOBAL,
    SettingKey.ORDER_PREFIX: "order",
    SettingKey.ORDER_NUMBER_FORMAT: "{prefix}{number}",
    SettingKey.ADMIN_NOTIFICATIONS_ENABLED: "true",
    SettingKey.ORDER_NUMBER_ENABLED: "false",
    SettingKey.ORDER_NUMBER_LENGTH: "7",
    SettingKey.ORDER_NUMBER_DELETE_INVALID: "true",
    SettingKey.ORDER_NUMBER_REJECT_MESSAGE: (
        "{name} عزیز، شماره سفارش قرار نگرفته یا اشتباه است."
    ),
    SettingKey.RESULT_CONTENT_MODE: ResultContentMode.ORDER_AND_ATTACHMENTS,
    SettingKey.WOO_BASE_URL: "",
    SettingKey.WOO_CONSUMER_KEY: "",
    SettingKey.WOO_CONSUMER_SECRET: "",
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

    async def store_number_length(self) -> int:
        from app.orders.order_number import DEFAULT_LENGTH, clamp_length

        raw = await self.get(SettingKey.ORDER_NUMBER_LENGTH)
        try:
            return clamp_length(int(raw)) if raw else DEFAULT_LENGTH
        except ValueError:
            return DEFAULT_LENGTH

    async def result_content_mode(self) -> ResultContentMode:
        raw = await self.get(SettingKey.RESULT_CONTENT_MODE)
        try:
            return ResultContentMode(raw)
        except ValueError:
            return ResultContentMode.ORDER_AND_ATTACHMENTS

    async def woo_credentials(self) -> tuple[str, str, str]:
        return (
            (await self.get(SettingKey.WOO_BASE_URL) or "").strip().rstrip("/"),
            (await self.get(SettingKey.WOO_CONSUMER_KEY) or "").strip(),
            (await self.get(SettingKey.WOO_CONSUMER_SECRET) or "").strip(),
        )
