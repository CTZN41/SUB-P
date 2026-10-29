from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import (
    AnalysisStatus,
    JobStatus,
    PlayType,
    ShotResult,
    ShotSource,
    ShotStatus,
    ShotType,
)


class AnalysisRegistrationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    play_type: PlayType
    recorded_date: date | None
    original_name: str
    file_size: int
    duration_ms: int
    width: int | None
    height: int | None
    fps: float | None
    status: AnalysisStatus
    warnings: list[str]
    contains_others: bool
    consent_confirmed: bool
    ai_notice_confirmed: bool
    warnings_accepted: bool
    created_at: datetime


class WarningAcceptanceResponse(BaseModel):
    id: str
    warnings_accepted: bool


class CourtCalibrationRequest(BaseModel):
    frame_time_ms: int = Field(ge=0)
    image_points: list[list[float]]
    court_points: list[list[float]]
    hoop_point: list[float]


class CourtCalibrationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    analysis_id: str
    frame_time_ms: int
    image_points: list[list[float]]
    court_points: list[list[float]]
    hoop_point: list[float]
    transform: list[list[float]]
    confirmed_at: datetime | None


class CourtLandmarkSuggestionResponse(BaseModel):
    usable: bool
    method: str
    confidence: float
    image_points: list[list[float]]
    court_points: list[list[float]]
    hoop_point: list[float] | None
    landmark_names: list[str]
    warnings: list[str]


class PlayerCreateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    jersey_number: str | None = Field(default=None, max_length=20)


class PlayerUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    jersey_number: str | None = Field(default=None, max_length=20)
    initial_time_ms: int | None = Field(default=None, ge=0)
    initial_box: list[float] | None = None


class PlayerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    analysis_id: str
    display_name: str
    jersey_number: str | None
    is_temporary_name: bool
    initial_time_ms: int | None
    initial_box: list[float] | None


class PlayerListResponse(BaseModel):
    players: list[PlayerResponse]
    warnings: list[str]
    max_players: int


class AnalysisJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    analysis_id: str
    status: JobStatus
    stage: str | None
    processed_ms: int
    cancel_requested: bool
    is_simulation: bool
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None


class ShotCreateRequest(BaseModel):
    player_id: str
    candidate_time_ms: int = Field(ge=0)
    sequence_start_ms: int = Field(ge=0)
    sequence_end_ms: int = Field(ge=0)
    court_x: float = Field(ge=0, le=50)
    court_y: float = Field(ge=0, le=47)
    result: ShotResult
    shot_type: ShotType


class ShotUpdateRequest(BaseModel):
    player_id: str | None = None
    candidate_time_ms: int | None = Field(default=None, ge=0)
    sequence_start_ms: int | None = Field(default=None, ge=0)
    sequence_end_ms: int | None = Field(default=None, ge=0)
    court_x: float | None = Field(default=None, ge=0, le=50)
    court_y: float | None = Field(default=None, ge=0, le=47)
    result: ShotResult | None = None
    shot_type: ShotType | None = None


class ShotResponse(BaseModel):
    id: str
    analysis_id: str
    player_id: str
    player_name: str
    candidate_time_ms: int
    sequence_start_ms: int
    sequence_end_ms: int
    court_x: float | None
    court_y: float | None
    zone_code: str | None
    zone_label: str | None
    result: ShotResult
    shot_type: ShotType
    status: ShotStatus
    review_reason: str | None
    source: ShotSource
    confidence_data: dict
    created_at: datetime
    updated_at: datetime


class ShotListResponse(BaseModel):
    analysis_id: str
    analysis_status: AnalysisStatus
    duration_ms: int
    fps: float | None
    shots: list[ShotResponse]
    statistics: dict[str, object]
