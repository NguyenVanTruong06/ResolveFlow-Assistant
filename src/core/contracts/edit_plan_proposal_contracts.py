"""Hợp đồng Snapshot Bản đề xuất kịch bản ChunDVC — EditPlanProposal (G06-06C1b1-I).

Module pure Python chuẩn stdlib, định nghĩa:
- EditPlanProposal: Frozen DTO biểu diễn bản đề xuất kịch bản trọn gói bất biến.
- normalize_edit_plan_proposal: Chuẩn hóa, validate toàn diện và tạo owned instance.
- edit_plan_proposal_to_dict: Xuất bản từ điển tươi mới chứa JSON primitives (defensive copy).
- compute_proposal_revision: Tính mã băm SHA-256 64-hex chuẩn tắc của toàn bộ proposal payload.

Tuân thủ nghiêm ngặt:
- Zero IO, zero network, zero Qt/UI/Resolve proxy/clock/random.
- Immutable & owned (frozen dataclass, MappingProxyType, tuple, zero runtime aliases).
- Giới hạn trần dung lượng UTF-8: MAX_PROPOSAL_SNAPSHOT_BYTES = 524,288 (512 KiB).
- Giới hạn cấu trúc & bộ đếm: clips <= 4096, timeline_segments <= 256, raw inserts <= 4096,
  assignments <= 128, omissions <= 128, MAX_JSON_DEPTH = 32, MAX_JSON_NODES = 100,000.
- Ranh giới miền dữ liệu: Khớp manifest_id nhất quán, clip_id thuộc source_binding,
  bounds thời gian [0 <= start < end, speed > 0], slot chỉ broll/sfx, source_kind chuẩn,
  origin_library_id bắt buộc cho managed/linked, demand_id unique & disjoint.
- Cách ly tuyệt đối tài liệu tham khảo (role='reference_only') khỏi footage & assignments.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Optional, Tuple, Union

from src.core.contracts.edit_plan_contracts import (
    CreativeContext,
    creative_context_to_dict,
    normalize_creative_context,
)

# Giới hạn kích thước và số lượng intake siêu dữ liệu
MAX_SOURCE_CLIPS: int = 4096
MAX_TIMELINE_SEGMENTS: int = 256
MAX_RAW_INSERTS: int = 4096  # Tổng broll_inserts + sfx_inserts trong video_plan
MAX_PROPOSED_ASSIGNMENTS: int = 128
MAX_PROPOSED_OMISSIONS: int = 128

MAX_PROPOSAL_SNAPSHOT_BYTES: int = 524288  # 512 KiB UTF-8
MAX_JSON_DEPTH: int = 32
MAX_JSON_NODES: int = 100000

VALID_SOURCE_KINDS: frozenset[str] = frozenset({"managed", "linked", "builtin", "generated"})
VALID_SLOTS: frozenset[str] = frozenset({"broll", "sfx"})

ALLOWED_ROOT_KEYS: frozenset[str] = frozenset({
    "proposal_id",
    "manifest_id",
    "creative_context",
    "source_binding",
    "video_plan",
    "kinetic_plan",
    "proposed_asset_assignments",
    "proposed_omissions",
})


def _validate_str(val: Any, field_name: str, allow_blank: bool = False) -> str:
    """Xác thực chuỗi UTF-8 hợp lệ, không chứa ký tự NUL hoặc lone surrogates."""
    if type(val) is not str:
        raise ValueError(f"ERR_INVALID_TYPE: {field_name} phải là kiểu str, nhận: {type(val).__name__}")
    if "\0" in val:
        raise ValueError(f"ERR_INVALID_STRING: {field_name} chứa ký tự NUL không hợp lệ")
    try:
        val.encode("utf-8")
    except UnicodeEncodeError as e:
        raise ValueError(f"ERR_INVALID_UTF8: {field_name} không thể mã hóa UTF-8 (lone surrogate): {e}") from e
    if not allow_blank and not val.strip():
        raise ValueError(f"ERR_BLANK_FIELD: {field_name} không được để trống hoặc chỉ chứa khoảng trắng")
    return val


def _validate_finite_num(
    val: Any,
    field_name: str,
    min_val: Optional[float] = None,
    strictly_positive: bool = False,
) -> Union[int, float]:
    """Xác thực số thực hoặc số nguyên hữu hạn, loại trừ tuyệt đối kiểu bool và NaN/Inf."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ValueError(f"ERR_INVALID_NUMBER: {field_name} phải là int hoặc float (loại trừ bool), nhận: {type(val).__name__}")

    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            raise ValueError(f"ERR_INVALID_NUMBER: {field_name} không được là NaN hoặc Inf, nhận: {val}")
    elif isinstance(val, int):
        try:
            val_float = float(val)
            if math.isinf(val_float):
                raise ValueError(f"ERR_NUMBER_OVERFLOW: {field_name} vượt quá giới hạn số học float")
        except OverflowError as e:
            raise ValueError(f"ERR_NUMBER_OVERFLOW: {field_name} vượt quá giới hạn số học: {e}") from e

    if strictly_positive and val <= 0:
        raise ValueError(f"ERR_INVALID_NUMBER: {field_name} phải là số dương (> 0), nhận: {val}")
    if min_val is not None and val < min_val:
        raise ValueError(f"ERR_INVALID_NUMBER: {field_name} phải >= {min_val}, nhận: {val}")
    return val


