"""Review-first shot candidates from local MP4; no made/missed judgement."""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

SAMPLE_FPS = 8.0
DETAILED_PERSON_HEIGHT_RATIO = .25
SEGMENT_CONTEXT_MS = 3000
HOOP_HOLD_MS = 1500
SCENE_JUMP_RATIO = .18
CANDIDATE_COOLDOWN_MS = 1000
RIM_EVIDENCE_MERGE_MS = 2500
POSE_CONFIDENCE = .2


class Phase7Error(RuntimeError):
    pass


class Phase7Canceled(RuntimeError):
    pass


def center(box: dict[str, Any]) -> tuple[float, float]:
    x1, y1, x2, y2 = box["xyxy"]
    return (x1 + x2) / 2, (y1 + y2) / 2


def choose(boxes: list[dict[str, Any]], previous: dict[str, Any] | None, width: int, height: int, limit: float) -> dict[str, Any] | None:
    if not boxes:
        return None
    if previous:
        nearest = min(boxes, key=lambda box: math.dist(center(box), center(previous)))
        if math.dist(center(nearest), center(previous)) < math.hypot(width, height) * limit:
            return nearest
    return max(boxes, key=lambda box: box["confidence"])


def release_candidate(previous: dict[str, Any], current: dict[str, Any], hoop: list[float], height: int) -> dict[str, Any] | None:
    if not previous.get("hoop_valid") or not current.get("hoop_valid"):
        return None
    person, ball, next_ball = previous.get("person"), previous.get("ball"), current.get("ball")
    if not person or not ball or not next_ball:
        return None
    bx, by = center(ball)
    nx, ny = center(next_ball)
    x1, y1, x2, y2 = person["xyxy"]
    w, h = x2 - x1, y2 - y1
    if not (x1 - .3*w <= bx <= x2 + .3*w and y1 - .3*h <= by <= y1 + .55*h):
        return None
    elapsed = current["time_ms"] - previous["time_ms"]
    if not 0 < elapsed <= 750 or by - ny < height * .035:
        return None
    if math.dist((nx, ny), hoop) >= math.dist((bx, by), hoop):
        return None
    confidence = min(person["confidence"], ball["confidence"], next_ball["confidence"])
    reasons = ["탐지 신뢰도가 낮음"] if confidence < .35 else []
    if previous.get("camera_shift") or current.get("camera_shift"):
        reasons.append("골대 위치 변화 — 코트 좌표 재확인 필요")
    if previous.get("rim_missing") or current.get("rim_missing"):
        reasons.append("림 자동 탐지 실패 — 기준점으로 판단")
    return {
        "time_ms": previous["time_ms"], "next_time_ms": current["time_ms"],
        "frame_index": previous["frame_index"],
        "foot_point_px": [round((x1+x2)/2, 2), round(y2, 2)],
        "ball_point_px": [round(bx, 2), round(by, 2)],
        "confidence": round(confidence, 4),
        "review_reasons": reasons,
    }


def merge_candidates(candidates: list[dict[str, Any]], cooldown_ms: int = CANDIDATE_COOLDOWN_MS) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: item["time_ms"]):
        if merged and candidate["time_ms"] - merged[-1]["time_ms"] < cooldown_ms:
            if candidate["confidence"] > merged[-1]["confidence"]:
                merged[-1] = candidate
        else:
            merged.append(candidate)
    return merged


def candidate_overlaps_range(candidate: dict[str, Any], start_ms: int, end_ms: int) -> bool:
    """Keep a release just before the boundary when its tracked flight enters the range."""
    candidate_time = int(candidate["time_ms"])
    next_time = int(candidate.get("next_time_ms", candidate_time))
    return candidate_time < end_ms and (candidate_time >= start_ms or next_time >= start_ms)


def _detections(result: Any, names: dict[int, str]) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = {"person": [], "basketball": [], "rim": []}
    for box in result.boxes:
        name = names.get(int(box.cls.item()))
        if name in found:
            found[name].append({"confidence": float(box.conf.item()), "xyxy": [float(value) for value in box.xyxy[0].tolist()]})
    return found


