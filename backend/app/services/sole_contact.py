"""Pixel-grounded free-throw boundary evidence, separate from person boxes.

The box/pose track only locates the crop and time window. A vision response must
identify actual grounded soles. Observed white-line pixels independently check
the saved homography. Uncertain or straddling measurements cannot confirm XY.
"""
from __future__ import annotations

import base64
from math import ceil, floor, hypot
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel, Field
from typing import Literal

from app.services.ground_contact import plant_sample
from app.services.court_zones import classify_zone
from app.analysis.candidate_windows import WINDOW_RULES
from app.services.shot_review_policy import BOUNDARY_EVIDENCE_CONFIDENCE
from app.services.zone_boundaries import physical_clearance

CONTACT_BEFORE_MS = WINDOW_RULES['sole_pixels'].before_ms
CONTACT_AFTER_MS = WINDOW_RULES['sole_pixels'].after_ms  # bracket takeoff, not evidence of grounded feet
MIN_CONTACT_FRAMES = 3
MIN_CONTACT_SPAN_MS = 60
CONTACT_CONFIDENCE = BOUNDARY_EVIDENCE_CONFIDENCE


class SolePixel(BaseModel):
    foot_id: Literal['foot_1', 'foot_2']
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    error_px: float = Field(ge=1, le=20)
    confidence: float = Field(ge=0, le=1)


class ContactFrame(BaseModel):
    frame_index: int = Field(ge=0)
    state: Literal['both_grounded', 'one_grounded', 'airborne', 'unclear']
    contacts: list[SolePixel]


class ContactSequence(BaseModel):
    frames: list[ContactFrame]
    takeoff_frame_index: int | None = Field(ge=0)
    reason: str = Field(max_length=500)


def project_xy(matrix, point):
    h = np.asarray(matrix, dtype=float)
    if h.shape != (3, 3) or not np.isfinite(h).all():
        raise ValueError('invalid_homography')
    q = h @ np.array([*point, 1.])
    if not np.isfinite(q).all() or abs(q[2]) < 1e-8:
        raise ValueError('invalid_projection')
    return [float(q[0]/q[2]), float(q[1]/q[2])]


def free_throw_pixels(matrix):
    inverse = np.linalg.inv(np.asarray(matrix, dtype=float))
    return np.asarray([project_xy(inverse, p) for p in ((17, 19), (33, 19))])


def verify_fixed_floor(reference, current, court_transform):
    from app.services.view_alignment_trial import align_view, validate_floor_alignment
    try:
        corners = [project_xy(np.linalg.inv(court_transform), p) for p in ((17, 0), (33, 0), (33, 19), (17, 19))]
        warp, metrics = align_view(reference, current)
        validate_floor_alignment(reference, current, warp, corners)
        displacement = max(hypot(*(np.asarray(project_xy(warp, p))-p)) for p in corners)
        return {'verified': displacement <= 2 and metrics['holdout_median_px'] <= 2,
                'landmark_displacement_px': displacement, **metrics}
    except ValueError:
        return {'verified': False, 'reason': 'floor_or_fixed_view_unverified'}


