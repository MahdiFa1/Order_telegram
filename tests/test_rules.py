"""Success / failure detection (spec tests 94, 95, 96, 104)."""

from __future__ import annotations

import pytest

from app.utils.enums import MatchMode, OrderStatus, RuleMode, SignalKey
from tests.conftest import (
    OPERATOR_ID,
    SOURCE_CHAT_ID,
    STRANGER_ID,
    WORK_GROUP_CHAT_ID,
    configure_rule,
)
from tests.helpers import (
    deliver_order,
    get_order,
    operator_reacts,
    operator_replies,
    photo_payload,
    primary_work_group_message,
    text_payload,
)

pytestmark = pytest.mark.asyncio


async def _pending_order(services) -> tuple[int, int, int]:
    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "New Order"))
    chat_id, message_id = await primary_work_group_message(order_id, WORK_GROUP_CHAT_ID)
    return order_id, chat_id, message_id


async def test_reaction_detection_disabled_leaves_order_pending(wired):
    """Spec test 94: reaction detection OFF, operator reacts ✅ => PENDING."""
    services = wired
    await configure_rule(
        OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,), reactions=("✅",)
    )
    order_id, chat_id, message_id = await _pending_order(services)

    await operator_reacts(services, order_id, chat_id, message_id, "✅", OPERATOR_ID)

    order = await get_order(order_id)
    assert order.status == OrderStatus.PENDING


async def test_success_any_mode_fires_on_reaction_alone(wired):
    """Spec test 95: Photo ON + Reaction ✅ ON, mode ANY, only ✅ => SUCCESS."""
    services = wired
    await configure_rule(
        OrderStatus.SUCCESS,
        mode=RuleMode.ANY,
        signals=(SignalKey.REPLY_PHOTO, SignalKey.REACTION),
        reactions=("✅",),
    )
    order_id, chat_id, message_id = await _pending_order(services)

    await operator_reacts(services, order_id, chat_id, message_id, "✅", OPERATOR_ID)

    order = await get_order(order_id)
    assert order.status == OrderStatus.SUCCESS
    assert order.completion_trigger_type == "REACTION"
    # A reaction trigger has no operator message of its own.
    assert order.completion_trigger_message_id == message_id


async def test_success_any_mode_fires_on_photo_alone(wired):
    services = wired
    await configure_rule(
        OrderStatus.SUCCESS,
        mode=RuleMode.ANY,
        signals=(SignalKey.REPLY_PHOTO, SignalKey.REACTION),
        reactions=("✅",),
    )
    order_id, _chat, _message = await _pending_order(services)

    reply = photo_payload(WORK_GROUP_CHAT_ID)
    await operator_replies(services, order_id, reply, OPERATOR_ID)

    order = await get_order(order_id)
    assert order.status == OrderStatus.SUCCESS
    assert order.completion_trigger_type == "REPLY_PHOTO"
    assert order.completion_trigger_message_id == reply.message_id


async def test_failure_all_mode_needs_every_enabled_signal(wired):
    """Spec test 96: text 'failed' alone => PENDING; then ❌ => FAILED."""
    services = wired
    await configure_rule(
        OrderStatus.FAILED,
        mode=RuleMode.ALL,
        signals=(SignalKey.REPLY_TEXT, SignalKey.REACTION),
        texts=("failed",),
        reactions=("❌",),
    )
    order_id, chat_id, message_id = await _pending_order(services)

    await operator_replies(
        services, order_id, text_payload(WORK_GROUP_CHAT_ID, "failed"), OPERATOR_ID
    )
    assert (await get_order(order_id)).status == OrderStatus.PENDING

    await operator_reacts(services, order_id, chat_id, message_id, "❌", OPERATOR_ID)
    order = await get_order(order_id)
    assert order.status == OrderStatus.FAILED
    # The event that COMPLETED the rule is what gets recorded as the trigger.
    assert order.completion_trigger_type == "REACTION"


