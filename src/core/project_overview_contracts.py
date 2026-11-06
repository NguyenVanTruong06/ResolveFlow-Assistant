"""
src/core/project_overview_contracts.py
=============================================================================
Pure Core Value Objects & Formatter cho Kịch Bản Tổng Quan Dự Án (Project Overview).
- Zero Qt / UI / IO / Network / Model dependencies.
- Bounded UTF-8 prompt rendering (mặc định trần 32,768 bytes).
- Full provenance preservation thông qua verify_validated_entry_integrity.
- Deterministic ordering theo batch_index tăng dần.
- Lossless JSON serialization cho dữ liệu ngữ cảnh và output schema example.
- Strict boundary validation & immutable context ownership (zero silent coercion).
- Phân biệt rõ rệt ba tầng thông tin:
  * MEASURED_SOURCE_BOUNDS: Sự thật vật lý từ file và scan cache (không re-stat đĩa).
  * AI_DRAFT_STRATEGY: Đề xuất biên tập cấp lô từ AI bên ngoài.
  * USER_CHOICES: Lựa chọn phong cách và quyết định của người dùng.
=============================================================================
"""

import math
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Tuple, Optional, Any, Dict, List, Union

from src.core.batch_draft_contracts import (
    BatchSourceRange,
    ValidatedSegmentCut,
    ValidatedBatchDraftEntry,
    verify_validated_entry_integrity,
)
from src.core.contracts.edit_plan_contracts import (
    CreativeContext,
    normalize_creative_context,
    creative_context_to_dict,
    compute_creative_context_signature,
)

FORMATTER_POLICY = "project_overview_v1"

ERR_OVERVIEW_INVALID_CONTEXT = "ERR_OVERVIEW_INVALID_CONTEXT"
ERR_OVERVIEW_NO_DRAFTS = "ERR_OVERVIEW_NO_DRAFTS"
ERR_OVERVIEW_BUDGET_OVERFLOW = "ERR_OVERVIEW_BUDGET_OVERFLOW"

VALID_BINDING_MODES = ("echo_matched", "user_associated_current_snapshot")


# =============================================================================
# HELPER FUNCTIONS (PURE CORE IMMUTABLE OWNERSHIP & VALIDATION)
# =============================================================================

def _is_valid_utf8_string(val: Any) -> bool:
    """Kiểm tra một giá trị có phải chuỗi văn bản và có thể mã hóa UTF-8 an toàn (chặn lone surrogates)."""
    if isinstance(val, bool) or not isinstance(val, str):
        return False
    try:
        val.encode("utf-8")
        return True
    except (UnicodeEncodeError, UnicodeError):
        return False


def _has_initialized_field(instance: Any, field_name: str) -> bool:
    """Kiểm tra trường thực sự tồn tại trong instance storage, không fallback về class default."""
    try:
        inst_dict = object.__getattribute__(instance, "__dict__")
        return field_name in inst_dict
    except AttributeError:
        try:
            object.__getattribute__(instance, field_name)
            return True
        except AttributeError:
            return False


def _reconstruct_owned_range(r: Any) -> BatchSourceRange:
    """Tái tạo BatchSourceRange thành đối tượng thuộc sở hữu bất biến của context."""
    if not isinstance(r, BatchSourceRange):
        raise ValueError(f"Mục trong source_ranges phải là BatchSourceRange: {r!r}")

    # window_ids: bắt buộc tuple hoặc list hợp lệ, cấm bool/None/{}/'bad'
    w_raw = getattr(r, "window_ids", None)
    if isinstance(w_raw, bool) or not isinstance(w_raw, (tuple, list)):
        raise ValueError(f"window_ids trong range {getattr(r, 'clip_id', '')!r} phải là tuple hoặc list: {w_raw!r}")

    w_tuple = tuple(w_raw)
    for w in w_tuple:
        if not _is_valid_utf8_string(w) or not w.strip():
            raise ValueError(f"window_id phải là chuỗi không rỗng hợp lệ: {w!r}")

    return replace(r, window_ids=w_tuple)


def _reconstruct_owned_batch(b: Any) -> "BatchPartitionRef":
    """Tái tạo BatchPartitionRef và các dải nguồn con thành đối tượng bất biến độc lập."""
    if not isinstance(b, BatchPartitionRef):
        raise ValueError(f"batches phải chứa BatchPartitionRef: {b!r}")
    if isinstance(b.source_ranges, bool) or not isinstance(b.source_ranges, (tuple, list)) or not b.source_ranges:
        raise ValueError(f"source_ranges của lô {getattr(b, 'batch_id', '')!r} phải là sequence không rỗng: {getattr(b, 'source_ranges', None)!r}")
    r_tuple = tuple(_reconstruct_owned_range(r) for r in b.source_ranges)
    return replace(b, source_ranges=r_tuple)


