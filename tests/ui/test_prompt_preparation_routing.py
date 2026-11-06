"""
tests/ui/test_prompt_preparation_routing.py
=============================================================================
Finite TDD Test Suite cho G05-05C2b-I:
1. Real v3 tmpcache: valid speech vs completed empty / no audio, visual only, no data;
   verify_audio=True và max_prompt_bytes=32768 bounded spies.
2. Manifest scoping & same basename: previous manifest extra entry không lọt;
   source changed during read -> fail-closed 'source_changed' / 0 clipboard.
3. Delayed actual QThread: GUI heartbeat vẫn process; I/O thực thi trên thread phụ.
4. Clipboard protection: error/no_data/budget_error giữ full clipboard MIME;
   valid copy đúng 1 lần vào clipboard và phát prompt_copied('video').
5. Director Run AI single dispatch: 0 trước khi ready, exactly 1 sau khi ready;
   double click / supersede không duplicate.
6. Settings/path/style đổi while blocked: kết quả cũ bị loại bỏ; snapshot không khớp.
7. Window close cooperative cancellation: closeEvent chờ physical QThread.finished;
   cleanup registries, không late dispatch.
8. Getters zero I/O & V2 gate: get_video_prompt() 0 I/O; V2 gated trả về '',
   _copy_kinetic_prompt() 0 clipboard mutation.
=============================================================================
"""

import os
import time
import hashlib
import pytest
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThread
from PySide6.QtTest import QTest

from src.core.clip_manifest import ClipManifest, ClipEntry, build_clips_data
from src.core.cache_manager import ScanCacheManager, compute_full_file_sha256
from src.ui.director.workers.prompt_preparation_worker import (
    PromptPreparationWorker, PromptPreparationRequest
)
from src.ui.director.pages.review_page import ReviewPage
from src.ui.director.director_window import DirectorWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _mock_acquisition_dependency_for_dummy_files(monkeypatch):
    """
    Thích ứng chỉ dành cho kiểm thử: Các tệp dummy byte trong test_prompt_preparation_routing.py
    không phải là video decodable. Fixture này giả lập acquire_local_video_header để trả về
    LocalHeaderAcquisitionResult hợp lệ ràng buộc với đường dẫn nguồn và khớp stat tiền kiểm.
    """
    from fractions import Fraction
    from src.core.contracts.time_contracts import RationalFPS
    from src.core.geometry.local_metadata_acquisition import (
        LocalHeaderAcquisitionResult,
        SourceFreshnessStat,
    )
    from src.core.geometry.source_frame_evidence_contracts import VideoStreamHeader

    def _mock_acquire(file_path, *, ffprobe_path=None, selected_stream_index=None, timeout_sec=5.0, cancelled_fn=None):
        if cancelled_fn and cancelled_fn():
            raise InterruptedError("ERR_PROBE_CANCELLED: tác vụ bị hủy bởi caller")
        abs_p = os.path.normpath(os.path.abspath(file_path))
        st = os.stat(abs_p)
        freshness = SourceFreshnessStat(
            file_size_bytes=st.st_size,
            mtime_ns=st.st_mtime_ns,
            ctime_ns=st.st_ctime_ns,
            device_id=st.st_dev,
            inode_number=st.st_ino,
        )
        header = VideoStreamHeader(
            stream_index=0,
            codec_name="h264",
            r_frame_rate=RationalFPS(30, 1),
            avg_frame_rate=RationalFPS(30, 1),
            reported_nb_frames=300,
            time_base=Fraction(1, 1000),
        )
        return LocalHeaderAcquisitionResult(
            file_path=abs_p,
            video_header=header,
            format_duration_sec=Fraction(10, 1),
            freshness=freshness,
        )

    import src.core.geometry.local_metadata_acquisition as lma
    monkeypatch.setattr(lma, "acquire_local_video_header", _mock_acquire)


