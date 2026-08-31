"""Signal-based rule engine.

The engine is deliberately decoupled from Telegram: it consumes *signals*
that were already persisted for an order and produces a status decision.
Adding a future signal source (API callback, payment verification, timeout,
OCR, an inline button) only means persisting a new ``OrderSignal`` -- the
evaluation below does not change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.database.models import StatusRule
from app.utils.enums import OrderStatus, RuleMode, SignalKey


@dataclass(frozen=True, slots=True)
class RuleEvaluation:
    status: OrderStatus
    matched: bool
    mode: RuleMode
    required_signals: frozenset[str]
    present_signals: frozenset[str]
    missing_signals: frozenset[str]

    def reason(self) -> str:
        if not self.matched:
            return ""
        present = ", ".join(sorted(self.present_signals & self.required_signals))
        return f"{self.mode.value} rule satisfied by: {present}"


@dataclass(frozen=True, slots=True)
class Decision:
    """Outcome of evaluating both rule sets for one order."""

    status: OrderStatus
    success: RuleEvaluation
    failure: RuleEvaluation
    reason: str = ""

    @property
    def is_conflict(self) -> bool:
        return self.status is OrderStatus.CONFLICT


def enabled_signal_keys(rule: StatusRule) -> frozenset[str]:
    return frozenset(signal.signal_key for signal in rule.signals if signal.enabled)


def evaluate_rule(
    rule: StatusRule, status: OrderStatus, present: set[str]
) -> RuleEvaluation:
    """Evaluate one rule set against the signals observed so far."""
    required = enabled_signal_keys(rule)
    mode = RuleMode(rule.mode)
    present_frozen = frozenset(present)

    if not rule.enabled or not required:
        # A rule with no enabled signal can never fire. This is also why the
        # admin panel refuses to save "mode = ALL with zero signals".
        return RuleEvaluation(
            status=status,
            matched=False,
            mode=mode,
            required_signals=required,
            present_signals=present_frozen,
            missing_signals=required,
        )

    overlap = required & present_frozen
    missing = required - present_frozen
    matched = bool(overlap) if mode is RuleMode.ANY else not missing

    return RuleEvaluation(
        status=status,
        matched=matched,
        mode=mode,
        required_signals=required,
        present_signals=present_frozen,
        missing_signals=missing,
    )


def decide(
    success_rule: StatusRule,
    failure_rule: StatusRule,
    success_signals: set[str],
    failure_signals: set[str],
) -> Decision:
    """Combine both rule sets into a single status decision.

    * both matched  -> ``CONFLICT`` (nothing is dispatched until an admin rules)
    * success only  -> ``SUCCESS``
    * failure only  -> ``FAILED``
    * neither       -> ``PENDING``
    """
    success = evaluate_rule(success_rule, OrderStatus.SUCCESS, success_signals)
    failure = evaluate_rule(failure_rule, OrderStatus.FAILED, failure_signals)

    if success.matched and failure.matched:
        return Decision(
            status=OrderStatus.CONFLICT,
            success=success,
            failure=failure,
            reason=(
                "Success and failure rules matched simultaneously "
                f"(success: {success.reason()}; failure: {failure.reason()})"
            ),
        )
    if success.matched:
        return Decision(
            status=OrderStatus.SUCCESS, success=success, failure=failure, reason=success.reason()
        )
    if failure.matched:
        return Decision(
            status=OrderStatus.FAILED, success=success, failure=failure, reason=failure.reason()
        )
    return Decision(status=OrderStatus.PENDING, success=success, failure=failure, reason="")


@dataclass(slots=True)
class ExtractedSignal:
    """A signal candidate produced from a Telegram event."""

    rule_status: OrderStatus
    signal_key: SignalKey
    trigger_type: str
    trigger_chat_id: int
    trigger_message_id: int | None
    actor_user_id: int
    detail: dict = field(default_factory=dict)
