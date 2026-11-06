"""
tests/ui/test_batch_draft_intake.py
=============================================================================
Integration & UI Test Suite cho G06-06A4b-I:
- Dedicated Batch Draft Intake UI in ReviewPage (frame_batch_section).
- PromptPreparationWorker running intent='validate_batch_draft' on real QThread.
- Typed partition geometry validation (BatchSourceRange) & provenance check.
- In-memory ledger (_batch_drafts_ledger) keyed by (snapshot_id, batch_id).
- Progress memory ("Đã nhận X/N bản thảo") & combobox text badges.
- Idempotent same-digest no-op vs different-digest replacement consent.
- Zero mutation of Slot 1 (_video_plan_data), Slot 2 (_kinetic_plan_data),
  or ClipManifest.
- Generic slot guard still rejects batch draft JSON with updated guidance text.
=============================================================================
"""

import os
import json
import time
import hashlib
import pytest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt

from src.ui.director.pages.review_page import ReviewPage
from src.ui.director.director_state import BATCH_DRAFT_REJECT_MSG
from src.core.cache_manager import ScanCacheManager, compute_full_file_sha256
from src.core.batch_draft_contracts import (
    BatchSourceRange,
    ValidatedBatchDraftEntry,
    ERR_MISSING_PREPARED_GEOMETRY,
    ERR_MALFORMED_JSON,
    ERR_OVERSIZE_PAYLOAD,
    ERR_DUPLICATE_JSON_KEYS,
)
from src.ui.director.workers.prompt_preparation_worker import (
    PromptPreparationWorker,
    PromptPreparationRequest,
    PreparedBatchItem,
    PreparedBatchSnapshot,
    BaselineSourceEntry,
    compute_snapshot_id,
    validate_and_normalize_snapshot,
)


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _create_dummy_video(path: str, size: int = 2048, content: bytes = None):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    payload = content if content is not None else (b"RESOLVEFLOW_DUMMY_BATCH_" * (size // 24 + 1))[:size]
    with open(path, "wb") as f:
        f.write(payload)
    digest, prov = compute_full_file_sha256(path)
    return digest, prov


def _make_dummy_manifest_metadata(project_name: str, clips_info: list) -> dict:
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


def _wait_for_review_page(page, timeout_sec: float = 12.0):
    deadline = time.monotonic() + timeout_sec
    app = QApplication.instance()
    while time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        has_prompt_workers = bool(getattr(page, "_prompt_workers_registry", None))
        has_kinetic_workers = bool(getattr(page, "_kinetic_workers_registry", None))
        is_loading = bool(page.is_analysis_loading())
        if not has_prompt_workers and not has_kinetic_workers and not is_loading:
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
# NHÓM 1: WORKER REAL QTHREAD VALIDATE BATCH DRAFT
# =============================================================================

def test_worker_validate_batch_draft_real_qthread(tmp_path, qapp):
    """Worker thẩm định bản thảo lô chạy trên QThread thực:
    - Nhận diện geometry (BatchSourceRange).
    - Thẩm định payload hợp lệ.
    - Phát finished_signal đúng 1 lần với validated_entry và draft_digest."""
    v1 = str(tmp_path / "clip_a.mp4")
    _create_dummy_video(v1, size=1024)
    st = os.stat(v1)
    base_entry = BaselineSourceEntry(
        path=os.path.normpath(os.path.abspath(v1)),
        st_size=st.st_size,
        st_mtime_ns=st.st_mtime_ns,
        st_ctime_ns=getattr(st, "st_ctime_ns", 0),
        st_ino=st.st_ino,
        st_dev=st.st_dev,
    )
    range_c01 = BatchSourceRange(
        clip_id="C01",
        source_in_sec=0.0,
        source_out_sec=60.0,
        partition_index=0,
        total_partitions=1,
        window_ids=("W01", "W02"),
    )
    p_text = "Prompt text for Batch 1"
    b_sha = hashlib.sha256(p_text.encode("utf-8")).hexdigest()
    b_item = PreparedBatchItem(
        batch_id="B01",
        batch_index=0,
        distinct_clip_count=1,
        total_duration_sec=60.0,
        rendered_prompt=p_text,
        rendered_bytes=len(p_text.encode("utf-8")),
        rendered_sha256=b_sha,
        content_signature="sig_b01",
        clip_ids=("C01",),
        items_count=1,
        source_ranges=(range_c01,),
    )
    ev_sig = (os.path.normpath(os.path.abspath(v1)),)
    snap_id = compute_snapshot_id(
        manifest_id="m_test",
        project_name="TestProj",
        baseline_sources=(base_entry,),
        batches=(b_item,),
        evidence_context_signature=ev_sig,
        max_prompt_bytes=32768,
    )
    snapshot = PreparedBatchSnapshot(
        manifest_id="m_test",
        project_name="TestProj",
        snapshot_id=snap_id,
        formatter_policy="source_only_v1",
        total_batches=1,
        planned_items_count=1,
        distinct_planned_clips_count=1,
        planned_clip_ids=("C01",),
        planned_clip_ids_count=1,
        unaccounted_clip_ids=(),
        unaccounted_clips=(),
        adapter_issues=(),
        max_prompt_bytes=32768,
        batches=(b_item,),
        baseline_sources=(base_entry,),
        evidence_context_signature=ev_sig,
        ui_context_signature="ui_sig_123",
    )

    valid_payload = {
        "manifest_id": "m_test",
        "batch_id": "B01",
        "batch_strategy_summary": "Chiến lược cắt ngắn chọn đoạn thoại mở đầu sắc nét.",
        "timeline_segments": [
            {
                "clip_id": "C01",
                "start_sec": 5.0,
                "end_sec": 15.0,
                "action": "keep",
                "speed": 1.0,
                "role": "hook",
                "note": "Mở đầu hấp dẫn",
            }
        ]
    }
    raw_draft_json = json.dumps(valid_payload)

    req = PromptPreparationRequest(
        request_id="req_test_draft_1",
        generation=1,
        intent="validate_batch_draft",
        video_paths=(v1,),
        project_name="TestProj",
        prepared_batch_snapshot=snapshot,
        selected_batch_index=0,
        expected_snapshot_id=snap_id,
        expected_batch_id="B01",
        expected_rendered_sha256=b_sha,
        expected_formatter_policy="source_only_v1",
        expected_manifest_id="m_test",
        expected_project_name="TestProj",
        expected_ui_context_signature="ui_sig_123",
        batch_draft_text=raw_draft_json,
        associate_with_current_snapshot=True,
    )

    results = []
    worker = PromptPreparationWorker(request=req)
    worker.finished_signal.connect(lambda req_id, ok, msg, res: results.append((ok, msg, res)))
    worker.start()

    deadline = time.monotonic() + 10.0
    while len(results) == 0 and time.monotonic() < deadline:
        QApplication.instance().processEvents()
        time.sleep(0.01)
    for _ in range(5):
        QApplication.instance().processEvents()

    assert not worker.isRunning()
    assert len(results) == 1
    ok, msg, res = results[0]
    assert ok is True
    assert msg == "success"
    assert res["intent"] == "validate_batch_draft"
    assert res["batch_id"] == "B01"
    assert res["snapshot_id"] == snap_id
    assert res["binding_mode"] == "user_associated_current_snapshot"
    assert res["total_cuts_count"] == 1
    assert res["total_selected_duration_sec"] == 10.0
    assert "draft_digest" in res and len(res["draft_digest"]) == 64


def test_worker_validate_batch_draft_missing_geometry_rejected(tmp_path, qapp):
    """Snapshot cũ thiếu thông tin source_ranges phải bị từ chối với ERR_MISSING_PREPARED_GEOMETRY."""
    v1 = str(tmp_path / "clip_a.mp4")
    _create_dummy_video(v1, size=1024)
    st = os.stat(v1)
    base_entry = BaselineSourceEntry(
        path=os.path.normpath(os.path.abspath(v1)),
        st_size=st.st_size,
        st_mtime_ns=st.st_mtime_ns,
        st_ctime_ns=getattr(st, "st_ctime_ns", 0),
        st_ino=st.st_ino,
        st_dev=st.st_dev,
    )
    p_text = "Prompt text without geometry"
    b_sha = hashlib.sha256(p_text.encode("utf-8")).hexdigest()
    # Không có source_ranges (legacy empty tuple)
    b_item = PreparedBatchItem(
        batch_id="B01",
        batch_index=0,
        distinct_clip_count=1,
        total_duration_sec=60.0,
        rendered_prompt=p_text,
        rendered_bytes=len(p_text.encode("utf-8")),
        rendered_sha256=b_sha,
        content_signature="sig_b01",
        clip_ids=("C01",),
        items_count=1,
        source_ranges=(),  # Rỗng!
    )
    ev_sig = (os.path.normpath(os.path.abspath(v1)),)
    snap_id = compute_snapshot_id(
        manifest_id="m_test",
        project_name="TestProj",
        baseline_sources=(base_entry,),
        batches=(b_item,),
        evidence_context_signature=ev_sig,
        max_prompt_bytes=32768,
    )
    snapshot = PreparedBatchSnapshot(
        manifest_id="m_test",
        project_name="TestProj",
        snapshot_id=snap_id,
        formatter_policy="source_only_v1",
        total_batches=1,
        planned_items_count=1,
        distinct_planned_clips_count=1,
        planned_clip_ids=("C01",),
        planned_clip_ids_count=1,
        unaccounted_clip_ids=(),
        unaccounted_clips=(),
        adapter_issues=(),
        max_prompt_bytes=32768,
        batches=(b_item,),
        baseline_sources=(base_entry,),
        evidence_context_signature=ev_sig,
    )

    req = PromptPreparationRequest(
        request_id="req_test_draft_no_geom",
        generation=1,
        intent="validate_batch_draft",
        video_paths=(v1,),
        project_name="TestProj",
        prepared_batch_snapshot=snapshot,
        selected_batch_index=0,
        expected_snapshot_id=snap_id,
        expected_batch_id="B01",
        expected_rendered_sha256=b_sha,
        expected_formatter_policy="source_only_v1",
        expected_manifest_id="m_test",
        expected_project_name="TestProj",
        batch_draft_text='{"manifest_id": "m_test", "batch_id": "B01", "batch_strategy_summary": "Test", "timeline_segments": []}',
        associate_with_current_snapshot=True,
    )

    results = []
    worker = PromptPreparationWorker(request=req)
    worker.finished_signal.connect(lambda req_id, ok, msg, res: results.append((ok, msg, res)))
    worker.start()

    deadline = time.monotonic() + 10.0
    while len(results) == 0 and time.monotonic() < deadline:
        QApplication.instance().processEvents()
        time.sleep(0.01)
    for _ in range(5):
        QApplication.instance().processEvents()

    assert len(results) == 1
    ok, msg, res = results[0]
    assert ok is False
    assert msg == "missing_geometry"
    assert res.get("error_code") == ERR_MISSING_PREPARED_GEOMETRY


def test_worker_validate_batch_draft_source_tampered_fails(tmp_path, qapp):
    """Nếu tệp nguồn bị thay đổi kích thước/mtime trước hoặc sau validate, worker phải từ chối."""
    v1 = str(tmp_path / "clip_a.mp4")
    _create_dummy_video(v1, size=1024)
    st = os.stat(v1)
    base_entry = BaselineSourceEntry(
        path=os.path.normpath(os.path.abspath(v1)),
        st_size=st.st_size,
        st_mtime_ns=st.st_mtime_ns,
        st_ctime_ns=getattr(st, "st_ctime_ns", 0),
        st_ino=st.st_ino,
        st_dev=st.st_dev,
    )
    range_c01 = BatchSourceRange("C01", 0.0, 60.0, 0, 1)
    p_text = "Prompt text"
    b_sha = hashlib.sha256(p_text.encode("utf-8")).hexdigest()
    b_item = PreparedBatchItem(
        batch_id="B01",
        batch_index=0,
        distinct_clip_count=1,
        total_duration_sec=60.0,
        rendered_prompt=p_text,
        rendered_bytes=len(p_text.encode("utf-8")),
        rendered_sha256=b_sha,
        content_signature="sig_b01",
        clip_ids=("C01",),
        items_count=1,
        source_ranges=(range_c01,),
    )
    ev_sig = (os.path.normpath(os.path.abspath(v1)),)
    snap_id = compute_snapshot_id(
        manifest_id="m_test",
        project_name="TestProj",
        baseline_sources=(base_entry,),
        batches=(b_item,),
        evidence_context_signature=ev_sig,
        max_prompt_bytes=32768,
    )
    snapshot = PreparedBatchSnapshot(
        manifest_id="m_test",
        project_name="TestProj",
        snapshot_id=snap_id,
        formatter_policy="source_only_v1",
        total_batches=1,
        planned_items_count=1,
        distinct_planned_clips_count=1,
        planned_clip_ids=("C01",),
        planned_clip_ids_count=1,
        unaccounted_clip_ids=(),
        unaccounted_clips=(),
        adapter_issues=(),
        max_prompt_bytes=32768,
        batches=(b_item,),
        baseline_sources=(base_entry,),
        evidence_context_signature=ev_sig,
    )

    # Làm biến đổi file nguồn (thêm bytes)
    with open(v1, "ab") as f:
        f.write(b"TAMPERED_BYTES")

    req = PromptPreparationRequest(
        request_id="req_test_tampered",
        generation=1,
        intent="validate_batch_draft",
        video_paths=(v1,),
        project_name="TestProj",
        prepared_batch_snapshot=snapshot,
        selected_batch_index=0,
        expected_snapshot_id=snap_id,
        expected_batch_id="B01",
        expected_rendered_sha256=b_sha,
        expected_formatter_policy="source_only_v1",
        expected_manifest_id="m_test",
        expected_project_name="TestProj",
        batch_draft_text='{"manifest_id": "m_test", "batch_id": "B01", "batch_strategy_summary": "Test", "timeline_segments": []}',
        associate_with_current_snapshot=True,
    )

    results = []
    worker = PromptPreparationWorker(request=req)
    worker.finished_signal.connect(lambda req_id, ok, msg, res: results.append((ok, msg, res)))
    worker.start()

    deadline = time.monotonic() + 10.0
    while len(results) == 0 and time.monotonic() < deadline:
        QApplication.instance().processEvents()
        time.sleep(0.01)
    for _ in range(5):
        QApplication.instance().processEvents()

    assert len(results) == 1
    ok, msg, res = results[0]
    assert ok is False
    assert msg == "source_changed"


# =============================================================================
# NHÓM 2: UI DEDICATED INTAKE, TWO BATCHES PROGRESS MEMORY, ZERO MUTATION
# =============================================================================

def test_review_page_intake_ui_two_batches_progress_and_no_slot_mutation(tmp_path, qapp):
    """Kiểm tra quy trình thực tế trên ReviewPage:
    - Phân lô tạo 2 lô kịch bản.
    - Nạp bản thảo Lô 1 thành công: tiến độ 'Đã nhận 1/2 bản thảo', badge combobox xuất hiện.
    - Chuyển sang Lô 2, nạp bản thảo Lô 2 thành công: tiến độ 'Đã nhận 2/2 bản thảo'.
    - TUYỆT ĐỐI KHÔNG GÂY MUTATE _video_plan_data hay _kinetic_plan_data (Slot 1 và Slot 2 giữ nguyên rỗng)."""
    page = ReviewPage()

    v1 = str(tmp_path / "clip1.mp4")
    v2 = str(tmp_path / "clip2.mp4")
    d1, p1 = _create_dummy_video(v1, content=b"VIDEO_CLIP_1_DATA")
    d2, p2 = _create_dummy_video(v2, content=b"VIDEO_CLIP_2_DATA")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 30.0, "raw_subtitles": [{"start": 0, "end": 5, "text": "Clip 1 speech"}]},
        model_size="small",
        language="vi",
        verified_identity=(d1, p1),
    )
    cache_mgr.save_scan_result(
        file_path=v2,
        scan_data={"clip_dur": 40.0, "raw_subtitles": [{"start": 0, "end": 6, "text": "Clip 2 speech"}]},
        model_size="small",
        language="vi",
        verified_identity=(d2, p2),
    )

    meta = _make_dummy_manifest_metadata(
        "Project_Two_Batches",
        [
            {"clip_id": "C01", "path": v1, "duration_sec": 30.0, "content_hash": d1},
            {"clip_id": "C02", "path": v2, "duration_sec": 40.0, "content_hash": d2},
        ]
    )

    ctx = {
        "video_paths": [v1, v2],
        "whisper_model": "small",
        "whisper_language": "vi",
        "style_intent": "travel_vlog",
        "target_duration": "full",
        "hook_duration": "30_60s",
        "project_name": "Project_Two_Batches",
        "max_prompt_bytes": 32768,
        "manifest_metadata": meta,
        # Giới hạn 1 clip 1 batch để ép ra đúng 2 batches
        "max_clips_per_batch": 1,
        "target_batch_duration_sec": 30.0,
        "max_batch_duration_sec": 50.0,
    }

    # 1. Kích hoạt phân lô
    page.request_prompt_preparation(
        intent="prepare_batches",
        context=ctx,
        cache_mgr=cache_mgr,
        manifest_cache_dir=str(tmp_path / "m"),
        manifest_metadata=meta,
    )
    _wait_for_review_page(page)

    assert page._prepared_batch_snapshot is not None
    total_batches = page._prepared_batch_snapshot.total_batches
    assert total_batches == 2
    assert page.cbo_batch_selector.count() == 2
    assert page.btn_submit_batch_draft.isEnabled() is True

    snap_id = page._prepared_batch_snapshot.snapshot_id
    manifest_id = page._prepared_batch_snapshot.manifest_id
    b1 = page._prepared_batch_snapshot.batches[0]
    b2 = page._prepared_batch_snapshot.batches[1]

    # Kiểm tra Slot 1 và Slot 2 ban đầu hoàn toàn rỗng
    assert getattr(page, "_video_plan_data", None) is None
    assert getattr(page, "_kinetic_plan_data", None) is None

    # 2. Nạp bản thảo cho Lô 1
    page.cbo_batch_selector.setCurrentIndex(0)
    draft_payload_1 = {
        "manifest_id": manifest_id,
        "batch_id": b1.batch_id,
        "batch_strategy_summary": "Tập trung phân đoạn mở đầu cuốn hút cho Lô 1.",
        "timeline_segments": [
            {
                "clip_id": "C01",
                "start_sec": 2.0,
                "end_sec": 12.0,
                "action": "keep",
                "speed": 1.0,
                "role": "hook",
                "note": "Đoạn thoại chính",
            }
        ]
    }
    page.txt_batch_draft_input.setPlainText(json.dumps(draft_payload_1))
    page.btn_submit_batch_draft.click()
    _wait_for_review_page(page)

    # Kiểm tra tiến độ và ledger
    assert (snap_id, b1.batch_id) in page._batch_drafts_ledger
    assert len(page._batch_drafts_ledger) == 1
    assert "Đã nhận 1/2 bản thảo" in page.lbl_batch_draft_status.text()
    assert "[✓ Đã nhận]" in page.cbo_batch_selector.itemText(0)
    assert "[✓ Đã nhận]" not in page.cbo_batch_selector.itemText(1)

    # 3. Nạp bản thảo cho Lô 2
    page.cbo_batch_selector.setCurrentIndex(1)
    draft_payload_2 = {
        "manifest_id": manifest_id,
        "batch_id": b2.batch_id,
        "batch_strategy_summary": "Tập trung kết bài tóm tắt hành trình cho Lô 2.",
        "timeline_segments": [
            {
                "clip_id": "C02",
                "start_sec": 10.0,
                "end_sec": 25.0,
                "action": "keep",
                "speed": 1.0,
                "role": "climax",
                "note": "Cao trào phong cảnh",
            }
        ]
    }
    page.txt_batch_draft_input.setPlainText(json.dumps(draft_payload_2))
    page.btn_submit_batch_draft.click()
    _wait_for_review_page(page)

    assert (snap_id, b2.batch_id) in page._batch_drafts_ledger
    assert len(page._batch_drafts_ledger) == 2
    assert "Đã nhận 2/2 bản thảo" in page.lbl_batch_draft_status.text()
    assert "[✓ Đã nhận]" in page.cbo_batch_selector.itemText(1)

    # 4. BẢO VỆ TUYỆT ĐỐI BẤT BIẾN: Slot 1 và Slot 2 vẫn KHÔNG BỊ MUTATE
    assert getattr(page, "_video_plan_data", None) is None
    assert getattr(page, "_kinetic_plan_data", None) is None


