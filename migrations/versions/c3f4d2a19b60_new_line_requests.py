"""new_line_requests

Revision ID: c3f4d2a19b60
Revises: a7c53d9e21b4
Create Date: 2026-11-04 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3f4d2a19b60"
down_revision: str | Sequence[str] | None = "a7c53d9e21b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "new_line_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reseller_id", sa.Integer(), nullable=False),
        sa.Column("requested_by_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column(
            "service",
            sa.Enum(
                "live",
                "live_vod",
                name="service",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                "expired",
                name="requeststatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("origin_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("origin_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("line_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "months > 0", name=op.f("ck_new_line_requests_months_positive")
        ),
        sa.ForeignKeyConstraint(
            ["reseller_id"],
            ["resellers.id"],
            name=op.f("fk_new_line_requests_reseller_id_resellers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["line_id"],
            ["lines.id"],
            name=op.f("fk_new_line_requests_line_id_lines"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_new_line_requests")),
    )
    with op.batch_alter_table("new_line_requests", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_new_line_requests_reseller_id"), ["reseller_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("new_line_requests", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_new_line_requests_reseller_id"))

    op.drop_table("new_line_requests")
