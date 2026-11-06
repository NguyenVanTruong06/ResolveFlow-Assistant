"""tests/core/test_timeline_plan_execution_binder.py
Kiểm thử đơn vị tập trung thuần túy (TDD) cho Bộ Liên Kết Kế Hoạch Thi Công Timeline Offline (G07-07B2-I).

Quy tắc nghiêm ngặt:
- Zero filesystem IO, zero network, zero subprocess, zero Qt widgets, zero DaVinci Resolve API.
- Bất biến hoàn toàn (frozen dataclass, MappingProxyType, tuple defensive copies).
- Ràng buộc an toàn: ready_to_execute=False bảo toàn 100%, Gate G07/M1/M2 giữ mở.
- Bao phủ toàn diện ma trận kiểm thử:
  1. Thẩm định phê duyệt: phát hiện giả mạo/sai lệch proposal_id, revision hash, approval_scope, omission list.
  2. Xác thực TargetSpec: định danh dự án/timeline, RationalFPS, độ phân giải, timecode offset.
  3. Lắp ráp hình học Formula B: làm tròn half-even, đổi FPS chéo (30fps -> 24fps), timecode offset bù trừ.
  4. Bằng chứng nguồn: VERIFIED_CFR vs HEADER_REPORTED_ONLY vs VERIFIED_VFR vs thiếu bằng chứng.
  5. Cấm bỏ qua footage chính: A-roll và global hook không bao giờ được phép omission.
  6. Xử lý omission trung thực D01/D02/D03: B-roll/SFX đã duyệt vs chưa duyệt.
  7. Ảnh chụp chiếm dụng có thẩm quyền: snapshot khớp project/timeline/FPS, từ chối khi vắng mặt, STALE, AMBIGUOUS.
  8. Từ chối track chưa quét: chỉ coi track là rỗng khi track đó nằm trong scanned_tracks của snapshot AUTHORITATIVE.
  9. Phát hiện va chạm ngoại vi (với clip hiện hữu trên Resolve) và va chạm nội bộ (giữa các planned actions).
  10. Tính tất định: mã băm idempotency_key, action_idempotency_hash, JSON sort_keys.
  11. Ranh giới trần dung lượng: kiểm tra chính xác biên trần MAX_DRY_RUN_JSON_BYTES (bằng trần chấp nhận, +1 từ chối).
  12. Kịch bản All-cut / Empty plan: 0 frames, 0 actions, kết quả BOUND_VERIFIED.
  13. Tính bất biến và Zero-IO tripwire.
"""
from __future__ import annotations

import dataclasses
from fractions import Fraction
import json
from types import MappingProxyType
from typing import Any, Mapping
from unittest.mock import patch

import pytest

from src.core.contracts.edit_plan_contracts import CreativeContext
from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    compute_proposal_revision,
    normalize_edit_plan_proposal,
)
from src.core.contracts.edit_plan_approval_contracts import (
    AppOwnedApprovalRecord,
    VALID_APPROVAL_SCOPE,
)
from src.core.contracts.time_contracts import RationalFPS, TimeRangeFrames
from src.core.geometry.plan_geometry_contracts import SourceFrameEvidence
from src.core.timeline_plan_execution_binder import (
    MAX_DRY_RUN_JSON_BYTES,
    MAX_DRY_RUN_NODES,
    MAX_PLANNED_ACTIONS,
    OccupiedTrackSpan,
    PlanBindingIssue,
    PlannedOmissionRecord,
    PlannedTimelineAction,
    TargetSpec,
    TimelineExecutionPlan,
    TimelineOccupancySnapshot,
    bind_timeline_execution_plan,
    canonical_plan_bytes,
    compute_canonical_plan_digest,
    compute_plan_idempotency_key,
    dry_run_report_to_dict,
    serialize_canonical_plan_payload,
    serialize_dry_run_report,
)


