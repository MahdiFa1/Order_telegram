"""Rendering of audit entries.

Lives outside the handler so the Telegram layer stays presentation-only and
the same formatting can later feed a web panel or a CSV export.
"""

from __future__ import annotations

from app.database.models import AuditLog
from app.utils.time import format_local

#: Telegram rejects messages longer than 4096 characters.
MESSAGE_LIMIT = 4000

_LEVEL_ICONS = {"INFO": "•", "WARNING": "⚠️", "ERROR": "❌"}


def format_entry(entry: AuditLog, *, include_order: bool = True) -> str:
    icon = _LEVEL_ICONS.get(entry.level, "•")
    order_ref = f" · order #{entry.order_id}" if include_order and entry.order_id else ""
    body = entry.message or ""
    return (
        f"<code>{format_local(entry.created_at, '%m-%d %H:%M:%S')}</code> "
        f"{icon} <b>{entry.event}</b>{order_ref}\n  {body}"
    )


def format_page(entries: list[AuditLog], header: str, *, include_order: bool = True) -> str:
    lines = [header, ""]
    if not entries:
        lines.append("No entries.")
    else:
        lines.extend(format_entry(entry, include_order=include_order) for entry in entries)
    return truncate("\n".join(lines))


def truncate(text: str, limit: int = MESSAGE_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