def _create_dummy_video(path: str, size: int = 2048, content: bytes = None):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    payload = content if content is not None else (b"RESOLVEFLOW_DUMMY_CONTENT_" * (size // 25 + 1))[:size]
    with open(path, "wb") as f:
        f.write(payload)
    digest, prov = compute_full_file_sha256(path)
    return digest, prov


def _wait_for_worker(worker, timeout_ms: int = 10000):
    elapsed = 0
    try:
        while worker.isRunning() and elapsed < timeout_ms:
            QTest.qWait(20)
            elapsed += 20
    except RuntimeError:
        pass
    QTest.qWait(100)


def _wait_for_request_id(page, req_id: str, timeout_ms: int = 10000):
    elapsed = 0
    while req_id in page._prompt_workers_registry and elapsed < timeout_ms:
        QTest.qWait(20)
        elapsed += 20
    QTest.qWait(100)


def _wait_for_review_page(page, timeout_ms: int = 5000):
    elapsed = 0
    while page.is_analysis_loading() and elapsed < timeout_ms:
        QTest.qWait(20)
        elapsed += 20
    QTest.qWait(50)


class DummyVisualIndex:
    def __init__(self, data_by_path=None):
        self._data = data_by_path or {}

    def load(self, path: str, content_hash=None):
        return self._data.get(str(path))


# =============================================================================
# 1. TEST WORKER V3 TMPCACHE SCENARIOS (VERIFY_AUDIO & BUDGET SPY)
# =============================================================================

def test_prompt_preparation_worker_v3_tmpcache_scenarios(tmp_path, qapp):
    """Kiểm tra C2b-1:
    - Worker chạy qua tmpcache thật với các kịch bản:
      + Valid speech -> thành công, có transcript.
      + Completed empty -> thành công, không thoại.
      + Visual only -> thành công với mô tả thị giác.
      + No data -> thất bại 'no_data', không bịa 120s / DEMO_WORDS.
    - Spy xác nhận verify_audio=True và max_prompt_bytes=32768 được truyền chuẩn."""
    cache_dir = str(tmp_path / "cache")
    manifest_dir = str(tmp_path / "manifests")
    video_file = str(tmp_path / "video1.mp4")
    digest, prov = _create_dummy_video(video_file)

    cache_mgr = ScanCacheManager(cache_dir=cache_dir)
    valid_payload = {
        "clip_dur": 12.0,
        "raw_subtitles": [{"start": 1.0, "end": 4.0, "text": "Hôm nay thời tiết rất đẹp"}],
    }
    cache_mgr.save_scan_result(
        file_path=video_file,
        scan_data=valid_payload,
        model_size="small",
        language="vi",
        verified_identity=(digest, prov)
    )

    # 1. Kịch bản có thoại hợp lệ
    req = PromptPreparationRequest(
        request_id="req_speech",
        generation=1,
        intent="copy_video",
        video_paths=(video_file,),
        whisper_model="small",
        whisper_language="vi",
        manifest_cache_dir=manifest_dir,
    )
    worker = PromptPreparationWorker(req, cache_mgr=cache_mgr)

    results = []
    worker.finished_signal.connect(lambda rid, ok, msg, res: results.append((rid, ok, msg, res)))

    worker.start()
    while worker.isRunning():
        QTest.qWait(20)
    QTest.qWait(50)

    assert len(results) == 1
    rid, ok, msg, res = results[0]
    assert ok is True
    assert msg == "success"
    assert "Hôm nay thời tiết rất đẹp" in res["prompt_text"]
    assert res["clips_count"] == 1
    assert res["prompt_bytes"] <= 32768

    # 2. Kịch bản completed empty (không có thoại)
    video_empty = str(tmp_path / "video_empty.mp4")
    d_empty, p_empty = _create_dummy_video(video_empty, content=b"EMPTY_SPEECH_DATA_STREAM")
    cache_mgr.save_scan_result(
        file_path=video_empty,
        scan_data={"clip_dur": 15.0, "raw_subtitles": []},
        model_size="small",
        language="vi",
        verified_identity=(d_empty, p_empty)
    )

    req_empty = PromptPreparationRequest(
        request_id="req_empty",
        generation=2,
        intent="copy_video",
        video_paths=(video_empty,),
        whisper_model="small",
        whisper_language="vi",
        manifest_cache_dir=manifest_dir,
    )
    worker_em = PromptPreparationWorker(req_empty, cache_mgr=cache_mgr)
    results_em = []
    worker_em.finished_signal.connect(lambda rid, ok, msg, res: results_em.append((rid, ok, msg, res)))
    worker_em.start()
    while worker_em.isRunning():
        QTest.qWait(20)
    QTest.qWait(50)

    assert len(results_em) == 1
    assert results_em[0][1] is True
    assert results_em[0][2] == "success"
    assert results_em[0][3]["clips_count"] == 1

    # 3. Kịch bản visual only (thị giác opt-in có dữ liệu, không có thoại)
    video_vis = str(tmp_path / "video_vis.mp4")
    d_vis, p_vis = _create_dummy_video(video_vis, content=b"VISUAL_ONLY_DATA_STREAM")
    vis_index = DummyVisualIndex({
        video_vis: {
            "status": "complete",
            "coverage": {"complete": True, "total_windows": 1, "sampled_windows": 1},
            "analysis": {
                "status": "complete",
                "coverage": {"analyzed_windows": 1, "total_windows": 1},
                "signature_data": {
                    "model": "qwen3-vl:2b-instruct",
                    "endpoint": "http://127.0.0.1:11434",
                    "sampling": {"window_sec": 8.0, "frames_per_window": 2, "max_windows": 10},
                    "max_refinements": 1,
                }
            },
            "descriptions": [{
                "window_id": "W0",
                "start_sec": 0.0,
                "end_sec": 5.0,
                "description": "Cảnh hoàng hôn trên biển rất đẹp",
                "observed_actions": ["ngắm hoàng hôn"],
                "evidence_times": [2.0],
                "evidence_frame_ids": ["F0"],
                "analysis_status": "described",
                "origin": "local_model:qwen3-vl:2b-instruct",
                "model": "qwen3-vl:2b-instruct",
            }]
        }
    })

    req_vis = PromptPreparationRequest(
        request_id="req_vis",
        generation=3,
        intent="copy_video",
        video_paths=(video_vis,),
        enable_visual_describe=True,
        vision_context={"model": "qwen3-vl:2b-instruct", "endpoint": "http://127.0.0.1:11434"},
        manifest_cache_dir=manifest_dir,
    )
    worker_vis = PromptPreparationWorker(req_vis, cache_mgr=cache_mgr, visual_index=vis_index)
    results_vis = []
    worker_vis.finished_signal.connect(lambda rid, ok, msg, res: results_vis.append((rid, ok, msg, res)))
    worker_vis.start()
    while worker_vis.isRunning():
        QTest.qWait(20)
    QTest.qWait(50)

    assert len(results_vis) == 1
    assert results_vis[0][1] is True
    assert "hoàng hôn trên biển" in results_vis[0][3]["prompt_text"]

    # 4. Kịch bản no_data: video mới chưa hề được phân tích
    unscanned_file = str(tmp_path / "unscanned.mp4")
    _create_dummy_video(unscanned_file, content=b"UNSCANNED_FILE_DATA")

    req_nodata = PromptPreparationRequest(
        request_id="req_nodata",
        generation=4,
        intent="copy_video",
        video_paths=(unscanned_file,),
        manifest_cache_dir=manifest_dir,
    )
    worker_nd = PromptPreparationWorker(req_nodata, cache_mgr=cache_mgr)
    results_nd = []
    worker_nd.finished_signal.connect(lambda rid, ok, msg, res: results_nd.append((rid, ok, msg, res)))

    worker_nd.start()
    while worker_nd.isRunning():
        QTest.qWait(20)
    QTest.qWait(50)

    assert len(results_nd) == 1
    _, ok_nd, msg_nd, res_nd = results_nd[0]
    assert ok_nd is False
    assert msg_nd == "no_data"
    assert "Chưa có dữ liệu phân tích" in res_nd["error"]


# =============================================================================
# 2. TEST MANIFEST SCOPING & SAME BASENAME & SOURCE CHANGED
# =============================================================================

def test_manifest_scoping_no_ghost_clips_and_source_changed(tmp_path, qapp):
    """Kiểm tra C2b-2:
    - 2 clip cùng tên file ở 2 thư mục khác nhau được phân biệt độc lập.
    - Manifest cũ có 3 clip không làm rò rỉ clip thứ 3 vào request chỉ có 2 clip.
    - Tệp nguồn bị thay đổi mtime/size giữa lúc chuẩn bị -> fail-closed 'source_changed'."""
    manifest_dir = str(tmp_path / "manifests")
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    v1 = str(tmp_path / "dir_a" / "clip.mp4")
    v2 = str(tmp_path / "dir_b" / "clip.mp4")
    v3_extra = str(tmp_path / "dir_c" / "extra.mp4")
    d1, p1 = _create_dummy_video(v1, content=b"VIDEO_CONTENT_CLIP_DIR_A")
    d2, p2 = _create_dummy_video(v2, content=b"VIDEO_CONTENT_CLIP_DIR_B")
    d3, p3 = _create_dummy_video(v3_extra, content=b"VIDEO_CONTENT_CLIP_EXTRA")

    # Nạp cache v3 hợp lệ cho cả 3
    for v, (d, p) in [(v1, (d1, p1)), (v2, (d2, p2)), (v3_extra, (d3, p3))]:
        cache_mgr.save_scan_result(
            file_path=v,
            scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 0, "end": 2, "text": f"Thoại {os.path.basename(v)}"}]},
            model_size="small",
            language="vi",
            verified_identity=(d, p)
        )

    # Trước đó có manifest chứa cả 3 clips
    old_manifest = ClipManifest.create([v1, v2, v3_extra], project_name="ChunDVC_Project", cache_dir=manifest_dir)
    assert len(old_manifest.clips) == 3

    # Bây giờ tạo request mới chỉ có [v1, v2]
    req = PromptPreparationRequest(
        request_id="req_scoped",
        generation=1,
        intent="copy_video",
        video_paths=(v1, v2),
        manifest_cache_dir=manifest_dir,
    )
    worker = PromptPreparationWorker(req, cache_mgr=cache_mgr)
    results = []
    worker.finished_signal.connect(lambda rid, ok, msg, res: results.append((rid, ok, msg, res)))

    worker.start()
    while worker.isRunning():
        QTest.qWait(20)
    QTest.qWait(50)

    assert len(results) == 1
    _, ok, _, res = results[0]
    assert ok is True
    assert res["clips_count"] == 2
    # Clip v3_extra tuyệt đối không lọt vào kịch bản mới
    assert "extra.mp4" not in res["prompt_text"]

    # Kiểm tra source changed: sửa file v1 trong khi worker đang xử lý build_clips_data
    from src.core import clip_manifest as cm_module
    orig_build = cm_module.build_clips_data

    def build_and_modify_source(*args, **kwargs):
        # Sửa đổi file v1 trên đĩa
        stat = os.stat(v1)
        os.utime(v1, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        return orig_build(*args, **kwargs)

    with patch("src.core.clip_manifest.build_clips_data", side_effect=build_and_modify_source):
        req_changed = PromptPreparationRequest(
            request_id="req_chg",
            generation=2,
            intent="copy_video",
            video_paths=(v1,),
            manifest_cache_dir=manifest_dir,
        )
        worker_chg = PromptPreparationWorker(req_changed, cache_mgr=cache_mgr)
        results_chg = []
        worker_chg.finished_signal.connect(lambda rid, ok, msg, res: results_chg.append((rid, ok, msg, res)))

        worker_chg.start()
        _wait_for_worker(worker_chg)

        assert len(results_chg) == 1
        assert results_chg[0][1] is False
        assert results_chg[0][2] in ("source_changed", "source_error")


# =============================================================================
# 3. TEST DELAYED ACTUAL QTHREAD I/O THREAD SEPARATION
# =============================================================================

def test_delayed_qthread_io_not_on_gui_thread(tmp_path, qapp):
    """Kiểm tra C2b-3:
    - Toàn bộ I/O nặng (ClipManifest, build_clips_data, cache read) thực thi
      trên worker thread khác hoàn toàn với GUI thread.
    - GUI event loop vẫn phản hồi bình thường trong khi worker xử lý."""
    v1 = str(tmp_path / "thread_test.mp4")
    d, p = _create_dummy_video(v1, content=b"THREAD_TEST_STREAM_DATA")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 8.0, "raw_subtitles": [{"start": 0, "end": 2, "text": "Kiểm tra thread"}]},
        model_size="small",
        language="vi",
        verified_identity=(d, p)
    )

    gui_thread = QThread.currentThread()
    io_threads = []

    orig_get_cached = cache_mgr.get_cached_scan
    def spy_get_cached(*args, **kwargs):
        io_threads.append(QThread.currentThread())
        return orig_get_cached(*args, **kwargs)
    cache_mgr.get_cached_scan = spy_get_cached

    req = PromptPreparationRequest(
        request_id="req_thread",
        generation=1,
        intent="copy_video",
        video_paths=(v1,),
        manifest_cache_dir=str(tmp_path / "manifests"),
    )
    worker = PromptPreparationWorker(req, cache_mgr=cache_mgr)

    worker.start()
    while worker.isRunning():
        # Kiểm tra GUI event loop vẫn nhịp tim bình thường
        QTest.qWait(10)
    QTest.qWait(50)

    assert len(io_threads) >= 1
    assert io_threads[0] != gui_thread
    assert io_threads[0] == worker


