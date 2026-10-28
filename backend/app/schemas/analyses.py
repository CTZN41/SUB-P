from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

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
