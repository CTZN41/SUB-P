"""MP4 원본을 검사하고 분석 등록 정보와 함께 안전하게 저장한다."""

from __future__ import annotations

import errno
import shutil
from dataclasses import dataclass
from pathlib import Path

import cv2
from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Analysis, AnalysisStatus, Player, PlayType, new_id


CHUNK_SIZE = 1024 * 1024


class RegistrationError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class VideoMetadata:
    duration_ms: int
    width: int
    height: int
    fps: float
    warnings: list[str]


def validate_title(raw_title: str) -> str:
    title = raw_title.strip()
    if not 1 <= len(title) <= 100:
        raise RegistrationError("TITLE_INVALID", "분석 이름은 공백을 제외하고 1~100자로 입력해 주세요.")
    return title


def validate_confirmations(
    *, contains_others: bool, consent_confirmed: bool, ai_notice_confirmed: bool
) -> None:
    if contains_others and not consent_confirmed:
        raise RegistrationError(
            "CONSENT_REQUIRED", "다른 사람이 포함된 영상은 촬영·분석 동의 확인이 필요합니다."
        )
    if not ai_notice_confirmed:
        raise RegistrationError(
            "AI_NOTICE_REQUIRED", "OpenAI가 슛 후보 장면을 처리할 수 있다는 안내를 확인해 주세요."
        )


def has_mp4_signature(path: Path) -> bool:
    with path.open("rb") as stream:
        return b"ftyp" in stream.read(64)


def probe_video(path: Path, max_duration_ms: int) -> VideoMetadata:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RegistrationError("FILE_INVALID", "영상 파일을 읽을 수 없습니다. 다른 MP4를 선택해 주세요.")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if fps <= 0 or frame_count <= 0 or width <= 0 or height <= 0:
            raise RegistrationError("FILE_INVALID", "영상 정보를 확인할 수 없는 MP4입니다.")
        duration_ms = round(frame_count / fps * 1000)
        if duration_ms > max_duration_ms:
            minutes = duration_ms / 60_000
            raise RegistrationError(
                "FILE_LIMIT",
                f"영상 길이는 최대 10분입니다. 선택한 영상은 약 {minutes:.1f}분입니다.",
                413,
            )

        warnings: list[str] = []
        if height < 1080:
            warnings.append(f"해상도가 {width}×{height}입니다. 정확도를 위해 1080p 이상을 권장합니다.")
        if fps < 60:
            warnings.append(f"영상이 {fps:.2f}fps입니다. 장면 재생은 원본 fps를 유지합니다.")

        brightness_values: list[float] = []
        blur_values: list[float] = []
        for position in (0, frame_count // 2, max(frame_count - 1, 0)):
            capture.set(cv2.CAP_PROP_POS_FRAMES, position)
            ok, frame = capture.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            brightness_values.append(float(gray.mean()))
            blur_values.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
        if brightness_values and sum(brightness_values) / len(brightness_values) < 40:
            warnings.append("영상이 전반적으로 어둡습니다. 공과 선수 인식 정확도가 낮아질 수 있습니다.")
        if blur_values and sum(blur_values) / len(blur_values) < 25:
            warnings.append("영상이 전반적으로 흐립니다. 공 추적 정확도가 낮아질 수 있습니다.")
        warnings.append("다음 화면에서 골대·반코트·선수 전신이 보이고 카메라가 고정됐는지 확인해 주세요.")
        return VideoMetadata(duration_ms, width, height, fps, warnings)
    finally:
        capture.release()


async def save_upload(upload: UploadFile, destination: Path, max_bytes: int) -> int:
    if not upload.filename or Path(upload.filename).suffix.lower() != ".mp4":
        raise RegistrationError("FILE_INVALID", "MP4 영상만 사용할 수 있습니다.")
    written = 0
    try:
        destination.parent.mkdir(parents=True, exist_ok=False)
        with destination.open("xb") as output:
            while chunk := await upload.read(CHUNK_SIZE):
                written += len(chunk)
                if written > max_bytes:
                    raise RegistrationError(
                        "FILE_LIMIT", "영상 파일은 최대 5GB까지 등록할 수 있습니다.", 413
                    )
                output.write(chunk)
    except OSError as error:
        if error.errno == errno.ENOSPC:
            raise RegistrationError(
                "STORAGE_LOW", "저장 공간이 부족합니다. 공간을 확보한 뒤 다시 시도해 주세요.", 507
            ) from error
        raise
    finally:
        await upload.close()
    if written == 0:
        raise RegistrationError("FILE_INVALID", "비어 있는 파일은 등록할 수 없습니다.")
    if not has_mp4_signature(destination):
        raise RegistrationError("FILE_INVALID", "확장자만 MP4인 파일은 사용할 수 없습니다.")
    return written


async def register_video(
    session: Session,
    settings: Settings,
    *,
    title: str,
    play_type: PlayType,
    recorded_date,
    player_name: str | None,
    contains_others: bool,
    consent_confirmed: bool,
    ai_notice_confirmed: bool,
    upload: UploadFile,
) -> Analysis:
    clean_title = validate_title(title)
    validate_confirmations(
        contains_others=contains_others,
        consent_confirmed=consent_confirmed,
        ai_notice_confirmed=ai_notice_confirmed,
    )
    analysis_id = new_id()
    analysis_dir = settings.upload_path / analysis_id
    stored_file = analysis_dir / "original.mp4"
    try:
        file_size = await save_upload(upload, stored_file, settings.max_video_bytes)
        metadata = probe_video(stored_file, settings.max_video_minutes * 60_000)
        analysis = Analysis(
            id=analysis_id,
            title=clean_title,
            play_type=play_type,
            recorded_date=recorded_date,
            original_name=Path(upload.filename or "video.mp4").name,
            stored_path=str(stored_file.relative_to(settings.upload_path.parent)),
            file_size=file_size,
            duration_ms=metadata.duration_ms,
            width=metadata.width,
            height=metadata.height,
            fps=metadata.fps,
            status=AnalysisStatus.SETUP_REQUIRED,
            warnings=metadata.warnings,
            contains_others=contains_others,
            consent_confirmed=consent_confirmed,
            ai_notice_confirmed=ai_notice_confirmed,
        )
        clean_player_name = (player_name or "").strip()
        if play_type is PlayType.SOLO:
            analysis.players.append(
                Player(
                    display_name=clean_player_name or "선수 A",
                    is_temporary_name=not bool(clean_player_name),
                )
            )
        session.add(analysis)
        session.commit()
        session.refresh(analysis)
        return analysis
    except Exception:
        session.rollback()
        shutil.rmtree(analysis_dir, ignore_errors=True)
        raise
