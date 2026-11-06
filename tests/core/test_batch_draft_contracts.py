"""
tests/core/test_batch_draft_contracts.py
=============================================================================
Unit Test Suite cho G06-06A4b-I (Pure Geometry & Batch Draft Contracts):
- Value Objects: BatchSourceRange, ValidatedSegmentCut, ValidatedBatchDraftEntry.
- Pure Validation Function: validate_batch_draft_payload.
- Strict Geometry Bounds: exact intervals, gap bridge reject, short cut valid.
- Strict Type & Numeric Checks: no bool, no NaN, no Inf, overflow reject.
- Snapshot Binding Modes: echo_matched vs user_associated_current_snapshot.
- Strict JSON Decoding: duplicate keys, non-finite constants, multiple fences.
=============================================================================
"""

import json
import pytest

from src.core.batch_draft_contracts import (
    BatchSourceRange,
    ValidatedSegmentCut,
    ValidatedBatchDraftEntry,
    BatchDraftValidationResult,
    validate_batch_draft_payload,
    decode_strict_batch_draft_json,
    verify_validated_entry_integrity,
    ERR_MALFORMED_JSON,
    ERR_OVERSIZE_PAYLOAD,
    ERR_DUPLICATE_JSON_KEYS,
    ERR_UNSUPPORTED_SCHEMA_VERSION,
    ERR_MANIFEST_MISMATCH,
    ERR_BATCH_ID_MISMATCH,
    ERR_STALE_SNAPSHOT,
    ERR_MISSING_SNAPSHOT_ID,
    ERR_FOREIGN_CLIP_ID,
    ERR_OUT_OF_PARTITION_BOUNDS,
    ERR_REVERSED_OR_ZERO_DURATION,
    ERR_NONFINITE_TIMESTAMP,
    ERR_UNSUPPORTED_ACTION,
    ERR_INVALID_SPEED,
    ERR_INVALID_WINDOW_REF,
    ERR_MISSING_BATCH_SUMMARY,
    ERR_UNSUPPORTED_CONTAINER,
    ERR_MALFORMED_SEGMENTS,
)


@pytest.fixture
def sample_source_ranges():
    return (
        BatchSourceRange(
            clip_id="C01",
            source_in_sec=0.0,
            source_out_sec=600.0,
            partition_index=1,
            total_partitions=3,
            window_ids=("W01", "W02", "W03"),
        ),
        BatchSourceRange(
            clip_id="C02",
            source_in_sec=100.0,
            source_out_sec=200.0,
            partition_index=0,
            total_partitions=1,
            window_ids=("W10", "W11"),
        ),
    )


# =============================================================================
# NHÓM 1: VALUE OBJECTS & IMMUTABILITY
# =============================================================================

def test_batch_source_range_validation():
    # 1. Hợp lệ
    r = BatchSourceRange(
        clip_id="C01",
        source_in_sec=10.0,
        source_out_sec=20.0,
        partition_index=1,
        total_partitions=2,
        window_ids=("W1",),
    )
    assert r.clip_id == "C01"
    assert r.source_in_sec == 10.0
    assert r.source_out_sec == 20.0

    # 2. Frozen immutability
    with pytest.raises(Exception):
        r.clip_id = "C02"

    # 3. Lỗi reversed hoặc in >= out
    with pytest.raises(ValueError):
        BatchSourceRange("C01", 20.0, 10.0, 1, 2)
    with pytest.raises(ValueError):
        BatchSourceRange("C01", 10.0, 10.0, 1, 2)

    # 4. Lỗi thời gian âm
    with pytest.raises(ValueError):
        BatchSourceRange("C01", -5.0, 10.0, 1, 2)

    # 5. Lỗi partition index
    with pytest.raises(ValueError):
        BatchSourceRange("C01", 0.0, 10.0, -1, 2)
    with pytest.raises(ValueError):
        BatchSourceRange("C01", 0.0, 10.0, 0, 0)
    with pytest.raises(ValueError):
        BatchSourceRange("C01", 0.0, 10.0, 3, 2)


# =============================================================================
# NHÓM 2: STRICT JSON DECODING HELPER
# =============================================================================

