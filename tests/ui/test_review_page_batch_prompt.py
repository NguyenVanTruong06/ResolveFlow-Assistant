"""
tests/ui/test_review_page_batch_prompt.py
=============================================================================
Finite TDD Test Suite cho G06-06A3-R1 (Review Batch Prepare / View / Copy):
- Nhóm A: Copy authority, owned snapshot immutability, exact inode/mtime/ctime/size
  stat recheck, 0 producer/planner calls on copy, budget cap tamper rejection.
- Nhóm B: GUI validation/lifecycle: strict int index check before any mutation,
  discard stale copy on selection change, physical busy aggregate SSOT,
  truthful labels (distinct clips vs partitions/items, unaccounted reasons),
  full preview (0 crop).
- Nhóm C: SSOT trung thực: manifest duration 0 stays unaccounted (0 fake planned),
  StoryCopilot wrapper không fake IDs/times/auto-Resolve promise.
=============================================================================
"""

import os
import stat
import time
import hashlib
import dataclasses
import pytest
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThread, QTimer
from PySide6.QtTest import QTest

from src.core.cache_manager import ScanCacheManager, compute_full_file_sha256
from src.ui.director.workers.prompt_preparation_worker import (
    PromptPreparationWorker, PromptPreparationRequest,
    PreparedBatchSnapshot, PreparedBatchItem, BaselineSourceEntry,
    UnaccountedClipEntry, AdapterIssueEntry,
    validate_and_normalize_snapshot, compute_snapshot_id,
)
from src.ui.director.pages.review_page import ReviewPage


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _mock_acquisition_dependency_for_dummy_files(monkeypatch):
    """Giả lập acquire_local_video_header cho dummy files để tránh gọi ffprobe thật."""
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
            ctime_ns=getattr(st, "st_ctime_ns", 0),
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
            format_duration_sec=Fraction(15, 1),
            freshness=freshness,
        )

    import src.core.geometry.local_metadata_acquisition as lma
    monkeypatch.setattr(lma, "acquire_local_video_header", _mock_acquire)


def _create_dummy_video(path: str, size: int = 2048, content: bytes = None):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    payload = content if content is not None else (b"RESOLVEFLOW_DUMMY_BATCH_" * (size // 24 + 1))[:size]
    with open(path, "wb") as f:
        f.write(payload)
    digest, prov = compute_full_file_sha256(path)
    return digest, prov


def _make_dummy_manifest_metadata(project_name: str, clips_info: list) -> dict:
    """Tạo manifest_metadata hợp lệ với known duration phục vụ happy path tests."""
    return {
        "project_name": project_name,
        "manifest_id": f"m_{project_name}",
        "created_at": "2026-10-28T00:00:00",
        "updated_at": "2026-10-28T00:00:00",
        "clips": [
            {
                "clip_id": c.get("clip_id", f"C{idx+1:02d}"),
                "file_name": os.path.basename(c["path"]),
                "current_path": c["path"],
                "duration_sec": c.get("duration_sec", 60.0),
                "content_hash": c.get("content_hash", "dummy_hash"),
            }
            for idx, c in enumerate(clips_info)
        ],
    }


def _make_valid_test_snapshot(
    tmp_path,
    project_name="ChunDVC_Project",
    manifest_id="m_test",
    prompt_text="Prompt text",
    items_count=2,
    distinct_clip_count=2,
    unaccounted_clips=(),
    unaccounted_clip_ids=(),
    distinct_planned_clips_count=None,
    planned_items_count=None,
    video_path=None,
):
    p = video_path or str(tmp_path / "dummy_base.mp4")
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"0" * 1024)
    st = os.stat(p)
    base_entry = BaselineSourceEntry(
        path=os.path.normpath(os.path.abspath(p)),
        st_size=st.st_size,
        st_mtime_ns=st.st_mtime_ns,
        st_ctime_ns=getattr(st, "st_ctime_ns", 0),
        st_ino=st.st_ino,
        st_dev=st.st_dev,
    )
    p_bytes = len(prompt_text.encode("utf-8"))
    sha = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    clip_ids = tuple(f"C{i+1:02d}" for i in range(distinct_clip_count))
    batch_item = PreparedBatchItem(
        batch_id="B01",
        batch_index=0,
        distinct_clip_count=distinct_clip_count,
        total_duration_sec=60.0,
        rendered_prompt=prompt_text,
        rendered_bytes=p_bytes,
        rendered_sha256=sha,
        content_signature="sig_b01",
        clip_ids=clip_ids,
        items_count=items_count,
        item_count=items_count,
        prompt_bytes=p_bytes,
    )
    baselines = (base_entry,)
    batches = (batch_item,)
    ev_sig = (os.path.normpath(os.path.abspath(p)),)
    snap_id = compute_snapshot_id(
        manifest_id=manifest_id,
        project_name=project_name,
        baseline_sources=baselines,
        batches=batches,
        evidence_context_signature=ev_sig,
        max_prompt_bytes=32768,
        formatter_policy="source_only_v1",
    )
    u_entries = tuple(
        UnaccountedClipEntry(clip_id=u["clip_id"], reason=u["reason"], message=u.get("message", ""))
        for u in unaccounted_clips
    )
    return PreparedBatchSnapshot(
        manifest_id=manifest_id,
        project_name=project_name,
        snapshot_id=snap_id,
        formatter_policy="source_only_v1",
        total_batches=1,
        planned_items_count=planned_items_count or items_count,
        distinct_planned_clips_count=distinct_planned_clips_count or distinct_clip_count,
        planned_clip_ids=clip_ids,
        planned_clip_ids_count=len(clip_ids),
        unaccounted_clip_ids=tuple(unaccounted_clip_ids),
        unaccounted_clips=u_entries,
        adapter_issues=(),
        max_prompt_bytes=32768,
        batches=batches,
        baseline_sources=baselines,
        evidence_context_signature=ev_sig,
        ui_context_signature=None,
    )


def _wait_for_review_page(page, timeout_sec: float = 12.0):
    """Wait helper sử dụng monotonic deadline + processEvents và assert physical registry all-idle."""
    deadline = time.monotonic() + timeout_sec
    app = QApplication.instance()
    while time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        has_prompt_workers = bool(getattr(page, "_prompt_workers_registry", None))
        has_kinetic_workers = bool(getattr(page, "_kinetic_workers_registry", None))
        is_loading = bool(page.is_analysis_loading())
        if not has_prompt_workers and not has_kinetic_workers and not is_loading:
            # Hoàn tất vật lý
            return
        time.sleep(0.01)

    diagnostics = (
        f"_prompt_workers_registry={list(getattr(page, '_prompt_workers_registry', {}).keys())}, "
        f"_kinetic_workers_registry={list(getattr(page, '_kinetic_workers_registry', {}).keys())}, "
        f"is_analysis_loading={page.is_analysis_loading()}"
    )
    if hasattr(page, "shutdown_analysis_workers"):
        page.shutdown_analysis_workers()
    pytest.fail(f"Timeout chờ ReviewPage all-idle sau {timeout_sec}s! Chi tiết: {diagnostics}")


# =============================================================================
# 1. TEST PREPARE BATCHES POPULATES UI & ZERO AUTO-CLIPBOARD
# =============================================================================

def test_batch_prepare_success_populates_ui_and_zero_clipboard(tmp_path, qapp):
    """Kiểm tra:
    - Chuẩn bị phân lô thành công nạp đầy đủ danh sách cbo_batch_selector, summary, preview.
    - Tuyệt đối 0 tự động ghi clipboard khi phân lô xong."""
    page = ReviewPage()
    QApplication.clipboard().setText("ORIGINAL_USER_CLIPBOARD_UNTOUCHED")

    v1 = str(tmp_path / "clip_batch_1.mp4")
    v2 = str(tmp_path / "clip_batch_2.mp4")
    d1, p1 = _create_dummy_video(v1, content=b"VIDEO_CLIP_1_DATA_FOR_BATCH")
    d2, p2 = _create_dummy_video(v2, content=b"VIDEO_CLIP_2_DATA_FOR_BATCH")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 30.0, "raw_subtitles": [{"start": 0, "end": 5, "text": "Đoạn mở đầu lô 1"}]},
        model_size="small",
        language="vi",
        verified_identity=(d1, p1),
    )
    cache_mgr.save_scan_result(
        file_path=v2,
        scan_data={"clip_dur": 45.0, "raw_subtitles": [{"start": 0, "end": 6, "text": "Đoạn tiếp theo lô 2"}]},
        model_size="small",
        language="vi",
        verified_identity=(d2, p2),
    )

    copied_signals = []
    page.prompt_copied.connect(copied_signals.append)

    meta = _make_dummy_manifest_metadata(
        "Test_Batch_Project",
        [
            {"clip_id": "C01", "path": v1, "duration_sec": 30.0, "content_hash": d1},
            {"clip_id": "C02", "path": v2, "duration_sec": 45.0, "content_hash": d2},
        ]
    )

    ctx = {
        "video_paths": [v1, v2],
        "whisper_model": "small",
        "whisper_language": "vi",
        "style_intent": "travel_vlog",
        "target_duration": "full",
        "hook_duration": "30_60s",
        "project_name": "Test_Batch_Project",
        "max_prompt_bytes": 32768,
        "manifest_metadata": meta,
    }

    req_id = page.request_prompt_preparation(
        intent="prepare_batches",
        context=ctx,
        cache_mgr=cache_mgr,
        manifest_cache_dir=str(tmp_path / "m"),
        manifest_metadata=meta,
    )
    assert req_id is not None

    _wait_for_review_page(page)

    # 1. UI populates
    assert page._prepared_batch_snapshot is not None
    batches = page._prepared_batch_snapshot.get("batches", [])
    assert len(batches) >= 1
    assert page.cbo_batch_selector.count() == len(batches)
    assert page.btn_copy_selected_batch.isEnabled() is True
    assert not page.txt_batch_preview.isHidden()
    assert "Test_Batch_Project" in page.txt_batch_preview.toPlainText() or "Lô 1" in page.txt_batch_preview.toPlainText()

    # 2. Getter hoạt động in-memory
    b_prompt = page.get_batch_prompt(0)
    assert len(b_prompt) > 0
    assert "json" in b_prompt

    # 3. Clipboard tuyệt đối không bị đè
    assert QApplication.clipboard().text() == "ORIGINAL_USER_CLIPBOARD_UNTOUCHED"
    assert len(copied_signals) == 0


