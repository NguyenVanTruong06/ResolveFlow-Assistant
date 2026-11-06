"""
tests/ui/test_prompt_worker_creative_binding.py
=============================================================================
Finite Unit Test Suite cho PromptPreparationWorker Creative Context Binding (G06-06C3-S2a-R1):
1. Construction-time ownership:
   - Deep normalized owned CreativeContext & ReferenceItem instances created at __init__.
   - Isolation from top-level field mutations on request.creative_context.
   - Isolation from nested ReferenceItem.user_note mutations on request.
   - Isolation from mutable mapping downgraded onto request before construction.
   - Isolation at controlled after-start barrier.
   - Controlled capture of malformed context at construction -> exactly 1 invalid_config before IO.
2. Actual post-format evidence:
   - Nonempty context calling original StoryCopilot.generate_copilot_prompt.
   - Post-format source file deletion -> source_missing, 0 success.
   - Post-format source file mutation -> source_changed, 0 success.
   - Post-format worker.cancel -> cancelled, 0 success.
3. Safe isolated worker tests:
   - Monotonic deadline, physical completion assertions (not isRunning, isFinished).
   - Queued signal flush, try/finally cooperative cancel/wait for ALL started workers.
   - Isolated HOME/XDG/cache, mocked chronology aliases, zero subprocess/locator calls.
4. Legacy parity:
   - None/empty context preserves legacy result payload (no signature field).
   - Supported intent parity: copy_video and run_ai have identical prompt and signature.
   - Unsupported intent gate rejects before IO.
=============================================================================
"""

import os
import time
import pytest
from fractions import Fraction
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from src.core.contracts.edit_plan_contracts import (
    CreativeContext,
    ReferenceItem,
    normalize_creative_context,
    compute_creative_context_signature,
)
from src.core.cache_manager import ScanCacheManager, compute_full_file_sha256
from src.core.geometry.local_metadata_acquisition import (
    LocalHeaderAcquisitionResult,
    SourceFreshnessStat,
)
from src.core.geometry.source_frame_evidence_contracts import VideoStreamHeader
from src.core.contracts.time_contracts import RationalFPS
from src.core.story_copilot import StoryCopilot
from src.ui.director.workers.prompt_preparation_worker import (
    PromptPreparationWorker,
    PromptPreparationRequest,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _isolate_worker_environment(tmp_path, monkeypatch):
    """Cô lập toàn diện môi trường kiểm thử: HOME, XDG, chronology sorter, ffprobe locator."""
    fake_home = str(tmp_path / "fake_home")
    os.makedirs(fake_home, exist_ok=True)
    monkeypatch.setenv("HOME", fake_home)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "fake_xdg_config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "fake_xdg_cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "fake_xdg_data"))
    monkeypatch.setenv("RESOLVEFLOW_VISUAL_CACHE_DIR", str(tmp_path / "fake_visual_cache"))

    # Giả lập acquire_local_video_header để tránh gọi ffprobe thật trên video giả lập
    def _mock_acquire(file_path, *, ffprobe_path=None, selected_stream_index=None, timeout_sec=5.0, cancelled_fn=None):
        if cancelled_fn and cancelled_fn():
            raise InterruptedError("ERR_PROBE_CANCELLED")
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

    # Giả lập chronological_sorter để tránh lệnh subprocess trích xuất creation_time từ metadata container
    import src.core.chronological_sorter as cs
    monkeypatch.setattr(cs, "get_clip_shot_time", lambda path, **kw: ("2026-08-31T12:00:00", "mtime"))
    monkeypatch.setattr(cs, "sort_clips_chronologically", lambda paths, **kw: list(paths))

    # Cài đặt tripwire phát hiện bất kỳ lệnh subprocess nào bị gọi ngoài ý muốn
    import subprocess
    def _tripwire_subprocess(*args, **kwargs):
        raise AssertionError(f"TRIPWIRE: subprocess.run được gọi ngoài ý muốn: {args}")
    monkeypatch.setattr(subprocess, "run", _tripwire_subprocess)