class _NodeCounter:
    """Bộ đếm số node JSON trong cây để phòng thủ DoS/memory exhaustion.
    
    Quy định hạch toán (Accounting):
    - Mỗi primitive (str, int, float, bool, None) hoặc container (Mapping, list, tuple)
      được tính là 1 node khi bắt đầu duyệt.
    - Với container, pre-check kiểm tra sức chứa tối thiểu: count + len(container) <= MAX_JSON_NODES
      trước khi duyệt bất kỳ phần tử con nào (bảo đảm 0 iterations nếu chắc chắn tràn budget).
    - Các node được chia sẻ trong DAG (shared alias) không được tính miễn phí mà được tính đầy đủ
      mỗi lần xuất hiện, phản ánh đúng chi phí serialize trong canonical JSON.
    - Giới hạn trần: MAX_JSON_NODES = 100,000 (equality được chấp nhận).
    """
    def __init__(self):
        self.count = 0

    def inc(self, field_path: str):
        self.count += 1
        if self.count > MAX_JSON_NODES:
            raise ValueError(f"ERR_JSON_NODES_EXCEEDED: Tổng số JSON nodes ({self.count}) vượt quá trần cho phép ({MAX_JSON_NODES}) tại {field_path}")

    def check_capacity(self, child_count: int, field_path: str):
        """Kiểm tra sức chứa tối thiểu của container trước khi duyệt các phần tử con."""
        if self.count + child_count > MAX_JSON_NODES:
            raise ValueError(f"ERR_JSON_NODES_EXCEEDED: Tổng số JSON nodes dự kiến ({self.count + child_count}) vượt quá trần cho phép ({MAX_JSON_NODES}) tại {field_path}")


def _own_json_value(
    val: Any,
    field_path: str,
    depth: int,
    active_ancestors: set[int],
    node_counter: _NodeCounter,
    freeze: bool = True,
) -> Any:
    """Sao chép sở hữu sâu một giá trị JSON, kiểm tra độ sâu, số node và phát hiện chu trình (cycle).
    
    Quy định:
    - active_ancestors chỉ lưu các object ID trên nhánh đệ quy hiện hành: cho phép cấu trúc DAG chia sẻ
      node không có chu trình (shared alias được nhân bản độc lập và tính chi phí node mỗi lần xuất hiện).
    - Pre-check len của container trước khi iterate/copy/enqueue, bảo đảm 0 iterations nếu không thể fit.
    - Cho phép empty string ("") và khoảng trắng đối với khóa mapping siêu dữ liệu chung (generic JSON metadata).
    - freeze=True trả về MappingProxyType trên dict và tuple trên list.
    - freeze=False trả về plain dict và plain list tươi mới cho export.
    """
    node_counter.inc(field_path)
    if depth > MAX_JSON_DEPTH:
        raise ValueError(f"ERR_JSON_DEPTH_EXCEEDED: Độ sâu JSON ({depth}) vượt quá giới hạn ({MAX_JSON_DEPTH}) tại {field_path}")

    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        try:
            float(val)
        except OverflowError as e:
            raise ValueError(f"ERR_NUMBER_OVERFLOW: Giá trị int tại {field_path} vượt quá giới hạn số học: {e}") from e
        return val
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            raise ValueError(f"ERR_INVALID_NUMBER: Số thực float tại {field_path} không được là NaN hoặc Inf")
        return val
    if isinstance(val, str):
        return _validate_str(val, field_path, allow_blank=True)

    # Xử lý container (Mapping hoặc Sequence)
    obj_id = id(val)
    if obj_id in active_ancestors:
        raise ValueError(f"ERR_CYCLE_DETECTED: Phát hiện vòng lặp tham chiếu (circular reference) tại {field_path}")

    active_ancestors.add(obj_id)
    try:
        if isinstance(val, Mapping):
            child_count = len(val)
            node_counter.check_capacity(child_count, field_path)
            owned_dict = {}
            for k, v in val.items():
                if not isinstance(k, str):
                    raise ValueError(f"ERR_INVALID_KEY: Khóa mapping tại {field_path} phải là str, nhận: {repr(k)}")
                _validate_str(k, f"{field_path}.key({repr(k)})", allow_blank=True)
                owned_dict[k] = _own_json_value(
                    v, f"{field_path}.{k}", depth + 1, active_ancestors, node_counter, freeze=freeze
                )
            return MappingProxyType(owned_dict) if freeze else owned_dict

        elif isinstance(val, (list, tuple)):
            child_count = len(val)
            node_counter.check_capacity(child_count, field_path)
            owned_list = [
                _own_json_value(
                    elem, f"{field_path}[{idx}]", depth + 1, active_ancestors, node_counter, freeze=freeze
                )
                for idx, elem in enumerate(val)
            ]
            return tuple(owned_list) if freeze else owned_list

        else:
            raise ValueError(f"ERR_INVALID_JSON_TYPE: Đối tượng tại {field_path} có kiểu không hỗ trợ trong JSON ({type(val).__name__})")
    finally:
        active_ancestors.remove(obj_id)


