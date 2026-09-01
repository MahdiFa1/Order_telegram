"""Order-number gate applied before a source post becomes an order.

When the store order number is required, a post whose last line does not
carry a well-formed number never becomes an order: the bot replies to the
author, optionally deletes the post, and nothing reaches the work group.

The refused text is kept in ``rejected_messages`` even when the post itself
is deleted, so an admin can still see what was sent.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import SettingRepository
from app.orders.order_number import OrderNumberResult, extract
from app.telegram.payload import MessagePayload
from app.utils.enums import SettingKey


@dataclass(frozen=True, slots=True)
class GateDecision:
    """Whether a post may become an order, and the number it carries."""

    accepted: bool
    order_number: str | None = None
    result: OrderNumberResult | None = None
    required: bool = False
    length: int = 0

    @property
    def rejected(self) -> bool:
        return not self.accepted


def payload_text(payload: MessagePayload) -> str | None:
    """The text an order number could be written in."""
    return payload.text or payload.caption


async def evaluate(session: AsyncSession, payload: MessagePayload) -> GateDecision:
    """Decide whether this source post carries an acceptable order number."""
    settings = SettingRepository(session)
    required = await settings.get_bool(SettingKey.ORDER_NUMBER_ENABLED, default=False)
    if not required:
        # Feature off: accept everything, but still record a number if one is
        # obviously present, so enabling WooCommerce later has data to use.
        length = await settings.store_number_length()
        found = extract(payload_text(payload), length)
        return GateDecision(True, found.number, found, required=False, length=length)

    length = await settings.store_number_length()
    result = extract(payload_text(payload), length)
    return GateDecision(result.ok, result.number, result, required=True, length=length)


def author_name(payload: MessagePayload, fallback: str = "کاربر") -> str:
    """Best available name for the rejection reply."""
    name = payload.extra.get("author_name") if payload.extra else None
    return (name or fallback).strip() or fallback


def render_rejection(template: str, name: str) -> str:
    """Fill the admin-configured rejection message.

    ``{name}`` is the only placeholder; an unknown one is left untouched
    rather than raising, so a typo in the template cannot break intake.
    """
    try:
        return template.format(name=name)
    except (KeyError, IndexError, ValueError):
        return template
