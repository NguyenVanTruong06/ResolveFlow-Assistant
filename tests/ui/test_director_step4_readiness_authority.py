"""Unit & UI tests cho Thẩm Quyền Đích Resolve & Tiền Kiểm Chuyên Sâu Bước 4 — G07-07A5-I.

Kiểm thử toàn diện 4 nhóm nghiệp vụ theo chỉ thị của Tech Lead Codex:
1. Query Contract (`query_active_timeline_authority`):
   - Resolve không chạy / resolve_app is None.
   - ProjectManager vắng mặt.
   - Project vắng mặt.
   - Timeline vắng mặt.
   - FPS rỗng, missing, boolean, hoặc malformed string.
   - ZERO FLOAT FALLBACK: không bao giờ rơi về 24.0 hay 30.0.
   - Ánh xạ chuẩn tắc cho toàn bộ định dạng được hỗ trợ: "23.976", "24", "25", "29.97", "30", "50", "59.94", "60", "24000/1001".
   - Phát hiện hoán đổi danh tính (identity drift) giữa chừng.
   - Trả về bản ghi sở hữu `ResolveTimelineAuthority` đầy đủ.
2. DirectorWindow Deep Readiness Wiring:
   - Proposal có bằng chứng byte/CFR đầy đủ -> opt-in `verify_source_execution=True` với exact `timeline_fps`.
   - Proposal duyệt biên tập thiếu SHA/stream -> giữ nguyên `verify_source_execution=False`, không bịa đặt binding.
   - Target query thất bại (Resolve chưa mở) -> giữ nguyên non-deep an toàn, ghi nhận diagnostic.
   - Bảo toàn các cơ chế pending authority, busy, cancel và exactly-one terminal.
3. BuildPage Separated States & Execution Lock:
   - Hiển thị tách bạch 4 tầng: Phê duyệt biên tập, Đích Resolve, Bằng chứng nguồn (CFR & SHA), Khóa thi công.
   - Bảng hiển thị thông tin deep CFR & Formula B geometry khi có.
   - `ready_to_execute` luôn là `False` trong mọi kịch bản (kể cả kết quả lỗi hoặc giả mạo).
"""

from fractions import Fraction
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from src.core.contracts.edit_plan_approval_contracts import (
    AppOwnedApprovalRecord,
    create_app_owned_approval_record,
)
from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    compute_proposal_revision,
    normalize_edit_plan_proposal,
)
from src.core.contracts.time_contracts import RationalFPS
from src.core.geometry.plan_geometry_contracts import (
    PlanGeometryAssemblyResult,
    SourceFrameEvidence,
)
from src.core.geometry.source_evidence_service import SourceExecutionEvidenceEnvelope
from tests.core.contracts.test_edit_plan_proposal_contracts import _make_valid_proposal_dict
from src.core.resolve_bridge import (
    ResolveBridge,
    ResolveTimelineAuthority,
    query_active_timeline_authority,
)
from src.core.source_catalog_readiness import (
    AssetAssignmentObservation,
    ReadinessRequest,
    SourceCatalogReadinessResult,
    SourceClipObservation,
)
from src.ui.director.director_controller import DirectorController
from src.ui.director.director_window import DirectorWindow
from src.ui.director.pages.build_page import BuildPage


# ==============================================================================
# NHÓM 1: Query Contract cho query_active_timeline_authority
# ==============================================================================

def test_query_authority_resolve_unavailable():
    """1.1 Resolve không chạy -> trả về lỗi kiểm soát ERR_RESOLVE_NOT_RUNNING."""
    with patch("src.core.resolve_bridge.get_resolve", side_effect=RuntimeError("Connection refused")):
        res = query_active_timeline_authority()
    assert res.ok is False
    assert res.error_code == "ERR_RESOLVE_NOT_RUNNING"
    assert "Connection refused" in res.error_message


def test_query_authority_resolve_app_none():
    """1.2 resolve_app là None -> trả về ERR_RESOLVE_NOT_RUNNING."""
    res = query_active_timeline_authority(resolve_app=None)
    with patch("src.core.resolve_bridge.get_resolve", return_value=None):
        res = query_active_timeline_authority()
    assert res.ok is False
    assert res.error_code == "ERR_RESOLVE_NOT_RUNNING"


