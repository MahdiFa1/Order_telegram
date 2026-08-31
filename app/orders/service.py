"""Order intake: numbering, persistence and routing into work groups."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.database.engine import session_scope
from app.database.models import Order
from app.database.repositories import (
    AuditRepository,
    CounterRepository,
    OrderRepository,
    RouteRepository,
    SettingRepository,
    SourceChannelRepository,
)
from app.database.repositories.orders import scope_key_for
from app.orders.numbering import render_display_number
from app.telegram.composer import compose
from app.telegram.gateway import TelegramGateway
from app.telegram.payload import MessagePayload
from app.utils.enums import AuditEvent, DeliveryStatus
from app.utils.logging import get_logger
from app.utils.time import business_date

logger = get_logger(__name__)

#: How long to wait for the remaining messages of an album before routing.
#: Telegram never says how many parts an album has, so the group is flushed a
#: short moment after the last part arrived.
ALBUM_FLUSH_DELAY = 2.0


@dataclass(slots=True)
class IntakeResult:
    order_id: int | None
    created: bool
    attached: bool
    reason: str = ""


class OrderService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        gateway: TelegramGateway,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.gateway = gateway
        self.settings = settings
        self._album_tasks: dict[tuple[int, str], tuple[asyncio.Task, int]] = {}
        self._album_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Intake
    # ------------------------------------------------------------------
    async def ingest(self, payload: MessagePayload) -> IntakeResult:
        """Persist an incoming source message as (part of) an order.

        Duplicate protection works on two levels:

        * ``(source_chat_id, source_message_id)`` is unique, so a redelivered
          Telegram update cannot create a second order;
        * ``(source_chat_id, source_media_group_id)`` is unique, so every part
          of an album collapses onto the first part's order.
        """
        async with session_scope() as session:
            sources = SourceChannelRepository(session)
            channel = await sources.get_enabled_by_chat_id(payload.chat_id)
            if channel is None:
                return IntakeResult(None, False, False, "source channel not enabled")

            orders = OrderRepository(session)
            audit = AuditRepository(session)

            existing = await orders.get_by_source_message(payload.chat_id, payload.message_id)
            if existing is not None:
                return IntakeResult(existing.id, False, False, "duplicate source message")

            if payload.media_group_id:
                album_order = await orders.get_by_media_group(
                    payload.chat_id, payload.media_group_id
                )
                if album_order is not None:
                    await orders.add_source_message(
                        album_order.id,
                        **payload.as_columns(
                            position=len(await orders.list_source_messages(album_order.id))
                        ),
                    )
                    return IntakeResult(album_order.id, False, True, "album part attached")

            settings_repo = SettingRepository(session)
            scope = await settings_repo.counter_scope()
            prefix = await settings_repo.order_prefix()
            template = await settings_repo.order_number_format()

            day = business_date()
            scope_key = scope_key_for(scope, payload.chat_id)

            try:
                async with session.begin_nested():
                    number = await CounterRepository(session).allocate(day, scope_key)
                    order = await orders.create(
                        business_day=day,
                        daily_number=number,
                        counter_scope_key=scope_key,
                        display_number=render_display_number(number, prefix, template),
                        source_channel_id=channel.id,
                        source_chat_id=payload.chat_id,
                        source_message_id=payload.message_id,
                        source_media_group_id=payload.media_group_id,
                    )
                    await orders.add_source_message(order.id, **payload.as_columns(position=0))
            except IntegrityError:
                # Lost a race: another worker created the order (or the album
                # head) first. Attach to theirs instead of failing.
                await session.rollback()
                return await self._attach_after_race(payload)

            await audit.log(
                AuditEvent.ORDER_CREATED,
                order_id=order.id,
                chat_id=payload.chat_id,
                message_id=payload.message_id,
                message=f"Order {order.display_number} created",
                data={
                    "display_number": order.display_number,
                    "business_date": day,
                    "counter_scope_key": scope_key,
                    "media_group_id": payload.media_group_id,
                    "content_type": payload.content_type.value,
                },
            )
            logger.info(
                "order_created",
                order_id=order.id,
                display_number=order.display_number,
                chat_id=payload.chat_id,
                message_id=payload.message_id,
            )
            return IntakeResult(order.id, True, False, "created")

    async def _attach_after_race(self, payload: MessagePayload) -> IntakeResult:
        async with session_scope() as session:
            orders = OrderRepository(session)
            order = await orders.get_by_source_message(payload.chat_id, payload.message_id)
            if order is None and payload.media_group_id:
                order = await orders.get_by_media_group(
                    payload.chat_id, payload.media_group_id
                )
            if order is None:
                return IntakeResult(None, False, False, "integrity conflict")
            await orders.add_source_message(
                order.id,
                **payload.as_columns(
                    position=len(await orders.list_source_messages(order.id))
                ),
            )
            return IntakeResult(order.id, False, True, "attached after race")

    # ------------------------------------------------------------------
    # Album flushing
    # ------------------------------------------------------------------
    async def schedule_routing(self, order_id: int, media_group_id: str | None, chat_id: int) -> None:
        """Route immediately, or once the album stops growing.

        Only the *timer* lives in memory. The order and all of its source
        messages are already committed, and routing is re-attempted from the
        database on startup, so a restart mid-album loses nothing.
        """
        if not media_group_id:
            await self.route_order(order_id)
            return

        key = (chat_id, media_group_id)
        async with self._album_lock:
            existing = self._album_tasks.get(key)
            if existing is not None and not existing[0].done():
                existing[0].cancel()
            task = asyncio.create_task(self._flush_album(key, order_id))
            self._album_tasks[key] = (task, order_id)

    async def _flush_album(self, key: tuple[int, str], order_id: int) -> None:
        try:
            await asyncio.sleep(ALBUM_FLUSH_DELAY)
        except asyncio.CancelledError:
            return
        async with self._album_lock:
            self._album_tasks.pop(key, None)
        try:
            await self.route_order(order_id)
        except Exception:  # noqa: BLE001 - never let a task die silently
            logger.exception("album_flush_failed", order_id=order_id)

    def pending_album_count(self) -> int:
        return len(self._album_tasks)

    async def flush_pending_albums(self) -> None:
        """Route every buffered album immediately (graceful shutdown)."""
        async with self._album_lock:
            buffered = list(self._album_tasks.values())
            self._album_tasks.clear()
        for task, _order_id in buffered:
            task.cancel()
        for _task, order_id in buffered:
            try:
                await self.route_order(order_id)
            except Exception:  # noqa: BLE001 - shutdown must not raise
                logger.exception("album_shutdown_flush_failed", order_id=order_id)

    # ------------------------------------------------------------------
    # Routing into work groups
    # ------------------------------------------------------------------
    async def route_order(self, order_id: int) -> None:
        """Copy the order into every work group the source routes to."""
        async with session_scope() as session:
            orders = OrderRepository(session)
            order = await orders.get(order_id)
            if order is None or order.source_channel_id is None:
                return
            work_groups = await RouteRepository(session).target_work_groups(
                order.source_channel_id
            )
            if not work_groups:
                await AuditRepository(session).log(
                    AuditEvent.ORDER_ROUTE_FAILED,
                    order_id=order.id,
                    level="WARNING",
                    message="No enabled route configured for this source channel",
                )
                logger.warning("order_no_route", order_id=order.id)
                return
            for group in work_groups:
                await orders.ensure_delivery(order.id, group.id, group.chat_id)

        await self.process_pending_deliveries(order_id=order_id)

    async def process_pending_deliveries(self, order_id: int | None = None) -> int:
        """Send every claimed-but-unsent delivery. Safe to call repeatedly."""
        async with session_scope() as session:
            orders = OrderRepository(session)
            if order_id is not None:
                order = await orders.get(order_id)
                deliveries = (
                    [
                        d
                        for d in order.deliveries
                        if d.status in (DeliveryStatus.PENDING, DeliveryStatus.FAILED)
                    ]
                    if order
                    else []
                )
            else:
                deliveries = await orders.pending_deliveries()
            targets = [(d.id, d.order_id, d.chat_id) for d in deliveries]

        sent = 0
        for delivery_id, target_order_id, chat_id in targets:
            if await self._deliver(delivery_id, target_order_id, chat_id):
                sent += 1
        return sent

    async def _deliver(self, delivery_id: int, order_id: int, chat_id: int) -> bool:
        # Claim in its own transaction so the Telegram call never runs while a
        # row lock is held.
        async with session_scope() as session:
            if not await OrderRepository(session).claim_delivery(delivery_id):
                return False

        try:
            async with session_scope() as session:
                orders = OrderRepository(session)
                order = await orders.get(order_id)
                if order is None:
                    return False
                source_messages = await orders.list_source_messages(order_id)
                composed = compose(
                    order.display_number,
                    source_messages,
                    source_chat_id=order.source_chat_id,
                )
                display_number = order.display_number

            if composed.is_empty:
                async with session_scope() as session:
                    await OrderRepository(session).fail_delivery(
                        delivery_id, "nothing to send: no source message stored"
                    )
                return False

            message_ids = await self.gateway.send_composed(chat_id, composed)
        except Exception as error:  # noqa: BLE001 - recorded and retried later
            logger.warning(
                "order_delivery_failed",
                order_id=order_id,
                chat_id=chat_id,
                error=str(error),
            )
            async with session_scope() as session:
                await OrderRepository(session).fail_delivery(delivery_id, str(error))
                await AuditRepository(session).log(
                    AuditEvent.ORDER_ROUTE_FAILED,
                    order_id=order_id,
                    chat_id=chat_id,
                    level="ERROR",
                    message=f"Delivery to work group failed: {error}",
                )
            return False

        async with session_scope() as session:
            orders = OrderRepository(session)
            await orders.complete_delivery(delivery_id, message_ids, chat_id, order_id)
            await AuditRepository(session).log(
                AuditEvent.ORDER_ROUTED,
                order_id=order_id,
                chat_id=chat_id,
                message_id=message_ids[0] if message_ids else None,
                message=f"Order {display_number} delivered to work group",
                data={"message_ids": message_ids},
            )
        logger.info(
            "order_routed", order_id=order_id, chat_id=chat_id, message_ids=message_ids
        )
        return True

    # ------------------------------------------------------------------
    async def get_order(self, order_id: int) -> Order | None:
        async with session_scope() as session:
            return await OrderRepository(session).get(order_id)
