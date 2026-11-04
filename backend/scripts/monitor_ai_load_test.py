"""Read-only monitoring of the already running, user-started 247-candidate job."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from time import monotonic, sleep

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import create_database_engine, create_session_factory
from app.models import Analysis, AnalysisJob, Shot

ANALYSIS_ID = "f6680389-9556-4c53-8d0d-d67e3c4ad655"
JOB_ID = "4cbef17d-dcc3-4596-a927-faa422683656"
TERMINAL = {"succeeded", "failed", "canceled"}


def utc(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def display_bucket(shot):
    if shot.result.value not in ("made", "missed"):
        return "outcome_review"
    if shot.court_x is None or shot.court_y is None or not (0 <= shot.court_x <= 50 and 0 <= shot.court_y <= 47):
        return "position_review"
    return shot.result.value


def snapshot(factory, settings, observe_now):
    with factory() as db:
        job = db.get(AnalysisJob, JOB_ID)
        observed = observe_now()
        if job is None:
            return {"status": "not_found", "analysis_id": ANALYSIS_ID}
        result = {"analysis_id": ANALYSIS_ID, "job_id": JOB_ID,
                  "observed_utc": observed.isoformat(), "status": job.status.value,
                  "stage": job.stage, "processed_ms": job.processed_ms,
                  "started_at": utc(job.started_at).isoformat(),
                  "elapsed_seconds": round((observed - utc(job.started_at)).total_seconds(), 1),
                  "heartbeat_at": utc(job.heartbeat_at).isoformat() if job.heartbeat_at else None,
                  "heartbeat_age_seconds": round(max(0, (observed - utc(job.heartbeat_at)).total_seconds()), 1) if job.heartbeat_at else None,
                  "error_code": job.error_code}
        if job.status.value not in TERMINAL:
            return result
        result["finished_at"] = utc(job.finished_at).isoformat() if job.finished_at else None
        result["elapsed_seconds"] = round((utc(job.finished_at) - utc(job.started_at)).total_seconds(), 3) if job.finished_at else None
        analysis = db.get(Analysis, ANALYSIS_ID)
        shots = list(db.scalars(select(Shot).where(Shot.analysis_id == ANALYSIS_ID)))
        result["video"] = {"duration_ms": analysis.duration_ms, "file_size": analysis.file_size,
                           "width": analysis.width, "height": analysis.height, "fps": analysis.fps}
        result["saved_shots"] = len(shots)
        result["stored_result_counts"] = dict(Counter(shot.result.value for shot in shots))
        result["screen_buckets"] = dict(Counter(display_bucket(shot) for shot in shots))
        result["shot_status_counts"] = dict(Counter(shot.status.value for shot in shots))
        result["saved_ids_unique"] = len({shot.id for shot in shots}) == len(shots)
        result["position_missing"] = sum(shot.court_x is None or shot.court_y is None for shot in shots)
        result["saved_type_counts"] = dict(Counter(shot.shot_type.value for shot in shots))
        result["by_zone"] = dict(Counter(shot.zone_code or "unknown" for shot in shots))
    work = settings.work_path / ANALYSIS_ID
    checkpoint = work / f"local-candidates-{JOB_ID}.json"
    if checkpoint.is_file():
        local = json.loads(checkpoint.read_text(encoding="utf-8"))["result"]
        result["local_detected_candidates"] = len(local.get("candidates", []))
        result["local_motion_hints"] = len(local.get("motion_hints", []))
    final = work / "phase7-candidates.json"
    if final.is_file():
        data = json.loads(final.read_text(encoding="utf-8"))
        result["final_candidates"] = len(data.get("candidates", []))
        result["excluded_non_shot_count"] = len(data.get("excluded_non_shot_candidates", []))
        result["api_usage"] = data.get("api_usage")
        result["timing_seconds"] = data.get("timing_seconds")
        result["runtime_options"] = data.get("runtime_options")
        result["status_io"] = data.get("status_io")
        result["gate_summary"] = {k: v for k, v in data.get("shot_gate_summary", {}).items() if isinstance(v, (int, float, str))}
        result["ai_summary"] = {k: v for k, v in data.get("ai_review", {}).items() if isinstance(v, (int, float, str))}
        result["foot_refresh_count"] = data.get("foot_track_refresh_summary", {}).get("refreshed")
    return result


def main():
    # Pass a clock-tool timestamp to avoid the host's historical timezone offset.
    anchor = datetime.fromisoformat(sys.argv[1])
    started = monotonic()
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    readonly = engine.execution_options(postgresql_readonly=True)
    factory = create_session_factory(readonly)
    destination = settings.work_path / "large-upload-2026-11-04" / "ai-load-monitor.json"
    previous = None
    last_notice = 0
    try:
        while True:
            try:
                result = snapshot(factory, settings, lambda: datetime.fromtimestamp(anchor.timestamp() + monotonic() - started, timezone.utc))
                destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
                key = (result["status"], result.get("stage"), result.get("error_code"))
                if key != previous or monotonic() - last_notice >= 300:
                    print(json.dumps(result, ensure_ascii=True), flush=True)
                    previous, last_notice = key, monotonic()
                if result["status"] in TERMINAL or result["status"] == "not_found":
                    break
            except Exception as error:
                print(json.dumps({"monitor_error_type": type(error).__name__, "analysis_status": "unknown"}), flush=True)
            sleep(30)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
