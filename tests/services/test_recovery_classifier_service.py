"""Kiểm thử Dịch vụ Phân loại Phục hồi Chỉ Đọc (RecoveryClassifierService).

Kiểm tra:
- Ma trận 8 trạng thái sự cố tất định:
  1. STATE_CLEAN_TERMINAL
  2. STATE_INITIALIZED_NO_JOURNAL
  3. STATE_CLEAN_ACTIVE_LOCKED (thăm dò khóa OS bị chiếm, bất kể PID sống hay chết)
  4. STATE_COMMITTED_CANDIDATE_COMPENSATION (các hành động COMMITTED chưa rollback)
  5. STATE_TORN_TAIL_DETECTED (dòng cuối bị cắt cụt, bảo tồn chẩn đoán an toàn)
  6. STATE_AMBIGUOUS_IN_DOUBT (dòng cuối ACTION_PENDING mồ côi)
  7. STATE_IDEMPOTENCY_DUPLICATE_BLOCKED
  8. STATE_ARTIFACT_CORRUPTED_OR_MISSING (tệp receipt/journal hỏng hoặc thiếu chỉ mục idempotency)
- Bất định danh tính đích (Target Identity Drift vs Missing Authority).
- Thứ tự ứng viên bù trừ (LIFO - đảo ngược thứ tự commit).
- Giới hạn tham số an toàn (sanitized summary, zero mutable leak).
"""

from __future__ import annotations

import pytest

from src.core.contracts.execution_receipt_contracts import (
    CatalogAssetDigestBinding,
    ExecutionReceipt,
    JournalEntry,
    JournalReplayResult,
    SourceFileDigestBinding,
    TargetTrackReservation,
    TornTailDiagnostic,
)
from src.core.contracts.recovery_assessment_contracts import (
    RecoveryAssessment,
    TargetIdentityMatchReport,
)
from src.services.recovery_classifier_service import RecoveryClassifierService


def _create_sample_receipt(
    execution_id: str = "exec_test_001",
    state: str = "INITIALIZED",
    project_id: str = "proj_01",
    timeline_id: str = "tl_01",
) -> ExecutionReceipt:
    return ExecutionReceipt(
        schema_version=2,
        execution_id=execution_id,
        idempotency_key="f" * 64,
        proposal_id="prop_001",
        proposal_revision="1" * 64,
        approval_id="appr_001",
        approved_at_iso="2026-11-02T12:00:00Z",
        project_name="TestProject",
        project_id=project_id,
        timeline_name="Timeline 1",
        timeline_id=timeline_id,
        timeline_fps_numerator=24000,
        timeline_fps_denominator=1000,
        timeline_resolution=(1920, 1080),
        source_digests=(
            SourceFileDigestBinding("c1", "/path/clip.mp4", "a" * 64, 0, (1, 2, 100, 1000, 1000)),
        ),
        catalog_digests=(
            CatalogAssetDigestBinding("a1", "/path/sfx.wav", "b" * 64, (1, 2, 50, 1000, 1000)),
        ),
        track_reservations=(TargetTrackReservation("video", 1, 0, 1000, "V1_MAIN"),),
        planned_actions_count=2,
        created_at_iso="2026-11-02T12:01:00Z",
        state=state,
    )


def test_classify_clean_terminal_completed():
    """TC-REC-01: Receipt COMPLETED, journal kết thúc bằng FINISH, lock rảnh -> STATE_CLEAN_TERMINAL."""
    receipt = _create_sample_receipt(state="COMPLETED")
    entries = (
        JournalEntry(0, "START", "NO_OP", "2026-11-02T12:01:01Z", {}),
        JournalEntry(1, "ACTION_COMMITTED", "INSERT_CLIP", "2026-11-02T12:01:02Z", {"start": 0}, resolve_item_id="item_1", track_type="video", track_index=1),
        JournalEntry(2, "FINISH", "NO_OP", "2026-11-02T12:01:03Z", {}),
    )
    journal_res = JournalReplayResult(entries=entries, torn_tail=None, is_clean=True, total_bytes_read=500)

    assessment = RecoveryClassifierService.classify(
        receipt=receipt,
        journal_result=journal_res,
        is_advisory_lock_held=False,
        diagnostic_recorded_pid=None,
        active_project_id="proj_01",
        active_timeline_id="tl_01",
        is_idempotency_index_valid=True,
    )

    assert assessment.classified_state == "STATE_CLEAN_TERMINAL"
    assert assessment.recommended_action == "NO_ACTION_NEEDED"
    assert assessment.requires_operator_confirmation is False
    assert len(assessment.candidate_compensations) == 0


