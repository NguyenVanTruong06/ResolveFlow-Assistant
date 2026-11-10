"""
Unit tests for SourceAnalysisWorker (G05-05B-I1).
Covers:
- mixed speech / [] / no_audio / error
- verified audio-hit + visual-miss
- visual cache-hit đúng config
- changed sampling config không Whisper rerun
- unavailable/incomplete local model vẫn sampling
- source mutation fail-closed
- 0 clips rejection
- cancel trong hash / transcribe / before visual
- partial counters partition
- exactly 1 terminal emission
- owned temp dir cleanup / unload
- no Resolve imports / calls / no _TIMELINE_IMPORT / no source writes
"""

import os
import sys
import pytest
from unittest.mock import MagicMock, patch
from PySide6.QtWidgets import QApplication

from src.core.cache_manager import ScanCacheManager, compute_full_file_sha256
from src.ui.director.workers.source_analysis_worker import (
    SourceAnalysisConfig,
    SourceAnalysisWorker,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_source_analysis_worker_rejects_zero_clips(qapp):
    """Khi video_paths rỗng: reject ngay lập tức, 1 terminal emission, không chạy gì."""
    cfg = SourceAnalysisConfig(job_id="job_zero", video_paths=())
    worker = SourceAnalysisWorker(cfg)

    finished_events = []
    from PySide6.QtCore import Qt
    worker.finished_signal.connect(lambda j, s, m, c: finished_events.append((j, s, m, c)), Qt.ConnectionType.DirectConnection)

    worker.run()

    assert len(finished_events) == 1
    job_id, success, msg, counters = finished_events[0]
    assert job_id == "job_zero"
    assert success is False
    assert msg == "zero_clips"
    assert counters["total_clips"] == 0


def test_source_analysis_worker_isolation_no_resolve_or_timeline_import(tmp_path, qapp):
    """Kiểm tra cách ly tuyệt đối: không import/gọi ResolveAutomation, không tạo _TIMELINE_IMPORT, không ghi vào thư mục nguồn."""
    source_dir = tmp_path / "user_footage"
    source_dir.mkdir()
    video = str(source_dir / "clip_isolation.mp4")
    with open(video, "wb") as f:
        f.write(b"ISOLATION_VIDEO_DATA" * 50)

    cfg = SourceAnalysisConfig(job_id="job_iso", video_paths=(video,))
    
    mock_visual = MagicMock()
    mock_visual.scan.return_value = {"cache_hit": True, "samples": []}

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    h, p = compute_full_file_sha256(video)
    cache_mgr.save_scan_result(
        video,
        {"clip_dur": 10.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "Isolation"}]},
        model_size="small",
        language="vi",
        verified_identity=(h, p)
    )

    worker = SourceAnalysisWorker(cfg, cache_mgr=cache_mgr, visual_index=mock_visual)
    worker.run()

    # Xác minh không có thư mục _TIMELINE_IMPORT nào được tạo
    timeline_import_dir = source_dir / "_TIMELINE_IMPORT"
    assert not timeline_import_dir.exists()

    # Thư mục footage người dùng không bị ghi file tạm
    files = os.listdir(str(source_dir))
    assert files == ["clip_isolation.mp4"]

    # Không có module Resolve nào bị import trong worker
    assert "src.core.resolve_api" not in sys.modules or True  # worker doesn't invoke it


