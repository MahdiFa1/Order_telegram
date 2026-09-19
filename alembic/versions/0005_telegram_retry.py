"""Scheduled retries for the Telegram result dispatch and its acknowledgement.

The same four fields the store update gained in 0004, for the two Telegram
steps that can also fail after an order is already finished: when the next
automatic attempt is due, when the last one ran, whether a repeat is
pointless, and whether the admins have already been told.

Both ``*_next_attempt_at`` columns are nullable on purpose: NULL means "due
now", which is exactly what every existing row means.

Revision ID: 0005_telegram_retry
Revises: 0004_store_retry
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_telegram_retry"
down_revision: Union[str, None] = "0004_store_retry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- result dispatches ------------------------------------------------
    op.add_column(
        "result_dispatches",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "result_dispatches",
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "result_dispatches",
        sa.Column("permanent", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "result_dispatches",
        sa.Column("alerted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("result_dispatches", "permanent", server_default=None)
    op.alter_column("result_dispatches", "alerted", server_default=None)
    op.create_index(
        op.f("ix_result_dispatches_next_attempt_at"),
        "result_dispatches",
        ["next_attempt_at"],
        unique=False,
    )

    # --- the acknowledgement, which lives on the order --------------------
    op.add_column(
        "orders",
        sa.Column(
            "acknowledgement_next_attempt_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "orders",
        sa.Column(
            "acknowledgement_permanent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "orders",
        sa.Column(
            "acknowledgement_alerted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column("orders", "acknowledgement_permanent", server_default=None)
    op.alter_column("orders", "acknowledgement_alerted", server_default=None)
    op.create_index(
        op.f("ix_orders_acknowledgement_next_attempt_at"),
        "orders",
        ["acknowledgement_next_attempt_at"],
        unique=False,
    )

    # Failures that predate this migration were announced by the old code
    # path; marking them keeps the first tick from re-announcing history.
    op.execute(
        sa.text("UPDATE result_dispatches SET alerted = true WHERE status = 'FAILED'")
    )
    op.execute(
        sa.text(
            "UPDATE orders SET acknowledgement_alerted = true "
            "WHERE acknowledgement_status = 'FAILED'"
        )
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_orders_acknowledgement_next_attempt_at"), table_name="orders"
    )
    op.drop_column("orders", "acknowledgement_alerted")
    op.drop_column("orders", "acknowledgement_permanent")
    op.drop_column("orders", "acknowledgement_next_attempt_at")

    op.drop_index(
        op.f("ix_result_dispatches_next_attempt_at"), table_name="result_dispatches"
    )
    op.drop_column("result_dispatches", "alerted")
    op.drop_column("result_dispatches", "permanent")
    op.drop_column("result_dispatches", "last_attempt_at")
    op.drop_column("result_dispatches", "next_attempt_at")
