"""Reporting formulas, audit trail and admin-facing behaviour."""

from __future__ import annotations

import pytest

from app.database.engine import session_scope
from app.database.repositories import (
    AdminRepository,
    AuditRepository,
    OperatorRepository,
    OrderRepository,
    SettingRepository,
)
from app.orders.numbering import render_display_number
from app.reports.service import ReportPeriod
from app.utils.enums import (
    AdminRole,
    AuditEvent,
    OrderStatus,
    SettingKey,
    SignalKey,
)
from tests.conftest import (
    OPERATOR_ID,
    SOURCE_CHAT_ID,
    WORK_GROUP_CHAT_ID,
    configure_acknowledgement,
    configure_rule,
)
from tests.helpers import (
    deliver_order,
    operator_replies,
    photo_payload,
    primary_work_group_message,
    text_payload,
)

pytestmark = pytest.mark.asyncio


async def _finalise(services, status: OrderStatus) -> int:
    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "order"))
    await services.finalizer.manual_override(order_id, status, admin_user_id=1000)
    return order_id


async def test_report_rates_use_completed_orders_only(destinations):
    services = destinations
    for _ in range(6):
        await _finalise(services, OrderStatus.SUCCESS)
    for _ in range(2):
        await _finalise(services, OrderStatus.FAILED)
    # Two orders stay pending and must not affect the rates.
    await deliver_order(services, text_payload(SOURCE_CHAT_ID, "pending 1"))
    await deliver_order(services, text_payload(SOURCE_CHAT_ID, "pending 2"))

    report = await services.reports.order_report(ReportPeriod.today())
    assert report.total == 10
    assert (report.success, report.failed, report.pending) == (6, 2, 2)
    assert report.completed == 8
    assert report.success_rate == pytest.approx(75.0)
    assert report.failure_rate == pytest.approx(25.0)


async def test_report_matches_the_spec_example():
    """150 orders: 120 success + 20 failed => 85.71% / 14.29%."""
    from app.reports.service import OrderReport

    report = OrderReport(
        period=ReportPeriod.today(),
        total=150,
        success=120,
        failed=20,
        pending=8,
        conflict=2,
    )
    assert report.completed == 140
    assert round(report.success_rate, 2) == 85.71
    assert round(report.failure_rate, 2) == 14.29


async def test_report_with_no_completed_orders_reports_zero_rates(wired):
    services = wired
    await deliver_order(services, text_payload(SOURCE_CHAT_ID, "pending"))
    report = await services.reports.order_report(ReportPeriod.today())
    assert report.success_rate == 0.0
    assert report.failure_rate == 0.0


async def test_operator_report_groups_by_completing_user(destinations):
    services = destinations
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    await configure_acknowledgement(OrderStatus.SUCCESS, enabled=False)

    for _ in range(3):
        order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "order"))
        await primary_work_group_message(order_id, WORK_GROUP_CHAT_ID)
        await operator_replies(
            services, order_id, photo_payload(WORK_GROUP_CHAT_ID), OPERATOR_ID
        )

    reports = await services.reports.operator_reports(ReportPeriod.today())
    assert len(reports) == 1
    assert reports[0].user_id == OPERATOR_ID
    assert reports[0].display_name == "Operator One"
    assert reports[0].total == 3
    assert reports[0].success == 3
    assert reports[0].average_completion_seconds is not None


async def test_system_status_counts_configured_entities(destinations):
    status = await destinations.reports.system_status()
    assert status.database_ok is True
    assert status.sources == 1
    assert status.work_groups == 1
    assert status.operators == 1
    assert status.failed_dispatches == 0


async def test_audit_trail_records_the_whole_lifecycle(destinations):
    services = destinations
    await configure_rule(OrderStatus.SUCCESS, signals=(SignalKey.REPLY_PHOTO,))
    await configure_acknowledgement(OrderStatus.SUCCESS, reaction="✅")

    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "order"))
    await primary_work_group_message(order_id, WORK_GROUP_CHAT_ID)
    await operator_replies(services, order_id, photo_payload(WORK_GROUP_CHAT_ID), OPERATOR_ID)

    async with session_scope() as session:
        events = [e.event for e in await AuditRepository(session).for_order(order_id)]

    for expected in (
        AuditEvent.ORDER_CREATED,
        AuditEvent.ORDER_ROUTED,
        AuditEvent.OPERATOR_SIGNAL_RECEIVED,
        AuditEvent.SUCCESS_RULE_MATCHED,
        AuditEvent.STATUS_CHANGED,
        AuditEvent.RESULT_DISPATCH_SUCCEEDED,
        AuditEvent.ACKNOWLEDGEMENT_APPLIED,
    ):
        assert expected.value in events, f"missing audit event {expected}"


async def test_notification_throttle_suppresses_repeats(services):
    async with session_scope() as session:
        audit = AuditRepository(session)
        assert await audit.should_notify("key", 300) is True
        assert await audit.should_notify("key", 300) is False
        assert await audit.should_notify("other", 300) is True


async def test_super_admins_from_env_are_seeded_and_protected(services):
    async with session_scope() as session:
        repo = AdminRepository(session)
        admin = await repo.get_by_user_id(1000)
        assert admin is not None
        assert admin.role == AdminRole.SUPER_ADMIN
        assert admin.from_env is True
        # An env-defined super admin cannot be removed from the panel.
        assert await repo.delete(admin.id) is False


