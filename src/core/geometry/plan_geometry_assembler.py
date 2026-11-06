"""src/core/geometry/plan_geometry_assembler.py
Bộ thẩm định và lắp ráp hình học kịch bản toàn bản thuần Python (G06-06C2d-R1).

Quy tắc kiến trúc:
1. Pure Python standard library (math, fractions, typing).
2. Tái sử dụng duy nhất một bộ tính toán lượng tử hóa Formula B:
   - quantize_cfr_video_span từ src/core/geometry/frame_geometry_calculator.py
3. Tái sử dụng hợp đồng đề xuất và mã băm chuẩn tắc:
   - normalize_edit_plan_proposal, compute_proposal_revision từ src/core/contracts/edit_plan_proposal_contracts.py
4. Không import UI (PySide6), filesystem IO, network, QSettings, hoặc Resolve API.
5. Kiểm tra và sở hữu độc lập toàn bộ bảng evidence_table ở ranh giới đầu vào:
   - Kiểm tra kiểu dữ liệu, các thuộc tính danh nghĩa, và giới hạn tối đa 4096 mục.
   - Bắt buộc clip_id phải thuộc proposal.source_binding.clips (từ chối clip lạ bằng ERR_UNKNOWN_CLIP_ID).
   - Chỉ áp dụng quantize_cfr_video_span với is_cfr=True khi clip nguồn có VERIFIED_CFR.
   - HEADER_REPORTED_ONLY hoặc thiếu bằng chứng dẫn đến GEOMETRY_UNRESOLVED (không lắp ráp executable).
   - VERIFIED_VFR hoặc lỗi ranh giới dẫn đến GEOMETRY_BLOCKED.
6. Thẩm định chặt chẽ toàn diện global_hook trước khi tra cứu bằng chứng:
   - Kiểm tra cấu trúc mapping, các trường bắt buộc, kiểu số thô (không bool/string/ép kiểu).
   - clip_id của hook phải thuộc source_binding.clips và tuyệt đối không thuộc reference_items.
   - Chỉ khi hook có cấu trúc hợp lệ mà thiếu placement_mode mới giữ UNRESOLVED (AMBIGUITY).
7. Nguyên tắc lắp ráp toàn bản (All-or-Nothing Assembly):
   - Thứ tự ưu tiên: GEOMETRY_BLOCKED > GEOMETRY_UNRESOLVED > GEOMETRY_VERIFIED.
   - Nếu có bất kỳ lỗi/chưa giải quyết: assembled_segments bắt buộc phải rỗng (),
     cả total_timeline_frames và total_timeline_duration_sec đều là None (cấm trả về prefix dở dang).
   - Toàn bộ phân đoạn bị cut (all-cut) mà hợp lệ hình học: trả về GEOMETRY_VERIFIED với 0 frames và Fraction(0, 1)s.
"""
from __future__ import annotations

from fractions import Fraction
import math
from typing import Any, Mapping, Optional, Sequence, Union

from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    compute_proposal_revision,
    normalize_edit_plan_proposal,
)
from src.core.contracts.time_contracts import RationalFPS, TimeRangeFrames
from src.core.geometry.frame_geometry_calculator import quantize_cfr_video_span
from src.core.geometry.frame_geometry_contracts import validate_fps_instance
from src.core.geometry.plan_geometry_contracts import (
    PlanGeometryAssemblyResult,
    PlanGeometryIssue,
    PlanTimelineSegmentGeometry,
    SourceFrameEvidence,
    UnresolvedReviewItem,
    _validate_clean_str,
    reconstruct_source_frame_evidence,
)

MAX_EVIDENCE_TABLE_ITEMS = 4096
VALID_ACTIONS = frozenset({"keep", "speedup", "cut"})
VALID_HOOK_MODES = frozenset({"intro_cut", "highlight_ref"})


def _to_exact_fraction(val: Union[int, float]) -> Fraction:
    """Chuyển đổi số nguyên hoặc số thực sang Fraction một cách chuẩn tắc và chính xác."""
    if isinstance(val, int):
        return Fraction(val, 1)
    return Fraction(str(val))


