"""
tests/core/contracts/test_status_contracts.py
Bộ kiểm thử pure Python cho hợp đồng trạng thái đột biến (MutationStatus & MutationEvidence)
và hàm adapter lut_result_to_mutation_outcome.
"""

from types import MappingProxyType
import pytest

from src.core.contracts.status_contracts import (
    MutationStatus,
    MutationEvidence,
    lut_result_to_mutation_outcome,
)


# =========================================================================
# 1. KIỂM THỬ PRECEDENCE & CLASSIFICATION KHI ĐỦ 3 COUNTERS
# =========================================================================

def test_applied_only():
    """applied > 0, failed == 0, skipped == 0 -> APPLIED"""
    res, ev = lut_result_to_mutation_outcome({"applied": 1, "skipped": 0, "failed": 0, "status": "applied"})
    assert res == MutationStatus.APPLIED
    assert ev.applied_count == 1
    assert ev.skipped_count == 0
    assert ev.failed_count == 0
    assert ev.raw_status == "applied"


def test_skipped_only():
    """applied == 0, failed == 0, skipped > 0 -> SKIPPED"""
    res, ev = lut_result_to_mutation_outcome({"applied": 0, "skipped": 1, "failed": 0, "status": "skipped"})
    assert res == MutationStatus.SKIPPED
    assert ev.applied_count == 0
    assert ev.skipped_count == 1
    assert ev.failed_count == 0
    assert ev.raw_status == "skipped"


def test_failed_only():
    """applied == 0, failed > 0, skipped == 0 -> FAILED"""
    res, ev = lut_result_to_mutation_outcome({"applied": 0, "skipped": 0, "failed": 1, "status": "error"})
    assert res == MutationStatus.FAILED
    assert ev.applied_count == 0
    assert ev.skipped_count == 0
    assert ev.failed_count == 1
    assert ev.raw_status == "error"


def test_applied_plus_failed():
    """applied > 0, failed > 0 -> PARTIAL_APPLIED (cấm che giấu lỗi bằng ok=True)"""
    res, ev = lut_result_to_mutation_outcome({"applied": 2, "skipped": 0, "failed": 1, "status": "error", "ok": True})
    assert res == MutationStatus.PARTIAL_APPLIED
    assert ev.applied_count == 2
    assert ev.skipped_count == 0
    assert ev.failed_count == 1
    assert ev.raw_status == "error"


def test_applied_plus_skipped():
    """applied > 0, failed == 0, skipped > 0 -> APPLIED (bỏ qua hợp lệ, đã có clip áp được)"""
    res, ev = lut_result_to_mutation_outcome({"applied": 2, "skipped": 1, "failed": 0, "status": "applied"})
    assert res == MutationStatus.APPLIED
    assert ev.applied_count == 2
    assert ev.skipped_count == 1
    assert ev.failed_count == 0
    assert ev.raw_status == "applied"


def test_skipped_plus_failed():
    """applied == 0, failed > 0, skipped > 0 -> FAILED (skipped không che failed!)"""
    res, ev = lut_result_to_mutation_outcome({"applied": 0, "skipped": 2, "failed": 1, "status": "error"})
    assert res == MutationStatus.FAILED
    assert ev.applied_count == 0
    assert ev.skipped_count == 2
    assert ev.failed_count == 1


def test_all_three_positive():
    """applied > 0, failed > 0, skipped > 0 -> PARTIAL_APPLIED"""
    res, ev = lut_result_to_mutation_outcome({"applied": 3, "skipped": 2, "failed": 1, "status": "error"})
    assert res == MutationStatus.PARTIAL_APPLIED
    assert ev.applied_count == 3
    assert ev.skipped_count == 2
    assert ev.failed_count == 1


def test_zero_all():
    """applied == 0, failed == 0, skipped == 0 -> UNKNOWN (không bằng chứng đột biến)"""
    res, ev = lut_result_to_mutation_outcome({"applied": 0, "skipped": 0, "failed": 0, "status": "none"})
    assert res == MutationStatus.UNKNOWN
    assert ev.applied_count == 0
    assert ev.skipped_count == 0
    assert ev.failed_count == 0
    assert ev.raw_status == "none"


