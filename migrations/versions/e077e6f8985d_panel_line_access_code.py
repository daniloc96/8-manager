"""panel line access code

Revision ID: e077e6f8985d
Revises: fe398d565583
Create Date: 2026-11-02 19:27:44.863018

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e077e6f8985d'
down_revision: str | None = 'fe398d565583'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("panel_lines", schema=None) as batch_op:
        batch_op.add_column(sa.Column("access_code", sa.String(length=160), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("panel_lines", schema=None) as batch_op:
        batch_op.drop_column("access_code")
