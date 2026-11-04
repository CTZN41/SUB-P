"""Opt-in disk/HTTP benchmark; never connects to the service DB or calls AI.

Run from backend: python scripts/load_test_large_upload.py prepare --source PATH
Then: python scripts/load_test_large_upload.py measure
Generated fixtures live in ignored work/, not in Git. Padding measures file I/O,
not the decoder cost of an equally large iPhone HEVC stream.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LAB = ROOT / "work" / "large-upload-2026-11-04"
FIXTURE = LAB / "one-hour-1080p60-10GB-padded.mp4"
TARGET = 10_000_000_000  # Decimal GB, not 10 GiB.
PORT = 8011


def write_json(name, value):
    LAB.mkdir(parents=True, exist_ok=True)
    (LAB / name).write_text(json.dumps(value, indent=2), encoding="utf-8")


def emit(event, **values):
    print(json.dumps({"event": event, **values}), flush=True)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def metadata(path):
    import cv2
    c = cv2.VideoCapture(str(path))
    try:
        fps = c.get(cv2.CAP_PROP_FPS)
        return {"fps": fps, "frames": int(c.get(cv2.CAP_PROP_FRAME_COUNT)),
                "duration_s": c.get(cv2.CAP_PROP_FRAME_COUNT) / fps if fps else 0,
                "width": int(c.get(3)), "height": int(c.get(4))}
    finally:
        c.release()


def prepare(source):
    import imageio_ffmpeg
    import psutil
    LAB.mkdir(parents=True, exist_ok=True)
    if FIXTURE.exists():
        raise RuntimeError("Fixture already exists; inspect manifest before reuse")
    if shutil.disk_usage(LAB).free < 40_000_000_000:
        raise RuntimeError("Need at least 40 GB free for fixture, spool and upload")
    source = source.resolve(strict=True)
    initial = metadata(source)
    if initial["duration_s"] < 599 or initial["fps"] != 60:
        raise RuntimeError("Expected the specified ten-minute 60fps source")
    started = time.perf_counter()
    # Stream copy, no re-encoding, no model inference.
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-nostdin", "-n",
           "-threads", "1", "-stream_loop", "5", "-i", str(source), "-t", "3600",
           "-map", "0:v:0", "-map", "0:a?", "-c", "copy", str(FIXTURE)]
    with (LAB / "remux.log").open("wb") as log:
        subprocess.run(cmd, stdout=log, stderr=log, check=True)
    media_bytes = FIXTURE.stat().st_size
    padding = TARGET - media_bytes
    if padding < 16:
        raise RuntimeError("Remux exceeds target size")
    # A legal 64-bit ISO BMFF free box; write actual bytes, never truncate/seek.
    block = bytes(range(256)) * (4 * 1024 * 1024 // 256)
    with FIXTURE.open("ab") as stream:
        stream.write(struct.pack(">I4sQ", 1, b"free", padding))
        remaining = padding - 16
        pad_started = time.perf_counter()
        written = 0
        while remaining:
            count = min(remaining, len(block))
            stream.write(block[:count])
            written += count
            remaining -= count
            delay = written / (64 * 1024 * 1024) - (time.perf_counter() - pad_started)
            if delay > 0:
                time.sleep(delay)
        stream.flush()
        os.fsync(stream.fileno())
    result = {"source_name": source.name, "fixture_name": FIXTURE.name,
              "bytes": FIXTURE.stat().st_size, "remux_bytes": media_bytes,
              "free_box_bytes": padding, "sparse": False, "repetitions": 6,
              "metadata": metadata(FIXTURE), "sha256": digest(FIXTURE),
              "prepare_seconds": time.perf_counter() - started,
              "available_ram_after": psutil.virtual_memory().available,
              "limitation": "H264 repeated media plus free-box padding, not iPhone HEVC"}
    assert result["bytes"] == TARGET
    assert 3599 <= result["metadata"]["duration_s"] <= 3601
    write_json("fixture.json", result)
    emit("prepared", **result)


def serve(mode):
    import psutil
    import uvicorn
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.api.analyses import router
    from app.core.config import Settings
    from app.db.base import Base
    folder = LAB / mode
    folder.mkdir(parents=True, exist_ok=True)
    spool = folder / "spool"
    spool.mkdir(exist_ok=True)
    tempfile.tempdir = str(spool)
    settings = Settings(_env_file=None, app_env="test", auth_required=False,
        database_url=f"sqlite:///{(folder / 'test.db').as_posix()}",
        upload_dir=str(folder / "uploads"), work_dir=str(folder / "work"),
        openai_api_key=None, openai_shot_review_enabled=False,
        supabase_url=None, supabase_publishable_key=None,
        max_video_minutes=40 if mode in ("current-limits", "streaming-current-limits") else 65,
        max_video_bytes=5 * 1024**3 if mode in ("current-limits", "streaming-current-limits") else 12_000_000_000)
    settings.ensure_storage_directories()
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    # Instrument only this helper process. Imported production source is unchanged.
    from app.services import video_registration as registration
    timings = {}
    original_save = registration.save_upload

    async def timed_save(*args, **kwargs):
        tick = time.perf_counter()
        try:
            return await original_save(*args, **kwargs)
        finally:
            timings["copy_from_multipart_seconds"] = time.perf_counter() - tick
            write_json(f"{mode}-server-timings.json", timings)

    registration.save_upload = timed_save
    for name in ("probe_video", "ensure_browser_playback"):
        original = getattr(registration, name)

        def timed_call(*args, _original=original, _name=name, **kwargs):
            tick = time.perf_counter()
            try:
                return _original(*args, **kwargs)
            finally:
                timings[_name + "_seconds"] = time.perf_counter() - tick
                write_json(f"{mode}-server-timings.json", timings)

        setattr(registration, name, timed_call)
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = sessionmaker(bind=engine, autoflush=False)
    # Reuse real registration/retrieval routes only; no analysis-start routes.
    allowed = {("/api/analyses", "POST"), ("/api/analyses/{analysis_id}", "GET"),
               ("/api/analyses/{analysis_id}/video", "GET")}
    for route in router.routes:
        if any((route.path, method) in allowed for method in route.methods):
            app.router.routes.append(route)
    from app.api.uploads import router as uploads_router
    from app.services.upload_body_limit import UploadBodyLimitMiddleware
    from app.services.streaming_upload import recover_uploads
    app.include_router(uploads_router)
    app.add_middleware(UploadBodyLimitMiddleware)
    recover_uploads(settings)

    @app.get("/health")
    def health():
        return {"mode": mode, "pid": os.getpid(), "ai_enabled": False}

    try:
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except (AttributeError, psutil.Error):
        pass
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="error")


def folder_size(folder):
    total = 0
    for path in folder.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            pass
    return total


def run_case(mode):
    import httpx
    import psutil
    folder = LAB / mode
    if folder.exists():
        raise RuntimeError("Case exists; preserve evidence and choose a new run folder")
    url = f"http://127.0.0.1:{PORT}"
    # No inherited dotenv values, no remote DB. Hide the owned helper window.
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    log = (LAB / f"{mode}-server.log").open("wb")
    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "server", "--mode", mode],
                             cwd=ROOT, stdout=log, stderr=log, creationflags=flags)
    server_pid = None
    stop = threading.Event()
    samples = []
    pings = []
    thread = None
    result = {"mode": mode, "ai_calls": 0}
    try:
        with httpx.Client(trust_env=False, timeout=2) as health:
            for _ in range(120):
                if child.poll() is not None:
                    raise RuntimeError("Isolated helper failed; inspect local server log")
                try:
                    payload = health.get(url + "/health").json()
                    if payload.get("mode") == mode and not payload["ai_enabled"]:
                        server_pid = payload["pid"]
                        break
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(.5)
        if not server_pid:
            raise RuntimeError("Isolated helper not ready")
        proc = psutil.Process(server_pid)
        result["server_baseline_rss_bytes"] = proc.memory_info().rss

        def sample():
            with httpx.Client(trust_env=False, timeout=5) as client:
                while not stop.is_set():
                    try:
                        samples.append({"rss": proc.memory_info().rss,
                                        "client_rss": psutil.Process().memory_info().rss,
                                        "spool": folder_size(folder / "spool"),
                                        "upload": folder_size(folder / "uploads")})
                        tick = time.perf_counter()
                        response = client.get(url + "/health")
                        pings.append({"seconds": time.perf_counter() - tick,
                                      "status": response.status_code})
                    except httpx.HTTPError:
                        pings.append({"timeout": True})
                    except psutil.Error:
                        break
                    stop.wait(.5)
        thread = threading.Thread(target=sample, daemon=True)
        thread.start()
        started = time.perf_counter()
        with FIXTURE.open("rb") as stream, httpx.Client(trust_env=False, timeout=600) as client:
            if mode.startswith("streaming"):
                phase = time.perf_counter()
                response = client.post(url + "/api/uploads", json={
                    "title": "10GB streaming load test", "play_type": "solo",
                    "recorded_date": "2026-11-04", "contains_others": False,
                    "consent_confirmed": False, "ai_notice_confirmed": False,
                    "original_name": FIXTURE.name, "file_size": TARGET})
                result["phase_seconds"] = {"prepare": time.perf_counter() - phase}
                if response.status_code == 201:
                    upload_id = response.json()["id"]
                    phase = time.perf_counter()
                    transferred = client.put(url + f"/api/uploads/{upload_id}", content=stream,
                        headers={"Content-Type": "application/octet-stream", "Content-Length": str(TARGET)})
                    assert transferred.status_code == 200, transferred.status_code
                    result["phase_seconds"]["receive"] = time.perf_counter() - phase
                    phase = time.perf_counter()
                    response = client.post(url + f"/api/uploads/{upload_id}/complete")
                    assert response.status_code == 200, response.status_code
                    result["phase_seconds"]["finalize"] = time.perf_counter() - phase
            else:
                response = client.post(url + "/api/analyses",
                    data={"title": "10GB isolated load test", "play_type": "solo",
                          "recorded_date": "2026-11-04", "contains_others": "false",
                          "consent_confirmed": "false", "ai_notice_confirmed": "false"},
                    files={"video": (FIXTURE.name, stream, "video/mp4")})
        elapsed = time.perf_counter() - started
        transferred_bytes = 0 if mode == "streaming-current-limits" else TARGET
        result.update(http_status=response.status_code, upload_and_registration_seconds=elapsed,
                      bytes_transferred=transferred_bytes,
                      effective_MB_per_second=transferred_bytes / elapsed / 1_000_000 if transferred_bytes else None)
        payload = response.json()
        if mode in ("current-limits", "streaming-current-limits"):
            assert response.status_code == 413, response.status_code
            result["error_code"] = payload.get("detail", {}).get("code")
            assert result["error_code"] == "FILE_LIMIT"
            assert not list((folder / "uploads").iterdir())
            result["partial_upload_cleaned"] = True
        else:
            assert response.status_code == (200 if mode.startswith("streaming") else 201), response.status_code
            analysis_id = payload["id"]
            saved = folder / "uploads" / analysis_id / "original.mp4"
            result.update(analysis_id=analysis_id, saved_bytes=saved.stat().st_size,
                          duration_ms=payload["duration_ms"], width=payload["width"],
                          height=payload["height"], fps=payload["fps"])
            result["saved_sha256"] = digest(saved)
            manifest = json.loads((LAB / "fixture.json").read_text(encoding="utf-8"))
            assert result["saved_sha256"] == manifest["sha256"]
            result["hash_match"] = True
            result["range_checks"] = []
            with httpx.Client(trust_env=False, timeout=30) as client, FIXTURE.open("rb") as source:
                for offset in (0, TARGET // 2, TARGET - 65536):
                    start = time.perf_counter()
                    r = client.get(url + f"/api/analyses/{analysis_id}/video",
                                   headers={"Range": f"bytes={offset}-{offset + 65535}"})
                    source.seek(offset)
                    matches = r.content == source.read(65536)
                    assert r.status_code == 206 and matches
                    result["range_checks"].append({"offset": offset, "status": r.status_code,
                        "matches": matches, "content_range": r.headers.get("content-range"),
                        "seconds": time.perf_counter() - start})
            import cv2
            capture = cv2.VideoCapture(str(saved))
            capture.set(cv2.CAP_PROP_N_THREADS, 1)
            result["decode_bursts"] = []
            try:
                for seconds in (0, 900, 1800, 2700, 3598):
                    tick = time.perf_counter()
                    capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
                    read = 0
                    for _ in range(30):
                        ok, frame = capture.read()
                        if not ok:
                            break
                        assert frame.shape[:2] == (1080, 1920)
                        read += 1
                    result["decode_bursts"].append({"time_s": seconds, "frames_read": read,
                                                    "seconds": time.perf_counter() - tick})
                    assert read == 30
            finally:
                capture.release()
            result["metadata"] = metadata(saved)
        stop.set()
        thread.join(timeout=8)
        result.update(peak_server_rss_bytes=max(s["rss"] for s in samples),
            peak_client_rss_bytes=max(s["client_rss"] for s in samples),
            peak_combined_rss_bytes=max(s["rss"] + s["client_rss"] for s in samples),
            peak_spool_bytes=max(s["spool"] for s in samples),
            peak_upload_bytes=max(s["upload"] for s in samples),
            peak_spool_plus_upload_bytes=max(s["spool"] + s["upload"] for s in samples),
            spool_remaining_bytes=folder_size(folder / "spool"),
            health_samples=len(pings), health_timeouts=sum(bool(p.get("timeout")) for p in pings),
            max_health_seconds=max((p.get("seconds", 0) for p in pings), default=0))
        timing_path = LAB / f"{mode}-server-timings.json"
        result["server_timings"] = json.loads(timing_path.read_text()) if timing_path.exists() else {}
        result["peak_disk_including_fixture_bytes"] = TARGET + result["peak_spool_plus_upload_bytes"]
        write_json(f"{mode}.json", result)
        emit("case_completed", **result)
        return result
    finally:
        stop.set()
        if thread:
            thread.join(timeout=8)
        # Only the helper this function spawned, never production PID/port 8000.
        parent = psutil.Process(child.pid) if child.poll() is None else None
        if parent:
            owned = parent.children(recursive=True)
            for process in reversed(owned):
                try:
                    process.terminate()
                except psutil.Error:
                    pass
            parent.terminate()
        child.wait(timeout=20)
        log.close()


def measure():
    import socket
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", PORT)) == 0:
            raise RuntimeError("Port occupied; do not touch the existing process")
    assert FIXTURE.stat().st_size == TARGET
    assert (LAB / "fixture.json").is_file()
    results = [run_case("current-limits"), run_case("expanded-test-only")]
    write_json("summary.json", {"fixture": json.loads((LAB / "fixture.json").read_text()),
                                "cases": results, "production_settings_changed": False,
                                "full_yolo_or_ai_tested": False})


def compare():
    import socket
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", PORT)) == 0:
            raise RuntimeError("Port occupied; preserve the running process")
    assert FIXTURE.stat().st_size == TARGET
    modes = ("streaming-current-limits", "legacy-repeat-1", "streaming-expanded-1", "streaming-expanded-2")
    results = [run_case(mode) for mode in modes]
    write_json("streaming-comparison.json", {"fixture": json.loads((LAB / "fixture.json").read_text()),
        "original_baseline": json.loads((LAB / "summary.json").read_text())["cases"][1], "cases": results})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "measure", "compare", "server"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--mode", choices=("current-limits", "expanded-test-only", "streaming-current-limits", "legacy-repeat-1", "streaming-expanded-1", "streaming-expanded-2"))
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.source)
    elif args.action == "measure":
        measure()
    elif args.action == "compare":
        compare()
    else:
        serve(args.mode)
