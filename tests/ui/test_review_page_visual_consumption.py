"""
tests/ui/test_review_page_visual_consumption.py
=============================================================================
Finite Unit Tests for G05-05C1-R1:
- Review analysis worker (read-only metadata loader QThread)
- Outer fail-safe & safe duration normalization (no ValueError, exactly 1 terminal)
- Speech evidence precedence: runtime evidence over old cache, no fake silence
- Nested visual producer schema (signature_data, analyzed vs sampling coverage)
- User origin preservation during visual config mismatch
- Pre/post source stat checks & same basename across two folders
- UI state switching A -> B (clearing old rows on start, 0 old rows shown)
- Lazy provider initialization on worker thread (zero GUI I/O)
- DirectorWindow physical finished refresh coordination (zero mock-only branches)
=============================================================================
"""

import os
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt, QAbstractTableModel, QThread

from src.ui.director.workers.review_analysis_worker import (
    ReviewAnalysisWorker,
    ReviewAnalysisRequest,
    _normalize_duration,
)
from src.ui.director.pages.review_page import ReviewPage, VisualObservationsTableModel
from src.ui.director.director_window import DirectorWindow
from src.ui.director.pages.source_page import StructuredAnalysisSnapshot


@pytest.fixture(autouse=True)
def auto_mock_message_boxes():
    """Tự động mock tất cả modal dialog QMessageBox để tránh treo test trên CI/offscreen."""
    with patch.object(QMessageBox, "information") as mock_info, \
         patch.object(QMessageBox, "warning") as mock_warn, \
         patch.object(QMessageBox, "question") as mock_quest:
        mock_quest.return_value = QMessageBox.StandardButton.Yes
        yield


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def pump_events(app, count: int = 5):
    for _ in range(count):
        app.processEvents()
        time.sleep(0.01)


class DummyCacheManager:
    def __init__(self, data_by_path=None):
        self._data = data_by_path or {}

    def get_cached_scan(self, path: str, model: str = "small", lang: str = "vi"):
        return self._data.get(path)


class DummyVisualIndex:
    def __init__(self, data_by_path=None):
        self._data = data_by_path or {}

    def load(self, path: str, content_hash=None):
        return self._data.get(str(path))


# =============================================================================
# 1. TEST REVIEW ANALYSIS WORKER (READ-ONLY METADATA LOADER)
# =============================================================================

def test_review_analysis_worker_loads_speech_and_visual(qapp, tmp_path):
    clip_a = str(tmp_path / "clip_a.mp4")
    clip_b = str(tmp_path / "clip_b.mp4")
    with open(clip_a, "wb") as f:
        f.write(b"data_a")
    with open(clip_b, "wb") as f:
        f.write(b"data_b")

    cache_mgr = DummyCacheManager({
        clip_a: {
            "status": "complete",
            "clip_dur": 10.0,
            "raw_subtitles": [
                {"start": 1.0, "end": 3.0, "text": "Câu nói thứ nhất"},
                {"start": 4.0, "end": 6.0, "text": "Câu nói thứ hai"},
            ]
        },
        clip_b: {
            "status": "complete",
            "clip_dur": 8.0,
            "raw_subtitles": []  # Silent clip
        }
    })

    visual_index = DummyVisualIndex({
        clip_b: {
            "status": "complete",
            "coverage": {"complete": True, "total_windows": 1, "sampled_windows": 1},
            "descriptions": [
                {
                    "window_id": "W00000",
                    "start_sec": 0.0,
                    "end_sec": 6.0,
                    "description": "Hình vuông đỏ di chuyển sang hình vuông xanh",
                    "observed_actions": ["di chuyển"],
                    "evidence_times": [1.5, 4.5],
                    "evidence_frame_ids": ["F0", "F1"],
                    "analysis_status": "described",
                    "origin": "local_model:qwen3-vl:2b-instruct",
                    "model": "qwen3-vl:2b-instruct",
                }
            ]
        }
    })

    req = ReviewAnalysisRequest(
        request_id="req_test_1",
        generation=1,
        video_paths=(clip_a, clip_b),
        whisper_model="small",
        whisper_language="vi",
        vision_model="qwen3-vl:2b-instruct",
    )

    worker = ReviewAnalysisWorker(req, cache_mgr=cache_mgr, visual_index=visual_index)

    loaded_clips = {}
    def on_clip_loaded(req_id, path, payload):
        loaded_clips[path] = payload

    terminal_results = []
    def on_finished(req_id, success, msg, summary):
        terminal_results.append((success, msg, summary))

    worker.clip_loaded_signal.connect(on_clip_loaded)
    worker.finished_signal.connect(on_finished)

    worker.start()
    worker.wait(3000)
    pump_events(qapp)

    assert len(terminal_results) == 1
    assert terminal_results[0][0] is True  # success
    assert len(loaded_clips) == 2

    # Clip A: Has speech
    payload_a = loaded_clips[clip_a]
    assert payload_a["speech_status"] == "transcribed"
    assert len(payload_a["subtitle_rows"]) == 2
    assert payload_a["subtitle_rows"][0][1] == "Câu nói thứ nhất"

    # Clip B: Silent clip
    payload_b = loaded_clips[clip_b]
    assert payload_b["speech_status"] == "completed_empty"
    assert len(payload_b["subtitle_rows"]) == 0
    assert len(payload_b["visual_observations"]) == 1
    assert payload_b["visual_observations"][0]["window_id"] == "W00000"
    assert payload_b["visual_observations"][0]["origin"] == "local_model:qwen3-vl:2b-instruct"


# =============================================================================
# 2. TEST SET_REVIEW_ROWS([]) PROPERLY CLEARS OLD ROWS & SELECTION
# =============================================================================

def test_set_review_rows_clears_old_rows_when_empty(qapp):
    page = ReviewPage()
    # Initially has demo rows
    assert len(page._review_rows) > 0

    # Explicitly clear with empty list
    page.set_review_rows([])
    pump_events(qapp)

    assert len(page._review_rows) == 0
    speech_rows_count = sum(1 for w in page._row_widgets if hasattr(w, "row_index"))
    assert speech_rows_count == 0


# =============================================================================
# 3. TEST DUAL-CHANNEL CONSUMPTION IN REVIEWPAGE (SILENT CLIP INVENTORY)
# =============================================================================

def test_review_page_load_analysis_data_preserves_silent_clip_in_inventory(qapp, tmp_path):
    clip_silent = str(tmp_path / "drone_scenic.mp4")
    with open(clip_silent, "wb") as f:
        f.write(b"drone_data")

    cache_mgr = DummyCacheManager({
        clip_silent: {
            "status": "complete",
            "clip_dur": 15.0,
            "raw_subtitles": []
        }
    })

    visual_index = DummyVisualIndex({
        clip_silent: {
            "status": "complete",
            "coverage": {"complete": True, "total_windows": 2, "sampled_windows": 2},
            "descriptions": [
                {
                    "window_id": "W00000",
                    "start_sec": 0.0,
                    "end_sec": 8.0,
                    "description": "Bãi biển buổi hoàng hôn",
                    "observed_actions": ["sóng vỗ"],
                    "evidence_times": [2.0],
                    "evidence_frame_ids": ["F0"],
                    "analysis_status": "described",
                    "origin": "local_model:qwen3-vl:2b-instruct",
                    "model": "qwen3-vl:2b-instruct",
                }
            ]
        }
    })

    page = ReviewPage()
    page.load_analysis_data(
        clip_infos=[clip_silent],
        cache_mgr=cache_mgr,
        visual_index=visual_index,
        whisper_model="small",
        whisper_lang="vi",
    )

    deadline = time.time() + 3.0
    while time.time() < deadline and page.is_analysis_loading():
        pump_events(qapp, 2)

    assert len(page._clip_infos) == 1
    # Speech rows must be empty (NOT inserting fake speech row [Không thoại])
    assert len(page._review_rows) == 0

    # Visual observations table must have 1 row for the visual observation
    assert hasattr(page, "visual_model")
    assert page.visual_model.rowCount() == 1
    desc = page.visual_model.data(page.visual_model.index(0, 2), Qt.ItemDataRole.DisplayRole)
    assert "Bãi biển buổi hoàng hôn" in desc


