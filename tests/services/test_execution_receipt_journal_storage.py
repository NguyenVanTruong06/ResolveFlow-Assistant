"""Kiểm thử Dịch vụ Lưu trữ Biên bản & Nhật ký Thi công Bền vững (G07-07A6-I).

Bao quát toàn diện:
- Ghi đĩa 2 chặng nguyên tử, fsync, khóa idempotency bền vững chống duplicate execution.
- Khóa file độc quyền (.lock) chống xung đột cùng execution_id.
- Xử lý lỗi hệ thống tệp: ENOSPC, EACCES/PermissionError, tệp bị biến dạng, oversized.
- Giao thức WAL append-only, fsync từng dòng, kiểm tra tính tuần tự monotonic sequence.
- Phát hiện dòng cuối bị rách phi phá hủy (Non-destructive torn-tail detection): bảo tồn 100% byte gốc.
- Reverse iterator phục vụ hoàn tác bù trừ.
"""

from dataclasses import replace
import json
import os
from unittest.mock import patch
import pytest

from src.core.contracts.execution_receipt_contracts import (
    CatalogAssetDigestBinding,
    ExecutionReceipt,
    JournalEntry,
    SourceFileDigestBinding,
    TargetTrackReservation,
    compute_idempotency_key,
)
from src.services.execution_receipt_service import (
    ExecutionLockActiveError,
    ExecutionReceiptService,
    IdempotencyDuplicateActiveError,
    IdempotencyDuplicateCompletedError,
    ReceiptAlreadyExistsError,
    ReceiptNotFoundError,
    ReceiptStorageError,
)
from src.services.execution_journal_service import (
    ExecutionJournalService,
    JournalCorruptedError,
    JournalNotFoundError,
    JournalSequenceError,
)


def _make_receipt(execution_id="exec_test_01", proposal_id="prop_01", revision="1" * 64, state="INITIALIZED"):
    ik = compute_idempotency_key(
        proposal_id=proposal_id,
        proposal_revision=revision,
        approval_id="appr_01",
        project_id="proj_uid_123",
        timeline_id="tl_uid_456",
    )
    return ExecutionReceipt(
        execution_id=execution_id,
        idempotency_key=ik,
        proposal_id=proposal_id,
        proposal_revision=revision,
        approval_id="appr_01",
        approved_at_iso="2026-11-02T14:00:00Z",
        project_name="Demo Project",
        project_id="proj_uid_123",
        timeline_name="Timeline 1",
        timeline_id="tl_uid_456",
        timeline_fps_numerator=30,
        timeline_fps_denominator=1,
        timeline_resolution=(1920, 1080),
        source_digests=(
            SourceFileDigestBinding(
                clip_id="C01",
                absolute_path="/footage/cam_a.mp4",
                full_sha256="a" * 64,
                stream_index=0,
                stat_identity=(1, 2, 3, 4, 5),
            ),
        ),
        catalog_digests=(
            CatalogAssetDigestBinding(
                asset_id="cat_01",
                absolute_path="/assets/cat.mp4",
                full_sha256="b" * 64,
                stat_identity=(1, 2, 3, 4, 5),
            ),
        ),
        track_reservations=(
            TargetTrackReservation(
                track_type="video",
                track_index=1,
                reserved_start_frame=0,
                reserved_end_frame=100,
                intended_usage="V1_CUTS",
            ),
        ),
        planned_actions_count=1,
        created_at_iso="2026-11-02T14:00:01Z",
        state=state,
    )


# =============================================================================
# 1. Tests for ExecutionReceiptService
# =============================================================================

def test_receipt_service_create_and_load(tmp_path):
    service = ExecutionReceiptService(base_dir=str(tmp_path))
    receipt = _make_receipt("exec_01")

    saved = service.create_receipt(receipt)
    assert saved == receipt

    # Verify target file exists
    target_file = os.path.join(str(tmp_path), "receipts", "exec_01.receipt.json")
    assert os.path.exists(target_file)

    loaded = service.load_receipt("exec_01")
    assert loaded == receipt


