"""Unit tests cho Hợp đồng Snapshot Bản đề xuất kịch bản (EditPlanProposal) — G06-06C1b1-I.

Kiểm thử toàn diện:
- Cấu trúc frozen DTO EditPlanProposal với đúng 8 fields chuẩn tắc.
- Ba public helpers: normalize_edit_plan_proposal, edit_plan_proposal_to_dict, compute_proposal_revision.
- Tính bất biến (frozen, MappingProxyType, tuple), defensive copies và roundtrip hoàn hảo.
- Bảo toàn trọn vẹn metadata Slot 1 (global_hook, broll/sfx inserts), Slot 2 (kinetic pages/words) và source_binding.
- Ranh giới kiểm tra khóa root (chặn user_approved, approval_record, unknown keys an toàn với mixed keys).
- Kiểm tra tính nhất quán manifest_id giữa root, source_binding, video_plan, kinetic_plan.
- Ranh giới clip_id: timeline_segments phải tham chiếu clip_id có trong source_binding.clips.
- Ranh giới thời gian: start_sec >= 0, start_sec < end_sec, speed > 0, chặn bool/NaN/Inf/bigint.
- Domain gates cho assignments và omissions: slot chỉ broll/sfx, source_kind hợp lệ, origin_library_id bắt buộc cho managed/linked.
- Tính duy nhất và tách biệt của demand_id: không trùng trong list và không giao thoa giữa assignments và omissions.
- Cách ly tuyệt đối tài liệu tham khảo (reference_only) khỏi source clips, timeline segments và asset assignments.
- Giới hạn bộ đếm (pre-check len 0 iterations), giới hạn độ sâu JSON (depth <= 32), giới hạn số node (<= 100,000), DAG hợp lệ vs phát hiện vòng lặp (cycle).
- Giới hạn dung lượng UTF-8 (MAX_PROPOSAL_SNAPSHOT_BYTES = 524,288): equality accepted, +1 rejected.
- Purity check: zero filesystem IO (open, stat) và zero network calls (socket).
"""

from collections.abc import Mapping
import hashlib
import json
import math
import os
import socket
from types import MappingProxyType
import pytest

from src.core.contracts.edit_plan_contracts import (
    CreativeContext,
    ReferenceItem,
)
from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    MAX_PROPOSAL_SNAPSHOT_BYTES,
    MAX_RAW_INSERTS,
    MAX_SOURCE_CLIPS,
    MAX_TIMELINE_SEGMENTS,
    MAX_PROPOSED_ASSIGNMENTS,
    MAX_PROPOSED_OMISSIONS,
    MAX_JSON_DEPTH,
    MAX_JSON_NODES,
    compute_proposal_revision,
    edit_plan_proposal_to_dict,
    normalize_edit_plan_proposal,
)


def _make_valid_creative_context() -> CreativeContext:
    return CreativeContext(
        free_brief="Video review 60s ẩm thực vui tươi",
        style_preset="cute_vlog",
        target_duration_sec=60.0,
        must_keep_rules=("Giữ đoạn mở đầu",),
        must_not_rules=("cấm meme phản cảm",),
        reference_items=(
            ReferenceItem(
                reference_id="ref_01",
                local_path="/media/refs/sample_ref.mp4",
                media_type="video",
                user_note="Học nhịp dựng nhanh",
            ),
        ),
        reference_urls=("https://example.com/ref_video",),
    )


def _make_valid_source_binding(manifest_id: str = "m_test01") -> dict[str, object]:
    return {
        "manifest_id": manifest_id,
        "clips": [
            {
                "clip_id": "C01",
                "file_name": "intro.mp4",
                "current_path": "/media/footage/intro.mp4",
                "content_hash": "hash_c01_abc123",
                "duration_sec": 10.5,
                "fps": 30.0,
            },
            {
                "clip_id": "C02",
                "file_name": "body.mp4",
                "current_path": "/media/footage/body.mp4",
                "content_hash": "hash_c02_def456",
                "duration_sec": 25.0,
                "fps": 30.0,
            },
        ],
    }


def _make_valid_video_plan(manifest_id: str = "m_test01") -> dict[str, object]:
    return {
        "manifest_id": manifest_id,
        "strategy_summary": "Chiến lược dựng 60s ẩm thực",
        "target_platform": "TikTok / Shorts",
        "global_hook": {
            "clip_id": "C01",
            "start_sec": 0.0,
            "end_sec": 3.0,
            "hook_title": "Món ăn độc lạ",
        },
        "timeline_segments": [
            {
                "segment_index": 0,
                "clip_id": "C01",
                "start_sec": 0.0,
                "end_sec": 5.0,
                "action": "keep",
                "speed": 1.0,
                "role": "hook",
                "note": "Giữ câu chào",
            },
            {
                "segment_index": 1,
                "clip_id": "C02",
                "start_sec": 2.5,
                "end_sec": 12.0,
                "action": "keep",
                "speed": 1.2,
                "role": "dialogue",
                "note": "Nấu ăn",
            },
        ],
        "broll_inserts": [
            {
                "timeline_sec": 2.0,
                "duration_sec": 2.0,
                "asset_file": "broll_cake.mp4",
                "description": "Cảnh quay cận bánh",
            }
        ],
        "sfx_inserts": [],
    }


def _make_valid_kinetic_plan(manifest_id: str = "m_test01") -> dict[str, object]:
    return {
        "manifest_id": manifest_id,
        "preset_style": "karaoke_pop",
        "lines": [
            {
                "line_index": 0,
                "timeline_sec": 0.5,
                "duration_sec": 2.5,
                "text": "XIN CHÀO MỌI NGƯỜI",
                "words": [
                    {"word": "XIN", "start_sec": 0.5, "end_sec": 0.8},
                    {"word": "CHÀO", "start_sec": 0.8, "end_sec": 1.2},
                ],
            }
        ],
    }


