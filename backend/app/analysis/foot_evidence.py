"""Quality checks for bounding-box foot proxies, without inferring hidden feet."""
from math import isfinite


def foot_proxy_is_clipped(foot, frame_size=None, person_box=None) -> bool:
    if not isinstance(foot, (list, tuple)) or len(foot) != 2:
        return True
    try:
        if not all(isfinite(float(value)) for value in foot):
            return True
        if frame_size is None:
            return False  # Legacy results have no image size; do not invent one.
        width, height = map(float, frame_size)
        if not all(isfinite(value) and value > 0 for value in (width, height)):
            return True
        margin = max(2, min(width, height)*.002)
        x, y = map(float, foot)
        if not (margin < x < width-margin and margin < y < height-margin):
            return True
        if person_box is not None:
            x1, _, x2, _ = person_box
            return x1 <= margin or x2 >= width-margin
        return False
    except (TypeError, ValueError):
        return True
