"""Admin panel keyboards."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards.callbacks import (
    AckCB,
    AdminCB,
    AuditCB,
    ChatCB,
    Nav,
    OperatorCB,
    OrderCB,
    ReportCB,
    RouteCB,
    RuleCB,
    SettingCB,
)
from app.admin import strings as t
from app.bot.keyboards.common import back_button, toggle_icon
from app.utils.enums import OrderStatus, SignalKey

MAIN_MENU: list[tuple[str, str]] = t.MENU_ITEMS


def main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index in range(0, len(MAIN_MENU), 2):
        row = [
            InlineKeyboardButton(text=label, callback_data=Nav(section=section).pack())
            for label, section in MAIN_MENU[index : index + 2]
        ]
        builder.row(*row)
    return builder.as_markup()


# ---------------------------------------------------------------------------
# Chats (sources / work groups / destinations)
# ---------------------------------------------------------------------------
def chat_list(kind: str, entities, back: str = "main", arg: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for entity in entities:
        title = entity.title or (f"@{entity.username}" if entity.username else str(entity.chat_id))
        builder.row(
            InlineKeyboardButton(
                text=f"{toggle_icon(entity.enabled)} {title}",
                callback_data=ChatCB(kind=kind, action="view", id=entity.id, arg=arg).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_ADD,
            callback_data=ChatCB(kind=kind, action="add", arg=arg).pack(),
        )
    )
    builder.row(back_button(back))
    return builder.as_markup()


def chat_detail(kind: str, entity, back_section: str, arg: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t.toggle_button(entity.enabled),
            callback_data=ChatCB(kind=kind, action="toggle", id=entity.id, arg=arg).pack(),
        ),
        InlineKeyboardButton(
            text=t.BTN_TEST_ACCESS,
            callback_data=ChatCB(kind=kind, action="test", id=entity.id, arg=arg).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_EDIT_TITLE,
            callback_data=ChatCB(kind=kind, action="edit", id=entity.id, arg=arg).pack(),
        ),
        InlineKeyboardButton(
            text=t.BTN_DELETE,
            callback_data=ChatCB(kind=kind, action="delete", id=entity.id, arg=arg).pack(),
        ),
    )
    if kind == "dest":
        builder.row(
            InlineKeyboardButton(
                text=t.BTN_REQUIRED.format(value=t.yes_no(entity.required)),
                callback_data=ChatCB(
                    kind=kind, action="required", id=entity.id, arg=arg
                ).pack(),
            ),
            InlineKeyboardButton(
                text=t.LABEL_PRIMARY if entity.is_primary else t.BTN_MAKE_PRIMARY,
                callback_data=ChatCB(kind=kind, action="primary", id=entity.id, arg=arg).pack(),
            ),
        )
    builder.row(back_button(back_section))
    return builder.as_markup()


def confirm_delete(kind: str, entity_id: int, back_section: str, arg: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_CONFIRM_DELETE,
            callback_data=ChatCB(
                kind=kind, action="delete_confirm", id=entity_id, arg=arg
            ).pack(),
        ),
        InlineKeyboardButton(text=t.BTN_CANCEL, callback_data=Nav(section=back_section).pack()),
    )
    return builder.as_markup()


def destinations_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_SUCCESS_DESTINATIONS,
            callback_data=ChatCB(kind="dest", action="list", arg=OrderStatus.SUCCESS).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_FAILURE_DESTINATIONS,
            callback_data=ChatCB(kind="dest", action="list", arg=OrderStatus.FAILED).pack(),
        )
    )
    builder.row(back_button("main"))
    return builder.as_markup()


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
def routing_list(routes) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for route in routes:
        source = route.source_channel.title or str(route.source_channel.chat_id)
        target = route.work_group.title or str(route.work_group.chat_id)
        builder.row(
            InlineKeyboardButton(
                text=f"{toggle_icon(route.enabled)} {source} → {target}",
                callback_data=RouteCB(action="view", id=route.id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(text=t.BTN_ADD_ROUTE, callback_data=RouteCB(action="add").pack())
    )
    builder.row(back_button("main"))
    return builder.as_markup()


def routing_detail(route) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t.toggle_button(route.enabled),
            callback_data=RouteCB(action="toggle", id=route.id).pack(),
        ),
        InlineKeyboardButton(
            text=t.BTN_DELETE, callback_data=RouteCB(action="delete", id=route.id).pack()
        ),
    )
    builder.row(back_button("routing"))
    return builder.as_markup()


def pick_entities(entities, action: str, arg: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for entity in entities:
        title = entity.title or str(entity.chat_id)
        builder.row(
            InlineKeyboardButton(
                text=title,
                callback_data=RouteCB(action=action, id=entity.id, arg=arg).pack(),
            )
        )
    builder.row(back_button("routing"))
    return builder.as_markup()


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------
def operator_list(operators) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for operator in operators:
        name = operator.display_name or (
            f"@{operator.username}" if operator.username else str(operator.telegram_user_id)
        )
        builder.row(
            InlineKeyboardButton(
                text=f"{toggle_icon(operator.enabled)} {name}",
                callback_data=OperatorCB(action="view", id=operator.id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_ADD_OPERATOR, callback_data=OperatorCB(action="add").pack()
        )
    )
    builder.row(back_button("main"))
    return builder.as_markup()


def operator_detail(operator, work_groups, assigned_ids: set[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t.toggle_button(operator.enabled),
            callback_data=OperatorCB(action="toggle", id=operator.id).pack(),
        ),
        InlineKeyboardButton(
            text=t.BTN_DELETE,
            callback_data=OperatorCB(action="delete", id=operator.id).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_OPERATOR_SCOPE.format(
                scope=t.OPERATOR_SCOPE_ALL
                if operator.all_work_groups
                else t.OPERATOR_SCOPE_SELECTED
            ),
            callback_data=OperatorCB(action="scope", id=operator.id).pack(),
        )
    )
    if not operator.all_work_groups:
        for group in work_groups:
            mark = "☑️" if group.id in assigned_ids else "⬜️"
            builder.row(
                InlineKeyboardButton(
                    text=f"{mark} {group.title or group.chat_id}",
                    callback_data=OperatorCB(
                        action="assign", id=operator.id, arg=group.id
                    ).pack(),
                )
            )
    builder.row(back_button("operators"))
    return builder.as_markup()


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
def rules_menu(status: OrderStatus, rule, enabled_keys: set[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_DETECTION.format(status=t.toggle_text(rule.enabled)),
            callback_data=RuleCB(status=status, action="toggle_rule").pack(),
        ),
        InlineKeyboardButton(
            text=t.BTN_MODE.format(mode=t.MODE_NAMES.get(rule.mode, rule.mode)),
            callback_data=RuleCB(status=status, action="mode").pack(),
        ),
    )
    for key in SignalKey:
        builder.row(
            InlineKeyboardButton(
                text=f"{toggle_icon(key.value in enabled_keys)} {t.SIGNAL_LABELS_FA[key]}",
                callback_data=RuleCB(status=status, action="signal", arg=key.value).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_TEXT_PATTERNS,
            callback_data=RuleCB(status=status, action="texts").pack(),
        ),
        InlineKeyboardButton(
            text=t.BTN_RULE_REACTIONS,
            callback_data=RuleCB(status=status, action="reactions").pack(),
        ),
    )
    builder.row(back_button("main"))
    return builder.as_markup()


def text_pattern_list(status: OrderStatus, patterns) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for pattern in patterns:
        builder.row(
            InlineKeyboardButton(
                text=(
                    f"{toggle_icon(pattern.enabled)} {pattern.pattern[:24]} "
                    f"[{t.MATCH_MODE_NAMES.get(pattern.match_mode, pattern.match_mode)}]"
                ),
                callback_data=RuleCB(
                    status=status, action="text_toggle", id=pattern.id
                ).pack(),
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=RuleCB(status=status, action="text_del", id=pattern.id).pack(),
            ),
        )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_ADD_PATTERN,
            callback_data=RuleCB(status=status, action="text_add").pack(),
        )
    )
    builder.row(
        back_button(
            "rules_success" if status == OrderStatus.SUCCESS else "rules_failed"
        )
    )
    return builder.as_markup()


def match_mode_picker(status: OrderStatus) -> InlineKeyboardMarkup:
    from app.utils.enums import MatchMode

    builder = InlineKeyboardBuilder()
    for mode in MatchMode:
        builder.row(
            InlineKeyboardButton(
                text=t.MATCH_MODE_NAMES.get(mode.value, mode.value),
                callback_data=RuleCB(status=status, action="text_mode", arg=mode.value).pack(),
            )
        )
    builder.row(back_button("rules_success" if status == OrderStatus.SUCCESS else "rules_failed"))
    return builder.as_markup()


def rule_reaction_list(status: OrderStatus, reactions) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for reaction in reactions:
        builder.row(
            InlineKeyboardButton(
                text=f"{toggle_icon(reaction.enabled)} {reaction.emoji}",
                callback_data=RuleCB(
                    status=status, action="reaction_toggle", id=reaction.id
                ).pack(),
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=RuleCB(
                    status=status, action="reaction_del", id=reaction.id
                ).pack(),
            ),
        )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_ADD_REACTION,
            callback_data=RuleCB(status=status, action="reaction_add").pack(),
        )
    )
    builder.row(back_button("rules_success" if status == OrderStatus.SUCCESS else "rules_failed"))
    return builder.as_markup()


# ---------------------------------------------------------------------------
# Acknowledgements
# ---------------------------------------------------------------------------
def reactions_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_SUCCESS_ACK,
            callback_data=AckCB(status=OrderStatus.SUCCESS, action="view").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_FAILURE_ACK,
            callback_data=AckCB(status=OrderStatus.FAILED, action="view").pack(),
        )
    )
    builder.row(back_button("main"))
    return builder.as_markup()


def acknowledgement_detail(status: OrderStatus, config) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t.toggle_button(config.enabled),
            callback_data=AckCB(status=status, action="toggle").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_CHANGE_REACTION,
            callback_data=AckCB(status=status, action="set_reaction").pack(),
        ),
        InlineKeyboardButton(
            text=t.BTN_CHANGE_TARGET,
            callback_data=AckCB(status=status, action="target").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_DISPATCH_POLICY,
            callback_data=AckCB(status=status, action="policy").pack(),
        ),
        InlineKeyboardButton(
            text=t.BTN_TEST_REACTION,
            callback_data=AckCB(status=status, action="test").pack(),
        ),
    )
    builder.row(back_button("reactions"))
    return builder.as_markup()


def target_mode_picker(status: OrderStatus) -> InlineKeyboardMarkup:
    from app.utils.enums import AcknowledgementTargetMode

    builder = InlineKeyboardBuilder()
    for mode in AcknowledgementTargetMode:
        builder.row(
            InlineKeyboardButton(
                text=t.TARGET_MODE_NAMES.get(mode.value, mode.value),
                callback_data=AckCB(status=status, action="set_target", arg=mode.value).pack(),
            )
        )
    builder.row(back_button("reactions"))
    return builder.as_markup()


def dispatch_policy_picker(status: OrderStatus) -> InlineKeyboardMarkup:
    from app.utils.enums import DispatchPolicy

    builder = InlineKeyboardBuilder()
    for policy in DispatchPolicy:
        builder.row(
            InlineKeyboardButton(
                text=t.DISPATCH_POLICY_NAMES.get(policy.value, policy.value),
                callback_data=AckCB(
                    status=status, action="set_policy", arg=policy.value
                ).pack(),
            )
        )
    builder.row(back_button("reactions"))
    return builder.as_markup()


# ---------------------------------------------------------------------------
# Reports / orders / settings / audit
# ---------------------------------------------------------------------------
def reports_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=t.BTN_TODAY, callback_data=ReportCB(action="today").pack()),
        InlineKeyboardButton(
            text=t.BTN_YESTERDAY, callback_data=ReportCB(action="yesterday").pack()
        ),
    )
    builder.row(
        InlineKeyboardButton(text=t.BTN_LAST_7, callback_data=ReportCB(action="last7").pack()),
        InlineKeyboardButton(
            text=t.BTN_LAST_30, callback_data=ReportCB(action="last30").pack()
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_CUSTOM_RANGE, callback_data=ReportCB(action="custom").pack()
        ),
        InlineKeyboardButton(
            text=t.BTN_BY_OPERATOR, callback_data=ReportCB(action="operators").pack()
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_BY_SOURCE, callback_data=ReportCB(action="sources").pack()
        ),
        InlineKeyboardButton(
            text=t.BTN_BY_WORK_GROUP, callback_data=ReportCB(action="workgroups").pack()
        ),
    )
    builder.row(back_button("main"))
    return builder.as_markup()


def report_result_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(back_button("reports"))
    return builder.as_markup()


def order_actions(order) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_MARK_SUCCESS,
            callback_data=OrderCB(action="mark", id=order.id, arg=OrderStatus.SUCCESS).pack(),
        ),
        InlineKeyboardButton(
            text=t.BTN_MARK_FAILED,
            callback_data=OrderCB(action="mark", id=order.id, arg=OrderStatus.FAILED).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_MARK_PENDING,
            callback_data=OrderCB(action="mark", id=order.id, arg=OrderStatus.PENDING).pack(),
        ),
        InlineKeyboardButton(
            text=t.BTN_RETRY_DISPATCH,
            callback_data=OrderCB(action="retry", id=order.id).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_ORDER_AUDIT,
            callback_data=OrderCB(action="audit", id=order.id).pack(),
        )
    )
    builder.row(back_button("find_order"))
    return builder.as_markup()


def override_options(order_id: int, status: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for label, flags in (
        (t.BTN_OVERRIDE_FULL, "11"),
        (t.BTN_OVERRIDE_DISPATCH, "10"),
        (t.BTN_OVERRIDE_STATUS, "00"),
    ):
        builder.row(
            InlineKeyboardButton(
                text=label,
                callback_data=OrderCB(
                    action="mark_go", id=order_id, arg=status, flags=flags
                ).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_CANCEL, callback_data=OrderCB(action="view", id=order_id).pack()
        )
    )
    return builder.as_markup()


def settings_menu(values: dict[str, str | None]) -> InlineKeyboardMarkup:
    from app.utils.enums import SettingKey

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_COUNTER_SCOPE.format(
                value=t.COUNTER_SCOPE_NAMES.get(
                    values.get(SettingKey.COUNTER_SCOPE) or "",
                    values.get(SettingKey.COUNTER_SCOPE),
                )
            ),
            callback_data=SettingCB(action="counter_scope").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_ORDER_PREFIX.format(value=values.get(SettingKey.ORDER_PREFIX)),
            callback_data=SettingCB(action="prefix").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_NUMBER_FORMAT.format(
                value=values.get(SettingKey.ORDER_NUMBER_FORMAT)
            ),
            callback_data=SettingCB(action="format").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_NOTIFICATIONS.format(
                value=t.NOTIFICATIONS_ON
                if (values.get(SettingKey.ADMIN_NOTIFICATIONS_ENABLED) or "").lower()
                == "true"
                else t.NOTIFICATIONS_OFF
            ),
            callback_data=SettingCB(action="notifications").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_ADMINS, callback_data=AdminCB(action="list").pack()
        )
    )
    builder.row(back_button("main"))
    return builder.as_markup()


def admin_list(admins) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for admin in admins:
        name = admin.display_name or (
            f"@{admin.username}" if admin.username else str(admin.telegram_user_id)
        )
        badge = "👑" if admin.role == "SUPER_ADMIN" else "👮"
        lock = " 🔒" if admin.from_env else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{badge} {name}{lock}",
                callback_data=AdminCB(action="view", id=admin.id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=t.BTN_ADD_ADMIN, callback_data=AdminCB(action="add").pack()
        )
    )
    builder.row(back_button("settings"))
    return builder.as_markup()


def admin_detail(admin) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if not admin.from_env:
        builder.row(
            InlineKeyboardButton(
                text=(
                    t.BTN_DEMOTE if admin.role == "SUPER_ADMIN" else t.BTN_PROMOTE
                ),
                callback_data=AdminCB(action="role", id=admin.id).pack(),
            )
        )
        builder.row(
            InlineKeyboardButton(
                text=t.toggle_button(admin.enabled),
                callback_data=AdminCB(action="toggle", id=admin.id).pack(),
            ),
            InlineKeyboardButton(
                text=t.BTN_REMOVE_ADMIN,
                callback_data=AdminCB(action="delete", id=admin.id).pack(),
            ),
        )
    builder.row(
        InlineKeyboardButton(text=t.BTN_BACK, callback_data=AdminCB(action="list").pack())
    )
    return builder.as_markup()


def counter_scope_picker() -> InlineKeyboardMarkup:
    from app.utils.enums import CounterScope

    builder = InlineKeyboardBuilder()
    for scope in CounterScope:
        builder.row(
            InlineKeyboardButton(
                text=t.COUNTER_SCOPE_NAMES.get(scope.value, scope.value),
                callback_data=SettingCB(action="set_scope", arg=scope.value).pack(),
            )
        )
    builder.row(back_button("settings"))
    return builder.as_markup()


def audit_keyboard(offset: int, has_more: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    buttons = []
    if offset > 0:
        buttons.append(
            InlineKeyboardButton(
                text=t.BTN_NEWER,
                callback_data=AuditCB(action="page", offset=max(0, offset - 10)).pack(),
            )
        )
    if has_more:
        buttons.append(
            InlineKeyboardButton(
                text=t.BTN_OLDER,
                callback_data=AuditCB(action="page", offset=offset + 10).pack(),
            )
        )
    if buttons:
        builder.row(*buttons)
    builder.row(back_button("main"))
    return builder.as_markup()
