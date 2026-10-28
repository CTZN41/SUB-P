from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "sqlite:///./data/shotmap.db"
    upload_dir: str = "./uploads"
    work_dir: str = "./work"
    max_video_minutes: int = 10
    max_video_bytes: int = 5 * 1024 * 1024 * 1024
    openai_api_key: str | None = None
    openai_vision_model: str | None = None
    openai_timeout_seconds: int = 60
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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
        if not self.database_url.startswith("sqlite:///"):
            return (BACKEND_ROOT / "data").resolve()
        database_file = self.database_url.removeprefix("sqlite:///")
        return self.resolve_path(database_file).parent

    def ensure_storage_directories(self) -> None:
        for path in (self.upload_path, self.work_path, self.data_path):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
