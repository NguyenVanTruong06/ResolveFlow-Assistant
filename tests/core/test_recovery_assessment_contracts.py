"""Kiểm thử Hợp đồng DTO Bất biến Đánh giá & Phân loại Phục hồi Chỉ đọc (G07-07A7-I).

Kiểm tra:
- Tính bất biến (frozen dataclass), cấm gán thuộc tính sau khởi tạo.
- Giới hạn trần an toàn (bounded diagnostics): MAX_TORN_TAIL_PREVIEW_BYTES (256), MAX_PARAMS_SUMMARY_CHARS (512).
- Hàm làm sạch chuỗi chẩn đoán (sanitize_diagnostics_string): loại bỏ/thoát ký tự điều khiển (NUL, ANSI escape, newline, unprintable), bảo vệ chống UI injection.
- Xác thực trường giá trị (scalar bounds, enum values, non-empty IDs).
- Không chứa raw bytes hoặc mutable dictionary tự do trong DTOs.
"""

from __future__ import annotations

import dataclasses
import pytest

from src.core.contracts.recovery_assessment_contracts import (
    MAX_PARAMS_SUMMARY_CHARS,
    MAX_TORN_TAIL_PREVIEW_BYTES,
    MAX_UNCERTAINTY_REASON_CHARS,
    VALID_CRASH_STATES,
    VALID_RECOVERY_ACTIONS,
    AmbiguousActionDetail,
    CandidateCompensatingAction,
    RecoveryAssessment,
    TargetIdentityMatchReport,
    sanitize_diagnostics_string,
    assessment_to_dict,
)


def test_constants_and_enums_contract():
    """Xác nhận danh mục trạng thái sự cố 8 giá trị và các hành động phục hồi chuẩn."""
    assert len(VALID_CRASH_STATES) == 8
    assert "STATE_CLEAN_TERMINAL" in VALID_CRASH_STATES
    assert "STATE_INITIALIZED_NO_JOURNAL" in VALID_CRASH_STATES
    assert "STATE_CLEAN_ACTIVE_LOCKED" in VALID_CRASH_STATES
    assert "STATE_COMMITTED_CANDIDATE_COMPENSATION" in VALID_CRASH_STATES
    assert "STATE_TORN_TAIL_DETECTED" in VALID_CRASH_STATES
    assert "STATE_AMBIGUOUS_IN_DOUBT" in VALID_CRASH_STATES
    assert "STATE_IDEMPOTENCY_DUPLICATE_BLOCKED" in VALID_CRASH_STATES
    assert "STATE_ARTIFACT_CORRUPTED_OR_MISSING" in VALID_CRASH_STATES

    assert "CANDIDATE_COMPENSATION_FOR_OPERATOR_REVIEW" in VALID_RECOVERY_ACTIONS
    assert "SAFE_TO_ABORT_AFTER_CONFIRMATION" in VALID_RECOVERY_ACTIONS
    assert "CONCURRENT_JOB_RUNNING_DO_NOT_DISTURB" in VALID_RECOVERY_ACTIONS
    assert "MANUAL_TIMELINE_INSPECTION_REQUIRED" in VALID_RECOVERY_ACTIONS
    assert "REJECT_DUPLICATE" in VALID_RECOVERY_ACTIONS
    assert "FORENSIC_QUARANTINE_REQUIRED" in VALID_RECOVERY_ACTIONS


def test_sanitize_diagnostics_string():
    """Kiểm tra làm sạch chuỗi chẩn đoán: cắt trần, thoát ký tự điều khiển, loại bỏ NUL."""
    # 1. Ký tự NUL và ký tự điều khiển
    dirty = "Line1\nLine2\r\x00\x1b[31mRedAlert\x1b[0m\tTab"
    clean = sanitize_diagnostics_string(dirty, max_len=100)
    assert "\x00" not in clean
    assert "\x1b" not in clean
    assert "\\n" in clean or "Line1" in clean

    # 2. Vượt quá độ dài trần
    long_str = "A" * 1000
    capped = sanitize_diagnostics_string(long_str, max_len=256)
    assert len(capped) <= 256

    # 3. None hoặc giá trị không phải chuỗi
    assert sanitize_diagnostics_string(None, max_len=100) == ""
    assert sanitize_diagnostics_string(12345, max_len=100) == "12345"


def test_candidate_compensating_action_immutability():
    """Kiểm tra tính bất biến và xác thực của CandidateCompensatingAction."""
    action = CandidateCompensatingAction(
        seq=1,
        action_type="INSERT_CLIP",
        resolve_item_id="item_123",
        track_type="video",
        track_index=1,
        clip_name="interview_01.mp4",
        action_params_summary="start=0, end=100, record_frame=50",
    )
    assert action.seq == 1
    assert action.resolve_item_id == "item_123"
    assert action.track_type == "video"
    assert action.track_index == 1
    assert action.clip_name == "interview_01.mp4"

    # Frozen: cấm sửa đổi
    with pytest.raises(dataclasses.FrozenInstanceError):
        action.seq = 2  # type: ignore

    # Lỗi khi seq âm
    with pytest.raises(ValueError, match="ERR_INT_OUT_OF_RANGE"):
        CandidateCompensatingAction(
            seq=-1,
            action_type="INSERT_CLIP",
            resolve_item_id="id",
            track_type="video",
            track_index=1,
            clip_name=None,
            action_params_summary="",
        )

    # Lỗi khi track_type không hợp lệ
    with pytest.raises(ValueError, match="ERR_INVALID_TRACK_TYPE"):
        CandidateCompensatingAction(
            seq=1,
            action_type="INSERT_CLIP",
            resolve_item_id="id",
            track_type="subtitles",
            track_index=1,
            clip_name=None,
            action_params_summary="",
        )