def _make_valid_proposal_dict(manifest_id: str = "m_test01") -> dict[str, object]:
    return {
        "proposal_id": "prop_20261029_001",
        "manifest_id": manifest_id,
        "creative_context": _make_valid_creative_context(),
        "source_binding": _make_valid_source_binding(manifest_id),
        "video_plan": _make_valid_video_plan(manifest_id),
        "kinetic_plan": _make_valid_kinetic_plan(manifest_id),
        "proposed_asset_assignments": [
            {
                "slot": "sfx",
                "demand_id": "dem_sfx_01",
                "timeline_sec": 0.5,
                "duration_sec": 1.0,
                "asset_id": "sfx_pop_cute",
                "source_kind": "managed",
                "origin_library_id": "lib_default_sfx",
                "pack_id": "pack_vlog_01",
                "status": "PROPOSED",
            }
        ],
        "proposed_omissions": [
            {
                "slot": "broll",
                "demand_id": "dem_broll_01",
                "timeline_sec": 2.0,
                "duration_sec": 2.0,
                "reason": "ERR_CATALOG_EMPTY",
                "warning_message": "Không tìm thấy video bánh nướng trong kho",
            }
        ],
    }


class CountedList(list):
    """List đếm số phần tử được duyệt để kiểm tra pre-check len trước lặp."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.iterations = 0

    def __iter__(self):
        for item in super().__iter__():
            self.iterations += 1
            yield item


class CountedMapping(dict):
    """Mapping đếm số phần tử được duyệt qua items() để kiểm tra pre-check len trước lặp."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.iterations = 0

    def items(self):
        for k, v in super().items():
            self.iterations += 1
            yield k, v


# ==============================================================================
# 1. POSITIVE CONTROL & ROUNDTRIP TESTS
# ==============================================================================

def test_successful_proposal_creation_and_roundtrip():
    raw_dict = _make_valid_proposal_dict()
    proposal = normalize_edit_plan_proposal(raw_dict)

    assert isinstance(proposal, EditPlanProposal)
    assert proposal.proposal_id == "prop_20261029_001"
    assert proposal.manifest_id == "m_test01"
    assert isinstance(proposal.source_binding, MappingProxyType)
    assert isinstance(proposal.video_plan, MappingProxyType)
    assert isinstance(proposal.kinetic_plan, MappingProxyType)
    assert isinstance(proposal.proposed_asset_assignments, tuple)
    assert isinstance(proposal.proposed_omissions, tuple)

    # Kiểm tra bảo toàn dữ liệu mở rộng Slot 1 và Slot 2
    assert proposal.video_plan["global_hook"]["hook_title"] == "Món ăn độc lạ"
    assert len(proposal.video_plan["broll_inserts"]) == 1
    assert proposal.kinetic_plan["lines"][0]["words"][0]["word"] == "XIN"

    # Export roundtrip
    exported_dict = edit_plan_proposal_to_dict(proposal)
    assert isinstance(exported_dict, dict)
    assert len(exported_dict) == 8  # Đúng 8 fields
    assert "proposal_revision" not in exported_dict  # Revision không tự nhúng vào export

    # Tái chuẩn hóa từ export phải sinh instance tương đương
    reloaded = normalize_edit_plan_proposal(exported_dict)
    assert reloaded == proposal

    # Revision SHA-256
    rev1 = compute_proposal_revision(proposal)
    rev2 = compute_proposal_revision(reloaded)
    assert isinstance(rev1, str)
    assert len(rev1) == 64
    assert rev1 == rev2


def test_kinetic_plan_optional_none_accepted():
    raw_dict = _make_valid_proposal_dict()
    raw_dict["kinetic_plan"] = None
    proposal = normalize_edit_plan_proposal(raw_dict)
    assert proposal.kinetic_plan is None

    exported = edit_plan_proposal_to_dict(proposal)
    assert exported["kinetic_plan"] is None
    reloaded = normalize_edit_plan_proposal(exported)
    assert reloaded.kinetic_plan is None


def test_recomputes_digest_when_any_content_changes():
    raw_dict = _make_valid_proposal_dict()
    base_prop = normalize_edit_plan_proposal(raw_dict)
    base_rev = compute_proposal_revision(base_prop)

    # 1. Thay đổi proposal_id
    d1 = _make_valid_proposal_dict()
    d1["proposal_id"] = "prop_different_id"
    assert compute_proposal_revision(normalize_edit_plan_proposal(d1)) != base_rev

    # 2. Thay đổi brief trong CreativeContext
    d2 = _make_valid_proposal_dict()
    d2["creative_context"] = CreativeContext(free_brief="Brief đã thay đổi")
    assert compute_proposal_revision(normalize_edit_plan_proposal(d2)) != base_rev

    # 3. Thay đổi đường dẫn tệp trong source_binding (dù manifest_id không đổi)
    d3 = _make_valid_proposal_dict()
    d3["source_binding"]["clips"][0]["current_path"] = "/new/path/intro.mp4"
    assert compute_proposal_revision(normalize_edit_plan_proposal(d3)) != base_rev

    # 4. Thay đổi điểm cắt trong timeline_segments
    d4 = _make_valid_proposal_dict()
    d4["video_plan"]["timeline_segments"][0]["start_sec"] = 0.5
    assert compute_proposal_revision(normalize_edit_plan_proposal(d4)) != base_rev

    # 5. Thay đổi hook_title trong global_hook
    d5 = _make_valid_proposal_dict()
    d5["video_plan"]["global_hook"]["hook_title"] = "Tiêu đề hook mới"
    assert compute_proposal_revision(normalize_edit_plan_proposal(d5)) != base_rev

    # 6. Thay đổi kinetic_plan text
    d6 = _make_valid_proposal_dict()
    d6["kinetic_plan"]["lines"][0]["text"] = "TEXT MỚI"
    assert compute_proposal_revision(normalize_edit_plan_proposal(d6)) != base_rev

    # 7. Thay đổi asset_id trong proposed_asset_assignments
    d7 = _make_valid_proposal_dict()
    d7["proposed_asset_assignments"][0]["asset_id"] = "sfx_different"
    assert compute_proposal_revision(normalize_edit_plan_proposal(d7)) != base_rev

    # 8. Thay đổi reason trong proposed_omissions
    d8 = _make_valid_proposal_dict()
    d8["proposed_omissions"][0]["reason"] = "ERR_LOW_CONFIDENCE"
    assert compute_proposal_revision(normalize_edit_plan_proposal(d8)) != base_rev