# =============================================================================
# 2. TEST EXPLICIT COPY SELECTED BATCH RECHECKS SOURCE & WRITES CLIPBOARD
# =============================================================================

def test_batch_copy_selected_batch_rechecks_source_and_writes_clipboard(tmp_path, qapp):
    """Kiểm tra:
    - Khi bấm copy batch, worker recheck baseline stat các file nguồn.
    - Ghi đúng chuỗi rendered_prompt của batch vào clipboard.
    - Phát prompt_copied('batch')."""
    page = ReviewPage()
    QApplication.clipboard().setText("BEFORE_EXPLICIT_COPY")

    v1 = str(tmp_path / "clip_copy_batch.mp4")
    d1, p1 = _create_dummy_video(v1, content=b"VIDEO_CLIP_COPY_EXPLICIT")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 20.0, "raw_subtitles": [{"start": 0, "end": 4, "text": "Thoại kiểm tra copy"}]},
        model_size="small",
        language="vi",
        verified_identity=(d1, p1),
    )

    copied_signals = []
    page.prompt_copied.connect(copied_signals.append)

    meta = _make_dummy_manifest_metadata(
        "Test_Copy_Project",
        [{"clip_id": "C01", "path": v1, "duration_sec": 20.0, "content_hash": d1}]
    )

    ctx = {
        "video_paths": [v1],
        "whisper_model": "small",
        "whisper_language": "vi",
        "style_intent": "travel_vlog",
        "project_name": "Test_Copy_Project",
        "max_prompt_bytes": 32768,
        "manifest_metadata": meta,
    }

    # B1: Prepare batches
    page.request_prompt_preparation(
        intent="prepare_batches",
        context=ctx,
        cache_mgr=cache_mgr,
        manifest_cache_dir=str(tmp_path / "m"),
        manifest_metadata=meta,
    )
    _wait_for_review_page(page)

    expected_prompt = page.get_batch_prompt(0)
    assert len(expected_prompt) > 0
    assert QApplication.clipboard().text() == "BEFORE_EXPLICIT_COPY"

    # B2: Click copy selected batch
    page._on_copy_selected_batch_clicked()
    _wait_for_review_page(page)

    # Đã ghi vào clipboard và emit signal
    assert QApplication.clipboard().text() == expected_prompt
    assert copied_signals == ["batch"]


# =============================================================================
# 3. TEST COPY DENIED WHEN SOURCE MODIFIED OR MISSING
# =============================================================================

def test_batch_copy_denied_when_source_modified_or_missing(tmp_path, qapp):
    """Kiểm tra:
    - Sau khi phân lô, tệp nguồn bị chỉnh sửa (size/mtime) hoặc bị xóa.
    - Nhánh copy_batch trên worker phát hiện lệch stat, từ chối sao chép.
    - Clipboard không bị ghi, snapshot bị vô hiệu hóa."""
    page = ReviewPage()
    QApplication.clipboard().setText("ORIGINAL_CLIPBOARD_SAFE")

    v1 = str(tmp_path / "clip_source_tamper.mp4")
    d1, p1 = _create_dummy_video(v1, content=b"ORIGINAL_VIDEO_BYTES_CONTENT")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 25.0, "raw_subtitles": [{"start": 0, "end": 5, "text": "Thoại nguồn ban đầu"}]},
        model_size="small",
        language="vi",
        verified_identity=(d1, p1),
    )

    meta = _make_dummy_manifest_metadata(
        "Tamper_Test",
        [{"clip_id": "C01", "path": v1, "duration_sec": 25.0, "content_hash": d1}]
    )

    ctx = {
        "video_paths": [v1],
        "whisper_model": "small",
        "whisper_language": "vi",
        "project_name": "Tamper_Test",
        "max_prompt_bytes": 32768,
        "manifest_metadata": meta,
    }

    # Prepare batches
    page.request_prompt_preparation(
        intent="prepare_batches",
        context=ctx,
        cache_mgr=cache_mgr,
        manifest_cache_dir=str(tmp_path / "m"),
        manifest_metadata=meta,
    )
    _wait_for_review_page(page)
    assert page._prepared_batch_snapshot is not None

    # Tác động làm thay đổi file nguồn (thêm bytes làm thay đổi size & mtime)
    with open(v1, "ab") as f:
        f.write(b"TAMPERED_BYTES_AFTER_PREPARATION")
    time.sleep(0.01)

    copied_signals = []
    page.prompt_copied.connect(copied_signals.append)

    # Bấm copy batch
    page._on_copy_selected_batch_clicked()
    _wait_for_review_page(page)

    # 1. Clipboard không bị ghi
    assert QApplication.clipboard().text() == "ORIGINAL_CLIPBOARD_SAFE"
    assert len(copied_signals) == 0

    # 2. Snapshot bị vô hiệu hóa, nút copy bị disable
    assert page._prepared_batch_snapshot is None
    assert page.btn_copy_selected_batch.isEnabled() is False
    assert "Lỗi tệp nguồn" in page.lbl_json_hint.text() or "thay đổi" in page.lbl_json_hint.text()


# =============================================================================
# 4. TEST CONTEXT INVALIDATION
# =============================================================================

def test_batch_context_invalidation(tmp_path, qapp):
    """Kiểm tra:
    - Khi ngữ cảnh UI thay đổi (ví dụ set_story_metadata), snapshot batch cũ bị hủy.
    - get_batch_prompt() trả về '', copy_batch bị chặn fail-closed."""
    page = ReviewPage()

    v1 = str(tmp_path / "clip_inv.mp4")
    d1, p1 = _create_dummy_video(v1, content=b"VIDEO_CLIP_INVAL")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 20.0, "raw_subtitles": [{"start": 0, "end": 4, "text": "Thoại test invalidation"}]},
        model_size="small",
        language="vi",
        verified_identity=(d1, p1),
    )

    meta = _make_dummy_manifest_metadata(
        "Test_Inval_Project",
        [{"clip_id": "C01", "path": v1, "duration_sec": 20.0, "content_hash": d1}]
    )

    ctx = {
        "video_paths": [v1],
        "whisper_model": "small",
        "whisper_language": "vi",
        "style_intent": "travel_vlog",
        "project_name": "Test_Inval_Project",
        "max_prompt_bytes": 32768,
        "manifest_metadata": meta,
    }

    page.request_prompt_preparation(
        intent="prepare_batches",
        context=ctx,
        cache_mgr=cache_mgr,
        manifest_cache_dir=str(tmp_path / "m"),
        manifest_metadata=meta,
    )
    _wait_for_review_page(page)
    assert page.get_batch_prompt(0) != ""

    # Thay đổi metadata kịch bản
    page.set_story_metadata(style_intent="cinematic", hook_dur="15_30s")

    # Snapshot đã bị xóa
    assert page.get_batch_prompt(0) == ""
    assert page._prepared_batch_snapshot is None
    assert page.btn_copy_selected_batch.isEnabled() is False


# =============================================================================
# 5. TEST BUDGET ERROR NO CROP
# =============================================================================

def test_batch_budget_error_no_crop(tmp_path, qapp):
    """Kiểm tra:
    - Khi cấu hình max_prompt_bytes quá nhỏ không đủ cho 1 clip + wrapper:
    - Planner/Worker trả về budget_error, không tự ý crop xén văn bản.
    - Snapshot là None, 0 ghi clipboard."""
    page = ReviewPage()
    QApplication.clipboard().setText("CLIPBOARD_BEFORE_BUDGET_TEST")

    v1 = str(tmp_path / "clip_budget.mp4")
    d1, p1 = _create_dummy_video(v1, content=b"VIDEO_CLIP_BUDGET_OVERSIZED")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 100.0, "raw_subtitles": [{"start": 0, "end": 50, "text": "Câu thoại rất dài " * 50}]},
        model_size="small",
        language="vi",
        verified_identity=(d1, p1),
    )

    meta = _make_dummy_manifest_metadata(
        "Test_Budget_Project",
        [{"clip_id": "C01", "path": v1, "duration_sec": 100.0, "content_hash": d1}]
    )

    ctx = {
        "video_paths": [v1],
        "whisper_model": "small",
        "whisper_language": "vi",
        "project_name": "Test_Budget_Project",
        "max_prompt_bytes": 100,  # Cực kỳ nhỏ, chắc chắn không vừa wrapper
        "manifest_metadata": meta,
    }

    req_id = page.request_prompt_preparation(
        intent="prepare_batches",
        context=ctx,
        cache_mgr=cache_mgr,
        manifest_cache_dir=str(tmp_path / "m"),
        manifest_metadata=meta,
    )
    _wait_for_review_page(page)

    assert page._prepared_batch_snapshot is None
    assert page.btn_copy_selected_batch.isEnabled() is False
    assert "Giới hạn dung lượng" in page.lbl_json_hint.text()
    assert QApplication.clipboard().text() == "CLIPBOARD_BEFORE_BUDGET_TEST"


# =============================================================================
# 6. TEST COOPERATIVE CANCEL AND SHUTDOWN
# =============================================================================

def test_batch_cooperative_cancel_and_shutdown(tmp_path, qapp):
    """Kiểm tra:
    - Khi shutdown_analysis_workers() được gọi, worker batch đang chạy dừng cooperative.
    - Không crash, không rò rỉ thread."""
    page = ReviewPage()

    v1 = str(tmp_path / "clip_cancel.mp4")
    _create_dummy_video(v1, content=b"VIDEO_CLIP_CANCEL")

    ctx = {
        "video_paths": [v1],
        "whisper_model": "small",
        "whisper_language": "vi",
        "max_prompt_bytes": 32768,
    }

    # Gửi request và cancel ngay lập tức
    req_id = page.request_prompt_preparation(intent="prepare_batches", context=ctx)
    assert req_id is not None
    page.shutdown_analysis_workers()

    _wait_for_review_page(page)

    assert page._prepared_batch_snapshot is None
    assert len(page._prompt_workers_registry) == 0


