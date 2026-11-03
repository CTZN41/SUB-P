"""Prepare ownership without assigning or removing existing records."""
from alembic import op
import sqlalchemy as sa

revision = '20261103_0006'
down_revision = '20261103_0005'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('analyses', sa.Column('owner_id', sa.String(36), nullable=True))
    op.create_index('ix_analyses_owner_id', 'analyses', ['owner_id'])
    if op.get_bind().dialect.name == 'postgresql':
        # Browser clients use Auth only; all application data goes through the
        # owner-checking FastAPI API. No permissive Data API policy is created.
        for table in ('analyses', 'court_calibrations', 'players', 'shots', 'analysis_jobs'):
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade():
    # Preserve RLS rather than re-opening data access on a schema downgrade.
    op.drop_index('ix_analyses_owner_id', table_name='analyses')
    op.drop_column('analyses', 'owner_id')
