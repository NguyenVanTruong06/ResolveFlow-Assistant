"""Unit tests cho Hợp đồng Hồ sơ Phê duyệt ChunDVC (AppOwnedApprovalRecord) — G06-06C1b2-I.

Kiểm thử toàn diện:
- Cấu trúc frozen DTO AppOwnedApprovalRecord với đúng 6 fields chuẩn tắc.
- Ba public helpers/factories: create_app_owned_approval_record, approval_record_to_dict, verify_proposal_approval_binding.
- Tính bất biến (frozen dataclass, tuple immutable), defensive copies và determinism.
- Ranh giới kiểu và định dạng: proposal_revision 64-hex lowercase, ISO timestamp có timezone (loại trừ naive/date-only/whitespace).
- Ranh giới accepted_omission_ids: len <= 128 (pre-check 0 iterations), unique exact, empty tuple hợp lệ.
- approval_scope bắt buộc chỉ 'ENTIRE_PROPOSAL'.
- Giới hạn dung lượng UTF-8 (MAX_APPROVAL_RECORD_BYTES = 528,384): equality accepted, +1 rejected.
- Xác minh binding: True khi khớp tuyệt đối, False khi phát hiện bất kỳ thay đổi nào (stale) ở bất kỳ tầng nào.
- Purity spies: zero filesystem IO (open, stat), zero network (socket), zero system clock (now).
"""

import dataclasses
from datetime import datetime
import json
import os
import socket
import time
import pytest

from src.core.contracts.edit_plan_approval_contracts import (
    AppOwnedApprovalRecord,
    MAX_APPROVAL_RECORD_BYTES,
    approval_record_to_dict,
    create_app_owned_approval_record,
    verify_proposal_approval_binding,
)
from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    MAX_PROPOSED_OMISSIONS,
    compute_proposal_revision,
    normalize_edit_plan_proposal,
)
from tests.core.contracts.test_edit_plan_proposal_contracts import (
    CountedList,
    _make_valid_proposal_dict,
)


def _make_valid_proposal(manifest_id: str = "m_test01") -> EditPlanProposal:
    raw_dict = _make_valid_proposal_dict(manifest_id)
    return normalize_edit_plan_proposal(raw_dict)


# ==============================================================================
# 1. FACTORY & ROUNDTRIP TESTS
# ==============================================================================

def test_create_app_owned_approval_record_with_omissions():
    """Tạo bản ghi phê duyệt hợp lệ từ proposal có proposed omissions."""
    proposal = _make_valid_proposal()
    # Baseline proposal có 1 omission: demand_id='dem_broll_01'
    approval = create_app_owned_approval_record(
        proposal,
        approval_id="appr_20261029_001",
        approved_at_iso="2026-10-29T20:30:00Z",
    )

    assert isinstance(approval, AppOwnedApprovalRecord)
    assert approval.approval_id == "appr_20261029_001"
    assert approval.proposal_id == proposal.proposal_id
    assert approval.proposal_revision == compute_proposal_revision(proposal)
    assert approval.approved_at_iso == "2026-10-29T20:30:00Z"
    assert approval.accepted_omission_ids == ("dem_broll_01",)
    assert approval.approval_scope == "ENTIRE_PROPOSAL"

    # Export roundtrip
    exported = approval_record_to_dict(approval)
    assert isinstance(exported, dict)
    assert len(exported) == 6
    assert exported["approval_id"] == "appr_20261029_001"
    assert exported["proposal_id"] == proposal.proposal_id
    assert exported["proposal_revision"] == approval.proposal_revision
    assert exported["approved_at_iso"] == "2026-10-29T20:30:00Z"
    assert exported["accepted_omission_ids"] == ["dem_broll_01"]
    assert exported["approval_scope"] == "ENTIRE_PROPOSAL"

    # Determinism: cùng input cho ra kết quả y hệt
    approval2 = create_app_owned_approval_record(
        proposal,
        approval_id="appr_20261029_001",
        approved_at_iso="2026-10-29T20:30:00Z",
    )
    assert approval == approval2

    # Common approval một lượt: bao trùm toàn bộ proposal (không per-item API)
    assert verify_proposal_approval_binding(proposal, approval) is True


