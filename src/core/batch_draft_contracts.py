"""
src/core/batch_draft_contracts.py
=============================================================================
Hợp đồng dữ liệu thuần túy (Pure Stdlib Value Objects & Validator)
cho việc tiếp nhận và đối soát bản thảo phân lô kịch bản (Batch Drafts).
Quy chuẩn G06-06A4b-I:
- 100% Pure Standard Library (dataclasses, typing, json, re, hashlib, math, datetime).
- Tuyệt đối ZERO import từ PySide6, Qt GUI, OS IO, Resolve API, hay AI models.
- Hỗ trợ đối soát hình học thời gian thực tế từng partition (BatchSourceRange).
- Bắt buộc kiểm tra kiểu chặt chẽ: không bool trong float/int, không NaN/Inf, không overflow.
- Phân định rõ hai chế độ ràng buộc snapshot:
  + "echo_matched": AI có trả về snapshot_id và khớp hoàn toàn.
  + "user_associated_current_snapshot": Người dùng chủ động liên kết với snapshot hiện tại.
=============================================================================
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


# =============================================================================
# MÃ LỖI THẨM ĐỊNH (VALIDATION ERROR CODES)
# =============================================================================

ERR_MALFORMED_JSON = "ERR_MALFORMED_JSON"
ERR_OVERSIZE_PAYLOAD = "ERR_OVERSIZE_PAYLOAD"
ERR_DUPLICATE_JSON_KEYS = "ERR_DUPLICATE_JSON_KEYS"
ERR_UNSUPPORTED_SCHEMA_VERSION = "ERR_UNSUPPORTED_SCHEMA_VERSION"
ERR_MANIFEST_MISMATCH = "ERR_MANIFEST_MISMATCH"
ERR_BATCH_ID_MISMATCH = "ERR_BATCH_ID_MISMATCH"
ERR_STALE_SNAPSHOT = "ERR_STALE_SNAPSHOT"
ERR_MISSING_SNAPSHOT_ID = "ERR_MISSING_SNAPSHOT_ID"
ERR_FOREIGN_CLIP_ID = "ERR_FOREIGN_CLIP_ID"
ERR_OUT_OF_PARTITION_BOUNDS = "ERR_OUT_OF_PARTITION_BOUNDS"
ERR_REVERSED_OR_ZERO_DURATION = "ERR_REVERSED_OR_ZERO_DURATION"
ERR_NONFINITE_TIMESTAMP = "ERR_NONFINITE_TIMESTAMP"
ERR_UNSUPPORTED_ACTION = "ERR_UNSUPPORTED_ACTION"
ERR_INVALID_SPEED = "ERR_INVALID_SPEED"
ERR_INVALID_WINDOW_REF = "ERR_INVALID_WINDOW_REF"
ERR_MISSING_BATCH_SUMMARY = "ERR_MISSING_BATCH_SUMMARY"
ERR_UNSUPPORTED_CONTAINER = "ERR_UNSUPPORTED_CONTAINER"
ERR_MALFORMED_SEGMENTS = "ERR_MALFORMED_SEGMENTS"
ERR_MISSING_PREPARED_GEOMETRY = "ERR_MISSING_PREPARED_GEOMETRY"


# =============================================================================
# VALUE OBJECTS BẤT BIẾN (FROZEN DATACLASSES)
# =============================================================================

@dataclass(frozen=True)
class BatchSourceRange:
    """Khung thời gian nguồn phân mảnh của một clip thuộc một lô cụ thể."""
    clip_id: str
    source_in_sec: float
    source_out_sec: float
    partition_index: int
    total_partitions: int
    window_ids: Tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.clip_id, str) or not self.clip_id.strip():
            raise ValueError(f"clip_id phải là chuỗi không rỗng: {self.clip_id!r}")

        # Kiểm tra kiểu nghiêm ngặt (không chấp nhận bool kế thừa int)
        if isinstance(self.source_in_sec, bool) or not isinstance(self.source_in_sec, (int, float)):
            raise ValueError(f"source_in_sec phải là số thực hữu hạn: {self.source_in_sec!r}")
        if isinstance(self.source_out_sec, bool) or not isinstance(self.source_out_sec, (int, float)):
            raise ValueError(f"source_out_sec phải là số thực hữu hạn: {self.source_out_sec!r}")

        try:
            in_sec = float(self.source_in_sec)
            out_sec = float(self.source_out_sec)
        except (OverflowError, ValueError) as err:
            raise ValueError(f"source timestamp quá lớn hoặc không thể chuyển sang float: {err}")

        if not math.isfinite(in_sec) or in_sec < 0.0:
            raise ValueError(f"source_in_sec phải hữu hạn và không âm: {in_sec}")
        if not math.isfinite(out_sec) or out_sec <= in_sec:
            raise ValueError(f"source_out_sec phải lớn hơn source_in_sec: {in_sec} >= {out_sec}")

        if isinstance(self.partition_index, bool) or not isinstance(self.partition_index, int) or self.partition_index < 0:
            raise ValueError(f"partition_index phải là số nguyên >= 0: {self.partition_index!r}")
        if isinstance(self.total_partitions, bool) or not isinstance(self.total_partitions, int) or self.total_partitions < 1:
            raise ValueError(f"total_partitions phải là số nguyên >= 1: {self.total_partitions!r}")
        if self.partition_index >= self.total_partitions:
            raise ValueError(f"partition_index ({self.partition_index}) phải < total_partitions ({self.total_partitions}) theo quy chuẩn 0-based")

        raw_w = getattr(self, "window_ids", ()) or ()
        if not isinstance(raw_w, (tuple, list)):
            raise ValueError(f"window_ids phải là tuple hoặc list: {raw_w!r}")
        clean_w = []
        for w in raw_w:
            if isinstance(w, bool) or not isinstance(w, str) or not w.strip():
                raise ValueError(f"Mỗi phần tử trong window_ids phải là chuỗi không rỗng: {w!r}")
            clean_w.append(w)
        object.__setattr__(self, "window_ids", tuple(clean_w))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clip_id": self.clip_id,
            "source_in_sec": self.source_in_sec,
            "source_out_sec": self.source_out_sec,
            "partition_index": self.partition_index,
            "total_partitions": self.total_partitions,
            "window_ids": self.window_ids,
        }


@dataclass(frozen=True)
class ValidatedSegmentCut:
    """Một phân đoạn biên tập đã được thẩm định tính hợp lệ hình học và nguồn gốc."""
    clip_id: str
    start_sec: float
    end_sec: float
    action: str = "keep"
    speed: float = 1.0
    role: str = ""
    note: str = ""
    source_window_ref: Optional[str] = None
    matched_range_index: int = 0

    @property
    def duration_sec(self) -> float:
        return self.end_sec - self.start_sec

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clip_id": self.clip_id,
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "action": self.action,
            "speed": self.speed,
            "role": self.role,
            "note": self.note,
            "source_window_ref": self.source_window_ref,
            "matched_range_index": self.matched_range_index,
        }


@dataclass(frozen=True)
class ValidatedBatchDraftEntry:
    """Bản ghi bản thảo phân lô hoàn chỉnh trong bộ nhớ, sẵn sàng tích lũy."""
    manifest_id: str
    batch_id: str
    snapshot_id: str
    received_at: str
    raw_payload_sha256: str
    draft_digest: str
    batch_strategy_summary: str
    segments: Tuple[ValidatedSegmentCut, ...]
    total_cuts_count: int
    total_selected_duration_sec: float
    binding_mode: str
    source_ranges: Tuple[BatchSourceRange, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "batch_id": self.batch_id,
            "snapshot_id": self.snapshot_id,
            "received_at": self.received_at,
            "raw_payload_sha256": self.raw_payload_sha256,
            "draft_digest": self.draft_digest,
            "batch_strategy_summary": self.batch_strategy_summary,
            "segments": tuple(s.to_dict() for s in self.segments),
            "total_cuts_count": self.total_cuts_count,
            "total_selected_duration_sec": self.total_selected_duration_sec,
            "binding_mode": self.binding_mode,
            "source_ranges": tuple(r.to_dict() for r in self.source_ranges),
        }


@dataclass(frozen=True)
class BatchDraftValidationResult:
    """Kết quả thẩm định bản thảo phân lô."""
    ok: bool
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    entry: Optional[ValidatedBatchDraftEntry] = None


# =============================================================================
# STRICT JSON DECODER
# =============================================================================

def decode_strict_batch_draft_json(raw_text: str) -> Tuple[bool, Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    """
    Bóc tách chuỗi JSON kịch bản phân lô với các quy tắc an toàn nghiêm ngặt:
    - Trần kích thước UTF-8 <= 65,536 bytes (64KiB).
    - Chỉ chấp nhận Whole JSON object {...} HOẶC đúng 1 complete markdown code fence ```(?:json)? ... ```.
    - Cấm tuyệt đối văn bản bao quanh (prose outside fence, text before/after object).
    - Cấm khóa trùng lặp (Duplicate Keys Rejection).
    - Cấm các hằng số không chuẩn của JSON (NaN, Infinity, -Infinity).
    Trả về: (ok: bool, error_code, error_message, parsed_dict)
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        return False, ERR_MALFORMED_JSON, "Dữ liệu JSON rỗng hoặc không phải chuỗi văn bản.", None

    try:
        text_bytes = raw_text.encode("utf-8")
    except UnicodeEncodeError as uee:
        return False, ERR_MALFORMED_JSON, f"Lỗi mã hóa UTF-8: {uee}", None

    if len(text_bytes) > 65536:
        return False, ERR_OVERSIZE_PAYLOAD, f"Kích thước JSON ({len(text_bytes)} bytes) vượt quá giới hạn 65,536 bytes (64KiB).", None

    stripped = raw_text.strip()

    # Kiểm tra cấu trúc: Whole JSON Object hoặc Single Markdown Code Fence
    cand: str
    if stripped.startswith("```"):
        fence_match = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", stripped)
        if not fence_match:
            return False, ERR_MALFORMED_JSON, "Định dạng code fence không hợp lệ hoặc chứa nhiều khối code fence.", None
        cand = fence_match.group(1).strip()
    else:
        cand = stripped

    # Bắt buộc cand phải bắt đầu bằng '{' và kết thúc bằng '}'
    # Tuyệt đối cấm prose phía trước hoặc phía sau (PREFIX / SUFFIX)
    if not cand or not cand.startswith("{") or not cand.endswith("}"):
        return False, ERR_MALFORMED_JSON, "Dữ liệu JSON phải là một Object {...} hoàn chỉnh, không kèm văn bản thừa bao quanh.", None

    def _reject_duplicate_keys(pairs):
        d = {}
        for k, v in pairs:
            if k in d:
                raise ValueError(f"Khóa trùng lặp trong JSON: '{k}'")
            d[k] = v
        return d

    def _reject_nonstandard_constants(c):
        raise ValueError(f"Hằng số không hợp lệ trong chuẩn JSON: '{c}'")

    try:
        data = json.loads(
            cand,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constants,
        )
    except ValueError as ve:
        msg = str(ve)
        if "Khóa trùng lặp" in msg:
            return False, ERR_DUPLICATE_JSON_KEYS, msg, None
        return False, ERR_MALFORMED_JSON, f"Cú pháp JSON không hợp lệ: {msg}", None
    except Exception as e:
        return False, ERR_MALFORMED_JSON, f"Lỗi bóc tách JSON: {str(e)}", None

    if not isinstance(data, dict):
        return False, ERR_MALFORMED_JSON, "Dữ liệu JSON gốc phải là một Object {...}.", None

    return True, None, None, data


