"""Phase 8 불확실한 슛 위치와 결과 보존

Revision ID: 20261029_0004
Revises: 20261028_0003
Create Date: 2026-10-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261029_0004"
down_revision: str | None = "20261028_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("shots") as batch_op:
        batch_op.alter_column("court_x", existing_type=sa.Float(), nullable=True)
        batch_op.alter_column("court_y", existing_type=sa.Float(), nullable=True)
        batch_op.alter_column("zone_code", existing_type=sa.String(40), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("shots") as batch_op:
        batch_op.alter_column("zone_code", existing_type=sa.String(40), nullable=False)
        batch_op.alter_column("court_y", existing_type=sa.Float(), nullable=False)
        batch_op.alter_column("court_x", existing_type=sa.Float(), nullable=False)
