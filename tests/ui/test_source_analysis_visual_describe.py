"""
tests/ui/test_source_analysis_visual_describe.py
Kiểm thử toàn diện chặng 3 (Visual Describe Opt-In) trong Source-Only Analysis:
1. defaultOFF0calls: Default OFF -> 0 calls tới describer hay local_model_info, skipped count == total
2. optin orderaudio->overview->describe: Thứ tự thực thi chuẩn xác theo clip
3. audiohitvisualmissing0Whisper: Audio cache-hit vẫn chạy visual sampling & describe mà không gọi Whisper
4. descriptioncachemodelchanged0Whisper/overviewredecode: Đổi vision model không gọi lại Whisper hay overview sampling
5. unavailable/remote/capabilitydeny: Thiếu model/offline đánh dấu unavailable, không rollback audio hay visual sampling
6. partialneedsreviewbudget: Báo cáo partial trung thực khi cắt max_windows hoặc needs_review, không nâng khống complete
7. cancel giữcheckpoint/counters0pending: Hủy trong lúc describe bảo toàn checkpoint đã commit, counters có 0 pending
8. sourceconfigđổi: Nguồn bị sửa đổi trước describe fails-closed, không gửi ảnh tới vision model
9. optin0windows khôngcomplete: Video rỗng/0 windows không bao giờ được đánh dấu complete
10. Tracer bullet dùng actual core describe_with_ollama + fake HTTP endpoint
11. UI test QTest toggle/click, empty model warning, lock/unlock controls
12. DirectorWindow summary hiển thị đúng 3 stage, limitation note, và snapshot matching 5-tuple / 3-tuple
13. Close owner regression: đóng window khi active describe không popup, đóng an toàn
"""

import copy
import json
import os
import sys
import tempfile
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt, Signal, QObject, QThread
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest

from src.core.cache_manager import ScanCacheManager, compute_full_file_sha256
from src.core.visual_index import VisualIndex, VISUAL_SCHEMA_VERSION, ANALYSIS_VERSION
from src.core.visual_sampling import SAMPLER_VERSION
from src.core.visual_describer import describe_with_ollama, PROMPT_VERSION
from src.ui.director.director_state import DirectorConfig, SourceClipInfo
from src.ui.director.director_controller import DirectorController
from src.ui.director.director_window import DirectorWindow
from src.ui.director.pages.source_page import SourcePage, SourceClipRow
from src.ui.director.workers.source_analysis_worker import (
    SourceAnalysisConfig,
    SourceAnalysisWorker,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def suppress_modal_dialogs():
    """Tự động mock tất cả modal dialog QMessageBox để tránh treo test trên CI/offscreen."""
    with patch.object(QMessageBox, "information") as mock_info, \
         patch.object(QMessageBox, "warning") as mock_warn, \
         patch.object(QMessageBox, "question") as mock_quest:
        mock_quest.return_value = QMessageBox.StandardButton.Yes
        yield mock_info, mock_warn, mock_quest


def _create_mock_image(path: Path) -> Path:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (64, 64), color="blue")
    img.save(path, format="JPEG")
    return path


def load_clips_into_page(page, paths, qapp):
    """Helper nạp clip an toàn vào SourcePage đồng bộ không phụ thuộc ffprobe ngầm."""
    clips = [
        SourceClipInfo(
            path=os.path.normpath(p),
            filename=os.path.basename(p),
            duration_sec=10.0,
            duration_str="00:10",
            resolution="1080p",
            has_cache=False,
            file_size_mb=5.0
        )
        for p in paths
    ]
    page._clip_infos = clips
    page._refresh_clip_list()
    page.clips_changed.emit([c.path for c in clips])
    qapp.processEvents()


# =============================================================================
# 1. DEFAULT OFF -> ZERO VISION CALLS
# =============================================================================

def test_default_opt_out_makes_zero_vision_calls(tmp_path, qapp):
    """Mặc định enable_visual_describe=False: 0 cuộc gọi tới model info hoặc describer, skipped count == total."""
    clip = str(tmp_path / "clip1.mp4")
    with open(clip, "wb") as f:
        f.write(b"SAMPLE_VIDEO_BYTES_1")

    cfg = SourceAnalysisConfig(
        job_id="job_opt_out",
        video_paths=(clip,),
        enable_visual_describe=False,
    )

    mock_describer = MagicMock()
    mock_model_info = MagicMock()
    mock_vis = MagicMock()
    mock_vis.scan.return_value = {"cache_hit": False, "samples": ["f1.jpg"]}

    finished_events = []
    status_events = []

    worker = SourceAnalysisWorker(
        cfg,
        visual_index=mock_vis,
        describer_fn=mock_describer,
        local_model_info_fn=mock_model_info,
        audio_probe_fn=lambda p: False,  # No audio track để đơn giản hóa
    )
    worker.finished_signal.connect(lambda j, s, m, c: finished_events.append((j, s, m, c)))
    worker.status_signal.connect(lambda j, p, r: status_events.append((p, r)))

    worker.run()

    assert len(finished_events) == 1
    _, success, msg, counters = finished_events[0]
    assert success is True
    assert mock_describer.call_count == 0
    assert mock_model_info.call_count == 0
    assert counters["visual_describe"]["skipped"] == 1
    assert counters["visual_describe"]["cached"] == 0
    assert counters["visual_describe"]["new"] == 0

    assert len(status_events) == 1
    path, res = status_events[0]
    assert res["visual_describe_status"] == "skipped"


# =============================================================================
# 2. EXECUTION ORDER: AUDIO -> OVERVIEW -> DESCRIBE
# =============================================================================

