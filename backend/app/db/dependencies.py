from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session


def get_db_session(request: Request) -> Generator[Session, None, None]:
    session = request.app.state.session_factory()
    session.info['auth_required'] = request.app.state.settings.auth_required
    session.info['auth_user_id'] = getattr(request.state, 'auth_user_id', None)
    try:
        yield session
    finally:
        session.close()
