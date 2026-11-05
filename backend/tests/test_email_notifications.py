from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import smtplib
import ssl
from uuid import uuid4
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app.api.analyses import router
from app.models import Analysis, AnalysisJob, AnalysisNotification, AnalysisEmailRequest, AnalysisStatus, JobStatus, Shot, ShotResult, ShotStatus, ShotType
from app.services import email_notifications as notifications, practice_report as reports
from app.services.authentication import require_analysis_user
from test_practice_report import store, add_practice, fake_text

REAL_SEND = notifications.send_message


def test_manual_send_is_idempotent_and_resends_only_on_a_new_explicit_request(setup):
    factory,settings,calls=setup
    with factory() as session:
        a=add_practice(session);aid=a.id;request_id=str(uuid4())
        notifications.request_report_delivery(session,a,settings,True,request_id)
        notifications.request_report_delivery(session,a,settings,True,request_id)
    notifications.process_notification(factory,settings,aid)
    with factory() as session:
        a=session.get(Analysis,aid)
        notifications.request_report_delivery(session,a,settings,True,request_id)
        with pytest.raises(notifications.NotificationError) as cooldown:notifications.request_report_delivery(session,a,settings,True,str(uuid4()))
        assert cooldown.value.code=='EMAIL_COOLDOWN'
        session.get(AnalysisNotification,aid).accepted_at=datetime.now(timezone.utc)-timedelta(minutes=2);session.commit()
        notifications.request_report_delivery(session,a,settings,True,str(uuid4()))
    notifications.process_notification(factory,settings,aid)
    assert len(calls)==2
    with factory() as session:assert len(list(session.scalars(select(AnalysisEmailRequest))))==2


def test_manual_send_blocks_incomplete_unknown_delivery_and_reused_foreign_key(setup):
    factory,settings,calls=setup
    with factory() as session:
        a=add_practice(session);b=add_practice(session,owner='other');key=str(uuid4())
        shot=session.scalar(select(Shot).where(Shot.analysis_id==a.id));shot.court_x=None;session.commit()
        with pytest.raises(notifications.NotificationError) as pending:notifications.request_report_delivery(session,a,settings,True,key)
        assert pending.value.code=='EMAIL_NOT_READY'
        shot.court_x=25;session.commit();notifications.request_report_delivery(session,a,settings,True,key)
        with pytest.raises(notifications.NotificationError) as conflict:notifications.request_report_delivery(session,b,settings,True,key)
        assert conflict.value.code=='EMAIL_REQUEST_CONFLICT'
        session.get(AnalysisNotification,a.id).status='unknown';session.commit()
        with pytest.raises(notifications.NotificationError):notifications.request_report_delivery(session,a,settings,True,str(uuid4()))
    assert not calls


def test_manual_send_api_retries_return_same_request_without_ai_and_rejects_other_owner(setup,monkeypatch):
    factory,settings,calls=setup
    app=FastAPI();app.state.settings=settings.model_copy(update={'auth_required':True});app.state.session_factory=factory;app.include_router(router)
    async def signed_in(request:Request):request.state.auth_user_id='a'
    app.dependency_overrides[require_analysis_user]=signed_in
    with factory() as session:
        own=add_practice(session);other=add_practice(session,owner='other')
    monkeypatch.setattr(reports,'_generate_text',lambda *a:pytest.fail('Registration must not call AI'))
    with TestClient(app) as client:
        url=f'/api/analyses/{own.id}/email-notification/send';body={'consent':True,'request_id':str(uuid4())}
        assert client.post(url,json=body).status_code==200
        assert client.post(url,json=body).status_code==200
        assert client.post(url,json={**body,'email':'other@example.com'}).status_code==422
        assert client.post(url,json={'consent':True}).status_code==422
        assert client.post(f'/api/analyses/{other.id}/email-notification/send',json=body).status_code==404
    assert calls==[]


@pytest.fixture
def setup(store, monkeypatch):
    factory, settings = store
    settings = settings.model_copy(update={'email_notifications_enabled': True,
        'gmail_sender': 'test-sender@gmail.com', 'gmail_app_password': SecretStr('fake-app-password'),
        'public_app_url': 'https://shotlog.example'})
    monkeypatch.setattr(notifications,'verified_account_email',lambda session,owner: f'{owner}@example.com')
    calls = []
    monkeypatch.setattr(reports, '_generate_text', fake_text)
    monkeypatch.setattr(notifications, 'send_message', lambda payload, settings: calls.append(payload) or ('accepted', 'message-1', None))
    return factory, settings, calls


def request_one(factory, settings):
    with factory() as session:
        analysis = add_practice(session)
        notifications.subscribe(session, analysis, settings, True)
        return analysis.id


