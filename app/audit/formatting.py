"""Rendering of audit entries.

Lives outside the handler so the Telegram layer stays presentation-only and
the same formatting can later feed a web panel or a CSV export.
"""

from __future__ import annotations

from app.admin import strings as t
from app.database.models import AuditLog
from app.utils.time import format_local

#: Telegram rejects messages longer than 4096 characters.
MESSAGE_LIMIT = 4000

_LEVEL_ICONS = {"INFO": "•", "WARNING": "⚠️", "ERROR": "❌"}


def format_entry(entry: AuditLog, *, include_order: bool = True) -> str:
    icon = _LEVEL_ICONS.get(entry.level, "•")
    order_ref = (
        t.AUDIT_ORDER_REF.format(order_id=t.fa_digits(entry.order_id))
        if include_order and entry.order_id
        else ""
    )
    body = entry.message or ""
    stamp = t.fa_digits(format_local(entry.created_at, "%m-%d %H:%M:%S"))
    return (
        f"<code>{stamp}</code> "
        f"{icon} <b>{t.audit_event_label(entry.event)}</b>{order_ref}\n  {body}"
    )


def format_page(entries: list[AuditLog], header: str, *, include_order: bool = True) -> str:
    lines = [header, ""]
    if not entries:
        lines.append(t.AUDIT_EMPTY)
    else:
        lines.extend(format_entry(entry, include_order=include_order) for entry in entries)
    return truncate("\n".join(lines))


def truncate(text: str, limit: int = MESSAGE_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
