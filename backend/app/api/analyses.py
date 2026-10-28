from datetime import date
from typing import Annotated

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.dependencies import get_db_session
from app.models import Analysis, CalibrationSource, CourtCalibration, Player, PlayType
from app.schemas.analyses import (
    AnalysisRegistrationResponse,
    CourtCalibrationRequest,
    CourtCalibrationResponse,
    PlayerCreateRequest,
    PlayerListResponse,
    PlayerResponse,
    PlayerUpdateRequest,
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

PLAYER_LIMITS = {
    PlayType.SOLO: 1,
    PlayType.ONE_ON_ONE: 2,
    PlayType.TWO_ON_TWO: 4,
    PlayType.THREE_ON_THREE: 6,
}


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


def player_warnings(players: list[Player]) -> list[str]:
    warnings: list[str] = []
    names = [player.display_name.strip().casefold() for player in players]
    numbers = [player.jersey_number.strip().casefold() for player in players if player.jersey_number and player.jersey_number.strip()]
    if len(names) != len(set(names)):
        warnings.append("같은 선수 이름이 있어 화면에서 구분하기 어려울 수 있습니다.")
    if len(numbers) != len(set(numbers)):
        warnings.append("같은 등번호가 있어 화면에서 구분하기 어려울 수 있습니다.")
    if any(player.initial_box is None for player in players):
        warnings.append("화면 위치를 지정하지 않은 선수가 있어 자동 구분 정확도가 낮아질 수 있습니다.")
    return warnings


def list_players_for(session: Session, analysis_id: str) -> list[Player]:
    return list(session.scalars(select(Player).where(Player.analysis_id == analysis_id).order_by(Player.created_at)))


@router.get("/{analysis_id}/players", response_model=PlayerListResponse)
def get_players(
    analysis_id: str, session: Annotated[Session, Depends(get_db_session)]
) -> PlayerListResponse:
    analysis = find_analysis(session, analysis_id)
    players = list_players_for(session, analysis_id)
    return PlayerListResponse(players=players, warnings=player_warnings(players), max_players=PLAYER_LIMITS[analysis.play_type])


@router.post("/{analysis_id}/players", response_model=PlayerResponse, status_code=201)
def create_player(
    analysis_id: str,
    payload: PlayerCreateRequest,
    session: Annotated[Session, Depends(get_db_session)],
) -> Player:
    analysis = find_analysis(session, analysis_id)
    players = list_players_for(session, analysis_id)
    if len(players) >= PLAYER_LIMITS[analysis.play_type]:
        raise HTTPException(status_code=409, detail={"code": "PLAYER_LIMIT", "message": "촬영 형태에서 허용하는 최대 선수 수를 넘었습니다."})
    name = (payload.display_name or "").strip()
    player = Player(
        analysis_id=analysis_id,
        display_name=name or f"선수 {chr(65 + len(players))}",
        jersey_number=(payload.jersey_number or "").strip() or None,
        is_temporary_name=not bool(name),
    )
    session.add(player); session.commit(); session.refresh(player)
    return player


def boxes_overlap(left: list[float], right: list[float]) -> bool:
    lx, ly, lw, lh = left; rx, ry, rw, rh = right
    return lx < rx + rw and lx + lw > rx and ly < ry + rh and ly + lh > ry


@router.patch("/{analysis_id}/players/{player_id}", response_model=PlayerResponse)
def update_player(
    analysis_id: str,
    player_id: str,
    payload: PlayerUpdateRequest,
    session: Annotated[Session, Depends(get_db_session)],
) -> Player:
    analysis = find_analysis(session, analysis_id)
    player = session.get(Player, player_id)
    if player is None or player.analysis_id != analysis_id:
        raise HTTPException(status_code=404, detail={"code": "PLAYER_NOT_FOUND", "message": "선수를 찾을 수 없습니다."})
    if payload.initial_time_ms is not None and payload.initial_time_ms > analysis.duration_ms:
        raise HTTPException(status_code=400, detail={"code": "PLAYER_TIME_INVALID", "message": "선수 지정 시간이 영상 범위를 벗어났습니다."})
    if payload.initial_box is not None:
        if len(payload.initial_box) != 4 or any(value < 0 or value > 1 for value in payload.initial_box) or payload.initial_box[2] <= 0 or payload.initial_box[3] <= 0 or payload.initial_box[0] + payload.initial_box[2] > 1 or payload.initial_box[1] + payload.initial_box[3] > 1:
            raise HTTPException(status_code=400, detail={"code": "PLAYER_BOX_INVALID", "message": "선수 표시 영역이 영상 화면 밖에 있습니다."})
        others = [item for item in list_players_for(session, analysis_id) if item.id != player.id and item.initial_box]
        if any(boxes_overlap(payload.initial_box, item.initial_box) for item in others):
            raise HTTPException(status_code=409, detail={"code": "PLAYER_BOX_OVERLAP", "message": "같은 화면 위치에 두 선수를 겹쳐 지정할 수 없습니다."})
        player.initial_box = payload.initial_box
        player.initial_time_ms = payload.initial_time_ms or 0
    if payload.display_name is not None:
        name = payload.display_name.strip()
        player.display_name = name or player.display_name
        if name: player.is_temporary_name = False
    if payload.jersey_number is not None:
        player.jersey_number = payload.jersey_number.strip() or None
    session.commit(); session.refresh(player)
    return player


@router.delete("/{analysis_id}/players/{player_id}", status_code=204)
def delete_player(
    analysis_id: str, player_id: str, session: Annotated[Session, Depends(get_db_session)]
) -> Response:
    analysis = find_analysis(session, analysis_id)
    player = session.get(Player, player_id)
    if player is None or player.analysis_id != analysis_id:
        raise HTTPException(status_code=404, detail={"code": "PLAYER_NOT_FOUND", "message": "선수를 찾을 수 없습니다."})
    if analysis.play_type is PlayType.SOLO:
        raise HTTPException(status_code=409, detail={"code": "SOLO_PLAYER_REQUIRED", "message": "개인 연습의 기본 선수는 삭제할 수 없습니다."})
    session.delete(player); session.commit()
    return Response(status_code=204)
