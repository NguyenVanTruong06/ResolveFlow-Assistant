"""Hợp đồng DTO Bất biến Đánh giá & Phân loại Phục hồi Chỉ Đọc (G07-07A7-I).

Module pure Python chuẩn stdlib, định nghĩa:
- RecoveryAssessment: Frozen DTO đánh giá toàn diện trạng thái sự cố và khuyến nghị phục hồi chỉ đọc.
- CandidateCompensatingAction: Frozen DTO chi tiết hành động ứng viên bù trừ đã commit.
- AmbiguousActionDetail: Frozen DTO chi tiết hành động rơi vào vùng bất định (ACTION_PENDING mồ côi).
- TargetIdentityMatchReport: Frozen DTO đối chiếu danh tính project và timeline DaVinci Resolve.
- Các enum và hằng số trần an toàn: MAX_TORN_TAIL_PREVIEW_BYTES (256), MAX_PARAMS_SUMMARY_CHARS (512).
- sanitize_diagnostics_string: Hàm làm sạch chuỗi chẩn đoán phòng vệ UI injection và tràn bộ nhớ.

Tuân thủ nghiêm ngặt:
- Zero IO, zero network, zero Qt/UI, zero Resolve scripting API.
- Bất biến & sở hữu độc lập (frozen dataclass, tuple, zero mutable aliases).
- Giới hạn trần dung lượng và làm sạch an toàn dữ liệu chẩn đoán.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, Optional, Tuple, Literal

MAX_TORN_TAIL_PREVIEW_BYTES: int = 256
MAX_PARAMS_SUMMARY_CHARS: int = 512
MAX_UNCERTAINTY_REASON_CHARS: int = 256
MAX_EXPLANATION_CHARS: int = 2048

RE_HEX_64: re.Pattern = re.compile(r"^[0-9a-f]{64}$")
RE_ANSI_ESCAPE: re.Pattern = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

CrashStateEnum = Literal[
    "STATE_CLEAN_TERMINAL",
    "STATE_INITIALIZED_NO_JOURNAL",
    "STATE_CLEAN_ACTIVE_LOCKED",
    "STATE_COMMITTED_CANDIDATE_COMPENSATION",
    "STATE_TORN_TAIL_DETECTED",
    "STATE_AMBIGUOUS_IN_DOUBT",
    "STATE_IDEMPOTENCY_DUPLICATE_BLOCKED",
    "STATE_ARTIFACT_CORRUPTED_OR_MISSING",
]

RecoveryActionType = Literal[
    "NO_ACTION_NEEDED",
    "SAFE_TO_ABORT_AFTER_CONFIRMATION",
    "CONCURRENT_JOB_RUNNING_DO_NOT_DISTURB",
    "CANDIDATE_COMPENSATION_FOR_OPERATOR_REVIEW",
    "TORN_TAIL_OBSERVED_OPERATOR_REVIEW",
    "MANUAL_TIMELINE_INSPECTION_REQUIRED",
    "REJECT_DUPLICATE",
    "FORENSIC_QUARANTINE_REQUIRED",
]

VALID_CRASH_STATES: Tuple[str, ...] = (
    "STATE_CLEAN_TERMINAL",
    "STATE_INITIALIZED_NO_JOURNAL",
    "STATE_CLEAN_ACTIVE_LOCKED",
    "STATE_COMMITTED_CANDIDATE_COMPENSATION",
    "STATE_TORN_TAIL_DETECTED",
    "STATE_AMBIGUOUS_IN_DOUBT",
    "STATE_IDEMPOTENCY_DUPLICATE_BLOCKED",
    "STATE_ARTIFACT_CORRUPTED_OR_MISSING",
)

VALID_RECOVERY_ACTIONS: Tuple[str, ...] = (
    "NO_ACTION_NEEDED",
    "SAFE_TO_ABORT_AFTER_CONFIRMATION",
    "CONCURRENT_JOB_RUNNING_DO_NOT_DISTURB",
    "CANDIDATE_COMPENSATION_FOR_OPERATOR_REVIEW",
    "TORN_TAIL_OBSERVED_OPERATOR_REVIEW",
    "MANUAL_TIMELINE_INSPECTION_REQUIRED",
    "REJECT_DUPLICATE",
    "FORENSIC_QUARANTINE_REQUIRED",
)


def sanitize_diagnostics_string(val: Any, max_len: int = 512) -> str:
    """Làm sạch chuỗi chẩn đoán: loại bỏ ký tự điều khiển, thoát escape, giới hạn độ dài."""
    if val is None:
        return ""
    if not isinstance(val, str):
        val = str(val)

    # Loại bỏ ANSI escape sequences
    val = RE_ANSI_ESCAPE.sub("", val)

    # Loại bỏ ký tự NUL
    val = val.replace("\x00", "")

    # Thoát các ký tự điều khiển newline, carriage return, tab
    val = val.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")

    # Loại bỏ các ký tự unprintable khác (ASCII < 32 hoặc 127)
    val = "".join(ch if (ord(ch) >= 32 and ord(ch) != 127) or ord(ch) > 127 else f"\\x{ord(ch):02x}" for ch in val)

    if len(val) > max_len:
        val = val[:max_len]
    return val


def _validate_str(val: Any, field_name: str, allow_blank: bool = False, max_len: int = 4096) -> str:
    if type(val) is not str:
        raise ValueError(f"ERR_INVALID_TYPE: {field_name} phải là str, nhận: {type(val).__name__}")
    if "\0" in val:
        raise ValueError(f"ERR_INVALID_STRING: {field_name} chứa ký tự NUL")
    if len(val) > max_len:
        raise ValueError(f"ERR_STRING_TOO_LONG: {field_name} vượt quá giới hạn {max_len} ký tự")
    if not allow_blank and not val.strip():
        raise ValueError(f"ERR_BLANK_FIELD: {field_name} không được để trống")
    return val


def _validate_int(val: Any, field_name: str, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
    if type(val) is not int or type(val) is bool:
        raise ValueError(f"ERR_INVALID_TYPE: {field_name} phải là int (không chấp nhận bool), nhận: {type(val).__name__}")
    if min_val is not None and val < min_val:
        raise ValueError(f"ERR_INT_OUT_OF_RANGE: {field_name} ({val}) < giá trị tối thiểu {min_val}")
    if max_val is not None and val > max_val:
        raise ValueError(f"ERR_INT_OUT_OF_RANGE: {field_name} ({val}) > giá trị tối đa {max_val}")
    return val


@dataclass(frozen=True)
class CandidateCompensatingAction:
    """Chi tiết một hành động ứng viên được ứng dụng ghi nhận COMMITTED có thể xem xét bù trừ."""
    seq: int
    action_type: str
    resolve_item_id: str
    track_type: str
    track_index: int
    clip_name: Optional[str] = None
    action_params_summary: str = ""

    def __post_init__(self):
        _validate_int(self.seq, "seq", min_val=0)
        _validate_str(self.action_type, "action_type")
        _validate_str(self.resolve_item_id, "resolve_item_id")
        tt = _validate_str(self.track_type, "track_type").lower()
        if tt not in ("video", "audio"):
            raise ValueError(f"ERR_INVALID_TRACK_TYPE: track_type phải là 'video' hoặc 'audio': {tt!r}")
        object.__setattr__(self, "track_type", tt)
        _validate_int(self.track_index, "track_index", min_val=1)
        if self.clip_name is not None:
            _validate_str(self.clip_name, "clip_name", allow_blank=True, max_len=512)
        object.__setattr__(
            self,
            "action_params_summary",
            sanitize_diagnostics_string(self.action_params_summary, max_len=MAX_PARAMS_SUMMARY_CHARS),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "action_type": self.action_type,
            "resolve_item_id": self.resolve_item_id,
            "track_type": self.track_type,
            "track_index": self.track_index,
            "clip_name": self.clip_name,
            "action_params_summary": self.action_params_summary,
        }


@dataclass(frozen=True)
class AmbiguousActionDetail:
    """Chi tiết hành động rơi vào vùng bất định (ACTION_PENDING chưa có xác nhận kết quả)."""
    seq: int
    action_type: str
    track_type: str
    track_index: int
    action_params_summary: str = ""
    uncertainty_reason: str = ""

    def __post_init__(self):
        _validate_int(self.seq, "seq", min_val=0)
        _validate_str(self.action_type, "action_type")
        if self.track_type:
            tt = _validate_str(self.track_type, "track_type").lower()
            if tt not in ("video", "audio"):
                raise ValueError(f"ERR_INVALID_TRACK_TYPE: track_type phải là 'video' hoặc 'audio': {tt!r}")
            object.__setattr__(self, "track_type", tt)
        if self.track_index != 0:
            _validate_int(self.track_index, "track_index", min_val=1)
        object.__setattr__(
            self,
            "action_params_summary",
            sanitize_diagnostics_string(self.action_params_summary, max_len=MAX_PARAMS_SUMMARY_CHARS),
        )
        object.__setattr__(
            self,
            "uncertainty_reason",
            sanitize_diagnostics_string(self.uncertainty_reason, max_len=MAX_UNCERTAINTY_REASON_CHARS),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "action_type": self.action_type,
            "track_type": self.track_type,
            "track_index": self.track_index,
            "action_params_summary": self.action_params_summary,
            "uncertainty_reason": self.uncertainty_reason,
        }


@dataclass(frozen=True)
class TargetIdentityMatchReport:
    """Kết quả đối chiếu danh tính Project và Timeline giữa Receipt và DaVinci Resolve thực tế."""
    receipt_project_id: str
    receipt_timeline_id: str
    active_project_id: Optional[str]
    active_timeline_id: Optional[str]
    is_project_matched: bool
    is_timeline_matched: bool
    drift_detected: bool
    authority_available: bool

    def __post_init__(self):
        _validate_str(self.receipt_project_id, "receipt_project_id", allow_blank=True)
        _validate_str(self.receipt_timeline_id, "receipt_timeline_id", allow_blank=True)
        if self.active_project_id is not None:
            _validate_str(self.active_project_id, "active_project_id", allow_blank=True)
        if self.active_timeline_id is not None:
            _validate_str(self.active_timeline_id, "active_timeline_id", allow_blank=True)
        if not isinstance(self.is_project_matched, bool):
            raise ValueError("is_project_matched phải là bool")
        if not isinstance(self.is_timeline_matched, bool):
            raise ValueError("is_timeline_matched phải là bool")
        if not isinstance(self.drift_detected, bool):
            raise ValueError("drift_detected phải là bool")
        if not isinstance(self.authority_available, bool):
            raise ValueError("authority_available phải là bool")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "receipt_project_id": self.receipt_project_id,
            "receipt_timeline_id": self.receipt_timeline_id,
            "active_project_id": self.active_project_id,
            "active_timeline_id": self.active_timeline_id,
            "is_project_matched": self.is_project_matched,
            "is_timeline_matched": self.is_timeline_matched,
            "drift_detected": self.drift_detected,
            "authority_available": self.authority_available,
        }


@dataclass(frozen=True)
class RecoveryAssessment:
    """Hồ sơ đánh giá toàn diện tình trạng sự cố và khuyến nghị phục hồi (Read-Only Frozen DTO)."""
    execution_id: str
    idempotency_key: str
    classified_state: CrashStateEnum
    recommended_action: RecoveryActionType
    requires_operator_confirmation: bool
    explanation_for_operator: str

    # Thẩm quyền khóa cố vấn OS và siêu dữ liệu chẩn đoán PID
    is_advisory_lock_held: bool
    diagnostic_recorded_pid: Optional[int]

    # Thống kê nhật ký và ứng viên bù trừ
    total_journal_entries_count: int
    candidate_compensations: Tuple[CandidateCompensatingAction, ...]
    ambiguous_actions: Tuple[AmbiguousActionDetail, ...]

    # Chẩn đoán dòng rách quan sát được an toàn (không raw bytes)
    torn_tail_detected: bool
    torn_tail_line_number: Optional[int]
    torn_tail_byte_count: int
    torn_tail_sha256: Optional[str]
    torn_tail_preview_escaped: Optional[str]

    # Đối chiếu danh tính Resolve thực tế
    target_match_report: TargetIdentityMatchReport

    def __post_init__(self):
        _validate_str(self.execution_id, "execution_id")
        _validate_str(self.idempotency_key, "idempotency_key")
        if self.classified_state not in VALID_CRASH_STATES:
            raise ValueError(f"ERR_INVALID_CRASH_STATE: {self.classified_state!r} không nằm trong {VALID_CRASH_STATES}")
        if self.recommended_action not in VALID_RECOVERY_ACTIONS:
            raise ValueError(f"ERR_INVALID_RECOVERY_ACTION: {self.recommended_action!r} không nằm trong {VALID_RECOVERY_ACTIONS}")
        if not isinstance(self.requires_operator_confirmation, bool):
            raise ValueError("requires_operator_confirmation phải là bool")
        object.__setattr__(
            self,
            "explanation_for_operator",
            sanitize_diagnostics_string(self.explanation_for_operator, max_len=MAX_EXPLANATION_CHARS),
        )

        if not isinstance(self.is_advisory_lock_held, bool):
            raise ValueError("is_advisory_lock_held phải là bool")
        if self.diagnostic_recorded_pid is not None:
            _validate_int(self.diagnostic_recorded_pid, "diagnostic_recorded_pid", min_val=0)

        _validate_int(self.total_journal_entries_count, "total_journal_entries_count", min_val=0)
        if type(self.candidate_compensations) is not tuple:
            raise ValueError("candidate_compensations phải là tuple")
        if type(self.ambiguous_actions) is not tuple:
            raise ValueError("ambiguous_actions phải là tuple")

        if not isinstance(self.torn_tail_detected, bool):
            raise ValueError("torn_tail_detected phải là bool")
        if self.torn_tail_line_number is not None:
            _validate_int(self.torn_tail_line_number, "torn_tail_line_number", min_val=1)
        _validate_int(self.torn_tail_byte_count, "torn_tail_byte_count", min_val=0)
        if self.torn_tail_sha256 is not None:
            if not RE_HEX_64.match(self.torn_tail_sha256):
                raise ValueError("ERR_INVALID_SHA256: torn_tail_sha256 phải là chuỗi hex 64 ký tự")
        if self.torn_tail_preview_escaped is not None:
            object.__setattr__(
                self,
                "torn_tail_preview_escaped",
                sanitize_diagnostics_string(self.torn_tail_preview_escaped, max_len=MAX_TORN_TAIL_PREVIEW_BYTES),
            )
        if not isinstance(self.target_match_report, TargetIdentityMatchReport):
            raise ValueError("target_match_report phải là TargetIdentityMatchReport")


def assessment_to_dict(assessment: RecoveryAssessment) -> Dict[str, Any]:
    """Chuyển đổi RecoveryAssessment thành cấu trúc dict tuần tự hóa an toàn."""
    if not isinstance(assessment, RecoveryAssessment):
        raise ValueError("assessment phải là RecoveryAssessment")
    return {
        "execution_id": assessment.execution_id,
        "idempotency_key": assessment.idempotency_key,
        "classified_state": assessment.classified_state,
        "recommended_action": assessment.recommended_action,
        "requires_operator_confirmation": assessment.requires_operator_confirmation,
        "explanation_for_operator": assessment.explanation_for_operator,
        "is_advisory_lock_held": assessment.is_advisory_lock_held,
        "diagnostic_recorded_pid": assessment.diagnostic_recorded_pid,
        "total_journal_entries_count": assessment.total_journal_entries_count,
        "candidate_compensations": [a.to_dict() for a in assessment.candidate_compensations],
        "ambiguous_actions": [a.to_dict() for a in assessment.ambiguous_actions],
        "torn_tail_detected": assessment.torn_tail_detected,
        "torn_tail_line_number": assessment.torn_tail_line_number,
        "torn_tail_byte_count": assessment.torn_tail_byte_count,
        "torn_tail_sha256": assessment.torn_tail_sha256,
        "torn_tail_preview_escaped": assessment.torn_tail_preview_escaped,
        "target_match_report": assessment.target_match_report.to_dict(),
    }
