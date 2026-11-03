from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import cv2
import numpy as np
import pytest

from app.core.config import Settings
from app.analysis.adaptive_trial import select_rescan_windows
from app.services import openai_shot_review as r
from app.services.candidate_enrichment import enrich_candidates
from app.services.outcome_evidence_trial import needs_direct_outcome_check, strict_outcome_once
from app.services.review_frame_trial import fast_candidate_frame_inputs
from app.services.view_alignment_trial import guarded_calibration


def decision(**changes):
    values=dict(is_shot=True,confidence=.9,is_shot_confidence=1,outcome="made",
                outcome_confidence=.9,shot_type="set_shot",shot_type_confidence=.9,
                court_zone="mid_center",court_x=25,court_y=22,position_confidence=.9,
                visible_evidence=["rim_passage"],sequence_start_offset_ms=-2000,
                sequence_end_offset_ms=2000,review_required=False,reason="test")
    values.update(changes)
    return r.ShotReviewDecision(**values)


@pytest.mark.parametrize("confidence", [.68,.70,.97])
def test_below_rim_alone_requires_check_even_if_confident(confidence):
    value=decision(outcome_confidence=confidence,visible_evidence=["ball_falling_below_rim"])
    assert not r._outcome_needs_dedicated_review(value)  # historical gap
    assert needs_direct_outcome_check(value)
    assert not needs_direct_outcome_check(decision())


def test_strict_check_does_not_accept_unsupported_make():
    response=SimpleNamespace(output_parsed=r.OutcomeReviewDecision(outcome="made",confidence=.99,
        visible_evidence=["ball_falling_below_rim"],review_required=False,reason="below"),usage=None)
    client=SimpleNamespace(responses=SimpleNamespace(parse=lambda **kw:response))
    value,_=strict_outcome_once(client,"unused",[])
    assert value.outcome=="unclear" and value.review_required


def test_long_video_rescan_budget_reaches_tail():
    source={"range_start_ms":0,"range_end_ms":600000,"candidates":[],
            "motion_hints":[{"time_ms":10000+i*60000,"confidence":.8,"scene_id":0} for i in range(10)]}
    windows=select_rescan_windows(source)
    assert len(windows)==10 and windows[-1][0]>540000
    bounded=select_rescan_windows(source,max_windows=4)
    assert len(bounded)==4 and bounded[-1]==windows[-1]


def test_late_rejected_candidate_still_gets_rescan(monkeypatch):
    monkeypatch.setattr("app.services.candidate_enrichment.review_shot_existence",
        lambda video,t,settings:{"verdict":"not_shot" if t%10000==1000 else "shot",
                                "confidence":.99,"release_offset_ms":0})
    def high(*args,**kw):
        time=kw["end_ms"]-3500
        return {"candidates":[{"time_ms":time+300,"frame_index":10}]}
    monkeypatch.setattr("app.services.candidate_enrichment.analyze_video",high)
    source={"duration_ms":600000,"source_fps":60,"candidates":[
        {"time_ms":1000+i*10000,"scene_id":0} for i in range(8)]}
    result=enrich_candidates(Path("unused"),Path("unused"),Path("unused"),[1,1],source,
                             Settings(_env_file=None,openai_api_key="test"))
    assert all(c["shot_gate"]["verdict"]=="shot" for c in result["candidates"])
    assert result["candidates"][-1]["time_ms"]==71300


def test_single_seek_frame_inputs_match_legacy(tmp_path):
    path=tmp_path/"sample.mp4"
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*"mp4v"),29.97,(320,180))
    assert writer.isOpened()
    for index in range(180):
        frame=np.full((180,320,3),(index%255,(index*3)%255,(index*7)%255),np.uint8)
        cv2.putText(frame,str(index),(20,100),0,1,(255,255,255),2)
        writer.write(frame)
    writer.release()
    for time in [200,1234,5101]:
        assert r.candidate_frame_inputs(path,time,[160,150])==fast_candidate_frame_inputs(path,time,[160,150])


