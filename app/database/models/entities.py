"""SQLAlchemy ORM models.

Design notes
------------
* Every enum is persisted as a short ``String`` so new members never need a
  database type migration.
* Telegram identifiers are ``BigInteger`` (channel IDs exceed 32 bits).
* Uniqueness constraints, not application checks, are what actually guarantee
  the "exactly once" requirements (duplicate protection, counter allocation,
  one dispatch per destination, one delivery message per Telegram message).
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, IntPK, TimestampMixin
from app.utils.enums import (
    AcknowledgementStatus,
    AcknowledgementTargetMode,
    AdminRole,
    ContentType,
    DeliveryStatus,
    DispatchPolicy,
    DispatchStatus,
    MatchMode,
    OrderDispatchState,
    OrderStatus,
    ReactionType,
    RuleMode,
)


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------
class Admin(Base, IntPK, TimestampMixin):
    __tablename__ = "admins"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(128))
    display_name: Mapped[str | None] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(32), default=AdminRole.ADMIN, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    from_env: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Operator(Base, IntPK, TimestampMixin):
    __tablename__ = "operators"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(128))
    display_name: Mapped[str | None] = mapped_column(String(256))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: When true the operator may act in every work group, and the
    #: ``assignments`` table is ignored for them.
    all_work_groups: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    assignments: Mapped[list["OperatorWorkGroup"]] = relationship(
        back_populates="operator", cascade="all, delete-orphan", lazy="selectin"
    )


class OperatorWorkGroup(Base, IntPK, TimestampMixin):
    __tablename__ = "operator_work_groups"
    __table_args__ = (UniqueConstraint("operator_id", "work_group_id"),)

    operator_id: Mapped[int] = mapped_column(
        ForeignKey("operators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    work_group_id: Mapped[int] = mapped_column(
        ForeignKey("work_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )

    operator: Mapped[Operator] = relationship(back_populates="assignments")
    work_group: Mapped["WorkGroup"] = relationship(lazy="selectin")


# ---------------------------------------------------------------------------
# Chats
# ---------------------------------------------------------------------------
class SourceChannel(Base, IntPK, TimestampMixin):
    __tablename__ = "source_channels"

    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(256))
    username: Mapped[str | None] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class WorkGroup(Base, IntPK, TimestampMixin):
    __tablename__ = "work_groups"

    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(256))
    username: Mapped[str | None] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Route(Base, IntPK, TimestampMixin):
    __tablename__ = "routes"
    __table_args__ = (UniqueConstraint("source_channel_id", "work_group_id"),)

    source_channel_id: Mapped[int] = mapped_column(
        ForeignKey("source_channels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    work_group_id: Mapped[int] = mapped_column(
        ForeignKey("work_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    source_channel: Mapped[SourceChannel] = relationship(lazy="selectin")
    work_group: Mapped[WorkGroup] = relationship(lazy="selectin")


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
class DailyCounter(Base, IntPK, TimestampMixin):
    """Atomic per-business-day counter.

    ``scope_key`` is ``"GLOBAL"`` or ``"SOURCE:<chat_id>"``. Allocation uses a
    single ``INSERT ... ON CONFLICT DO UPDATE ... RETURNING`` statement, so two
    concurrent orders can never receive the same number.
    """

    __tablename__ = "daily_counters"
    __table_args__ = (UniqueConstraint("business_date", "scope_key"),)

    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False)
    last_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Order(Base, IntPK, TimestampMixin):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("business_date", "counter_scope_key", "daily_number"),
        UniqueConstraint("source_chat_id", "source_message_id"),
        # An album shares one media_group_id across N messages: this partial
        # unique index is what makes the whole album collapse into ONE order.
        Index(
            "uq_orders_source_media_group",
            "source_chat_id",
            "source_media_group_id",
            unique=True,
            postgresql_where=text("source_media_group_id IS NOT NULL"),
        ),
        Index("ix_orders_status_business_date", "status", "business_date"),
    )

    uuid: Mapped[uuid_module.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid_module.uuid4, unique=True, nullable=False
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    daily_number: Mapped[int] = mapped_column(Integer, nullable=False)
    counter_scope_key: Mapped[str] = mapped_column(String(64), nullable=False)
    display_number: Mapped[str] = mapped_column(String(64), nullable=False)

    source_channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_channels.id", ondelete="SET NULL"), index=True
    )
    source_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_media_group_id: Mapped[str | None] = mapped_column(String(64), index=True)

    status: Mapped[str] = mapped_column(
        String(32), default=OrderStatus.PENDING, nullable=False, index=True
    )

    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by_user_id: Mapped[int | None] = mapped_column(BigInteger)
    completion_trigger_type: Mapped[str | None] = mapped_column(String(32))
    completion_trigger_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    completion_trigger_message_id: Mapped[int | None] = mapped_column(BigInteger)

    success_reason: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)

    #: Aggregate dispatch state, derived from ``result_dispatches``.
    result_dispatch_status: Mapped[str] = mapped_column(
        String(32), default=OrderDispatchState.NOT_REQUIRED, nullable=False
    )

    acknowledgement_status: Mapped[str] = mapped_column(
        String(32), default=AcknowledgementStatus.NOT_REQUIRED, nullable=False
    )
    acknowledgement_reaction: Mapped[str | None] = mapped_column(String(32))
    acknowledgement_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    acknowledgement_message_id: Mapped[int | None] = mapped_column(BigInteger)
    acknowledgement_applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledgement_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    acknowledgement_error: Mapped[str | None] = mapped_column(Text)

    #: Store order number parsed out of the source message's last line.
    source_order_number: Mapped[str | None] = mapped_column(String(32), index=True)

    #: Set when an operator marks the order as being worked on.
    in_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    in_progress_by_user_id: Mapped[int | None] = mapped_column(BigInteger)

    #: Last lifecycle reaction actually applied to the SOURCE message, so a
    #: repeated event never re-reacts and a restart resumes correctly.
    source_reaction_stage: Mapped[str | None] = mapped_column(String(32))
    source_reaction_value: Mapped[str | None] = mapped_column(String(64))

    attachments: Mapped[list["OrderAttachment"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )
    source_messages: Mapped[list["OrderSourceMessage"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )
    deliveries: Mapped[list["OrderDelivery"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )
    signals: Mapped[list["OrderSignal"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )
    dispatches: Mapped[list["ResultDispatch"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def order_status(self) -> OrderStatus:
        return OrderStatus(self.status)


class OrderSourceMessage(Base, IntPK, TimestampMixin):
    """One row per Telegram message of the source order (album => many)."""

    __tablename__ = "order_source_messages"
    __table_args__ = (UniqueConstraint("chat_id", "message_id"),)

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_group_id: Mapped[str | None] = mapped_column(String(64))
    content_type: Mapped[str] = mapped_column(String(32), default=ContentType.OTHER, nullable=False)
    file_id: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)
    entities: Mapped[list | None] = mapped_column(JSONB)
    caption_entities: Mapped[list | None] = mapped_column(JSONB)
    has_spoiler: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    order: Mapped[Order] = relationship(back_populates="source_messages")


class OrderDelivery(Base, IntPK, TimestampMixin):
    """Delivery of one order into one work group."""

    __tablename__ = "order_deliveries"
    __table_args__ = (UniqueConstraint("order_id", "work_group_id"),)

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    work_group_id: Mapped[int] = mapped_column(
        ForeignKey("work_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32), default=DeliveryStatus.PENDING, nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped[Order] = relationship(back_populates="deliveries")
    messages: Mapped[list["OrderDeliveryMessage"]] = relationship(
        back_populates="delivery", cascade="all, delete-orphan", lazy="selectin"
    )


class OrderDeliveryMessage(Base, IntPK, TimestampMixin):
    """Maps every Telegram message the bot created back to its order.

    Order identification is done through ``(chat_id, message_id)`` -- never by
    parsing the ``orderNN`` text out of a message body.
    """

    __tablename__ = "order_delivery_messages"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id"),
        Index("ix_order_delivery_messages_order_primary", "order_id", "is_primary"),
    )

    delivery_id: Mapped[int] = mapped_column(
        ForeignKey("order_deliveries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    delivery: Mapped[OrderDelivery] = relationship(back_populates="messages")


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
class StatusRule(Base, IntPK, TimestampMixin):
    """One rule set per result status (SUCCESS / FAILED)."""

    __tablename__ = "status_rules"

    status: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    mode: Mapped[str] = mapped_column(String(8), default=RuleMode.ANY, nullable=False)

    signals: Mapped[list["RuleSignal"]] = relationship(
        back_populates="rule", cascade="all, delete-orphan", lazy="selectin"
    )
    text_patterns: Mapped[list["RuleTextPattern"]] = relationship(
        back_populates="rule", cascade="all, delete-orphan", lazy="selectin"
    )
    reactions: Mapped[list["RuleReaction"]] = relationship(
        back_populates="rule", cascade="all, delete-orphan", lazy="selectin"
    )


class RuleSignal(Base, IntPK, TimestampMixin):
    __tablename__ = "rule_signals"
    __table_args__ = (UniqueConstraint("rule_id", "signal_key"),)

    rule_id: Mapped[int] = mapped_column(
        ForeignKey("status_rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    signal_key: Mapped[str] = mapped_column(String(48), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    rule: Mapped[StatusRule] = relationship(back_populates="signals")


class RuleTextPattern(Base, IntPK, TimestampMixin):
    __tablename__ = "rule_text_patterns"

    rule_id: Mapped[int] = mapped_column(
        ForeignKey("status_rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    match_mode: Mapped[str] = mapped_column(String(16), default=MatchMode.CONTAINS, nullable=False)
    case_sensitive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    rule: Mapped[StatusRule] = relationship(back_populates="text_patterns")


class RuleReaction(Base, IntPK, TimestampMixin):
    __tablename__ = "rule_reactions"
    __table_args__ = (UniqueConstraint("rule_id", "emoji"),)

    rule_id: Mapped[int] = mapped_column(
        ForeignKey("status_rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    emoji: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    rule: Mapped[StatusRule] = relationship(back_populates="reactions")


class OrderSignal(Base, IntPK, TimestampMixin):
    """A persisted signal observed for one order and one candidate status.

    Signals survive restarts, so a rule in ``ALL`` mode can be completed by
    events that arrive minutes apart (or across a redeploy).
    """

    __tablename__ = "order_signals"
    __table_args__ = (UniqueConstraint("order_id", "rule_status", "signal_key"),)

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_status: Mapped[str] = mapped_column(String(32), nullable=False)
    signal_key: Mapped[str] = mapped_column(String(48), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger)
    trigger_type: Mapped[str | None] = mapped_column(String(32))
    trigger_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    trigger_message_id: Mapped[int | None] = mapped_column(BigInteger)
    detail: Mapped[dict | None] = mapped_column(JSONB)

    order: Mapped[Order] = relationship(back_populates="signals")


# ---------------------------------------------------------------------------
# Result routing
# ---------------------------------------------------------------------------
class ResultDestination(Base, IntPK, TimestampMixin):
    __tablename__ = "result_destinations"
    __table_args__ = (UniqueConstraint("status", "chat_id"),)

    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str | None] = mapped_column(String(256))
    username: Mapped[str | None] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: Required destinations gate the acknowledgement under the default policy.
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ResultDispatch(Base, IntPK, TimestampMixin):
    """Outbox row: exactly one per (order, destination)."""

    __tablename__ = "result_dispatches"
    __table_args__ = (UniqueConstraint("order_id", "destination_id"),)

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    destination_id: Mapped[int] = mapped_column(
        ForeignKey("result_destinations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default=DispatchStatus.PENDING, nullable=False, index=True
    )
    order_status: Mapped[str] = mapped_column(String(32), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sent_message_id: Mapped[int | None] = mapped_column(BigInteger)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped[Order] = relationship(back_populates="dispatches")
    destination: Mapped[ResultDestination] = relationship(lazy="selectin")


# ---------------------------------------------------------------------------
# Acknowledgements
# ---------------------------------------------------------------------------
class AcknowledgementConfig(Base, IntPK, TimestampMixin):
    __tablename__ = "acknowledgement_configs"

    status: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reaction_type: Mapped[str] = mapped_column(
        String(32), default=ReactionType.EMOJI, nullable=False
    )
    reaction_value: Mapped[str | None] = mapped_column(String(64))
    target_mode: Mapped[str] = mapped_column(
        String(32), default=AcknowledgementTargetMode.SMART, nullable=False
    )
    dispatch_policy: Mapped[str] = mapped_column(
        String(48), default=DispatchPolicy.ALL_REQUIRED_DESTINATIONS, nullable=False
    )
    retry_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_retry_count: Mapped[int] = mapped_column(Integer, default=3, nullable=False)


class AcknowledgementEvent(Base, IntPK, TimestampMixin):
    __tablename__ = "acknowledgement_events"

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_status: Mapped[str] = mapped_column(String(32), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    reaction: Mapped[str | None] = mapped_column(String(64))
    target_mode: Mapped[str | None] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)


class StatusEvent(Base, IntPK, TimestampMixin):
    __tablename__ = "status_events"

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger)
    trigger_type: Mapped[str | None] = mapped_column(String(32))
    trigger_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    trigger_message_id: Mapped[int | None] = mapped_column(BigInteger)


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
class Setting(Base, IntPK, TimestampMixin):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    value: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base, IntPK):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_created_at_id", "created_at", "id"),)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(16), default="INFO", nullable=False)
    order_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    message: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict | None] = mapped_column(JSONB)


class ProcessedUpdate(Base, IntPK):
    """Idempotency ledger for raw Telegram updates.

    Telegram re-delivers an update if the bot dies before confirming the
    offset; this table makes such a redelivery a no-op.
    """

    __tablename__ = "processed_updates"
    __table_args__ = (UniqueConstraint("update_key"),)

    update_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class OrderAttachment(Base, IntPK, TimestampMixin):
    """Media an operator attached while working the order.

    Only Telegram's ``file_id`` is kept -- a short reference string, never the
    file itself -- so the result destination can be sent the operator's photos
    without the database growing with binary data.
    """

    __tablename__ = "order_attachments"
    __table_args__ = (UniqueConstraint("chat_id", "message_id"),)

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_id: Mapped[str] = mapped_column(Text, nullable=False)
    caption: Mapped[str | None] = mapped_column(Text)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    order: Mapped["Order"] = relationship(back_populates="attachments")


class SourceReactionConfig(Base, IntPK, TimestampMixin):
    """Which reaction the bot puts on the SOURCE message at each stage."""

    __tablename__ = "source_reaction_configs"

    stage: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reaction_value: Mapped[str | None] = mapped_column(String(64))


class ProgressReaction(Base, IntPK, TimestampMixin):
    """Operator reactions in the work group that mean "I am working on this"."""

    __tablename__ = "progress_reactions"
    __table_args__ = (UniqueConstraint("emoji"),)

    emoji: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ResultConfig(Base, IntPK, TimestampMixin):
    """Per-status behaviour when a finalised order is published."""

    __tablename__ = "result_configs"

    status: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)

    #: Text appended to the order in the result destination.
    append_text_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    append_text: Mapped[str | None] = mapped_column(Text)

    #: WooCommerce order-status update.
    woo_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    woo_status: Mapped[str | None] = mapped_column(String(64))
    woo_note_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    woo_note: Mapped[str | None] = mapped_column(Text)


class WooCommerceCall(Base, IntPK, TimestampMixin):
    """Outbox row for the WooCommerce update: exactly one per order."""

    __tablename__ = "woocommerce_calls"
    __table_args__ = (UniqueConstraint("order_id"),)

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_status: Mapped[str] = mapped_column(String(32), nullable=False)
    store_order_number: Mapped[str] = mapped_column(String(32), nullable=False)
    target_status: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(32), default=DispatchStatus.PENDING, nullable=False, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RejectedMessage(Base, IntPK):
    """A source post refused because its order number was missing or wrong.

    The text is kept even though the original message is deleted, so an admin
    can still see what was sent.
    """

    __tablename__ = "rejected_messages"
    __table_args__ = (UniqueConstraint("chat_id", "message_id"),)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    author_user_id: Mapped[int | None] = mapped_column(BigInteger)
    author_name: Mapped[str | None] = mapped_column(String(256))
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class NotificationThrottle(Base, IntPK):
    """Spam protection for admin notifications."""

    __tablename__ = "notification_throttle"
    __table_args__ = (UniqueConstraint("notification_key"),)

    notification_key: Mapped[str] = mapped_column(String(256), nullable=False)
    last_sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
