"""The Telegram result and its reaction survive a momentary failure.

Same shape as the store's retry suite, for the two steps that happen before
it: sending the result to its destinations, and placing the acknowledgement
reaction once that send is confirmed. A failure Telegram calls final (a bot
removed from the chat, a reaction the chat forbids) must still stop at once
-- retrying a verdict only burns rate limit.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.database.engine import session_scope
from app.database.repositories import (
    AcknowledgementRepository,
    OrderRepository,
    SettingRepository,
)
from app.dispatch.policy import TelegramRetryPolicy, load_telegram_policy
from app.utils.enums import (
    AcknowledgementStatus,
    DispatchStatus,
    OrderDispatchState,
    OrderStatus,
    RetryAlertMode,
    SettingKey,
    SignalKey,
)
from app.utils.time import utcnow
from tests.conftest import (
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
    operator_replies,
    photo_payload,
    text_payload,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _settings(**values) -> None:
    async with session_scope() as session:
        repo = SettingRepository(session)
        for key, value in values.items():
            await repo.set(key, str(value))


async def _finalised(services) -> int:
    """An order that reached SUCCESS through an operator's photo."""
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "New Order"))
    await operator_replies(
        services, order_id, photo_payload(WORK_GROUP_CHAT_ID), OPERATOR_ID
    )
    return order_id


async def _dispatch(order_id: int):
    async with session_scope() as session:
        rows = await AcknowledgementRepository(session).list_dispatches(order_id)
    return rows[0]


async def _dispatch_due_now(order_id: int) -> None:
    async with session_scope() as session:
        for row in await AcknowledgementRepository(session).list_dispatches(order_id):
            row.next_attempt_at = utcnow() - timedelta(seconds=1)


async def _ack_due_now(order_id: int) -> None:
    async with session_scope() as session:
        order = await OrderRepository(session).get(order_id)
        order.acknowledgement_next_attempt_at = utcnow() - timedelta(seconds=1)


# ---------------------------------------------------------------------------
# Result dispatch
# ---------------------------------------------------------------------------
async def test_an_unreachable_destination_schedules_another_attempt(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)
    services.gateway.flaky_chats.add(SUCCESS_CHAT_ID)

    order_id = await _finalised(services)

    row = await _dispatch(order_id)
    assert row.status == DispatchStatus.FAILED
    assert row.permanent is False
    assert row.attempts == 1
    assert row.next_attempt_at is not None and row.next_attempt_at > utcnow()
    # A momentary failure does not wake anyone up.
    assert "dispatch_failed" not in services.notifier.kinds()


async def test_the_dispatch_retry_waits_for_its_turn(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)
    services.gateway.flaky_chats.add(SUCCESS_CHAT_ID)

    await _finalised(services)
    services.gateway.flaky_chats.discard(SUCCESS_CHAT_ID)

    # The backoff has not elapsed, so the worker leaves the row alone.
    assert await services.dispatch.retry_due() == []
    assert services.gateway.orders_in(SUCCESS_CHAT_ID) == []


async def test_the_scheduled_retry_delivers_the_result(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)
    services.gateway.flaky_chats.add(SUCCESS_CHAT_ID)

    order_id = await _finalised(services)
    services.gateway.flaky_chats.discard(SUCCESS_CHAT_ID)
    await _dispatch_due_now(order_id)

    assert await services.dispatch.retry_due() == [order_id]

    row = await _dispatch(order_id)
    assert row.status == DispatchStatus.SENT
    assert row.attempts == 2
    assert row.next_attempt_at is None
    assert len(services.gateway.orders_in(SUCCESS_CHAT_ID)) == 1
    assert (await get_order(order_id)).result_dispatch_status == OrderDispatchState.SENT


async def test_a_result_is_never_delivered_twice_by_a_retry(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)

    order_id = await _finalised(services)
    assert len(services.gateway.orders_in(SUCCESS_CHAT_ID)) == 1

    for _ in range(3):
        await services.dispatch.retry_due()
        await services.finalizer.run_pipeline(order_id)

    assert len(services.gateway.orders_in(SUCCESS_CHAT_ID)) == 1


async def test_each_dispatch_wait_is_twice_the_last(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)
    await _settings(**{SettingKey.TELEGRAM_RETRY_BASE_MINUTES: 2})
    services.gateway.flaky_chats.add(SUCCESS_CHAT_ID)

    order_id = await _finalised(services)
    waits = []
    for _ in range(3):
        row = await _dispatch(order_id)
        waits.append(round((row.next_attempt_at - utcnow()).total_seconds() / 60))
        await _dispatch_due_now(order_id)
        await services.dispatch.retry_due()

    assert waits == [2, 4, 8]


