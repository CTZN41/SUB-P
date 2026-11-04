import numpy as np
import pytest
from app.analysis.foot_evidence import foot_proxy_is_clipped
from app.analysis.phase7_pipeline import attach_candidate_foot_tracks
from app.services.openai_shot_review import _projected_foot_position, _projected_plant_position


@pytest.mark.parametrize('foot', [[100,1080],[100,1079],[0,700],[1920,700],[float('nan'),700]])
def test_image_edge_and_invalid_proxies_are_not_projected(foot):
    assert foot_proxy_is_clipped(foot,[1920,1080])
    assert _projected_foot_position(np.eye(3), {'foot_point_px':foot,'frame_size_px':[1920,1080]}) is None


def test_side_cropped_person_marks_even_a_central_proxy():
    c={'time_ms':5000,'scene_id':0,'foot_point_px':[250,700]}
    attach_candidate_foot_tracks([c],[{'time_ms':4600,'scene_id':0,'person':{'xyxy':[0,100,500,700]}}],1920,1080)
    assert c['foot_track'][0]['foot_point_clipped']
    assert _projected_plant_position(np.eye(3),c) == (None,None)


def test_rejected_plant_is_not_silently_replaced_by_later_airborne_box():
    c={'time_ms':5000,'frame_size_px':[1920,1080],'foot_track':[
        {'time_ms':4600,'foot_point_px':[100,1080]},
        {'time_ms':4800,'foot_point_px':[100,900]},
    ]}
    assert _projected_plant_position(np.eye(3),c) == (None,None)


def test_valid_and_legacy_proxy_projections_still_work():
    assert not foot_proxy_is_clipped([25,30],[1920,1080])
    assert _projected_foot_position(np.eye(3),{'foot_point_px':[25,30]}) == (25,30)
    c={'time_ms':5000,'frame_size_px':[1920,1080],'foot_track':[{'time_ms':4600,'foot_point_px':[25,30]}]}
    assert _projected_plant_position(np.eye(3),c) == (None,None)