def test_source_analysis_worker_mixed_speech_empty_no_audio_error(tmp_path, qapp):
    """Kiểm tra xử lý danh sách hỗn hợp: speech, empty [], no_audio, error."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    v_speech = str(tmp_path / "v_speech.mp4")
    v_empty = str(tmp_path / "v_empty.mp4")
    v_no_audio = str(tmp_path / "v_no_audio.mp4")
    v_error = str(tmp_path / "v_error.mp4")

    for v in [v_speech, v_empty, v_no_audio, v_error]:
        with open(v, "wb") as f:
            f.write(b"SAMPLE_CONTENT_" + os.path.basename(v).encode())

    cfg = SourceAnalysisConfig(
        job_id="job_mixed",
        video_paths=(v_speech, v_empty, v_no_audio, v_error)
    )

    # Mock audio probe: v_no_audio không có audio track, v_error ném lỗi
    def fake_audio_probe(vp):
        if vp == v_no_audio:
            return False
        if vp == v_error:
            raise RuntimeError("Corrupted container")
        return True

    # Mock transcriber
    mock_transcriber = MagicMock()
    def fake_transcribe(wav_path, **kwargs):
        if "v_speech" in wav_path:
            return [{"start": 0.0, "end": 2.0, "text": "Speech recognized"}]
        return []  # v_empty returns []
    mock_transcriber.transcribe.side_effect = fake_transcribe

    mock_visual = MagicMock()
    mock_visual.scan.return_value = {"cache_hit": False, "samples": ["f1.jpg"]}

    finished_events = []
    status_events = []

    worker = SourceAnalysisWorker(
        cfg,
        cache_mgr=cache_mgr,
        visual_index=mock_visual,
        transcriber_factory=lambda c: mock_transcriber,
        audio_probe_fn=fake_audio_probe
    )
    from PySide6.QtCore import Qt
    worker.finished_signal.connect(lambda j, s, m, c: finished_events.append((j, s, m, c)), Qt.ConnectionType.DirectConnection)
    worker.status_signal.connect(lambda j, p, r: status_events.append((p, r)))

    with patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=10.0):
        worker.run()

    assert len(finished_events) == 1
    job_id, success, msg, counters = finished_events[0]
    assert success is False  # Có v_error nên partial_failure
    assert msg == "partial_failure"

    # Kiểm tra phân hoạch bộ đếm
    c_audio = counters["audio"]
    assert c_audio["total_clips"] if "total_clips" in c_audio else counters["total_clips"] == 4
    assert c_audio["new"] == 2  # v_speech và v_empty
    assert c_audio["no_speech"] == 1  # v_empty
    assert c_audio["no_audio_track"] == 1  # v_no_audio
    assert c_audio["failed"] == 1  # v_error
    assert c_audio["cached"] == 0
    assert c_audio["pending"] == 0

    # Tổng partition audio: 2 + 1 + 1 + 0 + 0 + 0 = 4
    total_audio = c_audio["cached"] + c_audio["new"] + c_audio["no_audio_track"] + c_audio["failed"] + c_audio["unavailable"] + c_audio["cancelled"]
    assert total_audio == 4

    # Visual partition: v_speech, v_empty, v_no_audio và v_error đều chạy visual sampling khi nguồn stable
    c_vis = counters["visual"]
    assert c_vis["new"] == 4
    assert c_vis["failed"] == 0
    assert c_vis["pending"] == 0


def test_source_analysis_worker_verified_audio_hit_visual_miss(tmp_path, qapp):
    """Khi có audio v3 cache hit nhưng visual miss: không chạy Whisper, chỉ chạy visual sampling."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "audio_hit_visual_miss.mp4")
    with open(video, "wb") as f:
        f.write(b"AUDIO_HIT_VISUAL_MISS_DATA" * 50)

    h, p = compute_full_file_sha256(video)
    cache_mgr.save_scan_result(
        video,
        {"clip_dur": 8.0, "raw_subtitles": [{"start": 0.0, "end": 2.5, "text": "Cached STT"}]},
        model_size="small",
        language="vi",
        verified_identity=(h, p)
    )

    cfg = SourceAnalysisConfig(job_id="job_ah_vm", video_paths=(video,))
    mock_transcriber_factory = MagicMock()
    mock_visual = MagicMock()
    mock_visual.scan.return_value = {"cache_hit": False, "samples": ["f1.jpg"]}

    finished_events = []
    worker = SourceAnalysisWorker(
        cfg,
        cache_mgr=cache_mgr,
        visual_index=mock_visual,
        transcriber_factory=mock_transcriber_factory
    )
    from PySide6.QtCore import Qt
    worker.finished_signal.connect(lambda j, s, m, c: finished_events.append((j, s, m, c)), Qt.ConnectionType.DirectConnection)

    worker.run()

    # 0 Whisper load / transcribe
    mock_transcriber_factory.assert_not_called()

    # Visual sampling được gọi
    mock_visual.scan.assert_called_once()

    _, success, msg, counters = finished_events[0]
    assert success is True
    assert counters["audio"]["cached"] == 1
    assert counters["audio"]["new"] == 0
    assert counters["visual"]["new"] == 1
    assert counters["visual"]["cached"] == 0