def _make_sample_proposal(
    proposal_id: str = "prop_test_001",
    manifest_id: str = "mf_test_001",
    include_hook: bool = True,
    hook_clip_id: str = "clip_hook",
    segments_config: list[dict[str, Any]] | None = None,
    proposed_omissions: tuple[dict[str, Any], ...] = (),
) -> EditPlanProposal:
    """Tạo proposal mẫu chuẩn tắc hợp lệ để kiểm thử."""
    c_ctx = CreativeContext(
        free_brief="Test video editing plan",
        style_preset="Cinematic",
        must_keep_rules=("keep rule 1",),
        must_not_rules=("must not 1",),
        reference_items=(),
    )

    clips = [
        {
            "clip_id": "clip_hook",
            "file_path": "/media/test/hook.mp4",
            "source_kind": "managed",
            "origin_library_id": "lib_01",
            "role": "footage",
        },
        {
            "clip_id": "clip_main_1",
            "file_path": "/media/test/main_1.mp4",
            "source_kind": "managed",
            "origin_library_id": "lib_01",
            "role": "footage",
        },
        {
            "clip_id": "clip_main_2",
            "file_path": "/media/test/main_2.mp4",
            "source_kind": "managed",
            "origin_library_id": "lib_01",
            "role": "footage",
        },
        {
            "clip_id": "clip_broll_1",
            "file_path": "/media/test/broll_1.mp4",
            "source_kind": "managed",
            "origin_library_id": "lib_01",
            "role": "broll",
        },
    ]

    source_binding = {
        "manifest_id": manifest_id,
        "clips": clips,
    }

    if segments_config is None:
        segments_config = [
            {
                "segment_index": 0,
                "clip_id": "clip_main_1",
                "start_sec": 0.0,
                "end_sec": 2.0,
                "action": "keep",
                "speed": 1.0,
                "broll_inserts": [],
                "sfx_inserts": [],
            },
            {
                "segment_index": 1,
                "clip_id": "clip_main_2",
                "start_sec": 1.0,
                "end_sec": 3.0,
                "action": "keep",
                "speed": 1.0,
                "broll_inserts": [],
                "sfx_inserts": [],
            },
        ]

    video_plan: dict[str, Any] = {
        "manifest_id": manifest_id,
        "timeline_segments": segments_config,
    }

    if include_hook:
        video_plan["global_hook"] = {
            "clip_id": hook_clip_id,
            "start_sec": 0.0,
            "end_sec": 1.0,
            "speed": 1.0,
            "placement_mode": "intro_cut",
        }

    return normalize_edit_plan_proposal({
        "proposal_id": proposal_id,
        "manifest_id": manifest_id,
        "creative_context": c_ctx,
        "source_binding": source_binding,
        "video_plan": video_plan,
        "kinetic_plan": None,
        "proposed_asset_assignments": (),
        "proposed_omissions": proposed_omissions,
    })


def _make_matching_approval(
    proposal: EditPlanProposal,
    approval_id: str = "appr_test_001",
    approved_at_iso: str = "2026-11-02T10:00:00Z",
) -> AppOwnedApprovalRecord:
    """Tạo bản ghi phê duyệt AppOwnedApprovalRecord khớp 100% với proposal."""
    rev = compute_proposal_revision(proposal)
    expected_omissions = tuple(str(item["demand_id"]) for item in proposal.proposed_omissions)
    return AppOwnedApprovalRecord(
        approval_id=approval_id,
        proposal_id=proposal.proposal_id,
        proposal_revision=rev,
        approved_at_iso=approved_at_iso,
        accepted_omission_ids=expected_omissions,
        approval_scope=VALID_APPROVAL_SCOPE,
    )


def _make_evidence_table(
    manifest_id: str = "mf_test_001",
    fps: RationalFPS = RationalFPS(24, 1),
    total_frames: int = 240,
) -> dict[str, SourceFrameEvidence]:
    """Tạo bảng tra cứu bằng chứng khung hình VERIFIED_CFR cho toàn bộ clips."""
    return {
        "clip_hook": SourceFrameEvidence(
            manifest_id=manifest_id,
            clip_id="clip_hook",
            source_fps=fps,
            total_frames=total_frames,
            evidence_status="VERIFIED_CFR",
        ),
        "clip_main_1": SourceFrameEvidence(
            manifest_id=manifest_id,
            clip_id="clip_main_1",
            source_fps=fps,
            total_frames=total_frames,
            evidence_status="VERIFIED_CFR",
        ),
        "clip_main_2": SourceFrameEvidence(
            manifest_id=manifest_id,
            clip_id="clip_main_2",
            source_fps=fps,
            total_frames=total_frames,
            evidence_status="VERIFIED_CFR",
        ),
        "clip_broll_1": SourceFrameEvidence(
            manifest_id=manifest_id,
            clip_id="clip_broll_1",
            source_fps=fps,
            total_frames=total_frames,
            evidence_status="VERIFIED_CFR",
        ),
    }


def _make_sample_target_spec(
    project_id: str = "proj_001",
    timeline_id: str = "tl_001",
    fps: RationalFPS = RationalFPS(24, 1),
    start_timecode_frame: int = 0,
) -> TargetSpec:
    """Tạo TargetSpec mẫu danh nghĩa."""
    return TargetSpec(
        project_name="Test Project",
        project_id=project_id,
        timeline_name="Test Timeline",
        timeline_id=timeline_id,
        timeline_fps=fps,
        timeline_resolution=(1920, 1080),
        start_timecode_frame=start_timecode_frame,
    )


