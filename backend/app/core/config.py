from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
import base64
import json

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str | None = None
    upload_dir: str = "./uploads"
    work_dir: str = "./work"
    max_video_minutes: int = 40
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
    auth_required: bool = False
    supabase_url: str | None = None
    supabase_publishable_key: str | None = None
    auth_frontend_origin: str = "http://127.0.0.1:5173"
    auth_google_enabled: bool = False
    auth_kakao_enabled: bool = False
    auth_naver_enabled: bool = False
    naver_client_id: str | None = None
    naver_client_secret: str | None = None
    naver_redirect_uri: str | None = None

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def reject_sqlite_outside_tests(self) -> "Settings":
        if self.supabase_publishable_key:
            key = self.supabase_publishable_key
            if key.startswith('sb_secret_'):
                raise ValueError('SUPABASE_PUBLISHABLE_KEY에는 공개용 publishable/anon 키만 사용하세요.')
            if key.count('.') == 2:
                try:
                    payload = key.split('.')[1]
                    role = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4))).get('role')
                    if role != 'anon':
                        raise ValueError('공개용 anon 키만 사용할 수 있습니다.')
                except (ValueError, TypeError, AttributeError):
                    raise ValueError('SUPABASE_PUBLISHABLE_KEY에 올바른 공개용 키를 입력하세요.') from None
        if self.supabase_url:
            url = urlparse(self.supabase_url)
            local = self.app_env in ('development', 'test') and url.hostname in ('localhost', '127.0.0.1')
            if not url.netloc or (url.scheme != 'https' and not (local and url.scheme == 'http')) or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
                raise ValueError('SUPABASE_URL에는 프로젝트의 HTTPS 주소를 입력하세요.')
        if self.app_env not in ("development", "test") and not self.auth_required:
            raise ValueError("배포 환경에서는 AUTH_REQUIRED=true로 로그인 보호를 켜세요.")
        if self.auth_required and not (self.supabase_url and self.supabase_publishable_key):
            raise ValueError("로그인 보호에는 SUPABASE_URL과 SUPABASE_PUBLISHABLE_KEY가 필요합니다.")
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