def test_opt_in_execution_order_audio_then_visual_then_describe(tmp_path, qapp):
    """Opt-in bật: Thứ tự thực thi tuần tự cho cùng clip phải là Audio -> Sampling -> Describe."""
    clip = str(tmp_path / "clip_order.mp4")
    with open(clip, "wb") as f:
        f.write(b"SAMPLE_VIDEO_ORDER")

    cfg = SourceAnalysisConfig(
        job_id="job_order",
        video_paths=(clip,),
        enable_visual_describe=True,
        vision_model="qwen2.5-vl:7b",
    )

    execution_order = []

    mock_transcriber = MagicMock()
    def fake_transcribe(wav_path, **kwargs):
        execution_order.append("audio_transcribe")
        return [{"start": 0.0, "end": 2.0, "text": "Hello"}]
    mock_transcriber.transcribe.side_effect = fake_transcribe

    mock_vis = MagicMock()
    def fake_scan(vp, **kwargs):
        execution_order.append("visual_sampling")
        return {"cache_hit": False, "samples": ["s1.jpg"]}
    mock_vis.scan.side_effect = fake_scan

    def fake_model_info(model, endpoint, **kwargs):
        execution_order.append("local_model_info")
        return {"name": model, "capabilities": ["vision"]}

    def fake_describer(vp, **kwargs):
        execution_order.append("visual_describe")
        return [{"window_id": "W0", "visual_description": "Described scene"}]

    # Mock index.load to return analysis
    mock_vis.load.return_value = {
        "status": "described",
        "analysis": {
            "status": "complete",
            "coverage": {"total_windows": 1, "selected_windows": 1},
            "descriptions": [{"window_id": "W0", "visual_description": "Described scene"}],
        },
        "descriptions": [{"window_id": "W0", "visual_description": "Described scene"}],
    }

    cache_dir = str(tmp_path / "cache_order")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    worker = SourceAnalysisWorker(
        cfg,
        cache_mgr=cache_mgr,
        visual_index=mock_vis,
        transcriber_factory=lambda c: mock_transcriber,
        audio_probe_fn=lambda p: True,
        describer_fn=fake_describer,
        local_model_info_fn=fake_model_info,
    )

    with patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=5.0):
        worker.run()

    assert execution_order == ["audio_transcribe", "visual_sampling", "local_model_info", "visual_describe"]


# =============================================================================
# 3. AUDIO-HIT + VISUAL-MISS: ZERO WHISPER CALLS, RUNS STAGE 2 & 3
# =============================================================================

def test_audio_hit_visual_missing_does_not_call_whisper(tmp_path, qapp):
    """Audio cache-hit nhưng visual chưa có: Whisper không được gọi, Stage 2 và Stage 3 vẫn chạy đầy đủ."""
    clip = str(tmp_path / "clip_audio_hit.mp4")
    with open(clip, "wb") as f:
        f.write(b"AUDIO_HIT_CONTENT")

    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)
    # Seed audio cache
    h, p = compute_full_file_sha256(clip)
    cache_mgr.save_scan_result(
        clip,
        {"clip_dur": 10.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "Cached speech"}]},
        model_size="small",
        language="vi",
        verified_identity=(h, p),
    )

    cfg = SourceAnalysisConfig(
        job_id="job_audio_hit",
        video_paths=(clip,),
        enable_visual_describe=True,
        vision_model="qwen2.5-vl:7b",
    )

    mock_transcriber = MagicMock()
    mock_vis = MagicMock()
    mock_vis.scan.return_value = {"cache_hit": False, "samples": ["f1.jpg"]}
    mock_vis.load.return_value = {
        "status": "described",
        "analysis": {
            "status": "complete",
            "coverage": {"total_windows": 1, "selected_windows": 1},
            "descriptions": [{"window_id": "W0", "visual_description": "Scene 1"}],
        },
        "descriptions": [{"window_id": "W0", "visual_description": "Scene 1"}],
    }

    mock_describer = MagicMock(return_value=[{"window_id": "W0"}])
    mock_model_info = MagicMock(return_value={"capabilities": ["vision"]})

    worker = SourceAnalysisWorker(
        cfg,
        cache_mgr=cache_mgr,
        visual_index=mock_vis,
        transcriber_factory=lambda c: mock_transcriber,
        describer_fn=mock_describer,
        local_model_info_fn=mock_model_info,
        audio_probe_fn=lambda p: True,
    )

    worker.run()

    # Whisper không bị gọi lại
    assert mock_transcriber.transcribe.call_count == 0
    # Stage 2 và Stage 3 chạy
    assert mock_vis.scan.call_count == 1
    assert mock_describer.call_count == 1


# =============================================================================
# 4. VISION MODEL CHANGED: ZERO WHISPER RERUN, ZERO OVERVIEW REDECODE
# =============================================================================

def test_description_cache_model_changed_does_not_redecode_overview(tmp_path, qapp):
    """Khi đổi vision model: không decode lại contact sheet/overview và không chạy lại Whisper."""
    clip = str(tmp_path / "clip_model_change.mp4")
    with open(clip, "wb") as f:
        f.write(b"MODEL_CHANGE_CONTENT")

    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)
    h, p = compute_full_file_sha256(clip)
    cache_mgr.save_scan_result(
        clip,
        {"clip_dur": 10.0, "raw_subtitles": [{"start": 0.0, "end": 1.0, "text": "Cached"}]},
        model_size="small",
        language="vi",
        verified_identity=(h, p),
    )

    mock_vis = MagicMock()
    # Contact sheet / overview đã có sẵn trong cache
    mock_vis.scan.return_value = {"cache_hit": True, "samples": ["sheet.jpg"]}
    mock_vis.has_sampled_frames.return_value = True
    mock_vis.get_contact_sheet_path.return_value = str(tmp_path / "sheet.jpg")
    mock_vis.load.return_value = {
        "status": "described",
        "analysis": {
            "status": "complete",
            "coverage": {"total_windows": 1, "selected_windows": 1},
            "descriptions": [{"window_id": "W0", "visual_description": "Scene described by model 2"}],
        },
        "descriptions": [{"window_id": "W0", "visual_description": "Scene described by model 2"}],
    }

    cfg = SourceAnalysisConfig(
        job_id="job_model_change",
        video_paths=(clip,),
        enable_visual_describe=True,
        vision_model="llava:13b",  # Model mới
    )

    mock_transcriber = MagicMock()
    mock_describer = MagicMock(return_value=[{"window_id": "W0"}])
    mock_model_info = MagicMock(return_value={"capabilities": ["vision"]})

    finished_events = []

    worker = SourceAnalysisWorker(
        cfg,
        cache_mgr=cache_mgr,
        visual_index=mock_vis,
        transcriber_factory=lambda c: mock_transcriber,
        describer_fn=mock_describer,
        local_model_info_fn=mock_model_info,
        audio_probe_fn=lambda p: True,
    )
    worker.finished_signal.connect(lambda j, s, m, c: finished_events.append((j, s, m, c)))

    worker.run()

    # Zero Whisper
    assert mock_transcriber.transcribe.call_count == 0
    # Overview sampling nạp từ cache mà không decode lại frame mới
    assert mock_vis.scan.call_count == 1
    assert len(finished_events) == 1
    _, success, msg, counters = finished_events[0]
    assert counters["visual"]["cached"] == 1
    assert counters["visual"]["new"] == 0
    # Gọi describer với model mới
    assert mock_describer.call_count == 1
    assert mock_describer.call_args[1].get("model") == "llava:13b"


# =============================================================================
# 5. VISION MODEL UNAVAILABLE: ZERO ROLLBACK CỦA AUDIO & VISUAL SAMPLING
# =============================================================================

