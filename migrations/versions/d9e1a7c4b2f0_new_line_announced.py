"""new_line_requests announced_at

Revision ID: d9e1a7c4b2f0
Revises: c3f4d2a19b60
Create Date: 2026-11-04 20:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9e1a7c4b2f0"
down_revision: str | Sequence[str] | None = "c3f4d2a19b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("new_line_requests", schema=None) as batch_op:
        batch_op.add_column(sa.Column("announced_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("new_line_requests", schema=None) as batch_op:
        batch_op.drop_column("announced_at")