def test_create_app_owned_approval_record_zero_omissions():
    """Tạo bản ghi phê duyệt hợp lệ khi proposal không có omission nào."""
    raw_dict = _make_valid_proposal_dict()
    raw_dict["proposed_omissions"] = []
    proposal = normalize_edit_plan_proposal(raw_dict)

    approval = create_app_owned_approval_record(
        proposal,
        approval_id="appr_20261029_zero",
        approved_at_iso="2026-10-29T20:30:00+07:00",
    )
    assert approval.accepted_omission_ids == ()
    assert verify_proposal_approval_binding(proposal, approval) is True


def test_create_app_owned_approval_record_multiple_omissions_order_preserved():
    """Tự động trích xuất đúng toàn bộ demand_id theo thứ tự của proposed_omissions."""
    raw_dict = _make_valid_proposal_dict()
    raw_dict["proposed_omissions"] = [
        {"demand_id": "omit_alpha", "slot": "broll", "timeline_sec": 1.0, "duration_sec": 1.0, "reason": "R1", "warning_message": "W1"},
        {"demand_id": "omit_beta", "slot": "sfx", "timeline_sec": 2.0, "duration_sec": 1.0, "reason": "R2", "warning_message": "W2"},
        {"demand_id": "omit_gamma", "slot": "broll", "timeline_sec": 3.0, "duration_sec": 1.0, "reason": "R3", "warning_message": "W3"},
    ]
    proposal = normalize_edit_plan_proposal(raw_dict)

    approval = create_app_owned_approval_record(
        proposal,
        approval_id="appr_multi",
        approved_at_iso="2026-10-29T20:30:00.123456Z",
    )
    assert approval.accepted_omission_ids == ("omit_alpha", "omit_beta", "omit_gamma")
    assert verify_proposal_approval_binding(proposal, approval) is True


def test_factory_rejects_non_proposal_and_caller_override_protection():
    """Chặn đứng input không phải EditPlanProposal và ngăn caller override hash/scope."""
    # 1. Truyền raw dict hoặc None
    with pytest.raises(ValueError, match="ERR_INVALID_TYPE"):
        create_app_owned_approval_record(
            _make_valid_proposal_dict(),  # type: ignore
            approval_id="appr_01",
            approved_at_iso="2026-10-29T20:30:00Z",
        )

    with pytest.raises(ValueError, match="ERR_INVALID_TYPE"):
        create_app_owned_approval_record(
            None,  # type: ignore
            approval_id="appr_01",
            approved_at_iso="2026-10-29T20:30:00Z",
        )

    # 2. Lookalike class
    class LookalikeProposal:
        proposal_id = "prop_fake"

    with pytest.raises(ValueError, match="ERR_INVALID_TYPE"):
        create_app_owned_approval_record(
            LookalikeProposal(),  # type: ignore
            approval_id="appr_01",
            approved_at_iso="2026-10-29T20:30:00Z",
        )

    # 3. Caller cố tình truyền tham số ghi đè hash hoặc scope bị TypeError
    proposal = _make_valid_proposal()
    with pytest.raises(TypeError):
        create_app_owned_approval_record(  # type: ignore
            proposal,
            approval_id="appr_01",
            approved_at_iso="2026-10-29T20:30:00Z",
            proposal_revision="forged_hash",
        )

    with pytest.raises(TypeError):
        create_app_owned_approval_record(  # type: ignore
            proposal,
            approval_id="appr_01",
            approved_at_iso="2026-10-29T20:30:00Z",
            approval_scope="ENTIRE_PLAN_BATCH",
        )


# ==============================================================================
# 2. FIELD VALIDATIONS & TYPE GATES
# ==============================================================================