def test_vision_model_unavailable_does_not_rollback_audio_or_visual(tmp_path, qapp):
    """Khi model thị giác không khả dụng (chưa cài / offline): Stage 3 đánh dấu unavailable, kết quả Stage 1 & 2 được giữ nguyên."""
    clip = str(tmp_path / "clip_unavail.mp4")
    with open(clip, "wb") as f:
        f.write(b"UNAVAIL_CONTENT")

    cfg = SourceAnalysisConfig(
        job_id="job_unavail",
        video_paths=(clip,),
        enable_visual_describe=True,
        vision_model="missing_model:latest",
    )

    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = [{"start": 0.0, "end": 1.0, "text": "Preserved audio"}]

    mock_vis = MagicMock()
    mock_vis.scan.return_value = {"cache_hit": False, "samples": ["f1.jpg"]}

    def failing_model_info(model, endpoint, **kwargs):
        raise ValueError(f"Chưa cài model Ollama: {model}")

    mock_describer = MagicMock()

    finished_events = []
    status_events = []

    cache_dir = str(tmp_path / "cache_unavail")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    worker = SourceAnalysisWorker(
        cfg,
        cache_mgr=cache_mgr,
        visual_index=mock_vis,
        transcriber_factory=lambda c: mock_transcriber,
        audio_probe_fn=lambda p: True,
        describer_fn=mock_describer,
        local_model_info_fn=failing_model_info,
    )
    worker.finished_signal.connect(lambda j, s, m, c: finished_events.append((j, s, m, c)))
    worker.status_signal.connect(lambda j, p, r: status_events.append((p, r)))

    with patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=5.0):
        worker.run()

    assert len(finished_events) == 1
    _, success, msg, counters = finished_events[0]
    # Khi stage được opt-in bị unavailable, phiên không thành công trọn vẹn
    assert success is False
    assert counters["audio"]["new"] == 1
    assert counters["visual"]["new"] == 1
    assert counters["visual_describe"]["unavailable"] == 1

    # Stage 3 describer không bị gọi
    assert mock_describer.call_count == 0

    # Dữ liệu audio và visual sampling vẫn còn nguyên trong clip_result
    assert len(status_events) == 1
    _, res = status_events[0]
    assert res["audio_status"] == "new"
    assert len(res["audio_segments"]) == 1
    assert res["visual_status"] == "new"
    assert res["visual_describe_status"] == "unavailable"


# =============================================================================
# 6. PARTIAL / NEEDS_REVIEW / BUDGET REPORTING
# =============================================================================

def test_partial_needs_review_budget_reporting(tmp_path, qapp):
    """Báo cáo trung thực: khi bị giới hạn max_windows hoặc needs_review, đếm partial, không nâng khống thành complete."""
    clip = str(tmp_path / "clip_partial.mp4")
    with open(clip, "wb") as f:
        f.write(b"PARTIAL_CONTENT")

    cfg = SourceAnalysisConfig(
        job_id="job_partial",
        video_paths=(clip,),
        enable_visual_describe=True,
        vision_model="qwen2.5-vl:7b",
        max_windows=2,
    )

    mock_vis = MagicMock()
    mock_vis.scan.return_value = {"cache_hit": False, "samples": ["f1.jpg"]}
    # Giả lập metadata trả về từ index.load với 2/5 windows được phân tích và có needs_review
    mock_vis.load.return_value = {
        "status": "needs_review",
        "analysis": {
            "status": "needs_review",
            "coverage": {"total_windows": 5, "selected_windows": 2, "analyzed_windows": 2},
            "descriptions": [{"window_id": "W0", "analysis_status": "needs_review"}],
        },
        "descriptions": [{"window_id": "W0", "analysis_status": "needs_review"}],
    }

    mock_describer = MagicMock(return_value=[{"window_id": "W0"}])
    mock_model_info = MagicMock(return_value={"capabilities": ["vision"]})

    finished_events = []
    status_events = []

    worker = SourceAnalysisWorker(
        cfg,
        visual_index=mock_vis,
        audio_probe_fn=lambda p: False,
        describer_fn=mock_describer,
        local_model_info_fn=mock_model_info,
    )
    worker.finished_signal.connect(lambda j, s, m, c: finished_events.append((j, s, m, c)))
    worker.status_signal.connect(lambda j, p, r: status_events.append((p, r)))

    worker.run()

    assert len(finished_events) == 1
    _, success, msg, counters = finished_events[0]
    # Phân tích chưa trọn vẹn (partial/needs_review) -> success=False
    assert success is False
    assert counters["visual_describe"]["partial"] == 1
    assert counters["visual_describe"]["cached"] == 0
    assert counters["visual_describe"]["new"] == 0

    assert len(status_events) == 1
    _, res = status_events[0]
    assert res["visual_describe_status"] == "partial"
    assert res["analysis_quality"] == "needs_review"
    assert res["coverage"]["selected_windows"] == 2
    assert res["coverage"]["total_windows"] == 5


# =============================================================================
# 7. CANCEL DURING VISUAL DESCRIBE PRESERVES CHECKPOINTS & ZERO PENDING
# =============================================================================

def test_cancel_during_visual_describe_preserves_checkpoints(tmp_path, qapp):
    """Hủy ngang trong lúc chạy Stage 3: phát cancelled, 0 pending tại terminal, checkpoint đã commit được giữ."""
    clip = str(tmp_path / "clip_cancel.mp4")
    with open(clip, "wb") as f:
        f.write(b"CANCEL_CONTENT")

    cfg = SourceAnalysisConfig(
        job_id="job_cancel_desc",
        video_paths=(clip,),
        enable_visual_describe=True,
        vision_model="qwen2.5-vl:7b",
    )

    mock_vis = MagicMock()
    mock_vis.scan.return_value = {"cache_hit": False, "samples": ["f1.jpg"]}

    worker = None
    def cancelling_describer(vp, **kwargs):
        # Giả lập huỷ worker trong lúc suy luận
        worker.cancel()
        raise InterruptedError("Cancelled by user")

    mock_model_info = MagicMock(return_value={"capabilities": ["vision"]})

    finished_events = []
    status_events = []

    worker = SourceAnalysisWorker(
        cfg,
        visual_index=mock_vis,
        audio_probe_fn=lambda p: False,
        describer_fn=cancelling_describer,
        local_model_info_fn=mock_model_info,
    )
    worker.finished_signal.connect(lambda j, s, m, c: finished_events.append((j, s, m, c)))
    worker.status_signal.connect(lambda j, p, r: status_events.append((p, r)))

    worker.run()

    assert len(finished_events) == 1
    _, success, msg, counters = finished_events[0]
    assert success is False
    assert msg == "cancelled"
    # Terminal counters có đúng 0 pending
    assert counters["visual_describe"]["pending"] == 0
    assert counters["visual_describe"]["cancelled"] == 1
    assert counters["audio"]["pending"] == 0
    assert counters["visual"]["pending"] == 0


