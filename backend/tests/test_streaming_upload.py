import asyncio
import json
from pathlib import Path
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect, Request

from app.api.uploads import router
from app.models import Analysis, Player
from app.services.streaming_upload import (
    UploadRegistration, create_upload, read_manifest, receive_upload, recover_uploads,
    save_manifest, upload_lock,
)
from app.services.upload_body_limit import UploadBodyLimitMiddleware
from test_video_registration import create_client, make_mp4


def make_client(tmp_path, **settings):
    client, engine, config = create_client(tmp_path, **settings)
    client.app.include_router(router)
    client.app.add_middleware(UploadBodyLimitMiddleware)
    return TestClient(client.app, raise_server_exceptions=False), engine, config


def registration(size, **changes):
    return dict(title="Streaming test", play_type="solo", recorded_date="2026-11-04",
                original_name="test.mp4", file_size=size, contains_others=False,
                consent_confirmed=False, **changes)


def prepare(client, size):
    response = client.post("/api/uploads", json=registration(size))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_normal_entry_point_renames_same_file_and_repeated_complete_is_idempotent(tmp_path):
    video = make_mp4(tmp_path / "source.mp4")
    client, engine, config = make_client(tmp_path)
    with client:
        upload_id = prepare(client, len(video))
        partial = config.upload_path / ".incoming" / upload_id / "original.part"
        assert client.put(f"/api/uploads/{upload_id}", content=video).status_code == 200
        file_id = partial.stat().st_ino
        assert partial.read_bytes() == video
        assert client.get(f"/api/analyses/{upload_id}").status_code == 404
        finished = client.post(f"/api/uploads/{upload_id}/complete")
        assert finished.status_code == 200, finished.text
        assert finished.json()["file_size"] == len(video)
        assert finished.json()["recorded_date"] == "2026-11-04"
        stored = config.upload_path / upload_id / "original.mp4"
        assert stored.stat().st_ino == file_id and stored.read_bytes() == video
        assert not partial.exists()
        assert (stored.parent / "playback.mp4").is_file()
        assert client.post(f"/api/uploads/{upload_id}/complete").json()["id"] == upload_id
        assert client.get(f"/api/analyses/{upload_id}/video", headers={"Range": "bytes=0-31"}).status_code == 206
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(Analysis)) == 1
            assert db.scalar(select(func.count()).select_from(Player)) == 1
        assert client.delete(f"/api/analyses/{upload_id}").status_code == 204
        assert not partial.parent.exists()
        assert client.post(f"/api/uploads/{upload_id}/complete").status_code == 404
    engine.dispose()


@pytest.mark.parametrize("changes,status", [
    ({"file_size": 101}, 413), ({"title": "   "}, 400),
    ({"original_name": "../test.mp4"}, 400), ({"recorded_date": "bad"}, 422),
])
def test_preflight_rejects_invalid_metadata_without_video_transfer(tmp_path, changes, status):
    client, engine, config = make_client(tmp_path, max_video_bytes=100)
    values = registration(100)
    values.update(changes)
    assert client.post("/api/uploads", json=values).status_code == status
    assert list(config.upload_path.iterdir()) == []
    engine.dispose()


def test_actual_stream_size_is_checked_and_same_id_can_retry_after_failure(tmp_path):
    video = make_mp4(tmp_path / "source.mp4")
    client, engine, config = make_client(tmp_path, max_video_bytes=len(video))
    upload_id = prepare(client, len(video))
    folder = config.upload_path / ".incoming" / upload_id
    # Iterator sends without Content-Length, exercising counted reception.
    response = client.put(f"/api/uploads/{upload_id}", content=iter([video, b"extra"]))
    assert response.status_code == 413
    assert not (folder / "original.part").exists()
    assert read_manifest(folder, None)["state"] == "interrupted"
    assert client.put(f"/api/uploads/{upload_id}", content=video).status_code == 200
    assert client.post(f"/api/uploads/{upload_id}/complete").status_code == 200
    engine.dispose()