# =============================================================================
# 7. TEST PRESERVES V1 SINGLE AND V2 KINETIC FLOW
# =============================================================================

def test_batch_preserves_v1_single_and_v2_kinetic_flow(tmp_path, qapp):
    """Kiểm tra:
    - copy_video vẫn chuẩn bị Prompt V1 đơn lẻ, phát prompt_copied('video').
    - copy_kinetic vẫn chuẩn bị Prompt V2 kinetic, phát prompt_copied('kinetic').
    - Các luồng không ảnh hưởng lẫn nhau."""
    page = ReviewPage()

    v1 = str(tmp_path / "clip_preservation.mp4")
    d1, p1 = _create_dummy_video(v1, content=b"VIDEO_CLIP_PRESERVE")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={
            "clip_dur": 15.0,
            "raw_subtitles": [
                {
                    "start": 0.0,
                    "end": 4.0,
                    "text": "Thoại bảo tồn v1 v2",
                    "words": [
                        {"word": "Thoại", "start": 0.0, "end": 1.0},
                        {"word": "bảo", "start": 1.0, "end": 2.0},
                        {"word": "tồn", "start": 2.0, "end": 3.0},
                        {"word": "v1", "start": 3.0, "end": 4.0},
                    ],
                }
            ],
        },
        model_size="small",
        language="vi",
        verified_identity=(d1, p1),
    )

    copied_signals = []
    page.prompt_copied.connect(copied_signals.append)

    ctx = {
        "video_paths": [v1],
        "whisper_model": "small",
        "whisper_language": "vi",
        "project_name": "Preserve_Project",
        "max_prompt_bytes": 32768,
    }

    # 1. Flow V1
    page.request_prompt_preparation(
        intent="copy_video",
        context=ctx,
        cache_mgr=cache_mgr,
        manifest_cache_dir=str(tmp_path / "m"),
    )
    _wait_for_review_page(page)

    assert "Thoại bảo tồn v1 v2" in QApplication.clipboard().text()
    assert copied_signals == ["video"]

    # 2. Flow V2
    page.request_kinetic_preparation(
        intent="copy_kinetic",
        context=ctx,
        cache_mgr=cache_mgr,
        manifest_cache_dir=str(tmp_path / "m"),
    )
    _wait_for_review_page(page)

    assert copied_signals == ["video", "kinetic"]


# =============================================================================
# 8. GROUP A PROBES — COPY AUTHORITY & OWNED SNAPSHOT IMMUTABILITY
# =============================================================================

def test_batch_copy_denied_when_baseline_missing_or_empty(tmp_path):
    """Probe A.1: baseline_sources missing hoặc rỗng -> copy bị từ chối (invalid_snapshot), không success."""
    v1 = str(tmp_path / "clip_a1.mp4")
    _create_dummy_video(v1)

    # Snapshot với baseline_sources rỗng
    raw_snap = {
        "manifest_id": "m01",
        "project_name": "ProjA1",
        "max_prompt_bytes": 32768,
        "batches": [
            {
                "batch_id": "B01",
                "batch_index": 0,
                "distinct_clip_count": 1,
                "total_duration_sec": 10.0,
                "rendered_prompt": "Valid rendered prompt text",
                "rendered_bytes": len("Valid rendered prompt text".encode("utf-8")),
                "content_signature": "sig01",
                "clip_ids": ("C01",),
                "items_count": 1,
            }
        ],
        "baseline_sources": (),  # Rỗng!
        "evidence_context_signature": "ev_sig",
    }

    req = PromptPreparationRequest(
        request_id="req_a1",
        generation=1,
        intent="copy_batch",
        video_paths=(v1,),
        prepared_batch_snapshot=raw_snap,
        selected_batch_index=0,
    )

    worker = PromptPreparationWorker(request=req)
    results = []
    worker.finished_signal.connect(lambda r_id, s, msg, payload: results.append((s, msg, payload)))
    worker.run()

    assert len(results) == 1
    success, msg, payload = results[0]
    assert success is False
    assert msg == "invalid_snapshot"


def test_batch_copy_denied_when_baseline_paths_mismatched(tmp_path):
    """Probe A.2: Snapshot baseline của file A, request paths chứa file B -> copy bị từ chối."""
    v_a = str(tmp_path / "clip_a.mp4")
    v_b = str(tmp_path / "clip_b.mp4")
    _create_dummy_video(v_a)
    _create_dummy_video(v_b)

    valid_snap = _make_valid_test_snapshot(
        tmp_path,
        project_name="ProjA2",
        manifest_id="m02",
        prompt_text="Valid rendered prompt text",
        items_count=1,
        distinct_clip_count=1,
        video_path=v_a,
    )

    # Request chứa v_b thay vì v_a
    req = PromptPreparationRequest(
        request_id="req_a2",
        generation=1,
        intent="copy_batch",
        project_name="ProjA2",
        video_paths=(v_b,),
        prepared_batch_snapshot=valid_snap,
        selected_batch_index=0,
        expected_snapshot_id=valid_snap.snapshot_id,
        expected_batch_id="B01",
        expected_rendered_sha256=valid_snap.batches[0].rendered_sha256,
        expected_formatter_policy="source_only_v1",
        expected_manifest_id=valid_snap.manifest_id,
        expected_project_name=valid_snap.project_name,
    )

    worker = PromptPreparationWorker(request=req)
    results = []
    worker.finished_signal.connect(lambda r_id, s, msg, payload: results.append((s, msg, payload)))
    worker.run()

    assert len(results) == 1
    success, msg, payload = results[0]
    assert success is False
    assert msg == "source_changed"


def test_batch_copy_denied_on_same_size_mtime_different_inode(tmp_path):
    """Probe A.3: File bị os.replace cùng kích thước và khôi phục mtime, nhưng inode đã đổi -> denied."""
    v1 = str(tmp_path / "clip_inode.mp4")
    _create_dummy_video(v1, size=1024, content=b"ORIGINAL_INODE_DATA")

    valid_snap = _make_valid_test_snapshot(
        tmp_path,
        project_name="ProjA3",
        manifest_id="m03",
        prompt_text="Prompt text for inode test",
        items_count=1,
        distinct_clip_count=1,
        video_path=v1,
    )
    st_orig = os.stat(v1)

    # Tạo tệp mới cùng kích thước tại đường dẫn tạm và dùng os.replace để tráo đổi
    v_new = str(tmp_path / "clip_temp.mp4")
    _create_dummy_video(v_new, size=1024, content=b"REPLACED_INODE_DATA")
    # Khôi phục đúng mtime cũ
    os.utime(v_new, ns=(st_orig.st_atime_ns, st_orig.st_mtime_ns))
    os.replace(v_new, v1)

    st_replaced = os.stat(v1)
    # Xác nhận: size và mtime_ns giống hệt, nhưng inode khác
    assert st_replaced.st_size == st_orig.st_size
    assert st_replaced.st_mtime_ns == st_orig.st_mtime_ns
    assert st_replaced.st_ino != st_orig.st_ino

    req = PromptPreparationRequest(
        request_id="req_a3",
        generation=1,
        intent="copy_batch",
        project_name="ProjA3",
        video_paths=(v1,),
        prepared_batch_snapshot=valid_snap,
        selected_batch_index=0,
        expected_snapshot_id=valid_snap.snapshot_id,
        expected_batch_id="B01",
        expected_rendered_sha256=valid_snap.batches[0].rendered_sha256,
        expected_formatter_policy="source_only_v1",
        expected_manifest_id=valid_snap.manifest_id,
        expected_project_name=valid_snap.project_name,
    )

    worker = PromptPreparationWorker(request=req)
    results = []
    worker.finished_signal.connect(lambda r_id, s, msg, payload: results.append((s, msg, payload)))
    worker.run()

    assert len(results) == 1
    success, msg, payload = results[0]
    assert success is False
    assert msg == "source_changed"


def test_batch_copy_denied_on_tampered_payload_or_oversized_cap(tmp_path):
    """Probe A.4: Snapshot bị sửa mutable thành rendered_prompt 40,000B hoặc rendered_bytes giả mạo -> denied."""
    v1 = str(tmp_path / "clip_cap.mp4")
    _create_dummy_video(v1)
    st1 = os.stat(v1)

    oversized_prompt = "X" * 40000
    raw_snap = {
        "manifest_id": "m04",
        "project_name": "ProjA4",
        "max_prompt_bytes": 32768,
        "batches": [
            {
                "batch_id": "B01",
                "batch_index": 0,
                "distinct_clip_count": 1,
                "total_duration_sec": 10.0,
                "rendered_prompt": oversized_prompt,
                "rendered_bytes": 40000,
                "content_signature": "sig04",
                "clip_ids": ("C01",),
                "items_count": 1,
            }
        ],
        "baseline_sources": [
            {
                "path": v1,
                "st_size": st1.st_size,
                "st_mtime_ns": st1.st_mtime_ns,
                "st_ctime_ns": getattr(st1, "st_ctime_ns", 0),
                "st_ino": st1.st_ino,
                "st_dev": st1.st_dev,
            }
        ],
        "evidence_context_signature": "ev_sig",
    }

    req = PromptPreparationRequest(
        request_id="req_a4",
        generation=1,
        intent="copy_batch",
        video_paths=(v1,),
        prepared_batch_snapshot=raw_snap,
        selected_batch_index=0,
        max_prompt_bytes=32768,
    )

    worker = PromptPreparationWorker(request=req)
    results = []
    worker.finished_signal.connect(lambda r_id, s, msg, payload: results.append((s, msg, payload)))
    worker.run()

    assert len(results) == 1
    success, msg, payload = results[0]
    assert success is False
    assert msg in ("invalid_snapshot", "budget_error")


