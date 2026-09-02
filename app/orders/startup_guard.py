"""Whether a source post that arrived while the bot was down still counts.

Telegram queues updates for an offline bot and delivers them all on
reconnect. Without a guard, a restart replays every post made during the
downtime into the work group -- which is what happened after a redeploy and
sent a morning's worth of already-handled posts through the pipeline again.

The bot's start time is the reference point: anything Telegram delivers that
was *posted* before it is backlog.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import SettingRepository
from app.telegram.payload import MessagePayload
from app.utils.enums import StartupBacklogMode

#: Set once, when polling starts. Naive callers get "no guard" rather than a
#: crash, which keeps the ingest path working in tests and one-off scripts.
_started_at: datetime | None = None


def mark_started(when: datetime | None = None) -> datetime:
    global _started_at
    _started_at = when or datetime.now(timezone.utc)
    return _started_at


def started_at() -> datetime | None:
    return _started_at


def reset() -> None:
    """Only for tests: forget the recorded start time."""
    global _started_at
    _started_at = None


@dataclass(frozen=True, slots=True)
class BacklogDecision:
    accepted: bool
    reason: str = ""
    age_minutes: int = 0

    @property
    def skipped(self) -> bool:
        return not self.accepted


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


async def evaluate(
    session: AsyncSession, payload: MessagePayload, now: datetime | None = None
) -> BacklogDecision:
    """Decide whether this post is fresh enough to become an order."""
    settings = SettingRepository(session)
    mode = await settings.startup_backlog_mode()
    if mode is StartupBacklogMode.ALL:
        return BacklogDecision(True, "backlog guard off")

    sent_at = payload.sent_at
    if sent_at is None:
        # Nothing to judge by; never drop a message on a missing timestamp.
        return BacklogDecision(True, "message carries no timestamp")
    sent_at = _as_utc(sent_at)
    now = _as_utc(now or datetime.now(timezone.utc))
    age = max(0, int((now - sent_at).total_seconds() // 60))

    if mode is StartupBacklogMode.IGNORE_DOWNTIME:
        start = started_at()
        if start is None:
            return BacklogDecision(True, "start time unknown")
        if sent_at < _as_utc(start):
            return BacklogDecision(False, "posted before the bot started", age)
        return BacklogDecision(True, "posted after the bot started", age)

    limit = await settings.startup_backlog_max_age()
    if _as_utc(sent_at) < now - timedelta(minutes=limit):
        return BacklogDecision(False, f"older than {limit} minutes", age)
    return BacklogDecision(True, f"within {limit} minutes", age)