def _create_dummy_video(path: str, size: int = 1024) -> tuple[str, str, str]:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"RESOLVEFLOW_TEST_VIDEO_" * (size // 22 + 1))
    abs_p = os.path.normpath(os.path.abspath(path))
    digest, prov = compute_full_file_sha256(abs_p)
    return abs_p, digest, prov


_RETAINED_RUNNING_WORKERS: list[PromptPreparationWorker] = []


def _wait_for_worker(worker: PromptPreparationWorker, timeout_sec: float = 5.0):
    """Chờ worker dừng với deadline monotonic, xả hàng đợi tín hiệu Qt, và xác nhận worker đã dừng vật lý."""
    deadline = time.monotonic() + timeout_sec
    app = QApplication.instance()
    while worker.isRunning() and time.monotonic() < deadline:
        QTest.qWait(20)
        if app is not None:
            app.processEvents()
    if app is not None:
        app.processEvents()
    if worker.isRunning():
        # Giữ tham chiếu mạnh để tránh việc QThread bị hủy C++ khi còn đang chạy ngầm
        _RETAINED_RUNNING_WORKERS.append(worker)
    assert not worker.isRunning(), f"Worker {worker.request.request_id} không dừng vật lý trước deadline {timeout_sec}s"
    assert worker.isFinished()


# =============================================================================
# 1. PromptPreparationRequest Validation & Intent Gating
# =============================================================================

def test_request_positional_compatibility_and_defaults():
    """Hỗ trợ gọi khởi tạo theo vị trí legacy không kèm creative_context (mặc định None)."""
    req = PromptPreparationRequest(
        "req_legacy",
        1,
        "copy_video",
        ("/tmp/v1.mp4",),
    )
    assert req.creative_context is None
    assert req.intent == "copy_video"


def test_request_stores_owned_normalized_context_defensively():
    """Request chuẩn hóa và lưu trữ một bản sao owned bất biến; đột biến dictionary gốc không ảnh hưởng request."""
    mutable_dict = {
        "free_brief": "Original brief",
        "must_keep_rules": ["Rule 1"],
    }
    req = PromptPreparationRequest(
        request_id="req_owned",
        generation=1,
        intent="copy_video",
        video_paths=("/tmp/v1.mp4",),
        creative_context=mutable_dict,
    )

    assert isinstance(req.creative_context, CreativeContext)
    assert req.creative_context.free_brief == "Original brief"

    # Đột biến dictionary gốc
    mutable_dict["free_brief"] = "Mutated brief"
    mutable_dict["must_keep_rules"].append("Rule 2")

    # Giá trị trong request không bị thay đổi
    assert req.creative_context.free_brief == "Original brief"
    assert req.creative_context.must_keep_rules == ("Rule 1",)


def test_request_rejects_nonempty_context_for_unsupported_intents():
    """Tất cả 6 intent chuẩn đều hỗ trợ creative_context không rỗng; intent lạ bị từ chối."""
    nonempty_cc = {"free_brief": "Chỉ thị đạo diễn"}

    # Intent lạ bị từ chối ngay tại Request validation
    with pytest.raises(ValueError, match="ERR_INVALID_INTENT"):
        PromptPreparationRequest(
            request_id="req_unknown",
            generation=1,
            intent="unknown_unsupported_intent",
            video_paths=("/tmp/v1.mp4",),
            creative_context=nonempty_cc,
        )

    # Cả 6 intent chuẩn đều được hỗ trợ context không rỗng trong S2c
    all_supported_intents = (
        "copy_video",
        "run_ai",
        "prepare_batches",
        "copy_batch",
        "validate_batch_draft",
        "copy_project_overview",
    )
    for intent in all_supported_intents:
        req_supported = PromptPreparationRequest(
            request_id=f"req_{intent}_supported",
            generation=1,
            intent=intent,
            video_paths=("/tmp/v1.mp4",),
            creative_context=nonempty_cc,
        )
        assert req_supported.creative_context is not None

    # Empty context hoặc None thì được phép với mọi intent hợp lệ
    for intent in all_supported_intents:
        req_none = PromptPreparationRequest(
            request_id=f"req_{intent}_none",
            generation=1,
            intent=intent,
            video_paths=("/tmp/v1.mp4",),
            creative_context=None,
        )
        assert req_none.creative_context is None

        req_empty = PromptPreparationRequest(
            request_id=f"req_{intent}_empty",
            generation=1,
            intent=intent,
            video_paths=("/tmp/v1.mp4",),
            creative_context={},
        )
        assert req_empty.creative_context == normalize_creative_context({})


def test_request_rejects_invalid_creative_context_types():
    """Từ chối các kiểu dữ liệu không hợp lệ cho creative_context (bool, int, unknown fields)."""
    with pytest.raises(ValueError, match="creative_context phải là Mapping hoặc CreativeContext"):
        PromptPreparationRequest(
            request_id="req_bad",
            generation=1,
            intent="copy_video",
            video_paths=("/tmp/v1.mp4",),
            creative_context=True,
        )

    with pytest.raises(ValueError, match="creative_context không hợp lệ"):
        PromptPreparationRequest(
            request_id="req_bad_dict",
            generation=1,
            intent="copy_video",
            video_paths=("/tmp/v1.mp4",),
            creative_context={"free_brief": 12345},
        )


# =============================================================================
# 2. Worker Construction-Time Ownership & Mutation Protection
# =============================================================================

def test_worker_construction_ownership_prevents_top_field_mutation(tmp_path, qapp):
    """Worker tạo bản sao độc lập tại __init__; đột biến in-place trường cấp 1 của request trước khi start không làm đổi kết quả."""
    video_path, digest, prov = _create_dummy_video(str(tmp_path / "v_owner1.mp4"))
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache1"))
    cache_mgr.save_scan_result(
        file_path=video_path,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 1.0, "end": 3.0, "text": "Thoại test"}]},
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    cc_orig = normalize_creative_context({"free_brief": "Brief gốc bất biến"})
    orig_sig = compute_creative_context_signature(cc_orig)

    req = PromptPreparationRequest(
        request_id="req_top_field_owner",
        generation=1,
        intent="copy_video",
        video_paths=(video_path,),
        manifest_cache_dir=str(tmp_path / "manifests1"),
        creative_context=cc_orig,
    )
    worker = PromptPreparationWorker(request=req, cache_mgr=cache_mgr)

    # Thẩm định snapshot tại constructor là đối tượng mới hoàn toàn
    assert worker._captured_creative_context is not req.creative_context

    # Đột biến in-place trường free_brief của request TRƯỚC KHI start
    object.__setattr__(req.creative_context, "free_brief", "Brief ĐÃ BỊ ĐỘT BIẾN TRƯỚC START")

    finished_records = []
    worker.finished_signal.connect(lambda rid, s, m, r: finished_records.append((s, m, r)))

    try:
        worker.start()
        _wait_for_worker(worker)
    finally:
        if worker.isRunning():
            worker.cancel()
            worker.wait(2000)

    assert len(finished_records) == 1
    success, msg, payload = finished_records[0]
    assert success is True
    assert payload["creative_context_signature"] == orig_sig
    assert "Brief gốc bất biến" in payload["prompt_text"]
    assert "Brief ĐÃ BỊ ĐỘT BIẾN TRƯỚC START" not in payload["prompt_text"]


