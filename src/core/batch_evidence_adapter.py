"""
src/core/batch_evidence_adapter.py
Adapter chuyển đổi dữ liệu evidence G05 sang batch snapshot chuẩn tắc cho G06 (G06-06A2-R2).
Pure stdlib: 0 Qt, 0 IO, 0 network, 0 models, 0 clock, 0 random.
"""

from dataclasses import dataclass
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from src.core.batch_planner import EvidenceWindowSummary

if TYPE_CHECKING:
    from src.core.clip_manifest import ClipManifest


# =============================================================================
# 1. VALUE OBJECTS & CONFIGURATION
# =============================================================================

@dataclass(frozen=True)
class AdapterConfig:
    """Cấu hình giới hạn tài nguyên cho evidence adapter."""
    max_input_records: int = 100000
    max_windows_per_clip: int = 10000

    def __post_init__(self):
        for name, val, min_v, max_v in [
            ("max_input_records", self.max_input_records, 1, 100000),
            ("max_windows_per_clip", self.max_windows_per_clip, 1, 100000),
        ]:
            if isinstance(val, bool) or not isinstance(val, int) or val < min_v or val > max_v:
                raise ValueError(
                    f"ERR_INVALID_CONFIG: {name} phải là int trong khoảng [{min_v}, {max_v}], nhận: {val!r}"
                )


@dataclass(frozen=True)
class AdapterIssue:
    """Bản ghi cảnh báo / lỗi có cấu trúc khi thích ứng evidence."""
    clip_id: str
    issue_type: str
    message: str
    record_kind: Optional[str] = None
    source_index: Optional[int] = None
    original_start_sec: Optional[float] = None
    original_end_sec: Optional[float] = None

    def __post_init__(self):
        if type(self.clip_id) is not str or not self.clip_id.strip():
            raise ValueError(f"ERR_INVALID_ISSUE: clip_id phải là str không rỗng, nhận: {self.clip_id!r}")
        if type(self.issue_type) is not str or not self.issue_type.strip():
            raise ValueError(f"ERR_INVALID_ISSUE: issue_type phải là str không rỗng, nhận: {self.issue_type!r}")
        if type(self.message) is not str or not self.message.strip():
            raise ValueError(f"ERR_INVALID_ISSUE: message phải là str không rỗng, nhận: {self.message!r}")

        for name, val in [
            ("original_start_sec", self.original_start_sec),
            ("original_end_sec", self.original_end_sec),
        ]:
            if val is not None:
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    raise ValueError(f"ERR_INVALID_ISSUE: {name} phải là số thực hoặc None, nhận: {val!r}")
                try:
                    f_val = float(val)
                except OverflowError:
                    raise ValueError(f"ERR_INVALID_ISSUE: {name} giá trị quá lớn gây tràn số: {val!r}")
                if math.isnan(f_val) or math.isinf(f_val):
                    raise ValueError(f"ERR_INVALID_ISSUE: {name} phải là số hữu hạn, nhận: {val!r}")

        if self.source_index is not None:
            if isinstance(self.source_index, bool) or not isinstance(self.source_index, int) or self.source_index < 0:
                raise ValueError(f"ERR_INVALID_ISSUE: source_index phải là int >= 0, nhận: {self.source_index!r}")


@dataclass(frozen=True)
class AdaptedBatchSnapshot:
    """Snapshot phân tích đã chuẩn hóa sẵn sàng cho A1 batch planner."""
    manifest_id: str
    analysis_cache: Mapping[str, Mapping[str, Any]]
    issues: Tuple[AdapterIssue, ...]
    audit_by_clip: Mapping[str, Mapping[str, Any]]
    context_signature: str
    word_count_method: str = "whitespace_estimate"


# =============================================================================
# 2. VALIDATION HELPERS & CANONICAL HASHING
# =============================================================================

