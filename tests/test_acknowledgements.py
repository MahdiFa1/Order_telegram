"""Result dispatch and acknowledgement reactions
(spec tests 97, 98, 99, 100, 101, 102, 103)."""

from __future__ import annotations

import asyncio

import pytest

from app.database.engine import session_scope
from app.database.repositories import AcknowledgementRepository, OrderRepository
from app.utils.enums import (
    AcknowledgementStatus,
    AcknowledgementTargetMode,
    DispatchPolicy,
    DispatchStatus,
    OrderDispatchState,
    OrderStatus,
    RuleMode,
    SignalKey,
)
from tests.conftest import (
    FAILURE_CHAT_ID,
    OPERATOR_ID,
    SOURCE_CHAT_ID,
    SUCCESS_CHAT_ID,
    WORK_GROUP_CHAT_ID,
    configure_acknowledgement,
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


# ---------------------------------------------------------------------------
# Spec test 97 — success acknowledgement on a reply
# ---------------------------------------------------------------------------
async def test_success_acknowledgement_lands_on_the_operator_reply(destinations):
    services = destinations
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅")

    order_id, _chat, _message = await _pending_order(services)
    reply = photo_payload(WORK_GROUP_CHAT_ID)
    await operator_replies(services, order_id, reply, OPERATOR_ID)

    order = await get_order(order_id)
    assert order.status == OrderStatus.SUCCESS
    assert order.result_dispatch_status == OrderDispatchState.SENT
    assert order.acknowledgement_status == AcknowledgementStatus.APPLIED

    # The order reached the success destination...
    assert len(services.gateway.messages_in(SUCCESS_CHAT_ID)) == 1
    # ...and only then was the operator's photo acknowledged.
    assert len(services.gateway.reactions) == 1
    reaction = services.gateway.reactions[0]
    assert (reaction.chat_id, reaction.message_id) == (WORK_GROUP_CHAT_ID, reply.message_id)
    assert reaction.reaction == "✅"


async def test_acknowledgement_reaction_is_configurable_per_status(destinations):
    """Requirement 30: success and failure emoji are independent."""
    services = destinations
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="👍")

    order_id, _chat, _message = await _pending_order(services)
    reply = photo_payload(WORK_GROUP_CHAT_ID)
    await operator_replies(services, order_id, reply, OPERATOR_ID)

    assert services.gateway.reactions[0].reaction == "👍"


# ---------------------------------------------------------------------------
# Spec test 98 — failure acknowledgement on a reply
# ---------------------------------------------------------------------------
async def test_failure_acknowledgement_lands_on_the_operator_reply(destinations):
    services = destinations
    await configure_rule(
        OrderStatus.FAILED, signals=(SignalKey.REPLY_TEXT,), texts=("failed",)
    )
    await configure_acknowledgement(OrderStatus.FAILED, reaction="❌")

    order_id, _chat, _message = await _pending_order(services)
    reply = text_payload(WORK_GROUP_CHAT_ID, "failed")
    await operator_replies(services, order_id, reply, OPERATOR_ID)

    order = await get_order(order_id)
    assert order.status == OrderStatus.FAILED
    assert len(services.gateway.messages_in(FAILURE_CHAT_ID)) == 1
    assert len(services.gateway.messages_in(SUCCESS_CHAT_ID)) == 0

    reaction = services.gateway.reactions[0]
    assert (reaction.chat_id, reaction.message_id) == (WORK_GROUP_CHAT_ID, reply.message_id)
    assert reaction.reaction == "❌"


async def test_success_acknowledgement_can_be_on_while_failure_is_off(destinations):
    """Requirement 31: each status is enabled independently."""
    services = destinations
    await configure_rule(
        OrderStatus.FAILED, signals=(SignalKey.REPLY_TEXT,), texts=("failed",)
    )
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=True, reaction="✅")
    await configure_acknowledgement(OrderStatus.FAILED, enabled=False, reaction="❌")

    order_id, _chat, _message = await _pending_order(services)
    await operator_replies(
        services, order_id, text_payload(WORK_GROUP_CHAT_ID, "failed"), OPERATOR_ID
    )

    order = await get_order(order_id)
    assert order.status == OrderStatus.FAILED
    assert len(services.gateway.messages_in(FAILURE_CHAT_ID)) == 1
    assert order.acknowledgement_status == AcknowledgementStatus.NOT_REQUIRED
    assert services.gateway.reactions == []


