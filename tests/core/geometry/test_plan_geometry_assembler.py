"""tests/core/geometry/test_plan_geometry_assembler.py
Kiểm thử đơn vị tập trung thuần túy (TDD) cho bộ lắp ráp hình học kịch bản toàn bản
(TASK_ID: G06-06C2c2-EVIDENCE-R2-THEN-06C2d-I).

Quy tắc nghiêm ngặt:
- Zero filesystem IO, zero network, zero subprocess, zero Whisper/AI inference, zero Qt widgets.
- Kiểm thử bao phủ trọn vẹn 5 ca kiểm thử chuẩn tắc (F01 -> F05):
  + F01: Chuyển đổi tốc độ chéo Formula B (30fps -> 24fps) với source30/1, total90, [1, 2.017) -> native[30, 61), record25.
         Giữ NTSC 30000/1001 60 frames [1, 2.017) làm ca kiểm chứng từ chối tràn biên (2.002s max).
  + F02: Lắp ráp nhiều phân đoạn liên tục, tốc độ speed 1.5/2.0, gapless cursor, phân đoạn cut không phát frame, all-cut trả về 0 frames đã biết.
  + F03: Từ chối siêu dữ liệu chỉ có Header Container (HEADER_REPORTED_ONLY) và thiếu bằng chứng nguồn (GEOMETRY_UNRESOLVED, 0 prefix).
  + F04: Bắt lỗi phân đoạn 0 khung hình (ERR_ZERO_FRAME_SEGMENT) và nguồn VFR (ERR_VFR_UNSUPPORTED) -> GEOMETRY_BLOCKED.
  + F05: Xử lý ngữ nghĩa global_hook: vắng mặt, thiếu placement_mode (AMBIGUITY -> UNRESOLVED), intro_cut, highlight_ref (contained vs uncontained).
- Rà soát CreativeContext (must_keep_rules / must_not_rules) và D02 proposed_omissions.
- Kiểm tra bảo vệ mutation, kiểu dữ liệu nghiêm ngặt, giới hạn bảng 4096 mục và tính tất định.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Mapping
from unittest.mock import patch

import pytest

from src.core.contracts.edit_plan_contracts import CreativeContext
from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    compute_proposal_revision,
    normalize_edit_plan_proposal,
)
from src.core.contracts.time_contracts import RationalFPS, TimeRangeFrames
from src.core.geometry.frame_geometry_contracts import QuantizedCFRSpan
from src.core.geometry.plan_geometry_assembler import assemble_plan_frame_geometry
from src.core.geometry.plan_geometry_contracts import (
    PlanGeometryAssemblyResult,
    PlanGeometryIssue,
    PlanTimelineSegmentGeometry,
    SourceFrameEvidence,
    UnresolvedReviewItem,
    reconstruct_source_frame_evidence,
)


def _make_minimal_proposal_dict(
    manifest_id: str = "mf_test_01",
    proposal_id: str = "prop_test_01",
    clips: Sequence[dict[str, Any]] = None,
    timeline_segments: Sequence[dict[str, Any]] = None,
    global_hook: Optional[dict[str, Any]] = None,
    must_keep_rules: Sequence[str] = (),
    must_not_rules: Sequence[str] = (),
    proposed_omissions: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Tạo dictionary thô tối thiểu nhưng hợp lệ cho EditPlanProposal."""
    if clips is None:
        clips = [{"clip_id": "c1", "duration_sec": 10.0, "fps": 30.0}]
    if timeline_segments is None:
        timeline_segments = [{"clip_id": "c1", "start_sec": 1.0, "end_sec": 2.0}]

    vp: dict[str, Any] = {
        "manifest_id": manifest_id,
        "timeline_segments": list(timeline_segments),
    }
    if global_hook is not None:
        vp["global_hook"] = global_hook

    return {
        "proposal_id": proposal_id,
        "manifest_id": manifest_id,
        "creative_context": {
            "free_brief": "Minimal brief for plan geometry tests",
            "style_preset": "jump_cut",
            "target_duration_sec": 60,
            "must_keep_rules": list(must_keep_rules),
            "must_not_rules": list(must_not_rules),
            "reference_items": [],
            "reference_urls": [],
        },
        "source_binding": {
            "manifest_id": manifest_id,
            "clips": list(clips),
        },
        "video_plan": vp,
        "kinetic_plan": None,
        "proposed_asset_assignments": [],
        "proposed_omissions": list(proposed_omissions),
    }


# ==============================================================================
# F01: Chuyển đổi tốc độ chéo Formula B (30fps -> 24fps)
# ==============================================================================

def test_f01_positive_cross_rate_formula_b():
    """F01 Positive: source 30/1, total 90 frames, [1.0, 2.017), timeline 24/1.
    Toán học Formula B:
    - src_in_f = pts_to_frame(1.0, 30/1) = 30
    - src_out_f = pts_to_frame(2017/1000, 30/1) = pts_to_frame(60510/1000) = round_half_even(60.51) = 61
    - source_dur = 31 frames.
    - exact_record_sec = 31 / 30.
    - rec_dur_f = pts_to_frame(31/30, 24/1) = round_half_even(744/30) = round_half_even(24.8) = 25 frames.
    - record_range: [0, 25).
    """
    raw_prop = _make_minimal_proposal_dict(
        manifest_id="mf_f01",
        proposal_id="prop_f01",
        clips=[{"clip_id": "clip_30fps", "duration_sec": 3.0, "fps": 30.0}],
        timeline_segments=[{"clip_id": "clip_30fps", "start_sec": 1.0, "end_sec": 2.017, "action": "keep", "speed": 1}],
    )
    ev_table = {
        "clip_30fps": SourceFrameEvidence(
            manifest_id="mf_f01",
            clip_id="clip_30fps",
            source_fps=RationalFPS(30, 1),
            total_frames=90,
            evidence_status="VERIFIED_CFR",
        )
    }
    result = assemble_plan_frame_geometry(raw_prop, ev_table, timeline_fps=RationalFPS(24, 1))

    assert result.status == "GEOMETRY_VERIFIED"
    assert len(result.issues) == 0
    assert len(result.assembled_segments) == 1
    seg = result.assembled_segments[0]
    assert seg.segment_index == 0
    assert seg.origin == "timeline_segment"
    assert seg.source_segment_index == 0
    assert seg.clip_id == "clip_30fps"
    assert seg.quantized_span.source_range == TimeRangeFrames(30, 61)
    assert seg.timeline_range == TimeRangeFrames(0, 25)
    assert seg.timeline_duration_frames == 25
    assert seg.applied_speed == Fraction(1, 1)
    assert result.total_timeline_frames == 25
    assert result.total_timeline_duration_sec == Fraction(25, 24)


def test_f01_negative_ntsc_overflow_denial():
    """F01 Negative Control: source NTSC 30000/1001 fps, 60 frames (max duration = 1001/500 = 2.002s).
    Đoạn cắt [1.0, 2.017) vượt quá thời lượng clip (2.017 > 2.002).
    Kỳ vọng: Lỗi ERR_SOURCE_BOUNDS_OVERFLOW, GEOMETRY_BLOCKED, assembled rỗng ().
    """
    raw_prop = _make_minimal_proposal_dict(
        manifest_id="mf_f01_ntsc",
        proposal_id="prop_f01_ntsc",
        clips=[{"clip_id": "clip_ntsc", "duration_sec": 2.002, "fps": 29.97002997}],
        timeline_segments=[{"clip_id": "clip_ntsc", "start_sec": 1.0, "end_sec": 2.017}],
    )
    ev_table = {
        "clip_ntsc": SourceFrameEvidence(
            manifest_id="mf_f01_ntsc",
            clip_id="clip_ntsc",
            source_fps=RationalFPS(30000, 1001),
            total_frames=60,
            evidence_status="VERIFIED_CFR",
        )
    }
    result = assemble_plan_frame_geometry(raw_prop, ev_table, timeline_fps=RationalFPS(24, 1))

    assert result.status == "GEOMETRY_BLOCKED"
    assert len(result.assembled_segments) == 0
    assert result.total_timeline_frames is None
    assert result.total_timeline_duration_sec is None
    assert any(iss.issue_code == "ERR_SOURCE_BOUNDS_OVERFLOW" for iss in result.issues)


# ==============================================================================
# F02: Lắp ráp nhiều phân đoạn liên tục, tốc độ, cut và all-cut
# ==============================================================================

