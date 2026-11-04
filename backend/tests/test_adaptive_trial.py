from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.analysis.adaptive_trial import (
    add_focused_candidates, rescan_rejected_times, select_rescan_windows,
)
from app.analysis.shot_gate_trial import (
    ShotExistenceDecision, review_adaptive_candidates, review_shot_existence,
)
from app.core.config import Settings
from app.services.candidate_enrichment import enrich_candidates


def test_unrepresented_pose_hint_rescans_missing_middle_shot() -> None:
    base = {
        "range_start_ms": 400000,
        "range_end_ms": 412000,
        "candidates": [{"time_ms": 402333, "scene_id": 0},
                       {"time_ms": 411533, "scene_id": 0}],
        "motion_hints": [
            {"time_ms": 400333, "confidence": .59, "scene_id": 0},
            {"time_ms": 405800, "confidence": .66, "scene_id": 0},
            {"time_ms": 407533, "confidence": .65, "scene_id": 0},
            {"time_ms": 410467, "confidence": .63, "scene_id": 0},
        ],
    }
    assert select_rescan_windows(base) == [(403800, 411033)]


def test_shot_gate_can_return_not_shot_without_changing_a_saved_candidate(monkeypatch) -> None:
    candidate = {"time_ms": 1618567, "candidate_source": "trajectory"}
    monkeypatch.setattr(
        "app.analysis.shot_gate_trial.shot_gate_frames",
        lambda *_: [{"type": "input_image", "image_url": "test", "detail": "high"}],
    )

    class FakeResponses:
        def parse(self, **kwargs):
            assert kwargs["model"] == "gpt-6.1-terra"
            assert kwargs["store"] is False
            return SimpleNamespace(
                output_parsed=ShotExistenceDecision(
                    verdict="not_shot", confidence=.98,
                    evidence="The ball returns to the player.",
                ),
                usage=SimpleNamespace(input_tokens=5756, output_tokens=73),
            )

    client = SimpleNamespace(responses=FakeResponses())
    decision = review_shot_existence(
        Path("unused.mp4"), candidate["time_ms"],
        Settings(_env_file=None, openai_api_key="test-key"), client=client,
    )
    assert decision["verdict"] == "not_shot"
    assert decision["usage"] == {"input_tokens": 5756, "output_tokens": 73}
    assert candidate == {"time_ms": 1618567, "candidate_source": "trajectory"}


def test_late_false_candidate_can_recover_earlier_real_release(monkeypatch) -> None:
    trial = {
        "base": {
            "candidates": [{"time_ms": 411533, "scene_id": 0}],
            "motion_hints": [{"time_ms": 410467, "confidence": .629, "scene_id": 0}],
        },
        "novel_candidates": [],
    }
    def review(_path, time_ms, _settings, *, client=None):
        return {"verdict": "shot" if time_ms == 410467 else "not_shot",
                "confidence": .99}
    monkeypatch.setattr("app.analysis.shot_gate_trial.review_shot_existence", review)
    result = review_adaptive_candidates(
        Path("unused.mp4"), trial,
        Settings(_env_file=None, openai_api_key="test-key"),
    )
    assert result["accepted_times_ms"] == [410467]
    assert result["needs_rescan_times_ms"] == []
    assert result["decisions"][0]["trial_action"] == "retime_to_pose_hint"


def test_rejected_cues_are_grouped_for_high_fps_rescan(monkeypatch) -> None:
    calls = []
    def analyze(*args, **kwargs):
        calls.append((kwargs["start_ms"], kwargs["end_ms"], kwargs["sample_fps"]))
        return {"sampled_frames": 20, "candidates": []}
    monkeypatch.setattr("app.analysis.adaptive_trial.analyze_video", analyze)
    trial = {"base": {"range_start_ms": 1600000, "range_end_ms": 1620000}}
    result = rescan_rejected_times(
        Path("unused.mp4"), Path("model.pt"), [1100, 160], Path("pose.pt"),
        trial, [1603533, 1614600, 1618200],
    )
    assert calls == [
        (1601533, 1607033, 30),
        (1612600, 1620000, 30),
    ]
    assert result["candidates"] == []


