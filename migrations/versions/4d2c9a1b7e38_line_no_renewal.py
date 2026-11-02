"""line no renewal flag

Revision ID: 4d2c9a1b7e38
Revises: 81f880a2f4a3
Create Date: 2026-11-02 23:10:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '4d2c9a1b7e38'
down_revision: str | None = '81f880a2f4a3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("lines", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("no_renewal", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("no_renewal_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("lines", schema=None) as batch_op:
        batch_op.drop_column("no_renewal_at")
        batch_op.drop_column("no_renewal")
