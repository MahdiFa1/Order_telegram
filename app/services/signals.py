"""Records operator signals and asks the finalizer to re-evaluate."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.engine import session_scope
from app.database.repositories import AuditRepository, OrderRepository
from app.rules.engine import ExtractedSignal
from app.services.finalizer import FinalizationResult, OrderFinalizer, TriggerContext
from app.utils.enums import AuditEvent, OrderStatus
from app.utils.logging import get_logger

logger = get_logger(__name__)


class SignalService:
    def __init__(self, session_factory: async_sessionmaker, finalizer: OrderFinalizer) -> None:
        self.session_factory = session_factory
        self.finalizer = finalizer

    async def apply(
        self, order_id: int, signals: list[ExtractedSignal]
    ) -> FinalizationResult | None:
        """Persist new signals, then re-run the rule engine once.

        Signals are stored rather than evaluated on the fly: an ``ALL`` rule
        can then be completed by events arriving minutes apart, or across a
        restart, exactly as required.
        """
        if not signals:
            return None

        async with session_scope() as session:
            orders = OrderRepository(session)
            order = await orders.get(order_id)
            if order is None:
                return None
            if OrderStatus(order.status).is_terminal:
                # Terminal orders are frozen; late signals are recorded in the
                # audit trail but never change the outcome.
                await AuditRepository(session).log(
                    AuditEvent.OPERATOR_SIGNAL_RECEIVED,
                    order_id=order_id,
                    actor_user_id=signals[0].actor_user_id,
                    message="Signal ignored: order already finalised",
                    data={"signals": [s.signal_key.value for s in signals]},
                )
                return None

            recorded: list[str] = []
            for signal in signals:
                is_new = await orders.record_signal(
                    order_id=order_id,
                    rule_status=signal.rule_status,
                    signal_key=signal.signal_key.value,
                    actor_user_id=signal.actor_user_id,
                    trigger_type=signal.trigger_type,
                    trigger_chat_id=signal.trigger_chat_id,
                    trigger_message_id=signal.trigger_message_id,
                    detail=signal.detail,
                )
                if is_new:
                    recorded.append(f"{signal.rule_status}:{signal.signal_key.value}")

            await AuditRepository(session).log(
                AuditEvent.OPERATOR_SIGNAL_RECEIVED,
                order_id=order_id,
                actor_user_id=signals[0].actor_user_id,
                chat_id=signals[0].trigger_chat_id,
                message_id=signals[0].trigger_message_id,
                message=f"Operator signal(s): {', '.join(s.signal_key.value for s in signals)}",
                data={"new": recorded, "trigger": signals[0].trigger_type},
            )

        # The trigger recorded on the order is the event that completed the
        # rule -- this is what decides the acknowledgement target later.
        last = signals[-1]
        trigger = TriggerContext(
            actor_user_id=last.actor_user_id,
            trigger_type=last.trigger_type,
            trigger_chat_id=last.trigger_chat_id,
            trigger_message_id=last.trigger_message_id,
        )
        return await self.finalizer.evaluate_and_finalize(order_id, trigger)

    async def deactivate(
        self, order_id: int, removed: list[tuple[OrderStatus, str]]
    ) -> None:
        """Handle an operator withdrawing a reaction.

        Terminal orders are never rolled back -- once SUCCESS or FAILED has
        been dispatched and acknowledged, taking the reaction away cannot undo
        it. Only a still-pending order loses the signal.
        """
        if not removed:
            return
        async with session_scope() as session:
            orders = OrderRepository(session)
            order = await orders.get(order_id)
            if order is None or OrderStatus(order.status).is_terminal:
                return
            for status, signal_key in removed:
                await orders.deactivate_signal(order_id, status, signal_key)
            await AuditRepository(session).log(
                AuditEvent.OPERATOR_SIGNAL_RECEIVED,
                order_id=order_id,
                message="Reaction removed; signal deactivated",
                data={"removed": [f"{s}:{k}" for s, k in removed]},
            )