def test_camera_guard_rejects_other_view_and_preserves_stable_view():
    image=np.random.default_rng(21).integers(0,256,(360,640,3),dtype=np.uint8)
    transform,info=guarded_calibration(image,image,[[1,0,0],[0,1,0],[0,0,1]])
    assert info["status"]=="aligned" and np.allclose(transform,np.eye(3),atol=1e-4)
    transform,info=guarded_calibration(image,np.zeros_like(image),np.eye(3))
    assert transform is None and info["status"]=="different_or_unverified_view"


def test_other_camera_does_not_receive_original_numeric_projection(monkeypatch):
    context=r.CameraContextDecision(camera_view="left_sideline",confidence=.9,
        court_left_direction_on_screen="left",toward_half_court_direction_on_screen="down",
        orientation_summary="fixed",visible_landmarks=["paint"],reason="test")
    directions=[]
    def contexts(client,video,candidates,settings,direction):
        directions.append(direction)
        return {int(candidates[0]["scene_id"]):context},[],0
    monkeypatch.setattr(r,"_build_shared_camera_contexts",contexts)
    monkeypatch.setattr(r,"candidate_frame_inputs",lambda video,time,*args:time)
    projections={}
    def once(client,model,images,**kw):
        projections[images]=kw["projected_foot"]
        return decision(),{"input_tokens":1,"output_tokens":1}
    monkeypatch.setattr(r,"_review_once",once)
    identity=[[1,0,0],[0,1,0],[0,0,1]]
    source={"candidates":[{"time_ms":1000,"scene_id":0,"foot_point_px":[25,22]},
                          {"time_ms":2000,"scene_id":1,"foot_point_px":[25,22]}]}
    result=r.review_candidates_with_openai(Path("unused"),source,
        Settings(_env_file=None,openai_api_key="test",openai_vision_fallback_model=None),
        client=object(),calibration_transform=identity,camera_view_calibrations={0:identity,1:None})
    assert projections[1000]==(25,22) and projections[2000] is None
    assert directions[-1] is None
    assert result["candidates"][1]["ai_review"]["calibrated_foot_projection"] is None


def test_initial_gate_parallel_preserves_call_budget(monkeypatch):
    calls=[]
    def gate(video,time,settings,*,client=None):
        calls.append(time)
        return {"verdict":"shot","confidence":.99,"release_offset_ms":300,
                "usage":{"input_tokens":2,"output_tokens":1}}
    monkeypatch.setattr("app.services.candidate_enrichment.review_shot_existence",gate)
    source={"duration_ms":10000,"source_fps":60,"candidates":[{"time_ms":1000},{"time_ms":4000}]}
    settings=Settings(_env_file=None,openai_api_key="test",openai_max_shot_gate_reviews=1)
    result=enrich_candidates(Path("unused"),Path("unused"),Path("unused"),[1,1],source,
                             settings,gate_workers=2,gate_client=object())
    assert calls==[1000]
    assert [c["time_ms"] for c in result["candidates"]]==[1300,4000]
    assert result["shot_gate_summary"]["calls"]==1
    assert result["shot_gate_summary"]["unreviewed_candidates"]==1


def test_initial_gate_sequential_parallel_same_results(monkeypatch):
    def gate(video,time,settings,*,client=None):
        return {"verdict":"shot","confidence":.99,"release_offset_ms":300,
                "usage":{"input_tokens":2,"output_tokens":1},"seconds":1}
    monkeypatch.setattr("app.services.candidate_enrichment.review_shot_existence",gate)
    source={"duration_ms":10000,"source_fps":60,"candidates":[{"time_ms":1000},{"time_ms":4000}]}
    settings=Settings(_env_file=None,openai_api_key="test")
    a=enrich_candidates(Path("unused"),Path("unused"),Path("unused"),[1,1],deepcopy(source),settings)
    b=enrich_candidates(Path("unused"),Path("unused"),Path("unused"),[1,1],deepcopy(source),settings,gate_workers=2)
    assert a==b