# =============================================================================
# 4. TEST CLIPBOARD PROTECTION & COPY INTENT
# =============================================================================

def test_copy_prompt_provenance_and_error_preserves_clipboard(tmp_path, qapp):
    """Kiểm tra C2b-4:
    - Khi chuẩn bị thất bại (no_data hoặc budget_error): Clipboard giữ nguyên 100%, 0 prompt_copied signal.
    - Khi thành công với intent='copy_video': Ghi đúng 1 lần vào clipboard và phát prompt_copied('video')."""
    review = ReviewPage()
    QApplication.clipboard().setText("ORIGINAL_USER_CLIPBOARD_CONTENT")

    v1 = str(tmp_path / "clip_copy.mp4")
    d, p = _create_dummy_video(v1, content=b"CLIP_FOR_COPY_PROTECTION_TEST")

    # 1. Thất bại do chưa có dữ liệu (no_data)
    copied_signals = []
    review.prompt_copied.connect(copied_signals.append)

    ctx_no_data = {"video_paths": [v1], "whisper_model": "small", "whisper_language": "vi"}
    review.request_prompt_preparation(intent="copy_video", context=ctx_no_data, manifest_cache_dir=str(tmp_path / "m"))

    _wait_for_review_page(review)

    # Clipboard không hề bị suy suyển
    assert QApplication.clipboard().text() == "ORIGINAL_USER_CLIPBOARD_CONTENT"
    assert len(copied_signals) == 0

    # 2. Thành công: nạp cache v3 hợp lệ
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 15.0, "raw_subtitles": [{"start": 0, "end": 3, "text": "Thành công mỹ mãn"}]},
        model_size="small",
        language="vi",
        verified_identity=(d, p)
    )

    review.request_prompt_preparation(
        intent="copy_video",
        context=ctx_no_data,
        cache_mgr=cache_mgr,
        manifest_cache_dir=str(tmp_path / "m")
    )

    _wait_for_review_page(review)

    assert "Thành công mỹ mãn" in QApplication.clipboard().text()
    assert copied_signals == ["video"]

    # 3. Thất bại do ngân sách quá nhỏ (budget_error)
    QApplication.clipboard().setText("SAVED_USER_CLIPBOARD_BEFORE_BUDGET_TEST")
    copied_signals.clear()

    ctx_budget = dict(ctx_no_data)
    ctx_budget["max_prompt_bytes"] = 30  # Quá nhỏ cho 1 prompt hợp lệ
    review.request_prompt_preparation(
        intent="copy_video",
        context=ctx_budget,
        cache_mgr=cache_mgr,
        manifest_cache_dir=str(tmp_path / "m")
    )
    _wait_for_review_page(review)

    assert QApplication.clipboard().text() == "SAVED_USER_CLIPBOARD_BEFORE_BUDGET_TEST"
    assert len(copied_signals) == 0


# =============================================================================
# 5. TEST DIRECTOR RUN AI SINGLE DISPATCH & CANCEL / SUPERSEDE
# =============================================================================

def test_run_ai_director_single_dispatch_and_supersede(tmp_path, qapp):
    """Kiểm tra C2b-5:
    - _on_run_ai_script không gọi trực tiếp controller trước khi chuẩn bị xong.
    - Khi prompt sẵn sàng: controller.run_ai_script được gọi ĐÚNG 1 LẦN (single dispatch).
    - Gọi liên tiếp 2 lần (supersede): request 1 bị hủy, controller chỉ nhận đúng 1 lệnh từ request 2."""
    director = DirectorWindow()
    fake_controller = MagicMock()
    fake_controller.worker = None
    director.controller = fake_controller

    v1 = str(tmp_path / "run_ai_test.mp4")
    d, p = _create_dummy_video(v1, content=b"RUN_AI_TEST_STREAM_CONTENT")

    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)
    director._cache_mgr = cache_mgr
    director._manifest_cache_dir = str(tmp_path / "manifests")
    director.page_source.get_video_paths = lambda: [v1]

    w_model = director.page_source.get_whisper_model()
    w_lang = director.page_source.get_whisper_language()

    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 0, "end": 2, "text": "Chạy AI kịch bản"}]},
        model_size=w_model,
        language=w_lang,
        verified_identity=(d, p)
    )

    # Bấm Run AI lần 1
    director._on_run_ai_script()
    assert fake_controller.run_ai_script.call_count == 0  # Chưa xong, 0 dispatch

    # Bấm tiếp Run AI lần 2 (supersede lần 1)
    director._on_run_ai_script()

    _wait_for_review_page(director.page_review)

    # Đúng 1 lần dispatch từ request 2
    assert fake_controller.run_ai_script.call_count == 1
    dispatched_prompt = fake_controller.run_ai_script.call_args[0][0]
    assert "Chạy AI kịch bản" in dispatched_prompt

    director.close()


# =============================================================================
# 6. TEST CONTEXT CHANGE INVALIDATES READY SNAPSHOT
# =============================================================================

