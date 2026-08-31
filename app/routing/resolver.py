"""Route resolution: which work groups an order must reach.

Kept out of both the handler and the repository so the rule "an order goes to
every enabled work group reachable through an enabled route from its enabled
source" lives in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import WorkGroup
from app.database.repositories import RouteRepository, SourceChannelRepository


@dataclass(frozen=True, slots=True)
class RoutingPlan:
    """Where one order should be delivered, and why it might go nowhere."""

    work_groups: tuple[WorkGroup, ...]
    reason: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.work_groups


async def resolve(session: AsyncSession, source_channel_id: int | None) -> RoutingPlan:
    if source_channel_id is None:
        return RoutingPlan((), "order has no source channel")

    channel = await SourceChannelRepository(session).get(source_channel_id)
    if channel is None:
        return RoutingPlan((), "source channel no longer exists")
    if not channel.enabled:
        return RoutingPlan((), "source channel is disabled")

    groups = await RouteRepository(session).target_work_groups(source_channel_id)
    if not groups:
        return RoutingPlan(
            (),
            "no enabled route points from this source to an enabled work group",
        )
    return RoutingPlan(tuple(groups))


async def describe_routing(session: AsyncSession) -> list[str]:
    """Human-readable routing summary used by the admin panel and diagnostics."""
    lines: list[str] = []
    for channel in await SourceChannelRepository(session).list_all():
        plan = await resolve(session, channel.id)
        source = channel.title or str(channel.chat_id)
        if plan.is_empty:
            lines.append(f"⚠️ {source} → nowhere ({plan.reason})")
        else:
            targets = ", ".join(g.title or str(g.chat_id) for g in plan.work_groups)
            lines.append(f"{source} → {targets}")
    return lines
