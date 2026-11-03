"""Optional manual three-point boundary; preserve existing calibrations."""
from alembic import op
import sqlalchemy as sa
revision = '20261103_0005'
down_revision = '20261029_0004'
branch_labels = None
depends_on = None
def upgrade():
    op.add_column('court_calibrations', sa.Column('three_point_line', sa.JSON(), nullable=True))
def downgrade():
    op.drop_column('court_calibrations', 'three_point_line')