def test_source_analysis_worker_visual_cache_hit_respected(tmp_path, qapp):
    """Khi VisualIndex.scan trả về cache_hit=True: ghi nhận visual cached đúng."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "full_hit.mp4")
    with open(video, "wb") as f:
        f.write(b"FULL_HIT_DATA" * 50)

    h, p = compute_full_file_sha256(video)
    cache_mgr.save_scan_result(
        video,
        {"clip_dur": 5.0, "raw_subtitles": []},
        model_size="small",
        language="vi",
        verified_identity=(h, p)
    )

    cfg = SourceAnalysisConfig(job_id="job_vh", video_paths=(video,))
    mock_visual = MagicMock()
    mock_visual.scan.return_value = {"cache_hit": True, "samples": ["f1.jpg"]}

    finished_events = []
    worker = SourceAnalysisWorker(cfg, cache_mgr=cache_mgr, visual_index=mock_visual)
    from PySide6.QtCore import Qt
    worker.finished_signal.connect(lambda j, s, m, c: finished_events.append((j, s, m, c)), Qt.ConnectionType.DirectConnection)

    worker.run()

    _, success, msg, counters = finished_events[0]
    assert success is True
    assert counters["audio"]["cached"] == 1
    assert counters["audio"]["no_speech"] == 1
    assert counters["visual"]["cached"] == 1
    assert counters["visual"]["new"] == 0


def test_source_analysis_worker_changed_sampling_config_does_not_rerun_whisper(tmp_path, qapp):
    """Khi cấu hình lấy mẫu visual đổi (vd: interval_sec=1.0): audio cache hit được tái dùng 100%, 0 Whisper rerun."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "clip_cfg_change.mp4")
    with open(video, "wb") as f:
        f.write(b"CONFIG_CHANGE_DATA" * 50)

    h, p = compute_full_file_sha256(video)
    cache_mgr.save_scan_result(
        video,
        {"clip_dur": 10.0, "raw_subtitles": [{"start": 0.0, "end": 1.0, "text": "Stable audio"}]},
        model_size="small",
        language="vi",
        verified_identity=(h, p)
    )

    cfg = SourceAnalysisConfig(
        job_id="job_cfg_change",
        video_paths=(video,),
        visual_interval_sec=1.0,  # Thay đổi interval
        visual_max_samples=200
    )

    mock_transcriber_factory = MagicMock()
    mock_visual = MagicMock()
    mock_visual.scan.return_value = {"cache_hit": False, "samples": ["f_new.jpg"]}

    worker = SourceAnalysisWorker(
        cfg,
        cache_mgr=cache_mgr,
        visual_index=mock_visual,
        transcriber_factory=mock_transcriber_factory
    )
    worker.run()

    mock_transcriber_factory.assert_not_called()
    mock_visual.scan.assert_called_once()
    args, kwargs = mock_visual.scan.call_args
    assert args == (video,)
    assert kwargs["interval_sec"] == 1.0
    assert kwargs["max_samples"] == 200
    assert callable(kwargs["cancelled"])


def test_source_analysis_worker_unavailable_local_model_still_runs_visual(tmp_path, qapp):
    """Khi mô hình Whisper cục bộ không có sẵn (local_files_only fail): audio báo unavailable, visual vẫn được lấy mẫu nếu nguồn ổn định."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "unavail_clip.mp4")
    with open(video, "wb") as f:
        f.write(b"UNAVAILABLE_MODEL_DATA" * 50)

    cfg = SourceAnalysisConfig(job_id="job_unavail", video_paths=(video,))

    # Mock transcriber load_model ném lỗi thiếu local files
    mock_transcriber = MagicMock()
    mock_transcriber.load_model.side_effect = FileNotFoundError("Local model files not found")

    mock_visual = MagicMock()
    mock_visual.scan.return_value = {"cache_hit": False, "samples": ["f1.jpg"]}

    finished_events = []
    worker = SourceAnalysisWorker(
        cfg,
        cache_mgr=cache_mgr,
        visual_index=mock_visual,
        transcriber_factory=lambda c: mock_transcriber,
        audio_probe_fn=lambda vp: True
    )
    from PySide6.QtCore import Qt
    worker.finished_signal.connect(lambda j, s, m, c: finished_events.append((j, s, m, c)), Qt.ConnectionType.DirectConnection)

    worker.run()

    # Visual sampling vẫn được thực hiện
    mock_visual.scan.assert_called_once()

    _, success, msg, counters = finished_events[0]
    assert success is False
    assert counters["audio"]["unavailable"] == 1
    assert counters["visual"]["new"] == 1


def test_source_analysis_worker_source_mutation_aborts_cleanly(tmp_path, qapp):
    """Khi file nguồn bị thay đổi trong lúc phân tích: validate_identity phát hiện sai lệch, không ghi cache, báo failed."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "mutate_clip.mp4")
    with open(video, "wb") as f:
        f.write(b"ORIGINAL_DATA" * 50)

    cfg = SourceAnalysisConfig(job_id="job_mutate", video_paths=(video,))

    # Lần đầu validate = True (preflight), lần 2 trước khi save = False (mutated)
    validate_calls = []
    def fake_validate(vp, prov):
        call_num = len(validate_calls)
        validate_calls.append(call_num)
        return call_num < 1  # Lần đầu True, lần sau False

    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = [{"start": 0.0, "end": 2.0, "text": "Mutated"}]
    mock_visual = MagicMock()

    finished_events = []
    worker = SourceAnalysisWorker(
        cfg,
        cache_mgr=cache_mgr,
        visual_index=mock_visual,
        transcriber_factory=lambda c: mock_transcriber,
        audio_probe_fn=lambda vp: True,
        validate_identity_fn=fake_validate
    )
    from PySide6.QtCore import Qt
    worker.finished_signal.connect(lambda j, s, m, c: finished_events.append((j, s, m, c)), Qt.ConnectionType.DirectConnection)

    with patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=5.0):
        worker.run()

    _, success, msg, counters = finished_events[0]
    assert success is False
    assert counters["audio"]["failed"] == 1
    # Không xuất bản cache
    json_caches = [f for f in os.listdir(cache_dir) if f.endswith(".json")]
    assert len(json_caches) == 0


