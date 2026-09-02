"""Source channels, work groups, routes and result destinations."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.database.models import ResultDestination, Route, SourceChannel, WorkGroup
from app.database.repositories.base import BaseRepository
from app.utils.enums import OrderStatus


class _ChatRepository(BaseRepository):
    model: type[SourceChannel] | type[WorkGroup]

    async def get(self, pk: int):
        return await self.session.get(self.model, pk)

    async def get_by_chat_id(self, chat_id: int, topic_id: int = 0):
        """The entry for this chat, preferring an exact topic match.

        A forum group may be registered once per topic and, separately, as a
        whole (``topic_id`` 0). A message from a topic belongs to the topic's
        entry when there is one, and otherwise to the chat-wide entry -- so
        registering the group alone keeps working for every topic in it.
        """
        result = await self.session.execute(
            select(self.model)
            .where(self.model.chat_id == chat_id)
            .order_by(self.model.topic_id.desc())
        )
        rows = list(result.scalars())
        for row in rows:
            if row.topic_id == topic_id:
                return row
        for row in rows:
            if row.topic_id == 0:
                return row
        return None

    async def get_exact(self, chat_id: int, topic_id: int = 0):
        """Only the row for exactly this chat and topic, with no fallback."""
        result = await self.session.execute(
            select(self.model).where(
                self.model.chat_id == chat_id, self.model.topic_id == topic_id
            )
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list:
        result = await self.session.execute(select(self.model).order_by(self.model.id))
        return list(result.scalars())

    async def list_enabled(self) -> list:
        result = await self.session.execute(
            select(self.model).where(self.model.enabled.is_(True)).order_by(self.model.id)
        )
        return list(result.scalars())

    async def count_enabled(self) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(self.model).where(self.model.enabled.is_(True))
        )
        return int(result.scalar_one())

    async def add(
        self,
        chat_id: int,
        title: str | None = None,
        username: str | None = None,
        topic_id: int = 0,
    ):
        existing = await self.get_exact(chat_id, topic_id)
        if existing is not None:
            existing.title = title or existing.title
            existing.username = username or existing.username
            existing.enabled = True
            return existing
        entity = self.model(
            chat_id=chat_id,
            topic_id=topic_id,
            title=title,
            username=username,
            enabled=True,
        )
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def set_topic(self, pk: int, topic_id: int):
        entity = await self.session.get(self.model, pk)
        if entity is not None:
            entity.topic_id = max(0, int(topic_id))
        return entity

    async def set_enabled(self, pk: int, enabled: bool):
        entity = await self.session.get(self.model, pk)
        if entity is not None:
            entity.enabled = enabled
        return entity

    async def update_meta(
        self, pk: int, title: str | None = None, username: str | None = None
    ):
        entity = await self.session.get(self.model, pk)
        if entity is None:
            return None
        if title is not None:
            entity.title = title
        if username is not None:
            entity.username = username or None
        return entity

    async def delete(self, pk: int) -> bool:
        entity = await self.session.get(self.model, pk)
        if entity is None:
            return False
        await self.session.delete(entity)
        await self.session.flush()
        return True


class SourceChannelRepository(_ChatRepository):
    model = SourceChannel

    async def get_enabled_by_chat_id(
        self, chat_id: int, topic_id: int = 0
    ) -> SourceChannel | None:
        """The enabled source for this chat, preferring an exact topic match.

        A group registered per topic only accepts posts from those topics; a
        group registered as a whole accepts every topic in it.
        """
        result = await self.session.execute(
            select(SourceChannel).where(
                SourceChannel.chat_id == chat_id, SourceChannel.enabled.is_(True)
            )
        )
        rows = list(result.scalars())
        for row in rows:
            if row.topic_id == topic_id:
                return row
        for row in rows:
            if row.topic_id == 0:
                return row
        return None


class WorkGroupRepository(_ChatRepository):
    model = WorkGroup


class RouteRepository(BaseRepository):
    async def get(self, route_id: int) -> Route | None:
        return await self.session.get(Route, route_id)

    async def list_all(self) -> list[Route]:
        result = await self.session.execute(
            select(Route)
            .options(selectinload(Route.source_channel), selectinload(Route.work_group))
            .order_by(Route.id)
        )
        return list(result.scalars())

    async def list_for_source(self, source_channel_id: int) -> list[Route]:
        result = await self.session.execute(
            select(Route)
            .options(selectinload(Route.work_group))
            .where(Route.source_channel_id == source_channel_id)
            .order_by(Route.id)
        )
        return list(result.scalars())

    async def target_work_groups(self, source_channel_id: int) -> list[WorkGroup]:
        """Enabled work groups an enabled route points at, for one source."""
        result = await self.session.execute(
            select(WorkGroup)
            .join(Route, Route.work_group_id == WorkGroup.id)
            .where(
                Route.source_channel_id == source_channel_id,
                Route.enabled.is_(True),
                WorkGroup.enabled.is_(True),
            )
            .order_by(WorkGroup.id)
        )
        return list(result.scalars())

    async def add(self, source_channel_id: int, work_group_id: int) -> Route:
        result = await self.session.execute(
            select(Route).where(
                Route.source_channel_id == source_channel_id,
                Route.work_group_id == work_group_id,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.enabled = True
            return existing
        route = Route(
            source_channel_id=source_channel_id, work_group_id=work_group_id, enabled=True
        )
        self.session.add(route)
        await self.session.flush()
        return route

    async def set_enabled(self, route_id: int, enabled: bool) -> Route | None:
        route = await self.session.get(Route, route_id)
        if route is not None:
            route.enabled = enabled
        return route

    async def delete(self, route_id: int) -> bool:
        route = await self.session.get(Route, route_id)
        if route is None:
            return False
        await self.session.delete(route)
        await self.session.flush()
        return True


class ResultDestinationRepository(BaseRepository):
    async def get(self, destination_id: int) -> ResultDestination | None:
        return await self.session.get(ResultDestination, destination_id)

    async def list_all(self) -> list[ResultDestination]:
        result = await self.session.execute(
            select(ResultDestination).order_by(
                ResultDestination.status, ResultDestination.position, ResultDestination.id
            )
        )
        return list(result.scalars())

    async def list_for_status(
        self, status: OrderStatus, only_enabled: bool = False
    ) -> list[ResultDestination]:
        stmt = select(ResultDestination).where(ResultDestination.status == status)
        if only_enabled:
            stmt = stmt.where(ResultDestination.enabled.is_(True))
        stmt = stmt.order_by(ResultDestination.position, ResultDestination.id)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def list_for_source(
        self,
        status: OrderStatus,
        source_channel_id: int | None,
        only_enabled: bool = True,
    ) -> list[ResultDestination]:
        """Where results of an order from this source must go.

        A source with its own destinations uses only those; a source without
        any falls back to the shared ones. Mixing the two would send every
        result twice, which is never what an admin means by "this source goes
        somewhere else".
        """
        rows = await self.list_for_status(status, only_enabled=only_enabled)
        if source_channel_id is not None:
            dedicated = [r for r in rows if r.source_channel_id == source_channel_id]
            if dedicated:
                return dedicated
        return [r for r in rows if r.source_channel_id is None]

    async def add(
        self,
        status: OrderStatus,
        chat_id: int,
        title: str | None = None,
        username: str | None = None,
        required: bool = True,
        topic_id: int = 0,
        source_channel_id: int | None = None,
    ) -> ResultDestination:
        result = await self.session.execute(
            select(ResultDestination).where(
                ResultDestination.status == status,
                ResultDestination.chat_id == chat_id,
                ResultDestination.topic_id == topic_id,
                ResultDestination.source_channel_id.is_(None)
                if source_channel_id is None
                else ResultDestination.source_channel_id == source_channel_id,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.enabled = True
            existing.title = title or existing.title
            existing.username = username or existing.username
            return existing

        siblings = await self.list_for_status(status)
        destination = ResultDestination(
            status=status,
            chat_id=chat_id,
            topic_id=topic_id,
            source_channel_id=source_channel_id,
            title=title,
            username=username,
            enabled=True,
            required=required,
            # The first destination configured for a status becomes primary,
            # which matters for the PRIMARY_DESTINATION acknowledgement policy.
            is_primary=not siblings,
            position=len(siblings),
        )
        self.session.add(destination)
        await self.session.flush()
        return destination

    async def set_enabled(self, destination_id: int, enabled: bool) -> ResultDestination | None:
        destination = await self.session.get(ResultDestination, destination_id)
        if destination is not None:
            destination.enabled = enabled
        return destination

    async def update_meta(
        self, destination_id: int, title: str | None = None, username: str | None = None
    ) -> ResultDestination | None:
        destination = await self.session.get(ResultDestination, destination_id)
        if destination is None:
            return None
        if title is not None:
            destination.title = title
        if username is not None:
            destination.username = username or None
        return destination

    async def set_topic(self, destination_id: int, topic_id: int) -> ResultDestination | None:
        destination = await self.get(destination_id)
        if destination is not None:
            destination.topic_id = max(0, int(topic_id))
        return destination

    async def set_source(
        self, destination_id: int, source_channel_id: int | None
    ) -> ResultDestination | None:
        """Bind a destination to one source, or back to every source."""
        destination = await self.get(destination_id)
        if destination is not None:
            destination.source_channel_id = source_channel_id
        return destination

    async def set_required(self, destination_id: int, required: bool) -> ResultDestination | None:
        destination = await self.session.get(ResultDestination, destination_id)
        if destination is not None:
            destination.required = required
        return destination

    async def set_primary(self, destination_id: int) -> ResultDestination | None:
        destination = await self.session.get(ResultDestination, destination_id)
        if destination is None:
            return None
        siblings = await self.list_for_status(OrderStatus(destination.status))
        for sibling in siblings:
            sibling.is_primary = sibling.id == destination_id
        return destination

    async def delete(self, destination_id: int) -> bool:
        destination = await self.session.get(ResultDestination, destination_id)
        if destination is None:
            return False
        was_primary = destination.is_primary
        status = OrderStatus(destination.status)
        await self.session.delete(destination)
        await self.session.flush()
        if was_primary:
            remaining = await self.list_for_status(status)
            if remaining:
                remaining[0].is_primary = True
        return True
