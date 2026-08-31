"""Timezone helpers.

The business day boundary is defined by the configured timezone
(``Asia/Tehran`` by default). A fixed UTC offset is never used, so DST-style
changes and future tzdata updates are handled by ``zoneinfo``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.config import get_settings


def app_timezone() -> ZoneInfo:
    return get_settings().tz


def utcnow() -> datetime:
    """Timezone-aware "now" in UTC. All timestamps are stored in UTC."""
    return datetime.now(tz=timezone.utc)


def local_now() -> datetime:
    """"Now" rendered in the configured business timezone."""
    return datetime.now(tz=app_timezone())


def to_local(value: datetime) -> datetime:
    """Convert a stored timestamp into the business timezone."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(app_timezone())


def business_date(moment: datetime | None = None) -> date:
    """The business date an event belongs to.

    Derived purely from wall-clock time in the business timezone, so the
    daily counter rolls over at local midnight even if the bot was offline
    at exactly 00:00 and no cron job ever runs.
    """
    if moment is None:
        return local_now().date()
    return to_local(moment).date()


def day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """Half-open ``[start, end)`` UTC range covering one local business day."""
    tz = app_timezone()
    start_local = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    end_local = _add_one_day(start_local)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def range_bounds_utc(first_day: date, last_day: date) -> tuple[datetime, datetime]:
    """Half-open UTC range covering the inclusive local day range."""
    start, _ = day_bounds_utc(first_day)
    _, end = day_bounds_utc(last_day)
    return start, end


def _add_one_day(moment: datetime) -> datetime:
    from datetime import timedelta

    tz = moment.tzinfo
    naive_next = (moment.replace(tzinfo=None) + timedelta(days=1))
    return naive_next.replace(tzinfo=tz)


def format_local(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    if value is None:
        return "-"
    return to_local(value).strftime(fmt)


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)
