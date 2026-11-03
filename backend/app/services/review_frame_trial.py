"""Trial: decode each requested frame once, in chronological frame order."""
from pathlib import Path
import cv2


def frame_map(video: Path, times: list[float]):
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise ValueError("video unavailable")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            raise ValueError("invalid frame rate")
        indices = {time: max(0, round(time / 1000 * fps)) for time in times}
        unique = sorted(set(indices.values()))
        frames = {}
        if not unique:
            return {}
        if not cap.set(cv2.CAP_PROP_POS_FRAMES, unique[0]):
            raise ValueError("frame seek failed")
        cursor = unique[0]
        for target in unique:
            while cursor < target:
                if not cap.grab():
                    return {time: frames[index] for time, index in indices.items() if index in frames}
                cursor += 1
            ok, frame = cap.read()
            cursor += 1
            if ok:
                frames[target] = frame
        return {time: frames[index] for time, index in indices.items() if index in frames}
    finally:
        cap.release()


def fast_candidate_frame_inputs(video, time_ms, focus_point=None, *, include_outcome_extension=False):
    from app.services import openai_shot_review as r
    offsets = r.FRAME_OFFSETS_MS + (r.OUTCOME_EXTENSION_OFFSETS_MS if include_outcome_extension else ())
    frames = frame_map(video, [max(0, time_ms + offset) for offset in offsets])
    full, focus, encoded = [], [], {}
    for offset in offsets:
        target = max(0, time_ms + offset)
        frame = frames.get(target)
        if frame is None:
            continue
        if target not in encoded:
            encoded[target] = r._encode_frame(frame)
        full.extend([{"type": "input_text", "text": f"full frame t={offset:+d}ms"},
                     {"type": "input_image", "image_url": encoded[target], "detail": "high"}])
        if offset in r.FOCUS_OFFSETS_MS:
            crop = r._focus_crop(frame, focus_point)
            if crop is not None:
                focus.extend([{"type": "input_text", "text": f"player crop t={offset:+d}ms"},
                              {"type": "input_image", "image_url": r._encode_frame(crop), "detail": "high"}])
    if len(full) < 6:
        raise r.OpenAIShotReviewError("insufficient frames")
    return [*full, *focus]


def fast_outcome_frame_inputs(video, time_ms, hoop_point, evidence_end_ms=None):
    from app.services import openai_shot_review as r
    impact = min(4000, max(800, evidence_end_ms - time_ms)) if evidence_end_ms is not None else None
    dense = tuple(impact + delta for delta in (-600, -450, -300, -150, 0, 150, 300)) if impact is not None else ()
    offsets = sorted(set(r.OUTCOME_OFFSETS_MS + dense))
    frames = frame_map(video, [max(0, time_ms + offset) for offset in offsets])
    full, crops = [], []
    for offset in offsets:
        frame = frames.get(max(0, time_ms + offset))
        if frame is None:
            continue
        if offset in r.OUTCOME_OFFSETS_MS:
            full.extend([{"type": "input_text", "text": f"full frame t={offset:+d}ms"},
                         {"type": "input_image", "image_url": r._encode_frame(frame), "detail": "high"}])
        crop = r._hoop_crop(frame, hoop_point)
        if crop is not None:
            crops.extend([{"type": "input_text", "text": f"rim crop t={offset:+d}ms"},
                          {"type": "input_image", "image_url": r._encode_frame(crop), "detail": "high"}])
    if len(full) < 6:
        raise r.OpenAIShotReviewError("insufficient frames")
    return [*full, *crops]


def dense_rim_inputs(video, time_ms, hoop_point, evidence_end_ms=None):
    """Experimental new evidence: 100ms rim sequence plus two overview frames.

    Unlike fast_outcome_frame_inputs, this deliberately changes the inputs and
    must be validated against outcome labels before use.
    """
    from app.services import openai_shot_review as r
    # Fast, clean makes can pass through the net between the 100ms samples.
    # Add five midpoint crops to the existing second-pass request, not a new call.
    offsets = sorted(set(range(600, 2801, 100)) | {850, 950, 1050, 1150, 1250})
    frames = frame_map(video, [time_ms + offset for offset in offsets])
    images = []
    for offset in offsets:
        frame = frames.get(time_ms + offset)
        if frame is None:
            continue
        if offset in (600, 2800):
            images.extend([{"type": "input_text", "text": f"overview t={offset:+d}ms"},
                           {"type": "input_image", "image_url": r._encode_frame(frame), "detail": "high"}])
        crop = r._hoop_crop(frame, hoop_point)
        if crop is not None:
            images.extend([{"type": "input_text", "text": f"rim crop t={offset:+d}ms"},
                           {"type": "input_image", "image_url": r._encode_frame(crop), "detail": "high"}])
    if len(images) < 12:
        raise r.InsufficientOutcomeEvidenceError("insufficient rim evidence")
    return images


def evidence_rich_detail_inputs(video, time_ms, focus_point, hoop_point):
    """Keep every base input and append eight targeted rim frames."""
    from app.services import openai_shot_review as r
    images = fast_candidate_frame_inputs(video,time_ms,focus_point)
    offsets = tuple(range(800,2201,200))
    frames = frame_map(video,[time_ms+offset for offset in offsets])
    for offset in offsets:
        frame = frames.get(time_ms+offset)
        if frame is None:
            continue
        crop = r._hoop_crop(frame,hoop_point)
        if crop is not None:
            images.extend([{"type":"input_text","text":f"scoring rim evidence t={offset:+d}ms"},
                           {"type":"input_image","image_url":r._encode_frame(crop),"detail":"high"}])
    return images
