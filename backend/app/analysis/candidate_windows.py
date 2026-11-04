"""Stage-specific fixed windows, each with an explicit timestamp anchor.

Gate images and planted-foot images serve different tasks and remain separately
bounded. These are relative windows; clip/range edges truncate, never duplicate.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class WindowRule:
    before_ms: int
    after_ms: int
    anchor: str


DECODER_CONTEXT_MS = 3000

# Each stage can be tuned separately here. Equal current rescan values are an
# explicit choice, not an assumption that their timestamp anchors are equal.
WINDOW_RULES = {
    'pose_hint': WindowRule(2000, 3500, 'pose_hint_time'),
    'rejected_candidate': WindowRule(2000, 3500, 'original_candidate_time'),
    'shot_gate': WindowRule(1400, 900, 'current_candidate_time'),
    'prior_shot_gate': WindowRule(3200, 100, 'late_candidate_time'),
    'plant_refresh': WindowRule(1400, 150, 'corrected_release_time'),
    'ground_contact': WindowRule(1200, -150, 'corrected_release_time'),
    'sole_pixels': WindowRule(400, 200, 'pose_stable_plant_time'),
    'detail_review': WindowRule(4000, 3500, 'corrected_release_time'),
    'outcome_extension': WindowRule(4000, 5500, 'corrected_release_time'),
}
RESCAN_BEFORE_MS = WINDOW_RULES['rejected_candidate'].before_ms
RESCAN_AFTER_MS = WINDOW_RULES['rejected_candidate'].after_ms
GATE_BEFORE_MS = WINDOW_RULES['shot_gate'].before_ms
GATE_AFTER_MS = WINDOW_RULES['shot_gate'].after_ms
PRIOR_BEFORE_MS = WINDOW_RULES['prior_shot_gate'].before_ms
PRIOR_AFTER_MS = WINDOW_RULES['prior_shot_gate'].after_ms


def rescan_window(time_ms: int, start_ms: int, end_ms: int, *, purpose='rejected_candidate') -> tuple[int, int]:
    rule = WINDOW_RULES[purpose]
    return max(start_ms, time_ms - rule.before_ms), min(end_ms, time_ms + rule.after_ms)


def window_policy() -> dict:
    return {'version': '2026-11-04', 'rescan_offsets_ms': [-RESCAN_BEFORE_MS, RESCAN_AFTER_MS],
            'plant_refresh_offsets_ms': [-WINDOW_RULES['plant_refresh'].before_ms, WINDOW_RULES['plant_refresh'].after_ms],
            'edge_policy': 'truncate_to_available_range', 'overlap_policy': 'union_for_local_rescans',
            'decoder_pre_context_ms': DECODER_CONTEXT_MS,
            'stage_rules': {name: {'offsets_ms': [-rule.before_ms, rule.after_ms], 'anchor': rule.anchor}
                            for name, rule in WINDOW_RULES.items()}}
