"""OAuth2 bridge for Naver's nested profile and token-exchange state requirement.

Supabase manages accounts/sessions. This adapter binds its S256 verifier to the
original OAuth state, forwards Naver credentials only server-to-server, and
normalizes the verified profile. One process is supported; use shared state
storage before deploying multiple workers.
"""
import base64
import hashlib
import re
import secrets
from time import monotonic
from urllib.parse import urlencode, urlparse, unquote_plus

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

router = APIRouter(prefix='/api/auth/naver', tags=['authentication'])


def settings(request):
    s = request.app.state.settings
    if not (s.auth_required and s.auth_naver_enabled and s.naver_client_id and s.naver_client_secret and s.naver_redirect_uri):
        raise HTTPException(503, detail={'message': '네이버 로그인 연결을 준비하고 있습니다.'})
    callback, project = urlparse(s.naver_redirect_uri), urlparse(s.supabase_url or '')
    if callback.scheme != 'https' or callback.netloc != project.netloc or not callback.path.startswith('/auth/v1/callback'):
        raise HTTPException(503, detail={'message': '네이버 로그인 돌아올 주소를 확인해 주세요.'})
    return s


@router.get('/authorize')
def authorize(request: Request, client_id: str, redirect_uri: str, state: str,
              code_challenge: str, code_challenge_method: str = 'S256', response_type: str = 'code'):
    s = settings(request)
    if (client_id != s.naver_client_id or redirect_uri != s.naver_redirect_uri or response_type != 'code'
            or code_challenge_method != 'S256' or not re.fullmatch(r'[A-Za-z0-9_-]{43}', code_challenge)
            or not 16 <= len(state) <= 2048 or any(ord(c) < 32 for c in state)):
        raise HTTPException(400, detail='Invalid OAuth request')
    cache = getattr(request.app.state, 'naver_states', {})
    cache = {key: value for key, value in cache.items() if value[1] > monotonic()}
    if len(cache) >= 256:
        raise HTTPException(429, detail='Please retry later')
    cache[code_challenge] = (state, monotonic() + 600)
    request.app.state.naver_states = cache
    url = 'https://nid.naver.com/oauth2.0/authorize?' + urlencode({
        'response_type': 'code', 'client_id': s.naver_client_id,
        'redirect_uri': s.naver_redirect_uri, 'state': state})
    return RedirectResponse(url, status_code=302, headers={'Cache-Control': 'no-store'})


@router.post('/token')
async def token(request: Request):
    s = settings(request)
    form = await request.form(max_fields=10)
    client_id, secret = str(form.get('client_id', '')), str(form.get('client_secret', ''))
    basic = request.headers.get('authorization', '')
    if basic.startswith('Basic '):
        try:
            client_id, secret = base64.b64decode(basic[6:], validate=True).decode().split(':', 1)
            client_id, secret = unquote_plus(client_id), unquote_plus(secret)
        except (ValueError, UnicodeError):
            raise HTTPException(401, detail='Invalid client') from None
    if not (secrets.compare_digest(client_id, s.naver_client_id) and secrets.compare_digest(secret, s.naver_client_secret)):
        raise HTTPException(401, detail='Invalid client')
    verifier, code = str(form.get('code_verifier', '')), str(form.get('code', ''))
    if form.get('grant_type') != 'authorization_code' or not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}', verifier) or not 1 <= len(code) <= 2048:
        raise HTTPException(400, detail='Invalid grant')
    if form.get('redirect_uri') != s.naver_redirect_uri:
        raise HTTPException(400, detail='Invalid redirect')
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    entry = getattr(request.app.state, 'naver_states', {}).pop(challenge, None)
    if not entry or entry[1] <= monotonic():
        raise HTTPException(400, detail='Expired or invalid grant')
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post('https://nid.naver.com/oauth2.0/token', data={
                'grant_type': 'authorization_code', 'client_id': s.naver_client_id,
                'client_secret': s.naver_client_secret, 'code': code, 'state': entry[0],
                'redirect_uri': s.naver_redirect_uri})
        response.raise_for_status()
        data = response.json()
        if not data.get('access_token') or data.get('error'):
            raise ValueError('Invalid provider response')
        result = {key: data[key] for key in ('access_token', 'refresh_token', 'expires_in', 'token_type') if key in data}
        result.setdefault('token_type', 'Bearer')
        return JSONResponse(result, headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'})
    except (httpx.HTTPError, ValueError, TypeError):
        raise HTTPException(502, detail='Naver authentication could not be completed') from None


@router.get('/userinfo')
async def userinfo(request: Request):
    settings(request)
    bearer = request.headers.get('authorization', '')
    if not bearer.lower().startswith('bearer ') or len(bearer) > 8192:
        raise HTTPException(401, detail='Invalid provider token')
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.get('https://openapi.naver.com/v1/nid/me', headers={'Authorization': bearer})
        response.raise_for_status()
        data = response.json()
        profile = data.get('response') or {}
        if data.get('resultcode') != '00' or not isinstance(profile.get('id'), str) or not profile['id']:
            raise ValueError('Invalid profile')
        # Never mark Naver email verified or merge accounts on an unverified email.
        return JSONResponse({'sub': profile['id'], 'name': profile.get('nickname') or '네이버 사용자'},
                            headers={'Cache-Control': 'no-store'})
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise HTTPException(401, detail='Invalid provider token') from None
