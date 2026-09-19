"""The store update survives a momentary failure.

The bug behind this suite: an order was finalised, the store call hit a
``TimeoutError``, the admins got one alert and the order stayed unfinished
in WooCommerce for good -- nothing ever tried again. Every test here pins
one part of the answer: retry what looks momentary, stop early on what does
not, and never let a retry duplicate work the store already did.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.database.engine import session_scope
from app.database.repositories import (
    ResultConfigRepository,
    SettingRepository,
    WooCommerceRepository,
)
from app.dispatch.policy import StoreRetryPolicy, load_store_policy
from app.utils.enums import (
    DispatchStatus,
    OrderStatus,
    SettingKey,
    StoreAlertMode,
)
from app.utils.time import utcnow
from tests.conftest import (
    SOURCE_CHAT_ID,
    configure_acknowledgement,
)
from tests.fakes import FakeWooCommerceClient
from tests.helpers import deliver_order, get_order, text_payload

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures for a configured store
# ---------------------------------------------------------------------------
async def _configure(**settings) -> None:
    async with session_scope() as session:
        repo = SettingRepository(session)
        await repo.set(SettingKey.WOO_BASE_URL, "https://shop.example")
        await repo.set(SettingKey.WOO_CONSUMER_KEY, "ck_test")
        await repo.set(SettingKey.WOO_CONSUMER_SECRET, "cs_test")
        await repo.set(SettingKey.ORDER_NUMBER_ENABLED, "true")
        await repo.set(SettingKey.ORDER_NUMBER_LENGTH, "7")
        for key, value in settings.items():
            await repo.set(key, str(value))
        await ResultConfigRepository(session).update(
            OrderStatus.SUCCESS,
            woo_enabled=True,
            woo_status="completed",
            woo_note_enabled=False,
        )


async def _finalised_order(services, number: str = "1234567") -> int:
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)
    order_id = await deliver_order(
        services, text_payload(SOURCE_CHAT_ID, f"Apple\n{number}")
    )
    await services.finalizer.manual_override(order_id, OrderStatus.SUCCESS, 1000)
    return order_id


async def _call(order_id: int):
    async with session_scope() as session:
        return await WooCommerceRepository(session).get_call(order_id)


async def _due_now(order_id: int) -> None:
    """Pretend the backoff has elapsed, without sleeping through it."""
    async with session_scope() as session:
        call = await WooCommerceRepository(session).get_call(order_id)
        call.next_attempt_at = utcnow() - timedelta(seconds=1)


# ---------------------------------------------------------------------------
# A momentary failure is retried, not abandoned
# ---------------------------------------------------------------------------
async def test_a_timeout_schedules_another_attempt_instead_of_alerting(destinations):
    services = destinations
    await _configure()
    FakeWooCommerceClient.fail_with = "no answer from the store within 30s (PUT orders)"

    order_id = await _finalised_order(services)

    call = await _call(order_id)
    assert call.status == DispatchStatus.FAILED
    assert call.permanent is False
    assert call.attempts == 1
    # Two minutes by default -- the admins are not woken for this.
    assert call.next_attempt_at is not None
    assert call.next_attempt_at > utcnow()
    assert "store_update_failed" not in services.notifier.kinds()


async def test_the_retry_waits_for_its_turn(destinations):
    services = destinations
    await _configure()
    FakeWooCommerceClient.fail_with = "temporary failure"

    order_id = await _finalised_order(services)
    FakeWooCommerceClient.fail_with = None

    # The backoff has not elapsed, so the worker leaves the row alone.
    assert await services.store.retry_due() == 0
    assert (await _call(order_id)).status == DispatchStatus.FAILED
    assert FakeWooCommerceClient.calls == []


async def test_the_scheduled_retry_updates_the_store(destinations):
    services = destinations
    await _configure()
    FakeWooCommerceClient.fail_with = "temporary failure"

    order_id = await _finalised_order(services)
    FakeWooCommerceClient.fail_with = None
    await _due_now(order_id)

    assert await services.store.retry_due() == 1

    call = await _call(order_id)
    assert call.status == DispatchStatus.SENT
    assert call.attempts == 2
    assert call.next_attempt_at is None
    assert FakeWooCommerceClient.calls[0]["order_number"] == "1234567"
    # The second attempt knows it is one, so a note is never added twice.
    assert FakeWooCommerceClient.calls[0]["repeat_attempt"] is True


async def test_each_wait_is_twice_the_last(destinations):
    services = destinations
    await _configure(**{SettingKey.WOO_RETRY_BASE_MINUTES: 2})
    FakeWooCommerceClient.fail_with = "temporary failure"

    order_id = await _finalised_order(services)
    waits = []
    for _ in range(3):
        call = await _call(order_id)
        waits.append(round((call.next_attempt_at - utcnow()).total_seconds() / 60))
        await _due_now(order_id)
        await services.store.retry_due()

    assert waits == [2, 4, 8]


async def test_the_wait_never_grows_past_its_cap(destinations):
    services = destinations
    await _configure(
        **{
            SettingKey.WOO_RETRY_BASE_MINUTES: 10,
            SettingKey.WOO_RETRY_MAX_MINUTES: 15,
        }
    )
    FakeWooCommerceClient.fail_with = "temporary failure"

    order_id = await _finalised_order(services)
    await _due_now(order_id)
    await services.store.retry_due()

    call = await _call(order_id)
    minutes = (call.next_attempt_at - utcnow()).total_seconds() / 60
    assert 14 <= minutes <= 15


# ---------------------------------------------------------------------------
# Giving up, and saying so exactly once
# ---------------------------------------------------------------------------
async def test_the_admins_hear_about_it_once_the_budget_is_spent(destinations):
    services = destinations
    await _configure(**{SettingKey.WOO_RETRY_MAX_ATTEMPTS: 2})
    FakeWooCommerceClient.fail_with = "temporary failure"

    order_id = await _finalised_order(services)
    assert services.notifier.kinds() == []

    await _due_now(order_id)
    await services.store.retry_due()

    call = await _call(order_id)
    assert call.attempts == 2
    assert call.next_attempt_at is None
    assert call.alerted is True
    assert services.notifier.kinds().count("store_update_failed") == 1

    # Nothing is due any more, and no second alert is ever sent.
    assert await services.store.retry_due() == 0
    assert services.notifier.kinds().count("store_update_failed") == 1


async def test_a_spent_budget_is_not_revived_by_the_next_order_event(destinations):
    """A later event on the order re-runs the pipeline, which must not retry."""
    services = destinations
    await _configure(**{SettingKey.WOO_RETRY_MAX_ATTEMPTS: 1})
    FakeWooCommerceClient.fail_with = "temporary failure"

    order_id = await _finalised_order(services)
    assert (await _call(order_id)).attempts == 1

    FakeWooCommerceClient.fail_with = None
    await services.finalizer.run_pipeline(order_id)

    call = await _call(order_id)
    assert call.attempts == 1
    assert call.status == DispatchStatus.FAILED
    assert FakeWooCommerceClient.calls == []


async def test_a_spent_budget_reads_as_stopped_on_screen(destinations):
    from app.admin import strings, texts

    services = destinations
    await _configure(**{SettingKey.WOO_RETRY_MAX_ATTEMPTS: 1})
    FakeWooCommerceClient.fail_with = "temporary failure"
    order_id = await _finalised_order(services)

    rendered = texts.store_section(await _call(order_id), max_attempts=1)

    assert strings.WOO_QUEUE_STOPPED in rendered


async def test_a_permanent_failure_is_reported_immediately(destinations):
    services = destinations
    await _configure()
    FakeWooCommerceClient.fail_with = "HTTP 401: invalid signature"
    FakeWooCommerceClient.fail_permanently = True

    order_id = await _finalised_order(services)

    call = await _call(order_id)
    assert call.permanent is True
    assert call.next_attempt_at is None
    assert call.attempts == 1
    assert "store_update_failed" in services.notifier.kinds()
    # A permanent failure is never picked up again on its own.
    assert await services.store.retry_due() == 0


async def test_a_recovery_after_an_alert_is_reported_too(destinations):
    services = destinations
    await _configure(**{SettingKey.WOO_RETRY_MAX_ATTEMPTS: 1})
    FakeWooCommerceClient.fail_with = "temporary failure"

    order_id = await _finalised_order(services)
    assert "store_update_failed" in services.notifier.kinds()

    FakeWooCommerceClient.fail_with = None
    await services.store.retry_now(order_id)

    assert (await _call(order_id)).status == DispatchStatus.SENT
    assert "store_update_recovered" in services.notifier.kinds()


async def test_every_attempt_can_be_announced_when_asked(destinations):
    services = destinations
    await _configure(**{SettingKey.WOO_ALERT_MODE: StoreAlertMode.EVERY_ATTEMPT.value})
    FakeWooCommerceClient.fail_with = "temporary failure"

    order_id = await _finalised_order(services)
    await _due_now(order_id)
    await services.store.retry_due()

    assert services.notifier.kinds().count("store_update_retrying") == 2


async def test_retries_can_be_switched_off_entirely(destinations):
    services = destinations
    await _configure(**{SettingKey.WOO_RETRY_ENABLED: "false"})
    FakeWooCommerceClient.fail_with = "temporary failure"

    order_id = await _finalised_order(services)

    call = await _call(order_id)
    assert call.next_attempt_at is None
    assert "store_update_failed" in services.notifier.kinds()
    assert await services.store.retry_due() == 0


# ---------------------------------------------------------------------------
# What an admin can do by hand
# ---------------------------------------------------------------------------
async def test_a_first_attempt_is_made_even_with_retries_switched_off(destinations):
    """Switching retries off must not mean "never call the store at all"."""
    services = destinations
    await _configure(**{SettingKey.WOO_RETRY_ENABLED: "false"})

    order_id = await _finalised_order(services)
    # As if the process died between creating the outbox row and calling.
    async with session_scope() as session:
        call = await WooCommerceRepository(session).get_call(order_id)
        call.status = DispatchStatus.PENDING
        call.attempts = 0
    FakeWooCommerceClient.reset()

    assert await services.store.retry_due() == 1
    assert (await _call(order_id)).status == DispatchStatus.SENT


async def test_an_admin_can_force_a_retry_the_schedule_would_refuse(destinations):
    services = destinations
    await _configure()
    FakeWooCommerceClient.fail_with = "temporary failure"

    order_id = await _finalised_order(services)
    FakeWooCommerceClient.fail_with = None

    outcome = await services.store.retry_now(order_id)

    assert outcome.ok is True
    call = await _call(order_id)
    assert call.status == DispatchStatus.SENT
    # The manual attempt restarts the budget rather than spending it.
    assert call.attempts == 1


async def test_a_permanent_failure_can_be_retried_after_the_admin_fixes_it(
    destinations,
):
    services = destinations
    await _configure()
    FakeWooCommerceClient.fail_with = "HTTP 401: invalid signature"
    FakeWooCommerceClient.fail_permanently = True

    order_id = await _finalised_order(services)
    assert (await _call(order_id)).permanent is True

    FakeWooCommerceClient.fail_with = None
    FakeWooCommerceClient.fail_permanently = False
    await services.store.retry_now(order_id)

    call = await _call(order_id)
    assert call.status == DispatchStatus.SENT
    assert call.permanent is False


async def test_an_order_with_no_store_update_is_left_alone(destinations):
    services = destinations
    await _configure()
    async with session_scope() as session:
        await ResultConfigRepository(session).update(
            OrderStatus.SUCCESS, woo_enabled=False
        )

    order_id = await _finalised_order(services)
    outcome = await services.store.retry_now(order_id)

    assert outcome.attempted is False
    assert await _call(order_id) is None


# ---------------------------------------------------------------------------
# Crashes and restarts
# ---------------------------------------------------------------------------
async def test_a_call_stuck_mid_flight_is_released_and_retried(destinations):
    services = destinations
    await _configure()
    FakeWooCommerceClient.fail_with = "temporary failure"

    order_id = await _finalised_order(services)
    FakeWooCommerceClient.fail_with = None

    # As if the process died between the claim and the store's answer.
    async with session_scope() as session:
        call = await WooCommerceRepository(session).get_call(order_id)
        call.status = DispatchStatus.SENDING
        call.updated_at = utcnow() - timedelta(hours=1)

    assert await services.store.retry_due() == 1
    assert (await _call(order_id)).status == DispatchStatus.SENT


async def test_startup_recovery_picks_up_a_due_store_call(destinations):
    services = destinations
    await _configure()
    FakeWooCommerceClient.fail_with = "temporary failure"

    order_id = await _finalised_order(services)
    FakeWooCommerceClient.fail_with = None
    await _due_now(order_id)

    counters = await services.finalizer.recover()

    assert counters["store_calls_retried"] == 1
    assert (await _call(order_id)).status == DispatchStatus.SENT


async def test_the_order_itself_is_never_touched_by_a_store_failure(destinations):
    services = destinations
    await _configure()
    FakeWooCommerceClient.fail_with = "temporary failure"

    order_id = await _finalised_order(services)

    order = await get_order(order_id)
    assert order.status == OrderStatus.SUCCESS
    assert order.result_dispatch_status == "SENT"


# ---------------------------------------------------------------------------
# The policy itself
# ---------------------------------------------------------------------------
async def test_the_policy_clamps_a_value_no_panel_could_produce(session_factory):
    async with session_scope() as session:
        repo = SettingRepository(session)
        await repo.set(SettingKey.WOO_RETRY_MAX_ATTEMPTS, "9999")
        await repo.set(SettingKey.WOO_REQUEST_TIMEOUT, "0")
        await repo.set(SettingKey.WOO_QUICK_RETRIES, "not a number")

    async with session_scope() as session:
        policy = await load_store_policy(session)

    assert policy.max_attempts == 20
    assert policy.request_timeout == 5
    assert policy.quick_retries == 2


async def test_the_default_policy_covers_about_half_an_hour(session_factory):
    async with session_scope() as session:
        policy = await load_store_policy(session)

    total = sum(
        policy.delay_after(attempt).total_seconds() / 60
        for attempt in range(1, policy.max_attempts)
    )
    assert policy.enabled is True
    assert policy.alert_mode is StoreAlertMode.EXHAUSTED
    assert 25 <= total <= 35


def test_one_call_can_never_hang_the_pipeline_for_long():
    """The first attempt runs inside a Telegram handler."""
    generous = StoreRetryPolicy(request_timeout=120, quick_retries=5)
    assert generous.call_budget <= 600
    assert StoreRetryPolicy().call_budget <= 300


# ---------------------------------------------------------------------------
# The background loop
# ---------------------------------------------------------------------------
async def test_the_worker_keeps_asking_and_survives_a_bad_tick():
    """No Telegram update ever wakes a finished order, so this loop must."""
    from app.dispatch.retry_worker import StoreRetryWorker

    ticks: list[int] = []

    class FlakyStore:
        async def retry_due(self) -> int:
            ticks.append(len(ticks))
            if len(ticks) == 1:
                raise RuntimeError("the database blinked")
            return 1

    worker = StoreRetryWorker(FlakyStore(), interval=0.01)
    worker.start()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if len(ticks) >= 3:
            break
    await worker.stop()

    # The first tick raised; the loop carried on regardless.
    assert len(ticks) >= 3


async def test_stopping_the_worker_twice_is_harmless():
    from app.dispatch.retry_worker import StoreRetryWorker

    class IdleStore:
        async def retry_due(self) -> int:
            return 0

    worker = StoreRetryWorker(IdleStore(), interval=0.01)
    worker.start()
    worker.start()  # already running: not started twice
    await worker.stop()
    await worker.stop()


# ---------------------------------------------------------------------------
# What the admin actually reads on screen
# ---------------------------------------------------------------------------
def _screen_call(**fields):
    from app.database.models import WooCommerceCall

    values = {
        "order_id": 8,
        "order_status": OrderStatus.SUCCESS,
        "store_order_number": "2795541",
        "target_status": "completed",
        "status": DispatchStatus.FAILED,
        "attempts": 1,
        "error": "no answer from the store within 30s (PUT orders)",
        "permanent": False,
        "alerted": False,
        "next_attempt_at": None,
    }
    values.update(fields)
    return WooCommerceCall(**values)


def test_the_settings_screen_spells_out_the_schedule():
    from app.admin import texts

    rendered = texts.store_retry_screen(StoreRetryPolicy(max_attempts=4))

    # 2, 4, 8 minutes in Persian digits, as the three waits before giving up.
    assert "۲، ۴، ۸" in rendered
    assert "۳۰" in rendered  # the request timeout


def test_a_disabled_retry_says_so_on_the_screen():
    from app.admin import strings, texts

    hint = strings.WOO_RETRY_DISABLED_HINT
    assert hint in texts.store_retry_screen(StoreRetryPolicy(enabled=False))
    assert hint not in texts.store_retry_screen(StoreRetryPolicy())


def test_the_queue_screen_shows_each_call_and_its_next_attempt():
    from app.admin import texts

    now = utcnow()
    calls = [
        _screen_call(next_attempt_at=now + timedelta(minutes=5)),
        _screen_call(order_id=9, permanent=True, store_order_number="7654321"),
    ]
    rendered = texts.store_queue_screen(
        calls, {8: "order8", 9: "order9"}, waiting=1, abandoned=1, max_attempts=5,
        now=now,
    )

    assert "order8" in rendered and "order9" in rendered
    assert "۲۷۹۵۵۴۱" in rendered
    assert "⏳" in rendered and "⛔️" in rendered


def test_an_empty_queue_says_there_is_nothing_to_do():
    from app.admin import strings, texts

    rendered = texts.store_queue_screen([], {}, 0, 0, 5, utcnow())

    assert strings.WOO_QUEUE_EMPTY in rendered


def test_the_order_screen_gains_a_store_section_only_when_there_is_one():
    from app.admin import texts

    assert texts.store_section(None) == ""
    rendered = texts.store_section(_screen_call(attempts=3))
    assert "۲۷۹۵۵۴۱" in rendered
    assert "۳" in rendered