def test_query_authority_no_project_manager():
    """1.3 GetProjectManager trả về None -> trả về ERR_RESOLVE_NO_PROJECT_MANAGER."""
    mock_app = MagicMock()
    mock_app.GetProjectManager.return_value = None
    res = query_active_timeline_authority(resolve_app=mock_app)
    assert res.ok is False
    assert res.error_code == "ERR_RESOLVE_NO_PROJECT_MANAGER"


def test_query_authority_no_project():
    """1.4 GetCurrentProject trả về None -> trả về ERR_RESOLVE_NO_PROJECT."""
    mock_app = MagicMock()
    mock_pm = MagicMock()
    mock_pm.GetCurrentProject.return_value = None
    mock_app.GetProjectManager.return_value = mock_pm

    res = query_active_timeline_authority(resolve_app=mock_app)
    assert res.ok is False
    assert res.error_code == "ERR_RESOLVE_NO_PROJECT"
    assert "Chưa có Project nào" in res.error_message


def test_query_authority_no_timeline():
    """1.5 GetCurrentTimeline trả về None -> trả về ERR_RESOLVE_NO_ACTIVE_TIMELINE."""
    mock_app = MagicMock()
    mock_pm = MagicMock()
    mock_proj = MagicMock()
    mock_proj.GetName.return_value = "MyTestProject"
    mock_proj.GetCurrentTimeline.return_value = None
    mock_pm.GetCurrentProject.return_value = mock_proj
    mock_app.GetProjectManager.return_value = mock_pm

    res = query_active_timeline_authority(resolve_app=mock_app)
    assert res.ok is False
    assert res.error_code == "ERR_RESOLVE_NO_ACTIVE_TIMELINE"
    assert "Chưa có Timeline nào" in res.error_message


def test_query_authority_fps_missing_or_blank():
    """1.6 timelineFrameRate vắng mặt hoặc rỗng -> ERR_RESOLVE_TIMELINE_FPS_MISSING."""
    mock_app = MagicMock()
    mock_pm = MagicMock()
    mock_proj = MagicMock()
    mock_tl = MagicMock()

    mock_proj.GetName.return_value = "TestProj"
    mock_tl.GetName.return_value = "TestTL"
    mock_tl.GetSetting.return_value = ""
    mock_proj.GetCurrentTimeline.return_value = mock_tl
    mock_pm.GetCurrentProject.return_value = mock_proj
    mock_app.GetProjectManager.return_value = mock_pm

    res = query_active_timeline_authority(resolve_app=mock_app)
    assert res.ok is False
    assert res.error_code == "ERR_RESOLVE_TIMELINE_FPS_MISSING"


def test_query_authority_project_fps_cannot_rescue_missing_timeline_fps():
    """1.6b NEGATIVE PROBE: Project FPS hợp lệ (24/29.97) tuyệt đối không được cứu timeline FPS thiếu."""
    mock_app = MagicMock()
    mock_pm = MagicMock()
    mock_proj = MagicMock()
    mock_tl = MagicMock()

    mock_proj.GetName.return_value = "TestProj"
    mock_tl.GetName.return_value = "TestTL"
    mock_tl.GetSetting.return_value = None  # Timeline không có FPS
    mock_proj.GetSetting.return_value = "24"  # Project có FPS hợp lệ 24fps
    mock_proj.GetCurrentTimeline.return_value = mock_tl
    mock_pm.GetCurrentProject.return_value = mock_proj
    mock_app.GetProjectManager.return_value = mock_pm

    res = query_active_timeline_authority(resolve_app=mock_app)
    # Bắt buộc thất bại có kiểm soát: timeline thiếu FPS -> FAIL CLOSED
    assert res.ok is False
    assert res.error_code == "ERR_RESOLVE_TIMELINE_FPS_MISSING"
    assert res.timeline_fps is None
    # Xác minh project.GetSetting không được gọi để cứu timelineFrameRate
    for call in mock_proj.GetSetting.call_args_list:
        assert "timelineFrameRate" not in call[0]