# ==============================================================================
# 2. ROOT KEYS & AI FORGERY REJECTION TESTS
# ==============================================================================

def test_rejects_unknown_root_keys_and_ai_forgery():
    raw_dict = _make_valid_proposal_dict()
    raw_dict["user_approved"] = True  # AI cố tình tự nhận phê duyệt
    with pytest.raises(ValueError, match="ERR_UNKNOWN_KEY"):
        normalize_edit_plan_proposal(raw_dict)

    raw_dict = _make_valid_proposal_dict()
    raw_dict["approval_record"] = {"approved": True}
    with pytest.raises(ValueError, match="ERR_UNKNOWN_KEY"):
        normalize_edit_plan_proposal(raw_dict)

    raw_dict = _make_valid_proposal_dict()
    raw_dict["proposal_revision"] = "fake_revision_token"
    with pytest.raises(ValueError, match="ERR_UNKNOWN_KEY"):
        normalize_edit_plan_proposal(raw_dict)


def test_mixed_unknown_keys_type_safe():
    raw_dict = _make_valid_proposal_dict()
    raw_dict[1] = "bad_int_key"
    raw_dict["extra_str_key"] = "bad"
    with pytest.raises(ValueError, match="ERR_UNKNOWN_KEY"):
        normalize_edit_plan_proposal(raw_dict)


def test_missing_mandatory_root_field():
    raw_dict = _make_valid_proposal_dict()
    del raw_dict["video_plan"]
    with pytest.raises(ValueError, match="ERR_MISSING_FIELD"):
        normalize_edit_plan_proposal(raw_dict)


# ==============================================================================
# 3. OWNERSHIP, IMMUTABILITY & DEFENSIVE COPIES
# ==============================================================================

def test_ownership_and_defensive_copies():
    raw_dict = _make_valid_proposal_dict()
    proposal = normalize_edit_plan_proposal(raw_dict)

    # 1. Mutate raw input sau khi normalize không ảnh hưởng proposal
    raw_dict["source_binding"]["clips"][0]["clip_id"] = "MUTATED"
    raw_dict["video_plan"]["timeline_segments"][0]["speed"] = 99.0
    raw_dict["proposed_asset_assignments"][0]["asset_id"] = "MUTATED_SFX"

    assert proposal.source_binding["clips"][0]["clip_id"] == "C01"
    assert proposal.video_plan["timeline_segments"][0]["speed"] == 1.0
    assert proposal.proposed_asset_assignments[0]["asset_id"] == "sfx_pop_cute"

    # 2. Thuộc tính là MappingProxyType, không thể gán đè trực tiếp
    with pytest.raises(TypeError):
        proposal.video_plan["new_key"] = 123  # type: ignore

    with pytest.raises(TypeError):
        proposal.source_binding["clips"][0]["new_key"] = 123  # type: ignore

    # 3. Export trả về dict có thể mutate độc lập
    exported = edit_plan_proposal_to_dict(proposal)
    exported["video_plan"]["timeline_segments"][0]["speed"] = 88.0
    assert proposal.video_plan["timeline_segments"][0]["speed"] == 1.0


def test_direct_dto_instantiation_parity():
    # Direct DTO instantiation cũng phải chạy revalidation tương tự helper
    valid_dict = _make_valid_proposal_dict()
    prop1 = normalize_edit_plan_proposal(valid_dict)

    # Khởi tạo trực tiếp bằng constructor
    prop2 = EditPlanProposal(
        proposal_id=prop1.proposal_id,
        manifest_id=prop1.manifest_id,
        creative_context=prop1.creative_context,
        source_binding=prop1.source_binding,
        video_plan=prop1.video_plan,
        kinetic_plan=prop1.kinetic_plan,
        proposed_asset_assignments=prop1.proposed_asset_assignments,
        proposed_omissions=prop1.proposed_omissions,
    )
    assert prop1 == prop2
    assert compute_proposal_revision(prop1) == compute_proposal_revision(prop2)


# ==============================================================================
# 4. DAG VS CYCLE DETECTION & TREE BOUNDS
# ==============================================================================

def test_dag_sharing_nodes_allowed_cycles_rejected():
    shared_node = {"title": "Shared Info", "code": 100}
    raw_dict = _make_valid_proposal_dict()
    # Gắn cùng một node vào 2 vị trí khác nhau trong tree (DAG hợp lệ)
    raw_dict["video_plan"]["timeline_segments"][0]["meta"] = shared_node
    raw_dict["video_plan"]["timeline_segments"][1]["meta"] = shared_node

    # DAG không chu trình phải được chấp nhận và clone độc lập
    prop = normalize_edit_plan_proposal(raw_dict)
    assert prop.video_plan["timeline_segments"][0]["meta"]["code"] == 100
    assert prop.video_plan["timeline_segments"][1]["meta"]["code"] == 100

    # Tạo cycle thực sự: a["child"] = a
    cycle_dict = {"title": "Cycle"}
    cycle_dict["child"] = cycle_dict
    raw_with_cycle = _make_valid_proposal_dict()
    raw_with_cycle["video_plan"]["timeline_segments"][0]["meta"] = cycle_dict

    with pytest.raises(ValueError, match="ERR_CYCLE_DETECTED"):
        normalize_edit_plan_proposal(raw_with_cycle)