@pytest.mark.parametrize('case', ['position', 'outcome', 'outside', 'active', 'failed', 'empty'])
def test_unfinished_analysis_never_generates_or_sends(setup, monkeypatch, case):
    factory, settings, calls = setup
    aid = request_one(factory, settings)
    with factory() as session:
        analysis = session.get(Analysis, aid)
        shot = session.scalar(select(Shot).where(Shot.analysis_id == aid))
        if case == 'position': shot.court_x = None
        if case == 'outcome': shot.result = ShotResult.NEEDS_REVIEW
        if case == 'outside': shot.court_x = 51  # In-memory legacy check without persisting invalid data.
        if case == 'active':
            session.add(AnalysisJob(analysis_id=aid, status=JobStatus.RUNNING, stage='testing', processed_ms=0))
        if case == 'failed': analysis.status = AnalysisStatus.FAILED
        if case == 'empty': session.delete(shot)
        if case == 'outside':
            with session.no_autoflush:
                assert not notifications.completion(session, analysis)['ready']
            session.rollback()
            return
        session.commit()
    monkeypatch.setattr(reports, '_generate_text', lambda *a: pytest.fail('Unfinished analysis reached AI'))
    notifications.process_notification(factory, settings, aid)
    assert calls == []
    with factory() as session:
        assert session.get(AnalysisNotification, aid).status == 'waiting'


def test_last_confirmation_triggers_once_and_reuses_saved_report(setup, monkeypatch):
    factory, settings, calls = setup
    aid = request_one(factory, settings)
    with factory() as session:
        analysis = session.get(Analysis, aid)
        shot = session.scalar(select(Shot).where(Shot.analysis_id == aid))
        shot.court_x = None
        session.commit()
    notifications.process_notification(factory, settings, aid)
    assert not calls
    with factory() as session:
        analysis = session.get(Analysis, aid)
        shot = session.scalar(select(Shot).where(Shot.analysis_id == aid))
        shot.court_x = 25
        shot.status = ShotStatus.USER_CONFIRMED
        session.commit()
        reports.generate_report(session, analysis, settings)
    monkeypatch.setattr(reports, '_generate_text', lambda *a: pytest.fail('Fresh saved report must be reused'))
    notifications.process_notification(factory, settings, aid)
    notifications.process_notification(factory, settings, aid)
    assert len(calls) == 1
    message = calls[0]
    assert message['To'] == 'a@example.com'
    assert '첨부한 PDF' in message.get_body(preferencelist=('plain',)).get_content()
    attachments=list(message.iter_attachments())
    assert len(attachments) == 1 and attachments[0].get_content_type() == 'application/pdf'
    assert attachments[0].get_payload(decode=True).startswith(b'%PDF-')
    assert f'https://shotlog.example/results/{aid}' in message.get_body(preferencelist=('html',)).get_content()
    assert '성공 1개' in message.get_body(preferencelist=('plain',)).get_content()
    with factory() as session:
        view = notifications.notification_view(session, session.get(Analysis, aid), settings)
        assert view['status'] == 'accepted' and view['recipient_masked'] == 'a***@example.com'
        assert 'a@example.com' not in str(view)


def test_manual_report_generation_keeps_notification_waiting(setup):
    factory,settings,calls = setup
    aid = request_one(factory,settings)
    with reports._generation_lock(reports._cache_path(settings,aid).with_suffix('.lock')):
        notifications.process_notification(factory,settings,aid)
        with factory() as session:
            assert session.get(AnalysisNotification,aid).status == 'waiting'
        assert calls == []
    notifications.process_notification(factory,settings,aid)
    assert len(calls) == 1


def test_type_review_alone_does_not_block_confirmed_position_and_outcome(setup):
    factory, settings, calls = setup
    aid = request_one(factory, settings)
    with factory() as session:
        shot = session.scalar(select(Shot).where(Shot.analysis_id == aid))
        shot.shot_type = ShotType.NEEDS_REVIEW
        session.get(Analysis, aid).status = AnalysisStatus.REVIEW_REQUIRED
        session.commit()
    notifications.process_notification(factory, settings, aid)
    assert len(calls) == 1


def test_record_changed_during_generation_prevents_delivery(setup, monkeypatch):
    factory, settings, calls = setup
    aid = request_one(factory, settings)
    def change(context, config):
        with factory() as session:
            shot = session.scalar(select(Shot).where(Shot.analysis_id == aid))
            shot.court_x = None
            session.commit()
        return fake_text(context, config)
    monkeypatch.setattr(reports, '_generate_text', change)
    notifications.process_notification(factory, settings, aid)
    assert calls == []
    with factory() as session:
        assert session.get(AnalysisNotification, aid).status == 'failed'


