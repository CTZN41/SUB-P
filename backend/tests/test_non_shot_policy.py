from copy import deepcopy
from types import SimpleNamespace
import pytest
from app.services.non_shot_policy import confirmed_non_shot, exclude_confirmed_non_shots, visible_shot


@pytest.mark.parametrize('confidence,verdict,review,expected', [
    (.9, 'not_shot', False, True), (.99, 'not_shot', True, False),
    (.89, 'not_shot', False, False), (.99, 'unclear', False, False),
    (.99, 'shot', True, False), (.99, 'not_shot', None, True),
    (None, 'not_shot', False, False), (True, 'not_shot', False, False),
    (float('nan'), 'not_shot', False, False),
])
def test_only_confident_uncontradicted_non_shots_are_excluded(confidence, verdict, review, expected):
    assert confirmed_non_shot({'verdict': verdict, 'confidence': confidence}, {'is_shot': review}) is expected


def test_user_confirmed_records_survive_legacy_non_shot_verdict():
    data = {'shot_gate': {'verdict': 'not_shot', 'confidence': .99}, 'ai_review': {'is_shot': False}}
    assert not visible_shot(SimpleNamespace(status='needs_review', confidence_data=data))
    assert visible_shot(SimpleNamespace(status='user_confirmed', confidence_data=data))
    assert visible_shot(SimpleNamespace(status='needs_review', confidence_data={**data, 'confirmed_by_user': True}))


def test_exclusion_is_idempotent_and_keeps_scoring_unknown_and_full_audit():
    rejected = {'time_ms': 1000, 'shot_gate': {'status': 'complete', 'verdict': 'not_shot', 'confidence': .95},
                'ai_review': {'is_shot': False}}
    scoring_unknown = {'time_ms': 2000, 'shot_gate': {'verdict': 'shot', 'confidence': .95},
                       'ai_review': {'is_shot': True, 'outcome': 'unclear'}}
    result = {'candidates': [deepcopy(rejected), deepcopy(scoring_unknown)]}
    exclude_confirmed_non_shots(result); exclude_confirmed_non_shots(result)
    assert result['candidates'] == [scoring_unknown]
    assert len(result['excluded_non_shot_candidates']) == 1
    assert result['excluded_non_shot_candidates'][0]['shot_gate'] == rejected['shot_gate']


def test_existing_api_hides_non_shot_without_deleting_and_manual_confirmation_restores(tmp_path):
    from test_analysis_jobs import make_ready_client
    from app.models import Shot, ShotStatus, ShotResult, ShotType, ShotSource, Player
    from sqlalchemy import select
    client, analysis_id, engine, factory = make_ready_client(tmp_path)
    with factory() as db:
        player = db.scalar(select(Player).where(Player.analysis_id == analysis_id))
        shot = Shot(analysis_id=analysis_id, player_id=player.id, candidate_time_ms=1000,
                    sequence_start_ms=0, sequence_end_ms=3000, result=ShotResult.NEEDS_REVIEW,
                    shot_type=ShotType.NEEDS_REVIEW, source=ShotSource.AUTO, status=ShotStatus.NEEDS_REVIEW,
                    confidence_data={'shot_gate': {'verdict': 'not_shot', 'confidence': .97}})
        db.add(shot); db.commit(); shot_id = shot.id
    with client:
        response = client.get(f'/api/analyses/{analysis_id}/shots')
        assert response.status_code == 200 and response.json()['shots'] == []
        with factory() as db:
            persisted = db.get(Shot, shot_id)
            assert persisted is not None
            persisted.status = ShotStatus.USER_CONFIRMED
            db.commit()
        assert len(client.get(f'/api/analyses/{analysis_id}/shots').json()['shots']) == 1
    engine.dispose()