# =============================================================================
# 8. SOURCE MUTATION BEFORE DESCRIBE FAILS CLOSED
# =============================================================================

def test_source_mutated_before_describe_fails_closed(tmp_path, qapp):
    """File nguồn bị thay đổi mtime/size trước Stage 3: fail-closed, không gửi frame tới vision model."""
    clip = str(tmp_path / "clip_mutate.mp4")
    with open(clip, "wb") as f:
        f.write(b"INITIAL_MUTATE_CONTENT")

    cfg = SourceAnalysisConfig(
        job_id="job_mutate",
        video_paths=(clip,),
        enable_visual_describe=True,
        vision_model="qwen2.5-vl:7b",
    )

    mock_vis = MagicMock()
    def fake_scan_and_mutate(vp, **kwargs):
        # Làm bẩn file nguồn sau khi scan xong
        with open(clip, "wb") as f:
            f.write(b"MUTATED_NEW_CONTENT_DIFFERENT_SIZE_123456789")
        return {"cache_hit": False, "samples": ["f1.jpg"]}
    mock_vis.scan.side_effect = fake_scan_and_mutate

    mock_describer = MagicMock()
    mock_model_info = MagicMock(return_value={"capabilities": ["vision"]})

    finished_events = []
    worker = SourceAnalysisWorker(
        cfg,
        visual_index=mock_vis,
        audio_probe_fn=lambda p: False,
        describer_fn=mock_describer,
        local_model_info_fn=mock_model_info,
    )
    worker.finished_signal.connect(lambda j, s, m, c: finished_events.append((j, s, m, c)))

    worker.run()

    # Describer TUYỆT ĐỐI không được gọi khi nguồn đã bị bẩn
    assert mock_describer.call_count == 0
    _, success, msg, counters = finished_events[0]
    assert success is False
    assert counters["visual_describe"]["failed"] == 1


# =============================================================================
# 9. OPT-IN 0 WINDOWS NEVER MARKED COMPLETE
# =============================================================================

def test_opt_in_empty_windows_never_marked_complete(tmp_path, qapp):
    """Clip rỗng/0 windows không bao giờ được coi là complete."""
    clip = str(tmp_path / "clip_zero_win.mp4")
    with open(clip, "wb") as f:
        f.write(b"EMPTY_WIN_CONTENT")

    cfg = SourceAnalysisConfig(
        job_id="job_zero_win",
        video_paths=(clip,),
        enable_visual_describe=True,
        vision_model="qwen2.5-vl:7b",
    )

    mock_vis = MagicMock()
    mock_vis.scan.return_value = {"cache_hit": False, "samples": []}
    # Giả lập load trả về 0 windows
    mock_vis.load.return_value = {
        "status": "none",
        "analysis": {
            "status": "none",
            "coverage": {"total_windows": 0, "selected_windows": 0},
            "descriptions": [],
        },
        "descriptions": [],
    }

    mock_describer = MagicMock(return_value=[])
    mock_model_info = MagicMock(return_value={"capabilities": ["vision"]})

    finished_events = []
    worker = SourceAnalysisWorker(
        cfg,
        visual_index=mock_vis,
        audio_probe_fn=lambda p: False,
        describer_fn=mock_describer,
        local_model_info_fn=mock_model_info,
    )
    worker.finished_signal.connect(lambda j, s, m, c: finished_events.append((j, s, m, c)))

    worker.run()

    _, success, msg, counters = finished_events[0]
    assert success is False
    assert counters["visual_describe"]["cached"] == 0
    assert counters["visual_describe"]["new"] == 0
    assert counters["visual_describe"]["partial"] == 1


# =============================================================================
# 10. TRACER BULLET A: ACTUAL CORE + FAKE HTTP (MODEL SWITCH & CACHE REPLAY)
# =============================================================================

