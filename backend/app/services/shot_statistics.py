"""저장된 슛 원본을 기준으로 통계를 매번 다시 계산한다."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Protocol

from app.models import ShotResult
from app.services.court_zones import ZoneCode


class ShotLike(Protocol):
    player_id: str
    zone_code: str
    shot_type: object
    result: object


@dataclass(frozen=True)
class StatLine:
    attempts: int
    made: int
    missed: int
    success_rate: float | None
    attempt_share: float | None = None
    difference_from_overall: float | None = None


def _value(value: object) -> str:
    return str(getattr(value, "value", value))


def _line(shots: list[ShotLike], *, total_attempts: int | None = None, overall_rate: float | None = None) -> StatLine:
    attempts = len(shots)
    made = sum(_value(shot.result) == ShotResult.MADE.value for shot in shots)
    missed = attempts - made
    rate = (made / attempts * 100) if attempts else None
    share = (attempts / total_attempts * 100) if total_attempts else None
    difference = rate - overall_rate if rate is not None and overall_rate is not None else None
    return StatLine(attempts, made, missed, rate, share, difference)


def calculate_statistics(shots: Iterable[ShotLike]) -> dict[str, object]:
    records = list(shots)
    overall = _line(records)

    players = sorted({shot.player_id for shot in records})
    shot_types = sorted({_value(shot.shot_type) for shot in records})
    by_player = {
        player_id: asdict(_line([shot for shot in records if shot.player_id == player_id]))
        for player_id in players
    }
    by_shot_type = {
        shot_type: asdict(
            _line([shot for shot in records if _value(shot.shot_type) == shot_type])
        )
        for shot_type in shot_types
    }
    by_zone = {}
    for zone in ZoneCode:
        zone_shots = [shot for shot in records if _value(shot.zone_code) == zone.value]
        by_zone[zone.value] = asdict(
            _line(
                zone_shots,
                total_attempts=overall.attempts,
                overall_rate=overall.success_rate,
            )
        )

    return {
        "overall": asdict(overall),
        "by_player": by_player,
        "by_shot_type": by_shot_type,
        "by_zone": by_zone,
    }
