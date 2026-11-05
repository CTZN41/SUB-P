"""Persist opt-in Kakao notifications independently of analysis processing."""
from alembic import op
import sqlalchemy as sa

revision = '20261105_0009'
down_revision = '20261104_0008'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('analysis_notifications',
        sa.Column('analysis_id', sa.String(36), sa.ForeignKey('analyses.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('owner_id', sa.String(36), nullable=False),
        sa.Column('phone', sa.String(16)),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('consent_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('claim_token', sa.String(36)),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('provider_message_id', sa.String(100)),
        sa.Column('error_code', sa.String(60)),
        sa.Column('accepted_at', sa.DateTime(timezone=True)))
    op.create_index('ix_analysis_notifications_status', 'analysis_notifications', ['status'])
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE analysis_notifications ENABLE ROW LEVEL SECURITY')


def downgrade():
    op.drop_table('analysis_notifications')
