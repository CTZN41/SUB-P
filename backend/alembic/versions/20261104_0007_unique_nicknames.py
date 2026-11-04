"""Claim existing names oldest-account-first without changing Auth metadata."""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

from app.services.nickname_policy import nickname_from_metadata, nickname_key

revision = '20261104_0007'
down_revision = '20261103_0006'
branch_labels = None
depends_on = None


def backfill_profiles(connection):
    claimed = set()
    rows = connection.execute(sa.text('SELECT id, raw_user_meta_data FROM auth.users ORDER BY created_at ASC NULLS LAST, id ASC')).mappings()
    for row in rows:
        nickname = nickname_from_metadata(row['raw_user_meta_data'])
        key = nickname_key(nickname)
        if not key or key in claimed:
            continue
        connection.execute(sa.text('INSERT INTO user_profiles (user_id, nickname, nickname_key, created_at) VALUES (:id, :nickname, :key, :created)'),
                           {'id': str(row['id']), 'nickname': nickname, 'key': key, 'created': datetime.now(timezone.utc)})
        claimed.add(key)


def upgrade():
    op.create_table('user_profiles',
        sa.Column('user_id', sa.String(36), primary_key=True),
        sa.Column('nickname', sa.String(20), nullable=False),
        sa.Column('nickname_key', sa.String(160), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('length(nickname) BETWEEN 2 AND 20', name='ck_user_profiles_nickname_length'),
        sa.UniqueConstraint('nickname_key'))
    connection = op.get_bind()
    if connection.dialect.name == 'postgresql':
        connection.execute(sa.text('LOCK TABLE auth.users IN SHARE MODE'))
        backfill_profiles(connection)
        op.execute('ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY')


def downgrade():
    op.drop_table('user_profiles')
