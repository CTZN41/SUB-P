from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Analysis, AnalysisStatus, Player, PlayType, Shot, ShotResult, ShotSource, ShotStatus, ShotType
from app.services.court_zones import classify_zone


def test_analysis_player_and_shot_are_saved_and_loaded(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase2.db').as_posix()}")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        analysis = Analysis(
            title="개인 연습",
            play_type=PlayType.SOLO,
            original_name="practice.mp4",
            stored_path="uploads/example.mp4",
            file_size=100,
            duration_ms=10_000,
            status=AnalysisStatus.SETUP_REQUIRED,
        )
        player = Player(analysis=analysis, display_name="선수 A", is_temporary_name=True)
        x, y = 25.0, 15.0
        session.add(
            Shot(
                analysis=analysis,
                player=player,
                candidate_time_ms=5000,
                sequence_start_ms=3000,
                sequence_end_ms=7000,
                court_x=x,
                court_y=y,
                zone_code=classify_zone(x, y).value,
                result=ShotResult.MADE,
                shot_type=ShotType.SET_SHOT,
                status=ShotStatus.AUTO,
                source=ShotSource.AUTO,
            )
        )
        session.commit()

        stored_values = session.execute(
            text("SELECT play_type, status FROM analyses WHERE id = :id"), {"id": analysis.id}
        ).one()
        assert stored_values == ("solo", "setup_required")

    with Session(engine) as session:
        saved = session.scalar(select(Shot))
        assert saved is not None
        assert saved.analysis.title == "개인 연습"
        assert saved.player.display_name == "선수 A"
        assert saved.zone_code == "paint_center"
        assert saved.result is ShotResult.MADE

    engine.dispose()
