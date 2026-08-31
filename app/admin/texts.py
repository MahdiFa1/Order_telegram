"""Rendering of admin panel screens.

The wording itself lives in :mod:`app.admin.strings`; this module only fills
those templates from domain objects.
"""

from __future__ import annotations

from app.admin import strings as t
from app.reports.service import OperatorReport, OrderReport, SystemStatus
from app.utils.enums import OrderStatus, RuleMode, SignalKey
from app.utils.time import format_duration, format_local

MAIN_TEXT = t.MAIN_TEXT


def _fa(value: object) -> str:
    return t.fa_digits(value)


def _rate(value: float) -> str:
    return t.fa_digits(f"{value:.2f}")


def _duration(seconds: float | None) -> str:
    return t.fa_digits(format_duration(seconds)) if seconds is not None else t.DASH


def dashboard(report: OrderReport, status: SystemStatus, bot_online: bool) -> str:
    return t.DASHBOARD.format(
        total=_fa(report.total),
        success=_fa(report.success),
        failed=_fa(report.failed),
        pending=_fa(report.pending),
        conflict=_fa(report.conflict),
        success_rate=_rate(report.success_rate),
        failure_rate=_rate(report.failure_rate),
        sources=_fa(status.sources),
        work_groups=_fa(status.work_groups),
        operators=_fa(status.operators),
        bot=t.ONLINE if bot_online else t.OFFLINE,
        database=t.CONNECTED if status.database_ok else t.DB_ERROR,
    )


def system_status(status: SystemStatus, uptime_seconds: float, bot_online: bool) -> str:
    return t.SYSTEM_STATUS.format(
        bot=t.ONLINE if bot_online else t.OFFLINE,
        database=t.CONNECTED if status.database_ok else t.DB_ERROR,
        sources=_fa(status.sources),
        work_groups=_fa(status.work_groups),
        operators=_fa(status.operators),
        pending=_fa(status.pending_orders),
        conflict=_fa(status.conflict_orders),
        failed_dispatches=_fa(status.failed_dispatches),
        failed_acks=_fa(status.failed_acknowledgements),
        uptime=_duration(uptime_seconds),
    )


def order_report(report: OrderReport) -> str:
    return t.ORDER_REPORT.format(
        period=report.period.label,
        first=_fa(report.period.first_day),
        last=_fa(report.period.last_day),
        total=_fa(report.total),
        success=_fa(report.success),
        failed=_fa(report.failed),
        pending=_fa(report.pending),
        conflict=_fa(report.conflict),
        completed=_fa(report.completed),
        success_rate=_rate(report.success_rate),
        failure_rate=_rate(report.failure_rate),
        average=_duration(report.average_completion_seconds),
    )


def operator_report(reports: list[OperatorReport], period_label: str) -> str:
    title = t.OPERATOR_REPORT_TITLE.format(period=period_label)
    if not reports:
        return f"{title}\n\n{t.OPERATOR_REPORT_EMPTY}"
    rows = [
        t.OPERATOR_REPORT_ROW.format(
            name=entry.display_name,
            total=_fa(entry.total),
            success=_fa(entry.success),
            failed=_fa(entry.failed),
            average=_duration(entry.average_completion_seconds),
        )
        for entry in reports
    ]
    return title + "\n\n" + "\n\n".join(rows)


def rules_screen(status: OrderStatus, rule, enabled_keys: set[str], patterns, reactions) -> str:
    mode = RuleMode(rule.mode)
    signal_lines = "\n".join(
        f"  {'🟢' if key.value in enabled_keys else '🔴'} {t.SIGNAL_LABELS_FA[key]}"
        for key in SignalKey
    )

    warning = ""
    if not enabled_keys:
        warning = t.WARN_NO_SIGNAL
    elif SignalKey.REPLY_TEXT.value in enabled_keys and not [p for p in patterns if p.enabled]:
        warning = t.WARN_TEXT_NO_PATTERN
    elif SignalKey.REACTION.value in enabled_keys and not [r for r in reactions if r.enabled]:
        warning = t.WARN_REACTION_NO_EMOJI

    body = t.RULES_SCREEN.format(
        icon="✅" if status is OrderStatus.SUCCESS else "❌",
        status=t.status_name(status),
        detection=t.toggle_text(rule.enabled),
        mode=t.MODE_NAMES.get(mode.value, mode.value),
        mode_help=t.MODE_HELP_ANY if mode is RuleMode.ANY else t.MODE_HELP_ALL,
        signals=signal_lines,
        patterns=_fa(len(patterns)),
        reactions=" ".join(r.emoji for r in reactions) or t.DASH,
    )
    return body + warning


