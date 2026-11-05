import base64
import hashlib
from time import monotonic
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.auth import router as auth_router
from app.api.naver_auth import router as naver_router
from app.api.analyses import router as analysis_router
from app.core.config import Settings
from app.services import authentication
from test_phase12_history import make_client, add_record

ORIGIN = {'Origin': 'http://127.0.0.1:5173'}
USER_A, USER_B = str(uuid4()), str(uuid4())


def auth_settings(**extra):
    values = dict(app_env='test', auth_required=True,
        supabase_url='https://project.example', supabase_publishable_key='public-test-key')
    return Settings(_env_file=None, **{**values, **extra})


def client_for(settings):
    app = FastAPI(); app.state.settings = settings
    app.include_router(auth_router); app.include_router(naver_router)
    return TestClient(app)


def test_public_config_never_returns_provider_secret():
    client = client_for(auth_settings(auth_google_enabled=True, auth_kakao_enabled=True,
        auth_naver_enabled=True, naver_client_id='test-client',
        naver_client_secret='private-test-secret', naver_redirect_uri='https://project.example/auth/v1/callback'))
    result = client.get('/api/auth/config')
    assert result.status_code == 200
    assert 'private-test-secret' not in result.text
    assert result.json()['providers'] == {'google': True, 'kakao': True}


def test_naver_login_routes_are_not_registered_in_service():
    from app.main import app

    client = TestClient(app)
    assert client.get('/api/auth/naver/authorize', follow_redirects=False).status_code == 404
    assert client.post('/api/auth/naver/token').status_code == 404
    assert client.get('/api/auth/naver/userinfo').status_code == 404


def test_local_mode_is_explicit_and_cannot_be_used_in_production():
    client = client_for(Settings(_env_file=None, app_env='test'))
    assert client.get('/api/auth/config').json()['required'] is False
    assert client.post('/api/auth/session', json={'access_token': 'test'}).status_code == 503
    with pytest.raises(ValueError, match='AUTH_REQUIRED'):
        Settings(_env_file=None, app_env='production')
    with pytest.raises(ValueError, match='SUPABASE_URL'):
        Settings(_env_file=None, auth_required=True)


def test_cookie_exchange_requires_same_origin_and_sets_http_only_cookie(monkeypatch):
    async def verified(settings, token):
        assert token == 'valid-token'
        return {'id': USER_A, 'email': None}
    monkeypatch.setattr('app.api.auth.verify_token', verified)
    client = client_for(auth_settings())
    assert client.post('/api/auth/session', headers={'Origin': 'https://evil.example'}, json={'access_token': 'valid-token'}).status_code == 403
    assert client.post('/api/auth/session', json={'access_token': 'valid-token'}).status_code == 403
    result = client.post('/api/auth/session', headers=ORIGIN, json={'access_token': 'valid-token'})
    assert result.status_code == 200
    assert 'HttpOnly' in result.headers['set-cookie']
    assert 'SameSite=lax' in result.headers['set-cookie']
    assert 'Path=/api' in result.headers['set-cookie']
    assert 'Max-Age' not in result.headers['set-cookie']
    assert 'expires=' not in result.headers['set-cookie'].lower()
    assert client.delete('/api/auth/session', headers=ORIGIN).status_code == 204


@pytest.mark.parametrize('remember', [False, True])
def test_cookie_lifetime_follows_remember_choice_without_changing_verification(monkeypatch, remember):
    async def verified(settings, token):
        assert token == 'valid-token'
        return {'id': USER_A, 'email': None}
    monkeypatch.setattr('app.api.auth.verify_token', verified)
    client = client_for(auth_settings())
    response = client.post('/api/auth/session', headers=ORIGIN,
                           json={'access_token': 'valid-token', 'remember': remember})
    assert response.status_code == 200
    cookie = response.headers['set-cookie']
    assert ('Max-Age=3600' in cookie) is remember
    assert 'HttpOnly' in cookie and 'SameSite=lax' in cookie and 'Path=/api' in cookie


def test_owner_isolation_covers_list_details_video_monthly_and_delete(tmp_path, monkeypatch):
    client, factory, settings = make_client(tmp_path)
    first, second = add_record(factory, settings, 'A'), add_record(factory, settings, 'B')
    from app.models import Analysis
    with factory() as db:
        db.get(Analysis, first).owner_id = USER_A
        db.get(Analysis, second).owner_id = USER_B
        db.commit()
    client.app.state.settings = auth_settings(database_url=settings.database_url,
        upload_dir=settings.upload_dir, work_dir=settings.work_dir)
    async def verified(settings, token):
        if token != 'valid-token':
            from fastapi import HTTPException
            raise HTTPException(401)
        return {'id': USER_A}
    monkeypatch.setattr(authentication, 'verify_token', verified)
    assert client.get('/api/analyses').status_code == 401
    headers = {'Authorization': 'Bearer valid-token'}
    records = client.get('/api/analyses', headers=headers).json()['analyses']
    assert [record['id'] for record in records] == [first]
    for tail in ('', '/video', '/shots', '/calibrations', '/jobs/current'):
        assert client.get('/api/analyses/'+second+tail, headers=headers).status_code == 404
    assert client.delete('/api/analyses/'+second, headers=headers).status_code == 404
    monthly = client.get('/api/analyses/monthly', headers=headers).json()
    assert all(shot['analysis_id'] == first for shot in monthly['shots'])
    assert client.get('/api/analyses/'+first, headers=headers).status_code == 200
    with factory() as db:
        assert db.get(Analysis, second) is not None