def test_worker_construction_ownership_prevents_nested_ref_item_mutation(tmp_path, qapp):
    """Worker clone các đối tượng ReferenceItem con tại __init__; đột biến user_note của item trong request không làm đổi kết quả."""
    video_path, digest, prov = _create_dummy_video(str(tmp_path / "v_owner2.mp4"))
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache2"))
    cache_mgr.save_scan_result(
        file_path=video_path,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 1.0, "end": 3.0, "text": "Thoại test"}]},
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    ref_item = ReferenceItem(
        reference_id="ref_nested_01",
        local_path="/abs/path/to/reference.mp4",
        media_type="video",
        role="reference_only",
        user_note="Ghi chú tham khảo gốc",
    )
    cc_orig = normalize_creative_context({
        "free_brief": "Brief kiểm tra item",
        "reference_items": [ref_item],
    })
    orig_sig = compute_creative_context_signature(cc_orig)

    req = PromptPreparationRequest(
        request_id="req_nested_ref_owner",
        generation=1,
        intent="copy_video",
        video_paths=(video_path,),
        manifest_cache_dir=str(tmp_path / "manifests2"),
        creative_context=cc_orig,
    )
    worker = PromptPreparationWorker(request=req, cache_mgr=cache_mgr)

    # Thẩm định reference_items con là instance mới độc lập, không dùng chung pointer
    assert worker._captured_creative_context.reference_items[0] is not req.creative_context.reference_items[0]

    # Đột biến in-place user_note của ReferenceItem con TRƯỚC KHI start
    object.__setattr__(req.creative_context.reference_items[0], "user_note", "Ghi chú ĐÃ BỊ ĐỘT BIẾN")

    finished_records = []
    worker.finished_signal.connect(lambda rid, s, m, r: finished_records.append((s, m, r)))

    try:
        worker.start()
        _wait_for_worker(worker)
    finally:
        if worker.isRunning():
            worker.cancel()
            worker.wait(2000)

    assert len(finished_records) == 1
    success, msg, payload = finished_records[0]
    assert success is True
    assert payload["creative_context_signature"] == orig_sig
    assert "Ghi chú tham khảo gốc" in payload["prompt_text"]
    assert "Ghi chú ĐÃ BỊ ĐỘT BIẾN" not in payload["prompt_text"]