def test_batch_copy_runs_zero_producer_or_planner_calls(tmp_path):
    """Probe A.5: Khi copy_batch, tuyệt đối không chạy build_clips_data, partition_manifest_into_batches hay adapt."""
    v1 = str(tmp_path / "clip_zero_calls.mp4")
    _create_dummy_video(v1)

    valid_snap = _make_valid_test_snapshot(
        tmp_path,
        project_name="ProjA5",
        manifest_id="m05",
        prompt_text="Prepared pure prompt",
        items_count=1,
        distinct_clip_count=1,
        video_path=v1,
    )

    req = PromptPreparationRequest(
        request_id="req_a5",
        generation=1,
        intent="copy_batch",
        project_name="ProjA5",
        video_paths=(v1,),
        prepared_batch_snapshot=valid_snap,
        selected_batch_index=0,
        expected_snapshot_id=valid_snap.snapshot_id,
        expected_batch_id="B01",
        expected_rendered_sha256=valid_snap.batches[0].rendered_sha256,
        expected_formatter_policy="source_only_v1",
        expected_manifest_id=valid_snap.manifest_id,
        expected_project_name=valid_snap.project_name,
    )

    with patch("src.core.clip_manifest.build_clips_data") as mock_build, \
         patch("src.core.batch_planner.partition_manifest_into_batches") as mock_plan, \
         patch("src.core.batch_evidence_adapter.adapt_clips_data_to_batch_analysis_cache") as mock_adapt:
        worker = PromptPreparationWorker(request=req)
        results = []
        worker.finished_signal.connect(lambda r_id, s, msg, payload: results.append((s, msg, payload)))
        worker.run()

        assert len(results) == 1
        assert results[0][0] is True
        assert results[0][2]["prompt_text"] == "Prepared pure prompt"
        assert mock_build.call_count == 0
        assert mock_plan.call_count == 0
        assert mock_adapt.call_count == 0


# =============================================================================
# 9. GROUP B PROBES — GUI VALIDATION & LIFECYCLE
# =============================================================================

def test_ui_refuses_invalid_selected_batch_index_before_mutation(tmp_path, qapp):
    """Probe B.1: selected_batch_index là True, '0', None, -1, 99 bị từ chối TRƯỚC generation/cancel/dispatch."""
    page = ReviewPage()
    v1 = str(tmp_path / "clip_b1.mp4")
    _create_dummy_video(v1)

    initial_gen = page._prompt_generation
    ctx = {"video_paths": [v1]}

    # Mock snapshot có 1 batch
    page._prepared_batch_snapshot = {
        "manifest_id": "m_b1",
        "project_name": "ProjB1",
        "max_prompt_bytes": 32768,
        "batches": [
            {
                "batch_id": "B01",
                "batch_index": 0,
                "distinct_clip_count": 1,
                "total_duration_sec": 10.0,
                "rendered_prompt": "Valid text",
                "rendered_bytes": len("Valid text".encode("utf-8")),
                "content_signature": "sig",
                "clip_ids": ("C01",),
                "items_count": 1,
            }
        ],
        "baseline_sources": [
            {
                "path": v1,
                "st_size": 2048,
                "st_mtime_ns": 100,
                "st_ctime_ns": 100,
                "st_ino": 1,
                "st_dev": 1,
            }
        ],
        "ui_context_signature": page._extract_context_signature(ctx),
    }

    # Các giá trị không hợp lệ
    invalid_indices = [True, False, "0", "1", None, -1, 99, 3.14]

    for inv in invalid_indices:
        res = page.request_prompt_preparation(
            intent="copy_batch",
            context=ctx,
            selected_batch_index=inv,
        )
        assert res is None
        # Không tăng generation, không dispatch worker
        assert page._prompt_generation == initial_gen
        assert len(page._prompt_workers_registry) == 0


def test_copy_batch_discards_stale_result_when_ui_selection_changes(tmp_path, qapp):
    """Probe B.2: Đang copy Lô 0 nhưng UI chuyển sang Lô 1 -> hủy kết quả cũ, 0 ghi clipboard."""
    page = ReviewPage()
    QApplication.clipboard().setText("STALE_CLIPBOARD_UNTOUCHED")

    # Giả lập cbo_batch_selector đang chọn index 1
    page.cbo_batch_selector.clear()
    page.cbo_batch_selector.addItem("Lô 1", userData=0)
    page.cbo_batch_selector.addItem("Lô 2", userData=1)
    page.cbo_batch_selector.setCurrentIndex(1)

    copied_signals = []
    page.prompt_copied.connect(copied_signals.append)

    # Worker trả về kết quả cho Lô 0 (do request xuất phát trước đó)
    fake_result = {
        "intent": "copy_batch",
        "selected_batch_index": 0,
        "prompt_text": "PROMPT_FOR_BATCH_0",
        "prompt_bytes": len("PROMPT_FOR_BATCH_0".encode("utf-8")),
    }

    page._active_prompt_request_id = "req_stale"
    page._on_prompt_preparation_finished("req_stale", True, "success", fake_result)

    # Do current combobox index là 1 != 0, kết quả bị hủy và 0 ghi clipboard!
    assert QApplication.clipboard().text() == "STALE_CLIPBOARD_UNTOUCHED"
    assert len(copied_signals) == 0


def test_prepare_finish_does_not_enable_copy_if_physical_worker_busy(tmp_path, qapp):
    """Probe B.3: Prepare logical finished nhưng registry còn physical worker -> nút Copy không được bật."""
    page = ReviewPage()
    v1 = str(tmp_path / "busy_clip.mp4")
    valid_snap = _make_valid_test_snapshot(tmp_path, video_path=v1)

    # Thêm một mock worker vào registry
    mock_worker = MagicMock()
    page._prompt_workers_registry["physical_busy_worker"] = mock_worker
    page._active_prompt_request_id = "req_prep"
    page._active_prompt_intent = "prepare_batches"
    page._pending_prompt_expected = {
        "request_id": "req_prep",
        "intent": "prepare_batches",
        "generation": page._prompt_generation,
        "context_signature": page._extract_context_signature(page._get_current_context()),
        "expected_creative_context_signature": None,
    }

    result_payload = {
        "request_id": "req_prep",
        "generation": page._prompt_generation,
        "intent": "prepare_batches",
        "batch_snapshot": valid_snap,
    }

    page._on_prompt_preparation_finished("req_prep", True, "success", result_payload)

    # Nút Copy BẮT BUỘC giữ khóa vì registry còn physical worker
    assert page.btn_copy_selected_batch.isEnabled() is False

    # Khi physical worker kết thúc và được dọn dẹp khỏi registry
    page._on_prompt_worker_thread_finished("physical_busy_worker", mock_worker)
    # Bây giờ registry đã rỗng, nút Copy mới được mở khóa!
    assert page.btn_copy_selected_batch.isEnabled() is True


def test_batch_ui_displays_truthful_fields_and_full_preview(tmp_path, qapp):
    """Probe B.4: UI thể hiện distinct clips khác items/partitions, đọc đúng rendered_bytes, preview full text."""
    page = ReviewPage()

    long_prompt = "Header\n" + ("Nội dung kịch bản dòng %d\n" % 1) * 35 + "Footer"
    p_bytes = len(long_prompt.encode("utf-8"))

    v1 = str(tmp_path / "truth_clip.mp4")
    valid_snap = _make_valid_test_snapshot(
        tmp_path,
        project_name="ChunDVC_Project",
        manifest_id="m_truth",
        prompt_text=long_prompt,
        items_count=5,
        distinct_clip_count=3,
        unaccounted_clips=[{"clip_id": "C99", "reason": "ERR_UNKNOWN_DURATION", "message": "Unknown duration"}],
        unaccounted_clip_ids=["C99"],
        planned_items_count=5,
        distinct_planned_clips_count=3,
        video_path=v1,
    )

    page._active_prompt_request_id = "req_truth"
    page._active_prompt_intent = "prepare_batches"
    page._pending_prompt_expected = {
        "request_id": "req_truth",
        "intent": "prepare_batches",
        "generation": page._prompt_generation,
        "context_signature": page._extract_context_signature(page._get_current_context()),
        "expected_creative_context_signature": None,
    }

    result_payload = {
        "request_id": "req_truth",
        "generation": page._prompt_generation,
        "intent": "prepare_batches",
        "batch_snapshot": valid_snap,
    }

    page._on_prompt_preparation_finished("req_truth", True, "success", result_payload)

    # 1. Combobox đọc đúng items và bytes
    assert page.cbo_batch_selector.count() == 1
    cbo_text = page.cbo_batch_selector.itemText(0)
    assert "5 clips" in cbo_text
    assert f"{p_bytes:,} B" in cbo_text

    # 2. Summary text phân biệt distinct clips vs items và lý do chưa rõ thời lượng
    summary_text = page.lbl_batch_summary.text()
    assert "3 clips" in summary_text
    assert "5 đoạn" in summary_text
    assert "1 clips chưa phân lô (chưa rõ thời lượng)" in summary_text

    # 3. Preview hiển thị toàn văn, không crop 20 dòng
    preview_text = page.txt_batch_preview.toPlainText()
    assert "dòng 1" in preview_text
    assert "Footer" in preview_text
    assert "dòng ẩn" not in preview_text


def test_empty_source_inventory_refuses_copy_fail_closed(qapp):
    """Probe B.5: Bỏ fallback dùng baseline paths khi current source inventory rỗng để hợp thức hóa copy."""
    page = ReviewPage()

    page._prepared_batch_snapshot = {
        "manifest_id": "m_test",
        "project_name": "ChunDVC_Project",
        "batches": [{"rendered_prompt": "text", "rendered_bytes": 4, "batch_id": "B01"}],
        "baseline_sources": [{"path": "/some/path.mp4"}],
        "ui_context_signature": ((), "small", "vi"),
    }

    ctx = {"video_paths": []}  # Rỗng!
    res = page.request_prompt_preparation(intent="copy_batch", context=ctx, selected_batch_index=0)
    # Fail-closed: từ chối, không lấy baseline paths để bù vào
    assert res is None
    assert "Không có tệp video nào" in page.lbl_json_hint.text()