def test_approval_record_id_and_scope_validation():
    proposal = _make_valid_proposal()
    valid_rev = compute_proposal_revision(proposal)
    valid_time = "2026-10-29T20:30:00Z"

    # 1. approval_id không hợp lệ
    for bad_aid in ("", "   ", 123, None, "bad\0id"):
        with pytest.raises(ValueError):
            AppOwnedApprovalRecord(
                approval_id=bad_aid,  # type: ignore
                proposal_id=proposal.proposal_id,
                proposal_revision=valid_rev,
                approved_at_iso=valid_time,
            )

    # 2. proposal_id không hợp lệ
    for bad_pid in ("", "   ", 123, None, "bad\0pid"):
        with pytest.raises(ValueError):
            AppOwnedApprovalRecord(
                approval_id="appr_01",
                proposal_id=bad_pid,  # type: ignore
                proposal_revision=valid_rev,
                approved_at_iso=valid_time,
            )

    # 3. proposal_revision không đúng 64 hex lowercase
    bad_revisions = (
        "",
        "   ",
        "a" * 63,  # 63 chars
        "a" * 65,  # 65 chars
        "A" * 64,  # Uppercase
        "g" * 64,  # Non-hex
        123,
        None,
    )
    for bad_rev in bad_revisions:
        with pytest.raises(ValueError, match="ERR_INVALID_REVISION|ERR_INVALID_TYPE"):
            AppOwnedApprovalRecord(
                approval_id="appr_01",
                proposal_id=proposal.proposal_id,
                proposal_revision=bad_rev,  # type: ignore
                approved_at_iso=valid_time,
            )

    # 4. approval_scope chỉ được là 'ENTIRE_PROPOSAL'
    bad_scopes = ("ENTIRE_PLAN_BATCH", "PARTIAL", "", "   ", 123, None)
    for bad_scope in bad_scopes:
        with pytest.raises(ValueError, match="ERR_INVALID_SCOPE"):
            AppOwnedApprovalRecord(
                approval_id="appr_01",
                proposal_id=proposal.proposal_id,
                proposal_revision=valid_rev,
                approved_at_iso=valid_time,
                approval_scope=bad_scope,  # type: ignore
            )


def test_approved_at_iso_strict_timestamp_validation():
    proposal = _make_valid_proposal()
    valid_rev = compute_proposal_revision(proposal)

    # 1. Các định dạng timestamp không hợp lệ (lexical formatrecordv1 hoặc calendar)
    bad_timestamps = (
        # Naive / thiếu timezone
        "2026-10-29T20:30:00",
        # Date only không có 'T'
        "2026-10-29",
        # Phân cách khoảng trắng thay vì 'T'
        "2026-10-29 20:30:00Z",
        # Khoảng trắng thừa (cấm tự ý trim)
        " 2026-10-29T20:30:00Z",
        "2026-10-29T20:30:00Z ",
        # Bốn probes trong review của Codex (G06-06C1b2-I-codex-review.md):
        "2026-10-29T21:18:00.1234567Z",  # quá 6 digits fraction (7 digits)
        "2026-10-29T21:18:00+07:00:30",  # timezone có seconds
        "2026-10-29T21:18:00+0700",       # thiếu colon trong timezone
        "2026-10-29T21:18:00+07",         # thiếu minutes trong timezone
        # Bổ sung các negative edge cases:
        "2026-10-29T21:18:00.",           # trailing dot, 0 fraction digits
        "2026-10-29T21:18:00+07:60",      # offset minutes 60 out of range
        "2026-10-29T21:18:00+24:00",      # offset hours 24 out of range
        "2026-10-29T21:18:00-24:00",      # offset hours -24 out of range
        # Ngày hoặc giờ không có thật trong lịch (calendar validation)
        "2026-02-31T20:30:00Z",
        "2026-04-31T20:30:00Z",
        "2026-10-29T25:00:00Z",
    )
    for bad_ts in bad_timestamps:
        # Direct constructor phải reject
        with pytest.raises(ValueError, match="ERR_INVALID_TIMESTAMP|ERR_NAIVE_TIMESTAMP"):
            AppOwnedApprovalRecord(
                approval_id="appr_01",
                proposal_id=proposal.proposal_id,
                proposal_revision=valid_rev,
                approved_at_iso=bad_ts,
            )
        # Factory cũng phải reject
        with pytest.raises(ValueError, match="ERR_INVALID_TIMESTAMP|ERR_NAIVE_TIMESTAMP"):
            create_app_owned_approval_record(
                proposal,
                approval_id="appr_01",
                approved_at_iso=bad_ts,
            )

    # 2. Các dạng ISO timestamp hợp lệ có timezone
    valid_timestamps = (
        "2026-10-29T20:30:00Z",
        "2026-10-29T20:30:00+07:00",
        "2026-10-29T20:30:00-05:00",
        "2026-10-29T20:30:00.1Z",
        "2026-10-29T20:30:00.123456Z",
        "2026-10-29T20:30:00.123456+07:00",
        "2026-10-29T20:30:00.123456-05:00",
    )
    for vt in valid_timestamps:
        rec = AppOwnedApprovalRecord(
            approval_id="appr_01",
            proposal_id=proposal.proposal_id,
            proposal_revision=valid_rev,
            approved_at_iso=vt,
        )
        assert rec.approved_at_iso == vt


