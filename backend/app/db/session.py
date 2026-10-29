from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import BACKEND_ROOT


def normalize_database_url(database_url: str | None) -> str:
    if not database_url or not database_url.strip():
        raise RuntimeError(
            "DATABASE_URL 설정이 없습니다. backend/.env에 Supabase Session pooler "
            "연결 문자열을 입력하세요."
        )

    database_url = database_url.strip()
    prefix = "sqlite:///"
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql+psycopg://"):
        return database_url
    if not database_url.startswith(prefix):
        raise RuntimeError(
            "DATABASE_URL 형식을 확인하세요. Supabase PostgreSQL 연결 문자열 또는 "
            "테스트용 sqlite:/// 경로만 사용할 수 있습니다."
        )

    raw_path = database_url.removeprefix(prefix)
    database_path = Path(raw_path)
    if not database_path.is_absolute():
        database_path = (BACKEND_ROOT / database_path).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{database_path.as_posix()}"


def create_database_engine(database_url: str | None) -> Engine:
    normalized_url = normalize_database_url(database_url)
    connect_args = {"check_same_thread": False} if normalized_url.startswith("sqlite") else {}
    return create_engine(
        normalized_url,
        connect_args=connect_args,
        hide_parameters=True,
        pool_pre_ping=True,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)