async def test_the_admins_hear_about_a_dispatch_once_the_budget_is_spent(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)
    await _settings(**{SettingKey.TELEGRAM_RETRY_MAX_ATTEMPTS: 2})
    services.gateway.flaky_chats.add(SUCCESS_CHAT_ID)

    order_id = await _finalised(services)
    assert "dispatch_failed" not in services.notifier.kinds()

    await _dispatch_due_now(order_id)
    await services.dispatch.retry_due()

    row = await _dispatch(order_id)
    assert row.attempts == 2
    assert row.next_attempt_at is None
    assert row.alerted is True
    assert services.notifier.kinds().count("dispatch_failed") == 1

    # Nothing is due any more, and no second alert is ever sent.
    assert await services.dispatch.retry_due() == []
    assert services.notifier.kinds().count("dispatch_failed") == 1


async def test_a_chat_that_refuses_the_bot_is_reported_immediately(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)
    # FakeTelegramError stands in for Forbidden: nothing a retry can fix.
    services.gateway.failing_chats.add(SUCCESS_CHAT_ID)

    order_id = await _finalised(services)

    row = await _dispatch(order_id)
    assert row.permanent is True
    assert row.next_attempt_at is None
    assert row.attempts == 1
    assert "dispatch_failed" in services.notifier.kinds()
    assert await services.dispatch.retry_due() == []


async def test_a_delivery_that_finally_works_is_reported_too(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)
    await _settings(**{SettingKey.TELEGRAM_RETRY_MAX_ATTEMPTS: 1})
    services.gateway.flaky_chats.add(SUCCESS_CHAT_ID)

    order_id = await _finalised(services)
    assert "dispatch_failed" in services.notifier.kinds()

    services.gateway.flaky_chats.discard(SUCCESS_CHAT_ID)
    await services.dispatch.retry_now(order_id)

    assert (await _dispatch(order_id)).status == DispatchStatus.SENT
    assert "dispatch_recovered" in services.notifier.kinds()


async def test_every_dispatch_attempt_can_be_announced_when_asked(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)
    await _settings(**{SettingKey.TELEGRAM_ALERT_MODE: RetryAlertMode.EVERY_ATTEMPT})
    services.gateway.flaky_chats.add(SUCCESS_CHAT_ID)

    order_id = await _finalised(services)
    await _dispatch_due_now(order_id)
    await services.dispatch.retry_due()

    assert services.notifier.kinds().count("dispatch_retrying") == 2


async def test_a_spent_dispatch_budget_is_not_revived_by_the_next_event(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)
    await _settings(**{SettingKey.TELEGRAM_RETRY_MAX_ATTEMPTS: 1})
    services.gateway.flaky_chats.add(SUCCESS_CHAT_ID)

    order_id = await _finalised(services)
    services.gateway.flaky_chats.discard(SUCCESS_CHAT_ID)
    await services.finalizer.run_pipeline(order_id)

    assert (await _dispatch(order_id)).attempts == 1
    assert services.gateway.orders_in(SUCCESS_CHAT_ID) == []


async def test_an_admin_can_force_a_delivery_the_schedule_would_refuse(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)
    services.gateway.flaky_chats.add(SUCCESS_CHAT_ID)

    order_id = await _finalised(services)
    services.gateway.flaky_chats.discard(SUCCESS_CHAT_ID)
    await services.finalizer.run_pipeline(order_id, force=True)

    row = await _dispatch(order_id)
    assert row.status == DispatchStatus.SENT
    # The manual attempt restarts the budget rather than spending it.
    assert row.attempts == 1
    assert len(services.gateway.orders_in(SUCCESS_CHAT_ID)) == 1