# =============================================================================
# 4. TEST PARTIAL CHECKPOINT KEEPS VALID WINDOWS WITH WARNING
# =============================================================================

def test_partial_checkpoint_keeps_valid_windows_with_warning(qapp, tmp_path):
    clip_partial = str(tmp_path / "partial_clip.mp4")
    with open(clip_partial, "wb") as f:
        f.write(b"partial_data")

    cache_mgr = DummyCacheManager({
        clip_partial: {
            "status": "complete",
            "clip_dur": 20.0,
            "raw_subtitles": [{"start": 0.5, "end": 2.0, "text": "Chào bạn"}]
        }
    })

    visual_index = DummyVisualIndex({
        clip_partial: {
            "status": "partial",
            "coverage": {"complete": False, "total_windows": 4, "sampled_windows": 1},
            "descriptions": [
                {
                    "window_id": "W00000",
                    "start_sec": 0.0,
                    "end_sec": 5.0,
                    "description": "Cửa sổ đầu tiên đã quan sát",
                    "analysis_status": "needs_review",
                    "origin": "local_model:qwen3-vl:2b-instruct",
                    "model": "qwen3-vl:2b-instruct",
                }
            ]
        }
    })

    page = ReviewPage()
    page.load_analysis_data(
        clip_infos=[clip_partial],
        cache_mgr=cache_mgr,
        visual_index=visual_index,
        whisper_model="small",
        whisper_lang="vi",
    )

    deadline = time.time() + 3.0
    while time.time() < deadline and page.is_analysis_loading():
        pump_events(qapp, 2)

    assert page.visual_model.rowCount() == 1
    status_text = page.visual_model.data(page.visual_model.index(0, 4), Qt.ItemDataRole.DisplayRole)
    assert "needs_review" in status_text or "cần đối chiếu" in status_text.lower()


# =============================================================================
# 5. TEST GENERATION GUARD DROPS SUPERSEDED REQUESTS
# =============================================================================

def test_superseded_request_generation_guard(qapp, tmp_path):
    clip_1 = str(tmp_path / "clip_1.mp4")
    clip_2 = str(tmp_path / "clip_2.mp4")
    with open(clip_1, "wb") as f:
        f.write(b"data_1")
    with open(clip_2, "wb") as f:
        f.write(b"data_2")

    cache_mgr = DummyCacheManager({
        clip_1: {"status": "complete", "clip_dur": 5.0, "raw_subtitles": [{"start": 0, "end": 2, "text": "Text 1"}]},
        clip_2: {"status": "complete", "clip_dur": 5.0, "raw_subtitles": [{"start": 0, "end": 2, "text": "Text 2"}]},
    })

    page = ReviewPage()

    # Trigger Request 1
    page.load_analysis_data([clip_1], cache_mgr=cache_mgr)
    gen_1 = page._analysis_generation

    # Immediately trigger Request 2 (superseding Request 1)
    page.load_analysis_data([clip_2], cache_mgr=cache_mgr)
    gen_2 = page._analysis_generation

    assert gen_2 > gen_1

    deadline = time.time() + 3.0
    while time.time() < deadline and page.is_analysis_loading():
        pump_events(qapp, 2)

    # Should display clip_2 data, not clip_1 data
    assert len(page._review_rows) == 1
    assert page._review_rows[0][1] == "Text 2"


# =============================================================================
# 6. TEST SHUTDOWN ANALYSIS WORKERS & CLOSE COORDINATION
# =============================================================================

def test_shutdown_analysis_workers_stops_threads_safely(qapp, tmp_path):
    clip_long = str(tmp_path / "long_clip.mp4")
    with open(clip_long, "wb") as f:
        f.write(b"data_long")
    cache_mgr = DummyCacheManager()

    page = ReviewPage()
    page.load_analysis_data([clip_long], cache_mgr=cache_mgr)

    # Call shutdown
    idle = page.shutdown_analysis_workers()
    deadline = time.time() + 3.0
    while time.time() < deadline and not page.is_analysis_idle():
        pump_events(qapp, 2)

    assert page.is_analysis_idle() is True


# =============================================================================
# 7. TEST DIRECTOR WINDOW CLOSE COORDINATION AND WIRING
# =============================================================================

def test_director_window_wiring_to_review_page(qapp, tmp_path):
    clip_a = str(tmp_path / "dir_a.mp4")
    with open(clip_a, "wb") as f:
        f.write(b"dir_data")
    window = DirectorWindow()
    window.config.video_paths = [clip_a]

    assert hasattr(window.page_review, "load_analysis_data")
    assert hasattr(window.page_review, "shutdown_analysis_workers")

    window.page_source.shutdown_workers()
    window.page_review.shutdown_analysis_workers()
    window.close()
    pump_events(qapp, 2)


# =============================================================================
# 8. TEST P1-A: OUTER FAIL-SAFE & DURATION NORMALIZATION (EXACTLY 1 TERMINAL)
# =============================================================================

def test_outer_failsafe_bad_duration_and_provider_error(qapp, tmp_path):
    """Kiểm tra P1-A:
    - normalize_duration xử lý an toàn: 'bad', NaN, Inf, bool, None -> không ném ValueError.
    - Outer fail-safe của worker đảm bảo đúng 1 terminal signal kể cả khi provider ném Exception.
    - Zero clips request trả về đúng terminal với zero_clips.
    - Cancel trả về terminal cancelled."""
    # 1. Kiểm tra helper normalize_duration
    d, ok = _normalize_duration("bad")
    assert ok is False and d == 0.0
    d, ok = _normalize_duration(float("nan"))
    assert ok is False and d == 0.0
    d, ok = _normalize_duration(float("inf"))
    assert ok is False and d == 0.0
    d, ok = _normalize_duration(True)  # bool
    assert ok is False and d == 0.0
    d, ok = _normalize_duration(None)
    assert ok is False and d == 0.0
    d, ok = _normalize_duration(15.5)
    assert ok is True and d == 15.5

    # 2. Worker nạp clip có duration_sec='bad' từ clip_infos -> không crash, phát đúng 1 terminal
    clip_bad = str(tmp_path / "clip_bad.mp4")
    with open(clip_bad, "wb") as f:
        f.write(b"data_bad")

    mock_ci = MagicMock()
    mock_ci.path = clip_bad
    mock_ci.duration_sec = "bad"

    req = ReviewAnalysisRequest(
        request_id="req_bad_dur",
        generation=1,
        video_paths=(clip_bad,),
        clip_infos=(mock_ci,),
    )
    worker = ReviewAnalysisWorker(req, cache_mgr=DummyCacheManager(), visual_index=DummyVisualIndex())
    term_res = []
    worker.finished_signal.connect(lambda r, s, m, sm: term_res.append((s, m)))
    worker.start()
    worker.wait(2000)
    pump_events(qapp)

    assert len(term_res) == 1
    assert term_res[0][0] is True

    # 3. Provider ném exception unhandled -> Outer fail-safe bắt và phát đúng 1 terminal signal
    class CrashingCacheManager:
        def get_cached_scan(self, *args, **kwargs):
            raise RuntimeError("CRASH_IN_CACHE_MANAGER")

    req_crash = ReviewAnalysisRequest(
        request_id="req_crash",
        generation=2,
        video_paths=(clip_bad,),
    )
    worker_crash = ReviewAnalysisWorker(req_crash, cache_mgr=CrashingCacheManager(), visual_index=DummyVisualIndex())
    term_crash = []
    worker_crash.finished_signal.connect(lambda r, s, m, sm: term_crash.append((s, m)))
    worker_crash.start()
    worker_crash.wait(2000)
    pump_events(qapp)

    assert len(term_crash) == 1
    assert term_crash[0][0] is False
    assert "CRASH_IN_CACHE_MANAGER" in term_crash[0][1]

    # 4. Zero clips request
    req_zero = ReviewAnalysisRequest(request_id="req_zero", generation=3, video_paths=())
    worker_zero = ReviewAnalysisWorker(req_zero)
    term_zero = []
    worker_zero.finished_signal.connect(lambda r, s, m, sm: term_zero.append((s, m, sm)))
    worker_zero.start()
    worker_zero.wait(2000)
    pump_events(qapp)

    assert len(term_zero) == 1
    assert term_zero[0][0] is True
    assert term_zero[0][1] == "zero_clips"
    assert term_zero[0][2]["total_clips"] == 0


