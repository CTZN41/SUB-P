"""Bounded paid A/B experiment; does not modify app defaults or stored shots."""
from pathlib import Path
import sys
import json
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from openai import OpenAI
from app.core.config import Settings
from app.services import openai_shot_review as review
from app.services.review_frame_trial import evidence_rich_detail_inputs, frame_map

EXTRA_OFFSETS = (-900,-800,-700,-600,-500,-400,-200,-100)


def dense_inputs(video, candidate):
    time=candidate['time_ms']
    images=evidence_rich_detail_inputs(video,time,candidate.get('foot_point_px'),candidate.get('tracked_hoop_px'))
    frames=frame_map(video,[time+offset for offset in EXTRA_OFFSETS])
    for offset in EXTRA_OFFSETS:
        frame=frames.get(time+offset)
        if frame is None:continue
        crop=review._focus_crop(frame,candidate.get('foot_point_px'))
        if crop is not None:
            images.extend([{'type':'input_text','text':f'player crop t={offset:+d}ms'},
                           {'type':'input_image','image_url':review._encode_frame(crop),'detail':'high'}])
    return images


def main():
    settings=Settings()
    if settings.openai_vision_model != 'gpt-6.1-terra' or not settings.openai_api_key:
        raise RuntimeError('Terra configuration required; no calls made')
    aid='2a9396e5-9f05-4541-8cde-6cfa73cb1c55'
    video=ROOT/'uploads'/aid/'original.mp4'
    saved=json.loads((ROOT/'work'/aid/'phase7-candidates.json').read_text(encoding='utf-8'))
    context=review.CameraContextDecision.model_validate(saved['ai_review']['camera_contexts'][0]['decision'])
    candidates=saved['candidates']
    output=ROOT/'evaluation/results/motion-density-trial-2026-11-03.json'
    report={'scope':'Six paid Terra detail calls, same prompt and context; only 8 player crops added. No DB mutation or production input change.',
            'source_analysis':aid,'extra_offsets_ms':EXTRA_OFFSETS,'provisional_labels':{'5700':'dribble_pull_up','11433':'step_back'},
            'limitations':['Only two distinct shots; provisional observer labels, not an overall accuracy estimate.','Per-request wall time includes network/model variability.'], 'runs':[]}
    with OpenAI(api_key=settings.openai_api_key,timeout=settings.openai_timeout_seconds,max_retries=0) as client:
        for index,mode,repeat in [(1,'base',1),(1,'dense',1),(1,'dense',2),(1,'base',2),(0,'base',1),(0,'dense',1)]:
            c=dict(candidates[index],frame_size_px=[1920,1080])
            transform=c.get('validated_court_transform')
            foot=review._projected_foot_position(transform,c)
            plant,_=review._projected_plant_position(transform,c)
            images=(dense_inputs(video,c) if mode=='dense' else evidence_rich_detail_inputs(video,c['time_ms'],c.get('foot_point_px'),c.get('tracked_hoop_px')))
            started=perf_counter()
            try:
                decision,usage=review._review_once(client,settings.openai_vision_model,images,camera_context=context,projected_foot=foot,projected_plant=plant)
                row={'time_ms':c['time_ms'],'mode':mode,'repeat':repeat,'image_count':sum(i['type']=='input_image' for i in images),
                     'seconds':round(perf_counter()-started,3),'usage':usage,'decision':decision.model_dump()}
            except Exception as error:
                row={'time_ms':c['time_ms'],'mode':mode,'repeat':repeat,'error_type':type(error).__name__,'seconds':round(perf_counter()-started,3)}
            report['runs'].append(row)
            report['api_usage']={key:sum(r.get('usage',{}).get(key,0) for r in report['runs']) for key in ('input_tokens','output_tokens')}
            output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps({key:row.get(key) for key in ('time_ms','mode','repeat','seconds','error_type')}|{'shot_type':row.get('decision',{}).get('shot_type'),'usage':row.get('usage')},ensure_ascii=True),flush=True)
    print('Complete: '+str(output),flush=True)


if __name__=='__main__':main()