def test_actual_core_with_fake_http_tracer_bullet(tmp_path, qapp, monkeypatch):
    """Tracer Bullet A: Chạy actual describe_with_ollama + real VisualIndex qua fake HTTP.
    Kiểm tra đổi model chạy inference mới (new=1, cached=0), replay cùng config tái sử dụng cache (cached=1, new=0).
    Zero Whisper rerun, zero overview redecode; GET tags/show không được tính là inference request."""
    video_path = tmp_path / "tracer_clip.mp4"
    video_path.write_bytes(b"TRACER_BULLET_VIDEO_CONTENT_BYTES_123456789")

    cache_dir = tmp_path / "visual_cache"
    index = VisualIndex(str(cache_dir))

    # Seed overview samples và contact sheet
    img1 = _create_mock_image(tmp_path / "f_000000.jpg")
    img2 = _create_mock_image(tmp_path / "f_000001.jpg")
    cs = _create_mock_image(tmp_path / "sheet.jpg")
    fp = index._fingerprint(video_path)

    initial_record = {
        "schema_version": VISUAL_SCHEMA_VERSION,
        "fingerprint": fp,
        "duration_sec": 8.0,
        "interval_sec": 8.0,
        "max_samples": 2,
        "status": "sampled",
        "sampler_version": SAMPLER_VERSION,
        "contact_sheet_path": str(cs),
        "samples": [{"time_sec": 1.0, "image_path": str(img1)}, {"time_sec": 4.0, "image_path": str(img2)}],
        "temporal_config": {
            "window_sec": 8.0,
            "frames_per_window": 2,
            "max_windows": 1,
            "sampler_version": SAMPLER_VERSION,
        },
        "coverage": {
            "complete": True,
            "total_windows": 1,
            "selected_windows": 1,
            "sampled_windows": 1,
            "valid_windows": 1,
            "sampled_ranges": [[0.0, 8.0]],
        },
        "windows": [
            {
                "window_id": "W0",
                "start_sec": 0.0,
                "end_sec": 8.0,
                "status": "ready",
                "samples": [
                    {"frame_id": "f_000000", "time_sec": 1.0, "image_path": str(img1)},
                    {"frame_id": "f_000001", "time_sec": 4.0, "image_path": str(img2)},
                ],
            }
        ],
    }
    index._write(video_path, initial_record, None)

    route_call_counts = {"/api/tags": 0, "/api/show": 0, "/api/chat": 0}

    # Fake HTTP handler cho Ollama API hỗ trợ đa mô hình
    def fake_json_request(endpoint, route, payload=None, timeout=30):
        if route in route_call_counts:
            route_call_counts[route] += 1
        if route == "/api/tags":
            return {"models": [{"name": "qwen2.5-vl:7b"}, {"name": "llava:13b"}]}
        if route == "/api/show":
            req_model = payload.get("model", "") if isinstance(payload, dict) else ""
            if "llava" in req_model:
                digest = "sha256:llava_digest_9876543210"
            else:
                digest = "sha256:qwen_digest_1234567890"
            return {
                "model": req_model or "qwen2.5-vl:7b",
                "capabilities": ["vision"],
                "digest": digest,
                "details": {"families": ["clip"]},
            }
        if route == "/api/chat":
            req_model = payload.get("model", "model") if isinstance(payload, dict) else "model"
            content = json.dumps({
                "segments": [
                    {
                        "start_sec": 0.0,
                        "end_sec": 8.0,
                        "description": f"Verified scene by {req_model}",
                        "objects": ["person"],
                        "observed_actions": ["walking"],
                        "evidence_frame_ids": ["F0", "F1"],
                        "analysis_status": "described",
                    }
                ]
            })
            return {"message": {"content": content}}
        raise ValueError(f"Unexpected route: {route}")

    monkeypatch.setattr("src.core.visual_describer._json_request", fake_json_request)

    # ----------------- LƯỢT 1: Chạy với model A (qwen2.5-vl:7b) -----------------
    cfg_a = SourceAnalysisConfig(
        job_id="job_model_a",
        video_paths=(str(video_path),),
        enable_visual_describe=True,
        vision_model="qwen2.5-vl:7b",
        window_sec=8.0,
        frames_per_window=2,
        max_windows=1,
        max_refinements=0,
    )

    finished_events_a = []
    with patch.object(index, "scan", return_value={"cache_hit": True, "samples": [str(img1), str(img2)]}) as mock_scan:
        worker_a = SourceAnalysisWorker(
            cfg_a,
            visual_index=index,
            audio_probe_fn=lambda p: False,
        )
        worker_a.finished_signal.connect(lambda j, s, m, c: finished_events_a.append((j, s, m, c)))
        worker_a.run()

    assert len(finished_events_a) == 1
    assert finished_events_a[0][1] is True
    assert finished_events_a[0][3]["visual_describe"]["new"] == 1
    assert finished_events_a[0][3]["visual_describe"]["cached"] == 0
    assert route_call_counts["/api/chat"] == 1

    # ----------------- LƯỢT 2: Đổi sang model B (llava:13b) khác digest -----------------
    # Vì digest khác, core phải chạy inference mới, KHÔNG ĐƯỢC báo cache!
    cfg_b = SourceAnalysisConfig(
        job_id="job_model_b",
        video_paths=(str(video_path),),
        enable_visual_describe=True,
        vision_model="llava:13b",
        window_sec=8.0,
        frames_per_window=2,
        max_windows=1,
        max_refinements=0,
    )

    finished_events_b = []
    with patch.object(index, "scan", return_value={"cache_hit": True, "samples": [str(img1), str(img2)]}) as mock_scan:
        worker_b = SourceAnalysisWorker(
            cfg_b,
            visual_index=index,
            audio_probe_fn=lambda p: False,
        )
        worker_b.finished_signal.connect(lambda j, s, m, c: finished_events_b.append((j, s, m, c)))
        worker_b.run()

    assert len(finished_events_b) == 1
    assert finished_events_b[0][1] is True
    # Khẳng định gap 1 đã được sửa: Đổi model thì new=1, cached=0!
    assert finished_events_b[0][3]["visual_describe"]["new"] == 1
    assert finished_events_b[0][3]["visual_describe"]["cached"] == 0
    assert route_call_counts["/api/chat"] == 2  # Thêm 1 lần gọi inference cho model B

    # ----------------- LƯỢT 3: Chạy lại model B cùng cấu hình (Replay) -----------------
    # Vì cùng signature và đã hoàn tất, core shortcut tái sử dụng: 0 /api/chat calls, cached=1, new=0!
    cfg_b_replay = SourceAnalysisConfig(
        job_id="job_model_b_replay",
        video_paths=(str(video_path),),
        enable_visual_describe=True,
        vision_model="llava:13b",
        window_sec=8.0,
        frames_per_window=2,
        max_windows=1,
        max_refinements=0,
    )

    finished_events_replay = []
    chat_calls_before_replay = route_call_counts["/api/chat"]

    with patch.object(index, "scan", return_value={"cache_hit": True, "samples": [str(img1), str(img2)]}) as mock_scan:
        worker_replay = SourceAnalysisWorker(
            cfg_b_replay,
            visual_index=index,
            audio_probe_fn=lambda p: False,
        )
        worker_replay.finished_signal.connect(lambda j, s, m, c: finished_events_replay.append((j, s, m, c)))
        worker_replay.run()

    assert len(finished_events_replay) == 1
    assert finished_events_replay[0][1] is True
    # Khẳng định replay cache hit chính xác:
    assert finished_events_replay[0][3]["visual_describe"]["cached"] == 1
    assert finished_events_replay[0][3]["visual_describe"]["new"] == 0
    # ZERO cuộc gọi /api/chat mới ở lượt replay:
    assert route_call_counts["/api/chat"] == chat_calls_before_replay


# =============================================================================
# 10B. COVERAGE METRICS, ROW LABELS & BUDGET BOUNDS
# =============================================================================

