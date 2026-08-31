"""Admin notifications for operationally important failures."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.database.engine import session_scope
from app.database.repositories import AdminRepository, AuditRepository, OrderRepository
from app.telegram.gateway import TelegramGateway
from app.utils.enums import SettingKey
from app.utils.logging import get_logger

logger = get_logger(__name__)


class AdminNotifier:
    """Sends alerts to every enabled admin, with per-key rate limiting."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        gateway: TelegramGateway,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.gateway = gateway
        self.settings = settings

    async def _send(self, key: str, text: str) -> None:
        async with session_scope() as session:
            from app.database.repositories import SettingRepository

            if not await SettingRepository(session).get_bool(
                SettingKey.ADMIN_NOTIFICATIONS_ENABLED, default=True
            ):
                return
            # Spam protection: identical alerts are throttled per key.
            if not await AuditRepository(session).should_notify(
                key, self.settings.admin_notification_cooldown
            ):
                return
            admins = await AdminRepository(session).list_enabled()
            recipients = [a.telegram_user_id for a in admins]

        for user_id in recipients:
            try:
                await self.gateway.send_text(user_id, text)
            except Exception as error:  # noqa: BLE001 - an unreachable admin is not fatal
                logger.warning("admin_notify_failed", admin=user_id, error=str(error))

    async def _display_number(self, order_id: int) -> str:
        async with session_scope() as session:
            order = await OrderRepository(session).get(order_id)
            return order.display_number if order else f"#{order_id}"

    async def dispatch_failed(self, order_id: int, chat_id: int, reason: str) -> None:
        number = await self._display_number(order_id)
        await self._send(
            f"dispatch_failed:{chat_id}",
            f"⚠️ Order {number}\n\nResult dispatch failed.\n\n"
            f"Destination:\n{chat_id}\n\nReason:\n{reason}",
        )

    async def acknowledgement_failed(self, order_id: int, reason: str) -> None:
        number = await self._display_number(order_id)
        await self._send(
            "acknowledgement_failed",
            f"⚠️ Order {number}\n\nResult successfully sent, "
            f"but acknowledgement reaction failed.\n\nReason:\n{reason}",
        )

    async def conflict_detected(self, order_id: int) -> None:
        number = await self._display_number(order_id)
        await self._send(
            f"conflict:{order_id}",
            f"⚠️ Order {number}\n\nSuccess and failure rules matched at the same time.\n"
            f"The order is on hold in CONFLICT: nothing was dispatched and no "
            f"acknowledgement was applied.\n\nResolve it from "
            f"⚙️ Admin Panel → 🔎 Find Order.",
        )

    async def route_failed(self, order_id: int, reason: str) -> None:
        number = await self._display_number(order_id)
        await self._send(
            "route_failed",
            f"⚠️ Order {number}\n\nDelivery to the work group failed.\n\nReason:\n{reason}",
        )
