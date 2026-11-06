import os
import subprocess
import tempfile
import pytest
from PySide6.QtWidgets import QApplication

from src.core.cache_manager import ScanCacheManager
from src.core.clip_manifest import ClipManifest, ClipEntry
from src.ui.director.workers.source_analysis_worker import (
    SourceAnalysisConfig,
    SourceAnalysisWorker,
)
from src.ui.director.workers.kinetic_preparation_worker import (
    KineticPreparationRequest,
    KineticPreparationWorker,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _create_noaudio_mp4(path: str, duration_sec: float = 1.0) -> None:
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"color=c=green:s=320x240:d={duration_sec}:r=24",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_source_noaudio_saves_verified_v3_cache_and_satisfies_v2(qapp, tmp_path) -> None:
    video_path = str(tmp_path / "clip_no_audio.mp4")
    _create_noaudio_mp4(video_path, duration_sec=1.0)

    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    cfg = SourceAnalysisConfig(
        job_id="test_noaudio_job",
        video_paths=(video_path,),
        whisper_model="small",
        whisper_language="vi",
        enable_audio=True,
        enable_visual_sampling=False,
    )

    worker = SourceAnalysisWorker(
        config=cfg,
        cache_mgr=cache_mgr,
    )

    completed_events = []
    status_events = []

    worker.finished_signal.connect(lambda jid, success, msg, counters: completed_events.append((jid, success, msg, counters)))
    worker.status_signal.connect(lambda jid, vp, status: status_events.append((vp, status)))

    worker.run()

    assert len(completed_events) == 1
    _, success, msg, counters = completed_events[0]
    assert success is True
    assert len(status_events) == 1
    _, res = status_events[0]
    assert res["audio_status"] == "no_audio_track"
    assert res["raw_subtitles"] == []
    assert res["cache_saved"] is True
    assert res["persisted"] is True
    assert 0.8 <= res["clip_dur"] <= 1.2

    # 1. Tra cứu trực tiếp v3 cache với verify_full=True
    cached_entry = cache_mgr.get_cached_scan(video_path, "small", "vi", verify_full=True)
    assert cached_entry is not None
    assert cached_entry.get("no_audio_track") is True or cached_entry.get("no_audio") is True
    assert cached_entry.get("raw_subtitles") == []
    assert 0.8 <= cached_entry.get("clip_dur", 0.0) <= 1.2

    # 2. Replay: Chạy lại worker phiên thứ 2 trên cùng file
    replay_completed = []
    replay_status = []
    worker_replay = SourceAnalysisWorker(
        config=cfg,
        cache_mgr=cache_mgr,
    )
    worker_replay.finished_signal.connect(lambda jid, success, msg, counters: replay_completed.append((jid, success, msg, counters)))
    worker_replay.status_signal.connect(lambda jid, vp, status: replay_status.append((vp, status)))
    worker_replay.run()

    assert len(replay_completed) == 1
    _, r_success, r_msg, r_counters = replay_completed[0]
    assert r_success is True
    assert len(replay_status) == 1
    _, r_res = replay_status[0]
    assert r_res["audio_status"] == "no_audio_track"
    assert r_res["raw_subtitles"] == []
    assert r_res["cache_saved"] is True
    assert r_res["persisted"] is True

    # Counters partition trên replay: 1 no_audio_track, 0 new, 0 model load
    c_audio = r_counters["audio"]
    assert c_audio["no_audio_track"] == 1
    assert c_audio["new"] == 0
    total_audio = c_audio["cached"] + c_audio["new"] + c_audio["no_audio_track"] + c_audio["failed"] + c_audio["unavailable"] + c_audio["cancelled"] + c_audio["skipped"]
    assert total_audio == 1

    # 3. Chuyển giao sang KineticPreparationWorker (V2): KHÔNG bị not_analyzed!
    prep_req = KineticPreparationRequest(
        request_id="req_noaudio",
        generation=1,
        intent="copy_kinetic",
        context_primitives={
            "video_paths": [video_path],
            "whisper_model": "small",
            "whisper_language": "vi",
            "project_name": "test_proj",
        },
        timeline_fps=24,
    )

    prep_worker = KineticPreparationWorker(
        request=prep_req,
        cache_mgr=cache_mgr,
    )

    prep_terminal = []
    prep_worker.finished_signal.connect(lambda r, s, m, res: prep_terminal.append((s, m, res)))
    prep_worker.run()

    assert len(prep_terminal) == 1
    success, kind, payload = prep_terminal[0]
    # KHÔNG được ném not_analyzed, phải trả về no_dialogue vì clip hoàn toàn không có thoại
    assert kind != "not_analyzed"
    assert success is False
    assert kind == "no_dialogue"
    assert payload.get("words") == []


def test_source_noaudio_invalid_duration_refuses_cache_save(qapp, tmp_path) -> None:
    video_path = str(tmp_path / "clip_invalid_dur.mp4")
    _create_noaudio_mp4(video_path, duration_sec=1.0)

    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    cfg = SourceAnalysisConfig(
        job_id="test_invalid_dur",
        video_paths=(video_path,),
        enable_audio=True,
        enable_visual_sampling=False,
    )

    worker = SourceAnalysisWorker(
        config=cfg,
        cache_mgr=cache_mgr,
        audio_probe_fn=lambda vp: False,  # probe báo không audio
    )

    # Monkeypatch hàm lấy duration trả về <= 0
    worker._duration_fn = lambda vp: 0.0

    completed_events = []
    status_events = []
    worker.finished_signal.connect(lambda jid, success, msg, counters: completed_events.append((jid, success, msg, counters)))
    worker.status_signal.connect(lambda jid, vp, status: status_events.append((vp, status)))
    worker.run()

    assert len(completed_events) == 1
    assert len(status_events) == 1
    _, res = status_events[0]
    assert res["audio_status"] == "failed"
    assert res["cache_saved"] is False
    assert res["persisted"] is False

    # Cache manager không được lưu
    cached_entry = cache_mgr.get_cached_scan(video_path, "small", "vi", verify_full=True)
    assert cached_entry is None


def test_source_noaudio_cancelled_before_save(qapp, tmp_path) -> None:
    video_path = str(tmp_path / "clip_cancel.mp4")
    _create_noaudio_mp4(video_path, duration_sec=1.0)

    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    cfg = SourceAnalysisConfig(
        job_id="test_cancel",
        video_paths=(video_path,),
        enable_audio=True,
        enable_visual_sampling=False,
    )

    worker = SourceAnalysisWorker(
        config=cfg,
        cache_mgr=cache_mgr,
        audio_probe_fn=lambda vp: False,
    )

    # Hủy worker trước khi chạy
    worker.cancel()

    completed_events = []
    worker.finished_signal.connect(lambda jid, success, msg, counters: completed_events.append((jid, success, msg, counters)))
    worker.run()

    # Worker đã bị hủy, không có cache nào được lưu
    cached_entry = cache_mgr.get_cached_scan(video_path, "small", "vi", verify_full=True)
    assert cached_entry is None
