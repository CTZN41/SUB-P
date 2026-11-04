"""Replay original AI responses through production review/save without new API calls."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import Base
from app.models import Shot
from app.services import openai_shot_review as review
from app.services.sole_contact import ContactSequence, project_xy
from app.services.phase8_shots import materialize_shots_from_candidates
from scripts.verify_zone_boundaries import analysis_setup
from app.services.review_frame_trial import frame_map


def run(output):
    root=Path('work/verification-2026-11-04')
    source_path=Path('work/a4dccc34-508a-46e3-804c-7ed529e82709/phase7-candidates.json')
    video=Path('uploads/a4dccc34-508a-46e3-804c-7ed529e82709/original.mp4')
    data=source_path.read_bytes(); source=json.loads(data)
    pixel=json.loads((root/'sole-contact-3-18/report.json').read_text(encoding='utf-8'))
    output.mkdir(parents=True,exist_ok=False)
    candidates=[deepcopy(source['candidates'][n-1]) for n in (3,18)]
    decisions=[review.ShotReviewDecision.model_validate(c['ai_review']['model_decisions']['primary']) for c in candidates]
    contexts={c['scene_id']:review.CameraContextDecision.model_validate(c['decision']) for c in source['ai_review']['camera_contexts'] if c.get('decision')}
    responses=[ContactSequence.model_validate(r['response']) for r in pixel['rows']]
    with patch.object(review,'_build_shared_camera_contexts',return_value=(contexts,[],0)), \
         patch.object(review,'_review_once',side_effect=[(d,{'input_tokens':0,'output_tokens':0}) for d in decisions]), \
         patch('app.services.sole_contact.locate_soles_once',side_effect=[(d,{'input_tokens':0,'output_tokens':0}) for d in responses]):
        result=review.review_candidates_with_openai(video,{'candidates':candidates},
            Settings(_env_file=None,openai_api_key='cached-response-test',openai_vision_fallback_model=None),
            client=object(),calibration_transform=candidates[0]['validated_court_transform'],fast_image_sampling=True)
    engine=create_engine(f'sqlite:///{(output/"replay.sqlite").resolve().as_posix()}')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        analysis,calibration=analysis_setup(session,'REPLAY sole pixels, not new AI inference',600000)
        materialize_shots_from_candidates(session,analysis,calibration,result)
        session.commit();session.expire_all()
        shots=list(session.scalars(select(Shot).where(Shot.analysis_id==analysis.id).order_by(Shot.candidate_time_ms)))
        report={'kind':'original_video_cached_responses_production_pipeline_replay','new_paid_api_calls':0,
                'live_records_modified':False,'source_analysis_id':source_path.parent.name,'source_sha256':sha256(data).hexdigest(),
                'rows':[]}
        for number,c,shot in zip((3,18),candidates,shots,strict=True):
            assert shot.court_x is None and shot.court_y is None
            assert shot.result.value==c['ai_review']['outcome']
            report['rows'].append({'shot_number':number,'time_ms':shot.candidate_time_ms,'saved_result':shot.result.value,
                'saved_type':shot.shot_type.value,'saved_zone':shot.zone_code,'saved_xy':[shot.court_x,shot.court_y],
                'status':shot.status.value,'contact_reason':c['ai_review']['sole_contact']['reason'],
                'diagnostic_frames':len(c['ai_review']['sole_contact']['diagnostic_frames'])})
    engine.dispose()
    assert sha256(source_path.read_bytes()).hexdigest()==report['source_sha256']
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    # Select the same underlying image in the two repeated clips for comparison.
    indices=[2190,20194]
    frames=frame_map(video,[i/60*1000 for i in indices])
    full=[frames[i/60*1000] for i in indices]
    difference=float(np.mean(abs(full[0].astype('float32')-full[1].astype('float32'))))
    validated=json.loads((root/'sole-contact-3-18-floor-verified/report.json').read_text(encoding='utf-8'))
    comparisons=[]; tiles=[]
    for number,index,r,c in zip((3,18),indices,validated['rows'],candidates,strict=True):
        sample=next(f for f in r['evaluations'][0]['diagnostic_frames'] if f['frame_index']==index)
        average=np.mean([p['pixel'] for p in sample['contacts']],axis=0).tolist()
        comparisons.append({'shot_number':number,'frame_index':index,'frame_time_ms':index/60*1000,
            'sole_mean_pixel':average,'xy_from_own_transform':sample['center_xy_ft'],
            'y_error_interval_ft':sample['y_interval_ft'],'minimum_sole_confidence':min(p['confidence'] for p in sample['contacts'])})
        image=cv2.imread(str(root/'sole-contact-3-18-floor-verified'/f'shot{number:02d}'/f'annotated-{index}.png'))
        crop=image[560:840,800:1420]
        header=np.full((80,crop.shape[1],3),20,dtype=np.uint8)
        for text,y in ((f'Shot {number}: {index/60:.3f}s | same original frame',25),
                       (f"sole center px={average} | y={sample['center_xy_ft'][1]:.3f}ft",50),
                       ('cyan=line, magenta=calibration, amber=uncertain sole/error',72)):
            cv2.putText(header,text,(7,y),cv2.FONT_HERSHEY_SIMPLEX,.48,(240,240,240),1,cv2.LINE_AA)
        tiles.append(np.vstack([header,crop]))
    comp={'frame_offset':indices[1]-indices[0],'repeat_offset_ms':(indices[1]-indices[0])/60*1000,
          'full_frame_pixel_mae':difference,'measurements':comparisons,
          'center_distance_cm':float(np.linalg.norm(np.asarray(comparisons[0]['xy_from_own_transform'])-comparisons[1]['xy_from_own_transform'])*30.48),
          'same_pixel_different_transform':comparisons[0]['sole_mean_pixel']==comparisons[1]['sole_mean_pixel']}
    cv2.imwrite(str(output/'comparison.png'),np.hstack(tiles))
    (output/'frame-comparison.json').write_text(json.dumps(comp,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report));print(json.dumps(comp))


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    run(p.parse_args().output)
