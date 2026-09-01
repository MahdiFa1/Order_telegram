"""Every admin keyboard must build, and every callback must round-trip.

aiogram rejects ':' inside a packed callback value and Telegram caps
callback_data at 64 bytes, so both are asserted for every button the panel
can render.
"""

from __future__ import annotations

import pytest
from aiogram.types import InlineKeyboardMarkup

from app.admin import strings
from app.bot.keyboards import admin as kb
from app.bot.keyboards.callbacks import (
    AckCB,
    AdminCB,
    AuditCB,
    ChatCB,
    Nav,
    OperatorCB,
    OrderCB,
    ReportCB,
    ResultCB,
    RouteCB,
    RuleCB,
    SettingCB,
    SourceCB,
)
from app.database.models import (
    AcknowledgementConfig,
    ProgressReaction,
    ResultConfig,
    SourceReactionConfig,
    Admin,
    Operator,
    Order,
    ResultDestination,
    Route,
    RuleReaction,
    RuleSignal,
    RuleTextPattern,
    SourceChannel,
    StatusRule,
    WorkGroup,
)
from app.utils.enums import (
    AcknowledgementTargetMode,
    SourceReactionStage,
    AdminRole,
    DispatchPolicy,
    MatchMode,
    OrderStatus,
    RuleMode,
    SettingKey,
    SignalKey,
)

pytestmark = pytest.mark.asyncio

CALLBACK_CLASSES = [
    Nav, ChatCB, RouteCB, OperatorCB, RuleCB, AckCB, ReportCB, OrderCB, SettingCB,
    AdminCB, AuditCB, SourceCB, ResultCB,
]

TELEGRAM_CALLBACK_LIMIT = 64


def assert_keyboard_is_valid(markup: InlineKeyboardMarkup) -> int:
    """Every button must carry callback data that packs, fits and unpacks."""
    known = {cls.__prefix__: cls for cls in CALLBACK_CLASSES}
    count = 0
    for row in markup.inline_keyboard:
        for button in row:
            data = button.callback_data
            assert data, f"button {button.text!r} has no callback data"
            assert len(data.encode()) <= TELEGRAM_CALLBACK_LIMIT, (
                f"callback data too long for Telegram: {data!r}"
            )
            prefix = data.split(":", 1)[0]
            assert prefix in known, f"unknown callback prefix in {data!r}"
            # Round-trips without raising => no stray separator in any value.
            known[prefix].unpack(data)
            count += 1
    return count


# --- fixtures built in memory (no database needed) ------------------------
def a_source() -> SourceChannel:
    return SourceChannel(id=1, chat_id=-1001, title="Source A", enabled=True)


def a_group() -> WorkGroup:
    return WorkGroup(id=2, chat_id=-1002, title="Work Group 1", enabled=True)


def a_destination(primary: bool = True) -> ResultDestination:
    return ResultDestination(
        id=3,
        status=OrderStatus.SUCCESS,
        chat_id=-1003,
        title="Successful Orders",
        enabled=True,
        required=True,
        is_primary=primary,
        position=0,
    )


def a_route() -> Route:
    route = Route(id=4, source_channel_id=1, work_group_id=2, enabled=True)
    route.source_channel = a_source()
    route.work_group = a_group()
    return route


def an_operator() -> Operator:
    operator = Operator(
        id=5, telegram_user_id=999, display_name="Op", enabled=True, all_work_groups=False
    )
    operator.assignments = []
    return operator


def a_rule(mode: RuleMode = RuleMode.ANY) -> StatusRule:
    rule = StatusRule(id=6, status=OrderStatus.SUCCESS, mode=mode, enabled=True)
    rule.signals = [RuleSignal(signal_key=key.value, enabled=True) for key in SignalKey]
    rule.text_patterns = []
    rule.reactions = []
    return rule