def test_decode_strict_batch_draft_json():
    # 1. JSON thường hợp lệ
    raw = '{"manifest_id": "M01", "batch_id": "B01"}'
    ok, err_code, err_msg, data = decode_strict_batch_draft_json(raw)
    assert ok is True
    assert data["manifest_id"] == "M01"

    # 2. JSON bọc đúng một code fence markdown
    raw_fence = '```json\n{"manifest_id": "M01", "batch_id": "B01"}\n```'
    ok, err_code, err_msg, data = decode_strict_batch_draft_json(raw_fence)
    assert ok is True
    assert data["batch_id"] == "B01"

    # 3. Lỗi trùng lặp key (Duplicate keys reject)
    raw_dup = '{"manifest_id": "M01", "batch_id": "B01", "batch_id": "B02"}'
    ok, err_code, err_msg, data = decode_strict_batch_draft_json(raw_dup)
    assert ok is False
    assert err_code == ERR_DUPLICATE_JSON_KEYS

    # 4. Lỗi hằng số NaN / Infinity
    raw_nan = '{"manifest_id": "M01", "batch_id": "B01", "start_sec": NaN}'
    ok, err_code, err_msg, data = decode_strict_batch_draft_json(raw_nan)
    assert ok is False
    assert err_code == ERR_MALFORMED_JSON

    # 5. Lỗi nhiều khối code fence (Multiple markdown blocks reject)
    raw_multi = '```json\n{"a": 1}\n```\n```json\n{"b": 2}\n```'
    ok, err_code, err_msg, data = decode_strict_batch_draft_json(raw_multi)
    assert ok is False
    assert err_code == ERR_MALFORMED_JSON

    # 6. Lỗi vượt trần kích thước (> 65,536 bytes)
    big_raw = '{"text": "' + ('x' * 66000) + '"}'
    ok, err_code, err_msg, data = decode_strict_batch_draft_json(big_raw)
    assert ok is False
    assert err_code == ERR_OVERSIZE_PAYLOAD


# =============================================================================
# NHÓM 3: PURE VALIDATION (validate_batch_draft_payload)
# =============================================================================

def test_validate_batch_draft_happy_path(sample_source_ranges):
    payload = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": "Chiến lược phân lô 1.",
        "timeline_segments": [
            {
                "clip_id": "C01",
                "start_sec": 0.0,
                "end_sec": 15.5,
                "action": "keep",
                "role": "dialogue",
                "note": "Chào mở đầu",
                "source_window_ref": "W01",
            },
            {
                "clip_id": "C02",
                "start_sec": 120.0,
                "end_sec": 130.0,
                "action": "speedup",
                "speed": 2.5,
                "role": "transition",
                "note": "Tua nhanh",
                "source_window_ref": "W10",
            },
        ],
    }

    res = validate_batch_draft_payload(
        payload,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
    )

    assert res.ok is True
    assert res.entry is not None
    assert res.entry.batch_id == "B01"
    assert res.entry.binding_mode == "echo_matched"
    assert res.entry.total_cuts_count == 2
    assert res.entry.total_selected_duration_sec == pytest.approx(25.5)
    assert len(res.entry.segments) == 2
    assert res.entry.segments[0].action == "keep"
    assert res.entry.segments[1].speed == 2.5


def test_validate_batch_draft_short_cut_and_boundary_equality(sample_source_ranges):
    # Short cut < 0.1s là hoàn toàn hợp lệ (không áp đặt min 0.1s tùy tiện)
    # Exact boundary equality: start == source_in_sec, end == source_out_sec hợp lệ
    payload = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": "Phân đoạn ngắn.",
        "timeline_segments": [
            {
                "clip_id": "C01",
                "start_sec": 0.0,
                "end_sec": 0.05,  # 0.05s (< 0.1s)
                "action": "cut",
            },
            {
                "clip_id": "C01",
                "start_sec": 0.0,
                "end_sec": 600.0,  # Đúng toàn bộ range của C01
                "action": "keep",
            },
        ],
    }

    res = validate_batch_draft_payload(
        payload,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
    )
    assert res.ok is True
    assert res.entry.total_cuts_count == 2