def test_f02_multiple_contiguous_cuts_and_speed():
    """F02: Lắp ráp nhiều phân đoạn với tốc độ khác nhau:
    - Clip A: 25fps, 100 frames, [0.0, 2.0), speed 1 -> native [0, 50), rec_dur = 50. Range [0, 50).
    - Clip B: 25fps, 100 frames, [1.0, 3.0), speed 2 -> native [25, 75) = 50 frames, speed 2 -> rec_dur = 25. Range [50, 75).
    - Clip C: 25fps, 100 frames, [0.0, 1.0), action 'cut' -> 0 frames.
    - Timeline: 25/1 fps.
    Kỳ vọng: Tổng 75 frames, 0 gap, 0 overlap.
    """
    raw_prop = _make_minimal_proposal_dict(
        manifest_id="mf_f02",
        proposal_id="prop_f02",
        clips=[
            {"clip_id": "cA", "duration_sec": 4.0, "fps": 25.0},
            {"clip_id": "cB", "duration_sec": 4.0, "fps": 25.0},
            {"clip_id": "cC", "duration_sec": 4.0, "fps": 25.0},
        ],
        timeline_segments=[
            {"clip_id": "cA", "start_sec": 0.0, "end_sec": 2.0, "action": "keep", "speed": 1},
            {"clip_id": "cB", "start_sec": 1.0, "end_sec": 3.0, "action": "speedup", "speed": 2},
            {"clip_id": "cC", "start_sec": 0.0, "end_sec": 1.0, "action": "cut"},
        ],
    )
    ev_table = {
        "cA": SourceFrameEvidence("mf_f02", "cA", RationalFPS(25, 1), 100, "VERIFIED_CFR"),
        "cB": SourceFrameEvidence("mf_f02", "cB", RationalFPS(25, 1), 100, "VERIFIED_CFR"),
        "cC": SourceFrameEvidence("mf_f02", "cC", RationalFPS(25, 1), 100, "VERIFIED_CFR"),
    }
    result = assemble_plan_frame_geometry(raw_prop, ev_table, timeline_fps=RationalFPS(25, 1))

    assert result.status == "GEOMETRY_VERIFIED"
    assert len(result.assembled_segments) == 2  # cC là action cut nên không emit frame
    assert result.assembled_segments[0].timeline_range == TimeRangeFrames(0, 50)
    assert result.assembled_segments[1].timeline_range == TimeRangeFrames(50, 75)
    assert result.assembled_segments[1].source_segment_index == 1
    assert result.total_timeline_frames == 75
    assert result.total_timeline_duration_sec == Fraction(75, 25)


def test_f02_all_cut_known_zero_frames():
    """F02b: Tất cả các phân đoạn đều là action 'cut' hợp lệ:
    - Trả về GEOMETRY_VERIFIED với 0 frames và Fraction(0, 1) giây (khác với None).
    """
    raw_prop = _make_minimal_proposal_dict(
        manifest_id="mf_all_cut",
        proposal_id="prop_all_cut",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        timeline_segments=[
            {"clip_id": "c1", "start_sec": 0.0, "end_sec": 1.0, "action": "cut"},
            {"clip_id": "c1", "start_sec": 2.0, "end_sec": 3.0, "action": "cut"},
        ],
    )
    ev_table = {
        "c1": SourceFrameEvidence("mf_all_cut", "c1", RationalFPS(25, 1), 125, "VERIFIED_CFR"),
    }
    result = assemble_plan_frame_geometry(raw_prop, ev_table, timeline_fps=RationalFPS(25, 1))

    assert result.status == "GEOMETRY_VERIFIED"
    assert len(result.assembled_segments) == 0
    assert result.total_timeline_frames == 0
    assert result.total_timeline_duration_sec == Fraction(0, 1)


def test_f02_action_and_speed_defaults_and_errors():
    """F02c: Giá trị mặc định cho action ('keep') và speed (1), và từ chối giá trị lạ."""
    # 1. Mặc định
    raw_prop = _make_minimal_proposal_dict(
        manifest_id="mf_defaults",
        proposal_id="prop_defaults",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        timeline_segments=[{"clip_id": "c1", "start_sec": 0.0, "end_sec": 2.0}],  # Thiếu action và speed
    )
    ev_table = {
        "c1": SourceFrameEvidence("mf_defaults", "c1", RationalFPS(25, 1), 125, "VERIFIED_CFR"),
    }
    res = assemble_plan_frame_geometry(raw_prop, ev_table, timeline_fps=RationalFPS(25, 1))
    assert res.status == "GEOMETRY_VERIFIED"
    assert len(res.assembled_segments) == 1
    assert res.assembled_segments[0].applied_speed == Fraction(1, 1)

    # 2. Unknown action -> BLOCKED
    raw_bad_act = _make_minimal_proposal_dict(
        manifest_id="mf_defaults",
        proposal_id="prop_defaults",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        timeline_segments=[{"clip_id": "c1", "start_sec": 0.0, "end_sec": 2.0, "action": "unsupported_action"}],
    )
    res_bad = assemble_plan_frame_geometry(raw_bad_act, ev_table, timeline_fps=RationalFPS(25, 1))
    assert res_bad.status == "GEOMETRY_BLOCKED"
    assert any(iss.issue_code == "ERR_INVALID_ACTION" for iss in res_bad.issues)


# ==============================================================================
# F03: Từ chối Header-Reported & Thiếu Bằng Chứng Khung Hình
# ==============================================================================

def test_f03_header_reported_only_denial():
    """F03: Clip chỉ có trạng thái HEADER_REPORTED_ONLY:
    - Bắt buộc trả GEOMETRY_UNRESOLVED kèm mã ERR_UNVERIFIED_CFR_EVIDENCE.
    - assembled_segments rỗng (), totals = None (không trả về prefix dở dang).
    """
    raw_prop = _make_minimal_proposal_dict(
        manifest_id="mf_f03",
        proposal_id="prop_f03",
        clips=[{"clip_id": "c_hdr", "duration_sec": 5.0, "fps": 30.0}],
        timeline_segments=[{"clip_id": "c_hdr", "start_sec": 0.0, "end_sec": 2.0}],
    )
    ev_table = {
        "c_hdr": SourceFrameEvidence(
            manifest_id="mf_f03",
            clip_id="c_hdr",
            source_fps=RationalFPS(30, 1),
            total_frames=150,
            evidence_status="HEADER_REPORTED_ONLY",
        )
    }
    result = assemble_plan_frame_geometry(raw_prop, ev_table, timeline_fps=RationalFPS(24, 1))

    assert result.status == "GEOMETRY_UNRESOLVED"
    assert len(result.assembled_segments) == 0
    assert result.total_timeline_frames is None
    assert result.total_timeline_duration_sec is None
    assert any(iss.issue_code == "ERR_UNVERIFIED_CFR_EVIDENCE" for iss in result.issues)