def test_unclear_shot_gate_keeps_candidate_for_review(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.analysis.shot_gate_trial.review_shot_existence",
        lambda *args, **kwargs: {"verdict": "unclear", "confidence": .55},
    )
    trial = {"base": {"candidates": [{"time_ms": 1000}], "motion_hints": []},
             "novel_candidates": []}
    result = review_adaptive_candidates(
        Path("unused.mp4"), trial,
        Settings(_env_file=None, openai_api_key="test-key"),
    )
    assert result["accepted_times_ms"] == []
    assert result["needs_review_times_ms"] == [1000]


def test_focused_rescan_adds_missing_candidate_without_removing_original(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.analysis.adaptive_trial.analyze_video",
        lambda *args, **kwargs: {
            "candidates": [{"time_ms": 406367, "scene_id": 0}],
        },
    )
    result = {
        "range_start_ms": 400000, "range_end_ms": 412000,
        "candidates": [{"time_ms": 402333, "scene_id": 0}],
        "motion_hints": [{"time_ms": 405800, "confidence": .66, "scene_id": 0}],
    }
    add_focused_candidates(Path("video.mp4"), Path("model.pt"), [10, 20],
                           Path("pose.pt"), result)
    assert [item["time_ms"] for item in result["candidates"]] == [402333, 406367]
    assert result["focused_rescan"]["added_candidates"] == 1


def test_negative_gate_recovers_real_shot_after_rescan(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.candidate_enrichment.review_shot_existence",
        lambda _path, time_ms, _settings: {
            "verdict": "shot" if time_ms == 1604167 else "not_shot",
            "confidence": .99,
            "usage": {"input_tokens": 100 if time_ms == 1604167 else 80,
                      "output_tokens": 10 if time_ms == 1604167 else 8},
            "seconds": 2.0,
        },
    )
    monkeypatch.setattr(
        "app.services.candidate_enrichment.analyze_video",
        lambda *args, **kwargs: {
            "candidates": [{"time_ms": 1604167, "scene_id": 0}],
        },
    )
    result = {
        "duration_ms": 1620000,
        "candidates": [{"time_ms": 1603533, "scene_id": 0}],
    }
    settings = Settings(_env_file=None, openai_api_key="test-key")
    enrich_candidates(Path("video.mp4"), Path("model.pt"), Path("pose.pt"),
                      [10, 20], result, settings)
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["time_ms"] == 1604167
    assert result["candidates"][0]["shot_gate"]["resolution"] == "retimed_after_rescan"
    assert result["shot_gate_summary"]["calls"] == 2
    assert result["shot_gate_summary"]["usage"] == {
        "input_tokens": 180, "output_tokens": 18,
    }
    assert result["shot_gate_summary"]["response_seconds"] == 4.0


def test_rescanned_shot_uses_confident_release_offset(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.candidate_enrichment.review_shot_existence",
        lambda _path, time_ms, _settings: (
            {"verdict": "shot", "confidence": .97, "release_offset_ms": 700}
            if time_ms == 14200 else {"verdict": "not_shot", "confidence": .99}
        ),
    )
    monkeypatch.setattr(
        "app.services.candidate_enrichment.analyze_video",
        lambda *args, **kwargs: {
            "candidates": [{"time_ms": 14200, "next_time_ms": 14300,
                            "frame_index": 426, "scene_id": 0}],
        },
    )
    result = {
        "duration_ms": 27000, "source_fps": 30,
        "candidates": [{"time_ms": 15733, "scene_id": 0}],
    }
    settings = Settings(_env_file=None, openai_api_key="test-key")
    enrich_candidates(Path("video.mp4"), Path("model.pt"), Path("pose.pt"),
                      [10, 20], result, settings)
    candidate = result["candidates"][0]
    assert candidate["time_ms"] == 14900
    assert candidate["frame_index"] == 447
    assert candidate["next_time_ms"] == 14900
    assert candidate["release_basis"] == "ai_rescan_window"
    assert candidate["shot_gate"]["detector_candidate_time_ms"] == 14200
    assert candidate["shot_gate"]["original_time_ms"] == 15733


