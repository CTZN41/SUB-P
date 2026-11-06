from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.analyses import router
from app.models import AnalysisJob, AnalysisNotification, AnalysisEmailRequest, AnalysisStatus, JobStatus, Shot, ShotResult, ReportEmailBatch
from app.services.authentication import require_analysis_user
from app.services import practice_report as reports, email_notifications as email
from app.services import selected_report_email as combined
from app.services.analysis_reuse import digest
from test_practice_report import store, add_practice
from test_email_notifications import setup


def client_for(factory, settings):
    app = FastAPI()
    app.state.settings = settings.model_copy(update={'auth_required': True})
    app.state.session_factory = factory
    app.include_router(router)
    async def signed_in(request: Request):
        request.state.auth_user_id = 'a'
    app.dependency_overrides[require_analysis_user] = signed_in
    return TestClient(app)


def payload(*ids):
    return {'consent': True, 'analysis_ids': list(ids), 'request_id': str(uuid4())}


def test_selected_reports_queue_atomically_and_retry_does_not_duplicate(setup, monkeypatch):
    factory, settings, calls = setup
    with factory() as session:
        ids = [add_practice(session).id for _ in range(2)]
    monkeypatch.setattr(reports, '_generate_text', lambda *a: pytest.fail('Queuing must not call AI'))
    body = payload(*ids)
    with client_for(factory, settings) as client:
        for _ in range(2):
            response = client.post('/api/analyses/bulk-report-email', json=body)
            assert response.status_code == 200
            assert sorted(response.json()['requested']) == sorted(ids)
            assert response.json()['combined'] is True
    with factory() as session:
        assert len(session.scalars(select(ReportEmailBatch)).all()) == 1
        assert not session.scalars(select(AnalysisNotification)).all()
    assert not calls


@pytest.mark.parametrize('blocked', ['position', 'outcome', 'active', 'unfinished', 'foreign', 'busy', 'consent'])
def test_mixed_selection_queues_nothing_if_any_record_is_blocked(setup, blocked):
    factory, settings, calls = setup
    with factory() as session:
        a = add_practice(session)
        b = add_practice(session, owner='other' if blocked == 'foreign' else 'a')
        shot = session.scalar(select(Shot).where(Shot.analysis_id == b.id))
        if blocked == 'position': shot.court_x = None
        if blocked == 'outcome': shot.result = ShotResult.NEEDS_REVIEW
        if blocked == 'active': session.add(AnalysisJob(analysis_id=b.id, status=JobStatus.RUNNING))
        if blocked == 'unfinished': b.status = AnalysisStatus.FAILED
        if blocked == 'busy': session.add(ReportEmailBatch(id=str(uuid4()), owner_id='a', analysis_ids=sorted([a.id,b.id]), selection_key=digest({'owner':'a','ids':sorted([a.id,b.id])}), recipient_email='a@example.com', status='preparing'))
        session.commit()
        ids = [a.id, b.id]
    body = payload(*ids)
    if blocked == 'consent': body['consent'] = False
    with client_for(factory, settings) as client:
        response = client.post('/api/analyses/bulk-report-email', json=body)
        assert response.status_code in (404, 409, 422)
    with factory() as session:
        assert session.get(AnalysisNotification, ids[0]) is None
        assert len(session.scalars(select(ReportEmailBatch)).all()) == (1 if blocked == 'busy' else 0)
    assert not calls


def test_history_ready_matches_completion_and_pending_clears_after_edit(setup):
    factory, settings, _ = setup
    with factory() as session:
        complete = add_practice(session)
        position = add_practice(session)
        outcome = add_practice(session)
        session.scalar(select(Shot).where(Shot.analysis_id == position.id)).court_x = None
        session.scalar(select(Shot).where(Shot.analysis_id == outcome.id)).result = ShotResult.NEEDS_REVIEW
        session.commit()
        ids = [complete.id, position.id, outcome.id]
    with client_for(factory, settings) as client:
        rows = {r['id']: r for r in client.get('/api/analyses').json()['analyses']}
        assert rows[ids[0]]['report_ready']
        assert not rows[ids[1]]['report_ready'] and rows[ids[1]]['position_pending'] == 1
        assert not rows[ids[2]]['report_ready'] and rows[ids[2]]['outcome_pending'] == 1
        with factory() as session:
            session.scalar(select(Shot).where(Shot.analysis_id == ids[1])).court_x = 25
            session.scalar(select(Shot).where(Shot.analysis_id == ids[2])).result = ShotResult.MISSED
            session.commit()
        rows = client.get('/api/analyses').json()['analyses']
        assert all(r['report_ready'] and r['position_pending'] == r['outcome_pending'] == 0 for r in rows)


