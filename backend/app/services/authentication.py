"""Verify sessions against Supabase Auth; never trust a browser-supplied user ID."""
import httpx
from fastapi import HTTPException, Request
from uuid import UUID

COOKIE_NAME = "shotlog_session"


def check_origin(request: Request) -> None:
    settings = request.app.state.settings
    allowed = {settings.auth_frontend_origin.rstrip('/')}
    if settings.app_env in ('development', 'test'):
        allowed.update({'http://127.0.0.1:5173', 'http://localhost:5173'})
    if request.headers.get('origin', '').rstrip('/') not in allowed:
        raise HTTPException(403, detail={'code': 'ORIGIN_INVALID', 'message': '요청한 화면을 확인할 수 없습니다.'})


async def verify_token(settings, token: str) -> dict:
    if not token or len(token) > 8192:
        raise HTTPException(401, detail={'code': 'LOGIN_REQUIRED', 'message': '로그인이 필요합니다.'})
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.get(settings.supabase_url.rstrip('/') + '/auth/v1/user',
                headers={'apikey': settings.supabase_publishable_key, 'Authorization': 'Bearer ' + token})
        if response.status_code in (401, 403):
            raise HTTPException(401, detail={'code': 'SESSION_EXPIRED', 'message': '다시 로그인해 주세요.'})
        response.raise_for_status()
        user = response.json()
        user_id = str(UUID(user['id']))
        return {'id': user_id, 'email': user.get('email')}
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise HTTPException(503, detail={'code': 'AUTH_UNAVAILABLE', 'message': '로그인 연결을 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.'}) from None


async def require_analysis_user(request: Request) -> None:
    if not request.app.state.settings.auth_required:
        return  # Explicit, local-only mode while provider setup is pending.
    header = request.headers.get('authorization', '')
    bearer = header[7:] if header.lower().startswith('bearer ') else None
    if not bearer and request.method not in ('GET', 'HEAD', 'OPTIONS'):
        check_origin(request)
    user = await verify_token(request.app.state.settings, bearer or request.cookies.get(COOKIE_NAME, ''))
    request.state.auth_user_id = user['id']