def test_509_second_candidate_accepts_release_1834ms_later(monkeypatch):
    monkeypatch.setattr('app.services.candidate_enrichment.analyze_video',
        lambda *args, **kwargs: {"candidates": [{"time_ms": 511766, "frame_index": 30706}]})
    def gate(video, time, settings, **kwargs):
        return {"status": "complete", "verdict": "shot" if time == 511766 else "not_shot",
                "confidence": .99, "release_offset_ms": 0}
    monkeypatch.setattr('app.services.candidate_enrichment.review_shot_existence', gate)
    source = {"duration_ms": 599050, "source_fps": 60,
              "candidates": [{"time_ms": 506100}, {"time_ms": 509932}]}
    result = enrich_candidates(Path('unused'), Path('unused'), Path('unused'), [1, 1], source,
                               Settings(_env_file=None, openai_api_key='test'))
    assert [c['time_ms'] for c in result['candidates']] == [506100, 511766]
    assert result['candidates'][1]['shot_gate']['verdict'] == 'shot'


def test_end_of_video_keeps_primary_type_position_without_paid_retry(monkeypatch):
    context = r.CameraContextDecision(camera_view='right_sideline', confidence=.9,
        court_left_direction_on_screen='left', toward_half_court_direction_on_screen='down',
        orientation_summary='fixed', visible_landmarks=['paint'], reason='test')
    monkeypatch.setattr(r, '_build_shared_camera_contexts', lambda *a: ({0: context}, [], 0))
    monkeypatch.setattr('app.services.review_frame_trial.evidence_rich_detail_inputs', lambda *a: [])
    def insufficient(*args):
        raise r.InsufficientOutcomeEvidenceError('insufficient rim evidence')
    monkeypatch.setattr('app.services.review_frame_trial.dense_rim_inputs', insufficient)
    calls = []
    def once(*args, **kwargs):
        calls.append('primary')
        return decision(outcome='unclear', outcome_confidence=0), {'input_tokens': 10, 'output_tokens': 2}
    monkeypatch.setattr(r, '_review_once', once)
    monkeypatch.setattr('app.services.outcome_evidence_trial.scoring_evidence_client', lambda client: client)
    result = r.review_candidates_with_openai(Path('unused'),
        {'candidates': [{'time_ms': 598349}]},
        Settings(_env_file=None, openai_api_key='test', openai_vision_fallback_model=None),
        client=object(), scoring_rim_evidence=True)
    review = result['candidates'][0]['ai_review']
    assert calls == ['primary']
    assert result['ai_review']['failed'] == 0
    assert review['is_shot'] and review['shot_type'] == 'set_shot'
    assert review['court_x'] == 25 and review['court_y'] == 22
    assert review['outcome'] == 'unclear' and review['review_required']
    assert result['ai_review']['usage'] == {'input_tokens': 10, 'output_tokens': 2}


def test_scoring_recheck_adds_only_five_midpoints_in_time_order(monkeypatch):
    from app.services import review_frame_trial as frames
    requested = []
    def extract(video, times):
        requested.extend(times)
        return {t: t for t in times}
    monkeypatch.setattr(frames, 'frame_map', extract)
    monkeypatch.setattr(r, '_hoop_crop', lambda frame, point: frame)
    monkeypatch.setattr(r, '_encode_frame', lambda frame: str(frame))
    images = frames.dense_rim_inputs(Path('unused'), 506098, [739, 332])
    assert requested == sorted(set(requested))
    assert {506098 + t for t in [850, 950, 1050, 1150, 1250]} <= set(requested)
    assert sum(v['type'] == 'input_image' for v in images) == 30
    assert sum(v.get('text', '').startswith('overview') for v in images) == 2


def test_scoring_recheck_still_rejects_a_truncated_video(monkeypatch):
    from app.services import review_frame_trial as frames
    monkeypatch.setattr(frames, 'frame_map', lambda video, times: {times[0]: times[0]})
    monkeypatch.setattr(r, '_hoop_crop', lambda frame, point: frame)
    monkeypatch.setattr(r, '_encode_frame', lambda frame: str(frame))
    with pytest.raises(r.InsufficientOutcomeEvidenceError):
        frames.dense_rim_inputs(Path('unused'), 598349, [739, 332])
