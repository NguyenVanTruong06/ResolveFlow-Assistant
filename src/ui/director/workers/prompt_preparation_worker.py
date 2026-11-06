"""
src/ui/director/workers/prompt_preparation_worker.py
=============================================================================
Worker QThread chuẩn bị Prompt V1 bất đồng bộ cho ReviewPage & DirectorWindow:
- Toàn bộ I/O (hash, cache read, manifest resolution, prompt formatting) chạy
  trên luồng nền, giải phóng 100% GUI thread.
- Xác thực Schema v3 nghiêm ngặt (verify_audio=True) và áp trần UTF-8 32768 bytes.
- Pre-stat & post-stat kiểm tra nguồn: file biến đổi/mất -> fail-closed ngay lập tức.
- Manifest scoping: không dùng chung manifest toàn cục gây ô nhiễm clip cũ.
- Tách biệt hoàn toàn tín hiệu kết thúc logic (finished_signal) và vòng đời vật lý (QThread.finished).
- Cooperative cancellation: kiểm tra cờ dừng giữa các clip và trước khi phát tín hiệu.
=============================================================================
"""

import os
import stat
import copy
import time
import math
import re
import json
import hashlib
import logging
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Dict, Any, Optional, Tuple, List, Union

from PySide6.QtCore import QThread, Signal

from src.core.contracts.edit_plan_contracts import (
    CreativeContext,
    normalize_creative_context,
    compute_creative_context_signature,
)

from src.core.batch_draft_contracts import (
    BatchSourceRange,
    ValidatedSegmentCut,
    ValidatedBatchDraftEntry,
    decode_strict_batch_draft_json,
    validate_batch_draft_payload,
    ERR_MISSING_PREPARED_GEOMETRY,
    ERR_MALFORMED_JSON,
    ERR_OVERSIZE_PAYLOAD,
    ERR_DUPLICATE_JSON_KEYS,
)
from src.core.project_overview_contracts import (
    ProjectOverviewContextInput,
    ProjectOverviewRenderResult,
    render_project_overview_prompt,
    ERR_OVERVIEW_NO_DRAFTS,
    ERR_OVERVIEW_BUDGET_OVERFLOW,
    ERR_OVERVIEW_INVALID_CONTEXT,
    FORMATTER_POLICY as OVERVIEW_FORMATTER_POLICY,
)

logger = logging.getLogger(__name__)


def _normalize_endpoint(url: str) -> str:
    """Chuẩn hóa URL endpoint (bỏ trailing slash, chuẩn hóa host loopback)."""
    if not url:
        return ""
    u = url.strip().rstrip("/")
    try:
        parsed = urllib.parse.urlparse(u)
        netloc = parsed.netloc.lower()
        if netloc.startswith("localhost"):
            netloc = "127.0.0.1" + netloc[9:]
        return urllib.parse.urlunparse((parsed.scheme.lower() or "http", netloc, parsed.path, "", "", ""))
    except Exception:
        return u


def _get_field(obj: Any, key: str, default: Any = None) -> Any:
    """Trích xuất giá trị trường an toàn từ dict hoặc DTO không gây exception."""
    if isinstance(obj, dict):
        val = obj.get(key)
        return default if val is None else val
    if hasattr(obj, key):
        val = getattr(obj, key)
        return default if val is None else val
    return default


@dataclass(frozen=True)
class BaselineSourceEntry:
    """Bản ghi bằng chứng tệp nguồn cơ sở (Frozen Value Object)."""
    path: str
    st_size: int
    st_mtime_ns: int
    st_ctime_ns: int
    st_ino: int
    st_dev: int

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "st_size": self.st_size,
            "st_mtime_ns": self.st_mtime_ns,
            "st_ctime_ns": self.st_ctime_ns,
            "st_ino": self.st_ino,
            "st_dev": self.st_dev,
        }


@dataclass(frozen=True)
class UnaccountedClipEntry:
    """Bản ghi clip chưa thể phân lô kèm lý do minh bạch (Frozen Value Object)."""
    clip_id: str
    reason: str
    message: str

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> Dict[str, Any]:
        return {"clip_id": self.clip_id, "reason": self.reason, "message": self.message}


@dataclass(frozen=True)
class AdapterIssueEntry:
    """Bản ghi cảnh báo adapter cho clip (Frozen Value Object)."""
    clip_id: str
    issue_type: str
    message: str

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> Dict[str, Any]:
        return {"clip_id": self.clip_id, "issue_type": self.issue_type, "message": self.message}


@dataclass(frozen=True)
class PreparedBatchItem:
    """Mục lô kịch bản đã kết xuất (Frozen Value Object)."""
    batch_id: str
    batch_index: int
    distinct_clip_count: int
    total_duration_sec: float
    rendered_prompt: str
    rendered_bytes: int
    rendered_sha256: str
    content_signature: str
    clip_ids: Tuple[str, ...]
    items_count: int
    item_count: int = 0
    prompt_bytes: int = 0
    source_ranges: Tuple[BatchSourceRange, ...] = ()

    def __post_init__(self):
        if not self.item_count:
            object.__setattr__(self, "item_count", self.items_count)
        if not self.prompt_bytes:
            object.__setattr__(self, "prompt_bytes", self.rendered_bytes)
        if not isinstance(self.source_ranges, tuple):
            object.__setattr__(self, "source_ranges", tuple(self.source_ranges or ()))

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "batch_index": self.batch_index,
            "distinct_clip_count": self.distinct_clip_count,
            "total_duration_sec": self.total_duration_sec,
            "rendered_prompt": self.rendered_prompt,
            "rendered_bytes": self.rendered_bytes,
            "rendered_sha256": self.rendered_sha256,
            "content_signature": self.content_signature,
            "clip_ids": self.clip_ids,
            "items_count": self.items_count,
            "item_count": self.item_count,
            "prompt_bytes": self.prompt_bytes,
            "source_ranges": tuple(
                r.to_dict() if hasattr(r, "to_dict") else r for r in self.source_ranges
            ),
        }


@dataclass(frozen=True)
class PreparedBatchSnapshot:
    """Ảnh chụp kịch bản phân lô bất biến thuộc quyền sở hữu (Owned Immutable Snapshot)."""
    manifest_id: str
    project_name: str
    snapshot_id: str
    formatter_policy: str
    total_batches: int
    planned_items_count: int
    distinct_planned_clips_count: int
    planned_clip_ids: Tuple[str, ...]
    planned_clip_ids_count: int
    unaccounted_clip_ids: Tuple[str, ...]
    unaccounted_clips: Tuple[UnaccountedClipEntry, ...]
    adapter_issues: Tuple[AdapterIssueEntry, ...]
    max_prompt_bytes: int
    batches: Tuple[PreparedBatchItem, ...]
    baseline_sources: Tuple[BaselineSourceEntry, ...]
    evidence_context_signature: Any
    ui_context_signature: Any = None
    creative_context_signature: Optional[str] = None

    def __getitem__(self, key: str) -> Any:
        if key == "context_signature":
            return self.evidence_context_signature
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key == "context_signature":
            return self.evidence_context_signature
        return getattr(self, key, default)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "manifest_id": self.manifest_id,
            "project_name": self.project_name,
            "snapshot_id": self.snapshot_id,
            "formatter_policy": self.formatter_policy,
            "total_batches": self.total_batches,
            "planned_items_count": self.planned_items_count,
            "distinct_planned_clips_count": self.distinct_planned_clips_count,
            "planned_clip_ids": self.planned_clip_ids,
            "planned_clip_ids_count": self.planned_clip_ids_count,
            "unaccounted_clip_ids": self.unaccounted_clip_ids,
            "unaccounted_clips": tuple(c.to_dict() for c in self.unaccounted_clips),
            "adapter_issues": tuple(i.to_dict() for i in self.adapter_issues),
            "max_prompt_bytes": self.max_prompt_bytes,
            "batches": tuple(b.to_dict() for b in self.batches),
            "baseline_sources": tuple(s.to_dict() for s in self.baseline_sources),
            "evidence_context_signature": self.evidence_context_signature,
            "context_signature": self.evidence_context_signature,
            "ui_context_signature": self.ui_context_signature,
        }
        if self.creative_context_signature is not None:
            d["creative_context_signature"] = self.creative_context_signature
        return d