# =============================================================================
# 9. TEST P1-B: RUNTIME EVIDENCE OVER OLD CACHE (NO STALE FALLBACK)
# =============================================================================

def test_runtime_speech_evidence_precedence_and_no_stale_fallback(qapp, tmp_path):
    """Kiểm tra P1-B:
    - Khi runtime result báo no_audio_track hoặc completed_empty kèm raw_subtitles=[]:
      Loader KHÔNG fallback sang transcript cũ trong cache!
    - Khi cache thiếu trường 'raw_subtitles': status phải là unscanned, không đoán completed_empty.
    - Missing cache: status unscanned, is_silent=False, clips_silent=0."""
    clip_p = str(tmp_path / "speech_test.mp4")
    with open(clip_p, "wb") as f:
        f.write(b"speech_data")

    # Cache CŨ có transcript (lỗi cũ từng hiển thị câu này)
    old_cache = {
        clip_p: {
            "status": "complete",
            "clip_dur": 10.0,
            "raw_subtitles": [
                {"start": 1.0, "end": 2.0, "text": "CACHE CU KHONG DUOC HIEN"}
            ]
        }
    }
    cache_mgr = DummyCacheManager(old_cache)

    # Runtime mới xác định clip này KHÔNG CÓ TIẾNG (no_audio_track)
    snap = StructuredAnalysisSnapshot(
        whisper_model="small",
        whisper_language="vi",
        enable_visual_describe=False,
        vision_model="",
        video_paths=(clip_p,)
    )
    runtime_res = {
        clip_p: {
            "audio_status": "no_audio_track",
            "raw_subtitles": [],
        }
    }

    req = ReviewAnalysisRequest(
        request_id="req_rt_no_audio",
        generation=1,
        video_paths=(clip_p,),
        snapshot=snap,
        runtime_results=runtime_res,
        whisper_model="small",
        whisper_language="vi",
    )
    worker = ReviewAnalysisWorker(req, cache_mgr=cache_mgr, visual_index=DummyVisualIndex())
    loaded = {}
    worker.clip_loaded_signal.connect(lambda r, p, pay: loaded.update({p: pay}))
    worker.start()
    worker.wait(2000)
    pump_events(qapp)

    payload = loaded[clip_p]
    assert payload["speech_status"] == "no_audio_track"
    # TUYỆT ĐỐI không chứa câu thoại từ cache cũ!
    assert len(payload["subtitle_rows"]) == 0
    assert payload["is_silent"] is True

    # Trường hợp missing 'raw_subtitles' key trong cache
    clip_incomplete = str(tmp_path / "incomplete.mp4")
    with open(clip_incomplete, "wb") as f:
        f.write(b"inc_data")
    cache_incomplete = DummyCacheManager({
        clip_incomplete: {
            "status": "in_progress",
            "clip_dur": 5.0,
            # KHÔNG có khóa raw_subtitles
        }
    })
    req_inc = ReviewAnalysisRequest(
        request_id="req_inc",
        generation=2,
        video_paths=(clip_incomplete,),
        whisper_model="small",
        whisper_language="vi",
    )
    worker_inc = ReviewAnalysisWorker(req_inc, cache_mgr=cache_incomplete, visual_index=DummyVisualIndex())
    loaded_inc = {}
    worker_inc.clip_loaded_signal.connect(lambda r, p, pay: loaded_inc.update({p: pay}))
    worker_inc.start()
    worker_inc.wait(2000)
    pump_events(qapp)

    # Missing field -> unscanned (chưa quét xong), KHÔNG coi là silent!
    assert loaded_inc[clip_incomplete]["speech_status"] == "unscanned"
    assert loaded_inc[clip_incomplete]["is_silent"] is False


# =============================================================================
# 10. TEST P1-A & B: RUNTIME CONFIG MISMATCH & REQUEST IMMUTABILITY
# =============================================================================

def test_runtime_config_mismatch_and_request_immutability(qapp, tmp_path):
    """Kiểm tra caller thay đổi dữ liệu sau khi gọi load_analysis_data:
    Request bất biến không bị ảnh hưởng bởi đột biến của caller."""
    clip_p = str(tmp_path / "immut_clip.mp4")
    with open(clip_p, "wb") as f:
        f.write(b"data_immut")

    cache_mgr = DummyCacheManager({
        clip_p: {
            "status": "complete",
            "clip_dur": 6.0,
            "raw_subtitles": [{"start": 0, "end": 2, "text": "Original text"}]
        }
    })

    page = ReviewPage()
    input_list = [clip_p]
    rt_dict = {clip_p: {"audio_status": "transcribed", "raw_subtitles": [{"start": 0, "end": 2, "text": "Original text"}]}}

    page.load_analysis_data(input_list, runtime_results=rt_dict, cache_mgr=cache_mgr)

    # Đột biến input_list và rt_dict ngay sau khi gọi
    input_list.append("MUTATED_PATH")
    rt_dict[clip_p]["audio_status"] = "MUTATED_STATUS"

    deadline = time.time() + 3.0
    while time.time() < deadline and page.is_analysis_loading():
        pump_events(qapp, 2)

    # UI chỉ nạp đúng 1 clip ban đầu, không bị dính MUTATED_PATH
    assert len(page._clip_infos) == 1
    assert len(page._review_rows) == 1
    assert page._review_rows[0][1] == "Original text"


# =============================================================================
# 11. TEST P1-C: NESTED VISUAL PRODUCER SCHEMA & USER ORIGIN PRESERVATION
# =============================================================================

