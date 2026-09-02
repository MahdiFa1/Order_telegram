"""Thin, typed wrapper around the Telegram Bot API calls we actually make.

Everything that talks to Telegram goes through here, which keeps the
services testable (the test suite substitutes a fake gateway) and keeps
retry/backoff policy in one place.
"""

from __future__ import annotations

from typing import Any, Sequence

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    MessageEntity,
    ReactionTypeCustomEmoji,
    ReactionTypeEmoji,
)

from app.config import Settings
from app.telegram.composer import ComposedOrder, SendOperation
from app.telegram.retry import with_retry
from app.utils.enums import ContentType, ReactionType
from app.utils.logging import get_logger

logger = get_logger(__name__)

_MEDIA_CLASSES = {
    "photo": InputMediaPhoto,
    "video": InputMediaVideo,
    "document": InputMediaDocument,
    "audio": InputMediaAudio,
    # Attachments are stored with the ContentType spelling.
    ContentType.PHOTO.value: InputMediaPhoto,
    ContentType.VIDEO.value: InputMediaVideo,
    ContentType.DOCUMENT.value: InputMediaDocument,
    ContentType.AUDIO.value: InputMediaAudio,
}


def _entities(raw: Sequence[dict[str, Any]] | None) -> list[MessageEntity] | None:
    if not raw:
        return None
    return [MessageEntity.model_validate(item) for item in raw]