# =============================================================================
# 10. GROUP C PROBES — SSOT TRUNG THỰC & SOURCE-ONLY DRAFT
# =============================================================================

def test_manifest_zero_duration_stays_unaccounted_zero_fake_planned(tmp_path):
    """Probe C.1: Manifest duration 0 và producer row duration 60 -> stays unaccounted, 0 fake planned."""
    v1 = str(tmp_path / "clip_c1.mp4")
    d1, p1 = _create_dummy_video(v1)

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 60.0, "raw_subtitles": [{"start": 0, "end": 10, "text": "Thoại test SSOT"}]},
        model_size="small",
        language="vi",
        verified_identity=(d1, p1),
    )

    # Manifest duration CỐ TÌNH là 0.0
    meta = {
        "project_name": "ProjC1",
        "manifest_id": "m_c1",
        "created_at": "2026-10-28T00:00:00",
        "updated_at": "2026-10-28T00:00:00",
        "clips": [
            {
                "clip_id": "C01",
                "file_name": "clip_c1.mp4",
                "current_path": v1,
                "duration_sec": 0.0,  # Unknown!
                "content_hash": d1,
            }
        ],
    }

    req = PromptPreparationRequest(
        request_id="req_c1",
        generation=1,
        intent="prepare_batches",
        video_paths=(v1,),
        project_name="ProjC1",
        manifest_metadata=meta,
    )

    worker = PromptPreparationWorker(request=req, cache_mgr=cache_mgr)
    results = []
    worker.finished_signal.connect(lambda r_id, s, msg, payload: results.append((s, msg, payload)))
    worker.run()

    assert len(results) == 1
    success, msg, payload = results[0]
    assert success is True
    snap = payload["batch_snapshot"]
    # Bắt buộc: 0 batches được tạo, C01 nằm trong unaccounted_clip_ids với lý do DURATION_UNKNOWN!
    assert len(snap["batches"]) == 0
    assert "C01" in snap["unaccounted_clip_ids"]
    assert any(iss["reason"] in ("DURATION_UNKNOWN", "ERR_UNKNOWN_DURATION") for iss in snap["unaccounted_clips"])
    assert any(iss["issue_type"] == "ERR_UNKNOWN_DURATION" for iss in snap["adapter_issues"])


def test_story_copilot_batch_wrapper_no_fake_ids_no_auto_resolve_promise():
    """Probe C.2: StoryCopilot wrapper không chứa fake source clip C01, fake 10/25.5s, không auto Resolve promise."""
    from src.core.story_copilot import StoryCopilot
    prefix, suffix = StoryCopilot.build_batch_prompt_wrapper(
        project_name="CleanProject",
        story_intent="travel_vlog",
    )
    full_text = prefix + "\n" + suffix

    # Không hứa tự động sáp nhập lên DaVinci Resolve
    assert "sáp nhập và đồng bộ tự động lên DaVinci Resolve" not in full_text
    assert "Local Batch Draft" in full_text

    # Không hardcode C01 làm dữ liệu mẫu
    assert '"clip_id": "C01"' not in full_text
    assert '"start_sec": 10.0' not in full_text
    assert '"end_sec": 25.5' not in full_text


# =============================================================================
# 11. G06-06A3-R2 TDD PROBES — RESIDUAL 1, 2, 3 VERIFICATION
# =============================================================================

def test_validator_rejects_typed_dto_with_empty_baseline_or_bool_cap(tmp_path):
    """Residual 1: Typed DTO với baseline_sources rỗng hoặc max_prompt_bytes=True bị validator từ chối."""
    valid_snap = _make_valid_test_snapshot(tmp_path)

    # 1. Baseline sources rỗng -> từ chối
    empty_base_snap = dataclasses.replace(valid_snap, baseline_sources=())
    assert validate_and_normalize_snapshot(empty_base_snap) is None

    # 2. max_prompt_bytes là bool (True) -> từ chối
    bool_cap_snap = dataclasses.replace(valid_snap, max_prompt_bytes=True)
    assert validate_and_normalize_snapshot(bool_cap_snap) is None

    # 3. batches rỗng -> từ chối
    empty_batches_snap = dataclasses.replace(valid_snap, batches=())
    assert validate_and_normalize_snapshot(empty_batches_snap) is None


def test_validator_symmetry_between_typed_dto_and_dict(tmp_path):
    """Residual 1: Tính đối xứng giữa DTO và dict: dữ liệu hợp lệ trả về DTO tương đương; dữ liệu sai bị từ chối như nhau."""
    valid_dto = _make_valid_test_snapshot(tmp_path)
    dto_dict = valid_dto.to_dict()

    # Cả 2 đều validate thành công và cho ra snapshot_id giống hệt nhau
    norm_from_dto = validate_and_normalize_snapshot(valid_dto)
    norm_from_dict = validate_and_normalize_snapshot(dto_dict)
    assert norm_from_dto is not None
    assert norm_from_dict is not None
    assert norm_from_dto.snapshot_id == norm_from_dict.snapshot_id
    assert norm_from_dto.snapshot_id == valid_dto.snapshot_id

    # Dữ liệu lỗi: clip_id là chuỗi rỗng
    invalid_dict = dict(dto_dict)
    invalid_dict["batches"] = [
        dict(dto_dict["batches"][0], clip_ids=("",))
    ]
    assert validate_and_normalize_snapshot(invalid_dict) is None

    invalid_dto = dataclasses.replace(
        valid_dto,
        batches=(dataclasses.replace(valid_dto.batches[0], clip_ids=("",)),)
    )
    assert validate_and_normalize_snapshot(invalid_dto) is None


def test_caller_mutating_input_dict_or_list_does_not_alias_snapshot(tmp_path):
    """Residual 1: Caller mutate dict/list sau khi validate không làm alias hoặc thay đổi snapshot đã validate."""
    valid_dto = _make_valid_test_snapshot(tmp_path)
    mutable_dict = valid_dto.to_dict()
    # Chuyển các tuple lồng nhau thành mutable list
    mutable_dict["batches"] = [dict(mutable_dict["batches"][0])]
    mutable_dict["baseline_sources"] = [dict(mutable_dict["baseline_sources"][0])]

    norm = validate_and_normalize_snapshot(mutable_dict)
    assert norm is not None
    orig_prompt = norm.batches[0].rendered_prompt

    # Caller mutate input dict
    mutable_dict["batches"][0]["rendered_prompt"] = "MUTATED_BY_CALLER"
    mutable_dict["baseline_sources"].clear()

    # Frozen snapshot không hề bị ảnh hưởng
    assert norm.batches[0].rendered_prompt == orig_prompt
    assert len(norm.baseline_sources) == 1


def test_request_captures_owned_immutable_snapshot_before_worker_start(tmp_path, qapp):
    """Residual 1: ReviewPage.request_prompt_preparation chụp snapshot DTO bất biến, không bị ảnh hưởng nếu caller sửa context."""
    page = ReviewPage()
    v1 = str(tmp_path / "immutable_test.mp4")
    _create_dummy_video(v1)
    valid_snap = _make_valid_test_snapshot(tmp_path, video_path=v1)

    page._prepared_batch_snapshot = valid_snap
    ctx = {
        "video_paths": [v1],
        "project_name": valid_snap.project_name,
        "max_prompt_bytes": 32768,
    }
    sig = page._extract_context_signature(ctx)
    page._prepared_batch_snapshot = dataclasses.replace(valid_snap, ui_context_signature=sig)

    req_id = page.request_prompt_preparation(
        intent="copy_batch",
        context=ctx,
        selected_batch_index=0,
    )
    assert req_id is not None
    captured_worker = page._prompt_workers_registry.get(req_id)
    assert captured_worker is not None
    # Worker request lưu frozen PreparedBatchSnapshot, không phải mutable dict
    req_obj = getattr(captured_worker, "request", getattr(captured_worker, "_request", None))
    assert req_obj is not None
    assert isinstance(req_obj.prepared_batch_snapshot, PreparedBatchSnapshot)
    assert req_obj.expected_snapshot_id == valid_snap.snapshot_id
    assert req_obj.expected_rendered_sha256 == valid_snap.batches[0].rendered_sha256


def test_same_byte_prompt_tamper_rejected(tmp_path):
    """Residual 2: Tamper nội dung prompt với cùng số bytes (8B vs 8B) bị từ chối bằng rendered_sha256."""
    v1 = str(tmp_path / "tamper_clip.mp4")
    _create_dummy_video(v1)

    # Prompt gốc dài đúng 8 bytes
    orig_prompt = "ORIGINAL"
    tampered_prompt = "TAMPERED"
    assert len(orig_prompt.encode("utf-8")) == len(tampered_prompt.encode("utf-8")) == 8

    valid_snap = _make_valid_test_snapshot(tmp_path, prompt_text=orig_prompt, video_path=v1)
    orig_sha = valid_snap.batches[0].rendered_sha256

    # Thay prompt trong batch bằng tampered_prompt mà giữ nguyên rendered_bytes = 8
    tampered_item = dataclasses.replace(
        valid_snap.batches[0],
        rendered_prompt=tampered_prompt,
        rendered_bytes=8,
        rendered_sha256=orig_sha,  # Giả mạo sha của prompt gốc
    )
    tampered_snap = dataclasses.replace(
        valid_snap,
        batches=(tampered_item,),
    )

    req = PromptPreparationRequest(
        request_id="req_tamper",
        generation=1,
        intent="copy_batch",
        project_name=valid_snap.project_name,
        video_paths=(v1,),
        prepared_batch_snapshot=tampered_snap,
        selected_batch_index=0,
        expected_snapshot_id=valid_snap.snapshot_id,
        expected_batch_id="B01",
        expected_rendered_sha256=orig_sha,
        expected_formatter_policy="source_only_v1",
    )

    worker = PromptPreparationWorker(request=req)
    results = []
    worker.finished_signal.connect(lambda r_id, s, msg, payload: results.append((s, msg, payload)))
    worker.run()

    assert len(results) == 1
    success, msg, payload = results[0]
    # Bị từ chối vì hashlib.sha256("TAMPERED") != orig_sha!
    assert success is False
    assert msg == "invalid_snapshot"


