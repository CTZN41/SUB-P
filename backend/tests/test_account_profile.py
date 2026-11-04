import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.api.auth import router
from app.db.session import create_session_factory
from app.models import UserProfile
from app.services import authentication
from app.services.account_profile import nickname_from_metadata
from app.services.nickname_policy import nickname_key
from test_authentication import auth_settings, USER_A, USER_B


@pytest.fixture
def profile_client(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    UserProfile.__table__.create(engine)
    with engine.begin() as db:
        db.execute(text("ATTACH DATABASE ':memory:' AS auth"))
        db.execute(text("CREATE TABLE auth.users (id TEXT PRIMARY KEY, email TEXT, created_at TEXT, raw_user_meta_data TEXT, email_confirmed_at TEXT, encrypted_password TEXT, email_change TEXT DEFAULT '')"))
        db.execute(text('CREATE TABLE auth.identities (user_id TEXT, provider TEXT)'))
        for user_id, email, password, nickname in [(USER_A, 'first@example.test', '', '  슈터 A  '), (USER_B, 'second@example.test', 'not-a-real-password-hash', '다른선수')]:
            db.execute(text('INSERT INTO auth.users (id,email,created_at,raw_user_meta_data,email_confirmed_at,encrypted_password) VALUES (:id,:email,:created,:metadata,:confirmed,:password)'),
                {'id': user_id, 'email': email, 'created': '2026-11-04T01:00:00Z',
                 'metadata': json.dumps({'shotlog_nickname': nickname, 'unrelated_private_field': 'must-not-leak'}),
                 'confirmed': '2026-11-04T01:00:00Z', 'password': password})
            db.execute(text('INSERT INTO auth.identities VALUES (:id,:provider)'), {'id': user_id, 'provider': 'google'})
    with Session(engine) as session:
        for user_id, nickname in [(USER_A, '슈터 A'), (USER_B, '다른선수')]:
            session.add(UserProfile(user_id=user_id, nickname=nickname, nickname_key=nickname_key(nickname)))
        session.commit()
    app = FastAPI(); app.state.settings = auth_settings(); app.state.session_factory = create_session_factory(engine)
    app.include_router(router)
    async def verify(settings, token):
        if token not in ('account-a', 'account-b'):
            raise HTTPException(401, detail='Login required')
        return {'id': USER_A if token == 'account-a' else USER_B}
    monkeypatch.setattr(authentication, 'verify_token', verify)
    yield TestClient(app), engine
    engine.dispose()


def test_profile_reads_only_authenticated_account_and_never_returns_password_material(profile_client):
    client, _ = profile_client
    assert client.get('/api/auth/profile').status_code == 401
    response = client.get('/api/auth/profile?user_id='+USER_B, headers={'Authorization': 'Bearer account-a'})
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    body = response.json()
    assert body['id'] == USER_A and body['nickname'] == '슈터 A'
    assert body['email'] == 'first@example.test'
    assert body['password_registered'] is False and body['can_register_password'] is True
    assert 'second@example.test' not in response.text
    assert 'encrypted_password' not in response.text and 'must-not-leak' not in response.text
    second = client.get('/api/auth/profile', headers={'Authorization': 'Bearer account-b'})
    assert second.json()['password_registered'] is True
    assert second.json()['can_register_password'] is False
    assert 'not-a-real-password-hash' not in second.text


def test_profile_requires_verified_email_for_password_registration(profile_client):
    client, engine = profile_client
    with engine.begin() as db:
        db.execute(text('UPDATE auth.users SET email_confirmed_at = NULL WHERE id = :id'), {'id': USER_A})
    body = client.get('/api/auth/profile', headers={'Authorization': 'Bearer account-a'}).json()
    assert body['email_confirmed'] is False and body['can_register_password'] is False


def test_saved_nickname_is_read_after_a_new_login_without_user_metadata(profile_client, monkeypatch):
    client, _ = profile_client
    async def verified(settings, token):
        if token not in ('first-session', 'relogin-session'):
            raise HTTPException(401)
        # OAuth/session metadata may be missing; durable DB profile is authoritative.
        return {'id': USER_A, 'email': None}
    monkeypatch.setattr(authentication, 'verify_token', verified)
    first = client.get('/api/auth/profile', headers={'Authorization': 'Bearer first-session'}).json()
    second = client.get('/api/auth/profile', headers={'Authorization': 'Bearer relogin-session'}).json()
    assert first['nickname'] == second['nickname'] == '슈터 A'
    assert first['id'] == second['id'] == USER_A


def test_profile_does_not_offer_password_registration_without_email_or_oauth(profile_client):
    client, engine = profile_client
    with engine.begin() as db:
        db.execute(text('UPDATE auth.users SET email = NULL WHERE id = :id'), {'id': USER_A})
    assert client.get('/api/auth/profile', headers={'Authorization': 'Bearer account-a'}).json()['can_register_password'] is False
    with engine.begin() as db:
        db.execute(text('UPDATE auth.users SET email = :email WHERE id = :id'), {'email': 'first@example.test', 'id': USER_A})
        db.execute(text('UPDATE auth.identities SET provider = :provider WHERE user_id = :id'), {'provider': 'sso:managed', 'id': USER_A})
    assert client.get('/api/auth/profile', headers={'Authorization': 'Bearer account-a'}).json()['can_register_password'] is False


def test_unknown_account_is_not_replaced_with_another_profile(profile_client):
    client, engine = profile_client
    with engine.begin() as db:
        db.execute(text('DELETE FROM auth.users WHERE id = :id'), {'id': USER_A})
    assert client.get('/api/auth/profile', headers={'Authorization': 'Bearer account-a'}).status_code == 404


@pytest.mark.parametrize('metadata', [None, 'invalid-json', [], {'nickname': 'Provider name'}, {'shotlog_nickname': '<script>'}, {'shotlog_nickname': 'a'}, {'shotlog_nickname': 'x'*21}, {'shotlog_nickname': ['not-a-string']}])
def test_profile_ignores_invalid_or_provider_nicknames(metadata):
    assert nickname_from_metadata(metadata) == ''


def nickname_headers(user_id=USER_A, token='account-a'):
    return {'Authorization': 'Bearer ' + token, 'Origin': 'http://127.0.0.1:5173', 'X-Account-Id': user_id}


def test_duplicate_loser_has_to_choose_a_new_name_without_changing_auth_metadata(profile_client):
    client, engine = profile_client
    with engine.begin() as db:
        db.execute(text('DELETE FROM user_profiles WHERE user_id=:id'), {'id': USER_B})
        db.execute(text('UPDATE auth.users SET raw_user_meta_data=:data WHERE id=:id'),
                   {'id': USER_B, 'data': json.dumps({'shotlog_nickname': '슈터 A'})})
    headers = nickname_headers(USER_B, 'account-b')
    body = client.get('/api/auth/profile', headers=headers).json()
    assert body['nickname'] == '' and body['nickname_conflict'] is True
    assert client.post('/api/auth/profile/nickname', json={'nickname': '슈터 a'}, headers=headers).status_code == 409
    result = client.post('/api/auth/profile/nickname', json={'nickname': '새 닉네임'}, headers=headers)
    assert result.status_code == 200 and result.json()['nickname'] == '새 닉네임'
    assert client.get('/api/auth/profile', headers=headers).json()['nickname'] == '새 닉네임'
    assert client.get('/api/auth/profile', headers=nickname_headers()).json()['nickname'] == '슈터 A'
    with engine.connect() as db:
        assert json.loads(db.scalar(text('SELECT raw_user_meta_data FROM auth.users WHERE id=:id'), {'id': USER_B}))['shotlog_nickname'] == '슈터 A'


def test_nickname_immutable_idempotent_and_cannot_be_assigned_from_stale_or_foreign_origin(profile_client):
    client, _ = profile_client
    url = '/api/auth/profile/nickname'
    assert client.post(url, json={'nickname': '슈터 A'}, headers=nickname_headers()).status_code == 200
    assert client.post(url, json={'nickname': '새 닉네임'}, headers=nickname_headers()).status_code == 409
    stale = client.post(url, json={'nickname': '슈터 A'}, headers=nickname_headers(USER_B))
    assert stale.status_code == 409 and stale.json()['detail']['code'] == 'SESSION_CHANGED'
    assert client.post(url, json={'nickname': '슈터 A', 'user_id': USER_B}, headers=nickname_headers()).status_code == 422
    headers = {**nickname_headers(), 'Origin': 'https://unrelated.example.test'}
    assert client.post(url, json={'nickname': '슈터 A'}, headers=headers).status_code == 403


def test_database_unique_constraint_blocks_competing_claims(profile_client):
    _, engine = profile_client
    with Session(engine) as session:
        session.add(UserProfile(user_id='new-user', nickname='슈터 a', nickname_key=nickname_key('슈터 a')))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        assert session.get(UserProfile, USER_A).nickname == '슈터 A'


def test_signup_duplicate_nickname_and_email_checks_are_case_insensitive(profile_client):
    client, _ = profile_client
    headers = {'Origin': 'http://127.0.0.1:5173'}
    url = '/api/auth/signup-check'
    result = client.post(url, json={'nickname': '새닉네임', 'email': ' FIRST@EXAMPLE.TEST '}, headers=headers)
    assert result.status_code == 409 and result.json()['detail']['code'] == 'EMAIL_TAKEN'
    result = client.post(url, json={'nickname': '슈터 ａ', 'email': 'unused@example.test'}, headers=headers)
    assert result.status_code == 409 and result.json()['detail']['code'] == 'NICKNAME_TAKEN'
    assert client.post(url, json={'nickname': '새닉네임', 'email': 'unused@example.test'}, headers=headers).json() == {'available': True}
    assert client.post(url, json={'nickname': '새닉네임', 'email': 'bad'}, headers=headers).status_code == 422
    assert client.post(url, json={'nickname': '새닉네임', 'email': 'unused@example.test'}).status_code == 403


def test_email_registration_rejects_existing_and_pending_email_but_never_changes_auth(profile_client):
    client, engine = profile_client
    headers = nickname_headers()
    url = '/api/auth/profile/email-check'
    assert client.post(url, json={'email': 'new@example.test'}, headers=headers).status_code == 409
    with engine.begin() as db:
        db.execute(text("UPDATE auth.users SET email=NULL, email_confirmed_at=NULL WHERE id=:id"), {'id': USER_A})
        db.execute(text("UPDATE auth.users SET email_change='pending@example.test' WHERE id=:id"), {'id': USER_B})
    for email in ('SECOND@example.test', 'PENDING@example.test'):
        result = client.post(url, json={'email': email}, headers=headers)
        assert result.status_code == 409 and result.json()['detail']['code'] == 'EMAIL_TAKEN'
    assert client.post(url, json={'email': 'new@example.test'}, headers=headers).json() == {'available': True}
    body = client.get('/api/auth/profile', headers=headers).json()
    assert body['email'] is None and body['email_confirmed'] is False and body['can_register_email'] is True
    assert client.post(url, json={'email': 'new@example.test'}).status_code == 403


def test_migration_preserves_oldest_account_and_leaves_duplicate_unclaimed(profile_client):
    import importlib.util
    from pathlib import Path
    _, engine = profile_client
    path = Path(__file__).parents[1] / 'alembic/versions/20261104_0007_unique_nicknames.py'
    spec = importlib.util.spec_from_file_location('unique_nickname_migration', path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    with engine.begin() as db:
        db.execute(text('DELETE FROM user_profiles'))
        db.execute(text('UPDATE auth.users SET raw_user_meta_data=:data, created_at=:created WHERE id=:id'),
                   {'id': USER_A, 'data': json.dumps({'shotlog_nickname': 'First Player'}), 'created': '2026-11-01T00:00:00Z'})
        db.execute(text('UPDATE auth.users SET raw_user_meta_data=:data WHERE id=:id'),
                   {'id': USER_B, 'data': json.dumps({'shotlog_nickname': 'first  player'})})
        migration.backfill_profiles(db)
        rows = db.execute(text('SELECT user_id, nickname FROM user_profiles')).all()
        assert rows == [(USER_A, 'First Player')]