def test_max_depth_exceeded():
    # Tạo cây lồng nhau sâu 35 tầng (> 32)
    deep_tree = {"val": 1}
    for _ in range(35):
        deep_tree = {"nested": deep_tree}

    raw_dict = _make_valid_proposal_dict()
    raw_dict["video_plan"]["deep_info"] = deep_tree

    with pytest.raises(ValueError, match="ERR_JSON_DEPTH_EXCEEDED"):
        normalize_edit_plan_proposal(raw_dict)


# ==============================================================================
# 5. MANIFEST ID CONSISTENCY TESTS
# ==============================================================================

def test_manifest_id_mismatch_rejected():
    # 1. source_binding lệch manifest_id
    d1 = _make_valid_proposal_dict("m_root")
    d1["source_binding"]["manifest_id"] = "m_other"
    with pytest.raises(ValueError, match="ERR_MANIFEST_MISMATCH"):
        normalize_edit_plan_proposal(d1)

    # 2. video_plan lệch manifest_id
    d2 = _make_valid_proposal_dict("m_root")
    d2["video_plan"]["manifest_id"] = "m_other"
    with pytest.raises(ValueError, match="ERR_MANIFEST_MISMATCH"):
        normalize_edit_plan_proposal(d2)

    # 3. kinetic_plan lệch manifest_id
    d3 = _make_valid_proposal_dict("m_root")
    d3["kinetic_plan"]["manifest_id"] = "m_other"
    with pytest.raises(ValueError, match="ERR_MANIFEST_MISMATCH"):
        normalize_edit_plan_proposal(d3)


# ==============================================================================
# 6. TIMELINE SEGMENTS & SOURCE CLIPS VALIDATION
# ==============================================================================

def test_unknown_clip_id_in_segments_rejected():
    raw_dict = _make_valid_proposal_dict()
    # C99 không có trong source_binding.clips (chỉ có C01, C02)
    raw_dict["video_plan"]["timeline_segments"][0]["clip_id"] = "C99"
    with pytest.raises(ValueError, match="ERR_UNKNOWN_CLIP_ID"):
        normalize_edit_plan_proposal(raw_dict)


def test_duplicate_clip_id_in_source_binding_rejected():
    raw_dict = _make_valid_proposal_dict()
    raw_dict["source_binding"]["clips"].append({
        "clip_id": "C01",  # Trùng lặp C01
        "file_name": "duplicate.mp4",
        "current_path": "/media/duplicate.mp4",
    })
    with pytest.raises(ValueError, match="ERR_DUPLICATE_CLIP_ID"):
        normalize_edit_plan_proposal(raw_dict)


def test_timeline_segments_bounds_validation():
    # start_sec < 0
    d1 = _make_valid_proposal_dict()
    d1["video_plan"]["timeline_segments"][0]["start_sec"] = -1.0
    with pytest.raises(ValueError, match="ERR_INVALID_TIMELINE_BOUNDS"):
        normalize_edit_plan_proposal(d1)

    # start_sec >= end_sec
    d2 = _make_valid_proposal_dict()
    d2["video_plan"]["timeline_segments"][0]["start_sec"] = 5.0
    d2["video_plan"]["timeline_segments"][0]["end_sec"] = 5.0
    with pytest.raises(ValueError, match="ERR_INVALID_TIMELINE_BOUNDS"):
        normalize_edit_plan_proposal(d2)

    # speed <= 0
    d3 = _make_valid_proposal_dict()
    d3["video_plan"]["timeline_segments"][0]["speed"] = 0.0
    with pytest.raises(ValueError, match="ERR_INVALID_SPEED"):
        normalize_edit_plan_proposal(d3)

    # Boolean coercion rejected
    d4 = _make_valid_proposal_dict()
    d4["video_plan"]["timeline_segments"][0]["start_sec"] = True
    with pytest.raises(ValueError, match="ERR_INVALID_NUMBER"):
        normalize_edit_plan_proposal(d4)

    # NaN / Inf rejected
    d5 = _make_valid_proposal_dict()
    d5["video_plan"]["timeline_segments"][0]["start_sec"] = float("nan")
    with pytest.raises(ValueError, match="ERR_INVALID_NUMBER"):
        normalize_edit_plan_proposal(d5)


def test_empty_timeline_segments_rejected():
    raw_dict = _make_valid_proposal_dict()
    raw_dict["video_plan"]["timeline_segments"] = []
    with pytest.raises(ValueError, match="ERR_EMPTY_TIMELINE_SEGMENTS"):
        normalize_edit_plan_proposal(raw_dict)


# ==============================================================================
# 7. PROPOSED ASSET ASSIGNMENTS & OMISSIONS DOMAIN GATES
# ==============================================================================

def test_assignment_and_omission_slots_validation():
    # Slot không hợp lệ
    d1 = _make_valid_proposal_dict()
    d1["proposed_asset_assignments"][0]["slot"] = "primary_footage"
    with pytest.raises(ValueError, match="ERR_INVALID_SLOT"):
        normalize_edit_plan_proposal(d1)

    d2 = _make_valid_proposal_dict()
    d2["proposed_omissions"][0]["slot"] = "constraints"
    with pytest.raises(ValueError, match="ERR_INVALID_SLOT"):
        normalize_edit_plan_proposal(d2)