def test_worker_construction_ownership_prevents_mapping_downgrade_mutation(tmp_path, qapp):
    """Nếu request chứa một mapping thô hợp lệ trước khi khởi tạo worker, đột biến mapping sau đó không ảnh hưởng worker."""
    video_path, digest, prov = _create_dummy_video(str(tmp_path / "v_owner3.mp4"))
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache3"))
    cache_mgr.save_scan_result(
        file_path=video_path,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 1.0, "end": 3.0, "text": "Thoại test"}]},
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    raw_mapping = {
        "free_brief": "Mapping brief ban đầu",
        "must_keep_rules": ["Luật mapping 1"],
    }
    req = PromptPreparationRequest(
        request_id="req_mapping_owner",
        generation=1,
        intent="copy_video",
        video_paths=(video_path,),
        manifest_cache_dir=str(tmp_path / "manifests3"),
    )
    # Gán mapping thô trực tiếp lên request trước khi khởi tạo worker
    object.__setattr__(req, "creative_context", raw_mapping)

    worker = PromptPreparationWorker(request=req, cache_mgr=cache_mgr)

    # Đột biến dictionary thô sau khi worker đã khởi tạo
    raw_mapping["free_brief"] = "Mapping brief ĐÃ BỊ THAY ĐỔI"
    raw_mapping["must_keep_rules"].append("Luật mapping lén lút")

    finished_records = []
    worker.finished_signal.connect(lambda rid, s, m, r: finished_records.append((s, m, r)))

    try:
        worker.start()
        _wait_for_worker(worker)
    finally:
        if worker.isRunning():
            worker.cancel()
            worker.wait(2000)

    assert len(finished_records) == 1
    success, msg, payload = finished_records[0]
    assert success is True
    assert "Mapping brief ban đầu" in payload["prompt_text"]
    assert "Mapping brief ĐÃ BỊ THAY ĐỔI" not in payload["prompt_text"]
    assert "Luật mapping lén lút" not in payload["prompt_text"]


def test_worker_caller_mutation_after_start_barrier(tmp_path, qapp):
    """Đột biến toàn bộ trường hoặc đối tượng con của request sau khi start không làm thay đổi prompt đã capture."""
    video_path, digest, prov = _create_dummy_video(str(tmp_path / "v_owner4.mp4"))
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache4"))
    cache_mgr.save_scan_result(
        file_path=video_path,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 1.0, "end": 3.0, "text": "Thoại test"}]},
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    cc_original = normalize_creative_context({"free_brief": "Brief trước start"})
    orig_sig = compute_creative_context_signature(cc_original)

    req = PromptPreparationRequest(
        request_id="req_after_start",
        generation=1,
        intent="copy_video",
        video_paths=(video_path,),
        manifest_cache_dir=str(tmp_path / "manifests4"),
        creative_context=cc_original,
    )
    worker = PromptPreparationWorker(request=req, cache_mgr=cache_mgr)

    finished_records = []
    worker.finished_signal.connect(lambda rid, s, m, r: finished_records.append((s, m, r)))

    try:
        worker.start()
        # Đột biến sau start
        cc_mutated = normalize_creative_context({"free_brief": "Brief đột biến sau start"})
        object.__setattr__(worker.request, "creative_context", cc_mutated)
        _wait_for_worker(worker)
    finally:
        if worker.isRunning():
            worker.cancel()
            worker.wait(2000)

    assert len(finished_records) == 1
    success, msg, payload = finished_records[0]
    assert success is True
    assert payload["creative_context_signature"] == orig_sig
    assert "Brief trước start" in payload["prompt_text"]
    assert "Brief đột biến sau start" not in payload["prompt_text"]


