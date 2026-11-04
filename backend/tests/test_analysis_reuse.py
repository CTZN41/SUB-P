from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from threading import Lock
import time

import numpy as np
import pytest
from pydantic import BaseModel

from app.analysis.model_reuse_trial import ModelReuseTrial
from app.services.analysis_reuse import AnalysisReuse
from app.core.config import Settings


class Decision(BaseModel):
    verdict: str


def fake_client():
    calls = []
    def parse(**kwargs):
        calls.append(deepcopy(kwargs))
        time.sleep(.01)
        return SimpleNamespace(output_parsed=Decision(verdict='shot'),
            usage=SimpleNamespace(input_tokens=100, output_tokens=10))
    return SimpleNamespace(responses=SimpleNamespace(parse=parse)), calls


def request(**extra):
    return {'model': 'test-model', 'input': [{'text': 'same question', 'image': 'private pixels'}],
            'text_format': Decision, **extra}


def test_identical_parallel_requests_are_coalesced_and_mutation_is_isolated(tmp_path):
    reuse = AnalysisReuse(tmp_path, {'source': 1})
    client, calls = fake_client()
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: reuse.parse(client, **request()), range(2)))
    assert len(calls) == 1
    responses[0].output_parsed.verdict = 'changed'
    assert reuse.parse(client, **request()).output_parsed.verdict == 'shot'
    assert sum(r.usage.input_tokens for r in responses) == 100
    assert reuse.stats['request_reused'] == 2
    assert reuse.stats['input_tokens'] == 100
    persisted = ''.join(p.read_text(encoding='utf-8') for p in tmp_path.rglob('*.json'))
    assert 'private pixels' not in persisted and 'same question' not in persisted


def test_changed_evidence_question_model_or_schema_is_not_reused(tmp_path):
    reuse = AnalysisReuse(tmp_path, {})
    client, calls = fake_client()
    for kw in (request(), request(model='other'), request(input=[{'text': 'new question'}]),
               request(input=[{'text': 'same question', 'image': 'different pixels'}])):
        reuse.parse(client, **kw)
    assert len(calls) == 4


def test_restart_reuses_successful_response_but_retries_failure(tmp_path):
    client, calls = fake_client()
    first = AnalysisReuse(tmp_path, {})
    first.parse(client, **request())
    def broken(**kwargs):
        raise TimeoutError()
    with pytest.raises(TimeoutError):
        first.parse(SimpleNamespace(responses=SimpleNamespace(parse=broken)), **request(model='timeout'))
    resumed = AnalysisReuse(tmp_path, {})
    assert resumed.parse(client, **request()).usage.input_tokens == 0
    resumed.parse(client, **request(model='timeout'))
    assert len(calls) == 2 and resumed.stats['api_calls'] == 1


def test_invalid_or_incomplete_decision_is_not_checkpointed(tmp_path):
    reuse = AnalysisReuse(tmp_path, {})
    client = SimpleNamespace(responses=SimpleNamespace(parse=lambda **kw:
        SimpleNamespace(output_parsed=None, usage=SimpleNamespace(input_tokens=3, output_tokens=0))))
    reuse.parse(client, **request())
    reuse.parse(client, **request())
    assert reuse.stats['api_calls'] == 2
    assert not list(reuse.directory.rglob('requests/*.json'))


def test_stage_resume_and_invalidation_after_calibration_or_success(tmp_path):
    video, model = tmp_path/'video.mp4', tmp_path/'model.pt'
    video.write_bytes(b'video'); model.write_bytes(b'model')
    settings = Settings(_env_file=None, app_env='test')
    make = lambda calibration: AnalysisReuse.for_job(tmp_path/'work', video, [model], calibration, settings)
    first = make({'point': 1})
    first.save_stage('local_candidates', {'candidates': [1]})
    assert make({'point': 1}).load_stage('local_candidates') == {'candidates': [1]}
    assert make({'point': 2}).load_stage('local_candidates') is None
    fresh = make({'point': 2})
    fresh.save_stage('completed', {})
    assert make({'point': 2}).directory != fresh.directory


def test_raw_frame_cache_preserves_inputs_and_respects_model_pixels_size_and_memory(tmp_path):
    video, model = tmp_path/'video.mp4', tmp_path/'model.pt'
    video.write_bytes(b'video'); model.write_bytes(b'model')
    cache = ModelReuseTrial()
    frame = np.zeros((4,4,3), dtype=np.uint8)
    calls = []
    def compute():
        calls.append(1)
        return {'balls': [[1,2]]}
    def predict(pixels=frame, options=(640,)):
        return cache.prediction(video, 1, pixels, model, 'detect', 'cpu', options, compute)
    first = predict(); first['balls'][0][0] = 99
    assert predict() == {'balls': [[1,2]]}
    predict(options=(960,)); predict(frame+1)
    model.write_bytes(b'changed model')
    predict()
    assert len(calls) == 4 and cache.cache_stats['hits'] == 1
    cache.max_prediction_bytes = cache.prediction_bytes + 1
    predict(frame+2)
    assert cache.cache_stats['evictions'] > 0