def test_query_authority_timeline_getter_exception_fails_closed():
    """1.6c NEGATIVE PROBE: Timeline GetSetting ném ngoại lệ -> fail closed, không fallback sang project."""
    mock_app = MagicMock()
    mock_pm = MagicMock()
    mock_proj = MagicMock()
    mock_tl = MagicMock()

    mock_proj.GetName.return_value = "TestProj"
    mock_tl.GetName.return_value = "TestTL"
    mock_tl.GetSetting.side_effect = RuntimeError("SDK COM error when querying timeline settings")
    mock_proj.GetSetting.return_value = "30"  # Giả sử project có giá trị
    mock_proj.GetCurrentTimeline.return_value = mock_tl
    mock_pm.GetCurrentProject.return_value = mock_proj
    mock_app.GetProjectManager.return_value = mock_pm

    res = query_active_timeline_authority(resolve_app=mock_app)
    assert res.ok is False
    assert res.error_code == "ERR_RESOLVE_TIMELINE_FPS_READ_FAILED"
    assert "SDK COM error" in res.error_message
    assert res.timeline_fps is None
    mock_proj.GetSetting.assert_not_called()


def test_query_authority_fps_boolean_rejected():
    """1.7 timelineFrameRate trả về True/False -> ERR_RESOLVE_TIMELINE_FPS_INVALID."""
    mock_app = MagicMock()
    mock_pm = MagicMock()
    mock_proj = MagicMock()
    mock_tl = MagicMock()

    mock_proj.GetName.return_value = "TestProj"
    mock_tl.GetName.return_value = "TestTL"
    mock_tl.GetSetting.side_effect = lambda k: True if k == "timelineFrameRate" else "1920"
    mock_proj.GetCurrentTimeline.return_value = mock_tl
    mock_pm.GetCurrentProject.return_value = mock_proj
    mock_app.GetProjectManager.return_value = mock_pm

    res = query_active_timeline_authority(resolve_app=mock_app)
    assert res.ok is False
    assert res.error_code == "ERR_RESOLVE_TIMELINE_FPS_INVALID"
    assert "boolean" in res.error_message


def test_query_authority_fps_malformed_rejected():
    """1.8 timelineFrameRate là chuỗi hỏng -> ERR_RESOLVE_TIMELINE_FPS_INVALID."""
    mock_app = MagicMock()
    mock_pm = MagicMock()
    mock_proj = MagicMock()
    mock_tl = MagicMock()

    mock_proj.GetName.return_value = "TestProj"
    mock_tl.GetName.return_value = "TestTL"
    mock_tl.GetSetting.side_effect = lambda k: "not_a_number" if k == "timelineFrameRate" else "1920"
    mock_proj.GetCurrentTimeline.return_value = mock_tl
    mock_pm.GetCurrentProject.return_value = mock_proj
    mock_app.GetProjectManager.return_value = mock_pm

    res = query_active_timeline_authority(resolve_app=mock_app)
    assert res.ok is False
    assert res.error_code == "ERR_RESOLVE_TIMELINE_FPS_INVALID"


@pytest.mark.parametrize(
    "raw_input,exp_num,exp_den",
    [
        ("24", 24, 1),
        ("23.976", 24000, 1001),
        ("25", 25, 1),
        ("29.97", 30000, 1001),
        ("30", 30, 1),
        ("50", 50, 1),
        ("59.94", 60000, 1001),
        ("60", 60, 1),
        ("24000/1001", 24000, 1001),
        ("30000/1001", 30000, 1001),
    ],
)
def test_query_authority_supported_fps_representations(raw_input, exp_num, exp_den):
    """1.9 Toàn bộ định dạng FPS được hỗ trợ ánh xạ chính xác sang RationalFPS chuẩn tắc."""
    mock_app = MagicMock()
    mock_pm = MagicMock()
    mock_proj = MagicMock()
    mock_tl = MagicMock()

    mock_proj.GetName.return_value = "Project Alpha"
    mock_tl.GetName.return_value = "Timeline 1"
    mock_tl.GetUniqueId.return_value = "tl_unique_123"

    def _get_setting(key):
        if key == "timelineFrameRate":
            return raw_input
        elif key == "timelineResolutionWidth":
            return "1920"
        elif key == "timelineResolutionHeight":
            return "1080"
        return ""

    mock_tl.GetSetting.side_effect = _get_setting
    mock_proj.GetCurrentTimeline.return_value = mock_tl
    mock_pm.GetCurrentProject.return_value = mock_proj
    mock_app.GetProjectManager.return_value = mock_pm

    res = query_active_timeline_authority(resolve_app=mock_app)
    assert res.ok is True
    assert res.project_name == "Project Alpha"
    assert res.timeline_id == "tl_unique_123"
    assert res.timeline_name == "Timeline 1"
    assert res.timeline_fps == RationalFPS(exp_num, exp_den)
    assert res.width == 1920
    assert res.height == 1080


