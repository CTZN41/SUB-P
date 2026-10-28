from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.db.dependencies import get_db_session
from app.models import Analysis, PlayType
from app.schemas.analyses import AnalysisRegistrationResponse, WarningAcceptanceResponse
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
