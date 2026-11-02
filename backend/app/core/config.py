from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str | None = None
    upload_dir: str = "./uploads"
    work_dir: str = "./work"
    max_video_minutes: int = 10
    max_video_bytes: int = 5 * 1024 * 1024 * 1024
    candidate_runtime: Literal["torch", "openvino_cpu", "openvino_gpu"] = "torch"
    openai_api_key: str | None = None
    openai_vision_model: str = "gpt-6.1-terra"
    openai_vision_fallback_model: str | None = "gpt-6.1-sol"
    openai_timeout_seconds: int = 60
    openai_shot_review_enabled: bool = True
    openai_max_candidate_reviews: int = 300
    openai_max_shot_gate_reviews: int = 600
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def reject_sqlite_outside_tests(self) -> "Settings":
        if (
            self.database_url
            and self.database_url.startswith("sqlite:///")
            and self.app_env != "test"
        ):
            raise ValueError(
                "SQLite는 자동 시험에서만 사용할 수 있습니다. backend/.env의 DATABASE_URL을 "
                "Supabase Session pooler 연결 문자열로 바꾸세요."
            )
        return self

    def resolve_path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else (BACKEND_ROOT / path).resolve()

    @property
    def upload_path(self) -> Path:
        return self.resolve_path(self.upload_dir)

    @property
    def work_path(self) -> Path:
        return self.resolve_path(self.work_dir)

    @property
    def data_path(self) -> Path:
        if not self.database_url or not self.database_url.startswith("sqlite:///"):
            return (BACKEND_ROOT / "data").resolve()
        database_file = self.database_url.removeprefix("sqlite:///")
        return self.resolve_path(database_file).parent

    def ensure_storage_directories(self) -> None:
        for path in (self.upload_path, self.work_path, self.data_path):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