def test_copy_batch_with_mismatched_expected_rendered_sha256_denied(tmp_path):
    """Residual 2: copy_batch khi expected_rendered_sha256 không khớp bị từ chối trước source IO."""
    v1 = str(tmp_path / "clip_sha_mismatch.mp4")
    _create_dummy_video(v1)
    valid_snap = _make_valid_test_snapshot(tmp_path, video_path=v1)

    req = PromptPreparationRequest(
        request_id="req_sha_mis",
        generation=1,
        intent="copy_batch",
        project_name=valid_snap.project_name,
        video_paths=(v1,),
        prepared_batch_snapshot=valid_snap,
        selected_batch_index=0,
        expected_snapshot_id=valid_snap.snapshot_id,
        expected_batch_id="B01",
        expected_rendered_sha256="fake_sha256_deadbeef" * 2,
        expected_formatter_policy="source_only_v1",
    )

    worker = PromptPreparationWorker(request=req)
    results = []
    worker.finished_signal.connect(lambda r_id, s, msg, payload: results.append((s, msg, payload)))
    worker.run()

    assert len(results) == 1
    assert results[0][0] is False
    assert results[0][1] == "invalid_snapshot"


def test_copy_batch_with_mismatched_project_or_manifest_denied_before_source_io(tmp_path):
    """Residual 2: copy_batch có project_name hoặc manifest_id không khớp bị từ chối trước khi kiểm tra source."""
    v1 = str(tmp_path / "clip_proj_mis.mp4")
    _create_dummy_video(v1)
    valid_snap = _make_valid_test_snapshot(tmp_path, project_name="ProjectA", manifest_id="m_A", video_path=v1)

    req = PromptPreparationRequest(
        request_id="req_proj_mis",
        generation=1,
        intent="copy_batch",
        project_name="DifferentProjectB",
        video_paths=(v1,),
        prepared_batch_snapshot=valid_snap,
        selected_batch_index=0,
        expected_snapshot_id=valid_snap.snapshot_id,
        expected_batch_id="B01",
        expected_rendered_sha256=valid_snap.batches[0].rendered_sha256,
        expected_formatter_policy="source_only_v1",
    )

    with patch("os.stat") as mock_stat:
        worker = PromptPreparationWorker(request=req)
        results = []
        worker.finished_signal.connect(lambda r_id, s, msg, payload: results.append((s, msg, payload)))
        worker.run()

        assert len(results) == 1
        assert results[0][0] is False
        assert results[0][1] == "invalid_snapshot"
        # Tuyệt đối không gọi os.stat vì đã bị từ chối trước source IO!
        assert mock_stat.call_count == 0


def test_missing_proof_fields_denied(tmp_path):
    """Residual 2: Snapshot thiếu snapshot_id hoặc batch thiếu rendered_sha256 bị từ chối."""
    v1 = str(tmp_path / "clip_proof.mp4")
    _create_dummy_video(v1)
    valid_snap = _make_valid_test_snapshot(tmp_path, video_path=v1)
    d = valid_snap.to_dict()

    # Xóa snapshot_id
    del d["snapshot_id"]
    assert validate_and_normalize_snapshot(d) is None

    # Xóa rendered_sha256 trong batch
    d2 = valid_snap.to_dict()
    del d2["batches"][0]["rendered_sha256"]
    assert validate_and_normalize_snapshot(d2) is None


def test_active_prepare_batches_discards_forged_copy_intent_zero_writes(qapp):
    """Residual 3: Đang chờ prepare_batches nhưng kết quả bị giả mạo intent='copy_batch' -> Discard hoàn toàn, 0 clipboard write."""
    page = ReviewPage()
    QApplication.clipboard().setText("ORIGINAL_CLIPBOARD_UNTOUCHED")

    page._active_prompt_request_id = "req_prep_1"
    page._active_prompt_intent = "prepare_batches"
    page._pending_prompt_expected = {
        "request_id": "req_prep_1",
        "intent": "prepare_batches",
        "context_signature": page._extract_context_signature(page._get_current_context()),
        "expected_creative_context_signature": None,
    }

    # Kết quả trả về tự khai là copy_batch và mang prompt_text
    forged_result = {
        "intent": "copy_batch",
        "selected_batch_index": 0,
        "prompt_text": "FORGED_PROMPT_PAYLOAD",
        "prompt_bytes": len("FORGED_PROMPT_PAYLOAD".encode("utf-8")),
    }

    page._on_prompt_preparation_finished("req_prep_1", True, "success", forged_result)

    # Tuyệt đối không ghi clipboard!
    assert QApplication.clipboard().text() == "ORIGINAL_CLIPBOARD_UNTOUCHED"
    assert page._prepared_batch_snapshot is None


def test_valid_copy_result_with_wrong_snapshot_id_discards_zero_writes(tmp_path, qapp):
    """Residual 3: Kết quả copy_batch trả về từ snapshot cũ khi snapshot đã bị thay đổi -> Discard, 0 clipboard write."""
    page = ReviewPage()
    QApplication.clipboard().setText("STALE_SNAP_CLIPBOARD_UNTOUCHED")

    v1 = str(tmp_path / "clip_s1.mp4")
    v2 = str(tmp_path / "clip_s2.mp4")
    snap1 = _make_valid_test_snapshot(tmp_path, manifest_id="m_snap1", video_path=v1)
    snap2 = _make_valid_test_snapshot(tmp_path, manifest_id="m_snap2", video_path=v2)

    # Đang lưu expected cho snap1
    page._active_prompt_request_id = "req_copy_1"
    page._active_prompt_intent = "copy_batch"
    page._pending_prompt_expected = {
        "request_id": "req_copy_1",
        "intent": "copy_batch",
        "generation": page._prompt_generation,
        "snapshot_id": snap1.snapshot_id,
        "batch_id": "B01",
        "batch_index": 0,
        "rendered_sha256": snap1.batches[0].rendered_sha256,
        "rendered_bytes": snap1.batches[0].rendered_bytes,
        "expected_creative_context_signature": None,
    }
    # Nhưng trên ReviewPage, snapshot hiện tại đã bị thay bằng snap2!
    page._prepared_batch_snapshot = snap2
    page.cbo_batch_selector.addItem("Lô 1", userData=0)
    page.cbo_batch_selector.setCurrentIndex(0)

    copy_result = {
        "request_id": "req_copy_1",
        "generation": page._prompt_generation,
        "intent": "copy_batch",
        "selected_batch_index": 0,
        "batch_id": "B01",
        "rendered_sha256": snap1.batches[0].rendered_sha256,
        "snapshot_id": snap1.snapshot_id,
        "manifest_id": snap1.manifest_id,
        "project_name": snap1.project_name,
        "prompt_text": snap1.batches[0].rendered_prompt,
        "prompt_bytes": snap1.batches[0].rendered_bytes,
    }

    page._on_prompt_preparation_finished("req_copy_1", True, "success", copy_result)

    # Discard: 0 clipboard write
    assert QApplication.clipboard().text() == "STALE_SNAP_CLIPBOARD_UNTOUCHED"


def test_copy_result_with_wrong_index_type_bool_discarded(qapp):
    """Residual 3: selected_batch_index có kiểu bool (True) bị từ chối ngay lập tức, 0 ghi clipboard."""
    page = ReviewPage()
    QApplication.clipboard().setText("BOOL_INDEX_CLIPBOARD_UNTOUCHED")

    page._active_prompt_request_id = "req_bool"
    page._active_prompt_intent = "copy_batch"
    page._pending_prompt_expected = {
        "request_id": "req_bool",
        "intent": "copy_batch",
        "generation": page._prompt_generation,
        "batch_index": 1,
        "expected_creative_context_signature": None,
    }

    # selected_batch_index = True (trong Python, isinstance(True, int) là True nhưng type(True) is bool)
    result_with_bool = {
        "request_id": "req_bool",
        "generation": page._prompt_generation,
        "intent": "copy_batch",
        "selected_batch_index": True,
        "prompt_text": "BOOL_PAYLOAD",
    }

    page._on_prompt_preparation_finished("req_bool", True, "success", result_with_bool)

    assert QApplication.clipboard().text() == "BOOL_INDEX_CLIPBOARD_UNTOUCHED"


def test_prepare_batches_validates_snapshot_before_publishing(qapp):
    """Residual 3: prepare_batches nhận snapshot không hợp lệ bị pure validator từ chối, không nạp UI hay _manifest."""
    page = ReviewPage()
    page._active_prompt_request_id = "req_invalid_snap"
    page._active_prompt_intent = "prepare_batches"
    page._pending_prompt_expected = {
        "request_id": "req_invalid_snap",
        "intent": "prepare_batches",
        "generation": page._prompt_generation,
        "context_signature": page._extract_context_signature(page._get_current_context()),
        "expected_creative_context_signature": None,
    }

    invalid_result = {
        "request_id": "req_invalid_snap",
        "generation": page._prompt_generation,
        "intent": "prepare_batches",
        "batch_snapshot": {
            "manifest_id": "m_corrupt",
            "project_name": "ProjCorrupt",
            "batches": [],  # Rỗng -> không hợp lệ
            "baseline_sources": [],
        },
        "manifest_metadata": {
            "project_name": "ProjCorrupt",
            "manifest_id": "m_corrupt",
            "clips": [],
        }
    }

    page._on_prompt_preparation_finished("req_invalid_snap", True, "success", invalid_result)

    assert page._prepared_batch_snapshot is None
    assert getattr(page, "_manifest", None) is None
    assert page.cbo_batch_selector.count() == 0


