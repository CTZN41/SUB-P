import pytest

from app.core.config import Settings
from app.db.session import normalize_database_url


def test_default_video_limits_and_optional_secret() -> None:
    settings = Settings(_env_file=None)

    assert settings.database_url is None
    assert settings.max_video_minutes == 40
    assert settings.max_video_bytes == 5 * 1024 * 1024 * 1024
    assert settings.openai_api_key is None
    assert settings.openai_vision_model == "gpt-6.1-terra"
    assert settings.openai_vision_fallback_model == "gpt-6.1-sol"
    assert settings.openai_shot_review_enabled is True
    assert settings.openai_max_candidate_reviews == 300
    assert settings.openai_max_shot_gate_reviews == 600


def test_database_url_is_required_when_database_is_used() -> None:
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        normalize_database_url(None)


def test_sqlite_is_rejected_outside_automated_tests() -> None:
    with pytest.raises(ValueError, match="SQLite는 자동 시험에서만"):
        Settings(_env_file=None, database_url="sqlite:///./data/shotmap.db")


def test_sqlite_is_allowed_for_automated_tests() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite:///./data/shotmap.db",
    )

    assert settings.database_url == "sqlite:///./data/shotmap.db"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "postgres://user:password@pooler.example.com:5432/postgres",
            "postgresql+psycopg://user:password@pooler.example.com:5432/postgres",
        ),
        (
            "postgresql://user:password@pooler.example.com:5432/postgres",
            "postgresql+psycopg://user:password@pooler.example.com:5432/postgres",
        ),
        (
            "postgresql+psycopg://user:password@pooler.example.com:5432/postgres",
            "postgresql+psycopg://user:password@pooler.example.com:5432/postgres",
        ),
    ],
)
def test_supabase_urls_use_psycopg_driver(source: str, expected: str) -> None:
    assert normalize_database_url(source) == expected


def test_unsupported_database_url_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="형식을 확인"):
        normalize_database_url("mysql://user:password@example.com/database")
