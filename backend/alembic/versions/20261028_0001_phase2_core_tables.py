"""Phase 2 핵심 데이터 구조

Revision ID: 20261028_0001
Revises:
Create Date: 2026-10-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261028_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analyses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("play_type", sa.String(40), nullable=False),
        sa.Column("recorded_date", sa.Date()),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("stored_path", sa.String(500), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("fps", sa.Float()),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("current_stage", sa.String(80)),
        sa.Column("processed_ms", sa.Integer(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("consent_confirmed", sa.Boolean(), nullable=False),
        sa.Column("ai_notice_confirmed", sa.Boolean(), nullable=False),
        sa.Column("failure_code", sa.String(80)),
        sa.Column("failure_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("length(trim(title)) BETWEEN 1 AND 100", name="ck_analyses_title_length"),
        sa.CheckConstraint("file_size >= 0", name="ck_analyses_file_size"),
        sa.CheckConstraint("duration_ms >= 0", name="ck_analyses_duration"),
        sa.CheckConstraint("processed_ms >= 0", name="ck_analyses_processed"),
    )
    op.create_table(
        "court_calibrations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("analysis_id", sa.String(36), nullable=False),
        sa.Column("frame_time_ms", sa.Integer(), nullable=False),
        sa.Column("image_points", sa.JSON(), nullable=False),
        sa.Column("court_points", sa.JSON(), nullable=False),
        sa.Column("hoop_point", sa.JSON(), nullable=False),
        sa.Column("transform", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_court_calibrations_analysis_id", "court_calibrations", ["analysis_id"])
    op.create_table(
        "players",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("analysis_id", sa.String(36), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("jersey_number", sa.String(20)),
        sa.Column("is_temporary_name", sa.Boolean(), nullable=False),
        sa.Column("initial_time_ms", sa.Integer()),
        sa.Column("initial_box", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_players_analysis_id", "players", ["analysis_id"])
    op.create_table(
        "analysis_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("analysis_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("stage", sa.String(80)),
        sa.Column("processed_ms", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_analysis_jobs_analysis_id", "analysis_jobs", ["analysis_id"])
    op.create_table(
        "shots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("analysis_id", sa.String(36), nullable=False),
        sa.Column("player_id", sa.String(36), nullable=False),
        sa.Column("candidate_time_ms", sa.Integer(), nullable=False),
        sa.Column("sequence_start_ms", sa.Integer(), nullable=False),
        sa.Column("sequence_end_ms", sa.Integer(), nullable=False),
        sa.Column("court_x", sa.Float(), nullable=False),
        sa.Column("court_y", sa.Float(), nullable=False),
        sa.Column("zone_code", sa.String(40), nullable=False),
        sa.Column("result", sa.String(40), nullable=False),
        sa.Column("shot_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("review_reason", sa.Text()),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("confidence_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("candidate_time_ms >= 0", name="ck_shots_candidate_time"),
        sa.CheckConstraint("sequence_start_ms >= 0", name="ck_shots_sequence_start"),
        sa.CheckConstraint("sequence_end_ms >= sequence_start_ms", name="ck_shots_sequence_order"),
        sa.CheckConstraint("court_x BETWEEN 0 AND 50", name="ck_shots_court_x"),
        sa.CheckConstraint("court_y BETWEEN 0 AND 47", name="ck_shots_court_y"),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_shots_analysis_id", "shots", ["analysis_id"])
    op.create_index("ix_shots_player_id", "shots", ["player_id"])
    op.create_index("ix_shots_zone_code", "shots", ["zone_code"])


def downgrade() -> None:
    op.drop_index("ix_shots_zone_code", table_name="shots")
    op.drop_index("ix_shots_player_id", table_name="shots")
    op.drop_index("ix_shots_analysis_id", table_name="shots")
    op.drop_table("shots")
    op.drop_index("ix_analysis_jobs_analysis_id", table_name="analysis_jobs")
    op.drop_table("analysis_jobs")
    op.drop_index("ix_players_analysis_id", table_name="players")
    op.drop_table("players")
    op.drop_index("ix_court_calibrations_analysis_id", table_name="court_calibrations")
    op.drop_table("court_calibrations")
    op.drop_table("analyses")