def test_validate_batch_draft_out_of_bounds_no_epsilon(sample_source_ranges):
    # Vi phạm biên nguồn dù chỉ 0.01s (không cho phép epsilon 0.05s)
    # Range của C02 là 100.0 .. 200.0
    payload_under = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": "Lỗi dưới biên",
        "timeline_segments": [
            {"clip_id": "C02", "start_sec": 99.99, "end_sec": 150.0, "action": "keep"}
        ],
    }
    res1 = validate_batch_draft_payload(
        payload_under,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
    )
    assert res1.ok is False
    assert res1.error_code == ERR_OUT_OF_PARTITION_BOUNDS

    payload_over = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": "Lỗi trên biên",
        "timeline_segments": [
            {"clip_id": "C01", "start_sec": 590.0, "end_sec": 600.01, "action": "keep"}
        ],
    }
    res2 = validate_batch_draft_payload(
        payload_over,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
    )
    assert res2.ok is False
    assert res2.error_code == ERR_OUT_OF_PARTITION_BOUNDS


def test_validate_batch_draft_gap_bridge_rejected():
    # 2 partition của cùng 1 clip nhưng có gap ở giữa: C01 (0..100) và C01 (200..300)
    gapped_ranges = (
        BatchSourceRange("C01", 0.0, 100.0, 0, 2),
        BatchSourceRange("C01", 200.0, 300.0, 1, 2),
    )
    payload_bridge = {
        "manifest_id": "M01",
        "batch_id": "B01",
        "snapshot_id": "SNAP01",
        "batch_strategy_summary": "Bridge gap",
        "timeline_segments": [
            # Cut bắt đầu ở partition 1 và kết thúc ở partition 2 (bắc cầu qua gap 100..200)
            {"clip_id": "C01", "start_sec": 50.0, "end_sec": 250.0, "action": "keep"}
        ],
    }
    res = validate_batch_draft_payload(
        payload_bridge,
        expected_manifest_id="M01",
        expected_snapshot_id="SNAP01",
        expected_batch_id="B01",
        source_ranges=gapped_ranges,
    )
    assert res.ok is False
    assert res.error_code == ERR_OUT_OF_PARTITION_BOUNDS


def test_validate_batch_draft_foreign_clip_id(sample_source_ranges):
    payload = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": "Clip lạ",
        "timeline_segments": [
            {"clip_id": "C_UNKNOWN", "start_sec": 1.0, "end_sec": 5.0, "action": "keep"}
        ],
    }
    res = validate_batch_draft_payload(
        payload,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
    )
    assert res.ok is False
    assert res.error_code == ERR_FOREIGN_CLIP_ID


def test_validate_batch_draft_snapshot_binding_modes(sample_source_ranges):
    # 1. Payload thiếu snapshot_id, associate_with_current_snapshot=False -> ERR_MISSING_SNAPSHOT_ID
    payload_no_snap = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "batch_strategy_summary": "Không có snapshot_id",
        "timeline_segments": [],
    }
    res1 = validate_batch_draft_payload(
        payload_no_snap,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
        associate_with_current_snapshot=False,
    )
    assert res1.ok is False
    assert res1.error_code == ERR_MISSING_SNAPSHOT_ID

    # 2. Payload thiếu snapshot_id, associate_with_current_snapshot=True (User click button)
    # -> Hợp lệ với binding_mode="user_associated_current_snapshot"
    res2 = validate_batch_draft_payload(
        payload_no_snap,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
        associate_with_current_snapshot=True,
    )
    assert res2.ok is True
    assert res2.entry.binding_mode == "user_associated_current_snapshot"
    assert res2.entry.snapshot_id == "SNAP_01"

    # 3. Payload có snapshot_id nhưng lệch -> ERR_STALE_SNAPSHOT
    payload_stale = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_OLD",
        "batch_strategy_summary": "Snapshot cũ",
        "timeline_segments": [],
    }
    res3 = validate_batch_draft_payload(
        payload_stale,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
        associate_with_current_snapshot=True,
    )
    assert res3.ok is False
    assert res3.error_code == ERR_STALE_SNAPSHOT