def test_f03_missing_evidence_denial_no_prefix():
    """F03b: Có 3 phân đoạn, phân đoạn thứ 2 thiếu bằng chứng trong evidence_table:
    - Tuyệt đối cấm trả về phân đoạn 1 như một executable prefix.
    - assembled_segments = (), totals = None.
    """
    raw_prop = _make_minimal_proposal_dict(
        manifest_id="mf_f03b",
        proposal_id="prop_f03b",
        clips=[
            {"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0},
            {"clip_id": "c2_missing", "duration_sec": 5.0, "fps": 25.0},
            {"clip_id": "c3", "duration_sec": 5.0, "fps": 25.0},
        ],
        timeline_segments=[
            {"clip_id": "c1", "start_sec": 0.0, "end_sec": 1.0},
            {"clip_id": "c2_missing", "start_sec": 0.0, "end_sec": 1.0},
            {"clip_id": "c3", "start_sec": 0.0, "end_sec": 1.0},
        ],
    )
    ev_table = {
        "c1": SourceFrameEvidence("mf_f03b", "c1", RationalFPS(25, 1), 125, "VERIFIED_CFR"),
        # c2_missing không có trong ev_table!
        "c3": SourceFrameEvidence("mf_f03b", "c3", RationalFPS(25, 1), 125, "VERIFIED_CFR"),
    }
    result = assemble_plan_frame_geometry(raw_prop, ev_table, timeline_fps=RationalFPS(25, 1))

    assert result.status == "GEOMETRY_UNRESOLVED"
    assert len(result.assembled_segments) == 0
    assert result.total_timeline_frames is None
    assert result.total_timeline_duration_sec is None
    assert any(iss.issue_code == "ERR_MISSING_SOURCE_EVIDENCE" for iss in result.issues)


# ==============================================================================
# F04: Phân đoạn 0 frame & Video VFR
# ==============================================================================

def test_f04_zero_frame_segment_denial():
    """F04a: Đoạn cắt quá ngắn [1.0, 1.005) trên 24fps -> pts_to_frame cho in=out=24 (0 frame).
    Kỳ vọng: Bắt lỗi ERR_ZERO_FRAME_SEGMENT, GEOMETRY_BLOCKED, assembled rỗng.
    """
    raw_prop = _make_minimal_proposal_dict(
        manifest_id="mf_f04a",
        proposal_id="prop_f04a",
        clips=[{"clip_id": "c_24fps", "duration_sec": 5.0, "fps": 24.0}],
        timeline_segments=[{"clip_id": "c_24fps", "start_sec": 1.0, "end_sec": 1.005}],
    )
    ev_table = {
        "c_24fps": SourceFrameEvidence("mf_f04a", "c_24fps", RationalFPS(24, 1), 120, "VERIFIED_CFR"),
    }
    result = assemble_plan_frame_geometry(raw_prop, ev_table, timeline_fps=RationalFPS(24, 1))

    assert result.status == "GEOMETRY_BLOCKED"
    assert len(result.assembled_segments) == 0
    assert any(iss.issue_code == "ERR_ZERO_FRAME_SEGMENT" for iss in result.issues)


def test_f04_vfr_unsupported_denial():
    """F04b: Nguồn được xác nhận là VFR (VERIFIED_VFR):
    Kỳ vọng: Bắt lỗi ERR_VFR_UNSUPPORTED, GEOMETRY_BLOCKED.
    """
    raw_prop = _make_minimal_proposal_dict(
        manifest_id="mf_f04b",
        proposal_id="prop_f04b",
        clips=[{"clip_id": "c_vfr", "duration_sec": 5.0, "fps": 30.0}],
        timeline_segments=[{"clip_id": "c_vfr", "start_sec": 1.0, "end_sec": 2.0}],
    )
    ev_table = {
        "c_vfr": SourceFrameEvidence("mf_f04b", "c_vfr", None, None, "VERIFIED_VFR"),
    }
    result = assemble_plan_frame_geometry(raw_prop, ev_table, timeline_fps=RationalFPS(24, 1))

    assert result.status == "GEOMETRY_BLOCKED"
    assert any(iss.issue_code == "ERR_VFR_UNSUPPORTED" for iss in result.issues)


# ==============================================================================
# F05: Ngữ nghĩa Global Hook
# ==============================================================================

def test_f05_hook_absent_or_none():
    """F05a: Không có global_hook -> chạy bình thường."""
    raw_prop = _make_minimal_proposal_dict(
        manifest_id="mf_no_hook",
        proposal_id="prop_no_hook",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        timeline_segments=[{"clip_id": "c1", "start_sec": 0.0, "end_sec": 2.0}],
        global_hook=None,
    )
    ev_table = {
        "c1": SourceFrameEvidence("mf_no_hook", "c1", RationalFPS(25, 1), 125, "VERIFIED_CFR"),
    }
    res = assemble_plan_frame_geometry(raw_prop, ev_table, RationalFPS(25, 1))
    assert res.status == "GEOMETRY_VERIFIED"
    assert len(res.assembled_segments) == 1
    assert res.assembled_segments[0].origin == "timeline_segment"


def test_f05_hook_ambiguity_missing_placement_mode():
    """F05b: Có global_hook nhưng thiếu placement_mode:
    - Báo ERR_HOOK_PLACEMENT_AMBIGUITY (WARNING) -> GEOMETRY_UNRESOLVED.
    - Có mục unresolved_reviews loại HOOK_PLACEMENT.
    """
    raw_prop = _make_minimal_proposal_dict(
        manifest_id="mf_hook_ambig",
        proposal_id="prop_hook_ambig",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        timeline_segments=[{"clip_id": "c1", "start_sec": 0.0, "end_sec": 2.0}],
        global_hook={"clip_id": "c1", "start_sec": 3.0, "end_sec": 4.0},  # Thiếu placement_mode!
    )
    ev_table = {
        "c1": SourceFrameEvidence("mf_hook_ambig", "c1", RationalFPS(25, 1), 125, "VERIFIED_CFR"),
    }
    res = assemble_plan_frame_geometry(raw_prop, ev_table, RationalFPS(25, 1))
    assert res.status == "GEOMETRY_UNRESOLVED"
    assert any(iss.issue_code == "ERR_HOOK_PLACEMENT_AMBIGUITY" for iss in res.issues)
    assert any(rev.category == "HOOK_PLACEMENT" for rev in res.unresolved_reviews)


def test_f05_hook_intro_cut_placement():
    """F05c: global_hook với placement_mode == 'intro_cut':
    - Được chèn lên đầu tiên (origin='global_hook', segment_index=0, source_segment_index=None).
    - Đoạn A-Roll tiếp theo bắt đầu ngay sau điểm kết thúc của hook.
    """
    raw_prop = _make_minimal_proposal_dict(
        manifest_id="mf_hook_intro",
        proposal_id="prop_hook_intro",
        clips=[
            {"clip_id": "c_hook", "duration_sec": 5.0, "fps": 25.0},
            {"clip_id": "c_main", "duration_sec": 5.0, "fps": 25.0},
        ],
        timeline_segments=[{"clip_id": "c_main", "start_sec": 0.0, "end_sec": 2.0}],
        global_hook={
            "clip_id": "c_hook",
            "start_sec": 1.0,
            "end_sec": 2.0,
            "placement_mode": "intro_cut",
        },
    )
    ev_table = {
        "c_hook": SourceFrameEvidence("mf_hook_intro", "c_hook", RationalFPS(25, 1), 125, "VERIFIED_CFR"),
        "c_main": SourceFrameEvidence("mf_hook_intro", "c_main", RationalFPS(25, 1), 125, "VERIFIED_CFR"),
    }
    res = assemble_plan_frame_geometry(raw_prop, ev_table, RationalFPS(25, 1))

    assert res.status == "GEOMETRY_VERIFIED"
    assert len(res.assembled_segments) == 2
    # Seg 0 là intro hook: [1.0, 2.0) = 25 frames -> range [0, 25)
    s0 = res.assembled_segments[0]
    assert s0.origin == "global_hook"
    assert s0.segment_index == 0
    assert s0.source_segment_index is None
    assert s0.clip_id == "c_hook"
    assert s0.timeline_range == TimeRangeFrames(0, 25)

    # Seg 1 là main segment: [0.0, 2.0) = 50 frames -> range [25, 75)
    s1 = res.assembled_segments[1]
    assert s1.origin == "timeline_segment"
    assert s1.segment_index == 1
    assert s1.source_segment_index == 0
    assert s1.clip_id == "c_main"
    assert s1.timeline_range == TimeRangeFrames(25, 75)

    assert res.total_timeline_frames == 75


def test_f05_hook_highlight_ref_contained_vs_uncontained():
    """F05d: global_hook với placement_mode == 'highlight_ref':
    - Nếu nằm trọn trong 1 segment keep: thành công, không tăng cursor (0 extra frames).
    - Nếu không nằm trọn: báo lỗi ERR_HOOK_HIGHLIGHT_NOT_CONTAINED -> GEOMETRY_BLOCKED.
    """
    # 1. Contained
    raw_prop_ok = _make_minimal_proposal_dict(
        manifest_id="mf_hook_ref",
        proposal_id="prop_hook_ref",
        clips=[{"clip_id": "c1", "duration_sec": 10.0, "fps": 25.0}],
        timeline_segments=[{"clip_id": "c1", "start_sec": 1.0, "end_sec": 5.0, "action": "keep"}],
        global_hook={
            "clip_id": "c1",
            "start_sec": 2.0,
            "end_sec": 3.0,
            "placement_mode": "highlight_ref",
        },
    )
    ev_table = {
        "c1": SourceFrameEvidence("mf_hook_ref", "c1", RationalFPS(25, 1), 250, "VERIFIED_CFR"),
    }
    res_ok = assemble_plan_frame_geometry(raw_prop_ok, ev_table, RationalFPS(25, 1))
    assert res_ok.status == "GEOMETRY_VERIFIED"
    assert len(res_ok.assembled_segments) == 1
    assert res_ok.total_timeline_frames == 100  # 4s * 25fps = 100 frames, hook không thêm frame

    # 2. Uncontained -> BLOCKED
    raw_prop_bad = _make_minimal_proposal_dict(
        manifest_id="mf_hook_ref",
        proposal_id="prop_hook_ref",
        clips=[{"clip_id": "c1", "duration_sec": 10.0, "fps": 25.0}],
        timeline_segments=[{"clip_id": "c1", "start_sec": 1.0, "end_sec": 2.0}],  # Hook [2.0, 3.0) không nằm trong [1.0, 2.0)
        global_hook={
            "clip_id": "c1",
            "start_sec": 2.0,
            "end_sec": 3.0,
            "placement_mode": "highlight_ref",
        },
    )
    res_bad = assemble_plan_frame_geometry(raw_prop_bad, ev_table, RationalFPS(25, 1))
    assert res_bad.status == "GEOMETRY_BLOCKED"
    assert any(iss.issue_code == "ERR_HOOK_HIGHLIGHT_NOT_CONTAINED" for iss in res_bad.issues)


# ==============================================================================
# Rà soát CreativeContext & Omissions D02
# ==============================================================================

def test_creative_context_and_omissions_extraction():
    """Kiểm tra trích xuất chính xác must_keep_rules, must_not_rules và proposed_omissions."""
    raw_prop = _make_minimal_proposal_dict(
        manifest_id="mf_rules",
        proposal_id="prop_rules",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        timeline_segments=[{"clip_id": "c1", "start_sec": 0.0, "end_sec": 2.0}],
        must_keep_rules=["Không được cắt câu chào đầu", "Giữ lại tiếng vỗ tay"],
        must_not_rules=["Không dùng nhạc nền quá lớn"],
        proposed_omissions=[
            {
                "demand_id": "demand_omit_01",
                "slot": "broll",
                "timeline_sec": 1.0,
                "duration_sec": 2.0,
                "reason": "Missing 4k file",
                "warning_message": "File was missing during render",
            }
        ],
    )
    ev_table = {
        "c1": SourceFrameEvidence("mf_rules", "c1", RationalFPS(25, 1), 125, "VERIFIED_CFR"),
    }
    res = assemble_plan_frame_geometry(raw_prop, ev_table, RationalFPS(25, 1))

    assert res.status == "GEOMETRY_VERIFIED"
    assert len(res.unresolved_reviews) == 4
    categories = [r.category for r in res.unresolved_reviews]
    assert categories.count("SEMANTIC_MUST_KEEP") == 2
    assert categories.count("SEMANTIC_MUST_NOT") == 1
    assert categories.count("SUPPLEMENTAL_OMISSION") == 1


# ==============================================================================
# Ranh giới API, giới hạn bảng 4096, đột biến & Purity Spies
# ==============================================================================

def test_structural_api_misuse_denials():
    """Kiểm tra bắt lỗi các sai phạm cấu trúc API."""
    ev = SourceFrameEvidence("mf_01", "c1", RationalFPS(25, 1), 100, "VERIFIED_CFR")

    # 1. proposal không phải Mapping/EditPlanProposal
    with pytest.raises(ValueError, match="ERR_INVALID_MAPPING"):
        assemble_plan_frame_geometry("not_a_proposal", {"c1": ev}, RationalFPS(25, 1))

    raw_prop = _make_minimal_proposal_dict(manifest_id="mf_01", clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}])

    # 2. evidence_table không phải Mapping
    with pytest.raises(ValueError, match="ERR_INVALID_MAPPING"):
        assemble_plan_frame_geometry(raw_prop, ["not_a_map"], RationalFPS(25, 1))

    # 3. Khóa mapping không khớp clip_id
    with pytest.raises(ValueError, match="ERR_CLIP_ID_MISMATCH"):
        assemble_plan_frame_geometry(raw_prop, {"wrong_key": ev}, RationalFPS(25, 1))

    # 4. manifest_id trong evidence không khớp proposal manifest_id
    ev_mismatch = SourceFrameEvidence("mf_other", "c1", RationalFPS(25, 1), 100, "VERIFIED_CFR")
    with pytest.raises(ValueError, match="ERR_MANIFEST_MISMATCH"):
        assemble_plan_frame_geometry(raw_prop, {"c1": ev_mismatch}, RationalFPS(25, 1))

    # 5. timeline_fps sai kiểu
    with pytest.raises(ValueError, match="ERR_INVALID_TIMELINE_FPS"):
        assemble_plan_frame_geometry(raw_prop, {"c1": ev}, "24")