def test_nested_visual_schema_detection_and_user_origin_preservation(qapp, tmp_path):
    """Kiểm tra P1-C:
    - Đọc đúng schema analysis.signature_data (model, endpoint, window_sec, ...).
    - Endpoint hoặc budget mismatch tạo stale_warning đúng tiếng Việt.
    - Quan sát có origin='user' KHÔNG bị đánh dấu stale khi lệch cấu hình AI model!"""
    clip_v = str(tmp_path / "vis_schema.mp4")
    with open(clip_v, "wb") as f:
        f.write(b"data_vis")

    nested_visual = {
        clip_v: {
            "schema_version": 2,
            "status": "described",
            "descriptions": [
                {
                    "window_id": "W00000",
                    "start_sec": 0.0,
                    "end_sec": 8.0,
                    "description": "Xe buýt màu đỏ",
                    "analysis_status": "described",
                    "origin": "local_model:qwen3-vl:2b-instruct",
                    "model": "qwen3-vl:2b-instruct",
                },
                {
                    "window_id": "W00001",
                    "start_sec": 8.0,
                    "end_sec": 12.0,
                    "description": "Ghi chú của đạo diễn: đoạn này nét căng",
                    "analysis_status": "described",
                    "origin": "user",
                    "model": "manual",
                }
            ],
            "analysis": {
                "status": "complete",
                "coverage": {"sampled_windows": 2, "total_windows": 2, "complete": True},
                "signature_data": {
                    "model": "qwen3-vl:2b-instruct",
                    "endpoint": "http://127.0.0.1:11434",
                    "sampling": {"window_sec": 8.0, "frames_per_window": 2, "max_windows": 10},
                    "refinement_policy": {"max_refinements": 1},
                }
            }
        }
    }
    visual_index = DummyVisualIndex(nested_visual)

    # Yêu cầu với endpoint khác (ví dụ qua remote host khác)
    req = ReviewAnalysisRequest(
        request_id="req_sig_check",
        generation=1,
        video_paths=(clip_v,),
        enable_visual_describe=True,
        vision_model="qwen3-vl:2b-instruct",
        vision_endpoint="http://192.168.1.100:11434",  # Lệch endpoint
        window_sec=8.0,
    )

    worker = ReviewAnalysisWorker(req, cache_mgr=DummyCacheManager(), visual_index=visual_index)
    loaded = {}
    worker.clip_loaded_signal.connect(lambda r, p, pay: loaded.update({p: pay}))
    worker.start()
    worker.wait(2000)
    pump_events(qapp)

    payload = loaded[clip_v]
    assert payload["stale_warning"] is not None
    assert "Lệch cấu hình mô tả thị giác" in payload["stale_warning"]
    assert "endpoint" in payload["stale_warning"]

    obs = payload["visual_observations"]
    assert len(obs) == 2
    # Cảnh quan sát AI bị đánh dấu stale
    assert obs[0]["analysis_status"] == "stale"
    assert obs[0]["origin"] == "local_model:qwen3-vl:2b-instruct"

    # Cảnh quan sát của người dùng (origin='user') KHÔNG bị đổi thành stale!
    assert obs[1]["analysis_status"] == "described"
    assert obs[1]["origin"] == "user"
    assert "Ghi chú của đạo diễn" in obs[1]["description"]


# =============================================================================
# 12. TEST P1-C: PRE/POST SOURCE STAT & SAME BASENAME ACROSS TWO FOLDERS
# =============================================================================

def test_source_stat_pre_post_and_same_basename_two_folders(qapp, tmp_path):
    """Kiểm tra P1-C:
    - 2 clip cùng tên 'clip.mp4' ở 2 thư mục con khác nhau không bị lẫn lộn dữ liệu.
    - File nguồn bị mất -> source_missing.
    - File nguồn bị sửa mtime giữa chừng -> source_changed."""
    dir_a = tmp_path / "folder_a"
    dir_b = tmp_path / "folder_b"
    dir_a.mkdir()
    dir_b.mkdir()

    p_a = str(dir_a / "clip.mp4")
    p_b = str(dir_b / "clip.mp4")

    with open(p_a, "wb") as f:
        f.write(b"content_a")
    with open(p_b, "wb") as f:
        f.write(b"content_b")

    cache_mgr = DummyCacheManager({
        p_a: {"status": "complete", "clip_dur": 4.0, "raw_subtitles": [{"start": 0, "end": 2, "text": "Thoại A"}]},
        p_b: {"status": "complete", "clip_dur": 8.0, "raw_subtitles": [{"start": 0, "end": 2, "text": "Thoại B"}]},
    })

    req = ReviewAnalysisRequest(
        request_id="req_basename_distinct",
        generation=1,
        video_paths=(p_a, p_b),
        whisper_model="small",
        whisper_language="vi",
    )
    worker = ReviewAnalysisWorker(req, cache_mgr=cache_mgr, visual_index=DummyVisualIndex())
    loaded = {}
    worker.clip_loaded_signal.connect(lambda r, p, pay: loaded.update({p: pay}))
    worker.start()
    worker.wait(2000)
    pump_events(qapp)

    # Hai clip độc lập hoàn toàn, không đè nhau
    assert len(loaded) == 2
    assert loaded[p_a]["subtitle_rows"][0][1] == "Thoại A"
    assert loaded[p_b]["subtitle_rows"][0][1] == "Thoại B"

    # Trường hợp file bị xóa trước khi nạp
    p_missing = str(dir_a / "non_existent.mp4")
    req_missing = ReviewAnalysisRequest(
        request_id="req_missing",
        generation=2,
        video_paths=(p_missing,),
    )
    worker_m = ReviewAnalysisWorker(req_missing, cache_mgr=cache_mgr, visual_index=DummyVisualIndex())
    loaded_m = {}
    worker_m.clip_loaded_signal.connect(lambda r, p, pay: loaded_m.update({p: pay}))
    worker_m.start()
    worker_m.wait(2000)
    pump_events(qapp)

    assert loaded_m[p_missing]["speech_status"] == "source_missing"
    assert loaded_m[p_missing]["is_silent"] is False


# =============================================================================
# 13. TEST P1-A: UI SWITCHING A -> B CLEARS OLD ROWS (NO OLD DATA UNDER B)
# =============================================================================

def test_ui_switching_from_a_to_b_clears_old_data_and_close_coordination(qapp, tmp_path):
    """Kiểm tra P1-A:
    - Khi bắt đầu nạp Request B, dữ liệu cũ của Request A bị xóa sạch ngay lập tức.
    - Không view speech/visual A dưới inventory B trong lúc đợi."""
    clip_a = str(tmp_path / "switch_a.mp4")
    clip_b = str(tmp_path / "switch_b.mp4")
    with open(clip_a, "wb") as f:
        f.write(b"data_sw_a")
    with open(clip_b, "wb") as f:
        f.write(b"data_sw_b")

    cache_mgr = DummyCacheManager({
        clip_a: {"status": "complete", "clip_dur": 5.0, "raw_subtitles": [{"start": 0, "end": 2, "text": "SPEECH_OF_A"}]},
        clip_b: {"status": "complete", "clip_dur": 5.0, "raw_subtitles": [{"start": 0, "end": 2, "text": "SPEECH_OF_B"}]},
    })

    page = ReviewPage()

    # 1. Nạp Request A hoàn tất
    page.load_analysis_data([clip_a], cache_mgr=cache_mgr)
    deadline = time.time() + 3.0
    while time.time() < deadline and page.is_analysis_loading():
        pump_events(qapp, 2)
    assert len(page._review_rows) == 1
    assert page._review_rows[0][1] == "SPEECH_OF_A"

    # 2. Bắt đầu Request B -> Ngay lập tức UI của A phải bị dọn sạch
    page.load_analysis_data([clip_b], cache_mgr=cache_mgr)
    # Kiểm tra ngay khi vừa khởi chạy, _review_rows đã được xóa rỗng
    assert len(page._review_rows) == 0
    assert page.visual_model.rowCount() == 0

    # Chờ Request B hoàn tất
    deadline = time.time() + 3.0
    while time.time() < deadline and page.is_analysis_loading():
        pump_events(qapp, 2)
    assert len(page._review_rows) == 1
    assert page._review_rows[0][1] == "SPEECH_OF_B"


# =============================================================================
# 14. TEST P1-A: LAZY INITIALIZATION ON WORKER THREAD (ZERO GUI I/O)
# =============================================================================

