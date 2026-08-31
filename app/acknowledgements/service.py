"""Acknowledgement reactions.

Ordering guarantee (the whole point of this module)::

    detect status -> persist status -> send to result destination
                  -> Telegram confirms -> mark dispatch SENT
                  -> apply acknowledgement reaction

The reaction is the operator-visible statement "your action was fully
processed", so it is applied only after the result actually left the bot.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.acknowledgements.targeting import AckTarget, resolve_target
from app.config import Settings
from app.database.engine import session_scope
from app.database.repositories import (
    AcknowledgementRepository,
    AuditRepository,
    OrderRepository,
)
from app.dispatch.service import policy_satisfied
from app.telegram.errors import describe
from app.telegram.gateway import TelegramGateway
from app.utils.enums import (
    AcknowledgementStatus,
    AcknowledgementTargetMode,
    AuditEvent,
    DispatchPolicy,
    OrderStatus,
    ReactionType,
)
from app.utils.logging import get_logger
from app.utils.time import utcnow

logger = get_logger(__name__)


@dataclass(slots=True)
class AckOutcome:
    status: AcknowledgementStatus
    reason: str
    target: AckTarget | None = None


class AcknowledgementService:
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

    async def prepare(self, order_id: int, status: OrderStatus) -> AcknowledgementStatus:
        """Arm (or explicitly skip) the acknowledgement for a finalised order."""
        async with session_scope() as session:
            acks = AcknowledgementRepository(session)
            config = await acks.get_config(status)
            if not config.enabled or not config.reaction_value:
                await acks.set_ack_status(order_id, AcknowledgementStatus.NOT_REQUIRED)
                return AcknowledgementStatus.NOT_REQUIRED
            await acks.set_ack_status(
                order_id,
                AcknowledgementStatus.PENDING,
                acknowledgement_reaction=config.reaction_value,
            )
            return AcknowledgementStatus.PENDING

    async def process(self, order_id: int) -> AckOutcome:
        """Apply the acknowledgement if -- and only if -- the gate is open."""
        async with session_scope() as session:
            orders = OrderRepository(session)
            acks = AcknowledgementRepository(session)
            order = await orders.get(order_id)
            if order is None:
                return AckOutcome(AcknowledgementStatus.NOT_REQUIRED, "order not found")

            status = OrderStatus(order.status)
            if not status.is_terminal:
                # CONFLICT and PENDING orders never receive an acknowledgement.
                return AckOutcome(
                    AcknowledgementStatus.NOT_REQUIRED, f"order is {status.value}"
                )

            current = AcknowledgementStatus(order.acknowledgement_status)
            if current is AcknowledgementStatus.APPLIED:
                # Idempotency: a restart or a duplicate event must not react twice.
                return AckOutcome(current, "already applied")
            if current is AcknowledgementStatus.NOT_REQUIRED:
                return AckOutcome(current, "acknowledgement disabled")

            config = await acks.get_config(status)
            if not config.enabled or not config.reaction_value:
                await acks.set_ack_status(order_id, AcknowledgementStatus.NOT_REQUIRED)
                return AckOutcome(
                    AcknowledgementStatus.NOT_REQUIRED, "acknowledgement disabled"
                )

            if (
                current is AcknowledgementStatus.FAILED
                and not config.retry_enabled
            ):
                return AckOutcome(current, "retry disabled")
            if (
                current is AcknowledgementStatus.FAILED
                and order.acknowledgement_attempts >= config.max_retry_count
            ):
                return AckOutcome(current, "retry budget exhausted")

            dispatches = await acks.list_dispatches(order_id)
            policy = DispatchPolicy(config.dispatch_policy)
            gate_open, gate_reason = policy_satisfied(dispatches, policy)
            if not gate_open:
                # Requirement: never acknowledge an order whose result did not
                # actually reach its destination.
                await AuditRepository(session).log(
                    AuditEvent.ACKNOWLEDGEMENT_SKIPPED,
                    order_id=order_id,
                    level="WARNING",
                    message=f"Acknowledgement withheld: {gate_reason}",
                    data={"policy": policy.value},
                )
                return AckOutcome(AcknowledgementStatus.PENDING, gate_reason)

            target = await resolve_target(
                session, order, AcknowledgementTargetMode(config.target_mode)
            )
            if target is None:
                await acks.set_ack_status(
                    order_id,
                    AcknowledgementStatus.FAILED,
                    acknowledgement_error="no valid acknowledgement target message",
                )
                await acks.log_event(
                    order_id=order_id,
                    order_status=status,
                    result="FAILED",
                    reaction=config.reaction_value,
                    target_mode=config.target_mode,
                    error="no valid target message",
                )
                await AuditRepository(session).log(
                    AuditEvent.ACKNOWLEDGEMENT_FAILED,
                    order_id=order_id,
                    level="ERROR",
                    message="No valid acknowledgement target message",
                )
                return AckOutcome(AcknowledgementStatus.FAILED, "no target message")

            reaction_value = config.reaction_value
            reaction_type = ReactionType(config.reaction_type)
            target_mode = config.target_mode
            max_retry = config.max_retry_count

        # Claim in its own transaction: only the claimer performs the API call.
        async with session_scope() as session:
            if not await AcknowledgementRepository(session).claim_acknowledgement(order_id):
                return AckOutcome(AcknowledgementStatus.APPLYING, "claimed by another worker")

        try:
            await self.gateway.set_reaction(
                target.chat_id, target.message_id, reaction_value, reaction_type
            )
        except Exception as error:  # noqa: BLE001
            detail = describe(error)
            logger.warning(
                "acknowledgement_failed",
                order_id=order_id,
                chat_id=target.chat_id,
                message_id=target.message_id,
                reaction=reaction_value,
                error=detail,
            )
            async with session_scope() as session:
                acks = AcknowledgementRepository(session)
                order = await OrderRepository(session).get(order_id)
                attempt = order.acknowledgement_attempts if order else 1
                # The order keeps its terminal status and its SENT dispatch:
                # a failed reaction never rolls anything back.
                await acks.set_ack_status(
                    order_id,
                    AcknowledgementStatus.FAILED,
                    acknowledgement_chat_id=target.chat_id,
                    acknowledgement_message_id=target.message_id,
                    acknowledgement_error=detail[:1000],
                )
                await acks.log_event(
                    order_id=order_id,
                    order_status=status,
                    result="FAILED",
                    chat_id=target.chat_id,
                    message_id=target.message_id,
                    reaction=reaction_value,
                    target_mode=target_mode,
                    attempt=attempt,
                    error=detail,
                )
                await AuditRepository(session).log(
                    AuditEvent.ACKNOWLEDGEMENT_FAILED,
                    order_id=order_id,
                    chat_id=target.chat_id,
                    message_id=target.message_id,
                    level="ERROR",
                    message=f"Acknowledgement reaction failed: {detail}",
                    data={"reaction": reaction_value, "attempt": attempt},
                )
            if self.notifier is not None:
                await self.notifier.acknowledgement_failed(order_id, detail)
            return AckOutcome(AcknowledgementStatus.FAILED, detail, target)

        async with session_scope() as session:
            acks = AcknowledgementRepository(session)
            await acks.set_ack_status(
                order_id,
                AcknowledgementStatus.APPLIED,
                acknowledgement_reaction=reaction_value,
                acknowledgement_chat_id=target.chat_id,
                acknowledgement_message_id=target.message_id,
                acknowledgement_applied_at=utcnow(),
                acknowledgement_error=None,
            )
            await acks.log_event(
                order_id=order_id,
                order_status=status,
                result="APPLIED",
                chat_id=target.chat_id,
                message_id=target.message_id,
                reaction=reaction_value,
                target_mode=target_mode,
            )
            await AuditRepository(session).log(
                AuditEvent.ACKNOWLEDGEMENT_APPLIED,
                order_id=order_id,
                chat_id=target.chat_id,
                message_id=target.message_id,
                message=f"Acknowledgement {reaction_value} applied",
                data={"target": target.resolved_from, "target_mode": target_mode},
            )
        logger.info(
            "acknowledgement_applied",
            order_id=order_id,
            chat_id=target.chat_id,
            message_id=target.message_id,
            reaction=reaction_value,
            target=target.resolved_from,
        )
        return AckOutcome(AcknowledgementStatus.APPLIED, "applied", target)

    async def test_reaction(
        self, chat_id: int, message_id: int, reaction: str
    ) -> tuple[bool, str]:
        """Admin-panel helper: try the configured emoji in a real chat."""
        try:
            await self.gateway.set_reaction(chat_id, message_id, reaction, retry=False)
        except Exception as error:  # noqa: BLE001
            return False, describe(error)
        return True, "reaction applied"