def test_final_position_check_after_report_prevents_stale_send(setup, monkeypatch):
    factory, settings, calls = setup
    aid = request_one(factory, settings)
    real_generate = notifications.generate_report
    def change_after_report(session, analysis, config):
        view = real_generate(session, analysis, config)
        session.rollback()
        with factory() as writer:
            shot = writer.scalar(select(Shot).where(Shot.analysis_id == aid))
            shot.result = ShotResult.NEEDS_REVIEW
            writer.commit()
        return view
    monkeypatch.setattr(notifications, 'generate_report', change_after_report)
    notifications.process_notification(factory, settings, aid)
    assert calls == []
    with factory() as session:
        row = session.get(AnalysisNotification, aid)
        assert row.status == 'failed' and row.error_code == 'EMAIL_RECORD_CHANGED'


def test_concurrent_dispatch_claims_only_once(setup):
    factory, settings, calls = setup
    aid = request_one(factory, settings)
    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(lambda _: notifications.process_notification(factory, settings, aid), range(3)))
    assert len(calls) == 1


@pytest.mark.parametrize('status,expected', [('preparing','accepted'), ('sending','unknown')])
def test_expired_process_recovery_never_repeats_uncertain_send(setup, status, expected):
    factory, settings, calls = setup
    aid = request_one(factory, settings)
    with factory() as session:
        row = session.get(AnalysisNotification, aid)
        row.status, row.claim_token = status, 'old-claim'
        row.lease_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.commit()
    notifications.process_notification(factory, settings, aid)
    notifications.process_notification(factory, settings, aid)
    with factory() as session:
        assert session.get(AnalysisNotification, aid).status == expected
    assert len(calls) == (1 if status == 'preparing' else 0)


def test_cancellation_and_owner_change_prevent_delivery(setup):
    factory, settings, calls = setup
    aid = request_one(factory, settings)
    with factory() as session:
        notifications.cancel_subscription(session, session.get(Analysis, aid), settings)
        assert session.get(AnalysisNotification, aid).recipient_email is None
    notifications.process_notification(factory, settings, aid)
    assert calls == []
    with factory() as session:
        notifications.subscribe(session, session.get(Analysis, aid), settings, True)
        session.get(Analysis, aid).owner_id = 'another'
        session.commit()
    notifications.process_notification(factory, settings, aid)
    assert calls == []


def test_disabled_configuration_consent_and_verified_email_are_required(setup,monkeypatch):
    factory,settings,calls = setup
    with factory() as session:
        analysis = add_practice(session)
        with pytest.raises(notifications.NotificationError) as error:
            notifications.subscribe(session,analysis,settings,False)
        assert error.value.code == 'CONSENT_REQUIRED'
        monkeypatch.setattr(notifications,'verified_account_email',lambda *a:None)
        with pytest.raises(notifications.NotificationError) as error:
            notifications.subscribe(session,analysis,settings,True)
        assert error.value.code == 'EMAIL_VERIFICATION_REQUIRED'
        for invalid in (settings.model_copy(update={'email_notifications_enabled':False}),
                        settings.model_copy(update={'gmail_sender':'bad-address'})):
            with pytest.raises(notifications.NotificationError) as error:
                notifications.subscribe(session,analysis,invalid,True)
            assert error.value.code == 'EMAIL_NOT_CONFIGURED'
        assert session.get(AnalysisNotification,analysis.id) is None
    assert calls == []


def test_changed_account_email_prevents_delivery_before_ai(setup,monkeypatch):
    factory,settings,calls = setup
    aid = request_one(factory,settings)
    monkeypatch.setattr(notifications,'verified_account_email',lambda *a:'new@example.com')
    monkeypatch.setattr(reports,'_generate_text',lambda *a:pytest.fail('Changed recipient reached AI'))
    notifications.process_notification(factory,settings,aid)
    assert calls == []
    with factory() as session:
        assert session.get(AnalysisNotification,aid).error_code == 'EMAIL_RECIPIENT_CHANGED'


def test_timeout_is_preserved_and_cannot_be_submitted_again(setup,monkeypatch):
    factory,settings,calls = setup
    aid = request_one(factory,settings)
    def timeout(message,config):
        calls.append('attempt')
        return 'unknown',None,'EMAIL_DELIVERY_UNKNOWN'
    monkeypatch.setattr(notifications,'send_message',timeout)
    notifications.process_notification(factory,settings,aid)
    notifications.process_notification(factory,settings,aid)
    assert calls == ['attempt']
    with factory() as session:
        assert session.get(AnalysisNotification,aid).status == 'unknown'
        with pytest.raises(notifications.NotificationError):
            notifications.subscribe(session,session.get(Analysis,aid),settings,True)