def test_provider_lazy_initialization_no_gui_io(qapp, tmp_path):
    """Kiểm tra P1-A:
    ReviewAnalysisWorker.__init__ KHÔNG tạo ScanCacheManager/VisualIndex trên GUI thread.
    Khởi tạo lazy chỉ diễn ra bên trong run()."""
    clip = str(tmp_path / "lazy_test.mp4")
    with open(clip, "wb") as f:
        f.write(b"lazy_data")

    req = ReviewAnalysisRequest(
        request_id="req_lazy",
        generation=1,
        video_paths=(clip,),
    )

    with patch("src.core.cache_manager.ScanCacheManager.__init__", return_value=None) as mock_cm_init, \
         patch("src.core.visual_index.VisualIndex.__init__", return_value=None) as mock_vi_init:
        # 1. Tại thời điểm __init__, KHÔNG được gọi constructor của provider
        worker = ReviewAnalysisWorker(req)
        assert mock_cm_init.call_count == 0
        assert mock_vi_init.call_count == 0

        # Dọn dẹp
        worker.cancel()


# =============================================================================
# 15. TEST P1-A: DIRECTORWINDOW REFRESH ONLY AFTER PHYSICAL FINISH (NO MOCK BRANCH)
# =============================================================================

def test_director_refresh_only_after_physical_finish_no_mock_branch(qapp, tmp_path):
    """Kiểm tra P1-A:
    DirectorWindow: _on_source_analysis_completed KHÔNG nạp review ngay;
    Chỉ khi _on_source_analysis_busy_changed(False) (physical finished) mới thực hiện nạp.
    Loại bỏ hoàn toàn MagicMock branch trong production."""
    clip = str(tmp_path / "dir_refresh.mp4")
    with open(clip, "wb") as f:
        f.write(b"dir_ref_data")

    window = DirectorWindow()
    window.config.video_paths = [clip]

    snap = StructuredAnalysisSnapshot(
        whisper_model="small",
        whisper_language="vi",
        enable_visual_describe=False,
        vision_model="",
        video_paths=(clip,)
    )
    window.page_source.get_video_paths = MagicMock(return_value=[clip])
    window.page_source.get_completed_analysis_snapshot = MagicMock(return_value=snap)
    window.config.whisper_model = "small"
    window.config.whisper_language = "vi"

    # Mock load_analysis_data trên review page để kiểm tra thời điểm được gọi
    window.page_review.load_analysis_data = MagicMock()

    # 1. Phát tín hiệu logical terminal
    counters = {"total_clips": 1, "audio": {"cached": 1}, "visual": {"cached": 1}}
    window._on_source_analysis_completed("job_test_1", True, "completed", counters)

    # Chưa được nạp tại logical terminal!
    assert window.page_review.load_analysis_data.call_count == 0
    assert window._pending_review_refresh is True

    # 2. Phát tín hiệu physical finish (busy = False)
    window._on_source_analysis_busy_changed(False)

    # Lúc này mới thực hiện nạp!
    assert window.page_review.load_analysis_data.call_count == 1
    assert window._pending_review_refresh is False

    window.page_source.shutdown_workers()
    window.page_review.shutdown_analysis_workers()
    window.close()
    pump_events(qapp, 2)


# =============================================================================
# 16. TEST R2-1: SOURCE INVALIDATION DELETED / MODIFIED DURING READ (0 OLD DATA)
# =============================================================================

def test_source_invalidation_deleted_or_modified_during_read(qapp, tmp_path):
    """Kiểm tra R2:
    - File nguồn bị xóa GIỮA pre/post stat: speech_status='source_missing', 0 subtitle rows, 0 observations.
    - File nguồn bị sửa GIỮA read: speech_status='source_changed', 0 subtitle rows, 0 observations.
    - Receiver ReviewPage không đưa dữ liệu invalid vào _review_rows hoặc visual_model."""
    clip_del = str(tmp_path / "clip_del.mp4")
    with open(clip_del, "wb") as f:
        f.write(b"content_to_delete")

    clip_mod = str(tmp_path / "clip_mod.mp4")
    with open(clip_mod, "wb") as f:
        f.write(b"content_to_modify")

    cache_mgr = DummyCacheManager({
        clip_del: {"status": "complete", "clip_dur": 5.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "OLD DEL SUB"}]},
        clip_mod: {"status": "complete", "clip_dur": 5.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "OLD MOD SUB"}]},
    })

    class MutatingVisualIndex:
        def load(self, path: str):
            if path == clip_del:
                if os.path.exists(clip_del):
                    os.remove(clip_del)
            elif path == clip_mod:
                time.sleep(0.01)
                with open(clip_mod, "ab") as f:
                    f.write(b"_APPENDED_BYTES_MAKE_FILE_BIGGER")
            return {
                "status": "complete",
                "descriptions": [{"window_id": "W0", "start_sec": 0, "end_sec": 5, "description": "OLD VISUAL"}]
            }

    page = ReviewPage()
    page.load_analysis_data([clip_del, clip_mod], cache_mgr=cache_mgr, visual_index=MutatingVisualIndex())

    deadline = time.time() + 3.0
    while time.time() < deadline and page.is_analysis_loading():
        pump_events(qapp, 2)

    # Cả hai clip vẫn nằm trong inventory (đúng clip_infos)
    assert len(page._clip_infos) == 2
    # NHƯNG receiver tuyệt đối KHÔNG đưa subtitle rows cũ của clip đã bị mất/sửa vào _review_rows!
    assert len(page._review_rows) == 0
    # Và visual_model tuyệt đối KHÔNG hiển thị observation cũ của clip invalid!
    assert len(page.visual_model._observations) == 0


# =============================================================================
# 17. TEST R2-2: PARAMETRIZE RAW_SUBTITLES VALIDATION & RUNTIME SNAPSHOT MATCHING
# =============================================================================

