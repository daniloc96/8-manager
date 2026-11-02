"""review exceptions

Revision ID: 1b68e6a3f7e5
Revises: e077e6f8985d
Create Date: 2026-11-02 21:40:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '1b68e6a3f7e5'
down_revision: str | None = 'e077e6f8985d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_exceptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("line_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("review_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["line_id"],
            ["lines.id"],
            name="fk_review_exceptions_line_id_lines",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_exceptions"),
    )
    with op.batch_alter_table("review_exceptions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_review_exceptions_line_id"), ["line_id"], unique=True
        )


def downgrade() -> None:
    with op.batch_alter_table("review_exceptions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_review_exceptions_line_id"))
    op.drop_table("review_exceptions")