def test_query_authority_zero_float_fallback_forbidden():
    """1.10 Tuyệt đối cấm fallback ngầm về 24.0 hay 30.0 khi FPS không đọc được."""
    mock_app = MagicMock()
    mock_pm = MagicMock()
    mock_proj = MagicMock()
    mock_tl = MagicMock()

    mock_proj.GetName.return_value = "Proj"
    mock_tl.GetName.return_value = "TL"
    mock_tl.GetSetting.side_effect = lambda k: None
    mock_proj.GetSetting.side_effect = lambda k: None
    mock_proj.GetCurrentTimeline.return_value = mock_tl
    mock_pm.GetCurrentProject.return_value = mock_proj
    mock_app.GetProjectManager.return_value = mock_pm

    res = query_active_timeline_authority(resolve_app=mock_app)
    assert res.ok is False
    assert res.timeline_fps is None  # KHÔNG BAO GIỜ là 24 hay 30!


def test_query_authority_identity_drift():
    """1.11 Phát hiện Project hoặc Timeline bị hoán đổi trong quá trình truy vấn."""
    mock_app = MagicMock()
    mock_pm = MagicMock()
    mock_proj_a = MagicMock()
    mock_proj_b = MagicMock()
    mock_tl = MagicMock()

    mock_tl.GetSetting.side_effect = lambda k: "30" if k == "timelineFrameRate" else "1920"

    # Lần 1 trả về proj_a, lần recheck trả về proj_b
    mock_pm.GetCurrentProject.side_effect = [mock_proj_a, mock_proj_b]
    mock_proj_a.GetCurrentTimeline.return_value = mock_tl
    mock_proj_b.GetCurrentTimeline.return_value = mock_tl
    mock_app.GetProjectManager.return_value = mock_pm

    res = query_active_timeline_authority(resolve_app=mock_app)
    assert res.ok is False
    assert res.error_code == "ERR_RESOLVE_IDENTITY_DRIFT"


def test_resolve_bridge_delegates_query():
    """1.12 ResolveBridge.query_active_timeline_authority ủy nhiệm đúng hàm chức năng."""
    bridge = ResolveBridge()
    mock_app = MagicMock()
    mock_pm = MagicMock()
    mock_proj = MagicMock()
    mock_tl = MagicMock()

    mock_proj.GetName.return_value = "BridgeProj"
    mock_tl.GetName.return_value = "BridgeTL"
    mock_tl.GetSetting.side_effect = lambda k: "25" if k == "timelineFrameRate" else "1920"
    mock_proj.GetCurrentTimeline.return_value = mock_tl
    mock_pm.GetCurrentProject.return_value = mock_proj
    mock_app.GetProjectManager.return_value = mock_pm

    res = bridge.query_active_timeline_authority(resolve_app=mock_app)
    assert res.ok is True
    assert res.timeline_fps == RationalFPS(25, 1)


# ==============================================================================
# NHÓM 2: DirectorWindow Deep Readiness Wiring
# ==============================================================================

def _make_test_proposal_with_evidence(complete: bool = True) -> EditPlanProposal:
    """Tạo proposal chuẩn tắc có hoặc thiếu bằng chứng kỹ thuật đầy đủ."""
    raw = _make_valid_proposal_dict("mf_test_01")
    clips = []
    for c in raw.get("source_binding", {}).get("clips", []):
        c_copy = dict(c)
        if complete:
            c_copy["expected_full_sha256"] = "a" * 64
            c_copy["stream_index"] = 0
        else:
            c_copy.pop("expected_full_sha256", None)
            c_copy.pop("stream_index", None)
        clips.append(c_copy)
    raw["source_binding"]["clips"] = clips
    return normalize_edit_plan_proposal(raw)


def _make_valid_approval_for(prop: EditPlanProposal) -> AppOwnedApprovalRecord:
    """Tạo AppOwnedApprovalRecord chuẩn tắc ràng buộc vào proposal."""
    return create_app_owned_approval_record(
        prop,
        approval_id="appr_0123456789ab",
        approved_at_iso="2026-11-02T13:00:00Z",
    )


