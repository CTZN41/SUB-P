from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.dependencies import get_db_session
from app.schemas.analyses import AnalysisRegistrationResponse
from app.services.authentication import require_analysis_user
from app.services.streaming_upload import UploadRegistration, create_upload, receive_upload, complete_upload
from app.services.video_registration import RegistrationError

router = APIRouter(prefix="/api/uploads", tags=["uploads"], dependencies=[Depends(require_analysis_user)])


def upload_error(error: RegistrationError) -> HTTPException:
    return HTTPException(error.status_code, detail={"code": error.code, "message": error.message})


@router.post("", status_code=201)
def prepare_upload(request: Request, data: UploadRegistration) -> dict:
    try:
        return create_upload(request.app.state.settings, getattr(request.state, "auth_user_id", None), data)
    except RegistrationError as error:
        raise upload_error(error) from error


@router.put("/{upload_id}")
async def upload_video(request: Request, upload_id: str) -> dict:
    try:
        return await receive_upload(request, request.app.state.settings, getattr(request.state, "auth_user_id", None), upload_id)
    except RegistrationError as error:
        raise upload_error(error) from error


@router.post("/{upload_id}/complete", response_model=AnalysisRegistrationResponse)
def finish_upload(request: Request, upload_id: str, session: Annotated[Session, Depends(get_db_session)]) -> dict:
    try:
        return complete_upload(session, request.app.state.settings, getattr(request.state, "auth_user_id", None), upload_id)
    except RegistrationError as error:
        raise upload_error(error) from error
