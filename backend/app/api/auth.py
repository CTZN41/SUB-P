from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from app.services.authentication import COOKIE_NAME, check_origin, verify_token

router = APIRouter(prefix='/api/auth', tags=['authentication'])


class SessionInput(BaseModel):
    access_token: str = Field(min_length=1, max_length=8192, repr=False)


@router.get('/config')
def auth_config(request: Request):
    s = request.app.state.settings
    ready = bool(s.auth_required and s.supabase_url and s.supabase_publishable_key)
    return {'required': s.auth_required, 'configured': ready,
        'url': s.supabase_url if ready else None,
        'publishableKey': s.supabase_publishable_key if ready else None,
        'providers': {'google': ready and s.auth_google_enabled, 'kakao': ready and s.auth_kakao_enabled,
            'naver': ready and s.auth_naver_enabled and bool(s.naver_client_id and s.naver_client_secret and s.naver_redirect_uri)}}


@router.post('/session')
async def set_session(body: SessionInput, request: Request, response: Response):
    s = request.app.state.settings
    if not s.auth_required:
        raise HTTPException(503, detail={'message': '아직 로그인 연결이 준비되지 않았습니다.'})
    check_origin(request)
    user = await verify_token(s, body.access_token)
    response.headers['Cache-Control'] = 'no-store'
    response.set_cookie(COOKIE_NAME, body.access_token, httponly=True, secure=s.app_env not in ('development', 'test'),
        samesite='lax', max_age=3600, path='/api')
    return {'user': user}


@router.delete('/session', status_code=204)
def clear_session(request: Request):
    check_origin(request)
    response = Response(status_code=204, headers={'Cache-Control': 'no-store'})
    response.delete_cookie(COOKIE_NAME, path='/api', httponly=True,
        secure=request.app.state.settings.app_env not in ('development', 'test'), samesite='lax')
    return response