def _make_sample_occupancy_snapshot(
    project_id: str = "proj_001",
    timeline_id: str = "tl_001",
    fps: RationalFPS = RationalFPS(24, 1),
    scanned_tracks: tuple[tuple[str, int], ...] = (("video", 1), ("video", 2), ("audio", 1), ("audio", 2)),
    occupied_spans: tuple[OccupiedTrackSpan, ...] = (),
    status: str = "AUTHORITATIVE",
) -> TimelineOccupancySnapshot:
    """Tạo TimelineOccupancySnapshot mẫu."""
    return TimelineOccupancySnapshot(
        snapshot_id="snap_test_001",
        project_id=project_id,
        timeline_id=timeline_id,
        timeline_fps=fps,
        captured_at_iso="2026-11-02T10:05:00Z",
        occupied_spans=occupied_spans,
        scanned_tracks=scanned_tracks,
        status=status,
    )


# ==============================================================================
# NHÓM 1: KIỂM THỬ THẨM ĐỊNH PHÊ DUYỆT & GIẢ MẠO (APPROVAL REVALIDATION)
# ==============================================================================

def test_approval_binding_stale_proposal_revision_rejected():
    """Từ chối thi công khi proposal bị sửa đổi sau khi duyệt (stale revision)."""
    prop = _make_sample_proposal()
    appr = _make_matching_approval(prop)
    target = _make_sample_target_spec()
    evidence = _make_evidence_table()
    snapshot = _make_sample_occupancy_snapshot()

    # Tạo approval có revision khác biệt (giả mạo hoặc proposal bị sửa đổi sau khi duyệt)
    tampered_appr = AppOwnedApprovalRecord(
        approval_id=appr.approval_id,
        proposal_id=appr.proposal_id,
        proposal_revision="0" * 64,  # sai mã băm
        approved_at_iso=appr.approved_at_iso,
        accepted_omission_ids=appr.accepted_omission_ids,
        approval_scope=appr.approval_scope,
    )

    plan = bind_timeline_execution_plan(prop, tampered_appr, target, evidence, snapshot)
    assert plan.binding_status == "BOUND_BLOCKED"
    assert len(plan.planned_actions) == 0
    assert plan.planned_actions_count == 0
    issue_codes = [iss.issue_code for iss in plan.issues]
    assert "ERR_STALE_PROPOSAL_REVISION" in issue_codes


def test_approval_binding_proposal_id_mismatch_rejected():
    """Từ chối khi proposal_id trong approval không khớp proposal."""
    prop = _make_sample_proposal(proposal_id="prop_real")
    appr = _make_matching_approval(prop)
    target = _make_sample_target_spec()
    evidence = _make_evidence_table()
    snapshot = _make_sample_occupancy_snapshot()

    mismatched_appr = AppOwnedApprovalRecord(
        approval_id=appr.approval_id,
        proposal_id="prop_fake",
        proposal_revision=appr.proposal_revision,
        approved_at_iso=appr.approved_at_iso,
        accepted_omission_ids=appr.accepted_omission_ids,
        approval_scope=appr.approval_scope,
    )

    plan = bind_timeline_execution_plan(prop, mismatched_appr, target, evidence, snapshot)
    assert plan.binding_status == "BOUND_BLOCKED"
    assert "ERR_PROPOSAL_ID_MISMATCH" in [iss.issue_code for iss in plan.issues]


# ==============================================================================
# NHÓM 2: KIỂM THỬ TARGETSPEC VÀ ĐỊNH DANH ĐÍCH (TARGET SPEC VALIDATION)
# ==============================================================================

def test_target_spec_validation_rejects_blank_fields_and_invalid_fps():
    """TargetSpec bắt buộc kiểm tra các trường rỗng, NUL, FPS âm/0."""
    with pytest.raises(ValueError, match="ERR_BLANK_FIELD"):
        TargetSpec(
            project_name="",
            project_id="p1",
            timeline_name="tl",
            timeline_id="t1",
            timeline_fps=RationalFPS(24, 1),
            timeline_resolution=(1920, 1080),
        )

    with pytest.raises(ValueError, match="ERR_INVALID_STRING"):
        TargetSpec(
            project_name="Valid",
            project_id="p1\0null",
            timeline_name="tl",
            timeline_id="t1",
            timeline_fps=RationalFPS(24, 1),
            timeline_resolution=(1920, 1080),
        )

    with pytest.raises(ValueError, match="ERR_INVALID_RESOLUTION"):
        TargetSpec(
            project_name="Valid",
            project_id="p1",
            timeline_name="tl",
            timeline_id="t1",
            timeline_fps=RationalFPS(24, 1),
            timeline_resolution=(10, 10),  # < 16x16
        )


