"""Filters that scope handlers to configured chats."""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import Message

from app.database.engine import session_scope
from app.database.repositories import SourceChannelRepository, WorkGroupRepository
from app.telegram.payload import topic_of


class IsSourceChannel(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        async with session_scope() as session:
            channel = await SourceChannelRepository(session).get_enabled_by_chat_id(
                message.chat.id, topic_of(message)
            )
        return channel is not None


class IsWorkGroup(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        async with session_scope() as session:
            group = await WorkGroupRepository(session).get_by_chat_id(
                message.chat.id, topic_of(message)
            )
        return group is not None and group.enabled