def _validate_and_own_source_binding(
    raw_sb: Any,
    root_manifest_id: str,
    ref_ids: frozenset[str],
    node_counter: _NodeCounter,
) -> tuple[MappingProxyType, frozenset[str]]:
    """Xác thực và sở hữu source_binding, trả về (owned_sb, seen_clip_ids)."""
    if not isinstance(raw_sb, Mapping):
        raise ValueError(f"ERR_INVALID_MAPPING: source_binding phải là Mapping, nhận: {type(raw_sb).__name__}")

    if "manifest_id" not in raw_sb:
        raise ValueError("ERR_MISSING_FIELD: source_binding bắt buộc phải có trường 'manifest_id'")
    sb_mid = _validate_str(raw_sb["manifest_id"], "source_binding.manifest_id", allow_blank=False)
    if sb_mid != root_manifest_id:
        raise ValueError(f"ERR_MANIFEST_MISMATCH: source_binding.manifest_id ({sb_mid!r}) không khớp root manifest_id ({root_manifest_id!r})")

    if "clips" not in raw_sb:
        raise ValueError("ERR_MISSING_FIELD: source_binding bắt buộc phải có trường 'clips'")
    raw_clips = raw_sb["clips"]
    if not isinstance(raw_clips, (list, tuple)):
        raise ValueError(f"ERR_INVALID_SEQUENCE: source_binding.clips phải là list hoặc tuple, nhận: {type(raw_clips).__name__}")

    # Pre-check độ dài clips trước iteration
    if len(raw_clips) > MAX_SOURCE_CLIPS:
        raise ValueError(f"ERR_COUNT_OVERFLOW: source_binding.clips có {len(raw_clips)} phần tử, vượt quá giới hạn {MAX_SOURCE_CLIPS}")

    seen_clip_ids: set[str] = set()
    for idx, c in enumerate(raw_clips):
        if not isinstance(c, Mapping):
            raise ValueError(f"ERR_INVALID_CLIP_ROW: clip [{idx}] trong source_binding.clips phải là Mapping, nhận: {type(c).__name__}")
        if "clip_id" not in c:
            raise ValueError(f"ERR_MISSING_FIELD: clip [{idx}] trong source_binding.clips thiếu trường 'clip_id'")
        cid = _validate_str(c["clip_id"], f"source_binding.clips[{idx}].clip_id", allow_blank=False)
        if cid in seen_clip_ids:
            raise ValueError(f"ERR_DUPLICATE_CLIP_ID: clip_id {cid!r} bị trùng lặp trong source_binding.clips")
        if cid in ref_ids or c.get("role") == "reference_only":
            raise ValueError(f"ERR_REFERENCE_LEAK: clip_id {cid!r} là tài liệu tham khảo (reference_only), cấm đưa vào source clips")
        seen_clip_ids.add(cid)

    # Sở hữu sâu toàn bộ source_binding
    owned_sb = _own_json_value(
        raw_sb, "source_binding", depth=1, active_ancestors=set(), node_counter=node_counter, freeze=True
    )
    return owned_sb, frozenset(seen_clip_ids)