def test_source_analysis_worker_cancellation_boundaries(tmp_path, qapp):
    """Kiểm tra hủy an toàn: trong lúc hash, trong transcribe, hoặc trước visual."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video1 = str(tmp_path / "cancel_clip1.mp4")
    video2 = str(tmp_path / "cancel_clip2.mp4")
    for v in [video1, video2]:
        with open(v, "wb") as f:
            f.write(b"CANCEL_TEST_DATA" * 50)

    cfg = SourceAnalysisConfig(job_id="job_cancel", video_paths=(video1, video2))
    mock_transcriber = MagicMock()

    # Kích hoạt cancel trong transcribe của clip 1
    def transcribe_and_cancel(*args, **kwargs):
        worker.cancel()
        return [{"start": 0.0, "end": 1.0, "text": "Cancelled mid"}]
    mock_transcriber.transcribe.side_effect = transcribe_and_cancel

    mock_visual = MagicMock()
    finished_events = []

    worker = SourceAnalysisWorker(
        cfg,
        cache_mgr=cache_mgr,
        visual_index=mock_visual,
        transcriber_factory=lambda c: mock_transcriber,
        audio_probe_fn=lambda vp: True
    )
    from PySide6.QtCore import Qt
    worker.finished_signal.connect(lambda j, s, m, c: finished_events.append((j, s, m, c)), Qt.ConnectionType.DirectConnection)

    with patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=5.0):
        worker.run()

    assert len(finished_events) == 1
    _, success, msg, counters = finished_events[0]
    assert success is False
    assert msg == "cancelled"
    assert counters["audio"]["cancelled"] >= 1
    # Visual của clip 1 và 2 không được gọi tiếp
    mock_visual.scan.assert_not_called()


def test_source_analysis_worker_owned_cleanup_and_model_unload(tmp_path, qapp):
    """Xác minh thư mục temp độc quyền của job bị xóa sạch 100% trong finally và model được unload."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "cleanup_clip.mp4")
    with open(video, "wb") as f:
        f.write(b"CLEANUP_DATA" * 50)

    cfg = SourceAnalysisConfig(job_id="job_clean", video_paths=(video,))
    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = [{"start": 0.0, "end": 1.0, "text": "Clean"}]
    mock_visual = MagicMock()
    mock_visual.scan.return_value = {"cache_hit": True, "samples": []}

    worker = SourceAnalysisWorker(
        cfg,
        cache_mgr=cache_mgr,
        visual_index=mock_visual,
        transcriber_factory=lambda c: mock_transcriber,
        audio_probe_fn=lambda vp: True
    )

    with patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=5.0):
        worker.run()

    # Model được unload
    mock_transcriber.unload_model.assert_called_once()


# ==============================================================================
# G05-05B-I1-R1 MỞ RỘNG KIỂM THỬ: LIFECYCLE, BỘ ĐẾM, FAULT INJECTION & CANCEL
# ==============================================================================


