from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.analyses import router
from app.core.config import Settings
from app.db.base import Base
from app.models import Analysis, AnalysisStatus, Player
from app.services.video_registration import video_codec, probe_video, RegistrationError


def make_mp4(path: Path, *, fps: float = 30, frames: int = 12) -> bytes:
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (320, 240)
    )
    assert writer.isOpened()
    for index in range(frames):
        frame = np.full((240, 320, 3), 70 + index, dtype=np.uint8)
        cv2.circle(frame, (80 + index * 4, 100), 14, (20, 120, 230), -1)
        writer.write(frame)
    writer.release()
    return path.read_bytes()


def create_client(tmp_path: Path, **setting_overrides):
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        upload_dir=str(tmp_path / "uploads"),
        work_dir=str(tmp_path / "work"),
        **setting_overrides,
    )
    settings.ensure_storage_directories()
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    app.include_router(router)
    return TestClient(app), engine, settings


def valid_form(**overrides) -> dict[str, str]:
    form = {
        "title": "  저녁 개인 연습  ",
        "play_type": "solo",
        "contains_others": "false",
        "consent_confirmed": "false",
        "player_name": "",
    }
    form.update(overrides)
    return form


def test_valid_mp4_is_saved_with_metadata_and_default_player(tmp_path) -> None:
    video = make_mp4(tmp_path / "source.mp4")
    client, engine, settings = create_client(tmp_path)
    with client:
        response = client.post(
            "/api/analyses",
            data=valid_form(),
            files={"video": ("practice.mp4", video, "video/mp4")},
        )
        assert response.status_code == 201
        payload = response.json()
        assert payload["title"] == "저녁 개인 연습"
        assert payload["status"] == "setup_required"
        assert payload["duration_ms"] > 0
        assert payload["width"] == 320
        assert payload["height"] == 240
        assert payload["warnings_accepted"] is False
        assert payload["ai_notice_confirmed"] is False

        accepted = client.post(f"/api/analyses/{payload['id']}/accept-warnings")
        assert accepted.status_code == 200
        assert accepted.json()["warnings_accepted"] is True

    with Session(engine) as session:
        analysis = session.scalar(select(Analysis))
        player = session.scalar(select(Player))
        assert analysis is not None
        assert analysis.status is AnalysisStatus.SETUP_REQUIRED
        assert analysis.warnings_accepted is True
        assert player is not None and player.display_name == "선수 A"
        assert (settings.upload_path / analysis.id / "original.mp4").is_file()
        playback = settings.upload_path / analysis.id / "playback.mp4"
        assert playback.is_file()
        assert video_codec(playback) in {"h264", "avc1", "x264"}
    engine.dispose()


def test_multiplayer_name_is_saved_as_first_player(tmp_path) -> None:
    video = make_mp4(tmp_path / "source.mp4")
    client, engine, _ = create_client(tmp_path)
    with client:
        response = client.post(
            "/api/analyses",
            data=valid_form(play_type="one_on_one", player_name="  민수  "),
            files={"video": ("practice.mp4", video, "video/mp4")},
        )
        assert response.status_code == 201
        players = client.get(f"/api/analyses/{response.json()['id']}/players")
        assert players.status_code == 200
        assert [player["display_name"] for player in players.json()["players"]] == ["민수"]
    engine.dispose()


@pytest.mark.parametrize(
    ("form", "expected_code"),
    [
        (valid_form(title="   "), "TITLE_INVALID"),
        (
            valid_form(contains_others="true", consent_confirmed="false"),
            "CONSENT_REQUIRED",
        ),
    ],
)
def test_missing_required_information_does_not_create_record(
    tmp_path, form: dict[str, str], expected_code: str
) -> None:
    video = make_mp4(tmp_path / "source.mp4")
    client, engine, settings = create_client(tmp_path)
    with client:
        response = client.post(
            "/api/analyses", data=form, files={"video": ("practice.mp4", video, "video/mp4")}
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == expected_code
    with Session(engine) as session:
        assert session.scalar(select(Analysis)) is None
    assert list(settings.upload_path.iterdir()) == []
    engine.dispose()


def test_fake_mp4_is_rejected_and_partial_file_is_removed(tmp_path) -> None:
    client, engine, settings = create_client(tmp_path)
    with client:
        response = client.post(
            "/api/analyses",
            data=valid_form(),
            files={"video": ("fake.mp4", b"this is not a video", "video/mp4")},
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "FILE_INVALID"
    assert list(settings.upload_path.iterdir()) == []
    engine.dispose()


def test_file_size_and_duration_limits_are_rejected(tmp_path) -> None:
    video = make_mp4(tmp_path / "source.mp4")
    size_client, size_engine, size_settings = create_client(tmp_path / "size", max_video_bytes=10)
    with size_client:
        size_response = size_client.post(
            "/api/analyses",
            data=valid_form(),
            files={"video": ("large.mp4", video, "video/mp4")},
        )
    assert size_response.status_code == 413
    assert size_response.json()["detail"]["code"] == "FILE_LIMIT"
    assert list(size_settings.upload_path.iterdir()) == []
    size_engine.dispose()

    duration_client, duration_engine, duration_settings = create_client(
        tmp_path / "duration", max_video_minutes=0
    )
    with duration_client:
        duration_response = duration_client.post(
            "/api/analyses",
            data=valid_form(),
            files={"video": ("long.mp4", video, "video/mp4")},
        )
    assert duration_response.status_code == 413
    assert duration_response.json()["detail"]["code"] == "FILE_LIMIT"
    assert list(duration_settings.upload_path.iterdir()) == []
    duration_engine.dispose()


@pytest.mark.parametrize('limit_minutes,frames,accepted', [
    (40, 108000, True),  # 30 minutes at 60fps
    (40, 144000, True),  # exactly 40 minutes
    (40, 144001, False), # one frame beyond the limit
    (25, 108000, False), # custom limit must also appear in the message
])
def test_long_video_metadata_boundary_and_configured_error(monkeypatch, limit_minutes, frames, accepted):
    class Capture:
        released = False
        def isOpened(self): return True
        def get(self, prop):
            return {cv2.CAP_PROP_FPS: 60, cv2.CAP_PROP_FRAME_COUNT: frames,
                    cv2.CAP_PROP_FRAME_WIDTH: 1920, cv2.CAP_PROP_FRAME_HEIGHT: 1080}[prop]
        def set(self, *args): return True
        def read(self): return False, None
        def release(self): self.released = True
    capture = Capture()
    monkeypatch.setattr('app.services.video_registration.cv2.VideoCapture', lambda *a: capture)
    if accepted:
        assert probe_video(Path('metadata-boundary.mp4'), limit_minutes*60000).duration_ms == round(frames/60*1000)
    else:
        with pytest.raises(RegistrationError) as caught:
            probe_video(Path('metadata-boundary.mp4'), limit_minutes*60000)
        assert caught.value.status_code == 413
        assert f'{limit_minutes}' in caught.value.message
    assert capture.released
