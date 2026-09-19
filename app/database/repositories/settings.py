"""Key/value application settings stored in PostgreSQL.

Everything the admin panel can change lives here rather than in the
environment, so a redeploy never resets configuration.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.database.models import Setting
from app.database.repositories.base import BaseRepository
from app.utils.enums import (
    CounterScope,
    ResultContentMode,
    SettingKey,
    StartupBacklogMode,
    StoreAlertMode,
)

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
    SettingKey.WOO_RETRY_ENABLED: "true",
    SettingKey.WOO_RETRY_MAX_ATTEMPTS: "5",
    SettingKey.WOO_RETRY_BASE_MINUTES: "2",
    SettingKey.WOO_RETRY_MAX_MINUTES: "60",
    SettingKey.WOO_REQUEST_TIMEOUT: "30",
    SettingKey.WOO_QUICK_RETRIES: "2",
    SettingKey.WOO_ALERT_MODE: StoreAlertMode.EXHAUSTED,
    SettingKey.STARTUP_BACKLOG_MODE: StartupBacklogMode.MAX_AGE,
    SettingKey.STARTUP_BACKLOG_MAX_AGE_MINUTES: "15",
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

    async def get_int(self, key: str, default: int, *, low: int, high: int) -> int:
        """A stored number, clamped to the range the panel offers.

        Settings are free text on the way in, so a value saved before the
        bounds changed -- or an empty row -- must never take a background
        worker out of service.
        """
        raw = await self.get(key)
        try:
            return max(low, min(high, int(str(raw).strip())))
        except (TypeError, ValueError):
            return default

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

    async def startup_backlog_mode(self) -> StartupBacklogMode:
        raw = await self.get(SettingKey.STARTUP_BACKLOG_MODE)
        try:
            return StartupBacklogMode(raw)
        except ValueError:
            return StartupBacklogMode.MAX_AGE

    async def startup_backlog_max_age(self) -> int:
        """Minutes, clamped to something a human would actually choose."""
        raw = await self.get(SettingKey.STARTUP_BACKLOG_MAX_AGE_MINUTES)
        try:
            return max(1, min(1440, int(raw)))
        except (TypeError, ValueError):
            return 15

    async def woo_credentials(self) -> tuple[str, str, str]:
        return (
            (await self.get(SettingKey.WOO_BASE_URL) or "").strip().rstrip("/"),
            (await self.get(SettingKey.WOO_CONSUMER_KEY) or "").strip(),
            (await self.get(SettingKey.WOO_CONSUMER_SECRET) or "").strip(),
        )
