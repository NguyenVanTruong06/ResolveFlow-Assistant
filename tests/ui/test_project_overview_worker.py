"""
tests/ui/test_project_overview_worker.py
=============================================================================
Unit & Integration Test Suite cho G06-06A4c2a-I:
- Overview worker và artifact thuộc quyền sở hữu của app.
- compute_project_overview_ledger_signature helper (canonical JSON, domain, semantic fields, no received_at).
- PreparedProjectOverviewArtifact frozen completion value object.
- validate_project_overview_result_against_artifact envelope & content verification.
- Real QThread execution cho intent='copy_project_overview' (partial 2/3, full 3/3+unaccounted).
- Zero-draft no_data, exact budget, budget overflow, pre/post baseline source check.
- Rejection of forged result / tampered prompt with recomputed hash.
- Cooperative cancellation lifecycle & zero artifact on failure.
- Zero IO / stat on unaccounted clips, zero Resolve / model calls.
=============================================================================
"""

import os
import stat
import time
import json
import math
import hashlib
import pytest
from typing import Dict, Any, Tuple
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThread

from src.core.batch_draft_contracts import (
    BatchSourceRange,
    ValidatedSegmentCut,
    ValidatedBatchDraftEntry,
)
from src.core.project_overview_contracts import (
    BatchPartitionRef,
    OverviewUnaccountedClip,
    ProjectOverviewContextInput,
    ProjectOverviewRenderResult,
    ERR_OVERVIEW_NO_DRAFTS,
    ERR_OVERVIEW_BUDGET_OVERFLOW,
    ERR_OVERVIEW_INVALID_CONTEXT,
    FORMATTER_POLICY as OVERVIEW_FORMATTER_POLICY,
)
from src.ui.director.workers.prompt_preparation_worker import (
    PromptPreparationWorker,
    PromptPreparationRequest,
    PreparedBatchItem,
    PreparedBatchSnapshot,
    BaselineSourceEntry,
    UnaccountedClipEntry,
    AdapterIssueEntry,
    compute_snapshot_id,
    validate_and_normalize_snapshot,
    compute_project_overview_ledger_signature,
    PreparedProjectOverviewArtifact,
    validate_project_overview_result_against_artifact,
)


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _create_dummy_video(path: str, size: int = 1024, fill_byte: bytes = b"A") -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(fill_byte * size)
    return os.path.normpath(os.path.abspath(path))


def _make_dummy_baseline(path: str) -> BaselineSourceEntry:
    norm_p = os.path.normpath(os.path.abspath(path))
    st = os.stat(norm_p)
    return BaselineSourceEntry(
        path=norm_p,
        st_size=st.st_size,
        st_mtime_ns=st.st_mtime_ns,
        st_ctime_ns=getattr(st, "st_ctime_ns", 0),
        st_ino=st.st_ino,
        st_dev=st.st_dev,
    )


def _make_dummy_draft_entry(
    manifest_id: str,
    snapshot_id: str,
    batch_id: str,
    source_ranges: Tuple[BatchSourceRange, ...] = None,
    clip_id: str = "C01",
    received_at: str = "2026-10-29T10:00:00",
    summary: str = "Chiến lược phân bổ cảnh quay A",
    duration_sec: float = 30.0,
) -> ValidatedBatchDraftEntry:
    if source_ranges is None:
        source_ranges = (
            BatchSourceRange(
                clip_id=clip_id,
                source_in_sec=0.0,
                source_out_sec=duration_sec,
                partition_index=0,
                total_partitions=1,
                window_ids=("W01", "W02"),
            ),
        )
    r0 = source_ranges[0]
    payload = {
        "manifest_id": manifest_id,
        "snapshot_id": snapshot_id,
        "batch_id": batch_id,
        "batch_strategy_summary": summary,
        "timeline_segments": [
            {
                "clip_id": r0.clip_id,
                "start_sec": r0.source_in_sec,
                "end_sec": min(r0.source_in_sec + duration_sec, r0.source_out_sec),
                "action": "keep",
                "speed": 1.0,
                "role": "dialogue",
                "note": "Đoạn thoại mở đầu",
            }
        ],
    }
    from src.core.batch_draft_contracts import validate_batch_draft_payload
    res = validate_batch_draft_payload(
        payload,
        expected_manifest_id=manifest_id,
        expected_snapshot_id=snapshot_id,
        expected_batch_id=batch_id,
        source_ranges=source_ranges,
    )
    assert res.ok, f"Failed to create valid draft entry: {res.error_message}"
    assert res.entry is not None
    if received_at != res.entry.received_at:
        import dataclasses
        return dataclasses.replace(res.entry, received_at=received_at)
    return res.entry


def _wait_for_worker(worker: PromptPreparationWorker, timeout_sec: float = 5.0) -> Tuple[bool, str, Dict[str, Any]]:
    deadline = time.monotonic() + timeout_sec
    terminal_res = []

    def _on_finished(r_id, ok, msg, res):
        terminal_res.append((ok, msg, res))

    worker.finished_signal.connect(_on_finished)
    worker.start()

    app = QApplication.instance()
    try:
        while time.monotonic() < deadline:
            if app is not None:
                app.processEvents()
            if terminal_res and worker.isFinished():
                break
            time.sleep(0.01)
    finally:
        if not worker.isFinished():
            worker.cancel()
            worker.wait(1000)

    if not terminal_res:
        pytest.fail(f"Worker không phát tín hiệu terminal sau {timeout_sec}s!")
    assert worker.isFinished(), "Worker phải kết thúc vật lý (physical finished)!"
    return terminal_res[0]


# =============================================================================
# NHÓM 1: HELPER COMPUTE_PROJECT_OVERVIEW_LEDGER_SIGNATURE
# =============================================================================

def test_compute_ledger_signature_positive_and_determinism():
    """Chữ ký ledger được tính quyết định luận từ canonical JSON bỏ received_at."""
    m_id = "m_proj_01"
    s_id = "s_snap_01"
    e1 = _make_dummy_draft_entry(m_id, s_id, "B01", clip_id="C01", received_at="2026-10-29T10:00:00")
    e2 = _make_dummy_draft_entry(m_id, s_id, "B02", clip_id="C02", received_at="2026-10-29T10:05:00")

    sig1 = compute_project_overview_ledger_signature(s_id, (e1, e2))
    sig2 = compute_project_overview_ledger_signature(s_id, [e2, e1])  # đảo thứ tự đầu vào

    assert isinstance(sig1, str)
    assert len(sig1) == 64
    assert sig1 == sig2, "Chữ ký phải độc lập với thứ tự truyền vào nhờ sắp xếp theo batch_id"


def test_compute_ledger_signature_received_at_invariance():
    """Chỉ đổi timestamp received_at không làm thay đổi chữ ký ledger."""
    m_id = "m_proj_01"
    s_id = "s_snap_01"
    e1_a = _make_dummy_draft_entry(m_id, s_id, "B01", received_at="2026-10-29T10:00:00")
    e1_b = _make_dummy_draft_entry(m_id, s_id, "B01", received_at="2026-10-29T11:59:59")

    sig_a = compute_project_overview_ledger_signature(s_id, (e1_a,))
    sig_b = compute_project_overview_ledger_signature(s_id, (e1_b,))

    assert sig_a == sig_b, "received_at audit-only timestamp không được ảnh hưởng semantic signature"


