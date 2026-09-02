"""Forum topics everywhere, per-source result destinations.

Adds ``topic_id`` to source channels, work groups and result destinations so
every leg of the pipeline can read from and write to a forum topic, and binds
a result destination to a single source channel when the admin wants that
source's results to go somewhere of their own.

The three ``topic_id`` columns are NOT NULL, so they are added with a server
default of 0 -- "the chat itself" -- which is what every existing row means.
The default is dropped afterwards to keep the schema matching the models.

Revision ID: 0003_topics
Revises: 0002_extensions
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_topics"
down_revision: Union[str, None] = "0002_extensions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TOPIC_TABLES = ("source_channels", "work_groups", "result_destinations")


def upgrade() -> None:
    for table in _TOPIC_TABLES:
        op.add_column(
            table,
            sa.Column("topic_id", sa.Integer(), nullable=False, server_default="0"),
        )
        op.alter_column(table, "topic_id", server_default=None)

    # --- source channels: one row per (chat, topic) ----------------------
    op.drop_constraint(
        op.f("uq_source_channels_chat_id"), "source_channels", type_="unique"
    )
    op.create_index(
        op.f("ix_source_channels_chat_id"), "source_channels", ["chat_id"], unique=False
    )
    op.create_unique_constraint(
        op.f("uq_source_channels_chat_id_topic_id"),
        "source_channels",
        ["chat_id", "topic_id"],
    )

    # --- work groups: same ------------------------------------------------
    op.drop_constraint(op.f("uq_work_groups_chat_id"), "work_groups", type_="unique")
    op.create_index(
        op.f("ix_work_groups_chat_id"), "work_groups", ["chat_id"], unique=False
    )
    op.create_unique_constraint(
        op.f("uq_work_groups_chat_id_topic_id"), "work_groups", ["chat_id", "topic_id"]
    )

    # --- result destinations: optional binding to one source -------------
    op.add_column(
        "result_destinations",
        sa.Column("source_channel_id", sa.BigInteger(), nullable=True),
    )
    op.drop_constraint(
        op.f("uq_result_destinations_status_chat_id"),
        "result_destinations",
        type_="unique",
    )
    op.create_index(
        op.f("ix_result_destinations_source_channel_id"),
        "result_destinations",
        ["source_channel_id"],
        unique=False,
    )
    # Two partial indexes: SQL treats NULLs as distinct, so one constraint
    # would let duplicate shared rows through.
    op.create_index(
        "uq_result_destination_shared",
        "result_destinations",
        ["status", "chat_id", "topic_id"],
        unique=True,
        postgresql_where=sa.text("source_channel_id IS NULL"),
    )
    op.create_index(
        "uq_result_destination_per_source",
        "result_destinations",
        ["status", "chat_id", "topic_id", "source_channel_id"],
        unique=True,
        postgresql_where=sa.text("source_channel_id IS NOT NULL"),
    )
    op.create_foreign_key(
        op.f("fk_result_destinations_source_channel_id_source_channels"),
        "result_destinations",
        "source_channels",
        ["source_channel_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_result_destinations_source_channel_id_source_channels"),
        "result_destinations",
        type_="foreignkey",
    )
    op.drop_index(
        "uq_result_destination_per_source",
        table_name="result_destinations",
        postgresql_where=sa.text("source_channel_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_result_destination_shared",
        table_name="result_destinations",
        postgresql_where=sa.text("source_channel_id IS NULL"),
    )
    op.drop_index(
        op.f("ix_result_destinations_source_channel_id"),
        table_name="result_destinations",
    )
    op.create_unique_constraint(
        op.f("uq_result_destinations_status_chat_id"),
        "result_destinations",
        ["status", "chat_id"],
    )
    op.drop_column("result_destinations", "source_channel_id")

    op.drop_constraint(
        op.f("uq_work_groups_chat_id_topic_id"), "work_groups", type_="unique"
    )
    op.drop_index(op.f("ix_work_groups_chat_id"), table_name="work_groups")
    op.create_unique_constraint(
        op.f("uq_work_groups_chat_id"), "work_groups", ["chat_id"]
    )

    op.drop_constraint(
        op.f("uq_source_channels_chat_id_topic_id"), "source_channels", type_="unique"
    )
    op.drop_index(op.f("ix_source_channels_chat_id"), table_name="source_channels")
    op.create_unique_constraint(
        op.f("uq_source_channels_chat_id"), "source_channels", ["chat_id"]
    )

    for table in _TOPIC_TABLES:
        op.drop_column(table, "topic_id")
