"""Append-only audit trail."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

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
        """Spam protection for admin notifications.

        A single atomic upsert claims the slot: the row is only updated when
        the cooldown has elapsed, so concurrent workers (and repeated calls
        inside one session) cannot both decide to send.
        """
        now = utcnow()
        cutoff = now - timedelta(seconds=cooldown_seconds)
        stmt = (
            insert(NotificationThrottle)
            .values(notification_key=key, last_sent_at=now)
            .on_conflict_do_update(
                index_elements=[NotificationThrottle.notification_key],
                set_={"last_sent_at": now},
                where=NotificationThrottle.last_sent_at < cutoff,
            )
            .returning(NotificationThrottle.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None


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