def test_export_revalidation_and_binding_fail_closed_on_corrupt_timestamp():
    """Kiểm tra export revalidation và verify binding fail-closed khi timestamp bị corrupt."""
    proposal = _make_valid_proposal()
    approval = create_app_owned_approval_record(
        proposal,
        approval_id="appr_01",
        approved_at_iso="2026-10-29T20:30:00Z",
    )

    bad_probe_timestamps = (
        "2026-10-29T21:18:00.1234567Z",
        "2026-10-29T21:18:00+07:00:30",
        "2026-10-29T21:18:00+0700",
        "2026-10-29T21:18:00+07",
        "2026-10-29T21:18:00.",
        "2026-10-29T21:18:00+07:60",
    )
    for bad_ts in bad_probe_timestamps:
        # Giả lập corrupt attribute trên frozen instance qua object.__setattr__
        corrupt_rec = dataclasses.replace(approval)
        object.__setattr__(corrupt_rec, "approved_at_iso", bad_ts)

        # 1. approval_record_to_dict phải chặn và raise ValueError
        with pytest.raises(ValueError, match="ERR_INVALID_TIMESTAMP"):
            approval_record_to_dict(corrupt_rec)

        # 2. verify_proposal_approval_binding phải trả về False an toàn (fail-closed)
        assert verify_proposal_approval_binding(proposal, corrupt_rec) is False


def test_binding_persists_across_valid_alternative_approval_metadata():
    """Thay đổi approval_id hoặc approved_at_iso hợp lệ vẫn giữ binding True nếu nội dung proposal không đổi."""
    proposal = _make_valid_proposal()
    approval1 = create_app_owned_approval_record(
        proposal,
        approval_id="appr_first",
        approved_at_iso="2026-10-29T20:30:00Z",
    )
    assert verify_proposal_approval_binding(proposal, approval1) is True

    # Tạo bản ghi approval thứ hai với ID và thời gian khác nhưng cho cùng proposal
    approval2 = create_app_owned_approval_record(
        proposal,
        approval_id="appr_second",
        approved_at_iso="2026-10-29T22:45:00+07:00",
    )
    assert verify_proposal_approval_binding(proposal, approval2) is True


