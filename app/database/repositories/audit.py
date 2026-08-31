"""Append-only audit trail."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.database.models import AuditLog, NotificationThrottle
from app.database.repositories.base import BaseRepository
from app.utils.enums import AuditEvent
from app.utils.time import utcnow


class AuditRepository(BaseRepository):
    async def log(
        self,
        event: AuditEvent | str,
        *,
        order_id: int | None = None,
        actor_user_id: int | None = None,
        chat_id: int | None = None,
        message_id: int | None = None,
        message: str | None = None,
        level: str = "INFO",
        data: dict[str, Any] | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            created_at=utcnow(),
            event=str(event),
            level=level,
            order_id=order_id,
            actor_user_id=actor_user_id,
            chat_id=chat_id,
            message_id=message_id,
            message=message,
            data=_jsonable(data) if data else None,
        )
        self.session.add(entry)
        return entry

    async def recent(self, limit: int = 20, offset: int = 0) -> list[AuditLog]:
        result = await self.session.execute(
            select(AuditLog).order_by(AuditLog.id.desc()).offset(offset).limit(limit)
        )
        return list(result.scalars())

    async def for_order(self, order_id: int, limit: int = 50) -> list[AuditLog]:
        result = await self.session.execute(
            select(AuditLog)
            .where(AuditLog.order_id == order_id)
            .order_by(AuditLog.id)
            .limit(limit)
        )
        return list(result.scalars())

    async def should_notify(self, key: str, cooldown_seconds: int) -> bool:
        """Spam protection for admin notifications."""
        now = utcnow()
        result = await self.session.execute(
            select(NotificationThrottle).where(NotificationThrottle.notification_key == key)
        )
        row = result.scalar_one_or_none()
        if row is None:
            self.session.add(NotificationThrottle(notification_key=key, last_sent_at=now))
            return True
        if (now - row.last_sent_at).total_seconds() < cooldown_seconds:
            return False
        row.last_sent_at = now
        return True


def _jsonable(value: Any) -> Any:
    """Best-effort conversion so JSONB never rejects a payload."""
    import datetime as _dt
    import uuid as _uuid

    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, _uuid.UUID):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
