"""Receive into one owned file and finalize it without a second full copy."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
import errno
import json
import os
from pathlib import Path
import shutil
import time
from uuid import UUID, uuid4

import anyio
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.config import Settings
from app.models import Analysis, PlayType
from app.schemas.analyses import AnalysisRegistrationResponse
from app.services.video_registration import (
    CHUNK_SIZE, RegistrationError, build_registered_analysis, has_mp4_signature,
    probe_video, validate_confirmations, validate_title,
)

SESSION_LIFETIME_SECONDS = 48 * 3600


class UploadRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=100)
    play_type: PlayType
    recorded_date: date
    original_name: str = Field(min_length=1, max_length=255)
    file_size: int = Field(gt=0)
    player_name: str | None = Field(default=None, max_length=100)
    contains_others: bool
    consent_confirmed: bool
    ai_notice_confirmed: bool = False


def save_manifest(folder: Path, manifest: dict) -> None:
    temporary = folder / f"manifest-{uuid4()}.tmp"
    try:
        temporary.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, folder / "manifest.json")
    finally:
        temporary.unlink(missing_ok=True)


def session_folder(settings: Settings, upload_id: str) -> Path:
    try:
        if str(UUID(upload_id)) != upload_id:
            raise ValueError
    except ValueError:
        raise RegistrationError("UPLOAD_NOT_FOUND", "업로드 정보를 찾을 수 없습니다.", 404) from None
    folder = settings.upload_path / ".incoming" / upload_id
    if not (folder / "manifest.json").is_file():
        raise RegistrationError("UPLOAD_NOT_FOUND", "업로드 정보를 찾을 수 없습니다.", 404)
    return folder


def read_manifest(folder: Path, owner_id: str | None) -> dict:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest["owner_id"] != owner_id:
        raise RegistrationError("UPLOAD_NOT_FOUND", "업로드 정보를 찾을 수 없습니다.", 404)
    if manifest["state"] == "expired":
        raise RegistrationError("UPLOAD_EXPIRED", "업로드 시간이 만료됐습니다. 다시 등록해 주세요.", 410)
    return manifest


@contextmanager
def upload_lock(folder: Path):
    """An OS lock is released after a crash and works across server processes."""
    with (folder / "upload.lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RegistrationError("UPLOAD_BUSY", "같은 영상의 업로드를 처리 중입니다. 잠시 후 다시 시도해 주세요.", 409) from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def create_upload(settings: Settings, owner_id: str | None, data: UploadRegistration) -> dict:
    data.title = validate_title(data.title)
    validate_confirmations(contains_others=data.contains_others, consent_confirmed=data.consent_confirmed)
    if Path(data.original_name).name != data.original_name or "\\" in data.original_name or Path(data.original_name).suffix.lower() != ".mp4":
        raise RegistrationError("FILE_INVALID", "MP4 영상만 사용할 수 있습니다.")
    if data.file_size > settings.max_video_bytes:
        raise RegistrationError("FILE_LIMIT", f"영상 파일은 최대 {settings.max_video_bytes / 1024**3:g}GB까지 등록할 수 있습니다.", 413)
    if shutil.disk_usage(settings.upload_path).free < data.file_size + 64 * 1024**2:
        raise RegistrationError("STORAGE_LOW", "저장 공간이 부족합니다. 공간을 확보한 뒤 다시 시도해 주세요.", 507)
    upload_id = str(uuid4())
    folder = settings.upload_path / ".incoming" / upload_id
    folder.mkdir(parents=True, exist_ok=False)
    save_manifest(folder, {"id": upload_id, "owner_id": owner_id, "created_at": time.time(),
                           "state": "prepared", "registration": data.model_dump(mode="json")})
    return {"id": upload_id, "state": "prepared"}


def reset_partial(folder: Path, manifest: dict, state: str) -> None:
    (folder / "original.part").unlink(missing_ok=True)
    manifest["state"] = state
    save_manifest(folder, manifest)


async def receive_upload(request: Request, settings: Settings, owner_id: str | None, upload_id: str) -> dict:
    folder = session_folder(settings, upload_id)
    read_manifest(folder, owner_id)
    with upload_lock(folder):
        manifest = read_manifest(folder, owner_id)
        if manifest["state"] in ("received", "finalizing", "completed"):
            return {"id": upload_id, "state": manifest["state"]}
        expected = manifest["registration"]["file_size"]
        length = request.headers.get("content-length")
        if length is not None and (not length.isdecimal() or int(length) != expected):
            raise RegistrationError("UPLOAD_SIZE_MISMATCH", "선택한 파일 크기와 전송 크기가 다릅니다.")
        manifest["state"] = "receiving"
        save_manifest(folder, manifest)
        partial = folder / "original.part"
        written = 0
        buffer = bytearray()
        try:
            with partial.open("wb") as output:
                async for chunk in request.stream():
                    written += len(chunk)
                    if written > settings.max_video_bytes:
                        raise RegistrationError("FILE_LIMIT", "영상 파일의 허용 용량을 초과했습니다.", 413)
                    if written > expected:
                        raise RegistrationError("UPLOAD_SIZE_MISMATCH", "선택한 파일 크기와 전송 크기가 다릅니다.")
                    buffer.extend(chunk)
                    if len(buffer) >= CHUNK_SIZE:
                        await anyio.to_thread.run_sync(output.write, bytes(buffer))
                        buffer.clear()
                if written != expected:
                    raise RegistrationError("UPLOAD_INCOMPLETE", "영상 전송이 끝나지 않았습니다. 다시 등록해 주세요.")
                if buffer:
                    await anyio.to_thread.run_sync(output.write, bytes(buffer))
                await anyio.to_thread.run_sync(output.flush)
                await anyio.to_thread.run_sync(os.fsync, output.fileno())
            if not has_mp4_signature(partial):
                raise RegistrationError("FILE_INVALID", "확장자만 MP4인 파일은 사용할 수 없습니다.")
            manifest["state"] = "received"
            save_manifest(folder, manifest)
            return {"id": upload_id, "state": "received", "bytes": written}
        except BaseException as error:
            reset_partial(folder, manifest, "interrupted")
            if isinstance(error, OSError) and error.errno == errno.ENOSPC:
                raise RegistrationError("STORAGE_LOW", "저장 공간이 부족합니다. 공간을 확보한 뒤 다시 시도해 주세요.", 507) from error
            raise


def complete_upload(session: Session, settings: Settings, owner_id: str | None, upload_id: str) -> dict:
    folder = session_folder(settings, upload_id)
    read_manifest(folder, owner_id)
    with upload_lock(folder):
        manifest = read_manifest(folder, owner_id)
        existing = session.get(Analysis, upload_id)
        if existing is not None:
            if existing.owner_id != owner_id:
                raise RegistrationError("UPLOAD_NOT_FOUND", "업로드 정보를 찾을 수 없습니다.", 404)
            manifest["state"] = "completed"
            save_manifest(folder, manifest)
            return AnalysisRegistrationResponse.model_validate(existing).model_dump(mode="json")
        if manifest["state"] == "completed":
            # Deleting a registered analysis must not let a stale upload recreate it.
            raise RegistrationError("UPLOAD_NOT_FOUND", "등록된 영상이 삭제됐습니다.", 410)
        if manifest["state"] not in ("received", "finalizing"):
            raise RegistrationError("UPLOAD_INCOMPLETE", "영상 전송을 먼저 완료해 주세요.", 409)
        # Release the read connection before video probing or a long HEVC conversion.
        session.rollback()
        data = UploadRegistration.model_validate(manifest["registration"])
        final_folder = settings.upload_path / upload_id
        stored = final_folder / "original.mp4"
        partial = folder / "original.part"
        source = stored if stored.is_file() else partial
        try:
            if not source.is_file() or source.stat().st_size != data.file_size:
                raise RegistrationError("UPLOAD_INCOMPLETE", "영상 전송을 다시 확인해 주세요.", 409)
            metadata = probe_video(source, settings.max_video_minutes * 60_000)
            manifest["state"] = "finalizing"
            save_manifest(folder, manifest)
            if source == partial:
                final_folder.mkdir(exist_ok=True)
                os.rename(partial, stored)
            analysis = build_registered_analysis(
                settings, analysis_id=upload_id, owner_id=owner_id, title=data.title,
                play_type=data.play_type, recorded_date=data.recorded_date,
                player_name=data.player_name, contains_others=data.contains_others,
                consent_confirmed=data.consent_confirmed, ai_notice_confirmed=data.ai_notice_confirmed,
                original_name=data.original_name, stored_file=stored,
                file_size=data.file_size, metadata=metadata,
            )
        except RegistrationError:
            for path in (partial, stored, final_folder / "playback.mp4"):
                path.unlink(missing_ok=True)
            if final_folder.exists():
                final_folder.rmdir()
            manifest["state"] = "failed"
            save_manifest(folder, manifest)
            raise
        try:
            session.add(analysis)
            session.flush()
            # Serialize before commit so a post-commit refresh cannot remove files.
            payload = AnalysisRegistrationResponse.model_validate(analysis).model_dump(mode="json")
            session.commit()
        except Exception:
            session.rollback()
            # Keep the valid file and finalizing manifest for an idempotent retry.
            raise
        manifest["state"] = "completed"
        save_manifest(folder, manifest)
        return payload


def recover_uploads(settings: Settings) -> None:
    """Recover abandoned receives at startup; preserve uncertain DB finalization."""
    incoming = settings.upload_path / ".incoming"
    if not incoming.is_dir():
        return
    for folder in incoming.iterdir():
        try:
            folder = session_folder(settings, folder.name)
            with upload_lock(folder):
                manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
                if manifest["state"] == "receiving":
                    reset_partial(folder, manifest, "interrupted")
                if manifest["state"] in ("prepared", "received", "interrupted") and time.time() - manifest["created_at"] > SESSION_LIFETIME_SECONDS:
                    reset_partial(folder, manifest, "expired")
        except (RegistrationError, OSError, ValueError, KeyError):
            continue
