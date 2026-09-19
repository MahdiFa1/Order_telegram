"""Scheduled retries for the WooCommerce store update.

A store call that failed used to stay failed until an admin noticed the
alert and pressed the retry button. These four columns let a background
worker pick the row up again on a backoff schedule, tell a momentary
failure apart from a permanent one, and alert the admins once rather than
once per attempt.

``next_attempt_at`` is nullable on purpose: NULL means "due now", which is
exactly what every existing row means.

Revision ID: 0004_store_retry
Revises: 0003_topics
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_store_retry"
down_revision: Union[str, None] = "0003_topics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "woocommerce_calls"


def upgrade() -> None:
    op.add_column(
        _TABLE, sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        _TABLE, sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "permanent", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column("alerted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # The models carry no server default; drop it once the existing rows are
    # filled so the schema keeps matching them.
    op.alter_column(_TABLE, "permanent", server_default=None)
    op.alter_column(_TABLE, "alerted", server_default=None)

    op.create_index(
        op.f("ix_woocommerce_calls_next_attempt_at"),
        _TABLE,
        ["next_attempt_at"],
        unique=False,
    )

    # Rows that already failed were alerted by the old code path; marking
    # them keeps the upgrade from re-announcing history on the first tick.
    op.execute(
        sa.text(
            f"UPDATE {_TABLE} SET alerted = true WHERE status = 'FAILED'"  # noqa: S608
        )
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_woocommerce_calls_next_attempt_at"), table_name=_TABLE)
    op.drop_column(_TABLE, "alerted")
    op.drop_column(_TABLE, "permanent")
    op.drop_column(_TABLE, "last_attempt_at")
    op.drop_column(_TABLE, "next_attempt_at")