# =========================================================================
# 2. KIỂM THỬ THIẾU COUNTERS / NONE / CHỮA CHÁY
# =========================================================================

@pytest.mark.parametrize(
    "payload,expected_counts",
    [
        ({"skipped": 0, "failed": 0}, (None, 0, 0)),
        ({"applied": 1, "failed": 0}, (1, None, 0)),
        ({"applied": 1, "skipped": 0}, (1, 0, None)),
        ({"applied": None, "skipped": 0, "failed": 0}, (None, 0, 0)),
        ({"applied": 1, "skipped": None, "failed": 0}, (1, None, 0)),
        ({"applied": 1, "skipped": 0, "failed": None}, (1, 0, None)),
        ({}, (None, None, None)),
    ]
)
def test_missing_or_none_counter_returns_unknown(payload, expected_counts):
    """Thiếu bất kỳ counter nào (key missing hoặc value None) -> UNKNOWN, giữ None trong evidence."""
    res, ev = lut_result_to_mutation_outcome(payload)
    assert res == MutationStatus.UNKNOWN
    assert (ev.applied_count, ev.skipped_count, ev.failed_count) == expected_counts


def test_raw_status_error_preserved():
    """Legacy status='error' phải được giữ nguyên trong raw_status."""
    _, ev = lut_result_to_mutation_outcome({"applied": 0, "skipped": 0, "failed": 1, "status": "error"})
    assert ev.raw_status == "error"


def test_raw_status_contradiction_counters_prevail():
    """Khi raw status mâu thuẫn bộ đếm (vd: status='applied' nhưng failed=1), counters quyết định."""
    res, ev = lut_result_to_mutation_outcome({"applied": 0, "skipped": 0, "failed": 1, "status": "applied"})
    assert res == MutationStatus.FAILED
    assert ev.raw_status == "applied"


def test_vietnamese_message_and_ok_not_inferred():
    """Không đoán mò status từ ok=True hoặc message tiếng Việt nếu thiếu counters."""
    res, ev = lut_result_to_mutation_outcome({"ok": True, "message": "Đã áp LUT thành công"})
    assert res == MutationStatus.UNKNOWN
    assert ev.applied_count is None


# =========================================================================
# 3. KIỂM THỬ VALIDATION POLICY & CÁC KIỂU DỮ LIỆU BẤT HỢP LỆ
# =========================================================================

@pytest.mark.parametrize("field_name", ["applied", "skipped", "failed"])
@pytest.mark.parametrize(
    "invalid_val",
    [
        True,               # bool
        False,              # bool
        "1",                # numeric string
        "invalid",          # string
        1.0,                # float
        float("nan"),       # NaN
        float("inf"),       # Inf
        -1,                 # âm
        -100,               # âm
        [],                 # list
        {},                 # dict
    ]
)
def test_invalid_counter_types_raise_value_error(field_name, invalid_val):
    """Mọi kiểu counter không phải (None hoặc int >= 0) bắt buộc ném ValueError."""
    payload = {"applied": 1, "skipped": 0, "failed": 0}
    payload[field_name] = invalid_val
    with pytest.raises(ValueError):
        lut_result_to_mutation_outcome(payload)


def test_invalid_in_present_field_not_masked_by_missing_fields():
    """Field hiện diện mang giá trị lỗi không được bị che giấu bởi việc thiếu các field khác."""
    with pytest.raises(ValueError):
        lut_result_to_mutation_outcome({"applied": "invalid"})

    with pytest.raises(ValueError):
        lut_result_to_mutation_outcome({"skipped": -5})

    with pytest.raises(ValueError):
        lut_result_to_mutation_outcome({"failed": 1.5})


@pytest.mark.parametrize("non_mapping", [None, 123, "string", [1, 2], (1, 2)])
def test_non_mapping_raises_value_error(non_mapping):
    """Đầu vào không phải Mapping bắt buộc ném ValueError."""
    with pytest.raises(ValueError):
        lut_result_to_mutation_outcome(non_mapping)


@pytest.mark.parametrize("invalid_raw_status", [123, True, [], {}])
def test_invalid_raw_status_raises_value_error(invalid_raw_status):
    """raw_status không phải str hay None bắt buộc ném ValueError."""
    with pytest.raises(ValueError):
        lut_result_to_mutation_outcome({"applied": 1, "skipped": 0, "failed": 0, "status": invalid_raw_status})