def test_evidence_table_limit_4096():
    """evidence_table vượt quá trần 4096 mục phải ném ERR_COUNT_OVERFLOW trước khi duyệt."""
    raw_prop = _make_minimal_proposal_dict()
    # Tạo mapping giả có 4097 phần tử
    huge_table = {f"c_{i}": None for i in range(4097)}
    with pytest.raises(ValueError, match="ERR_COUNT_OVERFLOW"):
        assemble_plan_frame_geometry(raw_prop, huge_table, RationalFPS(25, 1))


def test_defensive_mutation_isolation():
    """Mutation trên input collection hoặc DTO sau khi gọi hàm không làm thay đổi kết quả."""
    raw_prop = _make_minimal_proposal_dict(
        manifest_id="mf_mut",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        timeline_segments=[{"clip_id": "c1", "start_sec": 0.0, "end_sec": 2.0}],
    )
    ev_table = {
        "c1": SourceFrameEvidence("mf_mut", "c1", RationalFPS(25, 1), 125, "VERIFIED_CFR"),
    }
    res = assemble_plan_frame_geometry(raw_prop, ev_table, RationalFPS(25, 1))

    # Mutate raw_prop và ev_table
    raw_prop["video_plan"]["timeline_segments"].append({"clip_id": "c1", "start_sec": 2.0, "end_sec": 4.0})
    ev_table["c1"] = None

    # Khẳng định result không bị ảnh hưởng
    assert res.status == "GEOMETRY_VERIFIED"
    assert len(res.assembled_segments) == 1
    assert res.total_timeline_frames == 50

    # Khẳng định result là bất biến
    with pytest.raises(Exception):
        res.assembled_segments = ()
    with pytest.raises(Exception):
        res.status = "GEOMETRY_BLOCKED"


def test_zero_io_network_clock_purity():
    """Khẳng định hàm assemble_plan_frame_geometry hoàn toàn thuần túy (zero I/O, zero clock, zero network)."""
    raw_prop = _make_minimal_proposal_dict(
        manifest_id="mf_purity",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        timeline_segments=[{"clip_id": "c1", "start_sec": 0.0, "end_sec": 2.0}],
    )
    ev_table = {
        "c1": SourceFrameEvidence("mf_purity", "c1", RationalFPS(25, 1), 125, "VERIFIED_CFR"),
    }

    with patch("builtins.open", side_effect=AssertionError("Không được thực hiện I/O tệp!")):
        with patch("subprocess.Popen", side_effect=AssertionError("Không được gọi tiến trình con!")):
            with patch("socket.socket", side_effect=AssertionError("Không được gọi mạng!")):
                res = assemble_plan_frame_geometry(raw_prop, ev_table, RationalFPS(25, 1))

    assert res.status == "GEOMETRY_VERIFIED"
    assert res.total_timeline_frames == 50


# ==============================================================================
# G06-06C2d-R1: Nhóm A — Tái kiểm tra & sở hữu trọn vẹn evidence ở ranh giới đầu vào
# ==============================================================================

def test_probe_mutated_evidence_status_str_rejected():
    """Probe 1: evidence_status bị sửa lậu thành chuỗi lạ (e.g. 'BOGUS') phải bị từ chối, không lọt GEOMETRY_VERIFIED 0 frames."""
    ev = SourceFrameEvidence("mf_test", "c1", RationalFPS(25, 1), 100, "VERIFIED_CFR")
    object.__setattr__(ev, "evidence_status", "BOGUS")
    raw_prop = _make_minimal_proposal_dict(manifest_id="mf_test", clips=[{"clip_id": "c1", "duration_sec": 4.0, "fps": 25.0}])
    with pytest.raises(ValueError, match="ERR_INVALID_EVIDENCE_STATUS"):
        assemble_plan_frame_geometry(raw_prop, {"c1": ev}, RationalFPS(25, 1))


def test_probe_mutated_evidence_status_none_and_list_rejected():
    """Probe 2 & 3: evidence_status bị gán None, list hoặc dict phải bị chặn bằng ValueError, không fall-through."""
    raw_prop = _make_minimal_proposal_dict(manifest_id="mf_test", clips=[{"clip_id": "c1", "duration_sec": 4.0, "fps": 25.0}])

    ev_none = SourceFrameEvidence("mf_test", "c1", RationalFPS(25, 1), 100, "VERIFIED_CFR")
    object.__setattr__(ev_none, "evidence_status", None)
    with pytest.raises(ValueError, match="ERR_INVALID_EVIDENCE_STATUS"):
        assemble_plan_frame_geometry(raw_prop, {"c1": ev_none}, RationalFPS(25, 1))

    ev_list = SourceFrameEvidence("mf_test", "c1", RationalFPS(25, 1), 100, "VERIFIED_CFR")
    object.__setattr__(ev_list, "evidence_status", ["VERIFIED_CFR"])
    with pytest.raises(ValueError, match="ERR_INVALID_EVIDENCE_STATUS"):
        assemble_plan_frame_geometry(raw_prop, {"c1": ev_list}, RationalFPS(25, 1))

    ev_dict = SourceFrameEvidence("mf_test", "c1", RationalFPS(25, 1), 100, "VERIFIED_CFR")
    object.__setattr__(ev_dict, "evidence_status", {"status": "VERIFIED_CFR"})
    with pytest.raises(ValueError, match="ERR_INVALID_EVIDENCE_STATUS"):
        assemble_plan_frame_geometry(raw_prop, {"c1": ev_dict}, RationalFPS(25, 1))


def test_probe_deleted_evidence_status_controlled_error():
    """Probe 4: Xóa thuộc tính evidence_status phải ném controlled ValueError, tuyệt đối không rò rỉ AttributeError."""
    ev = SourceFrameEvidence("mf_test", "c1", RationalFPS(25, 1), 100, "VERIFIED_CFR")
    del ev.__dict__["evidence_status"]
    raw_prop = _make_minimal_proposal_dict(manifest_id="mf_test", clips=[{"clip_id": "c1", "duration_sec": 4.0, "fps": 25.0}])
    with pytest.raises(ValueError, match="ERR_MALFORMED_EVIDENCE"):
        assemble_plan_frame_geometry(raw_prop, {"c1": ev}, RationalFPS(25, 1))


def test_probe_deleted_other_attributes_controlled_error():
    """Xóa bất kỳ thuộc tính nào trong 5 thuộc tính bắt buộc của SourceFrameEvidence đều ném ERR_MALFORMED_EVIDENCE."""
    for attr in ("manifest_id", "clip_id", "source_fps", "total_frames"):
        ev = SourceFrameEvidence("mf_test", "c1", RationalFPS(25, 1), 100, "VERIFIED_CFR")
        del ev.__dict__[attr]
        raw_prop = _make_minimal_proposal_dict(manifest_id="mf_test", clips=[{"clip_id": "c1", "duration_sec": 4.0, "fps": 25.0}])
        with pytest.raises(ValueError, match="ERR_MALFORMED_EVIDENCE"):
            assemble_plan_frame_geometry(raw_prop, {"c1": ev}, RationalFPS(25, 1))