def _pose_for_person(result: Any, person: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the COCO keypoints belonging to the tracked player."""
    if person is None or result.keypoints is None or not len(result.keypoints):
        return None
    person_center = center(person)
    best: tuple[float, dict[str, Any]] | None = None
    for index, keypoints in enumerate(result.keypoints):
        xy = keypoints.xy[0].tolist()
        confidence = (
            keypoints.conf[0].tolist()
            if keypoints.conf is not None
            else [1.0] * len(xy)
        )
        if len(xy) < 17:
            continue
        if result.boxes is not None and index < len(result.boxes):
            box = [float(value) for value in result.boxes.xyxy[index].tolist()]
            distance = math.dist(person_center, ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2))
        else:
            visible = [point for point, score in zip(xy, confidence) if score >= POSE_CONFIDENCE]
            if not visible:
                continue
            pose_center = (
                sum(point[0] for point in visible) / len(visible),
                sum(point[1] for point in visible) / len(visible),
            )
            distance = math.dist(person_center, pose_center)
        pose = {"xy": xy, "confidence": confidence}
        if best is None or distance < best[0]:
            best = (distance, pose)
    return best[1] if best else None


def _raised_shooting_arm(record: dict[str, Any]) -> tuple[int, tuple[float, float], float] | None:
    """Find a confidently raised wrist; COCO sides are shoulder/elbow/wrist."""
    pose, person = record.get("pose"), record.get("person")
    if not pose or not person:
        return None
    xy, confidence = pose["xy"], pose["confidence"]
    person_height = person["xyxy"][3] - person["xyxy"][1]
    choices: list[tuple[int, tuple[float, float], float]] = []
    for shoulder, elbow, wrist in ((5, 7, 9), (6, 8, 10)):
        score = min(confidence[shoulder], confidence[elbow], confidence[wrist])
        if score < POSE_CONFIDENCE:
            continue
        shoulder_y = xy[shoulder][1]
        elbow_y = xy[elbow][1]
        wrist_point = tuple(xy[wrist])
        if (
            wrist_point[1] <= shoulder_y + person_height * .16
            and elbow_y <= shoulder_y + person_height * .38
        ):
            choices.append((wrist, wrist_point, score))
    return max(choices, key=lambda item: item[2]) if choices else None


def pose_motion_candidates(
    records: list[dict[str, Any]],
    hoop_point: list[float],
    frame_height: int,
) -> list[dict[str, Any]]:
    """Use a raised shooting arm plus later ball flight as a second candidate path."""
    raw: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        raised = _raised_shooting_arm(record)
        person = record.get("person")
        if not raised or not person or not record.get("hoop_valid"):
            continue
        if record.get("hoop_source") == "calibration":
            # Crossfades and opening overlays can imitate a raised arm. Pose-only
            # rescue candidates require rim evidence from the current scene.
            continue
        wrist_index, wrist_point, pose_score = raised
        x1, y1, x2, y2 = person["xyxy"]
        person_height = y2 - y1
        # A shooting motion must rise from a lower wrist position or have the ball
        # close to the wrist shortly before the raised pose.
        preparation_seen = False
        history_seen = False
        nearby_ball: dict[str, Any] | None = None
        for previous in reversed(records[max(0, index - 10):index + 1]):
            if record["time_ms"] - previous["time_ms"] > 1250:
                break
            previous_pose = previous.get("pose")
            if previous_pose and previous_pose["confidence"][wrist_index] >= POSE_CONFIDENCE:
                if previous["time_ms"] < record["time_ms"] - 200:
                    history_seen = True
                if previous_pose["xy"][wrist_index][1] - wrist_point[1] >= person_height * .1:
                    preparation_seen = True
            for ball in previous.get("balls", []):
                if math.dist(center(ball), wrist_point) <= max(40.0, person_height * .28):
                    nearby_ball = ball
                    preparation_seen = True
        if not preparation_seen or not history_seen:
            continue
        target_hoop = record.get("tracked_hoop") or record.get("detected_hoop") or hoop_point
        flight_ball = flight_record = None
        reference = center(nearby_ball) if nearby_ball else wrist_point
        for future in records[index:index + 13]:
            if future["time_ms"] - record["time_ms"] > 1600:
                break
            for ball in future.get("balls", []):
                ball_point = center(ball)
                if (
                    ball_point[1] <= y1 + person_height * .15
                    and math.dist(ball_point, target_hoop) < math.dist(reference, target_hoop)
                ):
                    flight_ball, flight_record = ball, future
                    break
            if flight_ball:
                break
        if not flight_ball or not flight_record:
            continue
        confidence = min(person["confidence"], pose_score, flight_ball["confidence"])
        reasons = ["선수 자세와 이후 공 비행으로 찾은 슛 동작 후보"]
        if confidence < .35:
            reasons.insert(0, "탐지 신뢰도가 낮음")
        raw.append({
            "time_ms": record["time_ms"],
            "next_time_ms": flight_record["time_ms"],
            "frame_index": record["frame_index"],
            "foot_point_px": [round((x1 + x2) / 2, 2), round(y2, 2)],
            "ball_point_px": [round(value, 2) for value in center(flight_ball)],
            "confidence": round(confidence, 4),
            "review_reasons": reasons,
            "candidate_source": "pose_shot_motion",
            "scene_id": int(record.get("scene_id", 0)),
            "tracked_hoop_px": [round(value, 2) for value in target_hoop],
            "calibration_shifted": bool(record.get("calibration_shifted")),
        })
    return merge_candidates(raw)


def merge_candidate_evidence(
    candidates: list[dict[str, Any]],
    frame_width: int,
    frame_height: int,
) -> list[dict[str, Any]]:
    """Merge late rim/pose evidence into a preceding release without hiding fast shots."""
    merged: list[dict[str, Any]] = []
    diagonal = math.hypot(frame_width, frame_height)
    source_priority = {"trajectory": 3, "pose_shot_motion": 2, "rim_approach_backtrack": 1}

    def evidence(candidate: dict[str, Any]) -> list[dict[str, Any]]:
        return candidate.get("_evidence", [{key: value for key, value in candidate.items() if key != "_evidence"}])

    for incoming in sorted(candidates, key=lambda item: item["time_ms"]):
        source = incoming.get("candidate_source", "trajectory")
        match_index = None
        for index in range(len(merged) - 1, -1, -1):
            existing = merged[index]
            gap = incoming["time_ms"] - existing["time_ms"]
            if gap > RIM_EVIDENCE_MERGE_MS:
                break
            if existing.get("scene_id", 0) != incoming.get("scene_id", 0):
                continue
            same_player = math.dist(existing["foot_point_px"], incoming["foot_point_px"]) <= diagonal * .08
            evidence_close = incoming["time_ms"] - int(existing.get("next_time_ms", existing["time_ms"])) <= 1200
            sources = set(existing.get("candidate_sources", [existing.get("candidate_source", "trajectory")]))
            sources.update(incoming.get("candidate_sources", [source]))
            flight_overlaps = (
                gap <= 1800
                and int(existing.get("next_time_ms", existing["time_ms"])) >= incoming["time_ms"] - 150
                and existing.get("candidate_source", "trajectory") == "trajectory"
                and source == "trajectory"
            )
            pose_follows_flight = (
                source == "pose_shot_motion"
                and existing.get("candidate_source", "trajectory") == "trajectory"
                and 0 <= incoming["time_ms"] - int(existing.get("next_time_ms", existing["time_ms"])) <= 500
                and math.dist(existing["foot_point_px"], incoming["foot_point_px"]) <= diagonal * .15
            )
            if evidence_close and "rim_approach_backtrack" in sources:
                match_index = index
                break
            if flight_overlaps or pose_follows_flight or (same_player and gap < CANDIDATE_COOLDOWN_MS):
                match_index = index
                break
        if match_index is None:
            item = {**incoming}
            item.setdefault("candidate_source", source)
            item["candidate_sources"] = [source]
            item["_evidence"] = evidence(incoming)
            merged.append(item)
            continue
        existing = merged[match_index]
        existing_source = existing.get("candidate_source", "trajectory")
        preferred = existing
        if flight_overlaps:
            preferred = incoming
        elif source_priority.get(source, 0) > source_priority.get(existing_source, 0):
            preferred = incoming
        combined_sources = sorted(set(existing.get("candidate_sources", [existing_source]) + [source]))
        combined_reasons = list(dict.fromkeys(existing.get("review_reasons", []) + incoming.get("review_reasons", [])))
        evidence_end = max(
            int(existing.get("evidence_end_ms", existing.get("next_time_ms", existing["time_ms"]))),
            int(incoming.get("evidence_end_ms", incoming.get("next_time_ms", incoming["time_ms"]))),
        )
        preferred = {**preferred}
        preferred["candidate_sources"] = combined_sources
        preferred["review_reasons"] = combined_reasons
        preferred["evidence_end_ms"] = evidence_end
        preferred["confidence"] = round(max(existing["confidence"], incoming["confidence"]), 4)
        preferred["_evidence"] = [*evidence(existing), *evidence(incoming)]
        merged[match_index] = preferred
    # Rim evidence can be attached after a late trajectory candidate was first
    # inserted. Revisit adjacent candidates once so that evidence can also join
    # the earlier release from the same shot.
    consolidated: list[dict[str, Any]] = []
    for incoming in merged:
        if not consolidated:
            consolidated.append(incoming)
            continue
        existing = consolidated[-1]
        gap = incoming["time_ms"] - existing["time_ms"]
        sources = set(existing.get("candidate_sources", [existing.get("candidate_source", "trajectory")]))
        sources.update(incoming.get("candidate_sources", [incoming.get("candidate_source", "trajectory")]))
        same_player = math.dist(existing["foot_point_px"], incoming["foot_point_px"]) <= diagonal * .08
        evidence_close = incoming["time_ms"] - int(existing.get("next_time_ms", existing["time_ms"])) <= 1200
        flight_overlaps = (
            gap <= 1800
            and int(existing.get("next_time_ms", existing["time_ms"])) >= incoming["time_ms"] - 150
            and "trajectory" in set(existing.get("candidate_sources", []))
            and "trajectory" in set(incoming.get("candidate_sources", []))
        )
        if (
            gap <= RIM_EVIDENCE_MERGE_MS
            and evidence_close
            and ("rim_approach_backtrack" in sources or flight_overlaps)
        ):
            if flight_overlaps:
                incoming["_evidence"] = [*evidence(existing), *evidence(incoming)]
                incoming["candidate_sources"] = sorted(sources)
                incoming["review_reasons"] = list(dict.fromkeys(
                    existing.get("review_reasons", []) + incoming.get("review_reasons", [])
                ))
                incoming["evidence_end_ms"] = max(
                    int(existing.get("evidence_end_ms", existing.get("next_time_ms", existing["time_ms"]))),
                    int(incoming.get("evidence_end_ms", incoming.get("next_time_ms", incoming["time_ms"]))),
                )
                incoming["confidence"] = round(max(existing["confidence"], incoming["confidence"]), 4)
                consolidated[-1] = incoming
                continue
            existing["_evidence"] = [*evidence(existing), *evidence(incoming)]
            existing["candidate_sources"] = sorted(sources)
            existing["review_reasons"] = list(dict.fromkeys(
                existing.get("review_reasons", []) + incoming.get("review_reasons", [])
            ))
            existing["evidence_end_ms"] = max(
                int(existing.get("evidence_end_ms", existing.get("next_time_ms", existing["time_ms"]))),
                int(incoming.get("evidence_end_ms", incoming.get("next_time_ms", incoming["time_ms"]))),
            )
            existing["confidence"] = round(max(existing["confidence"], incoming["confidence"]), 4)
            continue
        consolidated.append(incoming)
    filtered: list[dict[str, Any]] = []
    for candidate in consolidated:
        sources = set(candidate.get("candidate_sources", [candidate.get("candidate_source", "trajectory")]))
        if sources == {"pose_shot_motion"} or (
            candidate["confidence"] < .35
            and candidate.get("calibration_shifted")
            and "rim_approach_backtrack" not in sources
        ):
            continue
        preceding = filtered[-1] if filtered else None
        # A weak ball-only trace immediately after a strong pose-backed release
        # is usually the same ball in flight. Keep a second pose-backed release.
        if preceding and sources == {"trajectory"} and candidate["confidence"] < .25 and (
            "pose_shot_motion" in preceding.get("candidate_sources", [])
            and preceding["confidence"] >= .5
            and preceding.get("scene_id", 0) == candidate.get("scene_id", 0)
            and 0 < candidate["time_ms"] - preceding["time_ms"] <= 1200
        ):
            preceding["review_reasons"] = list(dict.fromkeys(
                preceding.get("review_reasons", []) + ["약한 후속 공 궤적을 같은 슛으로 병합"]
            ))
            continue
        filtered.append(candidate)
    return [refine_candidate_release(candidate) for candidate in filtered]


def refine_candidate_release(candidate: dict[str, Any]) -> dict[str, Any]:
    """Choose a release frame from retained evidence, rather than its first trigger."""
    evidence = candidate.pop("_evidence", [])
    trajectories = [item for item in evidence if item.get("candidate_source", "trajectory") == "trajectory"]
    if not trajectories:
        return candidate
    # Consolidation deliberately keeps the later release for overlapping ball
    # tracks. Do not undo that choice by picking the strongest older track.
    trajectory = next(
        (item for item in reversed(trajectories)
         if item["time_ms"] == candidate["time_ms"]
         and candidate.get("candidate_source", "trajectory") == "trajectory"),
        max(trajectories, key=lambda item: item["confidence"]),
    )
    flight_end = int(trajectory.get("next_time_ms", trajectory["time_ms"]))
    following_poses = sorted(
        (
            item for item in evidence
            if item.get("candidate_source") == "pose_shot_motion"
            and 0 < item["time_ms"] - flight_end <= 500
        ),
        key=lambda item: item["time_ms"],
    )
    reliable_pose = next((item for item in following_poses if item["confidence"] >= .25), None)
    if reliable_pose:
        selected = reliable_pose
        release_time = int(selected["time_ms"])
        candidate["release_basis"] = "pose_after_ball_flight"
    elif following_poses:
        selected = trajectory
        release_time = flight_end
        candidate["release_basis"] = "ball_flight_end"
    else:
        selected = trajectory
        release_time = int(trajectory["time_ms"])
        candidate["release_basis"] = "trajectory_start"
    candidate["time_ms"] = release_time
    candidate["next_time_ms"] = max(release_time, int(selected.get("next_time_ms", release_time)))
    candidate["confidence"] = selected["confidence"]
    candidate["candidate_source"] = selected.get("candidate_source", "trajectory")
    if "tracked_hoop_px" in selected:
        candidate["tracked_hoop_px"] = selected["tracked_hoop_px"]
    if "calibration_shifted" in selected:
        candidate["calibration_shifted"] = selected["calibration_shifted"]
    if reliable_pose:
        for key in ("frame_index", "foot_point_px", "ball_point_px"):
            if key in selected:
                candidate[key] = selected[key]
    elif following_poses:
        for key, future_key in (("frame_index", "next_frame_index"),
                                ("foot_point_px", "next_foot_point_px"),
                                ("ball_point_px", "next_ball_point_px")):
            value = selected.get(future_key) or selected.get(key)
            if value is not None:
                candidate[key] = value
    else:
        for key in ("frame_index", "foot_point_px", "ball_point_px"):
            if key in selected:
                candidate[key] = selected[key]
    return candidate


def _ball_cell(box: dict[str, Any], width: int, height: int) -> tuple[int, int]:
    """Group nearly fixed detections so video logos are not tracked as balls."""
    x, y = center(box)
    return (int(x / max(1.0, width * .02)), int(y / max(1.0, height * .02)))


def remove_static_ball_detections(
    records: list[dict[str, Any]], width: int, height: int
) -> int:
    counts = Counter(
        _ball_cell(ball, width, height)
        for record in records
        for ball in record.get("balls", [])
    )
    threshold = max(8, math.ceil(len(records) * .35))
    fixed_cells = {cell for cell, count in counts.items() if count >= threshold}
    removed = 0
    for record in records:
        balls = record.get("balls", [])
        filtered = [
            ball for ball in balls
            if _ball_cell(ball, width, height) not in fixed_cells
        ]
        removed += len(balls) - len(filtered)
        record["balls"] = filtered
    return removed


def trajectory_candidates(
    records: list[dict[str, Any]],
    hoop_point: list[float],
    frame_height: int,
    max_lookahead: int = 10,
) -> list[dict[str, Any]]:
    """Reconnect short ball-detection gaps and require a release above the head."""
    raw: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        person = record.get("person")
        if not person or not record.get("hoop_valid"):
            continue
        x1, y1, x2, y2 = person["xyxy"]
        person_width, person_height = x2 - x1, y2 - y1
        starts = []
        for ball in record.get("balls", []):
            ball_x, ball_y = center(ball)
            if (
                x1 - .3 * person_width <= ball_x <= x2 + .3 * person_width
                and y1 - .5 * person_height <= ball_y <= y1 + .4 * person_height
            ):
                starts.append(ball)
        if not starts:
            continue
        ball = max(starts, key=lambda item: item["confidence"])
        ball_x, ball_y = center(ball)
        ball_width = ball["xyxy"][2] - ball["xyxy"][0]
        ball_height = ball["xyxy"][3] - ball["xyxy"][1]
        ball_area = max(1.0, ball_width * ball_height)
        next_ball = next_record = None
        target_hoop = record.get("tracked_hoop") or record.get("detected_hoop") or hoop_point
        for future in records[index + 1 : index + 1 + max_lookahead]:
            if future["time_ms"] - record["time_ms"] > 1500:
                break
            if not future.get("hoop_valid"):
                continue
            for option in future.get("balls", []):
                next_x, next_y = center(option)
                elapsed_ms = future["time_ms"] - record["time_ms"]
                next_width = option["xyxy"][2] - option["xyxy"][0]
                next_height = option["xyxy"][3] - option["xyxy"][1]
                if (
                    ball_y - next_y >= max(4.0, person_height * .2)
                    and next_y <= y1 - person_height * .1
                    and math.dist((ball_x, ball_y), (next_x, next_y))
                    <= max(frame_height * .16, frame_height * 3.5 * elapsed_ms / 1000)
                    # A rebounding ball approaching the camera can move upward
                    # in the image too, but its apparent area grows sharply.
                    and next_width * next_height <= ball_area * 3.24
                    and math.dist((next_x, next_y), target_hoop)
                    < math.dist((ball_x, ball_y), target_hoop)
                ):
                    next_ball, next_record = option, future
                    break
            if next_ball:
                break
        if not next_ball or not next_record:
            continue
        confidence = min(
            person["confidence"], ball["confidence"], next_ball["confidence"]
        )
        reasons = ["탐지 신뢰도가 낮음"] if confidence < .35 else []
        if person_height < frame_height * DETAILED_PERSON_HEIGHT_RATIO:
            reasons.append("선수가 멀리 있어 AI 또는 사람의 확인 권장")
        if record.get("rim_missing") or next_record.get("rim_missing"):
            reasons.append("림 자동 탐지 실패 — 확인된 골대 위치 사용")
        raw.append({
            "time_ms": record["time_ms"],
            "next_time_ms": next_record["time_ms"],
            "next_frame_index": next_record["frame_index"],
            "next_foot_point_px": [
                round((next_record["person"]["xyxy"][0] + next_record["person"]["xyxy"][2]) / 2, 2),
                round(next_record["person"]["xyxy"][3], 2),
            ] if next_record.get("person") else None,
            "next_ball_point_px": [round(value, 2) for value in center(next_ball)],
            "frame_index": record["frame_index"],
            "foot_point_px": [round((x1 + x2) / 2, 2), round(y2, 2)],
            "ball_point_px": [round(ball_x, 2), round(ball_y, 2)],
            "confidence": round(confidence, 4),
            "review_reasons": reasons,
            "scene_id": int(record.get("scene_id", 0)),
            "tracked_hoop_px": [round(value, 2) for value in target_hoop],
            "calibration_shifted": bool(record.get("calibration_shifted")),
        })
    return merge_candidates(raw)


def rim_approach_candidates(
    records: list[dict[str, Any]],
    hoop_point: list[float],
    frame_height: int,
) -> list[dict[str, Any]]:
    """Backtrack from a ball approaching the rim when release frames were missed."""
    raw: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not record.get("hoop_valid"):
            continue
        target_hoop = record.get("tracked_hoop") or record.get("detected_hoop") or hoop_point
        near_rim = [
            ball for ball in record.get("balls", [])
            if math.dist(center(ball), target_hoop) <= max(24.0, frame_height * .1)
        ]
        if not near_rim:
            continue
        rim_ball = max(near_rim, key=lambda item: item["confidence"])
        rim_time = record["time_ms"]
        lookback = [
            previous for previous in records[max(0, index - 18):index]
            if previous.get("person")
            and previous.get("hoop_valid")
            and 450 <= rim_time - previous["time_ms"] <= 1800
        ]
        if not lookback:
            continue
        release_record = min(
            lookback,
            key=lambda previous: abs((rim_time - previous["time_ms"]) - 900),
        )
        person = release_record["person"]
        x1, y1, x2, y2 = person["xyxy"]
        confidence = min(person["confidence"], rim_ball["confidence"]) * .85
        reasons = ["릴리스 공을 놓쳐 림 접근 시점에서 약 1초 전으로 추정"]
        if confidence < .35:
            reasons.insert(0, "탐지 신뢰도가 낮음")
        raw.append({
            "time_ms": release_record["time_ms"],
            "next_time_ms": rim_time,
            "frame_index": release_record["frame_index"],
            "foot_point_px": [round((x1 + x2) / 2, 2), round(y2, 2)],
            "ball_point_px": [round(value, 2) for value in center(rim_ball)],
            "confidence": round(confidence, 4),
            "review_reasons": reasons,
            "candidate_source": "rim_approach_backtrack",
            "scene_id": int(release_record.get("scene_id", 0)),
            "tracked_hoop_px": [round(value, 2) for value in target_hoop],
            "calibration_shifted": bool(release_record.get("calibration_shifted")),
        })
    return merge_candidates(raw)


def attach_candidate_foot_tracks(
    candidates: list[dict[str, Any]], records: list[dict[str, Any]],
    frame_width: int, frame_height: int,
) -> None:
    """Keep nearby pre-release feet so a layup's late trigger is not its location."""
    max_distance = math.hypot(frame_width, frame_height) * .2
    for candidate in candidates:
        candidate_time = int(candidate["time_ms"])
        candidate_foot = candidate.get("foot_point_px")
        if not isinstance(candidate_foot, (list, tuple)) or len(candidate_foot) != 2:
            continue
        track = []
        for record in records:
            if not candidate_time - 1800 <= record["time_ms"] <= candidate_time + 300:
                continue
            if record.get("scene_id") != candidate.get("scene_id") or record.get("calibration_shifted"):
                continue
            person = record.get("person")
            if not person:
                continue
            x1, _, x2, y2 = person["xyxy"]
            foot = [(x1 + x2) / 2, y2]
            if math.dist(foot, candidate_foot) > max_distance:
                continue
            track.append({
                "time_ms": record["time_ms"],
                "foot_point_px": [round(foot[0], 2), round(foot[1], 2)],
            })
        if track:
            candidate["foot_track"] = track


def analyze_video(source: Path, model_path: Path, hoop_point: list[float], *, pose_model_path: Path | None = None,
                  sample_fps: float = SAMPLE_FPS,
                  start_ms: int = 0, end_ms: int | None = None,
                  progress: Callable[[int], None] | None = None, cancelled: Callable[[], bool] | None = None,
                  collect_motion_hints: bool = False,
                  inference_device: str = "cpu") -> dict[str, Any]:
    """Read source frames at fixed intervals and retain their original timestamps."""
    if not source.is_file() or not model_path.exists():
        raise Phase7Error("원본 영상 또는 탐지 모델 파일을 찾을 수 없습니다.")
    if sample_fps <= 0 or len(hoop_point) != 2:
        raise Phase7Error("분석 간격 또는 골대 기준점을 확인해 주세요.")
    total_started = perf_counter()
    timings: Counter[str] = Counter()
    from ultralytics import YOLO  # Torch must load before OpenCV on Windows.
    import cv2
    started = perf_counter()
    model = YOLO(str(model_path), task="detect")
    pose_model = YOLO(str(pose_model_path), task="pose") if pose_model_path and pose_model_path.exists() else None
    timings["model_load"] += perf_counter() - started
    if model_path.is_dir():
        import yaml
        metadata = yaml.safe_load((model_path / "metadata.yaml").read_text(encoding="utf-8"))
        raw_names = metadata["names"]
    else:
        raw_names = model.names
    names = {int(key): str(value) for key, value in raw_names.items()}
    if not {"person", "basketball"}.issubset(names.values()):
        raise Phase7Error("탐지 모델에 사람 또는 농구공 항목이 없습니다.")
    wanted = [key for key, value in names.items() if value in {"person", "basketball", "rim"}]
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise Phase7Error("원본 영상을 읽을 수 없습니다.")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if fps <= 0 or count <= 0 or width <= 0 or height <= 0:
            raise Phase7Error("영상의 길이, 크기 또는 프레임 수를 읽을 수 없습니다.")
        if not (0 <= hoop_point[0] < width and 0 <= hoop_point[1] < height):
            raise Phase7Error("저장한 골대 기준점이 영상 화면 밖에 있습니다.")
        duration_ms = round(count / fps * 1000)
        if start_ms < 0 or start_ms >= duration_ms or (end_ms is not None and end_ms <= start_ms):
            raise Phase7Error("분석할 영상 구간이 올바르지 않습니다.")
        step = max(1, round(fps / sample_fps))
        context_start_ms = max(0, start_ms - SEGMENT_CONTEXT_MS)
        first_frame = round(context_start_ms / 1000 * fps)
        last_frame = min(count, round((end_ms or duration_ms) / 1000 * fps))
        person_last = None
        tracked_hoop: list[float] | None = None
        tracked_hoop_time_ms: int | None = None
        scene_id = 0
        records: list[dict[str, Any]] = []
        inference_sizes: Counter[int] = Counter()
        issues: Counter[str] = Counter()
        sampled = person_frames = rim_frames = 0
        if first_frame and not cap.set(cv2.CAP_PROP_POS_FRAMES, first_frame):
            raise Phase7Error("분석 구간의 시작 프레임으로 이동하지 못했습니다.")
        next_frame_index = first_frame
        for frame_index in range(first_frame, last_frame, step):
            started = perf_counter()
            should_cancel = cancelled and cancelled()
            timings["cancel_callback"] += perf_counter() - started
            if should_cancel:
                raise Phase7Canceled("사용자가 분석을 취소했습니다.")
            started = perf_counter()
            while next_frame_index < frame_index:
                if not cap.grab():
                    raise Phase7Error("분석 구간의 영상 프레임을 건너뛰지 못했습니다.")
                next_frame_index += 1
            ok, frame = cap.read()
            next_frame_index += 1
            timings["decode"] += perf_counter() - started
            if not ok:
                issues["읽지 못한 프레임"] += 1
                continue
            started = perf_counter()
            result = model.predict(
                frame, classes=wanted, conf=.12, imgsz=640,
                device=inference_device, verbose=False
            )[0]
            timings["detector_640"] += perf_counter() - started
            inference_sizes[640] += 1
            groups = _detections(result, names)
            person = choose(groups["person"], person_last, width, height, .18)
            if (
                person is None
                or person["xyxy"][3] - person["xyxy"][1]
                < height * DETAILED_PERSON_HEIGHT_RATIO
            ):
                started = perf_counter()
                detailed = model.predict(
                    frame, classes=wanted, conf=.12, imgsz=960,
                    device=inference_device, verbose=False
                )[0]
                timings["detector_960"] += perf_counter() - started
                inference_sizes[960] += 1
                detailed_groups = _detections(detailed, names)
                detailed_person = choose(
                    detailed_groups["person"], person_last, width, height, .18
                )
                if detailed_person is not None:
                    groups, person = detailed_groups, detailed_person
            pose = None
            if pose_model is not None and person is not None:
                started = perf_counter()
                pose_result = pose_model.predict(
                    frame, classes=[0], conf=.2, imgsz=640,
                    device=inference_device, verbose=False,
                )[0]
                timings["pose"] += perf_counter() - started
                pose = _pose_for_person(pose_result, person)
            time_ms = round(frame_index / fps * 1000)
            diagonal = math.hypot(width, height)
            rim = None
            if groups["rim"]:
                if tracked_hoop is None:
                    rim = min(groups["rim"], key=lambda box: math.dist(center(box), hoop_point))
                else:
                    nearest = min(groups["rim"], key=lambda box: math.dist(center(box), tracked_hoop))
                    rim = nearest if math.dist(center(nearest), tracked_hoop) <= diagonal * .12 else max(groups["rim"], key=lambda box: box["confidence"])
                detected_hoop = list(center(rim))
                camera_shift = bool(tracked_hoop and math.dist(detected_hoop, tracked_hoop) > diagonal * .05)
                if tracked_hoop and math.dist(detected_hoop, tracked_hoop) > diagonal * SCENE_JUMP_RATIO:
                    scene_id += 1
                tracked_hoop = detected_hoop
                tracked_hoop_time_ms = time_ms
                hoop_source = "detected"
            elif tracked_hoop is not None and tracked_hoop_time_ms is not None and time_ms - tracked_hoop_time_ms <= HOOP_HOLD_MS:
                camera_shift = False
                hoop_source = "carried"
            else:
                tracked_hoop = list(hoop_point)
                tracked_hoop_time_ms = time_ms
                camera_shift = False
                hoop_source = "calibration"
            calibration_shifted = math.dist(tracked_hoop, hoop_point) > diagonal * .05
            if calibration_shifted:
                issues["동적 골대가 저장된 기준점과 다름 — 장면별 코트 보정 필요"] += 1
            hoop_valid = tracked_hoop is not None
            sampled += 1
            person_frames += person is not None
            rim_frames += bool(groups["rim"])
            if person is None:
                issues["선수 가림 또는 탐지 실패"] += 1
            records.append({
                "time_ms": time_ms,
                "frame_index": frame_index,
                "person": person,
                "pose": pose,
                "balls": groups["basketball"],
                "hoop_valid": hoop_valid,
                "camera_shift": camera_shift,
                "rim_missing": rim is None,
                "detected_hoop": list(center(rim)) if rim else None,
                "tracked_hoop": list(tracked_hoop),
                "hoop_source": hoop_source,
                "scene_id": scene_id,
                "calibration_shifted": calibration_shifted,
            })
            person_last = person
            if progress and (sampled % 8 == 0 or frame_index + step >= last_frame):
                started = perf_counter()
                progress(time_ms)
                timings["progress_callback"] += perf_counter() - started
        started = perf_counter()
        static_balls = remove_static_ball_detections(records, width, height)
        ball_frames = sum(bool(record["balls"]) for record in records)
        issues["공 가림 또는 탐지 실패"] += sampled - ball_frames
        if static_balls:
            issues["고정 그래픽으로 추정한 공 탐지 제외"] += static_balls
        if sampled == 0 or person_frames == 0 or ball_frames == 0:
            raise Phase7Error("선수와 공을 함께 확인할 영상 증거가 부족합니다.")
        release_candidates = trajectory_candidates(records, hoop_point, height)
        motion_candidates = pose_motion_candidates(records, hoop_point, height) if pose_model is not None else []
        rim_candidates = rim_approach_candidates(records, hoop_point, height)
        candidates = merge_candidate_evidence(
            [*release_candidates, *motion_candidates, *rim_candidates], width, height
        )
        range_end_ms = round(last_frame / fps * 1000)
        candidates = [
            candidate for candidate in candidates
            if candidate_overlaps_range(candidate, start_ms, range_end_ms)
        ]
        attach_candidate_foot_tracks(candidates, records, width, height)
        if not candidates:
            issues["슛 후보 없음 — 직접 확인 필요"] += 1
        if rim_frames == 0:
            issues["림 자동 탐지 실패 — 확인된 골대 위치 사용"] += 1
        result = {"method": "phase7-yolo-pose-trajectory-candidates-v3", "detector_model": model_path.name,
                "notice": "슛 후보이며 OpenAI 또는 사람의 검토 전에는 확정하지 않습니다.",
                "source_fps": round(fps, 4), "sample_fps": round(fps/step, 4), "source_frame_count": count,
                "duration_ms": duration_ms, "context_start_ms": context_start_ms,
                "range_start_ms": start_ms, "range_end_ms": range_end_ms,
                "sampled_frames": sampled, "person_frames": person_frames,
                "ball_frames": ball_frames, "rim_frames": rim_frames,
                "inference_sizes": {str(key): value for key, value in inference_sizes.items()},
                "pose_motion_enabled": pose_model is not None,
                "hoop_point_px": hoop_point,
                "candidates": candidates, "issues": dict(issues)}
        if collect_motion_hints:
            result["motion_hints"] = [
                {"time_ms": item["time_ms"], "confidence": item["confidence"],
                 "scene_id": item["scene_id"]}
                for item in motion_candidates
                if start_ms <= item["time_ms"] < range_end_ms
            ]
        timings["candidate_rules"] += perf_counter() - started
        result["performance"] = {
            "device": inference_device,
            "seconds": {key: round(value, 4) for key, value in timings.items()},
            "total_seconds": round(perf_counter() - total_started, 4),
        }
        return result
    finally:
        cap.release()


def save_result(work_path: Path, analysis_id: str, result: dict[str, Any]) -> Path:
    """Write atomically so canceled jobs cannot leave a partial result."""
    directory = (work_path / analysis_id).resolve()
    if work_path.resolve() not in directory.parents:
        raise Phase7Error("분석 결과 경로가 올바르지 않습니다.")
    directory.mkdir(parents=True, exist_ok=True)
    output, temporary = directory / "phase7-candidates.json", directory / "phase7-candidates.tmp"
    try:
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
