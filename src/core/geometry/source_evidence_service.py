"""src/core/geometry/source_evidence_service.py
Dịch vụ thu thập và kiểm định bằng chứng khung hình video cục bộ (G07-07A3-I / G07-07A3-R1).

RÀNG BUỘC KỸ THUẬT:
1. Thuần Python standard library (dataclasses, fractions, os, sys, subprocess, threading, time, typing).
2. Qt-free: Tuyệt đối không import PySide6, UI, QSettings, hay DaVinci Resolve API.
3. Không ghi nhận trạng thái toàn cục (zero global mutation, không mutate PATH/cwd/env).
4. Phân tách rõ ràng giữa siêu dữ liệu container do header báo cáo (VideoStreamHeader)
   và bằng chứng khung hình nguyên bản đã kiểm định (SourceFrameEvidence 5 fields).
5. Hồ sơ kiểm định v1: decoded_frame_uniform_ticks_v1:
   - Dùng ffprobe -show_frames với selected GLOBAL stream index (không dùng v:N hay -show_packets).
   - Kiểm tra Presentation Timestamps (PTS) giải mã thực tế (presentation order).
   - B-frames có packet order không đơn điệu vẫn được xác nhận CFR nếu decoded frame PTS đều đặn.
   - Streaming read có giới hạn bộ nhớ chặt chẽ (max_decoded_frames, stdout_budget, stderr_budget, per_record_budget).
   - Không dựng full giant JSON list trong RAM.
6. Bộ nhớ đệm RAM (in-memory cache) thuộc sở hữu của thể hiện SourceEvidenceService:
   - Chỉ lưu trữ chứng chỉ hoàn tất (complete proof) theo khóa 5-stat + global_stream_index + policy.
   - Cache hit vẫn kiểm tra stat trên đĩa (không claim '0 disk IO').
   - Rebind manifest_id, clip_id, expected_full_sha256 theo từng request của caller.
   - Cache hit thực hiện 0 lần probe/header acquisition/hash.
   - Constructor có 0 I/O (không gọi shutil.which khi khởi tạo).
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import codecs
import json
import math
import os
from pathlib import Path
import re
import select
import shutil
import stat
import subprocess
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union
import types

from src.core.cache_manager import compute_full_file_sha256, is_canonical_sha256_hex
from src.core.contracts.time_contracts import RationalFPS
from src.core.geometry.frame_geometry_contracts import validate_fps_instance
from src.core.geometry.local_metadata_acquisition import (
    SourceFreshnessStat,
    _BoundedPipeReader,
    _capture_source_freshness,
    _cleanup_child_and_readers,
    acquire_local_video_header,
)
from src.core.geometry.plan_geometry_contracts import (
    MAX_STRING_BYTES,
    SourceFrameEvidence,
    _validate_clean_str,
    reconstruct_source_frame_evidence,
)
from src.core.geometry.source_frame_evidence_contracts import VideoStreamHeader

MAX_ITEMS_PER_BATCH: int = 4096
DEFAULT_TIMEOUT_PER_FILE_SEC: float = 15.0
DEFAULT_MAX_DECODED_FRAMES: int = 1_000_000
DEFAULT_STDOUT_BUDGET_BYTES: int = 128 * 1024 * 1024  # 128 MiB
DEFAULT_STDERR_BUDGET_BYTES: int = 64 * 1024          # 64 KiB
DEFAULT_RECORD_BUDGET_BYTES: int = 16 * 1024          # 16 KiB
READ_CHUNK_BYTES: int = 64 * 1024                     # 64 KiB

PROFILE_UNIFORM_TICKS_V1: str = "decoded_frame_uniform_ticks_v1"
VALID_EVIDENCE_STATUSES: frozenset[str] = frozenset({"VERIFIED_CFR", "HEADER_REPORTED_ONLY", "VERIFIED_VFR"})


def _is_safe_finite_float(
    val: Any,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
) -> bool:
    """Kiểm tra số thực/nguyên an toàn, không bool, không NaN/Inf, nằm trong khoảng."""
    if type(val) not in (int, float) or isinstance(val, bool):
        return False
    try:
        f = float(val)
        if not math.isfinite(f):
            return False
    except (OverflowError, ValueError):
        return False
    if min_val is not None and f < min_val:
        return False
    if max_val is not None and f > max_val:
        return False
    return True


def _validate_file_path(path: Any, field_name: str = "file_path") -> str:
    """Xác thực nghiêm ngặt đường dẫn tệp cục bộ tuyệt đối."""
    if type(path) is not str:
        raise ValueError(f"ERR_INVALID_PATH: {field_name} phải là str, nhận: {type(path).__name__}")
    if not path or path.isspace() or "\x00" in path:
        raise ValueError(f"ERR_INVALID_PATH: {field_name} không được để trống hoặc chứa NUL")
    if "://" in path:
        raise ValueError(f"ERR_INVALID_PATH: {field_name} không được là URL/URI mạng")
    if not os.path.isabs(path):
        raise ValueError(f"ERR_INVALID_PATH: {field_name} phải là đường dẫn tuyệt đối, nhận: {path!r}")
    return os.path.normpath(path)


@dataclass(frozen=True)
class SourceEvidenceItemRequest:
    """Yêu cầu thu thập bằng chứng cho một clip nguồn cụ thể.

    Quy định:
    - clip_id: Mã định danh clip trong source_binding (bắt buộc str không rỗng).
    - manifest_id: Mã định danh bảng kê nguồn (bắt buộc str không rỗng).
    - file_path: Đường dẫn tệp cục bộ tuyệt đối.
    - stream_index: Chỉ số stream video toàn cục mong muốn (Optional[int] >= 0; None để tự động chọn).
    - expected_full_sha256: Mã băm toàn bộ tệp mong đợi từ proposal (Optional[str] 64-hex).
    """

    clip_id: str
    manifest_id: str
    file_path: str
    stream_index: Optional[int] = None
    expected_full_sha256: Optional[str] = None

    def __post_init__(self) -> None:
        c_id = _validate_clean_str(self.clip_id, "clip_id", allow_blank=False)
        m_id = _validate_clean_str(self.manifest_id, "manifest_id", allow_blank=False)
        norm_path = _validate_file_path(self.file_path, "file_path")

        object.__setattr__(self, "clip_id", c_id)
        object.__setattr__(self, "manifest_id", m_id)
        object.__setattr__(self, "file_path", norm_path)

        if self.stream_index is not None:
            if type(self.stream_index) is not int or isinstance(self.stream_index, bool) or self.stream_index < 0:
                raise ValueError(
                    f"ERR_INVALID_STREAM_INDEX: stream_index phải là int >= 0 hoặc None, "
                    f"nhận: {self.stream_index!r} ({type(self.stream_index).__name__})"
                )

        if self.expected_full_sha256 is not None:
            if type(self.expected_full_sha256) is not str or isinstance(self.expected_full_sha256, bool) or not is_canonical_sha256_hex(self.expected_full_sha256):
                raise ValueError(
                    f"ERR_INVALID_DIGEST: expected_full_sha256 phải là chuỗi SHA-256 64 ký tự hex, "
                    f"nhận: {self.expected_full_sha256!r}"
                )
            object.__setattr__(self, "expected_full_sha256", self.expected_full_sha256.lower())


@dataclass(frozen=True)
class SourceEvidenceBatchRequest:
    """Yêu cầu thu thập bằng chứng cho một lô các clip nguồn trong cùng manifest."""

    items: Tuple[SourceEvidenceItemRequest, ...]
    timeout_per_file_sec: float = DEFAULT_TIMEOUT_PER_FILE_SEC
    max_decoded_frames: int = DEFAULT_MAX_DECODED_FRAMES
    stdout_budget_bytes: int = DEFAULT_STDOUT_BUDGET_BYTES
    stderr_budget_bytes: int = DEFAULT_STDERR_BUDGET_BYTES

    def __post_init__(self) -> None:
        if not isinstance(self.items, (list, tuple)):
            raise ValueError(f"ERR_INVALID_ITEMS: items phải là list hoặc tuple, nhận: {type(self.items).__name__}")
        if len(self.items) > MAX_ITEMS_PER_BATCH:
            raise ValueError(
                f"ERR_COUNT_OVERFLOW: số lượng items ({len(self.items)}) vượt quá giới hạn tối đa {MAX_ITEMS_PER_BATCH}"
            )

        owned_items: List[SourceEvidenceItemRequest] = []
        seen_clips: Set[str] = set()
        first_manifest_id: Optional[str] = None

        for idx, it in enumerate(self.items):
            if not isinstance(it, SourceEvidenceItemRequest):
                raise ValueError(
                    f"ERR_INVALID_ITEM_TYPE: items[{idx}] phải là SourceEvidenceItemRequest, "
                    f"nhận: {type(it).__name__}"
                )
            
            # Revalidate typed boundaries strictly to prevent trust bypass via object.__setattr__
            s_idx = it.stream_index
            if s_idx is not None and (type(s_idx) is not int or isinstance(s_idx, bool) or s_idx < 0):
                raise ValueError(f"ERR_INVALID_STREAM_INDEX: stream_index phải là int >= 0 hoặc None, nhận: {s_idx!r}")

            exp_sha = it.expected_full_sha256
            if exp_sha is not None and (type(exp_sha) is not str or isinstance(exp_sha, bool) or not is_canonical_sha256_hex(exp_sha)):
                raise ValueError(f"ERR_INVALID_DIGEST: expected_full_sha256 không hợp lệ: {exp_sha!r}")

            # Defensive clone
            rebound_item = SourceEvidenceItemRequest(
                clip_id=it.clip_id,
                manifest_id=it.manifest_id,
                file_path=it.file_path,
                stream_index=s_idx,
                expected_full_sha256=exp_sha,
            )

            if rebound_item.clip_id in seen_clips:
                raise ValueError(f"ERR_DUPLICATE_CLIP_ID: clip_id {rebound_item.clip_id!r} bị trùng lặp trong batch request.")
            seen_clips.add(rebound_item.clip_id)

            if first_manifest_id is None:
                first_manifest_id = rebound_item.manifest_id
            elif rebound_item.manifest_id != first_manifest_id:
                raise ValueError(
                    f"ERR_MANIFEST_ID_MISMATCH: tất cả items trong batch phải có cùng manifest_id "
                    f"({first_manifest_id!r}), mục {rebound_item.clip_id!r} có {rebound_item.manifest_id!r}"
                )
            owned_items.append(rebound_item)

        object.__setattr__(self, "items", tuple(owned_items))

        if not _is_safe_finite_float(self.timeout_per_file_sec, min_val=0.001, max_val=300.0):
            raise ValueError(
                f"ERR_INVALID_TIMEOUT: timeout_per_file_sec phải là số thực hữu hạn trong (0, 300], "
                f"nhận: {self.timeout_per_file_sec!r}"
            )

        for name, val, min_v, max_v in (
            ("max_decoded_frames", self.max_decoded_frames, 1, 10_000_000),
            ("stdout_budget_bytes", self.stdout_budget_bytes, 1024, 256 * 1024 * 1024),
            ("stderr_budget_bytes", self.stderr_budget_bytes, 1024, 10 * 1024 * 1024),
        ):
            if type(val) is not int or isinstance(val, bool) or val < min_v or val > max_v:
                raise ValueError(
                    f"ERR_INVALID_BUDGET: {name} phải là int trong [{min_v}, {max_v}], "
                    f"nhận: {val!r} ({type(val).__name__})"
                )


@dataclass(frozen=True)
class SourceExecutionEvidenceEnvelope:
    """Bao bì chứa đầy đủ bằng chứng thi công của một clip nguồn.

    Tách biệt:
    - expected_full_sha256: Kỳ vọng từ proposal (không bao giờ tự suy diễn).
    - observed_full_sha256: Đo lường thực tế từ tệp trên đĩa (SHA256 toàn vẹn 100%).
    - freshness: Ảnh chụp 5-stat của tệp tại thời điểm kiểm định.
    - frame_evidence: SourceFrameEvidence chuẩn tắc 5 fields theo plan_geometry_contracts.
    """

    clip_id: str
    manifest_id: str
    file_path: str
    stream_index: int
    expected_full_sha256: Optional[str]
    observed_full_sha256: Optional[str]
    freshness: Optional[SourceFreshnessStat]
    frame_evidence: SourceFrameEvidence
    complete_scan: bool = True
    profile_policy: str = PROFILE_UNIFORM_TICKS_V1
    diagnostics: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        c_id = _validate_clean_str(self.clip_id, "clip_id", allow_blank=False)
        m_id = _validate_clean_str(self.manifest_id, "manifest_id", allow_blank=False)
        f_path = _validate_file_path(self.file_path, "file_path")

        object.__setattr__(self, "clip_id", c_id)
        object.__setattr__(self, "manifest_id", m_id)
        object.__setattr__(self, "file_path", f_path)

        if type(self.stream_index) is not int or isinstance(self.stream_index, bool) or self.stream_index < 0:
            raise ValueError(f"ERR_INVALID_STREAM_INDEX: stream_index phải là int >= 0, nhận: {self.stream_index!r}")

        if self.expected_full_sha256 is not None:
            if type(self.expected_full_sha256) is not str or isinstance(self.expected_full_sha256, bool) or not is_canonical_sha256_hex(self.expected_full_sha256):
                raise ValueError(f"ERR_INVALID_DIGEST: expected_full_sha256 không hợp lệ: {self.expected_full_sha256!r}")
            object.__setattr__(self, "expected_full_sha256", self.expected_full_sha256.lower())

        if self.observed_full_sha256 is not None:
            if type(self.observed_full_sha256) is not str or isinstance(self.observed_full_sha256, bool) or not is_canonical_sha256_hex(self.observed_full_sha256):
                raise ValueError(f"ERR_INVALID_DIGEST: observed_full_sha256 không hợp lệ: {self.observed_full_sha256!r}")
            object.__setattr__(self, "observed_full_sha256", self.observed_full_sha256.lower())

        # Defensive copy freshness
        if self.freshness is not None:
            if not isinstance(self.freshness, SourceFreshnessStat):
                raise ValueError(f"ERR_INVALID_FRESHNESS: freshness phải là SourceFreshnessStat, nhận: {type(self.freshness).__name__}")
            owned_freshness = SourceFreshnessStat(
                file_size_bytes=self.freshness.file_size_bytes,
                mtime_ns=self.freshness.mtime_ns,
                ctime_ns=self.freshness.ctime_ns,
                device_id=self.freshness.device_id,
                inode_number=self.freshness.inode_number,
            )
            object.__setattr__(self, "freshness", owned_freshness)

        # Defensive copy frame_evidence (bảo toàn 5 trường chuẩn tắc)
        if not isinstance(self.frame_evidence, SourceFrameEvidence):
            raise ValueError(
                f"ERR_INVALID_FRAME_EVIDENCE: frame_evidence phải là SourceFrameEvidence, nhận: {type(self.frame_evidence).__name__}"
            )
        owned_frame_ev = SourceFrameEvidence(
            manifest_id=self.frame_evidence.manifest_id,
            clip_id=self.frame_evidence.clip_id,
            source_fps=self.frame_evidence.source_fps,
            total_frames=self.frame_evidence.total_frames,
            evidence_status=self.frame_evidence.evidence_status,
        )
        object.__setattr__(self, "frame_evidence", owned_frame_ev)

        if type(self.complete_scan) is not bool:
            raise ValueError(f"ERR_INVALID_COMPLETE_SCAN: complete_scan phải là bool, nhận: {type(self.complete_scan).__name__}")

        pol = _validate_clean_str(self.profile_policy, "profile_policy", allow_blank=False)
        object.__setattr__(self, "profile_policy", pol)

        if not isinstance(self.diagnostics, (list, tuple)):
            raise ValueError(f"ERR_INVALID_DIAGNOSTICS: diagnostics phải là tuple[str], nhận: {type(self.diagnostics).__name__}")
        clean_diags = tuple(_validate_clean_str(d, "diagnostic", allow_blank=True) for d in self.diagnostics)
        object.__setattr__(self, "diagnostics", clean_diags)


@dataclass(frozen=True)
class SourceEvidenceBatchResult:
    """Kết quả hoàn tất hoặc hủy của một lô thu thập bằng chứng."""

    status: str  # "completed" | "cancelled" | "failed"
    evidence_by_clip: Mapping[str, SourceExecutionEvidenceEnvelope]
    diagnostics: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in ("completed", "cancelled", "failed"):
            raise ValueError(f"ERR_INVALID_BATCH_STATUS: status phải thuộc ('completed', 'cancelled', 'failed'), nhận: {self.status!r}")

        if not isinstance(self.evidence_by_clip, (dict, types.MappingProxyType)):
            raise ValueError(
                f"ERR_INVALID_EVIDENCE_MAP: evidence_by_clip phải là Mapping, nhận: {type(self.evidence_by_clip).__name__}"
            )

        owned_map: Dict[str, SourceExecutionEvidenceEnvelope] = {}
        for k, v in self.evidence_by_clip.items():
            k_clean = _validate_clean_str(k, "clip_id_key", allow_blank=False)
            if not isinstance(v, SourceExecutionEvidenceEnvelope):
                raise ValueError(
                    f"ERR_INVALID_ENVELOPE: evidence_by_clip[{k!r}] phải là SourceExecutionEvidenceEnvelope, nhận: {type(v).__name__}"
                )
            if v.clip_id != k_clean:
                raise ValueError(f"ERR_KEY_MISMATCH: khóa dict {k_clean!r} != envelope.clip_id {v.clip_id!r}")
            # Defensive deep clone envelope so result owns graph
            owned_envelope = SourceExecutionEvidenceEnvelope(
                clip_id=v.clip_id,
                manifest_id=v.manifest_id,
                file_path=v.file_path,
                stream_index=v.stream_index,
                expected_full_sha256=v.expected_full_sha256,
                observed_full_sha256=v.observed_full_sha256,
                freshness=v.freshness,
                frame_evidence=v.frame_evidence,
                complete_scan=v.complete_scan,
                profile_policy=v.profile_policy,
                diagnostics=v.diagnostics,
            )
            owned_map[k_clean] = owned_envelope

        object.__setattr__(self, "evidence_by_clip", types.MappingProxyType(owned_map))

        if not isinstance(self.diagnostics, (list, tuple)):
            raise ValueError(f"ERR_INVALID_DIAGNOSTICS: diagnostics phải là tuple[str], nhận: {type(self.diagnostics).__name__}")
        clean_diags = tuple(_validate_clean_str(d, "diagnostic", allow_blank=True) for d in self.diagnostics)
        object.__setattr__(self, "diagnostics", clean_diags)


@dataclass(frozen=True)
class _CachedEvidenceFact:
    """Dữ liệu bằng chứng đã hoàn tất xác nhận CFR được lưu trong bộ nhớ đệm RAM."""

    observed_full_sha256: str
    freshness: SourceFreshnessStat
    source_fps: RationalFPS
    total_frames: int
    evidence_status: str
    diagnostics: Tuple[str, ...]
    profile_policy: str


def _scan_decoded_frames_uniform_ticks(
    file_path: str,
    *,
    global_stream_index: int,
    time_base: Optional[Fraction],
    reported_nb_frames: Optional[int],
    deadline: float,
    max_frames: int,
    stdout_budget: int,
    stderr_budget: int,
    ffprobe_bin: str,
    cancelled_fn: Optional[Callable[[], bool]] = None,
) -> Tuple[Optional[int], Optional[RationalFPS], str, Tuple[str, ...], bool]:
    """Quét các khung hình giải mã (decoded frames) thực tế của stream video để kiểm định nhịp CFR.

    Trả về:
    (total_frames, inferred_source_fps, evidence_status, diagnostics, complete_scan)
    """
    diagnostics: List[str] = []

    # 1. Kiểm tra time_base
    if time_base is None or not isinstance(time_base, Fraction) or time_base <= 0:
        return (
            reported_nb_frames,
            None,
            "HEADER_REPORTED_ONLY",
            ("ERR_INVALID_TIME_BASE: time_base của luồng video không hợp lệ hoặc thiếu",),
            False,
        )

    # 2. Xây dựng lệnh ffprobe
    cmd = [
        ffprobe_bin,
        "-hide_banner",
        "-loglevel", "error",
        "-select_streams", str(global_stream_index),
        "-show_frames",
        "-show_entries", "frame=pts,pkt_pts,duration,pkt_duration",
        "-of", "json",
        file_path,
    ]

    proc: Optional[subprocess.Popen] = None
    stderr_reader: Optional[_BoundedPipeReader] = None

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except Exception as exc:
        return (
            reported_nb_frames,
            None,
            "HEADER_REPORTED_ONLY",
            (f"ERR_PROBE_EXECUTION_FAILED: không thể khởi chạy ffprobe: {exc}",),
            False,
        )

    # Đặt non-blocking cho stdout để không bao giờ bị nghẽn read(N)
    try:
        os.set_blocking(proc.stdout.fileno(), False)
    except Exception:
        pass

    stderr_reader = _BoundedPipeReader(proc.stderr, max_bytes=stderr_budget, name="ffprobe_stderr_reader")
    stderr_reader.start()

    # Bộ phân tích streaming frame JSON không giữ toàn bộ danh sách trong RAM
    # Trạng thái grammar hữu hạn: START -> ROOT_OPEN -> KEY('frames') -> COLON -> ARRAY_OPEN
    # -> FIRST_FRAME_OR_ARRAY_CLOSE -> IN_RECORD -> COMMA_OR_ARRAY_CLOSE -> NEXT_RECORD -> ROOT_CLOSE -> EOF
    _ST_START = 0
    _ST_EXPECT_KEY_QUOTE = 1
    _ST_READING_KEY = 2
    _ST_EXPECT_COLON = 3
    _ST_EXPECT_ARRAY_OPEN = 4
    _ST_EXPECT_FIRST_FRAME_OR_CLOSE = 5
    _ST_IN_RECORD = 6
    _ST_EXPECT_COMMA_OR_CLOSE = 7
    _ST_EXPECT_NEXT_RECORD = 8
    _ST_EXPECT_ROOT_CLOSE = 9
    _ST_EXPECT_EOF = 10

    parser_state = _ST_START
    depth = 0
    in_string = False
    escape = False

    frame_count = 0
    prev_pts: Optional[int] = None
    expected_delta: Optional[int] = None
    is_uniform = True
    last_frame_duration: Optional[int] = None

    root_key_chars: List[str] = []
    current_root_key: Optional[str] = None

    current_record: List[str] = []
    current_record_bytes = 0
    total_stdout_bytes = 0
    hit_budget_or_error = False

    def _parse_strict_int(val: Any) -> Optional[int]:
        if val is None or isinstance(val, bool):
            return None
        if isinstance(val, int):
            return val
        if isinstance(val, str):
            s = val.strip()
            if re.match(r"^-?[0-9]+$", s):
                return int(s)
        return None

    def _feed_text(text: str) -> None:
        nonlocal parser_state, depth, in_string, escape
        nonlocal root_key_chars, current_root_key, current_record, current_record_bytes
        nonlocal frame_count, prev_pts, expected_delta, is_uniform, last_frame_duration, hit_budget_or_error

        for ch in text:
            if hit_budget_or_error:
                break

            if parser_state == _ST_START:
                if ch.isspace():
                    continue
                if ch == "{":
                    parser_state = _ST_EXPECT_KEY_QUOTE
                    depth = 1
                else:
                    diagnostics.append(f"ERR_LEADING_GARBAGE: ký tự không hợp lệ trước root JSON: {ch!r}")
                    hit_budget_or_error = True
                    is_uniform = False
                    break

            elif parser_state == _ST_EXPECT_KEY_QUOTE:
                if ch.isspace():
                    continue
                if ch == '"':
                    parser_state = _ST_READING_KEY
                    root_key_chars = []
                elif ch == "}":
                    diagnostics.append("ERR_MISSING_FRAMES_KEY: root JSON rỗng, thiếu key 'frames'")
                    hit_budget_or_error = True
                    is_uniform = False
                    break
                else:
                    diagnostics.append(f"ERR_INVALID_ROOT_KEY: kỳ vọng key trong root JSON, gặp: {ch!r}")
                    hit_budget_or_error = True
                    is_uniform = False
                    break

            elif parser_state == _ST_READING_KEY:
                if ch == '"':
                    parser_state = _ST_EXPECT_COLON
                    current_root_key = "".join(root_key_chars)
                    if current_root_key != "frames":
                        diagnostics.append(f"ERR_WRONG_ROOT_KEY: root key là {current_root_key!r}, kỳ vọng 'frames'")
                        hit_budget_or_error = True
                        is_uniform = False
                        break
                elif ch == "\\":
                    root_key_chars.append(ch)
                else:
                    root_key_chars.append(ch)
                    if len(root_key_chars) > 256:
                        diagnostics.append("ERR_ROOT_KEY_OVERFLOW: root key vượt quá giới hạn 256 ký tự")
                        hit_budget_or_error = True
                        is_uniform = False
                        break

            elif parser_state == _ST_EXPECT_COLON:
                if ch.isspace():
                    continue
                if ch == ":":
                    parser_state = _ST_EXPECT_ARRAY_OPEN
                else:
                    diagnostics.append(f"ERR_MISSING_COLON: thiếu dấu ':' sau root key, gặp: {ch!r}")
                    hit_budget_or_error = True
                    is_uniform = False
                    break

            elif parser_state == _ST_EXPECT_ARRAY_OPEN:
                if ch.isspace():
                    continue
                if ch == "[":
                    parser_state = _ST_EXPECT_FIRST_FRAME_OR_CLOSE
                else:
                    diagnostics.append(f"ERR_MISSING_ARRAY_OPEN: kỳ vọng mảng '[' sau 'frames':, gặp: {ch!r}")
                    hit_budget_or_error = True
                    is_uniform = False
                    break

            elif parser_state == _ST_EXPECT_FIRST_FRAME_OR_CLOSE:
                if ch.isspace():
                    continue
                if ch == "]":
                    parser_state = _ST_EXPECT_ROOT_CLOSE
                elif ch == "{":
                    if frame_count >= max_frames:
                        diagnostics.append(f"ERR_FRAME_COUNT_OVERFLOW: vượt quá tối đa {max_frames} khung")
                        hit_budget_or_error = True
                        is_uniform = False
                        break
                    depth = 2
                    current_record = ["{"]
                    current_record_bytes = len("{".encode("utf-8"))
                    in_string = False
                    escape = False
                    parser_state = _ST_IN_RECORD
                else:
                    diagnostics.append(f"ERR_INVALID_ARRAY_ELEMENT: phần tử trong mảng frames không phải object, gặp: {ch!r}")
                    hit_budget_or_error = True
                    is_uniform = False
                    break

            elif parser_state == _ST_IN_RECORD:
                ch_bytes = len(ch.encode("utf-8"))
                current_record_bytes += ch_bytes
                if current_record_bytes > DEFAULT_RECORD_BUDGET_BYTES:
                    diagnostics.append("ERR_RECORD_OVERFLOW: frame JSON vượt quá hạn ngạch kích thước 16KiB")
                    hit_budget_or_error = True
                    is_uniform = False
                    break
                current_record.append(ch)

                if in_string:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_string = False
                    continue
                else:
                    if ch == '"':
                        in_string = True
                        continue
                    elif ch == "{":
                        depth += 1
                        continue
                    elif ch == "}":
                        depth -= 1
                        if depth == 1:
                            # Đóng một frame record
                            frame_str = "".join(current_record)
                            current_record = []
                            current_record_bytes = 0

                            try:
                                f_obj = json.loads(frame_str)
                            except Exception:
                                is_uniform = False
                                diagnostics.append("ERR_MALFORMED_FRAME_JSON: lỗi cú pháp frame JSON")
                                parser_state = _ST_EXPECT_COMMA_OR_CLOSE
                                continue

                            pts_raw = f_obj.get("pts")
                            if pts_raw is None:
                                pts_raw = f_obj.get("pkt_pts")
                            pts = _parse_strict_int(pts_raw)
                            if pts is None:
                                is_uniform = False
                                diagnostics.append(f"ERR_INVALID_PTS: PTS không phải integer hợp lệ: {pts_raw!r}")
                                parser_state = _ST_EXPECT_COMMA_OR_CLOSE
                                continue

                            dur_raw = f_obj.get("duration")
                            if dur_raw is None:
                                dur_raw = f_obj.get("pkt_duration")
                            dur = _parse_strict_int(dur_raw)
                            if dur_raw is not None and dur is None:
                                is_uniform = False
                                diagnostics.append(f"ERR_INVALID_DURATION: duration không phải integer hợp lệ: {dur_raw!r}")
                            last_frame_duration = dur

                            if frame_count == 0:
                                prev_pts = pts
                                frame_count = 1
                            elif frame_count == 1:
                                delta = pts - prev_pts  # type: ignore[operator]
                                if delta <= 0:
                                    is_uniform = False
                                    diagnostics.append(f"ERR_NON_POSITIVE_DELTA: delta {delta} <= 0 tại khung 1")
                                expected_delta = delta
                                prev_pts = pts
                                frame_count = 2
                            else:
                                delta = pts - prev_pts  # type: ignore[operator]
                                if delta != expected_delta:
                                    is_uniform = False
                                    diagnostics.append(
                                        f"ERR_NON_UNIFORM_DELTA: delta {delta} != expected {expected_delta} tại khung {frame_count}"
                                    )
                                prev_pts = pts
                                frame_count += 1

                            parser_state = _ST_EXPECT_COMMA_OR_CLOSE
                        continue

            elif parser_state == _ST_EXPECT_COMMA_OR_CLOSE:
                if ch.isspace():
                    continue
                if ch == ",":
                    parser_state = _ST_EXPECT_NEXT_RECORD
                elif ch == "]":
                    parser_state = _ST_EXPECT_ROOT_CLOSE
                else:
                    diagnostics.append(f"ERR_MISSING_COMMA: thiếu dấu phẩy giữa các frame records, gặp: {ch!r}")
                    hit_budget_or_error = True
                    is_uniform = False
                    break

            elif parser_state == _ST_EXPECT_NEXT_RECORD:
                if ch.isspace():
                    continue
                if ch == "{":
                    if frame_count >= max_frames:
                        diagnostics.append(f"ERR_FRAME_COUNT_OVERFLOW: vượt quá tối đa {max_frames} khung")
                        hit_budget_or_error = True
                        is_uniform = False
                        break
                    depth = 2
                    current_record = ["{"]
                    current_record_bytes = len("{".encode("utf-8"))
                    in_string = False
                    escape = False
                    parser_state = _ST_IN_RECORD
                elif ch == "]":
                    diagnostics.append("ERR_TRAILING_COMMA: dấu phẩy thừa trước khi đóng mảng frames")
                    hit_budget_or_error = True
                    is_uniform = False
                    break
                else:
                    diagnostics.append(f"ERR_INVALID_ARRAY_ELEMENT: phần tử trong mảng frames không phải object, gặp: {ch!r}")
                    hit_budget_or_error = True
                    is_uniform = False
                    break

            elif parser_state == _ST_EXPECT_ROOT_CLOSE:
                if ch.isspace():
                    continue
                if ch == "}":
                    parser_state = _ST_EXPECT_EOF
                    depth = 0
                else:
                    diagnostics.append(f"ERR_MISSING_ROOT_CLOSE: kỳ vọng đóng root JSON '}}', gặp: {ch!r}")
                    hit_budget_or_error = True
                    is_uniform = False
                    break

            elif parser_state == _ST_EXPECT_EOF:
                if ch.isspace():
                    continue
                else:
                    diagnostics.append(f"ERR_TRAILING_GARBAGE: phát hiện ký tự thừa sau JSON: {ch!r}")
                    hit_budget_or_error = True
                    is_uniform = False
                    break

    utf8_decoder = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        while True:
            # Checkpoint hủy và quá hạn
            if cancelled_fn is not None and cancelled_fn():
                raise InterruptedError("ERR_PROBE_CANCELLED: tác vụ bị hủy bởi caller")

            now = time.monotonic()
            if now > deadline:
                diagnostics.append("ERR_PROBE_TIMEOUT: vượt quá thời gian chờ quét khung hình")
                hit_budget_or_error = True
                break

            if stderr_reader.overflow:
                diagnostics.append("ERR_STDERR_OVERFLOW: dung lượng stderr vượt quá hạn ngạch")
                hit_budget_or_error = True
                break

            # Polling stdout không chặn
            rlist, _, _ = select.select([proc.stdout], [], [], 0.02)
            if rlist:
                try:
                    chunk = os.read(proc.stdout.fileno(), READ_CHUNK_BYTES)
                except (BlockingIOError, OSError):
                    chunk = b""
                if not chunk:
                    # Pipe đóng từ phía child
                    break

                total_stdout_bytes += len(chunk)
                if total_stdout_bytes > stdout_budget:
                    diagnostics.append("ERR_STDOUT_OVERFLOW: tổng dung lượng stdout vượt quá hạn ngạch")
                    hit_budget_or_error = True
                    break

                try:
                    text_chunk = utf8_decoder.decode(chunk, final=False)
                except UnicodeDecodeError as u_err:
                    diagnostics.append(f"ERR_INVALID_UTF8: dữ liệu stdout không phải UTF-8 hợp lệ: {u_err}")
                    hit_budget_or_error = True
                    is_uniform = False
                    break

                if text_chunk:
                    _feed_text(text_chunk)
                if hit_budget_or_error:
                    break
            else:
                # Không có dữ liệu trong 20ms, kiểm tra xem tiến trình đã kết thúc chưa
                if proc.poll() is not None:
                    # Đọc phần còn lại nếu có
                    try:
                        while True:
                            rem = os.read(proc.stdout.fileno(), READ_CHUNK_BYTES)
                            if not rem:
                                break
                            total_stdout_bytes += len(rem)
                            if total_stdout_bytes > stdout_budget:
                                diagnostics.append("ERR_STDOUT_OVERFLOW: tổng dung lượng stdout vượt quá hạn ngạch")
                                hit_budget_or_error = True
                                break
                            try:
                                text_rem = utf8_decoder.decode(rem, final=False)
                            except UnicodeDecodeError as u_err:
                                diagnostics.append(f"ERR_INVALID_UTF8: dữ liệu stdout không phải UTF-8 hợp lệ: {u_err}")
                                hit_budget_or_error = True
                                is_uniform = False
                                break
                            if text_rem:
                                _feed_text(text_rem)
                            if hit_budget_or_error:
                                break
                    except (BlockingIOError, OSError):
                        pass
                    break

        if not hit_budget_or_error:
            try:
                final_text = utf8_decoder.decode(b"", final=True)
                if final_text:
                    _feed_text(final_text)
            except UnicodeDecodeError as u_err:
                diagnostics.append(f"ERR_INVALID_UTF8: dữ liệu stdout không phải UTF-8 hợp lệ: {u_err}")
                hit_budget_or_error = True
                is_uniform = False

    finally:
        _cleanup_child_and_readers(proc, stderr_reader=stderr_reader, grace_sec=0.05)

    # 1. Kiểm tra stderr reader xem có lỗi, tràn hoặc dữ liệu lỗi trong stderr không
    if stderr_reader is not None:
        if stderr_reader.exception is not None:
            diagnostics.append(f"ERR_STDERR_READER_FAILED: lỗi đọc stderr: {stderr_reader.exception}")
            return (reported_nb_frames, None, "HEADER_REPORTED_ONLY", tuple(diagnostics), False)
        if stderr_reader.overflow:
            diagnostics.append("ERR_STDERR_OVERFLOW: dung lượng stderr vượt quá hạn ngạch")
            return (reported_nb_frames, None, "HEADER_REPORTED_ONLY", tuple(diagnostics), False)

    stderr_bytes = stderr_reader.get_data() if stderr_reader else b""
    if stderr_bytes.strip():
        # Dưới -loglevel error, bất kỳ đầu ra stderr nào đều là cảnh báo hoặc lỗi nghiêm trọng
        err_excerpt = stderr_bytes.decode("utf-8", errors="replace").strip()[:200]
        diagnostics.append(f"ERR_DECODER_ERROR_IN_STDERR: phát hiện lỗi trong stderr của ffprobe: {err_excerpt}")
        return (reported_nb_frames, None, "HEADER_REPORTED_ONLY", tuple(diagnostics), False)

    # 2. Kiểm tra tiến trình có exit code 0 không
    if proc.poll() != 0:
        err_msg = stderr_bytes.decode("utf-8", errors="replace").strip()
        diagnostics.append(f"ERR_PROBE_PROCESS_FAILED: ffprobe kết thúc với mã {proc.poll()}: {err_msg[:200]}")
        return (
            reported_nb_frames,
            None,
            "HEADER_REPORTED_ONLY",
            tuple(diagnostics),
            False,
        )

    # 3. Kiểm tra JSON hoàn tất cấu trúc sạch sẽ (clean JSON EOF)
    if parser_state != _ST_EXPECT_EOF:
        diagnostics.append(f"ERR_TRUNCATED_JSON: đầu ra JSON không hoàn tất cấu trúc sạch sẽ (state: {parser_state})")
        return (
            frame_count if frame_count > 0 else reported_nb_frames,
            None,
            "HEADER_REPORTED_ONLY",
            tuple(diagnostics),
            False,
        )

    if hit_budget_or_error:
        return (
            reported_nb_frames,
            None,
            "HEADER_REPORTED_ONLY",
            tuple(diagnostics),
            False,
        )

    # 4. Đánh giá điều kiện CFR
    if frame_count < 2:
        diagnostics.append(f"ERR_INSUFFICIENT_FRAMES: tệp có {frame_count} khung hình giải mã, không đủ tối thiểu 2 khung để xác nhận CFR")
        return (
            frame_count if frame_count > 0 else reported_nb_frames,
            None,
            "HEADER_REPORTED_ONLY",
            tuple(diagnostics),
            False,
        )

    if not is_uniform or expected_delta is None or expected_delta <= 0:
        return (
            frame_count,
            None,
            "HEADER_REPORTED_ONLY",
            tuple(diagnostics),
            False,
        )

    # 5. Kiểm tra last_frame_duration khớp expected_delta
    if last_frame_duration is None or last_frame_duration != expected_delta:
        diagnostics.append(
            f"ERR_LAST_FRAME_DURATION_MISMATCH: thời lượng khung cuối ({last_frame_duration}) không khớp delta ({expected_delta})"
        )
        return (
            frame_count,
            None,
            "HEADER_REPORTED_ONLY",
            tuple(diagnostics),
            False,
        )

    # 6. Kiểm tra reported_nb_frames nếu có báo cáo từ header
    if reported_nb_frames is not None and reported_nb_frames != frame_count:
        diagnostics.append(
            f"ERR_REPORTED_FRAME_COUNT_MISMATCH: header báo cáo {reported_nb_frames} khung nhưng giải mã được {frame_count}"
        )
        return (
            frame_count,
            None,
            "HEADER_REPORTED_ONLY",
            tuple(diagnostics),
            False,
        )

    # 7. Suy diễn source FPS chuẩn tắc từ Fraction(1, expected_delta * time_base)
    try:
        step_sec = Fraction(expected_delta, 1) * time_base
        if step_sec <= 0:
            diagnostics.append(f"ERR_INVALID_FRAME_STEP: step_sec {step_sec} <= 0")
            return (frame_count, None, "HEADER_REPORTED_ONLY", tuple(diagnostics), False)
        inferred_fps_frac = Fraction(1, step_sec)
        inferred_fps = RationalFPS.from_value(inferred_fps_frac)
    except Exception as exc:
        diagnostics.append(f"ERR_FPS_INFERENCE_FAILED: không thể suy diễn FPS từ delta: {exc}")
        return (frame_count, None, "HEADER_REPORTED_ONLY", tuple(diagnostics), False)

    return (
        frame_count,
        inferred_fps,
        "VERIFIED_CFR",
        tuple(diagnostics),
        True,
    )


class SourceEvidenceService:
    """Dịch vụ thu thập và kiểm định bằng chứng khung hình nguồn vận hành."""

    def __init__(self, *, ffprobe_path: Optional[str] = None) -> None:
        """Constructor có zero I/O - không gọi shutil.which hay thăm dò đĩa khi khởi tạo."""
        if ffprobe_path is not None and (type(ffprobe_path) is not str or not ffprobe_path.strip()):
            raise ValueError(f"ERR_INVALID_FFPROBE_PATH: ffprobe_path phải là str hợp lệ, nhận: {ffprobe_path!r}")
        self._ffprobe_path_arg = ffprobe_path
        self._cache: Dict[Tuple[str, int, int, int, int, int, int, str], _CachedEvidenceFact] = {}
        self._default_stream_map: Dict[Tuple[str, int, int, int, int, int], int] = {}

    def clear_cache(self) -> None:
        """Xóa toàn bộ bộ nhớ đệm RAM và bản đồ stream mặc định của thể hiện dịch vụ."""
        self._cache.clear()
        self._default_stream_map.clear()

    def _get_ffprobe_bin(self) -> str:
        """Tra cứu đường dẫn thực thi ffprobe lazily khi có cache miss cần quét thật."""
        if self._ffprobe_path_arg is not None:
            return self._ffprobe_path_arg
        resolved = shutil.which("ffprobe")
        if not resolved:
            raise FileNotFoundError("ERR_FFPROBE_NOT_FOUND: không tìm thấy ffprobe trên hệ thống PATH")
        return resolved

    @property
    def _ffprobe_bin(self) -> str:
        """Thuộc tính truy cập lazy cho ffprobe binary path (bảo toàn tương thích ngược cho tests)."""
        return self._get_ffprobe_bin()

    def acquire_evidence_for_batch(
        self,
        request: SourceEvidenceBatchRequest,
        *,
        cancelled_fn: Optional[Callable[[], bool]] = None,
        progress_fn: Optional[Callable[[int, str], None]] = None,
    ) -> SourceEvidenceBatchResult:
        """Thu thập và xác thực bằng chứng thi công cho một lô clip nguồn.

        Quy trình:
        1. Pre-validation toàn bộ tham số request trước khi thực hiện bất kỳ thao tác I/O nào và tạo owned request.
        2. Thăm dò ban đầu và tra cứu RAM cache hoàn chỉnh (cache hit không gọi header/probe).
        3. Với cache miss: Thu thập header video -> Tính Full SHA-256 (1MiB chunks) -> Quét decoded frames.
        4. Post-stat kiểm tra TOCTOU -> Chỉ ghi cache nếu complete CFR và không bị hủy.
        """
        # 1. Thẩm định nghiêm ngặt toàn bộ tham số request trước I/O và tạo owned request
        if not isinstance(request, SourceEvidenceBatchRequest):
            raise ValueError(
                f"ERR_INVALID_REQUEST_TYPE: request phải là SourceEvidenceBatchRequest, nhận {type(request).__name__}"
            )

        # Kiểm tra sự hiện diện của các thuộc tính instance-owned trên batch request
        # Để phát hiện trường hợp caller xóa instance attribute qua object.__delattr__
        batch_required = (
            "items",
            "timeout_per_file_sec",
            "max_decoded_frames",
            "stdout_budget_bytes",
            "stderr_budget_bytes",
        )
        req_vars = getattr(request, "__dict__", None)
        for f_name in batch_required:
            if req_vars is not None:
                if f_name not in req_vars:
                    raise ValueError(f"ERR_MISSING_REQUEST_FIELD: request thiếu thuộc tính instance {f_name!r}")
            else:
                try:
                    object.__getattribute__(request, f_name)
                except AttributeError:
                    raise ValueError(f"ERR_MISSING_REQUEST_FIELD: request thiếu thuộc tính {f_name!r}")

        req_items = request.items
        if not isinstance(req_items, (list, tuple)):
            raise ValueError(f"ERR_INVALID_ITEMS: items phải là list hoặc tuple, nhận {type(req_items).__name__}")

        # Kiểm tra giới hạn số lượng TRƯỚC bất kỳ vòng lặp iteration/clone nào
        if len(req_items) > MAX_ITEMS_PER_BATCH:
            raise ValueError(
                f"ERR_COUNT_OVERFLOW: số lượng items ({len(req_items)}) vượt quá giới hạn tối đa {MAX_ITEMS_PER_BATCH}"
            )

        item_required = (
            "clip_id",
            "manifest_id",
            "file_path",
            "stream_index",
            "expected_full_sha256",
        )

        owned_items: List[SourceEvidenceItemRequest] = []
        for it in req_items:
            if not isinstance(it, SourceEvidenceItemRequest):
                raise ValueError("ERR_INVALID_ITEM_TYPE: items chứa phần tử không phải SourceEvidenceItemRequest")

            it_vars = getattr(it, "__dict__", None)
            for f_name in item_required:
                if it_vars is not None:
                    if f_name not in it_vars:
                        raise ValueError(f"ERR_MISSING_ITEM_FIELD: item thiếu thuộc tính instance {f_name!r}")
                else:
                    try:
                        object.__getattribute__(it, f_name)
                    except AttributeError:
                        raise ValueError(f"ERR_MISSING_ITEM_FIELD: item thiếu thuộc tính {f_name!r}")

            owned_items.append(
                SourceEvidenceItemRequest(
                    clip_id=it.clip_id,
                    manifest_id=it.manifest_id,
                    file_path=it.file_path,
                    stream_index=it.stream_index,
                    expected_full_sha256=it.expected_full_sha256,
                )
            )

        owned_request = SourceEvidenceBatchRequest(
            items=tuple(owned_items),
            timeout_per_file_sec=request.timeout_per_file_sec,
            max_decoded_frames=request.max_decoded_frames,
            stdout_budget_bytes=request.stdout_budget_bytes,
            stderr_budget_bytes=request.stderr_budget_bytes,
        )

        evidence_by_clip: Dict[str, SourceExecutionEvidenceEnvelope] = {}
        batch_diagnostics: List[str] = []

        total_items = len(owned_request.items)
        if total_items == 0:
            return SourceEvidenceBatchResult(
                status="completed",
                evidence_by_clip={},
                diagnostics=("Yêu cầu rỗng, 0 clip được xử lý.",),
            )

        for idx, item in enumerate(owned_request.items):
            # Checkpoint hủy ban đầu
            if cancelled_fn is not None and cancelled_fn():
                return SourceEvidenceBatchResult(
                    status="cancelled",
                    evidence_by_clip=evidence_by_clip,
                    diagnostics=("Tác vụ bị hủy bởi caller.",),
                )

            item_start_time = time.monotonic()
            item_deadline = item_start_time + owned_request.timeout_per_file_sec

            if progress_fn is not None:
                pct = int((idx / total_items) * 100)
                progress_fn(pct, f"Đang xác minh clip nguồn: {item.clip_id} ({idx + 1}/{total_items})")

            # Pha 1: Thăm dò stat ban đầu của tệp nguồn
            try:
                freshness_before = _capture_source_freshness(item.file_path)
            except Exception as exc:
                msg = f"ERR_SOURCE_FILE_INACCESSIBLE: không thể truy cập tệp nguồn {item.file_path}: {exc}"
                batch_diagnostics.append(msg)
                unverified_ev = SourceFrameEvidence(
                    manifest_id=item.manifest_id,
                    clip_id=item.clip_id,
                    source_fps=None,
                    total_frames=None,
                    evidence_status="HEADER_REPORTED_ONLY",
                )
                env = SourceExecutionEvidenceEnvelope(
                    clip_id=item.clip_id,
                    manifest_id=item.manifest_id,
                    file_path=item.file_path,
                    stream_index=item.stream_index or 0,
                    expected_full_sha256=item.expected_full_sha256,
                    observed_full_sha256=None,
                    freshness=None,
                    frame_evidence=unverified_ev,
                    complete_scan=False,
                    diagnostics=(msg,),
                )
                evidence_by_clip[item.clip_id] = env
                continue

            # Pha 2: Tra cứu RAM cache TRƯỚC KHI gọi bất kỳ header probe nào
            stream_to_check: Optional[int] = item.stream_index
            if stream_to_check is None:
                five_stat_key = (
                    item.file_path,
                    freshness_before.device_id,
                    freshness_before.inode_number,
                    freshness_before.file_size_bytes,
                    freshness_before.mtime_ns,
                    freshness_before.ctime_ns,
                )
                stream_to_check = self._default_stream_map.get(five_stat_key)

            if stream_to_check is not None:
                cache_key = (
                    item.file_path,
                    freshness_before.device_id,
                    freshness_before.inode_number,
                    freshness_before.file_size_bytes,
                    freshness_before.mtime_ns,
                    freshness_before.ctime_ns,
                    stream_to_check,
                    PROFILE_UNIFORM_TICKS_V1,
                )
                if cache_key in self._cache:
                    cached_fact = self._cache[cache_key]
                    if cancelled_fn is not None and cancelled_fn():
                        return SourceEvidenceBatchResult(
                            status="cancelled",
                            evidence_by_clip=evidence_by_clip,
                            diagnostics=("Tác vụ bị hủy trong khi truy xuất cache.",),
                        )
                    rebound_frame_ev = SourceFrameEvidence(
                        manifest_id=item.manifest_id,
                        clip_id=item.clip_id,
                        source_fps=cached_fact.source_fps,
                        total_frames=cached_fact.total_frames,
                        evidence_status=cached_fact.evidence_status,
                    )
                    rebound_envelope = SourceExecutionEvidenceEnvelope(
                        clip_id=item.clip_id,
                        manifest_id=item.manifest_id,
                        file_path=item.file_path,
                        stream_index=stream_to_check,
                        expected_full_sha256=item.expected_full_sha256,
                        observed_full_sha256=cached_fact.observed_full_sha256,
                        freshness=cached_fact.freshness,
                        frame_evidence=rebound_frame_ev,
                        complete_scan=True,
                        profile_policy=cached_fact.profile_policy,
                        diagnostics=cached_fact.diagnostics,
                    )
                    evidence_by_clip[item.clip_id] = rebound_envelope
                    continue

            # Checkpoint hủy trước header
            if cancelled_fn is not None and cancelled_fn():
                return SourceEvidenceBatchResult(
                    status="cancelled",
                    evidence_by_clip=evidence_by_clip,
                    diagnostics=("Tác vụ bị hủy trước khi đọc tiêu đề video.",),
                )

            # Pha 3: Thu thập siêu dữ liệu tiêu đề (header) qua acquire_local_video_header
            ffprobe_bin = self._get_ffprobe_bin()
            header_res = None
            try:
                header_res = acquire_local_video_header(
                    item.file_path,
                    ffprobe_path=ffprobe_bin,
                    selected_stream_index=item.stream_index,
                    timeout_sec=min(owned_request.timeout_per_file_sec, 5.0),
                    cancelled_fn=cancelled_fn,
                )
            except Exception as exc:
                msg = f"ERR_HEADER_ACQUISITION_FAILED: không thể đọc tiêu đề video {item.file_path}: {exc}"
                batch_diagnostics.append(msg)
                unverified_ev = SourceFrameEvidence(
                    manifest_id=item.manifest_id,
                    clip_id=item.clip_id,
                    source_fps=None,
                    total_frames=None,
                    evidence_status="HEADER_REPORTED_ONLY",
                )
                env = SourceExecutionEvidenceEnvelope(
                    clip_id=item.clip_id,
                    manifest_id=item.manifest_id,
                    file_path=item.file_path,
                    stream_index=item.stream_index or 0,
                    expected_full_sha256=item.expected_full_sha256,
                    observed_full_sha256=None,
                    freshness=freshness_before,
                    frame_evidence=unverified_ev,
                    complete_scan=False,
                    diagnostics=(msg,),
                )
                evidence_by_clip[item.clip_id] = env
                continue

            # Kiểm tra TOCTOU giữa stat ban đầu và header stat
            if header_res.freshness != freshness_before:
                msg = f"ERR_SOURCE_MUTATED: tệp nguồn {item.file_path} đã biến động trong khi đọc header"
                batch_diagnostics.append(msg)
                unverified_ev = SourceFrameEvidence(
                    manifest_id=item.manifest_id,
                    clip_id=item.clip_id,
                    source_fps=None,
                    total_frames=None,
                    evidence_status="HEADER_REPORTED_ONLY",
                )
                env = SourceExecutionEvidenceEnvelope(
                    clip_id=item.clip_id,
                    manifest_id=item.manifest_id,
                    file_path=item.file_path,
                    stream_index=header_res.video_header.stream_index,
                    expected_full_sha256=item.expected_full_sha256,
                    observed_full_sha256=None,
                    freshness=header_res.freshness,
                    frame_evidence=unverified_ev,
                    complete_scan=False,
                    diagnostics=(msg,),
                )
                evidence_by_clip[item.clip_id] = env
                continue

            global_stream_index = header_res.video_header.stream_index
            time_base = header_res.video_header.time_base
            reported_nb_frames = header_res.video_header.reported_nb_frames

            # Checkpoint hủy trước khi băm SHA-256
            if cancelled_fn is not None and cancelled_fn():
                return SourceEvidenceBatchResult(
                    status="cancelled",
                    evidence_by_clip=evidence_by_clip,
                    diagnostics=("Tác vụ bị hủy trước khi băm SHA256.",),
                )

            # Pha 4: Tính mã băm toàn bộ tệp (Full SHA-256) qua helper G05
            def _hash_cancel_wrapper() -> bool:
                if cancelled_fn is not None and cancelled_fn():
                    return True
                if time.monotonic() > item_deadline:
                    return True
                return False

            observed_sha, prov = compute_full_file_sha256(item.file_path, cancelled_fn=_hash_cancel_wrapper)
            if cancelled_fn is not None and cancelled_fn():
                return SourceEvidenceBatchResult(
                    status="cancelled",
                    evidence_by_clip=evidence_by_clip,
                    diagnostics=("Tác vụ bị hủy trong quá trình tính toán mã băm SHA256.",),
                )

            if observed_sha is None or prov is None:
                msg = f"ERR_COMPUTE_HASH_FAILED: không thể băm toàn bộ tệp hoặc tệp biến động: {item.file_path}"
                batch_diagnostics.append(msg)
                unverified_ev = SourceFrameEvidence(
                    manifest_id=item.manifest_id,
                    clip_id=item.clip_id,
                    source_fps=None,
                    total_frames=None,
                    evidence_status="HEADER_REPORTED_ONLY",
                )
                env = SourceExecutionEvidenceEnvelope(
                    clip_id=item.clip_id,
                    manifest_id=item.manifest_id,
                    file_path=item.file_path,
                    stream_index=global_stream_index,
                    expected_full_sha256=item.expected_full_sha256,
                    observed_full_sha256=None,
                    freshness=freshness_before,
                    frame_evidence=unverified_ev,
                    complete_scan=False,
                    diagnostics=(msg,),
                )
                evidence_by_clip[item.clip_id] = env
                continue

            # Đối chiếu provenance của hash với 5-stat
            if (
                prov.get("file_size") != freshness_before.file_size_bytes
                or prov.get("mtime_ns") != freshness_before.mtime_ns
                or prov.get("ctime_ns") != freshness_before.ctime_ns
                or prov.get("dev") != freshness_before.device_id
                or prov.get("ino") != freshness_before.inode_number
            ):
                msg = f"ERR_SOURCE_MUTATED: tệp nguồn {item.file_path} biến động trong quá trình băm SHA256"
                batch_diagnostics.append(msg)
                unverified_ev = SourceFrameEvidence(
                    manifest_id=item.manifest_id,
                    clip_id=item.clip_id,
                    source_fps=None,
                    total_frames=None,
                    evidence_status="HEADER_REPORTED_ONLY",
                )
                env = SourceExecutionEvidenceEnvelope(
                    clip_id=item.clip_id,
                    manifest_id=item.manifest_id,
                    file_path=item.file_path,
                    stream_index=global_stream_index,
                    expected_full_sha256=item.expected_full_sha256,
                    observed_full_sha256=observed_sha,
                    freshness=freshness_before,
                    frame_evidence=unverified_ev,
                    complete_scan=False,
                    diagnostics=(msg,),
                )
                evidence_by_clip[item.clip_id] = env
                continue

            # Checkpoint hủy trước scan
            if cancelled_fn is not None and cancelled_fn():
                return SourceEvidenceBatchResult(
                    status="cancelled",
                    evidence_by_clip=evidence_by_clip,
                    diagnostics=("Tác vụ bị hủy trước khi quét khung hình.",),
                )

            # Pha 5: Quét khung hình giải mã (decoded frame scan)
            tot_frames, inf_fps, ev_status, scan_diags, complete_scan = _scan_decoded_frames_uniform_ticks(
                item.file_path,
                global_stream_index=global_stream_index,
                time_base=time_base,
                reported_nb_frames=reported_nb_frames,
                deadline=item_deadline,
                max_frames=owned_request.max_decoded_frames,
                stdout_budget=owned_request.stdout_budget_bytes,
                stderr_budget=owned_request.stderr_budget_bytes,
                ffprobe_bin=ffprobe_bin,
                cancelled_fn=cancelled_fn,
            )

            # Pha 6: Post-check stat kiểm tra TOCTOU sau khi quét frames
            try:
                freshness_after = _capture_source_freshness(item.file_path, is_after=True)
            except Exception as exc:
                msg = f"ERR_POST_STAT_FAILED: không thể đọc lại stat tệp {item.file_path} sau quét: {exc}"
                batch_diagnostics.append(msg)
                freshness_after = freshness_before
                ev_status = "HEADER_REPORTED_ONLY"
                complete_scan = False
                scan_diags = scan_diags + (msg,)

            # Checkpoint quá hạn sau post-stat: Nếu vượt quá deadline, không chứng nhận CFR và không ghi cache
            if time.monotonic() > item_deadline:
                msg = f"ERR_PROBE_TIMEOUT: vượt quá thời hạn tại pha post-stat cho clip {item.clip_id}"
                batch_diagnostics.append(msg)
                ev_status = "HEADER_REPORTED_ONLY"
                complete_scan = False
                scan_diags = scan_diags + (msg,)

            # Checkpoint hủy NGAY SAU post-stat: Nếu bị hủy, trả về status="cancelled" và KHÔNG thêm vào cache!
            if cancelled_fn is not None and cancelled_fn():
                return SourceEvidenceBatchResult(
                    status="cancelled",
                    evidence_by_clip=evidence_by_clip,
                    diagnostics=("Tác vụ bị hủy ngay sau khi kiểm tra post-stat.",),
                )

            if freshness_after != freshness_before:
                msg = f"ERR_SOURCE_MUTATED: tệp nguồn {item.file_path} đã biến động trong quá trình quét khung hình"
                batch_diagnostics.append(msg)
                ev_status = "HEADER_REPORTED_ONLY"
                complete_scan = False
                scan_diags = scan_diags + (msg,)

            # Đóng gói SourceFrameEvidence chuẩn tắc 5 trường
            frame_evidence = SourceFrameEvidence(
                manifest_id=item.manifest_id,
                clip_id=item.clip_id,
                source_fps=inf_fps if ev_status == "VERIFIED_CFR" else None,
                total_frames=tot_frames if ev_status == "VERIFIED_CFR" else None,
                evidence_status=ev_status,
            )

            envelope = SourceExecutionEvidenceEnvelope(
                clip_id=item.clip_id,
                manifest_id=item.manifest_id,
                file_path=item.file_path,
                stream_index=global_stream_index,
                expected_full_sha256=item.expected_full_sha256,
                observed_full_sha256=observed_sha,
                freshness=freshness_after,
                frame_evidence=frame_evidence,
                complete_scan=complete_scan,
                profile_policy=PROFILE_UNIFORM_TICKS_V1,
                diagnostics=scan_diags,
            )
            evidence_by_clip[item.clip_id] = envelope

            # Chỉ lưu RAM cache khi đạt COMPLETE proof (VERIFIED_CFR và complete_scan=True) VÀ không bị hủy
            if complete_scan and ev_status == "VERIFIED_CFR" and observed_sha is not None and inf_fps is not None and tot_frames is not None:
                five_stat_key = (
                    item.file_path,
                    freshness_before.device_id,
                    freshness_before.inode_number,
                    freshness_before.file_size_bytes,
                    freshness_before.mtime_ns,
                    freshness_before.ctime_ns,
                )
                if item.stream_index is None:
                    self._default_stream_map[five_stat_key] = global_stream_index
                cache_key = (
                    item.file_path,
                    freshness_before.device_id,
                    freshness_before.inode_number,
                    freshness_before.file_size_bytes,
                    freshness_before.mtime_ns,
                    freshness_before.ctime_ns,
                    global_stream_index,
                    PROFILE_UNIFORM_TICKS_V1,
                )
                self._cache[cache_key] = _CachedEvidenceFact(
                    observed_full_sha256=observed_sha,
                    freshness=freshness_after,
                    source_fps=inf_fps,
                    total_frames=tot_frames,
                    evidence_status=ev_status,
                    diagnostics=scan_diags,
                    profile_policy=PROFILE_UNIFORM_TICKS_V1,
                )

        if progress_fn is not None:
            progress_fn(100, "Hoàn tất kiểm định bằng chứng các clip nguồn.")

        # Checkpoint hủy cuối cùng trước khi đóng gói
        if cancelled_fn is not None and cancelled_fn():
            return SourceEvidenceBatchResult(
                status="cancelled",
                evidence_by_clip=evidence_by_clip,
                diagnostics=("Tác vụ bị hủy sau khi thu thập bằng chứng.",),
            )

        status = "completed"
        return SourceEvidenceBatchResult(
            status=status,
            evidence_by_clip=evidence_by_clip,
            diagnostics=tuple(batch_diagnostics),
        )