def test_empty_and_duplicate_selections_are_rejected(setup):
    factory, settings, _ = setup
    with factory() as session:
        aid = add_practice(session).id
    with client_for(factory, settings) as client:
        for body in [payload(), payload(aid, aid)]:
            assert client.post('/api/analyses/bulk-report-email', json=body).status_code == 422


def test_two_videos_produce_one_email_one_pdf_with_summed_statistics(setup, monkeypatch):
    from io import BytesIO
    from pypdf import PdfReader
    factory, settings, calls = setup
    with factory() as session:
        a = add_practice(session, results=(ShotResult.MADE, ShotResult.MISSED))
        b = add_practice(session, results=(ShotResult.MADE, ShotResult.MADE, ShotResult.MADE))
        ids = [a.id, b.id]
        context, _ = combined.selected_context(session, 'a', sorted(ids))
        overall = next(row['current'] for row in context['rows'] if row['key'] == 'overall')
        assert overall == {'attempts': 5, 'made': 4, 'missed': 1, 'unresolved': 0, 'success_rate': 80.0}
        zone = next(row['current'] for row in context['rows'] if row['key'] == 'zone/mid_center')
        assert zone['attempts'] == 5 and zone['made'] == 4
    monkeypatch.setattr(reports, '_generate_text', lambda *a: pytest.fail('Combined PDF must not call AI'))
    body = payload(*ids)
    with client_for(factory, settings) as client:
        assert client.post('/api/analyses/bulk-report-email', json=body).status_code == 200
    combined.process_batch(factory, settings, body['request_id'])
    combined.process_batch(factory, settings, body['request_id'])
    assert len(calls) == 1
    message = calls[0]
    assert message['To'] == 'a@example.com'
    assert '전체 5개 · 성공 4개 · 실패 1개' in message.get_body(preferencelist=('plain',)).get_content()
    files = list(message.iter_attachments())
    assert len(files) == 1 and files[0].get_filename() == 'ShotLog-combined-report.pdf'
    pdf = PdfReader(BytesIO(files[0].get_payload(decode=True)))
    assert len(pdf.pages) == 1
    text = pdf.pages[0].extract_text()
    assert '선택한 영상 2개 통합 리포트' in text and '80%' in text and '4/5' in text


@pytest.mark.parametrize('change', ['position', 'result', 'deleted', 'owner', 'consent', 'email'])
def test_combined_delivery_rechecks_every_selected_record_before_dispatch(setup, monkeypatch, change):
    factory, settings, calls = setup
    with factory() as session:
        ids = [add_practice(session).id for _ in range(2)]
        body = payload(*ids)
        combined.request_batch(session, settings, 'a', ids, True, body['request_id'])
    build = email.build_practice_pdf
    def change_after_pdf(*args):
        result = build(*args)
        with factory() as session:
            from app.models import Analysis, UserEmailPreference
            if change == 'position': session.scalar(select(Shot).where(Shot.analysis_id == ids[1])).court_x = None
            if change == 'result': session.scalar(select(Shot).where(Shot.analysis_id == ids[1])).result = ShotResult.NEEDS_REVIEW
            if change == 'deleted': session.delete(session.get(Analysis, ids[1]))
            if change == 'owner': session.get(Analysis, ids[1]).owner_id = 'other'
            if change == 'consent': session.get(UserEmailPreference, 'a').enabled = False
            session.commit()
        if change == 'email': monkeypatch.setattr(email, 'verified_account_email', lambda *a: 'changed@example.com')
        return result
    monkeypatch.setattr(email, 'build_practice_pdf', change_after_pdf)
    combined.process_batch(factory, settings, body['request_id'])
    assert calls == []
    with factory() as session:
        assert session.get(ReportEmailBatch, body['request_id']).status in ('failed', 'canceled')