def assemble_plan_frame_geometry(
    proposal: Union[Mapping[str, Any], EditPlanProposal],
    evidence_table: Mapping[str, SourceFrameEvidence],
    timeline_fps: RationalFPS,
) -> PlanGeometryAssemblyResult:
    """Thẩm định và lắp ráp toàn bộ hình học khung hình của đề xuất kịch bản dựng sang timeline.
    
    Tham số:
    - proposal: EditPlanProposal hoặc Mapping hợp lệ (được chuẩn hóa và sở hữu an toàn).
    - evidence_table: Bảng tra cứu bằng chứng khung hình nguồn theo clip_id (tối đa 4096 mục).
    - timeline_fps: Tốc độ khung hình của timeline dựng (RationalFPS).
    
    Trả về:
    - PlanGeometryAssemblyResult: Kết quả thẩm định hình học toàn bản.
    """
    # 1. Chuẩn hóa và sở hữu an toàn proposal
    owned_proposal = normalize_edit_plan_proposal(proposal)
    proposal_rev = compute_proposal_revision(owned_proposal)

    # 2. Xác thực timeline_fps
    owned_tl_fps = validate_fps_instance(timeline_fps, "ERR_INVALID_TIMELINE_FPS")

    # 3. Thu thập tập hợp clip_id hợp lệ và reference_id từ proposal
    proposal_clips = owned_proposal.source_binding.get("clips", ())
    if not isinstance(proposal_clips, (list, tuple)):
        proposal_clips = ()
    authorized_clip_ids: set[str] = set()
    for c in proposal_clips:
        if isinstance(c, Mapping) and "clip_id" in c and isinstance(c["clip_id"], str):
            authorized_clip_ids.add(c["clip_id"])

    reference_ids: set[str] = set()
    for ref in owned_proposal.creative_context.reference_items:
        ref_id = getattr(ref, "reference_id", None) if not isinstance(ref, Mapping) else ref.get("reference_id")
        if isinstance(ref_id, str):
            reference_ids.add(ref_id)

    # 4. Xác thực và tái sở hữu toàn bộ bảng evidence_table ở ranh giới đầu vào
    if not isinstance(evidence_table, Mapping):
        raise ValueError(f"ERR_INVALID_MAPPING: evidence_table phải là Mapping, nhận: {type(evidence_table).__name__}")
    if len(evidence_table) > MAX_EVIDENCE_TABLE_ITEMS:
        raise ValueError(f"ERR_COUNT_OVERFLOW: evidence_table chứa {len(evidence_table)} mục, vượt quá giới hạn {MAX_EVIDENCE_TABLE_ITEMS}")

    owned_evidence_table: dict[str, SourceFrameEvidence] = {}
    for k, ev in evidence_table.items():
        clean_k = _validate_clean_str(k, "evidence_table key", allow_blank=False)
        # Tái kiểm tra và sở hữu bản sao độc lập (chống deleted attributes và mutated status)
        owned_ev = reconstruct_source_frame_evidence(ev)
        if clean_k != owned_ev.clip_id:
            raise ValueError(f"ERR_CLIP_ID_MISMATCH: Khóa mapping {clean_k!r} không khớp ev.clip_id {owned_ev.clip_id!r}")
        if owned_ev.manifest_id != owned_proposal.manifest_id:
            raise ValueError(f"ERR_MANIFEST_MISMATCH: ev.manifest_id ({owned_ev.manifest_id!r}) không khớp proposal.manifest_id ({owned_proposal.manifest_id!r})")
        # Kiểm tra thẩm quyền nguồn: clip phải thuộc proposal.source_binding.clips
        if clean_k not in authorized_clip_ids:
            raise ValueError(f"ERR_UNKNOWN_CLIP_ID: clip_id {clean_k!r} trong evidence_table không tồn tại trong proposal.source_binding.clips")
        owned_evidence_table[clean_k] = owned_ev

    issues: list[PlanGeometryIssue] = []
    unresolved_reviews: list[UnresolvedReviewItem] = []
    emitted_segments: list[PlanTimelineSegmentGeometry] = []
    cursor_frames: int = 0

    # 5. Trích xuất các mục rà soát ngữ cảnh sáng tạo (CreativeContext)
    c_ctx = owned_proposal.creative_context
    for idx, rule in enumerate(c_ctx.must_keep_rules):
        unresolved_reviews.append(
            UnresolvedReviewItem(
                category="SEMANTIC_MUST_KEEP",
                item_id=f"must_keep_rule_{idx}",
                description=rule,
            )
        )
    for idx, rule in enumerate(c_ctx.must_not_rules):
        unresolved_reviews.append(
            UnresolvedReviewItem(
                category="SEMANTIC_MUST_NOT",
                item_id=f"must_not_rule_{idx}",
                description=rule,
            )
        )

    # 6. Trích xuất các tài sản bỏ sót bổ trợ (Supplemental Omissions - D02)
    for idx, omit in enumerate(owned_proposal.proposed_omissions):
        aid = omit.get("demand_id") or omit.get("asset_id") or f"omission_{idx}"
        desc = omit.get("reason", "Supplemental asset omission")
        unresolved_reviews.append(
            UnresolvedReviewItem(
                category="SUPPLEMENTAL_OMISSION",
                item_id=str(aid),
                description=str(desc),
            )
        )

    # 7. Thẩm định và xử lý global_hook (Giai đoạn kiểm tra nghiêm ngặt thống nhất)
    video_plan = owned_proposal.video_plan
    hook = video_plan.get("global_hook")
    if hook is not None:
        if not isinstance(hook, Mapping):
            issues.append(
                PlanGeometryIssue(
                    severity="BLOCKING",
                    issue_code="ERR_INVALID_HOOK_STRUCTURE",
                    clip_id=None,
                    segment_index=None,
                    message=f"global_hook phải là Mapping, nhận: {type(hook).__name__}",
                )
            )
        else:
            hook_has_blocking = False

            # 7.1 Kiểm tra role của hook: explicit 'reference_only' bị cấm tuyệt đối làm footage
            raw_hook_cid = hook.get("clip_id")
            if hook.get("role") == "reference_only":
                issues.append(
                    PlanGeometryIssue(
                        severity="BLOCKING",
                        issue_code="ERR_REFERENCE_CLIP_NOT_FOOTAGE",
                        clip_id=raw_hook_cid if isinstance(raw_hook_cid, str) and raw_hook_cid.strip() and "\0" not in raw_hook_cid else None,
                        segment_index=None,
                        message="global_hook có role='reference_only', cấm dùng làm video footage",
                    )
                )
                hook_has_blocking = True

            # 7.2 Kiểm tra clip_id của hook
            hook_cid = None
            if "clip_id" not in hook or raw_hook_cid is None:
                issues.append(
                    PlanGeometryIssue(
                        severity="BLOCKING",
                        issue_code="ERR_MISSING_HOOK_FIELD",
                        clip_id=None,
                        segment_index=None,
                        message="global_hook thiếu trường bắt buộc 'clip_id'",
                    )
                )
                hook_has_blocking = True
            elif type(raw_hook_cid) is not str or not raw_hook_cid.strip() or "\0" in raw_hook_cid:
                issues.append(
                    PlanGeometryIssue(
                        severity="BLOCKING",
                        issue_code="ERR_INVALID_CLIP_ID",
                        clip_id=None,
                        segment_index=None,
                        message=f"global_hook.clip_id phải là non-empty str, nhận: {type(raw_hook_cid).__name__}",
                    )
                )
                hook_has_blocking = True
            else:
                try:
                    hook_cid = _validate_clean_str(raw_hook_cid, "global_hook.clip_id", allow_blank=False)
                except ValueError as ve:
                    code = str(ve).split(":")[0] if ":" in str(ve) else "ERR_INVALID_CLIP_ID"
                    issues.append(
                        PlanGeometryIssue(
                            severity="BLOCKING",
                            issue_code=code,
                            clip_id=None,
                            segment_index=None,
                            message=str(ve),
                        )
                    )
                    hook_has_blocking = True

                if hook_cid:
                    if hook_cid in reference_ids:
                        issues.append(
                            PlanGeometryIssue(
                                severity="BLOCKING",
                                issue_code="ERR_REFERENCE_CLIP_NOT_FOOTAGE",
                                clip_id=hook_cid,
                                segment_index=None,
                                message=f"global_hook.clip_id {hook_cid!r} thuộc reference_items (role=reference_only), không được dùng làm video footage",
                            )
                        )
                        hook_has_blocking = True
                    elif hook_cid not in authorized_clip_ids:
                        issues.append(
                            PlanGeometryIssue(
                                severity="BLOCKING",
                                issue_code="ERR_UNKNOWN_CLIP_ID",
                                clip_id=hook_cid,
                                segment_index=None,
                                message=f"global_hook.clip_id {hook_cid!r} không tồn tại trong source_binding.clips",
                            )
                        )
                        hook_has_blocking = True

            # 7.2 Kiểm tra start_sec và end_sec của hook
            h_start = None
            h_end = None
            if "start_sec" not in hook or hook.get("start_sec") is None:
                issues.append(
                    PlanGeometryIssue(
                        severity="BLOCKING",
                        issue_code="ERR_MISSING_HOOK_FIELD",
                        clip_id=hook_cid,
                        segment_index=None,
                        message="global_hook thiếu trường bắt buộc 'start_sec'",
                    )
                )
                hook_has_blocking = True
            else:
                raw_start = hook["start_sec"]
                if type(raw_start) is bool or not isinstance(raw_start, (int, float)):
                    issues.append(
                        PlanGeometryIssue(
                            severity="BLOCKING",
                            issue_code="ERR_INVALID_NUMBER",
                            clip_id=hook_cid,
                            segment_index=None,
                            message=f"global_hook.start_sec phải là int hoặc float (không phải bool/string), nhận: {type(raw_start).__name__}",
                        )
                    )
                    hook_has_blocking = True
                elif isinstance(raw_start, float) and (math.isnan(raw_start) or math.isinf(raw_start)):
                    issues.append(
                        PlanGeometryIssue(
                            severity="BLOCKING",
                            issue_code="ERR_INVALID_NUMBER",
                            clip_id=hook_cid,
                            segment_index=None,
                            message="global_hook.start_sec không được là NaN hoặc Inf",
                        )
                    )
                    hook_has_blocking = True
                else:
                    h_start = raw_start

            if "end_sec" not in hook or hook.get("end_sec") is None:
                issues.append(
                    PlanGeometryIssue(
                        severity="BLOCKING",
                        issue_code="ERR_MISSING_HOOK_FIELD",
                        clip_id=hook_cid,
                        segment_index=None,
                        message="global_hook thiếu trường bắt buộc 'end_sec'",
                    )
                )
                hook_has_blocking = True
            else:
                raw_end = hook["end_sec"]
                if type(raw_end) is bool or not isinstance(raw_end, (int, float)):
                    issues.append(
                        PlanGeometryIssue(
                            severity="BLOCKING",
                            issue_code="ERR_INVALID_NUMBER",
                            clip_id=hook_cid,
                            segment_index=None,
                            message=f"global_hook.end_sec phải là int hoặc float (không phải bool/string), nhận: {type(raw_end).__name__}",
                        )
                    )
                    hook_has_blocking = True
                elif isinstance(raw_end, float) and (math.isnan(raw_end) or math.isinf(raw_end)):
                    issues.append(
                        PlanGeometryIssue(
                            severity="BLOCKING",
                            issue_code="ERR_INVALID_NUMBER",
                            clip_id=hook_cid,
                            segment_index=None,
                            message="global_hook.end_sec không được là NaN hoặc Inf",
                        )
                    )
                    hook_has_blocking = True
                else:
                    h_end = raw_end

            if h_start is not None and h_end is not None:
                if h_start < 0:
                    issues.append(
                        PlanGeometryIssue(
                            severity="BLOCKING",
                            issue_code="ERR_INVALID_NUMBER",
                            clip_id=hook_cid,
                            segment_index=None,
                            message=f"global_hook.start_sec phải không âm (>= 0), nhận: {h_start}",
                        )
                    )
                    hook_has_blocking = True
                if h_start >= h_end:
                    issues.append(
                        PlanGeometryIssue(
                            severity="BLOCKING",
                            issue_code="ERR_INVALID_TIME_RANGE",
                            clip_id=hook_cid,
                            segment_index=None,
                            message=f"global_hook.start_sec ({h_start}) phải nhỏ hơn end_sec ({h_end})",
                        )
                    )
                    hook_has_blocking = True

            # 7.3 Kiểm tra speed của hook (tùy chọn, mặc định 1; key có mặt nhưng None là không hợp lệ)
            hook_speed = 1
            if "speed" in hook:
                raw_speed = hook["speed"]
                if raw_speed is None or type(raw_speed) is bool or not isinstance(raw_speed, (int, float)):
                    issues.append(
                        PlanGeometryIssue(
                            severity="BLOCKING",
                            issue_code="ERR_INVALID_SPEED",
                            clip_id=hook_cid,
                            segment_index=None,
                            message=f"global_hook.speed phải là int hoặc float dương (không được là None/bool/string), nhận: {type(raw_speed).__name__}",
                        )
                    )
                    hook_has_blocking = True
                elif isinstance(raw_speed, float) and (math.isnan(raw_speed) or math.isinf(raw_speed)):
                    issues.append(
                        PlanGeometryIssue(
                            severity="BLOCKING",
                            issue_code="ERR_INVALID_SPEED",
                            clip_id=hook_cid,
                            segment_index=None,
                            message="global_hook.speed không được là NaN hoặc Inf",
                        )
                    )
                    hook_has_blocking = True
                elif raw_speed <= 0:
                    issues.append(
                        PlanGeometryIssue(
                            severity="BLOCKING",
                            issue_code="ERR_INVALID_SPEED",
                            clip_id=hook_cid,
                            segment_index=None,
                            message=f"global_hook.speed phải dương (> 0), nhận: {raw_speed}",
                        )
                    )
                    hook_has_blocking = True
                else:
                    hook_speed = raw_speed

            # 7.4 Kiểm tra placement_mode của hook
            mode = hook.get("placement_mode")
            if mode is None:
                # Chỉ khi cấu trúc hook hoàn toàn hợp lệ mới coi là chưa quyết định (AMBIGUITY)
                if not hook_has_blocking:
                    issues.append(
                        PlanGeometryIssue(
                            severity="WARNING",
                            issue_code="ERR_HOOK_PLACEMENT_AMBIGUITY",
                            clip_id=hook_cid,
                            segment_index=None,
                            message="global_hook thiếu trường placement_mode ('intro_cut' hoặc 'highlight_ref')",
                        )
                    )
                    unresolved_reviews.append(
                        UnresolvedReviewItem(
                            category="HOOK_PLACEMENT",
                            item_id="global_hook",
                            description="Xác định placement_mode cho global_hook",
                        )
                    )
            elif type(mode) is not str:
                issues.append(
                    PlanGeometryIssue(
                        severity="BLOCKING",
                        issue_code="ERR_INVALID_HOOK_PLACEMENT_MODE",
                        clip_id=hook_cid,
                        segment_index=None,
                        message=f"placement_mode của global_hook phải là str, nhận: {type(mode).__name__}",
                    )
                )
                hook_has_blocking = True
            elif mode not in VALID_HOOK_MODES:
                issues.append(
                    PlanGeometryIssue(
                        severity="BLOCKING",
                        issue_code="ERR_INVALID_HOOK_PLACEMENT_MODE",
                        clip_id=hook_cid,
                        segment_index=None,
                        message=f"placement_mode của global_hook không hợp lệ: {mode!r}",
                    )
                )
                hook_has_blocking = True
            else:
                # mode hợp lệ ('intro_cut' hoặc 'highlight_ref') và hook không có lỗi BLOCKING
                if not hook_has_blocking and hook_cid is not None and h_start is not None and h_end is not None:
                    if mode == "intro_cut":
                        if hook_cid not in owned_evidence_table:
                            issues.append(
                                PlanGeometryIssue(
                                    severity="WARNING",
                                    issue_code="ERR_MISSING_SOURCE_EVIDENCE",
                                    clip_id=hook_cid,
                                    segment_index=None,
                                    message=f"Clip {hook_cid!r} trong global_hook không có trong evidence_table",
                                )
                            )
                        else:
                            h_ev = owned_evidence_table[hook_cid]
                            if h_ev.evidence_status == "HEADER_REPORTED_ONLY":
                                issues.append(
                                    PlanGeometryIssue(
                                        severity="WARNING",
                                        issue_code="ERR_UNVERIFIED_CFR_EVIDENCE",
                                        clip_id=hook_cid,
                                        segment_index=None,
                                        message=f"Clip {hook_cid} trong global_hook chỉ có siêu dữ liệu header",
                                    )
                                )
                            elif h_ev.evidence_status == "VERIFIED_VFR":
                                issues.append(
                                    PlanGeometryIssue(
                                        severity="BLOCKING",
                                        issue_code="ERR_VFR_UNSUPPORTED",
                                        clip_id=hook_cid,
                                        segment_index=None,
                                        message=f"Clip {hook_cid} trong global_hook là video VFR",
                                    )
                                )
                            elif h_ev.evidence_status == "VERIFIED_CFR":
                                try:
                                    h_span = quantize_cfr_video_span(
                                        h_start,
                                        h_end,
                                        source_fps=h_ev.source_fps,
                                        timeline_fps=owned_tl_fps,
                                        total_source_frames=h_ev.total_frames,
                                        is_cfr=True,
                                        applied_speed=hook_speed,
                                    )
                                    h_rec_in = cursor_frames
                                    h_rec_out = cursor_frames + h_span.record_duration_frames
                                    cursor_frames = h_rec_out
                                    emitted_segments.append(
                                        PlanTimelineSegmentGeometry(
                                            segment_index=len(emitted_segments),
                                            origin="global_hook",
                                            source_segment_index=None,
                                            clip_id=hook_cid,
                                            quantized_span=h_span,
                                            timeline_range=TimeRangeFrames(h_rec_in, h_rec_out),
                                        )
                                    )
                                except ValueError as ve:
                                    code = str(ve).split(":")[0] if ":" in str(ve) else "ERR_GEOMETRY_CALCULATION"
                                    issues.append(
                                        PlanGeometryIssue(
                                            severity="BLOCKING",
                                            issue_code=code,
                                            clip_id=hook_cid,
                                            segment_index=None,
                                            message=str(ve),
                                        )
                                    )
                    elif mode == "highlight_ref":
                        # So sánh chính xác phân số hữu tỉ để kiểm tra bao hàm (containment)
                        contained = False
                        h_s_frac = _to_exact_fraction(h_start)
                        h_e_frac = _to_exact_fraction(h_end)
                        for seg in video_plan.get("timeline_segments", ()):
                            if (
                                isinstance(seg, Mapping)
                                and seg.get("clip_id") == hook_cid
                                and seg.get("action", "keep") in ("keep", "speedup")
                            ):
                                s_in = seg.get("start_sec")
                                s_out = seg.get("end_sec")
                                if (
                                    s_in is not None
                                    and s_out is not None
                                    and not isinstance(s_in, bool)
                                    and not isinstance(s_out, bool)
                                    and isinstance(s_in, (int, float))
                                    and isinstance(s_out, (int, float))
                                    and math.isfinite(s_in)
                                    and math.isfinite(s_out)
                                ):
                                    s_in_frac = _to_exact_fraction(s_in)
                                    s_out_frac = _to_exact_fraction(s_out)
                                    if s_in_frac <= h_s_frac and s_out_frac >= h_e_frac:
                                        contained = True
                                        break

                        if not contained:
                            issues.append(
                                PlanGeometryIssue(
                                    severity="BLOCKING",
                                    issue_code="ERR_HOOK_HIGHLIGHT_NOT_CONTAINED",
                                    clip_id=hook_cid,
                                    segment_index=None,
                                    message="global_hook dạng highlight_ref không nằm trọn trong phân đoạn keep/speedup nào trên cùng clip nguồn",
                                )
                            )

    # 8. Duyệt và lắp ráp tuần tự A-Roll timeline_segments
    timeline_segs = video_plan.get("timeline_segments", ())
    for orig_idx, seg in enumerate(timeline_segs):
        if not isinstance(seg, Mapping):
            issues.append(
                PlanGeometryIssue(
                    severity="BLOCKING",
                    issue_code="ERR_INVALID_SEGMENT",
                    clip_id=None,
                    segment_index=orig_idx,
                    message=f"timeline_segments[{orig_idx}] phải là Mapping, nhận: {type(seg).__name__}",
                )
            )
            continue

        raw_cid = seg.get("clip_id")
        action = seg.get("action", "keep")
        speed = seg.get("speed", 1)

        # 8.1 Xác thực clip_id
        cid = None
        if "clip_id" not in seg or raw_cid is None:
            issues.append(
                PlanGeometryIssue(
                    severity="BLOCKING",
                    issue_code="ERR_MISSING_CLIP_ID",
                    clip_id=None,
                    segment_index=orig_idx,
                    message=f"timeline_segments[{orig_idx}] thiếu trường 'clip_id'",
                )
            )
            continue
        elif type(raw_cid) is not str or not raw_cid.strip() or "\0" in raw_cid:
            issues.append(
                PlanGeometryIssue(
                    severity="BLOCKING",
                    issue_code="ERR_INVALID_CLIP_ID",
                    clip_id=None,
                    segment_index=orig_idx,
                    message=f"timeline_segments[{orig_idx}].clip_id phải là non-empty str",
                )
            )
            continue
        else:
            try:
                cid = _validate_clean_str(raw_cid, f"timeline_segments[{orig_idx}].clip_id", allow_blank=False)
            except ValueError as ve:
                code = str(ve).split(":")[0] if ":" in str(ve) else "ERR_INVALID_CLIP_ID"
                issues.append(
                    PlanGeometryIssue(
                        severity="BLOCKING",
                        issue_code=code,
                        clip_id=None,
                        segment_index=orig_idx,
                        message=str(ve),
                    )
                )
                continue

            if cid in reference_ids:
                issues.append(
                    PlanGeometryIssue(
                        severity="BLOCKING",
                        issue_code="ERR_REFERENCE_CLIP_NOT_FOOTAGE",
                        clip_id=cid,
                        segment_index=orig_idx,
                        message=f"timeline_segments[{orig_idx}].clip_id {cid!r} thuộc reference_items (role=reference_only), không được dùng làm video footage",
                    )
                )
                continue
            elif cid not in authorized_clip_ids:
                issues.append(
                    PlanGeometryIssue(
                        severity="BLOCKING",
                        issue_code="ERR_UNKNOWN_CLIP_ID",
                        clip_id=cid,
                        segment_index=orig_idx,
                        message=f"timeline_segments[{orig_idx}].clip_id {cid!r} không tồn tại trong source_binding.clips",
                    )
                )
                continue

        # 8.2 Xác thực hành động (action)
        if type(action) is not str or action not in VALID_ACTIONS:
            issues.append(
                PlanGeometryIssue(
                    severity="BLOCKING",
                    issue_code="ERR_INVALID_ACTION",
                    clip_id=cid,
                    segment_index=orig_idx,
                    message=f"timeline_segments[{orig_idx}].action không hợp lệ: {action!r}",
                )
            )
            continue

        # 8.3 Xác thực tốc độ (speed)
        try:
            if type(speed) is bool or not isinstance(speed, (int, float)):
                raise ValueError("speed phải là int hoặc float (không phải bool)")
            s_num = float(speed)
            if not math.isfinite(s_num) or s_num <= 0:
                raise ValueError("speed phải là số dương hữu hạn (> 0)")
        except Exception as e:
            issues.append(
                PlanGeometryIssue(
                    severity="BLOCKING",
                    issue_code="ERR_INVALID_SPEED",
                    clip_id=cid,
                    segment_index=orig_idx,
                    message=f"timeline_segments[{orig_idx}].speed không hợp lệ: {speed!r} ({e})",
                )
            )
            continue

        # 8.4 Xác thực start_sec và end_sec
        s_start = seg.get("start_sec")
        s_end = seg.get("end_sec")
        if s_start is None or s_end is None or type(s_start) is bool or type(s_end) is bool or not isinstance(s_start, (int, float)) or not isinstance(s_end, (int, float)):
            issues.append(
                PlanGeometryIssue(
                    severity="BLOCKING",
                    issue_code="ERR_INVALID_TIME_RANGE",
                    clip_id=cid,
                    segment_index=orig_idx,
                    message=f"timeline_segments[{orig_idx}] có start_sec/end_sec không hợp lệ",
                )
            )
            continue
        if s_start < 0 or s_start >= s_end or not math.isfinite(s_start) or not math.isfinite(s_end):
            issues.append(
                PlanGeometryIssue(
                    severity="BLOCKING",
                    issue_code="ERR_INVALID_TIME_RANGE",
                    clip_id=cid,
                    segment_index=orig_idx,
                    message=f"timeline_segments[{orig_idx}] có khoảng thời gian không hợp lệ: [{s_start}, {s_end})",
                )
            )
            continue

        # 8.5 Tra cứu bằng chứng khung hình nguồn từ owned_evidence_table
        if cid not in owned_evidence_table:
            issues.append(
                PlanGeometryIssue(
                    severity="WARNING",
                    issue_code="ERR_MISSING_SOURCE_EVIDENCE",
                    clip_id=cid,
                    segment_index=orig_idx,
                    message=f"Clip {cid!r} trong timeline_segments[{orig_idx}] không có trong evidence_table",
                )
            )
            continue

        ev = owned_evidence_table[cid]
        if ev.evidence_status == "HEADER_REPORTED_ONLY":
            issues.append(
                PlanGeometryIssue(
                    severity="WARNING",
                    issue_code="ERR_UNVERIFIED_CFR_EVIDENCE",
                    clip_id=cid,
                    segment_index=orig_idx,
                    message=f"Clip {cid} trong timeline_segments[{orig_idx}] chỉ có siêu dữ liệu header container",
                )
            )
            continue
        elif ev.evidence_status == "VERIFIED_VFR":
            issues.append(
                PlanGeometryIssue(
                    severity="BLOCKING",
                    issue_code="ERR_VFR_UNSUPPORTED",
                    clip_id=cid,
                    segment_index=orig_idx,
                    message=f"Clip {cid} trong timeline_segments[{orig_idx}] là video VFR",
                )
            )
            continue
        elif ev.evidence_status == "VERIFIED_CFR":
            if action == "cut":
                # Phân đoạn cut tường minh: phát ra 0 frame, nhưng bắt buộc phải kiểm tra tính hợp lệ hình học/bounds
                try:
                    _ = quantize_cfr_video_span(
                        s_start,
                        s_end,
                        source_fps=ev.source_fps,
                        timeline_fps=owned_tl_fps,
                        total_source_frames=ev.total_frames,
                        is_cfr=True,
                        applied_speed=speed,
                    )
                except ValueError as ve:
                    code = str(ve).split(":")[0] if ":" in str(ve) else "ERR_GEOMETRY_CALCULATION"
                    issues.append(
                        PlanGeometryIssue(
                            severity="BLOCKING",
                            issue_code=code,
                            clip_id=cid,
                            segment_index=orig_idx,
                            message=str(ve),
                        )
                    )
                continue

            # action == "keep" hoặc "speedup"
            try:
                span = quantize_cfr_video_span(
                    s_start,
                    s_end,
                    source_fps=ev.source_fps,
                    timeline_fps=owned_tl_fps,
                    total_source_frames=ev.total_frames,
                    is_cfr=True,
                    applied_speed=speed,
                )
                rec_in = cursor_frames
                rec_out = cursor_frames + span.record_duration_frames
                cursor_frames = rec_out
                emitted_segments.append(
                    PlanTimelineSegmentGeometry(
                        segment_index=len(emitted_segments),
                        origin="timeline_segment",
                        source_segment_index=orig_idx,
                        clip_id=cid,
                        quantized_span=span,
                        timeline_range=TimeRangeFrames(rec_in, rec_out),
                    )
                )
            except ValueError as ve:
                code = str(ve).split(":")[0] if ":" in str(ve) else "ERR_GEOMETRY_CALCULATION"
                issues.append(
                    PlanGeometryIssue(
                        severity="BLOCKING",
                        issue_code=code,
                        clip_id=cid,
                        segment_index=orig_idx,
                        message=str(ve),
                    )
                )

    # 9. Xác định trạng thái tổng thể theo thứ tự ưu tiên: BLOCKED > UNRESOLVED > VERIFIED
    has_blocking = any(iss.severity == "BLOCKING" for iss in issues)
    has_warning = any(iss.severity == "WARNING" for iss in issues)

    if has_blocking:
        status = "GEOMETRY_BLOCKED"
        assembled = ()
        total_frames = None
        total_duration = None
    elif has_warning:
        status = "GEOMETRY_UNRESOLVED"
        assembled = ()
        total_frames = None
        total_duration = None
    else:
        status = "GEOMETRY_VERIFIED"
        assembled = tuple(emitted_segments)
        total_frames = cursor_frames
        total_duration = Fraction(cursor_frames) / owned_tl_fps.as_fraction()

    return PlanGeometryAssemblyResult(
        status=status,
        proposal_id=owned_proposal.proposal_id,
        proposal_revision=proposal_rev,
        timeline_fps=owned_tl_fps,
        assembled_segments=assembled,
        total_timeline_frames=total_frames,
        total_timeline_duration_sec=total_duration,
        issues=tuple(issues),
        unresolved_reviews=tuple(unresolved_reviews),
    )
