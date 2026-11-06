"""src/core/geometry/plan_geometry_contracts.py
Hợp đồng hình học kịch bản dựng toàn bản thuần Python (G06-06C2d-R1).

Quy tắc kiến trúc:
1. Pure Python standard library (dataclasses, fractions, typing).
2. Tái sử dụng các hợp đồng thời gian và hình học khung hình:
   - RationalFPS, TimeRangeFrames từ src/core/contracts/time_contracts.py
   - QuantizedCFRSpan, validate_fps_instance, validate_source_range_instance từ src/core/geometry/frame_geometry_contracts.py
3. Không import UI (PySide6), filesystem IO, network, QSettings, hoặc Resolve API.
4. Bất biến hoàn toàn (frozen dataclass, defensive copies, deep rehydration).
5. Phân định rõ ràng 3 trạng thái của SourceFrameEvidence:
   - VERIFIED_CFR: Khung hình chuẩn CFR đã kiểm chứng, bắt buộc có source_fps và total_frames.
   - HEADER_REPORTED_ONLY: Chỉ do header container báo cáo, chưa đủ căn cứ toán học dựng.
   - VERIFIED_VFR: Video biến thiên tốc độ khung hình (VFR), bị chặn đối với lát cắt CFR.
6. Xác thực chuỗi UTF-8 nghiêm ngặt: loại trừ ký tự NUL, lone surrogates, giới hạn kích thước trần 512KiB.
7. Ràng buộc nhất quán toán học GEOMETRY_VERIFIED:
   - Thứ tự phân đoạn liên tục 0..N-1, cursor bắt đầu từ 0, không khoảng trống/chồng lấn.
   - Tổng số khung hình bằng đúng tổng độ dài các bản ghi và khớp final cursor.
   - Empty verified (all-cut) yêu cầu đúng 0 frames và Fraction(0, 1) giây.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Mapping, Optional, Sequence, Tuple, Union

from src.core.contracts.time_contracts import RationalFPS, TimeRangeFrames
from src.core.geometry.frame_geometry_contracts import (
    QuantizedCFRSpan,
    validate_fps_instance,
    validate_fraction_instance,
)

VALID_EVIDENCE_STATUSES = frozenset({"VERIFIED_CFR", "HEADER_REPORTED_ONLY", "VERIFIED_VFR"})
VALID_SEGMENT_ORIGINS = frozenset({"timeline_segment", "global_hook"})
VALID_ISSUE_SEVERITIES = frozenset({"BLOCKING", "WARNING"})
VALID_ASSEMBLY_STATUSES = frozenset({"GEOMETRY_VERIFIED", "GEOMETRY_UNRESOLVED", "GEOMETRY_BLOCKED"})

MAX_STRING_BYTES = 512 * 1024       # 512 KiB per string ceiling
MAX_ASSEMBLED_SEGMENTS = 257        # 256 cuts + 1 hook
MAX_ISSUES = 1024
MAX_REVIEW_ITEMS = 512


def _validate_clean_str(val: Any, field_name: str, allow_blank: bool = False) -> str:
    """Xác thực chuỗi UTF-8 hợp lệ, không chứa ký tự NUL, không lone surrogates, giới hạn 512KiB."""
    if type(val) is not str:
        raise ValueError(f"ERR_INVALID_TYPE: {field_name} phải là kiểu str, nhận: {type(val).__name__ if val is not None else 'None'}")
    if "\0" in val:
        raise ValueError(f"ERR_INVALID_STRING: {field_name} chứa ký tự NUL không hợp lệ")
    try:
        encoded = val.encode("utf-8")
    except UnicodeEncodeError as e:
        raise ValueError(f"ERR_INVALID_UTF8: {field_name} không thể mã hóa UTF-8 (lone surrogate): {e}") from e
    if len(encoded) > MAX_STRING_BYTES:
        raise ValueError(f"ERR_STRING_OVERFLOW: {field_name} có kích thước {len(encoded)} bytes, vượt quá giới hạn {MAX_STRING_BYTES}")
    if not allow_blank and not val.strip():
        raise ValueError(f"ERR_BLANK_FIELD: {field_name} không được để trống hoặc chỉ chứa khoảng trắng")
    return val


@dataclass(frozen=True)
class SourceFrameEvidence:
    """Bằng chứng khung hình nguồn đã được kiểm chứng (Conditional Trusted Caller Input).
    
    Quy định:
    - manifest_id: Mã định danh bảng kê nguồn (khớp với proposal.manifest_id).
    - clip_id: Mã định danh clip nguồn (khớp với clip_id trong source_binding).
    - source_fps: Tốc độ khung hình nguồn (RationalFPS) hoặc None.
    - total_frames: Tổng số khung hình giải mã (strict int > 0) hoặc None.
    - evidence_status: 'VERIFIED_CFR' | 'HEADER_REPORTED_ONLY' | 'VERIFIED_VFR'.
    - VERIFIED_CFR bắt buộc phải có source_fps và total_frames hợp lệ.
    """
    manifest_id: str
    clip_id: str
    source_fps: Optional[RationalFPS]
    total_frames: Optional[int]
    evidence_status: str

    def __post_init__(self) -> None:
        # 1. Validate manifest_id và clip_id
        m_id = _validate_clean_str(self.manifest_id, "manifest_id", allow_blank=False)
        c_id = _validate_clean_str(self.clip_id, "clip_id", allow_blank=False)
        object.__setattr__(self, "manifest_id", m_id)
        object.__setattr__(self, "clip_id", c_id)

        # 2. Validate evidence_status
        status = _validate_clean_str(self.evidence_status, "evidence_status", allow_blank=False)
        if status not in VALID_EVIDENCE_STATUSES:
            raise ValueError(
                f"ERR_INVALID_EVIDENCE_STATUS: evidence_status phải thuộc {sorted(VALID_EVIDENCE_STATUSES)}, "
                f"nhận: {status!r}"
            )
        object.__setattr__(self, "evidence_status", status)

        # 3. Validate source_fps & total_frames theo từng trạng thái
        if status == "VERIFIED_CFR":
            if self.source_fps is None:
                raise ValueError("ERR_MISSING_SOURCE_EVIDENCE: VERIFIED_CFR bắt buộc phải có source_fps")
            owned_fps = validate_fps_instance(self.source_fps, "ERR_INVALID_SOURCE_FPS")
            object.__setattr__(self, "source_fps", owned_fps)

            if self.total_frames is None:
                raise ValueError("ERR_MISSING_SOURCE_EVIDENCE: VERIFIED_CFR bắt buộc phải có total_frames")
            if type(self.total_frames) is not int or type(self.total_frames) is bool:
                raise ValueError(f"ERR_INVALID_TOTAL_FRAMES: total_frames phải là int (không phải bool), nhận: {type(self.total_frames).__name__}")
            if self.total_frames <= 0:
                raise ValueError(f"ERR_INVALID_TOTAL_FRAMES: total_frames phải dương (> 0), nhận: {self.total_frames}")
        else:
            if self.source_fps is not None:
                owned_fps = validate_fps_instance(self.source_fps, "ERR_INVALID_SOURCE_FPS")
                object.__setattr__(self, "source_fps", owned_fps)
            if self.total_frames is not None:
                if type(self.total_frames) is not int or type(self.total_frames) is bool:
                    raise ValueError(f"ERR_INVALID_TOTAL_FRAMES: total_frames phải là int (không phải bool), nhận: {type(self.total_frames).__name__}")
                if self.total_frames <= 0:
                    raise ValueError(f"ERR_INVALID_TOTAL_FRAMES: total_frames phải dương (> 0), nhận: {self.total_frames}")


def reconstruct_source_frame_evidence(ev: Any) -> SourceFrameEvidence:
    """Tái kiểm tra và tái tạo bản sao sở hữu độc lập của SourceFrameEvidence.
    
    Phòng thủ (G06-06C2d-R1):
    - Nominal type: isinstance(ev, SourceFrameEvidence).
    - Kiểm tra sự hiện diện đầy đủ của cả 5 thuộc tính bắt buộc (không bị xóa / thiếu).
    - Kiểm tra kiểu dữ liệu thô trước khi truy cập hoặc tính toán.
    - Tái tạo lại qua constructor SourceFrameEvidence để bảo đảm tính toàn vẹn bất biến.
    """
    if not isinstance(ev, SourceFrameEvidence):
        raise ValueError(f"ERR_INVALID_EVIDENCE: Giá trị bằng chứng phải là SourceFrameEvidence, nhận: {type(ev).__name__}")

    # Kiểm tra sự tồn tại của đầy đủ 5 trường bắt buộc
    for attr in ("manifest_id", "clip_id", "source_fps", "total_frames", "evidence_status"):
        if not hasattr(ev, attr):
            raise ValueError(f"ERR_MALFORMED_EVIDENCE: SourceFrameEvidence thiếu thuộc tính bắt buộc {attr!r}")

    raw_m_id = getattr(ev, "manifest_id")
    raw_c_id = getattr(ev, "clip_id")
    raw_status = getattr(ev, "evidence_status")
    raw_fps = getattr(ev, "source_fps")
    raw_total = getattr(ev, "total_frames")

    if type(raw_status) is not str:
        raise ValueError(f"ERR_INVALID_EVIDENCE_STATUS: evidence_status phải là str, nhận: {type(raw_status).__name__}")
    if raw_status not in VALID_EVIDENCE_STATUSES:
        raise ValueError(f"ERR_INVALID_EVIDENCE_STATUS: evidence_status phải thuộc {sorted(VALID_EVIDENCE_STATUSES)}, nhận: {raw_status!r}")

    owned_fps = None
    if raw_fps is not None:
        owned_fps = validate_fps_instance(raw_fps, "ERR_INVALID_SOURCE_FPS")

    return SourceFrameEvidence(
        manifest_id=raw_m_id,
        clip_id=raw_c_id,
        source_fps=owned_fps,
        total_frames=raw_total,
        evidence_status=raw_status,
    )


@dataclass(frozen=True)
class PlanTimelineSegmentGeometry:
    """Hình học khung hình của một phân đoạn video được phát ra trên timeline dựng.
    
    Quy định:
    - segment_index: Thứ tự phân đoạn xuất xưởng trên timeline (strict int >= 0).
    - origin: 'timeline_segment' (phân đoạn A-Roll chính) hoặc 'global_hook' (đoạn hook intro).
    - source_segment_index: Chỉ số dòng phân đoạn nguồn gốc trong proposal.video_plan (None khi origin == 'global_hook').
    - clip_id: Mã clip nguồn.
    - quantized_span: Đối tượng QuantizedCFRSpan sở hữu bản sao sâu.
    - timeline_range: Khoảng khung hình trên timeline [rec_in_f, rec_out_f) tại timeline_fps.
    - Duration và applied_speed là properties suy diễn, không tạo giá trị trùng lặp mâu thuẫn.
    """
    segment_index: int
    origin: str
    source_segment_index: Optional[int]
    clip_id: str
    quantized_span: QuantizedCFRSpan
    timeline_range: TimeRangeFrames

    def __post_init__(self) -> None:
        for seg_attr in (
            "segment_index",
            "origin",
            "source_segment_index",
            "clip_id",
            "quantized_span",
            "timeline_range",
        ):
            if not hasattr(self, seg_attr):
                raise ValueError(f"ERR_MALFORMED_SEGMENT: PlanTimelineSegmentGeometry thiếu thuộc tính bắt buộc {seg_attr!r}")

        if type(self.segment_index) is not int or type(self.segment_index) is bool or self.segment_index < 0:
            raise ValueError(f"ERR_INVALID_SEGMENT_INDEX: segment_index phải là int không âm (>= 0), nhận: {self.segment_index!r}")

        if type(self.origin) is not str or self.origin not in VALID_SEGMENT_ORIGINS:
            raise ValueError(f"ERR_INVALID_ORIGIN: origin phải thuộc {sorted(VALID_SEGMENT_ORIGINS)}, nhận: {self.origin!r}")

        if self.origin == "timeline_segment":
            if type(self.source_segment_index) is not int or type(self.source_segment_index) is bool or self.source_segment_index < 0:
                raise ValueError(f"ERR_INVALID_SOURCE_SEGMENT_INDEX: source_segment_index cho timeline_segment phải là int không âm, nhận: {self.source_segment_index!r}")
        elif self.origin == "global_hook":
            if self.source_segment_index is not None:
                raise ValueError(f"ERR_INVALID_SOURCE_SEGMENT_INDEX: source_segment_index cho global_hook bắt buộc phải là None, nhận: {self.source_segment_index!r}")

        c_id = _validate_clean_str(self.clip_id, "clip_id", allow_blank=False)
        object.__setattr__(self, "clip_id", c_id)

        if not isinstance(self.quantized_span, QuantizedCFRSpan):
            raise ValueError(f"ERR_INVALID_SPAN: quantized_span phải là QuantizedCFRSpan, nhận: {type(self.quantized_span).__name__}")

        # Kiểm tra thuộc tính bắt buộc của QuantizedCFRSpan
        for span_attr in (
            "source_fps",
            "timeline_fps",
            "source_range",
            "total_source_frames",
            "record_duration_frames",
            "applied_speed",
        ):
            if not hasattr(self.quantized_span, span_attr):
                raise ValueError(f"ERR_MALFORMED_SPAN: QuantizedCFRSpan thiếu thuộc tính bắt buộc {span_attr!r}")

        # Deep rehydration cho QuantizedCFRSpan để chống aliasing
        sr = getattr(self.quantized_span, "source_range", None)
        if not isinstance(sr, TimeRangeFrames):
            raise ValueError("ERR_INVALID_SPAN: quantized_span.source_range phải là TimeRangeFrames")
        for tr_attr in ("start_frame", "end_frame"):
            if not hasattr(sr, tr_attr):
                raise ValueError(f"ERR_MALFORMED_TIME_RANGE: TimeRangeFrames thiếu thuộc tính bắt buộc {tr_attr!r}")
        owned_sr = TimeRangeFrames(
            getattr(sr, "start_frame", None),
            getattr(sr, "end_frame", None),
        )
        owned_span = QuantizedCFRSpan(
            source_fps=validate_fps_instance(self.quantized_span.source_fps, "ERR_INVALID_SOURCE_FPS"),
            timeline_fps=validate_fps_instance(self.quantized_span.timeline_fps, "ERR_INVALID_TIMELINE_FPS"),
            source_range=owned_sr,
            total_source_frames=self.quantized_span.total_source_frames,
            record_duration_frames=self.quantized_span.record_duration_frames,
            applied_speed=self.quantized_span.applied_speed,
        )
        object.__setattr__(self, "quantized_span", owned_span)

        if not isinstance(self.timeline_range, TimeRangeFrames):
            raise ValueError(f"ERR_INVALID_TIMELINE_RANGE: timeline_range phải là TimeRangeFrames, nhận: {type(self.timeline_range).__name__}")
        for tr_attr in ("start_frame", "end_frame"):
            if not hasattr(self.timeline_range, tr_attr):
                raise ValueError(f"ERR_MALFORMED_TIME_RANGE: TimeRangeFrames thiếu thuộc tính bắt buộc {tr_attr!r}")
        tl_start = getattr(self.timeline_range, "start_frame", None)
        tl_end = getattr(self.timeline_range, "end_frame", None)
        if type(tl_start) is not int or type(tl_start) is bool or tl_start < 0:
            raise ValueError(f"ERR_INVALID_TIMELINE_RANGE: timeline_range.start_frame phải là int không âm, nhận: {tl_start!r}")
        if type(tl_end) is not int or type(tl_end) is bool or tl_end <= tl_start:
            raise ValueError(f"ERR_INVALID_TIMELINE_RANGE: timeline_range.end_frame phải > start_frame, nhận: [{tl_start}, {tl_end})")
        owned_range = TimeRangeFrames(tl_start, tl_end)
        object.__setattr__(self, "timeline_range", owned_range)

        # Cross check consistency giữa timeline_range và quantized_span
        if owned_range.duration_frames != owned_span.record_duration_frames:
            raise ValueError(
                f"ERR_TIMELINE_SPAN_MISMATCH: timeline_range duration ({owned_range.duration_frames}) "
                f"không khớp quantized_span record_duration_frames ({owned_span.record_duration_frames})"
            )

    @property
    def timeline_duration_frames(self) -> int:
        return self.timeline_range.duration_frames

    @property
    def duration_frames(self) -> int:
        return self.timeline_range.duration_frames

    @property
    def applied_speed(self) -> Fraction:
        return self.quantized_span.applied_speed


@dataclass(frozen=True)
class PlanGeometryIssue:
    """Vấn đề hoặc lỗi phát hiện trong quá trình thẩm định hình học kịch bản."""
    severity: str                         # "BLOCKING" | "WARNING"
    issue_code: str                       # ERR_MISSING_SOURCE_EVIDENCE, ERR_VFR_UNSUPPORTED, ...
    clip_id: Optional[str] = None
    segment_index: Optional[int] = None
    message: str = ""

    def __post_init__(self) -> None:
        for req_attr in ("severity", "issue_code", "message"):
            if not hasattr(self, req_attr):
                raise ValueError(f"ERR_MALFORMED_ISSUE: PlanGeometryIssue thiếu thuộc tính bắt buộc {req_attr!r}")

        if type(self.severity) is not str or self.severity not in VALID_ISSUE_SEVERITIES:
            raise ValueError(f"ERR_INVALID_SEVERITY: severity phải thuộc {sorted(VALID_ISSUE_SEVERITIES)}, nhận: {self.severity!r}")
        code = _validate_clean_str(self.issue_code, "issue_code", allow_blank=False)
        object.__setattr__(self, "issue_code", code)

        if self.clip_id is not None:
            c_id = _validate_clean_str(self.clip_id, "clip_id", allow_blank=False)
            object.__setattr__(self, "clip_id", c_id)

        if self.segment_index is not None:
            if type(self.segment_index) is not int or type(self.segment_index) is bool or self.segment_index < 0:
                raise ValueError(f"ERR_INVALID_SEGMENT_INDEX: segment_index phải là int không âm, nhận: {self.segment_index!r}")

        msg = _validate_clean_str(self.message, "message", allow_blank=False)
        object.__setattr__(self, "message", msg)


@dataclass(frozen=True)
class UnresolvedReviewItem:
    """Mục cần sự rà soát hoặc xác nhận của người dùng (D01 / D02)."""
    category: str                         # "SEMANTIC_MUST_KEEP" | "SEMANTIC_MUST_NOT" | "HOOK_PLACEMENT" | "SUPPLEMENTAL_OMISSION"
    item_id: str
    description: str

    def __post_init__(self) -> None:
        for req_attr in ("category", "item_id", "description"):
            if not hasattr(self, req_attr):
                raise ValueError(f"ERR_MALFORMED_REVIEW_ITEM: UnresolvedReviewItem thiếu thuộc tính bắt buộc {req_attr!r}")

        cat = _validate_clean_str(self.category, "category", allow_blank=False)
        i_id = _validate_clean_str(self.item_id, "item_id", allow_blank=False)
        desc = _validate_clean_str(self.description, "description", allow_blank=False)
        object.__setattr__(self, "category", cat)
        object.__setattr__(self, "item_id", i_id)
        object.__setattr__(self, "description", desc)


@dataclass(frozen=True)
class PlanGeometryAssemblyResult:
    """Kết quả thẩm định và lắp ráp hình học kịch bản toàn bản."""
    status: str                           # "GEOMETRY_VERIFIED" | "GEOMETRY_UNRESOLVED" | "GEOMETRY_BLOCKED"
    proposal_id: str
    proposal_revision: str
    timeline_fps: RationalFPS
    assembled_segments: Tuple[PlanTimelineSegmentGeometry, ...]
    total_timeline_frames: Optional[int]
    total_timeline_duration_sec: Optional[Fraction]
    issues: Tuple[PlanGeometryIssue, ...]
    unresolved_reviews: Tuple[UnresolvedReviewItem, ...]

    def __post_init__(self) -> None:
        for res_attr in (
            "status",
            "proposal_id",
            "proposal_revision",
            "timeline_fps",
            "assembled_segments",
            "total_timeline_frames",
            "total_timeline_duration_sec",
            "issues",
            "unresolved_reviews",
        ):
            if not hasattr(self, res_attr):
                raise ValueError(f"ERR_MALFORMED_RESULT: PlanGeometryAssemblyResult thiếu thuộc tính bắt buộc {res_attr!r}")

        if type(self.status) is not str or self.status not in VALID_ASSEMBLY_STATUSES:
            raise ValueError(f"ERR_INVALID_ASSEMBLY_STATUS: status phải thuộc {sorted(VALID_ASSEMBLY_STATUSES)}, nhận: {self.status!r}")

        p_id = _validate_clean_str(self.proposal_id, "proposal_id", allow_blank=False)
        rev = _validate_clean_str(self.proposal_revision, "proposal_revision", allow_blank=False)
        object.__setattr__(self, "proposal_id", p_id)
        object.__setattr__(self, "proposal_revision", rev)

        owned_tl_fps = validate_fps_instance(self.timeline_fps, "ERR_INVALID_TIMELINE_FPS")
        object.__setattr__(self, "timeline_fps", owned_tl_fps)

        # 1. Pre-check và deep rehydration cho assembled_segments
        if not isinstance(self.assembled_segments, (tuple, list)):
            raise ValueError(f"ERR_INVALID_SEQUENCE: assembled_segments phải là tuple hoặc list, nhận: {type(self.assembled_segments).__name__}")
        if len(self.assembled_segments) > MAX_ASSEMBLED_SEGMENTS:
            raise ValueError(f"ERR_COUNT_OVERFLOW: assembled_segments có {len(self.assembled_segments)} phần tử, vượt quá giới hạn {MAX_ASSEMBLED_SEGMENTS}")

        owned_segments = []
        for idx, seg in enumerate(self.assembled_segments):
            if not isinstance(seg, PlanTimelineSegmentGeometry):
                raise ValueError(f"ERR_INVALID_SEGMENT: assembled_segments[{idx}] phải là PlanTimelineSegmentGeometry, nhận: {type(seg).__name__}")
            for seg_attr in (
                "segment_index",
                "origin",
                "source_segment_index",
                "clip_id",
                "quantized_span",
                "timeline_range",
            ):
                if not hasattr(seg, seg_attr):
                    raise ValueError(f"ERR_MALFORMED_SEGMENT: assembled_segments[{idx}] thiếu thuộc tính bắt buộc {seg_attr!r}")

            # Tái tạo bản sao sâu độc lập để chống aliasing
            rehydrated_seg = PlanTimelineSegmentGeometry(
                segment_index=seg.segment_index,
                origin=seg.origin,
                source_segment_index=seg.source_segment_index,
                clip_id=seg.clip_id,
                quantized_span=seg.quantized_span,
                timeline_range=seg.timeline_range,
            )
            owned_segments.append(rehydrated_seg)
        object.__setattr__(self, "assembled_segments", tuple(owned_segments))

        # 2. Pre-check và deep rehydration cho issues
        if not isinstance(self.issues, (tuple, list)):
            raise ValueError(f"ERR_INVALID_SEQUENCE: issues phải là tuple hoặc list, nhận: {type(self.issues).__name__}")
        if len(self.issues) > MAX_ISSUES:
            raise ValueError(f"ERR_COUNT_OVERFLOW: issues có {len(self.issues)} phần tử, vượt quá giới hạn {MAX_ISSUES}")

        owned_issues = []
        for idx, iss in enumerate(self.issues):
            if not isinstance(iss, PlanGeometryIssue):
                raise ValueError(f"ERR_INVALID_ISSUE: issues[{idx}] phải là PlanGeometryIssue, nhận: {type(iss).__name__}")
            for iss_attr in ("severity", "issue_code", "message"):
                if not hasattr(iss, iss_attr):
                    raise ValueError(f"ERR_MALFORMED_ISSUE: issues[{idx}] thiếu thuộc tính bắt buộc {iss_attr!r}")

            rehydrated_iss = PlanGeometryIssue(
                severity=iss.severity,
                issue_code=iss.issue_code,
                clip_id=getattr(iss, "clip_id", None),
                segment_index=getattr(iss, "segment_index", None),
                message=iss.message,
            )
            owned_issues.append(rehydrated_iss)
        object.__setattr__(self, "issues", tuple(owned_issues))

        # 3. Pre-check và deep rehydration cho unresolved_reviews
        if not isinstance(self.unresolved_reviews, (tuple, list)):
            raise ValueError(f"ERR_INVALID_SEQUENCE: unresolved_reviews phải là tuple hoặc list, nhận: {type(self.unresolved_reviews).__name__}")
        if len(self.unresolved_reviews) > MAX_REVIEW_ITEMS:
            raise ValueError(f"ERR_COUNT_OVERFLOW: unresolved_reviews có {len(self.unresolved_reviews)} phần tử, vượt quá giới hạn {MAX_REVIEW_ITEMS}")

        owned_reviews = []
        for idx, rev_item in enumerate(self.unresolved_reviews):
            if not isinstance(rev_item, UnresolvedReviewItem):
                raise ValueError(f"ERR_INVALID_REVIEW_ITEM: unresolved_reviews[{idx}] phải là UnresolvedReviewItem, nhận: {type(rev_item).__name__}")
            for rev_attr in ("category", "item_id", "description"):
                if not hasattr(rev_item, rev_attr):
                    raise ValueError(f"ERR_MALFORMED_REVIEW_ITEM: unresolved_reviews[{idx}] thiếu thuộc tính bắt buộc {rev_attr!r}")

            rehydrated_rev = UnresolvedReviewItem(
                category=rev_item.category,
                item_id=rev_item.item_id,
                description=rev_item.description,
            )
            owned_reviews.append(rehydrated_rev)
        object.__setattr__(self, "unresolved_reviews", tuple(owned_reviews))

        # 4. Kiểm tra tính nhất quán trạng thái (Status Consistency)
        has_blocking = any(iss.severity == "BLOCKING" for iss in owned_issues)
        has_warning = any(iss.severity == "WARNING" for iss in owned_issues)

        if has_blocking:
            if self.status != "GEOMETRY_BLOCKED":
                raise ValueError(f"ERR_STATUS_INCONSISTENCY: issues có lỗi BLOCKING nhưng status là {self.status!r} (yêu cầu GEOMETRY_BLOCKED)")
        elif has_warning:
            if self.status == "GEOMETRY_VERIFIED":
                raise ValueError("ERR_STATUS_INCONSISTENCY: issues có cảnh báo WARNING nhưng status là GEOMETRY_VERIFIED")

        # 5. Invariant checks theo status
        if self.status in ("GEOMETRY_BLOCKED", "GEOMETRY_UNRESOLVED"):
            if len(owned_segments) != 0:
                raise ValueError(f"ERR_INVARIANT_VIOLATION: Khi status là {self.status}, assembled_segments bắt buộc phải rỗng ()")
            if self.total_timeline_frames is not None:
                raise ValueError(f"ERR_INVARIANT_VIOLATION: Khi status là {self.status}, total_timeline_frames bắt buộc phải là None")
            if self.total_timeline_duration_sec is not None:
                raise ValueError(f"ERR_INVARIANT_VIOLATION: Khi status là {self.status}, total_timeline_duration_sec bắt buộc phải là None")
        elif self.status == "GEOMETRY_VERIFIED":
            if self.total_timeline_frames is None:
                raise ValueError("ERR_INVARIANT_VIOLATION: Khi status là GEOMETRY_VERIFIED, total_timeline_frames không được là None")
            if type(self.total_timeline_frames) is not int or type(self.total_timeline_frames) is bool or self.total_timeline_frames < 0:
                raise ValueError(f"ERR_INVALID_TOTAL_FRAMES: total_timeline_frames phải là int không âm (>= 0), nhận: {self.total_timeline_frames!r}")

            if self.total_timeline_duration_sec is None:
                raise ValueError("ERR_INVARIANT_VIOLATION: Khi status là GEOMETRY_VERIFIED, total_timeline_duration_sec không được là None")
            if not isinstance(self.total_timeline_duration_sec, Fraction):
                raise ValueError(f"ERR_INVALID_TIMELINE_DURATION: total_timeline_duration_sec phải là Fraction, nhận: {type(self.total_timeline_duration_sec).__name__}")
            dur_num = getattr(self.total_timeline_duration_sec, "numerator", None)
            dur_den = getattr(self.total_timeline_duration_sec, "denominator", None)
            if type(dur_num) is not int or type(dur_num) is bool:
                raise ValueError("ERR_INVALID_TIMELINE_DURATION: numerator phải là int (không phải bool)")
            if type(dur_den) is not int or type(dur_den) is bool or dur_den <= 0:
                raise ValueError("ERR_INVALID_TIMELINE_DURATION: denominator phải là int > 0")
            if dur_num < 0:
                raise ValueError(f"ERR_INVALID_TIMELINE_DURATION: total_timeline_duration_sec không được âm (< 0), nhận: {dur_num}/{dur_den}")

            owned_dur_sec = Fraction(dur_num, dur_den)
            object.__setattr__(self, "total_timeline_duration_sec", owned_dur_sec)

            expected_dur_sec = Fraction(self.total_timeline_frames) / owned_tl_fps.as_fraction()
            if owned_dur_sec != expected_dur_sec:
                raise ValueError(
                    f"ERR_TIMELINE_DURATION_MISMATCH: total_timeline_duration_sec ({owned_dur_sec}) "
                    f"không khớp Fraction(total_timeline_frames) / timeline_fps ({expected_dur_sec})"
                )

            # Ràng buộc hình học chi tiết của GEOMETRY_VERIFIED
            if len(owned_segments) == 0:
                # All-cut timeline: yêu cầu đúng 0 frames và 0/1 giây
                if self.total_timeline_frames != 0:
                    raise ValueError(f"ERR_TOTAL_FRAMES_MISMATCH: Khi assembled_segments rỗng (all-cut), total_timeline_frames phải bằng 0, nhận: {self.total_timeline_frames}")
                if owned_dur_sec != Fraction(0, 1):
                    raise ValueError(f"ERR_TIMELINE_DURATION_MISMATCH: Khi assembled_segments rỗng (all-cut), total_timeline_duration_sec phải bằng Fraction(0, 1), nhận: {owned_dur_sec}")
            else:
                # Kiểm tra global_hook chỉ xuất hiện tối đa 1 lần và bắt buộc phải nằm ở vị trí đầu tiên (index 0)
                hook_count = sum(1 for s in owned_segments if s.origin == "global_hook")
                if hook_count > 1:
                    raise ValueError(f"ERR_HOOK_PLACEMENT_VIOLATION: global_hook xuất hiện {hook_count} lần trên timeline (chỉ cho phép tối đa 1 lần)")
                if hook_count == 1 and owned_segments[0].origin != "global_hook":
                    raise ValueError("ERR_HOOK_PLACEMENT_VIOLATION: global_hook bắt buộc phải xuất hiện ở vị trí đầu tiên (index 0) của timeline")

                # Kiểm tra phân đoạn đầu tiên bắt đầu tại cursor 0
                first_seg = owned_segments[0]
                if first_seg.timeline_range.start_frame != 0:
                    raise ValueError(f"ERR_CURSOR_START_NONZERO: Phân đoạn đầu tiên phải bắt đầu tại frame 0, nhận: {first_seg.timeline_range.start_frame}")

                # Kiểm tra thứ tự index 0..N-1, timeline_fps khớp, và tính liên tục không đè/không hở
                for idx, seg in enumerate(owned_segments):
                    if seg.segment_index != idx:
                        raise ValueError(f"ERR_SEGMENT_INDEX_MISMATCH: Phân đoạn tại vị trí {idx} có segment_index={seg.segment_index} (yêu cầu 0..N-1 liên tục)")
                    if seg.quantized_span.timeline_fps != owned_tl_fps:
                        raise ValueError(f"ERR_FPS_MISMATCH: Phân đoạn {idx} có timeline_fps={seg.quantized_span.timeline_fps} không khớp result timeline_fps={owned_tl_fps}")
                    if idx > 0:
                        prev_end = owned_segments[idx - 1].timeline_range.end_frame
                        cur_start = seg.timeline_range.start_frame
                        if cur_start != prev_end:
                            raise ValueError(f"ERR_TIMELINE_DISCONTINUITY: Khoảng cách hoặc đè lắp giữa phân đoạn {idx - 1} (end={prev_end}) và {idx} (start={cur_start})")

                # Kiểm tra tổng số frame khớp tổng độ dài phân đoạn và final cursor
                expected_total_frames = sum(s.timeline_duration_frames for s in owned_segments)
                final_cursor = owned_segments[-1].timeline_range.end_frame
                if expected_total_frames != final_cursor:
                    raise ValueError(f"ERR_FINAL_CURSOR_MISMATCH: final cursor ({final_cursor}) không khớp tổng độ dài các phân đoạn ({expected_total_frames})")
                if self.total_timeline_frames != expected_total_frames:
                    raise ValueError(f"ERR_TOTAL_FRAMES_MISMATCH: total_timeline_frames ({self.total_timeline_frames}) không khớp tổng frame các phân đoạn ({expected_total_frames})")
