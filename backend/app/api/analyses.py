from datetime import date
from typing import Annotated

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.dependencies import get_db_session
from app.models import (
    Analysis,
    AnalysisJob,
    AnalysisStatus,
    CalibrationSource,
    CourtCalibration,
    Player,
    PlayType,
    Shot,
    ShotResult,
    ShotSource,
    ShotStatus,
    ShotType,
)
from app.schemas.analyses import (
    AnalysisRegistrationResponse,
    AnalysisJobResponse,
    CourtCalibrationRequest,
    CourtCalibrationResponse,
    PlayerCreateRequest,
    PlayerListResponse,
    PlayerResponse,
    PlayerUpdateRequest,
    ShotCreateRequest,
    ShotListResponse,
    ShotResponse,
    ShotUpdateRequest,
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
from app.services.analysis_jobs import cancel_job, current_job, read_phase7_result, refresh_simulation, run_phase7_job, start_phase7, start_simulation
from app.analysis.phase7_pipeline import Phase7Error
from app.services.court_zones import ZONE_LABELS, ZoneCode, classify_zone
from app.services.shot_statistics import calculate_statistics


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


def player_warnings(players: list[Player], play_type: PlayType) -> list[str]:
    warnings: list[str] = []
    names = [player.display_name.strip().casefold() for player in players]
    numbers = [player.jersey_number.strip().casefold() for player in players if player.jersey_number and player.jersey_number.strip()]
    if len(names) != len(set(names)):
        warnings.append("같은 선수 이름이 있어 화면에서 구분하기 어려울 수 있습니다.")
    if len(numbers) != len(set(numbers)):
        warnings.append("같은 등번호가 있어 화면에서 구분하기 어려울 수 있습니다.")
    if play_type is not PlayType.SOLO and any(player.initial_box is None for player in players):
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
    return PlayerListResponse(players=players, warnings=player_warnings(players, analysis.play_type), max_players=PLAYER_LIMITS[analysis.play_type])


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


def ensure_analysis_ready(request: Request, session: Session, analysis: Analysis) -> None:
    missing: list[str] = []
    video_path = request.app.state.settings.upload_path / analysis.id / "original.mp4"
    if not video_path.is_file(): missing.append("원본 영상")
    if not analysis.ai_notice_confirmed: missing.append("OpenAI 처리 안내 확인")
    if analysis.contains_others and not analysis.consent_confirmed: missing.append("촬영·분석 동의")
    if analysis.warnings and not analysis.warnings_accepted: missing.append("촬영 조건 경고 확인")
    if session.scalar(select(CourtCalibration).where(CourtCalibration.analysis_id == analysis.id)) is None: missing.append("코트 위치 확인")
    if not list_players_for(session, analysis.id): missing.append("선수 설정")
    if missing:
        raise HTTPException(status_code=409, detail={"code": "ANALYSIS_NOT_READY", "message": "다음 설정을 먼저 확인해 주세요: " + ", ".join(missing)})


@router.post("/{analysis_id}/jobs/start", response_model=AnalysisJobResponse)
def start_analysis_job(
    request: Request,
    analysis_id: str,
    session: Annotated[Session, Depends(get_db_session)],
) -> AnalysisJob:
    analysis = find_analysis(session, analysis_id)
    ensure_analysis_ready(request, session, analysis)
    job, _ = start_simulation(session, analysis)
    return job


@router.post("/{analysis_id}/jobs/start-real", response_model=AnalysisJobResponse)
def start_real_analysis_job(
    request: Request,
    background_tasks: BackgroundTasks,
    analysis_id: str,
    session: Annotated[Session, Depends(get_db_session)],
) -> AnalysisJob:
    analysis = find_analysis(session, analysis_id)
    ensure_analysis_ready(request, session, analysis)
    try:
        job, created = start_phase7(session, analysis, request.app.state.settings)
    except Phase7Error as error:
        raise HTTPException(status_code=409, detail={"code": "PHASE7_NOT_READY", "message": str(error)}) from error
    if created:
        background_tasks.add_task(run_phase7_job, request.app.state.session_factory,
                                  request.app.state.settings, analysis_id, job.id)
    return job


@router.get("/{analysis_id}/phase7-candidates")
def get_phase7_candidates(
    request: Request, analysis_id: str, session: Annotated[Session, Depends(get_db_session)]
) -> dict:
    find_analysis(session, analysis_id)
    result = read_phase7_result(request.app.state.settings, analysis_id)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "CANDIDATES_NOT_FOUND", "message": "저장된 슛 후보가 없습니다."})
    return result