def test_compute_ledger_signature_semantic_changes_detected():
    """Thay đổi bất kỳ trường semantic nào (summary, duration, cut) làm đổi signature."""
    m_id = "m_proj_01"
    s_id = "s_snap_01"
    e1 = _make_dummy_draft_entry(m_id, s_id, "B01", summary="Chiến lược gốc", duration_sec=30.0)
    e1_modified_summary = _make_dummy_draft_entry(m_id, s_id, "B01", summary="Chiến lược mới", duration_sec=30.0)
    e1_modified_duration = _make_dummy_draft_entry(m_id, s_id, "B01", summary="Chiến lược gốc", duration_sec=45.0)

    sig_base = compute_project_overview_ledger_signature(s_id, (e1,))
    sig_mod_sum = compute_project_overview_ledger_signature(s_id, (e1_modified_summary,))
    sig_mod_dur = compute_project_overview_ledger_signature(s_id, (e1_modified_duration,))

    assert sig_base != sig_mod_sum
    assert sig_base != sig_mod_dur


def test_compute_ledger_signature_add_delete_detected():
    """Thêm hoặc xóa draft làm đổi chữ ký ledger."""
    m_id = "m_proj_01"
    s_id = "s_snap_01"
    e1 = _make_dummy_draft_entry(m_id, s_id, "B01")
    e2 = _make_dummy_draft_entry(m_id, s_id, "B02")

    sig_1 = compute_project_overview_ledger_signature(s_id, (e1,))
    sig_1_2 = compute_project_overview_ledger_signature(s_id, (e1, e2))

    assert sig_1 != sig_1_2


def test_compute_ledger_signature_validation_rejections():
    """Helper từ chối fail-closed đối với input sai kiểu, foreign snapshot, duplicate batch."""
    s_id = "s_snap_01"
    e1 = _make_dummy_draft_entry("m_01", s_id, "B01")
    e_foreign = _make_dummy_draft_entry("m_01", "s_other_snapshot", "B02")

    # 1. Foreign snapshot_id
    with pytest.raises(ValueError, match="Foreign snapshot_id"):
        compute_project_overview_ledger_signature(s_id, (e1, e_foreign))

    # 2. Duplicate batch_id
    e1_dup = _make_dummy_draft_entry("m_01", s_id, "B01")
    with pytest.raises(ValueError, match="Duplicate batch_id"):
        compute_project_overview_ledger_signature(s_id, (e1, e1_dup))

    # 3. Invalid snapshot_id
    with pytest.raises(ValueError):
        compute_project_overview_ledger_signature("", (e1,))
    with pytest.raises(ValueError):
        compute_project_overview_ledger_signature(False, (e1,))

    # 4. Invalid entries sequence
    with pytest.raises(ValueError):
        compute_project_overview_ledger_signature(s_id, False)
    with pytest.raises(ValueError):
        compute_project_overview_ledger_signature(s_id, [e1, "not_an_entry"])


def test_compute_ledger_signature_batch_id_malformed_shapes_raise_value_error():
    """batch_id dạng list, None, rỗng, bool, dict đều ném ValueError có kiểm soát."""
    import dataclasses
    s_id = "s_snap_01"
    e1 = _make_dummy_draft_entry("m_01", s_id, "B01")

    for bad_id in ([], None, "", "   ", True, False, {}):
        bad_entry = dataclasses.replace(e1, batch_id=bad_id)
        with pytest.raises(ValueError):
            compute_project_overview_ledger_signature(s_id, (bad_entry,))


def test_compute_ledger_signature_segments_malformed_shapes_raise_value_error():
    """segments dạng None, bool, hoặc chứa item không hợp lệ đều ném ValueError."""
    import dataclasses
    s_id = "s_snap_01"
    e1 = _make_dummy_draft_entry("m_01", s_id, "B01")

    for bad_segs in (None, True, False, [123], ("not_a_cut",)):
        bad_entry = dataclasses.replace(e1, segments=bad_segs)
        with pytest.raises(ValueError):
            compute_project_overview_ledger_signature(s_id, (bad_entry,))


def test_compute_ledger_signature_duration_overflow_and_bool_raise_value_error():
    """total_selected_duration_sec tràn số 10**400, bool, NaN, Inf, hoặc âm đều ném ValueError."""
    import dataclasses
    s_id = "s_snap_01"
    e1 = _make_dummy_draft_entry("m_01", s_id, "B01")

    for bad_dur in (10**400, True, False, float("nan"), float("inf"), -1.0):
        bad_entry = dataclasses.replace(e1, total_selected_duration_sec=bad_dur)
        with pytest.raises(ValueError):
            compute_project_overview_ledger_signature(s_id, (bad_entry,))


def test_compute_ledger_signature_counts_bool_negative_raise_value_error():
    """total_cuts_count dạng bool hoặc số âm đều ném ValueError."""
    import dataclasses
    s_id = "s_snap_01"
    e1 = _make_dummy_draft_entry("m_01", s_id, "B01")

    for bad_cnt in (True, False, -1, "5"):
        bad_entry = dataclasses.replace(e1, total_cuts_count=bad_cnt)
        with pytest.raises(ValueError):
            compute_project_overview_ledger_signature(s_id, (bad_entry,))


def test_compute_ledger_signature_raw_hash_and_digest_shapes_raise_value_error():
    """raw_payload_sha256, draft_digest, binding_mode sai dạng đều ném ValueError."""
    import dataclasses
    s_id = "s_snap_01"
    e1 = _make_dummy_draft_entry("m_01", s_id, "B01")

    # Sai SHA length/hex
    with pytest.raises(ValueError):
        compute_project_overview_ledger_signature(s_id, (dataclasses.replace(e1, raw_payload_sha256="bad_sha"),))
    with pytest.raises(ValueError):
        compute_project_overview_ledger_signature(s_id, (dataclasses.replace(e1, draft_digest=123),))

    # Sai binding_mode
    with pytest.raises(ValueError):
        compute_project_overview_ledger_signature(s_id, (dataclasses.replace(e1, binding_mode="unsupported_mode"),))


# =============================================================================
# NHÓM 2: VALIDATE_PROJECT_OVERVIEW_RESULT_AGAINST_ARTIFACT
# =============================================================================