def test_short_body_and_false_length_are_rejected(tmp_path):
    client, engine, config = make_client(tmp_path)
    upload_id = prepare(client, 1000)
    assert client.put(f"/api/uploads/{upload_id}", content=b"small").status_code == 400
    assert client.put(f"/api/uploads/{upload_id}", content=iter([b"small"])).status_code == 400
    assert not (config.upload_path / ".incoming" / upload_id / "original.part").exists()
    engine.dispose()


def test_disconnect_after_a_written_chunk_removes_partial_file(tmp_path):
    client, engine, config = make_client(tmp_path)
    data = UploadRegistration(**registration(3 * 1024**2))
    upload_id = create_upload(config, None, data)["id"]
    messages = iter([{"type": "http.request", "body": b"x" * 1024**2, "more_body": True},
                     {"type": "http.disconnect"}])

    async def receive():
        return next(messages)

    request = Request({"type": "http", "method": "PUT", "headers": []}, receive)
    with pytest.raises(ClientDisconnect):
        asyncio.run(receive_upload(request, config, None, upload_id))
    folder = config.upload_path / ".incoming" / upload_id
    assert not (folder / "original.part").exists()
    assert read_manifest(folder, None)["state"] == "interrupted"
    engine.dispose()


@pytest.mark.parametrize("after_commit", [False, True])
def test_db_failure_before_or_after_commit_preserves_file_and_retry_returns_one_record(tmp_path, monkeypatch, after_commit):
    video = make_mp4(tmp_path / "source.mp4")
    client, engine, config = make_client(tmp_path)
    upload_id = prepare(client, len(video))
    assert client.put(f"/api/uploads/{upload_id}", content=video).status_code == 200
    original_commit = Session.commit

    def failed_commit(db):
        if after_commit:
            original_commit(db)
        raise RuntimeError("simulated lost DB connection")

    monkeypatch.setattr(Session, "commit", failed_commit)
    assert client.post(f"/api/uploads/{upload_id}/complete").status_code == 500
    assert (config.upload_path / upload_id / "original.mp4").read_bytes() == video
    monkeypatch.setattr(Session, "commit", original_commit)
    assert client.post(f"/api/uploads/{upload_id}/complete").status_code == 200
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Analysis)) == 1
        assert db.scalar(select(func.count()).select_from(Player)) == 1
    engine.dispose()


def test_parallel_upload_is_rejected_and_crash_recovery_preserves_finalizing_files(tmp_path):
    client, engine, config = make_client(tmp_path)
    upload_id = prepare(client, 1000)
    folder = config.upload_path / ".incoming" / upload_id
    with upload_lock(folder):
        assert client.put(f"/api/uploads/{upload_id}", content=b"x" * 1000).status_code == 409
    manifest = read_manifest(folder, None)
    manifest["state"] = "receiving"
    (folder / "original.part").write_bytes(b"incomplete")
    save_manifest(folder, manifest)
    recover_uploads(config)
    assert read_manifest(folder, None)["state"] == "interrupted"
    assert not (folder / "original.part").exists()
    manifest["state"] = "received"
    manifest["created_at"] = time.time() - 49 * 3600
    (folder / "original.part").write_bytes(b"expired")
    save_manifest(folder, manifest)
    recover_uploads(config)
    assert json.loads((folder / "manifest.json").read_text())["state"] == "expired"
    # Uncertain finalization survives restarts even when the receive is old.
    manifest["state"] = "finalizing"
    (folder / "original.part").write_bytes(b"valid-file-awaiting-db")
    save_manifest(folder, manifest)
    recover_uploads(config)
    assert (folder / "original.part").read_bytes() == b"valid-file-awaiting-db"
    engine.dispose()


