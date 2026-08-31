"""Choosing which message the acknowledgement reaction lands on."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order
from app.database.repositories import OrderRepository
from app.utils.enums import AcknowledgementTargetMode, TriggerType


@dataclass(frozen=True, slots=True)
class AckTarget:
    chat_id: int
    message_id: int
    resolved_from: str  # "trigger" | "order"


async def resolve_target(
    session: AsyncSession, order: Order, mode: AcknowledgementTargetMode
) -> AckTarget | None:
    """Resolve the acknowledgement target for a finalised order.

    ``SMART`` (default) exists because the two trigger shapes are different:

    * a **reply** trigger has an operator message, and reacting on it tells
      that operator their specific action was processed;
    * a **reaction** trigger has no operator message at all, so the only
      sensible target is the original order message the bot posted.
    """
    orders = OrderRepository(session)

    trigger_type = (
        TriggerType(order.completion_trigger_type)
        if order.completion_trigger_type
        else None
    )
    trigger = None
    if order.completion_trigger_chat_id and order.completion_trigger_message_id:
        trigger = AckTarget(
            chat_id=order.completion_trigger_chat_id,
            message_id=order.completion_trigger_message_id,
            resolved_from="trigger",
        )

    async def order_message() -> AckTarget | None:
        # Prefer the order copy that lives in the same chat as the trigger.
        preferred_chat = order.completion_trigger_chat_id
        row = await orders.primary_delivery_message(order.id, preferred_chat)
        if row is None:
            row = await orders.primary_delivery_message(order.id)
        if row is None:
            return None
        return AckTarget(chat_id=row.chat_id, message_id=row.message_id, resolved_from="order")

    if mode is AcknowledgementTargetMode.ORDER_MESSAGE:
        return await order_message()

    if mode is AcknowledgementTargetMode.TRIGGER_MESSAGE:
        return trigger or await order_message()

    # SMART
    if trigger_type is TriggerType.REACTION or trigger_type is TriggerType.MANUAL:
        return await order_message()
    if trigger is not None:
        return trigger
    return await order_message()