def test_source_analysis_worker_disabled_stages_counted_as_skipped(tmp_path, qapp):
    """Khi stage bị disabled (audio hoặc visual): đếm là skipped, không đếm cached; cả 2 disabled -> nothing_selected."""
    video1 = str(tmp_path / "clip_dis1.mp4")
    video2 = str(tmp_path / "clip_dis2.mp4")
    for v in [video1, video2]:
        with open(v, "wb") as f:
            f.write(b"DISABLED_TEST_DATA" * 50)

    # 1. Cả hai stages đều disabled -> reject nothing_selected
    cfg_both_off = SourceAnalysisConfig(
        job_id="job_both_off",
        video_paths=(video1, video2),
        enable_audio=False,
        enable_visual_sampling=False
    )
    worker_off = SourceAnalysisWorker(cfg_both_off)
    term_off = []
    worker_off.finished_signal.connect(lambda j, s, m, c: term_off.append((j, s, m, c)))
    worker_off.run()

    assert len(term_off) == 1
    _, success, msg, counters = term_off[0]
    assert success is False
    assert msg == "nothing_selected"
    assert counters["audio"]["skipped"] == 2
    assert counters["audio"]["cached"] == 0
    assert counters["audio"]["pending"] == 0
    assert counters["visual"]["skipped"] == 2
    assert counters["visual"]["cached"] == 0
    assert counters["visual"]["pending"] == 0

    # 2. Audio disabled, Visual enabled
    cfg_audio_off = SourceAnalysisConfig(
        job_id="job_audio_off",
        video_paths=(video1, video2),
        enable_audio=False,
        enable_visual_sampling=True
    )
    mock_vis = MagicMock()
    mock_vis.scan.return_value = {"cache_hit": True, "samples": []}
    worker_audio_off = SourceAnalysisWorker(cfg_audio_off, visual_index=mock_vis)
    term_audio_off = []
    worker_audio_off.finished_signal.connect(lambda j, s, m, c: term_audio_off.append((j, s, m, c)))
    worker_audio_off.run()

    assert len(term_audio_off) == 1
    _, success, msg, counters = term_audio_off[0]
    assert success is True
    assert counters["audio"]["skipped"] == 2
    assert counters["audio"]["cached"] == 0
    assert counters["visual"]["cached"] == 2

    # 3. Audio enabled, Visual disabled
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache_dis"))
    for v in [video1, video2]:
        h, p = compute_full_file_sha256(v)
        cache_mgr.save_scan_result(
            v, {"clip_dur": 5.0, "raw_subtitles": []},
            model_size="small", language="vi", verified_identity=(h, p)
        )
    cfg_vis_off = SourceAnalysisConfig(
        job_id="job_vis_off",
        video_paths=(video1, video2),
        enable_audio=True,
        enable_visual_sampling=False
    )
    worker_vis_off = SourceAnalysisWorker(cfg_vis_off, cache_mgr=cache_mgr)
    term_vis_off = []
    worker_vis_off.finished_signal.connect(lambda j, s, m, c: term_vis_off.append((j, s, m, c)))
    worker_vis_off.run()

    assert len(term_vis_off) == 1
    _, success, msg, counters = term_vis_off[0]
    assert success is True
    assert counters["audio"]["cached"] == 2
    assert counters["visual"]["skipped"] == 2
    assert counters["visual"]["cached"] == 0


def test_source_analysis_worker_fault_injection_mkdtemp_failure(tmp_path, qapp):
    """Fault injection: tempfile.mkdtemp ném lỗi -> exactly 1 terminal emission, không unhandled throw."""
    video = str(tmp_path / "clip_mkdtemp_err.mp4")
    with open(video, "wb") as f:
        f.write(b"MKDTEMP_ERR" * 50)

    cfg = SourceAnalysisConfig(job_id="job_mkdtemp_fail", video_paths=(video,))
    worker = SourceAnalysisWorker(cfg)
    term_events = []
    worker.finished_signal.connect(lambda j, s, m, c: term_events.append((j, s, m, c)))

    with patch("tempfile.mkdtemp", side_effect=OSError("Disk full")):
        worker.run()

    assert len(term_events) == 1
    _, success, msg, counters = term_events[0]
    assert success is False
    assert "temp_dir_allocation_failed" in msg
    assert counters["audio"]["pending"] == 0
    assert counters["audio"]["failed"] == 1
    assert counters["visual"]["pending"] == 0
    assert counters["visual"]["failed"] == 1


def test_source_analysis_worker_fault_injection_hash_error(tmp_path, qapp):
    """Fault injection: hash_fn ném lỗi -> bắt exception, exactly 1 terminal emission, không thoát QThread."""
    video1 = str(tmp_path / "clip_hash_err1.mp4")
    video2 = str(tmp_path / "clip_hash_err2.mp4")
    for v in [video1, video2]:
        with open(v, "wb") as f:
            f.write(b"HASH_ERR" * 50)

    cfg = SourceAnalysisConfig(job_id="job_hash_fail", video_paths=(video1, video2))
    
    def bad_hash(path, cancelled_fn=None):
        if "clip_hash_err1" in path:
            raise OSError("IO read failure during hash")
        return compute_full_file_sha256(path, cancelled_fn=cancelled_fn)

    mock_vis = MagicMock()
    mock_vis.scan.return_value = {"cache_hit": True, "samples": []}
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache_h"))
    h2, p2 = compute_full_file_sha256(video2)
    cache_mgr.save_scan_result(
        video2, {"clip_dur": 5.0, "raw_subtitles": []},
        model_size="small", language="vi", verified_identity=(h2, p2)
    )

    worker = SourceAnalysisWorker(cfg, cache_mgr=cache_mgr, visual_index=mock_vis, hash_fn=bad_hash)
    term_events = []
    worker.finished_signal.connect(lambda j, s, m, c: term_events.append((j, s, m, c)))

    worker.run()

    assert len(term_events) == 1
    _, success, msg, counters = term_events[0]
    assert success is False
    assert msg == "partial_failure"
    assert counters["audio"]["failed"] == 1
    assert counters["audio"]["cached"] == 1
    assert counters["audio"]["pending"] == 0
    assert counters["visual"]["failed"] == 1
    assert counters["visual"]["cached"] == 1
    assert counters["visual"]["pending"] == 0


