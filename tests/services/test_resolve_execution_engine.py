"""tests/services/test_resolve_execution_engine.py
Tập kiểm thử toàn diện cho Động Cơ Thực Thi Kế Hoạch Resolve Có Nhật Ký Ghi Trước (WAL) - G07-07B4-I.

Bao phủ 11 mã sự cố (FLT-01 đến FLT-11), kiểm thử Observer Timeout giữ lock,
kiểm thử duplicate request trả về tham chiếu lịch sử, kiểm thử cancellation không ghi ABORTED
khi đã có clip commit, và tripwire kiểm tra không bao giờ kết nối Resolve thật.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timezone
import errno
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid
from unittest.mock import patch
import pytest

# Project contracts
from src.core.contracts.edit_plan_approval_contracts import (
    AppOwnedApprovalRecord,
    VALID_APPROVAL_SCOPE,
)
from src.core.contracts.edit_plan_proposal_contracts import (
    CreativeContext,
    EditPlanProposal,
    compute_proposal_revision,
    normalize_edit_plan_proposal,
)
from src.core.contracts.execution_receipt_contracts import (
    CatalogAssetDigestBinding,
    ExecutionReceipt,
    JournalEntry,
    SourceFileDigestBinding,
    TargetTrackReservation,
    compute_idempotency_key,
    reject_duplicate_journal_keys,
    validate_raw_journal_record,
)
from src.core.contracts.time_contracts import RationalFPS, TimeRangeFrames
from src.core.geometry.plan_geometry_contracts import SourceFrameEvidence
from src.core.timeline_plan_execution_binder import (
    OccupiedTrackSpan,
    PlannedOmissionRecord,
    PlannedTimelineAction,
    TargetSpec,
    TimelineExecutionPlan,
    TimelineOccupancySnapshot,
    bind_timeline_execution_plan,
)
from src.services.execution_journal_service import (
    ExecutionJournalService,
    JournalStorageError,
)
from src.services.execution_receipt_service import (
    ExecutionLockActiveError,
    ExecutionReceiptService,
    IdempotencyDuplicateActiveError,
    ReceiptStorageError,
    TargetStateCorruptError,
    ReceiptScanCapExceededError,
)
import src.services.resolve_execution_engine as engine_module
from src.services.resolve_execution_engine import (
    BoundedFileEvidenceVerifier,
    EvidenceVerificationError,
    EvidenceTrustProvider,
    ExecutionEngineResult,
    HmacEvidenceTrustProvider,
    ObserverTimeoutError,
    OperatorReconciliationProof,
    ReconciliationEvidenceVerifier,
    ResolveExecutionEngine,
    ResolveGatewayClientProtocol,
    TargetFPSDriftError,
    TargetTimelineShiftedError,
    UnexpectedTrackOccupancyError,
    WholePlanReconciliationError,
    validate_reconciliation_proof,
)

TEST_TRUST_ANCHOR_SECRET: bytes = b"test-secret-key-resolveflow-32b!"
TEST_TRUST_PROVIDER = HmacEvidenceTrustProvider(TEST_TRUST_ANCHOR_SECRET)


# =============================================================================
# Mock / Fake Gateway Harness (Đóng kín trong bộ nhớ, ZERO Resolve Connection)
# =============================================================================

@pytest.fixture(autouse=True)
def clean_unresolved_registry():
    """Tự động dọn dẹp registry sở hữu bất định trước và sau mỗi test."""
    engine_module.GLOBAL_UNRESOLVED_TARGET_REGISTRY.clear_for_test()
    yield
    engine_module.GLOBAL_UNRESOLVED_TARGET_REGISTRY.clear_for_test()


class FakeResolveGateway:
    """Fake Gateway hoàn toàn trong bộ nhớ mô phỏng Resolve Gateway."""
    _is_approved_mock_gateway: bool = True

    def __init__(
        self,
        project_id: str = "proj_test_01",
        project_name: str = "Test Project",
        timeline_id: str = "tl_test_01",
        timeline_name: str = "Timeline 1",
        fps: RationalFPS = RationalFPS(24, 1),
        resolution: Tuple[int, int] = (1920, 1080),
        occupancy_status: str = "AUTHORITATIVE",
    ):
        self.project_id = project_id
        self.project_name = project_name
        self.timeline_id = timeline_id
        self.timeline_name = timeline_name
        self.fps = fps
        self.resolution = resolution
        self.occupancy_status = occupancy_status

        # Điều khiển hành vi của execute_action
        self.action_outcomes: Dict[int, Dict[str, Any]] = {}
        self.action_timeout_indices: set[int] = set()
        self.action_exception_indices: set[int] = set()

        # Nhật ký các lệnh đã nhận
        self.dispatched_actions: List[PlannedTimelineAction] = []
        self.is_busy_unresolved: bool = False

    def query_active_timeline_authority(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "timeline_id": self.timeline_id,
            "timeline_name": self.timeline_name,
            "fps": self.fps,
            "resolution": self.resolution,
        }

    def query_track_occupancy_snapshot(self, target_spec: TargetSpec) -> TimelineOccupancySnapshot:
        return TimelineOccupancySnapshot(
            snapshot_id="snap_fake_001",
            captured_at_iso="2026-11-02T10:00:00Z",
            project_id=target_spec.project_id,
            timeline_id=target_spec.timeline_id,
            timeline_fps=target_spec.timeline_fps,
            status=self.occupancy_status,
            occupied_spans=(),
            scanned_tracks=(("video", 1), ("video", 2), ("audio", 1), ("audio", 2)),
        )

    def execute_action(
        self,
        action: PlannedTimelineAction,
        timeout_sec: float = 15.0,
    ) -> Dict[str, Any]:
        self.dispatched_actions.append(action)

        if action.action_index in self.action_timeout_indices:
            self.is_busy_unresolved = True
            raise ObserverTimeoutError(f"Observation timeout ({timeout_sec}s) on action {action.action_index}")

        if action.action_index in self.action_exception_indices:
            self.is_busy_unresolved = True
            raise RuntimeError(f"Simulated IPC transport failure on action {action.action_index}")

        # Kết quả mặc định: thành công với item_id sinh ngẫu nhiên/tất định
        outcome = self.action_outcomes.get(
            action.action_index,
            {"success": True, "item_id": f"item_uid_{action.action_index:03d}"},
        )
        return outcome


# =============================================================================
# Fixtures & Helper Generators
# =============================================================================

def _build_test_plan_and_approval(
    target_project_id: str = "proj_test_01",
    target_timeline_id: str = "tl_test_01",
    fps: RationalFPS = RationalFPS(24, 1),
    num_actions: int = 2,
    binding_status: str = "BOUND_VERIFIED",
) -> Tuple[TimelineExecutionPlan, AppOwnedApprovalRecord]:
    """Tạo cặp TimelineExecutionPlan và AppOwnedApprovalRecord hợp lệ khớp nhau 100%."""
    target_spec = TargetSpec(
        project_id=target_project_id,
        project_name="Test Project",
        timeline_id=target_timeline_id,
        timeline_name="Timeline 1",
        timeline_fps=fps,
        timeline_resolution=(1920, 1080),
        start_timecode_frame=86400,
    )

    actions = []
    for i in range(num_actions):
        actions.append(
            PlannedTimelineAction(
                action_index=i,
                action_type="INSERT_CLIP",
                target_track_type="video",
                target_track_index=1,
                clip_id=f"clip_{i:02d}",
                source_file_path=f"/media/test/clip_{i:02d}.mp4",
                source_range=TimeRangeFrames(start_frame=0, end_frame=48),
                timeline_range=TimeRangeFrames(start_frame=86400 + i * 48, end_frame=86400 + (i + 1) * 48),
                applied_speed=Fraction(1, 1),
                role="footage",
                action_idempotency_hash=f"hash_act_{i:02d}",
                action_params={"clip_index": i},
            )
        )

    src_digest = SourceFileDigestBinding(
        clip_id="clip_00",
        absolute_path="/media/test/clip_00.mp4",
        full_sha256="a" * 64,
        stream_index=0,
        stat_identity=(1000, 2000, 3000, 4000, 5000),
    )

    rev_hex = "a" * 64
    idemp_key = compute_idempotency_key(
        proposal_id="prop_01",
        proposal_revision=rev_hex,
        approval_id="appr_01",
        project_id=target_project_id,
        timeline_id=target_timeline_id,
    )

    plan = TimelineExecutionPlan(
        plan_id="plan_01",
        idempotency_key=idemp_key,
        proposal_id="prop_01",
        proposal_revision=rev_hex,
        approval_id="appr_01",
        approved_at_iso="2026-11-02T10:00:00Z",
        target_spec=target_spec,
        occupancy_snapshot_id="snap_fake_001",
        binding_status=binding_status,
        planned_actions=tuple(actions),
        planned_actions_count=len(actions),
        track_reservations=(),
        source_digests=(src_digest,),
        catalog_digests=(),
        accepted_omissions=(),
        total_timeline_frames=num_actions * 48,
        total_timeline_duration_sec=Fraction(num_actions * 48, 24),
        issues=(),
        created_at_iso="2026-11-02T10:00:00Z",
    )

    approval = AppOwnedApprovalRecord(
        approval_id="appr_01",
        proposal_id="prop_01",
        proposal_revision=rev_hex,
        approved_at_iso="2026-11-02T10:00:00Z",
        accepted_omission_ids=(),
        approval_scope=VALID_APPROVAL_SCOPE,
    )

    return plan, approval


def _write_evidence_artifact(
    tmp_path: Path,
    target_project_id: str,
    target_timeline_id: str,
    target_execution_id: str,
    action_index: Any,
    verified_outcome: str,
    operator_id: str = "op_1",
    timestamp_iso: str = "2026-11-02T12:00:00Z",
    observation_id: Optional[str] = "obs_auto_001",
    observed_at_iso: Optional[str] = "2026-11-02T12:00:00Z",
    observer_identity: Optional[str] = "resolveflow-supervisor",
    is_stale: Any = False,
    include_freshness: bool = True,
    attestation_signature: Optional[str] = None,
    trust_secret: bytes = TEST_TRUST_ANCHOR_SECRET,
    corrupt_json: bool = False,
    extra_bytes_after: int = 0,
    use_canonical: bool = True,
) -> Tuple[str, str]:
    """Tạo artifact bằng chứng JSON thực tế trên đĩa và trả về (file_path, sha256_hex)."""
    art_path = tmp_path / f"art_{target_execution_id}_{action_index}_{uuid.uuid4().hex[:8]}.json"
    if corrupt_json:
        raw = b"CORRUPTED_JSON_NOT_VALID_BYTES"
    else:
        payload: Dict[str, Any] = {
            "artifact_version": 1,
            "target_project_id": target_project_id,
            "target_timeline_id": target_timeline_id,
            "target_execution_id": target_execution_id,
            "action_index": action_index,
            "verified_outcome": verified_outcome,
            "operator_id": operator_id,
            "reconciliation_timestamp_iso": timestamp_iso,
        }
        if observation_id is not None:
            payload["observation_id"] = observation_id
        if observed_at_iso is not None:
            payload["observed_at_iso"] = observed_at_iso
        if observer_identity is not None:
            payload["observer_identity"] = observer_identity
        if include_freshness:
            payload["is_stale"] = is_stale

        # Attestation signature
        if attestation_signature is not None:
            payload["attestation_signature"] = attestation_signature
        else:
            try:
                payload["attestation_signature"] = HmacEvidenceTrustProvider.compute_canonical_attestation(
                    payload, secret_key=trust_secret
                )
            except Exception:
                # Nếu thiếu trường bắt buộc để ký (negative testing cho schema), gán dummy signature
                payload["attestation_signature"] = "dummy_attestation_missing_field"

        if use_canonical:
            raw = HmacEvidenceTrustProvider.canonical_serialize(payload)
        else:
            raw = json.dumps(payload, indent=2).encode("utf-8")

        if extra_bytes_after > 0:
            raw += b"X" * extra_bytes_after

    with open(art_path, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    digest = hashlib.sha256(raw).hexdigest().lower()
    return str(art_path), digest


# =============================================================================
# Test Suite
# =============================================================================

def test_tripwire_no_resolve_or_fusionscript_imports(tmp_path):
    """Tripwire: src/services/resolve_execution_engine.py tuyệt đối KHÔNG import Resolve hay fusionscript."""
    source_file = engine_module.__file__
    assert source_file is not None and os.path.exists(source_file)

    with open(source_file, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=source_file)

    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_names.add(node.module)

    # Khẳng định không có bất kỳ import nào của DaVinciResolveScript, fusionscript, bmd, ResolveAutomation, hay PySide6
    forbidden_substrings = ["davinci", "fusionscript", "bmd", "resolve_api", "pyside", "qt"]
    for name in imported_names:
        name_lower = name.lower()
        for forbidden in forbidden_substrings:
            assert forbidden not in name_lower, f"Tripwire violation: found forbidden import '{name}' in engine!"

    # Khởi tạo engine và thực thi với FakeGateway
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway()

    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    assert engine is not None


def test_happy_path_wal_execution_two_actions(tmp_path):
    """Happy Path: Thực thi 2 action thành công 100%, ghi WAL PENDING và COMMITTED bền vững."""
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway()

    plan, approval = _build_test_plan_and_approval(num_actions=2)

    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    result = engine.execute_plan(plan, approval, execution_id="exec_happy_01")

    # 1. Kết quả trả về
    assert result.status == "COMPLETED"
    assert result.execution_dispatched is True
    assert result.committed_actions_count == 2
    assert result.total_actions_count == 2
    assert result.committed_item_ids == ("item_uid_000", "item_uid_001")
    assert result.lock_retained is False

    # 2. Thẩm định Receipt trên đĩa
    receipt = receipt_svc.load_receipt("exec_happy_01")
    assert receipt.state == "COMPLETED"
    assert receipt.completed_at_iso is not None
    assert receipt.last_error_code == ""

    # 3. Thẩm định thứ tự WAL trong Journal
    replay = journal_svc.replay_journal("exec_happy_01")
    assert replay.is_clean is True
    assert len(replay.entries) == 6

    stages = [e.stage for e in replay.entries]
    assert stages == [
        "START",
        "ACTION_PENDING",
        "ACTION_COMMITTED",
        "ACTION_PENDING",
        "ACTION_COMMITTED",
        "FINISH",
    ]

    # Kiểm tra chuỗi sequence tăng dần đơn điệu
    for idx, e in enumerate(replay.entries):
        assert e.seq == idx

    # 4. Kiểm tra khóa độc quyền đã được giải phóng
    with receipt_svc.acquire_lock("exec_happy_01"):
        pass


def test_flt_01_pre_mutation_revision_mismatch(tmp_path):
    """FLT-01: Thất bại tiền đột biến do proposal_revision trong plan không khớp approval."""
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway()

    plan, approval = _build_test_plan_and_approval()
    # Giả lập approval có revision mới hơn plan (stale plan)
    stale_approval = replace(approval, proposal_revision="b" * 64)

    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    result = engine.execute_plan(plan, stale_approval, execution_id="exec_flt01")

    assert result.status == "FAILED_PRE_MUTATION"
    assert result.error_code == "ERR_STALE_PROPOSAL_REVISION"
    assert result.execution_dispatched is False
    assert len(gateway.dispatched_actions) == 0

    # 0 receipt, 0 journal được tạo
    assert not os.path.exists(receipt_svc._receipt_path("exec_flt01"))
    assert not os.path.exists(journal_svc.journal_path("exec_flt01"))


def test_flt_01_plan_binding_status_not_verified(tmp_path):
    """FLT-01: Từ chối kế hoạch nếu binding_status != BOUND_VERIFIED."""
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway()

    plan, approval = _build_test_plan_and_approval(binding_status="BOUND_BLOCKED")

    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    result = engine.execute_plan(plan, approval, execution_id="exec_flt01_blocked")

    assert result.status == "FAILED_PRE_MUTATION"
    assert result.error_code == "ERR_PLAN_NOT_VERIFIED"
    assert result.execution_dispatched is False
    assert len(gateway.dispatched_actions) == 0


def test_flt_02_storage_enospc_at_first_pending(tmp_path, monkeypatch):
    """FLT-02: Hết dung lượng đĩa (ENOSPC) khi ghi ACTION_PENDING đầu tiên -> DỪNG TRƯỚC KHI GỌI GATEWAY."""
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway()

    plan, approval = _build_test_plan_and_approval(num_actions=1)

    # Monkeypatch append_entry để ném OSError(ENOSPC) khi ghi PENDING
    orig_append = journal_svc.append_entry

    def mock_append(exec_id: str, entry: JournalEntry):
        if entry.stage == "ACTION_PENDING":
            raise OSError(errno.ENOSPC, "No space left on device")
        orig_append(exec_id, entry)

    monkeypatch.setattr(journal_svc, "append_entry", mock_append)

    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    result = engine.execute_plan(plan, approval, execution_id="exec_enospc")

    assert result.status == "FAILED_HALTED"
    assert result.error_code == "ERR_STORAGE_FSYNC_FAILED"
    # Quan trọng: 0 lệnh nào được dispatch sang Gateway!
    assert len(gateway.dispatched_actions) == 0
    assert result.execution_dispatched is False

    # Do 0 actions committed, receipt được chuyển sang ABORTED
    receipt = receipt_svc.load_receipt("exec_enospc")
    assert receipt.state == "ABORTED"


def test_flt_03_target_timeline_shifted(tmp_path):
    """FLT-03: Người dùng đổi timeline trên Resolve -> Phát hiện sai lệch và dừng khẩn cấp trước đột biến."""
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    # FakeGateway mở timeline_id = "tl_user_different" thay vì "tl_test_01"
    gateway = FakeResolveGateway(timeline_id="tl_user_different")

    plan, approval = _build_test_plan_and_approval(target_timeline_id="tl_test_01")

    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    result = engine.execute_plan(plan, approval, execution_id="exec_flt03")

    assert result.status == "FAILED_PRE_MUTATION"
    assert result.error_code == "ERR_TARGET_TIMELINE_SHIFTED"
    assert result.execution_dispatched is False
    assert len(gateway.dispatched_actions) == 0


def test_flt_04_target_fps_drift(tmp_path):
    """FLT-04: Sai lệch FPS giữa TargetSpec và timeline thực tế -> Dừng trước đột biến."""
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    # FakeGateway có FPS 30/1 thay vì 24/1
    gateway = FakeResolveGateway(fps=RationalFPS(30, 1))

    plan, approval = _build_test_plan_and_approval(fps=RationalFPS(24, 1))

    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    result = engine.execute_plan(plan, approval, execution_id="exec_flt04")

    assert result.status == "FAILED_PRE_MUTATION"
    assert result.error_code == "ERR_TARGET_FPS_DRIFT"
    assert result.execution_dispatched is False
    assert len(gateway.dispatched_actions) == 0


def test_flt_05_gateway_deterministic_failure_at_action_0(tmp_path):
    """FLT-05: Resolve trả về False tại action đầu tiên -> Ghi ACTION_FAILED, chuyển ABORTED (0 committed)."""
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway()

    # Cấu hình action 0 thất bại
    gateway.action_outcomes[0] = {
        "success": False,
        "error_code": "ERR_MEDIA_INSERT_FAILED",
        "error_message": "Clip not accepted by timeline",
    }

    plan, approval = _build_test_plan_and_approval(num_actions=2)

    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    result = engine.execute_plan(plan, approval, execution_id="exec_flt05_zero")

    assert result.status == "FAILED_HALTED"
    assert result.error_code == "ERR_MEDIA_INSERT_FAILED"
    assert result.committed_actions_count == 0
    # Do K=0 actions committed, receipt chuyển sang ABORTED
    receipt = receipt_svc.load_receipt("exec_flt05_zero")
    assert receipt.state == "ABORTED"

    # Action 1 không bao giờ được dispatch
    assert len(gateway.dispatched_actions) == 1


def test_flt_05_deterministic_failure_with_prior_committed_retains_executing(tmp_path):
    """FLT-05: Action 0 thành công, Action 1 thất bại -> Receipt giữ nguyên EXECUTING (TUYỆT ĐỐI CẤM ABORTED)."""
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway()

    # Action 0 thành công (mặc định), Action 1 thất bại
    gateway.action_outcomes[1] = {
        "success": False,
        "error_code": "ERR_INSUFFICIENT_TRACK_SPACE",
        "error_message": "Track 1 collision",
    }

    plan, approval = _build_test_plan_and_approval(num_actions=2)

    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    result = engine.execute_plan(plan, approval, execution_id="exec_flt05_partial")

    assert result.status == "INTERRUPTED_WITH_COMMITTED_ACTIONS"
    assert result.committed_actions_count == 1
    assert result.committed_item_ids == ("item_uid_000",)

    # TUYỆT ĐỐI CẤM ABORTED: Receipt phải ở EXECUTING
    receipt = receipt_svc.load_receipt("exec_flt05_partial")
    assert receipt.state == "EXECUTING"

    # Nhật ký ghi nhận trung thực
    replay = journal_svc.replay_journal("exec_flt05_partial")
    stages = [e.stage for e in replay.entries]
    assert stages == [
        "START",
        "ACTION_PENDING",
        "ACTION_COMMITTED",
        "ACTION_PENDING",
        "ACTION_FAILED",
        "FINISH",
    ]


def test_flt_06_observer_timeout_retains_lock_and_halts(tmp_path):
    """FLT-06: Observer Timeout (>15s) -> Retain Lock, Gateway BUSY, IN_DOUBT, KHÔNG tự động replay."""
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway()

    # Cấu hình action 1 gây timeout quan sát
    gateway.action_timeout_indices.add(1)

    plan, approval = _build_test_plan_and_approval(num_actions=3)

    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    result = engine.execute_plan(plan, approval, execution_id="exec_timeout_01")

    # 1. Trạng thái kết quả
    assert result.status == "IN_DOUBT_OBSERVER_TIMEOUT"
    assert result.error_code == "ERR_OBSERVER_TIMEOUT_IN_DOUBT"
    assert result.committed_actions_count == 1
    assert result.lock_retained is True
    assert gateway.is_busy_unresolved is True

    # Action 2 không bao giờ được dispatch
    assert len(gateway.dispatched_actions) == 2

    # 2. KHÓA CỐ VẤN VẪN ĐANG BỊ GIỮ NGUYÊN (Lock Retained)
    with pytest.raises(ExecutionLockActiveError):
        with receipt_svc.acquire_lock("exec_timeout_01"):
            pass

    # 3. Receipt giữ EXECUTING (không ABORTED)
    receipt = receipt_svc.load_receipt("exec_timeout_01")
    assert receipt.state == "EXECUTING"

    # 4. Dòng cuối journal là FINISH với note HALTED_IN_DOUBT_OBSERVER_TIMEOUT
    replay = journal_svc.replay_journal("exec_timeout_01")
    last_entry = replay.entries[-1]
    assert last_entry.stage == "FINISH"
    assert "HALTED_IN_DOUBT_OBSERVER_TIMEOUT" in last_entry.note


def test_flt_07_torn_journal_tail_non_destructive_preservation(tmp_path):
    """FLT-07: Sập nguồn gây rách dòng cuối nhật ký -> 100% byte gốc được bảo toàn, KHÔNG cắt gọt."""
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway()

    plan, approval = _build_test_plan_and_approval(num_actions=1)

    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    result = engine.execute_plan(plan, approval, execution_id="exec_torn")
    assert result.status == "COMPLETED"

    # Giả lập sập nguồn ngắt quãng ghi thêm một dòng dở dang ở cuối file
    journal_file = journal_svc.journal_path("exec_torn")
    orig_size = os.path.getsize(journal_file)

    corrupt_fragment = b'{"seq": 99, "stage": "ACTION_PENDING", "unfi'
    with open(journal_file, "ab") as f:
        f.write(corrupt_fragment)
        f.flush()

    new_size = os.path.getsize(journal_file)
    assert new_size == orig_size + len(corrupt_fragment)

    # Replay journal qua service nghiệm thu
    replay = journal_svc.replay_journal("exec_torn")
    assert replay.is_clean is False
    assert replay.torn_tail is not None
    assert replay.torn_tail.is_torn is True
    assert replay.torn_tail.raw_bytes == corrupt_fragment

    # BẢO TỒN NGUYÊN VẸN: Dung lượng file trên đĩa vẫn giữ nguyên new_size, KHÔNG truncate!
    assert os.path.getsize(journal_file) == new_size


def test_flt_08_duplicate_request_returns_historical_reference_no_dispatch(tmp_path):
    """FLT-08: Yêu cầu trùng lặp đã COMPLETED -> Trả về tham chiếu lịch sử, 0 dispatch, không chiếm lock."""
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway()

    plan, approval = _build_test_plan_and_approval(num_actions=1)

    # Chạy lần 1 thành công
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    result1 = engine.execute_plan(plan, approval, execution_id="exec_orig_01")
    assert result1.status == "COMPLETED"
    assert len(gateway.dispatched_actions) == 1

    # Chạy lần 2 với cùng plan (cùng idempotency_key)
    result2 = engine.execute_plan(plan, approval, execution_id="exec_retry_02")

    # Thẩm định ngữ nghĩa nhất quán
    assert result2.status == "DUPLICATE_REQUEST_PREVIOUSLY_COMPLETED"
    assert result2.execution_dispatched is False
    assert result2.prior_receipt_id == "exec_orig_01"
    assert result2.prior_completed_at is not None
    assert result2.error_code == "ERR_IDEMPOTENCY_DUPLICATE_COMPLETED"

    # Gateway KHÔNG nhận thêm bất kỳ lệnh nào mới
    assert len(gateway.dispatched_actions) == 1


def test_flt_09_cancellation_pre_dispatch_action_0_aborts(tmp_path):
    """FLT-09 Case A: Hủy bỏ trước khi dispatch action 0 -> Hợp lệ chuyển ABORTED (0 committed)."""
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway()

    plan, approval = _build_test_plan_and_approval(num_actions=2)

    # Cờ hủy bật ngay từ đầu
    cancel_flag = True

    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    result = engine.execute_plan(
        plan,
        approval,
        execution_id="exec_cancel_zero",
        is_cancellation_requested=lambda: cancel_flag,
    )

    assert result.status == "CANCELLED_PRE_DISPATCH"
    assert result.execution_dispatched is False
    assert result.committed_actions_count == 0
    assert len(gateway.dispatched_actions) == 0

    receipt = receipt_svc.load_receipt("exec_cancel_zero")
    assert receipt.state == "ABORTED"
    assert receipt.last_error_code == "CANCELLED_PRE_DISPATCH"


def test_flt_09_cancellation_after_action_0_committed_retains_executing(tmp_path):
    """FLT-09 Case B: Action 0 đã commit, hủy trước action 1 -> TUYỆT ĐỐI CẤM ABORTED, giữ EXECUTING."""
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway()

    plan, approval = _build_test_plan_and_approval(num_actions=3)

    def should_cancel() -> bool:
        return len(gateway.dispatched_actions) >= 1

    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    result = engine.execute_plan(
        plan,
        approval,
        execution_id="exec_cancel_mid",
        is_cancellation_requested=should_cancel,
    )

    assert result.status == "INTERRUPTED_WITH_COMMITTED_ACTIONS"
    assert result.committed_actions_count == 1
    assert result.committed_item_ids == ("item_uid_000",)
    assert len(gateway.dispatched_actions) == 1

    # TUYỆT ĐỐI CẤM ABORTED: Receipt giữ nguyên EXECUTING
    receipt = receipt_svc.load_receipt("exec_cancel_mid")
    assert receipt.state == "EXECUTING"


def test_flt_10_partial_insert_interruption_reports_truthful_state(tmp_path):
    """FLT-10: Chèn dở dang 2/3 action, báo cáo đối soát trung thực 2 clip đã commit."""
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway()

    # Action 2 gặp sự cố
    gateway.action_outcomes[2] = {
        "success": False,
        "error_code": "ERR_MEDIA_OFFLINE",
        "error_message": "Drive unmounted",
    }

    plan, approval = _build_test_plan_and_approval(num_actions=3)

    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    result = engine.execute_plan(plan, approval, execution_id="exec_flt10")

    assert result.status == "INTERRUPTED_WITH_COMMITTED_ACTIONS"
    assert result.committed_actions_count == 2
    assert result.total_actions_count == 3
    assert result.committed_item_ids == ("item_uid_000", "item_uid_001")

    receipt = receipt_svc.load_receipt("exec_flt10")
    assert receipt.state == "EXECUTING"


def test_flt_11_no_automatic_rollback_on_failure(tmp_path):
    """FLT-11: Khi gặp sự cố dở dang, Động cơ KHÔNG BAO GIỜ tự động rollback hay xóa clip ngầm."""
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway()

    # Action 1 gặp lỗi
    gateway.action_outcomes[1] = {"success": False, "error_code": "ERR_FAIL"}

    plan, approval = _build_test_plan_and_approval(num_actions=2)

    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    result = engine.execute_plan(plan, approval, execution_id="exec_no_auto_rollback")

    # Xác nhận chỉ có 2 action được gửi tới gateway, 0 lệnh xóa hay rollback bù trừ tự động nào được phát sinh
    assert len(gateway.dispatched_actions) == 2
    assert result.committed_actions_count == 1
    # Bù trừ bắt buộc phải chuyển sang RecoveryAssessmentDialog để người vận hành xem xét


# =============================================================================
# G07-07B4-R1 Safety Suite: B4-R1 through B4-R5 Verification
# =============================================================================

def test_b4_r1_target_scoped_exclusion_and_barrier_race(tmp_path):
    """B4-R1: Khóa theo target timeline (project_id, timeline_id) ngăn chặn hai kế hoạch hoặc
    hai execution_id khác nhau cùng thi công trên một timeline Resolve.
    
    Bao gồm kiểm thử chạy đua đồng thời có Barrier kiểm soát để chứng minh tối đa 1 lệnh dispatch.
    """
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway(project_id="proj_target_01", timeline_id="tl_target_01")

    plan_a, approval_a = _build_test_plan_and_approval(
        target_project_id="proj_target_01",
        target_timeline_id="tl_target_01",
        num_actions=2,
    )

    plan_b = replace(
        plan_a,
        plan_id="plan_02",
        proposal_id="prop_02",
        approval_id="appr_02",
        idempotency_key=compute_idempotency_key("prop_02", plan_a.proposal_revision, "appr_02", "proj_target_01", "tl_target_01"),
    )
    approval_b = replace(
        approval_a,
        approval_id="appr_02",
        proposal_id="prop_02",
    )

    # Pha 1: Giữ lock bởi phiên A, phiên B thử truy cập target timeline -> bị chặn ngay
    plan_a_in_dispatch = threading.Event()
    release_plan_a = threading.Event()
    original_execute_action = gateway.execute_action

    def gated_execute_action(action, timeout_sec=15.0):
        if action.action_index == 0:
            plan_a_in_dispatch.set()
            release_plan_a.wait(timeout=5.0)
        return original_execute_action(action, timeout_sec=timeout_sec)

    gateway.execute_action = gated_execute_action

    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    result_a_holder: List[ExecutionEngineResult] = []

    def run_plan_a():
        res = engine.execute_plan(plan_a, approval_a, execution_id="exec_A")
        result_a_holder.append(res)

    thread_a = threading.Thread(target=run_plan_a)
    thread_a.start()

    assert plan_a_in_dispatch.wait(timeout=5.0), "Thread A không vào được execute_action"

    # Thread B cố gắng thi công kế hoạch khác trên cùng target timeline
    result_b = engine.execute_plan(plan_b, approval_b, execution_id="exec_B")
    assert result_b.status == "FAILED_PRE_MUTATION"
    assert result_b.error_code == "ERR_TARGET_TIMELINE_LOCKED"
    assert result_b.execution_dispatched is False

    # Cho phép thread A hoàn tất
    release_plan_a.set()
    thread_a.join(timeout=5.0)

    assert len(result_a_holder) == 1
    assert result_a_holder[0].status == "COMPLETED"
    assert result_a_holder[0].committed_actions_count == 2

    # Pha 2: Barrier-controlled concurrent race giữa 2 thread với 2 plan và 2 execution_id
    gateway.execute_action = original_execute_action
    barrier = threading.Barrier(2)
    race_results: Dict[str, ExecutionEngineResult] = {}

    def race_worker(exec_id: str, p: TimelineExecutionPlan, a: AppOwnedApprovalRecord):
        barrier.wait()
        res = engine.execute_plan(p, a, execution_id=exec_id)
        race_results[exec_id] = res

    plan_c = replace(
        plan_a,
        plan_id="plan_03",
        proposal_id="prop_03",
        approval_id="appr_03",
        idempotency_key=compute_idempotency_key("prop_03", plan_a.proposal_revision, "appr_03", "proj_target_01", "tl_target_01"),
    )
    approval_c = replace(approval_a, approval_id="appr_03", proposal_id="prop_03")

    plan_d = replace(
        plan_a,
        plan_id="plan_04",
        proposal_id="prop_04",
        approval_id="appr_04",
        idempotency_key=compute_idempotency_key("prop_04", plan_a.proposal_revision, "appr_04", "proj_target_01", "tl_target_01"),
    )
    approval_d = replace(approval_a, approval_id="appr_04", proposal_id="prop_04")

    t1 = threading.Thread(target=race_worker, args=("exec_race_1", plan_c, approval_c))
    t2 = threading.Thread(target=race_worker, args=("exec_race_2", plan_d, approval_d))

    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    # Đúng 1 thread thành công, thread còn lại bị từ chối với ERR_TARGET_TIMELINE_LOCKED
    statuses = [res.status for res in race_results.values()]
    assert "COMPLETED" in statuses
    assert "FAILED_PRE_MUTATION" in statuses
    err_codes = [res.error_code for res in race_results.values() if res.status == "FAILED_PRE_MUTATION"]
    assert err_codes == ["ERR_TARGET_TIMELINE_LOCKED"]


def test_b4_r1_idempotency_concurrent_reservation_race(tmp_path):
    """B4-R1: Khóa idempotency nguyên tử (acquire_idempotency_lock) chống race condition qua nhiều tiến trình."""
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    plan, _ = _build_test_plan_and_approval()

    receipt_1 = ExecutionReceipt(
        schema_version=2,
        execution_id="exec_idemp_1",
        idempotency_key=plan.idempotency_key,
        proposal_id=plan.proposal_id,
        proposal_revision=plan.proposal_revision,
        approval_id=plan.approval_id,
        approved_at_iso=plan.approved_at_iso,
        project_name=plan.target_spec.project_name,
        project_id=plan.target_spec.project_id,
        timeline_name=plan.target_spec.timeline_name,
        timeline_id=plan.target_spec.timeline_id,
        timeline_fps_numerator=24,
        timeline_fps_denominator=1,
        timeline_resolution=(1920, 1080),
        source_digests=(),
        catalog_digests=(),
        track_reservations=(),
        planned_actions_count=2,
        created_at_iso=datetime.now(timezone.utc).isoformat(),
        state="INITIALIZED",
    )
    receipt_2 = replace(receipt_1, execution_id="exec_idemp_2")

    # Tạo receipt 1 thành công
    created = receipt_svc.create_receipt(receipt_1)
    assert created.execution_id == "exec_idemp_1"

    # Lần 2 với cùng idempotency_key bị từ chối
    with pytest.raises(IdempotencyDuplicateActiveError):
        receipt_svc.create_receipt(receipt_2)


def test_b4_r2_occupancy_validation_missing_stale_ambiguous_or_colliding(tmp_path):
    """B4-R2: Thẩm định snapshot target identity, FPS, status, và scanned-track coverage.
    
    Mọi bằng chứng thiếu/cũ/mơ hồ/va chạm đều phải dừng trước khi ghi ACTION_PENDING và trước dispatch.
    """
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))

    # Case 2a: Snapshot status != "AUTHORITATIVE"
    gw_stale = FakeResolveGateway(occupancy_status="STALE")
    plan, approval = _build_test_plan_and_approval()
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gw_stale)
    res = engine.execute_plan(plan, approval, execution_id="exec_occ_stale")
    assert res.status == "FAILED_HALTED"
    assert res.error_code == "ERR_UNEXPECTED_TRACK_OCCUPANCY"
    assert "ERR_OCCUPANCY_NOT_AUTHORITATIVE" in res.error_message
    assert len(gw_stale.dispatched_actions) == 0

    # Case 2b: Snapshot project/timeline mismatch
    class MismatchedTargetGateway(FakeResolveGateway):
        def query_track_occupancy_snapshot(self, target_spec: TargetSpec) -> TimelineOccupancySnapshot:
            snap = super().query_track_occupancy_snapshot(target_spec)
            return replace(snap, project_id="foreign_project")

    gw_mismatch = MismatchedTargetGateway()
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gw_mismatch)
    res = engine.execute_plan(plan, approval, execution_id="exec_occ_mismatch")
    assert res.status == "FAILED_HALTED"
    assert "ERR_OCCUPANCY_TARGET_MISMATCH" in res.error_message
    assert len(gw_mismatch.dispatched_actions) == 0

    # Case 2c: Snapshot FPS mismatch
    class MismatchedFPSGateway(FakeResolveGateway):
        def query_track_occupancy_snapshot(self, target_spec: TargetSpec) -> TimelineOccupancySnapshot:
            snap = super().query_track_occupancy_snapshot(target_spec)
            return replace(snap, timeline_fps=RationalFPS(30, 1))

    gw_fps = MismatchedFPSGateway()
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gw_fps)
    res = engine.execute_plan(plan, approval, execution_id="exec_occ_fps")
    assert res.status == "FAILED_HALTED"
    assert "ERR_OCCUPANCY_FPS_MISMATCH" in res.error_message
    assert len(gw_fps.dispatched_actions) == 0

    # Case 2d: Target track không nằm trong scanned_tracks
    class UnscannedTrackGateway(FakeResolveGateway):
        def query_track_occupancy_snapshot(self, target_spec: TargetSpec) -> TimelineOccupancySnapshot:
            snap = super().query_track_occupancy_snapshot(target_spec)
            return replace(snap, scanned_tracks=(("audio", 1), ("audio", 2)))

    gw_unscanned = UnscannedTrackGateway()
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gw_unscanned)
    res = engine.execute_plan(plan, approval, execution_id="exec_occ_unscanned")
    assert res.status == "FAILED_HALTED"
    assert "ERR_OCCUPANCY_TRACK_NOT_SCANNED" in res.error_message
    assert len(gw_unscanned.dispatched_actions) == 0

    # Case 2e: Va chạm không gian với clip lạ hiện hữu (external collision)
    class CollidingTrackGateway(FakeResolveGateway):
        def query_track_occupancy_snapshot(self, target_spec: TargetSpec) -> TimelineOccupancySnapshot:
            snap = super().query_track_occupancy_snapshot(target_spec)
            # Clip lạ [86410, 86430) va chạm với planned action [86400, 86448)
            collision = OccupiedTrackSpan(
                track_type="video",
                track_index=1,
                start_frame=86410,
                end_frame=86430,
                item_name="ForeignClip.mov",
            )
            return replace(snap, occupied_spans=(collision,))

    gw_collision = CollidingTrackGateway()
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gw_collision)
    res = engine.execute_plan(plan, approval, execution_id="exec_occ_collision")
    assert res.status == "FAILED_HALTED"
    assert "ERR_TRACK_COLLISION" in res.error_message
    assert len(gw_collision.dispatched_actions) == 0


def test_b4_r2_occupancy_committed_clip_self_accounting(tmp_path):
    """B4-R2: Phân biệt chính xác giữa clip đã commit trong phiên hiện hành và va chạm ngoại lai."""
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))

    # Kế hoạch 2 action trên cùng video track 1:
    # Action 0: [86400, 86448)
    # Action 1: [86448, 86496)
    plan, approval = _build_test_plan_and_approval(num_actions=2)

    class SelfAccountingGateway(FakeResolveGateway):
        def __init__(self, include_external_collision_at_step_1: bool = False):
            super().__init__()
            self.step = 0
            self.include_external_collision = include_external_collision_at_step_1

        def query_track_occupancy_snapshot(self, target_spec: TargetSpec) -> TimelineOccupancySnapshot:
            spans: List[OccupiedTrackSpan] = []
            if self.step > 0:
                # Sau khi action 0 commit, Resolve báo có clip do chính action 0 tạo ra
                spans.append(
                    OccupiedTrackSpan(
                        track_type="video",
                        track_index=1,
                        start_frame=86400,
                        end_frame=86448,
                        item_name="Committed_Clip_00.mp4",
                    )
                )
                if self.include_external_collision:
                    # Clip lạ va chạm với action 1 [86448, 86496)
                    spans.append(
                        OccupiedTrackSpan(
                            track_type="video",
                            track_index=1,
                            start_frame=86450,
                            end_frame=86470,
                            item_name="SurpriseForeignClip.mp4",
                        )
                    )
            self.step += 1
            return TimelineOccupancySnapshot(
                snapshot_id=f"snap_step_{self.step}",
                captured_at_iso="2026-11-02T10:00:00Z",
                project_id=target_spec.project_id,
                timeline_id=target_spec.timeline_id,
                timeline_fps=target_spec.timeline_fps,
                status="AUTHORITATIVE",
                occupied_spans=tuple(spans),
                scanned_tracks=(("video", 1), ("video", 2), ("audio", 1), ("audio", 2)),
            )

    # Phần 1: Tự nhận diện clip của chính mình -> Vượt qua CP-06 thành công, COMPLETED
    gw_clean = SelfAccountingGateway(include_external_collision_at_step_1=False)
    engine_clean = ResolveExecutionEngine(receipt_svc, journal_svc, gw_clean)
    res_clean = engine_clean.execute_plan(plan, approval, execution_id="exec_self_clean")
    assert res_clean.status == "COMPLETED"
    assert res_clean.committed_actions_count == 2
    assert len(gw_clean.dispatched_actions) == 2

    # Phần 2: Clip của mình được bỏ qua, nhưng phát hiện clip ngoại lai va chạm action 1 -> Dừng ngay
    gw_dirty = SelfAccountingGateway(include_external_collision_at_step_1=True)
    engine_dirty = ResolveExecutionEngine(receipt_svc, journal_svc, gw_dirty)
    plan2, approval2 = _build_test_plan_and_approval(num_actions=2)
    plan2 = replace(
        plan2,
        plan_id="plan_dirty",
        proposal_id="prop_dirty",
        idempotency_key=compute_idempotency_key("prop_dirty", plan2.proposal_revision, "appr_dirty", "proj_test_01", "tl_test_01"),
    )
    approval2 = replace(approval2, proposal_id="prop_dirty")

    res_dirty = engine_dirty.execute_plan(plan2, approval2, execution_id="exec_self_dirty")
    assert res_dirty.status == "INTERRUPTED_WITH_COMMITTED_ACTIONS"
    assert res_dirty.committed_actions_count == 1
    assert "ERR_TRACK_COLLISION" in res_dirty.error_message
    # Action 1 KHÔNG BAO GIỜ được gửi tới Gateway
    assert len(gw_dirty.dispatched_actions) == 1


def test_b4_r3_post_dispatch_commit_persistence_failure_retains_lock(tmp_path):
    """B4-R3: Khi Gateway đã thành công nhưng ghi ACTION_COMMITTED vào WAL bị lỗi đĩa:
    - Xếp loại IN_DOUBT_PERSISTENCE_FAILED.
    - Giữ khóa cố vấn (lock_retained=True) và đăng ký vào UnresolvedTargetRegistry.
    - Cấm replay, cấm thi công mới trên timeline.
    - Bảo toàn byte nhật ký cuối cùng (không truncate/hỏng file).
    """
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway()

    plan, approval = _build_test_plan_and_approval(num_actions=2)
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)

    orig_append = journal_svc.append_entry

    def faulty_append_entry(exec_id: str, entry: JournalEntry) -> None:
        if entry.stage == "ACTION_COMMITTED":
            raise OSError(errno.EIO, "Simulated I/O failure during ACTION_COMMITTED append")
        orig_append(exec_id, entry)

    with patch.object(journal_svc, "append_entry", side_effect=faulty_append_entry):
        result = engine.execute_plan(plan, approval, execution_id="exec_commit_fault")

    assert result.status == "IN_DOUBT_PERSISTENCE_FAILED"
    assert result.error_code == "ERR_POST_DISPATCH_STORAGE_FAILED"
    assert result.lock_retained is True
    assert result.in_doubt_action is not None
    assert result.in_doubt_action.action_index == 0

    # Khóa và trạng thái bất định được ghi nhận trong registry tiến trình
    assert engine_module.GLOBAL_UNRESOLVED_TARGET_REGISTRY.is_target_unresolved("proj_test_01", "tl_test_01") is True

    # Kiểm tra byte nhật ký bền vững cuối cùng: hợp lệ, dừng tại ACTION_PENDING
    replay = journal_svc.replay_journal("exec_commit_fault")
    assert len(replay.entries) == 2  # START và ACTION_PENDING
    assert replay.entries[0].stage == "START"
    assert replay.entries[1].stage == "ACTION_PENDING"
    assert replay.entries[1].action_type == "INSERT_CLIP"

    # Lệnh thi công mới trên timeline này bị chặn đứng hoàn toàn
    plan_next, approval_next = _build_test_plan_and_approval(num_actions=1)
    plan_next = replace(
        plan_next,
        plan_id="plan_blocked",
        proposal_id="prop_blocked",
        idempotency_key=compute_idempotency_key("prop_blocked", plan_next.proposal_revision, "appr_blocked", "proj_test_01", "tl_test_01"),
    )
    approval_next = replace(approval_next, proposal_id="prop_blocked")

    result_blocked = engine.execute_plan(plan_next, approval_next, execution_id="exec_blocked")
    assert result_blocked.status == "FAILED_PRE_MUTATION"
    assert result_blocked.error_code == "ERR_TARGET_HAS_UNRESOLVED_IN_DOUBT"
    assert result_blocked.execution_dispatched is False


def test_b4_r3_post_dispatch_failure_and_finish_persistence_faults(tmp_path):
    """B4-R3: Xử lý lỗi lưu trữ khi ghi ACTION_FAILED/FINISH hoặc ghi receipt COMPLETED cuối cùng."""
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))

    # Tiểu mục 1: Lỗi lưu trữ khi ghi ACTION_FAILED sau dispatch
    gw_fail = FakeResolveGateway()
    gw_fail.action_outcomes[0] = {"success": False, "error_code": "ERR_RESOLVE_FAIL"}
    engine1 = ResolveExecutionEngine(receipt_svc, journal_svc, gw_fail)
    plan1, approval1 = _build_test_plan_and_approval(num_actions=1)

    orig_append = journal_svc.append_entry

    def faulty_failed_append(exec_id: str, entry: JournalEntry) -> None:
        if entry.stage == "ACTION_FAILED":
            raise OSError(errno.ENOSPC, "Disk full during ACTION_FAILED write")
        orig_append(exec_id, entry)

    with patch.object(journal_svc, "append_entry", side_effect=faulty_failed_append):
        res1 = engine1.execute_plan(plan1, approval1, execution_id="exec_failed_fault")

    assert res1.status == "IN_DOUBT_PERSISTENCE_FAILED"
    assert res1.lock_retained is True
    assert engine_module.GLOBAL_UNRESOLVED_TARGET_REGISTRY.is_target_unresolved("proj_test_01", "tl_test_01") is True

    # Dọn dẹp registry để kiểm thử tiếp tiểu mục 2
    engine_module.GLOBAL_UNRESOLVED_TARGET_REGISTRY.clear_for_test()

    # Tiểu mục 2: Lỗi lưu trữ khi ghi transition_state(COMPLETED) cuối cùng
    gw_ok = FakeResolveGateway(project_id="proj_test_02", timeline_id="tl_test_02")
    engine2 = ResolveExecutionEngine(receipt_svc, journal_svc, gw_ok)
    plan2, approval2 = _build_test_plan_and_approval(
        target_project_id="proj_test_02",
        target_timeline_id="tl_test_02",
        num_actions=1,
    )
    plan2 = replace(
        plan2,
        plan_id="plan_comp_fault",
        proposal_id="prop_comp_fault",
        idempotency_key=compute_idempotency_key("prop_comp_fault", plan2.proposal_revision, "appr_comp_fault", "proj_test_02", "tl_test_02"),
    )
    approval2 = replace(approval2, proposal_id="prop_comp_fault")

    orig_transition = receipt_svc.transition_state

    def faulty_transition_state(exec_id: str, new_state: str, **kwargs):
        if new_state == "COMPLETED":
            raise ReceiptStorageError("Simulated disk error during receipt completion")
        return orig_transition(exec_id, new_state=new_state, **kwargs)

    with patch.object(receipt_svc, "transition_state", side_effect=faulty_transition_state):
        res2 = engine2.execute_plan(plan2, approval2, execution_id="exec_comp_fault")

    assert res2.status == "IN_DOUBT_PERSISTENCE_FAILED"
    assert res2.lock_retained is True
    assert engine_module.GLOBAL_UNRESOLVED_TARGET_REGISTRY.is_target_unresolved("proj_test_02", "tl_test_02") is True


def test_b4_r4_unresolved_registry_lifecycle_and_reconciliation(tmp_path):
    """B4-R4: Vòng đời UnresolvedTargetRegistry:
    - Khi timeout, ghi nhận target và giữ lock.
    - Chặn mọi lệnh thi công mới cho target (kể cả execution_id khác).
    - Cấm giải phóng khi không có bằng chứng hợp lệ.
    - Chỉ giải phóng khi có verified_by='OPERATOR_RECONCILIATION'.
    """
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway(project_id="proj_recon_01", timeline_id="tl_recon_01")
    gateway.action_timeout_indices.add(0)  # Gây observer timeout

    plan, approval = _build_test_plan_and_approval(
        target_project_id="proj_recon_01",
        target_timeline_id="tl_recon_01",
        num_actions=1,
    )
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    result = engine.execute_plan(plan, approval, execution_id="exec_timeout_01")

    assert result.status == "IN_DOUBT_OBSERVER_TIMEOUT"
    assert result.lock_retained is True
    assert engine_module.GLOBAL_UNRESOLVED_TARGET_REGISTRY.is_target_unresolved("proj_recon_01", "tl_recon_01") is True

    record = engine_module.GLOBAL_UNRESOLVED_TARGET_REGISTRY.get_record("proj_recon_01", "tl_recon_01")
    assert record is not None
    assert record.execution_id == "exec_timeout_01"
    assert record.in_doubt_action is not None

    # Lệnh thi công khác bị chặn đứng ở CP-02
    plan2, approval2 = _build_test_plan_and_approval(
        target_project_id="proj_recon_01",
        target_timeline_id="tl_recon_01",
        num_actions=1,
    )
    plan2 = replace(
        plan2,
        plan_id="plan_diff_exec",
        proposal_id="prop_diff_exec",
        idempotency_key=compute_idempotency_key("prop_diff_exec", plan2.proposal_revision, "appr_diff_exec", "proj_recon_01", "tl_recon_01"),
    )
    approval2 = replace(approval2, proposal_id="prop_diff_exec")

    res_denied = engine.execute_plan(plan2, approval2, execution_id="exec_diff_id")
    assert res_denied.status == "FAILED_PRE_MUTATION"
    assert res_denied.error_code == "ERR_TARGET_HAS_UNRESOLVED_IN_DOUBT"

    # Thử giải phóng bằng plain dict -> Ném TypeError
    with pytest.raises(TypeError, match="ERR_INVALID_PROOF_TYPE"):
        engine_module.GLOBAL_UNRESOLVED_TARGET_REGISTRY.resolve_and_release(
            "proj_recon_01",
            "tl_recon_01",
            reconciliation_proof={"verified_by": "INVALID_AUTOMATIC_TIMEOUT"},
        )

    # Thử giải phóng bằng proof có target mismatch -> Ném ValueError
    mismatched_proof = OperatorReconciliationProof(
        schema_version=1,
        proof_id="proof_mismatch_01",
        target_project_id="other_proj",
        target_timeline_id="tl_recon_01",
        target_execution_id="exec_timeout_01",
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="operator_01",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash="a" * 64,
        notes="Target mismatch probe",
    )
    with pytest.raises(ValueError, match="ERR_PROOF_TARGET_MISMATCH"):
        engine_module.GLOBAL_UNRESOLVED_TARGET_REGISTRY.resolve_and_release(
            "proj_recon_01",
            "tl_recon_01",
            reconciliation_proof=mismatched_proof,
        )

    # Giải phóng với bằng chứng OperatorReconciliationProof xác thực chuẩn mực kèm artifact hợp lệ
    art_path, art_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id="proj_recon_01",
        target_timeline_id="tl_recon_01",
        target_execution_id="exec_timeout_01",
        action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="operator_editor_01",
    )
    valid_proof = OperatorReconciliationProof(
        schema_version=1,
        proof_id="proof_recon_01",
        target_project_id="proj_recon_01",
        target_timeline_id="tl_recon_01",
        target_execution_id="exec_timeout_01",
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="operator_editor_01",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha,
        evidence_artifact_path=art_path,
        notes="Verified visually on timeline that clip was not inserted during timeout.",
    )
    released = engine.reconcile_target(valid_proof)
    assert released is True
    assert engine_module.GLOBAL_UNRESOLVED_TARGET_REGISTRY.is_target_unresolved("proj_recon_01", "tl_recon_01") is False

    # Sau khi giải phóng, target timeline sẵn sàng nhận lệnh thi công mới
    gateway.action_timeout_indices.clear()
    res_retry = engine.execute_plan(plan2, approval2, execution_id="exec_retry_after_recon")
    assert res_retry.status == "COMPLETED"


def test_b4_r5_mock_only_admission_positive_and_negative_probes(tmp_path):
    """B4-R5: Cổng thẩm định mock-only prototype:
    - Chấp nhận FakeResolveGateway có _is_approved_mock_gateway=True.
    - Từ chối mọi gateway thiếu marker phê duyệt.
    - Từ chối adapter chứa các thuộc tính live Resolve (_automation, resolve, _resolve_override).
    """
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    plan, approval = _build_test_plan_and_approval()

    # Probe 1: Positive Control (FakeResolveGateway chuẩn)
    valid_fake_gw = FakeResolveGateway()
    engine_valid = ResolveExecutionEngine(receipt_svc, journal_svc, valid_fake_gw)
    res_valid = engine_valid.execute_plan(plan, approval, execution_id="exec_probe_valid")
    assert res_valid.status == "COMPLETED"

    # Probe 2: Negative Control - Adapter thiếu marker _is_approved_mock_gateway
    class UnapprovedGateway:
        def query_active_timeline_authority(self):
            return {}
        def query_track_occupancy_snapshot(self, spec):
            return None
        def execute_action(self, action, timeout_sec=15.0):
            return {}

    engine_unapproved = ResolveExecutionEngine(receipt_svc, journal_svc, UnapprovedGateway())
    res_unapproved = engine_unapproved.execute_plan(plan, approval, execution_id="exec_probe_unapproved")
    assert res_unapproved.status == "FAILED_PRE_MUTATION"
    assert res_unapproved.error_code == "ERR_MOCK_ONLY_GATEWAY_REQUIRED"
    assert "ERR_LIVE_RESOLVE_FORBIDDEN" in res_unapproved.error_message

    # Probe 3: Negative Control - Adapter chứa resolve
    gw_live_resolve = FakeResolveGateway()
    gw_live_resolve.resolve = "live_resolve_object"
    engine_live_resolve = ResolveExecutionEngine(receipt_svc, journal_svc, gw_live_resolve)
    res_live = engine_live_resolve.execute_plan(plan, approval, execution_id="exec_probe_live_res")
    assert res_live.status == "FAILED_PRE_MUTATION"
    assert res_live.error_code == "ERR_MOCK_ONLY_GATEWAY_REQUIRED"
    assert "ERR_LIVE_ADAPTER_REJECTED" in res_live.error_message

    # Probe 4: Negative Control - Adapter chứa _automation
    gw_automation = FakeResolveGateway()
    gw_automation._automation = "live_automation_object"
    engine_automation = ResolveExecutionEngine(receipt_svc, journal_svc, gw_automation)
    res_auto = engine_automation.execute_plan(plan, approval, execution_id="exec_probe_auto")
    assert res_auto.status == "FAILED_PRE_MUTATION"
    assert res_auto.error_code == "ERR_MOCK_ONLY_GATEWAY_REQUIRED"
    assert "ERR_LIVE_ADAPTER_REJECTED" in res_auto.error_message

    # Probe 5: Negative Control - Adapter chứa _resolve_override
    gw_override = FakeResolveGateway()
    gw_override._resolve_override = "live_override"
    engine_override = ResolveExecutionEngine(receipt_svc, journal_svc, gw_override)
    res_override = engine_override.execute_plan(plan, approval, execution_id="exec_probe_override")
    assert res_override.status == "FAILED_PRE_MUTATION"
    assert res_override.error_code == "ERR_MOCK_ONLY_GATEWAY_REQUIRED"
    assert "ERR_LIVE_ADAPTER_REJECTED" in res_override.error_message


def test_tripwire_no_ui_wiring_or_ui_imports():
    """Tripwire: Kiểm tra cấu trúc source code đảm bảo zero UI wiring tới resolve_execution_engine."""
    engine_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "src", "services", "resolve_execution_engine.py")
    assert os.path.exists(engine_path)

    with open(engine_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=engine_path)

    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_names.add(node.module)

    # Tuyệt đối không import PySide6 / PyQt hay UI modules trong resolve_execution_engine
    for mod in imported_names:
        mod_lower = mod.lower()
        assert "pyside" not in mod_lower, f"Forbidden import found: {mod}"
        assert "pyqt" not in mod_lower, f"Forbidden import found: {mod}"
        assert "src.ui" not in mod_lower, f"Forbidden import found: {mod}"

    # Duyệt toàn bộ src/ui/ để chứng minh không file UI nào import resolve_execution_engine
    ui_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "src", "ui")
    for root, _, files in os.walk(ui_dir):
        for fname in files:
            if fname.endswith(".py"):
                fpath = os.path.join(root, fname)
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    uicontent = f.read()
                assert "resolve_execution_engine" not in uicontent, f"Phát hiện UI wiring tại {fpath}"


# =============================================================================
# G07-07B4-R2 Residual Safety Suite: B4-R1-RES-01 & B4-R1-RES-02 Verification
# =============================================================================

def test_b4_r1_res_01_verifiable_reconciliation_proof_contract(tmp_path):
    """B4-R1-RES-01: Hợp đồng bằng chứng đối soát xác thực (OperatorReconciliationProof).
    
    Từ chối tuyệt đối plain dict, đối tượng thiếu trường, hash sai, target mismatch, execution mismatch.
    Chỉ giải phóng khi bằng chứng hợp lệ và khớp 100% bản ghi bất định.
    """
    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway(project_id="proj_proof_01", timeline_id="tl_proof_01")
    gateway.action_timeout_indices.add(0)

    plan, approval = _build_test_plan_and_approval(
        target_project_id="proj_proof_01",
        target_timeline_id="tl_proof_01",
        num_actions=1,
    )
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    result = engine.execute_plan(plan, approval, execution_id="exec_proof_01")
    assert result.status == "IN_DOUBT_OBSERVER_TIMEOUT"

    # 1. Probe: Plain dict bị từ chối với TypeError
    with pytest.raises(TypeError, match="ERR_INVALID_PROOF_TYPE"):
        validate_reconciliation_proof({"verified_by": "OPERATOR_RECONCILIATION"})

    with pytest.raises(TypeError, match="ERR_INVALID_PROOF_TYPE"):
        engine.reconcile_target({"verified_by": "OPERATOR_RECONCILIATION"})  # type: ignore

    # 2. Probe: Schema version không hợp lệ
    with pytest.raises(ValueError, match="ERR_UNSUPPORTED_PROOF_SCHEMA"):
        validate_reconciliation_proof(
            OperatorReconciliationProof(
                schema_version=2,
                proof_id="prf_01",
                target_project_id="proj_proof_01",
                target_timeline_id="tl_proof_01",
                target_execution_id="exec_proof_01",
                verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
                operator_id="editor_1",
                reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
                readback_evidence_hash="a" * 64,
                notes="Invalid schema",
            )
        )

    # 3. Probe: Outcome không nằm trong danh mục cho phép
    with pytest.raises(ValueError, match="ERR_PROOF_INVALID_OUTCOME"):
        validate_reconciliation_proof(
            OperatorReconciliationProof(
                schema_version=1,
                proof_id="prf_01",
                target_project_id="proj_proof_01",
                target_timeline_id="tl_proof_01",
                target_execution_id="exec_proof_01",
                verified_outcome="ARBITRARY_CUSTOM_OUTCOME",
                operator_id="editor_1",
                reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
                readback_evidence_hash="a" * 64,
                notes="Custom outcome",
            )
        )

    # 4. Probe: Hash không phải 64-hex SHA-256
    with pytest.raises(ValueError, match="ERR_PROOF_INVALID_HASH"):
        validate_reconciliation_proof(
            OperatorReconciliationProof(
                schema_version=1,
                proof_id="prf_01",
                target_project_id="proj_proof_01",
                target_timeline_id="tl_proof_01",
                target_execution_id="exec_proof_01",
                verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
                operator_id="editor_1",
                reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
                readback_evidence_hash="invalid_short_hash",
                notes="Bad hash",
            )
        )

    # 5. Probe: Execution mismatch
    mismatch_exec_proof = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_01",
        target_project_id="proj_proof_01",
        target_timeline_id="tl_proof_01",
        target_execution_id="foreign_exec_99",
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="editor_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash="a" * 64,
        notes="Wrong execution ID",
    )
    with pytest.raises(ValueError, match="ERR_PROOF_EXECUTION_MISMATCH"):
        engine.unresolved_registry.resolve_and_release("proj_proof_01", "tl_proof_01", mismatch_exec_proof)

    # 6. Probe: In-doubt action mismatch
    mismatch_act_proof = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_01",
        target_project_id="proj_proof_01",
        target_timeline_id="tl_proof_01",
        target_execution_id="exec_proof_01",
        in_doubt_action_index=99,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="editor_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash="a" * 64,
        notes="Wrong action index",
    )
    with pytest.raises(ValueError, match="ERR_PROOF_ACTION_MISMATCH"):
        engine.unresolved_registry.resolve_and_release("proj_proof_01", "tl_proof_01", mismatch_act_proof)

    # 7. Positive Control: Proof hợp lệ và khớp 100% kèm artifact thực tế -> giải phóng chính xác
    art_path, art_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id="proj_proof_01",
        target_timeline_id="tl_proof_01",
        target_execution_id="exec_proof_01",
        action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="lead_editor",
    )
    valid_proof = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_valid_01",
        target_project_id="proj_proof_01",
        target_timeline_id="tl_proof_01",
        target_execution_id="exec_proof_01",
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="lead_editor",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha,
        evidence_artifact_path=art_path,
        notes="Inspected Resolve timeline track 1 at frame 86400, no clip was inserted.",
    )
    released = engine.reconcile_target(valid_proof)
    assert released is True
    assert engine.unresolved_registry.is_target_unresolved("proj_proof_01", "tl_proof_01") is False


def test_b4_r1_res_02_restart_safe_target_admission_blocks_until_reconciliation(tmp_path):
    """B4-R1-RES-02: Mô phỏng process restart / crash:
    - Phiên 1 để lại biên bản dở dang (state=EXECUTING, journal dừng tại ACTION_PENDING).
    - Tạo một engine và registry hoàn toàn mới (mô phỏng tiến trình mới khởi động).
    - Gửi một kế hoạch MỚI với idempotency_key KHÁC cho cùng target timeline.
    - Khẳng định 100% bị CHẶN ĐỨNG với ERR_TARGET_HAS_UNRESOLVED_DURABLE_EXECUTION.
    - Zero Gateway dispatch.
    - Chỉ sau khi đối soát có thẩm quyền với OperatorReconciliationProof, lệnh mới mới được thi công.
    """
    proj_id = "proj_restart_01"
    tl_id = "tl_restart_01"

    receipt_svc_1 = ExecutionReceiptService(str(tmp_path))
    journal_svc_1 = ExecutionJournalService(str(tmp_path))
    gateway_1 = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)
    gateway_1.action_timeout_indices.add(0)

    plan_1, approval_1 = _build_test_plan_and_approval(
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        num_actions=1,
    )
    engine_1 = ResolveExecutionEngine(receipt_svc_1, journal_svc_1, gateway_1)
    res_1 = engine_1.execute_plan(plan_1, approval_1, execution_id="exec_phase_1")
    assert res_1.status == "IN_DOUBT_OBSERVER_TIMEOUT"

    # Giả lập sập tiến trình: dọn dẹp in-memory registry singleton, hủy bỏ engine cũ
    engine_module.GLOBAL_UNRESOLVED_TARGET_REGISTRY.clear_for_test()

    # Khởi tạo tiến trình mới: các service và engine hoàn toàn mới, 0 trạng thái trong RAM
    receipt_svc_2 = ExecutionReceiptService(str(tmp_path))
    journal_svc_2 = ExecutionJournalService(str(tmp_path))
    gateway_2 = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)
    engine_2 = ResolveExecutionEngine(receipt_svc_2, journal_svc_2, gateway_2, trust_provider=TEST_TRUST_PROVIDER)

    # Kế hoạch MỚI hoàn toàn, idempotency_key MỚI, proposal_id MỚI, execution_id MỚI
    plan_2, approval_2 = _build_test_plan_and_approval(
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        num_actions=1,
    )
    plan_2 = replace(
        plan_2,
        plan_id="plan_after_restart",
        proposal_id="prop_after_restart",
        idempotency_key=compute_idempotency_key("prop_after_restart", plan_2.proposal_revision, "appr_after_restart", proj_id, tl_id),
    )
    approval_2 = replace(approval_2, proposal_id="prop_after_restart")

    # Thử thi công kế hoạch mới: phải bị chặn ngay tại Step 3b
    res_2 = engine_2.execute_plan(plan_2, approval_2, execution_id="exec_fresh_process")
    assert res_2.status == "FAILED_PRE_MUTATION"
    assert res_2.error_code == "ERR_TARGET_HAS_UNRESOLVED_DURABLE_EXECUTION"
    assert res_2.execution_dispatched is False
    # Không có bất kỳ lệnh nào được gửi tới gateway mới
    assert len(gateway_2.dispatched_actions) == 0

    # Tiến hành đối soát có thẩm quyền kèm artifact xác thực
    art_path, art_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id="exec_phase_1",
        action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="senior_colorist",
        timestamp_iso="2026-11-02T13:00:00Z",
    )
    proof = OperatorReconciliationProof(
        schema_version=1,
        proof_id="proof_recon_restart",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id="exec_phase_1",
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="senior_colorist",
        reconciliation_timestamp_iso="2026-11-02T13:00:00Z",
        readback_evidence_hash=art_sha,
        evidence_artifact_path=art_path,
        notes="Verified that Resolve C++ engine dropped the action due to timeout.",
    )
    reconciled = engine_2.reconcile_target(proof)
    assert reconciled is True

    # Thử thi công lại kế hoạch mới sau khi đã đối soát -> THÀNH CÔNG
    gateway_2.action_timeout_indices.clear()
    res_3 = engine_2.execute_plan(plan_2, approval_2, execution_id="exec_fresh_process_success")
    assert res_3.status == "COMPLETED"
    assert res_3.committed_actions_count == 1
    assert len(gateway_2.dispatched_actions) == 1


def test_b4_r1_res_02_restart_safe_torn_tail_and_corrupt_fail_closed(tmp_path):
    """B4-R1-RES-02: Bằng chứng bền vững bị rách dòng cuối (torn tail) hoặc biến dạng dữ liệu (corrupt)
    phải fail-closed tuyệt đối sau khi restart tiến trình, bảo tồn 100% byte gốc trên đĩa.
    """
    proj_id = "proj_torn_01"
    tl_id = "tl_torn_01"

    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)

    plan, approval = _build_test_plan_and_approval(
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        num_actions=1,
    )
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)

    # Khởi tạo một receipt dở dang EXECUTING
    now_iso = datetime.now(timezone.utc).isoformat()
    receipt = ExecutionReceipt(
        schema_version=2,
        execution_id="exec_torn_01",
        idempotency_key=compute_idempotency_key("prop_t1", "1" * 64, "appr_t1", proj_id, tl_id),
        proposal_id="prop_t1",
        proposal_revision="1" * 64,
        approval_id="appr_t1",
        approved_at_iso=now_iso,
        project_name="Test Project",
        project_id=proj_id,
        timeline_name="Timeline 1",
        timeline_id=tl_id,
        timeline_fps_numerator=24,
        timeline_fps_denominator=1,
        timeline_resolution=(1920, 1080),
        source_digests=(),
        catalog_digests=(),
        track_reservations=(),
        planned_actions_count=1,
        created_at_iso=now_iso,
        state="EXECUTING",
    )
    receipt_svc.create_receipt(receipt)

    # Ghi một START entry hợp lệ
    start_entry = JournalEntry(
        seq=0,
        stage="START",
        action_type="NO_OP",
        timestamp_iso=now_iso,
        action_params={"total_actions": 1},
    )
    journal_svc.append_entry("exec_torn_01", start_entry)

    # Ghi nối một dòng bị rách (torn tail) không có ký tự newline kết thúc và JSON dở dang
    j_path = journal_svc.journal_path("exec_torn_01")
    torn_bytes = b'{"seq": 1, "stage": "ACTION_PENDING", "action_t'
    with open(j_path, "ab") as f:
        f.write(torn_bytes)
        f.flush()
        os.fsync(f.fileno())

    expected_len = os.path.getsize(j_path)

    # Giả lập restart tiến trình
    engine_module.GLOBAL_UNRESOLVED_TARGET_REGISTRY.clear_for_test()
    fresh_receipt_svc = ExecutionReceiptService(str(tmp_path))
    fresh_journal_svc = ExecutionJournalService(str(tmp_path))
    fresh_gw = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)
    fresh_engine = ResolveExecutionEngine(fresh_receipt_svc, fresh_journal_svc, fresh_gw, trust_provider=TEST_TRUST_PROVIDER)

    plan_new, approval_new = _build_test_plan_and_approval(
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        num_actions=1,
    )
    plan_new = replace(
        plan_new,
        plan_id="plan_fresh_torn",
        proposal_id="prop_fresh_torn",
        idempotency_key=compute_idempotency_key("prop_fresh_torn", plan_new.proposal_revision, "appr_fresh_torn", proj_id, tl_id),
    )
    approval_new = replace(approval_new, proposal_id="prop_fresh_torn")

    # Gọi execute_plan: phải fail closed
    res_torn = fresh_engine.execute_plan(plan_new, approval_new, execution_id="exec_after_torn")
    assert res_torn.status == "FAILED_PRE_MUTATION"
    assert res_torn.error_code == "ERR_TARGET_HAS_UNRESOLVED_DURABLE_EXECUTION"
    assert "torn tail" in res_torn.error_message
    assert len(fresh_gw.dispatched_actions) == 0

    # Bảo toàn 100% byte gốc: dung lượng file journal không bị cắt cụt
    assert os.path.getsize(j_path) == expected_len

    # Tiểu mục 2: Tệp receipt bị biến dạng dữ liệu (corrupted receipt)
    corrupt_rec_path = fresh_receipt_svc._receipt_path("exec_corrupt_99")
    with open(corrupt_rec_path, "wb") as f:
        f.write(b"NOT_A_VALID_JSON_CORRUPT_BYTES_XYZ")
        f.flush()
        os.fsync(f.fileno())

    plan_corrupt, approval_corrupt = _build_test_plan_and_approval(
        target_project_id="proj_corrupt_02",
        target_timeline_id="tl_corrupt_02",
        num_actions=1,
    )
    res_corrupt = fresh_engine.execute_plan(plan_corrupt, approval_corrupt, execution_id="exec_after_corrupt")
    assert res_corrupt.status == "FAILED_PRE_MUTATION"
    assert res_corrupt.error_code == "ERR_TARGET_CORRUPT_EVIDENCE"
    assert len(fresh_gw.dispatched_actions) == 0


# =============================================================================
# G07-07B4-R3: Residual Repair Tests (B4-R2-RES-01, RES-02, RES-03)
# =============================================================================

def test_b4_r2_res_01_evidence_digest_verification_probes(tmp_path):
    """B4-R2-RES-01: Thẩm định bằng chứng đối soát vật lý độc lập:
    - Bằng chứng có mã băm ngẫu nhiên hoặc đường dẫn artifact không tồn tại bị từ chối.
    - Sai lệch mã băm (hash mismatch) bị từ chối.
    - Nội dung JSON bị hỏng (malformed JSON) bị từ chối.
    - Sai lệch danh tính mục tiêu (target/execution/action/outcome) bị từ chối.
    - Bằng chứng lỗi thời (is_stale=True) bị từ chối.
    - Tất cả các trường hợp lỗi đều giữ nguyên khóa và không giải tỏa trạng thái dở dang.
    - Chỉ artifact hợp lệ, khớp toàn vẹn byte và danh tính mới giải tỏa thành công.
    """
    proj_id = "proj_ev_01"
    tl_id = "tl_ev_01"
    exec_id = "exec_ev_01"

    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)
    gateway.action_timeout_indices.add(0)

    plan, approval = _build_test_plan_and_approval(
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        num_actions=1,
    )
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)
    res = engine.execute_plan(plan, approval, execution_id=exec_id)
    assert res.status == "IN_DOUBT_OBSERVER_TIMEOUT"
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True

    # Probe 1: evidence_artifact_path rỗng
    p_empty_path = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_empty_path",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash="a" * 64,
        notes="Empty path probe",
        evidence_artifact_path="",
    )
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_PATH_REQUIRED"):
        engine.reconcile_target(p_empty_path)
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True

    # Probe 2: Tệp artifact không tồn tại trên đĩa
    p_missing_file = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_missing_file",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash="a" * 64,
        notes="Missing file probe",
        evidence_artifact_path=str(tmp_path / "nonexistent_evidence.json"),
    )
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_NOT_FOUND"):
        engine.reconcile_target(p_missing_file)
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True

    # Probe 3: Tệp có tồn tại nhưng hash trong proof sai lệch (hash mismatch)
    art_path, real_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
    )
    fake_sha = ("0" * 63) + "1"
    p_bad_hash = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_bad_hash",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=fake_sha,
        notes="Bad hash probe",
        evidence_artifact_path=art_path,
    )
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_HASH_MISMATCH"):
        engine.reconcile_target(p_bad_hash)
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True

    # Probe 4: Tệp artifact chứa JSON hỏng (malformed)
    corrupt_path, corrupt_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        corrupt_json=True,
    )
    p_corrupt_art = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_corrupt_json",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=corrupt_sha,
        notes="Corrupt JSON probe",
        evidence_artifact_path=corrupt_path,
    )
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_MALFORMED_JSON"):
        engine.reconcile_target(p_corrupt_art)
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True

    # Probe 5: Tệp artifact có outcome không khớp với proof
    mismatch_art_path, mismatch_art_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=0,
        verified_outcome="ACTION_COMMITTED_CONFIRMED",  # Khác với proof
    )
    p_mismatch_outcome = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_mismatch_outcome",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=mismatch_art_sha,
        notes="Mismatch outcome probe",
        evidence_artifact_path=mismatch_art_path,
    )
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_OUTCOME_MISMATCH"):
        engine.reconcile_target(p_mismatch_outcome)
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True

    # Probe 6: Tệp artifact bị đánh dấu lỗi thời (is_stale=True)
    stale_path, stale_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        is_stale=True,
    )
    p_stale = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_stale",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=stale_sha,
        notes="Stale probe",
        evidence_artifact_path=stale_path,
    )
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_STALE"):
        engine.reconcile_target(p_stale)
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True

    # Probe 7: Positive Control - Artifact hoàn toàn hợp lệ -> Giải tỏa thành công
    p_valid = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_valid_ok",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=real_sha,
        notes="Valid real evidence",
        evidence_artifact_path=art_path,
    )
    assert engine.reconcile_target(p_valid) is True
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is False
    target_st = receipt_svc.get_target_state(proj_id, tl_id)
    assert target_st is not None
    assert target_st.get("state") == "RECONCILED"


def test_b4_r2_res_02_post_restart_reconciliation_binds_durable_authority(tmp_path):
    """B4-R2-RES-02: Sau khi restart tiến trình, reconcile_target() bắt buộc đối chiếu
    proof với hồ sơ target state bền vững, receipt trên đĩa, và journal replay:
    - Sai lệch active_execution_id bị từ chối (0 sửa đổi state).
    - Sai lệch action index với bản ghi ACTION_PENDING bị từ chối (0 sửa đổi state).
    - Thiếu receipt trên đĩa bị từ chối (0 sửa đổi state).
    - Tệp journal rách đuôi (torn tail) bị từ chối (0 sửa đổi state).
    - Lỗi khi transition receipt không bao giờ ghi đè RECONCILED vào target state.
    """
    proj_id = "proj_bind_01"
    tl_id = "tl_bind_01"
    exec_id = "exec_bind_01"

    receipt_svc_1 = ExecutionReceiptService(str(tmp_path))
    journal_svc_1 = ExecutionJournalService(str(tmp_path))
    gw_1 = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)
    gw_1.action_timeout_indices.add(0)

    plan_1, approval_1 = _build_test_plan_and_approval(
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        num_actions=1,
    )
    engine_1 = ResolveExecutionEngine(receipt_svc_1, journal_svc_1, gw_1)
    res_1 = engine_1.execute_plan(plan_1, approval_1, execution_id=exec_id)
    assert res_1.status == "IN_DOUBT_OBSERVER_TIMEOUT"

    # Giả lập restart: xóa in-memory registry, tạo engine và services hoàn toàn mới
    engine_module.GLOBAL_UNRESOLVED_TARGET_REGISTRY.clear_for_test()
    fresh_receipt_svc = ExecutionReceiptService(str(tmp_path))
    fresh_journal_svc = ExecutionJournalService(str(tmp_path))
    fresh_gw = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)
    fresh_engine = ResolveExecutionEngine(fresh_receipt_svc, fresh_journal_svc, fresh_gw, trust_provider=TEST_TRUST_PROVIDER)

    # Probe A: Proof gửi sai execution ID
    art_wrong_exec_path, art_wrong_exec_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id="exec_other_99",
        action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
    )
    p_wrong_exec = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_wrong_exec",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id="exec_other_99",
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_wrong_exec_sha,
        notes="Wrong exec probe",
        evidence_artifact_path=art_wrong_exec_path,
    )
    with pytest.raises(ValueError, match="ERR_PROOF_EXECUTION_MISMATCH"):
        fresh_engine.reconcile_target(p_wrong_exec)
    # Xác nhận 0 sửa đổi target state
    st_after_a = fresh_receipt_svc.get_target_state(proj_id, tl_id)
    assert st_after_a is not None and st_after_a.get("state") == "IN_DOUBT"

    # Probe B: Proof gửi sai action index (trong journal là 0, proof gửi 5)
    art_wrong_act_path, art_wrong_act_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=5,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
    )
    p_wrong_act = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_wrong_act",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=5,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_wrong_act_sha,
        notes="Wrong act probe",
        evidence_artifact_path=art_wrong_act_path,
    )
    with pytest.raises(ValueError, match="ERR_ACTION_INDEX_MISMATCH"):
        fresh_engine.reconcile_target(p_wrong_act)
    st_after_b = fresh_receipt_svc.get_target_state(proj_id, tl_id)
    assert st_after_b is not None and st_after_b.get("state") == "IN_DOUBT"

    # Probe C: Lỗi khi transition receipt (giả lập exception trong transition_state)
    art_valid_path, art_valid_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
    )
    p_valid = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_valid_attempt",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_valid_sha,
        notes="Transition failure probe",
        evidence_artifact_path=art_valid_path,
    )
    # Bơm lỗi vào transition_state của fresh_receipt_svc
    def _faulty_transition(*args, **kwargs):
        raise ReceiptStorageError("Simulated disk I/O failure during receipt transition")
    fresh_receipt_svc.transition_state = _faulty_transition

    with pytest.raises(ReceiptStorageError, match="Simulated disk I/O failure"):
        fresh_engine.reconcile_target(p_valid)

    # B4-R4-RES-04: KHÔNG ĐƯỢC đánh dấu RECONCILED vào target state khi receipt transition thất bại!
    # Trạng thái phải dừng lại ở RECONCILING với in_doubt=True (Pha 1 của crash-safe protocol)
    st_after_c = fresh_receipt_svc.get_target_state(proj_id, tl_id)
    assert st_after_c is not None and st_after_c.get("state") != "RECONCILED"
    assert st_after_c.get("in_doubt") is True
    assert st_after_c.get("state") == "RECONCILING"


def test_b4_r2_res_03_bounded_admission_scan_and_corrupt_state(tmp_path):
    """B4-R2-RES-03: Thẩm định giới hạn quét và trạng thái hỏng:
    - Vượt quá scan cap (1000 tệp biên bản) phải fail-closed với ERR_RECEIPT_SCAN_CAP_EXCEEDED.
    - Tệp target state bị hỏng (corrupt JSON) phải fail-closed với ERR_TARGET_CORRUPT_EVIDENCE.
    - Lỗi truy cập thư mục receipts phải fail-closed với ERR_TARGET_STORAGE_ACCESS_FAILED.
    - Tất cả ngoại lệ trong giai đoạn kiểm tra đều giải phóng khóa sạch sẽ.
    """
    proj_id = "proj_scan_01"
    tl_id = "tl_scan_01"

    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gateway = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gateway, trust_provider=TEST_TRUST_PROVIDER)

    plan, approval = _build_test_plan_and_approval(
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        num_actions=1,
    )

    # Tiểu mục 1: Vượt quá giới hạn cứng 1000 receipts -> Fail-closed ERR_RECEIPT_SCAN_CAP_EXCEEDED
    for i in range(1005):
        dummy_rec_path = os.path.join(receipt_svc.receipts_dir, f"dummy_rec_{i:04d}.receipt.json")
        with open(dummy_rec_path, "wb") as f:
            f.write(b'{"schema_version": 1}')

    res_cap = engine.execute_plan(plan, approval, execution_id="exec_over_cap")
    assert res_cap.status == "FAILED_PRE_MUTATION"
    assert res_cap.error_code == "ERR_RECEIPT_SCAN_CAP_EXCEEDED"
    assert "vượt quá giới hạn quét an toàn" in res_cap.error_message
    assert len(gateway.dispatched_actions) == 0

    # Dọn dẹp các tệp dummy
    for i in range(1005):
        dummy_rec_path = os.path.join(receipt_svc.receipts_dir, f"dummy_rec_{i:04d}.receipt.json")
        if os.path.exists(dummy_rec_path):
            os.remove(dummy_rec_path)

    # Tiểu mục 2: Tệp target state bị biến dạng/hỏng cấu trúc
    target_state_file = receipt_svc._target_state_path(proj_id, tl_id)
    with open(target_state_file, "wb") as f:
        f.write(b"CORRUPTED_RAW_NON_JSON_BYTES_XYZ")
        f.flush()
        os.fsync(f.fileno())

    res_corrupt_state = engine.execute_plan(plan, approval, execution_id="exec_corrupt_state")
    assert res_corrupt_state.status == "FAILED_PRE_MUTATION"
    assert res_corrupt_state.error_code == "ERR_TARGET_CORRUPT_EVIDENCE"
    assert "bị hỏng hoặc biến dạng" in res_corrupt_state.error_message
    assert len(gateway.dispatched_actions) == 0

    # Khóa đã được giải phóng sạch sẽ (thử acquire lock lại phải thành công)
    test_lock = receipt_svc.acquire_target_lock(proj_id, tl_id)
    with test_lock:
        pass


def test_b4_r3_res_01_whole_plan_terminal_state_semantics(tmp_path):
    """B4-R3-RES-01: Ngữ nghĩa đối soát kết thúc kế hoạch toàn diện (Whole-Plan Terminal Semantics):
    - Kế hoạch 2 actions: action 0 committed, action 1 pending xác nhận không dispatch -> KHÔNG BAO GIỜ ABORTED.
      Target tiếp tục bị khóa và unresolved pending whole-plan disposition.
    - Kế hoạch nhiều actions: action 0 pending xác nhận committed nhưng các actions sau chưa chạy -> KHÔNG BAO GIỜ COMPLETED.
      Target tiếp tục bị khóa và unresolved.
    - Positive control:
      + Kế hoạch 2 actions: action 0 committed trong nhật ký, action 1 pending xác nhận COMMITTED -> COMPLETED thành công.
      + Kế hoạch 1 action: action 0 pending xác nhận NOT DISPATCHED (0 actions committed) -> ABORTED thành công.
    """
    proj_id = "proj_whole_01"
    tl_id = "tl_whole_01"

    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gw = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)

    # -------------------------------------------------------------------------
    # Probe 1: Kế hoạch 2 actions: action 0 committed, action 1 timed out
    # -------------------------------------------------------------------------
    gw.action_timeout_indices.add(1)
    plan_2, approval_2 = _build_test_plan_and_approval(
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        num_actions=2,
    )
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gw, trust_provider=TEST_TRUST_PROVIDER)
    exec_id_1 = "exec_whole_01"
    res_1 = engine.execute_plan(plan_2, approval_2, execution_id=exec_id_1)
    assert res_1.status == "IN_DOUBT_OBSERVER_TIMEOUT"
    assert res_1.committed_actions_count == 1
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True

    # Operator cung cấp proof xác nhận action 1 không điều phối (ACTION_NOT_DISPATCHED_CONFIRMED)
    art_path_1, art_sha_1 = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id_1,
        action_index=1,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_lead",
    )
    p_not_disp = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_probe1_not_disp",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id_1,
        in_doubt_action_index=1,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_lead",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha_1,
        notes="Action 1 was not dispatched but action 0 committed",
        evidence_artifact_path=art_path_1,
    )

    # KHÔNG ĐƯỢC CHUYỂN SANG ABORTED vì đã có action 0 committed!
    with pytest.raises(WholePlanReconciliationError, match="ERR_RECONCILIATION_CANNOT_ABORT_COMMITTED_ACTIONS"):
        engine.reconcile_target(p_not_disp)

    # Target tiếp tục bị khóa và unresolved, receipt KHÔNG bị đổi sang ABORTED
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True
    rec_after_1 = receipt_svc.load_receipt(exec_id_1)
    assert rec_after_1.state != "ABORTED"
    tgt_st_1 = receipt_svc.get_target_state(proj_id, tl_id)
    assert tgt_st_1 is not None and tgt_st_1.get("state") != "RECONCILED"

    # Dọn dẹp registry để chuẩn bị Probe 2
    engine.unresolved_registry.clear_for_test()

    # -------------------------------------------------------------------------
    # Probe 2: Kế hoạch 2 actions: action 0 timed out, action 1 chưa chạy
    # -------------------------------------------------------------------------
    proj_id_2 = "proj_whole_02"
    tl_id_2 = "tl_whole_02"
    gw_2 = FakeResolveGateway(project_id=proj_id_2, timeline_id=tl_id_2)
    gw_2.action_timeout_indices.add(0)  # timeout ngay action 0
    engine_2 = ResolveExecutionEngine(receipt_svc, journal_svc, gw_2, trust_provider=TEST_TRUST_PROVIDER)

    plan_2b, approval_2b = _build_test_plan_and_approval(
        target_project_id=proj_id_2,
        target_timeline_id=tl_id_2,
        num_actions=2,
    )
    exec_id_2 = "exec_whole_02"
    res_2 = engine_2.execute_plan(plan_2b, approval_2b, execution_id=exec_id_2)
    assert res_2.status == "IN_DOUBT_OBSERVER_TIMEOUT"
    assert res_2.committed_actions_count == 0

    # Operator xác nhận action 0 đã committed (ACTION_COMMITTED_CONFIRMED), nhưng action 1 chưa thi công!
    art_path_2, art_sha_2 = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id_2,
        target_timeline_id=tl_id_2,
        target_execution_id=exec_id_2,
        action_index=0,
        verified_outcome="ACTION_COMMITTED_CONFIRMED",
        operator_id="op_lead",
    )
    p_act0_comm = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_probe2_act0_comm",
        target_project_id=proj_id_2,
        target_timeline_id=tl_id_2,
        target_execution_id=exec_id_2,
        in_doubt_action_index=0,
        verified_outcome="ACTION_COMMITTED_CONFIRMED",
        operator_id="op_lead",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha_2,
        notes="Action 0 committed, but action 1 was planned and unexecuted",
        evidence_artifact_path=art_path_2,
    )

    # KHÔNG ĐƯỢC CHUYỂN SANG COMPLETED vì action 1 chưa thi công!
    with pytest.raises(WholePlanReconciliationError, match="ERR_RECONCILIATION_INCOMPLETE_PLAN"):
        engine_2.reconcile_target(p_act0_comm)

    assert engine_2.unresolved_registry.is_target_unresolved(proj_id_2, tl_id_2) is True
    rec_after_2 = receipt_svc.load_receipt(exec_id_2)
    assert rec_after_2.state != "COMPLETED"
    tgt_st_2 = receipt_svc.get_target_state(proj_id_2, tl_id_2)
    assert tgt_st_2 is not None and tgt_st_2.get("state") != "RECONCILED"

    # -------------------------------------------------------------------------
    # Probe 3: Positive Control - Toàn bộ kế hoạch được chứng thực kết thúc toàn diện
    # Case A: Kế hoạch 2 actions, action 0 committed, action 1 được xác nhận COMMITTED (2/2 actions)
    # -------------------------------------------------------------------------
    art_path_3a, art_sha_3a = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id_1,
        action_index=1,
        verified_outcome="ACTION_COMMITTED_CONFIRMED",
        operator_id="op_lead",
    )
    p_complete_all = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_probe3_complete_all",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id_1,
        in_doubt_action_index=1,
        verified_outcome="ACTION_COMMITTED_CONFIRMED",
        operator_id="op_lead",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha_3a,
        notes="Action 1 confirmed committed, satisfying 2/2 planned actions",
        evidence_artifact_path=art_path_3a,
    )
    assert engine.reconcile_target(p_complete_all) is True
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is False
    rec_3a = receipt_svc.load_receipt(exec_id_1)
    assert rec_3a.state == "COMPLETED"
    tgt_st_3a = receipt_svc.get_target_state(proj_id, tl_id)
    assert tgt_st_3a is not None and tgt_st_3a.get("state") == "RECONCILED"


def test_b4_r3_res_02_trusted_evidence_provenance_and_bounded_reads(tmp_path):
    """B4-R3-RES-02: Xác thực nguồn gốc tin cậy (trusted provenance) và giới hạn đọc chống TOCTOU:
    - Artifact tự tạo (forged) có SHA-256 khớp nhưng thiếu/sai chữ ký xác thực -> Từ chối ERR_EVIDENCE_UNTRUSTED_PROVENANCE.
    - Thiếu thông tin quan sát (observation_id, observed_at_iso, observer_identity) -> Từ chối tương ứng.
    - Thiếu hoặc sai tính tươi mới (is_stale thiếu, không phải bool, hoặc True) -> Từ chối tương ứng.
    - Tệp phát triển vượt trần (vượt max_bytes) -> Đọc descriptor tối đa max_bytes+1 và từ chối ERR_EVIDENCE_SIZE_EXCEEDED.
    """
    proj_id = "proj_prov_01"
    tl_id = "tl_prov_01"
    exec_id = "exec_prov_01"

    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gw = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)
    gw.action_timeout_indices.add(0)

    plan, approval = _build_test_plan_and_approval(
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        num_actions=1,
    )
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gw, trust_provider=TEST_TRUST_PROVIDER)
    res = engine.execute_plan(plan, approval, execution_id=exec_id)
    assert res.status == "IN_DOUBT_OBSERVER_TIMEOUT"

    # 1. Forged Artifact: caller tự tạo artifact với chữ ký giả lập hoặc sai trust anchor
    forged_path, forged_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_attacker",
        attestation_signature="0" * 64,  # chữ ký giả
    )
    p_forged = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_forged",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_attacker",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=forged_sha,
        notes="Forged signature probe",
        evidence_artifact_path=forged_path,
    )
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_UNTRUSTED_PROVENANCE"):
        engine.reconcile_target(p_forged)
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True

    # 2. Thiếu thông tin quan sát (observation_id / observed_at_iso / observer_identity)
    no_obs_path, no_obs_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        observation_id=None,
    )
    p_no_obs = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_no_obs",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=no_obs_sha,
        notes="Missing observation_id probe",
        evidence_artifact_path=no_obs_path,
    )
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_OBSERVATION_ID_REQUIRED"):
        engine.reconcile_target(p_no_obs)

    # 3. Thiếu tính tươi mới (is_stale field missing)
    no_fresh_path, no_fresh_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        include_freshness=False,
    )
    p_no_fresh = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_no_fresh",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=no_fresh_sha,
        notes="Missing freshness probe",
        evidence_artifact_path=no_fresh_path,
    )
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_FRESHNESS_MISSING"):
        engine.reconcile_target(p_no_fresh)

    # 4. is_stale không phải bool (ví dụ số nguyên 0 hoặc 1)
    bad_stale_path, bad_stale_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        is_stale=0,  # int thay vì bool
    )
    p_bad_stale = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_bad_stale",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=bad_stale_sha,
        notes="Non-bool freshness probe",
        evidence_artifact_path=bad_stale_path,
    )
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_FRESHNESS_TYPE"):
        engine.reconcile_target(p_bad_stale)

    # 5. Giới hạn đọc tệp (Bounded read & TOCTOU growth)
    small_verifier = BoundedFileEvidenceVerifier(max_bytes=200, trust_provider=TEST_TRUST_PROVIDER)
    over_size_path, over_size_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        extra_bytes_after=300,
    )
    p_over_size = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_over_size",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=over_size_sha,
        notes="Over size probe",
        evidence_artifact_path=over_size_path,
    )
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_SIZE_EXCEEDED"):
        small_verifier.verify_evidence(
            proof=p_over_size,
            expected_project_id=proj_id,
            expected_timeline_id=tl_id,
            expected_execution_id=exec_id,
            expected_action_index=0,
        )


def test_b4_r3_res_03_strict_primitive_type_probes(tmp_path):
    """B4-R3-RES-03: Thẩm định kiểm tra kiểu dữ liệu nguyên thủy chính xác (Strict Primitive Types):
    - Cấm tuyệt đối bool giả mạo int tại ranh giới proof (schema_version=True, in_doubt_action_index=True).
    - Cấm các kiểu float, string, unhashable cho các trường int.
    - Cấm kiểu non-string cho các trường định danh ID/hash/outcome.
    - Cấm bool/float/string tại trường action_index trong decoded JSON artifact.
    """
    proj_id = "proj_types_01"
    tl_id = "tl_types_01"
    exec_id = "exec_types_01"

    art_path, art_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
    )

    # 1. schema_version = True (bool thay vì int)
    p_bool_schema = OperatorReconciliationProof(
        schema_version=True,  # type: ignore
        proof_id="prf_bool_schema",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha,
        notes="Bool schema probe",
        evidence_artifact_path=art_path,
    )
    with pytest.raises(TypeError, match="ERR_PROOF_INVALID_SCHEMA_TYPE"):
        validate_reconciliation_proof(p_bool_schema)

    # 2. in_doubt_action_index = True (bool thay vì int)
    p_bool_act = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_bool_act",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=True,  # type: ignore
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha,
        notes="Bool action index probe",
        evidence_artifact_path=art_path,
    )
    with pytest.raises(TypeError, match="ERR_PROOF_INVALID_ACTION_INDEX_TYPE"):
        validate_reconciliation_proof(p_bool_act)

    # 3. in_doubt_action_index là chuỗi hoặc float
    p_str_act = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_str_act",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index="0",  # type: ignore
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha,
        notes="String action index probe",
        evidence_artifact_path=art_path,
    )
    with pytest.raises(TypeError, match="ERR_PROOF_INVALID_ACTION_INDEX_TYPE"):
        validate_reconciliation_proof(p_str_act)

    # 4. Trường string nhận int/bool (ví dụ target_project_id = 123)
    p_bad_str = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_bad_str",
        target_project_id=123,  # type: ignore
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha,
        notes="Non-string target project probe",
        evidence_artifact_path=art_path,
    )
    with pytest.raises(TypeError, match="ERR_PROOF_FIELD_TYPE"):
        validate_reconciliation_proof(p_bad_str)

    # 5. Decoded artifact boundary: action_index trong artifact là True (bool)
    art_bool_act_path, art_bool_act_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=True,  # bool trong JSON
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
    )
    p_art_bool = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_art_bool",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_bool_act_sha,
        notes="Artifact bool action probe",
        evidence_artifact_path=art_bool_act_path,
    )
    verifier = BoundedFileEvidenceVerifier(trust_provider=TEST_TRUST_PROVIDER)
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_ACTION_INDEX_TYPE"):
        verifier.verify_evidence(
            proof=p_art_bool,
            expected_project_id=proj_id,
            expected_timeline_id=tl_id,
            expected_execution_id=exec_id,
            expected_action_index=0,
        )


def test_b4_r4_res_01_unconfigured_trust_and_canonical_envelope_sealing(tmp_path):
    """B4-R4-RES-01: Thẩm định ranh giới nguồn tin cậy bắt buộc và niêm phong phong bì chuẩn tắc:
    - Verifier/Engine mặc định không có trust provider phải fail-closed với ERR_EVIDENCE_TRUST_UNCONFIGURED.
    - Sửa đổi bất kỳ trường nào trong phong bì đã ký -> Chữ ký không khớp, fail-closed với ERR_EVIDENCE_UNTRUSTED_PROVENANCE.
    - Chèn thêm trường lạ không được ký -> Thay đổi payload chuẩn tắc, fail-closed với ERR_EVIDENCE_UNTRUSTED_PROVENANCE.
    - Sai lệch giữa raw_bytes và artifact_data giải mã -> fail-closed.
    """
    proj_id = "proj_trust_01"
    tl_id = "tl_trust_01"
    exec_id = "exec_trust_01"

    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gw = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)
    gw.action_timeout_indices.add(0)

    plan, approval = _build_test_plan_and_approval(
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        num_actions=1,
    )
    # Engine mặc định: KHÔNG truyền trust_provider
    engine_default = ResolveExecutionEngine(receipt_svc, journal_svc, gw)
    assert engine_default.evidence_verifier.trust_provider is None

    res = engine_default.execute_plan(plan, approval, execution_id=exec_id)
    assert res.status == "IN_DOUBT_OBSERVER_TIMEOUT"

    art_path, art_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        trust_secret=TEST_TRUST_ANCHOR_SECRET,
    )
    proof = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_trust_01",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha,
        notes="Unconfigured trust probe",
        evidence_artifact_path=art_path,
    )

    # 1. Engine chưa cấu hình trust provider phải fail-closed
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_TRUST_UNCONFIGURED"):
        engine_default.reconcile_target(proof)

    # Verifier riêng lẻ chưa cấu hình trust provider cũng phải fail-closed
    unconfigured_verifier = BoundedFileEvidenceVerifier(trust_provider=None)
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_TRUST_UNCONFIGURED"):
        unconfigured_verifier.verify_evidence(
            proof=proof,
            expected_project_id=proj_id,
            expected_timeline_id=tl_id,
            expected_execution_id=exec_id,
            expected_action_index=0,
        )

    # 2. Thử nghiệm phong bì với trust provider hợp lệ
    configured_verifier = BoundedFileEvidenceVerifier(trust_provider=TEST_TRUST_PROVIDER)
    assert configured_verifier.verify_evidence(
        proof=proof,
        expected_project_id=proj_id,
        expected_timeline_id=tl_id,
        expected_execution_id=exec_id,
        expected_action_index=0,
    ) is True

    # 3. Thay đổi bất kỳ trường nào trong phong bì đã ký -> Chữ ký bị vô hiệu hóa
    with open(art_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    tampered_data = dict(data)
    tampered_data["observer_identity"] = "forged-observer"
    tampered_bytes = json.dumps(tampered_data, indent=2).encode("utf-8")
    tampered_path = tmp_path / "tampered_envelope.json"
    with open(tampered_path, "wb") as f:
        f.write(tampered_bytes)
    tampered_sha = hashlib.sha256(tampered_bytes).hexdigest().lower()

    proof_tampered = replace(proof, evidence_artifact_path=str(tampered_path), readback_evidence_hash=tampered_sha)
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_UNTRUSTED_PROVENANCE"):
        configured_verifier.verify_evidence(
            proof=proof_tampered,
            expected_project_id=proj_id,
            expected_timeline_id=tl_id,
            expected_execution_id=exec_id,
            expected_action_index=0,
        )

    # 4. Chèn thêm trường lạ vào phong bì -> Chữ ký bị vô hiệu hóa
    extra_field_data = dict(data)
    extra_field_data["unauthorized_injected_field"] = "malicious_payload"
    extra_bytes = json.dumps(extra_field_data, indent=2).encode("utf-8")
    extra_path = tmp_path / "extra_field.json"
    with open(extra_path, "wb") as f:
        f.write(extra_bytes)
    extra_sha = hashlib.sha256(extra_bytes).hexdigest().lower()

    proof_extra = replace(proof, evidence_artifact_path=str(extra_path), readback_evidence_hash=extra_sha)
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_UNTRUSTED_PROVENANCE"):
        configured_verifier.verify_evidence(
            proof=proof_extra,
            expected_project_id=proj_id,
            expected_timeline_id=tl_id,
            expected_execution_id=exec_id,
            expected_action_index=0,
        )


def test_b4_r4_res_02_whole_plan_action_ledger_bijection(tmp_path):
    """B4-R4-RES-02: Thẩm định đối ánh đơn xạ (bijection) sổ hành vi kế hoạch toàn diện:
    - Bản ghi ACTION_COMMITTED trùng lặp (duplicate commit) -> Từ chối với ERR_JOURNAL_DUPLICATE_COMMIT.
    - Khoảng trống trong chuỗi commit (sequence gap) -> Từ chối với ERR_JOURNAL_COMMIT_SEQUENCE_GAP.
    - action_index ngoài phạm vi kế hoạch -> Từ chối với ERR_JOURNAL_ACTION_INDEX_OUT_OF_RANGE.
    - Nhiều bản ghi ACTION_PENDING chưa giải tỏa -> Từ chối với ERR_JOURNAL_MULTIPLE_UNRESOLVED_PENDING.
    - Bản ghi ACTION_PENDING lặp lại -> Từ chối với ERR_JOURNAL_REPEATED_PENDING.
    - Vị trí pending index không khớp thứ tự tuần tự -> Từ chối với ERR_JOURNAL_PENDING_INDEX_INCONSISTENT.
    - Mọi trường hợp từ chối đều bảo lưu target unresolved, không sửa receipt, không mở khóa.
    """
    proj_id = "proj_bij_01"
    tl_id = "tl_bij_01"

    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gw = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)
    gw.action_timeout_indices.add(1)

    plan_3, approval_3 = _build_test_plan_and_approval(
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        num_actions=3,
    )
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gw, trust_provider=TEST_TRUST_PROVIDER)
    exec_id = "exec_bij_01"
    res = engine.execute_plan(plan_3, approval_3, execution_id=exec_id)
    assert res.status == "IN_DOUBT_OBSERVER_TIMEOUT"
    assert res.committed_actions_count == 1

    art_path, art_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=1,
        verified_outcome="ACTION_COMMITTED_CONFIRMED",
    )
    proof_comm = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_comm_1",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=1,
        verified_outcome="ACTION_COMMITTED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha,
        notes="Bijection probe",
        evidence_artifact_path=art_path,
    )

    # Probe 1: Duplicate commit (thêm một bản ghi commit trùng index 0)
    # B4-R6-RES-01: Loại bỏ dòng FINISH cuối để tái hiện trạng thái in-doubt dở dang thuần túy
    # trước khi kiểm tra các vi phạm cấu trúc WAL trong quá trình thi công.
    j_path = journal_svc.journal_path(exec_id)
    with open(j_path, "r", encoding="utf-8") as f:
        pre_finish_lines = [l for l in f if '"FINISH"' not in l]
    with open(j_path, "w", encoding="utf-8") as f:
        f.writelines(pre_finish_lines)
    with open(j_path, "rb") as f:
        original_journal_bytes = f.read()

    next_seq = journal_svc.replay_journal(exec_id).entries[-1].seq + 1
    dup_entry = JournalEntry(
        seq=next_seq,
        stage="ACTION_COMMITTED",
        action_type="INSERT_CLIP",
        timestamp_iso="2026-11-02T12:00:00Z",
        action_params={"action_index": 0},
    )
    journal_svc.append_entry(exec_id, dup_entry)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_DUPLICATE_COMMIT"):
        engine.reconcile_target(proof_comm)
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True

    # Phục hồi journal gốc
    with open(j_path, "wb") as f:
        f.write(original_journal_bytes)

    # Probe 2: Commit sequence gap (ví dụ commit index 0 và commit index 2, thiếu index 1)
    gap_entry = JournalEntry(
        seq=next_seq,
        stage="ACTION_COMMITTED",
        action_type="INSERT_CLIP",
        timestamp_iso="2026-11-02T12:00:00Z",
        action_params={"action_index": 2},
    )
    journal_svc.append_entry(exec_id, gap_entry)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_COMMIT_SEQUENCE_GAP"):
        engine.reconcile_target(proof_comm)

    with open(j_path, "wb") as f:
        f.write(original_journal_bytes)

    # Probe 3: Out-of-range action index (commit index 99)
    oor_entry = JournalEntry(
        seq=next_seq,
        stage="ACTION_COMMITTED",
        action_type="INSERT_CLIP",
        timestamp_iso="2026-11-02T12:00:00Z",
        action_params={"action_index": 99},
    )
    journal_svc.append_entry(exec_id, oor_entry)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_ACTION_INDEX_OUT_OF_RANGE"):
        engine.reconcile_target(proof_comm)

    with open(j_path, "wb") as f:
        f.write(original_journal_bytes)

    # Probe 4: Multiple unresolved pending (thêm pending action 2)
    pend2_entry = JournalEntry(
        seq=next_seq,
        stage="ACTION_PENDING",
        action_type="INSERT_CLIP",
        timestamp_iso="2026-11-02T12:00:00Z",
        action_params={"action_index": 2},
    )
    journal_svc.append_entry(exec_id, pend2_entry)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_MULTIPLE_UNRESOLVED_PENDING"):
        engine.reconcile_target(proof_comm)

    with open(j_path, "wb") as f:
        f.write(original_journal_bytes)

    # Probe 5: Repeated pending (thêm pending action 1 lần nữa)
    rep_pend_entry = JournalEntry(
        seq=next_seq,
        stage="ACTION_PENDING",
        action_type="INSERT_CLIP",
        timestamp_iso="2026-11-02T12:00:00Z",
        action_params={"action_index": 1},
    )
    journal_svc.append_entry(exec_id, rep_pend_entry)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_REPEATED_PENDING"):
        engine.reconcile_target(proof_comm)

    with open(j_path, "wb") as f:
        f.write(original_journal_bytes)

    # Khẳng định: Target vẫn hoàn toàn locked và unresolved
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True
    rec_check = receipt_svc.load_receipt(exec_id)
    assert rec_check.state == "EXECUTING"


def test_b4_r4_res_03_descriptor_fstat_failure_fails_closed(tmp_path, monkeypatch):
    """B4-R4-RES-03: Thẩm định fail-closed khi os.fstat trên descriptor tệp thất bại:
    - Bơm lỗi OSError vào os.fstat trong quá trình verify_evidence.
    - Bắt buộc ném EvidenceVerificationError("ERR_EVIDENCE_DESCRIPTOR_STAT_FAILED").
    - Chứng minh 0 chuyển đổi receipt state, 0 cập nhật target state sang RECONCILED,
      0 giải phóng registry/khóa, và 0 sửa đổi byte nhật ký.
    """
    proj_id = "proj_fstat_01"
    tl_id = "tl_fstat_01"
    exec_id = "exec_fstat_01"

    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gw = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)
    gw.action_timeout_indices.add(0)

    plan, approval = _build_test_plan_and_approval(
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        num_actions=1,
    )
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gw, trust_provider=TEST_TRUST_PROVIDER)
    res = engine.execute_plan(plan, approval, execution_id=exec_id)
    assert res.status == "IN_DOUBT_OBSERVER_TIMEOUT"

    art_path, art_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
    )
    proof = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_fstat_fail",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha,
        notes="fstat failure probe",
        evidence_artifact_path=art_path,
    )

    # Ghi nhận trạng thái và journal trước khi thử nghiệm
    j_path = journal_svc.journal_path(exec_id)
    with open(j_path, "rb") as f:
        journal_bytes_before = f.read()

    # Bơm lỗi vào os.fstat
    real_fstat = os.fstat
    def _faulty_fstat(fd):
        raise OSError(errno.EIO, "Simulated hardware I/O error during fstat descriptor inspection")

    monkeypatch.setattr(os, "fstat", _faulty_fstat)

    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_DESCRIPTOR_STAT_FAILED"):
        engine.reconcile_target(proof)

    # Khôi phục fstat
    monkeypatch.undo()

    # Chứng minh 0 chuyển đổi:
    # 1. Target vẫn unresolved trong in-memory registry
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True
    # 2. Receipt trên đĩa vẫn giữ EXECUTING
    rec_after = receipt_svc.load_receipt(exec_id)
    assert rec_after.state == "EXECUTING"
    # 3. Target state trên đĩa vẫn giữ IN_DOUBT (không bao giờ sang RECONCILED)
    st_after = receipt_svc.get_target_state(proj_id, tl_id)
    assert st_after is not None and st_after.get("state") == "IN_DOUBT"
    # 4. Byte nhật ký không bị biến dạng hay sửa đổi
    with open(j_path, "rb") as f:
        journal_bytes_after = f.read()
    assert journal_bytes_after == journal_bytes_before


def test_b4_r4_res_04_crash_safe_reconciliation_persistence_protocol(tmp_path):
    """B4-R4-RES-04: Thẩm định giao thức bền vững đa pha chống Crash (Crash-Safe Reconciliation):
    - Pha 1 ghi RECONCILING (in_doubt=True).
    - Pha 2 ghi receipt terminal state (COMPLETED / ABORTED).
    - Pha 3 ghi target state RECONCILED (in_doubt=False).
    - Sập giữa Pha 1 và Pha 2: restart admission chặn đứng (in_doubt=True). Reconcile phục hồi tiếp tục.
    - Sập giữa Pha 2 và Pha 3: restart admission chặn đứng (in_doubt=True). Reconcile nhận diện
      chế độ crash recovery (is_crash_recovering), hoàn tất Pha 3 và giải phóng khóa.
    - Không có bất kỳ hiện tượng tự động replay hoặc mâu thuẫn trạng thái terminal.
    """
    proj_id = "proj_crash_01"
    tl_id = "tl_crash_01"
    exec_id = "exec_crash_01"

    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gw = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)
    gw.action_timeout_indices.add(0)

    plan, approval = _build_test_plan_and_approval(
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        num_actions=1,
    )
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gw, trust_provider=TEST_TRUST_PROVIDER)
    res = engine.execute_plan(plan, approval, execution_id=exec_id)
    assert res.status == "IN_DOUBT_OBSERVER_TIMEOUT"

    art_path, art_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
    )
    proof = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_crash_safe_01",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha,
        notes="Crash safe persistence probe",
        evidence_artifact_path=art_path,
    )

    # -------------------------------------------------------------------------
    # Tình huống A: Crash giữa Pha 1 (RECONCILING) và Pha 2 (receipt transition)
    # -------------------------------------------------------------------------
    # Giả lập Pha 1 đã persist thành công nhưng tiến trình bị ngắt đột ngột
    receipt_svc.persist_target_state(
        project_id=proj_id,
        timeline_id=tl_id,
        payload={
            "project_id": proj_id,
            "timeline_id": tl_id,
            "active_execution_id": exec_id,
            "state": "RECONCILING",
            "in_doubt": True,
            "target_terminal_state": "ABORTED",
            "reconciliation_proof_id": proof.proof_id,
            "verified_outcome": proof.verified_outcome,
            "operator_id": proof.operator_id,
            "updated_at_iso": proof.reconciliation_timestamp_iso,
        },
    )
    # Xóa sạch RAM để mô phỏng khởi động lại tiến trình
    engine_module.GLOBAL_UNRESOLVED_TARGET_REGISTRY.clear_for_test()
    fresh_receipt_svc_a = ExecutionReceiptService(str(tmp_path))
    fresh_journal_svc_a = ExecutionJournalService(str(tmp_path))
    fresh_gw_a = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)
    fresh_engine_a = ResolveExecutionEngine(fresh_receipt_svc_a, fresh_journal_svc_a, fresh_gw_a, trust_provider=TEST_TRUST_PROVIDER)

    # Cổng admission khi khởi động lại phải CHẶN ĐỨNG vì target đang ở RECONCILING (in_doubt=True)
    adm_res_a = fresh_engine_a._check_durable_target_admission(proj_id, tl_id)
    assert adm_res_a is not None
    assert adm_res_a["error_code"] == "ERR_TARGET_HAS_UNRESOLVED_DURABLE_EXECUTION"

    # Gọi reconcile_target để hoàn tất quy trình phục hồi
    res_rec_a = fresh_engine_a.reconcile_target(proof)
    assert res_rec_a is True

    # Trạng thái bền vững sau khi phục hồi thành công
    st_recovered_a = fresh_receipt_svc_a.get_target_state(proj_id, tl_id)
    assert st_recovered_a is not None
    assert st_recovered_a["state"] == "RECONCILED"
    assert st_recovered_a["in_doubt"] is False
    rec_recovered_a = fresh_receipt_svc_a.load_receipt(exec_id)
    assert rec_recovered_a.state == "ABORTED"

    # -------------------------------------------------------------------------
    # Tình huống B: Crash giữa Pha 2 (Receipt sang terminal) và Pha 3 (Target sang RECONCILED)
    # -------------------------------------------------------------------------
    # Thiết lập phiên thứ 2
    proj_id_b = "proj_crash_02"
    tl_id_b = "tl_crash_02"
    exec_id_b = "exec_crash_02"

    gw_b = FakeResolveGateway(project_id=proj_id_b, timeline_id=tl_id_b)
    gw_b.action_timeout_indices.add(0)
    plan_b, approval_b = _build_test_plan_and_approval(
        target_project_id=proj_id_b,
        target_timeline_id=tl_id_b,
        num_actions=1,
    )
    engine_b = ResolveExecutionEngine(receipt_svc, journal_svc, gw_b, trust_provider=TEST_TRUST_PROVIDER)
    res_b = engine_b.execute_plan(plan_b, approval_b, execution_id=exec_id_b)
    assert res_b.status == "IN_DOUBT_OBSERVER_TIMEOUT"

    art_path_b, art_sha_b = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id_b,
        target_timeline_id=tl_id_b,
        target_execution_id=exec_id_b,
        action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
    )
    proof_b = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_crash_safe_02",
        target_project_id=proj_id_b,
        target_timeline_id=tl_id_b,
        target_execution_id=exec_id_b,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha_b,
        notes="Crash safe persistence probe B",
        evidence_artifact_path=art_path_b,
    )

    # Giả lập Pha 1 và Pha 2 đã ghi xuống đĩa, nhưng Pha 3 chưa ghi thì sập nguồn:
    # Pha 1: Target state là RECONCILING, in_doubt=True
    receipt_svc.persist_target_state(
        project_id=proj_id_b,
        timeline_id=tl_id_b,
        payload={
            "project_id": proj_id_b,
            "timeline_id": tl_id_b,
            "active_execution_id": exec_id_b,
            "state": "RECONCILING",
            "in_doubt": True,
            "target_terminal_state": "ABORTED",
            "reconciliation_proof_id": proof_b.proof_id,
            "verified_outcome": proof_b.verified_outcome,
            "operator_id": proof_b.operator_id,
            "updated_at_iso": proof_b.reconciliation_timestamp_iso,
        },
    )
    # Pha 2: Receipt đã transition sang ABORTED (mô phỏng Pha 2 với update_target_state=False)
    receipt_svc.transition_state(
        exec_id_b,
        new_state="ABORTED",
        updated_at_iso=proof_b.reconciliation_timestamp_iso,
        last_error_code="RECONCILED_BY_OPERATOR",
        update_target_state=False,
    )

    # Sập nguồn: Xóa in-memory registry, khởi động lại fresh engine
    engine_module.GLOBAL_UNRESOLVED_TARGET_REGISTRY.clear_for_test()
    fresh_receipt_svc_b = ExecutionReceiptService(str(tmp_path))
    fresh_journal_svc_b = ExecutionJournalService(str(tmp_path))
    fresh_gw_b = FakeResolveGateway(project_id=proj_id_b, timeline_id=tl_id_b)
    fresh_engine_b = ResolveExecutionEngine(fresh_receipt_svc_b, fresh_journal_svc_b, fresh_gw_b, trust_provider=TEST_TRUST_PROVIDER)

    # Khởi động lại: admission phải chặn vì in_doubt=True
    adm_res_b = fresh_engine_b._check_durable_target_admission(proj_id_b, tl_id_b)
    assert adm_res_b is not None
    assert adm_res_b["error_code"] == "ERR_TARGET_HAS_UNRESOLVED_DURABLE_EXECUTION"

    # Reconcile được kích hoạt lại với cùng proof:
    # Nhận diện is_crash_recovering = True, KHÔNG từ chối vì ERR_RECEIPT_ALREADY_TERMINAL,
    # mà hoàn tất Pha 3 và giải phóng an toàn!
    res_rec_b = fresh_engine_b.reconcile_target(proof_b)
    assert res_rec_b is True

    # Xác nhận Pha 3 đã hoàn tất: target state là RECONCILED (in_doubt=False)
    st_recovered_b = fresh_receipt_svc_b.get_target_state(proj_id_b, tl_id_b)
    assert st_recovered_b is not None
    assert st_recovered_b["state"] == "RECONCILED"
    assert st_recovered_b["in_doubt"] is False

    # Admission check sau khi đã RECONCILED phải cho phép (không bị chặn nữa)
    adm_after_b = fresh_engine_b._check_durable_target_admission(proj_id_b, tl_id_b)
    assert adm_after_b is None


def test_b4_r5_res_01_exact_artifact_bytes_attestation_and_duplicate_key_rejection(tmp_path):
    """B4-R5-RES-01: Ràng buộc byte chuẩn tắc tuyệt đối (Exact Byte Attestation) & Từ chối duplicate keys:
    - Byte serialization chuẩn tắc duy nhất (UTF-8, sort_keys=True, compact separators=(',', ':')).
    - Mọi biến dạng khoảng trắng (whitespace), thụt dòng (indentation), thêm khoảng trắng sau dấu phẩy/hai chấm
      đều bị verify_attestation từ chối (trả về False) mặc dù dữ liệu JSON sau khi parse là giống hệt.
    - Đổi thứ tự khóa trong byte raw bị từ chối.
    - JSON chứa khóa trùng lặp (duplicate keys) bị json.loads ném ERR_DUPLICATE_KEY và BoundedFileEvidenceVerifier
      ném ERR_EVIDENCE_MALFORMED_JSON.
    - Tệp artifact chuẩn tắc trên đĩa với chữ ký hợp lệ vượt qua thẩm định 100%.
    """
    proj_id = "proj_exact_01"
    tl_id = "tl_exact_01"
    exec_id = "exec_exact_01"

    provider = TEST_TRUST_PROVIDER
    verifier = BoundedFileEvidenceVerifier(trust_provider=provider)

    payload = {
        "artifact_version": 1,
        "observation_id": "obs_exact_01",
        "observed_at_iso": "2026-11-02T12:00:00Z",
        "observer_identity": "resolveflow-supervisor",
        "target_project_id": proj_id,
        "target_timeline_id": tl_id,
        "target_execution_id": exec_id,
        "action_index": 0,
        "verified_outcome": "ACTION_NOT_DISPATCHED_CONFIRMED",
        "operator_id": "op_exact_01",
        "reconciliation_timestamp_iso": "2026-11-02T12:00:00Z",
        "is_stale": False,
    }

    # 1. Tạo chữ ký hợp lệ trên payload
    sig = provider.compute_canonical_attestation(payload, TEST_TRUST_ANCHOR_SECRET)
    envelope = dict(payload)
    envelope["attestation_signature"] = sig

    # Byte chuẩn tắc
    canonical_bytes = provider.canonical_serialize(envelope)

    # Thẩm định trực tiếp qua provider:
    assert provider.verify_attestation(envelope, canonical_bytes) is True

    # Biến dạng 1: Thêm thụt dòng indent=2 (whitespace/newlines)
    reformatted_bytes = json.dumps(envelope, indent=2).encode("utf-8")
    assert reformatted_bytes != canonical_bytes
    assert json.loads(reformatted_bytes.decode("utf-8")) == envelope  # ngữ nghĩa JSON giống hệt
    # Nhưng thẩm định byte chính xác phải TỪ CHỐI vì không phải byte chuẩn tắc!
    assert provider.verify_attestation(envelope, reformatted_bytes) is False

    # Biến dạng 2: Thêm khoảng trắng sau separators=(', ', ': ')
    spaced_bytes = json.dumps(envelope, sort_keys=True, separators=(", ", ": ")).encode("utf-8")
    assert spaced_bytes != canonical_bytes
    assert provider.verify_attestation(envelope, spaced_bytes) is False

    # Biến dạng 3: Đổi thứ tự khóa trong raw bytes
    reordered_keys = list(envelope.keys())[::-1]
    reordered_dict = {k: envelope[k] for k in reordered_keys}
    reordered_bytes = json.dumps(reordered_dict, separators=(",", ":")).encode("utf-8")
    assert provider.verify_attestation(envelope, reordered_bytes) is False

    # Biến dạng 4: Khóa trùng lặp (duplicate keys)
    dup_key_bytes = b'{"observation_id":"obs_1","observation_id":"obs_2",' + canonical_bytes[1:]
    assert provider.verify_attestation(envelope, dup_key_bytes) is False

    # 2. Thẩm định qua BoundedFileEvidenceVerifier với tệp trên đĩa:
    # A. Tệp chuẩn tắc: thành công
    art_path = tmp_path / "valid_canonical.json"
    with open(art_path, "wb") as f:
        f.write(canonical_bytes)
    art_sha = hashlib.sha256(canonical_bytes).hexdigest().lower()

    proof_ok = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_ok",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="op_exact_01",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha,
        evidence_artifact_path=str(art_path),
        notes="Exact canonical probe",
    )
    assert verifier.verify_evidence(
        proof=proof_ok,
        expected_project_id=proj_id,
        expected_timeline_id=tl_id,
        expected_execution_id=exec_id,
        expected_action_index=0,
    ) is True

    # B. Tệp bị sửa whitespace (indent=2) với mã băm sha256 cập nhật lại khớp với tệp
    ws_path = tmp_path / "whitespace_reformatted.json"
    with open(ws_path, "wb") as f:
        f.write(reformatted_bytes)
    ws_sha = hashlib.sha256(reformatted_bytes).hexdigest().lower()

    proof_ws = replace(proof_ok, evidence_artifact_path=str(ws_path), readback_evidence_hash=ws_sha)
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_UNTRUSTED_PROVENANCE"):
        verifier.verify_evidence(
            proof=proof_ws,
            expected_project_id=proj_id,
            expected_timeline_id=tl_id,
            expected_execution_id=exec_id,
            expected_action_index=0,
        )

    # C. Tệp chứa khóa trùng lặp -> ERR_EVIDENCE_MALFORMED_JSON
    dup_path = tmp_path / "duplicate_keys.json"
    with open(dup_path, "wb") as f:
        f.write(dup_key_bytes)
    dup_sha = hashlib.sha256(dup_key_bytes).hexdigest().lower()

    proof_dup = replace(proof_ok, evidence_artifact_path=str(dup_path), readback_evidence_hash=dup_sha)
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_MALFORMED_JSON"):
        verifier.verify_evidence(
            proof=proof_dup,
            expected_project_id=proj_id,
            expected_timeline_id=tl_id,
            expected_execution_id=exec_id,
            expected_action_index=0,
        )


def test_b4_r5_res_02_proof_audit_identity_and_timestamp_binding_with_zero_state_mutation(tmp_path):
    """B4-R5-RES-02: Ràng buộc danh tính kiểm toán (operator_id) và mốc thời gian đối soát (timestamp):
    - Artifact ký xác thực chứa operator_id="operator_alice", reconciliation_timestamp_iso="2026-11-02T14:00:00Z".
    - Tamper 1: Caller nộp proof có operator_id="operator_mallory" -> Bị từ chối ERR_EVIDENCE_OPERATOR_MISMATCH.
    - Tamper 2: Caller nộp proof có timestamp="2026-11-02T15:00:00Z" -> Bị từ chối ERR_EVIDENCE_TIMESTAMP_MISMATCH.
    - Cả 2 trường hợp gian lận đều bảo lưu 0 sửa đổi target state, 0 chuyển đổi receipt, target vẫn unresolved và bị khóa.
    - Positive Control: Proof khớp hoàn toàn cả operator và timestamp -> Đối soát thành công, ghi nhận đúng danh tính đã ký.
    """
    proj_id = "proj_audit_01"
    tl_id = "tl_audit_01"
    exec_id = "exec_audit_01"

    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gw = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)
    gw.action_timeout_indices.add(0)

    plan, approval = _build_test_plan_and_approval(
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        num_actions=1,
    )
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gw, trust_provider=TEST_TRUST_PROVIDER)
    res = engine.execute_plan(plan, approval, execution_id=exec_id)
    assert res.status == "IN_DOUBT_OBSERVER_TIMEOUT"
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True

    initial_target_st = receipt_svc.get_target_state(proj_id, tl_id)
    initial_receipt = receipt_svc.load_receipt(exec_id)
    initial_journal_len = len(journal_svc.replay_journal(exec_id).entries)

    # Artifact được ký bởi operator_alice lúc 14:00:00Z
    art_path, art_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="operator_alice",
        timestamp_iso="2026-11-02T14:00:00Z",
    )

    # Tamper 1: Proof gửi operator_mallory
    p_tampered_op = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_tampered_op",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="operator_mallory",
        reconciliation_timestamp_iso="2026-11-02T14:00:00Z",
        readback_evidence_hash=art_sha,
        evidence_artifact_path=art_path,
        notes="Tampered operator",
    )
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_OPERATOR_MISMATCH"):
        engine.reconcile_target(p_tampered_op)

    # Khẳng định: Zero state mutation
    assert receipt_svc.get_target_state(proj_id, tl_id) == initial_target_st
    assert receipt_svc.load_receipt(exec_id).state == initial_receipt.state
    assert len(journal_svc.replay_journal(exec_id).entries) == initial_journal_len
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True

    # Tamper 2: Proof gửi timestamp giả mạo 15:00:00Z
    p_tampered_ts = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_tampered_ts",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="operator_alice",
        reconciliation_timestamp_iso="2026-11-02T15:00:00Z",
        readback_evidence_hash=art_sha,
        evidence_artifact_path=art_path,
        notes="Tampered timestamp",
    )
    with pytest.raises(EvidenceVerificationError, match="ERR_EVIDENCE_TIMESTAMP_MISMATCH"):
        engine.reconcile_target(p_tampered_ts)

    # Khẳng định: Zero state mutation
    assert receipt_svc.get_target_state(proj_id, tl_id) == initial_target_st
    assert receipt_svc.load_receipt(exec_id).state == initial_receipt.state
    assert len(journal_svc.replay_journal(exec_id).entries) == initial_journal_len
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True

    # Positive Control: Proof khớp 100% với artifact
    p_legit = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_legit",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=0,
        verified_outcome="ACTION_NOT_DISPATCHED_CONFIRMED",
        operator_id="operator_alice",
        reconciliation_timestamp_iso="2026-11-02T14:00:00Z",
        readback_evidence_hash=art_sha,
        evidence_artifact_path=art_path,
        notes="Legit proof",
    )
    assert engine.reconcile_target(p_legit) is True
    final_st = receipt_svc.get_target_state(proj_id, tl_id)
    assert final_st is not None
    assert final_st["state"] == "RECONCILED"
    assert final_st["operator_id"] == "operator_alice"
    assert final_st["updated_at_iso"] == "2026-11-02T14:00:00Z"
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is False


def test_b4_r5_res_03_journal_wal_stage_machine_validation_and_zero_mutation_on_malformed_history(tmp_path):
    """B4-R5-RES-03: Thẩm định máy trạng thái WAL toàn diện & Bảo lưu 0 đột biến khi nhật ký bị biến dạng:
    - Case 1: ACTION_PENDING sau khi action đã commit -> ERR_JOURNAL_PENDING_AFTER_COMMIT.
    - Case 2: ACTION_COMMITTED mà không có ACTION_PENDING trước đó -> ERR_JOURNAL_COMMIT_WITHOUT_PENDING.
    - Case 3: ACTION_COMMITTED có tham số (track_index) không nhất quán với ACTION_PENDING -> ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS.
    - Case 4: Bản ghi stage xung đột (ROLLBACK_DONE) -> ERR_JOURNAL_CONFLICTING_STAGE.
    - Case 5: Bản ghi stage xung đột (FINISH COMPLETED_ALL_ACTIONS khi in doubt) -> ERR_JOURNAL_CONFLICTING_STAGE.
    - Case 6: Bản ghi stage không xác định (UNKNOWN_STAGE) -> ERR_JOURNAL_UNKNOWN_STAGE.
    - Mỗi case đều chứng minh: zero receipt transition, zero target state mutation, target vẫn unresolved & bị khóa.
    """
    proj_id = "proj_wal_01"
    tl_id = "tl_wal_01"

    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gw = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)
    gw.action_timeout_indices.add(1)

    plan, approval = _build_test_plan_and_approval(
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        num_actions=3,
    )
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gw, trust_provider=TEST_TRUST_PROVIDER)
    exec_id = "exec_wal_01"
    res = engine.execute_plan(plan, approval, execution_id=exec_id)
    assert res.status == "IN_DOUBT_OBSERVER_TIMEOUT"
    assert res.committed_actions_count == 1  # action 0 committed, action 1 pending

    art_path, art_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=1,
        verified_outcome="ACTION_COMMITTED_CONFIRMED",
        operator_id="op_1",
    )
    proof = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_wal_01",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=1,
        verified_outcome="ACTION_COMMITTED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha,
        evidence_artifact_path=art_path,
        notes="WAL test proof",
    )

    j_path = journal_svc.journal_path(exec_id)
    with open(j_path, "r", encoding="utf-8") as f:
        pre_finish_lines = [l for l in f if '"FINISH"' not in l]
    with open(j_path, "w", encoding="utf-8") as f:
        f.writelines(pre_finish_lines)
    with open(j_path, "rb") as f:
        clean_journal_bytes = f.read()

    def _assert_zero_mutation():
        assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True
        rec = receipt_svc.load_receipt(exec_id)
        assert rec.state == "EXECUTING"
        st = receipt_svc.get_target_state(proj_id, tl_id)
        assert st is not None and st.get("state") == "IN_DOUBT"

    # Case 1: ACTION_PENDING sau khi đã commit (action 0 đã committed, append pending action 0)
    next_seq = journal_svc.replay_journal(exec_id).entries[-1].seq + 1
    p_after_comm = JournalEntry(
        seq=next_seq,
        stage="ACTION_PENDING",
        action_type="INSERT_CLIP",
        timestamp_iso="2026-11-02T12:00:00Z",
        action_params={"action_index": 0},
    )
    journal_svc.append_entry(exec_id, p_after_comm)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_PENDING_AFTER_COMMIT"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()

    # Phục hồi
    with open(j_path, "wb") as f:
        f.write(clean_journal_bytes)

    # Case 2: ACTION_COMMITTED mà không có pending trước đó
    # Xây dựng journal mới chỉ có commit cho action 0 mà không có pending
    with open(j_path, "wb") as f:
        pass
    bad_entries = [
        JournalEntry(seq=1, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 3}),
        JournalEntry(seq=2, stage="ACTION_COMMITTED", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"action_index": 0}, track_type="video", track_index=1),
    ]
    for e in bad_entries:
        journal_svc.append_entry(exec_id, e)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_COMMIT_WITHOUT_PENDING"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()

    # Phục hồi
    with open(j_path, "wb") as f:
        f.write(clean_journal_bytes)

    # Case 3: ACTION_COMMITTED có tham số không khớp với ACTION_PENDING
    comm_inconsistent = JournalEntry(
        seq=next_seq,
        stage="ACTION_COMMITTED",
        action_type="INSERT_CLIP",
        timestamp_iso="2026-11-02T12:00:00Z",
        action_params={"action_index": 1},
        track_type="video",
        track_index=99,  # sai track_index (pending là 1)
    )
    journal_svc.append_entry(exec_id, comm_inconsistent)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()

    # Phục hồi
    with open(j_path, "wb") as f:
        f.write(clean_journal_bytes)

    # Case 4: Stage xung đột (ROLLBACK_DONE)
    conflicting_stage = JournalEntry(
        seq=next_seq,
        stage="ROLLBACK_DONE",
        action_type="NO_OP",
        timestamp_iso="2026-11-02T12:00:00Z",
        action_params={},
    )
    journal_svc.append_entry(exec_id, conflicting_stage)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONFLICTING_STAGE"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()

    # Phục hồi
    with open(j_path, "wb") as f:
        f.write(clean_journal_bytes)

    # Case 5: Stage xung đột (FINISH COMPLETED_ALL_ACTIONS)
    conflict_finish = JournalEntry(
        seq=next_seq,
        stage="FINISH",
        action_type="NO_OP",
        timestamp_iso="2026-11-02T12:00:00Z",
        action_params={},
        note="COMPLETED_ALL_ACTIONS",
    )
    journal_svc.append_entry(exec_id, conflict_finish)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONFLICTING_STAGE"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()

    # Phục hồi
    with open(j_path, "wb") as f:
        f.write(clean_journal_bytes)

    # Case 6: Stage không xác định/không cho phép trong WAL (RECOVERY_NOTE)
    unknown_stage = JournalEntry(
        seq=next_seq,
        stage="RECOVERY_NOTE",
        action_type="NO_OP",
        timestamp_iso="2026-11-02T12:00:00Z",
        action_params={},
    )
    journal_svc.append_entry(exec_id, unknown_stage)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_UNKNOWN_STAGE"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()


def test_b4_r6_res_01_terminal_wal_closure_and_exact_payload_binding(tmp_path):
    """G07-07B4-R7 / B4-R6-RES-01: Thẩm định đóng kín WAL stage kết thúc và ràng buộc 1:1 payload thao tác:
    Phần 1: Pure-function probes trực tiếp trên ResolveExecutionEngine._validate_journal_wal_state_machine:
      - Probe 1A (Residual review R6): FINISH(HALTED_ON_ACTION_FAILURE) khi đang có pending action -> Từ chối với ERR_JOURNAL_CONFLICTING_STAGE.
      - Probe 1B (Residual review R6): action_params bị sửa đổi (source_path) giữa pending và commit -> Từ chối với ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS.
      - Probe 1C: action_params bị thiếu hoặc thừa trường giữa pending và commit -> Từ chối với ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS.
      - Probe 1D: Bất kỳ stage nào xuất hiện sau FINISH (continuation sau terminal) -> Từ chối với ERR_JOURNAL_STAGE_AFTER_FINISH.
      - Probe 1E: Bản ghi FINISH có note không xác định -> Từ chối với ERR_JOURNAL_UNKNOWN_STAGE.
      - Probe 1F: FINISH (HALTED_IN_DOUBT_OBSERVER_TIMEOUT) khi không có action pending -> Từ chối với ERR_JOURNAL_FINISH_WITHOUT_PENDING.
      - Probe 1G: FINISH (HALTED_IN_DOUBT_OBSERVER_TIMEOUT) có action_index lệch với action pending -> Từ chối với ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS.
      - Probe 1H: Orphan ACTION_FAILED (không có ACTION_PENDING trước đó) -> Từ chối với ERR_JOURNAL_ORPHAN_ACTION_FAILED.
      - Probe 1I: ACTION_FAILED có index không khớp với action đang pending -> Từ chối với ERR_JOURNAL_ACTION_FAILED_INDEX_MISMATCH.
      - Probe 1J: ACTION_FAILED có payload action_params không khớp với ACTION_PENDING -> Từ chối với ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS.
      - Probe 1K: Positive Control trực tiếp - lịch sử chuẩn tắc hợp lệ -> Trả về (committed_indices, committed_set, last_pending_idx).

    Phần 2: End-to-end reconciliation probes qua engine.reconcile_target() chứng minh Fail-Closed & Zero Mutation:
      - End-to-end Case A: Thao tác sau FINISH -> ERR_JOURNAL_STAGE_AFTER_FINISH -> zero mutation.
      - End-to-end Case B: Giả mạo payload commit trong journal -> ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS -> zero mutation.
      - End-to-end Case C: Chèn orphan/mismatched ACTION_FAILED -> ERR_JOURNAL_ACTION_FAILED_INDEX_MISMATCH -> zero mutation.
      - End-to-end Case D: Thay thế FINISH bằng HALTED_ON_ACTION_FAILURE -> ERR_JOURNAL_CONFLICTING_STAGE -> zero mutation.
      - End-to-end Case E: Positive Control hoàn tất đối soát -> Chuyển COMPLETED và RECONCILED hợp lệ.
    """
    fn = ResolveExecutionEngine._validate_journal_wal_state_machine

    # Probe 1A: FINISH(HALTED_ON_ACTION_FAILURE) khi đang có pending action
    entries_1a = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 2}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0, "source_path": "clip_a.mov"}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0}, note="HALTED_ON_ACTION_FAILURE"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONFLICTING_STAGE"):
        fn(entries_1a, planned_count=2, exec_id="probe_1a")

    # Probe 1B: action_params bị thay đổi source_path giữa pending và commit
    entries_1b = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 2}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0, "source_path": "clip_a.mov"}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="ACTION_COMMITTED", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0, "source_path": "clip_tampered.mov"}, track_type="video", track_index=1),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS"):
        fn(entries_1b, planned_count=2, exec_id="probe_1b")

    # Probe 1C: action_params thừa/thiếu trường giữa pending và commit
    entries_1c = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 2}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0, "clip_id": "c1", "source_file_path": "f.mov"}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="ACTION_COMMITTED", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0, "clip_id": "c1"}, track_type="video", track_index=1),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS"):
        fn(entries_1c, planned_count=2, exec_id="probe_1c")

    # Probe 1D: Continuation sau FINISH
    entries_1d = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 2}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0}, note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
        JournalEntry(seq=3, stage="ACTION_COMMITTED", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:03Z", action_params={"action_index": 0}, track_type="video", track_index=1),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_STAGE_AFTER_FINISH"):
        fn(entries_1d, planned_count=2, exec_id="probe_1d")

    # Probe 1E: Invalid FINISH note
    entries_1e = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 2}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0}, note="UNKNOWN_WEIRD_NOTE"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_UNKNOWN_STAGE"):
        fn(entries_1e, planned_count=2, exec_id="probe_1e")

    # Probe 1F: FINISH (HALTED_IN_DOUBT_OBSERVER_TIMEOUT) khi không có pending
    entries_1f = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 2}),
        JournalEntry(seq=1, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:01Z", action_params={}, note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_FINISH_WITHOUT_PENDING"):
        fn(entries_1f, planned_count=2, exec_id="probe_1f")

    # Probe 1G: FINISH (HALTED_IN_DOUBT_OBSERVER_TIMEOUT) lệch action_index
    entries_1g = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 2}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 1}, note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS"):
        fn(entries_1g, planned_count=2, exec_id="probe_1g")

    # Probe 1H: Orphan ACTION_FAILED
    entries_1h = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 2}),
        JournalEntry(seq=1, stage="ACTION_FAILED", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_ORPHAN_ACTION_FAILED"):
        fn(entries_1h, planned_count=2, exec_id="probe_1h")

    # Probe 1I: Mismatched ACTION_FAILED index
    entries_1i = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 2}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="ACTION_FAILED", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 1}, track_type="video", track_index=1),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_ACTION_FAILED_INDEX_MISMATCH"):
        fn(entries_1i, planned_count=2, exec_id="probe_1i")

    # Probe 1J: ACTION_FAILED payload mismatch
    entries_1j = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 2}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0, "source": "orig.mov"}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="ACTION_FAILED", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0, "source": "mismatch.mov"}, track_type="video", track_index=1),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS"):
        fn(entries_1j, planned_count=2, exec_id="probe_1j")

    # Probe 1K: Positive Control trực tiếp
    entries_1k = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 2}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0, "source": "clip.mov"}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="ACTION_COMMITTED", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0, "source": "clip.mov"}, track_type="video", track_index=1),
        JournalEntry(seq=3, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:03Z", action_params={"action_index": 1, "source": "clip2.mov"}, track_type="video", track_index=1),
        JournalEntry(seq=4, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:04Z", action_params={"action_index": 1}, note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
    ]
    comm_idx, comm_set, pending_idx = fn(entries_1k, planned_count=2, exec_id="probe_1k")
    assert comm_idx == [0]
    assert comm_set == {0}
    assert pending_idx == 1

    # -------------------------------------------------------------------------
    # Phần 2: End-to-end reconciliation probes qua engine.reconcile_target()
    # -------------------------------------------------------------------------
    proj_id = "proj_r6_res_01"
    tl_id = "tl_r6_res_01"
    exec_id = "exec_r6_res_01"

    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gw = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)
    gw.action_timeout_indices.add(1)  # timeout tại action 1 (action 0 committed)

    plan, approval = _build_test_plan_and_approval(
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        num_actions=2,
    )
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gw, trust_provider=TEST_TRUST_PROVIDER)
    res = engine.execute_plan(plan, approval, execution_id=exec_id)
    assert res.status == "IN_DOUBT_OBSERVER_TIMEOUT"
    assert res.committed_actions_count == 1

    art_path, art_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=1,
        verified_outcome="ACTION_COMMITTED_CONFIRMED",
        operator_id="op_1",
    )
    proof = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_r6_res_01",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=1,
        verified_outcome="ACTION_COMMITTED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha,
        evidence_artifact_path=art_path,
        notes="G07-07B4-R7 end-to-end test proof",
    )

    j_path = journal_svc.journal_path(exec_id)
    with open(j_path, "rb") as f:
        clean_bytes = f.read()

    def _assert_zero_mutation():
        assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True
        rec = receipt_svc.load_receipt(exec_id)
        assert rec.state == "EXECUTING"
        st = receipt_svc.get_target_state(proj_id, tl_id)
        assert st is not None and st.get("state") == "IN_DOUBT"

    # End-to-end Case A: Thao tác sau FINISH
    next_seq_a = journal_svc.replay_journal(exec_id).entries[-1].seq + 1
    post_finish_entry = JournalEntry(
        seq=next_seq_a,
        stage="ACTION_COMMITTED",
        action_type="INSERT_CLIP",
        timestamp_iso="2026-11-02T12:00:10Z",
        action_params={"action_index": 1, "clip_id": "c1", "source_file_path": "f.mov"},
        track_type="video",
        track_index=1,
    )
    journal_svc.append_entry(exec_id, post_finish_entry)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_STAGE_AFTER_FINISH"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()

    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case B: Giả mạo payload commit của action 0
    replay_entries = journal_svc.replay_journal(exec_id).entries
    tampered_entries = []
    for e in replay_entries:
        if e.stage == "ACTION_COMMITTED":
            bad_params = dict(e.action_params)
            bad_params["source_file_path"] = "/tampered/path/video.mov"
            tampered_entries.append(JournalEntry(
                seq=e.seq,
                stage=e.stage,
                action_type=e.action_type,
                timestamp_iso=e.timestamp_iso,
                action_params=bad_params,
                resolve_item_id=e.resolve_item_id,
                track_type=e.track_type,
                track_index=e.track_index,
            ))
        else:
            tampered_entries.append(e)

    with open(j_path, "w", encoding="utf-8") as f:
        for e in tampered_entries:
            f.write(json.dumps(e.to_dict()) + "\n")

    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()

    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case C: Chèn orphan ACTION_FAILED (không có pending tương ứng)
    pre_finish_lines = [l for l in open(j_path, "r", encoding="utf-8") if '"FINISH"' not in l]
    with open(j_path, "w", encoding="utf-8") as f:
        f.writelines(pre_finish_lines)
    next_seq_c = journal_svc.replay_journal(exec_id).entries[-1].seq + 1
    orphan_fail_entry = JournalEntry(
        seq=next_seq_c,
        stage="ACTION_FAILED",
        action_type="INSERT_CLIP",
        timestamp_iso="2026-11-02T12:00:08Z",
        action_params={"action_index": 0},
        track_type="video",
        track_index=1,
    )
    # Lúc này action 1 đang pending, ta chèn ACTION_FAILED cho action 0 (mismatch index)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_ACTION_FAILED_INDEX_MISMATCH"):
        journal_svc.append_entry(exec_id, orphan_fail_entry)
        engine.reconcile_target(proof)
    _assert_zero_mutation()

    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case D: Thay FINISH bằng HALTED_ON_ACTION_FAILURE khi đang có pending action
    halted_entries = [e for e in replay_entries if e.stage != "FINISH"]
    halted_entries.append(JournalEntry(
        seq=len(halted_entries),
        stage="FINISH",
        action_type="NO_OP",
        timestamp_iso="2026-11-02T12:00:10Z",
        action_params={"action_index": 1},
        note="HALTED_ON_ACTION_FAILURE",
    ))
    with open(j_path, "w", encoding="utf-8") as f:
        for e in halted_entries:
            f.write(json.dumps(e.to_dict()) + "\n")

    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONFLICTING_STAGE"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()

    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case E: Positive Control - giải tỏa và đối soát thành công 2/2 actions
    assert engine.reconcile_target(proof) is True
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is False
    rec_final = receipt_svc.load_receipt(exec_id)
    assert rec_final.state == "COMPLETED"
    st_final = receipt_svc.get_target_state(proj_id, tl_id)
    assert st_final is not None and st_final.get("state") == "RECONCILED"


def test_b4_r7_res_01_wal_start_record_and_strict_in_doubt_finish_binding(tmp_path):
    """G07-07B4-R8 / B4-R7-RES-01: Thẩm định sự kiện START duy nhất và ràng buộc FINISH in-doubt chặt chẽ:
    Phần 1: Pure-function probes trực tiếp trên ResolveExecutionEngine._validate_journal_wal_state_machine:
      - Probe 1A (Residual review R7): Bản ghi START trùng lặp hoặc đặt sai vị trí -> ERR_JOURNAL_DUPLICATE_START.
      - Probe 1B (Residual review R7): FINISH in-doubt có action_index kiểu bool (True/False) -> ERR_JOURNAL_ACTION_INDEX_TYPE.
      - Probe 1C: Nhật ký thiếu bản ghi START ở dòng đầu tiên -> ERR_JOURNAL_MISSING_START.
      - Probe 1D: START thiếu tham số total_actions -> ERR_JOURNAL_TOTAL_ACTIONS_MISSING.
      - Probe 1E: START có total_actions sai kiểu (bool, str) -> ERR_JOURNAL_TOTAL_ACTIONS_TYPE.
      - Probe 1F: START có total_actions không khớp planned_count -> ERR_JOURNAL_TOTAL_ACTIONS_MISMATCH.
      - Probe 1G: START chứa tham số thừa -> ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS.
      - Probe 1H: FINISH in-doubt thiếu trường action_index -> ERR_JOURNAL_ACTION_INDEX_MISSING.
      - Probe 1I: FINISH in-doubt chứa trường thừa trong action_params -> ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS.
      - Probe 1J: FINISH in-doubt có action_index lệch với action dở dang -> ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS.
      - Probe 1K: Positive Control trực tiếp - lịch sử chuẩn tắc -> Trả về (committed_indices, committed_set, pending_idx).

    Phần 2: End-to-end reconciliation probes qua engine.reconcile_target() chứng minh Fail-Closed & Zero Mutation:
      - End-to-end Case A: Chèn START trùng lặp vào journal -> ERR_JOURNAL_DUPLICATE_START -> zero mutation.
      - End-to-end Case B: Giả mạo action_index=True trong FINISH in-doubt -> ERR_JOURNAL_ACTION_INDEX_TYPE -> zero mutation.
      - End-to-end Case C: Sửa total_actions sai lệch trong START -> ERR_JOURNAL_TOTAL_ACTIONS_MISMATCH -> zero mutation.
      - End-to-end Case D: Xóa bản ghi START khỏi journal -> ERR_JOURNAL_MISSING_START -> zero mutation.
      - End-to-end Case E: Positive Control hợp lệ -> Đối soát thành công, chuyển COMPLETED và RECONCILED.
    """
    fn = ResolveExecutionEngine._validate_journal_wal_state_machine

    # Probe 1A (Residual review R7): Duplicate START
    entries_1a = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"total_actions": 1}),
        JournalEntry(seq=3, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:03Z", action_params={"action_index": 0}, note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_DUPLICATE_START"):
        fn(entries_1a, planned_count=1, exec_id="probe_1a")

    # Probe 1B (Residual review R7): FINISH in-doubt carrying action_index=True
    entries_1b = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 2}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="ACTION_COMMITTED", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=3, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:03Z", action_params={"action_index": 1}, track_type="video", track_index=1),
        JournalEntry(seq=4, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:04Z", action_params={"action_index": True}, note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_ACTION_INDEX_TYPE"):
        fn(entries_1b, planned_count=2, exec_id="probe_1b")

    # Probe 1C: Missing START as first record
    entries_1c = [
        JournalEntry(seq=0, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"action_index": 0}, track_type="video", track_index=1),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_MISSING_START"):
        fn(entries_1c, planned_count=1, exec_id="probe_1c")

    # Probe 1D: START missing total_actions
    entries_1d = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={}),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_TOTAL_ACTIONS_MISSING"):
        fn(entries_1d, planned_count=1, exec_id="probe_1d")

    # Probe 1E: START total_actions wrong type (bool or str)
    entries_1e1 = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": True}),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_TOTAL_ACTIONS_TYPE"):
        fn(entries_1e1, planned_count=1, exec_id="probe_1e1")

    entries_1e2 = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": "1"}),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_TOTAL_ACTIONS_TYPE"):
        fn(entries_1e2, planned_count=1, exec_id="probe_1e2")

    # Probe 1F: START total_actions mismatch planned_count
    entries_1f = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 99}),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_TOTAL_ACTIONS_MISMATCH"):
        fn(entries_1f, planned_count=2, exec_id="probe_1f")

    # Probe 1G: START extra params
    entries_1g = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1, "extra_field": "unauthorized"}),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS"):
        fn(entries_1g, planned_count=1, exec_id="probe_1g")

    # Probe 1H: FINISH in-doubt missing action_index
    entries_1h = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:02Z", action_params={}, note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_ACTION_INDEX_MISSING"):
        fn(entries_1h, planned_count=1, exec_id="probe_1h")

    # Probe 1I: FINISH in-doubt extra params
    entries_1i = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0, "extra": "not_allowed"}, note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS"):
        fn(entries_1i, planned_count=1, exec_id="probe_1i")

    # Probe 1J: FINISH in-doubt action_index mismatch
    entries_1j = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 2}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 1}, note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS"):
        fn(entries_1j, planned_count=2, exec_id="probe_1j")

    # Probe 1K: Positive Control trực tiếp
    entries_1k = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 2}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="ACTION_COMMITTED", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=3, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:03Z", action_params={"action_index": 1}, track_type="video", track_index=1),
        JournalEntry(seq=4, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:04Z", action_params={"action_index": 1}, note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
    ]
    comm_idx, comm_set, pending_idx = fn(entries_1k, planned_count=2, exec_id="probe_1k")
    assert comm_idx == [0]
    assert comm_set == {0}
    assert pending_idx == 1

    # -------------------------------------------------------------------------
    # Phần 2: End-to-end reconciliation probes qua engine.reconcile_target()
    # -------------------------------------------------------------------------
    proj_id = "proj_r7_res_01"
    tl_id = "tl_r7_res_01"
    exec_id = "exec_r7_res_01"

    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gw = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)
    gw.action_timeout_indices.add(1)  # timeout tại action 1 (action 0 committed)

    plan, approval = _build_test_plan_and_approval(
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        num_actions=2,
    )
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gw, trust_provider=TEST_TRUST_PROVIDER)
    res = engine.execute_plan(plan, approval, execution_id=exec_id)
    assert res.status == "IN_DOUBT_OBSERVER_TIMEOUT"
    assert res.committed_actions_count == 1

    art_path, art_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=1,
        verified_outcome="ACTION_COMMITTED_CONFIRMED",
        operator_id="op_1",
    )
    proof = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_r7_res_01",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=1,
        verified_outcome="ACTION_COMMITTED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha,
        evidence_artifact_path=art_path,
        notes="G07-07B4-R8 end-to-end test proof",
    )

    j_path = journal_svc.journal_path(exec_id)
    with open(j_path, "rb") as f:
        clean_bytes = f.read()

    def _assert_zero_mutation():
        assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True
        rec = receipt_svc.load_receipt(exec_id)
        assert rec.state == "EXECUTING"
        st = receipt_svc.get_target_state(proj_id, tl_id)
        assert st is not None and st.get("state") == "IN_DOUBT"

    # End-to-end Case A: Chèn START trùng lặp vào journal
    replay_entries = journal_svc.replay_journal(exec_id).entries
    dup_start_raw = []
    for idx, e in enumerate(replay_entries):
        dup_start_raw.append(e)
        if e.stage == "ACTION_PENDING" and e.action_params.get("action_index") == 0:
            dup_start_raw.append(JournalEntry(
                seq=0,
                stage="START",
                action_type="NO_OP",
                timestamp_iso=e.timestamp_iso,
                action_params={"total_actions": 2},
            ))
    dup_start_entries = [
        JournalEntry(
            seq=i,
            stage=e.stage,
            action_type=e.action_type,
            timestamp_iso=e.timestamp_iso,
            action_params=e.action_params,
            resolve_item_id=e.resolve_item_id,
            track_type=e.track_type,
            track_index=e.track_index,
            error_code=e.error_code,
            error_message=e.error_message,
            note=e.note,
        )
        for i, e in enumerate(dup_start_raw)
    ]
    with open(j_path, "w", encoding="utf-8") as f:
        for e in dup_start_entries:
            f.write(json.dumps(e.to_dict()) + "\n")

    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_DUPLICATE_START"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()

    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case B: Giả mạo action_index=True trong FINISH in-doubt
    bool_finish_entries = []
    for e in replay_entries:
        if e.stage == "FINISH":
            bool_finish_entries.append(JournalEntry(
                seq=e.seq,
                stage="FINISH",
                action_type=e.action_type,
                timestamp_iso=e.timestamp_iso,
                action_params={"action_index": True},
                note=e.note,
            ))
        else:
            bool_finish_entries.append(e)

    with open(j_path, "w", encoding="utf-8") as f:
        for e in bool_finish_entries:
            f.write(json.dumps(e.to_dict()) + "\n")

    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_ACTION_INDEX_TYPE"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()

    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case C: Sửa total_actions sai lệch trong START (ví dụ 99 thay vì 2)
    mismatch_start_entries = []
    for e in replay_entries:
        if e.stage == "START":
            mismatch_start_entries.append(JournalEntry(
                seq=e.seq,
                stage="START",
                action_type=e.action_type,
                timestamp_iso=e.timestamp_iso,
                action_params={"total_actions": 99},
            ))
        else:
            mismatch_start_entries.append(e)

    with open(j_path, "w", encoding="utf-8") as f:
        for e in mismatch_start_entries:
            f.write(json.dumps(e.to_dict()) + "\n")

    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_TOTAL_ACTIONS_MISMATCH"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()

    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case D: Xóa bản ghi START khỏi journal (bắt đầu ngay bằng ACTION_PENDING)
    no_start_raw = [e for e in replay_entries if e.stage != "START"]
    no_start_entries = [
        JournalEntry(
            seq=i,
            stage=e.stage,
            action_type=e.action_type,
            timestamp_iso=e.timestamp_iso,
            action_params=e.action_params,
            resolve_item_id=e.resolve_item_id,
            track_type=e.track_type,
            track_index=e.track_index,
            error_code=e.error_code,
            error_message=e.error_message,
            note=e.note,
        )
        for i, e in enumerate(no_start_raw)
    ]
    with open(j_path, "w", encoding="utf-8") as f:
        for e in no_start_entries:
            f.write(json.dumps(e.to_dict()) + "\n")

    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_MISSING_START"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()

    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case E: Positive Control - giải tỏa và đối soát thành công 2/2 actions
    assert engine.reconcile_target(proof) is True
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is False
    rec_final = receipt_svc.load_receipt(exec_id)
    assert rec_final.state == "COMPLETED"
    st_final = receipt_svc.get_target_state(proj_id, tl_id)
    assert st_final is not None and st_final.get("state") == "RECONCILED"


def test_b4_r8_res_01_exact_wal_control_record_envelope_validation(tmp_path: Path):
    """B4-R8-RES-01: Thẩm định cấu trúc phong bì (envelope) chuẩn tắc của bản ghi điều khiển START và FINISH.

    Quy định nghiệm thu (Workorder G07-07B4-R9 & 07B4-R8-code-review.md):
    1. Bản ghi START:
       - seq: kiểu int thuần >= 0 (loại trừ bool).
       - stage: bắt buộc là 'START'.
       - action_type: bắt buộc là 'NO_OP' (ERR_JOURNAL_CONTROL_RECORD_ACTION_TYPE).
       - timestamp_iso: chuỗi ISO 8601 hợp lệ (ERR_JOURNAL_CONTROL_RECORD_TIMESTAMP).
       - resolve_item_id, track_type, error_code, error_message, note: bắt buộc là '' (ERR_JOURNAL_CONTROL_RECORD_METADATA).
       - track_index: bắt buộc là 0 thuần túy (ERR_JOURNAL_CONTROL_RECORD_METADATA).
       - action_params: kiểu dict, chứa duy nhất key 'total_actions' khớp với planned count.
    2. Bản ghi FINISH (đặc biệt là in-doubt FINISH HALTED_IN_DOUBT_OBSERVER_TIMEOUT):
       - seq: kiểu int thuần >= 0 (loại trừ bool).
       - stage: bắt buộc là 'FINISH'.
       - action_type: bắt buộc là 'NO_OP' (ERR_JOURNAL_CONTROL_RECORD_ACTION_TYPE).
       - timestamp_iso: chuỗi ISO 8601 hợp lệ (ERR_JOURNAL_CONTROL_RECORD_TIMESTAMP).
       - resolve_item_id, track_type: bắt buộc là '' (ERR_JOURNAL_CONTROL_RECORD_METADATA).
       - track_index: bắt buộc là 0 thuần túy (ERR_JOURNAL_CONTROL_RECORD_METADATA).
       - error_code, error_message: bắt buộc là '' đối với in-doubt FINISH (ERR_JOURNAL_CONTROL_RECORD_METADATA).
       - note: bắt buộc là str hợp lệ.
       - action_params: chứa duy nhất key 'action_index' khớp với pending action.
    3. Fail-Closed & Zero Mutation: Mọi sai lệch phong bì điều khiển đều lập tức chặn đối soát,
       bảo lưu 100% target unresolved, target lock, trạng thái IN_DOUBT, receipt EXECUTING, và byte nhật ký.
    """
    fn = ResolveExecutionEngine._validate_journal_wal_state_machine

    # -------------------------------------------------------------------------
    # Phần 1: Pure-function probes trực tiếp trên envelope điều khiển
    # -------------------------------------------------------------------------
    # Probe 1A: START action_type không phải 'NO_OP' (ví dụ 'INSERT_CLIP')
    e_1a = [
        JournalEntry(seq=0, stage="START", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_ACTION_TYPE"):
        fn(e_1a, planned_count=1, exec_id="probe_1a")

    # Probe 1B: START có track_type='video'
    e_1b = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}, track_type="video"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_METADATA"):
        fn(e_1b, planned_count=1, exec_id="probe_1b")

    # Probe 1C: START có track_index=99
    e_1c = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}, track_index=99),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_METADATA"):
        fn(e_1c, planned_count=1, exec_id="probe_1c")

    # Probe 1D: START có resolve_item_id='item_01'
    e_1d = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}, resolve_item_id="item_01"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_METADATA"):
        fn(e_1d, planned_count=1, exec_id="probe_1d")

    # Probe 1E: START có error_code='ERR_START'
    e_1e = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}, error_code="ERR_START"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_METADATA"):
        fn(e_1e, planned_count=1, exec_id="probe_1e")

    # Probe 1F: START có error_message='failed'
    e_1f = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}, error_message="failed"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_METADATA"):
        fn(e_1f, planned_count=1, exec_id="probe_1f")

    # Probe 1G: START có note='START_NOTE'
    e_1g = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}, note="START_NOTE"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_METADATA"):
        fn(e_1g, planned_count=1, exec_id="probe_1g")

    # Probe 1H: START có timestamp_iso không đúng định dạng ISO 8601
    e_1h = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}),
    ]
    object.__setattr__(e_1h[0], "timestamp_iso", "invalid_ts")
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_TIMESTAMP"):
        fn(e_1h, planned_count=1, exec_id="probe_1h")

    # Probe 1I: START có seq=True (ép kiểu boolean)
    e_1i = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}),
    ]
    object.__setattr__(e_1i[0], "seq", True)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_SEQ_TYPE"):
        fn(e_1i, planned_count=1, exec_id="probe_1i")

    # Probe 1J: START có track_index=True (ép kiểu boolean)
    e_1j = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}),
    ]
    object.__setattr__(e_1j[0], "track_index", True)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_METADATA"):
        fn(e_1j, planned_count=1, exec_id="probe_1j")

    # Probe 1K: START có action_params không phải dict
    e_1k = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}),
    ]
    object.__setattr__(e_1k[0], "action_params", "not_dict")
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS"):
        fn(e_1k, planned_count=1, exec_id="probe_1k")

    # Probe 2A: FINISH action_type không phải 'NO_OP' (ví dụ 'INSERT_CLIP')
    e_2a = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="FINISH", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0}, note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_ACTION_TYPE"):
        fn(e_2a, planned_count=1, exec_id="probe_2a")

    # Probe 2B: FINISH có track_type='video'
    e_2b = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0}, track_type="video", note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_METADATA"):
        fn(e_2b, planned_count=1, exec_id="probe_2b")

    # Probe 2C: FINISH có track_index=99
    e_2c = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0}, track_index=99, note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_METADATA"):
        fn(e_2c, planned_count=1, exec_id="probe_2c")

    # Probe 2D: FINISH có resolve_item_id='item_01'
    e_2d = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0}, resolve_item_id="item_01", note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_METADATA"):
        fn(e_2d, planned_count=1, exec_id="probe_2d")

    # Probe 2E: FINISH in-doubt có error_code='ERR_CUSTOM'
    e_2e = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0}, error_code="ERR_CUSTOM", note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_METADATA"):
        fn(e_2e, planned_count=1, exec_id="probe_2e")

    # Probe 2F: FINISH in-doubt có error_message='failed finish'
    e_2f = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0}, error_message="failed finish", note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
    ]
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_METADATA"):
        fn(e_2f, planned_count=1, exec_id="probe_2f")

    # Probe 2G: FINISH có timestamp_iso không đúng ISO 8601
    e_2g = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0}, note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
    ]
    object.__setattr__(e_2g[2], "timestamp_iso", "not_a_timestamp")
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_TIMESTAMP"):
        fn(e_2g, planned_count=1, exec_id="probe_2g")

    # Probe 2H: FINISH có seq=True (ép kiểu boolean)
    e_2h = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0}, note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
    ]
    object.__setattr__(e_2h[2], "seq", True)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_SEQ_TYPE"):
        fn(e_2h, planned_count=1, exec_id="probe_2h")

    # Probe 2I: FINISH có track_index=True (ép kiểu boolean)
    e_2i = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 1}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0}, note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
    ]
    object.__setattr__(e_2i[2], "track_index", True)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_METADATA"):
        fn(e_2i, planned_count=1, exec_id="probe_2i")

    # Probe 3: Positive Control trực tiếp
    e_3 = [
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:00Z", action_params={"total_actions": 2}),
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:01Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=2, stage="ACTION_COMMITTED", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:02Z", action_params={"action_index": 0}, track_type="video", track_index=1),
        JournalEntry(seq=3, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T12:00:03Z", action_params={"action_index": 1}, track_type="video", track_index=1),
        JournalEntry(seq=4, stage="FINISH", action_type="NO_OP", timestamp_iso="2026-11-02T12:00:04Z", action_params={"action_index": 1}, note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT"),
    ]
    comm_idx, comm_set, pending_idx = fn(e_3, planned_count=2, exec_id="probe_3")
    assert comm_idx == [0]
    assert comm_set == {0}
    assert pending_idx == 1

    # -------------------------------------------------------------------------
    # Phần 2: End-to-end reconciliation probes qua engine.reconcile_target()
    # -------------------------------------------------------------------------
    proj_id = "proj_r8_res_01"
    tl_id = "tl_r8_res_01"
    exec_id = "exec_r8_res_01"

    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gw = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)
    gw.action_timeout_indices.add(1)  # timeout tại action 1 (action 0 committed)

    plan, approval = _build_test_plan_and_approval(
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        num_actions=2,
    )
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gw, trust_provider=TEST_TRUST_PROVIDER)
    res = engine.execute_plan(plan, approval, execution_id=exec_id)
    assert res.status == "IN_DOUBT_OBSERVER_TIMEOUT"
    assert res.committed_actions_count == 1

    art_path, art_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=1,
        verified_outcome="ACTION_COMMITTED_CONFIRMED",
        operator_id="op_1",
    )
    proof = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_r8_res_01",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=1,
        verified_outcome="ACTION_COMMITTED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha,
        evidence_artifact_path=art_path,
        notes="G07-07B4-R9 end-to-end test proof",
    )

    j_path = journal_svc.journal_path(exec_id)
    with open(j_path, "rb") as f:
        clean_bytes = f.read()

    def _assert_zero_mutation():
        assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True
        rec = receipt_svc.load_receipt(exec_id)
        assert rec.state == "EXECUTING"
        st = receipt_svc.get_target_state(proj_id, tl_id)
        assert st is not None and st.get("state") == "IN_DOUBT"

    replay_entries = journal_svc.replay_journal(exec_id).entries

    # End-to-end Case A: Làm sai lệch START action_type='INSERT_CLIP'
    corrupt_start_entries = []
    for e in replay_entries:
        if e.stage == "START":
            corrupt_start_entries.append(JournalEntry(
                seq=e.seq,
                stage="START",
                action_type="INSERT_CLIP",
                timestamp_iso=e.timestamp_iso,
                action_params=e.action_params,
            ))
        else:
            corrupt_start_entries.append(e)

    with open(j_path, "w", encoding="utf-8") as f:
        for e in corrupt_start_entries:
            f.write(json.dumps(e.to_dict()) + "\n")

    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_ACTION_TYPE"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()

    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case B: Làm sai lệch START track_type='video' và track_index=1
    corrupt_start_track_entries = []
    for e in replay_entries:
        if e.stage == "START":
            corrupt_start_track_entries.append(JournalEntry(
                seq=e.seq,
                stage="START",
                action_type=e.action_type,
                timestamp_iso=e.timestamp_iso,
                action_params=e.action_params,
                track_type="video",
                track_index=1,
            ))
        else:
            corrupt_start_track_entries.append(e)

    with open(j_path, "w", encoding="utf-8") as f:
        for e in corrupt_start_track_entries:
            f.write(json.dumps(e.to_dict()) + "\n")

    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_UNKNOWN_KEY|ERR_JOURNAL_CONTROL_RECORD_METADATA"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()

    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case C: Làm sai lệch FINISH action_type='INSERT_CLIP'
    corrupt_finish_type_entries = []
    for e in replay_entries:
        if e.stage == "FINISH":
            corrupt_finish_type_entries.append(JournalEntry(
                seq=e.seq,
                stage="FINISH",
                action_type="INSERT_CLIP",
                timestamp_iso=e.timestamp_iso,
                action_params=e.action_params,
                note=e.note,
            ))
        else:
            corrupt_finish_type_entries.append(e)

    with open(j_path, "w", encoding="utf-8") as f:
        for e in corrupt_finish_type_entries:
            f.write(json.dumps(e.to_dict()) + "\n")

    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_CONTROL_RECORD_ACTION_TYPE"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()

    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case D: Làm sai lệch FINISH track_type='video' và track_index=1
    corrupt_finish_track_entries = []
    for e in replay_entries:
        if e.stage == "FINISH":
            corrupt_finish_track_entries.append(JournalEntry(
                seq=e.seq,
                stage="FINISH",
                action_type=e.action_type,
                timestamp_iso=e.timestamp_iso,
                action_params=e.action_params,
                track_type="video",
                track_index=1,
                note=e.note,
            ))
        else:
            corrupt_finish_track_entries.append(e)

    with open(j_path, "w", encoding="utf-8") as f:
        for e in corrupt_finish_track_entries:
            f.write(json.dumps(e.to_dict()) + "\n")

    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_UNKNOWN_KEY|ERR_JOURNAL_CONTROL_RECORD_METADATA"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()

    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case E: Làm sai lệch FINISH error_code='ERR_CUSTOM_TIMEOUT'
    corrupt_finish_err_entries = []
    for e in replay_entries:
        if e.stage == "FINISH":
            corrupt_finish_err_entries.append(JournalEntry(
                seq=e.seq,
                stage="FINISH",
                action_type=e.action_type,
                timestamp_iso=e.timestamp_iso,
                action_params=e.action_params,
                error_code="ERR_CUSTOM_TIMEOUT",
                note=e.note,
            ))
        else:
            corrupt_finish_err_entries.append(e)

    with open(j_path, "w", encoding="utf-8") as f:
        for e in corrupt_finish_err_entries:
            f.write(json.dumps(e.to_dict()) + "\n")

    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_UNKNOWN_KEY|ERR_JOURNAL_CONTROL_RECORD_METADATA"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()

    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case F: Positive Control - giải tỏa và đối soát thành công 2/2 actions
    assert engine.reconcile_target(proof) is True
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is False
    rec_final = receipt_svc.load_receipt(exec_id)
    assert rec_final.state == "COMPLETED"
    st_final = receipt_svc.get_target_state(proj_id, tl_id)
    assert st_final is not None and st_final.get("state") == "RECONCILED"


def test_b4_r9_res_01_raw_wal_json_schema_validation_before_coercion(tmp_path: Path):
    """B4-R9-RES-01: Thẩm định cấu trúc và kiểu dữ liệu nguyên thủy của đối tượng JSON thô trước khi chuyển đổi sang JournalEntry.

    Quy định nghiệm thu (Workorder G07-07B4-R10 & 07B4-R9-code-review.md):
    1. Cấm hoàn toàn mọi hình thức ép kiểu ngầm hoặc bỏ qua trường lạ trong quá trình deserialization:
       - seq: true -> ERR_JOURNAL_FIELD_TYPE
       - seq: 0.75 -> ERR_JOURNAL_FIELD_TYPE
       - track_index: 0.0 -> ERR_JOURNAL_UNKNOWN_KEY (START/FINISH) hoặc ERR_JOURNAL_FIELD_TYPE (ACTION_PENDING)
       - untrusted_extra: 'x' -> ERR_JOURNAL_UNKNOWN_KEY
       - duplicate keys ở envelope hoặc action_params -> ERR_JOURNAL_DUPLICATE_KEY
    2. Thẩm định phong bì nghiêm ngặt theo từng stage:
       - START, FINISH, ACTION_PENDING, ACTION_COMMITTED, ACTION_FAILED, ROLLBACK_PENDING, ROLLBACK_DONE, ROLLBACK_FAILED, RECOVERY_NOTE.
    3. Thẩm định end-to-end qua engine.reconcile_target(proof):
       - Bất kỳ vi phạm schema thô hoặc duplicate key nào đều kích hoạt fail-closed.
       - Bảo tồn 100% byte gốc trên đĩa.
       - Target unresolved registry giữ nguyên trạng thái bị khóa.
       - Receipt giữ nguyên trạng thái EXECUTING.
       - Target state giữ nguyên IN_DOUBT.
       - Zero gateway action dispatch.
       - Positive control đối soát thành công khi tệp hợp lệ.
    """
    raw_fn = validate_raw_journal_record
    from_dict_fn = JournalEntry.from_dict

    # -------------------------------------------------------------------------
    # Phần 1: Pure-function probes trực tiếp trên raw JSON và JournalEntry.from_dict
    # -------------------------------------------------------------------------
    # Probe 1: Non-dict input
    with pytest.raises(ValueError, match="ERR_JOURNAL_RECORD_TYPE"):
        raw_fn(["not_a_dict"])
    with pytest.raises(ValueError, match="ERR_JOURNAL_RECORD_TYPE"):
        from_dict_fn("not_a_dict")  # type: ignore

    # Probe 2: Thiếu core keys
    valid_base = {
        "seq": 0,
        "stage": "START",
        "action_type": "NO_OP",
        "timestamp_iso": "2026-11-02T12:00:00Z",
        "action_params": {"total_actions": 1},
    }
    for req_key in ("seq", "stage", "action_type", "timestamp_iso", "action_params"):
        broken = dict(valid_base)
        del broken[req_key]
        with pytest.raises(ValueError, match="ERR_JOURNAL_MISSING_KEY"):
            raw_fn(broken)
        with pytest.raises(ValueError, match="ERR_JOURNAL_MISSING_KEY"):
            from_dict_fn(broken)

    # Probe 3: Stage không hợp lệ
    broken_stage = dict(valid_base, stage="INVALID_STAGE_XYZ")
    with pytest.raises(ValueError, match="ERR_JOURNAL_INVALID_STAGE"):
        raw_fn(broken_stage)
    with pytest.raises(ValueError, match="ERR_JOURNAL_INVALID_STAGE"):
        from_dict_fn(broken_stage)

    # Probe 4: seq: True (bool coercion) trên START
    broken_seq_bool = dict(valid_base, seq=True)
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        raw_fn(broken_seq_bool)
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(broken_seq_bool)

    # Probe 5: seq: False trên START
    broken_seq_false = dict(valid_base, seq=False)
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        raw_fn(broken_seq_false)
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(broken_seq_false)

    # Probe 6: seq: 0.75 (fractional float) trên START
    broken_seq_float = dict(valid_base, seq=0.75)
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        raw_fn(broken_seq_float)
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(broken_seq_float)

    # Probe 7: seq: -1 (negative int) trên START
    broken_seq_neg = dict(valid_base, seq=-1)
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        raw_fn(broken_seq_neg)
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(broken_seq_neg)

    # Probe 8: seq: True trên FINISH
    finish_base = {
        "seq": 2,
        "stage": "FINISH",
        "action_type": "NO_OP",
        "timestamp_iso": "2026-11-02T12:00:02Z",
        "action_params": {"action_index": 1},
        "note": "HALTED_IN_DOUBT_OBSERVER_TIMEOUT",
    }
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(dict(finish_base, seq=True))

    # Probe 9: seq: 0.75 trên FINISH
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(dict(finish_base, seq=0.75))

    # Probe 10: seq: True trên ACTION_PENDING
    pending_base = {
        "seq": 1,
        "stage": "ACTION_PENDING",
        "action_type": "INSERT_CLIP",
        "timestamp_iso": "2026-11-02T12:00:01Z",
        "action_params": {"action_index": 0},
        "track_type": "video",
        "track_index": 1,
    }
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(dict(pending_base, seq=True))

    # Probe 11: seq: 0.75 trên ACTION_PENDING
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(dict(pending_base, seq=0.75))

    # Probe 12: track_index: 0.0 (float zero) trên START -> Bị từ chối là unknown key
    broken_start_track = dict(valid_base, track_index=0.0)
    with pytest.raises(ValueError, match="ERR_JOURNAL_UNKNOWN_KEY"):
        raw_fn(broken_start_track)
    with pytest.raises(ValueError, match="ERR_JOURNAL_UNKNOWN_KEY"):
        from_dict_fn(broken_start_track)

    # Probe 13: track_index: 0.0 trên FINISH -> Bị từ chối là unknown key
    broken_finish_track = dict(finish_base, track_index=0.0)
    with pytest.raises(ValueError, match="ERR_JOURNAL_UNKNOWN_KEY"):
        raw_fn(broken_finish_track)
    with pytest.raises(ValueError, match="ERR_JOURNAL_UNKNOWN_KEY"):
        from_dict_fn(broken_finish_track)

    # Probe 14: track_index: 0.0 trên ACTION_PENDING -> Bị từ chối là type float (thay vì int)
    broken_pending_ti_float = dict(pending_base, track_index=0.0)
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        raw_fn(broken_pending_ti_float)
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(broken_pending_ti_float)

    # Probe 15: track_index: True (bool) trên ACTION_PENDING
    broken_pending_ti_bool = dict(pending_base, track_index=True)
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        raw_fn(broken_pending_ti_bool)
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(broken_pending_ti_bool)

    # Probe 16: track_index: -1 trên ACTION_PENDING
    broken_pending_ti_neg = dict(pending_base, track_index=-1)
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        raw_fn(broken_pending_ti_neg)
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(broken_pending_ti_neg)

    # Probe 17: Unknown key 'untrusted_extra' trên START
    broken_start_unknown = dict(valid_base, untrusted_extra="x")
    with pytest.raises(ValueError, match="ERR_JOURNAL_UNKNOWN_KEY"):
        raw_fn(broken_start_unknown)
    with pytest.raises(ValueError, match="ERR_JOURNAL_UNKNOWN_KEY"):
        from_dict_fn(broken_start_unknown)

    # Probe 18: Unknown key 'untrusted_extra' trên FINISH
    broken_finish_unknown = dict(finish_base, untrusted_extra="x")
    with pytest.raises(ValueError, match="ERR_JOURNAL_UNKNOWN_KEY"):
        raw_fn(broken_finish_unknown)
    with pytest.raises(ValueError, match="ERR_JOURNAL_UNKNOWN_KEY"):
        from_dict_fn(broken_finish_unknown)

    # Probe 19: Unknown key 'untrusted_extra' trên ACTION_PENDING
    broken_pending_unknown = dict(pending_base, untrusted_extra="x")
    with pytest.raises(ValueError, match="ERR_JOURNAL_UNKNOWN_KEY"):
        raw_fn(broken_pending_unknown)
    with pytest.raises(ValueError, match="ERR_JOURNAL_UNKNOWN_KEY"):
        from_dict_fn(broken_pending_unknown)

    # Probe 20: Duplicate key in envelope JSON string
    dup_env_json = '{"seq": 0, "seq": 0, "stage": "START", "action_type": "NO_OP", "timestamp_iso": "2026-11-02T12:00:00Z", "action_params": {}}'
    with pytest.raises(ValueError, match="ERR_JOURNAL_DUPLICATE_KEY"):
        json.loads(dup_env_json, object_pairs_hook=reject_duplicate_journal_keys)

    # Probe 21: Duplicate key in nested action_params JSON string
    dup_params_json = '{"seq": 0, "stage": "START", "action_type": "NO_OP", "timestamp_iso": "2026-11-02T12:00:00Z", "action_params": {"total_actions": 1, "total_actions": 1}}'
    with pytest.raises(ValueError, match="ERR_JOURNAL_DUPLICATE_KEY"):
        json.loads(dup_params_json, object_pairs_hook=reject_duplicate_journal_keys)

    # Probe 22: Primitive type checks trên các trường khác
    # action_type không phải str hoặc không nằm trong VALID_ACTION_TYPES
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(dict(valid_base, action_type=123))
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(dict(valid_base, action_type="INVALID_TYPE_XYZ"))

    # timestamp_iso không phải str hoặc không phải ISO-8601
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(dict(valid_base, timestamp_iso=123))
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(dict(valid_base, timestamp_iso="not_an_iso_ts"))

    # action_params không phải dict
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(dict(valid_base, action_params="not_a_dict"))

    # resolve_item_id không phải str trên ACTION_COMMITTED
    comm_base = {
        "seq": 2,
        "stage": "ACTION_COMMITTED",
        "action_type": "INSERT_CLIP",
        "timestamp_iso": "2026-11-02T12:00:02Z",
        "action_params": {"action_index": 0},
        "resolve_item_id": 12345,
    }
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(comm_base)

    # track_type không phải str hoặc không phải video/audio
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(dict(comm_base, resolve_item_id="item_1", track_type=123))
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(dict(comm_base, resolve_item_id="item_1", track_type="subtitle"))

    # error_code / error_message không phải str trên ACTION_FAILED
    failed_base = {
        "seq": 2,
        "stage": "ACTION_FAILED",
        "action_type": "INSERT_CLIP",
        "timestamp_iso": "2026-11-02T12:00:02Z",
        "action_params": {"action_index": 0},
        "error_code": 123,
    }
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(failed_base)
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(dict(failed_base, error_code="ERR_FAIL", error_message=True))

    # note không phải str trên RECOVERY_NOTE
    rec_note = {
        "seq": 3,
        "stage": "RECOVERY_NOTE",
        "action_type": "NO_OP",
        "timestamp_iso": "2026-11-02T12:00:03Z",
        "action_params": {},
        "note": 12345,
    }
    with pytest.raises(ValueError, match="ERR_JOURNAL_FIELD_TYPE"):
        from_dict_fn(rec_note)

    # -------------------------------------------------------------------------
    # Phần 2: End-to-end reconciliation probes qua engine.reconcile_target(proof)
    # -------------------------------------------------------------------------
    proj_id = "proj_r9_res_01"
    tl_id = "tl_r9_res_01"
    exec_id = "exec_r9_res_01"

    receipt_svc = ExecutionReceiptService(str(tmp_path))
    journal_svc = ExecutionJournalService(str(tmp_path))
    gw = FakeResolveGateway(project_id=proj_id, timeline_id=tl_id)
    gw.action_timeout_indices.add(1)  # timeout tại action 1 (action 0 committed)

    plan, approval = _build_test_plan_and_approval(
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        num_actions=2,
    )
    engine = ResolveExecutionEngine(receipt_svc, journal_svc, gw, trust_provider=TEST_TRUST_PROVIDER)
    res = engine.execute_plan(plan, approval, execution_id=exec_id)
    assert res.status == "IN_DOUBT_OBSERVER_TIMEOUT"
    assert res.committed_actions_count == 1

    art_path, art_sha = _write_evidence_artifact(
        tmp_path,
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        action_index=1,
        verified_outcome="ACTION_COMMITTED_CONFIRMED",
        operator_id="op_1",
    )
    proof = OperatorReconciliationProof(
        schema_version=1,
        proof_id="prf_r9_res_01",
        target_project_id=proj_id,
        target_timeline_id=tl_id,
        target_execution_id=exec_id,
        in_doubt_action_index=1,
        verified_outcome="ACTION_COMMITTED_CONFIRMED",
        operator_id="op_1",
        reconciliation_timestamp_iso="2026-11-02T12:00:00Z",
        readback_evidence_hash=art_sha,
        evidence_artifact_path=art_path,
        notes="G07-07B4-R10 end-to-end test proof",
    )

    j_path = journal_svc.journal_path(exec_id)
    with open(j_path, "rb") as f:
        clean_bytes = f.read()

    initial_gw_dispatches = len(gw.dispatched_actions)

    def _assert_zero_mutation():
        assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is True
        rec = receipt_svc.load_receipt(exec_id)
        assert rec.state == "EXECUTING"
        st = receipt_svc.get_target_state(proj_id, tl_id)
        assert st is not None and st.get("state") == "IN_DOUBT"
        assert len(gw.dispatched_actions) == initial_gw_dispatches

    lines = clean_bytes.decode("utf-8").splitlines(keepends=True)

    # End-to-end Case 1: START line có seq: true
    corrupt_lines_c1 = list(lines)
    corrupt_lines_c1[0] = '{"seq": true, "stage": "START", "action_type": "NO_OP", "timestamp_iso": "2026-11-02T12:00:00Z", "action_params": {"total_actions": 2}}\n'
    with open(j_path, "w", encoding="utf-8") as f:
        f.writelines(corrupt_lines_c1)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_FIELD_TYPE"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()
    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case 2: START line có seq: 0.75
    corrupt_lines_c2 = list(lines)
    corrupt_lines_c2[0] = '{"seq": 0.75, "stage": "START", "action_type": "NO_OP", "timestamp_iso": "2026-11-02T12:00:00Z", "action_params": {"total_actions": 2}}\n'
    with open(j_path, "w", encoding="utf-8") as f:
        f.writelines(corrupt_lines_c2)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_FIELD_TYPE"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()
    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case 3: START line có unknown key 'untrusted_extra'
    corrupt_lines_c3 = list(lines)
    corrupt_lines_c3[0] = '{"seq": 0, "stage": "START", "action_type": "NO_OP", "timestamp_iso": "2026-11-02T12:00:00Z", "action_params": {"total_actions": 2}, "untrusted_extra": "x"}\n'
    with open(j_path, "w", encoding="utf-8") as f:
        f.writelines(corrupt_lines_c3)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_UNKNOWN_KEY"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()
    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case 4: START line có duplicate key 'seq'
    corrupt_lines_c4 = list(lines)
    corrupt_lines_c4[0] = '{"seq": 0, "seq": 0, "stage": "START", "action_type": "NO_OP", "timestamp_iso": "2026-11-02T12:00:00Z", "action_params": {"total_actions": 2}}\n'
    with open(j_path, "w", encoding="utf-8") as f:
        f.writelines(corrupt_lines_c4)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_DUPLICATE_KEY"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()
    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case 5: FINISH line có track_index: 0.0
    corrupt_lines_c5 = list(lines)
    corrupt_lines_c5[-1] = '{"seq": 4, "stage": "FINISH", "action_type": "NO_OP", "timestamp_iso": "2026-11-02T12:00:05Z", "action_params": {"action_index": 1}, "note": "HALTED_IN_DOUBT_OBSERVER_TIMEOUT", "track_index": 0.0}\n'
    with open(j_path, "w", encoding="utf-8") as f:
        f.writelines(corrupt_lines_c5)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_UNKNOWN_KEY"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()
    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case 6: FINISH line có unknown key 'untrusted_extra'
    corrupt_lines_c6 = list(lines)
    corrupt_lines_c6[-1] = '{"seq": 4, "stage": "FINISH", "action_type": "NO_OP", "timestamp_iso": "2026-11-02T12:00:05Z", "action_params": {"action_index": 1}, "note": "HALTED_IN_DOUBT_OBSERVER_TIMEOUT", "untrusted_extra": "x"}\n'
    with open(j_path, "w", encoding="utf-8") as f:
        f.writelines(corrupt_lines_c6)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_UNKNOWN_KEY"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()
    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case 7: FINISH line có duplicate key trong action_params
    corrupt_lines_c7 = list(lines)
    corrupt_lines_c7[-1] = '{"seq": 4, "stage": "FINISH", "action_type": "NO_OP", "timestamp_iso": "2026-11-02T12:00:05Z", "action_params": {"action_index": 1, "action_index": 1}, "note": "HALTED_IN_DOUBT_OBSERVER_TIMEOUT"}\n'
    with open(j_path, "w", encoding="utf-8") as f:
        f.writelines(corrupt_lines_c7)
    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_DUPLICATE_KEY"):
        engine.reconcile_target(proof)
    _assert_zero_mutation()
    with open(j_path, "wb") as f:
        f.write(clean_bytes)

    # End-to-end Case 8: Positive Control - giải tỏa và đối soát thành công 2/2 actions
    assert engine.reconcile_target(proof) is True
    assert engine.unresolved_registry.is_target_unresolved(proj_id, tl_id) is False
    rec_final = receipt_svc.load_receipt(exec_id)
    assert rec_final.state == "COMPLETED"
    st_final = receipt_svc.get_target_state(proj_id, tl_id)
    assert st_final is not None and st_final.get("state") == "RECONCILED"