def _validate_number(val: Any, name: str) -> float:
    """Thẩm định nghiêm ngặt số thực hữu hạn, cấm bool, NaN, Inf, Overflow."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ValueError(f"ERR_INVALID_DATA_TYPE: {name} phải là số thực hữu hạn, nhận: {val!r}")
    try:
        f_val = float(val)
    except OverflowError:
        raise ValueError(f"ERR_INVALID_DATA_TYPE: {name} giá trị quá lớn gây tràn số: {val!r}")
    if math.isnan(f_val) or math.isinf(f_val):
        raise ValueError(f"ERR_INVALID_DATA_TYPE: {name} không được là NaN hoặc Inf, nhận: {val!r}")
    return f_val


def _compute_canonical_sha256(data: Any) -> str:
    """Tính SHA256 canonical từ data structure thuần túy (deterministic, allow_nan=False)."""
    raw_json = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()


def _freeze_recursive(val: Any) -> Any:
    """Đóng băng đệ quy các container thành tuple hoặc MappingProxyType."""
    if isinstance(val, dict):
        return MappingProxyType({k: _freeze_recursive(v) for k, v in val.items()})
    elif isinstance(val, (list, tuple)):
        return tuple(_freeze_recursive(x) for x in val)
    return val


def _validate_and_normalize_coverage(cov_map: Any, name: str) -> Dict[str, Any]:
    """Thẩm định cấu trúc và kiểu dữ liệu của coverage theo schema thật VisualIndex/VisualDescriber."""
    if not isinstance(cov_map, Mapping):
        raise ValueError(f"ERR_INVALID_DATA_TYPE: {name} phải là Mapping, nhận: {type(cov_map).__name__}")

    clean_cov: Dict[str, Any] = {}
    for k, v in cov_map.items():
        if not isinstance(k, str):
            raise ValueError(f"ERR_INVALID_DATA_TYPE: khóa của {name} phải là str: {k!r}")

        if k == "complete":
            if not isinstance(v, bool):
                raise ValueError(f"ERR_INVALID_DATA_TYPE: {name}.complete phải là bool: {v!r}")
            clean_cov[k] = v
        elif k in (
            "total_windows",
            "selected_windows",
            "sampled_windows",
            "valid_windows",
            "analyzed_windows",
            "error_windows",
            "needs_review_windows",
        ):
            if v is not None:
                if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: {name}.{k} phải là int >= 0: {v!r}")
                clean_cov[k] = v
        elif k in ("sampled_ratio", "analyzed_ratio"):
            if v is not None:
                f = _validate_number(v, f"{name}.{k}")
                if f < 0.0:
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: {name}.{k} không được âm: {f}")
                clean_cov[k] = f
        elif k == "sampled_ranges":
            if v is not None:
                if not isinstance(v, (list, tuple)):
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: {name}.sampled_ranges phải là sequence")
                ranges_clean = []
                for r in v:
                    if not isinstance(r, (list, tuple)) or len(r) != 2:
                        raise ValueError(f"ERR_INVALID_DATA_TYPE: mỗi range trong sampled_ranges phải có 2 phần tử")
                    r_s = _validate_number(r[0], f"{name}.sampled_range.start")
                    r_e = _validate_number(r[1], f"{name}.sampled_range.end")
                    ranges_clean.append([r_s, r_e])
                clean_cov[k] = ranges_clean
        else:
            # Kiểm tra các giá trị tùy biến khác: chỉ nhận primitive kiểu cơ bản hữu hạn
            if v is not None:
                if isinstance(v, bool):
                    clean_cov[k] = v
                elif isinstance(v, int):
                    clean_cov[k] = v
                elif isinstance(v, float):
                    if math.isnan(v) or math.isinf(v):
                        raise ValueError(f"ERR_INVALID_DATA_TYPE: {name}.{k} là float không hữu hạn")
                    clean_cov[k] = v
                elif isinstance(v, str):
                    clean_cov[k] = v
                else:
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: {name}.{k} chứa kiểu không hợp lệ: {type(v).__name__}")

    return clean_cov


# =============================================================================
# 3. CORE ADAPTER LOGIC
# =============================================================================

def adapt_clips_data_to_batch_analysis_cache(
    manifest: Any,
    clips_data: Sequence[Mapping[str, Any]],
    config: Optional[AdapterConfig] = None,
    context_metadata: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> AdaptedBatchSnapshot:
    """
    Chuyển đổi dữ liệu clips_data từ producer G05 sang analysis_cache chuẩn tắc của A1.
    """
    if config is None:
        config = AdapterConfig()
    elif not isinstance(config, AdapterConfig):
        raise ValueError(f"ERR_INVALID_CONFIG: config phải là AdapterConfig, nhận: {type(config).__name__}")

    # 1. Thẩm định Manifest
    if manifest is None:
        raise ValueError("ERR_INVALID_MANIFEST: manifest không được None")

    raw_manifest_id = getattr(manifest, "manifest_id", None)
    if isinstance(raw_manifest_id, bool) or not isinstance(raw_manifest_id, str) or not raw_manifest_id.strip():
        raise ValueError(f"ERR_INVALID_MANIFEST: manifest_id phải là chuỗi str không rỗng, nhận: {raw_manifest_id!r}")
    manifest_id = raw_manifest_id

    raw_project_name = getattr(manifest, "project_name", None)
    if raw_project_name is None or isinstance(raw_project_name, bool) or not isinstance(raw_project_name, str):
        raise ValueError(f"ERR_INVALID_MANIFEST: project_name phải là str, nhận: {raw_project_name!r}")
    project_name = raw_project_name

    clips_seq = getattr(manifest, "clips", None)
    if clips_seq is None or not isinstance(clips_seq, (list, tuple)):
        raise ValueError(f"ERR_INVALID_MANIFEST: manifest.clips phải là sequence, nhận: {type(clips_seq).__name__}")

    manifest_clips_map: Dict[str, Any] = {}
    manifest_durations: Dict[str, Optional[float]] = {}
    for c in clips_seq:
        cid = getattr(c, "clip_id", None)
        if isinstance(cid, bool) or not isinstance(cid, str) or not cid.strip():
            raise ValueError(f"ERR_INVALID_MANIFEST: clip_id trong manifest không hợp lệ: {cid!r}")
        if cid in manifest_clips_map:
            raise ValueError(f"ERR_INVALID_MANIFEST: duplicate clip_id trong manifest: {cid!r}")

        fn = getattr(c, "file_name", None)
        if isinstance(fn, bool) or not isinstance(fn, str) or not fn.strip():
            raise ValueError(f"ERR_INVALID_MANIFEST: file_name của {cid} phải là str không rỗng: {fn!r}")

        ch = getattr(c, "content_hash", None)
        if isinstance(ch, bool) or not isinstance(ch, str) or not ch.strip():
            raise ValueError(f"ERR_INVALID_MANIFEST: content_hash của {cid} phải là str không rỗng: {ch!r}")

        dur_val = getattr(c, "duration_sec", None)
        dur_float = None
        if dur_val is not None:
            if isinstance(dur_val, bool) or not isinstance(dur_val, (int, float)):
                raise ValueError(f"ERR_INVALID_MANIFEST: duration_sec phải là số, nhận: {dur_val!r}")
            f = _validate_number(dur_val, f"manifest.clip[{cid}].duration_sec")
            if f < 0.0:
                raise ValueError(f"ERR_INVALID_MANIFEST: duration_sec không được âm: {f}")
            if f > 0.0:
                dur_float = f
        manifest_clips_map[cid] = c
        manifest_durations[cid] = dur_float

    # 2. Thẩm định Context Metadata
    if context_metadata is not None:
        if not isinstance(context_metadata, Mapping):
            raise ValueError(f"ERR_INVALID_CONTEXT_METADATA: context_metadata phải là Mapping, nhận: {type(context_metadata).__name__}")
        for cid, c_meta in context_metadata.items():
            if cid not in manifest_clips_map:
                raise ValueError(f"ERR_UNKNOWN_CONTEXT_CLIP_ID: clip_id {cid!r} trong context_metadata không có trong manifest")
            if not isinstance(c_meta, Mapping):
                raise ValueError(f"ERR_INVALID_CONTEXT_METADATA: entry cho {cid} phải là Mapping, nhận: {type(c_meta).__name__}")
            for k, v in c_meta.items():
                if k == "full_content_sha256":
                    if v is not None:
                        if type(v) is not str or len(v) != 64 or not re.fullmatch(r"[0-9a-fA-F]{64}", v):
                            raise ValueError(f"ERR_INVALID_CONTEXT_METADATA: full_content_sha256 phải là 64-hex string, nhận: {v!r}")
                elif k == "analysis_signature":
                    if v is not None and type(v) is not str:
                        raise ValueError(f"ERR_INVALID_CONTEXT_METADATA: analysis_signature phải là str, nhận: {v!r}")
                else:
                    raise ValueError(f"ERR_INVALID_CONTEXT_METADATA: khóa không hợp lệ trong context_metadata: {k!r}")

    # 3. Thẩm định Clips Data (cấu trúc danh sách & caps)
    if not isinstance(clips_data, Sequence):
        raise ValueError(f"ERR_INVALID_CLIPS_DATA: clips_data phải là Sequence, nhận: {type(clips_data).__name__}")

    if len(clips_data) > config.max_input_records:
        raise ValueError(
            f"ERR_ADAPTER_LIMIT_EXCEEDED: số lượng records ({len(clips_data)}) vượt trần ({config.max_input_records})"
        )

    seen_clip_ids = set()
    row_by_clip_id: Dict[str, Mapping[str, Any]] = {}
    normalized_intervals_by_clip: Dict[str, List[Tuple[float, float, str]]] = {}
    normalized_visual_segs_by_clip: Dict[str, List[Dict[str, Any]]] = {}
    normalized_coverage_by_clip: Dict[str, Dict[str, Any]] = {}
    normalized_analyzed_coverage_by_clip: Dict[str, Dict[str, Any]] = {}

    for row in clips_data:
        if not isinstance(row, Mapping):
            raise ValueError(f"ERR_INVALID_CLIPS_DATA: mỗi record phải là Mapping, nhận: {type(row).__name__}")

        cid = row.get("clip_id")
        if isinstance(cid, bool) or not isinstance(cid, str) or not cid.strip():
            raise ValueError(f"ERR_INVALID_CLIP_ID: clip_id phải là str không rỗng: {cid!r}")

        if cid in seen_clip_ids:
            raise ValueError(f"ERR_DUPLICATE_CLIP_ID: phát hiện clip_id trùng lặp trong clips_data: {cid!r}")
        seen_clip_ids.add(cid)

        if cid not in manifest_clips_map:
            raise ValueError(f"ERR_FOREIGN_CLIP_ID: clip_id {cid!r} không tồn tại trong manifest")

        # B.1: manifest_id là bắt buộc và phải khớp tuyệt đối
        row_m_id = row.get("manifest_id")
        if isinstance(row_m_id, bool) or not isinstance(row_m_id, str) or not row_m_id.strip() or row_m_id != manifest_id:
            raise ValueError(f"ERR_MANIFEST_ID_MISMATCH: manifest_id trong record ({row_m_id!r}) không khớp manifest ({manifest_id!r})")

        # B.2: Thẩm định source_availability
        if "source_availability" in row:
            sa = row["source_availability"]
            if sa is None or not isinstance(sa, str) or sa not in ("available", "source_missing", "source_changed", "source_error", "unknown", "unavailable"):
                raise ValueError(f"ERR_INVALID_DATA_TYPE: source_availability không hợp lệ: {sa!r}")

        # B.4: Path mismatch check
        if "path" in row and row["path"] is not None:
            if not isinstance(row["path"], str):
                raise ValueError(f"ERR_INVALID_DATA_TYPE: path phải là str, nhận: {type(row['path']).__name__}")
            manifest_cpath = getattr(manifest_clips_map[cid], "current_path", None)
            if manifest_cpath is not None and row["path"] != manifest_cpath:
                raise ValueError(f"ERR_PATH_MISMATCH: path trong row ({row['path']!r}) khác manifest.current_path ({manifest_cpath!r})")

        # B.4: Known producer fields allowlist
        for bool_fld in ("duration_known", "is_broll"):
            if bool_fld in row and row[bool_fld] is not None:
                if not isinstance(row[bool_fld], bool):
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: {bool_fld} phải là bool, nhận: {row[bool_fld]!r}")

        for int_fld in ("clip_index", "id", "visual_samples"):
            if int_fld in row and row[int_fld] is not None:
                if isinstance(row[int_fld], bool) or not isinstance(row[int_fld], int) or row[int_fld] < 0:
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: {int_fld} phải là int >= 0, nhận: {row[int_fld]!r}")

        # visual_stale_excluded_count: cho phép None hoặc int >= 0
        if "visual_stale_excluded_count" in row and row["visual_stale_excluded_count"] is not None:
            sec_v = row["visual_stale_excluded_count"]
            if isinstance(sec_v, bool) or not isinstance(sec_v, int) or sec_v < 0:
                raise ValueError(f"ERR_INVALID_DATA_TYPE: visual_stale_excluded_count phải là int >= 0 hoặc None, nhận: {sec_v!r}")

        # Audio / Visual status strings allowlist
        if "audio_authority" in row and row["audio_authority"] is not None:
            aa = row["audio_authority"]
            if not isinstance(aa, str) or aa not in ("verified", "candidate", "unavailable"):
                raise ValueError(f"ERR_INVALID_DATA_TYPE: audio_authority không hợp lệ: {aa!r}")

        if "audio_analysis_status" in row and row["audio_analysis_status"] is not None:
            aas = row["audio_analysis_status"]
            if not isinstance(aas, str) or aas not in ("transcribed", "completed_empty", "no_audio_track", "failed", "unscanned"):
                raise ValueError(f"ERR_INVALID_DATA_TYPE: audio_analysis_status không hợp lệ: {aas!r}")

        for str_fld in ("speech_status", "visual_status", "visual_analysis_status"):
            if str_fld in row and row[str_fld] is not None:
                if not isinstance(row[str_fld], str):
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: {str_fld} phải là str, nhận: {row[str_fld]!r}")

        # Thẩm định sâu visual coverage
        clean_cov = {}
        if "visual_coverage" in row and row["visual_coverage"] is not None:
            clean_cov = _validate_and_normalize_coverage(row["visual_coverage"], f"row[{cid}].visual_coverage")
        normalized_coverage_by_clip[cid] = clean_cov

        clean_ana_cov = {}
        if "visual_analyzed_coverage" in row and row["visual_analyzed_coverage"] is not None:
            clean_ana_cov = _validate_and_normalize_coverage(row["visual_analyzed_coverage"], f"row[{cid}].visual_analyzed_coverage")
        normalized_analyzed_coverage_by_clip[cid] = clean_ana_cov

        for fld in ("duration_sec", "duration"):
            if fld in row and row[fld] is not None:
                _validate_number(row[fld], f"row[{cid}].{fld}")

        # B.3: Thẩm định & chuẩn hóa dialogue_intervals (Tránh lỗi float(None))
        clean_intervals: List[Tuple[float, float, str]] = []
        if "dialogue_intervals" in row and row["dialogue_intervals"] is not None:
            if not isinstance(row["dialogue_intervals"], (list, tuple)):
                raise ValueError(f"ERR_INVALID_DATA_TYPE: dialogue_intervals của {cid} phải là sequence")
            for intv in row["dialogue_intervals"]:
                if not isinstance(intv, Mapping):
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: phần tử trong dialogue_intervals của {cid} phải là Mapping")

                # Trích xuất và đối chiếu alias start / start_sec
                s_val = None
                if "start_sec" in intv and intv["start_sec"] is not None:
                    s_val = _validate_number(intv["start_sec"], f"interval.start_sec[{cid}]")
                if "start" in intv and intv["start"] is not None:
                    alt_s = _validate_number(intv["start"], f"interval.start[{cid}]")
                    if s_val is not None and s_val != alt_s:
                        raise ValueError(f"ERR_CONFLICTING_TIMESTAMPS: start_sec ({s_val}) khác start ({alt_s}) trong clip {cid}")
                    if s_val is None:
                        s_val = alt_s

                if s_val is None:
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: dialogue interval trong {cid} bắt buộc phải có start")

                # Trích xuất và đối chiếu alias end / end_sec
                e_val = None
                if "end_sec" in intv and intv["end_sec"] is not None:
                    e_val = _validate_number(intv["end_sec"], f"interval.end_sec[{cid}]")
                if "end" in intv and intv["end"] is not None:
                    alt_e = _validate_number(intv["end"], f"interval.end[{cid}]")
                    if e_val is not None and e_val != alt_e:
                        raise ValueError(f"ERR_CONFLICTING_TIMESTAMPS: end_sec ({e_val}) khác end ({alt_e}) trong clip {cid}")
                    if e_val is None:
                        e_val = alt_e

                if e_val is None:
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: dialogue interval trong {cid} bắt buộc phải có end")

                if "text" not in intv or intv["text"] is None or not isinstance(intv["text"], str):
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: text trong dialogue_intervals của {cid} phải là str")

                clean_intervals.append((s_val, e_val, intv["text"]))

        normalized_intervals_by_clip[cid] = clean_intervals

        # B.2: Thẩm định quarantined_visual_segments trước gating
        if "quarantined_visual_segments" in row and row["quarantined_visual_segments"] is not None:
            if not isinstance(row["quarantined_visual_segments"], (list, tuple)):
                raise ValueError(f"ERR_INVALID_DATA_TYPE: quarantined_visual_segments phải là sequence")
            for q_seg in row["quarantined_visual_segments"]:
                if not isinstance(q_seg, Mapping):
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: phần tử trong quarantined_visual_segments phải là Mapping")
                if "description" in q_seg and q_seg["description"] is not None and not isinstance(q_seg["description"], str):
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: description trong quarantined_visual_segments phải là str")
                if "origin" in q_seg and q_seg["origin"] is not None and not isinstance(q_seg["origin"], str):
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: origin trong quarantined_visual_segments phải là str")
                if "analysis_status" in q_seg and q_seg["analysis_status"] is not None and not isinstance(q_seg["analysis_status"], str):
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: analysis_status trong quarantined_visual_segments phải là str")

        # B.5: Thẩm định & chuẩn hóa visual_segments trước gating
        clean_visual_segs: List[Dict[str, Any]] = []
        if "visual_segments" in row and row["visual_segments"] is not None:
            if not isinstance(row["visual_segments"], (list, tuple)):
                raise ValueError(f"ERR_INVALID_DATA_TYPE: visual_segments của {cid} phải là sequence")
            for seg in row["visual_segments"]:
                if not isinstance(seg, Mapping):
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: phần tử trong visual_segments của {cid} phải là Mapping")

                if "description" not in seg or seg["description"] is None or not isinstance(seg["description"], str):
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: description trong visual_segments của {cid} phải là str")

                if "origin" in seg and seg["origin"] is not None and not isinstance(seg["origin"], str):
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: origin trong visual_segments của {cid} phải là str")

                if "analysis_status" in seg and seg["analysis_status"] is not None and not isinstance(seg["analysis_status"], str):
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: analysis_status trong visual_segments của {cid} phải là str")

                if "window_id" in seg and seg["window_id"] is not None and not isinstance(seg["window_id"], str):
                    raise ValueError(f"ERR_INVALID_DATA_TYPE: window_id trong visual_segments của {cid} phải là str")

                s_sec = None
                if "start_sec" in seg and seg["start_sec"] is not None:
                    s_sec = _validate_number(seg["start_sec"], f"seg.start_sec[{cid}]")
                t_sec = None
                if "time_sec" in seg and seg["time_sec"] is not None:
                    t_sec = _validate_number(seg["time_sec"], f"seg.time_sec[{cid}]")
                e_sec = None
                if "end_sec" in seg and seg["end_sec"] is not None:
                    e_sec = _validate_number(seg["end_sec"], f"seg.end_sec[{cid}]")

                if s_sec is not None and t_sec is not None and s_sec != t_sec:
                    raise ValueError(f"ERR_CONFLICTING_TIMESTAMPS: start_sec ({s_sec}) khác time_sec ({t_sec}) trong visual seg của {cid}")

                # R2.A.1: observation_kind nhận 'segment' canonical từ VisualIndex.validate_segment
                obs = seg.get("observation_kind")
                if obs is not None:
                    if obs not in ("point", "segment", "interval", "range"):
                        raise ValueError(f"ERR_INVALID_DATA_TYPE: observation_kind không hợp lệ: {obs!r}")
                    if e_sec is not None and obs == "point":
                        raise ValueError(f"ERR_CONFLICTING_OBSERVATION_KIND: segment có end_sec ({e_sec}) nhưng observation_kind='point' trong {cid}")
                    if e_sec is None and obs in ("segment", "interval", "range"):
                        raise ValueError(f"ERR_CONFLICTING_OBSERVATION_KIND: segment thiếu end_sec nhưng observation_kind={obs!r} trong {cid}")

                objs_clean: Tuple[str, ...] = ()
                if "objects" in seg and seg["objects"] is not None:
                    if not isinstance(seg["objects"], (list, tuple)) or not all(isinstance(x, str) for x in seg["objects"]):
                        raise ValueError(f"ERR_INVALID_DATA_TYPE: objects phải là list of str trong visual seg của {cid}")
                    objs_clean = tuple(seg["objects"])

                acts_clean: Tuple[str, ...] = ()
                if "observed_actions" in seg and seg["observed_actions"] is not None:
                    if not isinstance(seg["observed_actions"], (list, tuple)) or not all(isinstance(x, str) for x in seg["observed_actions"]):
                        raise ValueError(f"ERR_INVALID_DATA_TYPE: observed_actions phải là list of str trong visual seg của {cid}")
                    acts_clean = tuple(seg["observed_actions"])

                et_clean: Tuple[float, ...] = ()
                if "evidence_times" in seg and seg["evidence_times"] is not None:
                    if not isinstance(seg["evidence_times"], (list, tuple)):
                        raise ValueError(f"ERR_INVALID_DATA_TYPE: evidence_times phải là sequence trong visual seg của {cid}")
                    et_clean = tuple(_validate_number(et, f"seg.evidence_time[{cid}]") for et in seg["evidence_times"])

                clean_visual_segs.append({
                    "description": seg["description"],
                    "origin": seg.get("origin"),
                    "analysis_status": seg.get("analysis_status"),
                    "observation_kind": obs,
                    "window_id": seg.get("window_id"),
                    "start_sec": s_sec,
                    "time_sec": t_sec,
                    "end_sec": e_sec,
                    "objects": objs_clean,
                    "observed_actions": acts_clean,
                    "evidence_times": et_clean,
                })

        normalized_visual_segs_by_clip[cid] = clean_visual_segs
        row_by_clip_id[cid] = row

    # 4. Xử lý từng clip theo thứ tự Manifest (SSOT)
    analysis_cache_out: Dict[str, Dict[str, Any]] = {}
    audit_by_clip_out: Dict[str, Dict[str, Any]] = {}
    issues_list: List[AdapterIssue] = []
    clip_signatures_for_snapshot: List[Tuple[str, str, str, Optional[bool], int]] = []

    for clip in clips_seq:
        cid = clip.clip_id
        manifest_dur = manifest_durations[cid]
        clip_manifest_entry = manifest_clips_map[cid]

        # Context metadata cho clip
        clip_c_meta = context_metadata.get(cid, {}) if context_metadata else {}
        supplied_full_sha = clip_c_meta.get("full_content_sha256")
        if supplied_full_sha is not None:
            supplied_full_sha = str(supplied_full_sha).lower()
        supplied_sig = clip_c_meta.get("analysis_signature")

        # Trường hợp A: Clip vắng mặt trong dữ liệu producer
        if cid not in row_by_clip_id:
            if manifest_dur is not None and 0.0 < manifest_dur < 1.0:
                iss_type = "SHORT_CLIP_UNSCANNED"
                iss_msg = f"Clip ngắn ({manifest_dur}s < 1.0s) vắng mặt trong producer, hạch toán unscanned"
            else:
                iss_type = "MISSING_PRODUCER_ENTRY"
                iss_msg = f"Clip {cid} vắng mặt trong producer, hạch toán unscanned"

            iss = AdapterIssue(clip_id=cid, issue_type=iss_type, message=iss_msg)
            issues_list.append(iss)

            clip_canonical_data = {
                "clip_id": cid,
                "file_name": clip_manifest_entry.file_name,
                "content_hash": clip_manifest_entry.content_hash,
                "current_path": getattr(clip_manifest_entry, "current_path", None),
                "manifest_duration_sec": manifest_dur,
                "source_availability": "unavailable",
                "audio_authority": "unavailable",
                "audio_analysis_status": "unscanned",
                "speech_status": "unscanned",
                "has_audio": None,
                "visual_status": None,
                "visual_analysis_status": None,
                "full_content_sha256": supplied_full_sha,
                "supplied_signature": supplied_sig,
                "visual_coverage": {},
                "visual_analyzed_coverage": {},
                "visual_stale_excluded_count": 0,
                "windows": [],
            }
            c_sig = _compute_canonical_sha256(clip_canonical_data)

            analysis_cache_out[cid] = {
                "speech_status": "unscanned",
                "has_audio": None,
                "full_content_sha256": supplied_full_sha,
                "analysis_signature": c_sig,
                "windows": (),
            }
            audit_by_clip_out[cid] = {
                "origin": "manifest_absent",
                "producer_status": "missing",
                "canonical_signature": c_sig,
                "supplied_signature": supplied_sig,
                "supplied_full_content_sha256": supplied_full_sha,
                "windows_audit": (),
            }
            clip_signatures_for_snapshot.append((cid, c_sig, "unscanned", None, 0))
            continue

        # Trường hợp B: Clip có trong dữ liệu producer
        row = row_by_clip_id[cid]
        vis_coverage_clean = normalized_coverage_by_clip[cid]
        vis_analyzed_coverage_clean = normalized_analyzed_coverage_by_clip[cid]
        vis_stale_count = int(row["visual_stale_excluded_count"]) if row.get("visual_stale_excluded_count") is not None else 0

        # B.1. Kiểm tra Duration SSOT
        row_dur = row.get("duration_sec")
        if row_dur is not None and manifest_dur is not None:
            if abs(float(row_dur) - float(manifest_dur)) > 1e-4:
                issues_list.append(
                    AdapterIssue(
                        clip_id=cid,
                        issue_type="DURATION_MISMATCH",
                        message=f"Duration lệch: row={row_dur}s vs manifest={manifest_dur}s; manifest làm SSOT",
                        original_start_sec=float(manifest_dur),
                        original_end_sec=float(row_dur),
                    )
                )

        if manifest_dur is None:
            issues_list.append(
                AdapterIssue(
                    clip_id=cid,
                    issue_type="ERR_UNKNOWN_DURATION",
                    message=f"Manifest duration của clip {cid} không xác định (None hoặc <=0), không chấp nhận timed windows",
                )
            )

        # A.1: Source Availability Positive Gate
        src_avail = row.get("source_availability")
        if src_avail is None:
            src_avail = "unavailable"
            issues_list.append(
                AdapterIssue(
                    clip_id=cid,
                    issue_type="SOURCE_UNAVAILABLE",
                    message=f"Nguồn {cid} thiếu source_availability, coi là unavailable",
                )
            )

        if src_avail != "available":
            iss_code = f"SOURCE_{src_avail.upper()}"
            if not any(i.clip_id == cid and i.issue_type == iss_code for i in issues_list):
                issues_list.append(
                    AdapterIssue(
                        clip_id=cid,
                        issue_type=iss_code,
                        message=f"Nguồn {cid} có trạng thái {src_avail}, loại bỏ toàn bộ evidence",
                    )
                )
            clip_canonical_data = {
                "clip_id": cid,
                "file_name": clip_manifest_entry.file_name,
                "content_hash": clip_manifest_entry.content_hash,
                "current_path": getattr(clip_manifest_entry, "current_path", None),
                "manifest_duration_sec": manifest_dur,
                "source_availability": src_avail,
                "audio_authority": row.get("audio_authority"),
                "audio_analysis_status": row.get("audio_analysis_status"),
                "speech_status": src_avail,
                "has_audio": None,
                "visual_status": row.get("visual_status"),
                "visual_analysis_status": row.get("visual_analysis_status"),
                "full_content_sha256": supplied_full_sha,
                "supplied_signature": supplied_sig,
                "visual_coverage": vis_coverage_clean,
                "visual_analyzed_coverage": vis_analyzed_coverage_clean,
                "visual_stale_excluded_count": vis_stale_count,
                "windows": [],
            }
            c_sig = _compute_canonical_sha256(clip_canonical_data)

            analysis_cache_out[cid] = {
                "speech_status": src_avail,
                "has_audio": None,
                "full_content_sha256": supplied_full_sha,
                "analysis_signature": c_sig,
                "windows": (),
            }
            audit_by_clip_out[cid] = {
                "origin": "producer_row",
                "source_availability": src_avail,
                "canonical_signature": c_sig,
                "supplied_signature": supplied_sig,
                "supplied_full_content_sha256": supplied_full_sha,
                "windows_audit": (),
            }
            clip_signatures_for_snapshot.append((cid, c_sig, src_avail, None, 0))
            continue

        # B.3. Audio Authority & Status Mapping (A.3: Positive Gates, không fallback transcribed)
        audio_auth = row.get("audio_authority", "unavailable")
        audio_status = row.get("audio_analysis_status", "unscanned")
        raw_speech_status = row.get("speech_status", "unscanned")
        clean_intervals = normalized_intervals_by_clip[cid]

        usable_speech_windows: List[EvidenceWindowSummary] = []
        speech_window_audits: List[Dict[str, Any]] = []
        final_has_audio: Optional[bool] = None
        final_speech_status: str = "unscanned"

        if audio_status == "failed" or raw_speech_status in ("scan_error", "error") or audio_auth == "unavailable":
            final_speech_status = "scan_error" if (audio_status == "failed" or raw_speech_status in ("scan_error", "error")) else "unscanned"
            final_has_audio = None
            issues_list.append(
                AdapterIssue(
                    clip_id=cid,
                    issue_type="AUDIO_ERROR",
                    message=f"Audio phân tích thất bại hoặc unavailable: {audio_status} (authority={audio_auth})",
                )
            )
        elif audio_auth == "candidate":
            final_speech_status = "unscanned"
            final_has_audio = None
            issues_list.append(
                AdapterIssue(
                    clip_id=cid,
                    issue_type="AUDIO_UNVERIFIED",
                    message="Audio authority là candidate, chưa được xác minh",
                )
            )
        elif audio_auth == "verified":
            if audio_status == "no_audio_track":
                if len(clean_intervals) > 0:
                    final_speech_status = "scan_error"
                    final_has_audio = None
                    issues_list.append(
                        AdapterIssue(
                            clip_id=cid,
                            issue_type="AUDIO_CONTRADICTION",
                            message="Mâu thuẫn: no_audio_track nhưng có dialogue_intervals",
                        )
                    )
                else:
                    final_speech_status = "no_audio_track"
                    final_has_audio = False
            elif audio_status == "completed_empty":
                if len(clean_intervals) > 0:
                    final_speech_status = "scan_error"
                    final_has_audio = None
                    issues_list.append(
                        AdapterIssue(
                            clip_id=cid,
                            issue_type="AUDIO_CONTRADICTION",
                            message="Mâu thuẫn: completed_empty nhưng có dialogue_intervals",
                        )
                    )
                else:
                    final_speech_status = "completed_empty"
                    final_has_audio = None
            elif audio_status == "transcribed":
                final_speech_status = "transcribed"
                final_has_audio = True

                if manifest_dur is not None:
                    for s_idx, (f_s, f_e, txt_in) in enumerate(clean_intervals):
                        txt_clean = txt_in.strip()
                        if not txt_clean:
                            issues_list.append(
                                AdapterIssue(
                                    clip_id=cid,
                                    issue_type="EMPTY_EVIDENCE",
                                    message="Dialogue interval có text rỗng",
                                    record_kind="speech",
                                    source_index=s_idx,
                                    original_start_sec=f_s,
                                    original_end_sec=f_e,
                                )
                            )
                            continue

                        # ZERO CLAMPING: khoảng cách 0 <= start < end <= manifest_dur
                        if f_s < 0.0 or f_s >= f_e or f_e > manifest_dur:
                            issues_list.append(
                                AdapterIssue(
                                    clip_id=cid,
                                    issue_type="WINDOW_OUT_OF_BOUNDS",
                                    message=f"Dialogue interval [{f_s}, {f_e}] vượt phạm vi [0.0, {manifest_dur}]",
                                    record_kind="speech",
                                    source_index=s_idx,
                                    original_start_sec=f_s,
                                    original_end_sec=f_e,
                                )
                            )
                            continue

                        w_id = f"{cid}:speech:{s_idx}"
                        # B.5: Giữ raw text verbatim
                        w_summary = EvidenceWindowSummary(
                            window_id=w_id,
                            start_sec=f_s,
                            end_sec=f_e,
                            speech_summary=txt_in,
                            speech_word_count=len(txt_clean.split()),
                            visual_action="",
                            has_audio=True,
                            is_sampled=False,
                            status="transcribed",
                            origin="whisper",
                            overlap=False,
                            clipped=False,
                        )
                        usable_speech_windows.append(w_summary)
                        speech_window_audits.append({
                            "window_id": w_id,
                            "source_index": s_idx,
                            "modality": "speech",
                            "origin": "whisper",
                            "status": "transcribed",
                            "producer_window_id": None,
                            "objects": (),
                            "observed_actions": (),
                            "evidence_times": (),
                        })
            else:
                final_speech_status = "unscanned"
                final_has_audio = None
        else:
            final_speech_status = "unscanned"
            final_has_audio = None

        # B.4. Visual & User Records Mapping (A.2: Positive Gates)
        vis_top_status = row.get("visual_analysis_status")
        clean_visual_segs = normalized_visual_segs_by_clip[cid]
        quarantined_segs = row.get("quarantined_visual_segments") or []

        usable_visual_windows: List[EvidenceWindowSummary] = []
        visual_window_audits: List[Dict[str, Any]] = []

        if manifest_dur is not None:
            # A.2: Machine visual chỉ cấp phép khi parent analysis status explicit có bằng chứng thật
            machine_parent_valid = vis_top_status in ("complete", "partial", "needs_review", "described")

            for v_idx, seg in enumerate(clean_visual_segs):
                origin_raw = seg.get("origin")
                if origin_raw is None or not isinstance(origin_raw, str) or not origin_raw.strip():
                    issues_list.append(
                        AdapterIssue(
                            clip_id=cid,
                            issue_type="VISUAL_SEGMENT_MISSING_ORIGIN",
                            message="Visual segment thiếu origin hợp lệ",
                            record_kind="visual",
                            source_index=v_idx,
                        )
                    )
                    continue

                origin_lit = origin_raw
                is_user = (origin_lit == "user")

                # A.2: Parent status pending/unknown/stale/etc chặn machine
                if not is_user and not machine_parent_valid:
                    issues_list.append(
                        AdapterIssue(
                            clip_id=cid,
                            issue_type="VISUAL_ANALYSIS_UNVERIFIED",
                            message=f"Top visual status {vis_top_status!r} không cấp quyền machine segment",
                            record_kind="visual",
                            source_index=v_idx,
                        )
                    )
                    continue

                seg_status = seg.get("analysis_status")
                if seg_status is None or not isinstance(seg_status, str) or not seg_status.strip():
                    issues_list.append(
                        AdapterIssue(
                            clip_id=cid,
                            issue_type="VISUAL_SEGMENT_MISSING_STATUS",
                            message="Visual segment thiếu analysis_status hợp lệ",
                            record_kind="user" if is_user else "visual",
                            source_index=v_idx,
                        )
                    )
                    continue

                if not is_user and seg_status not in ("described", "needs_review"):
                    issues_list.append(
                        AdapterIssue(
                            clip_id=cid,
                            issue_type=f"VISUAL_SEGMENT_{seg_status.upper()}",
                            message=f"Machine segment có status {seg_status}",
                            record_kind="visual",
                            source_index=v_idx,
                        )
                    )
                    continue

                raw_desc = seg["description"]
                desc_clean = raw_desc.strip()
                if not desc_clean:
                    issues_list.append(
                        AdapterIssue(
                            clip_id=cid,
                            issue_type="EMPTY_EVIDENCE",
                            message="Visual segment thiếu mô tả",
                            record_kind="user" if is_user else "visual",
                            source_index=v_idx,
                        )
                    )
                    continue

                # Xác định Point vs Interval
                s_sec = seg["start_sec"]
                t_sec = seg["time_sec"]
                e_sec = seg["end_sec"]
                obs_kind = seg["observation_kind"]

                is_point = (e_sec is None) or (obs_kind == "point")

                # A.3: Machine cấm dùng point schema
                if is_point:
                    if not is_user:
                        issues_list.append(
                            AdapterIssue(
                                clip_id=cid,
                                issue_type="ERR_MACHINE_MISSING_RANGE",
                                message="Machine segment thiếu end_sec (chỉ user note được dùng point schema)",
                                record_kind="visual",
                                source_index=v_idx,
                            )
                        )
                        continue

                    raw_t = t_sec if t_sec is not None else s_sec
                    if raw_t is None:
                        issues_list.append(
                            AdapterIssue(
                                clip_id=cid,
                                issue_type="ERR_POINT_MISSING_TIME",
                                message="Point note thiếu time_sec",
                                record_kind="user",
                                source_index=v_idx,
                            )
                        )
                        continue

                    f_t = float(raw_t)
                    # ZERO CLAMPING cho Point: 0.0 <= t < manifest_dur (strict < duration)
                    if f_t < 0.0 or f_t >= manifest_dur:
                        issues_list.append(
                            AdapterIssue(
                                clip_id=cid,
                                issue_type="WINDOW_OUT_OF_BOUNDS",
                                message=f"Point note {f_t}s vượt phạm vi [0.0, {manifest_dur})",
                                record_kind="user",
                                source_index=v_idx,
                                original_start_sec=f_t,
                                original_end_sec=None,
                            )
                        )
                        continue

                    w_id = f"{cid}:user:{v_idx}"
                    w_summary = EvidenceWindowSummary(
                        window_id=w_id,
                        start_sec=f_t,
                        end_sec=f_t,
                        speech_summary="",
                        speech_word_count=0,
                        visual_action=raw_desc,
                        has_audio=None,
                        is_sampled=True,
                        status=seg_status,
                        origin=origin_lit,
                        overlap=False,
                        clipped=False,
                    )
                    usable_visual_windows.append(w_summary)
                    visual_window_audits.append({
                        "window_id": w_id,
                        "source_index": v_idx,
                        "modality": "user",
                        "origin": origin_lit,
                        "status": seg_status,
                        "producer_window_id": seg.get("window_id"),
                        "objects": seg["objects"],
                        "observed_actions": seg["observed_actions"],
                        "evidence_times": seg["evidence_times"],
                    })
                else:
                    # Interval segment
                    raw_s = s_sec if s_sec is not None else t_sec
                    if raw_s is None or e_sec is None:
                        continue
                    f_s = float(raw_s)
                    f_e = float(e_sec)

                    # ZERO CLAMPING cho Interval: 0.0 <= start < end <= manifest_dur
                    if f_s < 0.0 or f_s >= f_e or f_e > manifest_dur:
                        issues_list.append(
                            AdapterIssue(
                                clip_id=cid,
                                issue_type="WINDOW_OUT_OF_BOUNDS",
                                message=f"Visual interval [{f_s}, {f_e}] vượt phạm vi [0.0, {manifest_dur}]",
                                record_kind="user" if is_user else "visual",
                                source_index=v_idx,
                                original_start_sec=f_s,
                                original_end_sec=f_e,
                            )
                        )
                        continue

                    modality = "user" if is_user else "visual"
                    w_id = f"{cid}:{modality}:{v_idx}"
                    w_summary = EvidenceWindowSummary(
                        window_id=w_id,
                        start_sec=f_s,
                        end_sec=f_e,
                        speech_summary="",
                        speech_word_count=0,
                        visual_action=raw_desc,
                        has_audio=None,
                        is_sampled=True,
                        status=seg_status,
                        origin=origin_lit,
                        overlap=False,
                        clipped=False,
                    )
                    usable_visual_windows.append(w_summary)
                    visual_window_audits.append({
                        "window_id": w_id,
                        "source_index": v_idx,
                        "modality": modality,
                        "origin": origin_lit,
                        "status": seg_status,
                        "producer_window_id": seg.get("window_id"),
                        "objects": seg["objects"],
                        "observed_actions": seg["observed_actions"],
                        "evidence_times": seg["evidence_times"],
                    })

        # B.5. Hợp nhất Windows và kiểm tra Caps
        all_clip_windows = usable_speech_windows + usable_visual_windows
        all_windows_audits = speech_window_audits + visual_window_audits
        if len(all_clip_windows) > config.max_windows_per_clip:
            raise ValueError(
                f"ERR_ADAPTER_LIMIT_EXCEEDED: số windows của clip {cid} ({len(all_clip_windows)}) vượt trần ({config.max_windows_per_clip})"
            )

        # C.1 / C.2: Tính toán Canonical Signature cho Clip (Chứa đầy đủ identity, status, coverage, objects)
        clip_canonical_data = {
            "clip_id": cid,
            "file_name": clip_manifest_entry.file_name,
            "content_hash": clip_manifest_entry.content_hash,
            "current_path": getattr(clip_manifest_entry, "current_path", None),
            "manifest_duration_sec": manifest_dur,
            "source_availability": src_avail,
            "audio_authority": audio_auth,
            "audio_analysis_status": audio_status,
            "speech_status": final_speech_status,
            "has_audio": final_has_audio,
            "visual_status": row.get("visual_status"),
            "visual_analysis_status": vis_top_status,
            "full_content_sha256": supplied_full_sha,
            "supplied_signature": supplied_sig,
            "visual_coverage": vis_coverage_clean,
            "visual_analyzed_coverage": vis_analyzed_coverage_clean,
            "visual_stale_excluded_count": vis_stale_count,
            "windows": [
                {
                    "window_id": w.window_id,
                    "producer_window_id": audit_item["producer_window_id"],
                    "start_sec": w.start_sec,
                    "end_sec": w.end_sec,
                    "speech_summary": w.speech_summary,
                    "speech_word_count": w.speech_word_count,
                    "visual_action": w.visual_action,
                    "has_audio": w.has_audio,
                    "is_sampled": w.is_sampled,
                    "status": w.status,
                    "origin": w.origin,
                    "objects": audit_item["objects"],
                    "observed_actions": audit_item["observed_actions"],
                    "evidence_times": audit_item["evidence_times"],
                }
                for w, audit_item in zip(all_clip_windows, all_windows_audits)
            ],
        }
        c_sig = _compute_canonical_sha256(clip_canonical_data)

        # C.1: analysis_cache luôn chứa generated semantic analysis_signature
        analysis_cache_out[cid] = {
            "speech_status": final_speech_status,
            "has_audio": final_has_audio,
            "full_content_sha256": supplied_full_sha,
            "analysis_signature": c_sig,
            "windows": tuple(all_clip_windows),
        }
        audit_by_clip_out[cid] = {
            "origin": "producer_row",
            "source_availability": src_avail,
            "canonical_signature": c_sig,
            "supplied_signature": supplied_sig,
            "supplied_full_content_sha256": supplied_full_sha,
            "producer_speech_windows_count": len(clean_intervals),
            "usable_speech_windows_count": len(usable_speech_windows),
            "producer_visual_segments_count": len(clean_visual_segs),
            "usable_visual_windows_count": len(usable_visual_windows),
            "quarantined_count": len(quarantined_segs),
            "visual_coverage": vis_coverage_clean,
            "visual_analyzed_coverage": vis_analyzed_coverage_clean,
            "visual_stale_excluded_count": vis_stale_count,
            "windows_audit": tuple(all_windows_audits),
        }
        clip_signatures_for_snapshot.append((cid, c_sig, final_speech_status, final_has_audio, len(all_clip_windows)))

    # 5. Tính Snapshot Context Signature
    snapshot_canonical_data = {
        "manifest_id": manifest_id,
        "project_name": project_name,
        "config": {
            "max_input_records": config.max_input_records,
            "max_windows_per_clip": config.max_windows_per_clip,
        },
        "clips": clip_signatures_for_snapshot,
        "issues": [
            {
                "clip_id": iss.clip_id,
                "issue_type": iss.issue_type,
                "record_kind": iss.record_kind,
                "source_index": iss.source_index,
                "start": iss.original_start_sec,
                "end": iss.original_end_sec,
            }
            for iss in issues_list
        ],
    }
    context_sig = _compute_canonical_sha256(snapshot_canonical_data)

    # 6. Đóng băng bất biến đệ quy (Recursive Immutability)
    frozen_analysis_cache = _freeze_recursive(analysis_cache_out)
    frozen_audit_by_clip = _freeze_recursive(audit_by_clip_out)

    return AdaptedBatchSnapshot(
        manifest_id=manifest_id,
        analysis_cache=frozen_analysis_cache,
        issues=tuple(issues_list),
        audit_by_clip=frozen_audit_by_clip,
        context_signature=context_sig,
        word_count_method="whitespace_estimate",
    )
