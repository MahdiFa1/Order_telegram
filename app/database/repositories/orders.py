"""Order persistence, including atomic daily-number allocation."""

from __future__ import annotations

import uuid as uuid_module
from datetime import date, datetime
from typing import Any, Sequence

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import selectinload

from app.database.models import (
    DailyCounter,
    Order,
    OrderDelivery,
    OrderDeliveryMessage,
    OrderSignal,
    OrderSourceMessage,
    ProcessedUpdate,
    StatusEvent,
)
from app.database.repositories.base import BaseRepository
from app.utils.enums import CounterScope, DeliveryStatus, OrderStatus
from app.utils.time import utcnow

GLOBAL_SCOPE_KEY = "GLOBAL"


def scope_key_for(scope: CounterScope, source_chat_id: int) -> str:
    if scope is CounterScope.PER_SOURCE:
        return f"SOURCE:{source_chat_id}"
    return GLOBAL_SCOPE_KEY


class CounterRepository(BaseRepository):
    """Allocates the daily order number.

    The whole allocation is a single statement::

        INSERT INTO daily_counters (business_date, scope_key, last_number)
        VALUES (:day, :scope, 1)
        ON CONFLICT (business_date, scope_key)
        DO UPDATE SET last_number = daily_counters.last_number + 1
        RETURNING last_number

    PostgreSQL serialises concurrent writers on the unique index, so two
    orders arriving in the same instant get N and N+1 -- never N twice.
    Because the key is the business date itself, the counter restarts at 1
    on the first order of the next local day with no cron job involved.
    """

    async def allocate(self, business_day: date, scope_key: str) -> int:
        stmt = (
            insert(DailyCounter)
            .values(business_date=business_day, scope_key=scope_key, last_number=1)
            .on_conflict_do_update(
                index_elements=[DailyCounter.business_date, DailyCounter.scope_key],
                set_={"last_number": DailyCounter.last_number + 1},
            )
            .returning(DailyCounter.last_number)
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def peek(self, business_day: date, scope_key: str) -> int:
        result = await self.session.execute(
            select(DailyCounter.last_number).where(
                DailyCounter.business_date == business_day,
                DailyCounter.scope_key == scope_key,
            )
        )
        return int(result.scalar_one_or_none() or 0)


class OrderRepository(BaseRepository):
    # ------------------------------------------------------------------
    # Update-level idempotency
    # ------------------------------------------------------------------
    async def mark_update_processed(self, update_key: str) -> bool:
        """Claim a Telegram update. ``False`` means it was already handled."""
        stmt = (
            insert(ProcessedUpdate)
            .values(update_key=update_key, created_at=utcnow())
            .on_conflict_do_nothing(index_elements=[ProcessedUpdate.update_key])
            .returning(ProcessedUpdate.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def purge_processed_updates(self, older_than: datetime) -> int:
        from sqlalchemy import delete

        result = await self.session.execute(
            delete(ProcessedUpdate).where(ProcessedUpdate.created_at < older_than)
        )
        return int(result.rowcount or 0)

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------
    def _loaded(self, stmt: Select) -> Select:
        return stmt.options(
            selectinload(Order.source_messages),
            selectinload(Order.deliveries).selectinload(OrderDelivery.messages),
            selectinload(Order.signals),
            selectinload(Order.dispatches),
        )

    async def get(self, order_id: int) -> Order | None:
        result = await self.session.execute(
            self._loaded(select(Order).where(Order.id == order_id))
        )
        return result.scalar_one_or_none()

    async def get_by_uuid(self, order_uuid: uuid_module.UUID) -> Order | None:
        result = await self.session.execute(
            self._loaded(select(Order).where(Order.uuid == order_uuid))
        )
        return result.scalar_one_or_none()

    async def get_by_source_message(self, chat_id: int, message_id: int) -> Order | None:
        result = await self.session.execute(
            select(Order).where(
                Order.source_chat_id == chat_id, Order.source_message_id == message_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_media_group(self, chat_id: int, media_group_id: str) -> Order | None:
        result = await self.session.execute(
            select(Order).where(
                Order.source_chat_id == chat_id,
                Order.source_media_group_id == media_group_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_source_message_any(self, chat_id: int, message_id: int) -> Order | None:
        """Resolve an order from *any* of its source messages (album safe)."""
        result = await self.session.execute(
            select(Order)
            .join(OrderSourceMessage, OrderSourceMessage.order_id == Order.id)
            .where(
                OrderSourceMessage.chat_id == chat_id,
                OrderSourceMessage.message_id == message_id,
            )
        )
        return result.scalars().first()

    async def get_by_delivery_message(self, chat_id: int, message_id: int) -> Order | None:
        """Resolve an order from a work-group message the bot posted.

        This -- not the ``orderNN`` text -- is how operator interactions are
        attributed to an order.
        """
        result = await self.session.execute(
            self._loaded(
                select(Order)
                .join(OrderDeliveryMessage, OrderDeliveryMessage.order_id == Order.id)
                .where(
                    OrderDeliveryMessage.chat_id == chat_id,
                    OrderDeliveryMessage.message_id == message_id,
                )
            )
        )
        return result.scalars().first()

    async def get_by_daily_number(
        self, business_day: date, daily_number: int, scope_key: str | None = None
    ) -> list[Order]:
        stmt = select(Order).where(
            Order.business_date == business_day, Order.daily_number == daily_number
        )
        if scope_key is not None:
            stmt = stmt.where(Order.counter_scope_key == scope_key)
        result = await self.session.execute(self._loaded(stmt).order_by(Order.id))
        return list(result.scalars())

    async def lock(self, order_id: int) -> Order | None:
        """Row-level lock used to serialise finalisation across processes."""
        result = await self.session.execute(
            select(Order).where(Order.id == order_id).with_for_update()
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------
    async def create(
        self,
        *,
        business_day: date,
        daily_number: int,
        counter_scope_key: str,
        display_number: str,
        source_channel_id: int | None,
        source_chat_id: int,
        source_message_id: int,
        source_media_group_id: str | None,
    ) -> Order:
        order = Order(
            uuid=uuid_module.uuid4(),
            business_date=business_day,
            daily_number=daily_number,
            counter_scope_key=counter_scope_key,
            display_number=display_number,
            source_channel_id=source_channel_id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            source_media_group_id=source_media_group_id,
            status=OrderStatus.PENDING,
        )
        self.session.add(order)
        await self.session.flush()
        return order

    async def add_source_message(self, order_id: int, **fields: Any) -> OrderSourceMessage | None:
        stmt = (
            insert(OrderSourceMessage)
            .values(order_id=order_id, **fields)
            .on_conflict_do_nothing(
                index_elements=[OrderSourceMessage.chat_id, OrderSourceMessage.message_id]
            )
            .returning(OrderSourceMessage.id)
        )
        result = await self.session.execute(stmt)
        row_id = result.scalar_one_or_none()
        if row_id is None:
            return None
        return await self.session.get(OrderSourceMessage, row_id)

    async def list_source_messages(self, order_id: int) -> list[OrderSourceMessage]:
        result = await self.session.execute(
            select(OrderSourceMessage)
            .where(OrderSourceMessage.order_id == order_id)
            .order_by(OrderSourceMessage.position, OrderSourceMessage.message_id)
        )
        return list(result.scalars())

    # ------------------------------------------------------------------
    # Deliveries
    # ------------------------------------------------------------------
    async def ensure_delivery(
        self, order_id: int, work_group_id: int, chat_id: int
    ) -> OrderDelivery:
        stmt = (
            insert(OrderDelivery)
            .values(
                order_id=order_id,
                work_group_id=work_group_id,
                chat_id=chat_id,
                status=DeliveryStatus.PENDING,
            )
            .on_conflict_do_nothing(
                index_elements=[OrderDelivery.order_id, OrderDelivery.work_group_id]
            )
        )
        await self.session.execute(stmt)
        result = await self.session.execute(
            select(OrderDelivery).where(
                OrderDelivery.order_id == order_id,
                OrderDelivery.work_group_id == work_group_id,
            )
        )
        return result.scalar_one()

    async def claim_delivery(self, delivery_id: int) -> bool:
        """Atomically move a delivery PENDING/FAILED -> SENDING."""
        result = await self.session.execute(
            update(OrderDelivery)
            .where(
                OrderDelivery.id == delivery_id,
                OrderDelivery.status.in_([DeliveryStatus.PENDING, DeliveryStatus.FAILED]),
            )
            .values(status=DeliveryStatus.SENDING, attempts=OrderDelivery.attempts + 1)
            .returning(OrderDelivery.id)
        )
        return result.scalar_one_or_none() is not None

    async def complete_delivery(
        self, delivery_id: int, message_ids: Sequence[int], chat_id: int, order_id: int
    ) -> None:
        for position, message_id in enumerate(message_ids):
            await self.session.execute(
                insert(OrderDeliveryMessage)
                .values(
                    delivery_id=delivery_id,
                    order_id=order_id,
                    chat_id=chat_id,
                    message_id=message_id,
                    is_primary=position == 0,
                    position=position,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        OrderDeliveryMessage.chat_id,
                        OrderDeliveryMessage.message_id,
                    ]
                )
            )
        await self.session.execute(
            update(OrderDelivery)
            .where(OrderDelivery.id == delivery_id)
            .values(status=DeliveryStatus.DELIVERED, delivered_at=utcnow(), error=None)
        )

    async def fail_delivery(self, delivery_id: int, error: str) -> None:
        await self.session.execute(
            update(OrderDelivery)
            .where(OrderDelivery.id == delivery_id)
            .values(status=DeliveryStatus.FAILED, error=error[:1000])
        )

    async def primary_delivery_message(
        self, order_id: int, chat_id: int | None = None
    ) -> OrderDeliveryMessage | None:
        """The message the acknowledgement targets in ORDER_MESSAGE mode."""
        stmt = select(OrderDeliveryMessage).where(OrderDeliveryMessage.order_id == order_id)
        if chat_id is not None:
            stmt = stmt.where(OrderDeliveryMessage.chat_id == chat_id)
        stmt = stmt.order_by(
            OrderDeliveryMessage.is_primary.desc(),
            OrderDeliveryMessage.position,
            OrderDeliveryMessage.id,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def stale_deliveries(self, older_than: datetime) -> list[OrderDelivery]:
        result = await self.session.execute(
            select(OrderDelivery).where(
                OrderDelivery.status == DeliveryStatus.SENDING,
                OrderDelivery.updated_at < older_than,
            )
        )
        return list(result.scalars())

    async def pending_deliveries(self, limit: int = 100) -> list[OrderDelivery]:
        result = await self.session.execute(
            select(OrderDelivery)
            .where(OrderDelivery.status.in_([DeliveryStatus.PENDING, DeliveryStatus.FAILED]))
            .order_by(OrderDelivery.id)
            .limit(limit)
        )
        return list(result.scalars())

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------
    async def record_signal(
        self,
        *,
        order_id: int,
        rule_status: OrderStatus,
        signal_key: str,
        actor_user_id: int | None,
        trigger_type: str | None,
        trigger_chat_id: int | None,
        trigger_message_id: int | None,
        detail: dict | None = None,
    ) -> bool:
        """Persist a signal. Returns ``True`` when it was newly observed."""
        stmt = (
            insert(OrderSignal)
            .values(
                order_id=order_id,
                rule_status=rule_status,
                signal_key=signal_key,
                active=True,
                actor_user_id=actor_user_id,
                trigger_type=trigger_type,
                trigger_chat_id=trigger_chat_id,
                trigger_message_id=trigger_message_id,
                detail=detail,
            )
            .on_conflict_do_update(
                index_elements=[
                    OrderSignal.order_id,
                    OrderSignal.rule_status,
                    OrderSignal.signal_key,
                ],
                set_={
                    "active": True,
                    "actor_user_id": actor_user_id,
                    "trigger_type": trigger_type,
                    "trigger_chat_id": trigger_chat_id,
                    "trigger_message_id": trigger_message_id,
                    "detail": detail,
                },
                where=OrderSignal.active.is_(False),
            )
            .returning(OrderSignal.id)
        )
        result = await self.session.execute(stmt)
        if result.scalar_one_or_none() is not None:
            return True
        # Row existed and was already active -> nothing changed.
        return False

    async def list_signals(self, order_id: int) -> list[OrderSignal]:
        result = await self.session.execute(
            select(OrderSignal)
            .where(OrderSignal.order_id == order_id, OrderSignal.active.is_(True))
            .order_by(OrderSignal.id)
        )
        return list(result.scalars())

    async def deactivate_signal(
        self, order_id: int, rule_status: OrderStatus, signal_key: str
    ) -> None:
        await self.session.execute(
            update(OrderSignal)
            .where(
                OrderSignal.order_id == order_id,
                OrderSignal.rule_status == rule_status,
                OrderSignal.signal_key == signal_key,
            )
            .values(active=False)
        )

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------
    async def transition(
        self,
        *,
        order_id: int,
        expected_statuses: Sequence[OrderStatus],
        new_status: OrderStatus,
        reason: str | None,
        actor_user_id: int | None,
        trigger_type: str | None,
        trigger_chat_id: int | None,
        trigger_message_id: int | None,
    ) -> Order | None:
        """Conditional status change; ``None`` when another worker won the race."""
        values: dict[str, Any] = {"status": new_status}
        if new_status.is_terminal:
            values.update(
                completed_at=utcnow(),
                completed_by_user_id=actor_user_id,
                completion_trigger_type=trigger_type,
                completion_trigger_chat_id=trigger_chat_id,
                completion_trigger_message_id=trigger_message_id,
            )
            if new_status is OrderStatus.SUCCESS:
                values["success_reason"] = reason
            else:
                values["failure_reason"] = reason
        elif new_status is OrderStatus.CONFLICT:
            values.update(
                completion_trigger_type=trigger_type,
                completion_trigger_chat_id=trigger_chat_id,
                completion_trigger_message_id=trigger_message_id,
            )

        result = await self.session.execute(
            update(Order)
            .where(
                Order.id == order_id,
                Order.status.in_([s.value for s in expected_statuses]),
            )
            .values(**values)
            .returning(Order.id, Order.status)
        )
        row = result.first()
        if row is None:
            return None

        self.session.add(
            StatusEvent(
                order_id=order_id,
                from_status=None,
                to_status=new_status,
                reason=reason,
                actor_user_id=actor_user_id,
                trigger_type=trigger_type,
                trigger_chat_id=trigger_chat_id,
                trigger_message_id=trigger_message_id,
            )
        )
        await self.session.flush()
        self.session.expire_all()
        return await self.get(order_id)

    async def add_status_event(self, order_id: int, **fields: Any) -> None:
        self.session.add(StatusEvent(order_id=order_id, **fields))

    async def list_status_events(self, order_id: int) -> list[StatusEvent]:
        result = await self.session.execute(
            select(StatusEvent)
            .where(StatusEvent.order_id == order_id)
            .order_by(StatusEvent.id)
        )
        return list(result.scalars())

    async def set_dispatch_state(self, order_id: int, state: str) -> None:
        await self.session.execute(
            update(Order).where(Order.id == order_id).values(result_dispatch_status=state)
        )

    # ------------------------------------------------------------------
    # Reporting helpers
    # ------------------------------------------------------------------
    async def count_by_status(
        self,
        start: datetime,
        end: datetime,
        source_channel_id: int | None = None,
        operator_user_id: int | None = None,
    ) -> dict[str, int]:
        stmt = (
            select(Order.status, func.count())
            .where(Order.created_at >= start, Order.created_at < end)
            .group_by(Order.status)
        )
        if source_channel_id is not None:
            stmt = stmt.where(Order.source_channel_id == source_channel_id)
        if operator_user_id is not None:
            stmt = stmt.where(Order.completed_by_user_id == operator_user_id)
        result = await self.session.execute(stmt)
        counts = {status.value: 0 for status in OrderStatus}
        for status, count in result.all():
            counts[status] = int(count)
        return counts

    async def average_completion_seconds(
        self,
        start: datetime,
        end: datetime,
        operator_user_id: int | None = None,
    ) -> float | None:
        stmt = select(
            func.avg(
                func.extract("epoch", Order.completed_at)
                - func.extract("epoch", Order.created_at)
            )
        ).where(
            Order.created_at >= start,
            Order.created_at < end,
            Order.completed_at.is_not(None),
        )
        if operator_user_id is not None:
            stmt = stmt.where(Order.completed_by_user_id == operator_user_id)
        result = await self.session.execute(stmt)
        value = result.scalar_one_or_none()
        return float(value) if value is not None else None

    async def operator_breakdown(
        self, start: datetime, end: datetime
    ) -> list[tuple[int, dict[str, int]]]:
        result = await self.session.execute(
            select(Order.completed_by_user_id, Order.status, func.count())
            .where(
                Order.created_at >= start,
                Order.created_at < end,
                Order.completed_by_user_id.is_not(None),
            )
            .group_by(Order.completed_by_user_id, Order.status)
        )
        grouped: dict[int, dict[str, int]] = {}
        for user_id, status, count in result.all():
            grouped.setdefault(user_id, {}).setdefault(status, 0)
            grouped[user_id][status] += int(count)
        return sorted(grouped.items(), key=lambda item: -sum(item[1].values()))

    async def list_by_status(
        self, statuses: Sequence[OrderStatus], limit: int = 20
    ) -> list[Order]:
        result = await self.session.execute(
            self._loaded(
                select(Order)
                .where(Order.status.in_([s.value for s in statuses]))
                .order_by(Order.id.desc())
                .limit(limit)
            )
        )
        return list(result.scalars())

    async def count_pending(self) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(Order).where(Order.status == OrderStatus.PENDING)
        )
        return int(result.scalar_one())

    async def count_conflicts(self) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(Order).where(Order.status == OrderStatus.CONFLICT)
        )
        return int(result.scalar_one())

    async def search(
        self,
        *,
        business_day: date | None = None,
        daily_number: int | None = None,
        display_number: str | None = None,
        limit: int = 20,
    ) -> list[Order]:
        stmt = select(Order)
        if business_day is not None:
            stmt = stmt.where(Order.business_date == business_day)
        conditions = []
        if daily_number is not None:
            conditions.append(Order.daily_number == daily_number)
        if display_number:
            conditions.append(Order.display_number.ilike(display_number))
        if conditions:
            stmt = stmt.where(or_(*conditions))
        result = await self.session.execute(
            self._loaded(stmt).order_by(Order.id.desc()).limit(limit)
        )
        return list(result.scalars())

    async def orders_needing_recovery(self, limit: int = 200) -> list[Order]:
        """Terminal orders whose dispatch or acknowledgement never completed."""
        from app.utils.enums import AcknowledgementStatus, OrderDispatchState

        result = await self.session.execute(
            self._loaded(
                select(Order)
                .where(
                    Order.status.in_([OrderStatus.SUCCESS, OrderStatus.FAILED]),
                    or_(
                        Order.result_dispatch_status.in_(
                            [
                                OrderDispatchState.PENDING,
                                OrderDispatchState.PARTIAL,
                                OrderDispatchState.FAILED,
                            ]
                        ),
                        and_(
                            Order.acknowledgement_status.in_(
                                [
                                    AcknowledgementStatus.PENDING,
                                    AcknowledgementStatus.APPLYING,
                                ]
                            ),
                        ),
                    ),
                )
                .order_by(Order.id)
                .limit(limit)
            )
        )
        return list(result.scalars())