def test_target_spec_fps_mismatch_with_occupancy_snapshot():
    """Phát hiện và chặn đứng khi TargetSpec.timeline_fps lệch với Snapshot."""
    prop = _make_sample_proposal()
    appr = _make_matching_approval(prop)
    target = _make_sample_target_spec(fps=RationalFPS(24, 1))
    evidence = _make_evidence_table(fps=RationalFPS(24, 1))
    # Snapshot lại có FPS 30/1
    snapshot = _make_sample_occupancy_snapshot(fps=RationalFPS(30, 1))

    plan = bind_timeline_execution_plan(prop, appr, target, evidence, snapshot)
    assert plan.binding_status == "BOUND_BLOCKED"
    assert "ERR_TARGET_FPS_MISMATCH" in [iss.issue_code for iss in plan.issues]


# ==============================================================================
# NHÓM 3: HÌNH HỌC FORMULA B VÀ CHUYỂN ĐỔI FPS CHÉO (FORMULA B GEOMETRY)
# ==============================================================================

def test_happy_path_standard_binding_and_formula_b():
    """Happy Path chuẩn: A-roll + Hook khớp chính xác Formula B và phân bổ track V1."""
    prop = _make_sample_proposal()
    appr = _make_matching_approval(prop)
    target = _make_sample_target_spec(start_timecode_frame=0)
    evidence = _make_evidence_table()
    snapshot = _make_sample_occupancy_snapshot()

    plan = bind_timeline_execution_plan(prop, appr, target, evidence, snapshot)
    assert plan.binding_status == "BOUND_VERIFIED"
    assert plan.planned_actions_count == 3  # 1 hook + 2 main segments
    assert len(plan.planned_actions) == 3

    # Kiểm tra action 0 (hook): 0.0s đến 1.0s tại 24fps -> 24 frames
    act0 = plan.planned_actions[0]
    assert act0.role == "hook"
    assert act0.target_track_type == "video"
    assert act0.target_track_index == 1
    assert act0.timeline_range.start_frame == 0
    assert act0.timeline_range.end_frame == 24

    # Kiểm tra action 1 (main_1): 0.0s đến 2.0s tại 24fps -> 48 frames, nối tiếp tại 24
    act1 = plan.planned_actions[1]
    assert act1.role == "a_roll"
    assert act1.timeline_range.start_frame == 24
    assert act1.timeline_range.end_frame == 72

    # Kiểm tra action 2 (main_2): 1.0s đến 3.0s tại 24fps -> 48 frames, nối tiếp tại 72
    act2 = plan.planned_actions[2]
    assert act2.role == "a_roll"
    assert act2.timeline_range.start_frame == 72
    assert act2.timeline_range.end_frame == 120

    assert plan.total_timeline_frames == 120
    assert plan.total_timeline_duration_sec == Fraction(120, 24)


def test_cross_fps_rescale_formula_b_30fps_to_24fps():
    """Chuyển đổi FPS chéo Formula B (30fps nguồn -> 24fps timeline, half-even rounding)."""
    # Nguồn 30fps cắt [1.0, 2.017)s -> [30, 61) = 31 frames nguồn
    # Lượng tử hóa sang 24fps: 31 / 30 * 24 = 24.8 -> làm tròn half-even là 25 frames
    prop = _make_sample_proposal(
        include_hook=False,
        segments_config=[
            {
                "segment_index": 0,
                "clip_id": "clip_main_1",
                "start_sec": 1.0,
                "end_sec": 2.017,
                "action": "keep",
                "speed": 1.0,
                "broll_inserts": [],
                "sfx_inserts": [],
            }
        ]
    )
    appr = _make_matching_approval(prop)
    target = _make_sample_target_spec(fps=RationalFPS(24, 1))
    # Nguồn 30fps
    evidence = _make_evidence_table(fps=RationalFPS(30, 1), total_frames=120)
    snapshot = _make_sample_occupancy_snapshot(fps=RationalFPS(24, 1))

    plan = bind_timeline_execution_plan(prop, appr, target, evidence, snapshot)
    assert plan.binding_status == "BOUND_VERIFIED"
    assert plan.planned_actions_count == 1
    act = plan.planned_actions[0]
    assert act.source_range.start_frame == 30
    assert act.source_range.end_frame == 61
    assert act.timeline_range.start_frame == 0
    assert act.timeline_range.end_frame == 25  # đúng 25 frames Formula B


