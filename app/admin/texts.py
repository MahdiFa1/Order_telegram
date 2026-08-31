"""Rendering of admin panel screens."""

from __future__ import annotations

from app.reports.service import OperatorReport, OrderReport, SystemStatus
from app.utils.enums import (
    SIGNAL_LABELS,
    OrderStatus,
    RuleMode,
    SignalKey,
)
from app.utils.time import format_duration, format_local

MAIN_TEXT = (
    "🤖 <b>Order Management Bot</b>\n\n"
    "All configuration lives here — nothing is hard coded.\n"
    "Pick a section:"
)


def dashboard(report: OrderReport, status: SystemStatus, bot_online: bool) -> str:
    return (
        "📊 <b>Dashboard</b>\n\n"
        f"📦 Orders Today: <b>{report.total}</b>\n"
        f"✅ Success: <b>{report.success}</b>\n"
        f"❌ Failed: <b>{report.failed}</b>\n"
        f"⏳ Pending: <b>{report.pending}</b>\n"
        f"⚠️ Conflict: <b>{report.conflict}</b>\n\n"
        f"Success Rate: <b>{report.success_rate:.2f}%</b>\n"
        f"Failure Rate: <b>{report.failure_rate:.2f}%</b>\n\n"
        f"Active Sources: {status.sources}\n"
        f"Active Work Groups: {status.work_groups}\n"
        f"Active Operators: {status.operators}\n"
        f"Bot Status: {'✅ Online' if bot_online else '❌ Offline'}\n"
        f"Database Status: {'✅ Connected' if status.database_ok else '❌ Error'}"
    )


def system_status(status: SystemStatus, uptime_seconds: float, bot_online: bool) -> str:
    return (
        "🩺 <b>System Status</b>\n\n"
        f"Bot: {'✅ Online' if bot_online else '❌ Offline'}\n"
        f"Database: {'✅ Connected' if status.database_ok else '❌ Error'}\n\n"
        f"Sources: {status.sources}\n"
        f"Work Groups: {status.work_groups}\n"
        f"Operators: {status.operators}\n\n"
        f"Pending Orders: {status.pending_orders}\n"
        f"Conflict Orders: {status.conflict_orders}\n"
        f"Failed Dispatches: {status.failed_dispatches}\n"
        f"Failed Acknowledgements: {status.failed_acknowledgements}\n\n"
        f"Uptime: {format_duration(uptime_seconds)}"
    )


def order_report(report: OrderReport) -> str:
    average = (
        format_duration(report.average_completion_seconds)
        if report.average_completion_seconds is not None
        else "-"
    )
    return (
        f"📊 <b>{report.period.label} Report</b>\n"
        f"<i>{report.period.first_day} → {report.period.last_day}</i>\n\n"
        f"Total: <b>{report.total}</b>\n\n"
        f"✅ Success: <b>{report.success}</b>\n"
        f"❌ Failed: <b>{report.failed}</b>\n"
        f"⏳ Pending: <b>{report.pending}</b>\n"
        f"⚠️ Conflict: <b>{report.conflict}</b>\n\n"
        f"Completed (success + failed): <b>{report.completed}</b>\n"
        f"Success Rate: <b>{report.success_rate:.2f}%</b>\n"
        f"Failure Rate: <b>{report.failure_rate:.2f}%</b>\n\n"
        f"Average completion time: {average}"
    )


def operator_report(reports: list[OperatorReport], period_label: str) -> str:
    if not reports:
        return f"👤 <b>Operator Report — {period_label}</b>\n\nNo completed orders in this period."
    lines = [f"👤 <b>Operator Report — {period_label}</b>", ""]
    for entry in reports:
        average = (
            format_duration(entry.average_completion_seconds)
            if entry.average_completion_seconds is not None
            else "-"
        )
        lines.extend(
            [
                f"<b>{entry.display_name}</b>",
                f"  Orders handled: {entry.total}",
                f"  Success: {entry.success}",
                f"  Failed: {entry.failed}",
                f"  Average completion time: {average}",
                "",
            ]
        )
    return "\n".join(lines)