def test_resume_partial_coverage_row_label_and_budget_bounds(qapp, tmp_path):
    """Kiểm tra Gap 2:
    - Resume partial 1 window analyzed, 2 sampled valid / selected 2 -> row label analyzed 1/2; khi 2 complete đúng 2/2.
    - Budget total 4, selected 2, analyzed 2 -> không full coverage 4/4, chất lượng partial không claim complete.
    - needs_review / pending_refinement / error / insufficient được phân tách rõ ràng."""
    from src.ui.director.pages.source_page import SourceClipRow
    from src.ui.director.director_state import SourceClipInfo

    clip_info = SourceClipInfo(
        path=str(tmp_path / "test_clip.mp4"),
        filename="test_clip.mp4",
        duration_sec=32.0,
        duration_str="00:32",
        resolution="1920x1080",
    )
    row = SourceClipRow(clip_info)

    # 1. Partial: 1 window analyzed, 2 valid sampled / selected 2
    partial_cov = {
        "total_windows": 2,
        "selected_windows": 2,
        "sampled_valid_windows": 2,
        "analyzed_windows": 1,
        "described_windows": 1,
        "needs_review_windows": 0,
        "error_windows": 0,
        "insufficient_windows": 0,
        "pending_refinement_windows": 0,
        "complete": False,
    }
    row.update_analysis_status({
        "audio_status": "skipped",
        "visual_status": "cached",
        "visual_describe_status": "partial",
        "analysis_quality": "partial",
        "analysis_coverage": partial_cov,
    })
    txt = row.lbl_cache.text()
    assert "AI đã phân tích 1/2 cửa sổ được chọn; khung hình 2/2" in txt

    # 2. Complete: 2 windows analyzed, 2 valid sampled / selected 2
    complete_cov = {
        "total_windows": 2,
        "selected_windows": 2,
        "sampled_valid_windows": 2,
        "analyzed_windows": 2,
        "described_windows": 2,
        "needs_review_windows": 0,
        "error_windows": 0,
        "insufficient_windows": 0,
        "pending_refinement_windows": 0,
        "complete": True,
    }
    row.update_analysis_status({
        "audio_status": "skipped",
        "visual_status": "cached",
        "visual_describe_status": "new",
        "analysis_quality": "complete",
        "analysis_coverage": complete_cov,
    })
    txt_complete = row.lbl_cache.text()
    assert "Có mô tả AI cần đối chiếu (Mới · 2/2 cửa sổ)" in txt_complete

    # 3. Budget-limited: total 4, selected 2, analyzed 2 -> coverage complete là False, chất lượng partial
    budget_cov = {
        "total_windows": 4,
        "selected_windows": 2,
        "sampled_valid_windows": 2,
        "analyzed_windows": 2,
        "described_windows": 2,
        "needs_review_windows": 0,
        "error_windows": 0,
        "insufficient_windows": 0,
        "pending_refinement_windows": 0,
        "complete": False,
    }
    row.update_analysis_status({
        "audio_status": "skipped",
        "visual_status": "cached",
        "visual_describe_status": "partial",
        "analysis_quality": "partial",
        "analysis_coverage": budget_cov,
    })
    txt_budget = row.lbl_cache.text()
    # Nhãn thể hiện rõ 2/2 cửa sổ được chọn trong khi clip 4 cửa sổ
    assert "AI đã phân tích 2/2 cửa sổ được chọn; khung hình 2/2" in txt_budget
    assert budget_cov["complete"] is False


# =============================================================================
# 10C. SNAPSHOT GAP: STRUCTURED SNAPSHOT, BUDGET/ENDPOINT SWITCH & LEGACY COMPAT
# =============================================================================

def test_snapshot_invalidation_and_matching_with_endpoint_budget_switch(qapp, tmp_path):
    """Kiểm tra Gap 3:
    - StructuredAnalysisSnapshot lưu đủ endpoint, window_sec, frames_per_window, max_windows, max_refinements.
    - Đổi endpoint hoặc budget làm mất khớp snapshot -> Review refresh guard chặn refresh.
    - Khớp đầy đủ snapshot -> Review refresh guard cho phép refresh.
    - Legacy 3-tuple và 5-tuple read compatibility hoạt động trơn tru không crash."""
    ctrl = DirectorController()
    window = DirectorWindow(ctrl)

    p1 = str(tmp_path / "snap_clip.mp4")
    with open(p1, "wb") as f:
        f.write(b"data_snap")

    load_clips_into_page(window.page_source, [p1], qapp)
    window.config.video_paths = [os.path.normpath(p1)]
    window.config.whisper_model = "small"
    window.config.whisper_language = "vi"

    page = window.page_source

    # Khởi chạy analysis với cấu hình tùy chỉnh endpoint và budgets
    class DummyWorker(QThread):
        progress_signal = Signal(str, int, int)
        status_signal = Signal(str, str, dict)
        counters_signal = Signal(str, dict)
        finished_signal = Signal(str, bool, str, dict)
        log_signal = Signal(str, str)
        def isRunning(self): return True
        def cancel(self): pass

    page.chk_enable_visual_describe.setChecked(True)
    page.cbo_vision_model.setEditText("qwen2.5-vl:7b")
    window.config.enable_visual_describe = True
    window.config.vision_model = "qwen2.5-vl:7b"
    page.start_source_analysis(
        whisper_model="small",
        whisper_language="vi",
        enable_visual_describe=True,
        vision_model="qwen2.5-vl:7b",
        vision_endpoint="http://127.0.0.1:11434",
        window_sec=12.0,
        frames_per_window=3,
        max_windows=5,
        max_refinements=2,
        worker_factory=lambda cfg: DummyWorker(),
    )

    # 1. Kiểm tra structured snapshot lưu giữ đầy đủ các trường
    snap = page.get_completed_analysis_snapshot()
    assert snap is not None
    assert hasattr(snap, "vision_endpoint")
    assert snap.vision_endpoint == "http://127.0.0.1:11434"
    assert snap.window_sec == 12.0
    assert snap.frames_per_window == 3
    assert snap.max_windows == 5
    assert snap.max_refinements == 2

    # 2. Kiểm tra legacy accessor
    leg = page.get_legacy_analysis_snapshot()
    assert len(leg) == 5
    assert leg[0] == "small"
    assert leg[2] is True
    assert leg[3] == "qwen2.5-vl:7b"

    # 3. Đổi budget trên page (ví dụ window_sec) -> Snapshot không khớp, ReviewPage không được refresh
    page.set_vision_budgets(window_sec=8.0)  # Khác snapshot 12.0
    window.page_review.load_analysis_data = MagicMock()
    window._last_completed_source_job_id = None
    counters = {
        "total_clips": 1,
        "audio": {"cached": 1, "new": 0, "no_audio_track": 0, "failed": 0, "unavailable": 0, "skipped": 0, "cancelled": 0, "pending": 0},
        "visual": {"cached": 1, "new": 0, "failed": 0, "cancelled": 0, "pending": 0},
        "visual_describe": {"cached": 1, "new": 0, "partial": 0, "unavailable": 0, "failed": 0, "skipped": 0, "cancelled": 0, "pending": 0},
    }
    window._on_source_analysis_completed("job_snap_chk1", True, "completed", counters)
    window._on_source_analysis_busy_changed(False)
    assert window.page_review.load_analysis_data.call_count == 0

    # 4. Phục hồi đúng budget -> ReviewPage được refresh sau physical finish
    page.set_vision_budgets(window_sec=12.0, frames_per_window=3, max_windows=5, max_refinements=2)
    # Tái thiết lập snapshot cho test
    page._runtime_status_config = snap
    window._last_completed_source_job_id = None
    window._on_source_analysis_completed("job_snap_chk2", True, "completed", counters)
    window._on_source_analysis_busy_changed(False)
    assert window.page_review.load_analysis_data.call_count == 1

    # 5. Legacy 3-tuple và 5-tuple read compatibility
    page._runtime_status_config = ("small", "vi", (os.path.normpath(p1),))
    window._last_completed_source_job_id = None
    window.page_review.load_analysis_data.reset_mock()
    window._on_source_analysis_completed("job_snap_legacy3", True, "completed", counters)
    window._on_source_analysis_busy_changed(False)
    assert window.page_review.load_analysis_data.call_count == 1

    page._runtime_status_config = ("small", "vi", True, "qwen2.5-vl:7b", (os.path.normpath(p1),))
    window._last_completed_source_job_id = None
    window.page_review.load_analysis_data.reset_mock()
    window._on_source_analysis_completed("job_snap_legacy5", True, "completed", counters)
    window._on_source_analysis_busy_changed(False)
    assert window.page_review.load_analysis_data.call_count == 1

    window.close()
    qapp.processEvents()


