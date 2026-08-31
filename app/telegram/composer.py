"""Rebuilds a stored order into outgoing Telegram send operations.

Requirement: orders must reach work groups and result destinations WITHOUT a
``Forwarded from ...`` header. ``forwardMessage`` is therefore never used.
Instead the message is rebuilt from the stored file ids / text, which also
lets the daily order number be prepended while keeping the original
formatting entities aligned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.database.models import OrderSourceMessage
from app.utils.enums import ContentType

TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024

#: Content types that ``sendMediaGroup`` accepts.
ALBUM_TYPES = {ContentType.PHOTO, ContentType.VIDEO, ContentType.DOCUMENT, ContentType.AUDIO}

#: Content types that carry a caption we can prefix in a single message.
CAPTIONABLE = {
    ContentType.PHOTO,
    ContentType.VIDEO,
    ContentType.DOCUMENT,
    ContentType.AUDIO,
    ContentType.VOICE,
    ContentType.ANIMATION,
}


def utf16_length(value: str) -> int:
    """Telegram entity offsets are counted in UTF-16 code units."""
    return len(value.encode("utf-16-le")) // 2


def shift_entities(
    entities: Sequence[dict[str, Any]] | None, offset: int
) -> list[dict[str, Any]] | None:
    if not entities:
        return None
    shifted: list[dict[str, Any]] = []
    for entity in entities:
        clone = dict(entity)
        clone["offset"] = int(clone.get("offset", 0)) + offset
        shifted.append(clone)
    return shifted


@dataclass(slots=True)
class SendOperation:
    """One Telegram API call needed to reproduce the order."""

    kind: str  # text | media | album | copy
    payload: dict[str, Any]


@dataclass(slots=True)
class ComposedOrder:
    operations: list[SendOperation]

    @property
    def is_empty(self) -> bool:
        return not self.operations


def build_header(display_number: str) -> str:
    return f"{display_number}\n\n"


def compose(
    display_number: str,
    source_messages: Sequence[OrderSourceMessage],
    *,
    source_chat_id: int | None = None,
) -> ComposedOrder:
    """Turn the stored source messages into send operations.

    Strategy, in order of preference:

    1. **Album** (more than one media message) -> one ``sendMediaGroup`` with
       the order header prepended to the first caption. Rebuilt from file
       ids, which is the only way to both keep the album grouped and change
       its caption.
    2. **Single captionable media** -> ``sendPhoto`` / ``sendVideo`` / ... with
       the header prepended to the caption.
    3. **Text** -> ``sendMessage`` with the header prepended and entity
       offsets shifted.
    4. **Anything else** (sticker, video note, unsupported type) -> a header
       message followed by ``copyMessage``, which still carries no forward
       header.
    """
    messages = [m for m in source_messages if m is not None]
    if not messages:
        return ComposedOrder(operations=[])

    header = build_header(display_number)
    header_offset = utf16_length(header)

    album = [m for m in messages if ContentType(m.content_type) in ALBUM_TYPES]
    if len(messages) > 1 and len(album) == len(messages):
        return ComposedOrder(operations=[_compose_album(album, header, header_offset)])

    primary = messages[0]
    content_type = ContentType(primary.content_type)

    if content_type is ContentType.TEXT:
        return ComposedOrder(operations=_compose_text(primary, header, header_offset))

    if content_type in CAPTIONABLE and primary.file_id:
        return ComposedOrder(
            operations=_compose_single_media(primary, content_type, header, header_offset)
        )

    return ComposedOrder(
        operations=_compose_fallback(primary, header, source_chat_id or primary.chat_id)
    )


def _compose_album(
    album: Sequence[OrderSourceMessage], header: str, header_offset: int
) -> SendOperation:
    items: list[dict[str, Any]] = []
    caption_used = False
    for message in album:
        item: dict[str, Any] = {
            "type": ContentType(message.content_type).value.lower(),
            "media": message.file_id,
        }
        if ContentType(message.content_type) in {ContentType.PHOTO, ContentType.VIDEO}:
            item["has_spoiler"] = message.has_spoiler
        if not caption_used:
            caption = f"{header}{message.caption or ''}"
            if len(caption) <= CAPTION_LIMIT:
                item["caption"] = caption
                item["caption_entities"] = shift_entities(
                    message.caption_entities, header_offset
                )
            else:
                item["caption"] = caption[:CAPTION_LIMIT]
            caption_used = True
        elif message.caption:
            item["caption"] = message.caption[:CAPTION_LIMIT]
            item["caption_entities"] = message.caption_entities
        items.append(item)
    return SendOperation(kind="album", payload={"media": items})


def _compose_text(
    message: OrderSourceMessage, header: str, header_offset: int
) -> list[SendOperation]:
    body = message.text or ""
    combined = f"{header}{body}"
    if len(combined) <= TEXT_LIMIT:
        return [
            SendOperation(
                kind="text",
                payload={
                    "text": combined,
                    "entities": shift_entities(message.entities, header_offset),
                },
            )
        ]
    # Oversized body: send the header on its own, then the untouched text so
    # no formatting is lost to truncation.
    operations = [SendOperation(kind="text", payload={"text": header.strip()})]
    operations.append(
        SendOperation(
            kind="text", payload={"text": body[:TEXT_LIMIT], "entities": message.entities}
        )
    )
    return operations


def _compose_single_media(
    message: OrderSourceMessage,
    content_type: ContentType,
    header: str,
    header_offset: int,
) -> list[SendOperation]:
    caption = f"{header}{message.caption or ''}"
    payload: dict[str, Any] = {
        "content_type": content_type.value,
        "file_id": message.file_id,
    }
    if len(caption) <= CAPTION_LIMIT:
        payload["caption"] = caption
        payload["caption_entities"] = shift_entities(message.caption_entities, header_offset)
        operations = [SendOperation(kind="media", payload=payload)]
    else:
        payload["caption"] = message.caption
        payload["caption_entities"] = message.caption_entities
        operations = [
            SendOperation(kind="text", payload={"text": header.strip()}),
            SendOperation(kind="media", payload=payload),
        ]
    if content_type in {ContentType.PHOTO, ContentType.VIDEO, ContentType.ANIMATION}:
        payload["has_spoiler"] = message.has_spoiler
    return operations


def _compose_fallback(
    message: OrderSourceMessage, header: str, from_chat_id: int
) -> list[SendOperation]:
    """Header + ``copyMessage`` for types we cannot rebuild from a file id.

    ``copyMessage`` is used rather than ``forwardMessage`` precisely because
    it produces no "Forwarded from" header.
    """
    return [
        SendOperation(kind="text", payload={"text": header.strip()}),
        SendOperation(
            kind="copy",
            payload={"from_chat_id": from_chat_id, "message_id": message.message_id},
        ),
    ]