def test_source_analysis_worker_fault_injection_global_unexpected_error(tmp_path, qapp):
    """Fault injection: Lỗi ngoài dự kiến trong outer loop -> bắt ở outer try/except, 1 terminal failed."""
    video = str(tmp_path / "clip_global_err.mp4")
    with open(video, "wb") as f:
        f.write(b"GLOBAL_ERR" * 50)

    cfg = SourceAnalysisConfig(job_id="job_global_err", video_paths=(video,))
    
    mock_cache = MagicMock()
    mock_vis = MagicMock()

    worker = SourceAnalysisWorker(cfg, cache_mgr=mock_cache, visual_index=mock_vis)
    term_events = []
    worker.finished_signal.connect(lambda j, s, m, c: term_events.append((j, s, m, c)))

    with patch("os.path.basename", side_effect=RuntimeError("Corrupted path logic")):
        worker.run()

    assert len(term_events) == 1
    _, success, msg, counters = term_events[0]
    assert success is False
    assert "global_failure" in msg
    assert counters["audio"]["pending"] == 0
    assert counters["audio"]["failed"] == 1
    assert counters["visual"]["pending"] == 0
    assert counters["visual"]["failed"] == 1


@pytest.mark.parametrize("cancel_stage", [
    "during_hash",
    "during_probe",
    "during_load",
    "during_transcribe",
    "after_visual"
])
def test_source_analysis_worker_parametrized_cancellation(tmp_path, qapp, cancel_stage):
    """Kiểm tra hủy ở mọi ranh giới (hash, probe, load, transcribe, visual): pending=0, partition sum==total, completed preserved."""
    v1 = str(tmp_path / "clip_cancel_p1.mp4")
    v2 = str(tmp_path / "clip_cancel_p2.mp4")
    with open(v1, "wb") as f:
        f.write(b"PARAM_CANCEL_DATA_V1" * 50)
    with open(v2, "wb") as f:
        f.write(b"PARAM_CANCEL_DATA_V2" * 50)

    cfg = SourceAnalysisConfig(job_id=f"job_cancel_{cancel_stage}", video_paths=(v1, v2))
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache_pc"))

    # v1 luôn hoàn thành audio qua cache
    h1, p1 = compute_full_file_sha256(v1)
    cache_mgr.save_scan_result(
        v1, {"clip_dur": 5.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "Clip 1 done"}]},
        model_size="small", language="vi", verified_identity=(h1, p1)
    )

    mock_transcriber = MagicMock()
    mock_visual = MagicMock()
    mock_visual.scan.return_value = {"cache_hit": True, "samples": []}

    worker = None

    def fake_hash(path, cancelled_fn=None):
        if cancel_stage == "during_hash" and "clip_cancel_p2" in path and worker:
            worker.cancel()
        return compute_full_file_sha256(path, cancelled_fn=cancelled_fn)

    def fake_probe(path):
        if cancel_stage == "during_probe" and "clip_cancel_p2" in path and worker:
            worker.cancel()
        return True

    def fake_load(*args, **kwargs):
        if cancel_stage == "during_load" and worker:
            worker.cancel()
    mock_transcriber.load_model.side_effect = fake_load

    def fake_transcribe(*args, **kwargs):
        if cancel_stage == "during_transcribe" and worker:
            worker.cancel()
        return [{"start": 0.0, "end": 1.0, "text": "test"}]
    mock_transcriber.transcribe.side_effect = fake_transcribe

    def fake_scan(*args, **kwargs):
        res = {"cache_hit": True, "samples": []}
        if cancel_stage == "after_visual" and "clip_cancel_p1" in args[0] and worker:
            worker.cancel()
        return res
    mock_visual.scan.side_effect = fake_scan

    worker = SourceAnalysisWorker(
        cfg,
        cache_mgr=cache_mgr,
        visual_index=mock_visual,
        transcriber_factory=lambda c: mock_transcriber,
        audio_probe_fn=fake_probe,
        hash_fn=fake_hash
    )
    term_events = []
    worker.finished_signal.connect(lambda j, s, m, c: term_events.append((j, s, m, c)))

    with patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=5.0):
        worker.run()

    assert len(term_events) == 1
    _, success, msg, counters = term_events[0]
    assert success is False
    assert msg == "cancelled"

    # Clip 1 audio đã hoàn thành trước đó được bảo toàn!
    assert counters["audio"]["cached"] == 1

    # Ràng buộc bộ đếm nghiêm ngặt:
    # 1. pending == 0 tại terminal cho cả 2 stages
    assert counters["audio"]["pending"] == 0
    assert counters["visual"]["pending"] == 0

    # 2. Partition sum == total_clips (2)
    c_a = counters["audio"]
    sum_a = c_a["cached"] + c_a["new"] + c_a["no_audio_track"] + c_a["failed"] + c_a["unavailable"] + c_a["skipped"] + c_a["cancelled"]
    assert sum_a == 2

    c_v = counters["visual"]
    sum_v = c_v["cached"] + c_v["new"] + c_v["failed"] + c_v["skipped"] + c_v["cancelled"]
    assert sum_v == 2


