"""Dịch vụ Phân loại Phục hồi Chỉ Đọc Tất định (G07-07A7-I).

Module pure Python chuẩn stdlib:
- Triển khai RecoveryClassifierService: Thuật toán tất định thuần túy phân loại 8 trạng thái sự cố từ
  ExecutionReceipt, JournalReplayResult, kết quả thăm dò OS advisory lock và tệp chỉ mục idempotency.
- Trích xuất danh sách ứng viên bù trừ LIFO (CandidateCompensatingAction).
- Trích xuất hành động bất định (AmbiguousActionDetail) khi sập nguồn sau ACTION_PENDING.
- Làm sạch và giới hạn trần toàn bộ tham số chẩn đoán, loại bỏ nguy cơ UI injection và tràn bộ nhớ.

Tuân thủ nghiêm ngặt:
- Zero IO, zero network, zero Qt/UI, zero Resolve scripting API.
- Bất biến 100%, bảo đảm không gây đột biến lên bất kỳ đối tượng đầu vào nào.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from src.core.contracts.execution_receipt_contracts import (
    ExecutionReceipt,
    JournalEntry,
    JournalReplayResult,
)
from src.core.contracts.recovery_assessment_contracts import (
    AmbiguousActionDetail,
    CandidateCompensatingAction,
    CrashStateEnum,
    RecoveryActionType,
    RecoveryAssessment,
    TargetIdentityMatchReport,
    sanitize_diagnostics_string,
    MAX_PARAMS_SUMMARY_CHARS,
    MAX_TORN_TAIL_PREVIEW_BYTES,
)


def _format_params_summary(params: Dict[str, Any]) -> str:
    """Tạo chuỗi tóm tắt tham số ngắn gọn, có giới hạn và đã làm sạch."""
    if not isinstance(params, dict) or not params:
        return ""
    parts = []
    for k, v in sorted(params.items(), key=lambda x: str(x[0])):
        val_str = str(v)
        if len(val_str) > 64:
            val_str = val_str[:61] + "..."
        parts.append(f"{k}={val_str}")
    raw_summary = ", ".join(parts)
    return sanitize_diagnostics_string(raw_summary, max_len=MAX_PARAMS_SUMMARY_CHARS)


class RecoveryClassifierService:
    """Dịch vụ phân loại phục hồi chỉ đọc tất định."""

    @classmethod
    def classify(
        cls,
        receipt: Optional[ExecutionReceipt],
        journal_result: Optional[JournalReplayResult],
        is_advisory_lock_held: bool,
        diagnostic_recorded_pid: Optional[int] = None,
        active_project_id: Optional[str] = None,
        active_timeline_id: Optional[str] = None,
        is_idempotency_index_valid: bool = True,
        receipt_corrupted_or_missing: bool = False,
    ) -> RecoveryAssessment:
        """Phân loại trạng thái sự cố và sinh ra bản đánh giá phục hồi bất biến."""

        # 1. Báo cáo đối chiếu danh tính Project và Timeline thực tế
        receipt_proj_id = receipt.project_id if receipt else ""
        receipt_tl_id = receipt.timeline_id if receipt else ""
        authority_available = bool(active_project_id and active_timeline_id)

        is_proj_matched = bool(receipt_proj_id and active_project_id and receipt_proj_id == active_project_id)
        is_tl_matched = bool(receipt_tl_id and active_timeline_id and receipt_tl_id == active_timeline_id)

        drift_detected = False
        if authority_available and receipt:
            if not is_proj_matched or not is_tl_matched:
                drift_detected = True

        target_report = TargetIdentityMatchReport(
            receipt_project_id=receipt_proj_id,
            receipt_timeline_id=receipt_tl_id,
            active_project_id=active_project_id,
            active_timeline_id=active_timeline_id,
            is_project_matched=is_proj_matched,
            is_timeline_matched=is_tl_matched,
            drift_detected=drift_detected,
            authority_available=authority_available,
        )

        exec_id = receipt.execution_id if receipt else "UNKNOWN_EXECUTION"
        idemp_key = receipt.idempotency_key if receipt else "UNKNOWN_IDEMPOTENCY_KEY"

        # 2. Xử lý trường hợp hồ sơ lưu trữ bị biến dạng hoặc chỉ mục idempotency không hợp lệ (Fail-Closed)
        if receipt_corrupted_or_missing or receipt is None or not is_idempotency_index_valid:
            reason = "Receipt artifact corrupted or missing."
            if not is_idempotency_index_valid:
                reason = "Idempotency index artifact corrupted or missing for existing execution."

            return RecoveryAssessment(
                execution_id=exec_id,
                idempotency_key=idemp_key,
                classified_state="STATE_ARTIFACT_CORRUPTED_OR_MISSING",
                recommended_action="FORENSIC_QUARANTINE_REQUIRED",
                requires_operator_confirmation=True,
                explanation_for_operator=f"Fail-closed: {reason} Replay forbidden. Preserving disk artifacts for forensic investigation.",
                is_advisory_lock_held=is_advisory_lock_held,
                diagnostic_recorded_pid=diagnostic_recorded_pid,
                total_journal_entries_count=len(journal_result.entries) if journal_result else 0,
                candidate_compensations=(),
                ambiguous_actions=(),
                torn_tail_detected=False,
                torn_tail_line_number=None,
                torn_tail_byte_count=0,
                torn_tail_sha256=None,
                torn_tail_preview_escaped=None,
                target_match_report=target_report,
            )

        # 3. Xử lý trường hợp khóa cố vấn hệ điều hành đang bị chiếm giữ (Concurrent execution)
        if is_advisory_lock_held:
            return RecoveryAssessment(
                execution_id=exec_id,
                idempotency_key=idemp_key,
                classified_state="STATE_CLEAN_ACTIVE_LOCKED",
                recommended_action="CONCURRENT_JOB_RUNNING_DO_NOT_DISTURB",
                requires_operator_confirmation=False,
                explanation_for_operator=f"OS advisory lock actively held by concurrent execution process (diagnostic pid: {diagnostic_recorded_pid}). Do not disturb.",
                is_advisory_lock_held=True,
                diagnostic_recorded_pid=diagnostic_recorded_pid,
                total_journal_entries_count=len(journal_result.entries) if journal_result else 0,
                candidate_compensations=(),
                ambiguous_actions=(),
                torn_tail_detected=False,
                torn_tail_line_number=None,
                torn_tail_byte_count=0,
                torn_tail_sha256=None,
                torn_tail_preview_escaped=None,
                target_match_report=target_report,
            )

        # 4. Trích xuất thông tin dòng rách nếu có
        torn_tail_detected = False
        torn_tail_line_no: Optional[int] = None
        torn_tail_byte_cnt: int = 0
        torn_tail_sha: Optional[str] = None
        torn_tail_preview: Optional[str] = None

        if journal_result and journal_result.torn_tail and journal_result.torn_tail.is_torn:
            torn_tail_detected = True
            torn_tail_line_no = journal_result.torn_tail.line_number
            raw_b = journal_result.torn_tail.raw_bytes
            torn_tail_byte_cnt = len(raw_b)
            torn_tail_sha = hashlib.sha256(raw_b).hexdigest()
            # Làm sạch chuỗi xem trước an toàn
            preview_str = repr(raw_b[:MAX_TORN_TAIL_PREVIEW_BYTES])
            torn_tail_preview = sanitize_diagnostics_string(preview_str, max_len=MAX_TORN_TAIL_PREVIEW_BYTES)

        # 5. Phân tích các bản ghi trong nhật ký
        entries = journal_result.entries if journal_result else ()
        total_entries = len(entries)

        # Trích xuất ứng viên bù trừ (những hành động COMMITTED chưa có ROLLBACK_DONE tương ứng)
        rolled_back_seqs = {
            e.seq for e in entries if e.stage == "ROLLBACK_DONE"
        }
        rolled_back_items = {
            e.resolve_item_id for e in entries if e.stage == "ROLLBACK_DONE" and e.resolve_item_id
        }

        candidate_list: List[CandidateCompensatingAction] = []
        for e in entries:
            if e.stage == "ACTION_COMMITTED" and e.resolve_item_id:
                if e.seq in rolled_back_seqs or e.resolve_item_id in rolled_back_items:
                    continue
                clip_n = str(e.action_params.get("clip") or e.action_params.get("clip_name") or "") or None
                cand = CandidateCompensatingAction(
                    seq=e.seq,
                    action_type=e.action_type,
                    resolve_item_id=e.resolve_item_id,
                    track_type=e.track_type or "video",
                    track_index=e.track_index if e.track_index > 0 else 1,
                    clip_name=clip_n,
                    action_params_summary=_format_params_summary(e.action_params),
                )
                candidate_list.append(cand)

        # Sắp xếp LIFO (hành động commit sau hoàn tác trước)
        candidate_list.reverse()
        candidate_compensations = tuple(candidate_list)

        # 6. Kiểm tra trạng thái Receipt
        if receipt.state in ("COMPLETED", "ROLLED_BACK", "ABORTED"):
            return RecoveryAssessment(
                execution_id=exec_id,
                idempotency_key=idemp_key,
                classified_state="STATE_CLEAN_TERMINAL",
                recommended_action="NO_ACTION_NEEDED",
                requires_operator_confirmation=False,
                explanation_for_operator=f"Execution session ended cleanly in state {receipt.state}.",
                is_advisory_lock_held=False,
                diagnostic_recorded_pid=diagnostic_recorded_pid,
                total_journal_entries_count=total_entries,
                candidate_compensations=(),
                ambiguous_actions=(),
                torn_tail_detected=torn_tail_detected,
                torn_tail_line_number=torn_tail_line_no,
                torn_tail_byte_count=torn_tail_byte_cnt,
                torn_tail_sha256=torn_tail_sha,
                torn_tail_preview_escaped=torn_tail_preview,
                target_match_report=target_report,
            )

        if receipt.state == "INITIALIZED" and total_entries == 0 and not torn_tail_detected:
            return RecoveryAssessment(
                execution_id=exec_id,
                idempotency_key=idemp_key,
                classified_state="STATE_INITIALIZED_NO_JOURNAL",
                recommended_action="SAFE_TO_ABORT_AFTER_CONFIRMATION",
                requires_operator_confirmation=True,
                explanation_for_operator="Session initialized but no journal entries or Resolve mutations were made. Safe to mark aborted.",
                is_advisory_lock_held=False,
                diagnostic_recorded_pid=diagnostic_recorded_pid,
                total_journal_entries_count=0,
                candidate_compensations=(),
                ambiguous_actions=(),
                torn_tail_detected=False,
                torn_tail_line_number=None,
                torn_tail_byte_count=0,
                torn_tail_sha256=None,
                torn_tail_preview_escaped=None,
                target_match_report=target_report,
            )

        # 7. Xử lý khi có dòng rách (Torn-tail observed anomaly)
        if torn_tail_detected:
            return RecoveryAssessment(
                execution_id=exec_id,
                idempotency_key=idemp_key,
                classified_state="STATE_TORN_TAIL_DETECTED",
                recommended_action="TORN_TAIL_OBSERVED_OPERATOR_REVIEW",
                requires_operator_confirmation=True,
                explanation_for_operator=f"Observed incomplete trailing record at line {torn_tail_line_no} ({torn_tail_byte_cnt} bytes). Valid preceding committed entries are offered as candidate compensations for operator review.",
                is_advisory_lock_held=False,
                diagnostic_recorded_pid=diagnostic_recorded_pid,
                total_journal_entries_count=total_entries,
                candidate_compensations=candidate_compensations,
                ambiguous_actions=(),
                torn_tail_detected=True,
                torn_tail_line_number=torn_tail_line_no,
                torn_tail_byte_count=torn_tail_byte_cnt,
                torn_tail_sha256=torn_tail_sha,
                torn_tail_preview_escaped=torn_tail_preview,
                target_match_report=target_report,
            )

        # 8. Kiểm tra bản ghi cuối cùng của nhật ký
        last_entry = entries[-1] if entries else None

        if last_entry and last_entry.stage == "ACTION_PENDING":
            amb = AmbiguousActionDetail(
                seq=last_entry.seq,
                action_type=last_entry.action_type,
                track_type=last_entry.track_type or "video",
                track_index=last_entry.track_index if last_entry.track_index > 0 else 1,
                action_params_summary=_format_params_summary(last_entry.action_params),
                uncertainty_reason=f"Unconfirmed ACTION_PENDING before process exit for seq {last_entry.seq}.",
            )
            return RecoveryAssessment(
                execution_id=exec_id,
                idempotency_key=idemp_key,
                classified_state="STATE_AMBIGUOUS_IN_DOUBT",
                recommended_action="MANUAL_TIMELINE_INSPECTION_REQUIRED",
                requires_operator_confirmation=True,
                explanation_for_operator=f"Ambiguous boundary: Journal ends with ACTION_PENDING seq {last_entry.seq}. Replay and blind deletion strictly forbidden. Operator visual inspection on DaVinci Resolve timeline required.",
                is_advisory_lock_held=False,
                diagnostic_recorded_pid=diagnostic_recorded_pid,
                total_journal_entries_count=total_entries,
                candidate_compensations=candidate_compensations,
                ambiguous_actions=(amb,),
                torn_tail_detected=False,
                torn_tail_line_number=None,
                torn_tail_byte_count=0,
                torn_tail_sha256=None,
                torn_tail_preview_escaped=None,
                target_match_report=target_report,
            )

        if last_entry and last_entry.stage in ("FINISH", "ROLLBACK_DONE"):
            return RecoveryAssessment(
                execution_id=exec_id,
                idempotency_key=idemp_key,
                classified_state="STATE_CLEAN_TERMINAL",
                recommended_action="NO_ACTION_NEEDED",
                requires_operator_confirmation=False,
                explanation_for_operator=f"Journal concluded cleanly with stage {last_entry.stage}.",
                is_advisory_lock_held=False,
                diagnostic_recorded_pid=diagnostic_recorded_pid,
                total_journal_entries_count=total_entries,
                candidate_compensations=(),
                ambiguous_actions=(),
                torn_tail_detected=False,
                torn_tail_line_number=None,
                torn_tail_byte_count=0,
                torn_tail_sha256=None,
                torn_tail_preview_escaped=None,
                target_match_report=target_report,
            )

        # 9. Trạng thái còn lại: Đã commit một phần hoặc có lỗi có kiểm soát
        return RecoveryAssessment(
            execution_id=exec_id,
            idempotency_key=idemp_key,
            classified_state="STATE_COMMITTED_CANDIDATE_COMPENSATION",
            recommended_action="CANDIDATE_COMPENSATION_FOR_OPERATOR_REVIEW",
            requires_operator_confirmation=True,
            explanation_for_operator=f"Application recorded {len(candidate_compensations)} committed mutations before process stop. These are candidate compensations for operator review; current Resolve timeline state must be visually confirmed.",
            is_advisory_lock_held=False,
            diagnostic_recorded_pid=diagnostic_recorded_pid,
            total_journal_entries_count=total_entries,
            candidate_compensations=candidate_compensations,
            ambiguous_actions=(),
            torn_tail_detected=False,
            torn_tail_line_number=None,
            torn_tail_byte_count=0,
            torn_tail_sha256=None,
            torn_tail_preview_escaped=None,
            target_match_report=target_report,
        )