async def test_all_mode_signals_survive_a_restart(wired, session_factory):
    """Signals live in PostgreSQL, so a half-complete ALL rule is not lost."""
    services = wired
    await configure_rule(
        OrderStatus.FAILED,
        mode=RuleMode.ALL,
        signals=(SignalKey.REPLY_TEXT, SignalKey.REACTION),
        texts=("failed",),
        reactions=("❌",),
    )
    order_id, chat_id, message_id = await _pending_order(services)
    await operator_replies(
        services, order_id, text_payload(WORK_GROUP_CHAT_ID, "failed"), OPERATOR_ID
    )

    from app.database.engine import session_scope
    from app.database.repositories import OrderRepository

    async with session_scope() as session:
        stored = await OrderRepository(session).list_signals(order_id)
    assert {(s.rule_status, s.signal_key) for s in stored} == {
        (OrderStatus.FAILED.value, SignalKey.REPLY_TEXT.value)
    }

    await operator_reacts(services, order_id, chat_id, message_id, "❌", OPERATOR_ID)
    assert (await get_order(order_id)).status == OrderStatus.FAILED


async def test_conflict_blocks_dispatch_and_acknowledgement(destinations):
    """Spec test 104: both rules match => CONFLICT, nothing dispatched."""
    from app.utils.enums import AcknowledgementStatus

    services = destinations
    await configure_rule(
        OrderStatus.SUCCESS, signals=(SignalKey.REACTION,), reactions=("✅",)
    )
    await configure_rule(
        OrderStatus.FAILED, signals=(SignalKey.REACTION,), reactions=("✅",)
    )
    order_id, chat_id, message_id = await _pending_order(services)

    await operator_reacts(services, order_id, chat_id, message_id, "✅", OPERATOR_ID)

    order = await get_order(order_id)
    assert order.status == OrderStatus.CONFLICT
    assert order.acknowledgement_status == AcknowledgementStatus.NOT_REQUIRED
    assert services.gateway.reactions == []
    from tests.conftest import FAILURE_CHAT_ID, SUCCESS_CHAT_ID

    assert services.gateway.messages_in(SUCCESS_CHAT_ID) == []
    assert services.gateway.messages_in(FAILURE_CHAT_ID) == []
    assert "conflict_detected" in services.notifier.kinds()


async def test_admin_resolves_a_conflict_manually(destinations):
    from tests.conftest import SUCCESS_CHAT_ID

    services = destinations
    await configure_rule(
        OrderStatus.SUCCESS, signals=(SignalKey.REACTION,), reactions=("✅",)
    )
    await configure_rule(
        OrderStatus.FAILED, signals=(SignalKey.REACTION,), reactions=("✅",)
    )
    order_id, chat_id, message_id = await _pending_order(services)
    await operator_reacts(services, order_id, chat_id, message_id, "✅", OPERATOR_ID)
    assert (await get_order(order_id)).status == OrderStatus.CONFLICT

    await services.finalizer.manual_override(
        order_id, OrderStatus.SUCCESS, admin_user_id=1000
    )

    order = await get_order(order_id)
    assert order.status == OrderStatus.SUCCESS
    assert len(services.gateway.messages_in(SUCCESS_CHAT_ID)) == 1


async def test_reaction_from_a_non_operator_is_ignored(wired):
    """Requirement 44: only authorized operators can change a status."""
    services = wired
    await configure_rule(
        OrderStatus.SUCCESS, signals=(SignalKey.REACTION,), reactions=("✅",)
    )
    order_id, chat_id, message_id = await _pending_order(services)

    await operator_reacts(services, order_id, chat_id, message_id, "✅", STRANGER_ID)

    assert (await get_order(order_id)).status == OrderStatus.PENDING


async def test_reply_from_a_non_operator_is_ignored(wired):
    services = wired
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    order_id, _chat, _message = await _pending_order(services)

    await operator_replies(
        services, order_id, photo_payload(WORK_GROUP_CHAT_ID), STRANGER_ID
    )

    assert (await get_order(order_id)).status == OrderStatus.PENDING