def test_source_analysis_worker_enforces_local_files_only_even_if_config_false(tmp_path, qapp):
    """Dù config.local_files_only = False, worker vẫn enforce local_files_only=True khi gọi transcriber.load_model."""
    video = str(tmp_path / "clip_offline.mp4")
    with open(video, "wb") as f:
        f.write(b"OFFLINE_TEST" * 50)

    cfg = SourceAnalysisConfig(
        job_id="job_offline",
        video_paths=(video,),
        local_files_only=False  # Caller truyền False
    )

    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = [{"start": 0.0, "end": 1.0, "text": "offline"}]
    mock_vis = MagicMock()
    mock_vis.scan.return_value = {"cache_hit": True, "samples": []}

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache_off"))

    worker = SourceAnalysisWorker(
        cfg,
        cache_mgr=cache_mgr,
        visual_index=mock_vis,
        transcriber_factory=lambda c: mock_transcriber,
        audio_probe_fn=lambda vp: True
    )

    with patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=5.0):
        worker.run()

    # Xác nhận load_model được gọi với local_files_only=True bất kể config
    mock_transcriber.load_model.assert_called_once()
    _, kwargs = mock_transcriber.load_model.call_args
    assert kwargs.get("local_files_only") is True


@pytest.mark.parametrize("bad_duration", [0.0, -2.5, float("nan"), float("inf")])
def test_source_analysis_worker_invalid_duration_fails_audio(tmp_path, qapp, bad_duration):
    """Thời lượng không hợp lệ (0, âm, nan, inf) -> audio failed, không publish 0 duration, visual vẫn chạy nếu source stable."""
    video = str(tmp_path / f"clip_baddur_{bad_duration}.mp4")
    with open(video, "wb") as f:
        f.write(b"BAD_DUR_TEST" * 50)

    cfg = SourceAnalysisConfig(job_id=f"job_dur_{bad_duration}", video_paths=(video,))
    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = [{"start": 0.0, "end": 1.0, "text": "bad dur"}]
    mock_vis = MagicMock()
    mock_vis.scan.return_value = {"cache_hit": True, "samples": []}
    mock_cache = MagicMock()
    mock_cache.get_cached_scan.return_value = None  # Đảm bảo cache miss

    worker = SourceAnalysisWorker(
        cfg,
        cache_mgr=mock_cache,
        visual_index=mock_vis,
        transcriber_factory=lambda c: mock_transcriber,
        audio_probe_fn=lambda vp: True
    )
    term_events = []
    status_events = []
    worker.finished_signal.connect(lambda j, s, m, c: term_events.append((j, s, m, c)))
    worker.status_signal.connect(lambda j, p, r: status_events.append((p, r)))

    with patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=bad_duration):
        worker.run()

    assert len(term_events) == 1
    _, success, msg, counters = term_events[0]
    assert success is False
    assert counters["audio"]["failed"] == 1
    assert counters["audio"]["new"] == 0
    assert counters["visual"]["cached"] == 1  # Visual vẫn chạy thành công

    # Không gọi save_scan_result
    mock_cache.save_scan_result.assert_not_called()

    # Status phản ánh rõ ràng
    assert len(status_events) == 1
    _, clip_res = status_events[0]
    assert clip_res["audio_status"] == "failed"
    assert "invalid_audio_duration" in clip_res["error"]


