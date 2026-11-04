"""Keep supported XY and normalize only narrow, virtual adjacent-zone labels."""
from math import hypot, isfinite

from app.services.court_zones import classify_zone
from app.services.ground_contact import plant_sample
from app.services.shot_review_policy import POSITION_AUTO_CONFIRM_CONFIDENCE
from app.services.zone_boundaries import physical_clearance, zone_boundaries, zone_family

DEFAULT_ZONE_BOUNDARY_CM = 15.0


def coordinate_confidence(decision) -> float:
    value = getattr(decision, 'position_confidence', getattr(decision, 'confidence', 0))
    explicit = getattr(decision, 'coordinate_confidence', None)
    return min(value, explicit) if explicit is not None else value


def normalize_zone_boundary(decision, candidate: dict, projected_plant, shoe_evidence: dict,
                            *, tolerance_cm=DEFAULT_ZONE_BOUNDARY_CM) -> dict:
    """All 14 zones receive diagnostics; physical borders never get an exception.

    This only changes the label. It cannot move XY, raise confidence, resolve a
    non-shot, or turn absent/unclear grounded-shoe evidence into a confirmed point.
    """
    if tolerance_cm not in (15, 30):
        raise ValueError('Only the evaluated 15cm/30cm windows are supported')
    x, y = decision.court_x, decision.court_y
    audit = {'original_zone': decision.court_zone, 'coordinate_zone': None,
             'original_xy': [x, y], 'coordinate_confidence': coordinate_confidence(decision),
             'zone_confidence': getattr(decision, 'zone_confidence', None),
             'tolerance_cm': tolerance_cm, 'near_boundary': False, 'normalized': False,
             'coordinates_moved': False, 'reason': 'not_applicable'}
    if x is None or y is None or not all(isfinite(v) for v in (x, y)):
        audit['reason'] = 'missing_coordinates'
        return audit
    try:
        calculated = classify_zone(x, y).value
    except ValueError:
        audit['reason'] = 'coordinates_out_of_range'
        return audit
    audit['coordinate_zone'] = calculated
    mismatch = decision.court_zone != calculated
    pair = frozenset((decision.court_zone, calculated))
    choices = [b for b in zone_boundaries() if (frozenset(b.zones) == pair if mismatch else calculated in b.zones)]
    if not choices:
        audit['reason'] = 'nonadjacent_or_unknown_zone'
        return audit
    boundary = min(choices, key=lambda b: b.distance(x, y))
    distance_cm = boundary.distance(x, y)*30.48
    radius = tolerance_cm/30.48
    audit.update(boundary_zones=list(boundary.zones), boundary_kind=boundary.kind,
                 boundary_class='physical' if boundary.physical else 'virtual_internal',
                 distance_cm=round(distance_cm, 5), near_boundary=distance_cm <= tolerance_cm+1e-8,
                 physical_clearance_cm=round(physical_clearance(x, y)*30.48, 5))
    if boundary.kind == 'line' and boundary.start[0] == boundary.end[0]:
        audit['boundary_x_ft'] = boundary.start[0]
    if boundary.physical:
        audit['reason'] = 'physical_boundary_no_exception'
        return audit
    if not audit['near_boundary']:
        audit['reason'] = 'outside_tolerance'
        return audit
    if physical_clearance(x, y) <= radius:
        audit['reason'] = 'physical_boundary_nearby'
        return audit
    if coordinate_confidence(decision) < POSITION_AUTO_CONFIRM_CONFIDENCE:
        audit['reason'] = 'coordinate_confidence_low'
        return audit
    if getattr(decision, 'uncertainty_scope', None) in ('physical_boundary', 'coordinate', 'view'):
        audit['reason'] = 'explicit_physical_or_coordinate_uncertainty'
        return audit
    if getattr(decision, 'is_shot', True) is not True:
        audit['reason'] = 'shot_unconfirmed'
        return audit
    if candidate.get('calibration_view_valid') is not True or not candidate.get('validated_court_transform'):
        audit['reason'] = 'calibration_unverified'
        return audit
    expected_arc = 'outside' if zone_family(calculated) == 'three' else 'inside'
    if decision.visual_arc_relation != expected_arc:
        audit['reason'] = 'visual_distance_uncertain'
        return audit
    sample = plant_sample(candidate)
    if sample is None or projected_plant is None or len(projected_plant) != 2 or not all(isfinite(v) for v in projected_plant):
        audit['reason'] = 'plant_evidence_missing'
        return audit
    try:
        plant_zone = classify_zone(*projected_plant).value
    except ValueError:
        audit['reason'] = 'plant_projection_disagrees'
        return audit
    if (zone_family(plant_zone) != zone_family(calculated)
            or physical_clearance(*projected_plant) <= radius
            or hypot(x-projected_plant[0], y-projected_plant[1]) > radius):
        audit['reason'] = 'plant_projection_disagrees'
        return audit
    available = set(shoe_evidence.get('offsets_ms', []))-set(shoe_evidence.get('image_edge_offsets_ms', []))
    grounded = sorted({t for t in decision.grounded_frame_offsets_ms if t in available and -1200 <= t < 0})
    if shoe_evidence.get('status') != 'available' or len(grounded) < 2 or not decision.arc_evidence.strip():
        audit['reason'] = 'grounded_images_insufficient'
        return audit
    audit.update(grounded_offsets_ms=grounded, plant_sample_time_ms=sample['time_ms'],
                 reason='adjacent_virtual_label_only' if mismatch else 'already_consistent')
    if mismatch:
        decision.court_zone = calculated
        audit['normalized'] = True
    return audit


def position_conflict_prompt(decision) -> str:
    x, y = decision.court_x, decision.court_y
    if x is None or y is None:
        return 'Recover coordinates only from visible grounded shoes; missing numbers are not evidence of a different distance band. '
    try:
        calculated = classify_zone(x, y).value
    except ValueError:
        return 'Coordinates are outside the court. Recheck grounded shoes and camera calibration; do not clamp or snap the point. '
    pair = frozenset((decision.court_zone, calculated))
    borders = [b for b in zone_boundaries() if frozenset(b.zones) == pair]
    if borders and not any(b.physical for b in borders):
        boundary = min(borders, key=lambda b: b.distance(x, y))
        location = (f'x={boundary.start[0]:g}ft' if boundary.kind == 'line' and boundary.start[0] == boundary.end[0]
                    else f'y={boundary.start[1]:g}ft')
        family = zone_family(calculated)
        return (f'The reported mismatch is ONLY the virtual {family} sector boundary {location}: '
                f'label={decision.court_zone}, coordinate-derived sector={calculated}. '
                'First resolve these two adjacent sector names independently from numeric XY confidence. '
                'Do not reopen paint, restricted-area or two/three-point distance solely because sector names differ. '
                'If new visible evidence genuinely makes physical distance or foot contact unclear, identify that separately '
                'with uncertainty_scope and lower coordinate_confidence. ')
    return (f'The named zone {decision.court_zone} conflicts with coordinates ({x},{y}), which map to {calculated}. '
            'Check physical paint/restricted/three-point boundaries strictly; do not apply a virtual-sector tolerance. ')