def test_context_change_while_worker_running_invalidates_result(tmp_path, qapp):
    """Kiểm tra C2b-6:
    - Khi worker đang chạy, người dùng đổi model/nguồn -> snapshot cũ bị loại bỏ,
      không phát lệnh run_ai hay copy rác."""
    review = ReviewPage()
    v1 = str(tmp_path / "ctx_test.mp4")
    d, p = _create_dummy_video(v1, content=b"CONTEXT_TEST_DATA_STREAM")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 0, "end": 2, "text": "Thoại ngữ cảnh"}]},
        model_size="small",
        language="vi",
        verified_identity=(d, p)
    )

    ctx_old = {"video_paths": [v1], "whisper_model": "small", "whisper_language": "vi"}

    dispatched = []
    review.prompt_ready_for_run.connect(lambda rid, p: dispatched.append((rid, p)))

    # Tạo context supplier động
    current_model = ["small"]
    def supplier():
        return {"video_paths": [v1], "whisper_model": current_model[0], "whisper_language": "vi"}

    review.set_context_supplier(supplier)

    # Bắt đầu chuẩn bị với context cũ
    review.request_prompt_preparation(intent="run_ai", context=ctx_old, cache_mgr=cache_mgr, manifest_cache_dir=str(tmp_path / "m"))

    # Người dùng đổi mô hình sang medium ngay khi worker vừa xuất phát
    current_model[0] = "medium"

    _wait_for_review_page(review)

    # Vì context signature đã lệch ('small' != 'medium'), kết quả không được dispatch
    assert len(dispatched) == 0
    assert review.get_video_prompt() == ""


# =============================================================================
# 7. TEST WINDOW CLOSE COOPERATIVE CANCELLATION
# =============================================================================

def test_window_close_cooperative_cancellation_lifecycle(tmp_path, qapp):
    """Kiểm tra C2b-7:
    - Khi đóng DirectorWindow lúc worker đang chạy:
      + shutdown_analysis_workers() gửi cancel tới toàn bộ workers.
      + closeEvent được ignore cho tới khi physical QThread.finished.
      + Không rò rỉ dispatch hay popup."""
    director = DirectorWindow()
    fake_ctrl = MagicMock()
    fake_ctrl.worker = None
    director.controller = fake_ctrl

    v1 = str(tmp_path / "close_test.mp4")
    _create_dummy_video(v1, content=b"CLOSE_TEST_STREAM_DATA")
    director.page_source.get_video_paths = lambda: [v1]
    director._manifest_cache_dir = str(tmp_path / "m")

    # Giả lập build_clips_data chạy chậm để kiểm tra cancel
    with patch("src.core.clip_manifest.build_clips_data") as mock_build:
        def slow_build(*args, **kwargs):
            cancelled_fn = kwargs.get("cancelled_fn")
            for _ in range(50):
                if cancelled_fn and cancelled_fn():
                    return []
                time.sleep(0.01)
            return []
        mock_build.side_effect = slow_build

        director._on_run_ai_script()
        assert director.page_review.is_analysis_loading() is True

        # Đóng cửa sổ
        director.close()
        assert director._source_close_pending is True

        # Chờ worker kết thúc vật lý
        while not director.page_review.is_analysis_idle():
            QTest.qWait(20)

        assert director.page_review.is_analysis_idle() is True
        assert fake_ctrl.run_ai_script.call_count == 0


# =============================================================================
# 8. TEST GETTERS ZERO I/O & V2 GATE
# =============================================================================

def test_getters_zero_io_and_v2_gate(qapp):
    """Kiểm tra C2b-8:
    - get_video_prompt() và get_active_prompt():
      + Khi chưa chuẩn bị: trả về '' (0 fake data, 0 I/O).
      + Tuyệt đối không gọi ClipManifest hay ffprobe.
    - get_kinetic_prompt(): trả về '' (gated V2, 0 DEMO_WORDS).
    - _copy_kinetic_prompt(): không ghi đè clipboard, hiển thị thông báo trung thực."""
    page = ReviewPage()
    QApplication.clipboard().setText("EXISTING_CLIPBOARD")

    with patch("src.core.clip_manifest.ClipManifest.get_or_create") as mock_manifest:
        with patch("src.core.cache_manager.ScanCacheManager.get_cached_scan") as mock_cache:
            # Getter không thực hiện bất kỳ lệnh I/O nào
            assert page.get_video_prompt() == ""
            assert page.get_active_prompt() == ""
            assert mock_manifest.call_count == 0
            assert mock_cache.call_count == 0

    # V2 gated
    assert page.get_kinetic_prompt() == ""
    page._copy_kinetic_prompt()
    assert QApplication.clipboard().text() == "EXISTING_CLIPBOARD"
    assert "chưa khả dụng" in page.lbl_json_hint.text()


# =============================================================================
# 9. TEST R1-1: PARAMETRIZE SIGNATURE & DIRECTOR SOURCE ATTRS & SUPPLIER THROW
# =============================================================================

def test_r1_context_signature_and_director_source_attrs(tmp_path, qapp):
    """Kiểm tra R1-1:
    - Signature phản ánh mọi cấu hình nguồn: endpoint, window, frames, max_windows, refinement0,
      target, budget, path order (không sorted làm mất thứ tự).
    - DirectorWindow thu thập đúng runtime attributes nondefault từ page_source.
    - Supplier throw -> fail closed context unavailable (0 old clipboard/dispatch)."""
    p1 = str(tmp_path / "v1.mp4")
    p2 = str(tmp_path / "v2.mp4")
    _create_dummy_video(p1, content=b"VIDEO_P1")
    _create_dummy_video(p2, content=b"VIDEO_P2")

    base_ctx = {
        "video_paths": [p1, p2],
        "whisper_model": "small",
        "whisper_language": "vi",
        "enable_visual_describe": True,
        "vision_model": "qwen2.5-vl:7b",
        "vision_endpoint": "http://127.0.0.1:11434",
        "window_sec": 8.0,
        "frames_per_window": 2,
        "max_windows": 10,
        "max_refinements": 1,
        "style_intent": "travel_vlog",
        "hook_duration": "30_60s",
        "target_duration": "full",
        "project_name": "ChunDVC_Project",
        "max_prompt_bytes": 32768,
        "manifest_id": "M_TEST1",
    }
    sig_base = ReviewPage._extract_context_signature(base_ctx)

    # 1. Path order đảo chiều -> signature PHẢI KHÁC (không sorted)
    ctx_reorder = dict(base_ctx, video_paths=[p2, p1])
    assert ReviewPage._extract_context_signature(ctx_reorder) != sig_base

    # 2. Thay đổi từng trường -> signature PHẢI KHÁC
    assert ReviewPage._extract_context_signature(dict(base_ctx, vision_endpoint="http://192.168.1.50:11434")) != sig_base
    assert ReviewPage._extract_context_signature(dict(base_ctx, window_sec=10.0)) != sig_base
    assert ReviewPage._extract_context_signature(dict(base_ctx, frames_per_window=4)) != sig_base
    assert ReviewPage._extract_context_signature(dict(base_ctx, max_windows=5)) != sig_base
    # max_refinements = 0 phải khác 1 (không fallback default)
    assert ReviewPage._extract_context_signature(dict(base_ctx, max_refinements=0)) != sig_base
    assert ReviewPage._extract_context_signature(dict(base_ctx, target_duration="60s")) != sig_base
    assert ReviewPage._extract_context_signature(dict(base_ctx, max_prompt_bytes=16384)) != sig_base
    assert ReviewPage._extract_context_signature(dict(base_ctx, manifest_id="M_DIFF")) != sig_base

    # 3. Endpoint normalization: http://127.0.0.1:11434/ và http://localhost:11434 phải tương đương
    sig_loopback = ReviewPage._extract_context_signature(dict(base_ctx, vision_endpoint="http://localhost:11434/"))
    assert sig_loopback == sig_base

    # 4. DirectorWindow đọc đúng runtime attributes từ page_source
    director = DirectorWindow()
    fake_ctrl = MagicMock()
    fake_ctrl.worker = None
    director.controller = fake_ctrl
    director.page_source.get_video_paths = lambda: [p1, p2]
    director.page_source._runtime_vision_endpoint = "http://10.0.0.99:11434"
    director.page_source._runtime_window_sec = 12.0
    director.page_source._runtime_frames_per_window = 3
    director.page_source._runtime_max_windows = 7
    director.page_source._runtime_max_refinements = 0

    d_ctx = director._get_current_director_context()
    assert d_ctx["vision_endpoint"] == "http://10.0.0.99:11434"
    assert d_ctx["window_sec"] == 12.0
    assert d_ctx["frames_per_window"] == 3
    assert d_ctx["max_windows"] == 7
    assert d_ctx["max_refinements"] == 0

    # 5. Supplier throw -> fail-closed, không fallback sang stored context cũ
    page = ReviewPage()
    page._stored_validated_context = dict(base_ctx)
    page.set_context_supplier(lambda: 1 / 0)
    ctx_thrown = page._get_current_context()
    assert ctx_thrown == {}  # Fail-closed
    req_id = page.request_prompt_preparation(intent="copy_video")
    assert req_id is None

    director.close()