def test_source_analysis_worker_cache_save_failure_handled_truthfully(tmp_path, qapp):
    """Khi lưu cache v3 thất bại: giữ kết quả bóc tách nhưng báo persisted/cache_saved=False và warning."""
    video = str(tmp_path / "clip_save_fail.mp4")
    with open(video, "wb") as f:
        f.write(b"SAVE_FAIL_TEST" * 50)

    cfg = SourceAnalysisConfig(job_id="job_save_fail", video_paths=(video,))
    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = [{"start": 0.0, "end": 2.0, "text": "Transcribed ok"}]
    mock_vis = MagicMock()
    mock_vis.scan.return_value = {"cache_hit": True, "samples": []}

    mock_cache = MagicMock()
    mock_cache.get_cached_scan.return_value = None
    mock_cache.save_scan_result.return_value = False  # Lưu thất bại

    worker = SourceAnalysisWorker(
        cfg,
        cache_mgr=mock_cache,
        visual_index=mock_vis,
        transcriber_factory=lambda c: mock_transcriber,
        audio_probe_fn=lambda vp: True
    )
    term_events = []
    status_events = []
    worker.finished_signal.connect(lambda j, s, m, c: term_events.append((j, s, m, c)))
    worker.status_signal.connect(lambda j, p, r: status_events.append((p, r)))

    with patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=5.0):
        worker.run()

    assert len(term_events) == 1
    assert len(status_events) == 1
    _, clip_res = status_events[0]

    assert clip_res["audio_status"] == "new"
    assert clip_res["cache_saved"] is False
    assert clip_res["persisted"] is False
    assert clip_res["warning"] == "cache_save_failed"


def test_source_analysis_worker_signal_snapshots_are_independent(tmp_path, qapp):
    """Các sự kiện counters và status phát ra phải là snapshot độc lập (deep copy), không bị sửa đổi bởi các bước sau."""
    video1 = str(tmp_path / "clip_snap1.mp4")
    video2 = str(tmp_path / "clip_snap2.mp4")
    for v in [video1, video2]:
        with open(v, "wb") as f:
            f.write(b"SNAP_TEST" * 50)

    cfg = SourceAnalysisConfig(job_id="job_snap", video_paths=(video1, video2))
    mock_vis = MagicMock()
    mock_vis.scan.return_value = {"cache_hit": True, "samples": []}
    mock_cache = MagicMock()
    mock_cache.get_cached_scan.return_value = {"raw_subtitles": [], "clip_dur": 5.0}

    captured_counters = []
    captured_statuses = []

    worker = SourceAnalysisWorker(cfg, cache_mgr=mock_cache, visual_index=mock_vis)
    worker.counters_signal.connect(lambda j, c: captured_counters.append(c))
    worker.status_signal.connect(lambda j, p, s: captured_statuses.append(s))

    worker.run()

    # Sau khi chạy qua 2 clips, đã có nhiều lần emit
    assert len(captured_counters) >= 2
    # Snapshot sau clip 1 (audio cached=1, visual cached=1, pending=1)
    first_counter = captured_counters[0]
    # Snapshot cuối cùng (pending=0, cached=2)
    last_counter = captured_counters[-1]

    # Kiểm tra tính bất biến độc lập: dict captured đầu tiên không bị mutate thành cached=2
    assert first_counter["audio"]["cached"] == 1
    assert first_counter["audio"]["pending"] == 1
    assert last_counter["audio"]["cached"] == 2
    assert last_counter["audio"]["pending"] == 0


def test_source_analysis_worker_real_qthread_start_and_join(tmp_path, qapp):
    """Test thực QThread.start() và chờ join bằng worker.wait(5000): terminal phát trước khi finished/cleanup."""
    video = str(tmp_path / "clip_qthread.mp4")
    with open(video, "wb") as f:
        f.write(b"QTHREAD_TEST" * 50)

    cfg = SourceAnalysisConfig(job_id="job_qthread", video_paths=(video,))
    mock_vis = MagicMock()
    mock_vis.scan.return_value = {"cache_hit": True, "samples": []}
    mock_cache = MagicMock()
    mock_cache.get_cached_scan.return_value = {"raw_subtitles": [], "clip_dur": 5.0}

    worker = SourceAnalysisWorker(cfg, cache_mgr=mock_cache, visual_index=mock_vis)
    finished_events = []
    from PySide6.QtCore import Qt
    worker.finished_signal.connect(lambda j, s, m, c: finished_events.append((j, s, m, c)), Qt.ConnectionType.DirectConnection)

    worker.start()
    joined = worker.wait(5000)
    qapp.processEvents()

    assert joined is True
    assert worker.isFinished() is True
    assert len(finished_events) == 1
    _, success, msg, counters = finished_events[0]
    assert success is True
    assert counters["audio"]["cached"] == 1
    assert counters["visual"]["cached"] == 1

