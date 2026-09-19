"""Admin notifications for operationally important failures."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.admin import strings as t
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
from app.utils.time import format_local, local_now
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

    async def dispatch_failed(
        self,
        order_id: int,
        chat_id: int,
        reason: str,
        *,
        next_attempt: datetime | None = None,
        attempts: int = 1,
        max_attempts: int = 1,
        final: bool = True,
    ) -> None:
        """Report a result that did not reach its destination.

        Same split as the store: one message says when the bot will try
        again, the other says nothing is left to try.
        """
        number = await self._display_number(order_id)
        fa = t.fa_digits
        if final:
            key = f"dispatch_failed:{order_id}:{chat_id}"
            text = t.NOTIFY_DISPATCH_FAILED.format(
                number=number, chat_id=chat_id, reason=reason, attempts=fa(attempts)
            )
        else:
            key = f"dispatch_failed:{order_id}:{chat_id}:{attempts}"
            text = t.NOTIFY_DISPATCH_RETRYING.format(
                number=number,
                chat_id=chat_id,
                reason=reason,
                attempt=fa(attempts),
                max_attempts=fa(max_attempts),
                next_attempt=fa(format_local(next_attempt, "%H:%M")),
            )
        await self._send(key, text)

    async def dispatch_recovered(
        self, order_id: int, chat_id: int, attempts: int
    ) -> None:
        number = await self._display_number(order_id)
        await self._send(
            f"dispatch_recovered:{order_id}:{chat_id}",
            t.NOTIFY_DISPATCH_RECOVERED.format(
                number=number, chat_id=chat_id, attempts=t.fa_digits(attempts)
            ),
        )

    async def acknowledgement_failed(
        self,
        order_id: int,
        reason: str,
        *,
        next_attempt: datetime | None = None,
        attempts: int = 1,
        max_attempts: int = 1,
        final: bool = True,
    ) -> None:
        number = await self._display_number(order_id)
        fa = t.fa_digits
        if final:
            key = f"acknowledgement_failed:{order_id}"
            text = t.NOTIFY_ACK_FAILED.format(
                number=number, reason=reason, attempts=fa(attempts)
            )
        else:
            key = f"acknowledgement_failed:{order_id}:{attempts}"
            text = t.NOTIFY_ACK_RETRYING.format(
                number=number,
                reason=reason,
                attempt=fa(attempts),
                max_attempts=fa(max_attempts),
                next_attempt=fa(format_local(next_attempt, "%H:%M")),
            )
        await self._send(key, text)

    async def acknowledgement_recovered(self, order_id: int) -> None:
        number = await self._display_number(order_id)
        await self._send(
            f"acknowledgement_recovered:{order_id}",
            t.NOTIFY_ACK_RECOVERED.format(number=number),
        )

    async def conflict_detected(self, order_id: int) -> None:
        number = await self._display_number(order_id)
        await self._send(
            f"conflict:{order_id}", t.NOTIFY_CONFLICT.format(number=number)
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
                (t.STARTUP_MISSING_SOURCE, sources),
                (t.STARTUP_MISSING_WORK_GROUP, work_groups),
                (t.STARTUP_MISSING_ROUTE, routes),
                (t.STARTUP_MISSING_OPERATOR, operators),
            )
            if not value
        ]
        fa = t.fa_digits

        lines = [
            t.STARTUP_TITLE,
            "",
            t.STARTUP_BOT.format(username=username, bot_id=bot_id)
            if username
            else t.STARTUP_BOT_NO_USERNAME.format(bot_id=bot_id),
            t.STARTUP_DATABASE,
            t.STARTUP_TIME.format(
                time=fa(f"{local_now():%Y-%m-%d %H:%M}"),
                timezone=self.settings.timezone,
            ),
            "",
            t.STARTUP_CONFIG_TITLE,
            t.STARTUP_SOURCES.format(mark=mark(sources), count=fa(sources)),
            t.STARTUP_WORK_GROUPS.format(mark=mark(work_groups), count=fa(work_groups)),
            t.STARTUP_ROUTES.format(mark=mark(routes), count=fa(routes)),
            t.STARTUP_OPERATORS.format(mark=mark(operators), count=fa(operators)),
            t.STARTUP_SUCCESS_TARGETS.format(
                mark=mark(success_targets), count=fa(success_targets)
            ),
            t.STARTUP_FAILURE_TARGETS.format(
                mark=mark(failure_targets), count=fa(failure_targets)
            ),
        ]
        if pending:
            lines.append(t.STARTUP_PENDING.format(count=fa(pending)))

        lines.append("")
        if blocking:
            lines.append(t.STARTUP_NOT_READY.format(missing="، ".join(blocking)))
            lines.append(t.STARTUP_NOT_READY_HINT)
        else:
            lines.append(t.STARTUP_READY)

        text = "\n".join(lines)
        for user_id in recipients:
            try:
                await self.gateway.send_text(user_id, text)
            except Exception as error:  # noqa: BLE001 - never block startup
                logger.warning(
                    "startup_notify_failed", admin=user_id, error=str(error)
                )

    async def store_update_failed(
        self,
        order_id: int,
        order_number: str,
        reason: str,
        *,
        next_attempt: datetime | None = None,
        attempts: int = 1,
        max_attempts: int = 1,
        final: bool = True,
    ) -> None:
        """Report a failed store update.

        A failure that will be tried again reads differently from one that
        needs a human: the first says when the bot will try next, the second
        says what the admin has to do. The throttle key carries the order and
        the attempt so one order's alert never swallows another's.
        """
        number = await self._display_number(order_id)
        fa = t.fa_digits
        if final:
            key = f"store_update_failed:{order_id}"
            text = t.NOTIFY_STORE_FAILED.format(
                number=number,
                order_number=order_number,
                reason=reason,
                attempts=fa(attempts),
            )
        else:
            key = f"store_update_failed:{order_id}:{attempts}"
            text = t.NOTIFY_STORE_RETRYING.format(
                number=number,
                order_number=order_number,
                reason=reason,
                attempt=fa(attempts),
                max_attempts=fa(max_attempts),
                next_attempt=fa(format_local(next_attempt, "%H:%M")),
            )
        await self._send(key, text)

    async def store_update_recovered(
        self, order_id: int, order_number: str, attempts: int
    ) -> None:
        """Close the loop on an order the admins were warned about."""
        number = await self._display_number(order_id)
        await self._send(
            f"store_update_recovered:{order_id}",
            t.NOTIFY_STORE_RECOVERED.format(
                number=number,
                order_number=order_number,
                attempts=t.fa_digits(attempts),
            ),
        )

    async def route_failed(self, order_id: int, reason: str) -> None:
        number = await self._display_number(order_id)
        await self._send(
            f"route_failed:{order_id}", t.NOTIFY_ROUTE_FAILED.format(number=number, reason=reason)
        )
