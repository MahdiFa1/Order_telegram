"""``finalize_order()`` -- the result processing pipeline.

Transaction boundaries are deliberate: the status decision runs inside a
short transaction that holds a row lock, while every Telegram call happens
outside it. Holding a lock across a network round trip would stall the whole
bot behind one slow API call.

    [txn]  lock order -> re-evaluate rules -> persist status
                      -> create dispatch outbox rows -> arm acknowledgement
    [net]  send to each result destination (claim, send, persist)
    [net]  apply acknowledgement reaction (claim, react, persist)
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.acknowledgements.service import AcknowledgementService
from app.config import Settings
from app.database.engine import session_scope
from app.database.repositories import (
    AcknowledgementRepository,
    AuditRepository,
    OrderRepository,
    RuleRepository,
)
from app.dispatch.service import DispatchService
from app.rules.engine import Decision, decide
from app.utils.enums import (
    AcknowledgementStatus,
    AuditEvent,
    OrderStatus,
    RESULT_STATUSES,
    TriggerType,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class FinalizationResult:
    order_id: int
    status: OrderStatus
    changed: bool
    decision: Decision | None = None
    reason: str = ""


@dataclass(slots=True)
class TriggerContext:
    actor_user_id: int | None = None
    trigger_type: str | None = None
    trigger_chat_id: int | None = None
    trigger_message_id: int | None = None


class OrderFinalizer:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        dispatch: DispatchService,
        acknowledgements: AcknowledgementService,
        settings: Settings,
        notifier=None,
    ) -> None:
        self.session_factory = session_factory
        self.dispatch = dispatch
        self.acknowledgements = acknowledgements
        self.settings = settings
        self.notifier = notifier

    # ------------------------------------------------------------------
    async def evaluate_and_finalize(
        self, order_id: int, trigger: TriggerContext
    ) -> FinalizationResult:
        """Re-run the rule engine for an order and act on the outcome."""
        result = await self._decide_and_persist(order_id, trigger)
        if result.status.is_terminal and result.changed:
            await self.run_pipeline(order_id)
        elif result.status.is_terminal and not result.changed:
            # Already terminal (e.g. a duplicate event): still make sure any
            # unfinished dispatch or acknowledgement gets completed.
            await self.run_pipeline(order_id)
        return result

    async def _decide_and_persist(
        self, order_id: int, trigger: TriggerContext
    ) -> FinalizationResult:
        async with session_scope() as session:
            orders = OrderRepository(session)
            locked = await orders.lock(order_id)
            if locked is None:
                return FinalizationResult(order_id, OrderStatus.PENDING, False, None, "missing")

            current = OrderStatus(locked.status)
            if current.is_terminal:
                # Terminal states are final: repeated signals never re-dispatch,
                # re-acknowledge or double count.
                return FinalizationResult(order_id, current, False, None, "already terminal")

            rules = RuleRepository(session)
            success_rule = await rules.get_rule(OrderStatus.SUCCESS)
            failure_rule = await rules.get_rule(OrderStatus.FAILED)

            signals = await orders.list_signals(order_id)
            success_signals = {
                s.signal_key for s in signals if s.rule_status == OrderStatus.SUCCESS
            }
            failure_signals = {
                s.signal_key for s in signals if s.rule_status == OrderStatus.FAILED
            }

            decision = decide(success_rule, failure_rule, success_signals, failure_signals)
            audit = AuditRepository(session)

            if decision.status is OrderStatus.PENDING:
                return FinalizationResult(order_id, current, False, decision, "no rule matched")

            if decision.status is OrderStatus.CONFLICT:
                if current is OrderStatus.CONFLICT:
                    return FinalizationResult(
                        order_id, current, False, decision, "already conflicted"
                    )
                updated = await orders.transition(
                    order_id=order_id,
                    expected_statuses=[OrderStatus.PENDING],
                    new_status=OrderStatus.CONFLICT,
                    reason=decision.reason,
                    actor_user_id=trigger.actor_user_id,
                    trigger_type=trigger.trigger_type,
                    trigger_chat_id=trigger.trigger_chat_id,
                    trigger_message_id=trigger.trigger_message_id,
                )
                if updated is None:
                    return FinalizationResult(order_id, current, False, decision, "race lost")
                await audit.log(
                    AuditEvent.CONFLICT_DETECTED,
                    order_id=order_id,
                    actor_user_id=trigger.actor_user_id,
                    level="WARNING",
                    message="Success and failure rules matched simultaneously",
                    data={
                        "success_signals": sorted(success_signals),
                        "failure_signals": sorted(failure_signals),
                    },
                )
                logger.warning("order_conflict", order_id=order_id)
                if self.notifier is not None:
                    await self.notifier.conflict_detected(order_id)
                return FinalizationResult(
                    order_id, OrderStatus.CONFLICT, True, decision, decision.reason
                )

            # SUCCESS or FAILED
            await audit.log(
                AuditEvent.SUCCESS_RULE_MATCHED
                if decision.status is OrderStatus.SUCCESS
                else AuditEvent.FAILURE_RULE_MATCHED,
                order_id=order_id,
                actor_user_id=trigger.actor_user_id,
                message=decision.reason,
                data={"mode": decision.success.mode.value},
            )
            updated = await orders.transition(
                order_id=order_id,
                expected_statuses=[OrderStatus.PENDING],
                new_status=decision.status,
                reason=decision.reason,
                actor_user_id=trigger.actor_user_id,
                trigger_type=trigger.trigger_type,
                trigger_chat_id=trigger.trigger_chat_id,
                trigger_message_id=trigger.trigger_message_id,
            )
            if updated is None:
                return FinalizationResult(order_id, current, False, decision, "race lost")
            await audit.log(
                AuditEvent.STATUS_CHANGED,
                order_id=order_id,
                actor_user_id=trigger.actor_user_id,
                chat_id=trigger.trigger_chat_id,
                message_id=trigger.trigger_message_id,
                message=f"Status changed to {decision.status.value}",
                data={
                    "trigger_type": trigger.trigger_type,
                    "reason": decision.reason,
                },
            )
            logger.info(
                "order_finalized",
                order_id=order_id,
                status=decision.status.value,
                trigger_type=trigger.trigger_type,
            )
            return FinalizationResult(
                order_id, decision.status, True, decision, decision.reason
            )

    # ------------------------------------------------------------------
    async def run_pipeline(self, order_id: int) -> None:
        """Dispatch results, then -- only on success -- acknowledge."""
        async with session_scope() as session:
            order = await OrderRepository(session).get(order_id)
            if order is None:
                return
            status = OrderStatus(order.status)
            if not status.is_terminal:
                return
            ack_state = AcknowledgementStatus(order.acknowledgement_status)
            already_prepared = bool(await AcknowledgementRepository(session).list_dispatches(order_id))

        if not already_prepared:
            await self.dispatch.prepare(order_id, status)
        if ack_state is AcknowledgementStatus.NOT_REQUIRED:
            await self.acknowledgements.prepare(order_id, status)

        await self.dispatch.process(order_id)
        await self.acknowledgements.process(order_id)

    # ------------------------------------------------------------------
    async def manual_override(
        self,
        order_id: int,
        new_status: OrderStatus,
        admin_user_id: int,
        *,
        dispatch_result: bool = True,
        apply_acknowledgement: bool = True,
    ) -> FinalizationResult:
        """Admin-driven status change (mark success / failed / pending)."""
        async with session_scope() as session:
            orders = OrderRepository(session)
            locked = await orders.lock(order_id)
            if locked is None:
                return FinalizationResult(order_id, OrderStatus.PENDING, False, None, "missing")
            previous = OrderStatus(locked.status)

            updated = await orders.transition(
                order_id=order_id,
                expected_statuses=list(OrderStatus),
                new_status=new_status,
                reason=f"Manual override by admin {admin_user_id}",
                actor_user_id=admin_user_id,
                trigger_type=TriggerType.MANUAL.value,
                trigger_chat_id=None,
                trigger_message_id=None,
            )
            if updated is None:
                return FinalizationResult(order_id, previous, False, None, "race lost")

            if new_status is OrderStatus.PENDING:
                # Re-open: clear the acknowledgement arming so a later genuine
                # finalisation can arm it again. Already-sent dispatch rows are
                # kept so the order can never be delivered twice.
                await AcknowledgementRepository(session).set_ack_status(
                    order_id,
                    AcknowledgementStatus.NOT_REQUIRED,
                    acknowledgement_error=None,
                )

            await AuditRepository(session).log(
                AuditEvent.MANUAL_OVERRIDE,
                order_id=order_id,
                actor_user_id=admin_user_id,
                message=f"Manual override {previous.value} -> {new_status.value}",
                data={
                    "dispatch_result": dispatch_result,
                    "apply_acknowledgement": apply_acknowledgement,
                },
            )
        logger.info(
            "manual_override",
            order_id=order_id,
            from_status=previous.value,
            to_status=new_status.value,
            admin=admin_user_id,
        )

        if new_status.is_terminal and dispatch_result:
            await self.dispatch.prepare(order_id, new_status)
            if apply_acknowledgement:
                await self.acknowledgements.prepare(order_id, new_status)
            else:
                async with session_scope() as session:
                    await AcknowledgementRepository(session).set_ack_status(
                        order_id, AcknowledgementStatus.NOT_REQUIRED
                    )
            await self.dispatch.process(order_id)
            if apply_acknowledgement:
                await self.acknowledgements.process(order_id)

        return FinalizationResult(order_id, new_status, True, None, "manual override")

    # ------------------------------------------------------------------
    async def recover(self) -> dict[str, int]:
        """Startup recovery: finish work interrupted by a restart."""
        from datetime import timedelta

        from app.utils.time import utcnow

        cutoff = utcnow() - timedelta(minutes=2)
        counters = {"dispatches_released": 0, "acks_released": 0, "orders_resumed": 0}

        async with session_scope() as session:
            acks = AcknowledgementRepository(session)
            counters["dispatches_released"] = await acks.release_stale_dispatches(cutoff)
            counters["acks_released"] = await acks.release_stale_acknowledgements(cutoff)

        async with session_scope() as session:
            order_ids = [o.id for o in await OrderRepository(session).orders_needing_recovery()]

        for order_id in order_ids:
            try:
                await self.run_pipeline(order_id)
                counters["orders_resumed"] += 1
            except Exception:  # noqa: BLE001 - recovery must not abort startup
                logger.exception("recovery_failed", order_id=order_id)

        if any(counters.values()):
            async with session_scope() as session:
                await AuditRepository(session).log(
                    AuditEvent.RECOVERY_PERFORMED,
                    message="Startup recovery completed",
                    data=counters,
                )
            logger.info("startup_recovery", **counters)
        return counters
