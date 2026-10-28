from app.core.config import Settings


def test_default_video_limits_and_optional_secret() -> None:
    settings = Settings(_env_file=None)

    assert settings.max_video_minutes == 10
    assert settings.max_video_bytes == 5 * 1024 * 1024 * 1024
    assert settings.openai_api_key is None
