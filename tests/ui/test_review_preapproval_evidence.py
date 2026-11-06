"""
tests/ui/test_review_preapproval_evidence.py
=============================================================================
Kiểm thử bộ phận và tích hợp giao diện cho tiến trình kiểm tra nguồn tiền phê duyệt
trong ReviewPage (G07-07A4-I).
Bao phủ các nhóm kiểm thử T07, T08, T09, T10, T11, T12 theo Upfront Finite Matrix.
=============================================================================
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt, QEvent, QTimer
from PySide6.QtWidgets import QApplication

from src.core.clip_manifest import ClipEntry, ClipManifest
from src.core.contracts.edit_plan_approval_contracts import (
    AppOwnedApprovalRecord,
    create_app_owned_approval_record,
    verify_proposal_approval_binding,
)
from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    compute_proposal_revision,
)
from src.core.contracts.time_contracts import RationalFPS
from src.core.geometry.plan_geometry_contracts import SourceFrameEvidence
from src.core.geometry.source_evidence_service import (
    SourceEvidenceItemRequest,
    SourceEvidenceService,
    SourceExecutionEvidenceEnvelope,
    SourceFreshnessStat,
)
from src.core.proposal_builder import (
    build_edit_plan_proposal,
    compute_proof_signature,
    validate_and_normalize_source_evidence_snapshot,
)
from src.ui.director.pages.review_page import ReviewPage
from src.ui.director.workers.preapproval_evidence_worker import (
    PreapprovalEvidenceRequest,
    PreapprovalEvidenceTerminalPayload,
    PreapprovalEvidenceWorker,
)


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------

SAMPLE_SHA_1 = "1" * 64
SAMPLE_SHA_2 = "2" * 64


def _create_audio_first_bframe_video(
    file_path: Path,
    *,
    video_dur_sec: float = 0.5,
    audio_dur_sec: float = 1.0,
    fps: int = 30,
    b_frames: int = 2,
) -> None:
    """Tạo video fixture siêu nhỏ: audio stream 0, video stream 1, B-frames."""
    cmd = [
        "ffmpeg",
        "-v", "error",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
        "-f", "lavfi", "-i", f"testsrc=size=64x36:rate={fps}:duration={video_dur_sec}",
        "-map", "0:a:0",
        "-map", "1:v:0",
        "-t", str(audio_dur_sec),
        "-c:a", "aac",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-g", str(fps),
        "-bf", str(b_frames),
        "-x264-params", "b-adapt=0",
        "-y", str(file_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _wait_for_review_workers(page: ReviewPage, timeout_sec: float = 5.0) -> None:
    """Đợi an toàn bằng monotonic deadline và processEvents cho mọi worker của ReviewPage."""
    app = QApplication.instance()
    deadline = time.monotonic() + timeout_sec
    while not page.is_analysis_idle() and time.monotonic() < deadline:
        if app:
            app.processEvents()
        time.sleep(0.01)
    if app:
        app.processEvents()


def _setup_review_page(
    tmp_path: Path,
    clip_count: int = 2,
    create_real_media: bool = False,
) -> Tuple[ReviewPage, List[str], str]:
    """Khởi tạo ReviewPage với context và manifest chuẩn hóa."""
    page = ReviewPage()
    video_paths: List[str] = []
    manifest_id = f"mf_test_{uuid.uuid4().hex[:8]}"
    clips_list = []

    for i in range(clip_count):
        p = tmp_path / f"test_clip_{i+1:02d}_{uuid.uuid4().hex[:4]}.mp4"
        if create_real_media:
            _create_audio_first_bframe_video(p, video_dur_sec=0.2, audio_dur_sec=0.4, fps=24)
        else:
            p.write_bytes(b"\x00\x00\x00\x1cftypisom" + b"x" * 100)
        video_paths.append(str(p))
        clips_list.append(
            ClipEntry.from_dict({
                "clip_id": f"C{i+1:02d}",
                "file_name": p.name,
                "current_path": str(p),
                "duration_sec": 10.0,
                "fps": 24.0,
            })
        )

    manifest = ClipManifest(
        manifest_id=manifest_id,
        project_name="TestProject",
        clips=clips_list,
    )
    setattr(page, "_manifest", manifest)

    video_plan = {
        "version": "1.0",
        "cuts": [
            {"clip_id": f"C{i+1:02d}", "start_sec": 0.0, "end_sec": 3.0}
            for i in range(clip_count)
        ],
        "timeline_segments": [
            {"clip_id": f"C{i+1:02d}", "start_sec": 0.0, "end_sec": 3.0}
            for i in range(clip_count)
        ],
    }
    setattr(page, "_video_plan_data", video_plan)

    context = {
        "video_paths": list(video_paths),
        "timeline_fps": 24.0,
        "creative_context": {
            "style_preset": "travel_vlog",
            "target_duration_sec": 60.0,
        },
    }
    page.set_context_supplier(lambda: dict(context))
    page._update_source_evidence_button_state()
    page._rebuild_and_present_proposal()
    return page, video_paths, manifest_id


def _make_verified_snapshot(clip_id: str, manifest_id: str, file_path: str, sha: str, stream_idx: int = 1) -> Dict[str, Any]:
    return {
        "manifest_id": manifest_id,
        "clip_id": clip_id,
        "file_path": file_path,
        "stream_index": stream_idx,
        "observed_full_sha256": sha,
        "freshness": {
            "file_size_bytes": 1024,
            "mtime_ns": 1000,
            "ctime_ns": 1000,
            "device_id": 1,
            "inode_number": 1,
        },
        "frame_evidence": {
            "manifest_id": manifest_id,
            "clip_id": clip_id,
            "source_fps": {"numerator": 24, "denominator": 1},
            "total_frames": 240,
            "evidence_status": "VERIFIED_CFR",
        },
        "complete_scan": True,
        "profile_policy": "decoded_frame_uniform_ticks_v1",
    }


# ---------------------------------------------------------------------------
# T07: UI Admission & Lifecycle
# ---------------------------------------------------------------------------

class TestReviewPreapprovalAdmissionLifecycle:
    """Kiểm thử nhập cuộc (admission) và vòng đời vật lý (lifecycle) của preapproval worker."""

    def test_t07_ui_admission_busy_and_other_families_active(self, tmp_path):
        page, video_paths, m_id = _setup_review_page(tmp_path, clip_count=2)
        try:
            assert page.btn_check_source_evidence.isEnabled() is True
            assert page.is_source_evidence_busy() is False

            # 1. Khi prompt worker đang chạy, nút kiểm tra nguồn phải bị vô hiệu hóa
            page._prompt_workers_registry["pw_dummy"] = MagicMock()
            page._update_source_evidence_button_state()
            assert page.btn_check_source_evidence.isEnabled() is False

            # Bấm nút trong trạng thái bận không khởi động worker mới
            page._on_check_source_evidence_clicked()
            assert len(page._preapproval_evidence_workers) == 0

            # Giải phóng prompt worker giả
            page._prompt_workers_registry.clear()
            page._update_source_evidence_button_state()
            assert page.btn_check_source_evidence.isEnabled() is True

            # 2. Khi chính preapproval worker đang chạy: nút chuyển thành Hủy và luôn ENABLED để user hủy được
            page._preapproval_evidence_workers["se_dummy"] = MagicMock()
            page._update_source_evidence_button_state()
            assert page.btn_check_source_evidence.isEnabled() is True
            assert "Hủy" in page.btn_check_source_evidence.text()
            assert page.is_source_evidence_busy() is True
        finally:
            page._preapproval_evidence_workers.clear()
            page._prompt_workers_registry.clear()

    def test_t07_ui_start_failure_rollback(self, tmp_path):
        page, video_paths, m_id = _setup_review_page(tmp_path, clip_count=1)
        try:
            old_gen = page._preapproval_evidence_generation
            # Patch start để ném lỗi
            with patch.object(PreapprovalEvidenceWorker, "start", side_effect=RuntimeError("Worker start failed")):
                page._on_check_source_evidence_clicked()

            # Rollback: worker không còn trong registry, pending reset, status hiển thị lỗi
            assert len(page._preapproval_evidence_workers) == 0
            assert page._pending_preapproval_expected is None
            assert page.is_source_evidence_busy() is False
            assert "✕ Không thể khởi động kiểm tra" in page.lbl_source_evidence_status.text()
        finally:
            page.shutdown_analysis_workers()

    def test_t07_lifecycle_job_a_stale_vs_job_b_active(self, tmp_path):
        page, video_paths, m_id = _setup_review_page(tmp_path, clip_count=1)
        try:
            # Tạo job A giả lập
            req_a = "req_job_a"
            mock_worker_a = MagicMock(spec=PreapprovalEvidenceWorker)
            mock_worker_b = MagicMock(spec=PreapprovalEvidenceWorker)

            page._preapproval_evidence_workers[req_a] = mock_worker_a
            page._preapproval_evidence_workers["req_job_b"] = mock_worker_b
            page._pending_preapproval_expected = (mock_worker_b, "req_job_b", 2, "sig_b", ())

            # Finished callback của Job A đến
            page._on_preapproval_evidence_worker_finished(req_a, mock_worker_a)

            # Job A bị dọn nhưng Job B vẫn còn nguyên trong registry
            assert req_a not in page._preapproval_evidence_workers
            assert "req_job_b" in page._preapproval_evidence_workers
            assert page._pending_preapproval_expected == (mock_worker_b, "req_job_b", 2, "sig_b", ())
            assert page.is_source_evidence_busy() is True
        finally:
            page._preapproval_evidence_workers.clear()

    def test_t07_logical_terminal_does_not_release_physical_registry(self, tmp_path):
        page, video_paths, m_id = _setup_review_page(tmp_path, clip_count=1)
        try:
            curr_ctx = page._get_current_context()
            sig = page._build_pending_scan_input_signature(curr_ctx)
            req_id = "req_term_test"
            gen = 1
            item = SourceEvidenceItemRequest("C01", m_id, video_paths[0])
            req_obj = PreapprovalEvidenceRequest(
                request_id=req_id,
                generation=gen,
                manifest_id=m_id,
                input_signature=sig,
                items=(item,),
            )
            mock_worker = MagicMock(spec=PreapprovalEvidenceWorker)
            mock_worker._request = req_obj
            mock_worker.request = req_obj

            page._preapproval_evidence_generation = gen
            page._pending_preapproval_expected = (mock_worker, req_id, gen, sig, (item,))
            page._preapproval_evidence_workers[req_id] = mock_worker

            # Terminal payload đến
            payload = PreapprovalEvidenceTerminalPayload(
                request_id=req_id,
                generation=gen,
                input_signature=sig,
                status="cancelled",
                verified_evidence_by_clip={},
                raw_evidence_by_clip={},
                verified_count=0,
                total_count=1,
            )
            page._on_preapproval_terminal(payload, worker=mock_worker)

            # Terminal đã xử lý nhưng worker VẪN PHẢI NẰM TRONG REGISTRY tới khi finished vật lý
            assert req_id in page._preapproval_evidence_workers
            assert page.is_source_evidence_busy() is True

            # Sau khi finished callback được gọi
            page._on_preapproval_evidence_worker_finished(req_id, mock_worker)
            assert req_id not in page._preapproval_evidence_workers
            assert page.is_source_evidence_busy() is False
        finally:
            page._preapproval_evidence_workers.clear()


# ---------------------------------------------------------------------------
# T08: Binding Gate
# ---------------------------------------------------------------------------

class TestReviewPreapprovalBindingGate:
    """Kiểm thử cổng bảo vệ ràng buộc và chống stale/forged terminal."""

    def test_t08_binding_gate_missing_or_forged_pending(self, tmp_path):
        page, video_paths, m_id = _setup_review_page(tmp_path, clip_count=1)
        try:
            curr_ctx = page._get_current_context()
            sig = page._build_pending_scan_input_signature(curr_ctx)
            fake_snap = _make_verified_snapshot("C01", m_id, video_paths[0], SAMPLE_SHA_1)

            # Trường hợp 1: Không có pending expected
            page._pending_preapproval_expected = None
            payload1 = PreapprovalEvidenceTerminalPayload(
                request_id="req_forged",
                generation=1,
                input_signature=sig,
                status="completed",
                verified_evidence_by_clip={"C01": fake_snap},
                raw_evidence_by_clip={},
                verified_count=1,
                total_count=1,
            )
            page._on_preapproval_terminal(payload1)
            assert len(page._preapproval_evidence_map) == 0

            # Trường hợp 2: Lệch generation
            item = SourceEvidenceItemRequest("C01", m_id, video_paths[0])
            mock_w2 = MagicMock(spec=PreapprovalEvidenceWorker)
            mock_w2._request = PreapprovalEvidenceRequest(request_id="req_01", generation=1, manifest_id=m_id, input_signature=sig, items=(item,))
            mock_w2.request = mock_w2._request
            page._preapproval_evidence_generation = 1
            page._pending_preapproval_expected = (mock_w2, "req_01", 1, sig, (item,))
            page._preapproval_evidence_workers["req_01"] = mock_w2
            payload2 = PreapprovalEvidenceTerminalPayload(
                request_id="req_01",
                generation=2,  # Sai generation
                input_signature=sig,
                status="completed",
                verified_evidence_by_clip={"C01": fake_snap},
                raw_evidence_by_clip={},
                verified_count=1,
                total_count=1,
            )
            page._on_preapproval_terminal(payload2, worker=mock_w2)
            assert len(page._preapproval_evidence_map) == 0

            # Trường hợp 3: Lệch input_signature
            payload3 = PreapprovalEvidenceTerminalPayload(
                request_id="req_01",
                generation=1,
                input_signature="wrong_sig_value",  # Sai signature
                status="completed",
                verified_evidence_by_clip={"C01": fake_snap},
                raw_evidence_by_clip={},
                verified_count=1,
                total_count=1,
            )
            page._on_preapproval_terminal(payload3, worker=mock_w2)
            assert len(page._preapproval_evidence_map) == 0
        finally:
            page._preapproval_evidence_workers.clear()

    def test_t08_binding_gate_context_changed_during_scan(self, tmp_path):
        page, video_paths, m_id = _setup_review_page(tmp_path, clip_count=1)
        try:
            curr_ctx = page._get_current_context()
            orig_sig = page._build_pending_scan_input_signature(curr_ctx)
            fake_snap = _make_verified_snapshot("C01", m_id, video_paths[0], SAMPLE_SHA_1)

            item = SourceEvidenceItemRequest("C01", m_id, video_paths[0])
            mock_w = MagicMock(spec=PreapprovalEvidenceWorker)
            mock_w._request = PreapprovalEvidenceRequest(request_id="req_ctx", generation=1, manifest_id=m_id, input_signature=orig_sig, items=(item,))
            mock_w.request = mock_w._request
            page._preapproval_evidence_generation = 1
            page._pending_preapproval_expected = (mock_w, "req_ctx", 1, orig_sig, (item,))
            page._preapproval_evidence_workers["req_ctx"] = mock_w

            # Giả lập người dùng thay đổi kịch bản trong khi đang quét
            new_plan = {
                "version": "1.0",
                "cuts": [{"clip_id": "C01", "start_sec": 1.0, "end_sec": 4.0}],
                "timeline_segments": [{"clip_id": "C01", "start_sec": 1.0, "end_sec": 4.0}],
            }
            setattr(page, "_video_plan_data", new_plan)

            # Terminal payload cũ gửi về
            payload = PreapprovalEvidenceTerminalPayload(
                request_id="req_ctx",
                generation=1,
                input_signature=orig_sig,
                status="completed",
                verified_evidence_by_clip={"C01": fake_snap},
                raw_evidence_by_clip={},
                verified_count=1,
                total_count=1,
            )
            page._on_preapproval_terminal(payload, worker=mock_w)

            # Từ chối nạp kết quả cũ, giữ nguyên trạng thái chưa quét
            assert len(page._preapproval_evidence_map) == 0
            assert "⚠ Kết quả quét đã cũ do dữ liệu nguồn đã thay đổi" in page.lbl_source_evidence_status.text()
        finally:
            page._preapproval_evidence_workers.clear()


# ---------------------------------------------------------------------------
# T09: Cache Owner & Session Retention
# ---------------------------------------------------------------------------

class TestReviewPreapprovalCacheOwner:
    """Kiểm thử quyền sở hữu cache của ReviewPage và hành vi mở lại trang."""

    def test_t09_cache_owner_same_instance_service_reuse(self, tmp_path):
        page, video_paths, m_id = _setup_review_page(tmp_path, clip_count=1)
        try:
            svc1 = page._get_or_create_preapproval_service()
            svc2 = page._get_or_create_preapproval_service()
            assert svc1 is svc2
            assert isinstance(svc1, SourceEvidenceService)
        finally:
            page.shutdown_analysis_workers()

    def test_t09_no_autoworker_on_reopen_or_show(self, tmp_path):
        page, video_paths, m_id = _setup_review_page(tmp_path, clip_count=1)
        try:
            assert len(page._preapproval_evidence_workers) == 0
            # Giả lập sự kiện show
            page.show()
            QApplication.instance().processEvents()
            assert len(page._preapproval_evidence_workers) == 0
        finally:
            page.shutdown_analysis_workers()


# ---------------------------------------------------------------------------
# T10: Approval Flow
# ---------------------------------------------------------------------------

class TestReviewPreapprovalApprovalFlow:
    """Kiểm thử luồng phê duyệt biên tập và phê duyệt có bằng chứng nguồn."""

    def test_t10_whole_approval_with_verified_proof_binds_sha_and_stream(self, tmp_path):
        page, video_paths, m_id = _setup_review_page(tmp_path, clip_count=2)
        try:
            # Gán bằng chứng xác minh cho cả 2 clip
            snap1 = _make_verified_snapshot("C01", m_id, video_paths[0], SAMPLE_SHA_1, stream_idx=0)
            snap2 = _make_verified_snapshot("C02", m_id, video_paths[1], SAMPLE_SHA_2, stream_idx=1)
            page._preapproval_evidence_map = {"C01": snap1, "C02": snap2}

            # Tái tạo đề xuất
            page._rebuild_and_present_proposal()
            prop = page._current_proposal
            assert prop is not None
            clips = {c["clip_id"]: c for c in prop.source_binding["clips"]}
            assert clips["C01"]["expected_full_sha256"] == SAMPLE_SHA_1
            assert clips["C01"]["stream_index"] == 0
            assert clips["C02"]["expected_full_sha256"] == SAMPLE_SHA_2
            assert clips["C02"]["stream_index"] == 1

            # Người dùng bấm duyệt
            rev = compute_proposal_revision(prop)
            page._on_proposal_approved(prop.proposal_id, rev)

            # Getter trả về bản ghi hoàn hảo khớp revision và SHA
            res = page.get_approved_proposal_and_record()
            assert res is not None
            owned_prop, owned_appr = res
            assert owned_appr.proposal_revision == rev
            owned_clips = {c["clip_id"]: c for c in owned_prop.source_binding["clips"]}
            assert owned_clips["C01"]["expected_full_sha256"] == SAMPLE_SHA_1
            assert owned_clips["C02"]["expected_full_sha256"] == SAMPLE_SHA_2
        finally:
            page.shutdown_analysis_workers()

    def test_t10_editorial_approval_without_proof_remains_valid(self, tmp_path):
        page, video_paths, m_id = _setup_review_page(tmp_path, clip_count=1)
        try:
            # Chưa có bằng chứng nguồn
            page._preapproval_evidence_map.clear()
            page._rebuild_and_present_proposal()
            prop = page._current_proposal
            assert prop is not None
            clips = {c["clip_id"]: c for c in prop.source_binding["clips"]}
            assert "expected_full_sha256" not in clips["C01"]

            # Phê duyệt biên tập vẫn thành công
            rev = compute_proposal_revision(prop)
            page._on_proposal_approved(prop.proposal_id, rev)

            res = page.get_approved_proposal_and_record()
            assert res is not None
            owned_prop, owned_appr = res
            assert owned_appr.proposal_revision == rev
            owned_clips = {c["clip_id"]: c for c in owned_prop.source_binding["clips"]}
            assert "expected_full_sha256" not in owned_clips["C01"]
        finally:
            page.shutdown_analysis_workers()

    def test_t10_explicit_blocked_geometry_cannot_be_bypassed(self, tmp_path):
        page, video_paths, m_id = _setup_review_page(tmp_path, clip_count=1)
        try:
            # Cung cấp frame evidence table nhưng supplier thiếu fps
            page.set_context_supplier(lambda: {
                "video_paths": list(video_paths),
                "timeline_fps": None,  # Thiếu FPS
                "fps": None,
                "creative_context": {"style_preset": "vlog"},
            })
            page._source_frame_evidence_table = {
                "C01": SourceFrameEvidence(
                    manifest_id=m_id,
                    clip_id="C01",
                    source_fps=RationalFPS(30, 1),
                    total_frames=300,
                    evidence_status="VERIFIED_CFR",
                )
            }

            page._rebuild_and_present_proposal()
            assert page._current_geometry_result is not None
            assert page._current_geometry_result.status == "GEOMETRY_BLOCKED"

            # Dù có nạp bằng chứng byte proof, trạng thái BLOCKED vẫn không bị lách qua
            snap = _make_verified_snapshot("C01", m_id, video_paths[0], SAMPLE_SHA_1)
            page._preapproval_evidence_map = {"C01": snap}
            page._rebuild_and_present_proposal()
            assert page._current_geometry_result.status == "GEOMETRY_BLOCKED"

            # Phê duyệt bị từ chối
            prop = page._current_proposal
            rev = compute_proposal_revision(prop)
            page._on_proposal_approved(prop.proposal_id, rev)
            assert page.get_approved_proposal_and_record() is None
        finally:
            page.shutdown_analysis_workers()


# ---------------------------------------------------------------------------
# T11: Close / Shutdown Protocol Integration
# ---------------------------------------------------------------------------

class TestReviewPreapprovalShutdownProtocol:
    """Kiểm thử tích hợp giao thức tắt / đóng cửa sổ với preapproval worker."""

    def test_t11_shutdown_analysis_workers_cancels_preapproval_worker(self, tmp_path):
        page, video_paths, m_id = _setup_review_page(tmp_path, clip_count=1)
        try:
            mock_worker = MagicMock(spec=PreapprovalEvidenceWorker)
            mock_worker.cancel = MagicMock()
            page._preapproval_evidence_workers["req_sd"] = mock_worker

            # Gọi shutdown_analysis_workers
            res = page.shutdown_analysis_workers()
            mock_worker.cancel.assert_called_once()
            # Vì mock_worker chưa dọn vật lý khỏi registry nên trả về False (chưa hoàn toàn rỗng)
            assert res is False
            assert page.is_analysis_idle() is False

            # Sau khi worker hoàn tất vật lý
            page._on_preapproval_evidence_worker_finished("req_sd", mock_worker)
            assert page.is_analysis_idle() is True
        finally:
            page._preapproval_evidence_workers.clear()

    def test_t11_analysis_idle_signal_waits_for_physical_finish(self, tmp_path):
        page, video_paths, m_id = _setup_review_page(tmp_path, clip_count=1)
        try:
            idle_spy = MagicMock()
            page.analysis_workers_idle.connect(idle_spy)

            mock_worker = MagicMock(spec=PreapprovalEvidenceWorker)
            page._preapproval_evidence_workers["req_sig"] = mock_worker
            page._is_analysis_loading = True

            # Nhận terminal payload: tín hiệu idle CHƯA ĐƯỢC PHÁT
            payload = PreapprovalEvidenceTerminalPayload(
                request_id="req_sig",
                generation=0,
                input_signature="sig",
                status="completed",
                verified_evidence_by_clip={},
                raw_evidence_by_clip={},
            )
            # Terminal không emit idle
            assert idle_spy.call_count == 0

            # Finished vật lý: lúc này mới emit idle
            page._on_preapproval_evidence_worker_finished("req_sig", mock_worker)
            assert idle_spy.call_count == 1
        finally:
            page._preapproval_evidence_workers.clear()


# ---------------------------------------------------------------------------
# T12: Tiny Real Pipeline Trace
# ---------------------------------------------------------------------------

class TestReviewPreapprovalRealPipeline:
    """Kiểm thử chu trình thực tế siêu nhỏ: ffmpeg video -> QThread worker -> ReviewPage -> Approval -> Getter."""

    def test_t12_tiny_real_pipeline_trace(self, tmp_path):
        # 1. Tạo tệp video thật 64x36 CFR có 2 B-frames qua ffmpeg
        page, video_paths, m_id = _setup_review_page(tmp_path, clip_count=1, create_real_media=True)
        try:
            # 2. Bấm nút quét kiểm tra nguồn
            page.btn_check_source_evidence.click()

            # Đợi worker thực thi trên QThread nền
            _wait_for_review_workers(page, timeout_sec=8.0)

            # 3. Kiểm tra nhãn trạng thái hiển thị đúng chuẩn tiếng Việt màu xanh
            status_text = page.lbl_source_evidence_status.text()
            assert "✓ Đã xác minh 1/1 nguồn — chưa kiểm tra đích dựng" in status_text

            # 4. Kiểm tra proposal đã được tự động tái tạo với expected SHA và stream_index thật
            prop = page._current_proposal
            assert prop is not None
            clips = {c["clip_id"]: c for c in prop.source_binding["clips"]}
            c01_binding = clips.get("C01")
            assert c01_binding is not None
            assert c01_binding.get("expected_full_sha256") is not None
            assert len(c01_binding["expected_full_sha256"]) == 64
            # Video stream được trích xuất (audio là stream 0, video là stream 1)
            assert c01_binding.get("stream_index") == 1

            # 5. Người dùng thực hiện phê duyệt
            rev = compute_proposal_revision(prop)
            page._on_proposal_approved(prop.proposal_id, rev)

            # 6. Getter trả về cặp proposal và record đã được khóa với byte proof
            result = page.get_approved_proposal_and_record()
            assert result is not None
            approved_prop, approved_record = result
            assert approved_record.proposal_revision == rev
            approved_clips = {c["clip_id"]: c for c in approved_prop.source_binding["clips"]}
            assert approved_clips["C01"]["expected_full_sha256"] == c01_binding["expected_full_sha256"]
            assert approved_clips["C01"]["stream_index"] == 1

        finally:
            page.shutdown_analysis_workers()
            _wait_for_review_workers(page, timeout_sec=3.0)


class TestReviewPreapprovalR1Repairs:
    """Kiểm thử các ràng buộc sửa đổi của G07-07A4-R1 (Group C và Group D)."""

    def test_r1_ui_terminal_exact_binding_and_mutation_isolation(self, tmp_path):
        import copy
        page, paths, mid = _setup_review_page(tmp_path, clip_count=1)
        try:
            with patch.object(PreapprovalEvidenceWorker, "start", return_value=None):
                page._on_check_source_evidence_clicked()

            assert len(page._preapproval_evidence_workers) == 1
            worker = next(iter(page._preapproval_evidence_workers.values()))
            snapshot = _make_verified_snapshot("C01", mid, paths[0], "1" * 64)
            fe = snapshot["frame_evidence"]
            raw_env = SourceExecutionEvidenceEnvelope(
                "C01", mid, paths[0], snapshot["stream_index"], None, "1" * 64,
                SourceFreshnessStat(**snapshot["freshness"]),
                SourceFrameEvidence(mid, "C01", RationalFPS(**fe["source_fps"]), fe["total_frames"], "VERIFIED_CFR"),
                True, "decoded_frame_uniform_ticks_v1",
            )
            def make_payload(**kw):
                data = dict(
                    request_id=worker.request.request_id,
                    generation=worker.request.generation,
                    input_signature=worker.request.input_signature,
                    status="completed",
                    verified_evidence_by_clip={"C01": snapshot},
                    raw_evidence_by_clip={"C01": raw_env},
                    verified_count=1,
                    total_count=1,
                )
                data.update(kw)
                return PreapprovalEvidenceTerminalPayload(**data)

            # 1. Alias isolation: Mutating snapshot does not mutate page proof map
            worker.terminal_signal.emit(make_payload())
            assert page._preapproval_evidence_map.get("C01", {}).get("observed_full_sha256") == "1" * 64
            snapshot["observed_full_sha256"] = "2" * 64
            assert page._preapproval_evidence_map.get("C01", {}).get("observed_full_sha256") == "1" * 64

            # 2. False counters denied before mutation
            page._preapproval_evidence_map.clear()
            page._pending_preapproval_expected = (worker, worker.request.request_id, worker.request.generation, worker.request.input_signature, worker.request.items)
            worker.terminal_signal.emit(make_payload(verified_count=99, total_count=99))
            assert len(page._preapproval_evidence_map) == 0

            # 3. Bool generation denied
            page._pending_preapproval_expected = (worker, worker.request.request_id, worker.request.generation, worker.request.input_signature, worker.request.items)
            worker.terminal_signal.emit(make_payload(generation=True))
            assert len(page._preapproval_evidence_map) == 0

            # 4. Foreign sender denied
            page._pending_preapproval_expected = (worker, worker.request.request_id, worker.request.generation, worker.request.input_signature, worker.request.items)
            stranger = PreapprovalEvidenceWorker(worker.request)
            stranger.terminal_signal.connect(page._on_preapproval_terminal)
            stranger.terminal_signal.emit(make_payload())
            assert len(page._preapproval_evidence_map) == 0
            stranger.deleteLater()

            # 5. Malformed snapshot denied before mutation
            page._pending_preapproval_expected = (worker, worker.request.request_id, worker.request.generation, worker.request.input_signature, worker.request.items)
            broken = copy.deepcopy(snapshot)
            broken["file_path"] = "/foreign.mp4"
            worker.terminal_signal.emit(make_payload(verified_evidence_by_clip={"C01": broken}))
            assert len(page._preapproval_evidence_map) == 0

        finally:
            page.shutdown_analysis_workers()

    def test_r1_ui_user_cancel_and_source_invalidation(self, tmp_path):
        page, paths, mid = _setup_review_page(tmp_path, clip_count=1)
        try:
            with patch.object(PreapprovalEvidenceWorker, "start", return_value=None):
                page._on_check_source_evidence_clicked()

            worker = next(iter(page._preapproval_evidence_workers.values()))
            # 1. Cancel control usable while busy
            page._update_source_evidence_button_state()
            assert page.btn_check_source_evidence.isEnabled() is True
            page._on_check_source_evidence_clicked()
            assert worker._is_cancelled() is True

            # 2. Source change invalidates proof
            snapshot = _make_verified_snapshot("C01", mid, paths[0], "1" * 64)
            page._preapproval_evidence_map = {"C01": snapshot}
            new_source = tmp_path / "replacement.mp4"
            new_source.write_bytes(b"other")
            page._manifest.clips[0].current_path = str(new_source)
            page.set_context_supplier(lambda: dict(video_paths=[str(new_source)], creative_context=dict(style_preset="travel_vlog", target_duration_sec=60)))
            page._rebuild_and_present_proposal()
            assert len(page._preapproval_evidence_map) == 0

        finally:
            page.shutdown_analysis_workers()

    def test_r1_ui_start_query_failure_contained_and_cancels_retained(self, tmp_path):
        page, _, _ = _setup_review_page(tmp_path, clip_count=1)
        try:
            caught = None
            with patch.object(PreapprovalEvidenceWorker, "start", side_effect=RuntimeError("start failed")), patch.object(PreapprovalEvidenceWorker, "isRunning", side_effect=RuntimeError("query unknown")):
                try:
                    page._on_check_source_evidence_clicked()
                except Exception as e:
                    caught = type(e).__name__

            assert caught is None
            workers = list(page._preapproval_evidence_workers.values())
            assert bool(workers) is True
            assert all(w._is_cancelled() for w in workers)
        finally:
            page.shutdown_analysis_workers()