def test_probe_unknown_clip_in_evidence_table_rejected():
    """Bảng evidence_table chứa clip lạ không nằm trong proposal.source_binding.clips phải bị từ chối bằng ERR_UNKNOWN_CLIP_ID."""
    raw_prop = _make_minimal_proposal_dict(
        manifest_id="mf_test",
        clips=[{"clip_id": "c1", "duration_sec": 4.0, "fps": 25.0}],
    )
    ev_c1 = SourceFrameEvidence("mf_test", "c1", RationalFPS(25, 1), 100, "VERIFIED_CFR")
    ev_c2 = SourceFrameEvidence("mf_test", "c2", RationalFPS(25, 1), 100, "VERIFIED_CFR")
    with pytest.raises(ValueError, match="ERR_UNKNOWN_CLIP_ID"):
        assemble_plan_frame_geometry(raw_prop, {"c1": ev_c1, "c2": ev_c2}, RationalFPS(25, 1))


# ==============================================================================
# G06-06C2d-R1: Nhóm B — Thẩm định hook nghiêm ngặt & đồng nhất thẩm quyền nguồn
# ==============================================================================

def test_probe_hook_mode_mapping_controlled_blocked():
    """Probe 5: placement_mode của global_hook là dict, list, bool hoặc chuỗi lạ không được rò rỉ TypeError mà phải ghi nhận lỗi BLOCKING."""
    ev_table = {"c1": SourceFrameEvidence("mf_hook", "c1", RationalFPS(25, 1), 125, "VERIFIED_CFR")}

    for bad_mode in ({"mode": "intro_cut"}, ["intro_cut"], True, "unknown_mode"):
        raw_prop = _make_minimal_proposal_dict(
            manifest_id="mf_hook",
            clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
            timeline_segments=[{"clip_id": "c1", "start_sec": 0.0, "end_sec": 2.0}],
            global_hook={
                "clip_id": "c1",
                "start_sec": 0.0,
                "end_sec": 1.0,
                "placement_mode": bad_mode,
            },
        )
        res = assemble_plan_frame_geometry(raw_prop, ev_table, RationalFPS(25, 1))
        assert res.status == "GEOMETRY_BLOCKED"
        assert res.assembled_segments == ()
        assert any(iss.issue_code == "ERR_INVALID_HOOK_PLACEMENT_MODE" and iss.severity == "BLOCKING" for iss in res.issues)


def test_probe_hook_id_wrong_type_blocked():
    """Hook clip_id có kiểu không phải str (e.g. 123, list, dict) phải bị BLOCKING ERR_INVALID_CLIP_ID, không format ID giả."""
    ev_table = {"c1": SourceFrameEvidence("mf_hook", "c1", RationalFPS(25, 1), 125, "VERIFIED_CFR")}
    for bad_cid in (123, ["c1"], {"id": "c1"}):
        raw_prop = _make_minimal_proposal_dict(
            manifest_id="mf_hook",
            clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
            global_hook={"clip_id": bad_cid, "start_sec": 0.0, "end_sec": 1.0, "placement_mode": "intro_cut"},
        )
        res = assemble_plan_frame_geometry(raw_prop, ev_table, RationalFPS(25, 1))
        assert res.status == "GEOMETRY_BLOCKED"
        assert any(iss.issue_code == "ERR_INVALID_CLIP_ID" for iss in res.issues)


def test_probe_hook_missing_required_fields_controlled():
    """Probe 6: global_hook thiếu clip_id, start_sec hoặc end_sec phải báo ERR_MISSING_HOOK_FIELD (BLOCKING), không rò rỉ KeyError."""
    ev_table = {"c1": SourceFrameEvidence("mf_hook", "c1", RationalFPS(25, 1), 125, "VERIFIED_CFR")}

    # Thiếu start_sec
    raw_prop1 = _make_minimal_proposal_dict(
        manifest_id="mf_hook",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        global_hook={"clip_id": "c1", "end_sec": 1.0, "placement_mode": "intro_cut"},
    )
    res1 = assemble_plan_frame_geometry(raw_prop1, ev_table, RationalFPS(25, 1))
    assert res1.status == "GEOMETRY_BLOCKED"
    assert any(iss.issue_code == "ERR_MISSING_HOOK_FIELD" for iss in res1.issues)

    # Thiếu end_sec
    raw_prop2 = _make_minimal_proposal_dict(
        manifest_id="mf_hook",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        global_hook={"clip_id": "c1", "start_sec": 0.0, "placement_mode": "intro_cut"},
    )
    res2 = assemble_plan_frame_geometry(raw_prop2, ev_table, RationalFPS(25, 1))
    assert res2.status == "GEOMETRY_BLOCKED"
    assert any(iss.issue_code == "ERR_MISSING_HOOK_FIELD" for iss in res2.issues)

    # Thiếu clip_id
    raw_prop3 = _make_minimal_proposal_dict(
        manifest_id="mf_hook",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        global_hook={"start_sec": 0.0, "end_sec": 1.0, "placement_mode": "intro_cut"},
    )
    res3 = assemble_plan_frame_geometry(raw_prop3, ev_table, RationalFPS(25, 1))
    assert res3.status == "GEOMETRY_BLOCKED"
    assert any(iss.issue_code == "ERR_MISSING_HOOK_FIELD" for iss in res3.issues)


def test_probe_hook_unknown_source_blocked():
    """Probe 7: Hook trỏ vào clip không có trong proposal.source_binding.clips phải bị BLOCKING ERR_UNKNOWN_CLIP_ID."""
    raw_prop = _make_minimal_proposal_dict(
        manifest_id="mf_hook",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        global_hook={"clip_id": "c2_unauthorized", "start_sec": 0.0, "end_sec": 1.0, "placement_mode": "intro_cut"},
    )
    ev_table = {"c1": SourceFrameEvidence("mf_hook", "c1", RationalFPS(25, 1), 125, "VERIFIED_CFR")}
    res = assemble_plan_frame_geometry(raw_prop, ev_table, RationalFPS(25, 1))
    assert res.status == "GEOMETRY_BLOCKED"
    assert res.assembled_segments == ()
    assert any(iss.issue_code == "ERR_UNKNOWN_CLIP_ID" for iss in res.issues)


def test_probe_hook_reference_item_leak_blocked():
    """Hook tham chiếu tới reference_id trong creative_context.reference_items bị chặn bằng ERR_REFERENCE_CLIP_NOT_FOOTAGE."""
    raw_prop = _make_minimal_proposal_dict(
        manifest_id="mf_hook",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        global_hook={"clip_id": "ref_audio_01", "start_sec": 0.0, "end_sec": 1.0, "placement_mode": "intro_cut"},
    )
    raw_prop["creative_context"]["reference_items"].append({
        "reference_id": "ref_audio_01",
        "local_path": "/tmp/ref.mp4",
        "media_type": "video",
        "role": "reference_only",
        "user_note": "Background reference track",
    })
    ev_table = {"c1": SourceFrameEvidence("mf_hook", "c1", RationalFPS(25, 1), 125, "VERIFIED_CFR")}
    res = assemble_plan_frame_geometry(raw_prop, ev_table, RationalFPS(25, 1))
    assert res.status == "GEOMETRY_BLOCKED"
    assert any(iss.issue_code == "ERR_REFERENCE_CLIP_NOT_FOOTAGE" for iss in res.issues)


def test_probe_hook_reverse_and_zero_range_blocked():
    """Probe 8: Hook có start >= end (khoảng ngược hoặc 0) phải bị BLOCKING ERR_INVALID_TIME_RANGE."""
    ev_table = {"c1": SourceFrameEvidence("mf_hook", "c1", RationalFPS(25, 1), 125, "VERIFIED_CFR")}

    raw_prop_rev = _make_minimal_proposal_dict(
        manifest_id="mf_hook",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        timeline_segments=[{"clip_id": "c1", "start_sec": 1.0, "end_sec": 2.5}],
        global_hook={"clip_id": "c1", "start_sec": 1.9, "end_sec": 1.1, "placement_mode": "highlight_ref"},
    )
    res_rev = assemble_plan_frame_geometry(raw_prop_rev, ev_table, RationalFPS(25, 1))
    assert res_rev.status == "GEOMETRY_BLOCKED"
    assert any(iss.issue_code == "ERR_INVALID_TIME_RANGE" for iss in res_rev.issues)


def test_probe_hook_string_and_bool_times_blocked():
    """Probe 9 & 10: Hook start_sec/end_sec kiểu string hoặc bool phải bị từ chối bằng ERR_INVALID_NUMBER, cấm float coercion."""
    ev_table = {"c1": SourceFrameEvidence("mf_hook", "c1", RationalFPS(25, 1), 125, "VERIFIED_CFR")}

    # String times
    raw_prop_str = _make_minimal_proposal_dict(
        manifest_id="mf_hook",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        global_hook={"clip_id": "c1", "start_sec": "0.0", "end_sec": "1.0", "placement_mode": "intro_cut"},
    )
    res_str = assemble_plan_frame_geometry(raw_prop_str, ev_table, RationalFPS(25, 1))
    assert res_str.status == "GEOMETRY_BLOCKED"
    assert any(iss.issue_code == "ERR_INVALID_NUMBER" for iss in res_str.issues)

    # Bool times
    raw_prop_bool = _make_minimal_proposal_dict(
        manifest_id="mf_hook",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        global_hook={"clip_id": "c1", "start_sec": False, "end_sec": True, "placement_mode": "intro_cut"},
    )
    res_bool = assemble_plan_frame_geometry(raw_prop_bool, ev_table, RationalFPS(25, 1))
    assert res_bool.status == "GEOMETRY_BLOCKED"
    assert any(iss.issue_code == "ERR_INVALID_NUMBER" for iss in res_bool.issues)


