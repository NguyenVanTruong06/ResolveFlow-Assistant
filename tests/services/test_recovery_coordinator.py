"""Kiểm thử Bộ Điều phối Phục hồi Chỉ Đọc (RecoveryCoordinator).

Kiểm tra:
- Thăm dò khóa cố vấn hệ điều hành không chặn (authoritative non-blocking OS advisory lock probe):
  + Khi tệp .lock đang bị tiến trình khác flock -> phát hiện is_advisory_lock_held=True, cấm giải phóng khóa của tiến trình đó.
  + Khi tệp .lock tự do -> phát hiện is_advisory_lock_held=False, nhả khóa probe ngay lập tức, không để lại tàn dư khóa.
  + Thử nghiệm PID reuse: PID trong lock/receipt còn sống hoặc chết không ảnh hưởng tới quyền sở hữu OS lock.
- Bẫy không đột biến (Zero-mutation tripwire):
  + Các tệp receipt, journal, lock, idempotency trước và sau khi điều phối đánh giá phục hồi phải hoàn toàn giống nhau 100% về mã băm byte.
  + Cấm xóa, cấm dọn dẹp tệp, cấm truncate, cấm sửa đổi.
- Trường hợp chỉ mục idempotency bị thiếu hoặc biến dạng:
  + Fail-closed, trả về STATE_ARTIFACT_CORRUPTED_OR_MISSING.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import pytest

from src.core.contracts.execution_receipt_contracts import (
    ExecutionReceipt,
    JournalEntry,
    SourceFileDigestBinding,
    TargetTrackReservation,
    journal_entry_to_json_line,
    receipt_to_json,
)
from src.services.execution_journal_service import ExecutionJournalService
from src.services.execution_receipt_service import ExecutionReceiptService
from src.services.recovery_coordinator import RecoveryCoordinator


def _hash_file(path: str) -> str:
    """Hàm băm SHA-256 nội dung tệp."""
    if not os.path.exists(path):
        return "NON_EXISTENT"
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


@pytest.fixture
def recovery_env():
    """Tạo môi trường thư mục tạm cho biên bản, nhật ký, và khóa."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        receipts_dir = os.path.join(tmp_dir, "receipts")
        journals_dir = os.path.join(tmp_dir, "journals")
        idempotency_dir = os.path.join(tmp_dir, "idempotency")
        os.makedirs(receipts_dir, exist_ok=True)
        os.makedirs(journals_dir, exist_ok=True)
        os.makedirs(idempotency_dir, exist_ok=True)

        yield {
            "base_dir": tmp_dir,
            "receipts_dir": receipts_dir,
            "journals_dir": journals_dir,
            "idempotency_dir": idempotency_dir,
        }


