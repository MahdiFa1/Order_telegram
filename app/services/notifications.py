"""Admin notifications for operationally important failures."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.database.engine import session_scope
from app.database.repositories import (
    AdminRepository,
    AuditRepository,
    OperatorRepository,
    OrderRepository,
    ResultDestinationRepository,
    RouteRepository,
    SourceChannelRepository,
    WorkGroupRepository,
)
from app.telegram.gateway import TelegramGateway
from app.utils.enums import OrderStatus, SettingKey
from app.utils.time import local_now
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

    async def startup_completed(self, username: str | None, bot_id: int) -> None:
        """Tell the admins the bot is live, and what is still unconfigured.

        This is the answer to "I deployed it, is it actually running?" -- a
        message arriving in Telegram proves the token, the network, the
        database and the admin list are all correct at once.
        """
        async with session_scope() as session:
            sources = await SourceChannelRepository(session).count_enabled()
            work_groups = await WorkGroupRepository(session).count_enabled()
            operators = await OperatorRepository(session).count_enabled()
            routes = len(
                [r for r in await RouteRepository(session).list_all() if r.enabled]
            )
            destinations = ResultDestinationRepository(session)
            success_targets = len(
                await destinations.list_for_status(OrderStatus.SUCCESS, only_enabled=True)
            )
            failure_targets = len(
                await destinations.list_for_status(OrderStatus.FAILED, only_enabled=True)
            )
            pending = await OrderRepository(session).count_pending()
            recipients = [a.telegram_user_id for a in await AdminRepository(session).list_enabled()]

        def mark(value: int) -> str:
            return "✅" if value else "⚠️"

        # Orders cannot flow at all until these four exist.
        blocking = [
            name
            for name, value in (
                ("a source channel", sources),
                ("a work group", work_groups),
                ("a route between them", routes),
                ("an operator", operators),
            )
            if not value
        ]

        lines = [
            "✅ <b>Bot started successfully</b>",
            "",
            f"Bot: @{username} (<code>{bot_id}</code>)" if username else f"Bot id: {bot_id}",
            "Database: connected",
            f"Local time: {local_now():%Y-%m-%d %H:%M} ({self.settings.timezone})",
            "",
            "<b>Configuration</b>",
            f"{mark(sources)} Source channels: {sources}",
            f"{mark(work_groups)} Work groups: {work_groups}",
            f"{mark(routes)} Routes: {routes}",
            f"{mark(operators)} Operators: {operators}",
            f"{mark(success_targets)} Success destinations: {success_targets}",
            f"{mark(failure_targets)} Failure destinations: {failure_targets}",
        ]
        if pending:
            lines.append(f"⏳ Pending orders carried over: {pending}")

        lines.append("")
        if blocking:
            lines.append(
                "⚠️ <b>Not ready yet.</b> Orders cannot flow until you add "
                + ", ".join(blocking)
                + "."
            )
            lines.append("Send /start to open the admin panel and set them up.")
        else:
            lines.append("Everything required is configured. Send /start to manage it.")

        text = "\n".join(lines)
        for user_id in recipients:
            try:
                await self.gateway.send_text(user_id, text)
            except Exception as error:  # noqa: BLE001 - never block startup
                logger.warning(
                    "startup_notify_failed", admin=user_id, error=str(error)
                )

    async def route_failed(self, order_id: int, reason: str) -> None:
        number = await self._display_number(order_id)
        await self._send(
            "route_failed",
            f"⚠️ Order {number}\n\nDelivery to the work group failed.\n\nReason:\n{reason}",
        )