def test_probe_hook_speed_invalid_blocked():
    """Hook speed là bool hoặc string hoặc <= 0 phải bị BLOCKING ERR_INVALID_SPEED."""
    ev_table = {"c1": SourceFrameEvidence("mf_hook", "c1", RationalFPS(25, 1), 125, "VERIFIED_CFR")}

    raw_prop_spd = _make_minimal_proposal_dict(
        manifest_id="mf_hook",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        global_hook={"clip_id": "c1", "start_sec": 0.0, "end_sec": 1.0, "speed": True, "placement_mode": "intro_cut"},
    )
    res_spd = assemble_plan_frame_geometry(raw_prop_spd, ev_table, RationalFPS(25, 1))
    assert res_spd.status == "GEOMETRY_BLOCKED"
    assert any(iss.issue_code == "ERR_INVALID_SPEED" for iss in res_spd.issues)


def test_probe_hook_ambiguity_only_when_otherwise_valid():
    """global_hook chỉ giữ UNRESOLVED (AMBIGUITY) khi các trường hình học hợp lệ; nếu sai start/end thì phải là BLOCKED."""
    ev_table = {"c1": SourceFrameEvidence("mf_hook", "c1", RationalFPS(25, 1), 125, "VERIFIED_CFR")}

    # Hợp lệ nhưng thiếu placement_mode -> UNRESOLVED
    raw_prop_valid = _make_minimal_proposal_dict(
        manifest_id="mf_hook",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        global_hook={"clip_id": "c1", "start_sec": 0.0, "end_sec": 1.0},
    )
    res_valid = assemble_plan_frame_geometry(raw_prop_valid, ev_table, RationalFPS(25, 1))
    assert res_valid.status == "GEOMETRY_UNRESOLVED"
    assert any(iss.issue_code == "ERR_HOOK_PLACEMENT_AMBIGUITY" for iss in res_valid.issues)

    # Thiếu placement_mode nhưng thời gian ngược -> BLOCKED (không để lỗi cấu trúc đội lốt tính mơ hồ)
    raw_prop_invalid = _make_minimal_proposal_dict(
        manifest_id="mf_hook",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        global_hook={"clip_id": "c1", "start_sec": 2.0, "end_sec": 1.0},
    )
    res_invalid = assemble_plan_frame_geometry(raw_prop_invalid, ev_table, RationalFPS(25, 1))
    assert res_invalid.status == "GEOMETRY_BLOCKED"
    assert not any(iss.issue_code == "ERR_HOOK_PLACEMENT_AMBIGUITY" for iss in res_invalid.issues)


# ==============================================================================
# G06-06C2d-R1: Nhóm C — Sở hữu DTO sâu, ràng buộc bản ghi & kiểm tra chuỗi UTF-8
# ==============================================================================

def test_probe_result_wrong_total_frames_rejected():
    """Probe 11: Kết quả GEOMETRY_VERIFIED với total_timeline_frames sai lệch (e.g. 999) phải bị từ chối bằng ERR_TOTAL_FRAMES_MISMATCH."""
    span = QuantizedCFRSpan(
        source_fps=RationalFPS(24, 1),
        timeline_fps=RationalFPS(24, 1),
        source_range=TimeRangeFrames(0, 24),
        total_source_frames=100,
        record_duration_frames=24,
        applied_speed=Fraction(1, 1),
    )
    seg = PlanTimelineSegmentGeometry(
        segment_index=0,
        origin="timeline_segment",
        source_segment_index=0,
        clip_id="c1",
        quantized_span=span,
        timeline_range=TimeRangeFrames(0, 24),
    )

    with pytest.raises(ValueError, match="ERR_TOTAL_FRAMES_MISMATCH"):
        PlanGeometryAssemblyResult(
            status="GEOMETRY_VERIFIED",
            proposal_id="p1",
            proposal_revision="rev1",
            timeline_fps=RationalFPS(24, 1),
            assembled_segments=(seg,),
            total_timeline_frames=999,
            total_timeline_duration_sec=Fraction(999, 24),
            issues=(),
            unresolved_reviews=(),
        )


def test_probe_result_cursor_not_zero_rejected():
    """Probe 12: Phân đoạn đầu tiên không bắt đầu tại cursor 0 phải bị từ chối bằng ERR_CURSOR_START_NONZERO."""
    span = QuantizedCFRSpan(
        source_fps=RationalFPS(24, 1),
        timeline_fps=RationalFPS(24, 1),
        source_range=TimeRangeFrames(0, 24),
        total_source_frames=100,
        record_duration_frames=24,
        applied_speed=Fraction(1, 1),
    )
    seg = PlanTimelineSegmentGeometry(
        segment_index=0,
        origin="timeline_segment",
        source_segment_index=0,
        clip_id="c1",
        quantized_span=span,
        timeline_range=TimeRangeFrames(10, 34),
    )

    with pytest.raises(ValueError, match="ERR_CURSOR_START_NONZERO"):
        PlanGeometryAssemblyResult(
            status="GEOMETRY_VERIFIED",
            proposal_id="p1",
            proposal_revision="rev1",
            timeline_fps=RationalFPS(24, 1),
            assembled_segments=(seg,),
            total_timeline_frames=24,
            total_timeline_duration_sec=Fraction(24, 24),
            issues=(),
            unresolved_reviews=(),
        )


def test_probe_result_timeline_discontinuity_and_segment_index_mismatch_rejected():
    """Ràng buộc tính liên tục (không đè / không hở) và thứ tự segment_index 0..N-1."""
    span = QuantizedCFRSpan(
        source_fps=RationalFPS(24, 1),
        timeline_fps=RationalFPS(24, 1),
        source_range=TimeRangeFrames(0, 24),
        total_source_frames=100,
        record_duration_frames=24,
        applied_speed=Fraction(1, 1),
    )
    seg0 = PlanTimelineSegmentGeometry(0, "timeline_segment", 0, "c1", span, TimeRangeFrames(0, 24))

    # Hở khoảng cách (gap): seg0 kết thúc tại 24 nhưng seg1 bắt đầu tại 30
    seg1_gap = PlanTimelineSegmentGeometry(1, "timeline_segment", 1, "c1", span, TimeRangeFrames(30, 54))
    with pytest.raises(ValueError, match="ERR_TIMELINE_DISCONTINUITY"):
        PlanGeometryAssemblyResult(
            status="GEOMETRY_VERIFIED",
            proposal_id="p1",
            proposal_revision="rev1",
            timeline_fps=RationalFPS(24, 1),
            assembled_segments=(seg0, seg1_gap),
            total_timeline_frames=48,
            total_timeline_duration_sec=Fraction(48, 24),
            issues=(),
            unresolved_reviews=(),
        )

    # Sai chỉ số segment_index (ví dụ: seg1 có segment_index=5)
    seg1_wrong_idx = PlanTimelineSegmentGeometry(5, "timeline_segment", 1, "c1", span, TimeRangeFrames(24, 48))
    with pytest.raises(ValueError, match="ERR_SEGMENT_INDEX_MISMATCH"):
        PlanGeometryAssemblyResult(
            status="GEOMETRY_VERIFIED",
            proposal_id="p1",
            proposal_revision="rev1",
            timeline_fps=RationalFPS(24, 1),
            assembled_segments=(seg0, seg1_wrong_idx),
            total_timeline_frames=48,
            total_timeline_duration_sec=Fraction(48, 24),
            issues=(),
            unresolved_reviews=(),
        )

    # Lệch timeline_fps giữa phân đoạn và kết quả timeline (ví dụ: seg có timeline_fps 30/1 nhưng result 24/1)
    span_30 = QuantizedCFRSpan(
        source_fps=RationalFPS(30, 1),
        timeline_fps=RationalFPS(30, 1),
        source_range=TimeRangeFrames(0, 30),
        total_source_frames=100,
        record_duration_frames=30,
        applied_speed=Fraction(1, 1),
    )
    seg_30 = PlanTimelineSegmentGeometry(0, "timeline_segment", 0, "c1", span_30, TimeRangeFrames(0, 30))
    with pytest.raises(ValueError, match="ERR_FPS_MISMATCH"):
        PlanGeometryAssemblyResult(
            status="GEOMETRY_VERIFIED",
            proposal_id="p1",
            proposal_revision="rev1",
            timeline_fps=RationalFPS(24, 1),
            assembled_segments=(seg_30,),
            total_timeline_frames=30,
            total_timeline_duration_sec=Fraction(30, 24),
            issues=(),
            unresolved_reviews=(),
        )