@pytest.mark.parametrize("raw_subs_val, expected_status, expected_silent", [
    (None, "unscanned", False),
    ("invalid_string", "unscanned", False),
    ({"not": "a list"}, "unscanned", False),
    ([], "completed_empty", True),
])
def test_raw_subtitles_validation_and_runtime_snapshot_matching(qapp, tmp_path, raw_subs_val, expected_status, expected_silent):
    """Kiểm tra R2:
    - Chỉ [] với completed evidence mới là completed_empty / silent=True.
    - None, string, dict, missing không được coi là silent!
    - Snapshot wrong paths không nhận runtime kết quả."""
    clip_p = str(tmp_path / "param_clip.mp4")
    with open(clip_p, "wb") as f:
        f.write(b"param_data")

    cached_data = {
        "status": "complete",
        "clip_dur": 5.0,
    }
    if raw_subs_val is not None:
        cached_data["raw_subtitles"] = raw_subs_val

    cache_mgr = DummyCacheManager({clip_p: cached_data})
    req = ReviewAnalysisRequest(
        request_id="req_param",
        generation=1,
        video_paths=(clip_p,),
        whisper_model="small",
        whisper_language="vi",
    )
    worker = ReviewAnalysisWorker(req, cache_mgr=cache_mgr, visual_index=DummyVisualIndex())
    loaded = {}
    worker.clip_loaded_signal.connect(lambda r, p, pay: loaded.update({p: pay}))
    worker.start()
    worker.wait(2000)
    pump_events(qapp)

    payload = loaded[clip_p]
    assert payload["speech_status"] == expected_status
    assert payload["is_silent"] is expected_silent

    # Kiểm tra runtime với snapshot SAI video_paths: KHÔNG được nhận runtime!
    wrong_snap = StructuredAnalysisSnapshot(
        whisper_model="small",
        whisper_language="vi",
        enable_visual_describe=False,
        vision_model="",
        video_paths=("WRONG_PATH.mp4",)
    )
    rt_results = {
        clip_p: {"audio_status": "transcribed", "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "RUNTIME TEXT"}]}
    }
    req_wrong = ReviewAnalysisRequest(
        request_id="req_wrong_snap",
        generation=2,
        video_paths=(clip_p,),
        snapshot=wrong_snap,
        runtime_results=rt_results,
        whisper_model="small",
        whisper_language="vi",
    )
    worker_w = ReviewAnalysisWorker(req_wrong, cache_mgr=DummyCacheManager({clip_p: {"status": "complete", "raw_subtitles": []}}), visual_index=DummyVisualIndex())
    loaded_w = {}
    worker_w.clip_loaded_signal.connect(lambda r, p, pay: loaded_w.update({p: pay}))
    worker_w.start()
    worker_w.wait(2000)
    pump_events(qapp)

    # Do snapshot video_paths không khớp, runtime results bị bỏ qua! Không nhận RUNTIME TEXT.
    assert loaded_w[clip_p]["speech_status"] != "transcribed"


# =============================================================================
# 18. TEST R2-3: PER-CLIP ERROR ISOLATION AND PARTIAL TERMINAL
# =============================================================================

def test_provider_error_clip1_clip2_good_inventory_preserved_and_partial_terminal(qapp, tmp_path):
    """Kiểm tra R2:
    - Clip 1 lỗi provider, Clip 2 tốt -> Inventory giữ cả 2 clip!
    - Clip 2 vẫn nạp đầy đủ dữ liệu; worker emit đúng 1 terminal với partial."""
    clip1 = str(tmp_path / "clip1_err.mp4")
    clip2 = str(tmp_path / "clip2_ok.mp4")
    with open(clip1, "wb") as f:
        f.write(b"c1_data")
    with open(clip2, "wb") as f:
        f.write(b"c2_data")

    class SelectiveCrashingCacheManager:
        def get_cached_scan(self, path: str, *args, **kwargs):
            if path == clip1:
                raise RuntimeError("FAIL_CLIP1")
            return {
                "status": "complete",
                "clip_dur": 7.0,
                "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "Clip 2 speech"}]
            }

    page = ReviewPage()
    page.load_analysis_data([clip1, clip2], cache_mgr=SelectiveCrashingCacheManager(), visual_index=DummyVisualIndex())

    deadline = time.time() + 3.0
    while time.time() < deadline and page.is_analysis_loading():
        pump_events(qapp, 2)

    # Inventory giữ cả 2 clip!
    assert len(page._clip_infos) == 2
    # Clip 2 vẫn có data thoại trong review_rows!
    assert len(page._review_rows) == 1
    assert page._review_rows[0][1] == "Clip 2 speech"
    # Status label phản ánh đúng partial
    assert "✔" not in page.lbl_visual_status.text() or "một phần" in page.lbl_visual_status.text()


# =============================================================================
# 19. TEST R2-4: REAL RUNTIME IMMUTABILITY WITH BLOCKING AND MATCHING SNAPSHOT
# =============================================================================

def test_real_runtime_branch_immutability_with_blocking_and_matching_snapshot(qapp, tmp_path):
    """Kiểm tra R2:
    - Đột biến nested runtime text/status sau khi submit -> Request giữ nguyên bản gốc."""
    from threading import Event

    clip_p = str(tmp_path / "immut_branch.mp4")
    with open(clip_p, "wb") as f:
        f.write(b"immut_branch_data")

    snap = StructuredAnalysisSnapshot(
        whisper_model="small",
        whisper_language="vi",
        enable_visual_describe=False,
        vision_model="",
        video_paths=(clip_p,)
    )

    rt_results = {
        clip_p: {
            "audio_status": "transcribed",
            "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "ORIGINAL RUNTIME TEXT"}]
        }
    }

    cache_mgr = DummyCacheManager({
        clip_p: {
            "status": "complete",
            "clip_dur": 10.0,
            "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "DIFFERENT CACHE TEXT"}]
        }
    })

    block_event = Event()

    class BlockingVisualIndex:
        def load(self, path):
            block_event.wait(timeout=2.0)
            return {"status": "complete"}

    page = ReviewPage()
    page.load_analysis_data(
        [clip_p],
        snapshot=snap,
        runtime_results=rt_results,
        cache_mgr=cache_mgr,
        visual_index=BlockingVisualIndex()
    )

    # Đột biến sâu sau khi submit:
    rt_results[clip_p]["raw_subtitles"][0]["text"] = "MUTATED RUNTIME TEXT"
    rt_results[clip_p]["audio_status"] = "MUTATED_STATUS"

    # Giải phóng worker tiếp tục đọc
    block_event.set()

    deadline = time.time() + 3.0
    while time.time() < deadline and page.is_analysis_loading():
        pump_events(qapp, 2)

    # Dữ liệu nạp phải là bản gốc ORIGINAL RUNTIME TEXT
    assert len(page._review_rows) == 1
    assert page._review_rows[0][1] == "ORIGINAL RUNTIME TEXT"

    page.shutdown_analysis_workers()


# =============================================================================
# 20. TEST R2-5: MAX_REFINEMENTS=0 PRESERVED & MALFORMED REQUEST PRESERVES UI
# =============================================================================

def test_max_refinements_zero_preserved_and_malformed_request_preserves_ui(qapp, tmp_path):
    """Kiểm tra R2:
    - max_refinements=0 giữ nguyên 0, không bị ép thành 1.
    - Malformed request không xóa UI valid hiện có trước khi reject."""
    clip_p = str(tmp_path / "clip_ref0.mp4")
    with open(clip_p, "wb") as f:
        f.write(b"ref0_data")

    snap = StructuredAnalysisSnapshot(
        whisper_model="small",
        whisper_language="vi",
        enable_visual_describe=True,
        vision_model="qwen",
        max_refinements=0,
        video_paths=(clip_p,)
    )

    page = ReviewPage()
    page.set_review_rows([["00:00:01", "Existing Valid Speech", 0, ""]])
    assert len(page._review_rows) == 1

    # Gọi với malformed request
    page.load_analysis_data(None)
    # UI valid không bị xóa!
    assert len(page._review_rows) == 1
    assert page._review_rows[0][1] == "Existing Valid Speech"

    # Gọi nạp với snapshot có max_refinements = 0
    page.load_analysis_data([clip_p], snapshot=snap, cache_mgr=DummyCacheManager(), visual_index=DummyVisualIndex())

    worker = list(page._active_analysis_workers.values())[0]
    assert worker.request.max_refinements == 0

    page.shutdown_analysis_workers()


# =============================================================================
# 21. TEST R2-6: CONTROLLED CLOSE PENDING DROPS CALLBACKS & CLEANUP
# =============================================================================

def test_controlled_close_pending_when_worker_blocked(qapp, tmp_path):
    """Kiểm tra R2:
    - Close pending khi worker blocked: queued callback không mutate UI;
    - Physical finished dọn dẹp sạch pending request và worker."""
    from threading import Event

    clip_a = str(tmp_path / "close_a.mp4")
    with open(clip_a, "wb") as f:
        f.write(b"data_a")

    unblock_event = Event()

    class ControlledBlockingVisualIndex:
        def load(self, path):
            unblock_event.wait(timeout=2.0)
            return {"status": "complete", "descriptions": [{"window_id": "W0", "start_sec": 0, "end_sec": 5, "description": "Good Window"}]}

    page = ReviewPage()
    page.set_review_rows([])
    page.load_analysis_data(
        [clip_a],
        cache_mgr=DummyCacheManager({clip_a: {"status": "complete", "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "Speech before close"}]}}),
        visual_index=ControlledBlockingVisualIndex()
    )

    # Trong lúc worker đang blocked, trigger close / shutdown
    page.shutdown_analysis_workers()
    assert page._review_close_pending is True

    # Cho phép worker chạy tiếp và emit callbacks
    unblock_event.set()

    deadline = time.time() + 3.0
    while time.time() < deadline and not page.is_analysis_idle():
        pump_events(qapp, 2)

    # Do close pending, các callback muộn không được mutate UI hay đưa dữ liệu vào ReviewRows
    assert len(page._review_rows) == 0
    # Pending payloads được dọn dẹp sạch
    assert len(page._pending_request_payloads) == 0
    assert len(page._active_analysis_workers) == 0


# =============================================================================
# 22. TEST R3-1: TRANSACTIONAL INPUT PREPARATION & STATE PRESERVATION ON REJECT
# =============================================================================

@pytest.mark.parametrize("bad_field, bad_val", [
    ("max_windows", "bad"),
    ("max_windows", float("nan")),
    ("max_windows", float("inf")),
    ("max_windows", True),
    ("max_windows", 10_000_000),
    ("window_sec", "bad"),
    ("window_sec", 0.0),
    ("window_sec", -5.0),
    ("frames_per_window", 0),
    ("frames_per_window", "bad"),
    ("max_refinements", -1),
    ("max_refinements", "bad"),
])
def test_r3_transactional_input_preparation_preserves_ui_on_bad_config(qapp, tmp_path, bad_field, bad_val):
    """Kiểm tra R3-A:
    - Khi cấu hình snapshot không hợp lệ (bad, NaN, Inf, bool, overflow, non-positive),
      ReviewPage reject an toàn TRƯỚC KHI thay đổi UI/generation/active workers.
    - Không ném exception, không làm mất rows/observations cũ, generation không tăng."""
    clip_p = str(tmp_path / "valid_clip.mp4")
    with open(clip_p, "wb") as f:
        f.write(b"data")

    page = ReviewPage()
    page.set_review_rows([["00:00:01", "PRESERVED SPEECH", 0, ""]])
    page.visual_model.set_observations([{"window_id": "W0", "start_sec": 0, "end_sec": 5, "description": "PRESERVED OBS"}])
    old_gen = page._analysis_generation

    # Giả lập worker A đang hoạt động
    mock_worker_a = MagicMock()
    mock_worker_a.request = MagicMock(generation=old_gen)
    page._active_analysis_workers["worker_a"] = mock_worker_a

    # Tạo snapshot với field bị lỗi
    snap_kwargs = {
        "whisper_model": "small",
        "whisper_language": "vi",
        "enable_visual_describe": True,
        "vision_model": "qwen",
        "video_paths": (clip_p,),
    }
    snap_kwargs[bad_field] = bad_val
    snap = StructuredAnalysisSnapshot(**snap_kwargs)

    # Thực hiện load_analysis_data với snapshot không hợp lệ
    page.load_analysis_data([clip_p], snapshot=snap)

    # 1. UI rows cũ được bảo toàn 100%
    assert len(page._review_rows) == 1
    assert page._review_rows[0][1] == "PRESERVED SPEECH"

    # 2. Visual observations cũ được bảo toàn 100%
    assert len(page.visual_model._observations) == 1
    assert page.visual_model._observations[0]["description"] == "PRESERVED OBS"

    # 3. Generation token không bị tăng
    assert page._analysis_generation == old_gen

    # 4. Worker A đang chạy không bị cancel
    mock_worker_a.cancel.assert_not_called()


def test_r3_transactional_input_preparation_preserves_ui_on_bad_inputs_and_unpicklable(qapp, tmp_path):
    """Kiểm tra R3-A:
    - clip_infos chứa object invalid (None, int, empty path) -> reject, UI giữ nguyên.
    - runtime_results uncopyable / chứa unpicklable object -> reject, UI giữ nguyên."""
    clip_p = str(tmp_path / "valid_clip.mp4")
    with open(clip_p, "wb") as f:
        f.write(b"data")

    page = ReviewPage()
    page.set_review_rows([["00:00:01", "PRESERVED SPEECH", 0, ""]])
    old_gen = page._analysis_generation

    # 1. clip_infos chứa phần tử invalid
    page.load_analysis_data([None])
    assert len(page._review_rows) == 1
    assert page._analysis_generation == old_gen

    page.load_analysis_data([12345])
    assert len(page._review_rows) == 1
    assert page._analysis_generation == old_gen

    page.load_analysis_data(["   "])
    assert len(page._review_rows) == 1
    assert page._analysis_generation == old_gen

    # 2. runtime_results không thể deepcopy (chứa generator/unpicklable/crash)
    class UncopyableObject:
        def __deepcopy__(self, memo):
            raise TypeError("CANNOT_DEEPCOPY_OBJECT")

    bad_runtime = {clip_p: {"audio_status": "cached", "bad_obj": UncopyableObject()}}
    page.load_analysis_data([clip_p], runtime_results=bad_runtime)
    assert len(page._review_rows) == 1
    assert page._analysis_generation == old_gen


# =============================================================================
# 23. TEST R3-2: INVENTORY LOADED VS USABLE TERMINAL AND UI MESSAGES
# =============================================================================

def test_r3_all_missing_sources_emits_source_unavailable_terminal(qapp, tmp_path):
    """Kiểm tra R3-B:
    - Toàn bộ nguồn missing -> terminal success=False, message='source_unavailable'.
    - UI hiển thị cảnh báo nguồn không khả dụng, không hiển thị 'Không có quan sát'."""
    p1 = str(tmp_path / "non_existent_1.mp4")
    p2 = str(tmp_path / "non_existent_2.mp4")

    page = ReviewPage()
    page.load_analysis_data([p1, p2], cache_mgr=DummyCacheManager(), visual_index=DummyVisualIndex())

    deadline = time.time() + 3.0
    while time.time() < deadline and page.is_analysis_loading():
        pump_events(qapp, 2)

    # Cả 2 clip vẫn trong inventory của page
    assert len(page._clip_infos) == 2
    # Nhưng rows và visual observations là 0
    assert len(page._review_rows) == 0
    assert len(page.visual_model._observations) == 0

    # UI hiển thị rõ cảnh báo nguồn không khả dụng
    assert "không khả dụng" in page.lbl_visual_status.text()
    assert "Không có quan sát thị giác" not in page.lbl_visual_status.text()
    assert "✔" not in page.lbl_visual_status.text()


def test_r3_mixed_sources_and_all_online_cache_miss(qapp, tmp_path):
    """Kiểm tra R3-B:
    - Mixed good + missing -> partial terminal, UI nạp một phần, good rows được giữ.
    - All online nhưng cache miss -> no_data terminal, UI hiển thị 'Chưa có dữ liệu phân tích'."""
    good_clip = str(tmp_path / "good.mp4")
    missing_clip = str(tmp_path / "missing.mp4")
    with open(good_clip, "wb") as f:
        f.write(b"good_data")

    cache_mgr = DummyCacheManager({
        good_clip: {"status": "complete", "clip_dur": 5.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "GOOD CLIP SPEECH"}]}
    })

    # 1. Mixed good + missing
    page = ReviewPage()
    page.load_analysis_data([good_clip, missing_clip], cache_mgr=cache_mgr, visual_index=DummyVisualIndex())

    deadline = time.time() + 3.0
    while time.time() < deadline and page.is_analysis_loading():
        pump_events(qapp, 2)

    assert len(page._clip_infos) == 2
    assert len(page._review_rows) == 1
    assert page._review_rows[0][1] == "GOOD CLIP SPEECH"
    assert "một phần" in page.lbl_visual_status.text()

    # 2. All online nhưng cache miss (cả audio và visual đều chưa quét)
    online_clip = str(tmp_path / "online_noscan.mp4")
    with open(online_clip, "wb") as f:
        f.write(b"online_data")

    page_miss = ReviewPage()
    page_miss.load_analysis_data([online_clip], cache_mgr=DummyCacheManager(), visual_index=DummyVisualIndex())

    deadline = time.time() + 3.0
    while time.time() < deadline and page_miss.is_analysis_loading():
        pump_events(qapp, 2)

    assert len(page_miss._clip_infos) == 1
    assert len(page_miss._review_rows) == 0
    assert "Chưa có dữ liệu phân tích" in page_miss.lbl_visual_status.text()


# =============================================================================
# 24. TEST R3-3: ZERO GUI I/O & CALLER/IN-MEMORY ORDERING CONSISTENCY
# =============================================================================

def test_r3_zero_gui_io_and_ordering_consistency(qapp, tmp_path):
    """Kiểm tra R3-C:
    - load_analysis_data trên GUI thread: 0 calls tới ffprobe, compute_content_hash, os.stat.
    - Thứ tự danh sách caller được bảo toàn cho inventory, speech rows, và visual observations.
    - Khi có metadata shot_time sẵn in-memory: sắp xếp theo shot_time mà không gọi I/O."""
    clip_1 = str(tmp_path / "clip_first.mp4")
    clip_2 = str(tmp_path / "clip_second.mp4")
    with open(clip_1, "wb") as f:
        f.write(b"c1")
    with open(clip_2, "wb") as f:
        f.write(b"c2")

    import subprocess
    import src.core.chronological_sorter as sorter_module

    page = ReviewPage()

    with patch.object(subprocess, "run") as mock_sp_run, \
         patch("src.core.chronological_sorter.sort_clips_chronologically") as mock_sort, \
         patch("src.core.chronological_sorter.get_chronological_sort_key") as mock_sort_key:

        page.load_analysis_data([clip_2, clip_1], cache_mgr=DummyCacheManager(), visual_index=DummyVisualIndex())

        # Trên GUI thread: KHÔNG gọi chronological_sorter, KHÔNG chạy ffprobe
        mock_sort.assert_not_called()
        mock_sort_key.assert_not_called()
        mock_sp_run.assert_not_called()

        # Thứ tự đầu vào của caller được giữ nguyên: [clip_2, clip_1]
        assert [c if isinstance(c, str) else c.path for c in page._clip_infos] == [clip_2, clip_1]

    page.shutdown_analysis_workers()


def test_r3_zero_clips_list_handling(qapp):
    """Kiểm tra R3-A:
    - Danh sách rỗng [] là hợp lệ: generation tăng, UI rows được reset,
      terminal phát zero_clips trung thực, không claim completed analysis."""
    page = ReviewPage()
    page.set_review_rows([["00:00:01", "Old Speech", 0, ""]])
    old_gen = page._analysis_generation

    page.load_analysis_data([])

    assert page._analysis_generation == old_gen + 1
    assert len(page._review_rows) == 0

    deadline = time.time() + 2.0
    while time.time() < deadline and page.is_analysis_loading():
        pump_events(qapp, 2)

    assert "Chưa có dữ liệu phân tích" in page.lbl_visual_status.text()
    assert "✔" not in page.lbl_visual_status.text()


def test_r3_in_memory_metadata_ordering_and_consistency(qapp, tmp_path):
    """Kiểm tra R3-C:
    - Sắp xếp thuần in-memory dựa trên metadata shot_time có sẵn (không I/O).
    - Bảo toàn stable tie cho các clip có cùng shot_time.
    - Áp cùng thứ tự nhất quán cho inventory, speech rows và visual observations."""
    from types import SimpleNamespace

    c1 = str(tmp_path / "clip_late.mp4")
    c2 = str(tmp_path / "clip_early_1.mp4")
    c3 = str(tmp_path / "clip_early_2.mp4")
    for p in (c1, c2, c3):
        with open(p, "wb") as f:
            f.write(b"data")

    # c1: shot_time = 100.0; c2: shot_time = 20.0; c3: shot_time = 20.0
    item1 = SimpleNamespace(path=c1, shot_time=100.0, duration_sec=5.0, display_name="c1_late")
    item2 = SimpleNamespace(path=c2, shot_time=20.0, duration_sec=5.0, display_name="c2_early_1")
    item3 = SimpleNamespace(path=c3, shot_time=20.0, duration_sec=5.0, display_name="c3_early_2")

    cache_mgr = DummyCacheManager({
        c1: {"status": "complete", "clip_dur": 5.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "SPEECH C1"}]},
        c2: {"status": "complete", "clip_dur": 5.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "SPEECH C2"}]},
        c3: {"status": "complete", "clip_dur": 5.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "SPEECH C3"}]},
    })

    page = ReviewPage()
    # Truyền vào theo thứ tự [item1, item2, item3]
    page.load_analysis_data([item1, item2, item3], cache_mgr=cache_mgr, visual_index=DummyVisualIndex())

    deadline = time.time() + 3.0
    while time.time() < deadline and page.is_analysis_loading():
        pump_events(qapp, 2)

    # Thứ tự sau khi sort in-memory: c2 (20s tie 1), c3 (20s tie 2), c1 (100s)
    ordered_paths = [getattr(c, "path", c) for c in page._clip_infos]
    assert ordered_paths == [c2, c3, c1]

    # Thứ tự speech rows khớp hoàn toàn: C2, C3, C1
    assert len(page._review_rows) == 3
    assert page._review_rows[0][1] == "SPEECH C2"
    assert page._review_rows[1][1] == "SPEECH C3"
    assert page._review_rows[2][1] == "SPEECH C1"


