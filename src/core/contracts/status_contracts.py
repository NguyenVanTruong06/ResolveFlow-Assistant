"""
src/core/contracts/status_contracts.py
Hợp đồng trạng thái chuẩn hóa (MutationStatus & MutationEvidence) cho ResolveFlow.
100% Python thuần túy: Không nhập PySide6, DaVinci Resolve API hay I/O.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class MutationStatus(str, Enum):
    """
    Trạng thái kết quả đột biến Timeline / Project trong DaVinci Resolve.
    """
    APPLIED = "applied"
    SKIPPED = "skipped"
    FAILED = "failed"
    PARTIAL_APPLIED = "partial_applied"
    NO_OP_GUIDANCE = "no_op_guidance"
    UNKNOWN = "unknown"


def _validate_counter(val: object, name: str) -> None:
    """Xác thực bộ đếm: phải là None hoặc int >= 0 (cấm bool, float, str, âm...)."""
    if val is not None:
        if type(val) is bool or not isinstance(val, int) or val < 0:
            raise ValueError(
                f"Bộ đếm '{name}' phải là số nguyên int >= 0 hoặc None (nhận được kiểu {type(val).__name__})"
            )


def _validate_raw_status(val: object) -> None:
    """Xác thực raw_status: phải là None hoặc str."""
    if val is not None:
        if not isinstance(val, str):
            raise ValueError(
                f"Trường 'raw_status' phải là chuỗi hoặc None (nhận được kiểu {type(val).__name__})"
            )


@dataclass(frozen=True)
class MutationEvidence:
    """
    Bằng chứng định lượng về đột biến Timeline.
    Frozen dataclass bất biến, bảo toàn số nguyên lớn mà không bị ép kiểu sang float.
    """
    applied_count: Optional[int] = None
    skipped_count: Optional[int] = None
    failed_count: Optional[int] = None
    raw_status: Optional[str] = None

    def __post_init__(self) -> None:
        _validate_counter(self.applied_count, "applied_count")
        _validate_counter(self.skipped_count, "skipped_count")
        _validate_counter(self.failed_count, "failed_count")
        _validate_raw_status(self.raw_status)


def _extract_counter(mapping: Mapping[str, object], key: str) -> Optional[int]:
    """Trích xuất và kiểm tra bộ đếm từ Mapping nếu có mặt."""
    if key not in mapping:
        return None
    val = mapping[key]
    if val is None:
        return None
    if type(val) is bool or not isinstance(val, int) or val < 0:
        raise ValueError(
            f"Bộ đếm '{key}' phải là số nguyên int >= 0 hoặc None (nhận được kiểu {type(val).__name__})"
        )
    return val


def _extract_raw_status(mapping: Mapping[str, object]) -> Optional[str]:
    """Trích xuất và kiểm tra trường status từ Mapping nếu có mặt."""
    if "status" not in mapping:
        return None
    val = mapping["status"]
    if val is None:
        return None
    if not isinstance(val, str):
        raise ValueError(
            f"Trường 'status' phải là chuỗi hoặc None (nhận được kiểu {type(val).__name__})"
        )
    return val


def lut_result_to_mutation_outcome(
    res_dict: Mapping[str, object]
) -> Tuple[MutationStatus, MutationEvidence]:
    """
    Ánh xạ an toàn từ từ điển kết quả của ResolveAutomation.apply_lut_to_current_clip
    sang canonical MutationStatus và MutationEvidence.

    Quy tắc:
    - Yêu cầu đối tượng Mapping (MappingProxyType, dict...).
    - Validate toàn bộ các bộ đếm hiện diện trước khi phân loại.
    - Thiếu bất kỳ bộ đếm nào trong (applied, skipped, failed) -> UNKNOWN.
    - Khi đủ cả 3 bộ đếm, áp dụng chính xác thứ tự ưu tiên:
      1. applied > 0 và failed > 0 -> PARTIAL_APPLIED
      2. applied > 0 và failed == 0 -> APPLIED (kể cả khi skipped > 0)
      3. applied == 0 và failed > 0 -> FAILED (kể cả khi skipped > 0, skipped không che failed)
      4. applied == 0 và failed == 0 và skipped > 0 -> SKIPPED
      5. applied == 0 và failed == 0 và skipped == 0 -> UNKNOWN
    - Không suy đoán NO_OP_GUIDANCE hay dựa vào text message / cờ ok / cờ cancel.
    - Không làm biến đổi (mutate) hay deepcopy dữ liệu nguồn.
    """
    if not isinstance(res_dict, Mapping):
        raise ValueError(f"res_dict phải là đối tượng Mapping (nhận được kiểu {type(res_dict).__name__})")

    # 1. Trích xuất và validate toàn bộ các trường hiện diện
    app = _extract_counter(res_dict, "applied")
    sk = _extract_counter(res_dict, "skipped")
    fa = _extract_counter(res_dict, "failed")
    raw_status = _extract_raw_status(res_dict)

    ev = MutationEvidence(
        applied_count=app,
        skipped_count=sk,
        failed_count=fa,
        raw_status=raw_status,
    )

    # 2. Nếu thiếu bất kỳ bộ đếm nào -> UNKNOWN
    if app is None or sk is None or fa is None:
        return MutationStatus.UNKNOWN, ev

    # 3. Phân loại theo precedence khi đủ 3 bộ đếm
    if app > 0 and fa > 0:
        return MutationStatus.PARTIAL_APPLIED, ev
    elif app > 0 and fa == 0:
        return MutationStatus.APPLIED, ev
    elif app == 0 and fa > 0:
        return MutationStatus.FAILED, ev
    elif app == 0 and fa == 0 and sk > 0:
        return MutationStatus.SKIPPED, ev
    else:  # app == 0 and fa == 0 and sk == 0
        return MutationStatus.UNKNOWN, ev
