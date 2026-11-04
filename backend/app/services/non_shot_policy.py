"""Exclude confident AI non-shots; retain ambiguity and user-confirmed shots."""


def confirmed_non_shot(gate, review=None):
    gate, review = gate or {}, review or {}
    confidence = gate.get('confidence')
    return (gate.get('status', 'complete') == 'complete'
            and gate.get('verdict') == 'not_shot'
            and isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
            and .9 <= confidence <= 1
            and review.get('is_shot') is not True)


def visible_shot(shot):
    data = shot.confidence_data or {}
    if getattr(shot.status, 'value', shot.status) == 'user_confirmed' or data.get('confirmed_by_user'):
        return True
    return not confirmed_non_shot(data.get('shot_gate'), data.get('ai_review'))


def exclude_confirmed_non_shots(result):
    kept, excluded = [], list(result.get('excluded_non_shot_candidates', []))
    for candidate in result.get('candidates', []):
        if confirmed_non_shot(candidate.get('shot_gate'), candidate.get('ai_review')):
            candidate['exclusion'] = {'reason': 'ai_confirmed_non_shot'}
            excluded.append(candidate)
        else:
            kept.append(candidate)
    result['candidates'] = kept
    result['excluded_non_shot_candidates'] = excluded
    result['non_shot_recovery_summary'] = {'excluded_count': len(excluded), 'retained_count': len(kept)}
    return result