def _reconstruct_owned_entry(d: Any) -> ValidatedBatchDraftEntry:
    """Tái tạo ValidatedBatchDraftEntry và các collection con thành immutable tuples."""
    if not isinstance(d, ValidatedBatchDraftEntry):
        raise ValueError(f"received_drafts phải chứa ValidatedBatchDraftEntry: {d!r}")
    if isinstance(d.segments, bool) or not isinstance(d.segments, (tuple, list)):
        raise ValueError(f"segments của bản thảo {getattr(d, 'batch_id', '')!r} phải là sequence: {getattr(d, 'segments', None)!r}")
    if isinstance(d.source_ranges, bool) or not isinstance(d.source_ranges, (tuple, list)) or not d.source_ranges:
        raise ValueError(f"source_ranges của bản thảo {getattr(d, 'batch_id', '')!r} phải là sequence không rỗng: {getattr(d, 'source_ranges', None)!r}")

    seg_tuple = tuple(d.segments)
    range_tuple = tuple(_reconstruct_owned_range(r) for r in d.source_ranges)
    return replace(d, segments=seg_tuple, source_ranges=range_tuple)


# =============================================================================
# FROZEN VALUE OBJECTS (DTOs)
# =============================================================================

@dataclass(frozen=True)
class BatchPartitionRef:
    """Tham chiếu phân vùng nguồn bất biến của một lô thuộc quyền sở hữu ứng dụng."""
    batch_id: str
    batch_index: int
    source_ranges: Tuple[BatchSourceRange, ...]

    def __post_init__(self):
        if not _is_valid_utf8_string(self.batch_id) or not self.batch_id.strip():
            raise ValueError(f"batch_id phải là chuỗi không rỗng hợp lệ: {self.batch_id!r}")
        if isinstance(self.batch_index, bool) or not isinstance(self.batch_index, int) or self.batch_index < 0:
            raise ValueError(f"batch_index phải là số nguyên >= 0: {self.batch_index!r}")
        if isinstance(self.source_ranges, bool) or not isinstance(self.source_ranges, (tuple, list)) or not self.source_ranges:
            raise ValueError(f"source_ranges phải là tuple hoặc list không rỗng: {self.source_ranges!r}")
        owned_ranges = tuple(_reconstruct_owned_range(r) for r in self.source_ranges)
        object.__setattr__(self, "source_ranges", owned_ranges)

    @property
    def clip_ids(self) -> Tuple[str, ...]:
        """Danh sách clip_id duy nhất theo thứ tự xuất hiện."""
        seen = []
        for r in self.source_ranges:
            if r.clip_id not in seen:
                seen.append(r.clip_id)
        return tuple(seen)

    @property
    def distinct_clip_count(self) -> int:
        return len(self.clip_ids)

    @property
    def total_duration_sec(self) -> float:
        return sum(float(r.source_out_sec) - float(r.source_in_sec) for r in self.source_ranges)


@dataclass(frozen=True)
class OverviewUnaccountedClip:
    """Mục clip không thể phân lô (Bất biến, Pure Core DTO)."""
    clip_id: str
    reason: str
    message: str

    def __post_init__(self):
        if not _is_valid_utf8_string(self.clip_id) or not self.clip_id.strip():
            raise ValueError(f"clip_id phải là chuỗi không rỗng hợp lệ: {self.clip_id!r}")
        if not _is_valid_utf8_string(self.reason):
            raise ValueError(f"reason phải là chuỗi văn bản hợp lệ: {self.reason!r}")
        if not _is_valid_utf8_string(self.message):
            raise ValueError(f"message phải là chuỗi văn bản hợp lệ: {self.message!r}")


@dataclass(frozen=True)
class ProjectOverviewContextInput:
    """Ngữ cảnh đầu vào hoàn chỉnh cho bộ định dạng tổng quan dự án (Sở hữu bất biến)."""
    manifest_id: str
    project_name: str
    snapshot_id: str
    batches: Tuple[BatchPartitionRef, ...]
    received_drafts: Tuple[ValidatedBatchDraftEntry, ...] = ()
    unaccounted_clips: Tuple[OverviewUnaccountedClip, ...] = ()
    style_intent: str = ""
    max_prompt_bytes: int = 32768
    creative_context: Optional[Union[Mapping[str, Any], CreativeContext]] = None

    def __post_init__(self):
        # batches: bắt buộc sequence rõ ràng, không nhận bool/None/mapping/int
        if isinstance(self.batches, bool) or not isinstance(self.batches, (tuple, list)):
            raise ValueError(f"batches phải là tuple hoặc list chứa BatchPartitionRef: {self.batches!r}")
        object.__setattr__(self, "batches", tuple(_reconstruct_owned_batch(b) for b in self.batches))

        # received_drafts: bắt buộc sequence rõ ràng, không nhận bool/None/mapping
        if isinstance(self.received_drafts, bool) or not isinstance(self.received_drafts, (tuple, list)):
            raise ValueError(f"received_drafts phải là tuple hoặc list chứa ValidatedBatchDraftEntry: {self.received_drafts!r}")
        owned_drafts = []
        for d in self.received_drafts:
            if isinstance(d, ValidatedBatchDraftEntry):
                owned_drafts.append(_reconstruct_owned_entry(d))
            else:
                owned_drafts.append(d)
        object.__setattr__(self, "received_drafts", tuple(owned_drafts))

        # unaccounted_clips: bắt buộc sequence rõ ràng, không nhận bool/None/mapping
        if isinstance(self.unaccounted_clips, bool) or not isinstance(self.unaccounted_clips, (tuple, list)):
            raise ValueError(f"unaccounted_clips phải là tuple hoặc list chứa OverviewUnaccountedClip: {self.unaccounted_clips!r}")
        for u in self.unaccounted_clips:
            if not isinstance(u, OverviewUnaccountedClip):
                raise ValueError(f"Mỗi phần tử trong unaccounted_clips phải là OverviewUnaccountedClip: {u!r}")
        object.__setattr__(self, "unaccounted_clips", tuple(self.unaccounted_clips))

        # creative_context: None hoặc Mapping hoặc CreativeContext (chuẩn hóa và sở hữu độc lập)
        raw_cc = self.creative_context
        if raw_cc is not None:
            if isinstance(raw_cc, bool) or not isinstance(raw_cc, (Mapping, CreativeContext)):
                raise ValueError(f"creative_context phải là Mapping hoặc CreativeContext, nhận: {type(raw_cc).__name__}")
            try:
                owned_cc = normalize_creative_context(raw_cc)
            except Exception as e:
                raise ValueError(f"creative_context không hợp lệ: {e}")
            object.__setattr__(self, "creative_context", owned_cc)


