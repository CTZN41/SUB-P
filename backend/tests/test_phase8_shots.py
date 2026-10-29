from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    Analysis,
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
from app.services.phase8_shots import materialize_shots_from_candidates


def analysis_setup(session: Session) -> tuple[Analysis, CourtCalibration]:
    analysis = Analysis(
        title="Phase 8 시험",
        play_type=PlayType.SOLO,
        original_name="phase8.mp4",
        stored_path="uploads/phase8.mp4",
        file_size=1,
        duration_ms=10_000,
        status=AnalysisStatus.ANALYZING,
    )
    session.add(analysis)
    session.flush()
    session.add(Player(analysis_id=analysis.id, display_name="선수 A"))
    calibration = CourtCalibration(
        analysis_id=analysis.id,
        frame_time_ms=0,
        image_points=[[0, 0], [50, 0], [50, 47], [0, 47]],
        court_points=[[0, 0], [50, 0], [50, 47], [0, 47]],
        hoop_point=[25, 5.25],
        transform=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        source=CalibrationSource.USER,
    )
    session.add(calibration)
    session.flush()
    return analysis, calibration


def test_confident_review_becomes_complete_shot(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase8.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        analysis, calibration = analysis_setup(session)
        result = {
            "candidates": [{
                "time_ms": 5000,
                "foot_point_px": [25, 10],
                "confidence": .8,
                "review_reasons": [],
                "ai_review": {
                    "is_shot": True,
                    "confidence": .9,
                    "outcome": "made",
                    "shot_type": "set_shot",
                    "sequence_start_offset_ms": -2000,
                    "sequence_end_offset_ms": 2000,
                    "review_required": False,
                },
            }]
        }
        shots = materialize_shots_from_candidates(session, analysis, calibration, result)
        session.commit()

        assert len(shots) == 1
        assert shots[0].result is ShotResult.MADE
        assert shots[0].shot_type is ShotType.SET_SHOT
        assert shots[0].status is ShotStatus.AUTO
        assert shots[0].zone_code == "paint_center"
        assert (shots[0].sequence_start_ms, shots[0].sequence_end_ms) == (3000, 7000)
        assert analysis.status is AnalysisStatus.COMPLETED
        assert result["candidates"][0]["phase8"]["shot_id"] == shots[0].id
    engine.dispose()


def test_uncertain_candidate_is_preserved_for_review(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase8.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        analysis, calibration = analysis_setup(session)
        result = {"candidates": [{"time_ms": 1000, "confidence": .3}]}
        shot = materialize_shots_from_candidates(
            session, analysis, calibration, result
        )[0]
        session.commit()

        assert shot.result is ShotResult.NEEDS_REVIEW
        assert shot.shot_type is ShotType.NEEDS_REVIEW
        assert shot.status is ShotStatus.NEEDS_REVIEW
        assert shot.court_x is None and shot.zone_code is None
        assert (shot.sequence_start_ms, shot.sequence_end_ms) == (0, 4000)
        assert "OpenAI" in shot.review_reason
        assert analysis.status is AnalysisStatus.REVIEW_REQUIRED
    engine.dispose()


def test_retry_updates_auto_shot_without_overwriting_confirmed_shot(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase8.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        analysis, calibration = analysis_setup(session)
        result = {
            "candidates": [{
                "time_ms": 5000,
                "foot_point_px": [25, 10],
                "ai_review": {
                    "is_shot": True,
                    "outcome": "missed",
                    "shot_type": "layup",
                    "sequence_start_offset_ms": -2000,
                    "sequence_end_offset_ms": 2000,
                    "review_required": False,
                },
            }]
        }
        first = materialize_shots_from_candidates(
            session, analysis, calibration, result
        )[0]
        assert first.result is ShotResult.MISSED
        assert first.shot_type is ShotType.LAYUP
        first.status = ShotStatus.USER_CONFIRMED
        first.result = ShotResult.MADE
        session.flush()

        second = materialize_shots_from_candidates(
            session, analysis, calibration, result
        )[0]
        session.commit()

        assert second.id == first.id
        assert second.status is ShotStatus.USER_CONFIRMED
        assert second.result is ShotResult.MADE
        assert len(list(session.scalars(select(Shot)))) == 1
        assert second.source is ShotSource.AUTO
    engine.dispose()


def test_unknown_outcome_keeps_agreed_shot_type_for_partial_automation(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase8.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        analysis, calibration = analysis_setup(session)
        shot = materialize_shots_from_candidates(
            session,
            analysis,
            calibration,
            {"candidates": [{
                "time_ms": 5000,
                "foot_point_px": [25, 10],
                "review_reasons": ["림 자동 탐지 실패 — 확인된 골대 위치 사용"],
                "ai_review": {
                    "is_shot": True,
                    "outcome": "unclear",
                    "shot_type": "dribble_pull_up",
                    "sequence_start_offset_ms": -3000,
                    "sequence_end_offset_ms": 3000,
                    "review_required": True,
                    "reason": "성공 여부 근거가 화면에 없음",
                },
            }]},
        )[0]
        session.commit()

        assert shot.result is ShotResult.NEEDS_REVIEW
        assert shot.shot_type is ShotType.DRIBBLE_PULL_UP
        assert shot.status is ShotStatus.NEEDS_REVIEW
        assert shot.court_x == 25
        assert "성공·실패" in shot.review_reason
        assert shot.confidence_data["diagnostic_reasons"]
    engine.dispose()


def test_local_diagnostic_warning_does_not_block_clear_ai_result(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase8.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        analysis, calibration = analysis_setup(session)
        shot = materialize_shots_from_candidates(
            session,
            analysis,
            calibration,
            {"candidates": [{
                "time_ms": 5000,
                "foot_point_px": [25, 10],
                "review_reasons": ["림 자동 탐지 실패 — 확인된 골대 위치 사용"],
                "ai_review": {
                    "is_shot": True,
                    "outcome": "made",
                    "shot_type": "set_shot",
                    "sequence_start_offset_ms": -3000,
                    "sequence_end_offset_ms": 3000,
                    "review_required": False,
                },
            }]},
        )[0]
        session.commit()

        assert shot.result is ShotResult.MADE
        assert shot.shot_type is ShotType.SET_SHOT
        assert shot.status is ShotStatus.AUTO
        assert shot.review_reason is None
        assert shot.confidence_data["diagnostic_reasons"]
    engine.dispose()


def test_camera_shifted_candidate_does_not_save_wrong_court_position(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase8.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        analysis, calibration = analysis_setup(session)
        shot = materialize_shots_from_candidates(
            session,
            analysis,
            calibration,
            {"candidates": [{
                "time_ms": 5000,
                "foot_point_px": [25, 10],
                "scene_id": 1,
                "tracked_hoop_px": [40, 8],
                "calibration_shifted": True,
                "ai_review": {
                    "is_shot": True,
                    "outcome": "made",
                    "shot_type": "set_shot",
                    "sequence_start_offset_ms": -2000,
                    "sequence_end_offset_ms": 2000,
                },
            }]},
        )[0]
        session.commit()

        assert shot.court_x is None and shot.court_y is None and shot.zone_code is None
        assert shot.status is ShotStatus.NEEDS_REVIEW
        assert "카메라 이동" in shot.review_reason
        assert shot.confidence_data["scene_id"] == 1
        assert shot.confidence_data["tracked_hoop_px"] == [40, 8]
    engine.dispose()