def test_correct_copy_batch_writes_clipboard_exactly_once(tmp_path, qapp):
    """Residual 3: Happy path copy_batch ghi clipboard đúng 1 lần và phát tín hiệu prompt_copied."""
    page = ReviewPage()
    QApplication.clipboard().setText("INITIAL_STATE")

    v1 = str(tmp_path / "happy_clip.mp4")
    _create_dummy_video(v1)
    prompt_content = "PERFECT_BATCH_PROMPT_CONTENT"
    valid_snap = _make_valid_test_snapshot(tmp_path, prompt_text=prompt_content, video_path=v1)

    sig = page._extract_context_signature(page._get_current_context())
    owned_snap = dataclasses.replace(valid_snap, ui_context_signature=sig)
    page._prepared_batch_snapshot = owned_snap

    page.cbo_batch_selector.addItem("Lô 1", userData=0)
    page.cbo_batch_selector.setCurrentIndex(0)

    page._active_prompt_request_id = "req_happy"
    page._active_prompt_intent = "copy_batch"
    selected_b = owned_snap.batches[0]
    page._pending_prompt_expected = {
        "request_id": "req_happy",
        "intent": "copy_batch",
        "generation": page._prompt_generation,
        "snapshot_id": owned_snap.snapshot_id,
        "manifest_id": owned_snap.manifest_id,
        "project_name": owned_snap.project_name,
        "batch_id": selected_b.batch_id,
        "batch_index": 0,
        "rendered_sha256": selected_b.rendered_sha256,
        "rendered_bytes": selected_b.rendered_bytes,
        "expected_creative_context_signature": None,
    }

    copied_signals = []
    page.prompt_copied.connect(copied_signals.append)

    happy_result = {
        "request_id": "req_happy",
        "generation": page._prompt_generation,
        "intent": "copy_batch",
        "selected_batch_index": 0,
        "batch_id": selected_b.batch_id,
        "snapshot_id": owned_snap.snapshot_id,
        "manifest_id": owned_snap.manifest_id,
        "project_name": owned_snap.project_name,
        "rendered_sha256": selected_b.rendered_sha256,
        "prompt_text": prompt_content,
        "prompt_bytes": len(prompt_content.encode("utf-8")),
    }

    page._on_prompt_preparation_finished("req_happy", True, "success", happy_result)

    assert QApplication.clipboard().text() == prompt_content
    assert len(copied_signals) == 1
    assert copied_signals[0] == "batch"


# =============================================================================
# 9. GROUP R3 PROBES — REQUIRED BATCH BINDING GATE & TERMINAL OWNERSHIP
# =============================================================================

@pytest.mark.parametrize("field_name", [
    "expected_snapshot_id",
    "expected_batch_id",
    "expected_rendered_sha256",
    "expected_formatter_policy",
    "expected_manifest_id",
    "expected_project_name",
])
@pytest.mark.parametrize("invalid_value", [
    None,
    "",
    True,
    False,
    12345,
])
def test_worker_copy_batch_missing_or_invalid_expected_field_denied_zero_io(tmp_path, field_name, invalid_value):
    """R3 Gate 1: Worker copy_batch bắt buộc có 6 expected bindings, đúng strict type str không rỗng/bool.
    Thiếu/sai kiểu -> controlled denied 'invalid_snapshot' ngay lập tức với 0 source IO."""
    v1 = str(tmp_path / "clip_gate.mp4")
    _create_dummy_video(v1)
    valid_snap = _make_valid_test_snapshot(tmp_path, video_path=v1)

    req_kwargs = {
        "request_id": "req_gate_test",
        "generation": 1,
        "intent": "copy_batch",
        "project_name": valid_snap.project_name,
        "video_paths": (v1,),
        "prepared_batch_snapshot": valid_snap,
        "selected_batch_index": 0,
        "expected_snapshot_id": valid_snap.snapshot_id,
        "expected_batch_id": "B01",
        "expected_rendered_sha256": valid_snap.batches[0].rendered_sha256,
        "expected_formatter_policy": "source_only_v1",
        "expected_manifest_id": valid_snap.manifest_id,
        "expected_project_name": valid_snap.project_name,
    }
    req_kwargs[field_name] = invalid_value

    req = PromptPreparationRequest(**req_kwargs)

    with patch("os.stat") as mock_stat:
        worker = PromptPreparationWorker(request=req)
        results = []
        worker.finished_signal.connect(lambda r_id, s, msg, payload: results.append((s, msg, payload)))
        worker.run()

        assert len(results) == 1
        success, msg, payload = results[0]
        assert success is False
        assert msg == "invalid_snapshot"
        assert mock_stat.call_count == 0


@pytest.mark.parametrize("mismatched_field,mismatched_value", [
    ("expected_snapshot_id", "mismatched_snap_id_999"),
    ("expected_batch_id", "B99_NONEXISTENT"),
    ("expected_rendered_sha256", "f" * 64),
    ("expected_manifest_id", "m_mismatched_other"),
    ("expected_project_name", "Different_Project_Name"),
    ("expected_formatter_policy", "unsupported_policy_v9"),
])
def test_worker_copy_batch_expected_binding_mismatches_denied_zero_io(tmp_path, mismatched_field, mismatched_value):
    """R3 Gate 1b: Worker copy_batch có expected bindings không khớp snapshot -> từ chối 'invalid_snapshot' trước IO."""
    v1 = str(tmp_path / "clip_mis.mp4")
    _create_dummy_video(v1)
    valid_snap = _make_valid_test_snapshot(tmp_path, video_path=v1)

    req_kwargs = {
        "request_id": "req_mis_test",
        "generation": 1,
        "intent": "copy_batch",
        "project_name": valid_snap.project_name,
        "video_paths": (v1,),
        "prepared_batch_snapshot": valid_snap,
        "selected_batch_index": 0,
        "expected_snapshot_id": valid_snap.snapshot_id,
        "expected_batch_id": "B01",
        "expected_rendered_sha256": valid_snap.batches[0].rendered_sha256,
        "expected_formatter_policy": "source_only_v1",
        "expected_manifest_id": valid_snap.manifest_id,
        "expected_project_name": valid_snap.project_name,
    }
    req_kwargs[mismatched_field] = mismatched_value

    req = PromptPreparationRequest(**req_kwargs)

    with patch("os.stat") as mock_stat:
        worker = PromptPreparationWorker(request=req)
        results = []
        worker.finished_signal.connect(lambda r_id, s, msg, payload: results.append((s, msg, payload)))
        worker.run()

        assert len(results) == 1
        success, msg, payload = results[0]
        assert success is False
        assert msg == "invalid_snapshot"
        assert mock_stat.call_count == 0


def test_terminal_without_pending_record_discards_zero_writes(tmp_path, qapp):
    """R3 Gate 2a: Terminal không fallback _active_prompt_intent khi thiếu pending record.
    Thiếu pending record -> discard an toàn, 0 clipboard write, 0 signal emit."""
    page = ReviewPage()
    QApplication.clipboard().setText("ORIGINAL_SAFE_CLIPBOARD")

    copied_signals = []
    page.prompt_copied.connect(copied_signals.append)

    # Không có _pending_prompt_expected (None)
    page._pending_prompt_expected = None
    page._active_prompt_intent = "copy_batch"

    result_payload = {
        "request_id": "req_orphan",
        "generation": page._prompt_generation,
        "intent": "copy_batch",
        "prompt_text": "FORGED_CLIPBOARD_TEXT",
        "prompt_bytes": len("FORGED_CLIPBOARD_TEXT".encode("utf-8")),
    }

    page._on_prompt_preparation_finished("req_orphan", True, "success", result_payload)

    assert QApplication.clipboard().text() == "ORIGINAL_SAFE_CLIPBOARD"
    assert len(copied_signals) == 0


@pytest.mark.parametrize("corrupt_field,corrupt_value", [
    ("generation", True),            # bool generation
    ("generation", False),           # bool generation
    ("generation", 999),             # generation mismatch
    ("generation", "0"),             # str generation
    ("request_id", "wrong_req_id"),  # request_id mismatch
    ("intent", "prepare_batches"),   # intent mismatch
    ("intent", "run_ai"),            # intent mismatch
    ("snapshot_id", "wrong_snap"),   # snapshot_id mismatch
    ("manifest_id", "wrong_man"),    # manifest_id mismatch
    ("project_name", "wrong_proj"),  # project_name mismatch
    ("batch_id", "wrong_batch"),     # batch_id mismatch
    ("selected_batch_index", 1),     # selected_batch_index mismatch
    ("selected_batch_index", True),  # bool index
    ("rendered_sha256", "d" * 64),   # sha256 mismatch
    ("prompt_text", "tampered text"), # prompt digest mismatch with sha256
])
def test_terminal_result_envelope_mismatch_or_bool_generation_discards(tmp_path, qapp, corrupt_field, corrupt_value):
    """R3 Gate 2b: Success result envelope có bất kỳ trường nào sai, lệch kiểu bool/int/str hoặc hash -> 0 write."""
    page = ReviewPage()
    QApplication.clipboard().setText("ORIGINAL_UNTOUCHED_CLIPBOARD")

    copied_signals = []
    page.prompt_copied.connect(copied_signals.append)

    v1 = str(tmp_path / "clip_env.mp4")
    _create_dummy_video(v1)
    prompt_text = "AUTHENTIC_PROMPT_CONTENT"
    valid_snap = _make_valid_test_snapshot(tmp_path, prompt_text=prompt_text, video_path=v1)

    sig = page._extract_context_signature(page._get_current_context())
    owned_snap = dataclasses.replace(valid_snap, ui_context_signature=sig)
    page._prepared_batch_snapshot = owned_snap

    page.cbo_batch_selector.addItem("Lô 1", userData=0)
    page.cbo_batch_selector.setCurrentIndex(0)

    selected_b = owned_snap.batches[0]
    req_id = "req_env_test"
    page._active_prompt_request_id = req_id
    page._pending_prompt_expected = {
        "request_id": req_id,
        "intent": "copy_batch",
        "generation": page._prompt_generation,
        "snapshot_id": owned_snap.snapshot_id,
        "manifest_id": owned_snap.manifest_id,
        "project_name": owned_snap.project_name,
        "batch_id": selected_b.batch_id,
        "batch_index": 0,
        "rendered_sha256": selected_b.rendered_sha256,
        "rendered_bytes": selected_b.rendered_bytes,
        "expected_creative_context_signature": None,
    }

    result = {
        "request_id": req_id,
        "generation": page._prompt_generation,
        "intent": "copy_batch",
        "selected_batch_index": 0,
        "batch_id": selected_b.batch_id,
        "snapshot_id": owned_snap.snapshot_id,
        "manifest_id": owned_snap.manifest_id,
        "project_name": owned_snap.project_name,
        "rendered_sha256": selected_b.rendered_sha256,
        "prompt_text": prompt_text,
        "prompt_bytes": len(prompt_text.encode("utf-8")),
    }
    result[corrupt_field] = corrupt_value

    page._on_prompt_preparation_finished(req_id, True, "success", result)

    assert QApplication.clipboard().text() == "ORIGINAL_UNTOUCHED_CLIPBOARD"
    assert len(copied_signals) == 0