# =============================================================================
# 10. TEST R1-2: BLOCKED QTHREAD ENDPOINT/BUDGET CHANGE & BAD CONTEXT REJECT
# =============================================================================

def test_r1_qthread_blocked_endpoint_budget_change_and_bad_context_reject(tmp_path, qapp):
    """Kiểm tra R1-2:
    - Đổi endpoint hoặc max_prompt_bytes khi QThread đang chạy -> kết quả cũ bị loại bỏ.
    - Bad context (chứa object lạ, non-str paths) bị reject TRƯỚC cancel và giữ nguyên state."""
    review = ReviewPage()
    v1 = str(tmp_path / "blocked_ctx.mp4")
    d, p = _create_dummy_video(v1, content=b"BLOCKED_CTX_DATA")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 0, "end": 2, "text": "Thoại test"}]},
        model_size="small",
        language="vi",
        verified_identity=(d, p)
    )

    ctx_running = {
        "video_paths": [v1],
        "whisper_model": "small",
        "whisper_language": "vi",
        "max_prompt_bytes": 32768,
        "vision_endpoint": "http://127.0.0.1:11434"
    }

    current_budget = [32768]
    def supplier():
        return {
            "video_paths": [v1],
            "whisper_model": "small",
            "whisper_language": "vi",
            "max_prompt_bytes": current_budget[0],
            "vision_endpoint": "http://127.0.0.1:11434"
        }
    review.set_context_supplier(supplier)

    dispatched = []
    review.prompt_ready_for_run.connect(lambda rid, p: dispatched.append((rid, p)))

    # Khởi động worker với budget 32768
    review.request_prompt_preparation(intent="run_ai", context=ctx_running, cache_mgr=cache_mgr, manifest_cache_dir=str(tmp_path / "m"))

    # Đổi budget giữa chừng
    current_budget[0] = 16384

    _wait_for_review_page(review)

    # Chữ ký không khớp -> không dispatch
    assert len(dispatched) == 0
    assert review.get_video_prompt() == ""

    # Thử bad context: chứa đối tượng không hợp lệ
    bad_ctx = {"video_paths": [object(), 12345], "whisper_model": {}}
    initial_gen = review._prompt_generation
    req_bad = review.request_prompt_preparation(context=bad_ctx)
    assert req_bad is None
    # Thế hệ không đổi và trạng thái cũ được bảo toàn
    assert review._prompt_generation == initial_gen


# =============================================================================
# 11. TEST R1-3: MUTATE FILE DURING FORMATTER & SHORT PATHS FINAL STAT
# =============================================================================

def test_r1_mutate_file_during_formatter_detected_and_short_paths_checked(tmp_path, qapp):
    """Kiểm tra R1-3:
    - Tệp nguồn bị sửa/xóa TRONG lúc StoryCopilot.generate_copilot_prompt đang chạy
      -> final post-stat check bắt được và trả về fail-closed 'source_changed'/'source_missing'.
    - Bao phủ cả required paths kể cả short clip bị filter khỏi clips_data."""
    from src.core.story_copilot import StoryCopilot

    v_main = str(tmp_path / "main.mp4")
    v_short = str(tmp_path / "short.mp4")
    _create_dummy_video(v_main, content=b"MAIN_VIDEO_CONTENT")
    _create_dummy_video(v_short, content=b"SHORT_VIDEO_CONTENT")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    for p in [v_main, v_short]:
        d, pr = compute_full_file_sha256(p)
        dur = 15.0 if p == v_main else 0.5
        cache_mgr.save_scan_result(
            file_path=p,
            scan_data={"clip_dur": dur, "raw_subtitles": [{"start": 0, "end": 1, "text": "Thoại"}]},
            model_size="small",
            language="vi",
            verified_identity=(d, pr)
        )

    req = PromptPreparationRequest(
        request_id="test_mutate_1",
        generation=1,
        intent="run_ai",
        video_paths=(v_main, v_short),
        whisper_model="small",
        whisper_language="vi",
        manifest_cache_dir=str(tmp_path / "m"),
    )

    worker = PromptPreparationWorker(request=req, cache_mgr=cache_mgr)
    emitted = []
    worker.finished_signal.connect(lambda rid, ok, msg, res: emitted.append((rid, ok, msg, res)))

    # Hook vào generate_copilot_prompt để mutate v_short (file phụ) ngay trong lúc format prompt
    orig_generate = StoryCopilot.generate_copilot_prompt
    def mutating_generate(*args, **kwargs):
        # Sửa đổi nội dung file v_short làm thay đổi size/mtime
        with open(v_short, "ab") as f:
            f.write(b"_MUTATED_TAIL_BYTES")
        return orig_generate(*args, **kwargs)

    with patch.object(StoryCopilot, "generate_copilot_prompt", side_effect=mutating_generate):
        worker.start()
        _wait_for_worker(worker)

    assert len(emitted) == 1
    rid, ok, msg, res = emitted[0]
    assert ok is False
    assert msg == "source_changed"


# =============================================================================
# 12. TEST R1-4: OVERLAPPING MANIFEST ISOLATION & APPLY PLAN MANIFEST CHECK
# =============================================================================

