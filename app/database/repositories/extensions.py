"""Repositories for the source-reaction, attachment, result and store features."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.database.models import (
    OrderAttachment,
    ProgressReaction,
    RejectedMessage,
    ResultConfig,
    SourceReactionConfig,
    WooCommerceCall,
)
from app.database.repositories.base import BaseRepository
from app.utils.enums import (
    AttachmentSource,
    DispatchStatus,
    OrderStatus,
    SourceReactionStage,
)
from app.utils.time import utcnow


class SourceReactionRepository(BaseRepository):
    """Reactions the bot places on the original source message."""

    async def get_config(self, stage: SourceReactionStage) -> SourceReactionConfig:
        result = await self.session.execute(
            select(SourceReactionConfig).where(SourceReactionConfig.stage == stage)
        )
        config = result.scalar_one_or_none()
        if config is not None:
            return config
        await self.session.execute(
            insert(SourceReactionConfig)
            .values(stage=stage, enabled=False, reaction_value=None)
            .on_conflict_do_nothing(index_elements=[SourceReactionConfig.stage])
        )
        await self.session.flush()
        return await self.get_config(stage)

    async def update_config(self, stage: SourceReactionStage, **fields) -> SourceReactionConfig:
        config = await self.get_config(stage)
        for key, value in fields.items():
            setattr(config, key, value)
        await self.session.flush()
        return config

    async def all_configs(self) -> list[SourceReactionConfig]:
        return [await self.get_config(stage) for stage in SourceReactionStage]

    # --- reactions that mark an order as "in progress" ------------------
    async def add_progress_reaction(self, emoji: str) -> ProgressReaction:
        await self.session.execute(
            insert(ProgressReaction)
            .values(emoji=emoji, enabled=True)
            .on_conflict_do_update(
                index_elements=[ProgressReaction.emoji], set_={"enabled": True}
            )
        )
        await self.session.flush()
        result = await self.session.execute(
            select(ProgressReaction).where(ProgressReaction.emoji == emoji)
        )
        return result.scalar_one()

    async def list_progress_reactions(self) -> list[ProgressReaction]:
        result = await self.session.execute(
            select(ProgressReaction).order_by(ProgressReaction.id)
        )
        return list(result.scalars())

    async def enabled_progress_emojis(self) -> set[str]:
        return {r.emoji for r in await self.list_progress_reactions() if r.enabled}

    async def toggle_progress_reaction(self, reaction_id: int) -> ProgressReaction | None:
        row = await self.session.get(ProgressReaction, reaction_id)
        if row is not None:
            row.enabled = not row.enabled
        return row

    async def delete_progress_reaction(self, reaction_id: int) -> bool:
        row = await self.session.get(ProgressReaction, reaction_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True


class AttachmentRepository(BaseRepository):
    """Operator media, stored as Telegram file ids only."""

    async def add(
        self,
        *,
        order_id: int,
        content_type: str,
        file_id: str,
        chat_id: int,
        message_id: int,
        caption: str | None = None,
        source: AttachmentSource = AttachmentSource.OPERATOR,
    ) -> bool:
        """Record one attachment. ``False`` when it was already recorded."""
        position = len(await self.list_for_order(order_id))
        stmt = (
            insert(OrderAttachment)
            .values(
                order_id=order_id,
                source=source,
                content_type=content_type,
                file_id=file_id,
                caption=caption,
                chat_id=chat_id,
                message_id=message_id,
                position=position,
            )
            .on_conflict_do_nothing(
                index_elements=[OrderAttachment.chat_id, OrderAttachment.message_id]
            )
            .returning(OrderAttachment.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def list_for_order(self, order_id: int) -> list[OrderAttachment]:
        result = await self.session.execute(
            select(OrderAttachment)
            .where(OrderAttachment.order_id == order_id)
            .order_by(OrderAttachment.position, OrderAttachment.id)
        )
        return list(result.scalars())


class ResultConfigRepository(BaseRepository):
    """Per-status result behaviour: appended text and WooCommerce update."""

    async def get(self, status: OrderStatus) -> ResultConfig:
        result = await self.session.execute(
            select(ResultConfig).where(ResultConfig.status == status)
        )
        config = result.scalar_one_or_none()
        if config is not None:
            return config
        await self.session.execute(
            insert(ResultConfig)
            .values(status=status, append_text_enabled=False, woo_enabled=False)
            .on_conflict_do_nothing(index_elements=[ResultConfig.status])
        )
        await self.session.flush()
        return await self.get(status)

    async def update(self, status: OrderStatus, **fields) -> ResultConfig:
        config = await self.get(status)
        for key, value in fields.items():
            setattr(config, key, value)
        await self.session.flush()
        return config


class WooCommerceRepository(BaseRepository):
    """Outbox for the store update, so it runs exactly once per order."""

    async def ensure_call(
        self,
        *,
        order_id: int,
        order_status: OrderStatus,
        store_order_number: str,
        target_status: str | None,
    ) -> WooCommerceCall:
        await self.session.execute(
            insert(WooCommerceCall)
            .values(
                order_id=order_id,
                order_status=order_status,
                store_order_number=store_order_number,
                target_status=target_status,
                status=DispatchStatus.PENDING,
            )
            .on_conflict_do_nothing(index_elements=[WooCommerceCall.order_id])
        )
        await self.session.flush()
        return await self.get_call(order_id)  # type: ignore[return-value]

    async def get_call(self, order_id: int) -> WooCommerceCall | None:
        result = await self.session.execute(
            select(WooCommerceCall).where(WooCommerceCall.order_id == order_id)
        )
        return result.scalar_one_or_none()

    async def claim(self, order_id: int) -> WooCommerceCall | None:
        """PENDING/FAILED -> SENDING, so only one worker calls the store."""
        result = await self.session.execute(
            update(WooCommerceCall)
            .where(
                WooCommerceCall.order_id == order_id,
                WooCommerceCall.status.in_([DispatchStatus.PENDING, DispatchStatus.FAILED]),
            )
            .values(status=DispatchStatus.SENDING, attempts=WooCommerceCall.attempts + 1)
            .returning(WooCommerceCall.id)
        )
        if result.scalar_one_or_none() is None:
            return None
        return await self.get_call(order_id)

    async def mark_sent(self, order_id: int) -> None:
        await self.session.execute(
            update(WooCommerceCall)
            .where(WooCommerceCall.order_id == order_id)
            .values(status=DispatchStatus.SENT, sent_at=utcnow(), error=None)
        )

    async def mark_failed(self, order_id: int, error: str) -> None:
        await self.session.execute(
            update(WooCommerceCall)
            .where(WooCommerceCall.order_id == order_id)
            .values(status=DispatchStatus.FAILED, error=error[:1000])
        )

    async def release_stale(self, older_than: datetime) -> int:
        result = await self.session.execute(
            update(WooCommerceCall)
            .where(
                WooCommerceCall.status == DispatchStatus.SENDING,
                WooCommerceCall.updated_at < older_than,
            )
            .values(status=DispatchStatus.PENDING)
        )
        return int(result.rowcount or 0)


class RejectedMessageRepository(BaseRepository):
    """Source posts refused for a missing or malformed order number."""

    async def record(
        self,
        *,
        chat_id: int,
        message_id: int,
        reason: str,
        content: str | None,
        author_user_id: int | None,
        author_name: str | None,
        deleted: bool,
    ) -> bool:
        stmt = (
            insert(RejectedMessage)
            .values(
                created_at=utcnow(),
                chat_id=chat_id,
                message_id=message_id,
                reason=reason,
                content=content,
                author_user_id=author_user_id,
                author_name=author_name,
                deleted=deleted,
            )
            .on_conflict_do_nothing(
                index_elements=[RejectedMessage.chat_id, RejectedMessage.message_id]
            )
            .returning(RejectedMessage.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def recent(self, limit: int = 20) -> list[RejectedMessage]:
        result = await self.session.execute(
            select(RejectedMessage).order_by(RejectedMessage.id.desc()).limit(limit)
        )
        return list(result.scalars())

    async def was_rejected(self, chat_id: int, message_id: int) -> bool:
        result = await self.session.execute(
            select(RejectedMessage.id).where(
                RejectedMessage.chat_id == chat_id,
                RejectedMessage.message_id == message_id,
            )
        )
        return result.scalar_one_or_none() is not None