# =============================================================================
# NHÓM 3: IDEMPOTENT NO-OP & REPLACEMENT CONSENT
# =============================================================================

def test_review_page_intake_idempotent_same_digest_and_replacement_consent(tmp_path, qapp):
    """Kiểm tra:
    - Nạp cùng bản thảo lần 2 -> idempotent no-op (không lỗi, không hỏi xác nhận, digest giữ nguyên).
    - Nạp bản thảo khác digest:
      + Nếu người dùng từ chối (No) -> bản thảo cũ được bảo toàn 100%.
      + Nếu người dùng đồng ý (Yes) -> bản thảo mới được cập nhật trong ledger."""
    page = ReviewPage()

    v1 = str(tmp_path / "clip_idemp.mp4")
    d1, p1 = _create_dummy_video(v1, content=b"VIDEO_CLIP_IDEMPOTENT")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 30.0, "raw_subtitles": [{"start": 0, "end": 5, "text": "Speech"}]},
        model_size="small",
        language="vi",
        verified_identity=(d1, p1),
    )

    meta = _make_dummy_manifest_metadata(
        "Project_Idemp",
        [{"clip_id": "C01", "path": v1, "duration_sec": 30.0, "content_hash": d1}]
    )

    ctx = {
        "video_paths": [v1],
        "whisper_model": "small",
        "whisper_language": "vi",
        "style_intent": "travel_vlog",
        "target_duration": "full",
        "hook_duration": "30_60s",
        "project_name": "Project_Idemp",
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

    snap_id = page._prepared_batch_snapshot.snapshot_id
    manifest_id = page._prepared_batch_snapshot.manifest_id
    b1 = page._prepared_batch_snapshot.batches[0]

    # Nạp bản thảo lần đầu
    payload_v1 = {
        "manifest_id": manifest_id,
        "batch_id": b1.batch_id,
        "batch_strategy_summary": "Bản thảo ban đầu v1.",
        "timeline_segments": [{"clip_id": "C01", "start_sec": 1.0, "end_sec": 10.0, "action": "keep"}]
    }
    page.txt_batch_draft_input.setPlainText(json.dumps(payload_v1))
    page.btn_submit_batch_draft.click()
    _wait_for_review_page(page)

    entry_v1 = page._batch_drafts_ledger[(snap_id, b1.batch_id)]
    digest_v1 = entry_v1.draft_digest

    # 1. Nạp lại đúng bản thảo đó (Idempotent no-op)
    # Không hiện dialog hỏi xác nhận
    with patch.object(QMessageBox, "question") as mock_q:
        page.txt_batch_draft_input.setPlainText(json.dumps(payload_v1))
        page.btn_submit_batch_draft.click()
        _wait_for_review_page(page)
        # Vì cùng payload hoặc worker trả về cùng digest, dialog question không được bật hoặc được chấp thuận
        assert page._batch_drafts_ledger[(snap_id, b1.batch_id)].draft_digest == digest_v1
        assert "trùng khớp" in page.lbl_batch_draft_status.text()

    # 2. Nạp bản thảo khác digest nhưng người dùng CHƯA OPT-IN ghi đè (chk_replace_existing_draft=False)
    payload_v2 = {
        "manifest_id": manifest_id,
        "batch_id": b1.batch_id,
        "batch_strategy_summary": "Bản thảo sửa đổi v2.",
        "timeline_segments": [{"clip_id": "C01", "start_sec": 5.0, "end_sec": 15.0, "action": "keep"}]
    }
    page.chk_replace_existing_draft.setChecked(False)
    page.txt_batch_draft_input.setPlainText(json.dumps(payload_v2))
    page.btn_submit_batch_draft.click()
    _wait_for_review_page(page)
    # Giữ nguyên bản thảo v1!
    assert page._batch_drafts_ledger[(snap_id, b1.batch_id)].draft_digest == digest_v1
    assert "Giữ nguyên bản thảo cũ" in page.lbl_batch_draft_status.text()

    # 3. Nạp bản thảo khác digest và người dùng ĐÃ OPT-IN ghi đè (chk_replace_existing_draft=True)
    page.chk_replace_existing_draft.setChecked(True)
    page.txt_batch_draft_input.setPlainText(json.dumps(payload_v2))
    page.btn_submit_batch_draft.click()
    _wait_for_review_page(page)
    # Cập nhật sang bản thảo v2!
    entry_v2 = page._batch_drafts_ledger[(snap_id, b1.batch_id)]
    assert entry_v2.draft_digest != digest_v1
    assert entry_v2.batch_strategy_summary == "Bản thảo sửa đổi v2."
    assert "Đã nhận 1/1 bản thảo" in page.lbl_batch_draft_status.text()


# =============================================================================
# NHÓM 4: GENERIC SLOT GUARD STILL REJECTS BATCH DRAFT
# =============================================================================

def test_generic_slot_guard_still_rejects_batch_draft(tmp_path, qapp):
    """Kịch bản batch draft dán vào ô JSON tổng thể hoặc Slot 1/2 vẫn bị từ chối
    bởi BATCH_DRAFT_REJECT_MSG với nội dung hướng dẫn ô nạp thực tế."""
    page = ReviewPage()

    batch_draft_payload = {
        "manifest_id": "m_test",
        "batch_id": "B01",
        "batch_strategy_summary": "Chiến lược phân lô",
        "timeline_segments": [{"clip_id": "C01", "start_sec": 0, "end_sec": 10}]
    }
    json_text = json.dumps(batch_draft_payload)

    # 1. Dán vào txt_json_input (ô JSON chính)
    page.txt_json_input.setPlainText(json_text)
    QApplication.instance().processEvents()

    assert BATCH_DRAFT_REJECT_MSG in page.lbl_json_hint.text()
    assert "Nạp Bản Thảo Cho Lô Đang Chọn" in BATCH_DRAFT_REJECT_MSG

    # 2. Thử dán trực tiếp vào Slot 1 qua clipboard
    QApplication.clipboard().setText(json_text)
    page._paste_to_slot("slot1")
    QApplication.instance().processEvents()

    assert BATCH_DRAFT_REJECT_MSG in page.lbl_json_hint.text()
    assert getattr(page, "_video_plan_data", None) is None


# =============================================================================
# NHÓM 5: CODEX PROBE REGRESSION TESTS (G06-06A4b-R1)
# =============================================================================

def test_snapshot_id_canonical_geometry_no_delimiter_collision():
    """Kiểm tra: compute_snapshot_id triệt tiêu delimiter collision ('a,b','c') vs ('a','b,c')."""
    base_src = ()
    r1 = BatchSourceRange("C01", 0.0, 10.0, 0, 1, window_ids=("a,b", "c"))
    r2 = BatchSourceRange("C01", 0.0, 10.0, 0, 1, window_ids=("a", "b,c"))

    b1 = PreparedBatchItem(
        batch_id="B01",
        batch_index=0,
        distinct_clip_count=1,
        total_duration_sec=10.0,
        rendered_prompt="P",
        rendered_bytes=1,
        rendered_sha256="h1",
        content_signature="sig_c",
        clip_ids=("C01",),
        items_count=1,
        source_ranges=(r1,),
    )
    b2 = PreparedBatchItem(
        batch_id="B01",
        batch_index=0,
        distinct_clip_count=1,
        total_duration_sec=10.0,
        rendered_prompt="P",
        rendered_bytes=1,
        rendered_sha256="h1",
        content_signature="sig_c",
        clip_ids=("C01",),
        items_count=1,
        source_ranges=(r2,),
    )

    sig1 = compute_snapshot_id("M01", "P01", base_src, (b1,), "sig", 32768)
    sig2 = compute_snapshot_id("M01", "P01", base_src, (b2,), "sig", 32768)
    assert sig1 != sig2


def test_review_page_intake_direct_api_invalid_input_zero_state_change(tmp_path, qapp):
    """Kiểm tra: Gọi trực tiếp request_prompt_preparation với input không hợp lệ (empty text, wrong type, non-bool)
    phải bị từ chối NGAY LẬP TỨC (early return None):
    - 0 worker cancel
    - 0 prompt generation delta
    - 0 pending record mutation
    - Không làm sập Qt / ném unhandled exception."""
    page = ReviewPage()
    orig_gen = page._prompt_generation
    assert getattr(page, "_pending_prompt_expected", None) is None

    # 1. batch_draft_text rỗng / None
    res = page.request_prompt_preparation(
        intent="validate_batch_draft",
        batch_draft_text="",
    )
    assert res is None
    assert page._prompt_generation == orig_gen
    assert getattr(page, "_pending_prompt_expected", None) is None

    # 2. batch_draft_text không phải str
    res2 = page.request_prompt_preparation(
        intent="validate_batch_draft",
        batch_draft_text=12345,
    )
    assert res2 is None
    assert page._prompt_generation == orig_gen

    # 3. associate_with_current_snapshot không phải bool
    res3 = page.request_prompt_preparation(
        intent="validate_batch_draft",
        batch_draft_text='{"manifest_id": "M01"}',
        associate_with_current_snapshot="true",
    )
    assert res3 is None
    assert page._prompt_generation == orig_gen


def test_review_page_intake_forged_end_sec_zero_ledger_publish(tmp_path, qapp):
    """Kiểm tra: Terminal callback nhận kết quả giả mạo end_sec=9999 (probe của Codex).
    Cổng verify_validated_entry_integrity phải chặn đứng, TUYỆT ĐỐI 0 ledger publish."""
    page = ReviewPage()

    v1 = str(tmp_path / "clip_forge.mp4")
    d1, p1 = _create_dummy_video(v1, content=b"VIDEO_CLIP_FORGE")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 30.0, "raw_subtitles": [{"start": 0, "end": 5, "text": "Speech"}]},
        model_size="small",
        language="vi",
        verified_identity=(d1, p1),
    )

    meta = _make_dummy_manifest_metadata(
        "Project_Forge",
        [{"clip_id": "C01", "path": v1, "duration_sec": 30.0, "content_hash": d1}]
    )

    ctx = {
        "video_paths": [v1],
        "whisper_model": "small",
        "whisper_language": "vi",
        "style_intent": "travel_vlog",
        "project_name": "Project_Forge",
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

    snap = page._prepared_batch_snapshot
    assert snap is not None
    b1 = snap.batches[0]

    # Thiết lập pending expected hợp lệ
    req_id = "req_test_forge_001"
    gen = page._prompt_generation
    curr_sig = page._extract_context_signature(page._get_current_context())
    legit_raw_sha = "a" * 64
    page._active_prompt_request_id = req_id
    page._pending_prompt_expected = {
        "request_id": req_id,
        "intent": "validate_batch_draft",
        "generation": gen,
        "context_signature": curr_sig,
        "manifest_id": snap.manifest_id,
        "project_name": snap.project_name,
        "snapshot_id": snap.snapshot_id,
        "batch_id": b1.batch_id,
        "batch_index": 0,
        "source_ranges": b1.source_ranges,
        "allow_replace_draft": False,
        "expected_raw_payload_sha256": legit_raw_sha,
        "allowed_binding_modes": ("echo_matched",),
        "expected_binding_mode": "echo_matched",
        "expected_creative_context_signature": None,
    }

    # Giả mạo result chứa segment với end_sec=9999
    forged_result = {
        "request_id": req_id,
        "intent": "validate_batch_draft",
        "generation": gen,
        "manifest_id": snap.manifest_id,
        "project_name": snap.project_name,
        "snapshot_id": snap.snapshot_id,
        "batch_id": b1.batch_id,
        "selected_batch_index": 0,
        "binding_mode": "echo_matched",
        "draft_digest": "some_digest_123",
        "raw_payload_sha256": legit_raw_sha,
        "validated_entry": {
            "manifest_id": snap.manifest_id,
            "batch_id": b1.batch_id,
            "snapshot_id": snap.snapshot_id,
            "binding_mode": "echo_matched",
            "batch_strategy_summary": "Forged draft",
            "draft_digest": "some_digest_123",
            "raw_payload_sha256": legit_raw_sha,
            "received_at": "2026-10-29T10:00:00Z",
            "total_cuts_count": 1,
            "total_selected_duration_sec": 9999.0,
            "segments": [
                {
                    "clip_id": "C01",
                    "start_sec": 0.0,
                    "end_sec": 9999.0,
                    "action": "keep",
                    "speed": 1.0,
                    "role": None,
                    "note": None,
                    "source_window_ref": None,
                }
            ],
        },
    }

    import src.core.batch_draft_contracts as bdc
    with patch.object(bdc, "verify_validated_entry_integrity", wraps=bdc.verify_validated_entry_integrity) as spy_gate:
        page._on_prompt_preparation_finished(req_id, True, "OK", forged_result)
        # Khẳng định: Request đã tới tận cổng verify_validated_entry_integrity và bị cổng này từ chối
        assert spy_gate.called
        call_res = spy_gate.spy_return if hasattr(spy_gate, "spy_return") else None

    # Khẳng định 0 ledger publish
    assert (snap.snapshot_id, b1.batch_id) not in page._batch_drafts_ledger
    assert len(page._batch_drafts_ledger) == 0


def test_review_page_intake_valid_snapshot_echo_plus_associate_true(tmp_path, qapp):
    """G06-06A4b-R2: UI button intake với explicit valid snapshot echo + associate=True phải được ACCEPTED.
    (Khắc phục P1 regression: nút UI luôn gửi associate=True nhưng payload có snapshot_id thì core trả echo_matched)."""
    page = ReviewPage()
    v1 = str(tmp_path / "clip_echo.mp4")
    d1, p1 = _create_dummy_video(v1, content=b"VIDEO_CLIP_ECHO_DATA")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 40.0, "raw_subtitles": [{"start": 0, "end": 10, "text": "Speech"}]},
        model_size="small",
        language="vi",
        verified_identity=(d1, p1),
    )
    meta = _make_dummy_manifest_metadata(
        "Project_Echo",
        [{"clip_id": "C01", "path": v1, "duration_sec": 40.0, "content_hash": d1}]
    )
    ctx = {
        "video_paths": [v1],
        "whisper_model": "small",
        "whisper_language": "vi",
        "style_intent": "travel_vlog",
        "project_name": "Project_Echo",
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

    snap = page._prepared_batch_snapshot
    assert snap is not None
    b1 = snap.batches[0]

    # Bản thảo CÓ snapshot_id khớp chính xác với snapshot phân lô hiện tại
    draft_payload = {
        "manifest_id": snap.manifest_id,
        "snapshot_id": snap.snapshot_id,
        "batch_id": b1.batch_id,
        "batch_strategy_summary": "Tập trung mở đầu cuốn hút cho Lô 1 với snapshot echo.",
        "timeline_segments": [
            {
                "clip_id": "C01",
                "start_sec": 2.0,
                "end_sec": 10.0,
                "action": "keep",
                "speed": 1.0,
                "role": "hook",
                "note": "Phần mở đầu",
            }
        ]
    }
    page.txt_batch_draft_input.setPlainText(json.dumps(draft_payload))
    page.btn_submit_batch_draft.click()  # Gửi associate_with_current_snapshot=True
    _wait_for_review_page(page)

    # Khẳng định bản thảo ĐƯỢC CHẤP NHẬN vào ledger
    assert (snap.snapshot_id, b1.batch_id) in page._batch_drafts_ledger
    entry = page._batch_drafts_ledger[(snap.snapshot_id, b1.batch_id)]
    assert entry.binding_mode == "echo_matched"
    assert "Đã nhận 1/" in page.lbl_batch_draft_status.text()


def test_review_page_intake_raw_proof_mismatch_and_malformed_pending(tmp_path, qapp):
    """G06-06A4b-R2: Kiểm tra bắt buộc pending raw hash (64 hex) và từ chối khi raw hash mismatch."""
    page = ReviewPage()
    v1 = str(tmp_path / "clip_proof.mp4")
    d1, p1 = _create_dummy_video(v1, content=b"VIDEO_CLIP_PROOF_DATA")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 30.0, "raw_subtitles": [{"start": 0, "end": 5, "text": "Speech"}]},
        model_size="small",
        language="vi",
        verified_identity=(d1, p1),
    )
    meta = _make_dummy_manifest_metadata(
        "Project_Proof",
        [{"clip_id": "C01", "path": v1, "duration_sec": 30.0, "content_hash": d1}]
    )
    ctx = {
        "video_paths": [v1],
        "whisper_model": "small",
        "whisper_language": "vi",
        "style_intent": "travel_vlog",
        "project_name": "Project_Proof",
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

    snap = page._prepared_batch_snapshot
    b1 = snap.batches[0]

    # 1. Malformed pending raw hash (không đủ 64 ký tự hex) -> 0 publish
    req_id = "req_malformed_hash"
    gen = page._prompt_generation
    curr_sig = page._extract_context_signature(page._get_current_context())
    page._active_prompt_request_id = req_id
    page._pending_prompt_expected = {
        "request_id": req_id,
        "intent": "validate_batch_draft",
        "generation": gen,
        "context_signature": curr_sig,
        "manifest_id": snap.manifest_id,
        "project_name": snap.project_name,
        "snapshot_id": snap.snapshot_id,
        "batch_id": b1.batch_id,
        "batch_index": 0,
        "source_ranges": b1.source_ranges,
        "allow_replace_draft": False,
        "expected_raw_payload_sha256": "short_hash_not_64_hex",
        "allowed_binding_modes": ("echo_matched",),
        "expected_creative_context_signature": None,
    }
    result_mock = {
        "request_id": req_id,
        "intent": "validate_batch_draft",
        "generation": gen,
        "manifest_id": snap.manifest_id,
        "project_name": snap.project_name,
        "snapshot_id": snap.snapshot_id,
        "batch_id": b1.batch_id,
        "selected_batch_index": 0,
        "binding_mode": "echo_matched",
        "draft_digest": "dig_1",
        "raw_payload_sha256": "short_hash_not_64_hex",
        "validated_entry": {
            "manifest_id": snap.manifest_id,
            "batch_id": b1.batch_id,
            "snapshot_id": snap.snapshot_id,
            "binding_mode": "echo_matched",
            "batch_strategy_summary": "Summary",
            "draft_digest": "dig_1",
            "raw_payload_sha256": "short_hash_not_64_hex",
            "received_at": "2026-10-29T10:00:00Z",
            "total_cuts_count": 1,
            "total_selected_duration_sec": 5.0,
            "segments": [
                {"clip_id": "C01", "start_sec": 0.0, "end_sec": 5.0, "action": "keep", "speed": 1.0, "role": None, "note": None, "source_window_ref": None}
            ],
        },
    }
    page._on_prompt_preparation_finished(req_id, True, "OK", result_mock)
    assert (snap.snapshot_id, b1.batch_id) not in page._batch_drafts_ledger

    # 2. Raw hash mismatch: pending có hashA (64 hex), nhưng result/validated_entry mang hashB (64 hex)
    hash_a = "a" * 64
    hash_b = "b" * 64
    req_id2 = "req_hash_mismatch"
    page._active_prompt_request_id = req_id2
    page._pending_prompt_expected = {
        "request_id": req_id2,
        "intent": "validate_batch_draft",
        "generation": gen,
        "context_signature": curr_sig,
        "manifest_id": snap.manifest_id,
        "project_name": snap.project_name,
        "snapshot_id": snap.snapshot_id,
        "batch_id": b1.batch_id,
        "batch_index": 0,
        "source_ranges": b1.source_ranges,
        "allow_replace_draft": False,
        "expected_raw_payload_sha256": hash_a,
        "allowed_binding_modes": ("echo_matched",),
        "expected_creative_context_signature": None,
    }
    result_mismatch = dict(result_mock, request_id=req_id2, raw_payload_sha256=hash_b)
    result_mismatch["validated_entry"] = dict(result_mock["validated_entry"], raw_payload_sha256=hash_b)
    page._on_prompt_preparation_finished(req_id2, True, "OK", result_mismatch)
    assert (snap.snapshot_id, b1.batch_id) not in page._batch_drafts_ledger


def test_review_page_intake_deleted_entry_race_no_resurrection(tmp_path, qapp):
    """G06-06A4b-R2: Khi bản thảo cũ bị xóa trong lúc worker chạy (cur_digest None != exp_old_digest D1),
    hệ thống phải từ chối nạp, tuyệt đối không phục hồi (no resurrection)."""
    page = ReviewPage()
    v1 = str(tmp_path / "clip_race.mp4")
    d1, p1 = _create_dummy_video(v1, content=b"VIDEO_CLIP_RACE_DATA")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 30.0, "raw_subtitles": [{"start": 0, "end": 5, "text": "Speech"}]},
        model_size="small",
        language="vi",
        verified_identity=(d1, p1),
    )
    meta = _make_dummy_manifest_metadata(
        "Project_Race",
        [{"clip_id": "C01", "path": v1, "duration_sec": 30.0, "content_hash": d1}]
    )
    ctx = {
        "video_paths": [v1],
        "whisper_model": "small",
        "whisper_language": "vi",
        "style_intent": "travel_vlog",
        "project_name": "Project_Race",
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

    snap = page._prepared_batch_snapshot
    b1 = snap.batches[0]

    # Giả lập đã có bản thảo D1 trong ledger
    from src.core.batch_draft_contracts import validate_batch_draft_payload
    v_res1 = validate_batch_draft_payload(
        {
            "manifest_id": snap.manifest_id,
            "snapshot_id": snap.snapshot_id,
            "batch_id": b1.batch_id,
            "batch_strategy_summary": "Old summary",
            "timeline_segments": [{"clip_id": "C01", "start_sec": 0.0, "end_sec": 5.0, "action": "keep", "speed": 1.0, "role": None, "note": None}],
        },
        expected_manifest_id=snap.manifest_id,
        expected_snapshot_id=snap.snapshot_id,
        expected_batch_id=b1.batch_id,
        source_ranges=b1.source_ranges,
    )
    assert v_res1.ok
    old_entry = v_res1.entry
    page._batch_drafts_ledger[(snap.snapshot_id, b1.batch_id)] = old_entry

    # Tạo bản thảo mới D2 hợp lệ hoàn toàn qua validator
    v_res2 = validate_batch_draft_payload(
        {
            "manifest_id": snap.manifest_id,
            "snapshot_id": snap.snapshot_id,
            "batch_id": b1.batch_id,
            "batch_strategy_summary": "New summary",
            "timeline_segments": [{"clip_id": "C01", "start_sec": 0.0, "end_sec": 6.0, "action": "keep", "speed": 1.0, "role": None, "note": None}],
        },
        expected_manifest_id=snap.manifest_id,
        expected_snapshot_id=snap.snapshot_id,
        expected_batch_id=b1.batch_id,
        source_ranges=b1.source_ranges,
    )
    assert v_res2.ok
    new_entry = v_res2.entry

    # Thiết lập pending expected ghi nhận old_digest=old_entry.draft_digest
    req_id = "req_race_delete"
    gen = page._prompt_generation
    curr_sig = page._extract_context_signature(page._get_current_context())
    page._active_prompt_request_id = req_id
    page._pending_prompt_expected = {
        "request_id": req_id,
        "intent": "validate_batch_draft",
        "generation": gen,
        "context_signature": curr_sig,
        "manifest_id": snap.manifest_id,
        "project_name": snap.project_name,
        "snapshot_id": snap.snapshot_id,
        "batch_id": b1.batch_id,
        "batch_index": 0,
        "source_ranges": b1.source_ranges,
        "allow_replace_draft": True,
        "expected_existing_draft_digest": old_entry.draft_digest,
        "expected_raw_payload_sha256": new_entry.raw_payload_sha256,
        "allowed_binding_modes": ("echo_matched",),
        "expected_creative_context_signature": None,
    }

    # BẤT NGỜ: Record bị xóa khỏi ledger trước khi callback về (cur_entry = None)
    del page._batch_drafts_ledger[(snap.snapshot_id, b1.batch_id)]

    result_new = {
        "request_id": req_id,
        "intent": "validate_batch_draft",
        "generation": gen,
        "manifest_id": snap.manifest_id,
        "project_name": snap.project_name,
        "snapshot_id": snap.snapshot_id,
        "batch_id": b1.batch_id,
        "selected_batch_index": 0,
        "binding_mode": "echo_matched",
        "draft_digest": new_entry.draft_digest,
        "raw_payload_sha256": new_entry.raw_payload_sha256,
        "validated_entry": new_entry.to_dict(),
    }

    page._on_prompt_preparation_finished(req_id, True, "OK", result_new)

    # Khẳng định: Phải từ chối, không ghi đè, không resurrect stale!
    assert (snap.snapshot_id, b1.batch_id) not in page._batch_drafts_ledger
    assert "thay đổi trong lúc thẩm định" in page.lbl_batch_draft_status.text()


@pytest.mark.parametrize(
    "invalid_policy,omit_key",
    [
        (None, False),
        (None, True),
        ((), False),
        ("echo_matched", False),
        (["echo_matched"], False),
        ({"echo_matched": True}, False),
        (("unknown_mode",), False),
        (("echo_matched", "invalid_enum"), False),
    ],
)
def test_review_page_intake_invalid_binding_policy_rejected_zero_helper(tmp_path, qapp, invalid_policy, omit_key):
    """G06-06A4b-FINAL: Bắt buộc pending record phải có allowed_binding_modes là non-empty tuple hợp lệ.
    Bất kỳ giá trị nào không phải non-empty tuple chứa enum hợp lệ (kể cả khi legacy expected_binding_mode
    hợp lệ) đều phải bị từ chối NGAY TRƯỚC integrity helper, 0 publish ledger, 0 resurrect.
    Kèm theo positive control kiểm chứng valid tuple được nhận."""
    page = ReviewPage()
    v1 = str(tmp_path / "clip_policy.mp4")
    d1, p1 = _create_dummy_video(v1, content=b"VIDEO_CLIP_POLICY_DATA")

    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 30.0, "raw_subtitles": [{"start": 0, "end": 5, "text": "Policy Speech"}]},
        model_size="small",
        language="vi",
        verified_identity=(d1, p1),
    )
    meta = _make_dummy_manifest_metadata(
        "Project_Policy",
        [{"clip_id": "C01", "path": v1, "duration_sec": 30.0, "content_hash": d1}],
    )
    ctx = {
        "video_paths": [v1],
        "whisper_model": "small",
        "whisper_language": "vi",
        "style_intent": "travel_vlog",
        "project_name": "Project_Policy",
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

    snap = page._prepared_batch_snapshot
    b1 = snap.batches[0]

    from src.core.batch_draft_contracts import validate_batch_draft_payload
    v_res = validate_batch_draft_payload(
        {
            "manifest_id": snap.manifest_id,
            "snapshot_id": snap.snapshot_id,
            "batch_id": b1.batch_id,
            "batch_strategy_summary": "Policy test draft",
            "timeline_segments": [{"clip_id": "C01", "start_sec": 0.0, "end_sec": 5.0, "action": "keep", "speed": 1.0, "role": None, "note": None}],
        },
        expected_manifest_id=snap.manifest_id,
        expected_snapshot_id=snap.snapshot_id,
        expected_batch_id=b1.batch_id,
        source_ranges=b1.source_ranges,
    )
    assert v_res.ok
    valid_entry = v_res.entry

    req_id_pos = "req_policy_positive"
    gen = page._prompt_generation
    curr_sig = page._extract_context_signature(page._get_current_context())

    result_payload = {
        "request_id": req_id_pos,
        "intent": "validate_batch_draft",
        "generation": gen,
        "manifest_id": snap.manifest_id,
        "project_name": snap.project_name,
        "snapshot_id": snap.snapshot_id,
        "batch_id": b1.batch_id,
        "selected_batch_index": 0,
        "binding_mode": "echo_matched",
        "draft_digest": valid_entry.draft_digest,
        "raw_payload_sha256": valid_entry.raw_payload_sha256,
        "validated_entry": valid_entry.to_dict(),
    }

    # 1. Positive Control: Với allowed_binding_modes = ("echo_matched",), helper PHẢI được gọi và entry được nạp
    import src.core.batch_draft_contracts as bdc
    page._active_prompt_request_id = req_id_pos
    page._pending_prompt_expected = {
        "request_id": req_id_pos,
        "intent": "validate_batch_draft",
        "generation": gen,
        "context_signature": curr_sig,
        "manifest_id": snap.manifest_id,
        "project_name": snap.project_name,
        "snapshot_id": snap.snapshot_id,
        "batch_id": b1.batch_id,
        "batch_index": 0,
        "source_ranges": b1.source_ranges,
        "allow_replace_draft": False,
        "expected_raw_payload_sha256": valid_entry.raw_payload_sha256,
        "allowed_binding_modes": ("echo_matched",),
        "expected_binding_mode": "echo_matched",
        "expected_creative_context_signature": None,
    }
    with patch.object(bdc, "verify_validated_entry_integrity", wraps=bdc.verify_validated_entry_integrity) as spy_gate_pos:
        page._on_prompt_preparation_finished(req_id_pos, True, "OK", result_payload)
        assert spy_gate_pos.called, "Positive control must call verify_validated_entry_integrity"
        assert (snap.snapshot_id, b1.batch_id) in page._batch_drafts_ledger
        assert page._batch_drafts_ledger[(snap.snapshot_id, b1.batch_id)].draft_digest == valid_entry.draft_digest

    # Xóa ledger để test negative
    page._batch_drafts_ledger.clear()

    # 2. Negative Test: Cung cấp invalid_policy trong khi expected_binding_mode legacy vẫn hợp lệ
    req_id_neg = f"req_policy_neg_{omit_key}"
    page._active_prompt_request_id = req_id_neg
    pending_neg = {
        "request_id": req_id_neg,
        "intent": "validate_batch_draft",
        "generation": gen,
        "context_signature": curr_sig,
        "manifest_id": snap.manifest_id,
        "project_name": snap.project_name,
        "snapshot_id": snap.snapshot_id,
        "batch_id": b1.batch_id,
        "batch_index": 0,
        "source_ranges": b1.source_ranges,
        "allow_replace_draft": False,
        "expected_raw_payload_sha256": valid_entry.raw_payload_sha256,
        "expected_binding_mode": "echo_matched",  # Legacy mode exists and valid!
    }
    if not omit_key:
        pending_neg["allowed_binding_modes"] = invalid_policy

    page._pending_prompt_expected = pending_neg
    result_neg = dict(result_payload, request_id=req_id_neg)

    with patch.object(bdc, "verify_validated_entry_integrity", wraps=bdc.verify_validated_entry_integrity) as spy_gate_neg:
        page._on_prompt_preparation_finished(req_id_neg, True, "OK", result_neg)
        # Bắt buộc: Helper KHÔNG được gọi vì bị chặn trước cổng integrity
        assert not spy_gate_neg.called, f"Gate helper should NOT be called for invalid_policy={invalid_policy!r}"

    # Khẳng định: 0 ledger publish, pending_expected bị reset
    assert (snap.snapshot_id, b1.batch_id) not in page._batch_drafts_ledger
    assert page._pending_prompt_expected is None