@router.get("/{analysis_id}/jobs/current", response_model=AnalysisJobResponse)
def get_current_analysis_job(
    analysis_id: str, session: Annotated[Session, Depends(get_db_session)]
) -> AnalysisJob:
    analysis = find_analysis(session, analysis_id)
    job = current_job(session, analysis_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": "아직 시작한 분석 작업이 없습니다."})
    return refresh_simulation(session, analysis, job)


@router.post("/{analysis_id}/jobs/{job_id}/cancel", response_model=AnalysisJobResponse)
def cancel_analysis_job(
    analysis_id: str,
    job_id: str,
    session: Annotated[Session, Depends(get_db_session)],
) -> AnalysisJob:
    analysis = find_analysis(session, analysis_id)
    job = session.get(AnalysisJob, job_id)
    if job is None or job.analysis_id != analysis_id:
        raise HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": "분석 작업을 찾을 수 없습니다."})
    return cancel_job(session, analysis, job)


def _find_shot(session: Session, analysis_id: str, shot_id: str) -> Shot:
    shot = session.get(Shot, shot_id)
    if shot is None or shot.analysis_id != analysis_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "SHOT_NOT_FOUND", "message": "슛 기록을 찾을 수 없습니다."},
        )
    return shot


def _find_shot_player(session: Session, analysis_id: str, player_id: str) -> Player:
    player = session.get(Player, player_id)
    if player is None or player.analysis_id != analysis_id:
        raise HTTPException(
            status_code=400,
            detail={"code": "PLAYER_INVALID", "message": "이 분석에 등록된 선수를 선택해 주세요."},
        )
    return player


def _validate_shot_values(analysis: Analysis, values: dict) -> None:
    start = int(values["sequence_start_ms"])
    end = int(values["sequence_end_ms"])
    candidate = int(values["candidate_time_ms"])
    if start >= end:
        raise HTTPException(
            status_code=400,
            detail={"code": "SEQUENCE_ORDER_INVALID", "message": "장면 시작 시간은 끝 시간보다 빨라야 합니다."},
        )
    if end > analysis.duration_ms or candidate > analysis.duration_ms:
        raise HTTPException(
            status_code=400,
            detail={"code": "SHOT_TIME_OUT_OF_RANGE", "message": "슛 시간과 재생 구간은 원본 영상 길이 안에 있어야 합니다."},
        )
    if not start <= candidate <= end:
        raise HTTPException(
            status_code=400,
            detail={"code": "CANDIDATE_OUTSIDE_SEQUENCE", "message": "슛 시간은 재생 장면의 시작과 끝 사이에 있어야 합니다."},
        )
    if end - start > 20_000:
        raise HTTPException(
            status_code=400,
            detail={"code": "SEQUENCE_TOO_LONG", "message": "한 슛의 재생 장면은 최대 20초까지 저장할 수 있습니다."},
        )
    if values["result"] is ShotResult.NEEDS_REVIEW or values["shot_type"] is ShotType.NEEDS_REVIEW:
        raise HTTPException(
            status_code=400,
            detail={"code": "CONFIRMED_VALUE_REQUIRED", "message": "사용자가 저장할 때는 성공 여부와 슛 종류를 확정해 주세요."},
        )


def _refresh_analysis_review_status(session: Session, analysis: Analysis) -> None:
    has_review = session.scalar(
        select(Shot.id).where(
            Shot.analysis_id == analysis.id,
            Shot.status == ShotStatus.NEEDS_REVIEW,
        ).limit(1)
    ) is not None
    analysis.status = AnalysisStatus.REVIEW_REQUIRED if has_review else AnalysisStatus.COMPLETED


def _shot_response(shot: Shot) -> ShotResponse:
    zone = ZoneCode(shot.zone_code) if shot.zone_code else None
    return ShotResponse(
        id=shot.id,
        analysis_id=shot.analysis_id,
        player_id=shot.player_id,
        player_name=shot.player.display_name,
        candidate_time_ms=shot.candidate_time_ms,
        sequence_start_ms=shot.sequence_start_ms,
        sequence_end_ms=shot.sequence_end_ms,
        court_x=shot.court_x,
        court_y=shot.court_y,
        zone_code=shot.zone_code,
        zone_label=ZONE_LABELS[zone] if zone else None,
        result=shot.result,
        shot_type=shot.shot_type,
        status=shot.status,
        review_reason=shot.review_reason,
        source=shot.source,
        confidence_data=shot.confidence_data,
        created_at=shot.created_at,
        updated_at=shot.updated_at,
    )