def observe_free_throw_line(frame, matrix, *, blocked_box=None):
    """Fit actual white pixels in a narrow band around the calibrated line.

    The predicted line is a search guide, never returned as observed evidence.
    Both length coverage and residual are checked; occluded regions are omitted.
    """
    endpoints = free_throw_pixels(matrix)
    delta = endpoints[1]-endpoints[0]
    length = np.linalg.norm(delta)
    if not 60 <= length <= max(frame.shape[:2])*3:
        return {'status': 'unavailable', 'reason': 'line_projection_invalid'}
    normal = np.array([-delta[1], delta[0]])/length
    positions = np.linspace(.03, .97, max(70, int(length)))
    distances = np.arange(-12, 13, dtype=float)
    points = endpoints[0]+positions[:, None]*delta
    samples = points[:, None, :]+distances[None, :, None]*normal
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    pixels = cv2.remap(hsv, samples[:, :, 0].astype('float32'), samples[:, :, 1].astype('float32'), cv2.INTER_NEAREST)
    white = (pixels[:, :, 1] < 65) & (pixels[:, :, 2] > 135)
    inside = ((samples[:, :, 0] >= 1) & (samples[:, :, 0] < frame.shape[1]-1)
              & (samples[:, :, 1] >= 1) & (samples[:, :, 1] < frame.shape[0]-1))
    white &= inside
    if blocked_box:
        x1, y1, x2, y2 = blocked_box
        white &= ~((samples[:, :, 0] >= x1-12) & (samples[:, :, 0] <= x2+12)
                   & (samples[:, :, 1] >= y1-12) & (samples[:, :, 1] <= y2+12))
    selected = []
    for i, row in enumerate(white):
        runs = np.split(np.flatnonzero(row), np.flatnonzero(np.diff(np.flatnonzero(row)) > 1)+1)
        runs = [r for r in runs if 2 <= len(r) <= 14]
        if runs:
            best = min(runs, key=lambda r: abs(float(np.mean(distances[r]))))
            selected.append((float(positions[i]), float(np.mean(distances[best]))))
    if len(selected) < 60:
        return {'status': 'unavailable', 'reason': 'white_line_not_visible'}
    values = np.asarray(selected)
    slope, offset = np.polyfit(values[:, 0], values[:, 1], 1)
    for _ in range(3):
        residuals = abs(values[:, 1]-(slope*values[:, 0]+offset))
        keep = residuals <= max(2., float(np.median(residuals))*2.5)
        if sum(keep) < 60:
            return {'status': 'unavailable', 'reason': 'line_inliers_insufficient'}
        values = values[keep]
        slope, offset = np.polyfit(values[:, 0], values[:, 1], 1)
    coverage = float(np.ptp(values[:, 0]))
    support = len(values)/len(positions)
    residual = float(np.quantile(abs(values[:, 1]-(slope*values[:, 0]+offset)), .9))
    if coverage < .55 or support < .35 or residual > 2.5:
        return {'status': 'unavailable', 'reason': 'line_coverage_or_fit_unreliable'}
    observed = np.asarray([endpoints[i]+normal*(offset+slope*i) for i in (0, 1)])
    line_y = [project_xy(matrix, p)[1] for p in observed]
    if max(abs(y-19) for y in line_y) > .5:
        return {'status': 'unavailable', 'reason': 'calibration_line_disagreement', 'observed_y_ft': line_y}
    return {'status': 'observed', 'points_px': observed.tolist(), 'predicted_points_px': endpoints.tolist(),
            'coverage': coverage, 'support': support, 'residual_px': residual,
            'pixel_error_px': max(2., residual+1.), 'observed_y_ft': line_y,
            'source': 'white_pixels_near_calibrated_free_throw_line'}