# =========================================================================
# 4. KIỂM THỬ TRỰC TIẾP DATACLASS MUTATIONEVIDENCE (FROZEN & BIG INT)
# =========================================================================

def test_mutation_evidence_frozen_and_valid():
    """MutationEvidence là frozen dataclass, hỗ trợ integer lớn mà không float overflow."""
    ev = MutationEvidence(applied_count=10**30, skipped_count=0, failed_count=0, raw_status="applied")
    assert ev.applied_count == 10**30
    assert ev.raw_status == "applied"

    with pytest.raises((TypeError, AttributeError)):
        ev.applied_count = 5  # FrozenInstanceError hoặc AttributeError


def test_mutation_evidence_direct_validation():
    """MutationEvidence trực tiếp ném ValueError khi khởi tạo với counter không hợp lệ."""
    with pytest.raises(ValueError):
        MutationEvidence(applied_count=True)

    with pytest.raises(ValueError):
        MutationEvidence(skipped_count=-1)

    with pytest.raises(ValueError):
        MutationEvidence(failed_count="2")

    with pytest.raises(ValueError):
        MutationEvidence(raw_status=123)


# =========================================================================
# 5. TƯƠNG THÍCH MAPPING TIÊU CHUẨN & BẢO TOÀN DỮ LIỆU NGUỒN (KHÔNG MUTATE/DEEPCOPY)
# =========================================================================

def test_mapping_proxy_type_support():
    """Hoạt động chuẩn xác với MappingProxyType (chứng minh chấp nhận Mapping trừu tượng, không chỉ dict)."""
    raw = {"applied": 1, "skipped": 0, "failed": 0, "status": "applied"}
    proxy = MappingProxyType(raw)
    res, ev = lut_result_to_mutation_outcome(proxy)
    assert res == MutationStatus.APPLIED
    assert ev.applied_count == 1


class UncopyableObject:
    def __deepcopy__(self, memo):
        raise RuntimeError("Không thể deepcopy đối tượng này!")


def test_uncopyable_runtime_objects_not_mutated_or_crashed():
    """
    Dict nguồn chứa object phức tạp không deepcopy được (vd: QObject, lock, handle)
    không làm crash adapter và không bị mutate.
    """
    obj = UncopyableObject()
    raw = {
        "applied": 1,
        "skipped": 0,
        "failed": 0,
        "runtime_handle": obj,
        "extra_info": [1, 2, 3],
    }
    res, ev = lut_result_to_mutation_outcome(raw)
    assert res == MutationStatus.APPLIED
    assert ev.applied_count == 1
    # Dữ liệu nguồn giữ nguyên vẹn
    assert raw["runtime_handle"] is obj
    assert raw["extra_info"] == [1, 2, 3]


# =========================================================================
# 6. ĐẶC TẢ ĐỘC LẬP: CÁC CỜ CANCEL VÀ GUIDANCE
# =========================================================================

def test_cancel_flag_does_not_alter_mutation_evidence():
    """
    Cancel flag trong dict (vd: cancelled=True) với counters 1/0/0 vẫn là APPLIED.
    Trục JobStatus bị hủy không tự động biến đổi trục MutationStatus.
    """
    raw = {"applied": 1, "skipped": 0, "failed": 0, "cancelled": True, "status": "cancelled"}
    res, ev = lut_result_to_mutation_outcome(raw)
    assert res == MutationStatus.APPLIED
    assert ev.applied_count == 1


def test_lut_adapter_does_not_infer_guidance_from_flags_or_zero():
    """
    LUT adapter tuyệt đối không suy đoán NO_OP_GUIDANCE kể cả khi có cờ is_guidance=True
    hoặc counters toàn 0; với LUT adapter, all-zero bắt buộc là UNKNOWN.
    """
    raw = {"applied": 0, "skipped": 0, "failed": 0, "is_guidance": True, "message": "Hãy kéo từ Effects"}
    res, ev = lut_result_to_mutation_outcome(raw)
    assert res == MutationStatus.UNKNOWN
