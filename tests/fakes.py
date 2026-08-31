"""Test doubles for the Telegram API.

The gateway is the single seam through which the application talks to
Telegram, so replacing it is enough to drive every pipeline end to end
without a network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.telegram.composer import ComposedOrder
from app.utils.enums import ReactionType


class FakeTelegramError(Exception):
    """Stands in for a permanent Telegram failure such as Forbidden."""


@dataclass
class SentMessage:
    chat_id: int
    message_id: int
    kind: str
    text: str | None = None
    caption: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class AppliedReaction:
    chat_id: int
    message_id: int
    reaction: str


class FakeGateway:
    """Records everything that would have been sent to Telegram."""

    def __init__(self) -> None:
        self.sent: list[SentMessage] = []
        self.reactions: list[AppliedReaction] = []
        self.texts: list[tuple[int, str]] = []
        self._next_message_id = 1000
        #: chat ids whose sends must fail (simulates Forbidden / no rights)
        self.failing_chats: set[int] = set()
        #: chat ids whose reactions must fail (simulates reaction not allowed)
        self.failing_reaction_chats: set[int] = set()
        self.fail_all_reactions = False
        self.reaction_attempts = 0

    # -- sending ------------------------------------------------------
    def _allocate(self) -> int:
        self._next_message_id += 1
        return self._next_message_id

    async def send_composed(self, chat_id: int, composed: ComposedOrder) -> list[int]:
        if chat_id in self.failing_chats:
            raise FakeTelegramError(f"Forbidden: bot can't send to {chat_id}")
        message_ids: list[int] = []
        for operation in composed.operations:
            if operation.kind == "album":
                for item in operation.payload["media"]:
                    message_id = self._allocate()
                    self.sent.append(
                        SentMessage(
                            chat_id=chat_id,
                            message_id=message_id,
                            kind="album_item",
                            caption=item.get("caption"),
                            payload=item,
                        )
                    )
                    message_ids.append(message_id)
            else:
                message_id = self._allocate()
                self.sent.append(
                    SentMessage(
                        chat_id=chat_id,
                        message_id=message_id,
                        kind=operation.kind,
                        text=operation.payload.get("text"),
                        caption=operation.payload.get("caption"),
                        payload=operation.payload,
                    )
                )
                message_ids.append(message_id)
        return message_ids

    async def send_text(self, chat_id: int, text: str) -> int:
        if chat_id in self.failing_chats:
            raise FakeTelegramError(f"Forbidden: bot can't send to {chat_id}")
        message_id = self._allocate()
        self.texts.append((chat_id, text))
        self.sent.append(
            SentMessage(chat_id=chat_id, message_id=message_id, kind="text", text=text)
        )
        return message_id

    # -- reactions ----------------------------------------------------
    async def set_reaction(
        self,
        chat_id: int,
        message_id: int,
        reaction: str,
        reaction_type: ReactionType = ReactionType.EMOJI,
        *,
        retry: bool = True,
    ) -> None:
        self.reaction_attempts += 1
        if self.fail_all_reactions or chat_id in self.failing_reaction_chats:
            raise FakeTelegramError("Bad Request: REACTION_INVALID")
        self.reactions.append(
            AppliedReaction(chat_id=chat_id, message_id=message_id, reaction=reaction)
        )

    # -- diagnostics --------------------------------------------------
    async def get_chat_info(self, chat_id: int | str) -> dict[str, Any]:
        return {
            "id": chat_id,
            "type": "supergroup",
            "title": f"Chat {chat_id}",
            "username": None,
            "available_reactions": None,
        }

    async def check_can_post(self, chat_id: int) -> tuple[bool, str]:
        return chat_id not in self.failing_chats, "fake"

    async def get_me_username(self) -> str:
        return "fake_bot"

    # -- helpers ------------------------------------------------------
    def messages_in(self, chat_id: int) -> list[SentMessage]:
        return [m for m in self.sent if m.chat_id == chat_id]

    def reactions_on(self, chat_id: int, message_id: int) -> list[AppliedReaction]:
        return [
            r for r in self.reactions if r.chat_id == chat_id and r.message_id == message_id
        ]

    def reset(self) -> None:
        self.sent.clear()
        self.reactions.clear()
        self.texts.clear()
        self.reaction_attempts = 0


class RecordingNotifier:
    """Captures admin notifications instead of sending them."""

    def __init__(self) -> None:
        self.events: list[tuple[str, tuple]] = []

    async def dispatch_failed(self, order_id: int, chat_id: int, reason: str) -> None:
        self.events.append(("dispatch_failed", (order_id, chat_id, reason)))

    async def acknowledgement_failed(self, order_id: int, reason: str) -> None:
        self.events.append(("acknowledgement_failed", (order_id, reason)))

    async def conflict_detected(self, order_id: int) -> None:
        self.events.append(("conflict_detected", (order_id,)))

    async def route_failed(self, order_id: int, reason: str) -> None:
        self.events.append(("route_failed", (order_id, reason)))

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.events]