def test_uncertain_combined_dispatch_is_not_retried(setup, monkeypatch):
    from datetime import datetime, timedelta, timezone
    factory, settings, calls = setup
    with factory() as session:
        aid = add_practice(session).id
        body = payload(aid)
        combined.request_batch(session, settings, 'a', [aid], True, body['request_id'])
    def interrupted(*args):
        # Another connection must see the durable sending state before SMTP.
        with factory() as session:
            assert session.get(ReportEmailBatch, body['request_id']).status == 'sending'
        calls.append('attempt')
        raise TimeoutError('transport interrupted')
    monkeypatch.setattr(email, 'send_message', interrupted)
    combined.process_batch(factory, settings, body['request_id'])
    combined.process_batch(factory, settings, body['request_id'])
    assert calls == ['attempt']
    with factory() as session:
        assert session.get(ReportEmailBatch, body['request_id']).status == 'unknown'
        with pytest.raises(email.NotificationError):
            combined.request_batch(session, settings, 'a', [aid], True, str(uuid4()))


def test_monthly_creation_and_delivery_wait_for_all_positions_and_results(setup, monkeypatch):
    factory, settings, calls = setup
    with factory() as session:
        a = add_practice(session)
        b = add_practice(session, results=(ShotResult.NEEDS_REVIEW,))
        session.scalar(select(Shot).where(Shot.analysis_id == a.id)).court_x = None
        session.commit()
        ids = [a.id, b.id]
    monkeypatch.setattr(reports, '_generate_text', lambda *a: pytest.fail('Unconfirmed month must not call AI'))
    with client_for(factory, settings) as client:
        view = client.get('/api/analyses/monthly/practice-report?month=2026-11').json()
        assert not view['ready'] and view['position_unresolved'] == view['outcome_unresolved'] == 1
        assert client.post('/api/analyses/monthly/practice-report?month=2026-11').status_code == 409
        body = {'consent': True, 'request_id': str(uuid4())}
        assert client.post('/api/analyses/monthly/report-email?month=2026-11', json=body).status_code == 409
        with factory() as session:
            session.scalar(select(Shot).where(Shot.analysis_id == ids[0])).court_x = 25
            session.scalar(select(Shot).where(Shot.analysis_id == ids[1])).result = ShotResult.MISSED
            session.commit()
        assert client.get('/api/analyses/monthly/practice-report?month=2026-11').json()['ready']
        response = client.post('/api/analyses/monthly/report-email?month=2026-11', json=body)
        assert response.status_code == 200 and sorted(response.json()['requested']) == sorted(ids)
    combined.process_batch(factory, settings, body['request_id'])
    assert len(calls) == 1


@pytest.mark.parametrize('kind', ['position', 'outcome'])
def test_result_summary_is_blocked_until_the_final_field_is_saved(setup, monkeypatch, kind):
    from test_practice_report import fake_text
    factory, settings, _ = setup
    with factory() as session:
        a = add_practice(session, results=(ShotResult.MADE if kind == 'position' else ShotResult.NEEDS_REVIEW,))
        shot = session.scalar(select(Shot).where(Shot.analysis_id == a.id))
        if kind == 'position': shot.court_x = shot.court_y = None
        session.commit()
        aid, sid = a.id, shot.id
    generated = []
    monkeypatch.setattr(reports, '_generate_text', lambda c,s: generated.append(c) or fake_text(c,s))
    with client_for(factory, settings) as client:
        url = f'/api/analyses/{aid}/practice-report'
        assert not client.get(url).json()['ready']
        assert client.post(url).status_code == 409
        assert not generated
        changes = {'court_x':25, 'court_y':22} if kind == 'position' else {'result':'missed'}
        assert client.patch(f'/api/analyses/{aid}/shots/{sid}', json=changes).status_code == 200
        assert client.get(url).json()['ready']
        assert client.post(url).status_code == 200
        assert len(generated) == 1
