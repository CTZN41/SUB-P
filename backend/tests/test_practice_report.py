from datetime import date, datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.analyses import router
from app.core.config import Settings
from app.db.base import Base
from app.models import Analysis, AnalysisStatus, PlayType, Player, Shot, ShotResult, ShotSource, ShotStatus, ShotType
from app.services.authentication import require_analysis_user
from app.services import practice_report as reports


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'reports.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(_env_file=None, app_env="test", work_dir=str(tmp_path / "work"), openai_api_key="test-only")
    yield factory, settings
    engine.dispose()


def add_practice(session, *, owner="a", day=2, play_type=PlayType.SOLO, status=AnalysisStatus.COMPLETED, results=(ShotResult.MADE,)):
    analysis = Analysis(title="Private title", owner_id=owner, play_type=play_type, original_name="test.mp4",
                        stored_path="test", file_size=100, duration_ms=1000, status=status,
                        created_at=datetime(2026, 11, day, tzinfo=timezone.utc), recorded_date=date(2026, 11, day))
    session.add(analysis)
    session.flush()
    player = Player(analysis_id=analysis.id, display_name="Private name")
    session.add(player)
    session.flush()
    for index, result in enumerate(results):
        session.add(Shot(analysis_id=analysis.id, player_id=player.id, candidate_time_ms=100 + index,
                         sequence_start_ms=0, sequence_end_ms=500, result=result, shot_type=ShotType.SET_SHOT,
                         status=ShotStatus.AUTO, source=ShotSource.AUTO, court_x=25, court_y=22, zone_code="mid_center"))
    session.commit()
    return analysis


