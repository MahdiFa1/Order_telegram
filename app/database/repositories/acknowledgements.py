"""Acknowledgement configuration, dispatch outbox and event log."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.database.models import (
    AcknowledgementConfig,
    AcknowledgementEvent,
    Order,
    ResultDestination,
    ResultDispatch,
)
from app.database.repositories.base import BaseRepository
from app.utils.enums import (
    AcknowledgementStatus,
    AcknowledgementTargetMode,
    DispatchPolicy,
    DispatchStatus,
    OrderStatus,
    ReactionType,
)
from app.utils.time import utcnow


class AcknowledgementRepository(BaseRepository):
    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    async def get_config(self, status: OrderStatus) -> AcknowledgementConfig:
        result = await self.session.execute(
            select(AcknowledgementConfig).where(AcknowledgementConfig.status == status)
        )
        config = result.scalar_one_or_none()
        if config is not None:
            return config
        await self.session.execute(
            insert(AcknowledgementConfig)
            .values(
                status=status,
                enabled=False,
                reaction_type=ReactionType.EMOJI,
                reaction_value=None,
                target_mode=AcknowledgementTargetMode.SMART,
                dispatch_policy=DispatchPolicy.ALL_REQUIRED_DESTINATIONS,
                retry_enabled=True,
                max_retry_count=3,
            )
            .on_conflict_do_nothing(index_elements=[AcknowledgementConfig.status])
        )
        await self.session.flush()
        return await self.get_config(status)

    async def update_config(self, status: OrderStatus, **fields) -> AcknowledgementConfig:
        config = await self.get_config(status)
        for key, value in fields.items():
            setattr(config, key, value)
        await self.session.flush()
        return config

    # ------------------------------------------------------------------
    # Result dispatch outbox
    # ------------------------------------------------------------------
    async def ensure_dispatches(
        self, order: Order, destinations: Sequence[ResultDestination]
    ) -> list[ResultDispatch]:
        """Create one PENDING outbox row per destination. Idempotent."""
        for destination in destinations:
            await self.session.execute(
                insert(ResultDispatch)
                .values(
                    order_id=order.id,
                    destination_id=destination.id,
                    status=DispatchStatus.PENDING,
                    order_status=order.status,
                    required=destination.required,
                    is_primary=destination.is_primary,
                    chat_id=destination.chat_id,
                )
                .on_conflict_do_nothing(
                    index_elements=[ResultDispatch.order_id, ResultDispatch.destination_id]
                )
            )
        await self.session.flush()
        return await self.list_dispatches(order.id)

    async def list_dispatches(self, order_id: int) -> list[ResultDispatch]:
        result = await self.session.execute(
            select(ResultDispatch)
            .where(ResultDispatch.order_id == order_id)
            .order_by(ResultDispatch.id)
        )
        return list(result.scalars())

    async def claim_dispatch(self, dispatch_id: int) -> ResultDispatch | None:
        """Atomically move a dispatch PENDING/FAILED -> SENDING.

        Only the claiming worker performs the Telegram send, which is what
        makes result delivery exactly-once even under duplicate events,
        concurrent signals or a restart mid-flight.
        """
        result = await self.session.execute(
            update(ResultDispatch)
            .where(
                ResultDispatch.id == dispatch_id,
                ResultDispatch.status.in_([DispatchStatus.PENDING, DispatchStatus.FAILED]),
            )
            .values(status=DispatchStatus.SENDING, attempts=ResultDispatch.attempts + 1)
            .returning(ResultDispatch.id)
        )
        if result.scalar_one_or_none() is None:
            return None
        return await self.session.get(ResultDispatch, dispatch_id)

    async def mark_dispatch_sent(self, dispatch_id: int, message_id: int | None) -> None:
        await self.session.execute(
            update(ResultDispatch)
            .where(ResultDispatch.id == dispatch_id)
            .values(
                status=DispatchStatus.SENT,
                sent_message_id=message_id,
                sent_at=utcnow(),
                error=None,
            )
        )

    async def mark_dispatch_failed(self, dispatch_id: int, error: str) -> None:
        await self.session.execute(
            update(ResultDispatch)
            .where(ResultDispatch.id == dispatch_id)
            .values(status=DispatchStatus.FAILED, error=error[:1000])
        )

    async def release_stale_dispatches(self, older_than: datetime) -> int:
        """Recover rows stuck in SENDING because the process died mid-send."""
        result = await self.session.execute(
            update(ResultDispatch)
            .where(
                ResultDispatch.status == DispatchStatus.SENDING,
                ResultDispatch.updated_at < older_than,
            )
            .values(status=DispatchStatus.PENDING)
        )
        return int(result.rowcount or 0)

    async def count_failed_dispatches(self) -> int:
        from sqlalchemy import func

        result = await self.session.execute(
            select(func.count())
            .select_from(ResultDispatch)
            .where(ResultDispatch.status == DispatchStatus.FAILED)
        )
        return int(result.scalar_one())

    # ------------------------------------------------------------------
    # Acknowledgement state on the order
    # ------------------------------------------------------------------
    async def set_ack_status(
        self, order_id: int, status: AcknowledgementStatus, **fields
    ) -> None:
        await self.session.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(acknowledgement_status=status, **fields)
        )

    async def claim_acknowledgement(self, order_id: int) -> bool:
        """Atomically move the order's acknowledgement PENDING/FAILED -> APPLYING."""
        result = await self.session.execute(
            update(Order)
            .where(
                Order.id == order_id,
                Order.acknowledgement_status.in_(
                    [AcknowledgementStatus.PENDING, AcknowledgementStatus.FAILED]
                ),
            )
            .values(
                acknowledgement_status=AcknowledgementStatus.APPLYING,
                acknowledgement_attempts=Order.acknowledgement_attempts + 1,
            )
            .returning(Order.id)
        )
        return result.scalar_one_or_none() is not None

    async def release_stale_acknowledgements(self, older_than: datetime) -> int:
        result = await self.session.execute(
            update(Order)
            .where(
                Order.acknowledgement_status == AcknowledgementStatus.APPLYING,
                Order.updated_at < older_than,
            )
            .values(acknowledgement_status=AcknowledgementStatus.PENDING)
        )
        return int(result.rowcount or 0)

    async def count_failed_acknowledgements(self) -> int:
        from sqlalchemy import func

        result = await self.session.execute(
            select(func.count())
            .select_from(Order)
            .where(Order.acknowledgement_status == AcknowledgementStatus.FAILED)
        )
        return int(result.scalar_one())

    async def log_event(
        self,
        *,
        order_id: int,
        order_status: OrderStatus,
        result: str,
        chat_id: int | None = None,
        message_id: int | None = None,
        reaction: str | None = None,
        target_mode: str | None = None,
        attempt: int = 1,
        error: str | None = None,
    ) -> None:
        self.session.add(
            AcknowledgementEvent(
                order_id=order_id,
                order_status=order_status,
                result=result,
                chat_id=chat_id,
                message_id=message_id,
                reaction=reaction,
                target_mode=target_mode,
                attempt=attempt,
                error=error[:1000] if error else None,
            )
        )

    async def list_events(self, order_id: int) -> list[AcknowledgementEvent]:
        result = await self.session.execute(
            select(AcknowledgementEvent)
            .where(AcknowledgementEvent.order_id == order_id)
            .order_by(AcknowledgementEvent.id)
        )
        return list(result.scalars())
