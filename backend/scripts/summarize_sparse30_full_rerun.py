"""Read-only status/comparison for the authorized 30-shot full rerun."""
import argparse
import csv
from collections import Counter
from datetime import timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding='utf-8')

from sqlalchemy import select
from app.core.config import get_settings
from app.db.session import create_database_engine,create_session_factory
from app.models import AnalysisJob,CourtCalibration,Shot
from app.services.phase8_shots import _court_position


def shot_data(shot,number):
    return {'number':number,'id':shot.id,'time_ms':shot.candidate_time_ms,
            'result':shot.result.value,'court_x':shot.court_x,'court_y':shot.court_y,
            'zone_code':shot.zone_code,'shot_type':shot.shot_type.value,'status':shot.status.value,
            'review_reason':shot.review_reason,
            'position_confidence':(shot.confidence_data or {}).get('ai_review',{}).get('position_confidence')}


def summary(shots):
    values={'total':len(shots),'made':0,'missed':0,'position_review':0,'outcome_review':0}
    for s in shots:
        valid=(s['court_x'] is not None and s['court_y'] is not None
               and math.isfinite(s['court_x']) and math.isfinite(s['court_y'])
               and 0<=s['court_x']<=50 and 0<=s['court_y']<=47)
        category='outcome_review' if s['result'] not in ('made','missed') else 'position_review' if not valid else s['result']
        values[category]+=1
    return values


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--report',action='store_true')
    parser.add_argument('--verification-name', default='sparse-30-full-rerun')
    args=parser.parse_args()
    if Path(args.verification_name).name != args.verification_name or args.verification_name in ('.', '..'):
        raise ValueError('A single verification directory name is required')
    settings=get_settings();root=Path(__file__).resolve().parents[1]
    work=settings.work_path/'verification-2026-11-04'/args.verification_name
    manifest=json.loads((work/'manifest.json').read_text(encoding='utf-8'))
    engine=create_database_engine(settings.database_url)
    with create_session_factory(engine)() as session:
        job=session.get(AnalysisJob,manifest['job_id'])
        elapsed=(job.finished_at-job.started_at).total_seconds() if job.finished_at else time.monotonic()-manifest['started_monotonic']
        current={'analysis_id':manifest['analysis_id'],'status':job.status.value,'stage':job.stage,
                 'processed_ms':job.processed_ms,'elapsed_seconds':round(elapsed,1),'error_code':job.error_code}
        (work/'monitor.json').write_text(json.dumps(current,ensure_ascii=False,indent=2),encoding='utf-8')
        if not args.report or job.status.value not in ('succeeded','failed','canceled'):
            print(json.dumps(current,ensure_ascii=False),flush=True)
            return
        if job.status.value!='succeeded':
            print(json.dumps(current,ensure_ascii=False),flush=True)
            return
        before=[shot_data(s,n) for n,s in enumerate(session.scalars(select(Shot).where(Shot.analysis_id==manifest['source_analysis_id']).order_by(Shot.candidate_time_ms,Shot.created_at)),1)]
        after=[shot_data(s,n) for n,s in enumerate(session.scalars(select(Shot).where(Shot.analysis_id==manifest['analysis_id']).order_by(Shot.candidate_time_ms,Shot.created_at)),1)]
        def calibration_values(analysis_id):
            return [[c.frame_time_ms,c.image_points,c.court_points,c.hoop_point,c.three_point_line,c.transform]
                    for c in session.scalars(select(CourtCalibration).where(
                        CourtCalibration.analysis_id==analysis_id).order_by(CourtCalibration.frame_time_ms))]
        calibration_identical = calibration_values(manifest['source_analysis_id']) == calibration_values(manifest['analysis_id'])
    engine.dispose()
    result=json.loads((settings.work_path/manifest['analysis_id']/'phase7-candidates.json').read_text(encoding='utf-8'))
    previous=json.loads((settings.work_path/manifest['source_analysis_id']/'phase7-candidates.json').read_text(encoding='utf-8'))
    labels_path=Path('C:/Users/aicam/Downloads/SUB-P-10min-30shots-black-gaps-60fps.csv')
    if labels_path.is_file():
        with labels_path.open(encoding='utf-8-sig') as label_file:
            labels=list(csv.DictReader(label_file))
        label_source='historical repeated-shot labels'
    else:
        labels=[{'number':s['number'],'reference_release_s':s['time_ms']/1000,
                 'historical_outcome':s['result']} for s in before]
        label_source='previous analysis stored decisions'
    used=set();matches=[];missing=[]
    for label in labels:
        target=round(float(label['reference_release_s'])*1000)
        choices=[s for s in after if s['id'] not in used and abs(s['time_ms']-target)<=1500]
        if not choices:
            missing.append({'reference_number':int(label['number']),'time_ms':target});continue
        s=min(choices,key=lambda s:abs(s['time_ms']-target));used.add(s['id'])
        matches.append({'reference_number':int(label['number']),'reference_time_ms':target,
                        'historical_outcome':label['historical_outcome'],'new_shot':s,
                        'timing_error_ms':s['time_ms']-target,
                        'outcome_matches':s['result']==label['historical_outcome']})
    highlights=[]
    for number in (3,4,7,8,14,18,22,24,26,29,30):
        original=before[number-1]
        choices=[s for s in after if abs(s['time_ms']-original['time_ms'])<=1500]
        highlights.append({'original_number':number,'before':original,
                           'after':min(choices,key=lambda s:abs(s['time_ms']-original['time_ms'])) if choices else None})
    report={**current,'source_analysis_id':manifest['source_analysis_id'],'result_url':manifest['result_url'],
        'registration_seconds':manifest['registration_seconds'],'calibration_identical':calibration_identical,
        'before':{'counts':summary(before),'stage_seconds':previous.get('timing_seconds'),'api_usage':previous.get('api_usage')},
        'after':{'counts':summary(after),'raw_result_counts':dict(Counter(s['result'] for s in after)),
                 'review_status_count':sum(s['status']=='needs_review' for s in after),
                 'stage_seconds':result.get('timing_seconds'),'api_usage':result.get('api_usage'),
                 'model':result.get('ai_review',{}).get('primary_model'),'runtime_options':result.get('runtime_options'),
                 'window_policy':result.get('candidate_window_policy'),
                 'foot_track_refresh_summary':result.get('foot_track_refresh_summary'),
                 'shot_gate_summary':result.get('shot_gate_summary'),'status_io':result.get('status_io')},
        'matches':matches,'missing':missing,'extra':[s for s in after if s['id'] not in used],
        'highlights':highlights,'shots':after,
        'excluded':[{'time_ms':c['time_ms'],'exclusion':c.get('exclusion')} for c in result.get('excluded_non_shot_candidates',[])],
        'source_result_unchanged':hashlib.sha256((settings.work_path/manifest['source_analysis_id']/'phase7-candidates.json').read_bytes()).hexdigest()==manifest['source_result_sha256'],
        'source_shots_unchanged':manifest.get('source_shots_unchanged'),
        'label_source':label_source,
        'label_limit':'Comparison to historical labels or saved AI decisions, not independently measured accuracy'}
    before_by_time={s['time_ms']:s for s in before}
    report['position_comparison']=[]
    for match in matches:
        original=before_by_time[match['reference_time_ms']] if label_source=='previous analysis stored decisions' else min(
            before,key=lambda s:abs(s['time_ms']-match['reference_time_ms']))
        new=match['new_shot']
        old_xy=original['court_x'] is not None and original['court_y'] is not None
        new_xy=new['court_x'] is not None and new['court_y'] is not None
        report['position_comparison'].append({'before_number':original['number'],'after_number':new['number'],
            'time_ms':original['time_ms'],'before_position':old_xy,'after_position':new_xy,
            'before_zone':original['zone_code'],'after_zone':new['zone_code'],
            'coordinate_shift_ft':round(math.hypot(new['court_x']-original['court_x'],new['court_y']-original['court_y']),3)
                                  if old_xy and new_xy else None})
    decided=[m for m in matches if m['new_shot']['result'] in ('made','missed')]
    report['historical_outcome_comparison']={'matched':len(matches),'decided':len(decided),
        'decided_agree':sum(m['outcome_matches'] for m in decided),
        'decided_disagree':[m for m in decided if not m['outcome_matches']],
        'held':[m for m in matches if m['new_shot']['result'] not in ('made','missed')]}
    position_stages=Counter()
    contact_applications=Counter()
    for candidate in result.get('candidates',[]):
        ai=candidate.get('ai_review') or {}
        decisions=ai.get('model_decisions') or {}
        for stage,key in (('primary','primary'),('before_contact','pre_contact_policy')):
            position_stages[stage]+=int(_court_position(decisions.get(key,ai),[])[0] is not None)
        position_stages['final']+=int(_court_position(ai,[])[0] is not None)
        if ai.get('sole_contact'):
            contact_applications[ai['sole_contact'].get('application','legacy')]+=1
    report['after']['position_stage_counts']=dict(position_stages)
    report['after']['contact_applications']=dict(contact_applications)
    report_name = ('sparse30-full-rerun' if args.verification_name == 'sparse-30-full-rerun'
                   else args.verification_name)
    destination=root/'evaluation/results'/f'{report_name}-2026-11-04.json'
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':report['status'],'elapsed_seconds':report['elapsed_seconds'],'before':report['before']['counts'],
        'after':report['after']['counts'],'matched':len(matches),'missing':len(missing),'extra':len(report['extra']),
        'decided_outcome_agreement':sum(m['outcome_matches'] for m in matches),
        'api_usage':report['after']['api_usage'],'source_result_unchanged':report['source_result_unchanged']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