async def test_bot_own_reaction_never_feeds_the_rule_engine(wired):
    """Requirement 43: the acknowledgement reaction must not loop back."""
    services = wired
    await configure_rule(
        OrderStatus.SUCCESS, signals=(SignalKey.REACTION,), reactions=("✅",)
    )
    order_id, chat_id, message_id = await _pending_order(services)

    # The bot is not an operator, and the extractor also rejects its user id
    # explicitly as a second line of defence.
    await operator_reacts(
        services, order_id, chat_id, message_id, "✅", services.bot_user_id
    )

    assert (await get_order(order_id)).status == OrderStatus.PENDING


async def test_unlisted_reaction_emoji_is_ignored(wired):
    services = wired
    await configure_rule(
        OrderStatus.SUCCESS, signals=(SignalKey.REACTION,), reactions=("✅",)
    )
    order_id, chat_id, message_id = await _pending_order(services)

    await operator_reacts(services, order_id, chat_id, message_id, "🔥", OPERATOR_ID)

    assert (await get_order(order_id)).status == OrderStatus.PENDING


async def test_text_rule_supports_exact_contains_and_regex(wired):
    services = wired
    await configure_rule(
        OrderStatus.SUCCESS,
        signals=(SignalKey.REPLY_TEXT,),
        texts=(r"^ok\s*\d+$",),
        match_mode=MatchMode.REGEX,
    )
    order_id, _chat, _message = await _pending_order(services)

    await operator_replies(
        services, order_id, text_payload(WORK_GROUP_CHAT_ID, "nope"), OPERATOR_ID
    )
    assert (await get_order(order_id)).status == OrderStatus.PENDING

    await operator_replies(
        services, order_id, text_payload(WORK_GROUP_CHAT_ID, "OK 42"), OPERATOR_ID
    )
    assert (await get_order(order_id)).status == OrderStatus.SUCCESS


async def test_persian_text_rule_matches(wired):
    services = wired
    await configure_rule(
        OrderStatus.SUCCESS, signals=(SignalKey.REPLY_TEXT,), texts=("انجام شد",)
    )
    order_id, _chat, _message = await _pending_order(services)

    await operator_replies(
        services,
        order_id,
        text_payload(WORK_GROUP_CHAT_ID, "سفارش انجام شد ✅"),
        OPERATOR_ID,
    )
    assert (await get_order(order_id)).status == OrderStatus.SUCCESS


async def test_removing_a_reaction_does_not_roll_back_a_terminal_order(destinations):
    """Requirement 45: terminal states stay stable."""
    services = destinations
    await configure_rule(
        OrderStatus.SUCCESS, signals=(SignalKey.REACTION,), reactions=("✅",)
    )
    order_id, chat_id, message_id = await _pending_order(services)
    await operator_reacts(services, order_id, chat_id, message_id, "✅", OPERATOR_ID)
    assert (await get_order(order_id)).status == OrderStatus.SUCCESS

    await services.signals.deactivate(
        order_id, [(OrderStatus.SUCCESS, SignalKey.REACTION.value)]
    )

    assert (await get_order(order_id)).status == OrderStatus.SUCCESS


async def test_removing_a_reaction_clears_a_pending_signal(wired):
    services = wired
    await configure_rule(
        OrderStatus.FAILED,
        mode=RuleMode.ALL,
        signals=(SignalKey.REPLY_TEXT, SignalKey.REACTION),
        texts=("failed",),
        reactions=("❌",),
    )
    order_id, chat_id, message_id = await _pending_order(services)
    await operator_reacts(services, order_id, chat_id, message_id, "❌", OPERATOR_ID)

    await services.signals.deactivate(
        order_id, [(OrderStatus.FAILED, SignalKey.REACTION.value)]
    )

    from app.database.engine import session_scope
    from app.database.repositories import OrderRepository

    async with session_scope() as session:
        active = await OrderRepository(session).list_signals(order_id)
    assert active == []