def test_validate_batch_draft_actions_and_speed(sample_source_ranges):
    # 1. Action speedup với speed hợp lệ
    payload_spd = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": "Speedup",
        "timeline_segments": [
            {"clip_id": "C01", "start_sec": 10.0, "end_sec": 20.0, "action": "speedup", "speed": 4.0}
        ],
    }
    res1 = validate_batch_draft_payload(
        payload_spd,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
    )
    assert res1.ok is True
    assert res1.entry.segments[0].speed == 4.0

    # 2. Action speedup với speed <= 1.0 hoặc > 16.0
    payload_bad_spd = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": "Bad speed",
        "timeline_segments": [
            {"clip_id": "C01", "start_sec": 10.0, "end_sec": 20.0, "action": "speedup", "speed": 1.0}
        ],
    }
    res2 = validate_batch_draft_payload(
        payload_bad_spd,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
    )
    assert res2.ok is False
    assert res2.error_code == ERR_INVALID_SPEED

    # 3. Action không hỗ trợ (ví dụ "hold" hay "broll")
    payload_bad_act = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": "Bad action",
        "timeline_segments": [
            {"clip_id": "C01", "start_sec": 10.0, "end_sec": 20.0, "action": "unsupported_action"}
        ],
    }
    res3 = validate_batch_draft_payload(
        payload_bad_act,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
    )
    assert res3.ok is False
    assert res3.error_code == ERR_UNSUPPORTED_ACTION


def test_validate_batch_draft_source_window_refs(sample_source_ranges):
    # C01 có window_ids là ("W01", "W02", "W03")
    # 1. source_window_ref hợp lệ
    payload_ok = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": "Window ref ok",
        "timeline_segments": [
            {"clip_id": "C01", "start_sec": 10.0, "end_sec": 20.0, "source_window_ref": "W02"}
        ],
    }
    res1 = validate_batch_draft_payload(
        payload_ok,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
    )
    assert res1.ok is True

    # 2. source_window_ref không tồn tại trong range của clip
    payload_bad = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": "Window ref bad",
        "timeline_segments": [
            {"clip_id": "C01", "start_sec": 10.0, "end_sec": 20.0, "source_window_ref": "W999"}
        ],
    }
    res2 = validate_batch_draft_payload(
        payload_bad,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
    )
    assert res2.ok is False
    assert res2.error_code == ERR_INVALID_WINDOW_REF


def test_validate_batch_draft_numeric_types_strict(sample_source_ranges):
    # Boolean trong Python kế thừa int (isinstance(True, int) is True),
    # bắt buộc phải reject không cho phép True/False làm start_sec hoặc end_sec!
    payload_bool = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": "Bool timestamp",
        "timeline_segments": [
            {"clip_id": "C01", "start_sec": False, "end_sec": 10.0}
        ],
    }
    res = validate_batch_draft_payload(
        payload_bool,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
    )
    assert res.ok is False
    assert res.error_code == ERR_NONFINITE_TIMESTAMP


# =============================================================================
# NHÓM 5: CODEX PROBE REGRESSION TESTS (G06-06A4b-R1)
# =============================================================================

def test_batch_draft_decode_strict_rejects_surrounding_garbage_and_fences():
    """Khóa chặt decoder: Whole JSON object hoặc đúng 1 code fence hoàn chỉnh.
    Cấm surrounding prose/garbage, multiple fences, array wrapper, quoted string."""
    # 1. Surrounding text trước/sau JSON
    raw_garbage = 'PREFIX {"manifest_id": "M01", "batch_id": "B01"} SUFFIX'
    ok, code, msg, data = decode_strict_batch_draft_json(raw_garbage)
    assert ok is False
    assert code == ERR_MALFORMED_JSON

    # 2. Text ngoài code fence
    raw_fence_garbage = 'Here is the JSON:\n```json\n{"manifest_id": "M01", "batch_id": "B01"}\n```\nHope it helps!'
    ok, code, msg, data = decode_strict_batch_draft_json(raw_fence_garbage)
    assert ok is False
    assert code == ERR_MALFORMED_JSON

    # 3. Array wrapper
    raw_array = '[{"manifest_id": "M01", "batch_id": "B01"}]'
    ok, code, msg, data = decode_strict_batch_draft_json(raw_array)
    assert ok is False
    assert code == ERR_MALFORMED_JSON

    # 4. Quoted JSON string
    raw_quoted = '"{\\"manifest_id\\": \\"M01\\"}"'
    ok, code, msg, data = decode_strict_batch_draft_json(raw_quoted)
    assert ok is False
    assert code == ERR_MALFORMED_JSON

    # 5. Đúng 1 code fence kèm whitespace ngoài -> Hợp lệ
    raw_valid_fence = '   \n```json\n{"manifest_id": "M01", "batch_id": "B01"}\n``` \n  '
    ok, code, msg, data = decode_strict_batch_draft_json(raw_valid_fence)
    assert ok is True
    assert data["manifest_id"] == "M01"