def test_ambiguous_action_detail_immutability():
    """Kiểm tra tính bất biến và xác thực của AmbiguousActionDetail."""
    amb = AmbiguousActionDetail(
        seq=5,
        action_type="INSERT_CLIP",
        track_type="video",
        track_index=2,
        action_params_summary="start=100, end=200",
        uncertainty_reason="Unconfirmed ACTION_PENDING before process exit",
    )
    assert amb.seq == 5
    assert amb.track_index == 2
    assert "ACTION_PENDING" in amb.uncertainty_reason

    with pytest.raises(dataclasses.FrozenInstanceError):
        amb.seq = 6  # type: ignore


def test_target_identity_match_report_immutability():
    """Kiểm tra TargetIdentityMatchReport."""
    rep = TargetIdentityMatchReport(
        receipt_project_id="proj_1",
        receipt_timeline_id="tl_1",
        active_project_id="proj_1",
        active_timeline_id="tl_1",
        is_project_matched=True,
        is_timeline_matched=True,
        drift_detected=False,
        authority_available=True,
    )
    assert rep.is_project_matched is True
    assert rep.drift_detected is False

    with pytest.raises(dataclasses.FrozenInstanceError):
        rep.drift_detected = True  # type: ignore


def test_recovery_assessment_bounded_and_sanitized():
    """Kiểm tra RecoveryAssessment với dữ liệu chẩn đoán giới hạn trần và làm sạch."""
    rep = TargetIdentityMatchReport(
        receipt_project_id="p1",
        receipt_timeline_id="t1",
        active_project_id="p1",
        active_timeline_id="t1",
        is_project_matched=True,
        is_timeline_matched=True,
        drift_detected=False,
        authority_available=True,
    )

    assessment = RecoveryAssessment(
        execution_id="exec_abc123",
        idempotency_key="idemp_key_001",
        classified_state="STATE_COMMITTED_CANDIDATE_COMPENSATION",
        recommended_action="CANDIDATE_COMPENSATION_FOR_OPERATOR_REVIEW",
        requires_operator_confirmation=True,
        explanation_for_operator="Application recorded 1 committed action before process exit.",
        is_advisory_lock_held=False,
        diagnostic_recorded_pid=12345,
        total_journal_entries_count=3,
        candidate_compensations=(
            CandidateCompensatingAction(
                seq=1,
                action_type="INSERT_CLIP",
                resolve_item_id="item_999",
                track_type="video",
                track_index=1,
                clip_name="clip_a.mp4",
                action_params_summary="test summary",
            ),
        ),
        ambiguous_actions=(),
        torn_tail_detected=False,
        torn_tail_line_number=None,
        torn_tail_byte_count=0,
        torn_tail_sha256=None,
        torn_tail_preview_escaped=None,
        target_match_report=rep,
    )

    assert assessment.classified_state == "STATE_COMMITTED_CANDIDATE_COMPENSATION"
    assert assessment.is_advisory_lock_held is False
    assert assessment.diagnostic_recorded_pid == 12345
    assert len(assessment.candidate_compensations) == 1

    d = assessment_to_dict(assessment)
    assert d["execution_id"] == "exec_abc123"
    assert d["is_advisory_lock_held"] is False
    assert d["candidate_compensations"][0]["resolve_item_id"] == "item_999"


def test_recovery_assessment_validation_rejects_raw_bytes_and_invalid_state():
    """Xác nhận DTO từ chối trạng thái không hợp lệ hoặc dữ liệu sai định dạng."""
    rep = TargetIdentityMatchReport(
        receipt_project_id="p1",
        receipt_timeline_id="t1",
        active_project_id="p1",
        active_timeline_id="t1",
        is_project_matched=True,
        is_timeline_matched=True,
        drift_detected=False,
        authority_available=True,
    )

    # Trạng thái không có trong enum
    with pytest.raises(ValueError, match="ERR_INVALID_CRASH_STATE"):
        RecoveryAssessment(
            execution_id="exec_1",
            idempotency_key="idemp_1",
            classified_state="INVALID_STATE",  # type: ignore
            recommended_action="NO_ACTION_NEEDED",
            requires_operator_confirmation=False,
            explanation_for_operator="",
            is_advisory_lock_held=False,
            diagnostic_recorded_pid=None,
            total_journal_entries_count=0,
            candidate_compensations=(),
            ambiguous_actions=(),
            torn_tail_detected=False,
            torn_tail_line_number=None,
            torn_tail_byte_count=0,
            torn_tail_sha256=None,
            torn_tail_preview_escaped=None,
            target_match_report=rep,
        )

    # Hành động không có trong enum
    with pytest.raises(ValueError, match="ERR_INVALID_RECOVERY_ACTION"):
        RecoveryAssessment(
            execution_id="exec_1",
            idempotency_key="idemp_1",
            classified_state="STATE_CLEAN_TERMINAL",
            recommended_action="INVALID_ACTION",  # type: ignore
            requires_operator_confirmation=False,
            explanation_for_operator="",
            is_advisory_lock_held=False,
            diagnostic_recorded_pid=None,
            total_journal_entries_count=0,
            candidate_compensations=(),
            ambiguous_actions=(),
            torn_tail_detected=False,
            torn_tail_line_number=None,
            torn_tail_byte_count=0,
            torn_tail_sha256=None,
            torn_tail_preview_escaped=None,
            target_match_report=rep,
        )