def _validate_and_own_video_plan(
    raw_vp: Any,
    root_manifest_id: str,
    valid_clip_ids: frozenset[str],
    ref_ids: frozenset[str],
    node_counter: _NodeCounter,
) -> MappingProxyType:
    """Xác thực và sở hữu video_plan (Slot 1)."""
    if not isinstance(raw_vp, Mapping):
        raise ValueError(f"ERR_INVALID_MAPPING: video_plan phải là Mapping, nhận: {type(raw_vp).__name__}")

    if "manifest_id" not in raw_vp:
        raise ValueError("ERR_MISSING_FIELD: video_plan bắt buộc phải có trường 'manifest_id'")
    vp_mid = _validate_str(raw_vp["manifest_id"], "video_plan.manifest_id", allow_blank=False)
    if vp_mid != root_manifest_id:
        raise ValueError(f"ERR_MANIFEST_MISMATCH: video_plan.manifest_id ({vp_mid!r}) không khớp root manifest_id ({root_manifest_id!r})")

    if "timeline_segments" not in raw_vp:
        raise ValueError("ERR_MISSING_FIELD: video_plan bắt buộc phải có trường 'timeline_segments'")
    raw_segs = raw_vp["timeline_segments"]
    if not isinstance(raw_segs, (list, tuple)):
        raise ValueError(f"ERR_INVALID_SEQUENCE: video_plan.timeline_segments phải là list hoặc tuple, nhận: {type(raw_segs).__name__}")

    # Pre-check độ dài timeline_segments
    if len(raw_segs) == 0:
        raise ValueError("ERR_EMPTY_TIMELINE_SEGMENTS: video_plan.timeline_segments không được để trống")
    if len(raw_segs) > MAX_TIMELINE_SEGMENTS:
        raise ValueError(f"ERR_COUNT_OVERFLOW: video_plan.timeline_segments có {len(raw_segs)} phân đoạn, vượt quá {MAX_TIMELINE_SEGMENTS}")

    # Kiểm tra kiểu và trần raw inserts (nếu có trong video_plan)
    has_broll = "broll_inserts" in raw_vp
    has_sfx = "sfx_inserts" in raw_vp

    if has_broll:
        raw_broll = raw_vp["broll_inserts"]
        if not isinstance(raw_broll, (list, tuple)):
            raise ValueError(f"ERR_INVALID_SEQUENCE: video_plan.broll_inserts phải là list hoặc tuple, nhận: {type(raw_broll).__name__}")
    else:
        raw_broll = ()

    if has_sfx:
        raw_sfx = raw_vp["sfx_inserts"]
        if not isinstance(raw_sfx, (list, tuple)):
            raise ValueError(f"ERR_INVALID_SEQUENCE: video_plan.sfx_inserts phải là list hoặc tuple, nhận: {type(raw_sfx).__name__}")
    else:
        raw_sfx = ()

    total_inserts = len(raw_broll) + len(raw_sfx)
    if total_inserts > MAX_RAW_INSERTS:
        raise ValueError(f"ERR_COUNT_OVERFLOW: Tổng số inserts ({total_inserts}) vượt quá giới hạn {MAX_RAW_INSERTS}")

    # Validate từng segment
    for idx, seg in enumerate(raw_segs):
        if not isinstance(seg, Mapping):
            raise ValueError(f"ERR_INVALID_SEGMENT: segment [{idx}] trong timeline_segments phải là Mapping, nhận: {type(seg).__name__}")
        if "clip_id" not in seg:
            raise ValueError(f"ERR_MISSING_FIELD: segment [{idx}] thiếu trường 'clip_id'")
        cid = _validate_str(seg["clip_id"], f"timeline_segments[{idx}].clip_id", allow_blank=False)
        if cid not in valid_clip_ids:
            raise ValueError(f"ERR_UNKNOWN_CLIP_ID: clip_id {cid!r} trong timeline_segments[{idx}] không tồn tại trong source_binding.clips")
        if cid in ref_ids or seg.get("role") == "reference_only":
            raise ValueError(f"ERR_REFERENCE_LEAK: clip_id {cid!r} trong timeline_segments[{idx}] là tài liệu tham khảo (reference_only)")

        if "start_sec" not in seg or "end_sec" not in seg:
            raise ValueError(f"ERR_MISSING_FIELD: segment [{idx}] thiếu start_sec hoặc end_sec")

        s_in = _validate_finite_num(seg["start_sec"], f"timeline_segments[{idx}].start_sec")
        if s_in < 0.0:
            raise ValueError(f"ERR_INVALID_TIMELINE_BOUNDS: timeline_segments[{idx}].start_sec phải >= 0.0, nhận: {s_in}")

        s_out = _validate_finite_num(seg["end_sec"], f"timeline_segments[{idx}].end_sec")
        if s_out <= 0.0:
            raise ValueError(f"ERR_INVALID_TIMELINE_BOUNDS: timeline_segments[{idx}].end_sec phải > 0.0, nhận: {s_out}")

        if s_in >= s_out:
            raise ValueError(f"ERR_INVALID_TIMELINE_BOUNDS: timeline_segments[{idx}] có start_sec ({s_in}) >= end_sec ({s_out})")

        if "speed" in seg:
            speed_val = _validate_finite_num(seg["speed"], f"timeline_segments[{idx}].speed")
            if speed_val <= 0:
                raise ValueError(f"ERR_INVALID_SPEED: timeline_segments[{idx}].speed phải > 0, nhận: {speed_val}")

    owned_vp = _own_json_value(
        raw_vp, "video_plan", depth=1, active_ancestors=set(), node_counter=node_counter, freeze=True
    )
    return owned_vp


