"""Deduplicate user-requested report sends, including explicit resends."""
from alembic import op
import sqlalchemy as sa

revision = '20261105_0011'
down_revision = '20261105_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('analysis_email_requests',
        sa.Column('request_id', sa.String(36), primary_key=True),
        sa.Column('analysis_id', sa.String(36), sa.ForeignKey('analyses.id', ondelete='CASCADE'), nullable=False),
        sa.Column('owner_id', sa.String(36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_analysis_email_requests_analysis_id', 'analysis_email_requests', ['analysis_id'])
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE analysis_email_requests ENABLE ROW LEVEL SECURITY')


def downgrade():
    op.drop_table('analysis_email_requests')
