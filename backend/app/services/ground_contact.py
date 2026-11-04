"""Conservative pre-release ground-motion references; never claim measured contact."""
from math import dist, isfinite
from app.analysis.candidate_windows import rescan_window, WINDOW_RULES


def plant_sample(candidate: dict) -> dict | None:
    release = int(candidate['time_ms'])
    rule = WINDOW_RULES['ground_contact']
    samples = sorted((s for s in candidate.get('foot_track', [])
                      if release - rule.before_ms <= s.get('time_ms', -1) <= release + rule.after_ms
                      and not s.get('foot_point_clipped')
                      and isinstance(s.get('foot_point_px'), (list, tuple))
                      and len(s['foot_point_px']) == 2
                      and all(isfinite(float(v)) for v in s['foot_point_px'])),
                     key=lambda s: s['time_ms'])
    pairs = []
    for earlier, later in zip(samples, samples[1:]):
        gap = later['time_ms'] - earlier['time_ms']
        if not 60 <= gap <= 300:
            continue
        a, b = earlier.get('ankle_points_px'), later.get('ankle_points_px')
        # Older stored tracks do not contain pose landmarks. Do not turn their
        # box midpoint into a newly "confirmed" plant when replaying old data.
        if not a or not b:
            continue
        floor_a, floor_b = max(a, key=lambda p: p[1]), max(b, key=lambda p: p[1])
        box = later.get('person_box_px')
        if not box:
            continue
        height = max(1, box[3] - box[1])
        # During takeoff both shoes move upwards quickly. Require a stable pair
        # before that rise; reject lateral running/motion too large for a plant.
        if abs(floor_b[1] - floor_a[1]) > max(3, height * .018):
            continue
        if dist(floor_a, floor_b) > max(6, height * .06):
            continue
        pairs.append(later)
    if not pairs:
        return None
    # Prefer the lowest visible stable shoe pair, not a fixed release-400ms.
    # This resists a stable-looking frame near the apex of a jump.
    return max(pairs, key=lambda s: (max(p[1] for p in s['ankle_points_px']), s['time_ms']))


def refresh_retimed_foot_tracks(video, model, pose, hoop, result, *, cancelled=None,
                               inference_device='cpu', prefetch_frames=False, model_reuse=None,
                               activity_progress=None):
    from app.analysis.phase7_pipeline import analyze_video, Phase7Canceled
    reports = []
    targets = []
    for candidate in result.get('candidates', []):
        gate = candidate.get('shot_gate') or {}
        if gate.get('verdict') != 'shot':
            continue
        original = gate.get('detector_candidate_time_ms', gate.get('original_time_ms', candidate['time_ms']))
        if abs(int(original) - candidate['time_ms']) < 200:
            continue
        targets.append(candidate)
    for candidate_index, candidate in enumerate(targets):
        if activity_progress:
            activity_progress('발 추적 보정', candidate_index, len(targets))
        if cancelled and cancelled():
            raise Phase7Canceled('사용자가 분석을 취소했습니다.')
        start, end = rescan_window(candidate['time_ms'], 0, result['duration_ms'], purpose='plant_refresh')
        target = {**candidate}
        target.pop('foot_track', None)
        try:
            scan = analyze_video(video, model, hoop, pose_model_path=pose, sample_fps=15,
                                 start_ms=start, end_ms=end, cancelled=cancelled,
                                 inference_device=inference_device, prefetch_frames=prefetch_frames,
                                 model_reuse=model_reuse, foot_track_targets=[target])
            candidate['foot_track'] = target.get('foot_track', [])
            candidate['frame_size_px'] = target.get('frame_size_px', candidate.get('frame_size_px'))
            report = {'status': 'complete', 'release_ms': candidate['time_ms'],
                      'window_ms': [start, end], 'samples': len(candidate['foot_track']),
                      'stable_plant_found': plant_sample(candidate) is not None,
                      'seconds': scan.get('performance', {}).get('total_seconds')}
        except Phase7Canceled:
            raise
        except Exception:
            # Old track is not relabelled as fresh on failure.
            report = {'status': 'failed', 'release_ms': candidate['time_ms'], 'window_ms': [start, end]}
        candidate['foot_track_refresh'] = report
        reports.append(report)
        if activity_progress:
            activity_progress('발 추적 보정', candidate_index + 1, len(targets))
    result['foot_track_refresh_summary'] = {'refreshed': sum(r['status'] == 'complete' for r in reports), 'items': reports}
    return result