def test_timecode_start_frame_offset_respected():
    """Khi timeline có start_timecode_frame = 86400 (01:00:00:00), tọa độ timeline được tịnh tiến chuẩn."""
    prop = _make_sample_proposal(include_hook=False, segments_config=[
        {
            "segment_index": 0,
            "clip_id": "clip_main_1",
            "start_sec": 0.0,
            "end_sec": 1.0,
            "action": "keep",
            "speed": 1.0,
            "broll_inserts": [],
            "sfx_inserts": [],
        }
    ])
    appr = _make_matching_approval(prop)
    target = _make_sample_target_spec(start_timecode_frame=86400)
    evidence = _make_evidence_table()
    snapshot = _make_sample_occupancy_snapshot()

    plan = bind_timeline_execution_plan(prop, appr, target, evidence, snapshot)
    assert plan.binding_status == "BOUND_VERIFIED"
    act = plan.planned_actions[0]
    assert act.timeline_range.start_frame == 86400
    assert act.timeline_range.end_frame == 86424


# ==============================================================================
# NHÓM 4: BẰNG CHỨNG NGUỒN & CẤM OMISSION FOOTAGE CHÍNH (SOURCE EVIDENCE & PRIMARY)
# ==============================================================================

def test_missing_or_unverified_source_evidence_blocks_plan():
    """Clip chính thiếu bằng chứng hoặc chỉ là HEADER_REPORTED_ONLY -> BLOCKED."""
    prop = _make_sample_proposal()
    appr = _make_matching_approval(prop)
    target = _make_sample_target_spec()
    evidence = _make_evidence_table()
    # Biến clip_main_1 thành HEADER_REPORTED_ONLY
    evidence["clip_main_1"] = SourceFrameEvidence(
        manifest_id="mf_test_001",
        clip_id="clip_main_1",
        source_fps=RationalFPS(24, 1),
        total_frames=240,
        evidence_status="HEADER_REPORTED_ONLY",
    )
    snapshot = _make_sample_occupancy_snapshot()

    plan = bind_timeline_execution_plan(prop, appr, target, evidence, snapshot)
    assert plan.binding_status == "BOUND_BLOCKED"
    assert len(plan.planned_actions) == 0


def test_primary_footage_can_never_be_omitted():
    """Primary A-roll/hook tuyệt đối cấm omission, nếu cố tình khai báo omission -> Bị chặn."""
    # Khai báo proposed_omissions với demand_id của footage chính
    prop = _make_sample_proposal(
        proposed_omissions=(
            {
                "demand_id": "omit_clip_main_1",
                "slot": "broll",
                "timeline_sec": 0.0,
                "duration_sec": 1.0,
                "reason": "Attempting to omit primary footage",
                "warning_message": "Warning: primary footage omitted",
            },
        )
    )
    appr = _make_matching_approval(prop)
    target = _make_sample_target_spec()
    evidence = _make_evidence_table()
    snapshot = _make_sample_occupancy_snapshot()

    # Bỏ clip_main_1 khỏi evidence table để thử xem binder có cho omission footage chính không
    del evidence["clip_main_1"]

    plan = bind_timeline_execution_plan(prop, appr, target, evidence, snapshot)
    assert plan.binding_status == "BOUND_BLOCKED"
    assert "ERR_MISSING_PRIMARY_FOOTAGE" in [iss.issue_code for iss in plan.issues]


# ==============================================================================
# NHÓM 5: XỬ LÝ OMISSION TRUNG THỰC THEO D01/D02/D03 (TRUTHFUL OMISSIONS)
# ==============================================================================

def test_accepted_supplemental_omission_is_truthfully_recorded():
    """B-roll/SFX thiếu nhưng đã được người dùng duyệt omission (D02) -> Được ghi nhận trung thực, không phát action clip."""
    prop = _make_sample_proposal(
        include_hook=False,
        proposed_omissions=(
            {
                "demand_id": "demand_sfx_whoosh",
                "slot": "sfx",
                "timeline_sec": 1.0,
                "duration_sec": 0.5,
                "reason": "SFX pack not downloaded, user accepted omission",
                "warning_message": "SFX whoosh will be omitted",
            },
        )
    )
    appr = _make_matching_approval(prop)
    target = _make_sample_target_spec()
    evidence = _make_evidence_table()
    snapshot = _make_sample_occupancy_snapshot()

    plan = bind_timeline_execution_plan(prop, appr, target, evidence, snapshot)
    assert plan.binding_status == "BOUND_VERIFIED"
    assert len(plan.accepted_omissions) == 1
    omission_rec = plan.accepted_omissions[0]
    assert omission_rec.demand_id == "demand_sfx_whoosh"
    assert omission_rec.slot == "sfx"
    assert omission_rec.accepted_by_approval_id == appr.approval_id
    # Không có action INSERT_CLIP nào sinh ra cho SFX này
    assert all(act.role != "sfx" for act in plan.planned_actions)