def _validate_and_own_kinetic_plan(
    raw_kp: Any,
    root_manifest_id: str,
    node_counter: _NodeCounter,
) -> Optional[MappingProxyType]:
    """Xác thực và sở hữu kinetic_plan (Slot 2)."""
    if raw_kp is None:
        return None
    if not isinstance(raw_kp, Mapping):
        raise ValueError(f"ERR_INVALID_MAPPING: kinetic_plan phải là Mapping hoặc None, nhận: {type(raw_kp).__name__}")

    if "manifest_id" in raw_kp:
        kp_mid = _validate_str(raw_kp["manifest_id"], "kinetic_plan.manifest_id", allow_blank=False)
        if kp_mid != root_manifest_id:
            raise ValueError(f"ERR_MANIFEST_MISMATCH: kinetic_plan.manifest_id ({kp_mid!r}) không khớp root manifest_id ({root_manifest_id!r})")

    owned_kp = _own_json_value(
        raw_kp, "kinetic_plan", depth=1, active_ancestors=set(), node_counter=node_counter, freeze=True
    )
    return owned_kp


def _validate_and_own_assignments_and_omissions(
    raw_assignments: Any,
    raw_omissions: Any,
    ref_ids: frozenset[str],
    node_counter: _NodeCounter,
) -> tuple[tuple[MappingProxyType, ...], tuple[MappingProxyType, ...]]:
    """Xác thực và sở hữu assignments và omissions, bảo đảm tính duy nhất và tách biệt của demand_id."""
    if not isinstance(raw_assignments, (list, tuple)):
        raise ValueError(f"ERR_INVALID_SEQUENCE: proposed_asset_assignments phải là list hoặc tuple, nhận: {type(raw_assignments).__name__}")
    if not isinstance(raw_omissions, (list, tuple)):
        raise ValueError(f"ERR_INVALID_SEQUENCE: proposed_omissions phải là list hoặc tuple, nhận: {type(raw_omissions).__name__}")

    # Pre-check độ dài
    if len(raw_assignments) > MAX_PROPOSED_ASSIGNMENTS:
        raise ValueError(f"ERR_COUNT_OVERFLOW: proposed_asset_assignments có {len(raw_assignments)} mục, vượt quá {MAX_PROPOSED_ASSIGNMENTS}")
    if len(raw_omissions) > MAX_PROPOSED_OMISSIONS:
        raise ValueError(f"ERR_COUNT_OVERFLOW: proposed_omissions có {len(raw_omissions)} mục, vượt quá {MAX_PROPOSED_OMISSIONS}")

    assign_demand_ids: set[str] = set()
    for idx, item in enumerate(raw_assignments):
        if not isinstance(item, Mapping):
            raise ValueError(f"ERR_INVALID_RECORD: assignment [{idx}] phải là Mapping, nhận: {type(item).__name__}")
        for req_field in ("demand_id", "slot", "timeline_sec", "duration_sec", "asset_id", "source_kind"):
            if req_field not in item:
                raise ValueError(f"ERR_MISSING_FIELD: assignment [{idx}] thiếu trường bắt buộc '{req_field}'")

        did = _validate_str(item["demand_id"], f"proposed_asset_assignments[{idx}].demand_id", allow_blank=False)
        if did in assign_demand_ids:
            raise ValueError(f"ERR_DUPLICATE_DEMAND_ID: demand_id {did!r} bị trùng lặp trong proposed_asset_assignments")
        assign_demand_ids.add(did)

        slot = _validate_str(item["slot"], f"proposed_asset_assignments[{idx}].slot", allow_blank=False)
        if slot not in VALID_SLOTS:
            raise ValueError(f"ERR_INVALID_SLOT: proposed_asset_assignments[{idx}].slot phải thuộc {sorted(VALID_SLOTS)}, nhận: {slot!r}")

        _validate_finite_num(item["timeline_sec"], f"proposed_asset_assignments[{idx}].timeline_sec", min_val=0.0)
        _validate_finite_num(item["duration_sec"], f"proposed_asset_assignments[{idx}].duration_sec", strictly_positive=True)

        aid = _validate_str(item["asset_id"], f"proposed_asset_assignments[{idx}].asset_id", allow_blank=False)
        if aid in ref_ids or item.get("role") == "reference_only":
            raise ValueError(f"ERR_REFERENCE_LEAK: asset_id {aid!r} là tài liệu tham khảo (reference_only)")

        skind = _validate_str(item["source_kind"], f"proposed_asset_assignments[{idx}].source_kind", allow_blank=False)
        if skind not in VALID_SOURCE_KINDS:
            raise ValueError(f"ERR_INVALID_SOURCE_KIND: proposed_asset_assignments[{idx}].source_kind phải thuộc {sorted(VALID_SOURCE_KINDS)}, nhận: {skind!r}")

        # Provenance: managed/linked bắt buộc có origin_library_id non-blank
        orig_lib = item.get("origin_library_id")
        if skind in ("managed", "linked"):
            if orig_lib is None or not isinstance(orig_lib, str) or not orig_lib.strip():
                raise ValueError(f"ERR_MISSING_ORIGIN_LIBRARY_ID: assignment [{idx}] có source_kind '{skind}' bắt buộc phải có origin_library_id non-blank")
            _validate_str(orig_lib, f"proposed_asset_assignments[{idx}].origin_library_id", allow_blank=False)
        elif "origin_library_id" in item and orig_lib is not None:
            _validate_str(orig_lib, f"proposed_asset_assignments[{idx}].origin_library_id", allow_blank=False)

        # Provenance: source_id và pack_id khi có chỉ được là non-blank str hoặc None
        if "source_id" in item:
            src_id = item["source_id"]
            if src_id is not None:
                _validate_str(src_id, f"proposed_asset_assignments[{idx}].source_id", allow_blank=False)

        if "pack_id" in item:
            p_id = item["pack_id"]
            if p_id is not None:
                _validate_str(p_id, f"proposed_asset_assignments[{idx}].pack_id", allow_blank=False)

        if "status" in item:
            stat = _validate_str(item["status"], f"proposed_asset_assignments[{idx}].status", allow_blank=False)
            if stat != "PROPOSED":
                raise ValueError(f"ERR_INVALID_STATUS: proposed_asset_assignments[{idx}].status nếu có chỉ được là 'PROPOSED', nhận: {stat!r}")

    omit_demand_ids: set[str] = set()
    for idx, item in enumerate(raw_omissions):
        if not isinstance(item, Mapping):
            raise ValueError(f"ERR_INVALID_RECORD: omission [{idx}] phải là Mapping, nhận: {type(item).__name__}")
        for req_field in ("demand_id", "slot", "timeline_sec", "duration_sec", "reason", "warning_message"):
            if req_field not in item:
                raise ValueError(f"ERR_MISSING_FIELD: omission [{idx}] thiếu trường bắt buộc '{req_field}'")

        did = _validate_str(item["demand_id"], f"proposed_omissions[{idx}].demand_id", allow_blank=False)
        if did in omit_demand_ids:
            raise ValueError(f"ERR_DUPLICATE_DEMAND_ID: demand_id {did!r} bị trùng lặp trong proposed_omissions")
        omit_demand_ids.add(did)

        slot = _validate_str(item["slot"], f"proposed_omissions[{idx}].slot", allow_blank=False)
        if slot not in VALID_SLOTS:
            raise ValueError(f"ERR_INVALID_SLOT: proposed_omissions[{idx}].slot phải thuộc {sorted(VALID_SLOTS)}, nhận: {slot!r}")

        _validate_finite_num(item["timeline_sec"], f"proposed_omissions[{idx}].timeline_sec", min_val=0.0)
        _validate_finite_num(item["duration_sec"], f"proposed_omissions[{idx}].duration_sec", strictly_positive=True)
        _validate_str(item["reason"], f"proposed_omissions[{idx}].reason", allow_blank=False)
        _validate_str(item["warning_message"], f"proposed_omissions[{idx}].warning_message", allow_blank=False)

    # Disjointness check
    overlap = assign_demand_ids & omit_demand_ids
    if overlap:
        bad_id = sorted(overlap)[0]
        raise ValueError(f"ERR_DEMAND_ASSIGNED_AND_OMITTED: demand_id {bad_id!r} xuất hiện đồng thời trong proposed_asset_assignments và proposed_omissions")

    owned_assignments = tuple(
        _own_json_value(item, f"proposed_asset_assignments[{i}]", depth=1, active_ancestors=set(), node_counter=node_counter, freeze=True)
        for i, item in enumerate(raw_assignments)
    )
    owned_omissions = tuple(
        _own_json_value(item, f"proposed_omissions[{i}]", depth=1, active_ancestors=set(), node_counter=node_counter, freeze=True)
        for i, item in enumerate(raw_omissions)
    )
    return owned_assignments, owned_omissions