def fake_text(context, settings):
    return {"content": {"summary": "정면 슛을 연습했습니다.",
                        "findings": [{"evidence_key": "overall", "text": "이번 기록을 확인했습니다."}],
                        "next_practice": [{"evidence_key": "overall", "text": "같은 위치에서 다시 연습해 보세요."}]},
            "model": settings.openai_vision_model, "generated_at": "2026-11-05T00:00:00Z", "elapsed_seconds": 1,
            "api_usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}}


def test_previous_is_earlier_same_owner_and_play_type(store):
    factory, _ = store
    with factory() as session:
        previous = add_practice(session, day=1, results=(ShotResult.MADE, ShotResult.MISSED))
        current = add_practice(session, day=3)
        add_practice(session, owner="other", day=2)
        add_practice(session, day=2, play_type=PlayType.ONE_ON_ONE)
        add_practice(session, day=2, status=AnalysisStatus.FAILED)
        add_practice(session, day=4)
        context = reports.comparison_context(session, current)
        assert context["previous"]["id"] == previous.id
        overall = next(row for row in context["rows"] if row["key"] == "overall")
        assert overall["rate_change_pp"] == 50
        assert overall["attempt_change"] == -1
        assert context["same_source_metadata"]
        assert any(row["kind"] == "zone_type" for row in context["rows"])


def test_unknown_position_and_result_have_separate_denominators(store):
    factory, _ = store
    with factory() as session:
        current = add_practice(session, results=(ShotResult.MADE, ShotResult.MISSED, ShotResult.NEEDS_REVIEW))
        current.shots[0].court_x = None
        current.shots[0].court_y = None
        # 오래된 구역 이름 대신 실제 저장 좌표에서 구역을 계산한다.
        current.shots[1].zone_code = "three_center"
        session.commit()
        context = reports.comparison_context(session, current)
        rows = {row["key"]: row for row in context["rows"]}
        assert context["previous"] is None
        assert context["position_unresolved"] == 1
        assert rows["overall"]["current"]["success_rate"] == 50
        assert rows["overall"]["current"]["unresolved"] == 1
        assert rows["type/set_shot"]["current"]["attempts"] == 3
        assert rows["zone/mid_center"]["current"]["attempts"] == 2
        assert rows["zone/mid_center"]["current"]["success_rate"] == 0
        assert rows["zone/mid_center"]["rate_change_pp"] is None


def test_confirmed_non_shot_is_excluded_but_user_confirmation_is_kept(store):
    factory, _ = store
    with factory() as session:
        current = add_practice(session, results=(ShotResult.MADE, ShotResult.NEEDS_REVIEW))
        current.shots[1].confidence_data = {"shot_gate": {"verdict": "not_shot", "confidence": .97}}
        session.commit()
        assert reports.comparison_context(session, current)["rows"][0]["current"]["attempts"] == 1
        current.shots[1].status = ShotStatus.USER_CONFIRMED
        session.commit()
        assert reports.comparison_context(session, current)["rows"][0]["current"]["attempts"] == 2


def test_generation_cached_after_restart_and_invalidated_after_edit(store, monkeypatch):
    factory, settings = store
    calls = []
    monkeypatch.setattr(reports, "_generate_text", lambda context, settings: calls.append(context) or fake_text(context, settings))
    with factory() as session:
        current = add_practice(session)
        analysis_id = current.id
        first = reports.generate_report(session, current, settings)
        assert first["report"]["api_usage"]["total_tokens"] == 150
    with factory() as session:
        current = session.get(Analysis, analysis_id)
        assert reports.generate_report(session, current, settings)["stale"] is False
        assert len(calls) == 1
        current.shots[0].result = ShotResult.MISSED
        session.commit()
        assert reports.report_view(reports.comparison_context(session, current), settings, analysis_id)["stale"] is True
        reports.generate_report(session, current, settings)
        assert len(calls) == 2


def test_generation_rejects_snapshot_changed_during_call(store, monkeypatch):
    factory, settings = store
    with factory() as session:
        current = add_practice(session)
        analysis_id = current.id
        def change_during_call(context, settings):
            with factory() as writer:
                shot = writer.get(Analysis, analysis_id).shots[0]
                shot.result = ShotResult.MISSED
                writer.commit()
            return fake_text(context, settings)
        monkeypatch.setattr(reports, "_generate_text", change_during_call)
        with pytest.raises(reports.ReportError, match="생성 중"):
            reports.generate_report(session, current, settings)
        assert not reports._cache_path(settings, analysis_id).exists()


def test_duplicate_lock_and_failure_release(store, monkeypatch):
    factory, settings = store
    with factory() as session:
        current = add_practice(session)
        lock_path = reports._cache_path(settings, current.id).with_suffix(".lock")
        with reports._generation_lock(lock_path):
            with pytest.raises(reports.ReportError, match="생성하고"):
                reports.generate_report(session, current, settings)
        def fail(context, settings):
            raise reports.ReportError(502, "TEST", "일시 오류")
        monkeypatch.setattr(reports, "_generate_text", fail)
        with pytest.raises(reports.ReportError, match="일시 오류"):
            reports.generate_report(session, current, settings)
        monkeypatch.setattr(reports, "_generate_text", fake_text)
        assert reports.generate_report(session, current, settings)["report"]


def test_api_get_is_free_and_other_account_is_hidden(store, monkeypatch):
    factory, settings = store
    settings.auth_required = True
    with factory() as session:
        own = add_practice(session)
        other = add_practice(session, owner="other")
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = factory
    app.include_router(router)
    async def signed_in(request: Request):
        request.state.auth_user_id = "a"
    app.dependency_overrides[require_analysis_user] = signed_in
    monkeypatch.setattr(reports, "_generate_text", lambda *args: pytest.fail("GET must not call AI"))
    with TestClient(app) as client:
        assert client.get(f"/api/analyses/{own.id}/practice-report").status_code == 200
        for method in (client.get, client.post):
            assert method(f"/api/analyses/{other.id}/practice-report").status_code == 404
        calls = []
        monkeypatch.setattr(reports, "_generate_text", lambda context, settings: calls.append(context) or fake_text(context, settings))
        assert client.post(f"/api/analyses/{own.id}/practice-report").status_code == 200
        assert client.post(f"/api/analyses/{own.id}/practice-report").status_code == 200
        assert len(calls) == 1


def test_prompt_uses_statistics_only_and_unknown_evidence_is_rejected(store, monkeypatch):
    factory, settings = store
    captured = []
    class FakeClient:
        def __init__(self, **kwargs):
            self.responses = self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def parse(self, **kwargs):
            captured.append(kwargs)
            text = fake_text(None, settings)["content"]
            text["findings"][0]["evidence_key"] = "invented"
            return SimpleNamespace(status="completed", output_parsed=reports.ReportText.model_validate(text))
    monkeypatch.setattr(reports, "OpenAI", FakeClient)
    with factory() as session:
        current = add_practice(session)
        with pytest.raises(reports.ReportError, match="통계 근거"):
            reports.generate_report(session, current, settings)
    prompt = str(captured)
    assert "Private title" not in prompt
    assert "Private name" not in prompt
    assert "test.mp4" not in prompt
    assert captured[0]["store"] is False
    assert captured[0]["model"] == settings.openai_vision_model


def test_empty_analysis_is_not_generated(store, monkeypatch):
    factory, settings = store
    monkeypatch.setattr(reports, "_generate_text", lambda *args: pytest.fail("No shots"))
    with factory() as session:
        current = add_practice(session, results=())
        with pytest.raises(reports.ReportError, match="슛 기록"):
            reports.generate_report(session, current, settings)


def test_removed_group_keeps_stale_notice_and_corrupt_cache_is_ignored(store, monkeypatch):
    factory, settings = store
    monkeypatch.setattr(reports, "_generate_text", fake_text)
    with factory() as session:
        current = add_practice(session)
        reports.generate_report(session, current, settings)
        current.shots[0].shot_type = ShotType.LAYUP
        session.commit()
        view = reports.report_view(reports.comparison_context(session, current), settings, current.id)
        assert view["stale"] is True
        cache = reports._cache_path(settings, current.id)
        cache.write_text('{"content": {}}', encoding="utf-8")
        assert reports.report_view(reports.comparison_context(session, current), settings, current.id)["report"] is None


def test_previous_edit_or_new_eligible_practice_invalidates_report(store, monkeypatch):
    factory, settings = store
    monkeypatch.setattr(reports, "_generate_text", fake_text)
    with factory() as session:
        previous = add_practice(session, day=1)
        current = add_practice(session, day=3)
        reports.generate_report(session, current, settings)
        previous.shots[0].result = ShotResult.MISSED
        session.commit()
        assert reports.report_view(reports.comparison_context(session, current), settings, current.id)["stale"]
        reports.generate_report(session, current, settings)
        add_practice(session, day=2)
        assert reports.report_view(reports.comparison_context(session, current), settings, current.id)["stale"]
