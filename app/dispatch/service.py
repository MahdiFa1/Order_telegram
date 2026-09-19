"""Result dispatch: sending a finalised order to its result destinations.

Exactly-once delivery is achieved with an outbox:

1. one ``result_dispatches`` row per ``(order, destination)`` -- a unique
   constraint makes duplicates impossible;
2. a row is *claimed* (``PENDING/FAILED -> SENDING``) with a conditional
   ``UPDATE ... RETURNING`` before the Telegram call, so only one worker ever
   sends it, no matter how many signals or duplicate events arrive;
3. the Telegram call happens outside any transaction, so no database lock is
   held across a network round trip.

A send that fails is not the end of the story either. Telegram can be
unreachable, rate limiting or briefly broken, and the order is finished, so
no further event would ever retry it. Such a row is scheduled for another
attempt -- 2, 4, 8 … minutes later, up to the configured budget -- and the
admins hear about it only once nothing is left to try. A failure Telegram
calls final (the bot was removed from the chat, the topic is closed) skips
the schedule and is reported at once, because repeating it cannot help.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.database.engine import session_scope
from app.database.repositories import (
    AcknowledgementRepository,
    AttachmentRepository,
    AuditRepository,
    OrderRepository,
    ResultConfigRepository,
    ResultDestinationRepository,
    SettingRepository,
)
from app.dispatch.policy import TelegramRetryPolicy, load_telegram_policy
from app.telegram.composer import compose
from app.telegram.errors import is_retryable
from app.telegram.gateway import TelegramGateway
from app.utils.enums import (
    AuditEvent,
    ResultContentMode,
    DispatchPolicy,
    DispatchStatus,
    OrderDispatchState,
    OrderStatus,
    RetryAlertMode,
)
from app.utils.logging import get_logger
from app.utils.time import utcnow

logger = get_logger(__name__)

#: A row left in SENDING for longer than this belongs to a process that died
#: mid-send; a live send is bounded by the gateway's own retry budget, which
#: is far shorter.
STALE_AFTER = timedelta(minutes=20)


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
            destinations = await ResultDestinationRepository(session).list_for_source(
                status, order.source_channel_id, only_enabled=True
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

    async def process(self, order_id: int, *, force: bool = False) -> DispatchOutcome:
        """Send every outstanding dispatch of an order and refresh its state.

        ``force`` is the admin pressing "try again": it ignores both the
        backoff and the "permanent" verdict, because the admin may well have
        just re-added the bot to the destination chat.
        """
        async with session_scope() as session:
            policy = await load_telegram_policy(session)
            dispatches = await AcknowledgementRepository(session).list_dispatches(order_id)
            pending = [
                d.id
                for d in dispatches
                if d.status in (DispatchStatus.PENDING, DispatchStatus.FAILED)
            ]

        for dispatch_id in pending:
            await self._send_one(order_id, dispatch_id, policy, force=force)

        return await self.refresh_state(order_id)

    async def retry_due(self) -> list[int]:
        """Attempt every dispatch whose backoff has expired.

        Returns the orders that were touched, so the caller can re-check
        their acknowledgement gate. Used by the retry worker and by startup
        recovery, which face exactly the same question.
        """
        async with session_scope() as session:
            policy = await load_telegram_policy(session)
            acks = AcknowledgementRepository(session)
            released = await acks.release_stale_dispatches(utcnow() - STALE_AFTER)
            # With automatic retry switched off, a budget of one still lets
            # through the rows nobody has attempted even once -- a restart
            # between creating the outbox row and sending, say.
            due = await acks.due_dispatches(
                now=utcnow(),
                max_attempts=policy.max_attempts if policy.enabled else 1,
            )
            work = [(d.order_id, d.id) for d in due]
        if released:
            logger.info("result_dispatches_released", count=released)

        touched: list[int] = []
        for order_id, dispatch_id in work:
            try:
                await self._send_one(order_id, dispatch_id, policy)
            except Exception:  # noqa: BLE001 - one bad row never stops the rest
                logger.exception("result_dispatch_retry_failed", order_id=order_id)
                continue
            if order_id not in touched:
                touched.append(order_id)

        for order_id in touched:
            await self.refresh_state(order_id)
        return touched

    async def retry_now(self, order_id: int) -> DispatchOutcome:
        """An admin asked for this order's results to be sent again."""
        async with session_scope() as session:
            await AcknowledgementRepository(session).reschedule_dispatches(order_id)
        return await self.process(order_id, force=True)

    async def _send_one(
        self,
        order_id: int,
        dispatch_id: int,
        policy: TelegramRetryPolicy,
        *,
        force: bool = False,
    ) -> None:
        async with session_scope() as session:
            claimed = await AcknowledgementRepository(session).claim_dispatch(
                dispatch_id,
                due_at=None if force else utcnow(),
                max_attempts=None if force else policy.max_attempts,
            )
            if claimed is None:
                return
            chat_id = claimed.chat_id
            attempts = claimed.attempts
            already_alerted = claimed.alerted
            # The destination may point at a forum topic rather than the
            # chat's main view.
            destination = await ResultDestinationRepository(session).get(
                claimed.destination_id
            )
            topic_id = destination.topic_id if destination is not None else 0

        try:
            async with session_scope() as session:
                orders = OrderRepository(session)
                order = await orders.get(order_id)
                if order is None:
                    return
                order_status = OrderStatus(order.status)
                display_number = order.display_number

                # Optional trailing line, e.g. "✅ سفارش با موفقیت انجام شد".
                result_config = await ResultConfigRepository(session).get(order_status)
                footer = (
                    result_config.append_text
                    if result_config.append_text_enabled and result_config.append_text
                    else None
                )
                content_mode = await SettingRepository(session).result_content_mode()

                source_messages = await orders.list_source_messages(order_id)
                composed = compose(
                    order.display_number,
                    source_messages,
                    source_chat_id=order.source_chat_id,
                    footer=footer,
                )
                # What the operator sent while working the order. Only the
                # Telegram file ids are stored, never the media itself.
                attachments = await AttachmentRepository(session).list_for_order(order_id)

            send_order = content_mode is ResultContentMode.ORDER_AND_ATTACHMENTS
            if send_order and composed.is_empty:
                raise RuntimeError("no stored source message to send")
            if not send_order and not attachments:
                # Attachments-only was asked for but the operator sent none;
                # fall back to the order so the destination is never empty.
                send_order = True

            message_ids: list[int] = []
            if send_order:
                message_ids.extend(
                    await self.gateway.send_composed(chat_id, composed, topic_id)
                )
            if attachments:
                message_ids.extend(
                    await self.gateway.send_attachments(
                        chat_id,
                        attachments,
                        caption=None if send_order else footer,
                        topic_id=topic_id,
                    )
                )
        except Exception as error:  # noqa: BLE001 - persisted, never fatal
            await self._failed(
                order_id,
                dispatch_id,
                chat_id,
                error,
                attempts=attempts,
                policy=policy,
                already_alerted=already_alerted,
            )
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
                data={
                    "dispatch_id": dispatch_id,
                    "message_ids": message_ids,
                    "attachments": len(attachments),
                },
            )
        logger.info(
            "result_dispatch_sent",
            order_id=order_id,
            dispatch_id=dispatch_id,
            chat_id=chat_id,
            attempts=attempts,
        )
        if already_alerted and self.notifier is not None:
            # The admins were told this order was stuck; close the loop.
            await self.notifier.dispatch_recovered(order_id, chat_id, attempts)

    # ------------------------------------------------------------------
    async def _failed(
        self,
        order_id: int,
        dispatch_id: int,
        chat_id: int,
        error: BaseException,
        *,
        attempts: int,
        policy: TelegramRetryPolicy,
        already_alerted: bool,
    ) -> None:
        """Record the failure, schedule the next attempt, alert if it is over."""
        detail = str(error)
        # A bot kicked from the chat, a closed topic, an order with nothing
        # to send: the same call in ten minutes fails the same way.
        permanent = not is_retryable(error)
        retry_at = (
            utcnow() + policy.delay_after(attempts)
            if not permanent and policy.has_budget_after(attempts)
            else None
        )
        final = retry_at is None
        alert = (
            policy.alert_mode is RetryAlertMode.EVERY_ATTEMPT
            or (final and not already_alerted)
        )

        logger.warning(
            "result_dispatch_failed",
            order_id=order_id,
            dispatch_id=dispatch_id,
            chat_id=chat_id,
            error=detail,
            attempts=attempts,
            permanent=permanent,
            retry_at=retry_at.isoformat() if retry_at else None,
        )
        async with session_scope() as session:
            await AcknowledgementRepository(session).mark_dispatch_failed(
                dispatch_id,
                detail,
                permanent=permanent,
                next_attempt_at=retry_at,
                alerted=already_alerted or alert,
            )
            await AuditRepository(session).log(
                AuditEvent.RESULT_DISPATCH_GAVE_UP if final
                else AuditEvent.RESULT_DISPATCH_RETRY_SCHEDULED,
                order_id=order_id,
                chat_id=chat_id,
                level="ERROR" if final else "WARNING",
                message=f"Result dispatch failed: {detail}",
                data={
                    "dispatch_id": dispatch_id,
                    "attempt": attempts,
                    "max_attempts": policy.max_attempts,
                    "permanent": permanent,
                    "next_attempt_at": retry_at.isoformat() if retry_at else None,
                },
            )
        if alert and self.notifier is not None:
            await self.notifier.dispatch_failed(
                order_id,
                chat_id,
                detail,
                next_attempt=retry_at,
                attempts=attempts,
                max_attempts=policy.max_attempts,
                final=final,
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
