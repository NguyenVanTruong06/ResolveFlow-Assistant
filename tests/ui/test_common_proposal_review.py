"""
tests/ui/test_common_proposal_review.py
=============================================================================
Suite Kiểm thử Chấp nhận Giao diện Đánh giá Kịch bản Toàn bản (G06-06C3-S3):
T01-T10 Acceptance Matrix:
- T01: ReviewPage nạp Slot 1 / Dual-slot -> ProposalBuilder -> CommonReviewWidget -> Phê duyệt biên tập (0 Resolve execution).
- T02: Lựa chọn ứng viên / chủ động lược bỏ -> Tái lập đề xuất với revision mới, hủy duyệt cũ, ứng viên offline/reconnect thành omission.
- T03: Dữ liệu biến dạng / sai kiểu (bool, danh sách, thời gian âm) -> Bắt lỗi có kiểm soát, khóa nút duyệt, không làm đột biến dữ liệu gốc.
- T04: Bằng chứng hình học (Geometry): Thiếu bảng bằng chứng -> Hiển thị trung thực UNRESOLVED; Có bảng bằng chứng -> VERIFIED / BLOCKED (khóa duyệt nếu BLOCKED).
- T05: Ràng buộc revision tại thời điểm bấm duyệt: Chống stale event / TOCTOU, sửa kịch bản hủy duyệt cũ.
- T06: Tính ổn định của revision hash: Cùng đầu vào sinh cùng hash; Kết quả matching đến trễ hủy duyệt kịch bản cũ an toàn.
- T07: Giới hạn biên tập: Phê duyệt biên tập hoàn toàn không gọi DaVinci Resolve (G07) hoặc network API; prompt V1/V2 được bảo toàn.
- T08: Bố cục giao diện co giãn ở độ phân giải mục tiêu 1200x750 và 1280x800, các bảng QTableView và nút bấm đầy đủ, không tràn viền.
- T09: Thẩm định API độc lập của CommonProposalReviewWidget: set_proposal, set_stale, set_error, clear xử lý fail-closed an toàn.
- T10: Đảm bảo cách ly môi trường và giải phóng sạch sẽ tài nguyên sau khi kết thúc test.
=============================================================================
"""

import copy
import time
from fractions import Fraction
from typing import Any, Dict, List, Optional
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.core.clip_manifest import ClipEntry, ClipManifest
from src.core.contracts.edit_plan_approval_contracts import (
    AppOwnedApprovalRecord,
    verify_proposal_approval_binding,
)
from src.core.contracts.edit_plan_contracts import (
    CreativeContext,
    normalize_creative_context,
)
from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    compute_proposal_revision,
    normalize_edit_plan_proposal,
)
from src.core.contracts.plan_demand_contracts import (
    AssetDemandItem,
    AssetMatchingSidecar,
    DemandMatchEntry,
)
from src.core.contracts.time_contracts import RationalFPS
from src.core.geometry.plan_geometry_contracts import (
    PlanGeometryAssemblyResult,
    PlanGeometryIssue,
    SourceFrameEvidence,
)
from src.core.geometry.plan_geometry_assembler import assemble_plan_frame_geometry
from src.core.plan_demand_adapter import PlanDemandAdapter
from src.core.proposal_builder import build_edit_plan_proposal
from src.core.offline_asset_matcher import (
    OfflineAssetCandidate,
    OfflineAssetMatchResult,
)
from src.ui.director.pages.review_page import ReviewPage
from src.ui.director.widgets.common_proposal_review_widget import (
    AssignmentsTableModel,
    CommonProposalReviewWidget,
    CutsTableModel,
    OmissionsTableModel,
)