def prepare_contact_sequence(video: Path, candidate: dict):
    """Decode every native frame in a fixed window anchored to the plant time."""
    reference = plant_sample(candidate)
    matrix = candidate.get('validated_court_transform')
    if reference is None or candidate.get('calibration_view_valid') is not True or not matrix:
        return [], {'status': 'unavailable', 'reason': 'stable_plant_or_validated_view_missing'}, {}
    anchor = reference['time_ms']
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return [], {'status': 'unavailable', 'reason': 'video_unavailable'}, {}
    try:
        fps, total = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if not 10 <= fps <= 240:
            return [], {'status': 'unavailable', 'reason': 'unsupported_fps'}, {}
        lo = max(0, ceil((anchor-CONTACT_BEFORE_MS)*fps/1000))
        hi = min(total-1, floor((anchor+CONTACT_AFTER_MS)*fps/1000))
        # Bound the same source scene; no frames from an adjoining cut are used.
        start = candidate.get('scene_start_ms', 0)
        end = candidate.get('scene_end_ms', total/fps*1000)
        lo, hi = max(lo, ceil(start*fps/1000)), min(hi, floor(end*fps/1000))
        if hi-lo < 4:
            return [], {'status': 'unavailable', 'reason': 'sequence_too_short'}, {}
        cap.set(cv2.CAP_PROP_POS_FRAMES, lo)
        frames = {}
        for index in range(lo, hi+1):
            ok, frame = cap.read()
            if not ok:
                break
            frames[index] = frame
        validation_index = min(total-1, max(0, round(candidate['time_ms']*fps/1000)))
        cap.set(cv2.CAP_PROP_POS_FRAMES, validation_index)
        validation_ok, validation_frame = cap.read()
    finally:
        cap.release()
    if not frames:
        return [], {'status': 'unavailable', 'reason': 'frames_missing'}, {}
    if not validation_ok:
        return [], {'status': 'unavailable', 'reason': 'calibration_validation_frame_missing'}, frames
    # Native pixels: never resize shoes. The crop locator is not a measured foot.
    px, py = reference['foot_point_px']
    frame = next(iter(frames.values()))
    fh, fw = frame.shape[:2]
    x1, x2 = max(0, int(px-fw*.16)), min(fw, int(px+fw*.16))
    y1, y2 = max(0, int(py-fh*.20)), min(fh, int(py+fh*.10))
    if x2-x1 < 100 or y2-y1 < 100 or x1 == 0 or x2 == fw or y2 == fh:
        return [], {'status': 'unavailable', 'reason': 'sole_crop_clipped'}, frames
    stride = max(1, round(fps/30))
    indices = sorted(set(list(frames)[::stride]+[min(frames, key=lambda i: abs(i/fps*1000-anchor)), max(frames)]))
    frame_views = {str(index): verify_fixed_floor(validation_frame, frames[index], matrix) for index in indices}
    line_checks = []
    for index in indices:
        # Feet/ankles cannot supply white-line support underneath the player.
        sample = min(candidate.get('foot_track', [reference]), key=lambda s: abs(s['time_ms']-index/fps*1000))
        check = observe_free_throw_line(frames[index], matrix, blocked_box=sample.get('person_box_px'))
        if not frame_views[str(index)]['verified']:
            check = {'status': 'unavailable', 'reason': 'frame_floor_or_view_unverified'}
        line_checks.append({'frame_index': index, **check})
    observed = [l for l in line_checks if l['status'] == 'observed']
    if len(observed) >= 3:
        median_line = np.median([l['points_px'] for l in observed], axis=0)
        for line in observed:
            if np.max(np.linalg.norm(np.asarray(line['points_px'])-median_line, axis=1)) > 4:
                line.update(status='unavailable', reason='temporal_line_disagreement')
    # Borrow an occluded line only after independently checking that frame's
    # floor and camera against the proposed reference. Do not trust time alone.
    observed = [l for l in line_checks if l['status'] == 'observed']
    borrowed = {}
    if observed:
        for check in line_checks:
            if check['status'] == 'observed':
                continue
            target = check['frame_index']
            if not frame_views[str(target)]['verified']:
                continue
            reference_line = min(observed, key=lambda l: abs(l['frame_index']-target))
            reference_index = reference_line['frame_index']
            if abs(reference_index-target)/fps*1000 > 250:
                continue
            borrowed[str(target)] = {'reference_frame_index': reference_index, **frame_views[str(target)]}
    inputs = []
    overview = frames[min(frames, key=lambda i: abs(i/fps*1000-anchor))]
    from app.services.openai_shot_review import _encode_frame
    inputs.extend([{'type': 'input_text', 'text': 'Unmarked full court overview; use it to identify the straight free-throw line, not the circular arc.'},
                   {'type': 'input_image', 'image_url': _encode_frame(overview), 'detail': 'high'}])
    for index in indices:
        crop = frames[index][y1:y2, x1:x2]
        ok, data = cv2.imencode('.png', crop)
        if not ok:
            continue
        inputs.extend([{'type': 'input_text', 'text': f'frame_index={index}; absolute_time_ms={index/fps*1000:.3f}; native crop {x2-x1}x{y2-y1}. Report crop-local x,y (origin at top left).'},
                       {'type': 'input_image', 'image_url': 'data:image/png;base64,'+base64.b64encode(data).decode(), 'detail': 'high'}])
    metadata = {'status': 'prepared', 'source_fps': fps, 'source_frame_indices': list(frames),
        'sent_frame_indices': indices, 'anchor_time_ms': anchor, 'anchor_source': 'pose_stable_time_only',
        'roi_px': [x1, y1, x2, y2], 'decoded_window_ms': [lo/fps*1000, hi/fps*1000],
        'line_observations': line_checks, 'verified_line_borrows': borrowed, 'native_crop_resized': False}
    metadata.update(verified_contact_frames=frame_views, calibration_validation_frame_index=validation_index)
    return inputs, metadata, frames