def test_source_kind_and_origin_library_id():
    # source_kind không thuộc enum chuẩn
    d1 = _make_valid_proposal_dict()
    d1["proposed_asset_assignments"][0]["source_kind"] = "user"  # Cấm enum tự bịa
    with pytest.raises(ValueError, match="ERR_INVALID_SOURCE_KIND"):
        normalize_edit_plan_proposal(d1)

    # managed nhưng thiếu origin_library_id
    d2 = _make_valid_proposal_dict()
    d2["proposed_asset_assignments"][0]["source_kind"] = "managed"
    d2["proposed_asset_assignments"][0]["origin_library_id"] = ""
    with pytest.raises(ValueError, match="ERR_MISSING_ORIGIN_LIBRARY_ID"):
        normalize_edit_plan_proposal(d2)

    # builtin không bắt buộc origin_library_id
    d3 = _make_valid_proposal_dict()
    d3["proposed_asset_assignments"][0]["source_kind"] = "builtin"
    del d3["proposed_asset_assignments"][0]["origin_library_id"]
    prop = normalize_edit_plan_proposal(d3)
    assert prop.proposed_asset_assignments[0]["source_kind"] == "builtin"


def test_assignment_status_only_proposed():
    d = _make_valid_proposal_dict()
    d["proposed_asset_assignments"][0]["status"] = "APPROVED"  # Cấm tự xưng approved
    with pytest.raises(ValueError, match="ERR_INVALID_STATUS"):
        normalize_edit_plan_proposal(d)


def test_omission_must_have_reason_and_warning():
    d1 = _make_valid_proposal_dict()
    d1["proposed_omissions"][0]["reason"] = ""
    with pytest.raises(ValueError, match="ERR_BLANK_FIELD"):
        normalize_edit_plan_proposal(d1)

    d2 = _make_valid_proposal_dict()
    del d2["proposed_omissions"][0]["warning_message"]
    with pytest.raises(ValueError, match="ERR_MISSING_FIELD"):
        normalize_edit_plan_proposal(d2)


def test_demand_id_uniqueness_and_disjointness():
    # Trùng lặp demand_id trong assignments
    d1 = _make_valid_proposal_dict()
    d1["proposed_asset_assignments"].append(dict(d1["proposed_asset_assignments"][0]))
    with pytest.raises(ValueError, match="ERR_DUPLICATE_DEMAND_ID"):
        normalize_edit_plan_proposal(d1)

    # Trùng lặp demand_id trong omissions
    d2 = _make_valid_proposal_dict()
    d2["proposed_omissions"].append(dict(d2["proposed_omissions"][0]))
    with pytest.raises(ValueError, match="ERR_DUPLICATE_DEMAND_ID"):
        normalize_edit_plan_proposal(d2)

    # Giao thoa demand_id giữa assignments và omissions (vừa gán vừa bỏ qua)
    d3 = _make_valid_proposal_dict()
    d3["proposed_omissions"][0]["demand_id"] = d3["proposed_asset_assignments"][0]["demand_id"]
    with pytest.raises(ValueError, match="ERR_DEMAND_ASSIGNED_AND_OMITTED"):
        normalize_edit_plan_proposal(d3)


# ==============================================================================
# 8. REFERENCE ISOLATION (D03)
# ==============================================================================

def test_reference_item_cannot_leak_to_footage_or_assignments():
    # 1. reference_id rò rỉ vào source_binding.clips
    d1 = _make_valid_proposal_dict()
    d1["source_binding"]["clips"].append({
        "clip_id": "ref_01",  # Trùng reference_id từ creative_context
        "current_path": "/media/refs/sample_ref.mp4",
    })
    with pytest.raises(ValueError, match="ERR_REFERENCE_LEAK"):
        normalize_edit_plan_proposal(d1)

    # 2. reference_id rò rỉ vào timeline_segments
    d2 = _make_valid_proposal_dict()
    d2["video_plan"]["timeline_segments"][0]["clip_id"] = "ref_01"
    with pytest.raises(ValueError, match="ERR_UNKNOWN_CLIP_ID"):
        normalize_edit_plan_proposal(d2)

    # 3. reference_id rò rỉ vào proposed_asset_assignments
    d3 = _make_valid_proposal_dict()
    d3["proposed_asset_assignments"][0]["asset_id"] = "ref_01"
    with pytest.raises(ValueError, match="ERR_REFERENCE_LEAK"):
        normalize_edit_plan_proposal(d3)


# ==============================================================================
# 9. COUNT BOUNDS & PRE-CHECK BEFORE ITERATION (0 ITERATIONS)
# ==============================================================================

def test_count_bounds_precheck_0_iterations():
    # 1. Overflow timeline_segments
    overflow_segs = CountedList([
        {"clip_id": "C01", "start_sec": 0.0, "end_sec": 1.0}
        for _ in range(MAX_TIMELINE_SEGMENTS + 1)
    ])
    d1 = _make_valid_proposal_dict()
    d1["video_plan"]["timeline_segments"] = overflow_segs
    with pytest.raises(ValueError, match="ERR_COUNT_OVERFLOW"):
        normalize_edit_plan_proposal(d1)
    assert overflow_segs.iterations == 0

    # 2. Overflow source_binding clips
    overflow_clips = CountedList([
        {"clip_id": f"C{i:04d}", "current_path": "/p"}
        for i in range(MAX_SOURCE_CLIPS + 1)
    ])
    d2 = _make_valid_proposal_dict()
    d2["source_binding"]["clips"] = overflow_clips
    with pytest.raises(ValueError, match="ERR_COUNT_OVERFLOW"):
        normalize_edit_plan_proposal(d2)
    assert overflow_clips.iterations == 0

    # 3. Overflow assignments
    overflow_assigns = CountedList([
        {
            "slot": "sfx", "demand_id": f"d_{i}", "timeline_sec": 0.0, "duration_sec": 1.0,
            "asset_id": f"a_{i}", "source_kind": "builtin"
        }
        for i in range(MAX_PROPOSED_ASSIGNMENTS + 1)
    ])
    d3 = _make_valid_proposal_dict()
    d3["proposed_asset_assignments"] = overflow_assigns
    with pytest.raises(ValueError, match="ERR_COUNT_OVERFLOW"):
        normalize_edit_plan_proposal(d3)
    assert overflow_assigns.iterations == 0

    # 4. Overflow omissions
    overflow_omits = CountedList([
        {
            "slot": "broll", "demand_id": f"d_{i}", "timeline_sec": 0.0, "duration_sec": 1.0,
            "reason": "ERR", "warning_message": "msg"
        }
        for i in range(MAX_PROPOSED_OMISSIONS + 1)
    ])
    d4 = _make_valid_proposal_dict()
    d4["proposed_omissions"] = overflow_omits
    with pytest.raises(ValueError, match="ERR_COUNT_OVERFLOW"):
        normalize_edit_plan_proposal(d4)
    assert overflow_omits.iterations == 0