# =============================================================================
# 10D. DI DESCRIBER WITHOUT EVIDENCE NEVER FALSE CACHED
# =============================================================================

def test_di_describer_without_evidence_never_reports_false_cache(tmp_path, qapp):
    """Khi DI describer_fn là custom/mock không cung cấp run_evidence:
    Worker không được tự gán 'cached', phải báo 'new' để minh bạch."""
    clip = str(tmp_path / "clip_di_no_ev.mp4")
    with open(clip, "wb") as f:
        f.write(b"DI_NO_EVIDENCE_CONTENT")

    mock_vis = MagicMock()
    mock_vis.scan.return_value = {"cache_hit": True, "samples": ["s.jpg"]}
    mock_vis.load.return_value = {
        "status": "complete",
        "analysis": {
            "status": "complete",
            "coverage": {"total_windows": 1, "selected_windows": 1, "valid_windows": 1},
            "windows": [{"window_id": "W0", "status": "described"}],
        },
        "descriptions": [{"window_id": "W0", "visual_description": "DI scene"}],
    }

    # DI describer không nhận/không trả run_evidence
    def legacy_di_describer(vp, **kwargs):
        return [{"window_id": "W0", "description": "DI scene"}]

    cfg = SourceAnalysisConfig(
        job_id="job_di_ev",
        video_paths=(clip,),
        enable_visual_describe=True,
        vision_model="qwen2.5-vl:7b",
    )

    finished_events = []
    worker = SourceAnalysisWorker(
        cfg,
        visual_index=mock_vis,
        audio_probe_fn=lambda p: False,
        describer_fn=legacy_di_describer,
        local_model_info_fn=lambda m, **k: {"capabilities": ["vision"]},
    )
    worker.finished_signal.connect(lambda j, s, m, c: finished_events.append((j, s, m, c)))
    worker.run()

    assert len(finished_events) == 1
    _, success, msg, counters = finished_events[0]
    assert success is True
    # Bắt buộc là new=1, TUYỆT ĐỐI không phải cached=1 vì DI không có evidence chứng minh cache hit:
    assert counters["visual_describe"]["new"] == 1
    assert counters["visual_describe"]["cached"] == 0


# =============================================================================
# 11. UI TEST: SOURCEPAGE TOGGLE, EMPTY MODEL WARNING, CONTROL LOCKING
# =============================================================================

def test_source_page_ui_toggle_and_empty_model_warning(qapp, tmp_path):
    """Kiểm tra SourcePage: toggle checkbox, cảnh báo model rỗng khi opt-in, khóa/mở khóa controls."""
    page = SourcePage()
    p1 = str(tmp_path / "ui_clip1.mp4")
    with open(p1, "wb") as f:
        f.write(b"data1")
    load_clips_into_page(page, [p1], qapp)

    # 1. Mặc định checkbox phải unchecked (OFF)
    assert page.chk_enable_visual_describe.isChecked() is False

    # 2. Bật checkbox nhưng xóa model -> Bấm scan phải cảnh báo và không start
    page.chk_enable_visual_describe.setChecked(True)
    page.cbo_vision_model.setEditText("")

    with patch.object(QMessageBox, "warning") as mock_warn:
        started = page.start_source_analysis()
        assert started is False
        assert mock_warn.call_count == 1
        assert "Chưa chọn mô hình thị giác" in mock_warn.call_args[0][1]

    # 3. Điền model hợp lệ -> Start thành công với fake factory
    page.cbo_vision_model.setEditText("qwen2.5-vl:7b")

    class SimpleFakeWorker(QThread):
        progress_signal = Signal(str, int, int)
        status_signal = Signal(str, str, dict)
        counters_signal = Signal(str, dict)
        finished_signal = Signal(str, bool, str, dict)
        log_signal = Signal(str, str)

        def __init__(self, cfg):
            super().__init__()
            self.config = cfg

        def isRunning(self):
            return True

        def cancel(self):
            pass

    created = []
    def fake_factory(cfg):
        w = SimpleFakeWorker(cfg)
        created.append(w)
        return w

    started = page.start_source_analysis(worker_factory=fake_factory)
    assert started is True
    assert page.is_source_analysis_active() is True

    # Controls phải bị khóa khi worker active
    assert page.chk_enable_visual_describe.isEnabled() is False
    assert page.cbo_vision_model.isEnabled() is False

    # Mở khóa điều khiển
    page._set_analysis_controls_locked(False)
    assert page.chk_enable_visual_describe.isEnabled() is True
    assert page.cbo_vision_model.isEnabled() is True


# =============================================================================
# 12. DIRECTORWINDOW: SUMMARY SHOWS 3 STAGES & SNAPSHOT MATCHING
# =============================================================================

