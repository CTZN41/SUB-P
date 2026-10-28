from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.health import router as health_router
from app.core.config import get_settings
from app.db.session import create_database_engine, create_session_factory


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_storage_directories()

    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    yield
    engine.dispose()


app = FastAPI(
    title="농구 슈팅 분석 API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
