"""Read-only local regression: saved candidates, manual views and foot proxies.

No analysis start, OpenAI request, database write or change to saved candidates.
Run from backend with --analysis-id repeated and --output evaluation/results/....json.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import urllib.request

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.openai_shot_review import _projected_foot_position, _projected_plant_position
from app.services.court_calibration import three_point_trace_side
from app.services.view_alignment_trial import group_camera_views


def audit(aid, api_base):
    def get(path):
        with urllib.request.urlopen(f'{api_base}/analyses/{aid}{path}',timeout=20) as response:
            return json.load(response)
    details, saved = get(''), get('/calibrations')
    result = json.loads((ROOT/'work'/aid/'phase7-candidates.json').read_text(encoding='utf-8'))
    source = result['candidates']
    indexes = sorted(set([0, min(1,len(source)-1),len(source)-1]))
    if len(source)>50:
        indexes = sorted(set(indexes + [min(range(len(source)),key=lambda i: abs(source[i]['time_ms']-t)) for t in (92568,192454,498330,592548)]))
    candidates = deepcopy([source[i] for i in indexes])
    views = [{key:v[key] for key in ('frame_time_ms','image_points','transform','three_point_line')} for v in saved]
    video = ROOT/'uploads'/aid/'original.mp4'
    cap=cv2.VideoCapture(str(video));size=[int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))];cap.release()
    _, reports = group_camera_views(video,candidates,dict(views[0],views=views))
    rows=[]
    for candidate, report in zip(candidates,reports):
        candidate['frame_size_px']=size  # Supply actual size to legacy results.
        matrix=candidate.get('validated_court_transform')
        before = candidate.get('ai_review',{})
        projected = _projected_foot_position(matrix,candidate)
        plant, plant_ms = _projected_plant_position(matrix,candidate)
        foot = next((s.get('foot_point_px') for s in candidate.get('foot_track',[]) if s.get('time_ms')==plant_ms),None)
        if not candidate.get('foot_track') and projected is not None:
            foot=candidate.get('foot_point_px')
        arc=candidate.get('validated_three_point_line')
        side=three_point_trace_side(arc,foot,matrix) if arc and foot else None
        rows.append({'time_ms':candidate['time_ms'],'camera_valid':candidate['calibration_view_valid'],'view_id':report['view_id'],
                     'old_plant_projection':before.get('calibrated_plant_projection'),'new_plant_projection':plant,
                     'plant_time_ms':plant_ms,'new_release_projection':projected,'manual_trace_side':side,
                     'old_zone':before.get('court_zone'),'old_type':before.get('shot_type'),
                     'saved_records_modified':False})
    return {'analysis_id':aid,'duration_ms':details['duration_ms'],'manual_views':len(views),'samples':rows}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--analysis-id',action='append',required=True)
    parser.add_argument('--api-base',default='http://127.0.0.1:8000/api')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    results=[audit(aid,args.api_base.rstrip('/')) for aid in args.analysis_id]
    output={'scope':'Read-only saved evidence; no OpenAI calls, YOLO inference, data mutation or independent accuracy claim','cases':results}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'cases':len(results),'samples':sum(len(r['samples']) for r in results),'valid_views':sum(s['camera_valid'] for r in results for s in r['samples']),'output':str(args.output)}))


if __name__=='__main__':
    main()