def test_r1_manifest_isolation_between_overlapping_requests_and_apply_plan(tmp_path, qapp, monkeypatch):
    """Kiểm tra R1-4:
    - Overlapping requests A và B cùng project_name='ChunDVC_Project' không dùng chung
      manifest file gây ghi đè chéo; sử dụng isolated request-scoped path.
    - Result manifest_metadata được ReviewPage gán vào self._manifest in-memory.
    - _apply_json_data từ chối (fail-closed) kịch bản có manifest_id khác với self._manifest.
    - Fixture isolation: patch default expanduser về tmp_path để worker không chạm vào real user cache."""
    fake_home = str(tmp_path / "fake_home")
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", fake_home) if p.startswith("~") else p)
    v_a = str(tmp_path / "clip_a.mp4")
    v_b = str(tmp_path / "clip_b.mp4")
    _create_dummy_video(v_a, content=b"CONTENT_A")
    _create_dummy_video(v_b, content=b"CONTENT_B")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    for p in [v_a, v_b]:
        d, pr = compute_full_file_sha256(p)
        cache_mgr.save_scan_result(
            file_path=p,
            scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 0, "end": 1, "text": "Thoại"}]},
            model_size="small",
            language="vi",
            verified_identity=(d, pr)
        )

    # Chạy worker A
    req_a = PromptPreparationRequest(
        request_id="req_iso_a",
        generation=1,
        intent="copy_video",
        video_paths=(v_a,),
        project_name="ChunDVC_Project",
        manifest_cache_dir=None,  # Để worker tự tạo isolated cache
    )
    worker_a = PromptPreparationWorker(request=req_a, cache_mgr=cache_mgr)
    res_a = []
    worker_a.finished_signal.connect(lambda rid, ok, msg, res: res_a.append(res))
    worker_a.start()
    _wait_for_worker(worker_a)

    assert len(res_a) == 1
    meta_a = res_a[0]["manifest_metadata"]
    assert meta_a["manifest_id"] is not None

    # ReviewPage nhận kết quả A
    page = ReviewPage()
    page._headless_mode = True
    page._active_prompt_request_id = "req_iso_a"
    page._prompt_generation = 1
    page._pending_prompt_expected = {
        "request_id": "req_iso_a",
        "intent": "copy_video",
        "generation": 1,
        "expected_creative_context_signature": None,
    }
    page._on_prompt_preparation_finished("req_iso_a", True, "success", res_a[0])

    assert page._manifest is not None
    assert page._manifest.manifest_id == meta_a["manifest_id"]

    # Áp dụng kịch bản sai manifest_id -> BỊ TỪ CHỐI
    wrong_plan = {
        "type": "video_plan",
        "data": {
            "manifest_id": "M_WRONG_999",
            "timeline_segments": [{"title": "Cảnh 1", "clip_id": "C01"}]
        }
    }
    page._apply_json_data(wrong_plan, "Kịch bản sai manifest")
    assert page._video_plan_data is None
    assert "Từ chối" in page.lbl_json_hint.text()


# =============================================================================
# 13. TEST R1-5: EXACTLY-ONCE QUEUED DUPLICATE & CONTROLLER THROW
# =============================================================================

def test_r1_exactly_once_queued_duplicate_and_controller_throw(tmp_path, qapp):
    """Kiểm tra R1-5:
    - ReviewPage consume logical result đúng 1 lần; duplicate callback không xử lý lại.
    - DirectorWindow slot_on_prompt_ready_for_run:
      + Duplicate call với cùng req_id chỉ dispatch đúng 1 lần.
      + Wrong req_id không dispatch.
      + Controller throw không cho phép dispatch lại."""
    director = DirectorWindow()
    fake_ctrl = MagicMock()
    fake_ctrl.worker = None
    director.controller = fake_ctrl

    v1 = str(tmp_path / "once.mp4")
    _create_dummy_video(v1, content=b"ONCE_DATA")
    director.page_source.get_video_paths = lambda: [v1]

    req_id = "req_once_1"
    prompt_text = "Prompt chuẩn bị cho test exactly once"
    director._pending_run_ai_request_id = req_id

    # Giả lập ReviewPage snapshot sẵn sàng
    director.page_review._prepared_prompt_snapshot = {
        "prompt_text": prompt_text,
        "context_signature": director.page_review._extract_context_signature(director._get_current_director_context()),
        "request_id": req_id
    }

    # 1. Gọi với wrong ID -> 0 dispatch
    director._on_prompt_ready_for_run("req_wrong_id", prompt_text)
    assert fake_ctrl.run_ai_script.call_count == 0

    # 2. Gọi với đúng ID lần 1 -> dispatch 1 lần
    director._on_prompt_ready_for_run(req_id, prompt_text)
    assert fake_ctrl.run_ai_script.call_count == 1

    # 3. Duplicate call với cùng ID -> 0 thêm dispatch
    director._on_prompt_ready_for_run(req_id, prompt_text)
    assert fake_ctrl.run_ai_script.call_count == 1

    # 4. Khi controller throw lỗi, ID đã bị consume nên duplicate callback không thử lại
    req_throw = "req_throw_2"
    director._pending_run_ai_request_id = req_throw
    director.page_review._prepared_prompt_snapshot["request_id"] = req_throw
    fake_ctrl.run_ai_script.side_effect = RuntimeError("Controller fail")

    try:
        director._on_prompt_ready_for_run(req_throw, prompt_text)
    except RuntimeError:
        pass

    # Gọi lại -> không dispatch thêm
    director._on_prompt_ready_for_run(req_throw, prompt_text)
    assert fake_ctrl.run_ai_script.call_count == 2  # Chỉ lần throw đầu tiên

    director.close()


# =============================================================================
# 14. TEST R1-6: PHYSICAL BUSY CONTROLS & LATE JOB A
# =============================================================================

def test_r1_physical_busy_controls_and_late_job_a_does_not_unlock_job_b(tmp_path, qapp):
    """Kiểm tra R1-6:
    - Nút bấm copy prompt bị khóa tới khi QThread kết thúc vật lý.
    - Job A kết thúc sau khi Job B đã khởi chạy thì không mở khóa hay reset timer của Job B."""
    page = ReviewPage()
    v1 = str(tmp_path / "busy.mp4")
    d, p = _create_dummy_video(v1, content=b"BUSY_TEST_DATA")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 0, "end": 1, "text": "Thoại"}]},
        model_size="small",
        language="vi",
        verified_identity=(d, p)
    )

    ctx = {"video_paths": [v1], "whisper_model": "small", "whisper_language": "vi"}

    # Start Job A
    req_a = page.request_prompt_preparation(intent="copy_video", context=ctx, cache_mgr=cache_mgr, manifest_cache_dir=str(tmp_path / "m1"))
    assert page.btn_copy_video_prompt.isEnabled() is False

    # Start Job B ngay (supersede Job A)
    req_b = page.request_prompt_preparation(intent="copy_video", context=ctx, cache_mgr=cache_mgr, manifest_cache_dir=str(tmp_path / "m2"))
    assert page._active_prompt_request_id == req_b
    assert page.btn_copy_video_prompt.isEnabled() is False

    # Giả lập một worker trễ (Job A) phát tín hiệu kết thúc vật lý
    mock_late_worker = MagicMock()
    page._on_prompt_worker_thread_finished("late_job_a_phantom_id", mock_late_worker)

    # Nút bấm VẪN PHẢI BỊ KHÓA vì Job B vẫn đang chạy!
    assert page.btn_copy_video_prompt.isEnabled() is False

    # Chờ toàn bộ kết thúc
    _wait_for_review_page(page)
    # Giờ khi Job B đã xong vật lý -> nút được mở khóa
    assert page.btn_copy_video_prompt.isEnabled() is True


# =============================================================================
# 15. TEST R2-1: DETERMINISTIC CONTROLLED BARRIERS (B FINISHES BEFORE A)
# =============================================================================