def test_confirmed_shot_uses_release_offset_without_rescan(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.candidate_enrichment.review_shot_existence",
        lambda *_: {"verdict": "shot", "confidence": .99, "release_offset_ms": 700},
    )
    result = {
        "duration_ms": 27000, "source_fps": 30,
        "candidates": [{"time_ms": 22933, "next_time_ms": 23000,
                        "frame_index": 688, "scene_id": 0}],
    }
    settings = Settings(_env_file=None, openai_api_key="test-key")
    enrich_candidates(Path("video.mp4"), Path("model.pt"), Path("pose.pt"),
                      [10, 20], result, settings)
    candidate = result["candidates"][0]
    assert candidate["time_ms"] == 23633
    assert candidate["frame_index"] == 709
    assert candidate["next_time_ms"] == 23633
    assert candidate["release_basis"] == "ai_shot_gate_window"
    assert candidate["shot_gate"]["detector_candidate_time_ms"] == 22933


def test_negative_gate_keeps_unresolved_candidate(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.candidate_enrichment.review_shot_existence",
        lambda *_: {"verdict": "not_shot", "confidence": .99},
    )
    monkeypatch.setattr(
        "app.services.candidate_enrichment.analyze_video",
        lambda *args, **kwargs: {"candidates": []},
    )
    monkeypatch.setattr(
        "app.services.candidate_enrichment.review_prior_shot_existence",
        lambda *_: {"verdict": "not_shot", "confidence": .99},
    )
    result = {
        "duration_ms": 20000,
        "candidates": [{"time_ms": 10000, "scene_id": 0}],
    }
    settings = Settings(_env_file=None, openai_api_key="test-key")
    enrich_candidates(Path("video.mp4"), Path("model.pt"), Path("pose.pt"),
                      [10, 20], result, settings)
    assert [item["time_ms"] for item in result["candidates"]] == [10000]
    assert result["candidates"][0]["shot_gate"]["resolution"] == "needs_review"


def test_late_dunk_cue_is_retimed_by_wider_prior_review(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.candidate_enrichment.review_shot_existence",
        lambda *_: {"verdict": "not_shot", "confidence": .99,
                   "usage": {"input_tokens": 100, "output_tokens": 10}},
    )
    monkeypatch.setattr(
        "app.services.candidate_enrichment.analyze_video",
        lambda *args, **kwargs: {"candidates": []},
    )
    monkeypatch.setattr(
        "app.services.candidate_enrichment.review_prior_shot_existence",
        lambda *_: {"verdict": "shot", "confidence": .99,
                   "release_offset_ms": -1400,
                   "usage": {"input_tokens": 200, "output_tokens": 20}},
    )
    result = {"duration_ms": 19000, "source_fps": 24,
              "candidates": [{"time_ms": 15875, "scene_id": 0}]}
    settings = Settings(_env_file=None, openai_api_key="test-key")
    enrich_candidates(Path("video.mp4"), Path("model.pt"), Path("pose.pt"),
                      [10, 20], result, settings)
    candidate = result["candidates"][0]
    assert candidate["time_ms"] == 14475
    assert candidate["shot_gate"]["resolution"] == "retimed_from_prior_window"
    assert candidate["shot_gate"]["original_time_ms"] == 15875
    assert result["shot_gate_summary"]["calls"] == 2
    assert result["shot_gate_summary"]["usage"] == {
        "input_tokens": 300, "output_tokens": 30,
    }


def test_prior_review_does_not_duplicate_existing_shot(monkeypatch) -> None:
    def narrow(_path, time_ms, _settings):
        return {"verdict": "shot" if time_ms == 14475 else "not_shot",
                "confidence": .99}
    monkeypatch.setattr(
        "app.services.candidate_enrichment.review_shot_existence", narrow,
    )
    monkeypatch.setattr(
        "app.services.candidate_enrichment.analyze_video",
        lambda *args, **kwargs: {"candidates": []},
    )
    monkeypatch.setattr(
        "app.services.candidate_enrichment.review_prior_shot_existence",
        lambda *_: {"verdict": "shot", "confidence": .99,
                   "release_offset_ms": -1400},
    )
    result = {"duration_ms": 19000, "candidates": [
        {"time_ms": 14475, "scene_id": 0},
        {"time_ms": 15875, "scene_id": 0},
    ]}
    settings = Settings(_env_file=None, openai_api_key="test-key")
    enrich_candidates(Path("video.mp4"), Path("model.pt"), Path("pose.pt"),
                      [10, 20], result, settings)
    assert [candidate["time_ms"] for candidate in result["candidates"]] == [14475, 15875]
    assert result["candidates"][1]["shot_gate"]["resolution"] == "needs_review"