class TelegramGateway:
    def __init__(self, bot: Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings

    @staticmethod
    def _thread(topic_id: int | None) -> dict[str, Any]:
        """Kwargs that place a message inside a forum topic.

        0 and None both mean "the chat itself"; Telegram rejects
        ``message_thread_id=0``, so the key is omitted entirely.
        """
        return {"message_thread_id": topic_id} if topic_id else {}

    # ------------------------------------------------------------------
    # Sending orders
    # ------------------------------------------------------------------
    async def send_composed(
        self, chat_id: int, composed: ComposedOrder, topic_id: int | None = None
    ) -> list[int]:
        """Execute every operation of a composed order, returning message ids."""
        message_ids: list[int] = []
        for operation in composed.operations:
            message_ids.extend(await self._execute(chat_id, operation, topic_id))
        return message_ids

    async def _execute(
        self, chat_id: int, operation: SendOperation, topic_id: int | None = None
    ) -> list[int]:
        thread = self._thread(topic_id)

        async def call() -> list[int]:
            if operation.kind == "text":
                message = await self.bot.send_message(
                    chat_id=chat_id,
                    text=operation.payload["text"],
                    entities=_entities(operation.payload.get("entities")),
                    disable_web_page_preview=True,
                    **thread,
                )
                return [message.message_id]

            if operation.kind == "media":
                return [await self._send_media(chat_id, operation.payload, topic_id)]

            if operation.kind == "album":
                media = []
                for item in operation.payload["media"]:
                    cls = _MEDIA_CLASSES.get(item["type"])
                    if cls is None:
                        continue
                    kwargs: dict[str, Any] = {"media": item["media"]}
                    if item.get("caption"):
                        kwargs["caption"] = item["caption"]
                        kwargs["caption_entities"] = _entities(item.get("caption_entities"))
                    if "has_spoiler" in item and item["type"] in {"photo", "video"}:
                        kwargs["has_spoiler"] = bool(item["has_spoiler"])
                    media.append(cls(**kwargs))
                messages = await self.bot.send_media_group(
                    chat_id=chat_id, media=media, **thread
                )
                return [m.message_id for m in messages]

            if operation.kind == "copy":
                copied = await self.bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=operation.payload["from_chat_id"],
                    message_id=operation.payload["message_id"],
                    **thread,
                )
                return [copied.message_id]

            raise ValueError(f"Unknown send operation: {operation.kind}")

        return await with_retry(
            call,
            max_attempts=self.settings.telegram_max_retries,
            base_delay=self.settings.telegram_retry_base_delay,
            operation_name=f"send_{operation.kind}",
        )

    async def _send_media(
        self, chat_id: int, payload: dict[str, Any], topic_id: int | None = None
    ) -> int:
        content_type = ContentType(payload["content_type"])
        kwargs: dict[str, Any] = {
            "chat_id": chat_id,
            "caption": payload.get("caption"),
            "caption_entities": _entities(payload.get("caption_entities")),
            **self._thread(topic_id),
        }
        file_id = payload["file_id"]
        spoiler = payload.get("has_spoiler")

        message: Message
        if content_type is ContentType.PHOTO:
            message = await self.bot.send_photo(photo=file_id, has_spoiler=spoiler, **kwargs)
        elif content_type is ContentType.VIDEO:
            message = await self.bot.send_video(video=file_id, has_spoiler=spoiler, **kwargs)
        elif content_type is ContentType.ANIMATION:
            message = await self.bot.send_animation(
                animation=file_id, has_spoiler=spoiler, **kwargs
            )
        elif content_type is ContentType.DOCUMENT:
            message = await self.bot.send_document(document=file_id, **kwargs)
        elif content_type is ContentType.AUDIO:
            message = await self.bot.send_audio(audio=file_id, **kwargs)
        elif content_type is ContentType.VOICE:
            message = await self.bot.send_voice(voice=file_id, **kwargs)
        else:  # pragma: no cover - guarded by the composer
            raise ValueError(f"Unsupported media content type: {content_type}")
        return message.message_id

    async def send_text(self, chat_id: int, text: str, topic_id: int | None = None) -> int:
        async def call() -> int:
            message = await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                disable_web_page_preview=True,
                **self._thread(topic_id),
            )
            return message.message_id

        return await with_retry(
            call,
            max_attempts=self.settings.telegram_max_retries,
            base_delay=self.settings.telegram_retry_base_delay,
            operation_name="send_text",
        )

    async def send_reply(
        self,
        chat_id: int,
        reply_to_message_id: int,
        text: str,
        topic_id: int | None = None,
    ) -> int:
        """Reply to a specific message; falls back to a plain send if the
        message is already gone (a rejected post we just deleted)."""

        async def call() -> int:
            try:
                message = await self.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_to_message_id=reply_to_message_id,
                    disable_web_page_preview=True,
                    **self._thread(topic_id),
                )
            except TelegramBadRequest as error:
                if "not found" not in str(error).lower():
                    raise
                # The message is gone, so its thread has to be named
                # explicitly or the reply lands in the group's main view.
                message = await self.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    disable_web_page_preview=True,
                    **self._thread(topic_id),
                )
            return message.message_id

        return await with_retry(
            call,
            max_attempts=self.settings.telegram_max_retries,
            base_delay=self.settings.telegram_retry_base_delay,
            operation_name="send_reply",
        )

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        """Delete a message. ``False`` when Telegram refuses (too old, no
        rights); the caller carries on rather than failing the whole intake."""
        try:
            await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as error:  # noqa: BLE001 - reported by the caller
            logger.warning(
                "delete_message_failed",
                chat_id=chat_id,
                message_id=message_id,
                error=str(error),
            )
            return False
        return True

    async def send_attachments(
        self,
        chat_id: int,
        attachments,
        caption: str | None = None,
        topic_id: int | None = None,
    ) -> list[int]:
        """Re-send operator media from stored file ids.

        Photos and videos go as one album when there is more than one, which
        is how they were sent in the work group.
        """
        if not attachments:
            return []

        groupable = [a for a in attachments if a.content_type in _MEDIA_CLASSES]
        if len(groupable) == len(attachments) and len(attachments) > 1:
            media = []
            for index, attachment in enumerate(attachments):
                cls = _MEDIA_CLASSES[attachment.content_type]
                kwargs: dict[str, Any] = {"media": attachment.file_id}
                if index == 0 and caption:
                    kwargs["caption"] = caption
                media.append(cls(**kwargs))

            async def send_group() -> list[int]:
                sent = await self.bot.send_media_group(
                    chat_id=chat_id, media=media, **self._thread(topic_id)
                )
                return [m.message_id for m in sent]

            return await with_retry(
                send_group,
                max_attempts=self.settings.telegram_max_retries,
                base_delay=self.settings.telegram_retry_base_delay,
                operation_name="send_attachment_group",
            )

        message_ids: list[int] = []
        for index, attachment in enumerate(attachments):
            payload = {
                "content_type": attachment.content_type,
                "file_id": attachment.file_id,
                "caption": caption if index == 0 else attachment.caption,
                "caption_entities": None,
            }
            message_ids.append(
                await with_retry(
                    lambda p=payload: self._send_media(chat_id, p, topic_id),
                    max_attempts=self.settings.telegram_max_retries,
                    base_delay=self.settings.telegram_retry_base_delay,
                    operation_name="send_attachment",
                )
            )
        return message_ids

    # ------------------------------------------------------------------
    # Reactions
    # ------------------------------------------------------------------
    async def set_reaction(
        self,
        chat_id: int,
        message_id: int,
        reaction: str,
        reaction_type: ReactionType = ReactionType.EMOJI,
        *,
        retry: bool = True,
    ) -> None:
        """Apply a single reaction via ``setMessageReaction``.

        Telegram bots may set at most one reaction per message, so exactly one
        emoji is sent. Passing an empty list would clear reactions instead.
        """
        if reaction_type is ReactionType.CUSTOM_EMOJI:
            payload = [ReactionTypeCustomEmoji(custom_emoji_id=reaction)]
        else:
            payload = [ReactionTypeEmoji(emoji=reaction)]

        async def call() -> None:
            await self.bot.set_message_reaction(
                chat_id=chat_id, message_id=message_id, reaction=payload, is_big=False
            )

        if not retry:
            await call()
            return
        await with_retry(
            call,
            max_attempts=self.settings.telegram_max_retries,
            base_delay=self.settings.telegram_retry_base_delay,
            operation_name="set_message_reaction",
        )

    # ------------------------------------------------------------------
    # Diagnostics used by the admin panel
    # ------------------------------------------------------------------
    async def get_chat_info(self, chat_id: int | str) -> dict[str, Any]:
        chat = await self.bot.get_chat(chat_id)
        available: list[str] | None = None
        # ``available_reactions`` is None when the chat allows all reactions.
        raw_reactions = getattr(chat, "available_reactions", None)
        if raw_reactions is not None:
            available = [
                r.emoji
                for r in raw_reactions
                if getattr(r, "type", None) == "emoji" and getattr(r, "emoji", None)
            ]
        return {
            "id": chat.id,
            "type": chat.type,
            "title": chat.title or chat.full_name if hasattr(chat, "full_name") else chat.title,
            "username": chat.username,
            "available_reactions": available,
        }

    async def check_can_post(self, chat_id: int) -> tuple[bool, str]:
        try:
            me = await self.bot.get_me()
            member = await self.bot.get_chat_member(chat_id=chat_id, user_id=me.id)
        except Exception as error:  # noqa: BLE001 - reported to the admin verbatim
            return False, str(error)
        status = getattr(member, "status", "")
        if status in {"administrator", "creator"}:
            return True, f"bot is {status}"
        if status == "member":
            return True, "bot is a member (admin rights recommended)"
        return False, f"bot status is '{status}'"

    async def get_me_username(self) -> str:
        me = await self.bot.get_me()
        return me.username or str(me.id)
