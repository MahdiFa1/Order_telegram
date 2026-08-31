"""Turns Telegram operator events into rule-engine signals.

Two guards live here and nowhere else:

* only an **authorized operator** can produce a signal (a random member's
  reaction never changes an order);
* a reaction produced by the **bot itself** is never treated as a signal,
  which is what prevents an acknowledgement reaction from feeding back into
  the rule engine and re-finalising the order.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import OperatorRepository, RuleRepository
from app.rules.engine import ExtractedSignal
from app.rules.matching import first_match
from app.telegram.payload import MessagePayload
from app.utils.enums import (
    CONTENT_TYPE_TO_SIGNAL,
    ContentType,
    OrderStatus,
    RESULT_STATUSES,
    SIGNAL_TO_TRIGGER,
    SignalKey,
)


async def extract_from_reply(
    session: AsyncSession,
    payload: MessagePayload,
    actor_user_id: int,
) -> list[ExtractedSignal]:
    """Signals produced by an operator replying to an order message."""
    if not await OperatorRepository(session).is_authorized_in_chat(
        actor_user_id, payload.chat_id
    ):
        return []

    rules = RuleRepository(session)
    signals: list[ExtractedSignal] = []
    content_type = ContentType(payload.content_type)
    media_signal = CONTENT_TYPE_TO_SIGNAL.get(content_type)

    # Text carried in a caption counts as text too, so an operator can send a
    # photo captioned "done" and satisfy either rule shape.
    text = payload.text or payload.caption or ""

    for status in RESULT_STATUSES:
        rule = await rules.get_rule(status)
        if not rule.enabled:
            continue
        enabled = {s.signal_key for s in rule.signals if s.enabled}

        if media_signal is not None and media_signal.value in enabled:
            signals.append(
                ExtractedSignal(
                    rule_status=status,
                    signal_key=media_signal,
                    trigger_type=SIGNAL_TO_TRIGGER[media_signal].value,
                    trigger_chat_id=payload.chat_id,
                    trigger_message_id=payload.message_id,
                    actor_user_id=actor_user_id,
                    detail={"content_type": content_type.value},
                )
            )

        if SignalKey.REPLY_TEXT.value in enabled and text.strip():
            matched = first_match(text, list(rule.text_patterns))
            if matched is not None:
                signals.append(
                    ExtractedSignal(
                        rule_status=status,
                        signal_key=SignalKey.REPLY_TEXT,
                        trigger_type=SIGNAL_TO_TRIGGER[SignalKey.REPLY_TEXT].value,
                        trigger_chat_id=payload.chat_id,
                        trigger_message_id=payload.message_id,
                        actor_user_id=actor_user_id,
                        detail={
                            "pattern": matched.pattern,
                            "match_mode": matched.match_mode,
                        },
                    )
                )
    return signals


async def extract_from_reaction(
    session: AsyncSession,
    *,
    chat_id: int,
    message_id: int,
    emojis: list[str],
    actor_user_id: int | None,
    bot_user_id: int | None = None,
) -> list[ExtractedSignal]:
    """Signals produced by an operator reacting to an order message."""
    if actor_user_id is None:
        # Anonymous / channel-post reactions carry no user; they can never be
        # attributed to an authorized operator.
        return []
    if bot_user_id is not None and actor_user_id == bot_user_id:
        # Defensive check: the bot's own acknowledgement reaction must never
        # re-enter the rule engine, even if Telegram were to echo it back.
        return []
    if not await OperatorRepository(session).is_authorized_in_chat(actor_user_id, chat_id):
        return []

    rules = RuleRepository(session)
    signals: list[ExtractedSignal] = []
    for status in RESULT_STATUSES:
        rule = await rules.get_rule(status)
        if not rule.enabled:
            continue
        enabled = {s.signal_key for s in rule.signals if s.enabled}
        if SignalKey.REACTION.value not in enabled:
            # Reaction detection switched off for this status -> ignore.
            continue
        accepted = {r.emoji for r in rule.reactions if r.enabled}
        hit = next((emoji for emoji in emojis if emoji in accepted), None)
        if hit is None:
            continue
        signals.append(
            ExtractedSignal(
                rule_status=status,
                signal_key=SignalKey.REACTION,
                trigger_type=SIGNAL_TO_TRIGGER[SignalKey.REACTION].value,
                trigger_chat_id=chat_id,
                trigger_message_id=message_id,
                actor_user_id=actor_user_id,
                detail={"emoji": hit},
            )
        )
    return signals


async def removed_reaction_signals(
    session: AsyncSession,
    *,
    removed: list[str],
) -> list[tuple[OrderStatus, SignalKey]]:
    """Reaction signals whose emoji the operator has just taken back."""
    rules = RuleRepository(session)
    affected: list[tuple[OrderStatus, SignalKey]] = []
    for status in RESULT_STATUSES:
        rule = await rules.get_rule(status)
        accepted = {r.emoji for r in rule.reactions if r.enabled}
        if any(emoji in accepted for emoji in removed):
            affected.append((status, SignalKey.REACTION))
    return affected
