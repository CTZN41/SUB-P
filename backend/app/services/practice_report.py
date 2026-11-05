"""저장된 통계 비교와 사용자 요청형 AI 연습 리포트."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.models import Analysis, AnalysisStatus, Shot
from app.services.analysis_reuse import atomic_json, digest
from app.services.court_zones import ZONE_LABELS, classify_zone
from app.services.non_shot_policy import visible_shot
from app.services.shot_statistics import calculate_statistics


PROMPT_VERSION = "practice-report-1"
KOREA = ZoneInfo("Asia/Seoul")
READY_STATUSES = (AnalysisStatus.COMPLETED, AnalysisStatus.REVIEW_REQUIRED)
TYPE_LABELS = {
    "catch_and_shoot": "캐치 앤 슛", "set_shot": "제자리 슛",
    "dribble_pull_up": "드리블 풀업", "side_step": "사이드 스텝",
    "layup": "레이업", "dunk": "덩크", "fadeaway": "페이더웨이",
    "step_back": "스텝백", "turnaround": "턴어라운드", "hook_shot": "훅슛",
    "other": "기타", "needs_review": "종류 확인 필요",
}


class ReportError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


class ReportObservation(BaseModel):
    evidence_key: str
    text: str = Field(min_length=1, max_length=300)


class ReportText(BaseModel):
    summary: str = Field(min_length=1, max_length=400)
    findings: list[ReportObservation] = Field(min_length=1, max_length=3)
    next_practice: list[ReportObservation] = Field(min_length=1, max_length=2)


class SavedReport(BaseModel):
    fingerprint: str
    content: ReportText
    model: str
    generated_at: datetime
    elapsed_seconds: float
    api_usage: dict[str, int] | None


def _value(value):
    return getattr(value, "value", value)


def _practice_order(analysis):
    created = analysis.created_at.replace(tzinfo=timezone.utc) if analysis.created_at.tzinfo is None else analysis.created_at
    return (analysis.recorded_date or created.astimezone(KOREA).date(), created, analysis.id)


def _metadata(analysis):
    return {"id": analysis.id, "title": analysis.title, "date": _practice_order(analysis)[0].isoformat()}


def _shots(session, analysis_id):
    return [shot for shot in session.scalars(select(Shot).where(Shot.analysis_id == analysis_id)) if visible_shot(shot)]


def _line(shots):
    line = calculate_statistics(shots)["overall"]
    return {"attempts": line["attempts"], "made": line["made"], "missed": line["missed"],
            "unresolved": line["attempts"] - line["made"] - line["missed"],
            "success_rate": round(line["success_rate"], 2) if line["success_rate"] is not None else None}


def _groups(shots):
    groups = {"overall": {"label": "전체", "kind": "overall", "shots": shots}}
    for shot in shots:
        shot_type = _value(shot.shot_type)
        type_label = TYPE_LABELS.get(shot_type, "기타")
        groups.setdefault(f"type/{shot_type}", {"label": type_label, "kind": "type", "shots": []})["shots"].append(shot)
        if shot.court_x is None or shot.court_y is None or not math.isfinite(shot.court_x) or not math.isfinite(shot.court_y):
            continue
        try:
            zone = classify_zone(shot.court_x, shot.court_y)
        except ValueError:
            continue
        for key, kind, label in ((f"zone/{zone.value}", "zone", ZONE_LABELS[zone]),
                                 (f"zone_type/{zone.value}/{shot_type}", "zone_type", f"{ZONE_LABELS[zone]} · {type_label}")):
            groups.setdefault(key, {"label": label, "kind": kind, "shots": []})["shots"].append(shot)
    return groups


def comparison_context(session, analysis):
    current_shots = _shots(session, analysis.id)
    candidates = session.scalars(select(Analysis).where(
        Analysis.owner_id == analysis.owner_id, Analysis.play_type == analysis.play_type,
        Analysis.status.in_(READY_STATUSES), Analysis.id != analysis.id,
    )).all()
    previous = None
    previous_shots = []
    for candidate in sorted(candidates, key=_practice_order, reverse=True):
        if _practice_order(candidate) < _practice_order(analysis):
            previous_shots = _shots(session, candidate.id)
            if previous_shots:
                previous = candidate
                break
    current_groups, previous_groups = _groups(current_shots), _groups(previous_shots)
    rows = []
    for key in sorted(current_groups.keys() | previous_groups.keys()):
        group = current_groups.get(key) or previous_groups[key]
        current_line = _line(current_groups.get(key, {}).get("shots", []))
        previous_line = _line(previous_groups.get(key, {}).get("shots", [])) if previous else None
        current_rate = current_line["success_rate"]
        previous_rate = previous_line["success_rate"] if previous_line else None
        rows.append({"key": key, "kind": group["kind"], "label": group["label"],
                     "current": current_line, "previous": previous_line,
                     "attempt_change": current_line["attempts"] - previous_line["attempts"] if previous_line else None,
                     "rate_change_pp": round(current_rate - previous_rate, 2) if current_rate is not None and previous_rate is not None else None})
    same_source = bool(previous and (analysis.original_name, analysis.file_size, analysis.duration_ms) ==
                       (previous.original_name, previous.file_size, previous.duration_ms))
    return {"current": _metadata(analysis), "previous": _metadata(previous) if previous else None,
            "same_source_metadata": same_source, "rows": rows,
            "position_unresolved": sum(shot.court_x is None or shot.court_y is None for shot in current_shots),
            "ready": analysis.status in READY_STATUSES and bool(current_shots)}


def _fingerprint(context, model):
    return digest({"version": PROMPT_VERSION, "model": model, "context": context})


def _cache_path(settings, analysis_id):
    return settings.work_path / analysis_id / "practice-report.json"


def report_view(context, settings, analysis_id):
    report = None
    try:
        report = SavedReport.model_validate_json(_cache_path(settings, analysis_id).read_text(encoding="utf-8")).model_dump(mode="json")
    except (OSError, ValueError, KeyError, TypeError, ValidationError):
        report = None
    stale = bool(report and report.get("fingerprint") != _fingerprint(context, settings.openai_vision_model))
    return {**context, "report": report, "stale": stale, "ai_available": bool(settings.openai_api_key)}


@contextmanager
def _generation_lock(path: Path):
    """프로세스가 종료되면 OS가 해제하는 분석별 잠금으로 중복 호출을 막는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ReportError(409, "REPORT_BUSY", "리포트를 생성하고 있습니다. 잠시 후 다시 확인해 주세요.") from error
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _generate_text(context, settings):
    # 분석 제목·이메일·선수 이름·영상은 보내지 않고 코드에서 계산한 통계만 전달한다.
    evidence = {key: context[key] for key in ("rows", "same_source_metadata", "position_unresolved")}
    evidence["has_previous"] = context["previous"] is not None
    prompt = (
        "한국어로 개인 농구 연습 리포트를 작성한다. 제공된 통계만 근거로 삼는다. "
        "summary는 두 문장 이내, findings는 한두 개의 관찰, next_practice는 실행 가능한 과제 한두 개다. "
        "모든 관찰과 과제에는 해당 rows의 key를 evidence_key로 붙인다. "
        "숫자와 성공률은 화면에서 따로 표시하므로 본문에 숫자를 반복하지 말고 짧게 해석한다. "
        "표본이 작으면 우열이나 실력 향상을 단정하지 않는다. 미확정 결과는 성공률 분모에서 제외된다. "
        "위치가 없는 슛은 전체·종류별에는 포함되며 구역별에서는 제외된다. "
        "같은 원본 메타데이터이면 재분석일 수 있으므로 실력 변화라고 쓰지 않는다. "
        "이전 기록이 없으면 이번 연습만 요약한다. 촬영 형태가 같아도 선수 구성은 다를 수 있다. "
        "자세·피로·동작 원인은 통계로 추측하지 않는다. 일반적인 표현으로 구체적 연습 과제를 제안한다."
    )
    start = perf_counter()
    with OpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds, max_retries=0) as client:
        response = client.responses.parse(
            model=settings.openai_vision_model, store=False, reasoning={"effort": "low"},
            max_output_tokens=2000, text_format=ReportText,
            input=[{"role": "system", "content": prompt},
                   {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)}],
        )
    if response.status != "completed" or response.output_parsed is None:
        raise ReportError(502, "REPORT_INCOMPLETE", "AI 요약을 완성하지 못했습니다. 잠시 후 다시 시도해 주세요.")
    content = response.output_parsed
    keys = {row["key"] for row in context["rows"]}
    if any(item.evidence_key not in keys for item in content.findings + content.next_practice):
        raise ReportError(502, "REPORT_EVIDENCE_INVALID", "AI 요약의 통계 근거를 확인하지 못했습니다. 다시 시도해 주세요.")
    usage = response.usage
    return {"content": content.model_dump(), "model": settings.openai_vision_model,
            "generated_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": round(perf_counter() - start, 2),
            "api_usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                          "total_tokens": usage.total_tokens} if usage else None}


def generate_report(session, analysis, settings):
    analysis_id, owner_id = analysis.id, analysis.owner_id
    with _generation_lock(_cache_path(settings, analysis_id).with_suffix(".lock")):
        context = comparison_context(session, analysis)
        view = report_view(context, settings, analysis_id)
        if not context["ready"]:
            raise ReportError(409, "REPORT_NOT_READY", "분석이 완료되고 슛 기록이 있을 때 리포트를 만들 수 있습니다.")
        if view["report"] and not view["stale"]:
            return view
        if not settings.openai_api_key:
            raise ReportError(503, "REPORT_AI_UNAVAILABLE", "OpenAI 연결 설정이 필요합니다.")
        fingerprint = _fingerprint(context, settings.openai_vision_model)
        # AI 응답을 기다리는 동안 DB 연결을 점유하지 않는다.
        session.rollback()
        try:
            report = _generate_text(context, settings)
        except (OpenAIError, ValidationError) as error:
            raise ReportError(502, "REPORT_AI_FAILED", "AI 요약을 생성하지 못했습니다. 잠시 후 다시 시도해 주세요.") from error
        session.expire_all()
        current = session.get(Analysis, analysis_id)
        if current is None or current.owner_id != owner_id:
            raise ReportError(409, "REPORT_RECORD_CHANGED", "분석 기록이 변경됐습니다. 화면을 다시 열어 주세요.")
        updated_context = comparison_context(session, current)
        if _fingerprint(updated_context, settings.openai_vision_model) != fingerprint:
            raise ReportError(409, "REPORT_RECORD_CHANGED", "생성 중 슛 기록이 변경됐습니다. 최신 기록으로 다시 생성해 주세요.")
        report["fingerprint"] = fingerprint
        atomic_json(_cache_path(settings, analysis_id), report)
        return report_view(updated_context, settings, analysis_id)