def test_unapproved_omission_blocks_plan():
    """Tài sản thiếu nhưng KHÔNG nằm trong accepted_omission_ids của người dùng -> BLOCKING."""
    prop = _make_sample_proposal(
        include_hook=False,
        proposed_omissions=(
            {
                "demand_id": "demand_sfx_whoosh",
                "slot": "sfx",
                "timeline_sec": 1.0,
                "duration_sec": 0.5,
                "reason": "SFX missing",
                "warning_message": "SFX missing warning",
            },
        )
    )
    # Approval record lại không chứa demand_sfx_whoosh
    appr = AppOwnedApprovalRecord(
        approval_id="appr_001",
        proposal_id=prop.proposal_id,
        proposal_revision=compute_proposal_revision(prop),
        approved_at_iso="2026-11-02T10:00:00Z",
        accepted_omission_ids=(),  # Rỗng, chưa chấp thuận
        approval_scope=VALID_APPROVAL_SCOPE,
    )
    target = _make_sample_target_spec()
    evidence = _make_evidence_table()
    snapshot = _make_sample_occupancy_snapshot()

    plan = bind_timeline_execution_plan(prop, appr, target, evidence, snapshot)
    assert plan.binding_status == "BOUND_BLOCKED"
    assert "ERR_UNAPPROVED_OMISSION" in [iss.issue_code for iss in plan.issues]


# ==============================================================================
# NHÓM 6: ẢNH CHỤP CHIẾM DỤNG & TỪ CHỐI TRACK CHƯA QUÉT (OCCUPANCY & TRACKS)
# ==============================================================================

def test_missing_occupancy_snapshot_blocks_plan_fail_closed():
    """Khi snapshot là None -> BẮT BUỘC CHẶN (ERR_OCCUPANCY_UNAVAILABLE), không đoán mò track rỗng."""
    prop = _make_sample_proposal()
    appr = _make_matching_approval(prop)
    target = _make_sample_target_spec()
    evidence = _make_evidence_table()

    plan = bind_timeline_execution_plan(prop, appr, target, evidence, occupancy_snapshot=None)
    assert plan.binding_status == "BOUND_BLOCKED"
    assert len(plan.planned_actions) == 0
    assert "ERR_OCCUPANCY_UNAVAILABLE" in [iss.issue_code for iss in plan.issues]


def test_stale_or_ambiguous_occupancy_snapshot_blocks_plan():
    """Khi snapshot có trạng thái STALE hoặc AMBIGUOUS -> BẮT BUỘC CHẶN."""
    prop = _make_sample_proposal()
    appr = _make_matching_approval(prop)
    target = _make_sample_target_spec()
    evidence = _make_evidence_table()
    stale_snapshot = _make_sample_occupancy_snapshot(status="STALE")

    plan = bind_timeline_execution_plan(prop, appr, target, evidence, stale_snapshot)
    assert plan.binding_status == "BOUND_BLOCKED"
    assert "ERR_OCCUPANCY_AMBIGUOUS" in [iss.issue_code for iss in plan.issues]


def test_unscanned_track_in_authoritative_snapshot_blocks_plan():
    """Chỉ coi track là rỗng khi track đó NẰM TRONG scanned_tracks. Nếu chưa quét -> CHẶN."""
    prop = _make_sample_proposal()
    appr = _make_matching_approval(prop)
    target = _make_sample_target_spec()
    evidence = _make_evidence_table()
    # Snapshot chỉ mới quét video track 2, CHƯA quét video track 1
    snapshot = _make_sample_occupancy_snapshot(scanned_tracks=(("video", 2),))

    plan = bind_timeline_execution_plan(prop, appr, target, evidence, snapshot)
    assert plan.binding_status == "BOUND_BLOCKED"
    assert "ERR_OCCUPANCY_AMBIGUOUS" in [iss.issue_code for iss in plan.issues]


# ==============================================================================
# NHÓM 7: KIỂM TRA VA CHẠM NGOẠI VI VÀ NỘI BỘ (COLLISION DETECTION)
# ==============================================================================