@router.get("/{analysis_id}/video")
def get_analysis_video(
    request: Request,
    analysis_id: str,
    session: Annotated[Session, Depends(get_db_session)],
) -> FileResponse:
    analysis = find_analysis(session, analysis_id)
    video_path = request.app.state.settings.upload_path / analysis.id / "original.mp4"
    if not video_path.is_file():
        raise HTTPException(
            status_code=404,
            detail={"code": "VIDEO_NOT_FOUND", "message": "보관된 원본 영상을 찾을 수 없습니다."},
        )
    return FileResponse(video_path, media_type="video/mp4", headers={"Accept-Ranges": "bytes"})


@router.get("/{analysis_id}/shots", response_model=ShotListResponse)
def list_shots(
    analysis_id: str,
    session: Annotated[Session, Depends(get_db_session)],
) -> ShotListResponse:
    analysis = find_analysis(session, analysis_id)
    shots = list(
        session.scalars(
            select(Shot).where(Shot.analysis_id == analysis_id).order_by(Shot.candidate_time_ms, Shot.created_at)
        )
    )
    return ShotListResponse(
        analysis_id=analysis.id,
        analysis_status=analysis.status,
        duration_ms=analysis.duration_ms,
        fps=analysis.fps,
        shots=[_shot_response(shot) for shot in shots],
        statistics=calculate_statistics(shots),
    )


@router.post("/{analysis_id}/shots", response_model=ShotResponse, status_code=201)
def create_shot(
    analysis_id: str,
    payload: ShotCreateRequest,
    session: Annotated[Session, Depends(get_db_session)],
) -> ShotResponse:
    analysis = find_analysis(session, analysis_id)
    _find_shot_player(session, analysis_id, payload.player_id)
    values = payload.model_dump()
    _validate_shot_values(analysis, values)
    shot = Shot(
        analysis_id=analysis_id,
        **values,
        zone_code=classify_zone(payload.court_x, payload.court_y).value,
        status=ShotStatus.USER_CONFIRMED,
        source=ShotSource.USER,
        review_reason=None,
        confidence_data={"entered_by_user": True},
    )
    session.add(shot)
    session.flush()
    _refresh_analysis_review_status(session, analysis)
    session.commit()
    session.refresh(shot)
    return _shot_response(shot)


@router.patch("/{analysis_id}/shots/{shot_id}", response_model=ShotResponse)
def update_shot(
    analysis_id: str,
    shot_id: str,
    payload: ShotUpdateRequest,
    session: Annotated[Session, Depends(get_db_session)],
) -> ShotResponse:
    analysis = find_analysis(session, analysis_id)
    shot = _find_shot(session, analysis_id, shot_id)
    changes = payload.model_dump(exclude_unset=True)
    values = {
        "player_id": shot.player_id,
        "candidate_time_ms": shot.candidate_time_ms,
        "sequence_start_ms": shot.sequence_start_ms,
        "sequence_end_ms": shot.sequence_end_ms,
        "court_x": shot.court_x,
        "court_y": shot.court_y,
        "result": shot.result,
        "shot_type": shot.shot_type,
        **changes,
    }
    if values["court_x"] is None or values["court_y"] is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "COURT_POSITION_REQUIRED", "message": "확정하려면 코트의 가로·세로 위치를 입력해 주세요."},
        )
    _find_shot_player(session, analysis_id, values["player_id"])
    _validate_shot_values(analysis, values)
    for field, value in changes.items():
        setattr(shot, field, value)
    shot.zone_code = classify_zone(float(values["court_x"]), float(values["court_y"])).value
    shot.status = ShotStatus.USER_CONFIRMED
    shot.review_reason = None
    shot.confidence_data = {**shot.confidence_data, "confirmed_by_user": True}
    session.flush()
    _refresh_analysis_review_status(session, analysis)
    session.commit()
    session.refresh(shot)
    return _shot_response(shot)


@router.delete("/{analysis_id}/shots/{shot_id}", status_code=204)
def delete_shot(
    analysis_id: str,
    shot_id: str,
    session: Annotated[Session, Depends(get_db_session)],
) -> Response:
    analysis = find_analysis(session, analysis_id)
    shot = _find_shot(session, analysis_id, shot_id)
    session.delete(shot)
    session.flush()
    _refresh_analysis_review_status(session, analysis)
    session.commit()
    return Response(status_code=204)