# ---------------------------------------------------------------------------
# Spec test 99 — reaction trigger targets the original order message
# ---------------------------------------------------------------------------
async def test_reaction_trigger_acknowledges_the_order_message(destinations):
    services = destinations
    await configure_rule(
        OrderStatus.SUCCESS, signals=(SignalKey.REACTION,), reactions=("✅",)
    )
    await configure_acknowledgement(
        OrderStatus.SUCCESS, reaction="👍", target_mode=AcknowledgementTargetMode.SMART
    )

    order_id, chat_id, message_id = await _pending_order(services)
    await operator_reacts(services, order_id, chat_id, message_id, "✅", OPERATOR_ID)

    order = await get_order(order_id)
    assert order.status == OrderStatus.SUCCESS
    assert len(services.gateway.messages_in(SUCCESS_CHAT_ID)) == 1

    # There is no operator message here, so SMART falls back to the order message.
    reaction = services.gateway.reactions[0]
    assert (reaction.chat_id, reaction.message_id) == (chat_id, message_id)
    assert reaction.reaction == "👍"
    assert order.acknowledgement_message_id == message_id


async def test_order_message_target_mode_ignores_the_reply(destinations):
    services = destinations
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    await configure_acknowledgement(
        OrderStatus.SUCCESS,
        reaction="✅",
        target_mode=AcknowledgementTargetMode.ORDER_MESSAGE,
    )

    order_id, chat_id, message_id = await _pending_order(services)
    reply = photo_payload(WORK_GROUP_CHAT_ID)
    await operator_replies(services, order_id, reply, OPERATOR_ID)

    reaction = services.gateway.reactions[0]
    assert (reaction.chat_id, reaction.message_id) == (chat_id, message_id)
    assert reaction.message_id != reply.message_id


async def test_trigger_message_mode_falls_back_to_the_order_message(destinations):
    services = destinations
    await configure_rule(
        OrderStatus.SUCCESS, signals=(SignalKey.REACTION,), reactions=("✅",)
    )
    await configure_acknowledgement(
        OrderStatus.SUCCESS,
        reaction="✅",
        target_mode=AcknowledgementTargetMode.TRIGGER_MESSAGE,
    )

    order_id, chat_id, message_id = await _pending_order(services)
    await operator_reacts(services, order_id, chat_id, message_id, "✅", OPERATOR_ID)

    reaction = services.gateway.reactions[0]
    assert (reaction.chat_id, reaction.message_id) == (chat_id, message_id)


# ---------------------------------------------------------------------------
# Spec test 100 — destination failure blocks the acknowledgement
# ---------------------------------------------------------------------------
async def test_failed_dispatch_withholds_the_acknowledgement(destinations):
    services = destinations
    services.gateway.failing_chats.add(SUCCESS_CHAT_ID)
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅")

    order_id, _chat, _message = await _pending_order(services)
    await operator_replies(services, order_id, photo_payload(WORK_GROUP_CHAT_ID), OPERATOR_ID)

    order = await get_order(order_id)
    assert order.status == OrderStatus.SUCCESS
    assert order.result_dispatch_status == OrderDispatchState.FAILED
    # The reaction would tell the operator the work was done — it must not appear.
    assert services.gateway.reactions == []
    assert order.acknowledgement_status == AcknowledgementStatus.PENDING
    assert "dispatch_failed" in services.notifier.kinds()


async def test_retrying_after_the_destination_recovers_completes_the_pipeline(destinations):
    services = destinations
    services.gateway.failing_chats.add(SUCCESS_CHAT_ID)
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅")

    order_id, _chat, _message = await _pending_order(services)
    await operator_replies(services, order_id, photo_payload(WORK_GROUP_CHAT_ID), OPERATOR_ID)
    assert services.gateway.reactions == []

    services.gateway.failing_chats.discard(SUCCESS_CHAT_ID)
    await services.finalizer.run_pipeline(order_id)

    order = await get_order(order_id)
    assert order.result_dispatch_status == OrderDispatchState.SENT
    assert order.acknowledgement_status == AcknowledgementStatus.APPLIED
    assert len(services.gateway.messages_in(SUCCESS_CHAT_ID)) == 1


