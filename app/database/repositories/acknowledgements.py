"""Acknowledgement configuration, dispatch outbox and event log."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from sqlalchemy import func, or_, select, update
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

    async def claim_dispatch(
        self,
        dispatch_id: int,
        *,
        due_at: datetime | None = None,
        max_attempts: int | None = None,
    ) -> ResultDispatch | None:
        """Atomically move a dispatch PENDING/FAILED -> SENDING.

        Only the claiming worker performs the Telegram send, which is what
        makes result delivery exactly-once even under duplicate events,
        concurrent signals or a restart mid-flight.

        ``due_at`` and ``max_attempts`` make the claim respect the retry
        schedule; an admin pressing "try again" passes neither.
        """
        conditions = [
            ResultDispatch.id == dispatch_id,
            ResultDispatch.status.in_([DispatchStatus.PENDING, DispatchStatus.FAILED]),
        ]
        if due_at is not None:
            conditions.append(ResultDispatch.permanent.is_(False))
            conditions.append(
                or_(
                    ResultDispatch.next_attempt_at.is_(None),
                    ResultDispatch.next_attempt_at <= due_at,
                )
            )
        if max_attempts is not None:
            conditions.append(ResultDispatch.attempts < max_attempts)
        result = await self.session.execute(
            update(ResultDispatch)
            .where(*conditions)
            .values(
                status=DispatchStatus.SENDING,
                attempts=ResultDispatch.attempts + 1,
                last_attempt_at=utcnow(),
            )
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
                next_attempt_at=None,
                permanent=False,
                # Whatever alert this row raised is now answered.
                alerted=False,
            )
        )

    async def mark_dispatch_failed(
        self,
        dispatch_id: int,
        error: str,
        *,
        permanent: bool = False,
        next_attempt_at: datetime | None = None,
        alerted: bool | None = None,
    ) -> None:
        values: dict = {
            "status": DispatchStatus.FAILED,
            "error": error[:1000],
            "permanent": permanent,
            "next_attempt_at": next_attempt_at,
        }
        if alerted is not None:
            values["alerted"] = alerted
        await self.session.execute(
            update(ResultDispatch).where(ResultDispatch.id == dispatch_id).values(**values)
        )

    async def reschedule_dispatches(self, order_id: int) -> int:
        """An admin asked for another try: clear the brakes, keep history.

        ``alerted`` is deliberately kept, so an order the admins were warned
        about still produces the "it went through" message.
        """
        result = await self.session.execute(
            update(ResultDispatch)
            .where(
                ResultDispatch.order_id == order_id,
                ResultDispatch.status != DispatchStatus.SENT,
            )
            .values(
                status=DispatchStatus.PENDING,
                attempts=0,
                permanent=False,
                next_attempt_at=None,
            )
        )
        return int(result.rowcount or 0)

    async def due_dispatches(
        self, *, now: datetime, max_attempts: int, limit: int = 50
    ) -> list[ResultDispatch]:
        """Every dispatch the worker may attempt right now, oldest first."""
        result = await self.session.execute(
            select(ResultDispatch)
            .where(
                ResultDispatch.status.in_(
                    [DispatchStatus.PENDING, DispatchStatus.FAILED]
                ),
                ResultDispatch.permanent.is_(False),
                ResultDispatch.attempts < max_attempts,
                or_(
                    ResultDispatch.next_attempt_at.is_(None),
                    ResultDispatch.next_attempt_at <= now,
                ),
            )
            .order_by(ResultDispatch.id)
            .limit(limit)
        )
        return list(result.scalars())

    async def unfinished_dispatches(self, limit: int = 20) -> list[ResultDispatch]:
        """Newest first: what the result queue screen shows an admin."""
        result = await self.session.execute(
            select(ResultDispatch)
            .where(ResultDispatch.status != DispatchStatus.SENT)
            .order_by(ResultDispatch.id.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def release_stale_dispatches(self, older_than: datetime) -> int:
        """Recover rows stuck in SENDING because the process died mid-send."""
        result = await self.session.execute(
            update(ResultDispatch)
            .where(
                ResultDispatch.status == DispatchStatus.SENDING,
                ResultDispatch.updated_at < older_than,
            )
            .values(status=DispatchStatus.PENDING, next_attempt_at=None)
        )
        return int(result.rowcount or 0)

    async def count_failed_dispatches(self) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(ResultDispatch)
            .where(ResultDispatch.status == DispatchStatus.FAILED)
        )
        return int(result.scalar_one())

    async def dispatch_counts(self, max_attempts: int) -> tuple[int, int]:
        """(still to be retried, given up on)."""
        waiting = await self.session.execute(
            select(func.count())
            .select_from(ResultDispatch)
            .where(
                ResultDispatch.status.in_(
                    [DispatchStatus.PENDING, DispatchStatus.FAILED]
                ),
                ResultDispatch.permanent.is_(False),
                ResultDispatch.attempts < max_attempts,
            )
        )
        abandoned = await self.session.execute(
            select(func.count())
            .select_from(ResultDispatch)
            .where(
                ResultDispatch.status == DispatchStatus.FAILED,
                or_(
                    ResultDispatch.permanent.is_(True),
                    ResultDispatch.attempts >= max_attempts,
                ),
            )
        )
        return int(waiting.scalar_one()), int(abandoned.scalar_one())

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

    async def claim_acknowledgement(
        self, order_id: int, *, due_at: datetime | None = None
    ) -> bool:
        """Atomically move the order's acknowledgement PENDING/FAILED -> APPLYING.

        ``due_at`` leaves a reaction that is waiting out its backoff alone;
        the attempt budget is checked by the service, which reads it from the
        per-status configuration.
        """
        conditions = [
            Order.id == order_id,
            Order.acknowledgement_status.in_(
                [AcknowledgementStatus.PENDING, AcknowledgementStatus.FAILED]
            ),
        ]
        if due_at is not None:
            conditions.append(Order.acknowledgement_permanent.is_(False))
            conditions.append(
                or_(
                    Order.acknowledgement_next_attempt_at.is_(None),
                    Order.acknowledgement_next_attempt_at <= due_at,
                )
            )
        result = await self.session.execute(
            update(Order)
            .where(*conditions)
            .values(
                acknowledgement_status=AcknowledgementStatus.APPLYING,
                acknowledgement_attempts=Order.acknowledgement_attempts + 1,
            )
            .returning(Order.id)
        )
        return result.scalar_one_or_none() is not None

    async def reschedule_acknowledgement(self, order_id: int) -> None:
        """An admin asked for another try at the reaction."""
        await self.session.execute(
            update(Order)
            .where(
                Order.id == order_id,
                Order.acknowledgement_status == AcknowledgementStatus.FAILED,
            )
            .values(
                acknowledgement_status=AcknowledgementStatus.PENDING,
                acknowledgement_attempts=0,
                acknowledgement_permanent=False,
                acknowledgement_next_attempt_at=None,
            )
        )

    async def unfinished_acknowledgements(self, limit: int = 10) -> list[Order]:
        """Newest first: reactions the queue screen still owes an operator."""
        result = await self.session.execute(
            select(Order)
            .where(
                Order.acknowledgement_status.in_(
                    [AcknowledgementStatus.FAILED, AcknowledgementStatus.APPLYING]
                )
            )
            .order_by(Order.id.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def due_acknowledgements(
        self, *, now: datetime, limit: int = 50
    ) -> list[Order]:
        """Failed reactions whose backoff has expired.

        Only FAILED ones: a reaction still PENDING is waiting for its result
        to reach the destination, and that gate is re-checked right after
        every dispatch attempt rather than on a clock of its own.
        """
        result = await self.session.execute(
            select(Order)
            .where(
                Order.acknowledgement_status == AcknowledgementStatus.FAILED,
                Order.acknowledgement_permanent.is_(False),
                or_(
                    Order.acknowledgement_next_attempt_at.is_(None),
                    Order.acknowledgement_next_attempt_at <= now,
                ),
            )
            .order_by(Order.id)
            .limit(limit)
        )
        return list(result.scalars())

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