def test_api_requires_ownership_and_get_is_read_only(setup, monkeypatch):
    factory, settings, calls = setup
    app = FastAPI()
    app.state.settings = settings.model_copy(update={'auth_required':True})
    app.state.session_factory = factory
    app.include_router(router)
    async def signed_in(request: Request): request.state.auth_user_id = 'a'
    app.dependency_overrides[require_analysis_user] = signed_in
    with factory() as session:
        own = add_practice(session); other = add_practice(session, owner='other')
    monkeypatch.setattr(reports, '_generate_text', lambda *a: pytest.fail('GET reached AI'))
    with TestClient(app) as client:
        assert client.get(f'/api/analyses/{own.id}/email-notification').status_code == 200
        body = {'consent':True}
        assert client.post(f'/api/analyses/{own.id}/email-notification', json={**body,'email':'other@example.com'}).status_code == 422
        assert client.post(f'/api/analyses/{own.id}/email-notification', json=body).status_code == 200
        for method in ('GET','POST','DELETE'):
            assert client.request(method,f'/api/analyses/{other.id}/email-notification',json=body).status_code == 404
        assert client.delete(f'/api/analyses/{own.id}/email-notification').status_code == 200
    assert calls == []


@pytest.mark.parametrize('failure,expected',[
    ('none','accepted'),('auth','failed'),('recipient','failed'),('data','failed'),
    ('during_send','unknown'),('connect','failed'),('quit','accepted'),
])
def test_gmail_transport_uses_tls_and_preserves_uncertainty(setup,monkeypatch,failure,expected):
    factory,settings,_ = setup
    class FakeSMTP:
        def __init__(self,host,port,timeout,context):
            assert host == 'smtp.gmail.com' and port == 465 and timeout == 20
            assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
            if failure == 'connect': raise TimeoutError('test')
        def __enter__(self):return self
        def __exit__(self,*args):
            if failure == 'quit':raise smtplib.SMTPServerDisconnected('test')
        def login(self,sender,password):
            assert sender == 'test-sender@gmail.com' and password == 'fake-app-password'
            if failure == 'auth':raise smtplib.SMTPAuthenticationError(535,b'test')
        def send_message(self,message,from_addr,to_addrs):
            assert from_addr == settings.gmail_sender and to_addrs == ['a@example.com']
            if failure == 'recipient':raise smtplib.SMTPRecipientsRefused({'a@example.com':(550,b'test')})
            if failure == 'data':raise smtplib.SMTPDataError(550,b'test')
            if failure == 'during_send':raise TimeoutError('test')
            return {}
    monkeypatch.setattr(smtplib,'SMTP_SSL',FakeSMTP)
    with factory() as session:
        analysis = add_practice(session)
        view=reports.generate_report(session,analysis,settings)
        message=notifications.message_payload(analysis,reports.comparison_context(session,analysis),view['report'],settings,'a@example.com')
    assert REAL_SEND(message,settings)[0] == expected


def test_mail_contains_pdf_and_escapes_html_without_public_domain(setup):
    factory,settings,_ = setup
    settings=settings.model_copy(update={'public_app_url':None})
    assert notifications.configured(settings)
    with factory() as session:
        analysis=add_practice(session)
        analysis.title='<script>bad</script>\r\nBcc: bad@example.com'
        session.commit()
        context=reports.comparison_context(session,analysis)
        message=notifications.message_payload(analysis,context,fake_text(context,settings),settings,'a@example.com')
    assert message.get('Bcc') is None
    plain=message.get_body(preferencelist=('plain',)).get_content()
    html=message.get_body(preferencelist=('html',)).get_content()
    assert '첨부한 PDF' in plain
    attachment=next(message.iter_attachments())
    assert attachment.get_filename() == 'ShotLog-practice-report.pdf'
    assert attachment.get_payload(decode=True).startswith(b'%PDF-')
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert 'localhost' not in plain and '127.0.0.1' not in plain


def test_verified_email_lookup_uses_only_confirmed_owner():
    from sqlalchemy import create_engine,text
    from sqlalchemy.orm import Session
    from app.services.account_profile import verified_account_email
    engine=create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS auth"))
        connection.execute(text('CREATE TABLE auth.users (id TEXT PRIMARY KEY,email TEXT,email_confirmed_at TEXT)'))
        connection.execute(text("INSERT INTO auth.users VALUES ('a','a@example.com','2026-11-05'),('b','b@example.com',NULL)"))
    with Session(engine) as session:
        assert verified_account_email(session,'a') == 'a@example.com'
        assert verified_account_email(session,'b') is None
        assert verified_account_email(session,"a' OR 1=1 --") is None
    engine.dispose()