async def test_all_required_destinations_policy_waits_for_every_destination(wired):
    from app.database.repositories import ResultDestinationRepository

    services = wired
    second_success_chat = -1003000000077
    async with session_scope() as session:
        repo = ResultDestinationRepository(session)
        await repo.add(OrderStatus.SUCCESS, SUCCESS_CHAT_ID, "Channel A")
        await repo.add(OrderStatus.SUCCESS, second_success_chat, "Channel B")
    services.gateway.failing_chats.add(second_success_chat)

    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅")

    order_id, _chat, _message = await _pending_order(services)
    await operator_replies(services, order_id, photo_payload(WORK_GROUP_CHAT_ID), OPERATOR_ID)

    order = await get_order(order_id)
    assert order.result_dispatch_status == OrderDispatchState.PARTIAL
    assert services.gateway.reactions == []


async def test_any_destination_policy_acknowledges_after_one_success(wired):
    from app.database.repositories import ResultDestinationRepository

    services = wired
    second_success_chat = -1003000000078
    async with session_scope() as session:
        repo = ResultDestinationRepository(session)
        await repo.add(OrderStatus.SUCCESS, SUCCESS_CHAT_ID, "Channel A")
        await repo.add(OrderStatus.SUCCESS, second_success_chat, "Channel B")
    services.gateway.failing_chats.add(second_success_chat)

    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    await configure_acknowledgement(
        OrderStatus.SUCCESS, reaction="✅", policy=DispatchPolicy.ANY_DESTINATION
    )

    order_id, _chat, _message = await _pending_order(services)
    await operator_replies(services, order_id, photo_payload(WORK_GROUP_CHAT_ID), OPERATOR_ID)

    assert len(services.gateway.reactions) == 1


# ---------------------------------------------------------------------------
# Spec test 101 — acknowledgement failure never rolls the order back
# ---------------------------------------------------------------------------
async def test_reaction_failure_leaves_the_order_success_and_dispatch_sent(destinations):
    services = destinations
    services.gateway.fail_all_reactions = True
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅")

    order_id, _chat, _message = await _pending_order(services)
    await operator_replies(services, order_id, photo_payload(WORK_GROUP_CHAT_ID), OPERATOR_ID)

    order = await get_order(order_id)
    assert order.status == OrderStatus.SUCCESS
    assert order.result_dispatch_status == OrderDispatchState.SENT
    assert order.acknowledgement_status == AcknowledgementStatus.FAILED
    assert order.acknowledgement_error
    assert len(services.gateway.messages_in(SUCCESS_CHAT_ID)) == 1
    assert "acknowledgement_failed" in services.notifier.kinds()

    async with session_scope() as session:
        events = await AcknowledgementRepository(session).list_events(order_id)
    assert [e.result for e in events] == ["FAILED"]


async def test_acknowledgement_retry_budget_is_bounded(destinations):
    services = destinations
    services.gateway.fail_all_reactions = True
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    await configure_acknowledgement(
        OrderStatus.SUCCESS, reaction="✅", max_retry_count=2
    )

    order_id, _chat, _message = await _pending_order(services)
    await operator_replies(services, order_id, photo_payload(WORK_GROUP_CHAT_ID), OPERATOR_ID)

    for _ in range(5):
        await services.acknowledgements.process(order_id)

    order = await get_order(order_id)
    assert order.acknowledgement_status == AcknowledgementStatus.FAILED
    # One initial attempt plus one retry, then the budget stops the loop.
    assert order.acknowledgement_attempts == 2
    # Crucially, the result was never dispatched more than once.
    assert len(services.gateway.messages_in(SUCCESS_CHAT_ID)) == 1