def test_batch_draft_validator_strict_action_role_speed_types(sample_source_ranges):
    """Khóa chặt kiểu dữ liệu trong segments: cấm type coercion (bool action, object role, keep speed Inf)."""
    # 1. action=True (bool kế thừa int/str check)
    payload_bool_action = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": "Test action bool",
        "timeline_segments": [{"clip_id": "C01", "start_sec": 0.0, "end_sec": 10.0, "action": True}],
    }
    res = validate_batch_draft_payload(
        payload_bool_action,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
    )
    assert res.ok is False
    assert res.error_code == ERR_UNSUPPORTED_ACTION

    # 2. role={...} (object thay vì str)
    payload_obj_role = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": "Test role object",
        "timeline_segments": [{"clip_id": "C01", "start_sec": 0.0, "end_sec": 10.0, "role": {"role": "hook"}}],
    }
    res = validate_batch_draft_payload(
        payload_obj_role,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
    )
    assert res.ok is False
    assert res.error_code == ERR_MALFORMED_SEGMENTS

    # 3. keep với speed=float("inf") hoặc speed=2.0
    payload_keep_inf = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": "Test keep speed inf",
        "timeline_segments": [{"clip_id": "C01", "start_sec": 0.0, "end_sec": 10.0, "action": "keep", "speed": float("inf")}],
    }
    res = validate_batch_draft_payload(
        payload_keep_inf,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
    )
    assert res.ok is False
    assert res.error_code == ERR_INVALID_SPEED

    # 4. source_window_ref được truyền khi window_ids của range rỗng ()
    empty_win_range = (BatchSourceRange("C01", 0.0, 100.0, 0, 1, window_ids=()),)
    payload_unknown_win = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": "Unknown window ref",
        "timeline_segments": [{"clip_id": "C01", "start_sec": 0.0, "end_sec": 10.0, "source_window_ref": "W_GHOST"}],
    }
    res = validate_batch_draft_payload(
        payload_unknown_win,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=empty_win_range,
    )
    assert res.ok is False
    assert res.error_code == ERR_INVALID_WINDOW_REF


def test_batch_draft_validator_overflow_and_exact_raw_ids(sample_source_ranges):
    """Xử lý an toàn 10**400 không văng OverflowError; đối chiếu exact raw IDs không tự tiện .strip()."""
    # 1. 10**400 timestamp -> controlled ERR_NONFINITE_TIMESTAMP
    payload_overflow = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": "Overflow test",
        "timeline_segments": [{"clip_id": "C01", "start_sec": 0.0, "end_sec": 10**400}],
    }
    res = validate_batch_draft_payload(
        payload_overflow,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
    )
    assert res.ok is False
    assert res.error_code == ERR_NONFINITE_TIMESTAMP

    # 2. Exact raw manifest_id: expected 'M_TEST_01' nhưng payload có ' M_TEST_01 '
    payload_strip = {
        "manifest_id": " M_TEST_01 ",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": "Strip test",
        "timeline_segments": [],
    }
    res = validate_batch_draft_payload(
        payload_strip,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
    )
    assert res.ok is False
    assert res.error_code == ERR_MANIFEST_MISMATCH

    # 3. snapshot_id sai nhưng associate_with_current_snapshot=True -> Phải báo ERR_STALE_SNAPSHOT (không âm thầm coi là missing)
    payload_wrong_snap = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_WRONG",
        "batch_strategy_summary": "Stale snap test",
        "timeline_segments": [],
    }
    res = validate_batch_draft_payload(
        payload_wrong_snap,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
        associate_with_current_snapshot=True,
    )
    assert res.ok is False
    assert res.error_code == ERR_STALE_SNAPSHOT

    # 4. associate_with_current_snapshot không phải bool -> ValueError
    with pytest.raises(ValueError):
        validate_batch_draft_payload(
            payload_wrong_snap,
            expected_manifest_id="M_TEST_01",
            expected_snapshot_id="SNAP_01",
            expected_batch_id="B01",
            source_ranges=sample_source_ranges,
            associate_with_current_snapshot="true",  # String
        )