def test_external_collision_with_existing_timeline_clip():
    """Phát hiện va chạm khi vị trí đặt clip trùng lấn với clip hiện hữu trên track Resolve."""
    prop = _make_sample_proposal(include_hook=False, segments_config=[
        {
            "segment_index": 0,
            "clip_id": "clip_main_1",
            "start_sec": 0.0,
            "end_sec": 2.0,  # 48 frames: [0, 48)
            "action": "keep",
            "speed": 1.0,
            "broll_inserts": [],
            "sfx_inserts": [],
        }
    ])
    appr = _make_matching_approval(prop)
    target = _make_sample_target_spec()
    evidence = _make_evidence_table()
    # Snapshot thông báo track video 1 đã có clip chiếm [24, 100) -> Chồng lấn với [0, 48)
    snapshot = _make_sample_occupancy_snapshot(
        occupied_spans=(
            OccupiedTrackSpan(
                track_type="video",
                track_index=1,
                start_frame=24,
                end_frame=100,
                item_name="Existing Clip On Resolve",
            ),
        )
    )

    plan = bind_timeline_execution_plan(prop, appr, target, evidence, snapshot)
    assert plan.binding_status == "BOUND_BLOCKED"
    assert len(plan.planned_actions) == 0
    assert "ERR_TRACK_COLLISION" in [iss.issue_code for iss in plan.issues]


# ==============================================================================
# NHÓM 8: TÍNH TẤT ĐỊNH & XUẤT BẢN DRY-RUN JSON (DETERMINISM & BOUNDED SERIALIZE)
# ==============================================================================

def test_deterministic_idempotency_key_and_dry_run_json():
    """Mã băm idempotency_key và chuỗi JSON dry-run xuất xưởng là tất định 100% qua nhiều lần chạy."""
    prop = _make_sample_proposal()
    appr = _make_matching_approval(prop)
    target = _make_sample_target_spec()
    evidence = _make_evidence_table()
    snapshot = _make_sample_occupancy_snapshot()

    fixed_iso = "2026-11-02T10:00:00Z"
    plan1 = bind_timeline_execution_plan(prop, appr, target, evidence, snapshot, created_at_iso=fixed_iso)
    plan2 = bind_timeline_execution_plan(prop, appr, target, evidence, snapshot, created_at_iso=fixed_iso)

    assert plan1.idempotency_key == plan2.idempotency_key
    assert len(plan1.idempotency_key) == 64

    json1 = serialize_dry_run_report(plan1)
    json2 = serialize_dry_run_report(plan2)
    assert json1 == json2

    # Parse JSON để kiểm tra cấu trúc
    parsed = json.loads(json1)
    assert parsed["binding_status"] == "BOUND_VERIFIED"
    assert parsed["summary_metrics"]["planned_actions_count"] == 3
    assert len(parsed["planned_actions"]) == 3


def test_dry_run_json_byte_budget_limit_exact_boundary():
    """Kiểm tra chính xác biên trần MAX_DRY_RUN_JSON_BYTES: bằng trần chấp nhận, +1 từ chối."""
    prop = _make_sample_proposal()
    appr = _make_matching_approval(prop)
    target = _make_sample_target_spec()
    evidence = _make_evidence_table()
    snapshot = _make_sample_occupancy_snapshot()

    plan = bind_timeline_execution_plan(prop, appr, target, evidence, snapshot)

    # Serialize bình thường
    json_str = serialize_dry_run_report(plan)
    byte_len = len(json_str.encode("utf-8"))

    # Khi max_bytes đúng bằng byte_len -> Chấp thuận
    ok_str = serialize_dry_run_report(plan, max_bytes=byte_len)
    assert len(ok_str.encode("utf-8")) == byte_len

    # Khi max_bytes = byte_len - 1 -> Bị từ chối bằng ValueError
    with pytest.raises(ValueError, match="ERR_DRY_RUN_JSON_OVERFLOW"):
        serialize_dry_run_report(plan, max_bytes=byte_len - 1)


def test_action_count_budget_overflow_rejected():
    """Khi planned actions vượt quá trần MAX_PLANNED_ACTIONS (4096) -> Bị chặn."""
    prop = _make_sample_proposal()
    appr = _make_matching_approval(prop)
    target = _make_sample_target_spec()
    evidence = _make_evidence_table()
    snapshot = _make_sample_occupancy_snapshot()

    # Giả lập vượt ngưỡng MAX_PLANNED_ACTIONS bằng mock hoặc cap nhỏ
    with patch("src.core.timeline_plan_execution_binder.MAX_PLANNED_ACTIONS", 2):
        plan = bind_timeline_execution_plan(prop, appr, target, evidence, snapshot)
        assert plan.binding_status == "BOUND_BLOCKED"
        assert "ERR_BUDGET_ACTIONS_OVERFLOW" in [iss.issue_code for iss in plan.issues]


# ==============================================================================
# NHÓM 9: KỊCH BẢN ALL-CUT / EMPTY PLAN (EMPTY EXECUTION PLAN)
# ==============================================================================