def compute_snapshot_id(
    manifest_id: str,
    project_name: str,
    baseline_sources: Tuple[BaselineSourceEntry, ...],
    batches: Tuple[PreparedBatchItem, ...],
    evidence_context_signature: Any,
    max_prompt_bytes: int,
    formatter_policy: str = "source_only_v1",
    *,
    creative_context_signature: Optional[str] = None,
) -> str:
    """Tạo SHA256 context fingerprint chứng minh tính nhất quán dữ liệu nội bộ của snapshot."""
    hasher = hashlib.sha256()

    # Kiểm tra xem có geometry (source_ranges) hay không
    has_geometry = any(getattr(it, "source_ranges", None) for it in batches)

    if has_geometry:
        # Canonical structured JSON for header to prevent delimiter collision
        header_repr = json.dumps(
            {
                "domain": "geometry_snapshot_v2",
                "manifest_id": str(manifest_id),
                "project_name": str(project_name),
                "formatter_policy": str(formatter_policy),
                "max_prompt_bytes": int(max_prompt_bytes),
                "evidence_context_signature": str(evidence_context_signature),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        hasher.update(f"header_v2:{len(header_repr)}:{header_repr}\n".encode("utf-8"))
    else:
        hasher.update(f"manifest:{manifest_id}|project:{project_name}|policy:{formatter_policy}|cap:{max_prompt_bytes}\n".encode("utf-8"))
        hasher.update(f"evidence_sig:{evidence_context_signature}\n".encode("utf-8"))

    for b in baseline_sources:
        hasher.update(f"src:{b.path}:{b.st_size}:{b.st_mtime_ns}:{b.st_ctime_ns}:{b.st_ino}:{b.st_dev}\n".encode("utf-8"))

    for it in batches:
        if has_geometry:
            batch_repr = json.dumps(
                {
                    "batch_id": str(it.batch_id),
                    "batch_index": int(it.batch_index),
                    "rendered_bytes": int(it.rendered_bytes),
                    "rendered_sha256": str(it.rendered_sha256),
                    "clip_ids": [str(c) for c in it.clip_ids],
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            hasher.update(f"batch_v2:{len(batch_repr)}:{batch_repr}\n".encode("utf-8"))
            for r in getattr(it, "source_ranges", ()):
                w_ids = list(getattr(r, "window_ids", ()) or ())
                range_repr = json.dumps(
                    {
                        "clip_id": str(r.clip_id),
                        "source_in_sec": float(r.source_in_sec),
                        "source_out_sec": float(r.source_out_sec),
                        "partition_index": int(r.partition_index),
                        "total_partitions": int(r.total_partitions),
                        "window_ids": [str(w) for w in w_ids],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                hasher.update(f"range_v2:{len(range_repr)}:{range_repr}\n".encode("utf-8"))
        else:
            hasher.update(f"batch:{it.batch_id}:{it.batch_index}:{it.rendered_bytes}:{it.rendered_sha256}:{','.join(it.clip_ids)}\n".encode("utf-8"))

    if creative_context_signature is not None:
        if isinstance(creative_context_signature, bool) or not isinstance(creative_context_signature, str):
            raise ValueError(f"ERR_INVALID_CREATIVE_SIGNATURE: creative_context_signature phải là str, nhận: {type(creative_context_signature).__name__}")
        if len(creative_context_signature) != 64 or not re.fullmatch(r"[0-9a-f]{64}", creative_context_signature):
            raise ValueError(f"ERR_INVALID_CREATIVE_SIGNATURE: creative_context_signature phải là chuỗi 64-hex lowercase, nhận: {creative_context_signature!r}")
        hasher.update(f"creative_context_sig_v1:{creative_context_signature}\n".encode("utf-8"))

    return hasher.hexdigest()


def validate_and_normalize_snapshot(raw: Any) -> Optional[PreparedBatchSnapshot]:
    """Pure validation & normalization SSOT cho PreparedBatchSnapshot.

    Chấp nhận PreparedBatchSnapshot hoặc dict từ Qt signal.
    Không có fastpath/bypass cho DTO hay dict; mọi thuộc tính đều được thẩm định nghiêm ngặt.
    Trả về PreparedBatchSnapshot (frozen DTO) đã thẩm định và deep-freeze,
    hoặc None nếu không hợp lệ / thiếu bằng chứng / tampered / sai kiểu dữ liệu.
    """
    if raw is None:
        return None
    if not isinstance(raw, (dict, PreparedBatchSnapshot)):
        return None

    try:
        # 1. Manifest ID & Project Name (Nonblank strings, no bool, no raw object conversion)
        raw_mid = _get_field(raw, "manifest_id")
        if not isinstance(raw_mid, str) or isinstance(raw_mid, bool) or not raw_mid.strip():
            return None
        manifest_id = raw_mid.strip()

        raw_proj = _get_field(raw, "project_name")
        if not isinstance(raw_proj, str) or isinstance(raw_proj, bool) or not raw_proj.strip():
            return None
        project_name = raw_proj.strip()

        # 2. Formatter Policy & Max Prompt Bytes
        raw_policy = _get_field(raw, "formatter_policy", "source_only_v1")
        if not isinstance(raw_policy, str) or isinstance(raw_policy, bool) or raw_policy.strip() != "source_only_v1":
            return None
        formatter_policy = "source_only_v1"

        max_prompt_bytes = _get_field(raw, "max_prompt_bytes", 32768)
        if type(max_prompt_bytes) is not int or not (1 <= max_prompt_bytes <= 32768):
            return None

        # 3. Snapshot ID (Bắt buộc proof field, nonblank string)
        raw_snap_id = _get_field(raw, "snapshot_id")
        if not isinstance(raw_snap_id, str) or isinstance(raw_snap_id, bool) or not raw_snap_id.strip():
            return None
        snapshot_id = raw_snap_id.strip().lower()

        # 4. Thẩm định baseline_sources: BẮT BUỘC nonempty, unique ordered paths, strict int metadata
        raw_baselines = _get_field(raw, "baseline_sources")
        if not raw_baselines or not isinstance(raw_baselines, (list, tuple)):
            return None

        normalized_baselines = []
        seen_paths = set()
        for b in raw_baselines:
            if not isinstance(b, (dict, BaselineSourceEntry)):
                return None
            p = _get_field(b, "path")
            if not isinstance(p, str) or isinstance(p, bool) or not p.strip():
                return None
            norm_p = os.path.normpath(os.path.abspath(p))
            if norm_p in seen_paths:
                return None
            seen_paths.add(norm_p)

            st_size = _get_field(b, "st_size")
            st_mtime_ns = _get_field(b, "st_mtime_ns")
            st_ctime_ns = _get_field(b, "st_ctime_ns", 0)
            st_ino = _get_field(b, "st_ino")
            st_dev = _get_field(b, "st_dev")

            if type(st_size) is not int or st_size < 0:
                return None
            if type(st_mtime_ns) is not int:
                return None
            if type(st_ctime_ns) is not int:
                return None
            if type(st_ino) is not int:
                return None
            if type(st_dev) is not int:
                return None

            normalized_baselines.append(
                BaselineSourceEntry(
                    path=norm_p,
                    st_size=st_size,
                    st_mtime_ns=st_mtime_ns,
                    st_ctime_ns=st_ctime_ns,
                    st_ino=st_ino,
                    st_dev=st_dev,
                )
            )

        if not normalized_baselines:
            return None

        # 5. Thẩm định batches
        raw_batches = _get_field(raw, "batches")
        if raw_batches is None or not isinstance(raw_batches, (list, tuple)):
            return None

        normalized_batches = []
        seen_batch_ids = set()
        for idx, b in enumerate(raw_batches):
            if not isinstance(b, (dict, PreparedBatchItem)):
                return None

            b_id = _get_field(b, "batch_id")
            if not isinstance(b_id, str) or isinstance(b_id, bool) or not b_id.strip():
                return None
            b_id = b_id.strip()
            if b_id in seen_batch_ids:
                return None
            seen_batch_ids.add(b_id)

            b_idx = _get_field(b, "batch_index")
            if type(b_idx) is not int or b_idx != idx:
                return None

            r_prompt = _get_field(b, "rendered_prompt")
            if not isinstance(r_prompt, str) or isinstance(r_prompt, bool) or not r_prompt.strip():
                return None

            actual_bytes = len(r_prompt.encode("utf-8"))
            if actual_bytes > max_prompt_bytes or actual_bytes <= 0:
                return None

            r_bytes = _get_field(b, "rendered_bytes")
            if type(r_bytes) is not int or r_bytes != actual_bytes:
                return None

            p_bytes = _get_field(b, "prompt_bytes")
            if p_bytes is not None and (type(p_bytes) is not int or p_bytes != actual_bytes):
                return None

            # Đối chiếu rendered_sha256
            raw_sha = _get_field(b, "rendered_sha256")
            if not isinstance(raw_sha, str) or isinstance(raw_sha, bool) or not raw_sha.strip():
                return None
            b_sha = raw_sha.strip().lower()
            actual_sha = hashlib.sha256(r_prompt.encode("utf-8")).hexdigest().lower()
            if b_sha != actual_sha:
                # Content tampered hoặc sha256 không khớp!
                return None

            sig = _get_field(b, "content_signature")
            if not isinstance(sig, str) or isinstance(sig, bool):
                return None

            raw_clip_ids = _get_field(b, "clip_ids") or ()
            if not isinstance(raw_clip_ids, (list, tuple)):
                return None
            clip_ids = []
            for cid in raw_clip_ids:
                if not isinstance(cid, str) or isinstance(cid, bool) or not cid.strip():
                    return None
                clip_ids.append(cid.strip())
            clip_ids_tuple = tuple(clip_ids)

            distinct_clip_count = _get_field(b, "distinct_clip_count")
            if type(distinct_clip_count) is not int or distinct_clip_count < 0:
                return None
            if distinct_clip_count != len(set(clip_ids_tuple)):
                return None

            tot_dur = _get_field(b, "total_duration_sec")
            if isinstance(tot_dur, bool) or not isinstance(tot_dur, (int, float)):
                return None
            tot_dur = float(tot_dur)
            if math.isnan(tot_dur) or math.isinf(tot_dur) or tot_dur < 0.0:
                return None

            items_cnt = _get_field(b, "items_count")
            if items_cnt is None:
                items_cnt = _get_field(b, "item_count")
            if type(items_cnt) is not int or items_cnt < 0:
                return None

            item_cnt = _get_field(b, "item_count")
            if item_cnt is not None and (type(item_cnt) is not int or item_cnt != items_cnt):
                return None

            # Thẩm định source_ranges (Typed Partition Geometry)
            raw_ranges = _get_field(b, "source_ranges") or ()
            if not isinstance(raw_ranges, (list, tuple)):
                return None
            norm_ranges = []
            for r in raw_ranges:
                if isinstance(r, (BatchSourceRange, dict)):
                    rcid = _get_field(r, "clip_id")
                    rin = _get_field(r, "source_in_sec")
                    rout = _get_field(r, "source_out_sec")
                    rpidx = _get_field(r, "partition_index")
                    rtot = _get_field(r, "total_partitions")
                    rwids = _get_field(r, "window_ids") or ()
                    if not isinstance(rwids, (list, tuple)):
                        return None
                    clean_wids = []
                    for w in rwids:
                        if isinstance(w, bool) or not isinstance(w, str) or not w.strip():
                            return None
                        clean_wids.append(w.strip())
                    try:
                        norm_ranges.append(
                            BatchSourceRange(
                                clip_id=rcid,
                                source_in_sec=rin,
                                source_out_sec=rout,
                                partition_index=rpidx,
                                total_partitions=rtot,
                                window_ids=tuple(clean_wids),
                            )
                        )
                    except Exception:
                        return None
                else:
                    return None

            normalized_batches.append(
                PreparedBatchItem(
                    batch_id=b_id,
                    batch_index=b_idx,
                    distinct_clip_count=distinct_clip_count,
                    total_duration_sec=tot_dur,
                    rendered_prompt=r_prompt,
                    rendered_bytes=actual_bytes,
                    rendered_sha256=actual_sha,
                    content_signature=sig,
                    clip_ids=clip_ids_tuple,
                    items_count=items_cnt,
                    item_count=items_cnt,
                    prompt_bytes=actual_bytes,
                    source_ranges=tuple(norm_ranges),
                )
            )

        # 6. Signatures tách biệt & không chấp nhận rỗng
        ev_sig = _get_field(raw, "evidence_context_signature")
        if ev_sig is None:
            ev_sig = _get_field(raw, "context_signature")
        if ev_sig is None:
            return None
        ui_sig = _get_field(raw, "ui_context_signature")
        raw_creative_sig = _get_field(raw, "creative_context_signature")
        norm_creative_sig: Optional[str] = None
        if raw_creative_sig is not None:
            if isinstance(raw_creative_sig, bool) or not isinstance(raw_creative_sig, str):
                return None
            if len(raw_creative_sig) != 64 or not re.fullmatch(r"[0-9a-f]{64}", raw_creative_sig):
                return None
            norm_creative_sig = raw_creative_sig

        # 7. Deep-freeze nested audit issues & unaccounted clips
        raw_unacc = _get_field(raw, "unaccounted_clips") or ()
        if not isinstance(raw_unacc, (list, tuple)):
            return None
        norm_unacc = []
        for u in raw_unacc:
            if not isinstance(u, (dict, UnaccountedClipEntry)):
                return None
            cid = _get_field(u, "clip_id")
            reason = _get_field(u, "reason")
            msg = _get_field(u, "message", "")
            if not isinstance(cid, str) or isinstance(cid, bool):
                return None
            if not isinstance(reason, str) or isinstance(reason, bool):
                return None
            if not isinstance(msg, str) or isinstance(msg, bool):
                return None
            norm_unacc.append(
                UnaccountedClipEntry(clip_id=cid, reason=reason, message=msg)
            )

        raw_issues = _get_field(raw, "adapter_issues") or ()
        if not isinstance(raw_issues, (list, tuple)):
            return None
        norm_issues = []
        for iss in raw_issues:
            if not isinstance(iss, (dict, AdapterIssueEntry)):
                return None
            cid = _get_field(iss, "clip_id")
            itype = _get_field(iss, "issue_type")
            msg = _get_field(iss, "message", "")
            if not isinstance(cid, str) or isinstance(cid, bool):
                return None
            if not isinstance(itype, str) or isinstance(itype, bool):
                return None
            if not isinstance(msg, str) or isinstance(msg, bool):
                return None
            norm_issues.append(
                AdapterIssueEntry(clip_id=cid, issue_type=itype, message=msg)
            )

        raw_p_cids = _get_field(raw, "planned_clip_ids") or ()
        if not isinstance(raw_p_cids, (list, tuple)):
            return None
        planned_cids = tuple(str(x) for x in raw_p_cids if isinstance(x, str) and not isinstance(x, bool))

        raw_u_cids = _get_field(raw, "unaccounted_clip_ids") or ()
        if not isinstance(raw_u_cids, (list, tuple)):
            return None
        unacc_cids = tuple(str(x) for x in raw_u_cids if isinstance(x, str) and not isinstance(x, bool))

        total_batches = len(normalized_batches)
        raw_tot_batches = _get_field(raw, "total_batches")
        if raw_tot_batches is not None and (type(raw_tot_batches) is not int or raw_tot_batches != total_batches):
            return None

        planned_items_cnt = sum(b.items_count for b in normalized_batches)
        raw_planned_items = _get_field(raw, "planned_items_count")
        if raw_planned_items is not None and (type(raw_planned_items) is not int or raw_planned_items != planned_items_cnt):
            return None

        distinct_planned_cnt = _get_field(raw, "distinct_planned_clips_count")
        if distinct_planned_cnt is not None and (type(distinct_planned_cnt) is not int or distinct_planned_cnt < 0):
            return None
        if distinct_planned_cnt is None:
            distinct_planned_cnt = len({cid for b in normalized_batches for cid in b.clip_ids})

        # 8. Xác thực tính nhất quán nội bộ của snapshot_id
        computed_id = compute_snapshot_id(
            manifest_id=manifest_id,
            project_name=project_name,
            baseline_sources=tuple(normalized_baselines),
            batches=tuple(normalized_batches),
            evidence_context_signature=ev_sig,
            max_prompt_bytes=max_prompt_bytes,
            formatter_policy=formatter_policy,
            creative_context_signature=norm_creative_sig,
        ).lower()

        if snapshot_id != computed_id:
            # Snapshot ID không khớp fingerprint nội bộ
            return None

        return PreparedBatchSnapshot(
            manifest_id=manifest_id,
            project_name=project_name,
            snapshot_id=snapshot_id,
            formatter_policy=formatter_policy,
            total_batches=total_batches,
            planned_items_count=planned_items_cnt,
            distinct_planned_clips_count=distinct_planned_cnt,
            planned_clip_ids=planned_cids,
            planned_clip_ids_count=_get_field(raw, "planned_clip_ids_count", len(planned_cids)),
            unaccounted_clip_ids=unacc_cids,
            unaccounted_clips=tuple(norm_unacc),
            adapter_issues=tuple(norm_issues),
            max_prompt_bytes=max_prompt_bytes,
            batches=tuple(normalized_batches),
            baseline_sources=tuple(normalized_baselines),
            evidence_context_signature=ev_sig,
            ui_context_signature=ui_sig,
            creative_context_signature=norm_creative_sig,
        )
    except Exception as exc:
        logger.warning("Lỗi validation snapshot: %s", exc)
        return None


# =============================================================================
# PROJECT OVERVIEW ARTIFACT & LEDGER SIGNATURE (G06-06A4c2a-I / R1)
# =============================================================================

def _freeze_context_signature(sig: Any, is_root: bool = True, seen: Optional[set] = None) -> Any:
    """Deep-freeze signature thành cấu trúc nguyên thủy bất biến (tuple, str, int, float, bool).
    
    Quy tắc phân biệt root proof required và nested data (G06-06A4c2b-I / Section A):
    1. Root-required gate (chỉ áp dụng khi is_root=True):
       - Root None, rỗng, số, bool, bytes -> ném ValueError fail-closed.
       - Root chỉ chấp nhận:
         + Chuỗi str không rỗng hợp lệ UTF-8, không lone surrogate (backward compatibility).
         + Non-empty tuple, list, hoặc dict có cấu trúc hợp lệ.
       - Bất kỳ kiểu đối tượng phức tạp / UI / QObject / Resolve nào -> ném ValueError fail-closed.
    2. Chu kỳ tham chiếu (cycle protection):
       - Phát hiện chu kỳ trên containers và ném ValueError fail-closed (không RecursionError).
    3. Nested data rules (khi is_root=False):
       - None: trả về None (cho phép trường nested là None).
       - bool: trả về bool nguyên thủy.
       - int: strict int (không phải bool).
       - float: finite float (không phải bool, không overflow/inf/nan).
       - str: chuỗi hợp lệ UTF-8, không lone surrogate. CHẤP NHẬN chuỗi rỗng '' (ví dụ vision_model='', manifest_id='').
       - bytes: chuỗi bytes hợp lệ.
       - tuple / list: tuple chứa các phần tử đã freeze đệ quy. CHẤP NHẬN sequence rỗng () (ví dụ norm_paths=()).
       - dict: tuple các cặp (frozen_k, frozen_v) đã sắp xếp tăng dần. Khóa dict phải cùng kiểu nguyên thủy, không dùng repr-sort ép kiểu lẫn lộn.
    """
    if is_root:
        if sig is None:
            raise ValueError("Context signature root không được là None")
        if isinstance(sig, bool):
            raise ValueError("Context signature root không được là kiểu bool")
        if type(sig) in (int, float):
            raise ValueError(f"Context signature root không được là kiểu số: {type(sig).__name__}")
        if type(sig) is bytes:
            raise ValueError("Context signature root không được là kiểu bytes")
        if isinstance(sig, str):
            if not sig.strip():
                raise ValueError("Context signature root chuỗi không được rỗng hoặc chỉ có khoảng trắng")
            if any(0xD800 <= ord(c) <= 0xDFFF for c in sig):
                raise ValueError("Context signature chuỗi chứa lone surrogate")
            try:
                sig.encode("utf-8")
            except Exception as exc:
                raise ValueError(f"Context signature chuỗi không hợp lệ UTF-8: {exc}")
            return sig
        if isinstance(sig, (list, tuple)):
            if len(sig) == 0:
                raise ValueError("Context signature root sequence không được rỗng")
        elif isinstance(sig, dict):
            if len(sig) == 0:
                raise ValueError("Context signature root mapping không được rỗng")
        else:
            raise ValueError(f"Kiểu dữ liệu không được hỗ trợ ở root context signature: {type(sig).__name__}")

    # Cycle detection cho containers
    if isinstance(sig, (list, tuple, dict)):
        seen = set() if seen is None else seen
        obj_id = id(sig)
        if obj_id in seen:
            raise ValueError("Phát hiện tham chiếu vòng (cycle) trong context signature")
        seen = seen | {obj_id}

    # Nested element handling
    if sig is None:
        return None
    if isinstance(sig, bool):
        return sig
    if type(sig) is int:
        return sig
    if type(sig) is float:
        if not math.isfinite(sig):
            raise ValueError(f"Context signature float không hữu hạn: {sig!r}")
        return sig
    if type(sig) is str:
        if any(0xD800 <= ord(c) <= 0xDFFF for c in sig):
            raise ValueError("Context signature chuỗi chứa lone surrogate")
        try:
            sig.encode("utf-8")
        except Exception as exc:
            raise ValueError(f"Context signature chuỗi không hợp lệ UTF-8: {exc}")
        return sig
    if type(sig) is bytes:
        return sig
    if isinstance(sig, (list, tuple)):
        return tuple(_freeze_context_signature(x, is_root=False, seen=seen) for x in sig)
    if isinstance(sig, dict):
        items = []
        for k, v in sig.items():
            if not isinstance(k, (str, int, float, bool)):
                raise ValueError(f"Khóa của dict trong context signature phải là kiểu nguyên thủy: {type(k).__name__}")
            frozen_k = _freeze_context_signature(k, is_root=False, seen=seen)
            frozen_v = _freeze_context_signature(v, is_root=False, seen=seen)
            items.append((frozen_k, frozen_v))
        key_types = {type(k) for k, _ in items}
        if len(key_types) > 1:
            raise ValueError(f"Khóa của dict trong context signature không được lẫn lộn nhiều kiểu: {key_types}")
        try:
            items.sort(key=lambda pair: pair[0])
        except TypeError as exc:
            raise ValueError(f"Khóa của dict trong context signature không thể so sánh: {exc}")
        return tuple(items)

    raise ValueError(f"Kiểu dữ liệu không được hỗ trợ trong context signature: {type(sig).__name__}")


def compute_project_overview_ledger_signature(
    snapshot_id: str,
    entries: Any,
) -> str:
    """Tính chữ ký canonical SHA-256 bất biến cho tập bản thảo thuộc một snapshot.

    Quy tắc an toàn nghiêm ngặt (G06-06A4c2a-R1):
    - snapshot_id bắt buộc là chuỗi văn bản không rỗng hợp lệ UTF-8, không phải bool.
    - entries là tuple hoặc list chứa các ValidatedBatchDraftEntry (hoặc rỗng nếu không có draft).
    - Thẩm định 100% field types TRƯỚC mọi thao tác hash/membership/sort/to_dict/float.
    - Cấm duplicate batch_id, cấm entry có snapshot_id khác với snapshot_id đã cấp.
    - Bỏ trường audit-only 'received_at' để không làm đổi chữ ký khi chỉ cập nhật timestamp.
    - Bao quát toàn bộ dữ liệu semantic: raw/draft digest, binding_mode, source_ranges, segments, summary, counters.
    - Bọc toàn bộ try/except chuyển TypeError, OverflowError, AttributeError thành ValueError fail-closed.
    - Sắp xếp deterministically theo batch_id tăng dần.
    - Dùng canonical JSON (sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).
    """
    if isinstance(snapshot_id, bool) or not isinstance(snapshot_id, str) or not snapshot_id.strip():
        raise ValueError(f"snapshot_id phải là chuỗi không rỗng hợp lệ, nhận: {snapshot_id!r}")
    try:
        snapshot_id.encode("utf-8")
    except Exception as exc:
        raise ValueError(f"snapshot_id không thể mã hóa UTF-8: {exc}")

    if isinstance(entries, bool) or not isinstance(entries, (tuple, list)):
        raise ValueError(f"entries phải là tuple hoặc list, nhận: {entries!r}")

    seen_batch_ids = set()
    canonical_entries = []
    VALID_BINDING_MODES = ("echo_matched", "user_associated_current_snapshot")

    for idx, e in enumerate(entries):
        if not isinstance(e, ValidatedBatchDraftEntry):
            raise ValueError(f"Mỗi phần tử trong entries phải là ValidatedBatchDraftEntry, nhận: {type(e).__name__} tại chỉ số {idx}")

        try:
            # 1. Thẩm định kiểu batch_id trước bất kỳ thao tác hash / membership
            b_id = e.batch_id
            if isinstance(b_id, bool) or not isinstance(b_id, str) or not b_id.strip():
                raise ValueError(f"batch_id trong entry phải là chuỗi không rỗng, nhận: {b_id!r}")
            try:
                b_id.encode("utf-8")
            except Exception as exc:
                raise ValueError(f"batch_id không hợp lệ UTF-8: {exc}")

            if b_id in seen_batch_ids:
                raise ValueError(f"Duplicate batch_id trong entries: {b_id!r}")
            seen_batch_ids.add(b_id)

            # 2. Thẩm định snapshot_id và manifest_id
            s_id = e.snapshot_id
            if isinstance(s_id, bool) or not isinstance(s_id, str) or not s_id.strip():
                raise ValueError(f"snapshot_id trong entry phải là chuỗi không rỗng, nhận: {s_id!r}")
            if s_id != snapshot_id:
                raise ValueError(f"Foreign snapshot_id trong entry {b_id!r}: {s_id!r} != {snapshot_id!r}")

            m_id = e.manifest_id
            if isinstance(m_id, bool) or not isinstance(m_id, str) or not m_id.strip():
                raise ValueError(f"manifest_id trong entry phải là chuỗi không rỗng, nhận: {m_id!r}")

            # 3. Thẩm định binding_mode
            b_mode = e.binding_mode
            if b_mode not in VALID_BINDING_MODES:
                raise ValueError(f"binding_mode không hợp lệ trong entry {b_id!r}: {b_mode!r}, phải thuộc {VALID_BINDING_MODES}")

            # 4. Thẩm định raw_payload_sha256 và draft_digest (phải đúng 64-hex)
            for sha_name, sha_val in (("raw_payload_sha256", e.raw_payload_sha256), ("draft_digest", e.draft_digest)):
                if (
                    isinstance(sha_val, bool)
                    or not isinstance(sha_val, str)
                    or len(sha_val) != 64
                    or not all(c in "0123456789abcdefABCDEF" for c in sha_val)
                ):
                    raise ValueError(f"{sha_name} trong entry {b_id!r} phải là chuỗi 64-hex, nhận: {sha_val!r}")

            # 5. Thẩm định source_ranges
            s_ranges = e.source_ranges
            if isinstance(s_ranges, bool) or not isinstance(s_ranges, (tuple, list)):
                raise ValueError(f"source_ranges phải là tuple hoặc list trong entry {b_id!r}, nhận: {s_ranges!r}")
            for sr in s_ranges:
                if not isinstance(sr, BatchSourceRange):
                    raise ValueError(f"Phần tử trong source_ranges phải là BatchSourceRange trong entry {b_id!r}, nhận: {type(sr).__name__}")

            # 6. Thẩm định segments trước khi to_dict duyệt qua
            segs = e.segments
            if isinstance(segs, bool) or not isinstance(segs, (tuple, list)):
                raise ValueError(f"segments phải là tuple hoặc list trong entry {b_id!r}, nhận: {segs!r}")
            for seg in segs:
                if not isinstance(seg, ValidatedSegmentCut):
                    raise ValueError(f"Phần tử trong segments phải là ValidatedSegmentCut trong entry {b_id!r}, nhận: {type(seg).__name__}")

            # 7. Thẩm định counters và duration (chống bool, chống tràn số, chống non-finite)
            cuts_cnt = e.total_cuts_count
            if isinstance(cuts_cnt, bool) or type(cuts_cnt) is not int or cuts_cnt < 0:
                raise ValueError(f"total_cuts_count phải là int >= 0 và không phải bool trong entry {b_id!r}, nhận: {cuts_cnt!r}")

            dur = e.total_selected_duration_sec
            if isinstance(dur, bool) or not isinstance(dur, (int, float)):
                raise ValueError(f"total_selected_duration_sec phải là số thực hữu hạn trong entry {b_id!r}, nhận: {dur!r}")
            try:
                dur_f = float(dur)
            except (OverflowError, ValueError) as oe:
                raise ValueError(f"total_selected_duration_sec tràn số trong entry {b_id!r}: {oe}")
            if not math.isfinite(dur_f) or dur_f < 0.0:
                raise ValueError(f"total_selected_duration_sec không hữu hạn hoặc âm trong entry {b_id!r}: {dur_f!r}")

            # 8. Thẩm định batch_strategy_summary
            summary = e.batch_strategy_summary
            if not isinstance(summary, str):
                raise ValueError(f"batch_strategy_summary phải là str trong entry {b_id!r}, nhận: {type(summary).__name__}")
            try:
                summary.encode("utf-8")
            except Exception as exc:
                raise ValueError(f"batch_strategy_summary không hợp lệ UTF-8 trong entry {b_id!r}: {exc}")

            d = e.to_dict()
            # Loại bỏ trường audit-only 'received_at'
            d.pop("received_at", None)
            canonical_entries.append(d)

        except (TypeError, OverflowError, AttributeError) as exc:
            raise ValueError(f"Lỗi cấu trúc hoặc kiểu dữ liệu trong entry tại chỉ số {idx}: {exc}")

    # Sắp xếp theo batch_id tăng dần
    canonical_entries.sort(key=lambda item: item["batch_id"])

    payload_dict = {
        "version": "project_overview_ledger_v1",
        "snapshot_id": snapshot_id,
        "entries": canonical_entries,
    }

    try:
        payload_bytes = json.dumps(
            payload_dict,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except Exception as exc:
        raise ValueError(f"Không thể serialize canonical JSON cho ledger: {exc}")

    return hashlib.sha256(payload_bytes).hexdigest()


@dataclass(frozen=True)
class PreparedProjectOverviewArtifact:
    """Artifact bất biến chứa kết quả chuẩn bị kịch bản tổng quan dự án do worker sở hữu."""
    request_id: str
    generation: int
    intent: str  # 'copy_project_overview'
    snapshot_id: str
    manifest_id: str
    project_name: str
    ledger_signature: str
    formatter_policy: str
    max_prompt_bytes: int
    context_signature: Any
    overview: ProjectOverviewRenderResult
    creative_context_signature: Optional[str] = None

    def __post_init__(self):
        if self.creative_context_signature is not None:
            sig = self.creative_context_signature
            if (
                isinstance(sig, bool)
                or not isinstance(sig, str)
                or len(sig) != 64
                or not all(c in "0123456789abcdef" for c in sig)
            ):
                raise ValueError(f"creative_context_signature phải là chuỗi 64-hex viết thường hoặc None, nhận: {sig!r}")

    @property
    def prompt_text(self) -> str:
        return self.overview.rendered_prompt if self.overview else ""

    @property
    def rendered_prompt(self) -> str:
        return self.overview.rendered_prompt if self.overview else ""


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


def validate_project_overview_result_against_artifact(
    result: Dict[str, Any],
    artifact: PreparedProjectOverviewArtifact,
) -> bool:
    """Xác thực kết quả logic từ terminal finished signal đối chiếu với artifact bất biến do app sở hữu.

    Bảo đảm:
    - Cấm dùng checksum tự khai từ result làm expected proof.
    - Đối soát 100% các trường envelope và metadata danh tính.
    - Tính lại độ dài byte UTF-8 và SHA256 thực tế của rendered_prompt trong result và so với artifact.
    - Ngăn chặn triệt để hành vi tráo prompt cùng độ dài có sửa lại SHA256 tự khai.
    """
    if not isinstance(result, dict) or not isinstance(artifact, PreparedProjectOverviewArtifact):
        return False

    if not _has_initialized_field(artifact, "creative_context_signature"):
        return False

    # 1. Envelope validations (strict types, no bool for int)
    res_req_id = result.get("request_id")
    if not isinstance(res_req_id, str) or res_req_id != artifact.request_id:
        return False

    res_gen = result.get("generation")
    if type(res_gen) is not int or res_gen != artifact.generation:
        return False

    res_intent = result.get("intent")
    if not isinstance(res_intent, str) or res_intent != artifact.intent or res_intent != "copy_project_overview":
        return False

    res_snap_id = result.get("snapshot_id")
    if not isinstance(res_snap_id, str) or res_snap_id != artifact.snapshot_id:
        return False

    res_man_id = result.get("manifest_id")
    if not isinstance(res_man_id, str) or res_man_id != artifact.manifest_id:
        return False

    res_proj = result.get("project_name")
    if not isinstance(res_proj, str) or res_proj != artifact.project_name:
        return False

    res_led_sig = result.get("ledger_signature")
    if not isinstance(res_led_sig, str) or res_led_sig != artifact.ledger_signature:
        return False

    res_policy = result.get("formatter_policy")
    if not isinstance(res_policy, str) or res_policy != artifact.formatter_policy:
        return False

    if "max_prompt_bytes" not in result:
        return False
    res_max_b = result["max_prompt_bytes"]
    if type(res_max_b) is not int or res_max_b != artifact.max_prompt_bytes:
        return False

    # 2. Content validations against artifact.overview
    overview = artifact.overview
    if not isinstance(overview, ProjectOverviewRenderResult):
        return False

    res_prompt = result.get("rendered_prompt")
    if not isinstance(res_prompt, str) or res_prompt != overview.rendered_prompt:
        return False

    try:
        actual_bytes = len(res_prompt.encode("utf-8"))
        actual_sha = hashlib.sha256(res_prompt.encode("utf-8")).hexdigest()
    except Exception:
        return False

    res_bytes = result.get("rendered_bytes")
    if type(res_bytes) is not int or res_bytes != overview.rendered_bytes or res_bytes != actual_bytes:
        return False

    res_sha = result.get("rendered_sha256")
    if not isinstance(res_sha, str) or res_sha != overview.rendered_sha256 or res_sha != actual_sha:
        return False

    res_rec_cnt = result.get("received_batches_count")
    if type(res_rec_cnt) is not int or res_rec_cnt != overview.received_batches_count:
        return False

    res_tot_cnt = result.get("total_batches_expected")
    if type(res_tot_cnt) is not int or res_tot_cnt != overview.total_batches_expected:
        return False

    res_all_rec = result.get("all_batch_drafts_received")
    if type(res_all_rec) is not bool or res_all_rec != overview.all_batch_drafts_received:
        return False

    res_cov_comp = result.get("source_coverage_complete")
    if type(res_cov_comp) is not bool or res_cov_comp != overview.source_coverage_complete:
        return False

    res_missing_ids = result.get("missing_batch_ids")
    if isinstance(res_missing_ids, bool) or not isinstance(res_missing_ids, (tuple, list)):
        return False
    if tuple(res_missing_ids) != overview.missing_batch_ids:
        return False

    # 3. Creative context signature validation
    res_cc_sig = result.get("creative_context_signature")
    art_cc_sig = artifact.creative_context_signature

    if art_cc_sig is not None:
        if (
            isinstance(art_cc_sig, bool)
            or not isinstance(art_cc_sig, str)
            or len(art_cc_sig) != 64
            or not all(c in "0123456789abcdef" for c in art_cc_sig)
        ):
            return False
        if (
            isinstance(res_cc_sig, bool)
            or not isinstance(res_cc_sig, str)
            or len(res_cc_sig) != 64
            or not all(c in "0123456789abcdef" for c in res_cc_sig)
            or res_cc_sig != art_cc_sig
        ):
            return False
    else:
        if res_cc_sig is not None:
            return False

    return True


@dataclass(frozen=True)
class PromptPreparationRequest:
    """Yêu cầu chuẩn bị Prompt kịch bản (Immutable Value Object).
    
    Chỉ chứa dữ liệu nguyên thủy và pure value objects bất biến đã thẩm định (CreativeContext),
    tuyệt đối không chứa QObject hay Resolve proxy.
    """
    request_id: str
    generation: int
    intent: str  # 'copy_video' | 'run_ai' | 'prepare_batches' | 'copy_batch' | 'validate_batch_draft' | 'copy_project_overview'
    video_paths: Tuple[str, ...]
    whisper_model: str = "small"
    whisper_language: str = "vi"
    vision_context: Optional[Dict[str, Any]] = None
    enable_visual_describe: bool = False
    style_intent: str = "travel_vlog"
    target_duration: str = "full"
    hook_duration: str = "30_60s"
    project_name: str = "ChunDVC_Project"
    max_prompt_bytes: int = 32768
    manifest_cache_dir: Optional[str] = None
    manifest_metadata: Optional[Dict[str, Any]] = None
    # Additive fields cho batch
    prepared_batch_snapshot: Optional[Any] = None
    selected_batch_index: Optional[int] = None
    target_batch_duration_sec: float = 600.0
    max_batch_duration_sec: float = 900.0
    max_clips_per_batch: int = 20
    window_partition_sec: float = 600.0
    # Additive expected binding fields for copy_batch & validate_batch_draft (G06-06A3-R2 / R3 / 06A4b-I)
    expected_snapshot_id: Optional[str] = None
    expected_batch_id: Optional[str] = None
    expected_rendered_sha256: Optional[str] = None
    expected_formatter_policy: Optional[str] = None
    expected_manifest_id: Optional[str] = None
    expected_project_name: Optional[str] = None
    expected_ui_context_signature: Optional[Any] = None
    # Additive fields cho validate_batch_draft (G06-06A4b-I)
    batch_draft_text: str = ""
    associate_with_current_snapshot: bool = False
    selected_batch_id: Optional[str] = None
    # Additive fields cho copy_project_overview (G06-06A4c2a-I)
    project_overview_context: Optional[Any] = None
    expected_ledger_signature: Optional[str] = None
    # Additive field at END (G06-06C3-S2a)
    creative_context: Optional[Union[Mapping[str, Any], CreativeContext]] = None

    def __post_init__(self):
        valid_intents = ("copy_video", "run_ai", "prepare_batches", "copy_batch", "validate_batch_draft", "copy_project_overview")
        if self.intent not in valid_intents:
            raise ValueError(f"ERR_INVALID_INTENT: intent phải thuộc {valid_intents}, nhận: {self.intent!r}")
        if isinstance(self.max_prompt_bytes, bool) or not isinstance(self.max_prompt_bytes, int) or not (1 <= self.max_prompt_bytes <= 32768):
            raise ValueError(f"ERR_INVALID_CONFIG: max_prompt_bytes phải là int trong khoảng 1..32768, nhận: {self.max_prompt_bytes!r}")
        if self.selected_batch_index is not None:
            if isinstance(self.selected_batch_index, bool) or not isinstance(self.selected_batch_index, int) or self.selected_batch_index < 0:
                raise ValueError(f"ERR_INVALID_INDEX: selected_batch_index phải là int >= 0, nhận: {self.selected_batch_index!r}")
        if self.intent == "validate_batch_draft":
            if not isinstance(self.batch_draft_text, str):
                raise ValueError(f"ERR_INVALID_CONFIG: batch_draft_text phải là str, nhận: {type(self.batch_draft_text).__name__}")
            if len(self.batch_draft_text.encode("utf-8")) > 65536:
                raise ValueError(f"ERR_OVERSIZE_PAYLOAD: batch_draft_text vượt quá 65536 bytes")
        if self.intent == "copy_project_overview":
            if isinstance(self.request_id, bool) or not isinstance(self.request_id, str) or not self.request_id.strip():
                raise ValueError(f"ERR_INVALID_CONFIG: request_id phải là chuỗi không rỗng, nhận: {self.request_id!r}")
            if isinstance(self.generation, bool) or not isinstance(self.generation, int) or self.generation < 0:
                raise ValueError(f"ERR_INVALID_CONFIG: generation phải là int >= 0 và không phải bool, nhận: {self.generation!r}")
            if self.expected_ui_context_signature is not None:
                try:
                    frozen_ui_sig = _freeze_context_signature(self.expected_ui_context_signature)
                    object.__setattr__(self, "expected_ui_context_signature", frozen_ui_sig)
                except ValueError as ve:
                    raise ValueError(f"ERR_INVALID_CONFIG: expected_ui_context_signature không hợp lệ: {ve}")
            if self.expected_ledger_signature is not None:
                if (
                    isinstance(self.expected_ledger_signature, bool)
                    or not isinstance(self.expected_ledger_signature, str)
                    or len(self.expected_ledger_signature) != 64
                    or not all(c in "0123456789abcdefABCDEF" for c in self.expected_ledger_signature)
                ):
                    raise ValueError(f"ERR_INVALID_CONFIG: expected_ledger_signature phải là chuỗi 64-hex, nhận: {self.expected_ledger_signature!r}")
            if self.project_overview_context is not None and not isinstance(self.project_overview_context, ProjectOverviewContextInput):
                raise ValueError(f"ERR_INVALID_CONFIG: project_overview_context phải là ProjectOverviewContextInput, nhận: {type(self.project_overview_context).__name__}")

        if self.creative_context is not None:
            if isinstance(self.creative_context, bool) or not isinstance(self.creative_context, (Mapping, CreativeContext)):
                raise ValueError(f"ERR_INVALID_CONFIG: creative_context phải là Mapping hoặc CreativeContext, nhận: {type(self.creative_context).__name__}")
            try:
                owned_cc = normalize_creative_context(self.creative_context)
                object.__setattr__(self, "creative_context", owned_cc)
            except Exception as e:
                raise ValueError(f"ERR_INVALID_CONFIG: creative_context không hợp lệ: {e}")

            empty_cc = normalize_creative_context({})
            supported_creative_intents = (
                "copy_video",
                "run_ai",
                "prepare_batches",
                "copy_batch",
                "validate_batch_draft",
                "copy_project_overview",
            )
            if owned_cc != empty_cc and self.intent not in supported_creative_intents:
                raise ValueError(f"ERR_CREATIVE_CONTEXT_UNSUPPORTED_INTENT: creative_context chỉ được hỗ trợ cho intent {supported_creative_intents}, nhận intent: {self.intent!r}")


class PromptPreparationWorker(QThread):
    """Luồng worker chuẩn bị kịch bản phân tích nguồn cho ReviewPage & DirectorWindow."""

    started_signal = Signal(str)                          # (request_id)
    finished_signal = Signal(str, bool, str, dict)        # (request_id, success, message, result)

    def __init__(
        self,
        request: PromptPreparationRequest,
        cache_mgr: Optional[Any] = None,
        visual_index: Optional[Any] = None,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.request = request
        self._is_cancelled = False
        self._terminal_emitted = False
        self._project_overview_artifact: Optional[PreparedProjectOverviewArtifact] = None

        # DI dependencies phục vụ kiểm thử đơn vị ngoại tuyến
        self._cache_mgr_arg = cache_mgr
        self._visual_index_arg = visual_index

        # Bắt giữ bản sao độc lập (owned normalized snapshot) ngay tại thời điểm khởi tạo
        # Bẫy lỗi có kiểm soát nếu context không hợp lệ để phát invalid_config trong run(), không ném ngoại lệ Qt constructor
        self._captured_creative_context: Optional[CreativeContext] = None
        self._captured_creative_error: Optional[str] = None

        raw_cc = getattr(request, "creative_context", None)
        if raw_cc is not None:
            if isinstance(raw_cc, bool) or not isinstance(raw_cc, (Mapping, CreativeContext)):
                self._captured_creative_error = (
                    f"creative_context phải là Mapping hoặc CreativeContext, nhận: {type(raw_cc).__name__}"
                )
            else:
                try:
                    self._captured_creative_context = normalize_creative_context(raw_cc)
                except Exception as ce:
                    self._captured_creative_error = f"creative_context không hợp lệ: {ce}"

        # Bắt giữ độc lập trường creative_context trong project_overview_context (nếu có)
        self._captured_overview_cc: Optional[CreativeContext] = None
        self._captured_overview_cc_error: Optional[str] = None

        if hasattr(request, "project_overview_context") and request.project_overview_context is not None:
            ov_ctx = request.project_overview_context
            if not _has_initialized_field(ov_ctx, "creative_context"):
                self._captured_overview_cc_error = (
                    "project_overview_context bị thiếu trường creative_context hoặc không được khởi tạo hợp lệ."
                )
            else:
                raw_ov_cc = ov_ctx.creative_context
                if raw_ov_cc is not None:
                    if isinstance(raw_ov_cc, bool) or not isinstance(raw_ov_cc, (Mapping, CreativeContext)):
                        self._captured_overview_cc_error = (
                            f"project_overview_context.creative_context phải là Mapping hoặc CreativeContext, nhận: {type(raw_ov_cc).__name__}"
                        )
                    else:
                        try:
                            self._captured_overview_cc = normalize_creative_context(raw_ov_cc)
                        except Exception as ce:
                            self._captured_overview_cc_error = f"project_overview_context.creative_context không hợp lệ: {ce}"

    def cancel(self):
        """Yêu cầu dừng cooperative luồng chuẩn bị."""
        self._is_cancelled = True
        self._project_overview_artifact = None

    def get_project_overview_artifact(self) -> Optional[PreparedProjectOverviewArtifact]:
        """Trả về artifact kết quả tổng quan dự án do worker sở hữu (None nếu hủy hoặc thất bại)."""
        if self._is_cancelled:
            return None
        return self._project_overview_artifact

    def is_cancelled(self) -> bool:
        return self._is_cancelled

    def _emit_terminal(self, success: bool, message: str, result: Dict[str, Any]):
        """Đảm bảo đúng 1 lần phát tín hiệu kết thúc logic (terminal signal)."""
        if not self._terminal_emitted:
            self._terminal_emitted = True
            self.finished_signal.emit(
                self.request.request_id,
                success,
                message,
                copy.deepcopy(result)
            )

    def run(self):
        req = self.request
        req_id = req.request_id
        paths = list(req.video_paths)

        try:
            self.started_signal.emit(req_id)

            # Revalidate & capture fresh local owned creative snapshot BEFORE source/cache/formatter IO
            if self._captured_creative_error is not None:
                self._emit_terminal(
                    False,
                    "invalid_config",
                    {"error": self._captured_creative_error}
                )
                return

            captured_cc: Optional[CreativeContext] = None
            is_nonempty_cc = False
            captured_cc_sig: Optional[str] = None
            if self._captured_creative_context is not None:
                try:
                    captured_cc = normalize_creative_context(self._captured_creative_context)
                except Exception as ce:
                    self._emit_terminal(
                        False,
                        "invalid_config",
                        {"error": f"creative_context không hợp lệ: {ce}"}
                    )
                    return
                empty_cc = normalize_creative_context({})
                is_nonempty_cc = (captured_cc != empty_cc)

            supported_creative_intents = (
                "copy_video",
                "run_ai",
                "prepare_batches",
                "copy_batch",
                "validate_batch_draft",
                "copy_project_overview",
            )
            if is_nonempty_cc and req.intent not in supported_creative_intents:
                self._emit_terminal(
                    False,
                    "unsupported_intent",
                    {"error": f"ERR_CREATIVE_CONTEXT_UNSUPPORTED_INTENT: creative_context chỉ được hỗ trợ cho intent {supported_creative_intents}, nhận intent: {req.intent!r}"}
                )
                return

            if is_nonempty_cc and captured_cc is not None:
                captured_cc_sig = compute_creative_context_signature(captured_cc)

            # 0. Nhánh Lightweight Source Recheck cho copy_batch (0 re-analysis)
            if req.intent == "copy_batch":
                # Thẩm định snapshot bằng pure SSOT
                snapshot = validate_and_normalize_snapshot(req.prepared_batch_snapshot)
                if snapshot is None:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": "Không có snapshot phân lô hợp lệ hoặc dữ liệu snapshot bị sai lệch."}
                    )
                    return

                # Ràng buộc Creative Context cho copy_batch (FAIL-CLOSED TRƯỚC SOURCE/CACHE IO)
                snap_creative_sig = snapshot.creative_context_signature
                if is_nonempty_cc:
                    if snap_creative_sig is None:
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {"error": "Yêu cầu sao chép lô có ngữ cảnh sáng tạo nhưng snapshot phân lô là bản ghi legacy không chứa creative_context_signature."}
                        )
                        return
                    if snap_creative_sig != captured_cc_sig:
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {
                                "error": f"Chữ ký ngữ cảnh sáng tạo của yêu cầu ({captured_cc_sig}) không khớp với snapshot phân lô ({snap_creative_sig})."
                            }
                        )
                        return
                else:
                    if snap_creative_sig is not None:
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {"error": "Snapshot phân lô yêu cầu chữ ký ngữ cảnh sáng tạo nhưng yêu cầu sao chép lô hiện tại để trống hoặc là legacy."}
                        )
                        return

                # G06-06A3-R3: Required Expected Binding Gate - BẮT BUỘC, fail-closed trước source IO
                req_bindings = {
                    "expected_snapshot_id": req.expected_snapshot_id,
                    "expected_batch_id": req.expected_batch_id,
                    "expected_rendered_sha256": req.expected_rendered_sha256,
                    "expected_formatter_policy": req.expected_formatter_policy,
                    "expected_manifest_id": req.expected_manifest_id,
                    "expected_project_name": req.expected_project_name,
                }
                for f_name, f_val in req_bindings.items():
                    if type(f_val) is not str or not f_val.strip():
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {"error": f"Trường kỳ vọng bắt buộc '{f_name}' bị thiếu hoặc sai kiểu (nhận: {f_val!r})."}
                        )
                        return

                if snapshot.snapshot_id != req.expected_snapshot_id:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Snapshot ID ({snapshot.snapshot_id}) không khớp expected ({req.expected_snapshot_id})."}
                    )
                    return

                if snapshot.formatter_policy != req.expected_formatter_policy:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Formatter policy ({snapshot.formatter_policy}) không khớp expected ({req.expected_formatter_policy})."}
                    )
                    return

                if snapshot.manifest_id != req.expected_manifest_id:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Manifest ID ({snapshot.manifest_id}) không khớp expected ({req.expected_manifest_id})."}
                    )
                    return

                if snapshot.project_name != req.expected_project_name:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Project name ({snapshot.project_name}) không khớp expected ({req.expected_project_name})."}
                    )
                    return

                if req.project_name and req.project_name != req.expected_project_name:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Request project_name ({req.project_name}) không khớp expected_project_name ({req.expected_project_name})."}
                    )
                    return

                if req.manifest_metadata and req.manifest_metadata.get("manifest_id"):
                    meta_mid = str(req.manifest_metadata.get("manifest_id")).strip()
                    if meta_mid and meta_mid != req.expected_manifest_id:
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {"error": f"Manifest ID trong metadata ({meta_mid}) không khớp expected ({req.expected_manifest_id})."}
                        )
                        return

                if req.expected_ui_context_signature is not None:
                    if snapshot.ui_context_signature is not None and snapshot.ui_context_signature != req.expected_ui_context_signature:
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {"error": "Snapshot ui_context_signature không khớp expected."}
                        )
                        return

                # Thẩm định selected_batch_index: strict int, >= 0, < len(batches)
                if (
                    req.selected_batch_index is None
                    or type(req.selected_batch_index) is not int
                    or not (0 <= req.selected_batch_index < len(snapshot.batches))
                ):
                    self._emit_terminal(
                        False,
                        "invalid_index",
                        {"error": f"Chỉ số lô {req.selected_batch_index!r} không hợp lệ (tổng số lô: {len(snapshot.batches)})."}
                    )
                    return

                selected_batch = snapshot.batches[req.selected_batch_index]

                if selected_batch.batch_id != req.expected_batch_id:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Batch ID ({selected_batch.batch_id}) không khớp expected ({req.expected_batch_id})."}
                    )
                    return

                if selected_batch.rendered_sha256 != req.expected_rendered_sha256:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": "Rendered SHA256 của lô không khớp expected binding."}
                    )
                    return

                # Bắt buộc baseline_sources nonempty & đối chiếu 100% khớp danh sách video_paths của request
                if not snapshot.baseline_sources:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": "Danh sách tệp nguồn cơ sở (baseline_sources) rỗng trong snapshot."}
                    )
                    return

                snapshot_paths = [b.path for b in snapshot.baseline_sources]
                req_paths = [os.path.normpath(os.path.abspath(p)) for p in req.video_paths]

                if snapshot_paths != req_paths:
                    self._emit_terminal(
                        False,
                        "source_changed",
                        {
                            "error": "Danh sách video nguồn không khớp chính xác với snapshot phân lô.",
                            "expected": snapshot_paths,
                            "actual": req_paths,
                        }
                    )
                    return

                # Lightweight Source Recheck: regular file, readability, st_size, st_mtime_ns, st_ctime_ns, st_ino, st_dev
                for b_item in snapshot.baseline_sources:
                    if self._is_cancelled:
                        self._emit_terminal(False, "cancelled", {})
                        return
                    norm_p = b_item.path
                    if not os.path.exists(norm_p) or not os.path.isfile(norm_p):
                        self._emit_terminal(False, "source_missing", {"error": f"Tệp nguồn không tồn tại: {norm_p}", "clip_path": norm_p})
                        return
                    if not os.access(norm_p, os.R_OK):
                        self._emit_terminal(False, "source_error", {"error": f"Không có quyền đọc tệp: {norm_p}", "clip_path": norm_p})
                        return
                    try:
                        st = os.stat(norm_p)
                        if not stat.S_ISREG(st.st_mode):
                            self._emit_terminal(False, "source_error", {"error": f"Tệp không phải regular file: {norm_p}", "clip_path": norm_p})
                            return

                        # Kiểm tra toàn diện identity: size, mtime, ctime, inode, dev
                        st_ctime = getattr(st, "st_ctime_ns", 0)
                        mismatch = (
                            (st.st_size != b_item.st_size)
                            or (st.st_mtime_ns != b_item.st_mtime_ns)
                            or (st_ctime != b_item.st_ctime_ns)
                            or (st.st_ino != b_item.st_ino)
                            or (st.st_dev != b_item.st_dev)
                        )
                        if mismatch:
                            self._emit_terminal(
                                False,
                                "source_changed",
                                {
                                    "error": f"Tệp nguồn đã bị thay đổi (metadata/inode không khớp): {norm_p}",
                                    "clip_path": norm_p
                                }
                            )
                            return
                    except Exception as exc:
                        self._emit_terminal(False, "source_error", {"error": f"Lỗi kiểm tra tệp nguồn: {norm_p} ({exc})", "clip_path": norm_p})
                        return

                if self._is_cancelled:
                    self._emit_terminal(False, "cancelled", {})
                    return

                # Payload selected batch verification: nonempty str, exact byte count, budget cap, digest check
                prompt_text = selected_batch.rendered_prompt
                if not isinstance(prompt_text, str) or not prompt_text.strip():
                    self._emit_terminal(False, "invalid_snapshot", {"error": "Kịch bản lô đã render rỗng."})
                    return

                actual_bytes = len(prompt_text.encode("utf-8"))
                if actual_bytes != selected_batch.rendered_bytes:
                    self._emit_terminal(
                        False,
                        "budget_error",
                        {
                            "error": f"Dung lượng prompt thực tế ({actual_bytes} B) lệch so với rendered_bytes ({selected_batch.rendered_bytes} B) trong snapshot."
                        }
                    )
                    return

                if actual_bytes > req.max_prompt_bytes:
                    self._emit_terminal(
                        False,
                        "budget_error",
                        {
                            "error": f"Dung lượng prompt ({actual_bytes} bytes) vượt quá trần cho phép ({req.max_prompt_bytes} bytes)."
                        }
                    )
                    return

                actual_sha = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
                if actual_sha != selected_batch.rendered_sha256:
                    self._emit_terminal(
                        False,
                        "budget_error",
                        {
                            "error": "Digest SHA256 của prompt không khớp với rendered_sha256 đã lưu trong snapshot."
                        }
                    )
                    return

                result_payload = {
                    "request_id": req_id,
                    "generation": req.generation,
                    "intent": "copy_batch",
                    "selected_batch_index": req.selected_batch_index,
                    "prompt_text": prompt_text,
                    "prompt_bytes": actual_bytes,
                    "rendered_bytes": actual_bytes,
                    "rendered_sha256": actual_sha,
                    "batch_id": selected_batch.batch_id,
                    "snapshot_id": snapshot.snapshot_id,
                    "manifest_id": snapshot.manifest_id,
                    "project_name": snapshot.project_name,
                }
                if is_nonempty_cc and captured_cc_sig is not None:
                    result_payload["creative_context_signature"] = captured_cc_sig
                self._emit_terminal(True, "success", result_payload)
                return

            # 0B. Nhánh Thẩm định Bản thảo Phân lô (Dedicated Batch Draft Intake) cho G06-06A4b-I
            if req.intent == "validate_batch_draft":
                if self._is_cancelled:
                    self._emit_terminal(False, "cancelled", {})
                    return

                # 1. Thẩm định snapshot bằng pure SSOT
                snapshot = validate_and_normalize_snapshot(req.prepared_batch_snapshot)
                if snapshot is None:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": "Không có snapshot phân lô hợp lệ hoặc dữ liệu snapshot bị sai lệch.", "error_code": "ERR_STALE_SNAPSHOT"}
                    )
                    return

                # Ràng buộc Creative Context cho validate_batch_draft (FAIL-CLOSED TRƯỚC SOURCE/CACHE IO)
                snap_creative_sig = snapshot.creative_context_signature
                if is_nonempty_cc:
                    if snap_creative_sig is None:
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {
                                "error": "Yêu cầu thẩm định bản thảo có ngữ cảnh sáng tạo nhưng snapshot phân lô là bản ghi legacy không chứa creative_context_signature.",
                                "error_code": "ERR_STALE_SNAPSHOT",
                            }
                        )
                        return
                    if snap_creative_sig != captured_cc_sig:
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {
                                "error": f"Chữ ký ngữ cảnh sáng tạo của yêu cầu ({captured_cc_sig}) không khớp với snapshot phân lô ({snap_creative_sig}).",
                                "error_code": "ERR_STALE_SNAPSHOT",
                            }
                        )
                        return
                else:
                    if snap_creative_sig is not None:
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {
                                "error": "Snapshot phân lô yêu cầu chữ ký ngữ cảnh sáng tạo nhưng yêu cầu thẩm định bản thảo hiện tại để trống hoặc là legacy.",
                                "error_code": "ERR_STALE_SNAPSHOT",
                            }
                        )
                        return

                # 2. Required Expected Binding Gate - BẮT BUỘC 6 bindings
                req_bindings = {
                    "expected_snapshot_id": req.expected_snapshot_id,
                    "expected_batch_id": req.expected_batch_id,
                    "expected_rendered_sha256": req.expected_rendered_sha256,
                    "expected_formatter_policy": req.expected_formatter_policy,
                    "expected_manifest_id": req.expected_manifest_id,
                    "expected_project_name": req.expected_project_name,
                }
                for f_name, f_val in req_bindings.items():
                    if type(f_val) is not str or not f_val.strip():
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {"error": f"Trường kỳ vọng bắt buộc '{f_name}' bị thiếu hoặc sai kiểu (nhận: {f_val!r}).", "error_code": "ERR_STALE_SNAPSHOT"}
                        )
                        return

                if snapshot.snapshot_id != req.expected_snapshot_id:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Snapshot ID ({snapshot.snapshot_id}) không khớp expected ({req.expected_snapshot_id}).", "error_code": "ERR_STALE_SNAPSHOT"}
                    )
                    return

                if snapshot.formatter_policy != req.expected_formatter_policy:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Formatter policy ({snapshot.formatter_policy}) không khớp expected ({req.expected_formatter_policy}).", "error_code": "ERR_STALE_SNAPSHOT"}
                    )
                    return

                if snapshot.manifest_id != req.expected_manifest_id:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Manifest ID ({snapshot.manifest_id}) không khớp expected ({req.expected_manifest_id}).", "error_code": "ERR_MANIFEST_MISMATCH"}
                    )
                    return

                if snapshot.project_name != req.expected_project_name:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Project name ({snapshot.project_name}) không khớp expected ({req.expected_project_name}).", "error_code": "ERR_MANIFEST_MISMATCH"}
                    )
                    return

                if req.project_name and req.project_name != req.expected_project_name:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Request project_name ({req.project_name}) không khớp expected_project_name ({req.expected_project_name}).", "error_code": "ERR_MANIFEST_MISMATCH"}
                    )
                    return

                if req.manifest_metadata and req.manifest_metadata.get("manifest_id"):
                    meta_mid = str(req.manifest_metadata.get("manifest_id")).strip()
                    if meta_mid and meta_mid != req.expected_manifest_id:
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {"error": f"Manifest ID trong metadata ({meta_mid}) không khớp expected ({req.expected_manifest_id}).", "error_code": "ERR_MANIFEST_MISMATCH"}
                        )
                        return

                if req.expected_ui_context_signature is not None:
                    if snapshot.ui_context_signature is not None and snapshot.ui_context_signature != req.expected_ui_context_signature:
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {"error": "Snapshot ui_context_signature không khớp expected.", "error_code": "ERR_STALE_SNAPSHOT"}
                        )
                        return

                # 3. Thẩm định selected_batch_index
                if (
                    req.selected_batch_index is None
                    or type(req.selected_batch_index) is not int
                    or not (0 <= req.selected_batch_index < len(snapshot.batches))
                ):
                    self._emit_terminal(
                        False,
                        "invalid_index",
                        {"error": f"Chỉ số lô {req.selected_batch_index!r} không hợp lệ (tổng số lô: {len(snapshot.batches)}).", "error_code": "ERR_BATCH_ID_MISMATCH"}
                    )
                    return

                selected_batch = snapshot.batches[req.selected_batch_index]

                if selected_batch.batch_id != req.expected_batch_id:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Batch ID ({selected_batch.batch_id}) không khớp expected ({req.expected_batch_id}).", "error_code": "ERR_BATCH_ID_MISMATCH"}
                    )
                    return

                if req.selected_batch_id and req.selected_batch_id != selected_batch.batch_id:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"selected_batch_id ({req.selected_batch_id}) không khớp lô đang chọn ({selected_batch.batch_id}).", "error_code": "ERR_BATCH_ID_MISMATCH"}
                    )
                    return

                if selected_batch.rendered_sha256 != req.expected_rendered_sha256:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": "Rendered SHA256 của lô không khớp expected binding.", "error_code": "ERR_STALE_SNAPSHOT"}
                    )
                    return

                # 4. Kiểm tra sự tồn tại của source_ranges (Prepared Geometry Check)
                if not getattr(selected_batch, "source_ranges", None):
                    self._emit_terminal(
                        False,
                        "missing_geometry",
                        {
                            "error": "Lô kịch bản này thiếu thông tin hình học phân mảnh (source_ranges). Vui lòng thực hiện Phân lô lại kịch bản.",
                            "error_code": ERR_MISSING_PREPARED_GEOMETRY,
                        }
                    )
                    return

                # 5. Pre-validation source recheck: tệp nguồn phải tồn tại và không thay đổi
                if not snapshot.baseline_sources:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": "Danh sách tệp nguồn cơ sở (baseline_sources) rỗng trong snapshot.", "error_code": "ERR_STALE_SNAPSHOT"}
                    )
                    return

                snapshot_paths = [b.path for b in snapshot.baseline_sources]
                req_paths = [os.path.normpath(os.path.abspath(p)) for p in req.video_paths]

                if snapshot_paths != req_paths:
                    self._emit_terminal(
                        False,
                        "source_changed",
                        {
                            "error": "Danh sách video nguồn không khớp chính xác với snapshot phân lô.",
                            "expected": snapshot_paths,
                            "actual": req_paths,
                        }
                    )
                    return

                for b_item in snapshot.baseline_sources:
                    if self._is_cancelled:
                        self._emit_terminal(False, "cancelled", {})
                        return
                    norm_p = b_item.path
                    if not os.path.exists(norm_p) or not os.path.isfile(norm_p):
                        self._emit_terminal(False, "source_missing", {"error": f"Tệp nguồn không tồn tại: {norm_p}", "clip_path": norm_p})
                        return
                    if not os.access(norm_p, os.R_OK):
                        self._emit_terminal(False, "source_error", {"error": f"Không có quyền đọc tệp: {norm_p}", "clip_path": norm_p})
                        return
                    try:
                        st = os.stat(norm_p)
                        if not stat.S_ISREG(st.st_mode):
                            self._emit_terminal(False, "source_error", {"error": f"Tệp không phải regular file: {norm_p}", "clip_path": norm_p})
                            return
                        st_ctime = getattr(st, "st_ctime_ns", 0)
                        mismatch = (
                            (st.st_size != b_item.st_size)
                            or (st.st_mtime_ns != b_item.st_mtime_ns)
                            or (st_ctime != b_item.st_ctime_ns)
                            or (st.st_ino != b_item.st_ino)
                            or (st.st_dev != b_item.st_dev)
                        )
                        if mismatch:
                            self._emit_terminal(
                                False,
                                "source_changed",
                                {
                                    "error": f"Tệp nguồn đã bị thay đổi (metadata/inode không khớp): {norm_p}",
                                    "clip_path": norm_p
                                }
                            )
                            return
                    except Exception as exc:
                        self._emit_terminal(False, "source_error", {"error": f"Lỗi kiểm tra tệp nguồn: {norm_p} ({exc})", "clip_path": norm_p})
                        return

                if self._is_cancelled:
                    self._emit_terminal(False, "cancelled", {})
                    return

                # 6. Strict bounded JSON decode
                ok_json, err_code, err_msg, decoded_payload = decode_strict_batch_draft_json(req.batch_draft_text)
                if not ok_json or decoded_payload is None:
                    self._emit_terminal(
                        False,
                        "invalid_draft_json",
                        {"error": err_msg or "Mã JSON bản thảo không hợp lệ.", "error_code": err_code or ERR_MALFORMED_JSON}
                    )
                    return

                # 7. Pure core draft validation against actual selected batch geometry
                val_result = validate_batch_draft_payload(
                    payload=decoded_payload,
                    expected_manifest_id=snapshot.manifest_id,
                    expected_snapshot_id=snapshot.snapshot_id,
                    expected_batch_id=selected_batch.batch_id,
                    source_ranges=selected_batch.source_ranges,
                    associate_with_current_snapshot=req.associate_with_current_snapshot,
                    raw_text=req.batch_draft_text,
                )

                if not val_result.ok or val_result.entry is None:
                    self._emit_terminal(
                        False,
                        "validation_failed",
                        {
                            "error": val_result.error_message or "Thẩm định bản thảo thất bại.",
                            "error_code": val_result.error_code or "ERR_VALIDATION_FAILED",
                        }
                    )
                    return

                if self._is_cancelled:
                    self._emit_terminal(False, "cancelled", {})
                    return

                # 8. Post-validation source recheck: đảm bảo file nguồn không bị biến đổi trong lúc validate
                for b_item in snapshot.baseline_sources:
                    norm_p = b_item.path
                    try:
                        st = os.stat(norm_p)
                        st_ctime = getattr(st, "st_ctime_ns", 0)
                        mismatch = (
                            (st.st_size != b_item.st_size)
                            or (st.st_mtime_ns != b_item.st_mtime_ns)
                            or (st_ctime != b_item.st_ctime_ns)
                            or (st.st_ino != b_item.st_ino)
                            or (st.st_dev != b_item.st_dev)
                        )
                        if mismatch:
                            self._emit_terminal(
                                False,
                                "source_changed",
                                {
                                    "error": f"Tệp nguồn đã bị thay đổi sau thẩm định: {norm_p}",
                                    "clip_path": norm_p
                                }
                            )
                            return
                    except Exception as exc:
                        self._emit_terminal(False, "source_error", {"error": f"Lỗi kiểm tra tệp nguồn: {norm_p} ({exc})", "clip_path": norm_p})
                        return

                if self._is_cancelled:
                    self._emit_terminal(False, "cancelled", {})
                    return

                # 9. Phát tín hiệu kết thúc thành công kèm payload đã thẩm định
                entry_dict = val_result.entry.to_dict()
                result_payload = {
                    "request_id": req_id,
                    "generation": req.generation,
                    "intent": "validate_batch_draft",
                    "snapshot_id": snapshot.snapshot_id,
                    "manifest_id": snapshot.manifest_id,
                    "project_name": snapshot.project_name,
                    "batch_id": selected_batch.batch_id,
                    "selected_batch_index": req.selected_batch_index,
                    "validated_entry": entry_dict,
                    "draft_digest": val_result.entry.draft_digest,
                    "binding_mode": val_result.entry.binding_mode,
                    "batch_strategy_summary": val_result.entry.batch_strategy_summary,
                    "total_cuts_count": val_result.entry.total_cuts_count,
                    "total_selected_duration_sec": val_result.entry.total_selected_duration_sec,
                }
                if is_nonempty_cc and captured_cc_sig is not None:
                    result_payload["creative_context_signature"] = captured_cc_sig
                self._emit_terminal(True, "success", result_payload)
                return

            # 0C. Nhánh Chuẩn Bị Kịch Bản Tổng Quan Dự Án (copy_project_overview cho G06-06A4c2a-I / R1)
            if req.intent == "copy_project_overview":
                if self._is_cancelled:
                    self._emit_terminal(False, "cancelled", {})
                    return

                # 1. Thẩm định các trường expected binding bắt buộc
                if isinstance(req_id, bool) or not isinstance(req_id, str) or not req_id.strip():
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"request_id phải là chuỗi không rỗng, nhận: {req_id!r}."}
                    )
                    return

                req_bindings = {
                    "expected_snapshot_id": req.expected_snapshot_id,
                    "expected_manifest_id": req.expected_manifest_id,
                    "expected_project_name": req.expected_project_name,
                    "expected_ledger_signature": req.expected_ledger_signature,
                }
                for f_name, f_val in req_bindings.items():
                    if isinstance(f_val, bool) or not isinstance(f_val, str) or not f_val.strip():
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {"error": f"Trường kỳ vọng bắt buộc '{f_name}' bị thiếu hoặc sai kiểu (nhận: {f_val!r})."}
                        )
                        return

                exp_sig = req.expected_ledger_signature
                if (
                    isinstance(exp_sig, bool)
                    or not isinstance(exp_sig, str)
                    or len(exp_sig) != 64
                    or not all(c in "0123456789abcdefABCDEF" for c in exp_sig)
                ):
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"expected_ledger_signature không phải là 64-hex hợp lệ: {exp_sig!r}."}
                    )
                    return

                if isinstance(req.generation, bool) or not isinstance(req.generation, int) or req.generation < 0:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"generation phải là int >= 0 và không phải bool, nhận: {req.generation!r}."}
                    )
                    return

                ctx_input = req.project_overview_context
                if ctx_input is None or not isinstance(ctx_input, ProjectOverviewContextInput):
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": "project_overview_context bị thiếu hoặc không phải ProjectOverviewContextInput."}
                    )
                    return

                if self._captured_overview_cc_error is not None:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": self._captured_overview_cc_error}
                    )
                    return

                # 2. Thẩm định snapshot bằng pure SSOT
                snapshot = validate_and_normalize_snapshot(req.prepared_batch_snapshot)
                if snapshot is None:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": "Không có snapshot phân lô hợp lệ hoặc dữ liệu snapshot bị sai lệch."}
                    )
                    return

                # Ràng buộc Creative Context cho copy_project_overview (FAIL-CLOSED TRƯỚC SOURCE/FORMATTER IO)
                snap_creative_sig = snapshot.creative_context_signature
                if is_nonempty_cc:
                    if snap_creative_sig is None:
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {"error": "Yêu cầu tổng quan có ngữ cảnh sáng tạo nhưng snapshot phân lô là bản ghi legacy không chứa creative_context_signature."}
                        )
                        return
                    if snap_creative_sig != captured_cc_sig:
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {
                                "error": f"Chữ ký ngữ cảnh sáng tạo của yêu cầu ({captured_cc_sig}) không khớp với snapshot phân lô ({snap_creative_sig})."
                            }
                        )
                        return
                else:
                    if snap_creative_sig is not None:
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {"error": "Snapshot phân lô yêu cầu chữ ký ngữ cảnh sáng tạo nhưng yêu cầu tổng quan hiện tại để trống hoặc là legacy."}
                        )
                        return

                # Thẩm định và phân giải trường creative_context trùng lặp trong overview context input
                empty_cc = normalize_creative_context({})
                is_overview_cc_nonempty = (self._captured_overview_cc is not None and self._captured_overview_cc != empty_cc)

                if is_nonempty_cc:
                    if is_overview_cc_nonempty:
                        ov_sig = compute_creative_context_signature(self._captured_overview_cc)
                        if ov_sig != captured_cc_sig:
                            self._emit_terminal(
                                False,
                                "invalid_snapshot",
                                {"error": f"project_overview_context.creative_context ({ov_sig}) xung đột với request.creative_context ({captured_cc_sig})."}
                            )
                            return
                    resolved_cc = captured_cc
                else:
                    if is_overview_cc_nonempty:
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {"error": "project_overview_context chứa creative_context nhưng request không có creative_context tương ứng."}
                        )
                        return
                    resolved_cc = None

                owned_ctx_input = replace(ctx_input, creative_context=resolved_cc)

                # 3. Đối soát required UI context signatures (BẮT BUỘC, ZERO FALLBACK)
                if req.expected_ui_context_signature is None:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": "expected_ui_context_signature bắt buộc phải có mặt và không được là None."}
                    )
                    return

                if snapshot.ui_context_signature is None:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": "snapshot.ui_context_signature bắt buộc phải có mặt và không được là None (zero fallback sang evidence_context_signature)."}
                    )
                    return

                try:
                    frozen_req_ui_sig = _freeze_context_signature(req.expected_ui_context_signature)
                    frozen_snap_ui_sig = _freeze_context_signature(snapshot.ui_context_signature)
                except ValueError as ve:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Lỗi thẩm định context signature: {ve}"}
                    )
                    return

                if frozen_snap_ui_sig != frozen_req_ui_sig:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": "Snapshot ui_context_signature không khớp expected_ui_context_signature."}
                    )
                    return

                # 4. Đối soát context với normalized snapshot và request bindings
                if snapshot.snapshot_id != req.expected_snapshot_id:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Snapshot ID ({snapshot.snapshot_id}) không khớp expected ({req.expected_snapshot_id})."}
                    )
                    return
                if ctx_input.snapshot_id != snapshot.snapshot_id:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Context snapshot_id ({ctx_input.snapshot_id}) không khớp snapshot ({snapshot.snapshot_id})."}
                    )
                    return

                if snapshot.manifest_id != req.expected_manifest_id:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Manifest ID ({snapshot.manifest_id}) không khớp expected ({req.expected_manifest_id})."}
                    )
                    return
                if ctx_input.manifest_id != snapshot.manifest_id:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Context manifest_id ({ctx_input.manifest_id}) không khớp snapshot ({snapshot.manifest_id})."}
                    )
                    return

                if snapshot.project_name != req.expected_project_name:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Project name ({snapshot.project_name}) không khớp expected ({req.expected_project_name})."}
                    )
                    return
                if ctx_input.project_name != snapshot.project_name:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Context project_name ({ctx_input.project_name}) không khớp snapshot ({snapshot.project_name})."}
                    )
                    return

                # Đối soát style exact (strict str)
                if not isinstance(req.style_intent, str) or not isinstance(ctx_input.style_intent, str):
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": "style_intent phải là chuỗi văn bản str."}
                    )
                    return
                if req.style_intent != ctx_input.style_intent:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Context style_intent ({ctx_input.style_intent!r}) không khớp request ({req.style_intent!r})."}
                    )
                    return

                # Đối soát budget ba chiều: snapshot.max_prompt_bytes == req.max_prompt_bytes == ctx_input.max_prompt_bytes
                if type(req.max_prompt_bytes) is not int or not (1 <= req.max_prompt_bytes <= 32768):
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Request max_prompt_bytes không hợp lệ (1..32768): {req.max_prompt_bytes!r}."}
                    )
                    return
                if type(snapshot.max_prompt_bytes) is not int or snapshot.max_prompt_bytes != req.max_prompt_bytes:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Snapshot max_prompt_bytes ({snapshot.max_prompt_bytes}) không khớp request ({req.max_prompt_bytes})."}
                    )
                    return
                if type(ctx_input.max_prompt_bytes) is not int or ctx_input.max_prompt_bytes != req.max_prompt_bytes:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Context max_prompt_bytes ({ctx_input.max_prompt_bytes}) không khớp request ({req.max_prompt_bytes})."}
                    )
                    return

                # Đối soát hình học lô phân mảnh
                if len(ctx_input.batches) != len(snapshot.batches):
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Số lượng lô trong context ({len(ctx_input.batches)}) lệch so với snapshot ({len(snapshot.batches)})."}
                    )
                    return
                for b_ctx, b_snap in zip(ctx_input.batches, snapshot.batches):
                    if b_ctx.batch_id != b_snap.batch_id or b_ctx.batch_index != b_snap.batch_index:
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {"error": f"Lô {b_ctx.batch_id} không khớp lô {b_snap.batch_id} trong snapshot."}
                        )
                        return
                    if b_ctx.source_ranges != b_snap.source_ranges:
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {"error": f"source_ranges của lô {b_ctx.batch_id} trong context không khớp snapshot."}
                        )
                        return

                # Đối soát unaccounted clips
                if len(ctx_input.unaccounted_clips) != len(snapshot.unaccounted_clips):
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Số lượng unaccounted clips trong context ({len(ctx_input.unaccounted_clips)}) lệch so với snapshot ({len(snapshot.unaccounted_clips)})."}
                    )
                    return
                for u_ctx, u_snap in zip(ctx_input.unaccounted_clips, snapshot.unaccounted_clips):
                    if (
                        u_ctx.clip_id != u_snap.clip_id
                        or u_ctx.reason != u_snap.reason
                        or u_ctx.message != u_snap.message
                    ):
                        self._emit_terminal(
                            False,
                            "invalid_snapshot",
                            {"error": f"unaccounted_clip {u_ctx.clip_id} trong context không khớp snapshot."}
                        )
                        return

                # Đối soát chữ ký ledger tính toán với expected_ledger_signature
                try:
                    computed_led_sig = compute_project_overview_ledger_signature(
                        snapshot.snapshot_id, ctx_input.received_drafts
                    )
                except ValueError as ve:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Lỗi tính toán chữ ký ledger từ context: {ve}"}
                    )
                    return

                if computed_led_sig.lower() != exp_sig.lower():
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Chữ ký ledger tính toán ({computed_led_sig}) không khớp expected ({exp_sig})."}
                    )
                    return

                # 4. Source IO Policy: Pre-check baseline_sources
                if not snapshot.baseline_sources:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": "Danh sách tệp nguồn cơ sở (baseline_sources) rỗng trong snapshot."}
                    )
                    return

                snapshot_paths = [b.path for b in snapshot.baseline_sources]
                req_paths = [os.path.normpath(os.path.abspath(p)) for p in req.video_paths]

                if snapshot_paths != req_paths:
                    self._emit_terminal(
                        False,
                        "source_changed",
                        {
                            "error": "Danh sách video nguồn không khớp chính xác với snapshot phân lô.",
                            "expected": snapshot_paths,
                            "actual": req_paths,
                        }
                    )
                    return

                def _verify_baseline_sources() -> Tuple[bool, str, Dict[str, Any]]:
                    for b_item in snapshot.baseline_sources:
                        if self._is_cancelled:
                            return False, "cancelled", {}
                        norm_p = b_item.path
                        if not os.path.exists(norm_p) or not os.path.isfile(norm_p):
                            return False, "source_missing", {"error": f"Tệp nguồn không tồn tại: {norm_p}", "clip_path": norm_p}
                        if not os.access(norm_p, os.R_OK):
                            return False, "source_error", {"error": f"Không có quyền đọc tệp: {norm_p}", "clip_path": norm_p}
                        try:
                            st = os.stat(norm_p)
                            if not stat.S_ISREG(st.st_mode):
                                return False, "source_error", {"error": f"Tệp không phải regular file: {norm_p}", "clip_path": norm_p}
                            st_ctime = getattr(st, "st_ctime_ns", 0)
                            mismatch = (
                                (st.st_size != b_item.st_size)
                                or (st.st_mtime_ns != b_item.st_mtime_ns)
                                or (st_ctime != b_item.st_ctime_ns)
                                or (st.st_ino != b_item.st_ino)
                                or (st.st_dev != b_item.st_dev)
                            )
                            if mismatch:
                                return False, "source_changed", {
                                    "error": f"Tệp nguồn đã bị thay đổi (metadata/inode không khớp): {norm_p}",
                                    "clip_path": norm_p
                                }
                        except Exception as exc:
                            return False, "source_error", {"error": f"Lỗi kiểm tra tệp nguồn: {norm_p} ({exc})", "clip_path": norm_p}
                    return True, "ok", {}

                pre_ok, pre_msg, pre_res = _verify_baseline_sources()
                if not pre_ok:
                    self._emit_terminal(False, pre_msg, pre_res)
                    return

                if self._is_cancelled:
                    self._emit_terminal(False, "cancelled", {})
                    return

                # 5. Gọi pure core render_project_overview_prompt
                try:
                    render_res = render_project_overview_prompt(owned_ctx_input)
                except Exception as exc:
                    self._emit_terminal(
                        False,
                        "invalid_snapshot",
                        {"error": f"Lỗi thực thi formatter tổng quan: {exc}"}
                    )
                    return

                if not render_res.ok:
                    err_msg = render_res.message
                    err_code = render_res.error_code
                    msg_type = "budget_error" if err_code == ERR_OVERVIEW_BUDGET_OVERFLOW else (
                        "no_data" if err_code == ERR_OVERVIEW_NO_DRAFTS else "invalid_snapshot"
                    )
                    self._emit_terminal(
                        False,
                        msg_type,
                        {
                            "error": err_msg,
                            "error_code": err_code,
                            "required_bytes": render_res.required_bytes,
                        }
                    )
                    return

                if self._is_cancelled:
                    self._emit_terminal(False, "cancelled", {})
                    return

                # 6. Source IO Policy: Post-check baseline_sources
                post_ok, post_msg, post_res = _verify_baseline_sources()
                if not post_ok:
                    self._emit_terminal(False, post_msg, post_res)
                    return

                if self._is_cancelled:
                    self._emit_terminal(False, "cancelled", {})
                    return

                # 7. Khởi tạo và lưu trữ PreparedProjectOverviewArtifact bất biến
                artifact = PreparedProjectOverviewArtifact(
                    request_id=req_id,
                    generation=req.generation,
                    intent="copy_project_overview",
                    snapshot_id=snapshot.snapshot_id,
                    manifest_id=snapshot.manifest_id,
                    project_name=snapshot.project_name,
                    ledger_signature=computed_led_sig.lower(),
                    formatter_policy=render_res.formatter_policy,
                    max_prompt_bytes=req.max_prompt_bytes,
                    context_signature=frozen_snap_ui_sig,
                    overview=render_res,
                    creative_context_signature=(captured_cc_sig if is_nonempty_cc else None),
                )
                self._project_overview_artifact = artifact

                # 8. Phát tín hiệu kết thúc thành công kèm transport dictionary
                overview_payload = {
                    "request_id": req_id,
                    "generation": req.generation,
                    "intent": "copy_project_overview",
                    "snapshot_id": snapshot.snapshot_id,
                    "manifest_id": snapshot.manifest_id,
                    "project_name": snapshot.project_name,
                    "ledger_signature": computed_led_sig.lower(),
                    "formatter_policy": render_res.formatter_policy,
                    "max_prompt_bytes": req.max_prompt_bytes,
                    "rendered_prompt": render_res.rendered_prompt,
                    "rendered_bytes": render_res.rendered_bytes,
                    "rendered_sha256": render_res.rendered_sha256,
                    "received_batches_count": render_res.received_batches_count,
                    "total_batches_expected": render_res.total_batches_expected,
                    "all_batch_drafts_received": render_res.all_batch_drafts_received,
                    "source_coverage_complete": render_res.source_coverage_complete,
                    "missing_batch_ids": render_res.missing_batch_ids,
                }
                if is_nonempty_cc and captured_cc_sig is not None:
                    overview_payload["creative_context_signature"] = captured_cc_sig
                self._emit_terminal(True, "success", overview_payload)
                return

            if not paths:
                self._emit_terminal(False, "zero_clips", {"error": "Không có tệp video nào để tạo kịch bản."})
                return

            if self._is_cancelled:
                self._emit_terminal(False, "cancelled", {})
                return

            # 1. Preflight kiểm tra sự tồn tại và tính hợp lệ của tệp nguồn
            st_pres = {}
            for p in paths:
                if self._is_cancelled:
                    self._emit_terminal(False, "cancelled", {})
                    return
                norm_p = os.path.normpath(os.path.abspath(p))
                if not os.path.exists(norm_p) or not os.path.isfile(norm_p):
                    self._emit_terminal(
                        False,
                        "source_missing",
                        {"error": f"Tệp nguồn không tồn tại hoặc không phải là tệp: {norm_p}", "clip_path": norm_p}
                    )
                    return
                if not os.access(norm_p, os.R_OK):
                    self._emit_terminal(
                        False,
                        "source_error",
                        {"error": f"Không có quyền đọc tệp nguồn: {norm_p}", "clip_path": norm_p}
                    )
                    return
                try:
                    st = os.stat(norm_p)
                    if not stat.S_ISREG(st.st_mode):
                        self._emit_terminal(
                            False,
                            "source_error",
                            {"error": f"Tệp nguồn không phải là regular file: {norm_p}", "clip_path": norm_p}
                        )
                        return
                    st_pres[norm_p] = st
                except Exception as exc:
                    self._emit_terminal(
                        False,
                        "source_error",
                        {"error": f"Lỗi đọc trạng thái tệp nguồn: {norm_p} ({exc})", "clip_path": norm_p}
                    )
                    return

            # 2. Xây dựng hoặc tái sử dụng ClipManifest chính xác theo danh sách đường dẫn
            from src.core.clip_manifest import ClipManifest, ClipEntry, build_clips_data
            from src.core.story_copilot import StoryCopilot

            manifest = None

            # 2a. Dùng request.manifest_metadata nếu exact project và paths hợp lệ -> reconstruct pure / reuse IDs/order
            if req.manifest_metadata and isinstance(req.manifest_metadata, dict):
                meta_proj = str(req.manifest_metadata.get("project_name", "")).strip()
                meta_clips = req.manifest_metadata.get("clips") or []
                if meta_proj == req.project_name and meta_clips and req.manifest_metadata.get("manifest_id"):
                    meta_paths = [os.path.normpath(os.path.abspath(c.get("current_path") or c.get("path") or "")) for c in meta_clips if isinstance(c, dict)]
                    req_norm = [os.path.normpath(os.path.abspath(p)) for p in paths]
                    if meta_paths == req_norm:
                        try:
                            clips = [ClipEntry.from_dict(c) for c in meta_clips]
                            manifest = ClipManifest(
                                project_name=req.project_name,
                                manifest_id=str(req.manifest_metadata["manifest_id"]),
                                clips=clips,
                                created_at=str(req.manifest_metadata.get("created_at", "")),
                                updated_at=str(req.manifest_metadata.get("updated_at", "")),
                            )
                        except Exception as me:
                            logger.warning("Không thể reconstruct pure ClipManifest từ request.manifest_metadata: %s", me)
                            manifest = None

            # 2b. Nếu chưa có: kiểm tra manifest load nếu manifest_cache_dir tường minh khớp exact paths
            if manifest is None and req.manifest_cache_dir:
                existing = ClipManifest.load(req.project_name, cache_dir=req.manifest_cache_dir)
                if existing and existing.clips:
                    existing_norm = [os.path.normpath(os.path.abspath(c.current_path)) for c in existing.clips]
                    req_norm = [os.path.normpath(os.path.abspath(p)) for p in paths]
                    if existing_norm == req_norm:
                        manifest = existing

            # 2c. Nếu chưa có: tạo manifest mới trong bộ nhớ với metadata_provider an toàn (persist=False)
            if manifest is None:
                from src.core.geometry.local_metadata_acquisition import (
                    acquire_local_video_header,
                    LocalHeaderAcquisitionResult,
                )
                from src.core.clip_manifest import _validate_and_reconstruct_header_result

                current_probe_source = [None]

                def _worker_metadata_provider(src_path: str) -> LocalHeaderAcquisitionResult:
                    norm_src = os.path.normpath(os.path.abspath(src_path))
                    current_probe_source[0] = norm_src

                    if self.is_cancelled():
                        raise InterruptedError("ERR_PROBE_CANCELLED: tác vụ bị hủy bởi caller")

                    res = acquire_local_video_header(
                        norm_src,
                        timeout_sec=3.0,
                        cancelled_fn=self.is_cancelled,
                    )

                    if self.is_cancelled():
                        raise InterruptedError("ERR_PROBE_CANCELLED: tác vụ bị hủy bởi caller")

                    # Xác thực và tái tạo kết quả trước khi truy cập bất kỳ trường nào
                    res = _validate_and_reconstruct_header_result(res)

                    # Kiểm tra kết quả khớp đường dẫn (res.file_path là abs path đã xác thực)
                    res_norm = os.path.normpath(res.file_path)
                    if res_norm != norm_src:
                        raise ValueError(f"ERR_METADATA_SOURCE_MISMATCH: kết quả {res_norm} không khớp nguồn {norm_src}")

                    # Đối soát 5 trường freshness với st_pres của worker
                    pre_st = st_pres.get(norm_src)
                    if pre_st is None:
                        raise ValueError(f"ERR_SOURCE_CHANGED: thiếu stat tiền kiểm cho {norm_src}")

                    if (
                        res.freshness.file_size_bytes != pre_st.st_size
                        or res.freshness.mtime_ns != pre_st.st_mtime_ns
                        or res.freshness.ctime_ns != pre_st.st_ctime_ns
                        or res.freshness.device_id != pre_st.st_dev
                        or res.freshness.inode_number != pre_st.st_ino
                    ):
                        raise ValueError(f"ERR_SOURCE_CHANGED: tệp nguồn {norm_src} đã bị thay đổi so với tiền kiểm worker")

                    return res

                try:
                    manifest = ClipManifest.create(
                        paths,
                        project_name=req.project_name,
                        cache_dir=None,
                        metadata_provider=_worker_metadata_provider,
                        cancelled_fn=self.is_cancelled,
                        persist=False,
                    )
                except InterruptedError as ie:
                    err_msg = str(ie)
                    c_path = current_probe_source[0] or getattr(ie, "clip_path", None)
                    if "ERR_PROBE_CLEANUP_FAILED" in err_msg:
                        self._emit_terminal(
                            False,
                            "source_error",
                            {"error": f"Lỗi thu thập siêu dữ liệu nguồn: {err_msg}", "clip_path": c_path}
                        )
                        return
                    self._emit_terminal(False, "cancelled", {})
                    return
                except Exception as exc:
                    err_msg = str(exc)
                    c_path = current_probe_source[0] or getattr(exc, "clip_path", None)
                    is_cleanup_fail = "ERR_PROBE_CLEANUP_FAILED" in err_msg or (
                        isinstance(exc, RuntimeError) and "ERR_PROBE_CLEANUP_FAILED" in str(exc)
                    )
                    # Lỗi cleanup tiến trình con/reader phải ưu tiên phát source_error kể cả khi caller yêu cầu hủy
                    if not is_cleanup_fail and (isinstance(exc, InterruptedError) or self.is_cancelled()):
                        self._emit_terminal(False, "cancelled", {})
                        return

                    tag = "source_changed" if ("ERR_SOURCE_CHANGED" in err_msg or "ERR_SOURCE_MUTATED" in err_msg) else "source_error"
                    self._emit_terminal(
                        False,
                        tag,
                        {"error": f"Lỗi thu thập siêu dữ liệu nguồn: {err_msg}", "clip_path": c_path}
                    )
                    return

            if self._is_cancelled:
                self._emit_terminal(False, "cancelled", {})
                return

            # 3. Khởi tạo managers nền nếu chưa được truyền qua DI
            if self._cache_mgr_arg is not None:
                cache_mgr = self._cache_mgr_arg
            else:
                from src.core.cache_manager import ScanCacheManager
                cache_mgr = ScanCacheManager()

            if self._visual_index_arg is not None:
                visual_index = self._visual_index_arg
            else:
                from src.core.visual_index import VisualIndex
                visual_index = VisualIndex()

            # 4. Chuẩn bị visual_context
            # Chỉ truyền các key mà producer thực tế lưu trong signature để tránh false quarantine
            vis_ctx = None
            if req.enable_visual_describe and req.vision_context:
                v_model = req.vision_context.get("vision_model") or req.vision_context.get("model") or ""
                v_endpoint = _normalize_endpoint(req.vision_context.get("vision_endpoint") or req.vision_context.get("endpoint") or "")
                vis_ctx = {
                    "model": v_model,
                    "endpoint": v_endpoint,
                    "window_sec": req.vision_context.get("window_sec", 8.0),
                    "frames_per_window": req.vision_context.get("frames_per_window", 2),
                    "max_windows": req.vision_context.get("max_windows", 10),
                    "max_refinements": req.vision_context.get("max_refinements", 1),
                }

            # 5. Dựng clips_data với verify_audio=True nghiêm ngặt
            clips_data = build_clips_data(
                manifest,
                cache_mgr=cache_mgr,
                model=req.whisper_model,
                lang=req.whisper_language,
                visual_index=visual_index,
                verify_audio=True,
                visual_context=vis_ctx,
                cancelled_fn=self.is_cancelled,
            )

            if self._is_cancelled:
                self._emit_terminal(False, "cancelled", {})
                return

            # Nếu thị giác bị tắt: loại bỏ machine descriptions, chỉ giữ ghi chú người dùng
            if not req.enable_visual_describe:
                for c in clips_data:
                    c["visual_segments"] = [s for s in c.get("visual_segments", []) if s.get("origin") == "user"]
                    c["visual_analysis_status"] = "unscanned"
                    c["visual_stale_excluded_count"] = 0

            # 6. Post-stat kiểm tra tính nguyên vẹn của nguồn sau khi đọc
            for c in clips_data:
                cpath = c.get("current_path") or c.get("path")
                if cpath:
                    norm_cpath = os.path.normpath(os.path.abspath(cpath))
                    st_pre = st_pres.get(norm_cpath)
                    try:
                        st_post = os.stat(norm_cpath)
                        if st_pre is not None:
                            if (st_post.st_mtime_ns != st_pre.st_mtime_ns) or (st_post.st_size != st_pre.st_size):
                                self._emit_terminal(
                                    False,
                                    "source_changed",
                                    {"error": f"Tệp nguồn đã bị thay đổi trong quá trình phân tích: {norm_cpath}", "clip_path": norm_cpath}
                                )
                                return
                    except FileNotFoundError:
                        self._emit_terminal(
                            False,
                            "source_missing",
                            {"error": f"Tệp nguồn đã bị xóa trong quá trình phân tích: {norm_cpath}", "clip_path": norm_cpath}
                        )
                        return
                    except Exception as exc:
                        self._emit_terminal(
                            False,
                            "source_error",
                            {"error": f"Lỗi kiểm tra tệp nguồn: {norm_cpath} ({exc})", "clip_path": norm_cpath}
                        )
                        return

                if c.get("source_availability") in ("source_missing", "source_changed", "source_error"):
                    self._emit_terminal(
                        False,
                        c.get("source_availability"),
                        {"error": f"Tệp nguồn không hợp lệ: {c.get('name')}", "clip_id": c.get("clip_id")}
                    )
                    return

            # 7B. Nhánh phân lô G06 (Batch Consumer Path)
            if req.intent == "prepare_batches":
                if self._is_cancelled:
                    self._emit_terminal(False, "cancelled", {})
                    return

                # 7B.1 Thích ứng clips_data sang analysis_cache qua pure adapter G06-06A2
                from src.core.batch_evidence_adapter import adapt_clips_data_to_batch_analysis_cache, AdapterConfig
                adapter_cfg = AdapterConfig(
                    max_input_records=100000,
                    max_windows_per_clip=10000,
                )
                try:
                    adapted_snapshot = adapt_clips_data_to_batch_analysis_cache(
                        manifest=manifest,
                        clips_data=clips_data,
                        config=adapter_cfg,
                    )
                except Exception as ae:
                    self._emit_terminal(False, "adapter_error", {"error": f"Lỗi thích ứng bằng chứng lô: {ae}"})
                    return

                if self._is_cancelled:
                    self._emit_terminal(False, "cancelled", {})
                    return

                # 7B.2 Tạo static prefix/suffix wrapper qua StoryCopilot
                try:
                    b_prefix, b_suffix = StoryCopilot.build_batch_prompt_wrapper(
                        project_name=req.project_name,
                        story_intent=req.style_intent,
                        target_duration=req.target_duration,
                        hook_duration=req.hook_duration,
                        creative_context=captured_cc,
                    )
                except Exception as we:
                    self._emit_terminal(False, "wrapper_error", {"error": f"Lỗi tạo khung kịch bản lô: {we}"})
                    return

                # 7B.3 Phân chia lô qua pure batch planner G06-06A1
                from src.core.batch_planner import (
                    BatchBudgetConfig,
                    partition_manifest_into_batches,
                    render_batch_prompt,
                    estimate_batch_prompt_bytes,
                )
                win_part = req.window_partition_sec
                if win_part > req.max_batch_duration_sec:
                    win_part = req.max_batch_duration_sec
                planner_cfg = BatchBudgetConfig(
                    max_prompt_bytes=req.max_prompt_bytes,
                    target_batch_duration_sec=req.target_batch_duration_sec,
                    max_batch_duration_sec=req.max_batch_duration_sec,
                    max_clips_per_batch=req.max_clips_per_batch,
                    window_partition_sec=win_part,
                    prompt_prefix=b_prefix,
                    prompt_suffix=b_suffix,
                )
                try:
                    plan = partition_manifest_into_batches(
                        manifest=manifest,
                        analysis_cache=adapted_snapshot.analysis_cache,
                        config=planner_cfg,
                    )
                except ValueError as ve:
                    self._emit_terminal(False, "budget_error", {"error": str(ve)})
                    return
                except Exception as pe:
                    self._emit_terminal(False, "planner_error", {"error": f"Lỗi chia lô kịch bản: {pe}"})
                    return

                if self._is_cancelled:
                    self._emit_terminal(False, "cancelled", {})
                    return

                # 7B.4 Render và thẩm định byte authority cho từng lô
                batch_items_rendered = []
                for b in plan.batches:
                    if self._is_cancelled:
                        self._emit_terminal(False, "cancelled", {})
                        return
                    r_prompt = render_batch_prompt(b)
                    r_bytes = len(r_prompt.encode("utf-8"))
                    if r_bytes > req.max_prompt_bytes:
                        self._emit_terminal(
                            False,
                            "budget_error",
                            {"error": f"Lô {b.batch_id} có kích thước ({r_bytes} bytes) vượt quá trần {req.max_prompt_bytes} bytes."}
                        )
                        return
                    r_sha256 = hashlib.sha256(r_prompt.encode("utf-8")).hexdigest()
                    c_ids = tuple(dict.fromkeys(it.clip_id for it in b.items))
                    n_items = len(b.items)
                    b_source_ranges = []
                    for it in b.items:
                        w_ids = tuple(w.window_id for w in it.windows)
                        b_source_ranges.append(
                            BatchSourceRange(
                                clip_id=it.clip_id,
                                source_in_sec=it.source_in_sec,
                                source_out_sec=it.source_out_sec,
                                partition_index=it.partition_index,
                                total_partitions=it.total_partitions,
                                window_ids=w_ids,
                            )
                        )
                    batch_items_rendered.append(
                        PreparedBatchItem(
                            batch_id=b.batch_id,
                            batch_index=b.batch_index,
                            distinct_clip_count=b.distinct_clip_count,
                            total_duration_sec=b.total_duration_sec,
                            rendered_prompt=r_prompt,
                            rendered_bytes=r_bytes,
                            rendered_sha256=r_sha256,
                            content_signature=b.content_signature,
                            clip_ids=c_ids,
                            items_count=n_items,
                            item_count=n_items,
                            prompt_bytes=r_bytes,
                            source_ranges=tuple(b_source_ranges),
                        )
                    )

                if self._is_cancelled:
                    self._emit_terminal(False, "cancelled", {})
                    return

                # 7B.5 Final-stat check cho toàn bộ video_paths
                for p in paths:
                    norm_p = os.path.normpath(os.path.abspath(p))
                    st_pre = st_pres.get(norm_p)
                    try:
                        if not os.path.exists(norm_p) or not os.path.isfile(norm_p):
                            self._emit_terminal(
                                False,
                                "source_missing",
                                {"error": f"Tệp nguồn đã bị xóa hoặc mất trước khi hoàn thành: {norm_p}", "clip_path": norm_p}
                            )
                            return
                        st_post = os.stat(norm_p)
                        if not stat.S_ISREG(st_post.st_mode):
                            self._emit_terminal(
                                False,
                                "source_error",
                                {"error": f"Tệp nguồn không còn là regular file: {norm_p}", "clip_path": norm_p}
                            )
                            return
                        if not os.access(norm_p, os.R_OK):
                            self._emit_terminal(
                                False,
                                "source_error",
                                {"error": f"Không thể đọc tệp nguồn trước khi hoàn thành: {norm_p}", "clip_path": norm_p}
                            )
                            return
                        if st_pre is not None:
                            changed = (
                                (st_post.st_size != st_pre.st_size)
                                or (st_post.st_mtime_ns != st_pre.st_mtime_ns)
                                or (hasattr(st_post, "st_ctime_ns") and hasattr(st_pre, "st_ctime_ns") and st_post.st_ctime_ns != st_pre.st_ctime_ns)
                                or (hasattr(st_post, "st_ino") and hasattr(st_pre, "st_ino") and st_post.st_ino != st_pre.st_ino)
                                or (hasattr(st_post, "st_dev") and hasattr(st_pre, "st_dev") and st_post.st_dev != st_pre.st_dev)
                            )
                            if changed:
                                self._emit_terminal(
                                    False,
                                    "source_changed",
                                    {"error": f"Tệp nguồn đã bị thay đổi trước khi hoàn tất chia lô: {norm_p}", "clip_path": norm_p}
                                )
                                return
                    except FileNotFoundError:
                        self._emit_terminal(
                            False,
                            "source_missing",
                            {"error": f"Tệp nguồn không còn tồn tại: {norm_p}", "clip_path": norm_p}
                        )
                        return
                    except Exception as exc:
                        self._emit_terminal(
                            False,
                            "source_error",
                            {"error": f"Lỗi kiểm tra tệp nguồn cuối: {norm_p} ({exc})", "clip_path": norm_p}
                        )
                        return

                if self._is_cancelled:
                    self._emit_terminal(False, "cancelled", {})
                    return

                # 7B.6 Xây dựng snapshot phân lô bất biến (Owned Immutable Snapshot)
                baseline_sources = tuple(
                    BaselineSourceEntry(
                        path=os.path.normpath(os.path.abspath(p)),
                        st_size=int(st_pres[os.path.normpath(os.path.abspath(p))].st_size),
                        st_mtime_ns=int(st_pres[os.path.normpath(os.path.abspath(p))].st_mtime_ns),
                        st_ctime_ns=int(getattr(st_pres[os.path.normpath(os.path.abspath(p))], "st_ctime_ns", 0)),
                        st_ino=int(st_pres[os.path.normpath(os.path.abspath(p))].st_ino),
                        st_dev=int(st_pres[os.path.normpath(os.path.abspath(p))].st_dev),
                    )
                    for p in paths
                    if os.path.normpath(os.path.abspath(p)) in st_pres
                )

                planned_clip_ids = tuple(sorted({it.clip_id for b in plan.batches for it in b.items}))

                snapshot_id = compute_snapshot_id(
                    manifest_id=manifest.manifest_id,
                    project_name=manifest.project_name,
                    baseline_sources=baseline_sources,
                    batches=tuple(batch_items_rendered),
                    evidence_context_signature=adapted_snapshot.context_signature,
                    max_prompt_bytes=req.max_prompt_bytes,
                    formatter_policy="source_only_v1",
                    creative_context_signature=captured_cc_sig if is_nonempty_cc else None,
                )

                batch_snapshot = PreparedBatchSnapshot(
                    manifest_id=manifest.manifest_id,
                    project_name=manifest.project_name,
                    snapshot_id=snapshot_id,
                    formatter_policy="source_only_v1",
                    total_batches=len(plan.batches),
                    planned_items_count=sum(len(b.items) for b in plan.batches),
                    distinct_planned_clips_count=plan.planned_clip_count,
                    planned_clip_ids=planned_clip_ids,
                    planned_clip_ids_count=plan.planned_clip_count,
                    unaccounted_clip_ids=tuple(plan.unaccounted_clips),
                    unaccounted_clips=tuple(
                        UnaccountedClipEntry(
                            clip_id=iss.clip_id,
                            reason=iss.reason,
                            message=iss.message,
                        )
                        for iss in plan.issues
                    ),
                    adapter_issues=tuple(
                        AdapterIssueEntry(
                            clip_id=iss.clip_id,
                            issue_type=iss.issue_type,
                            message=iss.message,
                        )
                        for iss in adapted_snapshot.issues
                    ),
                    max_prompt_bytes=req.max_prompt_bytes,
                    batches=tuple(batch_items_rendered),
                    baseline_sources=baseline_sources,
                    evidence_context_signature=adapted_snapshot.context_signature,
                    ui_context_signature=None,
                    creative_context_signature=captured_cc_sig if is_nonempty_cc else None,
                )

                result_payload = {
                    "request_id": req_id,
                    "generation": req.generation,
                    "intent": "prepare_batches",
                    "manifest_id": manifest.manifest_id,
                    "project_name": manifest.project_name,
                    "batch_snapshot": batch_snapshot,
                    "manifest_metadata": {
                        "project_name": manifest.project_name,
                        "manifest_id": manifest.manifest_id,
                        "created_at": getattr(manifest, "created_at", ""),
                        "updated_at": getattr(manifest, "updated_at", ""),
                        "clips": [c.to_dict() for c in manifest.clips] if hasattr(manifest, "clips") else [],
                    },
                    "clips_count": len(manifest.clips),
                }
                if is_nonempty_cc and captured_cc_sig is not None:
                    result_payload["creative_context_signature"] = captured_cc_sig
                self._emit_terminal(True, "success", result_payload)
                return

            # 7. Kiểm tra bằng chứng tối thiểu (Usable Evidence Check)
            # Toàn bộ inventory không có bất kỳ bằng chứng hợp lệ nào -> no_data
            has_speech = any(bool(c.get("dialogue_intervals")) for c in clips_data)
            has_audio_done = any(c.get("audio_analysis_status") in ("completed_empty", "no_audio_track") for c in clips_data)
            has_visual = any(bool(c.get("visual_segments")) for c in clips_data)
            has_samples = any(c.get("visual_coverage", {}).get("sampled_windows", 0) > 0 for c in clips_data)

            if not (has_speech or has_audio_done or has_visual or has_samples):
                self._emit_terminal(
                    False,
                    "no_data",
                    {
                        "error": "Chưa có dữ liệu phân tích âm thanh hoặc hình ảnh cho các video nguồn. "
                                 "Vui lòng chạy phân tích nguồn tại Bước 1 trước khi tạo kịch bản."
                    }
                )
                return

            if self._is_cancelled:
                self._emit_terminal(False, "cancelled", {})
                return

            # 8. Định dạng Bounded Prompt với trần byte nghiêm ngặt
            try:
                prompt_text = StoryCopilot.generate_copilot_prompt(
                    project_name=req.project_name,
                    clips_data=clips_data,
                    story_intent=req.style_intent,
                    target_duration=req.target_duration,
                    hook_duration=req.hook_duration,
                    manifest_id=manifest.manifest_id,
                    max_prompt_bytes=req.max_prompt_bytes,
                    creative_context=captured_cc,
                )
            except ValueError as ve:
                self._emit_terminal(False, "budget_error", {"error": str(ve)})
                return
            except Exception as fe:
                self._emit_terminal(False, "formatter_error", {"error": f"Lỗi định dạng kịch bản: {fe}"})
                return

            if self._is_cancelled:
                self._emit_terminal(False, "cancelled", {})
                return

            # Final source stat check: NGAY TRƯỚC khi emit terminal success
            # Bao phủ TOÀN BỘ req.video_paths (kể cả short paths đã bị filter khỏi clips_data)
            for p in paths:
                norm_p = os.path.normpath(os.path.abspath(p))
                st_pre = st_pres.get(norm_p)
                try:
                    if not os.path.exists(norm_p) or not os.path.isfile(norm_p):
                        self._emit_terminal(
                            False,
                            "source_missing",
                            {"error": f"Tệp nguồn đã bị xóa hoặc mất trước khi hoàn thành: {norm_p}", "clip_path": norm_p}
                        )
                        return
                    st_post = os.stat(norm_p)
                    if not stat.S_ISREG(st_post.st_mode):
                        self._emit_terminal(
                            False,
                            "source_error",
                            {"error": f"Tệp nguồn không còn là regular file: {norm_p}", "clip_path": norm_p}
                        )
                        return
                    if not os.access(norm_p, os.R_OK):
                        self._emit_terminal(
                            False,
                            "source_error",
                            {"error": f"Không thể đọc tệp nguồn trước khi hoàn thành: {norm_p}", "clip_path": norm_p}
                        )
                        return
                    if st_pre is not None:
                        changed = (
                            (st_post.st_size != st_pre.st_size)
                            or (st_post.st_mtime_ns != st_pre.st_mtime_ns)
                            or (hasattr(st_post, "st_ctime_ns") and hasattr(st_pre, "st_ctime_ns") and st_post.st_ctime_ns != st_pre.st_ctime_ns)
                            or (hasattr(st_post, "st_ino") and hasattr(st_pre, "st_ino") and st_post.st_ino != st_pre.st_ino)
                            or (hasattr(st_post, "st_dev") and hasattr(st_pre, "st_dev") and st_post.st_dev != st_pre.st_dev)
                        )
                        if changed:
                            self._emit_terminal(
                                False,
                                "source_changed",
                                {"error": f"Tệp nguồn đã bị thay đổi trước khi hoàn tất tạo kịch bản: {norm_p}", "clip_path": norm_p}
                            )
                            return
                except FileNotFoundError:
                    self._emit_terminal(
                        False,
                        "source_missing",
                        {"error": f"Tệp nguồn không còn tồn tại: {norm_p}", "clip_path": norm_p}
                    )
                    return
                except Exception as exc:
                    self._emit_terminal(
                        False,
                        "source_error",
                        {"error": f"Lỗi kiểm tra tệp nguồn cuối: {norm_p} ({exc})", "clip_path": norm_p}
                    )
                    return

            if self._is_cancelled:
                self._emit_terminal(False, "cancelled", {})
                return

            # 9. Hoàn thành mỹ mãn
            prompt_bytes = len(prompt_text.encode("utf-8"))
            result_payload = {
                "request_id": req_id,
                "generation": req.generation,
                "intent": req.intent,
                "prompt_text": prompt_text,
                "prompt_bytes": prompt_bytes,
                "manifest_id": manifest.manifest_id,
                "manifest_metadata": {
                    "project_name": manifest.project_name,
                    "manifest_id": manifest.manifest_id,
                    "created_at": getattr(manifest, "created_at", ""),
                    "updated_at": getattr(manifest, "updated_at", ""),
                    "clips": [c.to_dict() for c in manifest.clips] if hasattr(manifest, "clips") else [],
                },
                "clips_count": len(manifest.clips),
                "clips_data_summary": {
                    "total_clips": len(clips_data),
                    "speech_clips": sum(1 for c in clips_data if c.get("dialogue_intervals")),
                    "silent_clips": sum(1 for c in clips_data if c.get("audio_analysis_status") == "completed_empty"),
                    "no_audio_clips": sum(1 for c in clips_data if c.get("audio_analysis_status") == "no_audio_track"),
                    "visual_clips": sum(1 for c in clips_data if c.get("visual_segments")),
                }
            }
            if is_nonempty_cc and captured_cc_sig is not None:
                result_payload["creative_context_signature"] = captured_cc_sig

            self._emit_terminal(True, "success", result_payload)

        except InterruptedError:
            self._emit_terminal(False, "cancelled", {})
        except Exception as exc:
            logger.exception("Lỗi không mong muốn trong PromptPreparationWorker: %s", exc)
            self._emit_terminal(False, "unexpected_error", {"error": str(exc)})
