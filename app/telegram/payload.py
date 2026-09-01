"""Extraction of a reusable payload from an incoming Telegram message.

Everything needed to *rebuild* the message later (file id, text, caption and
entities) is captured at receive time and persisted, so the order can be
re-sent into work groups without a forward header and without depending on
the source message still existing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aiogram.types import Message

from app.utils.enums import ContentType


@dataclass(slots=True)
class MessagePayload:
    chat_id: int
    message_id: int
    content_type: ContentType
    file_id: str | None = None
    text: str | None = None
    caption: str | None = None
    entities: list[dict[str, Any]] | None = None
    caption_entities: list[dict[str, Any]] | None = None
    media_group_id: str | None = None
    has_spoiler: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def as_columns(self, position: int = 0) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "media_group_id": self.media_group_id,
            "content_type": self.content_type.value,
            "file_id": self.file_id,
            "text": self.text,
            "caption": self.caption,
            "entities": self.entities,
            "caption_entities": self.caption_entities,
            "has_spoiler": self.has_spoiler,
            "position": position,
        }


def _dump_entities(entities: list | None) -> list[dict[str, Any]] | None:
    if not entities:
        return None
    dumped: list[dict[str, Any]] = []
    for entity in entities:
        data = entity.model_dump(exclude_none=True, mode="json")
        # ``user`` is a full User object; only the id is needed to rebuild
        # a text_mention entity.
        if isinstance(data.get("user"), dict):
            data["user"] = {"id": data["user"].get("id"), "is_bot": False, "first_name": ""}
        dumped.append(data)
    return dumped


def _author(message: Message) -> dict[str, Any]:
    """Who posted, for addressing a rejection reply.

    A channel post has no ``from_user``; the channel's own title is the only
    name available there.
    """
    user = message.from_user
    if user is not None:
        return {
            "author_user_id": user.id,
            "author_name": user.full_name or user.username or str(user.id),
        }
    author_signature = getattr(message, "author_signature", None)
    return {
        "author_user_id": None,
        "author_name": author_signature or message.chat.title or None,
    }


def extract_payload(message: Message) -> MessagePayload:
    """Map an aiogram ``Message`` onto the columns we persist."""
    common = {
        "extra": _author(message),
        "chat_id": message.chat.id,
        "message_id": message.message_id,
        "media_group_id": message.media_group_id,
        "caption": message.caption,
        "caption_entities": _dump_entities(message.caption_entities),
    }

    if message.photo:
        # Telegram sends every rendered size; the last one is the largest.
        largest = message.photo[-1]
        return MessagePayload(
            content_type=ContentType.PHOTO,
            file_id=largest.file_id,
            has_spoiler=bool(getattr(message, "has_media_spoiler", False)),
            **common,
        )
    if message.video:
        return MessagePayload(
            content_type=ContentType.VIDEO,
            file_id=message.video.file_id,
            has_spoiler=bool(getattr(message, "has_media_spoiler", False)),
            **common,
        )
    if message.animation:
        return MessagePayload(
            content_type=ContentType.ANIMATION,
            file_id=message.animation.file_id,
            has_spoiler=bool(getattr(message, "has_media_spoiler", False)),
            **common,
        )
    if message.document:
        return MessagePayload(
            content_type=ContentType.DOCUMENT, file_id=message.document.file_id, **common
        )
    if message.audio:
        return MessagePayload(
            content_type=ContentType.AUDIO, file_id=message.audio.file_id, **common
        )
    if message.voice:
        return MessagePayload(
            content_type=ContentType.VOICE, file_id=message.voice.file_id, **common
        )
    if message.video_note:
        return MessagePayload(
            content_type=ContentType.VIDEO_NOTE, file_id=message.video_note.file_id, **common
        )
    if message.sticker:
        return MessagePayload(
            content_type=ContentType.STICKER, file_id=message.sticker.file_id, **common
        )
    if message.text is not None:
        return MessagePayload(
            content_type=ContentType.TEXT,
            text=message.text,
            entities=_dump_entities(message.entities),
            **common,
        )
    return MessagePayload(content_type=ContentType.OTHER, **common)


def describe_content(payload: MessagePayload) -> str:
    if payload.content_type is ContentType.TEXT:
        preview = (payload.text or "").strip().replace("\n", " ")
        return preview[:60] or "text"
    return payload.content_type.value.lower()
