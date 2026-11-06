"""Durable single-email deliveries for a selected set of analyses."""
from alembic import op
import sqlalchemy as sa

revision = '20261106_0013'
down_revision = '20261105_0012'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('report_email_batches',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('owner_id', sa.String(36), nullable=False),
        sa.Column('analysis_ids', sa.JSON(), nullable=False),
        sa.Column('selection_key', sa.String(64), nullable=False),
        sa.Column('recipient_email', sa.String(254), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('claim_token', sa.String(36)),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('provider_message_id', sa.String(100)),
        sa.Column('error_code', sa.String(60)),
        sa.Column('accepted_at', sa.DateTime(timezone=True)))
    for name in ('owner_id', 'selection_key', 'status'):
        op.create_index('ix_report_email_batches_' + name, 'report_email_batches', [name])
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE report_email_batches ENABLE ROW LEVEL SECURITY')


def downgrade():
    op.drop_table('report_email_batches')