def _serialize_plain_proposal_dict(proposal: "EditPlanProposal") -> dict[str, object]:
    """Chuyển đổi EditPlanProposal thành plain dict chứa các JSON primitives tươi mới."""
    node_counter = _NodeCounter()
    return {
        "proposal_id": proposal.proposal_id,
        "manifest_id": proposal.manifest_id,
        "creative_context": creative_context_to_dict(proposal.creative_context),
        "source_binding": _own_json_value(proposal.source_binding, "source_binding", 1, set(), node_counter, freeze=False),
        "video_plan": _own_json_value(proposal.video_plan, "video_plan", 1, set(), node_counter, freeze=False),
        "kinetic_plan": _own_json_value(proposal.kinetic_plan, "kinetic_plan", 1, set(), node_counter, freeze=False) if proposal.kinetic_plan is not None else None,
        "proposed_asset_assignments": [
            _own_json_value(item, f"proposed_asset_assignments[{i}]", 1, set(), node_counter, freeze=False)
            for i, item in enumerate(proposal.proposed_asset_assignments)
        ],
        "proposed_omissions": [
            _own_json_value(item, f"proposed_omissions[{i}]", 1, set(), node_counter, freeze=False)
            for i, item in enumerate(proposal.proposed_omissions)
        ],
    }