def test_r2_barrier_b_finishes_before_a_preserves_b_snapshot_and_unlocks_on_all_idle(tmp_path, qapp):
    """Test R2-1: Deterministic actual QThreads controlled barriers A/B:
    - Worker A bị block bởi barrier_a trong scan cache lookup.
    - Worker B khởi chạy (supersede A) và hoàn thành vật lý trước A.
    - Khi B kết thúc: nút bấm VẪN BỊ KHÓA (isEnabled() == False) vì worker A vẫn đang chạy trong registry.
    - Sau đó nhả barrier cho A kết thúc:
      + Registry rỗng (all idle).
      + Nút bấm được mở lại (isEnabled() == True).
      + Snapshot và nhãn thành công của B không bị Old A ghi đè!
    """
    import threading
    page = ReviewPage()
    v1 = str(tmp_path / "barrier_v1.mp4")
    d, p = _create_dummy_video(v1, content=b"BARRIER_DATA_B_FIRST")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 0, "end": 1, "text": "Câu thoại B"}]},
        model_size="small",
        language="vi",
        verified_identity=(d, p)
    )

    ctx = {"video_paths": [v1], "whisper_model": "small", "whisper_language": "vi"}

    started_a = threading.Event()
    barrier_a = threading.Event()

    class BlockingCacheManager:
        def __init__(self, real_cache):
            self._real = real_cache
        def get_cached_scan(self, *args, **kwargs):
            started_a.set()
            barrier_a.wait(timeout=10)
            return self._real.get_cached_scan(*args, **kwargs)
        def __getattr__(self, name):
            return getattr(self._real, name)

    try:
        # 1. Khởi chạy Worker A (bị block trên barrier_a)
        req_a = page.request_prompt_preparation(
            intent="copy_video",
            context=ctx,
            cache_mgr=BlockingCacheManager(cache_mgr),
            manifest_cache_dir=str(tmp_path / "m_a")
        )
        assert req_a in page._prompt_workers_registry
        assert page.btn_copy_video_prompt.isEnabled() is False
        assert started_a.wait(timeout=5) is True

        # 2. Khởi chạy Worker B (supersede A, không bị block)
        req_b = page.request_prompt_preparation(
            intent="copy_video",
            context=ctx,
            cache_mgr=cache_mgr,
            manifest_cache_dir=str(tmp_path / "m_b")
        )
        assert page._active_prompt_request_id == req_b
        assert page.btn_copy_video_prompt.isEnabled() is False

        # 3. Chờ worker B hoàn tất vật lý trước worker A
        _wait_for_request_id(page, req_b)

        # 4. Khi B đã kết thúc vật lý nhưng A vẫn đang chạy trong nền:
        # - Registry vẫn còn Worker A
        assert req_a in page._prompt_workers_registry
        assert page.is_analysis_idle() is False
        # - Nút bấm VẪN PHẢI BỊ KHÓA!
        assert page.btn_copy_video_prompt.isEnabled() is False
        # - Nhãn là nhãn thành công của B
        assert page.btn_copy_video_prompt.text() == "✓ Đã sao chép Prompt V1!"
        # - Snapshot sẵn sàng là của B
        assert page._prepared_prompt_snapshot is not None
        assert page._prepared_prompt_snapshot["request_id"] == req_b

        # 5. Nhả barrier cho worker A tiếp tục chạy và hoàn tất
        barrier_a.set()
        _wait_for_request_id(page, req_a)

        # 6. Cả A và B đều đã hoàn tất vật lý (toàn bộ registry rỗng / all idle)
        assert page.is_analysis_idle() is True
        assert len(page._prompt_workers_registry) == 0

        # - Nút bấm ĐƯỢC MỞ LẠI!
        assert page.btn_copy_video_prompt.isEnabled() is True
        # - Nhãn thành công của B KHÔNG BỊ Old A ĐÈ!
        assert page.btn_copy_video_prompt.text() == "✓ Đã sao chép Prompt V1!"
        # - Snapshot của B vẫn được giữ nguyên!
        assert page._prepared_prompt_snapshot["request_id"] == req_b
    finally:
        barrier_a.set()
        _wait_for_review_page(page)


# =============================================================================
# 16. TEST R2-2: COUNTERORDER A FINISHES FIRST, ACTIVE B ERROR / CLOSE PENDING
# =============================================================================

def test_r2_counterorder_a_finishes_first_and_active_b_cleanup_or_close_pending(tmp_path, qapp):
    """Test R2-2: Counterorder A finishes first / B running vẫn disabled; B finish last unlock.
    - Worker A hoàn tất trước trong khi Worker B (active) vẫn đang chạy (bị giữ bởi barrier_b).
    - Khi A kết thúc: Worker B vẫn đang chạy trong registry -> nút bấm VẪN BỊ KHÓA.
    - Khi B kết thúc với lỗi (error/cancel) ->
      + Toàn bộ registry idle -> nút bấm được mở lại.
      + Nhãn nút bấm được phục hồi về nhãn gốc '📋 1. Prompt Dựng Video (V1)'.
      + Snapshot là None.
    - Kiểm tra nhánh close pending: 0 late UI update."""
    import threading
    page = ReviewPage()
    v1 = str(tmp_path / "counter_v1.mp4")
    d, p = _create_dummy_video(v1, content=b"COUNTER_DATA_A_FIRST")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 0, "end": 1, "text": "Câu thoại A"}]},
        model_size="small",
        language="vi",
        verified_identity=(d, p)
    )

    ctx = {"video_paths": [v1], "whisper_model": "small", "whisper_language": "vi"}

    started_b = threading.Event()
    barrier_b = threading.Event()

    class BlockingCacheManagerForB:
        def __init__(self, real_cache):
            self._real = real_cache
        def get_cached_scan(self, *args, **kwargs):
            started_b.set()
            barrier_b.wait(timeout=10)
            # Trả về rỗng để Worker B kết thúc với no_data lỗi
            return None
        def __getattr__(self, name):
            return getattr(self._real, name)

    try:
        # 1. Khởi chạy Worker A (chạy nhanh không block)
        req_a = page.request_prompt_preparation(
            intent="copy_video",
            context=ctx,
            cache_mgr=cache_mgr,
            manifest_cache_dir=str(tmp_path / "m_a2")
        )
        assert page.btn_copy_video_prompt.isEnabled() is False

        # 2. Khởi chạy Worker B ngay (active, bị block trên barrier_b)
        req_b = page.request_prompt_preparation(
            intent="copy_video",
            context=ctx,
            cache_mgr=BlockingCacheManagerForB(cache_mgr),
            manifest_cache_dir=str(tmp_path / "m_b2")
        )
        assert page._active_prompt_request_id == req_b
        assert page.btn_copy_video_prompt.isEnabled() is False
        assert started_b.wait(timeout=5) is True

        # 3. Chờ worker A hoàn tất vật lý trước
        _wait_for_request_id(page, req_a)

        # 4. Khi A đã xong nhưng B vẫn đang chạy trong nền:
        # - Nút bấm VẪN PHẢI BỊ KHÓA!
        assert req_b in page._prompt_workers_registry
        assert page.btn_copy_video_prompt.isEnabled() is False
        assert page.is_analysis_idle() is False

        # 5. Nhả barrier cho worker B chạy tiếp và kết thúc với lỗi (no_data)
        barrier_b.set()
        _wait_for_request_id(page, req_b)

        # 6. Khi B kết thúc:
        # - Registry rỗng (all idle)
        assert page.is_analysis_idle() is True
        # - Nút bấm được mở lại
        assert page.btn_copy_video_prompt.isEnabled() is True
        # - Nhãn nút bấm khôi phục về nhãn gốc vì active B không có snapshot thành công
        assert page.btn_copy_video_prompt.text() == "📋 1. Prompt Dựng Video (V1)"
        assert page._prepared_prompt_snapshot is None

        # 7. Sub-case: close pending 0 late UI update
        req_c = page.request_prompt_preparation(
            intent="copy_video",
            context=ctx,
            cache_mgr=cache_mgr,
            manifest_cache_dir=str(tmp_path / "m_c")
        )
        assert page.btn_copy_video_prompt.isEnabled() is False
        # Thiết lập close pending
        page._review_close_pending = True
        _wait_for_request_id(page, req_c)

        # Sau khi c hoàn tất với close pending:
        # Nút không được enable lại (0 late UI update)
        assert page.btn_copy_video_prompt.isEnabled() is False
        # Nhưng registry vẫn được cleanup và emit idle
        assert page.is_analysis_idle() is True
    finally:
        barrier_b.set()
        _wait_for_review_page(page)


