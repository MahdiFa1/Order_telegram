"""Pure unit tests for the rule engine and text matching."""

from __future__ import annotations

import pytest

from app.database.models import RuleSignal, RuleTextPattern, StatusRule
from app.rules.engine import decide, evaluate_rule
from app.rules.matching import matches, validate_pattern
from app.utils.enums import MatchMode, OrderStatus, RuleMode, SignalKey

pytestmark = pytest.mark.asyncio


def build_rule(
    status: OrderStatus,
    mode: RuleMode,
    enabled_signals: tuple[SignalKey, ...],
    enabled: bool = True,
) -> StatusRule:
    rule = StatusRule(status=status.value, mode=mode.value, enabled=enabled)
    rule.signals = [
        RuleSignal(signal_key=key.value, enabled=key in enabled_signals) for key in SignalKey
    ]
    rule.text_patterns = []
    rule.reactions = []
    return rule


async def test_any_mode_matches_on_a_single_signal():
    rule = build_rule(
        OrderStatus.SUCCESS, RuleMode.ANY, (SignalKey.REPLY_PHOTO, SignalKey.REACTION)
    )
    result = evaluate_rule(rule, OrderStatus.SUCCESS, {SignalKey.REACTION.value})
    assert result.matched is True


async def test_all_mode_requires_every_enabled_signal():
    rule = build_rule(
        OrderStatus.FAILED, RuleMode.ALL, (SignalKey.REPLY_TEXT, SignalKey.REACTION)
    )
    partial = evaluate_rule(rule, OrderStatus.FAILED, {SignalKey.REPLY_TEXT.value})
    assert partial.matched is False
    assert partial.missing_signals == frozenset({SignalKey.REACTION.value})

    complete = evaluate_rule(
        rule,
        OrderStatus.FAILED,
        {SignalKey.REPLY_TEXT.value, SignalKey.REACTION.value},
    )
    assert complete.matched is True


async def test_rule_with_no_enabled_signal_never_matches():
    for mode in (RuleMode.ANY, RuleMode.ALL):
        rule = build_rule(OrderStatus.SUCCESS, mode, ())
        result = evaluate_rule(rule, OrderStatus.SUCCESS, {SignalKey.REACTION.value})
        assert result.matched is False


async def test_disabled_rule_never_matches():
    rule = build_rule(
        OrderStatus.SUCCESS, RuleMode.ANY, (SignalKey.REACTION,), enabled=False
    )
    assert evaluate_rule(rule, OrderStatus.SUCCESS, {SignalKey.REACTION.value}).matched is False


async def test_signals_outside_the_rule_are_ignored():
    rule = build_rule(OrderStatus.SUCCESS, RuleMode.ANY, (SignalKey.REPLY_PHOTO,))
    assert evaluate_rule(rule, OrderStatus.SUCCESS, {SignalKey.REACTION.value}).matched is False


async def test_decide_returns_conflict_when_both_rules_match():
    success = build_rule(OrderStatus.SUCCESS, RuleMode.ANY, (SignalKey.REACTION,))
    failure = build_rule(OrderStatus.FAILED, RuleMode.ANY, (SignalKey.REACTION,))
    decision = decide(
        success, failure, {SignalKey.REACTION.value}, {SignalKey.REACTION.value}
    )
    assert decision.status is OrderStatus.CONFLICT
    assert decision.is_conflict


async def test_decide_returns_pending_when_nothing_matches():
    success = build_rule(OrderStatus.SUCCESS, RuleMode.ANY, (SignalKey.REACTION,))
    failure = build_rule(OrderStatus.FAILED, RuleMode.ANY, (SignalKey.REACTION,))
    assert decide(success, failure, set(), set()).status is OrderStatus.PENDING


async def test_decide_prefers_the_matching_rule():
    success = build_rule(OrderStatus.SUCCESS, RuleMode.ANY, (SignalKey.REPLY_PHOTO,))
    failure = build_rule(OrderStatus.FAILED, RuleMode.ANY, (SignalKey.REPLY_TEXT,))
    assert (
        decide(success, failure, {SignalKey.REPLY_PHOTO.value}, set()).status
        is OrderStatus.SUCCESS
    )
    assert (
        decide(success, failure, set(), {SignalKey.REPLY_TEXT.value}).status
        is OrderStatus.FAILED
    )


# --- text matching --------------------------------------------------------
def pattern(value: str, mode: MatchMode, case_sensitive: bool = False) -> RuleTextPattern:
    return RuleTextPattern(
        pattern=value, match_mode=mode.value, case_sensitive=case_sensitive, enabled=True
    )


async def test_contains_is_case_insensitive_by_default():
    assert matches("Order is DONE now", pattern("done", MatchMode.CONTAINS)) is True


async def test_case_sensitive_contains_respects_case():
    assert matches("DONE", pattern("done", MatchMode.CONTAINS, True)) is False
    assert matches("done", pattern("done", MatchMode.CONTAINS, True)) is True


async def test_exact_ignores_surrounding_whitespace_only():
    assert matches("  done  ", pattern("done", MatchMode.EXACT)) is True
    assert matches("done now", pattern("done", MatchMode.EXACT)) is False


async def test_regex_mode_matches():
    assert matches("ORD 42 ok", pattern(r"\bORD \d+\b", MatchMode.REGEX)) is True


async def test_invalid_regex_never_matches_and_never_raises():
    assert matches("anything", pattern("([unclosed", MatchMode.REGEX)) is False


async def test_disabled_pattern_never_matches():
    row = pattern("done", MatchMode.CONTAINS)
    row.enabled = False
    assert matches("done", row) is False


async def test_persian_contains_matches():
    assert matches("سفارش انجام شد", pattern("انجام شد", MatchMode.CONTAINS)) is True


async def test_validate_pattern_rejects_empty_and_bad_regex():
    assert validate_pattern("   ", MatchMode.CONTAINS) is not None
    assert validate_pattern("([", MatchMode.REGEX) is not None
    assert validate_pattern("done", MatchMode.CONTAINS) is None