def test_worker_malformed_context_at_construction_emits_controlled_invalid_config(tmp_path, qapp):
    """Context dị dạng truyền vào worker lúc khởi tạo được bắt giữ có kiểm soát, phát invalid_config trong run() trước I/O."""
    req = PromptPreparationRequest(
        request_id="req_malformed_construct",
        generation=1,
        intent="copy_video",
        video_paths=("/non/existent/path.mp4",),
    )
    # Gán giá trị sai kiểu (bool hoặc dict chứa unknown key) lên request
    object.__setattr__(req, "creative_context", True)

    # Khởi tạo worker: KHÔNG ĐƯỢC ném ngoại lệ Qt-constructor
    worker = PromptPreparationWorker(request=req)
    assert worker._captured_creative_error is not None

    finished_records = []
    worker.finished_signal.connect(lambda rid, s, m, r: finished_records.append((s, m, r)))

    try:
        worker.start()
        _wait_for_worker(worker)
    finally:
        if worker.isRunning():
            worker.cancel()
            worker.wait(2000)

    assert len(finished_records) == 1
    success, msg, payload = finished_records[0]
    assert success is False
    assert msg == "invalid_config"
    assert "creative_context phải là Mapping hoặc CreativeContext" in payload.get("error", "")


# =============================================================================
# 3. Actual Post-Format Evidence (Original StoryCopilot Wrapper Tests)
# =============================================================================

def test_worker_post_format_source_deletion_fails_closed(tmp_path, qapp, monkeypatch):
    """Formatter thực tế sinh ra prompt thành công; tệp nguồn bị xóa NGAY SAU formatter -> phát source_missing, 0 success."""
    video_path, digest, prov = _create_dummy_video(str(tmp_path / "v_post_del.mp4"))
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache_post1"))
    cache_mgr.save_scan_result(
        file_path=video_path,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 1.0, "end": 3.0, "text": "Thoại post delete"}]},
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    orig_func = StoryCopilot.generate_copilot_prompt.__func__
    formatter_called = []

    def _wrapper(cls, *args, **kwargs):
        res = orig_func(cls, *args, **kwargs)
        formatter_called.append(True)
        # Xóa tệp nguồn ngay sau khi formatter đã xuất ra prompt
        if os.path.exists(video_path):
            os.remove(video_path)
        return res

    monkeypatch.setattr(StoryCopilot, "generate_copilot_prompt", classmethod(_wrapper))

    req = PromptPreparationRequest(
        request_id="req_post_delete",
        generation=1,
        intent="copy_video",
        video_paths=(video_path,),
        manifest_cache_dir=str(tmp_path / "manifests_post1"),
        creative_context={"free_brief": "Post format delete test"},
    )
    worker = PromptPreparationWorker(request=req, cache_mgr=cache_mgr)

    finished_records = []
    worker.finished_signal.connect(lambda rid, s, m, r: finished_records.append((s, m, r)))

    try:
        worker.start()
        _wait_for_worker(worker)
    finally:
        if worker.isRunning():
            worker.cancel()
            worker.wait(2000)

    # Xác nhận formatter THỰC TẾ đã được gọi
    assert len(formatter_called) == 1
    # Nhưng kết quả bị fail-closed tại final source stat check
    assert len(finished_records) == 1
    success, msg, payload = finished_records[0]
    assert success is False
    assert msg == "source_missing"
    assert "prompt_text" not in payload


def test_worker_post_format_source_mutation_fails_closed(tmp_path, qapp, monkeypatch):
    """Formatter thực tế sinh ra prompt thành công; tệp nguồn bị sửa đổi NGAY SAU formatter -> phát source_changed, 0 success."""
    video_path, digest, prov = _create_dummy_video(str(tmp_path / "v_post_mut.mp4"))
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache_post2"))
    cache_mgr.save_scan_result(
        file_path=video_path,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 1.0, "end": 3.0, "text": "Thoại post mutate"}]},
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    orig_func = StoryCopilot.generate_copilot_prompt.__func__
    formatter_called = []

    def _wrapper(cls, *args, **kwargs):
        res = orig_func(cls, *args, **kwargs)
        formatter_called.append(True)
        # Sửa đổi kích thước/mtime tệp nguồn ngay sau khi formatter đã xuất ra prompt
        with open(video_path, "ab") as f:
            f.write(b"CORRUPTING_EXTRA_BYTES_POST_FORMAT")
        return res

    monkeypatch.setattr(StoryCopilot, "generate_copilot_prompt", classmethod(_wrapper))

    req = PromptPreparationRequest(
        request_id="req_post_mutate",
        generation=1,
        intent="copy_video",
        video_paths=(video_path,),
        manifest_cache_dir=str(tmp_path / "manifests_post2"),
        creative_context={"free_brief": "Post format mutate test"},
    )
    worker = PromptPreparationWorker(request=req, cache_mgr=cache_mgr)

    finished_records = []
    worker.finished_signal.connect(lambda rid, s, m, r: finished_records.append((s, m, r)))

    try:
        worker.start()
        _wait_for_worker(worker)
    finally:
        if worker.isRunning():
            worker.cancel()
            worker.wait(2000)

    # Xác nhận formatter THỰC TẾ đã được gọi
    assert len(formatter_called) == 1
    # Nhưng kết quả bị fail-closed tại final source stat check
    assert len(finished_records) == 1
    success, msg, payload = finished_records[0]
    assert success is False
    assert msg == "source_changed"
    assert "prompt_text" not in payload


