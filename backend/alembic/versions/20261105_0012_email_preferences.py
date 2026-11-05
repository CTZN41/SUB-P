"""Persist account-level opt-in to result/report emails; existing users opt out."""
from alembic import op
import sqlalchemy as sa

revision = '20261105_0012'
down_revision = '20261105_0011'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('user_email_preferences',
        sa.Column('user_id', sa.String(36), primary_key=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE user_email_preferences ENABLE ROW LEVEL SECURITY')


def downgrade():
    op.drop_table('user_email_preferences')
