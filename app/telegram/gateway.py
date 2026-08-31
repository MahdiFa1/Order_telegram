"""Thin, typed wrapper around the Telegram Bot API calls we actually make.

Everything that talks to Telegram goes through here, which keeps the
services testable (the test suite substitutes a fake gateway) and keeps
retry/backoff policy in one place.
"""

from __future__ import annotations

from typing import Any, Sequence

from aiogram import Bot
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
}


def _entities(raw: Sequence[dict[str, Any]] | None) -> list[MessageEntity] | None:
    if not raw:
        return None
    return [MessageEntity.model_validate(item) for item in raw]


class TelegramGateway:
    def __init__(self, bot: Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings

    # ------------------------------------------------------------------
    # Sending orders
    # ------------------------------------------------------------------
    async def send_composed(self, chat_id: int, composed: ComposedOrder) -> list[int]:
        """Execute every operation of a composed order, returning message ids."""
        message_ids: list[int] = []
        for operation in composed.operations:
            message_ids.extend(await self._execute(chat_id, operation))
        return message_ids

    async def _execute(self, chat_id: int, operation: SendOperation) -> list[int]:
        async def call() -> list[int]:
            if operation.kind == "text":
                message = await self.bot.send_message(
                    chat_id=chat_id,
                    text=operation.payload["text"],
                    entities=_entities(operation.payload.get("entities")),
                    disable_web_page_preview=True,
                )
                return [message.message_id]

            if operation.kind == "media":
                return [await self._send_media(chat_id, operation.payload)]

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
                messages = await self.bot.send_media_group(chat_id=chat_id, media=media)
                return [m.message_id for m in messages]

            if operation.kind == "copy":
                copied = await self.bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=operation.payload["from_chat_id"],
                    message_id=operation.payload["message_id"],
                )
                return [copied.message_id]

            raise ValueError(f"Unknown send operation: {operation.kind}")

        return await with_retry(
            call,
            max_attempts=self.settings.telegram_max_retries,
            base_delay=self.settings.telegram_retry_base_delay,
            operation_name=f"send_{operation.kind}",
        )

    async def _send_media(self, chat_id: int, payload: dict[str, Any]) -> int:
        content_type = ContentType(payload["content_type"])
        kwargs: dict[str, Any] = {
            "chat_id": chat_id,
            "caption": payload.get("caption"),
            "caption_entities": _entities(payload.get("caption_entities")),
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

    async def send_text(self, chat_id: int, text: str) -> int:
        async def call() -> int:
            message = await self.bot.send_message(
                chat_id=chat_id, text=text, disable_web_page_preview=True
            )
            return message.message_id

        return await with_retry(
            call,
            max_attempts=self.settings.telegram_max_retries,
            base_delay=self.settings.telegram_retry_base_delay,
            operation_name="send_text",
        )

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