def test_cookie_mutation_without_origin_is_rejected(tmp_path, monkeypatch):
    client, factory, settings = make_client(tmp_path)
    id = add_record(factory, settings)
    client.app.state.settings = auth_settings()
    client.cookies.set(authentication.COOKIE_NAME, 'valid')
    assert client.delete('/api/analyses/'+id).status_code == 403


def naver_client():
    return client_for(auth_settings(auth_naver_enabled=True, naver_client_id='test-client',
        naver_client_secret='test-secret', naver_redirect_uri='https://project.example/auth/v1/callback'))


def naver_params():
    verifier = 'a' * 43
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return verifier, {'client_id': 'test-client', 'redirect_uri': 'https://project.example/auth/v1/callback',
        'state': 'opaque-supabase-state', 'code_challenge': challenge, 'code_challenge_method': 'S256'}


def test_naver_authorize_rejects_redirect_and_pkce_tampering():
    client = naver_client(); _, params = naver_params()
    assert client.get('/api/auth/naver/authorize', params={**params, 'redirect_uri': 'https://evil.example'}, follow_redirects=False).status_code == 400
    assert client.get('/api/auth/naver/authorize', params={**params, 'code_challenge_method': 'plain'}, follow_redirects=False).status_code == 400
    response = client.get('/api/auth/naver/authorize', params=params, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers['location'].startswith('https://nid.naver.com/oauth2.0/authorize?')
    assert 'opaque-supabase-state' in response.headers['location']
    assert 'test-secret' not in response.headers['location']


def test_naver_token_requires_client_pkce_and_original_state_and_rejects_replay(monkeypatch):
    client = naver_client(); verifier, params = naver_params()
    client.get('/api/auth/naver/authorize', params=params, follow_redirects=False)
    form = {'grant_type': 'authorization_code', 'client_id': 'test-client', 'client_secret': 'test-secret',
        'code_verifier': verifier, 'code': 'provider-code', 'redirect_uri': params['redirect_uri']}
    assert client.post('/api/auth/naver/token', data={**form, 'client_secret': 'bad'}).status_code == 401
    assert client.post('/api/auth/naver/token', data={**form, 'code_verifier': 'b'*43}).status_code == 400
    class Provider:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, data):
            assert url == 'https://nid.naver.com/oauth2.0/token'
            assert data['state'] == params['state']
            return httpx.Response(200, json={'access_token': 'provider-token', 'token_type': 'Bearer'}, request=httpx.Request('POST', url))
    monkeypatch.setattr('app.api.naver_auth.httpx.AsyncClient', lambda **kwargs: Provider())
    result = client.post('/api/auth/naver/token', data=form)
    assert result.status_code == 200 and result.headers['cache-control'] == 'no-store'
    assert client.post('/api/auth/naver/token', data=form).status_code == 400


def test_naver_expired_state_is_rejected():
    client = naver_client(); verifier, params = naver_params()
    client.app.state.naver_states = {params['code_challenge']: (params['state'], monotonic()-1)}
    assert client.post('/api/auth/naver/token', data={'grant_type': 'authorization_code', 'client_id': 'test-client',
        'client_secret': 'test-secret', 'code_verifier': verifier, 'code': 'code', 'redirect_uri': params['redirect_uri']}).status_code == 400


def test_naver_profile_is_normalized_without_trusting_email_verification(monkeypatch):
    client = naver_client()
    class Provider:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, headers):
            return httpx.Response(200, json={'resultcode': '00', 'response': {'id': 'naver-id', 'nickname': 'player', 'email': 'unverified@example.test'}}, request=httpx.Request('GET', url))
    monkeypatch.setattr('app.api.naver_auth.httpx.AsyncClient', lambda **kwargs: Provider())
    response = client.get('/api/auth/naver/userinfo', headers={'Authorization': 'Bearer test'})
    assert response.json() == {'sub': 'naver-id', 'name': 'player'}
    assert client.get('/api/auth/naver/userinfo').status_code == 401


@pytest.mark.parametrize('status, body, expected', [(401, {}, 401), (503, {}, 503), (200, {'id': 'invalid-user-id'}, 503)])
def test_supabase_verification_fails_closed(monkeypatch, status, body, expected):
    class Provider:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, headers):
            assert url == 'https://project.example/auth/v1/user'
            return httpx.Response(status, json=body, request=httpx.Request('GET', url))
    monkeypatch.setattr(authentication.httpx, 'AsyncClient', lambda **kwargs: Provider())
    result = client_for(auth_settings()).post('/api/auth/session', headers=ORIGIN, json={'access_token': 'expired-or-invalid'})
    assert result.status_code == expected
    assert 'expired-or-invalid' not in result.text


def test_private_supabase_key_is_never_exposed_as_public_config():
    with pytest.raises(ValueError, match='공개용'):
        auth_settings(supabase_publishable_key='sb_secret_test_not_real')