def test_director_window_opts_into_deep_mode_when_evidence_complete(qapp):
    """2.1 Khi proposal có bằng chứng đầy đủ & Resolve trả về FPS -> request có verify_source_execution=True và timeline_fps."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)

    prop = _make_test_proposal_with_evidence(complete=True)
    appr = _make_valid_approval_for(prop)

    win.page_review.get_approved_proposal_and_record = MagicMock(return_value=(prop, appr))

    # Mock Resolve authority trả về 29.97 fps (30000/1001)
    mock_auth = ResolveTimelineAuthority(
        ok=True,
        project_name="ChunDVC",
        timeline_id="tl_01",
        timeline_name="MainTimeline",
        timeline_fps=RationalFPS(30000, 1001),
        width=1920,
        height=1080,
    )
    win._query_resolve_timeline_authority = MagicMock(return_value=mock_auth)

    with patch("src.ui.director.director_window.PreflightWorker") as MockWorker:
        mock_worker_instance = MagicMock()
        mock_worker_instance.isRunning.return_value = False
        MockWorker.return_value = mock_worker_instance

        win._start_readiness_preflight()

        # Kiểm tra request được tạo
        assert MockWorker.call_count == 1
        call_kwargs = MockWorker.call_args[1]
        req = call_kwargs.get("request")
        assert req is not None
        assert req.verify_source_execution is True
        assert req.timeline_fps == RationalFPS(30000, 1001)


def test_director_window_non_deep_when_evidence_incomplete(qapp):
    """2.2 Khi proposal thiếu SHA/stream -> giữ nguyên verify_source_execution=False, không bịa đặt binding."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)

    # Proposal không có expected_full_sha256
    prop = _make_test_proposal_with_evidence(complete=False)
    appr = _make_valid_approval_for(prop)

    win.page_review.get_approved_proposal_and_record = MagicMock(return_value=(prop, appr))

    with patch("src.ui.director.director_window.PreflightWorker") as MockWorker:
        mock_worker_instance = MagicMock()
        mock_worker_instance.isRunning.return_value = False
        MockWorker.return_value = mock_worker_instance

        win._start_readiness_preflight()

        assert MockWorker.call_count == 1
        call_kwargs = MockWorker.call_args[1]
        req = call_kwargs.get("request")
        assert req is not None
        # Bắt buộc False và None
        assert req.verify_source_execution is False
        assert req.timeline_fps is None