def an_ack_config() -> AcknowledgementConfig:
    return AcknowledgementConfig(
        id=7,
        status=OrderStatus.SUCCESS,
        enabled=True,
        reaction_value="✅",
        target_mode=AcknowledgementTargetMode.SMART,
        dispatch_policy=DispatchPolicy.ALL_REQUIRED_DESTINATIONS,
        retry_enabled=True,
        max_retry_count=3,
    )


def a_result_config() -> ResultConfig:
    return ResultConfig(
        id=9,
        status=OrderStatus.SUCCESS,
        append_text_enabled=True,
        append_text="✅ انجام شد",
        woo_enabled=True,
        woo_status="completed",
        woo_note_enabled=True,
        woo_note="note",
    )


def an_order() -> Order:
    return Order(id=8, status=OrderStatus.PENDING, display_number="order8")


REQUIRED_SECTIONS = (
    "dashboard",
    "sources",
    "workgroups",
    "routing",
    "operators",
    "rules_success",
    "rules_failed",
    "reactions",
    "destinations",
    "reports",
    "find_order",
    "settings",
    "system_status",
    "audit",
    "source_reactions",
    "result_content",
)


async def test_main_menu_exposes_every_required_section():
    """Every section the specification requires must be reachable."""
    markup = kb.main_menu()
    assert_keyboard_is_valid(markup)

    sections = {
        Nav.unpack(button.callback_data).section
        for row in markup.inline_keyboard
        for button in row
    }
    assert sections == set(REQUIRED_SECTIONS)


async def test_main_menu_is_labelled_in_persian():
    labels = [b.text for row in kb.main_menu().inline_keyboard for b in row]
    assert labels == [label for label, _section in strings.MENU_ITEMS]
    # No Latin letters should survive in a button label (emoji are fine).
    leftovers = [
        label for label in labels if any(ch.isascii() and ch.isalpha() for ch in label)
    ]
    assert leftovers == []


