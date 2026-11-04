"""Store file sizes above 2 GiB without PostgreSQL integer overflow."""
from alembic import op
import sqlalchemy as sa

revision = "20261104_0008"
down_revision = "20261104_0007"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("analyses") as batch:
        batch.alter_column("file_size", existing_type=sa.Integer(), type_=sa.BigInteger(), existing_nullable=False)


def downgrade():
    # The DB rejects narrowing while out-of-range values exist, preserving data.
    with op.batch_alter_table("analyses") as batch:
        batch.alter_column("file_size", existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=False)