# =============================================================================
# PURE VALIDATOR
# =============================================================================

def validate_batch_draft_payload(
    payload: Any,
    *,
    expected_manifest_id: str,
    expected_snapshot_id: str,
    expected_batch_id: str,
    source_ranges: Sequence[BatchSourceRange],
    associate_with_current_snapshot: bool = False,
    raw_text: Optional[str] = None,
) -> BatchDraftValidationResult:
    """
    Thẩm định thuần túy một payload bản thảo phân lô theo đúng các ràng buộc kiến trúc.
    Không có tác dụng phụ (pure function), an toàn tuyệt đối trước mọi loại dữ liệu lạ.
    """
    if isinstance(expected_manifest_id, bool) or not isinstance(expected_manifest_id, str) or not expected_manifest_id.strip():
        raise ValueError("expected_manifest_id phải là chuỗi không rỗng.")
    if isinstance(expected_snapshot_id, bool) or not isinstance(expected_snapshot_id, str) or not expected_snapshot_id.strip():
        raise ValueError("expected_snapshot_id phải là chuỗi không rỗng.")
    if isinstance(expected_batch_id, bool) or not isinstance(expected_batch_id, str) or not expected_batch_id.strip():
        raise ValueError("expected_batch_id phải là chuỗi không rỗng.")
    if not isinstance(associate_with_current_snapshot, bool):
        raise ValueError("associate_with_current_snapshot phải là kiểu boolean.")

    if not isinstance(payload, dict):
        return BatchDraftValidationResult(
            ok=False,
            error_code=ERR_MALFORMED_JSON,
            error_message="Dữ liệu payload phải là một dict Object.",
        )

    # 1. Kiểm tra các container lạ không được hỗ trợ (ngăn routing nhầm vào Slot 1)
    unsupported_keys = ("video_plan", "kinetic_plan", "dual_plan", "generic_plan")
    if any(k in payload for k in unsupported_keys):
        return BatchDraftValidationResult(
            ok=False,
            error_code=ERR_UNSUPPORTED_CONTAINER,
            error_message="Payload chứa container kịch bản không hợp lệ cho bản thảo phân lô.",
        )

    # 2. Kiểm tra schema_version nếu có
    if "schema_version" in payload:
        ver = payload["schema_version"]
        if isinstance(ver, bool) or not isinstance(ver, int) or ver != 1:
            return BatchDraftValidationResult(
                ok=False,
                error_code=ERR_UNSUPPORTED_SCHEMA_VERSION,
                error_message=f"Chỉ hỗ trợ schema_version=1 (nhận được: {ver!r}).",
            )

    # 3. Kiểm tra Manifest ID (So khớp chính xác raw value, không tự động strip để match)
    manifest_id = payload.get("manifest_id")
    if isinstance(manifest_id, bool) or not isinstance(manifest_id, str) or not manifest_id.strip():
        return BatchDraftValidationResult(
            ok=False,
            error_code=ERR_MANIFEST_MISMATCH,
            error_message="Thiếu trường 'manifest_id' trong bản thảo.",
        )
    if manifest_id != expected_manifest_id:
        return BatchDraftValidationResult(
            ok=False,
            error_code=ERR_MANIFEST_MISMATCH,
            error_message=f"Lệch Manifest ID: Bản thảo thuộc manifest '{manifest_id}', nhưng dự án hiện tại là '{expected_manifest_id}'.",
        )

    # 4. Kiểm tra Batch ID (So khớp chính xác raw value)
    batch_id = payload.get("batch_id")
    if isinstance(batch_id, bool) or not isinstance(batch_id, str) or not batch_id.strip():
        return BatchDraftValidationResult(
            ok=False,
            error_code=ERR_BATCH_ID_MISMATCH,
            error_message="Thiếu trường 'batch_id' trong bản thảo.",
        )
    if batch_id != expected_batch_id:
        return BatchDraftValidationResult(
            ok=False,
            error_code=ERR_BATCH_ID_MISMATCH,
            error_message=f"Lệch Batch ID: Kịch bản được tạo cho Lô '{batch_id}', không khớp với Lô đang chọn '{expected_batch_id}'.",
        )

    # 5. Kiểm tra Snapshot ID & Xác định Binding Mode
    if "snapshot_id" in payload:
        snapshot_id_val = payload["snapshot_id"]
        if isinstance(snapshot_id_val, bool) or not isinstance(snapshot_id_val, str) or not snapshot_id_val.strip():
            return BatchDraftValidationResult(
                ok=False,
                error_code=ERR_STALE_SNAPSHOT,
                error_message=f"Trường 'snapshot_id' được cung cấp không hợp lệ: {snapshot_id_val!r}.",
            )
        if snapshot_id_val != expected_snapshot_id:
            return BatchDraftValidationResult(
                ok=False,
                error_code=ERR_STALE_SNAPSHOT,
                error_message=f"Kịch bản được tạo cho phiên phân lô cũ ({snapshot_id_val} != {expected_snapshot_id}). Vui lòng sao chép lại prompt mới.",
            )
        binding_mode = "echo_matched"
    else:
        # "snapshot_id" hoàn toàn vắng mặt trong payload
        if not associate_with_current_snapshot:
            return BatchDraftValidationResult(
                ok=False,
                error_code=ERR_MISSING_SNAPSHOT_ID,
                error_message="Kịch bản không có snapshot_id đối soát. Hãy bấm nút 'Nạp Bản Thảo Cho Lô Đang Chọn' để xác nhận gắn vào phiên hiện tại.",
            )
        binding_mode = "user_associated_current_snapshot"

    # 6. Kiểm tra Batch Strategy Summary (Giữ toàn bộ nội dung lossless trong giới hạn payload)
    summary = payload.get("batch_strategy_summary")
    if isinstance(summary, bool) or not isinstance(summary, str) or not summary.strip():
        return BatchDraftValidationResult(
            ok=False,
            error_code=ERR_MISSING_BATCH_SUMMARY,
            error_message="Thiếu tóm tắt chiến lược biên tập lô ('batch_strategy_summary').",
        )
    summary_clean = summary.strip()

    # 7. Kiểm tra nguồn geometry của lô
    ranges_tuple = tuple(source_ranges)
    if not ranges_tuple:
        return BatchDraftValidationResult(
            ok=False,
            error_code=ERR_MISSING_PREPARED_GEOMETRY,
            error_message="Lô hiện tại chưa có thông tin hình học nguồn (source ranges). Vui lòng bấm 'Phân Lô Kịch Bản' lại.",
        )

    ranges_by_clip: Dict[str, List[Tuple[int, BatchSourceRange]]] = {}
    for idx, r in enumerate(ranges_tuple):
        ranges_by_clip.setdefault(r.clip_id, []).append((idx, r))

    # 8. Thẩm định danh sách timeline_segments
    raw_segments = payload.get("timeline_segments")
    if raw_segments is None or not isinstance(raw_segments, list):
        return BatchDraftValidationResult(
            ok=False,
            error_code=ERR_MALFORMED_SEGMENTS,
            error_message="Trường 'timeline_segments' bắt buộc phải là một danh sách (List).",
        )

    validated_cuts: List[ValidatedSegmentCut] = []
    total_duration = 0.0

    for i, seg in enumerate(raw_segments):
        if not isinstance(seg, dict):
            return BatchDraftValidationResult(
                ok=False,
                error_code=ERR_MALFORMED_SEGMENTS,
                error_message=f"Phân đoạn #{i + 1} không phải là một Object dict.",
            )

        cid = seg.get("clip_id")
        if isinstance(cid, bool) or not isinstance(cid, str) or not cid.strip():
            return BatchDraftValidationResult(
                ok=False,
                error_code=ERR_FOREIGN_CLIP_ID,
                error_message=f"Phân đoạn #{i + 1} thiếu hoặc có clip_id không hợp lệ.",
            )
        cid = cid.strip()
        if cid not in ranges_by_clip:
            return BatchDraftValidationResult(
                ok=False,
                error_code=ERR_FOREIGN_CLIP_ID,
                error_message=f"Phân đoạn #{i + 1} chứa clip_id '{cid}' không thuộc danh sách clip của Lô {expected_batch_id}.",
            )

        start_raw = seg.get("start_sec")
        end_raw = seg.get("end_sec")

        # Nghiêm ngặt: Cấm kiểu bool (kể cả True/False kế thừa int)
        if isinstance(start_raw, bool) or not isinstance(start_raw, (int, float)):
            return BatchDraftValidationResult(
                ok=False,
                error_code=ERR_NONFINITE_TIMESTAMP,
                error_message=f"Phân đoạn #{i + 1} có start_sec không phải là số: {start_raw!r}",
            )
        if isinstance(end_raw, bool) or not isinstance(end_raw, (int, float)):
            return BatchDraftValidationResult(
                ok=False,
                error_code=ERR_NONFINITE_TIMESTAMP,
                error_message=f"Phân đoạn #{i + 1} có end_sec không phải là số: {end_raw!r}",
            )

        try:
            start = float(start_raw)
            end = float(end_raw)
        except (OverflowError, ValueError) as o_err:
            return BatchDraftValidationResult(
                ok=False,
                error_code=ERR_NONFINITE_TIMESTAMP,
                error_message=f"Phân đoạn #{i + 1} có mốc thời gian vượt quá giới hạn số thực: {o_err}",
            )

        if not math.isfinite(start) or not math.isfinite(end) or start < 0.0:
            return BatchDraftValidationResult(
                ok=False,
                error_code=ERR_NONFINITE_TIMESTAMP,
                error_message=f"Phân đoạn #{i + 1} có mốc thời gian không hợp lệ (start={start}, end={end}).",
            )

        if start >= end:
            return BatchDraftValidationResult(
                ok=False,
                error_code=ERR_REVERSED_OR_ZERO_DURATION,
                error_message=f"Phân đoạn #{i + 1} có start_sec >= end_sec ({start} >= {end}).",
            )

        # Kiểm tra hình học: Bắt buộc nằm trọn trong MỘT partition range của clip đó
        # Không cho phép dung sai epsilon, không cho phép bắc cầu qua gap
        matched_idx = -1
        matched_range: Optional[BatchSourceRange] = None

        for r_idx, r in ranges_by_clip[cid]:
            if r.source_in_sec <= start and end <= r.source_out_sec:
                matched_idx = r_idx
                matched_range = r
                break

        if matched_range is None:
            clip_ranges_str = ", ".join(f"[{r.source_in_sec}..{r.source_out_sec}]" for _, r in ranges_by_clip[cid])
            return BatchDraftValidationResult(
                ok=False,
                error_code=ERR_OUT_OF_PARTITION_BOUNDS,
                error_message=f"Phân đoạn #{i + 1} [{start}..{end}] vượt ngoài khung phân mảnh cho phép của clip '{cid}': {clip_ranges_str}.",
            )

        # Kiểm tra action
        if "action" in seg:
            raw_action = seg["action"]
            if isinstance(raw_action, bool) or not isinstance(raw_action, str):
                return BatchDraftValidationResult(
                    ok=False,
                    error_code=ERR_UNSUPPORTED_ACTION,
                    error_message=f"Phân đoạn #{i + 1} có action không phải là chuỗi: {raw_action!r}",
                )
            act = raw_action.strip().lower()
            if act not in ("keep", "cut", "speedup"):
                return BatchDraftValidationResult(
                    ok=False,
                    error_code=ERR_UNSUPPORTED_ACTION,
                    error_message=f"Phân đoạn #{i + 1} có hành động '{act}' không được hỗ trợ (chỉ chấp nhận: keep, cut, speedup).",
                )
        else:
            act = "keep"

        # Kiểm tra speed theo action
        speed_val = 1.0
        if act in ("keep", "cut"):
            if "speed" in seg:
                raw_speed = seg["speed"]
                if isinstance(raw_speed, bool) or not isinstance(raw_speed, (int, float)):
                    return BatchDraftValidationResult(
                        ok=False,
                        error_code=ERR_INVALID_SPEED,
                        error_message=f"Phân đoạn #{i + 1} có speed không hợp lệ cho action '{act}': {raw_speed!r}",
                    )
                try:
                    s_float = float(raw_speed)
                except (OverflowError, ValueError):
                    return BatchDraftValidationResult(
                        ok=False,
                        error_code=ERR_INVALID_SPEED,
                        error_message=f"Phân đoạn #{i + 1} có speed vượt quá giới hạn số thực.",
                    )
                if not math.isfinite(s_float) or s_float != 1.0:
                    return BatchDraftValidationResult(
                        ok=False,
                        error_code=ERR_INVALID_SPEED,
                        error_message=f"Phân đoạn #{i + 1} có action '{act}' nhưng speed={s_float} (phải là 1.0).",
                    )
                speed_val = 1.0
        elif act == "speedup":
            if "speed" not in seg:
                return BatchDraftValidationResult(
                    ok=False,
                    error_code=ERR_INVALID_SPEED,
                    error_message=f"Phân đoạn #{i + 1} speedup thiếu trường 'speed'.",
                )
            raw_speed = seg["speed"]
            if isinstance(raw_speed, bool) or not isinstance(raw_speed, (int, float)):
                return BatchDraftValidationResult(
                    ok=False,
                    error_code=ERR_INVALID_SPEED,
                    error_message=f"Phân đoạn #{i + 1} speedup có speed không phải là số: {raw_speed!r}",
                )
            try:
                s_float = float(raw_speed)
            except (OverflowError, ValueError):
                return BatchDraftValidationResult(
                    ok=False,
                    error_code=ERR_INVALID_SPEED,
                    error_message=f"Phân đoạn #{i + 1} speedup có speed vượt quá giới hạn số thực.",
                )
            if not math.isfinite(s_float) or s_float <= 1.0 or s_float > 16.0:
                return BatchDraftValidationResult(
                    ok=False,
                    error_code=ERR_INVALID_SPEED,
                    error_message=f"Phân đoạn #{i + 1} speedup có speed={s_float} không hợp lệ (phải 1.0 < speed <= 16.0).",
                )
            speed_val = s_float

        # Kiểm tra source_window_ref nếu có
        clean_win_ref = None
        if "source_window_ref" in seg and seg["source_window_ref"] is not None:
            win_ref = seg["source_window_ref"]
            if isinstance(win_ref, bool) or not isinstance(win_ref, str):
                return BatchDraftValidationResult(
                    ok=False,
                    error_code=ERR_INVALID_WINDOW_REF,
                    error_message=f"Phân đoạn #{i + 1} có source_window_ref không phải là chuỗi: {win_ref!r}",
                )
            if win_ref.strip():
                clean_win_ref = win_ref.strip()
                if clean_win_ref not in matched_range.window_ids:
                    return BatchDraftValidationResult(
                        ok=False,
                        error_code=ERR_INVALID_WINDOW_REF,
                        error_message=f"Phân đoạn #{i + 1} tham chiếu source_window_ref '{clean_win_ref}' không tồn tại trong window_ids của phân mảnh.",
                    )

        role_str = ""
        if "role" in seg and seg["role"] is not None:
            raw_role = seg["role"]
            if isinstance(raw_role, bool) or not isinstance(raw_role, str):
                return BatchDraftValidationResult(
                    ok=False,
                    error_code=ERR_MALFORMED_SEGMENTS,
                    error_message=f"Phân đoạn #{i + 1} có role không phải là chuỗi: {raw_role!r}",
                )
            role_str = raw_role

        note_str = ""
        if "note" in seg and seg["note"] is not None:
            raw_note = seg["note"]
            if isinstance(raw_note, bool) or not isinstance(raw_note, str):
                return BatchDraftValidationResult(
                    ok=False,
                    error_code=ERR_MALFORMED_SEGMENTS,
                    error_message=f"Phân đoạn #{i + 1} có note không phải là chuỗi: {raw_note!r}",
                )
            note_str = raw_note

        cut_entry = ValidatedSegmentCut(
            clip_id=cid,
            start_sec=start,
            end_sec=end,
            action=act,
            speed=speed_val,
            role=role_str,
            note=note_str,
            source_window_ref=clean_win_ref,
            matched_range_index=matched_idx,
        )
        validated_cuts.append(cut_entry)
        total_duration += (end - start)

    # 9. Tính toán SHA256 và Canonical Draft Digest
    now_iso = datetime.now(timezone.utc).isoformat()
    raw_sha = hashlib.sha256(raw_text.encode("utf-8")).hexdigest() if raw_text else hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()

    canonical_repr = {
        "manifest_id": expected_manifest_id,
        "batch_id": expected_batch_id,
        "snapshot_id": expected_snapshot_id,
        "binding_mode": binding_mode,
        "batch_strategy_summary": summary_clean,
        "segments": [
            {
                "clip_id": c.clip_id,
                "start_sec": c.start_sec,
                "end_sec": c.end_sec,
                "action": c.action,
                "speed": c.speed,
                "role": c.role,
                "note": c.note,
                "source_window_ref": c.source_window_ref,
                "matched_range_index": c.matched_range_index,
            }
            for c in validated_cuts
        ],
        "source_ranges": [
            {
                "clip_id": r.clip_id,
                "source_in_sec": r.source_in_sec,
                "source_out_sec": r.source_out_sec,
                "partition_index": r.partition_index,
                "total_partitions": r.total_partitions,
                "window_ids": list(r.window_ids),
            }
            for r in ranges_tuple
        ],
    }
    canonical_bytes = json.dumps(canonical_repr, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    draft_digest = hashlib.sha256(canonical_bytes).hexdigest()

    draft_entry = ValidatedBatchDraftEntry(
        manifest_id=expected_manifest_id,
        batch_id=expected_batch_id,
        snapshot_id=expected_snapshot_id,
        received_at=now_iso,
        raw_payload_sha256=raw_sha,
        draft_digest=draft_digest,
        batch_strategy_summary=summary_clean,
        segments=tuple(validated_cuts),
        total_cuts_count=len(validated_cuts),
        total_selected_duration_sec=total_duration,
        binding_mode=binding_mode,
        source_ranges=ranges_tuple,
    )

    return BatchDraftValidationResult(ok=True, entry=draft_entry)


# =============================================================================
# IN-MEMORY TERMINAL INTEGRITY GATE
# =============================================================================

def verify_validated_entry_integrity(
    raw_entry_or_dict: Any,
    *,
    expected_manifest_id: str,
    expected_snapshot_id: str,
    expected_batch_id: str,
    expected_source_ranges: Sequence[BatchSourceRange],
    expected_binding_mode: str,
    expected_draft_digest: str,
    expected_raw_payload_sha256: str,
) -> Optional[ValidatedBatchDraftEntry]:
    """Cổng đối soát toàn vẹn hình học và digest trong RAM tại terminal callback.

    Xác minh bản ghi kết quả không bị can thiệp (ví dụ mốc thời gian ngoài khung end_sec=9999,
    lệch IDs, sai counters, sai digest).
    Trả về ValidatedBatchDraftEntry hợp lệ hoặc None nếu bị sai lệch/giả mạo.
    """
    if raw_entry_or_dict is None:
        return None

    if isinstance(raw_entry_or_dict, ValidatedBatchDraftEntry):
        d = raw_entry_or_dict.to_dict()
    elif isinstance(raw_entry_or_dict, dict):
        d = raw_entry_or_dict
    else:
        return None

    if d.get("manifest_id") != expected_manifest_id:
        return None
    if d.get("snapshot_id") != expected_snapshot_id:
        return None
    if d.get("batch_id") != expected_batch_id:
        return None
    if d.get("binding_mode") != expected_binding_mode:
        return None
    if d.get("draft_digest") != expected_draft_digest:
        return None
    if d.get("raw_payload_sha256") != expected_raw_payload_sha256:
        return None

    summary = d.get("batch_strategy_summary")
    if not isinstance(summary, str) or not summary.strip():
        return None
    summary_clean = summary.strip()

    received_at = d.get("received_at")
    if not isinstance(received_at, str) or not received_at.strip():
        return None

    ranges_tuple = tuple(expected_source_ranges)
    if not ranges_tuple:
        return None

    ranges_by_clip: Dict[str, List[Tuple[int, BatchSourceRange]]] = {}
    for idx, r in enumerate(ranges_tuple):
        ranges_by_clip.setdefault(r.clip_id, []).append((idx, r))

    raw_segs = d.get("segments")
    if not isinstance(raw_segs, (list, tuple)):
        return None

    verified_cuts: List[ValidatedSegmentCut] = []
    total_dur = 0.0

    for seg in raw_segs:
        if isinstance(seg, ValidatedSegmentCut):
            s_dict = seg.to_dict()
        elif isinstance(seg, dict):
            s_dict = seg
        else:
            return None

        cid = s_dict.get("clip_id")
        if not isinstance(cid, str) or cid not in ranges_by_clip:
            return None

        s_in = s_dict.get("start_sec")
        s_out = s_dict.get("end_sec")
        if isinstance(s_in, bool) or isinstance(s_out, bool) or not isinstance(s_in, (int, float)) or not isinstance(s_out, (int, float)):
            return None
        try:
            start = float(s_in)
            end = float(s_out)
        except (OverflowError, ValueError):
            return None
        if not math.isfinite(start) or not math.isfinite(end) or start < 0.0 or start >= end:
            return None

        matched_idx = -1
        matched_r = None
        for r_idx, r in ranges_by_clip[cid]:
            if r.source_in_sec <= start and end <= r.source_out_sec:
                matched_idx = r_idx
                matched_r = r
                break
        if matched_r is None:
            # Vượt ngoài khung phân mảnh (ví dụ end_sec=9999) -> TỪ CHỐI
            return None

        act = s_dict.get("action", "keep")
        if isinstance(act, bool) or not isinstance(act, str) or act not in ("keep", "cut", "speedup"):
            return None

        speed_raw = s_dict.get("speed")
        if speed_raw is not None:
            if isinstance(speed_raw, bool) or not isinstance(speed_raw, (int, float)):
                return None
            try:
                speed_val = float(speed_raw)
            except (OverflowError, ValueError):
                return None
        else:
            speed_val = 1.0

        if not math.isfinite(speed_val):
            return None
        if act in ("keep", "cut") and speed_val != 1.0:
            return None
        if act == "speedup" and not (1.0 < speed_val <= 16.0):
            return None

        win_ref = s_dict.get("source_window_ref")
        clean_win_ref = None
        if win_ref is not None:
            if isinstance(win_ref, bool) or not isinstance(win_ref, str):
                return None
            if win_ref.strip():
                clean_win_ref = win_ref.strip()
                if clean_win_ref not in matched_r.window_ids:
                    return None

        role_raw = s_dict.get("role")
        if role_raw is not None:
            if isinstance(role_raw, bool) or not isinstance(role_raw, str):
                return None
            role_str = role_raw
        else:
            role_str = ""

        note_raw = s_dict.get("note")
        if note_raw is not None:
            if isinstance(note_raw, bool) or not isinstance(note_raw, str):
                return None
            note_str = note_raw
        else:
            note_str = ""

        verified_cuts.append(
            ValidatedSegmentCut(
                clip_id=cid,
                start_sec=start,
                end_sec=end,
                action=act,
                speed=speed_val,
                role=role_str,
                note=note_str,
                source_window_ref=clean_win_ref,
                matched_range_index=matched_idx,
            )
        )
        total_dur += (end - start)

    raw_cuts_count = d.get("total_cuts_count")
    if isinstance(raw_cuts_count, bool) or not isinstance(raw_cuts_count, int) or raw_cuts_count != len(verified_cuts):
        return None

    reported_dur = d.get("total_selected_duration_sec")
    if reported_dur is not None:
        if isinstance(reported_dur, bool) or not isinstance(reported_dur, (int, float)):
            return None
        try:
            r_dur = float(reported_dur)
        except (OverflowError, ValueError):
            return None
        if not math.isfinite(r_dur) or abs(r_dur - total_dur) > 1e-4:
            return None

    canonical_repr = {
        "manifest_id": expected_manifest_id,
        "batch_id": expected_batch_id,
        "snapshot_id": expected_snapshot_id,
        "binding_mode": expected_binding_mode,
        "batch_strategy_summary": summary_clean,
        "segments": [
            {
                "clip_id": c.clip_id,
                "start_sec": c.start_sec,
                "end_sec": c.end_sec,
                "action": c.action,
                "speed": c.speed,
                "role": c.role,
                "note": c.note,
                "source_window_ref": c.source_window_ref,
                "matched_range_index": c.matched_range_index,
            }
            for c in verified_cuts
        ],
        "source_ranges": [
            {
                "clip_id": r.clip_id,
                "source_in_sec": r.source_in_sec,
                "source_out_sec": r.source_out_sec,
                "partition_index": r.partition_index,
                "total_partitions": r.total_partitions,
                "window_ids": list(r.window_ids),
            }
            for r in ranges_tuple
        ],
    }
    canonical_bytes = json.dumps(canonical_repr, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    computed_digest = hashlib.sha256(canonical_bytes).hexdigest()

    if computed_digest != expected_draft_digest:
        return None

    return ValidatedBatchDraftEntry(
        manifest_id=expected_manifest_id,
        batch_id=expected_batch_id,
        snapshot_id=expected_snapshot_id,
        received_at=received_at,
        raw_payload_sha256=expected_raw_payload_sha256,
        draft_digest=computed_digest,
        batch_strategy_summary=summary_clean,
        segments=tuple(verified_cuts),
        total_cuts_count=len(verified_cuts),
        total_selected_duration_sec=total_dur,
        binding_mode=expected_binding_mode,
        source_ranges=ranges_tuple,
    )