def test_validate_result_against_artifact_positive():
    """Xác thực thành công khi result envelope và nội dung khớp 100% với artifact."""
    overview = ProjectOverviewRenderResult(
        ok=True,
        error_code="",
        message="Thành công",
        rendered_prompt="Nội dung kịch bản tổng quan",
        rendered_bytes=len("Nội dung kịch bản tổng quan".encode("utf-8")),
        rendered_sha256=hashlib.sha256("Nội dung kịch bản tổng quan".encode("utf-8")).hexdigest(),
        formatter_policy=OVERVIEW_FORMATTER_POLICY,
        received_batches_count=2,
        total_batches_expected=3,
        missing_batch_ids=("B03",),
        all_batch_drafts_received=False,
        source_coverage_complete=False,
        required_bytes=len("Nội dung kịch bản tổng quan".encode("utf-8")),
    )
    artifact = PreparedProjectOverviewArtifact(
        request_id="req_001",
        generation=1,
        intent="copy_project_overview",
        snapshot_id="snap_001",
        manifest_id="man_001",
        project_name="ChunDVC_Project",
        ledger_signature="a" * 64,
        formatter_policy=OVERVIEW_FORMATTER_POLICY,
        max_prompt_bytes=32768,
        context_signature="sig_ctx_01",
        overview=overview,
    )
    result = {
        "request_id": "req_001",
        "generation": 1,
        "intent": "copy_project_overview",
        "snapshot_id": "snap_001",
        "manifest_id": "man_001",
        "project_name": "ChunDVC_Project",
        "ledger_signature": "a" * 64,
        "formatter_policy": OVERVIEW_FORMATTER_POLICY,
        "max_prompt_bytes": 32768,
        "rendered_prompt": "Nội dung kịch bản tổng quan",
        "rendered_bytes": len("Nội dung kịch bản tổng quan".encode("utf-8")),
        "rendered_sha256": hashlib.sha256("Nội dung kịch bản tổng quan".encode("utf-8")).hexdigest(),
        "received_batches_count": 2,
        "total_batches_expected": 3,
        "all_batch_drafts_received": False,
        "source_coverage_complete": False,
        "missing_batch_ids": ("B03",),
    }

    assert validate_project_overview_result_against_artifact(result, artifact) is True


def test_validate_result_against_artifact_tamper_detection():
    """Phát hiện prompt bị sửa đổi dù kẻ tấn công có tự tính lại SHA256 và giữ nguyên số byte."""
    overview = ProjectOverviewRenderResult(
        ok=True,
        error_code="",
        message="Thành công",
        rendered_prompt="Kịch bản gốc 12345",
        rendered_bytes=len("Kịch bản gốc 12345".encode("utf-8")),
        rendered_sha256=hashlib.sha256("Kịch bản gốc 12345".encode("utf-8")).hexdigest(),
        formatter_policy=OVERVIEW_FORMATTER_POLICY,
        received_batches_count=1,
        total_batches_expected=1,
        missing_batch_ids=(),
        all_batch_drafts_received=True,
        source_coverage_complete=True,
        required_bytes=len("Kịch bản gốc 12345".encode("utf-8")),
    )
    artifact = PreparedProjectOverviewArtifact(
        request_id="req_001",
        generation=1,
        intent="copy_project_overview",
        snapshot_id="snap_001",
        manifest_id="man_001",
        project_name="ChunDVC_Project",
        ledger_signature="a" * 64,
        formatter_policy=OVERVIEW_FORMATTER_POLICY,
        max_prompt_bytes=32768,
        context_signature="sig_ctx_01",
        overview=overview,
    )

    tampered_prompt = "Kịch bản sửa 12345"  # cùng độ dài byte
    tampered_sha = hashlib.sha256(tampered_prompt.encode("utf-8")).hexdigest()
    result = {
        "request_id": "req_001",
        "generation": 1,
        "intent": "copy_project_overview",
        "snapshot_id": "snap_001",
        "manifest_id": "man_001",
        "project_name": "ChunDVC_Project",
        "ledger_signature": "a" * 64,
        "formatter_policy": OVERVIEW_FORMATTER_POLICY,
        "max_prompt_bytes": 32768,
        "rendered_prompt": tampered_prompt,
        "rendered_bytes": len(tampered_prompt.encode("utf-8")),
        "rendered_sha256": tampered_sha,
        "received_batches_count": 1,
        "total_batches_expected": 1,
        "all_batch_drafts_received": True,
        "source_coverage_complete": True,
        "missing_batch_ids": (),
    }

    # Bị từ chối vì khác với artifact.overview.rendered_prompt
    assert validate_project_overview_result_against_artifact(result, artifact) is False


def test_validate_result_against_artifact_bool_and_envelope_rejections():
    """Từ chối bool generation, thiếu trường, hoặc envelope không khớp."""
    overview = ProjectOverviewRenderResult(
        ok=True,
        error_code="",
        message="",
        rendered_prompt="ABC",
        rendered_bytes=3,
        rendered_sha256=hashlib.sha256(b"ABC").hexdigest(),
        formatter_policy=OVERVIEW_FORMATTER_POLICY,
        received_batches_count=1,
        total_batches_expected=1,
        missing_batch_ids=(),
        all_batch_drafts_received=True,
        source_coverage_complete=True,
        required_bytes=3,
    )
    artifact = PreparedProjectOverviewArtifact(
        request_id="req_001",
        generation=1,
        intent="copy_project_overview",
        snapshot_id="snap_001",
        manifest_id="man_001",
        project_name="ChunDVC_Project",
        ledger_signature="a" * 64,
        formatter_policy=OVERVIEW_FORMATTER_POLICY,
        max_prompt_bytes=32768,
        context_signature="sig_ctx_01",
        overview=overview,
    )

    base_result = {
        "request_id": "req_001",
        "generation": 1,
        "intent": "copy_project_overview",
        "snapshot_id": "snap_001",
        "manifest_id": "man_001",
        "project_name": "ChunDVC_Project",
        "ledger_signature": "a" * 64,
        "formatter_policy": OVERVIEW_FORMATTER_POLICY,
        "max_prompt_bytes": 32768,
        "rendered_prompt": "ABC",
        "rendered_bytes": 3,
        "rendered_sha256": hashlib.sha256(b"ABC").hexdigest(),
        "received_batches_count": 1,
        "total_batches_expected": 1,
        "all_batch_drafts_received": True,
        "source_coverage_complete": True,
        "missing_batch_ids": (),
    }

    # 1. generation is bool True
    bad_res = dict(base_result, generation=True)
    assert validate_project_overview_result_against_artifact(bad_res, artifact) is False

    # 2. mismatch request_id
    bad_res = dict(base_result, request_id="req_other")
    assert validate_project_overview_result_against_artifact(bad_res, artifact) is False

    # 3. forged ledger_signature
    bad_res = dict(base_result, ledger_signature="b" * 64)
    assert validate_project_overview_result_against_artifact(bad_res, artifact) is False

    # 4. bool received_batches_count
    bad_res = dict(base_result, received_batches_count=True)
    assert validate_project_overview_result_against_artifact(bad_res, artifact) is False

    # 5. Non-dict result
    assert validate_project_overview_result_against_artifact("bad_result", artifact) is False

    # 6. missing max_prompt_bytes
    bad_res = dict(base_result)
    del bad_res["max_prompt_bytes"]
    assert validate_project_overview_result_against_artifact(bad_res, artifact) is False

    # 7. max_prompt_bytes is bool, None, or mismatch
    assert validate_project_overview_result_against_artifact(dict(base_result, max_prompt_bytes=True), artifact) is False
    assert validate_project_overview_result_against_artifact(dict(base_result, max_prompt_bytes=None), artifact) is False
    assert validate_project_overview_result_against_artifact(dict(base_result, max_prompt_bytes=1000), artifact) is False


# =============================================================================
# NHÓM 3: PROMPT_PREPARATION_REQUEST VALIDATION
# =============================================================================