# ---------------------------------------------------------------------------
# Spec test 102 — duplicate finalisation happens exactly once
# ---------------------------------------------------------------------------
async def test_multiple_simultaneous_success_signals_dispatch_once(destinations):
    services = destinations
    await configure_rule(
        OrderStatus.SUCCESS,
        mode=RuleMode.ANY,
        signals=(SignalKey.REPLY_PHOTO, SignalKey.REPLY_TEXT, SignalKey.REACTION),
        texts=("done",),
        reactions=("✅",),
    )
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅")

    order_id, chat_id, message_id = await _pending_order(services)

    await asyncio.gather(
        operator_reacts(services, order_id, chat_id, message_id, "✅", OPERATOR_ID),
        operator_replies(services, order_id, photo_payload(WORK_GROUP_CHAT_ID), OPERATOR_ID),
        operator_replies(
            services, order_id, text_payload(WORK_GROUP_CHAT_ID, "done"), OPERATOR_ID
        ),
    )

    order = await get_order(order_id)
    assert order.status == OrderStatus.SUCCESS
    assert len(services.gateway.messages_in(SUCCESS_CHAT_ID)) == 1
    assert len(services.gateway.reactions) == 1

    async with session_scope() as session:
        dispatches = await AcknowledgementRepository(session).list_dispatches(order_id)
    assert len(dispatches) == 1
    assert dispatches[0].status == DispatchStatus.SENT
    assert dispatches[0].attempts == 1


async def test_repeated_pipeline_runs_are_idempotent(destinations):
    services = destinations
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅")

    order_id, _chat, _message = await _pending_order(services)
    await operator_replies(services, order_id, photo_payload(WORK_GROUP_CHAT_ID), OPERATOR_ID)

    for _ in range(3):
        await services.finalizer.run_pipeline(order_id)

    assert len(services.gateway.messages_in(SUCCESS_CHAT_ID)) == 1
    assert len(services.gateway.reactions) == 1


async def test_late_signal_on_a_terminal_order_changes_nothing(destinations):
    services = destinations
    await configure_rule(
        OrderStatus.SUCCESS,
        signals=(SignalKey.REPLY_PHOTO, SignalKey.REACTION),
        reactions=("✅",),
    )
    await configure_rule(
        OrderStatus.FAILED, signals=(SignalKey.REPLY_TEXT,), texts=("failed",)
    )
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅")

    order_id, chat_id, message_id = await _pending_order(services)
    await operator_replies(services, order_id, photo_payload(WORK_GROUP_CHAT_ID), OPERATOR_ID)
    assert (await get_order(order_id)).status == OrderStatus.SUCCESS

    # A late "failed" reply must not flip an already dispatched order.
    await operator_replies(
        services, order_id, text_payload(WORK_GROUP_CHAT_ID, "failed"), OPERATOR_ID
    )

    order = await get_order(order_id)
    assert order.status == OrderStatus.SUCCESS
    assert len(services.gateway.messages_in(SUCCESS_CHAT_ID)) == 1
    assert len(services.gateway.messages_in(FAILURE_CHAT_ID)) == 0
    assert len(services.gateway.reactions) == 1


# ---------------------------------------------------------------------------
# Spec test 103 — restart in the middle of the flow
# ---------------------------------------------------------------------------
async def test_flow_completes_across_a_bot_restart(destinations, session_factory):
    """Order created, bot restarts, operator acts, everything still completes."""
    services = destinations
    await configure_rule(
        OrderStatus.SUCCESS, signals=(SignalKey.REACTION,), reactions=("✅",)
    )
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="👍")

    order_id, chat_id, message_id = await _pending_order(services)
    assert (await get_order(order_id)).status == OrderStatus.PENDING

    # --- restart: brand new service graph over the same database ---
    from app.acknowledgements.service import AcknowledgementService
    from app.config import get_settings
    from app.dispatch.service import DispatchService
    from app.orders.service import OrderService
    from app.reports.service import ReportService
    from app.services.container import Services
    from app.services.finalizer import OrderFinalizer
    from app.services.signals import SignalService

    settings = get_settings()
    gateway = services.gateway  # same fake "Telegram"
    notifier = services.notifier
    dispatch = DispatchService(session_factory, gateway, settings, notifier)
    acks = AcknowledgementService(session_factory, gateway, settings, notifier)
    finalizer = OrderFinalizer(session_factory, dispatch, acks, settings, notifier)
    restarted = Services(
        settings=settings,
        session_factory=session_factory,
        gateway=gateway,
        notifier=notifier,
        orders=OrderService(session_factory, gateway, settings),
        dispatch=dispatch,
        acknowledgements=acks,
        finalizer=finalizer,
        signals=SignalService(session_factory, finalizer),
        reports=ReportService(session_factory),
        bot_user_id=42,
    )
    await restarted.finalizer.recover()

    await operator_reacts(restarted, order_id, chat_id, message_id, "✅", OPERATOR_ID)

    order = await get_order(order_id)
    assert order.status == OrderStatus.SUCCESS
    assert order.result_dispatch_status == OrderDispatchState.SENT
    assert order.acknowledgement_status == AcknowledgementStatus.APPLIED
    assert len(gateway.messages_in(SUCCESS_CHAT_ID)) == 1
    assert gateway.reactions[-1].reaction == "👍"