def test_worker_post_format_cancel_fails_closed(tmp_path, qapp, monkeypatch):
    """Formatter thực tế sinh ra prompt thành công; worker bị cancel NGAY SAU formatter -> phát cancelled, 0 success."""
    video_path, digest, prov = _create_dummy_video(str(tmp_path / "v_post_cancel.mp4"))
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache_post3"))
    cache_mgr.save_scan_result(
        file_path=video_path,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 1.0, "end": 3.0, "text": "Thoại post cancel"}]},
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    orig_func = StoryCopilot.generate_copilot_prompt.__func__
    formatter_called = []

    req = PromptPreparationRequest(
        request_id="req_post_cancel",
        generation=1,
        intent="copy_video",
        video_paths=(video_path,),
        manifest_cache_dir=str(tmp_path / "manifests_post3"),
        creative_context={"free_brief": "Post format cancel test"},
    )
    worker = PromptPreparationWorker(request=req, cache_mgr=cache_mgr)

    def _wrapper(cls, *args, **kwargs):
        res = orig_func(cls, *args, **kwargs)
        formatter_called.append(True)
        # Hủy worker ngay sau khi formatter đã xuất ra prompt
        worker.cancel()
        return res

    monkeypatch.setattr(StoryCopilot, "generate_copilot_prompt", classmethod(_wrapper))

    finished_records = []
    worker.finished_signal.connect(lambda rid, s, m, r: finished_records.append((s, m, r)))

    try:
        worker.start()
        _wait_for_worker(worker)
    finally:
        if worker.isRunning():
            worker.cancel()
            worker.wait(2000)

    # Xác nhận formatter THỰC TẾ đã được gọi
    assert len(formatter_called) == 1
    # Nhưng kết quả bị dừng do cancel
    assert len(finished_records) == 1
    success, msg, payload = finished_records[0]
    assert success is False
    assert msg == "cancelled"
    assert "prompt_text" not in payload


# =============================================================================
# 4. Parity & Intent Gate Tests (copy_video & run_ai)
# =============================================================================

def test_worker_copy_video_and_run_ai_parity_with_creative_context(tmp_path, qapp):
    """copy_video và run_ai với cùng creative_context tạo ra prompt và signature hoàn toàn đồng nhất."""
    video_path, digest, prov = _create_dummy_video(str(tmp_path / "v_parity.mp4"))
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache_parity"))
    cache_mgr.save_scan_result(
        file_path=video_path,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 1.0, "end": 4.0, "text": "Đoạn thoại mẫu"}]},
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    cc = normalize_creative_context({
        "free_brief": "Yêu cầu đạo diễn dựng vlog du lịch",
        "must_keep_rules": ["Bảo toàn thoại mở đầu"],
    })
    expected_sig = compute_creative_context_signature(cc)

    results = {}
    for intent in ("copy_video", "run_ai"):
        req = PromptPreparationRequest(
            request_id=f"req_{intent}",
            generation=1,
            intent=intent,
            video_paths=(video_path,),
            style_intent="travel_vlog",
            manifest_cache_dir=str(tmp_path / "manifests_parity"),
            creative_context=cc,
        )
        worker = PromptPreparationWorker(request=req, cache_mgr=cache_mgr)

        signals_received = []
        worker.started_signal.connect(lambda rid: signals_received.append(("started", rid)))
        worker.finished_signal.connect(lambda rid, s, m, r: signals_received.append(("finished", rid, s, m, r)))

        try:
            worker.start()
            _wait_for_worker(worker)
        finally:
            if worker.isRunning():
                worker.cancel()
                worker.wait(2000)

        assert len(signals_received) == 2
        assert signals_received[0] == ("started", f"req_{intent}")
        fin = signals_received[1]
        assert fin[0] == "finished"
        assert fin[1] == f"req_{intent}"
        assert fin[2] is True
        assert fin[3] == "success"

        payload = fin[4]
        assert payload["intent"] == intent
        assert payload["creative_context_signature"] == expected_sig
        assert "creative_context" not in payload
        assert "YÊU CẦU ĐẠO DIỄN VÀ NGỮ CẢNH SÁNG TẠO" in payload["prompt_text"]

        results[intent] = payload

    assert results["copy_video"]["prompt_text"] == results["run_ai"]["prompt_text"]
    assert results["copy_video"]["creative_context_signature"] == results["run_ai"]["creative_context_signature"]