def test_request_validation_for_copy_project_overview():
    """PromptPreparationRequest thẩm định chặt chẽ intent='copy_project_overview'."""
    # Positive
    req = PromptPreparationRequest(
        request_id="req_001",
        generation=1,
        intent="copy_project_overview",
        video_paths=("/path/to/video.mp4",),
        expected_snapshot_id="snap_01",
        expected_manifest_id="man_01",
        expected_project_name="ChunDVC",
        expected_ledger_signature="a" * 64,
        expected_ui_context_signature="ui_sig_01",
    )
    assert req.intent == "copy_project_overview"
    assert req.expected_ui_context_signature == "ui_sig_01"

    # request_id rỗng hoặc bool -> ValueError
    for bad_req_id in ("", "   ", True, False):
        with pytest.raises(ValueError, match="request_id"):
            PromptPreparationRequest(
                request_id=bad_req_id,
                generation=1,
                intent="copy_project_overview",
                video_paths=("/path/to/video.mp4",),
                expected_snapshot_id="snap_01",
                expected_manifest_id="man_01",
                expected_project_name="ChunDVC",
                expected_ledger_signature="a" * 64,
                expected_ui_context_signature="ui_sig_01",
            )

    # generation is bool -> ValueError
    with pytest.raises(ValueError, match="generation"):
        PromptPreparationRequest(
            request_id="req_001",
            generation=True,
            intent="copy_project_overview",
            video_paths=("/path/to/video.mp4",),
        )

    # max_prompt_bytes invalid -> ValueError
    with pytest.raises(ValueError, match="max_prompt_bytes"):
        PromptPreparationRequest(
            request_id="req_001",
            generation=1,
            intent="copy_project_overview",
            video_paths=("/path/to/video.mp4",),
            max_prompt_bytes=40000,
        )

    # invalid expected_ledger_signature at construction -> ValueError
    with pytest.raises(ValueError, match="expected_ledger_signature"):
        PromptPreparationRequest(
            request_id="req_001",
            generation=1,
            intent="copy_project_overview",
            video_paths=("/path/to/video.mp4",),
            expected_ledger_signature="bad_hex",
        )


# =============================================================================
# NHÓM 4: REAL QTHREAD WORKER EXECUTION
# =============================================================================

def _build_snapshot_fixture(tmp_path, max_b: int = 32768, ui_sig: Any = "ui_sig_01") -> Tuple[PreparedBatchSnapshot, str, str, str]:
    """Tạo snapshot phân lô chuẩn 3 lô + 1 unaccounted clip."""
    v1 = _create_dummy_video(str(tmp_path / "clip1.mp4"), size=1024)
    v2 = _create_dummy_video(str(tmp_path / "clip2.mp4"), size=2048)

    base1 = _make_dummy_baseline(v1)
    base2 = _make_dummy_baseline(v2)

    r1 = BatchSourceRange("C01", 0.0, 30.0, 0, 1, ("W01",))
    r2 = BatchSourceRange("C01", 30.0, 60.0, 0, 1, ("W02",))
    r3 = BatchSourceRange("C02", 0.0, 45.0, 0, 1, ("W03",))

    p1 = "Prompt for B01"
    p2 = "Prompt for B02"
    p3 = "Prompt for B03"

    b1 = PreparedBatchItem("B01", 0, 1, 30.0, p1, len(p1.encode("utf-8")), hashlib.sha256(p1.encode("utf-8")).hexdigest(), "sig1", ("C01",), 1, source_ranges=(r1,))
    b2 = PreparedBatchItem("B02", 1, 1, 30.0, p2, len(p2.encode("utf-8")), hashlib.sha256(p2.encode("utf-8")).hexdigest(), "sig2", ("C01",), 1, source_ranges=(r2,))
    b3 = PreparedBatchItem("B03", 2, 1, 45.0, p3, len(p3.encode("utf-8")), hashlib.sha256(p3.encode("utf-8")).hexdigest(), "sig3", ("C02",), 1, source_ranges=(r3,))

    u1 = UnaccountedClipEntry("C03", "DURATION_UNKNOWN", "Clip chưa xác định thời lượng")

    m_id = "man_overview_test"
    p_name = "ChunDVC_Overview_Project"
    ev_sig = "ev_sig_test"

    s_id = compute_snapshot_id(m_id, p_name, (base1, base2), (b1, b2, b3), ev_sig, max_b)

    snap = PreparedBatchSnapshot(
        manifest_id=m_id,
        project_name=p_name,
        snapshot_id=s_id,
        formatter_policy="source_only_v1",
        total_batches=3,
        planned_items_count=3,
        distinct_planned_clips_count=2,
        planned_clip_ids=("C01", "C02"),
        planned_clip_ids_count=2,
        unaccounted_clip_ids=("C03",),
        unaccounted_clips=(u1,),
        adapter_issues=(),
        max_prompt_bytes=max_b,
        batches=(b1, b2, b3),
        baseline_sources=(base1, base2),
        evidence_context_signature=ev_sig,
        ui_context_signature=ui_sig,
    )
    return snap, v1, v2, s_id


def test_worker_overview_partial_drafts_real_qthread(tmp_path, qapp):
    """Worker chạy QThread thật cho partial drafts (2/3 lô):
    - Render thành công.
    - Lưu PreparedProjectOverviewArtifact riêng.
    - validate_project_overview_result_against_artifact trả về True."""
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path)

    # Tạo context có 2 drafts (B01, B02), thiếu B03
    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges, duration_sec=30.0)
    e2 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B02", source_ranges=snap.batches[1].source_ranges, duration_sec=30.0)

    part_b1 = BatchPartitionRef("B01", 0, snap.batches[0].source_ranges)
    part_b2 = BatchPartitionRef("B02", 1, snap.batches[1].source_ranges)
    part_b3 = BatchPartitionRef("B03", 2, snap.batches[2].source_ranges)

    unacc1 = OverviewUnaccountedClip("C03", "DURATION_UNKNOWN", "Clip chưa xác định thời lượng")

    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(part_b1, part_b2, part_b3),
        received_drafts=(e1, e2),
        unaccounted_clips=(unacc1,),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
    )

    exp_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1, e2))

    req = PromptPreparationRequest(
        request_id="req_part_01",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_input,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig,
        expected_ui_context_signature=snap.ui_context_signature,
    )

    worker = PromptPreparationWorker(req)
    # Getter trước khi chạy phải là None
    assert worker.get_project_overview_artifact() is None

    ok, msg, res = _wait_for_worker(worker)
    print("\nDEBUG res:", msg, res)

    assert ok is True
    assert msg == "success"
    assert res["received_batches_count"] == 2
    assert res["total_batches_expected"] == 3
    assert res["missing_batch_ids"] == ("B03",)
    assert res["all_batch_drafts_received"] is False

    artifact = worker.get_project_overview_artifact()
    assert artifact is not None
    assert artifact.request_id == "req_part_01"
    assert validate_project_overview_result_against_artifact(res, artifact) is True