def test_director_window_summary_shows_correct_stage3_counts(qapp, tmp_path):
    """Kiểm tra DirectorWindow summary popup: phân tách 3 chặng, limitation note, snapshot matching 5-tuple."""
    ctrl = DirectorController()
    window = DirectorWindow(ctrl)

    p1 = str(tmp_path / "clip_sum1.mp4")
    p2 = str(tmp_path / "clip_sum2.mp4")
    for p in (p1, p2):
        with open(p, "wb") as f:
            f.write(b"data_sum")

    load_clips_into_page(window.page_source, [p1, p2], qapp)
    window.config.video_paths = [os.path.normpath(p1), os.path.normpath(p2)]

    # A. Opt-out summary
    opt_out_counters = {
        "total_clips": 2,
        "audio": {"cached": 1, "new": 1, "no_audio_track": 0, "failed": 0, "unavailable": 0, "skipped": 0, "cancelled": 0, "pending": 0},
        "visual": {"cached": 2, "new": 0, "failed": 0, "cancelled": 0, "pending": 0},
        "visual_describe": {"cached": 0, "new": 0, "partial": 0, "unavailable": 0, "failed": 0, "skipped": 2, "cancelled": 0, "pending": 0},
    }

    with patch.object(QMessageBox, "information") as mock_info:
        window._on_source_analysis_completed("job_sum_opt_out", True, "completed", opt_out_counters)
        assert mock_info.call_count == 1
        body = mock_info.call_args[0][2]
        assert "Thoại: 2/2" in body
        assert "Khung hình: 2/2" in body
        assert "Mô tả AI: Chưa chạy mô tả AI (tùy chọn tắt)." in body

    # B. Opt-in summary hoàn tất
    window._last_completed_source_job_id = None  # Reset guard
    opt_in_counters = {
        "total_clips": 2,
        "audio": {"cached": 2, "new": 0, "no_audio_track": 0, "failed": 0, "unavailable": 0, "skipped": 0, "cancelled": 0, "pending": 0},
        "visual": {"cached": 2, "new": 0, "failed": 0, "cancelled": 0, "pending": 0},
        "visual_describe": {"cached": 1, "new": 1, "partial": 0, "unavailable": 0, "failed": 0, "skipped": 0, "cancelled": 0, "pending": 0},
    }

    with patch.object(QMessageBox, "information") as mock_info:
        window._on_source_analysis_completed("job_sum_opt_in", True, "completed", opt_in_counters)
        assert mock_info.call_count == 1
        body = mock_info.call_args[0][2]
        assert "Mô tả AI: 2/2 clip hoàn tất (1 từ cache, 1 vừa phân tích)." in body
        assert "Lưu ý: Mô tả hình ảnh đã lưu trong chỉ mục, chưa tự động áp dụng vào dựng phim" in body

    # C. Opt-in partial / budget limited
    window._last_completed_source_job_id = None
    partial_counters = {
        "total_clips": 2,
        "audio": {"cached": 2, "new": 0, "no_audio_track": 0, "failed": 0, "unavailable": 0, "skipped": 0, "cancelled": 0, "pending": 0},
        "visual": {"cached": 2, "new": 0, "failed": 0, "cancelled": 0, "pending": 0},
        "visual_describe": {"cached": 1, "new": 0, "partial": 1, "unavailable": 0, "failed": 0, "skipped": 0, "cancelled": 0, "pending": 0},
    }

    with patch.object(QMessageBox, "warning") as mock_warn:
        window._on_source_analysis_completed("job_sum_partial", False, "partial_analysis", partial_counters)
        assert mock_warn.call_count == 1
        body = mock_warn.call_args[0][2]
        assert "Mô tả AI: 1/2 thành công, 1 một phần/giới hạn ngân sách." in body

    # D. Snapshot matching 5-tuple
    window.page_review.load_analysis_data = MagicMock()
    # Gán snapshot 5-tuple: whisper_model, whisper_lang, enable_vdesc, vision_model, paths
    window.config.whisper_model = "small"
    window.config.whisper_language = "vi"
    window.page_source._runtime_status_config = ("small", "vi", True, "qwen2.5-vl:7b", tuple(window.config.video_paths))
    # Sửa combobox vision_model thành khác snapshot -> ReviewPage không được refresh
    window.page_source.chk_enable_visual_describe.setChecked(True)
    window.page_source.cbo_vision_model.setEditText("different-model")

    window._last_completed_source_job_id = None
    window._on_source_analysis_completed("job_snap_test", True, "completed", opt_in_counters)
    window._on_source_analysis_busy_changed(False)
    assert window.page_review.load_analysis_data.call_count == 0

    # Đưa combobox về đúng snapshot -> ReviewPage được refresh sau physical finish
    window.page_source.cbo_vision_model.setEditText("qwen2.5-vl:7b")
    window.page_source._runtime_status_config = ("small", "vi", True, "qwen2.5-vl:7b", tuple(window.config.video_paths))
    window._last_completed_source_job_id = None
    window._on_source_analysis_completed("job_snap_test_2", True, "completed", opt_in_counters)
    window._on_source_analysis_busy_changed(False)
    assert window.page_review.load_analysis_data.call_count == 1

    window.close()
    qapp.processEvents()
    qapp.processEvents()


# =============================================================================
# 13. CLOSE REGRESSION DURING STAGE 3
# =============================================================================

def test_close_window_during_visual_describe_cancels_cleanly_without_popup(qapp, tmp_path):
    """Đóng window trong lúc worker đang chạy Stage 3: cancel requested, 0 popup, đóng an toàn."""
    ctrl = DirectorController()
    window = DirectorWindow(ctrl)

    p1 = str(tmp_path / "close_clip.mp4")
    with open(p1, "wb") as f:
        f.write(b"data_close")
    load_clips_into_page(window.page_source, [p1], qapp)

    class FakeRunningWorker(QThread):
        progress_signal = Signal(str, int, int)
        status_signal = Signal(str, str, dict)
        counters_signal = Signal(str, dict)
        finished_signal = Signal(str, bool, str, dict)
        log_signal = Signal(str, str)

        def __init__(self, cfg):
            super().__init__()
            self.config = cfg
            self._is_running_fake = False
            self.cancelled = False

        def isRunning(self):
            return self._is_running_fake

        def start(self, priority=None):
            self._is_running_fake = True

        def cancel(self):
            self.cancelled = True

        def emit_physical_finished(self):
            self._is_running_fake = False
            self.finished.emit()

    w = None
    def fake_factory(cfg):
        nonlocal w
        w = FakeRunningWorker(cfg)
        return w

    window.page_source.chk_enable_visual_describe.setChecked(True)
    window.page_source.cbo_vision_model.setEditText("qwen2.5-vl:7b")
    window.page_source.start_source_analysis(worker_factory=fake_factory)
    assert window.page_source.is_source_analysis_active() is True

    with patch.object(QMessageBox, "information") as mock_info, \
         patch.object(QMessageBox, "warning") as mock_warn:
        # User trigger đóng cửa sổ
        window.close()
        qapp.processEvents()

        # Worker phải nhận tín hiệu cancel
        assert w.cancelled is True
        # Không có popup nào được bật lên khi đang close pending
        assert mock_info.call_count == 0
        assert mock_warn.call_count == 0

        # Physical finish
        w.emit_physical_finished()
        qapp.processEvents()
        qapp.processEvents()

    assert window._is_closed is True
    assert window.page_source.is_source_analysis_active() is False