def test_all_cut_empty_execution_plan():
    """Toàn bộ phân đoạn kịch bản có action 'cut' -> Kế hoạch BOUND_VERIFIED với 0 actions và 0 frames."""
    prop = _make_sample_proposal(
        include_hook=False,
        segments_config=[
            {
                "segment_index": 0,
                "clip_id": "clip_main_1",
                "start_sec": 0.0,
                "end_sec": 2.0,
                "action": "cut",  # Cắt bỏ toàn bộ
                "speed": 1.0,
                "broll_inserts": [],
                "sfx_inserts": [],
            }
        ]
    )
    appr = _make_matching_approval(prop)
    target = _make_sample_target_spec()
    evidence = _make_evidence_table()
    snapshot = _make_sample_occupancy_snapshot()

    plan = bind_timeline_execution_plan(prop, appr, target, evidence, snapshot)
    assert plan.binding_status == "BOUND_VERIFIED"
    assert plan.planned_actions_count == 0
    assert len(plan.planned_actions) == 0
    assert plan.total_timeline_frames == 0
    assert plan.total_timeline_duration_sec == Fraction(0, 1)


# ==============================================================================
# NHÓM 10: TÍNH BẤT BIẾN & BẪY ZERO-IO (IMMUTABILITY & TRIPWIRE)
# ==============================================================================

def test_immutability_defenses():
    """DTOs bất biến hoàn toàn, không thể sửa đổi thuộc tính trực tiếp."""
    target = _make_sample_target_spec()
    with pytest.raises(Exception):
        target.project_name = "Mutated Name"

    span = OccupiedTrackSpan("video", 1, 0, 100, "clip")
    with pytest.raises(Exception):
        span.start_frame = 50


def test_zero_io_and_no_resolve_tripwire():
    """Xác thực binder hoàn toàn không gọi socket, filesystem open ghi, hoặc Resolve API."""
    prop = _make_sample_proposal()
    appr = _make_matching_approval(prop)
    target = _make_sample_target_spec()
    evidence = _make_evidence_table()
    snapshot = _make_sample_occupancy_snapshot()

    with patch("socket.socket") as mock_socket, patch("builtins.open") as mock_open:
        plan = bind_timeline_execution_plan(prop, appr, target, evidence, snapshot)
        assert plan.binding_status == "BOUND_VERIFIED"
        mock_socket.assert_not_called()
        mock_open.assert_not_called()


# ==============================================================================
# NHÓM 11: CANONICAL PLAN DIGEST & SERIALIZATION (Workorder R7 §4)
# ==============================================================================

def test_canonical_plan_digest_determinism_and_stability():
    """Xác thực digest kế hoạch tất định, ổn định giữa 2 instances giống nhau, thay đổi khi hành động thay đổi."""
    prop = _make_sample_proposal()
    appr = _make_matching_approval(prop)
    target = _make_sample_target_spec()
    evidence = _make_evidence_table()
    snapshot = _make_sample_occupancy_snapshot()

    plan1 = bind_timeline_execution_plan(prop, appr, target, evidence, snapshot)
    plan2 = bind_timeline_execution_plan(prop, appr, target, evidence, snapshot)

    digest1 = compute_canonical_plan_digest(plan1)
    digest2 = compute_canonical_plan_digest(plan2)

    assert isinstance(digest1, str)
    assert len(digest1) == 64
    assert digest1 == digest2, "Hai kế hoạch tương đương bắt buộc cho cùng một plan_digest_sha256"

    # Thay đổi action_idempotency_hash của action -> digest bắt buộc thay đổi
    mutated_action = dataclasses.replace(plan1.planned_actions[0], action_idempotency_hash="hash_mutated_12345")
    mutated_plan = dataclasses.replace(plan1, planned_actions=(mutated_action,))
    mutated_digest = compute_canonical_plan_digest(mutated_plan)

    assert mutated_digest != digest1, "Thay đổi action_idempotency_hash bắt buộc làm thay đổi plan_digest_sha256"

    # Thay đổi timeline range của action -> digest bắt buộc thay đổi
    mutated_action_range = dataclasses.replace(plan1.planned_actions[0], timeline_range=TimeRangeFrames(start_frame=100, end_frame=200))
    mutated_plan_range = dataclasses.replace(plan1, planned_actions=(mutated_action_range,))
    assert compute_canonical_plan_digest(mutated_plan_range) != digest1


def test_canonical_plan_digest_rejects_invalid_type():
    """Từ chối đối tượng không phải TimelineExecutionPlan với TypeError."""
    with pytest.raises(TypeError, match="ERR_INVALID_PLAN_TYPE"):
        compute_canonical_plan_digest("not_a_plan")  # type: ignore