def locate_soles_once(client, model, inputs):
    response = client.responses.parse(model=model, reasoning={'effort': 'low'}, store=False,
        input=[{'role': 'user', 'content': [{'type': 'input_text', 'text': (
            'Measure actual shoe sole-ground contact in this chronological basketball jump-shot sequence. '
            'The person box and ankles are NOT contact points. Identify the final planted stance immediately before takeoff. '
            'For each supplied frame return its exact frame_index and state. Return contacts=[] for airborne/unclear frames. '
            'For both_grounded return exactly two distinct visible shoes with stable foot_1/foot_2 IDs. '
            'For each shoe place x,y at the CENTER of its visible sole touching the ground, not the ankle, top of shoe, shadow, toe in air, or person-box bottom. '
            'Coordinates must be in that crop, not full-image pixels. Give an honest pixel error radius and confidence for the contact point. '
            'If two shoes cannot be individually localized, do NOT claim both_grounded. '
            'Identify takeoff_frame_index as the first supplied frame of the final jump where the soles are off the ground, or null if not visible. '
            'Distinguish approach running steps from the final stationary two-foot plant. Do not return a court zone or estimated court XY. '
            'The code independently observes the straight free-throw line and projects your measured pixels. Do not infer a contact from an expected court location.'
        )}, *inputs]}], text_format=ContactSequence)
    parsed = getattr(response, 'output_parsed', None)
    if parsed is None:
        raise ValueError('sole_contact_response_missing')
    from app.services.openai_shot_review import _usage_dict
    return parsed, _usage_dict(response)


