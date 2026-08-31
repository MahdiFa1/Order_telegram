"""Route resolution and admin account management."""

from __future__ import annotations

import pytest

from app.audit.formatting import format_page, truncate
from app.database.engine import session_scope
from app.database.models import AuditLog
from app.database.repositories import (
    AdminRepository,
    ResultDestinationRepository,
    RouteRepository,
    SourceChannelRepository,
    WorkGroupRepository,
)
from app.routing.resolver import describe_routing, resolve
from app.utils.enums import AdminRole, OrderStatus
from app.utils.time import utcnow
from tests.conftest import SOURCE_CHAT_ID, WORK_GROUP_CHAT_ID

pytestmark = pytest.mark.asyncio


async def test_resolver_returns_the_routed_work_groups(wired):
    async with session_scope() as session:
        source = await SourceChannelRepository(session).get_by_chat_id(SOURCE_CHAT_ID)
        plan = await resolve(session, source.id)
    assert [g.chat_id for g in plan.work_groups] == [WORK_GROUP_CHAT_ID]
    assert plan.is_empty is False


async def test_resolver_explains_a_disabled_source(wired):
    async with session_scope() as session:
        repo = SourceChannelRepository(session)
        source = await repo.get_by_chat_id(SOURCE_CHAT_ID)
        await repo.set_enabled(source.id, False)
        plan = await resolve(session, source.id)
    assert plan.is_empty
    assert "disabled" in plan.reason


async def test_resolver_explains_a_disabled_route(wired):
    async with session_scope() as session:
        source = await SourceChannelRepository(session).get_by_chat_id(SOURCE_CHAT_ID)
        routes = await RouteRepository(session).list_for_source(source.id)
        await RouteRepository(session).set_enabled(routes[0].id, False)
        plan = await resolve(session, source.id)
    assert plan.is_empty
    assert "no enabled route" in plan.reason


async def test_resolver_explains_a_disabled_work_group(wired):
    async with session_scope() as session:
        group = await WorkGroupRepository(session).get_by_chat_id(WORK_GROUP_CHAT_ID)
        await WorkGroupRepository(session).set_enabled(group.id, False)
        source = await SourceChannelRepository(session).get_by_chat_id(SOURCE_CHAT_ID)
        plan = await resolve(session, source.id)
    assert plan.is_empty


async def test_resolver_handles_a_missing_source(services):
    async with session_scope() as session:
        assert (await resolve(session, None)).is_empty
        assert (await resolve(session, 99999)).is_empty


async def test_describe_routing_flags_unrouted_sources(wired):
    async with session_scope() as session:
        await SourceChannelRepository(session).add(-1001000000055, "Orphan Source")
        lines = await describe_routing(session)
    assert any("Orphan Source → nowhere" in line for line in lines)
    assert any("Orders Source → Work Group 1" in line for line in lines)


# ---------------------------------------------------------------------------
# Destination repository (the Edit Title path)
# ---------------------------------------------------------------------------
async def test_destination_title_can_be_edited(wired):
    async with session_scope() as session:
        repo = ResultDestinationRepository(session)
        destination = await repo.add(OrderStatus.SUCCESS, -1003000000010, "Old name")
        updated = await repo.update_meta(destination.id, title="New name")
        assert updated.title == "New name"


async def test_first_destination_becomes_primary_and_promotion_moves_it(wired):
    async with session_scope() as session:
        repo = ResultDestinationRepository(session)
        first = await repo.add(OrderStatus.SUCCESS, -1003000000011, "A")
        second = await repo.add(OrderStatus.SUCCESS, -1003000000012, "B")
        assert first.is_primary is True
        assert second.is_primary is False

        await repo.set_primary(second.id)
        refreshed = await repo.list_for_status(OrderStatus.SUCCESS)
        assert {d.chat_id: d.is_primary for d in refreshed} == {
            -1003000000011: False,
            -1003000000012: True,
        }


async def test_deleting_the_primary_destination_promotes_another(wired):
    async with session_scope() as session:
        repo = ResultDestinationRepository(session)
        first = await repo.add(OrderStatus.SUCCESS, -1003000000013, "A")
        await repo.add(OrderStatus.SUCCESS, -1003000000014, "B")
        await repo.delete(first.id)
        remaining = await repo.list_for_status(OrderStatus.SUCCESS)
    assert len(remaining) == 1
    assert remaining[0].is_primary is True


# ---------------------------------------------------------------------------
# Admin accounts
# ---------------------------------------------------------------------------
async def test_admin_can_be_added_promoted_and_removed(services):
    async with session_scope() as session:
        repo = AdminRepository(session)
        admin = await repo.add(777001, AdminRole.ADMIN)
        assert admin.role == AdminRole.ADMIN
        assert admin.from_env is False

        admin.role = AdminRole.SUPER_ADMIN
        await session.flush()
        assert (await repo.get_by_user_id(777001)).role == AdminRole.SUPER_ADMIN

        assert await repo.delete(admin.id) is True
        assert await repo.get_by_user_id(777001) is None


async def test_env_super_admin_cannot_be_removed(services):
    async with session_scope() as session:
        repo = AdminRepository(session)
        env_admin = await repo.get_by_user_id(1000)
        assert env_admin.from_env is True
        assert await repo.delete(env_admin.id) is False
        assert await repo.get_by_user_id(1000) is not None


async def test_bootstrap_restores_an_env_super_admin_that_was_demoted(services, session_factory):
    from app.config import get_settings
    from app.services.bootstrap import bootstrap

    async with session_scope() as session:
        admin = await AdminRepository(session).get_by_user_id(1000)
        admin.role = AdminRole.ADMIN
        admin.enabled = False

    await bootstrap(session_factory, get_settings())

    async with session_scope() as session:
        admin = await AdminRepository(session).get_by_user_id(1000)
    assert admin.role == AdminRole.SUPER_ADMIN
    assert admin.enabled is True


async def test_admin_pk_lookup(services):
    async with session_scope() as session:
        repo = AdminRepository(session)
        admin = await repo.get_by_user_id(1000)
        assert (await repo.get_by_user_id_pk(admin.id)).telegram_user_id == 1000
        assert await repo.get_by_user_id_pk(999999) is None


# ---------------------------------------------------------------------------
# Audit formatting
# ---------------------------------------------------------------------------
async def test_audit_page_renders_entries_and_empty_state():
    entry = AuditLog(
        created_at=utcnow(),
        event="ORDER_CREATED",
        level="INFO",
        order_id=7,
        message="Order order7 created",
    )
    rendered = format_page([entry], "HEADER")
    assert "HEADER" in rendered
    assert "ORDER_CREATED" in rendered
    assert "order #7" in rendered

    without_order = format_page([entry], "HEADER", include_order=False)
    assert "order #7" not in without_order

    assert "No entries." in format_page([], "HEADER")


async def test_audit_page_stays_within_the_telegram_limit():
    entries = [
        AuditLog(
            created_at=utcnow(),
            event="STATUS_CHANGED",
            level="ERROR",
            order_id=index,
            message="x" * 200,
        )
        for index in range(100)
    ]
    rendered = format_page(entries, "HEADER")
    assert len(rendered) <= 4000
    assert rendered.endswith("…")


async def test_truncate_leaves_short_text_alone():
    assert truncate("short") == "short"
