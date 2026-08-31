"""Result dispatch: sending a finalised order to its result destinations.

Exactly-once delivery is achieved with an outbox:

1. one ``result_dispatches`` row per ``(order, destination)`` -- a unique
   constraint makes duplicates impossible;
2. a row is *claimed* (``PENDING/FAILED -> SENDING``) with a conditional
   ``UPDATE ... RETURNING`` before the Telegram call, so only one worker ever
   sends it, no matter how many signals or duplicate events arrive;
3. the Telegram call happens outside any transaction, so no database lock is
   held across a network round trip.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.database.engine import session_scope
from app.database.repositories import (
    AcknowledgementRepository,
    AuditRepository,
    OrderRepository,
    ResultDestinationRepository,
)
from app.telegram.composer import compose
from app.telegram.gateway import TelegramGateway
from app.utils.enums import (
    AuditEvent,
    DispatchPolicy,
    DispatchStatus,
    OrderDispatchState,
    OrderStatus,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class DispatchOutcome:
    total: int
    sent: int
    failed: int
    state: OrderDispatchState

    @property
    def all_sent(self) -> bool:
        return self.total > 0 and self.failed == 0 and self.sent == self.total


class DispatchService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        gateway: TelegramGateway,
        settings: Settings,
        notifier=None,
    ) -> None:
        self.session_factory = session_factory
        self.gateway = gateway
        self.settings = settings
        self.notifier = notifier

    async def prepare(self, order_id: int, status: OrderStatus) -> int:
        """Create the outbox rows for a freshly finalised order."""
        async with session_scope() as session:
            orders = OrderRepository(session)
            order = await orders.get(order_id)
            if order is None:
                return 0
            destinations = await ResultDestinationRepository(session).list_for_status(
                status, only_enabled=True
            )
            if not destinations:
                await orders.set_dispatch_state(order_id, OrderDispatchState.NOT_REQUIRED)
                await AuditRepository(session).log(
                    AuditEvent.RESULT_DISPATCH_ATTEMPTED,
                    order_id=order_id,
                    level="WARNING",
                    message=f"No enabled {status.value} destination configured",
                )
                return 0
            await AcknowledgementRepository(session).ensure_dispatches(order, destinations)
            await orders.set_dispatch_state(order_id, OrderDispatchState.PENDING)
            return len(destinations)

    async def process(self, order_id: int) -> DispatchOutcome:
        """Send every outstanding dispatch of an order and refresh its state."""
        async with session_scope() as session:
            dispatches = await AcknowledgementRepository(session).list_dispatches(order_id)
            pending = [
                d.id
                for d in dispatches
                if d.status in (DispatchStatus.PENDING, DispatchStatus.FAILED)
            ]

        for dispatch_id in pending:
            await self._send_one(order_id, dispatch_id)

        return await self.refresh_state(order_id)

    async def _send_one(self, order_id: int, dispatch_id: int) -> None:
        async with session_scope() as session:
            claimed = await AcknowledgementRepository(session).claim_dispatch(dispatch_id)
            if claimed is None:
                return
            chat_id = claimed.chat_id
            attempts = claimed.attempts

        try:
            async with session_scope() as session:
                orders = OrderRepository(session)
                order = await orders.get(order_id)
                if order is None:
                    return
                source_messages = await orders.list_source_messages(order_id)
                composed = compose(
                    order.display_number,
                    source_messages,
                    source_chat_id=order.source_chat_id,
                )
                display_number = order.display_number
                order_status = OrderStatus(order.status)

            if composed.is_empty:
                raise RuntimeError("no stored source message to send")

            message_ids = await self.gateway.send_composed(chat_id, composed)
        except Exception as error:  # noqa: BLE001 - persisted, never fatal
            logger.warning(
                "result_dispatch_failed",
                order_id=order_id,
                dispatch_id=dispatch_id,
                chat_id=chat_id,
                error=str(error),
            )
            async with session_scope() as session:
                await AcknowledgementRepository(session).mark_dispatch_failed(
                    dispatch_id, str(error)
                )
                await AuditRepository(session).log(
                    AuditEvent.RESULT_DISPATCH_FAILED,
                    order_id=order_id,
                    chat_id=chat_id,
                    level="ERROR",
                    message=f"Result dispatch failed: {error}",
                    data={"dispatch_id": dispatch_id, "attempt": attempts},
                )
            if self.notifier is not None:
                await self.notifier.dispatch_failed(order_id, chat_id, str(error))
            return

        async with session_scope() as session:
            await AcknowledgementRepository(session).mark_dispatch_sent(
                dispatch_id, message_ids[0] if message_ids else None
            )
            await AuditRepository(session).log(
                AuditEvent.RESULT_DISPATCH_SUCCEEDED,
                order_id=order_id,
                chat_id=chat_id,
                message_id=message_ids[0] if message_ids else None,
                message=f"Order {display_number} sent to {order_status.value} destination",
                data={"dispatch_id": dispatch_id, "message_ids": message_ids},
            )
        logger.info(
            "result_dispatch_sent",
            order_id=order_id,
            dispatch_id=dispatch_id,
            chat_id=chat_id,
        )

    async def refresh_state(self, order_id: int) -> DispatchOutcome:
        async with session_scope() as session:
            orders = OrderRepository(session)
            dispatches = await AcknowledgementRepository(session).list_dispatches(order_id)
            total = len(dispatches)
            sent = sum(1 for d in dispatches if d.status == DispatchStatus.SENT)
            failed = sum(1 for d in dispatches if d.status == DispatchStatus.FAILED)

            if total == 0:
                state = OrderDispatchState.NOT_REQUIRED
            elif sent == total:
                state = OrderDispatchState.SENT
            elif sent == 0 and failed:
                state = OrderDispatchState.FAILED
            elif sent:
                state = OrderDispatchState.PARTIAL
            else:
                state = OrderDispatchState.PENDING

            await orders.set_dispatch_state(order_id, state)
            return DispatchOutcome(total=total, sent=sent, failed=failed, state=state)


def policy_satisfied(dispatches, policy: DispatchPolicy) -> tuple[bool, str]:
    """Decide whether the acknowledgement gate is open.

    ``ALL_REQUIRED_DESTINATIONS`` (the default) demands that every destination
    flagged *required* reported ``SENT``. With no destination configured at
    all there is nothing to confirm, so the gate is vacuously open -- the
    admin panel warns about that combination instead.
    """
    if not dispatches:
        return True, "no result destination configured"

    if policy is DispatchPolicy.ANY_DESTINATION:
        ok = any(d.status == DispatchStatus.SENT for d in dispatches)
        return ok, "at least one destination sent" if ok else "no destination sent yet"

    if policy is DispatchPolicy.PRIMARY_DESTINATION:
        primary = [d for d in dispatches if d.is_primary] or dispatches
        ok = all(d.status == DispatchStatus.SENT for d in primary)
        return ok, "primary destination sent" if ok else "primary destination not sent"

    required = [d for d in dispatches if d.required]
    if not required:
        required = dispatches
    outstanding = [d for d in required if d.status != DispatchStatus.SENT]
    if outstanding:
        return False, (
            f"{len(outstanding)} required destination(s) not sent "
            f"({', '.join(str(d.chat_id) for d in outstanding)})"
        )
    return True, "all required destinations sent"
