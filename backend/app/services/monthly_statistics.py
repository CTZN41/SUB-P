"""Aggregate saved shot records by recording month (or Korea-local registration month)."""

from __future__ import annotations

from datetime import timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Analysis, Shot
from app.services.court_zones import ZONE_LABELS, ZoneCode
from app.services.shot_statistics import calculate_statistics
from app.services.non_shot_policy import visible_shot


KOREA = ZoneInfo("Asia/Seoul")


def _month(analysis: Analysis) -> str:
    if analysis.recorded_date is not None:
        return analysis.recorded_date.strftime("%Y-%m")
    created = analysis.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created.astimezone(KOREA).strftime("%Y-%m")


def _is_shot(shot: Shot) -> bool:
    return visible_shot(shot)


def monthly_statistics(session: Session, requested_month: str | None = None) -> dict:
    query = select(Analysis, Shot).join(Shot, Shot.analysis_id == Analysis.id)
    if session.info.get('auth_required'):
        query = query.where(Analysis.owner_id == session.info['auth_user_id'])
    rows = session.execute(query).all()
    months = sorted({_month(analysis) for analysis, shot in rows if _is_shot(shot)}, reverse=True)
    month = requested_month or (months[0] if months else None)
    selected = [(analysis, shot) for analysis, shot in rows if month == _month(analysis) and _is_shot(shot)]
    shots = [shot for _, shot in selected]
    analyses = {analysis.id: analysis for analysis, _ in selected}
    return {
        "month": month,
        "available_months": months,
        "analysis_count": len(analyses),
        "unresolved_count": sum(str(shot.result.value) == "needs_review" for shot in shots),
        "statistics": calculate_statistics(shots),
        "shots": [
            {
                "id": shot.id,
                "analysis_id": analysis.id,
                "analysis_title": analysis.title,
                "player_name": shot.player.display_name,
                "jersey_number": shot.player.jersey_number,
                "candidate_time_ms": shot.candidate_time_ms,
                "court_x": shot.court_x,
                "court_y": shot.court_y,
                "zone_code": shot.zone_code,
                "zone_label": ZONE_LABELS.get(ZoneCode(shot.zone_code)) if shot.zone_code in ZoneCode._value2member_map_ else None,
                "result": shot.result,
                "shot_type": shot.shot_type,
                "status": shot.status,
            }
            for analysis, shot in sorted(selected, key=lambda row: (row[0].created_at, row[1].candidate_time_ms, row[1].id), reverse=True)
        ],
    }
