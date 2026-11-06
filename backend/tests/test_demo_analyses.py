from datetime import date, datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.analyses import router
from app.core.config import Settings
from app.db.base import Base
from app.models import Analysis, AnalysisStatus, PlayType, Player, Shot, ShotResult, ShotSource, ShotStatus, ShotType
from app.services.monthly_statistics import monthly_statistics
from app.services.monthly_practice_report import monthly_context
from app.services.practice_report import comparison_context


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'demos.db'}", connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = FastAPI()
    app.state.settings = Settings(_env_file=None, app_env='test', upload_dir=str(tmp_path / 'uploads'))
    app.state.session_factory = factory
    app.include_router(router)
    yield factory, TestClient(app)
    engine.dispose()


def add(session, title, day, *, result=ShotResult.MADE, month=11):
    a = Analysis(title=title, owner_id='owner', play_type=PlayType.SOLO,
                 original_name='clip.mp4', stored_path='clip', file_size=1, duration_ms=1000,
                 status=AnalysisStatus.COMPLETED, recorded_date=date(2026, month, day),
                 created_at=datetime(2026, month, day, tzinfo=timezone.utc))
    session.add(a); session.flush()
    player = Player(analysis_id=a.id, display_name='A')
    session.add(player); session.flush()
    session.add(Shot(analysis_id=a.id, player_id=player.id, candidate_time_ms=300,
                     sequence_start_ms=0, sequence_end_ms=800, result=result,
                     shot_type=ShotType.SET_SHOT, status=ShotStatus.AUTO, source=ShotSource.AUTO,
                     court_x=25, court_y=22, zone_code='mid_center'))
    session.commit()
    return a


def test_history_places_newer_demos_last_and_keeps_direct_access(store):
    factory, client = store
    with factory() as session:
        real = add(session, '실제 연습', 1)
        demo = add(session, '초록색 더미', 6)
        legacy = add(session, '[테스트용 더미] 확인 필요', 5)
    with client:
        rows = client.get('/api/analyses').json()['analyses']
        assert [r['id'] for r in rows] == [real.id, demo.id, legacy.id]
        assert [r['is_demo'] for r in rows] == [False, True, True]
        assert client.get(f'/api/analyses/{demo.id}/shots').json()['statistics']['overall']['attempts'] == 1


def test_comparison_skips_demos_and_demo_itself_has_no_previous_practice(store):
    factory, _ = store
    with factory() as session:
        previous = add(session, '실제 이전 연습', 1)
        demo = add(session, '빨간색 더미', 4, result=ShotResult.MISSED)
        add(session, '[테스트용 더미] 예전 시연', 3, result=ShotResult.MISSED)
        current = add(session, '실제 현재 연습', 5)
        context = comparison_context(session, current)
        assert context['previous']['id'] == previous.id
        assert next(r for r in context['rows'] if r['key'] == 'overall')['rate_change_pp'] == 0
        assert comparison_context(session, demo)['previous'] is None


def test_monthly_real_results_ignore_demo_only_month_and_pending_demo(store):
    factory, _ = store
    with factory() as session:
        real = add(session, '실제 기록', 1)
        add(session, '결과 선정 더미', 6, result=ShotResult.NEEDS_REVIEW)
        add(session, '위치 선정 더미', 30, month=10)
        stats = monthly_statistics(session)
        assert stats['available_months'] == ['2026-11']
        assert stats['analysis_count'] == 1
        assert stats['statistics']['overall']['attempts'] == 1
        assert stats['shots'][0]['analysis_id'] == real.id
        report = monthly_context(session, 'owner', '2026-11')
        assert report['ready'] and report['analysis_ids'] == [real.id]
        assert report['previous'] is None
        assert report['outcome_unresolved'] == 0


def test_ordinary_title_containing_demo_word_is_not_excluded(store):
    factory, client = store
    with factory() as session:
        real = add(session, '더미 촬영을 참고한 실제 연습', 6)
        assert monthly_statistics(session)['analysis_count'] == 1
    with client:
        row = client.get('/api/analyses').json()['analyses'][0]
        assert row['id'] == real.id and row['is_demo'] is False
