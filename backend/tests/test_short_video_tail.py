from pathlib import Path
from types import SimpleNamespace
import sys

import cv2
import numpy as np
import pytest

from app.analysis.phase7_pipeline import analyze_video


@pytest.mark.parametrize('prefetch', [False, True])
@pytest.mark.parametrize('available,should_succeed', [(996, True), (800, False)])
def test_pipeline_accepts_only_a_short_physical_tail_and_reports_actual_coverage(tmp_path, monkeypatch, prefetch, available, should_succeed):
    import app.analysis.phase7_pipeline as pipeline
    class Capture:
        position=0
        def isOpened(self):return True
        def get(self,prop):
            return {cv2.CAP_PROP_FPS:30,cv2.CAP_PROP_FRAME_COUNT:1000,
                    cv2.CAP_PROP_FRAME_WIDTH:640,cv2.CAP_PROP_FRAME_HEIGHT:360}[prop]
        def set(self,prop,value):self.position=int(value);return True
        def grab(self):
            if self.position>=available:return False
            self.position+=1;return True
        def read(self):
            if self.position>=available:return False,None
            self.position+=1;return True,np.zeros((360,640,3),dtype=np.uint8)
        def release(self):pass
    class Model:
        names={0:'person',1:'basketball'}
        def predict(self,*a,**k):return [None]
    monkeypatch.setitem(sys.modules,'ultralytics',SimpleNamespace(YOLO=lambda *a,**k:Model()))
    monkeypatch.setattr(cv2,'VideoCapture',lambda *a:Capture())
    monkeypatch.setattr(pipeline,'_detections',lambda *a:{'person':[{'xyxy':[100,50,200,330],'confidence':.9}],
        'basketball':[{'xyxy':[240,100,260,120],'confidence':.9}],'rim':[]})
    monkeypatch.setattr(pipeline,'remove_static_ball_detections',lambda *a:0)
    monkeypatch.setattr(pipeline,'trajectory_candidates',lambda *a:[])
    monkeypatch.setattr(pipeline,'rim_approach_candidates',lambda *a:[])
    video=tmp_path/'test.mp4';video.write_bytes(b'test')
    model=tmp_path/'model.pt';model.write_bytes(b'test')
    if not should_succeed:
        with pytest.raises(RuntimeError):analyze_video(video,model,[400,80],prefetch_frames=prefetch)
        return
    result=analyze_video(video,model,[400,80],prefetch_frames=prefetch)
    assert result['decode_warning']['first_unread_frame']==996
    assert result['decode_warning']['frames_fabricated'] is False
    assert result['duration_ms']==33333
    assert result['range_end_ms']==33100


def test_completed_local_checkpoint_survives_later_stage_failure_without_publishing_fake_result(tmp_path,monkeypatch):
    import json
    from sqlalchemy import select
    from app.models import AnalysisJob,JobStatus
    from app.services import analysis_jobs as jobs
    from test_analysis_jobs import make_ready_client
    client,aid,engine,factory=make_ready_client(tmp_path)
    with factory() as session:
        job=AnalysisJob(analysis_id=aid,status=JobStatus.QUEUED,is_simulation=False)
        session.add(job);session.commit();jid=job.id
    settings=client.app.state.settings
    monkeypatch.setattr(jobs,'candidate_models',lambda *a:(Path('model'),Path('pose'),'cpu'))
    monkeypatch.setattr(jobs,'analyze_video',lambda *a,**k:{'candidates':[]})
    monkeypatch.setattr(jobs,'enrich_candidates',lambda *a,**k:a[4])
    def fail(*a,**k):raise RuntimeError('raw message must never be saved in diagnostics')
    monkeypatch.setattr(jobs,'review_candidates_with_openai',fail)
    jobs.run_phase7_job(factory,settings,aid,jid)
    folder=settings.work_path/aid
    assert (folder/f'local-candidates-{jid}.json').is_file()
    assert not (folder/'phase7-candidates.json').exists()
    diagnostic=(folder/f'failure-{jid}.json').read_text(encoding='utf-8')
    assert 'raw message must never' not in diagnostic
    assert json.loads(diagnostic)['exception_chain'][0]['type']=='RuntimeError'
    with factory() as session:
        assert session.get(AnalysisJob,jid).status is JobStatus.FAILED
    engine.dispose()
