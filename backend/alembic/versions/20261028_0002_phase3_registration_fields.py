"""Phase 3 영상 등록 확인 필드

Revision ID: 20261028_0002
Revises: 20261028_0001
Create Date: 2026-10-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261028_0002"
down_revision: str | None = "20261028_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("analyses") as batch_op:
        batch_op.add_column(
            sa.Column("contains_others", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("warnings_accepted", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("analyses") as batch_op:
        batch_op.drop_column("warnings_accepted")
        batch_op.drop_column("contains_others")
