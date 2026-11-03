"""Explicit two-case paint-only full trial. Keeps original records unchanged."""
from pathlib import Path
import argparse
import json
import time
import httpx

ROOT=Path(__file__).resolve().parents[1]
CASES={
    'short12':('2a9396e5-9f05-4541-8cde-6cfa73cb1c55','SUB-P-three-point-problem-12s-60fps.mp4','11/03 페인트존만 후반3점 12초'),
    'sample32':('4ae7224e-2826-4b56-b975-e6212c5b625c','SUB-P-fixed-32s-60fps-outcome-test.mp4','11/03 페인트존만 32초 비교'),
}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--case',choices=CASES,required=True);parser.add_argument('--run',action='store_true');parser.add_argument('--prepare-only',action='store_true');args=parser.parse_args()
    if not args.run:raise SystemExit('Use --run only for an authorized paid trial')
    source,name,title=CASES[args.case]
    folder=ROOT/'work/verification-2026-11-03/paint-only-trial';folder.mkdir(parents=True,exist_ok=True)
    manifest=folder/f'{args.case}.json'
    def save():manifest.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with httpx.Client(base_url='http://127.0.0.1:8000/api',timeout=120,headers={'Connection':'close'}) as client:
        def get(path):
            response=client.get(path);response.raise_for_status();return response.json()
        if manifest.exists():state=json.loads(manifest.read_text(encoding='utf-8'))
        else:
            reference=get(f'/analyses/{source}/calibrations')[0]
            with (ROOT/'uploads'/source/'original.mp4').open('rb') as stream:
                response=client.post('/analyses',data={'title':title,'play_type':'solo','recorded_date':'2026-11-03','contains_others':'false','consent_confirmed':'false','ai_notice_confirmed':'true'},files={'video':(name,stream,'video/mp4')})
            response.raise_for_status();aid=response.json()['id']
            state={'source_id':source,'analysis_id':aid,'case':args.case,'phase':'registered','arc_removed':True};save()
            # Persist exactly the user-confirmed paint/hoop geometry, excluding arc.
            payload={key:reference[key] for key in ('frame_time_ms','image_points','court_points','hoop_point')}
            payload['three_point_line']=None
            client.put(f'/analyses/{aid}/calibration',json=payload).raise_for_status()
            client.post(f'/analyses/{aid}/accept-warnings').raise_for_status()
            state['phase']='ready';save()
        aid=state['analysis_id']
        if args.prepare_only:
            print(json.dumps({'analysis_id':aid,'phase':state['phase'],'analysis_not_started':True}),flush=True);return
        response=client.get(f'/analyses/{aid}/jobs/current')
        if response.status_code==404:
            response=client.post(f'/analyses/{aid}/jobs/start-real');response.raise_for_status();job=response.json()
        else:response.raise_for_status();job=response.json()
        state['job_id']=job['id'];save();print(json.dumps({'case':args.case,'analysis_id':aid,'job_id':job['id']},ensure_ascii=True),flush=True)
        previous=None
        for _ in range(90):
            try:job=get(f'/analyses/{aid}/jobs/current')
            except (httpx.ReadError,httpx.ConnectError,httpx.ReadTimeout):
                print('Read-only poll interrupted; job is not restarted',flush=True);time.sleep(2);continue
            now=(job['status'],job['stage'])
            if now!=previous:
                print(json.dumps({key:job.get(key) for key in ('status','stage','processed_ms','error_code')},ensure_ascii=True),flush=True);previous=now
            if job['status'] in ('succeeded','failed','canceled'):
                state.update(phase=job['status'],started_at=job['started_at'],finished_at=job['finished_at']);save();return
            time.sleep(5)
        state['phase']='monitor_timeout_job_not_canceled';save()


if __name__=='__main__':main()
