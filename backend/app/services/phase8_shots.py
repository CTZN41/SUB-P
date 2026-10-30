"""Turn reviewed Phase 7 candidates into durable Phase 8 shot records."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Analysis,
    AnalysisStatus,
    CourtCalibration,
    Player,
    Shot,
    ShotResult,
    ShotSource,
    ShotStatus,
    ShotType,
)
from app.services.court_zones import classify_zone


DEFAULT_SEQUENCE_BEFORE_MS = 5000
DEFAULT_SEQUENCE_AFTER_MS = 3000
MIN_AI_SEQUENCE_MS = 3000
MAX_AI_SEQUENCE_MS = 15000


SHOT_TYPE_MAP = {
    "catch_and_shoot": ShotType.CATCH_AND_SHOOT,
    "set_shot": ShotType.SET_SHOT,
    "dribble_pull_up": ShotType.DRIBBLE_PULL_UP,
    "layup": ShotType.LAYUP,
    "dunk": ShotType.DUNK,
    "fadeaway": ShotType.FADEAWAY,
    "step_back": ShotType.STEP_BACK,
    "turnaround": ShotType.TURNAROUND,
    "hook_shot": ShotType.HOOK_SHOT,
    "other": ShotType.OTHER,
    "needs_review": ShotType.NEEDS_REVIEW,
}


def _append_reason(reasons: list[str], value: str | None) -> None:
    text = (value or "").strip()
    if text and text not in reasons:
        reasons.append(text)


def _sequence_bounds(
    candidate_time_ms: int,
    duration_ms: int,
    ai_review: dict,
    reasons: list[str],
) -> tuple[int, int]:
    start_offset = ai_review.get("sequence_start_offset_ms")
    end_offset = ai_review.get("sequence_end_offset_ms")
    valid_offsets = (
        isinstance(start_offset, int)
        and isinstance(end_offset, int)
        and start_offset <= 0 <= end_offset
        and MIN_AI_SEQUENCE_MS <= end_offset - start_offset <= MAX_AI_SEQUENCE_MS
    )
    if not valid_offsets:
        start_offset = -DEFAULT_SEQUENCE_BEFORE_MS
        end_offset = DEFAULT_SEQUENCE_AFTER_MS
        _append_reason(reasons, "전체 슛 구간을 확정하지 못해 기본 구간을 사용")

    start_ms = max(0, candidate_time_ms + start_offset)
    end_ms = min(duration_ms, candidate_time_ms + end_offset)
    if end_ms <= start_ms:
        start_ms = max(0, candidate_time_ms - DEFAULT_SEQUENCE_BEFORE_MS)
        end_ms = min(duration_ms, candidate_time_ms + DEFAULT_SEQUENCE_AFTER_MS)
        _append_reason(reasons, "영상 경계 때문에 슛 구간 확인 필요")
    return start_ms, end_ms


def _court_position(
    ai_review: dict,
    reasons: list[str],
) -> tuple[float | None, float | None, str | None]:
    zone_code = ai_review.get("court_zone")
    court_x = ai_review.get("court_x")
    court_y = ai_review.get("court_y")
    confidence = ai_review.get("position_confidence")
    if (
        zone_code == "needs_review"
        or not isinstance(court_x, (int, float))
        or not isinstance(court_y, (int, float))
        or not isinstance(confidence, (int, float))
        or confidence < .75
    ):
        _append_reason(reasons, ai_review.get("position_reason") or "AI가 슛 위치를 확정하지 못함")
        return None, None, None
    if not 0 <= court_x <= 50 or not 0 <= court_y <= 47:
        _append_reason(reasons, "AI가 반환한 슛 위치가 반코트 범위를 벗어남")
        return None, None, None
    calculated_zone = classify_zone(float(court_x), float(court_y)).value
    if calculated_zone != zone_code:
        _append_reason(reasons, "AI가 반환한 구역과 코트 좌표가 서로 맞지 않아 위치 확인 필요")
        return None, None, None
    return round(float(court_x), 4), round(float(court_y), 4), str(zone_code)


def _result(ai_review: dict, reasons: list[str]) -> ShotResult:
    if not ai_review.get("is_shot"):
        _append_reason(reasons, "AI가 새 슛 동작을 확인하지 못했습니다. 리바운드·패스 등일 수 있으니 원본을 확인해 주세요.")
        return ShotResult.NEEDS_REVIEW
    outcome = ai_review.get("outcome")
    if outcome == "made":
        return ShotResult.MADE
    if outcome == "missed":
        return ShotResult.MISSED
    _append_reason(reasons, "성공·실패를 영상에서 확정하지 못함")
    return ShotResult.NEEDS_REVIEW


def _shot_type(ai_review: dict, reasons: list[str]) -> ShotType:
    shot_type = SHOT_TYPE_MAP.get(ai_review.get("shot_type"), ShotType.NEEDS_REVIEW)
    if shot_type is ShotType.NEEDS_REVIEW:
        _append_reason(reasons, "슛 종류를 확정하지 못함")
    return shot_type


def _existing_auto_shot(
    session: Session, analysis_id: str, candidate_time_ms: int
) -> Shot | None:
    return session.scalar(
        select(Shot).where(
            Shot.analysis_id == analysis_id,
            Shot.candidate_time_ms == candidate_time_ms,
            Shot.source == ShotSource.AUTO,
        )
    )


def materialize_shots_from_candidates(
    session: Session,
    analysis: Analysis,
    calibration: CourtCalibration,
    result: dict,
) -> list[Shot]:
    """Insert or update only unconfirmed auto shots; caller owns the transaction."""
    player = session.scalar(
        select(Player)
        .where(Player.analysis_id == analysis.id)
        .order_by(Player.created_at, Player.id)
    )
    if player is None:
        raise RuntimeError("슛 기록을 연결할 선수가 없습니다.")

    shots: list[Shot] = []
    candidates = result.get("candidates") or []
    for candidate in candidates:
        candidate_time_ms = max(0, min(analysis.duration_ms, int(candidate["time_ms"])))
        reasons: list[str] = []
        diagnostic_reasons: list[str] = []
        for reason in candidate.get("review_reasons") or []:
            _append_reason(diagnostic_reasons, str(reason))

        ai_review = candidate.get("ai_review") or {}
        if ai_review.get("status") == "failed" or not ai_review:
            _append_reason(reasons, "OpenAI 보조 판정을 사용할 수 없어 로컬 후보를 보존")

        court_x, court_y, zone_code = _court_position(ai_review, reasons)
        sequence_start_ms, sequence_end_ms = _sequence_bounds(
            candidate_time_ms, analysis.duration_ms, ai_review, diagnostic_reasons
        )
        shot_result = _result(ai_review, reasons)
        shot_type = _shot_type(ai_review, reasons)
        if reasons:
            _append_reason(reasons, ai_review.get("reason"))
        unresolved = (
            shot_result is ShotResult.NEEDS_REVIEW
            or shot_type is ShotType.NEEDS_REVIEW
            or court_x is None
            or not ai_review.get("is_shot")
            or ai_review.get("status") == "failed"
        )
        status = ShotStatus.NEEDS_REVIEW if unresolved else ShotStatus.AUTO
        shot = _existing_auto_shot(session, analysis.id, candidate_time_ms)
        if shot is not None and shot.status is ShotStatus.USER_CONFIRMED:
            shots.append(shot)
            continue
        if shot is None:
            shot = Shot(
                analysis_id=analysis.id,
                candidate_time_ms=candidate_time_ms,
                source=ShotSource.AUTO,
            )
            session.add(shot)
        shot.player_id = player.id
        shot.sequence_start_ms = sequence_start_ms
        shot.sequence_end_ms = sequence_end_ms
        shot.court_x = court_x
        shot.court_y = court_y
        shot.zone_code = zone_code
        shot.result = shot_result
        shot.shot_type = shot_type
        shot.status = status
        shot.review_reason = "; ".join(reasons) or None
        shot.confidence_data = {
            "local_confidence": candidate.get("confidence"),
            "candidate_source": candidate.get("candidate_source", "trajectory"),
            "scene_id": candidate.get("scene_id", 0),
            "tracked_hoop_px": candidate.get("tracked_hoop_px"),
            "calibration_shifted": bool(candidate.get("calibration_shifted")),
            "diagnostic_reasons": diagnostic_reasons,
            "ai_review": ai_review,
            "shot_gate": candidate.get("shot_gate"),
        }
        shots.append(shot)

    session.flush()
    for candidate, shot in zip(candidates, shots, strict=True):
        candidate["phase8"] = {
            "shot_id": shot.id,
            "court_x": shot.court_x,
            "court_y": shot.court_y,
            "zone_code": shot.zone_code,
            "result": shot.result.value,
            "shot_type": shot.shot_type.value,
            "status": shot.status.value,
            "sequence_start_ms": shot.sequence_start_ms,
            "sequence_end_ms": shot.sequence_end_ms,
            "review_reason": shot.review_reason,
        }

    needs_review = not shots or any(shot.status is ShotStatus.NEEDS_REVIEW for shot in shots)
    analysis.status = AnalysisStatus.REVIEW_REQUIRED if needs_review else AnalysisStatus.COMPLETED
    analysis.current_stage = "슛 기록 검토 필요" if needs_review else "분석 완료"
    return shots