def test_coordinator_advisory_lock_probe_free_vs_held(recovery_env):
    """Kiểm tra thăm dò khóa cố vấn: phân định chính xác khi khóa bị giữ và khi tự do."""
    base_dir = recovery_env["base_dir"]
    exec_id = "exec_lock_probe_001"
    lock_path = os.path.join(recovery_env["receipts_dir"], f"{exec_id}.lock")

    # Tạo receipt hợp lệ
    receipt_service = ExecutionReceiptService(base_dir)
    journal_service = ExecutionJournalService(base_dir)

    receipt = ExecutionReceipt(
        schema_version=2,
        execution_id=exec_id,
        idempotency_key="e" * 64,
        proposal_id="prop_01",
        proposal_revision="1" * 64,
        approval_id="appr_01",
        approved_at_iso="2026-11-02T12:00:00Z",
        project_name="P1",
        project_id="proj_1",
        timeline_name="T1",
        timeline_id="tl_1",
        timeline_fps_numerator=24000,
        timeline_fps_denominator=1000,
        timeline_resolution=(1920, 1080),
        source_digests=(SourceFileDigestBinding("c1", "/a.mp4", "0" * 64, 0, (1, 2, 10, 1000, 1000)),),
        catalog_digests=(),
        track_reservations=(TargetTrackReservation("video", 1, 0, 1000, "V1_MAIN"),),
        planned_actions_count=1,
        created_at_iso="2026-11-02T12:01:00Z",
        state="EXECUTING",
    )
    receipt_service.create_receipt(receipt)

    # Ghi 1 dòng START vào journal
    journal_service.append_entry(exec_id, JournalEntry(0, "START", "NO_OP", "2026-11-02T12:01:02Z", {}))

    coordinator = RecoveryCoordinator(base_dir)

    # 1. Khi lock file tự do (chưa bị flock bởi tiến trình nào)
    assessment_free = coordinator.assess_execution(
        execution_id=exec_id,
        active_project_id="proj_1",
        active_timeline_id="tl_1",
    )
    assert assessment_free.is_advisory_lock_held is False
    assert assessment_free.classified_state != "STATE_CLEAN_ACTIVE_LOCKED"

    # 2. Chiếm khóa độc quyền bên ngoài bằng fcntl.flock để giả lập tiến trình đang chạy
    with open(lock_path, "w") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_f.write(f"pid={os.getpid()}\n")
        lock_f.flush()

        # Thăm dò trong lúc khóa đang bị chiếm
        assessment_held = coordinator.assess_execution(
            execution_id=exec_id,
            active_project_id="proj_1",
            active_timeline_id="tl_1",
        )
        assert assessment_held.is_advisory_lock_held is True
        assert assessment_held.classified_state == "STATE_CLEAN_ACTIVE_LOCKED"
        assert assessment_held.recommended_action == "CONCURRENT_JOB_RUNNING_DO_NOT_DISTURB"

        # Khóa của bên ngoài vẫn phải được bảo toàn, không bị probe nhả trộm!
        with pytest.raises(BlockingIOError):
            test_fd = os.open(lock_path, os.O_RDWR)
            try:
                fcntl.flock(test_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(test_fd)


def test_coordinator_pid_reuse_irrelevance(recovery_env):
    """Kiểm tra: PID ghi trong file lock dù còn sống (ví dụ PID hiện tại) nhưng không flock -> coi là khóa rảnh."""
    base_dir = recovery_env["base_dir"]
    exec_id = "exec_pid_reuse_001"
    receipt_service = ExecutionReceiptService(base_dir)
    journal_service = ExecutionJournalService(base_dir)

    receipt = ExecutionReceipt(
        schema_version=2,
        execution_id=exec_id,
        idempotency_key="d" * 64,
        proposal_id="prop_01",
        proposal_revision="1" * 64,
        approval_id="appr_01",
        approved_at_iso="2026-11-02T12:00:00Z",
        project_name="P1",
        project_id="proj_1",
        timeline_name="T1",
        timeline_id="tl_1",
        timeline_fps_numerator=24000,
        timeline_fps_denominator=1000,
        timeline_resolution=(1920, 1080),
        source_digests=(SourceFileDigestBinding("c1", "/a.mp4", "0" * 64, 0, (1, 2, 10, 1000, 1000)),),
        catalog_digests=(),
        track_reservations=(TargetTrackReservation("video", 1, 0, 1000, "V1_MAIN"),),
        planned_actions_count=1,
        created_at_iso="2026-11-02T12:01:00Z",
        state="EXECUTING",
    )
    receipt_service.create_receipt(receipt)
    journal_service.append_entry(exec_id, JournalEntry(0, "START", "NO_OP", "2026-11-02T12:01:02Z", {}))
    journal_service.append_entry(
        exec_id,
        JournalEntry(1, "ACTION_COMMITTED", "INSERT_CLIP", "2026-11-02T12:01:03Z", {}, resolve_item_id="i1", track_type="video", track_index=1),
    )

    # Ghi PID của chính tiến trình này vào lock file nhưng KHÔNG GỌI FLOCK (giả lập PID reuse hoặc crash bỏ lại file)
    lock_path = os.path.join(recovery_env["receipts_dir"], f"{exec_id}.lock")
    with open(lock_path, "w") as f:
        f.write(f"pid={os.getpid()}\n")

    coordinator = RecoveryCoordinator(base_dir)
    assessment = coordinator.assess_execution(
        execution_id=exec_id,
        active_project_id="proj_1",
        active_timeline_id="tl_1",
    )

    # Thẩm quyền OS lock là tối thượng: không bị flock -> lock rảnh
    assert assessment.is_advisory_lock_held is False
    assert assessment.diagnostic_recorded_pid == os.getpid()
    assert assessment.classified_state == "STATE_COMMITTED_CANDIDATE_COMPENSATION"


def test_coordinator_zero_mutation_tripwire(recovery_env):
    """Bẫy không đột biến (Zero-mutation tripwire): Hồ sơ đĩa nguyên vẹn 100% byte sau khi đối soát."""
    base_dir = recovery_env["base_dir"]
    exec_id = "exec_tripwire_001"
    receipt_service = ExecutionReceiptService(base_dir)
    journal_service = ExecutionJournalService(base_dir)

    receipt = ExecutionReceipt(
        schema_version=2,
        execution_id=exec_id,
        idempotency_key="c" * 64,
        proposal_id="prop_01",
        proposal_revision="1" * 64,
        approval_id="appr_01",
        approved_at_iso="2026-11-02T12:00:00Z",
        project_name="P1",
        project_id="proj_1",
        timeline_name="T1",
        timeline_id="tl_1",
        timeline_fps_numerator=24000,
        timeline_fps_denominator=1000,
        timeline_resolution=(1920, 1080),
        source_digests=(SourceFileDigestBinding("c1", "/a.mp4", "0" * 64, 0, (1, 2, 10, 1000, 1000)),),
        catalog_digests=(),
        track_reservations=(TargetTrackReservation("video", 1, 0, 1000, "V1_MAIN"),),
        planned_actions_count=1,
        created_at_iso="2026-11-02T12:01:00Z",
        state="EXECUTING",
    )
    receipt_service.create_receipt(receipt)
    journal_service.append_entry(exec_id, JournalEntry(0, "START", "NO_OP", "2026-11-02T12:01:02Z", {}))
    journal_service.append_entry(
        exec_id,
        JournalEntry(1, "ACTION_COMMITTED", "INSERT_CLIP", "2026-11-02T12:01:03Z", {}, resolve_item_id="i1", track_type="video", track_index=1),
    )

    receipt_path = os.path.join(recovery_env["receipts_dir"], f"{exec_id}.receipt.json")
    journal_path = os.path.join(recovery_env["journals_dir"], f"{exec_id}.journal.jsonl")
    idemp_path = os.path.join(recovery_env["idempotency_dir"], f"idempotency_{receipt.idempotency_key}.json")

    hash_receipt_before = _hash_file(receipt_path)
    hash_journal_before = _hash_file(journal_path)
    hash_idemp_before = _hash_file(idemp_path)

    coordinator = RecoveryCoordinator(base_dir)
    assessment = coordinator.assess_execution(
        execution_id=exec_id,
        active_project_id="proj_1",
        active_timeline_id="tl_1",
    )
    assert assessment.classified_state == "STATE_COMMITTED_CANDIDATE_COMPENSATION"

    # Xác nhận SHA-256 sau khi assess giống hệt trước khi assess
    assert _hash_file(receipt_path) == hash_receipt_before
    assert _hash_file(journal_path) == hash_journal_before
    assert _hash_file(idemp_path) == hash_idemp_before


def test_coordinator_missing_or_corrupt_idempotency_fail_closed(recovery_env):
    """Chỉ mục idempotency bị thiếu hoặc biến dạng -> Phân loại fail-closed STATE_ARTIFACT_CORRUPTED_OR_MISSING."""
    base_dir = recovery_env["base_dir"]
    exec_id = "exec_corrupt_idemp_001"
    receipt_service = ExecutionReceiptService(base_dir)

    receipt = ExecutionReceipt(
        schema_version=2,
        execution_id=exec_id,
        idempotency_key="b" * 64,
        proposal_id="prop_01",
        proposal_revision="1" * 64,
        approval_id="appr_01",
        approved_at_iso="2026-11-02T12:00:00Z",
        project_name="P1",
        project_id="proj_1",
        timeline_name="T1",
        timeline_id="tl_1",
        timeline_fps_numerator=24000,
        timeline_fps_denominator=1000,
        timeline_resolution=(1920, 1080),
        source_digests=(SourceFileDigestBinding("c1", "/a.mp4", "0" * 64, 0, (1, 2, 10, 1000, 1000)),),
        catalog_digests=(),
        track_reservations=(TargetTrackReservation("video", 1, 0, 1000, "V1_MAIN"),),
        planned_actions_count=1,
        created_at_iso="2026-11-02T12:01:00Z",
        state="EXECUTING",
    )
    receipt_service.create_receipt(receipt)

    # Cố tình xóa tệp idempotency để kiểm tra fail-closed
    idemp_path = os.path.join(recovery_env["idempotency_dir"], f"idempotency_{receipt.idempotency_key}.json")
    os.remove(idemp_path)

    coordinator = RecoveryCoordinator(base_dir)
    assessment = coordinator.assess_execution(
        execution_id=exec_id,
        active_project_id="proj_1",
        active_timeline_id="tl_1",
    )

    assert assessment.classified_state == "STATE_ARTIFACT_CORRUPTED_OR_MISSING"
    assert assessment.recommended_action == "FORENSIC_QUARANTINE_REQUIRED"