@pytest.mark.parametrize(
    "markup_factory",
    [
        lambda: kb.main_menu(),
        lambda: kb.destinations_menu(),
        lambda: kb.reactions_menu(),
        lambda: kb.reports_menu(),
        lambda: kb.report_result_keyboard(),
        lambda: kb.counter_scope_picker(),
        lambda: kb.chat_list("source", [a_source()]),
        lambda: kb.chat_list("workgroup", [a_group()]),
        lambda: kb.chat_list("dest", [a_destination()], "destinations", OrderStatus.SUCCESS),
        lambda: kb.chat_detail("source", a_source(), "sources"),
        lambda: kb.chat_detail("dest", a_destination(), "destinations", OrderStatus.SUCCESS),
        lambda: kb.confirm_delete("source", 1, "sources"),
        lambda: kb.routing_list([a_route()]),
        lambda: kb.routing_detail(a_route()),
        lambda: kb.pick_entities([a_source()], "pick_source"),
        lambda: kb.pick_entities([a_group()], "create", arg=1),
        lambda: kb.operator_list([an_operator()]),
        lambda: kb.operator_detail(an_operator(), [a_group()], set()),
        lambda: kb.rules_menu(OrderStatus.SUCCESS, a_rule(), {SignalKey.REACTION.value}),
        lambda: kb.rules_menu(OrderStatus.FAILED, a_rule(RuleMode.ALL), set()),
        lambda: kb.text_pattern_list(
            OrderStatus.SUCCESS,
            [RuleTextPattern(id=1, pattern="done", match_mode=MatchMode.CONTAINS, enabled=True)],
        ),
        lambda: kb.match_mode_picker(OrderStatus.FAILED),
        lambda: kb.rule_reaction_list(
            OrderStatus.SUCCESS, [RuleReaction(id=1, emoji="✅", enabled=True)]
        ),
        lambda: kb.acknowledgement_detail(OrderStatus.SUCCESS, an_ack_config()),
        lambda: kb.acknowledgement_detail(OrderStatus.FAILED, an_ack_config()),
        lambda: kb.target_mode_picker(OrderStatus.SUCCESS),
        lambda: kb.dispatch_policy_picker(OrderStatus.FAILED),
        lambda: kb.order_actions(an_order()),
        lambda: kb.override_options(8, OrderStatus.SUCCESS.value),
        lambda: kb.override_options(8, OrderStatus.FAILED.value),
        lambda: kb.settings_menu(
            {
                SettingKey.COUNTER_SCOPE: "GLOBAL",
                SettingKey.ORDER_PREFIX: "order",
                SettingKey.ORDER_NUMBER_FORMAT: "{prefix}{number}",
                SettingKey.ADMIN_NOTIFICATIONS_ENABLED: "true",
            }
        ),
        lambda: kb.admin_list(
            [Admin(id=1, telegram_user_id=1000, role=AdminRole.SUPER_ADMIN, enabled=True, from_env=True)]
        ),
        lambda: kb.admin_detail(
            Admin(id=2, telegram_user_id=2000, role=AdminRole.ADMIN, enabled=True, from_env=False)
        ),
        lambda: kb.admin_detail(
            Admin(id=1, telegram_user_id=1000, role=AdminRole.SUPER_ADMIN, enabled=True, from_env=True)
        ),
        lambda: kb.audit_keyboard(0, True),
        lambda: kb.audit_keyboard(20, False),
        lambda: kb.source_reactions_menu(
            [
                SourceReactionConfig(id=i, stage=stage.value, enabled=bool(i % 2), reaction_value="👀")
                for i, stage in enumerate(SourceReactionStage, start=1)
            ]
        ),
        lambda: kb.source_stage_detail(
            SourceReactionConfig(
                id=1, stage=SourceReactionStage.RECEIVED, enabled=True, reaction_value="👀"
            )
        ),
        lambda: kb.progress_reaction_list([ProgressReaction(id=1, emoji="👍", enabled=True)]),
        lambda: kb.result_content_menu("ORDER_AND_ATTACHMENTS"),
        lambda: kb.result_mode_picker(),
        lambda: kb.append_text_detail(OrderStatus.SUCCESS, a_result_config()),
        lambda: kb.woo_detail(OrderStatus.FAILED, a_result_config()),
        lambda: kb.woo_store_detail(),
        lambda: kb.order_number_detail(True, True, 7),
        lambda: kb.order_number_detail(False, False, 12),
    ],
)
async def test_every_keyboard_builds_and_round_trips(markup_factory):
    assert assert_keyboard_is_valid(markup_factory()) > 0


async def test_override_options_encode_both_flags():
    markup = kb.override_options(8, OrderStatus.SUCCESS.value)
    decoded = [
        OrderCB.unpack(button.callback_data)
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data.startswith("ord:mark_go")
    ]
    assert {d.flags for d in decoded} == {"11", "10", "00"}
    assert all(d.arg == OrderStatus.SUCCESS.value for d in decoded)
    assert all(d.id == 8 for d in decoded)


async def test_env_super_admin_detail_offers_no_destructive_action():
    """A locked super admin must not be demotable, disableable or removable."""
    locked = kb.admin_detail(
        Admin(id=1, telegram_user_id=1000, role=AdminRole.SUPER_ADMIN, enabled=True, from_env=True)
    )
    labels = [b.text for row in locked.inline_keyboard for b in row]
    assert labels == [strings.BTN_BACK]

    unlocked = kb.admin_detail(
        Admin(id=2, telegram_user_id=2000, role=AdminRole.ADMIN, enabled=True, from_env=False)
    )
    unlocked_labels = [b.text for row in unlocked.inline_keyboard for b in row]
    assert strings.BTN_REMOVE_ADMIN in unlocked_labels
    assert strings.BTN_PROMOTE in unlocked_labels


async def test_rules_menu_lists_every_signal():
    markup = kb.rules_menu(OrderStatus.SUCCESS, a_rule(), set())
    signal_args = [
        RuleCB.unpack(button.callback_data).arg
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data.startswith("rule:")
        and RuleCB.unpack(button.callback_data).action == "signal"
    ]
    assert set(signal_args) == {key.value for key in SignalKey}