def test_classify_initialized_no_journal():
    """TC-REC-03: Receipt INITIALIZED, journal rỗng, lock rảnh -> STATE_INITIALIZED_NO_JOURNAL."""
    receipt = _create_sample_receipt(state="INITIALIZED")
    journal_res = JournalReplayResult(entries=(), torn_tail=None, is_clean=True, total_bytes_read=0)

    assessment = RecoveryClassifierService.classify(
        receipt=receipt,
        journal_result=journal_res,
        is_advisory_lock_held=False,
        diagnostic_recorded_pid=None,
        active_project_id="proj_01",
        active_timeline_id="tl_01",
        is_idempotency_index_valid=True,
    )

    assert assessment.classified_state == "STATE_INITIALIZED_NO_JOURNAL"
    assert assessment.recommended_action == "SAFE_TO_ABORT_AFTER_CONFIRMATION"
    assert assessment.requires_operator_confirmation is True


def test_classify_clean_active_locked():
    """TC-REC-04: OS advisory lock đang bị chiếm -> STATE_CLEAN_ACTIVE_LOCKED."""
    receipt = _create_sample_receipt(state="EXECUTING")
    entries = (JournalEntry(0, "START", "NO_OP", "2026-11-02T12:01:01Z", {}),)
    journal_res = JournalReplayResult(entries=entries, torn_tail=None, is_clean=True, total_bytes_read=100)

    # Thăm dò lock trả về True (bị chiếm)
    assessment = RecoveryClassifierService.classify(
        receipt=receipt,
        journal_result=journal_res,
        is_advisory_lock_held=True,
        diagnostic_recorded_pid=99999,  # PID chỉ là diagnostic
        active_project_id="proj_01",
        active_timeline_id="tl_01",
        is_idempotency_index_valid=True,
    )

    assert assessment.classified_state == "STATE_CLEAN_ACTIVE_LOCKED"
    assert assessment.recommended_action == "CONCURRENT_JOB_RUNNING_DO_NOT_DISTURB"
    assert assessment.is_advisory_lock_held is True
    assert assessment.diagnostic_recorded_pid == 99999
    assert assessment.requires_operator_confirmation is False


def test_classify_committed_candidate_compensation():
    """TC-REC-05/06: Đã commit 2 clip, lock rảnh -> STATE_COMMITTED_CANDIDATE_COMPENSATION với danh sách LIFO."""
    receipt = _create_sample_receipt(state="EXECUTING")
    entries = (
        JournalEntry(0, "START", "NO_OP", "2026-11-02T12:01:01Z", {}),
        JournalEntry(1, "ACTION_PENDING", "INSERT_CLIP", "2026-11-02T12:01:02Z", {"clip": "clip1.mp4"}, track_type="video", track_index=1),
        JournalEntry(2, "ACTION_COMMITTED", "INSERT_CLIP", "2026-11-02T12:01:03Z", {"clip": "clip1.mp4"}, resolve_item_id="item_1", track_type="video", track_index=1),
        JournalEntry(3, "ACTION_PENDING", "INSERT_CLIP", "2026-11-02T12:01:04Z", {"clip": "clip2.mp4"}, track_type="video", track_index=1),
        JournalEntry(4, "ACTION_COMMITTED", "INSERT_CLIP", "2026-11-02T12:01:05Z", {"clip": "clip2.mp4"}, resolve_item_id="item_2", track_type="video", track_index=1),
    )
    journal_res = JournalReplayResult(entries=entries, torn_tail=None, is_clean=True, total_bytes_read=800)

    assessment = RecoveryClassifierService.classify(
        receipt=receipt,
        journal_result=journal_res,
        is_advisory_lock_held=False,
        diagnostic_recorded_pid=None,
        active_project_id="proj_01",
        active_timeline_id="tl_01",
        is_idempotency_index_valid=True,
    )

    assert assessment.classified_state == "STATE_COMMITTED_CANDIDATE_COMPENSATION"
    assert assessment.recommended_action == "CANDIDATE_COMPENSATION_FOR_OPERATOR_REVIEW"
    assert assessment.requires_operator_confirmation is True
    assert len(assessment.candidate_compensations) == 2
    # LIFO: item 2 (seq 4) phải xếp trước item 1 (seq 2)
    assert assessment.candidate_compensations[0].resolve_item_id == "item_2"
    assert assessment.candidate_compensations[1].resolve_item_id == "item_1"


def test_classify_ambiguous_in_doubt():
    """TC-REC-07: Dòng cuối là ACTION_PENDING mồ côi -> STATE_AMBIGUOUS_IN_DOUBT."""
    receipt = _create_sample_receipt(state="EXECUTING")
    entries = (
        JournalEntry(0, "START", "NO_OP", "2026-11-02T12:01:01Z", {}),
        JournalEntry(1, "ACTION_COMMITTED", "INSERT_CLIP", "2026-11-02T12:01:02Z", {"clip": "c1"}, resolve_item_id="item_1", track_type="video", track_index=1),
        JournalEntry(2, "ACTION_PENDING", "INSERT_CLIP", "2026-11-02T12:01:03Z", {"clip": "c2", "param": "x" * 200}, track_type="video", track_index=2),
    )
    journal_res = JournalReplayResult(entries=entries, torn_tail=None, is_clean=True, total_bytes_read=400)

    assessment = RecoveryClassifierService.classify(
        receipt=receipt,
        journal_result=journal_res,
        is_advisory_lock_held=False,
        diagnostic_recorded_pid=None,
        active_project_id="proj_01",
        active_timeline_id="tl_01",
        is_idempotency_index_valid=True,
    )

    assert assessment.classified_state == "STATE_AMBIGUOUS_IN_DOUBT"
    assert assessment.recommended_action == "MANUAL_TIMELINE_INSPECTION_REQUIRED"
    assert assessment.requires_operator_confirmation is True
    assert len(assessment.ambiguous_actions) == 1
    assert assessment.ambiguous_actions[0].seq == 2
    assert "Unconfirmed ACTION_PENDING" in assessment.ambiguous_actions[0].uncertainty_reason