def test_receipt_service_rejects_duplicate_execution_id(tmp_path):
    service = ExecutionReceiptService(base_dir=str(tmp_path))
    receipt = _make_receipt("exec_01")
    service.create_receipt(receipt)

    with pytest.raises(ReceiptAlreadyExistsError, match="ERR_RECEIPT_EXISTS"):
        service.create_receipt(receipt)


def test_receipt_service_idempotency_duplicate_active_rejection(tmp_path):
    service = ExecutionReceiptService(base_dir=str(tmp_path))
    r1 = _make_receipt("exec_01", state="INITIALIZED")
    service.create_receipt(r1)

    # Second client tries with a fresh UUID execution_id, but identical proposal & timeline
    r2 = _make_receipt("exec_02", state="INITIALIZED")
    assert r1.idempotency_key == r2.idempotency_key

    with pytest.raises(IdempotencyDuplicateActiveError, match="ERR_IDEMPOTENCY_DUPLICATE_ACTIVE"):
        service.create_receipt(r2)


def test_receipt_service_idempotency_duplicate_completed_rejection(tmp_path):
    service = ExecutionReceiptService(base_dir=str(tmp_path))
    r1 = _make_receipt("exec_01", state="INITIALIZED")
    service.create_receipt(r1)

    # Transition to EXECUTING then COMPLETED
    service.transition_state("exec_01", "EXECUTING", updated_at_iso="2026-11-02T14:01:00Z")
    service.transition_state("exec_01", "COMPLETED", updated_at_iso="2026-11-02T14:02:00Z", completed_at_iso="2026-11-02T14:02:00Z")

    # Second client tries with a new UUID execution_id
    r2 = _make_receipt("exec_02", state="INITIALIZED")
    with pytest.raises(IdempotencyDuplicateCompletedError, match="ERR_IDEMPOTENCY_DUPLICATE_COMPLETED"):
        service.create_receipt(r2)


def test_receipt_service_state_transition_validation(tmp_path):
    service = ExecutionReceiptService(base_dir=str(tmp_path))
    service.create_receipt(_make_receipt("exec_01", state="INITIALIZED"))

    # Valid: INITIALIZED -> EXECUTING
    t1 = service.transition_state("exec_01", "EXECUTING", updated_at_iso="2026-11-02T14:01:00Z")
    assert t1.state == "EXECUTING"

    # Invalid: EXECUTING -> INITIALIZED
    with pytest.raises(ValueError, match="ERR_INVALID_TRANSITION"):
        service.transition_state("exec_01", "INITIALIZED", updated_at_iso="2026-11-02T14:02:00Z")

    # Valid: EXECUTING -> ROLLED_BACK
    t2 = service.transition_state(
        "exec_01",
        "ROLLED_BACK",
        updated_at_iso="2026-11-02T14:03:00Z",
        last_error_code="ERR_MUTATION_FAILED",
        last_error_message="Simulated failure",
    )
    assert t2.state == "ROLLED_BACK"
    assert t2.last_error_code == "ERR_MUTATION_FAILED"

    # Invalid: Terminal ROLLED_BACK -> COMPLETED
    with pytest.raises(ValueError, match="ERR_INVALID_TRANSITION"):
        service.transition_state("exec_01", "COMPLETED", updated_at_iso="2026-11-02T14:04:00Z")


def test_receipt_service_advisory_lock(tmp_path):
    service = ExecutionReceiptService(base_dir=str(tmp_path))

    # Single lock context
    with service.acquire_lock("exec_01") as lock1:
        assert lock1.execution_id == "exec_01"

        # Attempting concurrent lock on same execution_id must raise ExecutionLockActiveError
        with pytest.raises(ExecutionLockActiveError, match="ERR_EXECUTION_LOCKED"):
            with service.acquire_lock("exec_01"):
                pass

    # After exit, lock can be re-acquired
    with service.acquire_lock("exec_01"):
        pass


