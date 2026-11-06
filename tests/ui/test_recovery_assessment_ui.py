"""Kiểm thử Giao diện Đối soát & Phục hồi Chỉ Đọc (Read-Only Recovery Assessment UI — G07-07A8-I).

Bao quát 12 kịch bản nghiệm thu hữu hạn (TC-UI-REC-01 đến TC-UI-REC-12) + Bẫy không đột biến (Zero-Mutation):
- TC-UI-REC-01: Hiển thị Clean Terminal (ghi nhận quá khứ, không cam kết timeline Resolve hiện tại).
- TC-UI-REC-02: Hiển thị Active Process Locked (báo PID giữ OS lock, không tuyên bố "chạy bình thường", chỉ cho Đóng).
- TC-UI-REC-03: Hiển thị Candidate Compensations (LIFO order, max 100 rows, lưu ý ứng dụng quá khứ, ready_to_execute=False).
- TC-UI-REC-04: Hiển thị In-Doubt Ambiguity (vùng bất định ACTION_PENDING, cấm retry tự động, yêu cầu nhìn mắt thường).
- TC-UI-REC-05: Hiển thị Torn-Tail Diagnostics (byte count, hex SHA-256, escaped preview <= 256 ký tự, PlainText).
- TC-UI-REC-06: Hiển thị Artifacts Corrupted (JSON/Idempotency hỏng cấu trúc, đóng băng forensic, cấm chạy đè).
- TC-UI-REC-07: Cảnh báo Target Identity Drift (lệch danh tính Timeline ID, khóa đối chiếu bù trừ).
- TC-UI-REC-08: Resolve Authority Unavailable (snapshot không khả dụng, phân loại bảo thủ).
- TC-UI-REC-09: Phòng vệ UI Injection (chuỗi chứa HTML script, ANSI escape, NUL render nguyên văn dạng PlainText).
- TC-UI-REC-10: Giới hạn trần bảng (vượt quá 100 dòng -> hiển thị đúng 100 dòng kèm nhãn thống kê tổng).
- TC-UI-REC-11: Lỗi truy cập I/O hệ điều hành (EACCES/EIO -> hiển thị khung lỗi riêng, không quy chụp hỏng forensic).
- TC-UI-REC-12: Ranh giới đơn luồng Resolve & Khóa không chiếm giữ (Worker 0 Resolve calls, non-retaining lock probe).
- TC-UI-REC-13: Bẫy không đột biến (Zero-Mutation Tripwire: 100% tệp đĩa nguyên vẹn trước và sau tương tác).
- TC-UI-REC-14: Tích hợp nút kiểm tra phiên cũ trên BuildPage (bảo toàn ready_to_execute=False).
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from src.core.contracts.execution_receipt_contracts import (
    ExecutionReceipt,
    JournalEntry,
    SourceFileDigestBinding,
    TargetTrackReservation,
    journal_entry_to_json_line,
    receipt_to_json,
)
from src.core.contracts.recovery_assessment_contracts import (
    AmbiguousActionDetail,
    CandidateCompensatingAction,
    RecoveryAssessment,
    TargetIdentityMatchReport,
)
from src.core.resolve_bridge import RationalFPS, ResolveTimelineAuthority
from src.services.execution_journal_service import ExecutionJournalService
from src.services.recovery_coordinator import RecoveryCoordinator
from src.ui.director.pages.build_page import BuildPage
from src.ui.director.widgets.recovery_assessment_dialog import RecoveryAssessmentDialog
from src.ui.director.workers.recovery_assessment_worker import RecoveryAssessmentWorker


def _hash_file(path: str) -> str:
    """Hàm băm SHA-256 nội dung tệp."""
    if not os.path.exists(path):
        return "NON_EXISTENT"
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _hash_directory_tree(base_dir: str) -> Dict[str, str]:
    """Băm toàn bộ cây thư mục để làm bẫy Zero-Mutation."""
    hashes: Dict[str, str] = {}
    for root, _, files in os.walk(base_dir):
        for f in sorted(files):
            full_p = os.path.join(root, f)
            rel_p = os.path.relpath(full_p, base_dir)
            hashes[rel_p] = _hash_file(full_p)
    return hashes


@pytest.fixture
def test_env():
    """Tạo môi trường thư mục tạm cho biên bản, nhật ký và khóa."""
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


def _create_test_receipt(
    env: Dict[str, str],
    execution_id: str,
    idempotency_key: str = "a" * 64,
    status: str = "COMPLETED",
    project_id: str = "proj_001",
    timeline_id: str = "tl_001",
) -> ExecutionReceipt:
    digest = SourceFileDigestBinding(
        clip_id="clip_1",
        absolute_path="/media/footage/clip1.mp4",
        full_sha256="c" * 64,
        stream_index=0,
        stat_identity=(1, 2, 3, 4, 5),
    )
    reservation = TargetTrackReservation(
        track_type="video",
        track_index=1,
        reserved_start_frame=0,
        reserved_end_frame=100,
        intended_usage="broll",
    )
    receipt = ExecutionReceipt(
        schema_version=2,
        execution_id=execution_id,
        idempotency_key=idempotency_key,
        proposal_id="prop_01",
        proposal_revision="1" * 64,
        approval_id="appr_01",
        approved_at_iso="2026-11-02T12:00:00Z",
        project_name="P1",
        project_id=project_id,
        timeline_name="T1",
        timeline_id=timeline_id,
        timeline_fps_numerator=24000,
        timeline_fps_denominator=1000,
        timeline_resolution=(1920, 1080),
        source_digests=(digest,),
        catalog_digests=(),
        track_reservations=(reservation,),
        planned_actions_count=1,
        created_at_iso="2026-11-02T12:01:00Z",
        state=status,
    )
    receipt_path = os.path.join(env["receipts_dir"], f"{execution_id}.receipt.json")
    with open(receipt_path, "w", encoding="utf-8") as f:
        f.write(receipt_to_json(receipt))

    idemp_path = os.path.join(env["idempotency_dir"], f"idempotency_{idempotency_key}.json")
    with open(idemp_path, "w", encoding="utf-8") as f:
        json.dump({"idempotency_key": idempotency_key, "execution_id": execution_id}, f)

    return receipt


# ============================================================================
# TC-UI-REC-01: Hiển thị Clean Terminal
# ============================================================================
def test_ui_rec_01_clean_terminal(test_env, qapp):
    exec_id = "exec_rec_01"
    _create_test_receipt(test_env, exec_id, status="COMPLETED")

    authority = ResolveTimelineAuthority(
        ok=True,
        project_name="proj_001",
        timeline_id="tl_001",
        timeline_name="Main",
        timeline_fps=RationalFPS(30, 1),
    )

    worker = RecoveryAssessmentWorker(
        base_dir=test_env["base_dir"],
        execution_id=exec_id,
        authority_snapshot=authority,
    )
    result_holder = []
    worker.assessment_completed.connect(result_holder.append)

    worker.start()
    assert worker.wait(5000) is True
    QApplication.processEvents()
    assert len(result_holder) == 1

    assessment = result_holder[0]
    assert assessment.classified_state == "STATE_CLEAN_TERMINAL"

    dialog = RecoveryAssessmentDialog(assessment=assessment)
    dialog.show()
    QApplication.processEvents()

    try:
        assert dialog.lbl_status_badge.text() == "STATE_CLEAN_TERMINAL"
        # Bắt buộc lưu ý biên nhận là hồ sơ quá khứ, không cam kết timeline Resolve hiện tại
        explanation = dialog.lbl_explanation.text()
        assert "hồ sơ" in explanation.lower() or "biên nhận" in explanation.lower() or "quá khứ" in explanation.lower()
        # Bảng ứng viên phải rỗng
        assert dialog.table_compensations.rowCount() == 0
        # Không có nút thi công bù trừ
        assert dialog.btn_execute_compensation is None or not dialog.btn_execute_compensation.isEnabled()
    finally:
        dialog.close()
        QApplication.processEvents()


# ============================================================================
# TC-UI-REC-02: Hiển thị Active Process Locked
# ============================================================================
def test_ui_rec_02_active_process_locked(test_env, qapp):
    exec_id = "exec_rec_02"
    _create_test_receipt(test_env, exec_id, status="EXECUTING")

    lock_path = os.path.join(test_env["receipts_dir"], f"{exec_id}.lock")
    with open(lock_path, "w") as f:
        f.write("PID 54321")

    # Chiếm khóa cố vấn qua process hiện tại
    lock_fd = os.open(lock_path, os.O_RDWR)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    try:
        authority = ResolveTimelineAuthority(ok=True, project_name="proj_001", timeline_id="tl_001")
        worker = RecoveryAssessmentWorker(
            base_dir=test_env["base_dir"],
            execution_id=exec_id,
            authority_snapshot=authority,
        )
        result_holder = []
        worker.assessment_completed.connect(result_holder.append)

        worker.start()
        assert worker.wait(5000) is True
        QApplication.processEvents()
        assert len(result_holder) == 1

        assessment = result_holder[0]
        assert assessment.classified_state == "STATE_CLEAN_ACTIVE_LOCKED"
        assert assessment.is_advisory_lock_held is True

        dialog = RecoveryAssessmentDialog(assessment=assessment)
        dialog.show()
        QApplication.processEvents()

        try:
            assert dialog.lbl_status_badge.text() == "STATE_CLEAN_ACTIVE_LOCKED"
            # Cảnh báo tiến trình khác giữ khóa; KHÔNG tuyên bố tiến trình chạy bình thường
            explanation = dialog.lbl_explanation.text()
            assert "bình thường" not in explanation
            assert "khóa" in explanation.lower() or "lock" in explanation.lower()
            # Bảng ứng viên rỗng
            assert dialog.table_compensations.rowCount() == 0
            # Nút đóng khả dụng
            assert dialog.btn_close.isEnabled() is True
        finally:
            dialog.close()
            QApplication.processEvents()
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


# ============================================================================
# TC-UI-REC-03: Hiển thị Candidate Compensations (LIFO order, max 100 rows)
# ============================================================================
def test_ui_rec_03_candidate_compensations(test_env, qapp):
    exec_id = "exec_rec_03"
    _create_test_receipt(test_env, exec_id, status="EXECUTING")

    # Ghi 2 journal entries committed
    journal_path = os.path.join(test_env["journals_dir"], f"{exec_id}.journal.jsonl")
    with open(journal_path, "w", encoding="utf-8") as f:
        e0 = JournalEntry(0, "START", "NO_OP", "2026-11-02T12:00:00Z", {})
        e1 = JournalEntry(
            1,
            "ACTION_COMMITTED",
            "INSERT_CLIP",
            "2026-11-02T12:00:10Z",
            {"start": 0, "end": 100},
            resolve_item_id="item_01",
            track_type="video",
            track_index=1,
        )
        e2 = JournalEntry(
            2,
            "ACTION_COMMITTED",
            "INSERT_CLIP",
            "2026-11-02T12:00:20Z",
            {"start": 100, "end": 200},
            resolve_item_id="item_02",
            track_type="video",
            track_index=1,
        )
        f.write(journal_entry_to_json_line(e0))
        f.write(journal_entry_to_json_line(e1))
        f.write(journal_entry_to_json_line(e2))

    authority = ResolveTimelineAuthority(ok=True, project_name="proj_001", timeline_id="tl_001")
    worker = RecoveryAssessmentWorker(
        base_dir=test_env["base_dir"],
        execution_id=exec_id,
        authority_snapshot=authority,
    )
    result_holder = []
    worker.assessment_completed.connect(result_holder.append)

    worker.start()
    assert worker.wait(5000) is True
    QApplication.processEvents()
    assert len(result_holder) == 1

    assessment = result_holder[0]
    assert assessment.classified_state == "STATE_COMMITTED_CANDIDATE_COMPENSATION"
    assert len(assessment.candidate_compensations) == 2

    dialog = RecoveryAssessmentDialog(assessment=assessment)
    dialog.show()
    QApplication.processEvents()

    try:
        # Bảng phải hiển thị đúng 2 dòng LIFO (seq 2 trước, seq 1 sau)
        assert dialog.table_compensations.rowCount() == 2
        assert dialog.table_compensations.item(0, 0).text() == "2"
        assert dialog.table_compensations.item(0, 2).text() == "item_02"
        assert dialog.table_compensations.item(1, 0).text() == "1"
        assert dialog.table_compensations.item(1, 2).text() == "item_01"

        # Kiểm tra lưu ý quá khứ
        note = dialog.lbl_table_note.text()
        assert "quá khứ" in note.lower() or "không bảo đảm" in note.lower()

        # Thi công timeline bị khóa
        assert dialog.btn_execute_compensation is None or not dialog.btn_execute_compensation.isEnabled()
    finally:
        dialog.close()
        QApplication.processEvents()


# ============================================================================
# TC-UI-REC-04: Hiển thị In-Doubt Ambiguity
# ============================================================================
def test_ui_rec_04_in_doubt_ambiguity(test_env, qapp):
    exec_id = "exec_rec_04"
    _create_test_receipt(test_env, exec_id, status="EXECUTING")

    journal_path = os.path.join(test_env["journals_dir"], f"{exec_id}.journal.jsonl")
    with open(journal_path, "w", encoding="utf-8") as f:
        e0 = JournalEntry(0, "START", "NO_OP", "2026-11-02T12:00:00Z", {})
        e1 = JournalEntry(
            1,
            "ACTION_PENDING",
            "INSERT_CLIP",
            "2026-11-02T12:00:10Z",
            {"clip": "uncertain.mp4"},
            track_type="video",
            track_index=1,
        )
        f.write(journal_entry_to_json_line(e0))
        f.write(journal_entry_to_json_line(e1))

    authority = ResolveTimelineAuthority(ok=True, project_name="proj_001", timeline_id="tl_001")
    worker = RecoveryAssessmentWorker(
        base_dir=test_env["base_dir"],
        execution_id=exec_id,
        authority_snapshot=authority,
    )
    result_holder = []
    worker.assessment_completed.connect(result_holder.append)

    worker.start()
    assert worker.wait(5000) is True
    QApplication.processEvents()

    assessment = result_holder[0]
    assert assessment.classified_state == "STATE_AMBIGUOUS_IN_DOUBT"

    dialog = RecoveryAssessmentDialog(assessment=assessment)
    dialog.show()
    QApplication.processEvents()

    try:
        assert dialog.frame_in_doubt.isVisible() is True
        warning_txt = dialog.lbl_in_doubt_warning.text()
        assert "bất định" in warning_txt.lower() or "action_pending" in warning_txt.lower()
        assert "cấm" in warning_txt.lower()
    finally:
        dialog.close()
        QApplication.processEvents()


# ============================================================================
# TC-UI-REC-05: Hiển thị Torn-Tail Diagnostics (Safe preview <= 256 bytes)
# ============================================================================
def test_ui_rec_05_torn_tail_diagnostics(test_env, qapp):
    exec_id = "exec_rec_05"
    _create_test_receipt(test_env, exec_id, status="EXECUTING")

    journal_path = os.path.join(test_env["journals_dir"], f"{exec_id}.journal.jsonl")
    with open(journal_path, "w", encoding="utf-8") as f:
        e0 = JournalEntry(0, "START", "NO_OP", "2026-11-02T12:00:00Z", {})
        e1 = JournalEntry(
            1,
            "ACTION_COMMITTED",
            "INSERT_CLIP",
            "2026-11-02T12:00:10Z",
            {"start": 0},
            resolve_item_id="item_01",
            track_type="video",
            track_index=1,
        )
        f.write(journal_entry_to_json_line(e0))
        f.write(journal_entry_to_json_line(e1))
        # Dòng rách không có ký tự xuống dòng
        f.write('{"seq": 2, "action_type": "INSERT_CLIP", "par')

    authority = ResolveTimelineAuthority(ok=True, project_name="proj_001", timeline_id="tl_001")
    worker = RecoveryAssessmentWorker(
        base_dir=test_env["base_dir"],
        execution_id=exec_id,
        authority_snapshot=authority,
    )
    result_holder = []
    worker.assessment_completed.connect(result_holder.append)

    worker.start()
    assert worker.wait(5000) is True
    QApplication.processEvents()

    assessment = result_holder[0]
    assert assessment.classified_state == "STATE_TORN_TAIL_DETECTED"
    assert assessment.torn_tail_detected is True

    dialog = RecoveryAssessmentDialog(assessment=assessment)
    dialog.show()
    QApplication.processEvents()

    try:
        assert dialog.frame_torn_tail.isVisible() is True
        preview = dialog.lbl_torn_tail_preview.text()
        assert len(preview) <= 256
        assert "INSERT_CLIP" in preview
        assert assessment.torn_tail_sha256 in dialog.lbl_torn_tail_sha.text()
    finally:
        dialog.close()
        QApplication.processEvents()


# ============================================================================
# TC-UI-REC-06: Hiển thị Artifacts Corrupted (Forensic Quarantine)
# ============================================================================
def test_ui_rec_06_artifacts_corrupted_forensic_quarantine(test_env, qapp):
    exec_id = "exec_rec_06"
    receipt_path = os.path.join(test_env["receipts_dir"], f"{exec_id}.receipt.json")
    with open(receipt_path, "w") as f:
        f.write("{ INVALID JSON DATA CORRUPTED")

    authority = ResolveTimelineAuthority(ok=True, project_name="proj_001", timeline_id="tl_001")
    worker = RecoveryAssessmentWorker(
        base_dir=test_env["base_dir"],
        execution_id=exec_id,
        authority_snapshot=authority,
    )
    result_holder = []
    worker.assessment_completed.connect(result_holder.append)

    worker.start()
    assert worker.wait(5000) is True
    QApplication.processEvents()

    assessment = result_holder[0]
    assert assessment.classified_state == "STATE_ARTIFACT_CORRUPTED_OR_MISSING"

    dialog = RecoveryAssessmentDialog(assessment=assessment)
    dialog.show()
    QApplication.processEvents()

    try:
        assert dialog.lbl_status_badge.text() == "STATE_ARTIFACT_CORRUPTED_OR_MISSING"
        explanation = dialog.lbl_explanation.text()
        assert "forensic" in explanation.lower() or "biến dạng" in explanation.lower() or "cách ly" in explanation.lower()
    finally:
        dialog.close()
        QApplication.processEvents()


# ============================================================================
# TC-UI-REC-07: Cảnh báo Target Identity Drift
# ============================================================================
def test_ui_rec_07_target_identity_drift(test_env, qapp):
    exec_id = "exec_rec_07"
    _create_test_receipt(test_env, exec_id, project_id="proj_A", timeline_id="tl_A")

    # Mở timeline khác trong Resolve
    authority = ResolveTimelineAuthority(
        ok=True,
        project_name="proj_A",
        timeline_id="tl_B",  # Lệch timeline!
        timeline_name="Different_Timeline",
    )

    worker = RecoveryAssessmentWorker(
        base_dir=test_env["base_dir"],
        execution_id=exec_id,
        authority_snapshot=authority,
    )
    result_holder = []
    worker.assessment_completed.connect(result_holder.append)

    worker.start()
    assert worker.wait(5000) is True
    QApplication.processEvents()

    assessment = result_holder[0]
    assert assessment.target_match_report.drift_detected is True

    dialog = RecoveryAssessmentDialog(assessment=assessment)
    dialog.show()
    QApplication.processEvents()

    try:
        assert dialog.lbl_target_drift_warning.isVisible() is True
        drift_txt = dialog.lbl_target_drift_warning.text()
        assert "không khớp" in drift_txt.lower() or "trôi" in drift_txt.lower() or "drift" in drift_txt.lower()
    finally:
        dialog.close()
        QApplication.processEvents()


# ============================================================================
# TC-UI-REC-08: Resolve Authority Unavailable
# ============================================================================
def test_ui_rec_08_resolve_authority_unavailable(test_env, qapp):
    exec_id = "exec_rec_08"
    _create_test_receipt(test_env, exec_id, status="COMPLETED")

    # Snapshot báo Resolve chưa bật
    authority = ResolveTimelineAuthority(
        ok=False,
        error_code="ERR_RESOLVE_NOT_FOUND",
        error_message="DaVinci Resolve chưa khởi chạy",
    )

    worker = RecoveryAssessmentWorker(
        base_dir=test_env["base_dir"],
        execution_id=exec_id,
        authority_snapshot=authority,
    )
    result_holder = []
    worker.assessment_completed.connect(result_holder.append)

    worker.start()
    assert worker.wait(5000) is True
    QApplication.processEvents()

    assessment = result_holder[0]
    assert assessment.target_match_report.authority_available is False

    dialog = RecoveryAssessmentDialog(assessment=assessment)
    dialog.show()
    QApplication.processEvents()

    try:
        assert dialog.lbl_target_unavailable_warning.isVisible() is True
        unavail_txt = dialog.lbl_target_unavailable_warning.text()
        assert "chưa sẵn sàng" in unavail_txt.lower() or "không khả dụng" in unavail_txt.lower()
    finally:
        dialog.close()
        QApplication.processEvents()


# ============================================================================
# TC-UI-REC-09: Phòng vệ UI Injection (PlainText strict)
# ============================================================================
def test_ui_rec_09_anti_ui_injection_plain_text(test_env, qapp):
    report = TargetIdentityMatchReport(
        receipt_project_id="proj_01",
        receipt_timeline_id="tl_01",
        active_project_id="proj_01",
        active_timeline_id="tl_01",
        is_project_matched=True,
        is_timeline_matched=True,
        drift_detected=False,
        authority_available=True,
    )
    malicious_str = "<script>alert('xss')</script>\x1b[31mBoldRed\x1b[0m"
    action = CandidateCompensatingAction(
        seq=1,
        action_type="INSERT_CLIP",
        resolve_item_id="item_xss",
        track_type="video",
        track_index=1,
        clip_name="malicious.mp4",
        action_params_summary=malicious_str,
    )
    assessment = RecoveryAssessment(
        execution_id="exec_xss",
        idempotency_key="e" * 64,
        classified_state="STATE_COMMITTED_CANDIDATE_COMPENSATION",
        recommended_action="CANDIDATE_COMPENSATION_FOR_OPERATOR_REVIEW",
        requires_operator_confirmation=False,
        explanation_for_operator=malicious_str,
        is_advisory_lock_held=False,
        diagnostic_recorded_pid=123,
        total_journal_entries_count=1,
        candidate_compensations=(action,),
        ambiguous_actions=(),
        torn_tail_detected=False,
        torn_tail_line_number=None,
        torn_tail_byte_count=0,
        torn_tail_sha256=None,
        torn_tail_preview_escaped=None,
        target_match_report=report,
    )

    dialog = RecoveryAssessmentDialog(assessment=assessment)
    dialog.show()
    QApplication.processEvents()

    try:
        # Bắt buộc QLabel dùng PlainText format
        assert dialog.lbl_explanation.textFormat() == Qt.TextFormat.PlainText
        assert "<script>" in dialog.lbl_explanation.text()
        # Ô bảng hiển thị literal
        item_text = dialog.table_compensations.item(0, 5).text()
        assert "<script>" in item_text
    finally:
        dialog.close()
        QApplication.processEvents()


# ============================================================================
# TC-UI-REC-10: Giới hạn trần bảng (Cap at 100 rows)
# ============================================================================
def test_ui_rec_10_table_max_rows_cap(test_env, qapp):
    report = TargetIdentityMatchReport(
        receipt_project_id="proj_01",
        receipt_timeline_id="tl_01",
        active_project_id="proj_01",
        active_timeline_id="tl_01",
        is_project_matched=True,
        is_timeline_matched=True,
        drift_detected=False,
        authority_available=True,
    )
    actions = tuple(
        CandidateCompensatingAction(
            seq=i,
            action_type="INSERT_CLIP",
            resolve_item_id=f"item_{i}",
            track_type="video",
            track_index=1,
            clip_name=f"clip_{i}.mp4",
            action_params_summary=f"frame={i}",
        )
        for i in range(120)
    )
    assessment = RecoveryAssessment(
        execution_id="exec_cap",
        idempotency_key="f" * 64,
        classified_state="STATE_COMMITTED_CANDIDATE_COMPENSATION",
        recommended_action="CANDIDATE_COMPENSATION_FOR_OPERATOR_REVIEW",
        requires_operator_confirmation=False,
        explanation_for_operator="120 items test",
        is_advisory_lock_held=False,
        diagnostic_recorded_pid=123,
        total_journal_entries_count=120,
        candidate_compensations=actions,
        ambiguous_actions=(),
        torn_tail_detected=False,
        torn_tail_line_number=None,
        torn_tail_byte_count=0,
        torn_tail_sha256=None,
        torn_tail_preview_escaped=None,
        target_match_report=report,
    )

    dialog = RecoveryAssessmentDialog(assessment=assessment)
    dialog.show()
    QApplication.processEvents()

    try:
        # Giới hạn trần đúng 100 dòng
        assert dialog.table_compensations.rowCount() == 100
        count_lbl = dialog.lbl_compensations_count.text()
        assert "100" in count_lbl and "120" in count_lbl
    finally:
        dialog.close()
        QApplication.processEvents()


# ============================================================================
# TC-UI-REC-11: Lỗi truy cập I/O hệ điều hành (EACCES/EIO distinct from corruption)
# ============================================================================
def test_ui_rec_11_os_io_error_assessment_unavailable(test_env, qapp):
    exec_id = "exec_rec_11"
    _create_test_receipt(test_env, exec_id, status="EXECUTING")

    # Giả lập lỗi PermissionError khi mở tệp receipt
    receipt_path = os.path.join(test_env["receipts_dir"], f"{exec_id}.receipt.json")

    worker = RecoveryAssessmentWorker(
        base_dir=test_env["base_dir"],
        execution_id=exec_id,
        authority_snapshot=None,
    )
    io_errors: List[str] = []
    worker.assessment_io_error.connect(io_errors.append)

    with patch("builtins.open", side_effect=PermissionError(errno.EACCES, "Permission denied")):
        worker.start()
        assert worker.wait(5000) is True
        QApplication.processEvents()

    assert len(io_errors) == 1
    assert "EACCES" in io_errors[0] or "Permission" in io_errors[0]

    # Kiểm tra hiển thị trên dialog
    dialog = RecoveryAssessmentDialog(assessment=None, io_error=io_errors[0])
    dialog.show()
    QApplication.processEvents()

    try:
        assert dialog.frame_io_error.isVisible() is True
        # Không được gắn nhãn forensic quarantine
        assert "STATE_ARTIFACT_CORRUPTED_OR_MISSING" not in dialog.lbl_status_badge.text()
        assert "ASSESSMENT_UNAVAILABLE" in dialog.lbl_status_badge.text()
        msg = dialog.lbl_io_error_message.text()
        assert "EACCES" in msg or "quyền truy cập" in msg.lower()
        # Thi công bị khóa
        assert dialog.btn_execute_compensation is None or not dialog.btn_execute_compensation.isEnabled()
    finally:
        dialog.close()
        QApplication.processEvents()


# ============================================================================
# TC-UI-REC-12: Ranh giới đơn luồng Resolve & Khóa không chiếm giữ
# ============================================================================
def test_ui_rec_12_single_thread_affinity_and_lock_non_retention(test_env, qapp):
    import ast
    import src.ui.director.workers.recovery_assessment_worker as worker_mod

    # 1. Kiểm tra mã nguồn worker: KHÔNG ĐƯỢC import hoặc gọi fusionscript / get_resolve / query_active_timeline_authority
    with open(worker_mod.__file__, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    imported_names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_names.append(node.module)
            for alias in node.names:
                imported_names.append(alias.name)
    assert "fusionscript" not in imported_names
    assert "get_resolve" not in imported_names
    assert "query_active_timeline_authority" not in imported_names

    # 2. Khóa thăm dò không chiếm giữ (non-retaining lock probe)
    exec_id = "exec_rec_12"
    _create_test_receipt(test_env, exec_id, status="COMPLETED")
    lock_path = os.path.join(test_env["receipts_dir"], f"{exec_id}.lock")
    with open(lock_path, "w") as f:
        f.write("PID 12345")

    coordinator = RecoveryCoordinator(test_env["base_dir"])
    is_held, diag_pid = coordinator.probe_advisory_lock(exec_id)
    assert is_held is False

    # Khóa phải được nhả ngay lập tức; tiến trình khác có thể flock ngay sau đó mà không bị BlockingIOError
    test_fd = os.open(lock_path, os.O_RDWR)
    try:
        fcntl.flock(test_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(test_fd, fcntl.LOCK_UN)
    finally:
        os.close(test_fd)


# ============================================================================
# TC-UI-REC-13: Bẫy không đột biến (Zero-Mutation Tripwire)
# ============================================================================
def test_ui_rec_13_zero_mutation_tripwire(test_env, qapp):
    exec_id = "exec_rec_13"
    _create_test_receipt(test_env, exec_id, status="EXECUTING")

    journal_path = os.path.join(test_env["journals_dir"], f"{exec_id}.journal.jsonl")
    with open(journal_path, "w", encoding="utf-8") as f:
        e0 = JournalEntry(0, "START", "NO_OP", "2026-11-02T12:00:00Z", {})
        e1 = JournalEntry(
            1,
            "ACTION_COMMITTED",
            "INSERT_CLIP",
            "2026-11-02T12:00:10Z",
            {"start": 0, "end": 100},
            resolve_item_id="item_01",
            track_type="video",
            track_index=1,
        )
        f.write(journal_entry_to_json_line(e0))
        f.write(journal_entry_to_json_line(e1))

    lock_path = os.path.join(test_env["receipts_dir"], f"{exec_id}.lock")
    with open(lock_path, "w") as f:
        f.write("PID 9999")

    # Băm toàn bộ thư mục trước khi chạy
    before_hashes = _hash_directory_tree(test_env["base_dir"])

    authority = ResolveTimelineAuthority(ok=True, project_name="proj_001", timeline_id="tl_001")
    worker = RecoveryAssessmentWorker(
        base_dir=test_env["base_dir"],
        execution_id=exec_id,
        authority_snapshot=authority,
    )
    result_holder = []
    worker.assessment_completed.connect(result_holder.append)

    worker.start()
    assert worker.wait(5000) is True
    QApplication.processEvents()

    assessment = result_holder[0]
    dialog = RecoveryAssessmentDialog(assessment=assessment)
    dialog.show()
    QApplication.processEvents()

    # Bấm refresh trên dialog (nếu có callback)
    dialog.btn_refresh.click()
    QApplication.processEvents()
    dialog.close()
    QApplication.processEvents()

    # Băm toàn bộ thư mục sau khi chạy
    after_hashes = _hash_directory_tree(test_env["base_dir"])

    assert before_hashes == after_hashes, "Vi phạm Zero-Mutation: Tệp lưu trữ bị thay đổi bởi UI hoặc Worker!"


# ============================================================================
# TC-UI-REC-14: Tích hợp nút kiểm tra phiên cũ trên BuildPage
# ============================================================================
def test_ui_rec_14_build_page_inspection_button(test_env, qapp):
    page = BuildPage()
    page.show()
    QApplication.processEvents()

    try:
        # Nút kiểm tra phiên cũ phải tồn tại
        assert hasattr(page, "btn_inspect_recovery")
        assert page.btn_inspect_recovery.isVisible() is True
        assert "phiên thi công cũ" in page.btn_inspect_recovery.text().lower() or "đối soát" in page.btn_inspect_recovery.text().lower()

        # Kiểm tra launch_recovery_assessment method
        assert hasattr(page, "launch_recovery_assessment")
    finally:
        page.close()
        QApplication.processEvents()