def test_worker_overview_full_drafts_with_unaccounted(tmp_path, qapp):
    """Worker chạy full 3/3 drafts + 1 unaccounted clip:
    - all_batch_drafts_received = True.
    - source_coverage_complete = False (vì có clip unaccounted).
    - Unaccounted clip không có file trên đĩa vẫn thành công (không bị stat)."""
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path)

    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges, duration_sec=30.0)
    e2 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B02", source_ranges=snap.batches[1].source_ranges, duration_sec=30.0)
    e3 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B03", source_ranges=snap.batches[2].source_ranges, duration_sec=45.0)

    part_b1 = BatchPartitionRef("B01", 0, snap.batches[0].source_ranges)
    part_b2 = BatchPartitionRef("B02", 1, snap.batches[1].source_ranges)
    part_b3 = BatchPartitionRef("B03", 2, snap.batches[2].source_ranges)

    unacc1 = OverviewUnaccountedClip("C03", "DURATION_UNKNOWN", "Clip chưa xác định thời lượng")

    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(part_b1, part_b2, part_b3),
        received_drafts=(e1, e2, e3),
        unaccounted_clips=(unacc1,),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
    )

    exp_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1, e2, e3))

    req = PromptPreparationRequest(
        request_id="req_full_01",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_input,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig,
        expected_ui_context_signature=snap.ui_context_signature,
    )

    worker = PromptPreparationWorker(req)
    ok, msg, res = _wait_for_worker(worker)
    print("\nDEBUG res:", msg, res)

    assert ok is True
    assert res["received_batches_count"] == 3
    assert res["all_batch_drafts_received"] is True
    assert res["source_coverage_complete"] is False

    artifact = worker.get_project_overview_artifact()
    assert artifact is not None
    assert validate_project_overview_result_against_artifact(res, artifact) is True


def test_worker_overview_zero_drafts_returns_no_data(tmp_path, qapp):
    """Không có draft nào trong context: worker phát no_data, artifact là None."""
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path)

    part_b1 = BatchPartitionRef("B01", 0, snap.batches[0].source_ranges)
    part_b2 = BatchPartitionRef("B02", 1, snap.batches[1].source_ranges)
    part_b3 = BatchPartitionRef("B03", 2, snap.batches[2].source_ranges)
    unacc1 = OverviewUnaccountedClip("C03", "DURATION_UNKNOWN", "Clip chưa xác định thời lượng")

    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(part_b1, part_b2, part_b3),
        received_drafts=(),
        unaccounted_clips=(unacc1,),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
    )
    exp_sig = compute_project_overview_ledger_signature(snap.snapshot_id, ())

    req = PromptPreparationRequest(
        request_id="req_zero_01",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_input,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig,
        expected_ui_context_signature=snap.ui_context_signature,
    )

    worker = PromptPreparationWorker(req)
    ok, msg, res = _wait_for_worker(worker)

    assert ok is False
    assert msg == "no_data"
    assert res.get("error_code") == ERR_OVERVIEW_NO_DRAFTS
    assert worker.get_project_overview_artifact() is None


def test_worker_overview_budget_overflow_returns_budget_error(tmp_path, qapp):
    """Trần byte bị vượt: worker phát budget_error kèm required_bytes, artifact là None."""
    # Tạo snapshot fixture với ngân sách 50 byte để khớp chính xác req và ctx budget
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path, max_b=50)
    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges, duration_sec=30.0)
    part_b1 = BatchPartitionRef("B01", 0, snap.batches[0].source_ranges)
    part_b2 = BatchPartitionRef("B02", 1, snap.batches[1].source_ranges)
    part_b3 = BatchPartitionRef("B03", 2, snap.batches[2].source_ranges)
    unacc1 = OverviewUnaccountedClip("C03", "DURATION_UNKNOWN", "Clip chưa xác định thời lượng")

    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(part_b1, part_b2, part_b3),
        received_drafts=(e1,),
        unaccounted_clips=(unacc1,),
        style_intent="travel_vlog",
        max_prompt_bytes=50,
    )
    exp_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1,))

    req = PromptPreparationRequest(
        request_id="req_overflow_01",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=50,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_input,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig,
        expected_ui_context_signature=snap.ui_context_signature,
    )

    worker = PromptPreparationWorker(req)
    ok, msg, res = _wait_for_worker(worker)

    assert ok is False
    assert msg == "budget_error"
    assert res.get("error_code") == ERR_OVERVIEW_BUDGET_OVERFLOW
    assert res.get("required_bytes", 0) > 50
    assert worker.get_project_overview_artifact() is None


def test_worker_overview_context_geometry_mismatch_rejected(tmp_path, qapp):
    """Context có geometry source_ranges bị lệch so với snapshot: bị từ chối fail-closed."""
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path)
    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", clip_id="C01")

    # Tạo batch partition ref có source_out_sec bị lệch (999.0 thay vì 30.0)
    corrupted_range = BatchSourceRange("C01", 0.0, 999.0, 0, 1, ("W01",))
    part_b1_corrupted = BatchPartitionRef("B01", 0, (corrupted_range,))

    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(part_b1_corrupted, BatchPartitionRef("B02", 1, snap.batches[1].source_ranges), BatchPartitionRef("B03", 2, snap.batches[2].source_ranges)),
        received_drafts=(e1,),
        unaccounted_clips=(OverviewUnaccountedClip("C03", "DURATION_UNKNOWN", "Clip chưa xác định thời lượng"),),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
    )
    exp_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1,))

    req = PromptPreparationRequest(
        request_id="req_geom_mismatch",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_input,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig,
        expected_ui_context_signature=snap.ui_context_signature,
    )

    worker = PromptPreparationWorker(req)
    ok, msg, res = _wait_for_worker(worker)

    assert ok is False
    assert msg == "invalid_snapshot"
    assert "source_ranges" in res.get("error", "")
    assert worker.get_project_overview_artifact() is None


def test_worker_overview_source_pre_and_post_recheck(tmp_path, qapp):
    """Kiểm tra nguồn baseline pre/post check:
    - Pre-check phát hiện file bị xóa -> source_missing.
    - Post-check phát hiện file bị sửa trong lúc render -> source_changed."""
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path)
    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01")
    part_b1 = BatchPartitionRef("B01", 0, snap.batches[0].source_ranges)
    part_b2 = BatchPartitionRef("B02", 1, snap.batches[1].source_ranges)
    part_b3 = BatchPartitionRef("B03", 2, snap.batches[2].source_ranges)
    unacc = OverviewUnaccountedClip("C03", "DURATION_UNKNOWN", "Clip chưa xác định thời lượng")

    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(part_b1, part_b2, part_b3),
        received_drafts=(e1,),
        unaccounted_clips=(unacc,),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
    )
    exp_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1,))

    # Ca A: Xóa file v2 trước khi chạy -> Pre-check fail
    os.remove(v2)
    req_pre = PromptPreparationRequest(
        request_id="req_pre_missing",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_input,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig,
        expected_ui_context_signature=snap.ui_context_signature,
    )
    worker_pre = PromptPreparationWorker(req_pre)
    ok_pre, msg_pre, _ = _wait_for_worker(worker_pre)
    assert ok_pre is False
    assert msg_pre == "source_missing"

    # Khôi phục file v2 nhưng sửa size
    _create_dummy_video(v2, size=9999)  # kích thước khác snapshot
    req_pre_changed = PromptPreparationRequest(
        request_id="req_pre_changed",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_input,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig,
        expected_ui_context_signature=snap.ui_context_signature,
    )
    worker_changed = PromptPreparationWorker(req_pre_changed)
    ok_c, msg_c, _ = _wait_for_worker(worker_changed)
    assert ok_c is False
    assert msg_c == "source_changed"


