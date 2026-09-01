"""Domain enumerations shared across the whole application.

Every value is stored in PostgreSQL as a plain string so that adding a new
member never requires a database type migration.
"""

from __future__ import annotations

from enum import StrEnum


class AdminRole(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CONFLICT = "CONFLICT"

    @property
    def is_terminal(self) -> bool:
        return self in (OrderStatus.SUCCESS, OrderStatus.FAILED)


#: Statuses that own rule sets, destinations and acknowledgement configs.
RESULT_STATUSES: tuple[OrderStatus, ...] = (OrderStatus.SUCCESS, OrderStatus.FAILED)


class RuleMode(StrEnum):
    ANY = "ANY"
    ALL = "ALL"


class SignalKey(StrEnum):
    """Signals a rule can be built from.

    The rule engine is signal-agnostic: adding e.g. ``API_CALLBACK`` or
    ``PAYMENT_VERIFIED`` later only requires a new member plus an extractor.
    """

    REPLY_PHOTO = "REPLY_PHOTO"
    REPLY_VIDEO = "REPLY_VIDEO"
    REPLY_DOCUMENT = "REPLY_DOCUMENT"
    REPLY_AUDIO = "REPLY_AUDIO"
    REPLY_VOICE = "REPLY_VOICE"
    REPLY_ANIMATION = "REPLY_ANIMATION"
    REPLY_TEXT = "REPLY_TEXT"
    REACTION = "REACTION"


#: Signals produced by an operator replying with a media message.
MEDIA_SIGNALS: tuple[SignalKey, ...] = (
    SignalKey.REPLY_PHOTO,
    SignalKey.REPLY_VIDEO,
    SignalKey.REPLY_DOCUMENT,
    SignalKey.REPLY_AUDIO,
    SignalKey.REPLY_VOICE,
    SignalKey.REPLY_ANIMATION,
)

SIGNAL_LABELS: dict[SignalKey, str] = {
    SignalKey.REPLY_PHOTO: "Reply Photo",
    SignalKey.REPLY_VIDEO: "Reply Video",
    SignalKey.REPLY_DOCUMENT: "Reply Document",
    SignalKey.REPLY_AUDIO: "Reply Audio",
    SignalKey.REPLY_VOICE: "Reply Voice",
    SignalKey.REPLY_ANIMATION: "Reply Animation",
    SignalKey.REPLY_TEXT: "Reply Text",
    SignalKey.REACTION: "Reaction",
}


class MatchMode(StrEnum):
    EXACT = "EXACT"
    CONTAINS = "CONTAINS"
    REGEX = "REGEX"


class TriggerType(StrEnum):
    """What made an order reach its terminal state."""

    REPLY_PHOTO = "REPLY_PHOTO"
    REPLY_VIDEO = "REPLY_VIDEO"
    REPLY_DOCUMENT = "REPLY_DOCUMENT"
    REPLY_AUDIO = "REPLY_AUDIO"
    REPLY_VOICE = "REPLY_VOICE"
    REPLY_ANIMATION = "REPLY_ANIMATION"
    REPLY_TEXT = "REPLY_TEXT"
    REACTION = "REACTION"
    MANUAL = "MANUAL"


#: Signals map 1:1 onto trigger types for operator-driven events.
SIGNAL_TO_TRIGGER: dict[SignalKey, TriggerType] = {
    SignalKey.REPLY_PHOTO: TriggerType.REPLY_PHOTO,
    SignalKey.REPLY_VIDEO: TriggerType.REPLY_VIDEO,
    SignalKey.REPLY_DOCUMENT: TriggerType.REPLY_DOCUMENT,
    SignalKey.REPLY_AUDIO: TriggerType.REPLY_AUDIO,
    SignalKey.REPLY_VOICE: TriggerType.REPLY_VOICE,
    SignalKey.REPLY_ANIMATION: TriggerType.REPLY_ANIMATION,
    SignalKey.REPLY_TEXT: TriggerType.REPLY_TEXT,
    SignalKey.REACTION: TriggerType.REACTION,
}


class DispatchStatus(StrEnum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class OrderDispatchState(StrEnum):
    """Aggregate view of all result dispatches belonging to an order."""

    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    SENT = "SENT"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class AcknowledgementStatus(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    APPLYING = "APPLYING"
    APPLIED = "APPLIED"
    FAILED = "FAILED"


class AcknowledgementTargetMode(StrEnum):
    SMART = "SMART"
    TRIGGER_MESSAGE = "TRIGGER_MESSAGE"
    ORDER_MESSAGE = "ORDER_MESSAGE"


class DispatchPolicy(StrEnum):
    ALL_REQUIRED_DESTINATIONS = "ALL_REQUIRED_DESTINATIONS"
    ANY_DESTINATION = "ANY_DESTINATION"
    PRIMARY_DESTINATION = "PRIMARY_DESTINATION"


class ReactionType(StrEnum):
    EMOJI = "emoji"
    CUSTOM_EMOJI = "custom_emoji"


class CounterScope(StrEnum):
    GLOBAL = "GLOBAL"
    PER_SOURCE = "PER_SOURCE"


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class ContentType(StrEnum):
    TEXT = "TEXT"
    PHOTO = "PHOTO"
    VIDEO = "VIDEO"
    DOCUMENT = "DOCUMENT"
    AUDIO = "AUDIO"
    VOICE = "VOICE"
    ANIMATION = "ANIMATION"
    STICKER = "STICKER"
    VIDEO_NOTE = "VIDEO_NOTE"
    OTHER = "OTHER"


CONTENT_TYPE_TO_SIGNAL: dict[ContentType, SignalKey] = {
    ContentType.PHOTO: SignalKey.REPLY_PHOTO,
    ContentType.VIDEO: SignalKey.REPLY_VIDEO,
    ContentType.DOCUMENT: SignalKey.REPLY_DOCUMENT,
    ContentType.AUDIO: SignalKey.REPLY_AUDIO,
    ContentType.VOICE: SignalKey.REPLY_VOICE,
    ContentType.ANIMATION: SignalKey.REPLY_ANIMATION,
}


class AuditEvent(StrEnum):
    ORDER_CREATED = "ORDER_CREATED"
    ORDER_ROUTED = "ORDER_ROUTED"
    ORDER_ROUTE_FAILED = "ORDER_ROUTE_FAILED"
    OPERATOR_SIGNAL_RECEIVED = "OPERATOR_SIGNAL_RECEIVED"
    SUCCESS_RULE_MATCHED = "SUCCESS_RULE_MATCHED"
    FAILURE_RULE_MATCHED = "FAILURE_RULE_MATCHED"
    STATUS_CHANGED = "STATUS_CHANGED"
    CONFLICT_DETECTED = "CONFLICT_DETECTED"
    RESULT_DISPATCH_ATTEMPTED = "RESULT_DISPATCH_ATTEMPTED"
    RESULT_DISPATCH_SUCCEEDED = "RESULT_DISPATCH_SUCCEEDED"
    RESULT_DISPATCH_FAILED = "RESULT_DISPATCH_FAILED"
    ACKNOWLEDGEMENT_ATTEMPTED = "ACKNOWLEDGEMENT_ATTEMPTED"
    ACKNOWLEDGEMENT_APPLIED = "ACKNOWLEDGEMENT_APPLIED"
    ACKNOWLEDGEMENT_FAILED = "ACKNOWLEDGEMENT_FAILED"
    ACKNOWLEDGEMENT_SKIPPED = "ACKNOWLEDGEMENT_SKIPPED"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"
    RULE_CHANGED = "RULE_CHANGED"
    REACTION_CONFIGURATION_CHANGED = "REACTION_CONFIGURATION_CHANGED"
    CONFIGURATION_CHANGED = "CONFIGURATION_CHANGED"
    RECOVERY_PERFORMED = "RECOVERY_PERFORMED"
    ORDER_REJECTED = "ORDER_REJECTED"
    SOURCE_REACTION_APPLIED = "SOURCE_REACTION_APPLIED"
    SOURCE_REACTION_FAILED = "SOURCE_REACTION_FAILED"
    ORDER_IN_PROGRESS = "ORDER_IN_PROGRESS"
    WOOCOMMERCE_UPDATED = "WOOCOMMERCE_UPDATED"
    WOOCOMMERCE_FAILED = "WOOCOMMERCE_FAILED"


class SourceReactionStage(StrEnum):
    """Points in an order's life at which the SOURCE message is re-reacted.

    Telegram lets a bot hold one reaction per message, so each stage replaces
    the previous one -- the source message always shows the latest state.
    """

    RECEIVED = "RECEIVED"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


SOURCE_STAGE_LABELS: dict[SourceReactionStage, str] = {
    SourceReactionStage.RECEIVED: "دریافت شد",
    SourceReactionStage.IN_PROGRESS: "در حال انجام",
    SourceReactionStage.SUCCESS: "موفق",
    SourceReactionStage.FAILED: "ناموفق",
}


class AttachmentSource(StrEnum):
    OPERATOR = "OPERATOR"


class ResultContentMode(StrEnum):
    """What the result destination receives."""

    ORDER_AND_ATTACHMENTS = "ORDER_AND_ATTACHMENTS"
    ATTACHMENTS_ONLY = "ATTACHMENTS_ONLY"


class SettingKey(StrEnum):
    COUNTER_SCOPE = "counter_scope"
    ORDER_PREFIX = "order_prefix"
    ORDER_NUMBER_FORMAT = "order_number_format"
    ADMIN_NOTIFICATIONS_ENABLED = "admin_notifications_enabled"
    # --- order number extracted from the source message ---
    ORDER_NUMBER_ENABLED = "order_number_enabled"
    ORDER_NUMBER_LENGTH = "order_number_length"
    ORDER_NUMBER_REJECT_MESSAGE = "order_number_reject_message"
    ORDER_NUMBER_DELETE_INVALID = "order_number_delete_invalid"
    # --- WooCommerce store credentials (one store per deployment) ---
    WOO_BASE_URL = "woo_base_url"
    WOO_CONSUMER_KEY = "woo_consumer_key"
    WOO_CONSUMER_SECRET = "woo_consumer_secret"
    # --- result content ---
    RESULT_CONTENT_MODE = "result_content_mode"