def evaluate_contact_sequence(sequence: ContactSequence, metadata: dict, candidate: dict, *, extra_pixel_error=2.):
    """Project measured pixels and propagate stated pixel + observed-line errors.

    Pixel error is an explicit sensitivity assumption, not calibrated statistical
    confidence or a claim about the user's manual calibration accuracy.
    """
    report = {'status': 'needs_review', 'reason': 'insufficient_measured_contact', 'measured_frames': [], 'diagnostic_frames': [],
              'rejected_frames': [],
              'extra_pixel_error_px': extra_pixel_error, 'confidence_threshold': CONTACT_CONFIDENCE}
    if metadata.get('status') != 'prepared' or candidate.get('calibration_view_valid') is not True:
        return report
    transform = candidate.get('validated_court_transform')
    if not transform:
        return report
    allowed = set(metadata['sent_frame_indices'])
    if len({f.frame_index for f in sequence.frames}) != len(sequence.frames):
        report['reason'] = 'duplicate_frame_measurement'
        return report
    if sequence.takeoff_frame_index not in allowed:
        report['reason'] = 'takeoff_not_bracketed'
        return report
    takeoff = next((f for f in sequence.frames if f.frame_index == sequence.takeoff_frame_index), None)
    if takeoff is None or takeoff.state != 'airborne' or takeoff.contacts:
        report['reason'] = 'takeoff_not_observed_airborne'
        return report
    if not metadata.get('verified_contact_frames', {}).get(str(sequence.takeoff_frame_index), {}).get('verified'):
        report['reason'] = 'takeoff_view_unverified'
        return report
    lines = {l['frame_index']: l for l in metadata.get('line_observations', []) if l['status'] == 'observed'}
    if len(lines) < 2:
        report['reason'] = 'observed_free_throw_line_missing'
        return report
    roi = metadata['roi_px']
    samples = []
    for f in sequence.frames:
        if f.frame_index not in allowed or f.frame_index >= sequence.takeoff_frame_index or f.state != 'both_grounded':
            continue
        view = metadata.get('verified_contact_frames', {}).get(str(f.frame_index), {})
        if not view.get('verified'):
            report['rejected_frames'].append({'frame_index': f.frame_index, 'reason': 'frame_floor_or_view_unverified'})
            continue
        if len(f.contacts) != 2 or {p.foot_id for p in f.contacts} != {'foot_1', 'foot_2'}:
            continue
        high_confidence = min(p.confidence for p in f.contacts) >= CONTACT_CONFIDENCE
        if hypot(f.contacts[0].x-f.contacts[1].x, f.contacts[0].y-f.contacts[1].y) < 3:
            continue
        line = lines.get(f.frame_index)
        # Same fixed view: adjacent frames may provide an occluded straight line.
        if line is None:
            reference = metadata.get('verified_line_borrows', {}).get(str(f.frame_index), {})
            nearest = reference.get('reference_frame_index')
            if nearest not in lines:
                report['rejected_frames'].append({'frame_index': f.frame_index, 'reason': 'no_verified_line_reference'})
                continue
            line = lines[nearest]
        points, bounds = [], []
        for p in f.contacts:
            if not (p.error_px <= p.x <= roi[2]-roi[0]-p.error_px and p.error_px <= p.y <= roi[3]-roi[1]-p.error_px):
                break
            pixel = [p.x+roi[0], p.y+roi[1]]
            error = p.error_px+extra_pixel_error+line['pixel_error_px']+view.get('landmark_displacement_px', 0)
            try:
                xy = project_xy(transform, pixel)
                corners = [project_xy(transform, (pixel[0]+dx*error, pixel[1]+dy*error)) for dx in (-1, 1) for dy in (-1, 1)]
            except ValueError:
                break
            if not all(0 <= q[0] <= 50 and 0 <= q[1] <= 47 for q in [xy, *corners]):
                break
            points.append({'foot_id': p.foot_id, 'pixel': pixel, 'xy_ft': xy, 'error_px': error,
                           'confidence': p.confidence, 'xy_bounds_ft': [[min(q[j] for q in corners), max(q[j] for q in corners)] for j in (0, 1)]})
            bounds.extend(q[1] for q in corners)
        if len(points) != 2:
            continue
        # Both the numeric free-throw boundary and observed line must agree.
        lower_line = min(19., *line['observed_y_ft'])
        upper_line = max(19., *line['observed_y_ft'])
        side = 'outside' if min(bounds) > upper_line else 'inside' if max(bounds) < lower_line else 'straddling'
        center = np.mean([p['xy_ft'] for p in points], axis=0).tolist()
        measurement = {'frame_index': f.frame_index, 'time_ms': f.frame_index/metadata['source_fps']*1000,
                        'contacts': points, 'center_xy_ft': center, 'y_interval_ft': [min(bounds), max(bounds)],
                        'line_y_interval_ft': [lower_line, upper_line], 'side': side,
                        'line_reference_frame_index': line['frame_index'], 'accepted_contact_confidence': high_confidence}
        report['diagnostic_frames'].append(measurement)
        if high_confidence:
            samples.append(measurement)
        else:
            report['rejected_frames'].append({'frame_index': f.frame_index, 'reason': 'sole_contact_confidence_low',
                                             'minimum_confidence': min(p.confidence for p in f.contacts)})
    # Only the final stance, not a running contact earlier in the window.
    samples.sort(key=lambda s: s['frame_index'])
    final = []
    for sample in reversed(samples):
        if (sequence.takeoff_frame_index-sample['frame_index'])/metadata['source_fps']*1000 > 250:
            continue
        if final and (final[-1]['frame_index']-sample['frame_index'])/metadata['source_fps']*1000 > 70:
            break
        final.append(sample)
    final.reverse()
    report['measured_frames'] = final
    if (len(final) < MIN_CONTACT_FRAMES or final[-1]['time_ms']-final[0]['time_ms'] < MIN_CONTACT_SPAN_MS
            or (sequence.takeoff_frame_index-final[-1]['frame_index'])/metadata['source_fps']*1000 > 100):
        if any(r['reason'] == 'sole_contact_confidence_low' for r in report['rejected_frames']):
            report['reason'] = 'sole_contact_occluded_or_low_confidence'
        return report
    centers = np.asarray([s['center_xy_ft'] for s in final])
    if np.max(np.linalg.norm(centers-np.median(centers, axis=0), axis=1)) > .5:
        report['reason'] = 'contact_points_unstable'
        return report
    report['xy_estimate_ft'] = np.median(centers, axis=0).tolist()
    report['observed_sides'] = [s['side'] for s in final]
    if len(set(report['observed_sides'])) != 1 or report['observed_sides'][0] == 'straddling':
        report['reason'] = 'free_throw_boundary_within_error'
        return report
    xy = report['xy_estimate_ft']
    # The independently observed straight segment is meaningful within the lane.
    if not all(17 < p['xy_bounds_ft'][0][0] <= p['xy_bounds_ft'][0][1] < 33 for s in final for p in s['contacts']):
        report['reason'] = 'lane_side_boundary_within_error'
        return report
    zone = classify_zone(*xy).value
    side = report['observed_sides'][0]
    if not ((side == 'outside' and zone.startswith('mid_')) or (side == 'inside' and zone.startswith('paint_'))):
        report['reason'] = 'other_physical_boundary_requires_review'
        return report
    report.update(status='resolved', reason='grounded_pixels_and_observed_line_agree',
                  court_x=xy[0], court_y=xy[1], court_zone=zone,
                  confidence=min(p['confidence'] for s in final for p in s['contacts']))
    return report


