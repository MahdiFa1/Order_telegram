"""Routing: which source channels feed which work groups."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.filters import IsAdmin
from app.bot.handlers.admin.common import render
from app.bot.keyboards.admin import pick_entities, routing_detail, routing_list
from app.bot.keyboards.callbacks import Nav, RouteCB
from app.database.engine import session_scope
from app.database.repositories import (
    AuditRepository,
    RouteRepository,
    SourceChannelRepository,
    WorkGroupRepository,
)
from app.utils.enums import AuditEvent

router = Router(name="admin_routing")


@router.callback_query(Nav.filter(F.section == "routing"), IsAdmin())
async def open_routing(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_list(callback)


async def _show_list(callback: CallbackQuery) -> None:
    async with session_scope() as session:
        routes = await RouteRepository(session).list_all()
    if routes:
        lines = ["🔀 <b>Routing</b>", ""]
        for route in routes:
            source = route.source_channel.title or str(route.source_channel.chat_id)
            target = route.work_group.title or str(route.work_group.chat_id)
            lines.append(f"{'🟢' if route.enabled else '🔴'} {source} → {target}")
        text = "\n".join(lines)
    else:
        text = (
            "🔀 <b>Routing</b>\n\n"
            "No route configured. Orders will not reach any work group until a "
            "source channel is routed to at least one work group.\n\n"
            "One source may feed several work groups."
        )
    await render(callback, text, routing_list(routes))


@router.callback_query(RouteCB.filter(F.action == "add"), IsAdmin())
async def pick_source(callback: CallbackQuery) -> None:
    async with session_scope() as session:
        sources = await SourceChannelRepository(session).list_all()
    if not sources:
        await callback.answer("Add a source channel first.", show_alert=True)
        return
    await render(
        callback, "Choose the <b>source channel</b>:", pick_entities(sources, "pick_source")
    )


@router.callback_query(RouteCB.filter(F.action == "pick_source"), IsAdmin())
async def pick_work_group(callback: CallbackQuery, callback_data: RouteCB) -> None:
    async with session_scope() as session:
        groups = await WorkGroupRepository(session).list_all()
    if not groups:
        await callback.answer("Add a work group first.", show_alert=True)
        return
    await render(
        callback,
        "Choose the <b>work group</b> this source routes to:",
        pick_entities(groups, "create", arg=callback_data.id),
    )


@router.callback_query(RouteCB.filter(F.action == "create"), IsAdmin())
async def create_route(callback: CallbackQuery, callback_data: RouteCB) -> None:
    async with session_scope() as session:
        await RouteRepository(session).add(callback_data.arg, callback_data.id)
        await AuditRepository(session).log(
            AuditEvent.CONFIGURATION_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"Route created: source #{callback_data.arg} → group #{callback_data.id}",
        )
    await _show_list(callback)


@router.callback_query(RouteCB.filter(F.action == "view"), IsAdmin())
async def view_route(callback: CallbackQuery, callback_data: RouteCB) -> None:
    async with session_scope() as session:
        route = await RouteRepository(session).get(callback_data.id)
        if route is None:
            await callback.answer("Not found", show_alert=True)
            return
        source = route.source_channel.title or str(route.source_channel.chat_id)
        target = route.work_group.title or str(route.work_group.chat_id)
        enabled = route.enabled
        detail = (
            f"🔀 <b>Route</b>\n\n"
            f"Source: {source}\n<code>{route.source_channel.chat_id}</code>\n\n"
            f"Work group: {target}\n<code>{route.work_group.chat_id}</code>\n\n"
            f"Status: {'🟢 Enabled' if enabled else '🔴 Disabled'}"
        )
    await render(callback, detail, routing_detail(route))


@router.callback_query(RouteCB.filter(F.action == "toggle"), IsAdmin())
async def toggle_route(callback: CallbackQuery, callback_data: RouteCB) -> None:
    async with session_scope() as session:
        repo = RouteRepository(session)
        route = await repo.get(callback_data.id)
        if route is not None:
            await repo.set_enabled(callback_data.id, not route.enabled)
    await _show_list(callback)


@router.callback_query(RouteCB.filter(F.action == "delete"), IsAdmin())
async def delete_route(callback: CallbackQuery, callback_data: RouteCB) -> None:
    async with session_scope() as session:
        await RouteRepository(session).delete(callback_data.id)
        await AuditRepository(session).log(
            AuditEvent.CONFIGURATION_CHANGED,
            actor_user_id=callback.from_user.id,
            message=f"Route #{callback_data.id} deleted",
        )
    await _show_list(callback)