def test_worker_overview_cancel_lifecycle(tmp_path, qapp):
    """Hủy worker cooperative trước khi chạy: phát terminal cancelled và 0 artifact."""
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path)
    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01")
    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap.batches[1].source_ranges),
            BatchPartitionRef("B03", 2, snap.batches[2].source_ranges),
        ),
        received_drafts=(e1,),
        unaccounted_clips=(OverviewUnaccountedClip("C03", "DURATION_UNKNOWN", "Clip chưa xác định thời lượng"),),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
    )
    exp_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1,))

    req = PromptPreparationRequest(
        request_id="req_cancel_01",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_input,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig,
        expected_ui_context_signature=snap.ui_context_signature,
    )

    worker = PromptPreparationWorker(req)
    worker.cancel()  # cancel trước khi start

    ok, msg, _ = _wait_for_worker(worker)
    assert ok is False
    assert msg == "cancelled"
    assert worker.get_project_overview_artifact() is None


def _make_dummy_unaccounted_clip(snap: PreparedBatchSnapshot) -> OverviewUnaccountedClip:
    u0 = snap.unaccounted_clips[0]
    return OverviewUnaccountedClip(u0.clip_id, u0.reason, u0.message)


# =============================================================================
# NHÓM 5: GROUP A TESTS - REQUIRED BINDINGS & REQUEST GATES
# =============================================================================

def test_overview_request_missing_expected_ui_signature_rejected(tmp_path, qapp):
    """expected_ui_context_signature bị thiếu (None hoặc rỗng) -> fail-closed invalid_snapshot, 0 stat, 0 artifact."""
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path)
    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges)
    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap.batches[1].source_ranges),
            BatchPartitionRef("B03", 2, snap.batches[2].source_ranges),
        ),
        received_drafts=(e1,),
        unaccounted_clips=(_make_dummy_unaccounted_clip(snap),),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
    )
    exp_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1,))

    for bad_sig in (None, "", "   "):
        try:
            req = PromptPreparationRequest(
                request_id="req_missing_ui_sig",
                generation=1,
                intent="copy_project_overview",
                video_paths=(v1, v2),
                style_intent="travel_vlog",
                max_prompt_bytes=snap.max_prompt_bytes,
                prepared_batch_snapshot=snap,
                project_overview_context=ctx_input,
                expected_snapshot_id=snap.snapshot_id,
                expected_manifest_id=snap.manifest_id,
                expected_project_name=snap.project_name,
                expected_ledger_signature=exp_sig,
                expected_ui_context_signature=bad_sig,
            )
            worker = PromptPreparationWorker(req)
            ok, msg, res = _wait_for_worker(worker)

            assert ok is False
            assert msg == "invalid_snapshot"
            assert worker.get_project_overview_artifact() is None
        except ValueError:
            pass  # Constructor rejected thành công


def test_overview_snapshot_missing_ui_signature_rejected(tmp_path, qapp):
    """Snapshot mất ui_context_signature (None/rỗng) dù request có -> fail-closed, zero fallback sang evidence signature."""
    # Build snapshot fixture với ui_sig=None
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path, ui_sig=None)
    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges)
    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap.batches[1].source_ranges),
            BatchPartitionRef("B03", 2, snap.batches[2].source_ranges),
        ),
        received_drafts=(e1,),
        unaccounted_clips=(_make_dummy_unaccounted_clip(snap),),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
    )
    exp_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1,))

    req = PromptPreparationRequest(
        request_id="req_snap_missing_ui_sig",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_input,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig,
        expected_ui_context_signature="ui_sig_01",
    )
    worker = PromptPreparationWorker(req)
    ok, msg, res = _wait_for_worker(worker)

    assert ok is False
    assert msg == "invalid_snapshot"
    assert worker.get_project_overview_artifact() is None


def test_overview_mismatched_ui_signature_rejected(tmp_path, qapp):
    """expected_ui_context_signature khác với snapshot.ui_context_signature -> fail-closed invalid_snapshot."""
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path, ui_sig="ui_sig_A")
    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges)
    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap.batches[1].source_ranges),
            BatchPartitionRef("B03", 2, snap.batches[2].source_ranges),
        ),
        received_drafts=(e1,),
        unaccounted_clips=(_make_dummy_unaccounted_clip(snap),),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
    )
    exp_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1,))

    req = PromptPreparationRequest(
        request_id="req_sig_mismatch",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_input,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig,
        expected_ui_context_signature="ui_sig_DIFFERENT",
    )
    worker = PromptPreparationWorker(req)
    ok, msg, res = _wait_for_worker(worker)

    assert ok is False
    assert msg == "invalid_snapshot"
    assert worker.get_project_overview_artifact() is None


def test_overview_blank_request_id_rejected(tmp_path, qapp):
    """Request ID rỗng hoặc bool -> bị từ chối ở constructor hoặc emit invalid_snapshot."""
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path)
    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges)
    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap.batches[1].source_ranges),
            BatchPartitionRef("B03", 2, snap.batches[2].source_ranges),
        ),
        received_drafts=(e1,),
        unaccounted_clips=(_make_dummy_unaccounted_clip(snap),),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
    )
    exp_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1,))

    # Constructor rejection
    for bad_id in ("", "   ", True, False):
        try:
            req = PromptPreparationRequest(
                request_id=bad_id,
                generation=1,
                intent="copy_project_overview",
                video_paths=(v1, v2),
                style_intent="travel_vlog",
                max_prompt_bytes=snap.max_prompt_bytes,
                prepared_batch_snapshot=snap,
                project_overview_context=ctx_input,
                expected_snapshot_id=snap.snapshot_id,
                expected_manifest_id=snap.manifest_id,
                expected_project_name=snap.project_name,
                expected_ledger_signature=exp_sig,
                expected_ui_context_signature=snap.ui_context_signature,
            )
            # Nếu constructor chưa reject, worker phải reject fail-closed
            worker = PromptPreparationWorker(req)
            ok, msg, res = _wait_for_worker(worker)
            assert ok is False
            assert msg == "invalid_snapshot"
            assert worker.get_project_overview_artifact() is None
        except ValueError:
            pass  # Constructor rejected thành công