# ---------------------------------------------------------------------------
# The acknowledgement that follows the dispatch
# ---------------------------------------------------------------------------
async def test_a_refused_reaction_schedules_another_attempt(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅", max_retry_count=3)
    services.gateway.flaky_reactions = True

    order_id = await _finalised(services)

    order = await get_order(order_id)
    assert order.acknowledgement_status == AcknowledgementStatus.FAILED
    assert order.acknowledgement_permanent is False
    assert order.acknowledgement_next_attempt_at is not None
    assert "acknowledgement_failed" not in services.notifier.kinds()


async def test_the_scheduled_reaction_retry_applies_it(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅", max_retry_count=3)
    services.gateway.flaky_reactions = True

    order_id = await _finalised(services)
    services.gateway.flaky_reactions = False
    await _ack_due_now(order_id)

    assert await services.acknowledgements.retry_due() == 1

    order = await get_order(order_id)
    assert order.acknowledgement_status == AcknowledgementStatus.APPLIED
    assert order.acknowledgement_next_attempt_at is None
    assert len(services.gateway.reactions) == 1


async def test_the_reaction_retry_waits_for_its_turn(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅", max_retry_count=3)
    services.gateway.flaky_reactions = True

    await _finalised(services)
    services.gateway.flaky_reactions = False

    assert await services.acknowledgements.retry_due() == 0
    assert services.gateway.reactions == []


async def test_the_admins_hear_about_a_reaction_once_its_budget_is_spent(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅", max_retry_count=2)
    services.gateway.flaky_reactions = True

    order_id = await _finalised(services)
    assert "acknowledgement_failed" not in services.notifier.kinds()

    await _ack_due_now(order_id)
    await services.acknowledgements.retry_due()

    order = await get_order(order_id)
    assert order.acknowledgement_attempts == 2
    assert order.acknowledgement_next_attempt_at is None
    assert services.notifier.kinds().count("acknowledgement_failed") == 1


async def test_a_reaction_that_finally_works_is_reported_too(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅", max_retry_count=1)
    services.gateway.flaky_reactions = True

    order_id = await _finalised(services)
    assert "acknowledgement_failed" in services.notifier.kinds()

    services.gateway.flaky_reactions = False
    await services.acknowledgements.retry_now(order_id)

    assert (await get_order(order_id)).acknowledgement_status == (
        AcknowledgementStatus.APPLIED
    )
    assert "acknowledgement_recovered" in services.notifier.kinds()


async def test_a_reaction_is_never_applied_before_its_result_arrives(destinations):
    """The whole point of the acknowledgement, unchanged by the retries."""
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅")
    services.gateway.flaky_chats.add(SUCCESS_CHAT_ID)

    order_id = await _finalised(services)

    assert services.gateway.reactions == []
    order = await get_order(order_id)
    assert order.acknowledgement_status == AcknowledgementStatus.PENDING
    # Nothing is scheduled for it either: it is waiting on the dispatch.
    assert order.acknowledgement_next_attempt_at is None


async def test_the_reaction_follows_a_delivery_the_worker_repaired(destinations):
    """One tick: the result goes out, and the reaction goes on right after."""
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅")
    services.gateway.flaky_chats.add(SUCCESS_CHAT_ID)

    order_id = await _finalised(services)
    services.gateway.flaky_chats.discard(SUCCESS_CHAT_ID)
    await _dispatch_due_now(order_id)

    counters = await services.finalizer.retry_due()

    assert counters["dispatches"] == 1
    order = await get_order(order_id)
    assert order.result_dispatch_status == OrderDispatchState.SENT
    assert order.acknowledgement_status == AcknowledgementStatus.APPLIED


# ---------------------------------------------------------------------------
# Crashes, restarts and the policy itself
# ---------------------------------------------------------------------------
async def test_a_dispatch_stuck_mid_flight_is_released_and_retried(destinations):
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)
    services.gateway.flaky_chats.add(SUCCESS_CHAT_ID)

    order_id = await _finalised(services)
    services.gateway.flaky_chats.discard(SUCCESS_CHAT_ID)

    async with session_scope() as session:
        for row in await AcknowledgementRepository(session).list_dispatches(order_id):
            row.status = DispatchStatus.SENDING
            row.updated_at = utcnow() - timedelta(hours=1)

    assert await services.dispatch.retry_due() == [order_id]
    assert (await _dispatch(order_id)).status == DispatchStatus.SENT


async def test_startup_recovery_picks_up_a_due_dispatch(destinations):
    """A restart is a retry: whatever is due goes out before polling starts."""
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)
    services.gateway.flaky_chats.add(SUCCESS_CHAT_ID)

    order_id = await _finalised(services)
    services.gateway.flaky_chats.discard(SUCCESS_CHAT_ID)
    await _dispatch_due_now(order_id)

    counters = await services.finalizer.recover()

    assert sum(counters.values()) > 0
    assert (await _dispatch(order_id)).status == DispatchStatus.SENT
    assert len(services.gateway.orders_in(SUCCESS_CHAT_ID)) == 1


async def test_startup_recovery_leaves_a_dispatch_that_is_not_due_alone(destinations):
    """Restarting must not shortcut a backoff the last failure just set."""
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)
    services.gateway.flaky_chats.add(SUCCESS_CHAT_ID)

    order_id = await _finalised(services)
    services.gateway.flaky_chats.discard(SUCCESS_CHAT_ID)

    await services.finalizer.recover()

    assert (await _dispatch(order_id)).status == DispatchStatus.FAILED
    assert services.gateway.orders_in(SUCCESS_CHAT_ID) == []


async def test_a_first_delivery_happens_even_with_retries_switched_off(destinations):
    """Switching retries off must not mean "never deliver at all"."""
    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)
    await _settings(**{SettingKey.TELEGRAM_RETRY_ENABLED: "false"})
    services.gateway.failing_chats.add(SUCCESS_CHAT_ID)

    order_id = await _finalised(services)
    services.gateway.failing_chats.discard(SUCCESS_CHAT_ID)

    # As if the process died between creating the outbox row and sending.
    async with session_scope() as session:
        for row in await AcknowledgementRepository(session).list_dispatches(order_id):
            row.status = DispatchStatus.PENDING
            row.attempts = 0
            row.permanent = False
            row.next_attempt_at = None

    assert await services.dispatch.retry_due() == [order_id]
    assert (await _dispatch(order_id)).status == DispatchStatus.SENT


async def test_the_telegram_policy_clamps_a_value_no_panel_could_produce(
    session_factory,
):
    await _settings(
        **{
            SettingKey.TELEGRAM_RETRY_MAX_ATTEMPTS: "9999",
            SettingKey.TELEGRAM_RETRY_BASE_MINUTES: "not a number",
        }
    )
    async with session_scope() as session:
        policy = await load_telegram_policy(session)

    assert policy.max_attempts == 20
    assert policy.base_minutes == 2


async def test_the_default_telegram_policy_covers_about_half_an_hour(session_factory):
    async with session_scope() as session:
        policy = await load_telegram_policy(session)

    total = sum(
        policy.delay_after(attempt).total_seconds() / 60
        for attempt in range(1, policy.max_attempts)
    )
    assert policy.enabled is True
    assert policy.alert_mode is RetryAlertMode.EXHAUSTED
    assert 25 <= total <= 35


def test_the_acknowledgement_budget_overrides_the_policys_own():
    """Its per-status setting decides how many times, not the shared one."""
    policy = TelegramRetryPolicy(max_attempts=10)

    assert policy.has_budget_after(3, max_attempts=3) is False
    assert policy.has_budget_after(3) is True


# ---------------------------------------------------------------------------
# What the admin actually reads on screen
# ---------------------------------------------------------------------------
def test_the_settings_screen_spells_out_the_schedule():
    from app.admin import strings, texts

    rendered = texts.telegram_retry_screen(
        TelegramRetryPolicy(max_attempts=4), immediate_attempts=3
    )

    # 2, 4, 8 minutes in Persian digits, as the three waits before giving up.
    assert "۲، ۴، ۸" in rendered
    assert strings.TG_RETRY_DISABLED_HINT not in rendered

    off = texts.telegram_retry_screen(
        TelegramRetryPolicy(enabled=False), immediate_attempts=3
    )
    assert strings.TG_RETRY_DISABLED_HINT in off


async def test_the_queue_screen_shows_what_is_outstanding(destinations):
    from app.admin import texts

    services = destinations
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅")
    services.gateway.flaky_chats.add(SUCCESS_CHAT_ID)
    order_id = await _finalised(services)

    async with session_scope() as session:
        acks = AcknowledgementRepository(session)
        rows = await acks.unfinished_dispatches()
        waiting, abandoned = await acks.dispatch_counts(5)
        pending_acks = await acks.unfinished_acknowledgements()
        order = await OrderRepository(session).get(order_id)

    rendered = texts.delivery_queue_screen(
        rows, pending_acks, {order_id: order.display_number}, waiting, abandoned, 5,
        utcnow(),
    )

    assert order.display_number in rendered
    assert waiting == 1 and abandoned == 0
    assert "⏳" in rendered


def test_an_empty_queue_says_there_is_nothing_to_do():
    from app.admin import strings, texts

    rendered = texts.delivery_queue_screen([], [], {}, 0, 0, 5, utcnow())

    assert strings.TG_QUEUE_EMPTY in rendered