def test_probe_sequence_bounds_before_iteration_rejected():
    """Kiểm tra giới hạn kích thước danh sách trước khi duyệt (assembled_segments <= 257, issues <= 1024, review_items <= 512)."""
    # 258 segments vượt quá 257
    dummy_segments = [None] * 258
    with pytest.raises(ValueError, match="ERR_COUNT_OVERFLOW"):
        PlanGeometryAssemblyResult(
            status="GEOMETRY_BLOCKED",
            proposal_id="p1",
            proposal_revision="rev1",
            timeline_fps=RationalFPS(24, 1),
            assembled_segments=tuple(dummy_segments),
            total_timeline_frames=None,
            total_timeline_duration_sec=None,
            issues=(),
            unresolved_reviews=(),
        )

    # 1025 issues vượt quá 1024
    dummy_issues = [None] * 1025
    with pytest.raises(ValueError, match="ERR_COUNT_OVERFLOW"):
        PlanGeometryAssemblyResult(
            status="GEOMETRY_BLOCKED",
            proposal_id="p1",
            proposal_revision="rev1",
            timeline_fps=RationalFPS(24, 1),
            assembled_segments=(),
            total_timeline_frames=None,
            total_timeline_duration_sec=None,
            issues=tuple(dummy_issues),
            unresolved_reviews=(),
        )

    # 513 review items vượt quá 512
    dummy_reviews = [None] * 513
    with pytest.raises(ValueError, match="ERR_COUNT_OVERFLOW"):
        PlanGeometryAssemblyResult(
            status="GEOMETRY_BLOCKED",
            proposal_id="p1",
            proposal_revision="rev1",
            timeline_fps=RationalFPS(24, 1),
            assembled_segments=(),
            total_timeline_frames=None,
            total_timeline_duration_sec=None,
            issues=(),
            unresolved_reviews=tuple(dummy_reviews),
        )


def test_probe_result_nested_alias_mutation_isolation():
    """Probe 13: Đột biến đối tượng con sau khi khởi tạo kết quả không được ảnh hưởng tới DTO đã sở hữu."""
    span = QuantizedCFRSpan(
        source_fps=RationalFPS(24, 1),
        timeline_fps=RationalFPS(24, 1),
        source_range=TimeRangeFrames(0, 24),
        total_source_frames=100,
        record_duration_frames=24,
        applied_speed=Fraction(1, 1),
    )
    caller_seg = PlanTimelineSegmentGeometry(
        segment_index=0,
        origin="timeline_segment",
        source_segment_index=0,
        clip_id="c1",
        quantized_span=span,
        timeline_range=TimeRangeFrames(0, 24),
    )
    res = PlanGeometryAssemblyResult(
        status="GEOMETRY_VERIFIED",
        proposal_id="p1",
        proposal_revision="rev1",
        timeline_fps=RationalFPS(24, 1),
        assembled_segments=(caller_seg,),
        total_timeline_frames=24,
        total_timeline_duration_sec=Fraction(24, 24),
        issues=(),
        unresolved_reviews=(),
    )

    # Phân đoạn bên trong res phải là bản sao sâu được tái tạo mới, không chung identity với caller_seg
    owned_seg = res.assembled_segments[0]
    assert owned_seg is not caller_seg
    assert owned_seg.quantized_span is not caller_seg.quantized_span
    assert owned_seg.timeline_range is not caller_seg.timeline_range


def test_probe_string_surrogate_and_nul_rejected():
    """Probe 14 & 15: Chuỗi chứa lone surrogate hoặc ký tự NUL phải bị từ chối bằng lỗi kiểm soát."""
    # Lone surrogate
    with pytest.raises(ValueError, match="ERR_INVALID_UTF8"):
        SourceFrameEvidence("mf_test", "clip_\ud800", RationalFPS(25, 1), 100, "VERIFIED_CFR")

    # NUL character
    with pytest.raises(ValueError, match="ERR_INVALID_STRING"):
        PlanGeometryIssue(severity="BLOCKING", issue_code="ERR_TEST", clip_id="c1", message="Error with \x00 character")


def test_probe_global_hook_source_segment_index_must_be_none():
    """Phân đoạn có origin='global_hook' bắt buộc phải có source_segment_index=None."""
    span = QuantizedCFRSpan(
        source_fps=RationalFPS(24, 1),
        timeline_fps=RationalFPS(24, 1),
        source_range=TimeRangeFrames(0, 24),
        total_source_frames=100,
        record_duration_frames=24,
        applied_speed=Fraction(1, 1),
    )
    with pytest.raises(ValueError, match="ERR_INVALID_SOURCE_SEGMENT_INDEX"):
        PlanTimelineSegmentGeometry(
            segment_index=0,
            origin="global_hook",
            source_segment_index=0,  # Không được phép khác None
            clip_id="c1",
            quantized_span=span,
            timeline_range=TimeRangeFrames(0, 24),
        )


def test_probe_status_consistency_enforced():
    """Danh sách issues có lỗi BLOCKING bắt buộc status phải là GEOMETRY_BLOCKED."""
    iss = PlanGeometryIssue(severity="BLOCKING", issue_code="ERR_TEST", clip_id="c1", message="Critical blocker")

    with pytest.raises(ValueError, match="ERR_STATUS_INCONSISTENCY"):
        PlanGeometryAssemblyResult(
            status="GEOMETRY_VERIFIED",
            proposal_id="p1",
            proposal_revision="rev1",
            timeline_fps=RationalFPS(24, 1),
            assembled_segments=(),
            total_timeline_frames=0,
            total_timeline_duration_sec=Fraction(0, 1),
            issues=(iss,),
            unresolved_reviews=(),
        )


def test_probe_all_cut_zero_zero_invariants_enforced():
    """All-cut timeline (assembled_segments=()) với GEOMETRY_VERIFIED bắt buộc total_frames=0 và duration=Fraction(0, 1)."""
    # Nếu total_frames khác 0 -> ném lỗi
    with pytest.raises(ValueError, match="ERR_TOTAL_FRAMES_MISMATCH"):
        PlanGeometryAssemblyResult(
            status="GEOMETRY_VERIFIED",
            proposal_id="p1",
            proposal_revision="rev1",
            timeline_fps=RationalFPS(24, 1),
            assembled_segments=(),
            total_timeline_frames=10,
            total_timeline_duration_sec=Fraction(10, 24),
            issues=(),
            unresolved_reviews=(),
        )

    # Đúng 0/0 -> thành công
    res = PlanGeometryAssemblyResult(
        status="GEOMETRY_VERIFIED",
        proposal_id="p1",
        proposal_revision="rev1",
        timeline_fps=RationalFPS(24, 1),
        assembled_segments=(),
        total_timeline_frames=0,
        total_timeline_duration_sec=Fraction(0, 1),
        issues=(),
        unresolved_reviews=(),
    )
    assert res.total_timeline_frames == 0
    assert res.total_timeline_duration_sec == Fraction(0, 1)


# ==============================================================================
# G06-06C2d-R2: Nhóm tồn đọng cuối cùng — Residual Hook & Nested Member Matrix
# ==============================================================================

def test_residual_hook_role_reference_blocked_all_modes():
    """global_hook có role='reference_only' bắt buộc phải bị GEOMETRY_BLOCKED với lỗi ERR_REFERENCE_CLIP_NOT_FOOTAGE
    trên cả 3 chế độ (intro_cut, highlight_ref, None/ambiguous), ngay cả khi clip_id thuộc source_binding hợp lệ và có bằng chứng CFR."""
    ev_table = {"c1": SourceFrameEvidence("mf_hook", "c1", RationalFPS(25, 1), 125, "VERIFIED_CFR")}

    for mode in ("intro_cut", "highlight_ref", None):
        hook_dict = {
            "clip_id": "c1",
            "role": "reference_only",
            "start_sec": 0.0,
            "end_sec": 2.0,
        }
        if mode is not None:
            hook_dict["placement_mode"] = mode

        raw_prop = _make_minimal_proposal_dict(
            manifest_id="mf_hook",
            clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
            timeline_segments=[{"clip_id": "c1", "start_sec": 0.0, "end_sec": 3.0}],
            global_hook=hook_dict,
        )
        res = assemble_plan_frame_geometry(raw_prop, ev_table, RationalFPS(25, 1))
        assert res.status == "GEOMETRY_BLOCKED"
        assert res.assembled_segments == ()
        assert any(
            iss.issue_code == "ERR_REFERENCE_CLIP_NOT_FOOTAGE" and iss.severity == "BLOCKING"
            for iss in res.issues
        )

    # Đối chứng: role khác (không phải reference_only) hoặc vắng mặt role vẫn VERIFIED bình thường
    for valid_role in (None, "main", "broll"):
        hook_dict = {
            "clip_id": "c1",
            "start_sec": 0.0,
            "end_sec": 2.0,
            "placement_mode": "intro_cut",
        }
        if valid_role is not None:
            hook_dict["role"] = valid_role
        raw_prop = _make_minimal_proposal_dict(
            manifest_id="mf_hook",
            clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
            timeline_segments=[{"clip_id": "c1", "start_sec": 0.0, "end_sec": 2.0}],
            global_hook=hook_dict,
        )
        res = assemble_plan_frame_geometry(raw_prop, ev_table, RationalFPS(25, 1))
        assert res.status == "GEOMETRY_VERIFIED"
        assert res.total_timeline_frames == 100  # 50 frames hook + 50 frames segment