def test_overview_style_empty_vs_nonempty_rejected(tmp_path, qapp):
    """Style mismatch: req.style_intent == '' trong khi context là 'travel_vlog' -> fail-closed."""
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path)
    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges)
    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap.batches[1].source_ranges),
            BatchPartitionRef("B03", 2, snap.batches[2].source_ranges),
        ),
        received_drafts=(e1,),
        unaccounted_clips=(_make_dummy_unaccounted_clip(snap),),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
    )
    exp_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1,))

    # req style rỗng trong khi context style travel_vlog
    req = PromptPreparationRequest(
        request_id="req_style_empty",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="",  # rỗng
        max_prompt_bytes=snap.max_prompt_bytes,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_input,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig,
        expected_ui_context_signature=snap.ui_context_signature,
    )
    worker = PromptPreparationWorker(req)
    ok, msg, res = _wait_for_worker(worker)

    assert ok is False
    assert msg == "invalid_snapshot"
    assert worker.get_project_overview_artifact() is None

    # Positive control: cả hai cùng rỗng -> thành công
    import dataclasses
    ctx_empty_style = dataclasses.replace(ctx_input, style_intent="")
    req_both_empty = dataclasses.replace(req, project_overview_context=ctx_empty_style)
    worker_ok = PromptPreparationWorker(req_both_empty)
    ok_b, msg_b, _ = _wait_for_worker(worker_ok)
    assert ok_b is True
    assert msg_b == "success"


def test_overview_three_way_budget_mismatch_rejected(tmp_path, qapp):
    """Budget ba chiều mismatch: snapshot.max_prompt_bytes != req.max_prompt_bytes hoặc != context.max_prompt_bytes."""
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path, max_b=32768)
    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges)
    part_b1 = BatchPartitionRef("B01", 0, snap.batches[0].source_ranges)
    part_b2 = BatchPartitionRef("B02", 1, snap.batches[1].source_ranges)
    part_b3 = BatchPartitionRef("B03", 2, snap.batches[2].source_ranges)
    unacc = _make_dummy_unaccounted_clip(snap)

    # Ca 1: req=32767, ctx=32767 trong khi snapshot=32768
    ctx_32767 = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(part_b1, part_b2, part_b3),
        received_drafts=(e1,),
        unaccounted_clips=(unacc,),
        style_intent="travel_vlog",
        max_prompt_bytes=32767,
    )
    exp_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1,))

    req_32767 = PromptPreparationRequest(
        request_id="req_budget_mismatch_1",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=32767,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_32767,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig,
        expected_ui_context_signature=snap.ui_context_signature,
    )
    worker_1 = PromptPreparationWorker(req_32767)
    ok_1, msg_1, _ = _wait_for_worker(worker_1)
    assert ok_1 is False
    assert msg_1 == "invalid_snapshot"
    assert worker_1.get_project_overview_artifact() is None

    # Ca 2: snap=32768, req=32768, nhưng ctx=20000
    import dataclasses
    ctx_20000 = dataclasses.replace(ctx_32767, max_prompt_bytes=20000)
    req_ctx_mismatch = dataclasses.replace(req_32767, max_prompt_bytes=32768, project_overview_context=ctx_20000)
    worker_2 = PromptPreparationWorker(req_ctx_mismatch)
    ok_2, msg_2, _ = _wait_for_worker(worker_2)
    assert ok_2 is False
    assert msg_2 == "invalid_snapshot"
    assert worker_2.get_project_overview_artifact() is None


# =============================================================================
# NHÓM 6: GROUP C TESTS - OWNED IMMUTABLE CONTEXT PROOF & LIFECYCLE
# =============================================================================

def test_overview_caller_mutable_ui_signature_deep_frozen_and_owned(tmp_path, qapp):
    """Caller truyền nested list/dict vào expected_ui_context_signature; caller mutate sau đó
    không làm đổi request.expected_ui_context_signature và artifact.context_signature."""
    mutable_sig = ["level1", [10, 20], {"sub_key": ["sub_val1", "sub_val2"]}]

    import copy
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path, ui_sig=copy.deepcopy(mutable_sig))
    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges)
    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap.batches[1].source_ranges),
            BatchPartitionRef("B03", 2, snap.batches[2].source_ranges),
        ),
        received_drafts=(e1,),
        unaccounted_clips=(_make_dummy_unaccounted_clip(snap),),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
    )
    exp_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1,))

    req = PromptPreparationRequest(
        request_id="req_mutable_sig",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_input,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig,
        expected_ui_context_signature=mutable_sig,
    )

    # Caller mutate list ngay sau khi khởi tạo request
    mutable_sig[1].append(999)
    mutable_sig[2]["sub_key"].append("injected_val")

    worker = PromptPreparationWorker(req)
    ok, msg, res = _wait_for_worker(worker)

    assert ok is True
    assert msg == "success"

    artifact = worker.get_project_overview_artifact()
    assert artifact is not None

    # Caller mutate tiếp sau khi worker đã hoàn tất
    mutable_sig.append("evil_mutation")

    # Kiểm tra artifact.context_signature và req.expected_ui_context_signature là owned immutable tuple
    assert isinstance(artifact.context_signature, tuple)
    assert "999" not in str(artifact.context_signature)
    assert "injected_val" not in str(artifact.context_signature)
    assert "evil_mutation" not in str(artifact.context_signature)

    assert isinstance(req.expected_ui_context_signature, tuple)
    assert "999" not in str(req.expected_ui_context_signature)
    assert "injected_val" not in str(req.expected_ui_context_signature)
    assert "evil_mutation" not in str(req.expected_ui_context_signature)

    assert artifact.context_signature == req.expected_ui_context_signature


def test_worker_overview_post_render_mutation_triggers_source_changed(tmp_path, qapp):
    """Pre-check pass; formatter hook đổi file size/identity trước return -> post-check phát hiện
    source_changed, exactly 1 terminal failure, artifact None (kèm spy xác nhận hook đã chạy)."""
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path)
    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges)
    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap.batches[1].source_ranges),
            BatchPartitionRef("B03", 2, snap.batches[2].source_ranges),
        ),
        received_drafts=(e1,),
        unaccounted_clips=(_make_dummy_unaccounted_clip(snap),),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
    )
    exp_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1,))

    req = PromptPreparationRequest(
        request_id="req_post_mutation",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_input,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig,
        expected_ui_context_signature=snap.ui_context_signature,
    )

    from src.core.project_overview_contracts import render_project_overview_prompt as real_render

    spy = {"called": False}

    def hook_render(ctx):
        spy["called"] = True
        # Mutate file v1 trên đĩa: nối thêm bytes
        with open(v1, "ab") as f:
            f.write(b"CORRUPT_BYTES_POST_RENDER")
        return real_render(ctx)

    with patch("src.ui.director.workers.prompt_preparation_worker.render_project_overview_prompt", side_effect=hook_render):
        worker = PromptPreparationWorker(req)
        ok, msg, res = _wait_for_worker(worker)

    assert spy["called"] is True, "Positive control: formatter phải thực sự đã được gọi!"
    assert ok is False
    assert msg == "source_changed"
    assert worker.get_project_overview_artifact() is None


