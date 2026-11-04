"""ShotMap AI의 로컬 저장 모델."""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StringEnum(str, Enum):
    """DB와 API에서 같은 문자열을 사용하는 열거형."""


class PlayType(StringEnum):
    SOLO = "solo"
    ONE_ON_ONE = "one_on_one"
    TWO_ON_TWO = "two_on_two"
    THREE_ON_THREE = "three_on_three"


class AnalysisStatus(StringEnum):
    REGISTERING = "registering"
    SETUP_REQUIRED = "setup_required"
    QUEUED = "queued"
    ANALYZING = "analyzing"
    REVIEW_REQUIRED = "review_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class ShotResult(StringEnum):
    MADE = "made"
    MISSED = "missed"
    NEEDS_REVIEW = "needs_review"


class ShotType(StringEnum):
    CATCH_AND_SHOOT = "catch_and_shoot"
    SET_SHOT = "set_shot"
    DRIBBLE_PULL_UP = "dribble_pull_up"
    SIDE_STEP = "side_step"
    LAYUP = "layup"
    DUNK = "dunk"
    FADEAWAY = "fadeaway"
    STEP_BACK = "step_back"
    TURNAROUND = "turnaround"
    HOOK_SHOT = "hook_shot"
    OTHER = "other"
    NEEDS_REVIEW = "needs_review"


class ShotStatus(StringEnum):
    AUTO = "auto"
    NEEDS_REVIEW = "needs_review"
    USER_CONFIRMED = "user_confirmed"


class ShotSource(StringEnum):
    AUTO = "auto"
    USER = "user"


class CalibrationSource(StringEnum):
    AUTO = "auto"
    USER = "user"


class JobStatus(StringEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


def enum_column(enum_type: type[StringEnum], *, default: StringEnum | None = None):
    return mapped_column(
        SqlEnum(
            enum_type,
            native_enum=False,
            length=40,
            validate_strings=True,
            values_callable=lambda members: [member.value for member in members],
        ),
        default=default,
        nullable=False,
    )


class UserProfile(Base):
    __tablename__ = 'user_profiles'
    __table_args__ = (CheckConstraint('length(nickname) BETWEEN 2 AND 20', name='ck_user_profiles_nickname_length'),)

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    nickname: Mapped[str] = mapped_column(String(20), nullable=False)
    nickname_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class Analysis(Base):
    __tablename__ = "analyses"
    __table_args__ = (
        CheckConstraint("length(trim(title)) BETWEEN 1 AND 100", name="ck_analyses_title_length"),
        CheckConstraint("file_size >= 0", name="ck_analyses_file_size"),
        CheckConstraint("duration_ms >= 0", name="ck_analyses_duration"),
        CheckConstraint("processed_ms >= 0", name="ck_analyses_processed"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str | None] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    play_type: Mapped[PlayType] = enum_column(PlayType)
    recorded_date: Mapped[date | None] = mapped_column(Date)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    status: Mapped[AnalysisStatus] = enum_column(
        AnalysisStatus, default=AnalysisStatus.REGISTERING
    )
    current_stage: Mapped[str | None] = mapped_column(String(80))
    processed_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    contains_others: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consent_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ai_notice_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    warnings_accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    failure_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    calibrations: Mapped[list["CourtCalibration"]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )
    players: Mapped[list["Player"]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )
    shots: Mapped[list["Shot"]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["AnalysisJob"]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )


class CourtCalibration(Base):
    __tablename__ = "court_calibrations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    frame_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    image_points: Mapped[list[list[float]]] = mapped_column(JSON, nullable=False)
    court_points: Mapped[list[list[float]]] = mapped_column(JSON, nullable=False)
    hoop_point: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    three_point_line: Mapped[list[list[float]] | None] = mapped_column(JSON, nullable=True)
    transform: Mapped[list[list[float]]] = mapped_column(JSON, nullable=False)
    source: Mapped[CalibrationSource] = enum_column(CalibrationSource)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    analysis: Mapped[Analysis] = relationship(back_populates="calibrations")


class Player(Base):
    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    jersey_number: Mapped[str | None] = mapped_column(String(20))
    is_temporary_name: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    initial_time_ms: Mapped[int | None] = mapped_column(Integer)
    initial_box: Mapped[list[float] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    analysis: Mapped[Analysis] = relationship(back_populates="players")
    shots: Mapped[list["Shot"]] = relationship(back_populates="player")


class Shot(Base):
    __tablename__ = "shots"
    __table_args__ = (
        CheckConstraint("candidate_time_ms >= 0", name="ck_shots_candidate_time"),
        CheckConstraint("sequence_start_ms >= 0", name="ck_shots_sequence_start"),
        CheckConstraint("sequence_end_ms >= sequence_start_ms", name="ck_shots_sequence_order"),
        CheckConstraint("court_x BETWEEN 0 AND 50", name="ck_shots_court_x"),
        CheckConstraint("court_y BETWEEN 0 AND 47", name="ck_shots_court_y"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    player_id: Mapped[str] = mapped_column(
        ForeignKey("players.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    candidate_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence_start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence_end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    court_x: Mapped[float | None] = mapped_column(Float)
    court_y: Mapped[float | None] = mapped_column(Float)
    zone_code: Mapped[str | None] = mapped_column(String(40), index=True)
    result: Mapped[ShotResult] = enum_column(ShotResult)
    shot_type: Mapped[ShotType] = enum_column(ShotType)
    status: Mapped[ShotStatus] = enum_column(ShotStatus, default=ShotStatus.AUTO)
    review_reason: Mapped[str | None] = mapped_column(Text)
    source: Mapped[ShotSource] = enum_column(ShotSource, default=ShotSource.AUTO)
    confidence_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    analysis: Mapped[Analysis] = relationship(back_populates="shots")
    player: Mapped[Player] = relationship(back_populates="shots")


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[JobStatus] = enum_column(JobStatus, default=JobStatus.QUEUED)
    stage: Mapped[str | None] = mapped_column(String(80))
    processed_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_simulation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    analysis: Mapped[Analysis] = relationship(back_populates="jobs")
