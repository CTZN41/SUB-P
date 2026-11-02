"""Track a user's existing calibration across a validated zoom/reframe.

Does not find new court corners or save calibration. Refuses weak matches.
"""
import cv2
import numpy as np


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
        references=[reference]
        transforms={0:calibration["transform"]}
        reports=[]
        for candidate in candidates:
            capture.set(cv2.CAP_PROP_POS_MSEC,max(0,int(candidate["time_ms"])-300))
            ok,frame=capture.read()
            selected=None
            if ok:
                for index,ref in enumerate(references):
                    try:
                        align_view(ref,frame)
                        selected=index
                        break
                    except ValueError:
                        continue
            if selected is None:
                selected=len(references)
                if not ok:
                    raise ValueError("candidate frame unavailable")
                references.append(frame)
                transforms[selected]=None
            candidate["source_scene_id"]=candidate.get("scene_id",0)
            candidate["scene_id"]=selected
            candidate["calibration_view_valid"]=transforms[selected] is not None
            reports.append({"time_ms":candidate["time_ms"],"view_id":selected,
                            "old_calibration_usable":transforms[selected] is not None})
        return transforms,reports
    finally:
        capture.release()
