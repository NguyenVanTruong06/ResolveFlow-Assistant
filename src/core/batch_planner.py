"""
src/core/batch_planner.py
=============================================================================
Pure Bounded Batch Partition Contract (G06-06A1-R2).
Cung cấp logic thuần túy phân chia danh mục clip dự án thành các Lô (Batches)
có kiểm soát chặt chẽ ngân sách (bytes UTF-8, thời lượng, số lượng clip),
bảo toàn 100% ID toàn cục (manifest_id, clip_id), không I/O, không Qt, không model.
Đảm bảo:
- Byte budget cứng tuyệt đối sau rollover (đo chính xác tại batch index thật).
- Thời lượng cứng đo bằng số học Fraction chuẩn xác, zero epsilon tolerance.
- Không round(..., 6) gây mất độ chuẩn xác mốc thời gian nguồn trong JSON.
- Chữ ký nội dung (content_signature) toàn diện phản ánh mọi trường text,
  visual, provenance, config và framing ranh giới prefix/suffix.
- Thẩm định nghiêm ngặt kiểu dữ liệu thô (raw types), loại bỏ hoàn toàn
  coercion ngầm (str(None), bool->float) và ngăn chặn mutable aliasing.
- Thẩm định toàn bộ snapshot của clip trước khi quyết định skip/unaccounted.
- Không strip() làm thay đổi ID nguồn; None không thành text/filename/hash giả.
- Ranh giới window tuyệt đối không epsilon: [0, duration] cho interval,
  [0, duration) cho point observation; point tại ranh giới partition gán bên phải.
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
import re
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

if TYPE_CHECKING:
    from src.core.clip_manifest import ClipManifest, ClipEntry


# =============================================================================
# 1. DATA CONTRACTS & IMMUTABLE VALUE OBJECTS
# =============================================================================

@dataclass(frozen=True)
class BatchBudgetConfig:
    """Cấu hình ngân sách phân chia lô (Immutable Value Object)."""
    max_prompt_bytes: int = 32768
    target_batch_duration_sec: float = 600.0
    max_batch_duration_sec: float = 900.0
    max_clips_per_batch: int = 20
    window_partition_sec: float = 600.0
    max_planned_items: int = 10000
    prompt_prefix: str = ""
    prompt_suffix: str = ""

    def __post_init__(self):
        # max_prompt_bytes: strict int 1..32768, bool không hợp lệ
        if type(self.max_prompt_bytes) is not int or isinstance(self.max_prompt_bytes, bool):
            raise ValueError(f"ERR_INVALID_CONFIG: max_prompt_bytes phải là int, nhận: {self.max_prompt_bytes!r}")
        if not (1 <= self.max_prompt_bytes <= 32768):
            raise ValueError(f"ERR_INVALID_CONFIG: max_prompt_bytes phải trong khoảng 1..32768, nhận: {self.max_prompt_bytes}")

        # target_batch_duration_sec & max_batch_duration_sec & window_partition_sec: numeric finite positive
        for name, val in [
            ("target_batch_duration_sec", self.target_batch_duration_sec),
            ("max_batch_duration_sec", self.max_batch_duration_sec),
            ("window_partition_sec", self.window_partition_sec),
        ]:
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ValueError(f"ERR_INVALID_CONFIG: {name} phải là số thực dương, nhận: {val!r}")
            try:
                f_val = float(val)
            except OverflowError:
                raise ValueError(f"ERR_INVALID_CONFIG: {name} giá trị quá lớn gây tràn số: {val!r}")

            if math.isnan(f_val) or math.isinf(f_val) or f_val <= 0.0:
                raise ValueError(f"ERR_INVALID_CONFIG: {name} phải là số hữu hạn > 0, nhận: {val}")

        if float(self.target_batch_duration_sec) > float(self.max_batch_duration_sec):
            raise ValueError(
                f"ERR_INVALID_CONFIG: target_batch_duration_sec ({self.target_batch_duration_sec}) "
                f"không được lớn hơn max_batch_duration_sec ({self.max_batch_duration_sec})"
            )

        if float(self.window_partition_sec) > float(self.max_batch_duration_sec):
            raise ValueError(
                f"ERR_INVALID_CONFIG: window_partition_sec ({self.window_partition_sec}) "
                f"không được lớn hơn max_batch_duration_sec ({self.max_batch_duration_sec})"
            )

        # max_clips_per_batch: strict int >= 1
        if type(self.max_clips_per_batch) is not int or isinstance(self.max_clips_per_batch, bool) or self.max_clips_per_batch < 1:
            raise ValueError(f"ERR_INVALID_CONFIG: max_clips_per_batch phải là int >= 1, nhận: {self.max_clips_per_batch!r}")
        if self.max_clips_per_batch > 100000:
            raise ValueError(f"ERR_INVALID_CONFIG: max_clips_per_batch quá lớn: {self.max_clips_per_batch}")

        # max_planned_items: strict int 1..100000
        if type(self.max_planned_items) is not int or isinstance(self.max_planned_items, bool) or not (1 <= self.max_planned_items <= 100000):
            raise ValueError(f"ERR_INVALID_CONFIG: max_planned_items phải là int 1..100000, nhận: {self.max_planned_items!r}")

        # prompt_prefix & prompt_suffix: strict str (không bool)
        if type(self.prompt_prefix) is not str or type(self.prompt_suffix) is not str:
            raise ValueError("ERR_INVALID_CONFIG: prompt_prefix và prompt_suffix phải là chuỗi str")


@dataclass(frozen=True)
class EvidenceWindowSummary:
    """Tóm tắt một cửa sổ bằng chứng trong clip (Immutable Value Object)."""
    window_id: str
    start_sec: float
    end_sec: float
    speech_summary: str = ""
    speech_word_count: int = 0
    visual_action: str = ""
    has_audio: Optional[bool] = None
    is_sampled: bool = True
    status: str = "unscanned"
    origin: str = "unknown"
    overlap: bool = False
    clipped: bool = False

    def __post_init__(self):
        if type(self.window_id) is not str or not self.window_id.strip():
            raise ValueError(f"ERR_INVALID_WINDOW: window_id phải là chuỗi không rỗng, nhận: {self.window_id!r}")

        for name, val in [("start_sec", self.start_sec), ("end_sec", self.end_sec)]:
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ValueError(f"ERR_INVALID_WINDOW: {name} phải là số thực >= 0, nhận: {val!r}")
            try:
                f_val = float(val)
            except OverflowError:
                raise ValueError(f"ERR_INVALID_WINDOW: {name} giá trị quá lớn gây tràn số: {val!r}")
            if math.isnan(f_val) or math.isinf(f_val) or f_val < 0.0:
                raise ValueError(f"ERR_INVALID_WINDOW: {name} phải là số hữu hạn >= 0, nhận: {val!r}")

        if float(self.start_sec) > float(self.end_sec):
            raise ValueError(f"ERR_INVALID_WINDOW: start_sec ({self.start_sec}) > end_sec ({self.end_sec})")

        if type(self.speech_summary) is not str:
            raise ValueError(f"ERR_INVALID_WINDOW: speech_summary phải là str, nhận: {type(self.speech_summary).__name__}")

        if type(self.visual_action) is not str:
            raise ValueError(f"ERR_INVALID_WINDOW: visual_action phải là str, nhận: {type(self.visual_action).__name__}")

        if type(self.speech_word_count) is not int or isinstance(self.speech_word_count, bool) or self.speech_word_count < 0:
            raise ValueError(f"ERR_INVALID_WINDOW: speech_word_count phải là int >= 0, nhận: {self.speech_word_count!r}")

        if self.has_audio is not None and type(self.has_audio) is not bool:
            raise ValueError(f"ERR_INVALID_WINDOW: has_audio phải là bool hoặc None, nhận: {self.has_audio!r}")

        if type(self.is_sampled) is not bool:
            raise ValueError(f"ERR_INVALID_WINDOW: is_sampled phải là bool, nhận: {self.is_sampled!r}")

        if type(self.status) is not str:
            raise ValueError(f"ERR_INVALID_WINDOW: status phải là str, nhận: {type(self.status).__name__}")

        if type(self.origin) is not str:
            raise ValueError(f"ERR_INVALID_WINDOW: origin phải là str, nhận: {type(self.origin).__name__}")

        if type(self.overlap) is not bool:
            raise ValueError(f"ERR_INVALID_WINDOW: overlap phải là bool, nhận: {self.overlap!r}")

        if type(self.clipped) is not bool:
            raise ValueError(f"ERR_INVALID_WINDOW: clipped phải là bool, nhận: {self.clipped!r}")


@dataclass(frozen=True)
class ClipBatchItem:
    """Một phân đoạn clip trong Lô (Batch), giữ toàn vẹn xuất xứ."""
    clip_id: str
    file_name: str
    duration_sec: float
    source_in_sec: float
    source_out_sec: float
    item_duration_sec: float
    partition_index: int
    total_partitions: int
    has_audio: Optional[bool]
    speech_status: str
    content_hash: str = ""
    windows: Tuple[EvidenceWindowSummary, ...] = ()
    full_content_sha256: Optional[str] = None
    analysis_signature: Optional[str] = None

    def __post_init__(self):
        if type(self.clip_id) is not str or not self.clip_id.strip():
            raise ValueError(f"ERR_INVALID_ITEM: clip_id phải là chuỗi str không rỗng, nhận: {self.clip_id!r}")
        if type(self.file_name) is not str:
            raise ValueError(f"ERR_INVALID_ITEM: file_name phải là str, nhận: {self.file_name!r}")
        if type(self.content_hash) is not str:
            raise ValueError(f"ERR_INVALID_ITEM: content_hash phải là str, nhận: {self.content_hash!r}")

        for name, val in [
            ("duration_sec", self.duration_sec),
            ("source_in_sec", self.source_in_sec),
            ("source_out_sec", self.source_out_sec),
            ("item_duration_sec", self.item_duration_sec),
        ]:
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ValueError(f"ERR_INVALID_ITEM: {name} phải là số thực >= 0, nhận: {val!r}")
            try:
                f_val = float(val)
            except OverflowError:
                raise ValueError(f"ERR_INVALID_ITEM: {name} giá trị quá lớn gây tràn số: {val!r}")
            if math.isnan(f_val) or math.isinf(f_val) or f_val < 0.0:
                raise ValueError(f"ERR_INVALID_ITEM: {name} phải là số hữu hạn >= 0, nhận: {val!r}")

        if type(self.partition_index) is not int or isinstance(self.partition_index, bool) or self.partition_index < 0:
            raise ValueError(f"ERR_INVALID_ITEM: partition_index phải là int >= 0, nhận: {self.partition_index!r}")
        if type(self.total_partitions) is not int or isinstance(self.total_partitions, bool) or self.total_partitions < 1:
            raise ValueError(f"ERR_INVALID_ITEM: total_partitions phải là int >= 1, nhận: {self.total_partitions!r}")

        if self.has_audio is not None and type(self.has_audio) is not bool:
            raise ValueError(f"ERR_INVALID_ITEM: has_audio phải là bool hoặc None, nhận: {self.has_audio!r}")
        if type(self.speech_status) is not str:
            raise ValueError(f"ERR_INVALID_ITEM: speech_status phải là str, nhận: {self.speech_status!r}")

        if self.full_content_sha256 is not None:
            if type(self.full_content_sha256) is not str or len(self.full_content_sha256) != 64 or not re.fullmatch(r"[0-9a-fA-F]{64}", self.full_content_sha256):
                raise ValueError(f"ERR_INVALID_ITEM: full_content_sha256 phải là 64-hex string: {self.full_content_sha256!r}")

        if self.analysis_signature is not None and type(self.analysis_signature) is not str:
            raise ValueError(f"ERR_INVALID_ITEM: analysis_signature phải là str: {self.analysis_signature!r}")

        # Ensure windows is immutable tuple of EvidenceWindowSummary
        if not isinstance(self.windows, tuple):
            object.__setattr__(self, "windows", tuple(self.windows))
        for w in self.windows:
            if not isinstance(w, EvidenceWindowSummary):
                raise ValueError(f"ERR_INVALID_ITEM: phần tử trong windows phải là EvidenceWindowSummary, nhận: {type(w).__name__}")


@dataclass(frozen=True)
class BoundedBatch:
    """Một Lô phân tích độc lập (Self-contained Batch)."""
    batch_id: str
    batch_index: int
    manifest_id: str
    project_name: str
    items: Tuple[ClipBatchItem, ...]
    distinct_clip_count: int
    total_duration_sec: float
    estimated_prompt_bytes: int
    prompt_prefix: str = ""
    prompt_suffix: str = ""
    content_signature: str = ""

    def __post_init__(self):
        if type(self.batch_id) is not str or not self.batch_id.strip():
            raise ValueError(f"ERR_INVALID_BATCH: batch_id phải là chuỗi không rỗng, nhận: {self.batch_id!r}")
        if type(self.batch_index) is not int or isinstance(self.batch_index, bool) or self.batch_index < 0:
            raise ValueError(f"ERR_INVALID_BATCH: batch_index phải là int >= 0, nhận: {self.batch_index!r}")
        if type(self.manifest_id) is not str or not self.manifest_id.strip():
            raise ValueError(f"ERR_INVALID_BATCH: manifest_id phải là chuỗi không rỗng, nhận: {self.manifest_id!r}")
        if type(self.project_name) is not str:
            raise ValueError(f"ERR_INVALID_BATCH: project_name phải là str, nhận: {self.project_name!r}")

        if not isinstance(self.items, tuple):
            object.__setattr__(self, "items", tuple(self.items))
        for it in self.items:
            if not isinstance(it, ClipBatchItem):
                raise ValueError(f"ERR_INVALID_BATCH: phần tử trong items phải là ClipBatchItem, nhận: {type(it).__name__}")

        if type(self.distinct_clip_count) is not int or isinstance(self.distinct_clip_count, bool) or self.distinct_clip_count < 0:
            raise ValueError(f"ERR_INVALID_BATCH: distinct_clip_count phải là int >= 0, nhận: {self.distinct_clip_count!r}")
        if isinstance(self.total_duration_sec, bool) or not isinstance(self.total_duration_sec, (int, float)) or self.total_duration_sec < 0.0:
            raise ValueError(f"ERR_INVALID_BATCH: total_duration_sec phải là số thực >= 0, nhận: {self.total_duration_sec!r}")
        if type(self.estimated_prompt_bytes) is not int or isinstance(self.estimated_prompt_bytes, bool) or self.estimated_prompt_bytes < 0:
            raise ValueError(f"ERR_INVALID_BATCH: estimated_prompt_bytes phải là int >= 0, nhận: {self.estimated_prompt_bytes!r}")
        if type(self.prompt_prefix) is not str or type(self.prompt_suffix) is not str:
            raise ValueError("ERR_INVALID_BATCH: prompt_prefix và prompt_suffix phải là str")
        if type(self.content_signature) is not str:
            raise ValueError("ERR_INVALID_BATCH: content_signature phải là str")


@dataclass(frozen=True)
class UnaccountedClipIssue:
    """Ghi nhận nguyên nhân clip không thể đưa vào batch."""
    clip_id: str
    reason: str
    message: str

    def __post_init__(self):
        if type(self.clip_id) is not str or not self.clip_id.strip():
            raise ValueError(f"ERR_INVALID_ISSUE: clip_id phải là chuỗi không rỗng, nhận: {self.clip_id!r}")
        if type(self.reason) is not str or type(self.message) is not str:
            raise ValueError("ERR_INVALID_ISSUE: reason và message phải là str")


@dataclass(frozen=True)
class ProjectBatchPlan:
    """Kế hoạch phân lô tổng thể của toàn bộ dự án."""
    manifest_id: str
    project_name: str
    batches: Tuple[BoundedBatch, ...]
    total_clips: int
    planned_clip_count: int
    unaccounted_clips: Tuple[str, ...] = ()
    issues: Tuple[UnaccountedClipIssue, ...] = ()
    total_duration_sec: float = 0.0
    created_at: str = ""

    def __post_init__(self):
        if type(self.manifest_id) is not str or not self.manifest_id.strip():
            raise ValueError(f"ERR_INVALID_PLAN: manifest_id phải là chuỗi không rỗng, nhận: {self.manifest_id!r}")
        if type(self.project_name) is not str:
            raise ValueError(f"ERR_INVALID_PLAN: project_name phải là str, nhận: {self.project_name!r}")
        if not isinstance(self.batches, tuple):
            object.__setattr__(self, "batches", tuple(self.batches))
        for b in self.batches:
            if not isinstance(b, BoundedBatch):
                raise ValueError(f"ERR_INVALID_PLAN: phần tử trong batches phải là BoundedBatch, nhận: {type(b).__name__}")
        if not isinstance(self.unaccounted_clips, tuple):
            object.__setattr__(self, "unaccounted_clips", tuple(self.unaccounted_clips))
        if not isinstance(self.issues, tuple):
            object.__setattr__(self, "issues", tuple(self.issues))
        for iss in self.issues:
            if not isinstance(iss, UnaccountedClipIssue):
                raise ValueError(f"ERR_INVALID_PLAN: phần tử trong issues phải là UnaccountedClipIssue, nhận: {type(iss).__name__}")


# =============================================================================
# 2. PROMPT RENDERING & EXACT BYTE ESTIMATION (SSOT)
# =============================================================================

def _build_batch_envelope_dict(batch: BoundedBatch) -> Dict[str, Any]:
    """Tạo payload JSON envelope chuẩn tắc không áp dụng round() làm méo mó mốc thời gian."""
    return {
        "schema_version": 1,
        "manifest_id": batch.manifest_id,
        "project_name": batch.project_name,
        "batch_id": batch.batch_id,
        "batch_index": batch.batch_index,
        "distinct_clip_count": batch.distinct_clip_count,
        "total_duration_sec": batch.total_duration_sec,
        "items": [
            {
                "clip_id": item.clip_id,
                "file_name": item.file_name,
                "content_hash": item.content_hash,
                "clip_duration_sec": item.duration_sec,
                "source_in_sec": item.source_in_sec,
                "source_out_sec": item.source_out_sec,
                "item_duration_sec": item.item_duration_sec,
                "partition_index": item.partition_index,
                "total_partitions": item.total_partitions,
                "has_audio": item.has_audio,
                "speech_status": item.speech_status,
                "full_content_sha256": item.full_content_sha256,
                "analysis_signature": item.analysis_signature,
                "windows": [
                    {
                        "window_id": w.window_id,
                        "start_sec": w.start_sec,
                        "end_sec": w.end_sec,
                        "speech_summary": w.speech_summary,
                        "speech_word_count": w.speech_word_count,
                        "visual_action": w.visual_action,
                        "has_audio": w.has_audio,
                        "is_sampled": w.is_sampled,
                        "status": w.status,
                        "origin": w.origin,
                        "overlap": w.overlap,
                        "clipped": w.clipped,
                    }
                    for w in item.windows
                ]
            }
            for item in batch.items
        ]
    }


def render_batch_prompt(batch: BoundedBatch) -> str:
    """
    Kết xuất canonical JSON evidence envelope đại diện cho Lô.
    Đây là Single Source of Truth (SSOT) duy nhất để đo lường kích thước UTF-8 bytes.
    Không áp dụng round() làm thay đổi source bounds / window times.
    """
    envelope = _build_batch_envelope_dict(batch)
    json_str = json.dumps(
        envelope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"{batch.prompt_prefix}{json_str}{batch.prompt_suffix}"


def estimate_batch_prompt_bytes(batch: BoundedBatch) -> int:
    """Đo lường chính xác 100% số byte UTF-8 của kết xuất prompt batch."""
    return len(render_batch_prompt(batch).encode("utf-8"))


def _compute_batch_content_signature(
    envelope_dict: Dict[str, Any],
    config: BatchBudgetConfig,
    prompt_prefix: str,
    prompt_suffix: str,
) -> str:
    """
    Tính toán chữ ký xác thực nội dung lô toàn diện:
    Bao gồm toàn bộ payload JSON đã kết xuất (items, windows, speech text, visual action,
    provenance, sample flags, bounds, hashes, IDs), exact config và prefix/suffix boundary.
    Loại trừ self-referential content_signature và estimated_prompt_bytes.
    Không phụ thuộc đồng hồ hay file I/O.
    """
    sig_context = {
        "payload": envelope_dict,
        "prompt_prefix": prompt_prefix,
        "prompt_suffix": prompt_suffix,
        "config": {
            "max_prompt_bytes": config.max_prompt_bytes,
            "target_batch_duration_sec": config.target_batch_duration_sec,
            "max_batch_duration_sec": config.max_batch_duration_sec,
            "max_clips_per_batch": config.max_clips_per_batch,
            "window_partition_sec": config.window_partition_sec,
            "max_planned_items": config.max_planned_items,
            "prompt_prefix": config.prompt_prefix,
            "prompt_suffix": config.prompt_suffix,
        }
    }
    canonical_json = json.dumps(
        sig_context,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


# =============================================================================
# 3. HELPER GATE FOR WINDOW PARSING
# =============================================================================

def _parse_and_validate_window_number(val: Any, name: str, cid: str) -> float:
    """Thẩm định nghiêm ngặt giá trị thời gian cửa sổ không để lọt overflow hay bool."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ValueError(f"ERR_INVALID_WINDOW: {name} trong clip {cid} phải là số thực >= 0, nhận: {val!r}")
    try:
        f_val = float(val)
    except OverflowError:
        raise ValueError(f"ERR_INVALID_WINDOW: {name} trong clip {cid} giá trị quá lớn gây tràn số: {val!r}")
    if math.isnan(f_val) or math.isinf(f_val) or f_val < 0.0:
        raise ValueError(f"ERR_INVALID_WINDOW: {name} trong clip {cid} phải là số hữu hạn >= 0, nhận: {val!r}")
    return f_val


