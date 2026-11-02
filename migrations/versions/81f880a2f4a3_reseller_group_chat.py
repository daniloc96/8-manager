"""reseller group chat

Revision ID: 81f880a2f4a3
Revises: 1b68e6a3f7e5
Create Date: 2026-11-02 22:30:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '81f880a2f4a3'
down_revision: str | None = '1b68e6a3f7e5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("resellers", schema=None) as batch_op:
        batch_op.add_column(sa.Column("group_chat_id", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("group_title", sa.String(length=200), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_resellers_group_chat_id"), ["group_chat_id"], unique=True
        )


def downgrade() -> None:
    with op.batch_alter_table("resellers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_resellers_group_chat_id"))
        batch_op.drop_column("group_title")
        batch_op.drop_column("group_chat_id")
