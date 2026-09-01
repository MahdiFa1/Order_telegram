"""Lifecycle reactions on the ORIGINAL source message.

The person who posted the order in the source channel never sees the work
group, so the source message itself is used to report progress back to them:

    received  ->  in progress  ->  success / failed

Telegram allows a bot one reaction per message, so each stage *replaces* the
previous one. The stage actually applied is stored on the order, which makes
a repeated event or a restart a no-op instead of a re-reaction.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.database.engine import session_scope
from app.database.repositories import (
    AuditRepository,
    OrderRepository,
    SourceReactionRepository,
)
from app.telegram.errors import describe
from app.telegram.gateway import TelegramGateway
from app.utils.enums import AuditEvent, OrderStatus, SourceReactionStage
from app.utils.logging import get_logger

logger = get_logger(__name__)

#: Later stages must not be overwritten by an earlier one arriving late.
_STAGE_ORDER: dict[SourceReactionStage, int] = {
    SourceReactionStage.RECEIVED: 1,
    SourceReactionStage.IN_PROGRESS: 2,
    SourceReactionStage.SUCCESS: 3,
    SourceReactionStage.FAILED: 3,
}

STATUS_TO_STAGE: dict[OrderStatus, SourceReactionStage] = {
    OrderStatus.SUCCESS: SourceReactionStage.SUCCESS,
    OrderStatus.FAILED: SourceReactionStage.FAILED,
}


@dataclass(slots=True)
class SourceReactionOutcome:
    applied: bool
    reason: str
    reaction: str | None = None


class SourceReactionService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        gateway: TelegramGateway,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.gateway = gateway
        self.settings = settings

    async def apply(self, order_id: int, stage: SourceReactionStage) -> SourceReactionOutcome:
        async with session_scope() as session:
            order = await OrderRepository(session).get(order_id)
            if order is None:
                return SourceReactionOutcome(False, "order not found")

            config = await SourceReactionRepository(session).get_config(stage)
            if not config.enabled or not config.reaction_value:
                return SourceReactionOutcome(False, f"{stage.value} stage disabled")

            current = order.source_reaction_stage
            if current == stage.value:
                # Idempotent: this exact stage is already showing.
                return SourceReactionOutcome(False, "stage already applied")
            if current and _STAGE_ORDER.get(SourceReactionStage(current), 0) > _STAGE_ORDER[stage]:
                # A late RECEIVED must never undo a SUCCESS already shown.
                return SourceReactionOutcome(False, "a later stage is already applied")

            chat_id = order.source_chat_id
            message_id = order.source_message_id
            reaction = config.reaction_value

        try:
            await self.gateway.set_reaction(chat_id, message_id, reaction)
        except Exception as error:  # noqa: BLE001 - never affects the order
            detail = describe(error)
            logger.warning(
                "source_reaction_failed",
                order_id=order_id,
                stage=stage.value,
                chat_id=chat_id,
                error=detail,
            )
            async with session_scope() as session:
                await AuditRepository(session).log(
                    AuditEvent.SOURCE_REACTION_FAILED,
                    order_id=order_id,
                    chat_id=chat_id,
                    message_id=message_id,
                    level="WARNING",
                    message=f"Source reaction {reaction} ({stage.value}) failed: {detail}",
                )
            return SourceReactionOutcome(False, detail, reaction)

        async with session_scope() as session:
            order = await OrderRepository(session).get(order_id)
            if order is not None:
                order.source_reaction_stage = stage.value
                order.source_reaction_value = reaction
            await AuditRepository(session).log(
                AuditEvent.SOURCE_REACTION_APPLIED,
                order_id=order_id,
                chat_id=chat_id,
                message_id=message_id,
                message=f"Source reaction {reaction} applied at stage {stage.value}",
                data={"stage": stage.value, "reaction": reaction},
            )
        logger.info(
            "source_reaction_applied",
            order_id=order_id,
            stage=stage.value,
            reaction=reaction,
        )
        return SourceReactionOutcome(True, "applied", reaction)

    async def apply_for_status(self, order_id: int, status: OrderStatus) -> SourceReactionOutcome:
        stage = STATUS_TO_STAGE.get(status)
        if stage is None:
            return SourceReactionOutcome(False, f"no stage for {status.value}")
        return await self.apply(order_id, stage)

    async def mark_in_progress(
        self, order_id: int, actor_user_id: int
    ) -> SourceReactionOutcome:
        """Record that an operator picked the order up, then re-react."""
        from app.utils.time import utcnow

        async with session_scope() as session:
            orders = OrderRepository(session)
            order = await orders.get(order_id)
            if order is None:
                return SourceReactionOutcome(False, "order not found")
            if OrderStatus(order.status).is_terminal:
                return SourceReactionOutcome(False, "order already finalised")
            already = order.in_progress_at is not None
            if not already:
                order.in_progress_at = utcnow()
                order.in_progress_by_user_id = actor_user_id
                await AuditRepository(session).log(
                    AuditEvent.ORDER_IN_PROGRESS,
                    order_id=order_id,
                    actor_user_id=actor_user_id,
                    message="Operator marked the order as in progress",
                )

        return await self.apply(order_id, SourceReactionStage.IN_PROGRESS)