def test_batch_draft_lossless_summary_and_distinct_digest(sample_source_ranges):
    """Bảo toàn lossless toàn văn summary: 2 summaries dài >4096 ký tự khác đuôi phải ra 2 draft_digest khác nhau."""
    base_text = "A" * 4096
    summary_1 = base_text + "_SUFFIX_ONE"
    summary_2 = base_text + "_SUFFIX_TWO"

    p1 = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": summary_1,
        "timeline_segments": [{"clip_id": "C01", "start_sec": 0.0, "end_sec": 10.0}],
    }
    p2 = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": summary_2,
        "timeline_segments": [{"clip_id": "C01", "start_sec": 0.0, "end_sec": 10.0}],
    }

    res1 = validate_batch_draft_payload(
        p1,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
    )
    res2 = validate_batch_draft_payload(
        p2,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
    )

    assert res1.ok is True
    assert res2.ok is True
    assert res1.entry.batch_strategy_summary == summary_1
    assert res2.entry.batch_strategy_summary == summary_2
    assert res1.entry.draft_digest != res2.entry.draft_digest


def test_batch_source_range_partition_index_bounds_and_window_ids():
    """BatchSourceRange: 0 <= partition_index < total_partitions, strict non-blank string window_ids, cấm mutable objects."""
    # 1. partition_index == total_partitions -> ValueError
    with pytest.raises(ValueError):
        BatchSourceRange("C01", 0.0, 10.0, 2, 2)

    # 2. partition_index > total_partitions -> ValueError
    with pytest.raises(ValueError):
        BatchSourceRange("C01", 0.0, 10.0, 3, 2)

    # 3. 0-based hợp lệ: 0 < 2 và 1 < 2
    r0 = BatchSourceRange("C01", 0.0, 10.0, 0, 2)
    r1 = BatchSourceRange("C01", 10.0, 20.0, 1, 2)
    assert r0.partition_index == 0
    assert r1.partition_index == 1

    # 4. window_ids chứa dict mutable hoặc bool -> ValueError
    with pytest.raises(ValueError):
        BatchSourceRange("C01", 0.0, 10.0, 0, 1, window_ids=({"key": "val"},))
    with pytest.raises(ValueError):
        BatchSourceRange("C01", 0.0, 10.0, 0, 1, window_ids=(True,))


def test_verify_validated_entry_integrity_rejects_forged_timestamps(sample_source_ranges):
    """Cổng đối soát in-memory: chặn đứng DTO bị giả mạo end_sec=9999 hoặc digest lệch."""
    payload = {
        "manifest_id": "M_TEST_01",
        "batch_id": "B01",
        "snapshot_id": "SNAP_01",
        "batch_strategy_summary": "Valid base",
        "timeline_segments": [{"clip_id": "C01", "start_sec": 0.0, "end_sec": 10.0}],
    }
    res = validate_batch_draft_payload(
        payload,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        source_ranges=sample_source_ranges,
    )
    assert res.ok is True
    valid_entry = res.entry

    # 1. Đầu vào hợp lệ -> vượt qua gate
    verified = verify_validated_entry_integrity(
        valid_entry,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        expected_source_ranges=sample_source_ranges,
        expected_binding_mode="echo_matched",
        expected_draft_digest=valid_entry.draft_digest,
        expected_raw_payload_sha256=valid_entry.raw_payload_sha256,
    )
    assert verified is not None
    assert verified.draft_digest == valid_entry.draft_digest

    # 2. Giả mạo end_sec=9999 trong segment
    forged_dict = valid_entry.to_dict()
    forged_dict["segments"][0]["end_sec"] = 9999.0
    verified_bad = verify_validated_entry_integrity(
        forged_dict,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        expected_source_ranges=sample_source_ranges,
        expected_binding_mode="echo_matched",
        expected_draft_digest=valid_entry.draft_digest,
        expected_raw_payload_sha256=valid_entry.raw_payload_sha256,
    )
    assert verified_bad is None

    # 3. Giả mạo digest
    verified_bad_digest = verify_validated_entry_integrity(
        valid_entry,
        expected_manifest_id="M_TEST_01",
        expected_snapshot_id="SNAP_01",
        expected_batch_id="B01",
        expected_source_ranges=sample_source_ranges,
        expected_binding_mode="echo_matched",
        expected_draft_digest="fake_digest_0000000000000000000000000000000000000000000000000000",
        expected_raw_payload_sha256=valid_entry.raw_payload_sha256,
    )
    assert verified_bad_digest is None
