"""Telegram error classification.

Retrying a *permanent* error (a bot kicked from a channel, a deleted
message, a reaction the chat forbids) only burns rate limit, so failures are
split into retryable and permanent before any backoff is applied.
"""

from __future__ import annotations

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)

RETRYABLE_EXCEPTIONS = (TelegramNetworkError, TelegramServerError, TelegramRetryAfter)
PERMANENT_EXCEPTIONS = (
    TelegramForbiddenError,
    TelegramUnauthorizedError,
)

#: Substrings of ``TelegramBadRequest`` descriptions that are never worth a retry.
_PERMANENT_BAD_REQUEST_MARKERS = (
    "chat not found",
    "message to react not found",
    "message not found",
    "message to copy not found",
    "message can't be copied",
    "message_id_invalid",
    "reaction_invalid",
    "reactions_too_many",
    "not enough rights",
    "have no rights",
    "user_banned_in_channel",
    "chat_write_forbidden",
    "reaction is not allowed",
    "reaction_empty",
    "peer_id_invalid",
    "bot was blocked",
    "bot is not a member",
    "topic_closed",
)


def is_retryable(error: BaseException) -> bool:
    if isinstance(error, RETRYABLE_EXCEPTIONS):
        return True
    if isinstance(error, PERMANENT_EXCEPTIONS):
        return False
    if isinstance(error, TelegramBadRequest):
        description = str(error).lower()
        return not any(marker in description for marker in _PERMANENT_BAD_REQUEST_MARKERS)
    return False


def describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


class ReactionNotAllowed(Exception):
    """The configured acknowledgement reaction is rejected by the chat."""


def is_reaction_rejected(error: BaseException) -> bool:
    if not isinstance(error, TelegramBadRequest):
        return False
    description = str(error).lower()
    return any(
        marker in description
        for marker in (
            "reaction_invalid",
            "reaction is not allowed",
            "reactions_too_many",
            "reaction_empty",
        )
    )
