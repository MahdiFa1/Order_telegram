"""Operator interactions inside work groups: replies and reactions."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message, MessageReactionUpdated

from app.bot.filters import IsWorkGroup
from app.database.engine import session_scope
from app.database.repositories import (
    OperatorRepository,
    OrderRepository,
    SourceReactionRepository,
)
from app.rules.extractor import (
    extract_from_reaction,
    extract_from_reply,
    removed_reaction_signals,
)
from app.services.container import Services
from app.telegram.payload import extract_payload
from app.utils.logging import get_logger

logger = get_logger(__name__)

router = Router(name="operators")


@router.message(IsWorkGroup())
async def handle_work_group_reply(message: Message, services: Services) -> None:
    """A reply to a delivered order message may carry a success/failure signal."""
    if message.reply_to_message is None or message.from_user is None:
        return

    # The order is resolved by (chat_id, message_id) of the replied-to message,
    # never by parsing "orderNN" out of its text.
    async with session_scope() as session:
        order = await OrderRepository(session).get_by_delivery_message(
            message.chat.id, message.reply_to_message.message_id
        )
        if order is None:
            return
        order_id = order.id
        payload = extract_payload(message)
        authorised = await OperatorRepository(session).is_authorized_in_chat(
            message.from_user.id, message.chat.id
        )
        signals = await extract_from_reply(session, payload, message.from_user.id)

    # Keep the operator's media even when it carries no signal on its own:
    # the result destination should show everything they attached.
    if authorised:
        await services.signals.record_attachment(order_id, payload)

    if not signals:
        return
    await services.signals.apply(order_id, signals)


@router.message_reaction()
async def handle_message_reaction(
    event: MessageReactionUpdated, services: Services
) -> None:
    """``message_reaction`` updates drive reaction-based detection."""
    actor = event.user.id if event.user else None
    new_emojis = [
        r.emoji for r in (event.new_reaction or []) if getattr(r, "emoji", None)
    ]
    old_emojis = [
        r.emoji for r in (event.old_reaction or []) if getattr(r, "emoji", None)
    ]
    removed = [emoji for emoji in old_emojis if emoji not in new_emojis]

    async with session_scope() as session:
        order = await OrderRepository(session).get_by_delivery_message(
            event.chat.id, event.message_id
        )
        if order is None:
            return
        order_id = order.id
        # "I am working on this" is a separate marker from success/failure.
        progress_emojis = await SourceReactionRepository(session).enabled_progress_emojis()
        marks_progress = bool(progress_emojis & set(new_emojis)) and (
            actor is not None
            and await OperatorRepository(session).is_authorized_in_chat(
                actor, event.chat.id
            )
        )
        signals = await extract_from_reaction(
            session,
            chat_id=event.chat.id,
            message_id=event.message_id,
            emojis=new_emojis,
            actor_user_id=actor,
            bot_user_id=services.bot_user_id,
        )
        withdrawn = (
            await removed_reaction_signals(session, removed=removed) if removed else []
        )

    if marks_progress and services.source_reactions is not None:
        await services.source_reactions.mark_in_progress(order_id, actor)

    if signals:
        await services.signals.apply(order_id, signals)
    elif withdrawn:
        # Removing a reaction only matters while the order is still pending;
        # terminal states are never rolled back automatically.
        await services.signals.deactivate(
            order_id, [(status, key.value) for status, key in withdrawn]
        )