def test_gate_call_limit_counts_rescan_review_and_remaining_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.candidate_enrichment.review_shot_existence",
        lambda _path, time_ms, _settings: {
            "verdict": "shot" if time_ms == 1500 else "not_shot",
            "confidence": .99,
        },
    )
    monkeypatch.setattr(
        "app.services.candidate_enrichment.analyze_video",
        lambda *args, **kwargs: {"candidates": [{"time_ms": 1500}]},
    )
    result = {"duration_ms": 10000, "candidates": [
        {"time_ms": 1000}, {"time_ms": 5000},
    ]}
    settings = Settings(
        _env_file=None, openai_api_key="test-key",
        openai_max_shot_gate_reviews=2,
    )
    enrich_candidates(Path("video.mp4"), Path("model.pt"), Path("pose.pt"),
                      [10, 20], result, settings)
    assert result["shot_gate_summary"]["calls"] == 2
    assert result["shot_gate_summary"]["unreviewed_candidates"] == 1
    assert "shot_gate" not in result["candidates"][1]


def test_last_of_twelve_candidates_keeps_a_recheck_call(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.candidate_enrichment.review_shot_existence",
        lambda _path, time_ms, _settings: {
            "verdict": "not_shot" if time_ms == 12000 else "shot",
            "confidence": .99,
        },
    )
    monkeypatch.setattr(
        "app.services.candidate_enrichment.analyze_video",
        lambda *args, **kwargs: {"candidates": [{"time_ms": 12800}]},
    )
    result = {"duration_ms": 20000, "candidates": [
        {"time_ms": time_ms} for time_ms in range(1000, 13000, 1000)
    ]}
    settings = Settings(_env_file=None, openai_api_key="test-key")
    enrich_candidates(Path("video.mp4"), Path("model.pt"), Path("pose.pt"),
                      [10, 20], result, settings)
    assert result["candidates"][-1]["time_ms"] == 12800
    assert result["candidates"][-1]["shot_gate"]["resolution"] == "retimed_after_rescan"
    assert result["shot_gate_summary"]["calls"] == 13
    assert result["shot_gate_summary"]["unreviewed_candidates"] == 0
def test_negative_gate_reports_follow_up_work(monkeypatch):
    from app.services.candidate_enrichment import enrich_candidates
    from app.core.config import Settings
    from pathlib import Path
    monkeypatch.setattr('app.services.candidate_enrichment.review_shot_existence',
                        lambda *a, **kw: {'status': 'complete', 'verdict': 'not_shot', 'confidence': .99})
    monkeypatch.setattr('app.services.candidate_enrichment.review_prior_shot_existence',
                        lambda *a, **kw: {'status': 'complete', 'verdict': 'not_shot', 'confidence': .99})
    monkeypatch.setattr('app.services.candidate_enrichment.analyze_video', lambda *a, **kw: {'candidates': []})
    activity = []
    result = {'duration_ms': 20000, 'candidates': [{'time_ms': 10000, 'scene_id': 0}]}
    enrich_candidates(Path('video.mp4'), Path('model.pt'), Path('pose.pt'), [50, 20], result,
                      Settings(_env_file=None, app_env='test', openai_api_key='test'),
                      gate_workers=2, activity_progress=lambda *event: activity.append(event))
    assert ('추가 후보 탐지', 0, 1) in activity
    assert ('추가 후보 탐지', 1, 1) in activity
    assert ('추가 슛 검증', 0, 1) in activity
    assert ('추가 슛 검증', 1, 1) in activity
