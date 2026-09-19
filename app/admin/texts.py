"""Rendering of admin panel screens.

The wording itself lives in :mod:`app.admin.strings`; this module only fills
those templates from domain objects.
"""

from __future__ import annotations

from app.admin import strings as t
from app.reports.service import OperatorReport, OrderReport, SystemStatus
from app.utils.enums import (
    AcknowledgementStatus,
    DispatchStatus,
    OrderStatus,
    RuleMode,
    SignalKey,
)
from app.utils.time import format_duration, format_local, utcnow

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
        store_waiting=_fa(status.store_updates_waiting),
        store_abandoned=_fa(status.store_updates_abandoned),
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


def _schedule_ladder(policy) -> str:
    """"۲، ۴، ۸ دقیقه" -- the waits this policy actually produces."""
    steps = [
        int(policy.delay_after(attempt).total_seconds() // 60)
        for attempt in range(1, policy.max_attempts)
    ]
    if not steps:
        return t.DASH
    return "، ".join(_fa(step) for step in steps) + " " + t.MINUTES_SUFFIX


def store_retry_screen(policy) -> str:
    """The automatic-retry settings, with the resulting schedule spelled out."""
    schedule = _schedule_ladder(policy)
    text = t.WOO_RETRY_SCREEN.format(
        enabled=t.toggle_text(policy.enabled),
        max_attempts=_fa(policy.max_attempts),
        base_minutes=_fa(policy.base_minutes),
        max_minutes=_fa(policy.max_minutes),
        schedule=schedule,
        timeout=_fa(policy.request_timeout),
        quick=_fa(policy.quick_retries),
        alert=t.ALERT_MODE_NAMES.get(
            policy.alert_mode.value, policy.alert_mode.value
        ),
    )
    if not policy.enabled:
        text += f"\n\n{t.WOO_RETRY_DISABLED_HINT}"
    return text


def telegram_retry_screen(policy, immediate_attempts: int) -> str:
    """The Telegram retry settings, with the resulting schedule spelled out."""
    text = t.TG_RETRY_SCREEN.format(
        enabled=t.toggle_text(policy.enabled),
        max_attempts=_fa(policy.max_attempts),
        base_minutes=_fa(policy.base_minutes),
        max_minutes=_fa(policy.max_minutes),
        schedule=_schedule_ladder(policy),
        alert=t.ALERT_MODE_NAMES.get(
            policy.alert_mode.value, policy.alert_mode.value
        ),
        immediate=_fa(immediate_attempts),
    )
    if not policy.enabled:
        text += f"\n\n{t.TG_RETRY_DISABLED_HINT}"
    return text


def delivery_schedule(row, now, next_attempt_at, permanent: bool, status: str,
                      max_attempts: int | None = None, attempts: int = 0) -> str:
    """One line saying what happens to this delivery or reaction next."""
    if status in {DispatchStatus.SENDING, AcknowledgementStatus.APPLYING}:
        return t.RETRY_IN_FLIGHT
    spent = max_attempts is not None and attempts >= max_attempts
    if permanent or spent:
        return t.RETRY_STOPPED
    if next_attempt_at is None or next_attempt_at <= now:
        return t.RETRY_SOON
    return t.RETRY_NEXT_AT.format(
        time=_fa(format_local(next_attempt_at, "%H:%M"))
    )


def delivery_queue_screen(
    dispatches,
    acknowledgements,
    display_numbers: dict[int, str],
    waiting: int,
    abandoned: int,
    max_attempts: int,
    now,
) -> str:
    """Unfinished result deliveries, and the reactions still owed."""
    rows = [
        t.TG_QUEUE_ROW.format(
            icon="⏳"
            if not row.permanent and row.attempts < max_attempts
            else "⛔️",
            display=display_numbers.get(row.order_id, t.DASH),
            chat_id=_fa(row.chat_id),
            attempts=_fa(row.attempts),
            schedule=delivery_schedule(
                row,
                now,
                row.next_attempt_at,
                row.permanent,
                row.status,
                max_attempts,
                row.attempts,
            ),
            error=(row.error or t.DASH)[:200],
        )
        for row in dispatches
    ]
    body = "\n\n".join(rows) if rows else t.TG_QUEUE_EMPTY

    if acknowledgements:
        ack_rows = [
            t.TG_QUEUE_ACK_ROW.format(
                icon="⏳" if not order.acknowledgement_permanent else "⛔️",
                display=order.display_number,
                attempts=_fa(order.acknowledgement_attempts),
                schedule=delivery_schedule(
                    order,
                    now,
                    order.acknowledgement_next_attempt_at,
                    order.acknowledgement_permanent,
                    order.acknowledgement_status,
                ),
                error=(order.acknowledgement_error or t.DASH)[:200],
            )
            for order in acknowledgements
        ]
        body += "\n\n" + t.TG_QUEUE_ACK_TITLE + "\n\n" + "\n\n".join(ack_rows)

    return t.TG_QUEUE_SCREEN.format(
        waiting=_fa(waiting), abandoned=_fa(abandoned), rows=body
    )


def store_schedule(
    call, now, fmt: str = "%H:%M", max_attempts: int | None = None
) -> str:
    """One line saying what happens to this store call next."""
    if call.status == DispatchStatus.SENT:
        return t.DASH
    if call.status == DispatchStatus.SENDING:
        return t.RETRY_IN_FLIGHT
    spent = max_attempts is not None and call.attempts >= max_attempts
    if call.permanent or spent:
        return t.RETRY_STOPPED
    if call.next_attempt_at is None or call.next_attempt_at <= now:
        return t.RETRY_SOON
    return t.RETRY_NEXT_AT.format(
        time=_fa(format_local(call.next_attempt_at, fmt))
    )


def store_queue_screen(
    calls, display_numbers: dict[int, str], waiting: int, abandoned: int,
    max_attempts: int, now,
) -> str:
    """The list of store updates that have not gone through yet."""
    rows = [
        t.WOO_QUEUE_ROW.format(
            icon="⏳" if not call.permanent and call.attempts < max_attempts else "⛔️",
            display=display_numbers.get(call.order_id, t.DASH),
            order_number=_fa(call.store_order_number),
            attempts=_fa(call.attempts),
            schedule=store_schedule(call, now, max_attempts=max_attempts),
            error=(call.error or t.DASH)[:200],
        )
        for call in calls
    ]
    return t.WOO_QUEUE_SCREEN.format(
        waiting=_fa(waiting),
        abandoned=_fa(abandoned),
        rows="\n\n".join(rows) if rows else t.WOO_QUEUE_EMPTY,
    )


def store_section(call, max_attempts: int | None = None) -> str:
    """The store update's own state, as it reads on the order screen."""
    if call is None:
        return ""
    return t.ORDER_STORE_SECTION.format(
        order_number=_fa(call.store_order_number),
        status=call.status,
        attempts=_fa(call.attempts),
        schedule=store_schedule(call, utcnow(), "%Y-%m-%d %H:%M", max_attempts),
        error=(call.error or t.DASH)[:200],
    )


def order_detail(
    order,
    source_title: str | None,
    signals,
    dispatches,
    store_call=None,
    store_max_attempts: int | None = None,
    telegram_max_attempts: int | None = None,
) -> str:
    signal_lines = (
        "\n".join(
            f"  • {t.status_name(s.rule_status)}: "
            f"{t.SIGNAL_LABELS_FA.get(SignalKey(s.signal_key), s.signal_key)}"
            for s in signals
        )
        or f"  {t.DASH}"
    )
    now = utcnow()
    dispatch_lines = (
        "\n".join(
            t.DISPATCH_ROW.format(
                chat=_fa(d.chat_id),
                status=d.status,
                schedule=delivery_schedule(
                    d,
                    now,
                    d.next_attempt_at,
                    d.permanent,
                    d.status,
                    telegram_max_attempts,
                    d.attempts,
                ),
            )
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
        ack_schedule=delivery_schedule(
            order,
            now,
            order.acknowledgement_next_attempt_at,
            order.acknowledgement_permanent,
            order.acknowledgement_status,
        )
        if order.acknowledgement_status
        in {
            AcknowledgementStatus.FAILED,
            AcknowledgementStatus.PENDING,
            AcknowledgementStatus.APPLYING,
        }
        else t.DASH,
        ack_error=order.acknowledgement_error or t.DASH,
        deliveries=delivery_lines,
        signals=signal_lines,
    ) + store_section(store_call, store_max_attempts)