def test_accepted_omission_ids_validation_and_precheck():
    proposal = _make_valid_proposal()
    valid_rev = compute_proposal_revision(proposal)
    valid_time = "2026-10-29T20:30:00Z"

    # 1. Sai kiểu sequence
    for bad_seq in (123, "not_a_list", {"a": 1}, False):
        with pytest.raises(ValueError, match="ERR_INVALID_SEQUENCE"):
            AppOwnedApprovalRecord(
                approval_id="appr_01",
                proposal_id=proposal.proposal_id,
                proposal_revision=valid_rev,
                approved_at_iso=valid_time,
                accepted_omission_ids=bad_seq,  # type: ignore
            )

    # 2. Phần tử sai kiểu hoặc blank
    for bad_item in (123, None, "", "   ", "bad\0id"):
        with pytest.raises(ValueError):
            AppOwnedApprovalRecord(
                approval_id="appr_01",
                proposal_id=proposal.proposal_id,
                proposal_revision=valid_rev,
                approved_at_iso=valid_time,
                accepted_omission_ids=(bad_item,),  # type: ignore
            )

    # 3. Trùng lặp phần tử
    with pytest.raises(ValueError, match="ERR_DUPLICATE_OMISSION_ID"):
        AppOwnedApprovalRecord(
            approval_id="appr_01",
            proposal_id=proposal.proposal_id,
            proposal_revision=valid_rev,
            approved_at_iso=valid_time,
            accepted_omission_ids=("dem_01", "dem_01"),
        )

    # 4. Equality: đúng 128 omission IDs
    eq_ids = tuple(f"dem_{i:04d}" for i in range(MAX_PROPOSED_OMISSIONS))
    rec_eq = AppOwnedApprovalRecord(
        approval_id="appr_01",
        proposal_id=proposal.proposal_id,
        proposal_revision=valid_rev,
        approved_at_iso=valid_time,
        accepted_omission_ids=eq_ids,
    )
    assert len(rec_eq.accepted_omission_ids) == MAX_PROPOSED_OMISSIONS

    # 5. Overflow: 129 omission IDs với 0 children iterations
    overflow_ids = CountedList([f"dem_{i:04d}" for i in range(MAX_PROPOSED_OMISSIONS + 1)])
    with pytest.raises(ValueError, match="ERR_COUNT_OVERFLOW"):
        AppOwnedApprovalRecord(
            approval_id="appr_01",
            proposal_id=proposal.proposal_id,
            proposal_revision=valid_rev,
            approved_at_iso=valid_time,
            accepted_omission_ids=overflow_ids,
        )
    assert overflow_ids.iterations == 0


# ==============================================================================
# 3. EXACT BYTE BUDGET & OVERFLOW TESTS
# ==============================================================================

