from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.models import Analysis, AnalysisJob, AnalysisNotification, AnalysisEmailRequest, JobStatus, Player, Shot
from app.services import authentication
from test_phase12_history import make_client, add_record


def bulk(client, ids, **kwargs):
    return client.post('/api/analyses/bulk-delete', json={'analysis_ids': ids}, **kwargs)


def test_one_batch_removes_related_rows_and_files_with_one_commit(tmp_path):
    client, factory, settings = make_client(tmp_path)
    ids = [add_record(factory, settings) for _ in range(3)]
    unselected = add_record(factory, settings)
    for record_id in ids:
        for folder in (settings.work_path / record_id, settings.upload_path / '.incoming' / record_id):
            folder.mkdir(parents=True)
            (folder / 'test.json').write_text('{}')
    with factory() as session:
        session.add(AnalysisNotification(analysis_id=ids[0], owner_id=str(uuid4())))
        session.add(AnalysisEmailRequest(request_id=str(uuid4()), analysis_id=ids[0], owner_id=str(uuid4())))
        session.commit()
    commits = []
    event.listen(factory, 'after_commit', lambda _session: commits.append(True))
    response = bulk(client, [*ids, ids[0]])
    assert response.status_code == 200
    assert response.json() == {'removed': ids, 'failed': []}
    assert len(commits) == 1
    with factory() as session:
        for model in (Analysis, Player, Shot, AnalysisJob, AnalysisNotification, AnalysisEmailRequest):
            column = model.id if model is Analysis else model.analysis_id
            assert session.scalars(select(model).where(column.in_(ids))).all() == []
        assert session.get(Analysis, unselected) is not None
    assert (settings.upload_path / unselected / 'original.mp4').exists()
    for record_id in ids:
        assert not (settings.upload_path / record_id).exists()
        assert not (settings.work_path / record_id).exists()
        assert not (settings.upload_path / '.incoming' / record_id).exists()


def test_batch_file_cleanup_runs_concurrently(tmp_path, monkeypatch):
    client, factory, settings = make_client(tmp_path)
    ids = [add_record(factory, settings) for _ in range(2)]
    import shutil
    original = shutil.rmtree
    barrier = Barrier(2)

    def synchronized_remove(folder):
        barrier.wait(timeout=5)
        original(folder)

    monkeypatch.setattr('app.api.analyses.shutil.rmtree', synchronized_remove)
    assert bulk(client, ids).json() == {'removed': ids, 'failed': []}


def test_active_and_file_failures_remain_while_other_records_are_removed(tmp_path, monkeypatch):
    client, factory, settings = make_client(tmp_path)
    good = add_record(factory, settings)
    active = add_record(factory, settings, job_status=JobStatus.RUNNING)
    blocked = add_record(factory, settings)
    import shutil
    original = shutil.rmtree

    def fail_blocked(folder):
        if folder.name == blocked:
            raise PermissionError('test-only error')
        original(folder)

    monkeypatch.setattr('app.api.analyses.shutil.rmtree', fail_blocked)
    response = bulk(client, [good, active, blocked]).json()
    assert response['removed'] == [good]
    assert [(item['id'], item['code']) for item in response['failed']] == [(active, 'ANALYSIS_ACTIVE'), (blocked, 'PARTIAL_DELETE')]
    for record_id in (active, blocked):
        assert client.get(f'/api/analyses/{record_id}').status_code == 200
        assert (settings.upload_path / record_id / 'original.mp4').exists()
    monkeypatch.setattr('app.api.analyses.shutil.rmtree', original)
    assert bulk(client, [blocked]).json() == {'removed': [blocked], 'failed': []}


def test_db_failure_rolls_back_whole_successful_set_and_allows_retry(tmp_path, monkeypatch):
    client, factory, settings = make_client(tmp_path)
    ids = [add_record(factory, settings) for _ in range(2)]
    original = factory.class_.commit

    def fail_commit(_session):
        raise RuntimeError('test-only database error')

    monkeypatch.setattr(factory.class_, 'commit', fail_commit)
    payload = bulk(client, ids).json()
    assert payload['removed'] == []
    assert [item['code'] for item in payload['failed']] == ['DATABASE_DELETE_FAILED'] * 2
    with factory() as session:
        assert all(session.get(Analysis, record_id) is not None for record_id in ids)
        assert len(session.scalars(select(Shot).where(Shot.analysis_id.in_(ids))).all()) == 2
    monkeypatch.setattr(factory.class_, 'commit', original)
    assert bulk(client, ids).json() == {'removed': ids, 'failed': []}


def test_batch_enforces_owner_and_cookie_origin(tmp_path, monkeypatch):
    client, factory, settings = make_client(tmp_path)
    own, other = [add_record(factory, settings) for _ in range(2)]
    user_id = str(uuid4())
    with factory() as session:
        session.get(Analysis, own).owner_id = user_id
        session.get(Analysis, other).owner_id = str(uuid4())
        session.commit()
    settings.auth_required = True

    async def verified(_settings, _token):
        return {'id': user_id}

    monkeypatch.setattr(authentication, 'verify_token', verified)
    client.cookies.set(authentication.COOKIE_NAME, 'test-token')
    assert bulk(client, [own]).status_code == 403
    missing = str(uuid4())
    payload = bulk(client, [own, other, missing], headers={'Origin': 'http://127.0.0.1:5173'}).json()
    assert payload['removed'] == [own]
    assert [item['code'] for item in payload['failed']] == ['NOT_FOUND', 'NOT_FOUND']
    assert payload['failed'][0]['message'] == payload['failed'][1]['message']
    with factory() as session:
        assert session.get(Analysis, other) is not None
    assert (settings.upload_path / other / 'original.mp4').exists()


@pytest.mark.parametrize('ids', [[], ['../outside'], [str(uuid4())] * 1001])
def test_invalid_batch_never_touches_files(tmp_path, ids):
    client, factory, settings = make_client(tmp_path)
    record_id = add_record(factory, settings)
    assert bulk(client, ids).status_code == 422
    assert client.get(f'/api/analyses/{record_id}').status_code == 200
    assert (settings.upload_path / record_id / 'original.mp4').exists()
