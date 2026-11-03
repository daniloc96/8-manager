"""review_issue

Revision ID: a7c53d9e21b4
Revises: 4d2c9a1b7e38
Create Date: 2026-11-03 13:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c53d9e21b4"
down_revision: str | Sequence[str] | None = "4d2c9a1b7e38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("review_exceptions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("issue", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("local_expires", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("panel_expires", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("review_exceptions", schema=None) as batch_op:
        batch_op.drop_column("panel_expires")
        batch_op.drop_column("local_expires")
        batch_op.drop_column("issue")