def test_approval_record_byte_budget_equality_accepted_overflow_rejected():
    proposal = _make_valid_proposal()
    valid_rev = compute_proposal_revision(proposal)
    valid_time = "2026-10-29T20:30:00Z"

    base_rec = AppOwnedApprovalRecord(
        approval_id="appr_01",
        proposal_id=proposal.proposal_id,
        proposal_revision=valid_rev,
        approved_at_iso=valid_time,
    )
    base_dict = approval_record_to_dict(base_rec)
    base_bytes = json.dumps(
        base_dict, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    base_len = len(base_bytes)

    # Điều chỉnh độ dài approval_id để đạt đúng chính xác MAX_APPROVAL_RECORD_BYTES (528,384 bytes)
    target_len = MAX_APPROVAL_RECORD_BYTES  # 528384
    delta = target_len - base_len
    padded_id = "A" * (len("appr_01") + delta)

    rec_padded = AppOwnedApprovalRecord(
        approval_id=padded_id,
        proposal_id=proposal.proposal_id,
        proposal_revision=valid_rev,
        approved_at_iso=valid_time,
    )
    padded_bytes = json.dumps(
        approval_record_to_dict(rec_padded),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    assert len(padded_bytes) == MAX_APPROVAL_RECORD_BYTES

    # Thêm 1 byte UTF-8 (+1 overflow) -> Phải bị từ chối
    with pytest.raises(ValueError, match="ERR_BYTE_BUDGET_OVERFLOW"):
        AppOwnedApprovalRecord(
            approval_id=padded_id + "X",
            proposal_id=proposal.proposal_id,
            proposal_revision=valid_rev,
            approved_at_iso=valid_time,
        )


# ==============================================================================
# 4. BINDING VERIFICATION & STALE INVALIDATION TESTS
# ==============================================================================

def test_verify_proposal_approval_binding_success():
    proposal = _make_valid_proposal()
    approval = create_app_owned_approval_record(
        proposal,
        approval_id="appr_test_01",
        approved_at_iso="2026-10-29T20:30:00Z",
    )
    assert verify_proposal_approval_binding(proposal, approval) is True


def test_verify_proposal_approval_binding_stale_on_any_content_change():
    """Bất kỳ thay đổi nội dung nào trong snapshot cũng làm mất hiệu lực approval (False)."""
    base_prop = _make_valid_proposal()
    approval = create_app_owned_approval_record(
        base_prop,
        approval_id="appr_test_01",
        approved_at_iso="2026-10-29T20:30:00Z",
    )

    # 1. Sửa brief trong creative_context
    d_brief = _make_valid_proposal_dict()
    d_brief["creative_context"] = dataclasses.replace(
        d_brief["creative_context"], free_brief="Sửa đổi brief kịch bản"
    )
    assert verify_proposal_approval_binding(normalize_edit_plan_proposal(d_brief), approval) is False

    # 2. Sửa style_preset
    d_preset = _make_valid_proposal_dict()
    d_preset["creative_context"] = dataclasses.replace(
        d_preset["creative_context"], style_preset="vlog_khac"
    )
    assert verify_proposal_approval_binding(normalize_edit_plan_proposal(d_preset), approval) is False

    # 3. Sửa rules
    d_rules = _make_valid_proposal_dict()
    d_rules["creative_context"] = dataclasses.replace(
        d_rules["creative_context"], must_keep_rules=("Quy tắc mới",)
    )
    assert verify_proposal_approval_binding(normalize_edit_plan_proposal(d_rules), approval) is False

    # 4. Sửa timeline segment điểm cắt
    d_cut = _make_valid_proposal_dict()
    d_cut["video_plan"]["timeline_segments"][0]["end_sec"] = 4.0
    assert verify_proposal_approval_binding(normalize_edit_plan_proposal(d_cut), approval) is False

    # 5. Sửa hook title
    d_hook = _make_valid_proposal_dict()
    d_hook["video_plan"]["global_hook"]["hook_title"] = "Hook khác"
    assert verify_proposal_approval_binding(normalize_edit_plan_proposal(d_hook), approval) is False

    # 6. Sửa Slot 2 kinetic word
    d_kinetic = _make_valid_proposal_dict()
    d_kinetic["kinetic_plan"]["lines"][0]["words"][0]["word"] = "CHÀO"
    assert verify_proposal_approval_binding(normalize_edit_plan_proposal(d_kinetic), approval) is False

    # 7. Sửa asset assignment
    d_assign = _make_valid_proposal_dict()
    d_assign["proposed_asset_assignments"][0]["asset_id"] = "asset_different"
    assert verify_proposal_approval_binding(normalize_edit_plan_proposal(d_assign), approval) is False


def test_verify_proposal_approval_binding_omission_mismatch():
    """Phát hiện sai lệch danh sách omission: đổi ID, thêm/bớt ID, sai thứ tự."""
    raw_dict = _make_valid_proposal_dict()
    raw_dict["proposed_omissions"] = [
        {"demand_id": "omit_01", "slot": "broll", "timeline_sec": 1.0, "duration_sec": 1.0, "reason": "R", "warning_message": "W"},
        {"demand_id": "omit_02", "slot": "sfx", "timeline_sec": 2.0, "duration_sec": 1.0, "reason": "R", "warning_message": "W"},
    ]
    proposal = normalize_edit_plan_proposal(raw_dict)
    valid_rev = compute_proposal_revision(proposal)

    # 1. Thay omission khác cùng số lượng
    rec_different = AppOwnedApprovalRecord(
        approval_id="appr_01",
        proposal_id=proposal.proposal_id,
        proposal_revision=valid_rev,
        approved_at_iso="2026-10-29T20:30:00Z",
        accepted_omission_ids=("omit_01", "omit_DIFF"),
    )
    assert verify_proposal_approval_binding(proposal, rec_different) is False

    # 2. Đảo ngược thứ tự omissions
    rec_reordered = AppOwnedApprovalRecord(
        approval_id="appr_01",
        proposal_id=proposal.proposal_id,
        proposal_revision=valid_rev,
        approved_at_iso="2026-10-29T20:30:00Z",
        accepted_omission_ids=("omit_02", "omit_01"),
    )
    assert verify_proposal_approval_binding(proposal, rec_reordered) is False

    # 3. Thiếu omission ID
    rec_missing = AppOwnedApprovalRecord(
        approval_id="appr_01",
        proposal_id=proposal.proposal_id,
        proposal_revision=valid_rev,
        approved_at_iso="2026-10-29T20:30:00Z",
        accepted_omission_ids=("omit_01",),
    )
    assert verify_proposal_approval_binding(proposal, rec_missing) is False

    # 4. Thừa foreign omission ID
    rec_extra = AppOwnedApprovalRecord(
        approval_id="appr_01",
        proposal_id=proposal.proposal_id,
        proposal_revision=valid_rev,
        approved_at_iso="2026-10-29T20:30:00Z",
        accepted_omission_ids=("omit_01", "omit_02", "omit_foreign"),
    )
    assert verify_proposal_approval_binding(proposal, rec_extra) is False


def test_verify_proposal_approval_binding_safe_fail_closed():
    """Kiểm tra fail-closed an toàn: trả về False có kiểm soát, không rò rỉ exception."""
    proposal = _make_valid_proposal()
    approval = create_app_owned_approval_record(
        proposal,
        approval_id="appr_test_01",
        approved_at_iso="2026-10-29T20:30:00Z",
    )

    # 1. Sai kiểu nominal (truyền dict, str, None)
    assert verify_proposal_approval_binding(_make_valid_proposal_dict(), approval) is False  # type: ignore
    assert verify_proposal_approval_binding(proposal, approval_record_to_dict(approval)) is False  # type: ignore
    assert verify_proposal_approval_binding(None, approval) is False  # type: ignore
    assert verify_proposal_approval_binding(proposal, None) is False  # type: ignore

    # 2. Sai proposal_id
    rec_wrong_pid = AppOwnedApprovalRecord(
        approval_id=approval.approval_id,
        proposal_id="prop_different",
        proposal_revision=approval.proposal_revision,
        approved_at_iso=approval.approved_at_iso,
        accepted_omission_ids=approval.accepted_omission_ids,
    )
    assert verify_proposal_approval_binding(proposal, rec_wrong_pid) is False


# ==============================================================================
# 5. PURITY SPIES (ZERO FILESYSTEM IO, ZERO NETWORK, ZERO SYSTEM CLOCK)
# ==============================================================================

def test_purity_zero_io_network_and_clock(monkeypatch):
    io_called = []
    net_called = []
    clock_called = []

    real_open = open
    real_stat = os.stat
    real_socket = socket.socket
    real_now = datetime.now
    real_time = time.time

    def fake_open(*args, **kwargs):
        io_called.append(("open", args))
        return real_open(*args, **kwargs)

    def fake_stat(*args, **kwargs):
        io_called.append(("stat", args))
        return real_stat(*args, **kwargs)

    def fake_socket(*args, **kwargs):
        net_called.append(("socket", args))
        return real_socket(*args, **kwargs)

    import src.core.contracts.edit_plan_approval_contracts as app_mod

    class SpyDatetime:
        @classmethod
        def fromisoformat(cls, *args, **kwargs):
            return datetime.fromisoformat(*args, **kwargs)

        @classmethod
        def now(cls, *args, **kwargs):
            clock_called.append(("datetime.now", args))
            return real_now(*args, **kwargs)

    def fake_time():
        clock_called.append(("time.time", ()))
        return real_time()

    monkeypatch.setattr("builtins.open", fake_open)
    monkeypatch.setattr("os.stat", fake_stat)
    monkeypatch.setattr("socket.socket", fake_socket)
    monkeypatch.setattr(app_mod, "datetime", SpyDatetime)
    monkeypatch.setattr("time.time", fake_time)

    proposal = _make_valid_proposal()
    approval = create_app_owned_approval_record(
        proposal,
        approval_id="appr_pure_01",
        approved_at_iso="2026-10-29T20:30:00Z",
    )
    _ = approval_record_to_dict(approval)
    _ = verify_proposal_approval_binding(proposal, approval)

    assert io_called == [], f"Phát hiện filesystem IO trái phép: {io_called}"
    assert net_called == [], f"Phát hiện network calls trái phép: {net_called}"
    assert clock_called == [], f"Phát hiện system clock calls trái phép: {clock_called}"