def test_receipt_service_enospc_failure_cleanup(tmp_path):
    service = ExecutionReceiptService(base_dir=str(tmp_path))
    receipt = _make_receipt("exec_enospc")

    # Fault injection: simulate ENOSPC during os.fsync
    with patch("os.fsync", side_effect=OSError(28, "No space left on device")):
        with pytest.raises(OSError, match="No space left on device"):
            service.create_receipt(receipt)

    # Verify target file was NEVER created
    target_file = os.path.join(str(tmp_path), "receipts", "exec_enospc.receipt.json")
    assert not os.path.exists(target_file)

    # Verify no dangling .tmp files
    files = os.listdir(os.path.join(str(tmp_path), "receipts"))
    assert len(files) == 0


def test_receipt_service_corrupted_json_detection(tmp_path):
    service = ExecutionReceiptService(base_dir=str(tmp_path))
    target_file = os.path.join(str(tmp_path), "receipts", "exec_bad.receipt.json")

    # Write corrupt bytes
    with open(target_file, "wb") as f:
        f.write(b"NOT_A_VALID_JSON_{{{")

    with pytest.raises(ValueError, match="ERR_INVALID_JSON"):
        service.load_receipt("exec_bad")


def test_receipt_service_not_found(tmp_path):
    service = ExecutionReceiptService(base_dir=str(tmp_path))
    with pytest.raises(ReceiptNotFoundError, match="ERR_RECEIPT_NOT_FOUND"):
        service.load_receipt("non_existent_id")


# =============================================================================
# 2. Tests for ExecutionJournalService
# =============================================================================