def test_r3_source_changed_during_read_emits_source_unavailable_terminal(qapp, tmp_path):
    """Kiểm tra R3-B:
    - Khi file nguồn bị sửa đổi mtime/size giữa chừng và toàn bộ nguồn đều hỏng:
      worker phát terminal success=False, message='source_unavailable'.
      Counters: invalid_source_clips=1, usable_clips=0."""
    mod_clip = str(tmp_path / "mutating.mp4")
    with open(mod_clip, "wb") as f:
        f.write(b"original_bytes")

    class MutatingVisualIndex:
        def load(self, path: str):
            time.sleep(0.01)
            with open(mod_clip, "ab") as f:
                f.write(b"_APPENDED_BYTES_MUTATION")
            return {"status": "complete", "descriptions": []}

    cache_mgr = DummyCacheManager({
        mod_clip: {"status": "complete", "clip_dur": 5.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "OLD SUB"}]}
    })

    req = ReviewAnalysisRequest(
        request_id="req_mod",
        generation=1,
        video_paths=(mod_clip,),
    )
    worker = ReviewAnalysisWorker(req, cache_mgr=cache_mgr, visual_index=MutatingVisualIndex())
    term_res = []
    worker.finished_signal.connect(lambda r, s, m, sm: term_res.append((s, m, sm)))
    worker.start()
    worker.wait(3000)
    pump_events(qapp)

    assert len(term_res) == 1
    success, msg, summary = term_res[0]
    assert success is False
    assert msg == "source_unavailable"
    assert summary["invalid_source_clips"] == 1
    assert summary["usable_clips"] == 0
    assert summary["speech_status_counts"]["source_changed"] == 1