def test_raw_cache_cannot_cross_predictor_threads(tmp_path):
    cache = ModelReuseTrial()
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(cache.prediction, None, None, None, None, None, None, None, None)
        with pytest.raises(RuntimeError):
            future.result()


def test_gate_reuse_skips_rebuilding_images_but_keeps_distinct_windows(tmp_path, monkeypatch):
    from app.analysis.shot_gate_trial import review_shot_existence, review_prior_shot_existence
    video = tmp_path/'video.mp4'; video.write_bytes(b'video')
    reuse = AnalysisReuse(tmp_path, {})
    calls = []
    def evaluate(*args, **kwargs):
        calls.append(kwargs['prior_window'])
        return {'status': 'complete', 'verdict': 'shot', 'confidence': .95,
                'usage': {'input_tokens': 20, 'output_tokens': 3}, 'seconds': 2}
    monkeypatch.setattr('app.analysis.shot_gate_trial._review_shot_gate', evaluate)
    client = reuse.client(None)
    settings = Settings(_env_file=None, app_env='test')
    assert review_shot_existence(video, 1000, settings, client=client)['usage']['input_tokens'] == 20
    assert review_shot_existence(video, 1000, settings, client=client)['usage']['input_tokens'] == 0
    review_prior_shot_existence(video, 1000, settings, client=client)
    review_shot_existence(video, 1100, settings, client=client)
    assert calls == [False, True, False]
    assert reuse.stats['decision_reused'] == 1


def test_worker_failure_resumes_completed_stages_and_requests_without_repeating_charges(tmp_path, monkeypatch):
    from test_analysis_jobs import make_ready_client
    from app.services.analysis_jobs import run_phase7_job
    from app.models import AnalysisJob, JobStatus
    client, analysis_id, engine, factory = make_ready_client(tmp_path)
    settings = client.app.state.settings.model_copy(update={'openai_api_key': 'test-placeholder'})
    api, calls = fake_client()
    api.close = lambda: None
    monkeypatch.setattr('openai.OpenAI', lambda **kw: api)
    stage_calls = {'local': 0, 'gate': 0, 'detail': 0}
    def local(*args, **kwargs):
        stage_calls['local'] += 1
        return {'duration_ms': 10000, 'candidates': [{'time_ms': 1000}], 'issues': {}}
    def gate(*args, **kwargs):
        stage_calls['gate'] += 1
        kwargs['gate_client'].responses.parse(**request(model='gate'))
        return args[4]
    def detail(*args, **kwargs):
        stage_calls['detail'] += 1
        kwargs['client'].responses.parse(**request(model='detail-first'))
        if stage_calls['detail'] == 1:
            raise RuntimeError('interrupted after first completed candidate')
        kwargs['client'].responses.parse(**request(model='detail-second'))
        result = args[1]
        result['ai_review'] = {'status': 'complete'}
        return result
    monkeypatch.setattr('app.services.analysis_jobs.analyze_video', local)
    monkeypatch.setattr('app.services.analysis_jobs.enrich_candidates', gate)
    monkeypatch.setattr('app.services.analysis_jobs.review_candidates_with_openai', detail)
    monkeypatch.setattr('app.services.analysis_jobs.group_camera_views', lambda *args: ({0: None}, []))
    for expected in (JobStatus.FAILED, JobStatus.SUCCEEDED):
        with factory() as db:
            job = AnalysisJob(analysis_id=analysis_id, status=JobStatus.QUEUED, stage='test', is_simulation=False)
            db.add(job); db.commit(); job_id = job.id
        run_phase7_job(factory, settings, analysis_id, job_id)
        with factory() as db:
            assert db.get(AnalysisJob, job_id).status is expected
    assert stage_calls == {'local': 1, 'gate': 1, 'detail': 2}
    assert len(calls) == 3
    import json
    saved = json.loads((settings.work_path / analysis_id / 'phase7-candidates.json').read_text(encoding='utf-8'))
    assert saved['api_usage'] == {'input_tokens': 100, 'output_tokens': 10}
    assert saved['reuse']['request_reused'] == 1
    assert set(saved['reuse']['stage_reused']) == {'local_candidates', 'enriched_candidates', 'camera_views'}
    engine.dispose()