def test_residual_hook_speed_null_blocked_all_modes():
    """global_hook có key 'speed' nhưng giá trị là None (null) bắt buộc phải bị GEOMETRY_BLOCKED với lỗi ERR_INVALID_SPEED
    trên cả 3 chế độ (intro_cut, highlight_ref, None/ambiguous). Không được tự ý coi None là vắng mặt/mặc định 1."""
    ev_table = {"c1": SourceFrameEvidence("mf_hook", "c1", RationalFPS(25, 1), 125, "VERIFIED_CFR")}

    for mode in ("intro_cut", "highlight_ref", None):
        hook_dict = {
            "clip_id": "c1",
            "speed": None,
            "start_sec": 0.0,
            "end_sec": 2.0,
        }
        if mode is not None:
            hook_dict["placement_mode"] = mode

        raw_prop = _make_minimal_proposal_dict(
            manifest_id="mf_hook",
            clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
            timeline_segments=[{"clip_id": "c1", "start_sec": 0.0, "end_sec": 3.0}],
            global_hook=hook_dict,
        )
        res = assemble_plan_frame_geometry(raw_prop, ev_table, RationalFPS(25, 1))
        assert res.status == "GEOMETRY_BLOCKED"
        assert res.assembled_segments == ()
        assert any(
            iss.issue_code == "ERR_INVALID_SPEED" and iss.severity == "BLOCKING"
            for iss in res.issues
        )

    # Đối chứng 1: speed vắng mặt (absent) -> mặc định 1 hợp lệ
    raw_prop_no_speed = _make_minimal_proposal_dict(
        manifest_id="mf_hook",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        timeline_segments=[{"clip_id": "c1", "start_sec": 0.0, "end_sec": 2.0}],
        global_hook={"clip_id": "c1", "start_sec": 0.0, "end_sec": 2.0, "placement_mode": "intro_cut"},
    )
    res_no_speed = assemble_plan_frame_geometry(raw_prop_no_speed, ev_table, RationalFPS(25, 1))
    assert res_no_speed.status == "GEOMETRY_VERIFIED"
    assert res_no_speed.assembled_segments[0].applied_speed == Fraction(1, 1)

    # Đối chứng 2: speed dương rõ ràng (e.g. 2.0) -> áp dụng thành công
    raw_prop_speed2 = _make_minimal_proposal_dict(
        manifest_id="mf_hook",
        clips=[{"clip_id": "c1", "duration_sec": 5.0, "fps": 25.0}],
        timeline_segments=[{"clip_id": "c1", "start_sec": 0.0, "end_sec": 2.0}],
        global_hook={"clip_id": "c1", "start_sec": 0.0, "end_sec": 2.0, "speed": 2.0, "placement_mode": "intro_cut"},
    )
    res_speed2 = assemble_plan_frame_geometry(raw_prop_speed2, ev_table, RationalFPS(25, 1))
    assert res_speed2.status == "GEOMETRY_VERIFIED"
    assert res_speed2.assembled_segments[0].applied_speed == Fraction(2, 1)


def _make_valid_test_segment() -> PlanTimelineSegmentGeometry:
    span = QuantizedCFRSpan(
        source_fps=RationalFPS(24, 1),
        timeline_fps=RationalFPS(24, 1),
        source_range=TimeRangeFrames(0, 48),
        total_source_frames=100,
        record_duration_frames=48,
        applied_speed=Fraction(1, 1),
    )
    return PlanTimelineSegmentGeometry(
        segment_index=0,
        origin="timeline_segment",
        source_segment_index=0,
        clip_id="c1",
        quantized_span=span,
        timeline_range=TimeRangeFrames(0, 48),
    )


@pytest.mark.parametrize("target,attr,expected_error", [
    ("segment", "segment_index", "ERR_MALFORMED_SEGMENT"),
    ("segment", "origin", "ERR_MALFORMED_SEGMENT"),
    ("segment", "source_segment_index", "ERR_MALFORMED_SEGMENT"),
    ("segment", "clip_id", "ERR_MALFORMED_SEGMENT"),
    ("segment", "quantized_span", "ERR_MALFORMED_SEGMENT"),
    ("segment", "timeline_range", "ERR_MALFORMED_SEGMENT"),
    ("span", "source_fps", "ERR_MALFORMED_SPAN"),
    ("span", "timeline_fps", "ERR_MALFORMED_SPAN"),
    ("span", "source_range", "ERR_MALFORMED_SPAN"),
    ("span", "total_source_frames", "ERR_MALFORMED_SPAN"),
    ("span", "record_duration_frames", "ERR_MALFORMED_SPAN"),
    ("span", "applied_speed", "ERR_MALFORMED_SPAN"),
    ("issue", "severity", "ERR_MALFORMED_ISSUE"),
    ("issue", "issue_code", "ERR_MALFORMED_ISSUE"),
    ("issue", "message", "ERR_MALFORMED_ISSUE|ERR_BLANK_FIELD"),
    ("review", "category", "ERR_MALFORMED_REVIEW_ITEM"),
    ("review", "item_id", "ERR_MALFORMED_REVIEW_ITEM"),
    ("review", "description", "ERR_MALFORMED_REVIEW_ITEM"),
])
def test_nested_member_deletion_matrix_controlled_errors(target: str, attr: str, expected_error: str):
    """Kiểm tra toàn bộ ma trận 18 trường bắt buộc bị xóa thuộc tính trên đối tượng danh nghĩa.
    Bắt buộc phải ném ValueError có mã lỗi tiền tố kiểm soát (ERR_*), TUYỆT ĐỐI KHÔNG rò rỉ AttributeError."""
    seg = _make_valid_test_segment()
    iss = PlanGeometryIssue(severity="WARNING", issue_code="WARN_CODE", message="Test warning message")
    rev = UnresolvedReviewItem(category="HOOK_PLACEMENT", item_id="h1", description="Review hook")

    if target == "segment":
        object.__delattr__(seg, attr)
        with pytest.raises(ValueError, match=expected_error):
            PlanGeometryAssemblyResult(
                status="GEOMETRY_VERIFIED",
                proposal_id="p1",
                proposal_revision="rev1",
                timeline_fps=RationalFPS(24, 1),
                assembled_segments=(seg,),
                total_timeline_frames=48,
                total_timeline_duration_sec=Fraction(48, 24),
                issues=(),
                unresolved_reviews=(),
            )
    elif target == "span":
        object.__delattr__(seg.quantized_span, attr)
        with pytest.raises(ValueError, match=expected_error):
            PlanGeometryAssemblyResult(
                status="GEOMETRY_VERIFIED",
                proposal_id="p1",
                proposal_revision="rev1",
                timeline_fps=RationalFPS(24, 1),
                assembled_segments=(seg,),
                total_timeline_frames=48,
                total_timeline_duration_sec=Fraction(48, 24),
                issues=(),
                unresolved_reviews=(),
            )
    elif target == "issue":
        object.__delattr__(iss, attr)
        with pytest.raises(ValueError, match=expected_error):
            PlanGeometryAssemblyResult(
                status="GEOMETRY_UNRESOLVED",
                proposal_id="p1",
                proposal_revision="rev1",
                timeline_fps=RationalFPS(24, 1),
                assembled_segments=(),
                total_timeline_frames=None,
                total_timeline_duration_sec=None,
                issues=(iss,),
                unresolved_reviews=(),
            )
    elif target == "review":
        object.__delattr__(rev, attr)
        with pytest.raises(ValueError, match=expected_error):
            PlanGeometryAssemblyResult(
                status="GEOMETRY_VERIFIED",
                proposal_id="p1",
                proposal_revision="rev1",
                timeline_fps=RationalFPS(24, 1),
                assembled_segments=(),
                total_timeline_frames=0,
                total_timeline_duration_sec=Fraction(0, 1),
                issues=(),
                unresolved_reviews=(rev,),
            )


def test_issue_declared_defaults_resolve_none_controlled():
    """PlanGeometryIssue.clip_id và segment_index có giá trị mặc định là None ở cấp lớp.
    Xóa thuộc tính ở instance vẫn phân giải về None hợp lệ mà không ném lỗi missing-required."""
    iss = PlanGeometryIssue(
        severity="WARNING",
        issue_code="WARN_CODE",
        clip_id="c1",
        segment_index=5,
        message="Valid message",
    )
    # Xóa clip_id trên instance
    object.__delattr__(iss, "clip_id")
    # Xóa segment_index trên instance
    object.__delattr__(iss, "segment_index")

    # Khi đưa vào PlanGeometryAssemblyResult, rehydration vẫn giữ giá trị None hợp lệ
    res = PlanGeometryAssemblyResult(
        status="GEOMETRY_UNRESOLVED",
        proposal_id="p1",
        proposal_revision="rev1",
        timeline_fps=RationalFPS(24, 1),
        assembled_segments=(),
        total_timeline_frames=None,
        total_timeline_duration_sec=None,
        issues=(iss,),
        unresolved_reviews=(),
    )
    assert res.status == "GEOMETRY_UNRESOLVED"
    assert len(res.issues) == 1
    assert res.issues[0].clip_id is None
    assert res.issues[0].segment_index is None


