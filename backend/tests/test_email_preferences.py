from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import AnalysisNotification, UserEmailPreference
from app.services import email_notifications as notifications, practice_report as reports
from test_account_profile import profile_client
from test_authentication import USER_A, USER_B
from test_email_notifications import setup, request_one
from test_practice_report import store, add_practice, fake_text


def headers(account='a'):
    return {'Authorization': f'Bearer account-{account}', 'Origin': 'http://127.0.0.1:5173',
            'X-Account-Id': USER_A if account == 'a' else USER_B}


def test_consent_defaults_off_persists_and_is_account_scoped(profile_client):
    client, engine = profile_client
    url = '/api/auth/profile/email-preference'
    assert client.get('/api/auth/profile', headers=headers()).json()['email_reports_consent'] is False
    saved = client.post(url, headers=headers(), json={'enabled': True})
    assert saved.status_code == 200
    assert saved.json()['email_reports_consent'] is True
    assert client.get('/api/auth/profile', headers=headers()).json()['email_reports_consent'] is True
    assert client.get('/api/auth/profile', headers=headers('b')).json()['email_reports_consent'] is False
    with Session(engine) as session:
        assert session.get(UserEmailPreference, USER_A).enabled
    assert client.post(url, headers=headers(), json={'enabled': False}).json()['email_reports_consent'] is False


def test_consent_rejects_stale_accounts_bad_origin_and_unverified_email(profile_client):
    from sqlalchemy import text
    client, engine = profile_client
    url = '/api/auth/profile/email-preference'
    assert client.post(url, headers={**headers(), 'X-Account-Id': USER_B}, json={'enabled': True}).status_code == 409
    assert client.post(url, headers={**headers(), 'Origin': 'https://evil.example'}, json={'enabled': True}).status_code == 403
    assert client.post(url, headers=headers(), json={'enabled': 'false'}).status_code == 422
    assert client.post(url, headers=headers(), json={'enabled': True, 'user_id': USER_B}).status_code == 422
    with engine.begin() as connection:
        connection.execute(text('UPDATE auth.users SET email_confirmed_at = NULL WHERE id = :id'), {'id': USER_A})
    assert client.post(url, headers=headers(), json={'enabled': True}).json()['detail']['code'] == 'EMAIL_VERIFICATION_REQUIRED'


def test_withdrawal_cancels_only_own_unsent_notifications(profile_client):
    client, engine = profile_client
    with Session(engine) as session:
        for owner, status in [(USER_A, 'waiting'), (USER_A, 'preparing'), (USER_A, 'accepted'), (USER_B, 'waiting')]:
            a = add_practice(session, owner=owner)
            session.add(AnalysisNotification(analysis_id=a.id, owner_id=owner, status=status,
                                            recipient_email='test@example.test'))
        session.commit()
    assert client.post('/api/auth/profile/email-preference', headers=headers(), json={'enabled': False}).status_code == 200
    with Session(engine) as session:
        rows = session.query(AnalysisNotification).all()
        assert sorted(row.status for row in rows if row.owner_id == USER_A) == ['accepted', 'canceled', 'canceled']
        assert next(row for row in rows if row.owner_id == USER_B).status == 'waiting'


def test_request_body_cannot_override_profile_opt_out(setup):
    factory, settings, calls = setup
    with factory() as session:
        a = add_practice(session)
        session.get(UserEmailPreference, 'a').enabled = False
        session.commit()
        with pytest.raises(notifications.NotificationError) as blocked:
            notifications.request_report_delivery(session, a, settings, True, str(uuid4()))
        assert blocked.value.code == 'CONSENT_REQUIRED'
    assert not calls


def test_withdrawal_during_ai_preparation_prevents_smtp_dispatch(setup, monkeypatch):
    factory, settings, calls = setup
    aid = request_one(factory, settings)
    def withdraw(context, config):
        with factory() as session:
            session.get(UserEmailPreference, 'a').enabled = False
            session.commit()
        return fake_text(context, config)
    monkeypatch.setattr(reports, '_generate_text', withdraw)
    notifications.process_notification(factory, settings, aid)
    assert not calls
    with factory() as session:
        assert session.get(AnalysisNotification, aid).status == 'canceled'
