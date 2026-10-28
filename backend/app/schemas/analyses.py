from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import AnalysisStatus, PlayType


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