def rules_screen(status: OrderStatus, rule, enabled_keys: set[str], patterns, reactions) -> str:
    icon = "✅" if status is OrderStatus.SUCCESS else "❌"
    mode = RuleMode(rule.mode)
    signal_lines = "\n".join(
        f"  {'🟢' if key.value in enabled_keys else '🔴'} {SIGNAL_LABELS[key]}"
        for key in SignalKey
    )
    warning = ""
    if not enabled_keys:
        warning = (
            "\n⚠️ <b>No signal is enabled — this rule can never match.</b>"
        )
    elif SignalKey.REPLY_TEXT.value in enabled_keys and not [p for p in patterns if p.enabled]:
        warning = "\n⚠️ Reply Text is enabled but no text pattern is configured."
    elif SignalKey.REACTION.value in enabled_keys and not [r for r in reactions if r.enabled]:
        warning = "\n⚠️ Reaction is enabled but no accepted reaction is configured."

    mode_help = (
        "ANY — any single enabled signal finalises the order."
        if mode is RuleMode.ANY
        else "ALL — every enabled signal must be observed before the order finalises."
    )
    return (
        f"{icon} <b>{status.value.title()} Rules</b>\n\n"
        f"Detection: {'🟢 Enabled' if rule.enabled else '🔴 Disabled'}\n"
        f"Mode: <b>{mode.value}</b>\n"
        f"<i>{mode_help}</i>\n\n"
        f"<b>Signals</b>\n{signal_lines}\n\n"
        f"Text patterns: {len(patterns)}\n"
        f"Accepted reactions: {' '.join(r.emoji for r in reactions) or '—'}"
        f"{warning}"
    )


def acknowledgement_screen(
    status: OrderStatus, config, warnings: list[str]
) -> str:
    icon = "✅" if status is OrderStatus.SUCCESS else "❌"
    warning_block = ""
    if warnings:
        warning_block = "\n\n" + "\n".join(f"⚠️ {w}" for w in warnings)
    return (
        f"{icon} <b>{status.value.title()} Acknowledgement</b>\n\n"
        f"Status:\n{'🟢 Enabled' if config.enabled else '🔴 Disabled'}\n\n"
        f"Reaction:\n{config.reaction_value or '— not set —'}\n\n"
        f"Target:\n{config.target_mode}\n\n"
        f"Dispatch Policy:\n{config.dispatch_policy.replace('_', ' ').title()}\n\n"
        f"Retry: {'ON' if config.retry_enabled else 'OFF'} "
        f"(max {config.max_retry_count})\n\n"
        "<i>The reaction is applied only after the order has actually been "
        "sent to its result destination.</i>"
        f"{warning_block}"
    )


def order_detail(order, source_title: str | None, signals, dispatches) -> str:
    signal_lines = (
        "\n".join(f"  • {s.rule_status}: {s.signal_key}" for s in signals) or "  —"
    )
    dispatch_lines = (
        "\n".join(f"  • {d.chat_id}: {d.status}" + (f" ({d.error[:60]})" if d.error else "")
                  for d in dispatches)
        or "  —"
    )
    delivery_lines = (
        "\n".join(
            f"  • chat {d.chat_id}: {d.status} "
            f"[{', '.join(str(m.message_id) for m in d.messages) or 'no message'}]"
            for d in order.deliveries
        )
        or "  —"
    )
    return (
        f"🔎 <b>Order {order.display_number}</b>\n\n"
        f"UUID: <code>{order.uuid}</code>\n"
        f"Business date: {order.business_date}\n"
        f"Daily number: {order.daily_number} (scope {order.counter_scope_key})\n"
        f"Source: {source_title or order.source_chat_id} / msg {order.source_message_id}\n"
        f"Album: {order.source_media_group_id or '—'}\n\n"
        f"Status: <b>{order.status}</b>\n"
        f"Created: {format_local(order.created_at)}\n"
        f"Completed: {format_local(order.completed_at)}\n"
        f"Completed by: {order.completed_by_user_id or '—'}\n"
        f"Trigger: {order.completion_trigger_type or '—'} "
        f"(chat {order.completion_trigger_chat_id or '—'}, "
        f"msg {order.completion_trigger_message_id or '—'})\n"
        f"Reason: {order.success_reason or order.failure_reason or '—'}\n\n"
        f"Result dispatch: <b>{order.result_dispatch_status}</b>\n{dispatch_lines}\n\n"
        f"Acknowledgement: <b>{order.acknowledgement_status}</b>\n"
        f"  Reaction: {order.acknowledgement_reaction or '—'}\n"
        f"  Target: chat {order.acknowledgement_chat_id or '—'} / "
        f"msg {order.acknowledgement_message_id or '—'}\n"
        f"  Applied at: {format_local(order.acknowledgement_applied_at)}\n"
        f"  Attempts: {order.acknowledgement_attempts}\n"
        f"  Error: {order.acknowledgement_error or '—'}\n\n"
        f"<b>Work group deliveries</b>\n{delivery_lines}\n\n"
        f"<b>Signals</b>\n{signal_lines}"
    )