def needs_contact_verification(decision: dict, candidate: dict, projected_plant=None) -> bool:
    """This first measured-contact pass is for two-foot free-throw-area jumpers.

    Other physical boundaries keep their existing independent checks. In
    particular, this pass cannot override a visually identified three-pointer.
    """
    if (not decision.get('is_shot') or decision.get('shot_type') in ('layup', 'dunk')
            or decision.get('visual_arc_relation') == 'outside'
            or candidate.get('calibration_view_valid') is not True
            or not candidate.get('validated_court_transform') or plant_sample(candidate) is None):
        return False
    points = [projected_plant, (decision.get('court_x'), decision.get('court_y'))]
    return any(p is not None and len(p) == 2 and all(isinstance(v, (float, int)) and np.isfinite(v) for v in p)
               and 17 < p[0] < 33 and abs(p[1]-19) <= 2 for p in points)


def review_contact_pixels(client, model, video: Path, candidate: dict):
    inputs, metadata, native_frames = prepare_contact_sequence(video, candidate)
    del native_frames
    if not inputs or sum(l['status'] == 'observed' for l in metadata.get('line_observations', [])) < 2:
        return {'status': 'needs_review', 'reason': metadata.get('reason', 'observed_free_throw_line_missing'),
                'preparation': metadata, 'api_called': False}, {}
    sequence, usage = locate_soles_once(client, model, inputs)
    result = evaluate_contact_sequence(sequence, metadata, candidate)
    # A second, wider sensitivity assumption cannot turn an ambiguous result
    # into confirmation; both checks must agree before returning a saved point.
    sensitivity = evaluate_contact_sequence(sequence, metadata, candidate, extra_pixel_error=4.)
    if result['status'] == 'resolved' and (sensitivity['status'] != 'resolved'
            or sensitivity.get('court_zone') != result.get('court_zone')):
        result.update(status='needs_review', reason='pixel_error_sensitivity_crosses_boundary')
    result.update(preparation=metadata, model_measurement=sequence.model_dump(), api_called=True,
                  wider_error_check={**{k: v for k, v in sensitivity.items() if k not in ('measured_frames', 'diagnostic_frames')},
                                     'diagnostic_sides': [f['side'] for f in sensitivity.get('diagnostic_frames', [])]})
    return result, usage


