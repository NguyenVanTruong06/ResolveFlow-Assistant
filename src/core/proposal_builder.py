"""
src/core/proposal_builder.py
=============================================================================
Module thuần Python xây dựng Đề xuất Kịch bản Dựng (EditPlanProposal) có chủ quyền (G06-06C3-S3-R1).
Đóng gói việc ghép nối Slot 1, Slot 2, ClipManifest, CreativeContext và AssetMatchingSidecar.

Quy tắc kiến trúc:
1. Pure Python standard library (math, typing, collections.abc).
2. Không import PySide6/Qt, filesystem IO, network, time/clock, hoặc random.
3. Tái sử dụng 100% các hợp đồng cốt lõi đã đóng băng:
   - normalize_creative_context (src/core/contracts/edit_plan_contracts.py)
   - EditPlanProposal, normalize_edit_plan_proposal (src/core/contracts/edit_plan_proposal_contracts.py)
   - AssetMatchingSidecar, DemandMatchEntry, AssetDemandBatch (src/core/contracts/plan_demand_contracts.py)
   - ClipManifest, ClipEntry (src/core/clip_manifest.py)
   - PlanDemandAdapter (src/core/plan_demand_adapter.py)
4. Xác thực nghiêm ngặt đầu vào trước khi duyệt mảng, không đột biến hoặc alias dữ liệu người dùng.
5. Hạch toán chính xác 1-1 từng nhu cầu (demand) thành proposed_assignment hoặc proposed_omission.
=============================================================================
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Set, Union

from src.core.clip_manifest import ClipEntry, ClipManifest
from src.core.contracts.edit_plan_contracts import (
    CreativeContext,
    normalize_creative_context,
)
from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    normalize_edit_plan_proposal,
)
from src.core.contracts.plan_demand_contracts import (
    AssetDemandBatch,
    AssetDemandItem,
    AssetMatchingSidecar,
    DemandMatchEntry,
    validate_asset_demand_item,
    validate_demand_batch,
    validate_demand_candidate,
    validate_demand_match_result,
)
from src.core.contracts.time_contracts import RationalFPS
from src.core.plan_demand_adapter import PlanDemandAdapter

MAX_MANIFEST_CLIPS: int = 4096
MAX_TIMELINE_SEGMENTS: int = 256
MAX_INSERTS_COUNT: int = 4096
MAX_PROOF_METADATA_BYTES: int = 16 * 1024 * 1024  # 16 MiB
PROFILE_UNIFORM_TICKS_V1: str = "decoded_frame_uniform_ticks_v1"

_SHA256_HEX_REGEX = re.compile(r"^[0-9a-fA-F]{64}$")


def is_canonical_sha256_hex(val: Any) -> bool:
    """Kiểm tra chuỗi băm SHA256 canonical 64 ký tự hex hợp lệ."""
    if not isinstance(val, str) or len(val) != 64:
        return False
    return bool(_SHA256_HEX_REGEX.match(val))


def validate_and_normalize_source_evidence_snapshot(
    entry: Any,
    *,
    clip_id: str,
    expected_manifest_id: str,
    expected_path: str,
) -> Dict[str, Any]:
    """Thẩm định nghiêm ngặt và chuẩn hóa một snapshot bằng chứng nguồn thuần túy (pure).

    Quy định từ Codex G07-07A4-I:
    - manifest_id, clip_id, file_path, stream_index, observed_full_sha256
    - freshness: file_size_bytes, mtime_ns, ctime_ns, device_id, inode_number
    - frame_evidence: manifest_id, clip_id, source_fps ({numerator: int, denominator: int}), total_frames, evidence_status
    - complete_scan: strict True
    - profile_policy: exact 'decoded_frame_uniform_ticks_v1'
    - evidence_status: exact 'VERIFIED_CFR'
    - full canonical SHA-256 (64 hex lowercase)
    - positive total_frames và rational source_fps
    - Không import Qt/worker/SourceEvidenceService, zero IO, zero clock.
    """
    if not isinstance(entry, Mapping):
        raise ValueError(
            f"ERR_INVALID_EVIDENCE_SNAPSHOT: Bằng chứng của clip '{clip_id}' phải là Mapping, nhận: {type(entry).__name__}"
        )

    # 1. Thẩm định các trường bắt buộc cấp 1
    req_fields = (
        "manifest_id",
        "clip_id",
        "file_path",
        "stream_index",
        "observed_full_sha256",
        "freshness",
        "frame_evidence",
        "complete_scan",
        "profile_policy",
    )
    for rf in req_fields:
        if rf not in entry:
            raise ValueError(f"ERR_MISSING_EVIDENCE_FIELD: Snapshot của clip '{clip_id}' thiếu trường bắt buộc '{rf}'")

    # 2. Thẩm định manifest_id, clip_id, file_path
    e_mid = entry["manifest_id"]
    if type(e_mid) is not str or not e_mid.strip() or e_mid != expected_manifest_id:
        raise ValueError(
            f"ERR_EVIDENCE_MANIFEST_MISMATCH: manifest_id '{e_mid}' không khớp kỳ vọng '{expected_manifest_id}'"
        )

    e_cid = entry["clip_id"]
    if type(e_cid) is not str or not e_cid.strip() or e_cid != clip_id:
        raise ValueError(
            f"ERR_EVIDENCE_CLIP_MISMATCH: clip_id '{e_cid}' không khớp kỳ vọng '{clip_id}'"
        )

    e_path = entry["file_path"]
    if type(e_path) is not str or not e_path or not e_path.strip() or "\0" in e_path:
        raise ValueError(f"ERR_INVALID_EVIDENCE_PATH: file_path của clip '{clip_id}' phải là chuỗi đường dẫn không rỗng")
    try:
        e_path.encode("utf-8")
    except UnicodeEncodeError as u_err:
        raise ValueError(f"ERR_INVALID_UTF8_PATH: file_path của clip '{clip_id}' không phải UTF-8 hợp lệ: {u_err}")

    if type(expected_path) is not str or not expected_path or not expected_path.strip() or "\0" in expected_path:
        raise ValueError(f"ERR_INVALID_EXPECTED_PATH: expected_path của clip '{clip_id}' phải là chuỗi đường dẫn không rỗng")
    try:
        expected_path.encode("utf-8")
    except UnicodeEncodeError as u_err:
        raise ValueError(f"ERR_INVALID_UTF8_PATH: expected_path của clip '{clip_id}' không phải UTF-8 hợp lệ: {u_err}")

    # Chuẩn hóa đường dẫn để so khớp
    if os.path.normpath(e_path) != os.path.normpath(expected_path):
        raise ValueError(
            f"ERR_EVIDENCE_PATH_MISMATCH: file_path '{e_path}' không khớp đường dẫn clip '{expected_path}'"
        )

    # 3. Thẩm định stream_index (strict int >= 0, không bool)
    e_sidx = entry["stream_index"]
    if type(e_sidx) is not int or isinstance(e_sidx, bool) or e_sidx < 0:
        raise ValueError(
            f"ERR_INVALID_STREAM_INDEX: stream_index của clip '{clip_id}' phải là int >= 0, nhận: {e_sidx!r}"
        )

    # 4. Thẩm định observed_full_sha256 (canonical 64-hex)
    e_sha = entry["observed_full_sha256"]
    if type(e_sha) is not str or isinstance(e_sha, bool) or not is_canonical_sha256_hex(e_sha):
        raise ValueError(
            f"ERR_INVALID_DIGEST: observed_full_sha256 của clip '{clip_id}' không phải canonical SHA256 hex: {e_sha!r}"
        )
    norm_sha = e_sha.lower()

    # 5. Thẩm định complete_scan (strict bool True) và profile_policy
    e_cscan = entry["complete_scan"]
    if type(e_cscan) is not bool or e_cscan is not True:
        raise ValueError(
            f"ERR_INCOMPLETE_SCAN: complete_scan của clip '{clip_id}' phải là strict True, nhận: {e_cscan!r}"
        )

    e_pol = entry["profile_policy"]
    if type(e_pol) is not str or e_pol != PROFILE_UNIFORM_TICKS_V1:
        raise ValueError(
            f"ERR_UNTRUSTED_PROFILE_POLICY: profile_policy của clip '{clip_id}' phải là '{PROFILE_UNIFORM_TICKS_V1}', nhận: {e_pol!r}"
        )

    # 6. Thẩm định freshness (5-stat)
    fresh = entry["freshness"]
    if not isinstance(fresh, Mapping):
        fresh_dict = {}
        for f_attr in ("file_size_bytes", "mtime_ns", "ctime_ns", "device_id", "inode_number"):
            if not hasattr(fresh, f_attr):
                raise ValueError(f"ERR_INVALID_FRESHNESS: freshness của clip '{clip_id}' thiếu thuộc tính '{f_attr}'")
            fresh_dict[f_attr] = getattr(fresh, f_attr)
        fresh = fresh_dict

    fresh_req = ("file_size_bytes", "mtime_ns", "ctime_ns", "device_id", "inode_number")
    norm_fresh: Dict[str, int] = {}
    for f_key in fresh_req:
        if f_key not in fresh:
            raise ValueError(f"ERR_INVALID_FRESHNESS: freshness của clip '{clip_id}' thiếu trường '{f_key}'")
        f_val = fresh[f_key]
        if f_key in ("file_size_bytes", "device_id", "inode_number"):
            if type(f_val) is not int or isinstance(f_val, bool) or f_val < 0:
                raise ValueError(
                    f"ERR_INVALID_FRESHNESS_STAT: freshness.{f_key} của clip '{clip_id}' phải là int >= 0, nhận: {f_val!r}"
                )
        else:
            # mtime_ns và ctime_ns: signed int (SourceFreshnessStat policy cho phép giá trị âm trước 1970)
            if type(f_val) is not int or isinstance(f_val, bool):
                raise ValueError(
                    f"ERR_INVALID_FRESHNESS_STAT: freshness.{f_key} của clip '{clip_id}' phải là int, nhận: {f_val!r}"
                )
        norm_fresh[f_key] = f_val

    # 7. Thẩm định frame_evidence
    fe = entry["frame_evidence"]
    if not isinstance(fe, Mapping):
        fe_dict = {}
        for fe_attr in ("manifest_id", "clip_id", "source_fps", "total_frames", "evidence_status"):
            if not hasattr(fe, fe_attr):
                raise ValueError(f"ERR_INVALID_FRAME_EVIDENCE: frame_evidence của clip '{clip_id}' thiếu thuộc tính '{fe_attr}'")
            fe_dict[fe_attr] = getattr(fe, fe_attr)
        fe = fe_dict

    fe_mid = fe.get("manifest_id")
    if type(fe_mid) is not str or fe_mid != expected_manifest_id:
        raise ValueError(
            f"ERR_FRAME_EVIDENCE_MANIFEST_MISMATCH: frame_evidence.manifest_id của clip '{clip_id}' ({fe_mid!r}) không khớp {expected_manifest_id!r}"
        )

    fe_cid = fe.get("clip_id")
    if type(fe_cid) is not str or fe_cid != clip_id:
        raise ValueError(
            f"ERR_FRAME_EVIDENCE_CLIP_MISMATCH: frame_evidence.clip_id ({fe_cid!r}) không khớp {clip_id!r}"
        )

    fe_stat = fe.get("evidence_status")
    if type(fe_stat) is not str or fe_stat != "VERIFIED_CFR":
        raise ValueError(
            f"ERR_UNVERIFIED_FRAME_STATUS: frame_evidence.evidence_status của clip '{clip_id}' phải là 'VERIFIED_CFR', nhận: {fe_stat!r}"
        )

    fe_tf = fe.get("total_frames")
    if type(fe_tf) is not int or isinstance(fe_tf, bool) or fe_tf <= 0:
        raise ValueError(
            f"ERR_INVALID_TOTAL_FRAMES: frame_evidence.total_frames của clip '{clip_id}' phải là int > 0, nhận: {fe_tf!r}"
        )

    # Thẩm định rational source_fps
    fe_fps = fe.get("source_fps")
    if isinstance(fe_fps, RationalFPS) or (hasattr(fe_fps, "numerator") and hasattr(fe_fps, "denominator") and not isinstance(fe_fps, Mapping)):
        num = getattr(fe_fps, "numerator")
        den = getattr(fe_fps, "denominator")
    elif isinstance(fe_fps, Mapping):
        if "numerator" not in fe_fps or "denominator" not in fe_fps:
            raise ValueError(
                f"ERR_INVALID_RATIONAL_FPS: source_fps của clip '{clip_id}' phải có cả 'numerator' và 'denominator'"
            )
        num = fe_fps["numerator"]
        den = fe_fps["denominator"]
    else:
        raise ValueError(
            f"ERR_INVALID_RATIONAL_FPS: source_fps của clip '{clip_id}' phải là RationalFPS hoặc Mapping {{numerator, denominator}}, nhận: {type(fe_fps).__name__}"
        )

    if type(num) is not int or isinstance(num, bool) or num <= 0:
        raise ValueError(f"ERR_INVALID_RATIONAL_FPS: source_fps.numerator của clip '{clip_id}' phải là int > 0, nhận: {num!r}")
    if type(den) is not int or isinstance(den, bool) or den <= 0:
        raise ValueError(f"ERR_INVALID_RATIONAL_FPS: source_fps.denominator của clip '{clip_id}' phải là int > 0, nhận: {den!r}")
    g = math.gcd(num, den)
    norm_fps: Dict[str, int] = {"numerator": num // g, "denominator": den // g}

    norm_fe: Dict[str, Any] = {
        "manifest_id": expected_manifest_id,
        "clip_id": clip_id,
        "source_fps": norm_fps,
        "total_frames": fe_tf,
        "evidence_status": "VERIFIED_CFR",
    }

    return {
        "manifest_id": expected_manifest_id,
        "clip_id": clip_id,
        "file_path": os.path.normpath(expected_path),
        "stream_index": e_sidx,
        "observed_full_sha256": norm_sha,
        "freshness": norm_fresh,
        "frame_evidence": norm_fe,
        "complete_scan": True,
        "profile_policy": PROFILE_UNIFORM_TICKS_V1,
    }


def _to_canonical_mapping_dict(obj: Any) -> Any:
    """Chuyển đổi đệ quy Mapping/Sequences sang dict/list thuần Python mà không qua __str__."""
    if isinstance(obj, Mapping):
        return {str(k): _to_canonical_mapping_dict(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_to_canonical_mapping_dict(v) for v in obj]
    elif isinstance(obj, RationalFPS):
        return {"numerator": obj.numerator, "denominator": obj.denominator}
    elif isinstance(obj, (int, float, str, bool)) or obj is None:
        return obj
    elif hasattr(obj, "__dataclass_fields__"):
        return {k: _to_canonical_mapping_dict(getattr(obj, k)) for k in obj.__dataclass_fields__}
    else:
        raise ValueError(f"ERR_UNSUPPORTED_TYPE_IN_PROOF: Không hỗ trợ kiểu dữ liệu '{type(obj).__name__}' trong proof")


def normalize_source_evidence_map(
    source_evidence_by_clip: Mapping[str, Any],
    *,
    expected_manifest_id: Optional[str] = None,
    expected_paths_by_clip: Optional[Mapping[str, str]] = None,
) -> Tuple[Dict[str, Dict[str, Any]], bytes]:
    """Thẩm định, chuẩn hóa và kiểm tra ngân sách byte/số lượng của bảng bằng chứng nguồn.

    Quy định từ Codex G07-07A4-R2:
    - Kiểm tra mapping count <= MAX_MANIFEST_CLIPS (4096) TRƯỚC khi duyệt/lặp bất kỳ phần tử nào.
    - Ép kiểu nghiêm ngặt Mapping, không nhận kiểu dữ liệu khác.
    - Thẩm định ngân sách byte chuẩn tắc trước trần MAX_PROOF_METADATA_BYTES (16 MiB) (equality accepted).
    - Chuẩn hóa facts kỹ thuật từng clip qua validate_and_normalize_source_evidence_snapshot.
    - Định dạng chuẩn tắc JSON: ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':').
    - Trả về tuple gồm mapping chuẩn tắc và chuỗi byte UTF-8 tương ứng.
    """
    if not isinstance(source_evidence_by_clip, Mapping):
        raise ValueError(
            f"ERR_INVALID_MAPPING: source_evidence_by_clip phải là Mapping, nhận: {type(source_evidence_by_clip).__name__}"
        )
    # Precheck count <= 4096 TRƯỚC iteration
    if len(source_evidence_by_clip) > MAX_MANIFEST_CLIPS:
        raise ValueError(
            f"ERR_COUNT_OVERFLOW: source_evidence_by_clip chứa {len(source_evidence_by_clip)} mục, vượt quá giới hạn {MAX_MANIFEST_CLIPS}"
        )

    # Đo lường ngân sách byte của dữ liệu đầu vào trước
    try:
        raw_clean = _to_canonical_mapping_dict(source_evidence_by_clip)
        raw_bytes = json.dumps(
            raw_clean,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(raw_bytes) > MAX_PROOF_METADATA_BYTES:
            raise ValueError(
                f"ERR_PROOF_METADATA_BUDGET_EXCEEDED: proof metadata ({len(raw_bytes)} bytes) vượt quá ngân sách tối đa {MAX_PROOF_METADATA_BYTES} bytes"
            )
    except (TypeError, OverflowError, ValueError) as j_err:
        if "ERR_PROOF_METADATA_BUDGET_EXCEEDED" in str(j_err) or "ERR_UNSUPPORTED_TYPE_IN_PROOF" in str(j_err):
            raise
        raise ValueError(f"ERR_INVALID_PROOF_METADATA: Không thể thẩm định kích thước proof metadata: {j_err}")

    norm_map: Dict[str, Dict[str, Any]] = {}
    for cid in sorted(source_evidence_by_clip.keys(), key=str):
        if not isinstance(cid, str) or not cid.strip() or "\0" in cid:
            raise ValueError(f"ERR_INVALID_CLIP_ID: clip_id trong bằng chứng nguồn phải là str không rỗng: {cid!r}")
        entry = source_evidence_by_clip[cid]
        if not isinstance(entry, Mapping):
            raise ValueError(
                f"ERR_INVALID_EVIDENCE_ENTRY: Bằng chứng của clip '{cid}' phải là Mapping, nhận: {type(entry).__name__}"
            )

        e_mid = entry.get("manifest_id")
        if type(e_mid) is not str or not e_mid:
            raise ValueError(f"ERR_MISSING_EVIDENCE_FIELD: Snapshot của clip '{cid}' thiếu hoặc sai manifest_id")
        if expected_manifest_id is not None and str(e_mid) != str(expected_manifest_id):
            raise ValueError(
                f"ERR_EVIDENCE_MANIFEST_MISMATCH: Snapshot clip '{cid}' có manifest_id '{e_mid}' không khớp kỳ vọng '{expected_manifest_id}'"
            )

        e_path = entry.get("file_path")
        if type(e_path) is not str or not e_path:
            raise ValueError(f"ERR_MISSING_EVIDENCE_FIELD: Snapshot của clip '{cid}' thiếu hoặc sai file_path")
        if expected_paths_by_clip is not None:
            if cid not in expected_paths_by_clip:
                raise ValueError(f"ERR_UNKNOWN_EVIDENCE_CLIP: clip '{cid}' không nằm trong manifest")
            exp_p = expected_paths_by_clip[cid]
            if os.path.normpath(e_path) != os.path.normpath(exp_p):
                raise ValueError(
                    f"ERR_EVIDENCE_PATH_MISMATCH: file_path '{e_path}' không khớp đường dẫn kỳ vọng '{exp_p}'"
                )
        else:
            exp_p = e_path

        norm_entry = validate_and_normalize_source_evidence_snapshot(
            entry,
            clip_id=cid,
            expected_manifest_id=e_mid,
            expected_path=exp_p,
        )
        norm_map[cid] = norm_entry

    # Canonical UTF-8 bytes measurement trên normalized facts
    try:
        proof_bytes = json.dumps(
            norm_map,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, OverflowError, ValueError) as j_err:
        raise ValueError(f"ERR_INVALID_PROOF_METADATA: Không thể mã hóa chuẩn tắc JSON: {j_err}")

    if len(proof_bytes) > MAX_PROOF_METADATA_BYTES:
        raise ValueError(
            f"ERR_PROOF_METADATA_BUDGET_EXCEEDED: proof metadata ({len(proof_bytes)} bytes) vượt quá ngân sách tối đa {MAX_PROOF_METADATA_BYTES} bytes"
        )

    return norm_map, proof_bytes


def compute_proof_signature(evidence_by_clip: Optional[Mapping[str, Any]]) -> str:
    """Tính toán chữ ký xác thực chuẩn tắc (canonical SHA-256) của bảng bằng chứng nguồn.

    Quy định từ Codex G07-07A4-R2:
    - Băm trên danh sách có thứ tự theo clip_id các facts kỹ thuật bất biến đã chuẩn hóa.
    - None hoặc Mapping rỗng trả về 'empty_proof'.
    - Bắt buộc kiểm tra mapping count <= 4096 trước iteration.
    - Áp dụng cùng ngân sách byte chuẩn tắc MAX_PROOF_METADATA_BYTES qua normalize_source_evidence_map.
    - Non-mapping hoặc malformed entry ném ValueError, tuyệt đối không coercion/skip.
    - Hoàn toàn tất định (deterministic), pure, zero IO.
    """
    if evidence_by_clip is None:
        return "empty_proof"
    if not isinstance(evidence_by_clip, Mapping):
        raise ValueError(
            f"ERR_INVALID_MAPPING: evidence_by_clip phải là Mapping hoặc None, nhận: {type(evidence_by_clip).__name__}"
        )
    # Precheck count <= 4096 TRƯỚC iteration
    if len(evidence_by_clip) > MAX_MANIFEST_CLIPS:
        raise ValueError(
            f"ERR_COUNT_OVERFLOW: evidence_by_clip chứa {len(evidence_by_clip)} mục, vượt quá giới hạn {MAX_MANIFEST_CLIPS}"
        )
    if len(evidence_by_clip) == 0:
        return "empty_proof"

    norm_map, _ = normalize_source_evidence_map(evidence_by_clip)
    canonical_items = [norm_map[cid] for cid in sorted(norm_map.keys())]
    raw_json = json.dumps(
        canonical_items,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(raw_json.encode("utf-8")).hexdigest().lower()



def _validate_raw_nonblank_id(val: Any, field_name: str) -> str:
    """Kiểm tra ID chuỗi không rỗng, bảo toàn nguyên vẹn ID gốc không làm biến đổi bằng .strip()."""
    if val is None or type(val) is not str:
        raise ValueError(f"ERR_INVALID_ID: '{field_name}' bắt buộc phải là str, nhận: {type(val).__name__}")
    if not val or not val.strip() or "\0" in val:
        raise ValueError(f"ERR_INVALID_ID: '{field_name}' bắt buộc phải là chuỗi không rỗng.")
    return val


def _assert_demand_items_equal(d1: AssetDemandItem, d2: AssetDemandItem, context_label: str = "sidecar") -> None:
    """Xác thực hai AssetDemandItem khớp nhau từng trường một, loại trừ các đột biến kiểu (như bool source_index)."""
    validate_asset_demand_item(d1)
    validate_asset_demand_item(d2)

    if (
        type(d1.source_index) is bool
        or type(d2.source_index) is bool
        or type(d1.source_index) is not int
        or type(d2.source_index) is not int
    ):
        raise ValueError(
            f"ERR_SIDECAR_DEMAND_MISMATCH: source_index phải là int, không chấp nhận bool hoặc non-int "
            f"({type(d1.source_index).__name__}={d1.source_index!r} vs {type(d2.source_index).__name__}={d2.source_index!r})"
        )

    if (
        d1.demand_id != d2.demand_id
        or d1.source_slot != d2.source_slot
        or d1.source_index != d2.source_index
        or d1.status != d2.status
        or d1.raw_type != d2.raw_type
        or d1.category != d2.category
        or d1.timeline_sec != d2.timeline_sec
        or d1.desired_duration_sec != d2.desired_duration_sec
        or d1.raw_query_hint != d2.raw_query_hint
        or d1.query_text != d2.query_text
        or d1.query_source != d2.query_source
        or d1.rejection_reason != d2.rejection_reason
    ):
        raise ValueError(
            f"ERR_SIDECAR_DEMAND_MISMATCH: Demand '{d1.demand_id}' trong {context_label} "
            f"không khớp kịch bản chuẩn tắc."
        )


def _validate_safe_duration(val: Any, field_name: str, *, allow_zero: bool = False) -> float:
    """Kiểm tra số thực hữu hạn an toàn, bắt OverflowError thành ValueError, cấm bool/None/NaN/Inf."""
    if val is None or type(val) is bool:
        raise ValueError(f"ERR_INVALID_NUMERIC: '{field_name}' không được là None hoặc boolean.")
    if type(val) not in (int, float):
        raise ValueError(f"ERR_INVALID_NUMERIC: '{field_name}' phải là số thực hoặc số nguyên, nhận: {type(val).__name__}")
    try:
        f_val = float(val)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"ERR_NUMERIC_OVERFLOW: '{field_name}' bị tràn số hoặc lỗi chuyển đổi: {exc}")

    try:
        if not math.isfinite(f_val):
            raise ValueError(f"ERR_INVALID_NUMERIC: '{field_name}' phải là số hữu hạn (nhận {f_val}).")
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"ERR_NUMERIC_OVERFLOW: '{field_name}' bị tràn số trong isfinite: {exc}")

    if allow_zero:
        if f_val < 0.0:
            raise ValueError(f"ERR_INVALID_NUMERIC: '{field_name}' phải >= 0.0 (nhận {f_val}).")
    else:
        if f_val <= 0.0:
            raise ValueError(f"ERR_INVALID_NUMERIC: '{field_name}' phải > 0.0 (nhận {f_val}).")
    return f_val


def _is_candidate_eligible(cand: Any, ref_ids: Set[str]) -> bool:
    """Kiểm tra tính hợp lệ toàn diện của ứng viên tài nguyên."""
    if cand is None:
        return False
    avail = getattr(cand, "availability", None)
    if avail != "online":
        return False
    if getattr(cand, "requires_reconnect", False):
        return False
    if getattr(cand, "is_native_guidance", False):
        return False
    aid = getattr(cand, "asset_id", None)
    if not aid or aid in ref_ids:
        return False
    sk = getattr(cand, "source_kind", None)
    if sk in ("managed", "linked") and not getattr(cand, "origin_library_id", None):
        return False
    dur = getattr(cand, "duration_sec", None)
    if dur is not None:
        try:
            f_dur = float(dur)
            if f_dur <= 0.0 or not math.isfinite(f_dur):
                return False
        except Exception:
            return False
    return True


def build_edit_plan_proposal(
    *,
    proposal_id: str,
    manifest: Union[ClipManifest, Mapping[str, Any]],
    video_plan: Mapping[str, Any],
    creative_context: Union[CreativeContext, Mapping[str, Any]],
    kinetic_plan: Optional[Mapping[str, Any]] = None,
    sidecar: Optional[AssetMatchingSidecar] = None,
    matching_plan_revision: Optional[str] = None,
    expected_batch: Optional[Any] = None,
    selected_candidate_ids: Optional[Mapping[str, str]] = None,
    user_choices: Optional[Mapping[str, str]] = None,
    source_evidence_by_clip: Optional[Mapping[str, Any]] = None,
) -> EditPlanProposal:
    """Xây dựng và chuẩn hóa thực thể EditPlanProposal có chủ quyền từ các mảnh ghép runtime.

    Tham số:
    - proposal_id: Định danh đề xuất do caller cấp (chuỗi không rỗng, không sinh random).
    - manifest: ClipManifest hoặc Mapping chứa manifest_id và clips.
    - video_plan: Kịch bản video Slot 1 (Mapping chứa timeline_segments).
    - creative_context: Ngữ cảnh sáng tạo (CreativeContext hoặc Mapping).
    - kinetic_plan: Kịch bản phụ đề kinetic Slot 2 tùy chọn.
    - sidecar: Báo cáo tư vấn khớp tài nguyên AssetMatchingSidecar (tùy chọn).
    - matching_plan_revision: Mã băm phiên bản kịch bản dùng tra cứu sidecar (để kiểm tra khớp).
    - expected_batch: Lô nhu cầu kịch bản chuẩn tắc mong đợi (AssetDemandBatch).
    - selected_candidate_ids: Tùy biến lựa chọn của người dùng {demand_id: asset_id hoặc '__omit__'}.
    - user_choices: Bí danh tương đương của selected_candidate_ids.
    - source_evidence_by_clip: Bảng bằng chứng nguồn đã thẩm định {clip_id: snapshot} (G07-07A4-I).

    Trả về:
    - EditPlanProposal bất biến đã qua xác thực toàn diện (đúng 8 fields chuẩn tắc).
    """
    # 0. Type-gate độc lập TRƯỚC KHI truy cập hoặc kiểm tra truthiness
    if sidecar is not None and not isinstance(sidecar, AssetMatchingSidecar):
        raise ValueError(f"ERR_INVALID_SIDECAR: sidecar phải là AssetMatchingSidecar hoặc None, nhận: {type(sidecar).__name__}")

    if expected_batch is not None and not isinstance(expected_batch, AssetDemandBatch):
        raise ValueError(f"ERR_INVALID_EXPECTED_BATCH: expected_batch phải là AssetDemandBatch hoặc None, nhận: {type(expected_batch).__name__}")

    if selected_candidate_ids is not None and not isinstance(selected_candidate_ids, Mapping):
        raise ValueError(f"ERR_INVALID_MAPPING: selected_candidate_ids phải là Mapping hoặc None, nhận: {type(selected_candidate_ids).__name__}")

    if user_choices is not None and not isinstance(user_choices, Mapping):
        raise ValueError(f"ERR_INVALID_MAPPING: user_choices phải là Mapping hoặc None, nhận: {type(user_choices).__name__}")

    if selected_candidate_ids is not None and user_choices is not None:
        if dict(selected_candidate_ids) != dict(user_choices):
            raise ValueError("ERR_CONFLICTING_CHOICE_ALIASES: Cả selected_candidate_ids và user_choices đều được cung cấp nhưng có nội dung khác nhau.")

    if source_evidence_by_clip is not None:
        if not isinstance(source_evidence_by_clip, Mapping):
            raise ValueError(f"ERR_INVALID_MAPPING: source_evidence_by_clip phải là Mapping hoặc None, nhận: {type(source_evidence_by_clip).__name__}")
        # Precheck count <= 4096 TRƯỚC iteration/clone
        if len(source_evidence_by_clip) > MAX_MANIFEST_CLIPS:
            raise ValueError(f"ERR_COUNT_OVERFLOW: source_evidence_by_clip chứa {len(source_evidence_by_clip)} mục, vượt quá giới hạn {MAX_MANIFEST_CLIPS}")

    # 1. Thẩm định proposal_id do caller cấp
    pid = _validate_raw_nonblank_id(proposal_id, "proposal_id")

    # 2. Chuẩn hóa creative_context và thu thập reference_ids
    if creative_context is None:
        creative_context = {}
    c_ctx = normalize_creative_context(creative_context)
    ref_ids: Set[str] = {r.reference_id for r in c_ctx.reference_items}

    # 3. Trích xuất và thẩm định ClipManifest / source_binding
    if isinstance(manifest, ClipManifest):
        root_manifest_id = _validate_raw_nonblank_id(manifest.manifest_id, "manifest.manifest_id")
        raw_clips_val = manifest.clips
    elif isinstance(manifest, Mapping):
        root_manifest_id = _validate_raw_nonblank_id(manifest.get("manifest_id"), "manifest.manifest_id")
        raw_clips_val = manifest.get("clips", ())
    else:
        raise ValueError(f"ERR_INVALID_MANIFEST: manifest phải là ClipManifest hoặc Mapping, nhận: {type(manifest).__name__}")

    if not isinstance(raw_clips_val, (list, tuple)):
        raise ValueError(f"ERR_INVALID_SEQUENCE: manifest.clips phải là list hoặc tuple, nhận: {type(raw_clips_val).__name__}")

    # Precheck độ dài mảng clips trước khi duyệt
    if len(raw_clips_val) > MAX_MANIFEST_CLIPS:
        raise ValueError(f"ERR_COUNT_OVERFLOW: manifest.clips chứa {len(raw_clips_val)} mục, vượt quá giới hạn {MAX_MANIFEST_CLIPS}")

    source_binding_clips: List[Dict[str, Any]] = []
    valid_clip_ids: Set[str] = set()
    clip_durations: Dict[str, float] = {}

    for idx, c in enumerate(raw_clips_val):
        if isinstance(c, ClipEntry):
            cid = _validate_raw_nonblank_id(c.clip_id, f"manifest.clips[{idx}].clip_id")
            c_path = getattr(c, "current_path", "") or getattr(c, "file_path", "") or getattr(c, "path", "")
            role = getattr(c, "role", None)
            dur_raw = getattr(c, "duration_sec", getattr(c, "duration", None))
        elif isinstance(c, Mapping):
            cid = _validate_raw_nonblank_id(c.get("clip_id"), f"manifest.clips[{idx}].clip_id")
            c_path = c.get("current_path", c.get("file_path", c.get("path", "")))
            role = c.get("role")
            dur_raw = c.get("duration_sec", c.get("duration"))
        else:
            raise ValueError(f"ERR_INVALID_CLIP_ROW: manifest.clips[{idx}] phải là ClipEntry hoặc Mapping, nhận: {type(c).__name__}")

        if cid in valid_clip_ids:
            raise ValueError(f"ERR_DUPLICATE_CLIP_ID: clip_id '{cid}' bị trùng lặp trong manifest clips.")
        if cid in ref_ids or role == "reference_only":
            raise ValueError(f"ERR_REFERENCE_LEAK: clip_id '{cid}' là tài liệu tham khảo (reference_only), cấm đưa vào source clips.")

        # Thẩm định đường dẫn không được là kiểu dữ liệu sai
        if c_path is not None and c_path != "":
            if not isinstance(c_path, str):
                raise ValueError(f"ERR_INVALID_PATH: Đường dẫn clip '{cid}' phải là str, nhận: {type(c_path).__name__}")

        valid_clip_ids.add(cid)
        clip_row: Dict[str, Any] = {"clip_id": cid}
        if c_path:
            clip_row["path"] = c_path

        # Thẩm định thời lượng clip nếu có cung cấp
        if dur_raw is not None:
            dur_f = _validate_safe_duration(dur_raw, f"manifest.clips[{idx}].duration", allow_zero=False)
            clip_row["duration"] = dur_f
            clip_durations[cid] = dur_f

        source_binding_clips.append(clip_row)

    # Thẩm định và chuẩn hóa bảng bằng chứng nguồn qua normalize_source_evidence_map
    manifest_clip_paths = {c["clip_id"]: c.get("path", "") for c in source_binding_clips}
    norm_source_evidence_map: Optional[Dict[str, Dict[str, Any]]] = None
    if source_evidence_by_clip is not None:
        norm_source_evidence_map, _ = normalize_source_evidence_map(
            source_evidence_by_clip,
            expected_manifest_id=root_manifest_id,
            expected_paths_by_clip=manifest_clip_paths,
        )
        for k in norm_source_evidence_map:
            if k not in valid_clip_ids:
                raise ValueError(
                    f"ERR_UNKNOWN_EVIDENCE_CLIP: clip_id '{k}' trong source_evidence_by_clip không tồn tại trong manifest clips."
                )

        # Gắn kết các facts kỹ thuật đã chuẩn hóa vào source_binding_clips
        for clip_row in source_binding_clips:
            cid = clip_row["clip_id"]
            if cid in norm_source_evidence_map:
                norm_ev = norm_source_evidence_map[cid]
                clip_row["expected_full_sha256"] = norm_ev["observed_full_sha256"]
                clip_row["stream_index"] = norm_ev["stream_index"]

    source_binding: Dict[str, Any] = {
        "manifest_id": root_manifest_id,
        "clips": source_binding_clips,
    }

    # 4. Thẩm định video_plan (Slot 1)
    if not isinstance(video_plan, Mapping):
        raise ValueError(f"ERR_INVALID_VIDEO_PLAN: video_plan phải là Mapping, nhận: {type(video_plan).__name__}")

    # Chặn các wrapper envelope đã biết
    if "type" in video_plan and isinstance(video_plan["type"], str):
        v_type = video_plan["type"].strip().lower()
        if v_type in ("dual_plan", "kinetic_plan", "generic_plan", "project_overview", "project_overview_draft", "batch_draft"):
            raise ValueError(f"Không nhận wrapper envelope '{video_plan['type']}'. Chỉ nhận raw Video Plan Mapping.")

    # Quy tắc manifest_id của video_plan: chỉ được cấp tự động khi key hoàn toàn vắng mặt (legacy)
    if "manifest_id" in video_plan:
        vp_manifest_id = video_plan["manifest_id"]
        if vp_manifest_id is None:
            raise ValueError("ERR_MANIFEST_MISMATCH: video_plan.manifest_id không được là None.")
        vp_mid_clean = _validate_raw_nonblank_id(vp_manifest_id, "video_plan.manifest_id")
        if vp_mid_clean != root_manifest_id:
            raise ValueError(f"ERR_MANIFEST_MISMATCH: video_plan manifest_id ({vp_mid_clean!r}) không khớp manifest_id ({root_manifest_id!r})")
    else:
        # Key vắng mặt trên kịch bản legacy Slot 1 -> gán manifest_id ứng dụng sở hữu
        vp_mid_clean = root_manifest_id

    raw_segments = video_plan.get("timeline_segments")
    if not isinstance(raw_segments, (list, tuple)):
        raise ValueError(f"ERR_INVALID_SEQUENCE: video_plan.timeline_segments phải là list hoặc tuple, nhận: {type(raw_segments).__name__}")
    if len(raw_segments) == 0:
        raise ValueError("ERR_EMPTY_TIMELINE_SEGMENTS: video_plan.timeline_segments không được để trống.")
    if len(raw_segments) > MAX_TIMELINE_SEGMENTS:
        raise ValueError(f"ERR_COUNT_OVERFLOW: video_plan.timeline_segments chứa {len(raw_segments)} mục, vượt quá giới hạn {MAX_TIMELINE_SEGMENTS}")

    # Xác thực mọi clip_id trong timeline_segments phải thuộc valid_clip_ids của manifest
    owned_segments: List[Dict[str, Any]] = []
    for s_idx, seg in enumerate(raw_segments):
        if not isinstance(seg, Mapping):
            raise ValueError(f"ERR_INVALID_SEGMENT: timeline_segments[{s_idx}] phải là Mapping.")
        s_cid = seg.get("clip_id")
        if s_cid is None:
            raise ValueError(f"ERR_MISSING_CLIP_ID: timeline_segments[{s_idx}] thiếu clip_id.")
        s_cid_raw = _validate_raw_nonblank_id(s_cid, f"timeline_segments[{s_idx}].clip_id")
        if s_cid_raw not in valid_clip_ids:
            raise ValueError(f"ERR_UNKNOWN_CLIP_ID: timeline_segments[{s_idx}] sử dụng clip_id '{s_cid_raw}' không tồn tại trong manifest.")

        seg_dict = dict(seg)
        # Hỗ trợ ánh xạ start_sec / end_sec nếu kịch bản chỉ có source_in_sec / source_out_sec
        if "start_sec" not in seg_dict and "source_in_sec" in seg_dict:
            seg_dict["start_sec"] = seg_dict["source_in_sec"]
        if "end_sec" not in seg_dict and "source_out_sec" in seg_dict:
            seg_dict["end_sec"] = seg_dict["source_out_sec"]

        # Thẩm định thời gian phân đoạn đường cắt chính
        s_in_raw = seg_dict.get("start_sec")
        s_out_raw = seg_dict.get("end_sec")
        s_in = _validate_safe_duration(s_in_raw, f"timeline_segments[{s_idx}].start_sec", allow_zero=True)
        s_out = _validate_safe_duration(s_out_raw, f"timeline_segments[{s_idx}].end_sec", allow_zero=False)

        if s_in >= s_out:
            raise ValueError(f"ERR_INVALID_TIME_RANGE: timeline_segments[{s_idx}] start_sec ({s_in}) phải nhỏ hơn end_sec ({s_out}).")

        # Kiểm tra vượt thời lượng clip đã nạp (nếu clip có thời lượng)
        known_clip_dur = clip_durations.get(s_cid_raw)
        if known_clip_dur is not None and s_out > known_clip_dur:
            raise ValueError(
                f"ERR_PRIMARY_CUT_EXCEEDS_DURATION: timeline_segments[{s_idx}] end_sec ({s_out}) "
                f"vượt quá thời lượng clip '{s_cid_raw}' ({known_clip_dur}s)"
            )

        seg_dict["start_sec"] = s_in
        seg_dict["end_sec"] = s_out
        owned_segments.append(seg_dict)

    # Thẩm định raw inserts trước khi xử lý (ngăn chặn bỏ sót hoặc lọt dòng lỗi)
    raw_brolls = video_plan.get("broll_inserts", ())
    raw_sfxs = video_plan.get("sfx_inserts", ())
    if raw_brolls is not None and not isinstance(raw_brolls, (list, tuple)):
        raise ValueError(f"ERR_INVALID_SEQUENCE: video_plan.broll_inserts phải là list hoặc tuple, nhận: {type(raw_brolls).__name__}")
    if raw_sfxs is not None and not isinstance(raw_sfxs, (list, tuple)):
        raise ValueError(f"ERR_INVALID_SEQUENCE: video_plan.sfx_inserts phải là list hoặc tuple, nhận: {type(raw_sfxs).__name__}")

    total_inserts_count = (len(raw_brolls) if raw_brolls else 0) + (len(raw_sfxs) if raw_sfxs else 0)
    if total_inserts_count > MAX_INSERTS_COUNT:
        raise ValueError(f"ERR_COUNT_OVERFLOW: Tổng số lượng inserts ({total_inserts_count}) vượt quá {MAX_INSERTS_COUNT}")

    if raw_brolls:
        for b_idx, b_item in enumerate(raw_brolls):
            if not isinstance(b_item, Mapping):
                raise ValueError(f"ERR_INVALID_INSERT_ROW: broll_inserts[{b_idx}] phải là Mapping, nhận: {type(b_item).__name__}")
            if "timeline_sec" in b_item or "start_sec" in b_item:
                _validate_safe_duration(b_item.get("timeline_sec", b_item.get("start_sec")), f"broll_inserts[{b_idx}].timeline_sec", allow_zero=True)
            if "duration_sec" in b_item or "duration" in b_item:
                _validate_safe_duration(b_item.get("duration_sec", b_item.get("duration")), f"broll_inserts[{b_idx}].duration_sec", allow_zero=False)

    if raw_sfxs:
        for s_idx, s_item in enumerate(raw_sfxs):
            if not isinstance(s_item, Mapping):
                raise ValueError(f"ERR_INVALID_INSERT_ROW: sfx_inserts[{s_idx}] phải là Mapping, nhận: {type(s_item).__name__}")
            if "timeline_sec" in s_item or "start_sec" in s_item:
                _validate_safe_duration(s_item.get("timeline_sec", s_item.get("start_sec")), f"sfx_inserts[{s_idx}].timeline_sec", allow_zero=True)
            if "duration_sec" in s_item or "duration" in s_item:
                _validate_safe_duration(s_item.get("duration_sec", s_item.get("duration")), f"sfx_inserts[{s_idx}].duration_sec", allow_zero=False)

    # Xây dựng video_plan dict có chủ quyền
    video_plan_dict: Dict[str, Any] = dict(video_plan)
    video_plan_dict["manifest_id"] = root_manifest_id
    video_plan_dict["timeline_segments"] = owned_segments

    # 5. Thẩm định kinetic_plan (Slot 2) tùy chọn
    kinetic_plan_dict: Optional[Dict[str, Any]] = None
    if kinetic_plan is not None:
        if not isinstance(kinetic_plan, Mapping):
            raise ValueError(f"ERR_INVALID_KINETIC_PLAN: kinetic_plan phải là Mapping, nhận: {type(kinetic_plan).__name__}")
        if "manifest_id" in kinetic_plan:
            kp_manifest_id = kinetic_plan["manifest_id"]
            if kp_manifest_id is not None:
                kp_mid_clean = _validate_raw_nonblank_id(kp_manifest_id, "kinetic_plan.manifest_id")
                if kp_mid_clean != root_manifest_id:
                    raise ValueError(f"ERR_MANIFEST_MISMATCH: kinetic_plan manifest_id ({kp_mid_clean!r}) không khớp manifest_id ({root_manifest_id!r})")
        kinetic_plan_dict = dict(kinetic_plan)
        kinetic_plan_dict["manifest_id"] = root_manifest_id

    # 6. Xác định demands chuẩn tắc
    if sidecar is not None:
        if matching_plan_revision is None or type(matching_plan_revision) is not str or not matching_plan_revision.strip():
            raise ValueError("ERR_MISSING_MATCHING_PLAN_REVISION: matching_plan_revision bắt buộc là chuỗi không rỗng khi sidecar được cung cấp.")
        if sidecar.plan_revision != matching_plan_revision:
            raise ValueError(f"ERR_PLAN_REVISION_MISMATCH: sidecar.plan_revision ({sidecar.plan_revision!r}) không khớp với matching_plan_revision ({matching_plan_revision!r})")
        canonical_plan_revision = matching_plan_revision
    else:
        canonical_plan_revision = matching_plan_revision or "provisional_plan_revision"

    # LUÔN LUÔN trích xuất canonical demands (kể cả khi raw_brolls và raw_sfxs rỗng)
    plan_batch: AssetDemandBatch = PlanDemandAdapter.extract(
        video_plan_dict,
        plan_revision=canonical_plan_revision,
    )

    # Thẩm định expected_batch nếu có cung cấp
    if expected_batch is not None:
        validate_demand_batch(expected_batch)
        if expected_batch.plan_revision != canonical_plan_revision:
            raise ValueError(f"ERR_EXPECTED_BATCH_MISMATCH: expected_batch.plan_revision ({expected_batch.plan_revision!r}) không khớp revision kịch bản ({canonical_plan_revision!r}).")
        if expected_batch.manifest_id != root_manifest_id:
            raise ValueError(f"ERR_EXPECTED_BATCH_MISMATCH: expected_batch.manifest_id ({expected_batch.manifest_id!r}) không khớp manifest_id ({root_manifest_id!r}).")
        if len(expected_batch.demands) != len(plan_batch.demands):
            raise ValueError(f"ERR_EXPECTED_BATCH_MISMATCH: Số lượng demands trong expected_batch ({len(expected_batch.demands)}) không khớp kịch bản ({len(plan_batch.demands)}).")
        for exp_d, cur_d in zip(expected_batch.demands, plan_batch.demands):
            _assert_demand_items_equal(exp_d, cur_d, "expected_batch")

        if len(expected_batch.requests) != len(plan_batch.requests):
            raise ValueError(f"ERR_EXPECTED_BATCH_MISMATCH: Số lượng requests trong expected_batch ({len(expected_batch.requests)}) không khớp kịch bản ({len(plan_batch.requests)}).")
        for exp_r, cur_r in zip(expected_batch.requests, plan_batch.requests):
            if exp_r != cur_r:
                raise ValueError(f"ERR_EXPECTED_BATCH_MISMATCH: Request '{exp_r.request_id}' trong expected_batch không khớp request kịch bản.")

    # 7. Hạch toán Tài nguyên Bổ trợ (Assignments) và Lược bỏ (Omissions)
    proposed_assignments: List[Dict[str, Any]] = []
    proposed_omissions: List[Dict[str, Any]] = []
    effective_user_choices: Mapping[str, str] = selected_candidate_ids if selected_candidate_ids is not None else (user_choices or {})

    # Thu thập danh sách demand_id hợp lệ từ plan_batch
    valid_demand_ids = {d.demand_id for d in plan_batch.demands}

    for did, c_val in effective_user_choices.items():
        if not isinstance(did, str) or not did.strip():
            raise ValueError(f"ERR_INVALID_CHOICE_KEY: Key trong user_choices/selected_candidate_ids phải là chuỗi không rỗng, nhận: {did!r}")
        if did not in valid_demand_ids:
            raise ValueError(f"ERR_UNKNOWN_DEMAND_CHOICE: demand_id '{did}' trong lựa chọn không tồn tại trong kịch bản.")
        if c_val is None or type(c_val) is not str or not c_val.strip():
            raise ValueError(f"ERR_INVALID_CHOICE_VALUE: Giá trị lựa chọn cho demand '{did}' phải là chuỗi không rỗng.")

    if sidecar is not None:
        if sidecar.manifest_id is not None:
            sc_mid_clean = _validate_raw_nonblank_id(sidecar.manifest_id, "sidecar.manifest_id")
            if sc_mid_clean != root_manifest_id:
                raise ValueError(f"ERR_MANIFEST_MISMATCH: sidecar.manifest_id ({sc_mid_clean!r}) không khớp manifest_id ({root_manifest_id!r})")

        # Kiểm tra đối soát 1-1 chặt chẽ giữa sidecar.entries và demands của plan
        if len(sidecar.entries) != len(plan_batch.demands):
            raise ValueError(
                f"ERR_SIDECAR_DEMAND_MISMATCH: Số lượng entries trong sidecar ({len(sidecar.entries)}) "
                f"không khớp số lượng demands trong kịch bản ({len(plan_batch.demands)})."
            )

        # Đối chiếu từng trường một (field-for-field) giữa sidecar demand và canonical plan demand
        for entry, p_dem in zip(sidecar.entries, plan_batch.demands):
            if not isinstance(entry, DemandMatchEntry):
                raise ValueError(f"ERR_INVALID_SIDECAR_ENTRY: entry trong sidecar phải là DemandMatchEntry, nhận: {type(entry).__name__}")
            s_dem = entry.demand
            if not isinstance(s_dem, AssetDemandItem):
                raise ValueError(f"ERR_INVALID_DEMAND_ITEM: demand trong sidecar phải là AssetDemandItem, nhận: {type(s_dem).__name__}")

            # Thẩm định hợp lệ và so sánh toàn bộ 12 fields
            _assert_demand_items_equal(s_dem, p_dem, "sidecar")

            # Revalidate match result và candidate attributes
            if p_dem.status == "eligible":
                if entry.result is not None:
                    req = next((r for r in plan_batch.requests if r.request_id == p_dem.demand_id), None)
                    if req is not None:
                        validate_demand_match_result(entry.result, req)
                    for cand in entry.candidates:
                        validate_demand_candidate(cand, expected_category=p_dem.category)
            else:
                if entry.result is not None:
                    raise ValueError(f"ERR_SIDECAR_RESULT_INVALID: Demand không eligible '{p_dem.demand_id}' không được phép có result.")

        # Duyệt 1-1 từng demand trong sidecar
        for entry in sidecar.entries:
            if not isinstance(entry, DemandMatchEntry):
                raise ValueError(f"ERR_INVALID_SIDECAR_ENTRY: entry trong sidecar phải là DemandMatchEntry, nhận: {type(entry).__name__}")
            d = entry.demand
            did = d.demand_id
            slot = "broll" if d.source_slot == "broll_inserts" else "sfx"

            t_sec = _validate_safe_duration(d.timeline_sec, f"demand[{did}].timeline_sec", allow_zero=True)
            d_sec = _validate_safe_duration(d.desired_duration_sec, f"demand[{did}].desired_duration_sec", allow_zero=False)

            # Kiểm tra xem demand này có kết quả khớp không
            if entry.is_matched and entry.candidates:
                user_choice = effective_user_choices.get(did)

                if user_choice == "__omit__":
                    # Người dùng chủ động chọn lược bỏ mục này
                    proposed_omissions.append({
                        "demand_id": did,
                        "slot": slot,
                        "timeline_sec": t_sec,
                        "duration_sec": d_sec,
                        "reason": "Người dùng chủ động lược bỏ mục này",
                        "warning_message": f"Mục [{slot}] tại {t_sec}s đã được người dùng chọn lược bỏ.",
                    })
                    continue

                chosen_cand = None
                if user_choice is not None:
                    # Người dùng chỉ định một asset_id cụ thể
                    matched_user_cand = next((c for c in entry.candidates if getattr(c, "asset_id", None) == user_choice), None)
                    if matched_user_cand is None:
                        raise ValueError(f"ERR_UNKNOWN_CANDIDATE: asset_id '{user_choice}' không thuộc danh sách ứng viên của demand '{did}'.")
                    chosen_cand = matched_user_cand
                else:
                    # Mặc định: Chọn ứng viên HỢP LỆ ĐẦU TIÊN (online, không reconnect, không guidance, không ref-only)
                    eligible_cands = [c for c in entry.candidates if _is_candidate_eligible(c, ref_ids)]
                    if eligible_cands:
                        chosen_cand = eligible_cands[0]
                    else:
                        chosen_cand = entry.candidates[0] if entry.candidates else None

                # Đánh giá tính hợp lệ của ứng viên đã chọn
                is_eligible = _is_candidate_eligible(chosen_cand, ref_ids)

                if is_eligible and chosen_cand is not None:
                    assign_row: Dict[str, Any] = {
                        "demand_id": did,
                        "slot": slot,
                        "timeline_sec": t_sec,
                        "duration_sec": d_sec,
                        "asset_id": chosen_cand.asset_id,
                        "source_kind": chosen_cand.source_kind,
                        "status": "PROPOSED",
                    }
                    if chosen_cand.origin_library_id is not None:
                        assign_row["origin_library_id"] = chosen_cand.origin_library_id
                    elif chosen_cand.source_kind in ("managed", "linked"):
                        raise ValueError(f"ERR_MISSING_ORIGIN_LIBRARY_ID: candidate '{chosen_cand.asset_id}' thiếu origin_library_id bắt buộc.")

                    if getattr(chosen_cand, "source_id", None) is not None:
                        assign_row["source_id"] = chosen_cand.source_id
                    if getattr(chosen_cand, "pack_id", None) is not None:
                        assign_row["pack_id"] = chosen_cand.pack_id

                    proposed_assignments.append(assign_row)
                else:
                    # Không có ứng viên trực tuyến hợp lệ -> Proposed Omission
                    if chosen_cand is not None:
                        if getattr(chosen_cand, "requires_reconnect", False):
                            reason_text = f"Tài nguyên {chosen_cand.asset_id} yêu cầu kết nối lại (requires_reconnect)"
                        elif getattr(chosen_cand, "is_native_guidance", False):
                            reason_text = f"Tài nguyên {chosen_cand.asset_id} là tài liệu hướng dẫn nội bộ (is_native_guidance)"
                        elif getattr(chosen_cand, "availability", None) != "online":
                            reason_text = f"Tài nguyên {chosen_cand.asset_id} đang ngoại tuyến (offline)"
                        elif getattr(chosen_cand, "asset_id", None) in ref_ids:
                            reason_text = f"Tài nguyên {chosen_cand.asset_id} là tài liệu tham khảo (reference_only)"
                        else:
                            reason_text = entry.unmatched_reason or "Không có ứng viên hợp lệ khả dụng"
                    else:
                        reason_text = entry.unmatched_reason or "Không có ứng viên hợp lệ khả dụng"

                    proposed_omissions.append({
                        "demand_id": did,
                        "slot": slot,
                        "timeline_sec": t_sec,
                        "duration_sec": d_sec,
                        "reason": reason_text,
                        "warning_message": f"Không có tài nguyên trực tuyến khả dụng cho [{slot}] tại {t_sec}s: {reason_text}.",
                    })
            else:
                # Nhu cầu không khớp hoặc không eligible
                reason_text = entry.unmatched_reason or f"Nhu cầu không tìm thấy tài nguyên (trạng thái: {d.status})"
                proposed_omissions.append({
                    "demand_id": did,
                    "slot": slot,
                    "timeline_sec": t_sec,
                    "duration_sec": d_sec,
                    "reason": reason_text,
                    "warning_message": f"Lược bỏ tài nguyên [{slot}] tại {t_sec}s: {reason_text}.",
                })

    else:
        # Trường hợp sidecar=None: toàn bộ demands hợp lệ của plan trở thành proposed_omissions với lý do NOT_MATCHED_YET (D02)
        if plan_batch is not None:
            for d in plan_batch.demands:
                did = d.demand_id
                slot = "broll" if d.source_slot == "broll_inserts" else "sfx"
                t_sec = _validate_safe_duration(d.timeline_sec, f"demand[{did}].timeline_sec", allow_zero=True)
                d_sec = _validate_safe_duration(d.desired_duration_sec, f"demand[{did}].desired_duration_sec", allow_zero=False)

                proposed_omissions.append({
                    "demand_id": did,
                    "slot": slot,
                    "timeline_sec": t_sec,
                    "duration_sec": d_sec,
                    "reason": "NOT_MATCHED_YET",
                    "warning_message": f"Chưa tra cứu tài nguyên trong kho media (missing sidecar) cho [{did}] tại {t_sec}s.",
                })

    # 8. Khởi tạo và chuẩn hóa EditPlanProposal qua hợp đồng cốt lõi
    raw_proposal = EditPlanProposal(
        proposal_id=pid,
        manifest_id=root_manifest_id,
        creative_context=c_ctx,
        source_binding=source_binding,
        video_plan=video_plan_dict,
        kinetic_plan=kinetic_plan_dict,
        proposed_asset_assignments=tuple(proposed_assignments),
        proposed_omissions=tuple(proposed_omissions),
    )

    return normalize_edit_plan_proposal(raw_proposal)