def test_worker_legacy_parity_none_and_empty_context(tmp_path, qapp):
    """Khi context là None hoặc rỗng, payload kết quả không chứa trường creative_context_signature."""
    video_path, digest, prov = _create_dummy_video(str(tmp_path / "v_legacy.mp4"))
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache_legacy"))
    cache_mgr.save_scan_result(
        file_path=video_path,
        scan_data={"clip_dur": 8.0, "raw_subtitles": [{"start": 0.5, "end": 3.0, "text": "Thoại ngắn"}]},
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    for cc_input in (None, {}):
        req = PromptPreparationRequest(
            request_id="req_legacy_worker",
            generation=1,
            intent="copy_video",
            video_paths=(video_path,),
            manifest_cache_dir=str(tmp_path / "manifests_legacy"),
            creative_context=cc_input,
        )
        worker = PromptPreparationWorker(request=req, cache_mgr=cache_mgr)

        finished_records = []
        worker.finished_signal.connect(lambda rid, s, m, r: finished_records.append((s, m, r)))

        try:
            worker.start()
            _wait_for_worker(worker)
        finally:
            if worker.isRunning():
                worker.cancel()
                worker.wait(2000)

        assert len(finished_records) == 1
        success, msg, payload = finished_records[0]
        assert success is True
        assert "creative_context_signature" not in payload
        assert "YÊU CẦU ĐẠO DIỄN VÀ NGỮ CẢNH SÁNG TẠO" not in payload["prompt_text"]


def test_worker_unsupported_intent_gate_before_io(tmp_path, qapp):
    """Nếu worker gặp intent không hỗ trợ với context không rỗng, phát unsupported_intent trước IO."""
    req = PromptPreparationRequest(
        request_id="req_bypass",
        generation=1,
        intent="copy_video",
        video_paths=("/non/existent/path.mp4",),
        creative_context={"free_brief": "Context không rỗng"},
    )
    object.__setattr__(req, "intent", "fake_unsupported_intent")

    worker = PromptPreparationWorker(request=req)

    finished_records = []
    worker.finished_signal.connect(lambda rid, s, m, r: finished_records.append((s, m, r)))

    try:
        worker.start()
        _wait_for_worker(worker)
    finally:
        if worker.isRunning():
            worker.cancel()
            worker.wait(2000)

    assert len(finished_records) == 1
    success, msg, payload = finished_records[0]
    assert success is False
    assert msg == "unsupported_intent"
    assert "ERR_CREATIVE_CONTEXT_UNSUPPORTED_INTENT" in payload.get("error", "")


def test_worker_physical_cleanup_and_retained_ownership(tmp_path, qapp):
    """UNIT_RETENTION_VERIFIED: Physical thread cleanup and retained ownership if cooperative join times out."""
    req = PromptPreparationRequest(
        request_id="req_cleanup_direct",
        generation=1,
        intent="copy_video",
        video_paths=("/non/existent/path.mp4",),
    )
    object.__setattr__(req, "intent", "unsupported_fake_intent")

    dummy_worker = PromptPreparationWorker(request=req)
    initial_retained_count = len(_RETAINED_RUNNING_WORKERS)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(dummy_worker, "isRunning", lambda: True)
        mp.setattr(dummy_worker, "isFinished", lambda: False)
        with pytest.raises(AssertionError, match="không dừng vật lý"):
            _wait_for_worker(dummy_worker, timeout_sec=0.05)

    assert len(_RETAINED_RUNNING_WORKERS) == initial_retained_count + 1
    assert _RETAINED_RUNNING_WORKERS[-1] is dummy_worker
