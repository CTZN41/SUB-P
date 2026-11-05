"""Use verified account email for new notification subscriptions."""
from alembic import op
import sqlalchemy as sa

revision = '20261105_0010'
down_revision = '20261105_0009'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('analysis_notifications', sa.Column('recipient_email', sa.String(254), nullable=True))
    # Phone subscriptions require a new, explicit email opt-in.
    op.execute("UPDATE analysis_notifications SET status='canceled' WHERE status IN ('waiting','preparing')")


def downgrade():
    op.drop_column('analysis_notifications', 'recipient_email')