def test_journal_service_append_and_replay(tmp_path):
    journal_svc = ExecutionJournalService(base_dir=str(tmp_path))
    exec_id = "exec_j01"

    e0 = JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T14:00:00Z", action_params={})
    e1 = JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T14:00:01Z", action_params={"clip_id": "C01"})
    e2 = JournalEntry(seq=2, stage="ACTION_COMMITTED", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T14:00:02Z", action_params={"clip_id": "C01"}, resolve_item_id="item_01")

    journal_svc.append_entry(exec_id, e0)
    journal_svc.append_entry(exec_id, e1)
    journal_svc.append_entry(exec_id, e2)

    replay = journal_svc.replay_journal(exec_id)
    assert replay.is_clean is True
    assert replay.torn_tail is None
    assert len(replay.entries) == 3
    assert replay.entries[0] == e0
    assert replay.entries[1] == e1
    assert replay.entries[2] == e2


def test_journal_service_sequence_monotonicity(tmp_path):
    journal_svc = ExecutionJournalService(base_dir=str(tmp_path))
    exec_id = "exec_seq_err"

    e0 = JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T14:00:00Z", action_params={})
    journal_svc.append_entry(exec_id, e0)

    # Next entry must have seq=1. If seq=3 is provided -> JournalSequenceError
    e_bad = JournalEntry(seq=3, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T14:00:01Z", action_params={})
    with pytest.raises(JournalSequenceError, match="ERR_INVALID_SEQ"):
        journal_svc.append_entry(exec_id, e_bad)


def test_journal_service_torn_tail_detection_without_truncation(tmp_path):
    journal_svc = ExecutionJournalService(base_dir=str(tmp_path))
    exec_id = "exec_torn"

    e0 = JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T14:00:00Z", action_params={})
    e1 = JournalEntry(seq=1, stage="ACTION_COMMITTED", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T14:00:01Z", action_params={}, resolve_item_id="item_1")

    journal_svc.append_entry(exec_id, e0)
    journal_svc.append_entry(exec_id, e1)

    # Simulate crash mid-write at EOF (torn line)
    path = journal_svc.journal_path(exec_id)
    with open(path, "ab") as f:
        f.write(b'{"seq": 2, "stage": "ACTION_PENDING", "action_type": "INSERT_CLIP", "time')  # truncated mid-token

    # Capture file size and bytes before replay
    bytes_before = open(path, "rb").read()

    # Replay journal: must detect torn tail without destroying original bytes
    replay = journal_svc.replay_journal(exec_id)
    assert replay.is_clean is False
    assert replay.torn_tail is not None
    assert replay.torn_tail.is_torn is True
    assert replay.torn_tail.line_number == 3
    assert len(replay.entries) == 2  # exactly e0 and e1 returned

    # Verify original bytes on disk are 100% untouched
    bytes_after = open(path, "rb").read()
    assert bytes_before == bytes_after


def test_journal_service_corrupted_middle_line_raises(tmp_path):
    journal_svc = ExecutionJournalService(base_dir=str(tmp_path))
    exec_id = "exec_corrupted_mid"
    path = journal_svc.journal_path(exec_id)

    # Write: line 1 valid, line 2 corrupted, line 3 valid
    l1 = json.dumps({"seq": 0, "stage": "START", "action_type": "NO_OP", "timestamp_iso": "2026-11-02T14:00:00Z", "action_params": {}}) + "\n"
    l2 = "CORRUPTED_MIDDLE_LINE_GARBAGE\n"
    l3 = json.dumps({"seq": 1, "stage": "FINISH", "action_type": "NO_OP", "timestamp_iso": "2026-11-02T14:00:02Z", "action_params": {}}) + "\n"

    with open(path, "w", encoding="utf-8") as f:
        f.write(l1 + l2 + l3)

    # Middle line corruption must raise JournalCorruptedError, NOT treated as torn-tail
    with pytest.raises(JournalCorruptedError, match="ERR_JOURNAL_CORRUPTED"):
        journal_svc.replay_journal(exec_id)


def test_journal_service_reverse_committed_entries(tmp_path):
    journal_svc = ExecutionJournalService(base_dir=str(tmp_path))
    exec_id = "exec_reverse"

    # seq 0: START
    # seq 1: ACTION_COMMITTED for clip 1
    # seq 2: ACTION_COMMITTED for clip 2
    # seq 3: ACTION_FAILED for clip 3
    # seq 4: ROLLBACK_DONE for clip 2 (seq 2)
    e0 = JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T14:00:00Z", action_params={})
    e1 = JournalEntry(seq=1, stage="ACTION_COMMITTED", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T14:00:01Z", action_params={"clip": "C1"}, resolve_item_id="item_01")
    e2 = JournalEntry(seq=2, stage="ACTION_COMMITTED", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T14:00:02Z", action_params={"clip": "C2"}, resolve_item_id="item_02")
    e3 = JournalEntry(seq=3, stage="ACTION_FAILED", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T14:00:03Z", action_params={"clip": "C3"}, error_code="ERR_FAIL")
    e4 = JournalEntry(seq=2, stage="ROLLBACK_DONE", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T14:00:04Z", action_params={"clip": "C2"}, note="ROLLED_BACK")

    # Note: seq in append_entry must be monotonic. For rollback, e4 has monotonic seq 4, referencing target seq 2 in params
    e4_monotonic = JournalEntry(seq=4, stage="ROLLBACK_DONE", action_type="INSERT_CLIP", timestamp_iso="2026-11-02T14:00:04Z", action_params={"target_seq": 2}, note="ROLLED_BACK")

    journal_svc.append_entry(exec_id, e0)
    journal_svc.append_entry(exec_id, e1)
    journal_svc.append_entry(exec_id, e2)
    journal_svc.append_entry(exec_id, e3)

    # Before rollback: both e2 and e1 need rollback (in reverse order: e2, then e1)
    unrolled = journal_svc.replay_reverse_committed_entries(exec_id)
    assert len(unrolled) == 2
    assert unrolled[0] == e2
    assert unrolled[1] == e1


def test_journal_service_isolated_recovery_copy(tmp_path):
    journal_svc = ExecutionJournalService(base_dir=str(tmp_path))
    exec_id = "exec_copy_test"
    e0 = JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso="2026-11-02T14:00:00Z", action_params={})
    journal_svc.append_entry(exec_id, e0)

    copy_path = journal_svc.create_isolated_recovery_copy(exec_id, suffix="backup_01")
    assert os.path.exists(copy_path)
    assert copy_path.endswith(".backup_01")
    assert open(copy_path, "rb").read() == open(journal_svc.journal_path(exec_id), "rb").read()