def test_upload_session_owner_cannot_be_overridden_by_another_login(tmp_path, monkeypatch):
    client, engine, config = make_client(tmp_path)
    client.app.state.settings = config.model_copy(update={"auth_required": True})
    owners = {"a": str(uuid4()), "b": str(uuid4())}

    async def verified(settings, token):
        return {"id": owners[token]}

    monkeypatch.setattr("app.services.authentication.verify_token", verified)
    response = client.post("/api/uploads", json=registration(10), headers={"Authorization": "Bearer a"})
    assert response.status_code == 201
    upload_id = response.json()["id"]
    assert client.put(f"/api/uploads/{upload_id}", content=b"x" * 10,
                      headers={"Authorization": "Bearer b"}).status_code == 404
    assert client.post(f"/api/uploads/{upload_id}/complete",
                       headers={"Authorization": "Bearer b"}).status_code == 404
    assert not (config.upload_path / ".incoming" / upload_id / "original.part").exists()
    engine.dispose()


def test_legacy_size_limit_catches_chunked_body_before_multipart_copy(tmp_path):
    client, engine, config = make_client(tmp_path, max_video_bytes=100)
    header = b'--boundary\r\nContent-Disposition: form-data; name="video"; filename="x.mp4"\r\n\r\n'
    body = header + b"x" * (128 * 1024) + b"\r\n--boundary--\r\n"
    for content in (body, iter([body])):
        response = client.post("/api/analyses", content=content,
                               headers={"Content-Type": "multipart/form-data; boundary=boundary"})
        assert response.status_code == 413, response.text
        assert response.json()["detail"]["code"] == "FILE_LIMIT"
    assert not list(config.upload_path.iterdir())
    engine.dispose()


def test_file_size_postgresql_column_can_represent_10gb():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable
    assert "file_size BIGINT" in str(CreateTable(Analysis.__table__).compile(dialect=postgresql.dialect()))


def test_bigint_migration_preserves_rows_in_an_isolated_database(tmp_path):
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261104_0008_video_size_bigint.py"
    spec = importlib.util.spec_from_file_location("size_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE analyses (id TEXT PRIMARY KEY, file_size INTEGER NOT NULL)"))
        connection.execute(text("INSERT INTO analyses VALUES ('kept', 10000000000)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert str(inspect(connection).get_columns("analyses")[1]["type"]) == "BIGINT"
        assert connection.execute(text("SELECT file_size FROM analyses WHERE id='kept'")).scalar() == 10_000_000_000
    engine.dispose()


def test_disk_full_during_stream_returns_507_and_cleans_partial(tmp_path, monkeypatch):
    import errno
    import anyio
    client, engine, config = make_client(tmp_path)
    upload_id = prepare(client, 2 * 1024**2)
    original_run = anyio.to_thread.run_sync

    async def disk_full(function, *args, **kwargs):
        stream = getattr(function, "__self__", None)
        if getattr(function, "__name__", None) == "write" and str(getattr(stream, "name", "")).endswith("original.part"):
            raise OSError(errno.ENOSPC, "simulated full disk")
        return await original_run(function, *args, **kwargs)

    monkeypatch.setattr(anyio.to_thread, "run_sync", disk_full)
    response = client.put(f"/api/uploads/{upload_id}", content=b"x" * (2 * 1024**2))
    assert response.status_code == 507
    assert not (config.upload_path / ".incoming" / upload_id / "original.part").exists()
    engine.dispose()


def test_legacy_post_commit_refresh_failure_preserves_committed_video(tmp_path, monkeypatch):
    video = make_mp4(tmp_path / "source.mp4")
    client, engine, config = make_client(tmp_path)
    original_refresh = Session.refresh

    def failed_refresh(*args, **kwargs):
        raise RuntimeError("simulated post-commit refresh failure")

    monkeypatch.setattr(Session, "refresh", failed_refresh)
    response = client.post("/api/analyses", data={"title": "kept", "play_type": "solo",
        "contains_others": "false", "consent_confirmed": "false"},
        files={"video": ("test.mp4", video, "video/mp4")})
    assert response.status_code == 500
    monkeypatch.setattr(Session, "refresh", original_refresh)
    with Session(engine) as db:
        analysis = db.scalar(select(Analysis))
        assert analysis is not None
        assert (config.upload_path / analysis.id / "original.mp4").read_bytes() == video
    engine.dispose()