def _encode_canonical_proposal_bytes(proposal_dict: dict[str, object]) -> bytes:
    """Mã hóa canonical JSON UTF-8 phục vụ kiểm tra byte budget và tính SHA-256."""
    return json.dumps(
        proposal_dict,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True)
class EditPlanProposal:
    """Snapshot bản đề xuất kịch bản trọn gói (Immutable Value Object).
    
    Quy định:
    - Root DTO có đúng 8 fields chuẩn tắc.
    - Toàn bộ cây con là MappingProxyType và tuple bất biến (zero runtime aliases).
    - Validation toàn diện được thực thi đồng nhất khi khởi tạo trực tiếp hoặc qua normalize helper.
    """
    proposal_id: str
    manifest_id: str
    creative_context: CreativeContext
    source_binding: Mapping[str, object]
    video_plan: Mapping[str, object]
    kinetic_plan: Optional[Mapping[str, object]] = None
    proposed_asset_assignments: Tuple[Mapping[str, object], ...] = field(default_factory=tuple)
    proposed_omissions: Tuple[Mapping[str, object], ...] = field(default_factory=tuple)

    def __post_init__(self):
        node_counter = _NodeCounter()

        # 1. proposal_id & manifest_id
        pid = _validate_str(self.proposal_id, "proposal_id", allow_blank=False)
        object.__setattr__(self, "proposal_id", pid)

        mid = _validate_str(self.manifest_id, "manifest_id", allow_blank=False)
        object.__setattr__(self, "manifest_id", mid)

        # 2. creative_context
        c_ctx = normalize_creative_context(self.creative_context)
        object.__setattr__(self, "creative_context", c_ctx)
        ref_ids = frozenset(r.reference_id for r in c_ctx.reference_items)

        # 3. source_binding
        owned_sb, valid_clip_ids = _validate_and_own_source_binding(
            self.source_binding, mid, ref_ids, node_counter
        )
        object.__setattr__(self, "source_binding", owned_sb)

        # 4. video_plan (Slot 1)
        owned_vp = _validate_and_own_video_plan(
            self.video_plan, mid, valid_clip_ids, ref_ids, node_counter
        )
        object.__setattr__(self, "video_plan", owned_vp)

        # 5. kinetic_plan (Slot 2)
        owned_kp = _validate_and_own_kinetic_plan(
            self.kinetic_plan, mid, node_counter
        )
        object.__setattr__(self, "kinetic_plan", owned_kp)

        # 6. proposed_asset_assignments & proposed_omissions
        owned_assigns, owned_omits = _validate_and_own_assignments_and_omissions(
            self.proposed_asset_assignments, self.proposed_omissions, ref_ids, node_counter
        )
        object.__setattr__(self, "proposed_asset_assignments", owned_assigns)
        object.__setattr__(self, "proposed_omissions", owned_omits)

        # 7. Byte budget check trên canonical JSON UTF-8
        plain_dict = _serialize_plain_proposal_dict(self)
        canonical_bytes = _encode_canonical_proposal_bytes(plain_dict)
        if len(canonical_bytes) > MAX_PROPOSAL_SNAPSHOT_BYTES:
            raise ValueError(f"ERR_BYTE_BUDGET_OVERFLOW: Dung lượng canonical JSON ({len(canonical_bytes)} bytes) vượt quá MAX_PROPOSAL_SNAPSHOT_BYTES ({MAX_PROPOSAL_SNAPSHOT_BYTES} bytes)")


