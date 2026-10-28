from types import SimpleNamespace

from app.models import ShotResult, ShotType
from app.services.court_zones import ZoneCode
from app.services.shot_statistics import calculate_statistics


def shot(player: str, zone: ZoneCode, result: ShotResult, shot_type: ShotType):
    return SimpleNamespace(
        player_id=player,
        zone_code=zone.value,
        result=result,
        shot_type=shot_type,
    )


def test_empty_statistics_use_record_none_instead_of_zero_percent() -> None:
    result = calculate_statistics([])

    assert result["overall"]["attempts"] == 0
    assert result["overall"]["success_rate"] is None
    assert len(result["by_zone"]) == 14
    assert result["by_zone"][ZoneCode.RESTRICTED.value]["success_rate"] is None


def test_overall_player_type_and_zone_statistics_match_source_shots() -> None:
    shots = [
        shot("a", ZoneCode.RESTRICTED, ShotResult.MADE, ShotType.LAYUP),
        shot("a", ZoneCode.RESTRICTED, ShotResult.MISSED, ShotType.LAYUP),
        shot("b", ZoneCode.THREE_CENTER, ShotResult.MADE, ShotType.SET_SHOT),
        shot("b", ZoneCode.THREE_WING_LEFT, ShotResult.MISSED, ShotType.SET_SHOT),
    ]

    result = calculate_statistics(shots)

    assert result["overall"] == {
        "attempts": 4,
        "made": 2,
        "missed": 2,
        "success_rate": 50.0,
        "attempt_share": None,
        "difference_from_overall": None,
    }
    assert result["by_player"]["a"]["success_rate"] == 50.0
    assert result["by_shot_type"][ShotType.SET_SHOT.value]["attempts"] == 2
    restricted = result["by_zone"][ZoneCode.RESTRICTED.value]
    assert restricted["attempts"] == 2
    assert restricted["attempt_share"] == 50.0
    assert restricted["difference_from_overall"] == 0.0


def test_recalculation_reflects_add_edit_and_delete() -> None:
    first = shot("a", ZoneCode.MID_CENTER, ShotResult.MISSED, ShotType.SET_SHOT)
    shots = [first]
    assert calculate_statistics(shots)["overall"]["success_rate"] == 0.0

    first.result = ShotResult.MADE
    shots.append(shot("a", ZoneCode.MID_CENTER, ShotResult.MISSED, ShotType.SET_SHOT))
    assert calculate_statistics(shots)["overall"]["success_rate"] == 50.0

    shots.pop()
    assert calculate_statistics(shots)["overall"]["success_rate"] == 100.0
