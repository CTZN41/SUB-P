"""Trial: decode each requested frame once, in chronological frame order."""
from pathlib import Path
from math import isfinite
import cv2


PLANT_OFFSETS_MS = (-1000, -850, -700, -550, -400, -250, -100)


def dense_shoe_inputs(video, candidate):
    """Show real pre-release shoes/arc, without relabeling a box as a foot.

    Crop centers follow the existing track only to locate image content. A
    clipped shoe remains clipped; no off-screen pixels or ankle coordinates
    are manufactured.
    """
    from app.services import openai_shot_review as r
    release = int(candidate['time_ms'])
    focus = candidate.get('foot_point_px')
    if not focus or not Path(video).is_file():
        return [], {'status': 'unavailable', 'offsets_ms': []}
    from app.services.ground_contact import grounded_image_offsets, plant_sample
    reference = plant_sample(candidate)
    reference_offsets = [reference['time_ms'] - release] if reference else []
    offsets = sorted(o for o in grounded_image_offsets([*PLANT_OFFSETS_MS, *reference_offsets]) if release + o >= 0)
    frames = frame_map(video, [release + o for o in offsets])
    images, available, clipped, padded = [], [], [], []
    for offset in offsets:
        target = release + offset
        frame = frames.get(target)
        if frame is None:
            continue
        track = candidate.get('foot_track') or []
        sample = min(track, key=lambda s: abs(s['time_ms']-target)) if track else None
        point = sample.get('foot_point_px') if sample and abs(sample['time_ms']-target) <= 250 else focus
        if not point or len(point) != 2:
            continue
        if not all(isfinite(float(value)) for value in point):
            continue
        height, width = frame.shape[:2]
        x, y = int(point[0]), int(point[1])
        # Include both shoes and nearby court line at original pixel density.
        x1, x2 = max(0, x-int(width*.14)), min(width, x+int(width*.14))
        y1, y2 = max(0, y-int(height*.24)), min(height, y+int(height*.10))
        crop = frame[y1:y2, x1:x2]
        if crop.shape[0] < 64 or crop.shape[1] < 64:
            continue
        padding_clipped = y+height*.10 >= height or x1 == 0 or x2 == width
        tracked_clipped = (sample.get('foot_point_clipped', False)
                           if sample and abs(sample['time_ms']-target) <= 250 else candidate.get('foot_point_clipped', False))
        edge = not (0 < x < width-1 and 0 < y < height-1) or tracked_clipped
        available.append(offset)
        if edge:
            clipped.append(offset)
        if padding_clipped:
            padded.append(offset)
        images.extend([
            {'type': 'input_text', 'text': (
                f'pre-release shoe/line crop t={offset:+d}ms; '
                f'original pixels ROI=({x1},{y1},{x2},{y2}); '
                'box midpoint is a crop locator, NOT an ankle or contact measurement. '
                + ('Crop padding reaches the original image border; check whether the actual shoes remain visible. ' if padding_clipped else '')
                + ('The tracked foot locator is clipped; do not infer an off-screen sole. ' if edge else '')
                + 'Determine actual ground contact from the shoes in these images; '
                'do not treat airborne feet or an image edge as ground contact.'
            )},
            {'type': 'input_image', 'image_url': r._encode_frame(crop), 'detail': 'high'},
        ])
    return images, {'status': 'available' if available else 'unavailable',
                    'offsets_ms': available, 'image_edge_offsets_ms': clipped,
                    'crop_padding_edge_offsets_ms': padded,
                    'source': 'original_shoe_pixels', 'numeric_proxy_replaced': False}


def position_only_inputs(images, shoes):
    """Keep a small pre-release overview plus the dense shoe sequence for retry."""
    overview = []
    if isinstance(images, list):
        for index in range(0, len(images)-1):
            item = images[index]
            if item.get('type') == 'input_text' and item.get('text') in (
                    'full frame t=-1000ms', 'full frame t=-600ms', 'full frame t=+0ms'):
                overview.extend(images[index:index+2])
    return [*overview, *shoes] if shoes and overview else images


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
    frames = frame_map(video, [time_ms + offset for offset in offsets if time_ms + offset >= 0])
    full, focus, encoded = [], [], {}
    for offset in offsets:
        target = time_ms + offset
        if target < 0:
            continue
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


def dense_rim_inputs(video, time_ms, hoop_point, evidence_end_ms=None, *,
                     include_early=False, scene_start_ms=0, scene_end_ms=None):
    """100ms rim sequence, including earlier contact for fast layups/dunks.

    Unlike fast_outcome_frame_inputs, this deliberately changes the inputs and
    must be validated against outcome labels before use.
    """
    from app.services import openai_shot_review as r
    from app.analysis.candidate_windows import WINDOW_RULES
    # Fast, clean makes can pass through the net between the 100ms samples.
    # Add five midpoint crops to the existing second-pass request, not a new call.
    offsets = sorted(set(range(600, WINDOW_RULES['layup_rim'].after_ms+1, 100)) | {850, 950, 1050, 1150, 1250})
    if include_early:
        offsets = sorted(set(offsets) | set(range(-WINDOW_RULES['layup_rim'].before_ms, 600, 100)))
    frames = frame_map(video, [time_ms + offset for offset in offsets
        if time_ms + offset >= scene_start_ms
        and (scene_end_ms is None or time_ms + offset <= scene_end_ms)])
    images = []
    post_release_crops = 0
    for offset in offsets:
        frame = frames.get(time_ms + offset)
        if frame is None:
            continue
        if offset in (600, 2800):
            images.extend([{"type": "input_text", "text": f"overview t={offset:+d}ms"},
                           {"type": "input_image", "image_url": r._encode_frame(frame), "detail": "high"}])
        crop = r._hoop_crop(frame, hoop_point)
        if crop is not None:
            post_release_crops += offset >= 600
            images.extend([{"type": "input_text", "text": f"rim crop t={offset:+d}ms"},
                           {"type": "input_image", "image_url": r._encode_frame(crop), "detail": "high"}])
    if len(images) < 12 or post_release_crops < 5:
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
