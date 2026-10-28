from datetime import date
from typing import Annotated

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.dependencies import get_db_session
from app.models import Analysis, CalibrationSource, CourtCalibration, PlayType
from app.schemas.analyses import (
    AnalysisRegistrationResponse,
    CourtCalibrationRequest,
    CourtCalibrationResponse,
    WarningAcceptanceResponse,
)
from app.services.court_calibration import (
    CalibrationError,
    calculate_transform,
    read_video_frame,
    resolve_video_path,
    validate_points,
)
from app.services.video_registration import RegistrationError, register_video


router = APIRouter(prefix="/api/analyses", tags=["analyses"])


def find_analysis(session: Session, analysis_id: str) -> Analysis:
    analysis = session.get(Analysis, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "분석 기록을 찾을 수 없습니다."})
    return analysis


@router.post("", response_model=AnalysisRegistrationResponse, status_code=status.HTTP_201_CREATED)
async def create_analysis(
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
    title: Annotated[str, Form()],
    play_type: Annotated[PlayType, Form()],
    contains_others: Annotated[bool, Form()],
    consent_confirmed: Annotated[bool, Form()],
    ai_notice_confirmed: Annotated[bool, Form()],
    video: Annotated[UploadFile, File()],
    recorded_date: Annotated[date | None, Form()] = None,
    player_name: Annotated[str | None, Form()] = None,
) -> Analysis:
    try:
        return await register_video(
            session,
            request.app.state.settings,
            title=title,
            play_type=play_type,
            recorded_date=recorded_date,
            player_name=player_name,
            contains_others=contains_others,
            consent_confirmed=consent_confirmed,
            ai_notice_confirmed=ai_notice_confirmed,
            upload=video,
        )
    except RegistrationError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": error.message},
        ) from error


@router.get("/{analysis_id}", response_model=AnalysisRegistrationResponse)
def get_analysis(
    analysis_id: str, session: Annotated[Session, Depends(get_db_session)]
) -> Analysis:
    return find_analysis(session, analysis_id)


@router.post("/{analysis_id}/accept-warnings", response_model=WarningAcceptanceResponse)
def accept_warnings(
    analysis_id: str, session: Annotated[Session, Depends(get_db_session)]
) -> WarningAcceptanceResponse:
    analysis = find_analysis(session, analysis_id)
    analysis.warnings_accepted = True
    session.commit()
    return WarningAcceptanceResponse(id=analysis.id, warnings_accepted=True)


@router.get("/{analysis_id}/calibration/frame")
def get_calibration_frame(
    request: Request,
    analysis_id: str,
    session: Annotated[Session, Depends(get_db_session)],
    time_ms: int = 0,
) -> Response:
    analysis = find_analysis(session, analysis_id)
    if time_ms < 0 or time_ms > analysis.duration_ms:
        raise HTTPException(status_code=400, detail={"code": "FRAME_TIME_INVALID", "message": "대표 장면 시간이 영상 범위를 벗어났습니다."})
    try:
        content, _, _ = read_video_frame(
            resolve_video_path(request.app.state.settings.upload_path, analysis.id), time_ms
        )
    except CalibrationError as error:
        raise HTTPException(status_code=400, detail={"code": "FRAME_READ_FAILED", "message": str(error)}) from error
    return Response(content=content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/{analysis_id}/calibration", response_model=CourtCalibrationResponse)
def get_calibration(
    analysis_id: str, session: Annotated[Session, Depends(get_db_session)]
) -> CourtCalibration:
    find_analysis(session, analysis_id)
    calibration = session.scalar(
        select(CourtCalibration).where(CourtCalibration.analysis_id == analysis_id)
    )
    if calibration is None:
        raise HTTPException(status_code=404, detail={"code": "CALIBRATION_NOT_FOUND", "message": "저장된 코트 위치가 없습니다."})
    return calibration


@router.put("/{analysis_id}/calibration", response_model=CourtCalibrationResponse)
def save_calibration(
    request: Request,
    analysis_id: str,
    payload: CourtCalibrationRequest,
    session: Annotated[Session, Depends(get_db_session)],
) -> CourtCalibration:
    analysis = find_analysis(session, analysis_id)
    if payload.frame_time_ms > analysis.duration_ms:
        raise HTTPException(status_code=400, detail={"code": "FRAME_TIME_INVALID", "message": "대표 장면 시간이 영상 범위를 벗어났습니다."})
    try:
        _, width, height = read_video_frame(
            resolve_video_path(request.app.state.settings.upload_path, analysis.id),
            payload.frame_time_ms,
        )
        validate_points(payload.image_points, payload.court_points, payload.hoop_point, width, height)
        transform = calculate_transform(payload.image_points, payload.court_points)
    except CalibrationError as error:
        raise HTTPException(status_code=400, detail={"code": "CALIBRATION_INVALID", "message": str(error)}) from error

    session.execute(delete(CourtCalibration).where(CourtCalibration.analysis_id == analysis_id))
    calibration = CourtCalibration(
        analysis_id=analysis_id,
        frame_time_ms=payload.frame_time_ms,
        image_points=payload.image_points,
        court_points=payload.court_points,
        hoop_point=payload.hoop_point,
        transform=transform,
        source=CalibrationSource.USER,
        confirmed_at=datetime.now(timezone.utc),
    )
    session.add(calibration)
    session.commit()
    session.refresh(calibration)
    return calibration