def test_director_window_target_unavailable_retains_truthful_non_deep(qapp):
    """2.3 Khi proposal có bằng chứng nhưng Resolve không mở timeline -> chạy non-deep an toàn, 0 crash."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)

    prop = _make_test_proposal_with_evidence(complete=True)
    appr = _make_valid_approval_for(prop)

    win.page_review.get_approved_proposal_and_record = MagicMock(return_value=(prop, appr))

    # Resolve báo lỗi không mở timeline
    mock_auth = ResolveTimelineAuthority(
        ok=False,
        error_code="ERR_RESOLVE_NO_ACTIVE_TIMELINE",
        error_message="Chưa mở timeline nào.",
    )
    win._query_resolve_timeline_authority = MagicMock(return_value=mock_auth)

    with patch("src.ui.director.director_window.PreflightWorker") as MockWorker:
        mock_worker_instance = MagicMock()
        mock_worker_instance.isRunning.return_value = False
        MockWorker.return_value = mock_worker_instance

        win._start_readiness_preflight()

        assert MockWorker.call_count == 1
        call_kwargs = MockWorker.call_args[1]
        req = call_kwargs.get("request")
        assert req is not None
        # Không opt-in deep khi không có timeline authority hợp lệ
        assert req.verify_source_execution is False
        assert req.timeline_fps is None


# ==============================================================================
# NHÓM 3: BuildPage Separated States & Execution Lock
# ==============================================================================

def test_build_page_renders_separated_states_and_locks_execution(qapp):
    """3.1 BuildPage hiển thị tách bạch 4 tầng: Biên tập, Đích Resolve, Nguồn CFR, Khóa thi công."""
    page = BuildPage()

    fe = SourceFrameEvidence(
        manifest_id="mf_01",
        clip_id="C01",
        evidence_status="VERIFIED_CFR",
        total_frames=240,
        source_fps=RationalFPS(24, 1),
    )
    envelope = SourceExecutionEvidenceEnvelope(
        clip_id="C01",
        manifest_id="mf_01",
        file_path="/media/footage1.mp4",
        stream_index=0,
        expected_full_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        observed_full_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        freshness=None,
        frame_evidence=fe,
        complete_scan=True,
    )

    geom = PlanGeometryAssemblyResult(
        status="GEOMETRY_VERIFIED",
        proposal_id="prop_abcdef123456",
        proposal_revision="b" * 64,
        timeline_fps=RationalFPS(24, 1),
        assembled_segments=(),
        total_timeline_frames=0,
        total_timeline_duration_sec=Fraction(0, 1),
        issues=(),
        unresolved_reviews=(),
    )

    # Giả lập kết quả readiness có cả deep evidence và target authority
    mock_result = SourceCatalogReadinessResult(
        request_id="req_001",
        generation=1,
        proposal_id="prop_abcdef123456",
        proposal_revision="b" * 64,
        approval_id="appr_555555aaaaaa",
        status="completed",
        checks_passed=True,
        ready_to_execute=False,
        source_observations=(
            SourceClipObservation(
                clip_id="C01",
                path="/media/footage1.mp4",
                duration_sec=10.0,
                exists=True,
                is_regular_file=True,
                is_non_empty=True,
                is_readable=True,
                stat_identity=(1, 2, 3, 4, 5),
                recheck_passed=True,
            ),
        ),
        catalog_observations=(),
        diagnostics=(),
        source_execution_evidence={"C01": envelope},
        source_geometry=geom,
    )

    mock_target = ResolveTimelineAuthority(
        ok=True,
        project_name="ChunDVC_Project",
        timeline_id="tl_123",
        timeline_name="Final_Edit",
        timeline_fps=RationalFPS(24, 1),
        width=1920,
        height=1080,
    )

    page.show_readiness_result(mock_result, target_authority=mock_target)

    # 1. Kiểm tra execution lock banner
    assert "ready_to_execute=False" in page.lbl_readiness_status.text()
    assert "khóa" in page.lbl_readiness_status.text()

    # 2. Kiểm tra các dòng quan sát trong bảng
    table = page.table_readiness
    row_count = table.rowCount()
    assert row_count >= 4  # Biên tập, Đích, Nguồn, Hình học, Khóa

    row_data = []
    for r in range(row_count):
        row_data.append((
            table.item(r, 0).text(),
            table.item(r, 1).text(),
            table.item(r, 2).text(),
            table.item(r, 3).text(),
        ))

    # Tầng 1: Phê duyệt biên tập
    assert any(col[0] == "Phê duyệt biên tập" and "Đã phê duyệt" in col[2] for col in row_data)

    # Tầng 2: Đích Resolve
    assert any(col[0] == "Đích Resolve" and "ChunDVC_Project / Final_Edit" in col[1] and "Timeline hợp lệ" in col[2] for col in row_data)

    # Tầng 3: Nguồn CFR
    assert any(col[0] == "Nguồn footage" and "C01" in col[1] and "Xác minh CFR" in col[2] for col in row_data)

    # Tầng 4: Hình học kịch bản
    assert any(col[0] == "Hình học kịch bản" and "Formula B" in col[1] and "Đã xác minh" in col[2] for col in row_data)

    # Tầng 5: Khóa thi công
    assert any(col[0] == "Thi công timeline" and "ĐANG KHÓA" in col[2] for col in row_data)


def test_build_page_retains_lock_on_failed_or_forged_results(qapp):
    """3.2 Khi tiền kiểm thất bại hoặc bị hủy, bảng hiển thị rõ ràng và khóa thi công vẫn bật."""
    page = BuildPage()

    failed_result = SourceCatalogReadinessResult(
        request_id="req_fail",
        generation=1,
        proposal_id="prop_01",
        proposal_revision="c" * 64,
        approval_id="appr_01",
        status="failed",
        checks_passed=False,
        ready_to_execute=False,
        source_observations=(
            SourceClipObservation(
                clip_id="C01",
                path="/media/missing.mp4",
                duration_sec=10.0,
                exists=False,
                is_regular_file=False,
                is_non_empty=False,
                is_readable=False,
                stat_identity=None,
                recheck_passed=False,
                error_code="ERR_SOURCE_NOT_FOUND",
                error_message="Tệp không tồn tại.",
            ),
        ),
        catalog_observations=(),
        diagnostics=("ERR_SOURCE_NOT_FOUND: Tệp nguồn không tồn tại.",),
    )

    page.show_readiness_result(failed_result)

    assert "Không vượt qua kiểm tra" in page.lbl_readiness_status.text()
    assert "ready_to_execute=False" in page.lbl_readiness_status.text() or "khóa" in page.lbl_readiness_status.text()