# =============================================================================
# 4. CORE PARTITIONING ENGINE
# =============================================================================

def partition_manifest_into_batches(
    manifest: Any,
    analysis_cache: Mapping[str, Mapping[str, object]],
    config: Optional[BatchBudgetConfig] = None,
) -> ProjectBatchPlan:
    """
    Phân chia toàn bộ danh mục clip dự án thành các BoundedBatch có kiểm soát ngân sách.

    Args:
        manifest: Object ClipManifest thật do caller cấp (đọc project_name, manifest_id, clips).
        analysis_cache: Mapping in-memory theo clip_id -> snapshot phân tích nguồn.
        config: Cấu hình ngân sách phân lô BatchBudgetConfig.
    """
    if config is None:
        config = BatchBudgetConfig()
    elif not isinstance(config, BatchBudgetConfig):
        raise ValueError(f"ERR_INVALID_CONFIG: config phải là BatchBudgetConfig, nhận: {type(config).__name__}")

    # 1. Thẩm định tính hợp lệ của manifest
    if manifest is None:
        raise ValueError("ERR_INVALID_MANIFEST: manifest không được None")

    raw_manifest_id = getattr(manifest, "manifest_id", None)
    if isinstance(raw_manifest_id, bool) or not isinstance(raw_manifest_id, str) or not raw_manifest_id.strip():
        raise ValueError(f"ERR_INVALID_MANIFEST: manifest_id phải là chuỗi str không rỗng, nhận: {raw_manifest_id!r}")
    # Bảo toàn nguyên bản chuỗi manifest_id (không strip)
    manifest_id = raw_manifest_id

    raw_project_name = getattr(manifest, "project_name", None)
    if raw_project_name is None or isinstance(raw_project_name, bool) or not isinstance(raw_project_name, str):
        raise ValueError(f"ERR_INVALID_MANIFEST: project_name phải là str, nhận: {raw_project_name!r}")
    # Bảo toàn nguyên bản project_name
    project_name = raw_project_name

    clips_seq = getattr(manifest, "clips", None)
    if clips_seq is None or not isinstance(clips_seq, (list, tuple)):
        raise ValueError("ERR_INVALID_MANIFEST: clips phải là sequence (list hoặc tuple)")

    # Kiểm tra trùng lặp và tính hợp lệ của clip_id
    seen_clip_ids: Set[str] = set()
    for c in clips_seq:
        cid = getattr(c, "clip_id", None)
        if isinstance(cid, bool) or not isinstance(cid, str) or not cid.strip():
            raise ValueError(f"ERR_INVALID_CLIP_ID: clip_id không hợp lệ: {cid!r}")
        if cid in seen_clip_ids:
            raise ValueError(f"ERR_DUPLICATE_CLIP_ID: clip_id trùng lặp trong manifest: {cid}")
        seen_clip_ids.add(cid)

    # 2. Thẩm định analysis_cache
    if not isinstance(analysis_cache, Mapping):
        raise ValueError(f"ERR_INVALID_ANALYSIS_CACHE: analysis_cache phải là Mapping, nhận: {type(analysis_cache).__name__}")

    for k, entry in analysis_cache.items():
        if isinstance(k, bool) or not isinstance(k, str) or k not in seen_clip_ids:
            raise ValueError(f"ERR_UNKNOWN_ANALYSIS_CLIP_ID: clip_id trong analysis_cache không có trong manifest: {k!r}")
        if not isinstance(entry, Mapping):
            raise ValueError(f"ERR_INVALID_ANALYSIS: entry cho {k} phải là Mapping, nhận: {type(entry).__name__}")

    # Manifest rỗng -> Trả về kế hoạch rỗng hợp lệ
    if not clips_seq:
        return ProjectBatchPlan(
            manifest_id=manifest_id,
            project_name=project_name,
            batches=(),
            total_clips=0,
            planned_clip_count=0,
            unaccounted_clips=(),
            issues=(),
            total_duration_sec=0.0,
        )

    # 3. Phân loại clip và tạo các ClipBatchItem
    all_planned_items: List[ClipBatchItem] = []
    unaccounted_clips_list: List[str] = []
    issues_list: List[UnaccountedClipIssue] = []

    # Pre-allocation limit guard
    est_total_items = 0
    window_part_sec = config.window_partition_sec

    for c in clips_seq:
        cid = c.clip_id

        # Thẩm định bắt buộc các metadata fields của ClipEntry (không được None hay tự suy đoán tên file)
        raw_file_name = getattr(c, "file_name", None)
        if raw_file_name is None or isinstance(raw_file_name, bool) or not isinstance(raw_file_name, str):
            raise ValueError(f"ERR_INVALID_CLIP: file_name của {cid} phải là str, nhận: {raw_file_name!r}")
        c_file_name = raw_file_name

        raw_content_hash = getattr(c, "content_hash", None)
        if raw_content_hash is None or isinstance(raw_content_hash, bool) or not isinstance(raw_content_hash, str):
            raise ValueError(f"ERR_INVALID_CLIP: content_hash của {cid} phải là str, nhận: {raw_content_hash!r}")
        c_content_hash = raw_content_hash

        raw_dur = getattr(c, "duration_sec", None)
        # Kiểm tra tính hợp lệ của duration
        if isinstance(raw_dur, bool) or (raw_dur is not None and not isinstance(raw_dur, (int, float))):
            raise ValueError(f"ERR_INVALID_CLIP_DURATION: duration_sec của {cid} không hợp lệ: {raw_dur!r}")

        c_dur: Optional[float] = None
        if raw_dur is not None:
            try:
                f_dur = float(raw_dur)
            except OverflowError:
                raise ValueError(f"ERR_INVALID_CLIP_DURATION: duration_sec của {cid} quá lớn: {raw_dur!r}")
            if math.isnan(f_dur) or math.isinf(f_dur) or f_dur < 0.0:
                raise ValueError(f"ERR_INVALID_CLIP_DURATION: duration_sec của {cid} không hợp lệ: {raw_dur!r}")
            c_dur = f_dur

        # THẨM ĐỊNH TOÀN BỘ ANALYSIS SNAPSHOT HIỆN DIỆN TRƯỚC KHI XÉT SKIP/UNACCOUNTED!
        c_analysis = analysis_cache.get(cid, {})

        speech_status_raw = c_analysis.get("speech_status", "unscanned")
        if isinstance(speech_status_raw, bool) or not isinstance(speech_status_raw, str):
            raise ValueError(f"ERR_INVALID_ANALYSIS: speech_status của {cid} phải là str, nhận: {speech_status_raw!r}")
        speech_status = speech_status_raw

        raw_has_audio = c_analysis.get("has_audio", None)
        has_audio: Optional[bool] = None
        if raw_has_audio is not None:
            if type(raw_has_audio) is not bool:
                raise ValueError(f"ERR_INVALID_ANALYSIS: has_audio của {cid} phải là bool hoặc None, nhận: {raw_has_audio!r}")
            has_audio = raw_has_audio

        full_sha = c_analysis.get("full_content_sha256", None)
        if full_sha is not None:
            if type(full_sha) is not str or len(full_sha) != 64 or not re.fullmatch(r"[0-9a-fA-F]{64}", full_sha):
                raise ValueError(f"ERR_INVALID_ANALYSIS: full_content_sha256 của {cid} phải là 64-hex string: {full_sha!r}")
            full_sha = full_sha.lower()

        analysis_sig = c_analysis.get("analysis_signature", None)
        if analysis_sig is not None and type(analysis_sig) is not str:
            raise ValueError(f"ERR_INVALID_ANALYSIS: analysis_signature của {cid} phải là str: {analysis_sig!r}")

        # Thẩm định raw windows
        raw_windows = c_analysis.get("windows", ())
        if not isinstance(raw_windows, (list, tuple)):
            raise ValueError(f"ERR_INVALID_ANALYSIS: windows của {cid} phải là sequence, nhận: {type(raw_windows).__name__}")

        parsed_windows: List[EvidenceWindowSummary] = []
        seen_win_ids: Set[str] = set()

        for rw in raw_windows:
            if isinstance(rw, EvidenceWindowSummary):
                win_obj = rw
            elif isinstance(rw, Mapping):
                wid = rw.get("window_id", None)
                if isinstance(wid, bool) or not isinstance(wid, str) or not wid.strip():
                    raise ValueError(f"ERR_INVALID_WINDOW: window_id trong {cid} phải là str không rỗng: {wid!r}")

                w_start_raw = rw.get("start_sec", None)
                w_end_raw = rw.get("end_sec", None)
                if w_start_raw is None or w_end_raw is None:
                    raise ValueError(f"ERR_INVALID_WINDOW: window trong {cid} thiếu start_sec hoặc end_sec")

                # Validate types strictly trước khi conversion bằng helper gate
                w_start = _parse_and_validate_window_number(w_start_raw, "start_sec", cid)
                w_end = _parse_and_validate_window_number(w_end_raw, "end_sec", cid)

                if w_start > w_end:
                    raise ValueError(f"ERR_INVALID_WINDOW: start_sec ({w_start}) > end_sec ({w_end}) trong clip {cid}")

                w_sp_sum = rw.get("speech_summary", "")
                if type(w_sp_sum) is not str:
                    raise ValueError(f"ERR_INVALID_WINDOW: speech_summary phải là str trong {cid}, nhận: {type(w_sp_sum).__name__}")

                w_sp_words = rw.get("speech_word_count", 0)
                if type(w_sp_words) is not int or isinstance(w_sp_words, bool) or w_sp_words < 0:
                    raise ValueError(f"ERR_INVALID_WINDOW: speech_word_count phải là int >= 0 trong {cid}, nhận: {w_sp_words!r}")

                w_vis = rw.get("visual_action", "")
                if type(w_vis) is not str:
                    raise ValueError(f"ERR_INVALID_WINDOW: visual_action phải là str trong {cid}, nhận: {type(w_vis).__name__}")

                w_aud = rw.get("has_audio", None)
                if w_aud is not None and type(w_aud) is not bool:
                    raise ValueError(f"ERR_INVALID_WINDOW: has_audio phải là bool hoặc None trong {cid}, nhận: {w_aud!r}")

                w_samp = rw.get("is_sampled", True)
                if type(w_samp) is not bool:
                    raise ValueError(f"ERR_INVALID_WINDOW: is_sampled phải là bool trong {cid}, nhận: {w_samp!r}")

                w_st = rw.get("status", "unscanned")
                if type(w_st) is not str:
                    raise ValueError(f"ERR_INVALID_WINDOW: status phải là str trong {cid}, nhận: {type(w_st).__name__}")

                w_orig = rw.get("origin", "unknown")
                if type(w_orig) is not str:
                    raise ValueError(f"ERR_INVALID_WINDOW: origin phải là str trong {cid}, nhận: {type(w_orig).__name__}")

                win_obj = EvidenceWindowSummary(
                    window_id=wid,
                    start_sec=w_start,
                    end_sec=w_end,
                    speech_summary=w_sp_sum,
                    speech_word_count=w_sp_words,
                    visual_action=w_vis,
                    has_audio=w_aud,
                    is_sampled=w_samp,
                    status=w_st,
                    origin=w_orig,
                )
            else:
                raise ValueError(f"ERR_INVALID_WINDOW: window không đúng định dạng trong {cid}: {type(rw).__name__}")

            if win_obj.window_id in seen_win_ids:
                raise ValueError(f"ERR_DUPLICATE_WINDOW_ID: window_id trùng lặp trong clip {cid}: {win_obj.window_id}")
            seen_win_ids.add(win_obj.window_id)

            # Thẩm định bounds của window đối với toàn bộ clip KHI DURATION ĐÃ BIẾT (Zero epsilon tolerance)
            if c_dur is not None and c_dur > 0.0:
                if win_obj.start_sec < win_obj.end_sec:
                    # Positive interval: 0 <= start < end <= duration
                    if win_obj.end_sec > c_dur:
                        raise ValueError(
                            f"ERR_WINDOW_OUT_OF_BOUNDS: window {win_obj.window_id} ({win_obj.end_sec}s) "
                            f"vượt thời lượng clip {cid} ({c_dur}s)"
                        )
                    if win_obj.start_sec >= c_dur:
                        raise ValueError(
                            f"ERR_WINDOW_OUT_OF_BOUNDS: window {win_obj.window_id} start_sec ({win_obj.start_sec}s) "
                            f">= thời lượng clip {cid} ({c_dur}s)"
                        )
                else:
                    # Point observation: 0 <= t < duration (t == duration bị từ chối)
                    if win_obj.start_sec >= c_dur:
                        raise ValueError(
                            f"ERR_WINDOW_OUT_OF_BOUNDS: point window {win_obj.window_id} ({win_obj.start_sec}s) "
                            f"phải < thời lượng clip {cid} ({c_dur}s)"
                        )

            parsed_windows.append(win_obj)

        # SAU KHI ĐÃ THẨM ĐỊNH TOÀN BỘ FIELDS VÀ WINDOWS -> XÉT CÁC TRƯỜNG HỢP KHÔNG LẬP TIMED ITEM (ACCOUNTING)
        # Duration unknown (None hoặc 0.0) -> unaccounted
        if c_dur is None or c_dur <= 0.0:
            unaccounted_clips_list.append(cid)
            issues_list.append(
                UnaccountedClipIssue(
                    clip_id=cid,
                    reason="DURATION_UNKNOWN",
                    message=f"Clip {cid} có thời lượng không xác định ({raw_dur!r}); không thể chia lô.",
                )
            )
            continue

        # Explicit source_missing / source_changed -> unaccounted
        if speech_status == "source_missing":
            unaccounted_clips_list.append(cid)
            issues_list.append(
                UnaccountedClipIssue(
                    clip_id=cid,
                    reason="SOURCE_MISSING",
                    message=f"Tệp nguồn của clip {cid} bị thiếu trên hệ thống.",
                )
            )
            continue
        elif speech_status == "source_changed":
            unaccounted_clips_list.append(cid)
            issues_list.append(
                UnaccountedClipIssue(
                    clip_id=cid,
                    reason="SOURCE_CHANGED",
                    message=f"Tệp nguồn của clip {cid} đã bị biến đổi nội dung kể từ phiên phân tích.",
                )
            )
            continue

        # Clip hợp lệ để lập kế hoạch -> Tính số partitions dự kiến
        d_frac = Fraction(str(c_dur))
        w_frac = Fraction(str(window_part_sec))
        n_parts = math.ceil(d_frac / w_frac)
        est_total_items += n_parts
        if est_total_items > config.max_planned_items:
            raise ValueError(
                f"ERR_PLAN_LIMIT_EXCEEDED: Tổng số phân đoạn dự kiến ({est_total_items}) vượt quá trần "
                f"max_planned_items ({config.max_planned_items})"
            )

        # Chia clip thành các consecutive ranges
        for p_idx in range(n_parts):
            p_start_frac = p_idx * w_frac
            p_end_frac = min((p_idx + 1) * w_frac, d_frac)

            p_start_sec = float(p_start_frac)
            p_end_sec = float(p_end_frac)
            item_dur_sec = float(p_end_frac - p_start_frac)

            # Map windows thuộc partition này
            item_windows: List[EvidenceWindowSummary] = []
            for w in parsed_windows:
                if w.start_sec < w.end_sec:
                    # Positive window range: overlaps if max(start, p_start) < min(end, p_end)
                    if max(w.start_sec, p_start_sec) < min(w.end_sec, p_end_sec):
                        is_clipped = (w.start_sec < p_start_sec) or (w.end_sec > p_end_sec)
                        # Giữ nguyên toàn bộ text và bounds gốc, chỉ gán cờ clipped/overlap
                        item_windows.append(
                            EvidenceWindowSummary(
                                window_id=w.window_id,
                                start_sec=w.start_sec,
                                end_sec=w.end_sec,
                                speech_summary=w.speech_summary,
                                speech_word_count=w.speech_word_count,
                                visual_action=w.visual_action,
                                has_audio=w.has_audio,
                                is_sampled=w.is_sampled,
                                status=w.status,
                                origin=w.origin,
                                overlap=True,
                                clipped=is_clipped,
                            )
                        )
                else:
                    # Point observation (start == end): 0 <= t < duration
                    # Gán partition bên phải tại ranh giới: p_start <= t < p_end
                    if p_start_sec <= w.start_sec < p_end_sec:
                        item_windows.append(w)

            all_planned_items.append(
                ClipBatchItem(
                    clip_id=cid,
                    file_name=c_file_name,
                    duration_sec=c_dur,
                    source_in_sec=p_start_sec,
                    source_out_sec=p_end_sec,
                    item_duration_sec=item_dur_sec,
                    partition_index=p_idx,
                    total_partitions=n_parts,
                    has_audio=has_audio,
                    speech_status=speech_status,
                    content_hash=c_content_hash,
                    windows=tuple(item_windows),
                    full_content_sha256=full_sha,
                    analysis_signature=analysis_sig,
                )
            )

    # 4. Đóng gói các items vào các BoundedBatch có kiểm soát ngân sách
    batches: List[BoundedBatch] = []
    current_batch_items: List[ClipBatchItem] = []

    # Fraction representations của limits để so sánh chính xác tuyệt đối không epsilon
    max_batch_duration_frac = Fraction(str(config.max_batch_duration_sec))
    target_batch_duration_frac = Fraction(str(config.target_batch_duration_sec))

    def make_candidate_batch(b_index: int, items_to_pack: Sequence[ClipBatchItem]) -> BoundedBatch:
        b_id = f"B{b_index + 1:02d}" if (b_index + 1 < 100) else f"B{b_index + 1:03d}"
        d_clips = len(set(it.clip_id for it in items_to_pack))
        t_dur_frac = sum(Fraction(str(it.item_duration_sec)) for it in items_to_pack)
        t_dur = float(t_dur_frac)

        temp_batch = BoundedBatch(
            batch_id=b_id,
            batch_index=b_index,
            manifest_id=manifest_id,
            project_name=project_name,
            items=tuple(items_to_pack),
            distinct_clip_count=d_clips,
            total_duration_sec=t_dur,
            estimated_prompt_bytes=0,
            prompt_prefix=config.prompt_prefix,
            prompt_suffix=config.prompt_suffix,
            content_signature="",
        )

        exact_bytes = estimate_batch_prompt_bytes(temp_batch)
        envelope_dict = _build_batch_envelope_dict(temp_batch)
        sig = _compute_batch_content_signature(
            envelope_dict=envelope_dict,
            config=config,
            prompt_prefix=config.prompt_prefix,
            prompt_suffix=config.prompt_suffix,
        )

        return BoundedBatch(
            batch_id=b_id,
            batch_index=b_index,
            manifest_id=manifest_id,
            project_name=project_name,
            items=tuple(items_to_pack),
            distinct_clip_count=d_clips,
            total_duration_sec=t_dur,
            estimated_prompt_bytes=exact_bytes,
            prompt_prefix=config.prompt_prefix,
            prompt_suffix=config.prompt_suffix,
            content_signature=sig,
        )

    for item in all_planned_items:
        if not current_batch_items:
            # Batch hiện tại rỗng -> Đo trực tiếp item ở batch index hiện tại
            single_candidate = make_candidate_batch(len(batches), [item])
            if single_candidate.estimated_prompt_bytes > config.max_prompt_bytes:
                raise ValueError(
                    f"ERR_ITEM_EXCEEDS_PROMPT_BUDGET: Phân đoạn của clip {item.clip_id} có kích thước kết xuất "
                    f"({single_candidate.estimated_prompt_bytes} bytes) vượt quá trần max_prompt_bytes ({config.max_prompt_bytes} bytes)"
                )
            current_batch_items.append(item)
            continue

        # Batch hiện tại đã có items -> Kiểm tra xem có thể append item vào không
        candidate_items = current_batch_items + [item]
        candidate_distinct_clips = len(set(it.clip_id for it in candidate_items))
        candidate_dur_frac = sum(Fraction(str(it.item_duration_sec)) for it in candidate_items)

        # 1. Ràng buộc cứng về số clips và hard duration (Fraction exact, 0 epsilon tolerance)
        violates_hard = (
            candidate_distinct_clips > config.max_clips_per_batch
            or candidate_dur_frac > max_batch_duration_frac
        )

        # 2. Ràng buộc mềm về target duration
        violates_soft_target = (candidate_dur_frac > target_batch_duration_frac)

        # 3. Ràng buộc cứng về byte budget
        candidate_batch = make_candidate_batch(len(batches), candidate_items)
        violates_byte_budget = (candidate_batch.estimated_prompt_bytes > config.max_prompt_bytes)

        if violates_hard or violates_soft_target or violates_byte_budget:
            # Chốt batch hiện tại và mở batch mới
            finalized_batch = make_candidate_batch(len(batches), current_batch_items)
            batches.append(finalized_batch)

            # Rất quan trọng: Sau khi flush, item sẽ bắt đầu batch mới ở index len(batches) (index mới).
            # Ta phải đo lường lại chính xác item ở index mới này để ngăn chặn hiện tượng rollover byte drift!
            new_single_candidate = make_candidate_batch(len(batches), [item])
            if new_single_candidate.estimated_prompt_bytes > config.max_prompt_bytes:
                raise ValueError(
                    f"ERR_ITEM_EXCEEDS_PROMPT_BUDGET: Phân đoạn của clip {item.clip_id} khi mở lô mới "
                    f"({new_single_candidate.estimated_prompt_bytes} bytes) vượt quá trần max_prompt_bytes ({config.max_prompt_bytes} bytes)"
                )
            current_batch_items = [item]
        else:
            current_batch_items.append(item)

    # Chốt batch cuối cùng nếu còn items
    if current_batch_items:
        finalized_batch = make_candidate_batch(len(batches), current_batch_items)
        batches.append(finalized_batch)

    # Thẩm định an toàn cuối cùng: Mọi batch được sinh ra PHẢI <= max_prompt_bytes
    for b in batches:
        if b.estimated_prompt_bytes > config.max_prompt_bytes:
            raise ValueError(
                f"ERR_BATCH_EXCEEDS_BUDGET: Lô {b.batch_id} có {b.estimated_prompt_bytes} bytes, "
                f"vượt quá max_prompt_bytes ({config.max_prompt_bytes})"
            )

    # Tính toán các chỉ số kế hoạch tổng thể
    planned_clip_ids = set(it.clip_id for b in batches for it in b.items)
    total_planned_duration = float(sum(Fraction(str(b.total_duration_sec)) for b in batches))

    return ProjectBatchPlan(
        manifest_id=manifest_id,
        project_name=project_name,
        batches=tuple(batches),
        total_clips=len(clips_seq),
        planned_clip_count=len(planned_clip_ids),
        unaccounted_clips=tuple(unaccounted_clips_list),
        issues=tuple(issues_list),
        total_duration_sec=total_planned_duration,
    )