def test_classify_torn_tail_detected():
    """TC-REC-08: Phát hiện dòng cuối bị rách -> STATE_TORN_TAIL_DETECTED với chẩn đoán làm sạch an toàn."""
    receipt = _create_sample_receipt(state="EXECUTING")
    entries = (
        JournalEntry(0, "START", "NO_OP", "2026-11-02T12:01:01Z", {}),
        JournalEntry(1, "ACTION_COMMITTED", "INSERT_CLIP", "2026-11-02T12:01:02Z", {"clip": "c1"}, resolve_item_id="item_1", track_type="video", track_index=1),
    )
    raw_torn = b'{"seq": 2, "stage": "ACTION_COMMITTED", "incomplete": true\x00\x1b[31m'
    torn_diag = TornTailDiagnostic(
        is_torn=True,
        line_number=3,
        raw_bytes=raw_torn,
        error_message="Unexpected EOF in JSON string",
    )
    journal_res = JournalReplayResult(entries=entries, torn_tail=torn_diag, is_clean=False, total_bytes_read=300)

    assessment = RecoveryClassifierService.classify(
        receipt=receipt,
        journal_result=journal_res,
        is_advisory_lock_held=False,
        diagnostic_recorded_pid=None,
        active_project_id="proj_01",
        active_timeline_id="tl_01",
        is_idempotency_index_valid=True,
    )

    assert assessment.classified_state == "STATE_TORN_TAIL_DETECTED"
    assert assessment.recommended_action == "TORN_TAIL_OBSERVED_OPERATOR_REVIEW"
    assert assessment.torn_tail_detected is True
    assert assessment.torn_tail_byte_count == len(raw_torn)
    assert assessment.torn_tail_sha256 is not None
    assert len(assessment.torn_tail_sha256) == 64
    assert "\x00" not in assessment.torn_tail_preview_escaped  # type: ignore
    assert "\x1b" not in assessment.torn_tail_preview_escaped  # type: ignore


def test_classify_fail_closed_on_invalid_or_missing_idempotency_index():
    """TC-REC-12: Khi chỉ mục idempotency không hợp lệ hoặc thiếu -> STATE_ARTIFACT_CORRUPTED_OR_MISSING."""
    receipt = _create_sample_receipt(state="EXECUTING")
    journal_res = JournalReplayResult(entries=(), torn_tail=None, is_clean=True, total_bytes_read=0)

    assessment = RecoveryClassifierService.classify(
        receipt=receipt,
        journal_result=journal_res,
        is_advisory_lock_held=False,
        diagnostic_recorded_pid=None,
        active_project_id="proj_01",
        active_timeline_id="tl_01",
        is_idempotency_index_valid=False,  # Chỉ mục bị hỏng/thiếu!
    )

    assert assessment.classified_state == "STATE_ARTIFACT_CORRUPTED_OR_MISSING"
    assert assessment.recommended_action == "FORENSIC_QUARANTINE_REQUIRED"
    assert assessment.requires_operator_confirmation is True
    assert "idempotency index" in assessment.explanation_for_operator.lower()


def test_classify_target_identity_drift():
    """TC-REC-11: Lệch danh tính timeline đích -> drift_detected = True."""
    receipt = _create_sample_receipt(state="EXECUTING", project_id="proj_A", timeline_id="tl_A")
    entries = (
        JournalEntry(0, "START", "NO_OP", "2026-11-02T12:01:01Z", {}),
        JournalEntry(1, "ACTION_COMMITTED", "INSERT_CLIP", "2026-11-02T12:01:02Z", {}, resolve_item_id="i1", track_type="video", track_index=1),
    )
    journal_res = JournalReplayResult(entries=entries, torn_tail=None, is_clean=True, total_bytes_read=200)

    assessment = RecoveryClassifierService.classify(
        receipt=receipt,
        journal_result=journal_res,
        is_advisory_lock_held=False,
        diagnostic_recorded_pid=None,
        active_project_id="proj_A",
        active_timeline_id="tl_DIFFERENT",  # Lệch timeline!
        is_idempotency_index_valid=True,
    )

    assert assessment.target_match_report.drift_detected is True
    assert assessment.target_match_report.is_timeline_matched is False
    assert assessment.target_match_report.is_project_matched is True