def _keep_supported_visual_position(review, result, candidate, shoe_evidence) -> bool:
    from app.services.phase8_shots import _court_position

    if (result.get('reason') not in ('observed_free_throw_line_missing', 'sole_contact_occluded_or_low_confidence',
                                     'contact_verification_failed')
            or candidate.get('calibration_view_valid') is not True or not candidate.get('validated_court_transform')
            or review.get('uncertainty_scope') in ('physical_boundary', 'view')
            or not review.get('is_shot') or shoe_evidence.get('status') != 'available'):
        return False
    x, y, zone = _court_position(review, [])
    if zone is None or physical_clearance(x, y) <= 30 / 30.48:
        return False
    expected_arc = 'outside' if zone.startswith('three_') else 'inside'
    if review.get('visual_arc_relation') != expected_arc or not review.get('arc_evidence', '').strip():
        return False
    available = set(shoe_evidence.get('offsets_ms', [])) - set(shoe_evidence.get('image_edge_offsets_ms', []))
    grounded = {t for t in review.get('grounded_frame_offsets_ms', []) if t in available and -1200 <= t < 0}
    if len(grounded) < 2:
        return False
    diagnostics = result.get('diagnostic_frames', [])
    expected_side = 'outside' if zone.startswith('mid_') else 'inside'
    if result.get('reason') == 'sole_contact_occluded_or_low_confidence':
        wider_sides = result.get('wider_error_check', {}).get('diagnostic_sides', [])
        if not diagnostics or not wider_sides or any(side != expected_side for side in wider_sides):
            return False
    return all(frame.get('side') == expected_side for frame in diagnostics)


def apply_contact_evidence(review: dict, result: dict, candidate=None, shoe_evidence=None):
    """Change only location based on new measurements; keep shot/type/outcome."""
    if result.get('status') != 'resolved':
        if _keep_supported_visual_position(review, result, candidate or {}, shoe_evidence or {}):
            result['application'] = 'preserved_visual_position'
            return
        result['application'] = 'held_for_review'
        review.update(court_x=None, court_y=None, court_zone='needs_review', position_confidence=0,
                      coordinate_confidence=None, review_required=True,
                      position_reason='접지점과 자유투선 관계를 확정하지 못해 위치 확인 필요')
        review['confidence'] = 0
        return
    x, y = result['court_x'], result['court_y']
    if result['confidence'] < CONTACT_CONFIDENCE or classify_zone(x, y).value != result['court_zone']:
        raise ValueError('invalid_measured_contact_result')
    result['application'] = 'measured_position'
    review.update(court_x=x, court_y=y, court_zone=result['court_zone'],
                  position_confidence=result['confidence'], coordinate_confidence=result['confidence'],
                  zone_confidence=result['confidence'], uncertainty_scope='none',
                  position_reason='여러 접지 프레임과 실제 자유투선의 오차 범위가 한쪽으로 일치')
    from app.services.openai_shot_review import ESCALATION_CONFIDENCE
    review['review_required'] = (review.get('outcome') == 'unclear' or review.get('shot_type') == 'needs_review'
                                or review.get('outcome_confidence', 0) < ESCALATION_CONFIDENCE
                                or review.get('shot_type_confidence', 0) < ESCALATION_CONFIDENCE)
    review['confidence'] = min(review.get('outcome_confidence', 0), review.get('shot_type_confidence', 0), result['confidence'])