def acknowledgement_screen(status: OrderStatus, config, warnings: list[str]) -> str:
    body = t.ACK_SCREEN.format(
        icon="✅" if status is OrderStatus.SUCCESS else "❌",
        status=t.status_name(status),
        enabled=t.toggle_text(config.enabled),
        reaction=config.reaction_value or t.ACK_NOT_SET,
        target=t.TARGET_MODE_NAMES.get(config.target_mode, config.target_mode),
        policy=t.DISPATCH_POLICY_NAMES.get(config.dispatch_policy, config.dispatch_policy),
        retry=t.RETRY_ON if config.retry_enabled else t.RETRY_OFF,
        max_retry=_fa(config.max_retry_count),
    )
    if warnings:
        body += "\n\n" + "\n".join(f"⚠️ {w}" for w in warnings)
    return body


def order_detail(order, source_title: str | None, signals, dispatches) -> str:
    signal_lines = (
        "\n".join(
            f"  • {t.status_name(s.rule_status)}: "
            f"{t.SIGNAL_LABELS_FA.get(SignalKey(s.signal_key), s.signal_key)}"
            for s in signals
        )
        or f"  {t.DASH}"
    )
    dispatch_lines = (
        "\n".join(
            t.DISPATCH_ROW.format(chat=_fa(d.chat_id), status=d.status)
            + (f" ({d.error[:60]})" if d.error else "")
            for d in dispatches
        )
        or f"  {t.DASH}"
    )
    delivery_lines = (
        "\n".join(
            t.DELIVERY_ROW.format(
                chat=_fa(d.chat_id),
                status=d.status,
                messages=", ".join(_fa(m.message_id) for m in d.messages)
                or t.DELIVERY_NO_MESSAGE,
            )
            for d in order.deliveries
        )
        or f"  {t.DASH}"
    )

    return t.ORDER_DETAIL.format(
        display=order.display_number,
        uuid=order.uuid,
        business_date=_fa(order.business_date),
        daily_number=_fa(order.daily_number),
        scope=order.counter_scope_key,
        source=source_title or _fa(order.source_chat_id),
        source_message=_fa(order.source_message_id),
        album=order.source_media_group_id or t.DASH,
        status=t.status_name(order.status),
        created=_fa(format_local(order.created_at)),
        completed=_fa(format_local(order.completed_at)),
        completed_by=_fa(order.completed_by_user_id) if order.completed_by_user_id else t.DASH,
        trigger=order.completion_trigger_type or t.DASH,
        trigger_chat=_fa(order.completion_trigger_chat_id)
        if order.completion_trigger_chat_id
        else t.DASH,
        trigger_message=_fa(order.completion_trigger_message_id)
        if order.completion_trigger_message_id
        else t.DASH,
        reason=order.success_reason or order.failure_reason or t.DASH,
        dispatch_state=order.result_dispatch_status,
        dispatches=dispatch_lines,
        ack_status=order.acknowledgement_status,
        ack_reaction=order.acknowledgement_reaction or t.DASH,
        ack_chat=_fa(order.acknowledgement_chat_id) if order.acknowledgement_chat_id else t.DASH,
        ack_message=_fa(order.acknowledgement_message_id)
        if order.acknowledgement_message_id
        else t.DASH,
        ack_applied=_fa(format_local(order.acknowledgement_applied_at)),
        ack_attempts=_fa(order.acknowledgement_attempts),
        ack_error=order.acknowledgement_error or t.DASH,
        deliveries=delivery_lines,
        signals=signal_lines,
    )