# ==============================================================================
# 10. EXACT BYTE BUDGET & OVERFLOW TESTS
# ==============================================================================

def test_exact_byte_budget_equality_accepted_overflow_rejected():
    raw_dict = _make_valid_proposal_dict()
    base_prop = normalize_edit_plan_proposal(raw_dict)
    base_bytes = json.dumps(
        edit_plan_proposal_to_dict(base_prop),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    base_len = len(base_bytes)

    # Chèn padding text vào video_plan.strategy_summary để đạt đúng 524,288 bytes
    target_len = MAX_PROPOSAL_SNAPSHOT_BYTES  # 524288
    delta = target_len - base_len
    # Ban đầu chừa an toàn 50 bytes cho key overhead
    padding = "A" * (delta - 50)
    raw_dict["video_plan"]["padding"] = padding
    prop_padded = normalize_edit_plan_proposal(raw_dict)
    padded_bytes = json.dumps(
        edit_plan_proposal_to_dict(prop_padded),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    # Tinh chỉnh để đúng chính xác target_len
    cur_len = len(padded_bytes)
    adj_delta = target_len - cur_len
    if adj_delta != 0:
        raw_dict["video_plan"]["padding"] = "A" * (len(padding) + adj_delta)
        prop_padded = normalize_edit_plan_proposal(raw_dict)
        padded_bytes = json.dumps(
            edit_plan_proposal_to_dict(prop_padded),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    # Kiểm tra equality: đúng 524,288 bytes phải được chấp nhận
    assert len(padded_bytes) == MAX_PROPOSAL_SNAPSHOT_BYTES
    rev = compute_proposal_revision(prop_padded)
    assert len(rev) == 64

    # Thêm 1 byte UTF-8 (+1 overflow) -> Phải bị từ chối
    raw_dict["video_plan"]["padding"] += "X"
    with pytest.raises(ValueError, match="ERR_BYTE_BUDGET_OVERFLOW"):
        normalize_edit_plan_proposal(raw_dict)


# ==============================================================================
# 11. PURITY SPIES (ZERO FILESYSTEM IO & ZERO NETWORK CALLS)
# ==============================================================================

def test_purity_zero_io_and_zero_network(monkeypatch):
    io_called = []
    net_called = []

    real_open = open
    real_stat = os.stat
    real_socket = socket.socket

    def fake_open(*args, **kwargs):
        io_called.append(("open", args))
        return real_open(*args, **kwargs)

    def fake_stat(*args, **kwargs):
        io_called.append(("stat", args))
        return real_stat(*args, **kwargs)

    def fake_socket(*args, **kwargs):
        net_called.append(("socket", args))
        return real_socket(*args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    monkeypatch.setattr("os.stat", fake_stat)
    monkeypatch.setattr("socket.socket", fake_socket)

    raw_dict = _make_valid_proposal_dict()
    prop = normalize_edit_plan_proposal(raw_dict)
    _ = edit_plan_proposal_to_dict(prop)
    _ = compute_proposal_revision(prop)

    assert io_called == [], f"Phát hiện filesystem IO trái phép: {io_called}"
    assert net_called == [], f"Phát hiện network calls trái phép: {net_called}"


# ==============================================================================
# 12. G06-06C1b1-R1 HARDENING PROBES & REWORK TESTS
# ==============================================================================

def test_video_plan_inserts_type_gates_and_bounds():
    """Kiểm tra kiểu dữ liệu bắt buộc (list/tuple) và trần inserts <= 4096."""
    # 1. broll_inserts sai kiểu: False, None, dict, str, range
    for bad_val in (False, None, {"a": 1}, "not_a_list", range(10)):
        d = _make_valid_proposal_dict()
        d["video_plan"]["broll_inserts"] = bad_val
        with pytest.raises(ValueError, match="ERR_INVALID_SEQUENCE"):
            normalize_edit_plan_proposal(d)

    # Constructor trực tiếp cũng phải validate tương tự
    d_bad_ctor = _make_valid_proposal_dict()
    d_bad_ctor["video_plan"]["broll_inserts"] = False
    with pytest.raises(ValueError, match="ERR_INVALID_SEQUENCE"):
        EditPlanProposal(
            proposal_id=d_bad_ctor["proposal_id"],
            manifest_id=d_bad_ctor["manifest_id"],
            creative_context=d_bad_ctor["creative_context"],
            source_binding=d_bad_ctor["source_binding"],
            video_plan=d_bad_ctor["video_plan"],
        )

    # 2. sfx_inserts sai kiểu: False, None, dict, int, range
    for bad_val in (False, None, {"sfx": 1}, 123, range(5)):
        d = _make_valid_proposal_dict()
        d["video_plan"]["sfx_inserts"] = bad_val
        with pytest.raises(ValueError, match="ERR_INVALID_SEQUENCE"):
            normalize_edit_plan_proposal(d)

    # 3. Missing inserts: raw payload không có broll/sfx inserts được chấp nhận và không tự chèn default
    d_missing = _make_valid_proposal_dict()
    del d_missing["video_plan"]["broll_inserts"]
    del d_missing["video_plan"]["sfx_inserts"]
    prop_missing = normalize_edit_plan_proposal(d_missing)
    assert "broll_inserts" not in prop_missing.video_plan
    assert "sfx_inserts" not in prop_missing.video_plan
    exported_missing = edit_plan_proposal_to_dict(prop_missing)
    assert "broll_inserts" not in exported_missing["video_plan"]
    assert "sfx_inserts" not in exported_missing["video_plan"]

    # 4. Chỉ có một trong hai inserts: field còn lại thiếu vẫn giữ nguyên
    d_broll_only = _make_valid_proposal_dict()
    del d_broll_only["video_plan"]["sfx_inserts"]
    prop_broll_only = normalize_edit_plan_proposal(d_broll_only)
    assert "broll_inserts" in prop_broll_only.video_plan
    assert "sfx_inserts" not in prop_broll_only.video_plan

    d_sfx_only = _make_valid_proposal_dict()
    del d_sfx_only["video_plan"]["broll_inserts"]
    prop_sfx_only = normalize_edit_plan_proposal(d_sfx_only)
    assert "sfx_inserts" in prop_sfx_only.video_plan
    assert "broll_inserts" not in prop_sfx_only.video_plan

    # 5. Equality: đúng 4096 inserts (2048 broll + 2048 sfx) phải được chấp nhận
    d_eq = _make_valid_proposal_dict()
    d_eq["video_plan"]["broll_inserts"] = [{"idx": i} for i in range(2048)]
    d_eq["video_plan"]["sfx_inserts"] = [{"idx": i} for i in range(2048)]
    prop_eq = normalize_edit_plan_proposal(d_eq)
    assert len(prop_eq.video_plan["broll_inserts"]) + len(prop_eq.video_plan["sfx_inserts"]) == MAX_RAW_INSERTS

    # 6. Overflow: 4097 inserts phải bị từ chối với 0 children iterations
    d_overflow = _make_valid_proposal_dict()
    overflow_broll = CountedList([{"idx": i} for i in range(2049)])
    overflow_sfx = CountedList([{"idx": i} for i in range(2048)])
    d_overflow["video_plan"]["broll_inserts"] = overflow_broll
    d_overflow["video_plan"]["sfx_inserts"] = overflow_sfx
    with pytest.raises(ValueError, match="ERR_COUNT_OVERFLOW"):
        normalize_edit_plan_proposal(d_overflow)
    assert overflow_broll.iterations == 0
    assert overflow_sfx.iterations == 0


def test_assignment_provenance_source_id_and_pack_id():
    """Kiểm tra provenance fields source_id và pack_id: non-blank str hoặc None."""
    # 1. source_id sai kiểu: [], 23, bool, {}, blank str
    for bad_source_id in ([], 23, False, {}, "", "   "):
        d = _make_valid_proposal_dict()
        d["proposed_asset_assignments"][0]["source_id"] = bad_source_id
        with pytest.raises(ValueError):
            normalize_edit_plan_proposal(d)

    # 2. pack_id sai kiểu: [], 23, bool, {}, blank str
    for bad_pack_id in ([], 23, True, {}, "", "   "):
        d = _make_valid_proposal_dict()
        d["proposed_asset_assignments"][0]["pack_id"] = bad_pack_id
        with pytest.raises(ValueError):
            normalize_edit_plan_proposal(d)

    # 3. Hợp lệ: source_id và pack_id là non-blank str hoặc None
    d_valid = _make_valid_proposal_dict()
    d_valid["proposed_asset_assignments"][0]["source_id"] = "src_lib_audio_01"
    d_valid["proposed_asset_assignments"][0]["pack_id"] = "pack_cinematic_v2"
    prop_valid = normalize_edit_plan_proposal(d_valid)
    assert prop_valid.proposed_asset_assignments[0]["source_id"] == "src_lib_audio_01"
    assert prop_valid.proposed_asset_assignments[0]["pack_id"] == "pack_cinematic_v2"
    exported = edit_plan_proposal_to_dict(prop_valid)
    assert exported["proposed_asset_assignments"][0]["source_id"] == "src_lib_audio_01"
    assert exported["proposed_asset_assignments"][0]["pack_id"] == "pack_cinematic_v2"

    d_none = _make_valid_proposal_dict()
    d_none["proposed_asset_assignments"][0]["source_id"] = None
    d_none["proposed_asset_assignments"][0]["pack_id"] = None
    prop_none = normalize_edit_plan_proposal(d_none)
    assert prop_none.proposed_asset_assignments[0]["source_id"] is None
    assert prop_none.proposed_asset_assignments[0]["pack_id"] is None

    # 4. Hash sensitivity: thay đổi source_id hoặc pack_id phải làm đổi revision SHA-256
    rev_base = compute_proposal_revision(prop_valid)

    d_alt_src = _make_valid_proposal_dict()
    d_alt_src["proposed_asset_assignments"][0]["source_id"] = "src_lib_audio_02"
    d_alt_src["proposed_asset_assignments"][0]["pack_id"] = "pack_cinematic_v2"
    rev_alt_src = compute_proposal_revision(normalize_edit_plan_proposal(d_alt_src))
    assert rev_alt_src != rev_base

    d_alt_pack = _make_valid_proposal_dict()
    d_alt_pack["proposed_asset_assignments"][0]["source_id"] = "src_lib_audio_01"
    d_alt_pack["proposed_asset_assignments"][0]["pack_id"] = "pack_cinematic_v3"
    rev_alt_pack = compute_proposal_revision(normalize_edit_plan_proposal(d_alt_pack))
    assert rev_alt_pack != rev_base

    rev_none = compute_proposal_revision(prop_none)
    assert rev_none != rev_base


def test_generic_metadata_keys_fidelity_and_empty_keys():
    """Kiểm tra cho phép empty/whitespace keys trong generic metadata, UTF-8 tiếng Việt và sensitivity."""
    # 1. Cho phép empty key ("") và whitespace key trong generic metadata
    d = _make_valid_proposal_dict()
    d["video_plan"]["extra_meta"] = {
        "": "empty key value",
        "   ": "whitespace key value",
        "mô_tả_dựng": "tiếng Việt UTF-8 chuẩn",
        'key "với" ngoặc kép': 999,
    }
    prop = normalize_edit_plan_proposal(d)
    assert prop.video_plan["extra_meta"][""] == "empty key value"
    assert prop.video_plan["extra_meta"]["   "] == "whitespace key value"
    assert prop.video_plan["extra_meta"]["mô_tả_dựng"] == "tiếng Việt UTF-8 chuẩn"
    assert prop.video_plan["extra_meta"]['key "với" ngoặc kép'] == 999

    # Roundtrip bảo toàn nguyên vẹn khóa
    exported = edit_plan_proposal_to_dict(prop)
    assert exported["video_plan"]["extra_meta"][""] == "empty key value"
    assert exported["video_plan"]["extra_meta"]["   "] == "whitespace key value"
    assert exported["video_plan"]["extra_meta"]["mô_tả_dựng"] == "tiếng Việt UTF-8 chuẩn"
    assert exported["video_plan"]["extra_meta"]['key "với" ngoặc kép'] == 999

    # 2. Hash sensitivity trên empty key
    rev1 = compute_proposal_revision(prop)
    d2 = _make_valid_proposal_dict()
    d2["video_plan"]["extra_meta"] = {
        "": "changed empty key value",
        "   ": "whitespace key value",
        "mô_tả_dựng": "tiếng Việt UTF-8 chuẩn",
        'key "với" ngoặc kép': 999,
    }
    rev2 = compute_proposal_revision(normalize_edit_plan_proposal(d2))
    assert rev1 != rev2

    # 3. Khóa non-str trong generic mapping bị từ chối
    d_bad_key = _make_valid_proposal_dict()
    d_bad_key["video_plan"]["extra_meta"] = {123: "val"}  # type: ignore
    with pytest.raises(ValueError, match="ERR_INVALID_KEY"):
        normalize_edit_plan_proposal(d_bad_key)

    # 4. Ký tự NUL trong khóa generic mapping bị từ chối
    d_nul_key = _make_valid_proposal_dict()
    d_nul_key["video_plan"]["extra_meta"] = {"key\0nul": "val"}
    with pytest.raises(ValueError, match="ERR_INVALID_STRING"):
        normalize_edit_plan_proposal(d_nul_key)

    # 5. Domain IDs và root allowlist keys vẫn nghiêm ngặt cấm rỗng/khoảng trắng
    for bad_id in ("", "   "):
        d_bad_mid = _make_valid_proposal_dict()
        d_bad_mid["manifest_id"] = bad_id
        with pytest.raises(ValueError, match="ERR_BLANK_FIELD"):
            normalize_edit_plan_proposal(d_bad_mid)

        d_bad_pid = _make_valid_proposal_dict()
        d_bad_pid["proposal_id"] = bad_id
        with pytest.raises(ValueError, match="ERR_BLANK_FIELD"):
            normalize_edit_plan_proposal(d_bad_pid)

        d_bad_cid = _make_valid_proposal_dict()
        d_bad_cid["source_binding"]["clips"][0]["clip_id"] = bad_id
        with pytest.raises(ValueError, match="ERR_BLANK_FIELD"):
            normalize_edit_plan_proposal(d_bad_cid)


def test_generic_node_container_precheck_0_iterations():
    """Kiểm tra pre-check sức chứa container trong generic JSON: 0 children iterations khi tràn."""
    # 1. CountedList 100,001 phần tử trong video_plan.extra
    d1 = _make_valid_proposal_dict()
    cl = CountedList([None] * (MAX_JSON_NODES + 1))
    d1["video_plan"]["extra"] = cl
    with pytest.raises(ValueError, match="ERR_JSON_NODES_EXCEEDED"):
        normalize_edit_plan_proposal(d1)
    assert cl.iterations == 0

    # 2. CountedMapping 100,001 phần tử trong video_plan.extra
    d2 = _make_valid_proposal_dict()
    cm = CountedMapping({f"k_{i}": i for i in range(MAX_JSON_NODES + 1)})
    d2["video_plan"]["extra"] = cm
    with pytest.raises(ValueError, match="ERR_JSON_NODES_EXCEEDED"):
        normalize_edit_plan_proposal(d2)
    assert cm.iterations == 0


def test_node_cap_equality_and_overflow_with_precheck(monkeypatch):
    """Kiểm tra tính chính xác của trần node JSON: equality accepted, overflow rejected tại đúng tầng."""
    import src.core.contracts.edit_plan_proposal_contracts as mod

    raw_dict = _make_valid_proposal_dict()
    # Baseline fixture có đúng 87 JSON nodes
    EXACT_NODES = 87

    # Equality check: monkeypatch trần đúng bằng EXACT_NODES
    monkeypatch.setattr(mod, "MAX_JSON_NODES", EXACT_NODES)
    prop = mod.normalize_edit_plan_proposal(raw_dict)
    assert prop.proposal_id == "prop_20261029_001"

    # Overflow check: monkeypatch trần EXACT_NODES - 1
    monkeypatch.setattr(mod, "MAX_JSON_NODES", EXACT_NODES - 1)
    with pytest.raises(ValueError, match="ERR_JSON_NODES_EXCEEDED"):
        mod.normalize_edit_plan_proposal(raw_dict)

