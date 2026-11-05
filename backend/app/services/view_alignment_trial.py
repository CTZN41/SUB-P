"""Track a user's existing calibration across a validated zoom/reframe.

Does not find new court corners or save calibration. Refuses weak matches.
"""
import cv2
import numpy as np


class FloorAlignmentError(ValueError):
    def __init__(self, message, evidence):
        super().__init__(message)
        self.evidence = evidence


def floor_landmark_quality(frame, points):
    """Report whether each manual point has a checkable native-pixel patch."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    height, width = gray.shape
    quality = []
    for index, point in enumerate(points[:4]):
        x, y = map(int, point)
        margin = min(x, y, width-1-x, height-1-y)
        texture = None
        reason = 'image_edge'
        if margin >= 28:
            texture = float(gray[y-22:y+23, x-22:x+23].std())
            reason = 'usable' if texture >= 12 else 'low_texture'
        quality.append({'point_index': index+1, 'usable': reason == 'usable',
                        'reason': reason, 'margin_px': margin,
                        'texture_stddev': round(texture, 4) if texture is not None else None})
    return quality


def align_view(reference, current):
    orb = cv2.ORB_create(nfeatures=5000)
    a, da = orb.detectAndCompute(cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY), None)
    b, db = orb.detectAndCompute(cv2.cvtColor(current, cv2.COLOR_BGR2GRAY), None)
    if da is None or db is None:
        raise ValueError("insufficient scene features")
    matches = [m for m,n in cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da,db,k=2) if m.distance < .72*n.distance]
    if len(matches) < 25:
        raise ValueError("insufficient scene matches")
    src = np.float32([a[m.queryIdx].pt for m in matches])
    dst = np.float32([b[m.trainIdx].pt for m in matches])
    # Hold out 20% from fitting: report prediction error on unfit correspondences.
    fit = np.arange(len(matches)) % 5 != 0
    matrix, mask = cv2.findHomography(src[fit],dst[fit],cv2.RANSAC,3)
    if matrix is None or int(mask.sum()) < 25 or float(mask.mean()) < .45:
        raise ValueError("unstable scene transform")
    inliers = src[fit][mask.ravel().astype(bool)]
    if np.ptp(inliers[:,0]) < reference.shape[1]*.25 or np.ptp(inliers[:,1]) < reference.shape[0]*.2:
        raise ValueError("scene matches cover too little area")
    projected = cv2.perspectiveTransform(src[~fit].reshape(1,-1,2),matrix)[0]
    errors = np.linalg.norm(projected-dst[~fit],axis=1)
    median = float(np.median(errors))
    if median > 3:
        raise ValueError("scene holdout mismatch")
    center = np.float32([[[reference.shape[1]/2,reference.shape[0]/2],
                           [reference.shape[1]/2+100,reference.shape[0]/2]]])
    segment = cv2.perspectiveTransform(center,matrix)[0]
    scale = float(np.linalg.norm(segment[1]-segment[0])/100)
    if not .65 <= scale <= 1.6 or np.linalg.det(matrix[:2,:2]) <= 0:
        raise ValueError("unsupported camera change")
    return matrix, {"matches":len(matches),"inliers":int(mask.sum()),
                    "holdout_median_px":round(median,4),"center_scale":round(scale,4),
                    "changed":abs(scale-1)>.035}


def transform_calibration(court_transform, view_transform):
    result = np.array(court_transform) @ np.linalg.inv(view_transform)
    return (result/result[2,2]).tolist()


def guarded_calibration(reference, current, court_transform):
    try:
        matrix, metrics = align_view(reference,current)
        return transform_calibration(court_transform,matrix), {"status":"aligned",**metrics}
    except ValueError as error:
        # No numeric foot projection is safer than applying another camera's map.
        return None, {"status":"different_or_unverified_view","reason":str(error)}


def floor_patch_scores(reference, current, matrix, points):
    """Keep landmark indices, including unusable patches, for temporal checks."""
    warped = cv2.warpPerspective(current, np.linalg.inv(matrix), (reference.shape[1], reference.shape[0]))
    a = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    b = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    scores = []
    for (x, y), quality in zip(points[:4], floor_landmark_quality(a, points)):
        x, y = int(x), int(y)
        if not quality['usable']:
            scores.append(None)
            continue
        patch = a[y-22:y+23, x-22:x+23]
        score = cv2.matchTemplate(b[y-25:y+26,x-25:x+26], patch, cv2.TM_CCOEFF_NORMED).max()
        scores.append(float(score))
    return scores


def validate_floor_alignment(reference, current, matrix, points):
    """Check manual floor landmark patches independently of background matches."""
    patches = floor_patch_scores(reference, current, matrix, points)
    scores = [s for s in patches if s is not None]
    if len(scores) < 3 or sum(s >= .75 for s in scores) < 3:
        reason = 'insufficient_usable_landmarks' if len(scores) < 3 else 'floor_landmark_mismatch'
        raise FloorAlignmentError('manual floor landmarks '+reason,
            {'method': 'single_frame', 'reason': reason, 'scores': patches,
             'landmarks': floor_landmark_quality(reference, points)})
    return scores


def _read_frame(capture, time_ms):
    if time_ms < 0:
        return None
    capture.set(cv2.CAP_PROP_POS_MSEC, time_ms)
    ok, frame = capture.read()
    return frame if ok else None


def temporal_floor_alignment(capture, reference, current, matrix, points,
                             reference_ms, current_ms, reference_cache=None):
    """Recover occluded patches without reducing the 3-landmark/.75 threshold.

    Every extra frame must align to its anchor. Each accepted landmark must
    match in two distinct current frames. Background matching alone never
    grants permission to reuse a court transform.
    """
    reference_cache = reference_cache if reference_cache is not None else {}
    key = int(reference_ms)
    if key not in reference_cache:
        references = [(reference_ms, reference, np.eye(3))]
        for offset in (-1200, -600, 600, 1200):
            image = _read_frame(capture, reference_ms + offset)
            if image is None:
                continue
            try:
                ref_matrix, _ = align_view(reference, image)
                references.append((reference_ms + offset, image, ref_matrix))
            except ValueError:
                continue
        reference_cache[key] = references
    references = reference_cache[key]
    currents = [(current_ms, current, np.eye(3))]
    for offset in (-600, 600):
        image = _read_frame(capture, current_ms + offset)
        if image is None:
            continue
        try:
            current_matrix, _ = align_view(current, image)
            currents.append((current_ms + offset, image, current_matrix))
        except ValueError:
            continue
    supports = [set() for _ in points[:4]]
    best = [0.0 for _ in points[:4]]
    observable = set()
    for current_time, current_image, current_matrix in currents:
        for _, ref_image, ref_matrix in references:
            # Original reference points -> this reference neighbor -> current
            # neighbor. The actual candidate still uses the original matrix.
            neighbor_points = cv2.perspectiveTransform(
                np.float32([points[:4]]), ref_matrix)[0].tolist()
            neighbor_matrix = current_matrix @ matrix @ np.linalg.inv(ref_matrix)
            for i, score in enumerate(floor_patch_scores(
                    ref_image, current_image, neighbor_matrix, neighbor_points)):
                if score is not None:
                    observable.add(i)
                    best[i] = max(best[i], score)
                    if score >= .75:
                        supports[i].add(current_time)
    evidence = {
        'method': 'temporal_floor',
        'reference_frames': len(references), 'current_frames': len(currents),
        'best_scores': [round(s, 4) for s in best],
        'support_counts': [len(s) for s in supports],
        'usable_landmark_count': len(observable),
    }
    if sum(len(s) >= 2 for s in supports) < 3:
        reason = ('insufficient_usable_landmarks' if len(observable) < 3 else
                  'floor_landmark_mismatch' if sum(s >= .75 for s in best) < 3 else
                  'insufficient_temporal_support')
        evidence['reason'] = reason
        raise FloorAlignmentError('temporal floor landmarks '+reason, evidence)
    return evidence


def group_camera_views(video, candidates, calibration):
    """Experimental grouping; unmatched views receive no old court transform."""
    capture=cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise ValueError("video unavailable")
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC,calibration["frame_time_ms"])
        ok,reference=capture.read()
        if not ok:
            raise ValueError("calibration frame unavailable")
        profiles = calibration.get('views') or [calibration]
        references = []
        for profile in profiles:
            capture.set(cv2.CAP_PROP_POS_MSEC, profile['frame_time_ms'])
            ok, image = capture.read()
            if not ok:
                raise ValueError('saved calibration frame unavailable')
            references.append(image)
        transforms={i:p['transform'] for i,p in enumerate(profiles)}
        reports=[]
        reference_cache={}
        for candidate in candidates:
            capture.set(cv2.CAP_PROP_POS_MSEC,max(0,int(candidate["time_ms"])-300))
            ok,frame=capture.read()
            selected=None
            checks=[]
            candidate.pop('validated_court_transform', None)
            candidate.pop('validated_three_point_line', None)
            if ok:
                # Prefer a nearby manually saved view; no camera grouping alone
                # grants permission to reuse that view's numeric coordinates.
                order = sorted(range(len(profiles)), key=lambda i: abs(profiles[i]['frame_time_ms']-candidate['time_ms']))
                for index in order:
                    ref = references[index]
                    try:
                        matrix, metrics = align_view(ref,frame)
                        points = profiles[index].get('image_points')
                        if not points:
                            raise ValueError('manual floor landmarks missing')
                        try:
                            scores = validate_floor_alignment(ref,frame,matrix,points)
                            evidence = {'method': 'single_frame', 'scores': scores}
                        except ValueError:
                            evidence = temporal_floor_alignment(
                                capture, ref, frame, matrix, points,
                                profiles[index]['frame_time_ms'], max(0,int(candidate['time_ms'])-300),
                                reference_cache)
                        checks.append({'profile': index, 'status': 'accepted',
                                       'alignment': metrics, 'floor': evidence})
                        selected=index
                        candidate['validated_court_transform'] = transform_calibration(profiles[index]['transform'],matrix)
                        candidate['validated_three_point_line'] = (cv2.perspectiveTransform(np.float32([profiles[index]['three_point_line']]),matrix)[0].tolist() if profiles[index].get('three_point_line') else None)
                        break
                    except ValueError as error:
                        checks.append({'profile': index, 'status': 'rejected', 'reason': str(error)})
                        if isinstance(error, FloorAlignmentError):
                            checks[-1]['floor'] = error.evidence
                        continue
            if selected is None:
                selected=len(references)
                if not ok:
                    raise ValueError("candidate frame unavailable")
                selected = len(profiles)
                transforms[selected]=None
                candidate.pop('validated_court_transform', None)
                candidate.pop('validated_three_point_line', None)
            candidate["source_scene_id"]=candidate.get("scene_id",0)
            candidate["scene_id"]=selected
            candidate["calibration_view_valid"]=candidate.get('validated_court_transform') is not None
            reports.append({"time_ms":candidate["time_ms"],"view_id":selected,
                            "old_calibration_usable":candidate['calibration_view_valid'],
                            'checks': checks})
        return transforms,reports
    finally:
        capture.release()