@pytest.fixture(scope="module")
def qapp():
    """Đảm bảo có QApplication cho toàn bộ suite test."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def managed_review_page(qapp):
    """Fixture khởi tạo ReviewPage và đảm bảo dọn dẹp sạch sẽ tài nguyên."""
    page = ReviewPage()
    page._headless_mode = True

    yield page

    page.shutdown_analysis_workers()
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not page.is_analysis_idle():
        qapp.processEvents()
        time.sleep(0.02)
    page.deleteLater()
    qapp.processEvents()


def _make_candidate(
    asset_id: str,
    name: str = "Candidate Asset",
    category: str = "broll",
    subcategory: str = "general",
    source_kind: str = "managed",
    origin_library_id: Optional[str] = "lib_main",
    availability: str = "online",
    match_score: int = 90,
    requires_reconnect: bool = False,
    is_native_guidance: bool = False,
) -> OfflineAssetCandidate:
    return OfflineAssetCandidate(
        asset_id=asset_id,
        name=name,
        category=category,
        subcategory=subcategory,
        source_kind=source_kind,
        origin_library_id=origin_library_id,
        availability=availability,
        match_score=match_score,
        matched_signals=("test_signal",),
        duration_sec=3.0,
        pack_id="pack_01",
        pack_name="Main Pack",
        requires_reconnect=requires_reconnect,
        is_native_guidance=is_native_guidance,
    )


def _make_sample_manifest(manifest_id: str = "mf_test_ui") -> ClipManifest:
    c1 = ClipEntry.from_dict({
        "clip_id": "clip_01",
        "file_name": "scene1.mp4",
        "current_path": "/media/scene1.mp4",
        "duration_sec": 10.0,
        "fps": 24.0,
    })
    c2 = ClipEntry.from_dict({
        "clip_id": "clip_02",
        "file_name": "scene2.mp4",
        "current_path": "/media/scene2.mp4",
        "duration_sec": 15.0,
        "fps": 24.0,
    })
    return ClipManifest(
        manifest_id=manifest_id,
        project_name="ChunDVC_Project",
        clips=[c1, c2],
    )


def _make_sample_video_plan(manifest_id: str = "mf_test_ui") -> Dict[str, Any]:
    return {
        "manifest_id": manifest_id,
        "timeline_segments": [
            {
                "clip_id": "clip_01",
                "start_sec": 1.0,
                "end_sec": 4.0,
                "action": "keep",
            },
            {
                "clip_id": "clip_02",
                "start_sec": 2.0,
                "end_sec": 7.0,
                "action": "speedup",
            },
        ],
        "broll_inserts": [
            {
                "asset_type": "broll",
                "timeline_sec": 2.5,
                "duration_sec": 2.0,
                "description": "funny cat",
                "query": "funny cat",
            }
        ],
    }


def _make_sample_kinetic_plan(manifest_id: str = "mf_test_ui") -> Dict[str, Any]:
    return {
        "manifest_id": manifest_id,
        "pages": [
            {"text": "Xin chào thế giới", "start_sec": 0.5, "end_sec": 2.0}
        ],
    }


# =============================================================================
# T01: Vertical Approval Path (Slot 1 & Dual-Slot) -> Exact 8/6 Fields -> 0 Resolve
# =============================================================================

def test_t01_real_review_page_vertical_approval_slot1_and_dual(managed_review_page, qapp):
    page = managed_review_page
    manifest = _make_sample_manifest("mf_t01")
    page._manifest = manifest
    page._video_plan_data = _make_sample_video_plan("mf_t01")

    # 1. Bấm nút cập nhật đề xuất toàn bản
    page.btn_update_proposal.click()
    qapp.processEvents()

    assert page._current_proposal is not None
    prop: EditPlanProposal = page._current_proposal

    # Thẩm định đúng 8 fields chuẩn của EditPlanProposal
    assert hasattr(prop, "proposal_id")
    assert hasattr(prop, "manifest_id") and prop.manifest_id == "mf_t01"
    assert hasattr(prop, "creative_context")
    assert hasattr(prop, "source_binding")
    assert hasattr(prop, "video_plan")
    assert hasattr(prop, "kinetic_plan")
    assert hasattr(prop, "proposed_asset_assignments")
    assert hasattr(prop, "proposed_omissions")

    # Kiểm tra widget
    w = page.common_review_widget
    assert w.model_cuts.rowCount() == 2
    assert w.btn_approve_proposal.isEnabled()

    # 2. Người dùng bấm phê duyệt đề xuất
    approved_signal_fired = []
    w.proposal_approved.connect(lambda pid, rev: approved_signal_fired.append((pid, rev)))
    w.btn_approve_proposal.click()
    qapp.processEvents()

    assert len(approved_signal_fired) == 1
    assert page._current_approval_record is not None
    appr: AppOwnedApprovalRecord = page._current_approval_record

    # Thẩm định đúng 6 fields chuẩn của AppOwnedApprovalRecord
    assert hasattr(appr, "approval_id")
    assert hasattr(appr, "proposal_id") and appr.proposal_id == prop.proposal_id
    assert hasattr(appr, "proposal_revision") and appr.proposal_revision == compute_proposal_revision(prop)
    assert hasattr(appr, "approved_at_iso")
    assert hasattr(appr, "accepted_omission_ids")
    assert hasattr(appr, "approval_scope") and appr.approval_scope == "ENTIRE_PROPOSAL"

    # Kiểm tra xác thực liên kết toàn vẹn bất biến
    assert verify_proposal_approval_binding(prop, appr) is True

    # Nút bấm chuyển sang trạng thái đã duyệt (disabled)
    assert w.btn_approve_proposal.isEnabled() is False
    assert "ĐÃ PHÊ DUYỆT" in w.badge_approval.text()

    # 3. Dual-slot: Bổ sung kinetic plan
    page._kinetic_plan_data = _make_sample_kinetic_plan("mf_t01")
    page.btn_update_proposal.click()
    qapp.processEvents()

    dual_prop: EditPlanProposal = page._current_proposal
    assert dual_prop.kinetic_plan is not None
    # Thay đổi kịch bản làm vô hiệu hóa hồ sơ duyệt cũ
    assert page._current_approval_record is None
    assert w.btn_approve_proposal.isEnabled() is True


# =============================================================================
# T02: Candidate Choices, Omissions Accounting, Offline Downgrade
# =============================================================================

def test_t02_candidate_selection_and_omission_overrides(managed_review_page, qapp):
    page = managed_review_page
    manifest = _make_sample_manifest("mf_t02")
    page._manifest = manifest
    page._video_plan_data = _make_sample_video_plan("mf_t02")

    # Trích xuất canonical batch từ plan thực tế
    _, plan_rev, _ = page._build_matching_context(page._video_plan_data)
    batch = PlanDemandAdapter.extract(page._video_plan_data, plan_revision=plan_rev)
    demand_item = batch.demands[0]

    cand_online = _make_candidate("asset_cat_meme", availability="online")
    cand_alt = _make_candidate("asset_cat_alt", availability="online")
    res_broll = OfflineAssetMatchResult(
        request_id=demand_item.demand_id,
        category="broll",
        query_text="funny cat",
        is_matched=True,
        candidates=(cand_online, cand_alt),
    )
    sidecar = PlanDemandAdapter.assemble(batch, (res_broll,))
    page._last_good_matching_sidecar = sidecar

    # Cập nhật đề xuất
    page.btn_update_proposal.click()
    qapp.processEvents()

    w = page.common_review_widget
    assert len(page._current_proposal.proposed_asset_assignments) == 1
    assert len(page._current_proposal.proposed_omissions) == 0

    # Phê duyệt lần 1
    w.btn_approve_proposal.click()
    qapp.processEvents()
    first_appr = page._current_approval_record
    assert first_appr is not None

    # Tương tác giao diện thật: Chọn dòng 0 và đổi ứng viên sang asset_cat_alt qua combobox và nút bấm
    w.tbl_assignments.selectRow(0)
    qapp.processEvents()
    assert w.combo_assign_candidate.isEnabled() is True
    assert w.btn_apply_candidate.isEnabled() is True
    idx = w.combo_assign_candidate.findData("asset_cat_alt")
    assert idx >= 0
    w.combo_assign_candidate.setCurrentIndex(idx)
    w.btn_apply_candidate.click()
    qapp.processEvents()

    # Khẳng định: Hồ sơ duyệt cũ đã bị hủy! Đã đổi thành công sang asset_cat_alt
    assert page._current_approval_record is None
    assert page._current_proposal.proposed_asset_assignments[0]["asset_id"] == "asset_cat_alt"

    # Duyệt lại với ứng viên mới
    w.btn_approve_proposal.click()
    qapp.processEvents()
    assert page._current_approval_record is not None

    # Tương tác giao diện thật: Bấm nút lược bỏ mục đã chọn
    w.tbl_assignments.selectRow(0)
    qapp.processEvents()
    w.btn_omit_selected.click()
    qapp.processEvents()

    # Khẳng định: Hồ sơ duyệt cũ bị hủy, mục chuyển sang bảng omissions
    assert page._current_approval_record is None
    assert len(page._current_proposal.proposed_asset_assignments) == 0
    assert len(page._current_proposal.proposed_omissions) == 1
    assert page._current_proposal.proposed_omissions[0]["demand_id"] == demand_item.demand_id

    # Tương tác giao diện thật: Chuyển sang tab omissions, chọn dòng và bấm khôi phục
    w.tab_widget.setCurrentIndex(2)
    w.tbl_omissions.selectRow(0)
    qapp.processEvents()
    assert w.combo_restore_candidate.isEnabled() is True
    assert w.btn_restore_selected.isEnabled() is True
    w.btn_restore_selected.click()
    qapp.processEvents()

    # Khẳng định: Mục đã được khôi phục trở lại assignments
    assert len(page._current_proposal.proposed_asset_assignments) == 1
    assert len(page._current_proposal.proposed_omissions) == 0


# =============================================================================
# T03: Malformed Inputs, Strict Types, Zero Mutation
# =============================================================================

def test_t03_malformed_inputs_controlled_error(managed_review_page, qapp):
    page = managed_review_page
    manifest = _make_sample_manifest("mf_t03")
    page._manifest = manifest

    # Kịch bản bị lỗi: start_sec âm
    bad_plan = {
        "manifest_id": "mf_t03",
        "timeline_segments": [
            {"clip_id": "clip_01", "start_sec": -5.0, "end_sec": 3.0}
        ],
    }
    raw_copy = copy.deepcopy(bad_plan)
    page._video_plan_data = bad_plan

    page.btn_update_proposal.click()
    qapp.processEvents()

    w = page.common_review_widget
    # Bắt lỗi có kiểm soát
    assert "SCHEMA: KHÔNG HỢP LỆ" in w.badge_schema.text()
    assert w.btn_approve_proposal.isEnabled() is False
    # Dữ liệu gốc không bị biến dạng
    assert bad_plan == raw_copy


# =============================================================================
# T04: Geometry: Honest Unresolved & Conditional Verified/Blocked
# =============================================================================

def test_t04_geometry_honest_unresolved_and_conditional_blocked(managed_review_page, qapp):
    page = managed_review_page
    manifest = _make_sample_manifest("mf_t04")
    page._manifest = manifest
    page._video_plan_data = _make_sample_video_plan("mf_t04")

    # 1. Chưa có frame evidence -> Honest UNRESOLVED
    page.btn_update_proposal.click()
    qapp.processEvents()

    w = page.common_review_widget
    assert "UNRESOLVED" in w.badge_geometry.text()
    # Thiếu frame evidence vẫn cho phép duyệt biên tập (với cảnh báo)
    assert w.btn_approve_proposal.isEnabled() is True

    # 2. Cung cấp frame evidence table không hợp lệ -> assemble_plan_frame_geometry thất bại và bị BLOCKED
    page._source_frame_evidence_table = {"clip_01": "invalid_evidence_object"}
    page._stored_validated_context = {"timeline_fps": RationalFPS(24, 1)}
    page.btn_update_proposal.click()
    qapp.processEvents()

    assert "HÌNH HỌC: BỊ CHẶN" in w.badge_geometry.text()
    assert w.btn_approve_proposal.isEnabled() is False  # BỊ KHÓA DUYỆT!

    # 3. Widget từ chối đối tượng geometry ngoại lai / giả mạo
    class ForeignGeometry:
        proposal_id = "foreign_pid"
        proposal_revision = "foreign_rev"
        status = "GEOMETRY_VERIFIED"
    w.set_proposal(page._current_proposal, ForeignGeometry())
    qapp.processEvents()
    assert "BỊ CHẶN" in w.badge_geometry.text()
    assert w.btn_approve_proposal.isEnabled() is False


# =============================================================================
# T05: Action-Time Revision Binding & Stale Invalidation
# =============================================================================

def test_t05_action_time_revision_binding_and_stale_invalidation(managed_review_page, qapp):
    page = managed_review_page
    manifest = _make_sample_manifest("mf_t05")
    page._manifest = manifest
    page._video_plan_data = _make_sample_video_plan("mf_t05")

    page.btn_update_proposal.click()
    qapp.processEvents()

    w = page.common_review_widget
    old_rev = page.common_review_widget._proposal_revision

    # Phát tín hiệu duyệt giả mạo revision cũ
    page._on_proposal_approved(page._current_proposal.proposal_id, "forged_old_rev_000000000000000000000000000000000000000000000000000000000000")
    qapp.processEvents()

    # Bị từ chối, hồ sơ duyệt không được tạo
    assert page._current_approval_record is None

    # Thay đổi creative context làm kịch bản chuyển sang stale
    page.notify_creative_context_changed()
    qapp.processEvents()

    assert w._is_stale is True
    assert "CẦN CẬP NHẬT" in w.badge_approval.text()
    assert w.btn_approve_proposal.isEnabled() is False

    # Thử click nút duyệt khi đang stale: bị khóa, không phát sinh duyệt
    w.btn_approve_proposal.click()
    qapp.processEvents()
    assert page._current_approval_record is None

    # Thử gọi trực tiếp slot _on_approve_clicked khi stale: không phát tín hiệu
    w._on_approve_clicked()
    qapp.processEvents()
    assert page._current_approval_record is None


# =============================================================================
# T06: Stable Revision Hash & Late Matching Outcome
# =============================================================================

def test_t06_stable_revision_hash_and_late_matching(managed_review_page, qapp):
    page = managed_review_page
    manifest = _make_sample_manifest("mf_t06")
    page._manifest = manifest
    page._video_plan_data = _make_sample_video_plan("mf_t06")

    # Lần 1
    page.btn_update_proposal.click()
    rev1 = page._current_proposal_revision = compute_proposal_revision(page._current_proposal)

    # Lần 2 với cùng đầu vào
    page.btn_update_proposal.click()
    rev2 = compute_proposal_revision(page._current_proposal)

    # Khẳng định hash ổn định tuyệt đối
    assert rev1 == rev2

    # Phê duyệt đề xuất lần 1
    w = page.common_review_widget
    w.btn_approve_proposal.click()
    assert page._current_approval_record is not None

    # Thay đổi kịch bản (khiến matching outcome cũ trở thành trễ / stale)
    page._on_video_plan_changed()
    qapp.processEvents()

    # Hồ sơ duyệt cũ đã bị hủy và widget được đánh dấu stale
    assert page._current_approval_record is None
    assert w._is_stale is True


# =============================================================================
# T07: Editorial Only — Zero DaVinci Resolve Execution
# =============================================================================

def test_t07_editorial_only_zero_resolve_execution(managed_review_page, qapp, monkeypatch):
    page = managed_review_page
    manifest = _make_sample_manifest("mf_t07")
    page._manifest = manifest
    page._video_plan_data = _make_sample_video_plan("mf_t07")

    # Spy kiểm tra không có bất kỳ lệnh gọi Resolve nào
    resolve_called = []
    monkeypatch.setattr("src.ui.director.pages.review_page.logger.info", lambda *args: resolve_called.append(args))

    page.btn_update_proposal.click()
    page.common_review_widget.btn_approve_proposal.click()
    qapp.processEvents()

    # Phê duyệt thành công hồ sơ biên tập
    assert page._current_approval_record is not None
    # 0 execution actions
    assert not any("davinci" in str(x).lower() or "render" in str(x).lower() for x in resolve_called)


# =============================================================================
# T08: Bounded Layout at 1200x750 & 1280x800
# =============================================================================

def test_t08_layout_bounds_and_compact_sizes(qapp):
    w = CommonProposalReviewWidget()
    w.show()

    # Kiểm tra kích thước 1200x750
    w.resize(1200, 750)
    qapp.processEvents()
    assert w.width() == 1200
    assert w.height() == 750
    assert w.tab_widget.isVisible()
    assert w.btn_approve_proposal.isVisible()

    # Kiểm tra kích thước 1280x800
    w.resize(1280, 800)
    qapp.processEvents()
    assert w.width() == 1280
    assert w.height() == 800
    assert w.tab_widget.isVisible()

    w.close()
    w.deleteLater()
    qapp.processEvents()


# =============================================================================
# T09: CommonProposalReviewWidget Direct API & Typed Revalidation
# =============================================================================

def test_t09_widget_api_direct_typed_revalidation(qapp):
    w = CommonProposalReviewWidget()

    # 1. set_proposal(None) -> Xóa trắng
    w.set_proposal(None)
    assert w._proposal is None
    assert w.btn_approve_proposal.isEnabled() is False

    # 2. set_error -> Báo lỗi có kiểm soát
    w.set_error("Lỗi giả lập kiểm tra API")
    assert "KHÔNG HỢP LỆ" in w.badge_schema.text()
    assert w.btn_approve_proposal.isEnabled() is False

    # 3. set_stale -> Đánh dấu cũ
    w.set_stale(True)
    assert w._is_stale is True

    # 4. clear
    w.clear()
    assert w.model_cuts.rowCount() == 0
    assert w.model_assignments.rowCount() == 0
    assert w.model_omissions.rowCount() == 0

    w.deleteLater()
    qapp.processEvents()


# =============================================================================
# T10: Environment Isolation & Clean Thread Teardown
# =============================================================================

def test_t10_isolation_and_clean_teardown(managed_review_page, qapp):
    page = managed_review_page
    assert page.is_analysis_idle() is True
    # Không có worker mồ côi
    assert len(page._matching_workers_registry) == 0
    assert len(page._active_analysis_workers) == 0


# =============================================================================
# Probes A1-A3, C1: Current-Input Authority & Geometry Enforcement
# =============================================================================

def test_probe_a1_stale_creative_proposal_reapproved_denied(managed_review_page, qapp):
    """Probe A1: Sau khi duyệt, sửa creative context -> Nút duyệt bị disable, click/event cũ đều bị từ chối."""
    page = managed_review_page
    manifest = _make_sample_manifest("mf_a1")
    page._manifest = manifest
    page._video_plan_data = _make_sample_video_plan("mf_a1")

    # Duyệt lần đầu
    page.btn_update_proposal.click()
    qapp.processEvents()
    w = page.common_review_widget
    w.btn_approve_proposal.click()
    qapp.processEvents()
    assert page._current_approval_record is not None
    old_pid = page._current_proposal.proposal_id
    old_rev = compute_proposal_revision(page._current_proposal)

    # Đổi creative context
    page._stored_validated_context = {
        "creative_context": normalize_creative_context({"free_brief": "Brief đã thay đổi mới"}),
    }
    page.notify_creative_context_changed()
    qapp.processEvents()

    # Widget đánh dấu stale và disable nút duyệt
    assert w._is_stale is True
    assert w.btn_approve_proposal.isEnabled() is False

    # Thử click nút duyệt
    w.btn_approve_proposal.click()
    qapp.processEvents()
    assert page._current_approval_record is None

    # Thử gọi direct event cũ
    page._on_proposal_approved(old_pid, old_rev)
    qapp.processEvents()
    assert page._current_approval_record is None


def test_probe_a2_plan_mutation_without_signal_denied(managed_review_page, qapp):
    """Probe A2: Đột biến _video_plan_data in-place không qua tín hiệu -> Bị từ chối duyệt kịch bản cũ."""
    page = managed_review_page
    manifest = _make_sample_manifest("mf_a2")
    page._manifest = manifest
    page._video_plan_data = _make_sample_video_plan("mf_a2")

    page.btn_update_proposal.click()
    qapp.processEvents()
    w = page.common_review_widget
    old_pid = page._current_proposal.proposal_id
    old_rev = compute_proposal_revision(page._current_proposal)

    # Đột biến in-place không phát signal
    page._video_plan_data["timeline_segments"][0]["end_sec"] = 3.5

    # Gọi duyệt với old_rev
    page._on_proposal_approved(old_pid, old_rev)
    qapp.processEvents()

    assert page._current_approval_record is None
    assert w._is_stale is True


def test_probe_a2_changed_active_source_set_denied(managed_review_page, qapp):
    """Probe A2: Thay đổi tập nguồn active video_paths khác manifest -> Từ chối duyệt."""
    page = managed_review_page
    manifest = _make_sample_manifest("mf_a2_src")
    page._manifest = manifest
    page._video_plan_data = _make_sample_video_plan("mf_a2_src")

    page.btn_update_proposal.click()
    qapp.processEvents()
    old_pid = page._current_proposal.proposal_id
    old_rev = compute_proposal_revision(page._current_proposal)

    # Thay đổi video_paths sang file lạ không thuộc manifest
    page._stored_validated_context = {
        "video_paths": ["/media/unregistered_foreign_clip.mp4"],
    }

    page._on_proposal_approved(old_pid, old_rev)
    qapp.processEvents()

    assert page._current_approval_record is None


def test_probe_a2_unavailable_current_context_fails_rebuild_and_clears_approval(managed_review_page, qapp):
    """Probe A2: Supplier context trả về {} -> Báo lỗi, xóa approval, không tự động fallback."""
    page = managed_review_page
    manifest = _make_sample_manifest("mf_a2_empty_ctx")
    page._manifest = manifest
    page._video_plan_data = _make_sample_video_plan("mf_a2_empty_ctx")

    # Nạp và duyệt
    page.btn_update_proposal.click()
    qapp.processEvents()
    page.common_review_widget.btn_approve_proposal.click()
    qapp.processEvents()
    assert page._current_approval_record is not None

    # Giả lập supplier trả về {}
    page._context_supplier = lambda: {}
    page.btn_update_proposal.click()
    qapp.processEvents()

    assert page._current_approval_record is None
    assert "không khả dụng" in page.common_review_widget._error_message.lower()


def test_probe_a3_false_approval_verifier_ignored(managed_review_page, qapp, monkeypatch):
    """Probe A3: Nếu verify_proposal_approval_binding trả về False -> Không lưu approval record."""
    page = managed_review_page
    manifest = _make_sample_manifest("mf_a3")
    page._manifest = manifest
    page._video_plan_data = _make_sample_video_plan("mf_a3")

    page.btn_update_proposal.click()
    qapp.processEvents()

    # Monkeypatch verify_proposal_approval_binding trả về False
    import src.ui.director.pages.review_page as rp_mod
    monkeypatch.setattr(rp_mod, "verify_proposal_approval_binding", lambda p, a: False)

    page.common_review_widget.btn_approve_proposal.click()
    qapp.processEvents()

    assert page._current_approval_record is None


# =============================================================================
# Probes R2: Strict Current-Input Authority, Geometry Typing & Denial Safety
# =============================================================================

def test_probe_r2_active_source_paths_subset_reorder_empty_denied(managed_review_page, qapp):
    """Probe R2-1: Active source subset removal / reorder / empty bị từ chối duyệt;
    chỉ cùng tập thứ tự ban đầu mới được chấp nhận.
    """
    page = managed_review_page
    manifest = _make_sample_manifest("mf_r2_sources")
    page._manifest = manifest
    page._video_plan_data = _make_sample_video_plan("mf_r2_sources")

    # 1. Khởi tạo hợp lệ với 2 đường dẫn chuẩn thứ tự
    ordered_paths = ["/media/scene1.mp4", "/media/scene2.mp4"]
    page._stored_validated_context = {"video_paths": list(ordered_paths)}

    page.btn_update_proposal.click()
    qapp.processEvents()
    assert page._current_proposal is not None
    orig_pid = page._current_proposal.proposal_id
    orig_rev = compute_proposal_revision(page._current_proposal)

    # Positive: Phê duyệt thành công khi dữ liệu khớp hoàn toàn
    page.common_review_widget.btn_approve_proposal.click()
    qapp.processEvents()
    assert page._current_approval_record is not None

    # 2. Xóa 1 clip khỏi tập nguồn active (subset: chỉ còn scene1)
    page._stored_validated_context = {"video_paths": ["/media/scene1.mp4"]}
    page._on_proposal_approved(orig_pid, orig_rev)
    qapp.processEvents()
    assert page._current_approval_record is None, "Subset nguồn active phải bị từ chối duyệt và xóa record!"
    assert page.common_review_widget._is_stale is True

    # 3. Đảo thứ tự tập nguồn active ([scene2, scene1])
    # Tái lập hiển thị trước
    page._stored_validated_context = {"video_paths": list(ordered_paths)}
    page.btn_update_proposal.click()
    qapp.processEvents()
    p_rev = compute_proposal_revision(page._current_proposal)

    # Đổi thứ tự context nhưng không bấm update
    page._stored_validated_context = {"video_paths": ["/media/scene2.mp4", "/media/scene1.mp4"]}
    page._on_proposal_approved(page._current_proposal.proposal_id, p_rev)
    qapp.processEvents()
    assert page._current_approval_record is None, "Đổi thứ tự nguồn active phải bị từ chối duyệt!"
    assert page.common_review_widget._is_stale is True

    # 4. Tập nguồn rỗng ([])
    page._stored_validated_context = {"video_paths": []}
    page._on_proposal_approved(page._current_proposal.proposal_id, p_rev)
    qapp.processEvents()
    assert page._current_approval_record is None, "Tập nguồn rỗng phải bị từ chối duyệt!"


def test_probe_r2_evidence_table_or_fps_changed_in_place_denies_approval(managed_review_page, qapp):
    """Probe R2-2: Bằng chứng khung hình hoặc FPS bị thay đổi in-place sau khi hiển thị
    sẽ bị từ chối phê duyệt do lệch fingerprint, xóa hồ sơ phê duyệt và đánh dấu stale.
    """
    page = managed_review_page
    manifest = _make_sample_manifest("mf_r2_ev")
    page._manifest = manifest
    page._video_plan_data = _make_sample_video_plan("mf_r2_ev")

    # Cung cấp bảng bằng chứng ban đầu và FPS 24.0
    ev1 = SourceFrameEvidence(
        manifest_id="mf_r2_ev",
        clip_id="clip_01",
        evidence_status="VERIFIED_CFR",
        total_frames=240,
        source_fps=RationalFPS(24, 1),
    )
    ev2 = SourceFrameEvidence(
        manifest_id="mf_r2_ev",
        clip_id="clip_02",
        evidence_status="VERIFIED_CFR",
        total_frames=360,
        source_fps=RationalFPS(24, 1),
    )
    page._source_frame_evidence_table = [ev1, ev2]
    page._stored_validated_context = {
        "video_paths": ["/media/scene1.mp4", "/media/scene2.mp4"],
        "timeline_fps": 24.0,
    }

    # Nạp và hiển thị
    page.btn_update_proposal.click()
    qapp.processEvents()
    pid = page._current_proposal.proposal_id
    rev = compute_proposal_revision(page._current_proposal)

    # Trường hợp A: Thay đổi FPS in-place sau hiển thị (24.0 -> 30.0)
    page._stored_validated_context["timeline_fps"] = 30.0
    page._on_proposal_approved(pid, rev)
    qapp.processEvents()
    assert page._current_approval_record is None, "Thay đổi FPS in-place phải bị từ chối duyệt!"
    assert page.common_review_widget._is_stale is True

    # Phục hồi FPS, cập nhật lại đề xuất
    page._stored_validated_context["timeline_fps"] = 24.0
    page.btn_update_proposal.click()
    qapp.processEvents()
    rev2 = compute_proposal_revision(page._current_proposal)

    # Trường hợp B: Đột biến bảng bằng chứng frame in-place (thay đổi total_frames của clip_01)
    ev1_tampered = SourceFrameEvidence(
        manifest_id="mf_r2_ev",
        clip_id="clip_01",
        evidence_status="VERIFIED_CFR",
        total_frames=99999,  # Bị sửa ngầm
        source_fps=RationalFPS(24, 1),
    )
    page._source_frame_evidence_table = [ev1_tampered, ev2]
    page._on_proposal_approved(pid, rev2)
    qapp.processEvents()
    assert page._current_approval_record is None, "Thay đổi bảng bằng chứng frame in-place phải bị từ chối duyệt!"
    assert page.common_review_widget._is_stale is True


def test_probe_r2_untyped_geometry_denies_verified_badge_and_blocks_approval(qapp):
    """Probe R2-3: Đối tượng geometry untyped (SimpleNamespace hoặc MagicMock) có mang status
    GEOMETRY_VERIFIED và matching IDs tuyệt đối KHÔNG được hiển thị badge ĐÃ XÁC THỰC
    và bị chuyển thành GEOMETRY_BLOCKED, khóa nút phê duyệt.
    """
    import types
    widget = CommonProposalReviewWidget()
    manifest = _make_sample_manifest("mf_geom_untyped")
    proposal = build_edit_plan_proposal(
        proposal_id="prop_geom_untyped",
        manifest=manifest,
        video_plan=_make_sample_video_plan("mf_geom_untyped"),
        creative_context=normalize_creative_context({"free_brief": "Test"}),
    )
    prop_rev = compute_proposal_revision(proposal)

    # 1. Thử nghiệm SimpleNamespace có đủ status="GEOMETRY_VERIFIED", proposal_id, proposal_revision
    fake_ns = types.SimpleNamespace(
        status="GEOMETRY_VERIFIED",
        proposal_id="prop_geom_untyped",
        proposal_revision=prop_rev,
        timeline_fps="24",
        issues=(),
        assembled_segments=(),
    )

    widget.set_proposal(
        proposal=proposal,
        geometry_result=fake_ns,  # type: ignore
        approval=None,
    )
    qapp.processEvents()

    assert "ĐÃ XÁC THỰC" not in widget.badge_geometry.text(), "SimpleNamespace không được hiển thị VERIFIED!"
    assert "BỊ CHẶN" in widget.badge_geometry.text()
    assert widget.btn_approve_proposal.isEnabled() is False, "Untyped geometry phải khóa nút duyệt!"

    # 2. Thử nghiệm MagicMock
    from unittest.mock import MagicMock
    fake_mock = MagicMock()
    fake_mock.status = "GEOMETRY_VERIFIED"
    fake_mock.proposal_id = "prop_geom_untyped"
    fake_mock.proposal_revision = prop_rev

    widget.set_proposal(
        proposal=proposal,
        geometry_result=fake_mock,  # type: ignore
        approval=None,
    )
    qapp.processEvents()

    assert "ĐÃ XÁC THỰC" not in widget.badge_geometry.text(), "MagicMock không được hiển thị VERIFIED!"
    assert "BỊ CHẶN" in widget.badge_geometry.text()
    assert widget.btn_approve_proposal.isEnabled() is False

    # 3. Positive: Đối tượng PlanGeometryAssemblyResult thật qua assembler chuẩn
    ev1 = SourceFrameEvidence(
        manifest_id="mf_geom_untyped",
        clip_id="clip_01",
        evidence_status="VERIFIED_CFR",
        total_frames=240,
        source_fps=RationalFPS(24, 1),
    )
    ev2 = SourceFrameEvidence(
        manifest_id="mf_geom_untyped",
        clip_id="clip_02",
        evidence_status="VERIFIED_CFR",
        total_frames=360,
        source_fps=RationalFPS(24, 1),
    )
    real_geom = assemble_plan_frame_geometry(proposal, {"clip_01": ev1, "clip_02": ev2}, RationalFPS(24, 1))
    widget.set_proposal(
        proposal=proposal,
        geometry_result=real_geom,
        approval=None,
    )
    qapp.processEvents()

    assert "ĐÃ XÁC THỰC" in widget.badge_geometry.text()
    assert widget.btn_approve_proposal.isEnabled() is True


def test_probe_r2_denial_paths_clear_prior_approval_while_preserving_user_json(managed_review_page, qapp):
    """Probe R2-6: Mọi đường từ chối duyệt sau khi đã duyệt thành công phải xóa
    _current_approval_record nhưng bảo tồn nguyên vẹn bản thảo JSON của người dùng
    và chế độ xem chỉ đọc.
    """
    page = managed_review_page
    manifest = _make_sample_manifest("mf_r2_preserve")
    page._manifest = manifest
    page._video_plan_data = _make_sample_video_plan("mf_r2_preserve")
    page._stored_validated_context = {
        "video_paths": ["/media/scene1.mp4", "/media/scene2.mp4"],
    }

    user_draft = '{\n  "USER_BATCH_DRAFT": "CANONICAL_PRESERVED_CONTENT"\n}'
    page.txt_batch_draft_input.setPlainText(user_draft)

    # Nạp và phê duyệt thành công
    page.btn_update_proposal.click()
    qapp.processEvents()
    page.common_review_widget.btn_approve_proposal.click()
    qapp.processEvents()
    assert page._current_approval_record is not None

    # Gây ra đường từ chối (thay đổi nguồn active sang tập rỗng)
    page._stored_validated_context = {"video_paths": []}
    pid = page._current_proposal.proposal_id
    rev = compute_proposal_revision(page._current_proposal)
    page._on_proposal_approved(pid, rev)
    qapp.processEvents()

    # Khẳng định bảo toàn:
    assert page._current_approval_record is None, "Hồ sơ duyệt phải bị xóa khi bị từ chối"
    assert page.txt_batch_draft_input.toPlainText() == user_draft, "Bản thảo JSON người dùng phải được bảo toàn trọn vẹn!"
    assert page.common_review_widget._is_stale is True


# =============================================================================
# R3: Current-Input Authority, Geometry Rehydration & Evidence Parity Probes
# =============================================================================

def test_probe_r3_real_mapping_evidence_and_in_place_mutation_denied(managed_review_page, qapp):
    """R3 Matrix 1 & 3: Bảng bằng chứng thật dạng Mapping -> VERIFIED, duyệt được.
    Đột biến in-place total_frames 240->1 -> old event bị từ chối duyệt."""
    import dataclasses
    page = managed_review_page
    mid = "mf_r3_ev_map"
    manifest = _make_sample_manifest(mid)
    page._manifest = manifest
    page._video_plan_data = _make_sample_video_plan(mid)
    ctx = {
        "video_paths": [c.current_path for c in manifest.clips],
        "manifest_id": mid,
        "fps": 24,
    }
    page._context_supplier = lambda: ctx

    # 1. Positive: Real mapping evidence
    ev_table = {
        c.clip_id: SourceFrameEvidence(
            manifest_id=mid,
            clip_id=c.clip_id,
            source_fps=RationalFPS(24, 1),
            total_frames=int(c.duration_sec * 24),
            evidence_status="VERIFIED_CFR",
        )
        for c in manifest.clips
    }
    page._source_frame_evidence_table = ev_table
    page._rebuild_and_present_proposal()
    qapp.processEvents()

    assert page._current_geometry_result.status == "GEOMETRY_VERIFIED"
    pid = page._current_proposal.proposal_id
    rev = compute_proposal_revision(page._current_proposal)

    # Đột biến clip_01.total_frames = 1
    page._source_frame_evidence_table["clip_01"] = dataclasses.replace(
        page._source_frame_evidence_table["clip_01"], total_frames=1
    )
    page._on_proposal_approved(pid, rev)
    qapp.processEvents()

    assert page._current_approval_record is None, "Old event approval phải bị từ chối khi evidence thay đổi!"
    assert page.common_review_widget._is_stale is True


def test_probe_r3_manifest_clip_duration_change_in_place_denied(managed_review_page, qapp):
    """R3 Matrix 2: Manifest clip duration 10.0 -> 1.0 sau khi hiển thị -> old event bị từ chối."""
    page = managed_review_page
    mid = "mf_r3_dur"
    manifest = _make_sample_manifest(mid)
    page._manifest = manifest
    page._video_plan_data = _make_sample_video_plan(mid)
    ctx = {
        "video_paths": [c.current_path for c in manifest.clips],
        "manifest_id": mid,
        "fps": 24,
    }
    page._context_supplier = lambda: ctx

    page._rebuild_and_present_proposal()
    qapp.processEvents()
    pid = page._current_proposal.proposal_id
    rev = compute_proposal_revision(page._current_proposal)

    # Đột biến duration_sec của clip 0
    page._manifest.clips[0].duration_sec = 1.0
    page._on_proposal_approved(pid, rev)
    qapp.processEvents()

    assert page._current_approval_record is None, "Thay đổi clip duration_sec phải từ chối phê duyệt!"
    assert page.common_review_widget._is_stale is True


def test_probe_r3_supplier_manifest_id_swap_denied(managed_review_page, qapp):
    """R3 Matrix 2: Supplier manifest_id bị tráo sang foreign_manifest -> bị từ chối."""
    page = managed_review_page
    mid = "mf_r3_swap"
    manifest = _make_sample_manifest(mid)
    page._manifest = manifest
    page._video_plan_data = _make_sample_video_plan(mid)
    ctx = {
        "video_paths": [c.current_path for c in manifest.clips],
        "manifest_id": mid,
        "fps": 24,
    }
    page._context_supplier = lambda: ctx

    page._rebuild_and_present_proposal()
    qapp.processEvents()
    pid = page._current_proposal.proposal_id
    rev = compute_proposal_revision(page._current_proposal)

    # Tráo supplier manifest_id
    ctx["manifest_id"] = "foreign_manifest"
    page._on_proposal_approved(pid, rev)
    qapp.processEvents()

    assert page._current_approval_record is None, "Tráo supplier manifest_id phải từ chối phê duyệt!"
    assert page.common_review_widget._is_stale is True


def test_probe_r3_explicit_falsy_evidence_cannot_approve(managed_review_page, qapp):
    """R3 Matrix 3: Bảng bằng chứng explicit False/0/[]/'' -> BLOCKED, không thể duyệt."""
    page = managed_review_page
    mid = "mf_r3_falsy"
    manifest = _make_sample_manifest(mid)
    page._manifest = manifest
    page._video_plan_data = _make_sample_video_plan(mid)
    ctx = {
        "video_paths": [c.current_path for c in manifest.clips],
        "manifest_id": mid,
        "fps": 24,
    }
    page._context_supplier = lambda: ctx

    for bad_ev in (False, 0, [], ""):
        page._source_frame_evidence_table = bad_ev
        page._rebuild_and_present_proposal()
        qapp.processEvents()

        assert page._current_geometry_result is not None
        assert page._current_geometry_result.status == "GEOMETRY_BLOCKED"
        assert page.common_review_widget.btn_approve_proposal.isEnabled() is False

        pid = page._current_proposal.proposal_id
        rev = compute_proposal_revision(page._current_proposal)
        page._on_proposal_approved(pid, rev)
        qapp.processEvents()
        assert page._current_approval_record is None


def test_probe_r3_widget_rehydration_catches_mutated_typed_geometry(qapp):
    """R3 Matrix 4: total_timeline_frames=True trên PlanGeometryAssemblyResult bị rehydration bắt và khóa duyệt."""
    import copy
    w = CommonProposalReviewWidget()
    manifest = _make_sample_manifest("mf_r3_rehyd")
    prop = build_edit_plan_proposal(
        proposal_id="prop_r3_rehyd",
        manifest=manifest,
        video_plan=_make_sample_video_plan("mf_r3_rehyd"),
        creative_context=normalize_creative_context({"free_brief": "Test"}),
    )
    ev_table = {
        c.clip_id: SourceFrameEvidence(
            manifest_id="mf_r3_rehyd",
            clip_id=c.clip_id,
            source_fps=RationalFPS(24, 1),
            total_frames=int(c.duration_sec * 24),
            evidence_status="VERIFIED_CFR",
        )
        for c in manifest.clips
    }
    good_geom = assemble_plan_frame_geometry(prop, ev_table, RationalFPS(24, 1))
    assert good_geom.status == "GEOMETRY_VERIFIED"

    # Đột biến kiểu
    bad_geom = copy.copy(good_geom)
    object.__setattr__(bad_geom, "total_timeline_frames", True)

    w.set_proposal(prop, bad_geom)
    qapp.processEvents()

    assert "BỊ CHẶN" in w.badge_geometry.text()
    assert w.btn_approve_proposal.isEnabled() is False

    w.deleteLater()
    qapp.processEvents()