def normalize_edit_plan_proposal(
    raw: Union[Mapping[str, object], EditPlanProposal]
) -> EditPlanProposal:
    """Chuẩn hóa mapping thô hoặc EditPlanProposal thành instance bất biến đã được xác thực an toàn.
    
    Quy định:
    - Root mapping chỉ nhận đúng 8 keys cho phép; unknown keys (kể cả user_approved) -> ValueError.
    - Mixed-type keys không gây TypeError.
    - Re-validate toàn diện kể cả khi đầu vào là EditPlanProposal typed.
    """
    if isinstance(raw, EditPlanProposal):
        # Tái xác thực toàn diện để chặn typed mutation bypass
        return EditPlanProposal(
            proposal_id=raw.proposal_id,
            manifest_id=raw.manifest_id,
            creative_context=raw.creative_context,
            source_binding=raw.source_binding,
            video_plan=raw.video_plan,
            kinetic_plan=raw.kinetic_plan,
            proposed_asset_assignments=raw.proposed_asset_assignments,
            proposed_omissions=raw.proposed_omissions,
        )

    if not isinstance(raw, Mapping):
        raise ValueError(f"ERR_INVALID_MAPPING: raw input phải là Mapping hoặc EditPlanProposal, nhận: {type(raw).__name__}")

    # Kiểm tra unknown keys an toàn với mixed types
    bad_keys = sorted(repr(k) for k in raw.keys() if not isinstance(k, str) or k not in ALLOWED_ROOT_KEYS)
    if bad_keys:
        raise ValueError(f"ERR_UNKNOWN_KEY: raw mapping chứa unknown hoặc non-str keys: {bad_keys}")

    # Kiểm tra thiếu các trường bắt buộc
    for req_field in ("proposal_id", "manifest_id", "creative_context", "source_binding", "video_plan"):
        if req_field not in raw:
            raise ValueError(f"ERR_MISSING_FIELD: raw mapping thiếu trường bắt buộc '{req_field}'")

    return EditPlanProposal(
        proposal_id=raw.get("proposal_id", ""),
        manifest_id=raw.get("manifest_id", ""),
        creative_context=raw["creative_context"],
        source_binding=raw["source_binding"],
        video_plan=raw["video_plan"],
        kinetic_plan=raw.get("kinetic_plan", None),
        proposed_asset_assignments=raw.get("proposed_asset_assignments", ()),
        proposed_omissions=raw.get("proposed_omissions", ()),
    )


def edit_plan_proposal_to_dict(proposal: EditPlanProposal) -> dict[str, object]:
    """Chuyển đổi EditPlanProposal thành plain dictionary tươi mới chứa đúng 8 fields JSON primitives."""
    # Re-validate để bảo đảm instance hợp lệ
    validated = normalize_edit_plan_proposal(proposal)
    return _serialize_plain_proposal_dict(validated)


def compute_proposal_revision(proposal: EditPlanProposal) -> str:
    """Tính toán mã băm SHA-256 (64 ký tự hex thường) chuẩn tắc của toàn bộ dữ liệu EditPlanProposal.
    
    Quy định:
    - Băm trên đúng bytes canonical JSON UTF-8 của plain export dict.
    - Hoàn toàn tất định (deterministic), không phụ thuộc thời gian hay trạng thái hệ thống.
    - Zero I/O, không gọi hash tệp vật lý, không sinh thời gian ngẫu nhiên.
    """
    validated = normalize_edit_plan_proposal(proposal)
    plain_dict = _serialize_plain_proposal_dict(validated)
    raw_bytes = _encode_canonical_proposal_bytes(plain_dict)
    return hashlib.sha256(raw_bytes).hexdigest().lower()