async def test_order_search_finds_by_number_and_prefix(wired):
    services = wired
    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "searchable"))
    async with session_scope() as session:
        repo = OrderRepository(session)
        order = await repo.get(order_id)
        by_number = await repo.search(
            business_day=order.business_date, daily_number=order.daily_number
        )
        by_display = await repo.search(display_number=order.display_number)
    assert [o.id for o in by_number] == [order_id]
    assert [o.id for o in by_display] == [order_id]


async def test_order_prefix_and_format_are_configurable(wired):
    services = wired
    async with session_scope() as session:
        settings_repo = SettingRepository(session)
        await settings_repo.set(SettingKey.ORDER_PREFIX, "ORD")
        await settings_repo.set(SettingKey.ORDER_NUMBER_FORMAT, "{prefix}-{number}")

    order_id = await deliver_order(services, text_payload(SOURCE_CHAT_ID, "custom"))
    async with session_scope() as session:
        order = await OrderRepository(session).get(order_id)
    assert order.display_number == "ORD-1"
    assert services.gateway.messages_in(WORK_GROUP_CHAT_ID)[0].text.startswith("ORD-1\n\n")


async def test_number_format_falls_back_on_a_broken_template():
    assert render_display_number(5, "order", "{bogus}") == "order5"
    assert render_display_number(5, "order", "{prefix}{number}") == "order5"


async def test_operator_scope_limits_authorisation_to_assigned_groups(wired):
    from app.database.repositories import WorkGroupRepository

    other_chat = -1002000000123
    async with session_scope() as session:
        groups = WorkGroupRepository(session)
        assigned = await groups.get_by_chat_id(WORK_GROUP_CHAT_ID)
        await groups.add(other_chat, "Other WG")
        repo = OperatorRepository(session)
        operator = await repo.get_by_user_id(OPERATOR_ID)
        operator.all_work_groups = False
        await session.flush()
        await repo.assign_work_group(operator.id, assigned.id)

    async with session_scope() as session:
        repo = OperatorRepository(session)
        assert await repo.is_authorized_in_chat(OPERATOR_ID, WORK_GROUP_CHAT_ID) is True
        assert await repo.is_authorized_in_chat(OPERATOR_ID, other_chat) is False


async def test_disabled_operator_loses_authorisation(wired):
    async with session_scope() as session:
        repo = OperatorRepository(session)
        operator = await repo.get_by_user_id(OPERATOR_ID)
        await repo.set_enabled(operator.id, False)
    async with session_scope() as session:
        assert (
            await OperatorRepository(session).is_authorized_in_chat(
                OPERATOR_ID, WORK_GROUP_CHAT_ID
            )
            is False
        )


async def test_duplicate_update_ledger_claims_once(services):
    async with session_scope() as session:
        repo = OrderRepository(session)
        assert await repo.mark_update_processed("update:1") is True
    async with session_scope() as session:
        assert await OrderRepository(session).mark_update_processed("update:1") is False


async def test_report_can_be_filtered_by_work_group(services):
    """Spec §61: the report structure supports source / work group /
    operator / status filters."""
    from app.database.repositories import (
        RouteRepository,
        SourceChannelRepository,
        WorkGroupRepository,
    )
    from tests.conftest import WORK_GROUP_CHAT_ID

    other_group_chat = -1002000000200
    async with session_scope() as session:
        source = await SourceChannelRepository(session).add(SOURCE_CHAT_ID, "Src")
        group_a = await WorkGroupRepository(session).add(WORK_GROUP_CHAT_ID, "WG A")
        group_b = await WorkGroupRepository(session).add(other_group_chat, "WG B")
        await RouteRepository(session).add(source.id, group_a.id)
        group_a_id, group_b_id = group_a.id, group_b.id

    await deliver_order(services, text_payload(SOURCE_CHAT_ID, "routed to A only"))

    in_a = await services.reports.order_report(
        ReportPeriod.today(), work_group_id=group_a_id
    )
    in_b = await services.reports.order_report(
        ReportPeriod.today(), work_group_id=group_b_id
    )
    assert in_a.total == 1
    assert in_b.total == 0


async def test_report_can_be_filtered_by_status(destinations):
    services = destinations
    await _finalise(services, OrderStatus.SUCCESS)
    await _finalise(services, OrderStatus.FAILED)
    await deliver_order(services, text_payload(SOURCE_CHAT_ID, "still pending"))

    only_success = await services.reports.order_report(
        ReportPeriod.today(), statuses=[OrderStatus.SUCCESS]
    )
    assert only_success.total == 1
    assert only_success.success == 1
    assert only_success.failed == 0
    assert only_success.pending == 0


async def test_report_can_be_filtered_by_source(destinations):
    from app.database.repositories import (
        RouteRepository,
        SourceChannelRepository,
        WorkGroupRepository,
    )
    from tests.conftest import WORK_GROUP_CHAT_ID

    services = destinations
    second_source = -1001000000300
    async with session_scope() as session:
        other = await SourceChannelRepository(session).add(second_source, "Src B")
        group = await WorkGroupRepository(session).get_by_chat_id(WORK_GROUP_CHAT_ID)
        await RouteRepository(session).add(other.id, group.id)
        first = await SourceChannelRepository(session).get_by_chat_id(SOURCE_CHAT_ID)
        first_id, other_id = first.id, other.id

    await deliver_order(services, text_payload(SOURCE_CHAT_ID, "from A"))
    await deliver_order(services, text_payload(second_source, "from B"))
    await deliver_order(services, text_payload(second_source, "from B again"))

    from_a = await services.reports.order_report(
        ReportPeriod.today(), source_channel_id=first_id
    )
    from_b = await services.reports.order_report(
        ReportPeriod.today(), source_channel_id=other_id
    )
    assert (from_a.total, from_b.total) == (1, 2)