@dataclass(frozen=True)
class ProjectOverviewRenderResult:
    """Kết quả kết xuất kịch bản tổng quan dự án (Bất biến)."""
    ok: bool
    error_code: str
    message: str
    rendered_prompt: str
    rendered_bytes: int
    rendered_sha256: str
    formatter_policy: str
    received_batches_count: int
    total_batches_expected: int
    missing_batch_ids: Tuple[str, ...]
    all_batch_drafts_received: bool
    source_coverage_complete: bool
    required_bytes: int


# =============================================================================
# PURE CORE FORMATTER
# =============================================================================

def render_project_overview_prompt(context_input: ProjectOverviewContextInput) -> ProjectOverviewRenderResult:
    """
    Kết xuất kịch bản tổng quan vĩ mô dự án với các bảo đảm:
    - Bắt buộc kiểm tra boundary của DTO và geometry các lô.
    - Đối chiếu tính toàn vẹn và xuất xứ qua verify_validated_entry_integrity.
    - Sắp xếp thứ tự quyết định luận theo batch_index tăng dần.
    - Tuần tự hóa JSON an toàn cho khối ngữ cảnh và ví dụ output schema.
    - Loại bỏ raw interpolation ngoài JSON block để cách ly cấu trúc prompt.
    - Không làm tròn mốc thời gian nguồn (lossless source bounds).
    - Đo đạc chính xác toàn bộ kích thước UTF-8 bytes (Equality <= max_prompt_bytes).
    - Tràn ngân sách trả về ERR_OVERVIEW_BUDGET_OVERFLOW kèm required_bytes chính xác.
    - Chặn đứng mọi UnicodeEncodeError (trả về ERR_OVERVIEW_INVALID_CONTEXT).
    """
    # 1. Thẩm định cơ bản đối tượng ContextInput
    if not isinstance(context_input, ProjectOverviewContextInput):
        return _make_invalid_result("context_input phải là một đối tượng ProjectOverviewContextInput hợp lệ.")

    m_id = getattr(context_input, "manifest_id", None)
    p_name = getattr(context_input, "project_name", None)
    s_id = getattr(context_input, "snapshot_id", None)
    style = getattr(context_input, "style_intent", "")
    max_bytes = getattr(context_input, "max_prompt_bytes", 32768)

    # Thẩm định an toàn chuỗi văn bản và UTF-8 encodability
    if not _is_valid_utf8_string(m_id) or not m_id.strip():
        return _make_invalid_result("manifest_id phải là chuỗi không rỗng hợp lệ (không chứa lone surrogates).")
    if not _is_valid_utf8_string(p_name):
        return _make_invalid_result("project_name phải là chuỗi văn bản hợp lệ (không chứa lone surrogates).")
    if not _is_valid_utf8_string(s_id) or not s_id.strip():
        return _make_invalid_result("snapshot_id phải là chuỗi không rỗng hợp lệ (không chứa lone surrogates).")
    if not _is_valid_utf8_string(style):
        return _make_invalid_result("style_intent phải là chuỗi văn bản hợp lệ (không chứa lone surrogates).")

    # Kiểm tra max_prompt_bytes: strict int 1..32768, không bool
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or max_bytes < 1
        or max_bytes > 32768
    ):
        return _make_invalid_result(f"max_prompt_bytes phải là số nguyên trong khoảng 1..32768: {max_bytes!r}")

    # Revalidate creative_context from typed DTO
    if not _has_initialized_field(context_input, "creative_context"):
        return _make_invalid_result("Trường creative_context bị thiếu hoặc không được khởi tạo trên context_input.")

    raw_cc = context_input.creative_context
    norm_cc = None
    is_nonempty_cc = False
    if raw_cc is not None:
        if isinstance(raw_cc, bool) or not isinstance(raw_cc, (Mapping, CreativeContext)):
            return _make_invalid_result(f"creative_context phải là Mapping hoặc CreativeContext, nhận: {type(raw_cc).__name__}")
        try:
            norm_cc = normalize_creative_context(raw_cc)
        except Exception as exc:
            return _make_invalid_result(f"creative_context không hợp lệ: {exc}")
        empty_cc = normalize_creative_context({})
        is_nonempty_cc = (norm_cc != empty_cc)

    # 2. Thẩm định danh sách batches
    raw_batches = getattr(context_input, "batches", None)
    if isinstance(raw_batches, bool) or not isinstance(raw_batches, (tuple, list)) or not raw_batches:
        return _make_invalid_result("batches phải là danh sách không rỗng các BatchPartitionRef.")

    batch_by_id: Dict[str, BatchPartitionRef] = {}
    batch_by_idx: Dict[int, BatchPartitionRef] = {}

    for b in raw_batches:
        if not isinstance(b, BatchPartitionRef):
            return _make_invalid_result(f"Mỗi phần tử trong batches phải là BatchPartitionRef: {b!r}")
        if not _is_valid_utf8_string(b.batch_id) or not b.batch_id.strip():
            return _make_invalid_result(f"batch_id không hợp lệ: {b.batch_id!r}")
        if isinstance(b.batch_index, bool) or not isinstance(b.batch_index, int) or b.batch_index < 0:
            return _make_invalid_result(f"batch_index không hợp lệ: {b.batch_index!r}")
        if b.batch_id in batch_by_id:
            return _make_invalid_result(f"Trùng lặp batch_id trong danh sách batches: {b.batch_id!r}")
        if b.batch_index in batch_by_idx:
            return _make_invalid_result(f"Trùng lặp batch_index trong danh sách batches: {b.batch_index!r}")
        batch_by_id[b.batch_id] = b
        batch_by_idx[b.batch_index] = b

        # Revalidate source ranges geometry & strict range types
        if isinstance(b.source_ranges, bool) or not isinstance(b.source_ranges, (tuple, list)) or not b.source_ranges:
            return _make_invalid_result(f"source_ranges không hợp lệ trong lô {b.batch_id}: {b.source_ranges!r}")

        for r in b.source_ranges:
            if not isinstance(r, BatchSourceRange):
                return _make_invalid_result(f"Phân vùng nguồn sai kiểu trong lô {b.batch_id}: {r!r}")
            if not _is_valid_utf8_string(r.clip_id) or not r.clip_id.strip():
                return _make_invalid_result(f"clip_id không hợp lệ trong phân vùng nguồn lô {b.batch_id}: {r.clip_id!r}")
            if (
                isinstance(r.source_in_sec, bool)
                or isinstance(r.source_out_sec, bool)
                or not isinstance(r.source_in_sec, (int, float))
                or not isinstance(r.source_out_sec, (int, float))
            ):
                return _make_invalid_result(f"Mốc thời gian nguồn phải là số: [{r.source_in_sec!r}, {r.source_out_sec!r}]")

            try:
                in_s = float(r.source_in_sec)
                out_s = float(r.source_out_sec)
                if not math.isfinite(in_s) or in_s < 0.0 or not math.isfinite(out_s) or out_s <= in_s:
                    return _make_invalid_result(f"Mốc thời gian nguồn không hợp lệ trong lô {b.batch_id}: [{in_s}, {out_s}]")
            except (OverflowError, ValueError, TypeError) as err:
                return _make_invalid_result(f"Lỗi mốc thời gian nguồn trong lô {b.batch_id}: {err}")

            if (
                isinstance(r.partition_index, bool)
                or not isinstance(r.partition_index, int)
                or isinstance(r.total_partitions, bool)
                or not isinstance(r.total_partitions, int)
                or r.total_partitions <= 0
                or r.partition_index < 0
                or r.partition_index >= r.total_partitions
            ):
                return _make_invalid_result(f"Chỉ số phân vùng không hợp lệ trong lô {b.batch_id}: {r.partition_index}/{r.total_partitions}")

            if isinstance(r.window_ids, bool) or not isinstance(r.window_ids, (tuple, list)):
                return _make_invalid_result(f"window_ids không hợp lệ trong phân vùng nguồn lô {b.batch_id}: {r.window_ids!r}")
            for w in r.window_ids:
                if not _is_valid_utf8_string(w) or not w.strip():
                    return _make_invalid_result(f"window_id không hợp lệ trong phân vùng nguồn lô {b.batch_id}: {w!r}")

    total_expected = len(raw_batches)
    # Kiểm tra tính liên tục của batch_index (0..N-1)
    for expected_i in range(total_expected):
        if expected_i not in batch_by_idx:
            return _make_invalid_result(f"batch_index không liên tục: thiếu index {expected_i} (tổng {total_expected} lô).")

    # Sắp xếp tăng dần theo batch_index
    sorted_batches = tuple(batch_by_idx[i] for i in range(total_expected))

    # 3. Thẩm định unaccounted clips
    raw_unaccounted = getattr(context_input, "unaccounted_clips", None)
    if isinstance(raw_unaccounted, bool) or not isinstance(raw_unaccounted, (tuple, list)):
        return _make_invalid_result("unaccounted_clips phải là tuple hoặc list.")
    for u in raw_unaccounted:
        if not isinstance(u, OverviewUnaccountedClip):
            return _make_invalid_result(f"Mục trong unaccounted_clips phải là OverviewUnaccountedClip: {u!r}")
        if not _is_valid_utf8_string(u.clip_id) or not u.clip_id.strip():
            return _make_invalid_result(f"clip_id không hợp lệ trong unaccounted_clips: {u.clip_id!r}")
        if not _is_valid_utf8_string(u.reason):
            return _make_invalid_result(f"reason không hợp lệ trong unaccounted_clips: {u.reason!r}")
        if not _is_valid_utf8_string(u.message):
            return _make_invalid_result(f"message không hợp lệ trong unaccounted_clips: {u.message!r}")

    # 4. Thẩm định received_drafts
    raw_drafts = getattr(context_input, "received_drafts", None)
    if isinstance(raw_drafts, bool) or not isinstance(raw_drafts, (tuple, list)):
        return _make_invalid_result("received_drafts phải là tuple hoặc list.")

    # Quy tắc: zero drafts -> ERR_OVERVIEW_NO_DRAFTS
    if len(raw_drafts) == 0:
        return ProjectOverviewRenderResult(
            ok=False,
            error_code=ERR_OVERVIEW_NO_DRAFTS,
            message="Chưa có bản thảo phân lô nào được tiếp nhận trong ngữ cảnh dự án.",
            rendered_prompt="",
            rendered_bytes=0,
            rendered_sha256="",
            formatter_policy=FORMATTER_POLICY,
            received_batches_count=0,
            total_batches_expected=total_expected,
            missing_batch_ids=tuple(b.batch_id for b in sorted_batches),
            all_batch_drafts_received=False,
            source_coverage_complete=False,
            required_bytes=0,
        )

    drafts_by_batch_id: Dict[str, ValidatedBatchDraftEntry] = {}
    for d in raw_drafts:
        if not isinstance(d, ValidatedBatchDraftEntry):
            return _make_invalid_result(f"Mục trong received_drafts phải là ValidatedBatchDraftEntry: {d!r}")

        # Thẩm định batch_id kiểu dữ liệu chuỗi TRƯỚC khi tra cứu hash/membership
        if not _is_valid_utf8_string(d.batch_id) or not d.batch_id.strip():
            return _make_invalid_result(f"Bản thảo chứa batch_id không hợp lệ (không phải chuỗi hợp lệ): {d.batch_id!r}")

        # Kiểm tra trùng lặp
        if d.batch_id in drafts_by_batch_id:
            return _make_invalid_result(f"Trùng lặp bản thảo cho cùng batch_id: {d.batch_id!r}")

        # Kiểm tra batch_id có thuộc danh sách phân lô của dự án không
        if d.batch_id not in batch_by_id:
            return _make_invalid_result(f"Bản thảo thuộc batch_id lạ không có trong danh sách phân lô: {d.batch_id!r}")

        # Kiểm tra manifest_id và snapshot_id
        if d.manifest_id != m_id:
            return _make_invalid_result(f"Lệch manifest_id trong bản thảo lô {d.batch_id}: {d.manifest_id} != {m_id}")
        if d.snapshot_id != s_id:
            return _make_invalid_result(f"Lệch snapshot_id trong bản thảo lô {d.batch_id}: {d.snapshot_id} != {s_id}")

        # Kiểm tra kiểu dữ liệu các collection con trước khi gọi to_dict()
        if isinstance(d.segments, bool) or not isinstance(d.segments, (tuple, list)):
            return _make_invalid_result(f"segments của bản thảo lô {d.batch_id} không phải sequence hợp lệ: {d.segments!r}")
        for seg in d.segments:
            if not isinstance(seg, ValidatedSegmentCut):
                return _make_invalid_result(f"segment trong bản thảo lô {d.batch_id} không phải ValidatedSegmentCut: {seg!r}")
            if not _is_valid_utf8_string(seg.clip_id):
                return _make_invalid_result(f"clip_id trong segment của bản thảo lô {d.batch_id} chứa ký tự UTF-8 không hợp lệ.")
            if not _is_valid_utf8_string(seg.action):
                return _make_invalid_result(f"action trong segment của bản thảo lô {d.batch_id} chứa ký tự UTF-8 không hợp lệ.")
            if not _is_valid_utf8_string(seg.role):
                return _make_invalid_result(f"role trong segment của bản thảo lô {d.batch_id} chứa ký tự UTF-8 không hợp lệ.")
            if not _is_valid_utf8_string(seg.note):
                return _make_invalid_result(f"note trong segment của bản thảo lô {d.batch_id} chứa ký tự UTF-8 không hợp lệ.")

        if isinstance(d.source_ranges, bool) or not isinstance(d.source_ranges, (tuple, list)) or not d.source_ranges:
            return _make_invalid_result(f"source_ranges của bản thảo lô {d.batch_id} không phải sequence hợp lệ: {d.source_ranges!r}")
        for sr in d.source_ranges:
            if not isinstance(sr, BatchSourceRange):
                return _make_invalid_result(f"source_range trong bản thảo lô {d.batch_id} không phải BatchSourceRange: {sr!r}")
            if not _is_valid_utf8_string(sr.clip_id) or not sr.clip_id.strip():
                return _make_invalid_result(f"clip_id không hợp lệ trong source_range bản thảo lô {d.batch_id}: {sr.clip_id!r}")
            if isinstance(sr.window_ids, bool) or not isinstance(sr.window_ids, (tuple, list)):
                return _make_invalid_result(f"window_ids không hợp lệ trong source_range bản thảo lô {d.batch_id}: {sr.window_ids!r}")
            for w in sr.window_ids:
                if not _is_valid_utf8_string(w) or not w.strip():
                    return _make_invalid_result(f"window_id không hợp lệ trong source_range bản thảo lô {d.batch_id}: {w!r}")

        # Kiểm tra raw hash và draft digest 64-hex
        if (
            isinstance(d.raw_payload_sha256, bool)
            or not isinstance(d.raw_payload_sha256, str)
            or len(d.raw_payload_sha256) != 64
            or not all(c in "0123456789abcdefABCDEF" for c in d.raw_payload_sha256)
        ):
            return _make_invalid_result(f"raw_payload_sha256 không hợp lệ trong bản thảo lô {d.batch_id}: {d.raw_payload_sha256!r}")

        if (
            isinstance(d.draft_digest, bool)
            or not isinstance(d.draft_digest, str)
            or len(d.draft_digest) != 64
            or not all(c in "0123456789abcdefABCDEF" for c in d.draft_digest)
        ):
            return _make_invalid_result(f"draft_digest không hợp lệ trong bản thảo lô {d.batch_id}: {d.draft_digest!r}")

        if (
            isinstance(d.binding_mode, bool)
            or not isinstance(d.binding_mode, str)
            or d.binding_mode not in VALID_BINDING_MODES
        ):
            return _make_invalid_result(f"binding_mode không hợp lệ trong bản thảo lô {d.batch_id}: {d.binding_mode!r}")

        if not _is_valid_utf8_string(d.batch_strategy_summary):
            return _make_invalid_result(f"batch_strategy_summary chứa ký tự UTF-8 không hợp lệ trong lô {d.batch_id}.")

        # Re-verify tính toàn vẹn ngữ nghĩa qua verify_validated_entry_integrity
        matching_b = batch_by_id[d.batch_id]
        try:
            d_dict = d.to_dict()
        except (TypeError, ValueError, AttributeError) as err:
            return _make_invalid_result(f"Lỗi khi tuần tự hóa bản thảo lô {d.batch_id}: {err}")

        try:
            verified_entry = verify_validated_entry_integrity(
                d_dict,
                expected_manifest_id=m_id,
                expected_snapshot_id=s_id,
                expected_batch_id=d.batch_id,
                expected_source_ranges=matching_b.source_ranges,
                expected_binding_mode=d.binding_mode,
                expected_draft_digest=d.draft_digest,
                expected_raw_payload_sha256=d.raw_payload_sha256,
            )
        except (UnicodeEncodeError, UnicodeError):
            return _make_invalid_result(f"Lỗi mã hóa Unicode khi kiểm tra tính toàn vẹn bản thảo lô {d.batch_id}.")

        if verified_entry is None:
            return _make_invalid_result(f"Bản thảo lô {d.batch_id} không vượt qua kiểm định tính toàn vẹn (integrity gate).")

        drafts_by_batch_id[d.batch_id] = verified_entry

    # 5. Tính toán coverage và missing IDs
    received_count = len(drafts_by_batch_id)
    missing_ids = tuple(b.batch_id for b in sorted_batches if b.batch_id not in drafts_by_batch_id)
    all_drafts_received = (received_count == total_expected)
    source_coverage_complete = (all_drafts_received and len(raw_unaccounted) == 0)

    # 6. Chuẩn bị cấu trúc dữ liệu JSON ngữ cảnh (Context Data Block)
    status_label = "HOÀN TẤT" if all_drafts_received else "BÁN PHẦN (PARTIAL)"
    style_label = style.strip() if style and style.strip() else "Mặc định (Tiêu chuẩn)"

    context_json_dict = {
        "manifest_id": m_id,
        "snapshot_id": s_id,
        "project_name": p_name,
        "style_intent": style_label,
        "status": status_label,
        "received_batches_count": received_count,
        "total_batches_expected": total_expected,
        "all_batch_drafts_received": all_drafts_received,
        "source_coverage_complete": source_coverage_complete,
        "missing_batch_ids": list(missing_ids),
        "unaccounted_clips": [
            {"clip_id": u.clip_id, "reason": u.reason, "message": u.message}
            for u in raw_unaccounted
        ],
        "measured_source_bounds": [
            {
                "batch_id": b.batch_id,
                "batch_index": b.batch_index,
                "total_duration_sec": b.total_duration_sec,
                "source_ranges": [
                    {
                        "clip_id": r.clip_id,
                        "source_in_sec": float(r.source_in_sec),
                        "source_out_sec": float(r.source_out_sec),
                        "partition_index": r.partition_index,
                        "total_partitions": r.total_partitions,
                        "window_ids": list(r.window_ids),
                    }
                    for r in b.source_ranges
                ],
            }
            for b in sorted_batches
        ],
        "received_draft_strategies": [
            {
                "batch_id": b.batch_id,
                "batch_index": b.batch_index,
                "draft_digest": drafts_by_batch_id[b.batch_id].draft_digest,
                "binding_mode": drafts_by_batch_id[b.batch_id].binding_mode,
                "total_cuts_count": drafts_by_batch_id[b.batch_id].total_cuts_count,
                "total_selected_duration_sec": drafts_by_batch_id[b.batch_id].total_selected_duration_sec,
                "batch_strategy_summary": drafts_by_batch_id[b.batch_id].batch_strategy_summary,
            }
            for b in sorted_batches
            if b.batch_id in drafts_by_batch_id
        ],
    }

    if is_nonempty_cc and norm_cc is not None:
        try:
            context_json_dict["creative_context"] = creative_context_to_dict(norm_cc)
        except Exception as exc:
            return _make_invalid_result(f"creative_context không hợp lệ khi chuyển đổi dict: {exc}")

    try:
        context_json_str = json.dumps(context_json_dict, ensure_ascii=False, indent=2, allow_nan=False)
    except (TypeError, ValueError, UnicodeEncodeError) as err:
        return _make_invalid_result(f"Lỗi khi tuần tự hóa JSON khối ngữ cảnh: {err}")

    # Chuẩn bị ví dụ JSON đầu ra (Output Schema Example)
    output_schema_example = {
        "kind": "project_overview_draft",
        "schema_version": 1,
        "manifest_id": m_id,
        "snapshot_id": s_id,
        "macro_narrative": "<mô tả mạch truyện tổng quan toàn dự án>",
        "batch_order": ["<danh sách batch_id theo thứ tự đề xuất (chỉ dùng các batch_id đã nhận)>"],
        "transitions_and_bridges": [
            {
                "from_batch": "<batch_id>",
                "to_batch": "<batch_id>",
                "transition_notes": "<ý tưởng chuyển đoạn giữa hai lô>",
            }
        ],
        "editorial_limitations": "<ghi chú các phần bị khuyết hoặc cần lưu ý do lô thiếu hoặc clip chưa phân lô>",
    }

    try:
        output_schema_str = json.dumps(output_schema_example, ensure_ascii=False, indent=2, allow_nan=False)
    except (TypeError, ValueError, UnicodeEncodeError) as err:
        return _make_invalid_result(f"Lỗi khi tuần tự hóa JSON mẫu đầu ra: {err}")

    # 7. Kết xuất văn bản kịch bản tổng quan quyết định luận (Deterministic Rendering)
    # Toàn bộ dữ liệu động scalar được escape bằng json.dumps cùng dòng để cách ly cấu trúc prompt tuyệt đối
    lines: List[str] = []
    lines.append("=== CHUNDVC PROJECT OVERVIEW PROMPT ===")
    lines.append("Mục tiêu: Tổng hợp kịch bản tổng quan vĩ mô toàn dự án từ các bản thảo phân lô đã được thẩm định.")
    lines.append("")
    lines.append("[THÔNG TIN DỰ ÁN & LỰA CHỌN NGƯỜI DÙNG (USER_CHOICES)]")
    lines.append(f"- Manifest ID: {json.dumps(m_id, ensure_ascii=False)}")
    lines.append(f"- Snapshot ID: {json.dumps(s_id, ensure_ascii=False)}")
    lines.append(f"- Tên dự án: {json.dumps(p_name, ensure_ascii=False)}")
    lines.append(f"- Tiến độ tiếp nhận: Đã nhận {received_count}/{total_expected} bản thảo lô ({status_label})")
    lines.append(f"- Định hướng phong cách: {json.dumps(style_label, ensure_ascii=False)}")

    if raw_unaccounted:
        lines.append("")
        lines.append("[CẢNH BÁO NGUỒN CHƯA PHÂN LÔ]")
        lines.append(f"Phát hiện {len(raw_unaccounted)} clip nguồn chưa được phân lô (nguồn chưa bao phủ hoàn toàn):")
        for u in raw_unaccounted:
            lines.append(f"- Clip {json.dumps(u.clip_id, ensure_ascii=False)}: Lý do={json.dumps(u.reason, ensure_ascii=False)} - {json.dumps(u.message, ensure_ascii=False)}")

    lines.append("")
    lines.append("[BẢN ĐỒ PHÂN VÙNG NGUỒN (MEASURED_SOURCE_BOUNDS)]")
    lines.append("Lưu ý ranh giới: Phân vùng nguồn được đo từ scan cache / snapshot của ứng dụng; formatter không re-stat file đĩa và không chứng minh audio-visual coverage hoặc approval. Structural isolation không bảo đảm tuyệt đối mô hình miễn nhiễm prompt injection.")
    for b in sorted_batches:
        lines.append(f"* Lô {json.dumps(b.batch_id, ensure_ascii=False)} (Index {b.batch_index}): {len(b.source_ranges)} phân vùng nguồn, {b.total_duration_sec:.1f}s")
        for r in b.source_ranges:
            w_str = f" Windows={json.dumps(list(r.window_ids), ensure_ascii=False)}" if r.window_ids else ""
            lines.append(f"  - Clip {json.dumps(r.clip_id, ensure_ascii=False)}: {r.source_in_sec}s -> {r.source_out_sec}s (Phần {r.partition_index + 1}/{r.total_partitions}){w_str}")

    if missing_ids:
        lines.append("")
        lines.append("[LÔ CHƯA TIẾP NHẬN BẢN THẢO]")
        lines.append("Các lô sau chưa có bản thảo kịch bản (TUYỆT ĐỐI KHÔNG TỰ BỊA ĐẶT KỊCH BẢN CHO CÁC LÔ NÀY):")
        for mid in missing_ids:
            b_missing = batch_by_id[mid]
            lines.append(f"* Lô {json.dumps(b_missing.batch_id, ensure_ascii=False)} (Index {b_missing.batch_index}): Chưa có bản thảo")

    lines.append("")
    lines.append("[CHIẾN LƯỢC CÁC LÔ ĐÃ NHẬN (AI_DRAFT_STRATEGY)]")
    for b in sorted_batches:
        if b.batch_id in drafts_by_batch_id:
            entry = drafts_by_batch_id[b.batch_id]
            lines.append(f"* Lô {json.dumps(b.batch_id, ensure_ascii=False)} (Index {b.batch_index}):")
            lines.append(f"  - Digest: {json.dumps(entry.draft_digest, ensure_ascii=False)}")
            lines.append(f"  - Số đoạn cắt chọn: {entry.total_cuts_count} đoạn ({entry.total_selected_duration_sec:.1f}s)")
            lines.append(f"  - Chế độ liên kết: {json.dumps(entry.binding_mode, ensure_ascii=False)}")

    if is_nonempty_cc:
        lines.append("")
        lines.append("[LƯU Ý NGỮ CẢNH SÁNG TẠO (CREATIVE_CONTEXT)]")
        lines.append("Yêu cầu biên tập và ghi chú người dùng nằm trong khóa creative_context của JSON ngữ cảnh. Đường dẫn tệp và URL chỉ là siêu dữ liệu; hệ thống chưa xem, tải về hoặc phân tích nội dung tệp/liên kết; mô hình không cam kết tuân thủ ngữ nghĩa tuyệt đối.")

    lines.append("")
    lines.append("[DỮ LIỆU NGỮ CẢNH DỰ ÁN CẤU TRÚC (PROJECT_OVERVIEW_CONTEXT_JSON)]")
    lines.append("```json")
    lines.append(context_json_str)
    lines.append("```")

    lines.append("")
    lines.append("[HƯỚNG DẪN TỔNG HỢP CHO AI]")
    lines.append("1. Xây dựng mạch dẫn vĩ mô (Macro Narrative Arc) xuyên suốt các lô đã nhận.")
    lines.append("2. Đề xuất điểm nối và chuyển cảnh (Bridges & Transitions) giữa ranh giới các lô kịch bản.")
    lines.append("3. Chỉ sử dụng các batch_id đã nhận trong cấu trúc thứ tự và chuyển đoạn; TUYỆT ĐỐI KHÔNG tự bịa đặt batch_id còn thiếu, cuts chi tiết hoặc đường dẫn file mới.")
    lines.append("4. Nêu rõ các giới hạn biên tập do các lô còn thiếu hoặc các clip nguồn chưa phân lô (nếu có).")
    lines.append("5. Không biến tổng thời lượng của mọi hành động thành thời lượng final timeline.")
    lines.append("6. Lưu ý: Macro schema hiện tại chưa có bộ nạp tự động (importer) vào timeline.")

    lines.append("")
    lines.append("[YÊU CẦU ĐỊNH DẠNG ĐẦU RA JSON]")
    lines.append("Trả về duy nhất 1 JSON Object tuân thủ schema sau:")
    lines.append("```json")
    lines.append(output_schema_str)
    lines.append("```")

    full_rendered_prompt = "\n".join(lines)

    try:
        prompt_bytes = full_rendered_prompt.encode("utf-8")
    except (UnicodeEncodeError, UnicodeError):
        return _make_invalid_result("Lỗi mã hóa UTF-8 trong nội dung kịch bản tổng quan.")

    required_bytes = len(prompt_bytes)

    # 8. Kiểm tra ngân sách (Equality <= max_prompt_bytes được nhận)
    if required_bytes > max_bytes:
        return ProjectOverviewRenderResult(
            ok=False,
            error_code=ERR_OVERVIEW_BUDGET_OVERFLOW,
            message=f"Kích thước kịch bản tổng quan ({required_bytes} B) vượt quá trần ngân sách ({max_bytes} B).",
            rendered_prompt="",
            rendered_bytes=0,
            rendered_sha256="",
            formatter_policy=FORMATTER_POLICY,
            received_batches_count=received_count,
            total_batches_expected=total_expected,
            missing_batch_ids=missing_ids,
            all_batch_drafts_received=all_drafts_received,
            source_coverage_complete=source_coverage_complete,
            required_bytes=required_bytes,
        )

    rendered_sha = hashlib.sha256(prompt_bytes).hexdigest()

    return ProjectOverviewRenderResult(
        ok=True,
        error_code="",
        message="Kịch bản tổng quan dự án đã được kết xuất thành công.",
        rendered_prompt=full_rendered_prompt,
        rendered_bytes=required_bytes,
        rendered_sha256=rendered_sha,
        formatter_policy=FORMATTER_POLICY,
        received_batches_count=received_count,
        total_batches_expected=total_expected,
        missing_batch_ids=missing_ids,
        all_batch_drafts_received=all_drafts_received,
        source_coverage_complete=source_coverage_complete,
        required_bytes=required_bytes,
    )


def _make_invalid_result(msg: str) -> ProjectOverviewRenderResult:
    return ProjectOverviewRenderResult(
        ok=False,
        error_code=ERR_OVERVIEW_INVALID_CONTEXT,
        message=msg,
        rendered_prompt="",
        rendered_bytes=0,
        rendered_sha256="",
        formatter_policy=FORMATTER_POLICY,
        received_batches_count=0,
        total_batches_expected=0,
        missing_batch_ids=(),
        all_batch_drafts_received=False,
        source_coverage_complete=False,
        required_bytes=0,
    )