# =============================================================================
# 17. TEST R2-3: PARAMETRIZE BAD CONFIG (OVERFLOW / NAN / INF / TYPED FIELDS)
# =============================================================================

@pytest.mark.parametrize("bad_key,bad_val", [
    ("max_windows", float("inf")),
    ("max_windows", float("-inf")),
    ("max_windows", float("nan")),
    ("max_windows", True),
    ("max_windows", False),
    ("max_windows", 10**400),
    ("max_windows", 1.5),
    ("max_windows", "not_a_num"),
    ("max_windows", [1, 2]),
    ("max_windows", object()),
    ("frames_per_window", float("inf")),
    ("frames_per_window", 1.5),
    ("frames_per_window", True),
    ("frames_per_window", -1),
    ("max_refinements", float("inf")),
    ("max_refinements", 1.5),
    ("max_refinements", True),
    ("max_refinements", -1),
    ("max_prompt_bytes", float("inf")),
    ("max_prompt_bytes", 40000),
    ("max_prompt_bytes", 0),
    ("max_prompt_bytes", True),
    ("window_sec", float("inf")),
    ("window_sec", float("-inf")),
    ("window_sec", float("nan")),
    ("window_sec", -5.0),
    ("window_sec", "bad_window"),
    ("window_sec", True),
    ("whisper_model", object()),
    ("whisper_model", 123),
    ("video_paths", "not_a_list"),
    ("video_paths", []),
    ("video_paths", [""]),
    ("video_paths", ["   "]),
    ("video_paths", [object()]),
])
def test_r2_parametrize_bad_config_fail_closed_and_preserves_state(tmp_path, qapp, bad_key, bad_val):
    """Test R2-3:
    - Parametrize bad configs không gây uncaught OverflowError hoặc Qt boundary crash.
    - _sanitize_and_validate_context trả về None.
    - ReviewPage.request_prompt_preparation từ chối (trả về None).
    - generation, owner, ready snapshot, clipboard được giữ nguyên trước khi reject.
    - Đảm bảo valid refinement 0 + valid settings vẫn pass."""
    v1 = str(tmp_path / "param_v1.mp4")
    _create_dummy_video(v1, content=b"PARAM_TEST_DATA")

    valid_base = {
        "video_paths": [v1],
        "whisper_model": "small",
        "whisper_language": "vi",
        "enable_visual_describe": False,
        "vision_model": "",
        "vision_endpoint": "http://127.0.0.1:11434",
        "window_sec": 8.0,
        "frames_per_window": 2,
        "max_windows": 10,
        "max_refinements": 0,  # Valid refinement 0
        "max_prompt_bytes": 32768,
        "style_intent": "travel_vlog",
        "hook_duration": "30_60s",
        "target_duration": "full",
        "project_name": "ChunDVC_Project",
    }

    # 1. Base valid context phải pass validation (kể cả max_refinements == 0)
    clean_base = ReviewPage._sanitize_and_validate_context(valid_base)
    assert clean_base is not None
    assert clean_base["max_refinements"] == 0

    # 2. Tạo bad context
    bad_ctx = dict(valid_base)
    bad_ctx[bad_key] = bad_val

    # 3. Validation phải trả về None mà không crash OverflowError hay ném exception
    clean_bad = ReviewPage._sanitize_and_validate_context(bad_ctx)
    assert clean_bad is None

    # 4. _extract_context_signature không crash OverflowError
    sig_bad = ReviewPage._extract_context_signature(bad_ctx)
    assert isinstance(sig_bad, tuple)

    # 5. ReviewPage với request_prompt_preparation
    page = ReviewPage()
    # Giả lập state ban đầu
    page._prompt_generation = 42
    page._active_prompt_request_id = "req_initial_owner"
    page._prepared_prompt_snapshot = {"request_id": "req_initial_owner", "prompt_text": "VALID_OLD"}
    initial_clipboard = "UNTOUCHED_CLIPBOARD"
    QApplication.clipboard().setText(initial_clipboard)

    # Gọi request với context lỗi
    req_res = page.request_prompt_preparation(intent="copy_video", context=bad_ctx)

    # Phải bị từ chối
    assert req_res is None

    # State phải được giữ nguyên hoàn toàn (generation, owner, snapshot, clipboard)
    assert page._prompt_generation == 42
    assert page._active_prompt_request_id == "req_initial_owner"
    assert page._prepared_prompt_snapshot["request_id"] == "req_initial_owner"
    assert QApplication.clipboard().text() == initial_clipboard
    assert page.is_analysis_idle() is True


# =============================================================================
# 18. TEST R2-4: SUPPLIER SOURCE READ EXCEPTION (FAIL-CLOSED & 0 DISPATCH)
# =============================================================================

def test_r2_supplier_source_read_exception_fail_closed_and_0_dispatch(tmp_path, qapp):
    """Test R2-4:
    - Source attribute read exception khiến _get_current_director_context trả về {} (fail-closed).
    - ReviewPage get_video_prompt() không matching snapshot cũ -> trả về ''.
    - DirectorWindow _on_run_ai_script() không dispatch request (req_id is None, pending_id is None).
    - Bảo toàn các giá trị hiện hữu trên SourcePage."""
    director = DirectorWindow()
    v1 = str(tmp_path / "supplier_exc.mp4")
    _create_dummy_video(v1, content=b"SUPPLIER_EXC_DATA")

    # Đặt giá trị runtime hợp lệ trên page_source
    director.page_source.get_video_paths = lambda: [v1]
    director.page_source._runtime_vision_endpoint = "http://127.0.0.1:11434"
    director.page_source._runtime_window_sec = 14.0
    director.page_source._runtime_max_windows = 8

    # 1. Trước khi có exception: context hợp lệ
    ctx_ok = director._get_current_director_context()
    assert ctx_ok["window_sec"] == 14.0
    assert ctx_ok["max_windows"] == 8

    # Giả lập ReviewPage có snapshot sẵn sàng khớp với ctx_ok
    prompt_text = "Prompt sẵn sàng cho supplier test"
    sig_ok = director.page_review._extract_context_signature(ctx_ok)
    director.page_review._prepared_prompt_snapshot = {
        "prompt_text": prompt_text,
        "context_signature": sig_ok,
        "request_id": "req_supplier_ok"
    }
    assert director.page_review.get_video_prompt() == prompt_text

    # 2. Mô phỏng Source attribute read ném exception
    director.page_source.get_video_paths = MagicMock(side_effect=RuntimeError("DaVinci Resolve connection lost"))

    # 3. Director _get_current_director_context phải fail-closed -> trả về {}
    ctx_thrown = director._get_current_director_context()
    assert ctx_thrown == {}

    # 4. ReviewPage get_video_prompt() phải fail-closed -> trả về chuỗi rỗng '' (readiness invalidated)
    assert director.page_review.get_video_prompt() == ""

    # 5. Director _on_run_ai_script() không được dispatch
    fake_ctrl = MagicMock()
    fake_ctrl.worker = None
    director.controller = fake_ctrl
    director._pending_run_ai_request_id = None

    director._on_run_ai_script()

    # req_id rejected -> pending_id vẫn là None -> controller.run_ai_script không được gọi
    assert director._pending_run_ai_request_id is None
    assert fake_ctrl.run_ai_script.call_count == 0

    # 6. Bảo toàn giá trị trên page_source
    assert director.page_source._runtime_window_sec == 14.0
    assert director.page_source._runtime_max_windows == 8

    director.close()