def test_terminal_stale_or_closing_discards_zero_writes(tmp_path, qapp):
    """R3 Gate 2c: Khi UI đã closing hoặc generation đã sang phiên mới -> discard 0 writes."""
    page = ReviewPage()
    QApplication.clipboard().setText("INITIAL_CLIPBOARD")

    copied_signals = []
    page.prompt_copied.connect(copied_signals.append)

    v1 = str(tmp_path / "clip_stale.mp4")
    _create_dummy_video(v1)
    prompt_text = "PROMPT_STALE_TEST"
    valid_snap = _make_valid_test_snapshot(tmp_path, prompt_text=prompt_text, video_path=v1)

    sig = page._extract_context_signature(page._get_current_context())
    owned_snap = dataclasses.replace(valid_snap, ui_context_signature=sig)
    page._prepared_batch_snapshot = owned_snap

    page.cbo_batch_selector.addItem("Lô 1", userData=0)
    page.cbo_batch_selector.setCurrentIndex(0)

    selected_b = owned_snap.batches[0]
    req_id = "req_stale_test"
    page._active_prompt_request_id = req_id
    page._pending_prompt_expected = {
        "request_id": req_id,
        "intent": "copy_batch",
        "generation": page._prompt_generation,
        "snapshot_id": owned_snap.snapshot_id,
        "manifest_id": owned_snap.manifest_id,
        "project_name": owned_snap.project_name,
        "batch_id": selected_b.batch_id,
        "batch_index": 0,
        "rendered_sha256": selected_b.rendered_sha256,
        "rendered_bytes": selected_b.rendered_bytes,
        "expected_creative_context_signature": None,
    }

    result = {
        "request_id": req_id,
        "generation": page._prompt_generation,
        "intent": "copy_batch",
        "selected_batch_index": 0,
        "batch_id": selected_b.batch_id,
        "snapshot_id": owned_snap.snapshot_id,
        "manifest_id": owned_snap.manifest_id,
        "project_name": owned_snap.project_name,
        "rendered_sha256": selected_b.rendered_sha256,
        "prompt_text": prompt_text,
        "prompt_bytes": len(prompt_text.encode("utf-8")),
    }

    # Case A: Closing
    page._review_close_pending = True
    page._on_prompt_preparation_finished(req_id, True, "success", result)
    assert QApplication.clipboard().text() == "INITIAL_CLIPBOARD"
    assert len(copied_signals) == 0

    # Reset closing, increment generation
    page._review_close_pending = False
    page._prompt_generation += 1  # Stale generation!
    page._on_prompt_preparation_finished(req_id, True, "success", result)
    assert QApplication.clipboard().text() == "INITIAL_CLIPBOARD"
    assert len(copied_signals) == 0


def test_terminal_error_and_cancel_cleanup_zero_writes(qapp):
    """R3 Gate 2d: Error hoặc cancel terminal dọn dẹp nút/labels đúng chuẩn mà không ghi clipboard."""
    page = ReviewPage()
    QApplication.clipboard().setText("SAFE_ERROR_CLIPBOARD")

    req_id = "req_err_test"
    page._active_prompt_request_id = req_id
    page._active_prompt_intent = "copy_batch"
    page._pending_prompt_expected = {
        "request_id": req_id,
        "intent": "copy_batch",
        "generation": page._prompt_generation,
        "expected_creative_context_signature": None,
    }

    page._on_prompt_preparation_finished(req_id, False, "source_changed", {})
    assert QApplication.clipboard().text() == "SAFE_ERROR_CLIPBOARD"
    assert page._pending_prompt_expected is None


# =============================================================================
# 10. G06-06A3-FINAL PROBES — REQUIRED SUCCESS ENVELOPE GATE
# =============================================================================

@pytest.mark.parametrize("corrupt_mode", [
    "missing_request_id",
    "none_request_id",
    "blank_request_id",
    "missing_generation",
    "none_generation",
    "bool_true_generation",
    "bool_false_generation",
    "str_generation",
    "missing_intent",
    "none_intent",
    "blank_intent",
    "missing_all_three",
])
def test_terminal_copy_batch_missing_envelope_fields_discards_zero_writes(tmp_path, qapp, corrupt_mode):
    """FINAL-A Probe 1: copy_batch kết quả thiếu/sai kiểu request_id, generation, intent -> 0 clipboard write."""
    page = ReviewPage()
    QApplication.clipboard().setText("ORIGINAL_SAFE_CLIPBOARD")

    copied_signals = []
    page.prompt_copied.connect(copied_signals.append)

    v1 = str(tmp_path / "clip_final_copy.mp4")
    _create_dummy_video(v1)
    prompt_text = "AUTHENTIC_PROMPT_CONTENT"
    valid_snap = _make_valid_test_snapshot(tmp_path, prompt_text=prompt_text, video_path=v1)

    sig = page._extract_context_signature(page._get_current_context())
    owned_snap = dataclasses.replace(valid_snap, ui_context_signature=sig)
    page._prepared_batch_snapshot = owned_snap

    page.cbo_batch_selector.addItem("Lô 1", userData=0)
    page.cbo_batch_selector.setCurrentIndex(0)

    selected_b = owned_snap.batches[0]
    req_id = "req_final_test"
    page._active_prompt_request_id = req_id
    page._pending_prompt_expected = {
        "request_id": req_id,
        "intent": "copy_batch",
        "generation": page._prompt_generation,
        "snapshot_id": owned_snap.snapshot_id,
        "manifest_id": owned_snap.manifest_id,
        "project_name": owned_snap.project_name,
        "batch_id": selected_b.batch_id,
        "batch_index": 0,
        "rendered_sha256": selected_b.rendered_sha256,
        "rendered_bytes": selected_b.rendered_bytes,
        "expected_creative_context_signature": None,
    }

    result = {
        "request_id": req_id,
        "generation": page._prompt_generation,
        "intent": "copy_batch",
        "selected_batch_index": 0,
        "batch_id": selected_b.batch_id,
        "snapshot_id": owned_snap.snapshot_id,
        "manifest_id": owned_snap.manifest_id,
        "project_name": owned_snap.project_name,
        "rendered_sha256": selected_b.rendered_sha256,
        "prompt_text": prompt_text,
        "prompt_bytes": len(prompt_text.encode("utf-8")),
    }

    if corrupt_mode == "missing_request_id":
        del result["request_id"]
    elif corrupt_mode == "none_request_id":
        result["request_id"] = None
    elif corrupt_mode == "blank_request_id":
        result["request_id"] = "   "
    elif corrupt_mode == "missing_generation":
        del result["generation"]
    elif corrupt_mode == "none_generation":
        result["generation"] = None
    elif corrupt_mode == "bool_true_generation":
        result["generation"] = True
    elif corrupt_mode == "bool_false_generation":
        result["generation"] = False
    elif corrupt_mode == "str_generation":
        result["generation"] = str(page._prompt_generation)
    elif corrupt_mode == "missing_intent":
        del result["intent"]
    elif corrupt_mode == "none_intent":
        result["intent"] = None
    elif corrupt_mode == "blank_intent":
        result["intent"] = ""
    elif corrupt_mode == "missing_all_three":
        del result["request_id"]
        del result["generation"]
        del result["intent"]

    page._on_prompt_preparation_finished(req_id, True, "success", result)

    assert QApplication.clipboard().text() == "ORIGINAL_SAFE_CLIPBOARD"
    assert len(copied_signals) == 0


@pytest.mark.parametrize("corrupt_mode", [
    "missing_request_id",
    "none_request_id",
    "missing_generation",
    "bool_generation",
    "missing_intent",
    "missing_all_three",
])
def test_terminal_prepare_batches_missing_envelope_fields_discards_zero_publish(tmp_path, qapp, corrupt_mode):
    """FINAL-A Probe 2: prepare_batches kết quả thiếu envelope fields -> 0 snapshot publish, 0 manifest assign."""
    page = ReviewPage()
    v1 = str(tmp_path / "clip_final_prep.mp4")
    _create_dummy_video(v1)
    valid_snap = _make_valid_test_snapshot(tmp_path, video_path=v1)

    req_id = "req_final_prep"
    page._active_prompt_request_id = req_id
    page._pending_prompt_expected = {
        "request_id": req_id,
        "intent": "prepare_batches",
        "generation": page._prompt_generation,
        "context_signature": page._extract_context_signature(page._get_current_context()),
        "expected_creative_context_signature": None,
    }

    result = {
        "request_id": req_id,
        "generation": page._prompt_generation,
        "intent": "prepare_batches",
        "batch_snapshot": valid_snap,
    }

    if corrupt_mode == "missing_request_id":
        del result["request_id"]
    elif corrupt_mode == "none_request_id":
        result["request_id"] = None
    elif corrupt_mode == "missing_generation":
        del result["generation"]
    elif corrupt_mode == "bool_generation":
        result["generation"] = True
    elif corrupt_mode == "missing_intent":
        del result["intent"]
    elif corrupt_mode == "missing_all_three":
        del result["request_id"]
        del result["generation"]
        del result["intent"]

    page._on_prompt_preparation_finished(req_id, True, "success", result)

    assert page._prepared_batch_snapshot is None
    assert getattr(page, "_manifest", None) is None
    assert page.cbo_batch_selector.count() == 0