async def test_recovery_finishes_a_dispatch_interrupted_mid_flight(
    destinations, session_factory
):
    from datetime import timedelta

    from app.utils.time import utcnow

    services = destinations
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅")

    order_id, _chat, _message = await _pending_order(services)
    await operator_replies(services, order_id, photo_payload(WORK_GROUP_CHAT_ID), OPERATOR_ID)

    # Simulate a crash that left the dispatch row stuck in SENDING.
    async with session_scope() as session:
        dispatches = await AcknowledgementRepository(session).list_dispatches(order_id)
        dispatches[0].status = DispatchStatus.SENDING
        dispatches[0].updated_at = utcnow() - timedelta(minutes=10)
        order = await OrderRepository(session).get(order_id)
        order.result_dispatch_status = OrderDispatchState.PENDING

    services.gateway.reset()
    await services.finalizer.recover()

    order = await get_order(order_id)
    assert order.result_dispatch_status == OrderDispatchState.SENT
    assert len(services.gateway.messages_in(SUCCESS_CHAT_ID)) == 1


# ---------------------------------------------------------------------------
# Manual override
# ---------------------------------------------------------------------------
async def test_manual_override_can_dispatch_without_acknowledging(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅")
    order_id, _chat, _message = await _pending_order(services)

    await services.finalizer.manual_override(
        order_id,
        OrderStatus.SUCCESS,
        admin_user_id=1000,
        dispatch_result=True,
        apply_acknowledgement=False,
    )

    order = await get_order(order_id)
    assert order.status == OrderStatus.SUCCESS
    assert len(services.gateway.messages_in(SUCCESS_CHAT_ID)) == 1
    assert services.gateway.reactions == []


async def test_manual_override_status_only_dispatches_nothing(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅")
    order_id, _chat, _message = await _pending_order(services)

    await services.finalizer.manual_override(
        order_id,
        OrderStatus.FAILED,
        admin_user_id=1000,
        dispatch_result=False,
        apply_acknowledgement=False,
    )

    order = await get_order(order_id)
    assert order.status == OrderStatus.FAILED
    assert services.gateway.messages_in(FAILURE_CHAT_ID) == []
    assert services.gateway.reactions == []


async def test_manual_override_to_success_targets_the_order_message(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅")
    order_id, chat_id, message_id = await _pending_order(services)

    await services.finalizer.manual_override(order_id, OrderStatus.SUCCESS, 1000)

    reaction = services.gateway.reactions[0]
    assert (reaction.chat_id, reaction.message_id) == (chat_id, message_id)


async def test_no_destination_configured_still_acknowledges(wired):
    """Documented interpretation: with nothing to dispatch the gate is vacuous."""
    services = wired
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅")

    order_id, _chat, _message = await _pending_order(services)
    await operator_replies(services, order_id, photo_payload(WORK_GROUP_CHAT_ID), OPERATOR_ID)

    order = await get_order(order_id)
    assert order.result_dispatch_status == OrderDispatchState.NOT_REQUIRED
    assert order.acknowledgement_status == AcknowledgementStatus.APPLIED
