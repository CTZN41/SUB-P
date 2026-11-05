from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.orm import Session

from app.db.dependencies import get_db_session
from app.services.authentication import COOKIE_NAME, check_origin, verify_token, require_analysis_user
from app.services.account_profile import read_account_profile, nickname_available, claim_nickname, check_email_available, save_email_preference

router = APIRouter(prefix='/api/auth', tags=['authentication'])


class SessionInput(BaseModel):
    access_token: str = Field(min_length=1, max_length=8192, repr=False)
    remember: bool = False


class NicknameInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    nickname: str = Field(min_length=2, max_length=100)


class SignupCheckInput(NicknameInput):
    email: str = Field(min_length=3, max_length=254, repr=False)


class EmailInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email: str = Field(min_length=3, max_length=254, repr=False)


class EmailPreferenceInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool


@router.get('/config')
def auth_config(request: Request):
    s = request.app.state.settings
    ready = bool(s.auth_required and s.supabase_url and s.supabase_publishable_key)
    return {'required': s.auth_required, 'configured': ready,
        'url': s.supabase_url if ready else None,
        'publishableKey': s.supabase_publishable_key if ready else None,
        'providers': {'google': ready and s.auth_google_enabled, 'kakao': ready and s.auth_kakao_enabled}}


@router.get('/profile', dependencies=[Depends(require_analysis_user)])
def own_profile(request: Request, response: Response, session: Annotated[Session, Depends(get_db_session)]):
    user_id = getattr(request.state, 'auth_user_id', None)
    if not user_id:
        raise HTTPException(401, detail={'code': 'LOGIN_REQUIRED', 'message': '로그인이 필요합니다.'})
    response.headers['Cache-Control'] = 'no-store'
    return read_account_profile(session, user_id)


@router.post('/signup-check')
def signup_check(body: SignupCheckInput, request: Request, response: Response, session: Annotated[Session, Depends(get_db_session)]):
    check_origin(request)
    response.headers['Cache-Control'] = 'no-store'
    if not request.app.state.settings.auth_required:
        raise HTTPException(503, detail={'code': 'AUTH_UNAVAILABLE'})
    check_email_available(session, body.email)
    if not nickname_available(session, body.nickname):
        raise HTTPException(409, detail={'code': 'NICKNAME_TAKEN', 'message': '이미 사용 중인 닉네임입니다.'})
    return {'available': True}


@router.post('/profile/email-preference', dependencies=[Depends(require_analysis_user)])
def update_email_preference(body: EmailPreferenceInput, request: Request, response: Response,
                            session: Annotated[Session, Depends(get_db_session)]):
    check_origin(request)
    user_id = getattr(request.state, 'auth_user_id', None)
    if not user_id:
        raise HTTPException(401, detail={'code': 'LOGIN_REQUIRED'})
    if request.headers.get('x-account-id') != user_id:
        raise HTTPException(409, detail={'code': 'SESSION_CHANGED'})
    response.headers['Cache-Control'] = 'no-store'
    return save_email_preference(session, user_id, body.enabled)


@router.post('/profile/nickname', dependencies=[Depends(require_analysis_user)])
def save_nickname(body: NicknameInput, request: Request, response: Response, session: Annotated[Session, Depends(get_db_session)]):
    check_origin(request)
    user_id = getattr(request.state, 'auth_user_id', None)
    if not user_id:
        raise HTTPException(401, detail={'code': 'LOGIN_REQUIRED'})
    # A precondition, never an identity source. Prevent a stale tab from claiming
    # its nickname on an account that signed in since the form was opened.
    if request.headers.get('x-account-id') != user_id:
        raise HTTPException(409, detail={'code': 'SESSION_CHANGED'})
    response.headers['Cache-Control'] = 'no-store'
    return claim_nickname(session, user_id, body.nickname)


@router.post('/profile/email-check', dependencies=[Depends(require_analysis_user)])
def email_check(body: EmailInput, request: Request, response: Response, session: Annotated[Session, Depends(get_db_session)]):
    check_origin(request)
    user_id = getattr(request.state, 'auth_user_id', None)
    if not user_id:
        raise HTTPException(401, detail={'code': 'LOGIN_REQUIRED'})
    response.headers['Cache-Control'] = 'no-store'
    profile = read_account_profile(session, user_id)
    if not profile['can_register_email']:
        raise HTTPException(409, detail={'code': 'EMAIL_EXISTS', 'message': '이메일이 이미 등록됐습니다.'})
    check_email_available(session, body.email, user_id)
    return {'available': True}


@router.post('/session')
async def set_session(body: SessionInput, request: Request, response: Response):
    s = request.app.state.settings
    if not s.auth_required:
        raise HTTPException(503, detail={'message': '아직 로그인 연결이 준비되지 않았습니다.'})
    check_origin(request)
    user = await verify_token(s, body.access_token)
    response.headers['Cache-Control'] = 'no-store'
    response.set_cookie(COOKIE_NAME, body.access_token, httponly=True, secure=s.app_env not in ('development', 'test'),
        samesite='lax', max_age=3600 if body.remember else None, path='/api')
    return {'user': user}


@router.delete('/session', status_code=204)
def clear_session(request: Request):
    check_origin(request)
    response = Response(status_code=204, headers={'Cache-Control': 'no-store'})
    response.delete_cookie(COOKIE_NAME, path='/api', httponly=True,
        secure=request.app.state.settings.app_env not in ('development', 'test'), samesite='lax')
    return response