def test_worker_overview_cancel_inflight_during_render(tmp_path, qapp):
    """Cancel gọi trong lúc render prompt: worker chuyển sang terminal cancelled sạch, getter None, physical finished."""
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path)
    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges)
    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap.batches[1].source_ranges),
            BatchPartitionRef("B03", 2, snap.batches[2].source_ranges),
        ),
        received_drafts=(e1,),
        unaccounted_clips=(_make_dummy_unaccounted_clip(snap),),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
    )
    exp_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1,))

    req = PromptPreparationRequest(
        request_id="req_inflight_cancel",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_input,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig,
        expected_ui_context_signature=snap.ui_context_signature,
    )

    from src.core.project_overview_contracts import render_project_overview_prompt as real_render

    worker = PromptPreparationWorker(req)

    def hook_render_cancel(ctx):
        worker.cancel()  # cancel in-flight
        return real_render(ctx)

    with patch("src.ui.director.workers.prompt_preparation_worker.render_project_overview_prompt", side_effect=hook_render_cancel):
        ok, msg, res = _wait_for_worker(worker)

    assert ok is False
    assert msg == "cancelled"
    assert worker.get_project_overview_artifact() is None
    assert worker.isFinished() is True


def test_worker_overview_exception_in_render_handled_safely(tmp_path, qapp):
    """Exception nảy sinh trong formatter được bắt an toàn, phát đúng 1 terminal failure, không leak QThread, artifact là None."""
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path)
    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges)
    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap.batches[1].source_ranges),
            BatchPartitionRef("B03", 2, snap.batches[2].source_ranges),
        ),
        received_drafts=(e1,),
        unaccounted_clips=(_make_dummy_unaccounted_clip(snap),),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
    )
    exp_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1,))

    req = PromptPreparationRequest(
        request_id="req_injected_exc",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_input,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig,
        expected_ui_context_signature=snap.ui_context_signature,
    )

    worker = PromptPreparationWorker(req)

    def hook_render_raise(ctx):
        raise RuntimeError("Simulated crash in formatter")

    with patch("src.ui.director.workers.prompt_preparation_worker.render_project_overview_prompt", side_effect=hook_render_raise):
        ok, msg, res = _wait_for_worker(worker)

    assert ok is False
    assert worker.get_project_overview_artifact() is None
    assert worker.isFinished() is True


def test_freeze_context_signature_producer_review_page_actual_signature():
    """G06-06A4c2b-I / Section A:
    Chữ ký thực tế sinh ra từ ReviewPage._extract_context_signature phải được _freeze_context_signature
    và PromptPreparationRequest chấp nhận trong cả 2 trường hợp:
    1. Visual disabled + vision_model=''
    2. Visual enabled + vision_model thật dạng text metadata
    Bảo toàn cấu trúc tuple16 và không ném ValueError vì nested empty string hoặc empty paths tuple.
    """
    from src.ui.director.pages.review_page import ReviewPage
    from src.ui.director.workers.prompt_preparation_worker import _freeze_context_signature

    # Case 1: Visual disabled, vision_model="", empty paths
    ctx1 = {
        "video_paths": [],
        "whisper_model": "small",
        "whisper_language": "vi",
        "enable_visual_describe": False,
        "vision_model": "",
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
        "manifest_id": "",
    }
    sig1 = ReviewPage._extract_context_signature(ctx1)
    assert len(sig1) == 16
    assert sig1[0] == ()
    assert sig1[4] == ""

    frozen1 = _freeze_context_signature(sig1)
    assert isinstance(frozen1, tuple)
    assert len(frozen1) == 16
    assert frozen1[0] == ()
    assert frozen1[4] == ""

    # Thử qua PromptPreparationRequest
    req1 = PromptPreparationRequest(
        request_id="req_sig_real_prod1",
        generation=1,
        intent="copy_project_overview",
        video_paths=(),
        expected_ui_context_signature=sig1,
    )
    assert req1.expected_ui_context_signature == frozen1

    # Case 2: Visual enabled, model metadata thật
    ctx2 = dict(ctx1)
    ctx2["enable_visual_describe"] = True
    ctx2["vision_model"] = "internvl2_5_1b"
    ctx2["manifest_id"] = "man_abc123"

    sig2 = ReviewPage._extract_context_signature(ctx2)
    assert sig2[4] == "internvl2_5_1b"
    assert sig2[15] == "man_abc123"

    frozen2 = _freeze_context_signature(sig2)
    assert frozen2[4] == "internvl2_5_1b"
    assert frozen2[15] == "man_abc123"
    assert frozen1 != frozen2


def test_freeze_context_signature_root_rejections_and_leaf_allowance():
    """G06-06A4c2b-I / Section A:
    Root proof required: Root chỉ nhận nonblank str hoặc nonempty tuple/list/mapping hợp lệ.
    Root None, rỗng, số, bool, bytes -> fail-closed (ValueError).
    Nested leaf cho phép empty str '', empty sequence (), None, số, bool.
    """
    from src.ui.director.workers.prompt_preparation_worker import _freeze_context_signature

    # Root rejections
    for invalid_root in [
        None,
        "",
        "   ",
        True,
        False,
        123,
        45.6,
        b"raw_bytes",
        (),
        [],
        {},
    ]:
        with pytest.raises(ValueError):
            _freeze_context_signature(invalid_root)

    # Valid root str
    assert _freeze_context_signature("valid_sig_str") == "valid_sig_str"

    # Valid root containers with nested empty leaves and primitives
    nested_structure = (
        "",  # nested empty str is allowed
        (),  # nested empty tuple is allowed
        [],  # nested empty list becomes empty tuple
        None,  # nested None is allowed
        True,  # nested bool is allowed
        False,  # nested bool is allowed
        100,  # nested int is allowed
        3.14,  # nested finite float is allowed
        b"leaf_bytes",  # nested bytes is allowed
        {"key1": "", "key2": ()},  # nested mapping with empty leaves
    )
    frozen = _freeze_context_signature(nested_structure)
    assert isinstance(frozen, tuple)
    assert frozen[0] == ""
    assert frozen[1] == ()
    assert frozen[2] == ()
    assert frozen[3] is None
    assert frozen[4] is True
    assert frozen[5] is False
    assert frozen[6] == 100
    assert frozen[7] == 3.14
    assert frozen[8] == b"leaf_bytes"
    assert frozen[9] == (("key1", ""), ("key2", ()))


def test_freeze_context_signature_cycle_protection():
    """G06-06A4c2b-I / Section A:
    Tham chiếu vòng (cycle) phải bị phát hiện và ném controlled ValueError (không RecursionError).
    """
    from src.ui.director.workers.prompt_preparation_worker import _freeze_context_signature

    # Cyclic list
    cyclic_list = [1, 2]
    cyclic_list.append(cyclic_list)
    with pytest.raises(ValueError, match="cycle|vòng"):
        _freeze_context_signature(cyclic_list)

    # Cyclic dict
    cyclic_dict = {"a": 1}
    cyclic_dict["self"] = cyclic_dict
    with pytest.raises(ValueError, match="cycle|vòng"):
        _freeze_context_signature(cyclic_dict)


def test_freeze_context_signature_dict_mixed_keys_rejected():
    """G06-06A4c2b-I / Section A:
    Dict có mixed key types (không so sánh được hoặc gây mơ hồ) phải bị từ chối bằng ValueError,
    không dùng repr-sort để ép hợp lệ dữ liệu mơ hồ.
    """
    from src.ui.director.workers.prompt_preparation_worker import _freeze_context_signature

    mixed_dict = {1: "one", "two": 2}
    with pytest.raises(ValueError, match="không được lẫn lộn|hỗ trợ"):
        _freeze_context_signature({"root": mixed_dict})



